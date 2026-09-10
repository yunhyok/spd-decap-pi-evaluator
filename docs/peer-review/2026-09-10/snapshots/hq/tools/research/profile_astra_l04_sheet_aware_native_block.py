"""Save the native+L04 auxiliary block and its static cost; never factor or solve a board."""
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

R = base.R
RUN_RELEASED = True  # Sol/root accepted02b574ef for one saved-data assembly-only run.
PINS = {name: coupled.PINS[name] for name in ('base_helper', 'operator', 'bridge', 'bridge_result', 'ntd_helper', 'budget', 'guard')}
PINS.update({name: ntd.PINS[name] for name in ('stream_result', 'stream_space')})
PINS['coupled_helper'] = (Path(coupled.__file__), '6f9b6ce6dc22cefdbb5980b47b30b0c06c8e5ab04a4a0840e7738b014d642430')


def self_check():
    a = np.array([[8+.2j, -1, .3], [-1, 6+.4j, -.2], [.3, -.2, -2]], complex)
    u = np.array([[1+.1j, -.3], [.2, .8+.2j]], complex)
    ue = np.vstack([u, np.zeros((1, 2))])
    d = np.array([3+.4j, 4+.2j])
    lap = np.diag([3., 4., 5., 3.])+np.diag([-1., -1., -1.], 1)+np.diag([-1., -1., -1.], -1)
    e = np.eye(4)[:, [1, 3]]
    z = e.T@np.linalg.solve(lap, e)
    matrix = np.column_stack([coupled.interface_apply(lambda x: a@x, u, d, lambda g: z@g,
                                                     v[:3], v[3:], 2) for v in np.eye(5)])
    primal = np.block([[a, ue@e.T], [e@ue.T, lap+e@np.diag(d)@e.T]])
    transform = np.block([[np.eye(3), -ue], [np.zeros((4, 3)), -e@np.diag(d)]])
    inverse = transform.T@np.linalg.solve(primal, transform)-np.diag(np.r_[np.zeros(3), d])
    assert np.max(abs(matrix@inverse-np.eye(5))) < 3e-14
    assert np.max(abs(inverse-inverse.T)) < 3e-14
    # An approximate symmetric primal inverse must still yield a symmetric MNA preconditioner.
    approximate = transform.T@np.diag(1/primal.diagonal())@transform-np.diag(np.r_[np.zeros(3), d])
    assert np.max(abs(approximate-approximate.T)) < 3e-14
    small = graph_laplacian(np.array([0, 1, 0]), np.array([1, 2, 1]), np.array([1., 2., 3.]), 3, 1)
    assert np.max(abs(small.toarray()-np.diag([4/3, .5]))) < 3e-14
    print('PASS_SHEET_AWARE_PRIMAL_MNA_INVERSE_AND_TRANSPOSE')


def graph_laplacian(first, second, diagonal, node_count, root):
    assert first.shape == second.shape == diagonal.shape and np.all(diagonal > 0)
    conductance = 1/diagonal
    matrix = sparse.coo_matrix((np.r_[conductance, conductance, -conductance, -conductance],
        (np.r_[first, second, first, second], np.r_[first, second, second, first])),
        shape=(node_count, node_count)).tocsc()
    matrix.sum_duplicates(); matrix.eliminate_zeros()
    keep = np.r_[np.arange(root), np.arange(root+1, node_count)]
    return matrix[keep, :][:, keep].tocsc()


def worker(output):
    budget = recon._Budget.create(120, 8)
    try:
        frozen = output / 'driver-at-run.py'
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = {name: ntd.receipt(path, digest) for name, (path, digest) in PINS.items()}
        stream = json.loads(PINS['stream_result'][0].read_bytes())
        bridge_result = json.loads(PINS['bridge_result'][0].read_bytes())
        assert stream['status'] == 'PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'
        assert bridge_result['status'] == 'PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE'
        assert stream['geometry_approximation'] == bridge_result['geometry_approximation']
        root = int(stream['metrics']['gauge_contact_graph_row'])
        node_count = ntd.NODE_COUNT
        assert root == ntd.FREE_CELL_COUNT+bridge_result['root_contact_index']
        with np.load(PINS['stream_space'][0], allow_pickle=False) as space:
            r = base.csr(space, 'r')
            diagonal = r.diagonal().copy()
            first, second = space['branch_first_node'], space['branch_second_node']
            supports = space['contact_support_index']
        assert r.shape == (ntd.BRANCH_COUNT,)*2 and len(supports) == ntd.CONTACT_COUNT
        assert np.all(np.isfinite(diagonal)) and np.all(diagonal > 0)
        del r
        lap = graph_laplacian(first, second, diagonal, node_count, root)
        lap_nnz = int(lap.nnz)
        del first, second
        contacts = np.delete(np.arange(ntd.CONTACT_COUNT), bridge_result['root_contact_index'])
        contact_nodes = ntd.FREE_CELL_COUNT+contacts
        contact_rows = contact_nodes-(contact_nodes > root)
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
            shape=(len(native), node_count-1)).tocsc()
        off = u[l02].tocoo()
        off_rows, off_cols, off_data = l02[off.row], contact_rows[off.col], off.data.copy()
        del y, delta, u, cross, off, l14, l25, l02
        robin = lap+sparse.coo_matrix((d, (contact_rows, contact_rows)), shape=lap.shape).tocsc()
        matrix = sparse.bmat([[native_y, ue], [ue.T, robin]], format='csc')
        matrix.sum_duplicates(); matrix.eliminate_zeros(); matrix.sort_indices()
        assert matrix.shape == (2384989,)*2 and np.all(np.isfinite(matrix.data))
        symmetry = float(np.max(abs((matrix-matrix.T).data), initial=0)/max(np.max(abs(matrix.data)), 1e-300))
        assert symmetry < 2e-13
        scale = 1/np.sqrt(np.asarray(abs(matrix).sum(axis=1)).ravel())
        assert np.all(np.isfinite(scale)) and np.all(scale > 0)
        budget.check('saved-diagonal graph and native block assembly only')
        artifact = output / 'native-l04-auxiliary-block.npz'
        base.atomic_npz(artifact, p_data=matrix.data, p_indices=matrix.indices, p_indptr=matrix.indptr,
            p_shape=np.array(matrix.shape), diagonal_scale=scale, native_gauged_potential_indices=native,
            l04_contact_gauged_graph_rows=contact_rows, l04_root_graph_row=np.array([root]),
            l04_contact_diagonal_admittance_s=d, l04_r_diagonal_ohm=diagonal,
            offblock_l02_gauged_potential_rows=off_rows, offblock_l04_gauged_graph_rows=off_cols,
            offblock_coupling_s=off_data)
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_STATIC_L04_SHEET_AWARE_NATIVE_AUXILIARY_NO_FACTOR',
            driver=base.receipt(frozen), inputs=inputs, artifact=base.receipt(artifact),
            dimensions=dict(native=len(native), l04_gauged_graph=node_count-1, combined=matrix.shape[0],
                            contacts=len(contacts), root_graph=root),
            sparse=dict(lumped_l04_graph_nnz=lap_nnz, native_diagonal_block_nnz=int(native_y.nnz),
                        native_delta_nnz=native_delta_nnz, native_l04_coupling_nnz=int(ue.nnz),
                        robin_nnz=int(robin.nnz), total_nnz=int(matrix.nnz),
                        csc_storage_bytes=int(matrix.data.nbytes+matrix.indices.nbytes+matrix.indptr.nbytes)),
            u_partition=split, symmetry_relative=symmetry,
            geometry_approximation=stream['geometry_approximation'], frequency_hz=1e6,
            budget=budget.receipt(),
            scope='Preconditioner-only diag(R04) graph, including all gauged graph/contact nodes and same root. '
                  'Native+L04 replaces the old native factor; it must not overlap that factor in a later solver. '
                  'Twenty L02 couplings remain explicit off-block. No RT0 mass reconstruction, mesh change, '
                  'exact-NtD replacement, sparse LU, global solve, spectral equivalence, residual reduction, or accuracy claim.')
        base.atomic_json(output / 'result.json', report)
        print(json.dumps(dict(status=report['status'], dimensions=report['dimensions'], sparse=report['sparse'], budget=report['budget'])), flush=True)
    except BaseException:
        base.atomic_json(output / 'failure.json', dict(status='STOP_STATIC_L04_SHEET_AWARE_AUXILIARY',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise
    finally:
        gc.collect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check', action='store_true')
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--native-worker', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        assert RUN_RELEASED and args.output is not None, 'Static assembly held for Sol/root review'
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
