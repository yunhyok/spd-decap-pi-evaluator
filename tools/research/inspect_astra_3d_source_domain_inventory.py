"""SPD Decap PI Evaluator v0.23.1 cached 3-D source-domain inventory.

The helper reads pinned JSON and immutable SQLite caches. It records source
stackup z intervals and representative plane, trace, pad, and via rows. It
does not decode geometry assets, build conductor unions or meshes, read a raw
SPD/scenario, or solve a field problem.
"""
from __future__ import annotations

import argparse
from hashlib import file_digest
import json
import math
from pathlib import Path
import sqlite3
from time import monotonic


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
SOURCE = RESEARCH / "astra-native-frequency-stamps-01/source-frequency-inputs.json"
RAW_DB = RESEARCH / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
COMPILED_DB = RESEARCH / "astra-step4-basis-01/indexes/compiled-topology.sqlite"
GC_RESULT = RESEARCH / "astra-reference-gc-source-coverage-01/result.json"
GC_REVIEW = RESEARCH / "astra-reference-gc-source-coverage-review-01/independent-review.json"

PINS = {
    "source_frequency_inputs": (SOURCE, "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc"),
    "gc_source_coverage_result": (GC_RESULT, "4545b8bcbb81b24553cf4ca596a429852a0b678114fbdd3bb22a3135e0c1f594"),
    "gc_source_coverage_review": (GC_REVIEW, "6485f38782e022bf422fc2c9cfd546fc3153b0911b2db6761cef4d294df6be54"),
}
DB_IDENTITIES = {
    "raw_spatial": {
        "path": RAW_DB,
        "sha256_from_accepted_review": "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7",
        "size_bytes": 2_752_458_752,
    },
    "compiled_topology": {
        "path": COMPILED_DB,
        "sha256_from_accepted_review": "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b",
        "size_bytes": 461_893_632,
    },
}
RAW_SCHEMAS = {
    "meta": ("key", "value"),
    "source_coverage": ("ordinal", "source_basename", "source_size_bytes", "source_sha256", "raw_header_count", "logical_record_count"),
    "section_ledger": ("section_name", "row_count", "logical_sha256"),
    "stackup_layers": ("layer_ordinal", "layer_name", "layer_kind", "thickness_um", "conductivity_s_per_m", "material_name"),
    "dielectric_points": ("layer_ordinal", "point_ordinal", "frequency_hz", "epsilon_r", "loss_tangent"),
    "nodes": ("ordinal", "node_id", "node_id_fold", "net_name", "net_fold", "net_status", "layer_id", "layer_id_fold", "x_pm", "y_pm", "padstack_id", "padstack_id_fold", "rotation_microdegrees", "source_record_sha256"),
    "traces": ("ordinal", "trace_id", "trace_id_fold", "net_name", "net_fold", "layer_id", "layer_id_fold", "start_node_id", "start_node_id_fold", "end_node_id", "end_node_id_fold", "width_pm", "width_status", "width_location", "geometry_status", "owner_id", "owner_id_fold", "source_record_sha256", "min_x_pm", "min_y_pm", "max_x_pm", "max_y_pm"),
    "vias": ("ordinal", "via_id", "via_id_fold", "net_name", "net_fold", "start_layer_id", "start_layer_id_fold", "end_layer_id", "end_layer_id_fold", "start_node_id", "start_node_id_fold", "end_node_id", "end_node_id_fold", "padstack_id", "padstack_id_fold", "status", "owner_id", "owner_id_fold", "start_x_pm", "start_y_pm", "end_x_pm", "end_y_pm", "rotation_microdegrees", "source_record_sha256"),
    "padstacks": ("ordinal", "padstack_id", "padstack_id_fold", "drill_diameter_pm", "material", "source_record_sha256"),
    "pad_shapes": ("ordinal", "padstack_id", "padstack_id_fold", "layer_id", "layer_id_fold", "shape_kind", "width_pm", "height_pm", "source_record_sha256"),
    "surfaces": ("ordinal", "surface_id", "surface_id_fold", "net_name", "net_fold", "layer_id", "layer_id_fold", "artwork_asset_sha256", "island_manifest_sha256", "source_record_sha256", "min_x_pm", "min_y_pm", "max_x_pm", "max_y_pm"),
    "plane_primitives": ("primitive_ordinal", "layer_ordinal", "layer_name", "net_name", "polarity", "kind", "source_asset_name", "source_asset_sha256", "coordinate_unit", "primitive_sha256"),
    "plane_vertices": ("primitive_ordinal", "vertex_ordinal", "x_um", "y_um"),
    "plane_circles": ("primitive_ordinal", "center_x_um", "center_y_um", "radius_um"),
}
COMPILED_SCHEMAS = {
    "meta": ("key", "value"),
    "views": ("name", "payload", "payload_size", "payload_sha256"),
}

SOURCE_NET = "ADC_VDD_075_VTRIP_SRAM/0"
TRACE_CONTROL = {"ordinal": 816_605, "id": "Trace463597", "sha256": "1306cbec22b0a3d576ba782859eefec73477fc9f1b52ebce5a2688be93f6034a"}
VIA_CONTROL = {"ordinal": 1_255_909, "id": "Via501139", "sha256": "b7b9d7583d9c80db2374f1ef657781a3947cd9807dc920e41daec2f2e1cfa24b"}
SURFACE_CONTROL = {"ordinal": 154, "sha256": "82996d86eef7125b7e86f7efe3089033d0a720826244cc221ca5990a0d817fa2"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def open_database(path: Path, deadline: float) -> sqlite3.Connection:
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    db.set_progress_handler(lambda: int(monotonic() > deadline), 10_000)
    return db


def query(db: sqlite3.Connection, deadline: float, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    require(monotonic() <= deadline, "SQLite deadline expired before query")
    try:
        rows = list(db.execute(sql, params))
    except sqlite3.OperationalError as exc:
        if monotonic() > deadline or "interrupted" in str(exc).lower():
            raise TimeoutError("SQLite metadata deadline exceeded") from exc
        raise
    require(monotonic() <= deadline, "SQLite metadata deadline exceeded")
    return rows


def one(db: sqlite3.Connection, deadline: float, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row:
    rows = query(db, deadline, sql, params)
    require(len(rows) == 1, "query did not return exactly one row")
    return rows[0]


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def schemas(db: sqlite3.Connection, deadline: float, expected: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    names = {str(row[0]) for row in query(db, deadline, "SELECT name FROM sqlite_master WHERE type='table'")}
    require(set(expected) <= names, "required SQLite table is absent")
    result: dict[str, list[str]] = {}
    for table, columns in expected.items():
        actual = tuple(str(row[1]) for row in query(db, deadline, f"PRAGMA table_info({table!r})"))
        require(actual == columns, f"{table} schema changed")
        result[table] = list(actual)
    return result


def stackup_rows(source: dict[str, object], raw_rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    source_rows = source["stackup_layers"]
    require(isinstance(source_rows, list) and len(source_rows) == len(raw_rows) == 95, "stackup row count")
    z_um = 0.0
    conductors: list[dict[str, object]] = []
    dielectrics: list[dict[str, object]] = []
    for ordinal, (src, raw) in enumerate(zip(source_rows, raw_rows, strict=True)):
        require(raw["layer_ordinal"] == ordinal and src["name"] == raw["layer_name"], "stackup order/name mismatch")
        require(src["material"] == raw["material_name"] and src["thickness_um"] == raw["thickness_um"], "stackup material/thickness mismatch")
        require(src["conductivity_s_m"] == raw["conductivity_s_per_m"], "stackup conductivity mismatch")
        kind = "conductor" if src["conductivity_s_m"] is not None else "dielectric"
        require(raw["layer_kind"] == kind, "stackup kind mismatch")
        thickness = float(src["thickness_um"])
        require(math.isfinite(thickness) and thickness > 0.0, "nonpositive stackup thickness")
        compact = {
            "ordinal": ordinal,
            "name": src["name"],
            "kind": kind,
            "material": src["material"],
            "z_top_um": z_um,
            "z_bottom_um": z_um + thickness,
            "thickness_um": thickness,
            "conductivity_s_m": src["conductivity_s_m"],
            "nominal_dk": src["dk"],
            "nominal_df": src["df"],
            "frequency_property_count": len(src["dielectric_properties"]),
            "pwr_nets": list(src["pwr_nets"]),
        }
        (conductors if kind == "conductor" else dielectrics).append(compact)
        z_um += thickness
    require(len(conductors) == 48 and len(dielectrics) == 47 and z_um == 2922.0, "source stackup dimensions")
    return conductors, dielectrics


def collect(max_seconds: float) -> dict[str, object]:
    started = monotonic()
    deadline = started + max_seconds
    for label, (path, expected) in PINS.items():
        require(sha256(path) == expected, f"{label} hash changed")
    gc_result = json.loads(GC_RESULT.read_text(encoding="utf-8"))
    gc_review = json.loads(GC_REVIEW.read_text(encoding="utf-8"))
    require(gc_result["status"] == "PASS_SAVED_ADJACENT_GC_SOURCE_COVERAGE_AUDIT", "G/C audit status")
    require(gc_review["status"] == "ACCEPT_SAVED_ADJACENT_GC_SOURCE_COVERAGE_INDEPENDENT_REVIEW" and gc_review["findings"] == [], "G/C review status")
    for key, db_identity in DB_IDENTITIES.items():
        require(db_identity["path"].stat().st_size == db_identity["size_bytes"], f"{key} size changed")
        producer_key = "raw" if key == "raw_spatial" else "compiled"
        require(gc_result["inputs"][producer_key]["sha256"] == db_identity["sha256_from_accepted_review"], f"{key} producer pin")
        require(gc_review["inputs"][producer_key]["sha256"] == db_identity["sha256_from_accepted_review"], f"{key} review pin")

    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    with open_database(RAW_DB, deadline) as raw:
        raw_schema = schemas(raw, deadline, RAW_SCHEMAS)
        raw_meta = {str(row["key"]): str(row["value"]) for row in query(raw, deadline, "SELECT key,value FROM meta ORDER BY key")}
        required_meta = {
            "payload_schema": "spd-raw-spatial-contact-sqlite-v3",
            "compiler_id": "raw-spd-finite-via-spatial-contact-v3",
            "source_sha256": "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2",
            "project_binding_sha256": "a20f131070142ecb4c332d9a8339454c13021eb6787c51944cc628f19449616e",
            "geometry_identity_sha256": "1593dcbe3e59c1eac48173a348a9b893862e99bcd3e66b14e69057197660cb4e",
            "layers_count": "95",
            "plane_sheet_dielectric_points_count": "301",
            "surfaces_count": "371",
            "traces_count": "1412611",
            "vias_count": "1810200",
        }
        require(all(raw_meta.get(key) == value for key, value in required_meta.items()), "raw cache identity metadata changed")
        source_coverage = row_dict(one(raw, deadline, "SELECT * FROM source_coverage ORDER BY ordinal"))
        require(source_coverage["source_sha256"] == raw_meta["source_sha256"] and source_coverage["logical_record_count"] == source_coverage["raw_header_count"] == 5_974_148, "source coverage identity")
        section_ledger = [row_dict(row) for row in query(raw, deadline, "SELECT * FROM section_ledger ORDER BY section_name")]
        raw_stack = [row_dict(row) for row in query(raw, deadline, "SELECT * FROM stackup_layers ORDER BY layer_ordinal")]
        conductors, dielectrics = stackup_rows(source, raw_stack)
        dielectric_point_count = int(one(raw, deadline, "SELECT count(*) AS n FROM dielectric_points")["n"])
        require(dielectric_point_count == sum(row["frequency_property_count"] for row in dielectrics) == 301, "dielectric point count")

        expected_counts = {"nodes": 2_751_337, "traces": 1_412_611, "vias": 1_810_200, "padstacks": 98, "pad_shapes": 186, "surfaces": 371, "plane_primitives": 339_162, "plane_vertices": 11_968_968, "plane_circles": 57_279}
        row_counts = {table: int(one(raw, deadline, f"SELECT count(*) AS n FROM {table}")["n"]) for table in expected_counts}
        require(row_counts == expected_counts, "raw geometry row counts")

        trace = row_dict(one(raw, deadline, "SELECT * FROM traces WHERE ordinal=?", (TRACE_CONTROL["ordinal"],)))
        require(trace["trace_id"] == TRACE_CONTROL["id"] and trace["source_record_sha256"] == TRACE_CONTROL["sha256"], "trace control identity")
        require(trace["net_name"] == SOURCE_NET and trace["layer_id"] == "Signal$TOP" and trace["width_status"] == trace["geometry_status"] == "EXACT", "trace control status")
        trace_nodes = [row_dict(row) for row in query(raw, deadline, "SELECT * FROM nodes WHERE node_id_fold IN (?,?) ORDER BY node_id_fold", (trace["start_node_id_fold"], trace["end_node_id_fold"]))]
        require(len(trace_nodes) == 2 and {row["node_id_fold"] for row in trace_nodes} == {trace["start_node_id_fold"], trace["end_node_id_fold"]}, "trace endpoint nodes")
        require(all(row["net_name"] == SOURCE_NET and row["layer_id"] == trace["layer_id"] for row in trace_nodes), "trace endpoint ownership")
        node_by_id = {row["node_id_fold"]: row for row in trace_nodes}
        start_node, end_node = node_by_id[trace["start_node_id_fold"]], node_by_id[trace["end_node_id_fold"]]
        trace_length_um = math.hypot(float(end_node["x_pm"] - start_node["x_pm"]), float(end_node["y_pm"] - start_node["y_pm"])) / 1e6
        require(trace["width_pm"] == 45_000_000 and trace_length_um == 130.0, "trace dimensions")
        require((trace["min_x_pm"], trace["min_y_pm"], trace["max_x_pm"], trace["max_y_pm"]) == (min(start_node["x_pm"], end_node["x_pm"]), min(start_node["y_pm"], end_node["y_pm"]), max(start_node["x_pm"], end_node["x_pm"]), max(start_node["y_pm"], end_node["y_pm"])), "trace centerline bounds")

        via = row_dict(one(raw, deadline, "SELECT * FROM vias WHERE ordinal=?", (VIA_CONTROL["ordinal"],)))
        require(via["via_id"] == VIA_CONTROL["id"] and via["source_record_sha256"] == VIA_CONTROL["sha256"], "via control identity")
        require(via["net_name"] == SOURCE_NET and via["status"] == "EXACT" and via["start_x_pm"] == via["end_x_pm"] and via["start_y_pm"] == via["end_y_pm"], "via control status")
        via_nodes = [row_dict(row) for row in query(raw, deadline, "SELECT * FROM nodes WHERE node_id_fold IN (?,?) ORDER BY node_id_fold", (via["start_node_id_fold"], via["end_node_id_fold"]))]
        require(len(via_nodes) == 2 and {row["layer_id"] for row in via_nodes} == {via["start_layer_id"], via["end_layer_id"]}, "via endpoint nodes")
        padstack = row_dict(one(raw, deadline, "SELECT * FROM padstacks WHERE padstack_id_fold=?", (via["padstack_id_fold"],)))
        pad_shapes = [row_dict(row) for row in query(raw, deadline, "SELECT * FROM pad_shapes WHERE padstack_id_fold=? ORDER BY ordinal", (via["padstack_id_fold"],))]
        require(padstack["padstack_id"] == "DR-1415_60" and padstack["drill_diameter_pm"] == 40_000_000 and padstack["material"] == "COPPER", "via drill control")
        require(len(pad_shapes) == 2 and {row["layer_id"] for row in pad_shapes} == {via["start_layer_id"], via["end_layer_id"]}, "via pad layers")
        require(all(row["shape_kind"] == "CIRCLE" and row["width_pm"] == row["height_pm"] == 60_000_000 for row in pad_shapes), "via pad shape")

        surface = row_dict(one(raw, deadline, "SELECT * FROM surfaces WHERE ordinal=?", (SURFACE_CONTROL["ordinal"],)))
        require(surface["source_record_sha256"] == SURFACE_CONTROL["sha256"] and surface["net_name"] == SOURCE_NET and surface["layer_id"] == "Signal$L14(MAIN_POWER4)", "surface control identity")
        primitive_summary = row_dict(one(raw, deadline, "SELECT count(*) AS primitive_count,min(primitive_ordinal) AS first_ordinal,max(primitive_ordinal) AS last_ordinal FROM plane_primitives WHERE source_asset_sha256=?", (surface["artwork_asset_sha256"],)))
        require(primitive_summary == {"primitive_count": 1842, "first_ordinal": 79858, "last_ordinal": 81699}, "surface primitive coverage")
        primitive = row_dict(one(raw, deadline, "SELECT * FROM plane_primitives WHERE primitive_ordinal=?", (primitive_summary["first_ordinal"],)))
        require(primitive["source_asset_sha256"] == surface["artwork_asset_sha256"] and primitive["layer_name"] == surface["layer_id"] and primitive["net_name"] == surface["net_name"] and primitive["kind"] == "polygon" and primitive["coordinate_unit"] == "um", "surface primitive identity")
        primitive_vertices = [row_dict(row) for row in query(raw, deadline, "SELECT * FROM plane_vertices WHERE primitive_ordinal=? ORDER BY vertex_ordinal", (primitive["primitive_ordinal"],))]
        require(len(primitive_vertices) >= 3 and [row["vertex_ordinal"] for row in primitive_vertices] == list(range(len(primitive_vertices))), "surface primitive vertices")
        surface_extent_pm = tuple(one(raw, deadline, "SELECT min(min_x_pm),min(min_y_pm),max(max_x_pm),max(max_y_pm) FROM surfaces"))
        require(surface_extent_pm == (-49_700_000_000, -49_700_000_000, 49_700_000_000, 49_700_000_000), "surface source envelope")
        raw_tables = sorted(str(row[0]) for row in query(raw, deadline, "SELECT name FROM sqlite_master WHERE type='table'"))

    with open_database(COMPILED_DB, deadline) as compiled:
        compiled_schema = schemas(compiled, deadline, COMPILED_SCHEMAS)
        compiled_meta = {str(row["key"]): str(row["value"]) for row in query(compiled, deadline, "SELECT key,value FROM meta ORDER BY key")}
        require(compiled_meta["payload_schema"] == "spd-layerwise-compiled-topology-sqlite-v1" and compiled_meta["source_sha256"] == source_coverage["source_sha256"] and compiled_meta["project_binding_sha256"] == raw_meta["project_binding_sha256"], "compiled cache identity")
        views = [row_dict(row) for row in query(compiled, deadline, "SELECT name,payload_size,payload_sha256 FROM views ORDER BY name")]
        require([row["name"] for row in views] == ["external", "scenario", "surface"], "compiled view names")
        compiled_tables = sorted(str(row[0]) for row in query(compiled, deadline, "SELECT name FROM sqlite_master WHERE type='table'"))

    layer_by_name = {row["name"]: row for row in conductors}
    start_layer, end_layer = layer_by_name[via["start_layer_id"]], layer_by_name[via["end_layer_id"]]
    via_center_span_um = abs((end_layer["z_top_um"] + end_layer["z_bottom_um"] - start_layer["z_top_um"] - start_layer["z_bottom_um"]) / 2)
    require(via_center_span_um == 50.0, "via foil-center span")
    explicit_outline_names = sorted(name for name in raw_tables + [row["name"] for row in views] if "outline" in name.casefold() or "boundary" in name.casefold())
    explicit_outline_meta = sorted(key for key in list(raw_meta) + list(compiled_meta) if "outline" in key.casefold() or "boundary" in key.casefold())
    require(explicit_outline_names == [] and explicit_outline_meta == [], "unexpected explicit outline evidence")

    elapsed = monotonic() - started
    require(elapsed <= max_seconds, "inventory time budget")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETE_CACHED_3D_SOURCE_DOMAIN_INVENTORY_WITH_EXPLICIT_GAPS",
        "driver_sha256": sha256(Path(__file__)),
        "inputs": {
            **{label: {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": expected} for label, (path, expected) in PINS.items()},
            **{label: {"path": str(row["path"].relative_to(ROOT)).replace("\\", "/"), "sha256_from_accepted_review": row["sha256_from_accepted_review"], "size_bytes": row["size_bytes"], "byte_hash_recomputed": False} for label, row in DB_IDENTITIES.items()},
        },
        "cache_identity": {
            "source_coverage": source_coverage,
            "raw_meta": {key: raw_meta[key] for key in required_meta},
            "compiled_meta": {key: compiled_meta[key] for key in ("payload_schema", "compiler_id", "source_sha256", "project_binding_sha256", "topology_identity_sha256", "surface_schema_version")},
            "section_ledger": section_ledger,
            "schemas": {"raw": raw_schema, "compiled": compiled_schema},
            "compiled_views_not_opened": views,
        },
        "stackup": {
            "coordinate_convention": "z=0 at Signal$TOP top face; positive z follows source stackup order",
            "total_thickness_um": 2922.0,
            "conductor_count": len(conductors),
            "dielectric_count": len(dielectrics),
            "dielectric_frequency_point_count": dielectric_point_count,
            "conductors": conductors,
            "dielectrics": dielectrics,
        },
        "geometry_representation": {
            "row_counts": row_counts,
            "plane_artwork": "plane_primitives plus plane_vertices/plane_circles are 2-D source geometry in um; surfaces bind asset/island hashes and pm bounds",
            "traces": "traces store source centerline endpoints, width and source hash; endpoint coordinates are in nodes",
            "pads": "padstacks store drill/material and pad_shapes store per-layer 2-D footprint dimensions; nodes/vias carry padstack and rotation bindings",
            "vias": "vias store two layer/node endpoints, XY, padstack, rotation and source hash; no plated-barrel wall volume is stored",
            "source_surface_envelope_um": [value / 1e6 for value in surface_extent_pm],
        },
        "source_geometry_controls": {
            "selected_net": SOURCE_NET,
            "trace": {"row": trace, "start_node": start_node, "end_node": end_node, "length_um": trace_length_um, "width_um": trace["width_pm"] / 1e6, "qualification": "exact source centerline and width; endcap/cross-section volume is not specified"},
            "via": {"row": via, "endpoint_nodes": via_nodes, "padstack": padstack, "pad_shapes": pad_shapes, "foil_center_span_um": via_center_span_um, "qualification": "exact source endpoint/pad/drill rows; plated barrel outer diameter and wall thickness are unavailable"},
            "surface": {"row": surface, "primitive_summary": primitive_summary, "first_primitive": primitive, "first_primitive_vertex_count": len(primitive_vertices), "first_primitive_first_vertices": primitive_vertices[:8], "qualification": "exact cached source asset and one polygon record; no union or geometry decode was performed"},
        },
        "board_outline_and_dielectric_extent": {
            "status": "NO_EXPLICIT_BOARD_OUTLINE_OR_LATERAL_DIELECTRIC_EXTENT_IN_CHECKED_CACHE_SCHEMA",
            "checked_outline_named_tables_or_views": explicit_outline_names,
            "checked_outline_named_meta_keys": explicit_outline_meta,
            "source_surface_envelope_um": [value / 1e6 for value in surface_extent_pm],
            "z_extent_is_source_bound": True,
            "lateral_extent_is_unverified": True,
            "note": "The source surface envelope is geometry evidence, not a board-outline certificate. Compiled scenario payload bytes were not opened.",
        },
        "remaining_input_gaps": [
            "An explicit board outline and lateral dielectric/void/cutout domain are absent from the checked table, view and metadata names.",
            "No preassembled 3-D conductor solid, dielectric solid, tetrahedral mesh or current/charge basis is stored.",
            "Trace endcap and through-thickness cross-section choices are not encoded by the centerline/width rows.",
            "Via plated-barrel wall thickness and outer diameter are absent; drill diameter and two endpoint pad footprints are available.",
            "2-D plane primitives and pad shapes still require reviewed source unions and conductor/dielectric interface ownership before a board VIE input exists.",
        ],
        "scope": "A bounded inventory of pinned cached source rows and derived z intervals. It supplies dimensional controls for later 3-D kernel/interface work but does not qualify a board outline, conductor union, dielectric domain, 3-D mesh, VIE/PEEC system, board response or PowerSI accuracy.",
        "elapsed_s": elapsed,
    }


def self_check() -> None:
    z = 0.0
    intervals = []
    for thickness in (10.0, 3.0, 2.0):
        intervals.append((z, z + thickness))
        z += thickness
    require(intervals == [(0.0, 10.0), (10.0, 13.0), (13.0, 15.0)], "self-check z intervals")
    print(json.dumps({"status": "PASS_SELF_CHECK", "intervals_um": intervals}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-seconds", type=float, default=30.0)
    parser.add_argument("--dry-check", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    require(0 < args.max_seconds <= 30.0, "max-seconds must be in (0,30]")
    result = collect(args.max_seconds)
    if args.dry_check:
        print(json.dumps({"status": "PASS_DRY_CHECK", "conductors": result["stackup"]["conductor_count"], "dielectrics": result["stackup"]["dielectric_count"], "source_controls": sorted(result["source_geometry_controls"]), "outline_status": result["board_outline_and_dielectric_extent"]["status"], "elapsed_s": result["elapsed_s"]}, allow_nan=False))
        return 0
    require(args.output is not None, "--output is required")
    output = args.output.resolve()
    output.mkdir(parents=False, exist_ok=False)
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"status": result["status"], "conductors": result["stackup"]["conductor_count"], "dielectrics": result["stackup"]["dielectric_count"], "elapsed_s": result["elapsed_s"], "result_sha256": sha256(output / "result.json")}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
