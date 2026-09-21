"""SPD Decap PI Evaluator v0.23.1: held complete-current GCROT continuation."""
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

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime"), str(ROOT / "outputs/research-fmm-runtime")]
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gcrotmk, splu

RUN_RELEASED = True
import probe_astra_l04_10mhz_three_direction_complete_current as third
import probe_astra_l04_10mhz_two_direction_complete_current as two
import probe_astra_l25_l04_joint_magnetic_action as joint
import qualify_astra_l04_10mhz_closed_magnetic_auxiliary as aux

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 1200.0, 1320.0, 32.0
TOTAL, CLOSED = 3_820_411, 644_870
ABSOLUTE_TOLERANCE = 1.0010814007331201e-12
WARM_NORM = 1.3963924149604572
FULL_PROGRESS_CAP = 0.7912610103778645
GAP_PROGRESS_CAP_OHM = 0.00010279694220459625
RESEARCH = ROOT / "outputs/research"
THIRD = RESEARCH / "astra-l04-10mhz-three-direction-complete-current-01"
AUXILIARY = RESEARCH / "astra-l04-10mhz-closed-magnetic-auxiliary-01"
PINS = {
    "third_result": (THIRD / "result.json", "3e5704ef3bf96592fd52b62de2c198fcc960da4dbbde1e98f2f505d8af1de590"),
    "third_guard": (THIRD / "external-budget.json", "e62b6756ced3b4ce6a5a3f33750bc062c80b62209cbd0b26c3b9596d52b8e0c5"),
    "third_arrays": (THIRD / "three-direction-fit-before-gates.npz", "79c3ff3f9ab87591c5c762851f3baff1b4d76a2143e433fcb456989c123987de"),
    "third_driver": (THIRD / "driver-at-run.py", "991a1f488f6aa520bb9b909774e8030da12f81ab4e84a56f6698124883ed8f0c"),
    "third_source": (Path(third.__file__), "991a1f488f6aa520bb9b909774e8030da12f81ab4e84a56f6698124883ed8f0c"),
    "two_source": (Path(two.__file__), "6e7466c04d38c4db2d49bfbe6a73e07edb4e418fa582f58b3b192c47aa5c42b0"),
    "joint_source": (Path(joint.__file__), "caf5ba8fd0a1a156f987069be4d14e12d5ae8268e613716d120fb6edf2ab6a5e"),
    "magnetic_diagnostic": third.PINS["magnetic_physical_diagnostic"],
    "frequency_operator": two.PINS["frequency_operator"],
    "frequency_bridge": two.PINS["frequency_bridge"],
    "prior_arrays": two.PINS["prior_arrays"],
    "prior_initial": two.PINS["prior_initial"],
    "aux_source": (Path(aux.__file__), "f49ceb023a1b1ce5aaaec6f329b7623f2f79dd3d3301dc8f64ac054ce2e35e0f"),
    "aux_driver": (AUXILIARY / "driver-at-run.py", "f49ceb023a1b1ce5aaaec6f329b7623f2f79dd3d3301dc8f64ac054ce2e35e0f"),
    "aux_result": (AUXILIARY / "result.json", "8dcab2dd118a7711dadd37e38f0f936986f842d57ab826fe5f0674f9f5f958c4"),
    "aux_guard": (AUXILIARY / "external-budget.json", "16492f5db2639fa54a5077c104c4fc1d98595ee628b4a31d2fdf6f859b11871f"),
    "aux_matrix": (AUXILIARY / "l04-closed-magnetic-auxiliary-matrices.npz", "80fd946fba12022a6dfe3a1fa6534001ed6b1e5d4239b2cae8540a879e17904b"),
    "aux_diagnostics": (AUXILIARY / "factor-diagnostics.npz", "aadb495583e53e30ae3ff5740c2948d796d8d39d83fa5eeeae773320acf7c11b"),
}


class StopBeforeAction(RuntimeError):
    pass


def receipt(path, digest=None):
    return two.receipt(path, digest)


def save(path, value):
    two.save_json(path, value)


def preflight():
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    result = json.loads(PINS["third_result"][0].read_bytes())
    guard = json.loads(PINS["third_guard"][0].read_bytes())
    chain = third.preflight()
    aux_result = json.loads(PINS["aux_result"][0].read_bytes())
    aux_guard = json.loads(PINS["aux_guard"][0].read_bytes())
    aux_chain = aux.preflight()
    assert result["artifact"]["sha256"] == PINS["third_arrays"][1]
    assert result["raw_gcrotmk_info"] == 1 and result["screen_pass"] is False
    assert all(result["gates"].values()) and guard["status"] == "COMPLETED_NATIVE_WORKER"
    assert guard["exit_code"] == 0 and guard["driver_sha256"] == PINS["third_driver"][1]
    failures = ["l25_constitutive", "matrix_global_circuit_kcl", "matrix_identity_power",
                "physical_power_closure", "physical_source_global_circuit_kcl"]
    assert result["actual_magnetic_physical_failures"] == failures
    assert aux_result["matrix_checkpoint"]["sha256"] == PINS["aux_matrix"][1]
    assert aux_result["factor_diagnostics"]["sha256"] == PINS["aux_diagnostics"][1]
    assert all(aux_result["matrix_gates"].values()) and all(aux_result["factor_gates"].values()) and aux_guard["exit_code"] == 0
    assert aux_guard["driver_sha256"] == PINS["aux_driver"][1]
    return {"program": PROGRAM, "version": VERSION,
            "status": "PASS_HELD_COMPLETE_CURRENT_GCROTMK_PREFLIGHT",
            "run_released": RUN_RELEASED, "inputs": inputs, "third_helper_preflight": chain, "auxiliary_preflight": aux_chain,
            "raw_info_expected": 1, "actual_magnetic_physical_failures": failures,
            "scope": "Receipt-only held preflight; no M, H, FMM, or complete action."}


def reconstruct_action(captured_z, captured_w, correction):
    z = np.column_stack(captured_z); w = np.column_stack(captured_w)
    norms = np.linalg.norm(z, axis=0)
    assert np.all(np.isfinite(norms)) and np.all(norms > 0)
    normalized = z / norms
    scaled_coefficients, _, rank, singular = np.linalg.lstsq(normalized, correction, rcond=1e-12)
    coefficients = scaled_coefficients / norms
    reconstructed = z @ coefficients; action = w @ coefficients
    condition = float(singular[0] / max(singular[-1], np.finfo(float).tiny))
    replay = two.relative(reconstructed, correction)
    return z, w, coefficients, action, {"rank": int(rank), "column_count": len(captured_z),
        "normalized_condition": condition, "correction_replay_relative": replay,
        "singular_values": singular, "column_norms": norms,
        "gate": bool(rank == len(captured_z) and condition <= 1e6 and replay <= 2e-8)}


def self_check():
    aux.self_check()
    calls = {"a": 0, "m": 0}
    matrix = np.array([[3.0, 1j, 0.2, 0.0], [-1j, 2.0, 0.0, -0.1],
                       [0.2, 0.0, 4.0, 0.3j], [0.0, -0.1, -0.3j, 2.5]], complex)
    warm = np.array([0.2 - 0.1j, -0.3 + 0.4j, 0.1j, -0.2])
    rhs = np.array([1 + 2j, -0.5j, 0.3 - 0.2j, -0.4 + 0.1j])
    warm_residual = rhs - matrix @ warm; captured_z, captured_w = [], []
    def action(value):
        calls["a"] += 1; answer = matrix @ value
        captured_z.append(value.copy()); captured_w.append(answer.copy()); return answer
    def precondition(value):
        calls["m"] += 1; return value / (3 + 0.01 * calls["m"])
    correction, info = gcrotmk(LinearOperator((4, 4), matvec=action, dtype=np.complex128),
        warm_residual, x0=None, M=LinearOperator((4, 4), matvec=precondition, dtype=np.complex128),
        m=2, k=0, CU=None, discard_C=True, maxiter=1, rtol=0.0, atol=1e-12)
    z, w, coefficients, a_delta, replay = reconstruct_action(captured_z, captured_w, correction)
    explicit = rhs - matrix @ (warm + correction); algebraic = warm_residual - a_delta
    assert calls["a"] <= 2 and calls["m"] <= 2 and info in (0, 1)
    tiny_replay = float(np.linalg.norm(explicit - algebraic) / np.linalg.norm(rhs))
    assert replay["gate"] and tiny_replay <= 2e-12 and np.linalg.norm(explicit) > 1e-8
    assert z.shape == w.shape and coefficients.shape == (z.shape[1],)
    third.self_check()
    json.dumps(two.builtin({"calls": calls, "raw_info": info, "replay": replay}), allow_nan=False)
    print(f"{PROGRAM} v{VERSION}: PASS_HELD_COMPLETE_CURRENT_GCROTMK_SELF_CHECK")


def load_warm_and_scales():
    with np.load(PINS["third_arrays"][0], allow_pickle=False) as archive:
        base = np.asarray(archive["candidate_base_scaled"], complex)
        psi_physical = np.asarray(archive["candidate_psi"], complex)
        warm_residual = np.asarray(archive["r3"], complex)
    with np.load(PINS["prior_arrays"][0], allow_pickle=False) as archive:
        sv, si, sc, sh = (np.asarray(archive[name]) for name in
                          ("scales_sv", "scales_si", "scales_sc", "scales_sh"))
    with np.load(PINS["prior_initial"][0], allow_pickle=False) as archive:
        base_rhs = np.asarray(archive["rhs_scaled"], complex)
    psi_scaled = psi_physical / sh
    psi_replay = two.relative(sh * psi_scaled, psi_physical)
    rhs = np.r_[base_rhs, np.zeros(len(sh), complex)]
    assert base.shape == (TOTAL,) and psi_scaled.shape == sh.shape == (CLOSED,)
    assert warm_residual.shape == rhs.shape == (TOTAL + CLOSED,)
    assert abs(np.linalg.norm(base_rhs) - 0.00100108140073312) <= ABSOLUTE_TOLERANCE
    assert abs(np.linalg.norm(warm_residual) - WARM_NORM) <= 2e-9 and psi_replay <= 2e-8
    return np.r_[base, psi_scaled], warm_residual, rhs, sv, si, sc, sh, psi_replay


def worker(output):
    budget = joint.ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB); stop_receipts = []
    try:
        assert (output / "driver-at-run.py").read_bytes() == Path(__file__).read_bytes()
        provenance = preflight()
        warm, warm_residual, rhs, sv, si, sc, sh, psi_replay = load_warm_and_scales()
        scales = np.r_[sv, si, sc]
        counts = {"M": 0, "A": 0, "B1": 0, "Q": 0, "R": 0, "fmm": 0}
        captured_z, captured_w, m_inputs, m_records, a_records = [], [], [], [], []
        final_snapshot = {}

        def reserve(phase, remaining_a, remaining_m):
            elapsed = budget.receipt()["elapsed_s"]; required = 330 * remaining_a + 75 * remaining_m + 30
            record = {"phase": phase, "elapsed_s": elapsed, "remaining_internal_s": INTERNAL_SECONDS - elapsed,
                      "required_reserve_s": required, "remaining_full_actions": remaining_a,
                      "remaining_m_actions": remaining_m,
                      "pass": bool(INTERNAL_SECONDS - elapsed >= required)}
            path = output / f"reserve-{len(stop_receipts) + 1}.json"; save(path, record)
            stop_receipts.append(receipt(path))
            if not record["pass"]: raise StopBeforeAction(phase)

        def m_apply(residual):
            counts["M"] += 1; assert counts["M"] <= 2
            m_inputs.append(residual.copy())
            reserve(f"before M {counts['M']}", 3 - counts["A"], 3 - counts["M"])
            sub = output / f"m-{counts['M']}"; sub.mkdir()
            base, metrics, resources = two._one_r_direction(residual[:TOTAL], sv, si, sc, sub, budget)
            counts["B1"] += metrics["counters"]["b1"]
            counts["Q"] += metrics["counters"]["q"]; counts["R"] += metrics["counters"]["r"]
            inverse_gate = all(value <= 2e-8 for value in metrics["inverse_relative_max"].values())
            identity_gate = all(metrics["identities"][name] <= metrics["identities"]["tolerance"] for name in ("k", "q", "r"))
            del resources; gc.collect(); rss_after_base = joint.ntd.recon._rss_bytes()
            with np.load(PINS["aux_matrix"][0], allow_pickle=False) as archive:
                scaled = sparse.csc_matrix((archive["scaled_homega_data"], archive["scaled_homega_indices"], archive["scaled_homega_indptr"]), shape=tuple(archive["scaled_homega_shape"]))
                assert np.array_equal(archive["h_scale"], sh)
            factor = splu(scaled, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
                          options={"SymmetricMode": True})
            dpsi_scaled = aux.scaled_solve(factor, residual[TOTAL:]); dpsi = sh * dpsi_scaled
            closed_rhs_norm = max(float(np.linalg.norm(residual[TOTAL:])), np.finfo(float).tiny)
            homega_relative = float(np.linalg.norm(-scaled @ dpsi_scaled-residual[TOTAL:]) /
                                     closed_rhs_norm)
            answer = np.r_[base, dpsi_scaled]
            record = {"index": counts["M"], "metrics": metrics, "inverse_gate": bool(inverse_gate),
                      "identity_gate": bool(identity_gate), "homega_rhs_relative": homega_relative,
                      "finite": bool(np.isfinite(answer).all()), "rss_after_base_release": rss_after_base,
                      "counts": dict(counts)}
            artifact = sub / "direction.npz"
            np.savez_compressed(artifact, input_residual=residual, direction=answer,
                                base_direction_scaled=base, psi_direction_scaled=dpsi_scaled, psi_direction_physical=dpsi)
            record["artifact"] = receipt(artifact); save(sub / "receipt.json", record)
            del factor, scaled; gc.collect(); record["rss_after_homega_release"] = joint.ntd.recon._rss_bytes()
            m_records.append(record)
            assert inverse_gate and identity_gate and homega_relative <= 2e-8 and record["finite"]
            assert counts["B1"] <= 14 and counts["Q"] <= 12 and counts["R"] <= 2
            assert record["rss_after_homega_release"] <= 8 * 2**30
            budget.check(f"completed M {counts['M']}"); return answer

        def a_apply(value, *, final=False):
            counts["A"] += 1; assert counts["A"] <= 3 and value.shape == warm.shape
            sub = output / f"a-{counts['A']}"; sub.mkdir()
            inherited, local_paths = joint.preflight(); action = joint.ntd.load_action(inherited["qualified_stream_result"])
            assert np.array_equal(action.h_scale, sh)
            base, psi_scaled = value[:TOTAL], value[TOTAL:]; physical = scales * base
            psi_physical = sh * psi_scaled; dg = physical[two.legacy.NX:]
            dq25 = physical[two.legacy.NV:two.legacy.NX]
            field = action.apply(dg, return_field=True); cpsi = action.c_apply(psi_physical)
            q04 = field.branch_current_a + cpsi; rq_cpsi = action.resistance @ cpsi
            rq_q04 = action.resistance @ q04
            pt_rc, projected_rc, pt_rc_gradient = joint.cross.lift_probe.contact_lift_transpose(action, rq_cpsi)
            rc_norm = max(np.linalg.norm(rq_cpsi), np.finfo(float).tiny)
            field_gates = {"field_finite": bool(np.isfinite(q04).all() and np.isfinite(field.dual_potential_v).all()),
                "pg_kcl": bool(two.relative(action.b_apply(field.branch_current_a), field.target_bq_a) <= 1e-7),
                "combined_kcl": bool(two.relative(action.b_apply(q04), field.target_bq_a) <= 1e-7),
                "field_stationarity": bool(field.metrics["energy_scaled_stationarity_relative"] <= 1e-7),
                "field_dual": bool(field.metrics["dual_rq_relative"] <= 1e-7),
                "pt_rcpsi": bool(np.linalg.norm(pt_rc) / rc_norm <= 2e-8),
                "projected_rcpsi": bool(np.linalg.norm(projected_rc) / rc_norm <= 2e-8),
                "pt_rc_gradient_finite": bool(np.isfinite(pt_rc_gradient))}
            save(sub / "field-before-fmm.json", {"metrics": field.metrics, "gates": field_gates})
            assert all(field_gates.values()), field_gates
            with np.load(PINS["frequency_operator"][0], allow_pickle=False) as archive:
                y = two.legacy.source.base.csc(archive, "y")[1:, 1:]; b = two.legacy.source.base.csc(archive, "b")[1:, :]
                resistance = two.legacy.source.base.csc(archive, "r")
            with np.load(PINS["frequency_bridge"][0], allow_pickle=False) as archive:
                u = two.legacy.source.base.csc(archive, "l04_u")[1:, :]
                delta = two.legacy.source.base.csc(archive, "l04_delta")[1:, 1:]
                diagonal = np.asarray(archive["l04_contact_diagonal_admittance_s"])
            def r_only(state):
                answer = np.r_[y @ state[:two.legacy.NV] + b @ state[two.legacy.NV:],
                               b.T @ state[:two.legacy.NV] - resistance @ state[two.legacy.NV:]]
                answer[:two.legacy.NV] += delta @ state[:two.legacy.NV]; return answer
            cached = field.dual_potential_v[action.free_cell_count:][action.independent_contacts].copy()
            def ntd(g):
                assert g.shape == dg.shape and two.relative(g, dg) <= 2e-8; return cached
            electrical = scales * two.legacy.coupled.interface_apply(r_only, u, diagonal, ntd,
                physical[:two.legacy.NX], dg, two.legacy.NV)
            source, target, _, _, _ = joint.cross._load_cross_geometry()
            source[-1][2] = joint.cross.L25_Z_M; target[-1][2] = joint.cross.L04_Z_M
            matrices = [joint.read_csc(path, prefix) for path, _, prefix in local_paths]
            runtime = joint.magnetic.verify_environment()
            save(sub / "runtime-before-fmm.json", {"runtime": runtime, "field": receipt(sub / "field-before-fmm.json")})
            reserve(f"before A {counts['A']}", 4 - counts["A"], 2 - counts["M"])
            (f25, f04), calls = joint.joint_action(dq25, q04, (source, target), matrices, budget)
            counts["fmm"] += len(calls)
            raw = sub / "raw-action-before-projection.npz"
            np.savez_compressed(raw, input_scaled=value, q04=q04, l04_dual=field.dual_potential_v,
                                l04_target=field.target_bq_a, f25=f25, f04=f04,
                                calls_json=np.asarray(json.dumps(two.builtin(calls), allow_nan=False)))
            save(sub / "raw-action-before-projection.json", {"artifact": receipt(raw), "calls": calls})
            budget.check(f"raw A {counts['A']} before projection")
            projection = {}
            def p_transpose(force):
                answer, _, gradient = joint.cross.lift_probe.contact_lift_transpose(action, force)
                projection["gradient"] = gradient; return answer
            answer = third.assemble_ad3(electrical, f25, f04, sh, p_transpose, action.ct_apply,
                pt_rc, action.ct_apply(rq_q04), omega=two.OMEGA, nv=two.legacy.NV,
                nx=two.legacy.NX, scales=scales)
            action_gates = {"four_fmm_calls": bool(len(calls) == 4),
                "fmm_ier_zero": bool(all(call["ier"] == 0 for call in calls)),
                "pt_gradient": bool(projection["gradient"] <= 1e-7),
                "finite_action": bool(np.isfinite(answer).all())}
            record = {"index": counts["A"], "final_replay": bool(final), "field_gates": field_gates,
                      "action_gates": action_gates, "calls": calls, "raw": receipt(raw)}
            save(sub / "receipt.json", record); assert all(action_gates.values()), action_gates
            if final:
                final_snapshot.update(q04=q04.copy(), dual=field.dual_potential_v.copy(), target=field.target_bq_a.copy(),
                    f25=f25.copy(), f04=f04.copy(), physical_base=physical.copy(), physical_psi=psi_physical.copy())
            else:
                captured_z.append(value.copy()); captured_w.append(answer.copy())
            a_records.append(record)
            del source, target, matrices, action, y, b, resistance, u, delta
            gc.collect(); budget.check(f"completed A {counts['A']}"); return answer

        operator = LinearOperator((len(rhs), len(rhs)), matvec=a_apply, dtype=np.complex128)
        preconditioner = LinearOperator((len(rhs), len(rhs)), matvec=m_apply, dtype=np.complex128)
        correction, raw_info = gcrotmk(operator, warm_residual, x0=None, M=preconditioner,
            m=2, k=0, CU=None, discard_C=True, maxiter=1, rtol=0.0, atol=ABSOLUTE_TOLERANCE)
        arnoldi_inputs = np.column_stack(m_inputs)
        arnoldi_norms = np.linalg.norm(arnoldi_inputs, axis=0)
        assert np.all(np.isfinite(arnoldi_norms)) and np.all(arnoldi_norms > 0)
        arnoldi_normalized = arnoldi_inputs / arnoldi_norms
        arnoldi_gram_error = float(np.linalg.norm(
            arnoldi_normalized.conj().T @ arnoldi_normalized - np.eye(len(m_inputs))))
        arnoldi_receipt = output / "gcrot-m-inputs-before-final.npz"
        np.savez_compressed(arnoldi_receipt, inputs=arnoldi_inputs, norms=arnoldi_norms,
                            gram=arnoldi_normalized.conj().T @ arnoldi_normalized)
        save(output / "gcrot-m-inputs-before-final.json", {"artifact": receipt(arnoldi_receipt),
            "input_count": len(m_inputs), "normalized_gram_error": arnoldi_gram_error,
            "gate": bool(len(m_inputs) <= 2 and arnoldi_gram_error <= 2e-8)})
        assert len(m_inputs) == counts["M"] and arnoldi_gram_error <= 2e-8
        z, w, coefficients, a_delta, capture = reconstruct_action(captured_z, captured_w, correction)
        capture_path = output / "gcrot-captures-before-final.npz"
        np.savez_compressed(capture_path, Z=z, W=w, correction=correction, coefficients=coefficients, A_delta=a_delta)
        save(output / "gcrot-captures-before-final.json", {"artifact": receipt(capture_path), "metrics": capture})
        assert capture["gate"]
        candidate = warm + correction; final_action = a_apply(candidate, final=True)
        final_residual = rhs - final_action; algebraic_residual = warm_residual - a_delta
        replay_denominator = max(np.linalg.norm(final_residual), np.linalg.norm(algebraic_residual), np.finfo(float).tiny)
        replay_absolute = float(np.linalg.norm(final_residual - algebraic_residual))
        replay_relative = replay_absolute / replay_denominator
        rhs_norm = float(np.linalg.norm(rhs)); residual_norm = float(np.linalg.norm(final_residual))
        with np.load(PINS["frequency_operator"][0], allow_pickle=False) as archive:
            positive, negative = (int(archive[name][0]) - 1 for name in ("positive_active_index", "negative_active_index"))
        artifact = output / "complete-current-final.npz"
        np.savez_compressed(artifact, warm_scaled=warm, correction_scaled=correction,
            candidate_scaled=candidate, candidate_base_physical=final_snapshot["physical_base"],
            candidate_psi_physical=final_snapshot["physical_psi"], l04_branch_current_a=final_snapshot["q04"],
            l04_dual_potential_v=final_snapshot["dual"], l04_target_bq_a=final_snapshot["target"],
            active_voltage_v=final_snapshot["physical_base"][:two.legacy.NV],
            l25_branch_current_a=final_snapshot["physical_base"][two.legacy.NV:two.legacy.NX],
            l04_independent_contact_current_into_sheet_a=final_snapshot["physical_base"][two.legacy.NX:],
            l25_magnetic_flux_linkage_wb=final_snapshot["f25"], l04_magnetic_flux_linkage_wb=final_snapshot["f04"],
            final_true_action=final_action, final_true_residual=final_residual,
            algebraic_residual=algebraic_residual, original_rhs_scaled=rhs,
            frequency_hz=np.asarray([10_000_000.0]), source_current_a=np.asarray([1.0]),
            source_current_amplitude_a=np.asarray([1.0]),
            source_positive_negative_gauge_active_indices=np.asarray([positive, negative]),
            raw_gcrotmk_info=np.asarray([raw_info]),
            algebraically_converged=np.asarray([raw_info == 0 and residual_norm <= ABSOLUTE_TOLERANCE]))
        save(output / "complete-current-final-replay-gate.json", {"artifact": receipt(artifact),
            "explicit_vs_algebraic": {"absolute": replay_absolute,
                "denominator": float(replay_denominator), "relative": replay_relative}})
        assert replay_relative <= 2e-8
        metrics = two.blocks(warm_residual, final_residual, two.legacy.NV, two.legacy.NX, TOTAL)
        metrics["original_rhs_norm"] = rhs_norm
        metrics["warm_relative_to_original_rhs"] = float(np.linalg.norm(warm_residual) / rhs_norm)
        metrics["final_relative_to_original_rhs"] = residual_norm / rhs_norm
        physical_voltage = scales[:two.legacy.NV] * candidate[:two.legacy.NV]
        raw_z = two.active_voltage(physical_voltage, positive, two.legacy.NV) - two.active_voltage(physical_voltage, negative, two.legacy.NV)
        stationary = np.dot(rhs, candidate) + np.dot(candidate, final_residual)
        stationary_gap = float(abs(stationary - raw_z))
        progress = {"full_residual_lte_cap": bool(residual_norm <= FULL_PROGRESS_CAP),
                    "stationary_gap_lte_cap": bool(stationary_gap <= GAP_PROGRESS_CAP_OHM)}
        numerical_acceptance = bool(raw_info == 0 and residual_norm <= ABSOLUTE_TOLERANCE)
        result = {"program": PROGRAM, "version": VERSION,
            "status": "UNVALIDATED_COMPLETE_CURRENT_GCROTMK", "run_released": RUN_RELEASED,
            "inputs": provenance["inputs"], "raw_gcrotmk_info": int(raw_info),
            "original_numerical_gate": numerical_acceptance, "artifact": receipt(artifact),
            "captures": receipt(output / "gcrot-captures-before-final.json"),
            "arnoldi_inputs": receipt(output / "gcrot-m-inputs-before-final.json"),
            "pre_replay_checkpoint": receipt(output / "complete-current-final-replay-gate.json"),
            "action_records": a_records, "m_records": m_records, "counts": counts,
            "metrics": metrics, "two_step_residual_factor": residual_norm / WARM_NORM,
            "raw_z_unvalidated": two.builtin(raw_z), "stationary_j_unvalidated": two.builtin(stationary),
            "stationary_raw_gap_ohm": stationary_gap, "progress_gates": progress,
            "positive_progress_screen": bool(all(progress.values())),
            "explicit_vs_algebraic_replay": {"absolute": replay_absolute,
                "denominator": float(replay_denominator), "relative": replay_relative},
            "warm_psi_scale_replay_relative": psi_replay,
            "closed_auxiliary": {"operator": "H+jw*C.T*L04self*C",
                "scaled_rhs_rule": "dpsi_scaled=-factor.solve(r_closed_scaled); no extra D on the RHS",
                "fresh_factor_per_m_application": True, "true_operator_unchanged": True},
            "actual_magnetic_physical_failures": provenance["actual_magnetic_physical_failures"],
            "no_automatic_extension": True,
            "scope": "Two-step public flexible GCROT discriminator on the conditional complete-current model; no convergence, physical, accuracy, PSD, error-bound, or PowerSI acceptance."}
        save(output / "result.json", result)
        save(output / "final-worker-budget.json", {"status": "FINAL_WORKER_BUDGET_AFTER_RESULT",
            "result": receipt(output / "result.json"), "artifact": receipt(artifact), "budget": budget.receipt()})
        budget.check("final serialization")
    except StopBeforeAction as error:
        save(output / "result.json", {"program": PROGRAM, "version": VERSION,
            "status": "STOP_BEFORE_COMPLETE_ACTION_INSUFFICIENT_REMAINING_BUDGET",
            "run_released": RUN_RELEASED, "reason": str(error), "stop_receipts": stop_receipts,
            "positive_progress_screen": False, "no_automatic_extension": True,
            "budget": budget.receipt()})
    except BaseException:
        save(output / "failure.json", {"program": PROGRAM, "version": VERSION,
            "status": "UNVALIDATED", "failure": traceback.format_exc(), "budget": budget.receipt()})
        raise
    finally:
        gc.collect()


def launch(output):
    assert RUN_RELEASED and not output.exists()
    assert two.sha(Path(two.legacy.counter.__file__)) == joint.PINS["counter"][1]
    availability = two.legacy.source.available_34gib(); output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(two.legacy.counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    started = time.monotonic(); private = working = 0; reason = guard_failure = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        try:
            print(f"complete-current GCROT: owned PID={child.pid}, {EXTERNAL_SECONDS}s/32GiB", flush=True)
            while child.poll() is None:
                values = two.legacy.counter._MemoryCounters(); values.cb = ctypes.sizeof(values)
                if getter(int(child._handle), ctypes.byref(values), values.cb):
                    private = max(private, int(values.private_usage)); working = max(working, int(values.working_set))
                elif child.poll() is None: reason = "STOP_PROCESS_MEMORY_QUERY"
                if time.monotonic() - started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private, working) > int(MEMORY_GIB * 2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason: child.kill(); break
                time.sleep(0.5)
            code = child.wait(timeout=10)
        except BaseException:
            guard_failure = traceback.format_exc(); reason = "STOP_PARENT_GUARD_EXCEPTION"
            if child.poll() is None: child.kill()
            code = child.wait(timeout=10)
    save(output / "external-budget.json", {"program": PROGRAM, "version": VERSION,
        "status": reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT"),
        "owned_pid": child.pid, "exit_code": code, "elapsed_s": time.monotonic() - started,
        "sampled_peak_private_bytes": private, "sampled_peak_working_set_bytes": working,
        "max_runtime_s": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30),
        "driver_sha256": two.sha(frozen), "worker_command": command,
        "memory_at_launch": availability, "guard_failure": guard_failure})
    raise SystemExit(0 if reason is None and code == 0 else 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__); modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true"); modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true"); modes.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight: print(json.dumps(two.builtin(preflight()), indent=2, allow_nan=False))
    elif args.run: assert RUN_RELEASED and args.output is not None; launch(args.output.resolve())
    else: assert RUN_RELEASED and args.output is not None; worker(args.output.resolve())


if __name__ == "__main__": main()
