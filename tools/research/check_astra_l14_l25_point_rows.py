"""SPD Decap PI Evaluator v0.23.1: direct saved-current point-row references."""
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'outputs/research/astra-combined-magnetic-source-descriptor-02/combined-magnetic-source-descriptor.npz'
PIN = '7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02'


def direct_rows(points, charges, targets, own_indices):
    """Bare 1/(4 pi r) potential; omit only the identical source index."""
    result = np.zeros((len(targets), 2), complex)
    absolute = np.zeros((len(targets), 2))
    # ponytail: a few full-source rows only; FMM handles the full action.
    for row, (target, own) in enumerate(zip(targets, own_indices, strict=True)):
        for start in range(0, len(points), 131072):
            stop = min(start + 131072, len(points))
            distance = np.linalg.norm(points[start:stop] - target, axis=1)
            if start <= own < stop:
                distance[own-start] = np.inf
            assert np.all(distance > 0)
            kernel = 1 / (4 * np.pi * distance)
            result[row] += np.sum(kernel[:, None] * charges[start:stop], axis=0)
            absolute[row] += np.sum(kernel[:, None] * abs(charges[start:stop]), axis=0)
    return result, absolute


def main(output):
    started = perf_counter()
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == PIN
    # Independent normalization and self-exclusion check.
    tiny = np.array([[0., 0., 0.], [0., 0., 2.]])
    q = np.array([[1+2j, 3j], [4-1j, 2.]])
    got, _ = direct_rows(tiny, q, tiny, np.arange(2))
    assert np.allclose(got, q[::-1]/(8*np.pi), rtol=1e-14, atol=0)
    layers = []
    with np.load(SOURCE, allow_pickle=False) as source:
        for name in ('l14', 'l25'):
            tri = source[name+'_triangle_vertices_um'] * 1e-6
            edge = tri[:, 1:] - tri[:, :1]
            area = abs(edge[:, 0, 0]*edge[:, 1, 1]-edge[:, 0, 1]*edge[:, 1, 0])/2
            center = tri.mean(axis=1)
            points = np.column_stack((center, np.full(len(center), source[name+'_slab_z_um'].mean()*1e-6)))
            key = 'l14_sheet_current_density_a_per_m' if name == 'l14' else 'l25_average_current_a_per_m'
            charge = area[:, None] * source[key]
            assert np.isfinite(charge).all() and np.all(area > 0)
            strength = np.linalg.norm(charge, axis=1)
            strongest = np.argsort(strength)[-4:]
            spread = np.linspace(0, len(points)-1, 4, dtype=int)
            rows = np.unique(np.concatenate((strongest, spread)))
            layers.append((points, charge, rows))
    arrays = {}
    for observer, (points, _, rows) in enumerate(layers):
        arrays[f'layer{observer}_rows'] = rows
        arrays[f'layer{observer}_points_m'] = points[rows]
        for source_index, (sources, charges, _) in enumerate(layers):
            own = rows if observer == source_index else np.full(len(rows), -1)
            value, absolute = direct_rows(sources, charges, points[rows], own)
            arrays[f'potential_observer{observer}_source{source_index}'] = value
            arrays[f'absolute_sum_observer{observer}_source{source_index}'] = absolute
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(output/'direct-point-rows.npz', **arrays)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_DIRECT_FULL_SOURCE_POINT_ROW_REFERENCES',
                  descriptor_sha256=PIN, layers=['L14', 'L25'],
                  rows_per_layer=[len(layer[2]) for layer in layers],
                  source_counts=[len(layer[0]) for layer in layers],
                  elapsed_s=perf_counter()-started,
                  scope='Bare 1/(4 pi r) centroid potentials from each labelled sheet. Same-index source omitted; no physical local self or near correction. Direct all-source rows qualify subsequent FMM only, not finite-support physics or board accuracy.')
    (output/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
