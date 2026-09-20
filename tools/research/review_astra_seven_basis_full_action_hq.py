"""SPD Decap PI Evaluator v0.23.1: source-based saved-action QA, no FMM."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'outputs/research/astra-seven-basis-full-action-review-02'
PINS = {
    'outputs/research/astra-seven-basis-full-action-01/result.json': '0c7305addd7251b33f794a8cdab5cf9ab1908826e5edc7bc34f4d43f53abc5ef',
    'outputs/research/astra-seven-basis-full-action-01/full-actions.npz': '13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b',
    'outputs/research/astra-joint-seven-basis-extension-review-03/independent-review.json': '433de3e7d32e59c49506e532173992be714717d7261d99b1990520e671d9105d',
    'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz': '29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933',
    'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz': '14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',
    'outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz': 'ca0c1d600316fd704aff4e741f956a36eed9c1fd224d10f1384c3aaf17ce9c44',
}


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def load(path):
    with np.load(ROOT/path) as z:
        return {key: z[key] for key in z.files}


def relative(a, b):
    return float(np.linalg.norm(a-b)/np.linalg.norm(b))


def main():
    started = monotonic()
    for path, expected in PINS.items():
        assert sha(ROOT/path) == expected, path
    producer = json.loads((ROOT/'outputs/research/astra-seven-basis-full-action-01/result.json').read_text())
    for path, expected in producer['pins'].items():
        assert sha(ROOT/path) == expected, path
    saved = load('outputs/research/astra-seven-basis-full-action-01/full-actions.npz')
    mesh = load('outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz')
    registry = load('outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz')
    old = load('outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz')
    vertices, cells = mesh['vertices_local_um']*1e-6, mesh['cells']
    tets, q = vertices[cells], saved['face_currents']
    volume = np.linalg.det(tets[:, 1:]-tets[:, :1])/6
    assert volume.min() > 0 and q.shape == (12546, 7)
    face_lookup = {tuple(face): i for i, face in enumerate(mesh['face_vertices'])}
    columns = np.array([[face_lookup[tuple(sorted(np.delete(cell, i)))] for i in range(4)] for cell in cells])
    signs = np.where(mesh['first_owner_cell'][columns] == np.arange(5304)[:, None], 1., -1.)
    local = signs[:, :, None]*q[columns]
    qa, qb = (5+3*np.sqrt(5))/20, (5-np.sqrt(5))/20
    bary2 = np.full((4, 4), qb)
    np.fill_diagonal(bary2, qa)
    quadrature2 = np.einsum('qi,cid->cqd', bary2, tets)
    phi2 = (quadrature2[:, :, None]-tets[:, None])/(3*volume[:, None, None, None])
    mass = np.einsum('c,cqid,cqjd->cij', volume/(4*59.59e6), phi2, phi2)
    signed_mass = mass*signs[:, :, None]*signs[:, None, :]
    rows = np.broadcast_to(columns[:, :, None], mass.shape).ravel()
    cols = np.broadcast_to(columns[:, None, :], mass.shape).ravel()
    resistance = coo_matrix((signed_mass.ravel(), (rows, cols)), shape=(12546, 12546)).tocsr()
    gram = q.T@(resistance@q)
    assert np.linalg.norm(gram-np.eye(7), 2) < 1e-10
    assert np.max(np.abs(local.sum(1))) < 1e-10
    boundary = mesh['boundary_face_ids']
    coords = vertices[mesh['face_vertices'][boundary]]
    body = mesh['cell_body'][mesh['first_owner_cell'][boundary]]
    terminals = []
    for z in (0., 75e-6):
        for b in (0, 1):
            center = vertices[np.unique(cells[mesh['cell_body'] == b])]
            xy = (center[:, :2].min(0)+center[:, :2].max(0))/2
            mask = (body == b)&np.all(np.abs(coords[:, :, 2]-z) < 1e-15, axis=1)
            if z:
                mask &= np.max(np.linalg.norm(coords[:, :, :2]-xy, axis=2), axis=1) <= 20e-6+1e-15
            terminals.append(q[boundary[mask]].sum(0))
    terminals = np.array(terminals)
    assert relative(terminals, saved['terminal_flux']) < 1e-12
    pa, pb = registry['pair_a'], registry['pair_b']
    h = registry['reference_forward_reverse_h'][registry['pair_reference_index'], 0]
    assert len(pa) == len(np.unique(pa*5304+pb)) == 58252
    self_ids = np.unique(np.linspace(0, 5303, 64, dtype=int))
    pair_ids = np.unique(np.linspace(0, 58251, 256, dtype=int))
    checks, rebuilt = {}, {}
    for name, old_key in [('jacobi', 'corrected_jac27_matrix'), ('xg31', 'corrected_xg31_matrix')]:
        bary, weights = saved[f'{name}_barycentric'], saved[f'{name}_normalized_weights']
        points = np.einsum('qi,cid->cqd', bary, tets)
        # Independent affine coefficients J(x)=a+b*x with physical volume weights.
        coeff_b = local.sum(1)/(3*volume[:, None])
        coeff_a = -np.einsum('cim,cid->cmd', local, tets)/(3*volume[:, None, None])
        weighted = (coeff_a[:, None]+coeff_b[:, None, :, None]*points[:, :, None])*(volume[:, None, None, None]*weights[None, :, None, None])
        expected_moment = volume[:, None, None]*(coeff_a+coeff_b[:, :, None]*tets.mean(1)[:, None])
        assert relative(weighted.sum(1), expected_moment) < 1e-12
        flat = points.reshape(-1, 3)
        ids = saved[f'{name}_direct_ids']
        distances = np.linalg.norm(flat[ids, None]-flat[None], axis=2)
        inverse = np.divide(1., distances, out=np.zeros_like(distances), where=distances > 0)
        direct = np.einsum('tp,pmd->tmd', inverse, weighted.reshape(-1, 7, 3))
        per_mode = np.linalg.norm(direct-saved[f'{name}_fmm_checks'], axis=(0, 2))/np.linalg.norm(direct, axis=(0, 2))
        assert per_mode.max() < 1e-10
        spot_errors = {}
        for label, aa, bb, expected in [
            ('self64', self_ids, self_ids, saved[f'{name}_self_point_blocks'][self_ids]),
            ('pairs256', pa[pair_ids], pb[pair_ids], saved[f'{name}_pair_point_blocks'][pair_ids])]:
            blocks = []
            for ca, cb in zip(aa, bb, strict=True):
                # Unit local RT0 affine coefficients; independent double point sum.
                basis_a = (points[ca, :, None]/3-tets[ca][None]/3)*weights[:, None, None]
                basis_b = (points[cb, :, None]/3-tets[cb][None]/3)*weights[:, None, None]
                distance = np.linalg.norm(points[ca, :, None]-points[cb, None], axis=2)
                inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > 0)
                block = sum(basis_a[:, :, d].T@inverse@basis_b[:, :, d] for d in range(3))*1e-7
                blocks.append(block)
            spot_errors[label] = relative(np.array(blocks), expected)
            assert spot_errors[label] < 1e-11
        corrected_local = np.einsum('cij,cjn->cin', registry['exact_cell_self_h']-saved[f'{name}_self_point_blocks'], local)
        pair = h-saved[f'{name}_pair_point_blocks']
        np.add.at(corrected_local, pa, np.einsum('pij,pjn->pin', pair, local[pb]))
        np.add.at(corrected_local, pb, np.einsum('pij,pin->pjn', pair, local[pa]))
        assembled = np.zeros((12546, 7))
        np.add.at(assembled, columns.ravel(), (signs[:, :, None]*(saved[f'{name}_local_point_action']+corrected_local)).reshape(-1, 7))
        matrix = q.T@assembled
        rebuilt[name] = assembled
        action_relative = relative(assembled, saved[f'{name}_full_action'])
        foldback = relative(matrix, saved[f'{name}_matrix'])
        replay = relative(matrix[:5, :5], old[old_key])
        assert max(action_relative, foldback, replay) < 1e-11
        checks[name] = dict(per_mode_direct_relative=per_mode.tolist(), point_block_spot_checks=spot_errors,
                           all_self_pair_action_rebuild_relative=action_relative,
                           fine_action_matrix_foldback_relative=foldback, frozen_five_replay_relative=replay,
                           raw_matrix_symmetry_relative=relative(matrix, matrix.T))
        assert checks[name]['raw_matrix_symmetry_relative'] < 1e-10
    mx, mj = q.T@rebuilt['xg31'], q.T@rebuilt['jacobi']
    energy_scale = np.linalg.inv(np.linalg.cholesky((mx+mx.T)/2))
    energy_difference = float(np.linalg.norm(energy_scale@(mx-mj)@energy_scale.T, 2))
    factor = splu(resistance.tocsc())
    delta = rebuilt['xg31']-rebuilt['jacobi']
    dual_delta = delta.T@factor.solve(delta)
    dual_reference = rebuilt['xg31'].T@factor.solve(rebuilt['xg31'])
    dual_scale = np.linalg.inv(np.linalg.cholesky((dual_reference+dual_reference.T)/2))
    dual_difference = float(np.sqrt(np.linalg.norm(dual_scale@dual_delta@dual_scale.T, 2)))
    assert abs(energy_difference-producer['empirical_seven_mode_updated_energy_rule_difference']) < 1e-10
    assert abs(dual_difference-producer['unrestricted_fine_action_r_dual_relative_difference']) < 1e-10
    OUT.mkdir()
    np.savez_compressed(OUT/'review-arrays.npz', jacobi_action=rebuilt['jacobi'], xg31_action=rebuilt['xg31'],
                        resistance_gram=gram, terminal_flux=terminals, updated_energy_scale=energy_scale,
                        full_action_delta_r_dual_gram=dual_delta, full_action_reference_r_dual_gram=dual_reference,
                        self_sample_ids=self_ids, pair_sample_ids=pair_ids)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='QUALIFIED_SAVED_ACTION_COMPOSITION_RULE_AGREEMENT_FAILED', pins=PINS,
                  producer_pins=producer['pins'], checks=checks,
                  independently_updated_seven_energy_difference=energy_difference,
                  independently_unrestricted_r_dual_difference=dual_difference,
                  empirical_rule_gate=energy_difference < 5e-5, elapsed_s=monotonic()-started,
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(OUT/'review-arrays.npz'),
                  scope='Independent geometric incidence, degree2 R, affine sources and16 all-source direct targets per mode/rule. '
                  'All5304 self/58252 near action projections/scatter reconstructed; point4x4 kernels independently spot-checked64 self/256 pairs per rule. '
                  'Full local FMM moments are reused, not independently recomputed. Actual updated7-energy and unrestricted R-dual metrics regenerated. '
                  'Earlier review01 only saved algebra. No new FMM, full-space/integral/field/board convergence.')
    assert not result['empirical_rule_gate']
    (OUT/'independent-review.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
