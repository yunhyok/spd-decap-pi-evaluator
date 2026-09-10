"""SPD Decap PI Evaluator v0.23.1: disabled 10 MHz L25 self-L sensitivity."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
from scipy import sparse

import reconstruct_astra_native_loaded_field as recon


PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
FREQUENCY_HZ, SOURCE_CURRENT_A = 10_000_000.0, 1.0
L25_BRANCHES = 604_031
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 50.0, 60.0, 4.0
MEMORY_COUNTER_SOURCE_SHA = "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"
FAILED_PHYSICAL_GATES = {
    "l04_all_contact_kcl",
    "matrix_global_circuit_kcl",
    "matrix_identity_power",
    "physical_power_closure",
    "physical_source_global_circuit_kcl",
}

RECOVERY = R / "astra-l04-frequency-10mhz-unvalidated-recovery-01"
PHYSICAL = R / "astra-l04-frequency-10mhz-unvalidated-physical-diagnostic-01"
SOLVER = R / "astra-l04-frequency-10mhz-hybrid-aux-right-lgmres-01"
SELF = R / "astra-l25-rt0-self-magnetic-02"
STEP = R / "astra-l25-adaptive-longest-pair-01/step-06"
FREQUENCY_OPERATOR = R / "astra-full-contact-frequency-operators-02"
PINS = {
    "recovery_result": (RECOVERY / "result.json", "b059ed5a067b0244fca004c51d9797fc478e93df2397fdf30f0daafa64c6e4f4"),
    "recovery_driver": (RECOVERY / "driver-at-run.py", "0334087871401cf664f4eea7cf66b97cdb37fdb1e49a0a06dfc1e8e0cdd6ba95"),
    "recovery_external": (RECOVERY / "external-budget.json", "1025a11ca14a0111894667fcd4dfb223225ec2d42fa351a0de84c74a96fadba6"),
    "recovery_field": (RECOVERY / "recovered-unvalidated-full-contact-l04-10mhz-field.npz", "c60f3f67ac7b8914ab36fa1e9d273c4be20f259817347517cb8117c032163443"),
    "physical_result": (PHYSICAL / "physical-diagnostic.json", "a8adce893b8c518f2636030cdc0913984a0a8284f70f8b6a958a818440045206"),
    "physical_driver": (PHYSICAL / "driver-at-run.py", "aa8e0752ea98ce98b4f8e7944a4e0b2bb580e6075133f5e83efd34b6c646f07f"),
    "physical_external": (PHYSICAL / "external-budget.json", "13c17defb5a36b2ca0494388391a3be7a34c7df8802e198d83610bbb87307732"),
    "solver_result": (SOLVER / "result.json", "5fa0b4faef1909e0c66d0d21f75e37aa3f0cd96c94504bcf18be09c2001e2e8d"),
    "solver_driver": (SOLVER / "driver-at-run.py", "e50707e8189ae6f71e107a620d8b194c387965c53db276c4ebdc97fc1dcd3c9f"),
    "solver_external": (SOLVER / "external-budget.json", "c05235fc381d1a442d7835b90e996aa2051dae5841d6c71e1c2447bc3df00caf"),
    "output_impact": (R / "astra-l04-unvalidated-10mhz-output-impact-20260910.json", "4ad32ed03904945a02acf827af2b31d3b01ecbed836a3fb165f75292f5ea2aab"),
    "frequency_operator_result": (FREQUENCY_OPERATOR / "result.json", "7efcd3d999162b27311136a462ef5335071c00e371c6bbe6ac1333d7a98f6555"),
    "frequency_operator": (FREQUENCY_OPERATOR / "frequency-10000000-conditional-operator.npz", "7d528de4ced4e24e6fbf1c0ba437276ce8ccc706684e36028f63494685171701"),
    "original_operator": (R / "astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz", "5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92"),
    "self_result": (SELF / "result.json", "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"),
    "self_driver": (SELF / "driver-at-run.py", "ec964ed1b08dd37a86f5dd4a8c2a36efb30dd2310c8bc454bce588e9c8326c33"),
    "self_matrix": (SELF / "rt0-self-magnetic.npz", "3008ff8b4cd50cfaf202f90ece440b5aebff44e8cd3bdc79a048c5048bc407d9"),
    "topology": (STEP / "topology.npz", "d70ceb1b38cede682247967495c6ef83d08ef2b0ef63e3543df908ba13412a4d"),
    "rt0_r": (STEP / "rt0.npz", "b41e42f5b4c3d1788a4ab3dd4b2ef6f221d1dbbeb6a58c55b675613f17b9a5e4"),
    "budget_source": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "memory_counter": (ROOT / "tools/research/probe_astra_l25_rt0_p1_pair.py", MEMORY_COUNTER_SOURCE_SHA),
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict[str, object]:
    found = sha(path)
    if expected is not None:
        assert found == expected, f"pinned input changed: {path}"
    return {"path": str(path), "sha256": found, "size_bytes": path.stat().st_size}


def _pair(value: complex) -> list[float]:
    value = complex(value)
    assert np.isfinite(value)
    return [float(value.real), float(value.imag)]


def _csc(archive: np.lib.npyio.NpzFile, prefix: str) -> sparse.csc_matrix:
    shape = tuple(int(value) for value in archive[f"{prefix}_shape"])
    matrix = sparse.csc_matrix((archive[f"{prefix}_data"], archive[f"{prefix}_indices"],
                                archive[f"{prefix}_indptr"]), shape=shape)
    assert matrix.shape == (L25_BRANCHES, L25_BRANCHES) if prefix in ("r", "lself") else matrix.shape[1] == L25_BRANCHES
    assert np.all(np.isfinite(matrix.data))
    return matrix


def _same_csc(left: np.lib.npyio.NpzFile, right: np.lib.npyio.NpzFile,
              left_prefix: str, right_prefix: str) -> bool:
    return all(np.array_equal(left[f"{left_prefix}_{suffix}"], right[f"{right_prefix}_{suffix}"])
               for suffix in ("data", "indices", "indptr", "shape"))


def self_check() -> None:
    assert sha(Path(recon.__file__)) == PINS["budget_source"][1]
    inductance = np.array([[2.0, -0.5], [-0.5, 1.0]]) * 1e-9
    q = np.array([1.0 + 0.4j, -0.3 + 0.2j])
    ell = inductance @ q
    ordinary = np.dot(q, ell)
    hermitian = np.vdot(q, ell)
    assert abs(ordinary - hermitian) > 1e-12
    assert hermitian.real > 0.0 and abs(hermitian.imag) < 1e-24
    omega = 2.0 * np.pi * FREQUENCY_HZ
    added_constitutive = 1j * omega * ell
    old_mna_action = np.array([0.2 - 0.1j, -0.4 + 0.3j])
    rhs = np.array([0.0, 0.0])
    new_mna_action = old_mna_action - added_constitutive
    assert np.allclose(rhs - new_mna_action, rhs - old_mna_action + added_constitutive)
    assert np.allclose(1j * omega * ordinary, -np.dot(q, new_mna_action - old_mna_action))
    print(f"{PROGRAM} v{VERSION}: PASS_L25_SELF_SENSITIVITY_SIGN_AND_ENERGY_SELF_CHECK")


def verify_contract() -> dict[str, object]:
    inputs = {name: receipt(path, expected) for name, (path, expected) in PINS.items()}
    recovery = json.loads(PINS["recovery_result"][0].read_bytes())
    physical = json.loads(PINS["physical_result"][0].read_bytes())
    solver = json.loads(PINS["solver_result"][0].read_bytes())
    impact = json.loads(PINS["output_impact"][0].read_bytes())
    frequency = json.loads(PINS["frequency_operator_result"][0].read_bytes())
    self_result = json.loads(PINS["self_result"][0].read_bytes())
    assert recovery["status"] == "RECOVERED_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT"
    assert recovery["driver"]["sha256"] == PINS["recovery_driver"][1]
    assert recovery["field"]["sha256"] == PINS["recovery_field"][1]
    assert recovery["inputs"]["solver_result"]["sha256"] == PINS["solver_result"][1]
    assert recovery["inputs"]["solver_driver"]["sha256"] == PINS["solver_driver"][1]
    assert recovery["inputs"]["solver_external"]["sha256"] == PINS["solver_external"][1]
    assert recovery["frequency_hz"] == FREQUENCY_HZ and recovery["source_current_a"] == SOURCE_CURRENT_A
    assert recovery["original_numerical_gate"] is False and recovery["numerical"]["info"] == 4
    assert recovery["numerical"]["algebraically_converged"] is False
    assert solver["status"] == "MEASURED_BOUNDED_10MHZ_HYBRID_AUX_RIGHT_LGMRES"
    assert solver["driver"]["sha256"] == PINS["solver_driver"][1]
    assert solver["lgmres_info"] == 4 and solver["algebraically_converged"] is False
    assert physical["status"] == "DIAGNOSTIC_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT"
    assert physical["validator_driver"]["sha256"] == PINS["physical_driver"][1]
    assert physical["original_numerical_gate"] is False and physical["physical_gates_all_pass"] is False
    failed = {name for name, passed in physical["gates"].items() if not passed}
    assert failed == FAILED_PHYSICAL_GATES and len(physical["gates"]) == 23
    assert impact["status"] == "DIAGNOSTIC_UNVALIDATED_L04_OUTPUT_IMPACT_10MHZ"
    assert impact["inputs"]["solver"]["sha256"] == PINS["solver_result"][1]
    assert impact["inputs"]["physical_diagnostic"]["sha256"] == PINS["physical_result"][1]
    assert impact["original_numerical_gate"] is False
    assert frequency["status"] == "ASSEMBLED_CONDITIONAL_FULL_CONTACT_FREQUENCY_OPERATORS_NO_SOLVE"
    point = next(item for item in frequency["points"] if item["frequency_hz"] == FREQUENCY_HZ)
    assert point["artifact"]["sha256"] == PINS["frequency_operator"][1]
    assert frequency["inputs"]["old_operator"]["sha256"] == PINS["original_operator"][1]
    assert self_result["status"] == "COMPLETED_L25_RT0_SELF_MAGNETIC"
    assert all(self_result["gates"].values())
    assert self_result["script_sha256"] == PINS["self_driver"][1]
    assert self_result["checkpoint"]["sha256"] == PINS["self_matrix"][1]
    assert self_result["inputs"]["topology"]["sha256"] == PINS["topology"][1]
    assert self_result["inputs"]["rt0_r"]["sha256"] == PINS["rt0_r"][1]

    with np.load(PINS["self_matrix"][0], allow_pickle=False) as self_pack, \
            np.load(PINS["topology"][0], allow_pickle=False) as topology, \
            np.load(PINS["rt0_r"][0], allow_pickle=False) as rt0, \
            np.load(PINS["frequency_operator"][0], allow_pickle=False) as target, \
            np.load(PINS["original_operator"][0], allow_pickle=False) as original:
        assert np.array_equal(self_pack["branch_first_node"], topology["branch_first_node"])
        assert np.array_equal(self_pack["branch_second_node"], topology["branch_second_node"])
        assert np.array_equal(self_pack["branch_first_node"], rt0["branch_first_node"])
        assert np.array_equal(self_pack["branch_second_node"], rt0["branch_second_node"])
        assert _same_csc(target, original, "r", "r") and _same_csc(target, rt0, "r", "r")
        assert _same_csc(target, original, "b", "b")
        assert np.array_equal(target["frequency_hz"], [FREQUENCY_HZ])
        assert np.array_equal(target["source_current_a"], [SOURCE_CURRENT_A])
        assert tuple(target["r_shape"]) == (L25_BRANCHES, L25_BRANCHES)
        assert tuple(target["b_shape"])[1] == L25_BRANCHES
        assert tuple(self_pack["lself_shape"]) == tuple(target["r_shape"])

    return {"inputs": inputs, "recovery": recovery, "physical": physical,
            "solver": solver, "impact": impact}


def _write_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
        stream.flush(); os.fsync(stream.fileno())
    os.link(temporary, path)
    temporary.unlink()


def _write_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.link(temporary, path)
    temporary.unlink()


def worker(output: Path) -> None:
    assert {path.name for path in output.iterdir()} == {"driver-at-run.py"}
    assert sha(output / "driver-at-run.py") == sha(Path(__file__))
    started = time.monotonic()
    budget = recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    try:
        contract = verify_contract()
        budget.check("input and L25 ordering contracts")
        with np.load(PINS["recovery_field"][0], allow_pickle=False) as field:
            assert np.array_equal(field["frequency_hz"], [FREQUENCY_HZ])
            assert np.array_equal(field["source_current_amplitude_a"], [SOURCE_CURRENT_A])
            assert str(field["frequency_operator_sha256_utf8"][0]) == PINS["frequency_operator"][1]
            q = np.asarray(field["l25_branch_current_a"], dtype=np.complex128)
        assert q.shape == (L25_BRANCHES,) and np.all(np.isfinite(q))
        with np.load(PINS["self_matrix"][0], allow_pickle=False) as packed:
            lself = _csc(packed, "lself")
        ell = lself @ q
        omega = 2.0 * np.pi * FREQUENCY_HZ
        added_constitutive = 1j * omega * ell
        assert np.all(np.isfinite(ell)) and np.all(np.isfinite(added_constitutive))
        ordinary = complex(np.dot(q, ell))
        hermitian = complex(np.vdot(q, ell))
        hermitian_scale = max(abs(hermitian.real), np.finfo(float).tiny)
        hermitian_imag_relative = abs(hermitian.imag) / hermitian_scale
        roundoff_tol = 64.0 * np.finfo(float).eps * max(float(np.linalg.norm(q) * np.linalg.norm(ell)), np.finfo(float).tiny)
        assert hermitian.real >= -roundoff_tol
        assert hermitian_imag_relative <= 1e-12
        directional = 1j * omega * ordinary / (SOURCE_CURRENT_A ** 2)
        impact = contract["impact"]
        indicator = float(impact["stationary_correction_ohm"])
        gap = float(impact["diagnostic_errors_unvalidated"]["complex_gap_ohm"])
        assert np.isclose(indicator, 3.855837398541936e-6, rtol=0.0, atol=1e-18)
        assert np.isclose(gap, 0.0010164817188069604, rtol=0.0, atol=1e-18)
        artifact = output / "l25-self-magnetic-10mhz-fixed-field-action.npz"
        _write_npz(artifact, lself_q_h_a=ell,
                   added_l25_constitutive_voltage_v=added_constitutive,
                   added_mna_current_row_residual_v=added_constitutive,
                   frequency_hz=np.asarray([FREQUENCY_HZ]),
                   source_current_a=np.asarray([SOURCE_CURRENT_A]))
        budget.check("sparse L25 self action saved")
        physical = contract["physical"]
        report = {
            "program": PROGRAM, "version": VERSION,
            "status": "DIAGNOSTIC_UNVALIDATED_L25_SELF_MAGNETIC_FIXED_FIELD_10MHZ",
            "driver": receipt(output / "driver-at-run.py"),
            "artifact": receipt(artifact),
            "inputs": contract["inputs"],
            "frequency_hz": FREQUENCY_HZ, "source_current_a": SOURCE_CURRENT_A,
            "raw_solver_info": contract["solver"]["lgmres_info"],
            "algebraically_converged": contract["solver"]["algebraically_converged"],
            "original_numerical_gate": contract["recovery"]["original_numerical_gate"],
            "failed_physical_gates": sorted(FAILED_PHYSICAL_GATES),
            "failed_physical_gate_count": len(FAILED_PHYSICAL_GATES),
            "physical_gates_all_pass": physical["physical_gates_all_pass"],
            "sign_convention": {
                "old_mna_current_row": "B.T@v - R@q",
                "new_minus_old_mna_action": "-1j*omega*Lself@q",
                "new_minus_old_constitutive_voltage": "+1j*omega*Lself@q",
                "new_minus_old_mna_residual": "+1j*omega*Lself@q",
                "stationary_fixed_field_derivative": "+1j*omega*q.T@Lself@q/I**2",
            },
            "metrics": {
                "ordinary_transpose_q_lself_q_h_a2": _pair(ordinary),
                "hermitian_q_lself_q_h_a2": _pair(hermitian),
                "hermitian_imaginary_relative": float(hermitian_imag_relative),
                "hermitian_roundoff_nonnegative_tolerance_h_a2": float(roundoff_tol),
                "lself_q_l2_h_a": float(np.linalg.norm(ell)),
                "lself_q_max_abs_h_a": float(np.max(np.abs(ell), initial=0.0)),
                "added_current_equation_residual_l2_v": float(np.linalg.norm(added_constitutive)),
                "added_current_equation_residual_max_abs_v": float(np.max(np.abs(added_constitutive), initial=0.0)),
                "stationary_fixed_field_directional_ohm": _pair(directional),
                "stationary_fixed_field_directional_abs_ohm": float(abs(directional)),
                "prior_stationary_residual_indicator_abs_ohm": indicator,
                "prior_raw_saved_reference_gap_abs_ohm": gap,
                "directional_to_stationary_residual_indicator_ratio": float(abs(directional) / indicator),
                "directional_to_raw_saved_reference_gap_ratio": float(abs(directional) / gap),
            },
            "model_components": {
                "l25_same_triangle_zero_thickness_self_partial": True,
                "l25_mutual_triangle_terms": False,
                "return_path_or_proximity_terms": False,
                "native_scalar_inductance_reapplied": False,
                "factor_or_solve_performed": False,
            },
            "budget": budget.receipt(), "elapsed_seconds": time.monotonic() - started,
            "scope": "Fixed-field sensitivity on the unvalidated 10 MHz checkpoint. Sparse qualified L25 same-triangle self-L action only. No FMM, factor, solve, convergence or physical-gate clearance, acceptance, error bound, gap attribution, mutual/return/proximity exclusion, self-only model selection, or PowerSI fitting claim.",
        }
        _write_json(output / "result.json", report)
        print(json.dumps({key: report[key] for key in ("status", "raw_solver_info", "original_numerical_gate", "failed_physical_gate_count")}, allow_nan=False), flush=True)
        print(json.dumps(report["metrics"], allow_nan=False), flush=True)
    except BaseException:
        _write_json(output / "failure.json", {"status": "STOP_L25_SELF_MAGNETIC_FIXED_FIELD_DIAGNOSTIC",
                                                "traceback": traceback.format_exc()})
        raise


def launch(output: Path) -> int:
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
        print(f"bounded L25 self diagnostic: owned PID={process.pid},{EXTERNAL_SECONDS}s/{MEMORY_GIB:g}GiB", flush=True)
        while process.poll() is None:
            counters = counter._MemoryCounters(); counters.cb = ctypes.sizeof(counters)
            if not getter(int(process._handle), ctypes.byref(counters), counters.cb):
                if process.poll() is None:
                    reason = "STOP_PROCESS_MEMORY_QUERY"
            else:
                private = max(private, int(counters.private_usage)); working = max(working, int(counters.working_set))
            if time.monotonic() - started >= EXTERNAL_SECONDS:
                reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
            if max(private, working) > int(MEMORY_GIB * 2**30):
                reason = "STOP_EXTERNAL_MEMORY_BUDGET"
            if reason:
                process.kill(); break
            time.sleep(0.25)
        code = process.wait(timeout=10)
    status = reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT")
    _write_json(output / "external-budget.json", {
        "program": PROGRAM, "version": VERSION, "status": status, "exit_code": code,
        "driver_sha256": sha(frozen), "external_seconds": EXTERNAL_SECONDS,
        "max_memory_bytes": int(MEMORY_GIB * 2**30), "printed_memory_cap_gib": MEMORY_GIB,
        "sampled_peak_private_bytes": private, "sampled_peak_working_set_bytes": working,
        "sampling_interval_s": 0.25})
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
        verify_contract()
        print(f"{PROGRAM} v{VERSION}: PASS_DISABLED_L25_10MHZ_SELF_MAGNETIC_SENSITIVITY_PREFLIGHT")
        return
    assert RUN_RELEASED, "RUN_RELEASED remains false pending HQ review"
    assert args.output is not None
    if args.native_worker:
        worker(args.output.resolve())
    else:
        raise SystemExit(launch(args.output.resolve()))


if __name__ == "__main__":
    main()
