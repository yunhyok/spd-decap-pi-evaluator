"""SPD Decap PI Evaluator v0.23.1: only three missing closed-transport FMM columns."""
import argparse
import json
import os
from pathlib import Path
import sys
from time import monotonic
import traceback

import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
from probe_astra_fourteen_current_incremental_action import PINS as PREVIOUS_PINS, sha, load
from probe_astra_seven_basis_full_action import scatter

ROOT = geometry.ROOT
PINS = {**PREVIOUS_PINS,
    'tools/research/probe_astra_fourteen_current_incremental_action.py': '5d66daef4a9395becd6804c0062e046a51fdcc44452efef41e9690542a78e482',
    'outputs/research/astra-fourteen-current-incremental-action-01/result.json': 'a88bfe63450ee4f3e47ba2b187b904d2aae007de7946f34df83cacaf006de07d',
    'outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz': '382137f5398cb7c1532c12ba264653172d59f90f2baabdc6462b4e25fff3de49',
    'outputs/research/astra-seventeen-closed-transport-space-01/result.json': '7386889b0f343c6a2b15fe8447bb4ca46a07376c5c8ec3114fbf07d9e9af4cf3',
    'outputs/research/astra-seventeen-closed-transport-space-01/seventeen-current-space.npz': 'fa7a8ac8e4784db5751bef5115248ee719987f7285dd722a9db49c7212c79f50',
}


def run(out):
    started, arrays, checks = monotonic(), {}, {}
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha(ROOT/path) == expected, path
        space = load('outputs/research/astra-seventeen-closed-transport-space-01/seventeen-current-space.npz')
        parent = load('outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz')
        first = load('outputs/research/astra-seven-basis-full-action-01/full-actions.npz')
        correction = load('outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz')
        reg = load('outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz')
        q, r, terminal = space['face_currents'], space['resistance_gram'], space['terminal_flux']
        assert q.shape == (12546, 17) and np.array_equal(q[:, :14], parent['face_currents'])
        assert np.linalg.eigvalsh(r).min() > 0.99
        joint = geometry.load_joint(); tets = joint['tetrahedra_m']
        local = joint['local_face_signs'][:, :, None]*q[joint['local_face_columns']]
        pa = np.r_[reg['pair_a'], correction['new_pair_a']]
        pb = np.r_[reg['pair_b'], correction['new_pair_b']]
        exact = np.r_[reg['reference_forward_reverse_h'][reg['pair_reference_index'], 0],
                      correction['reference_forward_reverse_h'][correction['new_pair_reference_index'], 0]]
        assert len(pa) == len(np.unique(pa*5304+pb)) == 150584 and np.all(pa < pb)
        moments = np.einsum('cin,cid->nd', local, tets.mean(1)[:, None]-tets)/3
        rank_term = 1e-7*moments@moments.T
        arrays.update(face_currents=q, resistance_gram=r, terminal_flux=terminal,
                      exact_integrated_currents=moments, minus_ik_coefficient=rank_term)
        for name in ('jacobi', 'xg31'):
            bary, weights = first[f'{name}_barycentric'], first[f'{name}_normalized_weights']
            pc = np.einsum('pi,cid->cpd', bary, tets)
            wl = (pc[:, :, None]-tets[:, None])*weights[None, :, None, None]/3
            wc = np.einsum('cin,cpid->cpnd', local, wl, optimize=True)
            point_pair = np.r_[first[f'{name}_pair_point_blocks'], correction[f'{name}_new_pair_point_blocks']]
            ln = local[:, :, 14:]
            near = np.einsum('cij,cjn->cin', reg['exact_cell_self_h']-first[f'{name}_self_point_blocks'], ln)
            np.add.at(near, pa, np.einsum('pij,pjn->pin', exact-point_pair, ln[pb]))
            np.add.at(near, pb, np.einsum('pji,pjn->pin', exact-point_pair, ln[pa]))
            near_action = scatter(joint, near)
            points, currents = pc.reshape(-1, 3), wc.reshape(-1, 17, 3)
            ids = np.unique(np.linspace(0, len(points)-1, 16, dtype=int))
            distances = np.linalg.norm(points[ids, None]-points[None], axis=2)
            threshold = np.ptp(points, axis=0).max()*np.finfo(float).eps
            inverse = np.divide(1., distances, out=np.zeros_like(distances), where=distances > threshold)
            direct = np.einsum('tp,pnd->tnd', inverse, currents[:, 14:])
            checked = np.full_like(direct, np.nan)
            new_action = np.full((12546, 3), np.nan)
            local_action = np.full((5304, 4, 3), np.nan)
            times = []
            arrays.update({f'{name}_new_near_action': near_action, f'{name}_direct_ids': ids,
                           f'{name}_direct_reference': direct, f'{name}_barycentric': bary,
                           f'{name}_normalized_weights': weights})
            for column in range(3):
                assert monotonic()-started < 285
                print(json.dumps(dict(stage='new_fmm_column_start', rule=name, column=column,
                                      old_columns_reused=14, owned_pid=os.getpid())), flush=True)
                before = monotonic()
                field = geometry.fmm3dpy.lfmm3d(eps=1e-10, sources=np.asfortranarray(points.T),
                    charges=np.asfortranarray(currents[:, 14+column].T), pg=1, nd=3)
                assert field.ier == 0 and np.isfinite(field.pot).all()
                potential = 4*np.pi*np.asarray(field.pot).T
                local_action[:, :, column] = 1e-7*np.einsum('cpid,cpd->ci', wl, potential.reshape(pc.shape))
                new_action[:, column] = scatter(joint, local_action[:, :, column, None])[:, 0]+near_action[:, column]
                direct_matrix = 1e-7*np.einsum('pnd,pd->n', currents, potential)
                replay = q.T@(new_action[:, column]-near_action[:, column])
                assert np.linalg.norm(replay-direct_matrix)/np.linalg.norm(direct_matrix) < 1e-10
                checked[:, column] = potential[ids]
                error = float(np.linalg.norm(checked[:, column]-direct[:, column])/np.linalg.norm(direct[:, column]))
                assert error < 1e-10
                times.append(monotonic()-before)
                arrays.update({f'{name}_new_full_action': new_action, f'{name}_new_local_point_action': local_action,
                               f'{name}_fmm_checks': checked, f'{name}_completed_new_columns': np.arange(column+1)})
                np.savez_compressed(out/'seventeen-actions.npz', **arrays)
                print(json.dumps(dict(stage='new_fmm_column_complete', rule=name, column=column,
                                      elapsed_s=times[-1], direct_relative=error)), flush=True)
                del field, potential
            full_action = np.c_[parent[f'{name}_full_action'], new_action]
            matrix = q.T@full_action
            cross = float(np.linalg.norm(matrix[:14, 14:]-matrix[14:, :14].T)/np.linalg.norm(matrix[14:, :14]))
            symmetry = float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix))
            assert max(cross, symmetry) < 1e-10
            assert np.linalg.norm(matrix[:14, :14]-parent[f'{name}_matrix'])/np.linalg.norm(matrix[:14, :14]) < 1e-12
            e = np.linalg.inv(np.linalg.cholesky((matrix+matrix.T)/2))
            arrays.update({f'{name}_full_action': full_action, f'{name}_matrix': matrix, f'{name}_energy_scale': e})
            rows = []
            for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
                omega = 2*np.pi*frequency
                z = r+1j*omega*matrix+omega**2/geometry.C0*rank_term
                response = np.linalg.solve(z, terminal.T)
                old = np.linalg.solve(z[:14, :14], terminal[:, :14].T)
                y, yo = terminal@response, terminal[:, :14]@old
                diff = response-np.r_[old, np.zeros((3, 4))]
                rn = lambda u: float(np.sqrt(np.trace(u.conj().T@r@u).real))
                rows.append(dict(frequency_hz=frequency, primary_band=frequency <= 1e8,
                    four_contact_current_r_change=rn(diff)/rn(response),
                    four_contact_admittance_relative=float(np.linalg.norm(y-yo)/np.linalg.norm(y))))
                arrays[f'{name}_four_contact_response_{int(frequency)}'] = response
            checks[name] = dict(new_column_s=times, independent_cross_reciprocity=cross,
                                raw_symmetry=symmetry, retained14_to17_diagnostics=rows)
        e = arrays['xg31_energy_scale']
        error = float(np.linalg.norm(e@(arrays['xg31_matrix']-arrays['jacobi_matrix'])@e.T, 2))
        checks.update(empirical_seventeen_energy_difference=error, empirical_rule_gate=error < 5e-5)
        status, failure = 'COMPLETED_SEVENTEEN_INCREMENTAL_ACTION', None
    except Exception:
        status, failure = 'STOP_SEVENTEEN_INCREMENTAL_ACTION', traceback.format_exc()
    np.savez_compressed(out/'seventeen-actions.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status=status, failure=failure,
        pins=PINS, driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'seventeen-actions.npz'),
        elapsed_s=monotonic()-started, checks=checks,
        scope='Three new closed-transport FMM columns; old14 and all5304self/150584pair replacements reused. '
        'Actual physical R cross retained. Local14-to17 conditional contact response only; no full closed-space '
        'or mesh convergence, charge/exterior/dielectric closure or board-accuracy claim.')
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
    raise SystemExit(guarded_source_worker(out, max_runtime_s=300, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]))
