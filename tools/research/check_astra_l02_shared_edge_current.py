"""Actual shared-edge RT0 current: finite cross-cell integral versus point rule."""
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np

from assemble_astra_l02_finite_charge_self import prism_union
from measure_astra_l02_adaptive_nonself import AnisotropicOuter, sha
from prepare_astra_retained_sheet_current_green import chunks
from qualify_astra_tetra_volume_green import tetra_inner

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'


class CurrentOuter(AnisotropicOuter):
    """Reuse positive anisotropic outer rules with exact affine source moments."""
    def __init__(self, observer, source, observer_triangle, source_triangle,
                 observer_local, source_local, observer_sign, source_sign, deadline):
        self.ov = observer_triangle[observer_local]
        self.sv = source_triangle[source_local]
        self.od = abs(np.linalg.det(observer_triangle[1:]-observer_triangle[0])) * 20e-6
        self.sd = abs(np.linalg.det(source_triangle[1:]-source_triangle[0])) * 20e-6
        self.sign = observer_sign * source_sign
        self.volume = sum(abs(np.linalg.det(triangle[1:]-triangle[0])) * (bounds[1]-bounds[0])/2
                          for triangle, bounds in observer)
        super().__init__(observer, source, deadline)

    def potential(self, points):
        assert monotonic() < self.deadline, '120s current near control boundary'
        self.points += len(points)
        scalar = np.zeros(len(points))
        moment = np.zeros((len(points), 3))
        for source, _, _ in self.sources:
            s, m = tetra_inner(source, points)
            scalar += s
            moment += m
        test = (points[:, :2]-(self.ov-self.anchor[:2])) / self.od
        trial = ((points[:, :2]-(self.sv-self.anchor[:2])) * scalar[:, None] + moment[:, :2]) / self.sd
        result = 1e-7 * self.sign * self.volume * np.einsum('pi,pi->p', test, trial)
        assert np.isfinite(result).all()
        return result


def run():
    started = monotonic()
    out = R / 'astra-l02-shared-edge-current-20260912-01'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    history = []
    try:
        current = R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
        single = R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz'
        pins = {current:'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
                single:'5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05'}
        for path, expected in pins.items():
            assert sha(path) == expected
        with np.load(current, allow_pickle=False) as z:
            payload = {key:z[key] for key in ('piece_triangle_xy_m', 'piece_parent_free_ordinal',
                       'original_free_triangle_xy_m', 'compact_local_columns', 'local_signs')}
        rows = np.array([0, 75])
        cols = payload['compact_local_columns'][rows]
        common = np.intersect1d(cols[0], cols[1])
        assert len(common) == 1
        column = int(common[0])
        support_rows = np.flatnonzero(np.any(payload['compact_local_columns'] == column, axis=1))
        actual_rows = np.intersect1d(support_rows, np.unique(payload['piece_parent_free_ordinal']))
        assert np.array_equal(actual_rows, rows), 'global current must have exactly these two retained cell supports'
        local = np.array([int(np.flatnonzero(c == column)[0]) for c in cols])
        signs = payload['local_signs'][rows, local]
        triangle = payload['original_free_triangle_xy_m'][rows]
        bounds = np.array([55e-6, 75e-6])
        domains, tetra, point, integrated = [], [], [], []
        for ordinal, row in enumerate(rows):
            selected = payload['piece_parent_free_ordinal'] == row
            pieces = payload['piece_triangle_xy_m'][selected]
            assert len(pieces) == 1 and np.array_equal(pieces[0], triangle[ordinal])
            domains.append([(pieces[0], bounds)])
            tetra.append(prism_union(pieces, bounds)[0])
            small = dict(piece_triangle_xy_m=pieces, piece_parent_free_ordinal=np.array([0]),
                         original_free_triangle_xy_m=triangle[ordinal:ordinal+1],
                         compact_local_columns=cols[ordinal:ordinal+1], local_signs=payload['local_signs'][row:row+1])
            xyz, _, basis, weights = next(chunks(small))
            point.append(xyz[0])
            integrated.append(basis[0, :, local[ordinal], :] * weights[0, :, None])
        with np.load(single, allow_pickle=False) as z:
            saved_rows = z['original_free_ordinals']
            indices = np.array([int(np.flatnonzero(saved_rows == row)[0]) for row in rows])
            physical_self = z['physical_block_h'][indices, local, local]
            point_self = z['point_block_h'][indices, local, local]
        distances = np.linalg.norm(point[0][:, None]-point[1][None], axis=-1)
        inverse = np.divide(1., distances, out=np.zeros_like(distances), where=distances > 0)
        point_cross = float(1e-7*np.einsum('pi,pq,qi->', integrated[0], inverse, integrated[1]))
        deadline = monotonic()+120
        forward = CurrentOuter(domains[0], tetra[1], triangle[0], triangle[1], *local, *signs, deadline)
        reverse = CurrentOuter(domains[1], tetra[0], triangle[1], triangle[0], *local[::-1], *signs[::-1], deadline)
        previous = None
        for tolerance in (5e-5, 1.25e-5, 3.125e-6, 7.8125e-7):
            values = np.array([forward.refine(tolerance).real, reverse.refine(tolerance).real])
            scale = max(abs(values))
            reciprocal = float(abs(values[0]-values[1])/scale)
            change = None if previous is None else float(max(abs(values-previous))/scale)
            history.append(dict(tolerance=tolerance, directed_cross_h=values.tolist(), reciprocity=reciprocal,
                                refinement=change, forward=forward.receipt(), reverse=reverse.receipt()))
            if previous is not None and reciprocal <= 5e-5 and change <= 5e-5:
                break
            previous = values.copy()
        else:
            raise RuntimeError('current cross-cell refinement failed')
        physical = float(physical_self.sum()+values.sum())
        self_corrected = float(physical_self.sum()+2*point_cross)
        assert physical > 0
        report = dict(status='PASS_ACTUAL_SHARED_EDGE_CURRENT_CROSS', original_free_rows=rows.tolist(),
                      global_current_column=column, local_indices=local.tolist(), local_signs=signs.tolist(),
                      physical_cell_self_h=physical_self.tolist(), point_cell_self_h=point_self.tolist(),
                      point_cross_h=point_cross, directed_cross_h=values.tolist(),
                      physical_complete_current_diagonal_h=physical,
                      self_corrected_point_current_diagonal_h=self_corrected,
                      relative_diagonal_error=abs(self_corrected-physical)/physical,
                      directed_cross_delta_h=(values-point_cross).tolist(), history=history,
                      input_sha256={str(p.relative_to(ROOT)):h for p,h in pins.items()},
                      driver_sha256=sha(Path(__file__)), elapsed_s=monotonic()-started,
                      scope='One actual global RT0 current spanning two long shared-edge prisms; all its self/cross support pairs retained. Other currents, off-diagonal current interactions, full L and board response remain unqualified.')
    except Exception:
        report = dict(status='STOP_ACTUAL_SHARED_EDGE_CURRENT_CROSS', traceback=traceback.format_exc(),
                      history=history, elapsed_s=monotonic()-started)
        (out/'failure.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        raise
    finally:
        if 'report' in locals():
            (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
