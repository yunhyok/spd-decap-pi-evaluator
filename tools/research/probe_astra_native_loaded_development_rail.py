#!/usr/bin/env python3
"""Solve one source-selected non-holdout loaded rail at four fixed frequencies."""

from __future__ import annotations

import argparse
from collections import Counter
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


TARGET_RAIL_ID = "ADC_VDD_075_VTRIP_SRAM/0"
EXPECTED_TARGET_SOURCE_MOUNTED_COUNT = 421
FREQUENCIES_HZ = (1.0e6, 1.0e7, 1.0e8, 1.0e9)
EXPECTED_BASE_DRIVER_SHA256 = (
    "5f73464f9bd8aea1eac54bae7c7840134fbf19a63d62b28e1edb57e76ec844eb"
)
EXPECTED_W6_REPORT_SHA256 = (
    "969e40046e3a099d09557ba7500693460362336d76b067962436bd3b5177abc4"
)
EXPECTED_W6_REPORT_SIZE_BYTES = 823047
EXPECTED_SELECTION_RECEIPT_SHA256 = (
    "99d0f77fcceb9d07b97aa65953dd42b8d106b7f2485f42f11ee643134d64b8f9"
)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _point_json(result: Any, port_id: str, frequency_hz: float) -> dict[str, Any]:
    admittance = complex(result.effective_admittance_by_port[port_id][0])
    if not (np.isfinite(admittance.real) and np.isfinite(admittance.imag)):
        raise ValueError("Device effective admittance is non-finite")
    if admittance == 0.0:
        raise ValueError("Device effective admittance is zero")
    impedance = 1.0 / admittance
    return {
        "status": "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVICE_POINT",
        "frequency_hz": frequency_hz,
        "effective_admittance_s": base._complex(admittance),
        "device_zdd_ohm": base._complex(impedance),
        "device_zdd_magnitude_ohm": float(abs(impedance)),
        "device_zdd_phase_deg": float(np.degrees(np.angle(impedance))),
        "device_zdd_real_nonnegative": bool(impedance.real >= 0.0),
        "diagnostics": base._json_diagnostics(result.diagnostics),
    }


def run(args: argparse.Namespace, budget: base._Budget) -> dict[str, Any]:
    base_driver_path = Path(base.__file__).resolve()
    base_driver_sha256 = sha256(base_driver_path.read_bytes()).hexdigest()
    if base_driver_sha256 != EXPECTED_BASE_DRIVER_SHA256:
        raise ValueError("the pinned successful 1 MHz driver has changed")

    score_bytes = args.score_contract.read_bytes()
    score_sha256 = sha256(score_bytes).hexdigest()
    if (
        len(score_bytes) != EXPECTED_W6_REPORT_SIZE_BYTES
        or score_sha256 != EXPECTED_W6_REPORT_SHA256
    ):
        raise ValueError("W6 score-split report differs from its pinned receipt")
    score_report = json.loads(score_bytes)
    holdout_rails = tuple(score_report["score_split"]["loaded_final_holdout"])
    if len(holdout_rails) != 6 or TARGET_RAIL_ID in holdout_rails:
        raise ValueError("loaded_final_holdout contract is unexpected")

    selection_receipt_bytes = args.selection_receipt.read_bytes()
    selection_receipt_sha256 = sha256(selection_receipt_bytes).hexdigest()
    if selection_receipt_sha256 != EXPECTED_SELECTION_RECEIPT_SHA256:
        raise ValueError("development selection receipt differs from its pinned hash")
    selection_receipt = json.loads(selection_receipt_bytes)
    expected_excluded = set(holdout_rails) | set(
        score_report["score_split"]["vqps_holdout"]
    )
    if (
        selection_receipt.get("status")
        != "PINNED_SOURCE_SELECTED_LOADED_DEVELOPMENT_CASE"
        or selection_receipt.get("rail_id") != TARGET_RAIL_ID
        or selection_receipt.get("source_enabled_decap_count")
        != EXPECTED_TARGET_SOURCE_MOUNTED_COUNT
        or not selection_receipt.get("selection_written_before_target_reference_extraction")
        or set(selection_receipt.get("excluded_holdout_rails", ())) != expected_excluded
    ):
        raise ValueError("development selection receipt content is unexpected")

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

    mounted_enabled = [
        item for item in scenario.decaps if item.enabled and item.source_mounted
    ]
    mounted_by_rail = Counter(item.current_rail_id for item in mounted_enabled)
    excluded_holdout_rails = tuple(selection_receipt["excluded_holdout_rails"])
    candidates = sorted(
        (
            rail_id
            for rail_id in mounted_by_rail
            if rail_id not in excluded_holdout_rails
        ),
        key=lambda rail_id: (-mounted_by_rail[rail_id], rail_id.casefold()),
    )
    if not candidates or candidates[0] != TARGET_RAIL_ID:
        observed = candidates[0] if candidates else None
        raise ValueError(f"source-only development selection yielded {observed!r}")
    if mounted_by_rail[TARGET_RAIL_ID] != EXPECTED_TARGET_SOURCE_MOUNTED_COUNT:
        raise ValueError("target source-mounted enabled population differs from 421")

    selection = {
        "rule": (
            "maximize enabled-and-source-mounted decap count over rails outside "
            "the pinned W6 holdout sets; break ties by casefolded rail id"
        ),
        "selected_before_project_compile_or_response_solve": True,
        "selected_rail_id": TARGET_RAIL_ID,
        "selected_enabled_source_mounted_count": mounted_by_rail[TARGET_RAIL_ID],
        "loaded_final_holdout": list(holdout_rails),
        "selected_is_not_loaded_final_holdout": TARGET_RAIL_ID not in holdout_rails,
        "all_excluded_holdout_rails": list(excluded_holdout_rails),
        "eligible_ranked_counts": [
            {"rail_id": rail_id, "count": mounted_by_rail[rail_id]}
            for rail_id in candidates
        ],
        "score_contract": {
            "path": str(args.score_contract),
            "size_bytes": len(score_bytes),
            "sha256": score_sha256,
            "schema_version": score_report.get("schema_version"),
        },
        "prior_selection_receipt": {
            "path": str(args.selection_receipt),
            "size_bytes": len(selection_receipt_bytes),
            "sha256": selection_receipt_sha256,
            "written_before_target_reference_extraction": True,
            "reference_port_one_based": selection_receipt["port_one_based"],
        },
    }
    budget.emit(
        "development_target_pinned",
        rail_id=TARGET_RAIL_ID,
        enabled_source_mounted_count=mounted_by_rail[TARGET_RAIL_ID],
        loaded_final_holdout=list(holdout_rails),
    )
    budget.emit(
        "load_bundle_done",
        attachment_count=len(attachments),
        decap_count=len(scenario.decaps),
    )

    project = base.build_evaluation_project(
        scenario,
        evaluation_rail_id=TARGET_RAIL_ID,
        solver_profile=base.LAYERWISE_ADMITTANCE_PROFILE.key,
        attachments=attachments,
    )
    template = base.compile_project_evaluation_template(
        project, TARGET_RAIL_ID, terminal_complete_external_input=True
    )
    budget.emit("project_template_done", rail_id=TARGET_RAIL_ID)

    source_model = base.build_layerwise_uniform_source_model(
        project,
        attachments,
        TARGET_RAIL_ID,
        template,
        progress=budget.progress,
        is_cancelled=budget.cancelled,
    )
    substrate = source_model.substrate
    device_port = substrate.port_by_rail_key[TARGET_RAIL_ID.casefold()]
    probe_network = base._compile_probe_network(substrate.network, (device_port,))
    selected_port = next(
        port for port in probe_network.ports if port.port_id == device_port.port_id
    )
    probe_substrate = base.LayerwiseNetworkSubstrate(
        network=probe_network,
        port_by_rail_key={TARGET_RAIL_ID.casefold(): selected_port},
        selected_net_by_rail_key={
            TARGET_RAIL_ID.casefold(): substrate.selected_net_by_rail_key[
                TARGET_RAIL_ID.casefold()
            ]
        },
        reference_net_by_rail_key={
            TARGET_RAIL_ID.casefold(): substrate.reference_net_by_rail_key[
                TARGET_RAIL_ID.casefold()
            ]
        },
        layer_blocks=substrate.layer_blocks,
        substrate_identity_sha256=substrate.substrate_identity_sha256,
        provenance={
            **dict(substrate.provenance),
            "research_measurement_port_subset": [selected_port.port_id],
            "research_measurement_port_subset_only": True,
            "research_source_selected_development_rail": TARGET_RAIL_ID,
        },
        scenario_certificate_view=substrate.scenario_certificate_view,
        external_port_proof_view=substrate.external_port_proof_view,
    )
    source_model_evidence_sha256 = source_model.evidence_sha256
    substrate_identity_sha256 = substrate.substrate_identity_sha256
    budget.emit(
        "native_substrate_done",
        physical_surface_count=len(substrate.network.surface_node_ids),
        reduced_node_count=len(substrate.network._reduced_node_ids),
        native_port_count=len(substrate.network.ports),
        probe_port_count=1,
        device_port_id=selected_port.port_id,
    )

    binding = base.LayerwiseScenarioTerminationFactory(
        scenario=scenario,
        project=project,
        attachments=attachments,
    )(probe_substrate, template)
    loaded_substrate = probe_substrate.with_scenario_network_binding(binding)
    budget.emit(
        "scenario_binding_done",
        scenario_identity_sha256=binding.scenario_identity_sha256,
        termination_cluster_count=len(binding.termination_manifest.clusters),
    )

    common = {
        "program": base.APP_DISPLAY_NAME,
        "version": base.__version__,
        "state": "source_mounted_scenario_manifest",
        "rail_id": TARGET_RAIL_ID,
        "selection": selection,
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
            "source_model_evidence_sha256": source_model_evidence_sha256,
            "base_substrate_identity_sha256": substrate_identity_sha256,
            "loaded_scenario_identity_sha256": binding.scenario_identity_sha256,
            "termination_manifest_sha256": binding.termination_manifest.manifest_sha256,
        },
        "device_port": {
            "port_id": selected_port.port_id,
            "positive_node_id": selected_port.positive_node_id,
            "negative_node_id": selected_port.negative_node_id,
        },
        "scenario_population": {
            "decap_count": len(scenario.decaps),
            "enabled_source_mounted_count": len(mounted_enabled),
            "enabled_source_mounted_by_rail": dict(sorted(mounted_by_rail.items())),
        },
        "termination_cluster_count": len(binding.termination_manifest.clusters),
    }

    del source_model, substrate, probe_substrate, loaded, project, template, attachments
    base.clear_layerwise_substrate_cache()
    base.clear_layerwise_scenario_binding_cache()
    gc.collect()

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
                selected_rail_id=TARGET_RAIL_ID,
                base_network_identity_sha256=loaded_substrate.substrate_identity_sha256,
                is_cancelled=budget.cancelled,
            )
            point = _point_json(solved, selected_port.port_id, frequency_hz)
            completed += 1
            budget.emit("frequency_done", frequency_hz=frequency_hz)
        except Exception as exc:
            point = {
                "status": "STOP_ACTUAL_SOURCE_MOUNTED_DEVICE_POINT",
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
            "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVELOPMENT_RAIL_FOUR_POINT"
            if completed == len(FREQUENCIES_HZ)
            else "PARTIAL_ACTUAL_SOURCE_MOUNTED_DEVELOPMENT_RAIL_FOUR_POINT"
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
            "This is a source-selected non-holdout development rail, not a frozen W6 holdout score.",
            "Only the saved source-mounted Scenario is solved; no unloaded state or AON rail is repeated.",
            "No local trace shadow, new physical parameter, PowerSI response, or fit enters these native solves.",
            "The four Device Zdd points diagnose this one rail and do not establish broadband accuracy.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--score-contract", type=Path, required=True)
    parser.add_argument("--selection-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--max-runtime-s", type=float, default=2100.0)
    parser.add_argument("--max-rss-gib", type=float, default=24.0)
    args = parser.parse_args()
    args.bundle = args.bundle.resolve()
    args.score_contract = args.score_contract.resolve()
    args.selection_receipt = args.selection_receipt.resolve()
    args.output = args.output.resolve()
    args.run_dir = args.run_dir.resolve()
    if args.output.exists() or args.run_dir.exists():
        parser.error("output and run-dir must not already exist")
    if not all(
        path.is_file()
        for path in (args.bundle, args.score_contract, args.selection_receipt)
    ):
        parser.error("bundle, score-contract, and selection-receipt must be files")
    if args.max_runtime_s <= 0.0 or args.max_runtime_s > 2100.0:
        parser.error("max-runtime-s must be in (0, 2100]")
    if args.max_rss_gib <= 0.0 or args.max_rss_gib > 24.0:
        parser.error("max-rss-gib must be in (0, 24]")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.run_dir.mkdir(parents=True)

    print(
        f"{base.APP_DISPLAY_NAME} - Astra source-mounted development rail",
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
            "status": "STOP_ACTUAL_SOURCE_MOUNTED_DEVELOPMENT_RAIL_FOUR_POINT",
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
