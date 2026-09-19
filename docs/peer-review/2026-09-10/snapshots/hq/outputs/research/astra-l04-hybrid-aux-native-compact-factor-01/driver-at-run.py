"""SPD Decap PI Evaluator v0.23.1: disabled hybrid-H auxiliary compact-factor probe."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np
from scipy.sparse.linalg import splu

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import probe_astra_l04_sheet_aware_native_compact_factor as compact
import reconstruct_astra_native_loaded_field as recon


ROOT, R = base.ROOT, base.R
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
SIZE, SECONDS, MEMORY_GIB, EXTERNAL_SECONDS = 2_455_688, 210.0, 24.0, 240.0
PINS = {
    "static_result": (R / "astra-l04-hybrid-aux-native-block-01/result.json", "c786147c6c5db1ef8ae8e3f5610c670188e8b482996656e2fb4782a8880922a4"),
    "static": (R / "astra-l04-hybrid-aux-native-block-01/native-l04-hybrid-aux-block.npz", "32d4c9f040c980d91a58e5d51d39e80839a8098a7dfebd950e46e8b7c811dc3b"),
    "static_driver": (R / "astra-l04-hybrid-aux-native-block-01/driver-at-run.py", "0c8e32bf2e59397cadc425e1d76aa0b2b2d6c6c130ae4cbb3deebe0d2e628c65"),
    "static_external": (R / "astra-l04-hybrid-aux-native-block-01/external-budget.json", "101a5859c31c17c75f6bbd625c53ea186739a95541f092063da33ea9f5fa1aa2"),
    "compact_factor_helper": (Path(compact.__file__), "eed904035db4aa0dbcca7c2b85ff8b5d2c0e9b61acfd86527b9d0725e3786acf"),
    "base_helper": (Path(base.__file__), "ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "external_guard": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
}
POLICY = {"permc_spec": "MMD_AT_PLUS_A", "diag_pivot_thresh": 0.0, "SymmetricMode": True}


def receipt(path: Path, digest: str | None = None) -> dict:
    found = base.sha(path)
    assert digest is None or found == digest, str(path)
    return {"path": str(path.resolve()), "sha256": found, "size_bytes": path.stat().st_size}


def pending() -> list[str]:
    return [name for name, (_, digest) in PINS.items() if digest.startswith("PENDING_")]


def verify_released_inputs() -> dict:
    assert not pending(), "static result/artifact/driver pins are pending"
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    result = json.loads(PINS["static_result"][0].read_text(encoding="utf-8"))
    assert result['status'] == 'PASS_STATIC_L04_HYBRID_AUX_NATIVE_BLOCK_NO_FACTOR'
    assert result["artifact"]["sha256"] == PINS["static"][1]
    assert result["driver"]["sha256"] == PINS["static_driver"][1]
    assert result['physical_operator_replacement_accepted'] is False and result['preconditioner_candidate_only'] is True
    assert result['preserved_failed_gates'] == ['raw_hlambda_minus_direct_relative']
    assert result['true_operator_contract']['sha256'] == 'e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8'
    external = json.loads(PINS['static_external'][0].read_bytes())
    assert external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0
    assert compact.scipy.__version__ == '1.18.1'
    return inputs


def self_check() -> None:
    """Use the frozen helper's two-RHS normal/ordinary-transpose public-factor parity check."""
    assert base.sha(Path(compact.__file__)) == PINS['compact_factor_helper'][1]
    compact.self_check()
    assert POLICY == {"permc_spec": "MMD_AT_PLUS_A", "diag_pivot_thresh": 0.0, "SymmetricMode": True}
    assert SIZE == 2_455_688
    print(f"{PROGRAM} v{VERSION}: PASS_HYBRID_AUX_COMPACT_FACTOR_ALGEBRA")


def worker(output: Path) -> None:
    """Measure only the saved auxiliary; keep the original exact NtD authoritative."""
    budget = recon._Budget.create(SECONDS, MEMORY_GIB)
    diagnostic = output / "compact-factor-diagnostic.json"
    try:
        frozen = output / "driver-at-run.py"
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = verify_released_inputs()
        with np.load(PINS["static"][0], allow_pickle=False) as archive:
            matrix = compact.csc(archive, "p")
            scale = np.asarray(archive["diagonal_scale"], dtype=np.float64)
            contact_support = np.asarray(archive["contact_support_index"], dtype=np.int64)
            independent_contacts = np.asarray(archive["independent_contact_indices"], dtype=np.int64)
            gauged_contact_rows = np.asarray(archive["l04_contact_gauged_trace_rows"], dtype=np.int64)
        assert matrix.shape == (SIZE, SIZE) and matrix.nnz == 10930714 and scale.shape == (SIZE,)
        assert np.all(np.isfinite(matrix.data)) and np.all(np.isfinite(scale)) and np.all(scale > 0)
        assert contact_support.ndim == independent_contacts.ndim == gauged_contact_rows.ndim == 1
        assert len(independent_contacts) == len(gauged_contact_rows) == len(contact_support) - 1 == 38277
        assert np.array_equal(independent_contacts, np.delete(np.arange(38278),25440))
        assert np.array_equal(gauged_contact_rows, 1660526+independent_contacts-(independent_contacts>25440))
        scaled = base.scaled_block(matrix, scale, scale)
        assert np.all(np.isfinite(scaled.data))
        preflight = {"program": PROGRAM, "version": VERSION, "status": "PREPARED_HYBRID_AUX_COMPACT_FACTOR",
                     "driver": receipt(frozen), "inputs": inputs,
                     "matrix": {"shape": list(matrix.shape), "nnz": int(matrix.nnz), "unscaled_storage_bytes": compact.storage(matrix), "scaled_storage_bytes": compact.storage(scaled)},
                     "contact_trace_order": {"contact_support_count": len(contact_support), "independent_contact_count": len(independent_contacts), "gauged_contact_trace_rows_count": len(gauged_contact_rows)},
                     "policy": POLICY, "budget": budget.receipt(),
                     "scope": "Auxiliary-only compact factor preparation. Exact ContactNtD remains the true operator; no factor timing or result from another matrix is evidence here."}
        base.atomic_json(output / "pre-factor-static-receipt.json", preflight)
        budget.check("static auxiliary contract")
        started = perf_counter(); factor = splu(scaled, permc_spec=POLICY["permc_spec"], diag_pivot_thresh=POLICY["diag_pivot_thresh"], options={"SymmetricMode": POLICY["SymmetricMode"]}); factor_seconds = perf_counter() - started
        rhs = compact.deterministic_rhs(SIZE)
        started = perf_counter(); reference_normal = factor.solve(rhs); reference_normal_seconds = perf_counter() - started
        started = perf_counter(); reference_transpose = factor.solve(rhs, trans="T"); reference_transpose_seconds = perf_counter() - started
        lower, upper, perm_r, perm_c = factor.L.copy(), factor.U.copy(), factor.perm_r.copy(), factor.perm_c.copy(); pivots = upper.diagonal().copy()
        del matrix; gc.collect(); live_rss = int(recon._rss_bytes())
        del factor; gc.collect(); released_rss = int(recon._rss_bytes())
        started = perf_counter(); public_normal = compact.compact_solve(lower, upper, perm_r, perm_c, rhs); public_normal_seconds = perf_counter() - started
        started = perf_counter(); public_transpose = compact.compact_solve(lower, upper, perm_r, perm_c, rhs, transpose=True); public_transpose_seconds = perf_counter() - started
        artifact = output / "compact-factor-probe.npz"
        base.atomic_npz(artifact, reference_normal=reference_normal, reference_transpose=reference_transpose, public_normal=public_normal, public_transpose=public_transpose, perm_r=perm_r, perm_c=perm_c)
        normal_parity, transpose_parity = compact.rel(public_normal, reference_normal), compact.rel(public_transpose, reference_transpose)
        normal_residual, transpose_residual = compact.rel(scaled @ public_normal, rhs), compact.rel(scaled.T @ public_transpose, rhs)
        metrics = {"policy": POLICY, "rhs_count": 2, "factor_seconds": factor_seconds, "L_nnz": int(lower.nnz), "U_nnz": int(upper.nnz), "L_storage_bytes": compact.storage(lower), "U_storage_bytes": compact.storage(upper), "permutation_storage_bytes": int(perm_r.nbytes + perm_c.nbytes), "pivot_finite": bool(np.all(np.isfinite(pivots))), "pivot_nonzero": bool(np.all(abs(pivots) > 0)), "reference_normal_seconds": reference_normal_seconds, "reference_transpose_seconds": reference_transpose_seconds, "public_normal_seconds": public_normal_seconds, "public_transpose_seconds": public_transpose_seconds, "normal_parity_relative": compact.finite_or_none(normal_parity), "ordinary_transpose_parity_relative": compact.finite_or_none(transpose_parity), "normal_scaled_residual_relative": compact.finite_or_none(normal_residual), "ordinary_transpose_scaled_residual_relative": compact.finite_or_none(transpose_residual), "n_parity_gate_lte_2e_8": bool(np.isfinite(normal_parity) and normal_parity <= 2e-8), "t_parity_gate_lte_2e_8": bool(np.isfinite(transpose_parity) and transpose_parity <= 2e-8), "n_residual_gate_lte_2e_8": bool(np.isfinite(normal_residual) and normal_residual <= 2e-8), "t_residual_gate_lte_2e_8": bool(np.isfinite(transpose_residual) and transpose_residual <= 2e-8), "physical_operator_replacement_accepted": False, "preconditioner_candidate_only": True}
        metrics.update(factor_live_max_private_or_working_bytes=live_rss, post_delete_max_private_or_working_bytes=released_rss, post_delete_delta_bytes=released_rss-live_rss)
        base.atomic_json(diagnostic, {"program": PROGRAM, "version": VERSION, "status": "MEASURED_HYBRID_AUX_COMPACT_FACTOR_PREACCEPTANCE", "driver": receipt(frozen), "inputs": inputs, "artifact": receipt(artifact), "metrics": metrics, "budget": budget.receipt()})
        budget.check("two RHS public-factor normal and ordinary-transpose parity")
        assert metrics["pivot_finite"] and metrics["pivot_nonzero"] and metrics["n_parity_gate_lte_2e_8"] and metrics["t_parity_gate_lte_2e_8"] and metrics["n_residual_gate_lte_2e_8"] and metrics["t_residual_gate_lte_2e_8"]
        base.atomic_json(output / "result.json", {"program": PROGRAM, "version": VERSION, "status": "PASS_COMPACT_PUBLIC_HYBRID_AUX_NATIVE_L04_FACTOR_SINGLE_PROBE", "physical_operator_replacement_accepted": False, "preconditioner_candidate_only": True, "driver": receipt(frozen), "inputs": inputs, "artifact": receipt(artifact), "diagnostic": receipt(diagnostic), "metrics": metrics, "budget": budget.receipt(), "scope": "Auxiliary-only native+L04 hybrid factor; physical trace equivalence remains failed. Exact ContactNtD remains the true operator; no global solve, field, new Z, or accuracy claim."})
    except BaseException:
        base.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_HYBRID_AUX_COMPACT_FACTOR", "traceback": traceback.format_exc(), "budget": budget.receipt()})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}: hybrid-H auxiliary compact-factor probe")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--native-worker", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.preflight:
        inputs = verify_released_inputs()
        print(json.dumps(dict(status='PASS_HYBRID_AUX_FACTOR_INPUTS', inputs=inputs)))
    else:
        assert RUN_RELEASED and not pending() and args.output is not None, "Factor probe held for review"
        output = args.output.resolve()
        if args.native_worker:
            worker(output)
        else:
            assert base.sha(PINS['external_guard'][0]) == PINS['external_guard'][1]
            import probe_astra_fmm3d_runtime as guard
            output.mkdir(parents=True, exist_ok=False)
            (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
            command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(output)]
            raise SystemExit(guard.guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_SECONDS))


if __name__ == "__main__": main()
