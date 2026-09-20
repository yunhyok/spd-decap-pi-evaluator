"""Read L14 P1 triangle currents from the accepted hybrid field; no new solve."""
import argparse
import json
from pathlib import Path

import numpy as np

from compare_astra_hybrid_board_1mhz import load_accepted_field, sha
from reconstruct_astra_native_loaded_field import _Budget, _atomic_exclusive_json
from solve_astra_l14_sheet_sensitivity import triangle_energy

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
PINS = {
    'acceptance_helper': (ROOT/'tools/research/compare_astra_hybrid_board_1mhz.py', 'e9ce9ad63366586293364c80ec49bae77c1812c423811f06bce1c5c9a44dc1fa'),
    'gradient_helper': (ROOT/'tools/research/solve_astra_l14_sheet_sensitivity.py', 'd5ae985a139c690e3506d4625a5a2320acca32bae3e035a2c9e8f8c044bfd41c'),
    'budget_helper': (ROOT/'tools/research/reconstruct_astra_native_loaded_field.py', '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
    'accepted_result': (R/'astra-l02-hybrid-right-correction-01/result.json', '7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3'),
    'map': (R/'astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz', 'a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469'),
    'map_result': (R/'astra-l02-l14-l25-combined-assembly-map-02/result.json', 'cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81'),
    'mesh': (R/'astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz', 'a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779'),
    'drive': (R/'astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz', '05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d'),
}
SHEET_CONDUCTANCE_S = 59_590_000. * 20e-6


def self_check():
    xy = np.array([[0., 0.], [2., 0.], [0., 3.]])
    gradient = np.array([2.+3j, -1.+4j])
    voltage = xy @ gradient + 7.-2j
    for triangle in ([[0, 1, 2]], [[0, 2, 1]]):
        bilinear, joule, actual = triangle_energy(xy, np.array(triangle), voltage, 5.)
        assert np.max(abs(actual-gradient)) < 1e-14
        assert abs(joule[0]-15.*np.vdot(gradient, gradient)) < 1e-12
        assert abs(bilinear[0]-15.*(gradient @ gradient)) < 1e-12


def main(output):
    assert not output.exists()
    budget = _Budget.create(60., 4.)
    self_check()
    for name, (path, digest) in PINS.items():
        assert sha(path) == digest, name
    actual, voltage = load_accepted_field(*PINS['accepted_result'])
    assert actual['frequency_hz'] == 1e6  # loader also asserts the saved source amplitude is exactly 1 A.
    with np.load(PINS['map'][0], allow_pickle=False) as mapping:
        active = mapping['l14_sheet_active_indices']
    # These coordinates are unchanged by the L02-only condensation.
    assert np.array_equal(active, np.r_[718402, np.arange(756889, 903945)])
    with np.load(PINS['drive'][0], allow_pickle=False) as drive:
        full_to_contracted = drive['full_to_contracted']
    with np.load(PINS['mesh'][0], allow_pickle=False) as mesh:
        xy, triangles = mesh['node_xy_um'], mesh['triangles']
    assert xy.shape == (171957, 2) and triangles.shape == (214873, 3)
    assert full_to_contracted.shape == (171957,)
    assert full_to_contracted.min() == 0 and full_to_contracted.max() == len(active)-1
    assert len(np.unique(full_to_contracted)) == len(active)
    full_voltage = voltage[active[full_to_contracted]]
    bilinear, joule, gradient = triangle_energy(xy, triangles, full_voltage, SHEET_CONDUCTANCE_S)
    density = -SHEET_CONDUCTANCE_S * gradient * 1e6  # gradient was in V/um; density is A/m.
    assert np.isfinite(density).all() and np.isfinite(joule).all() and np.all(joule >= 0.)
    expected = complex(*actual['physical']['physical']['power_contributions_ohm']['l14_sheet_dc'])
    difference = abs(float(joule.sum())-expected)
    relative = difference / expected.real
    assert expected.real > 0 and relative < 1e-7
    budget.check('accepted L14 P1 current/energy readback')
    output.mkdir(parents=True)
    frozen = output/'driver-at-run.py'
    frozen.write_bytes(Path(__file__).read_bytes())
    artifact = output/'l14-p1-current-density.npz'
    with artifact.open('xb') as stream:
        np.savez_compressed(stream, l14_sheet_active_indices=active,
                            sheet_current_density_a_per_m=density,
                            triangle_joule_w=joule, triangle_bilinear_va=bilinear)
    budget.check('saved L14 current artifact')
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_ACCEPTED_HYBRID_L14_P1_CURRENT_ENERGY_READBACK',
              'driver_sha256': sha(frozen),
              'inputs': {name: {'path': str(path), 'sha256': digest} for name, (path, digest) in PINS.items()},
              'accepted_field': actual['field'], 'frequency_hz': 1e6, 'source_current_amplitude_a': 1.,
              'counts': {'nodes': len(xy), 'triangles': len(triangles), 'contracted_potentials': len(active)},
              'sheet_conductance_s': SHEET_CONDUCTANCE_S,
              'gradient_joule_w': float(joule.sum()), 'original_category_power_va': [expected.real, expected.imag],
              'joule_difference_va': difference, 'joule_relative_difference': relative,
              'artifact': {'path': str(artifact), 'sha256': sha(artifact), 'size_bytes': artifact.stat().st_size},
              'budget': budget.receipt(),
              'scope': 'Current density from the accepted field on the unchanged source P1 mesh; piecewise constant gradient with weak nodal conservation. No H(div)/RT0 continuity qualification, new solve, source geometry, magnetic action, reference or accuracy claim.'}
    _atomic_exclusive_json(output/'result.json', result)
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('PASS_COMPLEX_AFFINE_TRIANGLE_GRADIENT_BOTH_ORIENTATIONS')
    else:
        assert args.output
        main(args.output.resolve())
