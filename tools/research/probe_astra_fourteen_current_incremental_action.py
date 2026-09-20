"""SPD Decap PI Evaluator v0.23.1: only seven NEW FMM columns for retained14."""
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
from probe_astra_seven_basis_full_action import scatter

ROOT = geometry.ROOT
PINS = {
    'tools/research/probe_astra_seven_basis_full_action.py': 'cee40c743a43ecd25ca80532e30ad2f2a7edfa121d5484d9183990a2f83e92ca',
    'tools/research/probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
    'outputs/research/astra-joint-fourteen-current-space-01/result.json': '7174858a5367355fe7663356be71f8840976bb3b6742529101f951d3acd65313',
    'outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz': '3125d32b03485347679a360c93adcb1f35948c51987ae12110348743dbca967a',
    'outputs/research/astra-seven-touch-group-correction-01/result.json': 'a3f465db16a86db24eb4c0d8ed0df1c4e8e964a3b498c169bf2dbe1a2450c3e6',
    'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz': 'a58b0943bdef0baccb6669882d33e500bbb5edb668113d17858bff5e7c5f7c0a',
    'outputs/research/astra-seven-basis-full-action-01/full-actions.npz': '13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b',
    'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz': '29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933',
}


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def load(path):
    with np.load(ROOT/path) as z:
        return {k: z[k] for k in z.files}


def run(out):
    started, arrays, history = monotonic(), {}, []
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha(ROOT/path) == expected, path
        space = load('outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz')
        parent = load('outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz')
        first = load('outputs/research/astra-seven-basis-full-action-01/full-actions.npz')
        registry = load('outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz')
        q, r = space['face_flux_basis'], space['physical_r_gram']
        assert q.shape == (12546, 14) and np.array_equal(q[:, :7], parent['face_currents'])
        assert np.linalg.norm(r-np.eye(14), 2) < 1e-9
        joint = geometry.load_joint()
        tets = joint['tetrahedra_m']
        local = joint['local_face_signs'][:, :, None]*q[joint['local_face_columns']]
        pa = np.r_[registry['pair_a'], parent['new_pair_a']]
        pb = np.r_[registry['pair_b'], parent['new_pair_b']]
        exact = np.r_[registry['reference_forward_reverse_h'][registry['pair_reference_index'], 0],
                      parent['reference_forward_reverse_h'][parent['new_pair_reference_index'], 0]]
        assert len(pa) == len(np.unique(pa*5304+pb)) == 150584 and np.all(pa < pb)
        terminal_map = np.c_[first['terminal_flux'], np.zeros((4, 7))]
        moments = np.einsum('cin,cid->nd', local, tets.mean(1)[:, None]-tets)/3
        rank_term = 1e-7*moments@moments.T
        arrays.update(face_currents=q, resistance_gram=r, terminal_flux=terminal_map,
                      exact_integrated_currents=moments, minus_ik_coefficient=rank_term)
        for name in ('jacobi', 'xg31'):
            assert monotonic()-started < 570
            bary, weights = first[f'{name}_barycentric'], first[f'{name}_normalized_weights']
            points_cell = np.einsum('pi,cid->cpd', bary, tets)
            weighted_local = (points_cell[:, :, None]-tets[:, None])*weights[None, :, None, None]/3
            weighted = np.einsum('cin,cpid->cpnd', local, weighted_local, optimize=True)
            point_pairs = np.r_[first[f'{name}_pair_point_blocks'], parent[f'{name}_new_pair_point_blocks']]
            local_new = local[:, :, 7:]
            near = np.einsum('cij,cjn->cin', registry['exact_cell_self_h']-first[f'{name}_self_point_blocks'], local_new)
            np.add.at(near, pa, np.einsum('pij,pjn->pin', exact-point_pairs, local_new[pb]))
            np.add.at(near, pb, np.einsum('pji,pjn->pin', exact-point_pairs, local_new[pa]))
            near_action = scatter(joint, near)
            points, currents = points_cell.reshape(-1, 3), weighted.reshape(-1, 14, 3)
            ids = np.unique(np.linspace(0, len(points)-1, 16, dtype=int))
            distance = np.linalg.norm(points[ids, None]-points[None], axis=2)
            threshold = np.ptp(points, axis=0).max()*np.finfo(float).eps
            inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > threshold)
            direct = np.einsum('tp,pnd->tnd', inverse, currents[:, 7:])
            checks = np.full_like(direct, np.nan)
            action_new = np.full((12546, 7), np.nan)
            point_matrix = np.full((14, 7), np.nan)
            local_actions = np.full((5304, 4, 7), np.nan)
            times = []
            arrays.update({f'{name}_new_near_action': near_action, f'{name}_direct_ids': ids,
                           f'{name}_direct_reference': direct, f'{name}_barycentric': bary,
                           f'{name}_normalized_weights': weights})
            for column in range(7):
                assert monotonic()-started < 570
                print(json.dumps(dict(stage='new_fmm_column_start', rule=name, new_column=column,
                                      old_columns_reused=7, points=len(points), owned_pid=os.getpid())), flush=True)
                before = monotonic()
                field = geometry.fmm3dpy.lfmm3d(eps=1e-10, sources=np.asfortranarray(points.T),
                    charges=np.asfortranarray(currents[:, 7+column].T), pg=1, nd=3)
                assert field.ier == 0 and np.isfinite(field.pot).all()
                potential = 4*np.pi*np.asarray(field.pot).T
                local_actions[:, :, column] = 1e-7*np.einsum('cpid,cpd->ci', weighted_local, potential.reshape(points_cell.shape))
                action_new[:, column] = scatter(joint, local_actions[:, :, column, None])[:, 0]+near_action[:, column]
                point_matrix[:, column] = 1e-7*np.einsum('pnd,pd->n', currents, potential)
                reconstructed = q.T@(action_new[:, column]-near_action[:, column])
                assert np.linalg.norm(reconstructed-point_matrix[:, column])/np.linalg.norm(point_matrix[:, column]) < 1e-10
                checks[:, column] = potential[ids]
                error = float(np.linalg.norm(checks[:, column]-direct[:, column])/np.linalg.norm(direct[:, column]))
                assert error < 1e-10
                times.append(monotonic()-before)
                arrays.update({f'{name}_new_full_action': action_new, f'{name}_new_local_point_action': local_actions,
                               f'{name}_new_point_matrix': point_matrix, f'{name}_fmm_checks': checks,
                               f'{name}_completed_new_columns': np.arange(column+1)})
                np.savez_compressed(out/'fourteen-actions.npz', **arrays)
                print(json.dumps(dict(stage='new_fmm_column_complete', rule=name, new_column=column,
                                      elapsed_s=times[-1], direct_relative=error)), flush=True)
                del field, potential
            full_action = np.c_[parent[f'{name}_full_action'], action_new]
            matrix = q.T@full_action
            cross = float(np.linalg.norm(matrix[:7, 7:]-matrix[7:, :7].T)/np.linalg.norm(matrix[7:, :7]))
            symmetry = float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix))
            assert max(cross, symmetry) < 1e-10
            assert np.linalg.norm(matrix[:7, :7]-parent[f'{name}_matrix'])/np.linalg.norm(matrix[:7, :7]) < 1e-12
            e = np.linalg.inv(np.linalg.cholesky((matrix+matrix.T)/2))
            arrays.update({f'{name}_full_action': full_action, f'{name}_matrix': matrix, f'{name}_energy_scale': e})
            rows = []
            for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
                omega = 2*np.pi*frequency
                z = r+1j*omega*matrix+omega*(omega/geometry.C0)*rank_term
                forcing = np.r_[np.eye(7), np.zeros((7, 7))]
                response14 = np.linalg.solve(z, forcing)
                response7 = np.linalg.solve(z[:7, :7], np.eye(7))
                difference = response14-np.r_[response7, np.zeros((7, 7))]
                eig, vec = np.linalg.eigh((r+r.T)/2)
                root_r = (vec*np.sqrt(eig))@vec.T
                old_y = terminal_map[:, :7]@np.linalg.solve(z[:7, :7], terminal_map[:, :7].T)
                new_y = terminal_map@np.linalg.solve(z, terminal_map.T)
                rows.append(dict(frequency_hz=frequency, primary_band=frequency <= 1e8,
                    unit_modal_current_change_r_frobenius=float(np.linalg.norm(root_r@difference)/np.linalg.norm(root_r@response14)),
                    local_four_contact_admittance_relative=float(np.linalg.norm(new_y-old_y)/np.linalg.norm(new_y))))
            history.append(dict(rule=name, total_new_fmm_s=sum(times), new_column_s=times,
                                independently_computed_cross_reciprocity=cross, raw_matrix_symmetry=symmetry,
                                local_retained7_to14_diagnostics=rows))
            np.savez_compressed(out/'fourteen-actions.npz', **arrays)
        e = arrays['xg31_energy_scale']
        difference = arrays['xg31_matrix']-arrays['jacobi_matrix']
        error = float(np.linalg.norm(e@difference@e.T, 2))
        status, failure = 'COMPLETED_INCREMENTAL_FOURTEEN_CURRENT_ACTION', None
        metrics = dict(empirical_fourteen_energy_difference=error, empirical_fourteen_rule_gate=error < 5e-5,
                       old_columns_reused_per_rule=7, new_columns_computed_per_rule=7, total_corrected_pairs=len(pa))
    except Exception:
        status, failure, metrics = 'STOP_INCREMENTAL_FOURTEEN_CURRENT_ACTION', traceback.format_exc(), {}
    np.savez_compressed(out/'fourteen-actions.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status=status, failure=failure,
                  pins=PINS, driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'fourteen-actions.npz'),
                  elapsed_s=monotonic()-started, history=history, metrics=metrics,
                  scope='Only7 new closed-current FMM columns per rule; all12546 fine rows retained and old7 actions reused. '
                  'All5304 self/150584 pair full-affine point replacements reused, with independently computed old-new and new-old cross checks. '
                  'Local R+jwL minus-ik rank term diagnostics compare defined7/14 trial spaces, not a full physical field or board port. '
                  'No scalar/contact-charge/exterior-return/material closure, absolute integral bound or mesh/current-space convergence.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if failure is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args(); out = args.output.resolve()
    if args.worker:
        raise SystemExit(run(out))
    assert sha(ROOT/'tools/research/probe_astra_fmm3d_runtime.py') == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=600, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]))
