"""SPD Decap PI Evaluator v0.23.1: extend qualified static pairs by verified rigid reuse."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
from qualify_astra_ordered_pair_geometry_reuse import group_pairs

ROOT = geometry.ROOT
PINS = {
    'tools/research/qualify_astra_ordered_pair_geometry_reuse.py': '90770d3f459375466c7cd83ff284a4d8037911c6e6da2b7163b42325dd940f92',
    'outputs/research/astra-ordered-pair-geometry-reuse-01/result.json': 'ac61f1599ea6098da56d1d2e36c64eadfff6a8ccc16eb82f6ba09dcc87893fd4',
    'outputs/research/astra-ordered-pair-geometry-reuse-01/pair-geometry-registry.npz': 'f5bf5437ac77b1532977ad10b462bf28f23b6be1dbd5dbce617e3528bd671335',
    'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz': '0288c88834314667fb48c993a148bd57974ea1ed9db6bcc069ace252854d8ebc',
    'outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz': '0fb2f99be551708ca53fcb546ad56468ce6736f97f202358f8ddaa349342c939',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-leading-lift-touching-correction-02/result.json': '8f9b6611f3e7c5b79caa592401a138ace72dbca26ee32248879f49f77a2d0fe7',
    'outputs/research/astra-leading-lift-touching-correction-02/pair-correction.npz': '7c89bcafe7ab81915289ec9eb548c57c6e18429ea15ac6c3317f4f0d2666d27d',
    'outputs/research/astra-leading-lift-touching-correction-02/pair-records.json': '108fd7cbf8e749d8abbeb2fd7060a06423667d5f98d0c35c83e5364cec1332eb',
    'outputs/research/astra-leading-lift-nontouch-correction-01/result.json': '921693f63bf1b88837b5ccebcee26571daa54bd1fefc9d2ac7fa896bfd10fb3b',
    'outputs/research/astra-leading-lift-nontouch-correction-01/pair-correction.npz': '98fa35e653acc04d618775c953168604c00836427a3e6f2689d22018107208c9',
    'outputs/research/astra-leading-lift-nontouch-correction-01/pair-records.json': 'c8b4984f5ba0076c56efbaf7841bc3d254fb873c0f1c21f293243b3caa9e29ed',
}


def main():
    out = ROOT/'outputs/research/astra-qualified-pair-geometry-reuse-01'
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, arrays, cases = monotonic(), {}, []
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        joint = geometry.load_joint()
        with np.load(ROOT/'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz') as d:
            current = d['whitened_face_currents']
            baseline = {q: d[f'corrected_matrix_q{q}'] for q in (2, 3)}
        local = joint['local_face_signs'][:, :, None]*current[joint['local_face_columns']]
        rules = {}
        for q in (2, 3):
            d = geometry.build_rt0_volume_sources(current, q, joint)
            rules[q] = (d['source_points_m'].reshape(len(local), -1, 3),
                        d['source_weighted_current_a_m'].reshape(len(local), -1, 5, 3))
        corrected = {q: x.copy() for q, x in baseline.items()}
        for kind in ('touch', 'nontouch'):
            if kind == 'touch':
                with np.load(ROOT/'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz') as d:
                    a, b = d['pair_source_cell'], d['pair_observer_cell']
                    delta, score = d['pair_q3_minus_q2_matrix'], d['normalized_pair_frobenius']
                    energy_scale = d['baseline_energy_scaling']
                with np.load(ROOT/'outputs/research/astra-ordered-pair-geometry-reuse-01/pair-geometry-registry.npz') as d:
                    np.testing.assert_array_equal(d['pair_a'], a)
                    np.testing.assert_array_equal(d['pair_b'], b)
                    group, rep, fit = d['pair_group'], d['representative_pair_index'], d['relative_rigid_fit']
                prior = ROOT/'outputs/research/astra-leading-lift-touching-correction-02'
            else:
                with np.load(ROOT/'outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz') as d:
                    a, b = d['pair_cell_a'], d['pair_cell_b']
                    delta, score = d['pair_q3_minus_q2'], d['baseline_score']
                    np.testing.assert_allclose(energy_scale, d['baseline_energy_scaling'], rtol=1e-14, atol=0)
                rep, group, fit, _ = group_pairs(joint['tetrahedra_m']*1e6, a, b, started+180)
                assert not np.any(joint['cells'][a, :, None] == joint['cells'][b, None, :])
                prior = ROOT/'outputs/research/astra-leading-lift-nontouch-correction-01'
            with np.load(prior/'pair-correction.npz') as d:
                ra, rb, reference = d['pair_a'], d['pair_b'], d['forward_reverse_h']
            records = json.loads((prior/'pair-records.json').read_text())
            assert len(records) == len(reference)
            errors = np.array([max(r['relative_change'], r['relative_reciprocity']) for r in records])
            assert np.all(np.isfinite(errors)) and errors.max() < 5e-5
            keys, reference_keys = a*len(local)+b, ra*len(local)+rb
            order = np.argsort(keys)
            located = order[np.searchsorted(keys[order], reference_keys)]
            np.testing.assert_array_equal(keys[located], reference_keys)
            reference_for_group = np.full(len(rep), -1, dtype=np.int64)
            for index in np.argsort(-errors):
                reference_for_group[group[located[index]]] = index
            selected = np.flatnonzero(reference_for_group[group] >= 0)
            ref_index = reference_for_group[group[selected]]
            x, y = a[selected], b[selected]
            blocks = reference[ref_index, 0]
            directed = local[x].transpose(0, 2, 1) @ blocks @ local[y]
            exact = directed+directed.transpose(0, 2, 1)
            point = {q: np.empty_like(exact) for q in (2, 3)}
            for first in range(0, len(selected), 256):
                assert monotonic() < started+180
                sl = slice(first, first+256)
                for q in (2, 3):
                    p, w = rules[q]
                    distance = np.linalg.norm(p[x[sl], :, None]-p[y[sl], None, :], axis=3)
                    assert distance.min() > 0
                    potential = np.einsum('pij,pjnd->pind', 1/distance, w[y[sl]], optimize=True)
                    value = 1e-7*np.einsum('pimd,pind->pmn', w[x[sl]], potential, optimize=True)
                    point[q][sl] = value+value.transpose(0, 2, 1)
            replay = np.linalg.norm((point[3]-point[2]).sum(axis=0)-delta[selected].sum(axis=0))/np.linalg.norm(baseline[3])
            assert replay < 1e-12
            for q in (2, 3):
                corrected[q] += exact.sum(axis=0)-point[q].sum(axis=0)
            for name, value in dict(pair_a=x, pair_b=y, source_pair_indices=selected,
                    reference_pair_a=ra, reference_pair_b=rb, reference_forward_reverse_h=reference,
                    reference_index=ref_index, point_q2=point[2], point_q3=point[3],
                    exact_pair=exact, all_pair_group=group, representative_pair_index=rep).items():
                arrays[f'{kind}_{name}'] = value
            cases.append(dict(kind=kind, all_pairs=len(a), geometry_groups=len(rep),
                              qualified_groups=int(np.sum(reference_for_group >= 0)), original_references=len(ra),
                              covered_pairs=len(selected), maximum_rigid_fit=float(fit.max()),
                              replay_relative=float(replay), empirical_remaining_l1=float(score.sum()-score[selected].sum())))
            print(json.dumps(cases[-1]), flush=True)
        scale = np.linalg.inv(np.linalg.cholesky((corrected[3]+corrected[3].T)/2))
        difference = corrected[3]-corrected[2]
        remaining = float(np.linalg.norm(scale @ difference @ scale.T, 2))
        arrays.update(corrected_matrix_q2=corrected[2], corrected_matrix_q3=corrected[3], original_energy_scale=energy_scale)
        result = dict(status='REUSED_QUALIFIED_PAIRS_REMAINING_QUADRATURE', cases=cases,
                      remaining_updated_energy_relative=remaining,
                      remaining_original_energy_relative=float(np.linalg.norm(energy_scale @ difference @ energy_scale.T, 2)),
                      global_quadrature_gate_passed=remaining < 5e-5, failure=None)
    except Exception:
        result = dict(status='STOP_QUALIFIED_PAIR_REUSE', cases=cases, failure=traceback.format_exc())
    np.savez_compressed(out/'pair-reuse.npz', **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
                  pins=PINS, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  artifact_sha256=sha256((out/'pair-reuse.npz').read_bytes()).hexdigest(),
                  scope='No new Green integrals. Existing forward/reverse-qualified ordered local RT0 '
                  'blocks extend only to verified rigid geometries. Original self-replaced point matrices '
                  'receive every covered touching/non-touch pair once, with identical point blocks removed. '
                  'Remaining interactions and self blocks stay present. q2/q3 differences and uncovered L1 '
                  'are empirical estimators, not absolute continuous-integral, field or board certificates.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['failure'] is None else 2


if __name__ == '__main__':
    raise SystemExit(main())
