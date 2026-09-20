"""SPD Decap PI Evaluator v0.23.1: prepared XG31/Jac27 geometry-group corrector.

This producer is intentionally unlaunched by preparation.  When invoked it
integrates only one rigid canonical pair per selected reuse group and projects
that directed block to every new member without averaging.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic
import traceback

import numpy as np
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_tetra_charge_green import measure
from qualify_astra_conforming_power_joint_sparse_current import local_mass


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/qualify_astra_tetra_volume_green.py": "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb",
    "tools/research/qualify_astra_tetra_charge_green.py": "aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9",
    "tools/research/qualify_astra_conforming_power_joint_sparse_current.py": "dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e",
    "tools/research/probe_astra_fmm3d_runtime.py": "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25",
    "outputs/research/astra-compact-rule-static-matrix-review-04/independent-review.json": "7731d8498f0a2d3e5d6bc337e622179304e2e53495fdd8969005f307643d41b8",
    "tools/research/correct_astra_leading_lift_touching_pairs.py": "f1c4ab70f7021eb6317e54defe8610d9c9f737f4a4e7a42c4432230b94e803b7",
    "tools/research/attribute_astra_xg7_jac27_pair_deltas.py": "b77a838d5a6a1dd2efffa8e693123ad87a9fd6004ddafae5e4b08cd7c2d7761c",
    "outputs/research/astra-xg7-jac27-pair-attribution-01/result.json": "5048d8eccfb2f6064ad5452dfb898ba447ae132cf055002ca5d21fd8c58a0c6a",
    "outputs/research/astra-xg7-jac27-pair-attribution-01/attribution.npz": "0ba81660b2df30571a8d00b8cd37794657c0493b99bd79268a970ef26dc03185",
    "outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz": "2b739de10382dbeb1cb327948d4d5f8b540cfc61944e92788ad8093f5af9de45",
    "outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz": "6cab19f0cf9e906e123f6aef8dfd39b5c82e4e496907ea85ead7f3f365d816dd",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz": "dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95",
}
DEADLINE_S = 240.0


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def rigid_map(reference, target):
    """Ordered eight-vertex proper rigid map, returned as row-vector R,t."""
    rc, tc = reference.mean(axis=0), target.mean(axis=0)
    x, y = reference - rc, target - tc
    u, _, vt = np.linalg.svd(x.T @ y)
    rot = u @ vt
    if np.linalg.det(rot) < 0:
        u[:, -1] *= -1
        rot = u @ vt
    mapped = reference @ rot + (tc - rc @ rot)
    scale = max(float(np.ptp(reference, axis=0).max()), 1e-300)
    return rot, tc - rc @ rot, float(np.max(np.linalg.norm(mapped - target, axis=1)) / scale)


def weighted_rule(tetra, local, bary, weights):
    points = np.einsum("pa,cad->cpd", bary, tetra, optimize=True)
    weighted = np.einsum("cim,cpid->cpmd", local, points[:, :, None] - tetra[:, None], optimize=True)
    return points, weighted * weights[None, :, None, None] / 3.0


def point_block(a, b, points, weighted):
    distance = np.linalg.norm(points[a, :, None] - points[b, None, :], axis=2)
    assert distance.min() > 0.0
    potential = np.einsum("ij,jnd->ind", 1.0 / distance, weighted[b], optimize=True)
    forward = 1e-7 * np.einsum("imd,ind->mn", weighted[a], potential, optimize=True)
    return forward + forward.T


def select_groups(labels, scores, limit):
    order = np.argsort(np.abs(scores))[::-1][:limit]
    assert len(np.unique(labels[order])) == limit and np.all(scores[order] > 0)
    return labels[order]


def run(output, reuse_qualified=False):
    started, arrays, records = monotonic(), {}, []
    pins = dict(PINS)
    if reuse_qualified:
        pins.update({
            'outputs/research/astra-xg7-jac27-group-correction-01/driver-at-run.py': '203cff94ea3c0c510eec1b400790663e6f966402f350402139d85e3be63eed2f',
            'outputs/research/astra-xg7-jac27-group-correction-01/result.json': '6ffa06a5fb454ae3f8e898055702dbea0cc7cb38cdeef80f3dd24b845dbe21ad',
            'outputs/research/astra-xg7-jac27-group-correction-01/partial-canonical-references.npz': '586dbe1d385e308a749a4463218067ae7bc24fbdb9f4d377323bd272d147784c',
        })
    assert output.is_dir() and not (output/'result.json').exists()
    assert digest(output/'driver-at-run.py') == digest(Path(__file__))
    try:
        for name, expected in pins.items():
            assert digest(ROOT / name) == expected, name
        with np.load(ROOT / "outputs/research/astra-xg7-jac27-pair-attribution-01/attribution.npz", allow_pickle=False) as d:
            ta, tb, tm, tl = d["touch_pair_a"], d["touch_pair_b"], d["touch_membership"], d["touch_group_label"]
            na, nb, nm, nl = d["nontouch_pair_a"], d["nontouch_pair_b"], d["nontouch_membership"], d["nontouch_group_label"]
            tg, ts, ng, ns = d["touch_group_id"], d["touch_group_score"], d["nontouch_group_id"], d["nontouch_group_score"]
            full_delta = d["full_delta"]
            touch_delta, non_delta = d['touch_pair_delta'], d['nontouch_pair_delta']
        selected_touch, selected_non = select_groups(tg, ts, 512), select_groups(ng, ns, 1024)
        selected_kind = np.r_[np.zeros(len(selected_touch), dtype=np.int8), np.ones(len(selected_non), dtype=np.int8)]
        selected_group = np.r_[selected_touch, selected_non]
        cache = {}
        if reuse_qualified:
            with np.load(ROOT/'outputs/research/astra-xg7-jac27-group-correction-01/partial-canonical-references.npz') as d:
                assert len(d['kind']) == 1536 and np.all(d['change'] < 5e-5) and np.all(d['reciprocity'] < 5e-5)
                assert np.isfinite(d['forward_reverse_h']).all()
                for i in range(len(d['kind'])):
                    cache[int(d['kind'][i]), int(d['group'][i])] = (int(d['a'][i]), int(d['b'][i]),
                        d['forward_reverse_h'][i], int(d['order'][i]), float(d['change'][i]), float(d['reciprocity'][i]))
            assert set(cache) == set(zip(map(int, selected_kind), map(int, selected_group)))
        with np.load(ROOT / "outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz", allow_pickle=False) as d:
            jac_l, jac_bary, jac_weights = d["jacobi_corrected_matrix"], d["jacobi_rule_barycentric"], d["jacobi_rule_weights"]
            corrected_a, corrected_b = d["corrected_pair_a"], d["corrected_pair_b"]
        with np.load(ROOT / "outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz", allow_pickle=False) as d:
            xg_l, xg_bary, xg_weights = d["xiao_gimbutas7_corrected_matrix"], d["xiao_gimbutas7_rule_barycentric"], d["xiao_gimbutas7_rule_weights"]
        with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as d:
            cells = d['cells'].astype(np.int64)
            tetra = (d["vertices_local_um"] * 1e-6)[cells]
        with np.load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz", allow_pickle=False) as d:
            columns, signs = d["local_rt0_face_columns"], d["local_rt0_face_signs"]
        with np.load(ROOT / "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz", allow_pickle=False) as d:
            currents = d["whitened_face_currents"]
        local = signs[:, :, None] * currents[columns]
        px, wx = weighted_rule(tetra, local, xg_bary, xg_weights)
        pj, wj = weighted_rule(tetra, local, jac_bary, jac_weights)
        corrected = {(int(a), int(b)) for a, b in zip(corrected_a, corrected_b)}
        # Full selected-member ledger, including historical members for rigid-map proof.
        member_kind = []; member_group = []; member_a = []; member_b = []; member_new = []; member_rot = []; member_t = []; member_fit = []
        canonical_kind = []; canonical_group = []; canonical_a = []; canonical_b = []; canonical_h = []; canonical_reverse = []; canonical_order = []; canonical_change = []; canonical_recip = []
        exact_updates = []; point_jac = []; point_xg = []; last_steps = []; last_full_h = []
        expected_selected_delta = np.zeros((5, 5))
        for kind, group in zip(selected_kind, selected_group, strict=True):
            labels, aa, bb, keep = (tl, ta, tb, tm) if kind == 0 else (nl, na, nb, nm)
            indices = np.flatnonzero(labels == group)
            new_indices = indices[keep[indices]]
            if not len(new_indices):
                continue
            reference_i = int(new_indices[0])
            ref_a, ref_b = int(aa[reference_i]), int(bb[reference_i])
            reference = np.r_[tetra[ref_a], tetra[ref_b]]
            expected_selected_delta += (touch_delta if kind == 0 else non_delta)[new_indices].sum(axis=0)
            for i in indices:
                assert monotonic()-started < DEADLINE_S
                a, b = int(aa[i]), int(bb[i])
                assert a < b and bool(keep[i]) == ((a, b) not in corrected)
                assert bool(np.intersect1d(cells[a], cells[b]).size) == (kind == 0)
                rot, shift, fit = rigid_map(reference, np.r_[tetra[a], tetra[b]])
                assert fit < 1e-12, (kind, int(group), a, b, fit)
                member_kind.append(kind); member_group.append(group); member_a.append(a); member_b.append(b)
                member_new.append(bool(keep[i])); member_rot.append(rot); member_t.append(shift); member_fit.append(fit)
            wa = np.linalg.inv(np.linalg.cholesky(local_mass(tetra[ref_a], measure(tetra[ref_a]))).T)
            wb = np.linalg.inv(np.linalg.cholesky(local_mass(tetra[ref_b], measure(tetra[ref_b]))).T)
            prior, h_prior, forward_prior, last_forward = None, None, None, np.zeros((4, 4))
            if reuse_qualified:
                ca, cb, saved_h, order, change, reciprocity = cache[int(kind), int(group)]
                assert (ca, cb) == (ref_a, ref_b) and order in (16, 32, 64)
                forward, reverse = saved_h
                # Final qualified H is reused. Only its lower previous order is
                # evaluated to recover the assembled last-step diagnostic.
                last_forward = forward-tetra_pair(tetra[ref_a], tetra[ref_b], order//2)
                forward_change = np.linalg.norm(wa.T @ last_forward @ wb)/np.linalg.norm(wa.T @ forward @ wb)
                assert forward_change <= change*(1+1e-8)+1e-12
            for order in ([] if reuse_qualified else [8, 16, 32, 64]):
                assert monotonic() - started < DEADLINE_S, "group corrector deadline"
                forward = tetra_pair(tetra[ref_a], tetra[ref_b], order)
                reverse = tetra_pair(tetra[ref_b], tetra[ref_a], order)
                scaled_forward, scaled_reverse = wa.T @ forward @ wb, wa.T @ reverse.T @ wb
                scaled = np.stack((scaled_forward, scaled_reverse))
                change = np.inf if prior is None else float(np.max(np.linalg.norm(scaled-prior, axis=(1, 2))/np.linalg.norm(scaled, axis=(1, 2))))
                reciprocity = float(np.linalg.norm(scaled_forward - scaled_reverse) / np.linalg.norm(scaled_forward))
                h = local[ref_a].T @ forward @ local[ref_b]
                if h_prior is not None:
                    last_forward = forward - forward_prior
                prior, h_prior, forward_prior = scaled, h + h.T, forward
                if max(change, reciprocity) < 5e-5:
                    break
            assert max(change, reciprocity) < 5e-5
            canonical_kind.append(kind); canonical_group.append(group); canonical_a.append(ref_a); canonical_b.append(ref_b)
            canonical_h.append(np.stack((forward, reverse))); canonical_order.append(order); canonical_change.append(change); canonical_recip.append(reciprocity)
            if len(canonical_h) % 32 == 0 or len(canonical_h) == len(selected_group):
                np.savez_compressed(output/'partial-canonical-references.npz', kind=canonical_kind, group=canonical_group,
                    a=canonical_a, b=canonical_b, forward_reverse_h=canonical_h, order=canonical_order,
                    change=canonical_change, reciprocity=canonical_recip)
                print(json.dumps(dict(stage='qualified_groups', count=len(canonical_h), elapsed_s=monotonic()-started)), flush=True)
            for i in new_indices:
                assert monotonic()-started < DEADLINE_S
                a, b = int(aa[i]), int(bb[i])
                # Ordered vertex map proves local index preservation; canonical H is
                # projected directly through actual local currents, reverse=transpose.
                projected_forward = local[a].T @ forward @ local[b]
                exact_updates.append(projected_forward + projected_forward.T)
                point_jac.append(point_block(a, b, pj, wj)); point_xg.append(point_block(a, b, px, wx))
                projected_last = local[a].T @ last_forward @ local[b]
                last_steps.append(projected_last + projected_last.T); last_full_h.append(last_forward)
                records.append({"kind": "touch" if kind == 0 else "nontouch", "group": int(group), "a": a, "b": b,
                                "canonical_a": ref_a, "canonical_b": ref_b, "order": int(order), "relative_change": change,
                                "relative_reciprocity": reciprocity})
        exact_updates, point_jac, point_xg, last_steps, last_full_h = map(np.asarray, (exact_updates, point_jac, point_xg, last_steps, last_full_h))
        new_pairs = [(r['a'], r['b']) for r in records]
        assert len(new_pairs) == len(set(new_pairs)) and not (set(new_pairs) & corrected)
        attribution_replay = float(np.linalg.norm((point_xg-point_jac).sum(axis=0)-expected_selected_delta)/np.linalg.norm(expected_selected_delta))
        assert attribution_replay < 1e-8
        corrected_jac = jac_l - point_jac.sum(axis=0) + exact_updates.sum(axis=0)
        corrected_xg = xg_l - point_xg.sum(axis=0) + exact_updates.sum(axis=0)
        signed_delta = corrected_xg - corrected_jac
        replay_absolute = float(np.linalg.norm(signed_delta-(full_delta-(point_xg-point_jac).sum(axis=0))))
        # This is arithmetic consistency of differences between nearly equal
        # full matrices; normalizing by the tiny difference demands unavailable
        # extra floating-point digits. Physical rule accuracy is gated separately.
        replay = replay_absolute/max(np.linalg.norm(xg_l), np.linalg.norm(jac_l))
        replay_difference_relative = replay_absolute/max(np.linalg.norm(full_delta), 1e-300)
        assert replay < 1e-12
        fixed = np.linalg.inv(np.linalg.cholesky((jac_l + jac_l.T) / 2.0))
        updated = np.linalg.inv(np.linalg.cholesky((corrected_xg+corrected_xg.T)/2))
        updated_error = float(np.linalg.norm(updated @ signed_delta @ updated.T, 2))
        sym = float(np.linalg.norm(corrected_xg - corrected_xg.T) / np.linalg.norm(corrected_xg))
        pd = float(np.linalg.eigvalsh((corrected_xg + corrected_xg.T) / 2.0).min())
        jac_sym = float(np.linalg.norm(corrected_jac-corrected_jac.T)/np.linalg.norm(corrected_jac))
        jac_pd = float(np.linalg.eigvalsh((corrected_jac+corrected_jac.T)/2).min())
        assert max(sym, jac_sym) < 1e-10 and min(pd, jac_pd) > 0
        arrays = dict(selected_kind=selected_kind, selected_group=selected_group, member_kind=np.asarray(member_kind), member_group=np.asarray(member_group),
                      member_a=np.asarray(member_a), member_b=np.asarray(member_b), member_is_new=np.asarray(member_new), member_rotation=np.asarray(member_rot),
                      member_translation=np.asarray(member_t), member_rigid_relative=np.asarray(member_fit), canonical_kind=np.asarray(canonical_kind),
                      canonical_group=np.asarray(canonical_group), canonical_a=np.asarray(canonical_a), canonical_b=np.asarray(canonical_b),
                      canonical_forward_reverse_h=np.asarray(canonical_h), canonical_order=np.asarray(canonical_order), canonical_change=np.asarray(canonical_change),
                      canonical_reciprocity=np.asarray(canonical_recip), projected_exact_blocks=exact_updates, point_pair_jac27=point_jac, point_pair_xg31=point_xg,
                      last_step_full_forward_h=last_full_h, last_step_projected_change=last_steps, corrected_jac27_matrix=corrected_jac, corrected_xg31_matrix=corrected_xg,
                      signed_delta=signed_delta, fixed_jac_scale=fixed, expected_selected_delta=expected_selected_delta,
                      updated_xg31_scale=updated, prior_jac27_matrix=jac_l, prior_xg31_matrix=xg_l)
        metrics = {"selected_touch_groups": int(len(selected_touch)), "selected_nontouch_groups": int(len(selected_non)), "new_member_count": int(len(records)),
                   "all_member_count": int(len(member_a)), "rigid_map_max_relative": float(max(member_fit)), "replay_relative": replay,
                   "arithmetic_replay_absolute": replay_absolute, "arithmetic_replay_difference_relative": replay_difference_relative,
                   "reused_qualified_representatives": len(cache),
                   "signed_delta_frobenius": float(np.linalg.norm(signed_delta)), "signed_delta_fixed_jac_spectral": float(np.linalg.norm(fixed @ signed_delta @ fixed.T, 2)),
                   "signed_delta_fixed_jac_frobenius": float(np.linalg.norm(fixed @ signed_delta @ fixed.T)),
                   "signed_delta_updated_xg_spectral": updated_error, "global_rule_difference_gate_passed": bool(updated_error < 5e-5),
                   "attribution_point_delta_replay_relative": attribution_replay,
                   "jac_raw_symmetry": jac_sym, "jac_pd_min_eigenvalue": jac_pd,
                   "corrected_raw_symmetry": sym, "corrected_pd_min_eigenvalue": pd, "last_step_l1_fixed_jac": float(sum(np.linalg.norm(fixed @ x @ fixed.T, 2) for x in last_steps)),
                   "maximum_local_change": float(max(canonical_change)), "maximum_local_reciprocity": float(max(canonical_recip))}
        status, failure = "QUALIFIED_XG7_JAC27_GROUP_CORRECTIONS", None
    except Exception:
        status, failure, metrics = "STOP_XG7_JAC27_GROUP_CORRECTOR", traceback.format_exc(), {}
    artifact = output / "group-correction.npz"
    np.savez_compressed(artifact, **arrays)
    (output / "pair-records.json").write_text(json.dumps(records, indent=2, allow_nan=False), encoding="utf-8")
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": status, "failure": failure, "pins": pins,
              "metrics": metrics, "elapsed_s": monotonic() - started, "driver_sha256": digest(Path(__file__)), "artifact_sha256": digest(artifact),
              "records_sha256": digest(output / "pair-records.json"), "budget": {"deadline_s": DEADLINE_S, "memory_gib": 24, "external_receipt": "external-budget.json"},
              "scope": "Groupwise local vector-pair correction only. Baseline already carries the prior17324 common exact corrections; new selected members subtract their own Jac27/XG31 point blocks and add one projected exact block. Rule difference is empirical and does not prove an absolute integral bound. No FMM, scalar, charge, field, port, or board claim."}
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False), flush=True)
    return 0 if failure is None else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--reuse-qualified', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.worker:
        raise SystemExit(run(out, args.reuse_qualified))
    assert digest(ROOT/'tools/research/probe_astra_fmm3d_runtime.py') == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=DEADLINE_S,
        worker_command=[sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]+(['--reuse-qualified'] if args.reuse_qualified else [])))
