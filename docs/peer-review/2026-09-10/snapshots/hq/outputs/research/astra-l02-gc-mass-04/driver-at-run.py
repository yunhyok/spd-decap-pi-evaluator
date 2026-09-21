"""Project frozen L02 source G/C owners onto an accepted P1 mesh, without a solve."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np
import shapely
from scipy import sparse
from shapely import STRtree
from shapely.geometry import MultiPolygon, Polygon, box


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_astra_l14_gc_mass as l14_mass


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OVERLAP_DIR = ROOT / "outputs/research/astra-l02-gc-source-overlaps-01"
OVERLAP_RESULT = OVERLAP_DIR / "result.json"
OVERLAP_NPZ = OVERLAP_DIR / "source-overlap-polygons.npz"
OVERLAP_REVIEW = OVERLAP_DIR / "independent-review.json"
OWNER_LEDGER = ROOT / "outputs/research/astra-l02-gc-owner-ledger-01/result.json"
PINS = {
    "overlap_result": (OVERLAP_RESULT, "3ee62248ceae6d54a2d182a615c882b2e270d8752b166408fcc1441a9549fb46"),
    "overlap_npz": (OVERLAP_NPZ, "7ce1575f8b03380aff3a549f347bbafe9bc0fd85d47fe4039383de9bcaa6d920"),
    "overlap_review": (OVERLAP_REVIEW, "3f135214d73478b0d32173b1580e0812b4966634a80231be6d8596d1c59caebb"),
    "owner_ledger": (OWNER_LEDGER, "69fb96a75b7bbe8a73d6704b237043272b7b1ca2da8c35880894205f606982b8"),
    "triangle_mass_helper": (Path(l14_mass.__file__).resolve(), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
OWNER_COUNT = 2_064
EXTERNAL_ACTIVE_COUNT = 1_696
NATIVE_C_TOTAL_F = 5.909971854073682e-09
AREA_RELATIVE_TOLERANCE = 2.0e-9
CHUNK_TRIANGLES = 8_192
MESH_RESULT_STATUS = "COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT"
MESH_REVIEW_STATUS = "ACCEPT_SAVED_L02_PAD_DOMAIN_MESH_AND_STIFFNESS_INDEPENDENT_REVIEW"
UPPER_I = np.asarray((0, 0, 0, 1, 1, 2), dtype=np.int64)
UPPER_J = np.asarray((0, 1, 2, 1, 2, 2), dtype=np.int64)
FULL_TEMPLATE = (np.ones((3, 3), dtype=np.float64) + np.eye(3, dtype=np.float64)) / 12.0
REFERENCE_TRIANGLE = np.asarray(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)), dtype=np.float64)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def decode_text(array: np.ndarray) -> Any:
    value = np.asarray(array)
    require(value.dtype == np.uint8 and value.ndim == 1, "packed text is not uint8")
    return json.loads(value.tobytes().decode("utf-8"))


def packed_text(value: Any) -> np.ndarray:
    data = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return np.frombuffer(data, dtype=np.uint8)


def verify_file(path: Path, expected: str, label: str) -> dict[str, object]:
    require(len(expected) == 64 and all(character in "0123456789abcdef" for character in expected), f"{label} SHA pin is invalid")
    actual = digest(path)
    require(actual == expected, f"{label} SHA-256 differs: {actual}")
    return {"path": str(path.resolve()), "sha256": expected, "bytes": path.stat().st_size}


def verify_fixed_inputs() -> dict[str, dict[str, object]]:
    return {name: verify_file(path, expected, name) for name, (path, expected) in PINS.items()}


def load_source_inputs() -> tuple[dict[str, Any], dict[str, Any], list[str], np.ndarray, np.ndarray, list[Any], dict[str, str]]:
    overlap = json.loads(OVERLAP_RESULT.read_text(encoding="utf-8"))
    review = json.loads(OVERLAP_REVIEW.read_text(encoding="utf-8"))
    ledger = json.loads(OWNER_LEDGER.read_text(encoding="utf-8"))
    require(overlap.get("program") == PROGRAM and overlap.get("version") == VERSION, "overlap program/version differs")
    require(overlap.get("status") == "COMPLETED_L02_GC_SOURCE_OVERLAPS", "overlap result is incomplete")
    require(review.get("review_status") == "ACCEPT_L02_GC_SOURCE_OVERLAPS", "overlap review is not accepted")
    require(ledger.get("status") == "COMPLETED_L02_GC_SOURCE_EDGE_ASSET_LEDGER", "owner ledger is incomplete")
    owners = ledger["owners"]
    require(len(owners) == overlap.get("owner_count") == OWNER_COUNT, "owner count differs")
    fingerprints = [str(owner["fingerprint"]) for owner in owners]
    require(fingerprints == sorted(fingerprints) and len(set(fingerprints)) == OWNER_COUNT, "owner fingerprints are not unique sorted source order")
    require(all(int(owner["partial_ordinal"]) == 0 for owner in owners), "an owner is outside source partial 0")
    external_active = np.asarray([int(owner["external_active_index"]) for owner in owners], dtype=np.int64)
    require(len(np.unique(external_active)) == ledger.get("external_active_node_count") == EXTERNAL_ACTIVE_COUNT, "external active identity count differs")
    ledger_c = np.asarray([float.fromhex(owner["nominal_capacitance_f_hex"]) for owner in owners], dtype=np.float64)
    require(np.array_equal(ledger_c, np.asarray([owner["nominal_capacitance_f"] for owner in owners])), "ledger decimal/hex C differs")
    require(float(np.sum(ledger_c, dtype=np.float64)) == float(ledger["nominal_capacitance_total_f"]) == NATIVE_C_TOTAL_F, "native C total differs")

    with np.load(OVERLAP_NPZ, allow_pickle=False) as source:
        required = {"wkb_bytes", "wkb_offsets", "owner_fingerprints_json_utf8", "overlap_area_um2", "nominal_capacitance_f"}
        require(required <= set(source.files), "overlap NPZ schema is incomplete")
        saved_fingerprints = decode_text(source["owner_fingerprints_json_utf8"])
        areas = np.asarray(source["overlap_area_um2"], dtype=np.float64).copy()
        capacitance = np.asarray(source["nominal_capacitance_f"], dtype=np.float64).copy()
        data = np.asarray(source["wkb_bytes"], dtype=np.uint8)
        offsets = np.asarray(source["wkb_offsets"], dtype=np.int64)
        geometry_array_sha256 = {
            "wkb_bytes": hashlib.sha256(data.tobytes()).hexdigest(),
            "wkb_offsets": hashlib.sha256(offsets.tobytes()).hexdigest(),
            "overlap_area_um2": hashlib.sha256(areas.tobytes()).hexdigest(),
        }
        require(offsets.shape == (OWNER_COUNT + 1,) and offsets[0] == 0 and offsets[-1] == len(data), "overlap WKB offsets differ")
        polygons = [shapely.from_wkb(data[offsets[index]:offsets[index + 1]].tobytes()) for index in range(OWNER_COUNT)]
    require(saved_fingerprints == fingerprints, "overlap NPZ owner order differs")
    require(np.array_equal(capacitance, ledger_c), "overlap NPZ capacitance differs bitwise")
    require(np.all(np.isfinite(areas)) and np.all(areas > 0.0), "overlap areas are invalid")
    receipt_rows = overlap["owners"]
    require([row["owner_fingerprint"] for row in receipt_rows] == fingerprints, "overlap receipt owner order differs")
    require(np.array_equal(capacitance, np.asarray([row["nominal_capacitance_f"] for row in receipt_rows])), "overlap receipt capacitance differs")
    require(np.array_equal(areas, np.asarray([row["area_um2"] for row in receipt_rows])), "overlap receipt areas differ")
    require(np.array_equal(capacitance / areas, np.asarray([row["retained_native_density_f_per_um2"] for row in receipt_rows])), "frozen owner density differs")
    for index, polygon in enumerate(polygons):
        require(polygon.geom_type in {"Polygon", "MultiPolygon"} and polygon.is_valid and not polygon.is_empty, f"owner {index} WKB is invalid")
        require(abs(float(polygon.area) - float(areas[index])) <= max(float(areas[index]), 1.0) * 2.0e-12, f"owner {index} WKB area differs")
    return overlap, ledger, fingerprints, areas, capacitance, polygons, geometry_array_sha256


def clipped_triangle_mass(cut: Any, vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Evaluate the unchanged moment kernel after mapping the parent to a unit triangle."""
    vertices = np.asarray(vertices, dtype=np.float64)
    require(vertices.shape == (3, 2) and np.all(np.isfinite(vertices)), "clipped parent vertices are invalid")
    edge = (vertices[1:] - vertices[0]).T
    determinant = abs(float(np.linalg.det(edge)))
    require(np.isfinite(determinant) and determinant > 0.0, "clipped parent triangle is degenerate")

    def to_reference(coordinates: np.ndarray) -> np.ndarray:
        mapped = np.linalg.solve(edge, (np.asarray(coordinates, dtype=np.float64) - vertices[0]).T).T
        require(np.all(np.isfinite(mapped)), "clipped reference coordinates are nonfinite")
        return mapped

    reference = shapely.transform(cut, to_reference)
    require(not reference.is_empty and reference.is_valid, "clipped reference geometry is invalid")
    require(np.all(np.isfinite(shapely.get_coordinates(reference))), "clipped reference geometry is nonfinite")
    origin = np.asarray(reference.centroid.coords[0], dtype=np.float64)
    require(origin.shape == (2,) and np.all(np.isfinite(origin)), "clipped reference centroid is invalid")
    matrix, moment_error, area = l14_mass.triangle_mass(reference, REFERENCE_TRIANGLE, origin=origin)
    return matrix * determinant, moment_error * determinant, area * determinant


def element_mass(overlap: Any, triangle: Any, vertices: np.ndarray) -> tuple[np.ndarray | None, float, bool, float]:
    """Return local P1 mass; analytic full-triangle path needs exact covers()."""
    if overlap.covers(triangle):
        edge_a = vertices[1] - vertices[0]
        edge_b = vertices[2] - vertices[0]
        area = abs(float(edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0])) * 0.5
        require(area > 0.0, "covered mesh triangle is degenerate")
        return FULL_TEMPLATE * area, area, True, 0.0
    cut = triangle.intersection(overlap)
    if cut.is_empty or cut.area <= 0.0:
        return None, 0.0, False, 0.0
    matrix, moment_error, area = clipped_triangle_mass(cut, vertices)
    return matrix, area, False, float(np.max(np.abs(moment_error), initial=0.0))


def self_check() -> dict[str, object]:
    vertices = np.asarray(((0.0, 0.0), (4.0, 0.0), (0.0, 3.0)), dtype=np.float64)
    triangle = Polygon(vertices)
    full_domain = box(-1.0, -1.0, 5.0, 4.0)
    full, full_area, used_analytic, full_error = element_mass(full_domain, triangle, vertices)
    require(used_analytic and full is not None, "exact covered triangle did not use analytic path")
    require(np.max(np.abs(full - FULL_TEMPLATE * triangle.area)) <= 1.0e-13 and full_error == 0.0, "analytic triangle mass differs")

    clipped_domain = box(0.0, 0.0, 2.0, 3.0)
    clipped, clipped_area, used_analytic, clipped_error = element_mass(clipped_domain, triangle, vertices)
    require(not used_analytic and clipped is not None and 0.0 < clipped_area < full_area, "partial triangle did not use clipped path")
    direct_cut = triangle.intersection(clipped_domain)
    direct_origin = np.asarray(direct_cut.centroid.coords[0], dtype=np.float64)
    direct, direct_error, direct_area = l14_mass.triangle_mass(direct_cut, vertices, origin=direct_origin)
    require(np.max(np.abs(clipped - direct)) <= 1.0e-13 and abs(clipped_area - direct_area) <= 1.0e-13, "clipped mass differs from direct triangle_mass")
    require(max(clipped_error, float(np.max(np.abs(direct_error), initial=0.0))) <= 1.0e-12, "clipped first moments differ")
    require(np.max(np.abs(clipped - clipped.T)) <= 1.0e-14, "clipped mass is nonsymmetric")
    require(float(np.min(np.linalg.eigvalsh(clipped))) >= -1.0e-12, "clipped mass is non-PSD")
    first = clipped @ np.ones(3)
    require(np.min(first) >= -1.0e-12 and abs(float(np.sum(first)) - clipped_area) <= 1.0e-12, "clipped consistent first moments differ")

    density = 7.0e-12 / clipped_area
    block = density * np.block([[clipped, -first[:, None]], [-first[None, :], np.asarray([[clipped_area]])]])
    require(np.max(np.abs(block - block.T)) == 0.0, "synthetic capacitance block is nonsymmetric")
    require(float(np.min(np.linalg.eigvalsh(block))) >= -1.0e-26, "synthetic capacitance block is non-PSD")
    require(np.max(np.abs(block.sum(axis=1))) <= 1.0e-26, "synthetic capacitance block is not conservative")

    regression_cases = (
        (
            "owner-0",
            np.asarray(((20755.85786437627, -3094.142135623731),
                        (20762.3463313527, -3098.477590650226),
                        (21219.0, -3926.0)), dtype=np.float64),
            Polygon(((21219.0, -3926.0),
                     (21135.486549783778, -3776.0),
                     (21136.22514155125, -3776.0))),
            "parent",
        ),
        (
            "owner-1635",
            np.asarray(((-49700.0, -48875.729999999996),
                        (-48150.0, -47280.0),
                        (-48148.477590650225, -47287.653668647305)), dtype=np.float64),
            Polygon(((-48150.0, -47280.0),
                     (-48148.477590650225, -47287.653668647305),
                     (-49500.0, -48671.01799303411),
                     (-49500.0, -48669.82935483871))),
            "centroid",
        ),
    )

    def check_reference_case(
        label: str,
        parent: np.ndarray,
        cut: Any,
        old_origin: str | None = None,
        check_exact_translation: bool = False,
    ) -> None:
        if old_origin is not None:
            origin = parent[0] if old_origin == "parent" else np.asarray(cut.centroid.coords[0])
            old_mass, _, old_area = l14_mass.triangle_mass(cut, parent, origin=origin)
            old_tolerance = max(old_area, 1.0) * 1.0e-12
            require(abs(float(np.sum(old_mass)) - old_area) > old_tolerance, f"{label} old regression no longer reproduces")
        matrix, error, area = clipped_triangle_mass(cut, parent)
        tolerance = max(area, 1.0) * 1.0e-12
        first = matrix @ np.ones(3)
        require(
            np.all(np.isfinite(matrix))
            and np.max(np.abs(matrix - matrix.T)) <= tolerance
            and float(np.min(np.linalg.eigvalsh(matrix))) >= -tolerance
            and float(np.min(matrix)) >= -tolerance
            and float(np.min(first)) >= -tolerance
            and abs(float(np.sum(first)) - area) <= tolerance
            and float(np.max(np.abs(error), initial=0.0)) <= tolerance,
            f"{label} reference mass gates failed",
        )
        order = np.asarray((0, 2, 1), dtype=np.int64)
        reversed_matrix, _, reversed_area = clipped_triangle_mass(cut, parent[order])
        require(
            np.max(np.abs(reversed_matrix - matrix[np.ix_(order, order)])) <= tolerance
            and abs(reversed_area - area) <= tolerance,
            f"{label} winding congruence differs",
        )
        if check_exact_translation:
            shift = np.asarray((65536.0, -65536.0), dtype=np.float64)
            shifted_cut = shapely.transform(cut, lambda coordinates: coordinates + shift)
            shifted_matrix, _, shifted_area = clipped_triangle_mass(shifted_cut, parent + shift)
            require(
                np.max(np.abs(shifted_matrix - matrix)) <= tolerance and abs(shifted_area - area) <= tolerance,
                f"{label} exact translation differs",
            )

    for label, parent, cut, old_origin in regression_cases:
        check_reference_case(label, parent, cut, old_origin)

    multipart_parent = np.asarray(((0.0, 0.0), (12.0, 0.0), (0.0, 12.0)), dtype=np.float64)
    multipart_cut = MultiPolygon((
        Polygon(((1.0, 1.0), (4.0, 1.0), (4.0, 4.0), (1.0, 4.0)),
                holes=(((2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 3.0)),)),
        Polygon(((6.0, 1.0), (8.0, 1.0), (6.0, 2.0))),
    ))
    check_reference_case("multipart-hole", multipart_parent, multipart_cut, check_exact_translation=True)

    import tempfile
    mesh_xy = np.asarray(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (2.0, 0.0), (1.0, 1.0)), dtype=np.float64)
    mesh_triangles = np.asarray(((0, 1, 2), (1, 3, 4)), dtype=np.int64)
    mesh_geometry = np.asarray(shapely.polygons(mesh_xy[mesh_triangles]), dtype=object)
    owner_overlap = box(-0.1, -0.1, 1.5, 1.1)
    owner_area = float(owner_overlap.intersection(shapely.union_all(mesh_geometry)).area)
    owner_c = 7.0e-12
    with tempfile.TemporaryDirectory() as temporary:
        arrays, diagnostic = append_owner_mass(
            0, len(mesh_xy), owner_c, owner_area, owner_overlap, STRtree(mesh_geometry),
            mesh_geometry, mesh_xy, mesh_triangles, 10.0, time.perf_counter(), Path(temporary) / "progress.jsonl",
        )
    upper = sparse.coo_matrix(
        (np.concatenate((arrays[2], arrays[5], arrays[8])),
         (np.concatenate((arrays[0], arrays[3], arrays[6])), np.concatenate((arrays[1], arrays[4], arrays[7])))),
        shape=(len(mesh_xy) + 1, len(mesh_xy) + 1),
    ).tocsc()
    assembled = (upper + upper.T - sparse.diags(upper.diagonal(), format="csc")).tocsc()
    require(diagnostic["full_triangle_count"] == diagnostic["clipped_triangle_count"] == 1, "two-triangle analytic/clipped routing differs")
    require(diagnostic["relative_area_error"] <= 1.0e-14 and diagnostic["capacitance_relative_error"] <= 1.0e-14, "two-triangle area/C recollapse differs")
    require(np.max(np.abs(np.asarray(assembled.sum(axis=1)).ravel())) <= 1.0e-25, "two-triangle capacitance rows do not conserve")

    predicate_xy = np.asarray((
        (-0.5, 0.5), (0.5, 0.5), (0.5, 1.5),
        (1.6, 1.6), (1.9, 1.6), (1.6, 1.9),
        (1.5, 0.5), (1.7, 0.6), (1.6, 0.6),
    ), dtype=np.float64)
    predicate_triangles = np.asarray(((0, 1, 2), (3, 4, 5), (6, 7, 8)), dtype=np.int64)
    predicate_geometry = np.asarray(shapely.polygons(predicate_xy[predicate_triangles]), dtype=object)
    predicate_overlap = Polygon(((0.0, 0.0), (2.0, 0.0), (0.0, 2.0)))
    predicate_area = float(predicate_overlap.intersection(predicate_geometry[0]).area)
    with tempfile.TemporaryDirectory() as temporary:
        predicate_arrays, predicate_diagnostic = append_owner_mass(
            1, len(predicate_xy), 1.0e-12, predicate_area, predicate_overlap,
            STRtree(predicate_geometry), predicate_geometry, predicate_xy, predicate_triangles,
            10.0, time.perf_counter(), Path(temporary) / "progress.jsonl",
        )
    used_nodes = np.unique(np.concatenate((predicate_arrays[0], predicate_arrays[1], predicate_arrays[3])))
    require(predicate_diagnostic["predicate_rejected_candidate_count"] == 1
            and predicate_diagnostic["zero_area_intersection_count"] == 1
            and predicate_diagnostic["full_triangle_count"] == 0
            and predicate_diagnostic["clipped_triangle_count"] == 1
            and np.all(used_nodes < 3),
            "prepared-intersects disjoint/boundary-touch routing differs")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS", "clipped_area": clipped_area,
            "full_analytic_path": True, "two_triangle_full_count": 1, "two_triangle_clipped_count": 1,
            "predicate_rejected_candidates": 1, "boundary_touch_zero_area_candidates": 1}


def accepted_mesh_inputs(
    result_path: Path,
    result_sha: str,
    mesh_path: Path,
    mesh_sha: str,
    review_path: Path,
    review_sha: str,
) -> tuple[dict[str, dict[str, object]], dict[str, Any]]:
    pins = {
        "mesh_result": verify_file(result_path, result_sha, "mesh result"),
        "mesh_npz": verify_file(mesh_path, mesh_sha, "mesh NPZ"),
        "mesh_review": verify_file(review_path, review_sha, "mesh review"),
    }
    result = json.loads(result_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    require(result.get("status") == MESH_RESULT_STATUS, "mesh result status differs")
    require(review.get("status") == MESH_REVIEW_STATUS, "mesh review status differs")
    require(review.get("reviewed", {}).get("result", {}).get("sha256") == result_sha, "mesh review result SHA binding differs")
    require(review.get("reviewed", {}).get("snapshot", {}).get("sha256") == mesh_sha, "mesh review snapshot SHA binding differs")
    require(result.get("snapshot", {}).get("sha256") == mesh_sha, "mesh result snapshot SHA binding differs")
    require(Path(result["snapshot"]["path"]).resolve() == mesh_path.resolve(), "mesh result snapshot path differs")
    program = str(result.get("program", ""))
    require(program in {PROGRAM, f"{PROGRAM} v{VERSION}"}, "mesh result program differs")
    if program == PROGRAM:
        require(str(result.get("version")) == VERSION, "mesh result version differs")
    return pins, result


def append_owner_mass(
    owner_index: int,
    external_column: int,
    capacitance_f: float,
    saved_area: float,
    overlap: Any,
    tree: STRtree,
    triangle_geometry: np.ndarray,
    node_xy: np.ndarray,
    triangles: np.ndarray,
    maximum_s: float,
    started: float,
    progress: Path,
) -> tuple[list[np.ndarray], dict[str, float | int]]:
    shapely.prepare(overlap)
    candidates = np.asarray(tree.query(overlap), dtype=np.int64)
    require(candidates.size > 0, f"owner {owner_index} has no mesh candidates")
    candidates.sort(kind="stable")
    node_chunks: list[np.ndarray] = []
    mass_chunks: list[np.ndarray] = []
    area_sum = 0.0
    first_error = 0.0
    minimum_eigenvalue = float("inf")
    full_count = 0
    clipped_count = 0
    predicate_rejected_count = 0
    zero_area_intersection_count = 0
    with progress.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "owner_start", "owner_index": owner_index, "candidate_triangles": len(candidates), "elapsed_s": time.perf_counter() - started}, separators=(",", ":")) + "\n")
    for begin in range(0, len(candidates), CHUNK_TRIANGLES):
        require(time.perf_counter() - started <= maximum_s, "runtime bound exceeded during owner projection")
        indices = candidates[begin:begin + CHUNK_TRIANGLES]
        geometries = triangle_geometry[indices]
        vertices_batch = node_xy[triangles[indices]]
        covered = np.asarray(shapely.covers(overlap, geometries), dtype=np.bool_)
        local_nodes: list[np.ndarray] = []
        local_mass: list[np.ndarray] = []
        if np.any(covered):
            full_indices = indices[covered]
            full_vertices = vertices_batch[covered]
            edge_a = full_vertices[:, 1] - full_vertices[:, 0]
            edge_b = full_vertices[:, 2] - full_vertices[:, 0]
            full_areas = np.abs(edge_a[:, 0] * edge_b[:, 1] - edge_a[:, 1] * edge_b[:, 0]) * 0.5
            require(np.all(full_areas > 0.0), f"owner {owner_index} covered triangle is degenerate")
            full_masses = full_areas[:, None, None] * FULL_TEMPLATE
            node_chunks.append(triangles[full_indices])
            mass_chunks.append(full_masses)
            area_sum += float(np.sum(full_areas, dtype=np.float64))
            minimum_eigenvalue = min(minimum_eigenvalue, float(np.min(full_areas)) / 12.0)
            full_count += len(full_indices)
        partial_positions = np.flatnonzero(~covered)
        if len(partial_positions):
            intersecting = np.asarray(
                shapely.intersects(overlap, geometries[partial_positions]), dtype=np.bool_
            )
            intersecting_positions = partial_positions[intersecting]
            predicate_rejected_count += len(partial_positions) - len(intersecting_positions)
            cuts = shapely.intersection(geometries[intersecting_positions], overlap)
            cut_areas = np.asarray(shapely.area(cuts), dtype=np.float64)
            zero_area_intersection_count += int(np.count_nonzero(cut_areas <= 0.0))
            positive_positions = intersecting_positions[np.flatnonzero(cut_areas > 0.0)]
        else:
            intersecting_positions = np.empty(0, dtype=np.int64)
            cuts = np.empty(0, dtype=object)
            positive_positions = np.empty(0, dtype=np.int64)
        for local_position in positive_positions:
            tri_index = int(indices[local_position])
            vertices = node_xy[triangles[tri_index]]
            cut_position = int(np.searchsorted(intersecting_positions, local_position))
            cut = cuts[cut_position]
            matrix, moment_error, area = clipped_triangle_mass(cut, vertices)
            error = float(np.max(np.abs(moment_error), initial=0.0))
            tolerance = max(area, 1.0) * 1.0e-12
            require(np.all(np.isfinite(matrix)) and np.max(np.abs(matrix - matrix.T)) <= tolerance, f"owner {owner_index} triangle mass is invalid")
            eigenvalue = float(np.min(np.linalg.eigvalsh(matrix)))
            require(eigenvalue >= -tolerance, f"owner {owner_index} triangle mass is non-PSD")
            first = matrix @ np.ones(3)
            require(float(np.min(matrix)) >= -tolerance and float(np.min(first)) >= -tolerance and abs(float(np.sum(first)) - area) <= tolerance, f"owner {owner_index} first moments are inconsistent")
            local_nodes.append(triangles[tri_index])
            local_mass.append(matrix)
            area_sum += area
            first_error = max(first_error, error)
            minimum_eigenvalue = min(minimum_eigenvalue, eigenvalue)
            clipped_count += 1
        if local_nodes:
            node_chunks.append(np.asarray(local_nodes, dtype=np.int64))
            mass_chunks.append(np.asarray(local_mass, dtype=np.float64))
        with progress.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "owner_chunk", "owner_index": owner_index, "candidate_begin": begin, "candidate_end": min(begin + CHUNK_TRIANGLES, len(candidates)), "candidate_total": len(candidates), "elapsed_s": time.perf_counter() - started}, separators=(",", ":")) + "\n")
    require(node_chunks, f"owner {owner_index} has no positive mesh intersection")
    relative_area_error = abs(area_sum - saved_area) / saved_area
    require(relative_area_error <= AREA_RELATIVE_TOLERANCE, f"owner {owner_index} mesh area differs")
    nodes = np.concatenate(node_chunks)
    matrices = np.concatenate(mass_chunks)
    first = matrices @ np.ones(3)
    mass_identity_area = float(np.sum(first, dtype=np.float64))
    require(abs(mass_identity_area - area_sum) / saved_area <= AREA_RELATIVE_TOLERANCE, f"owner {owner_index} mass identity area differs")
    density = capacitance_f / saved_area
    integrated_capacitance = density * mass_identity_area
    capacitance_relative_error = abs(integrated_capacitance - capacitance_f) / capacitance_f
    require(capacitance_relative_error <= AREA_RELATIVE_TOLERANCE, f"owner {owner_index} integrated capacitance differs")

    mm_row = np.minimum(nodes[:, UPPER_I], nodes[:, UPPER_J]).reshape(-1)
    mm_col = np.maximum(nodes[:, UPPER_I], nodes[:, UPPER_J]).reshape(-1)
    mm_data = (matrices[:, UPPER_I, UPPER_J] * density).reshape(-1)
    raw_mass = matrices[:, UPPER_I, UPPER_J].reshape(-1)
    raw_owner = np.full(len(raw_mass), owner_index, dtype=np.int16)
    raw_rows = mm_row.copy()
    raw_cols = mm_col.copy()

    flat_nodes = nodes.reshape(-1)
    flat_first = first.reshape(-1)
    unique_nodes, inverse = np.unique(flat_nodes, return_inverse=True)
    aggregated_first = np.zeros(len(unique_nodes), dtype=np.float64)
    np.add.at(aggregated_first, inverse, flat_first)
    require(np.min(aggregated_first) >= -max(saved_area, 1.0) * 1.0e-12, f"owner {owner_index} aggregated first moments are negative")
    require(abs(float(np.sum(aggregated_first)) - mass_identity_area) / mass_identity_area <= AREA_RELATIVE_TOLERANCE, f"owner {owner_index} first moments do not conserve area")
    coupling_row = unique_nodes
    coupling_col = np.full(len(unique_nodes), external_column, dtype=np.int64)
    coupling_data = -density * aggregated_first
    diagonal_row = np.asarray((external_column,), dtype=np.int64)
    diagonal_data = np.asarray((integrated_capacitance,), dtype=np.float64)
    return [
        mm_row, mm_col, mm_data,
        coupling_row, coupling_col, coupling_data,
        diagonal_row, diagonal_row.copy(), diagonal_data,
        raw_owner, raw_rows, raw_cols, raw_mass,
    ], {
        "mesh_area_um2": area_sum,
        "mass_identity_area_um2": mass_identity_area,
        "source_area_um2": saved_area,
        "relative_area_error": relative_area_error,
        "density_f_per_um2": density,
        "integrated_capacitance_f": integrated_capacitance,
        "capacitance_relative_error": capacitance_relative_error,
        "first_moment_error_max": first_error,
        "minimum_triangle_mass_eigenvalue": minimum_eigenvalue,
        "positive_triangle_count": len(nodes),
        "full_triangle_count": full_count,
        "clipped_triangle_count": clipped_count,
        "predicate_rejected_candidate_count": predicate_rejected_count,
        "zero_area_intersection_count": zero_area_intersection_count,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    fixed_inputs = verify_fixed_inputs()
    mesh_inputs, mesh_result = accepted_mesh_inputs(
        args.mesh_result.resolve(), args.mesh_result_sha256,
        args.mesh_npz.resolve(), args.mesh_npz_sha256,
        args.mesh_review.resolve(), args.mesh_review_sha256,
    )
    overlap_result, ledger, fingerprints, saved_areas, capacitance, overlaps, geometry_array_sha256 = load_source_inputs()
    require(time.perf_counter() - started <= args.max_seconds, "runtime bound exceeded during input validation")

    with np.load(args.mesh_npz, allow_pickle=False) as mesh:
        require({"node_xy_um", "triangles"} <= set(mesh.files), "accepted mesh NPZ lacks node_xy_um/triangles")
        node_xy = np.asarray(mesh["node_xy_um"], dtype=np.float64).copy()
        triangles = np.asarray(mesh["triangles"], dtype=np.int64).copy()
    mesh_array_sha256 = {
        "node_xy_um": hashlib.sha256(node_xy.tobytes()).hexdigest(),
        "triangles": hashlib.sha256(triangles.tobytes()).hexdigest(),
    }
    require(node_xy.ndim == 2 and node_xy.shape[1] == 2 and triangles.ndim == 2 and triangles.shape[1] == 3, "mesh shapes differ")
    require(np.all(np.isfinite(node_xy)) and np.min(triangles) >= 0 and np.max(triangles) < len(node_xy), "mesh coordinates/indices are invalid")
    triangle_geometry = np.asarray(shapely.polygons(node_xy[triangles]), dtype=object)
    require(np.all(shapely.is_valid(triangle_geometry)) and np.all(shapely.area(triangle_geometry) > 0.0), "mesh contains invalid triangles")
    tree = STRtree(triangle_geometry)

    external_active = np.asarray([int(owner["external_active_index"]) for owner in ledger["owners"]], dtype=np.int64)
    external_ids = np.unique(external_active)
    require(len(external_ids) == EXTERNAL_ACTIVE_COUNT and np.all(external_ids[:-1] < external_ids[1:]), "external active identities differ")
    external_slot = np.searchsorted(external_ids, external_active)
    expanded_size = len(node_xy) + EXTERNAL_ACTIVE_COUNT

    upper_row_chunks: list[np.ndarray] = []
    upper_col_chunks: list[np.ndarray] = []
    upper_data_chunks: list[np.ndarray] = []
    raw_owner_chunks: list[np.ndarray] = []
    raw_row_chunks: list[np.ndarray] = []
    raw_col_chunks: list[np.ndarray] = []
    raw_mass_chunks: list[np.ndarray] = []
    diagnostics = []
    progress = output / "progress.jsonl"
    for owner_index, (overlap, source_area, owner_c) in enumerate(zip(overlaps, saved_areas, capacitance, strict=True)):
        arrays, diagnostic = append_owner_mass(
            owner_index, len(node_xy) + int(external_slot[owner_index]), float(owner_c), float(source_area),
            overlap, tree, triangle_geometry, node_xy, triangles, args.max_seconds, started, progress,
        )
        upper_row_chunks.extend((arrays[0], arrays[3], arrays[6]))
        upper_col_chunks.extend((arrays[1], arrays[4], arrays[7]))
        upper_data_chunks.extend((arrays[2], arrays[5], arrays[8]))
        raw_owner_chunks.append(arrays[9]); raw_row_chunks.append(arrays[10]); raw_col_chunks.append(arrays[11]); raw_mass_chunks.append(arrays[12])
        diagnostics.append(diagnostic)
        with progress.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"owner_index": owner_index, "fingerprint": fingerprints[owner_index], **diagnostic}, separators=(",", ":")) + "\n")

    upper_rows = np.concatenate(upper_row_chunks)
    upper_cols = np.concatenate(upper_col_chunks)
    upper_data = np.concatenate(upper_data_chunks)
    upper = sparse.coo_matrix((upper_data, (upper_rows, upper_cols)), shape=(expanded_size, expanded_size)).tocsc()
    upper.sum_duplicates()
    capacitance_matrix = (upper + upper.T - sparse.diags(upper.diagonal(), format="csc")).tocsc()
    capacitance_matrix.sum_duplicates(); capacitance_matrix.eliminate_zeros()
    difference = capacitance_matrix - capacitance_matrix.T
    difference.eliminate_zeros()
    require(difference.nnz == 0 and np.all(np.isfinite(capacitance_matrix.data)), "capacitance block is nonsymmetric or nonfinite")
    require(float(np.min(capacitance_matrix.diagonal(), initial=0.0)) >= -np.finfo(float).eps, "capacitance block has a negative diagonal")
    row_sum = np.asarray(capacitance_matrix.sum(axis=1)).ravel()
    scale = max(float(np.max(np.asarray(abs(capacitance_matrix).sum(axis=1)).ravel(), initial=0.0)), np.finfo(float).tiny)
    rowsum_relative = float(np.max(np.abs(row_sum), initial=0.0)) / scale
    require(rowsum_relative <= AREA_RELATIVE_TOLERANCE, "capacitance block is not conservative")
    require(float(np.sum(capacitance, dtype=np.float64)) == NATIVE_C_TOTAL_F, "assembled native C total differs")

    artifact = output / "l02-gc-capacitance-block.npz"
    l14_mass.atomic_npz(
        artifact,
        capacitance_data_f=np.asarray(capacitance_matrix.data, dtype=np.float64),
        capacitance_indices=np.asarray(capacitance_matrix.indices, dtype=np.int64),
        capacitance_indptr=np.asarray(capacitance_matrix.indptr, dtype=np.int64),
        capacitance_shape=np.asarray(capacitance_matrix.shape, dtype=np.int64),
        mesh_node_count=np.asarray((len(node_xy),), dtype=np.int64),
        external_active_indices=external_ids,
        owner_external_active_indices=external_active,
        block_layout_json_utf8=packed_text({
            "order": ["all_saved_mesh_nodes", "sorted_external_active_nodes"],
            "mesh_node_count": len(node_xy),
            "external_active_count": len(external_ids),
        }),
        source_geometry_array_sha256_json_utf8=packed_text(geometry_array_sha256),
        mesh_geometry_array_sha256_json_utf8=packed_text(mesh_array_sha256),
        owner_fingerprints_json_utf8=packed_text(fingerprints),
        owner_capacitance_f=capacitance,
        owner_source_overlap_area_um2=saved_areas,
        owner_mesh_overlap_area_um2=np.asarray([row["mesh_area_um2"] for row in diagnostics], dtype=np.float64),
        owner_density_f_per_um2=np.asarray([row["density_f_per_um2"] for row in diagnostics], dtype=np.float64),
        owner_integrated_capacitance_f=np.asarray([row["integrated_capacitance_f"] for row in diagnostics], dtype=np.float64),
        owner_mass_owner_index=np.concatenate(raw_owner_chunks),
        owner_mass_row=np.concatenate(raw_row_chunks),
        owner_mass_col=np.concatenate(raw_col_chunks),
        owner_mass_data_um2=np.concatenate(raw_mass_chunks),
    )
    max_area_error = max(float(row["relative_area_error"]) for row in diagnostics)
    max_capacitance_error = max(float(row["capacitance_relative_error"]) for row in diagnostics)
    min_eigenvalue = min(float(row["minimum_triangle_mass_eigenvalue"]) for row in diagnostics)
    integrated_total = float(np.sum([row["integrated_capacitance_f"] for row in diagnostics], dtype=np.float64))
    integrated_total_relative_error = abs(integrated_total - NATIVE_C_TOTAL_F) / NATIVE_C_TOTAL_F
    require(integrated_total_relative_error <= AREA_RELATIVE_TOLERANCE, "integrated capacitance total differs from native C")
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK",
        "inputs": fixed_inputs | mesh_inputs,
        "owner_contract": {
            "partial_ordinal": 0,
            "owner_count": OWNER_COUNT,
            "fingerprints_sorted_and_exact": True,
            "external_active_identity_count": EXTERNAL_ACTIVE_COUNT,
            "native_capacitance_total_f": NATIVE_C_TOTAL_F,
            "integrated_capacitance_total_f": integrated_total,
            "integrated_capacitance_total_relative_error": integrated_total_relative_error,
            "maximum_owner_capacitance_recollapse_relative_error": max_capacitance_error,
            "maximum_owner_area_relative_error": max_area_error,
            "area_relative_tolerance": AREA_RELATIVE_TOLERANCE,
        },
        "mass": {
            "mesh_node_count": len(node_xy),
            "mesh_triangle_count": len(triangles),
            "expanded_shape": list(capacitance_matrix.shape),
            "expanded_nnz": int(capacitance_matrix.nnz),
            "sparse_block_layout": "[all saved mesh nodes, sorted external active nodes]",
            "symmetric": True,
            "finite": True,
            "local_element_psd_gate_passed": True,
            "local_psd_absolute_tolerance": "max(element_area_um2,1)*1e-12",
            "owner_block_construction": "density * [M,-M1;-(M1)^T,1^T M 1] from symmetric local consistent P1 masses",
            "global_spectral_claim": None,
            "minimum_local_triangle_mass_eigenvalue": min_eigenvalue,
            "nonnegative_consistent_first_moments": True,
            "row_sum_relative_max": rowsum_relative,
            "full_triangle_analytic_count": sum(int(row["full_triangle_count"]) for row in diagnostics),
            "clipped_triangle_count": sum(int(row["clipped_triangle_count"]) for row in diagnostics),
        },
        "source_geometry_arrays": {
            "container_sha256": PINS["overlap_npz"][1],
            "raw_array_sha256": geometry_array_sha256,
        },
        "mesh_geometry_arrays": {
            "container_sha256": args.mesh_npz_sha256,
            "raw_array_sha256": mesh_array_sha256,
        },
        "output": {"path": str(artifact.resolve()), "sha256": digest(artifact), "bytes": artifact.stat().st_size},
        "mesh_result_status": mesh_result.get("status"),
        "script_sha256": digest(Path(__file__)),
        "elapsed_s": time.perf_counter() - started,
        "scope": "Sparse source-owned capacitance block in F with layout [all saved mesh nodes, sorted external active nodes]. Only the frozen 2064 owner overlaps and their original C values are stamped; added trace/pad copper creates no invented G/C owner. The original partial-0 frequency coefficient is intentionally applied later. No contact contraction, raw source replay, mesh compilation, circuit/operator mutation, LU, or solve.",
    }
    l14_mass.atomic_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-check", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mesh-result", type=Path)
    parser.add_argument("--mesh-result-sha256")
    parser.add_argument("--mesh-npz", type=Path)
    parser.add_argument("--mesh-npz-sha256")
    parser.add_argument("--mesh-review", type=Path)
    parser.add_argument("--mesh-review-sha256")
    parser.add_argument("--max-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check(), sort_keys=True))
        return
    verify_fixed_inputs()
    _, ledger, fingerprints, areas, capacitance, _, _ = load_source_inputs()
    if args.dry_check:
        print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_L02_GC_MASS_SOURCE_DRY_CHECK", "owners": len(fingerprints), "external_active_identities": len({owner["external_active_index"] for owner in ledger["owners"]}), "area_total_um2": float(np.sum(areas)), "native_capacitance_total_f": float(np.sum(capacitance))}, sort_keys=True))
        return
    required = (args.output, args.mesh_result, args.mesh_result_sha256, args.mesh_npz, args.mesh_npz_sha256, args.mesh_review, args.mesh_review_sha256)
    require(all(value is not None for value in required), "actual projection requires --output and all three accepted mesh paths plus SHA-256 pins")
    output_preexisted = args.output.resolve().exists()
    try:
        result = run(args)
    except BaseException as exc:
        failure_path = args.output.resolve() / "failure.json"
        if not output_preexisted and args.output.resolve().is_dir() and not failure_path.exists():
            l14_mass.atomic_json(failure_path, {
                "program": PROGRAM,
                "version": VERSION,
                "status": "STOP_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "script_sha256": digest(Path(__file__)),
            })
        raise
    print(json.dumps({key: result[key] for key in ("status", "owner_contract", "mass", "output", "elapsed_s")}, sort_keys=True))


if __name__ == "__main__":
    main()
