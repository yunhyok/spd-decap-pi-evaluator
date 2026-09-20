"""SPD Decap PI Evaluator v0.23.1: targeted vertical-current near correction."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic
import traceback

import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import splu
from correct_astra_xg7_jac27_group_pairs import rigid_map
from qualify_astra_conforming_power_joint_sparse_current import local_mass
from qualify_astra_tetra_volume_green import tetra_pair
from probe_astra_seven_basis_full_action import point_blocks, scatter

ROOT = geometry.ROOT
PINS = {
    'tools/research/probe_astra_seven_basis_full_action.py': 'cee40c743a43ecd25ca80532e30ad2f2a7edfa121d5484d9183990a2f83e92ca',
    'tools/research/correct_astra_xg7_jac27_group_pairs.py': '05b0de992fba40dc8e2b2148795ba12103093d2279fcbeb056e76d505c5119c5',
    'tools/research/qualify_astra_tetra_volume_green.py': '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    'tools/research/qualify_astra_tetra_charge_green.py': 'aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9',
    'tools/research/qualify_astra_conforming_power_joint_sparse_current.py': 'dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e',
    'tools/research/probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
    'outputs/research/astra-seven-basis-full-action-review-02/independent-review.json': '8f60e377071dd4b9a8af056716accf6993d99fdc2a079097ed75cd4410f190eb',
    'outputs/research/astra-seven-basis-full-action-01/result.json': '0c7305addd7251b33f794a8cdab5cf9ab1908826e5edc7bc34f4d43f53abc5ef',
    'outputs/research/astra-seven-basis-full-action-01/full-actions.npz': '13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b',
    'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz': '29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933',
    'outputs/research/astra-seven-xg31-jac27-pair-attribution-01/result.json': '376117c9c9b64dedcb3f6d7c1f15b3c31b495d1aed24c8690c1665cfa33ed303',
    'outputs/research/astra-seven-xg31-jac27-pair-attribution-01/attribution.npz': '8a21bcd0a08a57537930af01cce954b8ba5d01a09c0f85f95d7ced09318dbf02',
}


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def load(path):
    with np.load(ROOT/path) as z:
        return {key: z[key] for key in z.files}


def run(out):
    started, arrays = monotonic(), {}
    deadline = started+230
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha(ROOT/path) == expected, path
        base = load('outputs/research/astra-seven-basis-full-action-01/full-actions.npz')
        attributed = load('outputs/research/astra-seven-xg31-jac27-pair-attribution-01/attribution.npz')
        registry = load('outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz')
        joint = geometry.load_joint()
        tets, volumes, cells = joint['tetrahedra_m'], joint['cell_volumes_m3'], joint['cells']
        q = base['face_currents']
        local = joint['local_face_signs'][:, :, None]*q[joint['local_face_columns']]
        matrix = base['xg31_matrix']
        energy = np.linalg.inv(np.linalg.cholesky((matrix+matrix.T)/2))
        scores = np.linalg.norm(np.einsum('ab,pbc,dc->pad', energy, attributed['touch_group_delta'], energy), axis=(1, 2))
        ranking = np.argsort(scores)[::-1]
        residual = attributed['full_delta'].copy()
        assert np.linalg.norm(residual-(base['xg31_matrix']-base['jacobi_matrix'])) == 0
        for count, index in enumerate(ranking, 1):
            residual -= attributed['touch_group_delta'][index]
            if np.linalg.norm(energy@residual@energy.T, 2) < 2e-5:
                break
        assert count == 1040, 'frozen evidence-based group selection'
        selection = ranking[:count]
        groups = attributed['touch_group_id'][selection]
        aa, bb, labels, keep = [attributed[k] for k in ('touch_pair_a', 'touch_pair_b', 'touch_group', 'touch_keep')]
        assert len(aa) == 416525 and len(attributed['nontouch_pair_a']) == 257477
        old_ids = registry['pair_a']*5304+registry['pair_b']
        assert np.array_equal(keep, ~np.isin(aa*5304+bb, old_ids))
        ca, cb, hs, orders, changes, recips, steps = [], [], [], [], [], [], []
        pa, pb, ref_ids, fits = [], [], [], []
        predicted_selected = attributed['touch_group_delta'][selection].sum(0)
        for reference_index, group in enumerate(groups):
            assert monotonic() < deadline
            members = np.flatnonzero((labels == group)&keep)
            assert len(members) > 0
            a, b = int(aa[members[0]]), int(bb[members[0]])
            canonical = np.r_[tets[a], tets[b]]
            for index in members:
                m, n = int(aa[index]), int(bb[index])
                assert m < n and np.intersect1d(cells[m], cells[n]).size > 0
                rotation, translation, fit = rigid_map(canonical, np.r_[tets[m], tets[n]])
                assert fit < 1e-12
                pa.append(m); pb.append(n); ref_ids.append(reference_index); fits.append(fit)
            wa = np.linalg.inv(np.linalg.cholesky(local_mass(tets[a], volumes[a])).T)
            wb = np.linalg.inv(np.linalg.cholesky(local_mass(tets[b], volumes[b])).T)
            previous, previous_forward = None, None
            for order in (8, 16, 32, 64):
                assert monotonic() < deadline
                forward, reverse = tetra_pair(tets[a], tets[b], order), tetra_pair(tets[b], tets[a], order)
                scaled = np.stack((wa.T@forward@wb, wa.T@reverse.T@wb))
                change = np.inf if previous is None else float(np.max(np.linalg.norm(scaled-previous, axis=(1, 2))/np.linalg.norm(scaled, axis=(1, 2))))
                reciprocity = float(np.linalg.norm(scaled[0]-scaled[1])/np.linalg.norm(scaled[0]))
                if max(change, reciprocity) < 5e-5:
                    break
                previous, previous_forward = scaled, forward
            assert max(change, reciprocity) < 5e-5 and previous_forward is not None
            ca.append(a); cb.append(b); hs.append(np.stack((forward, reverse)))
            orders.append(order); changes.append(change); recips.append(reciprocity); steps.append(forward-previous_forward)
            if len(hs)%32 == 0 or len(hs) == count:
                np.savez_compressed(out/'partial-references.npz', groups=groups[:len(hs)],
                    reference_a=ca, reference_b=cb, forward_reverse_h=hs, order=orders,
                    change=changes, reciprocity=recips, last_forward_change=steps)
                print(json.dumps(dict(stage='qualified_new_touch_groups', completed=len(hs), total=count,
                                      elapsed_s=monotonic()-started)), flush=True)
        pa, pb, ref_ids, hs, steps = map(np.asarray, (pa, pb, ref_ids, hs, steps))
        assert len(pa) == len(np.unique(pa*5304+pb)) == 92332
        assert not np.any(np.isin(pa*5304+pb, old_ids))
        exact = hs[ref_ids, 0]
        arrays.update(face_currents=q, selected_group_ids=groups, reference_a=ca, reference_b=cb,
                      reference_forward_reverse_h=hs, reference_order=orders, reference_change=changes,
                      reference_reciprocity=recips, reference_last_forward_change=steps,
                      new_pair_a=pa, new_pair_b=pb, new_pair_reference_index=ref_ids,
                      ordered_rigid_fit_relative=fits, predicted_selected_delta=predicted_selected,
                      selection_fixed_xg_energy_scale=energy, predicted_remaining_delta=residual)
        projected_points, history = {}, []
        for name in ('jacobi', 'xg31'):
            bary, weights = base[f'{name}_barycentric'], base[f'{name}_normalized_weights']
            points = np.einsum('pi,cid->cpd', bary, tets)
            weighted_local = (points[:, :, None]-tets[:, None])*weights[None, :, None, None]/3
            threshold = np.ptp(points.reshape(-1, 3), axis=0).max()*np.finfo(float).eps
            point = point_blocks(points, weighted_local, pa, pb, threshold, deadline)
            local_update = np.zeros((5304, 4, 7))
            np.add.at(local_update, pa, np.einsum('pij,pjn->pin', exact-point, local[pb]))
            np.add.at(local_update, pb, np.einsum('pji,pjn->pin', exact-point, local[pa]))
            update = scatter(joint, local_update)
            action = base[f'{name}_full_action']+update
            matrix = q.T@action
            symmetry = float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix))
            assert symmetry < 1e-10
            np.linalg.cholesky((matrix+matrix.T)/2)
            projected = np.einsum('pim,pij,pjn->mn', local[pa], point, local[pb])
            projected_points[name] = projected+projected.T
            arrays.update({f'{name}_new_pair_point_blocks': point, f'{name}_full_action': action,
                           f'{name}_matrix': matrix, f'{name}_new_pair_action_update': update})
            history.append(dict(rule=name, raw_symmetry_relative=symmetry))
        replay = float(np.linalg.norm(projected_points['xg31']-projected_points['jacobi']-predicted_selected)/np.linalg.norm(predicted_selected))
        assert replay < 1e-8
        xg, jac = arrays['xg31_matrix'], arrays['jacobi_matrix']
        updated = np.linalg.inv(np.linalg.cholesky((xg+xg.T)/2))
        error = float(np.linalg.norm(updated@(xg-jac)@updated.T, 2))
        expected_delta = attributed['full_delta']-predicted_selected
        arithmetic = float(np.linalg.norm((xg-jac)-expected_delta)/np.linalg.norm(xg))
        assert arithmetic < 1e-12
        last = np.einsum('pim,pij,pjn->pmn', local[pa], steps[ref_ids], local[pb])
        last += last.transpose(0, 2, 1).copy()
        last_l1 = float(np.linalg.norm(np.einsum('ab,pbc,dc->pad', updated, last, updated), ord=2, axis=(1, 2)).sum())
        with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:
            r = csr_matrix((z['resistance_data_ohm'], z['mass_col'], z['mass_row_ptr']), shape=tuple(z['mass_shape']))
        factor = splu(r.tocsc())
        dx = arrays['xg31_full_action']-arrays['jacobi_full_action']
        ad = dx.T@factor.solve(dx)
        reference = arrays['xg31_full_action']
        ar = reference.T@factor.solve(reference)
        metric = np.linalg.inv(np.linalg.cholesky((ar+ar.T)/2))
        fine_error = float(np.sqrt(np.linalg.norm(metric@ad@metric.T, 2)))
        arrays.update(updated_xg_energy_scale=updated, last_refinement_projected_pair_change=last,
                      full_action_delta_r_dual_gram=ad, full_action_reference_r_dual_gram=ar)
        metrics = dict(selected_touch_groups=count, new_pairs=len(pa), total_corrected_pairs=len(pa)+len(old_ids),
                       maximum_rigid_fit=max(fits), maximum_local_two_direction_change=max(changes),
                       maximum_local_reciprocity=max(recips), order_counts={str(o): orders.count(o) for o in sorted(set(orders))},
                       attribution_projected_point_replay_relative=replay, full_matrix_delta_replay_relative=arithmetic,
                       empirical_updated_seven_energy_difference=error, empirical_seven_rule_gate=error < 5e-5,
                       unrestricted_full_action_r_dual_difference=fine_error,
                       all_member_last_refinement_l1_updated_energy=last_l1)
        status, failure = 'COMPLETED_TARGETED_SEVEN_TOUCH_GROUP_CORRECTION', None
    except Exception:
        status, failure, metrics, history = 'STOP_TARGETED_SEVEN_TOUCH_GROUP_CORRECTION', traceback.format_exc(), {}, []
    np.savez_compressed(out/'corrected-actions.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status=status, failure=failure,
                  driver_sha256=sha(Path(__file__)), pins=PINS, artifact_sha256=sha(out/'corrected-actions.npz'),
                  elapsed_s=monotonic()-started, metrics=metrics, history=history,
                  scope='1040 evidence-ranked touching geometry groups replace92332 NEW point pairs in both saved12546-row actions. '
                  'All prior58252 pair/self corrections and every remaining interaction retained. Full-affine raw blocks qualify both directions. '
                  'Seven-current two-rule agreement and last-refinement sums are empirical diagnostics, not absolute Green/fine-space/field/board error bounds. '
                  'No FMM, source parser, scalar, charge, global return, port or board solve.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if failure is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.worker:
        raise SystemExit(run(out))
    assert sha(ROOT/'tools/research/probe_astra_fmm3d_runtime.py') == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=240, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]))
