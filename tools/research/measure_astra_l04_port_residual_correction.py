"""SPD Decap PI Evaluator v0.23.1: two-state, unvalidated port residual correction."""
from pathlib import Path
import argparse
import ctypes
import ctypes.wintypes
import json
import shutil
import subprocess
import sys
import time
import traceback
import numpy as np
import recover_astra_l04_unvalidated_checkpoint as saved
import solve_astra_l04_full_contact_coupled as coupled
import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import prepare_astra_l04_contact_ntd_action as ntd

ROOT, R, PROGRAM, VERSION = saved.ROOT, saved.R, saved.PROGRAM, saved.VERSION
RUN_RELEASED = True
EXTERNAL_SECONDS, MEMORY_GIB = 150., 8.
MEMORY_COUNTER_SOURCE_SHA = saved.MEMORY_COUNTER_SOURCE_SHA
sha, receipt = saved.sha, saved.receipt
PINS = {**saved.PINS,
    'recovery_source': (Path(saved.__file__), 'c057e25597038594495f7c493db84f5d14741bdc6c4dae38d2140cae39c21daf'),
    'operator': (R/'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz', '5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'),
    'coupled_source': (Path(coupled.__file__), '6f9b6ce6dc22cefdbb5980b47b30b0c06c8e5ab04a4a0840e7738b014d642430'),
    'base_source': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'ntd_source': (Path(ntd.__file__), 'e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8'),
}

def pending():
    return [k for k,(_,h) in PINS.items() if h.startswith('PENDING_')]

def self_check():
    a=np.array([[3+2j, .2-.1j],[.2-.1j,2-.3j]])
    b=np.array([1.,-1.]); x=np.array([.12+.03j,-.3+.11j])
    exact=np.linalg.solve(a,b); e=exact-x; r=b-a@x
    corrected=np.dot(b,x)+np.dot(x,r)
    assert abs(np.dot(b,exact)-corrected-np.dot(e,a@e))<1e-14
    scale=np.array([.3,4.]); xs=x/scale; bs=scale*b; ass=scale[:,None]*a*scale[None,:]
    assert abs(np.dot(xs,bs-ass@xs)-np.dot(x,r))<1e-14
    assert abs(np.vdot(x,r)-np.dot(x,r))>1e-3
    print('PASS_TRANSPOSE_PORT_STATIONARY_IDENTITY_AND_CONGRUENCE')

def worker(output):
    assert {p.name for p in output.iterdir()}=={'driver-at-run.py'}
    assert sha(output/'driver-at-run.py')==sha(Path(__file__))
    budget=ntd.recon._Budget.create(120.,MEMORY_GIB)
    started=time.monotonic()
    try:
        inputs={k:receipt(p,h) for k,(p,h) in PINS.items()}
        result,history,selected,_=saved._verify_solver()
        ntd_inputs,stream,_=ntd.verify_contract()
        with np.load(PINS['operator'][0],allow_pickle=False) as z:
            y=base.csc(z,'y')[1:,1:].tocsc()
            b=base.csc(z,'b')[1:,:].tocsc()
            resistance=base.csc(z,'r')
            assert tuple(int(z[k][0]) for k in ('positive_active_index','negative_active_index','gauge_active_index'))==(2699,2656,0)
        with np.load(PINS['bridge'][0],allow_pickle=False) as z:
            u=base.csc(z,'u')[1:,:]
            delta=coupled.changed_star(z)
            diagonal=z['contact_diagonal_admittance_s']
        nv,ni,nc=3178103,604031,38277; nx=nv+ni
        assert y.shape==(nv,nv) and b.shape==(nv,ni) and resistance.shape==(ni,ni)
        assert u.shape==(nv,nc) and diagonal.shape==(nc,)
        sv=1/np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel()+np.asarray(abs(b).sum(axis=1)).ravel())
        si=1/np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel()+np.asarray(abs(resistance).sum(axis=1)).ravel())
        scales=np.r_[sv,si,np.sqrt(abs(diagonal))]
        assert np.all(np.isfinite(scales)) and np.all(scales>0)
        action=ntd.load_action(stream)
        measurements=[]
        def pair(z): return [float(z.real),float(z.imag)]
        for selected in history[-2:]:
            receipt(Path(selected['field']['path']),selected['field']['sha256'])
            with np.load(selected['field']['path'],allow_pickle=False) as z:
                assert np.array_equal(z['source_current_a'],[1.]) and np.array_equal(z['frequency_hz'],[1e6])
                assert np.array_equal(z['source_positive_negative_gauge_active_indices'],[2699,2656,0])
                voltage=z['active_voltage_v']; q=z['l25_branch_current_a']; g=z['l04_independent_contact_current_into_sheet_a']
            assert voltage.shape==(nv+1,) and q.shape==(ni,) and g.shape==(nc,) and voltage[0]==0
            physical=np.r_[voltage[1:],q,g]
            assert np.all(np.isfinite(physical))
            x=physical/scales; rhs=np.zeros(nx+nc,complex)
            rhs[2698]=sv[2698]; rhs[2655]=-sv[2655]
            budget.check('one true action ready')
            def a_apply(v):
                value=np.r_[y@v[:nv]+b@v[nv:], b.T@v[:nv]-resistance@v[nv:]]
                value[:nv]+=delta@v[:nv]
                return value
            action_start=time.monotonic()
            ax=scales*coupled.interface_apply(a_apply,u,diagonal,action.apply,physical[:nx],physical[nx:],nv)
            action_seconds=time.monotonic()-action_start
            residual=rhs-ax
            relative=float(np.linalg.norm(residual)/np.linalg.norm(rhs))
            assert np.isclose(relative,selected['relative_true_residual'],rtol=1e-8,atol=1e-11)
            raw=complex(np.dot(rhs,x)); correction=complex(np.dot(x,residual)); corrected=raw+correction
            assert abs(raw-complex(voltage[2699]-voltage[2656]))<1e-15
            assert abs(raw-complex(*selected['port_impedance_unvalidated_estimate_ohm']))<1e-15
            measurements.append({'cycle':selected['cycle'],'field':selected['field'],
                'z_raw_ohm':pair(raw),'transpose_residual_correction_ohm':pair(correction),
                'z_stationary_unvalidated_ohm':pair(corrected),'relative_true_residual':relative,
                'true_operator_seconds':action_seconds})
        delta_raw=complex(*measurements[1]['z_raw_ohm'])-complex(*measurements[0]['z_raw_ohm'])
        delta_stationary=complex(*measurements[1]['z_stationary_unvalidated_ohm'])-complex(*measurements[0]['z_stationary_unvalidated_ohm'])
        previous=json.loads(Path(result['inputs']['predecessor_result']['path']).read_bytes())
        receipt(Path(result['inputs']['predecessor_result']['path']), result['inputs']['predecessor_result']['sha256'])
        previous_z=complex(*previous['history'][-1]['port_impedance_unvalidated_estimate_ohm'])
        last_delta=abs(raw-previous_z)
        top=np.argsort(abs(residual[:nv]/sv))[-8:][::-1]
        def pair(z): return [float(z.real),float(z.imag)]
        budget.check('port correction measured')
        report={'program':PROGRAM,'version':VERSION,'status':'DIAGNOSTIC_UNVALIDATED_PORT_RESIDUAL_CORRECTION',
          'driver':receipt(output/'driver-at-run.py'), 'inputs':inputs,'ntd_inputs':ntd_inputs,
          'frequency_hz':1e6,'source_current_a':1.,'source_positive_negative_gauge_active_indices':[2699,2656,0],
          'raw_solver_info':result['lgmres_info'],'algebraically_converged':result['algebraically_converged'],
          'original_numerical_gate':False,'relative_true_residual':relative,
          'z_raw_ohm':pair(raw),'transpose_residual_correction_ohm':pair(correction),'z_stationary_unvalidated_ohm':pair(corrected),
          'correction_abs_ohm':abs(correction),'previous_completed_run_delta_abs_ohm':last_delta,
          'correction_to_previous_delta_ratio':abs(correction)/last_delta,
          'top_potential_rows':(top+1).tolist(),'top_physical_potential_residual_a':[pair(z) for z in (residual[:nv]/sv)[top]],
          'measurements':measurements,'delta_raw_ohm':pair(delta_raw),'delta_stationary_ohm':pair(delta_stationary),
          'stationary_delta_to_raw_delta_ratio':abs(delta_stationary)/abs(delta_raw),
          'true_operator_calls':2,'true_operator_seconds':sum(m['true_operator_seconds'] for m in measurements),'elapsed_seconds':time.monotonic()-started,'budget':budget.receipt(),
          'scope':'Two original true-operator actions on completed restart3/4, no Krylov or preconditioner factor. Ordinary transpose stationary correction only; no bound on e.T A e, no acceptance, no physical gate clearance, no PowerSI access or fitting.'}
        (output/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('status','relative_true_residual','transpose_residual_correction_ohm','correction_to_previous_delta_ratio','elapsed_seconds')}),flush=True)
    except BaseException:
        (output/'failure.json').write_text(json.dumps({'status':'STOP_PORT_CORRECTION_DIAGNOSTIC','traceback':traceback.format_exc()},indent=2))
        raise

def launch(output: Path) -> int:
    assert not pending(), f"unfilled pins: {pending()}"
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
        print(f"bounded port diagnostic watchdog: owned PID={process.pid},{EXTERNAL_SECONDS}s/8GiB", flush=True)
        while process.poll() is None:
            counters = counter._MemoryCounters(); counters.cb = ctypes.sizeof(counters)
            if not getter(int(process._handle), ctypes.byref(counters), counters.cb):
                if process.poll() is None: reason = "STOP_PROCESS_MEMORY_QUERY"
            else:
                private = max(private, int(counters.private_usage)); working = max(working, int(counters.working_set))
            if time.monotonic() - started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
            if max(private, working) > int(MEMORY_GIB * 2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
            if reason:
                process.kill(); break
            time.sleep(.5)
        code = process.wait(timeout=10)
    status = reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT")
    (output / "external-budget.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
        "status": status, "exit_code": code, "driver_sha256": sha(frozen),
        "external_seconds": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30),
        "printed_memory_cap_gib": MEMORY_GIB, "sampled_peak_private_bytes": private,
        "sampled_peak_working_set_bytes": working, "sampling_interval_s": .5}, indent=2), encoding="utf-8")
    return 0 if status == "COMPLETED_NATIVE_WORKER" and code == 0 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--native-worker", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check(); return
    assert RUN_RELEASED, "RUN_RELEASED remains false pending HQ review"
    assert args.output is not None
    if args.native_worker:
        worker(args.output.resolve())
    else:
        raise SystemExit(launch(args.output.resolve()))


if __name__ == "__main__":
    main()
