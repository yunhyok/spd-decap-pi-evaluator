"""Strict independent geometry review of the selected-G L02 junction."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-selected-g-l02-junction-review-04"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
PINS = {
    "outputs/research/astra-selected-g-l02-junction-05/driver-at-run.py":
        "50ce8f22ec8423de717f76d1b8655d1e5f9a02eb862cf7d66b44e34e23999373",
    "outputs/research/astra-selected-g-l02-junction-05/result.json":
        "60647a1f511431b64dcd207c76b48ac4558ad8f917ff0db879969cc8ecb52049",
    "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz":
        "00afa62dabc05a825be3255cfc9888da628403d9ef783cba09ec6d88514bfe59",
    "outputs/research/astra-selected-g-l02-junction-05/l02-junction-template.npz":
        "d2e58cbc248e5957acf4f6b25c9685c3ff0052cc761075f14a7e74ef776d74ce",
    "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz":
        "5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913",
    "outputs/research/astra-selected-g-post-interfaces-02/result.json":
        "b0b8382e67fee48b8f40dd49a23c48d4c6afb0d985a307f591f25b0262e0320d",
    "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json":
        "5157020fd21232c25a236bf0a9892503a927218c5d164c1b0364372c7deb1bb0",
}

LOCAL_FACES = np.asarray([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]], dtype=np.int64)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def signed_volumes(vertices: np.ndarray, cells: np.ndarray) -> np.ndarray:
    tetra = vertices[cells]
    return np.linalg.det(tetra[:, 1:] - tetra[:, :1]) / 6.0


def face_occurrences(vertices: np.ndarray, cells: np.ndarray):
    occurrences = defaultdict(list)
    for cell_index, cell in enumerate(cells):
        for local_index, local in enumerate(LOCAL_FACES):
            ids = np.asarray(cell[local], dtype=np.int64)
            triangle = vertices[ids]
            area_vector = 0.5 * np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
            occurrences[tuple(sorted(map(int, ids)))].append((cell_index, local_index, area_vector))
    assert all(len(values) in (1, 2) for values in occurrences.values())
    return occurrences


def topology_metrics(saved: dict[str, np.ndarray]):
    vertices, cells = saved["vertices_local_um"], saved["cells"]
    occurrences = face_occurrences(vertices, cells)
    saved_keys = [tuple(sorted(map(int, ids))) for ids in saved["face_vertices"]]
    assert len(saved_keys) == len(set(saved_keys)) == len(occurrences)
    assert set(saved_keys) == set(occurrences)
    index_by_key = {key: i for i, key in enumerate(saved_keys)}
    area_error = internal_opposition = 0.0
    boundary_sum = np.zeros(3)
    boundary_scale = 0.0
    internal_keys = set()
    boundary_keys = set()
    for key, values in occurrences.items():
        face_index = index_by_key[key]
        owner = int(saved["first_owner_cell"][face_index])
        match = [value for value in values if value[0] == owner]
        assert len(match) == 1
        independent_area = match[0][2]
        stored_area = saved["face_area_vector_um2"][face_index]
        scale = max(np.linalg.norm(independent_area), 1.0e-30)
        area_error = max(area_error, float(np.linalg.norm(stored_area - independent_area) / scale))
        if len(values) == 2:
            internal_keys.add(key)
            pair_scale = max(np.linalg.norm(values[0][2]), np.linalg.norm(values[1][2]), 1.0e-30)
            internal_opposition = max(internal_opposition, float(np.linalg.norm(values[0][2] + values[1][2]) / pair_scale))
        else:
            boundary_keys.add(key)
            boundary_sum += values[0][2]
            boundary_scale += np.linalg.norm(values[0][2])
    saved_internal = set(tuple(sorted(map(int, saved["face_vertices"][i]))) for i in saved["internal_face_ids"])
    saved_boundary = set(tuple(sorted(map(int, saved["face_vertices"][i]))) for i in saved["boundary_face_ids"])
    assert saved_internal == internal_keys and saved_boundary == boundary_keys
    for face_id, pair in zip(saved["internal_face_ids"], saved["internal_owner_cells"], strict=True):
        key = tuple(sorted(map(int, saved["face_vertices"][face_id])))
        assert set(map(int, pair)) == {occurrence[0] for occurrence in occurrences[key]}
    return occurrences, {
        "stored_first_owner_area_vector_relative_error": area_error,
        "internal_owner_normal_opposition_relative": internal_opposition,
        "boundary_closure_relative": float(np.linalg.norm(boundary_sum) / boundary_scale),
        "unique_faces": len(occurrences),
        "internal_faces": len(internal_keys),
        "boundary_faces": len(boundary_keys),
    }


def horizontal_union(saved: dict[str, np.ndarray], z_value: float):
    vertices, cells = saved["vertices_local_um"], saved["cells"]
    polygons = []
    keys = set()
    for cell in cells:
        for local in LOCAL_FACES:
            ids = np.asarray(cell[local], dtype=np.int64)
            if np.all(abs(vertices[ids, 2] - z_value) < 1.0e-10):
                key = tuple(sorted(map(int, ids)))
                if key not in keys:
                    keys.add(key)
                    polygons.append(Polygon(vertices[ids, :2]))
    assert polygons and all(p.is_valid and p.area > 0 for p in polygons)
    return shapely.union_all(np.asarray(polygons, dtype=object))


def sorted_tetra(vertices: np.ndarray) -> np.ndarray:
    order = np.lexsort((vertices[:, 2], vertices[:, 1], vertices[:, 0]))
    return vertices[order]


def barycentric(parent: np.ndarray, points: np.ndarray) -> np.ndarray:
    affine = (parent[1:] - parent[0]).T
    tail = np.linalg.solve(affine, (points - parent[0]).T).T
    return np.c_[1.0 - tail.sum(axis=1), tail]


def load_npz(relative: str) -> dict[str, np.ndarray]:
    with np.load(ROOT / relative, allow_pickle=False) as saved:
        return {key: saved[key].copy() for key in saved.files}


def main() -> None:
    started = monotonic()
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative
    result = json.loads((ROOT / "outputs/research/astra-selected-g-l02-junction-05/result.json").read_text())
    assert result["status"] == "PASS_SOURCE_G_L02_CONFORMING_JUNCTION_GEOMETRY"
    post = load_npz("outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz")
    joint = load_npz("outputs/research/astra-selected-g-l02-junction-05/l02-junction-template.npz")
    old = load_npz("outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz")

    post_signed = signed_volumes(post["vertices_local_um"], post["cells"])
    joint_signed = signed_volumes(joint["vertices_local_um"], joint["cells"])
    old_signed = signed_volumes(old["vertices_local_um"], old["cells"])
    assert post_signed.min() > 1.0e-12 and joint_signed.min() > 1.0e-12 and old_signed.min() > 1.0e-12
    post_volume_error = float(np.max(abs(post_signed - post["cell_volume_um3"]) / post["cell_volume_um3"]))
    joint_volume_error = float(np.max(abs(joint_signed - joint["cell_volume_um3"]) / joint["cell_volume_um3"]))
    assert post_volume_error < 1.0e-12 and joint_volume_error < 1.0e-12

    post_occurrences, post_topology = topology_metrics(post)
    joint_occurrences, joint_topology = topology_metrics(joint)
    assert post_topology["stored_first_owner_area_vector_relative_error"] < 1.0e-12
    assert joint_topology["stored_first_owner_area_vector_relative_error"] < 1.0e-12
    assert post_topology["internal_owner_normal_opposition_relative"] < 1.0e-12
    assert joint_topology["internal_owner_normal_opposition_relative"] < 1.0e-12
    assert post_topology["boundary_closure_relative"] < 1.0e-12
    assert joint_topology["boundary_closure_relative"] < 1.0e-12

    mapping = post["new_cell_to_existing_post_cell"]
    mapped_new = np.flatnonzero(mapping >= 0)
    mapped_old = mapping[mapped_new]
    assert len(mapped_new) == len(np.unique(mapped_old)) == 2568
    coordinate_error = 0.0
    for new_index, old_index in zip(mapped_new, mapped_old, strict=True):
        coordinate_error = max(coordinate_error, float(np.max(abs(
            sorted_tetra(post["vertices_local_um"][post["cells"][new_index]])
            - sorted_tetra(old["vertices_local_um"][old["cells"][old_index]])
        ))))
    assert coordinate_error < 1.0e-12
    replaced_old = np.setdiff1d(np.arange(len(old["cells"])), mapped_old)
    split_new = np.flatnonzero(mapping < 0)
    assert len(replaced_old) == 36 and len(split_new) == 72
    split_parent_offsets = [0]
    split_parent_indices = []
    containment_error = 0.0
    for row, new_index in enumerate(split_new):
        new_tetra = post["vertices_local_um"][post["cells"][new_index]]
        candidates = []
        for old_index in replaced_old:
            old_tetra = old["vertices_local_um"][old["cells"][old_index]]
            center_bary = barycentric(old_tetra, new_tetra.mean(axis=0, keepdims=True))[0]
            if center_bary.min() > -1.0e-10 and center_bary.max() < 1.0 + 1.0e-10:
                candidates.append(int(old_index))
        assert len(candidates) in (1, 2)
        split_parent_indices.extend(candidates)
        split_parent_offsets.append(len(split_parent_indices))
        # Every new vertex must lie in the union of candidate replaced cells.
        for point in new_tetra:
            violations = []
            for old_index in replaced_old:
                weights = barycentric(old["vertices_local_um"][old["cells"][old_index]], point[None])[0]
                violations.append(max(0.0, -weights.min(), weights.max() - 1.0))
            containment_error = max(containment_error, float(min(violations)))
    split_parent_offsets = np.asarray(split_parent_offsets, dtype=np.int64)
    split_parent_indices = np.asarray(split_parent_indices, dtype=np.int64)
    assert set(split_parent_indices.tolist()) == set(replaced_old.tolist())
    split_volume_error = float(abs(post_signed[split_new].sum() - old_signed[replaced_old].sum()) / old_signed[replaced_old].sum())
    assert containment_error < 1.0e-12 and split_volume_error < 1.0e-12

    horizontal = {}
    for z_value in (0.0, 25.0, 55.0, 75.0):
        prior = horizontal_union(old, z_value)
        current = horizontal_union(post, z_value)
        xor = current.symmetric_difference(prior).area
        horizontal[str(int(z_value))] = {
            "old_area_um2": float(prior.area), "new_area_um2": float(current.area), "xor_area_um2": float(xor)}
        assert xor < 1.0e-10
    new_zone_volume = np.bincount(post["cell_zone"], weights=post_signed)
    old_zone_volume = np.bincount(old["cell_zone"], weights=old_signed)
    zone_error = float(np.max(abs(new_zone_volume / old_zone_volume - 1.0)))
    assert zone_error < 1.0e-12

    lower_ids = np.r_[post["lower_r20_contact_face_ids"], post["lower_r30_complement_face_ids"]]
    lower_triangles = post["vertices_local_um"][post["face_vertices"][lower_ids]][:, :, :2]
    pad0 = shapely.union_all(np.asarray([Polygon(x) for x in lower_triangles], dtype=object))
    pad1 = affinity.translate(pad0, yoff=225.2)
    trace = shapely.from_wkb(bytes.fromhex(str(joint["source_trace_rectangle_wkb_hex"])))
    bridge = shapely.from_wkb(bytes.fromhex(str(joint["single_owned_bridge_wkb_hex"])))
    expected_bridge = trace.difference(shapely.union_all([pad0, pad1]))
    bridge_xor = float(bridge.symmetric_difference(expected_bridge).area)
    bridge_pad_overlap = float(bridge.intersection(shapely.union_all([pad0, pad1])).area)
    assert bridge_xor < 1.0e-10 and bridge_pad_overlap < 1.0e-12

    bodies = joint["cell_body"]
    shared_keys = set(tuple(sorted(map(int, joint["face_vertices"][i]))) for i in joint["shared_interface_face_ids"])
    independent_shared = set()
    shared_by_post = {0: [], 1: []}
    shared_area = {0: 0.0, 1: 0.0}
    for key, values in joint_occurrences.items():
        if len(values) != 2:
            continue
        owners = [value[0] for value in values]
        owner_bodies = {int(bodies[i]) for i in owners}
        if 2 in owner_bodies and len(owner_bodies) == 2:
            independent_shared.add(key)
            post_body = next(int(bodies[i]) for i in owners if int(bodies[i]) != 2)
            assert post_body in (0, 1)
            shared_by_post[post_body].append(key)
            shared_area[post_body] += np.linalg.norm(values[0][2])
    assert shared_keys == independent_shared
    assert len(shared_keys) == 56 and len(shared_by_post[0]) == len(shared_by_post[1]) == 28
    expected_shared_area = float(result["source_contact_length_per_end_um"] * 20.0)
    shared_area_error = max(abs(shared_area[i] / expected_shared_area - 1.0) for i in (0, 1))
    assert shared_area_error < 1.0e-12
    shared_ids = np.asarray(sorted({vertex for key in shared_keys for vertex in key}), dtype=np.int64)
    shared_xyz = joint["vertices_local_um"][shared_ids]
    assert abs(shared_xyz[:, 0].min() + 12.5) < 1.0e-12
    assert abs(shared_xyz[:, 0].max() - 12.5) < 1.0e-12
    assert abs(shared_xyz[:, 2].min() - 55.0) < 1.0e-12
    assert abs(shared_xyz[:, 2].max() - 75.0) < 1.0e-12
    assert shared_xyz[:, 1].min() > 27.2 and shared_xyz[:, 1].max() < 198.0

    bridge_cell_volume = float(joint_signed[bodies == 2].sum())
    bridge_volume_error = abs(bridge_cell_volume - bridge.area * 20.0) / (bridge.area * 20.0)
    joint_additive_error = abs(joint_signed.sum() - 2.0 * post_signed.sum() - bridge.area * 20.0) / joint_signed.sum()
    assert bridge_volume_error < 1.0e-12 and joint_additive_error < 1.0e-12

    arrays_path = OUT / "independent-checks.npz"
    OUT.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(arrays_path, post_signed_volume_um3=post_signed,
        joint_signed_volume_um3=joint_signed, mapped_new_cell=mapped_new,
        mapped_existing_cell=mapped_old, split_new_cell=split_new,
        split_parent_offsets=split_parent_offsets, split_parent_existing_cell=split_parent_indices,
        replaced_existing_cell=replaced_old,
        shared_interface_vertex_xyz_um=shared_xyz)
    receipt = {
        "program": PROGRAM, "version": VERSION, "status": "ACCEPT_WITH_SCOPE",
        "elapsed_s": monotonic() - started, "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "artifacts": {arrays_path.name: digest(arrays_path)},
        "signed_geometry": {
            "post_min_signed_cell_volume_um3": float(post_signed.min()),
            "joint_min_signed_cell_volume_um3": float(joint_signed.min()),
            "post_saved_volume_relative_error": post_volume_error,
            "joint_saved_volume_relative_error": joint_volume_error,
            "post_topology": post_topology, "joint_topology": joint_topology,
        },
        "existing_cell_reuse": {
            "identical_cells": len(mapped_new), "replaced_existing_cells": len(replaced_old),
            "split_new_cells": len(split_new), "maximum_coordinate_error_um": coordinate_error,
            "maximum_split_barycentric_violation": containment_error,
            "maximum_split_parent_volume_relative_error": split_volume_error,
        },
        "horizontal_cross_sections": horizontal,
        "zone_volume_relative_error": zone_error,
        "source_bridge": {
            "trace_bounds_um": list(map(float, trace.bounds)), "pad_area_um2": float(pad0.area),
            "bridge_area_um2": float(bridge.area), "bridge_source_xor_area_um2": bridge_xor,
            "bridge_pad_overlap_area_um2": bridge_pad_overlap,
        },
        "shared_interface": {
            "face_count": len(shared_keys), "faces_per_end": [len(shared_by_post[0]), len(shared_by_post[1])],
            "surface_area_per_end_um2": [float(shared_area[0]), float(shared_area[1])],
            "maximum_surface_area_relative_error": float(shared_area_error),
            "bridge_volume_relative_error": float(bridge_volume_error),
            "joint_additive_volume_relative_error": float(joint_additive_error),
        },
        "scope": (
            "Independent signed-tetra, face-owner, old-to-new coverage and vertical-source-polygon review of the "
            "canonical selected-G L02 two-post junction. It qualifies a local conforming partition only. DGND artwork "
            "single ownership, external traces/artwork/lower vias, current/charge/Green operators, field, port, board, "
            "and PowerSI accuracy remain outside this receipt."),
    }
    assert receipt["elapsed_s"] < 20.0
    (OUT / "independent-review.json").write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
