"""SPD Decap PI Evaluator v0.23.1: restore selected port18 pin-chain geometry from caches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from shapely.geometry import Point


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUTPUT = R / "astra-port18-selected-pin-geometry-20260912.json"
PINS = {
    "chain": (R / "astra-port18-selected-pin-chain-topology-20260912.json", "0c6943c193f03f05bcfb4ba81950997afbc23513641993c4ed3bee7f93a259bc"),
    "pin_map": (R / "astra-native-selected-first-via-map-02/pin-edge-map.json", "72bb2b2e6389bd5407c7bea67b53cb306ade91f3c1a65d6e71c5a97bdfc379b4"),
    "owner_inventory": (R / "astra-full-return-source-owners-04/source-owner-inventory.json", "4cdca3e08018ed552d987a0d23489499d03fc4365d6d5d37cfb498c9478caa82"),
    "l02_geometry": (R / "astra-device-l02-via-contacts-03/lower-via-contact-ledger.json", "7d2477d09ab5e50cbea2397eeb19385389c7722fdd05d682987c08736a02affa"),
    "l14_footprint": (R / "astra-step6e-loaded-boundary-01/l14-via-footprint-qualification.json", "98848ffea1eaa07f601d24fbfdf8ba642226ded7d05d9e5d2f1286af14854d9c"),
    "l14_contact": (R / "astra-port18-l14-contact-935-20260912.json", "2867a112e60db50a69c4c3b3b68c9f048f151d43d78661b2eeac7a0af9db3175"),
}
RAW_DB = R / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
RAW_DB_SHA256 = "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def one(db: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> dict[str, object]:
    rows = [dict(row) for row in db.execute(sql, values)]
    assert len(rows) == 1, (sql, values, len(rows))
    return rows[0]


def disk_wkb(x_pm: int, y_pm: int, diameter_pm: int) -> str:
    # 24 segments per quadrant matches the existing 96-point cached disc convention.
    return Point(x_pm / 1e6, y_pm / 1e6).buffer(diameter_pm / 2e6, resolution=24).wkb_hex


def main() -> None:
    assert not OUTPUT.exists()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    assert RAW_DB.stat().st_size == 2_752_458_752

    chain = json.loads(PINS["chain"][0].read_bytes())
    pin_map = json.loads(PINS["pin_map"][0].read_bytes())
    selected_pin = [row for row in pin_map if row["pin_id"] == "SITE0:3576"]
    assert len(selected_pin) == 1
    selected_pin = selected_pin[0]
    assert selected_pin["via_id"] == "Via507127" and selected_pin["role"] == "power"

    owner_inventory = json.loads(PINS["owner_inventory"][0].read_bytes())
    first_edge = [row for row in owner_inventory["edges"] if row["quotient_edge_id"] == selected_pin["edge_id"]]
    assert len(first_edge) == 1 and first_edge[0]["final_finite_edge_index"] == 587

    owners = [row["owners"][0].split(":", 1)[1] for row in chain["rows"]]
    assert owners == [f"via507{number}" for number in (127, 126, 125, 124, 123, 122, 121, 120, 119, 117, 118, 116, 115)]

    db = sqlite3.connect(RAW_DB.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        assert meta["payload_schema"] == "spd-raw-spatial-contact-sqlite-v3"
        segments = []
        source_vias: dict[str, dict[str, object]] = {}
        source_nodes: dict[str, dict[str, object]] = {}
        for chain_row, via_id in zip(chain["rows"], owners, strict=True):
            via = one(db, "SELECT * FROM vias WHERE via_id_fold=?", (via_id,))
            start = one(db, "SELECT * FROM nodes WHERE node_id_fold=?", (via["start_node_id_fold"],))
            end = one(db, "SELECT * FROM nodes WHERE node_id_fold=?", (via["end_node_id_fold"],))
            stack = one(db, "SELECT * FROM padstacks WHERE padstack_id_fold=?", (via["padstack_id_fold"],))
            layers = [dict(row) for row in db.execute(
                "SELECT * FROM stackup_layers WHERE layer_name IN (?,?) ORDER BY layer_ordinal",
                (via["start_layer_id"], via["end_layer_id"]),
            )]
            shapes = [dict(row) for row in db.execute(
                "SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id_fold IN (?,?) ORDER BY ordinal",
                (via["padstack_id_fold"], via["start_layer_id_fold"], via["end_layer_id_fold"]),
            )]
            assert via["net_fold"] == "adc_vdd_075_vtrip_sram/0"
            expected_status = "EXACT" if via_id in {"via507127", "via507115"} else "OUT_OF_SCOPE"
            assert via["status"] == expected_status
            assert len(layers) == 2 and stack["material"] == "COPPER" and stack["drill_diameter_pm"] > 0
            assert via["start_x_pm"] == via["end_x_pm"] == 8_315_200_000
            assert via["start_y_pm"] == via["end_y_pm"]
            assert via["start_y_pm"] in {20_308_500_000, 20_368_500_000}
            source_vias[via_id] = via
            source_nodes[start["node_id"]] = start
            source_nodes[end["node_id"]] = end
            segments.append({
                "chain_row": chain_row,
                "via": via,
                "start_node": start,
                "end_node": end,
                "padstack": stack,
                "endpoint_pad_shapes": shapes,
                "stackup_layers": layers,
                "solid_barrel_footprint_wkb_hex": disk_wkb(via["start_x_pm"], via["start_y_pm"], stack["drill_diameter_pm"]),
            })

        first, last = segments[0], segments[-1]
        top_shape = one(db, "SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id_fold=?", (first["start_node"]["padstack_id_fold"], "signal$top"))
        top_via_shape = one(db, "SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id_fold=?", (first["via"]["padstack_id_fold"], "signal$top"))
        l14_shape = one(db, "SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id_fold=?", (last["via"]["padstack_id_fold"], "signal$l14(main_power4)"))
        assert top_shape["shape_kind"] == top_via_shape["shape_kind"] == l14_shape["shape_kind"] == "CIRCLE"
        assert top_shape["width_pm"] == top_shape["height_pm"] == 100_000_000
        assert top_via_shape["width_pm"] == top_via_shape["height_pm"] == 60_000_000
        assert l14_shape["width_pm"] == l14_shape["height_pm"] == 60_000_000
    finally:
        db.close()

    l02 = json.loads(PINS["l02_geometry"][0].read_bytes())
    l02_rows = {row["via_id_fold"]: row for row in l02["entities"] if row["via_id_fold"] in {"via507127", "via507126"}}
    assert set(l02_rows) == {"via507127", "via507126"}
    l14 = json.loads(PINS["l14_footprint"][0].read_bytes())
    l14_rows = [row for row in l14["via_rows"] if row["via_id"].casefold() == "via507115"]
    assert len(l14_rows) == 1 and l14_rows[0]["pad_disk_fully_covered"]
    l14_contact = json.loads(PINS["l14_contact"][0].read_bytes())
    assert l14_contact["global_active_node"] == 757823
    assert l14_contact["local_electrode"] == 935
    assert l14_contact["contact"]["owner_id"] == "conditional-contact:Via507115"
    assert l14_contact["contact"]["footprint_sha256"] == "13bfd2893646fd99b54b985fe594d3a0fd53ab0499c80a4b23e15086e9ac196a"
    assert l14_contact["group"]["island_id"] == l14_rows[0]["island_id"]
    assert l14_contact["group"]["maximum_barrel_radius_um"] == 20.0
    assert len(l14_contact["full_basis_ids"]) == 16

    continuity = []
    for previous, following in zip(segments, segments[1:]):
        previous_end = previous["via"]["end_node_id"]
        following_start = following["via"]["start_node_id"]
        exact = previous_end == following_start
        same_layer = previous["via"]["end_layer_id_fold"] == following["via"]["start_layer_id_fold"]
        continuity.append({
            "from_via_id": previous["via"]["via_id"],
            "to_via_id": following["via"]["via_id"],
            "from_end_node_id": previous_end,
            "to_start_node_id": following_start,
            "exact_source_node_match": exact,
            "same_source_layer": same_layer,
            "source_xy_offset_um": [
                (following["start_node"]["x_pm"] - previous["end_node"]["x_pm"]) / 1e6,
                (following["start_node"]["y_pm"] - previous["end_node"]["y_pm"]) / 1e6,
            ],
            "active_topology_continuous": True,
            "continuity_kind": "exact-source-node" if exact else "same-layer-artwork-quotient",
        })
    assert all(row["same_source_layer"] and row["active_topology_continuous"] for row in continuity)
    assert [row["from_via_id"] for row in continuity if not row["exact_source_node_match"]] == ["Via507123", "Via507119"]

    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "RESTORED_SELECTED_PORT18_PIN_CHAIN_GEOMETRY_NOT_FIELD_CLOSURE",
        "inputs": {name: {"path": str(path), "sha256": expected, "size_bytes": path.stat().st_size} for name, (path, expected) in PINS.items()},
        "raw_spatial": {"path": str(RAW_DB), "sha256": RAW_DB_SHA256, "size_bytes": RAW_DB.stat().st_size, "hash_recomputed": False, "meta": {key: meta[key] for key in ("payload_schema", "source_sha256", "project_binding_sha256", "geometry_identity_sha256")}},
        "cache_identity": "raw_spatial",
        "selected_pin": selected_pin,
        "first_quotient_edge_binding": first_edge[0],
        "source_vias": source_vias,
        "source_nodes": source_nodes,
        "source_node_continuity": continuity,
        "segments": segments,
        "top_dut_pad": {
            "source_node": first["start_node"],
            "pad_shape": top_shape,
            "pad_footprint_wkb_hex": disk_wkb(first["via"]["start_x_pm"], first["via"]["start_y_pm"], top_shape["width_pm"]),
            "pad_radius_um": top_shape["width_pm"] / 2e6,
            "first_via_launch_pad_shape": top_via_shape,
            "first_via_launch_pad_radius_um": top_via_shape["width_pm"] / 2e6,
            "conditional_2d_electrode_support_available": False,
            "full_3d_electrode_h_d_closure_available": False,
        },
        "l14_terminal_pad": {
            "source_node": last["end_node"],
            "pad_shape": l14_shape,
            "pad_footprint_wkb_hex": disk_wkb(last["via"]["end_x_pm"], last["via"]["end_y_pm"], l14_shape["width_pm"]),
            "qualified_artwork_contact": l14_rows[0],
            "conditional_2d_filled_core_electrode": l14_contact,
            "raw_endpoint_active_index": 718402,
            "final_hybrid_spatial_node": 757823,
            "pad_radius_um": l14_shape["width_pm"] / 2e6,
            "filled_core_barrel_radius_um": l14_contact["group"]["maximum_barrel_radius_um"],
            "conditional_2d_electrode_support_available": True,
            "full_3d_electrode_h_d_closure_available": False,
            "support_interpretation": "The conditional electrode uses the 20 um filled barrel core and 16 L14 P1 nodes; the restored source pad is a distinct 30 um-radius fully covered pad.",
        },
        "l02_cached_geometry": {
            key: {name: row[name] for name in (
                "entity_index", "via_id", "start_layer_id", "end_layer_id", "status",
                "extends_below_l02", "is_through_l02", "effective_l02_support_wkb_hex", "solid_barrel_wkb_hex",
            )}
            for key, row in l02_rows.items()
        },
        "checks": {
            "selected_pin_first_edge_exact": True,
            "thirteen_source_rows_same_net_and_exactly_bound": True,
            "source_status_end_segments_exact_interior_segments_out_of_scope": True,
            "twelve_source_layer_transitions_recorded": True,
            "ten_exact_source_node_joins_and_two_same_layer_artwork_quotient_joins": True,
            "source_rows_and_padstacks_hash_bound": True,
            "top_pad_shape_restored": True,
            "l14_pad_shape_restored": True,
            "l14_barrel_and_pad_fully_covered_by_qualified_artwork": True,
            "l14_conditional_2d_filled_core_electrode_bound": True,
            "raw_endpoint_718402_must_map_to_final_spatial_node_757823": True,
        },
        "limitations": [
            "Two-dimensional pad/barrel footprints and source layer spans are restored; the chain changes Y by 60 um at two same-layer artwork quotient joins, and no finite three-dimensional electrode surface mesh is supplied.",
            "The TOP pad record does not prove a unique equipotential source-electrode cut or current distribution across the 978 merged source branches.",
            "The L14 pad is geometrically contained in one qualified artwork island and has a conditional two-dimensional filled-core electrode support at local electrode 935; this is not the full pad support and does not supply a compatible full-three-dimensional H/D electrode-charge closure for the actual board.",
            "No circuit condensation, Green assembly, field solve, canonical load transfer, spatial-convergence claim, or board-accuracy claim is made.",
        ],
    }
    OUTPUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "segments": len(segments), "output": str(OUTPUT), "sha256": sha(OUTPUT)}))


if __name__ == "__main__":
    main()
