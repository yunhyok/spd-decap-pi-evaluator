"""Bounded level-zero FEM feasibility on the actual L14 plane/trace domain."""
import hashlib
import faulthandler
from functools import lru_cache
from math import floor
import json
from pathlib import Path
import os
import sys
import time
from types import SimpleNamespace

import shapely
import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from qualify_astra_l14_trace_centerlines import DIRECTORY, services
from recover_astra_field_run02 import ROOT
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog, _write_json
from spd_decap_pi._core.solver import tri_fem_sheet as fem


def main(*, l25=False):
    label = "l25" if l25 else "l14"
    source_directory = ROOT / "outputs/research/astra-l25-source-sheet-01" if l25 else DIRECTORY
    domain_name = "l25-source-domain.wkb" if l25 else "l14-plane-trace-domain-flat.wkb"
    footprint_name = "receipt.json" if l25 else "l14-via-footprint-qualification.json"
    domain_sha = "e66b5fedb01d04ebd4e768f6354f6215b3d18f57d2c83d23c8f45a759a532976" if l25 else "2fb52931635cf0ad78507b076570d055460f0ed86c1542821a50a9c1ef7f5124"
    footprint_sha = "a45f6601bae33dc72f9c56190cd85a42037c4046c460bedbdd7cecbc9e85851b" if l25 else "98848ffea1eaa07f601d24fbfdf8ba642226ded7d05d9e5d2f1286af14854d9c"
    max_runtime = 600. if l25 else 300.
    max_memory = (16 if l25 else 8) * 2**30
    output = ROOT / f"outputs/research/astra-{label}-sheet-mesh-preflight-{'01' if l25 else '05'}"
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    profile = (output / "stack-samples.log").open("w", encoding="utf-8")
    faulthandler.dump_traceback_later(15, repeat=True, file=profile)
    budget = base._Budget(max_runtime, max_memory, output / "progress.jsonl")
    budget.emit("start", pid=os.getpid(), scope="local source sheet mesh only")
    watcher = _start_shutdown_safe_watchdog(budget)
    original_mesh = fem._compile_mesh
    original_contacts = fem._prepare_contacts
    original_cdt = fem._constrained_delaunay_triangles
    original_covers = BaseGeometry.covers
    original_union = fem.unary_union
    original_stiffness = fem._compile_stiffness
    result = {"program": base.APP_DISPLAY_NAME, "status": f"STOP_{label.upper()}_SOURCE_SHEET_MESH_PREFLIGHT"}
    try:
        domain_bytes = (source_directory / domain_name).read_bytes()
        assert hashlib.sha256(domain_bytes).hexdigest() == domain_sha
        footprint_bytes = (source_directory / footprint_name).read_bytes()
        assert hashlib.sha256(footprint_bytes).hexdigest() == footprint_sha
        domain = shapely.from_wkb(domain_bytes)
        footprint = json.loads(footprint_bytes)
        conductivity = footprint["material"]["conductivity_s_per_m"] if l25 else 59590000.
        thickness_m = footprint["material"]["thickness_um"] * 1e-6 if l25 else 20e-6
        contact_prefix = "l25-drill-radius-group" if l25 else "l14-filled-core-group"
        contacts = []
        for i, row in enumerate(footprint["coincident_contact_groups"]):
            contacts.append(fem.FiniteSheetContact(f"{contact_prefix}-{i}", "conditional-contact:" + "+".join(row["via_ids"]),
                Point(row["xy_um"]).buffer(row["maximum_barrel_radius_um"], quad_segs=4)))
        assert len(contacts) == (175 if l25 else 1660)
        result.update({"domain_wkb_sha256": hashlib.sha256(domain_bytes).hexdigest(),
                       "footprint_receipt_sha256": hashlib.sha256(footprint_bytes).hexdigest(),
                       "source_hole_count": len(domain.interiors), "source_coordinate_count": int(shapely.get_num_coordinates(domain)),
                       "contact_count": len(contacts), "contact_circle_sides": 16, "refinement_level": 0,
                       "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        def prepared_mesh(island, *args, **kwargs):
            # GEOS prepared indexing changes performance only, not geometry,
            # validation predicates, mesh bounds, or operator mathematics.
            shapely.prepare(island)
            stats = {"calls": 0, "global_control_checks": 0, "tile_width_um": 1024, "cache_maxsize": 2048}
            @lru_cache(maxsize=2048)
            def clipped_domain(key):
                rectangle = box(*(value * 1024. for value in key))
                clipped = island.intersection(rectangle)
                if not clipped.is_valid:
                    raise ValueError("invalid local domain intersection")
                shapely.prepare(clipped)
                return rectangle, clipped
            def local_covers(this, candidate):
                if this is not island or not isinstance(candidate, Polygon) or len(candidate.exterior.coords) != 4 or candidate.interiors:
                    return original_covers(this, candidate)
                x0, y0, x1, y1 = candidate.bounds
                key = (floor(x0 / 1024.), floor(y0 / 1024.), floor(x1 / 1024.) + 1, floor(y1 / 1024.) + 1)
                rectangle, clipped = clipped_domain(key)
                if not original_covers(rectangle, candidate):
                    raise ValueError("local coverage box does not contain full triangle")
                local = original_covers(clipped, candidate)
                stats["calls"] += 1
                if stats["calls"] <= 16 or stats["calls"] % 4096 == 0:
                    if local != original_covers(island, candidate):
                        raise ValueError("local/global triangle coverage predicate mismatch")
                    stats["global_control_checks"] += 1
                if stats["calls"] % 16384 == 0:
                    budget.emit("local_coverage_progress", **stats, cache_info=list(clipped_domain.cache_info()))
                return local
            BaseGeometry.covers = local_covers
            try:
                return original_mesh(island, *args, **kwargs)
            finally:
                BaseGeometry.covers = original_covers
                result["local_coverage_cache"] = {**stats, "cache_info": list(clipped_domain.cache_info())}
        def prepared_contacts(island, *args, **kwargs):
            budget.emit("contact_validation_start")
            boundary = island.boundary
            shapely.prepare(island)
            shapely.prepare(boundary)
            # Preserve every original predicate and uniqueness/work guard;
            # cache the identical boundary instead of rebuilding it 1660 times.
            proxy = SimpleNamespace(covers=island.covers, boundary=boundary)
            result = original_contacts(proxy, *args, **kwargs)
            budget.emit("contact_validation_done")
            return result
        def timed_cdt(face):
            large = shapely.get_num_coordinates(face) > 10000
            if large:
                budget.emit("large_face_cdt_start", coordinates=int(shapely.get_num_coordinates(face)))
            triangles = original_cdt(face)
            if large:
                budget.emit("large_face_cdt_done", triangles=len(triangles.geoms))
            return triangles
        def timed_union(items):
            budget.emit("mesh_union_start", item_count=len(items))
            union = original_union(items)
            budget.emit("mesh_union_done")
            return union
        def timed_stiffness(mesh):
            budget.emit("stiffness_start", nodes=len(mesh.node_xy_m), triangles=len(mesh.triangles))
            matrix = original_stiffness(mesh)
            budget.emit("stiffness_done", nnz=matrix.nnz)
            return matrix
        fem._compile_mesh = prepared_mesh
        fem._prepare_contacts = prepared_contacts
        fem._constrained_delaunay_triangles = timed_cdt
        fem.unary_union = timed_union
        fem._compile_stiffness = timed_stiffness
        budget.emit("mesh_compile_start", contacts=len(contacts), holes=len(domain.interiors))
        sheet = fem.compile_tri_fem_sheet(f"research:{label}:source-domain:um:mesh-feasibility", domain,
            contacts=contacts, conductivity_s_per_m=conductivity, thickness_m=thickness_m,
            refinement_levels=0, max_nodes=600000 if l25 else 250000,
            max_triangles=1200000 if l25 else 500000,
            max_contacts=len(contacts), max_contact_work=10000000)
        budget.emit("mesh_compile_done", nodes=len(sheet.mesh.node_xy_m), triangles=len(sheet.mesh.triangles))
        result.update({"mesh_nodes": len(sheet.mesh.node_xy_m),
                       "mesh_triangles": len(sheet.mesh.triangles), "stiffness_nnz": sheet.stiffness.nnz,
                       "operator_identity_sha256": sheet.identity_sha256})
        contact_nodes = [item.node_indices for item in sheet.mesh.contacts]
        packed_contacts = np.frombuffer(json.dumps([
            {"contact_id": item.contact_id, "owner_id": item.owner_id,
             "footprint_sha256": item.footprint_sha256}
            for item in sheet.mesh.contacts], ensure_ascii=False).encode("utf-8"), dtype=np.uint8)
        snapshot_path = output / "mesh-stiffness.npz"
        with snapshot_path.open("xb") as handle:
            np.savez_compressed(handle, node_xy_um=sheet.mesh.node_xy_m, triangles=sheet.mesh.triangles,
                stiffness_data=sheet.stiffness.data, stiffness_indices=sheet.stiffness.indices,
                stiffness_indptr=sheet.stiffness.indptr, stiffness_shape=np.asarray(sheet.stiffness.shape),
                contacts_json_utf8=packed_contacts, contact_node_indices=np.concatenate(contact_nodes),
                contact_node_indptr=np.cumsum([0] + [len(nodes) for nodes in contact_nodes]))
        with np.load(snapshot_path, allow_pickle=False) as saved:
            assert all(saved[key].dtype.kind != "O" for key in saved.files)
            assert np.array_equal(saved["node_xy_um"], sheet.mesh.node_xy_m)
            assert np.array_equal(saved["stiffness_data"], sheet.stiffness.data)
        result["snapshot"] = {"path": str(snapshot_path), "sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            "coordinate_units": "um", "stiffness_units": "dimensionless; DC admittance is sigma*t*stiffness",
            "conductivity_s_per_m": sheet.conductivity_s_per_m, "thickness_m": sheet.thickness_m}
        budget.emit("mesh_snapshot_saved", **result["snapshot"])
        result["status"] = f"COMPLETED_{label.upper()}_SOURCE_SHEET_MESH_PREFLIGHT"
        # Preserve a complete checkpoint before teardown; this does not assert
        # that the later process shutdown or final receipt will succeed.
        _write_json(output / "mesh-compile-checkpoint.json", result)
    except Exception as exc:
        result["status"] = f"STOP_{label.upper()}_SOURCE_SHEET_MESH_PREFLIGHT"
        result["error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", None), "message": str(exc)}
        budget.emit("stopped", **result["error"])
    finally:
        fem._compile_mesh = original_mesh
        fem._prepare_contacts = original_contacts
        fem._constrained_delaunay_triangles = original_cdt
        BaseGeometry.covers = original_covers
        fem.unary_union = original_union
        fem._compile_stiffness = original_stiffness
        budget.stop.set()
        watcher.join(timeout=5.)
        faulthandler.cancel_dump_traceback_later()
        profile.close()
    result["resource"] = {"elapsed_s": budget.elapsed(), "peak_private_bytes": budget.peak_private,
                          "peak_working_set_bytes": budget.peak_working_set, "max_runtime_s": max_runtime, "max_memory_bytes": max_memory}
    result["limitations"] = ["No geometric repair, coordinate snapping or source simplification is applied.",
        "16-sided inscribed drill-radius disks and equipotential shared-contact treatment are conditional mesh inputs, not fill, plating, injection or convergence evidence.",
        "Micrometre-coordinate values are a DC similarity-coordinate feasibility test; they are not an SI geometry for AC or magnetic operators.",
        "No contact reduction, global source G/C partition, Device response or PowerSI accuracy claim is produced."]
    _write_json(output / "receipt.json", result)
    print(json.dumps(result), flush=True)
    return 0 if result["status"].startswith("COMPLETED") else 1


if __name__ == "__main__":
    # Set-identity control: every candidate lies in its clipping rectangle.
    test_domain = box(0, 0, 4, 4).difference(box(1, 1, 2, 2))
    for coords in (((0, 0), (1, 0), (0, 1)), ((1.1, 1.1), (1.9, 1.1), (1.1, 1.9)), ((0, 0), (3, 0), (0, 3))):
        triangle = Polygon(coords)
        rectangle = box(*triangle.bounds)
        assert rectangle.covers(triangle)
        assert test_domain.covers(triangle) == test_domain.intersection(rectangle).covers(triangle)
    if sys.argv[1:] not in ([], ["--l25"]):
        raise SystemExit("Usage: preflight_astra_l14_sheet_mesh.py [--l25]")
    raise SystemExit(main(l25=sys.argv[1:] == ["--l25"]))
