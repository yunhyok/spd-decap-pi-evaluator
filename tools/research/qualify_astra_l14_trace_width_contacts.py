"""Check 25 um source traces for connections omitted by endpoint-only graphs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
from zipfile import ZipFile

import shapely
from shapely.geometry import LineString, box
from shapely.strtree import STRtree

from qualify_astra_l14_trace_centerlines import DIRECTORY, TRACE_SHA, GEOMETRY_SHA, services, source


def main(output):
    started = time.monotonic()
    data = (DIRECTORY / "l14-source-traces.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == TRACE_SHA
    traces = json.loads(data)["records"]
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        compressed = archive.read("attachments/geometry/0090-eb5c10758ed3e08d.spdgeom.zlib")
    assert hashlib.sha256(compressed).hexdigest() == GEOMETRY_SHA
    shape = services._ordered_spd_geometry(services._decode_spd_geometry_asset(GEOMETRY_SHA, compressed))
    islands = services._spd_surface_islands(layer=source.PWR_LAYER, net=source.RAIL, asset_sha256=GEOMETRY_SHA, shape=shape)
    island_tree = STRtree([polygon for _, polygon in islands])
    for _, polygon in islands:
        shapely.prepare(polygon)
    lines = [LineString(t["endpoint_xy_um"]) for t in traces]
    # Flat caps are a conservative rectangular body. Round caps are an explicit
    # alternative because source pad/endcap semantics have not been certified.
    flat = [line.buffer(t["width_um"] / 2, cap_style="flat") for line, t in zip(lines, traces, strict=True)]
    line_tree = STRtree(lines)
    artwork = []
    contacts = []
    counts = Counter()
    completed = True
    processed = 0
    for i, trace in enumerate(traces):
        if time.monotonic() - started > 55:
            completed = False
            break
        radius = trace["width_um"] / 2
        endpoint_islands = set().union(*map(set, trace["endpoint_artwork_island_ids"]))
        for k in island_tree.query(lines[i].buffer(radius)):
            island_id, polygon = islands[int(k)]
            if island_id in endpoint_islands:
                continue
            if polygon.intersects(flat[i]) and polygon.intersection(flat[i]).area > 1e-9:
                kind = "flat_body_positive_area"
            elif polygon.distance(lines[i]) < radius - 1e-9:
                kind = "round_endcap_only"
            else:
                continue
            counts["new_artwork_" + kind] += 1
            artwork.append({"trace_id": trace["trace_id"], "island_id": island_id, "kind": kind})
        x0, y0, x1, y1 = lines[i].bounds
        for j in line_tree.query(box(x0 - 2 * radius, y0 - 2 * radius, x1 + 2 * radius, y1 + 2 * radius)):
            j = int(j)
            if j <= i or set(trace["source_node_ids"]) & set(traces[j]["source_node_ids"]):
                continue
            if not flat[i].intersects(flat[j]) and lines[i].distance(lines[j]) >= 2 * radius - 1e-9:
                continue
            overlap = flat[i].intersection(flat[j])
            if overlap.area > 1e-9:
                kind = "flat_body_positive_area"
                # Already-connected artwork contacts are recorded separately;
                # no endpoint node ID is silently substituted for geometry.
                covered = any(islands[int(k)][1].covers(overlap) for k in island_tree.query(overlap))
            else:
                kind = "round_endcap_only"
                round_overlap = lines[i].buffer(radius, quad_segs=32).intersection(lines[j].buffer(radius, quad_segs=32))
                covered = not round_overlap.is_empty and any(
                    islands[int(k)][1].covers(round_overlap) for k in island_tree.query(round_overlap))
            counts["trace_pair_" + kind + ("_covered_by_one_island" if covered else "_outside_or_unqualified")] += 1
            contacts.append({"trace_ids": [trace["trace_id"], traces[j]["trace_id"]],
                             "kind": kind, "contact_overlap_covered_by_one_artwork_island": covered,
                             "flat_overlap_area_um2": float(overlap.area)})
        processed += 1
    result = {
        "program": "SPD Decap PI Evaluator v0.23.1",
        "status": "COMPLETED_TRACE_WIDTH_CONTACT_AUDIT" if completed else "PARTIAL_TRACE_WIDTH_CONTACT_AUDIT_TIMEOUT",
        "trace_receipt_sha256": TRACE_SHA, "geometry_asset_sha256": GEOMETRY_SHA,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "processed_trace_count": processed, "source_trace_count": len(traces), "counts": dict(counts),
        "new_artwork_contacts": artwork, "trace_pairs_without_shared_source_endpoint": contacts,
        "elapsed_s": time.monotonic() - started,
        "limitations": ["Flat rectangles and round-endcap alternatives are explicit geometry interpretations, not a certified source pad/endcap model.",
                        "Positive areas/distances use 1e-9 um squared/um reporting thresholds, not manufacturing tolerances.",
                        "Contacts already covered by an ideal artwork island need no extra connectivity edge, but finite sheet losses remain absent.",
                        "No finite-width mesh, junction current sharing, R/L response, replacement or accuracy promotion is computed."]}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, separators=(",", ":"), allow_nan=False)
        handle.write("\n")
    print(json.dumps({key: result[key] for key in ("status", "processed_trace_count", "counts", "elapsed_s")}))
    print("receipt_sha256=" + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DIRECTORY / "l14-trace-width-contacts-02.json")
    args = parser.parse_args()
    a = LineString([(0, 0), (10, 0)]).buffer(1, cap_style="flat")
    b = LineString([(5, -5), (5, 5)]).buffer(1, cap_style="flat")
    assert abs(a.intersection(b).area - 4) < 1e-12
    assert a.disjoint(LineString([(11, 0), (20, 0)]).buffer(1, cap_style="flat"))
    main(args.output)
