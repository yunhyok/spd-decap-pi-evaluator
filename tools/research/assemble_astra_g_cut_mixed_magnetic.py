"""SPD Decap PI Evaluator v0.23.1: finite-thickness sheet/3D cut mutual L.

Each of the 12 cut-face bases and 46 sheet bases uses its complete remaining
support. This rectangular block is only part of the full physical L operator.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
import shapely
from shapely.geometry import Polygon
from scipy import sparse
from prepare_astra_l14_rt0_reconstruction_space import sha
from prepare_astra_conforming_power_joint import prisms, positive
from qualify_astra_tetra_volume_green import tetra_inner, tetra_quadrature, tetra_pair

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-g-cut-mixed-magnetic-20260912'
PINS = {
    R/'astra-g-window-l02-copper-partition-20260912/copper-partition.npz': 'ea737beba15bb927ea48b0dcb65751bd836c58b73cce95f90516e5185b98d0e4',
    R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz': '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9',
    R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz': '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f',
    R/'astra-g-window-l02-rt0-trace-20260912/trace-and-overlap.npz': '347422e14e8819b2cff70fea366b4c8a58cb5df5e1a1240719dd6f45d45b954e',
    R/'astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz': '600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5',
    ROOT/'tools/research/qualify_astra_tetra_volume_green.py': '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    ROOT/'tools/research/prepare_astra_conforming_power_joint.py': 'ec1485a02019bbd04cd17a084c17a0ebbca683fb25cdec63361817ff69b8eb8b',
}


def affine_pair(observer, observer_values, source, source_values, order):
    """General affine vector fields, including Jz=0 extruded sheet RT0."""
    origin = observer[0]
    observer = observer-origin; source = source-origin
    og = np.linalg.solve(observer[1:]-observer[0], (observer_values[1:]-observer_values[0]).reshape(3, -1)).reshape(3, -1, 3)
    sg = np.linalg.solve(source[1:]-source[0], (source_values[1:]-source_values[0]).reshape(3, -1)).reshape(3, -1, 3)
    points, weights = tetra_quadrature(observer, order)
    scalar, moment = tetra_inner(source, points)
    test = observer_values[0]+np.einsum('pa,aid->pid', points-observer[0], og)
    trial = source_values[0]+np.einsum('pa,aid->pid', points-source[0], sg)
    inner = trial*scalar[:, None, None]+np.einsum('pa,aid->pid', moment, sg)
    return 1e-7*np.einsum('p,pid,pjd->ij', weights, test, inner)


def run():
    started = monotonic()
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    paths = list(PINS)
    with np.load(paths[0], allow_pickle=False) as z:
        outside = {int(t): shapely.from_wkb(bytes.fromhex(w)) for t, w in zip(z['original_triangle_ids'], z['outside_wkb_hex'])}
    with np.load(paths[1], allow_pickle=False) as z:
        xy = z['node_xy_um']; triangles = z['triangles']
    with np.load(paths[2], allow_pickle=False) as z:
        free = z['free_triangle_indices']; local = z['local_facet_branch_index']; signs = z['local_outward_flux_sign']
    with np.load(paths[3], allow_pickle=False) as z:
        selected = np.unique(z['trace_col']); cut = z['cut_face_ids']
    with np.load(paths[4], allow_pickle=False) as z:
        xyz = z['vertices_um']*1e-6; cells = z['cells']; faces = z['face_vertices']
        cut_cells = z['first_owner_cell'][cut]
        opposite = z['first_owner_local_face'][cut]
    volume_tetra = xyz[cells[cut_cells]]
    vv = abs(np.linalg.det(volume_tetra[:, 1:]-volume_tetra[:, :1]))/6
    volume_values = [(tet-tet[int(opp)])[..., None, :]/(3*vol) for tet, opp, vol in zip(volume_tetra, opposite, vv)]
    # Local opposite-vertex convention independently reproduces the qualified
    # tetra RT0 Green result before adding the anisotropic sheet field.
    witness = volume_tetra[0]
    volume = vv[0]
    values = (witness[:, None, :]-witness[None, :, :])/(3*volume)
    expected = tetra_pair(witness, witness, 4)
    actual = affine_pair(witness, values, witness, values, 4)
    reproduction = float(np.linalg.norm(actual-expected)/np.linalg.norm(expected))
    assert reproduction < 1e-10
    free_rows = np.flatnonzero(np.isin(local, selected).any(axis=1))
    pieces = []; support_area = {}; full_outside_area = 0.
    for fi in free_rows:
        ti = int(free[fi]); v = xy[triangles[ti]]
        polygon = outside.get(ti, Polygon(v))
        if polygon.area == 0:
            continue
        full_outside_area += polygon.area
        twice_area = abs(np.linalg.det(v[1:]-v[0]))*1e-12
        tri_pieces = list(shapely.get_parts(shapely.constrained_delaunay_triangles(polygon)))
        for piece in tri_pieces:
            pxy = np.asarray(piece.exterior.coords)[:-1]
            xyz_piece = np.vstack([np.column_stack([pxy, np.full(3, z)]) for z in (55., 75.)])*1e-6
            tetra = xyz_piece[positive(xyz_piece, prisms(np.array([[0, 1, 2]]), np.arange(3), np.arange(3)+3))]
            for tet in tetra:
                j = np.zeros((4, 3, 3))
                j[:, :, :2] = (tet[:, None, :2]-v[None, :, :]*1e-6)*signs[fi][None, :, None]/(twice_area*20e-6)
                cols = np.searchsorted(selected, local[fi])
                use = np.isin(local[fi], selected)
                pieces.append((tet, j[:, use], cols[use], ti))
            support_area[ti] = support_area.get(ti, 0.)+piece.area
    area_error = abs(sum(support_area.values())/full_outside_area-1)
    assert area_error < 1e-10
    history = []; final_forward = None; final_reverse = None
    for order in (4, 8):
        forward = np.zeros((len(cut), len(selected))); reverse = np.zeros_like(forward)
        for row, (tet, val) in enumerate(zip(volume_tetra, volume_values)):
            for st, sv, cols, _ in pieces:
                forward[row, cols] += affine_pair(tet, val, st, sv, order)[0]
                reverse[row, cols] += affine_pair(st, sv, tet, val, order)[:, 0]
            if row % 4 == 3:
                print(f'Actual cut mixed L order {order}: {row+1}/{len(cut)} rows', flush=True)
        assert np.isfinite(forward).all() and np.isfinite(reverse).all()
        symmetric = (forward+reverse)/2
        history.append(dict(order=order, raw_reciprocity_relative=float(np.linalg.norm(forward-reverse)/np.linalg.norm(symmetric)),
                            block_norm_h=float(np.linalg.norm(symmetric)),
                            order_change_relative=None if final_forward is None else float(np.linalg.norm(symmetric-(final_forward+final_reverse)/2)/np.linalg.norm(symmetric))))
        final_forward, final_reverse = forward, reverse
    OUT.mkdir(exist_ok=False)
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(OUT/'mixed-magnetic-block.npz', cut_face_ids=cut, sheet_branch_ids=selected,
                        forward_h=final_forward, reverse_h=final_reverse, mutual_h=(final_forward+final_reverse)/2,
                        sheet_support_free_rows=free_rows, sheet_support_triangle_ids=free[free_rows])
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='ASSEMBLED_ACTUAL_CUT_MIXED_MAGNETIC_BLOCK',
                  pins={str(p): h for p, h in PINS.items()}, driver_sha256=sha(Path(__file__)),
                  artifact_sha256=sha(OUT/'mixed-magnetic-block.npz'), elapsed_s=monotonic()-started,
                  counts=dict(cut_basis_count=len(cut), sheet_basis_count=len(selected), sheet_support_cells=len(free_rows), sheet_tetra_integration_pieces=len(pieces)),
                  rt0_primitive_reproduction_relative=reproduction, integration_area_relative=area_error,
                  quadrature=history,
                  scope='Physical mu0/(4pi) rectangular mutual L for all12cut-face RT0 bases and all46 traced sheet bases, including complete outside support at20um thickness; Jz=0 retained exactly. Integration subdivisions add no current unknowns. Both directed quadratures and their arithmetic mean are saved; error is not hidden. This is a real off-diagonal L component, not the full self/mutual kernel, dielectric P, retarded remainder, board response or accuracy certificate.')
    (OUT/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
