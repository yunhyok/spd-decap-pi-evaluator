"""Recover exact source paths and R/L for20 composites containing excluded L02 vias."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import numpy as np
import project_astra_l14_gc_mass as mass
from spd_decap_pi._core import via_model

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
L02 = "Signal$L02(DGND)"
PINS = {
    "inventory": (R / "astra-l02-source-inventory-02/result.json", "0af954600a5070f8f2fa27a81ba941889b46ac9f26ac2b76d8669f4ce3af93f2"),
    "compiled_db": (R / "astra-step4-basis-01/indexes/compiled-topology.sqlite", "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"),
    "raw_db": (R / "astra-step4-basis-01/indexes/raw-spatial.sqlite", "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7"),
    "raw_field": (R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz", "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "native_source_builder": (ROOT / "src/spd_decap_pi/_core/io/spd.py", "fc17618801367294d1d2c1eabcef1d54db2603cfe88e2d6bfc191a3b484761fe"),
    "raw_scope_compiler": (ROOT / "src/spd_decap_pi/raw_spatial_contact_compiler.py", "a1d83f96cfda9fe62e1f30cfec5868596b752dd17a447c000b18ddb63dfb5c7b"),
    "via_model": (Path(via_model.__file__), "d9b1acb96e2ff5137f502fdf0d24a6e9ba2e16e161e67a84a0b40b526b2b4043"),
    "persistence_helper": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
# Exact raw trace rows found in the preserved run01 path-connectivity diagnosis.
# These are native ideal links for ordering, not additional finite trace R/L.
BRIDGES = {
    186263: [(309314, "f6e80f4547fa33ada7118fc9e9f0e5ba29f90d2482293e84c47ee75f7702577b")],
    754981: [(311661, "0ca612b193eab1a2068d76a13dbde6805f08c896ad731184bdefb275348f2190"),
            (311662, "97c7c46a7309cf17cb21980a06bf8b43e3e2eef237d7c6f5b9b9487ab607dbcb")],
    1521556: [(455779, "e2b7553a56fe83387afaad7915ac5a12745f9edff488993e6b3da55c37a586e8")],
    1690188: [(454098, "8a1bc2aab985aed8750051570f32ba7529463a73bbc6d8bbf575b3d3a3f887ac")],
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def native_digest(values):
    # Exact native spd.py finite_digest / ordered-owner digest byte convention.
    digest = hashlib.sha256()
    for value in values:
        encoded = str(value).casefold().encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def paths(vias):
    incident = defaultdict(list)
    for i, row in enumerate(vias):
        for key in ("start_node_id_fold", "end_node_id_fold"):
            incident[row[key]].append(i)
    ends = sorted(node for node, edges in incident.items() if len(edges) == 1)
    assert len(ends) == 2 and all(len(edges) <= 2 for edges in incident.values())
    results = []
    for start in ends:
        nodes, order, used = [start], [], set()
        while len(order) < len(vias):
            remaining = [i for i in incident[nodes[-1]] if i not in used]
            assert len(remaining) == 1
            index = remaining[0]
            row = vias[index]
            other = row["end_node_id_fold"] if row["start_node_id_fold"] == nodes[-1] else row["start_node_id_fold"]
            used.add(index); order.append(index); nodes.append(other)
        assert nodes[-1] == next(node for node in ends if node != start)
        results.append((order, nodes))
    return results


def self_check():
    rows = [{"start_node_id_fold": a, "end_node_id_fold": b} for a, b in (("b", "c"), ("a", "b"), ("d", "c"))]
    result = paths(rows)
    assert result == [([1, 0, 2], ["a", "b", "c", "d"]), ([2, 0, 1], ["d", "c", "b", "a"])]
    assert native_digest(["VIA1", "via2"]) == native_digest(["via1", "VIA2"])
    assert native_digest(["via1", "via2"]) != native_digest(["via2", "via1"])
    # A source ideal trace bridges two otherwise disconnected source-via paths.
    disconnected = [rows[1], rows[2]]
    try:
        paths(disconnected)
    except AssertionError:
        pass
    else:
        raise AssertionError("disconnected source vias accepted")
    with_bridge = [*disconnected, rows[0]]
    order, _ = paths(with_bridge)[0]
    assert [index for index in order if index < 2] == [0, 1]


def db_open(path, started):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    db.set_progress_handler(lambda: int(time.monotonic() - started > 60), 10000)
    return db


def main(output):
    started = time.monotonic()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    inventory = json.loads(PINS["inventory"][0].read_bytes())
    ordinals = sorted({row["ordinal"] for row in inventory["source_via_reconciliation"]["matching_compiled_finite_links"]})
    excluded_ids = {row["via_id_fold"] for row in inventory["source_via_reconciliation"]["source_vias_outside_native_group"]}
    assert len(ordinals) == 20 and len(excluded_ids) == 42
    links = []
    with db_open(PINS["compiled_db"][0], started) as db:
        for ordinal in ordinals:
            row = dict(db.execute("SELECT * FROM links WHERE kind=1 AND ordinal=?", (ordinal,)).fetchone())
            row["owners_sorted"] = [item[0] for item in db.execute("SELECT owner_id FROM link_owners WHERE kind=1 AND link_ordinal=? ORDER BY owner_ordinal", (ordinal,))]
            # nodes uses one flattened kind0/1 ordering, as the pinned serializer does.
            row["compiled_endpoints"] = []
            for index in (row["first_node"], row["second_node"]):
                row["compiled_endpoints"].append(db.execute("SELECT node_id FROM nodes ORDER BY kind,ordinal LIMIT 1 OFFSET ?", (index,)).fetchone()[0])
            links.append(row)
    with db_open(PINS["raw_db"][0], started) as db:
        layers = [dict(row) for row in db.execute("SELECT * FROM stackup_layers ORDER BY layer_ordinal")]
        layer_objects = [SimpleNamespace(name=row["layer_name"], is_conductor=row["layer_kind"] == "conductor", thickness_um=row["thickness_um"]) for row in layers]
        centers, depth = {}, 0.
        for layer in layer_objects:
            assert layer.name not in centers and layer.thickness_um > 0
            centers[layer.name] = depth + layer.thickness_um / 2; depth += layer.thickness_um
        for link in links:
            vias = []
            for owner in link["owners_sorted"]:
                assert owner.startswith("via:") and owner.count(":") == 1
                candidates = list(db.execute("SELECT * FROM vias WHERE net_fold='dgnd' AND via_id_fold=?", (owner.split(":", 1)[1].casefold(),)))
                assert len(candidates) == 1
                row = dict(candidates[0])
                assert row["status"] in ("EXACT", "OUT_OF_SCOPE"), row["via_id"]
                if row["status"] == "OUT_OF_SCOPE":
                    # Compiler scope means no retained endpoint surface, not missing source geometry.
                    assert row["via_id_fold"] == "via1497311" and row["source_record_sha256"] == "ace2b525f3b32d7ec2725409f06a19cccd196abdc2041d9846ab5dc4bd909004"
                    assert db.execute("SELECT COUNT(*) FROM surfaces WHERE net_fold=? AND layer_id_fold IN (?,?)",
                                      (row["net_fold"], row["start_layer_id_fold"], row["end_layer_id_fold"])).fetchone()[0] == 0
                assert row["via_id_fold"] == row["via_id"].casefold()
                assert row["start_x_pm"] == row["end_x_pm"] and row["start_y_pm"] == row["end_y_pm"]
                endpoint_depths = []
                for side in ("start", "end"):
                    node = dict(db.execute("SELECT * FROM nodes WHERE net_fold=? AND node_id_fold=?",
                                           (row["net_fold"], row[side + "_node_id_fold"])).fetchone())
                    assert (node["layer_id_fold"], node["x_pm"], node["y_pm"]) == (row[side + "_layer_id_fold"], row[side + "_x_pm"], row[side + "_y_pm"])
                    layer = next(item for item in layers if item["layer_name"] == row[side + "_layer_id"])
                    assert layer["layer_kind"] == "conductor"
                    endpoint_depths.append(centers[layer["layer_name"]])
                assert endpoint_depths[0] < endpoint_depths[1]
                ps = dict(db.execute("SELECT * FROM padstacks WHERE padstack_id_fold=?", (row["padstack_id_fold"],)).fetchone())
                assert ps["drill_diameter_pm"] is not None and ps["drill_diameter_pm"] > 0
                declared = [item[0] for item in db.execute("SELECT layer_id FROM pad_shapes WHERE padstack_id_fold=?", (row["padstack_id_fold"],))]
                assert len(declared) == 2 and set(declared) == {row["start_layer_id"], row["end_layer_id"]}
                length = abs(centers[row["end_layer_id"]] - centers[row["start_layer_id"]])
                model = via_model.estimate_via_segment_rl(length_um=length, drill_diameter_um=ps["drill_diameter_pm"] * 1e-6,
                    padstack_material=ps["material"], start_layer=row["start_layer_id"], end_layer=row["end_layer_id"], stackup_layers=layer_objects)
                row.update(length_um=length, resistance_ohm=model.resistance_ohm, inductance_h=model.inductance_h,
                           classification=model.classification.conductor_model, padstack_source_row_sha256=ps["source_record_sha256"])
                vias.append(row)
            bridges = []
            for trace_ordinal, row_sha in BRIDGES.get(link["ordinal"], []):
                trace = dict(db.execute("SELECT * FROM traces WHERE ordinal=?", (trace_ordinal,)).fetchone())
                assert trace["source_record_sha256"] == row_sha and trace["net_fold"] == "dgnd"
                assert trace["width_status"] == trace["geometry_status"] == "EXACT"
                assert trace["width_pm"] == 50000000
                for endpoint in (trace["start_node_id_fold"], trace["end_node_id_fold"]):
                    matching = [(row, side) for row in vias for side in ("start", "end") if row[side + "_node_id_fold"] == endpoint]
                    assert len(matching) == 1
                    row, side = matching[0]
                    assert row[side + "_layer_id_fold"] == trace["layer_id_fold"]
                bridges.append(trace)
            matches = []
            for complete_order, nodes in paths([*vias, *bridges]):
                order = [index for index in complete_order if index < len(vias)]
                # spd.py7565/7634 stores casefolded via_key bytes before edge_owner.
                owner_digest = native_digest([vias[i]["via_id_fold"] for i in order])
                candidate = "spd-finite-via-edge:" + native_digest([*link["compiled_endpoints"], owner_digest])[:24]
                if candidate == link["link_id"]:
                    matches.append((order, nodes, owner_digest, complete_order))
            assert len(matches) == 1, (link["ordinal"], "native ordered-path fingerprint did not match")
            order, nodes, owner_digest, complete_order = matches[0]
            ordered = [vias[i] for i in order]
            l02_nodes = {row[key + "_node_id_fold"] for row in vias for key in ("start", "end") if row[key + "_layer_id"] == L02}
            assert len(l02_nodes) == 1 and l02_nodes <= set(nodes[1:-1])
            split_node = nodes.index(next(iter(l02_nodes)))
            split = sum(index < len(vias) for index in complete_order[:split_node])
            resistance = sum(row["resistance_ohm"] for row in ordered)
            inductance = sum(row["inductance_h"] for row in ordered)
            assert np.isclose(resistance, link["resistance_ohm"], rtol=1e-13, atol=0)
            assert np.isclose(inductance, link["inductance_h"], rtol=1e-13, atol=0)
            link.update(source_vias_in_native_path_order=ordered, source_path_node_ids_fold=nodes, ordered_owner_digest=owner_digest,
                        source_ideal_trace_bridges=bridges,
                        l02_junction_node_id_fold=nodes[split_node], l02_split_after_source_via_count=split,
                        source_sum_resistance_ohm=resistance, source_sum_inductance_h=inductance,
                        first_leg_resistance_ohm=sum(row["resistance_ohm"] for row in ordered[:split]),
                        second_leg_resistance_ohm=sum(row["resistance_ohm"] for row in ordered[split:]),
                        first_leg_inductance_h=sum(row["inductance_h"] for row in ordered[:split]),
                        second_leg_inductance_h=sum(row["inductance_h"] for row in ordered[split:]),
                        l02_excluded_owner_ids=sorted(row["via_id_fold"] for row in vias if row["via_id_fold"] in excluded_ids))
            assert len(link["l02_excluded_owner_ids"]) == 2
            assert time.monotonic() - started < 60
    with np.load(PINS["raw_field"][0], allow_pickle=False) as raw:
        endpoint_lists = []
        for field_name in ("all_finite_first_node_ids", "all_finite_second_node_ids"):
            all_nodes = json.loads(raw[field_name].tobytes())
            endpoint_lists.append({ordinal: all_nodes[ordinal] for ordinal in ordinals})
            del all_nodes
        for link in links:
            assert link["compiled_endpoints"] == [items[link["ordinal"]] for items in endpoint_lists]
        original = raw["finite_active_original_indices"]
        selected = np.flatnonzero(np.isin(original, ordinals))
        position = {int(original[i]): int(i) for i in selected}
        first, second = raw["finite_first_active_indices"], raw["finite_second_active_indices"]
        raw_r, raw_l = raw["finite_resistance_ohm_per_via"], raw["finite_inductance_h_per_via"]
        for link in links:
            index = position.get(link["ordinal"])
            link["active_finite_index"] = index
            link["active_endpoints"] = None if index is None else [int(first[index]), int(second[index])]
            if index is not None:
                assert raw_r[index] == link["resistance_ohm"]
                assert raw_l[index] == link["inductance_h"]
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_COMPOSITE_SOURCE_PATH_RECOVERY",
              "script_sha256": sha(Path(__file__)), "inputs": {key: {"path": str(path), "sha256": expected} for key, (path, expected) in PINS.items()},
              "composite_count": len(links), "active_composite_count": sum(row["active_finite_index"] is not None for row in links),
              "total_source_via_owner_count": sum(len(row["source_vias_in_native_path_order"]) for row in links),
              "source_ideal_trace_bridge_count": sum(len(row["source_ideal_trace_bridges"]) for row in links),
              "composite_source_via_count_histogram": dict(sorted(Counter(len(row["source_vias_in_native_path_order"]) for row in links).items())),
              "stackup_layers": layers, "links": links, "elapsed_s": time.monotonic() - started,
              "scope": "Exact source-node paths (including five exact source trace bridges kept ideal as in the native model) independently oriented by the original native link fingerprint, then existing source-via R/L estimator and sum controls. Sorted compiled owners are not used as path order. Leg sums identify a potential explicit L02 junction; no new trace R, junction-to-sheet coupling, native topology correction, mesh, new solve or PowerSI claim."}
    mass.atomic_json(output / "result.json", result)
    print(json.dumps({key: result[key] for key in ("status", "composite_count", "active_composite_count", "total_source_via_owner_count", "composite_source_via_count_histogram", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("PASS_L02_COMPOSITE_SOURCE_PATH_SELF_CHECK")
    else:
        if args.output is None:
            parser.error("--output is required")
        output = args.output.resolve()
        output.mkdir(exist_ok=False)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        try:
            main(output)
        except BaseException as exc:
            mass.atomic_json(output / "failure.json", {"status": "STOP_L02_COMPOSITE_SOURCE_PATH_RECOVERY", "error_type": type(exc).__name__, "error": str(exc)})
            raise
