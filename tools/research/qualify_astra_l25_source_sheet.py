"""Bind the observed L25 bypass to its saved source copper and 350 via ends."""
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace
from zipfile import ZipFile

import numpy as np
import shapely
from shapely.geometry import Point
from shapely.strtree import STRtree

import query_astra_loaded_sheet_candidate as source
from reconstruct_astra_native_loaded_field import _sha256_file, _atomic_exclusive_json
from spd_decap_pi._core.via_model import classify_via_conductor

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-l25-source-sheet-01"
LAYER = "Signal$L25(MAIN_POWER4)"
TARGET = 258027
ASSET = "geometry/0247-c80867ceb7f82d7b.spdgeom.zlib"
ASSET_SHA = "c80867ceb7f82d7ba4f886c63c68dbdc88c55fc3c1d9cc703d8fdea4a25ca711"
ISLAND = "spd-surface-island:279609d3a8c5969250977818"


def materialize_gc_assets():
    inventory_path = OUT / "l25-gc-projection-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    targets = inventory["original_gc"]["external_surface_reduced_active_terminals"]
    assert len(targets) == 4
    assert _sha256_file(source.COMPILED_DB) == "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"
    with source._open_db(source.COMPILED_DB) as db:
        surface = json.loads(db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0])
    directory = OUT / "source-gc-assets"
    directory.mkdir(exist_ok=False)
    records = []
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        for target in targets:
            matches = [r for r in surface["geometry_assets"] if target["external_island_id"] in r["island_ids"] and r["layer"] == target["external_layer"] and r["net"].casefold() == target["external_net"].casefold()]
            assert len(matches) == 1
            record = matches[0]
            member = "attachments/" + record["asset"]
            path = directory / Path(record["asset"]).name
            with path.open("xb") as handle:
                handle.write(archive.read(member))
            assert _sha256_file(path) == record["asset_sha256"]
            records.append({"external_island_id": target["external_island_id"],
                "layer": record["layer"], "net": record["net"], "archive_member": member,
                "path": str(path), "sha256": record["asset_sha256"], "size_bytes": path.stat().st_size})
    receipt = {"program": source.PROGRAM, "version": source.VERSION,
        "status": "MATERIALIZED_EXACT_L25_GC_EXTERNAL_GEOMETRY_MEMBERS",
        "script_sha256": _sha256_file(Path(__file__)), "inventory_sha256": _sha256_file(inventory_path),
        "source_sha256": source.EXPECTED_SOURCE_SHA256, "members": records,
        "scope": "Four exact ZIP members verified against compiled source assets; no scenario decode or geometry integration."}
    _atomic_exclusive_json(directory / "receipt.json", receipt)
    print(json.dumps(receipt))


def main():
    started = time.monotonic()
    OUT.mkdir(exist_ok=False)
    (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    pins = {
        ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz": "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
        source.COMPILED_DB: "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b",
    }
    for path, expected in pins.items():
        assert _sha256_file(path) == expected, path.name
    with source._open_db(source.COMPILED_DB) as db:
        surface = json.loads(db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0])
        components = [r for r in surface["surface_equivalence_components"] if r["layer"] == LAYER and r["net"] == source.RAIL.casefold()]
        assets = [r for r in surface["geometry_assets"] if r["layer"] == LAYER and r["net"].casefold() == source.RAIL.casefold()]
        assert len(components) == len(assets) == 1
        assert components[0]["island_ids"] == [ISLAND] and components[0]["contact_status"] == "complete"
        assert assets[0]["asset"] == ASSET and assets[0]["asset_sha256"] == ASSET_SHA
        with np.load(next(iter(pins)), allow_pickle=False) as raw:
            surface_ids = json.loads(raw["surface_node_ids"].tobytes())
            global_index = int(raw["surface_to_reduced_indices"][surface_ids.index(ISLAND)])
            assert raw["global_to_active_indices"][global_index] == TARGET
            first, second = raw["finite_first_active_indices"], raw["finite_second_active_indices"]
            incidence = np.flatnonzero((first == TARGET) ^ (second == TARGET))
            assert len(incidence) == 350
            all_ids = json.loads(raw["all_finite_link_ids"].tobytes())
            original = raw["finite_active_original_indices"]
            native = {}
            for index in incidence:
                ordinal = int(original[index])
                link = db.execute("SELECT link_id,parallel_count,resistance_ohm,inductance_h FROM links WHERE kind=1 AND ordinal=?", (ordinal,)).fetchone()
                assert link and link[0] == all_ids[ordinal]
                owners = db.execute("SELECT owner_id FROM link_owners WHERE kind=1 AND link_ordinal=?", (ordinal,)).fetchall()
                assert len(owners) == 1 and owners[0][0].startswith("via:") and link[1] == 1
                via_id = owners[0][0].split(":", 1)[1]
                assert via_id not in native
                native[via_id] = {"native_link_id": link[0], "active_finite_index": int(index),
                    "compiled_link_ordinal": ordinal, "first_active_index": int(first[index]),
                    "second_active_index": int(second[index]), "native_count": link[1],
                    "native_resistance_ohm": float(raw["finite_resistance_ohm_per_via"][index]),
                    "native_inductance_h": float(raw["finite_inductance_h_per_via"][index])}

    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        compressed = archive.read("attachments/" + ASSET)
    asset_path = OUT / Path(ASSET).name
    asset_path.write_bytes(compressed)
    assert _sha256_file(asset_path) == ASSET_SHA
    shape = source.core_services._ordered_spd_geometry(source.core_services._decode_spd_geometry_asset(ASSET_SHA, compressed))
    assert shape.is_valid and shape.geom_type == "Polygon"
    islands = source.core_services._spd_surface_islands(layer=LAYER, net=source.RAIL, asset_sha256=ASSET_SHA, shape=shape)
    assert len(islands) == 1 and islands[0][0] == ISLAND
    shapely.prepare(shape)

    with source._open_db(source.RAW_DB) as db:
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic()-started > 90), 10000)
        meta = dict(db.execute("SELECT key,value FROM meta"))
        assert meta["source_sha256"] == source.EXPECTED_SOURCE_SHA256
        assert meta["logical_rows_sha256"] == "a49f447e34a48b220bfa5107ac8a520a5a4906745f1b715c91d584c0752b09bf"
        trace_count = db.execute("SELECT count(*) FROM traces WHERE net_fold=? AND layer_id_fold=?", (source.RAIL.casefold(), LAYER.casefold())).fetchone()[0]
        assert trace_count == 0, "Additional source trace geometry must be included before meshing"
        rows = [dict(r) for r in db.execute("SELECT * FROM vias WHERE net_fold=? AND (start_layer_id_fold=? OR end_layer_id_fold=?) ORDER BY ordinal", (source.RAIL.casefold(), LAYER.casefold(), LAYER.casefold()))]
        assert len(rows) == len(native) and {r["via_id_fold"] for r in rows} == set(native)
        layers = [dict(r) for r in db.execute("SELECT * FROM stackup_layers ORDER BY layer_ordinal")]
        objects = [SimpleNamespace(name=r["layer_name"], is_conductor=r["layer_kind"] == "conductor", thickness_um=r["thickness_um"]) for r in layers]
        copper = [r for r in layers if r["layer_name"] == LAYER]
        assert len(copper) == 1 and copper[0]["conductivity_s_per_m"] > 0 and copper[0]["thickness_um"] > 0
        stacks, pads = {}, {}
        for sid in sorted({r["padstack_id_fold"] for r in rows}):
            stack = [dict(r) for r in db.execute("SELECT * FROM padstacks WHERE padstack_id_fold=?", (sid,))]
            pad = [dict(r) for r in db.execute("SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id_fold=?", (sid, LAYER.casefold()))]
            assert len(stack) == len(pad) == 1 and pad[0]["shape_kind"] == "CIRCLE" and pad[0]["width_pm"] == pad[0]["height_pm"]
            stacks[sid], pads[sid] = stack[0], pad[0]

    via_rows, groups = [], defaultdict(list)
    for row in rows:
        assert time.monotonic()-started < 90
        assert row["status"] == "EXACT"
        assert (row["start_layer_id"] == LAYER) ^ (row["end_layer_id"] == LAYER)
        prefix = "start" if row["start_layer_id"] == LAYER else "end"
        xy = (row[prefix+"_x_pm"] * 1e-6, row[prefix+"_y_pm"] * 1e-6)
        assert row["start_x_pm"] == row["end_x_pm"] and row["start_y_pm"] == row["end_y_pm"]
        stack, pad = stacks[row["padstack_id_fold"]], pads[row["padstack_id_fold"]]
        radius, pad_radius = stack["drill_diameter_pm"] / 2e6, pad["width_pm"] / 2e6
        point = Point(xy)
        assert shape.contains(point), row["via_id"]
        clearance = point.distance(shape.boundary)
        assert clearance >= radius > 0
        classification = classify_via_conductor(drill_diameter_um=radius*2, padstack_material=stack["material"], start_layer=row["start_layer_id"], end_layer=row["end_layer_id"], stackup_layers=objects)
        entry = {**native[row["via_id_fold"]], "via_id": row["via_id"], "island_id": ISLAND,
            "xy_um": xy, "barrel_radius_um": radius, "pad_radius_um": pad_radius,
            "distance_to_artwork_boundary_um": clearance, "classification": asdict(classification),
            "source_via": row}
        via_rows.append(entry)
        groups[xy].append(entry)
    contacts = [{"island_id": ISLAND, "xy_um": xy, "via_ids": sorted(r["via_id"] for r in members),
        "barrel_radii_um": sorted({r["barrel_radius_um"] for r in members}),
        "maximum_barrel_radius_um": max(r["barrel_radius_um"] for r in members)} for xy, members in groups.items()]
    points = [Point(r["xy_um"]) for r in contacts]
    tree = STRtree(points)
    maximum_radius = max(r["maximum_barrel_radius_um"] for r in contacts)
    for i, contact in enumerate(contacts):
        for j in tree.query(points[i], predicate="dwithin", distance=contact["maximum_barrel_radius_um"]+maximum_radius):
            if int(j) > i:
                assert points[i].distance(points[int(j)]) >= contact["maximum_barrel_radius_um"]+contacts[int(j)]["maximum_barrel_radius_um"]
    domain_path = OUT / "l25-source-domain.wkb"
    domain_path.write_bytes(shapely.to_wkb(shapely.normalize(shape)))
    result = {"program": source.PROGRAM, "version": source.VERSION,
        "status": "COMPLETED_CONDITIONAL_L25_SOURCE_SHEET_BOUNDARY", "script_sha256": _sha256_file(Path(__file__)),
        "inputs": {str(path): expected for path, expected in pins.items()}, "raw_index_meta": meta,
        "rail_id": source.RAIL, "layer": LAYER, "component": components[0], "geometry_asset": assets[0],
        "global_reduced_index": global_index, "active_index": TARGET, "material": copper[0],
        "native_boundary_count": len(via_rows), "source_trace_count": trace_count,
        "geometry": {"hole_count": len(shape.interiors), "coordinate_count": int(shapely.get_num_coordinates(shape)),
            "area_um2": shape.area, "bounds_um": list(shape.bounds), "domain_sha256": _sha256_file(domain_path)},
        "padstacks": stacks, "pad_shapes": pads, "via_rows": via_rows, "coincident_contact_groups": contacts,
        "classification_counts": dict(Counter(r["classification"]["conductor_model"] for r in via_rows)),
        "elapsed_s": time.monotonic()-started,
        "limitations": ["Drill-radius equipotential contacts are conditional sheet boundary inputs; plated classification does not certify plating thickness or current injection.",
            "Native via R/L is retained exactly. No added trace exists on this source net/layer. Source G/C owner partition is separate.",
            "No meshing, new global solve, magnetic coupling, physical convergence or accuracy promotion."]}
    _atomic_exclusive_json(OUT / "receipt.json", result)
    print(json.dumps({k: result[k] for k in ("status", "native_boundary_count", "source_trace_count", "geometry", "classification_counts", "elapsed_s")}))
    print(json.dumps({"receipt_sha256": _sha256_file(OUT / "receipt.json")}))


if __name__ == "__main__":
    if sys.argv[1:] == ["--gc-assets"]:
        materialize_gc_assets()
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit("Usage: qualify_astra_l25_source_sheet.py [--gc-assets]")
