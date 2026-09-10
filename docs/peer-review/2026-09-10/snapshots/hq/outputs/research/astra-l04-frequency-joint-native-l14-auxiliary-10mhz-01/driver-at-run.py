"""SPD Decap PI Evaluator v0.23.1: disabled joint native+L14 10 MHz hybrid-H auxiliary."""
import argparse
import ctypes
import ctypes.wintypes
import gc
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
from scipy import sparse

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import prepare_astra_l04_contact_ntd_action as ntd
import reconstruct_astra_native_loaded_field as recon
import profile_astra_l04_hybrid_aux_native_block as qualified

R = base.R
RUN_RELEASED = True
FREQUENCY_HZ = 10_000_000.0
PROGRAM, VERSION = 'SPD Decap PI Evaluator', '0.23.1'
EXTERNAL_SECONDS, MEMORY_GIB = 150., 8.
TRACE_STATUS = 'QUALIFIED_SAVED_L04_HYBRID_H_AUXILIARY_ONLY'
NINTERNAL, NTRACE_GAUGED = 1660526, 1698803
PINS = {
    'operator_result': (R/'astra-full-contact-frequency-operators-02/result.json', '7efcd3d999162b27311136a462ef5335071c00e371c6bbe6ac1333d7a98f6555'),
    'operator_driver': (R/'astra-full-contact-frequency-operators-02/driver-at-run.py', '9218fdd6712f9ed4c9bf5c30815f3730d02bab50cc22ac93c9e665d4a2b779e4'),
    'operator_external': (R/'astra-full-contact-frequency-operators-02/external-budget.json', 'ff816c7f857cdd4d6a77f6f57a5331943770b9c3af29f5b7a691143b9fdc0a1c'),
    'operator': (R/'astra-full-contact-frequency-operators-02/frequency-10000000-conditional-operator.npz', '7d528de4ced4e24e6fbf1c0ba437276ce8ccc706684e36028f63494685171701'),
    'bridge_result': (R/'astra-l04-frequency-partial-inputs-01/result.json', 'a0117fe7cfe66646ac09d2450ed2be283e7754bce728dc8c33d9916c4b2d2958'),
    'bridge_driver': (R/'astra-l04-frequency-partial-inputs-01/driver-at-run.py', 'b32c738ba610319492be737d488c91bea77a3cc79a8063ab9d63bfac3256158f'),
    'bridge': (R/'astra-l04-frequency-partial-inputs-01/frequency-10000000-partial-inputs.npz', 'f5fff0c74c667d8243852b709ed07238585eec427a878bb074519d995f5f680a'),
    'bridge_external': (R/'astra-l04-frequency-partial-inputs-01/external-budget.json', '964b69f692efb28ab66d3dd7ea52348838f721d8efe646244889e2949511210a'),
    'geometry_bridge_result': (R/'astra-l04-full-contact-circuit-bridge-01/result.json', '1583612f90fca3c97f7b9fb4f8c3a0a004d6ae8d84ccc1f7bbc8f382247d266f'),
    'geometry_bridge': (R/'astra-l04-full-contact-circuit-bridge-01/l04-full-contact-circuit-bridge.npz', '02508f2e1588fe5e6ad4231b9c128a3324a88baf0229293d6a8b70ea10380e57'),
    'base_helper': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'qualified_helper': (Path(qualified.__file__), '0c8e32bf2e59397cadc425e1d76aa0b2b2d6c6c130ae4cbb3deebe0d2e628c65'),
    'memory_counter': (R.parents[1]/'tools/research/probe_astra_l25_rt0_p1_pair.py', '35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20'),
    'ntd_helper': (Path(ntd.__file__), 'e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8'),
    'stream_result': ntd.PINS['stream_result'],
    'budget': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
    'guard': (R.parents[1]/'tools/research/probe_astra_fmm3d_runtime.py', '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'),
    'trace_qualification': (R/'astra-l04-exact-hybrid-trace-01/auxiliary-only-qualification.json', 'dee4b3af06c3e335598d3b8f8705a85da6109c64ef3a3efd722971980035bb1e'),
    'trace_artifact': (R/'astra-l04-exact-hybrid-trace-01/structural-checkpoint.npz', '23bbdd3b62f5541ee1f762b5d0552902d41bdb4255cc6a9baef74305bfdb8aad'),
    'trace_driver': (R/'astra-l04-exact-hybrid-trace-01/driver-at-run.py', 'd2ba043b93d09590796b67274052e835d612123670df902528f15a65dbf21a3e'),
}


def pending():
    return [name for name, (_, digest) in PINS.items() if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)]


def self_check():
    assert base.sha(PINS['qualified_helper'][0]) == PINS['qualified_helper'][1]
    qualified.self_check()
    assert not pending()
    native = np.array([0, 2]); l14_rows = np.array([1]); joint = np.r_[native, l14_rows]
    y = sparse.csc_matrix(np.array([[3, 1, 2], [1, 4, 5], [2, 5, 6]], dtype=complex))
    u = sparse.csc_matrix(np.array([[7], [0], [8]], dtype=complex))
    assert len(np.unique(joint)) == len(joint) == 3
    assert np.array_equal((y[joint, :][:, joint]).toarray(), y.toarray()[np.ix_(joint, joint)])
    assert u[l14_rows].nnz == 0 and u[joint].nnz == u[native].nnz
    print('PASS_10MHZ_JOINT_NATIVE_L14_AUXILIARY_TOPOLOGY_AND_SOURCE_PINS')


check_qualification = qualified.check_qualification


def verify_inputs():
    assert not pending(), 'Auxiliary trace inputs are not pinned'
    inputs = {name: ntd.receipt(path, digest) for name, (path, digest) in PINS.items()}
    trace = json.loads(PINS['trace_qualification'][0].read_bytes())
    check_qualification(trace)
    assert trace['structural_checkpoint']['sha256'] == PINS['trace_artifact'][1]
    assert trace['driver']['sha256'] == PINS['trace_driver'][1]
    assert not PINS['trace_qualification'][0].with_name('result.json').exists()
    for key in ('source_structure', 'source_preacceptance', 'source_failure', 'source_decomposition', 'source_cells', 'source_equivalence_artifact'):
        saved = trace[key]
        inputs[key] = ntd.receipt(Path(saved['path']), saved['sha256'])
    for prefix, status in (('operator', 'ASSEMBLED_CONDITIONAL_FULL_CONTACT_FREQUENCY_OPERATORS_NO_SOLVE'),
                           ('bridge', 'PREPARED_PARTIAL_FREQUENCY_INPUTS_NO_SOLVE')):
        report = json.loads(PINS[prefix+'_result'][0].read_bytes())
        guard = json.loads(PINS[prefix+'_external'][0].read_bytes())
        assert report['status'] == status
        assert report['driver']['sha256'] == guard['driver_sha256'] == PINS[prefix+'_driver'][1]
        assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
        points = [p for p in report['points'] if p['frequency_hz'] == FREQUENCY_HZ]
        assert len(points) == 1 and points[0]['artifact']['sha256'] == PINS[prefix][1]
        if prefix == 'operator':
            assert points[0]['l04_bridge_inputs']['sha256'] == PINS['bridge'][1]
    geometry = json.loads(PINS['geometry_bridge_result'][0].read_bytes())
    assert geometry['status'] == 'PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE'
    assert geometry['artifact']['sha256'] == PINS['geometry_bridge'][1]
    return inputs, trace


def worker(output):
    budget = recon._Budget.create(120, 8)
    try:
        frozen = output / 'driver-at-run.py'
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs, trace_result = verify_inputs()
        stream = json.loads(PINS['stream_result'][0].read_bytes())
        bridge_result = json.loads(PINS['geometry_bridge_result'][0].read_bytes())
        assert stream['status'] == 'PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'
        assert bridge_result['status'] == 'PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE'
        assert stream['geometry_approximation'] == bridge_result['geometry_approximation'] == trace_result['geometry_approximation']
        root = int(stream['metrics']['gauge_contact_graph_row'])
        assert root == ntd.FREE_CELL_COUNT+bridge_result['root_contact_index']
        with np.load(PINS['trace_artifact'][0], allow_pickle=False) as archive:
            lap = base.csc(archive, 'h_gauged')
            supports = archive['contact_support_index']
            contacts = archive['independent_contact_indices']
            contact_rows = archive['gauged_contact_trace_rows']
            root_contact = int(archive['gauge_contact_index'][0])
        assert root_contact == bridge_result['root_contact_index'] == 25440
        assert lap.shape == (NTRACE_GAUGED,)*2 and supports.shape == (ntd.CONTACT_COUNT,)
        assert np.array_equal(contacts, np.delete(np.arange(ntd.CONTACT_COUNT), root_contact))
        assert np.array_equal(contact_rows, NINTERNAL+contacts-(contacts > root_contact))
        assert np.all(np.isfinite(lap.data)) and np.all(lap.diagonal() > 0)
        lap_nnz = int(lap.nnz)
        with np.load(PINS['bridge'][0], allow_pickle=False) as archive:
            assert np.array_equal(archive['frequency_hz'], [FREQUENCY_HZ]) and np.array_equal(archive['source_current_a'], [1.])
            u = base.csc(archive, 'l04_u')[1:, :].tocsc()
            d = archive['l04_contact_diagonal_admittance_s']
            delta = base.csc(archive, 'l04_delta')[1:, 1:].tocsc()
            bridge_coefficients = archive['native_partial_dispersion_s_per_f']
            bridge_terminations = archive['native_termination_admittance_s']
        with np.load(PINS['geometry_bridge'][0], allow_pickle=False) as archive:
            assert np.array_equal(archive['contact_support_index'], supports)
        with np.load(PINS['operator'][0], allow_pickle=False) as archive:
            assert np.array_equal(archive['frequency_hz'], [FREQUENCY_HZ]) and np.array_equal(archive['source_current_a'], [1.])
            assert np.array_equal(archive['native_partial_dispersion_s_per_f'], bridge_coefficients)
            assert np.array_equal(archive['termination_admittance_s'], bridge_terminations)
            y = base.csc(archive, 'y')[1:, 1:].tocsc()
            native, l14, l25, l02 = base.partition(archive['conditional_global_active_index'])
        assert y.shape == delta.shape == (3178103, 3178103)
        assert u.shape == (3178103, 38277) and d.shape == (38277,)
        assert np.all(np.isfinite(d)) and np.all(d.real > 0)
        split = {name: dict(nnz=int(u[rows].nnz), unique_rows=int(np.count_nonzero(np.diff(u[rows].tocsr().indptr))))
                 for name, rows in (('native', native), ('l14', l14), ('l25', l25), ('l02', l02))}
        assert split['native'] == dict(nnz=114421, unique_rows=59740)
        assert split['l02'] == dict(nnz=20, unique_rows=20)
        assert split['l14']['nnz'] == split['l25']['nnz'] == 0
        joint = np.r_[native, l14]
        assert len(joint) == 903942 and len(np.unique(joint)) == len(joint)
        assert np.intersect1d(native, l14).size == 0
        joint_y = (y[joint, :][:, joint]+delta[joint, :][:, joint]).tocsc()
        joint_delta_nnz = int(delta[joint, :][:, joint].nnz)
        cross = u[joint].tocoo()
        ue = sparse.coo_matrix((cross.data, (cross.row, contact_rows[cross.col])),
            shape=(len(joint), NTRACE_GAUGED)).tocsc()
        off = u[l02].tocoo()
        off_rows, off_cols, off_data = l02[off.row], contact_rows[off.col], off.data.copy()
        del y, delta, u, cross, off, l25, l02
        robin = lap+sparse.coo_matrix((d, (contact_rows, contact_rows)), shape=lap.shape).tocsc()
        matrix = sparse.bmat([[joint_y, ue], [ue.T, robin]], format='csc')
        matrix.sum_duplicates(); matrix.eliminate_zeros(); matrix.sort_indices()
        assert matrix.shape == (2602745,)*2 and np.all(np.isfinite(matrix.data))
        symmetry = float(np.max(abs((matrix-matrix.T).data), initial=0)/max(np.max(abs(matrix.data)), 1e-300))
        assert symmetry < 2e-13
        scale = 1/np.sqrt(np.asarray(abs(matrix).sum(axis=1)).ravel())
        assert np.all(np.isfinite(scale)) and np.all(scale > 0)
        budget.check('auxiliary-only hybrid trace H and joint native+L14 block assembly')
        artifact = output / 'joint-native-l14-l04-hybrid-aux-block.npz'
        base.atomic_npz(artifact, p_data=matrix.data, p_indices=matrix.indices, p_indptr=matrix.indptr,
            p_shape=np.array(matrix.shape), diagonal_scale=scale,
            joint_native_l14_gauged_potential_indices=joint,
            native_gauged_potential_indices=native, l14_gauged_potential_indices=l14,
            l04_contact_gauged_trace_rows=contact_rows, l04_root_contact_index=np.array([root_contact]),
            l04_root_trace_row=np.array([NINTERNAL+root_contact]),
            contact_support_index=supports, independent_contact_indices=contacts,
            l04_contact_diagonal_admittance_s=d,
            offblock_l02_gauged_potential_rows=off_rows, offblock_l04_gauged_trace_rows=off_cols,
            offblock_coupling_s=off_data, frequency_hz=np.array([FREQUENCY_HZ]), source_current_a=np.array([1.]))
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_STATIC_L04_HYBRID_AUX_JOINT_NATIVE_L14_BLOCK_NO_FACTOR',
            driver=base.receipt(frozen), inputs=inputs, artifact=base.receipt(artifact),
            physical_operator_replacement_accepted=False, preconditioner_candidate_only=True,
            true_operator_contract=trace_result['true_operator_contract'],
            preserved_failed_gates=trace_result['preserved_failed_gates'],
            dimensions=dict(native=len(native), l14=len(l14), joint_native_l14=len(joint),
                            l04_gauged_trace=NTRACE_GAUGED, combined=matrix.shape[0],
                            contacts=len(contacts), root_contact=root_contact, root_trace=NINTERNAL+root_contact),
            sparse=dict(l04_auxiliary_trace_nnz=lap_nnz, joint_native_l14_diagonal_block_nnz=int(joint_y.nnz),
                        joint_native_l14_delta_nnz=joint_delta_nnz, joint_native_l14_l04_coupling_nnz=int(ue.nnz),
                        robin_nnz=int(robin.nnz), total_nnz=int(matrix.nnz),
                        csc_storage_bytes=int(matrix.data.nbytes+matrix.indices.nbytes+matrix.indptr.nbytes)),
            u_partition=split, symmetry_relative=symmetry,
            geometry_approximation=stream['geometry_approximation'], frequency_hz=FREQUENCY_HZ, source_current_a=1.,
            budget=budget.receipt(),
            scope='Joint native+L14 potential and L04 hybrid-H auxiliary preconditioner candidate, including every contact and the same root. '
                  'The frozen trace physical-equivalence failure remains unchanged. Exact full-R ContactNtD remains the true operator. '
                  'Twenty L02 couplings remain explicit off-block. No new RT0 resistance, mesh, physical replacement, '
                  'factor, fill or memory claim, global solve, residual reduction or accuracy claim. A future inverse adapter must fill the joint block once, omit the separate L14 factor and L14 constant coarse mode, and retain only L25/L02 coarse modes.')
        base.atomic_json(output / 'result.json', report)
        print(json.dumps(dict(status=report['status'], dimensions=report['dimensions'], sparse=report['sparse'], budget=report['budget'])), flush=True)
    except BaseException:
        base.atomic_json(output / 'failure.json', dict(status='STOP_STATIC_L04_HYBRID_AUX_JOINT_NATIVE_L14_BLOCK',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise
    finally:
        gc.collect()


def launch(output: Path) -> int:
    assert not pending(), f"unfilled pins: {pending()}"
    import probe_astra_l25_rt0_p1_pair as counter
    assert base.sha(Path(counter.__file__)) == PINS['memory_counter'][1]
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
        print(f"bounded 10MHz joint native+L14 auxiliary assembly watchdog: owned PID={process.pid},{EXTERNAL_SECONDS}s/{MEMORY_GIB}GiB", flush=True)
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
        "status": status, "exit_code": code, "driver_sha256": base.sha(frozen),
        "external_seconds": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30),
        "printed_memory_cap_gib": MEMORY_GIB, "sampled_peak_private_bytes": private,
        "sampled_peak_working_set_bytes": working, "sampling_interval_s": .5}, indent=2), encoding="utf-8")
    return 0 if status == "COMPLETED_NATIVE_WORKER" and code == 0 else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check', action='store_true')
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--native-worker', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.preflight:
        inputs, trace = verify_inputs()
        print(json.dumps(dict(status='PASS_HYBRID_AUX_JOINT_NATIVE_L14_BLOCK_INPUTS', inputs=inputs, trace_status=trace['status'])))
    else:
        assert RUN_RELEASED and not pending() and args.output is not None, 'Static assembly held for Sol/root review'
        output = args.output.resolve()
        if args.native_worker:
            worker(output)
        else:
            raise SystemExit(launch(output))
