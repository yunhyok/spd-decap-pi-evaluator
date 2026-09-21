"""Bounded existing FEM compile on the accepted L02 pad domain; no board solve."""
import argparse
import faulthandler
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import numpy as np
import shapely
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

import project_astra_l14_gc_mass as mass
import probe_astra_l02_triangle_coverage as coverage_probe
from spd_decap_pi._core.solver import tri_fem_sheet as fem

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "fem": (Path(fem.__file__), "16eb6fd122fb5cf8d7ab60bb4b0364b760cb354806c02651d2847ede633640e0"),
    "previous_preflight": (ROOT / "tools/research/preflight_astra_l14_sheet_mesh.py", "88db083c385e24db8ad6d301adc98874212712afd0fbbcb3bce29aa0878cda5f"),
    "persistence": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
    "coverage_predicate": (Path(coverage_probe.__file__), "4fc704eb32c70cb5311de07a0b94bd0f998975d3c159a2e8150f3e072f3be07c"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_cdt_checkpoint(output, face, triangles):
    path = output / "unvalidated-large-face-cdt.wkb"
    assert not path.exists(), "single-large-face checkpoint already exists"
    temporary = path.with_suffix(".wkb.tmp")
    with temporary.open("xb") as stream:
        stream.write(shapely.to_wkb(triangles))
    temporary.replace(path)
    metadata = {"status": "UNVALIDATED_CDT_ONLY", "path": str(path), "sha256": sha(path),
        "face_wkb_sha256": hashlib.sha256(shapely.to_wkb(face)).hexdigest(),
        "triangle_count": len(triangles.geoms),
        "scope": "Raw CDT output before native coverage, area, contact or stiffness checks; not an accepted mesh."}
    mass.atomic_json(output / "unvalidated-large-face-cdt.json", metadata)
    return metadata


def self_check():
    contacts = [fem.FiniteSheetContact(f"test:{i}", f"owner:{i}", Point(x, .5).buffer(.1, quad_segs=4))
                for i, x in enumerate((.25, .75))]
    sheet = fem.compile_tri_fem_sheet("test:l02-contacts", box(0,0,1,1), contacts=contacts,
        conductivity_s_per_m=1., thickness_m=1., refinement_levels=0, max_nodes=100, max_triangles=100)
    assert len(sheet.mesh.node_xy_m) == 36 and len(sheet.mesh.triangles) == 66
    assert all(len(c.node_indices) == 16 and len(c.triangle_indices) == 14 for c in sheet.mesh.contacts)
    assert np.max(abs(np.asarray(sheet.stiffness.sum(axis=1)))) < 1e-12
    domain = Polygon(box(0, 0, 10, 10).exterior, [box(2, 2, 4, 4).exterior])
    indexed = coverage_probe.indexed_triangle_covers(domain)
    triangles = shapely.constrained_delaunay_triangles(domain)
    assert all(indexed(triangle) == domain.covers(triangle) for triangle in triangles.geoms)
    with TemporaryDirectory() as temporary:
        metadata = save_cdt_checkpoint(Path(temporary), domain, triangles)
        assert metadata["status"] == "UNVALIDATED_CDT_ONLY"
        assert Path(metadata["path"]).read_bytes() == shapely.to_wkb(triangles)


def run(args):
    started = time.monotonic()
    def emit(event, **values):
        row = {"event": event, "elapsed_s": time.monotonic()-started, **values}
        with (args.output / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, allow_nan=False)+"\n")
        print(json.dumps(row, allow_nan=False), flush=True)
        if row["elapsed_s"] > 2300:
            raise TimeoutError("internal phase deadline exceeded2300s")
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    assert sha(args.source_result) == args.source_sha256
    source = json.loads(args.source_result.read_bytes())
    review_path = args.source_result.parent / "independent-review.json"
    assert sha(review_path) == args.source_review_sha256
    if source["status"] != "COMPLETED_CONDITIONAL_L02_PAD_AUGMENTED_DOMAIN":
        # Explicitly reviewed floating boundary residue; preserve it, never repair/snap it.
        assert source["status"] == "COMPLETED_CONDITIONAL_L02_PAD_AUGMENTED_DOMAIN_WITH_PAD_COVERAGE_RESIDUE"
        assert args.source_sha256 == "91e16302f7b7c6b5a43067a6208331f1aa1acee75440aa48ba035c02f1350a9d"
        assert args.source_review_sha256 == "ac4f8ba26c1ad976d843fc0177e449f24523382498b21f7c65ad5cf5a5cae7f6"
    assert source["drill_footprint_union"]["strict_domain_coverage"] is True
    assert source["drill_footprint_union"]["uncovered_area_um2"] == 0
    for name in ("domain", "support_map"):
        meta = source["outputs"][name]
        assert sha(Path(meta["path"])) == meta["sha256"]
    assert source["pad_augmented_domain"]["component_count"] == 1
    domain = shapely.from_wkb(Path(source["outputs"]["domain"]["path"]).read_bytes())
    assert domain.is_valid and domain.geom_type == "Polygon"
    with np.load(source["outputs"]["support_map"]["path"], allow_pickle=False) as data:
        x, y, diameter = data["support_x_pm"], data["support_y_pm"], data["support_diameter_pm"]
        native, excluded = data["native_drill_support_index"], data["excluded_drill_support_index"]
        native_active, native_ordinal = data["native_active_finite_index"], data["native_source_via_ordinal"]
        excluded_ids = data["excluded_via_ids_json_utf8"]
        drill_support = np.flatnonzero(data["support_is_drill"])
    assert native.shape == (76139,) and excluded.shape == (42,)
    assert np.array_equal(drill_support, np.unique(np.r_[native, excluded]))
    assert len(drill_support) == source["supports"]["unique_drill_support_count"]
    assert x.shape == y.shape == diameter.shape and np.all(diameter > 0)
    # Same16-sided inscribed drill convention as the accepted L14/L25 DC feasibility route.
    contacts = [fem.FiniteSheetContact(f"l02-drill-support:{i:06d}", f"conditional-l02-drill:{i:06d}",
                Point(float(x[i])*1e-6, float(y[i])*1e-6).buffer(float(diameter[i])*0.5e-6, quad_segs=4))
                for i in drill_support]
    boundary_vertices = int(shapely.get_num_coordinates(domain)) - len(domain.interiors) - 1
    predicted_vertices = boundary_vertices + 16*len(contacts)
    predicted_triangles = boundary_vertices + 32*len(contacts) + 2*len(domain.interiors) - 2
    assert predicted_vertices <= args.max_nodes and predicted_triangles <= args.max_triangles, "conditional Euler size exceeds proposed bounds"
    emit("source_preflight_ready", boundary_vertices=boundary_vertices, contacts=len(contacts), holes=len(domain.interiors),
         conditional_vertices=predicted_vertices, conditional_triangles=predicted_triangles)
    original_mesh, original_contacts = fem._compile_mesh, fem._prepare_contacts
    original_cdt, original_union = fem._constrained_delaunay_triangles, fem.unary_union
    original_stiffness, original_covers = fem._compile_stiffness, BaseGeometry.covers
    coverage = {"calls": 0, "global_control_checks": 0, "indexed_predicate_s": 0., "global_controls_s": 0.}
    cdt_checkpoints = []
    def prepared_contacts(island, *a, **kw):
        from types import SimpleNamespace
        boundary = island.boundary
        shapely.prepare(island); shapely.prepare(boundary)
        checked = original_contacts(SimpleNamespace(covers=island.covers, boundary=boundary), *a, **kw)
        emit("strict_contacts_accepted", count=len(checked))
        return checked
    def prepared_mesh(island, *a, **kw):
        shapely.prepare(island)
        indexed_covers = coverage_probe.indexed_triangle_covers(island)
        emit("hole_coverage_index_ready", holes=len(island.interiors))
        def local_covers(this, candidate):
            if this is not island or not isinstance(candidate, Polygon) or len(candidate.exterior.coords) != 4 or candidate.interiors:
                return original_covers(this, candidate)
            if not candidate.is_valid or candidate.area <= 0:
                return original_covers(this, candidate)
            clock = time.perf_counter()
            value = indexed_covers(candidate)
            coverage["indexed_predicate_s"] += time.perf_counter()-clock
            coverage["calls"] += 1
            if coverage["calls"] <= 16 or coverage["calls"] % 4096 == 0:
                clock = time.perf_counter()
                assert value == original_covers(island, candidate), "indexed/global triangle coverage differs"
                coverage["global_controls_s"] += time.perf_counter()-clock
                coverage["global_control_checks"] += 1
            if coverage["calls"] % 32768 == 0:
                emit("triangle_coverage", **coverage)
            return value
        BaseGeometry.covers = local_covers
        try:
            return original_mesh(island, *a, **kw)
        finally:
            BaseGeometry.covers = original_covers
    def timed_cdt(face):
        large = shapely.get_num_coordinates(face) > 10000
        if large:
            emit("large_face_cdt_start", coordinates=int(shapely.get_num_coordinates(face)))
        clock = time.perf_counter()
        triangles = original_cdt(face)
        cdt_elapsed = time.perf_counter()-clock
        if large:
            cdt_checkpoints.append(save_cdt_checkpoint(args.output, face, triangles))
            emit("large_face_cdt_done", triangles=len(triangles.geoms), cdt_elapsed_s=cdt_elapsed)
            emit("unvalidated_cdt_checkpoint_saved", **cdt_checkpoints[-1])
        return triangles
    def timed_union(items):
        emit("mesh_union_start", item_count=len(items))
        value = original_union(items)
        emit("mesh_union_done")
        return value
    def timed_stiffness(mesh):
        emit("stiffness_start", nodes=len(mesh.node_xy_m), triangles=len(mesh.triangles))
        value = original_stiffness(mesh)
        emit("stiffness_done", nnz=value.nnz)
        return value
    try:
        fem._prepare_contacts, fem._compile_mesh = prepared_contacts, prepared_mesh
        fem._constrained_delaunay_triangles, fem.unary_union = timed_cdt, timed_union
        fem._compile_stiffness = timed_stiffness
        sheet = fem.compile_tri_fem_sheet("research:l02:pad-domain:um:mesh-feasibility", domain,
            contacts=contacts, conductivity_s_per_m=59590000., thickness_m=20e-6, refinement_levels=0,
            max_nodes=args.max_nodes, max_triangles=args.max_triangles, max_contacts=len(contacts), max_contact_work=50000000)
    finally:
        fem._prepare_contacts, fem._compile_mesh = original_contacts, original_mesh
        fem._constrained_delaunay_triangles, fem.unary_union = original_cdt, original_union
        fem._compile_stiffness, BaseGeometry.covers = original_stiffness, original_covers
    assert [c.contact_id for c in sheet.mesh.contacts] == [c.contact_id for c in contacts]
    node_lists = [c.node_indices for c in sheet.mesh.contacts]
    triangle_lists = [c.triangle_indices for c in sheet.mesh.contacts]
    output = args.output / "mesh-stiffness.npz"
    mass.atomic_npz(output, node_xy_um=sheet.mesh.node_xy_m, triangles=sheet.mesh.triangles,
        stiffness_data=sheet.stiffness.data, stiffness_indices=sheet.stiffness.indices, stiffness_indptr=sheet.stiffness.indptr,
        stiffness_shape=np.asarray(sheet.stiffness.shape), contact_support_index=drill_support,
        contact_node_indices=np.concatenate(node_lists), contact_node_indptr=np.cumsum([0]+[len(a) for a in node_lists]),
        contact_triangle_indices=np.concatenate(triangle_lists), contact_triangle_indptr=np.cumsum([0]+[len(a) for a in triangle_lists]),
        native_drill_support_index=native, excluded_drill_support_index=excluded, native_active_finite_index=native_active,
        native_source_via_ordinal=native_ordinal, excluded_via_ids_json_utf8=excluded_ids)
    result = {"program":"SPD Decap PI Evaluator","version":"0.23.1","status":"COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT",
        "script_sha256":sha(Path(__file__)), "source_result":{"path":str(args.source_result),"sha256":args.source_sha256},
        "source_review_sha256":args.source_review_sha256, "source_status":source["status"],
        "source_pad_coverage":{"strict":source["pad_augmented_domain"]["strict_full_pad_union_coverage"],
            "uncovered_area_um2":source["pad_augmented_domain"]["full_pad_union_uncovered_area_um2"]},
        "inputs":{key:{"path":str(path),"sha256":expected} for key,(path,expected) in PINS.items()},
        "source_outputs":source["outputs"], "mesh_nodes":len(sheet.mesh.node_xy_m), "mesh_triangles":len(sheet.mesh.triangles),
        "contact_count":len(contacts), "stiffness_nnz":sheet.stiffness.nnz, "coverage":coverage,
        "unvalidated_cdt_checkpoints":cdt_checkpoints,
        "operator_identity_sha256":sheet.identity_sha256, "elapsed_s":time.monotonic()-started,
        "snapshot":{"path":str(output),"sha256":sha(output),"coordinate_units":"um", "stiffness_units":"dimensionless; sigma*t multiplies for DC admittance"},
        "scope":"Existing fixed-level native FEM compiler with unchanged strict contact/triangle/union checks and exact exterior/hole-interior coverage indexing with original-predicate controls. Micrometre coordinates are a DC similarity calculation only. Source pad copper is retained;16-sided drill disks are conditional filled equipotential contacts, not a chosen plating/current injection truth. Includes source support metadata for20 junction corrections and2 leaves, but does not attach or revive any circuit owner. No Kron/contact dense matrix, global circuit assembly, LU, G/C projection, magnetic current basis, convergence or PowerSI claim."}
    mass.atomic_json(args.output / "result.json", result)
    emit("mesh_snapshot_saved", nodes=result["mesh_nodes"], triangles=result["mesh_triangles"], result_sha256=sha(args.output / "result.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--source-result", type=Path)
    parser.add_argument("--source-sha256")
    parser.add_argument("--source-review-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-nodes", type=int, default=1800000)
    parser.add_argument("--max-triangles", type=int, default=2800000)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("PASS_L02_MESH_CONTACT_EULER_SELF_CHECK")
        raise SystemExit(0)
    if any(value is None for value in (args.source_result, args.source_sha256, args.source_review_sha256, args.output)):
        parser.error("--source-result/--source-sha256/--source-review-sha256/--output required")
    args.output = args.output.resolve(); args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        with (args.output / "stack-samples.log").open("x", encoding="utf-8") as stack_log:
            faulthandler.dump_traceback_later(60, repeat=True, file=stack_log)
            try:
                run(args)
            finally:
                faulthandler.cancel_dump_traceback_later()
    except BaseException as exc:
        mass.atomic_json(args.output / "failure.json", {"program":"SPD Decap PI Evaluator","version":"0.23.1",
            "status":"STOP_L02_SHEET_MESH_PREFLIGHT", "error_type":type(exc).__name__, "code":getattr(exc,"code",None), "error":str(exc)})
        raise
