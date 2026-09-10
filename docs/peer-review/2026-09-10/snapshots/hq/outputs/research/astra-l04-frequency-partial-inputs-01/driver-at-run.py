"""SPD Decap PI Evaluator v0.23.1: partial source-frequency inputs, no board solve."""
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
from scipy import sparse
import prepare_astra_l04_full_contact_circuit_bridge as bridge_helper
import recover_astra_l04_unvalidated_checkpoint as saved

ROOT,R,PROGRAM,VERSION=saved.ROOT,saved.R,saved.PROGRAM,saved.VERSION
RUN_RELEASED=True
EXTERNAL_SECONDS,MEMORY_GIB=60.,2.
MEMORY_COUNTER_SOURCE_SHA=saved.MEMORY_COUNTER_SOURCE_SHA
sha,receipt=saved.sha,saved.receipt
PINS={k:saved.PINS[k] for k in ('bridge','bridge_result')}
PINS.update({
 'bridge_source':(Path(bridge_helper.__file__),'4d283db3256201714011a0c059e91e295ddc9b96b58f59de07324d7a9de3ef0f'),
 'stamps':(R/'astra-native-frequency-stamps-01/frequency-stamps.npz','bf2903c044423ea7429ce907567250528903c53eedddd9fffe3265a2669cba3b'),
 'stamp_result':(R/'astra-native-frequency-stamps-01/receipt.json','4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856'),
})
def pending(): return []

def finite_admittance(frequency,resistance,inductance):
    assert np.isfinite(frequency) and frequency>0
    assert resistance.shape==inductance.shape
    assert np.all(np.isfinite(resistance)) and np.all(resistance>0)
    assert np.all(np.isfinite(inductance)) and np.all(inductance>=0)
    return 1/(resistance+2j*np.pi*frequency*inductance)

def changed_star(bridge,frequency,size):
    select=bridge['l04_subleg_index']>=0
    first=np.r_[bridge['old_hidden_first_active_index'],bridge['first_active_index'][select]]
    second=np.r_[bridge['old_hidden_second_active_index'],bridge['second_active_index'][select]]
    values=np.r_[-finite_admittance(frequency,bridge['old_hidden_resistance_ohm'],bridge['old_hidden_inductance_h']),
                  finite_admittance(frequency,bridge['resistance_ohm'][select],bridge['inductance_h'][select])]
    assert len(first)==30 and np.count_nonzero(select)==20
    matrix=sparse.coo_matrix((np.r_[values,values,-values,-values],
        (np.r_[first,second,first,second],np.r_[first,second,second,first])),shape=(size,size)).tocsc()
    matrix.sum_duplicates();matrix.eliminate_zeros();matrix.sort_indices()
    return matrix

def self_check():
    r=np.array([2.,3.]); l=np.array([1e-8,2e-8])
    for f in (1e6,1e7,1e8):
        y=finite_admittance(f,r,l)
        assert np.all(y.real>0) and np.allclose(1/y,r+2j*np.pi*f*l,rtol=1e-14,atol=0)
    print('PASS_SOURCE_RL_RESTAMP_CHECK')

def worker(output):
    assert {p.name for p in output.iterdir()}=={'driver-at-run.py'}
    assert sha(output/'driver-at-run.py')==sha(Path(__file__))
    started=time.monotonic();budget=saved.ntd.recon._Budget.create(45.,MEMORY_GIB)
    try:
        inputs={k:receipt(p,h) for k,(p,h) in PINS.items()}
        meta=json.loads(PINS['bridge_result'][0].read_bytes())
        stampmeta=json.loads(PINS['stamp_result'][0].read_bytes())
        assert meta['status']=='PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE'
        assert stampmeta['status']=='VERIFIED_NATIVE_FREQUENCY_STAMPS_FROM_SAVED_SOURCE'
        with np.load(PINS['bridge'][0],allow_pickle=False) as z: bridge={k:z[k] for k in z.files}
        with np.load(PINS['stamps'][0],allow_pickle=False) as z:
            frequencies=z['frequencies_hz'];coefficients=z['partial_dispersion_s_per_f_by_frequency'];terms=z['termination_admittance_s_by_frequency']
        assert np.array_equal(frequencies,[1e6,1e7,1e8,1e9])
        assert coefficients.shape==(4,36) and terms.shape==(4,11050)
        assert np.all(np.isfinite(coefficients)) and np.all(np.isfinite(terms))
        size,contacts,root=meta['potential_count'],meta['contacts'],meta['root_contact_index']
        assert (size,contacts,root)==(3178104,38278,25440)
        points=[]
        for index in (0,1,2):
            f=float(frequencies[index])
            y=finite_admittance(f,bridge['resistance_ohm'],bridge['inductance_h'])
            u,d=bridge_helper.coupling(bridge['first_active_index'],bridge['second_active_index'],bridge['contact_index'],bridge['contact_drop_sign'],y,root,contacts,size)
            delta=changed_star(bridge,f,size)
            assert np.all(d.real>0) and np.all(np.isfinite(u.data))
            assert np.array_equal(u.indices,bridge['u_indices']) and np.array_equal(u.indptr,bridge['u_indptr'])
            if index==0:
                assert np.array_equal(u.data,bridge['u_data']) and np.array_equal(d,bridge['contact_diagonal_admittance_s'])
            artifact=output/f'frequency-{int(f)}-partial-inputs.npz'
            np.savez_compressed(artifact,frequency_hz=np.array([f]),source_current_a=np.array([1.]),
                native_partial_dispersion_s_per_f=coefficients[index],native_termination_admittance_s=terms[index],
                l02_partial0_dispersion_s_per_f=np.array([coefficients[index,0]]),
                l04_finite_admittance_s=y,l04_u_data=u.data,l04_u_indices=u.indices,l04_u_indptr=u.indptr,l04_u_shape=np.array(u.shape),
                l04_contact_diagonal_admittance_s=d,l04_delta_data=delta.data,l04_delta_indices=delta.indices,l04_delta_indptr=delta.indptr,l04_delta_shape=np.array(delta.shape))
            ratio=coefficients[index]/coefficients[0]
            points.append({'frequency_hz':f,'artifact':receipt(artifact),'l04_u_nnz':u.nnz,'l04_delta_nnz':delta.nnz,
                'gc_ratio_spread_from_partial0':float(np.max(abs(ratio-ratio[0]))),
                'termination_change_relative_norm':float(np.linalg.norm(terms[index]-terms[0])/np.linalg.norm(terms[0]))})
            budget.check('partial frequency input saved')
        report={'program':PROGRAM,'version':VERSION,'status':'PREPARED_PARTIAL_FREQUENCY_INPUTS_NO_SOLVE',
            'driver':receipt(output/'driver-at-run.py'),'inputs':inputs,'points':points,'geometry_approximation':meta['geometry_approximation'],
            'one_mhz_l04_u_diagonal_bitwise_reproduced':True,'retained_original_numerical_gate':False,
            'remaining_assembly':['L02 cell Schur and contact GC at partial0 coefficient','native finite and composite RL by owner mapping','retained partial GC and L14/L25 owner mass GC','actual termination stamping and complete Y assembly','frequency-specific preconditioner, solve and physical diagnostics'],
            'scope':'Partial inputs only; preserve topology/source ownership through pinned bridge. Actual saved native dispersion/termination values, fixed source R/L. No complete frequency operator, board response, skin/proximity/mutual magnetic addition, solved-field extrapolation or accuracy acceptance.',
            'elapsed_seconds':time.monotonic()-started,'budget':budget.receipt()}
        (output/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps({'status':report['status'],'elapsed_seconds':report['elapsed_seconds'],'frequencies':[p['frequency_hz'] for p in points]}),flush=True)
    except BaseException:
        (output/'failure.json').write_text(json.dumps({'status':'STOP_FREQUENCY_INPUT_PREPARATION','traceback':traceback.format_exc()},indent=2))
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
        print(f"bounded frequency preparation watchdog: owned PID={process.pid},{EXTERNAL_SECONDS}s/{MEMORY_GIB}GiB", flush=True)
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
