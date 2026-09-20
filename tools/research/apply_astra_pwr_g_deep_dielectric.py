"""Apply and refine the smooth deeper-dielectric correction on actual PWR/G q."""
import json
from pathlib import Path
from time import monotonic

import numpy as np

from apply_astra_owned_3d_green import TET_BARY, TRI_BARY
from astra_layered_charge_action import DeepRemainderAction
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    R/'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz': 'a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b',
    R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz': '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
    ROOT/'tools/research/astra_layered_charge_action.py': '95491093aab0392690fd442ee1af8a389734d20a7d9f52dd580f4cb4608e4b73',
    ROOT/'tools/research/astra_stratified_charge_green.py': '5808ad457d3d58b47413f47f02e30ce5f51c3365f16eb4d2562b2a742007451b',
}


def run():
    started = monotonic(); out = R/'astra-pwr-g-deep-dielectric-action-20260912'
    assert not out.exists()
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    with np.load(list(PINS)[0], allow_pickle=False) as z:
        xyz = z['vertices_um']; tetra = xyz[z['cells']]
        triangles = xyz[z['face_vertices'][z['free_surface_face_ids']]]
        ni = int(z['resistance_shape'][0])
    with np.load(list(PINS)[1], allow_pickle=False) as z:
        tetra = np.concatenate([tetra, z['volume_charge_vertices_um']])
        triangles = np.concatenate([triangles, z['surface_charge_vertices_um']])
        ni += int(z['local_resistance_shape'][0])
    nc = len(tetra); nf = len(triangles)
    points = np.vstack([np.einsum('qi,tid->tqd', TET_BARY, tetra).reshape(-1, 3),
                        np.einsum('qi,tid->tqd', TRI_BARY, triangles).reshape(-1, 3)])*1e-6
    rng = np.random.default_rng(20260912)
    rng.normal(size=ni); rng.normal(size=ni)  # Same charge witness as the running raw P/L action.
    charge = rng.normal(size=nc+nf)+1j*rng.normal(size=nc+nf)
    weighted = np.r_[np.repeat(charge[:nc]/4, 4), np.repeat(charge[nc:]/3, 3)]
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    history = []; actions = []
    for spacing in (125e-6, 62.5e-6):
        step = monotonic()
        operator = DeepRemainderAction(points, spacing_m=spacing, radial_count=768, vertical_degree=3)
        print(json.dumps(dict(stage='deep_dielectric_start', spacing_m=spacing, points=len(points))), flush=True)
        point_potential = operator.apply(weighted)[:, 0]
        action = np.r_[point_potential[:4*nc].reshape(nc, 4).mean(axis=1),
                        point_potential[4*nc:].reshape(nf, 3).mean(axis=1)]
        assert np.isfinite(action).all()
        actions.append(action)
        history.append(dict(configuration=operator.receipt, elapsed_s=monotonic()-step,
                            action_norm_v=float(np.linalg.norm(action))))
        print(json.dumps(dict(stage='deep_dielectric_done', spacing_m=spacing, elapsed_s=monotonic()-step)), flush=True)
    change = float(np.linalg.norm(actions[1]-actions[0])/np.linalg.norm(actions[1]))
    artifact = out/'action.npz'
    np.savez_compressed(artifact, integrated_charge=charge, coarse_potential_v=actions[0], fine_potential_v=actions[1])
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='EXECUTED_ACTUAL_PWR_G_DEEP_DIELECTRIC_REFINEMENT',
                  charge_count=len(charge), point_count=len(points), current_count_for_rng=ni,
                  full_action_refinement_relative=change, within_existing_point_check_2e5_gate=bool(change < 2e-5),
                  history=history, elapsed_s=monotonic()-started,
                  pins={str(p):h for p,h in PINS.items()}, driver_sha256=sha(Path(__file__)), artifact_sha256=sha(artifact),
                  scope='Actual full PWR/G support cubature and one arbitrary complex integrated-charge pattern, smooth deeper-stack correction only. Its point diagonal remains included. The halfspace/direct/image self and near corrections are separate; no universal density error bound, full board P, port solve or PowerSI improvement is claimed.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
