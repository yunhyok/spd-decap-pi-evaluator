"""Independently review a saved L02 source-owned P1 capacitance block."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any
from zipfile import ZipFile

import numpy as np
import shapely
from scipy import sparse
from shapely.geometry import Polygon

import project_astra_l14_gc_mass as mass


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OWNER_COUNT = 2_064
EXTERNAL_COUNT = 1_696
TOTAL_CAPACITANCE_F = 5.909971854073682e-9
RELATIVE_TOLERANCE = 2.0e-9
ACCEPT_STATUS = "ACCEPT_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK_INDEPENDENT_REVIEW"
PRODUCER_SHA256 = "b50915a6ec98b9d09665eac678a101444d055c610d6f9601f1439b0fe06e76eb"
GUARD_HELPER_SHA256 = "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"
MESH_STATUS = "COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT"
MESH_REVIEW_STATUS = "ACCEPT_SAVED_L02_PAD_DOMAIN_MESH_AND_STIFFNESS_INDEPENDENT_REVIEW"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_file(path: Path, expected: str, label: str) -> dict[str, object]:
    path = path.resolve()
    require(len(expected) == 64 and set(expected) <= set("0123456789abcdef"), f"{label} SHA pin is invalid")
    require(sha(path) == expected, f"{label} SHA-256 differs")
    return {"path": str(path), "sha256": expected, "bytes": path.stat().st_size}


def decode(array: np.ndarray, label: str) -> Any:
    value = np.asarray(array)
    require(value.dtype == np.uint8 and value.ndim == 1, f"{label} is not packed uint8 JSON")
    return json.loads(value.tobytes().decode("utf-8"))


def same_binding(inputs: dict[str, Any], key: str, checked: dict[str, object]) -> None:
    value = inputs.get(key, {})
    require(value.get("sha256") == checked["sha256"], f"producer {key} SHA binding differs")
    require(Path(value["path"]).resolve() == Path(str(checked["path"])), f"producer {key} path binding differs")


def raw_block_structure(
    owner: np.ndarray, row: np.ndarray, column: np.ndarray, data: np.ndarray, mesh_nodes: int
) -> int:
    require(owner.ndim == row.ndim == column.ndim == data.ndim == 1, "raw owner mass arrays are not vectors")
    require(owner.shape == row.shape == column.shape == data.shape and len(data) > 0 and len(data) % 6 == 0,
            "raw owner mass array lengths differ")
    require(np.all(np.isfinite(data)) and owner.min() == 0 and owner.max() == OWNER_COUNT - 1,
            "raw owner mass values/owner range differ")
    require(np.all(owner[:-1] <= owner[1:]) and row.min() >= 0 and column.max() < mesh_nodes and np.all(row <= column),
            "raw owner mass ordering/index range differs")
    owners = owner.reshape(-1, 6)
    rows = row.reshape(-1, 6)
    columns = column.reshape(-1, 6)
    for offset in range(1, 6):
        require(np.array_equal(owners[:, 0], owners[:, offset]), "one local mass block crosses owners")
    require(
        np.array_equal(rows[:, 0], columns[:, 0])
        and np.array_equal(rows[:, 0], rows[:, 1])
        and np.array_equal(rows[:, 0], rows[:, 2])
        and np.array_equal(columns[:, 1], rows[:, 3])
        and np.array_equal(rows[:, 3], columns[:, 3])
        and np.array_equal(columns[:, 1], rows[:, 4])
        and np.array_equal(columns[:, 2], columns[:, 4])
        and np.array_equal(columns[:, 2], rows[:, 5])
        and np.array_equal(rows[:, 5], columns[:, 5])
        and np.all(columns[:, 0] < columns[:, 1])
        and np.all(columns[:, 1] < columns[:, 2]),
        "raw owner mass does not contain canonical six-entry triangle blocks",
    )
    require(np.all(np.bincount(owners[:, 0], minlength=OWNER_COUNT) > 0), "an owner has no raw mass block")
    return len(data) // 6


def reconstruct_block(
    owner: np.ndarray,
    row: np.ndarray,
    column: np.ndarray,
    data_um2: np.ndarray,
    density: np.ndarray,
    owner_external: np.ndarray,
    external_ids: np.ndarray,
    mesh_nodes: int,
) -> tuple[sparse.csc_matrix, np.ndarray, sparse.csr_matrix]:
    owner_count = len(density)
    off_diagonal = row != column
    owner64 = owner.astype(np.int64, copy=False)
    first = sparse.coo_matrix(
        (
            np.concatenate((data_um2, data_um2[off_diagonal])),
            (
                np.concatenate((owner64, owner64[off_diagonal])),
                np.concatenate((row, column[off_diagonal])),
            ),
        ),
        shape=(owner_count, mesh_nodes),
    ).tocsr()
    first.sum_duplicates()
    owner_area = np.asarray(first.sum(axis=1), dtype=np.float64).ravel()
    first_owner = np.repeat(np.arange(owner_count, dtype=np.int64), np.diff(first.indptr))
    external_slot = np.searchsorted(external_ids, owner_external)
    require(np.array_equal(external_ids[external_slot], owner_external), "owner external identity is absent from layout")
    external_column = mesh_nodes + external_slot
    upper = sparse.coo_matrix(
        (
            np.concatenate((
                data_um2 * density[owner64],
                -first.data * density[first_owner],
                owner_area * density,
            )),
            (
                np.concatenate((row, first.indices, external_column)),
                np.concatenate((column, external_column[first_owner], external_column)),
            ),
        ),
        shape=(mesh_nodes + len(external_ids), mesh_nodes + len(external_ids)),
    ).tocsc()
    upper.sum_duplicates()
    result = (upper + upper.T - sparse.diags(upper.diagonal(), format="csc")).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result, owner_area, first


def raw_local_psd(data: np.ndarray, *, batch: int = 131_072) -> float:
    blocks = data.reshape(-1, 6)
    minimum = float("inf")
    for begin in range(0, len(blocks), batch):
        values = blocks[begin:begin + batch]
        matrices = np.empty((len(values), 3, 3), dtype=np.float64)
        matrices[:, 0, :] = values[:, (0, 1, 2)]
        matrices[:, 1, :] = values[:, (1, 3, 4)]
        matrices[:, 2, :] = values[:, (2, 4, 5)]
        first = matrices @ np.ones(3, dtype=np.float64)
        area = np.sum(first, axis=1)
        tolerance = np.maximum(area, 1.0) * 1.0e-12
        eigenvalues = np.linalg.eigvalsh(matrices)[:, 0]
        require(np.all(np.isfinite(eigenvalues)) and np.all(area > 0.0), "raw local mass is invalid")
        require(np.all(eigenvalues >= -tolerance) and np.all(first >= -tolerance[:, None]),
                "raw local mass fails PSD/first-moment gate")
        minimum = min(minimum, float(np.min(eigenvalues)))
    return minimum


def triangle_position(triangles: np.ndarray, wanted: tuple[int, int, int]) -> int:
    low, high = 0, len(triangles)
    while low < high:
        middle = (low + high) // 2
        value = tuple(map(int, triangles[middle]))
        if value < wanted:
            low = middle + 1
        else:
            high = middle
    require(low < len(triangles) and tuple(map(int, triangles[low])) == wanted,
            "sampled raw block is not a saved mesh triangle")
    return low


def quadrature_mass(cut: Any, parent_vertices: np.ndarray) -> tuple[np.ndarray, float, int]:
    result = shapely.constrained_delaunay_triangles(cut)
    covered: list[Polygon] = []
    for geometry in result.geoms:
        if isinstance(geometry, Polygon) and cut.covers(geometry):
            require(len(geometry.exterior.coords) == 4 and not geometry.interiors and geometry.area > 0.0,
                    "sampled constrained triangulation emitted a non-triangle")
            covered.append(geometry)
    require(covered, "sampled cut constrained triangulation has no covered triangles")
    covered_area = float(sum((geometry.area for geometry in covered), 0.0))
    area_tolerance = max(float(cut.area), 1.0) * 2.0e-11
    require(abs(covered_area - float(cut.area)) <= area_tolerance
            and shapely.union_all(covered).symmetric_difference(cut).area <= area_tolerance,
            "sampled constrained triangulation does not cover the cut")
    origin = np.asarray(parent_vertices[0], dtype=np.float64)
    parent_local = np.asarray(parent_vertices, dtype=np.float64) - origin
    parent_system = np.vstack((parent_local.T, np.ones(3, dtype=np.float64)))
    parent_inverse = np.linalg.inv(parent_system)
    quadrature_barycentric = np.asarray(
        ((2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0),
         (1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0),
         (1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0)),
        dtype=np.float64,
    )
    matrix = np.zeros((3, 3), dtype=np.float64)
    for geometry in covered:
        vertices = np.asarray(geometry.exterior.coords, dtype=np.float64)[:-1] - origin
        points = quadrature_barycentric @ vertices
        basis = np.column_stack((points, np.ones(3, dtype=np.float64))) @ parent_inverse.T
        matrix += (float(geometry.area) / 3.0) * (basis.T @ basis)
    return matrix, covered_area, len(covered)


def geometric_spots(
    owner: np.ndarray,
    row: np.ndarray,
    column: np.ndarray,
    data: np.ndarray,
    xy: np.ndarray,
    triangles: np.ndarray,
    wkb_bytes: np.ndarray,
    wkb_offsets: np.ndarray,
) -> dict[str, object]:
    block_count = len(data) // 6
    samples = np.unique(np.linspace(0, block_count - 1, min(12, block_count), dtype=np.int64))
    maximum_mass_relative = 0.0
    maximum_area_relative = 0.0
    constrained_triangle_count = 0
    sampled_owners: list[int] = []
    for block_index in samples:
        begin = int(block_index) * 6
        owner_index = int(owner[begin])
        nodes = (int(row[begin]), int(column[begin + 1]), int(column[begin + 2]))
        triangle_position(triangles, nodes)
        vertices = xy[np.asarray(nodes, dtype=np.int64)]
        triangle = Polygon(vertices)
        overlap = shapely.from_wkb(wkb_bytes[wkb_offsets[owner_index]:wkb_offsets[owner_index + 1]].tobytes())
        cut = triangle.intersection(overlap)
        require(not cut.is_empty and cut.area > 0.0, "sampled raw block has no positive source overlap")
        direct, direct_area, subtriangle_count = quadrature_mass(cut, vertices)
        saved = np.asarray(
            ((data[begin], data[begin + 1], data[begin + 2]),
             (data[begin + 1], data[begin + 3], data[begin + 4]),
             (data[begin + 2], data[begin + 4], data[begin + 5])),
            dtype=np.float64,
        )
        scale = max(float(np.max(np.abs(direct), initial=0.0)), np.finfo(float).tiny)
        maximum_mass_relative = max(maximum_mass_relative, float(np.max(np.abs(saved - direct))) / scale)
        maximum_area_relative = max(maximum_area_relative, abs(float(np.sum(saved)) - direct_area) / direct_area)
        constrained_triangle_count += subtriangle_count
        sampled_owners.append(owner_index)
    require(maximum_mass_relative <= 5.0e-10 and maximum_area_relative <= RELATIVE_TOLERANCE,
            "sampled direct geometric mass differs")
    return {
        "sampled_local_blocks": len(samples),
        "sampled_owner_indices": sampled_owners,
        "maximum_direct_mass_relative_error": maximum_mass_relative,
        "maximum_direct_area_relative_error": maximum_area_relative,
        "sampled_constrained_triangle_count": constrained_triangle_count,
    }


def self_check() -> dict[str, object]:
    xy = np.asarray(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)), dtype=np.float64)
    triangles = np.asarray(((0, 1, 2),), dtype=np.int64)
    local = 0.5 * (np.ones((3, 3)) + np.eye(3)) / 12.0
    owner = np.zeros(6, dtype=np.int16)
    row = np.asarray((0, 0, 0, 1, 1, 2), dtype=np.int64)
    column = np.asarray((0, 1, 2, 1, 2, 2), dtype=np.int64)
    data = local[(np.asarray((0, 0, 0, 1, 1, 2)), np.asarray((0, 1, 2, 1, 2, 2)))]
    density = np.asarray((2.0e-12 / 0.5,), dtype=np.float64)
    external = np.asarray((7,), dtype=np.int64)
    matrix, areas, _ = reconstruct_block(owner, row, column, data, density, external, external, len(xy))
    require(np.allclose(areas, 0.5, rtol=0.0, atol=1.0e-15), "coupon owner area differs")
    difference = matrix - matrix.T
    difference.eliminate_zeros()
    require(difference.nnz == 0 and np.max(np.abs(np.asarray(matrix.sum(axis=1)).ravel())) < 1.0e-25,
            "coupon block is not symmetric/conservative")
    direct, error, area = mass.triangle_mass(Polygon(xy), xy, origin=xy[0])
    require(np.max(np.abs(direct - local)) < 1.0e-15 and np.max(np.abs(error)) < 1.0e-15 and area == 0.5,
            "coupon direct moment differs")
    independent, independent_area, subtriangles = quadrature_mass(Polygon(xy), xy)
    require(np.max(np.abs(independent - local)) < 1.0e-15
            and independent_area == 0.5 and subtriangles == 1,
            "coupon independent degree-2 quadrature differs")
    translated = np.asarray(((1.0e5, -2.0e5), (1.0e5 + 1.0e-3, -2.0e5),
                             (1.0e5, -2.0e5 + 2.0e-3)), dtype=np.float64)
    translated_polygon = Polygon(translated)
    translated_area = float(translated_polygon.area)
    translated_expected = translated_area * (np.ones((3, 3)) + np.eye(3)) / 12.0
    translated_mass, represented_area, translated_count = quadrature_mass(translated_polygon, translated)
    translated_scale = max(float(np.max(np.abs(translated_expected))), np.finfo(float).tiny)
    require(float(np.max(np.abs(translated_mass - translated_expected))) / translated_scale < 1.0e-12
            and represented_area == translated_area and translated_count == 1,
            "translated thin-triangle degree-2 quadrature differs")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_L02_GC_MASS_REVIEW_SELF_CHECK"}


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    reviewed = {
        "result": checked_file(args.result, args.result_sha256, "GC result"),
        "capacitance_npz": checked_file(args.capacitance_npz, args.capacitance_npz_sha256, "GC capacitance NPZ"),
        "guard": checked_file(args.guard, args.guard_sha256, "GC guard"),
        "mesh_result": checked_file(args.mesh_result, args.mesh_result_sha256, "mesh result"),
        "mesh_npz": checked_file(args.mesh_npz, args.mesh_npz_sha256, "mesh NPZ"),
        "mesh_review": checked_file(args.mesh_review, args.mesh_review_sha256, "mesh review"),
        "source_overlap_result": checked_file(args.source_overlap_result, args.source_overlap_result_sha256, "source overlap result"),
        "source_overlap_npz": checked_file(args.source_overlap_npz, args.source_overlap_npz_sha256, "source overlap NPZ"),
        "source_overlap_review": checked_file(args.source_overlap_review, args.source_overlap_review_sha256, "source overlap review"),
        "source_owner_ledger": checked_file(args.source_owner_ledger, args.source_owner_ledger_sha256, "source owner ledger"),
    }
    result = json.loads(args.result.read_bytes())
    guard = json.loads(args.guard.read_bytes())
    mesh_result = json.loads(args.mesh_result.read_bytes())
    mesh_review = json.loads(args.mesh_review.read_bytes())
    overlap_result = json.loads(args.source_overlap_result.read_bytes())
    overlap_review = json.loads(args.source_overlap_review.read_bytes())
    ledger = json.loads(args.source_owner_ledger.read_bytes())
    require(result.get("program") == PROGRAM and result.get("version") == VERSION
            and result.get("status") == "COMPLETED_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK", "GC result identity/status differs")
    require(guard.get("status") == "COMPLETED_NATIVE_WORKER" and guard.get("exit_code") == 0,
            "GC external worker did not complete cleanly")
    require(guard["elapsed_s"] <= guard["max_runtime_s"] and guard["sampled_peak_private_bytes"] <= guard["max_memory_bytes"],
            "GC external resource bound failed")
    driver = args.result.resolve().parent / "driver-at-run.py"
    reviewed["frozen_driver"] = checked_file(driver, result["script_sha256"], "GC frozen driver")
    require(result["script_sha256"] == PRODUCER_SHA256, "GC producer SHA is not the reviewed frozen producer")
    guard_helper = Path(__file__).resolve().with_name("probe_astra_fmm3d_runtime.py")
    reviewed["guard_helper"] = checked_file(guard_helper, GUARD_HELPER_SHA256, "external guard helper")
    worker_command = guard.get("worker_command")
    producer_path = Path(__file__).resolve().with_name("project_astra_l02_gc_mass.py")
    require(guard.get("driver_sha256") == GUARD_HELPER_SHA256
            and guard.get("driver_sha256_role")
            == "guard helper, custom worker bound separately by its frozen driver and result"
            and isinstance(worker_command, list) and len(worker_command) >= 3
            and Path(worker_command[2]).resolve() == producer_path,
            "GC external guard helper/worker command binding differs")
    require(Path(result["output"]["path"]).resolve() == args.capacitance_npz.resolve()
            and result["output"]["sha256"] == args.capacitance_npz_sha256, "GC output binding differs")
    for key, name in (("mesh_result", "mesh_result"), ("mesh_npz", "mesh_npz"), ("mesh_review", "mesh_review"),
                      ("overlap_result", "source_overlap_result"), ("overlap_npz", "source_overlap_npz"),
                      ("overlap_review", "source_overlap_review"), ("owner_ledger", "source_owner_ledger")):
        same_binding(result["inputs"], key, reviewed[name])
    helper = Path(result["inputs"]["triangle_mass_helper"]["path"])
    reviewed["triangle_mass_helper"] = checked_file(helper, result["inputs"]["triangle_mass_helper"]["sha256"], "triangle mass helper")

    require(mesh_result.get("status") == MESH_STATUS and mesh_review.get("status") == MESH_REVIEW_STATUS,
            "accepted mesh status differs")
    require(mesh_result["snapshot"]["sha256"] == args.mesh_npz_sha256
            and Path(mesh_result["snapshot"]["path"]).resolve() == args.mesh_npz.resolve(), "mesh snapshot binding differs")
    require(mesh_review["reviewed"]["result"]["sha256"] == args.mesh_result_sha256
            and mesh_review["reviewed"]["snapshot"]["sha256"] == args.mesh_npz_sha256, "mesh independent-review binding differs")
    require(overlap_result.get("status") == "COMPLETED_L02_GC_SOURCE_OVERLAPS"
            and overlap_review.get("review_status") == "ACCEPT_L02_GC_SOURCE_OVERLAPS"
            and ledger.get("status") == "COMPLETED_L02_GC_SOURCE_EDGE_ASSET_LEDGER", "source overlap/ledger status differs")
    require(overlap_review["reviewed_artifacts"]["result"]["sha256"] == args.source_overlap_result_sha256
            and overlap_review["reviewed_artifacts"]["overlap_polygons"]["sha256"] == args.source_overlap_npz_sha256
            and overlap_review["reviewed_artifacts"]["owner_ledger"]["sha256"] == args.source_owner_ledger_sha256,
            "source overlap independent-review binding differs")
    require(time.monotonic() - started <= args.max_seconds, "review time bound exceeded during binding checks")

    owners = ledger["owners"]
    require(len(owners) == OWNER_COUNT and len(overlap_result["owners"]) == OWNER_COUNT, "source owner count differs")
    fingerprints = [str(value["fingerprint"]) for value in owners]
    external_owner = np.asarray([int(value["external_active_index"]) for value in owners], dtype=np.int64)
    source_c = np.asarray([float.fromhex(value["nominal_capacitance_f_hex"]) for value in owners], dtype=np.float64)
    external_ids = np.unique(external_owner)
    require(fingerprints == sorted(fingerprints) and len(set(fingerprints)) == OWNER_COUNT
            and len(external_ids) == EXTERNAL_COUNT, "source owner order/external identities differ")
    require(float(np.sum(source_c, dtype=np.float64)) == TOTAL_CAPACITANCE_F, "source capacitance total differs")
    with ZipFile(args.source_overlap_npz) as archive:
        require(archive.testzip() is None, "source overlap NPZ CRC failed")
    with np.load(args.source_overlap_npz, allow_pickle=False) as source:
        require(all(source[name].dtype.kind != "O" for name in source.files), "object array in source overlap NPZ")
        wkb_bytes = np.asarray(source["wkb_bytes"], dtype=np.uint8).copy()
        wkb_offsets = np.asarray(source["wkb_offsets"], dtype=np.int64).copy()
        source_fingerprints = decode(source["owner_fingerprints_json_utf8"], "source fingerprints")
        source_area = np.asarray(source["overlap_area_um2"], dtype=np.float64).copy()
        source_npz_c = np.asarray(source["nominal_capacitance_f"], dtype=np.float64).copy()
    require(wkb_offsets.shape == (OWNER_COUNT + 1,) and wkb_offsets[0] == 0 and wkb_offsets[-1] == len(wkb_bytes),
            "source WKB offsets differ")
    receipt_owners = overlap_result["owners"]
    receipt_area = np.asarray([value["area_um2"] for value in receipt_owners], dtype=np.float64)
    receipt_c = np.asarray([value["nominal_capacitance_f"] for value in receipt_owners], dtype=np.float64)
    receipt_density = np.asarray([value["retained_native_density_f_per_um2"] for value in receipt_owners], dtype=np.float64)
    require(source_fingerprints == fingerprints == [value["owner_fingerprint"] for value in receipt_owners],
            "source owner fingerprints differ")
    require(np.array_equal(source_npz_c, source_c) and np.array_equal(source_c, receipt_c)
            and np.array_equal(source_area, receipt_area) and np.array_equal(source_c / source_area, receipt_density),
            "source area/C/density arrays differ bitwise")

    with ZipFile(args.mesh_npz) as archive:
        require(archive.testzip() is None, "mesh NPZ CRC failed")
    with np.load(args.mesh_npz, allow_pickle=False) as mesh:
        xy = np.asarray(mesh["node_xy_um"], dtype=np.float64).copy()
        triangles = np.asarray(mesh["triangles"], dtype=np.int64).copy()
    mesh_nodes = len(xy)
    require(xy.shape == (mesh_result["mesh_nodes"], 2) and triangles.shape == (mesh_result["mesh_triangles"], 3),
            "mesh array/result shape differs")
    with ZipFile(args.capacitance_npz) as archive:
        require(archive.testzip() is None, "GC capacitance NPZ CRC failed")
    with np.load(args.capacitance_npz, allow_pickle=False) as saved:
        require(all(saved[name].dtype.kind != "O" for name in saved.files), "object array in GC capacitance NPZ")
        cap_data = np.asarray(saved["capacitance_data_f"])
        cap_indices = np.asarray(saved["capacitance_indices"])
        cap_indptr = np.asarray(saved["capacitance_indptr"])
        cap_shape = tuple(map(int, np.asarray(saved["capacitance_shape"]).ravel()))
        saved_mesh_nodes = np.asarray(saved["mesh_node_count"], dtype=np.int64).ravel()
        saved_external = np.asarray(saved["external_active_indices"], dtype=np.int64).copy()
        saved_owner_external = np.asarray(saved["owner_external_active_indices"], dtype=np.int64).copy()
        layout = decode(saved["block_layout_json_utf8"], "block layout")
        source_hashes = decode(saved["source_geometry_array_sha256_json_utf8"], "source geometry hashes")
        mesh_hashes = decode(saved["mesh_geometry_array_sha256_json_utf8"], "mesh geometry hashes")
        saved_fingerprints = decode(saved["owner_fingerprints_json_utf8"], "saved fingerprints")
        saved_c = np.asarray(saved["owner_capacitance_f"], dtype=np.float64).copy()
        saved_source_area = np.asarray(saved["owner_source_overlap_area_um2"], dtype=np.float64).copy()
        saved_mesh_area = np.asarray(saved["owner_mesh_overlap_area_um2"], dtype=np.float64).copy()
        saved_density = np.asarray(saved["owner_density_f_per_um2"], dtype=np.float64).copy()
        saved_integrated_c = np.asarray(saved["owner_integrated_capacitance_f"], dtype=np.float64).copy()
        raw_owner = np.asarray(saved["owner_mass_owner_index"], dtype=np.int64).copy()
        raw_row = np.asarray(saved["owner_mass_row"], dtype=np.int64).copy()
        raw_column = np.asarray(saved["owner_mass_col"], dtype=np.int64).copy()
        raw_data = np.asarray(saved["owner_mass_data_um2"], dtype=np.float64).copy()
    require(cap_data.dtype == np.float64 and cap_indices.dtype.kind in "iu" and cap_indptr.dtype.kind in "iu",
            "saved CSC dtype differs")
    require(saved_mesh_nodes.tolist() == [mesh_nodes] and saved_external.tolist() == external_ids.tolist()
            and np.array_equal(saved_owner_external, external_owner), "saved block node layout differs")
    require(layout == {"order": ["all_saved_mesh_nodes", "sorted_external_active_nodes"],
                       "mesh_node_count": mesh_nodes, "external_active_count": EXTERNAL_COUNT}, "saved layout manifest differs")
    require(saved_fingerprints == fingerprints and np.array_equal(saved_c, source_c)
            and np.array_equal(saved_source_area, source_area) and np.array_equal(saved_density, source_c / source_area),
            "saved owner source arrays differ bitwise")
    expected_source_hashes = {
        "wkb_bytes": hashlib.sha256(wkb_bytes.tobytes()).hexdigest(),
        "wkb_offsets": hashlib.sha256(wkb_offsets.tobytes()).hexdigest(),
        "overlap_area_um2": hashlib.sha256(source_area.tobytes()).hexdigest(),
    }
    expected_mesh_hashes = {
        "node_xy_um": hashlib.sha256(xy.tobytes()).hexdigest(),
        "triangles": hashlib.sha256(triangles.tobytes()).hexdigest(),
    }
    require(source_hashes == expected_source_hashes == result["source_geometry_arrays"]["raw_array_sha256"],
            "source geometry array hashes differ")
    require(mesh_hashes == expected_mesh_hashes == result["mesh_geometry_arrays"]["raw_array_sha256"],
            "mesh geometry array hashes differ")
    require(result["source_geometry_arrays"]["container_sha256"] == args.source_overlap_npz_sha256
            and result["mesh_geometry_arrays"]["container_sha256"] == args.mesh_npz_sha256,
            "result source/mesh container SHA binding differs")
    local_block_count = raw_block_structure(raw_owner, raw_row, raw_column, raw_data, mesh_nodes)
    require(time.monotonic() - started <= args.max_seconds, "review time bound exceeded before sparse reconstruction")

    shape = (mesh_nodes + EXTERNAL_COUNT, mesh_nodes + EXTERNAL_COUNT)
    require(cap_shape == shape, "saved capacitance shape differs")
    capacitance = sparse.csc_matrix((cap_data, cap_indices, cap_indptr), shape=cap_shape)
    capacitance.check_format(full_check=True)
    require(capacitance.has_canonical_format and np.all(np.isfinite(capacitance.data)), "saved CSC is noncanonical/nonfinite")
    symmetry = capacitance - capacitance.T
    symmetry.eliminate_zeros()
    require(symmetry.nnz == 0, "saved capacitance CSC is nonsymmetric")
    scale = max(float(np.max(np.asarray(abs(capacitance).sum(axis=1)).ravel(), initial=0.0)), np.finfo(float).tiny)
    row_sum_relative = float(np.max(np.abs(np.asarray(capacitance.sum(axis=1)).ravel()), initial=0.0)) / scale
    require(row_sum_relative <= RELATIVE_TOLERANCE, "saved capacitance CSC is not conservative")
    minimum_local_eigenvalue = raw_local_psd(raw_data)
    reconstructed, owner_area, first = reconstruct_block(
        raw_owner, raw_row, raw_column, raw_data, saved_density, saved_owner_external, saved_external, mesh_nodes
    )
    difference = capacitance - reconstructed
    difference.eliminate_zeros()
    reconstruction_relative = float(np.max(np.abs(difference.data), initial=0.0)) / scale
    require(reconstruction_relative <= RELATIVE_TOLERANCE, "saved CSC does not reconstruct from raw owner masses")
    owner_area_relative = np.abs(owner_area - saved_mesh_area) / saved_source_area
    owner_capacitance = owner_area * saved_density
    owner_capacitance_relative = np.abs(owner_capacitance - saved_c) / saved_c
    require(float(np.max(owner_area_relative)) <= RELATIVE_TOLERANCE
            and float(np.max(owner_capacitance_relative)) <= RELATIVE_TOLERANCE
            and np.allclose(owner_capacitance, saved_integrated_c, rtol=RELATIVE_TOLERANCE, atol=0.0),
            "raw per-owner mass does not collapse to saved area/capacitance")
    require(np.all(np.asarray(first.sum(axis=1)).ravel() > 0.0), "an owner raw mass has no positive first moment")
    integrated_total = float(np.sum(saved_integrated_c, dtype=np.float64))
    integrated_total_relative = abs(integrated_total - TOTAL_CAPACITANCE_F) / TOTAL_CAPACITANCE_F
    maximum_saved_area_error = float(np.max(np.abs(saved_mesh_area - saved_source_area) / saved_source_area))
    maximum_saved_capacitance_error = float(np.max(np.abs(saved_integrated_c - saved_c) / saved_c))
    owner_contract = result["owner_contract"]
    require(owner_contract["partial_ordinal"] == 0
            and owner_contract["owner_count"] == OWNER_COUNT
            and owner_contract["fingerprints_sorted_and_exact"] is True
            and owner_contract["external_active_identity_count"] == EXTERNAL_COUNT
            and owner_contract["native_capacitance_total_f"] == TOTAL_CAPACITANCE_F
            and owner_contract["integrated_capacitance_total_f"] == integrated_total
            and owner_contract["integrated_capacitance_total_relative_error"] == integrated_total_relative
            and owner_contract["maximum_owner_capacitance_recollapse_relative_error"] == maximum_saved_capacitance_error
            and owner_contract["maximum_owner_area_relative_error"] == maximum_saved_area_error
            and owner_contract["area_relative_tolerance"] == RELATIVE_TOLERANCE,
            "result owner contract differs from saved/computed values")
    mass_result = result["mass"]
    require(mass_result["mesh_node_count"] == mesh_nodes
            and mass_result["mesh_triangle_count"] == len(triangles)
            and mass_result["expanded_shape"] == list(shape)
            and mass_result["expanded_nnz"] == capacitance.nnz
            and mass_result["sparse_block_layout"] == "[all saved mesh nodes, sorted external active nodes]"
            and mass_result["symmetric"] is True and mass_result["finite"] is True
            and mass_result["local_element_psd_gate_passed"] is True
            and mass_result["local_psd_absolute_tolerance"] == "max(element_area_um2,1)*1e-12"
            and mass_result["global_spectral_claim"] is None
            and mass_result["nonnegative_consistent_first_moments"] is True
            and mass_result["row_sum_relative_max"] == row_sum_relative
            and mass_result["full_triangle_analytic_count"] + mass_result["clipped_triangle_count"] == local_block_count,
            "result mass metadata differs from saved/computed values")
    reported_minimum = float(mass_result["minimum_local_triangle_mass_eigenvalue"])
    require(abs(reported_minimum - minimum_local_eigenvalue)
            <= max(abs(reported_minimum), abs(minimum_local_eigenvalue), 1.0) * 2.0e-12,
            "result minimum local mass eigenvalue differs from raw blocks")
    spots = geometric_spots(raw_owner, raw_row, raw_column, raw_data, xy, triangles, wkb_bytes, wkb_offsets)
    require(time.monotonic() - started <= args.max_seconds, "review time bound exceeded")

    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": ACCEPT_STATUS,
        "reviewed": reviewed,
        "checks": {
            "owner_count": OWNER_COUNT,
            "external_active_identity_count": EXTERNAL_COUNT,
            "mesh_node_count": mesh_nodes,
            "expanded_shape": list(shape),
            "expanded_nnz": int(capacitance.nnz),
            "local_raw_mass_block_count": local_block_count,
            "source_arrays_bitwise_exact": True,
            "csc_canonical_finite_symmetric": True,
            "csc_row_sum_relative_max": row_sum_relative,
            "raw_block_reconstruction_relative_error": reconstruction_relative,
            "owner_area_collapse_relative_max": float(np.max(owner_area_relative)),
            "owner_capacitance_collapse_relative_max": float(np.max(owner_capacitance_relative)),
            "native_capacitance_total_f": float(np.sum(saved_c, dtype=np.float64)),
            "integrated_capacitance_total_f": integrated_total,
            "minimum_local_triangle_mass_eigenvalue": minimum_local_eigenvalue,
            **spots,
        },
        "scope": "Independent hash/binding/source-array/sparse-layout/raw-owner-mass review with a deterministic small direct moment sample. It does not replay all 2064 overlays, compile a mesh, contract contacts, assemble a board, run LU, fit PowerSI, or claim accuracy.",
        "elapsed_s": time.monotonic() - started,
    }
    mass.atomic_json(args.output.resolve(), review)
    print(json.dumps({"status": review["status"], "review_sha256": sha(args.output.resolve()), "checks": review["checks"]}, sort_keys=True))
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    for name in (
        "result", "capacitance-npz", "guard", "mesh-result", "mesh-npz", "mesh-review",
        "source-overlap-result", "source-overlap-npz", "source-overlap-review", "source-owner-ledger",
    ):
        parser.add_argument(f"--{name}", type=Path)
        parser.add_argument(f"--{name}-sha256")
    parser.add_argument("--max-seconds", type=float, default=900.0)
    args = parser.parse_args()
    check = self_check()
    if args.self_check:
        print(json.dumps(check, sort_keys=True))
        return
    required = [value for key, value in vars(args).items() if key not in {"self_check", "max_seconds"}]
    if any(value is None for value in required):
        parser.error("--output and every result/NPZ/guard/mesh/source path plus SHA-256 pin are required")
    require(args.max_seconds > 0.0, "--max-seconds must be positive")
    run(args)


if __name__ == "__main__":
    main()
