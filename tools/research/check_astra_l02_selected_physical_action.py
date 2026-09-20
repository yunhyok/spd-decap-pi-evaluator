"""Connect four owned pair corrections to actual L02 physical P spread/gather."""
import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import numpy as np
from scipy import sparse
from scipy.special import roots_legendre, j0

from apply_astra_physical_charge_green import apply
from astra_layered_charge_action import deep_spectral, halfspace_action, _direct_points
from astra_stratified_charge_green import EPS0, source_background
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def direct_deep(points, tail):
    interfaces, eps, _ = source_background()
    rho = np.linalg.norm(points[:, None, :2]-points[None, :, :2], axis=-1)
    cutoff = tail/(interfaces[1]-75e-6)
    order = int(max(96, np.ceil(cutoff*rho.max()/2)+48))
    x, w = roots_legendre(order)
    k, w = (x+1)*cutoff/2, w*cutoff/2
    amplitude, modes = deep_spectral(k, points[:, 2], interfaces, eps)
    result = np.einsum('abk,ak,bk,k->ab', j0(rho[:, :, None]*k), modes, modes,
                       w*amplitude/(2*np.pi*EPS0))
    assert np.isfinite(result).all()
    return result, order


def main():
    started = monotonic()
    pins = {
        'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz': '9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
        'astra-l02-complete-self-20260912/complete-self.npz': '03b38c0a431eb7b275bedfa8f8c5114501485d6e90b0e2dd93cbcdce2f34b08b',
        'astra-l02-selected-nonself-correction-20260912/selected-nonself-correction.npz': 'f804bd3da8d3ba1fc3721cee0efb118f319933e61cf0ce948f98382f7fc55b39',
    }
    for path, digest in pins.items():
        assert sha(R/path) == digest, path
    with np.load(R/list(pins)[2], allow_pickle=False) as z:
        selected = z['selected_charge_columns']
        selected_rows = z['selected_original_rows']
        row, col = np.searchsorted(selected, z['rows']), np.searchsorted(selected, z['columns'])
        pair_physical, pair_point, pair_delta = z['physical_halfspace_p'], z['point_halfspace_p'], z['halfspace_delta_p']
        assert np.array_equal(selected[row], z['rows']) and np.array_equal(selected[col], z['columns'])
    with np.load(R/list(pins)[0], allow_pickle=False) as z:
        columns = z['spread_col']
        mask = np.isin(columns, selected)
        points = z['quadrature_points_um'][mask]*1e-6
        columns, weights = np.searchsorted(selected, columns[mask]), z['spread_data'][mask]
        assert np.array_equal(z['charge_row_ids'][selected], selected_rows)
    with np.load(R/list(pins)[1], allow_pickle=False) as z:
        self_delta = z['physical_halfspace_self_delta_p'][selected]
        physical_self = z['physical_halfspace_self_p'][selected]
    count = len(selected)
    spread = sparse.csr_matrix((weights, (np.arange(len(points)), columns)), shape=(len(points), count))
    assert np.max(abs(np.asarray(spread.sum(axis=0))-1)) < 2e-15
    low, _ = direct_deep(points, 16.)
    deep, order = direct_deep(points, 32.)
    deep_change = float(np.linalg.norm(deep-low)/np.linalg.norm(deep))
    assert deep_change < 5e-5
    smooth_q = np.asarray(spread.T@(deep@spread.toarray()))
    half_points = halfspace_action(points, np.eye(len(points)), point_action=_direct_points)
    half_q = np.asarray(spread.T@(half_points@spread.toarray()))
    point_match = float(np.max(abs(half_q[row, col]-pair_point)/abs(pair_point)))
    assert point_match < 1e-12
    correction = sparse.csr_matrix((pair_delta, (row, col)), shape=(count, count))
    matrix = apply(points, columns, weights, self_delta, np.eye(count),
                   SimpleNamespace(apply=lambda density: deep@density), point_action=_direct_points,
                   near_delta=correction)
    pair_error = float(np.max(abs(matrix[row, col]-(pair_physical+smooth_q[row, col]))/abs(pair_physical+smooth_q[row, col])))
    self_error = float(np.max(abs(matrix.diagonal()-(physical_self+smooth_q.diagonal()))/abs(physical_self+smooth_q.diagonal())))
    assert pair_error < 1e-12 and self_error < 1e-12
    unowned = np.ones((count, count), bool)
    unowned[row, col] = False
    np.fill_diagonal(unowned, False)
    baseline = apply(points, columns, weights, self_delta, np.eye(count),
                     SimpleNamespace(apply=lambda density: deep@density), point_action=_direct_points)
    assert np.array_equal(matrix[unowned], baseline[unowned])
    out = R/'astra-l02-selected-physical-action-20260912'
    out.mkdir(exist_ok=False)
    np.savez_compressed(out/'physical-action.npz', selected_charge_columns=selected,
                        selected_original_rows=selected_rows, physical_p_per_f=matrix,
                        deep_p_per_f=smooth_q, corrected_pair_rows=row, corrected_pair_columns=col,
                        uncorrected_nonself_mask=unowned)
    report = dict(status='PASS_FOUR_OWNED_PAIR_PHYSICAL_ACTION_INTEGRATION', selected_q=count,
                  point_count=len(points), corrected_directed_entries=len(row),
                  exact_point_block_relative=point_match, corrected_entry_relative=pair_error,
                  self_plus_smooth_diagonal_relative=self_error, deep_cutoff_refinement=deep_change,
                  deep_quadrature_order=order, elapsed_s=monotonic()-started,
                  driver_sha256=sha(Path(__file__)), pins=pins,
                  artifact_sha256=sha(out/'physical-action.npz'),
                  scope='Actual five L02 supports with all point interactions, all owned self and four corrected nonself pairs. Remaining six unordered nonself pairs keep their saved point approximation. Smooth deep diagonals retained. No global P accuracy or board Z claim.')
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    (out/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
