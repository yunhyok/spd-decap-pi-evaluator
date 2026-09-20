"""Strict review of the saved 1,934-pair non-touch static correction.

The review independently rebuilds q2/q3 point-pair subtraction, RT0 projection,
the touching-corrected baseline composition, and eight doubled-order pair
integrals.  It executes no FMM or field solve and does not re-integrate all
1,934 pairs.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from numpy.polynomial.legendre import leggauss

from qualify_astra_tetra_volume_green import tetra_pair


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-leading-lift-nontouch-correction-review-04"
PINS = {
    "outputs/research/astra-leading-lift-nontouch-correction-01/driver-at-run.py":
        "f1c4ab70f7021eb6317e54defe8610d9c9f737f4a4e7a42c4432230b94e803b7",
    "outputs/research/astra-leading-lift-nontouch-correction-01/result.json":
        "921693f63bf1b88837b5ccebcee26571daa54bd1fefc9d2ac7fa896bfd10fb3b",
    "outputs/research/astra-leading-lift-nontouch-correction-01/pair-correction.npz":
        "98fa35e653acc04d618775c953168604c00836427a3e6f2689d22018107208c9",
    "outputs/research/astra-leading-lift-nontouch-correction-01/pair-records.json":
        "c8b4984f5ba0076c56efbaf7841bc3d254fb873c0f1c21f293243b3caa9e29ed",
    "outputs/research/astra-leading-lift-touching-correction-02/result.json":
        "8f9b6611f3e7c5b79caa592401a138ace72dbca26ee32248879f49f77a2d0fe7",
    "outputs/research/astra-leading-lift-touching-correction-02/pair-correction.npz":
        "7c89bcafe7ab81915289ec9eb548c57c6e18429ea15ac6c3317f4f0d2666d27d",
    "outputs/research/astra-lift-nontouch-knn-attribution-review-03/independent-review.json":
        "1a89ccc33759d657db51969ddea8c4ef3990c018a047bacdcc22b3dfebceccbc",
    "outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz":
        "0fb2f99be551708ca53fcb546ad56468ce6736f97f202358f8ddaa349342c939",
    "outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz":
        "0288c88834314667fb48c993a148bd57974ea1ed9db6bcc069ace252854d8ebc",
    "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz":
        "dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "tools/research/qualify_astra_tetra_volume_green.py":
        "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb",
}
GATES = {
    "saved_array_relative": 3.0e-13,
    "composition_relative": 3.0e-13,
    "reported_metric_relative": 3.0e-13,
    "pair_local_change": 5.0e-5,
    "pair_local_reciprocity": 5.0e-5,
    "doubled_order_change": 5.0e-5,
    "doubled_order_reciprocity": 5.0e-5,
    "doubled_order_assembled_energy_scale": 5.0e-5,
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def relative(actual: np.ndarray, expected: np.ndarray) -> float:
    return float(np.linalg.norm(actual - expected) /
                 max(float(np.linalg.norm(expected)), np.finfo(float).tiny))


def normalized_tetra_rule(tetrahedra: np.ndarray, order: int,
                          signed_flux: np.ndarray):
    x, w = leggauss(order)
    x = (x + 1.0) / 2.0
    w = w / 2.0
    u, v, z = np.meshgrid(x, x, x, indexing="ij")
    barycentric = np.column_stack((
        ((1.0-u)*(1.0-v)*(1.0-z)).ravel(), u.ravel(),
        ((1.0-u)*v).ravel(), ((1.0-u)*(1.0-v)*z).ravel(),
    ))
    weight = (w[:, None, None] * w[None, :, None] * w[None, None, :] *
              (1.0-u)**2 * (1.0-v)).ravel() * 6.0
    volume = np.abs(np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])) / 6.0
    points = np.einsum("qi,cid->cqd", barycentric, tetrahedra)
    basis = (points[:, :, None, :] - tetrahedra[:, None, :, :]) / (
        3.0 * volume[:, None, None, None]
    )
    current = np.einsum("cim,cqid->cqmd", signed_flux, basis)
    weighted = current * (volume[:, None, None, None] * weight[None, :, None, None])
    return points, weighted


def point_pair(a: int, b: int, rule) -> np.ndarray:
    points, weighted = rule
    distance = np.linalg.norm(points[a, :, None, :] - points[b, None, :, :], axis=2)
    if float(distance.min()) <= 0.0:
        raise AssertionError("non-touch point rule contains a coincidence")
    potential = np.einsum("ij,jnd->ind", 1.0 / distance, weighted[b], optimize=True)
    directed = 1.0e-7 * np.einsum("imd,ind->mn", weighted[a], potential, optimize=True)
    return directed + directed.T


def local_mass(tetrahedron: np.ndarray) -> np.ndarray:
    center = tetrahedron.mean(axis=0)
    volume = abs(float(np.linalg.det(tetrahedron[1:] - tetrahedron[:1]))) / 6.0
    variance = float(np.sum((tetrahedron - center) ** 2)) / 20.0
    offsets = center[None, :] - tetrahedron
    return (variance + offsets @ offsets.T) / (9.0 * volume)


def run() -> None:
    started = monotonic()
    if OUT.exists():
        raise FileExistsError(OUT)
    OUT.mkdir(parents=True)
    (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for path, expected in PINS.items():
            actual = digest(ROOT / path)
            if actual != expected:
                raise AssertionError(f"pin mismatch {path}: {actual}")

        source = ROOT / "outputs/research/astra-leading-lift-nontouch-correction-01"
        result = json.loads((source / "result.json").read_text(encoding="utf-8"))
        records = json.loads((source / "pair-records.json").read_text(encoding="utf-8"))
        with np.load(source / "pair-correction.npz", allow_pickle=False) as z:
            saved = {key: z[key].copy() for key in z.files}
        with np.load(ROOT / "outputs/research/astra-leading-lift-touching-correction-02/pair-correction.npz", allow_pickle=False) as z:
            baseline = {q: z[f"corrected_matrix_q{q}"].copy() for q in (2, 3)}
        with np.load(ROOT / "outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz", allow_pickle=False) as z:
            ranked_a = z["pair_cell_a"][z["descending_pair_indices"]]
            ranked_b = z["pair_cell_b"][z["descending_pair_indices"]]
            ranked_delta = z["pair_q3_minus_q2"][z["descending_pair_indices"]]
            rank_scale = z["baseline_energy_scaling"].copy()
        with np.load(ROOT / "outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz", allow_pickle=False) as z:
            touching = {tuple(sorted((int(a), int(b)))) for a, b in
                        zip(z["pair_source_cell"], z["pair_observer_cell"], strict=True)}
        with np.load(ROOT / "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz", allow_pickle=False) as z:
            face_currents = z["whitened_face_currents"].copy()
        with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as z:
            tetrahedra = z["vertices_local_um"][z["cells"]] * 1.0e-6
            cells = z["cells"].copy()
        with np.load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz", allow_pickle=False) as z:
            columns = z["local_rt0_face_columns"].copy()
            signs = z["local_rt0_face_signs"].copy()

        pair_count = 1934
        if len(records) != pair_count or len(saved["pair_a"]) != pair_count:
            raise AssertionError("unexpected pair count")
        pairs = np.column_stack((saved["pair_a"], saved["pair_b"]))
        ranked = np.column_stack((ranked_a[:pair_count], ranked_b[:pair_count]))
        if not np.array_equal(pairs, ranked):
            raise AssertionError("saved pairs are not the first 1,934 canonical ranking entries")
        if np.any(pairs[:, 0] >= pairs[:, 1]):
            raise AssertionError("non-touch pairs are not canonical unordered IDs")
        if any(tuple(map(int, pair)) in touching for pair in pairs):
            raise AssertionError("selected non-touch list contains a shared-vertex pair")
        if result["pairs"] != pair_count or result["reused_pairs"] != 0 or result["failure"] is not None:
            raise AssertionError("unexpected producer completion metadata")

        signed_flux = signs[:, :, None] * face_currents[columns]
        rule2 = normalized_tetra_rule(tetrahedra, 2, signed_flux)
        rule3 = normalized_tetra_rule(tetrahedra, 3, signed_flux)
        rebuilt_point2 = np.empty_like(saved["point_pair_q2"])
        rebuilt_point3 = np.empty_like(saved["point_pair_q3"])
        rebuilt_exact = np.empty_like(saved["exact_canonical_reciprocal_pair_matrices"])
        for index, (a, b) in enumerate(pairs):
            rebuilt_point2[index] = point_pair(int(a), int(b), rule2)
            rebuilt_point3[index] = point_pair(int(a), int(b), rule3)
            block = saved["forward_reverse_h"][index, 0]
            directed = signed_flux[int(a)].T @ block @ signed_flux[int(b)]
            rebuilt_exact[index] = directed + directed.T

        point2_relative = relative(rebuilt_point2, saved["point_pair_q2"])
        point3_relative = relative(rebuilt_point3, saved["point_pair_q3"])
        exact_projection_relative = relative(
            rebuilt_exact, saved["exact_canonical_reciprocal_pair_matrices"]
        )
        ranking_delta_relative = relative(
            rebuilt_point3 - rebuilt_point2, ranked_delta[:pair_count]
        )
        composed = {
            2: baseline[2] - rebuilt_point2.sum(axis=0) + rebuilt_exact.sum(axis=0),
            3: baseline[3] - rebuilt_point3.sum(axis=0) + rebuilt_exact.sum(axis=0),
        }
        composition_relative = max(
            relative(composed[2], saved["corrected_matrix_q2"]),
            relative(composed[3], saved["corrected_matrix_q3"]),
        )
        baseline_scale_relative = relative(rank_scale, saved["original_q3_energy_scale"])
        difference = composed[3] - composed[2]
        energy_scale = saved["original_q3_energy_scale"]
        updated_scale = np.linalg.inv(np.linalg.cholesky((composed[3] + composed[3].T) / 2.0))
        metrics_rebuilt = {
            "remaining_original_energy_relative": float(
                np.linalg.norm(energy_scale @ difference @ energy_scale.T, 2)
            ),
            "remaining_updated_energy_relative": float(
                np.linalg.norm(updated_scale @ difference @ updated_scale.T, 2)
            ),
            "remaining_R_coordinate_relative": float(
                np.linalg.norm(difference, 2) / np.linalg.norm(composed[3], 2)
            ),
        }
        reported_relative = max(
            abs(metrics_rebuilt[key] - float(result[key])) /
            max(abs(float(result[key])), np.finfo(float).tiny)
            for key in metrics_rebuilt
        )

        record_change = np.asarray([float(item["relative_change"]) for item in records])
        record_reciprocity = np.asarray([float(item["relative_reciprocity"]) for item in records])
        worst = np.argsort(-record_change, kind="stable")[:8]
        high_blocks = np.empty((8, 2, 4, 4))
        high_change = np.empty(8)
        high_reciprocity = np.empty(8)
        high_assembled = np.empty((8, 5, 5))
        for out_index, pair_index in enumerate(worst):
            if monotonic() - started > 175.0:
                raise TimeoutError("bounded eight-pair doubled-order review")
            a, b = map(int, pairs[pair_index])
            order = int(records[pair_index]["order"])
            high_blocks[out_index, 0] = tetra_pair(tetrahedra[a], tetrahedra[b], 2 * order)
            high_blocks[out_index, 1] = tetra_pair(tetrahedra[b], tetrahedra[a], 2 * order)
            wa = np.linalg.inv(np.linalg.cholesky(local_mass(tetrahedra[a])).T)
            wb = np.linalg.inv(np.linalg.cholesky(local_mass(tetrahedra[b])).T)
            old = saved["forward_reverse_h"][pair_index]
            old_scaled = np.stack((wa.T @ old[0] @ wb, wa.T @ old[1].T @ wb))
            new_scaled = np.stack((wa.T @ high_blocks[out_index, 0] @ wb,
                                   wa.T @ high_blocks[out_index, 1].T @ wb))
            high_change[out_index] = relative(old_scaled, new_scaled)
            high_reciprocity[out_index] = relative(new_scaled[0], new_scaled[1])
            old_directed = signed_flux[a].T @ old[0] @ signed_flux[b]
            new_directed = signed_flux[a].T @ high_blocks[out_index, 0] @ signed_flux[b]
            high_assembled[out_index] = ((new_directed + new_directed.T) -
                                         (old_directed + old_directed.T))
        high_assembled_each = np.asarray([
            float(np.linalg.norm(energy_scale @ value @ energy_scale.T, 2))
            for value in high_assembled
        ])
        high_assembled_sum = float(
            np.linalg.norm(energy_scale @ high_assembled.sum(axis=0) @ energy_scale.T, 2)
        )

        metrics = {
            "pairs": pair_count, "touching_overlap": 0, "retained_touching_pairs": 1292,
            "point_q2_relative": point2_relative,
            "point_q3_relative": point3_relative,
            "exact_rt0_projection_relative": exact_projection_relative,
            "ranking_pair_delta_relative": ranking_delta_relative,
            "touching_baseline_composition_relative": composition_relative,
            "baseline_energy_scale_relative": baseline_scale_relative,
            "reported_remaining_metrics_max_relative": reported_relative,
            **metrics_rebuilt,
            "saved_local_change_max": float(record_change.max()),
            "saved_local_reciprocity_max": float(record_reciprocity.max()),
            "doubled_order_pair_change_max": float(high_change.max()),
            "doubled_order_pair_reciprocity_max": float(high_reciprocity.max()),
            "doubled_order_assembled_each_original_energy_max": float(high_assembled_each.max()),
            "doubled_order_assembled_sum_original_energy": high_assembled_sum,
            "global_quadrature_gate_passed": False,
        }
        gate_results = {
            "saved_arrays": max(point2_relative, point3_relative, exact_projection_relative,
                                ranking_delta_relative, baseline_scale_relative) <= GATES["saved_array_relative"],
            "touching_baseline_composition": composition_relative <= GATES["composition_relative"],
            "reported_metrics": reported_relative <= GATES["reported_metric_relative"],
            "saved_pair_local_change": record_change.max() <= GATES["pair_local_change"],
            "saved_pair_local_reciprocity": record_reciprocity.max() <= GATES["pair_local_reciprocity"],
            "doubled_order_change": high_change.max() <= GATES["doubled_order_change"],
            "doubled_order_reciprocity": high_reciprocity.max() <= GATES["doubled_order_reciprocity"],
            "doubled_order_assembled_scale": max(high_assembled_each.max(), high_assembled_sum) <=
                GATES["doubled_order_assembled_energy_scale"],
            "global_stop_retained": (not bool(result["global_quadrature_gate_passed"]) and
                                     metrics_rebuilt["remaining_updated_energy_relative"] > 5.0e-5),
        }
        gate_results = {key: bool(value) for key, value in gate_results.items()}
        if not all(gate_results.values()):
            raise AssertionError(f"review gates failed: {gate_results}")

        artifact = OUT / "independent-review-arrays.npz"
        np.savez_compressed(
            artifact, worst_pair_indices=worst, worst_pair_a=pairs[worst, 0],
            worst_pair_b=pairs[worst, 1], worst_saved_order=np.asarray(
                [records[index]["order"] for index in worst], dtype=np.int64
            ), doubled_order_forward_reverse_h=high_blocks,
            doubled_order_pair_change=high_change,
            doubled_order_pair_reciprocity=high_reciprocity,
            doubled_order_assembled_change=high_assembled,
            composed_corrected_matrix_q2=composed[2], composed_corrected_matrix_q3=composed[3],
        )
        receipt = {
            "program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "ACCEPT_LOCAL_CORRECTION_WITH_GLOBAL_STOP",
            "elapsed_s": monotonic() - started, "reviewer_sha256": digest(Path(__file__)),
            "artifact_sha256": digest(artifact), "pins": PINS,
            "fixed_gates": GATES, "gate_results": gate_results, "metrics": metrics,
            "worst_pair_records": [
                {"index": int(index), "a": int(pairs[index, 0]), "b": int(pairs[index, 1]),
                 "saved_order": int(records[index]["order"]), "review_order": 2 * int(records[index]["order"]),
                 "saved_change": float(record_change[index]),
                 "review_change": float(high_change[out_index]),
                 "review_reciprocity": float(high_reciprocity[out_index]),
                 "assembled_original_energy_scale": float(high_assembled_each[out_index])}
                for out_index, index in enumerate(worst)
            ],
            "scope": (
                "The empirical leading 1,934 non-touch correction is locally accepted after independent "
                "q2/q3 point subtraction, RT0 projection, touching-baseline composition, and doubled-order "
                "checks of the eight worst saved pairs. The remaining five-lift global quadrature gate "
                "stays STOP. No complete near bound, FMM, field, Z, rail, board, or PowerSI claim."
            ),
            "supersedes": (
                "Review01 is retained but invalid: it compared the isolated pair update with the full "
                "corrected q3-minus-q2 matrix and omitted the pinned touching-corrected baseline."
            ),
        }
        (OUT / "independent-review.json").write_text(
            json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": receipt["status"], "metrics": metrics}, allow_nan=False))
    except Exception:
        (OUT / "failure.json").write_text(json.dumps({
            "program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "STOP_NONT0UCH_CORRECTION_REVIEW", "elapsed_s": monotonic() - started,
            "reviewer_sha256": digest(Path(__file__)), "pins": PINS,
            "fixed_gates": GATES, "traceback": traceback.format_exc(),
        }, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    run()
