"""Compute triangle currents from the saved epsilon=1 global solution, without a solve."""

import json
from pathlib import Path
import sys

import numpy as np

from reconstruct_astra_native_loaded_field import _atomic_exclusive_json, _sha256_file
from solve_astra_l14_sheet_sensitivity import triangle_energy
from render_astra_l14_sheet_current_map import render
from scipy.sparse import csc_matrix

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs/research/astra-l14-sheet-r-shadow-01"
MESH = ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05"


def sensitivity_only():
    # Reciprocal one-port: dZ/depsilon = V.T G V / epsilon**2.
    # A complex shunt control detects sign and accidental conjugation errors.
    step, g, y = 1e-4, 2., 1. + 2j
    control = g / (g + y)**2
    difference = (1 / (g/(1+step)+y) - 1 / (g/(1-step)+y)) / (2*step)
    assert abs(control-difference) / abs(control) < 1e-8
    paths = {
        RUN / "solved-sheet-field.npz": "dc802283bbadb7bc94cab6af3da227c60d8d4e51eab8779374f81addaa6cf12e",
        RUN / "epsilon-1-field.npz": "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a",
        MESH / "sheet-electrode-drive.npz": "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d",
        ROOT / "outputs/research/astra-l14-sheet-sensitivity-01/result.json": "559d9ff53d9c6c4e7a2ebbb1717fe8a15725aa741aa7af83f17ef36c19e1f2fc",
    }
    for path, expected in paths.items():
        assert _sha256_file(path) == expected, path.name
    with np.load(RUN / "solved-sheet-field.npz", allow_pickle=False) as a, np.load(RUN / "epsilon-1-field.npz", allow_pickle=False) as f, np.load(MESH / "sheet-electrode-drive.npz", allow_pickle=False) as d:
        derivative = complex(a["triangle_bilinear_ohm"].sum())
        loss = float(a["triangle_joule_ohm"].sum())
        voltage = f["active_voltage"][f["sheet_active_indices"]]
        conductance = csc_matrix((d["conductance_data"], d["conductance_indices"], d["conductance_indptr"]), shape=tuple(d["conductance_shape"]))
        matrix_value = complex(voltage @ (conductance @ voltage))
    error = abs(derivative-matrix_value)
    assert np.isfinite(derivative) and error / loss < 1e-7 and abs(derivative) <= loss * (1+1e-12)
    ideal_path = ROOT / "outputs/research/astra-l14-sheet-sensitivity-01/result.json"
    ideal = complex(*json.loads(ideal_path.read_text(encoding="utf-8"))["d_zdd_d_epsilon_at_zero_ohm"])
    pair = lambda value: [value.real, value.imag]
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "VERIFIED_SAVED_EPSILON1_DC_SHEET_LOCAL_SENSITIVITY",
        "script_sha256": _sha256_file(Path(__file__)),
        "inputs": {str(path): expected for path, expected in paths.items()},
        "epsilon_1_d_zdd_d_epsilon_ohm": pair(derivative),
        "independent_matrix_bilinear_ohm": pair(matrix_value),
        "triangle_matrix_difference_ohm": error,
        "epsilon_1_hermitian_joule_ohm": loss,
        "epsilon_0_d_zdd_d_epsilon_ohm": pair(ideal),
        "sensitivity_magnitude_ratio_epsilon1_over_epsilon0": abs(derivative)/abs(ideal),
        "complex_shunt_control_relative_error": abs(control-difference)/abs(control),
        "scope": "Local derivative at epsilon=1 when only all L14 DC sheet resistance scales. Reciprocal complex-symmetric one-port with unit current. No new solve, geometry, finite-change bound, magnetic model or accuracy promotion."
    }
    _atomic_exclusive_json(RUN / "saved-field-local-sensitivity.json", result)
    print(json.dumps(result, separators=(",", ":")))


def main():
    inputs = {
        "global_field": (RUN / "epsilon-1-field.npz", "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a"),
        "global_result": (RUN / "result.json", "b0401b240cd855dc4beffbc2f8920c2f285022885c2940660601cc41aa2adca5"),
        "mesh": (MESH / "mesh-stiffness.npz", "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
        "drive": (MESH / "sheet-electrode-drive.npz", "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d"),
    }
    for path, expected in inputs.values():
        assert _sha256_file(path) == expected, path.name
    with np.load(inputs["global_field"][0], allow_pickle=False) as field, np.load(inputs["drive"][0], allow_pickle=False) as drive, np.load(inputs["mesh"][0], allow_pickle=False) as mesh:
        nodes, triangles = mesh["node_xy_um"], mesh["triangles"]
        voltage = field["active_voltage"][field["sheet_active_indices"][drive["full_to_contracted"]]]
    bilinear, joule, gradient = triangle_energy(nodes, triangles, voltage, 59590000. * 20e-6)
    density = -59590000. * 20e-6 * gradient * 1e6
    magnitude = np.sqrt(np.sum(abs(density)**2, axis=1))
    total_loss = float(joule.sum())
    point = json.loads(inputs["global_result"][0].read_text(encoding="utf-8"))["points"][0]
    expected_loss = point["power_contributions_ohm"]["sheet_dc"][0]
    error = abs(total_loss - expected_loss)
    assert error / expected_loss < 1e-7 and np.all(np.isfinite(magnitude))
    output = RUN / "solved-sheet-field.npz"
    with output.open("xb") as handle:
        np.savez_compressed(handle, sheet_voltage=voltage, triangle_bilinear_ohm=bilinear, triangle_joule_ohm=joule, sheet_current_density_a_per_m=density)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "VERIFIED_SAVED_EPSILON1_TRIANGLE_CURRENT_AND_ENERGY", "script_sha256": _sha256_file(Path(__file__)), "inputs": {name: {"path": str(path), "sha256": expected} for name, (path, expected) in inputs.items()}, "maximum_sheet_current_density_a_per_m": float(magnitude.max()), "integrated_sheet_joule_ohm": total_loss, "global_matrix_sheet_joule_ohm": expected_loss, "gradient_vs_matrix_energy_error_ohm": error, "gradient_vs_matrix_energy_relative_error": error/expected_loss, "field_npz_sha256": _sha256_file(output), "scope": "Conditional fixed-mesh source DC sheet after global redistribution; no new solve or full AC accuracy claim."}
    _atomic_exclusive_json(RUN / "solved-sheet-field.json", result)
    render(nodes, triangles, density, total_loss, RUN / "l14-solved-sheet-current-density.png", actual=True)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    if sys.argv[1:] == ["--sensitivity-only"]:
        sensitivity_only()
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit("Usage: analyze_astra_l14_solved_sheet_field.py [--sensitivity-only]")
