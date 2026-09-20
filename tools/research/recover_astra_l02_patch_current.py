"""Recover a conservative RT0 lift of the saved L02 P1 field by vertex fans.

Each fan needs cumulative divergence loads and at most one scalar minimum.
The legacy field's insulating boundary is used only for this one lift.
All exterior-current DOFs remain in the referenced full current space.
"""
import argparse
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import csr_matrix

import reconstruct_astra_native_loaded_field as recon
import prepare_astra_l02_cell_gc_loads as loads

ROOT = Path(__file__).resolve().parents[2]
PINS = {k: loads.PINS[k] for k in ('mesh', 'space')}
PINS.update({
    'loads': (ROOT / 'outputs/research/astra-l02-cell-gc-loads-01/l02-cell-gc-loads.npz',
              '12ab38f5d692c442a18d6ec7df913c6d96108be73850f27c7014f6f26c9c2459'),
    'loads_helper': (Path(loads.__file__),
                     'f34fd8cd276f10a5b4ee37b48b256238cea87b90172d03d2b748f65f1d020c8f'),
})
CONDUCTANCE = 59.59e6 * 20e-6


def sparse(archive, prefix):
    return csr_matrix((archive[prefix+'_data'], archive[prefix+'_indices'], archive[prefix+'_indptr']),
                      shape=tuple(archive[prefix+'_shape']))


def run(output):
    start = monotonic()
    budget = recon._Budget.create(180, 4)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for name, (path, digest) in PINS.items():
            assert recon._sha256_file(path) == digest, name
        with np.load(PINS['mesh'][0]) as mesh:
            xy, triangles = mesh['node_xy_um'], mesh['triangles']
            node_contact = np.full(len(xy), -1, dtype=np.int32)
            node_contact[mesh['contact_node_indices']] = np.repeat(np.arange(38856), 16)
        with np.load(PINS['space'][0]) as space:
            free = space['free_triangle_indices']
            ids, signs = space['local_facet_branch_index'], space['local_outward_flux_sign']
            edges = space['branch_mesh_edges']
            first, second = space['branch_first_node'], space['branch_second_node']
            exterior = space['retained_exterior_branch_indices']
            d, b = sparse(space, 'd'), sparse(space, 'distributional_b')
        with np.load(PINS['loads'][0]) as saved:
            all_patch_load = saved['p1_vertex_patch_divergence_load_a']
            target = all_patch_load[free].ravel()
            f = saved['p1_local_gc_injection_a'][free].sum(axis=1)
            current = saved['p1_cell_current_a_per_m'][free]
            areas = saved['cell_area_m2'][free]
            contact_gc = saved['contact_interior_gc_outgoing_current_a']
        tri = triangles[free]
        ncell, nbranch = len(tri), len(edges)
        ncorner = 3*ncell
        assert ncell == 1583840 and nbranch == 3095567
        p = xy[tri]
        v = p - p[:, :1]
        ccw = v[:, 1, 0]*v[:, 2, 1] - v[:, 1, 1]*v[:, 2, 0] > 0
        vertex = tri.ravel()
        angle_vector = v.mean(axis=1)[:, None] - v
        angle = np.arctan2(angle_vector[:, :, 1], angle_vector[:, :, 0]).ravel()
        del p, v, angle_vector
        slots = np.arange(3)[None]
        previous_facet = (slots + np.where(ccw, 2, 1)[:, None]) % 3
        next_facet = (slots + np.where(ccw, 1, 2)[:, None]) % 3
        previous_branch = np.take_along_axis(ids, previous_facet, axis=1).ravel()
        next_branch = np.take_along_axis(ids, next_facet, axis=1).ravel()
        previous_sign = np.take_along_axis(signs, previous_facet, axis=1).ravel()
        next_sign = np.take_along_axis(signs, next_facet, axis=1).ravel()
        assert np.all((edges[previous_branch, 0] == vertex) | (edges[previous_branch, 1] == vertex))
        assert np.all((edges[next_branch, 0] == vertex) | (edges[next_branch, 1] == vertex))
        previous_key = 2*previous_branch + (edges[previous_branch, 1] == vertex)
        next_key = 2*next_branch + (edges[next_branch, 1] == vertex)
        assert len(np.unique(previous_key)) == len(np.unique(next_key)) == ncorner
        lookup = np.full(2*nbranch, -1, dtype=np.int64)
        lookup[previous_key] = np.arange(ncorner)
        next_corner = lookup[next_key]
        lookup.fill(-1)
        lookup[next_key] = np.arange(ncorner)
        previous_corner = lookup[previous_key]
        del lookup, previous_key, next_key
        present = next_corner >= 0
        assert np.array_equal(vertex[present], vertex[next_corner[present]])
        assert np.array_equal(previous_corner[next_corner[present]], np.flatnonzero(present))

        # Geometric angle orders each fan; actual shared-edge links certify it.
        order = np.lexsort((angle, vertex))
        del angle
        starts = np.r_[0, np.flatnonzero(np.diff(vertex[order])) + 1]
        sizes = np.diff(np.r_[starts, ncorner])
        patch_nodes = vertex[order[starts]]
        groups = np.repeat(np.arange(len(starts)), sizes)
        rank = np.arange(ncorner) - starts[groups]
        begins = np.flatnonzero(previous_corner[order] < 0)
        ends = np.flatnonzero(next_corner[order] < 0)
        begin_counts = np.bincount(groups[begins], minlength=len(starts))
        end_counts = np.bincount(groups[ends], minlength=len(starts))
        assert np.array_equal(begin_counts, end_counts) and np.all(begin_counts <= 1), 'multiple vertex fans'
        rotation = np.zeros(len(starts), dtype=np.int64)
        rotation[groups[begins]] = rank[begins]
        order = order[starts[groups] + (rank + rotation[groups]) % sizes[groups]]
        is_path = begin_counts == 1
        ends = starts + sizes - 1
        expected_next = np.roll(order, -1)
        expected_next[ends] = np.where(is_path, -1, order[starts])
        assert np.array_equal(next_corner[order], expected_next), 'angle order disagrees with actual topology'
        expected_previous = np.roll(order, 1)
        expected_previous[starts] = np.where(is_path, -1, order[ends])
        assert np.array_equal(previous_corner[order], expected_previous)
        assert np.array_equal(vertex[order], patch_nodes[groups])
        branch_kind = np.zeros(nbranch, dtype=np.int8)
        branch_kind[((first >= ncell) & (first < ncell+38856)) |
                    ((second >= ncell) & (second < ncell+38856))] = 1
        branch_kind[exterior] = 2
        patch_kind = np.zeros(len(starts), dtype=np.int8)
        patch_kind[is_path] = branch_kind[previous_branch[order[starts[is_path]]]]
        assert np.array_equal(patch_kind[is_path], branch_kind[next_branch[order[ends[is_path]]]])
        assert np.all(patch_kind[is_path] > 0)
        assert np.all(node_contact[patch_nodes[patch_kind == 1]] >= 0)
        assert np.all(node_contact[patch_nodes[patch_kind != 1]] < 0)
        del previous_corner, next_corner, expected_next, expected_previous, rank, rotation, vertex
        budget.check('actual vertex-fan topology')
        print(json.dumps(dict(phase='fan_topology', patches=len(starts),
            cycles=int(np.sum(patch_kind == 0)), contact_paths=int(np.sum(patch_kind == 1)),
            insulating_paths=int(np.sum(patch_kind == 2)), max_cells_per_patch=int(sizes.max()))), flush=True)

        ordered_load = target[order]
        cumulative = np.cumsum(ordered_load)
        base = cumulative[starts] - ordered_load[starts]
        cumulative -= base[groups]
        previous = cumulative - ordered_load
        previous[starts] = 0
        patch_total = np.add.reduceat(ordered_load, starts)
        compatible = patch_kind != 1
        compatibility_max = float(abs(patch_total[compatible]).max(initial=0))
        assert compatibility_max < 1e-9
        cnext, cprevious = np.empty(ncorner, dtype=np.complex128), np.empty(ncorner, dtype=np.complex128)
        cnext[order], cprevious[order] = cumulative, previous
        group_of_corner = np.empty(ncorner, dtype=np.int64)
        group_of_corner[order] = groups
        del cumulative, previous, base, ordered_load, target
        h = np.zeros(len(starts))
        linear = np.zeros(len(starts), dtype=np.complex128)
        for begin in range(0, ncell, 50000):
            end = min(begin+50000, ncell)
            p = xy[tri[begin:end]]
            p = (p-p[:, :1])*1e-6
            center = p.mean(axis=1)[:, None]
            pv = np.take_along_axis(p, previous_facet[begin:end, :, None], axis=1)
            nv = np.take_along_axis(p, next_facet[begin:end, :, None], axis=1)
            tangent = p[:, [2, 0, 1]] - p[:, [1, 2, 0]]
            normals = np.stack((tangent[:, :, 1], -tangent[:, :, 0]), axis=2)
            normals *= np.where(ccw[begin:end], 1., -1.)[:, None, None]
            q0 = .5*np.einsum('nid,nd->ni', normals, current[begin:end])
            qprevious = np.take_along_axis(q0, previous_facet[begin:end], axis=1)
            qnext = np.take_along_axis(q0, next_facet[begin:end], axis=1)
            cp = cprevious[3*begin:3*end].reshape(-1, 3)
            cn = cnext[3*begin:3*end].reshape(-1, 3)
            scale = 2*areas[begin:end, None, None]
            null_j = (pv-nv)/scale
            delta_j = ((-cp-qprevious)[:, :, None]*(center-pv) +
                       (cn-qnext)[:, :, None]*(center-nv))/scale
            local_h = areas[begin:end, None]/CONDUCTANCE*np.sum(null_j**2, axis=2)
            local_b = areas[begin:end, None]/CONDUCTANCE*np.sum(null_j*delta_j, axis=2)
            index = group_of_corner[3*begin:3*end]
            h += np.bincount(index, weights=local_h.ravel(), minlength=len(h))
            linear += (np.bincount(index, weights=local_b.real.ravel(), minlength=len(h)) +
                       1j*np.bincount(index, weights=local_b.imag.ravel(), minlength=len(h)))
            budget.check('one-scalar fan minimization')
        assert np.all(h > 0) and np.all(np.isfinite(h)) and np.all(np.isfinite(linear))
        alpha = -linear/h
        alpha[patch_kind == 2] = 0
        q = np.zeros(nbranch, dtype=np.complex128)
        outgoing = cnext + alpha[group_of_corner]
        # Enforce the legacy insulating lift exactly; record resulting roundoff residuals.
        outgoing[branch_kind[next_branch] == 2] = 0
        np.add.at(q, next_branch, next_sign*outgoing)
        path_start = order[starts[is_path]]
        np.add.at(q, previous_branch[path_start],
                  -previous_sign[path_start]*alpha[is_path])
        assert np.all(q[exterior] == 0)
        del outgoing, cnext, cprevious, next_branch, previous_branch, next_sign, previous_sign
        divergence = d @ q
        divergence_error = float(abs(divergence-f).max())
        assert divergence_error < 1e-9
        boundary = b @ q
        recovered_contact = boundary[ncell:ncell+38856] + contact_gc
        tagged = node_contact[triangles]
        selected = tagged >= 0
        expected_contact = (np.bincount(tagged[selected], weights=-all_patch_load.real[selected], minlength=38856)
                            + 1j*np.bincount(tagged[selected], weights=-all_patch_load.imag[selected], minlength=38856))
        contact_error = float(abs(recovered_contact-expected_contact).max())
        assert contact_error < 1e-9
        del all_patch_load, tagged, selected

        p1_energy, recovered_energy, difference_energy = 0., 0., 0.
        for begin in range(0, ncell, 50000):
            end = min(begin+50000, ncell)
            p = xy[tri[begin:end]]
            p = (p-p[:, :1])*1e-6
            center = p.mean(axis=1)
            local_q = signs[begin:end]*q[ids[begin:end]]
            jc = np.einsum('ni,nid->nd', local_q, center[:, None]-p)/(2*areas[begin:end, None])
            radial = local_q.sum(axis=1)/(2*areas[begin:end])
            variance = np.sum((p-center[:, None])**2, axis=(1, 2))/12
            weight = areas[begin:end]/CONDUCTANCE
            p1_energy += float(np.sum(weight*np.sum(abs(current[begin:end])**2, axis=1)))
            recovered_energy += float(np.sum(weight*(np.sum(abs(jc)**2, axis=1)+abs(radial)**2*variance)))
            difference_energy += float(np.sum(weight*(np.sum(abs(jc-current[begin:end])**2, axis=1)+abs(radial)**2*variance)))
            budget.check('actual affine-current difference')
        assert min(p1_energy, recovered_energy, difference_energy) >= 0
        target_path = output / 'l02-recovered-patch-current.npz'
        np.savez_compressed(target_path, branch_current_a=q, cell_divergence_a=divergence,
            cell_gc_injection_a=f, recovered_contact_total_outgoing_a=recovered_contact,
            saved_p1_contact_total_outgoing_a=expected_contact, patch_mesh_vertex=patch_nodes,
            patch_kind=patch_kind, patch_cycle_current_a=alpha, patch_divergence_sum_a=patch_total)
        budget.check('saved conservative lift')
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_CONDITIONAL_SAVED_L02_P1_VERTEX_PATCH_RT0_LIFT',
            input_sha256={k: dict(path=str(p), sha256=s) for k, (p, s) in PINS.items()},
            driver_sha256=recon._sha256_file(Path(__file__)), artifact_sha256=recon._sha256_file(target_path),
            elapsed_s=monotonic()-start, budget=budget.receipt(),
            counts=dict(patches=len(starts), cycles=int(np.sum(patch_kind == 0)),
                        contact_paths=int(np.sum(patch_kind == 1)), insulating_paths=int(np.sum(patch_kind == 2)),
                        max_cells_per_patch=int(sizes.max()), current_branches=nbranch),
            metrics=dict(compatible_patch_load_max_a=compatibility_max,
                cell_divergence_max_difference_a=divergence_error,
                electrode_total_current_max_difference_a=contact_error,
                insulating_exterior_max_a=float(abs(q[exterior]).max()),
                saved_p1_joule_w=p1_energy, recovered_joule_w=recovered_energy,
                recovered_minus_p1_r_norm_relative=float(np.sqrt(difference_energy/p1_energy))),
            scope='One source-coupled old L02-only P1 field lifted into the complete saved RT0 '
                  'space. Actual vertex-fan adjacency, all cell GC divergence and all38856 '
                  'electrode totals (including electrode-interior GC) are checked. Flux is '
                  'single-valued on shared faces. Every exterior flux column remains available; '
                  'zero exterior flux belongs only to this legacy insulating trial field. '
                  'No global minimum-R solve, Green action, new circuit response, 3D contact '
                  'model, discretization convergence or PowerSI accuracy qualification.')
        (output / 'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2), flush=True)
    except Exception:
        (output / 'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc()), indent=2))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
