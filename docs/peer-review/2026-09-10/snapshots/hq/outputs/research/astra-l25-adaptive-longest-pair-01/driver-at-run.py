"""Bounded primal/dual refinement on the unchanged source polygonal domain."""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PRIOR = R / "astra-l25-rt0-p1-refined-pair-01"
PRIOR_SHA = "3d744e4e125b805aeac197b4580fb029c7aa18993778919046b5fd6153c44047"
PINS = {
    "pair": (ROOT / "tools/research/probe_astra_l25_rt0_p1_refined_pair.py", "4e3f025b88b20633f5f64535c30c31c983d3e6344a231c07cc1cdeba06acbc33"),
    "localizer": (ROOT / "tools/research/localize_astra_l25_rt0_p1_gap.py", "37d17daffd6abf99bd8b1aa5146a2d7bdf0ec754401067ef30e1a4b032e704c0"),
}
PROGRAM = "SPD Decap PI Evaluator v0.23.1"
MAX_STEPS, MAX_NEW_NODES, GAP_TARGET = 10, 20000, .01


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def helper(name):
    path, expected = PINS[name]
    if sha(path) != expected:
        raise ValueError(f"helper hash differs: {path}")
    spec = importlib.util.spec_from_file_location("astra_adaptive_" + name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def split_edges(original, midpoints):
    result = set(original)
    for edge in original & midpoints.keys():
        result.remove(edge)
        a, b = edge
        midpoint = midpoints[edge]
        result.update((tuple(sorted((a, midpoint))), tuple(sorted((b, midpoint)))))
    return result


def attach_contacts(mesh, mapping, metadata):
    nodes = np.flatnonzero(mapping < 175)
    nodes = nodes[np.argsort(mapping[nodes], kind="stable")]
    counts = np.bincount(mapping[nodes], minlength=175)
    mesh["contact_node_indices"] = nodes
    mesh["contact_node_indptr"] = np.r_[0, np.cumsum(counts)]
    mesh["contacts_json_utf8"] = metadata


def check_area_roundoff(points, triangles, new_points, new_triangles, parents, edges, relative_error):
    """Prove intended dyadic subdivision when absolute-coordinate rounding dominates."""
    bad = np.flatnonzero(relative_error > 1e-12)
    if np.max(relative_error) > 1e-10:
        raise ValueError("stored-coordinate area change exceeds 1e-10 nesting ceiling")
    order = np.argsort(parents, kind="stable") if len(bad) else np.array([], dtype=int)
    sorted_parents = parents[order]
    coordinates = {}
    max_delta = max_ulp = 0.0

    def exact(node):
        nonlocal max_delta, max_ulp
        node = int(node)
        if node not in coordinates:
            if node < len(points):
                coordinates[node] = tuple(Fraction(float(x)) for x in points[node])
            else:
                a, b = edges[node - len(points)]
                coordinates[node] = tuple((x+y)/2 for x, y in zip(exact(a), exact(b)))
                for actual, intended in zip(new_points[node], coordinates[node]):
                    delta = abs(Fraction(float(actual)) - intended)
                    if delta > Fraction(float(abs(np.spacing(actual)))) / 2:
                        raise ValueError("midpoint displacement exceeds half an ULP")
                    max_delta = max(max_delta, float(delta))
                    max_ulp = max(max_ulp, float(delta / Fraction(float(abs(np.spacing(actual))))))
        return coordinates[node]

    def det(nodes):
        a, b, c = [exact(node) for node in nodes]
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])

    for parent in bad:
        start, stop = np.searchsorted(sorted_parents, [parent, parent + 1])
        original = det(triangles[parent])
        children = [det(new_triangles[i]) for i in order[start:stop]]
        if any(value * original <= 0 for value in children) or sum(children) != original:
            raise ValueError("exact intended child areas do not partition their parent")
    return {"fallback_parent_count": len(bad), "exact_intended_partition_pass": True, "max_midpoint_roundoff_um_in_fallback": max_delta, "max_midpoint_roundoff_ulp_in_fallback": max_ulp, "stored_area_relative_ceiling": 1e-10}


def load_start(h):
    receipt = h._json(PRIOR / "result.json", PRIOR_SHA)
    if receipt["status"] != "COMPLETED_CONDITIONAL_L25_ADAPTIVE_RT0_P1_PAIR":
        raise ValueError("accepted initial refinement required")
    state = {}
    for key, filename in (("mesh", "refined-mesh.npz"), ("topology", "refined-topology.npz"), ("drive", "refined-sheet-drive.npz"), ("rt0", "refined-rt0-resistance.npz")):
        state[key] = h._npz(PRIOR / filename, receipt["pre_lu"]["artifacts"][filename]["sha256"])
    for key, filename in (("rt0_field", "rt0-field.npz"), ("p1_field", "p1-field.npz")):
        state[key] = h._npz(PRIOR / filename, receipt["fields"][filename]["sha256"])
    original = h._npz(h.MESH_DIR / "mesh-stiffness.npz", h.MESH_SHA256)
    mesh = state["mesh"]
    mesh["original_triangle_index"] = mesh["parent_triangle_index"].copy()
    attach_contacts(mesh, state["drive"]["full_to_contracted"], original["contacts_json_utf8"])
    state["result"] = receipt
    return state


def refine_once(state, selected, h, refiner, initial_nodes, marking="all"):
    mesh, topo, drive = (state[key] for key in ("mesh", "topology", "drive"))
    points, triangles, tags = (mesh[key] for key in ("node_xy_um", "triangles", "triangle_contact_tag"))
    chosen = triangles[topo["free_triangle_indices"][selected]]
    candidates = chosen[:, [[1, 2], [2, 0], [0, 1]]]
    if marking == "longest":
        delta = points[candidates[:, :, 0]] - points[candidates[:, :, 1]]
        # Longest-edge marks with existing conforming closures; no claim of full LEPP quality guarantees.
        candidates = candidates[np.arange(len(chosen)), np.argmax(np.sum(delta*delta, axis=2), axis=1)]
    marked = np.unique(np.sort(candidates.reshape(-1, 2), axis=1), axis=0)
    if len(points) + len(marked) - initial_nodes > MAX_NEW_NODES:
        return None, {"requested_new_nodes": len(points) + len(marked) - initial_nodes}
    natural, rims = h._edge_inventory(triangles, tags)
    rim_owner = {edge: owner for owner, edges in rims.items() for edge in edges}
    new_points, new_triangles, parents, edges, coverage = refiner.refine_marked_edges(points, triangles, marked)
    if not np.array_equal(new_points[:len(points)], points) or not np.array_equal(new_points[len(points):], (points[edges[:, 0]] + points[edges[:, 1]]) / 2):
        raise ValueError("refinement moved old vertices or added a non-midpoint vertex")
    tuples = [tuple(map(int, edge)) for edge in edges]
    expected_coverage = np.array([1 if edge in natural else 2 for edge in tuples])
    if not np.array_equal(coverage, expected_coverage):
        raise ValueError("marked-edge support differs from old boundary inventory")
    middle = {edge: len(points) + i for i, edge in enumerate(tuples)}
    new_tags = tags[parents]
    new_natural, new_rims = h._edge_inventory(new_triangles, new_tags)
    if new_natural != split_edges(natural, middle):
        raise ValueError("natural boundary is not exactly the bisected old segments")
    for owner in range(175):
        if new_rims[owner] != split_edges(rims[owner], middle):
            raise ValueError(f"electrode {owner} rim changed physical segments")
    old_det = refiner.signed_twice_areas(points, triangles)
    new_det = refiner.signed_twice_areas(new_points, new_triangles)
    if np.any(new_det * old_det[parents] <= 0):
        raise ValueError("child winding changed")
    area_errors = abs(np.bincount(parents, weights=abs(new_det)) - abs(old_det)) / abs(old_det)
    area_error = float(np.max(area_errors))
    area_roundoff = check_area_roundoff(points, triangles, new_points, new_triangles, parents, edges, area_errors)
    old_contact_area = np.bincount(tags[tags >= 0], weights=abs(old_det[tags >= 0]), minlength=175)
    new_contact_area = np.bincount(new_tags[new_tags >= 0], weights=abs(new_det[new_tags >= 0]), minlength=175)
    contact_error = float(np.max(abs(old_contact_area - new_contact_area) / old_contact_area))
    if contact_error > 1e-12:
        raise ValueError(f"electrode region area changed: {contact_error}")
    expected_degree = np.array([len(new_rims[owner]) for owner in range(175)])
    new_topology = h._rebuild_topology(new_triangles, new_tags, expected_contact_degree=expected_degree)

    old_mapping = drive["full_to_contracted"]
    old_g = h._csr(drive, "conductance")
    midpoint_contact = np.array([rim_owner.get(edge, -1) for edge in tuples])
    free_midpoint = midpoint_contact < 0
    new_dofs = int(np.count_nonzero(free_midpoint))
    midpoint_mapping = midpoint_contact.copy()
    midpoint_mapping[free_midpoint] = np.arange(old_g.shape[0], old_g.shape[0] + new_dofs)
    new_mapping = np.r_[old_mapping, midpoint_mapping]
    labels = np.where(new_mapping < 175, new_mapping, -1)
    if not np.array_equal(h._triangle_contact_tags(new_triangles, labels), new_tags):
        raise ValueError("inherited contact region differs from electrode DOFs")
    multiplicity = np.bincount(parents, minlength=len(triangles))
    affected = np.flatnonzero(multiplicity > 1)
    affected_free = affected[tags[affected] < 0]
    children_free = np.flatnonzero((multiplicity[parents] > 1) & (new_tags < 0))
    dimension = old_g.shape[0] + new_dofs
    old_update = h._assemble_updates(triangles[affected_free], points * 1e-6, old_mapping, h.SHEET_CONDUCTANCE_S, dimension)
    new_update = h._assemble_updates(new_triangles[children_free], new_points * 1e-6, new_mapping, h.SHEET_CONDUCTANCE_S, dimension)
    new_g = (sparse.block_diag((old_g, sparse.csc_matrix((new_dofs, new_dofs))), format="csc") + new_update - old_update).tocsc()
    new_g.sum_duplicates(); new_g.sort_indices()
    old_v = state["p1_field"]["contracted_voltage_v"]
    midpoint_v = (old_v[old_mapping[edges[:, 0]]] + old_v[old_mapping[edges[:, 1]]]) / 2
    new_v = np.r_[old_v, midpoint_v[free_midpoint]]
    if not np.array_equal(new_v[new_mapping[len(points):][~free_midpoint]], midpoint_v[~free_midpoint]):
        raise ValueError("rim midpoint voltage does not equal its electrode")
    old_energy, new_energy = float(old_v @ (old_g @ old_v)), float(new_v @ (new_g @ new_v))
    p1_error = abs(new_energy - old_energy) / old_energy
    if p1_error > h.ENERGY_REL_TOL:
        raise ValueError(f"P1 prolongation energy changed: {p1_error}")
    new_mesh = {"node_xy_um": new_points, "triangles": new_triangles, "triangle_contact_tag": new_tags, "parent_triangle_index": parents, "original_triangle_index": mesh["original_triangle_index"][parents]}
    attach_contacts(new_mesh, new_mapping, mesh["contacts_json_utf8"])
    new_rt0 = h._assemble_rt0(new_points * 1e-6, new_triangles, new_topology, h.SHEET_CONDUCTANCE_S)
    new_rt0 = {key: new_rt0[key] for key in ("r_data", "r_indices", "r_indptr", "r_shape", "branch_first_node", "branch_second_node")}
    _, rt0_control = h._prolong_rt0(state["rt0_field"], state["rt0"], new_mesh, new_rt0, new_topology, parents, topo, mesh, h.SHEET_CONDUCTANCE_S)
    new_drive = {"full_to_contracted": new_mapping, "conductance_data": new_g.data, "conductance_indices": new_g.indices, "conductance_indptr": new_g.indptr, "conductance_shape": np.array(new_g.shape)}
    proof = {"selected_cells": len(selected), "marked_edges": len(edges), "marked_natural_edges": int(np.count_nonzero(expected_coverage == 1)), "marked_rim_edges": int(np.count_nonzero(~free_midpoint)), "new_p1_dofs": new_dofs, "affected_parents": len(affected), "new_nodes": len(new_points), "new_triangles": len(new_triangles), "contact_rim_degrees": expected_degree.tolist(), "exact_bisected_natural_and_rim_edge_sets": True, "parent_area_relative_error": area_error, "electrode_area_relative_error": contact_error, "p1_prolongation_energy_relative_error": p1_error, "rt0_prolongation": rt0_control}
    proof["area_roundoff_control"] = area_roundoff
    return {"mesh": new_mesh, "topology": new_topology, "drive": new_drive, "rt0": new_rt0}, proof


def run(output, marking="all"):
    started = time.perf_counter()
    h, localizer = helper("pair"), helper("localizer")
    base, _ = h._load_base_helper()
    refiner, _ = h._load_refiner()
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    for name, (path, _) in PINS.items():
        (output / (name + "-at-run.py")).write_bytes(path.read_bytes())
    budget = base._Budget(output / "progress.jsonl")
    watchdog = budget.start_watchdog()
    history = []
    try:
        state = load_start(h)
        initial_nodes = len(state["mesh"]["node_xy_um"])
        status = "STOP_MAX_ADAPTIVE_STEPS"
        for step in range(1, MAX_STEPS + 1):
            budget.check("step_start")
            previous = state["result"]
            cells = localizer._triangle_geometry(state["mesh"], state["topology"], state["drive"], state["rt0_field"], state["p1_field"])
            gap = cells["gap"]
            gap_sum = float(gap.sum())
            if not np.all(np.isfinite(gap)) or np.any(gap < 0) or abs(gap_sum - previous["gap"]["gap_ohm"]) > 1e-10 * previous["gap"]["gap_ohm"]:
                raise ValueError("cell gap no longer matches accepted pair energy")
            order = np.argsort(-gap, kind="stable")
            count = int(np.searchsorted(np.cumsum(gap[order]), .9 * gap_sum) + 1)
            selected = order[:count]
            del cells
            candidate, proof = refine_once(state, selected, h, refiner, initial_nodes, marking)
            if candidate is None:
                status = "STOP_MAX_NEW_NODES"
                budget.emit("adaptive_node_limit", **proof)
                break
            budget.check("refined_operators")
            step_dir = output / f"step-{step:02d}"
            step_dir.mkdir()
            artifacts = {}
            for key in ("mesh", "topology", "drive", "rt0"):
                path = step_dir / (key + ".npz")
                h._write_npz(path, **candidate[key])
                artifacts[key] = base._file_receipt(path)
            h._write_json(step_dir / "prelu.json", {"proof": proof, "artifacts": artifacts})
            budget.emit("adaptive_prelu_pass", step=step, **{key: proof[key] for key in ("selected_cells", "marked_edges", "marked_rim_edges", "marked_natural_edges", "new_nodes", "new_triangles")})
            inputs = {"rt0_npz": candidate["rt0"], "topology_npz": candidate["topology"], "drive_npz": candidate["drive"], "topology": {"free_cell_count": len(candidate["topology"]["free_triangle_indices"]), "potential_node_count": len(candidate["topology"]["free_triangle_indices"]) + 175}}
            rt, q, potential = base._rt0_solve(inputs, step_dir, budget)
            p1, v = base._p1_solve(inputs, step_dir, budget)
            integrated = base._gap_integral(candidate["mesh"], candidate["topology"], candidate["drive"]["full_to_contracted"], v, q)
            gates = base._final_gates(rt, p1, integrated)
            if not all(value for value in gates.values() if isinstance(value, bool)):
                raise ValueError("adaptive solved-field physical or gap gate failed")
            old_p1, old_rt = previous["p1"]["pair_voltage_ohm"], previous["rt0"]["pair_voltage_ohm"]
            tolerance = 2e-9 * max(old_p1, old_rt)
            if p1["pair_voltage_ohm"] < old_p1 - tolerance or rt["pair_voltage_ohm"] > old_rt + tolerance:
                raise ValueError("adaptive resistance bounds are not nested")
            contraction = integrated["gap_ohm"] / previous["gap"]["gap_ohm"]
            relative_gap = integrated["gap_ohm"] / p1["pair_voltage_ohm"]
            result = {"program": PROGRAM, "status": "COMPLETED_CONDITIONAL_ADAPTIVE_STEP", "step": step, "proof": proof, "artifacts": artifacts, "rt0": rt, "p1": p1, "gap": integrated, "gates": gates, "gap_ratio_to_previous": contraction, "gap_over_p1": relative_gap, "fields": {name: base._file_receipt(step_dir / name) for name in ("rt0-field.npz", "p1-field.npz")}}
            h._write_json(step_dir / "result.json", result)
            history.append({"step": step, "result_sha256": sha(step_dir / "result.json"), "p1_ohm": p1["pair_voltage_ohm"], "rt0_ohm": rt["pair_voltage_ohm"], "gap_over_p1": relative_gap, "gap_ratio_to_previous": contraction, "nodes": proof["new_nodes"], "triangles": proof["new_triangles"]})
            budget.emit("adaptive_step_complete", **history[-1])
            candidate.update(result=result, rt0_field={"branch_current_a": q, "potential_v": potential}, p1_field={"contracted_voltage_v": v})
            state = candidate
            if relative_gap <= GAP_TARGET:
                status = "COMPLETED_CONDITIONAL_GAP_TARGET"
                break
            if contraction >= .99:
                status = "STOP_WEAK_GAP_CONTRACTION"
                break
        receipt = {"program": PROGRAM, "status": status, "prior_result_sha256": PRIOR_SHA, "driver_sha256": sha(output / "driver-at-run.py"), "helpers": {name: {"sha256": digest, "path": str(path)} for name, (path, digest) in PINS.items()}, "policy": {"max_steps": MAX_STEPS, "max_new_nodes": MAX_NEW_NODES, "gap_over_p1_target": GAP_TARGET, "marking_fraction": .9, "minimum_gap_reduction_per_step": .01, "same_polygonal_domain": True}, "history": history, "elapsed_s": time.perf_counter() - started, "budget": {"max_runtime_s": base.MAX_RUNTIME_S, "max_rss_bytes": base.MAX_RSS_BYTES, "peak_working_set_bytes": budget.peak_working_set, "peak_private_bytes": budget.peak_private}, "limitations": ["Numerical DC primal/dual qualification for this fixed electrode pair and fixed source polygon only; not a rigorous roundoff-certified bound, other port, board, G/C or magnetic/PowerSI result. Source G/C must be exactly transferred again before frequency reuse."]}
        receipt["policy"]["marked_cell_edge_rule"] = marking
        receipt["limitations"].append("Same domain means the intended exact midpoint subdivision. Stored float coordinates have bounded midpoint quantization; parents exceeding 1e-12 float-evaluated relative area change require exact-rational intended area/winding checks and half-ULP midpoint checks, with a 1e-10 float-evaluated stored-area ceiling. No area normalization or coordinate correction is applied.")
        h._write_json(output / "result.json", receipt)
        print(json.dumps({"status": status, "steps": len(history), "last": history[-1] if history else None, "elapsed_s": receipt["elapsed_s"]}))
        return 0 if status == "COMPLETED_CONDITIONAL_GAP_TARGET" else 2
    except Exception as exc:
        h._write_json(output / "failure.json", {"program": PROGRAM, "status": "STOP_ADAPTIVE_EXCEPTION", "error": f"{type(exc).__name__}: {exc}", "completed_steps": history, "elapsed_s": time.perf_counter() - started})
        raise
    finally:
        budget.stop.set(); watchdog.join(timeout=5)


def self_check():
    assert split_edges({(0, 1), (1, 2)}, {(0, 1): 3}) == {(0, 3), (1, 3), (1, 2)}
    assert split_edges({(0, 1)}, {}) == {(0, 1)}
    h = helper("pair")
    h._self_check()
    refiner, _ = h._load_refiner()
    # One connected strip with 175 disjoint contact pads along its upper edge.
    # Exercise the actual rim/natural refinement path without a source file or LU.
    points = np.array([(x, y) for y in range(3) for x in range(351)], dtype=float)
    triangles = np.array([(a, a + 1, a + 352) if part == 0 else (a, a + 352, a + 351)
                          for y in range(2) for x in range(350) for a in [351*y+x] for part in range(2)])
    labels = np.full(len(points), -1, dtype=np.int64)
    for owner in range(175):
        labels[[351+2*owner, 352+2*owner, 702+2*owner, 703+2*owner]] = owner
    tags = h._triangle_contact_tags(triangles, labels)
    _, rims = h._edge_inventory(triangles, tags)
    topo = h._rebuild_topology(triangles, tags, expected_contact_degree=np.array([len(rims[i]) for i in range(175)]))
    mapping = labels.copy()
    mapping[labels < 0] = np.arange(175, 175 + np.count_nonzero(labels < 0))
    g = h._assemble_updates(triangles[tags < 0], points * 1e-6, mapping, h.SHEET_CONDUCTANCE_S).tocsc()
    drive = {"full_to_contracted": mapping, "conductance_data": g.data, "conductance_indices": g.indices, "conductance_indptr": g.indptr, "conductance_shape": np.array(g.shape)}
    first, second = topo["branch_first_node"], topo["branch_second_node"]
    free_count = len(topo["free_triangle_indices"])
    adjacency = sparse.coo_matrix((np.ones(len(first)), (first, second)), shape=(free_count + 175,)*2).tocsr()
    _, predecessor = sparse.csgraph.shortest_path(adjacency, directed=False, indices=free_count, return_predecessors=True, unweighted=True)
    edge_lookup = {tuple(sorted((int(a), int(b)))): i for i, (a, b) in enumerate(zip(first, second))}
    q = np.zeros(len(first))
    current = free_count + 1
    while current != free_count:
        previous = int(predecessor[current])
        assert previous >= 0
        branch = edge_lookup[tuple(sorted((previous, current)))]
        q[branch] = 1 if first[branch] == previous else -1
        current = previous
    rt0 = h._assemble_rt0(points * 1e-6, triangles, topo, h.SHEET_CONDUCTANCE_S)
    mesh = {"node_xy_um": points, "triangles": triangles, "triangle_contact_tag": tags, "original_triangle_index": np.arange(len(triangles)), "contacts_json_utf8": np.array([], dtype=np.uint8)}
    state = {"mesh": mesh, "topology": topo, "drive": drive, "rt0": rt0, "rt0_field": {"branch_current_a": q}, "p1_field": {"contracted_voltage_v": np.sin(np.arange(g.shape[0]))}}
    selected = np.arange(free_count)
    refined, proof = refine_once(state, selected, h, refiner, len(points))
    assert refined is not None and proof["marked_natural_edges"] > 0 and proof["marked_rim_edges"] > 0
    assert proof["new_p1_dofs"] + proof["marked_rim_edges"] == proof["marked_edges"]
    assert np.array_equal(proof["contact_rim_degrees"], 2*np.array([len(rims[i]) for i in range(175)]))
    assert refine_once(state, selected, h, refiner, -MAX_NEW_NODES)[0] is None
    longest, longest_proof = refine_once(state, selected, h, refiner, len(points), "longest")
    assert longest is not None and longest_proof["marked_edges"] < proof["marked_edges"]
    assert longest_proof["marked_edges"] <= len(selected)
    print(json.dumps({"boundary_policy_self_check": "PASS", "natural_edges": proof["marked_natural_edges"], "rim_edges": proof["marked_rim_edges"], "p1_energy_relative_error": proof["p1_prolongation_energy_relative_error"], "rt0_energy_relative_error": proof["rt0_prolongation"]["energy_relative_error"]}))
    # Minimal reproduction of absolute-coordinate midpoint quantization.
    p = np.array([[33136.996968167885, 22364.682793485284], [33143.49652535897, 22364.92218635213], [33150., 22675.]])
    t = np.array([[0, 1, 2]])
    pn, tn, parent, edges, _ = refiner.refine_marked_edges(p, t, np.array([[0, 1], [1, 2], [0, 2]]))
    d, dn = refiner.signed_twice_areas(p, t), refiner.signed_twice_areas(pn, tn)
    error = abs(np.bincount(parent, weights=abs(dn)) - abs(d)) / abs(d)
    assert check_area_roundoff(p, t, pn, tn, parent, edges, error)["fallback_parent_count"] == 1
    broken = tn.copy(); broken[-1] = broken[-1, [0, 2, 1]]
    try:
        check_area_roundoff(p, t, pn, broken, parent, edges, error)
    except ValueError:
        pass
    else:
        raise AssertionError("exact partition guard accepted changed child winding")
    print(PROGRAM + ": ADAPTIVE_SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=PROGRAM)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--marking", choices=("all", "longest"), default="all")
    parser.add_argument("--output-root", type=Path, default=R / "astra-l25-adaptive-pair-01")
    args = parser.parse_args()
    raise SystemExit((self_check() or 0) if args.self_check else run(args.output_root.resolve(), args.marking))
