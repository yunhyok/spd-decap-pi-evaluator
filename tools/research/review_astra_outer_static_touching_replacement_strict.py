"""SPD Decap PI Evaluator v0.23.1: strict saved review of touching replacement."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np

import qualify_astra_joint_mixed_point_correction as mixed
from qualify_astra_conforming_power_joint_sparse_current import local_mass
from qualify_astra_source_joint_charge_green import scalar_pair
from qualify_astra_tetra_charge_green import measure
from qualify_astra_tetra_volume_green import tetra_inner, tetra_pair, triangle_moments


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-outer-static-touching-replacement-review-03"
PRODUCER = ROOT / "outputs/research/astra-outer-static-touching-replacement-02"
PINS = {
    "tools/research/inspect_astra_outer_static_self_replacement.py":
        "592f6a37bd609b8c34d00615f8dc75239da3553dcf721b6d0ace00de050a6b00",
    "outputs/research/astra-outer-static-touching-replacement-02/driver-at-run.py":
        "592f6a37bd609b8c34d00615f8dc75239da3553dcf721b6d0ace00de050a6b00",
    "outputs/research/astra-outer-static-touching-replacement-02/result.json":
        "3e65b705480832e80f2ee85dc9d357399c7a62b85969d70d0be4f02bf6bd6f23",
    "outputs/research/astra-outer-static-touching-replacement-02/self-replacement.npz":
        "bcf2202698cefd0c256a3cd20152259ccc1bd50d602ec8bb6d4527e2914a1655",
    "outputs/research/astra-outer-static-touching-replacement-02/touching-pair-references.npz":
        "8320e572848c0a4561841d71c7f31c2c4f41e2986ce1d0512e746989b37f109c",
    "outputs/research/astra-outer-static-touching-replacement-02/pair-records.json":
        "991c289f03caa3b147106a4688b8dba197bb778c58e6ad7a85c5c14cd48349d0",
    "outputs/research/astra-joint-composite-outer-action-01/composite-action.npz":
        "325a5bb7c06c307d67a60d005be55222d09027f28b7ef0c8890d70438fb8fbb9",
    "outputs/research/astra-joint-adaptive-outer-action-01/adaptive-action.npz":
        "c16ba79d8033fe6fe4c197fa3bca362ca2c6d990d0da93c9b8a5231b758a2bf5",
    "outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz":
        "fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd",
    "outputs/research/astra-joint-composite-outer-patch-errors-03/patch-attribution.npz":
        "e377a7ddb652c788f853953473a0c9b1952d348f4e9fe5ea5721f6b922bec93c",
    "outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz":
        "3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1",
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as source:
        return {key: source[key] for key in source.files}


def relative(actual: np.ndarray, expected: np.ndarray) -> float:
    scale = max(float(np.linalg.norm(expected)), np.finfo(float).tiny)
    return float(np.linalg.norm(actual - expected) / scale)


def main() -> None:
    assert not OUTPUT.exists()
    actual_pins = {path: digest(ROOT / path) for path in PINS}
    assert actual_pins == PINS
    receipt = json.loads((PRODUCER / "result.json").read_text(encoding="utf-8"))
    assert receipt["driver_sha256"] == PINS[
        "tools/research/inspect_astra_outer_static_self_replacement.py"
    ]
    assert receipt["status"] == "COMPLETE_STATIC_REPLACEMENT_DISCRIMINATOR"
    assert receipt["replace_all_touching"] and receipt["reused_static_pairs"]
    assert not receipt["failed_pairs"]

    saved = load_npz(PRODUCER / "self-replacement.npz")
    pair_values = load_npz(PRODUCER / "touching-pair-references.npz")
    pair_records = json.loads((PRODUCER / "pair-records.json").read_text(encoding="utf-8"))
    base = load_npz(ROOT / "outputs/research/astra-joint-composite-outer-action-01/composite-action.npz")
    adaptive = load_npz(ROOT / "outputs/research/astra-joint-adaptive-outer-action-01/adaptive-action.npz")
    diagonal = load_npz(ROOT / "outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz")
    patches = load_npz(ROOT / "outputs/research/astra-joint-composite-outer-patch-errors-03/patch-attribution.npz")
    ownership = load_npz(ROOT / "outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz")

    source = mixed.prepare_sources(3)
    joint = mixed.fmm.load_joint()
    nc = int(source["nc"])
    columns = joint["local_face_columns"]
    signs = joint["local_face_signs"]
    tested_faces = base["tested_current_faces"]
    tested_cells = base["tested_current_cells"]
    scalar_cell = int(base["tested_scalar_volume"][0])
    scalar_mesh_face = int(base["tested_scalar_face"][0])
    scalar_face = nc + int(np.flatnonzero(diagonal["boundary_face_ids"] == scalar_mesh_face)[0])
    observers = [*map(int, tested_cells), scalar_face]
    with np.load(ROOT / list(mixed.PINS)[2], allow_pickle=False) as current_source:
        current = current_source["source_face_currents"]

    entity_vertex_ids = ownership["entity_vertex_ids"]
    neighbors: dict[int, np.ndarray] = {}
    common_vertex_histogram: dict[str, dict[str, int]] = {}
    for entity in observers:
        own_ids = entity_vertex_ids[entity]
        own_ids = own_ids[own_ids >= 0]
        neighbor = np.flatnonzero(np.isin(entity_vertex_ids, own_ids).any(axis=1))
        neighbors[entity] = neighbor
        common_counts = np.array([
            np.intersect1d(own_ids, entity_vertex_ids[other][entity_vertex_ids[other] >= 0]).size
            for other in neighbor
        ])
        unique, counts = np.unique(common_counts, return_counts=True)
        common_vertex_histogram[str(entity)] = {
            str(int(value)): int(count) for value, count in zip(unique, counts, strict=True)
        }
    assert {str(key): len(value) for key, value in neighbors.items()} == receipt[
        "static_touching_entity_counts"
    ]

    expected_records: list[tuple[str, int, int]] = []
    for observer in map(int, tested_cells):
        expected_records.extend(
            ("vector", observer, int(source_id))
            for source_id in neighbors[observer]
            if source_id < nc
        )
    for observer in (scalar_cell, scalar_face):
        expected_records.extend(
            ("scalar", observer, int(source_id)) for source_id in neighbors[observer]
        )
    actual_records = [
        (record["kind"], int(record["observer"]), int(record["source"]))
        for record in pair_records
    ]
    assert actual_records == expected_records
    assert len(pair_records) == receipt["pair_count"] == 165
    assert pair_values["vector_forward_reverse_h"].shape == (81, 2, 4, 4)
    assert pair_values["scalar_forward_reverse_per_m"].shape == (84, 2)

    whitening: dict[int, np.ndarray] = {}
    for entity in set(
        int(source_id)
        for observer in observers
        for source_id in neighbors[observer]
        if source_id < nc
    ):
        vertices = source["entities"][entity]
        factor = np.linalg.cholesky(local_mass(vertices, measure(vertices)))
        whitening[entity] = np.linalg.solve(factor.T, np.eye(4))

    exact_vector = np.zeros((2, 2), complex)
    exact_scalar = np.zeros((2, 2), complex)
    vector_index = scalar_index = 0
    record_reciprocity_errors: list[float] = []
    vector_record_values: dict[tuple[int, int], tuple[dict, np.ndarray]] = {}
    scalar_record_values: dict[tuple[int, int], tuple[dict, np.ndarray]] = {}

    def vector_pair_action(observer: int, source_id: int, block: np.ndarray) -> np.ndarray:
        local_current = signs[source_id, :, None] * current[columns[source_id]]
        local_answer = block @ local_current
        answer = np.zeros((2, 2), complex)
        for row, face in enumerate(tested_faces):
            for slot in np.flatnonzero(columns[observer] == face):
                answer[row] += signs[observer, slot] * local_answer[slot]
        return answer

    for record in pair_records:
        observer = int(record["observer"])
        source_id = int(record["source"])
        assert record["accepted"] and record["order"] in (16, 32, 64)
        if record["kind"] == "vector":
            values = pair_values["vector_forward_reverse_h"][vector_index]
            vector_index += 1
            forward, reverse = values
            wa, wb = whitening[observer], whitening[source_id]
            scaled_forward = wa.T @ forward @ wb
            scaled_reverse = wa.T @ reverse.T @ wb
            computed = float(
                np.linalg.norm(scaled_forward - scaled_reverse)
                / max(np.linalg.norm(scaled_forward), np.finfo(float).tiny)
            )
            exact_vector += vector_pair_action(observer, source_id, forward)
            vector_record_values[observer, source_id] = record, values
        else:
            values = pair_values["scalar_forward_reverse_per_m"][scalar_index]
            scalar_index += 1
            forward, reverse = values
            computed = float(abs(forward - reverse) / max(forward, reverse))
            row = 0 if observer == scalar_cell else 1
            exact_scalar[row] += forward * source["charge"][source_id]
            scalar_record_values[observer, source_id] = record, values
            assert min(forward, reverse) > 0
        record_reciprocity_errors.append(abs(computed - float(record["reciprocity"])))
    assert vector_index == 81 and scalar_index == 84
    assert max(record_reciprocity_errors) < 2e-14
    assert relative(exact_vector, saved["exact_static_self_vector"]) < 2e-14
    assert relative(exact_scalar, saved["exact_static_self_scalar"]) < 2e-14

    analytic_target_count = 0
    point_target_count = 0

    def static_entity_action(entity: int, points: np.ndarray) -> np.ndarray:
        nonlocal analytic_target_count, point_target_count
        vertices = source["entities"][entity]
        origin = vertices[0]
        answer = np.zeros((len(points), 8), complex)
        if entity < nc:
            potential, moment = tetra_inner(vertices - origin, points - origin)
            centered = moment + (points - vertices.mean(axis=0)) * potential[:, None]
            answer[:, :6] = (
                potential[:, None, None] * source["current_centers"][entity][None]
                + centered[:, None, :] * source["current_radial"][entity][None, :, None]
            ).reshape(-1, 6)
        else:
            potential = triangle_moments(vertices - origin, points - origin)[0]
        answer[:, 6:] = (
            potential[:, None] * source["charge"][entity][None] / measure(vertices)
        )
        outside = (
            np.linalg.norm(points - source["entity_centers"][entity], axis=1)
            > 3 * source["radii"][entity]
        )
        analytic_target_count += int(np.count_nonzero(~outside))
        point_target_count += int(np.count_nonzero(outside))
        if outside.any():
            first, last = source["offsets"][entity : entity + 2]
            distance = np.linalg.norm(
                points[outside, None] - source["points"][None, first:last], axis=2
            )
            assert distance.min() > 0
            answer[outside] = (1 / distance) @ source["channels"][first:last]
        return answer

    def touching_static_action(observer: int, points: np.ndarray) -> np.ndarray:
        answer = np.zeros((len(points), 8), complex)
        for source_id in neighbors[observer]:
            answer += static_entity_action(int(source_id), points)
        return answer

    base_static = np.zeros_like(base["corrected_action"])
    adaptive_static = np.zeros_like(adaptive["corrected_action"])
    original_vector: dict[str, np.ndarray] = {}
    original_scalar: dict[str, np.ndarray] = {}
    static_vector: dict[str, np.ndarray] = {}
    static_scalar: dict[str, np.ndarray] = {}

    def integrate(
        entity: int, points: np.ndarray, weights: np.ndarray, values: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        vector = np.zeros((2, 2), complex)
        scalar = np.zeros((2, 2), complex)
        if entity < nc:
            vertices = source["entities"][entity]
            volume = measure(vertices)
            for row, face in enumerate(tested_faces):
                slots = np.flatnonzero(columns[entity] == face)
                if slots.size:
                    slot = int(slots[0])
                    basis = signs[entity, slot] * (points - vertices[slot]) / (3 * volume)
                    for channel in range(2):
                        vector[row, channel] = 1e-7 * np.sum(
                            volume
                            * weights
                            * np.sum(basis * values[:, 3 * channel : 3 * channel + 3], axis=1)
                        )
            if entity == scalar_cell:
                scalar[0] = weights @ values[:, 6:]
        else:
            assert entity == scalar_face
            scalar[1] = weights @ values[:, 6:]
        return vector, scalar

    reconstruction_errors: dict[str, list[float]] = {}
    for depth in (3, 5, 7):
        original_v = np.zeros((2, 2), complex)
        original_s = np.zeros((2, 2), complex)
        static_v = np.zeros((2, 2), complex)
        static_s = np.zeros((2, 2), complex)
        for record_id in np.flatnonzero(base["record_depth"] == depth):
            observer = int(base["record_entity"][record_id])
            first = int(base["record_first"][record_id])
            last = int(base["record_last"][record_id])
            points = base["target_points_m"][first:last]
            weights = base["normalized_outer_weights"][first:last]
            base_static[first:last] = touching_static_action(observer, points)
            value_v, value_s = integrate(observer, points, weights, base_static[first:last])
            static_v += value_v
            static_s += value_s
            value_v, value_s = integrate(
                observer, points, weights, base["corrected_action"][first:last]
            )
            original_v += value_v
            original_s += value_s
        key = str(depth)
        original_vector[key], original_scalar[key] = original_v, original_s
        static_vector[key], static_scalar[key] = static_v, static_s
        reconstruction_errors[key] = [
            relative(original_v, base[f"vector_rows_depth{depth}"]),
            relative(original_s, base[f"scalar_rows_depth{depth}"]),
        ]
        assert relative(static_v, saved[f"own_static_vector_{depth}"]) < 3e-14
        assert relative(static_s, saved[f"own_static_scalar_{depth}"]) < 3e-14
    assert relative(base_static, saved["base_subtracted_static_action"]) < 3e-14

    adaptive_original_v = base["vector_rows_depth7"].copy()
    adaptive_original_s = base["scalar_rows_depth7"].copy()
    adaptive_static_v = static_vector["7"].copy()
    adaptive_static_s = static_scalar["7"].copy()
    adaptive_patch_v = patches["fine_vector_contribution"].copy()
    adaptive_patch_s = patches["fine_scalar_contribution"].copy()
    for index, patch in enumerate(adaptive["selected_patch_ids"]):
        patch = int(patch)
        first7, last7 = patches["depth7_target_spans"][patch]
        observer = int(patches["entity_id"][patch])
        value_v, value_s = integrate(
            observer,
            base["target_points_m"][first7:last7],
            base["normalized_outer_weights"][first7:last7],
            base_static[first7:last7],
        )
        adaptive_static_v -= value_v
        adaptive_static_s -= value_s
        first = int(adaptive["record_first"][index])
        last = int(adaptive["record_last"][index])
        points = adaptive["target_points_m"][first:last]
        weights = adaptive["normalized_outer_weights"][first:last]
        adaptive_static[first:last] = touching_static_action(observer, points)
        value_v, value_s = integrate(observer, points, weights, adaptive_static[first:last])
        adaptive_static_v += value_v
        adaptive_static_s += value_s
        value_v, value_s = integrate(
            observer, points, weights, adaptive["corrected_action"][first:last]
        )
        adaptive_patch_v[patch] = value_v
        adaptive_patch_s[patch] = value_s
    adaptive_original_v = adaptive_patch_v.sum(axis=0)
    adaptive_original_s = adaptive_patch_s.sum(axis=0)
    original_vector["adaptive"] = adaptive_original_v
    original_scalar["adaptive"] = adaptive_original_s
    static_vector["adaptive"] = adaptive_static_v
    static_scalar["adaptive"] = adaptive_static_s
    reconstruction_errors["adaptive"] = [
        relative(adaptive_original_v, adaptive["vector_rows"]),
        relative(adaptive_original_s, adaptive["scalar_rows"]),
    ]
    assert relative(adaptive_static, saved["adaptive_subtracted_static_action"]) < 3e-14
    assert relative(adaptive_static_v, saved["own_static_vector_adaptive"]) < 3e-14
    assert relative(adaptive_static_s, saved["own_static_scalar_adaptive"]) < 3e-14

    replacement_errors: dict[str, list[float]] = {}
    replaced_vector: dict[str, np.ndarray] = {}
    replaced_scalar: dict[str, np.ndarray] = {}
    for key in ("3", "5", "7", "adaptive"):
        value_v = original_vector[key] - static_vector[key] + exact_vector
        value_s = original_scalar[key] - static_scalar[key] + exact_scalar
        replaced_vector[key], replaced_scalar[key] = value_v, value_s
        replacement_errors[key] = [
            relative(value_v, saved[f"replaced_vector_{key}"]),
            relative(value_s, saved[f"replaced_scalar_{key}"]),
        ]
    assert max(value for pair in replacement_errors.values() for value in pair) < 3e-14

    patch_static_v: dict[int, np.ndarray] = {}
    patch_static_s: dict[int, np.ndarray] = {}
    for depth in (5, 7):
        values_v: list[np.ndarray] = []
        values_s: list[np.ndarray] = []
        for patch, (first, last) in enumerate(patches[f"depth{depth}_target_spans"]):
            observer = int(patches["entity_id"][patch])
            value_v, value_s = integrate(
                observer,
                base["target_points_m"][first:last],
                base["normalized_outer_weights"][first:last],
                base_static[first:last],
            )
            values_v.append(value_v)
            values_s.append(value_s)
        patch_static_v[depth] = np.asarray(values_v)
        patch_static_s[depth] = np.asarray(values_s)
        assert relative(patch_static_v[depth], saved[f"patch_static_vector_depth{depth}"]) < 3e-14
        assert relative(patch_static_s[depth], saved[f"patch_static_scalar_depth{depth}"]) < 3e-14

    remaining_v = patches["vector_signed_error"] - (patch_static_v[7] - patch_static_v[5])
    remaining_s = patches["scalar_signed_error"] - (patch_static_s[7] - patch_static_s[5])
    adaptive_patch_static_v = patch_static_v[7].copy()
    adaptive_patch_static_s = patch_static_s[7].copy()
    for index, patch in enumerate(adaptive["selected_patch_ids"]):
        patch = int(patch)
        first = int(adaptive["record_first"][index])
        last = int(adaptive["record_last"][index])
        observer = int(patches["entity_id"][patch])
        value_v, value_s = integrate(
            observer,
            adaptive["target_points_m"][first:last],
            adaptive["normalized_outer_weights"][first:last],
            adaptive_static[first:last],
        )
        remaining_v[patch] = adaptive["vector_signed_error"][patch] - (
            value_v - patch_static_v[7][patch]
        )
        remaining_s[patch] = adaptive["scalar_signed_error"][patch] - (
            value_s - patch_static_s[7][patch]
        )
        adaptive_patch_static_v[patch] = value_v
        adaptive_patch_static_s[patch] = value_s
    scales_v = np.linalg.norm(replaced_vector["adaptive"], axis=0)
    scales_s = np.linalg.norm(replaced_scalar["adaptive"], axis=0)
    normalized_patch = np.column_stack(
        [np.linalg.norm(remaining_v, axis=1) / scales_v,
         np.linalg.norm(remaining_s, axis=1) / scales_s]
    )
    estimator = normalized_patch.sum(axis=0)
    assert relative(remaining_v, saved["remaining_vector_signed_error"]) < 3e-14
    assert relative(remaining_s, saved["remaining_scalar_signed_error"]) < 3e-14
    assert np.allclose(normalized_patch, saved["normalized_remaining_patch_error"], rtol=2e-13, atol=2e-18)
    assert np.allclose(estimator, receipt["remaining_patch_estimator_l1"], rtol=2e-13, atol=2e-18)

    histories: list[dict[str, object]] = []
    previous_v = previous_s = None
    for key in ("3", "5", "7", "adaptive"):
        if previous_v is None:
            changes = None
        else:
            changes = {
                "vector_by_density": (
                    np.linalg.norm(replaced_vector[key] - previous_v, axis=0)
                    / np.linalg.norm(replaced_vector[key], axis=0)
                ).tolist(),
                "scalar_by_density": (
                    np.linalg.norm(replaced_scalar[key] - previous_s, axis=0)
                    / np.linalg.norm(replaced_scalar[key], axis=0)
                ).tolist(),
            }
        histories.append({"level": key, "changes": changes})
        previous_v, previous_s = replaced_vector[key], replaced_scalar[key]
    expected_histories = receipt["refinement_after_static_self_replacement"]
    assert [item["level"] for item in histories] == [
        item["level"] for item in expected_histories
    ]
    for actual_history, expected_history in zip(histories, expected_histories, strict=True):
        if actual_history["changes"] is None:
            assert expected_history["changes"] is None
            continue
        for field in ("vector_by_density", "scalar_by_density"):
            assert np.allclose(
                actual_history["changes"][field],
                expected_history["changes"][field],
                rtol=1e-9,
                atol=1e-15,
            )

    candidates = sorted(
        [record for record in pair_records if record["observer"] != record["source"]],
        key=lambda record: max(record["change"], record["reciprocity"]),
        reverse=True,
    )[:8]
    sample_results: list[dict[str, object]] = []
    for record in candidates:
        observer = int(record["observer"])
        source_id = int(record["source"])
        next_order = 2 * int(record["order"])
        if record["kind"] == "vector":
            _, old_values = vector_record_values[observer, source_id]
            old_forward, old_reverse = old_values
            new_forward = tetra_pair(
                source["entities"][observer], source["entities"][source_id], next_order
            )
            new_reverse = tetra_pair(
                source["entities"][source_id], source["entities"][observer], next_order
            )
            wa, wb = whitening[observer], whitening[source_id]
            old_scaled = np.asarray([wa.T @ old_forward @ wb, wa.T @ old_reverse.T @ wb])
            new_scaled = np.asarray([wa.T @ new_forward @ wb, wa.T @ new_reverse.T @ wb])
            next_change = relative(old_scaled, new_scaled)
            next_reciprocity = float(
                np.linalg.norm(new_scaled[0] - new_scaled[1])
                / max(np.linalg.norm(new_scaled[0]), np.finfo(float).tiny)
            )
            action_delta = vector_pair_action(observer, source_id, new_forward - old_forward)
            action_relative = (
                np.linalg.norm(action_delta, axis=0) / scales_v
            ).tolist()
        else:
            _, old_values = scalar_record_values[observer, source_id]
            old_forward, old_reverse = old_values
            new_forward = scalar_pair(
                source["entities"][observer], source["entities"][source_id], next_order
            )
            new_reverse = scalar_pair(
                source["entities"][source_id], source["entities"][observer], next_order
            )
            next_change = relative(
                np.asarray([old_forward, old_reverse]),
                np.asarray([new_forward, new_reverse]),
            )
            next_reciprocity = float(
                abs(new_forward - new_reverse) / max(new_forward, new_reverse)
            )
            action_delta = (new_forward - old_forward) * source["charge"][source_id]
            action_relative = (abs(action_delta) / scales_s).tolist()
        sample_results.append(
            {
                "kind": record["kind"],
                "observer": observer,
                "source": source_id,
                "saved_order": int(record["order"]),
                "next_order": next_order,
                "saved_last_change": float(record["change"]),
                "saved_reciprocity": float(record["reciprocity"]),
                "saved_to_next_whitened_change": next_change,
                "next_raw_reciprocity": next_reciprocity,
                "assembled_action_relative_by_density": action_relative,
            }
        )

    maximum_sample_change = max(item["saved_to_next_whitened_change"] for item in sample_results)
    maximum_sample_reciprocity = max(item["next_raw_reciprocity"] for item in sample_results)
    maximum_sample_action = max(
        max(item["assembled_action_relative_by_density"]) for item in sample_results
    )
    final_changes = histories[-1]["changes"]
    gate_values = [
        *final_changes["vector_by_density"],
        *final_changes["scalar_by_density"],
        *estimator.tolist(),
    ]
    assert max(value for pair in reconstruction_errors.values() for value in pair) < 3e-14
    assert max(gate_values) < 5e-5
    assert maximum_sample_change < 5e-5
    assert maximum_sample_reciprocity < 5e-5

    OUTPUT.mkdir(parents=True)
    arrays_path = OUTPUT / "strict-review-arrays.npz"
    np.savez_compressed(
        arrays_path,
        touching_neighbor_counts=np.asarray([len(neighbors[key]) for key in observers]),
        reconstructed_exact_static_vector=exact_vector,
        reconstructed_exact_static_scalar=exact_scalar,
        reconstructed_base_static_action=base_static,
        reconstructed_adaptive_static_action=adaptive_static,
        reconstructed_remaining_patch_error=normalized_patch,
        sampled_pair_ids=np.asarray(
            [[item["observer"], item["source"]] for item in sample_results], dtype=np.int64
        ),
        sampled_next_change=np.asarray(
            [item["saved_to_next_whitened_change"] for item in sample_results]
        ),
        sampled_assembled_action_relative=np.asarray(
            [item["assembled_action_relative_by_density"] for item in sample_results]
        ),
    )
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_STATIC_TOUCHING_REPLACEMENT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "artifact_sha256": digest(arrays_path),
        "metrics": {
            "observer_entity_ids": observers,
            "touching_entity_counts": [len(neighbors[key]) for key in observers],
            "common_vertex_histogram": common_vertex_histogram,
            "vector_pair_count": vector_index,
            "scalar_pair_count": scalar_index,
            "pair_record_reciprocity_metric_max_abs_error": max(record_reciprocity_errors),
            "exact_static_vector_relative_error": relative(
                exact_vector, saved["exact_static_self_vector"]
            ),
            "exact_static_scalar_relative_error": relative(
                exact_scalar, saved["exact_static_self_scalar"]
            ),
            "analytic_source_target_evaluations": analytic_target_count,
            "q3_point_source_target_evaluations": point_target_count,
            "base_static_action_relative_error": relative(
                base_static, saved["base_subtracted_static_action"]
            ),
            "adaptive_static_action_relative_error": relative(
                adaptive_static, saved["adaptive_subtracted_static_action"]
            ),
            "saved_row_reconstruction_relative": reconstruction_errors,
            "replacement_reconstruction_relative": replacement_errors,
            "remaining_patch_l1": estimator.tolist(),
            "final_signed_row_change": final_changes,
            "sampled_pair_count": len(sample_results),
            "sampled_pair_max_saved_to_next_change": maximum_sample_change,
            "sampled_pair_max_next_reciprocity": maximum_sample_reciprocity,
            "sampled_pair_max_assembled_action_relative": maximum_sample_action,
        },
        "sampled_pair_refinement": sample_results,
        "scope": (
            "Saved 100 MHz mixed action at two global RT0 rows and two scalar rows only. "
            "The review independently reconstructs all shared-face, shared-edge and shared-vertex "
            "source ownership; the radius-3 analytic-static versus identical q3 point-static "
            "subtraction; signed local-to-global RT0 block contraction including mu0/(4pi)=1e-7; "
            "unit-integrated scalar coefficients; forward/reverse mass-whitened reciprocity; and "
            "the adaptive patch L1 last-change estimator. Eight worst saved pairs are recomputed "
            "at twice their final outer order and scaled by the assembled row actions. These finite "
            "numerical references and the patch L1 sum are diagnostics, not rigorous global error "
            "bounds. Retarded and nontouching terms are retained but not independently reintegrated. "
            "No all-row operator, physical current-charge solve, field, impedance, board response, "
            "or PowerSI accuracy is approved."
        ),
    }
    receipt_path = OUTPUT / "independent-review.json"
    receipt_path.write_text(json.dumps(review, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps(review, allow_nan=False))


if __name__ == "__main__":
    main()
