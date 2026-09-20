"""SPD Decap PI Evaluator v0.23.1: reconstruct compact-rule sources and every correction."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
from qualify_astra_tetra_jacobi_rule import rule

ROOT = geometry.ROOT
PINS = {
    'outputs/research/astra-compact-rule-static-matrix-01/driver-at-run.py': '88bdfa5b0790c41f1d7de514ce2b665cf134a2bd8950cb24ccb004708a750e16',
    'outputs/research/astra-compact-rule-static-matrix-01/result.json': 'd554d51691dc6a3bfdef47b18e65847e854a42a285e1bb62fe723f773c70739c',
    'outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz': '2b739de10382dbeb1cb327948d4d5f8b540cfc61944e92788ad8093f5af9de45',
    'outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz': '726a49b8acd83d496f9d3bd9a864aabdd5bef9655f206fe46e00454ed3d17113',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz': 'fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd',
    'outputs/research/astra-leading-lift-touching-correction-02/pair-correction.npz': '7c89bcafe7ab81915289ec9eb548c57c6e18429ea15ac6c3317f4f0d2666d27d',
    'outputs/research/astra-leading-lift-nontouch-correction-01/pair-correction.npz': '98fa35e653acc04d618775c953168604c00836427a3e6f2689d22018107208c9',
    'tools/research/qualify_astra_tetra_jacobi_rule.py': 'b9dd7ccc41a39a3e8d1214dc9fc9cdef14e09f2a6637d321d25bca7874c6a49e',
}


def read(path):
    with np.load(ROOT/path) as d:
        return {k: d[k] for k in d.files}


def relative(a, b):
    return float(np.linalg.norm(a-b)/max(np.linalg.norm(b), np.finfo(float).tiny))


def source_arrays(tets, volume, local, bary, weights):
    # Independent affine coefficients a+b*x, followed by physical V*w.
    a = -np.einsum('cim,cid->cmd', local, tets)/(3*volume[:, None, None])
    b = local.sum(axis=1)/(3*volume[:, None])
    p = bary[None] @ tets
    j = a[:, None]+p[:, :, None]*b[:, None, :, None]
    return p, j*volume[:, None, None, None]*weights[None, :, None, None]


def pair_points(p, w, pa, pb, deadline):
    matrices = np.empty((len(pa), 5, 5))
    for first in range(0, len(pa), 64):
        assert monotonic() < deadline
        a, b = pa[first:first+64], pb[first:first+64]
        distance = np.linalg.norm(p[a, :, None]-p[b, None, :], axis=-1)
        assert np.all(distance > 0)
        value = 1e-7*np.einsum('cimx,cij,cjnx->cmn', w[a], 1/distance, w[b], optimize=True)
        matrices[first:first+64] = value+value.transpose(0, 2, 1)
    return matrices


def main(degree7=False):
    out = ROOT/('outputs/research/astra-compact-rule-static-matrix-review-04' if degree7 else 'outputs/research/astra-compact-rule-static-matrix-review-03')
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, arrays, metrics = monotonic(), {}, {}
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        extra_pins = {
            'outputs/research/astra-compact-rule-static-matrix-02/driver-at-run.py': '89516f41be49d725a4d538abe68674543dd2124a5e861bb81c7720e3bd5765ef',
            'outputs/research/astra-compact-rule-static-matrix-02/result.json': '5d5780174d462c224abbdd33b7ea2b1cbb5b3ea36ef9bae238624456ec58a0fa',
            'outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz': '6cab19f0cf9e906e123f6aef8dfd39b5c82e4e496907ea85ead7f3f365d816dd',
            'outputs/research/astra-compact-rule-static-matrix-review-03/independent-review.json': '62f8c3ae4c9023bbe13e51d63c21410faf462addaf1332bf1b0913f714cfb3c1',
        } if degree7 else {}
        for path, expected in extra_pins.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        source_dir = 'outputs/research/astra-compact-rule-static-matrix-'+('02' if degree7 else '01')
        receipt = json.loads((ROOT/source_dir/'result.json').read_text())
        for path, expected in (receipt['pins'] | receipt.get('extra_pins', {})).items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        saved = read(source_dir+'/static-matrices.npz')
        reuse = read('outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz')
        original = read('outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz')
        j = geometry.load_joint()
        tets, volume = j['tetrahedra_m'], j['cell_volumes_m3']
        local = j['local_face_signs'][:, :, None]*original['whitened_face_currents'][j['local_face_columns']]
        pa, pb = saved['corrected_pair_a'], saved['corrected_pair_b']
        assert len(pa) == 17324 and np.all(pa < pb) and len(np.unique(pa*len(tets)+pb)) == len(pa)
        self_h = read('outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz')['static_vector_self_h']
        exact_self = np.einsum('cim,cij,cjn->mn', local, self_h, local)
        metrics['raw_self_projection_relative'] = relative(exact_self, saved['exact_self'])
        assert metrics['raw_self_projection_relative'] < 1e-11
        exact_pairs = np.zeros((5, 5))
        propagation = {}
        for label, ref_path in [('touch', 'astra-leading-lift-touching-correction-02'), ('nontouch', 'astra-leading-lift-nontouch-correction-01')]:
            a, b, ix = reuse[f'{label}_pair_a'], reuse[f'{label}_pair_b'], reuse[f'{label}_reference_index']
            ra, rb = reuse[f'{label}_reference_pair_a'][ix], reuse[f'{label}_reference_pair_b'][ix]
            raw = read(f'outputs/research/{ref_path}/pair-correction.npz')
            assert np.array_equal(raw['pair_a'], reuse[f'{label}_reference_pair_a'])
            assert np.array_equal(raw['pair_b'], reuse[f'{label}_reference_pair_b'])
            assert np.array_equal(raw['forward_reverse_h'], reuse[f'{label}_reference_forward_reverse_h'])
            shared = (j['cells'][a, :, None] == j['cells'][b, None, :]).any(axis=(1, 2))
            assert np.all(shared if label == 'touch' else ~shared)
            x = np.concatenate((tets[ra], tets[rb]), axis=1)
            y = np.concatenate((tets[a], tets[b]), axis=1)
            x, y = x-x.mean(axis=1)[:, None], y-y.mean(axis=1)[:, None]
            u, _, vh = np.linalg.svd(x.transpose(0, 2, 1) @ y)
            fits = np.linalg.norm(x @ (u @ vh)-y, axis=(1, 2))/np.linalg.norm(y, axis=(1, 2))
            assert fits.max() < 1e-12
            block = np.einsum('pim,pij,pjn->pmn', local[a], raw['forward_reverse_h'][ix, 0], local[b])
            block += block.transpose(0, 2, 1).copy()
            projection = relative(block, reuse[f'{label}_exact_pair'])
            assert projection < 1e-11
            exact_pairs += block.sum(axis=0)
            propagation[label] = dict(pairs=len(a), ordered_rigid_fit_max=float(fits.max()), raw_reference_projection_relative=projection)
        assert np.array_equal(pa, np.r_[reuse['touch_pair_a'], reuse['nontouch_pair_a']])
        assert np.array_equal(pb, np.r_[reuse['touch_pair_b'], reuse['nontouch_pair_b']])
        assert relative(exact_pairs, saved['exact_pairs']) < 1e-11
        # Independently replay both Legendre pair arrays used by geometric reuse.
        for q in ([] if degree7 else [2, 3]):
            bary, weights = rule(q, 'legendre')
            p, w = source_arrays(tets, volume, local, bary, weights)
            values = pair_points(p, w, pa, pb, started+120)
            expected = np.r_[reuse[f'touch_point_q{q}'], reuse[f'nontouch_point_q{q}']]
            e = relative(values, expected)
            assert e < 1e-11
            rebuilt = original[f'corrected_matrix_q{q}']-values.sum(axis=0)+exact_pairs
            e2 = relative(rebuilt, reuse[f'corrected_matrix_q{q}'])
            assert e2 < 1e-11
            metrics[f'propagated_legendre_q{q}'] = dict(all_pair_replay_relative=e, full_composition_relative=e2)
        rules = []
        previous = read('outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz')['jacobi_corrected_matrix'] if degree7 else reuse['corrected_matrix_q3']
        for name in (['xiao_gimbutas7'] if degree7 else ['xiao_gimbutas', 'jacobi']):
            bary, weights = saved[f'{name}_rule_barycentric'], saved[f'{name}_rule_weights']
            p, w = source_arrays(tets, volume, local, bary, weights)
            own = np.zeros((5, 5))
            for first in range(0, len(tets), 64):
                assert monotonic()-started < 120
                pp, ww = p[first:first+64], w[first:first+64]
                distance = np.linalg.norm(pp[:, :, None]-pp[:, None, :], axis=-1)
                inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > 0)
                own += 1e-7*np.einsum('cimx,cij,cjnx->mn', ww, inverse, ww, optimize=True)
            pairs = pair_points(p, w, pa, pb, started+120).sum(axis=0)
            ids = saved[f'{name}_direct_ids']
            flatp, flatw = p.reshape(-1, 3), w.reshape(-1, 5, 3)
            distance = np.linalg.norm(flatp[ids, None]-flatp[None], axis=-1)
            threshold = np.ptp(flatp, axis=0).max()*np.finfo(float).eps
            inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > threshold)
            direct = np.einsum('ip,pmx->imx', inverse, flatw)
            check = saved[f'{name}_fmm_checks']
            by_mode = np.linalg.norm(direct-check, axis=(0, 2))/np.linalg.norm(direct, axis=(0, 2))
            matrix = saved[f'{name}_point_matrix']-own+exact_self-pairs+exact_pairs
            scale = np.linalg.inv(np.linalg.cholesky((matrix+matrix.T)/2))
            updated = float(np.linalg.norm(scale @ (matrix-previous) @ scale.T, 2))
            original_energy = float(np.linalg.norm(saved['original_energy_scale'] @ (matrix-previous) @ saved['original_energy_scale'].T, 2))
            m = dict(rule=name, all_self_point_relative=relative(own, saved[f'{name}_own_point']),
                     all_pair_point_relative=relative(pairs, saved[f'{name}_pair_point']),
                     full_composition_relative=relative(matrix, saved[f'{name}_corrected_matrix']),
                     independently_rebuilt_direct_max_relative=float(by_mode.max()),
                     updated_energy_difference=updated, original_energy_difference=original_energy)
            assert all(m[k] < 1e-10 for k in ('all_self_point_relative', 'all_pair_point_relative', 'full_composition_relative', 'independently_rebuilt_direct_max_relative'))
            reported = next(h for h in receipt['history'] if h['rule'] == name)
            assert abs(updated/reported['difference_from_previous_updated_energy']-1) < 1e-8
            assert abs(original_energy/reported['difference_from_previous_original_energy']-1) < 1e-8
            if degree7:
                centered = tets-tets.mean(axis=1)[:, None]
                mass = (np.einsum('cid,cjd->cij', centered, centered)+np.square(centered).sum((1, 2))[:, None, None]/20)/(9*volume[:, None, None])
                r = np.einsum('cim,cij,cjn->mn', local, mass, local)/59.59e6
                assert np.linalg.norm(r-np.eye(5), 2) < 1e-10
                rl = []
                for row in reported['five_mode_RL_solution_perturbation']:
                    omega = 2*np.pi*row['frequency_hz']
                    value = float(np.linalg.norm(np.linalg.solve(r+1j*omega*matrix, 1j*omega*(matrix-previous)), 2))
                    assert abs(value/row['relative_operator_norm']-1) < 1e-8
                    rl.append(dict(frequency_hz=row['frequency_hz'], reconstructed_operator_norm=value))
                m['reconstructed_RL_perturbation'] = rl
            arrays[f'{name}_reconstructed_matrix'] = matrix
            rules.append(m)
            previous = matrix
        metrics.update(propagation=propagation, rules=rules)
        assert (rules[-1]['updated_energy_difference'] < 5e-5) == receipt['empirical_rule_difference_gate'] == False
        result = dict(status='ACCEPT_RECONSTRUCTED_SOURCES_AND_CORRECTIONS_GLOBAL_GATE_FALSE', metrics=metrics, failure=None)
    except Exception:
        result = dict(status='STOP_HQ_COMPACT_RECONSTRUCTION', metrics=metrics, failure=traceback.format_exc())
    np.savez_compressed(out/'reconstructed-matrices.npz', **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
                  reviewer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
                  extra_pins=extra_pins if 'extra_pins' in locals() else {},
                  artifact_sha256=sha256((out/'reconstructed-matrices.npz').read_bytes()).hexdigest(),
                  scope='Independent affine coefficient reconstruction, every5304 cell self point block, every17324 pair point block, raw exact-self and canonical pair projection, '
                  'ordered rigid congruence of every propagated member, both Legendre propagation baselines in review03 (pinned/reused for degree7), all saved direct targets per mode and actual updated/original energy metrics. '
                  'Full FMM point matrices remain pinned producer outputs; no FMM rerun or new exact Green integrals. No field/board claim.')
    (out/'independent-review.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['failure'] is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--degree7', action='store_true')
    raise SystemExit(main(parser.parse_args().degree7))
