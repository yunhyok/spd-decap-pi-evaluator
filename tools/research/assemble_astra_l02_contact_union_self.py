"""Actual L02 contact-union self with verified boundary maps and owned point subtraction."""
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np

from astra_stratified_charge_green import source_background
from assemble_astra_l02_finite_charge_self import prism_union, union_self
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]; R = ROOT/'outputs/research'


def boundary_polygon(triangles, xy):
    triangle_xy = xy[triangles]
    signed_area = np.cross(triangle_xy[:, 1]-triangle_xy[:, 0], triangle_xy[:, 2]-triangle_xy[:, 0])/2
    assert np.all(signed_area != 0)
    oriented = triangles.copy(); flip = signed_area < 0
    oriented[flip] = oriented[flip][:, [0, 2, 1]]
    directed = oriented[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
    edges = np.sort(directed, axis=1)
    unique, inverse, count = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    assert np.all((count == 1) | (count == 2))
    circulation = np.bincount(inverse, weights=np.where(directed[:, 0] < directed[:, 1], 1, -1))
    assert np.all(circulation[count == 2] == 0)
    boundary = unique[count == 1]
    nodes, degree = np.unique(boundary, return_counts=True)
    assert len(nodes) == len(boundary) == 16 and np.all(degree == 2)
    points = xy[nodes]; center = points.mean(axis=0); points = points-center
    angular = np.argsort(np.arctan2(points[:, 1], points[:, 0]))
    node_order = nodes[angular]; points = points[angular]
    # The angular polygon must reproduce the actual boundary graph, not just its cloud.
    expected = np.sort(np.column_stack([node_order, np.roll(node_order, -1)]), axis=1)
    assert set(map(tuple, expected)) == set(map(tuple, boundary))
    # A positive triangle chain with exactly this simple boundary has winding
    # number one inside and zero outside: no overlaps or holes are hidden by area.
    assert set(map(tuple, directed[count[inverse] == 1])) == set(zip(node_order, np.roll(node_order, -1)))
    first = int(np.lexsort((points[:, 1], points[:, 0]))[0]); points = np.roll(points, -first, axis=0)
    following = np.roll(points, -1, axis=0)
    turns = np.cross(following-points, np.roll(following, -1, axis=0)-following)
    fan_area = np.cross(points, following)/2
    assert np.all(turns > 0) and np.all(fan_area > 0)
    triangle_area = abs(signed_area)
    assert len(triangles) == 14 and np.all(triangle_area > 0)
    area = fan_area.sum()
    assert abs(triangle_area.sum()/area-1) < 1e-10
    return points, float(area)


def geometry_bound(reference, member, reflection_abs, coordinate_scale):
    ref_edges = np.stack([reference, np.roll(reference, -1, axis=0)], axis=1)
    edges = np.stack([member, np.roll(member, -1, axis=0)], axis=1)
    conditioning = float(np.linalg.cond(ref_edges).max())
    assert conditioning < 1e4
    transform = np.linalg.solve(ref_edges, edges)
    reconstructed = ref_edges@transform
    assert np.max(abs(reconstructed-edges)) <= 64*np.finfo(float).eps*max(np.max(abs(edges)), 1e-30)
    # Conservative floating margin includes centering/edge operations and solves.
    # This is a numerical evaluation of the proved geometry inequality, not an
    # interval-arithmetic certificate for LAPACK or the original coordinates.
    sigma = float(np.linalg.svd(ref_edges, compute_uv=False)[:, -1].min())
    allowance = 512*np.finfo(float).eps*(conditioning+coordinate_scale/sigma)
    eta = float(np.linalg.svd(transform-np.eye(2), compute_uv=False)[:, 0].max())+allowance
    if eta >= 1:
        return float('inf')
    area_ratio = np.linalg.det(edges).sum()/np.linalg.det(ref_edges).sum()
    normalized_jacobian = np.linalg.det(transform)/area_ratio
    assert np.all(normalized_jacobian > 0)
    low = max(0., normalized_jacobian.min()-4*allowance)**2/(1+eta)
    high = (normalized_jacobian.max()+4*allowance)**2/(1-eta)
    error = max(0., 1-low, high-1)
    return float(error*(1+reflection_abs)/(1-reflection_abs))


def run():
    started = monotonic(); out = R/'astra-l02-contact-union-self-20260912'; out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    cases = []; failure = None
    try:
        source = R/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz'
        mesh = R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz'
        prior = R/'astra-l02-finite-charge-self-20260912-02/result.json'
        pins = {source:'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
                mesh:'137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9',
                prior:'ae5e542c598f9b432dbc6194155802ab66567c4393a9a7f4c5868fed7a5372ba'}
        for path, digest in pins.items():
            assert sha(path) == digest, path
        with np.load(source, allow_pickle=False) as z:
            payload = {key:z[key] for key in ('contact_ids', 'contact_triangle_ids', 'contact_triangle_contact_id',
                       'contact_charge_columns', 'contact_charge_row_ids', 'contact_global_active_index', 'z_interval_um',
                       'spread_col', 'spread_data', 'quadrature_points_um')}
        with np.load(mesh, allow_pickle=False) as z:
            xy = z['node_xy_um']*1e-6; triangles = z['triangles'][payload['contact_triangle_ids']]
        ids = payload['contact_triangle_contact_id']; contacts = payload['contact_ids']
        assert len(contacts) == 38854 and len(triangles) == 543956
        order = np.argsort(ids, kind='stable'); counts = np.bincount(ids)
        offsets = np.r_[0, np.cumsum(counts)]
        top, eps, _ = source_background(); reflection_abs = abs((eps[1]-eps[0])/(eps[1]+eps[0]))
        groups = []; lookup = {}; member_group = np.empty(len(contacts), np.int64)
        bounds = np.empty(len(contacts)); areas = np.empty(len(contacts))
        for i, contact in enumerate(contacts):
            owned = triangles[order[offsets[contact]:offsets[contact+1]]]
            polygon, areas[i] = boundary_polygon(owned, xy)
            key = tuple(np.rint(np.ptp(polygon, axis=0)*1e14).astype(np.int64))
            coordinate_scale = float(np.max(abs(xy[owned])))
            chosen = None
            for group in lookup.get(key, []):
                bound = geometry_bound(groups[group]['polygon'], polygon, reflection_abs,
                                       max(coordinate_scale, groups[group]['coordinate_scale']))
                if bound < 1e-7:
                    chosen = group; break
            if chosen is None:
                chosen = len(groups); bound = 0.
                groups.append(dict(polygon=polygon, contact=int(contact), triangles=owned,
                                   coordinate_scale=coordinate_scale, maximum_bound=0.))
                lookup.setdefault(key, []).append(chosen)
            member_group[i] = chosen; bounds[i] = bound
            groups[chosen]['maximum_bound'] = max(groups[chosen]['maximum_bound'], bound)
        np.savez_compressed(out/'geometry-registry.npz', contact_ids=contacts, group=member_group,
                            geometry_bound=bounds, area_m2=areas,
                            representative_contact_ids=[g['contact'] for g in groups])
        print(json.dumps(dict(stage='all_contact_geometry_verified', contacts=len(contacts), groups=len(groups),
                              maximum_geometry_bound=float(bounds.max()), elapsed_s=monotonic()-started)), flush=True)
        reference = next(c for c in json.loads(prior.read_text())['cases'] if c['kind'] == 'contact_prism_union')
        values = []; changes = []; z_bounds = payload['z_interval_um']*1e-6
        for group in groups:
            if group['contact'] == 0:
                value = complex(*reference['physical_self_per_f']); history = reference['history']
                change = history[-1]['complete_self_refinement']
            else:
                parts, fractions = prism_union(xy[group['triangles']], z_bounds)
                remaining = 5e-5*(1-group['maximum_bound'])-group['maximum_bound']
                value, history = union_self(parts, fractions, monotonic()+120, gate=remaining/(1+remaining))
                change = history[-1]['complete_self_refinement']
            assert (change/(1-change)+group['maximum_bound'])/(1-group['maximum_bound']) <= 5e-5
            values.append(value); changes.append(change)
            cases.append(dict(representative_contact=group['contact'], physical_self_per_f=[value.real,value.imag],
                              history=history, maximum_geometry_bound=group['maximum_bound']))
            (out/'representative-cases.json').write_text(json.dumps(cases, indent=2)+'\n', encoding='utf-8')
        from astra_l02_point_self import point_self
        columns = payload['contact_charge_columns']; first = int(columns[0])
        assert np.array_equal(columns, np.arange(first, first+len(contacts)))
        selected = (payload['spread_col'] >= first) & (payload['spread_col'] < first+len(contacts))
        point = point_self(payload['quadrature_points_um'][selected]*1e-6,
                           payload['spread_col'][selected]-first, payload['spread_data'][selected], len(contacts))
        assert np.isfinite(point).all()
        actual = np.asarray(values)[member_group]; refinement = np.asarray(changes)[member_group]
        combined = (refinement/(1-refinement)+bounds)/(1-bounds)
        assert np.isfinite(actual).all() and np.all(actual.real > 0) and np.all(combined <= 5e-5)
        artifact = out/'contact-self.npz'
        np.savez_compressed(artifact, charge_columns=columns, original_charge_rows=payload['contact_charge_row_ids'],
                            native_active_indices=payload['contact_global_active_index'], physical_halfspace_self_p=actual,
                            point_halfspace_self_p=point, physical_halfspace_self_delta_p=actual-point,
                            combined_refinement_geometry_indicator=combined, group=member_group)
        report = dict(status='PASS_ALL_RETAINED_L02_CONTACT_UNION_SELF', contacts=len(contacts), groups=len(groups),
                      maximum_combined_indicator=float(combined.max()), artifact_sha256=sha(artifact),
                      point_helper_sha256=sha(ROOT/'tools/research/astra_l02_point_self.py'),
                      source_pins={str(p):h for p,h in pins.items()}, cases=cases)
    except Exception:
        failure = traceback.format_exc()
        report = dict(status='STOP_CONTACT_UNION_SELF', cases=cases, failure=failure)
    report.update(elapsed_s=monotonic()-started, driver_sha256=sha(Path(__file__)),
                  scope='All retained native contact charge unions only. Positive triangle chains exactly reproduce each simple convex boundary, establishing coverage without overlap or holes. Continuous fan-map geometry bounds are evaluated with an explicit floating margin, not interval-certified. The target-relative combination with quadrature refinement remains an indicator, not a rigorous integral error bound. Each actual saved point self is subtracted once; deep diagonal stays outside. Free cells, sidewalls, nonself near terms and port response remain separate.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)
    return 0 if failure is None else 1


if __name__ == '__main__':
    raise SystemExit(run())
