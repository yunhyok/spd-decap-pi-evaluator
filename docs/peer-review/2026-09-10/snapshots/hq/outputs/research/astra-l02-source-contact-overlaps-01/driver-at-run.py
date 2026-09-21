"""Classify frozen L02 circle-pad/drill contacts against 491 cached islands.

This research helper consumes the already frozen native/contact input ledger.  It
does not scan SPD source, union traces, build a mesh/operator, or solve a system.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import Polygon, box


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spd_decap_pi._core.io import shared_pad
import project_astra_l14_gc_mass as mass
import prepare_astra_l02_gc_source_overlaps as exact_overlap


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
INPUT_DIR = ROOT / "outputs/research/astra-l02-source-contact-inputs-01"
INPUT_RECEIPT = INPUT_DIR / "result.json"
INPUT_ARRAYS = INPUT_DIR / "contact-ledger-inputs.npz"
PINS = {
    "input_receipt": (INPUT_RECEIPT, "98bce62fde03d676e43d121207d00bb5ed840552bf8fd02793ae6732ab8bffd2"),
    "input_arrays": (INPUT_ARRAYS, "c5c0dab9ac22d750aeecf3a4290c2b9ef9285bcf7ed33a5d8a7a2775c8430984"),
    "input_driver": (ROOT / "tools/research/qualify_astra_l02_source_contacts.py", "282def7c0eff7a7e6b822766bbb104ee910945a2af95b49d800badcd89a4deb1"),
    "input_review": (INPUT_DIR / "independent-review.json", "666463b00303314583df7c6413c803d417b01d94f680e96c9b0696ab0fb7499c"),
    "exact_overlap": (Path(exact_overlap.__file__).resolve(), "535e57dfdf656b4000a9e0703ab4d4aa3b8ea5f0d86b0b60f8d1a8aa4c7b86d1"),
    "placed_pad_geometry": (Path(shared_pad.__file__).resolve(), "f6a53ca964efe79bed9da2bc88099d395f5f01f65d9b925ab9c8e18749ce8b4c"),
    "persistence_helper": (Path(mass.__file__).resolve(), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
EXPECTED_ROWS = 76_139
EXPECTED_XY_GROUPS = 38_836
EXPECTED_ISLANDS = 491
EXPECTED_EXCLUDED = 42


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def decode_text(array: np.ndarray) -> list[str]:
    value = json.loads(np.asarray(array, dtype=np.uint8).tobytes().decode("utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("packed text is not a JSON string vector")
    return value


def packed_text(values: list[str]) -> np.ndarray:
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return np.frombuffer(encoded, dtype=np.uint8)


def atomic_json_replace(path: Path, value: object) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_pins() -> dict[str, dict[str, object]]:
    result = {}
    for name, (path, expected) in PINS.items():
        actual = digest(path)
        if actual != expected:
            raise ValueError(f"{name} SHA-256 differs: {actual}")
        result[name] = {"path": str(path), "sha256": expected, "bytes": path.stat().st_size}
    return result


def placed_circle(x_pm: int, y_pm: int, diameter_pm: int):
    if diameter_pm <= 0:
        raise ValueError("circle diameter must be positive")
    diameter_um = float(diameter_pm) * 1.0e-6
    placed = shared_pad._PlacedPad(
        owner_index=0,
        terminal="VIA",
        net_key="dgnd",
        x_um=float(x_pm) * 1.0e-6,
        y_um=float(y_pm) * 1.0e-6,
        kind="CIRCLE",
        width_um=diameter_um,
        height_um=diameter_um,
        rotation_degrees=0.0,
    )
    return shared_pad._placed_pad_polygon(placed)


def island_cache(polygons: list[object]):
    caches = []
    for polygon in polygons:
        if polygon.geom_type != "Polygon" or polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
            raise ValueError("cached island WKB is not one valid positive Polygon")
        shell = Polygon(polygon.exterior)
        holes = np.array([Polygon(ring) for ring in polygon.interiors], dtype=object)
        hole_tree = STRtree(holes) if len(holes) else None
        shapely.prepare(polygon)
        shapely.prepare(shell)
        caches.append((polygon, shell, holes, hole_tree))
    return caches, STRtree(np.array(polygons, dtype=object))


def positive_overlaps(footprint, caches, tree) -> tuple[tuple[int, float], ...]:
    rows = []
    for raw_index in tree.query(footprint):
        index = int(raw_index)
        target, shell, holes, hole_tree = caches[index]
        if hole_tree is None:
            cut = footprint if target.covers(footprint) else target.intersection(footprint)
        else:
            cut = exact_overlap.overlap(target, shell, holes, hole_tree, footprint)
        area = float(cut.area)
        if area > 0.0:
            rows.append((index, area))
    rows.sort()
    return tuple(rows)


def classify_key(
    key: tuple[int, int, int, int],
    caches,
    tree,
    footprint_cache: dict[tuple[int, int, int], object],
) -> tuple[tuple[tuple[int, float], ...], tuple[tuple[int, float], ...]]:
    x_pm, y_pm, pad_diameter_pm, drill_diameter_pm = key
    def footprint(diameter_pm: int):
        cache_key = (x_pm, y_pm, diameter_pm)
        value = footprint_cache.get(cache_key)
        if value is None:
            value = placed_circle(*cache_key)
            footprint_cache[cache_key] = value
        return value
    return (
        positive_overlaps(footprint(pad_diameter_pm), caches, tree),
        positive_overlaps(footprint(drill_diameter_pm), caches, tree),
    )


def csr(rows: list[tuple[tuple[int, float], ...]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    offsets = np.empty(len(rows) + 1, dtype=np.int64)
    offsets[0] = 0
    for index, row in enumerate(rows):
        offsets[index + 1] = offsets[index] + len(row)
    indices = np.fromiter((item[0] for row in rows for item in row), dtype=np.int32, count=int(offsets[-1]))
    areas = np.fromiter((item[1] for row in rows for item in row), dtype=np.float64, count=int(offsets[-1]))
    return offsets, indices, areas


def self_check() -> dict[str, object]:
    outer = Polygon(box(0, 0, 10, 10).exterior.coords, [box(4, 4, 6, 6).exterior.coords])
    separate = box(10.5, 4, 12, 6)
    caches, tree = island_cache([outer, separate])
    footprints = {}
    wholly_inside = positive_overlaps(placed_circle(2_000_000, 2_000_000, 1_000_000), caches, tree)
    across_hole = positive_overlaps(placed_circle(4_000_000, 5_000_000, 3_000_000), caches, tree)
    multi = positive_overlaps(placed_circle(10_250_000, 5_000_000, 2_000_000), caches, tree)
    tangent = positive_overlaps(placed_circle(13_000_000, 5_000_000, 2_000_000), caches, tree)
    assert len(wholly_inside) == 1 and wholly_inside[0][0] == 0
    assert len(across_hole) == 1 and across_hole[0][1] > 0
    assert {row[0] for row in multi} == {0, 1}
    assert tangent == ()
    for footprint in (
        placed_circle(2_000_000, 2_000_000, 1_000_000),
        placed_circle(4_000_000, 5_000_000, 3_000_000),
        placed_circle(10_250_000, 5_000_000, 2_000_000),
        placed_circle(13_000_000, 5_000_000, 2_000_000),
    ):
        optimized = positive_overlaps(footprint, caches, tree)
        direct = tuple(
            (index, float(cut.area))
            for index, polygon in enumerate((outer, separate))
            if (cut := polygon.intersection(footprint)).area > 0.0
        )
        assert [row[0] for row in optimized] == [row[0] for row in direct]
        assert np.allclose([row[1] for row in optimized], [row[1] for row in direct], rtol=0.0, atol=1e-12)
    key = (2_000_000, 2_000_000, 1_000_000, 500_000)
    first = classify_key(key, caches, tree, footprints)
    second = classify_key(key, caches, tree, footprints)
    assert first == second and len(footprints) == 2
    offsets, indices, areas = csr([wholly_inside, (), multi])
    assert offsets.tolist() == [0, 1, 1, 3]
    assert indices.tolist() == [0, 0, 1] and np.all(areas > 0)
    return {"program": PROGRAM, "version": VERSION, "status": "PASS", "cached_footprints": len(footprints)}


def load_inputs():
    receipt = json.loads(INPUT_RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("program") != PROGRAM or receipt.get("version") != VERSION:
        raise ValueError("input receipt program/version differs")
    if receipt.get("status") != "COMPLETED_L02_ORDERED_CONTACT_INPUT_LEDGER":
        raise ValueError("input receipt status differs")
    if receipt["native_contacts"]["count"] != EXPECTED_ROWS:
        raise ValueError("native contact count differs")
    if receipt["native_contacts"]["unique_center_count"] != EXPECTED_XY_GROUPS:
        raise ValueError("coincident center count differs")
    if receipt["target"]["island_count"] != EXPECTED_ISLANDS:
        raise ValueError("island count differs")
    if receipt["excluded_source_vias"]["count"] != EXPECTED_EXCLUDED:
        raise ValueError("excluded Via count differs")
    return receipt


def dry_check() -> dict[str, object]:
    started = time.monotonic()
    inputs = verify_pins()
    receipt = load_inputs()
    with np.load(INPUT_ARRAYS, allow_pickle=False) as arrays:
        required = {
            "active_finite_index", "x_pm", "y_pm", "padstack_index",
            "coincident_group_index", "island_ids_json_utf8", "island_wkb_bytes",
            "island_wkb_offsets", "excluded_source_via_ids_json_utf8",
        }
        if not required <= set(arrays.files):
            raise ValueError("input NPZ schema is incomplete")
        if arrays["active_finite_index"].shape != (EXPECTED_ROWS,):
            raise ValueError("native row array shape differs")
        if len(decode_text(arrays["island_ids_json_utf8"])) != EXPECTED_ISLANDS:
            raise ValueError("packed island ID count differs")
        if len(decode_text(arrays["excluded_source_via_ids_json_utf8"])) != EXPECTED_EXCLUDED:
            raise ValueError("packed excluded Via count differs")
        if arrays["island_wkb_offsets"].shape != (EXPECTED_ISLANDS + 1,):
            raise ValueError("island WKB offset shape differs")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_L02_CACHED_CONTACT_DRY_CHECK",
        "inputs": inputs,
        "native_rows": EXPECTED_ROWS,
        "exact_xy_groups": EXPECTED_XY_GROUPS,
        "islands": EXPECTED_ISLANDS,
        "excluded_rows": EXPECTED_EXCLUDED,
        "pad_definitions": receipt["pad_definitions"],
        "elapsed_s": time.monotonic() - started,
    }


def run(output: Path, maximum_s: float) -> dict[str, object]:
    started = time.monotonic()
    deadline = started + maximum_s
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    atomic_json_replace(output / "checkpoint.json", {"program": PROGRAM, "version": VERSION, "stage": "pins"})
    inputs = verify_pins()
    receipt = load_inputs()
    pad_defs = sorted(receipt["pad_definitions"], key=lambda row: row["padstack_index"])
    pad_diameters = np.array([row["l02_pad_shape"]["width_pm"] for row in pad_defs], dtype=np.int64)
    drill_diameters = np.array([row["padstack"]["drill_diameter_pm"] for row in pad_defs], dtype=np.int64)
    if (len(pad_defs) != 4 or np.any(pad_diameters <= drill_diameters) or np.any(drill_diameters <= 0)
            or any(row["l02_pad_shape"]["shape_kind"] != "CIRCLE" for row in pad_defs)
            or any(row["padstack"]["material"] != "COPPER" for row in pad_defs)):
        raise ValueError("four circular COPPER pad/drill definitions were not preserved")

    with np.load(INPUT_ARRAYS, allow_pickle=False) as source:
        active_finite_index = np.array(source["active_finite_index"], dtype=np.int64)
        source_via_ordinal = np.array(source["source_via_ordinal"], dtype=np.int64)
        via_ids_bytes = np.array(source["via_ids_json_utf8"], dtype=np.uint8)
        l02_endpoint_is_start = np.array(source["l02_endpoint_is_start"], dtype=np.bool_)
        native_x = np.array(source["x_pm"], dtype=np.int64)
        native_y = np.array(source["y_pm"], dtype=np.int64)
        native_padstack = np.array(source["padstack_index"], dtype=np.int16)
        coincident = np.array(source["coincident_group_index"], dtype=np.int64)
        island_ids_bytes = np.array(source["island_ids_json_utf8"], dtype=np.uint8)
        island_ids = decode_text(island_ids_bytes)
        wkb_bytes = np.array(source["island_wkb_bytes"], dtype=np.uint8)
        wkb_offsets = np.array(source["island_wkb_offsets"], dtype=np.int64)
        map_arrays = {
            name: np.array(source[name]) for name in (
                "island_area_um2", "island_hole_count", "island_coordinate_count", "island_bounds_um"
            )
        }
    if native_x.shape != (EXPECTED_ROWS,) or native_y.shape != native_x.shape:
        raise ValueError("native coordinate arrays differ")
    if len(np.unique(coincident)) != EXPECTED_XY_GROUPS:
        raise ValueError("coincident group identity differs")
    if len(island_ids) != EXPECTED_ISLANDS or wkb_offsets[-1] != len(wkb_bytes):
        raise ValueError("island WKB map differs")
    polygons = [
        shapely.from_wkb(wkb_bytes[wkb_offsets[i]:wkb_offsets[i + 1]].tobytes())
        for i in range(EXPECTED_ISLANDS)
    ]
    caches, tree = island_cache(polygons)
    geometry_map = output / "l02-island-geometry-map.npz"
    mass.atomic_npz(
        geometry_map,
        island_ids_json_utf8=island_ids_bytes,
        island_wkb_bytes=wkb_bytes,
        island_wkb_offsets=wkb_offsets,
        source_metadata_json_utf8=packed_text([
            json.dumps({
                "source_receipt_sha256": PINS["input_receipt"][1],
                "source_npz_sha256": PINS["input_arrays"][1],
                "geometry_asset_sha256": receipt["target"]["geometry_asset_sha256"],
                "component_id": receipt["target"]["component_id"],
            }, sort_keys=True, separators=(",", ":"))
        ]),
        **map_arrays,
    )
    atomic_json_replace(output / "checkpoint.json", {"program": PROGRAM, "version": VERSION, "stage": "classifying"})

    native_keys = [
        (int(x), int(y), int(pad_diameters[p]), int(drill_diameters[p]))
        for x, y, p in zip(native_x, native_y, native_padstack, strict=True)
    ]
    excluded_rows = receipt["excluded_source_vias"]["ordered_rows"]
    pad_index_by_name = {row["padstack"]["padstack_id"].casefold(): row["padstack_index"] for row in pad_defs}
    excluded_keys = [
        (
            int(row["start_x_pm"]), int(row["start_y_pm"]),
            int(pad_diameters[pad_index_by_name[row["padstack_id"].casefold()]]),
            int(drill_diameters[pad_index_by_name[row["padstack_id"].casefold()]]),
        )
        for row in excluded_rows
    ]
    unique_keys = list(dict.fromkeys([*native_keys, *excluded_keys]))
    key_results = {}
    footprint_cache = {}
    for index, key in enumerate(unique_keys):
        if time.monotonic() >= deadline:
            raise TimeoutError("L02 contact classification exceeded --max-seconds")
        key_results[key] = classify_key(key, caches, tree, footprint_cache)
        if index % 256 == 0:
            atomic_json_replace(output / "checkpoint.json", {
                "program": PROGRAM, "version": VERSION, "stage": "classifying",
                "unique_supports_completed": index + 1, "unique_supports_total": len(unique_keys),
                "elapsed_s": time.monotonic() - started,
            })
    native_pad = [key_results[key][0] for key in native_keys]
    native_drill = [key_results[key][1] for key in native_keys]
    excluded_pad = [key_results[key][0] for key in excluded_keys]
    excluded_drill = [key_results[key][1] for key in excluded_keys]
    native_pad_csr = csr(native_pad)
    native_drill_csr = csr(native_drill)
    excluded_pad_csr = csr(excluded_pad)
    excluded_drill_csr = csr(excluded_drill)

    # Preserve exact noncoincident polygon-overlap evidence between unique native
    # pad supports.  These pairs are candidates for a later reviewed union; they
    # do not merge contacts or assign current here.
    pad_support_keys = list(dict.fromkeys((key[0], key[1], key[2]) for key in native_keys))
    support_ordinal = {key: index for index, key in enumerate(pad_support_keys)}
    native_pad_support_index = np.array(
        [support_ordinal[(key[0], key[1], key[2])] for key in native_keys], dtype=np.int32
    )
    support_polygons = np.array([footprint_cache[key] for key in pad_support_keys], dtype=object)
    support_tree = STRtree(support_polygons)
    overlap_left, overlap_right, overlap_area = [], [], []
    tangent_pair_count = 0
    for left, polygon in enumerate(support_polygons):
        if time.monotonic() >= deadline:
            raise TimeoutError("L02 contact-support overlap classification exceeded --max-seconds")
        for right in support_tree.query(polygon, predicate="intersects"):
            right = int(right)
            if right <= left or pad_support_keys[left][:2] == pad_support_keys[right][:2]:
                continue
            area = float(polygon.intersection(support_polygons[right]).area)
            if area > 0.0:
                overlap_left.append(left)
                overlap_right.append(right)
                overlap_area.append(area)
            else:
                tangent_pair_count += 1
    support_count_by_xy = Counter(key[:2] for key in pad_support_keys)
    coincident_distinct_radius_pair_count = sum(count * (count - 1) // 2 for count in support_count_by_xy.values())

    artifact = output / "l02-source-contact-overlaps.npz"
    mass.atomic_npz(
        artifact,
        native_active_finite_index=active_finite_index, native_source_via_ordinal=source_via_ordinal,
        native_via_ids_json_utf8=via_ids_bytes, native_l02_endpoint_is_start=l02_endpoint_is_start,
        native_padstack_index=native_padstack, native_coincident_group_index=coincident,
        native_pad_overlap_offsets=native_pad_csr[0], native_pad_island_indices=native_pad_csr[1], native_pad_overlap_area_um2=native_pad_csr[2],
        native_drill_overlap_offsets=native_drill_csr[0], native_drill_island_indices=native_drill_csr[1], native_drill_overlap_area_um2=native_drill_csr[2],
        native_pad_match_count=np.diff(native_pad_csr[0]).astype(np.int16), native_drill_match_count=np.diff(native_drill_csr[0]).astype(np.int16),
        native_singular_pad_island_index=np.array([row[0][0] if len(row) == 1 else -1 for row in native_pad], dtype=np.int32),
        excluded_via_ids_json_utf8=packed_text([row["via_id"] for row in excluded_rows]),
        excluded_x_pm=np.array([key[0] for key in excluded_keys], dtype=np.int64), excluded_y_pm=np.array([key[1] for key in excluded_keys], dtype=np.int64),
        excluded_pad_overlap_offsets=excluded_pad_csr[0], excluded_pad_island_indices=excluded_pad_csr[1], excluded_pad_overlap_area_um2=excluded_pad_csr[2],
        excluded_drill_overlap_offsets=excluded_drill_csr[0], excluded_drill_island_indices=excluded_drill_csr[1], excluded_drill_overlap_area_um2=excluded_drill_csr[2],
        native_pad_support_index=native_pad_support_index,
        pad_support_xy_pm=np.asarray([[key[0], key[1]] for key in pad_support_keys], dtype=np.int64),
        pad_support_diameter_pm=np.asarray([key[2] for key in pad_support_keys], dtype=np.int64),
        noncoincident_pad_overlap_left_support=np.asarray(overlap_left, dtype=np.int32),
        noncoincident_pad_overlap_right_support=np.asarray(overlap_right, dtype=np.int32),
        noncoincident_pad_overlap_area_um2=np.asarray(overlap_area, dtype=np.float64),
    )
    native_hist = Counter(len(row) for row in native_pad)
    drill_hist = Counter(len(row) for row in native_drill)
    excluded_hist = Counter(len(row) for row in excluded_pad)
    excluded_drill_hist = Counter(len(row) for row in excluded_drill)
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_L02_CACHED_CIRCLE_CONTACT_OVERLAPS",
        "script_sha256": digest(Path(__file__)),
        "inputs": inputs,
        "native": {
            "ordered_row_count": EXPECTED_ROWS,
            "exact_xy_group_count": EXPECTED_XY_GROUPS,
            "pad_overlap_count_histogram": {str(k): int(v) for k, v in sorted(native_hist.items())},
            "drill_overlap_count_histogram": {str(k): int(v) for k, v in sorted(drill_hist.items())},
            "zero_polygonal_pad_overlap_count": int(native_hist[0]),
            "multi_island_pad_overlap_count": sum(v for k, v in native_hist.items() if k > 1),
            "prospective_coincident_contact_count": EXPECTED_XY_GROUPS,
            "prospective_island_contact_dof_count": len({(int(group), island) for group, row in zip(coincident, native_pad, strict=True) for island, _area in row}),
            "unique_polygonal_pad_support_count": len(pad_support_keys),
            "noncoincident_positive_pad_overlap_pair_count": len(overlap_left),
            "noncoincident_tangent_pad_pair_count": tangent_pair_count,
            "coincident_distinct_radius_support_pair_count": coincident_distinct_radius_pair_count,
        },
        "excluded": {
            "ordered_row_count": EXPECTED_EXCLUDED,
            "native_indices_present": False,
            "via_ids": [row["via_id"] for row in excluded_rows],
            "pad_overlap_count_histogram": {str(k): int(v) for k, v in sorted(excluded_hist.items())},
            "drill_overlap_count_histogram": {str(k): int(v) for k, v in sorted(excluded_drill_hist.items())},
        },
        "geometry": {
            "island_count": EXPECTED_ISLANDS,
            "map_path": str(geometry_map.resolve()),
            "map_sha256": digest(geometry_map),
            "exact_circle_builder": "shared_pad._PlacedPad + shared_pad._placed_pad_polygon; quad_segs=64",
            "intersection_method": "prepared island covers, then shell intersection minus STRtree-selected exact holes",
        },
        "output": {"path": str(artifact.resolve()), "sha256": digest(artifact), "bytes": artifact.stat().st_size},
        "elapsed_s": time.monotonic() - started,
        "scope": "Positive polygonal pad/drill-to-island area evidence only. Multi-island contacts remain explicit. A zero area for the 256-edge inscribed circle is unresolved and is not analytic-circle no-contact proof. The 42 excluded rows have no fabricated native indices. No trace/domain union, mesh, operator, solve, or current split.",
    }
    mass.atomic_json(output / "result.json", result)
    atomic_json_replace(output / "checkpoint.json", {"program": PROGRAM, "version": VERSION, "stage": "complete", "result_sha256": digest(output / "result.json")})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--dry-check", action="store_true")
    mode.add_argument("--output", type=Path)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check(), sort_keys=True))
    elif args.dry_check:
        print(json.dumps(dry_check(), sort_keys=True))
    else:
        result = run(args.output.resolve(), args.max_seconds)
        print(json.dumps({key: result[key] for key in ("status", "native", "excluded", "geometry", "output", "elapsed_s")}, sort_keys=True))


if __name__ == "__main__":
    main()
