"""SPD Decap PI Evaluator v0.23.1: held three-column complete-current screen."""
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
import probe_astra_l04_10mhz_two_direction_complete_current as two  # noqa: E402
import probe_astra_l25_l04_joint_magnetic_action as joint  # noqa: E402

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 480.0, 540.0, 32.0
OMEGA = 2 * pi * 10_000_000.0
RESEARCH = ROOT / "outputs/research"
PRIOR = RESEARCH / "astra-l04-10mhz-closed-current-direction-01"
TWO = RESEARCH / "astra-l04-10mhz-two-direction-complete-current-01"
PHYSICAL = RESEARCH / "astra-l04-10mhz-l25-magnetic-physical-diagnostic-01"
PINS = {
    "two_result": (TWO / "result.json", "3239c406102b43a1ae413190cf40e375b54c27083a66b1e135a0ff9882fbc306"),
    "two_guard": (TWO / "external-budget.json", "4cc044d0ae8c5c4237cbf373a1859878f0a5d16b7c2e5ca7ccccac8779e64f5f"),
    "two_arrays": (TWO / "two-direction-fit-before-gates.npz", "1ca4ee00d75da9fdd0dafb90d0de524a4a70ca81bf65734bc2b3e275758570ac"),
    "two_driver": (TWO / "driver-at-run.py", "6e7466c04d38c4db2d49bfbe6a73e07edb4e418fa582f58b3b192c47aa5c42b0"),
    "two_imported_source": (Path(two.__file__), "6e7466c04d38c4db2d49bfbe6a73e07edb4e418fa582f58b3b192c47aa5c42b0"),
    "tradeoff": (TWO / "hq-saved-two-direction-tradeoff.json", "1215f855227e379d0dbd4e807c28ab668e867d90c6376c16bfa8c60ba4079e6a"),
    "magnetic_physical_diagnostic": (PHYSICAL / "physical-diagnostic.json", "16820a21d61a4c1440bb3d7a2d961e4165677ac3f41dbf4046eebe5845e4cf48"),
    "frequency_operator": legacy.source.PINS["operator"], "frequency_bridge": legacy.source.PINS["bridge"],
    "static": legacy.source.PINS["static"], "cached_factors": legacy.source.PINS["cache_factors"],
}

def preflight() -> dict:
    inputs = {name: two.receipt(path, digest) for name, (path, digest) in PINS.items()}
    result = json.loads(PINS["two_result"][0].read_bytes()); guard = json.loads(PINS["two_guard"][0].read_bytes())
    tradeoff = json.loads(PINS["tradeoff"][0].read_bytes()); diagnostic = json.loads(PINS["magnetic_physical_diagnostic"][0].read_bytes())
    two_chain = two.preflight()
    assert result["artifact"]["sha256"] == PINS["two_arrays"][1] and guard["driver_sha256"] == PINS["two_driver"][1]
    assert result["screen_pass"] is False and result["prior"]["raw_info"] == 1 and all(result["gates"].values())
    assert guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0
    assert len(result["prior"]["structural_gates"]) == 13 and all(result["prior"]["structural_gates"].values())
    actual_failures = sorted(name for name, ok in diagnostic["gates"].items() if not ok)
    expected = ["l25_constitutive", "matrix_global_circuit_kcl", "matrix_identity_power", "physical_power_closure", "physical_source_global_circuit_kcl"]
    assert actual_failures == expected and tradeoff["result_sha256"] == PINS["two_result"][1] and tradeoff["artifact_sha256"] == PINS["two_arrays"][1]
    assert diagnostic["status"] == "DIAGNOSTIC_UNVALIDATED_L25_MAGNETIC_FULL_CONTACT_L04_CHECKPOINT" and len(diagnostic["gates"]) == 23
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_HELD_THREE_DIRECTION_PREFLIGHT", "run_released": RUN_RELEASED, "inputs": inputs, "two_helper_preflight": two_chain, "two_column_screen_pass": False, "raw_gcrotmk_info": 1, "actual_magnetic_physical_failures": actual_failures, "old_inherited_failure_list": sorted(legacy.source.FAILED_PHYSICAL_GATES), "old_inherited_failure_list_status": "stale; actual magnetic physical diagnostic above is authoritative", "scope": "Receipt-only held preflight; no M factor, H, FMM, or board action."}


def fit3(r0, columns):
    raw = np.linalg.norm(columns, axis=0); assert np.all(np.isfinite(raw)) and np.all(raw > 0)
    normal = columns / raw; cn, _, rank, singular = np.linalg.lstsq(normal, r0, rcond=1e-12); coeff = cn/raw; residual = r0-columns@coeff
    condition = float(singular[0]/max(singular[-1], np.finfo(float).tiny)); orth = float(np.linalg.norm(normal.conj().T@residual)/max(np.linalg.norm(normal)*np.linalg.norm(residual),np.finfo(float).tiny))
    return coeff, residual, {"raw_column_norms":raw,"normalized_column_norms":np.linalg.norm(normal,axis=0),"singular_values":singular,"rank":int(rank),"normalized_condition":condition,"rank3":bool(rank==3),"condition_lte_1e6":bool(condition<=1e6),"orthogonality_relative":orth}


def assemble_ad3(electrical_scaled, f25, f04, h_scale, p_transpose, c_transpose, pt_rcpsi, ct_r_q04, *, omega, nv, nx, scales):
    """Assemble all electrical and magnetic rows for the third current direction."""
    result = two.assemble_ad2(electrical_scaled, f25, f04, h_scale, p_transpose, c_transpose, omega=omega, nv=nv, nx=nx, scales=scales)
    total = len(electrical_scaled)
    result[nx:total] -= scales[nx:total] * pt_rcpsi
    result[total:] -= h_scale * ct_r_q04
    return result


def self_check() -> None:
    # Independent physical [v,q25,g,psi] assembly: q04=P*g+C*psi has two branches.
    omega=7.; S=np.array([2.,3.,5.,7.]); P=np.array([[1.],[-.4]]); C=np.array([[.3],[.7]])
    R=np.array([[2.,.25],[.25,1.5]],complex); L=np.array([[.8,.1,-.2],[.1,.9,.3],[-.2,.3,.7]],complex)
    base=np.array([[4.,.2,-.3,0.],[.2,5.,.4,0.],[-.3,.4,3.,0.],[0.,0.,0.,0.]],complex)
    base[2,2]-=(P.T@R@P)[0,0]; base[2,3]-=(P.T@R@C)[0,0]; base[3,2]=base[2,3]; base[3,3]-=(C.T@R@C)[0,0]
    T=np.array([[0.,1.,0.,0.],[0.,0.,P[0,0],C[0,0]],[0.,0.,P[1,0],C[1,0]]]); complete=base-1j*omega*T.T@L@T
    assert np.allclose(base,base.T) and np.allclose(complete,complete.T)
    d1=np.array([0j,0j,0j,.8-.2j]); d2=np.array([.3+.1j,-.4+.2j,.6-.5j,0j]); d3=np.array([.1-.3j,.4+.2j,-.2+.1j,.6+.1j]); physical=S*d3; q04=P*physical[2]+C*physical[3]; flux=L@np.r_[physical[1],q04[:,0]]
    electrical=S[:3]*(base[:3,:3]@physical[:3]); pt_rc=np.asarray(P.T@R@(C*physical[3])).ravel(); ct_r_q04=np.asarray(C.T@R@q04).ravel()
    got=assemble_ad3(electrical,np.array([flux[0]]),flux[1:],np.array([S[3]]),lambda force:np.asarray(P.T@force).ravel(),lambda force:np.asarray(C.T@force).ravel(),pt_rc,ct_r_q04,omega=omega,nv=1,nx=2,scales=S[:3])
    A=np.diag(S)@complete@np.diag(S); assert np.allclose(got,A@d3)
    psi_physical=S[3]*d3[3]; assert np.allclose(psi_physical/S[3],d3[3])
    columns=np.column_stack((A@d1,A@d2,A@d3)); witness=np.array([.1j,-.2,.3j,.1]); orthogonal=witness-columns@np.linalg.lstsq(columns,witness,rcond=1e-12)[0]
    r0=(1.2-.4j)*columns[:,0]+(-.7+.9j)*columns[:,1]+(.2+.6j)*columns[:,2]+orthogonal
    coeff,residual,fit=fit3(r0,columns); assert fit["rank3"] and fit["condition_lte_1e6"] and fit["orthogonality_relative"]<1e-10
    port=np.array([2+3j]); assert two.active_voltage(port,0,1)==2+3j and two.active_voltage(port,-1,1)==0j
    try: two.active_voltage(port,1,1)
    except AssertionError: pass
    else: raise AssertionError("invalid scalar port index")
    json.dumps(two.builtin({"coeff":coeff,"gates":{"ok":np.bool_(True)}}),allow_nan=False); print(f"{PROGRAM} v{VERSION}: PASS_HELD_THREE_DIRECTION_SELF_CHECK")


def _two_arrays():
    with np.load(PINS["two_arrays"][0], allow_pickle=False) as z:
        r0,a1,a2,d2,dpsi=(np.asarray(z[name],complex) for name in ("r0","adpsi","ad2","d2_scaled","dpsi_saved"))
    _,_,_,_,_,sv,si,sc,baseline = two._prior_arrays()
    return r0,a1,a2,d2,dpsi,baseline,sv,si,sc

def worker(output: Path) -> None:
    budget=joint.ntd.recon._Budget.create(INTERNAL_SECONDS,MEMORY_GIB)
    try:
        assert (output/"driver-at-run.py").read_bytes()==Path(__file__).read_bytes(); prov=preflight()
        r0,a1,a2,d2_saved,dpsi_saved,baseline,sv,si,sc=_two_arrays(); total=len(sv)+len(si)+len(sc)
        trade=json.loads(PINS["tradeoff"][0].read_bytes()); c1,c2=(complex(*pair) for pair in trade["coefficients"])
        r_boundary=r0-c1*a1-c2*a2; closed=float(np.linalg.norm(r_boundary[total:])); full=float(np.linalg.norm(r_boundary))
        assert abs(closed-.003854035501174978)<=2e-12 and abs(full-11.28042408214729)<=2e-9
        dbase, m_receipts, resources=two._one_r_direction(r_boundary[:total],sv,si,sc,output,budget); assert dbase.shape==(total,)
        direction=output/"third-boundary-m-direction-before-h.npz"; np.savez_compressed(direction,r_boundary=r_boundary,dbase_scaled=dbase,dbase_physical=np.r_[sv,si,sc]*dbase)
        m_gates={"inverse":bool(all(x<=2e-8 for x in m_receipts["inverse_relative_max"].values())),"primal_identities":bool(all(m_receipts["identities"][n]<=m_receipts["identities"]["tolerance"] for n in ("k","q","r")))}
        two.save_json(output/"third-m-before-acceptance.json",{"program":PROGRAM,"version":VERSION,"status":"ONE_R_BOUNDARY_DIRECTION","artifact":two.receipt(direction),"m":m_receipts,"gates":m_gates}); budget.check("third M checkpoint")
        assert all(m_gates.values()),m_gates
        before=joint.ntd.recon._rss_bytes(); del resources; gc.collect(); after=joint.ntd.recon._rss_bytes(); phase={"elapsed_s":budget.receipt()["elapsed_s"],"rss_before_release":before,"rss_after_release":after,"rss_lte_8_gib":bool(after<=8*2**30),"elapsed_lte_150":bool(budget.receipt()["elapsed_s"]<=150)}; two.save_json(output/"third-factor-release.json",phase)
        if not all((phase["rss_lte_8_gib"],phase["elapsed_lte_150"])): _stop(output,budget,prov,"STOP_BEFORE_FMM_INSUFFICIENT_REMAINING_BUDGET",direction); return
        inherited,local_paths=joint.preflight(); action=joint.ntd.load_action(inherited["qualified_stream_result"]); budget.check("qualified H")
        scales=np.r_[sv,si,sc]; physical=scales*dbase; dq25=physical[legacy.NV:legacy.NX]; dg=physical[legacy.NX:]
        field=action.apply(dg,return_field=True); cpsi_rhs=r_boundary[total:]/action.h_scale; dpsi=-action.solve_h(cpsi_rhs); cpsi=action.c_apply(dpsi); q04=field.branch_current_a+cpsi
        rq_cpsi=action.resistance@cpsi; rq_q04=action.resistance@q04; h_dpsi=action.ct_apply(rq_cpsi); ct_r_q04=action.ct_apply(rq_q04)
        bq=two.relative(action.b_apply(q04),field.target_bq_a); h_identity=two.relative(h_dpsi,-cpsi_rhs); pt_rc,projected,pt_gradient=joint.cross.lift_probe.contact_lift_transpose(action,rq_cpsi); rc=max(np.linalg.norm(rq_cpsi),np.finfo(float).tiny)
        field_gates={"field_shape":bool(field.branch_current_a.shape==action.first.shape),"field_finite":bool(np.isfinite(field.branch_current_a).all() and np.isfinite(field.dual_potential_v).all()),"field_kcl":bool(two.relative(action.b_apply(field.branch_current_a),field.target_bq_a)<=1e-7),"field_stationarity":bool(field.metrics["energy_scaled_stationarity_relative"]<=1e-7),"field_dual":bool(field.metrics["dual_rq_relative"]<=1e-7),"bq_cpsi":bool(bq<=1e-7),"h_rhs":bool(h_identity<=2e-8),"pt_rcpsi":bool(np.linalg.norm(pt_rc)/rc<=2e-8),"projected_rcpsi":bool(np.linalg.norm(projected)/rc<=2e-8),"finite_direction":bool(np.isfinite(dpsi).all() and np.isfinite(q04).all())}
        two.save_json(output/"third-h-field-before-fmm.json",{"program":PROGRAM,"version":VERSION,"status":"THIRD_DIRECTION_H_AND_FIELD","field":field.metrics,"metrics":{"bq":bq,"h_rhs":h_identity,"pt_rcpsi":float(np.linalg.norm(pt_rc)/rc),"projected_rcpsi":float(np.linalg.norm(projected)/rc),"pt_gradient":pt_gradient},"gates":field_gates}); budget.check("third H field receipt"); assert all(field_gates.values()),field_gates
        with np.load(PINS["frequency_operator"][0],allow_pickle=False) as z:
            y,b,resistance=legacy.source.base.csc(z,"y")[1:,1:],legacy.source.base.csc(z,"b")[1:,:],legacy.source.base.csc(z,"r"); positive_index,negative_index=(int(z[n][0])-1 for n in ("positive_active_index","negative_active_index"))
        with np.load(PINS["frequency_bridge"][0],allow_pickle=False) as z: u,delta,diagonal=legacy.source.base.csc(z,"l04_u")[1:,:],legacy.source.base.csc(z,"l04_delta")[1:,1:],np.asarray(z["l04_contact_diagonal_admittance_s"])
        def r_only(x):
            out=np.r_[y@x[:legacy.NV]+b@x[legacy.NV:],b.T@x[:legacy.NV]-resistance@x[legacy.NV:]]; out[:legacy.NV]+=delta@x[:legacy.NV]; return out
        cached=field.dual_potential_v[action.free_cell_count:][action.independent_contacts].copy(); replay={}
        def ntd(g): replay["relative"]=two.relative(g,dg); assert g.shape==dg.shape and replay["relative"]<=2e-8; return cached
        electrical=scales*legacy.coupled.interface_apply(r_only,u,diagonal,ntd,physical[:legacy.NX],dg,legacy.NV)
        two.save_json(output/"third-returned-ntd-replay.json",{"relative":replay.get("relative"),"selected_rows":"dual_potential_v[free_cell_count:][independent_contacts]"})
        s,t,_,_,_=joint.cross._load_cross_geometry(); s[-1][2]=joint.cross.L25_Z_M; t[-1][2]=joint.cross.L04_Z_M; matrices=[joint.read_csc(path,prefix) for path,_,prefix in local_paths]; runtime=joint.magnetic.verify_environment(); two.save_json(output/"third-runtime-before-fmm.json",{"runtime":runtime}); budget.check("third runtime before action")
        if budget.receipt()["elapsed_s"]>150: _stop(output,budget,prov,"STOP_BEFORE_FMM_INSUFFICIENT_REMAINING_BUDGET",direction); return
        (f25,f04),calls=joint.joint_action(dq25,q04,(s,t),matrices,budget); assert len(calls)==4 and f25.shape==dq25.shape and f04.shape==q04.shape and np.isfinite(f25).all() and np.isfinite(f04).all(); del s,t,matrices; gc.collect()
        raw=output/"raw-third-joint-action-before-projections.npz"; np.savez_compressed(raw,dq25=dq25,q04=q04,dpsi=dpsi,cpsi=cpsi,f25=f25,f04=f04,calls_json=np.asarray(json.dumps(two.builtin(calls),allow_nan=False))); two.save_json(output/"raw-third-joint-action-before-projections.json",{"artifact":two.receipt(raw),"calls":calls}); budget.check("third raw action")
        projection={}
        def pt(force): value,_,gradient=joint.cross.lift_probe.contact_lift_transpose(action,force); projection["gradient"]=gradient; return value
        a3=assemble_ad3(electrical,f25,f04,np.asarray(action.h_scale),pt,action.ct_apply,pt_rc,ct_r_q04,omega=OMEGA,nv=legacy.NV,nx=legacy.NX,scales=scales)
        coeff,r3,fit=fit3(r0,np.column_stack((a1,a2,a3))); arrays=output/"three-direction-fit-before-gates.npz"; np.savez_compressed(arrays,r0=r0,adpsi=a1,ad2=a2,ad3=a3,r3=r3,dbase_scaled=dbase,dpsi=dpsi,coefficients=coeff,candidate_base_scaled=baseline+coeff[1]*d2_saved+coeff[2]*dbase,candidate_psi=coeff[0]*dpsi_saved+coeff[2]*dpsi)
        action_gates=dict(field_gates,four_fmm_calls=bool(len(calls)==4),fmm_ier_zero=bool(all(x["ier"]==0 for x in calls)),pt_gradient=bool(projection["gradient"]<=1e-7),finite_ad3=bool(np.isfinite(a3).all()),fit=bool(fit["rank3"] and fit["condition_lte_1e6"] and fit["orthogonality_relative"]<=2e-8))
        two.save_json(output/"three-fit-before-gates.json",{"artifact":two.receipt(arrays),"fit":fit,"gates":action_gates,"calls":calls})
        if not (fit["rank3"] and fit["condition_lte_1e6"]): _stop(output,budget,prov,"STOP_THREE_DIRECTION_FIT_RANK_OR_CONDITION",arrays); return
        metrics=two.blocks(r0,r3,legacy.NV,legacy.NX,total); voltage=scales[:legacy.NV]; delta_v=(voltage*(coeff[1]*d2_saved[:legacy.NV]+coeff[2]*dbase[:legacy.NV])); initial_v=(voltage*baseline[:legacy.NV]); port_delta=two.active_voltage(delta_v,positive_index,legacy.NV)-two.active_voltage(delta_v,negative_index,legacy.NV); port_initial=two.active_voltage(initial_v,positive_index,legacy.NV)-two.active_voltage(initial_v,negative_index,legacy.NV); port_ok=bool(np.isfinite(port_delta) and np.isfinite(port_initial))
        gates=dict(action_gates,inverse=m_gates["inverse"],primal_identities=m_gates["primal_identities"],port_scalar_finite=port_ok,incremental_full_lte_80_percent=bool(np.linalg.norm(r3)<=1.9498975084191156)); screen_pass=bool(all(gates.values()) and all(metrics["performance_gates"].values()))
        result={"program":PROGRAM,"version":VERSION,"status":"UNVALIDATED_THREE_DIRECTION_COMPLETE_CURRENT_SCREEN","run_released":RUN_RELEASED,"inputs":prov["inputs"],"raw_gcrotmk_info":1,"actual_magnetic_physical_failures":prov["actual_magnetic_physical_failures"],"old_inherited_failure_list":prov["old_inherited_failure_list"],"artifact":two.receipt(arrays),"raw_action":two.receipt(raw),"fit":fit,"metrics":metrics,"gates":gates,"screen_pass":screen_pass,"positive_screen_only":screen_pass,"no_extension_after_screen":True,"raw_z_unvalidated_change_only_from_c2_c3":{"initial":two.builtin(port_initial),"delta":two.builtin(port_delta),"candidate":two.builtin(port_initial+port_delta),"positive_index":positive_index,"negative_index":negative_index},"linear_superposition_only":"Candidate is a three-column fit to saved r0; no fresh candidate operator replay.","scope":"Held third conditional complete-current direction; no convergence, physical, accuracy, PSD, error-bound, or PowerSI acceptance."}
        two.save_json(output/"result.json",result); two.save_json(output/"final-worker-budget.json",{"status":"FINAL_WORKER_BUDGET_AFTER_RESULT","result":two.receipt(output/"result.json"),"budget":budget.receipt()}); budget.check("third final serialization")
    except BaseException:
        two.save_json(output/"failure.json",{"program":PROGRAM,"version":VERSION,"status":"UNVALIDATED","failure":traceback.format_exc(),"budget":budget.receipt()}); raise
    finally: gc.collect()


def _stop(output,budget,prov,status,artifact):
    two.save_json(output/"result.json",{"program":PROGRAM,"version":VERSION,"status":status,"run_released":RUN_RELEASED,"inputs":prov["inputs"],"artifact":two.receipt(artifact),"screen_pass":False,"positive_screen_only":False,"no_extension_after_screen":True}); two.save_json(output/"final-worker-budget.json",{"status":"FINAL_WORKER_BUDGET_AFTER_RESULT","result":two.receipt(output/"result.json"),"budget":budget.receipt()}); budget.check("stop serialization")

def launch(output: Path) -> None:
    assert RUN_RELEASED and not output.exists() and two.sha(Path(legacy.counter.__file__)) == joint.PINS["counter"][1]
    availability=legacy.source.available_34gib(); output.mkdir(parents=True); frozen=output/"driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes()); command=[sys.executable,"-B",str(Path(__file__).resolve()),"--native-worker","--output",str(output)]; getter=ctypes.windll.psapi.GetProcessMemoryInfo; getter.argtypes=(ctypes.wintypes.HANDLE,ctypes.POINTER(legacy.counter._MemoryCounters),ctypes.wintypes.DWORD); getter.restype=ctypes.wintypes.BOOL; started=time.monotonic(); private=working=0; reason=guard_failure=None
    with subprocess.Popen(command,stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)) as child:
        try:
            print(f"three-direction complete current: owned PID={child.pid}, {EXTERNAL_SECONDS}s/32GiB",flush=True)
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
    two.save_json(output/"external-budget.json",{"program":PROGRAM,"version":VERSION,"status":reason or ("COMPLETED_NATIVE_WORKER" if code==0 else "STOP_NATIVE_WORKER_EXIT"),"owned_pid":child.pid,"exit_code":code,"elapsed_s":time.monotonic()-started,"sampled_peak_private_bytes":private,"sampled_peak_working_set_bytes":working,"max_runtime_s":EXTERNAL_SECONDS,"max_memory_bytes":int(MEMORY_GIB*2**30),"driver_sha256":two.sha(frozen),"worker_command":command,"memory_at_launch":availability,"guard_failure":guard_failure}); raise SystemExit(0 if reason is None and code==0 else 2)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); modes=parser.add_mutually_exclusive_group(required=True); modes.add_argument("--self-check",action="store_true"); modes.add_argument("--preflight",action="store_true"); modes.add_argument("--run",action="store_true"); modes.add_argument("--native-worker",action="store_true",help=argparse.SUPPRESS); parser.add_argument("--output",type=Path); args=parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight: print(json.dumps(two.builtin(preflight()),indent=2,allow_nan=False))
    elif args.run: assert RUN_RELEASED and args.output is not None; launch(args.output.resolve())
    else: assert RUN_RELEASED and args.output is not None; worker(args.output.resolve())


if __name__ == "__main__": main()
