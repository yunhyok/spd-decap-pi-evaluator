"""SPD Decap PI Evaluator v0.23.1: disabled diagnostic L04 field recovery."""
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

import prepare_astra_l04_contact_ntd_action as ntd

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 120.0, 150.0, 8.0
NV, L25_BRANCHES, CONTACTS = 3_178_104, 604_031, 38_278
L04_BRANCHES, L04_DUAL = 2_272_974, 1_628_105
POSITIVE, NEGATIVE, GAUGE = 2699, 2656, 0

# HQ fills these only after the fourth outer cycle has finished.
PINS = {
    "solver_result": (R / "astra-l04-hybrid-aux-right-lgmres-01/result.json", "a27c06f8e93c87a3823dac4578f27bedf8137e823cdb1648b40fa77fee35ed01"),
    "solver_driver": (R / "astra-l04-hybrid-aux-right-lgmres-01/driver-at-run.py", "4e0ff8031d71e6e1feba3e9f5dc166cd3cb74a07d2222cc092803940a888bd96"),
    "solver_external": (R / "astra-l04-hybrid-aux-right-lgmres-01/external-budget.json", "afd2e4f46c22159ca329f0e45c551a2511e6ddc2b03d2be5aff6f2cab3c97170"),
    "solver_field": (R / "astra-l04-hybrid-aux-right-lgmres-01/restart-4-unvalidated-field.npz", "1cd19f65be4ce8670a9a41f8ec6aed20c74b8fb4876e6e568eb162b268615532"),
    "bridge_result": (R / "astra-l04-full-contact-circuit-bridge-01/result.json", "1583612f90fca3c97f7b9fb4f8c3a0a004d6ae8d84ccc1f7bbc8f382247d266f"),
    "bridge": (R / "astra-l04-full-contact-circuit-bridge-01/l04-full-contact-circuit-bridge.npz", "02508f2e1588fe5e6ad4231b9c128a3324a88baf0229293d6a8b70ea10380e57"),
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


def pinned_receipt(pin: tuple[Path, str]) -> dict[str, object]:
    path, digest = pin
    assert not digest.startswith("PENDING_")
    return receipt(path, digest)


def pending() -> list[str]:
    return [name for name, (_, digest) in PINS.items() if digest.startswith("PENDING_")]


def original_numerical_gate(info: int, algebraically_converged: bool,
                            true_residual: float, selected_is_last: bool) -> bool:
    return bool(info == 0 and algebraically_converged is True and selected_is_last
                and true_residual <= 1e-9)


def _solver_metadata(result: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object], bool]:
    assert result.get("status") == "MEASURED_BOUNDED_HYBRID_AUX_RIGHT_LGMRES"
    history = result.get("history")
    assert isinstance(history, list) and history
    best = result.get("best_field")
    assert isinstance(best, dict) and isinstance(best.get("sha256"), str)
    matches = [item for item in history if isinstance(item, dict)
               and isinstance(item.get("field"), dict)
               and item["field"].get("sha256") == best["sha256"]]
    assert len(matches) == 1
    selected = matches[0]
    assert isinstance(selected["field"].get("path"), str)
    selected_is_last = selected is history[-1]
    return history, selected, selected_is_last


def self_check() -> None:
    try:
        _solver_metadata({"status": "WRONG", "history": []})
    except AssertionError:
        pass
    else:
        raise AssertionError("metadata rejection missing")
    assert original_numerical_gate(0, True, 1e-10, True)
    assert not original_numerical_gate(1, True, 1e-10, True)
    assert not original_numerical_gate(0, False, 1e-10, True)
    assert not original_numerical_gate(0, True, 1e-8, True)
    assert not original_numerical_gate(0, True, 1e-10, False)
    print("PASS_UNVALIDATED_L04_RECOVERY_METADATA_SELF_CHECK")


def _verify_solver() -> tuple[dict[str, object], list[dict[str, object]], dict[str, object], dict[str, object]]:
    result_path, result_digest = PINS["solver_result"]
    result_receipt = receipt(result_path, result_digest)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    history, selected, selected_is_last = _solver_metadata(result)
    driver = pinned_receipt(PINS["solver_driver"])
    assert driver["sha256"] == result["driver"]["sha256"]
    external_receipt = pinned_receipt(PINS["solver_external"])
    external = json.loads(PINS["solver_external"][0].read_text(encoding="utf-8"))
    assert external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0
    assert external["driver_sha256"] == driver["sha256"]
    best = receipt(Path(selected["field"]["path"]), selected["field"]["sha256"])
    assert best["sha256"] == result["best_field"]["sha256"]
    assert selected["field"]["sha256"] == result["best_field"]["sha256"]
    assert selected["relative_true_residual"] == result["best_relative"]
    assert selected_is_last or result["algebraically_converged"] is False
    for key in ("lgmres_info", "initial_relative", "final_relative", "best_relative"):
        assert isinstance(result[key], (int, float)) and math.isfinite(float(result[key]))
    assert isinstance(result["algebraically_converged"], bool)
    # The explicit solver field pin is a second immutable receipt for HQ's chosen best file.
    assert pinned_receipt(PINS["solver_field"])["sha256"] == best["sha256"]
    return result, history, selected, {"result": result_receipt, "driver": driver, "external": external_receipt, "field": best, "selected_is_last": selected_is_last}


def worker(output: Path) -> None:
    assert not pending(), f"unfilled pins: {pending()}"
    assert output.is_dir() and {item.name for item in output.iterdir()} == {"driver-at-run.py"}
    frozen = output / "driver-at-run.py"
    assert frozen.is_file() and sha(frozen) == sha(Path(__file__))
    budget_started = time.monotonic()
    internal = ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    budget = {"internal_seconds": INTERNAL_SECONDS, "memory_gib": MEMORY_GIB}
    try:
        solver_result, history, selected, prior = _verify_solver()
        internal.check("verified solver receipts")
        inputs, stream_result, _ = ntd.verify_contract()
        internal.check("verified ContactNtD contract")
        action = ntd.load_action(stream_result)
        internal.check("loaded ContactNtD action")
        with np.load(selected["field"]["path"], allow_pickle=False) as saved:
            voltage = np.asarray(saved["active_voltage_v"], dtype=np.complex128)
            q25 = np.asarray(saved["l25_branch_current_a"], dtype=np.complex128)
            g = np.asarray(saved["l04_independent_contact_current_into_sheet_a"], dtype=np.complex128)
            source = float(np.asarray(saved["source_current_a"])[0])
            frequency = float(np.asarray(saved["frequency_hz"])[0])
            indices = np.asarray(saved["source_positive_negative_gauge_active_indices"])
        assert voltage.shape == (NV,) and q25.shape == (L25_BRANCHES,) and g.shape == (CONTACTS - 1,)
        assert np.all(np.isfinite(voltage)) and np.all(np.isfinite(q25)) and np.all(np.isfinite(g))
        assert source == 1.0 and frequency == 1e6 and np.array_equal(indices, [POSITIVE, NEGATIVE, GAUGE])
        assert voltage[GAUGE] == 0j
        internal.check("loaded checkpoint field")
        # Exactly one qualified NtD action invocation for this recovery.
        recovered = action.apply(g, return_field=True)
        assert recovered.branch_current_a.shape == (L04_BRANCHES,)
        assert recovered.dual_potential_v.shape == (L04_DUAL,)
        assert recovered.target_bq_a.shape == (L04_DUAL,)
        assert np.all(np.isfinite(recovered.branch_current_a))
        assert np.all(np.isfinite(recovered.dual_potential_v)) and np.all(np.isfinite(recovered.target_bq_a))
        internal.check("recovered L04 field")
        bridge_result = json.loads(PINS["bridge_result"][0].read_text(encoding="utf-8"))
        bridge_result_receipt = pinned_receipt(PINS["bridge_result"])
        assert bridge_result["status"] == "PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE"
        assert bridge_result["artifact"]["sha256"] == PINS["bridge"][1]
        assert bridge_result["geometry_approximation"] == stream_result["geometry_approximation"]
        bridge = pinned_receipt(PINS["bridge"])
        field_path = output / "recovered-unvalidated-full-contact-l04-field.npz"
        np.savez(field_path, active_voltage_v=voltage, l25_branch_current_a=q25,
                 l04_independent_contact_current_into_sheet_a=g,
                 l04_contact_potential_v=recovered.dual_potential_v[action.free_cell_count:],
                 l04_branch_current_a=recovered.branch_current_a,
                 l04_dual_potential_v=recovered.dual_potential_v,
                 l04_target_into_sheet_a=recovered.target_bq_a,
                 source_current_amplitude_a=np.array([source]), frequency_hz=np.array([frequency]),
                 source_positive_negative_gauge_active_indices=indices)
        field = receipt(field_path)
        info = int(solver_result["lgmres_info"])
        algebraic = solver_result["algebraically_converged"]
        true_residual = float(selected["relative_true_residual"])
        gate = original_numerical_gate(info, algebraic, true_residual, prior["selected_is_last"])
        report = {"program": PROGRAM, "version": VERSION,
                  "status": "RECOVERED_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT",
                  "driver": receipt(frozen), "field": field,
                  "inputs": {**inputs, "solver_result": prior["result"], "solver_driver": prior["driver"],
                             "solver_external": prior["external"], "solver_field": prior["field"]},
                  "prior_solve": prior,
                  "numerical": {"info": info, "initial_scaled_residual_relative": solver_result["initial_relative"],
                                "final_scaled_residual_relative": solver_result["final_relative"],
                                "field_scaled_residual_relative": true_residual,
                                "algebraically_converged": algebraic},
                  "original_numerical_gate": gate,
                  "recovery": {"metrics": recovered.metrics, "field": field,
                               "contact_equation": {"status": "NOT_COMPUTED_DIAGNOSTIC_ONLY"}},
                  "bridge": {"result": bridge_result_receipt, "artifact": bridge},
                  "geometry_approximation": stream_result["geometry_approximation"],
                  "frequency_hz": frequency, "source_current_a": source,
                  "scope": "Diagnostic full-contact L04 checkpoint recovery only. No numerical, physical, PowerSI, or acceptance-compatible gate is emitted.",
                  "budget": {**budget, "elapsed_seconds": time.monotonic() - budget_started}}
        internal.check("serialized recovery result")
        (output / "result.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    except BaseException:
        (output / "failure.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
            "status": "STOP_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT", "traceback": traceback.format_exc(),
            "budget": {**budget, "elapsed_seconds": time.monotonic() - budget_started}}, indent=2), encoding="utf-8")
        raise


def launch(output: Path) -> int:
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
        "status": status, "exit_code": code, "driver_sha256": sha(frozen),
        "external_seconds": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30),
        "printed_memory_cap_gib": MEMORY_GIB, "sampled_peak_private_bytes": private,
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
    assert args.output is not None
    if args.native_worker:
        worker(args.output.resolve())
    else:
        raise SystemExit(launch(args.output.resolve()))


if __name__ == "__main__":
    main()
