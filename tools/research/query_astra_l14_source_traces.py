"""Bounded centerline-only census of source L14 traces; no conductor model."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from zipfile import ZipFile

import shapely
from shapely.geometry import Point
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from spd_decap_pi._core import services
import query_astra_loaded_sheet_candidate as source

OUTPUT = ROOT / "outputs/research/astra-step6e-loaded-boundary-01/l14-source-traces.json"
BOUNDARY = OUTPUT.parent / "loaded-sheet-candidate-final.json"
BOUNDARY_SHA = "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d"


def main():
    started = time.monotonic()

    def check():
        if time.monotonic() - started > 55:
            raise TimeoutError("source trace census exceeded 55 seconds")

    payload = BOUNDARY.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == BOUNDARY_SHA
    boundary = json.loads(payload)
    geometry = boundary["candidate_component"]["geometry"]
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        compressed = archive.read("attachments/" + geometry["member"])
    assert hashlib.sha256(compressed).hexdigest() == geometry["compressed_sha256"]
    shape = services._ordered_spd_geometry(
        services._decode_spd_geometry_asset(geometry["compressed_sha256"], compressed)
    )
    islands = services._spd_surface_islands(
        layer=source.PWR_LAYER, net=source.RAIL,
        asset_sha256=geometry["compressed_sha256"], shape=shape,
    )
    assert len(islands) == 110
    tree = STRtree([polygon for _, polygon in islands])
    for _, polygon in islands:
        shapely.prepare(polygon)
    connection = source._open_db(source.RAW_DB)
    connection.set_progress_handler(lambda: int(time.monotonic() - started > 55), 10000)
    try:
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        assert meta["source_sha256"] == source.EXPECTED_SOURCE_SHA256
        assert meta["logical_rows_sha256"] == "a49f447e34a48b220bfa5107ac8a520a5a4906745f1b715c91d584c0752b09bf"
        rows = connection.execute(
            "SELECT t.trace_id,t.owner_id,t.start_node_id,t.end_node_id,t.width_pm,"
            "t.width_status,t.geometry_status,t.source_record_sha256,"
            "a.x_pm,a.y_pm,b.x_pm,b.y_pm,a.source_record_sha256,b.source_record_sha256 "
            "FROM traces t LEFT JOIN nodes a ON a.node_id_fold=t.start_node_id_fold "
            "AND a.net_fold=t.net_fold AND a.layer_id_fold=t.layer_id_fold "
            "LEFT JOIN nodes b ON b.node_id_fold=t.end_node_id_fold "
            "AND b.net_fold=t.net_fold AND b.layer_id_fold=t.layer_id_fold "
            "WHERE t.net_fold=? AND t.layer_id_fold=? ORDER BY t.ordinal",
            (source.RAIL.casefold(), source.PWR_LAYER.casefold()),
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 12153
    counts = Counter()
    records = []
    for row in rows:
        check()
        assert row[4] == 25000000 and row[5:7] == ("EXACT", "EXACT")
        assert all(value is not None for value in row[8:])
        first, second = tuple(value * 1e-6 for value in row[8:10]), tuple(value * 1e-6 for value in row[10:12])
        length = ((second[0]-first[0])**2 + (second[1]-first[1])**2)**0.5
        assert length > 0
        endpoint_islands = []
        for xy in (first, second):
            point = Point(xy)
            endpoint_islands.append(sorted(
                islands[int(index)][0] for index in tree.query(point)
                if islands[int(index)][1].covers(point)
            ))
        bridge = len(endpoint_islands[0]) == len(endpoint_islands[1]) == 1 and endpoint_islands[0] != endpoint_islands[1]
        counts["distinct_singleton_endpoint_island_pair" if bridge else "other_endpoint_incidence"] += 1
        counts["endpoint_outside_artwork"] += sum(not values for values in endpoint_islands)
        records.append({
            "trace_id": row[0], "owner_id": row[1], "source_node_ids": row[2:4],
            "width_um": row[4] * 1e-6, "source_record_sha256": row[7],
            "endpoint_xy_um": [first, second], "endpoint_source_row_sha256": row[12:14],
            "endpoint_artwork_island_ids": endpoint_islands,
            "centerline_length_um": length,
        })
    result = {
        "program": "SPD Decap PI Evaluator v0.23.1",
        "status": "COMPLETED_SOURCE_TRACE_CENTERLINE_CENSUS_ONLY",
        "source_sha256": source.EXPECTED_SOURCE_SHA256,
        "raw_index_meta": meta, "boundary_receipt_sha256": BOUNDARY_SHA,
        "geometry_asset_sha256": geometry["compressed_sha256"],
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rail_id": source.RAIL, "layer": source.PWR_LAYER,
        "counts": dict(counts), "trace_count": len(records),
        "total_centerline_length_um": sum(row["centerline_length_um"] for row in records),
        "records": records, "elapsed_s": time.monotonic() - started,
        "limitations": [
            "A centerline intersection or coverage test is not a finite-width conductor/contact certificate.",
            "No current, resistance, inductance, finite-width union, mesh, native replacement or PowerSI fit is calculated.",
            "Trace interior coverage is not classified; endpoints on the same island do not prove the whole trace is covered by artwork.",
        ],
    }
    with OUTPUT.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        handle.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"records", "raw_index_meta", "limitations"}}))
    print("receipt_sha256=" + hashlib.sha256(OUTPUT.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
