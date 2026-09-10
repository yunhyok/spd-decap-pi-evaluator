"""Project frozen L14 GC owners onto exact P1 overlap mass blocks."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

import numpy as np
import shapely
from scipy import sparse
from shapely import STRtree
from shapely.geometry import MultiPolygon, Polygon

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from spd_decap_pi._core import services


PROJECTION = ROOT / "outputs/research/astra-l14-gc-projection-03"
MESH = ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz"
MESH_RECEIPT = ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05/verified-mesh-checkpoint.json"
RAW = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz"
INVENTORY = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/l14-gc-projection-inventory.json"
LEDGER = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/l14-island-external-current-ledger.json"
CANDIDATE = ROOT / "outputs/research/astra-step6e-loaded-boundary-01/loaded-sheet-candidate-final.json"
PROJECTION_RECEIPT = PROJECTION / "receipt.json"
PROJECTION_NPZ = PROJECTION / "gc-nodal-injection.npz"
SOURCE_CACHE = PROJECTION / "source"
PARTITION = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/hq-gc-source-edge-partition.json"
CONSERVATION = ROOT / "outputs/research/astra-l14-gc-mass-03/hq-independent-candidate-conservation.json"
EXPECTED_SHA256 = {
    "mesh": "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779",
    "mesh_receipt": "ab577767f16a6e0f51bfc3009c9fc91ff586218e23e4ecba3ee10c67bffa2f07",
    "raw": "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    "inventory": "4409cdccc3b3d488d8f3abdd2f1e8b09a488d3c18a5bace010c765a240bc397f",
    "ledger": "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b",
    "candidate": "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d",
    "projection_receipt": "c819c905e174bde9ce718fbbc053fb232c36d60dddde73a8aab0ae1ae2d9b98e",
    "projection_npz": "5d25cfd04533b2e92c5c82f0143801cf282b2b6f41f4e138295651ed1576bfa9",
    "partition": "e25498573fe85fb7c2e2a9de622f8ce9f92e60629fe8f22ff10e855d3e128288",
    "conservation": "32ac4e5f84d2602e1c38cdc94b587ff192c30d2c101c94eb0003469715d17678",
}
CANDIDATE_SHA256 = "521a70e96b0c89602e75352ae8d0a1da9fb478b95db52b94da210997917cba2e"
CANDIDATE_SIZE_BYTES = 7_488_067


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def decode_text(array: np.ndarray[Any, Any]) -> Any:
    value = np.asarray(array)
    require(value.dtype == np.uint8 and value.ndim == 1, "packed text is not uint8")
    return json.loads(value.tobytes().decode("utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_npz(path: Path, **arrays: np.ndarray[Any, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def polygon_map(layer: str, net: str, asset_sha: str, compressed: bytes) -> dict[str, Any]:
    payload = services._decode_spd_geometry_asset(asset_sha, compressed)
    shape = services._ordered_spd_geometry(payload)
    require(shape is not None and not shape.is_empty and shape.is_valid, "source geometry is invalid")
    rows = services._spd_surface_islands(layer=layer, net=net, asset_sha256=asset_sha, shape=shape)
    result = {str(island_id): polygon for island_id, polygon in rows}
    require(len(result) == len(rows), "source island IDs are duplicated")
    return result


def _ring_moments(coords: Any, origin: np.ndarray | None = None) -> np.ndarray:
    points = np.asarray(coords, dtype=np.float64)
    if len(points) < 4:
        return np.zeros(6, dtype=np.float64)
    if not np.array_equal(points[0], points[-1]):
        points = np.vstack((points, points[0]))
    if origin is not None:
        points = points - np.asarray(origin, dtype=np.float64)
    x0, y0 = points[:-1, 0], points[:-1, 1]
    x1, y1 = points[1:, 0], points[1:, 1]
    cross = x0 * y1 - x1 * y0
    return np.asarray(
        (
            0.5 * np.sum(cross),
            np.sum((x0 + x1) * cross) / 6.0,
            np.sum((y0 + y1) * cross) / 6.0,
            np.sum((x0 * x0 + x0 * x1 + x1 * x1) * cross) / 12.0,
            np.sum((2.0 * x0 * y0 + x0 * y1 + x1 * y0 + 2.0 * x1 * y1) * cross) / 24.0,
            np.sum((y0 * y0 + y0 * y1 + y1 * y1) * cross) / 12.0,
        ),
        dtype=np.float64,
    )


def _positive_ring_moments(coords: Any, origin: np.ndarray | None = None) -> np.ndarray:
    result = _ring_moments(coords, origin)
    return result if result[0] >= 0.0 else -result


def _polygon_parts(geometry: Any) -> list[Polygon]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    parts: list[Polygon] = []
    for child in getattr(geometry, "geoms", ()):
        parts.extend(_polygon_parts(child))
    return parts


def geometry_moments(geometry: Any, origin: np.ndarray | None = None) -> np.ndarray:
    result = np.zeros(6, dtype=np.float64)
    for polygon in _polygon_parts(geometry):
        result += _positive_ring_moments(polygon.exterior.coords, origin)
        for hole in polygon.interiors:
            result -= _positive_ring_moments(hole.coords, origin)
    require(np.all(np.isfinite(result)), "nonfinite polygon moments")
    require(result[0] >= -1.0e-12, "negative polygon area")
    return result


def barycentric_coefficients(vertices: np.ndarray, origin: np.ndarray | None = None) -> np.ndarray:
    shifted = vertices if origin is None else vertices - np.asarray(origin, dtype=np.float64)
    a, b, c = shifted
    determinant = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    require(np.isfinite(determinant) and abs(determinant) > 0.0, "degenerate mesh triangle")
    return np.asarray(
        (
            ((b[0] * c[1] - c[0] * b[1]), (b[1] - c[1]), (c[0] - b[0])),
            ((c[0] * a[1] - a[0] * c[1]), (c[1] - a[1]), (a[0] - c[0])),
            ((a[0] * b[1] - b[0] * a[1]), (a[1] - b[1]), (b[0] - a[0])),
        ),
        dtype=np.float64,
    ) / determinant


def linear_product_integral(left: np.ndarray, right: np.ndarray, moments: np.ndarray) -> float:
    a0, ax, ay = left
    b0, bx, by = right
    area, mx, my, mxx, mxy, myy = moments
    return float(
        a0 * b0 * area
        + (a0 * bx + ax * b0) * mx
        + (a0 * by + ay * b0) * my
        + ax * bx * mxx
        + (ax * by + ay * bx) * mxy
        + ay * by * myy
    )


def triangle_mass(cut: Any, vertices: np.ndarray, origin: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    moments = geometry_moments(cut, origin)
    coefficients = barycentric_coefficients(vertices, origin)
    mass = np.asarray(
        [[linear_product_integral(coefficients[i], coefficients[j], moments) for j in range(3)] for i in range(3)],
        dtype=np.float64,
    )
    mass = (mass + mass.T) * 0.5
    first = mass @ np.ones(3, dtype=np.float64)
    direct_first = np.asarray(
        [coefficients[i, 0] * moments[0] + coefficients[i, 1] * moments[1] + coefficients[i, 2] * moments[2] for i in range(3)],
        dtype=np.float64,
    )
    return mass, first - direct_first, float(moments[0])


def _diagnostic_matrix(mass: np.ndarray, area: float) -> dict[str, Any]:
    symmetric = (mass + mass.T) * 0.5
    return {
        "mass": [[float(value) for value in row] for row in symmetric],
        "eigenvalues": [float(value) for value in np.linalg.eigvalsh(symmetric)],
        "row_sum": [float(value) for value in symmetric.sum(axis=1)],
        "row_sum_total_area_error": float(abs(float(np.sum(symmetric)) - area)),
        "finite": bool(np.all(np.isfinite(symmetric))),
    }


def diagnose_first_cut(
    *,
    owner: dict[str, Any],
    partial: dict[str, Any],
    l14_polygons: dict[str, Any],
    polygons_by_asset: dict[str, dict[str, Any]],
    tree: STRtree,
    triangle_geometry: np.ndarray,
    triangles: np.ndarray,
    node_xy: np.ndarray,
    tile_keys: np.ndarray,
) -> dict[str, Any]:
    external_asset = next(asset for asset in partial["external_geometry_assets"] if str(asset["net"]).casefold() == str(owner["external_net"]).casefold())
    overlap = l14_polygons[owner["l14_island_id"]].intersection(polygons_by_asset[external_asset["asset_sha256"]][owner["external_island_id"]])
    candidates = np.asarray(tree.query(overlap), dtype=np.int64)
    candidates = candidates[np.argsort(tile_keys[candidates], kind="stable")]
    keys = tile_keys[candidates]
    boundaries = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        local_candidates = candidates[start:stop]
        local_points = node_xy[triangles[local_candidates]]
        minimum = np.min(local_points, axis=(0, 1))
        maximum = np.max(local_points, axis=(0, 1))
        local_overlap = shapely.intersection(overlap, shapely.box(float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])))
        cuts = shapely.intersection(triangle_geometry[local_candidates], local_overlap)
        areas = np.asarray(shapely.area(cuts), dtype=np.float64)
        positive = np.flatnonzero(areas > 0.0)
        if positive.size == 0:
            continue
        position = int(positive[0])
        triangle_index = int(local_candidates[position])
        cut = cuts[position]
        vertices = node_xy[triangles[triangle_index]]
        global_mass, global_error, area = triangle_mass(cut, vertices)
        shifted_mass, shifted_error, shifted_area = triangle_mass(cut, vertices, origin=vertices[0])
        return {
            "scope": "OWNER0_FIRST_POSITIVE_CUT_ONLY",
            "owner_index": 0,
            "fingerprint": owner["fingerprint"],
            "triangle_index": triangle_index,
            "tile_key": int(tile_keys[triangle_index]),
            "triangle_vertices_um": [[float(value) for value in row] for row in vertices],
            "triangle_local_origin_um": [float(value) for value in vertices[0]],
            "cut_area_um2_global": area,
            "cut_area_um2_shifted": shifted_area,
            "global": _diagnostic_matrix(global_mass, area) | {"first_moment_error_max": float(np.max(np.abs(global_error), initial=0.0))},
            "shifted": _diagnostic_matrix(shifted_mass, shifted_area) | {"first_moment_error_max": float(np.max(np.abs(shifted_error), initial=0.0))},
            "mass_max_abs_difference": float(np.max(np.abs(global_mass - shifted_mass), initial=0.0)),
            "eigen_min_difference": float(np.min(np.linalg.eigvalsh(global_mass)) - np.min(np.linalg.eigvalsh(shifted_mass))),
            "diagnosis": "global x/y second moments suffer cancellation at board coordinates; triangle-local translation is used for production mass evaluation",
        }
    raise ValueError("owner 0 has no positive cut for diagnostic")


def triangle_moments_quadrature(vertices: np.ndarray) -> np.ndarray:
    a, b, c = vertices
    area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) * 0.5
    points = np.asarray(((2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0), (1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0), (1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0)))
    xy = points @ vertices
    weight = area / 3.0
    x, y = xy[:, 0], xy[:, 1]
    return np.asarray((area, np.sum(weight * x), np.sum(weight * y), np.sum(weight * x * x), np.sum(weight * x * y), np.sum(weight * y * y)))


def finalize_upper(rows: np.ndarray, cols: np.ndarray, data: np.ndarray, shape: tuple[int, int]) -> tuple[sparse.csc_matrix, sparse.csc_matrix]:
    upper = sparse.coo_matrix((data, (rows, cols)), shape=shape).tocsc()
    upper.sum_duplicates()
    diagonal = sparse.diags(upper.diagonal(), format="csc")
    full = (upper + upper.T - diagonal).tocsc()
    full.sum_duplicates()
    full.eliminate_zeros()
    return upper, full


def sum_by_slot(values: np.ndarray, slots: np.ndarray, slot_count: int) -> np.ndarray:
    require(values.ndim == slots.ndim == 1 and len(values) == len(slots), "slot aggregation shapes differ")
    require(np.all((slots >= 0) & (slots < slot_count)), "slot aggregation index differs")
    result = np.zeros(slot_count, dtype=values.dtype)
    np.add.at(result, slots, values)
    return result


def self_check() -> None:
    vertices = np.asarray(((0.0, 0.0), (4.0, 0.0), (0.0, 3.0)), dtype=np.float64)
    triangle = Polygon(vertices)
    mass, error, area = triangle_mass(triangle, vertices)
    expected = area / 12.0 * (np.ones((3, 3)) + np.eye(3))
    require(np.max(np.abs(mass - expected)) <= 1.0e-12, "full triangle mass formula failed")
    require(np.max(np.abs(error)) <= 1.0e-12, "full triangle first moment failed")
    offset_vertices = vertices + np.asarray((1.0e9, -1.0e9))
    offset_mass, offset_error, offset_area = triangle_mass(Polygon(offset_vertices), offset_vertices, origin=offset_vertices[0])
    require(np.max(np.abs(offset_mass - expected)) <= 1.0e-12 and abs(offset_area - area) <= 1.0e-12, "large-offset local mass failed")
    require(np.max(np.abs(offset_error)) <= 1.0e-12, "large-offset local first moment failed")

    outer = Polygon(((0, 0), (4, 0), (4, 4), (0, 4)), holes=(((1, 1), (2, 1), (2, 2), (1, 2)),))
    second = Polygon(((6, 0), (8, 0), (6, 2)))
    multi = MultiPolygon((outer, second))
    exact = geometry_moments(multi)
    cells = (
        ((0, 0), (4, 0), (4, 1)), ((0, 0), (4, 1), (0, 1)),
        ((0, 2), (4, 2), (4, 4)), ((0, 2), (4, 4), (0, 4)),
        ((0, 1), (1, 1), (1, 2)), ((0, 1), (1, 2), (0, 2)),
        ((2, 1), (4, 1), (4, 2)), ((2, 1), (4, 2), (2, 2)),
        ((6, 0), (8, 0), (6, 2)),
    )
    quadrature = sum((triangle_moments_quadrature(np.asarray(cell, dtype=np.float64)) for cell in cells), np.zeros(6))
    require(np.max(np.abs(exact - quadrature)) <= 1.0e-12, "holed multipolygon degree-2 quadrature failed")

    rows = np.asarray((0, 0, 1), dtype=np.int64)
    cols = np.asarray((0, 1, 1), dtype=np.int64)
    data = np.asarray((2.0, 3.0, 5.0), dtype=np.float64)
    _, aggregate = finalize_upper(rows, cols, data, (2, 2))
    require(np.array_equal(aggregate.toarray(), np.asarray(((2.0, 3.0), (3.0, 5.0)))), "canonical upper mirror doubled an offdiagonal")
    shared_rows = np.asarray((0, 0, 1, 0, 0, 1, 0, 0, 2), dtype=np.int64)
    shared_cols = np.asarray((0, 1, 1, 0, 1, 1, 0, 2, 2), dtype=np.int64)
    shared_data = np.asarray((1.0, -1.0, 1.0, 2.0, -2.0, 2.0, 4.0, -4.0, 4.0), dtype=np.float64)
    require(len(shared_rows) == len(shared_cols) == len(shared_data), "shared-terminal COO lengths differ")
    _, shared = finalize_upper(shared_rows, shared_cols, shared_data, (3, 3))
    require(np.array_equal(shared.toarray(), np.asarray(((7.0, -3.0, -4.0), (-3.0, 3.0, 0.0), (-4.0, 0.0, 4.0)))), "shared-terminal diagonal aggregation failed")
    require(np.max(np.abs(np.asarray(shared.sum(axis=1)).ravel())) == 0.0, "shared-terminal rowsum failed")
    scales = np.asarray((2.0 + 1.0j, 3.0 + 2.0j, 5.0 + 3.0j))
    owners = np.asarray((0, 0, 0, 1, 1, 1, 2, 2, 2), dtype=np.int64)
    _, physical = finalize_upper(shared_rows, shared_cols, shared_data * scales[owners], (3, 3))
    require(np.max(np.abs(np.asarray(physical.sum(axis=1)).ravel())) <= 1.0e-14, "shared-terminal physical rowsum failed")
    require(abs(physical[1, 1] - (scales[0] + 2.0 * scales[1])) <= 1.0e-14 and abs(physical[2, 2] - 4.0 * scales[2]) <= 1.0e-14, "shared-terminal physical recollapse failed")
    owner_slots = np.asarray((0, 0, 1), dtype=np.int64)
    owner_areas = np.asarray((1.0, 2.0, 4.0))
    require(np.array_equal(sum_by_slot(owner_areas, owner_slots, 2), np.asarray((3.0, 4.0))), "aliased external area diagonal failed")
    require(np.array_equal(sum_by_slot(owner_areas * scales, owner_slots, 2), np.asarray((1.0 * scales[0] + 2.0 * scales[1], 4.0 * scales[2]))), "aliased external physical diagonal failed")


def append_upper_mass(owner_index: int, nodes: np.ndarray, mass: np.ndarray, owner_rows: list[int], owner_cols: list[int], owner_ids: list[int], owner_values: list[float]) -> None:
    for local_i in range(3):
        for local_j in range(local_i, 3):
            value = float(mass[local_i, local_j])
            if value == 0.0:
                continue
            owner_ids.append(owner_index)
            owner_rows.append(int(nodes[local_i]))
            owner_cols.append(int(nodes[local_j]))
            owner_values.append(value)


def run(output_dir: Path, max_runtime_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    inputs = {
        "mesh": MESH,
        "mesh_receipt": MESH_RECEIPT,
        "raw": RAW,
        "inventory": INVENTORY,
        "ledger": LEDGER,
        "candidate": CANDIDATE,
        "projection_receipt": PROJECTION_RECEIPT,
        "projection_npz": PROJECTION_NPZ,
    }
    input_receipts: dict[str, Any] = {}
    for name, path in inputs.items():
        require(path.is_file(), f"missing input {path}")
        actual = file_sha256(path)
        require(actual == EXPECTED_SHA256[name], f"{name} hash differs")
        input_receipts[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
        require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded")

    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    projection_receipt = json.loads(PROJECTION_RECEIPT.read_text(encoding="utf-8"))
    mesh_receipt = json.loads(MESH_RECEIPT.read_text(encoding="utf-8"))
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    require(projection_receipt["status"] == "COMPLETED_SOURCE_EXACT_L14_GC_P1_INJECTION", "projection03 is incomplete")
    require(mesh_receipt["status"] == "VERIFIED_SAVED_L14_MESH_CHECKPOINT", "mesh checkpoint is not verified")
    require(inventory["mapping"]["source_owner_count"] == 224, "source owner count differs")
    require(candidate["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0", "candidate rail differs")
    owners: list[dict[str, Any]] = []
    for partial in inventory["mapping"]["partials"]:
        owners.extend(partial["owners"])
    require(len(owners) == 224, "owner order differs")
    projection_npz = np.load(PROJECTION_NPZ, allow_pickle=False)
    try:
        projection_fingerprints = decode_text(projection_npz["owner_fingerprints_json_utf8"])
        require(projection_fingerprints == [owner["fingerprint"] for owner in owners], "projection owner order differs")
        saved_injection = np.asarray(projection_npz["gc_nodal_injection_a"], dtype=np.complex128)
        saved_owner_current = np.asarray(projection_npz["owner_current_into_l14_a"], dtype=np.complex128)
        require(saved_injection.shape == (171_957,) and saved_owner_current.shape == (224,), "projection shapes differ")
    finally:
        projection_npz.close()

    asset_records: dict[str, dict[str, Any]] = {}
    l14_geometry = candidate["candidate_component"]["geometry"]
    asset_records[l14_geometry["compressed_sha256"]] = {
        "layer": candidate["candidate_component"]["layer"],
        "net": candidate["candidate_component"]["net"],
        "member": l14_geometry["member"],
    }
    for partial in inventory["mapping"]["partials"]:
        for asset in partial["external_geometry_assets"]:
            asset_records[asset["asset_sha256"]] = dict(asset)
    require(len(asset_records) == 4, "geometry asset count differs")
    polygons_by_asset: dict[str, dict[str, Any]] = {}
    geometry_receipts: list[dict[str, Any]] = []
    for asset_sha in sorted(asset_records):
        record = asset_records[asset_sha]
        cached = SOURCE_CACHE / Path(str(record["member"])).name
        require(cached.is_file(), "materialized source geometry is absent")
        compressed = cached.read_bytes()
        require(sha256(compressed).hexdigest() == asset_sha, "materialized geometry hash differs")
        polygons = polygon_map(str(record["layer"]), str(record["net"]), asset_sha, compressed)
        polygons_by_asset[asset_sha] = polygons
        geometry_receipts.append({"layer": record["layer"], "net": record["net"], "member": record["member"], "sha256": asset_sha, "size_bytes": len(compressed), "source_island_count": len(polygons)})

    mesh = np.load(MESH, allow_pickle=False)
    raw = np.load(RAW, allow_pickle=False)
    try:
        node_xy = np.asarray(mesh["node_xy_um"], dtype=np.float64)
        triangles = np.asarray(mesh["triangles"], dtype=np.int64)
        require(node_xy.shape == (171_957, 2) and triangles.shape == (214_873, 3), "mesh shape differs")
        triangle_geometry = np.asarray(shapely.polygons(node_xy[triangles]), dtype=object)
        tree = STRtree(triangle_geometry)
        centers = np.mean(node_xy[triangles], axis=1)
        minimum_xy = np.min(node_xy, axis=0)
        tile_xy = np.floor((centers - minimum_xy) / 1024.0).astype(np.int64)
        tile_keys = tile_xy[:, 0] * 1_000_000 + tile_xy[:, 1]
        global_to_active = np.asarray(raw["global_to_active_indices"], dtype=np.int64)
        target_active = int(global_to_active[int(inventory["mapping"]["original_l14_reduced_index"])])
        require(target_active >= 0, "L14 quotient is inactive")
    finally:
        mesh.close()
        raw.close()

    l14_asset_sha = l14_geometry["compressed_sha256"]
    l14_polygons = polygons_by_asset[l14_asset_sha]
    expected_l14 = {str(row["island_id"]) for row in ledger["islands"]}
    require(expected_l14 == set(l14_polygons), "L14 source island IDs differ")

    atomic_json(
        output_dir / "diagnosis.json",
        diagnose_first_cut(
            owner=owners[0],
            partial=inventory["mapping"]["partials"][int(owners[0]["partial_ordinal"]) - 6],
            l14_polygons=l14_polygons,
            polygons_by_asset=polygons_by_asset,
            tree=tree,
            triangle_geometry=triangle_geometry,
            triangles=triangles,
            node_xy=node_xy,
            tile_keys=tile_keys,
        ),
    )

    owner_rows: list[int] = []
    owner_cols: list[int] = []
    owner_ids: list[int] = []
    owner_values: list[float] = []
    expanded_rows: list[int] = []
    expanded_cols: list[int] = []
    expanded_values: list[float] = []
    owner_bindings: list[dict[str, Any]] = []
    owner_area: list[float] = []
    owner_capacitance: list[float] = []
    owner_density: list[float] = []
    owner_coefficient: list[complex] = []
    owner_branch_y: list[complex] = []
    owner_first_errors: list[float] = []
    owner_psd_min: list[float] = []
    owner_weight_errors: list[float] = []
    owner_area_identity_errors: list[float] = []
    owner_physical_rowsum_residuals: list[float] = []
    owner_support: list[int] = []
    owner_intersections: list[int] = []
    shadow_injection = np.zeros(len(node_xy), dtype=np.complex128)
    control = {"seen": 0, "count": 0, "maximum_area_abs_error_um2": 0.0, "maximum_moment_abs_error_um3": 0.0}
    current_by_partial: dict[int, complex] = {6: 0.0j, 7: 0.0j}
    external_global_ids = sorted({int(owner["external_global_reduced_index"]) for owner in owners})
    external_column = {value: len(node_xy) + index for index, value in enumerate(external_global_ids)}
    progress_path = output_dir / "progress.jsonl"

    for owner_index, owner in enumerate(owners):
        with progress_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "owner_start", "owner_index": owner_index, "fingerprint": owner["fingerprint"], "elapsed_s": time.perf_counter() - started}) + "\n")
        partial_ordinal = int(owner["partial_ordinal"])
        partial = inventory["mapping"]["partials"][partial_ordinal - 6]
        coefficient = complex(*partial["actual_1mhz_dispersion_admittance_scale_s_per_f"])
        capacitance = float.fromhex(owner["capacitance_f_hex"])
        external_global = int(owner["external_global_reduced_index"])
        external_node = external_column[external_global]
        external_asset = next(asset for asset in partial["external_geometry_assets"] if str(asset["net"]).casefold() == str(owner["external_net"]).casefold())
        overlap = l14_polygons[owner["l14_island_id"]].intersection(polygons_by_asset[external_asset["asset_sha256"]][owner["external_island_id"]])
        overlap_area = float(overlap.area)
        implied_area = float(owner["implied_overlap_area_um2"])
        require(overlap_area > 0.0 and abs(overlap_area - implied_area) / implied_area <= 2.0e-10, "source overlap area differs")
        density = capacitance / overlap_area
        branch_y = coefficient * capacitance
        candidates = np.asarray(tree.query(overlap), dtype=np.int64)
        require(candidates.size > 0, "owner overlap has no mesh triangle")
        candidates = candidates[np.argsort(tile_keys[candidates], kind="stable")]
        keys = tile_keys[candidates]
        boundaries = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
        owner_mass_area = 0.0
        owner_first: dict[int, float] = {}
        owner_node_set: set[int] = set()
        positive_intersections = 0
        first_error = 0.0
        for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
            local_candidates = candidates[start:stop]
            local_points = node_xy[triangles[local_candidates]]
            minimum = np.min(local_points, axis=(0, 1))
            maximum = np.max(local_points, axis=(0, 1))
            local_overlap = shapely.intersection(overlap, shapely.box(float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])))
            cuts = shapely.intersection(triangle_geometry[local_candidates], local_overlap)
            areas = np.asarray(shapely.area(cuts), dtype=np.float64)
            positive = np.flatnonzero(areas > 0.0)
            for local_position in positive:
                tri_index = int(local_candidates[local_position])
                cut = cuts[local_position]
                vertices = node_xy[triangles[tri_index]]
                mass, moment_error, area = triangle_mass(cut, vertices, origin=vertices[0])
                require(area > 0.0 and np.all(np.isfinite(mass)), "triangle mass is invalid")
                first = mass @ np.ones(3, dtype=np.float64)
                first_error = max(first_error, float(np.max(np.abs(moment_error), initial=0.0)))
                eig_min = float(np.min(np.linalg.eigvalsh(mass)))
                owner_psd_min.append(eig_min)
                require(eig_min >= -max(area, 1.0) * 1.0e-12, "triangle mass is non-PSD")
                nodes = triangles[tri_index]
                owner_mass_area += area
                positive_intersections += 1
                owner_node_set.update(int(value) for value in nodes)
                for local_i, node in enumerate(nodes):
                    owner_first[int(node)] = owner_first.get(int(node), 0.0) + float(first[local_i])
                    shadow_injection[int(node)] += saved_owner_current[owner_index] * density / capacitance * float(first[local_i])
                append_upper_mass(owner_index, nodes, mass, owner_rows, owner_cols, owner_ids, owner_values)
                for local_i, row in enumerate(nodes):
                    for local_j, col in enumerate(nodes):
                        first_node = min(int(row), int(col))
                        second_node = max(int(row), int(col))
                        expanded_rows.append(first_node); expanded_cols.append(second_node); expanded_values.append(float(mass[local_i, local_j]))
                positions = np.arange(int(control["seen"]), int(control["seen"]) + 1)
                sample = np.flatnonzero((positions < 16) | (positions % 4096 == 0))
                if sample.size:
                    direct_cut = shapely.intersection(triangle_geometry[tri_index], overlap)
                    direct_moments = geometry_moments(direct_cut, origin=vertices[0])
                    area_error = abs(direct_moments[0] - area)
                    moment_abs = float(np.max(np.abs(direct_moments - geometry_moments(cut, origin=vertices[0])), initial=0.0))
                    control["count"] += 1
                    control["maximum_area_abs_error_um2"] = max(float(control["maximum_area_abs_error_um2"]), area_error)
                    control["maximum_moment_abs_error_um3"] = max(float(control["maximum_moment_abs_error_um3"]), moment_abs)
                control["seen"] += 1
        require(positive_intersections > 0, "owner overlap has no positive intersection")
        area_error = abs(owner_mass_area - overlap_area) / overlap_area
        require(area_error <= 2.0e-9, "mass intersections do not cover owner overlap once")
        first_vector = np.asarray(list(owner_first.values()), dtype=np.float64)
        require(abs(float(np.sum(first_vector)) - overlap_area) / overlap_area <= 2.0e-9, "owner first moment does not recollapse to area")
        area_identity_error = abs(float(np.sum(first_vector)) - overlap_area)
        for node, first in owner_first.items():
            expanded_rows.append(min(node, external_node))
            expanded_cols.append(max(node, external_node))
            expanded_values.append(-first)
        expanded_rows.append(external_node); expanded_cols.append(external_node); expanded_values.append(overlap_area)
        owner_weight_errors.append(area_identity_error / overlap_area)
        owner_area_identity_errors.append(area_identity_error)
        owner_physical_rowsum_residuals.append(abs(coefficient * density * area_identity_error))
        owner_area.append(overlap_area)
        owner_capacitance.append(capacitance)
        owner_density.append(density)
        owner_coefficient.append(coefficient)
        owner_branch_y.append(branch_y)
        owner_first_errors.append(first_error)
        owner_support.append(len(owner_node_set))
        owner_intersections.append(positive_intersections)
        owner_bindings.append({
            "owner_index": owner_index,
            "fingerprint": owner["fingerprint"],
            "partial_ordinal": partial_ordinal,
            "l14_island_id": owner["l14_island_id"],
            "external_island_id": owner["external_island_id"],
            "external_global_reduced_index": external_global,
            "external_active_index": int(owner["external_active_index"]),
            "external_reduced_node_id": owner["external_reduced_node_id"],
            "external_net": owner["external_net"],
            "capacitance_f": capacitance,
            "overlap_area_um2": overlap_area,
            "density_f_per_um2": density,
            "dispersion_s_per_f": [coefficient.real, coefficient.imag],
            "branch_admittance_s": [branch_y.real, branch_y.imag],
        })
        current_by_partial[partial_ordinal] += saved_owner_current[owner_index]
        with progress_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "owner_done", "owner_index": owner_index, "positive_triangle_intersections": positive_intersections, "elapsed_s": time.perf_counter() - started}) + "\n")
        require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded during mass projection")

    candidate_npz_path = output_dir / "owner-mass-candidate-unvalidated.npz"
    atomic_npz(
        candidate_npz_path,
        owner_mass_owner_index=np.asarray(owner_ids, dtype=np.int16),
        owner_mass_row=np.asarray(owner_rows, dtype=np.int64),
        owner_mass_col=np.asarray(owner_cols, dtype=np.int64),
        owner_mass_data_um2=np.asarray(owner_values, dtype=np.float64),
        owner_fingerprints_json_utf8=np.frombuffer(json.dumps([owner["fingerprint"] for owner in owners], separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
        owner_bindings_json_utf8=np.frombuffer(json.dumps(owner_bindings, separators=(",", ":"), allow_nan=False).encode("utf-8"), dtype=np.uint8),
        owner_overlap_area_um2=np.asarray(owner_area, dtype=np.float64),
        owner_area_identity_error_um2=np.asarray(owner_area_identity_errors, dtype=np.float64),
        owner_density_f_per_um2=np.asarray(owner_density, dtype=np.float64),
        owner_dispersion_s_per_f=np.asarray(owner_coefficient, dtype=np.complex128),
        owner_branch_admittance_s=np.asarray(owner_branch_y, dtype=np.complex128),
    )
    candidate_sha256 = file_sha256(candidate_npz_path)

    expanded_size = len(node_xy) + len(external_global_ids)
    upper = sparse.coo_matrix((np.asarray(expanded_values, dtype=np.float64), (np.asarray(expanded_rows, dtype=np.int64), np.asarray(expanded_cols, dtype=np.int64))), shape=(expanded_size, expanded_size)).tocsc()
    upper.sum_duplicates()
    diagonal = sparse.diags(upper.diagonal(), format="csc")
    expanded = (upper + upper.T - diagonal).tocsc()
    expanded.sum_duplicates()
    expanded.eliminate_zeros()
    require(np.all(np.isfinite(expanded.data)), "expanded mass is nonfinite")
    symmetry_error = expanded - expanded.T
    symmetry_error.eliminate_zeros()
    require(symmetry_error.nnz == 0, "expanded mass is nonsymmetric")
    expanded_row_sum = np.asarray(expanded.sum(axis=1)).ravel()
    expanded_abs_row_scale = float(np.max(np.asarray(abs(expanded).sum(axis=1)).ravel(), initial=0.0))
    expanded_row_abs = float(np.max(np.abs(expanded_row_sum), initial=0.0))
    expanded_row_normalized = expanded_row_abs / max(expanded_abs_row_scale, np.finfo(float).tiny)
    require(expanded_row_normalized <= 1.0e-12, "expanded mass rows do not sum to zero")
    shadow_error = np.max(np.abs(shadow_injection - saved_injection), initial=0.0)
    rhs_scale = max(float(np.max(np.abs(saved_injection), initial=0.0)), np.finfo(float).tiny)
    rhs_relative_error = float(shadow_error) / rhs_scale
    require(rhs_relative_error <= 2.0e-10, "M one does not recover projection03 RHS")
    expected_total = -complex(*ledger["gc_outgoing_total_a"])
    current_total = complex(np.sum(saved_owner_current, dtype=np.complex128))
    require(abs(current_total - expected_total) <= max(abs(expected_total), np.finfo(float).tiny) * 2.0e-10, "owner current differs from ledger")
    require(np.all(np.asarray(owner_capacitance) > 0.0), "invalid scalar capacitance")
    owner_y_errors = [abs(y - c * q) for y, c, q in zip(owner_branch_y, owner_coefficient, owner_capacitance, strict=True)]
    branch_recouple_error = max(owner_y_errors)
    owner_y_scale = max(float(np.max(np.abs(np.asarray(owner_branch_y)))), np.finfo(float).tiny)
    branch_recouple_relative_error = float(branch_recouple_error) / owner_y_scale
    require(branch_recouple_relative_error <= 2.0e-12, "owner branch Y does not recollapse")
    physical_rowsum_residual = max(owner_physical_rowsum_residuals)
    physical_rowsum_relative = physical_rowsum_residual / owner_y_scale
    require(physical_rowsum_relative <= 2.0e-12, "physical owner Y rows do not sum to zero")
    owner_finite = np.asarray(owner_capacitance) * np.asarray(owner_density)
    require(np.all(np.isfinite(owner_finite)) and np.all(np.asarray(owner_capacitance) > 0.0), "owner scalar gate failed")
    owner_psd_minimum = min(owner_psd_min) if owner_psd_min else 0.0
    atomic_npz(
        output_dir / "gc-mass-shadow.npz",
        expanded_mass_data_um2=np.asarray(expanded.data, dtype=np.float64),
        expanded_mass_indices=np.asarray(expanded.indices, dtype=np.int64),
        expanded_mass_indptr=np.asarray(expanded.indptr, dtype=np.int64),
        expanded_mass_shape=np.asarray(expanded.shape, dtype=np.int64),
        owner_mass_owner_index=np.asarray(owner_ids, dtype=np.int16),
        owner_mass_row=np.asarray(owner_rows, dtype=np.int64),
        owner_mass_col=np.asarray(owner_cols, dtype=np.int64),
        owner_mass_data_um2=np.asarray(owner_values, dtype=np.float64),
        owner_fingerprints_json_utf8=np.frombuffer(json.dumps([owner["fingerprint"] for owner in owners], separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
        owner_bindings_json_utf8=np.frombuffer(json.dumps(owner_bindings, separators=(",", ":"), allow_nan=False).encode("utf-8"), dtype=np.uint8),
        owner_overlap_area_um2=np.asarray(owner_area, dtype=np.float64),
        owner_capacitance_f=np.asarray(owner_capacitance, dtype=np.float64),
        owner_density_f_per_um2=np.asarray(owner_density, dtype=np.float64),
        owner_dispersion_s_per_f=np.asarray(owner_coefficient, dtype=np.complex128),
        owner_branch_admittance_s=np.asarray(owner_branch_y, dtype=np.complex128),
        owner_first_moment_error=np.asarray(owner_first_errors, dtype=np.float64),
        owner_support_node_count=np.asarray(owner_support, dtype=np.int64),
        owner_positive_triangle_intersections=np.asarray(owner_intersections, dtype=np.int64),
    )
    npz_path = output_dir / "gc-mass-shadow.npz"
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_SOURCE_EXACT_L14_GC_P1_MASS_SHADOW",
        "rail_id": candidate["rail_id"],
        "frequency_hz": 1.0e6,
        "inputs": input_receipts,
        "source_geometry": geometry_receipts,
        "projection03_binding": {"receipt_sha256": EXPECTED_SHA256["projection_receipt"], "npz_sha256": EXPECTED_SHA256["projection_npz"], "owner_count": len(owners), "owner_order_exact": True},
        "mass": {
            "coordinate_units": "um; mass entries are um^2 and density is F/um^2",
            "mesh_node_count": len(node_xy),
            "mesh_triangle_count": len(triangles),
            "expanded_shape": list(expanded.shape),
            "expanded_nnz": int(expanded.nnz),
            "owner_tagged_upper_entry_count": len(owner_values),
            "owner_mass_units": "raw geometric um^2; mirror canonical upper entries once downstream",
            "aggregate_matrix_kind": "unweighted_geometric_mass_diagnostic",
            "external_global_reduced_indices": external_global_ids,
            "external_column_indices": [external_column[value] for value in external_global_ids],
            "owner_count": len(owners),
            "partial_owner_counts": {"6": 110, "7": 114},
            "retained_nonincident_owner_counts": {"6": 336, "7": 412},
            "owner_first_moment_max_abs_error": max(owner_first_errors),
            "owner_weight_sum_max_abs_error": max(owner_weight_errors),
            "owner_area_identity_max_abs_error_um2": max(owner_area_identity_errors),
            "minimum_triangle_mass_eigenvalue": owner_psd_minimum,
            "expanded_rowsum_absolute_max": expanded_row_abs,
            "expanded_rowsum_abs_entry_scale": expanded_abs_row_scale,
            "expanded_rowsum_normalized_max": expanded_row_normalized,
            "expanded_symmetric": True,
            "expanded_finite": True,
            "projection03_first_moment_rhs_max_abs_error": float(shadow_error),
            "projection03_first_moment_rhs_scale": rhs_scale,
            "projection03_first_moment_rhs_relative_error": rhs_relative_error,
            "local_clip_whole_overlap_controls": control,
            "total_positive_area_triangle_intersections": int(sum(owner_intersections)),
            "original_224_owner_y_scale_s": owner_y_scale,
            "scalar_recoupled_branch_y_max_abs_error_s": float(branch_recouple_error),
            "scalar_recoupled_branch_y_relative_error": branch_recouple_relative_error,
            "physical_owner_y_rowsum_residual_max_s": float(physical_rowsum_residual),
            "physical_owner_y_rowsum_relative_error": physical_rowsum_relative,
            "owner_current_total_a": [current_total.real, current_total.imag],
            "ledger_expected_current_into_l14_a": [expected_total.real, expected_total.imag],
            "candidate_unvalidated_path": str(candidate_npz_path),
            "candidate_unvalidated_sha256": candidate_sha256,
            "candidate_unvalidated_size_bytes": candidate_npz_path.stat().st_size,
        },
        "projection_contract": {
            "one_sided_mass": "M_e = integral_S phi phi^T dA on the L14 P1 mesh",
            "expanded_owner_block": "density*[M,-M1;-(M1)^T,area] for the recorded external reduced terminal",
            "frequency_stamp": "Y_e(f) = density(F/um^2) * original dispersion(S/F) * raw owner mass(um^2), applied once",
            "recollapse": "1^T M 1=area and equipotential L14 recollapse gives original scalar C_e and Y_e",
            "nonincident_edges": "336/412 retained source edges are untouched",
        },
        "output": {
            "path": str(npz_path),
            "sha256": file_sha256(npz_path),
            "size_bytes": npz_path.stat().st_size,
            "allow_pickle_required": False,
            "expanded_mass_dtype": str(expanded.data.dtype),
            "owner_mass_upper_dtype": str(np.asarray(owner_values, dtype=np.float64).dtype),
        },
        "script_sha256": file_sha256(Path(__file__).resolve()),
        "elapsed_s": time.perf_counter() - started,
        "limitations": [
            "This is a source-owned first-order P1 mass shadow at the saved native equipotential; no global solve or finite-resistance re-evaluation was run.",
            "The expanded sparse matrix is an owner-mapped G/C mass assembly; it is not a product replacement or PowerSI fit.",
            "Only original partial 06/07 incident owners are projected; external terminal bindings and 336/412 nonincident source edges are preserved.",
        ],
    }
    atomic_json(output_dir / "receipt.json", result)
    return result


def finalize_candidate(output_dir: Path, candidate_path: Path, max_runtime_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    candidate_path = candidate_path.resolve()
    inputs = {
        "candidate": candidate_path,
        "projection_npz": PROJECTION_NPZ,
        "projection_receipt": PROJECTION_RECEIPT,
        "partition": PARTITION,
        "conservation": CONSERVATION,
    }
    input_receipts: dict[str, Any] = {}
    for name, path in inputs.items():
        require(path.is_file(), f"missing finalize input {path}")
        actual = file_sha256(path)
        if name == "candidate":
            require(actual == CANDIDATE_SHA256 and path.stat().st_size == CANDIDATE_SIZE_BYTES, "mass03 candidate identity differs")
        else:
            require(actual == EXPECTED_SHA256[name], f"{name} hash differs")
        input_receipts[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
    partition = json.loads(PARTITION.read_text(encoding="utf-8"))
    require(partition["status"] == "VERIFIED_ORIGINAL_L14_GC_SOURCE_EDGE_PARTITION", "edge partition receipt is not verified")
    expected_partition = {
        "partial_06": (446, 110, 336),
        "partial_07": (526, 114, 412),
    }
    for row in partition["partials"]:
        name = str(row["partial"])
        require((int(row["original_edges"]), int(row["removed_owner_edges"]), int(row["retained_nonincident_edges"])) == expected_partition[name], "edge partition counts differ")
        require(bool(row["owner_capacitance_bitwise_match"]) and int(row["retained_target_incidence"]) == 0, "edge partition ownership differs")
    conservation = json.loads(CONSERVATION.read_text(encoding="utf-8"))
    require(conservation["status"] == "VERIFIED_PRESERVED_OWNER_MASS_CANDIDATE_CONSERVATION", "candidate conservation receipt is not verified")
    require(conservation["candidate_sha256"] == CANDIDATE_SHA256 and int(conservation["owner_count"]) == 224, "candidate conservation identity differs")

    with np.load(candidate_path, allow_pickle=False) as raw:
        required = {
            "owner_mass_owner_index", "owner_mass_row", "owner_mass_col", "owner_mass_data_um2",
            "owner_fingerprints_json_utf8", "owner_bindings_json_utf8", "owner_overlap_area_um2",
            "owner_area_identity_error_um2", "owner_density_f_per_um2", "owner_dispersion_s_per_f",
            "owner_branch_admittance_s",
        }
        require(required.issubset(raw.files), "candidate fields are incomplete")
        owner_index = np.asarray(raw["owner_mass_owner_index"], dtype=np.int64).copy()
        raw_row = np.asarray(raw["owner_mass_row"], dtype=np.int64).copy()
        raw_col = np.asarray(raw["owner_mass_col"], dtype=np.int64).copy()
        mass_data = np.asarray(raw["owner_mass_data_um2"], dtype=np.float64).copy()
        fingerprints = decode_text(raw["owner_fingerprints_json_utf8"])
        bindings = json.loads(raw["owner_bindings_json_utf8"].tobytes().decode("utf-8"))
        area = np.asarray(raw["owner_overlap_area_um2"], dtype=np.float64).copy()
        candidate_area_error = np.asarray(raw["owner_area_identity_error_um2"], dtype=np.float64).copy()
        density = np.asarray(raw["owner_density_f_per_um2"], dtype=np.float64).copy()
        dispersion = np.asarray(raw["owner_dispersion_s_per_f"], dtype=np.complex128).copy()
        candidate_branch_y = np.asarray(raw["owner_branch_admittance_s"], dtype=np.complex128).copy()
    owner_count = 224
    mesh_count = 171_957
    require(len(fingerprints) == owner_count and len(bindings) == owner_count, "candidate owner binding count differs")
    require(np.array_equal(np.asarray([int(row["owner_index"]) for row in bindings]), np.arange(owner_count)), "candidate owner order differs")
    require(owner_index.shape == raw_row.shape == raw_col.shape == mass_data.shape and len(mass_data) == int(conservation["tagged_upper_entries"]), "candidate mass entry count differs")
    require(np.all((owner_index >= 0) & (owner_index < owner_count)) and np.all((raw_row >= 0) & (raw_row < mesh_count)) and np.all((raw_col >= 0) & (raw_col < mesh_count)), "candidate node or owner index differs")
    require(np.all(np.isfinite(mass_data)) and np.all(np.isfinite(area)) and np.all(np.isfinite(density)) and np.all(np.isfinite(dispersion)), "candidate contains nonfinite values")
    require(np.all(area > 0.0) and np.all(density > 0.0), "candidate scalar area/density is invalid")
    require([str(row) for row in fingerprints] == [str(row["fingerprint"]) for row in bindings], "candidate fingerprint order differs")

    canonical_row = np.minimum(raw_row, raw_col)
    canonical_col = np.maximum(raw_row, raw_col)
    off_diagonal = canonical_row != canonical_col
    first_owner_rows = np.concatenate((owner_index, owner_index[off_diagonal]))
    first_owner_cols = np.concatenate((canonical_row, canonical_col[off_diagonal]))
    first_owner_data = np.concatenate((mass_data, mass_data[off_diagonal]))
    owner_first = sparse.coo_matrix((first_owner_data, (first_owner_rows, first_owner_cols)), shape=(owner_count, mesh_count)).tocsr()
    owner_first.sum_duplicates()
    first_sum = np.asarray(owner_first.sum(axis=1)).ravel()
    area_identity = first_sum - area
    area_identity_abs = float(np.max(np.abs(area_identity), initial=0.0))
    area_identity_rel = float(np.max(np.abs(area_identity) / area, initial=0.0))
    require(area_identity_rel <= 2.0e-12, "owner 1^T M 1 differs from source area")

    external_global_ids = sorted({int(row["external_global_reduced_index"]) for row in bindings})
    external_column = {value: mesh_count + index for index, value in enumerate(external_global_ids)}
    external_slot = {value: index for index, value in enumerate(external_global_ids)}
    first_coo = owner_first.tocoo()
    external_for_owner = np.asarray([external_column[int(row["external_global_reduced_index"])] for row in bindings], dtype=np.int64)
    external_slot_for_owner = np.asarray([external_slot[int(row["external_global_reduced_index"])] for row in bindings], dtype=np.int64)
    cross_rows = np.minimum(first_coo.col.astype(np.int64), external_for_owner[first_coo.row])
    cross_cols = np.maximum(first_coo.col.astype(np.int64), external_for_owner[first_coo.row])
    cross_area = np.asarray([float(row["overlap_area_um2"]) for row in bindings], dtype=np.float64)
    binding_area_relative = float(np.max(np.abs(cross_area - area) / area, initial=0.0))
    require(binding_area_relative <= 2.0e-12, "binding area differs")
    external_nodes = np.asarray(list(external_column.values()), dtype=np.int64)
    external_diag_area = sum_by_slot(cross_area, external_slot_for_owner, len(external_nodes))
    raw_upper_rows = np.concatenate((canonical_row, cross_rows, external_nodes))
    raw_upper_cols = np.concatenate((canonical_col, cross_cols, external_nodes))
    raw_upper_data = np.concatenate((mass_data, -first_coo.data, external_diag_area))
    require(len(raw_upper_rows) == len(raw_upper_cols) == len(raw_upper_data), "raw finalize COO lengths differ")
    raw_upper, raw_full = finalize_upper(raw_upper_rows, raw_upper_cols, raw_upper_data, (mesh_count + len(external_global_ids),) * 2)

    owner_scale = density * dispersion
    require(np.all(np.real(owner_scale) >= 0.0), "physical scalar real scale is negative")
    external_diag_physical = sum_by_slot(cross_area * owner_scale, external_slot_for_owner, len(external_nodes))
    physical_upper_data = np.concatenate((mass_data * owner_scale[owner_index], -first_coo.data * owner_scale[first_coo.row], external_diag_physical))
    require(len(raw_upper_rows) == len(raw_upper_cols) == len(physical_upper_data), "physical finalize COO lengths differ")
    physical_upper, physical_full = finalize_upper(raw_upper_rows, raw_upper_cols, physical_upper_data, (mesh_count + len(external_global_ids),) * 2)
    raw_symmetry = raw_full - raw_full.T
    raw_symmetry.eliminate_zeros()
    physical_symmetry = physical_full - physical_full.T
    physical_symmetry.eliminate_zeros()
    require(raw_symmetry.nnz == 0 and physical_symmetry.nnz == 0, "canonical matrices are nonsymmetric")
    real_physical = physical_full.real.tocsc()
    real_symmetry = real_physical - real_physical.T
    real_symmetry.eliminate_zeros()
    require(real_symmetry.nnz == 0, "real physical Y is nonsymmetric")
    real_diag_min = float(np.min(real_physical.diagonal()))
    require(real_diag_min >= -np.finfo(float).eps, "real physical Y diagonal is negative")

    raw_rowsum = np.asarray(raw_full.sum(axis=1)).ravel()
    raw_abs = float(np.max(np.abs(raw_rowsum), initial=0.0))
    raw_abs_scale = float(np.max(np.asarray(abs(raw_full).sum(axis=1)).ravel(), initial=0.0))
    raw_relative = raw_abs / max(raw_abs_scale, np.finfo(float).tiny)
    require(raw_relative <= 1.0e-12, "raw geometric mass rows do not sum to zero")
    physical_rowsum = np.asarray(physical_full.sum(axis=1)).ravel()
    physical_abs = float(np.max(np.abs(physical_rowsum), initial=0.0))
    owner_y_from_binding = np.asarray([complex(*row["branch_admittance_s"]) for row in bindings], dtype=np.complex128)
    owner_y_scale = max(float(np.max(np.abs(owner_y_from_binding))), np.finfo(float).tiny)
    physical_relative = physical_abs / owner_y_scale
    require(physical_relative <= 2.0e-12, "physical GC Y rows do not sum to zero")

    binding_capacitance = np.asarray([float(row["capacitance_f"]) for row in bindings], dtype=np.float64)
    mass_capacitance = density * first_sum
    scalar_c_error = mass_capacitance - binding_capacitance
    scalar_c_abs = float(np.max(np.abs(scalar_c_error), initial=0.0))
    scalar_c_relative = float(np.max(np.abs(scalar_c_error) / np.abs(binding_capacitance), initial=0.0))
    require(scalar_c_relative <= 2.0e-12, "owner scalar C recollapse differs")
    owner_y = dispersion * mass_capacitance
    owner_y_error = owner_y - owner_y_from_binding
    owner_y_abs = float(np.max(np.abs(owner_y_error), initial=0.0))
    owner_y_relative = float(np.max(np.abs(owner_y_error) / np.maximum(np.abs(owner_y_from_binding), np.finfo(float).tiny), initial=0.0))
    require(owner_y_relative <= 2.0e-12, "owner original Y recollapse differs")
    with np.load(PROJECTION_NPZ, allow_pickle=False) as projection:
        saved_fingerprints = decode_text(projection["owner_fingerprints_json_utf8"])
        saved_injection = np.asarray(projection["gc_nodal_injection_a"], dtype=np.complex128).copy()
        saved_owner_current = np.asarray(projection["owner_current_into_l14_a"], dtype=np.complex128).copy()
    require(saved_fingerprints == fingerprints and saved_injection.shape == (mesh_count,) and saved_owner_current.shape == (owner_count,), "projection03 binding differs")
    rhs = np.zeros(mesh_count, dtype=np.complex128)
    np.add.at(rhs, first_coo.col, saved_owner_current[first_coo.row] * first_coo.data / area[first_coo.row])
    rhs_error = float(np.max(np.abs(rhs - saved_injection), initial=0.0))
    rhs_scale = max(float(np.max(np.abs(saved_injection), initial=0.0)), np.finfo(float).tiny)
    rhs_relative = rhs_error / rhs_scale
    require(rhs_relative <= 2.0e-10, "candidate M one does not recover projection03 RHS")

    final_npz_path = output_dir / "gc-mass-shadow.npz"
    owner_first_csr = owner_first.tocsr()
    atomic_npz(
        final_npz_path,
        raw_mass_data_um2=np.asarray(raw_full.data, dtype=np.float64),
        raw_mass_indices=np.asarray(raw_full.indices, dtype=np.int64),
        raw_mass_indptr=np.asarray(raw_full.indptr, dtype=np.int64),
        raw_mass_shape=np.asarray(raw_full.shape, dtype=np.int64),
        physical_gc_y_data_s=np.asarray(physical_full.data, dtype=np.complex128),
        physical_gc_y_indices=np.asarray(physical_full.indices, dtype=np.int64),
        physical_gc_y_indptr=np.asarray(physical_full.indptr, dtype=np.int64),
        physical_gc_y_shape=np.asarray(physical_full.shape, dtype=np.int64),
        owner_m1_data_um2=np.asarray(owner_first_csr.data, dtype=np.float64),
        owner_m1_indices=np.asarray(owner_first_csr.indices, dtype=np.int64),
        owner_m1_indptr=np.asarray(owner_first_csr.indptr, dtype=np.int64),
        owner_m1_shape=np.asarray(owner_first_csr.shape, dtype=np.int64),
        owner_fingerprints_json_utf8=np.frombuffer(json.dumps(fingerprints, separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
        owner_bindings_json_utf8=np.frombuffer(json.dumps(bindings, separators=(",", ":"), allow_nan=False).encode("utf-8"), dtype=np.uint8),
        owner_overlap_area_um2=area,
        owner_density_f_per_um2=density,
        owner_dispersion_s_per_f=dispersion,
        owner_branch_admittance_s=owner_y,
        owner_y_recoupling_error_s=owner_y_error,
        projection03_rhs_a=rhs,
    )
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_SOURCE_EXACT_L14_GC_P1_MASS_FINALIZE",
        "rail_id": "ADC_VDD_075_VTRIP_SRAM/0",
        "frequency_hz": 1.0e6,
        "inputs": input_receipts,
        "candidate_only": True,
        "source_geometry_read": False,
        "owner_binding": {"owner_count": owner_count, "partial_owner_counts": {"6": 110, "7": 114}, "retained_nonincident_owner_counts": {"6": 336, "7": 412}, "external_global_reduced_indices": external_global_ids},
        "mass": {
            "coordinate_units": "um; raw mass data is um^2",
            "aggregate_matrix_kind": "unweighted_geometric_mass_diagnostic",
            "shape": list(raw_full.shape),
            "raw_nnz": int(raw_full.nnz),
            "physical_gc_y_nnz": int(physical_full.nnz),
            "owner_m1_nnz": int(owner_first_csr.nnz),
            "canonical_upper_mirrored_once": True,
            "owner_m1_area_absolute_max_um2": area_identity_abs,
            "owner_m1_area_relative_max": area_identity_rel,
            "binding_area_relative_max": binding_area_relative,
            "mass_1t_m_first_sum_is_source_area": True,
            "external_diagonal_uses_source_area_by_terminal": True,
            "raw_rowsum_absolute_max_um2": raw_abs,
            "raw_rowsum_abs_entry_scale_um2": raw_abs_scale,
            "raw_rowsum_backward_relative": raw_relative,
            "physical_gc_y_rowsum_absolute_max_s": physical_abs,
            "physical_gc_y_rowsum_original_owner_y_relative": physical_relative,
            "complex_symmetric": True,
            "real_physical_y_symmetric": True,
            "real_scalar_scale_min_s_per_um2": float(np.min(np.real(owner_scale))),
            "real_physical_y_diagonal_min": real_diag_min,
            "real_scalar_scale_nonnegative": True,
            "owner_original_y_scale_s": owner_y_scale,
            "mass_derived_capacitance_absolute_max_error_f": scalar_c_abs,
            "mass_derived_capacitance_relative_max": scalar_c_relative,
            "owner_original_y_recoupling_absolute_max_s": owner_y_abs,
            "owner_original_y_recoupling_relative": owner_y_relative,
            "projection03_rhs_absolute_max_a": rhs_error,
            "projection03_rhs_scale_a": rhs_scale,
            "projection03_rhs_relative": rhs_relative,
            "partition_receipt_counts_verified": True,
            "candidate_unvalidated_sha256": CANDIDATE_SHA256,
        },
        "projection_contract": {
            "owner_mass_data": "raw geometric um^2; canonical upper entries are mirrored exactly once",
            "physical_stamp": "owner_density(F/um^2) * original_dispersion(S/F) applied once to each owner mass entry",
            "external_terminal": "source external reduced terminal and binding preserved per owner",
            "nonincident_edges": "partial06 336 and partial07 412 retained unchanged",
            "scope": "first-order GC mass shadow only; no native/global solve, geometry reread, or PowerSI fit",
        },
        "output": {"path": str(final_npz_path), "sha256": file_sha256(final_npz_path), "size_bytes": final_npz_path.stat().st_size, "allow_pickle_required": False},
        "script_sha256": file_sha256(Path(__file__).resolve()),
        "elapsed_s": time.perf_counter() - started,
    }
    atomic_json(output_dir / "receipt.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/research/astra-l14-gc-mass-01")
    parser.add_argument("--candidate-input", type=Path, help="Finalize an existing mass03 candidate without reading source geometry")
    parser.add_argument("--max-runtime-s", type=float, default=180.0)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if not (0.0 < args.max_runtime_s <= 180.0):
        parser.error("--max-runtime-s must be in (0, 180]")
    self_check()
    if args.self_check:
        print("SELF_CHECK PASS")
        return 0
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        parser.error("--output-dir must not already exist")
    output_dir.mkdir()
    (output_dir / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(f"{PROGRAM} v{VERSION} - exact L14 G/C P1 mass shadow", flush=True)
    completed = threading.Event()

    def watchdog() -> None:
        if not completed.wait(args.max_runtime_s):
            try:
                atomic_json(output_dir / "watchdog-stop.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_MAX_RUNTIME", "max_runtime_s": args.max_runtime_s})
            finally:
                os._exit(124)

    monitor = threading.Thread(target=watchdog, daemon=True)
    monitor.start()
    try:
        if args.candidate_input is None:
            result = run(output_dir, args.max_runtime_s)
        else:
            result = finalize_candidate(output_dir, args.candidate_input, args.max_runtime_s)
    except BaseException as exc:
        candidate_path = output_dir / "owner-mass-candidate-unvalidated.npz"
        failure = {"program": PROGRAM, "version": VERSION, "status": "STOP_GC_P1_MASS", "error": {"type": type(exc).__name__, "message": str(exc)}, "script_sha256": file_sha256(Path(__file__).resolve())}
        if candidate_path.is_file():
            failure["candidate_unvalidated"] = {"path": str(candidate_path), "sha256": file_sha256(candidate_path), "size_bytes": candidate_path.stat().st_size}
        atomic_json(output_dir / "failure.json", failure)
        raise
    finally:
        completed.set()
        monitor.join(timeout=1.0)
    print(f"{result['status']} -> {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
