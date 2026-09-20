"""SPD Decap PI Evaluator v0.23.1: restore the two port18 source Trace joins."""
from __future__ import annotations

import hashlib
import json
from math import hypot
from pathlib import Path
import sqlite3

from shapely.affinity import rotate
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs/research"
OUTPUT = RESEARCH / "astra-port18-artwork-joins-20260912.json"
RAW = RESEARCH / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
COMPILED = RESEARCH / "astra-step4-basis-01/indexes/compiled-topology.sqlite"
GEOMETRY = RESEARCH / "astra-port18-selected-pin-geometry-20260912.json"
CHAIN = RESEARCH / "astra-port18-selected-pin-chain-topology-20260912.json"
POLICY = ROOT / "src/spd_decap_pi/_core/io/reduced_conductor.py"
PINS = {
    GEOMETRY: "a84a2e6a4febc8a4cc45b25168cd1394c117b6a631b940fe920bc3bc3dc7f211",
    CHAIN: "0c6943c193f03f05bcfb4ba81950997afbc23513641993c4ed3bee7f93a259bc",
    COMPILED: "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b",
    POLICY: "aedbd8ad15123b8e6c7c94023a880b7efb2aea0e106331e92815e806f6399f4b",
}
RAW_SHA256 = "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7"
JOIN_SPECS = (
    ("Trace480236", "Via507123", "Via507122", "Node695285", "Node695286", "Signal$L06(DGND)"),
    ("Trace480235", "Via507119", "Via507117", "Node695284", "Node695283", "Signal$L10(DGND)"),
)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rows(db: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> list[dict[str, object]]:
    return [dict(row) for row in db.execute(sql, values)]


def one(db: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> dict[str, object]:
    found = rows(db, sql, values)
    assert len(found) == 1, (sql, values, len(found))
    return found[0]


def rectangle(trace: dict[str, object], start: dict[str, object], end: dict[str, object]) -> Polygon:
    dx = end["x_pm"] - start["x_pm"]
    dy = end["y_pm"] - start["y_pm"]
    length = hypot(dx, dy)
    width = trace["width_pm"]
    ox, oy = -dy * width / (2 * length), dx * width / (2 * length)
    return Polygon([
        ((start["x_pm"] + ox) / 1e6, (start["y_pm"] + oy) / 1e6),
        ((end["x_pm"] + ox) / 1e6, (end["y_pm"] + oy) / 1e6),
        ((end["x_pm"] - ox) / 1e6, (end["y_pm"] - oy) / 1e6),
        ((start["x_pm"] - ox) / 1e6, (start["y_pm"] - oy) / 1e6),
    ])


def pad_geometry(row: dict[str, object], x_um: float, y_um: float):
    width, height = row["width_pm"] / 1e6, row["height_pm"] / 1e6
    if row["shape_kind"] == "CIRCLE":
        assert width == height
        return Point(x_um, y_um).buffer(width / 2, resolution=24)
    assert row["shape_kind"] in {"RECTANGLE", "RECT"}
    result = box(x_um - width / 2, y_um - height / 2, x_um + width / 2, y_um + height / 2)
    angle = row.get("rotation_microdegrees", 0) / 1e6
    return rotate(result, angle, origin=(x_um, y_um)) if angle else result


def main() -> None:
    assert not OUTPUT.exists()
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    assert RAW.stat().st_size == 2_752_458_752
    restored = json.loads(GEOMETRY.read_bytes())
    source_vias = restored["source_vias"]
    source_nodes = restored["source_nodes"]
    continuity = restored["source_node_continuity"]

    raw = sqlite3.connect(RAW.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    compiled = sqlite3.connect(COMPILED.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    raw.row_factory = compiled.row_factory = sqlite3.Row
    raw.execute("PRAGMA query_only=ON")
    compiled.execute("PRAGMA query_only=ON")
    try:
        raw_meta = dict(raw.execute("SELECT key,value FROM meta"))
        compiled_meta = dict(compiled.execute("SELECT key,value FROM meta"))
        surface_view = one(compiled, "SELECT name,payload_size,payload_sha256 FROM views WHERE name=?", ("surface",))
        assert raw_meta["source_sha256"] == compiled_meta["source_sha256"]
        assert raw_meta["compiled_topology_identity_sha256"] == compiled_meta["topology_identity_sha256"]
        joins = []
        for trace_id, upper_via, lower_via, first_node, second_node, layer in JOIN_SPECS:
            trace = one(raw, "SELECT * FROM traces WHERE trace_id_fold=?", (trace_id.casefold(),))
            source = {node: one(raw, "SELECT * FROM nodes WHERE node_id_fold=?", (node.casefold(),)) for node in (first_node, second_node)}
            assert source == {node: source_nodes[node] for node in (first_node, second_node)}
            assert trace["net_fold"] == "adc_vdd_075_vtrip_sram/0"
            assert trace["layer_id"] == layer and trace["geometry_status"] == trace["width_status"] == "EXACT"
            assert {trace["start_node_id"], trace["end_node_id"]} == {first_node, second_node}
            assert trace["width_pm"] == 50_000_000
            material = one(raw, "SELECT * FROM stackup_layers WHERE layer_name=?", (layer,))
            assert material["layer_kind"] == "conductor" and material["material_name"] == "COPPER"
            assert material["thickness_um"] == 20.0 and material["conductivity_s_per_m"] == 59_590_000.0
            endpoint_inventory = {}
            for node_id in (first_node, second_node):
                key = node_id.casefold()
                endpoint_inventory[node_id] = {
                    "traces": rows(raw, "SELECT trace_id,owner_id,source_record_sha256 FROM traces WHERE start_node_id_fold=? OR end_node_id_fold=? ORDER BY ordinal", (key, key)),
                    "vias": rows(raw, "SELECT via_id,owner_id,status,source_record_sha256 FROM vias WHERE start_node_id_fold=? OR end_node_id_fold=? ORDER BY ordinal", (key, key)),
                }
                assert len(endpoint_inventory[node_id]["traces"]) == len(endpoint_inventory[node_id]["vias"]) == 1
                assert endpoint_inventory[node_id]["traces"][0]["trace_id"] == trace_id
            assert {endpoint_inventory[first_node]["vias"][0]["via_id"], endpoint_inventory[second_node]["vias"][0]["via_id"]} == {upper_via, lower_via}
            adjacent = {via_id: source_vias[via_id.casefold()] for via_id in (upper_via, lower_via)}
            pad_shapes = []
            for via_id, node_id in ((upper_via, first_node), (lower_via, second_node)):
                via = adjacent[via_id]
                shape = one(raw, "SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id_fold=?", (via["padstack_id_fold"], layer.casefold()))
                assert shape["shape_kind"] == "CIRCLE" and shape["width_pm"] == shape["height_pm"] == 60_000_000
                node = source[node_id]
                pad_shapes.append({"via_id": via_id, "node_id": node_id, "source_row": shape,
                                   "derived_96_segment_wkb_hex": Point(node["x_pm"] / 1e6, node["y_pm"] / 1e6).buffer(30.0, resolution=24).wkb_hex})
            trace_polygon = rectangle(trace, source[trace["start_node_id"]], source[trace["end_node_id"]])
            pads = [Point(source[row["node_id"]]["x_pm"] / 1e6, source[row["node_id"]]["y_pm"] / 1e6).buffer(30.0, resolution=24) for row in pad_shapes]
            domain = unary_union([trace_polygon, *pads])
            assert abs(trace_polygon.area - 3_000.0) < 1e-9 and domain.is_valid
            matching = [row for row in continuity if {row["from_via_id"], row["to_via_id"]} == {upper_via, lower_via}]
            assert len(matching) == 1 and not matching[0]["exact_source_node_match"] and matching[0]["active_topology_continuous"]
            min_x, min_y, max_x, max_y = [round(value * 1e6) for value in domain.bounds]
            surfaces = rows(raw, "SELECT * FROM surfaces WHERE net_fold=? AND layer_id_fold=? AND min_x_pm<=? AND max_x_pm>=? AND min_y_pm<=? AND max_y_pm>=? ORDER BY ordinal",
                            (trace["net_fold"], trace["layer_id_fold"], max_x, min_x, max_y, min_y))
            assert not surfaces
            trace_candidates = rows(raw, "SELECT * FROM traces WHERE net_fold=? AND layer_id_fold=? AND geometry_status='EXACT' AND width_pm IS NOT NULL AND min_x_pm-width_pm/2<=? AND max_x_pm+width_pm/2>=? AND min_y_pm-width_pm/2<=? AND max_y_pm+width_pm/2>=? ORDER BY ordinal",
                                    (trace["net_fold"], trace["layer_id_fold"], max_x, min_x, max_y, min_y))
            intersecting_traces = []
            for candidate in trace_candidates:
                c_start = one(raw, "SELECT * FROM nodes WHERE node_id_fold=?", (candidate["start_node_id_fold"],))
                c_end = one(raw, "SELECT * FROM nodes WHERE node_id_fold=?", (candidate["end_node_id_fold"],))
                if rectangle(candidate, c_start, c_end).intersects(domain):
                    intersecting_traces.append(candidate)
            assert [row["trace_id"] for row in intersecting_traces] == [trace_id]
            via_candidates = rows(raw, "SELECT v.*,p.ordinal AS pad_shape_ordinal,p.shape_kind,p.width_pm,p.height_pm,p.source_record_sha256 AS pad_shape_source_record_sha256 FROM vias v JOIN pad_shapes p ON p.padstack_id_fold=v.padstack_id_fold AND p.layer_id_fold=? WHERE v.net_fold=? AND (v.start_layer_id_fold=? OR v.end_layer_id_fold=?) AND v.start_x_pm-p.width_pm/2<=? AND v.start_x_pm+p.width_pm/2>=? AND v.start_y_pm-p.height_pm/2<=? AND v.start_y_pm+p.height_pm/2>=? ORDER BY v.ordinal,p.ordinal",
                                  (trace["layer_id_fold"], trace["net_fold"], trace["layer_id_fold"], trace["layer_id_fold"], max_x, min_x, max_y, min_y))
            intersecting_via_landings = []
            for candidate in via_candidates:
                geometry = pad_geometry(candidate, candidate["start_x_pm"] / 1e6, candidate["start_y_pm"] / 1e6)
                if geometry.intersects(domain):
                    intersecting_via_landings.append(candidate)
            assert {row["via_id"] for row in intersecting_via_landings} == {upper_via, lower_via}
            joins.append({
                "binding_kind": "exact-source-trace",
                "trace": trace,
                "source_nodes": source,
                "adjacent_vias": adjacent,
                "endpoint_contact_inventory": endpoint_inventory,
                "stackup_material": material,
                "exact_trace_rectangle": {"coordinate_unit": "um", "length_um": 60.0, "width_um": 50.0,
                                          "area_um2": trace_polygon.area, "wkb_hex": trace_polygon.wkb_hex},
                "source_derived_pad_footprints": pad_shapes,
                "source_derived_trace_pad_domain": {"coordinate_unit": "um", "circle_rendering": "96-segment deterministic approximation",
                                                    "area_um2": domain.area, "wkb_hex": domain.wkb_hex},
                "surface_rows_covering_join": surfaces,
                "intersecting_same_net_same_layer_traces": intersecting_traces,
                "intersecting_same_net_same_layer_via_landings": intersecting_via_landings,
                "entire_domain_extra_contacts": [],
                "domain_isolation": {
                    "no_additional_conductor_contacts": True,
                    "intersecting_trace_ids": [row["trace_id"] for row in intersecting_traces],
                    "intersecting_via_ids": [row["via_id"] for row in intersecting_via_landings],
                    "expected_trace_id": trace_id,
                    "expected_via_ids": [upper_via, lower_via],
                    "method": "indexed source bounds followed by exact source-derived footprint intersection over the full trace-plus-pad domain",
                },
                "surface_island_binding_required": False,
                "active_quotient_binding": matching[0],
                "electrical_ownership": {"mode": "topology_only_ideal", "series_resistance_or_inductance_assigned": False,
                                         "reason": "source Trace endpoints are joined by the production ideal Trace quotient"},
            })
    finally:
        raw.close()
        compiled.close()

    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "RESTORED_TWO_EXACT_SOURCE_TRACE_JOINS_NOT_SHEET_SOLUTION",
        "inputs": {str(path): {"sha256": expected, "size_bytes": path.stat().st_size} for path, expected in PINS.items()},
        "raw_cache": {"path": str(RAW), "sha256": RAW_SHA256, "hash_recomputed": False, "size_bytes": RAW.stat().st_size,
                      "meta": {key: raw_meta[key] for key in ("payload_schema", "source_sha256", "project_binding_sha256", "geometry_identity_sha256", "compiled_topology_identity_sha256")}},
        "compiled_cache": {"path": str(COMPILED), "sha256": PINS[COMPILED], "size_bytes": COMPILED.stat().st_size,
                           "meta": {key: compiled_meta[key] for key in ("payload_schema", "source_sha256", "project_binding_sha256", "topology_identity_sha256", "surface_compiler_id", "surface_schema_version")},
                           "surface_view": surface_view},
        "joins": joins,
        "checks": {
            "two_exact_trace_rows": True,
            "source_node_rows_match_restored_chain": True,
            "each_endpoint_has_one_trace_and_one_via": True,
            "same_net_and_same_layer_per_join": True,
            "exact_60um_by_50um_trace_rectangles": True,
            "copper_20um_thickness_and_59590000_s_per_m": True,
            "no_surface_island_inference_used": True,
            "entire_trace_pad_domain_has_no_extra_trace_or_via_contacts": True,
            "active_quotient_continuity_matches_path_chain": True,
            "ideal_trace_union_policy_hash_bound": True,
        },
        "limitations": [
            "The exact source Trace and pad scalar geometry is restored; circular pad WKB is a labeled deterministic 96-segment rendering.",
            "The production quotient treats each Trace as topology_only_ideal and assigns no horizontal series R or L.",
            "No same-layer artwork island is required or inferred for either join; the source Trace directly binds the endpoint nodes.",
            "This does not assign a distributed sheet current, field coupling, full-board accuracy, or a replacement stamp.",
        ],
    }
    OUTPUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "joins": len(joins), "output": str(OUTPUT), "sha256": sha(OUTPUT)}))


if __name__ == "__main__":
    main()
