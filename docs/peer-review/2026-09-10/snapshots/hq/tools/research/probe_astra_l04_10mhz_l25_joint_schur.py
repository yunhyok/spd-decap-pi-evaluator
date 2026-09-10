"""SPD Decap PI Evaluator v0.23.1: disabled one-cycle L25 joint-Schur diagnostic."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gmres

import probe_astra_l04_10mhz_joint_cached_one_step as source

base, recon, R = source.base, source.recon, source.R
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
TRACE = R / "astra-l04-10mhz-saved-primal-trace-01"
BACKSUB = R / "astra-l04-10mhz-saved-primal-backsubstitution-01"
PINS = {
    "trace_source": (Path(__file__).with_name("diagnose_astra_l04_10mhz_saved_primal_trace.py"), "6c2877ecc930861001c844960e1d2888c29778a259b69d0a664570d490ea5df0"),
    "trace_result": (TRACE / "result.json", "e14e7a7690d0326979ce3da4ce2c74e90a237261ec5b61a458bd5745fbbcb663"),
    "trace_external": (TRACE / "external-budget.json", "24ea860054ab9a99e411cb9da1078344020f33642a0388b65475cc8ce4f038a1"),
    "trace_array": (TRACE / "unvalidated-recovered-auxiliary-trace.npz", "776d3a323cf0a87b410359de7a31e391a9e7478f09b618077f90545e1032c692"),
    "one_step_source": (Path(source.__file__), "a9ab2e92e90cf04a7acb07fff2c4f4b56f45decd1f69bcf919fc86a3c1c29a4b"),
    "one_step_action": (R / "astra-l04-10mhz-joint-cached-one-step-01/unvalidated-cached-joint-one-step-action.npz", "ed0b6dc8a2ff2ab82be8a853802a349369d5fe54ca98b5ca6d5a0154de1b4c7c"),
    "backsub_source": (BACKSUB / "driver-at-run.py", "4aac93d97c05f883863a89d2387f3f5600c292714d528545660a7f27944aa235"),
    "backsub_result": (BACKSUB / "result.json", "b81bd537f6481dad0d78622c639954be62abd8abbd0971a8713240f4f1e43839"),
    "backsub_external": (BACKSUB / "external-budget.json", "72a6eac688c9cc1eabcb66f01e21762186d83844771f9a942d6c72f51ba2dc3a"),
    "backsub_artifact": (BACKSUB / "unvalidated-one-b1-one-ntd-repaired-step.npz", "d501eafef37ac1e9e49f6448a0c9c1344d75e338db774e5facdbea97a9b9349a"),
    "gmres_call_contract": (R / "astra-l25-joint-schur-gmres-call-contract-20260910.json", "d94e483b02668e40ca4b5b64ab3ec1175788f299036bef734d57777741654636"),
}


def receipt(path: Path, digest: str | None = None) -> dict:
    found = base.sha(path)
    assert digest is None or found == digest, str(path)
    return {"path": str(path.resolve()), "sha256": found, "size_bytes": path.stat().st_size}


def self_check() -> None:
    p = np.array([[4., 1., 2., -1.], [1., 3., -2., .5], [2., -2., 5., 1.], [-1., .5, 1., 2.]])
    k, q, r = np.array([0]), np.array([1, 2]), np.array([3])
    pk = p[np.ix_(k, k)]; pqq = p[np.ix_(q, q)]
    b1 = lambda value: value / pk[0, 0]
    e = np.array([1., -2., .5, 3.])
    h = e[q] - p[np.ix_(q, k)] @ b1(e[k])
    m = lambda value: np.linalg.solve(pqq, value)
    c = lambda value: pqq @ m(value) - p[np.ix_(q, k)] @ b1(p[np.ix_(k, q)] @ m(value))
    y = np.linalg.solve(np.column_stack((c(np.eye(2)[:, 0]), c(np.eye(2)[:, 1]))), h)
    dq = m(y); dk = b1(e[k] - p[np.ix_(k, q)] @ dq)
    delta = np.zeros(4); delta[k], delta[q] = dk, dq
    ep = e - p @ delta
    assert np.allclose(h - c(y), ep[q])
    assert np.allclose(ep[k], e[k] - p[np.ix_(k, k)] @ dk - p[np.ix_(k, q)] @ dq)
    assert np.allclose(ep[r] - e[r], -p[np.ix_(r, q)] @ dq - p[np.ix_(r, k)] @ dk)
    tx = np.array([2., 3.]); tt = np.array([5., 7.]); u = np.array([[.2], [.4]]); d = np.array([.6])
    dx, dl = tx * np.array([.1, -.2]), tt * np.array([.3, -.1]); g = np.array([1.])
    assert np.allclose(g - u.T @ dx - d * dl[:1] - g, -u.T @ dx - d * dl[:1])
    assert 1 + 8 <= 9 and 1 + 9 + 1 <= 11
    print(f"{PROGRAM} v{VERSION}: PASS_L25_JOINT_SCHUR_SELF_CHECK")


def verify() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    inherited = source.verify_inputs()
    trace = json.loads(PINS["trace_result"][0].read_text(encoding="utf-8"))
    backsub = json.loads(PINS["backsub_result"][0].read_text(encoding="utf-8"))
    external = json.loads(PINS["backsub_external"][0].read_text(encoding="utf-8"))
    gmres_contract = json.loads(PINS["gmres_call_contract"][0].read_text(encoding="utf-8"))
    assert trace["status"] == "DIAGNOSTIC_UNVALIDATED_SAVED_PRIMAL_TRACE"
    assert trace["driver"]["sha256"] == PINS["trace_source"][1]
    assert trace["artifact"]["sha256"] == PINS["trace_array"][1]
    assert trace["inputs"]["source"]["sha256"] == PINS["one_step_source"][1]
    assert trace["inputs"]["action"]["sha256"] == PINS["one_step_action"][1]
    assert trace["raw_solver_info"] == 4 and trace["original_numerical_gate"] is False
    assert backsub["status"] == "DIAGNOSTIC_UNVALIDATED"
    assert backsub["driver"]["sha256"] == PINS["backsub_source"][1]
    assert backsub["artifact"]["sha256"] == PINS["backsub_artifact"][1]
    assert backsub["inputs"]["trace"]["trace_array"]["sha256"] == PINS["trace_array"][1]
    assert external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0
    assert gmres_contract["status"] == "PASS_INSTALLED_SCIPY_GMRES_BOUNDED_CALL_CONTRACT"
    assert gmres_contract["source_sha256"] == "b467009e4c8853579bd8966002a017b761d4a1979df6eb0b8a62114b3ddab848"
    assert (gmres_contract["arnoldi_calls"], gmres_contract["total_matvec_calls"],
            gmres_contract["final_matvec_input_equals_returned_solution"]) == (8, 9, True)
    assert set(backsub["source_failed_physical_gates"]) == source.FAILED_PHYSICAL_GATES
    return {"inherited": inherited, "trace": inputs, "backsub": backsub}


def worker(output: Path) -> None:
    budget = recon._Budget.create(120, 12)
    try:
        frozen = output / "driver-at-run.py"
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = verify()
        with np.load(source.PINS["static"][0], allow_pickle=False) as archive:
            p = base.csc(archive, "p"); sp = np.asarray(archive["diagonal_scale"])
            joint = np.asarray(archive["joint_native_l14_gauged_potential_indices"])
            contacts = np.asarray(archive["l04_contact_gauged_trace_rows"])
        NJ, NT, NV, NX = source.JOINT_SIZE, source.TRACE_SIZE, source.NV, source.NX
        robin = p[NJ:, NJ:].tocsc(); pkk = base.scaled_block(p, sp, sp)
        assert pkk.shape == (NJ + NT, NJ + NT)
        with np.load(source.PINS["cache_factors"][0], allow_pickle=False) as archive:
            lower, upper, perm_r, perm_c = source._load_public_factor(archive)
            assert np.array_equal(sp, archive["diagonal_scale"])
            assert np.array_equal(joint, archive["joint_native_l14_gauged_potential_indices"])
        b1_calls = {"normal_count": 0, "normal_seconds": 0., "transpose_count": 0, "transpose_seconds": 0.}
        b1 = source._make_b1(pkk, lower, upper, perm_r, perm_c, b1_calls)
        budget.check("cached B1 loaded before exact L25 P_QQ factor")
        with np.load(source.PINS["operator"][0], allow_pickle=False) as archive:
            y = base.csc(archive, "y")[1:, 1:]; b = base.csc(archive, "b")[1:, :]; resistance = base.csc(archive, "r")
            native, l14, l25, l02 = base.partition(archive["conditional_global_active_index"])
            positive, negative, gauge = (int(archive[name][0]) for name in ("positive_active_index", "negative_active_index", "gauge_active_index"))
            assert np.array_equal(archive["frequency_hz"], [source.FREQUENCY_HZ])
            assert np.array_equal(archive["source_current_a"], [source.SOURCE_CURRENT_A])
        with np.load(source.PINS["bridge"][0], allow_pickle=False) as archive:
            u = base.csc(archive, "l04_u")[1:, :]; delta = base.csc(archive, "l04_delta")[1:, 1:]
            d = np.asarray(archive["l04_contact_diagonal_admittance_s"])
        assert (positive, negative, gauge) == (2699, 2656, 0)
        assert delta[l25].nnz == 0 and u[l25].nnz == 0
        sv = 1 / np.sqrt(np.asarray(abs(y).sum(1)).ravel() + np.asarray(abs(b).sum(1)).ravel())
        si = 1 / np.sqrt(np.asarray(abs(b).sum(0)).ravel() + np.asarray(abs(resistance).sum(1)).ravel())
        S = np.r_[sv, si, np.sqrt(abs(d))]
        T = np.r_[sv, si, sp[NJ:]]; T[joint] = sp[:NJ]
        K = np.r_[joint, np.arange(NX, NX + NT)]
        Q = np.r_[l25, np.arange(NV, NX)]
        R = l02
        assert np.array_equal(T[K], sp) and len(np.unique(np.r_[K, Q, R])) == NX + NT
        yq = base.scaled_block(y[l25, :][:, l25], sv[l25], sv[l25])
        bq = base.scaled_block(b[l25, :], sv[l25], si)
        rq = base.scaled_block(resistance, si, si)
        pqq = sparse.bmat([[yq, bq], [bq.T, -rq]], format="csc")
        del yq, bq, rq
        assert pqq.shape == (len(Q), len(Q))
        factors, reports = {}, {}
        budget.check("exact L25 P_QQ assembled before its sole factor")
        base.factor_block("l25_current_schur", pqq, factors, reports, output, base.Events(output / "progress.jsonl"))
        f_q = factors["l25_current_schur"]
        budget.check("cached B1 and exact L25 P_QQ factor loaded")

        def new_a(value):
            answer = np.r_[y @ value[:NV] + b @ value[NV:], b.T @ value[:NV] - resistance @ value[NV:]]
            answer[:NV] += delta @ value[:NV]
            return answer

        def primal(value):
            return T * source.primal_full_action(new_a, u, robin, contacts, NV, T * value)

        with np.load(PINS["one_step_action"][0], allow_pickle=False) as archive:
            sr = np.asarray(archive["scaled_initial_residual"])
            original_step = np.asarray(archive["scaled_cached_joint_preconditioned_step"])
        with np.load(PINS["trace_array"][0], allow_pickle=False) as archive:
            original_lambda = np.asarray(archive["reconstructed_auxiliary_trace_v"])
        with np.load(PINS["backsub_artifact"][0], allow_pickle=False) as archive:
            repaired_step = np.asarray(archive["repaired_scaled_step"])
            saved_dz = np.asarray(archive["full_dz"])
            assert str(archive["trace_array_sha256_utf8"][0]) == PINS["trace_array"][1]
            assert str(archive["one_step_source_sha256_utf8"][0]) == PINS["one_step_source"][1]
            assert str(archive["one_step_action_sha256_utf8"][0]) == PINS["one_step_action"][1]
        assert repaired_step.shape == sr.shape == original_step.shape == (source.TOTAL,)
        assert saved_dz.shape == (NX + NT,) and original_lambda.shape == (NT,)
        original_physical = S * original_step; x_original, g_original = original_physical[:NX], original_physical[NX:]
        xg0 = S * repaired_step; x0, g0 = xg0[:NX], xg0[NX:]
        saved_dx = T[:NX] * saved_dz[:NX]; saved_dl = T[NX:] * saved_dz[NX:]
        repaired_g_expected = g_original - u.T @ saved_dx[:NV] - d * saved_dl[contacts]
        repaired_g_relative = float(np.linalg.norm(g0 - repaired_g_expected) / max(np.linalg.norm(g0), np.linalg.norm(repaired_g_expected), np.finfo(float).tiny))
        assert repaired_g_relative <= 2e-8
        trace0 = original_lambda + T[NX:] * saved_dz[NX:]
        z0 = np.r_[x0, trace0] / T
        r_phys = sr / S; rx, rg = r_phys[:NX], r_phys[NX:]
        rp_phys = np.zeros(NX + NT, complex)
        rp_phys[:NV] = rx[:NV] - u @ rg; rp_phys[NV:NX] = rx[NV:NX]; rp_phys[NX + contacts] = -d * rg
        q = T * rp_phys; e = q - primal(z0)
        budget.check("repaired baseline and full primal residual reconstructed")

        def embed(rows, value):
            answer = np.zeros(NX + NT, complex); answer[rows] = value; return answer

        q_inverse_residuals, b1_residuals = [], []

        def m_q(value):
            answer = f_q.solve(value)
            q_inverse_residuals.append(float(np.linalg.norm(pqq @ answer - value) / max(np.linalg.norm(value), np.finfo(float).tiny)))
            return answer

        def b1_apply(value):
            answer = b1(value)
            b1_residuals.append(float(np.linalg.norm(value - pkk @ answer) / max(np.linalg.norm(value), np.finfo(float).tiny)))
            return answer

        initial_k = b1_apply(e[K])
        h = e[Q] - primal(embed(K, initial_k))[Q]
        budget.check("initial Schur rhs from one cached B1 action")
        c_calls, callback_history, last_c = 0, [], [None, None]

        def c_apply(value):
            nonlocal c_calls
            c_calls += 1
            mq = m_q(value)
            pkq_mq = primal(embed(Q, mq))[K]
            response = pqq @ mq - primal(embed(K, b1_apply(pkq_mq)))[Q]
            last_c[:] = [value.copy(), response.copy()]
            budget.check("Schur GMRES action")
            return response

        target = .1 * np.linalg.norm(h)
        operator = LinearOperator((len(Q), len(Q)), matvec=c_apply, dtype=np.complex128)
        y_gmres, gmres_info = gmres(operator, h, x0=np.zeros(len(Q), complex), restart=8, maxiter=1,
                                    rtol=0, atol=target, callback=callback_history.append, callback_type="pr_norm")
        assert c_calls <= 9 and len(callback_history) <= 8
        budget.check("single restart-8 Schur GMRES phase complete")
        assert last_c[0] is not None and np.array_equal(last_c[0], y_gmres)
        delta_q = m_q(y_gmres)
        pkq_delta = primal(embed(Q, delta_q))[K]
        delta_k = b1_apply(e[K] - pkq_delta)
        delta_z = np.zeros(NX + NT, complex); delta_z[Q], delta_z[K] = delta_q, delta_k
        assert np.all(delta_z[R] == 0)
        z_candidate = z0 + delta_z; e_prime = q - primal(z_candidate)
        p_rq_delta = primal(embed(Q, delta_q))[R]
        p_rk_delta = primal(embed(K, delta_k))[R]
        r_induced = -(p_rq_delta + p_rk_delta)
        outside_identity = e_prime[R] - e[R] + p_rq_delta + p_rk_delta
        k_rhs = e[K] - pkq_delta
        k_identity = e_prime[K] - (k_rhs - pkk @ delta_k)
        full_identity = e_prime - e + primal(delta_z)
        schur_residual = e_prime[Q]
        schur_cache_identity = schur_residual - (h - last_c[1])
        forward_envelope = 64 * np.finfo(float).eps * max(1., np.linalg.norm(q), np.linalg.norm(e), np.linalg.norm(delta_z))
        budget.check("final full primal residual and block identities measured")
        delta_x = T[:NX] * delta_z[:NX]; delta_lambda = T[NX:] * delta_z[NX:]
        candidate_g = g0 - u.T @ delta_x[:NV] - d * delta_lambda[contacts]
        candidate_scaled = np.r_[x0 + delta_x, candidate_g] / S
        assert np.array_equal(z_candidate[R], z0[R])
        pre_ntd = {
            "repaired_g_relative": repaired_g_relative,
            "q_inverse_residuals": q_inverse_residuals,
            "q_inverse_relative_max": max(q_inverse_residuals),
            "schur_relative": float(np.linalg.norm(schur_residual) / max(np.linalg.norm(h), np.finfo(float).tiny)),
            "b1_residuals": b1_residuals,
            "b1_relative_max": max(b1_residuals),
            "k_identity": float(np.linalg.norm(k_identity)), "r_identity": float(np.linalg.norm(outside_identity)),
            "full_identity": float(np.linalg.norm(full_identity)), "schur_cache_identity": float(np.linalg.norm(schur_cache_identity)),
            "forward_roundoff_envelope": float(forward_envelope),
            "r_outside_consequence": {
                "initial_norm": float(np.linalg.norm(e[R])),
                "initial_squared": float(np.vdot(e[R], e[R]).real),
                "induced_norm": float(np.linalg.norm(r_induced)),
                "induced_squared": float(np.vdot(r_induced, r_induced).real),
                "final_norm": float(np.linalg.norm(e_prime[R])),
                "final_squared": float(np.vdot(e_prime[R], e_prime[R]).real),
                "final_to_initial_growth": float(np.linalg.norm(e_prime[R]) / max(np.linalg.norm(e[R]), np.finfo(float).tiny)),
            },
            "full_primal_relative": float(np.linalg.norm(e_prime) / max(np.linalg.norm(q), np.finfo(float).tiny)),
            "b1_calls": b1_calls.copy(), "schur_action_count": c_calls,
            "gmres": {"info": int(gmres_info), "callback_type": "pr_norm", "callback_count": len(callback_history),
                      "relative_residual_history": callback_history.copy(),
                      "restart": 8, "maxiter": 1, "rtol": 0., "atol": float(target)},
            "block_squared_mass": {name: float(np.vdot(e_prime[rows], e_prime[rows]).real) for name, rows in
                                    (("k", K), ("q", Q), ("r", R))},
            "block_norms": {name: float(np.linalg.norm(e_prime[rows])) for name, rows in (("k", K), ("q", Q), ("r", R))},
        }
        base.atomic_json(output / "preacceptance-primal-metrics.json", pre_ntd)
        budget.check("preacceptance primal metrics saved")
        assert b1_calls["normal_count"] <= 11 and b1_calls["transpose_count"] == 0
        assert pre_ntd["k_identity"] <= forward_envelope and pre_ntd["r_identity"] <= forward_envelope and pre_ntd["full_identity"] <= forward_envelope and pre_ntd["schur_cache_identity"] <= forward_envelope
        del operator, c_apply, b1_apply, m_q, last_c, callback_history, b1, lower, upper, perm_r, perm_c, pkk, p, robin, primal, f_q, factors, pqq, reports
        del initial_k, h, pkq_delta, k_rhs, e, e_prime, q, z0, z_candidate, rp_phys, r_phys
        del original_physical, xg0, original_lambda, saved_dz, trace0, saved_dx, saved_dl, repaired_g_expected
        gc.collect(); budget.check("cached B1 and L25 factor released before exact ContactNtD")
        _, stream, _ = source.ntd.verify_contract(); loaded_ntd = source.ntd.load_action(stream)
        budget.check("exact ContactNtD loaded")
        ntd_calls = 0

        def counted_ntd(value):
            nonlocal ntd_calls
            ntd_calls += 1
            return loaded_ntd.apply(value)

        def full_action(value):
            physical = S * value
            return S * source.coupled.interface_apply(new_a, u, d, counted_ntd, physical[:NX], physical[NX:], NV)

        candidate_action = full_action(candidate_scaled)
        budget.check("one exact ContactNtD action")
        unit_residual = sr - candidate_action
        denominator = np.vdot(candidate_action, candidate_action)
        assert np.isfinite(denominator) and denominator.real > 0
        alpha = np.vdot(candidate_action, sr) / denominator
        optimized_residual = sr - alpha * candidate_action
        rhs = np.zeros_like(sr); rhs[positive - 1], rhs[negative - 1] = sv[positive - 1], -sv[negative - 1]
        rhs_norm = np.linalg.norm(rhs); tiny = np.finfo(float).tiny
        blocks = {"initial": source.coupled.residual_metrics(sr, sv, (native, l14, l25, l02), rhs_norm),
                  "unit": source.coupled.residual_metrics(unit_residual, sv, (native, l14, l25, l02), rhs_norm),
                  "optimized": source.coupled.residual_metrics(optimized_residual, sv, (native, l14, l25, l02), rhs_norm)}
        metrics = {"status": "DIAGNOSTIC_UNVALIDATED", **pre_ntd, "ntd_apply_count": ntd_calls,
                   "initial_global_rhs_relative": float(np.linalg.norm(sr) / rhs_norm),
                   "unit_global_rhs_relative": float(np.linalg.norm(unit_residual) / rhs_norm),
                   "optimized_global_rhs_relative": float(np.linalg.norm(optimized_residual) / rhs_norm),
                   "unit_ratio_to_saved_residual": float(np.linalg.norm(unit_residual) / max(np.linalg.norm(sr), tiny)),
                   "optimized_ratio_to_saved_residual": float(np.linalg.norm(optimized_residual) / max(np.linalg.norm(sr), tiny)),
                   "optimized_alpha": source._pair(alpha), "residual_blocks": blocks,
                   "raw_solver_info": 4, "original_numerical_gate": False,
                   "source_failed_physical_gates": sorted(source.FAILED_PHYSICAL_GATES)}
        gates = {"schur_reduced_lte_0p1": metrics["schur_relative"] <= .1,
                 "b1_residual_lte_2e_8": metrics["b1_relative_max"] <= 2e-8,
                 "l25_inverse_lte_2e_8": metrics["q_inverse_relative_max"] <= 2e-8,
                 "optimized_true_global_lte_0p007553155703": metrics["optimized_global_rhs_relative"] <= .007553155703,
                 "finite": bool(np.all(np.isfinite(candidate_action)) and np.isfinite(alpha) and all(np.isfinite(value) for value in (metrics["q_inverse_relative_max"], metrics["schur_relative"], metrics["b1_relative_max"], metrics["full_primal_relative"]))),
                 "one_ntd_action": ntd_calls == 1}
        metrics["decision_gates"] = gates
        metrics["decision_gates_all_pass"] = all(gates.values())
        base.atomic_json(output / "metrics-before-decision.json", metrics)
        budget.check("preacceptance true-action metrics saved")
        assert ntd_calls == 1
        artifact = output / "unvalidated-l25-joint-schur-candidate.npz"
        base.atomic_npz(artifact, candidate_scaled_step=candidate_scaled, candidate_true_action=candidate_action,
                        scaled_input_residual=sr, delta_z=delta_z, delta_q=delta_q, delta_k=delta_k,
                        backsub_artifact_sha256_utf8=np.asarray([PINS["backsub_artifact"][1]], dtype="U64"),
                        trace_array_sha256_utf8=np.asarray([PINS["trace_array"][1]], dtype="U64"),
                        frequency_hz=np.asarray([source.FREQUENCY_HZ]), source_current_a=np.asarray([source.SOURCE_CURRENT_A]),
                        diagnostic_status_utf8=np.asarray(["DIAGNOSTIC_UNVALIDATED"], dtype="U64"))
        artifact_receipt = receipt(artifact)
        del counted_ntd, full_action, loaded_ntd, new_a, y, b, resistance, delta, u
        del sr, original_step, repaired_step, candidate_action, unit_residual, optimized_residual, rhs
        gc.collect(); budget.check("operator owners released after diagnostic artifact")
        recommendation = ("POSITIVE_SCREEN_NO_AUTOMATIC_CONTINUATION: future use requires flexible outer or fixed linear approximation."
                          if metrics["decision_gates_all_pass"]
                          else "NEGATIVE_SCREEN_NO_CONTINUATION_RECOMMENDED")
        base.atomic_json(output / "result.json", {
            "program": PROGRAM, "version": VERSION, "status": "DIAGNOSTIC_UNVALIDATED", "driver": receipt(frozen),
            "inputs": inputs, "artifact": artifact_receipt, "metrics": metrics,
            "source_failed_physical_gates": sorted(source.FAILED_PHYSICAL_GATES),
            "recommendation": recommendation,
            "negative_result_scope": "A negative screen does not prove all Krylov methods fail.",
            "budget": budget.receipt(),
            "scope": "One restart-8 right-preconditioned L25 Schur diagnostic and one exact ContactNtD action. The fixed-eight-step inner GMRES is RHS-dependent and nonlinear as an inverse approximation; it must not be wrapped in the old right-preconditioned LGMRES. Future use requires a flexible outer method or a genuinely fixed linear approximation. No outer implementation, new joint or L02 factor, field, convergence, H-accuracy, causal, broadband, or PowerSI claim.",
        })
    except BaseException:
        base.atomic_json(output / "failure.json", {"status": "DIAGNOSTIC_UNVALIDATED", "failure": "L25_JOINT_SCHUR", "traceback": traceback.format_exc(), "budget": budget.receipt()})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true"); modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true"); modes.add_argument("--native-worker", action="store_true")
    parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check(); return
    if args.preflight: verify(); print("PASS_L25_JOINT_SCHUR_INPUTS"); return
    assert RUN_RELEASED and args.output is not None
    output = args.output.resolve()
    if args.native_worker: worker(output); return
    import probe_astra_fmm3d_runtime as guard
    assert base.sha(Path(guard.__file__)) == source.PINS["guard"][1]
    output.mkdir(parents=True, exist_ok=False); (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guard.guarded_source_worker(output, worker_command=[sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)], max_runtime_s=150.0))


if __name__ == "__main__": main()
