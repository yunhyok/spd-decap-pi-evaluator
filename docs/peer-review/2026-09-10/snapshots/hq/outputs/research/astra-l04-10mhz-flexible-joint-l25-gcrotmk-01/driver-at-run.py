"""SPD Decap PI Evaluator v0.23.1: disabled bounded 10 MHz flexible GCROT(m, k) field driver."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import gc
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gcrotmk, gmres

import probe_astra_l04_10mhz_joint_cached_one_step as source
import probe_astra_l04_10mhz_l25_joint_schur as schur
import probe_astra_l25_rt0_p1_pair as counter


base, ntd, compact = source.base, source.ntd, source.compact
recon, coupled = source.recon, source.coupled
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
NV, NI, NC, NX, TOTAL = source.NV, source.NI, source.NC, source.NX, source.TOTAL
FREQUENCY_HZ, SOURCE_CURRENT_A = source.FREQUENCY_HZ, source.SOURCE_CURRENT_A
JOINT_SIZE, TRACE_SIZE, PRIMAL_SIZE = source.JOINT_SIZE, source.TRACE_SIZE, source.PRIMAL_SIZE
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 180.0, 210.0, 32.0
OUTER_M, OUTER_K, OUTER_MAXITER = 4, 0, 1
INNER_RESTART, INNER_MAXITER = 4, 1
MAX_OUTER_M, MAX_B1, MAX_R_SOLVES, MAX_Q_SOLVES, MAX_TRUE_ACTIONS = 4, 28, 4, 24, 6
SCHUR = base.R / "astra-l04-10mhz-l25-joint-schur-01"
GCROT_CONTRACT = base.ROOT / "outputs/research/astra-gcrot-one-cycle-call-contract-20260910.json"
PINS = {
    "one_step_source": (Path(source.__file__), "a9ab2e92e90cf04a7acb07fff2c4f4b56f45decd1f69bcf919fc86a3c1c29a4b"),
    "schur_source": (Path(schur.__file__), "36ac874a11b7c87df505d5d37014e13ba7b2fbae17ded30e3d01048133f56817"),
    "schur_result": (SCHUR / "result.json", "b148569d71d7c47485fe52b986340165596b0f48f244ec173cf6269a789e9a3f"),
    "schur_candidate": (SCHUR / "unvalidated-l25-joint-schur-candidate.npz", "f14abb77fe15461c502d43b50b6245d4cc06e251c06b4013b34a7b872dcba30e"),
    "schur_external": (SCHUR / "external-budget.json", "5c55d87dfa080842e34ca873b0086e55ac1a2ece06a1e0a57b8a43f24e2a1c4b"),
    "gcrot_contract": (GCROT_CONTRACT, "48e114addeaa41a7ded9c1be9b98f5e1cf9df7ca2a98d1d8214556302b577a61"),
}


def receipt(path: Path, digest: str | None = None) -> dict:
    found = base.sha(path)
    assert digest is None or found == digest, str(path)
    return {"path": str(path.resolve()), "sha256": found, "size_bytes": path.stat().st_size}


def _pair(value: complex) -> list[float]:
    value = complex(value)
    assert np.isfinite(value)
    return [float(value.real), float(value.imag)]


def _relative(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.linalg.norm(left - right) / max(np.linalg.norm(left), np.linalg.norm(right), np.finfo(float).tiny))


def _empty(size: int, rows: np.ndarray, value: np.ndarray) -> np.ndarray:
    result = np.zeros(size, dtype=np.complex128)
    result[rows] = value
    return result


def _stationary(rhs: np.ndarray, state: np.ndarray, residual: np.ndarray) -> complex:
    """Ordinary transpose bilinear form, deliberately not Hermitian vdot."""
    return complex(np.dot(rhs, state) + np.dot(state, residual))


def self_check() -> None:
    """Pure algebra only; the installed gcrotmk call count is pinned in preflight."""
    p = np.array([[5.0, 1.0, 2.0], [1.0, 4.0, -1.0], [2.0, -1.0, 3.0]], dtype=np.complex128)
    k, q, r = np.array([0]), np.array([1]), np.array([2])
    rhs = np.array([1.0, -2.0, 3.0], dtype=np.complex128)
    z_r = rhs[r] / p[r, r]
    q_k = rhs[k] - p[np.ix_(k, r)] @ z_r
    q_q = rhs[q] - p[np.ix_(q, r)] @ z_r
    b1 = lambda value: value / p[k, k]
    h = q_q - p[np.ix_(q, k)] @ b1(q_k)
    z_q = np.linalg.solve(p[np.ix_(q, q)] - p[np.ix_(q, k)] @ (p[np.ix_(k, q)] / p[k, k]), h)
    z_k = b1(q_k - p[np.ix_(k, q)] @ z_q)
    rho_k = q_k - p[np.ix_(k, k)] @ z_k - p[np.ix_(k, q)] @ z_q
    rho_q = q_q - p[np.ix_(q, k)] @ z_k - p[np.ix_(q, q)] @ z_q
    rho_r = (rhs[r] - p[np.ix_(r, r)] @ z_r) - p[np.ix_(r, k)] @ z_k - p[np.ix_(r, q)] @ z_q
    assert np.linalg.norm(rho_k) < 1e-12 and np.linalg.norm(rho_q) < 1e-12 and np.linalg.norm(rho_r) > 0
    assert (OUTER_M, OUTER_K, OUTER_MAXITER, INNER_RESTART, INNER_MAXITER) == (4, 0, 1, 4, 1)
    assert (MAX_OUTER_M, MAX_B1, MAX_R_SOLVES, MAX_Q_SOLVES, MAX_TRUE_ACTIONS) == (4, 28, 4, 24, 6)
    print(f"{PROGRAM} v{VERSION}: PASS_FLEXIBLE_ONE_R_SWEEP_ALGEBRA")


def verify_inputs() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    inherited = source.verify_inputs()
    schur_inputs = schur.verify()
    result = json.loads(PINS["schur_result"][0].read_text(encoding="utf-8"))
    external = json.loads(PINS["schur_external"][0].read_text(encoding="utf-8"))
    contract = json.loads(PINS["gcrot_contract"][0].read_text(encoding="utf-8"))
    assert result["status"] == "DIAGNOSTIC_UNVALIDATED"
    assert result["driver"]["sha256"] == PINS["schur_source"][1]
    assert result["artifact"]["sha256"] == PINS["schur_candidate"][1]
    assert result["metrics"]["raw_solver_info"] == 4
    assert set(result["source_failed_physical_gates"]) == source.FAILED_PHYSICAL_GATES
    assert external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0
    assert contract["status"] == "PASS_INSTALLED_FLEXIBLE_GCROT_ONE_CYCLE_CONTRACT"
    assert contract["source_sha256"] == "2d9f0c77e4d0a0a43c261f13dced3132c576e8d2fdf88e0fc8f0d3a724e7c9c3"
    assert contract["counts"] == {"A": 6, "M": 4, "callback": 1} and contract["info"] == 1
    # Saved-algebra reconstruction only: it does not invoke a factor or true action.
    with np.load(source.PINS["source_field"][0], allow_pickle=False) as old, np.load(PINS["schur_candidate"][0], allow_pickle=False) as candidate, np.load(schur.PINS["one_step_action"][0], allow_pickle=False) as step:
        assert np.array_equal(old["frequency_hz"], [FREQUENCY_HZ]) and np.array_equal(old["source_current_a"], [SOURCE_CURRENT_A])
        assert np.array_equal(candidate["frequency_hz"], [FREQUENCY_HZ]) and np.array_equal(candidate["source_current_a"], [SOURCE_CURRENT_A])
        assert str(candidate["backsub_artifact_sha256_utf8"][0]) == schur.PINS["backsub_artifact"][1]
        assert str(candidate["trace_array_sha256_utf8"][0]) == schur.PINS["trace_array"][1]
        field = np.r_[old["active_voltage_v"][1:], old["l25_branch_current_a"], old["l04_independent_contact_current_into_sheet_a"]]
        direction, action = np.asarray(candidate["candidate_scaled_step"]), np.asarray(candidate["candidate_true_action"])
        alpha = complex(*result["metrics"]["optimized_alpha"])
        original_residual = np.asarray(step["scaled_initial_residual"])
        assert field.shape == direction.shape == action.shape == original_residual.shape == (TOTAL,)
        with np.load(source.PINS["operator"][0], allow_pickle=False) as operator, np.load(source.PINS["bridge"][0], allow_pickle=False) as bridge:
            y, b, resistance = base.csc(operator, "y")[1:, 1:], base.csc(operator, "b")[1:, :], base.csc(operator, "r")
            diagonal = np.asarray(bridge["l04_contact_diagonal_admittance_s"])
            sv = 1 / np.sqrt(np.asarray(abs(y).sum(1)).ravel() + np.asarray(abs(b).sum(1)).ravel())
            si = 1 / np.sqrt(np.asarray(abs(b).sum(0)).ravel() + np.asarray(abs(resistance).sum(1)).ravel())
            scales = np.r_[sv, si, np.sqrt(abs(diagonal))]
        warm = field / scales + alpha * direction
        warm_residual = original_residual - alpha * action
        assert np.all(np.isfinite(warm)) and np.all(np.isfinite(warm_residual))
    return {"inputs": inputs, "one_step": inherited, "schur": schur_inputs,
            "saved_warm_norm": float(np.linalg.norm(warm)), "saved_warm_residual_norm": float(np.linalg.norm(warm_residual))}


def worker(output: Path) -> None:
    budget = recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    diagnostic = output / "diagnostic-before-gates.json"
    try:
        frozen = output / "driver-at-run.py"
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = verify_inputs()
        reports, factors = {}, {}
        with np.load(source.PINS["static"][0], allow_pickle=False) as archive:
            p = base.csc(archive, "p"); sp = np.asarray(archive["diagonal_scale"], dtype=np.float64)
            joint = np.asarray(archive["joint_native_l14_gauged_potential_indices"], dtype=np.int64)
            contacts = np.asarray(archive["l04_contact_gauged_trace_rows"], dtype=np.int64)
        assert p.shape == (PRIMAL_SIZE, PRIMAL_SIZE) and len(joint) == JOINT_SIZE and len(contacts) == NC
        pkk = base.scaled_block(p, sp, sp); robin = p[JOINT_SIZE:, JOINT_SIZE:].tocsc(); del p
        with np.load(source.PINS["cache_factors"][0], allow_pickle=False) as archive:
            lower, upper, perm_r, perm_c = source._load_public_factor(archive)
            assert np.array_equal(archive["diagonal_scale"], sp) and np.array_equal(archive["joint_native_l14_gauged_potential_indices"], joint)
        b1_calls = {"normal_count": 0, "normal_seconds": 0.0, "transpose_count": 0, "transpose_seconds": 0.0}
        b1 = source._make_b1(pkk, lower, upper, perm_r, perm_c, b1_calls)
        with np.load(source.PINS["operator"][0], allow_pickle=False) as archive:
            y, b, resistance = base.csc(archive, "y")[1:, 1:], base.csc(archive, "b")[1:, :], base.csc(archive, "r")
            native, l14, l25, l02 = base.partition(archive["conditional_global_active_index"])
            positive, negative, gauge = (int(archive[name][0]) for name in ("positive_active_index", "negative_active_index", "gauge_active_index"))
        with np.load(source.PINS["bridge"][0], allow_pickle=False) as archive:
            u, delta, diagonal = base.csc(archive, "l04_u")[1:, :], base.csc(archive, "l04_delta")[1:, 1:], np.asarray(archive["l04_contact_diagonal_admittance_s"])
        sv = 1 / np.sqrt(np.asarray(abs(y).sum(1)).ravel() + np.asarray(abs(b).sum(1)).ravel())
        si = 1 / np.sqrt(np.asarray(abs(b).sum(0)).ravel() + np.asarray(abs(resistance).sum(1)).ravel())
        scales = np.r_[sv, si, np.sqrt(abs(diagonal))]
        primal_scale = np.r_[np.r_[sv, si], sp[JOINT_SIZE:]]; primal_scale[joint] = sp[:JOINT_SIZE]
        K, Q, R = np.r_[joint, np.arange(NX, NX + TRACE_SIZE)], np.r_[l25, np.arange(NV, NX)], l02
        assert len(np.unique(np.r_[K, Q, R])) == NX + TRACE_SIZE and np.array_equal(primal_scale[K], sp)
        assert delta[l25].nnz == 0 and u[l25].nnz == 0
        yq, bq, rq = base.scaled_block(y[l25, :][:, l25], sv[l25], sv[l25]), base.scaled_block(b[l25, :], sv[l25], si), base.scaled_block(resistance, si, si)
        pqq = sparse.bmat([[yq, bq], [bq.T, -rq]], format="csc"); del yq, bq, rq
        prr = base.scaled_block(y[R, :][:, R] + delta[R, :][:, R], sv[R], sv[R])
        base.factor_block("l25_current_schur", pqq, factors, reports, output, base.Events(output / "progress.jsonl"))
        base.factor_block("l02_exact", prr, factors, reports, output, base.Events(output / "progress.jsonl"))
        f_q, f_r = factors["l25_current_schur"], factors["l02_exact"]
        budget.check("cached B1, exact Q, and exact R factors")

        def new_a(value):
            answer = np.r_[y @ value[:NV] + b @ value[NV:], b.T @ value[:NV] - resistance @ value[NV:]]
            answer[:NV] += delta @ value[:NV]
            return answer

        def primal(value):
            return primal_scale * source.primal_full_action(new_a, u, robin, contacts, NV, primal_scale * value)

        _, stream, _ = ntd.verify_contract(); action = ntd.load_action(stream)
        true_actions = 0
        def a_true(value):
            nonlocal true_actions
            true_actions += 1
            assert true_actions <= MAX_TRUE_ACTIONS, "true ContactNtD/A action budget"
            physical = scales * value
            answer = scales * coupled.interface_apply(new_a, u, diagonal, action.apply, physical[:NX], physical[NX:], NV)
            assert np.all(np.isfinite(answer)), "nonfinite true action"
            return answer

        with np.load(source.PINS["source_field"][0], allow_pickle=False) as old, np.load(PINS["schur_candidate"][0], allow_pickle=False) as candidate, np.load(schur.PINS["one_step_action"][0], allow_pickle=False) as saved:
            original = np.r_[old["active_voltage_v"][1:], old["l25_branch_current_a"], old["l04_independent_contact_current_into_sheet_a"]] / scales
            direction = np.asarray(candidate["candidate_scaled_step"], dtype=np.complex128)
            saved_direction_action = np.asarray(candidate["candidate_true_action"], dtype=np.complex128)
            saved_initial_residual = np.asarray(saved["scaled_initial_residual"], dtype=np.complex128)
            alpha = complex(*json.loads(PINS["schur_result"][0].read_text(encoding="utf-8"))["metrics"]["optimized_alpha"])
        warm = original + alpha * direction
        rhs = np.zeros(TOTAL, dtype=np.complex128); rhs[positive - 1], rhs[negative - 1] = sv[positive - 1], -sv[negative - 1]
        rhs_norm = np.linalg.norm(rhs); warm_action = a_true(warm); budget.check("warm true action"); warm_residual = rhs - warm_action
        assert np.all(np.isfinite(warm_residual))
        saved_warm_residual = saved_initial_residual - alpha * saved_direction_action
        warm_replay_absolute = float(np.linalg.norm(warm_residual - saved_warm_residual))
        warm_replay_relative = warm_replay_absolute / max(np.linalg.norm(warm_residual), np.linalg.norm(saved_warm_residual), np.finfo(float).tiny)
        initial_blocks = coupled.residual_metrics(warm_residual, sv, (native, l14, l25, l02), rhs_norm)
        base.atomic_npz(output / "initial-warm-unvalidated-field.npz", scaled_state=warm, physical_state=scales * warm, rhs_scaled=rhs, true_action=warm_action, true_residual=warm_residual, frequency_hz=np.asarray([FREQUENCY_HZ]), source_current_a=np.asarray([SOURCE_CURRENT_A]))
        base.atomic_json(diagnostic, {"program": PROGRAM, "version": VERSION, "status": "WARM_TRUE_RESIDUAL_AND_RECEIPTS_BEFORE_GATES", "driver": receipt(frozen), "inputs": inputs, "warm_relative": float(np.linalg.norm(warm_residual) / rhs_norm), "warm_l02_norm": float(np.linalg.norm(warm_residual[l02])), "warm_saved_replay_absolute": warm_replay_absolute, "warm_saved_replay_relative": warm_replay_relative, "source_raw_solver_info": 4, "source_failed_physical_gates": sorted(source.FAILED_PHYSICAL_GATES), "budget": budget.receipt()})
        assert np.isfinite(warm_replay_absolute) and np.isfinite(warm_replay_relative) and warm_replay_absolute <= 1e-11 + 1e-8 * max(np.linalg.norm(warm_residual), np.linalg.norm(saved_warm_residual))

        records, outer_callbacks = [], []
        total_counters = {"outer_m": 0, "q_solves": 0, "r_solves": 0, "inner_histories": []}
        def primal_solve_scaled(q):
            """One approved R sweep, K/Q Schur solve, and intentionally no final R sweep."""
            z_r = f_r.solve(q[R]); total_counters["r_solves"] += 1; budget.check("R solve")
            r_inverse = _relative(prr @ z_r, q[R]); assert np.isfinite(r_inverse)
            p_r = primal(_empty(NX + TRACE_SIZE, R, z_r)); budget.check("R sweep")
            q_k, q_q = q[K] - p_r[K], q[Q] - p_r[Q]
            b1_residuals, q_residuals, inner_history = [], [], []
            def b1_checked(right):
                answer = b1(right); budget.check("B1 action")
                error = _relative(pkk @ answer, right); b1_residuals.append(error)
                assert np.isfinite(error) and b1_calls["normal_count"] <= MAX_B1
                return answer
            def q_solve(right):
                answer = f_q.solve(right); total_counters["q_solves"] += 1; budget.check("Q solve")
                error = _relative(pqq @ answer, right); q_residuals.append(error)
                assert np.isfinite(error) and total_counters["q_solves"] <= MAX_Q_SOLVES
                return answer
            z_k0 = b1_checked(q_k)
            h = q_q - primal(_empty(NX + TRACE_SIZE, K, z_k0))[Q]; budget.check("Schur rhs")
            def c_apply(value_q):
                zq = q_solve(value_q)
                pkq = primal(_empty(NX + TRACE_SIZE, Q, zq))[K]; budget.check("Schur KQ action")
                answer = pqq @ zq - primal(_empty(NX + TRACE_SIZE, K, b1_checked(pkq)))[Q]
                budget.check("Schur QK action")
                return answer
            inner_atol = .1 * np.linalg.norm(h)
            y_inner, inner_info = gmres(LinearOperator((len(Q), len(Q)), matvec=c_apply, dtype=np.complex128), h, x0=np.zeros(len(Q), complex), restart=INNER_RESTART, maxiter=INNER_MAXITER, rtol=0.0, atol=inner_atol, callback=inner_history.append, callback_type="pr_norm")
            budget.check("bounded inner Schur GMRES")
            z_q = q_solve(y_inner)
            p_q = primal(_empty(NX + TRACE_SIZE, Q, z_q)); budget.check("final Schur KQ action")
            z_k = b1_checked(q_k - p_q[K])
            p_k = primal(_empty(NX + TRACE_SIZE, K, z_k)); budget.check("final Schur QK action")
            rho_k = q_k - pkk @ z_k - p_q[K]
            rho_q = q_q - p_k[Q] - pqq @ z_q
            rho_r = (q[R] - prr @ z_r) - p_k[R] - p_q[R]
            z = np.zeros(NX + TRACE_SIZE, dtype=np.complex128); z[K], z[Q], z[R] = z_k, z_q, z_r
            full_rho = q - primal(z); budget.check("full primal residual identity")
            identity_differences = {"k": float(np.linalg.norm(full_rho[K] - rho_k)), "q": float(np.linalg.norm(full_rho[Q] - rho_q)), "r": float(np.linalg.norm(full_rho[R] - rho_r))}
            roundoff_envelope = float(64 * np.finfo(float).eps * max(1., np.linalg.norm(q), np.linalg.norm(z), np.linalg.norm(full_rho)))
            assert np.all(np.isfinite(z)) and b1_calls["normal_count"] <= MAX_B1 and total_counters["r_solves"] <= MAX_R_SOLVES
            record = {"outer_m_index": total_counters["outer_m"], "inner_info": int(inner_info), "inner_atol": float(inner_atol), "inner_pr_norm_history": [float(item) for item in inner_history], "b1_inverse_relative_max": float(max(b1_residuals)), "q_inverse_relative_max": float(max(q_residuals)), "r_inverse_relative": r_inverse, "rho_k_norm": float(np.linalg.norm(rho_k)), "rho_q_norm": float(np.linalg.norm(rho_q)), "rho_r_norm_intended_nonzero": float(np.linalg.norm(rho_r)), "rho_r_over_qr": float(np.linalg.norm(rho_r) / max(np.linalg.norm(q[R]), np.finfo(float).tiny)), "full_rho_block_identity_differences": identity_differences, "roundoff_envelope": roundoff_envelope, "no_final_r_sweep": True}
            records.append(record); total_counters["inner_histories"].append(record["inner_pr_norm_history"])
            base.atomic_json(output / "preacceptance-flexible-metrics.json", {"program": PROGRAM, "version": VERSION, "status": "METRICS_BEFORE_INVERSE_GATES", "driver": receipt(frozen), "inputs": inputs, "records": records, "counters": total_counters, "b1_calls": b1_calls, "budget": budget.receipt()})
            assert max(b1_residuals) <= 2e-8 and max(q_residuals) <= 2e-8 and r_inverse <= 2e-8
            assert all(np.isfinite(value) and value <= roundoff_envelope for value in identity_differences.values())
            return z

        def m_apply(value):
            total_counters["outer_m"] += 1
            assert total_counters["outer_m"] <= MAX_OUTER_M, "outer preconditioner budget"
            return source.lifted_mna_preconditioner(value, scales, primal_scale, u, diagonal, contacts,
                                                    primal_solve_scaled, NV, NX)

        m_var = LinearOperator((TOTAL, TOTAL), matvec=m_apply, dtype=np.complex128)
        def outer_callback(_correction):
            outer_callbacks.append({"outer_start_only": len(outer_callbacks) + 1})
        correction, raw_info = gcrotmk(LinearOperator((TOTAL, TOTAL), matvec=a_true, dtype=np.complex128), warm_residual, x0=None, M=m_var, m=OUTER_M, k=OUTER_K, CU=[], discard_C=True, maxiter=OUTER_MAXITER, rtol=0.0, atol=1e-9 * rhs_norm, callback=outer_callback)
        budget.check("bounded flexible gcrotmk correction")
        final = warm + correction; final_action = a_true(final); budget.check("final true action"); final_residual = rhs - final_action
        assert np.all(np.isfinite(final)) and np.all(np.isfinite(final_residual))
        assert true_actions <= MAX_TRUE_ACTIONS and total_counters["outer_m"] <= MAX_OUTER_M and b1_calls["normal_count"] <= MAX_B1 and total_counters["r_solves"] <= MAX_R_SOLVES and total_counters["q_solves"] <= MAX_Q_SOLVES
        final_blocks = coupled.residual_metrics(final_residual, sv, (native, l14, l25, l02), rhs_norm)
        warm_relative, final_relative = float(np.linalg.norm(warm_residual) / rhs_norm), float(np.linalg.norm(final_residual) / rhs_norm)
        screen = bool(final_relative <= .8 * warm_relative and np.linalg.norm(final_residual[l02]) <= 2 * np.linalg.norm(warm_residual[l02]))
        raw_z = {"initial": _pair((scales * warm)[positive - 1] - (scales * warm)[negative - 1]), "final": _pair((scales * final)[positive - 1] - (scales * final)[negative - 1])}
        stationary = {"initial": _pair(_stationary(rhs, warm, warm_residual)), "final": _pair(_stationary(rhs, final, final_residual))}
        assert gauge == 0
        del m_var, m_apply, a_true, outer_callback, b1, lower, upper, perm_r, perm_c, pkk, f_q, f_r, factors, pqq, prr, robin
        del y, b, resistance, delta, u, action, new_a, primal, primal_solve_scaled
        gc.collect()
        artifact = output / "unvalidated-flexible-gcrotmk-field.npz"
        final_physical = scales * final
        base.atomic_npz(artifact, warm_scaled_state=warm, final_scaled_state=final, warm_physical_state=scales * warm, final_physical_state=final_physical, active_voltage_v=np.r_[0j, final_physical[:NV]], l25_branch_current_a=final_physical[NV:NX], l04_independent_contact_current_into_sheet_a=final_physical[NX:], rhs_scaled=rhs, warm_true_action=warm_action, final_true_action=final_action, warm_true_residual=warm_residual, final_true_residual=final_residual, source_positive_negative_gauge_active_indices=np.asarray([positive, negative, gauge]), source_field_sha256_utf8=np.asarray([source.PINS["source_field"][1]], dtype="U64"), schur_candidate_sha256_utf8=np.asarray([PINS["schur_candidate"][1]], dtype="U64"), frequency_hz=np.asarray([FREQUENCY_HZ]), source_current_a=np.asarray([SOURCE_CURRENT_A]), source_current_amplitude_a=np.asarray([SOURCE_CURRENT_A]), frequency_operator_sha256_utf8=np.asarray([source.PINS["operator"][1]], dtype="U64"), frequency_bridge_sha256_utf8=np.asarray([source.PINS["bridge"][1]], dtype="U64"))
        result = {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED", "driver": receipt(frozen), "inputs": inputs, "artifact": receipt(artifact), "source_raw_solver_info": 4, "source_failed_physical_gates": sorted(source.FAILED_PHYSICAL_GATES), "raw_gcrotmk_info": int(raw_info), "raw_info_zero": bool(raw_info == 0), "true_action_count": true_actions, "outer_arnoldi_actions": max(0, true_actions - 2), "outer_callback_count": len(outer_callbacks), "preconditioner": {"one_r_sweep": True, "no_final_r_sweep": True, "old_coarse_balancing": False, "counters": total_counters, "b1_calls": b1_calls, "records": records}, "warm_relative": warm_relative, "warm_saved_replay_absolute": warm_replay_absolute, "warm_saved_replay_relative": warm_replay_relative, "final_relative": final_relative, "residual_blocks": {"initial": initial_blocks, "final": final_blocks, "l02_initial_norm": float(np.linalg.norm(warm_residual[l02])), "l02_final_norm": float(np.linalg.norm(final_residual[l02])), "l02_growth": float(np.linalg.norm(final_residual[l02]) / max(np.linalg.norm(warm_residual[l02]), np.finfo(float).tiny))}, "raw_z_unvalidated_ohm": raw_z, "ordinary_transpose_stationary_j_unvalidated_ohm": stationary, "positive_screen_only": screen, "no_extension_after_screen": True, "screen_semantics": "Positive screen is diagnostic only; a negative screen does not prove all flexible methods fail, and neither result extends this bounded run.", "actual_numerical_acceptance": bool(raw_info == 0 and final_relative <= 1e-9), "remaining_validation_gates_required": 23, "physical_operator_replacement_accepted": False, "physical_validation_performed": False, "scope": "One bounded flexible gcrotmk correction from the saved 10 MHz restart4 field plus saved Schur direction. Exact NtD remains authoritative. This run is UNVALIDATED regardless of screen or raw tolerance; no convergence, accuracy, physical pass, PowerSI, or replay claim."}
        base.atomic_json(output / "result.json", result)
        print(json.dumps({"status": result["status"], "raw_gcrotmk_info": result["raw_gcrotmk_info"], "final_relative": final_relative, "positive_screen_only": screen}, allow_nan=False), flush=True)
    except BaseException:
        base.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED", "failure": traceback.format_exc(), "budget": budget.receipt()})
        raise


def launch(output: Path) -> None:
    """Owned-PID 32 GiB guard; this cannot run until HQ releases RUN_RELEASED."""
    assert RUN_RELEASED and output is not None
    availability = source.available_34gib()
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    started = time.monotonic(); private = working = 0; reason = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        print(f"bounded flexible gcrotmk: owned PID={child.pid},{EXTERNAL_SECONDS}s/32GiB", flush=True)
        try:
            while child.poll() is None:
                values = counter._MemoryCounters(); values.cb = ctypes.sizeof(values)
                if getter(int(child._handle), ctypes.byref(values), values.cb):
                    private, working = max(private, int(values.private_usage)), max(working, int(values.working_set))
                elif child.poll() is None:
                    reason = "STOP_PROCESS_MEMORY_QUERY"
                if time.monotonic() - started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private, working) > int(MEMORY_GIB * 2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason: child.kill(); break
                time.sleep(.5)
            code = child.wait(timeout=10)
        except BaseException:
            if child.poll() is None: child.kill(); child.wait(timeout=10)
            raise
    report = {"program": PROGRAM, "version": VERSION, "status": reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT"), "owned_pid": child.pid, "exit_code": code, "elapsed_s": time.monotonic() - started, "sampled_peak_private_bytes": private, "sampled_peak_working_set_bytes": working, "max_runtime_s": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30), "driver_sha256": base.sha(frozen), "worker_command": command, "memory_at_launch": availability}
    base.atomic_json(output / "external-budget.json", report); print(json.dumps(report, allow_nan=False), flush=True)
    raise SystemExit(0 if reason is None and code == 0 else 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true"); modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true"); modes.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight: print(json.dumps({"status": "PASS_DISABLED_FLEXIBLE_GCROTMK_INPUTS", "inputs": verify_inputs()}, sort_keys=True, allow_nan=False))
    elif args.native_worker: assert RUN_RELEASED and args.output is not None; worker(args.output.resolve())
    else: assert RUN_RELEASED and args.run and args.output is not None; launch(args.output.resolve())


if __name__ == "__main__":
    main()
