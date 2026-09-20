"""Find artwork contacts inside saved source trace centerlines, without meshing."""

import hashlib
import json
from pathlib import Path
import sys
import time
from zipfile import ZipFile

import shapely
from shapely.geometry import LineString
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from spd_decap_pi._core import services
import query_astra_loaded_sheet_candidate as source

DIRECTORY = ROOT / "outputs/research/astra-step6e-loaded-boundary-01"
TRACE_SHA = "eec49e0a67fc31f6da1acfa1b28a573e820c5fb9ba0d805a926b7d8426790d7e"
GEOMETRY_SHA = "eb5c10758ed3e08d19db505d10e3efe35d1c764145fbe29d457719075b2c7838"


def main():
    started = time.monotonic()
    payload = (DIRECTORY / "l14-source-traces.json").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == TRACE_SHA
    traces = json.loads(payload)["records"]
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        compressed = archive.read("attachments/geometry/0090-eb5c10758ed3e08d.spdgeom.zlib")
    assert hashlib.sha256(compressed).hexdigest() == GEOMETRY_SHA
    shape = services._ordered_spd_geometry(services._decode_spd_geometry_asset(GEOMETRY_SHA, compressed))
    islands = services._spd_surface_islands(layer=source.PWR_LAYER, net=source.RAIL, asset_sha256=GEOMETRY_SHA, shape=shape)
    for _, polygon in islands:
        shapely.prepare(polygon)
    tree = STRtree([polygon for _, polygon in islands])
    results = []
    completed = True
    for trace in traces:
        if time.monotonic() - started > 55:
            completed = False
            break
        line = LineString(trace["endpoint_xy_um"])
        endpoint_ids = set().union(*map(set, trace["endpoint_artwork_island_ids"]))
        intersections = []
        for index in tree.query(line):
            island_id, polygon = islands[int(index)]
            if not polygon.intersects(line):
                continue
            if polygon.covers(line):
                intersection = line
            else:
                intersection = line.intersection(polygon)
            if not intersection.is_empty:
                intersections.append((island_id, intersection))
        covered = shapely.union_all([part for _, part in intersections]) if intersections else None
        inside_length = 0.0 if covered is None else float(covered.length)
        assert 0 <= inside_length <= line.length * (1 + 1e-10)
        missing_ids = sorted(island_id for island_id, part in intersections if part.length > 1e-9 and island_id not in endpoint_ids)
        results.append({
            "trace_id": trace["trace_id"],
            "owner_id": trace["owner_id"],
            "centerline_length_um": float(line.length),
            "inside_artwork_length_um": inside_length,
            "outside_artwork_length_um": max(0.0, float(line.length)-inside_length),
            "positive_length_artwork_contacts": sorted(island_id for island_id, part in intersections if part.length > 1e-9),
            "artwork_contacts_absent_at_both_endpoints": missing_ids,
        })
    result = {
        "program": "SPD Decap PI Evaluator v0.23.1",
        "status": "COMPLETED_CENTERLINE_ARTWORK_CONTACTS_ONLY" if completed else "PARTIAL_CENTERLINE_ARTWORK_CONTACTS_TIME_LIMIT",
        "trace_receipt_sha256": TRACE_SHA, "geometry_asset_sha256": GEOMETRY_SHA,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_trace_count": len(traces), "processed_trace_count": len(results),
        "new_interior_artwork_contact_trace_count": sum(bool(row["artwork_contacts_absent_at_both_endpoints"]) for row in results),
        "total_processed_outside_artwork_centerline_length_um": sum(row["outside_artwork_length_um"] for row in results),
        "records": results, "elapsed_s": time.monotonic()-started,
        "limitations": ["Centerline only; 25um finite-width bodies, trace-trace contacts, endcaps, bends, pads, and current injection are not qualified.", "Positive-length contacts use a 1e-9um numerical reporting threshold; this is not a conductor-clearance tolerance.", "No R/L, field solve, source replacement or PowerSI accuracy result is produced."],
    }
    output = DIRECTORY / "l14-trace-centerline-contacts.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, allow_nan=False, separators=(",", ":"))
        handle.write("\n")
    print(json.dumps({key:value for key,value in result.items() if key not in {"records","limitations"}}))
    print("receipt_sha256="+hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
