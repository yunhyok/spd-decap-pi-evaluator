"""SPD Decap PI Evaluator v0.23.1: held one-direction closed-current screen."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import gc
import hashlib
import json
from math import pi
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime"), str(ROOT / "outputs/research-fmm-runtime")]

# Held before importing any canonical execution helper; all run paths reassert it.
RUN_RELEASED = True
import continue_astra_l04_10mhz_l25_magnetic_gcrotmk as legacy  # noqa: E402
import probe_astra_l25_l04_joint_magnetic_action as joint  # noqa: E402

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 420.0, 450.0, 32.0
FREQUENCY_HZ, OMEGA = 10_000_000.0, 2.0 * pi * 10_000_000.0
RESEARCH = ROOT / "outputs/research"
BASELINE = RESEARCH / "astra-l04-10mhz-l25-magnetic-gcrotmk-01"
RECOVERY = RESEARCH / "astra-l25-l04-joint-magnetic-action-recovery-01"

PINS = {
    "baseline_field": (BASELINE / "unvalidated-flexible-gcrotmk-field.npz", "c1f833406f3f6baab9e6010202a323ed1db5f6de43f1f1e4a8dfe8e7382e5d8d"),
    "baseline_result": (BASELINE / "result.json", "90a640099e91d1088dee4f6cd146b64865d5b74d06f886a8f555a48e48e144e3"),
    "baseline_driver": (BASELINE / "driver-at-run.py", "fb737651456a57c1fcba96073ef1b8b2e48682b4dfb930a1abf10603bf51bcce"),
    "joint_recovery_result": (RECOVERY / "result.json", "77a73aca48f6d3a85d7f5d455539845a1be96756020aa12c0fbf4b6c91bb044c"),
    "joint_recovery_driver": (RECOVERY / "driver-at-run.py", "8167115ec65c32ee7697bd6f00eac3c559e2f12c2620ca7cd4e9f61ac52fd551"),
    "joint_recovery_guard": (RECOVERY / "external-budget.json", "492349c28348aa801a418269209059a8bf9d292f8814bf07531156eeea932915"),
    "joint_recovered_final_arrays": (RESEARCH / "astra-l25-l04-joint-magnetic-action-01" / "joint-action-and-reductions.npz", "f3ac30054f410a77648662fd3da96490b09646646d5e2d8ca21904bad515de82"),
    "joint_action_source": (Path(joint.__file__), "caf5ba8fd0a1a156f987069be4d14e12d5ae8268e613716d120fb6edf2ab6a5e"),
    "magnetic_source": (Path(legacy.__file__), "fb737651456a57c1fcba96073ef1b8b2e48682b4dfb930a1abf10603bf51bcce"),
    "ntd_source": (Path(joint.ntd.__file__), "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
    "lift_source": (Path(joint.cross.lift_probe.__file__), "85ac6bdcb2dca9c2ecfccb2709b548fef9f61de49af2feb1e8323aeb80e4722a"),
    "frequency_operator": legacy.source.PINS["operator"],
    "frequency_bridge": legacy.source.PINS["bridge"],
}


def sha(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict:
    digest = sha(path)
    assert expected is None or digest == expected, str(path)
    return {"path": str(path.resolve()), "sha256": digest, "size_bytes": int(path.stat().st_size)}


def builtin(value):
    """Only builtin JSON values reach receipts; recovered NumPy bools caused a prior failure."""
    if isinstance(value, np.generic): return builtin(value.item())
    if isinstance(value, np.ndarray): return [builtin(item) for item in value.tolist()]
    if isinstance(value, complex):
        assert np.isfinite(value)
        return [float(value.real), float(value.imag)]
    if isinstance(value, Path): return str(value)
    if isinstance(value, dict): return {str(key): builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [builtin(item) for item in value]
    return value


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(builtin(value), indent=2, allow_nan=False) + "\n", encoding="utf-8")


def pair(value: complex) -> list[float]:
    value = complex(value); assert np.isfinite(value)
    return [float(value.real), float(value.imag)]


def relative(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.linalg.norm(left - right) / max(np.linalg.norm(left), np.linalg.norm(right), np.finfo(float).tiny))


def symmetric_scales(y, b, resistance, bridge_diagonal):
    """Canonical magnetic GCROT scales: direct Y/B/R/D formulas only."""
    sv = 1.0 / np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel() + np.asarray(abs(b).sum(axis=1)).ravel())
    si = 1.0 / np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel() + np.asarray(abs(resistance).sum(axis=1)).ravel())
    sc = np.sqrt(abs(np.asarray(bridge_diagonal)))  # Do not cast possibly-complex D to float.
    assert np.all(np.isfinite(sv)) and np.all(np.isfinite(si)) and np.all(np.isfinite(sc))
    return sv, si, sc


def scaled_direction_action(dpsi, dq04, f25d, f04d, h_dpsi, p_transpose, c_transpose, *, omega, nv, nx, total, scales):
    """Exact physical psi-direction rows, then the fixed saved row scaling."""
    ptd, ctd = p_transpose(f04d), c_transpose(f04d)
    physical = np.zeros(total + len(dpsi), dtype=np.complex128)
    physical[nv:nx] = -1j * omega * f25d
    physical[nx:total] = -1j * omega * ptd
    physical[total:] = -h_dpsi - 1j * omega * ctd
    return scales * physical, physical, ptd, ctd


def screen_metrics(r0, r1, nv: int, nx: int, total_rows: int) -> dict:
    tiny = np.finfo(float).tiny
    blocks = {"potential": slice(0, nv), "l25": slice(nv, nx), "contact": slice(nx, total_rows),
              "original_nonclosed": slice(0, total_rows), "closed": slice(total_rows, None), "full": slice(0, None)}
    norms = {f"initial_{name}": float(np.linalg.norm(r0[rows])) for name, rows in blocks.items()}
    norms.update({f"optimized_{name}": float(np.linalg.norm(r1[rows])) for name, rows in blocks.items()})
    ratios = {name: norms[f"optimized_{name}"] / max(norms[f"initial_{name}"], tiny) for name in blocks}
    return {"norms": norms, "ratios": ratios,
            "performance_gates": {"full_lte_80_percent": bool(ratios["full"] <= .8),
                                  "closed_lte_80_percent": bool(ratios["closed"] <= .8),
                                  "original_nonclosed_lte_110_percent": bool(ratios["original_nonclosed"] <= 1.1)}}


def recovered_provenance(recovery: dict) -> dict:
    """Retain the original STOP guard and raw/final artifacts behind recovery."""
    return {"recovered_status": recovery["status"], "recovered_driver": recovery["driver"],
            "recovered_external_guard": recovery["external_guard"], "original_failure": recovery["original_failure"],
            "original_raw_checkpoint": recovery["raw_receipt"], "original_raw_arrays": recovery["inputs"]["raw_arrays"],
            "original_final_arrays": recovery["inputs"]["final_arrays"], "original_stop_guard": recovery["inputs"]["external_guard"]}


def preflight() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    baseline = json.loads(PINS["baseline_result"][0].read_bytes())
    recovery = json.loads(PINS["joint_recovery_result"][0].read_bytes())
    guard = json.loads(PINS["joint_recovery_guard"][0].read_bytes())
    assert baseline["status"] == "UNVALIDATED" and baseline["artifact"]["sha256"] == PINS["baseline_field"][1]
    assert baseline["driver"]["sha256"] == PINS["baseline_driver"][1]
    assert baseline["raw_gcrotmk_info"] == 1 and baseline["raw_info_zero"] is False
    assert baseline["old_r_only_model"]["ancestor_source_raw_solver_info"] == 4
    assert recovery["status"] == "RECOVERED_UNVALIDATED_JOINT_ACTION_AFTER_SERIALIZATION_FAILURE"
    assert recovery["driver"]["sha256"] == PINS["joint_recovery_driver"][1]
    # The recovery guard's file is pinned above. Its driver_sha256 names the
    # separate guard helper, so it must not be confused with this JSON receipt.
    assert guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0
    assert recovery["raw_receipt"]["status"] == "UNVALIDATED_JOINT_ACTION_CHECKPOINT"
    assert recovery["inputs"]["raw_arrays"]["sha256"] == recovery["raw_receipt"]["artifact"]["sha256"]
    assert recovery["inputs"]["final_arrays"]["sha256"] == PINS["joint_recovered_final_arrays"][1]
    assert len(recovery["gates"]) == 10 and all(recovery["gates"].values())
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_HELD_CLOSED_CURRENT_DIRECTION_PREFLIGHT",
            "run_released": RUN_RELEASED, "inputs": inputs, "baseline_raw_gcrotmk_info": 1,
            "baseline_ancestor_source_raw_solver_info": 4, "original_1e_9_info0_acceptance": False,
            "recovered_joint_gates": recovery["gates"],
            "recovered_joint_original_provenance": recovered_provenance(recovery),
            "scope": "Receipt-only held preflight; no geometry, FMM, NtD/H factor, or board action."}


def _dense_self_check_system():
    """Independent nonunit-scaled [v,q25,g,psi] system with q04=P*g+C*psi."""
    omega = 7.0; p = np.array([[1.2], [-.7]], complex); c = np.array([[.8], [1.1]], complex)
    h = np.array([[3.4]], complex); resistance = 2.3
    magnetic_l = np.array([[.6, .2, -.4], [.2, .5, .1], [-.4, .1, .7]], complex)
    f25, f04 = magnetic_l[:1], magnetic_l[1:]; s = np.diag([2., 3., 5., 7.]).astype(complex)
    x0_physical = np.array([.4-.1j, .9+.2j, -.3+.6j, 0j]); q04 = p[:, 0] * x0_physical[2]
    old25 = f25[:, :1] @ x0_physical[1:2]; full25 = f25 @ np.r_[x0_physical[1], q04]
    full04 = f04 @ np.r_[x0_physical[1], q04]
    aold = np.zeros((4, 4), complex); anew = np.zeros((4, 4), complex); aold[0, 0] = anew[0, 0] = 1
    aold[1, 1] = -resistance - 1j*omega*f25[0, 0]; anew[1, 1] = aold[1, 1]
    anew[1, 2] = -1j*omega*(f25[0, 1:] @ p[:, 0]); anew[1, 3] = -1j*omega*(f25[0, 1:] @ c[:, 0])
    aold[2, 2] = -1.7
    anew[2, 1] = -1j*omega*(p[:, 0] @ f04[:, :1]).item()
    anew[2, 2] = -1.7 - 1j*omega*(p[:, 0] @ f04[:, 1:] @ p[:, 0]); anew[2, 3] = -1j*omega*(p[:, 0] @ f04[:, 1:] @ c[:, 0])
    anew[3, 1] = -1j*omega*(c[:, 0] @ f04[:, :1]).item(); anew[3, 2] = -1j*omega*(c[:, 0] @ f04[:, 1:] @ p[:, 0])
    anew[3, 3] = -h[0, 0] - 1j*omega*(c[:, 0] @ f04[:, 1:] @ c[:, 0])
    assert np.allclose(magnetic_l, magnetic_l.T) and np.allclose(anew, anew.T)
    rhs = np.array([1.1+.2j, -.8+.4j, .3-.5j, 0j]); x0 = np.linalg.solve(s, x0_physical)
    baseline = rhs-s@aold@s@x0; r0 = baseline.copy()
    r0[1] += 1j*omega*s[1, 1]*(full25-old25).item()
    r0[2] += 1j*omega*s[2, 2]*(p[:, 0]@full04).item()
    r0[3] += 1j*omega*s[3, 3]*(c[:, 0]@full04).item()
    expected = rhs-s@anew@s@x0; dpsi = -1j*omega*np.linalg.solve(h, c.T@full04); delta = np.array([0j, 0j, 0j, dpsi[0]/s[3, 3]])
    dq04 = c @ dpsi; f25d, f04d = f25[:, 1:] @ dq04, f04[:, 1:] @ dq04
    ad_expected = s @ anew @ (s @ delta)
    ad, _, _, _ = scaled_direction_action(dpsi, dq04, f25d, f04d, h@dpsi,
                                            lambda force: p[:, 0] @ force, lambda force: c[:, 0] @ force,
                                            omega=omega, nv=1, nx=2, total=3, scales=np.diag(s))
    assert np.allclose(ad, ad_expected)
    alpha = np.vdot(ad, r0)/np.vdot(ad, ad); r1 = r0-alpha*ad
    return r0, expected, ad, delta, r1, alpha


def self_check() -> None:
    r0, expected, ad, delta, r1, alpha = _dense_self_check_system()
    assert np.allclose(r0, expected) and np.allclose(np.vdot(ad, r1), 0, atol=1e-12)
    assert np.isfinite(alpha) and np.linalg.norm(r1) <= np.linalg.norm(r0)*(1+1e-12) and delta.shape == (4,)
    json.dumps(builtin({"metrics": screen_metrics(r0, r1, 1, 2, 3), "bool": np.bool_(True)}), allow_nan=False)
    print(f"{PROGRAM} v{VERSION}: PASS_HELD_CLOSED_CURRENT_DIRECTION_SELF_CHECK")


def _load_scaled_baseline(action):
    base = legacy.source.base
    with np.load(PINS["frequency_operator"][0], allow_pickle=False) as archive:
        y, b, resistance = base.csc(archive, "y")[1:, 1:], base.csc(archive, "b")[1:, :], base.csc(archive, "r")
        positive, negative, gauge = (int(archive[name][0]) for name in ("positive_active_index", "negative_active_index", "gauge_active_index"))
    with np.load(PINS["frequency_bridge"][0], allow_pickle=False) as archive: diagonal = np.asarray(archive["l04_contact_diagonal_admittance_s"])
    sv, si, sc = symmetric_scales(y, b, resistance, diagonal); base_scales = np.r_[sv, si, sc]
    with np.load(PINS["baseline_field"][0], allow_pickle=False) as field:
        state, saved_action, saved_residual, saved_physical = (np.asarray(field[name], dtype=np.complex128) for name in ("final_scaled_state", "final_true_action", "final_true_residual", "final_physical_state"))
        old25 = np.asarray(field["l25_magnetic_flux_linkage_wb"], dtype=np.complex128)
    total = len(base_scales); assert state.shape == saved_action.shape == saved_residual.shape == saved_physical.shape == (total,) and old25.shape == (legacy.NI,)
    rhs = np.zeros(total, complex); rhs[positive-1], rhs[negative-1] = sv[positive-1], -sv[negative-1]
    replay = relative(rhs-saved_action, saved_residual); physical_replay = relative(base_scales*state, saved_physical)
    assert replay <= 2e-8 and physical_replay <= 2e-8 and gauge == 0
    assert action.h_scale.shape == (action.stream_count-1,)
    return {"sv": sv, "si": si, "sc": sc, "sh": np.asarray(action.h_scale, float), "state": state, "saved_action": saved_action,
            "saved_residual": saved_residual, "saved_physical": saved_physical, "old25": old25, "rhs": rhs,
            "replay_relative": replay, "physical_replay_relative": physical_replay, "port_indices": (positive-1, negative-1), "total": total}


def worker(output: Path) -> None:
    budget = joint.ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB); frozen = output / "driver-at-run.py"
    try:
        assert frozen.read_bytes() == Path(__file__).read_bytes(); provenance = preflight()
        inherited, local_paths = joint.preflight()  # receipt/geometry chain only; no FMM or H yet.
        budget.check("pinned receipts before saved H factor"); action = joint.ntd.load_action(inherited["qualified_stream_result"]); budget.check("loaded existing saved H factor")
        loaded = _load_scaled_baseline(action); sv, si, sc, sh = (loaded[name] for name in ("sv", "si", "sc", "sh")); total, ns = loaded["total"], len(sh)
        base_scales, all_scales = np.r_[sv, si, sc], np.r_[sv, si, sc, sh]; assert total == legacy.TOTAL and all_scales.shape == (total+ns,)
        with np.load(PINS["joint_recovered_final_arrays"][0], allow_pickle=False) as archive:
            full25, full04 = np.asarray(archive["l25_joint_flux_wb"], complex), np.asarray(archive["l04_joint_flux_wb"], complex)
        assert full25.shape == loaded["old25"].shape and full04.shape == action.first.shape
        pt0, _, pt0_gradient = joint.cross.lift_probe.contact_lift_transpose(action, full04); ct0 = action.ct_apply(full04)
        r0_existing = loaded["saved_residual"].copy(); r0_existing[legacy.NV:legacy.NX] += 1j*OMEGA*si*(full25-loaded["old25"]); r0_existing[legacy.NX:total] += 1j*OMEGA*sc*pt0
        r0 = np.r_[r0_existing, 1j*OMEGA*sh*ct0]; assert np.all(np.isfinite(r0)) and np.all(np.isfinite(full25)) and np.all(np.isfinite(full04))
        unchanged_floor = float(np.linalg.norm(r0[:legacy.NV])/max(np.linalg.norm(r0), np.finfo(float).tiny)); pos, neg = loaded["port_indices"]
        raw_port = (base_scales*loaded["state"])[pos]-(base_scales*loaded["state"])[neg]
        initial = output / "initial-complete-model-residual.npz"
        np.savez_compressed(initial, r0_scaled=r0, saved_magnetic_residual_scaled=loaded["saved_residual"], joint_l25_minus_old_l25_flux_wb=full25-loaded["old25"], full04_flux_wb=full04, pt_full04_wb=pt0, ct_full04_wb=ct0, scales_sv=sv, scales_si=si, scales_sc=sc, scales_sh=sh, baseline_scaled_state=loaded["state"], rhs_scaled=loaded["rhs"])
        budget.check("saved complete-model residual before geometry")
        common = {"program": PROGRAM, "version": VERSION, "run_released": RUN_RELEASED, "driver": receipt(frozen), "inputs": provenance["inputs"], "joint_helper_provenance": inherited, "recovered_joint_original_provenance": provenance["recovered_joint_original_provenance"], "baseline": {"raw_gcrotmk_info": 1, "ancestor_source_raw_solver_info": 4, "original_1e_9_info0_acceptance": False, "saved_rhs_action_relative": loaded["replay_relative"], "saved_scaled_to_physical_relative": loaded["physical_replay_relative"]}, "initial_checkpoint": receipt(initial), "scales": {"sv_count": len(sv), "si_count": len(si), "sc_count": len(sc), "sh_count": len(sh)}, "initial_unchanged_potential_floor": unchanged_floor, "raw_port_z_unchanged_ohm": {"initial": pair(raw_port), "after_direction": pair(raw_port), "reason": "dv=dq25=dg=0; the direction changes psi only."}}
        if unchanged_floor > .8:
            result = dict(common, status="STOP_SCREEN_IMPOSSIBLE_UNCHANGED_ROWS", positive_screen_only=False, no_extension_after_screen=True, screen="Skipped before geometry/FMM because immutable potential rows exceed 80 percent of the complete-model residual.", scope="Held unvalidated diagnostic; no FMM, source action, geometry, board solve, or acceptance claim.")
            save_json(output/"result.json", result); save_json(output/"final-worker-budget.json", {"status": "FINAL_WORKER_BUDGET_AFTER_RESULT", "result": receipt(output/"result.json"), "budget": budget.receipt()}); budget.check("final worker budget serialization"); return
        dpsi = -1j*OMEGA*action.solve_h(ct0); dq04 = action.c_apply(dpsi); h_dpsi = action.ct_apply(action.resistance@dq04)
        h_identity = relative(h_dpsi, (action.scaled_h@(dpsi/sh))/sh); h_rhs_identity = relative(h_dpsi, -1j*OMEGA*ct0); bc_identity = float(np.linalg.norm(action.b_apply(dq04)));
        pt_rc, projected_rc, pt_rc_gradient = joint.cross.lift_probe.contact_lift_transpose(action, action.resistance@dq04)
        rc_norm = max(np.linalg.norm(action.resistance@dq04), np.finfo(float).tiny)
        pt_rc_identity, projected_rc_identity = float(np.linalg.norm(pt_rc)/rc_norm), float(np.linalg.norm(projected_rc)/rc_norm)
        structural = {"rhs_minus_saved_action_matches_saved_residual": bool(loaded["replay_relative"] <= 2e-8), "saved_scaled_to_physical_matches": bool(loaded["physical_replay_relative"] <= 2e-8), "bc_dpsi": bool(bc_identity <= 1e-8*max(np.linalg.norm(dq04), 1.)), "pt_r_c_dpsi": bool(pt_rc_identity <= 2e-8), "projected_r_c_dpsi": bool(projected_rc_identity <= 2e-8), "h_scaled_identity": bool(h_identity <= 2e-8), "h_solve_rhs_identity": bool(h_rhs_identity <= 2e-8), "pt_full04_gradient": bool(pt0_gradient <= 1e-7), "finite_initial_direction": bool(np.isfinite(dpsi).all() and np.isfinite(dq04).all() and np.isfinite(h_dpsi).all())}
        pre_fmm = output / "pre-fmm-closed-direction.npz"
        np.savez_compressed(pre_fmm, r0_scaled=r0, dpsi_closed_coordinate=dpsi, dpsi_scaled_coordinate=dpsi/sh, dq04_closed_current_a=dq04, h_dpsi_v=h_dpsi, bc_dpsi=action.b_apply(dq04), pt_r_c_dpsi=pt_rc, projected_r_c_dpsi=projected_rc)
        pre_fmm_receipt = output / "pre-fmm-closed-direction.json"
        save_json(pre_fmm_receipt, {"program": PROGRAM, "version": VERSION, "status": "PRE_FMM_CLOSED_DIRECTION_STRUCTURAL_CHECKPOINT", "artifact": receipt(pre_fmm), "metrics": {"saved_rhs_action_relative": loaded["replay_relative"], "saved_scaled_to_physical_relative": loaded["physical_replay_relative"], "bc_dpsi_norm": bc_identity, "pt_r_c_dpsi_relative": pt_rc_identity, "projected_r_c_dpsi_relative": projected_rc_identity, "h_scaled_identity_relative": h_identity, "h_solve_rhs_identity_relative": h_rhs_identity, "pt_full04_gradient_relative": pt0_gradient, "pt_rc_gradient_diagnostic": pt_rc_gradient}, "gates": structural})
        common["pre_fmm_closed_direction_checkpoint"] = receipt(pre_fmm_receipt)
        budget.check("saved closed-current direction checkpoint before geometry and FMM")
        assert all(structural.values()), structural
        source_geometry, target_geometry, _, _, _ = joint.cross._load_cross_geometry(); source_geometry[-1][2] = joint.cross.L25_Z_M; target_geometry[-1][2] = joint.cross.L04_Z_M
        matrices = [joint.read_csc(path, prefix) for path, _, prefix in local_paths]; zero25 = np.zeros_like(loaded["old25"]); budget.check("direction geometry and matrices before one full-point action")
        runtime = joint.magnetic.verify_environment()
        runtime_receipt = output / "joint-magnetic-runtime-before-fmm.json"
        save_json(runtime_receipt, {"program": PROGRAM, "version": VERSION, "status": "JOINT_MAGNETIC_RUNTIME_VERIFIED_BEFORE_FMM", "runtime": runtime})
        common["joint_magnetic_runtime_before_fmm"] = receipt(runtime_receipt)
        budget.check("verified and recorded joint magnetic runtime before FMM")
        (f25d, f04d), calls = joint.joint_action(zero25, dq04, (source_geometry, target_geometry), matrices, budget); assert len(calls) == 4 and np.isfinite(f25d).all() and np.isfinite(f04d).all()
        del source_geometry, target_geometry, matrices
        gc.collect()
        raw_direction = output / "raw-direction-joint-action-before-projections.npz"; np.savez_compressed(raw_direction, dpsi_closed_coordinate=dpsi, dq04_closed_current_a=dq04, f25d_joint_flux_wb=f25d, f04d_joint_flux_wb=f04d, fmm_calls_json=np.asarray(json.dumps(builtin(calls), allow_nan=False)))
        budget.check("raw direction action checkpoint before H projections and gates")
        projection = {}
        def p_transpose(force):
            value, _, gradient = joint.cross.lift_probe.contact_lift_transpose(action, force); projection["gradient"] = gradient; return value
        ad, physical_ad, ptd, ctd = scaled_direction_action(dpsi, dq04, f25d, f04d, h_dpsi, p_transpose, action.ct_apply, omega=OMEGA, nv=legacy.NV, nx=legacy.NX, total=total, scales=all_scales)
        quadratic = np.vdot(ad, ad); assert np.isfinite(quadratic) and quadratic.real > 0 and abs(quadratic.imag) <= 64*np.finfo(float).eps*quadratic.real
        alpha = np.vdot(ad, r0)/quadratic; r1 = r0-alpha*ad; assert np.all(np.isfinite(ad)) and np.all(np.isfinite(r1)) and np.isfinite(alpha)
        direction = output / "closed-current-direction-screen-arrays.npz"; np.savez_compressed(direction, r0_scaled=r0, ad_scaled=ad, r1_scaled=r1, dpsi_closed_coordinate=dpsi, dpsi_scaled_coordinate=dpsi/sh, dq04_closed_current_a=dq04, f25d_joint_flux_wb=f25d, f04d_joint_flux_wb=f04d, physical_ad_rows=physical_ad, alpha=np.asarray([alpha]), scales_sv=sv, scales_si=si, scales_sc=sc, scales_sh=sh)
        metrics = screen_metrics(r0, r1, legacy.NV, legacy.NX, total); orthogonality = float(abs(np.vdot(ad, r1))/max(np.linalg.norm(ad)*np.linalg.norm(r1), np.finfo(float).tiny)); ptd_gradient = projection["gradient"]
        identity_gates = dict(structural, four_scalar_source_fmm_calls=bool(len(calls) == 4), direction_pt_gradient=bool(ptd_gradient <= 1e-7), alpha_orthogonality=bool(orthogonality <= 2e-8), norm_nonincrease=bool(np.linalg.norm(r1) <= np.linalg.norm(r0)*(1+1e-12)))
        finite_provenance_identity = bool(np.isfinite(r0).all() and np.isfinite(ad).all() and np.isfinite(r1).all() and all(identity_gates.values())); positive = bool(finite_provenance_identity and all(metrics["performance_gates"].values()))
        result = dict(common, status="UNVALIDATED_CLOSED_CURRENT_DIRECTION_SCREEN", artifact=receipt(direction), raw_direction_action_checkpoint=receipt(raw_direction), action_calls=calls, magnetic_runtime=runtime, structural_metrics={"bc_dpsi_norm": bc_identity, "pt_r_c_dpsi_relative": pt_rc_identity, "projected_r_c_dpsi_relative": projected_rc_identity, "h_scaled_identity_relative": h_identity, "alpha_orthogonality_relative": orthogonality, "pt_direction_gradient_relative": ptd_gradient}, structural_gates=identity_gates, finite_provenance_identity_gates=finite_provenance_identity, metrics=metrics, alpha=pair(alpha), positive_screen_only=positive, no_extension_after_screen=True, raw_solver_info_preserved={"raw_gcrotmk_info": 1, "raw_info_zero": False, "ancestor_source_raw_solver_info": 4, "original_1e_9_info0_acceptance": False}, linear_superposition_only="r1 is saved complete-model r0 minus alpha times the newly measured A*direction; no independent fresh full candidate-field operator replay was performed.", scope="One held conditional unvalidated psi-only direction using the full point geometry/four-channel joint action even though dq25=0. L04 remains Pg+Cpsi with absent distributed GC/charge, other layers/vias, and global return. No PSD, error bound, PowerSI, accuracy, or physical acceptance claim.")
        save_json(output/"result.json", result); budget.check("result serialization"); save_json(output/"final-worker-budget.json", {"status": "FINAL_WORKER_BUDGET_AFTER_RESULT", "result": receipt(output/"result.json"), "budget": budget.receipt()}); budget.check("final worker budget serialization")
        assert all(identity_gates.values()), identity_gates  # Negative performance remains exit 0.
    except BaseException:
        save_json(output/"failure.json", {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED", "failure": traceback.format_exc(), "budget": budget.receipt()}); raise
    finally: gc.collect()


def launch(output: Path) -> None:
    """Held owned-PID 420s/450s/32GiB guard adapted from the frozen joint driver."""
    assert RUN_RELEASED and not output.exists() and sha(Path(legacy.counter.__file__)) == joint.PINS["counter"][1]
    availability = legacy.source.available_34gib(); getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(legacy.counter._MemoryCounters), ctypes.wintypes.DWORD); getter.restype = ctypes.wintypes.BOOL
    output.mkdir(parents=True); frozen = output/"driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes()); command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    started = time.monotonic(); private = working = 0; reason = guard_failure = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        try:
            print(f"closed-current direction: owned PID={child.pid}, {EXTERNAL_SECONDS}s/32GiB", flush=True)
            while child.poll() is None:
                values = legacy.counter._MemoryCounters(); values.cb = ctypes.sizeof(values)
                if getter(int(child._handle), ctypes.byref(values), values.cb): private, working = max(private, int(values.private_usage)), max(working, int(values.working_set))
                elif child.poll() is None: reason = "STOP_PROCESS_MEMORY_QUERY"
                if time.monotonic()-started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private, working) > int(MEMORY_GIB*2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason: child.kill(); break
                time.sleep(.5)
            code = child.wait(timeout=10)
        except BaseException:
            guard_failure = traceback.format_exc(); reason = "STOP_PARENT_GUARD_EXCEPTION"
            if child.poll() is None: child.kill()
            code = child.wait(timeout=10)
    save_json(output/"external-budget.json", {"program": PROGRAM, "version": VERSION, "status": reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT"), "owned_pid": child.pid, "exit_code": code, "elapsed_s": time.monotonic()-started, "sampled_peak_private_bytes": private, "sampled_peak_working_set_bytes": working, "max_runtime_s": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB*2**30), "driver_sha256": sha(frozen), "worker_command": command, "memory_at_launch": availability, "guard_failure": guard_failure})
    raise SystemExit(0 if reason is None and code == 0 else 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true"); modes.add_argument("--preflight", action="store_true"); modes.add_argument("--run", action="store_true"); modes.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS); parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight: print(json.dumps(builtin(preflight()), indent=2, allow_nan=False))
    elif args.run: assert RUN_RELEASED and args.output is not None; launch(args.output.resolve())
    else: assert RUN_RELEASED and args.output is not None; worker(args.output.resolve())


if __name__ == "__main__": main()
