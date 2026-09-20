"""L04 reconstructed-current cross terms with three saved h128 sheet grids.

Only the three new L04 pairs are evaluated. These conditional cross terms are
neither a circuit derivative nor a finite impedance correction.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback

import numpy as np
from scipy.signal import fftconvolve

import apply_astra_separated_layer_grid_mutual as kernel
import project_astra_l14_gc_mass as mass
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
OLD_PROJECTION = R / 'astra-separated-layer-grid-projection-h128-01'
OLD_ACTION = R / 'astra-separated-layer-grid-mutual-h128-01/result.json'
PINS = {
    Path(kernel.__file__): '7ba1d2cff420a5b9ff7792481729e3fd391e28dce7eb8aa2580d30becfc15926',
    Path(mass.__file__): 'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3',
    Path(recon.__file__): '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213',
    OLD_PROJECTION / 'result.json': '2c86ce01465faf6b9e3dab79bdf317444fd85b3a0429972b082cce76b7a7e71b',
    OLD_PROJECTION / 'external-budget.json': '59d0808276213872f4b38ea246a0b27448d5594703b8e89b2b707d447abdbe18',
    OLD_ACTION: 'b7aa36d5907ac99eb65ea0170d995c1a6a4f4e8e056bb6b2646851707d525bf9',
    OLD_ACTION.parent / 'external-budget.json': 'e3a30a1e8dedd0e36ecc41f8cbdb0c14ccaeaa7035b26564db0478b0ac48801f',
    R / 'astra-3d-source-domain-inventory-01/result.json':
        'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663',
}
LAYERS = {'L02': 'Signal$L02(DGND)', 'L04': 'Signal$L04(DGND)',
          'L14': 'Signal$L14(MAIN_POWER4)', 'L25': 'Signal$L25(MAIN_POWER4)'}


def verify(path, expected):
    assert recon._sha256_file(path) == expected, path
    return dict(path=str(path), sha256=expected, size_bytes=path.stat().st_size)


def load_grid(path):
    with np.load(path, allow_pickle=False) as z:
        grid = z['grid_integrated_current_a_m']
        assert grid.shape == (778, 778, 2) and np.all(np.isfinite(grid))
        assert np.array_equal(z['origin_xy_um'], [-49792., -49792.])
        assert np.array_equal(z['shape_yx'], [778, 778]) and float(z['pitch_um'][0]) == 128.
    return grid


def cross_pairs(grids, slabs, budget):
    actions = {name: np.zeros_like(grids['L04']) for name in grids}
    metrics = []
    for other in ('L02', 'L14', 'L25'):
        # ponytail: only the new three pairs; reuse saved old-pair totals.
        order = 3 if other == 'L02' else 2
        k = kernel.slab_kernel((778, 778), 128., slabs['L04'], slabs[other], z_order=order)
        witness = kernel.check_kernel(k, (778, 778), 128., slabs['L04'], slabs[other], order)
        to_l04, to_other = np.empty_like(grids['L04']), np.empty_like(grids['L04'])
        for component in range(2):
            to_l04[:, :, component] = fftconvolve(grids[other][:, :, component], k, mode='same')
            to_other[:, :, component] = fftconvolve(grids['L04'][:, :, component], k, mode='same')
            budget.check('new L04/' + other + ' cross action')
        assert np.all(np.isfinite(to_l04)) and np.all(np.isfinite(to_other))
        forward = np.sum(grids['L04'] * to_l04)
        reverse = np.sum(grids[other] * to_other)
        hermitian = np.vdot(grids['L04'], to_l04) + np.vdot(grids[other], to_other)
        scale = float(np.sum(abs(grids['L04'] * to_l04)) + np.sum(abs(grids[other] * to_other)))
        assert abs(forward - reverse) <= max(scale * 1e-10, 1e-30)
        assert abs(hermitian.imag) <= max(scale * 1e-10, 1e-30)
        actions['L04'] += to_l04
        actions[other] = to_other
        metrics.append(dict(layers=['L04', other], depth_order=order, kernel_checks=witness,
            forward_bilinear_j=recon._complex(forward), reverse_bilinear_j=recon._complex(reverse),
            hermitian_cross_j=recon._complex(hermitian), operand_scale_j=scale))
    return actions, metrics


def worker(args):
    output = args.output.resolve()
    assert output.is_dir() and not (output / 'result.json').exists()
    frozen = output / 'driver-at-run.py'
    assert recon._sha256_file(frozen) == recon._sha256_file(Path(__file__))
    budget = recon._Budget.create(180, 8)
    try:
        inputs = {str(path.relative_to(ROOT)): verify(path, digest) for path, digest in PINS.items()}
        inputs['new_projection_result'] = verify(args.source_result, args.source_sha256)
        inputs['new_projection_guard'] = verify(args.source_result.parent / 'external-budget.json', args.source_guard_sha256)
        current = json.loads(args.source_result.read_bytes())
        assert current['status'] == 'COMPLETED_L04_H128_FIXED_CONTACT_CURRENT_PROJECTION'
        assert current['frequency_hz'] == 1e6 and current['source_current_a'] == 1.
        parent = current['source_stream_result']
        parent_path = Path(parent['path'])
        inputs['new_current_result'] = verify(parent_path, parent['sha256'])
        parent_result = json.loads(parent_path.read_bytes())
        assert parent_result['status'] == 'PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'
        assert parent_result['inputs']['source_current_result']['sha256'] == '6c03c997c724da002e12bef0845a08cdae409f76d33428ab1bfba2f875a3a46f'
        assert parent_result['frequency_hz'] == 1e6 and parent_result['source_current_a'] == 1.
        assert parent_result['inherited_source_field_sha256'] == '960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b'
        approximation = parent_result['geometry_approximation']
        assert approximation['method'] == 'EXACT_TWO_SOURCE_VERTEX_COALESCENCE_L04_RING563'
        assert approximation['unchanged_source_geometry'] is False
        assert current['geometry_approximation'] == approximation
        for path in (OLD_PROJECTION / 'external-budget.json', OLD_ACTION.parent / 'external-budget.json',
                     args.source_result.parent / 'external-budget.json'):
            guard = json.loads(path.read_bytes())
            assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
        old = json.loads((OLD_PROJECTION / 'result.json').read_bytes())
        assert old['status'] == 'COMPLETED_H128_SEPARATED_LAYER_SOURCE_PROJECTION_NO_MAGNETIC_ACTION'
        grids = {}
        for layer in ('L02', 'L14', 'L25'):
            item = old['checkpoints'][layer]['checkpoint']
            path = Path(item['path'])
            inputs[layer + '_grid'] = verify(path, item['sha256'])
            grids[layer] = load_grid(path)
        path = Path(current['artifact']['path'])
        inputs['L04_grid'] = verify(path, current['artifact']['sha256'])
        grids['L04'] = load_grid(path)
        stack = json.loads((R / 'astra-3d-source-domain-inventory-01/result.json').read_bytes())
        slabs = {}
        for layer, name in LAYERS.items():
            rows = [row for row in stack['stackup']['conductors'] if row['name'] == name]
            assert len(rows) == 1
            slabs[layer] = [rows[0]['z_top_um'], rows[0]['z_bottom_um']]
        assert slabs == {'L02': [55, 75], 'L04': [155, 175], 'L14': [655, 675], 'L25': [1511, 1543]}
        budget.check('saved grids and source slab positions')
        actions, pairs = cross_pairs(grids, slabs, budget)
        bilinear = sum(np.sum(grids[layer] * actions[layer]) for layer in grids)
        hermitian = sum(np.vdot(grids[layer], actions[layer]) for layer in grids)
        pair_sum = sum(complex(*p['forward_bilinear_j']) + complex(*p['reverse_bilinear_j']) for p in pairs)
        scale = sum(p['operand_scale_j'] for p in pairs)
        assert abs(bilinear - pair_sum) < max(scale * 1e-10, 1e-30)
        assert abs(hermitian.imag) < max(scale * 1e-10, 1e-30)
        old_action = json.loads(OLD_ACTION.read_bytes())
        assert old_action['frequency_hz'] == 1e6 and old_action['source_current_a'] == 1.
        old_bilinear = complex(*old_action['bilinear_integral_j'])
        artifact = output / 'l04-added-cross-vector-potential.npz'
        mass.atomic_npz(artifact, **{name.lower() + '_added_vector_potential_vs_per_m': a for name, a in actions.items()},
            origin_xy_um=np.array([-49792., -49792.]), pitch_um=np.array([128.]), shape_yx=np.array([778, 778]))
        budget.check('new-pair actions saved')
        result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='COMPLETED_CONDITIONAL_L04_RECONSTRUCTED_CURRENT_CROSS_TERMS_H128',
            frequency_hz=1e6, source_current_a=1.,
            geometry_approximation=approximation,
            driver=verify(frozen, recon._sha256_file(frozen)), inputs=inputs,
            artifact=verify(artifact, recon._sha256_file(artifact)), slabs_um=slabs, pairs=pairs,
            added_conditional_bilinear_cross_j=recon._complex(bilinear),
            added_hermitian_cross_j=recon._complex(hermitian),
            saved_three_layer_bilinear_cross_j=recon._complex(old_bilinear),
            combined_conditional_bilinear_cross_j=recon._complex(old_bilinear + bilinear),
            added_jomega_cross_over_1a_squared_ohm=recon._complex(1j * 2 * np.pi * 1e6 * bilinear),
            budget=budget.receipt(),
            scope='Only three new L04 cross-layer horizontal-current pairs. L04 is a conditional '
                  'fixed-contact minimum-Joule reconstruction, while other grids are the frozen '
                  'accepted R/G/C field. Bilinear versus Hermitian terms remain distinct. The '
                  'jomega conversion is a dimensional diagnostic, not an exact circuit derivative '
                  'or a finite impedance correction. Source-square average/target-centre h128, '
                  'uniform slab depth. No spatial convergence, same-layer/self, contact interior '
                  'field, full deeper return, scalar-L replacement, PSD acceptance, feedback '
                  'solve, new board response or PowerSI accuracy acceptance.')
        mass.atomic_json(output / 'result.json', result)
        print(json.dumps(dict(status=result['status'], added_cross_j=result['added_conditional_bilinear_cross_j'],
                              budget=result['budget'])), flush=True)
    except BaseException:
        mass.atomic_json(output / 'failure.json', dict(status='STOP_L04_RECONSTRUCTED_CROSS',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-worker', action='store_true')
    parser.add_argument('--source-result', type=Path, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--source-guard-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.native_worker:
        worker(args)
    else:
        import probe_astra_fmm3d_runtime as guard
        assert recon._sha256_file(Path(guard.__file__)) == '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'
        args.output = args.output.resolve()
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', *sys.argv[1:]]
        raise SystemExit(guard.guarded_source_worker(args.output, worker_command=command, max_runtime_s=240))
