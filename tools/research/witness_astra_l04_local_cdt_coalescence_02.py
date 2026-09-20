"""Released conditional exact-coordinate L04 coalescence witness using local contact controls only."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

import project_astra_l14_gc_mass as mass


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OUTPUT_NAME = "astra-l04-cdt-local-coalescence-witness-02"
RUN_RELEASED = True
COOPERATIVE_SECONDS = 60.0
EXTERNAL_SECONDS = 90.0
RAW = R / "astra-l04-conditional-sheet-mesh-01/unvalidated-l04-large-face-cdt.wkb"
DOMAIN = R / "astra-l04-pad-conductor-domain-01/l04-pad-augmented-conductor-domain.wkb"
DEGENERACY = R / "astra-l04-cdt-degeneracy-diagnostic-01/l04-raw-cdt-degeneracy.npz"
SUPPORT_MAP = R / "astra-l04-pad-conductor-domain-01/l04-pad-drill-support-map.npz"
CURRENT = R / "astra-l04-fixed-contact-currents-01/l04-fixed-contact-currents.npz"
WITNESS01 = R / "astra-l04-cdt-local-coalescence-witness-01"
PINS = {
    "raw_cdt": (RAW, "ec2850aafd627f40368872b20276b0e1c38ecc0db9513550c34d484079542e96"),
    "pad_domain": (DOMAIN, "0eeaffc34a6b9759fd285f28d35ebd59042e6bcd909a697fad3f18a5d2eafa68"),
    "degeneracy": (DEGENERACY, "9b434bdac976cef35257388217848c4ee5cb139e0ebc6651dc629646eaa2878c"),
    "support_map": (SUPPORT_MAP, "7c76d3e61680d2092bdb3ebe53a03fd52030de478da570a35c4ce09baafc92ff"),
    "fixed_current": (CURRENT, "5c2fd3bd30a45d9d92ccc47a5ad2d553f0d122acb4282a1046be09d7b4e51adb"),
    "witness01_driver": (WITNESS01 / "driver-at-run.py", "79c75cf72e46f2ad98e1e4ef7cc2e531e5ff79e81ee9e4e7ee583feb6297f26e"),
    "witness01_failure": (WITNESS01 / "failure.json", "a0896265c2a8b364fcef4a60509f71af305e1c4ab5c086dd932c20f702a0adb8"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "guarded_source_worker": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "persistence": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
OLD = np.asarray([[-12596.299999999852, 13546.300000000001], [-12596.300000000147, 13546.300000000001]])
TARGET = np.asarray([-12596.3, 13546.3])
RING_INDEX = 563
RING_POSITIONS = np.asarray([42, 44], dtype=np.int64)
EXPECTED_REMOVED = np.asarray([807731, 976112], dtype=np.int64)


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def verify_pins() -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for name, (path, digest) in PINS.items():
        require(path.is_file() and sha(path) == digest, f"pinned {name}")
        values[name] = receipt(path)
    return values


def load_budget():
    helper, digest = PINS["budget_helper"]
    require(sha(helper) == digest, "pinned budget helper")
    runtime = str(ROOT / "outputs" / "research-runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import reconstruct_astra_native_loaded_field as recon
    require(Path(recon.__file__).resolve() == helper.resolve(), "Budget import location")
    return recon._Budget.create(COOPERATIVE_SECONDS, 4.0)


def geometry_limits(domain) -> tuple[float, float, float]:
    bounds = np.asarray(domain.bounds, dtype=np.float64)
    scale = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    native_tolerance = max(domain.area * 2e-11, np.finfo(np.float64).eps * scale * scale * 512.0)
    return float(scale), float(native_tolerance * 1e-6), float(np.finfo(np.float64).eps * scale * scale * 32.0)


def changed_domain(domain):
    old_rings = [np.asarray(domain.exterior.coords, dtype=np.float64)]
    old_rings.extend(np.asarray(ring.coords, dtype=np.float64) for ring in domain.interiors)
    old_ring = old_rings[RING_INDEX]
    require(np.array_equal(old_ring[42], OLD[0]) and np.array_equal(old_ring[44], OLD[1]) and np.array_equal(old_ring[43], TARGET), "listed ring563 coordinates")
    new_rings = list(old_rings)
    new_ring = old_ring.copy()
    new_ring[RING_POSITIONS] = TARGET
    new_rings[RING_INDEX] = new_ring
    require(all(old_rings[index].tobytes() == new_rings[index].tobytes() for index in range(len(old_rings)) if index != RING_INDEX), "all non-563 domain rings byte-identical")
    patched = Polygon(new_rings[0], new_rings[1:])
    require(patched.is_valid and patched.geom_type == "Polygon", "exact copied domain remains valid")
    return patched, old_ring, new_ring


def aabb_union(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return np.asarray([min(float(first[:, 0].min()), float(second[:, 0].min())), min(float(first[:, 1].min()), float(second[:, 1].min())), max(float(first[:, 0].max()), float(second[:, 0].max())), max(float(first[:, 1].max()), float(second[:, 1].max()))])


def identity_and_candidates(local_bbox: np.ndarray):
    with np.load(SUPPORT_MAP, allow_pickle=False) as supports:
        support_xy_pm = np.asarray(supports["support_xy_pm"], dtype=np.int64)
        support_diameter_pm = np.asarray(supports["support_diameter_pm"], dtype=np.int64)
        drill = np.asarray(supports["drill_support_index"], dtype=np.int64)
        support_ordinal = np.asarray(supports["source_via_ordinal"], dtype=np.int64)
        support_group = np.asarray(supports["coincident_group_index"], dtype=np.int64)
    with np.load(CURRENT, allow_pickle=False) as currents:
        current_ordinal = np.asarray(currents["source_via_ordinal"], dtype=np.int64)
        current_group = np.asarray(currents["coincident_group_index"], dtype=np.int64)
        group_current = np.asarray(currents["coincident_group_current_outward_from_l04_sheet_a"], dtype=np.complex128)
        group_to_drill = np.asarray(currents["group_to_drill_support_index"], dtype=np.int64)
    require(np.array_equal(support_ordinal, current_ordinal) and np.array_equal(support_group, current_group), "pinned source current/group identity")
    require(len(drill) == 38278 and len(np.unique(drill)) == len(drill), "exact unique drill supports")
    require(len(group_to_drill) == len(group_current) == len(drill) and len(np.unique(group_to_drill)) == len(group_to_drill), "one group-to-drill support map")
    require(np.array_equal(np.sort(group_to_drill), np.sort(drill)), "group-to-drill support set equals pinned drill support set")
    xy_um = support_xy_pm[drill].astype(np.float64) * 1e-6
    radius_um = support_diameter_pm[drill].astype(np.float64) * 0.5e-6
    intersects = ((xy_um[:, 0] + radius_um >= local_bbox[0]) & (xy_um[:, 0] - radius_um <= local_bbox[2]) & (xy_um[:, 1] + radius_um >= local_bbox[1]) & (xy_um[:, 1] - radius_um <= local_bbox[3]))
    selected = np.flatnonzero(intersects).astype(np.int64, copy=False)
    group_order = np.argsort(group_to_drill)
    sorted_drill = group_to_drill[group_order]
    selected_group = group_order[np.searchsorted(sorted_drill, drill[selected])]
    require(np.array_equal(group_to_drill[selected_group], drill[selected]), "selected drill-to-current-group permutation")
    return {"support_xy_um": xy_um, "radius_um": radius_um, "selected": selected, "support_ordinal": support_ordinal, "support_group": support_group, "current_ordinal": current_ordinal, "current_group": current_group, "group_current": group_current, "drill_support_index": drill, "intersects": intersects, "group_to_drill_support_index": group_to_drill, "selected_current_group_index": selected_group}


def identity_checkpoint(path: Path, data: dict[str, np.ndarray], local_bbox: np.ndarray) -> None:
    selected = data["selected"]
    groups = data["selected_current_group_index"]
    mass.atomic_npz(path, support_source_via_ordinal=data["support_ordinal"], support_coincident_group_index=data["support_group"], current_source_via_ordinal=data["current_ordinal"], current_coincident_group_index=data["current_group"], drill_support_index=data["drill_support_index"], group_to_drill_support_index=data["group_to_drill_support_index"], selected_drill_ordinal=selected, selected_drill_support_index=data["drill_support_index"][selected], selected_current_group_index=groups, selected_group_current_outward_from_l04_sheet_a=data["group_current"][groups], local_changed_ring_bbox_um=local_bbox)


def local_contact_controls(original_domain, patched_domain, data: dict[str, np.ndarray], local_bbox: np.ndarray) -> dict[str, object]:
    selected = data["selected"]
    xy_um = data["support_xy_um"][selected]
    radius_um = data["radius_um"][selected]
    circles = shapely.buffer(shapely.points(xy_um), radius_um, quad_segs=4)
    shapely.prepare(original_domain)
    shapely.prepare(patched_domain)
    old_covered = np.asarray(shapely.covers(original_domain, circles), dtype=np.bool_)
    new_covered = np.asarray(shapely.covers(patched_domain, circles), dtype=np.bool_)
    contact_vertices = shapely.get_coordinates(circles)
    mapped_vertex = np.any(np.all(contact_vertices[:, None, :] == OLD[None, :, :], axis=2))
    unselected = ~data["intersects"]
    x = data["support_xy_um"][:, 0]
    y = data["support_xy_um"][:, 1]
    radius = data["radius_um"]
    separated = ((x[unselected] + radius[unselected] < local_bbox[0]) | (x[unselected] - radius[unselected] > local_bbox[2]) | (y[unselected] + radius[unselected] < local_bbox[1]) | (y[unselected] - radius[unselected] > local_bbox[3]))
    require(np.all(old_covered) and np.all(new_covered), "selected contacts strictly covered before and after")
    require(not mapped_vertex and np.all(separated), "unselected contacts AABB-disjoint from exact ring change")
    require(np.all(np.isfinite(data["group_current"])), "saved source currents finite")
    shapely.destroy_prepared(original_domain)
    shapely.destroy_prepared(patched_domain)
    return {"drill_support_count": int(len(data["drill_support_index"])), "selected_contact_count": int(len(selected)), "unselected_contact_count": int(unselected.sum()), "selected_covered_before": int(old_covered.sum()), "selected_covered_after": int(new_covered.sum()), "unselected_aabb_disjoint": True, "contact_vertex_exact_coordinate_remap_count": 0, "source_current_group_identity_bitwise": True}


def local_geometry_metrics(original_domain, patched_domain, old_ring: np.ndarray, new_ring: np.ndarray, original_patch, patched_patch) -> dict[str, float | bool | str]:
    old_ring_area = float(Polygon(old_ring).area)
    new_ring_area = float(Polygon(new_ring).area)
    ring_symdiff = Polygon(old_ring).symmetric_difference(Polygon(new_ring))
    patch_symdiff = original_patch.symmetric_difference(patched_patch)
    local_scale = max(1.0, old_ring_area, new_ring_area, float(original_patch.area), float(patched_patch.area))
    operand_bound = 128.0 * np.finfo(np.float64).eps * local_scale
    domain_area_delta = float(patched_domain.area - original_domain.area)
    expected_domain_delta = -(new_ring_area - old_ring_area)
    require(abs(domain_area_delta - expected_domain_delta) <= operand_bound, "domain area change is exact ring563 hole change")
    require(abs(float(ring_symdiff.area) - float(patch_symdiff.area)) <= operand_bound, "incident patch change matches local ring change")
    displacement = np.linalg.norm(old_ring[RING_POSITIONS] - TARGET, axis=1)
    return {"changed_domain_wkb_sha256": digest_bytes(shapely.to_wkb(patched_domain)), "domain_area_delta_um2": domain_area_delta, "ring_symmetric_difference_area_um2": float(ring_symdiff.area), "patch_symmetric_difference_area_um2": float(patch_symdiff.area), "local_operand_bound_um2": operand_bound, "ring_hausdorff_distance_um": float(LineString(old_ring).hausdorff_distance(LineString(new_ring))), "max_vertex_displacement_um": float(displacement.max()), "domain_valid": bool(patched_domain.is_valid), "bounds_equal": bool(original_domain.bounds == patched_domain.bounds), "component_count_equal": True, "hole_count_equal": bool(len(original_domain.interiors) == len(patched_domain.interiors))}


def worker(output: Path) -> dict[str, object]:
    require(output.is_dir(), "launcher-created output directory required")
    canonical = Path(__file__).resolve()
    driver = output / "driver-at-run.py"
    require(driver.is_file() and sha(driver) == sha(canonical), "frozen driver equals canonical helper")
    pins = verify_pins()
    budget = load_budget()
    try:
        with np.load(DEGENERACY, allow_pickle=False) as z:
            require(np.array_equal(z["failing_triangle_index"], EXPECTED_REMOVED), "pinned original degeneracy rows")
            require(np.array_equal(z["native_failure_threshold_um2"], np.asarray([1.874101850983222e-7])), "pinned native failure threshold")
        original_domain = shapely.from_wkb(DOMAIN.read_bytes())
        require(original_domain.is_valid and original_domain.geom_type == "Polygon", "pinned pad domain")
        patched_domain, old_ring, new_ring = changed_domain(original_domain)
        scale, native_gate, stiffness_gate = geometry_limits(patched_domain)
        require(native_gate == 1.874101850983222e-7, "native area gate")
        local_bbox = aabb_union(old_ring, new_ring)
        identity = identity_and_candidates(local_bbox)
        checkpoint = output / "l04-local-coalescence-source-identity-checkpoint.npz"
        identity_checkpoint(checkpoint, identity, local_bbox)
        budget.check("source identity checkpoint before local contact controls")
        raw = shapely.from_wkb(RAW.read_bytes())
        triangles = shapely.get_parts(raw)
        require(len(triangles) == 1_589_829 and np.all(shapely.get_num_coordinates(triangles) == 4), "saved raw triangle schema")
        coordinates = shapely.get_coordinates(triangles).reshape(len(triangles), 4, 2)
        changed_mask = np.all(coordinates == OLD[0], axis=2) | np.all(coordinates == OLD[1], axis=2)
        affected_rows, affected_vertices = np.nonzero(changed_mask)
        unique_rows = np.unique(affected_rows).astype(np.int64, copy=False)
        old_coordinates = coordinates[affected_rows, affected_vertices].copy()
        patched_local = coordinates[unique_rows].copy()
        patched_local[np.all(patched_local == OLD[0], axis=2) | np.all(patched_local == OLD[1], axis=2)] = TARGET
        require(np.all(patched_local[:, 3] == patched_local[:, 0]), "raw triangle closure retained")
        original_patch = shapely.union_all(shapely.polygons(coordinates[unique_rows, :3]))
        patched_patch = shapely.union_all(shapely.polygons(patched_local[:, :3]))
        local = coordinates[:, :3] - coordinates[:, :1]
        determinant = local[:, 1, 0] * local[:, 2, 1] - local[:, 1, 1] * local[:, 2, 0]
        area = np.abs(determinant) * 0.5
        patched_area = np.abs((patched_local[:, 1, 0] - patched_local[:, 0, 0]) * (patched_local[:, 2, 1] - patched_local[:, 0, 1]) - (patched_local[:, 1, 1] - patched_local[:, 0, 1]) * (patched_local[:, 2, 0] - patched_local[:, 0, 0])) * 0.5
        area[unique_rows] = patched_area
        repeated_local = ((patched_local[:, 0] == patched_local[:, 1]).all(axis=1) | (patched_local[:, 1] == patched_local[:, 2]).all(axis=1) | (patched_local[:, 2] == patched_local[:, 0]).all(axis=1))
        removed = unique_rows[repeated_local | (patched_area == 0.0)]
        require(np.array_equal(removed, EXPECTED_REMOVED), "only original two degenerate raw rows removed")
        retained = np.ones(len(area), dtype=np.bool_)
        retained[removed] = False
        require(np.all(area[retained] > native_gate) and np.all(area[retained] > stiffness_gate), "all retained raw triangles pass both exact gates")
        next_index = int(np.flatnonzero(retained)[np.argmin(area[retained])])
        next_area = float(area[next_index])
        metrics = local_geometry_metrics(original_domain, patched_domain, old_ring, new_ring, original_patch, patched_patch)
        geometry_checkpoint = output / "l04-local-coalescence-geometry-checkpoint.npz"
        geometry_metrics = output / "l04-local-coalescence-geometry-metrics.json"
        mass.atomic_npz(geometry_checkpoint,
            old_coordinate_um=OLD, replacement_coordinate_um=TARGET,
            domain_ring_index=np.asarray([RING_INDEX], dtype=np.int64), domain_ring_vertex_positions=RING_POSITIONS,
            local_changed_ring_bbox_um=local_bbox, raw_triangle_index=affected_rows.astype(np.int64),
            raw_triangle_vertex_index=affected_vertices.astype(np.int8), raw_triangle_old_coordinate_um=old_coordinates,
            removed_triangle_index=removed, next_retained_min_area_triangle_index=np.asarray([next_index], dtype=np.int64),
            next_retained_min_area_um2=np.asarray([next_area]), native_area_gate_um2=np.asarray([native_gate]),
            stiffness_area_gate_um2=np.asarray([stiffness_gate]))
        mass.atomic_json(geometry_metrics, {"coordinate_map": {"ring_index": RING_INDEX,
            "ring_positions": RING_POSITIONS.astype(int).tolist(), "replacement_coordinate_um": TARGET.tolist(),
            "affected_raw_occurrence_count": int(len(affected_rows)), "affected_raw_triangle_count": int(len(unique_rows)),
            "removed_triangle_rows": removed.astype(int).tolist()}, "retained_gates": {"native_area_gate_um2": native_gate,
            "stiffness_area_gate_um2": stiffness_gate, "next_minimum_area_um2": next_area,
            "next_minimum_area_triangle_index": next_index}, "local_geometry_change": metrics})
        budget.check("raw coordinate map and local patch gates")
        del raw, triangles, coordinates, patched_local, local, determinant, area
        controls = local_contact_controls(original_domain, patched_domain, identity, local_bbox)
        budget.check("selected local contact coverage controls")
        artifact = output / "l04-local-coalescence-witness.npz"
        mass.atomic_npz(artifact, old_coordinate_um=OLD, replacement_coordinate_um=TARGET, domain_ring_index=np.asarray([RING_INDEX], dtype=np.int64), domain_ring_vertex_positions=RING_POSITIONS, local_changed_ring_bbox_um=local_bbox, raw_triangle_index=affected_rows.astype(np.int64), raw_triangle_vertex_index=affected_vertices.astype(np.int8), raw_triangle_old_coordinate_um=old_coordinates, removed_triangle_index=removed, next_retained_min_area_triangle_index=np.asarray([next_index], dtype=np.int64), next_retained_min_area_um2=np.asarray([next_area]), native_area_gate_um2=np.asarray([native_gate]), stiffness_area_gate_um2=np.asarray([stiffness_gate]), selected_drill_ordinal=identity["selected"], selected_contact_covered_before=np.asarray([controls["selected_covered_before"]]), selected_contact_covered_after=np.asarray([controls["selected_covered_after"]]))
        budget.check("conditional local witness artifact")
        result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_CONDITIONAL_L04_LOCAL_COALESCENCE_WITNESS", "driver": receipt(driver), "inputs": pins, "identity_checkpoint": receipt(checkpoint), "local_geometry_checkpoint": receipt(geometry_checkpoint), "local_geometry_metrics": receipt(geometry_metrics), "artifact": receipt(artifact), "coordinate_map": {"ring_index": RING_INDEX, "ring_positions": RING_POSITIONS.astype(int).tolist(), "replacement_coordinate_um": TARGET.tolist(), "affected_raw_occurrence_count": int(len(affected_rows)), "affected_raw_triangle_count": int(len(unique_rows)), "affected_raw_triangle_indices": unique_rows.astype(int).tolist(), "removed_triangle_rows": removed.astype(int).tolist()}, "removed_raw_triangle_indices": removed.astype(int).tolist(), "retained_gates": {"native_area_gate_um2": native_gate, "stiffness_area_gate_um2": stiffness_gate, "next_minimum_area_um2": next_area, "next_minimum_area_triangle_index": next_index, "global_domain_scale_um": scale}, "local_geometry_change": metrics, "contact_controls": controls, "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)"}, "scope": "Conditional exact two-coordinate coalescence witness only. It writes no patched domain/CDT, regenerates no mesh, assembles no stiffness, solves no current, and does not qualify a mesh."}
        mass.atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        mass.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L04_CONDITIONAL_LOCAL_COALESCENCE_WITNESS", "error_type": type(error).__name__, "error": str(error), "driver": receipt(driver), "budget": budget.receipt()})
        raise


def self_check() -> None:
    old_ring = np.asarray([[0., 0.], [2., 0.], [2., 2.], [0., 2.], [0., 0.]])
    new_ring = old_ring.copy(); new_ring[1] = [1.5, 0.]
    require(np.array_equal(aabb_union(old_ring, new_ring), np.asarray([0., 0., 2., 2.])), "local bbox self-check")
    require(float(LineString(old_ring).hausdorff_distance(LineString(new_ring))) > 0.0, "ring Hausdorff self-check")
    budget = load_budget(); budget.check("local witness static self-check")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_L04_LOCAL_COALESCENCE_WITNESS_STATIC_SELF_CHECK", "run_released": RUN_RELEASED}, sort_keys=True))


def launch(output: Path) -> int:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release witness02")
    verify_pins()
    require(not output.exists(), "fresh witness02 output required")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    guard_path, guard_sha = PINS["guarded_source_worker"]
    require(sha(guard_path) == guard_sha, "pinned external guard")
    from probe_astra_fmm3d_runtime import guarded_source_worker
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output.resolve())]
    return guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=R / OUTPUT_NAME)
    args = parser.parse_args()
    require(sum((args.self_check, args.run, args.native_worker)) == 1, "select --self-check, --run, or --native-worker")
    if args.self_check:
        self_check()
    elif args.run:
        raise SystemExit(launch(args.output.resolve()))
    else:
        print(json.dumps({"status": worker(args.output.resolve())["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
