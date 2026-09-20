"""Bounded DC-only similarity-coordinate and inward dyadic mesh experiment."""

from hashlib import sha256
import json
from math import floor, hypot
from pathlib import Path
import sys
from time import monotonic

import numpy as np
from shapely.affinity import scale
from shapely.geometry import Point, Polygon, box

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.solver.tri_fem_sheet import FiniteSheetContact, TriFemSheetError, compile_tri_fem_sheet


def inward_dyadic(polygon, quantum):
    """Each chosen vertex and the whole polygon must remain inside the input."""
    assert not polygon.interiors and polygon.is_valid
    vertices = []
    for x, y in list(polygon.exterior.coords)[:-1]:
        ix, iy = floor(x / quantum), floor(y / quantum)
        candidates = [(i * quantum, j * quantum) for i in range(ix - 1, ix + 3) for j in range(iy - 1, iy + 3)]
        inside = [v for v in candidates if polygon.covers(Point(v))]
        assert inside, "no inward dyadic vertex in the bounded search"
        vertices.append(min(inside, key=lambda v: hypot(v[0] - x, v[1] - y)))
    result = Polygon(vertices)
    assert result.is_valid and polygon.covers(result), "snapping cannot enlarge the source approximation"
    assert result.hausdorff_distance(polygon) <= 3 * quantum
    return result


def solve(artwork, contacts, level, sigma, thickness, name):
    sheet = compile_tri_fem_sheet(name, artwork, contacts=[FiniteSheetContact(f"barrel{i}", f"electrode:{i}", p) for i, p in enumerate(contacts)],
        conductivity_s_per_m=sigma, thickness_m=thickness, refinement_levels=level,
        max_nodes=30000, max_triangles=60000, max_contacts=2, max_contact_work=5000000)
    y = sheet.contact_admittance_s(0, model="dc")
    b = np.array([1., -1.])
    resistance = float((b @ np.linalg.pinv(y) @ b).real)
    norm = np.linalg.norm(y)
    assert resistance > 0 and np.linalg.norm(y @ np.ones(2)) < norm * 1e-10
    assert np.linalg.norm(y - y.T) < norm * 1e-10 and np.linalg.eigvalsh(y.real).min() >= -norm * 1e-10
    return {"level": level, "nodes": len(sheet.mesh.node_xy_m), "triangles": len(sheet.mesh.triangles), "r_ohm": resistance, "operator_sha256": sheet.identity_sha256}


def main():
    output = ROOT / "docs/evaluation-research/astra_dyadic_trace_sheet_2026-09-07.json"
    if output.exists():
        raise FileExistsError(output)
    started = monotonic()
    path = ROOT / "docs/evaluation-research/astra_isolated_trace_patch_2026-09-06.json"
    blob = path.read_bytes()
    assert sha256(blob).hexdigest() == "3bb5027424488b17cb2c6f85c75f5edeb0ff9b041929c5b6c3087417b9c4554d"
    source = json.loads(blob)
    layer = source["source_trace"]["stackup_layer"]
    sigma, thickness = layer["conductivity_s_per_m"], layer["thickness_um"] * 1e-6
    assert source["source_trace"]["centerline_length_um"] == 60 and source["source_trace"]["trace"]["width_pm"] == 50000000
    # For 2D DC, grad transforms as 1/s and area as s^2: sheet G is unchanged.
    # This is a similarity-coordinate experiment, not an SI mesh usable for AC/L.
    control_domain = box(0, -25, 60, 25)
    control_exact = 60 / (sigma * thickness * 50)
    controls = []
    for factor in (1., 1e-6):
        sheet = compile_tri_fem_sheet(f"research:dc-scale-control:{factor}", scale(control_domain, xfact=factor, yfact=factor, origin=(0, 0)), contacts=(),
            conductivity_s_per_m=sigma, thickness_m=thickness, refinement_levels=0, max_nodes=100, max_triangles=100)
        potential = np.array([xy[0] / (60 * factor) for xy in sheet.mesh.node_xy_m])
        conductance = float(potential @ sheet.stiffness @ potential) * sigma * thickness
        assert abs(1 / conductance / control_exact - 1) < 1e-11
        controls.append({"xy_scale_from_um_numbers": factor, "linear_dirichlet_energy_conductance_s": conductance, "operator_sha256": sheet.identity_sha256})
    quantum = 2.**-20  # 0.953674 pm in the normalized micrometre coordinates.
    rows, geometries = [], []
    failure = None
    for sides in (64, 128):
        centers = [Point(0, 0), Point(60, 0)]
        original = box(0, -25, 60, 25).union(centers[0].buffer(30, quad_segs=sides // 4)).union(centers[1].buffer(30, quad_segs=sides // 4))
        artwork = inward_dyadic(original, quantum)
        original_ports = [p.buffer(20, quad_segs=sides // 4) for p in centers]
        contacts = [inward_dyadic(p, quantum) for p in original_ports]
        assert all(artwork.covers(p) for p in contacts) and contacts[0].disjoint(contacts[1])
        geometries.append({"circle_sides": sides, "artwork_inner_approximation": True,
            "dyadic_artwork_hausdorff_um": artwork.hausdorff_distance(original),
            "dyadic_electrode_hausdorff_um": [a.hausdorff_distance(b) for a, b in zip(contacts, original_ports)],
            "artwork_wkb_sha256": sha256(artwork.wkb).hexdigest(), "electrode_wkb_sha256": [sha256(p.wkb).hexdigest() for p in contacts]})
        for level in ((0, 1, 2, 3) if sides == 64 else (2, 3)):
            try:
                row = solve(artwork, contacts, level, sigma, thickness, f"research:Trace311318:dyadic-um:dc-only:{sides}")
                rows.append({"circle_sides": sides, **row})
            except TriFemSheetError as exc:
                failure = {"circle_sides": sides, "level": level, "code": exc.code, "detail": str(exc)}
                break
            if monotonic() - started > 55:
                failure = {"code": "RESEARCH_TIME_BUDGET", "detail": "55 s work budget reached"}
                break
        if failure:
            break
    changes = {}
    if failure is None:
        for sides in (64, 128):
            chosen = [r for r in rows if r["circle_sides"] == sides][-2:]
            changes[f"mesh_{sides}_relative"] = abs(chosen[1]["r_ohm"] - chosen[0]["r_ohm"]) / chosen[1]["r_ohm"]
        changes["circle_64_to_128_relative"] = abs(rows[3]["r_ohm"] - rows[-1]["r_ohm"]) / rows[-1]["r_ohm"]
    accepted = failure is None and all(v <= .02 for v in changes.values())
    result = {"program": source["program"], "version": source["version"], "status": "ACCEPT_ASSUMED_LOCAL_DC_REFINEMENT_ONLY" if accepted else "STOP_DYADIC_TRACE_EXPERIMENT",
        "input_sha256": sha256(blob).hexdigest(), "normalized_xy_unit": "um numerical coordinates; DC stiffness only", "dyadic_quantum_um": quantum,
        "rectangle_analytic_r_ohm": control_exact, "similarity_controls": controls, "geometries": geometries, "sheet_rows": rows,
        "prior_control_attempt": "Boundary-touching finite rectangle electrodes rejected as CONTACT_BOUNDARY_AMBIGUOUS before the source experiment; corrected control evaluates the exact linear Dirichlet energy on a contact-free rectangle, with no guard change.",
        "relative_changes": changes, "gate": .02, "failure": failure, "elapsed_s": monotonic() - started,
        "scope": "New inward polygon approximations of the same source trace/pads/qualified filled-microvia electrodes, in DC similarity coordinates. Original source and prior STOP remain unchanged. All existing mesh/contact/void guards run unchanged. Fixed electrodes within each mesh-refinement sequence; circle discretization changes are checked separately. No SI-coordinate AC/L operator, 3D injection, production eligibility attestation, complete-board replacement or PowerSI claim."}
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "relative_changes", "failure", "elapsed_s")}))


if __name__ == "__main__":
    main()
