"""SPD Decap PI Evaluator v0.23.1: disabled 10 MHz L04 checkpoint recovery."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True  # HQ verified the completed 10MHz solver and fixed-R helper contracts.
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 120.0, 150.0, 8.0
NV, L25_BRANCHES, CONTACTS = 3_178_104, 604_031, 38_278
L04_BRANCHES, L04_DUAL = 2_272_974, 1_628_105
POSITIVE, NEGATIVE, GAUGE = 2699, 2656, 0
FREQUENCY_HZ, SOURCE_CURRENT_A = 1e7, 1.0

# HQ replaces these four receipts only after the target-frequency solve finishes.
PINS = {
    "solver_result": (R / "astra-l04-frequency-10mhz-hybrid-aux-right-lgmres-01/result.json", "5fa0b4faef1909e0c66d0d21f75e37aa3f0cd96c94504bcf18be09c2001e2e8d"),
    "solver_driver": (R / "astra-l04-frequency-10mhz-hybrid-aux-right-lgmres-01/driver-at-run.py", "e50707e8189ae6f71e107a620d8b194c387965c53db276c4ebdc97fc1dcd3c9f"),
    "solver_external": (R / "astra-l04-frequency-10mhz-hybrid-aux-right-lgmres-01/external-budget.json", "c05235fc381d1a442d7835b90e996aa2051dae5841d6c71e1c2447bc3df00caf"),
    "solver_field": (R / "astra-l04-frequency-10mhz-hybrid-aux-right-lgmres-01/restart-4-unvalidated-field.npz", "02a2444e2aae549ffaae6422ec189a5d39e1a74213ae6cfb0bb449ca7c1a1849"),
    "ntd_action": (ROOT / "tools/research/prepare_astra_l04_contact_ntd_action.py", "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
    "budget": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
}
MEMORY_COUNTER_SOURCE_SHA = "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict[str, object]:
    found = sha(path)
    if expected is not None:
        assert found == expected, str(path)
    return {"path": str(path), "sha256": found, "size_bytes": path.stat().st_size}


def pending() -> list[str]:
    return [name for name, (_, digest) in PINS.items() if digest.startswith("PENDING_")]


def _solver_metadata(result: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object], bool]:
    assert result.get("status") == "MEASURED_BOUNDED_10MHZ_HYBRID_AUX_RIGHT_LGMRES"
    assert result.get("frequency_hz") == FREQUENCY_HZ
    assert result.get("source_current_amplitude_a") == SOURCE_CURRENT_A
    history = result.get("history")
    best = result.get("best_field")
    assert isinstance(history, list) and history and isinstance(best, dict)
    assert isinstance(best.get("sha256"), str) and isinstance(best.get("path"), str)
    matches = [item for item in history if isinstance(item, dict)
               and isinstance(item.get("field"), dict)
               and item["field"].get("sha256") == best["sha256"]]
    if matches:
        assert len(matches) == 1, "best hash matches more than one saved checkpoint"
        selected, selected_is_last = matches[0], matches[0] is history[-1]
    else:
        initial = result.get("initial_field")
        assert isinstance(initial, dict) and initial.get("sha256") == best["sha256"], \
            "best hash does not match a saved checkpoint or initial warm field"
        assert isinstance(initial.get("path"), str)
        selected = {"field": initial, "relative_true_residual": result.get("initial_relative")}
        selected_is_last = False
    for key in ("lgmres_info", "initial_relative", "final_relative"):
        assert key in result
    assert isinstance(result["lgmres_info"], int)
    assert isinstance(result.get("algebraically_converged"), bool)
    assert isinstance(selected.get("relative_true_residual"), (int, float))
    assert math.isfinite(float(selected["relative_true_residual"]))
    assert all(isinstance(result[key], (int, float)) and math.isfinite(float(result[key]))
               for key in ("initial_relative", "final_relative"))
    return history, selected, selected_is_last


def original_numerical_gate(info: int, algebraically_converged: bool,
                            true_residual: float, selected_is_last: bool) -> bool:
    return bool(info == 0 and algebraically_converged and selected_is_last and true_residual <= 1e-9)


def self_check() -> None:
    accepted = {"status": "MEASURED_BOUNDED_10MHZ_HYBRID_AUX_RIGHT_LGMRES",
                "frequency_hz": FREQUENCY_HZ, "source_current_amplitude_a": SOURCE_CURRENT_A,
                "history": [{"field": {"sha256": "a", "path": "x"}, "relative_true_residual": 1e-10}],
                "best_field": {"sha256": "a", "path": "x"}, "lgmres_info": 0,
                "initial_relative": 1.0, "final_relative": 1e-10, "algebraically_converged": True,
                "original_numerical_gate": True}
    _, selected, selected_is_last = _solver_metadata(accepted)
    assert original_numerical_gate(accepted["lgmres_info"], accepted["algebraically_converged"],
                                   selected["relative_true_residual"], selected_is_last)
    assert accepted["original_numerical_gate"] is True
    try:
        _solver_metadata({"status": "WRONG", "history": []})
    except AssertionError:
        pass
    else:
        raise AssertionError("metadata rejection missing")
    try:
        rejected = accepted.copy(); rejected["best_field"] = {"sha256": "missing", "path": "x"}
        _solver_metadata(rejected)
    except AssertionError:
        pass
    else:
        raise AssertionError("checkpoint rejection missing")
    print("PASS_DISABLED_10MHZ_UNVALIDATED_L04_RECOVERY_METADATA_SELF_CHECK")


def _verify_solver() -> tuple[dict[str, object], dict[str, object], bool, dict[str, object]]:
    assert not pending(), f"unfilled pins: {pending()}"
    result_path, result_digest = PINS["solver_result"]
    result_receipt = receipt(result_path, result_digest)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    _, selected, selected_is_last = _solver_metadata(result)
    driver = receipt(*PINS["solver_driver"])
    assert isinstance(result.get("driver"), dict) and result["driver"].get("sha256") == driver["sha256"]
    external = receipt(*PINS["solver_external"])
    external_report = json.loads(PINS["solver_external"][0].read_text(encoding="utf-8"))
    assert external_report.get("status") == "COMPLETED_NATIVE_WORKER" and external_report.get("exit_code") == 0
    assert external_report.get("driver_sha256") == driver["sha256"]
    field = receipt(Path(selected["field"]["path"]), selected["field"]["sha256"])
    assert field["sha256"] == PINS["solver_field"][1]
    return result, selected, selected_is_last, {"solver_result": result_receipt, "solver_driver": driver,
                                                  "solver_external": external, "solver_field": field}


def worker(output: Path) -> None:
    import prepare_astra_l04_contact_ntd_action as ntd

    assert not pending(), f"unfilled pins: {pending()}"
    assert sha(Path(ntd.__file__)) == PINS["ntd_action"][1]
    assert sha(Path(ntd.recon.__file__)) == PINS["budget"][1]
    assert output.is_dir() and {item.name for item in output.iterdir()} == {"driver-at-run.py"}
    frozen = output / "driver-at-run.py"
    assert sha(frozen) == sha(Path(__file__))
    started = time.monotonic()
    internal = ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    budget = {"internal_seconds": INTERNAL_SECONDS, "memory_gib": MEMORY_GIB}
    try:
        solver_result, selected, selected_is_last, prior = _verify_solver()
        internal.check("verified target-frequency solver receipts")
        inputs, stream_result, _ = ntd.verify_contract()
        internal.check("verified fixed-R ContactNtD contract")
        action = ntd.load_action(stream_result)
        internal.check("loaded fixed-R ContactNtD action")
        with np.load(selected["field"]["path"], allow_pickle=False) as saved:
            voltage = np.asarray(saved["active_voltage_v"], dtype=np.complex128)
            q25 = np.asarray(saved["l25_branch_current_a"], dtype=np.complex128)
            g = np.asarray(saved["l04_independent_contact_current_into_sheet_a"], dtype=np.complex128)
            source = float(np.asarray(saved["source_current_amplitude_a"])[0])
            frequency = float(np.asarray(saved["frequency_hz"])[0])
            indices = np.asarray(saved["source_positive_negative_gauge_active_indices"])
            operator_hash = str(np.asarray(saved["frequency_operator_sha256_utf8"])[0])
            bridge_hash = str(np.asarray(saved["frequency_bridge_sha256_utf8"])[0])
        assert voltage.shape == (NV,) and q25.shape == (L25_BRANCHES,) and g.shape == (CONTACTS - 1,)
        assert np.all(np.isfinite(voltage)) and np.all(np.isfinite(q25)) and np.all(np.isfinite(g))
        assert source == SOURCE_CURRENT_A and frequency == FREQUENCY_HZ
        assert np.array_equal(indices, [POSITIVE, NEGATIVE, GAUGE]) and voltage[GAUGE] == 0j
        assert len(operator_hash) == len(bridge_hash) == 64
        assert all(char in "0123456789abcdef" for char in operator_hash + bridge_hash)
        solver_inputs = solver_result.get("inputs")
        assert isinstance(solver_inputs, dict)
        assert solver_inputs["ntd_action"]["sha256"] == PINS["ntd_action"][1]
        assert operator_hash == solver_inputs["operator"]["sha256"]
        assert bridge_hash == solver_inputs["bridge"]["sha256"]
        internal.check("loaded 10 MHz checkpoint field")
        # Exactly one qualified fixed-R ContactNtD action applies this independent g.
        recovered = action.apply(g, return_field=True)
        assert recovered.branch_current_a.shape == (L04_BRANCHES,)
        assert recovered.dual_potential_v.shape == (L04_DUAL,) and recovered.target_bq_a.shape == (L04_DUAL,)
        assert np.all(np.isfinite(recovered.branch_current_a))
        assert np.all(np.isfinite(recovered.dual_potential_v)) and np.all(np.isfinite(recovered.target_bq_a))
        internal.check("recovered full L04 field")
        field_path = output / "recovered-unvalidated-full-contact-l04-10mhz-field.npz"
        np.savez(field_path, active_voltage_v=voltage, l25_branch_current_a=q25,
                 l04_independent_contact_current_into_sheet_a=g,
                 l04_branch_current_a=recovered.branch_current_a,
                 l04_dual_potential_v=recovered.dual_potential_v,
                 l04_target_into_sheet_a=recovered.target_bq_a,
                 l04_contact_potential_v=recovered.dual_potential_v[action.free_cell_count:],
                 source_current_amplitude_a=np.array([source]), frequency_hz=np.array([frequency]),
                 source_positive_negative_gauge_active_indices=indices,
                 frequency_operator_sha256_utf8=np.array([operator_hash], dtype="U64"),
                 frequency_bridge_sha256_utf8=np.array([bridge_hash], dtype="U64"))
        field = receipt(field_path)
        info = solver_result["lgmres_info"]
        algebraically_converged = solver_result["algebraically_converged"]
        true_residual = selected["relative_true_residual"]
        gate = original_numerical_gate(info, algebraically_converged, true_residual, selected_is_last)
        if "original_numerical_gate" in solver_result:
            assert isinstance(solver_result["original_numerical_gate"], bool)
            assert solver_result["original_numerical_gate"] is gate, "source original numerical gate disagrees"
        report = {"program": PROGRAM, "version": VERSION,
                  "status": "RECOVERED_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT",
                  "driver": receipt(frozen), "field": field, "inputs": {**inputs, **prior},
                  "prior_solve": prior,
                  "numerical": {"info": info, "initial_scaled_residual_relative": solver_result["initial_relative"],
                                "final_scaled_residual_relative": solver_result["final_relative"],
                                "field_scaled_residual_relative": true_residual,
                                "algebraically_converged": algebraically_converged},
                  "original_numerical_gate": gate,
                  "recovery": {"metrics": recovered.metrics, "field": field,
                               "contact_equation": {"status": "NOT_COMPUTED_DIAGNOSTIC_ONLY"}},
                  "geometry_approximation": stream_result["geometry_approximation"],
                  "frequency_hz": frequency, "source_current_amplitude_a": source,
                  "source_current_a": source,
                  "scope": "Diagnostic full-contact L04 checkpoint recovery only. No numerical, physical, PowerSI, or acceptance-compatible gate is emitted.",
                  "budget": {**budget, "elapsed_seconds": time.monotonic() - started}}
        internal.check("serialized recovery result")
        (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    except BaseException:
        (output / "failure.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
            "status": "STOP_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT", "traceback": traceback.format_exc(),
            "budget": {**budget, "elapsed_seconds": time.monotonic() - started}}, indent=2), encoding="utf-8")
        raise


def launch(output: Path) -> int:
    assert RUN_RELEASED, "RUN_RELEASED remains false pending HQ review"
    assert not pending(), f"unfilled pins: {pending()}"
    import probe_astra_l25_rt0_p1_pair as counter
    assert sha(Path(counter.__file__)) == MEMORY_COUNTER_SOURCE_SHA
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "driver-at-run.py"
    shutil.copy2(Path(__file__), frozen)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    started = time.monotonic(); private = working = 0; reason = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        print(f"bounded recovery watchdog: owned PID={process.pid},{EXTERNAL_SECONDS}s/8GiB", flush=True)
        while process.poll() is None:
            counters = counter._MemoryCounters(); counters.cb = ctypes.sizeof(counters)
            if not getter(int(process._handle), ctypes.byref(counters), counters.cb):
                if process.poll() is None: reason = "STOP_PROCESS_MEMORY_QUERY"
            else:
                private = max(private, int(counters.private_usage)); working = max(working, int(counters.working_set))
            if time.monotonic() - started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
            if max(private, working) > int(MEMORY_GIB * 2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
            if reason:
                process.kill(); break
            time.sleep(.5)
        code = process.wait(timeout=10)
    status = reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT")
    (output / "external-budget.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
        "status": status, "exit_code": code, "driver_sha256": sha(frozen), "external_seconds": EXTERNAL_SECONDS,
        "max_memory_bytes": int(MEMORY_GIB * 2**30), "printed_memory_cap_gib": MEMORY_GIB, "sampled_peak_private_bytes": private,
        "sampled_peak_working_set_bytes": working, "sampling_interval_s": .5}, indent=2), encoding="utf-8")
    return 0 if status == "COMPLETED_NATIVE_WORKER" and code == 0 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--native-worker", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check(); return
    assert RUN_RELEASED, "RUN_RELEASED remains false pending HQ review"
    assert not pending(), f"unfilled pins: {pending()}"
    assert args.output is not None
    if args.native_worker:
        worker(args.output.resolve())
    else:
        raise SystemExit(launch(args.output.resolve()))


if __name__ == "__main__":
    main()
