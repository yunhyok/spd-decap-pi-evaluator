#!/usr/bin/env python3
"""Solve the actual mounted Device/Via336274 2-port at three fixed frequencies."""

from __future__ import annotations

import argparse
import gc
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import probe_astra_native_device_branch_2port as base


FREQUENCIES_HZ = (1.0e7, 1.0e8, 1.0e9)
EXPECTED_BASE_DRIVER_SHA256 = (
    "5f73464f9bd8aea1eac54bae7c7840134fbf19a63d62b28e1edb57e76ec844eb"
)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def run(args: argparse.Namespace, budget: base._Budget) -> dict[str, Any]:
    base_driver_path = Path(base.__file__).resolve()
    base_driver_sha256 = sha256(base_driver_path.read_bytes()).hexdigest()
    if base_driver_sha256 != EXPECTED_BASE_DRIVER_SHA256:
        raise ValueError("the pinned successful 1 MHz driver has changed")

    budget.emit("load_bundle_start", bundle=str(args.bundle))
    with ZipFile(args.bundle, "r") as archive:
        bundle_manifest_bytes = archive.read("manifest.json")
        bundle_manifest = json.loads(bundle_manifest_bytes)
    if (
        bundle_manifest.get("scenario_sha256") != base.EXPECTED_SCENARIO_SHA256
        or bundle_manifest.get("design_fingerprint")
        != base.EXPECTED_DESIGN_FINGERPRINT
        or bundle_manifest.get("app_version") != base.__version__
    ):
        raise ValueError("bundle manifest differs from the pinned D115b v0.23.1 basis")

    loaded = base.load_scenario_bundle(args.bundle, is_cancelled=budget.cancelled)
    scenario = loaded.scenario
    attachments = loaded.attachments
    if scenario.design_fingerprint != base.EXPECTED_DESIGN_FINGERPRINT:
        raise ValueError("loaded Scenario design fingerprint differs from its pinned basis")
    budget.emit(
        "load_bundle_done",
        attachment_count=len(attachments),
        decap_count=len(scenario.decaps),
    )

    project = base.build_evaluation_project(
        scenario,
        evaluation_rail_id=base.RAIL_ID,
        solver_profile=base.LAYERWISE_ADMITTANCE_PROFILE.key,
        attachments=attachments,
    )
    template = base.compile_project_evaluation_template(
        project, base.RAIL_ID, terminal_complete_external_input=True
    )
    budget.emit("project_template_done")

    source_model = base.build_layerwise_uniform_source_model(
        project,
        attachments,
        base.RAIL_ID,
        template,
        progress=budget.progress,
        is_cancelled=budget.cancelled,
    )
    substrate = source_model.substrate
    device_port = substrate.port_by_rail_key[base.RAIL_ID.casefold()]
    ports, label_to_port, via = base._probe_ports(substrate.network, device_port)
    port_manifest = [
        {
            "port_id": port.port_id,
            "positive_node_id": port.positive_node_id,
            "negative_node_id": port.negative_node_id,
        }
        for port in ports
    ]
    port_manifest_sha256 = base._canonical_sha256(port_manifest)
    budget.emit(
        "native_substrate_done",
        physical_surface_count=len(substrate.network.surface_node_ids),
        reduced_node_count=len(substrate.network._reduced_node_ids),
        native_port_count=len(substrate.network.ports),
        probe_port_count=len(ports),
        via_link_id=via.link_id,
    )

    probe_network = base._compile_probe_network(substrate.network, ports)
    selected_port = next(
        port for port in probe_network.ports if port.port_id == device_port.port_id
    )
    probe_substrate = base.LayerwiseNetworkSubstrate(
        network=probe_network,
        port_by_rail_key={base.RAIL_ID.casefold(): selected_port},
        selected_net_by_rail_key={
            base.RAIL_ID.casefold(): substrate.selected_net_by_rail_key[
                base.RAIL_ID.casefold()
            ]
        },
        reference_net_by_rail_key={
            base.RAIL_ID.casefold(): substrate.reference_net_by_rail_key[
                base.RAIL_ID.casefold()
            ]
        },
        layer_blocks=substrate.layer_blocks,
        substrate_identity_sha256=substrate.substrate_identity_sha256,
        provenance={
            **dict(substrate.provenance),
            "research_measurement_port_subset": [port.port_id for port in ports],
            "research_measurement_port_subset_only": True,
        },
        scenario_certificate_view=substrate.scenario_certificate_view,
        external_port_proof_view=substrate.external_port_proof_view,
    )
    source_model_evidence_sha256 = source_model.evidence_sha256
    substrate_identity_sha256 = substrate.substrate_identity_sha256
    budget.emit("probe_substrate_done")

    binding = base.LayerwiseScenarioTerminationFactory(
        scenario=scenario,
        project=project,
        attachments=attachments,
    )(probe_substrate, template)
    joint_key = base.TRACE_VIA_JOINT_NODE_ID.casefold()
    joint_clusters = sorted(
        compiled.source.cluster_id
        for compiled in binding.termination_manifest.clusters
        if joint_key
        in {
            compiled.source.positive_surface_node_id.casefold(),
            compiled.source.negative_surface_node_id.casefold(),
        }
    )
    joint_native_links = sorted(
        link.link_id
        for link in binding.network.via_links
        if joint_key
        in {link.first_node_id.casefold(), link.second_node_id.casefold()}
    )
    loaded_substrate = probe_substrate.with_scenario_network_binding(binding)
    population = {
        "decap_count": len(scenario.decaps),
        "enabled_count": sum(1 for item in scenario.decaps if item.enabled),
        "source_mounted_count": sum(
            1 for item in scenario.decaps if item.source_mounted
        ),
    }
    budget.emit(
        "scenario_binding_done",
        scenario_identity_sha256=binding.scenario_identity_sha256,
        termination_cluster_count=len(binding.termination_manifest.clusters),
        termination_cluster_count_at_trace_via_joint=len(joint_clusters),
    )

    del source_model, substrate, probe_substrate, loaded, project, template, attachments
    base.clear_layerwise_substrate_cache()
    base.clear_layerwise_scenario_binding_cache()
    gc.collect()

    common = {
        "program": base.APP_DISPLAY_NAME,
        "version": base.__version__,
        "state": "source_mounted_scenario_manifest",
        "rail_id": base.RAIL_ID,
        "bundle": {
            "path": str(args.bundle),
            "size_bytes": args.bundle.stat().st_size,
            "manifest_sha256": sha256(bundle_manifest_bytes).hexdigest(),
            "scenario_sha256": bundle_manifest["scenario_sha256"],
            "design_fingerprint": scenario.design_fingerprint,
            "source_sha256": scenario.source.sha256,
            "load_validated_all_declared_member_hashes": True,
        },
        "identities": {
            "base_driver_sha256": base_driver_sha256,
            "driver_code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "solver_profile": base.LAYERWISE_ADMITTANCE_PROFILE.key,
            "probe_port_manifest_sha256": port_manifest_sha256,
            "source_model_evidence_sha256": source_model_evidence_sha256,
            "base_substrate_identity_sha256": substrate_identity_sha256,
            "loaded_scenario_identity_sha256": binding.scenario_identity_sha256,
            "termination_manifest_sha256": binding.termination_manifest.manifest_sha256,
        },
        "ports": {
            "manifest": port_manifest,
            "branch_owner_id": base.VIA_OWNER_ID,
            "branch_link_id": via.link_id,
            "branch_resistance_ohm": via.resistance_ohm_per_via,
            "branch_inductance_h": via.inductance_h_per_via,
        },
        "scenario_population": population,
        "trace_via_joint_incidence": {
            "node_id": base.TRACE_VIA_JOINT_NODE_ID,
            "native_via_link_ids_after_scenario_binding": joint_native_links,
            "source_mounted_termination_cluster_ids": joint_clusters,
            "source_mounted_termination_cluster_count": len(joint_clusters),
        },
    }

    points: dict[str, Any] = {}
    completed = 0
    for frequency_hz in FREQUENCIES_HZ:
        key = str(int(frequency_hz))
        checkpoint_path = args.run_dir / f"frequency-{key}.json"
        budget.emit("frequency_start", frequency_hz=frequency_hz)
        try:
            solved = loaded_substrate.network.solve(
                np.asarray([frequency_hz], dtype=np.float64),
                termination_manifest=loaded_substrate.termination_manifest,
                selected_rail_id=base.RAIL_ID,
                base_network_identity_sha256=loaded_substrate.substrate_identity_sha256,
                is_cancelled=budget.cancelled,
            )
            point = {
                "status": "COMPLETED_ACTUAL_MOUNTED_POINT",
                "frequency_hz": frequency_hz,
                "response": base._state_json(solved, label_to_port),
            }
            completed += 1
            budget.emit("frequency_done", frequency_hz=frequency_hz)
        except Exception as exc:
            point = {
                "status": "STOP_ACTUAL_MOUNTED_POINT",
                "frequency_hz": frequency_hz,
                "error": {
                    "type": type(exc).__name__,
                    "code": getattr(exc, "code", None),
                    "message": str(exc),
                },
            }
            budget.emit(
                "frequency_stopped",
                frequency_hz=frequency_hz,
                error_type=type(exc).__name__,
                error_code=getattr(exc, "code", None),
                message=str(exc),
            )
        point["resource_at_checkpoint"] = {
            "elapsed_s": budget.elapsed(),
            "peak_working_set_bytes": budget.peak_working_set,
            "peak_private_bytes": budget.peak_private,
        }
        _write_json(checkpoint_path, {**common, **point})
        points[key] = point
        gc.collect()

    return {
        **common,
        "status": (
            "COMPLETED_ACTUAL_MOUNTED_THREE_FREQUENCY_DIAGNOSTIC"
            if completed == len(FREQUENCIES_HZ)
            else "PARTIAL_ACTUAL_MOUNTED_THREE_FREQUENCY_DIAGNOSTIC"
        ),
        "frequencies_hz": list(FREQUENCIES_HZ),
        "points": points,
        "resource": {
            "elapsed_s": budget.elapsed(),
            "peak_working_set_bytes": budget.peak_working_set,
            "peak_private_bytes": budget.peak_private,
            "max_runtime_s": budget.runtime_s,
            "max_rss_bytes": budget.rss_bytes,
        },
        "limitations": [
            "Only the saved source-mounted Scenario is solved; the 1 MHz point and unloaded state are not repeated.",
            "Cross terms use reciprocal complex polarization; cancellation ratio is diagnostic, not an error bound.",
            "No trace replacement, DC trace extrapolation, PowerSI parameter, or fit enters these native solves.",
            "These three points diagnose frequency shape and do not establish broadband accuracy.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--max-runtime-s", type=float, default=2100.0)
    parser.add_argument("--max-rss-gib", type=float, default=24.0)
    args = parser.parse_args()
    args.bundle = args.bundle.resolve()
    args.output = args.output.resolve()
    args.run_dir = args.run_dir.resolve()
    if args.output.exists() or args.run_dir.exists():
        parser.error("output and run-dir must not already exist")
    if not args.bundle.is_file():
        parser.error("bundle is not a file")
    if args.max_runtime_s <= 0.0 or args.max_runtime_s > 2100.0:
        parser.error("max-runtime-s must be in (0, 2100]")
    if args.max_rss_gib <= 0.0 or args.max_rss_gib > 24.0:
        parser.error("max-rss-gib must be in (0, 24]")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.run_dir.mkdir(parents=True)

    print(
        f"{base.APP_DISPLAY_NAME} v{base.__version__} - Astra mounted high-frequency 2-port",
        flush=True,
    )
    budget = base._Budget(
        args.max_runtime_s,
        int(args.max_rss_gib * 2**30),
        args.run_dir / "progress.jsonl",
    )
    budget.emit("start", pid=base.os.getpid(), python=sys.version)
    budget.start_watchdog()
    original_splu = base._install_factor_probe(budget)
    try:
        result = run(args, budget)
    except BaseException as exc:
        result = {
            "program": base.APP_DISPLAY_NAME,
            "version": base.__version__,
            "status": "STOP_ACTUAL_MOUNTED_THREE_FREQUENCY_DIAGNOSTIC",
            "error": {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(exc),
            },
            "resource": {
                "elapsed_s": budget.elapsed(),
                "peak_working_set_bytes": budget.peak_working_set,
                "peak_private_bytes": budget.peak_private,
                "max_runtime_s": budget.runtime_s,
                "max_rss_bytes": budget.rss_bytes,
            },
        }
        budget.emit("stopped", error_type=type(exc).__name__, message=str(exc))
    finally:
        base._layer_surface_network.splu = original_splu
        budget.stop.set()
        base.clear_layerwise_scenario_binding_cache()
        base.clear_layerwise_substrate_cache()
    _write_json(args.output, result)
    print(f"{result['status']} -> {args.output}", flush=True)
    return 0 if result["status"].startswith("COMPLETED_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
