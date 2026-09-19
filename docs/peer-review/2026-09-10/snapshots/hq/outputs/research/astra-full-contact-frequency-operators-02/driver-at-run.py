"""SPD Decap PI Evaluator v0.23.1: source-frequency R/GC operators, no solve."""
from pathlib import Path
from collections import ChainMap
import argparse
import ctypes
import ctypes.wintypes
import gc
import json
import shutil
import subprocess
import sys
import time
import traceback
import numpy as np
from scipy import sparse
import assemble_astra_l02_conditional_hybrid_operator as cells
import reconstruct_astra_native_loaded_field as recon
import run_astra_l14_sheet_r_shadow as l14
import recover_astra_l04_unvalidated_checkpoint as saved

ROOT,R,PROGRAM,VERSION=saved.ROOT,saved.R,saved.PROGRAM,saved.VERSION
RUN_RELEASED=True
EXTERNAL_SECONDS,MEMORY_GIB=180.,5.
MEMORY_COUNTER_SOURCE_SHA=saved.MEMORY_COUNTER_SOURCE_SHA
sha,receipt=saved.sha,saved.receipt
PINS={
 'l14_source':(Path(l14.__file__),'ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803'),
 'inventory14':(R/'astra-native-loaded-vtrip-field-02/l14-gc-projection-inventory.json','4409cdccc3b3d488d8f3abdd2f1e8b09a488d3c18a5bace010c765a240bc397f'),
 'inventory25':(R/'astra-l25-source-sheet-01/l25-gc-projection-inventory.json','703e8cdf9988805a2fb05a40ff659bc9890ba999e2eef00c741a112aa5976a77'),
 'raw':(R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz','6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7'),
 'derived':(R/'astra-native-loaded-vtrip-field-02/derived-field-observation.npz','be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0'),
 'pack':cells.PINS['pack'],
 'binding':(R/'astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz','61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020'),
 'stamps':(R/'astra-native-frequency-stamps-01/frequency-stamps.npz','bf2903c044423ea7429ce907567250528903c53eedddd9fffe3265a2669cba3b'),
 'old_operator':(R/'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz','5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'),
 'cell_result':(R/'astra-l02-frequency-cell-operators-01/result.json','e1f31aa4ee180f2fe1a6969accb663af16b3c0a61625b694860a6e448a2261b6'),
 'cell_guard':(R/'astra-l02-frequency-cell-operators-01/external-budget.json','e69c1c6ae93233f19013eabb1732a0182fc22277c7f694c6439dbf39cb210641'),
 'bridge_frequency_result':(R/'astra-l04-frequency-partial-inputs-01/result.json','a0117fe7cfe66646ac09d2450ed2be283e7754bce728dc8c33d9916c4b2d2958'),
 'cells_source':(Path(cells.__file__),'79ab74ff53bef21347038a8ab0c243260049cd97dfea5e707320f84de19210d3'),
 'recon_source':(Path(recon.__file__),'354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}
N=3178104
L02_WHOLE_PARTIAL=0
def pending():return []

def difference(a,b):
    delta=a-b
    return float(np.max(abs(delta.data),initial=0)/max(np.max(abs(b.data),initial=0),np.finfo(float).tiny))

def category_ratio(target,baseline,partials):
    ratio=target[partials]/baseline[partials]
    spread=float(np.max(abs(ratio-ratio[0])))
    assert spread<=64*np.finfo(float).eps*abs(ratio[0]), 'partial-specific reconstruction required'
    return complex(ratio[0]),spread

def self_check():
    assert category_ratio(np.array([2+4j,6+12j]),np.array([1+2j,3+6j]),[0,1])[0]==2
    try: category_ratio(np.array([2+4j,9+18j]),np.array([1+2j,3+6j]),[0,1])
    except AssertionError:pass
    else:raise AssertionError('nonuniform dispersion must reject whole-category scaling')
    print('PASS_PARTIAL_DISPERSION_GROUP_REJECTION')

def worker(output):
    assert {p.name for p in output.iterdir()}=={'driver-at-run.py'}
    assert sha(output/'driver-at-run.py')==sha(Path(__file__))
    started=time.monotonic();budget=recon._Budget.create(165.,MEMORY_GIB)
    try:
        inputs={k:receipt(p,h) for k,(p,h) in PINS.items()}
        inventory14=json.loads(PINS['inventory14'][0].read_bytes());inventory25=json.loads(PINS['inventory25'][0].read_bytes())
        cell_result=json.loads(PINS['cell_result'][0].read_bytes());guard=json.loads(PINS['cell_guard'][0].read_bytes())
        assert cell_result['status']=='ASSEMBLED_L02_FREQUENCY_CELL_OPERATORS_NO_BOARD_SOLVE'
        assert guard['status']=='COMPLETED_NATIVE_WORKER' and guard['exit_code']==0 and guard['driver_sha256']==cell_result['driver']['sha256']
        bridges=json.loads(PINS['bridge_frequency_result'][0].read_bytes())
        assert bridges['status']=='PREPARED_PARTIAL_FREQUENCY_INPUTS_NO_SOLVE'
        with np.load(PINS['old_operator'][0],allow_pickle=False) as z:
            metadata={k:z[k] for k in ('conditional_global_active_index','conditional_to_full_face_transfer_row_index','gauge_active_index','positive_active_index','negative_active_index')}
            rb={};cells.save_csc(rb,'r',cells.read_csc(z,'r'));cells.save_csc(rb,'b',cells.read_csc(z,'b'))
        with np.load(PINS['pack'][0],allow_pickle=False) as z:
            first=z['finite_first_active_index'];second=z['finite_second_active_index'];original_y=z['finite_admittance_s'];owner=z['final_finite_native_active_row'];leg=z['final_finite_split_leg']
            category_names=json.loads(z['category_names_json_utf8'].tobytes())
            categories={name:cells.extend_square(cells.read_csc(z,'category_'+name),N) for name in category_names}
        assert len(first)==len(second)==len(original_y)==len(owner)==len(leg)==1692409
        assert set(category_names)=={'retained_gc','termination','l14_sheet_dc','l14_distributed_gc','l25_distributed_gc'}
        with np.load(PINS['binding'][0],allow_pickle=False) as z: junctions=json.loads(z['junctions_json_utf8'].tobytes())
        assert len(junctions)==20
        with np.load(PINS['derived'][0],allow_pickle=False) as z:
            tp=z['termination_positive_active_indices'];tn=z['termination_negative_active_indices'];old_term=z['termination_admittance_s']
        with np.load(PINS['stamps'][0],allow_pickle=False) as z:
            frequencies=z['frequencies_hz'];coefficients=z['partial_dispersion_s_per_f_by_frequency'];terms=z['termination_admittance_s_by_frequency']
        assert np.array_equal(frequencies,[1e6,1e7,1e8,1e9]) and terms.shape==(4,11050)
        with np.load(PINS['raw'][0],allow_pickle=False) as raw:
            source_r=raw['finite_resistance_ohm_per_via'];source_l=raw['finite_inductance_h_per_via'];source_count=raw['finite_count']
            assert np.all((owner>=0)&(owner<len(source_r)))
            resistance=source_r[owner].copy();inductance=source_l[owner].copy();counts=source_count[owner].copy()
            replaced=[]
            for junction in junctions:
                for k,prefix in ((0,'first'),(1,'second')):
                    found=np.flatnonzero((owner==junction['replaced_active_finite_index'])&(leg==k))
                    assert len(found)==1
                    i=int(found[0]);replaced.append(i)
                    resistance[i]=junction[prefix+'_leg_resistance_ohm'];inductance[i]=junction[prefix+'_leg_inductance_h'];counts[i]=1.
            assert set(replaced)==set(np.flatnonzero(leg>=0).tolist()) and len(replaced)==40
            assert np.all(resistance>0) and np.all(inductance>=0) and np.all(counts>0)
            baseline=raw['partial_actual_1mhz_dispersion_admittance_scale_s']
            names=recon._decode_text_vector(raw['surface_node_ids'],'surface')
            lookup={name:i for i,name in enumerate(names)}
            surface_to_reduced=raw['surface_to_reduced_indices'];global_to_active=raw['global_to_active_indices']
            results=[];control={}
            for index in (0,1,2):
                f=float(frequencies[index]);current_coeff=baseline.copy() if index==0 else coefficients[index].copy()
                retained_coeff=current_coeff.copy();retained_coeff[L02_WHOLE_PARTIAL]=0
                retained,_=l14.retained_partials(raw,inventory14,lookup,global_to_active[surface_to_reduced],N,coefficients=retained_coeff)
                removed25=0
                for partial in inventory25['original_gc']['partials']:
                    assert int(partial['ordinal']) in (13,14)
                    owners=partial['owners'];removed25+=len(owners)
                    capacitance=np.array([float.fromhex(o['nominal_capacitance_f_hex']) for o in owners])
                    external=np.array([o['external_active_index'] for o in owners])
                    retained-=cells.branch_laplacian(np.full(len(owners),258027),external,capacitance*current_coeff[int(partial['ordinal'])],N)
                assert removed25==4
                retained.eliminate_zeros()
                finite_y=counts/(resistance+2j*np.pi*f*inductance)
                term_y=old_term if index==0 else terms[index]
                termination=cells.branch_laplacian(tp,tn,term_y,N)
                ratio14,spread14=category_ratio(current_coeff,baseline,[6,7]);ratio25,spread25=category_ratio(current_coeff,baseline,[13,14])
                if index==0:
                    control={'retained_gc_relative':difference(retained,categories['retained_gc']),
                             'termination_relative':difference(termination,categories['termination']),
                             'finite_vector_max_relative':float(np.max(abs(finite_y-original_y)/abs(original_y))),
                             'l14_scale_error':abs(ratio14-1),'l25_scale_error':abs(ratio25-1)}
                    assert max(control.values())<2e-12,control
                    continue
                point=next(p for p in cell_result['points'] if p['frequency_hz']==f)
                cell_path=Path(point['artifact']['path']);receipt(cell_path,point['artifact']['sha256'])
                with np.load(cell_path,allow_pickle=False) as z:
                    assert np.array_equal(z['frequency_hz'],[f])
                    y=cells.read_csc(z,'l02_cell_y')+cells.read_csc(z,'l02_contact_y')
                y+=retained+termination+categories['l14_sheet_dc']+ratio14*categories['l14_distributed_gc']+ratio25*categories['l25_distributed_gc']
                y+=cells.branch_laplacian(first,second,finite_y,N)
                y.sum_duplicates();y.eliminate_zeros();y.sort_indices()
                assert np.all(np.isfinite(y.data))
                asymmetry=difference(y,y.T);assert asymmetry<2e-13
                bridge_point=next(p for p in bridges['points'] if p['frequency_hz']==f)
                receipt(Path(bridge_point['artifact']['path']),bridge_point['artifact']['sha256'])
                artifact=output/f'frequency-{int(f)}-conditional-operator.npz';arrays={**rb,**metadata}
                cells.save_csc(arrays,'y',y)
                np.savez_compressed(artifact,**arrays,frequency_hz=np.array([f]),source_current_a=np.array([1.]),
                    finite_admittance_s=finite_y,termination_admittance_s=term_y,native_partial_dispersion_s_per_f=current_coeff,
                    l14_gc_scale=np.array([ratio14]),l25_gc_scale=np.array([ratio25]))
                results.append({'frequency_hz':f,'artifact':receipt(artifact),'cell_operator':point['artifact'],'l04_bridge_inputs':bridge_point['artifact'],
                    'y_nnz':y.nnz,'symmetry_relative':asymmetry,'l14_dispersion_ratio_spread':spread14,'l25_dispersion_ratio_spread':spread25})
                del arrays,y;gc.collect();budget.check('full source-frequency operator saved')
                print(json.dumps({'event':'frequency_operator_assembled','frequency_hz':f,'y_nnz':results[-1]['y_nnz']}),flush=True)
        report={'program':PROGRAM,'version':VERSION,'status':'ASSEMBLED_CONDITIONAL_FULL_CONTACT_FREQUENCY_OPERATORS_NO_SOLVE',
            'driver':receipt(output/'driver-at-run.py'),'inputs':inputs,'one_mhz_external_component_reproduction':control,'points':results,
            'elapsed_seconds':time.monotonic()-started,'budget':budget.receipt(),
            'scope':'Actual per-partial native dispersion and terminations, source-owned native/composite scalarRL, rebuilt L02 cell/contactGC, unchanged L14/L25 DC sheetR. L14/L25 GC category scaling is guarded by within-group dispersion equality to roundoff. Each operator must be used with its frequency-matched L04 delta/U/D and original fixedR NtD. No new solve, physical field validation, magnetic/proximity/skin extension, or PowerSI accuracy claim.'}
        (output/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    except BaseException:
        (output/'failure.json').write_text(json.dumps({'status':'STOP_FULL_CONTACT_FREQUENCY_ASSEMBLY','traceback':traceback.format_exc()},indent=2))
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check',action='store_true');modes.add_argument('--run',action='store_true');modes.add_argument('--native-worker',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();assert RUN_RELEASED
    if args.self_check:self_check();return
    assert args.output is not None
    if args.native_worker:worker(args.output.resolve())
    else:raise SystemExit(launch(args.output.resolve()))
if __name__=='__main__':main()
