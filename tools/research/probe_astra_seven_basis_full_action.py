"""SPD Decap PI Evaluator v0.23.1: seven trial currents, all fine RT0 test rows.

Save the full L@Q action so subsequent complement analysis needs no repeated
FMM. Qualified self/pair blocks are reused on the identical frozen mesh.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import monotonic
import traceback

import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import splu
from scipy.spatial import cKDTree
from qualify_astra_tetra_jacobi_rule import rule

ROOT = geometry.ROOT
PINS = {
    'tools/research/apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified.py': '3ad0e14937943f4c7941828f1e1bae345b05b5c88b812ddbdb68e69c83ca60d4',
    'tools/research/qualify_astra_tetra_jacobi_rule.py': 'b9dd7ccc41a39a3e8d1214dc9fc9cdef14e09f2a6637d321d25bca7874c6a49e',
    'tools/research/probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
    'outputs/research/astra-joint-seven-basis-extension-01/result.json': 'a13d8339234a5f1fbe816f22f13f19e6dbd0d928d307878bfb6b41720af83e4f',
    'outputs/research/astra-joint-seven-basis-extension-01/seven-basis.npz': '36a9ea47941403b655f68a75460345f11171fa11b3f4cee99f618ae9cd70780c',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-joint-vector-pair-registry-01/result.json': '29beacbaaf4cd44016d59245c52321c263e4552565091d74f2b13cfac89b8eec',
    'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz': '29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933',
    'outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz': 'ca0c1d600316fd704aff4e741f956a36eed9c1fd224d10f1384c3aaf17ce9c44',
    'outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz': '2b739de10382dbeb1cb327948d4d5f8b540cfc61944e92788ad8093f5af9de45',
    'outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz': '6cab19f0cf9e906e123f6aef8dfd39b5c82e4e496907ea85ead7f3f365d816dd',
    'outputs/research/astra-tetra-xg7-rule-01/rule-comparison.npz': '75ba6ecd0fb04254f89dd7d7ba21af98579ca0426a133f5a142e55cda33802a5',
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def load(path):
    with np.load(ROOT/path) as z:
        return {k: z[k] for k in z.files}


def scatter(joint, local):
    answer = np.zeros((12546, local.shape[-1]))
    np.add.at(answer, joint['local_face_columns'].ravel(),
              (joint['local_face_signs'][:, :, None]*local).reshape(-1, local.shape[-1]))
    return answer


def point_blocks(p, weighted_local, pa, pb, threshold, deadline, self_pairs=False):
    """Direct full4x4 point Galerkin blocks, independent of trial currents."""
    blocks = np.empty((len(pa), 4, 4))
    for start in range(0, len(pa), 64):
        assert monotonic() < deadline, 'point-block deadline'
        a, b = pa[start:start+64], pb[start:start+64]
        distances = np.linalg.norm(p[a, :, None]-p[b, None, :], axis=3)
        if self_pairs:
            diagonal = np.arange(p.shape[1])
            assert np.all(distances[:, diagonal, diagonal] == 0)
            distances[:, diagonal, diagonal] = np.inf
        assert distances.min() > threshold
        potential = np.einsum('cpq,cqjd->cpjd', 1/distances, weighted_local[b], optimize=True)
        blocks[start:start+len(a)] = 1e-7*np.einsum('cpid,cpjd->cij', weighted_local[a], potential, optimize=True)
    return blocks


def run(out):
    started, arrays, history = monotonic(), {}, []
    deadline = started+590
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert digest(ROOT/path) == expected, path
        joint = geometry.load_joint()
        tets, volumes = joint['tetrahedra_m'], joint['cell_volumes_m3']
        seven = load('outputs/research/astra-joint-seven-basis-extension-01/seven-basis.npz')
        old = load('outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz')
        basis_transform = np.eye(7)
        basis_transform[:5, :5] = old['R_whitening']
        q = seven['final_face_flux_basis'] @ basis_transform
        assert np.linalg.norm(q[:, :5]-old['whitened_face_currents'])/np.linalg.norm(q[:, :5]) < 1e-13
        q[:, :5] = old['whitened_face_currents']
        local = joint['local_face_signs'][:, :, None]*q[joint['local_face_columns']]
        centered = tets-tets.mean(axis=1)[:, None]
        mass = (np.einsum('cid,cjd->cij', centered, centered)+
                np.square(centered).sum(axis=(1, 2))[:, None, None]/20)/(9*volumes[:, None, None])
        gram = np.einsum('cim,cij,cjn->mn', local, mass, local)/59.59e6
        assert np.linalg.norm(gram-np.eye(7), 2) < 1e-10
        reg = load('outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz')
        pa, pb = reg['pair_a'], reg['pair_b']
        assert len(pa) == 58252 and np.all(pa < pb) and len(np.unique(pa*5304+pb)) == len(pa)
        exact_pair = reg['reference_forward_reverse_h'][reg['pair_reference_index'], 0]
        exact_self = reg['exact_cell_self_h']
        baseline = load('outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz')
        arrays.update(face_currents=q, final_seven_to_whitened=basis_transform, verified_r_gram=gram,
                      terminal_flux=seven['terminal_flux_final_a']@basis_transform,
                      terminal_names=seven['terminal_names'])
        compact = [('jacobi', 3, '01', 'jacobi', 'jac27'), ('xg31', 7, '02', 'xiao_gimbutas7', 'xg31')]
        for name, order, stage, prior_name, suffix in compact:
            if name == 'jacobi':
                bary, weights = rule(order, 'jacobi')
            else:
                rule_data = load('outputs/research/astra-tetra-xg7-rule-01/rule-comparison.npz')
                bary, weights = rule_data['barycentric'], rule_data['normalized_weights']
            p = np.einsum('pi,cid->cpd', bary, tets)
            weighted_local = (p[:, :, None]-tets[:, None])*weights[None, :, None, None]/3
            weighted = np.einsum('cim,cpid->cpmd', local, weighted_local, optimize=True)
            exact_moment = np.einsum('cim,cid->cmd', local, tets.mean(axis=1)[:, None]-tets)/3
            assert np.linalg.norm(weighted.sum(1)-exact_moment)/np.linalg.norm(exact_moment) < 1e-12
            points = p.reshape(-1, 3)
            threshold = np.ptp(points, axis=0).max()*np.finfo(float).eps
            assert cKDTree(points).query(points, k=2)[0][:, 1].min() > threshold
            self_point = point_blocks(p, weighted_local, np.arange(5304), np.arange(5304), threshold, deadline, True)
            pair_point = point_blocks(p, weighted_local, pa, pb, threshold, deadline)
            # Same one-directed-block/transpose reciprocal definition as frozen5.
            # Reverse reference integrals were separately qualified, never averaged.
            local_correction = np.einsum('cij,cjn->cin', exact_self-self_point, local)
            np.add.at(local_correction, pa, np.einsum('pij,pjn->pin', exact_pair-pair_point, local[pb]))
            np.add.at(local_correction, pb, np.einsum('pji,pjn->pin', exact_pair-pair_point, local[pa]))
            correction = scatter(joint, local_correction)
            prior = load(f'outputs/research/astra-compact-rule-static-matrix-{stage}/static-matrices.npz')
            expected = baseline[f'corrected_{suffix}_matrix']-prior[f'{prior_name}_point_matrix']
            correction_replay = float(np.linalg.norm(q[:, :5].T@correction[:, :5]-expected)/np.linalg.norm(expected))
            assert correction_replay < 1e-11
            arrays.update({f'{name}_self_point_blocks': self_point, f'{name}_pair_point_blocks': pair_point,
                           f'{name}_near_correction_action': correction, f'{name}_barycentric': bary,
                           f'{name}_normalized_weights': weights})
            ids = np.unique(np.linspace(0, len(points)-1, 16, dtype=int))
            distances = np.linalg.norm(points[ids, None]-points[None], axis=2)
            inverse = np.divide(1., distances, out=np.zeros_like(distances), where=distances > threshold)
            flat_weighted = weighted.reshape(-1, 7, 3)
            direct = np.einsum('tp,pma->tma', inverse, flat_weighted)
            checks = np.empty_like(direct)
            fine_action = np.full((12546, 7), np.nan)
            point_matrix = np.full((7, 7), np.nan)
            local_actions = np.full((5304, 4, 7), np.nan)
            timings = []
            for column in range(7):
                assert monotonic() < deadline, 'FMM launch deadline'
                print(json.dumps(dict(stage='fmm_column_start', rule=name, column=column,
                                      points=len(points), channels=3, owned_pid=os.getpid())), flush=True)
                before = monotonic()
                field = geometry.fmm3dpy.lfmm3d(eps=1e-10, sources=np.asfortranarray(points.T),
                    charges=np.asfortranarray(flat_weighted[:, column].T), pg=1, nd=3)
                assert field.ier == 0 and np.isfinite(field.pot).all()
                potential = 4*np.pi*np.asarray(field.pot).T
                local_actions[:, :, column] = 1e-7*np.einsum('cpid,cpd->ci', weighted_local, potential.reshape(p.shape))
                fine_action[:, column] = scatter(joint, local_actions[:, :, column, None])[:, 0]
                point_matrix[:, column] = 1e-7*np.einsum('pma,pa->m', flat_weighted, potential)
                foldback = np.linalg.norm(q.T@fine_action[:, column]-point_matrix[:, column])/np.linalg.norm(point_matrix[:, column])
                checks[:, column] = potential[ids]
                error = np.linalg.norm(checks[:, column]-direct[:, column])/np.linalg.norm(direct[:, column])
                assert error < 1e-10 and foldback < 1e-11
                timings.append(monotonic()-before)
                arrays.update({f'{name}_point_action': fine_action, f'{name}_point_matrix': point_matrix,
                               f'{name}_local_point_action': local_actions, f'{name}_completed_columns': np.arange(column+1),
                               f'{name}_direct_ids': ids, f'{name}_direct_reference': direct,
                               f'{name}_fmm_checks': checks})
                np.savez_compressed(out/'full-actions.npz', **arrays)
                print(json.dumps(dict(stage='fmm_column_complete', rule=name, column=column,
                    elapsed_s=timings[-1], direct_relative=float(error), foldback_relative=float(foldback))), flush=True)
                del field, potential
            action = fine_action+correction
            matrix = q.T@action
            raw_symmetry = float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix))
            replay = float(np.linalg.norm(matrix[:5, :5]-baseline[f'corrected_{suffix}_matrix'])/np.linalg.norm(matrix[:5, :5]))
            assert raw_symmetry < 1e-10 and replay < 1e-10
            scale = np.linalg.inv(np.linalg.cholesky((matrix+matrix.T)/2))
            arrays.update({f'{name}_full_action': action, f'{name}_matrix': matrix, f'{name}_energy_scale': scale})
            history.append(dict(rule=name, points=len(points), total_fmm_s=sum(timings), column_s=timings,
                                raw_symmetry_relative=raw_symmetry, old_five_matrix_replay_relative=replay,
                                old_five_correction_replay_relative=correction_replay))
            np.savez_compressed(out/'full-actions.npz', **arrays)
        delta = arrays['xg31_full_action']-arrays['jacobi_full_action']
        difference = arrays['xg31_matrix']-arrays['jacobi_matrix']
        empirical = float(np.linalg.norm(arrays['xg31_energy_scale']@difference@arrays['xg31_energy_scale'].T, 2))
        space = load('outputs/research/astra-boundary-joint-current-space-01/current-space.npz')
        resistance = csr_matrix((space['resistance_data_ohm'], space['mass_col'], space['mass_row_ptr']), shape=tuple(space['mass_shape']))
        factor = splu(resistance.tocsc())
        dual_delta = delta.T@factor.solve(delta)
        ref_action = arrays['xg31_full_action']
        dual_reference = ref_action.T@factor.solve(ref_action)
        dual_scale = np.linalg.inv(np.linalg.cholesky((dual_reference+dual_reference.T)/2))
        full_action_difference = float(np.sqrt(np.linalg.norm(dual_scale@dual_delta@dual_scale.T, 2)))
        arrays.update(full_action_rule_delta=delta, full_action_delta_r_dual_gram=dual_delta,
                      full_action_reference_r_dual_gram=dual_reference)
        result = dict(status='COMPLETED_SEVEN_BASIS_FULL_FINE_ACTION', failure=None,
                      empirical_seven_mode_updated_energy_rule_difference=empirical,
                      empirical_seven_mode_rule_gate=empirical < 5e-5,
                      unrestricted_fine_action_r_dual_relative_difference=full_action_difference,
                      seven_mode_RL_solution_perturbation=[dict(frequency_hz=f, relative_operator_norm=float(np.linalg.norm(
                          np.linalg.solve(gram+2j*np.pi*f*arrays['xg31_matrix'], 2j*np.pi*f*difference), 2)))
                          for f in (1e3, 1e6, 1e7, 1e8, 1e9)])
    except Exception:
        result = dict(status='STOP_SEVEN_BASIS_FULL_FINE_ACTION', failure=traceback.format_exc())
    np.savez_compressed(out/'full-actions.npz', **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', pins=PINS, history=history,
                  driver_sha256=digest(Path(__file__)), artifact_sha256=digest(out/'full-actions.npz'),
                  elapsed_s=monotonic()-started,
                  scope='Seven trial currents on same5304 tetrahedra; all12546 fine test rows. '
                  'Full intercell point action plus identical-point subtraction and qualified5304 self/58252 pair replacements. '
                  'Both cross directions of7x7 point matrix independently computed. Stored full/local actions support later complements. '
                  'Empirical two-rule agreement is not absolute integration or current-space convergence. '
                  'Unrestricted R-dual fine-action difference includes charge/exterior tests and is not a constrained field error. '
                  'No charge/scalar/material/return/field/port/board closure.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['failure'] is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.worker:
        raise SystemExit(run(out))
    assert digest(ROOT/'tools/research/probe_astra_fmm3d_runtime.py') == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=600, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]))
