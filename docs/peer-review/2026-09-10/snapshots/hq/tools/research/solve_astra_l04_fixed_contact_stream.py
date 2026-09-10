"""Conditional L04 current lift: fixed contact totals and zero exterior flux.

Reuse the existing RT0 topology and local resistance. A tree supplies one
feasible current; the complete stream space then minimizes Joule loss. This
does not enlarge the circuit or predict a new PowerSI response.
"""
import argparse
import json
from pathlib import Path
import sys
from time import monotonic
import traceback

import numpy as np
import shapely
from scipy.sparse import coo_matrix, diags
from scipy.sparse.csgraph import breadth_first_order, connected_components
from scipy.sparse.linalg import splu

import assemble_astra_l25_rt0_resistance as local
import probe_astra_l25_rt0_p1_refined_pair as topology
import project_astra_l14_gc_mass as mass
import reconstruct_astra_native_loaded_field as recon
from prepare_astra_l02_rt0_current_space import sparse_arrays

CONDUCTANCE = 59.59e6 * 20e-6
CURRENT_SOURCE_SHA = '6c03c997c724da002e12bef0845a08cdae409f76d33428ab1bfba2f875a3a46f'
PAD_DOMAIN = Path(__file__).resolve().parents[2] / 'outputs/research/astra-l04-pad-conductor-domain-01/l04-pad-augmented-conductor-domain.wkb'
PINS = {
    PAD_DOMAIN: '0eeaffc34a6b9759fd285f28d35ebd59042e6bcd909a697fad3f18a5d2eafa68',
    Path(topology.__file__): '4e3f025b88b20633f5f64535c30c31c983d3e6344a231c07cc1cdeba06acbc33',
    Path(local.__file__): 'ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010',
    Path(mass.__file__): 'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3',
    Path(recon.__file__): '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213',
    Path(__file__).with_name('prepare_astra_l02_rt0_current_space.py'):
        'a5b8ecc5998a7ec9f5685a89eefaaa06cdc9c7382eb40de042ebac5a9b487d5e',
}


def tree_lift(first, second, target, root):
    """Satisfy independent Bq rows; retain the original dependent-row residual."""
    size, count = len(target), len(first)
    graph = coo_matrix((np.ones(2 * count),
        (np.r_[first, second], np.r_[second, first])), shape=(size, size)).tocsr()
    order, predecessor = breadth_first_order(graph, root, directed=False)
    assert len(order) == size and order[0] == root
    # Select one edge per child, including when the graph has parallel edges.
    child = np.where(predecessor[first] == second, first,
                     np.where(predecessor[second] == first, second, -1))
    candidates = np.flatnonzero(child >= 0)
    children, unique = np.unique(child[candidates], return_index=True)
    assert len(children) == size - 1 and root not in children
    parent_branch = np.full(size, -1, dtype=np.int64)
    parent_branch[children] = candidates[unique]
    subtotal = target.copy()
    q = np.zeros(count, dtype=np.complex128)
    for node in order[:0:-1]:
        edge = parent_branch[node]
        q[edge] = subtotal[node] * (1 if first[edge] == node else -1)
        subtotal[predecessor[node]] += subtotal[node]
    return q, parent_branch, subtotal[root]


def assemble_space(xy, triangles, contact_nodes, contact_triangles, count, budget):
    assert np.all(np.diff(triangles, axis=1) > 0)
    assert len(np.unique(contact_nodes)) == len(contact_nodes) == 16 * count
    assert len(np.unique(contact_triangles)) == len(contact_triangles) == 14 * count
    tags = np.full(len(triangles), -1, dtype=np.int32)
    tags[contact_triangles] = np.repeat(np.arange(count), 14)
    node_contact = np.full(len(xy), -1, dtype=np.int32)
    node_contact[contact_nodes] = np.repeat(np.arange(count), 16)
    assert np.all(node_contact[triangles[contact_triangles]] == tags[contact_triangles, None])
    space = topology._rebuild_topology(triangles, tags, electrode_count=count,
                                      expected_contact_degree=np.full(count, 16))
    free = space['free_triangle_indices']
    tri = triangles[free]
    ids, signs = space['local_facet_branch_index'], space['local_outward_flux_sign']
    edges = space['branch_mesh_edges']
    first, second = space['branch_first_node'], space['branch_second_node']
    ncell, nbranch = len(free), len(edges)
    branch = np.arange(nbranch)
    b = coo_matrix((np.r_[np.ones(nbranch), -np.ones(nbranch)],
        (np.r_[first, second], np.r_[branch, branch])), shape=(ncell + count, nbranch)).tocsr()
    assert np.max(abs(np.asarray(b.sum(axis=0))), initial=0) == 0
    present = ids >= 0
    d = coo_matrix((signs[present],
        (np.broadcast_to(np.arange(ncell)[:, None], ids.shape)[present], ids[present])),
        shape=(ncell, nbranch)).tocsr()
    assert (d - b[:ncell]).nnz == 0
    boundary = np.sort(np.concatenate([
        tri[~present[:, facet]][:, pair]
        for facet, pair in enumerate(([1, 2], [2, 0], [0, 1]))]), axis=1)
    assert len(np.unique(boundary, axis=0)) == len(boundary)
    assert np.all(node_contact[boundary] == -1)
    bg = coo_matrix((np.ones(2 * len(boundary)),
        (np.r_[boundary[:, 0], boundary[:, 1]], np.r_[boundary[:, 1], boundary[:, 0]])),
        shape=(len(xy), len(xy))).tocsr()
    degree = np.diff(bg.indptr)
    assert np.all(degree[contact_nodes] == 0) and np.all(degree[node_contact < 0] == 2)
    nstream, labels = connected_components(bg, directed=False)
    assert len(np.unique(labels[contact_nodes])) == len(contact_nodes)
    budget.check('existing topology and insulating boundary components')

    rows, columns, values = [], [], []
    orientation_sum = np.zeros(nbranch)
    multiplicity = np.bincount(ids[present], minlength=nbranch)
    max_matrix_error, min_eigenvalue = 0., np.inf
    for begin in range(0, ncell, 50000):
        end = min(begin + 50000, ncell)
        p = xy[tri[begin:end]].copy()
        p -= p[:, :1]
        p *= 1e-6
        block, det, area = local._batch_local_rt0(p, CONDUCTANCE)
        assert np.all(np.isfinite(block)) and np.all(area > 0)
        eig = np.linalg.eigvalsh(block)
        assert np.all(eig[:, 0] > 0)
        min_eigenvalue = min(min_eigenvalue, float(eig[:, 0].min()))
        samples = np.einsum('qi,nid->nqd', local.BARYCENTRIC, p)
        basis = (samples[:, :, None] - p[:, None]) / (2 * area[:, None, None, None])
        oracle = np.einsum('nqid,nqjd,n->nij', basis, basis, area / (3 * CONDUCTANCE))
        error = np.linalg.norm(block - oracle, axis=(1, 2)) / np.linalg.norm(block, axis=(1, 2))
        max_matrix_error = max(max_matrix_error, float(error.max()))
        assert max_matrix_error < 2e-12
        ix, sg = ids[begin:end], signs[begin:end]
        active = ix >= 0
        rr = np.broadcast_to(ix[:, :, None], block.shape)
        cc = np.broadcast_to(ix[:, None, :], block.shape)
        keep = active[:, :, None] & active[:, None, :]
        rows.append(rr[keep]); columns.append(cc[keep])
        values.append((sg[:, :, None] * block * sg[:, None, :])[keep])
        orientation = sg * np.sign(det)[:, None] * np.array([1, -1, 1])
        np.add.at(orientation_sum, ix[active], orientation[active])
        budget.check('centered RT0 resistance chunk ' + str(end))
    r = coo_matrix((np.concatenate(values), (np.concatenate(rows), np.concatenate(columns))),
                  shape=(nbranch, nbranch)).tocsr()
    r.sum_duplicates(); r.sort_indices()
    del rows, columns, values
    assert np.max(abs((r - r.T).data), initial=0) == 0
    assert np.all(abs(orientation_sum) == multiplicity)
    orientation = (orientation_sum / multiplicity).astype(np.int8)
    c_full = coo_matrix((np.r_[-orientation, orientation],
        (np.r_[branch, branch], np.r_[labels[edges[:, 0]], labels[edges[:, 1]]])),
        shape=(nbranch, nstream)).tocsr()
    c_full.sum_duplicates(); c_full.eliminate_zeros()
    assert (b @ c_full).nnz == 0
    qe = labels[edges]
    quotient = coo_matrix((np.ones(2 * len(qe)),
        (np.r_[qe[:, 0], qe[:, 1]], np.r_[qe[:, 1], qe[:, 0]])),
        shape=(nstream, nstream)).tocsr()
    assert connected_components(quotient, directed=False, return_labels=False) == 1
    assert nstream - 1 == nbranch - (ncell + count - 1)
    # Each insulating loop is constant; each contact-rim vertex remains free.
    c = c_full[:, 1:].tocsr()
    space.update(triangle_contact_index=tags, exterior_mesh_edges=boundary,
                 mesh_node_stream_index=labels, branch_stream_orientation=orientation)
    metrics = dict(stream_count=nstream, complete_closed_dimension=nstream - 1,
        insulating_loops=nstream - len(contact_nodes), all_local_matrix_relative=max_matrix_error,
        min_local_eigenvalue_ohm=min_eigenvalue,
        exterior_flux_condition='fixed zero; excluded before local resistance assembly')
    budget.check('complete constrained current space')
    return space, r, b, c, metrics


def minimum_current(r, b, c, first, second, target, root, budget, checkpoint):
    q0, parent_branch, root_sum = tree_lift(first, second, target, root)
    initial_error = b @ q0 - target
    independent = np.arange(len(target)) != root
    assert np.max(abs(initial_error[independent]), initial=0) < 1e-11
    assert abs(initial_error[root]) < 1e-10
    h = (c.T @ (r @ c)).tocsr()
    h.sum_duplicates(); h.eliminate_zeros()
    assert np.all(h.diagonal() > 0)
    assert np.max(abs((h - h.T).data), initial=0) / np.max(abs(h.data)) < 2e-12
    rhs = -(c.T @ (r @ q0))
    checkpoint(q0=q0, parent_branch=parent_branch, original_target_a=target,
               initial_constraint_residual_a=initial_error, rhs_v=rhs, **sparse_arrays(h, 'h'))
    scaling = 1 / np.sqrt(h.diagonal())
    scaled_h = (diags(scaling) @ h @ diags(scaling)).tocsc()
    scaled_rhs = scaling * rhs
    budget.check('saved particular current and stream system')
    start = monotonic()
    factor = splu(scaled_h, permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.,
                   options={'SymmetricMode': True})
    factor_seconds = monotonic() - start
    solved = factor.solve(np.column_stack((scaled_rhs.real, scaled_rhs.imag)))
    x = solved[:, 0] + 1j * solved[:, 1]
    for _ in range(2):
        residual = scaled_rhs - scaled_h @ x
        update = factor.solve(np.column_stack((residual.real, residual.imag)))
        x += update[:, 0] + 1j * update[:, 1]
    delta = c @ (scaling * x)
    q = q0 + delta
    rq = r @ q
    gradient = c.T @ rq
    before, after = np.vdot(q0, r @ q0), np.vdot(q, rq)
    error = b @ q - target
    equation_relative = float(np.linalg.norm(scaled_h @ x - scaled_rhs) / np.linalg.norm(scaled_rhs))
    energy_gradient_relative = float(np.linalg.norm(scaling * gradient) / np.sqrt(after.real))
    metrics = dict(factor_seconds=factor_seconds, factor_l_nnz=factor.L.nnz, factor_u_nnz=factor.U.nnz,
        gauge_contact_graph_row=int(root), dependent_equation='omitted; original target retained',
        tree_component_sum_a=recon._complex(root_sum), original_component_sum_a=recon._complex(target.sum()),
        root_constraint_residual_a=recon._complex(error[root]),
        independent_constraint_max_a=float(np.max(abs(error[independent]), initial=0)),
        correction_constraint_max_a=float(np.max(abs(b @ delta), initial=0)),
        scaled_equation_relative=equation_relative, energy_scaled_stationarity_relative=energy_gradient_relative,
        closed_gradient_max_v=float(np.max(abs(gradient), initial=0)),
        tree_joule_w=float(before.real), minimum_joule_w=float(after.real),
        bilinear_r_integral_a2_ohm=recon._complex(np.dot(q, rq)))
    # Save the numerical field even if qualification fails; do not rerun blindly.
    qualified = (equation_relative < 1e-9 and energy_gradient_relative < 2e-8
        and metrics['independent_constraint_max_a'] < 1e-10 and abs(error[root]) < 1e-10
        and metrics['correction_constraint_max_a'] < 1e-10 and 0 < after.real <= before.real * (1 + 1e-10))
    budget.check('conditional closed-stream current solved')
    return dict(branch_current_a=q, particular_tree_current_a=q0, stream_potential=np.r_[0, scaling * x],
        closed_current_correction_a=delta, closed_gradient_v=gradient,
        original_target_a=target, constraint_residual_a=error), metrics, qualified


def self_check():
    # A three-node ring with a parallel edge and a dependent-row roundoff residue.
    first, second = np.array([0, 1, 2, 0]), np.array([1, 2, 0, 1])
    target = np.array([1 + 2j, -0.25 - 0.5j, -0.75 - 1.5j + 1e-14])
    b = coo_matrix((np.r_[np.ones(4), -np.ones(4)],
        (np.r_[first, second], np.tile(np.arange(4), 2))), shape=(3, 4)).tocsr()
    c = coo_matrix(np.array([[1, 1], [1, 0], [1, 0], [0, -1]])).tocsr()
    r = diags([1., 2., 3., 4.]).tocsr()
    assert (b @ c).nnz == 0
    fields, metrics, accepted = minimum_current(r, b, c, first, second, target, 0,
        recon._Budget.create(10, 1), lambda **arrays: None)
    q = fields['branch_current_a']
    reduced = b.toarray()[1:]
    ri_bt = np.linalg.solve(r.toarray(), reduced.T)
    reference = ri_bt @ np.linalg.solve(reduced @ ri_bt, target[1:])
    assert accepted and np.max(abs(q - reference)) < 1e-14
    assert np.array_equal(fields['original_target_a'], target)
    assert abs(metrics['root_constraint_residual_a'][0]) > 1e-15
    print('PASS_FIXED_CONTACT_TREE_STREAM_DENSE_KKT_CHECK')


def verify(path, expected):
    assert recon._sha256_file(path) == expected, path
    return dict(path=str(path), sha256=expected, size_bytes=path.stat().st_size)


def worker(args):
    output = args.output.resolve()
    assert output.is_dir() and not (output / 'result.json').exists()
    frozen = output / 'driver-at-run.py'
    assert recon._sha256_file(frozen) == recon._sha256_file(Path(__file__))
    budget = recon._Budget.create(240, 12)
    try:
        inputs = {path.name: verify(path, digest) for path, digest in PINS.items()}
        inputs['mesh_result'] = verify(args.mesh_result, args.mesh_sha256)
        mesh_result = json.loads(args.mesh_result.read_bytes())
        inputs['mesh_guard'] = verify(args.mesh_result.parent / 'external-budget.json', args.mesh_guard_sha256)
        guard = json.loads((args.mesh_result.parent / 'external-budget.json').read_bytes())
        assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
        assert mesh_result['status'] == 'COMPLETED_CONDITIONAL_L04_RT0_MESH_QUALIFICATION'
        assert mesh_result['source_result']['sha256'] == 'bde2eb7c636276da737d0e6c91b0309603d9570899ccf5bbbfc2acffe7a74f96'
        approximation = mesh_result['geometry_approximation']
        assert approximation['method'] == 'EXACT_TWO_SOURCE_VERTEX_COALESCENCE_L04_RING563'
        assert approximation['unchanged_source_geometry'] is False
        assert 0 < approximation['max_vertex_displacement_um'] < 1.474e-10
        assert approximation['source_domain']['sha256'] == PINS[PAD_DOMAIN]
        witness_path = Path(approximation['witness_result']['path'])
        inputs['geometry_witness'] = verify(witness_path, approximation['witness_result']['sha256'])
        witness = json.loads(witness_path.read_bytes())
        assert witness['status'] == 'COMPLETED_CONDITIONAL_L04_LOCAL_COALESCENCE_WITNESS'
        assert witness['inputs']['pad_domain']['sha256'] == PINS[PAD_DOMAIN]
        coordinate_map = witness['coordinate_map']
        assert coordinate_map['ring_index'] == 563 and coordinate_map['ring_positions'] == [42, 44]
        assert coordinate_map['replacement_coordinate_um'] == [-12596.3, 13546.3]
        assert witness['removed_raw_triangle_indices'] == [807731, 976112]
        geometry_change = witness['local_geometry_change']
        assert geometry_change['changed_domain_wkb_sha256'] == approximation['domain']['sha256']
        assert geometry_change['max_vertex_displacement_um'] == approximation['max_vertex_displacement_um']
        conditional_domain_path = Path(approximation['domain']['path'])
        inputs['conditional_domain'] = verify(conditional_domain_path, approximation['domain']['sha256'])
        conditional_domain = shapely.from_wkb(conditional_domain_path.read_bytes())
        assert conditional_domain.is_valid and conditional_domain.geom_type == 'Polygon'
        mesh_path = Path(mesh_result['snapshot']['path'])
        inputs['mesh'] = verify(mesh_path, mesh_result['snapshot']['sha256'])
        inputs['source_current_result'] = verify(args.source_result, args.source_sha256)
        assert args.source_sha256 == CURRENT_SOURCE_SHA, 'fixed1MHz/1A source-current case'
        source = json.loads(args.source_result.read_bytes())
        assert source['status'] == 'COMPLETED_L04_FIXED_CONTACT_CURRENTS_SOURCE_AGGREGATION'
        source_path = Path(source['artifact']['path'])
        inputs['source_currents'] = verify(source_path, source['artifact']['sha256'])
        with np.load(mesh_path, allow_pickle=False) as z:
            xy, triangles = z['node_xy_um'], z['triangles']
            supports = z['contact_support_index']
            contact_nodes, contact_triangles = z['contact_node_indices'], z['contact_triangle_indices']
            assert len(supports) == 38278 and np.all(np.diff(supports) > 0)
            assert np.all(np.diff(z['contact_node_indptr']) == 16)
            assert np.all(np.diff(z['contact_triangle_indptr']) == 14)
        domain_bounds = np.asarray(shapely.from_wkb(PAD_DOMAIN.read_bytes()).bounds)
        assert np.array_equal(conditional_domain.bounds, domain_bounds)
        assert np.array_equal(np.r_[xy.min(axis=0), xy.max(axis=0)], domain_bounds), 'source/mesh micrometre bounds'
        with np.load(source_path, allow_pickle=False) as z:
            group_drill = z['group_to_drill_support_index']
            group_outward = z['coincident_group_current_outward_from_l04_sheet_a']
            group_component = z['group_to_component_index']
        assert group_drill.shape == group_outward.shape == group_component.shape == supports.shape
        source_order = np.argsort(group_drill)
        assert np.array_equal(group_drill[source_order], supports)
        assert np.all(group_component == 0) and np.all(np.isfinite(group_outward))
        outward = group_outward[source_order]
        assert abs(outward.sum()) < 1e-10
        budget.check('pinned conditional mesh and source-contact currents')
        space, r, b, c, metrics = assemble_space(xy, triangles, contact_nodes, contact_triangles, len(supports), budget)
        ncell = len(space['free_triangle_indices'])
        target = np.r_[np.zeros(ncell, dtype=complex), -outward]
        root = ncell + int(np.argmax(abs(outward)))
        space_path = output / 'l04-fixed-contact-rt0-space.npz'
        mass.atomic_npz(space_path, **space, **sparse_arrays(r, 'r'), **sparse_arrays(b, 'distributional_b'),
            contact_support_index=supports, contact_source_group_index=source_order,
            contact_current_outward_from_sheet_a=outward, original_target_a=target)
        system_path = output / 'l04-fixed-contact-stream-system.npz'

        def checkpoint(**arrays):
            mass.atomic_npz(system_path, **arrays)
            mass.atomic_json(output / 'prepared.json', dict(program='SPD Decap PI Evaluator', version='0.23.1',
                status='PREPARED_CONDITIONAL_L04_FIXED_CONTACT_STREAM_SYSTEM', inputs=inputs,
                geometry_approximation=approximation,
                driver_sha256=recon._sha256_file(frozen), counts=dict(mesh_nodes=len(xy),
                    free_triangles=ncell, current_branches=r.shape[0], contacts=len(supports)),
                topology=metrics, space=verify(space_path, recon._sha256_file(space_path)),
                system=verify(system_path, recon._sha256_file(system_path)), budget=budget.receipt()))
            print(json.dumps(dict(phase='saved_stream_system', dimension=c.shape[1])), flush=True)

        fields, solve_metrics, qualified = minimum_current(r, b, c, space['branch_first_node'],
            space['branch_second_node'], target, root, budget, checkpoint)
        field_path = output / 'l04-fixed-contact-current.npz'
        mass.atomic_npz(field_path, **fields)
        # Independent degree-two integration of the actual saved RT0 current.
        quad_energy = 0j
        free, ids, signs = (space['free_triangle_indices'], space['local_facet_branch_index'],
                            space['local_outward_flux_sign'])
        q = fields['branch_current_a']
        for begin in range(0, len(free), 50000):
            end = min(begin + 50000, len(free))
            p = xy[triangles[free[begin:end]]].copy()
            p -= p[:, :1]
            p *= 1e-6
            ix, sg = ids[begin:end], signs[begin:end]
            local_q = np.zeros(ix.shape, dtype=complex)
            active = ix >= 0
            local_q[active] = sg[active] * q[ix[active]]
            quad_energy += local._quadrature_energy(p, local_q[:, :, None], CONDUCTANCE)[0]
            budget.check('actual current independent quadrature ' + str(end))
        energy_error = float(abs(quad_energy - np.vdot(q, r @ q)) / abs(quad_energy))
        qualified = qualified and energy_error < 1e-10
        metrics.update(solve_metrics, source_and_mesh_bounds_um=domain_bounds.tolist(),
                       independent_quadrature_joule_w=float(quad_energy.real),
                       independent_quadrature_relative=energy_error)
        budget.check('saved conditional field and final gates')
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status=('PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM' if qualified else
                    'INCOMPLETE_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'),
            driver_sha256=recon._sha256_file(frozen), inputs=inputs,
            frequency_hz=1e6, source_current_a=1.,
            geometry_approximation=approximation,
            inherited_source_field_sha256='960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b',
            metrics=metrics, field=verify(field_path, recon._sha256_file(field_path)),
            space=verify(space_path, recon._sha256_file(space_path)),
            stream_system=verify(system_path, recon._sha256_file(system_path)), budget=budget.receipt(),
            scope='Conditional homogeneous20um Cu/59.59MS/m sheet with no cell G/C loads and zero '
                  'exterior-normal current. Explicit local ring563 vertex coalescence is retained '
                  'as a geometry approximation. Existing16-sided filled equipotential drill contacts; '
                  'contact interiors have no reconstructed current field. Every original1MHz source '
                  'contact drive remains saved. One dependent equation is omitted and its residual '
                  'reported without renormalization. Complete closed-stream minimum, not a new '
                  'coupled circuit response, derivative of the collapsed-L04 circuit, 3D conductor '
                  'model, mesh convergence, full return path or PowerSI accuracy acceptance. '
                  'The bilinear R form is a fixed-current diagnostic, not a finite impedance correction.')
        mass.atomic_json(output / 'result.json', report)
        print(json.dumps(dict(status=report['status'], metrics=metrics, budget=report['budget'])), flush=True)
        return 0 if qualified else 2
    except BaseException:
        mass.atomic_json(output / 'failure.json', dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='STOP_CONDITIONAL_L04_FIXED_CONTACT_STREAM', traceback=traceback.format_exc(), budget=budget.receipt()))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--native-worker', action='store_true')
    parser.add_argument('--mesh-result', type=Path)
    parser.add_argument('--mesh-sha256')
    parser.add_argument('--mesh-guard-sha256')
    parser.add_argument('--source-result', type=Path)
    parser.add_argument('--source-sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        if any(value is None for value in (args.mesh_result, args.mesh_sha256, args.mesh_guard_sha256,
                                           args.source_result, args.source_sha256, args.output)):
            parser.error('pinned mesh/result/guard, current-source result and fresh output required')
        if args.native_worker:
            raise SystemExit(worker(args))
        import probe_astra_fmm3d_runtime as guard
        assert recon._sha256_file(Path(guard.__file__)) == '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'
        args.output = args.output.resolve()
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', *sys.argv[1:]]
        raise SystemExit(guard.guarded_source_worker(args.output, worker_command=command, max_runtime_s=300))
