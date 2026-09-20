#!/usr/bin/env python3
"""Map the proven L14 finite-link endpoint centers to source artwork islands."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import time
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shapely.geometry import Point

import query_astra_loaded_sheet_candidate as source_audit
from spd_decap_pi._core import services as core_services


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
EXPECTED_RECEIPT_SHA256 = (
    "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d"
)
EXPECTED_SOURCE_AUDIT_SHA256 = (
    "60685a3fb1d69390c6c853f4dc3088a38701358e59f83b416882f551b924e662"
)
DEFAULT_RECEIPT = (
    ROOT
    / "outputs/research/astra-step6e-loaded-boundary-01"
    / "loaded-sheet-candidate-final.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "outputs/research/astra-step6e-loaded-boundary-01"
    / "l14-endpoint-polygon-census.json"
)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _check_time(started: float, maximum_s: float) -> None:
    if time.monotonic() - started >= maximum_s:
        raise TimeoutError("L14 endpoint polygon census exceeded its runtime bound")


def build_result(receipt_path: Path, maximum_s: float) -> dict[str, Any]:
    started = time.monotonic()
    receipt_bytes = receipt_path.read_bytes()
    if sha256(receipt_bytes).hexdigest() != EXPECTED_RECEIPT_SHA256:
        raise ValueError("Step6E source-boundary receipt differs from its pin")
    if _file_sha256(Path(source_audit.__file__).resolve()) != EXPECTED_SOURCE_AUDIT_SHA256:
        raise ValueError("Step6E source-boundary query differs from its pin")
    receipt = json.loads(receipt_bytes)
    candidate = receipt["candidate_component"]
    if (
        receipt.get("program") != PROGRAM
        or receipt.get("version") != VERSION
        or receipt.get("rail_id") != source_audit.RAIL
        or candidate.get("component_id") != source_audit.PWR_COMPONENT
        or candidate["geometry"]["compressed_sha256"]
        != source_audit.EXPECTED_PWR_ASSET_SHA256
        or candidate.get("common_plane_quotient_vertex_ordinal") != 744683
    ):
        raise ValueError("Step6E candidate identity differs from the declared L14 source input")

    bundle = Path(receipt["inputs"]["bundle_path"])
    if bundle.stat().st_size != int(receipt["inputs"]["bundle_size_bytes"]):
        raise ValueError("D115b bundle size differs from the source-boundary receipt")

    compiled = source_audit._open_db(source_audit.COMPILED_DB)
    raw = source_audit._open_db(source_audit.RAW_DB)
    try:
        surface = json.loads(
            compiled.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0]
        )
        component_rows = [
            row
            for row in surface["surface_equivalence_components"]
            if row["component_id"] == source_audit.PWR_COMPONENT
        ]
        if len(component_rows) != 1:
            raise ValueError("the pinned L14 surface-equivalence component is ambiguous")
        component_row = component_rows[0]
        expected_island_ids = set(component_row["island_ids"])
        if len(expected_island_ids) != 110:
            raise ValueError("the pinned L14 component no longer contains 110 islands")

        asset_rows = [
            row
            for row in surface["geometry_assets"]
            if row["asset_sha256"] == source_audit.EXPECTED_PWR_ASSET_SHA256
            and row["layer"] == source_audit.PWR_LAYER
            and row["net"].casefold() == source_audit.RAIL.casefold()
        ]
        if len(asset_rows) != 1:
            raise ValueError("the pinned L14 geometry asset is ambiguous")
        asset_row = asset_rows[0]

        plane_vertex = int(candidate["common_plane_quotient_vertex_ordinal"])
        finite_links = list(
            compiled.execute(
                "SELECT ordinal,link_id,first_node,second_node,parallel_count,resistance_ohm,inductance_h "
                "FROM links WHERE kind=1 AND (first_node=? OR second_node=?) ORDER BY ordinal",
                (plane_vertex, plane_vertex),
            )
        )
        if len(finite_links) != 2110:
            raise ValueError("the L14 finite boundary no longer contains 2,110 native links")
        link_ordinals = {int(row[0]) for row in finite_links}
        owners_by_link: dict[int, list[str]] = defaultdict(list)
        for ordinal, owner in compiled.execute(
            "SELECT link_ordinal,owner_id FROM link_owners WHERE kind=1 ORDER BY link_ordinal,owner_id"
        ):
            if int(ordinal) in link_ordinals:
                owners_by_link[int(ordinal)].append(str(owner))
        if set(owners_by_link) != link_ordinals:
            raise ValueError("one or more L14 boundary links have no persisted owner")

        via_to_link: dict[str, tuple[Any, ...]] = {}
        for link in finite_links:
            via_owners = [
                owner.split(":", 1)[1]
                for owner in owners_by_link[int(link[0])]
                if owner.casefold().startswith("via:")
            ]
            if len(via_owners) != 1:
                raise ValueError("an L14 boundary link does not have exactly one source Via owner")
            key = via_owners[0].casefold()
            if key in via_to_link:
                raise ValueError("a source Via owns more than one L14 boundary link")
            via_to_link[key] = link

        placeholders = ",".join("?" for _ in via_to_link)
        raw_rows = list(
            raw.execute(
                "SELECT via_id,net_name,start_layer_id,end_layer_id,start_node_id,end_node_id,"
                "padstack_id,owner_id,start_x_pm,start_y_pm,end_x_pm,end_y_pm,source_record_sha256 "
                f"FROM vias WHERE via_id_fold IN ({placeholders})",
                tuple(sorted(via_to_link)),
            )
        )
        if len(raw_rows) != 2110 or {str(row[0]).casefold() for row in raw_rows} != set(via_to_link):
            raise ValueError("native L14 boundary does not join one-to-one to source Via rows")
    finally:
        raw.close()
        compiled.close()

    _check_time(started, maximum_s)
    with ZipFile(bundle, "r") as archive:
        member = "attachments/" + asset_row["asset"]
        compressed = archive.read(member)
    if sha256(compressed).hexdigest() != source_audit.EXPECTED_PWR_ASSET_SHA256:
        raise ValueError("L14 geometry bytes differ from the pinned source asset")
    payload = core_services._decode_spd_geometry_asset(
        source_audit.EXPECTED_PWR_ASSET_SHA256,
        compressed,
    )
    shape = core_services._ordered_spd_geometry(payload)
    if shape is None or shape.is_empty or not shape.is_valid:
        raise ValueError("L14 ordered source geometry is invalid")
    original_parts = (shape,) if shape.geom_type == "Polygon" else tuple(shape.geoms)
    if len(original_parts) != 110:
        raise ValueError("L14 ordered source geometry no longer has 110 polygons")
    original_index_by_wkb = {
        sha256(bytes(part.normalize().wkb)).hexdigest(): index
        for index, part in enumerate(original_parts)
    }
    islands = core_services._spd_surface_islands(
        layer=source_audit.PWR_LAYER,
        net=source_audit.RAIL,
        asset_sha256=source_audit.EXPECTED_PWR_ASSET_SHA256,
        shape=shape,
    )
    if {island_id for island_id, _ in islands} != expected_island_ids:
        raise ValueError("source-derived polygon IDs differ from the compiled component")

    polygon_rows: list[dict[str, Any]] = []
    polygons: list[Any] = []
    for sorted_index, (island_id, polygon) in enumerate(islands):
        polygon_sha = sha256(bytes(polygon.normalize().wkb)).hexdigest()
        polygons.append(polygon)
        polygon_rows.append(
            {
                "deterministic_polygon_index": sorted_index,
                "ordered_geometry_component_index": original_index_by_wkb[polygon_sha],
                "island_id": island_id,
                "normalized_polygon_wkb_sha256": polygon_sha,
                "area_um2": float(polygon.area),
                "hole_count": len(polygon.interiors),
                "bounds_um": [float(value) for value in polygon.bounds],
            }
        )

    bounds = [polygon.bounds for polygon in polygons]
    endpoint_rows: list[dict[str, Any]] = []
    classifications: Counter[str] = Counter()
    matched_islands: Counter[str] = Counter()
    raw_rows.sort(key=lambda row: str(row[0]).casefold())
    for row in raw_rows:
        _check_time(started, maximum_s)
        (
            via_id,
            net_name,
            start_layer,
            end_layer,
            start_node,
            end_node,
            padstack,
            raw_owner,
            start_x_pm,
            start_y_pm,
            end_x_pm,
            end_y_pm,
            source_record_sha256,
        ) = row
        if start_layer == source_audit.PWR_LAYER:
            endpoint_node = start_node
            x_um = float(start_x_pm) / 1.0e6
            y_um = float(start_y_pm) / 1.0e6
        elif end_layer == source_audit.PWR_LAYER:
            endpoint_node = end_node
            x_um = float(end_x_pm) / 1.0e6
            y_um = float(end_y_pm) / 1.0e6
        else:
            raise ValueError("an alleged L14 boundary Via has no L14 endpoint")
        point = Point(x_um, y_um)
        matches: list[tuple[int, str]] = []
        for index, polygon in enumerate(polygons):
            min_x, min_y, max_x, max_y = bounds[index]
            if min_x <= x_um <= max_x and min_y <= y_um <= max_y and polygon.covers(point):
                relation = "boundary" if polygon.boundary.covers(point) else "interior"
                matches.append((index, relation))
        if not matches:
            classification = "outside"
        elif len(matches) > 1:
            classification = "multiple"
        else:
            classification = matches[0][1]
            matched_islands[polygon_rows[matches[0][0]]["island_id"]] += 1
        classifications[classification] += 1

        link = via_to_link[str(via_id).casefold()]
        endpoint_rows.append(
            {
                "via_id": via_id,
                "source_record_sha256": source_record_sha256,
                "net_name": net_name,
                "start_layer": start_layer,
                "end_layer": end_layer,
                "padstack_id": padstack,
                "raw_owner_id": raw_owner,
                "l14_endpoint_node_id": endpoint_node,
                "l14_center_um": [x_um, y_um],
                "center_classification": classification,
                "polygon_matches": [
                    {
                        "deterministic_polygon_index": index,
                        "island_id": polygon_rows[index]["island_id"],
                        "relation": relation,
                    }
                    for index, relation in matches
                ],
                "native_link": {
                    "ordinal": int(link[0]),
                    "link_id": link[1],
                    "first_global_node": int(link[2]),
                    "second_global_node": int(link[3]),
                    "parallel_count": int(link[4]),
                    "resistance_ohm": float(link[5]),
                    "inductance_h": float(link[6]),
                    "owner_ids": owners_by_link[int(link[0])],
                },
            }
        )

    if sum(classifications.values()) != 2110:
        raise ValueError("endpoint classification count is incomplete")
    elapsed = time.monotonic() - started
    _check_time(started, maximum_s)
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_SOURCE_CENTER_TO_L14_POLYGON_CENSUS",
        "scope": (
            "Source-only L14 Via endpoint-center membership in ordered artwork polygons. "
            "Center membership does not certify finite electrode area, physical current "
            "injection, current sharing, or a distributed replacement. The compiled "
            "surface-equivalence component also contains same-layer Trace connectivity."
        ),
        "inputs": {
            "source_boundary_receipt": str(receipt_path),
            "source_boundary_receipt_sha256": EXPECTED_RECEIPT_SHA256,
            "source_boundary_query_sha256": EXPECTED_SOURCE_AUDIT_SHA256,
            "bundle_path": str(bundle),
            "bundle_size_bytes": bundle.stat().st_size,
            "source_sha256": receipt["inputs"]["source_sha256"],
            "scenario_sha256": receipt["inputs"]["scenario_sha256"],
            "geometry_member": asset_row["asset"],
            "geometry_compressed_sha256": source_audit.EXPECTED_PWR_ASSET_SHA256,
            "script_sha256": _file_sha256(Path(__file__).resolve()),
        },
        "component": {
            "component_id": source_audit.PWR_COMPONENT,
            "layer": source_audit.PWR_LAYER,
            "net": source_audit.RAIL,
            "common_plane_quotient_vertex_ordinal": 744683,
            "polygon_count": len(polygon_rows),
            "component_island_ids_equal_source_derived_polygon_ids": True,
            "total_area_um2": float(sum(row["area_um2"] for row in polygon_rows)),
            "total_hole_count": int(sum(row["hole_count"] for row in polygon_rows)),
            "polygons": polygon_rows,
        },
        "boundary": {
            "source_via_count": len(endpoint_rows),
            "native_link_count": len(finite_links),
            "center_classification_counts": dict(sorted(classifications.items())),
            "matched_island_count": len(matched_islands),
            "matched_island_center_counts": dict(sorted(matched_islands.items())),
            "records_sha256": _canonical_sha256(endpoint_rows),
            "endpoints": endpoint_rows,
        },
        "resources": {
            "elapsed_s": elapsed,
            "maximum_runtime_s": maximum_s,
            "bounded": True,
        },
        "limitations": [
            "A Via center inside one polygon does not prove its pad disk or annulus is fully covered.",
            "Artwork polygons alone do not capture same-layer Trace connections used by the compiled surface-equivalence class.",
            "No native solve, raw SPD scan, import/compiler run, field-current ranking, PowerSI fit, or product edit was performed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-runtime-s", type=float, default=55.0)
    args = parser.parse_args()
    args.receipt = args.receipt.resolve()
    args.output = args.output.resolve()
    if not args.receipt.is_file():
        parser.error("--receipt must be an existing file")
    if args.output.exists():
        parser.error("--output already exists")
    if args.max_runtime_s <= 0.0 or args.max_runtime_s > 55.0:
        parser.error("--max-runtime-s must be in (0,55]")
    result = build_result(args.receipt, args.max_runtime_s)
    _write_json(args.output, result)
    print(
        f"{PROGRAM} v{VERSION} {result['status']} "
        f"elapsed={result['resources']['elapsed_s']:.3f}s output={args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
