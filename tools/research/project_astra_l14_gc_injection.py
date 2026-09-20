"""Project exact source-owned L14 adjacent-gap currents onto the saved P1 mesh."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import MultiPolygon, Polygon

from spd_decap_pi._core import services


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
MESH = ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz"
MESH_RECEIPT = ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05/verified-mesh-checkpoint.json"
RAW = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz"
DERIVED = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/derived-field-observation.npz"
INVENTORY = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/l14-gc-projection-inventory.json"
LEDGER = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/l14-island-external-current-ledger.json"
CANDIDATE = ROOT / "outputs/research/astra-step6e-loaded-boundary-01/loaded-sheet-candidate-final.json"
SOURCE_CACHE = ROOT / "outputs/research/astra-l14-gc-projection-02/source"
EXPECTED_SHA256 = {
    "mesh": "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779",
    "mesh_receipt": "ab577767f16a6e0f51bfc3009c9fc91ff586218e23e4ecba3ee10c67bffa2f07",
    "raw": "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    "derived": "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0",
    "inventory": "4409cdccc3b3d488d8f3abdd2f1e8b09a488d3c18a5bace010c765a240bc397f",
    "ledger": "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b",
    "candidate": "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d",
}


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


def complex_pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


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
    rows = services._spd_surface_islands(
        layer=layer,
        net=net,
        asset_sha256=asset_sha,
        shape=shape,
    )
    result = {str(island_id): polygon for island_id, polygon in rows}
    require(len(result) == len(rows), "source island IDs are duplicated")
    return result


def add_owner_moment(
    *,
    overlap: Any,
    owner_current: complex,
    node_xy: np.ndarray[Any, Any],
    triangles: np.ndarray[Any, Any],
    triangle_geometry: np.ndarray[Any, Any],
    triangle_tile_keys: np.ndarray[Any, Any],
    tree: STRtree,
    injection: np.ndarray[Any, Any],
    control: dict[str, float | int],
) -> tuple[float, float, float, float, int]:
    overlap_area = float(overlap.area)
    require(overlap_area > 0.0 and overlap.is_valid, "owner overlap is empty or invalid")
    candidates = np.asarray(tree.query(overlap), dtype=np.int64)
    require(candidates.size > 0, "owner overlap has no mesh triangle")
    order = np.argsort(triangle_tile_keys[candidates], kind="stable")
    candidates = candidates[order]
    keys = triangle_tile_keys[candidates]
    boundaries = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
    integrated_area = 0.0
    integrated_weight = 0.0
    barycentric_error = 0.0
    positive_count = 0
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        local_candidates = candidates[start:stop]
        local_points = node_xy[triangles[local_candidates]]
        minimum = np.min(local_points, axis=(0, 1))
        maximum = np.max(local_points, axis=(0, 1))
        local_overlap = overlap.intersection(
            shapely.box(float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1]))
        )
        if local_overlap.is_empty:
            continue
        cuts = shapely.intersection(triangle_geometry[local_candidates], local_overlap)
        areas = np.asarray(shapely.area(cuts), dtype=np.float64)
        positive = np.flatnonzero(areas > 0.0)
        if positive.size == 0:
            continue
        local_candidates = local_candidates[positive]
        areas = areas[positive]
        cuts = cuts[positive]
        centers = shapely.centroid(cuts)
        x = np.asarray(shapely.get_x(centers), dtype=np.float64)
        y = np.asarray(shapely.get_y(centers), dtype=np.float64)
        indices = triangles[local_candidates]
        points = node_xy[indices]
        a = points[:, 0, :]
        b = points[:, 1, :]
        c = points[:, 2, :]
        v0 = b - a
        v1 = c - a
        px = x - a[:, 0]
        py = y - a[:, 1]
        determinant = v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]
        require(np.all(np.isfinite(determinant)) and np.all(np.abs(determinant) > 0.0), "mesh triangle is degenerate")
        l1 = (px * v1[:, 1] - py * v1[:, 0]) / determinant
        l2 = (v0[:, 0] * py - v0[:, 1] * px) / determinant
        l0 = 1.0 - l1 - l2
        barycentric = np.column_stack((l0, l1, l2))
        barycentric_error = max(
            barycentric_error,
            0.0,
            -float(np.min(barycentric)),
            float(np.max(barycentric)) - 1.0,
        )
        require(barycentric_error <= 2.0e-9, "intersection centroid is outside its mesh triangle")
        positions = np.arange(int(control["seen"]), int(control["seen"]) + len(areas))
        sample = np.flatnonzero((positions < 16) | (positions % 4096 == 0))
        if sample.size:
            direct = shapely.intersection(triangle_geometry[local_candidates[sample]], overlap)
            direct_area = np.asarray(shapely.area(direct), dtype=np.float64)
            direct_center = shapely.centroid(direct)
            direct_first = direct_area[:, None] * np.column_stack(
                (
                    np.asarray(shapely.get_x(direct_center), dtype=np.float64),
                    np.asarray(shapely.get_y(direct_center), dtype=np.float64),
                )
            )
            local_first = areas[sample, None] * np.column_stack((x[sample], y[sample]))
            area_error = float(np.max(np.abs(direct_area - areas[sample]), initial=0.0))
            first_error = float(np.max(np.abs(direct_first - local_first), initial=0.0))
            area_scale = max(float(np.max(np.abs(direct_area), initial=0.0)), 1.0)
            first_scale = max(float(np.max(np.abs(direct_first), initial=0.0)), 1.0)
            require(area_error <= 2.0e-10 * area_scale, "local clip area control differs")
            require(first_error <= 2.0e-10 * first_scale, "local clip first-moment control differs")
            control["count"] = int(control["count"]) + int(sample.size)
            control["maximum_area_abs_error_um2"] = max(float(control["maximum_area_abs_error_um2"]), area_error)
            control["maximum_first_moment_abs_error_um3"] = max(float(control["maximum_first_moment_abs_error_um3"]), first_error)
        control["seen"] = int(control["seen"]) + len(areas)
        weights = areas[:, None] * barycentric / overlap_area
        contribution = owner_current * weights
        for local in range(3):
            np.add.at(injection, indices[:, local], contribution[:, local])
        integrated_area += float(np.sum(areas, dtype=np.float64))
        integrated_weight += float(np.sum(weights, dtype=np.float64))
        positive_count += len(areas)
    require(positive_count > 0, "owner overlap has no positive mesh intersection")
    area_relative_error = abs(integrated_area - overlap_area) / overlap_area
    require(area_relative_error <= 2.0e-9, "mesh intersections do not cover owner overlap once")
    require(abs(integrated_weight - 1.0) <= 2.0e-9, "owner P1 moment does not recollapse to one")
    return overlap_area, area_relative_error, barycentric_error, integrated_weight, positive_count


def self_check() -> None:
    node_xy = np.asarray(((0.0, 0.0), (10.0, 0.0), (0.0, 10.0)), dtype=np.float64)
    triangles = np.asarray(((0, 1, 2),), dtype=np.int64)
    triangle_geometry = np.asarray(shapely.polygons(node_xy[triangles]), dtype=object)
    outer = Polygon(((1, 1), (4, 1), (4, 4), (1, 4)), holes=(((2, 2), (3, 2), (3, 3), (2, 3)),))
    second = Polygon(((6, 1), (7, 1), (6, 2)))
    overlap = MultiPolygon((outer, second))
    injection = np.zeros(3, dtype=np.complex128)
    control: dict[str, float | int] = {
        "seen": 0,
        "count": 0,
        "maximum_area_abs_error_um2": 0.0,
        "maximum_first_moment_abs_error_um3": 0.0,
    }
    area, area_error, bary_error, weight_sum, count = add_owner_moment(
        overlap=overlap,
        owner_current=2.0 + 3.0j,
        node_xy=node_xy,
        triangles=triangles,
        triangle_geometry=triangle_geometry,
        triangle_tile_keys=np.zeros(1, dtype=np.int64),
        tree=STRtree(triangle_geometry),
        injection=injection,
        control=control,
    )
    require(abs(area - overlap.area) <= 1.0e-14 and area_error <= 1.0e-14, "holed/multipart area control failed")
    require(bary_error <= 1.0e-14 and abs(weight_sum - 1.0) <= 1.0e-14, "holed/multipart moment control failed")
    require(count == 1 and abs(np.sum(injection) - (2.0 + 3.0j)) <= 1.0e-14, "holed/multipart recollapse failed")


def run(output_dir: Path, max_runtime_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    inputs = {
        "mesh": MESH,
        "mesh_receipt": MESH_RECEIPT,
        "raw": RAW,
        "derived": DERIVED,
        "inventory": INVENTORY,
        "ledger": LEDGER,
        "candidate": CANDIDATE,
    }
    input_receipts: dict[str, Any] = {}
    for name, path in inputs.items():
        require(path.is_file(), f"missing input {path}")
        actual = file_sha256(path)
        require(actual == EXPECTED_SHA256[name], f"{name} hash differs")
        input_receipts[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    mesh_receipt = json.loads(MESH_RECEIPT.read_text(encoding="utf-8"))
    require(mesh_receipt["status"] == "VERIFIED_SAVED_L14_MESH_CHECKPOINT", "mesh checkpoint is not verified")
    require(inventory["mapping"]["source_owner_count"] == 224, "source owner count differs")

    asset_records: dict[str, dict[str, Any]] = {}
    l14_geometry = candidate["candidate_component"]["geometry"]
    asset_records[l14_geometry["compressed_sha256"]] = {
        "layer": candidate["candidate_component"]["layer"],
        "net": candidate["candidate_component"]["net"],
        "member": l14_geometry["member"],
        "asset_sha256": l14_geometry["compressed_sha256"],
    }
    owners: list[dict[str, Any]] = []
    for partial in inventory["mapping"]["partials"]:
        owners.extend(partial["owners"])
        for asset in partial["external_geometry_assets"]:
            asset_records[asset["asset_sha256"]] = dict(asset)
    require(len(owners) == 224 and len(asset_records) == 4, "geometry/owner inventory differs")

    source_dir = output_dir / "source"
    source_dir.mkdir()
    polygons_by_asset: dict[str, dict[str, Any]] = {}
    geometry_receipts: list[dict[str, Any]] = []
    for asset_sha in sorted(asset_records):
        record = asset_records[asset_sha]
        member = "attachments/" + str(record["member"])
        cached = SOURCE_CACHE / Path(str(record["member"])).name
        require(cached.is_file(), "materialized source geometry is absent")
        compressed = cached.read_bytes()
        require(sha256(compressed).hexdigest() == asset_sha, "materialized geometry hash differs")
        destination = source_dir / cached.name
        with destination.open("xb") as handle:
            handle.write(compressed)
        polygons = polygon_map(str(record["layer"]), str(record["net"]), asset_sha, compressed)
        polygons_by_asset[asset_sha] = polygons
        geometry_receipts.append(
            {
                "layer": record["layer"],
                "net": record["net"],
                "bundle_member": member,
                "cached_input_path": str(cached),
                "saved_path": str(destination),
                "compressed_sha256": asset_sha,
                "compressed_size_bytes": len(compressed),
                "source_island_count": len(polygons),
            }
        )
    require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded after source decode")

    l14_asset_sha = l14_geometry["compressed_sha256"]
    l14_polygons = polygons_by_asset[l14_asset_sha]
    expected_l14 = {str(row["island_id"]) for row in ledger["islands"]}
    require(expected_l14 == set(l14_polygons), "L14 source island IDs differ")

    mesh = np.load(MESH, allow_pickle=False)
    raw = np.load(RAW, allow_pickle=False)
    try:
        node_xy = np.asarray(mesh["node_xy_um"], dtype=np.float64)
        triangles = np.asarray(mesh["triangles"], dtype=np.int64)
        require(node_xy.shape == (171_957, 2) and triangles.shape == (214_873, 3), "mesh shape differs")
        triangle_geometry = np.asarray(shapely.polygons(node_xy[triangles]), dtype=object)
        require(len(triangle_geometry) == len(triangles), "triangle geometry count differs")
        tree = STRtree(triangle_geometry)
        centers = np.mean(node_xy[triangles], axis=1)
        minimum_xy = np.min(node_xy, axis=0)
        tile_xy = np.floor((centers - minimum_xy) / 1024.0).astype(np.int64)
        triangle_tile_keys = tile_xy[:, 0] * 1_000_000 + tile_xy[:, 1]
        active_voltage = np.asarray(raw["active_voltage"], dtype=np.complex128)[:, 0]
        global_to_active = np.asarray(raw["global_to_active_indices"], dtype=np.int64)
        target_global = int(inventory["mapping"]["original_l14_reduced_index"])
        target_active = int(global_to_active[target_global])
        require(target_active >= 0, "L14 quotient is inactive")
        l14_voltage = complex(active_voltage[target_active])
        injection = np.zeros(len(node_xy), dtype=np.complex128)
        owner_fingerprints: list[str] = []
        owner_area_um2: list[float] = []
        owner_implied_area_um2: list[float] = []
        owner_current_into_l14: list[complex] = []
        owner_weight_sum: list[float] = []
        maximum_source_area_relative_error = 0.0
        maximum_mesh_area_relative_error = 0.0
        maximum_barycentric_error = 0.0
        total_intersected_triangles = 0
        control: dict[str, float | int] = {
            "seen": 0,
            "count": 0,
            "maximum_area_abs_error_um2": 0.0,
            "maximum_first_moment_abs_error_um3": 0.0,
        }
        current_by_partial: dict[int, complex] = {6: 0.0j, 7: 0.0j}
        progress_path = output_dir / "progress.jsonl"
        for owner_index, owner in enumerate(owners):
            with progress_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "owner_start", "owner_index": owner_index, "fingerprint": owner["fingerprint"], "elapsed_s": time.perf_counter() - started}) + "\n")
            partial_ordinal = int(owner["partial_ordinal"])
            partial = inventory["mapping"]["partials"][partial_ordinal - 6]
            coefficient = complex(*partial["actual_1mhz_dispersion_admittance_scale_s_per_f"])
            capacitance_f = float.fromhex(owner["capacitance_f_hex"])
            external_active = int(owner["external_active_index"])
            require(external_active >= 0, "external source terminal is inactive")
            external_voltage = complex(active_voltage[external_active])
            current = coefficient * capacitance_f * (external_voltage - l14_voltage)
            l14_polygon = l14_polygons[owner["l14_island_id"]]
            external_asset = next(
                asset for asset in partial["external_geometry_assets"]
                if str(asset["net"]).casefold() == str(owner["external_net"]).casefold()
            )
            external_polygon = polygons_by_asset[external_asset["asset_sha256"]][owner["external_island_id"]]
            overlap = l14_polygon.intersection(external_polygon)
            actual_area, mesh_area_error, barycentric_error, measured_weight_sum, count = add_owner_moment(
                overlap=overlap,
                owner_current=current,
                node_xy=node_xy,
                triangles=triangles,
                triangle_geometry=triangle_geometry,
                triangle_tile_keys=triangle_tile_keys,
                tree=tree,
                injection=injection,
                control=control,
            )
            implied_area = float(owner["implied_overlap_area_um2"])
            source_area_error = abs(actual_area - implied_area) / implied_area
            require(source_area_error <= 2.0e-10, "source overlap area differs from scalar owner C")
            maximum_source_area_relative_error = max(maximum_source_area_relative_error, source_area_error)
            maximum_mesh_area_relative_error = max(maximum_mesh_area_relative_error, mesh_area_error)
            maximum_barycentric_error = max(maximum_barycentric_error, barycentric_error)
            total_intersected_triangles += count
            owner_fingerprints.append(owner["fingerprint"])
            owner_area_um2.append(actual_area)
            owner_implied_area_um2.append(implied_area)
            owner_current_into_l14.append(current)
            owner_weight_sum.append(measured_weight_sum)
            current_by_partial[partial_ordinal] += current
            with progress_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "owner_done", "owner_index": owner_index, "positive_triangle_intersections": count, "elapsed_s": time.perf_counter() - started}) + "\n")
            require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded during P1 projection")
    finally:
        mesh.close()
        raw.close()

    expected_total = -complex(*ledger["gc_outgoing_total_a"])
    actual_total = complex(np.sum(injection, dtype=np.complex128))
    owner_total = complex(np.sum(owner_current_into_l14, dtype=np.complex128))
    current_scale = max(abs(expected_total), np.finfo(float).tiny)
    require(abs(owner_total - expected_total) <= current_scale * 2.0e-10, "owner current does not recover saved GC current")
    require(abs(actual_total - owner_total) <= current_scale * 2.0e-10, "P1 nodal injection does not recollapse")
    for row in ledger["partial_rows"]:
        ordinal = int(row["index"])
        expected = -complex(*row["outgoing_total_a"])
        require(abs(current_by_partial[ordinal] - expected) <= max(abs(expected), np.finfo(float).tiny) * 2.0e-10, "partial current does not recover ledger")

    npz_path = output_dir / "gc-nodal-injection.npz"
    atomic_npz(
        npz_path,
        gc_nodal_injection_a=injection,
        owner_fingerprints_json_utf8=np.frombuffer(json.dumps(owner_fingerprints, separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
        owner_overlap_area_um2=np.asarray(owner_area_um2, dtype=np.float64),
        owner_implied_overlap_area_um2=np.asarray(owner_implied_area_um2, dtype=np.float64),
        owner_current_into_l14_a=np.asarray(owner_current_into_l14, dtype=np.complex128),
        owner_weight_sum=np.asarray(owner_weight_sum, dtype=np.float64),
    )
    require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded before receipt")
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_SOURCE_EXACT_L14_GC_P1_INJECTION",
        "rail_id": candidate["rail_id"],
        "frequency_hz": 1.0e6,
        "input_sha256": input_receipts,
        "source_geometry": {
            "cache_path": str(SOURCE_CACHE),
            "bundle_path_from_pinned_candidate": candidate["inputs"]["bundle_path"],
            "bundle_size_from_pinned_candidate": candidate["inputs"]["bundle_size_bytes"],
            "bundle_not_reopened": True,
            "exact_cached_geometry_members_read": len(geometry_receipts),
        },
        "geometry": geometry_receipts,
        "projection": {
            "mesh_node_order": "exact node_xy_um order from the verified mesh checkpoint",
            "mesh_node_count": len(node_xy),
            "mesh_triangle_count": len(triangles),
            "source_owner_count": len(owners),
            "partial_owner_counts": {str(key): sum(int(owner["partial_ordinal"]) == key for owner in owners) for key in (6, 7)},
            "retained_nonincident_owner_counts": {str(partial["ordinal"]): partial["retained_nonincident_source_edge_count"] for partial in inventory["mapping"]["partials"]},
            "total_positive_area_triangle_intersections": total_intersected_triangles,
            "maximum_source_area_relative_error": maximum_source_area_relative_error,
            "maximum_mesh_area_relative_error": maximum_mesh_area_relative_error,
            "maximum_barycentric_range_error": maximum_barycentric_error,
            "owner_weight_sum_max_abs_error": float(np.max(np.abs(np.asarray(owner_weight_sum) - 1.0), initial=0.0)),
            "local_clip_whole_overlap_controls": control,
            "current_sign": "positive into the L14 sheet",
            "current_by_partial_a": {str(key): complex_pair(value) for key, value in current_by_partial.items()},
            "owner_current_sum_a": complex_pair(owner_total),
            "saved_expected_current_sum_a": complex_pair(expected_total),
            "nodal_injection_sum_a": complex_pair(actual_total),
            "owner_to_saved_current_abs_error_a": abs(owner_total - expected_total),
            "nodal_recollapse_abs_error_a": abs(actual_total - owner_total),
        },
        "output": {
            "path": str(npz_path),
            "sha256": file_sha256(npz_path),
            "size_bytes": npz_path.stat().st_size,
            "allow_pickle_required": False,
            "gc_nodal_injection_dtype": str(injection.dtype),
            "gc_nodal_injection_shape": list(injection.shape),
        },
        "script_sha256": file_sha256(Path(__file__).resolve()),
        "elapsed_s": time.perf_counter() - started,
        "limitations": [
            "This is the exact first-order GC current RHS at the saved native equipotential L14 voltage; it is not a finite-resistance coupled-network re-evaluation of the capacitive stamp.",
            "Only original partial 06/07 source owners incident on the L14 candidate are projected; 336/412 nonincident edges and all external terminals remain untouched.",
            "No sheet solve, native compile, raw SPD scan, Touchstone read, PowerSI fit, product replacement, or accuracy claim was performed.",
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs/research/astra-l14-gc-projection-01",
    )
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
    print(f"{PROGRAM} v{VERSION} - exact L14 G/C P1 injection", flush=True)
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
        result = run(output_dir, args.max_runtime_s)
        atomic_json(output_dir / "receipt.json", result)
    except BaseException as exc:
        atomic_json(output_dir / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_GC_P1_PROJECTION", "error": {"type": type(exc).__name__, "message": str(exc)}, "script_sha256": file_sha256(Path(__file__).resolve())})
        raise
    finally:
        completed.set()
        monitor.join(timeout=1.0)
    print(f"{result['status']} -> {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
