"""Preserve every L02 partial-0 C edge and its exact source artwork identity."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
import audit_astra_l25_gc_projection as gc

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
TARGET = 349710
ISLAND = "spd-surface-island:150e4b6ef515db4afd92fc34"
PINS = {
    "nonvia": (R / "astra-l02-nonvia-boundary-04/result.json", "d58c3f55566a3299f94db68ba48d5b56123a2ea38c72a3cf13585080750442b2"),
    "raw": (R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz", "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "compiled": (R / "astra-step4-basis-01/indexes/compiled-topology.sqlite", "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"),
    "helper": (Path(gc.__file__), "0c6f2beeea2c1359786eea5bb288b9eff2573a99941b81afc7cac9f79de046af"),
}


def main(output):
    started = time.monotonic()
    for name, (path, expected) in PINS.items():
        assert gc.sha256_file(path) == expected, name
    prior = json.loads(PINS["nonvia"][0].read_bytes())
    assert prior["status"] == "COMPLETED_L02_SAVED_NONVIA_BOUNDARY" and prior["active_index"] == TARGET
    assert len(prior["source_partials"]) == 1 and prior["source_partials"][0]["ordinal"] == 0
    entries = prior["source_partials"][0]["directed_target_row_entries"]
    offdiag = {e["source_column_id"]: e for e in entries if e["column_active_index"] != TARGET}
    assert len(entries) == 2065 and len(offdiag) == 2064 and {e["source_row_id"] for e in entries} == {ISLAND}
    with gc.open_readonly_db(PINS["compiled"][0]) as db:
        db.set_progress_handler(lambda: int(time.monotonic() - started > 60), 10000)
        surface = json.loads(db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0])
    component, metadata = gc.target_component(surface, ISLAND)
    wanted = set(offdiag) | {ISLAND}
    assets = {}
    for asset in surface["geometry_assets"]:
        for island in wanted.intersection(asset["island_ids"]):
            assert island not in assets, island
            assets[island] = {k: asset[k] for k in ("asset", "asset_sha256", "layer", "net")}
    assert set(assets) == wanted
    assert component["layer"] == "Signal$L02(DGND)" and component["net"].casefold() == "dgnd"
    owners = []
    with np.load(PINS["raw"][0], allow_pickle=False) as raw:
        names = gc.string_vector(raw["partial_00_net_names"], label="partial0 names")
        index = names.index(ISLAND)
        upper = gc.scalar_text(raw["partial_00_upper_layer"], label="upper")
        lower = gc.scalar_text(raw["partial_00_lower_layer"], label="lower")
        assert (upper, lower) == ("Signal$TOP", "Signal$L02(DGND)")
        separation = float(raw["partial_00_separation_m"][0])
        epsilon = float(raw["partial_00_nominal_relative_permittivity"][0])
        edges, matrix_checks = gc.csc_edges(raw, "partial_00", names)
        all_edges = list(edges)
        incident = [(i, j, value) for i, j, value in all_edges if index in (i, j)]
        assert len(incident) == len(offdiag)
        for i, j, value in incident:
            external = names[j if i == index else i]
            row = offdiag[external]
            assert value == -row["nominal_c_f"] and row["retained_native"]
            assert metadata[external]["layer"] == "Signal$TOP"
            identity = {"source_sha256": gc.SOURCE_SHA256, "partial_ordinal": 0, "upper_layer": upper, "lower_layer": lower, "target_island_id": ISLAND, "external_island_id": external, "nominal_capacitance_f_hex": value.hex()}
            owners.append({"fingerprint": gc.canonical_hash(identity), **identity, "nominal_capacitance_f": value, "external_active_index": row["column_active_index"], "external_source_component": metadata[external], "external_geometry_asset": assets[external]})
    assert len({o["fingerprint"] for o in owners}) == 2064 and time.monotonic() - started < 60
    unique_assets = {o["external_geometry_asset"]["asset"]: o["external_geometry_asset"] for o in owners}
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_GC_SOURCE_EDGE_ASSET_LEDGER", "active_index": TARGET,
              "script_sha256": gc.sha256_file(Path(__file__)), "inputs": {name: {"path": str(path), "sha256": expected} for name, (path, expected) in PINS.items()},
              "source_sha256": gc.SOURCE_SHA256, "target_island_id": ISLAND, "target_geometry_asset": assets[ISLAND], "partial_ordinal": 0,
              "upper_layer": upper, "lower_layer": lower, "separation_m": separation, "nominal_relative_permittivity": epsilon,
              "matrix_checks": matrix_checks, "retained_nonincident_source_edge_count": len(all_edges) - len(incident),
              "source_owner_edge_count": len(owners), "nominal_capacitance_total_f": sum(o["nominal_capacitance_f"] for o in owners),
              "external_active_node_count": len({o["external_active_index"] for o in owners}), "external_asset_count": len(unique_assets),
              "owner_fingerprint_set_sha256": gc.canonical_hash(sorted(o["fingerprint"] for o in owners)), "owners": sorted(owners, key=lambda o: o["fingerprint"]),
              "external_assets": sorted(unique_assets.values(), key=lambda a: a["asset"]), "elapsed_s": time.monotonic() - started,
              "scope": "Original source C edges and exact cached artwork metadata, before active-node alias aggregation. No geometry decode, overlap integration, mesh, G/C projection or changed-board result. Preserve all2064 owners and original frequency scales; later geometry weights must reproduce their totals and external couplings exactly."}
    gc.atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({k: result[k] for k in ("status", "source_owner_edge_count", "external_active_node_count", "external_asset_count", "nominal_capacitance_total_f", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    main(output)
