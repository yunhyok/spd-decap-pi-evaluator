"""Exact frozen TOP PWR component around Trace464278; no field assembly."""
import json
from hashlib import sha256
from pathlib import Path
from time import monotonic
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUT = R / "astra-trace464278-pwr-component-20260912"
BRIDGE = R / "astra-device-power-top-bridges-02/bridge-assembly.json"
HASH = "583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790"

def sha(path): return sha256(path.read_bytes()).hexdigest()

def run():
    started = monotonic(); assert not OUT.exists(); assert sha(BRIDGE) == HASH
    graph = json.loads(BRIDGE.read_text())
    component = next(c for c in graph["components"] if "SITE0:3576" in c["pin_ids"])
    pins = component["pin_ids"]; pinset = set(pins)
    traces = [x for x in graph["instances"] if x["left_pin"] in pinset and x["right_pin"] in pinset]
    assert len(pins) == 107 and len(traces) == 106
    degree = {p: 0 for p in pins}
    for trace in traces:
        degree[trace["left_pin"]] += 1; degree[trace["right_pin"]] += 1
    endpoints = sorted(p for p, n in degree.items() if n == 1)
    assert endpoints == ["SITE0:3700", "SITE0:5811"] and all(n in (1, 2) for n in degree.values())
    center = next(x for x in traces if x["trace_id"] == "Trace464278")
    assert (center["left_pin"], center["right_pin"]) == ("SITE0:3576", "SITE0:3574")
    payload = dict(component_pin_id=np.asarray(pins), trace_id=np.asarray([x["trace_id"] for x in traces]),
                   trace_left_pin=np.asarray([x["left_pin"] for x in traces]), trace_right_pin=np.asarray([x["right_pin"] for x in traces]),
                   trace_translation_xy_um=np.asarray([x["translation_xy_um"] for x in traces]),
                   degree=np.asarray([degree[p] for p in pins], dtype=np.int8), endpoint_pin_id=np.asarray(endpoints),
                   central_trace_id=np.asarray(["Trace464278"]), central_source_pin_id=np.asarray(["SITE0:3576", "SITE0:3574"]),
                   assembly_status=np.asarray(["STOP_MISSING_PER_TRACE_BOUNDARY_CONFORMING_R_D_H_GEOMETRY"]),
                   missing_requirement=np.asarray(["The frozen graph supplies topology and translations, while the saved R/D/H closure exists only for Trace464278. A 106-trace/107-post complete copper assembly needs per-trace conforming volume/face geometry and deduplicated post ownership."]))
    OUT.mkdir(parents=True); (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(OUT / "component-topology.npz", **payload)
    result = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="STOP_MISSING_PER_TRACE_BOUNDARY_CONFORMING_R_D_H_GEOMETRY",
                  elapsed_s=monotonic()-started, driver_sha256=sha(Path(__file__)), artifact_sha256=sha(OUT / "component-topology.npz"),
                  pins={str(BRIDGE): HASH}, counts=dict(component_pins=107, component_traces=106, degree_one_endpoints=2,
                  degree_two_internal_pins=105, existing_rdh_central_traces=1, required_rdh_component_traces=106),
                  topology=dict(endpoint_pins=endpoints, central_trace="Trace464278 SITE0:3576->SITE0:3574", source_top_pads=["SITE0:3576", "SITE0:3574"]),
                  gates=dict(single_path=True, actual_trace_and_pins=True, all_107_lower_native_contacts_not_yet_bound=True,
                  no_scalar_facewise_outer_collapse=True, no_arbitrary_ground=True),
                  scope="Frozen graph census and exact topology payload only. It does not assemble R/D/H, alter first-via ownership, move the outer cut, use Green, or solve.")
    (OUT / "result.json").write_text(json.dumps(result, indent=2)+"\n"); print(json.dumps(result))
if __name__ == "__main__": run()
