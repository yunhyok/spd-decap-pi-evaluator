"""Build the saved L02 sheet's RT0 R/D and retain every exterior current edge.

Reuses the qualified L25 local resistance formula on the existing L02 mesh.
Contact interiors keep the existing aggregate equipotential approximation.
Exterior flux columns terminate on separate boundary supports, not ground.
No mesh generation, circuit solve, Green action or frequency claim.
"""
import argparse
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components

import assemble_astra_l25_rt0_resistance as local
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/assemble_astra_l25_rt0_resistance.py':
        'ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010',
    'outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz':
        '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9',
    'outputs/research/astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz':
        '61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020',
}
CONDUCTANCE = 59.59e6 * 20e-6


def sparse_arrays(matrix, prefix):
    matrix = matrix.tocsr()
    return {prefix + '_data': matrix.data, prefix + '_indices': matrix.indices,
            prefix + '_indptr': matrix.indptr, prefix + '_shape': np.array(matrix.shape)}


def run(output):
    started = monotonic()
    budget = recon._Budget.create(120, 4)
    assert not output.exists()
    output.mkdir(parents=True)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path, expected in PINS.items():
            assert recon._sha256_file(ROOT / path) == expected, path
        mesh_path = ROOT / 'outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz'
        with np.load(mesh_path) as mesh:
            xy, triangles = mesh['node_xy_um'], mesh['triangles']
            contact_triangles = mesh['contact_triangle_indices']
            tri_ptr, node_ptr = mesh['contact_triangle_indptr'], mesh['contact_node_indptr']
            contact_nodes, supports = mesh['contact_node_indices'], mesh['contact_support_index']
        contacts = len(supports)
        assert xy.shape == (1439614, 2) and triangles.shape == (2127824, 3)
        assert contacts == 38856 and np.all(np.diff(tri_ptr) == 14)
        assert np.all(np.diff(node_ptr) == 16)
        assert len(np.unique(contact_triangles)) == len(contact_triangles) == 543984
        assert len(np.unique(contact_nodes)) == len(contact_nodes) == contacts * 16
        tag = np.full(len(triangles), -1, dtype=np.int32)
        tag[contact_triangles] = np.repeat(np.arange(contacts), 14)
        node_contact = np.full(len(xy), -1, dtype=np.int32)
        node_contact[contact_nodes] = np.repeat(np.arange(contacts), 16)
        assert np.all(node_contact[triangles[contact_triangles]] == tag[contact_triangles, None])
        free = np.flatnonzero(tag < 0)
        nfree = len(free)
        assert nfree == 1583840
        potential = np.empty(len(triangles), dtype=np.int64)
        potential[free] = np.arange(nfree)
        potential[tag >= 0] = nfree + tag[tag >= 0]
        budget.check('mesh and contact identity')

        # Same opposite-vertex facet convention as the qualified L25 topology.
        edges = np.sort(np.concatenate([triangles[:, [1, 2]], triangles[:, [2, 0]],
                                        triangles[:, [0, 1]]]), axis=1)
        unique, inverse, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        del edges
        assert np.all((counts == 1) | (counts == 2))
        order = np.argsort(inverse, kind='stable')
        starts = np.r_[0, np.cumsum(counts[:-1])]
        first_cell = order[starts] % len(triangles)
        second_cell = order[starts + counts - 1] % len(triangles)
        first_p, second_p = potential[first_cell], potential[second_cell]
        outside = counts == 1
        assert np.all(first_p[outside] < nfree), 'contact disk touches external boundary'
        assert np.all((first_p[counts == 2] < nfree) |
                      (second_p[counts == 2] < nfree) |
                      (first_p[counts == 2] == second_p[counts == 2]))
        kept = outside | (first_p != second_p)
        kept_ids = np.flatnonzero(kept)
        branch_index = np.full(len(unique), -1, dtype=np.int64)
        branch_index[kept] = np.arange(len(kept_ids))
        first, second = first_p[kept].copy(), second_p[kept].copy()
        exterior = np.flatnonzero(outside[kept])
        second[exterior] = nfree + contacts + np.arange(len(exterior))
        nnode = nfree + contacts + len(exterior)
        nbranch = len(first)
        branch_edges = unique[kept]
        facet_ids = inverse.reshape(3, len(triangles)).T[free]
        local_branch = branch_index[facet_ids]
        assert np.all(local_branch >= 0), 'a free-cell facet was dropped'
        free_rows = np.arange(nfree)[:, None]
        signs = np.where(first[local_branch] == free_rows, 1, -1).astype(np.int8)
        assert np.all((first[local_branch] == free_rows) | (second[local_branch] == free_rows))
        for sign, owner in [(1, first), (-1, second)]:
            support_count = np.bincount(local_branch[signs == sign], minlength=nbranch)
            assert np.array_equal(support_count, (owner < nfree).astype(int))
        branch = np.arange(nbranch)
        incidence = coo_matrix((np.r_[np.ones(nbranch), -np.ones(nbranch)],
                               (np.r_[first, second], np.r_[branch, branch])),
                              shape=(nnode, nbranch)).tocsr()
        assert np.max(abs(np.asarray(incidence.sum(axis=0)).ravel())) == 0
        divergence = incidence[:nfree].tocsr()
        expected_d = csr_matrix((signs.ravel(), local_branch.ravel(),
                                 np.arange(0, 3 * nfree + 1, 3)), shape=(nfree, nbranch))
        assert (divergence - expected_d).nnz == 0
        del expected_d

        is_contact = ((first >= nfree) & (first < nfree + contacts)) | (
            (second >= nfree) & (second < nfree + contacts))
        rim_branch = np.flatnonzero(is_contact)
        rim_contact = np.where(first[is_contact] >= nfree, first[is_contact], second[is_contact]) - nfree
        assert np.all(np.bincount(rim_contact, minlength=contacts) == 16)
        assert np.all(node_contact[branch_edges[is_contact]] == rim_contact[:, None])
        contact_local_node = np.full(len(xy), -1, dtype=np.int64)
        contact_local_node[contact_nodes] = np.arange(len(contact_nodes))
        rims = contact_local_node[branch_edges[is_contact]]
        rim_graph = coo_matrix((np.ones(2 * len(rims)),
                               (np.r_[rims[:, 0], rims[:, 1]], np.r_[rims[:, 1], rims[:, 0]])),
                              shape=(len(contact_nodes), len(contact_nodes))).tocsr()
        assert np.all(np.diff(rim_graph.indptr) == 2)
        assert connected_components(rim_graph, directed=False, return_labels=False) == contacts
        adjacency = coo_matrix((np.ones(2 * nbranch),
                                (np.r_[first, second], np.r_[second, first])), shape=(nnode, nnode)).tocsr()
        components = connected_components(adjacency, directed=False, return_labels=False)
        del adjacency, rim_graph, rims, unique, inverse, counts, order, starts
        del first_cell, second_cell, first_p, second_p, outside, kept, kept_ids, facet_ids, branch_index
        budget.check('complete RT0 topology')
        print(json.dumps({'phase': 'topology_complete', 'free_cells': nfree,
                          'branches': nbranch, 'contacts': contacts, 'retained_exterior': len(exterior),
                          'connected_components': components}), flush=True)

        # Translate each triangle before the existing exact formula and oracle,
        # avoiding subtraction of large board coordinates in skinny cells.
        rng = np.random.default_rng(20260909)
        q = rng.standard_normal(nbranch) + 1j * rng.standard_normal(nbranch)
        rows, columns, values = [], [], []
        quadrature_energy = 0j
        analytic_energy = 0j
        min_eigen, max_condition, max_block_error = np.inf, 0., 0.
        min_area, max_area = np.inf, 0.
        for start in range(0, nfree, 50000):
            stop = min(start + 50000, nfree)
            vertices = xy[triangles[free[start:stop]]].copy()
            vertices -= vertices[:, :1]
            vertices *= 1e-6
            block, det, area = local._batch_local_rt0(vertices, CONDUCTANCE)
            assert np.all(np.isfinite(block)) and np.all(area > 0)
            eig = np.linalg.eigvalsh(block)
            assert np.all(eig[:, 0] > 0), 'numerically nonpositive local R'
            min_eigen = min(min_eigen, float(eig[:, 0].min()))
            max_condition = max(max_condition, float(np.max(eig[:, -1] / eig[:, 0])))
            min_area, max_area = min(min_area, float(area.min())), max(max_area, float(area.max()))
            samples = np.einsum('qi,nid->nqd', local.BARYCENTRIC, vertices)
            basis = (samples[:, :, None] - vertices[:, None]) / (2 * area[:, None, None, None])
            check_block = np.einsum('nqid,nqjd,n->nij', basis, basis, area / (3 * CONDUCTANCE))
            err = np.linalg.norm(check_block - block, axis=(1, 2)) / np.linalg.norm(block, axis=(1, 2))
            max_block_error = max(max_block_error, float(err.max()))
            assert max_block_error < 2e-12
            ids, sign = local_branch[start:stop], signs[start:stop]
            local_q = sign * q[ids]
            quadrature_energy += local._quadrature_energy(vertices, local_q[:, :, None], CONDUCTANCE)[0]
            analytic_energy += np.einsum('ni,nij,nj->', local_q.conj(), block, local_q)
            rows.append(np.repeat(ids, 3, axis=1).ravel())
            columns.append(np.tile(ids, (1, 3)).ravel())
            values.append((sign[:, :, None] * block * sign[:, None, :]).ravel())
            budget.check('R chunk ' + str(stop))
        resistance = coo_matrix((np.concatenate(values), (np.concatenate(rows), np.concatenate(columns))),
                                shape=(nbranch, nbranch)).tocsr()
        resistance.sum_duplicates()
        resistance.sort_indices()
        del rows, columns, values
        assert np.max(abs((resistance - resistance.T).data), initial=0) == 0
        energy = np.vdot(q, resistance @ q)
        energy_error = float(abs(energy - quadrature_energy) / abs(quadrature_energy))
        assert energy_error < 1e-11 and abs(analytic_energy - quadrature_energy) / abs(quadrature_energy) < 1e-11
        budget.check('complete R and quadrature')
        output_npz = output / 'l02-rt0-current-space.npz'
        np.savez_compressed(output_npz, free_triangle_indices=free, triangle_contact_index=tag,
            triangle_potential_index=potential, branch_mesh_edges=branch_edges,
            branch_first_node=first, branch_second_node=second,
            local_facet_branch_index=local_branch, local_outward_flux_sign=signs,
            retained_exterior_branch_indices=exterior, electrode_rim_branch_indices=rim_branch,
            electrode_rim_contact_index=rim_contact, contact_support_index=supports,
            **sparse_arrays(resistance, 'r'), **sparse_arrays(divergence, 'd'),
            **sparse_arrays(incidence, 'distributional_b'))
        budget.check('saved full sparse space')
        result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_CONDITIONAL_L02_RT0_SPARSE_CURRENT_SPACE_WITH_EXTERIOR_SUPPORTS',
            driver_sha256=recon._sha256_file(Path(__file__)), input_sha256=PINS,
            elapsed_s=monotonic() - started, budget=budget.receipt(),
            counts=dict(free_cells=nfree, current_branches=nbranch, contacts=contacts,
                        retained_exterior_branches=len(exterior), distributional_rows=nnode,
                        connected_components=components, resistance_nnz=resistance.nnz),
            metrics=dict(min_local_eigenvalue_ohm=min_eigen, max_local_condition=max_condition,
                         min_triangle_area_m2=min_area, max_triangle_area_m2=max_area,
                         all_local_q2_matrix_relative=max_block_error,
                         independent_global_joule_relative=energy_error),
            material=dict(conductivity_s_m=59.59e6, thickness_m=20e-6),
            artifact_sha256=recon._sha256_file(output_npz),
            scope='The existing 2D source mesh and all38856 aggregate contact interiors are retained '
                  'under their prior equipotential-contact approximation. Every free-cell facet is a '
                  'current DOF, including exterior fluxes with separate distributional supports; '
                  'no new zero-flux/ground condition is imposed. Contact interior current fields are '
                  'outside this conditional free-sheet space. Sparse R/D/topology only: no charge '
                  'Green/G-C transfer, circuit solution, magnetic action, 3D thickness convergence '
                  'or board/PowerSI accuracy claim.')
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2), flush=True)
    except Exception:
        (output / 'failure.json').write_text(json.dumps({'traceback': traceback.format_exc()}, indent=2))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
