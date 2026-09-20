"""Read saved source metadata for the observed L02 DGND group; no geometry decode."""
import argparse
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
from reconstruct_astra_native_loaded_field import _sha256_file, _atomic_exclusive_json

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
LAYER, TARGET = "Signal$L02(DGND)",349710
COMPONENT = "spd-surface-equivalence-component:d4691ffaac6a928e1ffad1a1"
COMPILED = R / "astra-step4-basis-01/indexes/compiled-topology.sqlite"
RAW_DB = R / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
PINS = {
    "compiled":(COMPILED,"5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"),
    "raw_field":(R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz","6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "current_census":(R / "astra-100mhz-source-current-census-01/result.json","a6ad22731a706e24b200cc674d2967813d51c49dfeff693370b99b93555ef796"),
}


def read_db(path,deadline):
    db = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON"); db.execute("PRAGMA trusted_schema=OFF")
    db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    return db


def main(output,reconcile_vias=False):
    started = time.monotonic(); deadline = started+60
    inputs = {}
    for name,(path,expected) in PINS.items():
        assert _sha256_file(path) == expected,name
        inputs[name] = {"path":str(path),"sha256":expected}
    census = json.loads(PINS["current_census"][0].read_text(encoding="utf-8"))
    group = next(row for row in census["groups"] if row["active_index"] == TARGET)
    assert group["source_components"][0]["component_id"] == COMPONENT
    with read_db(COMPILED,deadline) as db:
        surface = json.loads(db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0])
    components = [row for row in surface["surface_equivalence_components"] if row["layer"] == LAYER and row["net"].casefold() == "dgnd"]
    selected = next(row for row in components if row["component_id"] == COMPONENT)
    islands = set(selected["island_ids"])
    assets = [row for row in surface["geometry_assets"] if row["layer"] == LAYER and row["net"].casefold() == "dgnd" and islands.intersection(row["island_ids"])]
    assert set().union(*(set(row["island_ids"]) for row in assets)) >= islands
    with np.load(PINS["raw_field"][0],allow_pickle=False) as raw:
        surface_ids = json.loads(raw["surface_node_ids"].tobytes())
        positions = np.array([i for i,node in enumerate(surface_ids) if node in islands])
        active = raw["global_to_active_indices"][raw["surface_to_reduced_indices"][positions]]
        assert len(positions) == len(islands) and np.all(active == TARGET)
        first,second = raw["finite_first_active_indices"],raw["finite_second_active_indices"]
        native_count = int(np.count_nonzero((first == TARGET)^(second == TARGET)))
        assert native_count == group["dc"]["link_count"]
        if reconcile_vias:
            original = raw["finite_active_original_indices"]
            owner_rows = json.loads(raw["all_finite_link_owner_ids_json"].tobytes())
            native_vias = set()
            for index in np.flatnonzero((first == TARGET)^(second == TARGET)):
                owners = json.loads(owner_rows[int(original[index])])
                assert len(owners) == 1 and owners[0].startswith("via:")
                native_vias.add(owners[0].split(":",1)[1].casefold())
            assert len(native_vias) == native_count
    with read_db(RAW_DB,deadline) as db:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        assert meta["source_sha256"] == "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
        assert meta["logical_rows_sha256"] == "a49f447e34a48b220bfa5107ac8a520a5a4906745f1b715c91d584c0752b09bf"
        copper = [dict(row) for row in db.execute("SELECT * FROM stackup_layers WHERE layer_name=?",(LAYER,))]
        traces = db.execute("SELECT count(*) FROM traces WHERE net_fold=? AND layer_id_fold=?",("dgnd",LAYER.casefold())).fetchone()[0]
        vias = db.execute("SELECT count(*) FROM vias WHERE net_fold=? AND (start_layer_id_fold=? OR end_layer_id_fold=?)",("dgnd",LAYER.casefold(),LAYER.casefold())).fetchone()[0]
        if reconcile_vias:
            rows = [dict(row) for row in db.execute("SELECT * FROM vias WHERE net_fold=? AND (start_layer_id_fold=? OR end_layer_id_fold=?)",("dgnd",LAYER.casefold(),LAYER.casefold()))]
            source_vias = {row["via_id_fold"] for row in rows}
            assert native_vias <= source_vias
            missing = [row for row in rows if row["via_id_fold"] not in native_vias]
    if reconcile_vias:
        owner_ids = ["via:"+row["via_id_fold"] for row in missing]
        with read_db(COMPILED,deadline) as db:
            links = [dict(row) for row in db.execute("SELECT lo.owner_id,l.ordinal,l.link_id,l.parallel_count FROM link_owners lo JOIN links l ON l.kind=lo.kind AND l.ordinal=lo.link_ordinal WHERE lo.kind=1 AND lo.owner_id IN ("+",".join("?" for _ in owner_ids)+")",owner_ids)] if owner_ids else []
        for link in links:
            matches = np.flatnonzero(original == link["ordinal"])
            assert len(matches) <= 1
            link["captured_active_finite_index"] = int(matches[0]) if len(matches) else None
            if len(matches):
                link["first_active_index"],link["second_active_index"] = int(first[matches[0]]),int(second[matches[0]])
    assert len(copper) == 1 and time.monotonic()<deadline
    result = {"program":"SPD Decap PI Evaluator","version":"0.23.1","status":"COMPLETED_OBSERVED_L02_SOURCE_METADATA_INVENTORY",
        "inputs":inputs,"raw_index_meta":meta,"active_index":TARGET,"layer":LAYER,"component":selected,"layer_net_component_count":len(components),
        "material":copper[0],"geometry_assets":assets,"native_incident_finite_links":native_count,"whole_layer_net_source_trace_count":traces,"whole_layer_net_source_via_count":vias,
        "observed_100mhz_finite_throughput_a":{name:group[name]["half_sum_abs_a"] for name in ("dc","internal")},
        "elapsed_s":time.monotonic()-started,"script_sha256":_sha256_file(Path(__file__)),
        "limitations":["Compiled asset metadata and raw SQLite counts only; no geometry decoded, meshed, joined or replayed.",
            "The observed component is not a unique magnetic return. Whole-layer raw counts are not yet individually bound to its native endpoints.",
            "Source contact/trace unions, finite-via R/L ownership, GC replacement and a conforming current basis remain to be established before magnetic coupling."]}
    if reconcile_vias:
        result["source_via_reconciliation"] = {"native_via_owner_count":len(native_vias),"source_via_count":len(source_vias),"source_vias_outside_native_group":missing,"matching_compiled_finite_links":links}
        result["limitations"][1] = "The observed component is not a unique magnetic return. Source vias outside the native incident group and their compiled links are recorded without assuming they are parser errors."
    _atomic_exclusive_json(output / "result.json",result)
    print(json.dumps({"status":result["status"],"islands":len(islands),"assets":len(assets),"native_links":native_count,"whole_layer_traces":traces,"whole_layer_vias":vias,"material":copper,"elapsed_s":result["elapsed_s"]}))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output",type=Path,required=True); parser.add_argument("--reconcile-vias",action="store_true"); args=parser.parse_args()
    output=args.output.resolve(); output.mkdir(exist_ok=False); (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    main(output,args.reconcile_vias)
