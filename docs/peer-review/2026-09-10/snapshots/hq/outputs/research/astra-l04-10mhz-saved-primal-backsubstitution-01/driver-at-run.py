"""SPD Decap PI Evaluator v0.23.1: disabled saved-step B1 back-substitution probe."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
import traceback

import numpy as np

import probe_astra_l04_10mhz_joint_cached_one_step as source

base, recon, R = source.base, source.recon, source.R
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
TRACE = R / "astra-l04-10mhz-saved-primal-trace-01"
TRACE_SOURCE = Path(__file__).with_name("diagnose_astra_l04_10mhz_saved_primal_trace.py")
PINS = {
    "trace_source": (TRACE_SOURCE, "6c2877ecc930861001c844960e1d2888c29778a259b69d0a664570d490ea5df0"),
    "trace_result": (TRACE / "result.json", "e14e7a7690d0326979ce3da4ce2c74e90a237261ec5b61a458bd5745fbbcb663"),
    "trace_external": (TRACE / "external-budget.json", "24ea860054ab9a99e411cb9da1078344020f33642a0388b65475cc8ce4f038a1"),
    "trace_array": (TRACE / "unvalidated-recovered-auxiliary-trace.npz", "776d3a323cf0a87b410359de7a31e391a9e7478f09b618077f90545e1032c692"),
    "one_step_source": (Path(source.__file__), "a9ab2e92e90cf04a7acb07fff2c4f4b56f45decd1f69bcf919fc86a3c1c29a4b"),
    "one_step_action": (R / "astra-l04-10mhz-joint-cached-one-step-01/unvalidated-cached-joint-one-step-action.npz", "ed0b6dc8a2ff2ab82be8a853802a349369d5fe54ca98b5ca6d5a0154de1b4c7c"),
}


def self_check() -> None:
    p = np.array([[3., 1., 2.], [1., 4., -1.], [5., -2., 6.]])
    s = np.array([2., .5, 4.]); r_s = np.array([1., 2., 3.]); r_phys = r_s / s
    assert np.allclose(s * r_phys, r_s)
    q = np.array([1., 2., 3.]); z = np.array([.1, -.2, .3]); k = np.array([0, 1]); o = np.array([2])
    dzk = np.array([.02, -.01]); dz = np.zeros_like(z); dz[k] = dzk; zp = z + dz
    e, ep = q - p @ z, q - p @ zp
    outside_state = e[k] - (q[k] - p[np.ix_(k, k)] @ z[k])
    assert np.linalg.norm(outside_state) > 0 and np.allclose(outside_state, -p[np.ix_(k, o)] @ z[o])
    assert np.allclose(e[k] - p[np.ix_(k, k)] @ dzk, ep[k])
    assert np.allclose(ep[o] - e[o], -(p @ dz)[o])
    u = np.array([[2., -1.], [.5, 3.]]); d = np.array([.4, .7]); dx = np.array([.02, -.01]); dl = np.array([.03, -.02]); g = np.array([.5, -.4])
    gn = g-u.T@dx-d*dl
    assert np.allclose(gn-g, -u.T@dx-d*dl)
    sr = np.array([1., 2.]); anew = np.array([1.1, 1.8]); alpha = np.vdot(anew, sr)/np.vdot(anew, anew)
    assert np.isfinite(alpha) and np.linalg.norm(sr-alpha*anew) <= np.linalg.norm(sr)
    print(f"{PROGRAM} v{VERSION}: PASS_SAVED_PRIMAL_B1_BACKSUBSTITUTION_SELF_CHECK")


def receipt(path: Path, digest: str | None = None) -> dict:
    found = base.sha(path)
    assert digest is None or found == digest, str(path)
    return {"path": str(path.resolve()), "sha256": found, "size_bytes": path.stat().st_size}


def verify() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    inherited = source.verify_inputs()
    result = json.loads(PINS["trace_result"][0].read_text(encoding="utf-8"))
    external = json.loads(PINS["trace_external"][0].read_text(encoding="utf-8"))
    assert result["status"] == "DIAGNOSTIC_UNVALIDATED_SAVED_PRIMAL_TRACE"
    assert result["driver"]["sha256"] == PINS["trace_source"][1]
    assert result["artifact"]["sha256"] == PINS["trace_array"][1]
    assert result["inputs"]["source"]["sha256"] == PINS["one_step_source"][1]
    assert result["inputs"]["action"]["sha256"] == PINS["one_step_action"][1]
    assert external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0
    assert result["raw_solver_info"] == 4 and result["original_numerical_gate"] is False
    assert set(result["source_failed_physical_gates"]) == source.FAILED_PHYSICAL_GATES
    return {"trace": inputs, "inherited": inherited, "source": result}


def worker(output: Path) -> None:
    budget = recon._Budget.create(120, 8)
    try:
        frozen = output / "driver-at-run.py"
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = verify()
        with np.load(source.PINS["static"][0], allow_pickle=False) as a:
            p = base.csc(a, "p"); sp = a["diagonal_scale"]
            joint = a["joint_native_l14_gauged_potential_indices"]
            contacts = a["l04_contact_gauged_trace_rows"]
        NJ, NT, NV, NX = source.JOINT_SIZE, source.TRACE_SIZE, source.NV, source.NX
        robin = p[NJ:, NJ:].tocsc()
        scaled_p = base.scaled_block(p, sp, sp)
        assert scaled_p.shape == (NJ + NT, NJ + NT)
        with np.load(source.PINS["cache_factors"][0], allow_pickle=False) as a:
            lower, upper, pr, pc = source._load_public_factor(a)
            assert np.array_equal(sp, a["diagonal_scale"])
            assert np.array_equal(joint, a["joint_native_l14_gauged_potential_indices"])
        calls = dict(normal_count=0, normal_seconds=0., transpose_count=0, transpose_seconds=0.)
        b1 = source._make_b1(scaled_p, lower, upper, pr, pc, calls)
        budget.check("cached B1 and static K-by-K primal block loaded")
        with np.load(source.PINS["operator"][0], allow_pickle=False) as a:
            y = base.csc(a, "y")[1:, 1:]; b = base.csc(a, "b")[1:, :]
            r = base.csc(a, "r"); native, l14, l25, l02 = base.partition(a["conditional_global_active_index"])
            positive, negative, gauge = (int(a[name][0]) for name in
                                         ("positive_active_index", "negative_active_index", "gauge_active_index"))
            assert np.array_equal(a["frequency_hz"], [source.FREQUENCY_HZ])
            assert np.array_equal(a["source_current_a"], [source.SOURCE_CURRENT_A])
            assert (positive, negative, gauge) == (2699, 2656, 0)
        with np.load(source.PINS["bridge"][0], allow_pickle=False) as a:
            u = base.csc(a, "l04_u")[1:, :]; delta = base.csc(a, "l04_delta")[1:, 1:]
            d = a["l04_contact_diagonal_admittance_s"]
        sv = 1 / np.sqrt(np.asarray(abs(y).sum(1)).ravel() + np.asarray(abs(b).sum(1)).ravel())
        si = 1 / np.sqrt(np.asarray(abs(b).sum(0)).ravel() + np.asarray(abs(r).sum(1)).ravel())
        S = np.r_[sv, si, np.sqrt(abs(d))]
        T = np.r_[sv, si, sp[NJ:]]; T[joint] = sp[:NJ]
        K = np.r_[joint, np.arange(NX, NX + NT)]
        O = np.setdiff1d(np.arange(NX + NT), K)
        assert np.array_equal(T[K], sp) and np.array_equal(joint, np.r_[native, l14])
        def new_a(value):
            answer = np.r_[y @ value[:NV] + b @ value[NV:], b.T @ value[:NV] - r @ value[NV:]]
            answer[:NV] += delta @ value[:NV]
            return answer
        def primal(value):
            return T * source.primal_full_action(new_a, u, robin, contacts, NV, T * value)
        budget.check("operator and scaling inputs loaded")
        with np.load(PINS["one_step_action"][0], allow_pickle=False) as a:
            sr = a["scaled_initial_residual"]
            step_s = a["scaled_cached_joint_preconditioned_step"]
            astep = a["scaled_true_action_of_step"]
        r_phys = sr / S; xg_saved = S * step_s; x_saved, g_saved = xg_saved[:NX], xg_saved[NX:]
        rx, rg = r_phys[:NX], r_phys[NX:]
        lambda_c = -(g_saved + u.T @ x_saved[:NV]) / d - rg
        with np.load(PINS["trace_array"][0], allow_pickle=False) as a:
            lambda_saved = a["reconstructed_auxiliary_trace_v"]
        assert lambda_saved.shape == (NT,)
        contact_replay = lambda_saved[contacts] - lambda_c
        contact_replay_denominator = max(np.linalg.norm(lambda_saved[contacts]), np.linalg.norm(lambda_c), np.finfo(float).tiny)
        contact_replay_relative = np.linalg.norm(contact_replay) / contact_replay_denominator
        rp_phys = np.zeros(NX + NT, complex)
        rp_phys[:NV] = rx[:NV] - u @ rg
        rp_phys[NV:NX] = rx[NV:NX]
        rp_phys[NX + contacts] = -d * rg
        q = T * rp_phys
        z = np.r_[x_saved, lambda_saved] / T
        e = q - primal(z)
        e_k = e[K]
        preexisting_outside_state_k_contribution = e_k - (q[K] - scaled_p @ z[K])
        dz = np.zeros_like(z); dz[K] = b1(e_k)
        budget.check("one cached B1 action")
        dz_k = dz[K]
        b1_residual = e_k - scaled_p @ dz_k
        zp = z + dz
        primal_dz = primal(dz)
        ep = q - primal(zp)
        forward_defect = ep - e + primal_dz
        outside_induced = -primal_dz[O]
        outside_defect = ep[O] - e[O] - outside_induced
        k_forward_defect = ep[K] - e_k + scaled_p @ dz_k
        budget.check("full primal repair and outside coupling measured")
        dx = T[:NX] * dz[:NX]
        dl = T[NX:] * dz[NX:]
        g_new = g_saved - u.T @ dx[:NV] - d * dl[contacts]
        repaired_scaled = np.r_[x_saved + dx, g_new] / S
        primal_roundoff = 32 * np.finfo(float).eps * max(
            1., np.linalg.norm(q), np.linalg.norm(e), np.linalg.norm(primal_dz))
        pre_ntd_finite_gates = {
            "contact_replay": bool(np.isfinite(contact_replay_relative)),
            "primal_vectors": bool(np.all(np.isfinite(q)) and np.all(np.isfinite(e)) and np.all(np.isfinite(ep))),
            "identities": bool(np.isfinite(np.linalg.norm(k_forward_defect)) and np.isfinite(np.linalg.norm(outside_defect)) and np.isfinite(np.linalg.norm(forward_defect))),
            "repair": bool(np.all(np.isfinite(dx)) and np.all(np.isfinite(dl)) and np.all(np.isfinite(g_new)) and np.all(np.isfinite(repaired_scaled))),
        }
        primal_metrics = {
            "contact_replay_numerator_v": float(np.linalg.norm(contact_replay)),
            "contact_replay_denominator_v": float(contact_replay_denominator),
            "contact_replay_relative": float(contact_replay_relative),
            "b1_residual_relative": float(np.linalg.norm(b1_residual) / max(np.linalg.norm(e_k), np.finfo(float).tiny)),
            "full_repaired_primal_residual_relative": float(np.linalg.norm(ep) / max(np.linalg.norm(q), np.finfo(float).tiny)),
            "outside_induced_residual_norm": float(np.linalg.norm(outside_induced)),
            "preexisting_outside_state_k_contribution": float(np.linalg.norm(preexisting_outside_state_k_contribution)),
            "k_forward_identity_defect": float(np.linalg.norm(k_forward_defect)),
            "outside_forward_identity_defect": float(np.linalg.norm(outside_defect)),
            "full_forward_identity_defect": float(np.linalg.norm(forward_defect)),
            "forward_roundoff_envelope": float(primal_roundoff),
            "b1_calls": calls.copy(),
            "structural": {"k_size": int(len(K)), "outside_size": int(len(O)), "static_p_rows": int(scaled_p.shape[0])},
            "repair": {"dx_norm": float(np.linalg.norm(dx)), "dl_norm": float(np.linalg.norm(dl)),
                       "incremental_g_identity_defect": float(np.linalg.norm(g_new - g_saved + u.T @ dx[:NV] + d * dl[contacts]))},
            "finite_gates": pre_ntd_finite_gates,
        }
        base.atomic_json(output / "primal-metrics-before-ntd.json", primal_metrics)
        budget.check("primal metrics saved before cached B1 release")
        assert calls["normal_count"] == 1 and calls["transpose_count"] == 0
        assert contact_replay_relative <= 2e-8
        assert all(pre_ntd_finite_gates.values())
        assert primal_metrics["full_forward_identity_defect"] <= primal_roundoff
        assert primal_metrics["outside_forward_identity_defect"] <= primal_roundoff
        assert primal_metrics["k_forward_identity_defect"] <= primal_roundoff
        del b1, lower, upper, pr, pc, scaled_p, p, robin, primal
        gc.collect(); budget.check("cached B1 and primal block released before exact ContactNtD")
        _, stream, _ = source.ntd.verify_contract()
        loaded_ntd = source.ntd.load_action(stream)
        budget.check("exact ContactNtD loaded before its one true action")
        ntd_calls = 0
        def counted_ntd(value):
            nonlocal ntd_calls
            ntd_calls += 1
            return loaded_ntd.apply(value)
        def full_action(value):
            physical = S * value
            return S * source.coupled.interface_apply(new_a, u, d, counted_ntd,
                                                      physical[:NX], physical[NX:], NV)
        anew = full_action(repaired_scaled)
        budget.check("one exact ContactNtD action")
        unit_residual = sr - anew
        denominator = np.vdot(anew, anew)
        assert np.isfinite(denominator) and denominator.real > 0
        alpha = np.vdot(anew, sr) / denominator
        optimized_residual = sr - alpha * anew
        rhs = np.zeros_like(sr)
        rhs[positive - 1], rhs[negative - 1] = sv[positive - 1], -sv[negative - 1]
        rhs_norm = np.linalg.norm(rhs)
        saved_residual_norm = np.linalg.norm(sr)
        initial_blocks = source.coupled.residual_metrics(sr, sv, (native, l14, l25, l02), rhs_norm)
        unit_blocks = source.coupled.residual_metrics(unit_residual, sv, (native, l14, l25, l02), rhs_norm)
        optimized_blocks = source.coupled.residual_metrics(optimized_residual, sv, (native, l14, l25, l02), rhs_norm)
        tiny = np.finfo(float).tiny
        finite_gates = {
            "pre_ntd": bool(all(pre_ntd_finite_gates.values())),
            "outer_action": bool(np.all(np.isfinite(anew))),
            "alpha": bool(np.isfinite(alpha)),
            "residuals": bool(np.all(np.isfinite(unit_residual)) and np.all(np.isfinite(optimized_residual))),
        }
        metrics = {
            "status": "DIAGNOSTIC_UNVALIDATED",
            **primal_metrics,
            "ntd_apply_count": ntd_calls,
            "action_delta_norm": float(np.linalg.norm(anew - astep)),
            "initial_global_rhs_relative": float(saved_residual_norm / max(rhs_norm, tiny)),
            "unit_step_global_rhs_relative": float(np.linalg.norm(unit_residual) / max(rhs_norm, tiny)),
            "optimized_step_global_rhs_relative": float(np.linalg.norm(optimized_residual) / max(rhs_norm, tiny)),
            "unit_step_ratio_to_saved_residual": float(np.linalg.norm(unit_residual) / max(saved_residual_norm, tiny)),
            "optimized_step_ratio_to_saved_residual": float(np.linalg.norm(optimized_residual) / max(saved_residual_norm, tiny)),
            "optimized_complex_alpha": source._pair(alpha),
            "residual_blocks": {"initial": initial_blocks, "unit_step": unit_blocks, "optimized_step": optimized_blocks},
            "finite_gates": finite_gates,
            "raw_solver_info": 4,
            "original_numerical_gate": False,
            "source_failed_physical_gates": sorted(source.FAILED_PHYSICAL_GATES),
        }
        base.atomic_json(output / "metrics-before-acceptance.json", metrics)
        budget.check("metrics saved before acceptance checks")
        assert calls["normal_count"] == 1 and calls["transpose_count"] == 0 and ntd_calls == 1
        assert all(finite_gates.values())
        artifact = output / "unvalidated-one-b1-one-ntd-repaired-step.npz"
        base.atomic_npz(artifact,
                        repaired_scaled_step=repaired_scaled, repaired_true_action=anew,
                        scaled_input_residual=sr, saved_true_action=astep,
                        full_dz=dz, dz_k=dz_k,
                        trace_array_sha256_utf8=np.asarray([PINS["trace_array"][1]], dtype="U64"),
                        trace_source_sha256_utf8=np.asarray([PINS["trace_source"][1]], dtype="U64"),
                        one_step_source_sha256_utf8=np.asarray([PINS["one_step_source"][1]], dtype="U64"),
                        one_step_action_sha256_utf8=np.asarray([PINS["one_step_action"][1]], dtype="U64"),
                        frequency_hz=np.asarray([source.FREQUENCY_HZ]), source_current_a=np.asarray([source.SOURCE_CURRENT_A]),
                        diagnostic_status_utf8=np.asarray(["DIAGNOSTIC_UNVALIDATED"], dtype="U64"))
        artifact_receipt = receipt(artifact)
        budget.check("explicitly unvalidated repaired-step artifact saved")
        del counted_ntd, new_a, full_action, loaded_ntd
        del y, b, r, u, delta, q, z, zp, dz, primal_dz, e, ep, rp_phys
        del sr, step_s, astep, anew, unit_residual, optimized_residual, rhs
        gc.collect(); budget.check("factors and operator owners released")
        recommendation = ("NO_CONTINUATION_RECOMMENDED: poor optimized residual gain."
                          if metrics["optimized_step_ratio_to_saved_residual"] >= 1
                          else "NO_CONTINUATION_RECOMMENDED: this remains an unvalidated diagnostic.")
        base.atomic_json(output / "result.json", {
            "program": PROGRAM, "version": VERSION, "status": "DIAGNOSTIC_UNVALIDATED",
            "driver": receipt(frozen), "inputs": inputs, "artifact": artifact_receipt, "metrics": metrics,
            "source_failed_physical_gates": sorted(source.FAILED_PHYSICAL_GATES),
            "recommendation": recommendation, "budget": budget.receipt(),
            "scope": "One cached fixed B1 correction and one exact ContactNtD action on a saved step. No factorization, Krylov, field, convergence, H-accuracy, causal, broadband, or PowerSI claim.",
        })
    except BaseException:
        base.atomic_json(output / "failure.json", {"status": "DIAGNOSTIC_UNVALIDATED", "failure": "SAVED_PRIMAL_B1_BACKSUBSTITUTION", "traceback": traceback.format_exc(), "budget": budget.receipt()}); raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); modes = parser.add_mutually_exclusive_group(required=True); modes.add_argument("--self-check", action="store_true"); modes.add_argument("--preflight", action="store_true"); modes.add_argument("--run", action="store_true"); modes.add_argument("--native-worker", action="store_true"); parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check(); return
    if args.preflight: verify(); print("PASS_SAVED_PRIMAL_B1_BACKSUBSTITUTION_INPUTS"); return
    assert RUN_RELEASED and args.output is not None
    output = args.output.resolve()
    if args.native_worker: worker(output); return
    import probe_astra_fmm3d_runtime as guard
    assert base.sha(Path(guard.__file__)) == source.PINS["guard"][1]
    output.mkdir(parents=True, exist_ok=False); (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guard.guarded_source_worker(output, worker_command=[sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)], max_runtime_s=150.0))


if __name__ == "__main__": main()
