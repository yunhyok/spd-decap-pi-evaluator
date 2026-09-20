"""SPD Decap PI Evaluator v0.23.1: saved composite-outer patch attribution.

This reads the frozen depth-5/depth-7 target actions only.  It neither calls
FMM nor evaluates an analytic Green kernel.  Each depth-5 parent owns the four
contiguous depth-7 descendants created by two additional longest-edge splits.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
from time import monotonic

import numpy as np


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
COMPOSITE = Path("outputs/research/astra-joint-composite-outer-action-01")
MESH = Path("outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz")
SPACE = Path("outputs/research/astra-boundary-joint-current-space-01/current-space.npz")
PINS = {
    COMPOSITE / "driver-at-run.py": "80d1b7c33e36448fbfe58c0938e0579bd089e9c0c83989093ccdb2fef577da74",
    COMPOSITE / "result.json": "430821f1db1ac348006d352206fa1bfef8c9c147a71a59f3e5139b92f721baab",
    COMPOSITE / "composite-action.npz": "325a5bb7c06c307d67a60d005be55222d09027f28b7ef0c8890d70438fb8fbb9",
    MESH: "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    SPACE: "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
}
CHANNEL_LABELS = np.array(
    ["vector_density_0", "vector_density_1", "scalar_density_0", "scalar_density_1"]
)


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def relative(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), np.finfo(float).tiny))


def measure(vertices: np.ndarray) -> float:
    if len(vertices) == 3:
        return float(np.linalg.norm(np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0])) / 2)
    if len(vertices) == 4:
        return float(abs(np.linalg.det((vertices[1:] - vertices[0]).T)) / 6)
    raise AssertionError("unsupported simplex")


def longest_edge_pieces(vertices: np.ndarray, depth: int) -> list[np.ndarray]:
    pieces = [np.asarray(vertices, dtype=float)]
    edges = np.array(list(combinations(range(len(vertices)), 2)), dtype=np.int64)
    for _ in range(depth):
        children: list[np.ndarray] = []
        for parent in pieces:
            lengths = np.linalg.norm(parent[edges[:, 0]] - parent[edges[:, 1]], axis=1)
            a, b = edges[int(np.argmax(lengths))]
            middle = (parent[a] + parent[b]) / 2
            left, right = parent.copy(), parent.copy()
            left[b], right[a] = middle, middle
            children.extend((left, right))
        pieces = children
    return pieces


def count_for_fraction(values: np.ndarray, fraction: float) -> int:
    total = float(values.sum())
    if total == 0:
        return 0
    ordered = np.sort(values)[::-1]
    return int(np.searchsorted(np.cumsum(ordered), fraction * total, side="left") + 1)


def run(output: Path) -> int:
    start = monotonic()
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    for relative_path, expected in PINS.items():
        assert digest(ROOT / relative_path) == expected, str(relative_path)

    producer_result = json.loads((ROOT / COMPOSITE / "result.json").read_text(encoding="utf-8"))
    assert producer_result["status"] == "STOP_COMPOSITE_OUTER_SELECTED_ROWS_GATE"
    assert producer_result["failure"] is None
    assert producer_result["artifact_sha256"] == PINS[COMPOSITE / "composite-action.npz"]

    with np.load(ROOT / COMPOSITE / "composite-action.npz", allow_pickle=False) as saved:
        source = {key: saved[key] for key in saved.files}
    with np.load(ROOT / MESH, allow_pickle=False) as saved:
        vertices = saved["vertices_local_um"] * 1e-6
        cells = saved["cells"].astype(np.int64)
        face_vertices = saved["face_vertices"].astype(np.int64)
        boundary = saved["boundary_face_ids"].astype(np.int64)
    with np.load(ROOT / SPACE, allow_pickle=False) as saved:
        columns = saved["local_rt0_face_columns"].astype(np.int64)
        signs = saved["local_rt0_face_signs"].astype(np.int8)

    nc = len(cells)
    test_faces = source["tested_current_faces"].astype(np.int64)
    test_cells = source["tested_current_cells"].astype(np.int64)
    scalar_cell = int(source["tested_scalar_volume"][0])
    scalar_face = int(source["tested_scalar_face"][0])
    scalar_boundary_slot = int(np.flatnonzero(boundary == scalar_face)[0])
    scalar_face_entity = nc + scalar_boundary_slot
    expected_entities = np.r_[test_cells, scalar_face_entity]
    assert columns.shape == signs.shape == (nc, 4)

    depths = source["record_depth"].astype(np.int64)
    record_entities = source["record_entity"].astype(np.int64)
    firsts = source["record_first"].astype(np.int64)
    lasts = source["record_last"].astype(np.int64)
    for depth in (3, 5, 7):
        assert np.array_equal(record_entities[depths == depth], expected_entities)
    assert int(lasts.max()) == len(source["normalized_outer_weights"])
    assert int(lasts.max()) + 2 == len(source["target_points_m"])

    entity_vertices: dict[int, np.ndarray] = {
        int(cell): vertices[cells[cell]] for cell in test_cells
    }
    entity_vertices[scalar_face_entity] = vertices[face_vertices[scalar_face]]
    record_by_key = {
        (int(depth), int(entity)): (int(first), int(last))
        for depth, entity, first, last in zip(depths, record_entities, firsts, lasts, strict=True)
    }
    points = source["target_points_m"]
    weights = source["normalized_outer_weights"]
    actions = source["corrected_action"]

    def contributions(entity: int, point_slice: slice) -> tuple[np.ndarray, np.ndarray]:
        vector = np.zeros((2, 2), dtype=complex)
        scalar = np.zeros((2, 2), dtype=complex)
        p = points[point_slice]
        w = weights[point_slice]
        values = actions[point_slice]
        if entity < nc:
            original = entity_vertices[entity]
            weighted = w[:, None, None] * (p[:, None] - original[None]) / 3
            local = 1e-7 * np.einsum(
                "pid,pmd->im", weighted, values[:, :6].reshape(-1, 2, 3)
            )
            for row, face in enumerate(test_faces):
                slots = np.flatnonzero(columns[entity] == face)
                if len(slots):
                    assert len(slots) == 1
                    slot = int(slots[0])
                    vector[row] = signs[entity, slot] * local[slot]
            if entity == scalar_cell:
                scalar[0] = w @ values[:, 6:]
        else:
            assert entity == scalar_face_entity
            scalar[1] = w @ values[:, 6:]
        return vector, scalar

    patch_ids: list[int] = []
    patch_entity: list[int] = []
    patch_mesh_id: list[int] = []
    patch_kind: list[int] = []
    patch_piece: list[int] = []
    depth5_spans: list[tuple[int, int]] = []
    depth7_spans: list[tuple[int, int]] = []
    depth7_child_spans: list[list[tuple[int, int]]] = []
    parent_vertices = np.zeros((len(expected_entities) * 32, 4, 3), dtype=float)
    parent_vertex_count: list[int] = []
    coarse_vector: list[np.ndarray] = []
    fine_vector: list[np.ndarray] = []
    coarse_scalar: list[np.ndarray] = []
    fine_scalar: list[np.ndarray] = []
    saved_weight_error = 0.0
    child_geometry_error = 0.0
    patch_index = 0

    for entity in expected_entities:
        entity = int(entity)
        original = entity_vertices[entity]
        parents = longest_edge_pieces(original, 5)
        descendants = longest_edge_pieces(original, 7)
        assert len(parents) == 32 and len(descendants) == 128
        first5, last5 = record_by_key[(5, entity)]
        first7, last7 = record_by_key[(7, entity)]
        q5, rem5 = divmod(last5 - first5, 32)
        q7, rem7 = divmod(last7 - first7, 128)
        assert rem5 == rem7 == 0 and q5 == q7
        assert q5 == (27 if len(original) == 4 else 9)
        original_measure = measure(original)
        for parent_id, parent in enumerate(parents):
            children = descendants[4 * parent_id : 4 * parent_id + 4]
            expected_children = longest_edge_pieces(parent, 2)
            child_geometry_error = max(
                child_geometry_error,
                max(float(np.max(np.abs(a - b))) for a, b in zip(children, expected_children, strict=True)),
            )
            first_parent5 = first5 + parent_id * q5
            last_parent5 = first_parent5 + q5
            first_parent7 = first7 + 4 * parent_id * q7
            last_parent7 = first_parent7 + 4 * q7
            child_spans = [
                (first_parent7 + child * q7, first_parent7 + (child + 1) * q7)
                for child in range(4)
            ]
            expected_fraction = measure(parent) / original_measure
            saved_weight_error = max(
                saved_weight_error,
                abs(float(weights[first_parent5:last_parent5].sum()) - expected_fraction),
                abs(float(weights[first_parent7:last_parent7].sum()) - expected_fraction),
            )
            v5, s5 = contributions(entity, slice(first_parent5, last_parent5))
            v7, s7 = contributions(entity, slice(first_parent7, last_parent7))
            patch_ids.append(patch_index)
            patch_entity.append(entity)
            patch_mesh_id.append(entity if entity < nc else scalar_face)
            patch_kind.append(0 if entity < nc else 1)
            patch_piece.append(parent_id)
            depth5_spans.append((first_parent5, last_parent5))
            depth7_spans.append((first_parent7, last_parent7))
            depth7_child_spans.append(child_spans)
            parent_vertices[patch_index, : len(parent)] = parent
            parent_vertex_count.append(len(parent))
            coarse_vector.append(v5)
            fine_vector.append(v7)
            coarse_scalar.append(s5)
            fine_scalar.append(s7)
            patch_index += 1

    coarse_vector_array = np.asarray(coarse_vector)
    fine_vector_array = np.asarray(fine_vector)
    coarse_scalar_array = np.asarray(coarse_scalar)
    fine_scalar_array = np.asarray(fine_scalar)
    vector_error = fine_vector_array - coarse_vector_array
    scalar_error = fine_scalar_array - coarse_scalar_array

    saved_vector5 = source["vector_rows_depth5"]
    saved_vector7 = source["vector_rows_depth7"]
    saved_scalar5 = source["scalar_rows_depth5"]
    saved_scalar7 = source["scalar_rows_depth7"]
    reconstruction = {
        "vector_depth5": relative(coarse_vector_array.sum(axis=0), saved_vector5),
        "vector_depth7": relative(fine_vector_array.sum(axis=0), saved_vector7),
        "scalar_depth5": relative(coarse_scalar_array.sum(axis=0), saved_scalar5),
        "scalar_depth7": relative(fine_scalar_array.sum(axis=0), saved_scalar7),
        "vector_signed_error_relative_to_change": relative(
            vector_error.sum(axis=0), saved_vector7 - saved_vector5
        ),
        "scalar_signed_error_relative_to_change": relative(
            scalar_error.sum(axis=0), saved_scalar7 - saved_scalar5
        ),
        "vector_signed_error_depth7_scale": float(
            np.linalg.norm(vector_error.sum(axis=0) - (saved_vector7 - saved_vector5))
            / np.linalg.norm(saved_vector7)
        ),
        "scalar_signed_error_depth7_scale": float(
            np.linalg.norm(scalar_error.sum(axis=0) - (saved_scalar7 - saved_scalar5))
            / np.linalg.norm(saved_scalar7)
        ),
    }

    vector_scale = np.linalg.norm(saved_vector7, axis=0)
    scalar_scale = np.linalg.norm(saved_scalar7, axis=0)
    assert np.all(vector_scale > 0) and np.all(scalar_scale > 0)
    normalized_patch_error = np.column_stack(
        (
            np.linalg.norm(vector_error[:, :, 0], axis=1) / vector_scale[0],
            np.linalg.norm(vector_error[:, :, 1], axis=1) / vector_scale[1],
            np.linalg.norm(scalar_error[:, :, 0], axis=1) / scalar_scale[0],
            np.linalg.norm(scalar_error[:, :, 1], axis=1) / scalar_scale[1],
        )
    )
    priority = normalized_patch_error.max(axis=1)

    coverage_target = 0.99
    selected = np.zeros(len(priority), dtype=bool)
    concentration: dict[str, dict[str, float | int]] = {}
    for channel, label in enumerate(CHANNEL_LABELS):
        values = normalized_patch_error[:, channel]
        order = np.argsort(-values, kind="stable")
        total = float(values.sum())
        if total > 0:
            needed = count_for_fraction(values, coverage_target)
            selected[order[:needed]] = True
        concentration[str(label)] = {
            "l1_total": total,
            "patches_for_50_percent": count_for_fraction(values, 0.50),
            "patches_for_90_percent": count_for_fraction(values, 0.90),
            "patches_for_95_percent": count_for_fraction(values, 0.95),
            "patches_for_99_percent": count_for_fraction(values, 0.99),
        }
    selected_coverage = np.divide(
        normalized_patch_error[selected].sum(axis=0),
        normalized_patch_error.sum(axis=0),
        out=np.ones(4),
        where=normalized_patch_error.sum(axis=0) != 0,
    )
    # Only density-1 vector/scalar rows fail the fixed 5e-5 gate.  A first
    # adaptive pass targets 90% of each failing channel's absolute patch
    # estimator.  The all-channel 99% union above is retained as a conservative
    # fallback and exposes when localization is too weak to save work.
    recommended = np.zeros(len(priority), dtype=bool)
    for channel in (1, 3):
        values = normalized_patch_error[:, channel]
        order = np.argsort(-values, kind="stable")
        needed = count_for_fraction(values, 0.90)
        recommended[order[:needed]] = True
    recommended_coverage = np.divide(
        normalized_patch_error[recommended].sum(axis=0),
        normalized_patch_error.sum(axis=0),
        out=np.ones(4),
        where=normalized_patch_error.sum(axis=0) != 0,
    )
    recommended_qpoints = np.where(np.asarray(patch_kind) == 0, 27, 9)
    recommended_depth9_targets = int((16 * recommended_qpoints[recommended]).sum())
    recommended_added_over_depth7 = int((12 * recommended_qpoints[recommended]).sum())

    global_changes = np.r_[
        np.linalg.norm(saved_vector7 - saved_vector5, axis=0) / vector_scale,
        np.linalg.norm(saved_scalar7 - saved_scalar5, axis=0) / scalar_scale,
    ]
    expected_changes = np.r_[
        producer_result["refinement"][-1]["changes"]["vector_by_density"],
        producer_result["refinement"][-1]["changes"]["scalar_by_density"],
    ]
    global_change_error = float(np.max(np.abs(global_changes - expected_changes)))

    top_order = np.argsort(-priority, kind="stable")[:20]
    top_patches = []
    for index in top_order:
        top_patches.append(
            {
                "patch_id": int(index),
                "entity_id": int(patch_entity[index]),
                "entity_kind": "tetrahedron" if patch_kind[index] == 0 else "boundary_triangle",
                "mesh_tetra_or_face_id": int(patch_mesh_id[index]),
                "depth5_piece_id": int(patch_piece[index]),
                "depth5_target_span": list(depth5_spans[index]),
                "depth7_target_span": list(depth7_spans[index]),
                "priority": float(priority[index]),
                "normalized_error_by_channel": normalized_patch_error[index].tolist(),
                "selected_for_depth9": bool(recommended[index]),
            }
        )

    checks = {
        "input_status_is_preserved_stop": True,
        "record_entity_order_exact": True,
        "depth5_parent_count": int(len(priority)),
        "four_contiguous_depth7_children_each": True,
        "child_geometry_max_abs_m": child_geometry_error,
        "saved_parent_weight_fraction_max_abs": saved_weight_error,
        "row_reconstruction_relative": reconstruction,
        "global_change_max_abs": global_change_error,
        "selected_channel_l1_coverage": selected_coverage.tolist(),
        "recommended_failing_channel_l1_coverage": recommended_coverage.tolist(),
    }
    assert len(priority) == 128
    assert child_geometry_error == 0
    assert saved_weight_error < 5e-15
    reconstruction_gates = [
        reconstruction["vector_depth5"],
        reconstruction["vector_depth7"],
        reconstruction["scalar_depth5"],
        reconstruction["scalar_depth7"],
        reconstruction["vector_signed_error_depth7_scale"],
        reconstruction["scalar_signed_error_depth7_scale"],
    ]
    assert max(reconstruction_gates) < 2e-13
    assert global_change_error < 2e-15
    assert np.all(selected_coverage >= coverage_target - 2e-15)
    assert recommended_coverage[1] >= 0.90 and recommended_coverage[3] >= 0.90

    artifact = output / "patch-attribution.npz"
    np.savez_compressed(
        artifact,
        channel_labels=CHANNEL_LABELS,
        vector_depth7_scale=vector_scale,
        scalar_depth7_scale=scalar_scale,
        patch_id=np.asarray(patch_ids, dtype=np.int64),
        entity_id=np.asarray(patch_entity, dtype=np.int64),
        entity_kind_code=np.asarray(patch_kind, dtype=np.int8),
        mesh_tetra_or_face_id=np.asarray(patch_mesh_id, dtype=np.int64),
        depth5_piece_id=np.asarray(patch_piece, dtype=np.int64),
        depth5_target_spans=np.asarray(depth5_spans, dtype=np.int64),
        depth7_target_spans=np.asarray(depth7_spans, dtype=np.int64),
        depth7_child_target_spans=np.asarray(depth7_child_spans, dtype=np.int64),
        parent_vertex_count=np.asarray(parent_vertex_count, dtype=np.int8),
        parent_vertices_m=parent_vertices,
        coarse_vector_contribution=coarse_vector_array,
        fine_vector_contribution=fine_vector_array,
        vector_signed_error=vector_error,
        coarse_scalar_contribution=coarse_scalar_array,
        fine_scalar_contribution=fine_scalar_array,
        scalar_signed_error=scalar_error,
        normalized_patch_error=normalized_patch_error,
        priority_score=priority,
        selected_for_depth9=recommended,
        selected_patch_ids=np.flatnonzero(recommended),
        conservative_99_selected_for_depth9=selected,
        conservative_99_selected_patch_ids=np.flatnonzero(selected),
    )
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_SAVED_COMPOSITE_OUTER_PATCH_ATTRIBUTION",
        "elapsed_s": monotonic() - start,
        "script_sha256": digest(Path(__file__)),
        "pins": {str(path).replace("\\", "/"): value for path, value in PINS.items()},
        "artifact_sha256": digest(artifact),
        "source_status_preserved": producer_result["status"],
        "entities": {
            "current_tetrahedra": test_cells.tolist(),
            "scalar_volume_tetrahedron": scalar_cell,
            "scalar_boundary_face": scalar_face,
            "scalar_boundary_entity": scalar_face_entity,
        },
        "per_density_depth7_scales": {
            "vector": vector_scale.tolist(),
            "scalar": scalar_scale.tolist(),
        },
        "global_depth5_to_depth7_normalized_change": dict(
            zip(CHANNEL_LABELS.tolist(), global_changes.tolist(), strict=True)
        ),
        "checks": checks,
        "concentration": concentration,
        "depth9_proposal": {
            "criterion": "union of the smallest parent sets covering 90 percent of depth5-to-depth7 absolute normalized change in each of the two failing density-1 vector/scalar channels",
            "selected_parent_count": int(recommended.sum()),
            "total_parent_count": int(len(recommended)),
            "selected_patch_ids": np.flatnonzero(recommended).tolist(),
            "per_channel_l1_coverage": dict(
                zip(CHANNEL_LABELS.tolist(), recommended_coverage.tolist(), strict=True)
            ),
            "new_depth9_target_count": recommended_depth9_targets,
            "additional_targets_over_their_saved_depth7_spans": recommended_added_over_depth7,
            "fraction_of_full_depth9_target_count": recommended_depth9_targets / 46080,
            "conservative_all_channel_99_percent_fallback": {
                "selected_parent_count": int(selected.sum()),
                "selected_patch_ids": np.flatnonzero(selected).tolist(),
                "per_channel_l1_coverage": dict(
                    zip(CHANNEL_LABELS.tolist(), selected_coverage.tolist(), strict=True)
                ),
            },
            "limitation": "The selection localizes the saved depth5-to-depth7 estimator; it does not predict a depth9 convergence factor or relax the fixed 5e-5 gate. If the targeted pass fails, the 114-parent fallback shows that the estimator is broadly distributed and full depth9 or a different outer rule is more honest.",
        },
        "top_patches": top_patches,
        "scope": (
            "Saved depth5/depth7 corrected-action samples and positive outer weights only. "
            "The calculation reconstructs two complete selected current rows and two selected scalar rows, "
            "attributes their signed changes to 128 depth5 parents, and records existing target spans and "
            "source mesh IDs for targeted depth9 work. It performs no FMM, Green integration, field solve, "
            "contact solve, new geometry, board solve, or accuracy promotion."
        ),
    }
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "elapsed_s": result["elapsed_s"],
        "selected_parent_count": int(recommended.sum()),
        "global_changes": result["global_depth5_to_depth7_normalized_change"],
        "coverage": result["depth9_proposal"]["per_channel_l1_coverage"],
    }))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
