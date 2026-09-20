"""SPD Decap PI Evaluator v0.23.1: full-joint five-lift static Green discriminator.

All inter-cell interactions remain in the point sum. Only each owned cell's
point self block is replaced with its frozen qualified integrated self block.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.spatial import cKDTree
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry

ROOT = geometry.ROOT
PINS = {
    'tools/research/apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified.py': '3ad0e14937943f4c7941828f1e1bae345b05b5c88b812ddbdb68e69c83ca60d4',
    'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz': '9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92',
    'outputs/research/astra-source-joint-all-self-green-02/result.json': 'bc5cef31455394d6e6d9d68cc890b0291d6ec09d1d79ddf681defb5e3207bbab',
    'outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz': 'fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd',
    'outputs/research/astra-source-joint-all-self-green-review-03/independent-review.json': 'e273d416b23c11635896a37fd1461fc4a54cb33831d3d9b40be4835f43dcc91e',
}


def point_self(points, weighted, cell_count, threshold):
    points = points.reshape(cell_count, -1, 3)
    weighted = weighted.reshape(cell_count, -1, 5, 3)
    value = np.zeros((5, 5))
    for first in range(0, cell_count, 128):
        p, w = points[first:first+128], weighted[first:first+128]
        distance = np.linalg.norm(p[:, :, None]-p[:, None, :], axis=3)
        inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > threshold)
        potential = np.einsum('cij,cjma->cima', inverse, w)
        value += np.einsum('cima,cina->mn', w, potential)
    return 1e-7*value


def run(output):
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    start, history, arrays, failure = monotonic(), [], {}, None
    try:
        geometry.verify_inputs()
        for path, pin in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
        joint = geometry.load_joint()
        with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as d:
            gram = d['energy_gram_ohm']
            transform = np.linalg.inv(np.linalg.cholesky(gram).T)
            currents = d['face_flux_basis'] @ transform
        with np.load(ROOT/'outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz') as d:
            self_h = d['static_vector_self_h']
        local = joint['local_face_signs'][:, :, None]*currents[joint['local_face_columns']]
        exact = np.einsum('cim,cij,cjn->mn', local, self_h, local)
        assert np.linalg.eigvalsh(exact).min() > 0
        arrays.update(whitened_face_currents=currents, R_whitening=transform, exact_cell_self_matrix_h_per_ohm=exact)
        previous = None
        for order in (2, 3, 4):
            assert monotonic() < start+240, 'bounded static-matrix probe'
            source = geometry.build_rt0_volume_sources(currents, order, joint)
            points, weighted = source['source_points_m'], source['source_weighted_current_a_m']
            threshold = np.ptp(points, axis=0).max()*np.finfo(float).eps
            minimum = float(cKDTree(points).query(points, k=2)[0][:, 1].min())
            assert minimum > threshold, 'distinct source points must not be FMM-omitted'
            before = monotonic()
            print(json.dumps(dict(stage='fmm_start', order=order, points=len(points))), flush=True)
            fmm = geometry.fmm3dpy.lfmm3d(eps=1e-12, sources=np.asfortranarray(points.T),
                charges=np.asfortranarray(weighted.reshape(len(points), 15).T), pg=1, nd=15)
            assert fmm.ier == 0 and np.isfinite(fmm.pot).all()
            potential = 4*np.pi*np.asarray(fmm.pot).T.reshape(-1, 5, 3)
            fmm_s = monotonic()-before
            point_matrix = 1e-7*np.einsum('pma,pna->mn', weighted, potential)
            own_point = point_self(points, weighted, len(joint['cells']), threshold)
            corrected = point_matrix-own_point+exact
            # Actual all-source direct check, with the same per-target self omission.
            ids = np.unique(np.linspace(0, len(points)-1, 16, dtype=int))
            distance = np.linalg.norm(points[ids, None]-points[None], axis=2)
            inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > threshold)
            direct = np.einsum('tp,pma->tma', inverse, weighted)
            direct_error = np.linalg.norm((potential[ids]-direct).reshape(len(ids), 5, 3), axis=(0, 2))/np.linalg.norm(direct, axis=(0, 2))
            assert direct_error.max() < 1e-10
            symmetry = np.linalg.norm(corrected-corrected.T)/np.linalg.norm(corrected)
            assert symmetry < 1e-10
            change = None if previous is None else float(np.linalg.norm(corrected-previous, 2)/np.linalg.norm(corrected, 2))
            record = dict(order=order, points=len(points), fmm_s=fmm_s, elapsed_s=monotonic()-start,
                direct_fmm_max_relative=float(direct_error.max()), symmetry=float(symmetry),
                normalized_min_eigenvalue=float(np.linalg.eigvalsh(corrected).min()/np.linalg.norm(corrected, 2)),
                successive_spectral_relative=change, minimum_point_separation_m=minimum)
            history.append(record)
            arrays.update({f'point_matrix_q{order}': point_matrix, f'subtracted_cell_point_q{order}': own_point,
                f'corrected_matrix_q{order}': corrected, f'direct_check_ids_q{order}': ids,
                f'direct_reference_q{order}': direct, f'fmm_check_q{order}': potential[ids]})
            np.savez_compressed(output/'static-matrices.npz', **arrays)
            print(json.dumps(record), flush=True)
            previous = corrected
        accepted = history[-1]['successive_spectral_relative'] < 5e-5
        result = dict(status='PASS_SELF_REPLACED_LIFT_STATIC_REFINEMENT_SAMPLE' if accepted else 'STOP_INTERCELL_STATIC_QUADRATURE', history=history)
    except Exception:
        accepted = False
        failure = traceback.format_exc()
        result = dict(status='STOP_LIFT_STATIC_MATRIX_EXCEPTION', history=history)
    artifact = output/'static-matrices.npz'
    np.savez_compressed(artifact, **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-start,
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), failure=failure,
        threads={name: os.environ.get(name) for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS')},
        scope='Full5304-cell static vector Green matrix on5real conforming lifts with R-orthonormal coordinates. '
        'Every inter-cell pair remains; only the same-cell point block is subtracted and qualified integrated '
        'self restored once. This measures remaining inter-cell quadrature error, not a complete near correction, '
        'retarded Green action, physical current-space convergence, contact solve, Z or board response.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('status', 'elapsed_s', 'failure')}), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
