"""Source trace resistance scale, not a connected PDN or port impedance model."""

from hashlib import sha256
import json
from math import hypot, isfinite
from pathlib import Path
import sqlite3
from time import monotonic


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/research/astra-step4-basis-01"
PATHS = ROOT / "outputs/research/astra-step3-source-index-01/l21-plane-bridge.json"
OUTPUT = ROOT / "docs/evaluation-research/astra_source_trace_resistance_2026-09-06.json"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    started = monotonic()
    data = PATHS.read_bytes()
    assert sha256(data).hexdigest() == "e4bbbc54e4f1708ad684605e41c5a17cb0dc7fcdd49c2794437196803c82ec01"
    paths = json.loads(data)["top_to_l21_paths"]
    trace_ids = {e["id"] for p in paths.values() for e in p["edges"] if e["kind"] == "trace"}
    assert 0 < len(trace_ids) <= 20
    rows = {}
    with sqlite3.connect((BASE / "indexes/raw-spatial.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        meta = dict(db.execute("SELECT key,value FROM meta"))
        # The saved indexes are net-prefixed; a bare ID predicate scans the board.
        rail = "adc_vdd_180_vqps_sys_1_aon/0"
        records = {}
        for table, key, cap in (("nodes", "node_id_fold", 300), ("traces", "trace_id_fold", 160), ("vias", "via_id_fold", 220)):
            selected = [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE net_fold=? LIMIT ?", (rail, cap + 1))]
            assert len(selected) <= cap
            records[table] = {r[key]: r for r in selected}
        for name in sorted(trace_ids):
            trace = records["traces"][name.casefold()]
            endpoints = [records["nodes"][trace[k]] for k in ("start_node_id_fold", "end_node_id_fold")]
            layer = dict(db.execute("SELECT * FROM stackup_layers WHERE layer_name=?", (trace["layer_id"],)).fetchone())
            assert trace["width_status"] == trace["geometry_status"] == "EXACT"
            assert layer["layer_kind"] == "conductor" and layer["material_name"] == "COPPER"
            assert all(n["net_fold"] == trace["net_fold"] and n["layer_id"] == trace["layer_id"] for n in endpoints)
            length_um = hypot(endpoints[0]["x_pm"] - endpoints[1]["x_pm"], endpoints[0]["y_pm"] - endpoints[1]["y_pm"]) / 1e6
            width_um = trace["width_pm"] / 1e6
            thickness_um = layer["thickness_um"]
            sigma = layer["conductivity_s_per_m"]
            assert all(isfinite(v) and v > 0 for v in (length_um, width_um, thickness_um, sigma))
            nominal_r = length_um * 1e6 / (sigma * width_um * thickness_um)
            rows[name] = {"trace": trace, "nodes": endpoints, "stackup_layer": layer,
                          "centerline_length_um": length_um, "nominal_uniform_rectangle_dc_ohm": nominal_r}
        # Rebind every reused edge to the saved current-basis raw records, including Vias.
        paths_result = {}
        for start, path in paths.items():
            current = start
            for edge in path["edges"]:
                assert edge["from_node"] == current
                table, key = ("traces", "trace_id_fold") if edge["kind"] == "trace" else ("vias", "via_id_fold")
                record = records[table][edge["id"].casefold()]
                assert record["source_record_sha256"] == edge["source_record_sha256"]
                assert {record["start_node_id"], record["end_node_id"]} == {edge["from_node"], edge["to_node"]}
                current = edge["to_node"]
            assert current == path["target_node"]
            trace_edges = [e["id"] for e in path["edges"] if e["kind"] == "trace"]
            paths_result[start] = {"target_node": current, "edge_count": len(path["edges"]), "trace_ids": trace_edges,
                                   "sum_of_trace_nominal_dc_ohm": sum(rows[name]["nominal_uniform_rectangle_dc_ohm"] for name in trace_edges)}
    with sqlite3.connect((BASE / "indexes/compiled-topology.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        native = [dict(r) for r in db.execute("SELECT * FROM link_owners WHERE owner_id LIKE 'trace:%' LIMIT 2")]
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "ACCEPT_SOURCE_TRACE_SCALE_DIAGNOSTIC_ONLY",
              "elapsed_s": monotonic() - started, "raw_basis": meta, "reused_path_sha256": sha256(data).hexdigest(),
              "trace_count": len(rows), "traces": rows, "paths": paths_result,
              "compiled_trace_namespace_owner_sample": native,
              "assumptions": ["Uniform rectangular conductor from source width, layer copper thickness and 20 C conductivity; centerline-length isolated-trace diagnostic.",
                              "Full source trace/via paths and hashes match the saved D115b raw basis; no geometric proximity joins were added."],
              "limitations": ["No trace-pad overlap correction, junction spreading, parallel path/current-sharing, skin/proximity effect, external or mutual L.",
                              "A path's sum is not the effective PDN resistance, a proven upper/lower bound, or a PowerSI error attribution.",
                              "No trace:-namespace owners found only establishes the inspected saved model's owner representation; it is not global proof that all trace effects are absent.",
                              "No existing ideal trace/plane quotient was split; no resistor, return, native G/C replacement or production model was stamped."]}
    OUTPUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in ("raw_basis", "traces")}))


if __name__ == "__main__":
    main()
