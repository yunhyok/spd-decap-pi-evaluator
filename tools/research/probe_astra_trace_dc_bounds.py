"""Analytic DC sheet bounds and a local, ownership-preserving native RL splice."""

from dataclasses import asdict
from hashlib import sha256
import json
from math import atan, pi, sqrt
from pathlib import Path
import sys
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.solver.global_mna import DifferentialPort, SeriesBranchBlock, compile_global_mna


def main():
    output = ROOT / "docs/evaluation-research/astra_trace_dc_bounds_2026-09-06.json"
    if output.exists():
        raise FileExistsError(output)
    started = monotonic()
    source = ROOT / "docs/evaluation-research/astra_isolated_trace_patch_2026-09-06.json"
    blob = source.read_bytes()
    digest = sha256(blob).hexdigest()
    assert digest == "3bb5027424488b17cb2c6f85c75f5edeb0ff9b041929c5b6c3087417b9c4554d"
    data = json.loads(blob)
    trace = data["source_trace"]
    length = trace["centerline_length_um"] * 1e-6
    width = trace["trace"]["width_pm"] * 1e-12
    layer = trace["stackup_layer"]
    sheet_conductance = layer["conductivity_s_per_m"] * layer["thickness_um"] * 1e-6
    assert len(data["padstacks"]) == len(data["pad_shapes"]) == 2
    assert {p["drill_diameter_pm"] for p in data["padstacks"]} == {40000000}
    assert {p["width_pm"] for p in data["pad_shapes"]} == {60000000}
    radius, pad_radius = 20e-6, 30e-6
    assert 0 < 2 * radius < length and 2 * radius <= width
    # The gap between the enlarged electrodes is exactly the rectangular strip.
    assert sqrt(pad_radius**2 - radius**2) <= width / 2
    lower = (length - 2 * radius) / (sheet_conductance * width)
    integral = 2 * length / sqrt(length**2 - 4 * radius**2) * atan(sqrt((length + 2 * radius) / (length - 2 * radius))) - pi / 2
    upper = 1 / (sheet_conductance * integral)
    # Independent quadrature of the unit-current horizontal-channel construction.
    quadrature = []
    for count in (32, 64):
        x, w = np.polynomial.legendre.leggauss(count)
        theta = (x + 1) * pi / 4
        value = float((w @ (2 * radius * np.cos(theta) / (length - 2 * radius * np.cos(theta)))) * pi / 4)
        assert abs(value / integral - 1) < 1e-12
        quadrature.append({"points": count, "integral": value})
    assert 0 < lower < upper < trace["nominal_uniform_rectangle_dc_ohm"]
    owners = {r["owner_id"]: r for r in data["native_owner_refs"]}
    links = {l["ordinal"]: l for l in data["native_links"]}
    via_owners = ("via:via336274", "via:via336273")
    native = [links[owners[o]["link_ordinal"]] for o in via_owners]
    assert all(owners[o]["kind"] == 1 for o in via_owners)
    frequency = 1e6
    impedances = [l["resistance_ohm"] + 2j * pi * frequency * l["inductance_h"] for l in native]
    trace_owner = trace["trace"]["owner_id"]
    assert trace_owner not in via_owners
    port = (DifferentialPort("local-L05-to-L07-boundary", "left", "right"),)
    rows = []
    for name, resistance in (("native_ideal_joint", 0.), ("dc_sheet_lower_bound", lower), ("dc_sheet_upper_bound", upper)):
        nodes = ("left", "joint", "right") if resistance == 0 else ("left", "pad0", "pad1", "right")
        positive = ("left", "joint" if resistance == 0 else "pad1")
        negative = ("joint" if resistance == 0 else "pad0", "right")
        blocks = [SeriesBranchBlock((l["link_id"],), (positive[i],), (negative[i],), np.array([[impedances[i]]]), l["link_id"], owner_ids=(via_owners[i],)) for i, l in enumerate(native)]
        if resistance:
            blocks.append(SeriesBranchBlock(("Trace311318",), ("pad0",), ("pad1",), np.array([[resistance]]), "source-trace-pad-dc-bound", owner_ids=(trace_owner,)))
        solves = [compile_global_mna(order, branch_blocks=blocks, ports=port).solve(frequency) for order in (nodes, tuple(reversed(nodes)))]
        expected = sum(impedances) + resistance
        for solution in solves:
            z = solution.impedance_ohm[0, 0]
            assert abs(z - expected) < abs(expected) * 1e-11
        assert solves[0].diagnostics.gauge_node_ids != solves[1].diagnostics.gauge_node_ids
        rows.append({"case": name, "trace_dc_r_ohm": resistance, "z_ohm": [float(z.real), float(z.imag)], "analytic_z_ohm": [expected.real, expected.imag], "gauges": [asdict(s.diagnostics) for s in solves]})
    result = {"program": data["program"], "version": data["version"], "status": "ACCEPT_ASSUMED_LOCAL_DC_BOUNDS_AND_SPLICE_ONLY",
        "input": str(source.relative_to(ROOT)), "input_sha256": digest,
        "geometry_m": {"center_spacing": length, "trace_width": width, "pad_radius": pad_radius, "electrode_radius": radius},
        "sheet_conductance_s": sheet_conductance, "r_lower_ohm": lower, "r_upper_ohm": upper,
        "channel_integral": integral, "independent_quadrature": quadrature,
        "bound_basis": "Lower: enlarge equipotential electrodes to all copper at x<=r and x>=L-r; intervening width-W rectangle. Upper: admissible horizontal unit-current flow Jx(y)=1/(I*[L-2*sqrt(r*r-y*y)]), Jy=0, |y|<=r; its energy is 1/(sigma*t*I). Thomson/Rayleigh variational principles.",
        "reference": "https://math.dartmouth.edu/~doyle/docs/walkspdf/walks.pdf",
        "frequency_hz": frequency, "local_mna_solves": rows, "retained_owners": list(via_owners), "new_trace_owner": trace_owner,
        "native_gc_changed": False, "full_board_solve_executed": False, "powersi_comparison_executed": False,
        "scope": "Bounds for an assumed exact-circle, uniform-thickness 2D DC sheet with equipotential 40 um filled-microvia sections under the existing qualified MLO assumption. Not bounds on 3D conductor impedance, the 64-gon FEM geometry, AC sheet response, full PDN or PowerSI. Using each DC bound in a 1 MHz local RL network tests composition only; no point estimate or product candidate is selected. Both native via RL owners are retained unchanged; only their ideal degree-two joint is split. No native G/C is present in this isolated boundary experiment.",
        "elapsed_s": monotonic() - started}
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "r_lower_ohm", "r_upper_ohm", "elapsed_s")}))


if __name__ == "__main__":
    main()
