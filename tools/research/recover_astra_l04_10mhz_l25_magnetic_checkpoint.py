"""SPD Decap PI Evaluator v0.23.1: disabled 10 MHz L25-magnetic GCROT L04 checkpoint recovery."""
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
RUN_RELEASED = True
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 120.0, 150.0, 8.0
NV, L25_BRANCHES, CONTACTS = 3_178_104, 604_031, 38_278
L04_BRANCHES, L04_DUAL = 2_272_974, 1_628_105
POSITIVE, NEGATIVE, GAUGE = 2699, 2656, 0
FREQUENCY_HZ, SOURCE_CURRENT_A = 1e7, 1.0

RUN_DIR = R / "astra-l04-10mhz-l25-magnetic-gcrotmk-01"
FUTURE_OUTPUT_NAME = "astra-l04-10mhz-l25-magnetic-checkpoint-recovery-01"
SOLVER_FUTURE_KEYS = {"solver_result", "solver_external", "solver_field"}
PINS = {
    "solver_result": (RUN_DIR / "result.json", "90a640099e91d1088dee4f6cd146b64865d5b74d06f886a8f555a48e48e144e3"),
    "solver_driver": (RUN_DIR / "driver-at-run.py", "fb737651456a57c1fcba96073ef1b8b2e48682b4dfb930a1abf10603bf51bcce"),
    "solver_external": (RUN_DIR / "external-budget.json", "55fde3e765c4540312996128c6cd7aba3abad6ce37dedfa25bee64fcac698199"),
    "solver_field": (RUN_DIR / "unvalidated-flexible-gcrotmk-field.npz", "c1f833406f3f6baab9e6010202a323ed1db5f6de43f1f1e4a8dfe8e7382e5d8d"),
    "frequency_operator": (R / "astra-full-contact-frequency-operators-02/frequency-10000000-conditional-operator.npz", "7d528de4ced4e24e6fbf1c0ba437276ce8ccc706684e36028f63494685171701"),
    "frequency_bridge": (R / "astra-l04-frequency-partial-inputs-01/frequency-10000000-partial-inputs.npz", "f5fff0c74c667d8243852b709ed07238585eec427a878bb074519d995f5f680a"),
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


def pending(include_solver=True) -> list[str]:
    return [name for name, (_, digest) in PINS.items()
            if digest.startswith("PENDING_") and (include_solver or name not in SOLVER_FUTURE_KEYS)]


def _solver_metadata(result: dict[str, object]) -> tuple[dict[str, object], int, float, float]:
    assert result.get("status") == "UNVALIDATED"
    artifact = result.get("artifact")
    info, final = result.get("raw_gcrotmk_info"), result.get("final_relative")
    warm_changed_model = result.get("warm_changed_model")
    assert isinstance(artifact, dict) and isinstance(artifact.get("sha256"), str) and isinstance(artifact.get("path"), str)
    assert isinstance(info, int) and isinstance(final, (int, float)) and math.isfinite(float(final))
    assert isinstance(warm_changed_model, dict) and isinstance(warm_changed_model.get("relative"), (int, float))
    assert math.isfinite(float(warm_changed_model["relative"]))
    return artifact, info, float(warm_changed_model["relative"]), float(final)


def original_numerical_gate(info: int, true_residual: float) -> bool:
    return bool(info == 0 and true_residual <= 1e-9)


def self_check() -> None:
    assert RUN_RELEASED is False
    accepted = {"status": "UNVALIDATED", "frequency_hz": FREQUENCY_HZ,
                "artifact": {"sha256": "a", "path": "x"}, "raw_gcrotmk_info": 0, "final_relative": 1e-10,
                "warm_changed_model": {"relative": 1e-10}}
    _, info, warm, residual = _solver_metadata(accepted)
    assert warm == residual
    assert original_numerical_gate(info, residual)
    assert not original_numerical_gate(1, residual)
    try:
        _solver_metadata({"status": "WRONG", "history": []})
    except AssertionError:
        pass
    else:
        raise AssertionError("metadata rejection missing")
    try:
        rejected = accepted.copy(); rejected["artifact"] = {"sha256": "missing"}
        _solver_metadata(rejected)
    except AssertionError:
        pass
    else:
        raise AssertionError("checkpoint rejection missing")
    print("PASS_DISABLED_10MHZ_UNVALIDATED_L04_RECOVERY_METADATA_SELF_CHECK")


def _verify_solver() -> tuple[dict[str, object], dict[str, object], int, float, float, dict[str, object]]:
    assert not pending(), f"unfilled pins: {pending()}"
    result_path, result_digest = PINS["solver_result"]
    result_receipt = receipt(result_path, result_digest)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    selected, info, warm_residual, true_residual = _solver_metadata(result)
    assert result.get("actual_numerical_acceptance") is original_numerical_gate(info, true_residual)
    driver = receipt(*PINS["solver_driver"])
    assert isinstance(result.get("driver"), dict) and result["driver"].get("sha256") == driver["sha256"]
    external = receipt(*PINS["solver_external"])
    external_report = json.loads(PINS["solver_external"][0].read_text(encoding="utf-8"))
    assert external_report.get("status") == "COMPLETED_NATIVE_WORKER" and external_report.get("exit_code") == 0
    assert external_report.get("driver_sha256") == driver["sha256"]
    field = receipt(Path(selected["path"]), selected["sha256"])
    assert field["sha256"] == PINS["solver_field"][1]
    operator, bridge = receipt(*PINS["frequency_operator"]), receipt(*PINS["frequency_bridge"])
    ntd_source, budget_source = receipt(*PINS["ntd_action"]), receipt(*PINS["budget"])
    return result, selected, info, warm_residual, true_residual, {"solver_result": result_receipt, "solver_driver": driver,
                                                    "solver_external": external, "solver_field": field,
                                                    "frequency_operator": operator, "frequency_bridge": bridge,
                                                    "ntd_action": ntd_source, "budget": budget_source}


def _static_preflight() -> dict[str, dict[str, object]]:
    assert not pending(False), f"unfilled static pins: {pending(False)}"
    return {name: receipt(path, digest) for name, (path, digest) in PINS.items()
            if name not in SOLVER_FUTURE_KEYS}


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
        solver_result, selected, info, warm_residual, true_residual, prior = _verify_solver()
        assert solver_result["physical_operator_replacement_accepted"] is False
        assert solver_result["physical_validation_performed"] is False
        internal.check("verified target-frequency solver receipts")
        inputs, stream_result, _ = ntd.verify_contract()
        internal.check("verified fixed-R ContactNtD contract")
        action = ntd.load_action(stream_result)
        internal.check("loaded fixed-R ContactNtD action")
        with np.load(selected["path"], allow_pickle=False) as saved:
            voltage = np.asarray(saved["active_voltage_v"], dtype=np.complex128)
            q25 = np.asarray(saved["l25_branch_current_a"], dtype=np.complex128)
            lq25 = np.asarray(saved["l25_magnetic_flux_linkage_wb"], dtype=np.complex128)
            g = np.asarray(saved["l04_independent_contact_current_into_sheet_a"], dtype=np.complex128)
            source = float(np.asarray(saved["source_current_amplitude_a"])[0])
            frequency = float(np.asarray(saved["frequency_hz"])[0])
            indices = np.asarray(saved["source_positive_negative_gauge_active_indices"])
            operator_hash = str(np.asarray(saved["frequency_operator_sha256_utf8"])[0])
            bridge_hash = str(np.asarray(saved["frequency_bridge_sha256_utf8"])[0])
        assert voltage.shape == (NV,) and q25.shape == lq25.shape == (L25_BRANCHES,) and g.shape == (CONTACTS - 1,)
        assert np.all(np.isfinite(voltage)) and np.all(np.isfinite(q25)) and np.all(np.isfinite(lq25)) and np.all(np.isfinite(g))
        assert source == SOURCE_CURRENT_A and frequency == FREQUENCY_HZ
        assert np.array_equal(indices, [POSITIVE, NEGATIVE, GAUGE]) and voltage[GAUGE] == 0j
        assert len(operator_hash) == len(bridge_hash) == 64
        assert all(char in "0123456789abcdef" for char in operator_hash + bridge_hash)
        assert operator_hash == PINS["frequency_operator"][1]
        assert bridge_hash == PINS["frequency_bridge"][1]
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
                 l04_independent_contact_current_into_sheet_a=g, l25_magnetic_flux_linkage_wb=lq25,
                 l04_branch_current_a=recovered.branch_current_a,
                 l04_dual_potential_v=recovered.dual_potential_v,
                 l04_target_into_sheet_a=recovered.target_bq_a,
                 l04_contact_potential_v=recovered.dual_potential_v[action.free_cell_count:],
                 source_current_amplitude_a=np.array([source]), frequency_hz=np.array([frequency]),
                 source_positive_negative_gauge_active_indices=indices,
                 frequency_operator_sha256_utf8=np.array([operator_hash], dtype="U64"),
                 frequency_bridge_sha256_utf8=np.array([bridge_hash], dtype="U64"))
        field = receipt(field_path)
        gate = original_numerical_gate(info, true_residual)
        report = {"program": PROGRAM, "version": VERSION,
                  "status": "RECOVERED_UNVALIDATED_L25_MAGNETIC_FULL_CONTACT_L04_CHECKPOINT",
                  "driver": receipt(frozen), "field": field, "inputs": {**inputs, **prior},
                  "prior_solve": prior,
                  "numerical": {"raw_gcrotmk_info": info,
                                "warm_changed_model_relative": warm_residual,
                                "final_changed_model_relative": true_residual,
                                "field_changed_model_relative": true_residual},
                  "original_numerical_gate": gate,
                  "magnetic_provenance": {"run_directory": str(RUN_DIR), "driver": prior["solver_driver"],
                                           "physical_operator_replacement_accepted": False,
                                           "physical_validation_performed": False, "model_acceptance": False},
                  "recovery": {"metrics": recovered.metrics, "field": field,
                               "contact_equation": {"status": "NOT_COMPUTED_DIAGNOSTIC_ONLY"}},
                  "geometry_approximation": stream_result["geometry_approximation"],
                  "frequency_hz": frequency, "source_current_amplitude_a": source,
                  "source_current_a": source,
                  "scope": "Diagnostic full-contact L04 recovery from the final provisional L25 magnetic GCROT field only. No numerical, physical, model, PowerSI, or acceptance-compatible gate is emitted.",
                  "budget": {**budget, "elapsed_seconds": time.monotonic() - started}}
        (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        internal.check("serialized recovery result")
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
        "status": status, "exit_code": code, "owned_pid": process.pid, "elapsed_s": time.monotonic() - started,
        "driver_sha256": sha(frozen), "external_seconds": EXTERNAL_SECONDS,
        "max_memory_bytes": int(MEMORY_GIB * 2**30), "printed_memory_cap_gib": MEMORY_GIB, "sampled_peak_private_bytes": private,
        "sampled_peak_working_set_bytes": working, "sampling_interval_s": .5}, indent=2), encoding="utf-8")
    return 0 if status == "COMPLETED_NATIVE_WORKER" and code == 0 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--native-worker", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check(); return
    if args.preflight:
        _, _, _, _, _, receipts = _verify_solver()
        print(json.dumps({"status": "PASS_DISABLED_L25_MAGNETIC_RECOVERY_FULL_INPUTS",
                          "future_solver_pins": pending(), "receipts": receipts}, sort_keys=True)); return
    assert RUN_RELEASED, "RUN_RELEASED remains false pending HQ review"
    assert not pending(), f"unfilled pins: {pending()}"
    assert args.output is not None
    if args.native_worker:
        worker(args.output.resolve())
    else:
        raise SystemExit(launch(args.output.resolve()))


if __name__ == "__main__":
    main()
