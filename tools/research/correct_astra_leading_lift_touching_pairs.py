"""SPD Decap PI Evaluator v0.23.1: correct leading actual touching-pair integrals."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_tetra_charge_green import measure
from qualify_astra_conforming_power_joint_sparse_current import local_mass

ROOT = geometry.ROOT
PINS = {
    'tools/research/qualify_astra_tetra_volume_green.py': '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    'tools/research/qualify_astra_conforming_power_joint_sparse_current.py': 'dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e',
    'outputs/research/astra-lift-touching-pair-errors-01/result.json': 'c1845a594881b7b8f870db4610a02ffac0623442ff3109edd427db55073c3c65',
    'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz': '0288c88834314667fb48c993a148bd57974ea1ed9db6bcc069ace252854d8ebc',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-outer-static-touching-replacement-01/touching-pair-references.npz': '8320e572848c0a4561841d71c7f31c2c4f41e2986ce1d0512e746989b37f109c',
    'outputs/research/astra-outer-static-touching-replacement-01/pair-records.json': '991c289f03caa3b147106a4688b8dba197bb778c58e6ad7a85c5c14cd48349d0',
    'outputs/research/astra-leading-lift-touching-correction-01/result.json': '28290e0d2a484f7ec15959b2def767970688dd057af1903cbd9d4c83c059203d',
    'outputs/research/astra-leading-lift-touching-correction-01/pair-correction.npz': 'a240d642319aa354a0eee9e70b5be13a0b603b46e3b1c4ccb9429a958f2293b9',
    'outputs/research/astra-leading-lift-touching-correction-01/pair-records.json': 'ffe26acbb1fdd6712a073110768aa8138d28b3b613c7fdce3406428deb0e5145',
}


def point_pair(a, b, rule):
    points, weights = rule
    distance = np.linalg.norm(points[a, :, None]-points[b, None, :], axis=2)
    assert distance.min() > 0
    action = np.einsum('ij,jnd->ind', 1/distance, weights[b])
    value = 1e-7*np.einsum('imd,ind->mn', weights[a], action)
    return value+value.T


def run(output, nontouch_count=0):
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, records, arrays, failure = monotonic(), [], {}, None
    pins = dict(PINS)
    if nontouch_count:
        pins.update({
            'outputs/research/astra-lift-nontouch-knn-attribution-review-03/independent-review.json': '1a89ccc33759d657db51969ddea8c4ef3990c018a047bacdcc22b3dfebceccbc',
            'outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz': '0fb2f99be551708ca53fcb546ad56468ce6736f97f202358f8ddaa349342c939',
            'outputs/research/astra-leading-lift-touching-correction-02/result.json': '8f9b6611f3e7c5b79caa592401a138ace72dbca26ee32248879f49f77a2d0fe7',
            'outputs/research/astra-leading-lift-touching-correction-02/pair-correction.npz': '7c89bcafe7ab81915289ec9eb548c57c6e18429ea15ac6c3317f4f0d2666d27d',
        })
    try:
        geometry.verify_inputs()
        for path, pin in pins.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
        joint = geometry.load_joint()
        with np.load(ROOT/'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz') as d:
            currents = d['whitened_face_currents']
            baseline = {q: d[f'corrected_matrix_q{q}'] for q in (2, 3)}
        with np.load(ROOT/'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz') as d:
            chosen = d['descending_pair_indices'][:1292]
            rows, columns = d['pair_source_cell'][chosen], d['pair_observer_cell'][chosen]
            expected_difference = d['pair_q3_minus_q2_matrix'][chosen].sum(axis=0)
            energy_scale = d['baseline_energy_scaling']
        if nontouch_count:
            with np.load(ROOT/'outputs/research/astra-leading-lift-touching-correction-02/pair-correction.npz') as d:
                baseline = {q: d[f'corrected_matrix_q{q}'] for q in (2, 3)}
            with np.load(ROOT/'outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz') as d:
                assert 0 < nontouch_count <= len(d['descending_pair_indices'])
                chosen = d['descending_pair_indices'][:nontouch_count]
                rows, columns = d['pair_cell_a'][chosen], d['pair_cell_b'][chosen]
                expected_difference = d['pair_q3_minus_q2'][chosen].sum(axis=0)
                np.testing.assert_allclose(d['baseline_energy_scaling'], energy_scale, rtol=1e-14, atol=0)
            assert np.all(rows < columns)
            assert not np.any(joint['cells'][rows, :, None] == joint['cells'][columns, None, :])
        local = joint['local_face_signs'][:, :, None]*currents[joint['local_face_columns']]
        tets = joint['tetrahedra_m']
        whitening = {int(i): np.linalg.inv(np.linalg.cholesky(local_mass(tets[i], measure(tets[i]))).T)
            for i in np.unique(np.r_[rows, columns])}
        cache = {}
        with np.load(ROOT/'outputs/research/astra-outer-static-touching-replacement-01/touching-pair-references.npz') as d:
            old_blocks = d['vector_forward_reverse_h']
        old_records = json.loads((ROOT/'outputs/research/astra-outer-static-touching-replacement-01/pair-records.json').read_text())
        for record, values in zip([r for r in old_records if r['kind'] == 'vector'], old_blocks, strict=True):
            assert record['accepted']
            cache[record['observer'], record['source']] = record, values
        prior = ROOT/'outputs/research/astra-leading-lift-touching-correction-01'
        assert json.loads((prior/'result.json').read_text())['failure'] is None
        with np.load(prior/'pair-correction.npz') as d:
            prior_blocks = d['forward_reverse_h']
        for record, values in zip(json.loads((prior/'pair-records.json').read_text()), prior_blocks, strict=True):
            assert max(record['relative_change'], record['relative_reciprocity']) < 5e-5
            cache[record['a'], record['b']] = dict(order=record['order'],
                change=record['relative_change'], reciprocity=record['relative_reciprocity']), values
        rules = {}
        for order in (2, 3):
            rule = geometry.build_rt0_volume_sources(currents, order, joint)
            rules[order] = (rule['source_points_m'].reshape(len(tets), -1, 3),
                rule['source_weighted_current_a_m'].reshape(len(tets), -1, 5, 3))
        exact, point2, point3, blocks, projected_changes = [], [], [], [], []
        for index, (a, b) in enumerate(zip(rows, columns, strict=True)):
            assert monotonic() < started+240, 'bounded pair integration'
            reused = (int(a), int(b)) in cache or (int(b), int(a)) in cache
            last_change = np.zeros((5, 5))
            if reused:
                reverse_key = (int(a), int(b)) not in cache
                record, values = cache[(int(b), int(a)) if reverse_key else (int(a), int(b))]
                block, reverse = values[::-1] if reverse_key else values
                order, change, reciprocity = record['order'], record['change'], record['reciprocity']
            else:
                previous, old_projected = None, None
                for order in (8, 16, 32, 64):
                    assert monotonic() < started+240, 'pair refinement deadline'
                    block, reverse = tetra_pair(tets[a], tets[b], order), tetra_pair(tets[b], tets[a], order)
                    scaled = np.array([whitening[int(a)].T @ block @ whitening[int(b)],
                        whitening[int(a)].T @ reverse.T @ whitening[int(b)]])
                    change = np.inf if previous is None else float(np.linalg.norm(scaled-previous)/np.linalg.norm(scaled))
                    reciprocity = float(np.linalg.norm(scaled[0]-scaled[1])/np.linalg.norm(scaled[0]))
                    projected = local[a].T @ block @ local[b]
                    projected = projected+projected.T
                    if old_projected is not None:
                        last_change = projected-old_projected
                    previous, old_projected = scaled, projected
                    if max(change, reciprocity) < 5e-5:
                        break
            assert max(change, reciprocity) < 5e-5, f'unqualified pair {a},{b}'
            directed = local[a].T @ block @ local[b]
            exact.append(directed+directed.T)
            blocks.append([block, reverse])
            point2.append(point_pair(a, b, rules[2]))
            point3.append(point_pair(a, b, rules[3]))
            projected_changes.append(last_change)
            records.append(dict(a=int(a), b=int(b), order=int(order), relative_change=change,
                relative_reciprocity=reciprocity, reused=reused))
            if (index+1) % 32 == 0:
                print(json.dumps(dict(stage='qualified_pairs', count=index+1, elapsed_s=monotonic()-started)), flush=True)
        exact, point2, point3 = np.array(exact), np.array(point2), np.array(point3)
        replay = np.linalg.norm((point3-point2).sum(axis=0)-expected_difference)/np.linalg.norm(baseline[3])
        assert replay < 1e-12
        corrected = {2: baseline[2]-point2.sum(axis=0)+exact.sum(axis=0),
            3: baseline[3]-point3.sum(axis=0)+exact.sum(axis=0)}
        difference = corrected[3]-corrected[2]
        updated_scale = np.linalg.inv(np.linalg.cholesky((corrected[3]+corrected[3].T)/2))
        arrays.update(pair_a=rows, pair_b=columns, forward_reverse_h=np.array(blocks),
            exact_canonical_reciprocal_pair_matrices=exact, point_pair_q2=point2, point_pair_q3=point3,
            corrected_matrix_q2=corrected[2], corrected_matrix_q3=corrected[3],
            last_new_reference_pair_change=np.array(projected_changes), original_q3_energy_scale=energy_scale)
        remaining = float(np.linalg.norm(updated_scale @ difference @ updated_scale.T, 2))
        result = dict(status='QUALIFIED_LEADING_PAIR_CORRECTION_REMAINING_QUADRATURE', pairs=len(rows),
            reused_pairs=sum(r['reused'] for r in records), maximum_local_change=max(r['relative_change'] for r in records),
            maximum_local_reciprocity=max(r['relative_reciprocity'] for r in records), replay_relative=float(replay),
            remaining_original_energy_relative=float(np.linalg.norm(energy_scale @ difference @ energy_scale.T, 2)),
            remaining_updated_energy_relative=remaining,
            remaining_R_coordinate_relative=float(np.linalg.norm(difference, 2)/np.linalg.norm(corrected[3], 2)),
            global_quadrature_gate_passed=remaining < 5e-5,
            last_new_reference_change_l1_original_energy=float(sum(np.linalg.norm(energy_scale @ x @ energy_scale.T, 2) for x in projected_changes)))
    except Exception:
        failure = traceback.format_exc()
        result = dict(status='STOP_LEADING_PAIR_CORRECTION_EXCEPTION')
    artifact = output/'pair-correction.npz'
    np.savez_compressed(artifact, **arrays)
    (output/'pair-records.json').write_text(json.dumps(records, indent=2, allow_nan=False), encoding='utf-8')
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
        pins=pins, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),
        records_sha256=sha256((output/'pair-records.json').read_bytes()).hexdigest(), failure=failure,
        scope=(f'{nontouch_count} leading non-touching pairs, retaining all1292 prior touching corrections. '
        if nontouch_count else '1292 leading actual touching pairs. ') +
        'Selected by empirical q2/q3 matrix differences. '
        'Full affine RT0 forward and reverse integrals qualify independently in local-mass coordinates. '
        'The canonical forward estimate and its transpose preserve exact Green reciprocity after those checks. '
        'Identical point-pair blocks are subtracted and the qualified block added once; all other pairs remain. '
        'Last-step changes of reused references are not regenerated. No full-near bound, field, Z or board claim.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if failure is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--nontouch-count', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(run(args.output.resolve(), args.nontouch_count))
