"""Conditional exact-coordinate L04 local coalescence witness; no mesh regeneration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import shapely
from shapely.geometry import Polygon

import project_astra_l14_gc_mass as mass


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OUTPUT_NAME = "astra-l04-cdt-local-coalescence-witness-01"
RAW = R / "astra-l04-conditional-sheet-mesh-01/unvalidated-l04-large-face-cdt.wkb"
DOMAIN = R / "astra-l04-pad-conductor-domain-01/l04-pad-augmented-conductor-domain.wkb"
DEGENERACY = R / "astra-l04-cdt-degeneracy-diagnostic-01/l04-raw-cdt-degeneracy.npz"
SUPPORT_MAP = R / "astra-l04-pad-conductor-domain-01/l04-pad-drill-support-map.npz"
CURRENT = R / "astra-l04-fixed-contact-currents-01/l04-fixed-contact-currents.npz"
PINS = {
    "raw_cdt": (RAW, "ec2850aafd627f40368872b20276b0e1c38ecc0db9513550c34d484079542e96"),
    "pad_domain": (DOMAIN, "0eeaffc34a6b9759fd285f28d35ebd59042e6bcd909a697fad3f18a5d2eafa68"),
    "degeneracy": (DEGENERACY, "9b434bdac976cef35257388217848c4ee5cb139e0ebc6651dc629646eaa2878c"),
    "support_map": (SUPPORT_MAP, "7c76d3e61680d2092bdb3ebe53a03fd52030de478da570a35c4ce09baafc92ff"),
    "fixed_current": (CURRENT, "5c2fd3bd30a45d9d92ccc47a5ad2d553f0d122acb4282a1046be09d7b4e51adb"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "persistence": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
OLD = np.asarray([[-12596.299999999852, 13546.300000000001], [-12596.300000000147, 13546.300000000001]])
TARGET = np.asarray([-12596.3, 13546.3])
EXPECTED_REMOVED = np.asarray([807731, 976112], dtype=np.int64)


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def verify_pins() -> dict[str, dict[str, object]]:
    values = {}
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
    return recon._Budget.create(60.0, 4.0)


def geometry_limits(domain) -> tuple[float, float, float]:
    bounds = np.asarray(domain.bounds, dtype=np.float64)
    scale = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    native_tolerance = max(domain.area * 2e-11, np.finfo(np.float64).eps * scale * scale * 512.0)
    return float(scale), float(native_tolerance * 1e-6), float(np.finfo(np.float64).eps * scale * scale * 32.0)


def changed_domain(domain):
    rings = [np.asarray(domain.exterior.coords, dtype=np.float64)] + [np.asarray(ring.coords, dtype=np.float64) for ring in domain.interiors]
    ring = rings[563].copy()
    require(np.array_equal(ring[42], OLD[0]) and np.array_equal(ring[44], OLD[1]) and np.array_equal(ring[43], TARGET), "listed ring563 coordinates")
    ring[42] = TARGET
    ring[44] = TARGET
    rings[563] = ring
    return Polygon(rings[0], rings[1:]), np.asarray([42, 44], dtype=np.int64)


def contact_controls(domain):
    with np.load(SUPPORT_MAP, allow_pickle=False) as supports:
        support_xy_pm = np.asarray(supports["support_xy_pm"], dtype=np.int64)
        support_diameter_pm = np.asarray(supports["support_diameter_pm"], dtype=np.int64)
        drill = np.asarray(supports["drill_support_index"], dtype=np.int64)
        source_ordinal = np.asarray(supports["source_via_ordinal"], dtype=np.int64)
        source_group = np.asarray(supports["coincident_group_index"], dtype=np.int64)
    with np.load(CURRENT, allow_pickle=False) as currents:
        current_ordinal = np.asarray(currents["source_via_ordinal"], dtype=np.int64)
        current_group = np.asarray(currents["coincident_group_index"], dtype=np.int64)
        group_current = np.asarray(currents["coincident_group_current_outward_from_l04_sheet_a"], dtype=np.complex128)
    require(np.array_equal(source_ordinal, current_ordinal) and np.array_equal(source_group, current_group), "saved source current/group identity")
    require(len(drill) == 38278 and len(np.unique(drill)) == len(drill), "exact drill supports")
    xy_um = support_xy_pm[drill].astype(np.float64) * 1e-6
    radii_um = support_diameter_pm[drill].astype(np.float64) * 0.5e-6
    circles = shapely.buffer(shapely.points(xy_um), radii_um, quad_segs=4)
    covered = np.asarray(shapely.covers(domain, circles), dtype=np.bool_)
    vertices = shapely.get_coordinates(circles)
    target_hit = np.any(np.all(vertices[:, None, :] == OLD[None, :, :], axis=2))
    require(np.all(covered) and not target_hit and np.all(np.isfinite(group_current)), "contact coverage/current ordinals untouched")
    return {"drill_support_count": int(len(drill)), "covered_contact_count": int(covered.sum()), "contact_vertex_target_hit": bool(target_hit),
            "source_current_group_identity_bitwise": True}


def worker(output: Path) -> dict[str, object]:
    require(not output.exists(), "fresh witness output required")
    pins = verify_pins()
    budget = load_budget()
    output.mkdir(parents=True)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    try:
        with np.load(DEGENERACY, allow_pickle=False) as z:
            require(np.array_equal(z["failing_triangle_index"], EXPECTED_REMOVED), "pinned original degeneracy rows")
            require(np.array_equal(z["native_failure_threshold_um2"], np.asarray([1.874101850983222e-7])), "pinned native failure threshold")
        original_domain = shapely.from_wkb(DOMAIN.read_bytes())
        patched_domain, ring_positions = changed_domain(original_domain)
        require(patched_domain.is_valid and patched_domain.geom_type == "Polygon", "changed domain validity")
        scale, native_gate, stiffness_gate = geometry_limits(patched_domain)
        require(native_gate == 1.874101850983222e-7, "native area gate")
        raw = shapely.from_wkb(RAW.read_bytes())
        triangles = shapely.get_parts(raw)
        require(len(triangles) == 1_589_829 and np.all(shapely.get_num_coordinates(triangles) == 4), "saved raw triangle schema")
        coordinates = shapely.get_coordinates(triangles).reshape(len(triangles), 4, 2)
        match0 = np.all(coordinates == OLD[0], axis=2)
        match1 = np.all(coordinates == OLD[1], axis=2)
        changed_mask = match0 | match1
        affected_rows, affected_vertices = np.nonzero(changed_mask)
        old_coordinates = coordinates[affected_rows, affected_vertices].copy()
        patched_coordinates = coordinates.copy()
        patched_coordinates[changed_mask] = TARGET
        require(np.all(patched_coordinates[changed_mask] == TARGET), "exact listed coordinate replacement")
        budget.check("saved raw coordinate-map application")
        original_patch = shapely.union_all(shapely.polygons(coordinates[np.unique(affected_rows), :3]))
        patched_patch = shapely.union_all(shapely.polygons(patched_coordinates[np.unique(affected_rows), :3]))
        local = patched_coordinates[:, :3] - patched_coordinates[:, :1]
        determinant = local[:, 1, 0] * local[:, 2, 1] - local[:, 1, 1] * local[:, 2, 0]
        area = np.abs(determinant) * 0.5
        repeated = ((patched_coordinates[:, 0] == patched_coordinates[:, 1]).all(axis=1)
                    | (patched_coordinates[:, 1] == patched_coordinates[:, 2]).all(axis=1)
                    | (patched_coordinates[:, 2] == patched_coordinates[:, 0]).all(axis=1))
        removed = np.flatnonzero(repeated | (area == 0.0)).astype(np.int64, copy=False)
        require(np.array_equal(removed, EXPECTED_REMOVED), "only original two degenerate raw rows removed")
        retained = ~np.isin(np.arange(len(area)), removed)
        require(np.all(area[retained] > native_gate) and np.all(area[retained] > stiffness_gate), "retained raw triangles pass native/stiffness gates")
        next_index = int(np.flatnonzero(retained)[np.argmin(area[retained])])
        next_area = float(area[next_index])
        domain_symdiff = original_domain.symmetric_difference(patched_domain)
        patch_symdiff = original_patch.symmetric_difference(patched_patch)
        domain_delta = float(patched_domain.area - original_domain.area)
        operand_bound = 128.0 * np.finfo(np.float64).eps * max(1.0, original_domain.area, original_patch.area, patched_patch.area)
        require(abs(domain_symdiff.area - patch_symdiff.area) <= operand_bound, "local patch/domain change agreement")
        require(original_domain.is_valid and patched_domain.is_valid and original_domain.geom_type == patched_domain.geom_type
                and len(original_domain.interiors) == len(patched_domain.interiors) and original_domain.bounds == patched_domain.bounds, "domain topology/bounds preservation")
        budget.check("raw gate and local patch metrics")
        del raw, triangles, coordinates, patched_coordinates, local, determinant, area
        controls = contact_controls(patched_domain)
        budget.check("saved contacts/current identity controls")
        artifact = output / "l04-local-coalescence-witness.npz"
        mass.atomic_npz(artifact, old_coordinate_um=OLD, replacement_coordinate_um=TARGET,
            domain_ring_index=np.asarray([563], dtype=np.int64), domain_ring_vertex_positions=ring_positions,
            raw_triangle_index=affected_rows.astype(np.int64), raw_triangle_vertex_index=affected_vertices.astype(np.int8),
            raw_triangle_old_coordinate_um=old_coordinates, removed_triangle_index=removed,
            next_retained_min_area_triangle_index=np.asarray([next_index], dtype=np.int64),
            next_retained_min_area_um2=np.asarray([next_area]),
            native_area_gate_um2=np.asarray([native_gate]), stiffness_area_gate_um2=np.asarray([stiffness_gate]))
        budget.check("conditional local witness checkpoint")
        result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_CONDITIONAL_L04_LOCAL_COALESCENCE_WITNESS",
            "driver": receipt(driver), "inputs": pins, "artifact": receipt(artifact),
            "coordinate_map": {"ring_index": 563, "ring_positions": [42, 44], "replacement_coordinate_um": TARGET.tolist(),
                "affected_raw_occurrence_count": int(len(affected_rows)), "affected_raw_triangle_count": int(len(np.unique(affected_rows)),
                ), "affected_raw_triangle_indices": np.unique(affected_rows).astype(int).tolist()},
            "removed_raw_triangle_indices": removed.astype(int).tolist(),
            "retained_gates": {"native_area_gate_um2": native_gate, "stiffness_area_gate_um2": stiffness_gate,
                "next_minimum_area_um2": next_area, "next_minimum_area_triangle_index": next_index},
            "local_geometry_change": {"domain_symmetric_difference_area_um2": float(domain_symdiff.area), "patch_symmetric_difference_area_um2": float(patch_symdiff.area),
                "domain_area_delta_um2": domain_delta, "domain_patch_operand_bound_um2": operand_bound,
                "domain_hausdorff_distance_um": float(original_domain.hausdorff_distance(patched_domain)),
                "domain_valid": bool(patched_domain.is_valid), "component_count": 1, "hole_count": len(patched_domain.interiors), "bounds_equal": True},
            "contact_controls": controls, "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)"},
            "scope": "Conditional exact two-coordinate coalescence witness only. It does not write a patched domain/CDT, regenerate a mesh, repair geometry, assemble stiffness, solve currents, or qualify a mesh."}
        mass.atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        mass.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L04_CONDITIONAL_LOCAL_COALESCENCE_WITNESS",
            "error_type": type(error).__name__, "error": str(error), "driver": receipt(driver), "budget": budget.receipt()})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=R / OUTPUT_NAME)
    args = parser.parse_args()
    print(json.dumps({"status": worker(args.output.resolve())["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
