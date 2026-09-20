"""Bounded local source-copper contact query for two ground endpoints.

The acceptance witness is one positive source primitive covering the whole
pad disk while every relevant negative primitive misses it.  This is a
sufficient local witness; it does not claim complete positive-union coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import zlib

from shapely.geometry import Point, Polygon


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
DEFAULT_INPUT_DIR = Path("outputs/research/astra-step3-source-index-01/ground-source-01")
DEFAULT_OUTPUT = DEFAULT_INPUT_DIR / "ground-contact-membership.json"
MAX_ENDPOINTS = 2
MAX_RUNTIME_S = 55.0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _order_sha256(order: list[list[object]]) -> str:
    return _sha256(json.dumps(order, separators=(",", ":")).encode())


def _check_runtime(started: float, max_runtime_s: float) -> None:
    if time.monotonic() - started >= max_runtime_s:
        raise TimeoutError("ground contact query exceeded its bounded runtime")


def _query_contact(contact: dict[str, object], started: float, max_runtime_s: float) -> dict[str, object]:
    node = contact["node"]
    via = contact["via"]
    pad_shape = contact["pad_shape"]
    surface = contact["surface"]
    attachment = contact["attachment"]
    source_path = Path(str(contact["compressed_path"]))
    compressed = source_path.read_bytes()
    if len(compressed) != int(attachment["size"]):
        raise ValueError(f"compressed size mismatch for {source_path.name}")
    compressed_sha256 = _sha256(compressed)
    if compressed_sha256 != attachment["sha256"]:
        raise ValueError(f"compressed hash mismatch for {source_path.name}")
    decoded = zlib.decompress(compressed)
    if len(decoded) != int(contact["decoded_bytes"]):
        raise ValueError(f"decoded size mismatch for {source_path.name}")
    decoded_sha256 = _sha256(decoded)
    if decoded_sha256 != contact["decoded_sha256"]:
        raise ValueError(f"decoded hash mismatch for {source_path.name}")
    payload = json.loads(decoded)
    required = {
        "positive_polygons_um",
        "negative_polygons_um",
        "positive_circles_um",
        "negative_circles_um",
        "primitive_order",
    }
    if not required.issubset(payload):
        raise ValueError(f"selected source payload is missing required geometry keys: {source_path.name}")
    if payload.get("format") != "powersi-spd-plane-primitives-v1":
        raise ValueError(f"unexpected source payload format for {source_path.name}")
    if payload.get("layer") != surface["layer_id"] or payload.get("net") != surface["net_name"]:
        raise ValueError(f"source layer/net mismatch for {source_path.name}")
    if any(not key.endswith("_um") for key in required if key != "primitive_order"):
        raise ValueError("selected source geometry coordinate contract is not um")
    if via["padstack_id_fold"] != pad_shape["padstack_id_fold"]:
        raise ValueError(f"via/padstack association mismatch for {node['node_id']}")
    if via["end_node_id_fold"] != node["node_id_fold"]:
        raise ValueError(f"via endpoint association mismatch for {node['node_id']}")
    if attachment["sha256"] != surface["artwork_asset_sha256"]:
        raise ValueError(f"surface/attachment association mismatch for {source_path.name}")

    x = float(node["x_pm"]) / 1e6
    y = float(node["y_pm"]) / 1e6
    center = Point(x, y)
    if pad_shape["shape_kind"] != "CIRCLE" or pad_shape["width_pm"] != pad_shape["height_pm"]:
        raise ValueError(f"selected pad is not a circular pad for {node['node_id']}")
    if int(pad_shape["width_pm"]) != 60_000_000:
        raise ValueError(f"selected pad diameter is not 60 um for {node['node_id']}")
    radius_um = float(pad_shape["width_pm"]) / 2e6
    order = payload["primitive_order"]
    applied_order: list[list[object]] = []
    center_state = False
    positive_witness_count = 0
    negative_disk_intersection_count = 0
    negative_center_geometry_seen = False
    invalid_geometry_count = 0
    min_boundary_distance = math.inf
    min_circle_clearance = math.inf
    circles_clear = True

    for raw_kind, raw_index in order:
        _check_runtime(started, max_runtime_s)
        kind = str(raw_kind)
        index = int(raw_index)
        applied_order.append([kind, index])
        if kind == "positive_polygon":
            geometry = Polygon(payload["positive_polygons_um"][index])
            if not geometry.is_valid:
                invalid_geometry_count += 1
                raise ValueError(f"invalid positive polygon at primitive_order index {index}")
            boundary_distance = float(geometry.boundary.distance(center))
            min_boundary_distance = min(min_boundary_distance, boundary_distance)
            covers_center = bool(geometry.covers(center))
            center_state = center_state or covers_center
            if covers_center and boundary_distance >= radius_um:
                positive_witness_count += 1
        elif kind == "positive_circle":
            circle_x, circle_y, circle_radius = payload["positive_circles_um"][index]
            center_distance = math.hypot(x - circle_x, y - circle_y)
            boundary_distance = abs(center_distance - circle_radius)
            min_boundary_distance = min(min_boundary_distance, boundary_distance)
            covers_center = center_distance <= circle_radius
            center_state = center_state or covers_center
            if center_distance + radius_um <= circle_radius:
                positive_witness_count += 1
        elif kind == "negative_polygon":
            geometry = Polygon(payload["negative_polygons_um"][index])
            if not geometry.is_valid:
                invalid_geometry_count += 1
                raise ValueError(f"invalid negative polygon at primitive_order index {index}")
            boundary_distance = float(geometry.boundary.distance(center))
            min_boundary_distance = min(min_boundary_distance, boundary_distance)
            covers_center = bool(geometry.covers(center))
            negative_center_geometry_seen = negative_center_geometry_seen or covers_center
            if covers_center:
                center_state = False
            if covers_center or boundary_distance < radius_um:
                negative_disk_intersection_count += 1
        elif kind == "negative_circle":
            circle_x, circle_y, circle_radius = payload["negative_circles_um"][index]
            center_distance = math.hypot(x - circle_x, y - circle_y)
            boundary_distance = abs(center_distance - circle_radius)
            clearance = center_distance - circle_radius - radius_um
            min_boundary_distance = min(min_boundary_distance, boundary_distance)
            min_circle_clearance = min(min_circle_clearance, clearance)
            circles_clear = circles_clear and clearance >= 0.0
            negative_center_geometry_seen = (
                negative_center_geometry_seen or center_distance <= circle_radius
            )
            if center_distance <= circle_radius:
                center_state = False
            if clearance < 0.0:
                negative_disk_intersection_count += 1
        else:
            raise ValueError(f"unsupported primitive kind {kind!r}")

    if applied_order != order:
        raise ValueError(f"primitive_order replay mismatch for {source_path.name}")
    if math.isinf(min_boundary_distance):
        raise ValueError(f"selected source member has no boundary geometry: {source_path.name}")
    center_to_boundary = min_boundary_distance
    disk_margin = center_to_boundary - radius_um
    center_inside = bool(center_state and not negative_center_geometry_seen)
    disk_inside = bool(
        center_inside
        and positive_witness_count >= 1
        and negative_disk_intersection_count == 0
        and circles_clear
        and disk_margin >= 0.0
    )
    same_net = node["net_fold"] == via["net_fold"] == surface["net_fold"]
    same_layer = node["layer_id_fold"] == surface["layer_id_fold"] == pad_shape["layer_id_fold"]
    manifest_match = len(compressed) == int(attachment["size"]) and compressed_sha256 == attachment["sha256"]
    accepted = bool(
        same_net
        and same_layer
        and manifest_match
        and invalid_geometry_count == 0
        and center_inside
        and disk_inside
    )
    return {
        "node_id": node["node_id"],
        "via_id": via["via_id"],
        "padstack_id": pad_shape["padstack_id"],
        "layer_id": surface["layer_id"],
        "net_name": surface["net_name"],
        "source_identity": {
            "node_source_record_sha256": node["source_record_sha256"],
            "via_source_record_sha256": via["source_record_sha256"],
            "pad_shape_source_record_sha256": pad_shape["source_record_sha256"],
            "surface_source_record_sha256": surface["source_record_sha256"],
            "artwork_asset_sha256": surface["artwork_asset_sha256"],
            "island_manifest_sha256": surface["island_manifest_sha256"],
        },
        "center_um": {"x": x, "y": y},
        "coordinate_unit": "um",
        "pad_shape": {
            "shape_kind": pad_shape["shape_kind"],
            "diameter_um": float(pad_shape["width_pm"]) / 1e6,
            "radius_um": radius_um,
        },
        "compressed_member": {
            "path": source_path.name,
            "manifest_name": attachment["name"],
            "size_bytes": len(compressed),
            "sha256": compressed_sha256,
            "manifest_size_bytes": attachment["size"],
            "manifest_sha256": attachment["sha256"],
            "manifest_match": manifest_match,
        },
        "decoded": {
            "size_bytes": len(decoded),
            "sha256": decoded_sha256,
            "format": payload.get("format"),
            "layer": payload.get("layer"),
            "net": payload.get("net"),
            "coordinate_unit": "um",
        },
        "source_polarity": {
            "positive_polygon_count": len(payload["positive_polygons_um"]),
            "negative_polygon_count": len(payload["negative_polygons_um"]),
            "negative_circle_count": len(payload["negative_circles_um"]),
            "invalid_input_geometry_count": invalid_geometry_count,
            "negative_polarity_applied_in_source_order": True,
            "positive_union_not_claimed": True,
            "positive_witness_count": positive_witness_count,
            "negative_disk_intersection_count": negative_disk_intersection_count,
            "negative_center_geometry_seen": negative_center_geometry_seen,
            "negative_circle_count_clear_of_pad_disk": (
                len(payload["negative_circles_um"]) if circles_clear else None
            ),
            "minimum_negative_circle_disk_clearance_um": min_circle_clearance,
        },
        "primitive_order": order,
        "primitive_order_count": len(order),
        "primitive_order_sha256": _order_sha256(order),
        "primitive_order_applied_exactly": True,
        "contact_measurement": {
            "same_net_identity": same_net,
            "same_layer_identity": same_layer,
            "same_net_copper_contains_center": center_inside,
            "entire_pad_disk_inside_same_net_copper": disk_inside,
            "center_to_boundary_um": center_to_boundary,
            "disk_margin_um": disk_margin,
        },
        "accepted_exact_local_contact": accepted,
    }


def _hole_order_check() -> dict[str, bool]:
    hole = Polygon(
        [(0, 0), (4, 0), (4, 4), (0, 4)],
        holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]],
    )
    return {
        "hole_center_rejected": not hole.covers(Point(2, 2)),
        "outer_copper_accepted": hole.covers(Point(0.5, 0.5)),
        "passed": bool(not hole.covers(Point(2, 2)) and hole.covers(Point(0.5, 0.5))),
    }


def build_result(input_dir: Path, max_runtime_s: float) -> dict[str, object]:
    started = time.monotonic()
    manifest_path = input_dir / "inputs.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    contacts = manifest.get("contacts")
    if not isinstance(contacts, list) or not contacts or len(contacts) > MAX_ENDPOINTS:
        raise ValueError(f"expected one to {MAX_ENDPOINTS} local contacts")
    results = [_query_contact(contact, started, max_runtime_s) for contact in contacts]
    elapsed = time.monotonic() - started
    if elapsed >= max_runtime_s or elapsed >= 60.0:
        raise TimeoutError("ground contact query exceeded its wall-time limit")
    all_ok = all(bool(contact["accepted_exact_local_contact"]) for contact in results)
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_LOCAL_SOURCE_COPPER_CONTACT_ONLY" if all_ok else "STOP",
        "scope": "SOURCE_COPPER_CONTACT_ONLY",
        "inputs_manifest": {
            "path": str(manifest_path),
            "size_bytes": len(manifest_bytes),
            "sha256": _sha256(manifest_bytes),
            "status": manifest.get("status"),
        },
        "contacts": results,
        "negative_hole_order_check": _hole_order_check(),
        "nonclaims": [
            "This is a sufficient single-positive-primitive witness; complete positive-union coverage is not claimed.",
            "No via plating or completed via path claim.",
            "No complete L29 return-path or current-sharing claim.",
            "No solver, mesher, condensation, product-adapter, or production-acceptance claim.",
        ],
        "resources": {
            "elapsed_s": elapsed,
            "endpoint_count": len(results),
            "bounded_under_60s": elapsed < 60.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} ground contact query")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-runtime-s", type=float, default=55.0)
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - source copper contact membership")
    if not math.isfinite(float(args.max_runtime_s)) or not 1.0 <= args.max_runtime_s <= MAX_RUNTIME_S:
        print(f"{PROGRAM} v{VERSION}: ERROR: max runtime must be in [1, {MAX_RUNTIME_S:g}]", file=sys.stderr)
        return 2
    output = args.output.resolve()
    if output.exists():
        print(f"{PROGRAM} v{VERSION}: ERROR: refusing to overwrite existing output: {output}", file=sys.stderr)
        return 2
    if not args.input_dir.is_dir():
        print(f"{PROGRAM} v{VERSION}: ERROR: input directory does not exist: {args.input_dir}", file=sys.stderr)
        return 2
    try:
        result = build_result(args.input_dir, args.max_runtime_s)
        serialized = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")
    except (OSError, TimeoutError, ValueError, zlib.error) as exc:
        print(f"{PROGRAM} v{VERSION}: ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "output": str(output)}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
