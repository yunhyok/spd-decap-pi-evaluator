"""SPD Decap PI Evaluator v0.23.1: reuse the qualified L02 cell assembler at10/100MHz."""
from pathlib import Path
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
import assemble_astra_l02_conditional_hybrid_operator as cells
import recover_astra_l04_unvalidated_checkpoint as saved

ROOT,R,PROGRAM,VERSION=saved.ROOT,saved.R,saved.PROGRAM,saved.VERSION
RUN_RELEASED=True
EXTERNAL_SECONDS,MEMORY_GIB=150.,4.
MEMORY_COUNTER_SOURCE_SHA=saved.MEMORY_COUNTER_SOURCE_SHA
sha,receipt=saved.sha,saved.receipt
PINS={
 'cell_assembler':(Path(cells.__file__),'79ab74ff53bef21347038a8ab0c243260049cd97dfea5e707320f84de19210d3'),
 'frequency_inputs':(R/'astra-l02-frequency-gc-inputs-01/result.json','1381d9c8463cf69889da68f0042517f019c0ffccdedd9bd36db16bfb0297f2fe'),
 'frequency_guard':(R/'astra-l02-frequency-gc-inputs-01/external-budget.json','38b7347d8ff4c6629f45ac634ea2cf5245a9f999509ce938ca112fce92104eaf'),
 'restricted':cells.PINS['restricted'],
}
def pending():return []

def worker(output):
    assert {p.name for p in output.iterdir()}=={'driver-at-run.py'}
    assert sha(output/'driver-at-run.py')==sha(Path(__file__))
    started=time.monotonic();budget=saved.ntd.recon._Budget.create(135.,MEMORY_GIB)
    try:
        inputs={k:receipt(p,h) for k,(p,h) in PINS.items()}
        prep=json.loads(PINS['frequency_inputs'][0].read_bytes()); guard=json.loads(PINS['frequency_guard'][0].read_bytes())
        assert prep['status']=='PREPARED_SOURCE_AREA_L02_FREQUENCY_GC_INPUTS_NO_OPERATOR_NO_SOLVE'
        assert prep['one_mhz_gc_bitwise_reproduced'] is True
        assert guard['status']=='COMPLETED_NATIVE_WORKER' and guard['exit_code']==0 and guard['driver_sha256']==prep['driver']['sha256']
        points=[p for p in prep['points'] if p['frequency_hz'] in (1e7,1e8)]
        assert [p['frequency_hz'] for p in points]==[1e7,1e8]
        results=[]
        for point in points:
            item=point['artifact'];pack=Path(item['path']);receipt(pack,item['sha256'])
            with np.load(pack,allow_pickle=False) as z:
                assert np.array_equal(z['frequency_hz'],[point['frequency_hz']]) and np.array_equal(z['source_partial_ordinal'],[0])
            before=time.monotonic()
            y,maps,metrics,trace=cells.build_cell_operator(pack,PINS['restricted'][0],budget)
            assert y.shape==(3178104,3178104) and np.all(np.isfinite(y.data))
            contact_y=None
            with np.load(pack,allow_pickle=False) as z:
                contact_y=cells.branch_laplacian(z['contact_gc_first_active_index'],z['contact_gc_second_active_index'],z['contact_gc_admittance_s'],3178104)
            arrays={};cells.save_csc(arrays,'l02_cell_y',y);cells.save_csc(arrays,'l02_contact_y',contact_y)
            artifact=output/f"frequency-{int(point['frequency_hz'])}-l02-cell-operators.npz"
            np.savez_compressed(artifact,**arrays,frequency_hz=np.array([point['frequency_hz']]),source_partial_ordinal=np.array([0]))
            results.append({'frequency_hz':point['frequency_hz'],'cell_pack':item,'artifact':receipt(artifact),'metrics':metrics,
                'cell_nnz':y.nnz,'contact_nnz':contact_y.nnz,'elapsed_seconds':time.monotonic()-before})
            del arrays,y,contact_y,maps,trace;gc.collect();budget.check('frequency cell operator saved')
            print(json.dumps({'event':'frequency_cell_assembled',**results[-1]}),flush=True)
        report={'program':PROGRAM,'version':VERSION,'status':'ASSEMBLED_L02_FREQUENCY_CELL_OPERATORS_NO_BOARD_SOLVE',
            'driver':receipt(output/'driver-at-run.py'),'inputs':inputs,'points':results,'elapsed_seconds':time.monotonic()-started,'budget':budget.receipt(),
            'scope':'Unchanged qualified restricted RT0 cell assembly with actual frequency-specific partial0 GC and unchanged topology/R geometry. Cell algebra checks apply only to these blocks. Native/L14/L25 categories and finite/termination terms still require composition; no complete Y, board response or acceptance.'}
        (output/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    except BaseException:
        (output/'failure.json').write_text(json.dumps({'status':'STOP_FREQUENCY_CELL_ASSEMBLY','traceback':traceback.format_exc()},indent=2))
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
    modes.add_argument('--run',action='store_true');modes.add_argument('--native-worker',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();assert RUN_RELEASED
    if args.native_worker:worker(args.output.resolve())
    else:raise SystemExit(launch(args.output.resolve()))
if __name__=='__main__':main()
