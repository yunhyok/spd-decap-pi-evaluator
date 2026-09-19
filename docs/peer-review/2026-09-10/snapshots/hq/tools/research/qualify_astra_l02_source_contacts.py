"""Freeze the ordered native L02 via/contact inputs before geometric classification."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
import shapely

import project_astra_l14_gc_mass as mass


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
LAYER = "Signal$L02(DGND)"
NET = "DGND"
TARGET = 349710
COMPONENT = "spd-surface-equivalence-component:d4691ffaac6a928e1ffad1a1"
TARGET_VERTEX = "spd-finite-via-vertex:75e70fea61021703aa9311b5"
ASSET_SHA = "b6d6b3d52268fc4972140b93e62ce8eb191a5367f5fc0cc74f3f836a117b1ddd"
PINS = {
    "raw_field": (R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz", "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "compiled_db": (R / "astra-step4-basis-01/indexes/compiled-topology.sqlite", "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"),
    "raw_spatial_db": (R / "astra-step4-basis-01/indexes/raw-spatial.sqlite", "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7"),
    "inventory": (R / "astra-l02-source-inventory-02/result.json", "0af954600a5070f8f2fa27a81ba941889b46ac9f26ac2b76d8669f4ce3af93f2"),
    "exceptions": (R / "astra-l02-via-exceptions-01/result.json", "1a39a0f3360844dd801abf5914230cf5e132a7ee717a41ee82c304d35f7ea7da"),
    "geometry_asset": (R / "astra-l02-gc-source-overlaps-01/source-assets/0000-b6d6b3d52268fc49.spdgeom.zlib", ASSET_SHA),
    "polygon_helper": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
    "raw_spatial_accessors": (ROOT / "src/spd_decap_pi/raw_spatial_contact_asset.py", "383f17396ecc6ee2114cbb2d08be4b576b134930b6b6a49d0432a671959af4a3"),
    "placed_pad_geometry": (ROOT / "src/spd_decap_pi/_core/io/shared_pad.py", "f6a53ca964efe79bed9da2bc88099d395f5f01f65d9b925ab9c8e18749ce8b4c"),
}
RAW_SOURCE_SHA = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
RAW_LOGICAL_SHA = "a49f447e34a48b220bfa5107ac8a520a5a4906745f1b715c91d584c0752b09bf"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def decode_text(array: np.ndarray) -> list[str]:
    value = json.loads(array.tobytes().decode("utf-8"))
    assert isinstance(value, list) and all(isinstance(item, str) for item in value)
    return value


def packed_text(values: list[str]) -> np.ndarray:
    return np.frombuffer(json.dumps(values, separators=(",", ":")).encode("utf-8"), dtype=np.uint8)


def read_db(path: Path, deadline: float) -> sqlite3.Connection:
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    return db


def verify_inputs() -> dict[str, dict[str, str]]:
    result = {}
    for name, (path, expected) in PINS.items():
        assert digest(path) == expected, name
        result[name] = {"path": str(path.resolve()), "sha256": expected}
    return result


def dry_check() -> dict[str, object]:
    started = time.monotonic()
    inputs = verify_inputs()
    with read_db(PINS["raw_spatial_db"][0], started + 30) as db:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        assert meta["source_sha256"] == RAW_SOURCE_SHA
        assert meta["logical_rows_sha256"] == RAW_LOGICAL_SHA
        rows = list(db.execute(
            "SELECT ps.padstack_id,ps.shape_kind,ps.width_pm,ps.height_pm,p.drill_diameter_pm,p.material "
            "FROM pad_shapes ps JOIN padstacks p ON p.padstack_id_fold=ps.padstack_id_fold "
            "WHERE ps.layer_id_fold=? AND ps.padstack_id_fold IN "
            "(SELECT DISTINCT padstack_id_fold FROM vias WHERE net_fold=? AND "
            "(start_layer_id_fold=? OR end_layer_id_fold=?)) ORDER BY ps.padstack_id_fold",
            (LAYER.casefold(), NET.casefold(), LAYER.casefold(), LAYER.casefold()),
        ))
        counts = db.execute(
            "SELECT COUNT(*),SUM(status='EXACT') FROM vias WHERE net_fold=? AND "
            "(start_layer_id_fold=? OR end_layer_id_fold=?)",
            (NET.casefold(), LAYER.casefold(), LAYER.casefold()),
        ).fetchone()
    assert tuple(map(int, counts)) == (76181, 76181)
    assert len(rows) == 4
    assert all(row[1] == "CIRCLE" and row[2] == row[3] and row[4] > 0 and row[5] == "COPPER" for row in rows)
    return {"status": "PASS_L02_CONTACT_INPUT_DRY_CHECK", "inputs": inputs, "source_via_count": 76181,
            "pad_definitions": [dict(row) for row in rows], "elapsed_s": time.monotonic() - started}


def self_check() -> None:
    text = ["via:a", "via:b"]
    assert decode_text(packed_text(text)) == text
    polygons = [shapely.box(0, 0, 1, 1), shapely.box(2, 0, 3, 1)]
    wkbs = [shapely.to_wkb(shape) for shape in polygons]
    offsets = np.r_[0, np.cumsum([len(item) for item in wkbs], dtype=np.int64)]
    data = np.frombuffer(b"".join(wkbs), dtype=np.uint8)
    restored = [shapely.from_wkb(data[offsets[i]:offsets[i + 1]].tobytes()) for i in range(2)]
    assert all(a.equals_exact(b, 0.0) for a, b in zip(polygons, restored))
    xy = np.array([[0, 0], [0, 0], [1, 0]], dtype=np.int64)
    unique, inverse, counts = np.unique(xy, axis=0, return_inverse=True, return_counts=True)
    assert unique.tolist() == [[0, 0], [1, 0]] and inverse.tolist() == [0, 0, 1] and counts.tolist() == [2, 1]


def run(output: Path) -> dict[str, object]:
    started = time.monotonic()
    deadline = started + 90
    inputs = verify_inputs()
    inventory = json.loads(PINS["inventory"][0].read_bytes())
    exceptions = json.loads(PINS["exceptions"][0].read_bytes())
    component = inventory["component"]
    island_ids = list(component["island_ids"])
    assert inventory["active_index"] == TARGET and component["component_id"] == COMPONENT
    assert len(island_ids) == len(set(island_ids)) == 491
    assert inventory["native_incident_finite_links"] == 76139
    assert inventory["whole_layer_net_source_via_count"] == 76181
    assert inventory["whole_layer_net_source_trace_count"] == 38662

    geometry_bytes = PINS["geometry_asset"][0].read_bytes()
    polygon_by_id = mass.polygon_map(LAYER, NET, ASSET_SHA, geometry_bytes)
    assert set(polygon_by_id) == set(island_ids)
    island_wkbs = [shapely.to_wkb(shapely.normalize(polygon_by_id[item])) for item in island_ids]
    island_offsets = np.r_[0, np.cumsum([len(item) for item in island_wkbs], dtype=np.int64)]
    island_bytes = np.frombuffer(b"".join(island_wkbs), dtype=np.uint8)
    island_areas = np.array([polygon_by_id[item].area for item in island_ids], dtype=np.float64)
    island_holes = np.array([len(polygon_by_id[item].interiors) for item in island_ids], dtype=np.int64)
    island_coords = np.array([shapely.get_num_coordinates(polygon_by_id[item]) for item in island_ids], dtype=np.int64)
    island_bounds = np.array([polygon_by_id[item].bounds for item in island_ids], dtype=np.float64)
    assert np.isfinite(island_areas).all() and np.all(island_areas > 0)
    assert time.monotonic() < deadline

    with np.load(PINS["raw_field"][0], allow_pickle=False) as raw:
        first = raw["finite_first_active_indices"]
        second = raw["finite_second_active_indices"]
        selected = np.flatnonzero((first == TARGET) ^ (second == TARGET))
        assert selected.size == 76139 and np.all(np.diff(selected) > 0)
        original = np.asarray(raw["finite_active_original_indices"][selected], dtype=np.int64)
        target_side = np.where(first[selected] == TARGET, 0, 1).astype(np.int8)
        assert (int(np.count_nonzero(target_side == 0)), int(np.count_nonzero(target_side == 1))) == (47467, 28672)
        first_active = np.asarray(first[selected], dtype=np.int64)
        second_active = np.asarray(second[selected], dtype=np.int64)
        count = np.asarray(raw["finite_count"][selected], dtype=np.float64)
        resistance = np.asarray(raw["finite_resistance_ohm_per_via"][selected], dtype=np.float64)
        inductance = np.asarray(raw["finite_inductance_h_per_via"][selected], dtype=np.float64)
        assert np.all(count == 1) and np.all(resistance > 0) and np.all(inductance >= 0)

        all_owner_rows = decode_text(raw["all_finite_link_owner_ids_json"])
        owner_rows = [json.loads(all_owner_rows[int(index)]) for index in original]
        del all_owner_rows
        assert all(len(row) == 1 and row[0].startswith("via:") and row[0].count(":") == 1 for row in owner_rows)
        via_ids_fold = [row[0].split(":", 1)[1].casefold() for row in owner_rows]
        assert len(set(via_ids_fold)) == 76139
        all_link_ids = decode_text(raw["all_finite_link_ids"])
        link_ids = [all_link_ids[int(index)] for index in original]
        del all_link_ids
        assert len(set(map(int, original))) == len(set(link_ids)) == 76139
        assert all(item.startswith("spd-finite-via-edge:") for item in link_ids)
        all_first_nodes = decode_text(raw["all_finite_first_node_ids"])
        compiled_first = [all_first_nodes[int(index)] for index in original]
        del all_first_nodes
        all_second_nodes = decode_text(raw["all_finite_second_node_ids"])
        compiled_second = [all_second_nodes[int(index)] for index in original]
        del all_second_nodes
        compiled_target = [a if side == 0 else b for a, b, side in zip(compiled_first, compiled_second, target_side)]
        compiled_external = [b if side == 0 else a for a, b, side in zip(compiled_first, compiled_second, target_side)]
        assert set(compiled_target) == {TARGET_VERTEX}
        assert TARGET_VERTEX not in compiled_external
        del compiled_first, compiled_second, compiled_target
    assert time.monotonic() < deadline

    with read_db(PINS["raw_spatial_db"][0], deadline) as db:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        assert meta["payload_schema"] == "spd-raw-spatial-contact-sqlite-v3"
        assert meta["source_sha256"] == RAW_SOURCE_SHA and meta["logical_rows_sha256"] == RAW_LOGICAL_SHA
        rows = [dict(row) for row in db.execute(
            "SELECT ordinal,via_id,via_id_fold,status,start_layer_id,end_layer_id,start_node_id,end_node_id," 
            "start_x_pm,start_y_pm,end_x_pm,end_y_pm,padstack_id,padstack_id_fold,rotation_microdegrees,source_record_sha256 "
            "FROM vias WHERE net_fold=? AND (start_layer_id_fold=? OR end_layer_id_fold=?) ORDER BY ordinal",
            (NET.casefold(), LAYER.casefold(), LAYER.casefold()),
        )]
        assert len(rows) == 76181 and all(row["status"] == "EXACT" for row in rows)
        source_by_id = {row["via_id_fold"]: row for row in rows}
        assert len(source_by_id) == 76181 and set(via_ids_fold) <= set(source_by_id)
        source_rows = [source_by_id[item] for item in via_ids_fold]
        padstack_folds = sorted({row["padstack_id_fold"] for row in rows})
        placeholders = ",".join("?" for _ in padstack_folds)
        pad_rows = [dict(row) for row in db.execute(
            "SELECT * FROM pad_shapes WHERE layer_id_fold=? AND padstack_id_fold IN (" + placeholders + ") ORDER BY padstack_id_fold",
            (LAYER.casefold(), *padstack_folds),
        )]
        stack_rows = [dict(row) for row in db.execute(
            "SELECT * FROM padstacks WHERE padstack_id_fold IN (" + placeholders + ") ORDER BY padstack_id_fold",
            tuple(padstack_folds),
        )]
    assert len(pad_rows) == len(stack_rows) == len(padstack_folds) == 4
    pad_by_id = {row["padstack_id_fold"]: row for row in pad_rows}
    stack_by_id = {row["padstack_id_fold"]: row for row in stack_rows}
    assert set(pad_by_id) == set(stack_by_id) == set(padstack_folds)
    assert all(row["shape_kind"] == "CIRCLE" and row["width_pm"] == row["height_pm"] > 0 for row in pad_rows)
    assert all(row["drill_diameter_pm"] > 0 and row["material"] == "COPPER" for row in stack_rows)

    x_pm, y_pm, l02_is_start, endpoint_ids, opposite_ids = [], [], [], [], []
    source_ordinals, rotations, source_hashes, padstack_index = [], [], [], []
    pad_ordinal = {name: index for index, name in enumerate(padstack_folds)}
    for row in source_rows:
        at_start = row["start_layer_id"] == LAYER
        assert at_start ^ (row["end_layer_id"] == LAYER)
        assert row["start_x_pm"] == row["end_x_pm"] and row["start_y_pm"] == row["end_y_pm"]
        x_pm.append(int(row["start_x_pm"])); y_pm.append(int(row["start_y_pm"])); l02_is_start.append(at_start)
        endpoint_ids.append(row["start_node_id"] if at_start else row["end_node_id"])
        opposite_ids.append(row["end_node_id"] if at_start else row["start_node_id"])
        source_ordinals.append(int(row["ordinal"])); rotations.append(int(row["rotation_microdegrees"]))
        source_hashes.append(row["source_record_sha256"]); padstack_index.append(pad_ordinal[row["padstack_id_fold"]])
    x_pm = np.array(x_pm, dtype=np.int64); y_pm = np.array(y_pm, dtype=np.int64)
    xy = np.column_stack((x_pm, y_pm))
    unique_xy, group_index, group_counts = np.unique(xy, axis=0, return_inverse=True, return_counts=True)
    assert len(unique_xy) == len(group_counts) and int(group_counts.sum()) == 76139

    missing_rows = inventory["source_via_reconciliation"]["source_vias_outside_native_group"]
    missing_ids = {row["via_id_fold"] for row in missing_rows}
    assert missing_ids == set(source_by_id) - set(via_ids_fold) and len(missing_ids) == 42
    compiled_outside = inventory["source_via_reconciliation"]["matching_compiled_finite_links"]
    assert len(compiled_outside) == 40 and len({row["ordinal"] for row in compiled_outside}) == 20
    unresolved = exceptions["absent_all_kind_owners"]
    assert {row["via_id_fold"] for row in unresolved} == {"via1312784", "via1612214"}
    assert {row["via_id_fold"] for row in unresolved} <= missing_ids

    arrays = output / "contact-ledger-inputs.npz"
    mass.atomic_npz(
        arrays,
        active_finite_index=selected.astype(np.int64), original_finite_index=original,
        first_active_index=first_active, second_active_index=second_active, target_side=target_side,
        native_count=count, resistance_ohm=resistance, inductance_h=inductance,
        source_via_ordinal=np.array(source_ordinals, dtype=np.int64), l02_endpoint_is_start=np.array(l02_is_start, dtype=np.bool_),
        x_pm=x_pm, y_pm=y_pm, rotation_microdegrees=np.array(rotations, dtype=np.int64),
        padstack_index=np.array(padstack_index, dtype=np.int16), coincident_group_index=group_index.astype(np.int64),
        coincident_group_xy_pm=unique_xy.astype(np.int64), coincident_group_counts=group_counts.astype(np.int64),
        via_ids_json_utf8=packed_text([row["via_id"] for row in source_rows]), link_ids_json_utf8=packed_text(link_ids),
        compiled_external_vertex_ids_json_utf8=packed_text(compiled_external), l02_endpoint_node_ids_json_utf8=packed_text(endpoint_ids),
        opposite_endpoint_node_ids_json_utf8=packed_text(opposite_ids), source_record_sha256_json_utf8=packed_text(source_hashes),
        padstack_ids_json_utf8=packed_text([stack_by_id[item]["padstack_id"] for item in padstack_folds]),
        excluded_source_via_ids_json_utf8=packed_text(sorted(row["via_id"] for row in missing_rows)),
        island_ids_json_utf8=packed_text(island_ids), island_wkb_bytes=island_bytes, island_wkb_offsets=island_offsets,
        island_area_um2=island_areas, island_hole_count=island_holes, island_coordinate_count=island_coords, island_bounds_um=island_bounds,
    )
    result = {
        "program": PROGRAM, "version": VERSION, "status": "COMPLETED_L02_ORDERED_CONTACT_INPUT_LEDGER",
        "script_sha256": digest(Path(__file__)), "inputs": inputs, "raw_spatial_meta": meta,
        "target": {"active_index": TARGET, "layer": LAYER, "net": NET, "component_id": COMPONENT,
                   "compiled_target_vertex_id": TARGET_VERTEX, "island_count": len(island_ids),
                   "geometry_asset_sha256": ASSET_SHA},
        "native_contacts": {"count": 76139, "target_first_count": int(np.count_nonzero(target_side == 0)),
                            "target_second_count": int(np.count_nonzero(target_side == 1)),
                            "unique_via_owner_count": len(set(via_ids_fold)), "unique_center_count": len(unique_xy),
                            "l02_source_start_count": int(np.count_nonzero(l02_is_start)),
                            "padstack_contact_counts": {stack_by_id[padstack_folds[index]]["padstack_id"]: int(count_value)
                                                        for index, count_value in sorted(Counter(padstack_index).items())},
                            "coincident_multiplicity_histogram": {str(k): int(v) for k, v in sorted(Counter(group_counts).items())}},
        "pad_definitions": [{"padstack_index": pad_ordinal[item], "padstack": stack_by_id[item], "l02_pad_shape": pad_by_id[item]} for item in padstack_folds],
        "excluded_source_vias": {"count": 42, "compiled_owner_rows": 40, "compiled_link_count": 20,
                                  "unmatched_exception_via_ids": sorted(row["via_id"] for row in unresolved),
                                  "ordered_rows": [{key: row[key] for key in ("ordinal", "via_id", "start_layer_id", "end_layer_id",
                                                       "start_node_id", "end_node_id", "start_x_pm", "start_y_pm",
                                                       "padstack_id", "status", "source_record_sha256")} for row in missing_rows],
                                  "inclusion_state": "excluded_pending_source_trace_and_leaf-policy"},
        "source_trace_inventory": {"count": 38662, "binding_state": "unbound"},
        "geometry_classification": {"status": "PENDING_EXACT_FOOTPRINT_OVERLAP",
                                    "circle_convention": "shared_pad._placed_pad_polygon uses a 256-edge inscribed polygon (quad_segs=64)",
                                    "island_wkb_map_saved": True},
        "prospective_preflight": {"source_island_potential_count_before_trace_union": 491,
                                  "contact_center_count_before_overlap_union": len(unique_xy),
                                  "source_island_total_area_um2": float(np.sum(island_areas)),
                                  "source_island_total_holes": int(np.sum(island_holes)),
                                  "source_island_total_coordinates": int(np.sum(island_coords)),
                                  "mesh_node_count": None, "mesh_triangle_count": None,
                                  "note": "Counts are source/contact inputs, not measured mesh DOF."},
        "output": {"path": str(arrays.resolve()), "sha256": digest(arrays), "bytes": arrays.stat().st_size},
        "elapsed_s": time.monotonic() - started,
        "scope": "Exact ordered native finite/via endpoint, pad/drill, coincident-center and source-island WKB inputs. Footprint-to-island overlap, noncoincident pad union, trace connectivity, the 42 excluded vias, mesh, G/C mass/operator, solve, return closure and accuracy remain unresolved.",
    }
    assert time.monotonic() < deadline
    mass.atomic_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check(); print(json.dumps({"status": "PASS_L02_CONTACT_LEDGER_SELF_CHECK"})); return
    if args.dry_check:
        print(json.dumps(dry_check())); return
    if args.output is None:
        parser.error("--output is required unless --self-check or --dry-check is used")
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        result = run(output)
    except BaseException as exc:
        failure = {"program": PROGRAM, "version": VERSION, "status": "STOP_L02_ORDERED_CONTACT_INPUT_LEDGER",
                   "script_sha256": digest(Path(__file__)), "error_type": type(exc).__name__, "error": str(exc)}
        mass.atomic_json(output / "failure.json", failure)
        raise
    print(json.dumps({"status": result["status"], "native_contacts": result["native_contacts"],
                      "excluded_source_vias": result["excluded_source_vias"], "output": result["output"],
                      "elapsed_s": result["elapsed_s"]}))


if __name__ == "__main__":
    main()
