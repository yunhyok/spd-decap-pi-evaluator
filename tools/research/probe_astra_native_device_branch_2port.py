#!/usr/bin/env python3
"""Extract one actual-board Device/internal-branch 2-port at 1 MHz.

This research driver keeps the compiled physical network intact and changes
only the open measurement-port list.  The current solver exposes driving-point
impedances, so the reciprocal transfer term is recovered from four additional
two-terminal driving impedances by polarization.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread
from time import monotonic
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np

from spd_decap_pi import APP_DISPLAY_NAME, __version__
from spd_decap_pi._core.solver import layer_surface_network as _layer_surface_network
from spd_decap_pi._core.solver.evaluator import compile_project_evaluation_template
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfacePort,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layerwise_network import (
    LayerwiseNetworkSubstrate,
    build_layerwise_uniform_source_model,
    clear_layerwise_substrate_cache,
)
from spd_decap_pi._core.solver.profiles import LAYERWISE_ADMITTANCE_PROFILE
from spd_decap_pi.evaluation import build_evaluation_project
from spd_decap_pi.layerwise_scenario_adapter import (
    clear_layerwise_scenario_binding_cache,
)
from spd_decap_pi.layerwise_termination_adapter import (
    LayerwiseScenarioTerminationFactory,
)
from spd_decap_pi.scenario_io import load_scenario_bundle


RAIL_ID = "ADC_VDD_180_VQPS_SYS_1_AON/0"
VIA_OWNER_ID = "via:via336274"
FREQUENCY_HZ = 1.0e6
EXPECTED_SCENARIO_SHA256 = "2f5ae107221857f7f093ab8d6cbcff4c286a0c6cee5b6a9994de1c384cf39c83"
EXPECTED_DESIGN_FINGERPRINT = "73a0167ef13cadc885b0f97bcab00de4322095b1a25d9bfd014f32bb39b40dab"
TRACE_VIA_JOINT_NODE_ID = "spd-finite-via-vertex:b9bb8fd2222593d92b1c9511"


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set", ctypes.c_size_t),
        ("working_set", ctypes.c_size_t),
        ("quota_peak_paged_pool", ctypes.c_size_t),
        ("quota_paged_pool", ctypes.c_size_t),
        ("quota_peak_nonpaged_pool", ctypes.c_size_t),
        ("quota_nonpaged_pool", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


def _memory_bytes() -> tuple[int, int]:
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_MemoryCounters),
        wintypes.DWORD,
    )
    get_memory.restype = wintypes.BOOL
    ok = get_memory(
        current_process(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.working_set), int(counters.private_usage)


class _Budget:
    def __init__(self, runtime_s: float, rss_bytes: int, progress_path: Path) -> None:
        self.started = monotonic()
        self.runtime_s = runtime_s
        self.rss_bytes = rss_bytes
        self.progress_path = progress_path
        self.stop = Event()
        self.reason: str | None = None
        self.peak_working_set = 0
        self.peak_private = 0
        self._last_progress = 0.0
        self._last_message = ""

    def elapsed(self) -> float:
        return monotonic() - self.started

    def sample(self) -> tuple[int, int]:
        working, private = _memory_bytes()
        self.peak_working_set = max(self.peak_working_set, working)
        self.peak_private = max(self.peak_private, private)
        return working, private

    def emit(self, stage: str, **details: Any) -> None:
        working, private = self.sample()
        row = {
            "elapsed_s": self.elapsed(),
            "stage": stage,
            "working_set_bytes": working,
            "private_bytes": private,
            **details,
        }
        with self.progress_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        print(
            f"[{row['elapsed_s']:.1f}s] {stage} "
            f"WS={working / 2**30:.2f}GiB private={private / 2**30:.2f}GiB",
            flush=True,
        )

    def progress(self, value: int, message: str) -> None:
        now = monotonic()
        if message != self._last_message and now - self._last_progress >= 5.0:
            self.emit("compile_progress", percent=int(value), message=message)
            self._last_progress = now
            self._last_message = message
        if self.cancelled():
            raise RuntimeError(self.reason or "resource budget exceeded")

    def cancelled(self) -> bool:
        if self.stop.is_set():
            return True
        working, private = self.sample()
        if self.elapsed() > self.runtime_s:
            self.reason = "MAX_RUNTIME_EXCEEDED"
        elif max(working, private) > self.rss_bytes:
            self.reason = "MAX_RSS_EXCEEDED"
        if self.reason is not None:
            self.stop.set()
        return self.stop.is_set()

    def start_watchdog(self) -> Thread:
        def watch() -> None:
            next_report = monotonic() + 30.0
            while not self.stop.wait(1.0):
                try:
                    exceeded = self.cancelled()
                    if monotonic() >= next_report:
                        self.emit("watchdog")
                        next_report = monotonic() + 30.0
                    if exceeded:
                        self.emit("watchdog_stop", reason=self.reason)
                        os._exit(124)
                except BaseException:
                    os._exit(125)

        thread = Thread(target=watch, name="astra-resource-watchdog", daemon=True)
        thread.start()
        return thread


def _complex(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _install_factor_probe(budget: _Budget) -> Any:
    original = _layer_surface_network.splu

    def instrumented(matrix: Any, *args: Any, **kwargs: Any) -> Any:
        started = monotonic()
        budget.emit(
            "factor_start",
            shape=[int(value) for value in matrix.shape],
            nnz=int(matrix.nnz),
        )
        factor = original(matrix, *args, **kwargs)
        budget.emit("factor_done", factor_elapsed_s=monotonic() - started)
        return factor

    _layer_surface_network.splu = instrumented
    return original


def _json_diagnostics(diagnostics: Any) -> dict[str, Any]:
    fields = (
        "physical_surface_count",
        "reduced_node_count",
        "active_reduced_node_count",
        "pruned_portless_node_count",
        "structural_component_count",
        "active_structural_component_count",
        "finite_via_link_count",
        "topology_only_link_count",
        "maximum_port_rhs_batch_size",
        "maximum_factor_pivot_ratio",
        "maximum_relative_residual",
        "active_termination_cluster_count",
        "active_termination_element_count",
        "maximum_termination_kron_relative_residual",
        "termination_manifest_sha256",
        "solve_identity_sha256",
    )
    return {name: getattr(diagnostics, name) for name in fields}


def _probe_ports(network: Any, device_port: LayerSurfacePort) -> tuple[
    tuple[LayerSurfacePort, ...], dict[str, str | None], Any
]:
    matches = [
        link
        for link in network.via_links
        if tuple(owner.casefold() for owner in link.owner_ids)
        == (VIA_OWNER_ID.casefold(),)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one exact {VIA_OWNER_ID} link, found {len(matches)}")
    via = matches[0]
    if via.mode != "finite_parallel_rl" or via.count != 1:
        raise ValueError("Via336274 is not one finite native R/L owner")

    a, g = device_port.positive_node_id, device_port.negative_node_id
    u, v = via.first_node_id, via.second_node_id
    definitions = (
        ("device", device_port.port_id, a, g),
        ("branch", "astra-probe:branch", u, v),
        ("a_v", "astra-probe:a-v", a, v),
        ("g_u", "astra-probe:g-u", g, u),
        ("a_u", "astra-probe:a-u", a, u),
        ("g_v", "astra-probe:g-v", g, v),
    )
    ports: list[LayerSurfacePort] = []
    port_by_reduced_pair: dict[tuple[int, int], str] = {}
    label_to_port: dict[str, str | None] = {}
    for label, requested_id, positive, negative in definitions:
        first = network.reduced_node_index(positive)
        second = network.reduced_node_index(negative)
        if first == second:
            label_to_port[label] = None
            continue
        key = (min(first, second), max(first, second))
        existing = port_by_reduced_pair.get(key)
        if existing is None:
            port = LayerSurfacePort(requested_id, positive, negative)
            ports.append(port)
            port_by_reduced_pair[key] = port.port_id
            existing = port.port_id
        label_to_port[label] = existing
    if label_to_port["device"] is None or label_to_port["branch"] is None:
        raise ValueError("Device or Via336274 endpoints collapse under ideal topology")
    return tuple(ports), label_to_port, via


def _extract_2port(
    result: Any, label_to_port: dict[str, str | None]
) -> tuple[np.ndarray, dict[str, complex], float]:
    driving: dict[str, complex] = {}
    for label, port_id in label_to_port.items():
        driving[label] = (
            0.0j
            if port_id is None
            else 1.0 / complex(result.effective_admittance_by_port[port_id][0])
        )
    signed = (
        driving["a_v"]
        + driving["g_u"]
        - driving["a_u"]
        - driving["g_v"]
    )
    transfer = 0.5 * signed
    cancellation = sum(
        abs(driving[name]) for name in ("a_v", "g_u", "a_u", "g_v")
    ) / max(abs(signed), np.finfo(np.float64).tiny)
    matrix = np.asarray(
        [[driving["device"], transfer], [transfer, driving["branch"]]],
        dtype=np.complex128,
    )
    return matrix, driving, float(cancellation)


def _state_json(result: Any, label_to_port: dict[str, str | None]) -> dict[str, Any]:
    matrix, driving, cancellation = _extract_2port(result, label_to_port)
    hermitian = 0.5 * (matrix + matrix.conj().T)
    return {
        "z_ohm": [[_complex(value) for value in row] for row in matrix],
        "driving_pair_z_ohm": {key: _complex(value) for key, value in driving.items()},
        "polarization_cancellation_ratio": cancellation,
        "minimum_hermitian_eigenvalue_ohm": float(np.min(np.linalg.eigvalsh(hermitian))),
        "diagnostics": _json_diagnostics(result.diagnostics),
    }


def _compile_probe_network(network: Any, ports: tuple[LayerSurfacePort, ...]) -> Any:
    return compile_layer_surface_network(
        network.surface_node_ids,
        partials=network.partials,
        via_links=network.via_links,
        ports=ports,
    )


def run(args: argparse.Namespace, budget: _Budget) -> dict[str, Any]:
    budget.emit("load_bundle_start", bundle=str(args.bundle))
    with ZipFile(args.bundle, "r") as archive:
        bundle_manifest_bytes = archive.read("manifest.json")
        bundle_manifest = json.loads(bundle_manifest_bytes)
    if (
        bundle_manifest.get("scenario_sha256") != EXPECTED_SCENARIO_SHA256
        or bundle_manifest.get("design_fingerprint") != EXPECTED_DESIGN_FINGERPRINT
        or bundle_manifest.get("app_version") != __version__
    ):
        raise ValueError("bundle manifest differs from the pinned D115b v0.23.1 basis")
    loaded = load_scenario_bundle(args.bundle, is_cancelled=budget.cancelled)
    scenario = loaded.scenario
    attachments = loaded.attachments
    if scenario.design_fingerprint != EXPECTED_DESIGN_FINGERPRINT:
        raise ValueError("loaded Scenario design fingerprint differs from its pinned basis")
    budget.emit(
        "load_bundle_done",
        attachment_count=len(attachments),
        decap_count=len(scenario.decaps),
    )

    project = build_evaluation_project(
        scenario,
        evaluation_rail_id=RAIL_ID,
        solver_profile=LAYERWISE_ADMITTANCE_PROFILE.key,
        attachments=attachments,
    )
    template = compile_project_evaluation_template(
        project, RAIL_ID, terminal_complete_external_input=True
    )
    budget.emit("project_template_done")

    source_model = build_layerwise_uniform_source_model(
        project,
        attachments,
        RAIL_ID,
        template,
        progress=budget.progress,
        is_cancelled=budget.cancelled,
    )
    substrate = source_model.substrate
    device_port = substrate.port_by_rail_key[RAIL_ID.casefold()]
    ports, label_to_port, via = _probe_ports(substrate.network, device_port)
    code_sha256 = sha256(Path(__file__).read_bytes()).hexdigest()
    port_manifest = [
        {
            "port_id": port.port_id,
            "positive_node_id": port.positive_node_id,
            "negative_node_id": port.negative_node_id,
        }
        for port in ports
    ]
    port_manifest_sha256 = _canonical_sha256(port_manifest)
    budget.emit(
        "native_substrate_done",
        physical_surface_count=len(substrate.network.surface_node_ids),
        reduced_node_count=len(substrate.network._reduced_node_ids),
        native_port_count=len(substrate.network.ports),
        probe_port_count=len(ports),
        via_link_id=via.link_id,
    )

    probe_network = _compile_probe_network(substrate.network, ports)
    selected_port = next(port for port in probe_network.ports if port.port_id == device_port.port_id)
    probe_substrate = LayerwiseNetworkSubstrate(
        network=probe_network,
        port_by_rail_key={RAIL_ID.casefold(): selected_port},
        selected_net_by_rail_key={
            RAIL_ID.casefold(): substrate.selected_net_by_rail_key[RAIL_ID.casefold()]
        },
        reference_net_by_rail_key={
            RAIL_ID.casefold(): substrate.reference_net_by_rail_key[RAIL_ID.casefold()]
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
    del source_model, substrate
    clear_layerwise_substrate_cache()
    gc.collect()
    budget.emit("probe_substrate_done")

    frequencies = np.asarray([FREQUENCY_HZ], dtype=np.float64)
    base_result = probe_network.solve(
        frequencies,
        base_network_identity_sha256=substrate_identity_sha256,
        is_cancelled=budget.cancelled,
    )
    base_json = _state_json(base_result, label_to_port)
    budget.emit("unloaded_solve_done")
    with args.checkpoint_output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(
            {
                "program": APP_DISPLAY_NAME,
                "version": __version__,
                "status": "CHECKPOINT_ACTUAL_NATIVE_BASE_TWO_PORT",
                "scenario_sha256": EXPECTED_SCENARIO_SHA256,
                "design_fingerprint": EXPECTED_DESIGN_FINGERPRINT,
                "source_sha256": scenario.source.sha256,
                "code_sha256": code_sha256,
                "solver_profile": LAYERWISE_ADMITTANCE_PROFILE.key,
                "probe_port_manifest_sha256": port_manifest_sha256,
                "base_substrate_identity_sha256": substrate_identity_sha256,
                "rail_id": RAIL_ID,
                "frequency_hz": FREQUENCY_HZ,
                "via_owner_id": VIA_OWNER_ID,
                "via_link_id": via.link_id,
                "state": base_json,
                "resource_at_checkpoint": {
                    "elapsed_s": budget.elapsed(),
                    "peak_working_set_bytes": budget.peak_working_set,
                    "peak_private_bytes": budget.peak_private,
                },
            },
            handle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")

    binding = LayerwiseScenarioTerminationFactory(
        scenario=scenario,
        project=project,
        attachments=attachments,
    )(probe_substrate, template)
    joint_key = TRACE_VIA_JOINT_NODE_ID.casefold()
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
    budget.emit(
        "scenario_binding_done",
        scenario_identity_sha256=binding.scenario_identity_sha256,
        termination_cluster_count=len(binding.termination_manifest.clusters),
        termination_cluster_count_at_trace_via_joint=len(joint_clusters),
    )
    loaded_result = loaded_substrate.network.solve(
        frequencies,
        termination_manifest=loaded_substrate.termination_manifest,
        selected_rail_id=RAIL_ID,
        base_network_identity_sha256=loaded_substrate.substrate_identity_sha256,
        is_cancelled=budget.cancelled,
    )
    loaded_json = _state_json(loaded_result, label_to_port)
    budget.emit("loaded_solve_done")

    enabled = [item for item in scenario.decaps if item.enabled]
    source_mounted = [item for item in scenario.decaps if item.source_mounted]
    result = {
        "program": APP_DISPLAY_NAME,
        "version": __version__,
        "status": "COMPLETED_ACTUAL_NATIVE_TWO_PORT_DIAGNOSTIC",
        "scope": (
            "actual saved source/project/native G-C-R-L network; open Device and "
            "Via336274 endpoint ports; no trace replacement and no PowerSI fit"
        ),
        "frequency_hz": FREQUENCY_HZ,
        "rail_id": RAIL_ID,
        "bundle": {
            "path": str(args.bundle),
            "size_bytes": args.bundle.stat().st_size,
            "scenario_sha256": bundle_manifest["scenario_sha256"],
            "design_fingerprint": scenario.design_fingerprint,
            "source_sha256": scenario.source.sha256,
            "attachment_count": len(attachments),
            "load_validated_all_declared_member_hashes": True,
            "manifest_sha256": sha256(bundle_manifest_bytes).hexdigest(),
        },
        "identities": {
            "driver_code_sha256": code_sha256,
            "solver_profile": LAYERWISE_ADMITTANCE_PROFILE.key,
            "probe_port_manifest_sha256": port_manifest_sha256,
            "source_model_evidence_sha256": source_model_evidence_sha256,
            "base_substrate_identity_sha256": substrate_identity_sha256,
            "loaded_scenario_identity_sha256": binding.scenario_identity_sha256,
            "termination_manifest_sha256": binding.termination_manifest.manifest_sha256,
        },
        "ports": {
            "device": {
                "port_id": device_port.port_id,
                "positive_node_id": device_port.positive_node_id,
                "negative_node_id": device_port.negative_node_id,
            },
            "branch": {
                "owner_id": VIA_OWNER_ID,
                "link_id": via.link_id,
                "positive_node_id": via.first_node_id,
                "negative_node_id": via.second_node_id,
                "resistance_ohm": via.resistance_ohm_per_via,
                "inductance_h": via.inductance_h_per_via,
            },
            "probe_port_manifest": port_manifest,
            "probe_port_manifest_sha256": port_manifest_sha256,
            "polarization_identity": (
                "Zdb=(Z[a,v]+Z[g,u]-Z[a,u]-Z[g,v])/2; complex reciprocal "
                "bilinear identity; cancellation ratio is diagnostic, not an error bound"
            ),
        },
        "states": {
            "global_native_base_without_scenario_terminations": base_json,
            "source_mounted_scenario_manifest": loaded_json,
        },
        "scenario_population": {
            "decap_count": len(scenario.decaps),
            "enabled_count": len(enabled),
            "source_mounted_count": len(source_mounted),
            "enabled_by_rail": {
                rail: sum(1 for item in enabled if item.current_rail_id == rail)
                for rail in sorted({item.current_rail_id for item in enabled})
            },
        },
        "trace_via_joint_incidence": {
            "node_id": TRACE_VIA_JOINT_NODE_ID,
            "native_via_link_ids_after_scenario_binding": joint_native_links,
            "source_mounted_termination_cluster_ids": joint_clusters,
            "source_mounted_termination_cluster_count": len(joint_clusters),
            "no_source_mounted_termination_attaches_at_joint": not joint_clusters,
        },
        "resource": {
            "elapsed_s": budget.elapsed(),
            "peak_working_set_bytes": budget.peak_working_set,
            "peak_private_bytes": budget.peak_private,
            "max_runtime_s": budget.runtime_s,
            "max_rss_bytes": budget.rss_bytes,
        },
        "limitations": [
            "The two states are native base versus the saved source-mounted Scenario; they are not a W6 same-rail bare/loaded stratum contract.",
            "The current layerwise model is uniform-potential per certified artwork equivalence component and has no finite Trace R/L stamp.",
            "Via336274 remains in the native network; this run measures an open endpoint transfer and performs no replacement.",
            "Cross terms are recovered by subtraction; interpret the recorded cancellation ratio with the solver pivot and residual diagnostics.",
            "No PowerSI parameter, response, or error target enters this computation.",
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/evaluation-research/astra_native_device_branch_2port_2026-09-07.json",
    )
    parser.add_argument("--progress-output", type=Path)
    parser.add_argument("--checkpoint-output", type=Path)
    parser.add_argument("--max-runtime-s", type=float, default=2400.0)
    parser.add_argument("--max-rss-gib", type=float, default=24.0)
    args = parser.parse_args()
    args.bundle = args.bundle.resolve()
    args.output = args.output.resolve()
    args.progress_output = (
        args.progress_output.resolve()
        if args.progress_output is not None
        else (ROOT / "outputs/research/astra-native-device-branch-2port.progress.jsonl")
    )
    args.checkpoint_output = (
        args.checkpoint_output.resolve()
        if args.checkpoint_output is not None
        else (ROOT / "outputs/research/astra-native-device-branch-2port.base-checkpoint.json")
    )
    if any(path.exists() for path in (args.output, args.progress_output, args.checkpoint_output)):
        parser.error("output, progress-output, and checkpoint-output must not already exist")
    if args.max_runtime_s <= 0.0 or args.max_runtime_s > 2400.0:
        parser.error("max-runtime-s must be in (0, 2400]")
    if args.max_rss_gib <= 0.0 or args.max_rss_gib > 24.0:
        parser.error("max-rss-gib must be in (0, 24]")
    if not args.bundle.is_file():
        parser.error("bundle is not a file")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.progress_output.parent.mkdir(parents=True, exist_ok=True)
    args.checkpoint_output.parent.mkdir(parents=True, exist_ok=True)

    print(f"{APP_DISPLAY_NAME} - Astra native Device/Via 2-port probe", flush=True)
    budget = _Budget(args.max_runtime_s, int(args.max_rss_gib * 2**30), args.progress_output)
    budget.emit("start", pid=os.getpid(), python=sys.version)
    budget.start_watchdog()
    original_splu = _install_factor_probe(budget)
    try:
        result = run(args, budget)
    except BaseException as exc:
        result = {
            "program": APP_DISPLAY_NAME,
            "version": __version__,
            "status": "STOP_ACTUAL_NATIVE_TWO_PORT_DIAGNOSTIC",
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
        _layer_surface_network.splu = original_splu
        budget.stop.set()
        clear_layerwise_scenario_binding_cache()
        clear_layerwise_substrate_cache()
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(f"{result['status']} -> {args.output}", flush=True)
    return 0 if result["status"].startswith("COMPLETED_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
