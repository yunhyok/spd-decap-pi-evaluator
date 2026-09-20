"""SPD Decap PI Evaluator v0.23.1: independent affine-source and saved-action QA for new3."""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import csr_matrix
from review_astra_fourteen_incremental_action_strict import digest, load, relative_difference, scatter

ROOT = Path(__file__).resolve().parents[2]
SOURCE = 'outputs/research/astra-seventeen-incremental-action-01'
PINS = {
    'tools/research/review_astra_fourteen_incremental_action_strict.py': 'b01cc4c4b28f2d813acdbaa353e33ff2cd757dcad9430b51b7ed92a3d59ca492',
    'tools/research/probe_astra_seventeen_incremental_action.py': 'd06ad8a28b73eb89271ca5722a69084fc83ac4e23f0e4da50bbeb56402766b66',
    SOURCE+'/result.json': 'f452022bc2b11f1d0c9cd8f69b92c87c7267e2cfaec7697b1e0be4c6db699302',
    SOURCE+'/seventeen-actions.npz': '6b4b9b459c55746e90832a492eacd62de5c53ba3ad085c8be452d4f081ed6e76',
    'outputs/research/astra-fourteen-current-incremental-action-review-04/independent-review.json': '5d9abbff986cc231e16cac1e21536020e920378c6cab6a9f1a774ccaac533192',
    'outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz': '382137f5398cb7c1532c12ba264653172d59f90f2baabdc6462b4e25fff3de49',
    'outputs/research/astra-seven-basis-full-action-01/full-actions.npz': '13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b',
    'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz': 'a58b0943bdef0baccb6669882d33e500bbb5edb668113d17858bff5e7c5f7c0a',
    'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz': '29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933',
    'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz': '14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',
    'outputs/research/astra-boundary-joint-current-space-01/current-space.npz': '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5',
}


def run():
    started = monotonic(); out = ROOT/'outputs/research/astra-seventeen-incremental-action-review-01'
    assert not out.exists()
    for path, expected in PINS.items():
        assert digest(ROOT/path) == expected, path
    a = load(ROOT/SOURCE/'seventeen-actions.npz')
    receipt = json.loads((ROOT/SOURCE/'result.json').read_text())
    parent = load(ROOT/'outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz')
    first = load(ROOT/'outputs/research/astra-seven-basis-full-action-01/full-actions.npz')
    correction = load(ROOT/'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz')
    reg = load(ROOT/'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz')
    mesh = load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz')
    space = load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz')
    q = a['face_currents']; cols, signs = space['local_rt0_face_columns'], space['local_rt0_face_signs']
    assert np.array_equal(q[:, :14], parent['face_currents'])
    tets = mesh['vertices_local_um'][mesh['cells']]*1e-6
    volume = np.linalg.det(tets[:, 1:]-tets[:, :1])/6
    assert np.all(volume > 0)
    local = signs[:, :, None]*q[cols]
    centroid = tets.mean(1)
    # Independent centered affine J and physical integration volume; no V cancellation.
    center_j = np.einsum('cin,cid->cnd', local, centroid[:, None]-tets)/(3*volume[:, None, None])
    slope = local.sum(1)/(3*volume[:, None])
    moments = np.einsum('c,cnd->nd', volume, center_j)
    assert relative_difference(moments, a['exact_integrated_currents']) < 1e-12
    r = csr_matrix((space['resistance_data_ohm'], space['mass_col'], space['mass_row_ptr']), shape=tuple(space['mass_shape']))
    rg = q.T@(r@q)
    assert relative_difference(rg, a['resistance_gram']) < 1e-12
    rank = 1e-7*moments@moments.T
    assert relative_difference(rank, a['minus_ik_coefficient']) < 1e-12
    pa = np.r_[reg['pair_a'], correction['new_pair_a']]
    pb = np.r_[reg['pair_b'], correction['new_pair_b']]
    raw = np.r_[reg['reference_forward_reverse_h'][reg['pair_reference_index'], 0],
                correction['reference_forward_reverse_h'][correction['new_pair_reference_index'], 0]]
    assert len(np.unique(pa*5304+pb)) == len(pa) == 150584 and np.all(pa < pb)
    metrics, arrays = {}, {}
    for name in ('jacobi', 'xg31'):
        point = np.r_[first[f'{name}_pair_point_blocks'], correction[f'{name}_new_pair_point_blocks']]
        ln = local[:, :, 14:]
        near = np.einsum('cij,cjn->cin', reg['exact_cell_self_h']-first[f'{name}_self_point_blocks'], ln)
        np.add.at(near, pa, np.einsum('pij,pjn->pin', raw-point, ln[pb]))
        np.add.at(near, pb, np.einsum('pji,pjn->pin', raw-point, ln[pa]))
        rebuilt_near = scatter(cols, signs, near)
        rebuilt_new = rebuilt_near+scatter(cols, signs, a[f'{name}_new_local_point_action'])
        full = np.c_[parent[f'{name}_full_action'], rebuilt_new]
        matrix = q.T@full
        bary, weights = a[f'{name}_barycentric'], a[f'{name}_normalized_weights']
        pc = np.einsum('pi,cid->cpd', bary, tets)
        current = center_j[:, None]+slope[:, None, :, None]*(pc-centroid[:, None])[:, :, None]
        sources = (current*volume[:, None, None, None]*weights[None, :, None, None]).reshape(-1, 17, 3)
        pts = pc.reshape(-1, 3); ids = a[f'{name}_direct_ids']
        distances = np.linalg.norm(pts[ids, None]-pts[None], axis=2)
        inverse = np.divide(1., distances, out=np.zeros_like(distances), where=distances > np.ptp(pts, axis=0).max()*np.finfo(float).eps)
        direct = np.einsum('tp,pnd->tnd', inverse, sources[:, 14:])
        m = dict(near_relative=relative_difference(rebuilt_near, a[f'{name}_new_near_action']),
            full_relative=relative_difference(full, a[f'{name}_full_action']),
            matrix_relative=relative_difference(matrix, a[f'{name}_matrix']),
            cross_reciprocity=relative_difference(matrix[:14, 14:], matrix[14:, :14].T),
            independent_affine_direct_reference=relative_difference(direct, a[f'{name}_direct_reference']),
            independent_affine_direct_fmm=relative_difference(direct, a[f'{name}_fmm_checks']))
        assert max(m.values()) < 1e-10
        t = a['terminal_flux']; rows = []
        for expected in receipt['checks'][name]['retained14_to17_diagnostics']:
            frequency = expected['frequency_hz']; w = 2*np.pi*frequency
            z = rg+1j*w*matrix+w*w/299792458.0*rank
            u = np.linalg.solve(z, t.T); old = np.linalg.solve(z[:14, :14], t[:, :14].T)
            diff = u-np.r_[old, np.zeros((3, 4))]
            rn = lambda v: float(np.sqrt(np.trace(v.conj().T@rg@v).real))
            rc = rn(diff)/rn(u); yc = float(np.linalg.norm(t@u-t[:, :14]@old)/np.linalg.norm(t@u))
            assert abs(rc-expected['four_contact_current_r_change']) < 1e-12
            assert abs(yc-expected['four_contact_admittance_relative']) < 1e-12
            rows.append(dict(frequency_hz=frequency, current_r_change=rc, contact_y_change=yc))
        metrics[name] = dict(checks=m, response_rows=rows); arrays[f'{name}_matrix'] = matrix
    e = np.linalg.inv(np.linalg.cholesky((arrays['xg31_matrix']+arrays['xg31_matrix'].T)/2))
    gap = float(np.linalg.norm(e@(arrays['xg31_matrix']-arrays['jacobi_matrix'])@e.T, 2))
    assert abs(gap-receipt['checks']['empirical_seventeen_energy_difference']) < 1e-13
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'review.npz', physical_r_gram=rg, moments=moments, **arrays)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='ACCEPT_SAVED_SEVENTEEN_ACTION_WITH_RULE_STOP', pins=PINS,
        driver_sha256=digest(Path(__file__)), artifact_sha256=digest(out/'review.npz'),
        elapsed_s=monotonic()-started, checks=metrics,
        empirical_energy_rule_difference=gap, empirical_rule_gate=gap < 5e-5,
        scope='Fresh centered affine source moments and all16 source-target direct sums vs saved FMM, '
        'physical R, full5304self/150584pair correction scatter, complete saved action/matrix/cross and '
        'local14-to17 response. Raw Green references and local FMM moments reused. No absolute integral '
        'or current/mesh/board convergence; empirical two-rule agreement remains outside5e-5.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    run()
