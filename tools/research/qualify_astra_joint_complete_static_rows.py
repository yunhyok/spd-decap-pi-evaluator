"""SPD Decap PI Evaluator v0.23.1: complete-source analytic mixed Green rows.

Two global fine-current rows and two charge rows include every source support.
This is an oracle for a near-corrected accelerated action, not a field solve.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from scipy.sparse import csr_matrix
from qualify_astra_tetra_volume_green import tetra_inner, triangle_moments
from qualify_astra_tetra_charge_green import quadrature, measure
from qualify_astra_source_joint_self_green import ROOT, PINS


SPACE = 'outputs/research/astra-boundary-joint-current-space-01/current-space.npz'
SPACE_SHA = '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'


def complete_potentials(tets, triangles, centers, radial, charge, points, deadline):
    """Integrate every affine-current volume and normalized charge analytically."""
    vector = np.zeros((len(points), centers.shape[1], 3), complex)
    scalar = np.zeros((len(points), centers.shape[1]), complex)
    for cell, tetra in enumerate(tets):
        assert monotonic() < deadline, 'complete-source static action deadline'
        origin = tetra[0]
        potential, moment = tetra_inner(tetra-origin, points-origin)
        centered_moment = moment+(points-tetra.mean(axis=0))*potential[:, None]
        vector += potential[:, None, None]*centers[cell][None]
        vector += centered_moment[:, None, :]*radial[cell][None, :, None]
        scalar += potential[:, None]*(charge[cell]/measure(tetra))[None]
    for face, triangle in enumerate(triangles):
        assert monotonic() < deadline, 'complete-source static charge deadline'
        origin = triangle[0]
        potential = triangle_moments(triangle-origin, points-origin)[0]
        scalar += potential[:, None]*(charge[len(tets)+face]/measure(triangle))[None]
    return vector, scalar


def run(output):
    start = monotonic()
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, pin in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
    assert sha256((ROOT/SPACE).read_bytes()).hexdigest() == SPACE_SHA
    with np.load(ROOT/list(PINS)[2], allow_pickle=False) as d:
        xyz = d['vertices_local_um']*1e-6
        cells = d['cells']
        boundary = d['boundary_face_ids']
        face_vertices = d['face_vertices']
        shared = d['shared_interface_face_ids']
    tets, triangles = xyz[cells], xyz[face_vertices[boundary]]
    nc, nb = len(tets), len(triangles)
    with np.load(ROOT/SPACE, allow_pickle=False) as d:
        columns = d['local_rt0_face_columns']
        signs = d['local_rt0_face_signs']
        R = csr_matrix((d['resistance_data_ohm'], d['mass_col'], d['mass_row_ptr']), shape=d['mass_shape'])
        B = csr_matrix((d['distributional_b_data'], d['distributional_b_col'], d['distributional_b_row_ptr']), shape=d['distributional_b_shape'])
        smooth = d['energy_orthonormal_face_flux'] @ np.exp(.7j*np.arange(14))
    rng = np.random.default_rng(20260908)
    currents = np.column_stack((smooth, rng.standard_normal(R.shape[0])+1j*rng.standard_normal(R.shape[0])))
    energies = np.diag(currents.conj().T @ (R @ currents)).real
    assert energies.min() > 0
    currents /= np.sqrt(energies)[None]
    local_flux = signs[:, :, None]*currents[columns]
    volume = np.abs(np.linalg.det(tets[:, 1:]-tets[:, :1]))/6
    centers = np.einsum('cim,cid->cmd', local_flux, tets.mean(axis=1)[:, None]-tets)/(3*volume[:, None, None])
    radial = local_flux.sum(axis=1)/(3*volume[:, None])
    charge = B @ currents
    charge /= np.linalg.norm(charge, axis=0)[None]
    charge_balance = np.abs(charge.sum(axis=0))/np.abs(charge).sum(axis=0)
    assert charge_balance.max() < 1e-12 and np.linalg.norm(radial[:, 1]) > 0
    # Complete global-face test rows include both source-owned tetrahedra.
    singular = np.linalg.svd(tets[:, 1:]-tets[:, :1], compute_uv=False)
    worst = int(np.argmax(singular[:, 0]/singular[:, -1]))
    interface_face = int(np.intersect1d(columns[worst], shared)[0])
    exterior_face = int(boundary[np.argmin(np.linalg.norm(triangles.mean(axis=1)-tets[worst].mean(axis=0), axis=1))])
    test_faces = np.array([interface_face, exterior_face])
    test_cells = np.flatnonzero(np.isin(columns, test_faces).any(axis=1))
    assert all(np.count_nonzero(columns == face) == np.count_nonzero(columns[test_cells] == face) for face in test_faces)
    singular_tri = np.linalg.svd(triangles[:, 1:]-triangles[:, :1], compute_uv=False)
    test_charge_face = int(np.argmax(singular_tri[:, 0]/singular_tri[:, -1]))
    histories, arrays = [], dict(source_face_currents=currents, source_charge_coefficients=charge,
        source_current_centers=centers, source_current_radial=radial,
        tested_global_current_face_ids=test_faces, tested_current_cell_ids=test_cells,
        tested_scalar_volume_cell_ids=np.array([worst]), tested_scalar_boundary_face_ids=boundary[[test_charge_face]])
    previous = None
    failure = None
    try:
        for order in (4, 8, 16):
            points, spans, weights = [], [], []
            for vertices in [tets[i] for i in test_cells]+[triangles[test_charge_face]]:
                p, w = quadrature(vertices, order)
                begin = sum(len(x) for x in points)
                spans.append(slice(begin, begin+len(p)))
                points.append(p)
                weights.append(w)
            points = np.vstack(points)
            vector, scalar = complete_potentials(tets, triangles, centers, radial, charge, points, start+540)
            vector_rows = np.zeros((len(test_faces), currents.shape[1]), complex)
            scalar_rows = []
            for ti, cell in enumerate(test_cells):
                span = spans[ti]
                weighted = weights[ti][:, None, None]*(points[span, None]-tets[cell][None])/3
                local_rows = 1e-7*np.einsum('pid,pmd->im', weighted, vector[span])
                for row, face in enumerate(test_faces):
                    slots = np.flatnonzero(columns[cell] == face)
                    if len(slots):
                        slot = int(slots[0])
                        vector_rows[row] += signs[cell, slot]*local_rows[slot]
                if cell == worst:
                    scalar_rows.append(weights[ti] @ scalar[span])
            scalar_rows.append(weights[-1] @ scalar[spans[-1]])
            scalar_rows = np.array(scalar_rows)
            assert np.isfinite(vector_rows).all() and np.isfinite(scalar_rows).all()
            changes = None if previous is None else dict(
                vector_by_density=(np.linalg.norm(vector_rows-previous[0], axis=0)/np.linalg.norm(vector_rows, axis=0)).tolist(),
                scalar_by_density=(np.linalg.norm(scalar_rows-previous[1], axis=0)/np.linalg.norm(scalar_rows, axis=0)).tolist())
            histories.append(dict(order=order, changes=changes, elapsed_s=monotonic()-start))
            arrays.update({f'vector_rows_order{order}': vector_rows, f'scalar_rows_order{order}': scalar_rows,
                f'target_points_order{order}': points, f'vector_potential_order{order}': vector, f'scalar_potential_order{order}': scalar})
            np.savez_compressed(output/'complete-rows.npz', **arrays)
            print(json.dumps(histories[-1]), flush=True)
            previous = vector_rows, scalar_rows
            if changes is not None and max(changes['vector_by_density']+changes['scalar_by_density']) < 5e-5:
                break
    except Exception:
        failure = traceback.format_exc()
        np.savez_compressed(output/'complete-rows.npz', **arrays)
    final = histories[-1]['changes'] if histories else None
    accepted = failure is None and final is not None and max(final['vector_by_density']+final['scalar_by_density']) < 5e-5
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='ACCEPT_COMPLETE_SOURCE_STATIC_SELECTED_ROWS' if accepted else 'STOP_COMPLETE_SOURCE_STATIC_ROWS_GATE',
        elapsed_s=monotonic()-start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        space_sha256=SPACE_SHA, artifact_sha256=sha256((output/'complete-rows.npz').read_bytes()).hexdigest(),
        source_current_cells=nc, source_charge_volumes=nc, source_charge_faces=nb,
        test_global_current_faces=test_faces.tolist(), test_current_cells=test_cells.tolist(),
        test_scalar_volume=worst, test_scalar_boundary_face=int(boundary[test_charge_face]),
        source_charge_balance=charge_balance.tolist(), fixed_relative_gate=5e-5, refinement=histories, failure=failure,
        scope='All5304 actual source-current cells and9180 charge supports contribute analytically '
        'to two complete global RT0 current rows and two normalized P0 charge rows. Smooth seeded '
        'and arbitrary fine complex currents are normalized by physical Joule energy; charge vectors '
        'are independent normalized test densities constructed from B, not an imposed terminal '
        'continuity relation or physical excitation. Full affine radial currents survive. Exact inner '
        'static1/R with positive refined outer quadrature. No near truncation, FMM action, retarded '
        'field, all-row residual, contact boundary condition, solve or board accuracy.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'elapsed_s', 'test_global_current_faces', 'test_current_cells', 'failure')}), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
