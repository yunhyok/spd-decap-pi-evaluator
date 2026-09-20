"""Actual h64 cross action and h128 comparison at the same frozen source current."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'outputs/research/astra-separated-layer-grid-projection-h64-01'
OUTPUT = ROOT / 'outputs/research/astra-separated-layer-grid-mutual-h64-01'
RESULT_SHA = 'f3cb2beaf6177a5dee5fbdd035071832f0676463a7e39e07ee1347d9045551a6'
EXTERNAL_SHA = '1088dff1b5dca2e5d4375aede0b00307db5836ff728dcc0c6a0aa49c15039c55'
PROJECTION_DRIVER_SHA = '47dc2e179127dcbd743e94e08ce417dec0c007eb1a6a6b3eb45bd7c8efdc405e'
COARSE_HELPER_SHA = '08d1c614a81aceafbdffb46170deca996fe90df783be6e403431a56948b1b84e'
COARSE_RESULT_SHA = 'b7aa36d5907ac99eb65ea0170d995c1a6a4f4e8e056bb6b2646851707d525bf9'
COARSE_EXTERNAL_SHA = 'e3a30a1e8dedd0e36ecc41f8cbdb0c14ccaeaa7035b26564db0478b0ac48801f'


def dependencies():
    import hashlib
    path = ROOT / 'tools/research/run_astra_separated_layer_h128_mutual.py'
    if hashlib.sha256(path.read_bytes()).hexdigest() != COARSE_HELPER_SHA:
        raise ValueError('coarse action helper changed')
    import run_astra_separated_layer_h128_mutual as coarse
    return coarse, *coarse.dependencies()


def load_fine(coarse, projection, coarse_grids):
    result = json.loads(coarse.checked(SOURCE / 'result.json', RESULT_SHA).read_bytes())
    external = json.loads(coarse.checked(SOURCE / 'external-budget.json', EXTERNAL_SHA).read_bytes())
    projection.require(result['status'] == 'COMPLETED_H64_SEPARATED_LAYER_SOURCE_PROJECTION_NO_MAGNETIC_ACTION', 'fine projection status')
    projection.require(external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0
        and external['driver_sha256'] == coarse.PINS['probe_astra_fmm3d_runtime.py']
        and external['max_runtime_s'] == 1200 and external['max_memory_bytes'] == 24*2**30
        and external['elapsed_s'] < 1200
        and max(external['sampled_peak_private_bytes'], external['sampled_peak_working_set_bytes']) <= 24*2**30, 'fine projection external bounds')
    projection.require(result['driver']['sha256'] == PROJECTION_DRIVER_SHA, 'fine projection driver receipt')
    coarse.checked(SOURCE / 'driver-at-run.py', PROJECTION_DRIVER_SHA)
    projection.require(Path(external['worker_command'][2]).resolve() == ROOT / 'tools/research/project_astra_combined_magnetic_descriptor_h64.py', 'fine projection command')
    grids, comparisons = {}, {}
    for layer in ('L02', 'L14', 'L25'):
        name = layer.lower()
        report = result['checkpoints'][layer]
        projection.require(report['layer'] == layer and report['status'] == 'COMPLETED_H64_LAYER_PROJECTION_CHECKPOINT', 'fine checkpoint status')
        path = SOURCE / f'{name}-projection-checkpoint.npz'
        coarse.checked(path, report['checkpoint']['sha256'])
        with np.load(path, allow_pickle=False) as archive, np.load(coarse.SOURCE / path.name, allow_pickle=False) as old:
            projection.require(np.array_equal(archive['source_triangle_row_id'], old['source_triangle_row_id']), 'same original current triangles')
            projection.require(int(archive['schema_version'][0]) == 1 and archive['layer_utf8'].tobytes().decode() == layer, 'fine array schema')
            projection.require(np.array_equal(archive['origin_xy_um'], [-49728., -49728.])
                and np.array_equal(archive['shape_yx'], [1554, 1554]) and float(archive['pitch_um'][0]) == 64., 'fine grid coordinates')
            for key in ('descriptor_sha256_utf8', 'projector_sha256_utf8'):
                projection.require(np.array_equal(archive[key], old[key]), 'same current descriptor and projector')
            grid = archive['grid_integrated_current_a_m']
        projection.require(grid.shape == (1554, 1554, 2) and np.isfinite(grid).all(), 'fine current grid')
        # Same global grid edges: the fine rectangle omits one empty border cell per side.
        merged = np.pad(grid, ((1, 1), (1, 1), (0, 0))).reshape(778, 2, 778, 2, 2).sum(axis=(1, 3))
        difference = abs(merged-coarse_grids[name])
        max_error = float(difference.max()/max(np.max(abs(coarse_grids[name])), 1e-30))
        l1_error = float(difference.sum()/max(np.sum(abs(coarse_grids[name])), 1e-30))
        projection.require(max_error < 1e-7 and l1_error < 1e-7, 'fine-to-coarse integrated current conservation')
        comparisons[name] = {'maximum_error_over_global_maximum': max_error, 'l1_relative_error': l1_error}
        grids[name] = grid
    return grids, comparisons


def run(output):
    coarse, projection, apply_mutual, Budget = dependencies()
    budget = Budget.create(240, 8)
    try:
        coarse.checked(output / 'driver-at-run.py', projection.sha256(Path(__file__)))
        old_grids, slabs = coarse.load_inputs(projection)
        grids, grid_comparison = load_fine(coarse, projection, old_grids)
        del old_grids
        old_folder = coarse.OUTPUT
        old_result = json.loads(coarse.checked(old_folder / 'result.json', COARSE_RESULT_SHA).read_bytes())
        old_external = json.loads(coarse.checked(old_folder / 'external-budget.json', COARSE_EXTERNAL_SHA).read_bytes())
        projection.require(old_result['status'] == 'COMPLETED_H128_FROZEN_CURRENT_CROSS_LAYER_MAGNETIC_SENSITIVITY_ONLY'
            and old_external['status'] == 'COMPLETED_NATIVE_WORKER' and old_external['exit_code'] == 0, 'coarse action acceptance')
        budget.check('same-source coarse/fine current grids')
        actions, metrics = apply_mutual(grids, slabs, 64., budget=budget)
        bilinear = sum(np.sum(grids[name]*actions[name]) for name in grids)
        hermitian = sum(np.vdot(grids[name], actions[name]) for name in grids)
        pair_sum = sum(complex(*row['forward_bilinear_j'])+complex(*row['reverse_bilinear_j']) for row in metrics)
        scale = sum(row['operand_scale_j'] for row in metrics)
        projection.require(abs(bilinear-pair_sum) < max(scale*1e-10, 1e-30)
            and abs(hermitian.imag) < max(scale*1e-10, 1e-30), 'fine assembled energy closure')
        pair_comparison = []
        for fine, old in zip(metrics, old_result['pairs']):
            projection.require(fine['layers'] == old['layers'], 'coarse/fine pair order')
            a, b = complex(*fine['forward_bilinear_j']), complex(*old['forward_bilinear_j'])
            pair_comparison.append({'layers': fine['layers'], 'forward_difference_j': coarse.pair(a-b),
                'forward_relative_change': abs(a-b)/max(abs(b), 1e-30)})
        old_bilinear = complex(*old_result['bilinear_integral_j'])
        artifact = output / 'cross-layer-vector-potential.npz'
        projection.atomic_npz(artifact, {**{name+'_vector_potential_vs_per_m': value for name, value in actions.items()},
            'pitch_um': np.asarray([64.]), 'origin_xy_um': np.asarray([-49728., -49728.]),
            'shape_yx': np.asarray([1554, 1554]), 'frequency_hz': np.asarray([1e6]), 'source_current_a': np.asarray([1.])})
        budget.check('saved fine action')
        result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
            'status': 'COMPLETED_H64_FROZEN_CURRENT_CROSS_LAYER_MAGNETIC_REFINEMENT_ONLY',
            'frequency_hz': 1e6, 'source_current_a': 1., 'slabs_um': slabs,
            'source_projection': projection.receipt(SOURCE / 'result.json'),
            'source_external_budget': projection.receipt(SOURCE / 'external-budget.json'),
            'coarse_action': projection.receipt(old_folder / 'result.json'),
            'coarse_external_budget': projection.receipt(old_folder / 'external-budget.json'),
            'coarse_helper_sha256': COARSE_HELPER_SHA, 'kernel_sha256': coarse.PINS['apply_astra_separated_layer_grid_mutual.py'],
            'driver': projection.receipt(output / 'driver-at-run.py'), 'artifact': projection.receipt(artifact),
            'pairs': metrics, 'bilinear_integral_j': coarse.pair(bilinear),
            'cross_twice_magnetic_energy_j': coarse.pair(hermitian),
            'frozen_current_dz_dlambda_ohm': coarse.pair(1j*2*np.pi*1e6*bilinear),
            'refinement': {'current_grid_conservation': grid_comparison, 'pairs': pair_comparison,
                'bilinear_relative_change': abs(bilinear-old_bilinear)/max(abs(old_bilinear), 1e-30)},
            'budget': budget.receipt(),
            'scope': 'One h128-to-h64 refinement of the same frozen-current, uniform-slab, source-square/target-centre horizontal cross terms. No finite delta Z, self terms, complete return, PSD acceptance, feedback solve, PowerSI comparison or broadband accuracy acceptance.'}
        projection.atomic_json(output / 'result.json', result)
        print(json.dumps({'status': result['status'], 'sensitivity_ohm': result['frozen_current_dz_dlambda_ohm'],
            'refinement': result['refinement'], 'budget': result['budget']}))
    except BaseException as error:
        projection.atomic_json(output / 'failure.json', {'status': 'STOP_H64_CROSS_LAYER_ACTION',
            'error_type': type(error).__name__, 'error': str(error), 'budget': budget.receipt()})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-worker', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.native_worker:
        run(args.output)
    else:
        coarse, *_ = dependencies()
        coarse.checked(SOURCE / 'result.json', RESULT_SHA)
        coarse.checked(SOURCE / 'external-budget.json', EXTERNAL_SHA)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        from probe_astra_fmm3d_runtime import guarded_source_worker
        raise SystemExit(guarded_source_worker(args.output,
            worker_command=[sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(args.output.resolve())],
            max_runtime_s=360))
