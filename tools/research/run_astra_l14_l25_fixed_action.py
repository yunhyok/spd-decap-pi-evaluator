"""SPD Decap PI Evaluator v0.23.1: one guarded labelled sheet action."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np
from probe_astra_l25_rt0_p1_pair import _MemoryCounters

CHILD = Path('C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture')
DRIVER = CHILD/'magnetic-p1-adapter-qualification-01/build_and_check.py'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def worker(out):
    assert digest(Path(__file__)) == digest(out/'driver-at-run.py')
    from apply_astra_l25_rt0_magnetic import verify_environment, _geometry, _scatter
    verify_environment()
    spec = importlib.util.spec_from_file_location('qualified_child', out/'child-driver-at-run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PREFLIGHT = CHILD/'magnetic-action-preflight/result.json'
    module.OUTPUT = out/'qualification.json'
    module.ACTION = out/'fixed-current-point-action.npz'
    module.ACTION_RECEIPT = out/'fixed-current-point-action-receipt.json'
    qualification = module.run()
    assert qualification['qualification']['fmm_nd2']['status'] in (
        'PASS_FMM3D_ND2_TINY_DENSE', 'PASS_PINNED_ND2_SOURCE_EVALUATION_SELF_OMISSION')
    save(module.OUTPUT, qualification)
    source = next(Path(p) for p in qualification['inputs'] if p.endswith('combined-magnetic-source-descriptor.npz'))
    with np.load(source, allow_pickle=False) as z:
        geometry = _geometry(z['l25_triangle_vertices_um']*1e-6, z['l25_local_facet_branch_index'],
                             z['l25_local_outward_flux_sign'], len(z['l25_branch_current_a']))
        scattered = _scatter(z['l25_branch_current_a'], *geometry[1:6])
        expected = geometry[4][:, None]*z['l25_average_current_a_per_m']
        error = float(np.linalg.norm(scattered-expected)/np.linalg.norm(expected))
        assert error < 1e-12
        centers = z['l14_triangle_vertices_um'].mean(axis=1)
        assert len(np.unique(centers, axis=0)) == len(centers)
    save(out/'mapping-check.json', dict(status='PASS_ACTUAL_RT0_MOMENT_AND_DISTINCT_CENTROIDS', relative_error=error))
    del geometry, scattered, expected, centers
    print('START_SOURCE_SCALE_LABELLED_ACTION', flush=True)
    module.source_action()
    print('COMPLETED_SOURCE_SCALE_LABELLED_ACTION', flush=True)


def launch(out, driver_sha, source_driver=DRIVER, max_seconds=420):
    assert max_seconds > 0 and digest(source_driver) == driver_sha
    out.mkdir(parents=True, exist_ok=False)
    (out/'child-driver-at-run.py').write_bytes(source_driver.read_bytes())
    assert digest(out/'child-driver-at-run.py') == driver_sha
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD)
    getter.restype = wintypes.BOOL
    started = time.monotonic()
    private = working = 0
    reason = None
    with (out/'worker.log').open('w', encoding='utf-8') as log:
        with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              creationflags=subprocess.CREATE_NO_WINDOW) as child:
            print(f'Started owned worker {child.pid}: {max_seconds} seconds / 32 GiB', flush=True)
            try:
                while child.poll() is None:
                    values = _MemoryCounters(); values.cb = ctypes.sizeof(values)
                    if getter(int(child._handle), ctypes.byref(values), values.cb):
                        private = max(private, int(values.private_usage))
                        working = max(working, int(values.working_set))
                    elif child.poll() is None:
                        reason = 'STOP_MEMORY_QUERY_FAILED'
                    if time.monotonic()-started >= max_seconds:
                        reason = 'STOP_EXTERNAL_TIMEOUT'
                    if max(private, working) > 32*2**30:
                        reason = 'STOP_EXTERNAL_MEMORY'
                    if reason:
                        child.kill(); break
                    time.sleep(.5)
                code = child.wait(timeout=10)
            except BaseException:
                reason = traceback.format_exc()
                if child.poll() is None:
                    child.kill()
                code = child.wait(timeout=10)
    receipt = dict(status=reason or ('COMPLETED_NATIVE_WORKER' if code == 0 else 'STOP_NATIVE_WORKER_EXIT'),
                   owned_pid=child.pid, exit_code=code, elapsed_s=time.monotonic()-started,
                   peak_private_bytes=private, peak_working_set_bytes=working,
                   max_seconds=max_seconds, max_bytes=32*2**30, command=command, child_driver_sha256=driver_sha)
    save(out/'external-budget.json', receipt)
    print(json.dumps(receipt), flush=True)
    return 0 if reason is None and code == 0 else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--driver-sha')
    parser.add_argument('--source-driver', type=Path, default=DRIVER)
    parser.add_argument('--max-seconds', type=float, default=420)
    args = parser.parse_args()
    if args.worker:
        worker(args.output.resolve())
    else:
        raise SystemExit(launch(args.output.resolve(), args.driver_sha, args.source_driver, args.max_seconds))
