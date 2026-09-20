"""Explain the 42 saved L02 incidence exceptions from owner and source records."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from zipfile import ZipFile

from shapely.geometry import Point
import query_astra_loaded_sheet_candidate as source
from inspect_astra_l02_source_inventory import read_db, COMPILED, RAW_DB, LAYER
from reconstruct_astra_native_loaded_field import _sha256_file, _atomic_exclusive_json

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = ROOT / "outputs/research/astra-l02-source-inventory-02/result.json"
PREVIOUS_SHA = "0af954600a5070f8f2fa27a81ba941889b46ac9f26ac2b76d8669f4ce3af93f2"


def main(output):
    started = time.monotonic(); deadline = started + 90
    assert _sha256_file(PREVIOUS) == PREVIOUS_SHA
    previous = json.loads(PREVIOUS.read_text(encoding="utf-8"))
    assert _sha256_file(COMPILED) == previous["inputs"]["compiled"]["sha256"]
    exceptions = previous["source_via_reconciliation"]["source_vias_outside_native_group"]
    owners = ["via:" + row["via_id_fold"] for row in exceptions]
    placeholders = ",".join("?" for _ in owners)
    with read_db(COMPILED, deadline) as db:
        links = [dict(row) for row in db.execute("SELECT lo.owner_id,l.* FROM link_owners lo JOIN links l ON l.kind=lo.kind AND l.ordinal=lo.link_ordinal WHERE lo.owner_id IN (" + placeholders + ")", owners)]
        surface = json.loads(db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0])
    found = {row["owner_id"] for row in links}
    absent = [row for row in exceptions if "via:" + row["via_id_fold"] not in found]
    assert len(exceptions) == 42 and len(found) == 40 and len(absent) == 2
    assert len({(row["kind"], row["ordinal"]) for row in links}) == 20
    nodes = {row[key].casefold() for row in absent for key in ("start_node_id", "end_node_id")}
    placeholders = ",".join("?" for _ in nodes); ids = sorted(nodes)
    with read_db(RAW_DB, deadline) as db:
        assert dict(db.execute("SELECT key,value FROM meta")) == previous["raw_index_meta"]
        adjacent_vias = [dict(row) for row in db.execute("SELECT * FROM vias WHERE start_node_id_fold IN (" + placeholders + ") OR end_node_id_fold IN (" + placeholders + ")", ids * 2)]
        adjacent_traces = [dict(row) for row in db.execute("SELECT * FROM traces WHERE start_node_id_fold IN (" + placeholders + ") OR end_node_id_fold IN (" + placeholders + ")", ids * 2)]
        endpoints = [dict(row) for row in db.execute("SELECT * FROM nodes WHERE node_id_fold IN (" + placeholders + ")", ids)]
    assert len(endpoints) == 4
    layers = {row["layer_id"] for row in endpoints}
    assets = [row for row in surface["geometry_assets"] if row["layer"] in layers and row["net"].casefold() == "dgnd"]
    asset_results = []
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        for asset in assets:
            data = archive.read("attachments/" + asset["asset"])
            assert hashlib.sha256(data).hexdigest() == asset["asset_sha256"]
            shape = source.core_services._ordered_spd_geometry(source.core_services._decode_spd_geometry_asset(asset["asset_sha256"], data))
            assert shape is not None and shape.is_valid and not shape.is_empty
            observations = []
            for row in endpoints:
                if row["layer_id"] != asset["layer"]:
                    continue
                point = Point(row["x_pm"] * 1e-6, row["y_pm"] * 1e-6)
                observations.append({"node_id": row["node_id"], "artwork_covers_center": bool(shape.covers(point)), "distance_to_artwork_um": float(shape.distance(point))})
            asset_results.append({"asset": asset, "compressed_bytes": len(data), "endpoint_observations": observations})
            assert time.monotonic() < deadline
    adjacent_owners = ["via:" + row["via_id_fold"] for row in adjacent_vias]
    with read_db(COMPILED, deadline) as db:
        adjacent_links = [dict(row) for row in db.execute("SELECT lo.owner_id,l.* FROM link_owners lo JOIN links l ON l.kind=lo.kind AND l.ordinal=lo.link_ordinal WHERE lo.owner_id IN (" + ",".join("?" for _ in adjacent_owners) + ")", adjacent_owners)]
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_VIA_EXCEPTION_SOURCE_OBSERVATIONS",
        "previous_sha256": PREVIOUS_SHA, "script_sha256": _sha256_file(Path(__file__)), "all_kind_owner_links": links, "absent_all_kind_owners": absent,
        "source_endpoint_nodes": endpoints, "source_adjacent_vias": adjacent_vias, "source_adjacent_traces": adjacent_traces, "compiled_adjacent_via_links": adjacent_links,
        "source_artwork_observations": asset_results, "elapsed_s": time.monotonic() - started,
        "limitations": ["Exact owner and source-node observations only; no parser replay, mesh, LU or model change.", "Center-to-artwork distance does not establish pad overlap, trace-interior contact, or physical magnetic irrelevance of a pruned stub."]}
    _atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "owner_links": len(links), "absent_owner_ids": [row["via_id"] for row in absent], "adjacent_traces": len(adjacent_traces), "geometry_assets": len(assets), "elapsed_s": result["elapsed_s"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(exist_ok=False); (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    main(output)
