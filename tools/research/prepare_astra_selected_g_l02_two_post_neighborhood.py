"""SPD Decap PI Evaluator v0.23.1: actual two-post G neighborhood.

Mesh one source-selected L02 DGND neighborhood containing two actual G posts,
their retained artwork/trace owner, and the exact r30 post/residual interfaces.
All exterior faces remain explicit current/charge supports.  This is geometry,
RT0 topology, and copper Joule mass only; it imposes no field boundary value.
"""

from __future__ import annotations

import argparse
from collections import deque
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from scipy.sparse import coo_matrix, csr_matrix

from prepare_astra_conforming_power_joint import positive, prisms, topology
from qualify_astra_conforming_power_joint_sparse_current import local_mass


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SIGMA_S_M = 59.59e6
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/prepare_astra_conforming_power_joint.py":
        "ec1485a02019bbd04cd17a084c17a0ebbca683fb25cdec63361817ff69b8eb8b",
    "tools/research/qualify_astra_conforming_power_joint_sparse_current.py":
        "dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e",
    "outputs/research/astra-selected-g-post-interfaces-02/result.json":
        "b0b8382e67fee48b8f40dd49a23c48d4c6afb0d985a307f591f25b0262e0320d",
    "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json":
        "5157020fd21232c25a236bf0a9892503a927218c5d164c1b0364372c7deb1bb0",
    "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz":
        "00afa62dabc05a825be3255cfc9888da628403d9ef783cba09ec6d88514bfe59",
    "outputs/research/astra-l02-trace-inputs-01/result.json":
        "63c44792b82dc95202d0bcef7d115e1bde3186a626e59f35d7cb6313a5891b9f",
    "outputs/research/astra-l02-trace-inputs-01/source-trace-inputs.npz":
        "d1f7c1ed9cb1b1c404c704417b260d596109827e344ae831229b95f2078a6cf5",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/result.json":
        "048491fdf7fa97440834b09213595bb9f5120ebf510c8c6108a42ed9bc80ff22",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/selected-g-l02-residual-owner.wkb":
        "3a23b953242ccfeed9c427fd8a2d5e72aa1aa7267d92152813ee14300ab27a82",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/selected-g-l02-owned-component.wkb":
        "b88581f6e8013a554b5d3fc289275b6ab411b1743da41d33408815ee6411266d",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/post-interface-instances.npz":
        "d4c5a28a20267c0977a1893b411973632e37f7c8de5a96c412870b9e3ab67726",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_wkb(path: Path, geometry) -> str:
    path.write_bytes(shapely.to_wkb(shapely.normalize(geometry)))
    return digest(path)


def geometry_key(points: np.ndarray, digits: int = 9) -> tuple:
    return tuple(sorted(tuple(round(float(x), digits) for x in row) for row in points))


def csr_payload(matrix: csr_matrix, prefix: str) -> dict[str, np.ndarray]:
    matrix = matrix.tocsr()
    matrix.sum_duplicates()
    matrix.sort_indices()
    return {
        f"{prefix}_shape": np.asarray(matrix.shape, dtype=np.int64),
        f"{prefix}_row_ptr": matrix.indptr.astype(np.int64),
        f"{prefix}_col": matrix.indices.astype(np.int64),
        f"{prefix}_data": matrix.data,
    }


def cell_face_map(cells: np.ndarray, faces: np.ndarray, first: np.ndarray):
    lookup = {tuple(map(int, face)): i for i, face in enumerate(faces)}
    ids = np.empty((len(cells), 4), dtype=np.int64)
    signs = np.empty((len(cells), 4), dtype=np.int8)
    for cell_id, cell in enumerate(cells):
        for local_id in range(4):
            key = tuple(sorted(map(int, np.delete(cell, local_id))))
            face_id = lookup[key]
            ids[cell_id, local_id] = face_id
            signs[cell_id, local_id] = 1 if first[face_id] == cell_id else -1
    return ids, signs, lookup


def independent_quadrature_mass(vertices_m: np.ndarray, cells: np.ndarray, volume_m3: np.ndarray):
    tet = vertices_m[cells]
    a = (5.0 + 3.0 * np.sqrt(5.0)) / 20.0
    b = (5.0 - np.sqrt(5.0)) / 20.0
    bary = np.full((4, 4), b)
    np.fill_diagonal(bary, a)
    points = np.einsum("qa,nak->nqk", bary, tet)
    basis = (points[:, :, None, :] - tet[:, None, :, :]) / (
        3.0 * volume_m3[:, None, None, None]
    )
    return (
        volume_m3[:, None, None]
        * np.einsum("nqik,nqjk->nij", basis, basis)
        / 4.0
    )


def shortest_cell_path(cell_count: int, owners: np.ndarray, start: int, goal: int):
    adjacency: list[list[tuple[int, int]]] = [[] for _ in range(cell_count)]
    for face_offset, (a, b) in enumerate(owners):
        a = int(a)
        b = int(b)
        adjacency[a].append((b, face_offset))
        adjacency[b].append((a, face_offset))
    parent = np.full(cell_count, -1, dtype=np.int64)
    parent_face_offset = np.full(cell_count, -1, dtype=np.int64)
    parent[start] = start
    queue = deque([start])
    while queue and parent[goal] < 0:
        current = queue.popleft()
        for other, face_offset in adjacency[current]:
            if parent[other] < 0:
                parent[other] = current
                parent_face_offset[other] = face_offset
                queue.append(other)
    assert parent[goal] >= 0
    cells = [goal]
    face_offsets = []
    while cells[-1] != start:
        face_offsets.append(int(parent_face_offset[cells[-1]]))
        cells.append(int(parent[cells[-1]]))
    return np.asarray(cells[::-1]), np.asarray(face_offsets[::-1])


def run(output: Path) -> None:
    started = monotonic()
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative, expected in PINS.items():
            assert digest(ROOT / relative) == expected, relative

        ledger = json.loads(
            (ROOT / "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json").read_text()
        )
        pad_rows = ledger["pad_records"]
        trace_rows = ledger["l02_trace_records"]
        assert len(pad_rows) == 978
        assert sum(row["selected_pad_count"] == 2 for row in trace_rows) == 212
        centers_um = np.asarray([row["center_pm"] for row in pad_rows], dtype=float) / 1.0e6
        pin_to_index = {row["pin_id"]: i for i, row in enumerate(pad_rows)}
        assert len(pin_to_index) == len(pad_rows)

        partition_dir = ROOT / "outputs/research/astra-selected-g-l02-single-owned-partition-02"
        full_owner = shapely.from_wkb((partition_dir / "selected-g-l02-owned-component.wkb").read_bytes())
        residual_owner = shapely.from_wkb((partition_dir / "selected-g-l02-residual-owner.wkb").read_bytes())
        assert full_owner.is_valid and residual_owner.is_valid

        # Shapely 2.1 ordered cells tie each Voronoi region to the corresponding
        # source pad center.  Select the smallest actual internal two-pad window
        # deterministically, with source trace ordinal as the final tie-break.
        cells_geometry = np.asarray(
            list(
                shapely.get_parts(
                    shapely.voronoi_polygons(
                        MultiPoint(centers_um), extend_to=full_owner.envelope, ordered=True
                    )
                )
            ),
            dtype=object,
        )
        assert len(cells_geometry) == len(centers_um)
        candidates = []
        for row in trace_rows:
            if row["selected_pad_count"] != 2:
                continue
            pad_ids = [pin_to_index[item["pin_id"]] for item in row["endpoints"]]
            areas = [float(cells_geometry[index].area) for index in pad_ids]
            candidates.append(
                (round(max(areas), 9), round(sum(areas), 9), int(row["ordinal"]), pad_ids, row)
            )
        _, _, selected_ordinal, selected_pad_ids, trace_row = min(candidates, key=lambda item: item[:3])
        assert selected_ordinal == 1208696
        selected_pad_ids = sorted(selected_pad_ids, key=lambda index: tuple(centers_um[index]))
        selected_centers = centers_um[selected_pad_ids]
        assert np.max(np.abs(selected_centers - [[11890.2, 17277.0], [11890.2, 17502.2]])) < 1.0e-10
        window = shapely.normalize(shapely.union_all(cells_geometry[selected_pad_ids]))
        local_full_owner = shapely.normalize(full_owner.intersection(window))
        local_residual = shapely.normalize(residual_owner.intersection(window))
        assert window.geom_type == "Polygon"
        assert local_full_owner.geom_type == "Polygon"
        assert local_residual.geom_type == "Polygon" and len(local_residual.interiors) == 2
        assert abs(window.area - 58552.0) < 1.0e-7

        with np.load(
            ROOT / "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz",
            allow_pickle=False,
        ) as saved:
            post_vertices = saved["vertices_local_um"].copy()
            post_cells = saved["cells"].copy()
            post_zone = saved["cell_zone"].copy()
            post_faces = saved["face_vertices"].copy()
            post_boundary = saved["boundary_face_ids"].copy()
            post_l02_side = saved["l02_side_face_ids"].copy()
            post_top_patch = saved["top_patch_face_ids"].copy()
            post_lower_r20 = saved["lower_r20_contact_face_ids"].copy()
            post_lower_complement = saved["lower_r30_complement_face_ids"].copy()
        assert post_cells.shape == (2640, 4) and len(post_l02_side) == 200

        lower_face_ids = np.r_[post_lower_r20, post_lower_complement]
        lower_xy = post_vertices[np.unique(post_faces[lower_face_ids]), :2]
        pad_local = MultiPoint(lower_xy).convex_hull
        assert pad_local.geom_type == "Polygon"
        pads = [
            shapely.affinity.translate(pad_local, xoff=center[0], yoff=center[1])
            for center in selected_centers
        ]
        hole_match = np.asarray(
            [
                [Polygon(interior).symmetric_difference(pad).area for pad in pads]
                for interior in local_residual.interiors
            ]
        )
        assert np.all(np.min(hole_match, axis=0) < 1.0e-8)
        assert np.all(np.min(hole_match, axis=1) < 1.0e-8)
        owner_rebuild_xor = float(
            shapely.union_all([local_residual, *pads]).symmetric_difference(local_full_owner).area
        )
        assert owner_rebuild_xor < 1.0e-8

        with np.load(
            ROOT / "outputs/research/astra-l02-trace-inputs-01/source-trace-inputs.npz",
            allow_pickle=False,
        ) as saved:
            trace_ordinals = saved["source_trace_ordinal"].copy()
            selected_source = int(np.flatnonzero(trace_ordinals == selected_ordinal)[0])
            trace_xy = saved["endpoint_xy_um"][selected_source].copy()
            trace_width = float(saved["width_um"][selected_source])
            trace_length = float(saved["centerline_length_um"][selected_source])
            trace_ids = json.loads(saved["trace_ids_json_utf8"].tobytes().decode())
            endpoint_ids = json.loads(saved["endpoint_node_ids_json_utf8"].tobytes().decode())
            source_hashes = json.loads(saved["source_trace_row_sha256_json_utf8"].tobytes().decode())
        assert trace_ids[selected_source] == trace_row["trace_id"] == "Trace816479"
        assert endpoint_ids[selected_source] == [trace_row["start_node_id"], trace_row["end_node_id"]]
        assert source_hashes[selected_source] == trace_row["source_record_sha256"]
        assert trace_width == 25.0 and trace_length == 180.0
        source_trace = LineString(trace_xy).buffer(trace_width / 2.0, cap_style="flat")
        assert source_trace.difference(local_full_owner).area < 1.0e-8

        # The constrained triangulation respects the two exact 100-edge r30
        # holes.  Reuse every matching post-ring vertex at z=55 and z=75 so
        # those surfaces become true two-cell interfaces rather than mortar.
        residual_triangles_geometry = list(
            shapely.get_parts(shapely.constrained_delaunay_triangles(local_residual))
        )
        assert len(residual_triangles_geometry) == 266
        residual_triangle_union = shapely.normalize(shapely.union_all(residual_triangles_geometry))
        triangulation_xor = float(residual_triangle_union.symmetric_difference(local_residual).area)
        assert triangulation_xor < 1.0e-8

        planar_coordinates = sorted(
            {
                tuple(map(float, point))
                for triangle in residual_triangles_geometry
                for point in np.asarray(triangle.exterior.coords)[:-1]
            }
        )
        assert len(planar_coordinates) == 264
        planar_lookup = {
            (round(x, 9), round(y, 9)): i for i, (x, y) in enumerate(planar_coordinates)
        }
        planar_triangles = np.asarray(
            [
                [
                    planar_lookup[(round(float(p[0]), 9), round(float(p[1]), 9))]
                    for p in np.asarray(triangle.exterior.coords)[:-1]
                ]
                for triangle in residual_triangles_geometry
            ],
            dtype=np.int64,
        )

        left_vertices = post_vertices + [selected_centers[0, 0], selected_centers[0, 1], 0.0]
        right_vertices = post_vertices + [selected_centers[1, 0], selected_centers[1, 1], 0.0]
        vertex_list = [row.copy() for row in np.vstack([left_vertices, right_vertices])]
        existing = {
            (round(float(row[0]), 9), round(float(row[1]), 9), round(float(row[2]), 9)): i
            for i, row in enumerate(vertex_list)
        }
        bottom = np.empty(len(planar_coordinates), dtype=np.int64)
        top = np.empty(len(planar_coordinates), dtype=np.int64)
        reused_planar = 0
        for planar_id, (x, y) in enumerate(planar_coordinates):
            pair = []
            used_existing = False
            for z in (55.0, 75.0):
                key = (round(x, 9), round(y, 9), z)
                if key in existing:
                    index = existing[key]
                    used_existing = True
                else:
                    index = len(vertex_list)
                    existing[key] = index
                    vertex_list.append(np.asarray([x, y, z]))
                pair.append(index)
            bottom[planar_id], top[planar_id] = pair
            reused_planar += int(used_existing)
        assert reused_planar == 200
        vertices_um = np.asarray(vertex_list)
        residual_cells = positive(vertices_um, prisms(planar_triangles, bottom, top))
        assert len(residual_cells) == 798
        post_count = len(post_cells)
        post_vertex_count = len(post_vertices)
        cells = np.vstack(
            [post_cells, post_cells + post_vertex_count, residual_cells]
        ).astype(np.int64)
        cell_body = np.r_[
            np.zeros(post_count, dtype=np.int8),
            np.ones(post_count, dtype=np.int8),
            np.full(len(residual_cells), 2, dtype=np.int8),
        ]
        cell_zone = np.r_[post_zone, post_zone, np.full(len(residual_cells), 2, dtype=np.int8)]
        mesh, connected, closure = topology(vertices_um, cells)
        assert connected == 1
        faces = mesh["face_vertices"]
        internal = mesh["internal_face_ids"]
        owners = mesh["internal_owner_cells"]
        boundary = mesh["boundary_face_ids"]
        different_body = cell_body[owners[:, 0]] != cell_body[owners[:, 1]]
        shared = internal[different_body]
        shared_owners = owners[different_body]
        assert len(shared) == 400
        assert np.all(np.sum(cell_body[shared_owners] == 2, axis=1) == 1)
        assert np.count_nonzero(np.any(cell_body[shared_owners] == 0, axis=1)) == 200
        assert np.count_nonzero(np.any(cell_body[shared_owners] == 1, axis=1)) == 200

        global_face_keys = {tuple(map(int, face)): i for i, face in enumerate(faces)}
        expected_shared = []
        for body_id, vertex_offset in ((0, 0), (1, post_vertex_count)):
            for local_face in post_l02_side:
                expected_shared.append(
                    global_face_keys[tuple(sorted(map(int, post_faces[local_face] + vertex_offset)))]
                )
        assert set(expected_shared) == set(map(int, shared))

        cell_faces, cell_signs, _ = cell_face_map(cells, faces, mesh["first_owner_cell"])
        internal_incidence = np.zeros(len(faces), dtype=np.int16)
        np.add.at(internal_incidence, cell_faces.ravel(), cell_signs.ravel())
        assert np.max(np.abs(internal_incidence[internal])) == 0
        assert np.all(internal_incidence[boundary] == 1)

        vertices_m = vertices_um * 1.0e-6
        volume_m3 = mesh["cell_volume_um3"] * 1.0e-18
        signed_det = np.linalg.det(
            vertices_m[cells][:, 1:] - vertices_m[cells][:, :1]
        )
        assert float(signed_det.min()) > 0.0
        local_blocks = np.asarray(
            [local_mass(vertices_m[cell], volume_m3[i]) for i, cell in enumerate(cells)]
        )
        quadrature_blocks = independent_quadrature_mass(vertices_m, cells, volume_m3)
        local_quadrature_relative = float(
            np.max(np.abs(local_blocks - quadrature_blocks)) / np.max(np.abs(local_blocks))
        )
        assert local_quadrature_relative < 2.0e-12

        row = np.repeat(cell_faces, 4, axis=1).ravel()
        col = np.tile(cell_faces, (1, 4)).ravel()
        data = (
            cell_signs[:, :, None] * local_blocks * cell_signs[:, None, :]
        ).ravel()
        mass = coo_matrix((data, (row, col)), shape=(len(faces), len(faces))).tocsr()
        mass.sum_duplicates()
        mass.sort_indices()
        resistance = mass / SIGMA_S_M
        d_volume = csr_matrix(
            (
                cell_signs.ravel().astype(float),
                cell_faces.ravel(),
                np.arange(0, 4 * len(cells) + 1, 4, dtype=np.int64),
            ),
            shape=(len(cells), len(faces)),
        )

        # B=-outward-flux on every retained external support.  Volume rows
        # retain D; boundary supports remain independent and are not removed.
        b_row = np.r_[
            np.repeat(np.arange(len(cells)), 4),
            len(cells) + np.arange(len(boundary)),
        ]
        b_col = np.r_[cell_faces.ravel(), boundary]
        b_data = np.r_[cell_signs.ravel(), -np.ones(len(boundary), dtype=np.int8)]
        distributional_b = coo_matrix(
            (b_data, (b_row, b_col)),
            shape=(len(cells) + len(boundary), len(faces)),
        ).tocsr()

        rng = np.random.default_rng(20260909)
        trial = rng.standard_normal(len(faces)) + 1j * rng.standard_normal(len(faces))
        global_energy = float(np.vdot(trial, mass @ trial).real)
        local_flux = cell_signs * trial[cell_faces]
        local_energy = float(
            np.einsum("ni,nij,nj->", local_flux.conj(), local_blocks, local_flux).real
        )
        quadrature_energy = float(
            np.einsum("ni,nij,nj->", local_flux.conj(), quadrature_blocks, local_flux).real
        )
        energy_scatter_relative = abs(global_energy - local_energy) / local_energy
        energy_quadrature_relative = abs(global_energy - quadrature_energy) / quadrature_energy
        assert energy_scatter_relative < 2.0e-13
        assert energy_quadrature_relative < 2.0e-12

        area_vectors_m2 = mesh["face_area_vector_um2"] * 1.0e-12
        constant_current_errors = []
        total_volume = float(volume_m3.sum())
        for axis in range(3):
            flux = area_vectors_m2[:, axis]
            divergence = d_volume @ flux
            energy = float(flux @ (mass @ flux))
            constant_current_errors.append(
                max(
                    float(np.max(np.abs(divergence)) / (total_volume ** (2.0 / 3.0))),
                    abs(energy - total_volume) / total_volume,
                )
            )
        assert max(constant_current_errors) < 2.0e-12

        # Map all retained boundary conditions without eliminating a single DOF.
        state = np.full(len(boundary), "RETAINED_POST_EXTERNAL", dtype="<U64")
        boundary_position = {int(face_id): i for i, face_id in enumerate(boundary)}

        def map_post_faces(local_ids: np.ndarray, offset: int) -> np.ndarray:
            return np.asarray(
                [
                    global_face_keys[
                        tuple(sorted(map(int, post_faces[int(face_id)] + offset)))
                    ]
                    for face_id in local_ids
                ],
                dtype=np.int64,
            )

        top_electrode = np.r_[
            map_post_faces(post_top_patch, 0),
            map_post_faces(post_top_patch, post_vertex_count),
        ]
        lower_contact = np.r_[
            map_post_faces(post_lower_r20, 0),
            map_post_faces(post_lower_r20, post_vertex_count),
        ]
        lower_complement = np.r_[
            map_post_faces(post_lower_complement, 0),
            map_post_faces(post_lower_complement, post_vertex_count),
        ]
        assert np.all(np.isin(top_electrode, boundary))
        assert np.all(np.isin(lower_contact, boundary))
        assert np.all(np.isin(lower_complement, boundary))
        for face_id in top_electrode:
            state[boundary_position[int(face_id)]] = "DECLARED_DUT_TOP_GROUP_ELECTRODE"
        for face_id in lower_contact:
            state[boundary_position[int(face_id)]] = "RETAINED_NEXT_VIA_R20_CONTACT"
        for face_id in lower_complement:
            state[boundary_position[int(face_id)]] = "RETAINED_L02_LOWER_PAD_COMPLEMENT"

        boundary_triangles = vertices_um[faces[boundary]]
        residual_boundary_mask = cell_body[mesh["first_owner_cell"][boundary]] == 2
        residual_boundary_positions = np.flatnonzero(residual_boundary_mask)
        cut_face_ids = []
        source_perimeter_face_ids = []
        for position in residual_boundary_positions:
            face_id = int(boundary[position])
            triangle = boundary_triangles[position]
            if np.ptp(triangle[:, 2]) < 1.0e-10:
                if abs(float(triangle[:, 2].mean()) - 55.0) < 1.0e-10:
                    state[position] = "RETAINED_L02_TOP_SURFACE"
                else:
                    assert abs(float(triangle[:, 2].mean()) - 75.0) < 1.0e-10
                    state[position] = "RETAINED_L02_LOWER_SURFACE"
            else:
                xy = np.unique(np.round(triangle[:, :2], 12), axis=0)
                assert len(xy) == 2
                if max(window.boundary.distance(Point(point)) for point in xy) < 1.0e-8:
                    state[position] = "RETAINED_LOCAL_WINDOW_CUT"
                    cut_face_ids.append(face_id)
                else:
                    state[position] = "RETAINED_SOURCE_OWNER_PERIMETER"
                    source_perimeter_face_ids.append(face_id)
        cut_face_ids = np.asarray(cut_face_ids, dtype=np.int64)
        source_perimeter_face_ids = np.asarray(source_perimeter_face_ids, dtype=np.int64)
        assert len(cut_face_ids) > 0 and len(source_perimeter_face_ids) > 0
        cut_length_from_mesh = float(
            np.linalg.norm(mesh["face_area_vector_um2"][cut_face_ids], axis=1).sum() / 20.0
        )
        cut_length_geometry = float(local_residual.boundary.intersection(window.boundary).length)
        cut_length_relative = abs(cut_length_from_mesh - cut_length_geometry) / cut_length_geometry
        assert cut_length_relative < 2.0e-12

        left_shared = shared_owners[np.any(cell_body[shared_owners] == 0, axis=1)]
        right_shared = shared_owners[np.any(cell_body[shared_owners] == 1, axis=1)]
        left_start = int(left_shared[0, np.flatnonzero(cell_body[left_shared[0]] == 0)[0]])
        right_goal = int(right_shared[0, np.flatnonzero(cell_body[right_shared[0]] == 1)[0]])
        path_cells, path_offsets = shortest_cell_path(len(cells), owners, left_start, right_goal)
        path_faces = internal[path_offsets]
        path_bodies = cell_body[path_cells]
        compressed_path_bodies = [int(path_bodies[0])]
        for body in path_bodies[1:]:
            if int(body) != compressed_path_bodies[-1]:
                compressed_path_bodies.append(int(body))
        assert compressed_path_bodies == [0, 2, 1]

        l02_volume = float(volume_m3[cell_zone == 2].sum())
        expected_l02_volume = float(local_full_owner.area * 20.0e-18)
        l02_volume_relative = abs(l02_volume - expected_l02_volume) / expected_l02_volume
        assert l02_volume_relative < 2.0e-12
        residual_volume_relative = abs(
            float(volume_m3[cell_body == 2].sum()) - local_residual.area * 20.0e-18
        ) / (local_residual.area * 20.0e-18)
        assert residual_volume_relative < 2.0e-12

        artifacts = {}
        geometries = {
            "selected-two-pad-voronoi-window.wkb": window,
            "selected-two-pad-full-owner.wkb": local_full_owner,
            "selected-two-pad-residual-owner.wkb": local_residual,
            "residual-triangulation-union.wkb": residual_triangle_union,
            "selected-source-trace-body.wkb": source_trace,
        }
        for name, geometry in geometries.items():
            artifacts[name] = write_wkb(output / name, geometry)

        mesh_path = output / "two-post-neighborhood-mesh.npz"
        np.savez_compressed(
            mesh_path,
            vertices_um=vertices_um,
            cells=cells,
            cell_body=cell_body,
            cell_zone=cell_zone,
            cell_volume_um3=mesh["cell_volume_um3"],
            face_vertices=faces,
            face_area_vector_um2=mesh["face_area_vector_um2"],
            first_owner_cell=mesh["first_owner_cell"],
            first_owner_local_face=mesh["first_owner_local_face"],
            internal_face_ids=internal,
            internal_owner_cells=owners,
            boundary_face_ids=boundary,
            boundary_state=state,
            shared_r30_interface_face_ids=shared,
            shared_r30_interface_owner_cells=shared_owners,
            residual_planar_coordinates_um=np.asarray(planar_coordinates),
            residual_planar_triangles=planar_triangles,
            residual_cells=residual_cells,
            top_electrode_face_ids=top_electrode,
            lower_r20_next_via_contact_face_ids=lower_contact,
            lower_r30_complement_face_ids=lower_complement,
            retained_window_cut_face_ids=cut_face_ids,
            retained_source_owner_perimeter_face_ids=source_perimeter_face_ids,
            selected_pad_indices=np.asarray(selected_pad_ids, dtype=np.int64),
            selected_pad_pin_ids=np.asarray([pad_rows[i]["pin_id"] for i in selected_pad_ids]),
            selected_pad_centers_um=selected_centers,
            selected_trace_ordinal=np.asarray([selected_ordinal]),
            selected_trace_endpoint_xy_um=trace_xy,
            selected_trace_width_um=np.asarray([trace_width]),
            selected_trace_length_um=np.asarray([trace_length]),
        )
        artifacts[mesh_path.name] = digest(mesh_path)

        operator_path = output / "two-post-neighborhood-rt0.npz"
        np.savez_compressed(
            operator_path,
            cell_face_ids=cell_faces,
            cell_face_signs=cell_signs,
            conductivity_s_m=np.asarray([SIGMA_S_M]),
            boundary_face_ids=boundary,
            boundary_state=state,
            shared_r30_interface_face_ids=shared,
            path_cell_ids=path_cells,
            path_face_ids=path_faces,
            path_cell_body=path_bodies,
            **csr_payload(d_volume, "volume_d"),
            **csr_payload(distributional_b, "distributional_b"),
            **csr_payload(mass, "mass_inv_m"),
            **csr_payload(resistance, "resistance_ohm"),
        )
        artifacts[operator_path.name] = digest(operator_path)

        labels, label_counts = np.unique(state, return_counts=True)
        checks = {
            "selected_trace_ordinal": int(selected_ordinal),
            "selected_trace_id": trace_row["trace_id"],
            "selected_pad_pin_ids": [pad_rows[i]["pin_id"] for i in selected_pad_ids],
            "selected_pad_centers_um": selected_centers.tolist(),
            "voronoi_window_bounds_um": list(map(float, window.bounds)),
            "voronoi_window_area_um2": float(window.area),
            "local_full_owner_area_um2": float(local_full_owner.area),
            "local_residual_owner_area_um2": float(local_residual.area),
            "local_residual_hole_count": len(local_residual.interiors),
            "owner_rebuild_xor_area_um2": owner_rebuild_xor,
            "source_trace_outside_owner_area_um2": float(source_trace.difference(local_full_owner).area),
            "residual_planar_point_count": len(planar_coordinates),
            "residual_planar_triangle_count": len(planar_triangles),
            "residual_triangulation_xor_area_um2": triangulation_xor,
            "reused_post_ring_planar_point_count": int(reused_planar),
            "cell_count": len(cells),
            "face_count": len(faces),
            "internal_face_count": len(internal),
            "boundary_face_count": len(boundary),
            "connected_components": int(connected),
            "minimum_signed_tetra_volume_m3": float(signed_det.min() / 6.0),
            "manifold_boundary_closure_relative": float(closure),
            "shared_r30_interface_face_count": len(shared),
            "shared_left_post_face_count": int(np.count_nonzero(np.any(cell_body[shared_owners] == 0, axis=1))),
            "shared_right_post_face_count": int(np.count_nonzero(np.any(cell_body[shared_owners] == 1, axis=1))),
            "maximum_internal_incidence": int(np.max(np.abs(internal_incidence[internal]))),
            "local_mass_quadrature_relative": local_quadrature_relative,
            "random_complex_mass_scatter_relative": energy_scatter_relative,
            "random_complex_quadrature_relative": energy_quadrature_relative,
            "constant_current_reproduction_relative_xyz": constant_current_errors,
            "l02_owned_volume_relative": l02_volume_relative,
            "residual_volume_relative": residual_volume_relative,
            "retained_window_cut_face_count": len(cut_face_ids),
            "retained_window_cut_length_um": cut_length_from_mesh,
            "retained_window_cut_length_relative": cut_length_relative,
            "retained_source_perimeter_face_count": len(source_perimeter_face_ids),
            "boundary_state_counts": dict(zip(labels.tolist(), map(int, label_counts), strict=True)),
            "cell_path_length": len(path_cells),
            "cell_path_body_sequence": compressed_path_bodies,
            "mass_nnz": int(mass.nnz),
            "distributional_b_nnz": int(distributional_b.nnz),
        }
        gates = {
            "pinned_source_inputs": True,
            "deterministic_actual_internal_trace": selected_ordinal == 1208696,
            "exact_source_owner_rebuild": owner_rebuild_xor < 1.0e-8,
            "exact_residual_triangulation": triangulation_xor < 1.0e-8,
            "positive_manifold_connected_mesh": connected == 1 and signed_det.min() > 0 and closure < 1.0e-13,
            "two_sided_r30_interfaces": len(shared) == 400,
            "all_boundary_supports_retained": len(state) == len(boundary),
            "sparse_rt0_incidence": int(np.max(np.abs(internal_incidence[internal]))) == 0,
            "exact_physical_mass": max(local_quadrature_relative, energy_quadrature_relative) < 2.0e-12,
            "constant_current_reproduction": max(constant_current_errors) < 2.0e-12,
            "single_owned_l02_volume": max(l02_volume_relative, residual_volume_relative) < 2.0e-12,
            "post_residual_post_current_path": compressed_path_bodies == [0, 2, 1],
        }
        assert all(gates.values())

        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "PASS_SELECTED_G_ACTUAL_TWO_POST_VOLUME_AND_SPARSE_RT0",
            "elapsed_s": monotonic() - started,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifacts": artifacts,
            "checks": checks,
            "gates": gates,
            "ownership_contract": (
                "Two exact source r30 post volumes own their pad disks once; the constrained residual extrusion owns "
                "the actual selected artwork/trace remainder inside the two source Voronoi cells once. All 400 r30 "
                "triangles are internal two-cell faces. Every other exterior face remains an explicit current/charge "
                "support, including the artificial window cuts and both r20 next-via contacts."
            ),
            "scope": (
                "One actual selected G two-post source neighborhood and exact copper RT0 topology/Joule mass. The "
                "Voronoi cut is a retained continuation map, not a physical boundary. No exterior zero flux, grounding, "
                "equipotential assumption, current lift, magnetic/scalar Green operator, dielectric, field/terminal "
                "solve, board response, or PowerSI-accuracy conclusion is made."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "checks": checks}))
    except Exception as error:
        (output / "failure.json").write_text(
            json.dumps(
                {
                    "program": PROGRAM,
                    "version": VERSION,
                    "status": "STOP_SELECTED_G_ACTUAL_TWO_POST_VOLUME",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "elapsed_s": monotonic() - started,
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    destination = arguments.output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
