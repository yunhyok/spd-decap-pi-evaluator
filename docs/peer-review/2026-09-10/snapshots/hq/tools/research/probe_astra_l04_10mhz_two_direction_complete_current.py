"""SPD Decap PI Evaluator v0.23.1: held two-column complete-current screen."""
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
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"), str(ROOT / "outputs/research-runtime"), str(ROOT / "outputs/research-fmm-runtime")]
from scipy import sparse
from scipy.sparse.linalg import gmres, LinearOperator
RUN_RELEASED = True
import continue_astra_l04_10mhz_l25_magnetic_gcrotmk as legacy  # noqa: E402
import probe_astra_l04_10mhz_closed_current_direction as prior  # noqa: E402
import probe_astra_l25_l04_joint_magnetic_action as joint  # noqa: E402

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 480.0, 540.0, 32.0
OMEGA = 2 * pi * 10_000_000.0
RESEARCH = ROOT / "outputs/research"
PRIOR = RESEARCH / "astra-l04-10mhz-closed-current-direction-01"
PINS = {
    "prior_result": (PRIOR / "result.json", "63ba3212c49e73cdbbb576e1a213d3b89ac24cdfb5f4655469bcc995ac2c0c47"),
    "prior_guard": (PRIOR / "external-budget.json", "4bd84257c478967fcd7d39c1fcb5b0e209b0147cad56d3884569ad6a2c2addcf"),
    "prior_arrays": (PRIOR / "closed-current-direction-screen-arrays.npz", "11a36fa73db93d930439839af4d8cb1a0025649bade1901d2f8d7fa2f468615b"),
    "prior_initial": (PRIOR / "initial-complete-model-residual.npz", "16fd56e355f950b6ac5127a979758d4135343713d9a5b432fa2d2c57b664dd1b"),
    "prior_driver": (PRIOR / "driver-at-run.py", "aaf7700bb9294f71d69f142132795e0989763f467c5c27c2f88e8ee0bc52e7d7"),
    "magnetic_driver": (Path(legacy.__file__), "fb737651456a57c1fcba96073ef1b8b2e48682b4dfb930a1abf10603bf51bcce"),
    "joint_action_source": (Path(joint.__file__), "caf5ba8fd0a1a156f987069be4d14e12d5ae8268e613716d120fb6edf2ab6a5e"),
    "frequency_operator": legacy.source.PINS["operator"], "frequency_bridge": legacy.source.PINS["bridge"],
    "static": legacy.source.PINS["static"], "cached_factors": legacy.source.PINS["cache_factors"],
}


def sha(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict:
    digest = sha(path); assert expected is None or digest == expected, str(path)
    return {"path": str(path.resolve()), "sha256": digest, "size_bytes": int(path.stat().st_size)}


def builtin(value):
    if isinstance(value, np.generic): return builtin(value.item())
    if isinstance(value, np.ndarray): return [builtin(item) for item in value.tolist()]
    if isinstance(value, complex): assert np.isfinite(value); return [float(value.real), float(value.imag)]
    if isinstance(value, Path): return str(value)
    if isinstance(value, dict): return {str(key): builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [builtin(item) for item in value]
    return value


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(builtin(value), indent=2, allow_nan=False) + "\n", encoding="utf-8")


def relative(left, right) -> float:
    return float(np.linalg.norm(left-right) / max(np.linalg.norm(left), np.linalg.norm(right), np.finfo(float).tiny))


def active_voltage(physical_voltage, index: int, nv: int) -> complex:
    """The saved active gauge is represented by -1 after the active-to-gauged shift."""
    assert physical_voltage.shape == (nv,)
    if index == -1: return 0j
    assert 0 <= index < nv
    value = complex(physical_voltage[index]); assert np.isfinite(value)
    return value


def blocks(r0, r2, nv, nx, total):
    names = {"potential": slice(0, nv), "l25": slice(nv, nx), "contact": slice(nx, total), "original_nonclosed": slice(0, total), "closed": slice(total, None), "full": slice(None)}
    norms = {f"initial_{name}": float(np.linalg.norm(r0[row])) for name, row in names.items()}
    norms.update({f"candidate_{name}": float(np.linalg.norm(r2[row])) for name, row in names.items()})
    tiny = np.finfo(float).tiny; ratios = {name: norms[f"candidate_{name}"] / max(norms[f"initial_{name}"], tiny) for name in names}
    return {"norms": norms, "ratios": ratios, "performance_gates": {"full_lte_80_percent": bool(ratios["full"] <= .8), "closed_lte_80_percent": bool(ratios["closed"] <= .8), "original_nonclosed_lte_110_percent": bool(ratios["original_nonclosed"] <= 1.1), "potential_lte_200_percent": bool(ratios["potential"] <= 2), "l25_lte_200_percent": bool(ratios["l25"] <= 2)}}


def fit_columns(r0, columns):
    raw = np.linalg.norm(columns, axis=0); assert np.all(np.isfinite(raw)) and np.all(raw > 0)
    normalized = columns / raw; coefficient_normalized, _, rank, singular = np.linalg.lstsq(normalized, r0, rcond=1e-12)
    coefficient = coefficient_normalized / raw; condition = float(singular[0] / max(singular[-1], np.finfo(float).tiny))
    residual = r0-columns@coefficient
    return coefficient, residual, {"raw_column_norms": raw, "normalized_column_norms": np.linalg.norm(normalized, axis=0), "singular_values": singular, "rank": int(rank), "normalized_condition": condition, "rank2": bool(rank == 2), "condition_lte_1e6": bool(condition <= 1e6), "orthogonality_relative": float(np.linalg.norm(normalized.conj().T@residual)/max(np.linalg.norm(normalized)*np.linalg.norm(residual), np.finfo(float).tiny))}


def assemble_ad2(electrical_scaled, f25, f04, h_scale, p_transpose, c_transpose, *, omega, nv, nx, scales):
    """Keep electrical rows and add exactly the complete two-sheet magnetic rows."""
    total = len(electrical_scaled); result = np.r_[electrical_scaled, np.zeros(len(h_scale), complex)]
    result[nv:nx] -= 1j*omega*scales[nv:nx]*f25
    result[nx:total] -= 1j*omega*scales[nx:total]*p_transpose(f04)
    result[total:] -= 1j*omega*h_scale*c_transpose(f04)
    return result


def preflight() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    result, guard = json.loads(PINS["prior_result"][0].read_bytes()), json.loads(PINS["prior_guard"][0].read_bytes())
    assert result["driver"]["sha256"] == PINS["prior_driver"][1] and result["artifact"]["sha256"] == PINS["prior_arrays"][1]
    assert result["positive_screen_only"] is False and result["raw_solver_info_preserved"]["raw_gcrotmk_info"] == 1
    assert len(result["structural_gates"]) == 13 and all(result["structural_gates"].values())
    assert guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0
    assert result["baseline"]["original_1e_9_info0_acceptance"] is False
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_HELD_TWO_DIRECTION_PREFLIGHT", "run_released": RUN_RELEASED, "inputs": inputs, "prior_structural_gates": result["structural_gates"], "prior_positive_screen_only": False, "raw_gcrotmk_info": 1, "original_physical_failures": sorted(legacy.source.FAILED_PHYSICAL_GATES), "scope": "Receipt-only held preflight; no factors, H, FMM, or board action."}


def self_check() -> None:
    # Symmetric complete [v,q25,g,psi] matrix with nonunit scales and two independent columns.
    omega = 7.0
    physical = np.array([[4., 1., .2, -.3], [1., 5., .6, .4], [.2, .6, 3., .7], [-.3, .4, .7, 2.]], complex)
    scales = np.array([2., 3., 5., 7.]); scaled = np.diag(scales)@physical@np.diag(scales)
    assert np.allclose(physical, physical.T)
    dpsi = np.array([0j, 0j, 0j, .8-.2j]); d2 = np.array([.3+.1j, -.4+.2j, .6-.5j, 0j])
    a1, a2 = scaled@dpsi, scaled@d2; r0 = (1.2-.4j)*a1+(-.7+.9j)*a2+np.array([.1j, -.2, .3j, .1])
    coeff, r2, fit = fit_columns(r0, np.column_stack((a1, a2)))
    assert fit["rank2"] and fit["condition_lte_1e6"] and fit["orthogonality_relative"] < 1e-12
    assert np.linalg.norm(np.column_stack((a1, a2)).conj().T@r2) <= 1e-11*np.linalg.norm(np.column_stack((a1, a2)))*max(np.linalg.norm(r2), 1.)
    # Independent complete A: electrical plus a symmetric magnetic q/g/psi block.
    ae = np.array([[4., .3, -.2, 0.], [.3, 3., .4, 0.], [-.2, .4, 2., 0.], [0., 0., 0., 1.]], complex)
    lm = np.array([[.9, -.25], [-.25, .6]], complex)
    am = np.zeros((4, 4), complex); am[1:, 1:] = -1j*omega*np.array([[lm[0,0],lm[0,1],lm[0,1]], [lm[1,0],lm[1,1],lm[1,1]], [lm[1,0],lm[1,1],lm[1,1]]])
    a_complete = ae+am; d_complete = np.array([.3+.1j, -.4+.2j, .6-.5j, 0j]); expected = np.diag(scales)@a_complete@np.diag(scales)@d_complete
    physical_d = scales*d_complete; f25 = np.array([lm[0]@physical_d[1:3]]); f04 = np.array([lm[1]@physical_d[1:3]])
    electrical = (np.diag(scales)@ae@np.diag(scales)@d_complete)[:3]
    got = assemble_ad2(electrical, f25, f04, np.array([scales[3]]), lambda f: f, lambda f: f, omega=omega, nv=1, nx=2, scales=scales[:3])
    assert np.allclose(a_complete, a_complete.T) and np.allclose(got, expected)
    port_probe = np.array([2 + 3j])
    assert active_voltage(port_probe, 0, 1) == 2 + 3j and active_voltage(port_probe, -1, 1) == 0j
    try:
        active_voltage(port_probe, 1, 1)
    except AssertionError:
        pass
    else:
        raise AssertionError("invalid scalar port index was accepted")
    json.dumps(builtin({"fit": fit, "gates": {"ok": np.bool_(True)}}), allow_nan=False)
    print(f"{PROGRAM} v{VERSION}: PASS_HELD_TWO_DIRECTION_SELF_CHECK")


def _prior_arrays():
    with np.load(PINS["prior_arrays"][0], allow_pickle=False) as z:
        r0, a1, r1, alpha = (np.asarray(z[name], complex) for name in ("r0_scaled", "ad_scaled", "r1_scaled", "alpha"))
        dpsi, sv, si, sc = (np.asarray(z[name]) for name in ("dpsi_closed_coordinate", "scales_sv", "scales_si", "scales_sc"))
    with np.load(PINS["prior_initial"][0], allow_pickle=False) as z: baseline_state = np.asarray(z["baseline_scaled_state"], complex)
    assert relative(r1, r0-alpha[0]*a1) <= 2e-8
    return r0, a1, r1, alpha[0], dpsi, sv, si, sc, baseline_state


def _one_r_direction(rhs, sv, si, sc, output, budget):
    """One exact frozen arbitrary-RHS preconditioner application; never reused as linear M."""
    base, source = legacy.source.base, legacy.source
    qualified = source.verify_inputs()
    budget.check("qualified cache/static receipt verification")
    reports, factors = {}, {}
    with np.load(source.PINS["static"][0], allow_pickle=False) as z:
        p, sp = base.csc(z, "p"), np.asarray(z["diagonal_scale"]); joint_rows, contacts = np.asarray(z["joint_native_l14_gauged_potential_indices"]), np.asarray(z["l04_contact_gauged_trace_rows"])
    pkk, robin = base.scaled_block(p, sp, sp), p[len(joint_rows):, len(joint_rows):].tocsc()
    with np.load(source.PINS["cache_factors"][0], allow_pickle=False) as z:
        lower, upper, perm_r, perm_c = source._load_public_factor(z)
        assert np.array_equal(z["diagonal_scale"], sp) and np.array_equal(z["joint_native_l14_gauged_potential_indices"], joint_rows)
    budget.check("cached B1 coordinate binding")
    calls = {"normal_count": 0, "normal_seconds": 0., "transpose_count": 0, "transpose_seconds": 0.}; b1 = source._make_b1(pkk, lower, upper, perm_r, perm_c, calls)
    with np.load(source.PINS["operator"][0], allow_pickle=False) as z:
        y, b, resistance = base.csc(z, "y")[1:, 1:], base.csc(z, "b")[1:, :], base.csc(z, "r"); native, l14, l25, l02 = base.partition(z["conditional_global_active_index"])
    with np.load(source.PINS["bridge"][0], allow_pickle=False) as z: u, delta, diagonal = base.csc(z, "l04_u")[1:, :], base.csc(z, "l04_delta")[1:, 1:], np.asarray(z["l04_contact_diagonal_admittance_s"])
    self_receipt, near_receipt, *_ = legacy._magnetic_receipts(); l_full, lself = legacy._load_lfull(self_receipt, near_receipt, legacy.NI)
    resistance_aux = resistance+1j*OMEGA*lself; yq, bq, rq = base.scaled_block(y[l25,:][:,l25],sv[l25],sv[l25]), base.scaled_block(b[l25,:],sv[l25],si), base.scaled_block(resistance_aux,si,si)
    pqq = sparse.bmat([[yq,bq],[bq.T,-rq]],format="csc"); prr = base.scaled_block(y[l02,:][:,l02]+delta[l02,:][:,l02],sv[l02],sv[l02])
    base.factor_block("two_direction_l25_q", pqq, factors, reports, output, base.Events(output/"progress.jsonl")); budget.check("fresh L25 Q factor")
    base.factor_block("two_direction_l02_r", prr, factors, reports, output, base.Events(output/"progress.jsonl")); budget.check("fresh L02 R factor"); fq, fr = factors["two_direction_l25_q"], factors["two_direction_l02_r"]
    primal_scale = np.r_[np.r_[sv,si],sp[len(joint_rows):]]; primal_scale[joint_rows] = sp[:len(joint_rows)]; K, Q, R = np.r_[joint_rows,np.arange(legacy.NX,legacy.NX+source.TRACE_SIZE)], np.r_[l25,np.arange(legacy.NV,legacy.NX)], l02
    assert len(np.unique(np.r_[K,Q,R])) == legacy.NX+source.TRACE_SIZE
    assert np.array_equal(primal_scale[K], sp)
    counters = {"b1": 0, "q": 0, "r": 0, "factor_probe_solves_reported_separately": reports}
    inverse = {"b1": [], "q": [], "r": []}
    def old_a(value):
        answer=np.r_[y@value[:legacy.NV]+b@value[legacy.NV:],b.T@value[:legacy.NV]-resistance_aux@value[legacy.NV:]]; answer[:legacy.NV]+=delta@value[:legacy.NV]; return answer
    def primal(value): return primal_scale*source.primal_full_action(old_a,u,robin,contacts,legacy.NV,primal_scale*value)
    def embed(rows,value): answer=np.zeros(legacy.NX+source.TRACE_SIZE,complex); answer[rows]=value; return answer
    actual_identity, actual_info = {}, 0
    def primal_solve_scaled(q):
      nonlocal actual_identity, actual_info
      zr=fr.solve(q[R]); counters["r"]+=1; inverse["r"].append(relative(prr@zr,q[R])); budget.check("one R inverse")
      pr=primal(embed(R,zr)); qk, qq=q[K]-pr[K], q[Q]-pr[Q]
      def cb1(x):
        counters["b1"]+=1; assert counters["b1"]<=7; answer=b1(x); inverse["b1"].append(relative(pkk@answer,x)); budget.check("B1 inverse"); return answer
      def cq(x):
        counters["q"]+=1; assert counters["q"]<=6; answer=fq.solve(x); inverse["q"].append(relative(pqq@answer,x)); budget.check("Q inverse"); return answer
      zk0=cb1(qk); h=qq-primal(embed(K,zk0))[Q]
      def reduced(x):
        zq=cq(x); pkq=primal(embed(Q,zq))[K]; return pqq@zq-primal(embed(K,cb1(pkq)))[Q]
      yi, info=gmres(LinearOperator((len(Q),len(Q)),matvec=reduced,dtype=complex),h,restart=4,maxiter=1,rtol=0.,atol=.1*np.linalg.norm(h),callback_type="pr_norm")
      zq=cq(yi); pq=primal(embed(Q,zq)); zk=cb1(qk-pq[K]); pk=primal(embed(K,zk)); out=np.zeros_like(q); out[K],out[Q],out[R]=zk,zq,zr; full=q-primal(out)
      identities={"k": float(np.linalg.norm(full[K]-(qk-pkk@zk-pq[K]))), "q": float(np.linalg.norm(full[Q]-(qq-pk[Q]-pqq@zq))), "r": float(np.linalg.norm(full[R]-((q[R]-prr@zr)-pk[R]-pq[R]))), "full": float(np.linalg.norm(full)), "tolerance": float(64*np.finfo(float).eps*max(1.,np.linalg.norm(q),np.linalg.norm(out),np.linalg.norm(full)))}
      actual_identity, actual_info = identities, int(info)
      return out
    # One lifted-MNA call owns the TOTAL-to-primal split; do not call the primal solver directly.
    d2 = source.lifted_mna_preconditioner(rhs, np.r_[sv,si,sc], primal_scale, u, diagonal, contacts, primal_solve_scaled, legacy.NV, legacy.NX)
    max_inverse={name: float(max(values,default=0.)) for name,values in inverse.items()}
    assert counters["r"]==1 and counters["q"]<=6 and counters["b1"]<=7 and np.isfinite(d2).all()
    resources=[pkk,robin,lower,upper,perm_r,perm_c,pqq,prr,y,b,resistance,resistance_aux,delta,u,lself,l_full]
    assert actual_identity
    return d2, {"qualified_cache_static": qualified, "counters": counters, "inverse_relative_max": max_inverse, "gmres_info": actual_info, "identities": actual_identity, "factors": reports}, resources


def worker(output: Path) -> None:
    budget = joint.ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    try:
        assert (output/"driver-at-run.py").read_bytes() == Path(__file__).read_bytes(); prov=preflight(); r0,a1,r1,alpha1,dpsi,sv,si,sc,baseline_state=_prior_arrays(); total=len(sv)+len(si)+len(sc)
        assert r0.shape==a1.shape==r1.shape and total < len(r0) and relative(r1,r0-alpha1*a1)<=2e-8
        d2, m_receipts, resources=_one_r_direction(r1[:total],sv,si,sc,output,budget); assert d2.shape==(total,)
        d2_path=output/"m-existing-direction-before-h.npz"; np.savez_compressed(d2_path,d2_scaled=d2,d2_physical=np.r_[sv,si,sc]*d2,rhs_saved_psi_optimized=r1[:total]); budget.check("completed one M direction")
        m_gates={"inverse":bool(all(value<=2e-8 for value in m_receipts["inverse_relative_max"].values())),"primal_identities":bool(all(m_receipts["identities"][name]<=m_receipts["identities"]["tolerance"] for name in ("k","q","r")))}
        save_json(output/"m-existing-receipts-before-acceptance.json", {"program":PROGRAM,"version":VERSION,"status":"ONE_R_RHS_DEPENDENT_M_DIRECTION","artifact":receipt(d2_path),"m":m_receipts,"gates":m_gates})
        assert all(m_gates.values()), m_gates
        rss_before=joint.ntd.recon._rss_bytes(); del resources; gc.collect(); rss_after=joint.ntd.recon._rss_bytes(); phase={"elapsed_s": budget.receipt()["elapsed_s"], "rss_before_release":rss_before,"rss_after_release":rss_after,"rss_limit_bytes":8*2**30,"elapsed_lte_150":bool(budget.receipt()["elapsed_s"]<=150),"rss_lte_8_gib":bool(rss_after<=8*2**30),"status":"M_FACTORS_RELEASED_BEFORE_H_FMM"}; save_json(output/"pre-fmm-factor-release.json",phase)
        if not (phase["elapsed_lte_150"] and phase["rss_lte_8_gib"]):
            save_json(output/"result.json", {"program":PROGRAM,"version":VERSION,"status":"STOP_BEFORE_FMM_INSUFFICIENT_REMAINING_BUDGET","run_released":RUN_RELEASED,"inputs":prov["inputs"],"d2":receipt(d2_path),"m_receipt":receipt(output/"m-existing-receipts-before-acceptance.json"),"factor_release":receipt(output/"pre-fmm-factor-release.json"),"screen_pass":False,"positive_screen_only":False,"no_extension_after_screen":True}); save_json(output/"final-worker-budget.json",{"status":"FINAL_WORKER_BUDGET_AFTER_RESULT","result":receipt(output/"result.json"),"budget":budget.receipt()}); budget.check("stop serialization"); return
        inherited, local_paths=joint.preflight(); action=joint.ntd.load_action(inherited["qualified_stream_result"]); budget.check("loaded qualified H after M-factor release")
        physical=np.r_[sv,si,sc]*d2; dg=physical[legacy.NX:]; field=action.apply(dg,return_field=True); q04d=field.branch_current_a; dq25=physical[legacy.NV:legacy.NX]
        field_kcl=relative(action.b_apply(q04d),field.target_bq_a); field_stationarity=float(field.metrics["energy_scaled_stationarity_relative"]); field_dual=float(field.metrics["dual_rq_relative"]); field_gates={"shape":bool(q04d.shape==action.first.shape),"finite":bool(np.isfinite(q04d).all()),"kcl":bool(field_kcl<=1e-7),"energy_scaled_stationarity":bool(field_stationarity<=1e-7),"dual_rq":bool(field_dual<=1e-7),"dual_finite":bool(np.isfinite(field.dual_potential_v).all()),"stationarity_metrics_finite":bool(all(np.isfinite(value) for value in field.metrics.values()))}; save_json(output/"field-before-fmm.json",{"program":PROGRAM,"version":VERSION,"status":"RETURNED_CONTACT_NTD_FIELD_BEFORE_FMM","metrics":field.metrics,"kcl_relative":field_kcl,"gates":field_gates}); budget.check("qualified complete-current action"); assert all(field_gates.values()), field_gates
        with np.load(PINS["frequency_operator"][0],allow_pickle=False) as z: y,b,resistance=legacy.source.base.csc(z,"y")[1:,1:],legacy.source.base.csc(z,"b")[1:,:],legacy.source.base.csc(z,"r"); positive_index,negative_index=(int(z[name][0])-1 for name in ("positive_active_index","negative_active_index"))
        with np.load(PINS["frequency_bridge"][0],allow_pickle=False) as z: u,delta,diagonal=legacy.source.base.csc(z,"l04_u")[1:,:],legacy.source.base.csc(z,"l04_delta")[1:,1:],np.asarray(z["l04_contact_diagonal_admittance_s"])
        def r_only(x):
            answer=np.r_[y@x[:legacy.NV]+b@x[legacy.NV:],b.T@x[:legacy.NV]-resistance@x[legacy.NV:]]; answer[:legacy.NV]+=delta@x[:legacy.NV]; return answer
        returned_contacts=field.dual_potential_v[action.free_cell_count:][action.independent_contacts].copy(); returned_ntd_check={}
        def returned_ntd(g):
            returned_ntd_check["shape"] = bool(g.shape == dg.shape)
            returned_ntd_check["relative"] = relative(g,dg)
            assert returned_ntd_check["shape"] and returned_ntd_check["relative"] <= 2e-8
            return returned_contacts
        electrical=np.r_[sv,si,sc]*legacy.coupled.interface_apply(r_only,u,diagonal,returned_ntd,physical[:legacy.NX],physical[legacy.NX:],legacy.NV)
        save_json(output/"returned-ntd-replay.json",{"program":PROGRAM,"version":VERSION,"status":"RETURNED_CONTACT_NTD_REUSED_ONCE","metrics":returned_ntd_check,"selected_rows":"dual_potential_v[free_cell_count:][independent_contacts]"})
        s,t,_,_,_=joint.cross._load_cross_geometry(); s[-1][2]=joint.cross.L25_Z_M; t[-1][2]=joint.cross.L04_Z_M; matrices=[joint.read_csc(path,prefix) for path,_,prefix in local_paths]; runtime=joint.magnetic.verify_environment(); save_json(output/"runtime-before-fmm.json",{"program":PROGRAM,"version":VERSION,"status":"RUNTIME_VERIFIED_BEFORE_FMM","runtime":runtime}); budget.check("runtime receipt before FMM")
        if budget.receipt()["elapsed_s"]>150:
            save_json(output/"result.json",{"program":PROGRAM,"version":VERSION,"status":"STOP_BEFORE_FMM_INSUFFICIENT_REMAINING_BUDGET","run_released":RUN_RELEASED,"inputs":prov["inputs"],"d2":receipt(d2_path),"m_receipt":receipt(output/"m-existing-receipts-before-acceptance.json"),"factor_release":receipt(output/"pre-fmm-factor-release.json"),"field":receipt(output/"field-before-fmm.json"),"runtime":receipt(output/"runtime-before-fmm.json"),"state_evidence":{"prior_arrays":receipt(PINS["prior_arrays"][0]),"prior_initial":receipt(PINS["prior_initial"][0])},"positive_screen_only":False,"screen_pass":False,"no_extension_after_screen":True}); save_json(output/"final-worker-budget.json",{"status":"FINAL_WORKER_BUDGET_AFTER_RESULT","result":receipt(output/"result.json"),"budget":budget.receipt()}); budget.check("pre-fmm stop serialization"); return
        (f25,f04),calls=joint.joint_action(dq25,q04d,(s,t),matrices,budget); assert f25.shape==dq25.shape and f04.shape==q04d.shape and np.isfinite(f25).all() and np.isfinite(f04).all(); del s,t,matrices; gc.collect()
        raw=output/"raw-two-direction-joint-action-before-projections.npz"; np.savez_compressed(raw,dq25=dq25,q04d=q04d,f25=f25,f04=f04,calls_json=np.asarray(json.dumps(builtin(calls),allow_nan=False))); save_json(output/"raw-two-direction-joint-action-before-projections.json",{"program":PROGRAM,"version":VERSION,"status":"RAW_JOINT_ACTION_BEFORE_PROJECTIONS","artifact":receipt(raw),"calls":calls}); budget.check("raw joint action before Pt projection")
        projection={}
        def p_transpose(force): value,_,gradient=joint.cross.lift_probe.contact_lift_transpose(action,force); projection["gradient"]=gradient; return value
        a2=assemble_ad2(electrical,f25,f04,np.asarray(action.h_scale),p_transpose,action.ct_apply,omega=OMEGA,nv=legacy.NV,nx=legacy.NX,scales=np.r_[sv,si,sc]); gradient=projection["gradient"]; action_gates=dict(field_gates,four_fmm_calls=bool(len(calls)==4),fmm_ier_zero=bool(all(call["ier"]==0 for call in calls)),pt_gradient=bool(gradient<=1e-7),finite_ad2=bool(np.isfinite(a2).all()))
        coeff,r2,fit=fit_columns(r0,np.column_stack((a1,a2))); arrays=output/"two-direction-fit-before-gates.npz"; np.savez_compressed(arrays,r0=r0,adpsi=a1,ad2=a2,r2=r2,d2_scaled=d2,d2_physical=physical,coefficients=coeff,dpsi_saved=dpsi,candidate_base_scaled=baseline_state+coeff[1]*d2,candidate_psi=coeff[0]*dpsi)
        save_json(output/"fit-before-gates.json", {"program":PROGRAM,"version":VERSION,"status":"TWO_DIRECTION_FIT_BEFORE_GATES","artifact":receipt(arrays),"fit":fit,"calls":calls,"gradient":gradient,"field":field.metrics,"field_kcl_relative":field_kcl,"action_gates":action_gates})
        if not (fit["rank2"] and fit["condition_lte_1e6"]):
            save_json(output/"result.json",{"program":PROGRAM,"version":VERSION,"status":"STOP_TWO_DIRECTION_FIT_RANK_OR_CONDITION","run_released":RUN_RELEASED,"inputs":prov["inputs"],"m_checkpoint":receipt(d2_path),"artifact":receipt(arrays),"fit":fit,"screen_pass":False,"positive_screen_only":False,"no_extension_after_screen":True}); save_json(output/"final-worker-budget.json",{"status":"FINAL_WORKER_BUDGET_AFTER_RESULT","result":receipt(output/"result.json"),"budget":budget.receipt()}); budget.check("stop result serialization"); return
        metric=blocks(r0,r2,legacy.NV,legacy.NX,total); saved_full=float(np.linalg.norm(r1))
        voltage_scale=np.r_[sv,si,sc]; delta_voltage=(voltage_scale*(coeff[1]*d2))[:legacy.NV]; initial_voltage=(voltage_scale*baseline_state)[:legacy.NV]; port_delta=active_voltage(delta_voltage,positive_index,legacy.NV)-active_voltage(delta_voltage,negative_index,legacy.NV); port_initial=active_voltage(initial_voltage,positive_index,legacy.NV)-active_voltage(initial_voltage,negative_index,legacy.NV); port_check=bool(np.isfinite(port_initial) and np.isfinite(port_delta))
        gates=dict(action_gates,finite=bool(np.isfinite(r2).all()),fit=bool(fit["rank2"] and fit["condition_lte_1e6"] and fit["orthogonality_relative"]<=2e-8),inverse=m_gates["inverse"],primal_identities=m_gates["primal_identities"],saved_psi_full_lte_80_percent=bool(metric["norms"]["candidate_full"]<=.8*saved_full),port_scalar_finite=port_check); screen_pass=bool(all(gates.values()) and all(metric["performance_gates"].values()))
        result={"program":PROGRAM,"version":VERSION,"status":"UNVALIDATED_TWO_DIRECTION_COMPLETE_CURRENT_SCREEN","run_released":RUN_RELEASED,"inputs":prov["inputs"],"prior": {"raw_info":1,"positive_screen_only":False,"structural_gates":prov["prior_structural_gates"],"original_physical_failures":prov["original_physical_failures"]},"m_checkpoint":receipt(d2_path),"factor_release":receipt(output/"pre-fmm-factor-release.json"),"runtime_before_fmm":receipt(output/"runtime-before-fmm.json"),"returned_ntd_replay":receipt(output/"returned-ntd-replay.json"),"raw_joint_action":receipt(raw),"artifact":receipt(arrays),"fit":fit,"metrics":metric,"gates":gates,"screen_pass":screen_pass,"positive_screen_only":screen_pass,"no_extension_after_screen":True,"raw_z_unvalidated_change_only_from_coeff2_dv":{"positive_index":positive_index,"negative_index":negative_index,"scalar_finite":port_check,"initial":builtin(port_initial),"delta":builtin(port_delta),"candidate":builtin(port_initial+port_delta)},"linear_superposition_only":"Candidate residual is a two-column fit to saved r0; no fresh candidate true operator replay was performed.","scope":"Held conditional complete-current screen. No PSD, error bound, PowerSI, physical, or accuracy acceptance."}; save_json(output/"result.json",result); save_json(output/"final-worker-budget.json",{"status":"FINAL_WORKER_BUDGET_AFTER_RESULT","result":receipt(output/"result.json"),"budget":budget.receipt()}); budget.check("final serialization")
    except BaseException:
        save_json(output/"failure.json",{"program":PROGRAM,"version":VERSION,"status":"UNVALIDATED","failure":traceback.format_exc(),"budget":budget.receipt()}); raise
    finally: gc.collect()


def launch(output: Path) -> None:
    assert RUN_RELEASED and not output.exists() and sha(Path(legacy.counter.__file__)) == joint.PINS["counter"][1]
    availability=legacy.source.available_34gib(); output.mkdir(parents=True); frozen=output/"driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes()); command=[sys.executable,"-B",str(Path(__file__).resolve()),"--native-worker","--output",str(output)]; getter=ctypes.windll.psapi.GetProcessMemoryInfo; getter.argtypes=(ctypes.wintypes.HANDLE,ctypes.POINTER(legacy.counter._MemoryCounters),ctypes.wintypes.DWORD); getter.restype=ctypes.wintypes.BOOL; started=time.monotonic(); private=working=0; reason=guard_failure=None
    with subprocess.Popen(command,stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)) as child:
        try:
            print(f"two-direction complete current: owned PID={child.pid}, {EXTERNAL_SECONDS}s/32GiB",flush=True)
            while child.poll() is None:
                values=legacy.counter._MemoryCounters(); values.cb=ctypes.sizeof(values)
                if getter(int(child._handle),ctypes.byref(values),values.cb): private,working=max(private,int(values.private_usage)),max(working,int(values.working_set))
                elif child.poll() is None: reason="STOP_PROCESS_MEMORY_QUERY"
                if time.monotonic()-started>=EXTERNAL_SECONDS: reason="STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private,working)>int(MEMORY_GIB*2**30): reason="STOP_EXTERNAL_MEMORY_BUDGET"
                if reason: child.kill(); break
                time.sleep(.5)
            code=child.wait(timeout=10)
        except BaseException:
            guard_failure=traceback.format_exc(); reason="STOP_PARENT_GUARD_EXCEPTION"
            if child.poll() is None: child.kill()
            code=child.wait(timeout=10)
    save_json(output/"external-budget.json",{"program":PROGRAM,"version":VERSION,"status":reason or ("COMPLETED_NATIVE_WORKER" if code==0 else "STOP_NATIVE_WORKER_EXIT"),"owned_pid":child.pid,"exit_code":code,"elapsed_s":time.monotonic()-started,"sampled_peak_private_bytes":private,"sampled_peak_working_set_bytes":working,"max_runtime_s":EXTERNAL_SECONDS,"max_memory_bytes":int(MEMORY_GIB*2**30),"driver_sha256":sha(frozen),"worker_command":command,"memory_at_launch":availability,"guard_failure":guard_failure}); raise SystemExit(0 if reason is None and code==0 else 2)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); modes=parser.add_mutually_exclusive_group(required=True); modes.add_argument("--self-check",action="store_true"); modes.add_argument("--preflight",action="store_true"); modes.add_argument("--run",action="store_true"); modes.add_argument("--native-worker",action="store_true",help=argparse.SUPPRESS); parser.add_argument("--output",type=Path); args=parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight: print(json.dumps(builtin(preflight()),indent=2,allow_nan=False))
    elif args.run: assert RUN_RELEASED and args.output is not None; launch(args.output.resolve())
    else: assert RUN_RELEASED and args.output is not None; worker(args.output.resolve())


if __name__ == "__main__": main()
