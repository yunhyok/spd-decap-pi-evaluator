"""SPD Decap PI Evaluator v0.23.1: saved XG31-minus-Jac27 pair attribution (no FMM)."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-lift-touching-pair-errors-01/result.json": "c1845a594881b7b8f870db4610a02ffac0623442ff3109edd427db55073c3c65",
    "outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz": "0288c88834314667fb48c993a148bd57974ea1ed9db6bcc069ace252854d8ebc",
    "outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz": "0fb2f99be551708ca53fcb546ad56468ce6736f97f202358f8ddaa349342c939",
    "outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz": "726a49b8acd83d496f9d3bd9a864aabdd5bef9655f206fe46e00454ed3d17113",
    "outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz": "2b739de10382dbeb1cb327948d4d5f8b540cfc61944e92788ad8093f5af9de45",
    "outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz": "6cab19f0cf9e906e123f6aef8dfd39b5c82e4e496907ea85ead7f3f365d816dd",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz": "dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95",
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def weighted_rule(tetra, local, bary, weights):
    """Physical quadrature weights times affine RT0 current, shape cell,p,m,xyz."""
    points = np.einsum("pa,cad->cpd", bary, tetra, optimize=True)
    return points, np.einsum("cim,cpid->cpmd", local, points[:, :, None] - tetra[:, None], optimize=True) * weights[None, :, None, None] / 3.0


def pair_blocks(pair_a, pair_b, points, weighted, chunk=64):
    out = np.empty((len(pair_a), 5, 5), dtype=np.float64)
    for first in range(0, len(pair_a), chunk):
        sl = slice(first, min(first + chunk, len(pair_a)))
        a, b = pair_a[sl], pair_b[sl]
        distance = np.linalg.norm(points[a, :, None, :] - points[b, None, :, :], axis=3)
        if not np.all(distance > 0.0):
            raise AssertionError("coincident points in non-self pair block")
        inverse = 1.0 / distance
        potential = np.einsum("bpq,bqnd->bpnd", inverse, weighted[b], optimize=True)
        forward = 1e-7 * np.einsum("bpmd,bpnd->bmn", weighted[a], potential, optimize=True)
        out[sl] = forward + forward.transpose(0, 2, 1)
    return out


def group_summary(blocks, labels, fixed_scale):
    unique, inverse = np.unique(labels, return_inverse=True)
    grouped = np.zeros((len(unique), 5, 5))
    np.add.at(grouped, inverse, blocks)
    scores = np.linalg.norm(np.einsum("ab,gbc,dc->gad", fixed_scale, grouped, fixed_scale, optimize=True), axis=(1, 2))
    order = np.argsort(scores)[::-1]
    total = float(scores.sum())
    cumulative = np.cumsum(scores[order]) / max(total, 1e-300)
    return unique, grouped, scores, order, {"group90": int(np.searchsorted(cumulative, .90) + 1), "group99": int(np.searchsorted(cumulative, .99) + 1), "score_l1": total}


def run(output):
    start, arrays = monotonic(), {}
    output.mkdir(parents=False, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for name, expected in PINS.items():
            assert digest(ROOT / name) == expected, name
        with np.load(ROOT / "outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz", allow_pickle=False) as d:
            touch_a, touch_b = d["pair_source_cell"], d["pair_observer_cell"]
        with np.load(ROOT / "outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz", allow_pickle=False) as d:
            non_a, non_b = d["pair_cell_a"], d["pair_cell_b"]
        with np.load(ROOT / "outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz", allow_pickle=False) as d:
            touch_labels, non_labels = d["touch_all_pair_group"], d["nontouch_all_pair_group"]
        with np.load(ROOT / "outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz", allow_pickle=False) as d:
            jac_l = d["jacobi_corrected_matrix"]
            jac_bary, jac_weight = d["jacobi_rule_barycentric"], d["jacobi_rule_weights"]
            corrected_a, corrected_b = d["corrected_pair_a"], d["corrected_pair_b"]
        with np.load(ROOT / "outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz", allow_pickle=False) as d:
            xg_l = d["xiao_gimbutas7_corrected_matrix"]
            xg_bary, xg_weight = d["xiao_gimbutas7_rule_barycentric"], d["xiao_gimbutas7_rule_weights"]
        with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as d:
            tetra = (d["vertices_local_um"] * 1e-6)[d["cells"].astype(np.int64)]
        with np.load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz", allow_pickle=False) as d:
            columns, signs = d["local_rt0_face_columns"], d["local_rt0_face_signs"]
        with np.load(ROOT / "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz", allow_pickle=False) as d:
            currents = d["whitened_face_currents"]
        assert len(touch_a) == len(touch_labels) == 416525 and len(non_a) == len(non_labels) == 257477
        assert jac_bary.shape == (27, 4) and xg_bary.shape == (31, 4)
        assert abs(jac_weight.sum() - 1) < 2e-14 and abs(xg_weight.sum() - 1) < 2e-14
        full_delta = xg_l - jac_l
        fixed_scale = np.linalg.inv(np.linalg.cholesky((jac_l + jac_l.T) / 2.0))
        local = signs[:, :, None] * currents[columns]
        px, wx = weighted_rule(tetra, local, xg_bary, xg_weight)
        pj, wj = weighted_rule(tetra, local, jac_bary, jac_weight)
        corrected = set((int(a), int(b)) for a, b in zip(corrected_a, corrected_b))
        touch_corrected = np.fromiter(((int(a), int(b)) in corrected for a, b in zip(touch_a, touch_b)), bool, len(touch_a))
        non_corrected = np.fromiter(((int(a), int(b)) in corrected for a, b in zip(non_a, non_b)), bool, len(non_a))
        assert int(touch_corrected.sum() + non_corrected.sum()) == 17324
        keep_touch, keep_non = ~touch_corrected, ~non_corrected
        # Point contributions for corrected pairs cancel from fullDelta because
        # each compact matrix replaces precisely those entries by the same exact block.
        td = np.zeros((len(touch_a), 5, 5)); nd = np.zeros((len(non_a), 5, 5))
        ti, ni = np.flatnonzero(keep_touch), np.flatnonzero(keep_non)
        td[ti] = pair_blocks(touch_a[ti], touch_b[ti], px, wx) - pair_blocks(touch_a[ti], touch_b[ti], pj, wj)
        nd[ni] = pair_blocks(non_a[ni], non_b[ni], px, wx) - pair_blocks(non_a[ni], non_b[ni], pj, wj)
        touch_sum, non_sum = td.sum(axis=0), nd.sum(axis=0)
        outside = full_delta - touch_sum - non_sum
        reconstruction = float(np.linalg.norm(full_delta - (touch_sum + non_sum + outside)) / max(np.linalg.norm(full_delta), 1e-300))
        assert reconstruction <= 8 * np.finfo(float).eps
        tg, tgb, ts, to, tm = group_summary(td, touch_labels, fixed_scale)
        ng, ngb, ns, no, nm = group_summary(nd, non_labels, fixed_scale)
        arrays = dict(full_delta=full_delta, fixed_energy_scale=fixed_scale, touch_pair_a=touch_a, touch_pair_b=touch_b,
                      nontouch_pair_a=non_a, nontouch_pair_b=non_b, touch_membership=keep_touch, nontouch_membership=keep_non,
                      touch_group_label=touch_labels, nontouch_group_label=non_labels, touch_pair_delta=td, nontouch_pair_delta=nd,
                      touch_sum=touch_sum, nontouch_sum=non_sum, outside_sum=outside, touch_group_id=tg, nontouch_group_id=ng,
                      touch_group_delta=tgb, nontouch_group_delta=ngb, touch_group_score=ts, nontouch_group_score=ns,
                      touch_group_order=to, nontouch_group_order=no)
        metrics = {"touch_pairs": int(len(touch_a)), "nontouch_pairs": int(len(non_a)), "corrected_removed": int((~keep_touch).sum() + (~keep_non).sum()),
                   "reconstruction_relative": reconstruction, "fixed_full_delta_norm": float(np.linalg.norm(fixed_scale @ full_delta @ fixed_scale.T)),
                   "fixed_touch_norm": float(np.linalg.norm(fixed_scale @ touch_sum @ fixed_scale.T)), "fixed_nontouch_norm": float(np.linalg.norm(fixed_scale @ non_sum @ fixed_scale.T)),
                   "fixed_outside_norm": float(np.linalg.norm(fixed_scale @ outside @ fixed_scale.T)),
                   "touch": tm, "nontouch": nm,
                   "touch_top20": [{"group": int(tg[i]), "pair_count": int((touch_labels == tg[i]).sum()), "score": float(ts[i])} for i in to[:20]],
                   "nontouch_top20": [{"group": int(ng[i]), "pair_count": int((non_labels == ng[i]).sum()), "score": float(ns[i])} for i in no[:20]]}
        status, failure = "PASS_XG7_JAC27_PAIR_ATTRIBUTION", None
    except Exception:
        metrics, status, failure = {}, "STOP_XG7_JAC27_PAIR_ATTRIBUTION", traceback.format_exc()
    np.savez_compressed(output / "attribution.npz", **arrays)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": status, "failure": failure,
              "pins": PINS, "metrics": metrics, "elapsed_s": monotonic() - start, "driver_sha256": digest(Path(__file__)),
              "artifact_sha256": digest(output / "attribution.npz"),
              "scope": "Saved affine RT0 point-rule XG31-minus-Jac27 attribution only. Corrected near-pair point contributions are excluded because their common exact correction cancels. No FMM, exact Green, scalar subtraction, field, or board claim."}
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False), flush=True)
    return 0 if failure is None else 2


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    raise SystemExit(run(p.parse_args().output.resolve()))
