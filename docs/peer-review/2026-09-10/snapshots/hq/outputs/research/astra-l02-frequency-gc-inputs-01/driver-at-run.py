"""SPD Decap PI Evaluator v0.23.1: source-area L02 GC inputs at1/10/100MHz, no solve."""
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
import recover_astra_l04_unvalidated_checkpoint as saved

ROOT,R,PROGRAM,VERSION=saved.ROOT,saved.R,saved.PROGRAM,saved.VERSION
RUN_RELEASED=True
EXTERNAL_SECONDS,MEMORY_GIB=90.,3.
MEMORY_COUNTER_SOURCE_SHA=saved.MEMORY_COUNTER_SOURCE_SHA
sha,receipt=saved.sha,saved.receipt
PINS={
 'pack':(R/'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz','01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c'),
 'gc':(R/'astra-l02-cell-gc-loads-01/l02-cell-gc-loads.npz','12ab38f5d692c442a18d6ec7df913c6d96108be73850f27c7014f6f26c9c2459'),
 'stamps':(R/'astra-native-frequency-stamps-01/frequency-stamps.npz','bf2903c044423ea7429ce907567250528903c53eedddd9fffe3265a2669cba3b'),
 'owner_map_result':(R/'astra-l02-l14-l25-combined-assembly-map-02/result.json','cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81'),
 'source_recipe':(ROOT/'tools/research/assemble_astra_l02_full_face_hybrid_operator.py','d87a757c6edfc56b0f2f7329528e1ddee4fd9829efcd6e52c00e8191d7afaa3a'),
}
def pending(): return [k for k,(_,h) in PINS.items() if h.startswith('PENDING_')]

def self_check():
    # Frequency enters the Schur denominator, so scaling a baked cell is wrong.
    rho=2.;w=np.array([.4,.6]);g=.1+.2j
    cell=lambda gc:gc/(1+rho*gc)*np.outer(w,w)
    assert np.max(abs(cell(10*g)-10*cell(g)))>.1
    assert np.allclose(cell(g),g/(1+rho*g)*np.outer(w,w))
    print('PASS_GC_RESTAMP_REQUIRES_NEW_CELL_SCHUR')

def worker(output):
    assert {p.name for p in output.iterdir()}=={'driver-at-run.py'}
    assert sha(output/'driver-at-run.py')==sha(Path(__file__))
    started=time.monotonic();budget=saved.ntd.recon._Budget.create(75.,MEMORY_GIB)
    try:
        inputs={k:receipt(p,h) for k,(p,h) in PINS.items()}
        with np.load(PINS['pack'][0],allow_pickle=False) as z:
            keys=('trace_branch_index','exterior_branch_index','electrode_rim_branch_index','electrode_rim_contact_index',
                  'contact_global_active_index','free_cell_owner_count','free_cell_owner_external_active_index','free_cell_owner_ordinal',
                  'contact_gc_first_active_index','contact_gc_second_active_index')
            fixed={k:z[k] for k in keys};old_g=z['free_cell_gc_admittance_s'];old_c=z['contact_gc_admittance_s']
        with np.load(PINS['gc'][0],allow_pickle=False) as z:
            area=sparse.csr_matrix((z['owner_cell_area_um2_data'],z['owner_cell_area_um2_indices'],z['owner_cell_area_um2_indptr']),shape=tuple(z['owner_cell_area_um2_shape']))
            density=z['owner_density_f_per_um2'];external=z['owner_external_active_indices'];free=z['free_triangle_indices'];tri_c=z['triangle_contact_index'];scale=complex(z['gc_frequency_scale_s'][0])
        with np.load(PINS['stamps'][0],allow_pickle=False) as z: frequencies=z['frequencies_hz'];coefficients=z['partial_dispersion_s_per_f_by_frequency']
        assert np.array_equal(frequencies,[1e6,1e7,1e8,1e9]) and coefficients.shape==(4,36)
        assert abs(scale-coefficients[0,0])<=2e-12*abs(scale)
        assert len(density)==2064 and len(free)==1583840 and old_g.shape==(1583840,5)
        co=area.tocoo();mapping=np.full(len(tri_c),-1,np.int64);mapping[free]=np.arange(len(free));fr=mapping[co.col]
        keep=fr>=0;order=np.argsort(fr[keep],kind='stable');rows=fr[keep][order];owners=co.row[keep][order]
        capacitance=co.data*density[co.row];assert np.all(capacitance>0)
        free_c=capacitance[keep][order];count=np.bincount(rows,minlength=len(free));ptr=np.r_[0,np.cumsum(count)]
        assert np.array_equal(count,fixed['free_cell_owner_count'])
        contact=tri_c[co.col]>=0
        assert np.array_equal(fixed['contact_gc_first_active_index'],fixed['contact_global_active_index'][tri_c[co.col[contact]]])
        assert np.array_equal(fixed['contact_gc_second_active_index'],external[co.row[contact]])
        results=[]
        for index in (0,1,2):
            coefficient=scale if index==0 else complex(coefficients[index,0])
            value=free_c*coefficient;g=np.zeros_like(old_g)
            for k in range(5):
                selected=np.flatnonzero(count>k);positions=ptr[selected]+k
                assert np.array_equal(fixed['free_cell_owner_ordinal'][selected,k],owners[positions])
                g[selected,k]=value[positions]
            contact_g=(capacitance[contact]*coefficient).astype(np.complex128)
            if index==0: assert np.array_equal(g,old_g) and np.array_equal(contact_g,old_c)
            artifact=output/f'frequency-{int(frequencies[index])}-l02-cell-pack.npz'
            np.savez_compressed(artifact,**fixed,free_cell_gc_admittance_s=g,contact_gc_admittance_s=contact_g,
                frequency_hz=np.array([frequencies[index]]),source_partial_ordinal=np.array([0]),gc_frequency_scale_s=np.array([coefficient]))
            results.append({'frequency_hz':float(frequencies[index]),'artifact':receipt(artifact),'partial_ordinal':0})
            budget.check('L02 frequency GC input saved')
        report={'program':PROGRAM,'version':VERSION,'status':'PREPARED_SOURCE_AREA_L02_FREQUENCY_GC_INPUTS_NO_OPERATOR_NO_SOLVE',
            'driver':receipt(output/'driver-at-run.py'),'inputs':inputs,'points':results,'one_mhz_gc_bitwise_reproduced':True,
            'remaining_action':'Call unchanged assemble_astra_l02_conditional_hybrid_operator.build_cell_operator with each frequency cell pack and original restricted geometry, then assemble the remaining frequency-dependent categories and run a new frequency solve.',
            'scope':'Source owner area*density*actual partial0 frequency coefficient; no P1 load or 1MHz field reuse, no whole-cell-matrix scaling, no new board response or acceptance.',
            'elapsed_seconds':time.monotonic()-started,'budget':budget.receipt()}
        (output/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps({'status':report['status'],'elapsed_seconds':report['elapsed_seconds']}),flush=True)
    except BaseException:
        (output/'failure.json').write_text(json.dumps({'status':'STOP_L02_FREQUENCY_GC_INPUTS','traceback':traceback.format_exc()},indent=2))
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
