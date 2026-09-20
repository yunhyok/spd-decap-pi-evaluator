"""Actual-edge constant-current check of the full saved L02 RT0 space."""
import argparse
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import csr_matrix

import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/prepare_astra_l02_rt0_current_space.py':
        'a5b8ecc5998a7ec9f5685a89eefaaa06cdc9c7382eb40de042ebac5a9b487d5e',
    'outputs/research/astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz':
        '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f',
    'outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz':
        '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9',
}


def run(output):
    start = monotonic()
    assert not output.exists()
    budget = recon._Budget.create(60, 3)
    for path, digest in PINS.items():
        assert recon._sha256_file(ROOT / path) == digest, path
    with np.load(ROOT / next(p for p in PINS if 'current-space.npz' in p)) as z:
        a = {key: z[key] for key in z.files}
    with np.load(ROOT / next(p for p in PINS if 'mesh-stiffness.npz' in p)) as z:
        xy, triangles = z['node_xy_um'], z['triangles']
    free = a['free_triangle_indices']
    local_ids, signs = a['local_facet_branch_index'], a['local_outward_flux_sign']
    edges = a['branch_mesh_edges']
    q_sum = np.zeros((len(edges), 2))
    counts = np.zeros(len(edges), dtype=np.int64)
    areas = []
    for begin in range(0, len(free), 50000):
        end = min(begin + 50000, len(free))
        t = triangles[free[begin:end]]
        ids, sg = local_ids[begin:end], signs[begin:end]
        opposite = np.stack([t[:, [1, 2]], t[:, [2, 0]], t[:, [0, 1]]], axis=1)
        assert np.array_equal(edges[ids], np.sort(opposite, axis=2))
        p = xy[t]
        v = (p - p[:, :1]) * 1e-6
        det = v[:, 1, 0] * v[:, 2, 1] - v[:, 1, 1] * v[:, 2, 0]
        assert np.all(abs(det) > 0)
        tangent = (p[:, [2, 0, 1]] - p[:, [1, 2, 0]]) * 1e-6
        outward = np.stack([tangent[:, :, 1], -tangent[:, :, 0]], axis=2) * np.sign(det)[:, None, None]
        np.add.at(q_sum, ids.ravel(), (sg[:, :, None] * outward).reshape(-1, 2))
        np.add.at(counts, ids.ravel(), 1)
        areas.append(abs(det) / 2)
        budget.check('geometric edge scan')
    assert np.all((counts == 1) | (counts == 2))
    q = q_sum / counts[:, None]
    area = np.concatenate(areas)
    geometry_error, center_error, radial_max = 0., 0., 0.
    for begin in range(0, len(free), 50000):
        end = min(begin + 50000, len(free))
        p = xy[triangles[free[begin:end]]]
        v = (p - p[:, :1]) * 1e-6
        det = v[:, 1, 0] * v[:, 2, 1] - v[:, 1, 1] * v[:, 2, 0]
        tangent = (p[:, [2, 0, 1]] - p[:, [1, 2, 0]]) * 1e-6
        outward = np.stack([tangent[:, :, 1], -tangent[:, :, 0]], axis=2) * np.sign(det)[:, None, None]
        local_q = signs[begin:end, :, None] * q[local_ids[begin:end]]
        err = np.linalg.norm(local_q - outward, axis=2) / np.linalg.norm(outward, axis=2)
        geometry_error = max(geometry_error, float(err.max()))
        assert geometry_error < 1e-14
        center = v.mean(axis=1)
        jc = np.einsum('cik,cid->ckd', local_q, center[:, None] - v) / (2 * area[begin:end, None, None])
        center_error = max(center_error, float(abs(jc - np.eye(2)).max()))
        radial = local_q.sum(axis=1) / (2 * area[begin:end, None])
        radial_max = max(radial_max, float(abs(radial).max()))
        budget.check('constant affine-current replay')
    assert center_error < 1e-10
    def sparse(prefix):
        return csr_matrix((a[prefix + '_data'], a[prefix + '_indices'], a[prefix + '_indptr']),
                          shape=tuple(a[prefix + '_shape']))
    r, d, b = sparse('r'), sparse('d'), sparse('distributional_b')
    divergence = d @ q
    patch = (b @ q)[len(free):len(free) + 38856]
    assert np.max(abs(divergence)) < 1e-16 and np.max(abs(patch)) < 1e-16
    gram = q.T @ (r @ q)
    exact = np.eye(2) * area.sum() / (59.59e6 * 20e-6)
    energy_error = float(np.linalg.norm(gram - exact) / np.linalg.norm(exact))
    assert energy_error < 1e-10
    assert b.shape[0] == len(free) + 38856 + len(a['retained_exterior_branch_indices'])
    budget.check('constant Joule energy')
    output.mkdir(parents=True)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(output / 'constant-current-check.npz', computed_gram=gram, exact_gram=exact)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_ACTUAL_L02_GEOMETRIC_FACE_AND_CONSTANT_CURRENT_CHECK', input_sha256=PINS,
        driver_sha256=recon._sha256_file(Path(__file__)), elapsed_s=monotonic() - start,
        budget=budget.receipt(), metrics=dict(actual_face_normal_relative=geometry_error,
            cell_center_constant_current_max_error=center_error,
            radial_coefficient_max_per_m=radial_max, cell_divergence_max_a=float(abs(divergence).max()),
            contact_flux_sum_max_a=float(abs(patch).max()), constant_joule_gram_relative=energy_error),
        scope='Every actual free-cell edge, signed normal, affine constant Jx/Jy, contact rim sum '
              'and global constant-field Joule energy were checked. This complements the producer\'s '
              'full local q2/random-current checks. No current-space convergence, charge or magnetic '
              'kernel, electrode-interior field, circuit solve or physical board claim.')
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
