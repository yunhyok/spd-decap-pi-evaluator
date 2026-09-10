"""Restrict exterior RT0 fluxes before elimination, reusing full-face interior cells."""
import argparse
import json
from pathlib import Path

import numpy as np

import prepare_astra_l02_hybrid_cell_geometry as full

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = dict(full.PINS, **{
    'full_helper': (Path(full.__file__), '5f102d3a3f0a59919c7bacfb259cbf00a9959f50d01ed9913e7501acfcf6c9c2'),
    'full_geometry': (R/'astra-l02-hybrid-cell-geometry-02/hybrid-cell-geometry.npz', '15f1f6f6ad0ffd45ada9a74bde80d0e79ce82517a3ab485095d57898fbd06834'),
    'boundary_law': (R/'astra-l02-conditional-boundary-01/result.json', 'f5b6874bcdf8edfbfefb7aad58d1feeee03e4bb2adb8813a3589b0b6e9f99e05'),
})


def restricted_parameters(p, active, conductance):
    """Stable k=1/2 active-face coefficients; inactive faces have exactly zero flux."""
    n = len(p)
    count = active.sum(axis=1)
    assert np.all((count == 1) | (count == 2))
    edge1, edge2 = p[:, 1]-p[:, 0], p[:, 2]-p[:, 0]
    area = abs(edge1[:, 0]*edge2[:, 1]-edge1[:, 1]*edge2[:, 0])/2
    assert np.all(area > 0)
    center = p.mean(axis=1)
    variance = np.sum((p-center[:, None])**2, axis=(1, 2))/12
    h = np.zeros((n, 3, 3)); w = np.zeros((n, 3)); rho = np.empty(n)
    for k in (1, 2):
        rows = np.flatnonzero(count == k)
        if not len(rows):
            continue
        face = np.nonzero(active[rows])[1].reshape(-1, k)
        if k == 1:
            w[rows, face[:, 0]] = 1.
            offset = center[rows]-p[rows, face[:, 0]]
        else:
            a, b = face.T
            edge = p[rows, a]-p[rows, b]
            length2 = np.sum(edge**2, axis=1)
            coefficient = 4*conductance*area[rows]/length2
            h[rows, a, a] = h[rows, b, b] = coefficient
            h[rows, a, b] = h[rows, b, a] = -coefficient
            weight = np.sum((center[rows]-p[rows, b])*edge, axis=1)/length2
            w[rows, a], w[rows, b] = weight, 1-weight
            offset = center[rows]-p[rows, b]-weight[:, None]*edge
        rho[rows] = (np.sum(offset**2, axis=1)+variance[rows])/(4*conductance*area[rows])
    return h, w, rho


def self_check():
    triangle = np.array([[0., 0.], [2e-3, 0.], [3e-4, 1e-3]])
    masks = np.array([[bool(bits & (1 << i)) for i in range(3)] for bits in range(1, 7)])
    p = np.repeat(triangle[None], len(masks), axis=0)
    h, w, rho = restricted_parameters(p, masks, full.CONDUCTANCE)
    resistance, _, _ = full.local._batch_local_rt0(p, full.CONDUCTANCE)
    for row, mask in enumerate(masks):
        inverse = np.linalg.inv(resistance[row][np.ix_(mask, mask)])
        a = inverse.sum(axis=1); expected = inverse-np.outer(a, a)/a.sum()
        assert np.max(abs(h[row][np.ix_(mask, mask)]-expected)) < 2e-11
        assert np.max(abs(w[row, mask]-a/a.sum())) < 1e-14
        assert abs(rho[row]-1/a.sum())/rho[row] < 1e-14
    # Deleting a row of the full inverse would fail this one-active-face case.
    inverse = np.linalg.inv(resistance[0])
    assert abs(inverse[0, 0]-1/resistance[0, 0, 0]) > 1


def run(output):
    budget = full.recon._Budget.create(120, 4)
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for name, (path, digest) in PINS.items():
        assert full.recon._sha256_file(path) == digest, name
    with np.load(PINS['mesh'][0], allow_pickle=False) as mesh:
        xy, triangles = mesh['node_xy_um'], mesh['triangles']
    with np.load(PINS['space'][0], allow_pickle=False) as space:
        free, facets = space['free_triangle_indices'], space['local_facet_branch_index']
        exterior = space['retained_exterior_branch_indices']
    with np.load(PINS['full_geometry'][0], allow_pickle=False) as geometry:
        upper, rho = geometry['dc_trace_h_upper_s'], geometry['unit_divergence_resistance_ohm']
        assert np.array_equal(free, geometry['free_triangle_indices'])
        assert np.array_equal(facets, geometry['local_facet_branch_index'])
    blocked = np.zeros(3095567, dtype=bool); blocked[exterior] = True
    active = ~blocked[facets]
    count = active.sum(axis=1)
    histogram = np.bincount(count, minlength=4)
    assert np.array_equal(histogram, [0, 94983, 627952, 860905])
    w = np.full((len(free), 3), 1/3.)
    selected = np.flatnonzero(count < 3)
    ii, jj = np.triu_indices(3)
    eps = np.finfo(float).eps; gamma = 64*eps/(1-64*eps)
    maxima = {'rh_roundoff_ratio': 0., 'rw_roundoff_ratio': 0., 'rh_abs': 0., 'rw_abs_ohm': 0.,
              'weight_sum_roundoff_ratio': 0., 'weight_sum_abs': 0., 'weight_abs': 0.}
    for start in range(0, len(selected), 50000):
        rows = selected[start:start+50000]
        mask = active[rows]
        p = xy[triangles[free[rows]]].copy()
        p -= p[:, :1].copy(); p *= 1e-6
        h, weight, resistance = restricted_parameters(p, mask, full.CONDUCTANCE)
        r, _, _ = full.local._batch_local_rt0(p, full.CONDUCTANCE)
        r *= mask[:, :, None]*mask[:, None, :]
        projector = np.eye(3)[None]*mask[:, :, None]-mask[:, :, None]*weight[:, None, :]
        defect = r@h-projector
        bound = gamma*(abs(r)@abs(h)+abs(projector))
        div_defect = np.einsum('nij,nj->ni', r, weight)-resistance[:, None]*mask
        div_bound = gamma*(np.einsum('nij,nj->ni', abs(r), abs(weight))+resistance[:, None]*mask)
        rh_ratio = float(np.max(abs(defect)/np.maximum(bound, np.finfo(float).tiny)))
        rw_ratio = float(np.max(abs(div_defect)/np.maximum(div_bound, np.finfo(float).tiny)))
        assert max(rh_ratio, rw_ratio) <= 1, (start, rh_ratio, rw_ratio)
        assert np.all(np.isfinite(h)) and np.all(np.isfinite(weight)) and np.all(np.isfinite(resistance))
        assert np.all(resistance > 0) and np.max(abs(h.sum(axis=2))) == 0
        # Opposite active fluxes can exceed the unit net flux on skinny cells.
        # Bound cancellation using operand magnitudes, not a fixed absolute epsilon.
        weight_defect = abs(weight.sum(axis=1)-1)
        weight_bound = (4*eps/(1-4*eps))*abs(weight).sum(axis=1)
        weight_ratio = float(np.max(weight_defect/weight_bound))
        assert np.all(weight[~mask] == 0) and weight_ratio <= 1
        upper[rows], rho[rows], w[rows] = h[:, ii, jj], resistance, weight
        for key, value in zip(maxima, [rh_ratio, rw_ratio, float(np.max(abs(defect))), float(np.max(abs(div_defect))),
                                      weight_ratio, float(weight_defect.max()), float(abs(weight).max())]):
            maxima[key] = max(maxima[key], value)
        budget.check('restricted real cells')
    artifact = output/'restricted-hybrid-cell-geometry.npz'
    np.savez_compressed(artifact, free_triangle_indices=free, local_facet_branch_index=facets,
                        active_local_facet_mask=active, dc_trace_h_upper_s=upper,
                        unit_divergence_resistance_ohm=rho, unit_divergence_flux_weights=w,
                        upper_rows=ii, upper_columns=jj, zero_flux_exterior_branch_indices=exterior)
    budget.check('saved restricted cells')
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_REAL_L02_RESTRICTED_HYBRID_CELLS_CONDITIONAL_ZERO_EXTERIOR_FLUX',
              'driver_sha256': full.recon._sha256_file(Path(__file__)),
              'inputs': {key: {'path': str(path), 'sha256': digest} for key, (path, digest) in PINS.items()},
              'active_face_count_histogram': histogram.tolist(), 'changed_boundary_cells': len(selected),
              'unchanged_full_face_cells': int(histogram[3]), 'metrics': maxima,
              'artifact': {'path': str(artifact), 'sha256': full.recon._sha256_file(artifact)},
              'budget': budget.receipt(),
              'scope': 'Exact local RT0 flux restriction for the approved conditional2D lateral boundary. Interior k3 coefficients reused unchanged; k1/k2 geometric coefficients represent inverse of restricted R before cell elimination. All active contact and G/C terminals remain for the next coupled assembly. No global solve, magnetic action, or accuracy acceptance.'}
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    self_check()
    run(parser.parse_args().output.resolve())
