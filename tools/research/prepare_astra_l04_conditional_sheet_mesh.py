"""Released conditional L04 CDT/FEM mesh adapter; no current or solve."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np
import shapely
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

import project_astra_l14_gc_mass as mass
import probe_astra_l02_triangle_coverage as coverage_probe
from spd_decap_pi._core.solver import tri_fem_sheet as fem


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RUN_RELEASED = True
OUTPUT_NAME = "astra-l04-conditional-sheet-mesh-01"
PREFLIGHT_NAME = "astra-l04-conditional-sheet-mesh-preflight-01"
CONTACT_COUNT = 38_278
MAX_NODES = 1_700_000
MAX_TRIANGLES = 2_600_000
SIGMA_S_PER_M = 59.59e6
THICKNESS_M = 20e-6
COOPERATIVE_SECONDS = 1800.0
EXTERNAL_SECONDS = 1950.0

PINS = {
    "native_l02_mesh_path": (ROOT / "tools/research/preflight_astra_l02_sheet_mesh.py", "176ae7bd1aa0d9727cc905b4f805fa613a04988bd2a3fce58268351a2ad70a9c"),
    "fem": (Path(fem.__file__), "16eb6fd122fb5cf8d7ab60bb4b0364b760cb354806c02651d2847ede633640e0"),
    "persistence": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
    "coverage_predicate": (Path(coverage_probe.__file__), "4fc704eb32c70cb5311de07a0b94bd0f998975d3c159a2e8150f3e072f3be07c"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "guarded_source_worker": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "pad_result": (R / "astra-l04-pad-conductor-domain-01/result.json", "bde2eb7c636276da737d0e6c91b0309603d9570899ccf5bbbfc2acffe7a74f96"),
    "domain_wkb": (R / "astra-l04-pad-conductor-domain-01/l04-pad-augmented-conductor-domain.wkb", "0eeaffc34a6b9759fd285f28d35ebd59042e6bcd909a697fad3f18a5d2eafa68"),
    "drill_wkb": (R / "astra-l04-pad-conductor-domain-01/l04-source-drill-footprint-union.wkb", "3fd97f2a1bd451a758b3f787ae7ad8f70e6afabf9cb8b2a7dfe9d704a0fe1b98"),
    "support_map": (R / "astra-l04-pad-conductor-domain-01/l04-pad-drill-support-map.npz", "7c76d3e61680d2092bdb3ebe53a03fd52030de478da570a35c4ce09baafc92ff"),
    "pad_external": (R / "astra-l04-pad-conductor-domain-01/external-budget.json", "4a4cca2dc0b6e40721ad22a5b1c54d67da9d0b2813f981179de1aeef9f194547"),
    "fixed_current_result": (R / "astra-l04-fixed-contact-currents-01/result.json", "6c03c997c724da002e12bef0845a08cdae409f76d33428ab1bfba2f875a3a46f"),
    "fixed_current_npz": (R / "astra-l04-fixed-contact-currents-01/l04-fixed-contact-currents.npz", "5c2fd3bd30a45d9d92ccc47a5ad2d553f0d122acb4282a1046be09d7b4e51adb"),
}


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def atomic_json(path: Path, document: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def verify_pins() -> dict[str, dict[str, object]]:
    checked = {}
    for name, (path, expected) in PINS.items():
        require(path.is_file() and sha(path) == expected, f"pinned {name}")
        checked[name] = receipt(path)
    return checked


def load_budget():
    helper, expected = PINS["budget_helper"]
    require(sha(helper) == expected, "pinned budget helper")
    runtime = str(ROOT / "outputs" / "research-runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import reconstruct_astra_native_loaded_field as recon
    require(Path(recon.__file__).resolve() == helper.resolve(), "Budget import location")
    return recon._Budget.create(COOPERATIVE_SECONDS, 8.0), recon


def source_contract() -> tuple[Polygon, np.ndarray, np.ndarray, np.ndarray, dict[str, object]]:
    pad = json.loads(PINS["pad_result"][0].read_bytes())
    external = json.loads(PINS["pad_external"][0].read_bytes())
    current = json.loads(PINS["fixed_current_result"][0].read_bytes())
    require(pad["status"] == "COMPLETED_L04_PAD_DOMAIN_CANDIDATE", "pad result status")
    require(pad["outputs"]["domain"]["sha256"] == PINS["domain_wkb"][1], "pad domain receipt")
    require(pad["outputs"]["drill_union"]["sha256"] == PINS["drill_wkb"][1], "pad drill receipt")
    require(pad["outputs"]["support_map"]["sha256"] == PINS["support_map"][1], "pad support receipt")
    require(pad["unique_pad_supports"] == pad["unique_drill_supports"] == CONTACT_COUNT, "pad support count")
    require(external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0, "pad external guard")
    require(current["status"] == "COMPLETED_L04_FIXED_CONTACT_CURRENTS_SOURCE_AGGREGATION", "fixed current status")
    require(current["artifact"]["sha256"] == PINS["fixed_current_npz"][1], "fixed current artifact receipt")
    domain = shapely.from_wkb(PINS["domain_wkb"][0].read_bytes())
    require(isinstance(domain, Polygon) and domain.is_valid and not domain.is_empty and len(shapely.get_parts(domain)) == 1, "one valid L04 Polygon")
    with np.load(PINS["support_map"][0], allow_pickle=False) as archive:
        supports = np.sort(np.asarray(archive["drill_support_index"], dtype=np.int64))
        xy_pm = np.asarray(archive["support_xy_pm"], dtype=np.int64)
        diameter_pm = np.asarray(archive["support_diameter_pm"], dtype=np.int64)
    with np.load(PINS["fixed_current_npz"][0], allow_pickle=False) as archive:
        group_current = np.asarray(archive["coincident_group_current_outward_from_l04_sheet_a"], dtype=np.complex128)
        current_drill_support = np.asarray(archive["group_to_drill_support_index"], dtype=np.int64)
        current_category = np.asarray(archive["category"], dtype=np.int8)
    require(supports.shape == (CONTACT_COUNT,) and len(np.unique(supports)) == CONTACT_COUNT, "exact sorted drill support indices")
    require(xy_pm.ndim == 2 and xy_pm.shape[1] == 2 and diameter_pm.shape == (len(xy_pm),) and np.all(diameter_pm > 0), "support geometry arrays")
    require(np.all(supports >= 0) and np.all(supports < len(xy_pm)), "support index bounds")
    require(group_current.shape == (CONTACT_COUNT,) and current_category.shape == (76_166,), "fixed-current array shapes")
    require(np.array_equal(np.sort(current_drill_support), supports), "fixed-current/drill support identity")
    return domain, supports, xy_pm, diameter_pm, pad


def preflight() -> dict[str, object]:
    pins = verify_pins()
    domain, supports, xy_pm, diameter_pm, pad = source_contract()
    budget, _recon = load_budget()
    budget.check("L04 mesh source contract")
    boundary_vertices = int(shapely.get_num_coordinates(domain)) - len(domain.interiors) - 1
    estimate_nodes = boundary_vertices + 16 * CONTACT_COUNT
    estimate_triangles = boundary_vertices + 32 * CONTACT_COUNT + 2 * len(domain.interiors) - 2
    require(estimate_nodes <= MAX_NODES and estimate_triangles <= MAX_TRIANGLES, "Euler upper bound exceeds L04 mesh limits")
    return {
        "program": PROGRAM, "version": VERSION,
        "status": "PASS_DISABLED_CONDITIONAL_L04_SHEET_MESH_PREFLIGHT" if not RUN_RELEASED else "PASS_CONDITIONAL_L04_SHEET_MESH_PREFLIGHT",
        "run_released": RUN_RELEASED, "pins": pins,
        "source_result": pins["pad_result"], "contact_count": int(len(supports)),
        "domain": {"polygon_component_count": 1, "holes": int(len(domain.interiors)), "boundary_vertices": boundary_vertices,
                   "estimated_nodes": estimate_nodes, "estimated_triangles": estimate_triangles},
        "material": {"conductivity_s_per_m": SIGMA_S_PER_M, "thickness_m": THICKNESS_M, "sheet_conductance_s": SIGMA_S_PER_M * THICKNESS_M},
        "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(1800,8)"},
        "scope": "Conditional L04 mesh only. Each 16-sided inscribed drill circle is a later floating-electrode boundary placeholder; exterior remains natural no-normal current. No RT0 current, R/G/C, solve, field or magnetic action is made.",
    }


def contacts_for(supports: np.ndarray, xy_pm: np.ndarray, diameter_pm: np.ndarray) -> list[fem.FiniteSheetContact]:
    result = []
    for support in supports:
        x_m, y_m = xy_pm[support].astype(np.float64) * 1e-6
        radius_m = float(diameter_pm[support]) * 0.5e-6
        result.append(fem.FiniteSheetContact(f"l04-drill-support:{int(support):06d}", f"conditional-l04-drill:{int(support):06d}",
            Point(float(x_m), float(y_m)).buffer(radius_m, quad_segs=4)))
    return result


def face_sha(face: Polygon) -> str:
    return hashlib.sha256(shapely.to_wkb(face)).hexdigest()


def snapshot_paths(output: Path) -> tuple[Path, Path]:
    return output / "unvalidated-l04-large-face-cdt.wkb", output / "unvalidated-l04-large-face-cdt.json"


def load_or_save_large_cdt(output: Path, face: Polygon, triangles, driver: Path, inputs: dict[str, object]):
    raw, metadata = snapshot_paths(output)
    current_face_sha = face_sha(face)
    if raw.exists() or metadata.exists():
        require(raw.is_file() and metadata.is_file(), "incomplete raw CDT snapshot")
        saved = json.loads(metadata.read_bytes())
        require(saved["status"] == "UNVALIDATED_L04_LARGE_FACE_CDT" and saved["driver"]["sha256"] == sha(driver), "raw CDT frozen driver")
        require(saved["inputs"] == inputs and saved["face_sha256"] == current_face_sha and saved["snapshot"]["sha256"] == sha(raw), "raw CDT resume contract")
        return shapely.from_wkb(raw.read_bytes()), {**saved, "resumed": True}
    temporary = raw.with_name(raw.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(shapely.to_wkb(triangles))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(raw)
    saved = {"status": "UNVALIDATED_L04_LARGE_FACE_CDT", "driver": receipt(driver), "inputs": inputs,
             "face_sha256": current_face_sha, "snapshot": receipt(raw), "triangle_count": len(triangles.geoms),
             "scope": "Raw large-face CDT before native triangle coverage, contact mapping, stiffness or mesh qualification."}
    atomic_json(metadata, saved)
    return triangles, {**saved, "resumed": False}


def compile_mesh(output: Path, domain: Polygon, contacts: list[fem.FiniteSheetContact], budget, driver: Path, inputs: dict[str, object]):
    original_contacts, original_mesh = fem._prepare_contacts, fem._compile_mesh
    original_cdt, original_stiffness, original_covers = fem._constrained_delaunay_triangles, fem._compile_stiffness, BaseGeometry.covers
    coverage = {"calls": 0, "global_control_checks": 0, "snapshot": None}

    def prepared_contacts(island, *args, **kwargs):
        boundary = island.boundary
        shapely.prepare(island)
        shapely.prepare(boundary)
        return original_contacts(SimpleNamespace(covers=island.covers, boundary=boundary), *args, **kwargs)

    def prepared_mesh(island, *args, **kwargs):
        shapely.prepare(island)
        indexed = coverage_probe.indexed_triangle_covers(island)
        def local_covers(this, candidate):
            if this is not island or not isinstance(candidate, Polygon) or len(candidate.exterior.coords) != 4 or candidate.interiors or not candidate.is_valid or candidate.area <= 0:
                return original_covers(this, candidate)
            value = indexed(candidate)
            coverage["calls"] += 1
            if coverage["calls"] <= 16 or coverage["calls"] % 4096 == 0:
                require(value == original_covers(island, candidate), "indexed/global triangle coverage differs")
                coverage["global_control_checks"] += 1
            if coverage["calls"] % 32768 == 0:
                budget.check("periodic L04 triangle coverage")
            return value
        BaseGeometry.covers = local_covers
        try:
            return original_mesh(island, *args, **kwargs)
        finally:
            BaseGeometry.covers = original_covers

    def cached_cdt(face):
        if shapely.get_num_coordinates(face) <= 10_000:
            return original_cdt(face)
        raw, metadata = snapshot_paths(output)
        if raw.exists() or metadata.exists():
            value, saved = load_or_save_large_cdt(output, face, None, driver, inputs)
        else:
            value = original_cdt(face)
            value, saved = load_or_save_large_cdt(output, face, value, driver, inputs)
        coverage["snapshot"] = saved
        budget.check("large-face raw CDT snapshot")
        return value

    try:
        fem._prepare_contacts, fem._compile_mesh = prepared_contacts, prepared_mesh
        fem._constrained_delaunay_triangles = cached_cdt
        sheet = fem.compile_tri_fem_sheet("research:l04:conditional-sheet:um", domain, contacts=contacts,
            conductivity_s_per_m=SIGMA_S_PER_M, thickness_m=THICKNESS_M, refinement_levels=0,
            max_nodes=MAX_NODES, max_triangles=MAX_TRIANGLES, max_contacts=CONTACT_COUNT, max_contact_work=50_000_000)
    finally:
        fem._prepare_contacts, fem._compile_mesh = original_contacts, original_mesh
        fem._constrained_delaunay_triangles, fem._compile_stiffness, BaseGeometry.covers = original_cdt, original_stiffness, original_covers
    require(coverage["snapshot"] is not None, "raw large-face CDT snapshot missing")
    return sheet, coverage


def worker(output: Path) -> dict[str, object]:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release L04 mesh launcher")
    driver = output / "driver-at-run.py"
    require(output.is_dir() and driver.is_file() and sha(driver) == sha(Path(__file__)), "frozen L04 mesh driver")
    budget, _recon = load_budget()
    try:
        report = preflight()
        domain, supports, xy_pm, diameter_pm, _pad = source_contract()
        budget.check("L04 mesh source load")
        contacts = contacts_for(supports, xy_pm, diameter_pm)
        budget.check("L04 conditional contact footprints")
        sheet, coverage = compile_mesh(output, domain, contacts, budget, driver, report["pins"])
        budget.check("L04 native mesh and stiffness")
        require(len(sheet.mesh.node_xy_m) <= MAX_NODES and len(sheet.mesh.triangles) <= MAX_TRIANGLES, "L04 native mesh bounds")
        require([contact.contact_id for contact in sheet.mesh.contacts] == [contact.contact_id for contact in contacts], "contact ordering")
        node_lists = [contact.node_indices for contact in sheet.mesh.contacts]
        triangle_lists = [contact.triangle_indices for contact in sheet.mesh.contacts]
        require(all(len(nodes) == 16 and len(triangles) == 14 for nodes, triangles in zip(node_lists, triangle_lists, strict=True)), "strict 16-node/14-triangle contact coverage")
        node_indptr = np.cumsum([0] + [len(rows) for rows in node_lists], dtype=np.int64)
        triangle_indptr = np.cumsum([0] + [len(rows) for rows in triangle_lists], dtype=np.int64)
        require(node_indptr[0] == 0 and node_indptr[-1] == sum(len(rows) for rows in node_lists), "contact-node CSR bounds")
        require(triangle_indptr[0] == 0 and triangle_indptr[-1] == sum(len(rows) for rows in triangle_lists), "contact-triangle CSR bounds")
        snapshot = output / "l04-conditional-sheet-mesh.npz"
        mass.atomic_npz(snapshot, node_xy_um=np.asarray(sheet.mesh.node_xy_m, dtype=np.float64),
            triangles=np.asarray(sheet.mesh.triangles, dtype=np.int64), contact_support_index=supports,
            contact_node_indices=np.concatenate(node_lists), contact_node_indptr=node_indptr,
            contact_triangle_indices=np.concatenate(triangle_lists), contact_triangle_indptr=triangle_indptr,
            stiffness_data=np.asarray(sheet.stiffness.data, dtype=np.float64), stiffness_indices=np.asarray(sheet.stiffness.indices, dtype=np.int64),
            stiffness_indptr=np.asarray(sheet.stiffness.indptr, dtype=np.int64), stiffness_shape=np.asarray(sheet.stiffness.shape, dtype=np.int64),
            conductivity_s_per_m=np.asarray([SIGMA_S_PER_M]), thickness_m=np.asarray([THICKNESS_M]), sheet_conductance_s=np.asarray([SIGMA_S_PER_M * THICKNESS_M]))
        budget.check("L04 mesh snapshot")
        result = {**report, "status": "COMPLETED_CONDITIONAL_L04_SHEET_MESH", "driver": receipt(driver),
            "source_result": receipt(PINS["pad_result"][0]), "snapshot": receipt(snapshot), "raw_cdt_snapshot": coverage["snapshot"],
            "mesh_nodes": len(sheet.mesh.node_xy_m), "mesh_triangles": len(sheet.mesh.triangles), "stiffness_nnz": int(sheet.stiffness.nnz),
            "operator_identity_sha256": sheet.identity_sha256, "coverage": coverage,
            "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(1800,8)"},
            "scope": "Conditional one-polygon L04 mesh with 38,278 floating 16-sided drill contacts and a natural exterior no-normal-current law. This mesh sets no current and makes no RT0, R/G/C, solve, field or magnetic claim."}
        atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_CONDITIONAL_L04_SHEET_MESH",
            "error_type": type(error).__name__, "error": str(error), "driver": receipt(driver), "budget": budget.receipt()})
        raise


def self_check() -> None:
    report = preflight()
    contacts = [fem.FiniteSheetContact(f"self:{index}", f"owner:{index}", Point(x, .5).buffer(.1, quad_segs=4)) for index, x in enumerate((.25, .75))]
    sheet = fem.compile_tri_fem_sheet("self:l04", box(0, 0, 1, 1), contacts=contacts, conductivity_s_per_m=1.0, thickness_m=1.0, refinement_levels=0, max_nodes=100, max_triangles=100)
    require(len(sheet.mesh.node_xy_m) == 36 and len(sheet.mesh.triangles) == 66, "native FEM self-check")
    domain = Polygon(box(0, 0, 10, 10).exterior, [box(2, 2, 4, 4).exterior])
    indexed = coverage_probe.indexed_triangle_covers(domain)
    triangles = shapely.constrained_delaunay_triangles(domain)
    require(all(indexed(triangle) == domain.covers(triangle) for triangle in triangles.geoms), "coverage predicate self-check")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_CONDITIONAL_L04_SHEET_MESH_STATIC_SELF_CHECK", "preflight_status": report["status"], "run_released": RUN_RELEASED}, sort_keys=True))


def launch(output: Path) -> int:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release L04 mesh launcher")
    verify_pins()
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    from probe_astra_fmm3d_runtime import guarded_source_worker
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output.resolve())]
    return guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(sum((args.self_check, args.preflight, args.run, args.native_worker)) == 1, "select one mode")
    output = (args.output or R / (PREFLIGHT_NAME if args.preflight else OUTPUT_NAME)).resolve()
    if args.self_check:
        self_check()
    elif args.preflight:
        require(not output.exists(), "refusing preflight overwrite")
        output.mkdir(parents=True)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        report = preflight(); report["driver"] = receipt(output / "driver-at-run.py")
        atomic_json(output / "preflight.json", report)
        print(json.dumps({"status": report["status"], "receipt": receipt(output / "preflight.json")}, sort_keys=True))
    elif args.native_worker:
        require(output.is_dir(), "launcher-created output required")
        print(json.dumps({"status": worker(output)["status"]}, sort_keys=True))
    else:
        raise SystemExit(launch(output))


if __name__ == "__main__":
    main()
