"""Frozen-current h128 cross-layer magnetic sensitivity; no feedback solve."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'outputs/research/astra-separated-layer-grid-projection-h128-01'
OUTPUT = ROOT / 'outputs/research/astra-separated-layer-grid-mutual-h128-01'
RESULT_SHA = '2c86ce01465faf6b9e3dab79bdf317444fd85b3a0429972b082cce76b7a7e71b'
EXTERNAL_SHA = '59d0808276213872f4b38ea246a0b27448d5594703b8e89b2b707d447abdbe18'
PINS = {
    'project_astra_combined_magnetic_descriptor_h128.py': 'd1214640f10587860969010c3dd39ea4700127498b885227ac397a3a56e637bc',
    'apply_astra_separated_layer_grid_mutual.py': '7ba1d2cff420a5b9ff7792481729e3fd391e28dce7eb8aa2580d30becfc15926',
    'reconstruct_astra_native_loaded_field.py': '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213',
    'probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
}


def checked(path, digest):
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != digest:
        raise ValueError(f'SHA mismatch: {path}')
    return path


def dependencies():
    for name, digest in PINS.items():
        checked(ROOT / 'tools/research' / name, digest)
    import project_astra_combined_magnetic_descriptor_h128 as projection
    from apply_astra_separated_layer_grid_mutual import apply_mutual
    from reconstruct_astra_native_loaded_field import _Budget
    return projection, apply_mutual, _Budget


def load_inputs(projection):
    result = json.loads(checked(SOURCE / 'result.json', RESULT_SHA).read_bytes())
    external = json.loads(checked(SOURCE / 'external-budget.json', EXTERNAL_SHA).read_bytes())
    projection.require(result['status'] == 'COMPLETED_H128_SEPARATED_LAYER_SOURCE_PROJECTION_NO_MAGNETIC_ACTION', 'projection status')
    projection.require(external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0, 'projection guard status')
    projection.require(external['driver_sha256'] == PINS['probe_astra_fmm3d_runtime.py'], 'projection guard pin')
    projection.require(external['max_runtime_s'] == 600 and external['max_memory_bytes'] == 24*2**30
                       and external['elapsed_s'] < 600
                       and max(external['sampled_peak_private_bytes'], external['sampled_peak_working_set_bytes']) <= 24*2**30, 'projection resource bounds')
    driver_sha = PINS['project_astra_combined_magnetic_descriptor_h128.py']
    projection.require(result['driver']['sha256'] == driver_sha, 'projection driver receipt')
    checked(SOURCE / 'driver-at-run.py', driver_sha)
    projection.require(Path(external['worker_command'][2]).resolve() == ROOT / 'tools/research/project_astra_combined_magnetic_descriptor_h128.py', 'projection worker command')
    projection.verify_pins()
    grids, slabs = {}, {}
    with np.load(projection.PINS['descriptor'][0], allow_pickle=False) as archive:
        for layer in ('L02', 'L14', 'L25'):
            name = layer.lower()
            id_key = 'l25_step06_triangle_index' if layer == 'L25' else name+'_original_triangle_index'
            checkpoint = projection.verify_checkpoint(SOURCE, layer, archive[id_key])
            projection.require(checkpoint == result['checkpoints'][layer], f'{layer} checkpoint receipt')
            with np.load(projection.checkpoint_paths(SOURCE, layer)[0], allow_pickle=False) as grid_file:
                grids[name] = grid_file['grid_integrated_current_a_m']
            slabs[name] = archive[name+'_slab_z_um'].tolist()
    projection.require(slabs == {'l02': [55, 75], 'l14': [655, 675], 'l25': [1511, 1543]}, 'source slab positions')
    return grids, slabs


def pair(value):
    return [float(value.real), float(value.imag)]


def run(output):
    projection, apply_mutual, Budget = dependencies()
    budget = Budget.create(120, 8)
    try:
        checked(output / 'driver-at-run.py', projection.sha256(Path(__file__)))
        grids, slabs = load_inputs(projection)
        budget.check('accepted projection inputs')
        actions, metrics = apply_mutual(grids, slabs, 128., budget=budget)
        bilinear = sum(np.sum(grids[name]*actions[name]) for name in grids)
        hermitian = sum(np.vdot(grids[name], actions[name]) for name in grids)
        pair_sum = sum(complex(*row['forward_bilinear_j'])+complex(*row['reverse_bilinear_j']) for row in metrics)
        scale = sum(row['operand_scale_j'] for row in metrics)
        projection.require(abs(bilinear-pair_sum) < max(scale*1e-10, 1e-30), 'pair/assembled energy closure')
        projection.require(abs(hermitian.imag) < max(scale*1e-10, 1e-30), 'assembled Hermitian closure')
        artifact = output / 'cross-layer-vector-potential.npz'
        projection.atomic_npz(artifact, {**{name+'_vector_potential_vs_per_m': value for name, value in actions.items()},
            'pitch_um': np.asarray([128.]), 'origin_xy_um': projection.ORIGIN_UM,
            'shape_yx': projection.SHAPE_YX, 'frequency_hz': np.asarray([1e6]), 'source_current_a': np.asarray([1.])})
        budget.check('saved actual cross action')
        result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
            'status': 'COMPLETED_H128_FROZEN_CURRENT_CROSS_LAYER_MAGNETIC_SENSITIVITY_ONLY',
            'frequency_hz': 1e6, 'source_current_a': 1., 'slabs_um': slabs,
            'source_projection': projection.receipt(SOURCE / 'result.json'),
            'source_external_budget': projection.receipt(SOURCE / 'external-budget.json'),
            'driver': projection.receipt(output / 'driver-at-run.py'),
            'pins': PINS, 'artifact': projection.receipt(artifact), 'pairs': metrics,
            'bilinear_integral_j': pair(bilinear), 'cross_twice_magnetic_energy_j': pair(hermitian),
            'frozen_current_dz_dlambda_ohm': pair(1j*2*np.pi*1e6*bilinear),
            'budget': budget.receipt(),
            'scope': 'Directional sensitivity at the accepted 1 A R/G/C current, not a finite delta Z or a new board response. Source-square/target-centre h128 grid; uniform current through each original slab. No h64 convergence yet. Cross-layer horizontal terms only: no same-layer/self, posts, deeper paths, scalar-L replacement, PSD acceptance, feedback solve or PowerSI reference read.'}
        projection.atomic_json(output / 'result.json', result)
        print(json.dumps({'status': result['status'], 'sensitivity_ohm': result['frozen_current_dz_dlambda_ohm'], 'budget': result['budget']}))
    except BaseException as error:
        projection.atomic_json(output / 'failure.json', {'status': 'STOP_H128_CROSS_LAYER_ACTION',
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
        dependencies()
        checked(SOURCE / 'result.json', RESULT_SHA)
        checked(SOURCE / 'external-budget.json', EXTERNAL_SHA)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        from probe_astra_fmm3d_runtime import guarded_source_worker
        raise SystemExit(guarded_source_worker(args.output,
            worker_command=[sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(args.output.resolve())],
            max_runtime_s=180))
