"""Source-coordinate filament mutual coefficients; no filled-via or return claim."""

from hashlib import sha256
import json
from math import hypot, pi
from pathlib import Path
import sys
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.solver.via_peec import MU_0_H_PER_M, _finite_parallel_neumann_integral


def main():
    base = ROOT / "outputs/research/astra-step3-source-index-01"
    target = ROOT / "docs/evaluation-research/astra_source_via_mutual_2026-09-06.json"
    if target.exists():
        raise FileExistsError("refusing to replace source mutual diagnostic")
    started = monotonic()
    payload = (base / "mutual-via-inputs.json").read_bytes()
    source = json.loads(payload)
    launches = json.loads((base / "selected-launches.json").read_text())
    power = next(v for v in launches["power_records"]["vias"] if v["via_id"] == "Via336239")
    stack = source["stackup_rows"]
    assert [s["layer_ordinal"] for s in stack] == list(range(40, 55))
    assert stack[0]["layer_name"] == power["start_layer_id"] and stack[-1]["layer_name"] == power["end_layer_id"]
    assert power["start_x_pm"] == power["end_x_pm"] and power["start_y_pm"] == power["end_y_pm"]
    total = sum(s["thickness_um"] for s in stack) * 1e-6
    first, last = stack[0]["thickness_um"] * 1e-6, stack[-1]["thickness_um"] * 1e-6
    intervals = {"inner_foil_faces": (first, total - last),
                 "foil_midplanes": (first / 2, total - last / 2), "outer_foil_faces": (0., total)}
    rows = []
    abscissa, weights = np.polynomial.legendre.leggauss(32)
    for ground in source["ground_vias"]:
        assert ground["start_layer_id"] == power["start_layer_id"] and ground["end_layer_id"] == power["end_layer_id"]
        assert ground["start_x_pm"] == ground["end_x_pm"] and ground["start_y_pm"] == ground["end_y_pm"]
        distance = hypot(power["start_x_pm"] - ground["start_x_pm"], power["start_y_pm"] - ground["start_y_pm"]) * 1e-12
        values = {name: MU_0_H_PER_M / (4 * pi) * _finite_parallel_neumann_integral(a, b, a, b, distance)
                  for name, (a, b) in intervals.items()}
        length = intervals["foil_midplanes"][1] - intervals["foil_midplanes"][0]
        x = abscissa * length / 2
        kernel = 1 / np.sqrt(distance**2 + (x[:, None] - x[None, :])**2)
        quadrature = MU_0_H_PER_M / (4 * pi) * length**2 / 4 * float(weights @ kernel @ weights)
        relative = abs(quadrature - values["foil_midplanes"]) / quadrature
        assert relative < 1e-11 and 0 < values["inner_foil_faces"] < values["foil_midplanes"] < values["outer_foil_faces"]
        rows.append({"ground_source_via": ground, "axis_spacing_m": distance,
                     "mutual_inductance_h": values, "independent_quadrature_relative_error": relative})
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "ACCEPT_SOURCE_CENTERLINE_MUTUAL_DIAGNOSTIC_ONLY", "production_eligible": False,
              "input_sha256": sha256(payload).hexdigest(), "power_source_via": power,
              "source_stackup_rows": stack, "z_intervals_m": intervals, "rows": rows,
              "elapsed_s": monotonic() - started,
              "scope": "Existing free-space Neumann filament kernel, mu=mu0, straight source XY axes. Foil-face alternatives describe endpoint convention only, not physical confidence bounds. No solid-fill/plating/radius inference, self/internal L, R, G/C, plane screening, return assignment, current sharing, owner replacement or PowerSI improvement claim. The three same-span GND vias are coefficient examples, not a complete return network."}
    with target.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"output": str(target), "status": result["status"], "elapsed_s": result["elapsed_s"],
                      "z_intervals_m": intervals, "results": [{"ground_via": r["ground_source_via"]["via_id"],
                      "axis_spacing_m": r["axis_spacing_m"], "mutual_inductance_h": r["mutual_inductance_h"],
                      "quadrature_relative": r["independent_quadrature_relative_error"]} for r in rows]}))


if __name__ == "__main__":
    main()
