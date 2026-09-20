"""Released L04 mesh continuation that reuses only the witnessed coalesced large-face CDT."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

import project_astra_l14_gc_mass as mass
import prepare_astra_l04_conditional_sheet_mesh as base
from spd_decap_pi._core.solver import tri_fem_sheet as fem


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OUTPUT_NAME = "astra-l04-conditional-sheet-mesh-02"
RUN_RELEASED = True
COOPERATIVE_SECONDS = 1800.0
EXTERNAL_SECONDS = 1950.0
RAW01 = R / "astra-l04-conditional-sheet-mesh-01"
WITNESS = R / "astra-l04-cdt-local-coalescence-witness-02"
RAW_SOURCE = RAW01 / "unvalidated-l04-large-face-cdt.wkb"
PINS = {
    "base_helper": (ROOT / "tools/research/prepare_astra_l04_conditional_sheet_mesh.py", "bd40ca856a1874ebee2919c29fe871531a6af5fc289498a2d70623e38f25f140"),
    "raw01_cdt": (RAW_SOURCE, "ec2850aafd627f40368872b20276b0e1c38ecc0db9513550c34d484079542e96"),
    "raw01_failure": (RAW01 / "failure.json", "4e52381fd98d60bbd576a366a780acd95a369a8afcd242a21d282ba08774eea5"),
    "raw01_external": (RAW01 / "external-budget.json", "2d01f2e63068f65d74f659fffc8b78345487379eadb99cbde2d134da3df4022e"),
    "witness_result": (WITNESS / "result.json", "3e097e7086f6bd10e009afbcdb6b98ca8029f6555273f4e7b1f92c3059a216ce"),
    "witness_artifact": (WITNESS / "l04-local-coalescence-witness.npz", "c9754633834d5195ffb5b9c98dd53c798ad126fce7249c74e2dadfd283ca3412"),
    "witness_metrics": (WITNESS / "l04-local-coalescence-geometry-metrics.json", "02446b93cb795a67c35b0b14e6175ebe7aabe7995220b4048b49604e17066038"),
    "witness_external": (WITNESS / "external-budget.json", "6e498b27d456b236b6751236f2c7716f27679d3954a89462ec2bd3bdb03461e5"),
    "changed_domain_wkb_sha256": "8dbfbb75d87efa7c9504b1ab521a78687ef97b45d1eb0e6a8d5ff6007658c674",
    "pad_source": (R / "astra-l04-pad-conductor-domain-01/result.json", "bde2eb7c636276da737d0e6c91b0309603d9570899ccf5bbbfc2acffe7a74f96"),
    "guarded_source_worker": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
}
OLD = np.asarray([[-12596.299999999852, 13546.300000000001], [-12596.300000000147, 13546.300000000001]])
TARGET = np.asarray([-12596.3, 13546.3])
RING_INDEX = 563
RING_POSITIONS = np.asarray([42, 44], dtype=np.int64)
REMOVED_ROWS = np.asarray([807731, 976112], dtype=np.int64)
AFFECTED_ROWS = np.asarray([807731, 896310, 976112, 1043262, 1102499], dtype=np.int64)
AFFECTED_OCCURRENCES = 10


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def atomic_binary(path: Path, value: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def verify_pins() -> dict[str, dict[str, object] | str]:
    checked: dict[str, dict[str, object] | str] = {}
    for name, value in PINS.items():
        if name == "changed_domain_wkb_sha256":
            checked[name] = value
            continue
        path, expected = value
        require(path.is_file() and sha(path) == expected, f"pinned {name}")
        checked[name] = receipt(path)
    require(Path(base.__file__).resolve() == PINS["base_helper"][0].resolve(), "frozen base import location")
    return checked


def witness_contract() -> dict[str, object]:
    result = json.loads(PINS["witness_result"][0].read_bytes())
    metrics = json.loads(PINS["witness_metrics"][0].read_bytes())
    external = json.loads(PINS["witness_external"][0].read_bytes())
    require(result["status"] == "COMPLETED_CONDITIONAL_L04_LOCAL_COALESCENCE_WITNESS", "accepted witness status")
    require(external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0, "accepted witness external guard")
    require(result["artifact"]["sha256"] == PINS["witness_artifact"][1], "accepted witness artifact receipt")
    coordinate_map = result["coordinate_map"]
    require(coordinate_map["ring_index"] == RING_INDEX and coordinate_map["ring_positions"] == RING_POSITIONS.tolist(), "exact witnessed ring map")
    require(coordinate_map["replacement_coordinate_um"] == TARGET.tolist() and coordinate_map["removed_triangle_rows"] == REMOVED_ROWS.tolist(), "exact witnessed replacement/removal map")
    require(coordinate_map["affected_raw_triangle_indices"] == AFFECTED_ROWS.tolist() and coordinate_map["affected_raw_occurrence_count"] == AFFECTED_OCCURRENCES, "exact witnessed raw occurrence rows")
    local = metrics["local_geometry_change"]
    require(local["changed_domain_wkb_sha256"] == PINS["changed_domain_wkb_sha256"], "accepted changed-domain digest")
    require(local["max_vertex_displacement_um"] == result["local_geometry_change"]["max_vertex_displacement_um"], "witness max displacement receipt")
    return {"result": result, "metrics": metrics, "max_vertex_displacement_um": float(local["max_vertex_displacement_um"])}


def coalesced_domain(original: Polygon, contract: dict[str, object]) -> Polygon:
    rings = [np.asarray(original.exterior.coords, dtype=np.float64)]
    rings.extend(np.asarray(ring.coords, dtype=np.float64) for ring in original.interiors)
    ring = rings[RING_INDEX].copy()
    require(np.array_equal(ring[42], OLD[0]) and np.array_equal(ring[44], OLD[1]) and np.array_equal(ring[43], TARGET), "saved domain exact witnessed coordinates")
    ring[RING_POSITIONS] = TARGET
    changed_rings = list(rings)
    changed_rings[RING_INDEX] = ring
    require(all(rings[index].tobytes() == changed_rings[index].tobytes() for index in range(len(rings)) if index != RING_INDEX), "only ring563 changed")
    changed = Polygon(changed_rings[0], changed_rings[1:])
    digest = hashlib.sha256(shapely.to_wkb(changed)).hexdigest()
    require(changed.is_valid and digest == PINS["changed_domain_wkb_sha256"], "reconstructed accepted changed domain")
    return changed


def save_patched_raw(output: Path, contract: dict[str, object], budget) -> dict[str, object]:
    raw_path, metadata_path = base.snapshot_paths(output)
    require(not raw_path.exists() and not metadata_path.exists(), "fresh patched raw CDT output")
    raw = shapely.from_wkb(RAW_SOURCE.read_bytes())
    parts = shapely.get_parts(raw)
    require(len(parts) == 1_589_829 and np.all(shapely.get_num_coordinates(parts) == 4), "raw01 CDT schema")
    coordinates = shapely.get_coordinates(parts).reshape(len(parts), 4, 2)
    changed = np.all(coordinates == OLD[0], axis=2) | np.all(coordinates == OLD[1], axis=2)
    rows, vertices = np.nonzero(changed)
    require(len(rows) == AFFECTED_OCCURRENCES and np.array_equal(np.unique(rows), AFFECTED_ROWS), "all and only witnessed raw occurrences")
    local = coordinates[AFFECTED_ROWS].copy()
    local[np.all(local == OLD[0], axis=2) | np.all(local == OLD[1], axis=2)] = TARGET
    repeated = ((local[:, 0] == local[:, 1]).all(axis=1) | (local[:, 1] == local[:, 2]).all(axis=1) | (local[:, 2] == local[:, 0]).all(axis=1))
    removed = AFFECTED_ROWS[repeated]
    require(np.array_equal(removed, REMOVED_ROWS), "drop exactly witnessed degeneracies")
    replacement = shapely.polygons(local[:, :3])
    patched_parts = np.asarray(parts, dtype=object).copy()
    patched_parts[AFFECTED_ROWS] = replacement
    kept = np.ones(len(parts), dtype=np.bool_)
    kept[REMOVED_ROWS] = False
    patched = shapely.geometrycollections(patched_parts[kept])
    encoded = shapely.to_wkb(patched)
    atomic_binary(raw_path, encoded)
    provenance = {"status": "UNVALIDATED_L04_COALESCED_LARGE_FACE_CDT", "source_raw_cdt": receipt(RAW_SOURCE),
        "witness_result": receipt(PINS["witness_result"][0]), "witness_artifact": receipt(PINS["witness_artifact"][0]),
        "changed_domain_wkb_sha256": PINS["changed_domain_wkb_sha256"], "coordinate_map": {"ring_index": RING_INDEX,
        "ring_positions": RING_POSITIONS.astype(int).tolist(), "replacement_coordinate_um": TARGET.tolist(),
        "affected_raw_triangle_indices": AFFECTED_ROWS.astype(int).tolist(), "affected_raw_occurrence_count": int(len(rows)),
        "removed_triangle_rows": removed.astype(int).tolist()}, "snapshot": receipt(raw_path),
        "triangle_count": int(kept.sum()), "scope": "Saved raw CDT exact coordinate coalescence only; no new CDT was generated."}
    base.atomic_json(output / "coalesced-large-face-cdt-provenance.json", provenance)
    del raw, parts, coordinates, local, patched_parts, patched, encoded
    gc.collect()
    budget.check("saved coalesced raw CDT and released source geometry")
    return provenance


def install_large_face_hook(output: Path, driver: Path, inputs: dict[str, object], provenance: dict[str, object], changed_domain: Polygon):
    original = base.load_or_save_large_cdt
    state = {"calls": 0, "face_sha256": None}
    raw_path, metadata_path = base.snapshot_paths(output)
    def only_saved_coalesced(face: Polygon, triangles, hook_driver: Path, hook_inputs: dict[str, object]):
        require(shapely.get_num_coordinates(face) > 10_000, "only one large face may use saved CDT")
        require(triangles is None and state["calls"] == 0 and raw_path.is_file() and not metadata_path.exists(), "refuse regenerated or repeated large CDT")
        require(hook_driver == driver and hook_inputs == inputs, "base large-face hook provenance")
        state["calls"] += 1
        state["face_sha256"] = base.face_sha(face)
        saved = {"status": "UNVALIDATED_L04_LARGE_FACE_CDT", "driver": receipt(driver), "inputs": inputs,
            "face_sha256": state["face_sha256"], "snapshot": receipt(raw_path), "triangle_count": provenance["triangle_count"],
            "geometry_approximation": {"method": "EXACT_TWO_SOURCE_VERTEX_COALESCENCE_L04_RING563", "changed_domain_wkb_sha256": hashlib.sha256(shapely.to_wkb(changed_domain)).hexdigest()},
            "scope": "Saved coalesced large-face CDT before native coverage, contact, stiffness, or mesh qualification."}
        base.atomic_json(metadata_path, saved)
        return shapely.from_wkb(raw_path.read_bytes()), {**saved, "resumed": False, "coalesced_saved_geometry": True}
    base.load_or_save_large_cdt = lambda _output, face, triangles, hook_driver, hook_inputs: only_saved_coalesced(face, triangles, hook_driver, hook_inputs)
    return original, state


def mesh_checkpoint(output: Path, mesh, contacts, supports: np.ndarray, provenance: dict[str, object], driver: Path, domain_path: Path) -> Path:
    node_lists = [contact.node_indices for contact in mesh.contacts]
    triangle_lists = [contact.triangle_indices for contact in mesh.contacts]
    require([contact.contact_id for contact in mesh.contacts] == [contact.contact_id for contact in contacts], "pre-stiffness contact order")
    require(len(mesh.contacts) == len(supports) == base.CONTACT_COUNT, "pre-stiffness support/contact count")
    require(all(len(nodes) == 16 and len(triangles) == 14 for nodes, triangles in zip(node_lists, triangle_lists, strict=True)), "pre-stiffness strict contact coverage")
    node_indices = np.concatenate(node_lists)
    triangle_indices = np.concatenate(triangle_lists)
    node_indptr = np.cumsum([0] + [len(rows) for rows in node_lists], dtype=np.int64)
    triangle_indptr = np.cumsum([0] + [len(rows) for rows in triangle_lists], dtype=np.int64)
    require(node_indptr[0] == triangle_indptr[0] == 0 and node_indptr[-1] == len(node_indices) and triangle_indptr[-1] == len(triangle_indices), "pre-stiffness CSR bounds")
    require(np.all((node_indices >= 0) & (node_indices < len(mesh.node_xy_m))) and np.all((triangle_indices >= 0) & (triangle_indices < len(mesh.triangles))), "pre-stiffness contact index bounds")
    path = output / "l04-conditional-sheet-mesh-before-stiffness.npz"
    mass.atomic_npz(path, node_xy_um=np.asarray(mesh.node_xy_m, dtype=np.float64), triangles=np.asarray(mesh.triangles, dtype=np.int64),
        contact_support_index=supports, contact_node_indices=node_indices, contact_node_indptr=node_indptr,
        contact_triangle_indices=triangle_indices, contact_triangle_indptr=triangle_indptr,
        source_wrapper_sha256_utf8=np.asarray([sha(Path(__file__))]), base_helper_sha256_utf8=np.asarray([PINS["base_helper"][1]]),
        witness_result_sha256_utf8=np.asarray([PINS["witness_result"][1]]), changed_domain_wkb_sha256_utf8=np.asarray([PINS["changed_domain_wkb_sha256"]]),
        patched_raw_cdt_sha256_utf8=np.asarray([provenance["snapshot"]["sha256"]]),
        conductivity_s_per_m=np.asarray([base.SIGMA_S_PER_M]), thickness_m=np.asarray([base.THICKNESS_M]),
        sheet_conductance_s=np.asarray([base.SIGMA_S_PER_M * base.THICKNESS_M]))
    sidecar = output / "l04-conditional-sheet-mesh-before-stiffness.json"
    base.atomic_json(sidecar, {"program": PROGRAM, "version": VERSION, "status": "VALIDATED_L04_MESH_BEFORE_UNCHANGED_STIFFNESS",
        "driver": receipt(driver), "source_result": receipt(PINS["pad_source"][0]), "witness_result": receipt(PINS["witness_result"][0]),
        "domain": receipt(domain_path), "patched_raw_cdt": provenance["snapshot"], "snapshot": receipt(path),
        "contact_count": int(len(supports)), "contact_nodes_per_contact": 16, "contact_triangles_per_contact": 14})
    return path


def compile_mesh(output: Path, domain: Polygon, contacts, budget, driver: Path, inputs: dict[str, object], supports: np.ndarray, provenance: dict[str, object], domain_path: Path):
    original_loader, state = install_large_face_hook(output, driver, inputs, provenance, domain)
    original_stiffness = fem._compile_stiffness
    checkpoint: dict[str, Path] = {}
    def checkpoint_then_stiffness(mesh):
        require("path" not in checkpoint, "stiffness entered twice")
        checkpoint["path"] = mesh_checkpoint(output, mesh, contacts, supports, provenance, driver, domain_path)
        budget.check("validated mesh checkpoint before unchanged stiffness")
        return original_stiffness(mesh)
    fem._compile_stiffness = checkpoint_then_stiffness
    try:
        sheet, coverage = base.compile_mesh(output, domain, contacts, budget, driver, inputs)
    finally:
        base.load_or_save_large_cdt = original_loader
        fem._compile_stiffness = original_stiffness
    require(state["calls"] == 1 and coverage["snapshot"]["face_sha256"] == state["face_sha256"], "one deterministic saved large-face request")
    require("path" in checkpoint and checkpoint["path"].is_file(), "pre-stiffness mesh checkpoint")
    return sheet, coverage, checkpoint["path"], state


def preflight() -> dict[str, object]:
    pins = verify_pins()
    contract = witness_contract()
    base_report = base.preflight()
    require(base_report["source_result"]["sha256"] == PINS["pad_source"][1], "base pad source receipt")
    return {**base_report, "status": "PASS_DISABLED_CONDITIONAL_L04_COALESCED_CDT_MESH_PREFLIGHT", "run_released": RUN_RELEASED,
        "pins": pins, "source_result": pins["pad_source"], "witness": {"result": pins["witness_result"], "artifact": pins["witness_artifact"],
        "changed_domain_wkb_sha256": PINS["changed_domain_wkb_sha256"], "max_vertex_displacement_um": contract["max_vertex_displacement_um"]},
        "scope": "Disabled continuation only. It will reuse saved coalesced raw CDT geometry for one large face and leaves all small contact faces on native CDT."}


def worker(output: Path) -> dict[str, object]:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release mesh02")
    driver = output / "driver-at-run.py"
    require(output.is_dir() and driver.is_file() and sha(driver) == sha(Path(__file__)), "frozen mesh02 driver")
    budget, _recon = base.load_budget()
    try:
        report = preflight()
        contract = witness_contract()
        original_domain, supports, xy_pm, diameter_pm, _pad = base.source_contract()
        domain = coalesced_domain(original_domain, contract)
        domain_path = output / "l04-coalesced-conductor-domain.wkb"
        encoded_domain = shapely.to_wkb(domain)
        require(hashlib.sha256(encoded_domain).hexdigest() == PINS["changed_domain_wkb_sha256"], "saved changed-domain digest")
        atomic_binary(domain_path, encoded_domain)
        contacts = base.contacts_for(supports, xy_pm, diameter_pm)
        budget.check("mesh02 source domain and contacts")
        provenance = save_patched_raw(output, contract, budget)
        sheet, coverage, before_stiffness, large = compile_mesh(output, domain, contacts, budget, driver, report["pins"], supports, provenance, domain_path)
        budget.check("mesh02 native coverage contacts and unchanged stiffness")
        require(len(sheet.mesh.node_xy_m) <= base.MAX_NODES and len(sheet.mesh.triangles) <= base.MAX_TRIANGLES, "mesh02 native bounds")
        require([contact.contact_id for contact in sheet.mesh.contacts] == [contact.contact_id for contact in contacts], "mesh02 contact ordering")
        node_lists = [contact.node_indices for contact in sheet.mesh.contacts]
        triangle_lists = [contact.triangle_indices for contact in sheet.mesh.contacts]
        require(all(len(nodes) == 16 and len(triangles) == 14 for nodes, triangles in zip(node_lists, triangle_lists, strict=True)), "mesh02 strict 16-node/14-triangle contacts")
        node_indptr = np.cumsum([0] + [len(rows) for rows in node_lists], dtype=np.int64)
        triangle_indptr = np.cumsum([0] + [len(rows) for rows in triangle_lists], dtype=np.int64)
        snapshot = output / "l04-conditional-sheet-mesh.npz"
        mass.atomic_npz(snapshot, node_xy_um=np.asarray(sheet.mesh.node_xy_m, dtype=np.float64), triangles=np.asarray(sheet.mesh.triangles, dtype=np.int64), contact_support_index=supports, contact_node_indices=np.concatenate(node_lists), contact_node_indptr=node_indptr, contact_triangle_indices=np.concatenate(triangle_lists), contact_triangle_indptr=triangle_indptr, stiffness_data=np.asarray(sheet.stiffness.data, dtype=np.float64), stiffness_indices=np.asarray(sheet.stiffness.indices, dtype=np.int64), stiffness_indptr=np.asarray(sheet.stiffness.indptr, dtype=np.int64), stiffness_shape=np.asarray(sheet.stiffness.shape, dtype=np.int64), conductivity_s_per_m=np.asarray([base.SIGMA_S_PER_M]), thickness_m=np.asarray([base.THICKNESS_M]), sheet_conductance_s=np.asarray([base.SIGMA_S_PER_M * base.THICKNESS_M]))
        geometry = {"method": "EXACT_TWO_SOURCE_VERTEX_COALESCENCE_L04_RING563", "unchanged_source_geometry": False, "source_domain": receipt(base.PINS["domain_wkb"][0]), "witness_result": receipt(PINS["witness_result"][0]), "domain": receipt(domain_path), "max_vertex_displacement_um": contract["max_vertex_displacement_um"]}
        result = {**report, "status": "COMPLETED_CONDITIONAL_L04_SHEET_MESH", "driver": receipt(driver), "source_result": receipt(PINS["pad_source"][0]), "snapshot": receipt(snapshot), "raw_cdt_snapshot": coverage["snapshot"], "coalesced_raw_provenance": receipt(output / "coalesced-large-face-cdt-provenance.json"), "before_stiffness_checkpoint": receipt(before_stiffness), "before_stiffness_receipt": receipt(output / "l04-conditional-sheet-mesh-before-stiffness.json"), "geometry_approximation": geometry, "witness_metrics": receipt(PINS["witness_metrics"][0]), "mesh_nodes": len(sheet.mesh.node_xy_m), "mesh_triangles": len(sheet.mesh.triangles), "stiffness_nnz": int(sheet.stiffness.nnz), "operator_identity_sha256": sheet.identity_sha256, "coverage": coverage, "large_face": large, "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(1800,8)"}, "scope": "Conditional one-polygon L04 mesh with saved exact coalesced large-face CDT and native small contact faces. No RT0 current, solve, field, or magnetic action is made."}
        base.atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        base.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_CONDITIONAL_L04_SHEET_MESH", "error_type": type(error).__name__, "error": str(error), "driver": receipt(driver), "budget": budget.receipt()})
        raise


def self_check() -> None:
    require(not RUN_RELEASED and PINS["base_helper"][1] == sha(Path(base.__file__)), "disabled frozen base self-check")
    fake = np.asarray([[[0., 0.], [1., 0.], [0., 1.], [0., 0.]], [[2., 0.], [2., 1.], [3., 0.], [2., 0.]]])
    changed = np.all(fake == OLD[0], axis=2) | np.all(fake == OLD[1], axis=2)
    require(not changed.any() and shapely.geometrycollections(shapely.polygons(fake[:, :3])).geom_type == "GeometryCollection", "saved CDT hook static schema")
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        driver = output / "driver-at-run.py"
        driver.write_text("fixture", encoding="utf-8")
        raw_path, _metadata_path = base.snapshot_paths(output)
        tiny = shapely.geometrycollections(shapely.polygons(fake[:1, :3]))
        atomic_binary(raw_path, shapely.to_wkb(tiny))
        provenance = {"triangle_count": 1, "snapshot": receipt(raw_path)}
        angle = np.linspace(0.0, 2.0 * np.pi, 10_001, endpoint=False)
        points = np.c_[np.cos(angle), np.sin(angle)]
        face = Polygon(np.vstack([points, points[0]]))
        original, state = install_large_face_hook(output, driver, {"fixture": True}, provenance, face)
        try:
            value, saved = base.load_or_save_large_cdt(output, face, None, driver, {"fixture": True})
            require(len(shapely.get_parts(value)) == 1 and saved["coalesced_saved_geometry"] and state["calls"] == 1, "saved loader first call")
            rejected = False
            try:
                base.load_or_save_large_cdt(output, face, None, driver, {"fixture": True})
            except ValueError:
                rejected = True
            require(rejected, "saved loader second call rejected")
        finally:
            base.load_or_save_large_cdt = original
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_L04_COALESCED_CDT_MESH_STATIC_SELF_CHECK", "run_released": RUN_RELEASED}, sort_keys=True))


def launch(output: Path) -> int:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release mesh02")
    verify_pins()
    require(not output.exists(), "fresh mesh02 output required")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    guard_path, guard_sha = PINS["guarded_source_worker"]
    require(sha(guard_path) == guard_sha, "pinned mesh02 guard")
    from probe_astra_fmm3d_runtime import guarded_source_worker
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output.resolve())]
    return guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=R / OUTPUT_NAME)
    args = parser.parse_args()
    require(sum((args.self_check, args.preflight, args.run, args.native_worker)) == 1, "select exactly one mode")
    if args.self_check:
        self_check()
    elif args.preflight:
        print(json.dumps(preflight(), sort_keys=True))
    elif args.run:
        raise SystemExit(launch(args.output.resolve()))
    else:
        print(json.dumps({"status": worker(args.output.resolve())["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
