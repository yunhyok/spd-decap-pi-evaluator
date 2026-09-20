"""Owned finite-thickness L02 RT0 current cubature, with the existing R metric.

Three triangle points and two thickness points integrate affine J products
exactly. The clipped outside polygons only subdivide integration supports;
they introduce no current unknowns. Jz stays zero for the sheet model.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
import shapely

from apply_astra_owned_3d_green import TRI_BARY
from assemble_astra_g_window_sheet_volume_coupling import assemble as sheet_volume
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz': '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9',
    R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz': '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f',
    R/'astra-g-window-l02-copper-partition-20260912/copper-partition.npz': 'ea737beba15bb927ea48b0dcb65751bd836c58b73cce95f90516e5185b98d0e4',
    R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz': '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
}


def chunks(payload, chunk_size=16384):
    """Yield finite volume points and signed integrated local RT0 weights."""
    piece = payload['piece_triangle_xy_m']; parent = payload['piece_parent_free_ordinal']
    original = payload['original_free_triangle_xy_m']
    columns = payload['compact_local_columns']; signs = payload['local_signs']
    for start in range(0, len(piece), chunk_size):
        stop = min(start+chunk_size, len(piece)); local = parent[start:stop]
        xy = np.einsum('qi,tij->tqj', TRI_BARY, piece[start:stop])
        xy = np.repeat(xy, 2, axis=1)
        points = np.empty((stop-start, 6, 3)); points[:, :, :2] = xy
        points[:, :, 2] = 65e-6+np.tile([-1., 1.], 3)*10e-6/np.sqrt(3.)
        triangle = original[local]
        a = triangle[:, 1]-triangle[:, 0]; b = triangle[:, 2]-triangle[:, 0]
        twice_area = abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0])
        p = piece[start:stop]; a = p[:, 1]-p[:, 0]; b = p[:, 2]-p[:, 0]
        area = abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0])/2
        volume_weight = np.repeat((area*20e-6/6)[:, None], 6, axis=1)
        basis = np.zeros((stop-start, 6, 3, 3))
        basis[:, :, :, :2] = (xy[:, :, None, :]-triangle[:, None, :, :])/(twice_area[:, None, None, None]*20e-6)
        basis *= signs[local, None, :, None]
        yield points, columns[local], basis, volume_weight


def prepare():
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    paths = list(PINS)
    with np.load(paths[0], allow_pickle=False) as z:
        xy = z['node_xy_um']; triangles = z['triangles']
    with np.load(paths[1], allow_pickle=False) as z:
        free = z['free_triangle_indices']; old_columns = z['local_facet_branch_index']; signs = z['local_outward_flux_sign']
        branch_count = int(z['r_shape'][0])
    with np.load(paths[2], allow_pickle=False) as z:
        outside = {int(f):shapely.from_wkb(bytes.fromhex(w)) for f,w in zip(z['free_triangle_ordinals'], z['outside_wkb_hex']) if f >= 0}
    with np.load(paths[3], allow_pickle=False) as z:
        current_keep = z['retained_sheet_current_ids']
        q_keep = z['retained_sheet_charge_row_ids']
    free_keep = np.zeros(len(free), bool); free_keep[q_keep[q_keep < len(free)]] = True
    original = xy[triangles[free]]*1e-6
    untouched = np.ones(len(free), bool); untouched[list(outside)] = False
    piece_list = [original[untouched]]; parent_list = [np.flatnonzero(untouched)]
    for ordinal, polygon in outside.items():
        if not free_keep[ordinal] or polygon.is_empty or polygon.area == 0:
            continue
        pieces = list(shapely.get_parts(shapely.constrained_delaunay_triangles(polygon)))
        assert abs(sum(p.area for p in pieces)/polygon.area-1) < 1e-10
        piece_list.append(np.asarray([np.asarray(p.exterior.coords)[:-1] for p in pieces])*1e-6)
        parent_list.append(np.full(len(pieces), ordinal, np.int64))
    piece = np.concatenate(piece_list); parent = np.concatenate(parent_list)
    full_to_compact = np.full(branch_count, -1, np.int64)
    full_to_compact[current_keep] = np.arange(len(current_keep))
    compact = full_to_compact[old_columns]
    assert np.all(compact[parent] >= 0), 'removed current must have no owned integration support'
    return dict(piece_triangle_xy_m=piece, piece_parent_free_ordinal=parent,
                original_free_triangle_xy_m=original, compact_local_columns=compact,
                local_signs=signs, retained_original_current_ids=current_keep,
                z_bounds_m=np.array([55e-6, 75e-6]))


def run():
    started = monotonic(); out = R/'astra-retained-sheet-current-green-20260912-02'
    assert not out.exists(); payload = prepare()
    resistance, _, _, _, coupling = sheet_volume()
    ns = len(payload['retained_original_current_ids'])
    rng = np.random.default_rng(20260912)
    current = rng.normal(size=ns)
    full = np.r_[current, np.zeros(resistance.shape[0]-ns)]
    reference = float(full@(resistance@full))
    energy = 0.; forward_work = 0.; transpose_work = 0.; point_count = 0
    for points, columns, basis, weights in chunks(payload):
        field = np.einsum('tqic,ti->tqc', basis, current[columns])
        energy += float(np.einsum('tq,tqc,tqc->', weights, field, field)/59.59e6)
        # Independent physical affine test field exercises signed scatter/gather.
        test = np.empty_like(points)
        test[:, :, 0] = 1.+points[:, :, 0]*1e3
        test[:, :, 1] = -.25+points[:, :, 1]*1e3
        test[:, :, 2] = .5+points[:, :, 2]*1e3
        forward_work += float(np.einsum('tq,tqc,tqc->', weights, field, test))
        gathered = np.einsum('tq,tqic,tqc->ti', weights, basis, test)
        transpose_work += float(np.sum(gathered*current[columns]))
        point_count += points.shape[0]*points.shape[1]
    metric_error = abs(energy-reference)/reference
    work_error = abs(forward_work-transpose_work)/max(abs(forward_work), abs(transpose_work), 1e-30)
    assert metric_error < 5e-11 and work_error < 1e-10, (metric_error, work_error)
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'current-support.npz', **payload)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='ASSEMBLED_FINITE_THICKNESS_RETAINED_SHEET_CURRENT_CUBATURE',
                  current_count=ns, integration_prisms=len(payload['piece_triangle_xy_m']), points=point_count,
                  existing_resistance_joule_w=reference, cubature_joule_w=energy,
                  resistance_metric_relative=metric_error, spread_gather_work_relative=work_error,
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'current-support.npz'),
                  pins={str(p):h for p,h in PINS.items()}, elapsed_s=monotonic()-started,
                  scope='Every retained free-cell RT0 support is integrated over its actual outside prism at55..75um; Jz=0 and original facet current orientation preserved. No current is removed except previously owned dead currents. Three-point triangle/two-point thickness cubature exactly reproduces existing R energy. L self/near/far and electrode equipotential-interior current approximation remain separate; no full FMM or port solve.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
