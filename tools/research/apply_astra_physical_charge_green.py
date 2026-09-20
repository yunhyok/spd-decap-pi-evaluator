"""Physical halfspace plus deep-layer P with owned support self replacement."""
import json
import sys
from pathlib import Path
from time import monotonic

import numpy as np

from apply_astra_owned_3d_green import TET_BARY, TRI_BARY
from astra_layered_charge_action import halfspace_action, source_target_fmm, DeepRemainderAction
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def apply(points, columns, weights, self_delta, charge, deep_operator, point_action=source_target_fmm,
          near_delta=None, analytic_action=halfspace_action):
    """Return physical volts. Deep diagonal stays; bare self is not added again."""
    charge = np.asarray(charge, complex)
    vector = charge.ndim == 1
    if vector:
        charge = charge[:, None]
    assert charge.shape[0] == len(self_delta)
    point_charge = weights[:, None]*charge[columns]
    potential = analytic_action(points, point_charge, point_action=point_action, precision=1e-9)
    potential += deep_operator.apply(point_charge)
    answer = self_delta[:, None]*charge
    np.add.at(answer, columns, weights[:, None]*potential)
    if near_delta is not None:
        assert near_delta.shape == (len(self_delta), len(self_delta))
        assert np.all(near_delta.diagonal() == 0), 'self is already replaced'
        answer += near_delta @ charge
    assert np.isfinite(answer).all()
    return answer[:, 0] if vector else answer


def check_actual_supports():
    started = monotonic()
    pwr = R/'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz'
    g = R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz'
    self_path = R/'astra-halfspace-charge-self-full-20260912/full-pwr-g-halfspace-self.npz'
    pins = {pwr:'a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b',
            g:'72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
            self_path:'8f11ba2ee16f4a06d05b743209e4e4462be1abd1e6ddbe6016a8b2311b3c94b9'}
    for path, expected in pins.items():
        assert sha(path) == expected, path
    with np.load(pwr, allow_pickle=False) as z:
        xyz = z['vertices_um']; cells = z['cells']; free_faces = z['face_vertices'][z['free_surface_face_ids']]
        tetra = xyz[cells]; triangles = xyz[free_faces]
        post = int(z['selected_port_source_terminal_index'][0]); pwr_cells = len(cells)
        center = z['post_translation_xyz_um'][post]
    on_interface = np.flatnonzero(np.all(abs(triangles[:, :, 2]-25.) < 1e-10, axis=1))
    sf = int(on_interface[np.argmin(np.linalg.norm(triangles[on_interface].mean(axis=1)[:, :2]-center[:2], axis=1))])
    supports = list(tetra[post*2604+np.arange(3)])+[triangles[sf]]
    with np.load(g, allow_pickle=False) as z:
        gt = z['volume_charge_vertices_um']; gf = z['surface_charge_vertices_um']
    gi = int(np.flatnonzero(np.all(abs(gf[:, :, 2]-25.) < 1e-10, axis=1))[0])
    supports += list(gt[:3])+[gf[gi]]
    selected = np.r_[post*2604+np.arange(3), pwr_cells+len(gt)+sf,
                     pwr_cells+np.arange(3), pwr_cells+len(gt)+len(triangles)+gi]
    with np.load(self_path, allow_pickle=False) as z:
        delta = z['physical_halfspace_self_delta_p'][selected]
    points = []; columns = []; weights = []
    for i, vertices in enumerate(supports):
        bary = TET_BARY if len(vertices) == 4 else TRI_BARY
        # Exactly the same einsum rule as the full support self correction.
        p = np.einsum('qi,tid->tqd', bary, vertices[None])[0]*1e-6
        points.extend(p); columns.extend([i]*len(p)); weights.extend([1/len(p)]*len(p))
    points = np.asarray(points); columns = np.asarray(columns); weights = np.asarray(weights)
    deep = DeepRemainderAction(points, spacing_m=62.5e-6, radial_count=768)
    matrix = apply(points, columns, weights, delta, np.eye(len(supports)), deep)
    reciprocity = float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix))
    eigenvalues = np.linalg.eigvalsh((matrix.real+matrix.real.T)/2)
    assert reciprocity < 1e-10 and eigenvalues.min() > 0, (reciprocity, eigenvalues.min())
    out = R/'astra-physical-charge-action-integration-20260912'; out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'actual-support-block.npz', complete_point_order_charge_rows=selected,
                        physical_p_block_per_f=matrix, self_delta_per_f=delta)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_PHYSICAL_SELF_HALFSPACE_DEEP_INTEGRATION_ACTUAL_SUPPORTS',
                  charge_rows=selected.tolist(), count=len(supports), point_count=len(points),
                  reciprocity_relative=reciprocity, minimum_real_symmetric_eigenvalue_per_f=float(eigenvalues.min()),
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'actual-support-block.npz'),
                  pins={str(p):h for p,h in pins.items()}, elapsed_s=monotonic()-started,
                  scope='Eight actual adjacent volume/interface support functions from selected PWR and G, with all block interactions and physical self corrections. Checks integration units, order, reciprocity and positivity only for this block; mutual-near cubature accuracy, full support operator and board response are not certified.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


def apply_actual_g(general=False):
    """First complete G physical P action; all mutual-near uncertainty remains."""
    from astra_fmm3d_tree_tuning import LeafTunedAction
    from astra_layered_charge_action import _direct_points
    from astra_layered_charge_action import StratifiedPlaneRemainderAction, stratified_analytic_action
    started = monotonic()
    paths = {
        R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz':'72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
        R/'astra-halfspace-charge-self-full-20260912/full-pwr-g-halfspace-self.npz':'8f11ba2ee16f4a06d05b743209e4e4462be1abd1e6ddbe6016a8b2311b3c94b9',
        R/'astra-g-all-pair-current-charge-action-20260912/action.npz':'6b4f30c58258fd77855fb259cb8c878dc63db48642e9a2da4e28d43a3062a4b2'}
    for path, digest in paths.items():
        assert sha(path) == digest, path
    with np.load(list(paths)[0], allow_pickle=False) as z:
        tetra = z['volume_charge_vertices_um']*1e-6; faces = z['surface_charge_vertices_um']*1e-6
    nc, nf = len(tetra), len(faces)
    assert (nc, nf) == (6078, 3008)
    points = np.concatenate([np.einsum('qi,tid->tqd', TET_BARY, tetra).reshape(-1, 3),
                             np.einsum('qi,tid->tqd', TRI_BARY, faces).reshape(-1, 3)])
    columns = np.r_[np.repeat(np.arange(nc), 4), np.repeat(nc+np.arange(nf), 3)]
    weights = np.r_[np.full(4*nc, .25), np.full(3*nf, 1/3)]
    full_rows = np.r_[288804+np.arange(nc), 288804+nc+145516+np.arange(nf)]
    with np.load(list(paths)[1], allow_pickle=False) as z:
        assert z['physical_halfspace_self_delta_p'].shape == (443406,)
        delta = z['physical_halfspace_self_delta_p'][full_rows]
    with np.load(list(paths)[2], allow_pickle=False) as z:
        charge = z['charges'].T
    assert charge.shape == (nc+nf, 2)
    remainder_class = StratifiedPlaneRemainderAction if general else DeepRemainderAction
    analytic = stratified_analytic_action if general else halfspace_action
    deep = remainder_class(points, spacing_m=62.5e-6, radial_count=768)
    actor = LeafTunedAction(3200, 4)
    action_started = monotonic()
    answer = apply(points, columns, weights, delta, charge, deep, point_action=actor.source_target,
                   analytic_action=analytic)
    action_seconds = monotonic()-action_started
    # Independently evaluate the complete source density at selected support targets.
    selected = np.unique(np.r_[0, 1, 2, nc-1, nc, nc+200, nc+nf-1])
    mask = np.isin(columns, selected)
    density = weights[:, None]*charge[columns]
    target_half = analytic(points, density, targets=points[mask], point_action=_direct_points)
    target_deep = deep.apply(density)[mask]
    expected = delta[selected, None]*charge[selected]
    np.add.at(expected, np.searchsorted(selected, columns[mask]), weights[mask, None]*(target_half+target_deep))
    direct_error = float(np.linalg.norm(answer[selected]-expected)/np.linalg.norm(expected))
    xy, yx = charge[:, 0]@answer[:, 1], charge[:, 1]@answer[:, 0]
    reciprocal = float(abs(xy-yx)/max(abs(xy), abs(yx), 1e-30))
    assert direct_error < 1e-7 and reciprocal < 1e-7
    prior_difference = None
    if general:
        reference_path = R/'astra-g-physical-charge-action-20260912/action.npz'
        assert sha(reference_path) == '41f13d2063755a61c2538f52340bf9e7a8a642465fe93a1e539b28a3ca44d4a1'
        with np.load(reference_path, allow_pickle=False) as z:
            assert np.array_equal(charge, z['charge'])
            prior_difference = float(np.linalg.norm(answer-z['potential_v'])/np.linalg.norm(z['potential_v']))
        assert prior_difference < 5e-5
    out = R/('astra-g-general-stratified-charge-action-20260912' if general else 'astra-g-physical-charge-action-20260912')
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'action.npz', charge=charge, potential_v=answer,
                        complete_pwr_g_charge_rows=full_rows, direct_check_rows=selected,
                        direct_check_potential_v=expected)
    report = dict(status='COMPLETED_G_PHYSICAL_POINT_SELF_ACTION', charges=nc+nf, points=len(points),
                  action_seconds=action_seconds, elapsed_s=monotonic()-started,
                  direct_target_relative=direct_error, reciprocity_relative=reciprocal,
                  general_strata=general, original_halfspace_deep_difference_relative=prior_difference,
                  remainder_receipt=deep.receipt,
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'action.npz'),
                  pins={str(p):h for p,h in paths.items()},
                  scope='All actual G volume/free-face charges, physical halfspace and deep-layer action with owned self replacement. Mutual-near quadrature remains uncorrected; no global P accuracy or physical port solve claim.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    apply_actual_g('--general' in sys.argv) if '--actual-g' in sys.argv else check_actual_supports()
