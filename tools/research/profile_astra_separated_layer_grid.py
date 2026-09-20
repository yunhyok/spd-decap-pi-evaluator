"""Profile exact affine grid spreading on pinned actual source-cell witnesses."""
import argparse
import json
from pathlib import Path

import numpy as np

from compare_astra_hybrid_board_1mhz import sha
from project_astra_separated_layer_grid import project, self_check
from reconstruct_astra_native_loaded_field import _Budget, _atomic_exclusive_json

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'outputs/research/astra-combined-magnetic-source-descriptor-02'
PINS = {
    'projector': (ROOT/'tools/research/project_astra_separated_layer_grid.py', '49d823d9f4c625e95b29775cf02acb7900a8b287c51c2babfe3842f5f24a6415'),
    'source_driver': (SOURCE/'driver-at-run.py', '90642cf3d8a278aff6c93abd1ce79ad3291b8edcb6be1a34dcd1fff98d9efbc1'),
    'source_result': (SOURCE/'result.json', 'e906455926523a0872d82c1fdb715c8513c078b07cf3bc44921fa0a1c18095dc'),
    'source': (SOURCE/'combined-magnetic-source-descriptor.npz', '7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02'),
}


def run(output):
    assert not output.exists()
    budget = _Budget.create(90., 4.)
    for name, (path, digest) in PINS.items():
        assert sha(path) == digest, name
    self_check()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    (output/'projector-at-run.py').write_bytes(PINS['projector'][0].read_bytes())
    rows = []
    try:
        with np.load(PINS['source'][0], allow_pickle=False) as source:
            vertices = {layer: source[layer+'_triangle_vertices_um'] for layer in ('l02', 'l14', 'l25')}
            pitch = 128.
            origin = np.floor(np.min([v.min((0, 1)) for v in vertices.values()], axis=0)/pitch)*pitch
            maximum = np.max([v.max((0, 1)) for v in vertices.values()], axis=0)
            shape = tuple(map(int, (np.floor((maximum-origin)/pitch).astype(int)+1)[::-1]))
            assert shape == (778, 778)
            full_candidates = 0
            for layer, v in vertices.items():
                first = np.floor((v.min(1)-origin)/pitch).astype(np.int64)
                last = np.floor((v.max(1)-origin)/pitch).astype(np.int64)
                counts = (last-first+1).prod(1)
                full_candidates += int(counts.sum())
                local = v-v[:, :1]
                area = abs(local[:, 1, 0]*local[:, 2, 1]-local[:, 1, 1]*local[:, 2, 0])/2
                selected = np.unique(np.r_[np.argsort(counts)[-12:], np.argsort(area)[:12],
                                            np.linspace(0, len(v)-1, 64, dtype=int)])
                mean = source[layer+'_sheet_current_density_a_per_m'] if layer == 'l14' else source[layer+'_average_current_a_per_m']
                alpha = np.zeros(len(v), complex) if layer == 'l14' else source[layer+'_affine_coefficient_a_per_m2']
                grid, metrics = project(v[selected], mean[selected], alpha[selected], origin, pitch, shape, budget=budget)
                assert np.isfinite(grid).all()
                rows.append({'layer': layer, 'descriptor_triangle_rows': selected.tolist(), 'metrics': metrics})
                del grid, mean, alpha, local
            assert full_candidates == 12_369_213
        budget.check('all actual-source projection witnesses')
        result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
                  'status': 'PASS_ACTUAL_SOURCE_GRID_PROJECTION_PROFILE',
                  'driver_sha256': sha(output/'driver-at-run.py'),
                  'inputs': {name: {'path': str(path), 'sha256': digest} for name, (path, digest) in PINS.items()},
                  'pitch_um': pitch, 'origin_um': origin.tolist(), 'shape_yx': shape,
                  'actual_full_bbox_candidates': full_candidates, 'rows': rows, 'budget': budget.receipt(),
                  'scope': 'Actual accepted current/geometry witnesses only: largest bbox, smallest area and evenly spaced source-row selection. Exact affine clipped moments with floating-point checks; no renormalization, full source projection, FFT/FMM, magnetic integral, new solve or accuracy claim. Profile timing is not a guaranteed full-run bound.'}
        _atomic_exclusive_json(output/'result.json', result)
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:
        _atomic_exclusive_json(output/'failure.json', {'status': 'STOP_ACTUAL_SOURCE_GRID_PROFILE',
                              'error': repr(exc), 'completed_rows': rows, 'budget': budget.receipt()})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve())
