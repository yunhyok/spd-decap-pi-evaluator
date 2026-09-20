"""Update only changed L25 source G/C overlap areas after the frozen refinement."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
import shapely
from scipy import sparse

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM = "SPD Decap PI Evaluator v0.23.1"
PINS = {
    "mesh": (R / "astra-l25-sheet-mesh-preflight-01/mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
    "selection": (R / "astra-l25-rt0-p1-gap-inputs-02/cell-gap-selection.npz", "2f749e9623af96816a1d56ae6fee402789e0539d1e5d77761e586150183dff6f"),
    "areas": (R / "astra-l25-dual-cell-inputs-02/owner-cell-area.npz", "5c6b1f1a0d71ec7d8688e80f0aff192a6f9849b200e70e8ccf98ee0149515239"),
    "inventory": (R / "astra-l25-source-sheet-01/l25-gc-projection-inventory.json", "703e8cdf9988805a2fb05a40ff659bc9890ba999e2eef00c741a112aa5976a77"),
    "assets": (R / "astra-l25-source-sheet-01/source-gc-assets/receipt.json", "91f6dbfa0fed88b040dc84444110112dd1bd4a445a7ff9affc27536b8d835c5c"),
    "refiner": (ROOT / "tools/research/refine_astra_l25_pair_mesh.py", "2ceb44814ec1d9655a0a3e70d3cbd16f354c6a8b757a35868b1ef29db89f7393"),
    "decoder": (ROOT / "tools/research/project_astra_l14_gc_mass.py", "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
    "budget": (ROOT / "tools/research/probe_astra_l25_rt0_p1_pair.py", "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"),
}
REFINED_PINS = {
    "mesh": (R / "astra-l25-adaptive-longest-pair-01/step-06/mesh.npz", "20b52d26783bf98197524c56f22d9872aee3b67b7e526ff20a02e396f39e89cb"),
    "step_result": (R / "astra-l25-adaptive-longest-pair-01/step-06/result.json", "9734d6b5222ad72488a07bea7a901874cb983c2a6710d862faaee07233865217"),
    "adaptive_result": (R / "astra-l25-adaptive-longest-pair-01/result.json", "bb418b0ea06d9ea71a0316a55ed107cd77f323b3ea8fec2fa3ba8cd58b987eba"),
    "adaptive_driver": (R / "astra-l25-adaptive-longest-pair-01/driver-at-run.py", "8ba3f885906dc4b23867012b5a2da707cfe08cbc7c041d27ba2d86eac82f0e31"),
}


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pin(path, expected):
    if sha(path) != expected:
        raise ValueError(f"input hash mismatch: {path}")


def module(name):
    path, expected = PINS[name]
    pin(path, expected)
    spec = importlib.util.spec_from_file_location("astra_gc_" + name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def npz(name):
    with np.load(PINS[name][0], allow_pickle=False) as saved:
        return {key: saved[key] for key in saved.files}


def cut_areas(geometry, vertices):
    values = np.asarray(shapely.area(shapely.intersection(shapely.polygons(vertices), geometry)))
    if not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError("invalid source intersection area")
    return values


def _ancestry_contract(original_nodes, original_triangles, original_tags, refined_nodes, refined_triangles, original_index, refined_tags):
    original_nodes = np.asarray(original_nodes, dtype=np.float64)
    original_triangles = np.asarray(original_triangles, dtype=np.int64)
    original_tags = np.asarray(original_tags, dtype=np.int64)
    refined_nodes = np.asarray(refined_nodes, dtype=np.float64)
    refined_triangles = np.asarray(refined_triangles, dtype=np.int64)
    original_index = np.asarray(original_index, dtype=np.int64)
    refined_tags = np.asarray(refined_tags, dtype=np.int64)
    if original_nodes.ndim != 2 or original_nodes.shape[1] != 2 or refined_nodes.ndim != 2 or refined_nodes.shape[1] != 2:
        raise ValueError("node coordinate shape mismatch")
    if original_triangles.ndim != 2 or original_triangles.shape[1] != 3 or refined_triangles.ndim != 2 or refined_triangles.shape[1] != 3:
        raise ValueError("triangle shape mismatch")
    if refined_nodes.shape[0] < original_nodes.shape[0] or not np.array_equal(refined_nodes[: original_nodes.shape[0]], original_nodes):
        raise ValueError("refined mesh does not preserve the original node prefix")
    if original_index.shape != (refined_triangles.shape[0],) or np.any(original_index < 0) or np.any(original_index >= original_triangles.shape[0]):
        raise ValueError("invalid original triangle ancestry")
    if refined_tags.shape != original_index.shape or original_tags.shape != (original_triangles.shape[0],):
        raise ValueError("triangle contact tag shape mismatch")
    if not np.array_equal(refined_tags, original_tags[original_index]):
        raise ValueError("refined triangle contact tags are not inherited from original parents")
    original_vertices = original_nodes[original_triangles]
    refined_vertices = refined_nodes[refined_triangles]
    original_signed = ((original_vertices[:, 1] - original_vertices[:, 0])[:, 0] * (original_vertices[:, 2] - original_vertices[:, 0])[:, 1] - (original_vertices[:, 1] - original_vertices[:, 0])[:, 1] * (original_vertices[:, 2] - original_vertices[:, 0])[:, 0])
    refined_signed = ((refined_vertices[:, 1] - refined_vertices[:, 0])[:, 0] * (refined_vertices[:, 2] - refined_vertices[:, 0])[:, 1] - (refined_vertices[:, 1] - refined_vertices[:, 0])[:, 1] * (refined_vertices[:, 2] - refined_vertices[:, 0])[:, 0])
    if np.any(original_signed == 0.0) or np.any(refined_signed == 0.0):
        raise ValueError("degenerate original/refined triangle")
    if not np.all(np.sign(refined_signed) == np.sign(original_signed[original_index])):
        raise ValueError("refined triangle winding differs from original parent")
    multiplicity = np.bincount(original_index, minlength=original_triangles.shape[0])
    if np.any(multiplicity == 0):
        raise ValueError("refined ancestry has missing original parents")
    changed_parents = np.flatnonzero(multiplicity > 1)
    unchanged_parents = np.flatnonzero(multiplicity == 1)
    changed_children = np.flatnonzero(multiplicity[original_index] > 1)
    unchanged_children = np.flatnonzero(multiplicity[original_index] == 1)
    if unchanged_children.size != unchanged_parents.size:
        raise ValueError("unchanged ancestry cardinality mismatch")
    if unchanged_children.size:
        if not np.all(refined_triangles[unchanged_children] == original_triangles[original_index[unchanged_children]]):
            raise ValueError("sole unchanged child differs from original triangle")
    original_area = np.abs(original_signed) / 2.0
    refined_area = np.abs(refined_signed) / 2.0
    collapsed_area = np.bincount(original_index, weights=refined_area, minlength=original_triangles.shape[0])
    area_relative_error = np.abs(collapsed_area - original_area) / np.maximum(original_area, 1e-30)
    if not np.all(np.isfinite(area_relative_error)) or float(np.max(area_relative_error)) > 2e-9:
        raise ValueError(f"parent area is not preserved: {float(np.max(area_relative_error))}")
    return {
        "multiplicity": multiplicity,
        "changed_parents": changed_parents,
        "unchanged_parents": unchanged_parents,
        "changed_children": changed_children,
        "unchanged_children": unchanged_children,
        "original_index": original_index,
        "original_area_um2": original_area,
        "refined_area_um2": refined_area,
        "area_relative_error": area_relative_error,
        "original_signed_twice_area": original_signed,
        "refined_signed_twice_area": refined_signed,
        "winding_mismatch_count": 0,
    }


def self_check():
    # A cut through the parent disproves proportional child-area transfer.
    refiner = module("refiner")
    points = np.array([[0., 0.], [2., 0.], [0., 2.]])
    boundary = shapely.box(-1., -1., .75, 3.)
    for triangle in (np.array([[0, 1, 2]]), np.array([[0, 2, 1]])):
        p, t, parents, _, _ = refiner.refine_marked_edges(points, triangle, np.array([[0, 1], [1, 2], [0, 2]]))
        child = cut_areas(boundary, p[t])
        original = cut_areas(boundary, points[triangle])
        assert np.allclose(np.bincount(parents, weights=child), original, rtol=0, atol=1e-14)
        assert not np.allclose(child, original[0] / 4)
    original_nodes = np.array([[0., 0.], [2., 0.], [2., 2.], [0., 2.]])
    original_triangles = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    original_tags = np.array([7, -1], dtype=np.int64)
    refined_nodes = np.vstack((original_nodes, [[1., 0.], [2., 1.], [1., 1.]]))
    refined_triangles = np.array([[0, 4, 6], [4, 1, 5], [6, 5, 2], [4, 5, 6], [0, 2, 3]], dtype=np.int64)
    ancestry = np.array([0, 0, 0, 0, 1], dtype=np.int64)
    refined_tags = np.array([7, 7, 7, 7, -1], dtype=np.int64)
    contract = _ancestry_contract(original_nodes, original_triangles, original_tags, refined_nodes, refined_triangles, ancestry, refined_tags)
    assert np.array_equal(contract["changed_parents"], np.array([0]))
    assert np.array_equal(contract["unchanged_parents"], np.array([1]))
    assert np.array_equal(contract["changed_children"], np.array([0, 1, 2, 3]))
    assert np.array_equal(contract["unchanged_children"], np.array([4]))
    assert float(np.max(contract["area_relative_error"])) == 0.0
    print(PROGRAM + ": SELF_CHECK PASS (cut-cell area, CW/CCW, generic ancestry transfer)")


def run(output):
    started = time.perf_counter()
    base = module("budget")
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(output / "progress.jsonl")
    watchdog = budget.start_watchdog()
    try:
        for path, expected in PINS.values():
            pin(path, expected)
        mesh, selection, saved = npz("mesh"), npz("selection"), npz("areas")
        inventory = json.loads(PINS["inventory"][0].read_bytes())
        assets = json.loads(PINS["assets"][0].read_bytes())
        refiner, decoder = module("refiner"), module("decoder")
        p, t, parents, edges, coverage = refiner.refine_marked_edges(mesh["node_xy_um"], mesh["triangles"], selection["marked_mesh_edges_90"])
        if len(edges) != 132 or np.any(coverage != 2) or p.shape != (535281, 2) or t.shape != (542355, 3):
            raise ValueError("frozen 90-percent geometry contract differs")
        multiplicity = np.bincount(parents, minlength=len(mesh["triangles"]))
        affected = np.flatnonzero(multiplicity > 1)
        changed = np.flatnonzero(multiplicity[parents] > 1)
        keep = np.flatnonzero(multiplicity[parents] == 1)
        if len(affected) != 168 or len(changed) != 432 or np.any(saved["triangle_contact_index"][affected] >= 0):
            raise ValueError("changed parent/contact contract differs")
        old = sparse.csr_matrix((saved["data_um2"], saved["indices"], saved["indptr"]), shape=tuple(saved["shape"]))
        bindings = json.loads(saved["owner_bindings_json_utf8"].tobytes())
        owners = [owner for partial in inventory["original_gc"]["partials"] for owner in partial["owners"]]
        if [x["fingerprint"] for x in owners] != [x["fingerprint"] for x in bindings]:
            raise ValueError("source G/C owner ordering differs")
        vertices = p[t[changed]]
        parent_vertices = mesh["node_xy_um"][mesh["triangles"][affected]]
        bounds = shapely.box(*np.r_[vertices.min(axis=(0, 1)), vertices.max(axis=(0, 1))])
        geometry_receipts = []

        def polygon(record, path, digest, island):
            pin(path, digest)
            shapes = decoder.polygon_map(record["layer"], record["net"], digest, path.read_bytes())
            geometry_receipts.append({"path": str(path), "sha256": digest, "island": island})
            return shapely.intersection(shapes[island], bounds)

        target = inventory["target_mapping"]["source_geometry_asset"]
        target_path = R / "astra-l25-source-sheet-01/0247-c80867ceb7f82d7b.spdgeom.zlib"
        target_shape = polygon(target, target_path, target["asset_sha256"], target["island_ids"][0])
        members = {x["external_island_id"]: x for x in assets["members"]}
        child_areas = np.zeros((4, len(changed)))
        checks = []
        for index, owner in enumerate(owners):
            member = members[owner["external_island_id"]]
            path = PINS["assets"][0].parent / Path(member["path"]).name
            external = polygon(member, path, member["sha256"], owner["external_island_id"])
            overlap = shapely.intersection(target_shape, external)
            parent_cut = cut_areas(overlap, parent_vertices)
            child_areas[index] = cut_areas(overlap, vertices)
            collapsed = np.bincount(parents[changed], weights=child_areas[index], minlength=old.shape[1])[affected]
            stored = old[index, affected].toarray().ravel()
            scale = np.maximum(abs(refiner.signed_twice_areas(mesh["node_xy_um"], mesh["triangles"][affected])) / 2, 1e-30)
            source_error = float(np.max(abs(parent_cut - stored) / scale))
            child_error = float(np.max(abs(collapsed - parent_cut) / scale))
            if source_error > 2e-9 or child_error > 2e-9:
                raise ValueError(f"source parent/child area mismatch: {index}, {source_error}, {child_error}")
            checks.append({"owner": index, "parent_vs_saved_relative_to_cell_area": source_error, "child_collapse_relative_to_cell_area": child_error})
            budget.emit("owner_clipped", **checks[-1])
            budget.check("source_clipping")
        unchanged = old[:, parents[keep]].tocoo()
        row, col = np.nonzero(child_areas)
        new = sparse.coo_matrix((np.r_[unchanged.data, child_areas[row, col]], (np.r_[unchanged.row, row], np.r_[keep[unchanged.col], changed[col]])), shape=(4, len(t))).tocsr()
        new.sum_duplicates(); new.sort_indices()
        capacitance = np.asarray(new.sum(axis=1)).ravel() * saved["owner_density_f_per_um2"]
        expected = np.array([x["capacitance_f"] for x in bindings])
        cap_error = float(np.max(abs(capacitance - expected) / expected))
        if cap_error > 2e-12:
            raise ValueError(f"source scalar capacitance changed: {cap_error}")
        arrays = {"data_um2": new.data, "indices": new.indices, "indptr": new.indptr, "shape": np.array(new.shape), "parent_triangle_index": parents, "triangle_contact_index": saved["triangle_contact_index"][parents], "owner_density_f_per_um2": saved["owner_density_f_per_um2"], "owner_bindings_json_utf8": saved["owner_bindings_json_utf8"]}
        with (output / "owner-cell-area.npz").open("xb") as handle:
            np.savez_compressed(handle, **arrays)
        result = {"program": PROGRAM, "status": "COMPLETED_SOURCE_CLIPPED_L25_REFINED_GC_AREAS", "inputs": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items()}, "geometry_inputs": geometry_receipts, "changed_parent_count": len(affected), "child_count": len(changed), "refined_node_count": len(p), "refined_triangle_count": len(t), "geometry_array_sha256": {"node_xy_um": hashlib.sha256(p.tobytes()).hexdigest(), "triangles": hashlib.sha256(t.tobytes()).hexdigest()}, "checks": checks, "capacitance_relative_error": cap_error, "driver": base._file_receipt(output / "driver-at-run.py"), "output": base._file_receipt(output / "owner-cell-area.npz"), "budget": {"max_runtime_s": base.MAX_RUNTIME_S, "max_rss_bytes": base.MAX_RSS_BYTES, "peak_working_set_bytes": budget.peak_working_set, "peak_private_bytes": budget.peak_private}, "elapsed_s": time.perf_counter() - started, "limitations": ["Exact source-overlap area transfer for this frozen refinement only; no new global G/C matrix, frequency solve, magnetic operator or PowerSI accuracy result."]}
        base._write_json_exclusive(output / "result.json", result)
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "capacitance_relative_error": cap_error}))
    except Exception as exc:
        base._write_json_exclusive(output / "failure.json", {"program": PROGRAM, "status": "STOP_REFINED_GC_AREA", "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        budget.stop.set(); watchdog.join(timeout=5)


def run_refined(output):
    started = time.perf_counter()
    base = module("budget")
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(output / "progress.jsonl")
    watchdog = budget.start_watchdog()
    try:
        for path, expected in PINS.values():
            pin(path, expected)
        for path, expected in REFINED_PINS.values():
            pin(path, expected)
        old_mesh, saved = npz("mesh"), npz("areas")
        with np.load(REFINED_PINS["mesh"][0], allow_pickle=False) as archive:
            refined_mesh = {key: np.asarray(archive[key]) for key in archive.files}
        step_result = json.loads(REFINED_PINS["step_result"][0].read_bytes())
        adaptive_result = json.loads(REFINED_PINS["adaptive_result"][0].read_bytes())
        if step_result.get("status") != "COMPLETED_CONDITIONAL_ADAPTIVE_STEP":
            raise ValueError("refined step result is not completed")
        if adaptive_result.get("status") != "STOP_MAX_NEW_NODES":
            raise ValueError("adaptive history result status changed")
        ancestry = _ancestry_contract(old_mesh["node_xy_um"], old_mesh["triangles"], saved["triangle_contact_index"], refined_mesh["node_xy_um"], refined_mesh["triangles"], refined_mesh["original_triangle_index"], refined_mesh["triangle_contact_tag"])
        original_index = ancestry["original_index"]
        changed_parents = ancestry["changed_parents"]
        changed_children = ancestry["changed_children"]
        unchanged_parents = ancestry["unchanged_parents"]
        unchanged_children = ancestry["unchanged_children"]
        old_triangle_count = int(old_mesh["triangles"].shape[0])
        changed_parent_tags = saved["triangle_contact_index"][changed_parents]
        changed_contact_parent_count = int(np.count_nonzero(changed_parent_tags >= 0))
        changed_noncontact_parent_count = int(changed_parents.size - changed_contact_parent_count)
        changed_contact_child_count = int(np.count_nonzero(refined_mesh["triangle_contact_tag"][changed_children] >= 0))
        if changed_parents.size == 0 or changed_children.size == 0 or changed_contact_parent_count == 0:
            raise ValueError("refined ancestry has no changed/contact parent coverage")
        old = sparse.csr_matrix((saved["data_um2"], saved["indices"], saved["indptr"]), shape=tuple(saved["shape"]))
        bindings = json.loads(saved["owner_bindings_json_utf8"].tobytes())
        inventory = json.loads(PINS["inventory"][0].read_bytes())
        assets = json.loads(PINS["assets"][0].read_bytes())
        owners = [owner for partial in inventory["original_gc"]["partials"] for owner in partial["owners"]]
        if [x["fingerprint"] for x in owners] != [x["fingerprint"] for x in bindings]:
            raise ValueError("source G/C owner ordering differs")
        decoder = module("decoder")
        changed_vertices = refined_mesh["node_xy_um"][refined_mesh["triangles"][changed_children]]
        parent_vertices = old_mesh["node_xy_um"][old_mesh["triangles"][changed_parents]]
        bounds = shapely.box(*np.r_[changed_vertices.min(axis=(0, 1)), changed_vertices.max(axis=(0, 1))])
        geometry_receipts = []

        def polygon(record, path, digest, island):
            pin(path, digest)
            shapes = decoder.polygon_map(record["layer"], record["net"], digest, path.read_bytes())
            geometry_receipts.append({"path": str(path), "sha256": digest, "island": island})
            return shapely.intersection(shapes[island], bounds)

        target = inventory["target_mapping"]["source_geometry_asset"]
        target_path = R / "astra-l25-source-sheet-01/0247-c80867ceb7f82d7b.spdgeom.zlib"
        target_shape = polygon(target, target_path, target["asset_sha256"], target["island_ids"][0])
        members = {x["external_island_id"]: x for x in assets["members"]}
        child_areas = np.zeros((len(owners), changed_children.size), dtype=np.float64)
        checks = []
        for index, owner in enumerate(owners):
            member = members[owner["external_island_id"]]
            path = PINS["assets"][0].parent / Path(member["path"]).name
            external = polygon(member, path, member["sha256"], owner["external_island_id"])
            overlap = shapely.intersection(target_shape, external)
            parent_cut = cut_areas(overlap, parent_vertices)
            child_areas[index] = cut_areas(overlap, changed_vertices)
            collapsed = np.bincount(original_index[changed_children], weights=child_areas[index], minlength=old_triangle_count)[changed_parents]
            stored = old[index, changed_parents].toarray().ravel()
            scale = np.maximum(ancestry["original_area_um2"][changed_parents], 1e-30)
            source_error = float(np.max(abs(parent_cut - stored) / scale))
            child_error = float(np.max(abs(collapsed - parent_cut) / scale))
            if source_error > 2e-9 or child_error > 2e-9:
                raise ValueError(f"source parent/child area mismatch: {index}, {source_error}, {child_error}")
            checks.append({"owner": index, "changed_parent_count": int(changed_parents.size), "changed_child_count": int(changed_children.size), "parent_vs_saved_relative_to_parent_area": source_error, "child_collapse_relative_to_parent_area": child_error})
            budget.emit("refined_owner_clipped", **checks[-1])
            budget.check("refined_source_clipping")
        child_for_parent = np.full(old_triangle_count, -1, dtype=np.int64)
        child_for_parent[original_index[unchanged_children]] = unchanged_children
        unchanged_coo = old[:, unchanged_parents].tocoo()
        unchanged_child_columns = child_for_parent[unchanged_parents[unchanged_coo.col]]
        if np.any(unchanged_child_columns < 0):
            raise ValueError("unchanged parent to child mapping missing")
        child_row, child_col = np.nonzero(child_areas)
        new = sparse.coo_matrix((np.r_[unchanged_coo.data, child_areas[child_row, child_col]], (np.r_[unchanged_coo.row, child_row], np.r_[unchanged_child_columns, changed_children[child_col]])), shape=(len(owners), refined_mesh["triangles"].shape[0])).tocsr()
        new.sum_duplicates(); new.sort_indices()
        capacitance = np.asarray(new.sum(axis=1)).ravel() * saved["owner_density_f_per_um2"]
        expected = np.array([x["capacitance_f"] for x in bindings])
        cap_error = float(np.max(abs(capacitance - expected) / expected))
        if cap_error > 2e-12:
            raise ValueError(f"source scalar capacitance changed: {cap_error}")
        arrays = {"data_um2": new.data, "indices": new.indices, "indptr": new.indptr, "shape": np.array(new.shape), "original_triangle_index": refined_mesh["original_triangle_index"], "parent_triangle_index": refined_mesh["parent_triangle_index"], "triangle_contact_index": refined_mesh["triangle_contact_tag"], "owner_density_f_per_um2": saved["owner_density_f_per_um2"], "owner_bindings_json_utf8": saved["owner_bindings_json_utf8"]}
        with (output / "owner-cell-area.npz").open("xb") as handle:
            np.savez_compressed(handle, **arrays)
        result = {"program": PROGRAM, "status": "COMPLETED_SOURCE_CLIPPED_L25_REFINED_GC_AREAS_GENERIC_ANCESTRY", "inputs": {"legacy": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items()}, "refined": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in REFINED_PINS.items()}}, "geometry_inputs": geometry_receipts, "ancestry": {"coordinate_units": "node_xy_um; triangle areas are um2", "original_node_count": int(old_mesh["node_xy_um"].shape[0]), "refined_node_count": int(refined_mesh["node_xy_um"].shape[0]), "original_triangle_count": old_triangle_count, "refined_triangle_count": int(refined_mesh["triangles"].shape[0]), "changed_parent_count": int(changed_parents.size), "changed_child_count": int(changed_children.size), "unchanged_parent_count": int(unchanged_parents.size), "unchanged_child_count": int(unchanged_children.size), "missing_parent_count": 0, "changed_contact_parent_count": changed_contact_parent_count, "changed_noncontact_parent_count": changed_noncontact_parent_count, "changed_contact_child_count": changed_contact_child_count, "refined_contact_triangle_count": int(np.count_nonzero(refined_mesh["triangle_contact_tag"] >= 0)), "old_node_prefix_exact": True, "triangle_contact_tag_inherited": True, "winding_mismatch_count": int(ancestry["winding_mismatch_count"]), "parent_area_max_relative_error": float(np.max(ancestry["area_relative_error"]))}, "checks": checks, "geometry_array_sha256": {"node_xy_um": hashlib.sha256(np.ascontiguousarray(refined_mesh["node_xy_um"]).tobytes()).hexdigest(), "triangles": hashlib.sha256(np.ascontiguousarray(refined_mesh["triangles"]).tobytes()).hexdigest()}, "capacitance_relative_error": cap_error, "driver": base._file_receipt(output / "driver-at-run.py"), "output": base._file_receipt(output / "owner-cell-area.npz"), "budget": {"max_runtime_s": base.MAX_RUNTIME_S, "max_rss_bytes": base.MAX_RSS_BYTES, "peak_working_set_bytes": budget.peak_working_set, "peak_private_bytes": budget.peak_private}, "elapsed_s": time.perf_counter() - started, "limitations": ["Exact source-overlap area transfer for every changed original parent and all refined children in this frozen saved mesh; no new global G/C matrix, frequency solve, magnetic operator or PowerSI accuracy result."]}
        base._write_json_exclusive(output / "result.json", result)
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "changed_parent_count": int(changed_parents.size), "changed_child_count": int(changed_children.size), "capacitance_relative_error": cap_error}))
    except Exception as exc:
        base._write_json_exclusive(output / "failure.json", {"program": PROGRAM, "status": "STOP_REFINED_GC_AREA", "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        budget.stop.set(); watchdog.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=PROGRAM)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output-root", type=Path, default=R / "astra-l25-refined-gc-areas-01")
    parser.add_argument("--refined-mesh", action="store_true", help="use the pinned saved adaptive step-06 mesh with generic ancestry")
    parser.add_argument("--refined-output-root", type=Path, default=R / "astra-l25-refined-gc-areas-02")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.refined_mesh:
        run_refined(args.refined_output_root.resolve())
    else:
        run(args.output_root.resolve())
