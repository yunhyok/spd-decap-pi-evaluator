"""SPD Decap PI Evaluator v0.23.1: qualify a held L04 closed magnetic auxiliary."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import gc
import json
from math import pi
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime")]
from scipy import sparse
from scipy.sparse.linalg import splu
import probe_astra_l04_10mhz_two_direction_complete_current as two
import probe_astra_l25_l04_joint_magnetic_action as joint

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 120.0, 150.0, 8
OMEGA = 2 * pi * 10_000_000.0
TOTAL, CLOSED = 3_820_411, 644_870
RESEARCH = ROOT / "outputs/research"
CURRENT = RESEARCH / "astra-l04-10mhz-complete-current-gcrotmk-01"
SELF = RESEARCH / "astra-l04-rt0-self-magnetic-01"
PINS = {
    "current_result": (CURRENT / "result.json", "17eec05e5f710020703a40ec62916d3a01c563fa895d3cde1463979e1a2c4f25"),
    "current_guard": (CURRENT / "external-budget.json", "19ade392a5e26be674e0b26bd580a7c5bda112e4c8440ad4221e544da4492fb7"),
    "current_field": (CURRENT / "complete-current-final.npz", "85e8db88de6a07024a7076549d53e7fc4060789cffbca39f229e025ff4fdaa91"),
    "current_driver": (CURRENT / "driver-at-run.py", "c84e6475deee8374b4af0a3ba50df32d1f40b59a2bc5193904a9d348b90573b9"),
    "self_result": (SELF / "result.json", "d8a771b8e6e865b108f4094c81666e3b6a97025776a9f68716a5a92213590ae4"),
    "self_guard": (SELF / "external-budget.json", "099aa86a7d57539bb5caaebc7224acc93916ac8283ed93cf19fc5fd9a99bfb1e"),
    "self_matrix": (SELF / "rt0-self-magnetic.npz", "20d2cfe73a5170b082cf5378e7e4c18c505f8667d808bb022c74d2828377c65c"),
    "self_driver": (SELF / "driver-at-run.py", "31b4336b3450b198a65d9571574558cc8c2e168aa084fbc4a06f86941eade06b"),
    "joint_source": (Path(joint.__file__), "caf5ba8fd0a1a156f987069be4d14e12d5ae8268e613716d120fb6edf2ab6a5e"),
    "two_source": (Path(two.__file__), "6e7466c04d38c4db2d49bfbe6a73e07edb4e418fa582f58b3b192c47aa5c42b0"),
    "contact_source": (Path(joint.ntd.__file__), "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
    "counter": joint.PINS["counter"],
}


def sha(path: Path) -> str:
    import hashlib
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict:
    observed = sha(path)
    if expected is not None:
        assert observed == expected, path
    return {"path": str(path.resolve()), "sha256": observed, "size_bytes": path.stat().st_size}


def save(path: Path, value) -> None:
    path.write_text(json.dumps(two.builtin(value), indent=2, allow_nan=False) + "\n", encoding="utf-8")


def preflight() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    inherited, _ = joint.preflight()
    current = json.loads(PINS["current_result"][0].read_bytes())
    guard = json.loads(PINS["current_guard"][0].read_bytes())
    self_result = json.loads(PINS["self_result"][0].read_bytes())
    self_guard = json.loads(PINS["self_guard"][0].read_bytes())
    assert current["status"] == "UNVALIDATED_COMPLETE_CURRENT_GCROTMK"
    assert current["artifact"]["sha256"] == PINS["current_field"][1]
    assert current["raw_gcrotmk_info"] == 1 and current["original_numerical_gate"] is False
    assert current["positive_progress_screen"] is True
    assert guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0
    assert guard["driver_sha256"] == PINS["current_driver"][1]
    assert self_result["status"] == "COMPLETED_L04_RT0_SELF_MAGNETIC"
    assert self_result["checkpoint"]["file"] == PINS["self_matrix"][0].name
    assert self_result["checkpoint"]["sha256"] == PINS["self_matrix"][1]
    assert self_result["driver"]["sha256"] == PINS["self_driver"][1]
    assert all(self_result["gates"].values())
    assert self_guard["status"] == "COMPLETED_NATIVE_WORKER" and self_guard["exit_code"] == 0
    return {"program": PROGRAM, "version": VERSION,
            "status": "PASS_HELD_L04_CLOSED_MAGNETIC_AUXILIARY_PREFLIGHT",
            "run_released": RUN_RELEASED, "inputs": inputs,
            "qualified_stream_result": inherited["qualified_stream_result"],
            "stream_contract": {
                "stream_system": inherited["qualified_stream_result"]["stream_system"],
                "space": inherited["qualified_stream_result"]["space"],
                "driver_sha256": inherited["qualified_stream_result"]["driver_sha256"],
                "fixed_l04_r_h_tree_recon_receipts": inherited["fixed_l04_r_h_tree_recon_receipts"]},
            "scope": "Receipt-only held preflight; no H/Lself load, matrix assembly, factor, FMM, full action, or continuation."}


def explicit_c(action) -> sparse.csc_matrix:
    index = np.arange(len(action.first), dtype=np.int32)
    first = action.edge_label_first > 0
    second = action.edge_label_second > 0
    rows = np.r_[index[first], index[second]]
    columns = np.r_[action.edge_label_first[first] - 1, action.edge_label_second[second] - 1]
    data = np.r_[-action.orientation[first], action.orientation[second]].astype(float)
    matrix = sparse.coo_matrix((data, (rows, columns)),
                               shape=(len(action.first), action.stream_count - 1)).tocsc()
    matrix.sum_duplicates(); matrix.sort_indices()
    return matrix


def scaled_solve(factor, scaled_rhs: np.ndarray) -> np.ndarray:
    """Solve -D(H+jwK)D*xhat=r_scaled, with no extra D on the RHS."""
    return -factor.solve(scaled_rhs)


def self_check() -> None:
    h = np.array([[3.0, 0.4], [0.4, 2.0]])
    k = np.array([[0.5, 0.1], [0.1, 0.7]])
    d = np.array([2.0, 5.0])
    rhs = np.array([0.3 + 0.2j, -0.4j])
    homega = h + 3j * k
    scaled = np.diag(d) @ homega @ np.diag(d)
    factor = splu(sparse.csc_matrix(scaled))
    correct = scaled_solve(factor, rhs)
    physical = -np.linalg.solve(homega, rhs / d)
    rejected_extra_d = -factor.solve(d * rhs)
    assert np.allclose(correct, physical / d, rtol=2e-13, atol=2e-13)
    assert not np.allclose(rejected_extra_d, correct, rtol=1e-3, atol=1e-3)
    labels_first = np.array([1, 2, 0]); labels_second = np.array([2, 0, 1])
    orientation = np.array([1, -1, 1]); value = np.array([0.2j, 0.3])
    rows = np.arange(3); mask_f, mask_s = labels_first > 0, labels_second > 0
    c = sparse.coo_matrix((np.r_[-orientation[mask_f], orientation[mask_s]],
        (np.r_[rows[mask_f], rows[mask_s]], np.r_[labels_first[mask_f]-1, labels_second[mask_s]-1])),
        shape=(3, 2)).tocsc()
    expected = np.zeros(3, complex); expected[mask_f] -= orientation[mask_f] * value[labels_first[mask_f]-1]
    expected[mask_s] += orientation[mask_s] * value[labels_second[mask_s]-1]
    assert np.array_equal(c @ value, expected)
    json.dumps(two.builtin({"correct": correct, "extra_d_rejected": True}), allow_nan=False)
    print(f"{PROGRAM} v{VERSION}: PASS_HELD_L04_CLOSED_MAGNETIC_AUXILIARY_SELF_CHECK")


def load_lself(action) -> sparse.csc_matrix:
    with np.load(PINS["self_matrix"][0], allow_pickle=False) as archive:
        matrix = sparse.csc_matrix((archive["lself_data"], archive["lself_indices"], archive["lself_indptr"]),
                                   shape=tuple(archive["lself_shape"]))
        assert np.array_equal(archive["branch_first_node"], action.first)
        assert np.array_equal(archive["branch_second_node"], action.second)
    assert matrix.shape == action.resistance.shape and np.isfinite(matrix.data).all()
    return matrix


def worker(output: Path) -> None:
    assert RUN_RELEASED
    assert sha(output / "driver-at-run.py") == sha(Path(__file__))
    budget = joint.ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    try:
        provenance = preflight()
        action = joint.ntd.load_action(provenance["qualified_stream_result"])
        budget.check("loaded and factored qualified real-H ContactNtD")
        lself = load_lself(action)
        with np.load(PINS["current_field"][0], allow_pickle=False) as archive:
            rhs = np.asarray(archive["final_true_residual"][TOTAL:], complex)
            psi_actual = np.asarray(archive["candidate_psi_physical"], complex)
            psi_scaled = np.asarray(archive["candidate_scaled"][TOTAL:], complex)
            force_actual = np.asarray(archive["l04_magnetic_flux_linkage_wb"], complex)
        assert rhs.shape == psi_actual.shape == (CLOSED,)
        assert force_actual.shape == (len(action.first),)
        assert all(np.isfinite(value).all() for value in (rhs, psi_actual, force_actual))
        budget.check("loaded L04 self matrix and actual saved vectors")

        c = explicit_c(action)
        assert c.shape == (len(action.first), CLOSED)
        deterministic = np.exp(1j * (np.arange(CLOSED, dtype=float) + 1) * 1e-6)
        c_actual_relative = two.relative(c @ psi_actual, action.c_apply(psi_actual))
        c_deterministic_relative = two.relative(c @ deterministic, action.c_apply(deterministic))
        ct_actual_relative = two.relative(c.T @ force_actual, action.ct_apply(force_actual))
        budget.check("built and checked explicit C")

        k = (c.T @ lself @ c).tocsc()
        k.sum_duplicates(); k.sort_indices()
        k_actual_relative = two.relative(k @ psi_actual,
            action.ct_apply(lself @ action.c_apply(psi_actual)))
        k_deterministic_relative = two.relative(k @ deterministic,
            action.ct_apply(lself @ action.c_apply(deterministic)))
        d = np.asarray(action.h_scale, float)
        psi_scale_replay = two.relative(d * psi_scaled, psi_actual)
        diagonal_d = sparse.diags(d)
        scaled_k = (diagonal_d @ k @ diagonal_d).tocsc()
        scaled = (action.scaled_h + 1j * OMEGA * scaled_k).tocsc()
        k_scale = max(float(np.max(np.abs(k.data), initial=0.0)), np.finfo(float).tiny)
        scaled_homega_scale = max(float(np.max(np.abs(scaled.data), initial=0.0)), np.finfo(float).tiny)
        k_symmetry = float(np.max(np.abs((k-k.T).data), initial=0.0) / k_scale)
        scaled_homega_symmetry = float(np.max(np.abs((scaled-scaled.T).data), initial=0.0) /
                                         scaled_homega_scale)
        structural = {
            "dimensions": bool(k.shape == scaled_k.shape == scaled.shape == (CLOSED, CLOSED)),
            "finite": bool(np.isfinite(k.data).all() and np.isfinite(scaled_k.data).all() and
                           np.isfinite(scaled.data).all()),
            "k_ordinary_symmetry": bool(k_symmetry <= 2e-12),
            "scaled_homega_ordinary_symmetry": bool(scaled_homega_symmetry <= 2e-12),
            "c_actual_binding": bool(c_actual_relative <= 2e-12),
            "c_deterministic_binding": bool(c_deterministic_relative <= 2e-12),
            "ct_actual_binding": bool(ct_actual_relative <= 2e-12),
            "k_actual_action": bool(k_actual_relative <= 2e-8),
            "k_deterministic_action": bool(k_deterministic_relative <= 2e-8),
            "saved_psi_scale_replay": bool(psi_scale_replay <= 2e-12),
        }
        checkpoint = output / "l04-closed-magnetic-auxiliary-matrices.npz"
        np.savez_compressed(checkpoint,
            k_data=k.data, k_indices=k.indices, k_indptr=k.indptr, k_shape=np.asarray(k.shape),
            scaled_homega_data=scaled.data, scaled_homega_indices=scaled.indices,
            scaled_homega_indptr=scaled.indptr, scaled_homega_shape=np.asarray(scaled.shape),
            h_scale=d, frequency_hz=np.asarray([10_000_000.0]))
        pre_factor = {"program": PROGRAM, "version": VERSION,
            "status": "UNVALIDATED_L04_CLOSED_MAGNETIC_AUXILIARY_MATRIX_CHECKPOINT",
            "inputs": provenance["inputs"], "stream_contract": provenance["stream_contract"],
            "artifact": receipt(checkpoint),
            "counts": {"branches": len(action.first), "closed": CLOSED, "c_nnz": int(c.nnz),
                       "lself_nnz": int(lself.nnz), "k_nnz": int(k.nnz),
                       "scaled_homega_nnz": int(scaled.nnz)},
            "metrics": {"c_actual_relative": c_actual_relative,
                "c_deterministic_relative": c_deterministic_relative,
                "ct_actual_relative": ct_actual_relative,
                "k_actual_action_relative": k_actual_relative,
                "k_deterministic_action_relative": k_deterministic_relative,
                "saved_psi_scale_replay_relative": psi_scale_replay,
                "k_symmetry_relative": k_symmetry,
                "scaled_homega_symmetry_relative": scaled_homega_symmetry},
            "gates": structural, "budget": budget.receipt(),
            "scope": "Static same-triangle L04 self-magnetic closed auxiliary only; no true-operator change, FMM, full action, continuation, or physical/accuracy acceptance."}
        save(output / "matrix-checkpoint.json", pre_factor)
        budget.check("serialized closed magnetic matrix checkpoint")
        assert all(structural.values()), structural

        factor_started = time.perf_counter()
        factor = splu(scaled, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
                      options={"SymmetricMode": True})
        factor_seconds = time.perf_counter() - factor_started
        normal = factor.solve(rhs)
        transpose = factor.solve(rhs, trans="T")
        rhs_norm = max(float(np.linalg.norm(rhs)), np.finfo(float).tiny)
        normal_relative = float(np.linalg.norm(scaled @ normal - rhs) / rhs_norm)
        transpose_relative = float(np.linalg.norm(scaled.T @ transpose - rhs) / rhs_norm)
        diagnostics = output / "factor-diagnostics.npz"
        np.savez_compressed(diagnostics, actual_scaled_closed_rhs=rhs,
                            normal_solution=normal, transpose_solution=transpose,
                            normal_residual=scaled @ normal-rhs,
                            transpose_residual=scaled.T @ transpose-rhs)
        factor_gates = {"normal_actual_rhs_relative_lte_2e_8": bool(normal_relative <= 2e-8),
                        "transpose_actual_rhs_relative_lte_2e_8": bool(transpose_relative <= 2e-8),
                        "finite_solutions": bool(np.isfinite(normal).all() and np.isfinite(transpose).all())}
        result = {"program": PROGRAM, "version": VERSION,
            "status": "PASS_STATIC_L04_CLOSED_MAGNETIC_AUXILIARY" if all(factor_gates.values()) else "STOP_STATIC_L04_CLOSED_MAGNETIC_AUXILIARY_FACTOR_GATE",
            "run_released": RUN_RELEASED, "inputs": provenance["inputs"],
            "stream_contract": provenance["stream_contract"],
            "matrix_checkpoint": receipt(checkpoint), "factor_diagnostics": receipt(diagnostics),
            "matrix_gates": structural, "factor_gates": factor_gates,
            "factor": {"seconds": factor_seconds, "l_nnz": int(factor.L.nnz),
                       "u_nnz": int(factor.U.nnz), "normal_relative": normal_relative,
                       "transpose_relative": transpose_relative},
            "scaled_equation": "Aclosed=-D(H+jwK)D; correction_hat=-factor.solve(r_scaled), with no extra D on the RHS.",
            "budget": budget.receipt(),
            "scope": "Qualified preconditioner candidate only. Real-H ContactNtD P and the true complete-current operator remain unchanged; no FMM, full action, continuation, physical acceptance, error bound, or PowerSI accuracy claim."}
        save(output / "result.json", result)
        budget.check("serialized factor diagnostics and result")
        assert all(factor_gates.values()), factor_gates
        del factor, action, c, lself, k, scaled_k, scaled
        gc.collect()
        save(output / "final-worker-budget.json", {"program": PROGRAM, "version": VERSION,
            "status": "FINAL_WORKER_BUDGET_AFTER_RESULT", "result": receipt(output / "result.json"),
            "budget": budget.receipt()})
        budget.check("serialized final worker budget")
    except BaseException:
        save(output / "failure.json", {"program": PROGRAM, "version": VERSION,
            "status": "STOP_L04_CLOSED_MAGNETIC_AUXILIARY", "failure": traceback.format_exc(),
            "budget": budget.receipt()})
        raise


def launch(output: Path) -> int:
    assert RUN_RELEASED and not output.exists()
    import probe_astra_l25_rt0_p1_pair as counter
    assert sha(Path(counter.__file__)) == PINS["counter"][1]
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    shutil.copy2(Path(__file__), frozen)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    started = time.monotonic(); private = working = 0; reason = guard_failure = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        try:
            print(f"bounded closed magnetic auxiliary watchdog: owned PID={process.pid},150s/8GiB", flush=True)
            while process.poll() is None:
                counters = counter._MemoryCounters(); counters.cb = ctypes.sizeof(counters)
                if not getter(int(process._handle), ctypes.byref(counters), counters.cb):
                    if process.poll() is None:
                        reason = "STOP_PROCESS_MEMORY_QUERY"
                else:
                    private = max(private, int(counters.private_usage))
                    working = max(working, int(counters.working_set))
                if time.monotonic() - started >= EXTERNAL_SECONDS:
                    reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private, working) > MEMORY_GIB * 2**30:
                    reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason:
                    process.kill(); break
                time.sleep(0.5)
            code = process.wait(timeout=10)
        except BaseException:
            guard_failure = traceback.format_exc()
            reason = "STOP_PARENT_GUARD_EXCEPTION"
            if process.poll() is None:
                process.kill()
            code = process.wait(timeout=10)
    status = reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT")
    save(output / "external-budget.json", {"program": PROGRAM, "version": VERSION,
        "status": status, "exit_code": code, "driver_sha256": sha(frozen),
        "worker_command": command, "owned_pid": process.pid,
        "external_seconds": EXTERNAL_SECONDS, "elapsed_s": time.monotonic()-started,
        "max_memory_bytes": MEMORY_GIB*2**30, "printed_memory_cap_gib": MEMORY_GIB,
        "sampled_peak_private_bytes": private, "sampled_peak_working_set_bytes": working,
        "sampling_interval_s": 0.5, "guard_failure": guard_failure})
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
        print(json.dumps(two.builtin(preflight()), indent=2, allow_nan=False)); return
    assert RUN_RELEASED and args.output is not None
    if args.run:
        raise SystemExit(launch(args.output.resolve()))
    worker(args.output.resolve())


if __name__ == "__main__":
    main()
