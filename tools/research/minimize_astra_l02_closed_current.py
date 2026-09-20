"""Minimize a saved lift over the complete 2D closed-current stream space."""
import argparse
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import coo_matrix, diags
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import cg

import reconstruct_astra_native_loaded_field as recon
import recover_astra_l02_patch_current as source
from prepare_astra_l02_rt0_current_space import sparse_arrays

ROOT = Path(__file__).resolve().parents[2]
PINS = dict(source.PINS)
PINS.update({
    'current': (ROOT / 'outputs/research/astra-l02-patch-current-01/l02-recovered-patch-current.npz',
                'e4d8832fedd5e9eb3bd5f612fe84ec610160e7a5fe6581bf9e18fb3304d5e7d5'),
    'current_helper': (Path(source.__file__),
                       '6c71a74445f4828b890377f9a928838a36b279098449c522671048b2d8e24e45'),
})


def run(output):
    start = monotonic()
    budget = recon._Budget.create(180, 4)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for name, (path, digest) in PINS.items():
            assert recon._sha256_file(path) == digest, name
        with np.load(PINS['mesh'][0]) as z:
            xy, triangles = z['node_xy_um'], z['triangles']
            contact_nodes = z['contact_node_indices']
        with np.load(PINS['space'][0]) as z:
            free = z['free_triangle_indices']
            ids, signs = z['local_facet_branch_index'], z['local_outward_flux_sign']
            edges = z['branch_mesh_edges']
            exterior = z['retained_exterior_branch_indices']
            first, second = z['branch_first_node'], z['branch_second_node']
            r, d, b = source.sparse(z, 'r'), source.sparse(z, 'd'), source.sparse(z, 'distributional_b')
        with np.load(PINS['current'][0]) as z:
            q = z['branch_current_a']
            f = z['cell_gc_injection_a']
        ncell, nbranch, ncontact = len(free), len(edges), 38856
        boundary_edges = edges[exterior]
        boundary_graph = coo_matrix((np.ones(2*len(exterior)),
            (np.r_[boundary_edges[:, 0], boundary_edges[:, 1]],
             np.r_[boundary_edges[:, 1], boundary_edges[:, 0]])), shape=(len(xy), len(xy))).tocsr()
        degree = np.diff(boundary_graph.indptr)
        is_contact = np.zeros(len(xy), dtype=bool)
        is_contact[contact_nodes] = True
        assert np.all(degree[is_contact] == 0) and np.all(degree[~is_contact] == 2)
        nstream, labels = connected_components(boundary_graph, directed=False)
        del boundary_graph, degree
        assert len(np.unique(labels[contact_nodes])) == len(contact_nodes)
        assert not np.intersect1d(labels[is_contact], labels[~is_contact]).size
        orientation_sum = np.zeros(nbranch)
        counts = np.bincount(ids.ravel(), minlength=nbranch)
        tri = triangles[free]
        for begin in range(0, ncell, 50000):
            end = min(begin+50000, ncell)
            p = xy[tri[begin:end]]
            det = ((p[:, 1, 0]-p[:, 0, 0])*(p[:, 2, 1]-p[:, 0, 1]) -
                   (p[:, 1, 1]-p[:, 0, 1])*(p[:, 2, 0]-p[:, 0, 0]))
            assert np.all(np.diff(tri[begin:end], axis=1) > 0)
            local_orientation = signs[begin:end]*np.sign(det)[:, None]*np.array([1, -1, 1])
            np.add.at(orientation_sum, ids[begin:end].ravel(), local_orientation.ravel())
        orientation = orientation_sum/counts
        assert np.all(abs(orientation) == 1)
        branch = np.arange(nbranch)
        c = coo_matrix((np.r_[-orientation, orientation],
            (np.r_[branch, branch], np.r_[labels[edges[:, 0]], labels[edges[:, 1]]])),
            shape=(nbranch, nstream)).tocsr()
        c.sum_duplicates()
        c.eliminate_zeros()
        assert c[exterior].nnz == 0
        assert (d@c).nnz == 0 and (b[ncell:]@c).nnz == 0
        quotient_graph = abs(c.T)@abs(c)
        assert connected_components(quotient_graph, directed=False, return_labels=False) == 1
        del quotient_graph
        retained = np.ones(nbranch, dtype=bool)
        retained[exterior] = False
        graph = coo_matrix((np.ones(2*retained.sum()),
            (np.r_[first[retained], second[retained]], np.r_[second[retained], first[retained]])),
            shape=(ncell+ncontact, ncell+ncontact)).tocsr()
        assert connected_components(graph, directed=False, return_labels=False) == 1
        del graph
        nullity = int(retained.sum())-(ncell+ncontact-1)
        assert nstream-1 == nullity, 'stream columns do not span the entire closed subspace'
        # A constant stream function produces zero current; fix only that gauge.
        c = c[:, 1:].tocsr()
        h = (c.T@(r@c)).tocsr()
        h.sum_duplicates()
        h.eliminate_zeros()
        scale = float(abs(h.data).max())
        symmetry = float(abs((h-h.T).data).max(initial=0)/scale)
        assert symmetry < 2e-12 and np.all(h.diagonal() > 0)
        rhs = -(c.T@(r@q))
        before = np.vdot(q, r@q)
        original_boundary = b@q
        budget.check('complete closed space and coupled resistance')
        matrix_path = output/'closed-stream-system.npz'
        np.savez_compressed(matrix_path, **sparse_arrays(h, 'h'), rhs_a=rhs,
            mesh_node_stream_index=labels, branch_stream_orientation=orientation.astype(np.int8),
            stream_gauge_index=np.array([0]))
        prepared = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PREPARED_COMPLETE_L02_CLOSED_STREAM_SYSTEM',
            input_sha256={k: dict(path=str(p), sha256=s) for k, (p, s) in PINS.items()},
            driver_sha256=recon._sha256_file(Path(__file__)),
            matrix_sha256=recon._sha256_file(matrix_path),
            stream_count=nstream, closed_dimension=nullity,
            insulating_boundary_components=int(nstream-len(contact_nodes)),
            h_nnz=h.nnz, c_nnz=c.nnz, symmetry_relative=symmetry,
            complete_by_exact_incidence_connectedness_and_dimension=True,
            preparation_s=monotonic()-start)
        (output/'prepared.json').write_text(json.dumps(prepared, indent=2)+'\n')
        print(json.dumps(prepared), flush=True)
        # One bounded existing SciPy CG, with diagonal scaling; no new solver framework.
        inverse_root = 1/np.sqrt(h.diagonal())
        diagonal = diags(inverse_root)
        scaled = (diagonal@h@diagonal).tocsr()
        scaled_rhs = inverse_root*rhs
        rhs_norm = np.linalg.norm(scaled_rhs)
        iteration = 0
        last_x = None
        def observe(x):
            nonlocal iteration, last_x
            iteration += 1
            last_x = x
            budget.check('CG '+str(iteration))
            if monotonic()-start > 150:
                raise StopIteration
            if iteration % 100 == 0:
                residual = float(np.linalg.norm(scaled@x-scaled_rhs)/rhs_norm)
                print(json.dumps(dict(phase='cg', iteration=iteration, relative_residual=residual,
                                      elapsed_s=monotonic()-start)), flush=True)
        try:
            x, info = cg(scaled, scaled_rhs, rtol=1e-9, atol=0, maxiter=2000, callback=observe)
        except StopIteration:
            assert last_x is not None
            x, info = last_x, iteration
        delta = c@(inverse_root*x)
        q += delta
        after = np.vdot(q, r@q)
        actual_gradient = c.T@(r@q)
        true_residual = float(np.linalg.norm(inverse_root*actual_gradient)/rhs_norm)
        divergence_error = float(abs(d@q-f).max())
        boundary_error = float(abs((b@q-original_boundary)[ncell:]).max())
        assert divergence_error < 1e-9 and boundary_error < 1e-9
        assert np.all(q[exterior] == 0) and after.real <= before.real*(1+1e-10)
        accepted = info == 0 and true_residual < 2e-8
        current_path = output/'closed-minimized-current.npz'
        np.savez_compressed(current_path, branch_current_a=q, closed_current_correction_a=delta,
                            stream_potential=np.r_[0, inverse_root*x], cg_scaled_solution=x)
        budget.check('saved closed-current result')
        report = dict(prepared)
        report.update(status=('PASS_COMPLETE_L02_CLOSED_RT0_CURRENT_MINIMIZATION' if accepted else
                              'INCOMPLETE_L02_CLOSED_CURRENT_CG_DIAGNOSTIC'),
            current_sha256=recon._sha256_file(current_path), elapsed_s=monotonic()-start,
            budget=budget.receipt(), cg_info=int(info), iterations=iteration,
            metrics=dict(old_joule_w=float(before.real), minimized_joule_w=float(after.real),
                         energy_ratio=float(after.real/before.real), true_scaled_gradient_relative=true_residual,
                         cell_divergence_max_error_a=divergence_error,
                         electrode_and_exterior_max_change_a=boundary_error),
            scope='Complete same-mesh closed RT0 correction with fixed saved cell GC and electrode '
                  'totals, including circulation around every insulating hole. Rank follows from '
                  'exact D/B annihilation, connected incidence graphs and matching nullity. '
                  'CG stationarity is reported explicitly. No new board field, magnetic action, '
                  '3D contact model, mesh convergence or PowerSI accuracy qualification.')
        (output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2), flush=True)
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc()), indent=2))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
