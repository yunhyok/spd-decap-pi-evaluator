"""Strict saved-topology review of the selected-G two-post neighborhood."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import csr_matrix
from shapely import from_wkb
from shapely.geometry import Polygon
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/research/astra-selected-g-l02-two-post-neighborhood-01"
OUT = ROOT / "outputs/research/astra-selected-g-l02-two-post-neighborhood-review-04"
PINS = {
    "tools/research/prepare_astra_selected_g_l02_two_post_neighborhood.py":
        "7eb0628b97a82f2035577f71bb90c8a01d94c0e2ef956b332caa4e632be3f433",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/result.json":
        "bb2797dbbeb9f30997113732ce3350f2dba5dd4bc909984520131bcc0e26c0bd",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz":
        "600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz":
        "fee5084bfddc44771a16850eeefef2a2731ec29d4a132c6a03f3f42c40c719f1",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/selected-two-pad-residual-owner.wkb":
        "f9ba7333693d00bd9e35f512804ce6ff99ab6186d2edb97e99ca0feb6c0827f9",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as saved:
        return {name: saved[name] for name in saved.files}


def local_outward_area(tetrahedron: np.ndarray, local_face: int) -> np.ndarray:
    ids = np.delete(np.arange(4), local_face)
    triangle = tetrahedron[ids]
    area = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0]) / 2.0
    if np.dot(area, tetrahedron[local_face] - triangle.mean(axis=0)) > 0.0:
        area = -area
    return area


def run() -> None:
    started = monotonic()
    assert not OUT.exists()
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative

    mesh = load(SOURCE / "two-post-neighborhood-mesh.npz")
    rt0 = load(SOURCE / "two-post-neighborhood-rt0.npz")
    vertices = mesh["vertices_um"]
    cells = mesh["cells"]
    faces = mesh["face_vertices"]
    cell_faces = rt0["cell_face_ids"]
    cell_signs = rt0["cell_face_signs"]
    saved_area = mesh["face_area_vector_um2"]
    first_owner = mesh["first_owner_cell"]
    face_lookup = {tuple(sorted(row)): index for index, row in enumerate(faces)}
    assert len(face_lookup) == len(faces)

    reconstructed_faces = np.empty_like(cell_faces)
    reconstructed_signs = np.empty_like(cell_signs)
    maximum_area_vector_relative = 0.0
    owner_lists: list[list[int]] = [[] for _ in range(len(faces))]
    for cell_id, cell in enumerate(cells):
        tetrahedron = vertices[cell]
        signed_six_volume = np.linalg.det(
            (tetrahedron[1:] - tetrahedron[0]).T
        )
        assert signed_six_volume > 0.0
        for local_face in range(4):
            face_id = face_lookup[tuple(sorted(np.delete(cell, local_face)))]
            reconstructed_faces[cell_id, local_face] = face_id
            sign = 1 if first_owner[face_id] == cell_id else -1
            reconstructed_signs[cell_id, local_face] = sign
            owner_lists[face_id].append(cell_id)
            outward = local_outward_area(tetrahedron, local_face)
            expected = sign * saved_area[face_id]
            maximum_area_vector_relative = max(
                maximum_area_vector_relative,
                float(np.linalg.norm(outward - expected) / np.linalg.norm(expected)),
            )
    assert np.array_equal(reconstructed_faces, cell_faces)
    assert np.array_equal(reconstructed_signs, cell_signs)
    assert maximum_area_vector_relative < 2.0e-13

    internal = mesh["internal_face_ids"]
    internal_owners = mesh["internal_owner_cells"]
    boundary = mesh["boundary_face_ids"]
    assert all(len(owner_lists[index]) == 2 for index in internal)
    assert all(len(owner_lists[index]) == 1 for index in boundary)
    reconstructed_internal_owners = np.asarray(
        [owner_lists[index] for index in internal], dtype=np.int64
    )
    assert np.array_equal(
        np.sort(reconstructed_internal_owners, axis=1),
        np.sort(internal_owners, axis=1),
    )

    shared = mesh["shared_r30_interface_face_ids"]
    shared_owners = mesh["shared_r30_interface_owner_cells"]
    locations = np.searchsorted(internal, shared)
    assert np.array_equal(internal[locations], shared)
    assert np.array_equal(
        np.sort(shared_owners, axis=1),
        np.sort(internal_owners[locations], axis=1),
    )
    bodies = mesh["cell_body"]
    centers = mesh["selected_pad_centers_um"]
    body_pairs = np.sort(bodies[shared_owners], axis=1)
    expected_body_pairs = (np.array([0, 2]), np.array([1, 2]))
    shared_counts = []
    shared_areas = []
    maximum_chord_deficit = 0.0
    for center_index, body_pair in enumerate(expected_body_pairs):
        mask = np.all(body_pairs == body_pair, axis=1)
        assert int(mask.sum()) == 200
        selected_faces = shared[mask]
        xyz = vertices[faces[selected_faces]]
        assert np.array_equal(np.unique(xyz[:, :, 2]), np.array([55.0, 75.0]))
        radial = np.linalg.norm(xyz[:, :, :2] - centers[center_index], axis=2)
        assert np.min(radial) >= 30.0 * np.cos(np.pi / 96.0) - 1.0e-12
        # Absolute translation of the saved local template introduces at most
        # 1.73e-12 um here; keep a fixed 5e-12 um coordinate gate.
        assert np.max(radial) <= 30.0 + 5.0e-12
        maximum_chord_deficit = max(maximum_chord_deficit, float(30.0 - radial.min()))
        area = float(np.linalg.norm(saved_area[selected_faces], axis=1).sum())
        expected_area = 96.0 * 2.0 * 30.0 * np.sin(np.pi / 96.0) * 20.0
        assert abs(area - expected_area) / expected_area < 2.0e-14
        shared_counts.append(int(mask.sum()))
        shared_areas.append(area)
    assert abs(shared_areas[0] - shared_areas[1]) < 1.0e-12

    # Reconstruct the volume owner's planar residual from its saved triangles
    # and compare it with the independently pinned source-owner WKB.
    planar = mesh["residual_planar_coordinates_um"]
    triangles = mesh["residual_planar_triangles"]
    reconstructed_owner = unary_union([Polygon(planar[row]) for row in triangles])
    pinned_owner = from_wkb(
        (SOURCE / "selected-two-pad-residual-owner.wkb").read_bytes()
    )
    owner_xor_area = float(reconstructed_owner.symmetric_difference(pinned_owner).area)
    owner_scale = max(float(pinned_owner.area), 1.0e-300)
    assert owner_xor_area / owner_scale < 1.0e-14

    volume_d = csr_matrix(
        (rt0["volume_d_data"], rt0["volume_d_col"], rt0["volume_d_row_ptr"]),
        shape=tuple(rt0["volume_d_shape"]),
    )
    distributional_b = csr_matrix(
        (
            rt0["distributional_b_data"],
            rt0["distributional_b_col"],
            rt0["distributional_b_row_ptr"],
        ),
        shape=tuple(rt0["distributional_b_shape"]),
    )
    assert volume_d.shape == (6078, 14254) and volume_d.nnz == 24312
    assert np.all(np.isin(volume_d.data, (-1.0, 1.0)))
    assert distributional_b.shape == (10274, 14254) and distributional_b.nnz == 28508
    assert np.array_equal(distributional_b[:6078].indptr, volume_d.indptr)
    assert np.array_equal(distributional_b[:6078].indices, volume_d.indices)
    assert np.array_equal(distributional_b[:6078].data, volume_d.data)
    exterior_b = distributional_b[6078:]
    assert np.array_equal(exterior_b.indptr, np.arange(len(boundary) + 1))
    assert np.array_equal(exterior_b.indices, boundary)
    assert np.array_equal(exterior_b.data, -np.ones(len(boundary), dtype=np.int8))

    path_cells = rt0["path_cell_ids"]
    path_faces = rt0["path_face_ids"]
    internal_owner_lookup = {
        int(face_id): set(map(int, owner_pair))
        for face_id, owner_pair in zip(internal, internal_owners, strict=True)
    }
    for left, right, face_id in zip(
        path_cells[:-1], path_cells[1:], path_faces, strict=True
    ):
        assert internal_owner_lookup[int(face_id)] == {int(left), int(right)}
    assert np.array_equal(np.unique(bodies[path_cells]), np.array([0, 1, 2]))
    assert bodies[path_cells[0]] == 0 and bodies[path_cells[-1]] == 1

    OUT.mkdir(parents=True)
    metrics_path = OUT / "strict-metrics.npz"
    np.savez_compressed(
        metrics_path,
        reconstructed_cell_face_ids=reconstructed_faces,
        reconstructed_cell_face_signs=reconstructed_signs,
        shared_interface_face_ids=shared,
        shared_interface_body_pairs=body_pairs,
        shared_interface_area_um2=np.asarray(shared_areas),
        path_cell_ids=path_cells,
        path_face_ids=path_faces,
    )
    gates = {
        "pinned_inputs": True,
        "positive_signed_cell_orientation": True,
        "all_cell_face_ids_signs_and_area_vectors": True,
        "internal_owner_incidence": True,
        "all_400_equal_two_owner_r30_faces": True,
        "residual_mesh_footprint_matches_source_owner_wkb": True,
        "sparse_D_and_distributional_B_values": True,
        "actual_post_residual_post_face_path": True,
    }
    assert all(gates.values())
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_G_TWO_POST_TOPOLOGY_WITH_SCOPE",
        "elapsed_s": monotonic() - started,
        "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "metrics_artifact_sha256": digest(metrics_path),
        "metrics": {
            "cell_count": len(cells),
            "face_count": len(faces),
            "maximum_outward_area_vector_relative": maximum_area_vector_relative,
            "shared_r30_face_counts": shared_counts,
            "shared_r30_area_um2": shared_areas,
            "maximum_96gon_chord_radial_deficit_um": maximum_chord_deficit,
            "residual_owner_xor_area_um2": owner_xor_area,
            "volume_D_nnz": volume_d.nnz,
            "distributional_B_nnz": distributional_b.nnz,
            "boundary_minus_outward_row_count": len(boundary),
            "path_cell_count": len(path_cells),
        },
        "gates": gates,
        "scope": (
            "Strict saved geometry/topology/incidence review of one actual selected-G two-post neighborhood. "
            "All exterior supports remain retained. No current lift, boundary condition, charge or Green operator, "
            "field/terminal solve, global return model, board response, or PowerSI accuracy is approved."
        ),
    }
    (OUT / "independent-review.json").write_text(
        json.dumps(review, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({"status": review["status"], "elapsed_s": review["elapsed_s"], "metrics": review["metrics"]}))


if __name__ == "__main__":
    run()
