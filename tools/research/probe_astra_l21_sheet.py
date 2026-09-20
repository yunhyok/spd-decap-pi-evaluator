"""Isolated source L21 sheet witness, not a full-port or PDN replacement."""

from hashlib import sha256
import json
from math import cos, hypot, pi
from pathlib import Path
import sys
from time import monotonic

import numpy as np
from shapely.geometry import Point, Polygon

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.solver.tri_fem_sheet import FiniteSheetContact, TriFemSheetError, compile_tri_fem_sheet


def main():
    source = ROOT / "outputs/research/astra-step3-source-index-01/l21-plane-bridge.json"
    target = ROOT / "docs/evaluation-research/astra_l21_sheet_2026-09-06.json"
    if target.exists():
        raise FileExistsError("refusing to replace recorded sheet witness")
    started = monotonic()
    payload = source.read_bytes()
    record = json.loads(payload)
    assert record["status"] == "ACCEPT_LOCAL_SOURCE_COPPER_CONTACT_ONLY"
    assert all(c["entire_pad_disk_inside_source_copper"] for c in record["contacts"])
    material, = record["stackup_rows"]
    assert material["material_name"] == "COPPER"
    sigma, thickness = material["conductivity_s_per_m"], material["thickness_um"] * 1e-6
    contacts = {c["node"]["node_id"]: c for c in record["contacts"]}
    touching = []
    for i, a in enumerate(record["contacts"]):
        for b in record["contacts"][i + 1:]:
            # Test original integer-pm disks before tessellation can hide exact tangency.
            distance = hypot(a["node"]["x_pm"] - b["node"]["x_pm"], a["node"]["y_pm"] - b["node"]["y_pm"])
            gap = distance - (a["pad_shape"]["width_pm"] + b["pad_shape"]["width_pm"]) / 2
            if gap <= 0:
                touching.append({"nodes": [a["node"]["node_id"], b["node"]["node_id"]], "gap_pm": gap})
    assert touching == [{"nodes": ["Node2140578", "Node2140579"], "gap_pm": 0.0}]
    island = Polygon([(v["x_um"] * 1e-6, v["y_um"] * 1e-6) for v in record["vertices"]])
    selected = ("Node2140546", "Node2140547")
    ports = []
    for name in selected:
        c = contacts[name]
        center = Point(c["node"]["x_pm"] * 1e-12, c["node"]["y_pm"] * 1e-12)
        # ponytail: 64-sided inscribed disks; explicit boundary error below, not exact curved electrodes.
        disk = center.buffer(c["radius_um"] * 1e-6, quad_segs=16)
        ports.append(FiniteSheetContact(name, "research:l21-pad:" + c["via"]["owner_id"], disk))
    rows = []
    for level in (0, 1):
        try:
            sheet = compile_tri_fem_sheet(
                "research:l21:primitive109688", island, contacts=ports,
                conductivity_s_per_m=sigma, thickness_m=thickness, refinement_levels=level,
                max_nodes=4000, max_triangles=8000, max_contacts=4, max_contact_work=200000,
            )
        except TriFemSheetError as exc:
            rows.append({"refinement_level": level, "status": "STOP", "error_code": exc.code, "error": str(exc)})
            break
        y = sheet.contact_admittance_s(0, model="dc")
        incidence = np.array([1., -1.])
        resistance = float((incidence @ np.linalg.pinv(y) @ incidence).real)
        norm = float(np.linalg.norm(y))
        floating = float(np.linalg.norm(y @ np.ones(2)) / norm)
        reciprocal = float(np.linalg.norm(y - y.T) / norm)
        assert resistance > 0 and floating < 1e-10 and reciprocal < 1e-10
        assert np.linalg.eigvalsh(y.real).min() >= -norm * 1e-10
        assert not sheet.production_eligible
        rows.append({"refinement_level": level, "nodes": len(sheet.mesh.node_xy_m),
                     "triangles": len(sheet.mesh.triangles), "dc_resistance_ohm": resistance,
                     "admittance_s": y.real.tolist(), "floating_relative": floating,
                     "reciprocity_relative": reciprocal, "operator_identity_sha256": sheet.identity_sha256})
        if monotonic() - started > 55:
            raise TimeoutError("bounded local sheet study exceeded 55 seconds")
    complete = len(rows) == 2 and all("dc_resistance_ohm" in row for row in rows)
    change = abs(rows[1]["dc_resistance_ohm"] - rows[0]["dc_resistance_ohm"]) / rows[1]["dc_resistance_ohm"] if complete else None
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "ACCEPT_ISOLATED_DC_DIAGNOSTIC_ONLY" if complete else "STOP_LOCAL_SHEET_MESH", "production_eligible": False,
              "source_record": str(source), "source_record_sha256": sha256(payload).hexdigest(),
              "source_primitive": record["primitive"], "material": material, "selected_nodes": selected,
              "full_four_contact_status": "STOP_EXACT_PAD_TANGENCY", "touching_contacts": touching,
              "max_circle_boundary_error_um": max(contacts[n]["radius_um"] for n in selected) * (1 - cos(pi / 64)),
              "assumptions": "Two whole pad disks are equipotential electrodes; other launch locations remain ordinary sheet copper. This is a lateral sheet subproblem with the two selected terminal currents balancing each other, not a GND assignment or a model of all via-barrel injections.",
              "rows": rows, "coarse_to_fine_relative_change": change,
              "scope": "Existing triangular FEM; DC R only. No external/mutual L, G/C, dielectric/return, source ownership replacement, convergence attestation, full four-port transfer, or PowerSI accuracy claim.",
              "elapsed_s": monotonic() - started}
    with target.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
