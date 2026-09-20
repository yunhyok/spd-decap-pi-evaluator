"""Qualify source filled-via footprints and coincident contacts on L14."""
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from zipfile import ZipFile

from shapely.geometry import Point, box
from shapely.strtree import STRtree

from qualify_astra_l14_trace_centerlines import DIRECTORY, GEOMETRY_SHA, services, source
from spd_decap_pi._core.via_model import classify_via_conductor, SOLID_COPPER_FILLED_MICROVIA


def main():
    started = time.monotonic()
    data = (DIRECTORY / "l14-endpoint-polygon-census.json").read_bytes()
    census_sha = hashlib.sha256(data).hexdigest()
    assert census_sha == "82dcc35354101389509e87a8ef9eb050b0b47af50cb0d1a0156c26d1bd73a152"
    census = json.loads(data)
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        compressed = archive.read("attachments/geometry/0090-eb5c10758ed3e08d.spdgeom.zlib")
    assert hashlib.sha256(compressed).hexdigest() == GEOMETRY_SHA
    shape = services._ordered_spd_geometry(services._decode_spd_geometry_asset(GEOMETRY_SHA, compressed))
    islands = dict(services._spd_surface_islands(layer=source.PWR_LAYER, net=source.RAIL, asset_sha256=GEOMETRY_SHA, shape=shape))
    db = source._open_db(source.RAW_DB)
    db.row_factory = __import__("sqlite3").Row
    try:
        layers = [dict(row) for row in db.execute("SELECT layer_name,layer_kind,thickness_um FROM stackup_layers ORDER BY layer_ordinal")]
        layer_objects = [SimpleNamespace(name=r["layer_name"], is_conductor=r["layer_kind"] == "conductor", thickness_um=r["thickness_um"]) for r in layers]
        stacks, pads = {}, {}
        for sid in {r["padstack_id"] for r in census["boundary"]["endpoints"]}:
            rows = [dict(r) for r in db.execute("SELECT * FROM padstacks WHERE padstack_id_fold=?", (sid.casefold(),))]
            assert len(rows) == 1
            stacks[sid] = rows[0]
            rows = [dict(r) for r in db.execute("SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id_fold=?", (sid.casefold(), source.PWR_LAYER.casefold()))]
            assert len(rows) == 1 and rows[0]["shape_kind"] == "CIRCLE" and rows[0]["width_pm"] == rows[0]["height_pm"]
            pads[sid] = rows[0]
    finally:
        db.close()
    rows, groups = [], defaultdict(list)
    for row in census["boundary"]["endpoints"]:
        if time.monotonic() - started > 55:
            raise TimeoutError("via footprint audit exceeded 55s")
        stack, pad = stacks[row["padstack_id"]], pads[row["padstack_id"]]
        classification = classify_via_conductor(drill_diameter_um=stack["drill_diameter_pm"] / 1e6, padstack_material=stack["material"], start_layer=row["start_layer"], end_layer=row["end_layer"], stackup_layers=layer_objects)
        assert classification.conductor_model == SOLID_COPPER_FILLED_MICROVIA
        island = row["polygon_matches"][0]["island_id"]
        xy = tuple(row["l14_center_um"])
        point = Point(xy)
        polygon = islands[island]
        assert polygon.contains(point)
        clearance = point.distance(polygon.boundary)
        radius = stack["drill_diameter_pm"] / 2e6
        pad_radius = pad["width_pm"] / 2e6
        item = {"via_id": row["via_id"], "native_link_id": row["native_link"]["link_id"], "island_id": island,
                "xy_um": xy, "barrel_radius_um": radius, "pad_radius_um": pad_radius,
                "distance_to_artwork_boundary_um": clearance, "barrel_disk_fully_covered": clearance >= radius,
                "pad_disk_fully_covered": clearance >= pad_radius, "classification": asdict(classification)}
        rows.append(item)
        groups[(island, xy)].append(item)
    contacts = [{"island_id": island, "xy_um": xy, "via_ids": [r["via_id"] for r in members],
                 "barrel_radii_um": sorted({r["barrel_radius_um"] for r in members}),
                 "maximum_barrel_radius_um": max(r["barrel_radius_um"] for r in members)} for (island, xy), members in groups.items()]
    points = [Point(row["xy_um"]) for row in contacts]
    tree = STRtree(points)
    overlaps = []
    for i, contact in enumerate(contacts):
        x, y = contact["xy_um"]
        for j in tree.query(box(x - 60, y - 60, x + 60, y + 60)):
            j = int(j)
            if j <= i:
                continue
            if points[i].distance(points[j]) < contact["maximum_barrel_radius_um"] + contacts[j]["maximum_barrel_radius_um"]:
                overlaps.append([i, j])
    result = {
        "program": "SPD Decap PI Evaluator v0.23.1", "status": "COMPLETED_SOURCE_FILLED_VIA_FOOTPRINT_AUDIT",
        "census_sha256": census_sha, "geometry_asset_sha256": GEOMETRY_SHA,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "padstacks": stacks, "l14_pad_shapes": pads, "stackup_layers": layers,
        "counts": {"via_count": len(rows), "unique_island_center_count": len(contacts),
                   "barrel_disk_not_fully_covered": sum(not r["barrel_disk_fully_covered"] for r in rows),
                   "pad_disk_not_fully_covered": sum(not r["pad_disk_fully_covered"] for r in rows),
                   "center_multiplicity_histogram": dict(Counter(len(r["via_ids"]) for r in contacts)),
                   "noncoincident_barrel_overlap_pairs": len(overlaps)},
        "via_rows": rows, "coincident_contact_groups": contacts, "noncoincident_overlap_pairs": overlaps,
        "elapsed_s": time.monotonic() - started,
        "limitations": ["Geometric containment/classification only; source drill/pad radii do not prove uniform injection or equipotential finite electrodes.",
                        "Coincident vias require shared physical contact treatment; separate overlapping FEM electrodes would be invalid.",
                        "No sheet mesh, DC/AC response, source C distribution or replacement is generated."]}
    output = DIRECTORY / "l14-via-footprint-qualification.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, separators=(",", ":"), allow_nan=False)
        handle.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "counts", "elapsed_s")}))
    print("sha256=" + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    assert Point(0, 0).distance(box(-1, -1, 1, 1).boundary) == 1
    main()
