"""SPD Decap PI Evaluator v0.23.1: disabled hybrid-H auxiliary native+L04 block."""
import argparse
import gc
import json
from pathlib import Path
import sys
import traceback

import numpy as np
from scipy import sparse

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import prepare_astra_l04_contact_ntd_action as ntd
import reconstruct_astra_native_loaded_field as recon
import solve_astra_l04_full_contact_coupled as coupled
import profile_astra_l04_sheet_aware_native_block as prior

R = base.R
RUN_RELEASED = True
TRACE_STATUS = 'QUALIFIED_SAVED_L04_HYBRID_H_AUXILIARY_ONLY'
NINTERNAL, NTRACE_GAUGED = 1660526, 1698803
PINS = {name: coupled.PINS[name] for name in ('base_helper', 'operator', 'bridge', 'bridge_result', 'ntd_helper', 'budget', 'guard')}
PINS.update({name: ntd.PINS[name] for name in ('stream_result',)})
PINS['coupled_helper'] = (Path(coupled.__file__), '6f9b6ce6dc22cefdbb5980b47b30b0c06c8e5ab04a4a0840e7738b014d642430')


PINS.update({
    'prior_profile': (Path(prior.__file__), '5836ba78aa01a818675a4bb4ce3175219a32f2a39b896d8aa2a47314a8d4ffde'),
    'trace_qualification': (R/'astra-l04-exact-hybrid-trace-01/auxiliary-only-qualification.json', 'dee4b3af06c3e335598d3b8f8705a85da6109c64ef3a3efd722971980035bb1e'),
    'trace_artifact': (R/'astra-l04-exact-hybrid-trace-01/structural-checkpoint.npz', '23bbdd3b62f5541ee1f762b5d0552902d41bdb4255cc6a9baef74305bfdb8aad'),
    'trace_driver': (R/'astra-l04-exact-hybrid-trace-01/driver-at-run.py', 'd2ba043b93d09590796b67274052e835d612123670df902528f15a65dbf21a3e'),
})


def pending():
    return [name for name, (_, digest) in PINS.items() if digest.startswith('PENDING_')]


def self_check():
    # Reuse the accepted primal/MNA inverse and ordinary-transpose check.
    assert base.sha(PINS['prior_profile'][0]) == PINS['prior_profile'][1]
    prior.self_check()
    trace = json.loads(PINS['trace_qualification'][0].read_bytes())
    check_qualification(trace)
    for key, invalid in (('physical_operator_replacement_accepted', True),
                         ('preconditioner_candidate_only', False),
                         ('relative_threshold', 2e-7), ('preserved_failed_gates', [])):
        altered = dict(trace, **{key: invalid})
        try:
            check_qualification(altered)
        except AssertionError:
            continue
        raise AssertionError(f'Qualification guard accepted invalid {key}')
    print('PASS_AUXILIARY_ONLY_QUALIFICATION_GUARD', flush=True)


def check_qualification(trace):
    assert trace['status'] == TRACE_STATUS
    assert trace['physical_operator_replacement_accepted'] is False
    assert trace['preconditioner_candidate_only'] is True
    failed = ['raw_hlambda_minus_direct_relative']
    assert trace['preserved_failed_gates'] == failed
    assert len(trace['equivalence_gates']) == 12
    assert [key for key, value in trace['equivalence_gates'].items() if not value] == failed
    assert len(trace['structural_gates']) == 6 and all(trace['structural_gates'].values())
    assert trace['relative_threshold'] == 1e-7
    assert trace['metrics'][failed[0]] > trace['relative_threshold']
    assert trace['structural']['hypergraph_components'] == 1
    assert trace['true_operator_contract']['sha256'] == PINS['ntd_helper'][1]


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
    return inputs, trace


def worker(output):
    budget = recon._Budget.create(120, 8)
    try:
        frozen = output / 'driver-at-run.py'
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs, trace_result = verify_inputs()
        stream = json.loads(PINS['stream_result'][0].read_bytes())
        bridge_result = json.loads(PINS['bridge_result'][0].read_bytes())
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
            u = base.csc(archive, 'u')[1:, :].tocsc()
            d = archive['contact_diagonal_admittance_s']
            delta = coupled.changed_star(archive)
            assert np.array_equal(archive['contact_support_index'], supports)
        with np.load(PINS['operator'][0], allow_pickle=False) as archive:
            y = base.csc(archive, 'y')[1:, 1:].tocsc()
            native, l14, l25, l02 = base.partition(archive['conditional_global_active_index'])
        split = {name: dict(nnz=int(u[rows].nnz), unique_rows=int(np.count_nonzero(np.diff(u[rows].tocsr().indptr))))
                 for name, rows in (('native', native), ('l14', l14), ('l25', l25), ('l02', l02))}
        assert split['native'] == dict(nnz=114421, unique_rows=59740)
        assert split['l02'] == dict(nnz=20, unique_rows=20)
        assert split['l14']['nnz'] == split['l25']['nnz'] == 0
        native_y = (y[native, :][:, native]+delta[native, :][:, native]).tocsc()
        native_delta_nnz = int(delta[native, :][:, native].nnz)
        cross = u[native].tocoo()
        ue = sparse.coo_matrix((cross.data, (cross.row, contact_rows[cross.col])),
            shape=(len(native), NTRACE_GAUGED)).tocsc()
        off = u[l02].tocoo()
        off_rows, off_cols, off_data = l02[off.row], contact_rows[off.col], off.data.copy()
        del y, delta, u, cross, off, l14, l25, l02
        robin = lap+sparse.coo_matrix((d, (contact_rows, contact_rows)), shape=lap.shape).tocsc()
        matrix = sparse.bmat([[native_y, ue], [ue.T, robin]], format='csc')
        matrix.sum_duplicates(); matrix.eliminate_zeros(); matrix.sort_indices()
        assert matrix.shape == (2455688,)*2 and np.all(np.isfinite(matrix.data))
        symmetry = float(np.max(abs((matrix-matrix.T).data), initial=0)/max(np.max(abs(matrix.data)), 1e-300))
        assert symmetry < 2e-13
        scale = 1/np.sqrt(np.asarray(abs(matrix).sum(axis=1)).ravel())
        assert np.all(np.isfinite(scale)) and np.all(scale > 0)
        budget.check('auxiliary-only hybrid trace H and native block assembly')
        artifact = output / 'native-l04-hybrid-aux-block.npz'
        base.atomic_npz(artifact, p_data=matrix.data, p_indices=matrix.indices, p_indptr=matrix.indptr,
            p_shape=np.array(matrix.shape), diagonal_scale=scale, native_gauged_potential_indices=native,
            l04_contact_gauged_trace_rows=contact_rows, l04_root_contact_index=np.array([root_contact]),
            l04_root_trace_row=np.array([NINTERNAL+root_contact]),
            contact_support_index=supports, independent_contact_indices=contacts,
            l04_contact_diagonal_admittance_s=d,
            offblock_l02_gauged_potential_rows=off_rows, offblock_l04_gauged_trace_rows=off_cols,
            offblock_coupling_s=off_data)
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_STATIC_L04_HYBRID_AUX_NATIVE_BLOCK_NO_FACTOR',
            driver=base.receipt(frozen), inputs=inputs, artifact=base.receipt(artifact),
            physical_operator_replacement_accepted=False, preconditioner_candidate_only=True,
            true_operator_contract=trace_result['true_operator_contract'],
            preserved_failed_gates=trace_result['preserved_failed_gates'],
            dimensions=dict(native=len(native), l04_gauged_trace=NTRACE_GAUGED, combined=matrix.shape[0],
                            contacts=len(contacts), root_contact=root_contact, root_trace=NINTERNAL+root_contact),
            sparse=dict(l04_auxiliary_trace_nnz=lap_nnz, native_diagonal_block_nnz=int(native_y.nnz),
                        native_delta_nnz=native_delta_nnz, native_l04_coupling_nnz=int(ue.nnz),
                        robin_nnz=int(robin.nnz), total_nnz=int(matrix.nnz),
                        csc_storage_bytes=int(matrix.data.nbytes+matrix.indices.nbytes+matrix.indptr.nbytes)),
            u_partition=split, symmetry_relative=symmetry,
            geometry_approximation=stream['geometry_approximation'], frequency_hz=1e6,
            budget=budget.receipt(),
            scope='Native+L04 hybrid H auxiliary preconditioner candidate, including every contact and the same root. '
                  'The frozen trace physical-equivalence failure remains unchanged. Exact full-R ContactNtD remains the true operator. '
                  'Twenty L02 couplings remain explicit off-block. No new RT0 resistance, mesh, physical replacement, '
                  'factor, global solve, residual reduction or accuracy claim.')
        base.atomic_json(output / 'result.json', report)
        print(json.dumps(dict(status=report['status'], dimensions=report['dimensions'], sparse=report['sparse'], budget=report['budget'])), flush=True)
    except BaseException:
        base.atomic_json(output / 'failure.json', dict(status='STOP_STATIC_L04_HYBRID_AUX_NATIVE_BLOCK',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise
    finally:
        gc.collect()


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
        print(json.dumps(dict(status='PASS_HYBRID_AUX_NATIVE_BLOCK_INPUTS', inputs=inputs, trace_status=trace['status'])))
    else:
        assert RUN_RELEASED and not pending() and args.output is not None, 'Static assembly held for Sol/root review'
        output = args.output.resolve()
        if args.native_worker:
            worker(output)
        else:
            assert base.sha(PINS['guard'][0]) == PINS['guard'][1]
            import probe_astra_fmm3d_runtime as guard
            output.mkdir(parents=True, exist_ok=False)
            (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
            command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(output)]
            raise SystemExit(guard.guarded_source_worker(output, worker_command=command, max_runtime_s=150))
