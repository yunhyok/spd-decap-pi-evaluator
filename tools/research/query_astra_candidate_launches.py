"""Bounded source graph lookup; geometric proximity never creates an edge."""

from collections import deque
import json
from math import hypot
from pathlib import Path
import sqlite3
from time import monotonic


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "outputs/research/astra-step3-source-index-01"
TARGET = DATA / "selected-launches.json"
RAIL = "adc_vdd_180_vqps_sys_1_aon/0"


def shortest_paths(starts, targets, traces, vias):
    adjacency = {}
    for kind, rows, key in (("trace", traces, "trace_id"), ("via", vias, "via_id")):
        for row in rows:
            a, b = row["start_node_id"], row["end_node_id"]
            edge = {"kind": kind, "id": row[key], "source_record_sha256": row["source_record_sha256"]}
            adjacency.setdefault(a, []).append((b, edge))
            adjacency.setdefault(b, []).append((a, edge))
    found = {}
    for start in starts:
        queue = deque([(start, [])])
        seen = {start}
        while queue:
            node, path = queue.popleft()
            if node in targets:
                found[start] = {"target_node": node, "edges": path}
                break
            for neighbor, edge in adjacency.get(node, ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, path + [dict(edge, from_node=node, to_node=neighbor)]))
        else:
            found[start] = {"status": "NO_EXPLICIT_PATH_IN_QUERIED_TRACE_VIA_GRAPH", "visited_nodes": len(seen)}
    return found


def l21_bridge():
    """Test actual pad disks against one source polygon; never stamp an ideal sheet."""
    from shapely.geometry import Point, Polygon

    target = DATA / "l21-plane-bridge.json"
    if target.exists():
        raise FileExistsError("refusing to replace recorded plane lookup")
    started = monotonic()
    records = json.loads(TARGET.read_text(encoding="utf-8"))
    power = records["power_records"]
    layer = "Signal$L21(DGND)"
    with sqlite3.connect((DATA / "raw-spatial.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        primitives = [dict(r) for r in db.execute(
            "SELECT * FROM plane_primitives WHERE layer_name=? AND net_name=? LIMIT 2",
            (layer, "ADC_VDD_180_VQPS_SYS_1_AON/0"))]
        assert len(primitives) == 1
        primitive = primitives[0]
        assert primitive["polarity"] == "+" and primitive["kind"] == "polygon" and primitive["coordinate_unit"] == "um"
        vertices = [dict(r) for r in db.execute(
            "SELECT * FROM plane_vertices WHERE primitive_ordinal=? ORDER BY vertex_ordinal LIMIT 101",
            (primitive["primitive_ordinal"],))]
        assert len(vertices) == 8
        polygon = Polygon([(r["x_um"], r["y_um"]) for r in vertices])
        assert polygon.is_valid and not polygon.is_empty and not polygon.interiors
        nodes = [n for n in power["nodes"] if n["layer_id"] == layer]
        contacts = []
        for node in nodes:
            vias = [v for v in power["vias"] if node["node_id"] in (v["start_node_id"], v["end_node_id"])]
            assert len(vias) == 1
            via = vias[0]
            shape = dict(db.execute("SELECT * FROM pad_shapes WHERE padstack_id_fold=? AND layer_id=?",
                                    (via["padstack_id_fold"], layer)).fetchone())
            padstack = dict(db.execute("SELECT * FROM padstacks WHERE padstack_id_fold=?",
                                       (via["padstack_id_fold"],)).fetchone())
            assert shape["shape_kind"] == "CIRCLE" and shape["width_pm"] == shape["height_pm"]
            point = Point(node["x_pm"] / 10**6, node["y_pm"] / 10**6)
            radius = shape["width_pm"] / (2 * 10**6)
            # Exact disk containment for a center inside a simple polygon, without circle tessellation.
            clearance = polygon.boundary.distance(point)
            covered = polygon.contains(point) and clearance >= radius
            contacts.append({"node": node, "via": via, "pad_shape": shape, "padstack": padstack,
                             "center_to_boundary_um": clearance, "radius_um": radius,
                             "entire_pad_disk_inside_source_copper": bool(covered)})
        stackup = [dict(r) for r in db.execute("SELECT * FROM stackup_layers WHERE layer_name=?", (layer,))]
    assert len(contacts) == 4 and all(c["entire_pad_disk_inside_source_copper"] for c in contacts)
    top_ids = [n["node_id"] for n in power["nodes"] if n["layer_id_fold"] == "signal$top"]
    l30_ids = {n["node_id"] for n in power["nodes"] if n["layer_id_fold"] == "signal$l30(other_power1)"}
    top_paths = shortest_paths(top_ids, {n["node_id"] for n in nodes}, power["traces"], power["vias"])
    deeper_paths = shortest_paths([n["node_id"] for n in nodes], l30_ids, power["traces"], power["vias"])
    # A point inside a hole must never be certified as copper contact.
    holed = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]])
    assert not holed.contains(Point(2, 2)) and not polygon.contains(Point(0, 0))
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "ACCEPT_LOCAL_SOURCE_COPPER_CONTACT_ONLY", "elapsed_s": monotonic() - started,
              "database_metadata": records["database_metadata"], "primitive": primitive, "vertices": vertices,
              "area_um2": polygon.area, "contacts": contacts, "stackup_rows": stackup,
              "top_to_l21_paths": top_paths, "l21_to_l30_paths": deeper_paths,
              "scope": "Four exact via pad disks lie wholly in one same-net L21 polygon. This supplies a geometric bridge missing from the trace/via-only graph, not a zero-resistance connection, complete via/material proof, GND return, D096 owner binding, solver result, or production acceptance."}
    with target.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"output": str(target), "status": result["status"], "elapsed_s": result["elapsed_s"],
                      "area_um2": polygon.area,
                      "contacts": [{"node_id": c["node"]["node_id"], "radius_um": c["radius_um"],
                                    "margin_um": c["center_to_boundary_um"] - c["radius_um"]} for c in contacts],
                      "l21_to_l30_paths": deeper_paths}))


def main():
    if TARGET.exists():
        raise FileExistsError("refusing to replace recorded source lookup")
    started = monotonic()
    with sqlite3.connect((DATA / "raw-spatial.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        def query(sql, params=()):
            deadline = monotonic() + 10
            db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
            rows = [dict(row) for row in db.execute(sql + " LIMIT 1001", params)]
            if len(rows) > 1000:
                raise ValueError("selected lookup exceeds 1000 rows")
            return rows
        metadata = {row["key"]: row["value"] for row in query("SELECT * FROM meta")}
        assert metadata["source_sha256"] == "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
        power = {table: query(f"SELECT * FROM {table} WHERE net_fold=? ORDER BY ordinal", (RAIL,))
                 for table in ("nodes", "traces", "vias")}
        top = [row for row in power["nodes"] if row["layer_id_fold"] == "signal$top"]
        target_ids = {row["node_id"] for row in power["nodes"] if row["layer_id_fold"] == "signal$l30(other_power1)"}
        shapes = query("SELECT * FROM pad_shapes WHERE padstack_id_fold=?", ("dut",))
        assert len(top) == 3 and len(shapes) == 1
        assert shapes[0]["shape_kind"] == "CIRCLE" and shapes[0]["width_pm"] == shapes[0]["height_pm"]
        ground_top = query("SELECT * FROM nodes WHERE net_fold=? AND layer_id_fold=? AND x_pm BETWEEN ? AND ? AND y_pm BETWEEN ? AND ? ORDER BY ordinal",
                           ("dgnd", "signal$top", -12100 * 10**6, -11000 * 10**6, 12100 * 10**6, 12800 * 10**6))
        ground_pads = [row for row in ground_top if row["padstack_id_fold"] == "dut"]
        distances = []
        for pin in top:
            neighbor = min(ground_pads, key=lambda row: hypot(pin["x_pm"] - row["x_pm"], pin["y_pm"] - row["y_pm"]))
            distance = hypot(pin["x_pm"] - neighbor["x_pm"], pin["y_pm"] - neighbor["y_pm"]) / 10**6
            distances.append({"power_node": pin["node_id"], "nearby_ground_node": neighbor["node_id"],
                              "center_distance_um": distance, "equal_circle_edge_gap_um": distance - shapes[0]["width_pm"] / 10**6,
                              "role": "proximity diagnostic only; not an authorized ground pairing or return path"})
        paths = shortest_paths([row["node_id"] for row in top], target_ids, power["traces"], power["vias"])
    # One graph check prevents accidentally turning nearby/disconnected coordinates into connectivity.
    assert shortest_paths(["a"], {"b"}, [], []) == {"a": {"status": "NO_EXPLICIT_PATH_IN_QUERIED_TRACE_VIA_GRAPH", "visited_nodes": 1}}
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "SOURCE_LOOKUP_ONLY",
              "elapsed_s": monotonic() - started, "database_metadata": metadata,
              "power_records": power, "dut_pad_shapes": shapes, "nearby_ground_top_records": ground_top,
              "local_pad_spacing": distances, "top_to_l30_explicit_paths": paths,
              "scope": "No solver, inferred contacts, nearest-ground assignment, ideal plane edges, or product replacement. Missing graph paths do not prove a physical open circuit; plane contacts may require separate geometry evidence."}
    with TARGET.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps({"output": str(TARGET), "elapsed_s": result["elapsed_s"],
                      "record_counts": {key: len(rows) for key, rows in power.items()},
                      "local_pad_spacing": distances, "paths": paths}))


if __name__ == "__main__":
    main()
