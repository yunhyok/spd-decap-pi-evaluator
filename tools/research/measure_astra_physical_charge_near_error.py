"""Measure the unresolved nonself cubature error on the actual eight-support block."""
import json
import sys
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import coo_matrix

from apply_astra_owned_3d_green import TET_BARY, TRI_BARY
from astra_layered_charge_action import halfspace_action, _direct_points
from astra_stratified_charge_green import EPS0, source_background
from assemble_astra_halfspace_charge_self import classify
from qualify_astra_source_joint_charge_green import scalar_pair
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]; R = ROOT/'outputs/research'


def physical_pair(observer, source, order, interface, eps):
    direct = scalar_pair(observer, source, order)
    side = classify(observer, interface)
    if side == classify(source, interface) and side != 0:
        host, other = (eps[0], eps[1]) if side < 0 else (eps[1], eps[0])
        mirror = source.copy(); mirror[:, 2] = 2*interface-mirror[:, 2]
        if len(mirror) == 4 and np.linalg.det(mirror[1:]-mirror[:1]) < 0:
            mirror[[1, 2]] = mirror[[2, 1]]
        return (direct+(host-other)/(host+other)*scalar_pair(observer, mirror, order))/(4*np.pi*EPS0*host)
    return 2*direct/(4*np.pi*EPS0*(eps[0]+eps[1]))


def assemble_near_delta(supports, pairs, gate=5e-5, orders=(8, 16, 32, 64), seconds=120):
    """Correct explicitly owned unordered pairs; this does not select a full near set.

    Both directed integrals must refine and agree. Deep/self terms stay intact.
    ponytail: per-pair Python integration; template batching is needed for millions of pairs.
    """
    start = monotonic(); pairs = np.asarray(pairs)
    assert pairs.ndim == 2 and pairs.shape[1] == 2 and np.issubdtype(pairs.dtype, np.integer)
    assert np.all((pairs >= 0) & (pairs < len(supports))) and np.all(pairs[:, 0] < pairs[:, 1])
    assert len(np.unique(pairs, axis=0)) == len(pairs)
    assert 0 < gate < 1 and len(orders) >= 2 and all(a < b for a, b in zip(orders, orders[1:]))
    interfaces, eps, _ = source_background(); interface = interfaces[0]
    points = [np.einsum('qi,tid->tqd', TET_BARY if len(v) == 4 else TRI_BARY, v[None])[0]
              for v in supports]
    rows = []; cols = []; values = []; receipts = []
    for a, b in pairs:
        previous = None
        for order in orders:
            assert monotonic()-start < seconds, 'near correction execution boundary'
            exact = np.asarray([physical_pair(supports[a], supports[b], order, interface, eps),
                                physical_pair(supports[b], supports[a], order, interface, eps)])
            scale = max(abs(exact).max(), np.finfo(float).tiny)
            reciprocal = float(abs(exact[0]-exact[1])/scale)
            change = None if previous is None else float(abs(exact-previous).max()/scale)
            if change is not None and change < gate and reciprocal < gate:
                break
            previous = exact
        else:
            raise RuntimeError(f'Pair {(int(a), int(b))} did not converge: {change=}, {reciprocal=}')
        for observer, source, value in ((a, b, exact[0]), (b, a, exact[1])):
            base = halfspace_action(points[source], np.ones(len(points[source]))/len(points[source]),
                                    targets=points[observer], point_action=_direct_points).mean()
            rows.append(observer); cols.append(source); values.append(value-base)
        receipts.append(dict(pair=[int(a), int(b)], order=order, refinement_relative=change,
                             reciprocity_relative=reciprocal))
    return coo_matrix((values, (rows, cols)), shape=(len(supports), len(supports))).tocsr(), receipts


def run():
    start = monotonic(); source = R/'astra-physical-charge-action-integration-20260912/actual-support-block.npz'
    assert sha(source) == '1f68bde34cca830cb53f7c358034afef45e52b3957b6854550c61e77c4546b1b'
    with np.load(source, allow_pickle=False) as z:
        selected = z['complete_point_order_charge_rows']; baseline = z['physical_p_block_per_f']; delta = z['self_delta_per_f']
    with np.load(R/'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz', allow_pickle=False) as z:
        xyz = z['vertices_um']; pt = xyz[z['cells']]; pf = xyz[z['face_vertices'][z['free_surface_face_ids']]]
    with np.load(R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz', allow_pickle=False) as z:
        groups = [pt, z['volume_charge_vertices_um'], pf, z['surface_charge_vertices_um']]
    ends = np.cumsum([len(g) for g in groups]); starts = np.r_[0, ends[:-1]]
    supports = [groups[np.searchsorted(ends, row, side='right')][row-starts[np.searchsorted(ends, row, side='right')]]*1e-6 for row in selected]
    points = []; spread = []; gather = np.zeros((len(supports), 30)); cursor = 0
    for row, v in enumerate(supports):
        bary = TET_BARY if len(v) == 4 else TRI_BARY
        points.extend(np.einsum('qi,tid->tqd', bary, v[None])[0])
        weights = np.zeros((len(bary), len(supports))); weights[:, row] = 1/len(bary); spread.append(weights)
        gather[row, cursor:cursor+len(bary)] = 1/len(bary); cursor += len(bary)
    assert cursor == 30
    half_point = gather@halfspace_action(np.asarray(points), np.vstack(spread), point_action=_direct_points)
    deep = baseline-half_point-np.diag(delta)
    interfaces, eps, _ = source_background(); interface = interfaces[0]
    sides = [classify(v, interface) for v in supports]
    history = []; previous = None
    for order in (8, 16, 32):
        matrix = baseline.copy()
        for a, observer in enumerate(supports):
            for b, source_vertices in enumerate(supports):
                if a == b:
                    continue
                assert monotonic()-start < 120, '120s near-block execution boundary'
                direct = scalar_pair(observer, source_vertices, order)
                if sides[a] == sides[b] and sides[a] != 0:
                    host, other = (eps[0], eps[1]) if sides[a] < 0 else (eps[1], eps[0])
                    image = source_vertices.copy(); image[:, 2] = 2*interface-image[:, 2]
                    if len(image) == 4 and np.linalg.det(image[1:]-image[:1]) < 0:
                        image[[1, 2]] = image[[2, 1]]
                    physical = (direct+(host-other)/(host+other)*scalar_pair(observer, image, order))/(4*np.pi*EPS0*host)
                else:
                    physical = 2*direct/(4*np.pi*EPS0*(eps[0]+eps[1]))
                matrix[a, b] = physical+deep[a, b]
        reciprocity = float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix))
        change = None if previous is None else float(np.linalg.norm(matrix-previous)/np.linalg.norm(matrix))
        history.append(dict(order=order, block_refinement_relative=change, raw_reciprocity_relative=reciprocity))
        previous = matrix
        if change is not None and change < 5e-5 and reciprocity < 5e-5:
            break
    error = float(np.linalg.norm(baseline-matrix)/np.linalg.norm(matrix))
    out = R/'astra-physical-charge-near-error-20260912'; out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'near-block.npz', charge_rows=selected, point_cubature_p=baseline,
                        analytic_inner_near_p=matrix, replacement_delta_p=matrix-baseline)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='MEASURED_ACTUAL_PHYSICAL_P_NONSELF_QUADRATURE_ERROR',
                  history=history, point_cubature_block_error_relative=error,
                  driver_sha256=sha(Path(__file__)), source_block_sha256=sha(source),
                  artifact_sha256=sha(out/'near-block.npz'), elapsed_s=monotonic()-start,
                  scope='Same actual eight support functions and same deep-layer point correction; only56 off-diagonal halfspace support integrals are refined through analytic inner kernels. Both directions are retained without symmetrizing. This measures a concrete nonself cubature gap; it does not certify the rest of the full P operator or port error.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


def check_correction():
    from astra_layered_charge_action import DeepRemainderAction
    from apply_astra_physical_charge_green import apply
    started = monotonic()
    source = R/'astra-physical-charge-near-error-20260912/near-block.npz'
    assert sha(source) == 'a7414ef17991fe998200ab581fe1015c66e4177042caee0f2c6d6f4262e14af8'
    with np.load(source, allow_pickle=False) as z:
        selected = z['charge_rows']; reference = z['analytic_inner_near_p']; baseline = z['point_cubature_p']
    with np.load(R/'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz') as z:
        xyz = z['vertices_um']; pt = xyz[z['cells']]; pf = xyz[z['face_vertices'][z['free_surface_face_ids']]]
    with np.load(R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz') as z:
        groups = [pt, z['volume_charge_vertices_um'], pf, z['surface_charge_vertices_um']]
    ends = np.cumsum([len(g) for g in groups]); starts = np.r_[0, ends[:-1]]
    supports = [groups[np.searchsorted(ends, row, side='right')][row-starts[np.searchsorted(ends, row, side='right')]]*1e-6 for row in selected]
    pairs = np.column_stack(np.triu_indices(len(supports), 1))
    correction, history = assemble_near_delta(supports, pairs)
    points = []; columns = []; weights = []
    for i, vertices in enumerate(supports):
        bary = TET_BARY if len(vertices) == 4 else TRI_BARY
        points.extend(np.einsum('qi,tid->tqd', bary, vertices[None])[0])
        columns.extend([i]*len(bary)); weights.extend([1/len(bary)]*len(bary))
    with np.load(R/'astra-physical-charge-action-integration-20260912/actual-support-block.npz') as z:
        self_delta = z['self_delta_per_f']
    points = np.asarray(points); columns = np.asarray(columns); weights = np.asarray(weights)
    deep = DeepRemainderAction(points, spacing_m=62.5e-6, radial_count=768)
    rng = np.random.default_rng(20260912)
    charges = rng.normal(size=(len(supports), 2))+1j*rng.normal(size=(len(supports), 2))
    actual = apply(points, columns, weights, self_delta, charges, deep,
                   point_action=_direct_points, near_delta=correction)
    expected = (baseline+correction)@charges
    integration_error = float(np.linalg.norm(actual-expected)/np.linalg.norm(expected))
    reference_error = float(np.linalg.norm(baseline+correction-reference)/np.linalg.norm(reference))
    assert integration_error < 1e-10 and reference_error < 5e-5
    assert correction.nnz == 56 and np.all(correction.diagonal() == 0)
    out = R/'astra-physical-charge-owned-near-correction-20260912'; out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'near-correction.npz', charge_rows=selected, data=correction.data,
                        indices=correction.indices, indptr=correction.indptr, shape=correction.shape,
                        charge_witnesses=charges, corrected_action=actual)
    report = dict(status='PASS_EXPLICIT_PAIR_CORRECTION_INTEGRATION', pair_count=len(pairs),
                  integration_relative=integration_error, reference_block_relative=reference_error,
                  pair_history=history, elapsed_s=monotonic()-started,
                  driver_sha256=sha(Path(__file__)), apply_sha256=sha(ROOT/'tools/research/apply_astra_physical_charge_green.py'),
                  artifact_sha256=sha(out/'near-correction.npz'),
                  scope='Only 28 explicit unordered pairs on eight actual supports. Every directed pair meets the unchanged refinement and reciprocity gate; no full-domain near selection, global P convergence or port Z claim.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    check_correction() if '--correction-check' in sys.argv else run()
