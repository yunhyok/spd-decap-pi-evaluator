"""Finite L02 prism-union/sidewall self, using the existing analytic inner kernel."""
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np

from astra_stratified_charge_green import source_background
from astra_layered_charge_action import halfspace_action, _direct_points
from measure_astra_physical_charge_near_error import physical_pair
from prepare_astra_conforming_power_joint import prisms, positive
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]; R = ROOT/'outputs/research'


def prism_union(triangles, z):
    parts = []
    for triangle in triangles:
        vertices = np.vstack([np.column_stack([triangle, np.full(3, height)]) for height in z])
        cells = positive(vertices, prisms(np.array([[0, 1, 2]]), np.arange(3), np.arange(3, 6)))
        parts.extend(vertices[cells])
    parts = np.asarray(parts)
    volume = abs(np.linalg.det(parts[:, 1:]-parts[:, :1]))/6
    assert np.all(volume > 0)
    return parts, volume/volume.sum()


def wall_union(segments, z):
    parts = []
    for segment in segments:
        vertices = np.vstack([np.column_stack([segment, np.full(2, height)]) for height in z])
        parts.extend(vertices[[[0, 1, 3], [0, 3, 2]]])
    parts = np.asarray(parts)
    area = np.linalg.norm(np.cross(parts[:, 1]-parts[:, 0], parts[:, 2]-parts[:, 0]), axis=1)/2
    assert np.all(area > 0)
    return parts, area/area.sum()


def union_self(parts, fraction, deadline, gate=5e-5):
    assert len(parts) == len(fraction) and np.all(fraction > 0) and abs(fraction.sum()-1) < 2e-15
    interfaces, eps, _ = source_background()
    previous = None; history = []
    for order in (4, 8, 16, 32, 64):
        values = np.empty((len(parts), len(parts)), complex)
        for a, observer in enumerate(parts):
            for b, source in enumerate(parts):
                assert monotonic() < deadline, 'finite-union self execution boundary'
                values[a, b] = physical_pair(observer, source, order, interfaces[0], eps)
        value = fraction@values@fraction
        assert np.isfinite(value) and value.real > 0
        change = None if previous is None else float(abs(value-previous)/abs(value))
        directional = float(np.max(abs(values-values.T)*fraction[:, None]*fraction[None, :])/abs(value))
        history.append(dict(order=order, complete_self_refinement=change, maximum_weighted_directional_defect=directional))
        if change is not None and change < gate and directional < gate:
            return value, history
        previous = value
    raise RuntimeError(f'Finite-union self did not meet unchanged gate: {history}')


def run():
    started = monotonic(); deadline = started+120
    out = R/'astra-l02-finite-charge-self-20260912-02'; out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    cases = []; failure = None
    try:
        paths = {
            R/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz':'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
            R/'astra-retained-sheet-current-green-20260912-02/current-support.npz':'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
            R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz':'137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9'}
        for path, digest in paths.items():
            assert sha(path) == digest, path
        with np.load(list(paths)[0], allow_pickle=False) as z:
            payload = {key:z[key] for key in z.files}
        with np.load(list(paths)[1], allow_pickle=False) as z:
            triangle = z['piece_triangle_xy_m']; parent = z['piece_parent_free_ordinal']
        with np.load(list(paths)[2], allow_pickle=False) as z:
            contact_triangles = z['node_xy_um'][z['triangles'][payload['contact_triangle_ids']]]*1e-6
        z_bounds = payload['z_interval_um']*1e-6
        rows = payload['charge_row_ids']; nc = len(payload['free_charge_row_ids'])
        free_rows = np.r_[payload['free_charge_row_ids'][0], payload['partial_free_charge_row_ids'][0]]
        contact_counts = np.bincount(payload['contact_triangle_contact_id'])
        contact = payload['contact_ids'][np.argmin(contact_counts[payload['contact_ids']])]
        contact_position = int(np.flatnonzero(payload['contact_ids'] == contact)[0])
        selected = []
        for row in free_rows:
            column = int(np.searchsorted(rows, row))
            parts, fraction = prism_union(triangle[parent == row], z_bounds)
            selected.append((column, 'free_or_clipped_prism_union', parts, fraction))
        parts, fraction = prism_union(contact_triangles[payload['contact_triangle_contact_id'] == contact], z_bounds)
        selected.append((nc+contact_position, 'contact_prism_union', parts, fraction))
        offsets = payload['exterior_segment_offsets']; segments = payload['exterior_segment_vertices_um']*1e-6
        for index in np.unique([0, len(offsets)-2]).tolist():
            parts, fraction = wall_union(segments[offsets[index]:offsets[index+1]], z_bounds)
            column = nc+len(payload['contact_ids'])+index
            selected.append((column, 'finite_sidewall_union', parts, fraction))
        for column, kind, parts, fraction in selected:
            exact, history = union_self(parts, fraction, deadline)
            mask = payload['spread_col'] == column
            points = payload['quadrature_points_um'][mask]*1e-6; weights = payload['spread_data'][mask]
            assert len(points) and abs(weights.sum()-1) < 2e-15
            point = weights@halfspace_action(points, weights, point_action=_direct_points)[:, 0]
            delta = exact-point
            cases.append(dict(charge_column=column, original_charge_row=int(rows[column]), kind=kind,
                              primitives=len(parts), points=len(points), physical_self_per_f=[exact.real, exact.imag],
                              point_self_per_f=[point.real, point.imag], self_delta_per_f=[delta.real, delta.imag],
                              history=history))
            (out/'partial-cases.json').write_text(json.dumps(cases, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            print(json.dumps(dict(column=column, kind=kind, primitives=len(parts), elapsed_s=monotonic()-started)), flush=True)
    except Exception:
        failure = traceback.format_exc()
    report = dict(status='PASS_SELECTED_FINITE_L02_SELF' if failure is None else 'STOP_FINITE_L02_SELF',
                  cases=cases, failure=failure, elapsed_s=monotonic()-started,
                  driver_sha256=sha(Path(__file__)),
                  scope='Selected actual free/clipped/contact prism unions and finite sidewalls only. Internal primitive cross terms are included in each charge self; the exact saved point self is subtracted once. Deep diagonal remains outside this correction. This is not complete L02 self/near coverage or a port solve.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)
    return 0 if failure is None else 1


if __name__ == '__main__':
    raise SystemExit(run())
