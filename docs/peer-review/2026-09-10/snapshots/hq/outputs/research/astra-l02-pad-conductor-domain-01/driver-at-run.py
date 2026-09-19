"""Add exact source pad footprints to the conditional L02 flat domain.

Drill footprints are preserved as a separate union. They are not subtracted
from conductor copper, and this helper does not choose an electrode policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import shapely
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spd_decap_pi._core.io import shared_pad
import project_astra_l14_gc_mass as mass

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RESEARCH = ROOT / "outputs/research"
PINS = {
    "flat_result": (RESEARCH / "astra-l02-flat-conductor-domain-01/result.json", "c2757aa69aca3186852b8332c2ccf6cc84ec170f1a34fdb0098a09a74e8050d4"),
    "flat_domain": (RESEARCH / "astra-l02-flat-conductor-domain-01/l02-artwork-flat-trace-domain.wkb", "b99d76360170a0e3c80dde1a84982fd5d6c5658e2bdc9cc0c261123c8bdb0e26"),
    "flat_review": (RESEARCH / "astra-l02-flat-conductor-domain-01/independent-review.json", "362f13294cbbc4cd9954daa6f3c485fcba823f7f71bb6a4d73267da53245efb7"),
    "flat_boundary_cost": (RESEARCH / "astra-l02-flat-conductor-domain-01/boundary-cost-diagnostic.json", "52e71117958b618425ac5aa88afa9fa1ac16805cf498d645aace85d2a42e2ce6"),
    "contact_result": (RESEARCH / "astra-l02-source-contact-inputs-01/result.json", "98bce62fde03d676e43d121207d00bb5ed840552bf8fd02793ae6732ab8bffd2"),
    "contact_inputs": (RESEARCH / "astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz", "c5c0dab9ac22d750aeecf3a4290c2b9ef9285bcf7ed33a5d8a7a2775c8430984"),
    "overlap_result": (RESEARCH / "astra-l02-source-contact-overlaps-01/result.json", "5431d8f042b3c4ac3d53c28441f0301fad6a7a350a87add98aa6f9466e6b272f"),
    "overlap_inputs": (RESEARCH / "astra-l02-source-contact-overlaps-01/l02-source-contact-overlaps.npz", "dcdc4b8022cfce7e2decb206a812fc62f75b968a5c3e0d0cb2b27f4395707501"),
    "overlap_review": (RESEARCH / "astra-l02-source-contact-overlaps-01/independent-review.json", "10ae993144dcda1e01503cf70287ada4e02b5b5e541189bbeec0c792496cf811"),
    "excluded_result": (RESEARCH / "astra-l02-excluded-via-flat-domain-01/result.json", "5e18de3bba96b07870175058852aacd5ac2c937c8ffe946bd95518899e337587"),
    "excluded_review": (RESEARCH / "astra-l02-excluded-via-flat-domain-01/independent-review.json", "707ed47c3380ab6af1b0e79e38327337b74afa0bc50364165218dcb4e3702a7c"),
    "placed_pad_geometry": (Path(shared_pad.__file__).resolve(), "f6a53ca964efe79bed9da2bc88099d395f5f01f65d9b925ab9c8e18749ce8b4c"),
    "persistence_helper": (Path(mass.__file__).resolve(), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
NATIVE_ROWS = 76_139
EXCLUDED_ROWS = 42


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def packed_text(values: list[str]) -> np.ndarray:
    return np.frombuffer(json.dumps(values, separators=(",", ":")).encode(), dtype=np.uint8)


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_pins() -> dict[str, dict[str, object]]:
    checked = {}
    for name, (path, expected) in PINS.items():
        actual = digest(path)
        if actual != expected:
            raise ValueError(f"{name} SHA-256 differs: {actual}")
        checked[name] = {"path": str(path), "sha256": expected, "bytes": path.stat().st_size}
    return checked


def placed_circle(key: tuple[int, int, int]):
    x_pm, y_pm, diameter_pm = key
    if diameter_pm <= 0:
        raise ValueError("circle diameter must be positive")
    diameter_um = float(diameter_pm) * 1.0e-6
    polygon = shared_pad._placed_pad_polygon(shared_pad._PlacedPad(
        owner_index=0, terminal="VIA", net_key="dgnd",
        x_um=float(x_pm) * 1.0e-6, y_um=float(y_pm) * 1.0e-6,
        kind="CIRCLE", width_um=diameter_um, height_um=diameter_um,
        rotation_degrees=0.0,
    ))
    if polygon.geom_type != "Polygon" or polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError("source circle footprint is not one valid positive Polygon")
    return polygon


def support_table(pad_keys: list[tuple[int, int, int]], drill_keys: list[tuple[int, int, int]]):
    if len(pad_keys) != len(drill_keys):
        raise ValueError("pad/drill row counts differ")
    keys: list[tuple[int, int, int]] = []
    index_by_key: dict[tuple[int, int, int], int] = {}
    pad_map = np.empty(len(pad_keys), dtype=np.int32)
    drill_map = np.empty(len(drill_keys), dtype=np.int32)
    for row, (pad, drill) in enumerate(zip(pad_keys, drill_keys, strict=True)):
        for key, target in ((pad, pad_map), (drill, drill_map)):
            if key not in index_by_key:
                index_by_key[key] = len(keys)
                keys.append(key)
            target[row] = index_by_key[key]
    return keys, pad_map, drill_map


def union_batched(polygons, deadline: float | None, label: str, batch: int = 512):
    level = list(polygons)
    if not level:
        raise ValueError(f"{label} has no polygons")
    depth = 0
    while len(level) > 1:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"{label} union exceeded --max-seconds")
        level = [shapely.union_all(np.asarray(level[i:i + batch], dtype=object)) for i in range(0, len(level), batch)]
        depth += 1
        print(json.dumps({"event": "union_level", "label": label, "depth": depth, "parts": len(level)}), flush=True)
    value = shapely.normalize(level[0])
    if value.is_empty or not value.is_valid or value.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"{label} union is not valid polygonal geometry")
    return value


def parts(geometry):
    return list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]


def boundary_stats(geometry) -> dict[str, object]:
    ring_count = duplicate_count = collinear_count = vertex_count = 0
    hole_histogram: dict[int, int] = {}
    outer_counts = []
    polygons = parts(geometry)
    for polygon in polygons:
        rings = [polygon.exterior, *polygon.interiors]
        ring_count += len(rings)
        outer_counts.append(len(polygon.exterior.coords) - 1)
        for ring_index, ring in enumerate(rings):
            xy = np.asarray(ring.coords, dtype=np.float64)[:-1]
            count = len(xy)
            vertex_count += count
            duplicate_count += count - len(np.unique(xy, axis=0))
            incoming = xy - np.roll(xy, 1, axis=0)
            outgoing = np.roll(xy, -1, axis=0) - xy
            cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
            dot = np.einsum("ij,ij->i", incoming, outgoing)
            collinear_count += int(np.count_nonzero((cross == 0.0) & (dot > 0.0)))
            if ring_index:
                hole_histogram[count] = hole_histogram.get(count, 0) + 1
    return {
        "component_count": len(polygons), "ring_count": ring_count,
        "hole_count": ring_count - len(polygons), "outer_boundary_vertex_counts": outer_counts,
        "total_boundary_vertex_occurrences": vertex_count,
        "duplicate_within_ring_vertex_occurrences": duplicate_count,
        "exact_float_collinear_same_direction_vertex_count": collinear_count,
        "hole_vertex_count_histogram": {str(k): v for k, v in sorted(hole_histogram.items())},
        "coordinate_count_with_ring_closures": int(shapely.get_num_coordinates(geometry)),
        "area_um2": float(geometry.area),
    }


def input_rows():
    receipt = json.loads(PINS["contact_result"][0].read_bytes())
    overlap = json.loads(PINS["overlap_result"][0].read_bytes())
    excluded_result = json.loads(PINS["excluded_result"][0].read_bytes())
    if receipt.get("status") != "COMPLETED_L02_ORDERED_CONTACT_INPUT_LEDGER" or overlap.get("status") != "COMPLETED_L02_CACHED_CIRCLE_CONTACT_OVERLAPS":
        raise ValueError("contact input/overlap status differs")
    excluded_rows = receipt["excluded_source_vias"]["ordered_rows"]
    if len(excluded_rows) != EXCLUDED_ROWS:
        raise ValueError("excluded source row count differs")
    pad_defs = sorted(receipt["pad_definitions"], key=lambda row: row["padstack_index"])
    if len(pad_defs) != 4:
        raise ValueError("four source pad definitions were not preserved")
    pad_diameter = np.asarray([row["l02_pad_shape"]["width_pm"] for row in pad_defs], dtype=np.int64)
    drill_diameter = np.asarray([row["padstack"]["drill_diameter_pm"] for row in pad_defs], dtype=np.int64)
    if np.any(pad_diameter <= drill_diameter) or any(row["l02_pad_shape"]["shape_kind"] != "CIRCLE" for row in pad_defs):
        raise ValueError("source pad/drill geometry differs")
    with np.load(PINS["contact_inputs"][0], allow_pickle=False) as source:
        x = np.asarray(source["x_pm"], dtype=np.int64)
        y = np.asarray(source["y_pm"], dtype=np.int64)
        padstack = np.asarray(source["padstack_index"], dtype=np.int16)
        active = np.asarray(source["active_finite_index"], dtype=np.int64)
        via_ordinal = np.asarray(source["source_via_ordinal"], dtype=np.int64)
        native_ids = np.asarray(source["via_ids_json_utf8"], dtype=np.uint8)
    if x.shape != (NATIVE_ROWS,) or y.shape != x.shape or padstack.shape != x.shape:
        raise ValueError("native contact arrays differ")
    native_pad = [(int(a), int(b), int(pad_diameter[c])) for a, b, c in zip(x, y, padstack, strict=True)]
    native_drill = [(int(a), int(b), int(drill_diameter[c])) for a, b, c in zip(x, y, padstack, strict=True)]
    by_name = {row["padstack"]["padstack_id"].casefold(): row for row in pad_defs}
    excluded_pad, excluded_drill = [], []
    for row in excluded_rows:
        definition = by_name[row["padstack_id"].casefold()]
        excluded_pad.append((int(row["start_x_pm"]), int(row["start_y_pm"]), int(definition["l02_pad_shape"]["width_pm"])))
        excluded_drill.append((int(row["start_x_pm"]), int(row["start_y_pm"]), int(definition["padstack"]["drill_diameter_pm"])))
    witnesses = {row["via_id"].casefold(): row for row in excluded_result["rows"]}
    if set(witnesses) != {row["via_id"].casefold() for row in excluded_rows} or any(row["pad_overlap_area_um2"] <= 0 for row in witnesses.values()):
        raise ValueError("42 flat-domain pad witnesses differ")
    ordered_witnesses = [witnesses[row["via_id"].casefold()] for row in excluded_rows]
    if sum(bool(row["pad_polygon_fully_covered"]) for row in ordered_witnesses) != 2:
        raise ValueError("excluded-pad full-coverage witness count differs")
    return receipt, excluded_rows, ordered_witnesses, native_pad, native_drill, excluded_pad, excluded_drill, active, via_ordinal, native_ids


def validate_support_maps(all_pad, keys, pad_map, all_drill, drill_map) -> None:
    if any(keys[int(index)] != key for index, key in zip(pad_map, all_pad, strict=True)) or any(keys[int(index)] != key for index, key in zip(drill_map, all_drill, strict=True)):
        raise ValueError("pad/drill support map differs")
    with np.load(PINS["overlap_inputs"][0], allow_pickle=False) as overlap:
        old_map = np.asarray(overlap["native_pad_support_index"], dtype=np.int32)
        old_xy = np.asarray(overlap["pad_support_xy_pm"], dtype=np.int64)
        old_diameter = np.asarray(overlap["pad_support_diameter_pm"], dtype=np.int64)
    old_keys = [(int(old_xy[i, 0]), int(old_xy[i, 1]), int(old_diameter[i])) for i in range(len(old_xy))]
    if any(all_pad[row] != old_keys[int(old_map[row])] for row in range(NATIVE_ROWS)):
        raise ValueError("native pad support identity differs from accepted overlap ledger")


def self_check() -> None:
    pad = placed_circle((3_000_000, 1_000_000, 3_000_000))
    drill = placed_circle((3_000_000, 1_000_000, 1_000_000))
    flat = shapely.union_all([box(0, 0, 2, 2), box(4, 0, 6, 2)])
    domain = shapely.normalize(shapely.union_all([flat, pad]))
    assert domain.geom_type == "Polygon" and domain.covers(flat) and domain.covers(drill)
    assert not domain.covers(pad) and pad.difference(domain).area == 0.0
    assert abs(domain.difference(drill).area - (domain.area - drill.area)) < 1e-12
    keys, pad_map, drill_map = support_table([(0, 0, 100), (0, 0, 100), (1, 0, 100)], [(0, 0, 60), (0, 0, 60), (1, 0, 60)])
    assert len(keys) == 4 and pad_map.tolist() == [0, 0, 2] and drill_map.tolist() == [1, 1, 3]
    assert boundary_stats(domain)["duplicate_within_ring_vertex_occurrences"] == 0


def dry_check() -> dict[str, object]:
    started = time.monotonic()
    inputs = verify_pins()
    rows = input_rows()
    all_pad, all_drill = [*rows[3], *rows[5]], [*rows[4], *rows[6]]
    keys, pad_map, drill_map = support_table(all_pad, all_drill)
    validate_support_maps(all_pad, keys, pad_map, all_drill, drill_map)
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_L02_PAD_DOMAIN_DRY_CHECK",
            "inputs": inputs, "source_rows": len(all_pad), "combined_geometry_supports": len(keys),
            "unique_pad_supports": len(np.unique(pad_map)), "unique_drill_supports": len(np.unique(drill_map)),
            "elapsed_s": time.monotonic() - started}


def run(output: Path, maximum_s: float) -> dict[str, object]:
    started = time.monotonic()
    deadline = started + maximum_s
    inputs = verify_pins()
    receipt, excluded_rows, excluded_witnesses, native_pad, native_drill, excluded_pad, excluded_drill, active, via_ordinal, native_ids = input_rows()
    all_pad, all_drill = [*native_pad, *excluded_pad], [*native_drill, *excluded_drill]
    keys, pad_map, drill_map = support_table(all_pad, all_drill)
    validate_support_maps(all_pad, keys, pad_map, all_drill, drill_map)
    pad_support, drill_support = np.unique(pad_map), np.unique(drill_map)
    mass.atomic_json(output / "checkpoint.json", {"program": PROGRAM, "version": VERSION, "stage": "constructing_source_circles",
                     "combined_geometry_supports": len(keys), "unique_pad_supports": len(pad_support), "unique_drill_supports": len(drill_support)})
    polygons = np.asarray([placed_circle(key) for key in keys], dtype=object)
    if time.monotonic() >= deadline:
        raise TimeoutError("circle construction exceeded --max-seconds")
    pad_union = union_batched(polygons[pad_support], deadline, "source_pad")
    drill_union = union_batched(polygons[drill_support], deadline, "source_drill")
    flat = shapely.from_wkb(PINS["flat_domain"][0].read_bytes())
    if flat.is_empty or not flat.is_valid or flat.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("frozen flat domain differs")
    domain = shapely.normalize(shapely.union_all(np.asarray([flat, pad_union], dtype=object)))
    if domain.is_empty or not domain.is_valid or domain.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("pad-augmented domain is not valid polygonal geometry")
    pad_residue, drill_residue = pad_union.difference(domain), drill_union.difference(domain)
    strict_pad_cover, strict_drill_cover = bool(domain.covers(pad_union)), bool(domain.covers(drill_union))
    excluded_added = np.asarray([float(polygons[int(pad_map[NATIVE_ROWS + row])].difference(flat).area) for row in range(EXCLUDED_ROWS)], dtype=np.float64)
    witness_expected_added = np.asarray([
        float(row["pad_polygon_area_um2"] - row["pad_overlap_area_um2"])
        for row in excluded_witnesses
    ], dtype=np.float64)
    witness_fully_covered = np.asarray([bool(row["pad_polygon_fully_covered"]) for row in excluded_witnesses])
    witness_error = np.abs(excluded_added - witness_expected_added)
    witness_scale = np.maximum(1.0, np.abs(witness_expected_added))
    if (np.any(excluded_added < 0) or
            np.any(excluded_added[~witness_fully_covered] <= 0.0) or
            np.any(excluded_added[witness_fully_covered] > 1.0e-12) or
            float(np.max(witness_error / witness_scale)) > 1.0e-12):
        raise ValueError("excluded-pad added areas differ from frozen flat-domain witnesses")
    pad_stats, drill_stats = boundary_stats(domain), boundary_stats(drill_union)
    prior_cost = json.loads(PINS["flat_boundary_cost"][0].read_bytes())
    domain_path = output / "l02-pad-augmented-conductor-domain.wkb"
    drill_path = output / "l02-source-drill-footprint-union.wkb"
    atomic_bytes(domain_path, shapely.to_wkb(domain))
    atomic_bytes(drill_path, shapely.to_wkb(drill_union))
    arrays_path = output / "l02-pad-drill-support-map.npz"
    key_array = np.asarray(keys, dtype=np.int64)
    mass.atomic_npz(arrays_path, support_x_pm=key_array[:, 0], support_y_pm=key_array[:, 1], support_diameter_pm=key_array[:, 2],
                    support_is_pad=np.isin(np.arange(len(keys)), pad_support), support_is_drill=np.isin(np.arange(len(keys)), drill_support),
                    native_pad_support_index=pad_map[:NATIVE_ROWS], native_drill_support_index=drill_map[:NATIVE_ROWS],
                    excluded_pad_support_index=pad_map[NATIVE_ROWS:], excluded_drill_support_index=drill_map[NATIVE_ROWS:],
                    native_active_finite_index=active, native_source_via_ordinal=via_ordinal, native_via_ids_json_utf8=native_ids,
                    excluded_via_ids_json_utf8=packed_text([row["via_id"] for row in excluded_rows]), excluded_pad_added_area_um2=excluded_added)
    pad_coverage_exact = strict_pad_cover and pad_residue.area == 0.0
    status = ("COMPLETED_CONDITIONAL_L02_PAD_AUGMENTED_DOMAIN" if pad_coverage_exact else
              "COMPLETED_CONDITIONAL_L02_PAD_AUGMENTED_DOMAIN_WITH_PAD_COVERAGE_RESIDUE")
    result = {
        "program": PROGRAM, "version": VERSION, "status": status,
        "script_sha256": digest(Path(__file__)), "inputs": inputs,
        "source_rows": {"native": NATIVE_ROWS, "excluded": EXCLUDED_ROWS, "total": len(all_pad)},
        "supports": {"combined_geometry_support_count": len(keys), "unique_pad_support_count": len(pad_support),
                     "unique_drill_support_count": len(drill_support),
                     "support_used_as_both_pad_and_drill_count": len(set(map(int, pad_support)) & set(map(int, drill_support))),
                     "circle_convention": "shared_pad._PlacedPad + _placed_pad_polygon; quad_segs=64; 256-edge inscribed source polygon"},
        "pad_augmented_domain": {**pad_stats, "flat_domain_area_um2": float(flat.area), "added_union_area_um2": float(domain.area - flat.area),
                                 "strict_full_pad_union_coverage": strict_pad_cover, "full_pad_union_uncovered_area_um2": float(pad_residue.area)},
        "drill_footprint_union": {**drill_stats, "strict_domain_coverage": strict_drill_cover,
                                  "uncovered_area_um2": float(drill_residue.area), "subtracted_from_conductor": False},
        "excluded_pad_evidence": {"all42_positive_flat_domain_witnesses": True,
                                  "positive_added_area_above_1e_minus_12_um2_count": int(np.count_nonzero(excluded_added > 1.0e-12)),
                                  "near_zero_added_area_at_most_1e_minus_12_um2_count": int(np.count_nonzero(excluded_added <= 1.0e-12)),
                                  "frozen_fully_covered_count": int(np.count_nonzero(witness_fully_covered)),
                                  "maximum_relative_added_area_witness_error": float(np.max(witness_error / witness_scale)),
                                  "sum_per_row_added_area_um2_not_union_area": float(excluded_added.sum()),
                                  "minimum_per_row_added_area_um2": float(excluded_added.min()), "maximum_per_row_added_area_um2": float(excluded_added.max())},
        "prior_flat_boundary_cost": prior_cost,
        "outputs": {"domain": {"path": str(domain_path.resolve()), "sha256": digest(domain_path), "bytes": domain_path.stat().st_size},
                    "drill_union": {"path": str(drill_path.resolve()), "sha256": digest(drill_path), "bytes": drill_path.stat().st_size},
                    "support_map": {"path": str(arrays_path.resolve()), "sha256": digest(arrays_path), "bytes": arrays_path.stat().st_size}},
        "elapsed_s": time.monotonic() - started,
        "scope": "Exact source-convention pad polygons are unioned with the accepted conditional artwork+flat-trace domain. Drill polygons and ordered native/excluded support maps are preserved separately and are not subtracted or promoted to an electrode/rim policy. Counts and boundary vertices are source-geometry evidence, not a measured mesh/DOF/memory estimate. No current split, topology recollapse, mesh, G/C mass, R/L, solve, PowerSI fit or accuracy claim.",
    }
    if not strict_drill_cover or drill_residue.area != 0.0:
        mass.atomic_json(output / "geometry-gate.json", {"program": PROGRAM, "version": VERSION,
                         "status": "STOP_L02_DRILL_COVERAGE_GATE", "result_candidate": result})
        raise ValueError("pad-augmented domain does not strictly cover all source drill support")
    if result["elapsed_s"] >= maximum_s:
        raise TimeoutError("L02 pad-domain work exceeded --max-seconds")
    mass.atomic_json(output / "result.json", result)
    mass.atomic_json(output / "checkpoint.json", {"program": PROGRAM, "version": VERSION, "stage": "complete",
                     "result_sha256": digest(output / "result.json"), "elapsed_s": result["elapsed_s"]})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--dry-check", action="store_true")
    mode.add_argument("--output", type=Path)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("PASS_L02_PAD_CONDUCTOR_DOMAIN_SELF_CHECK")
    elif args.dry_check:
        print(json.dumps(dry_check(), sort_keys=True))
    else:
        output = args.output.resolve()
        output.mkdir(exist_ok=False)
        atomic_bytes(output / "driver-at-run.py", Path(__file__).read_bytes())
        try:
            result = run(output, args.max_seconds)
        except BaseException as exc:
            mass.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L02_PAD_AUGMENTED_DOMAIN",
                             "error_type": type(exc).__name__, "error": str(exc)})
            raise
        print(json.dumps({"status": result["status"], "supports": result["supports"],
                          "pad_augmented_domain": result["pad_augmented_domain"], "drill_footprint_union": result["drill_footprint_union"],
                          "elapsed_s": result["elapsed_s"]}, sort_keys=True))


if __name__ == "__main__":
    main()
