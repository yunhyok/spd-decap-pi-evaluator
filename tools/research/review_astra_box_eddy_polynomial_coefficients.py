"""Coefficient-level review of the frozen Poisson polynomial-basis diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from numpy.polynomial import Legendre, Polynomial


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "outputs/research/astra-box-eddy-polynomial-basis-01/driver-at-run.py"
RESULT = ROOT / "outputs/research/astra-box-eddy-polynomial-basis-01/result.json"
PINS = {
    "driver": "48e8c5552ec9499548785e34a81dacef64af2fcf1f5a751ea6aa165fe52ef0a3",
    "result": "85252d666b3f2fd0fee9ae54341da11ea790882cde10abe75f547f3cbd40eda5",
    "baseline_result": "1081ca3f6147e8435fd439f53ce00b02c07fc05ce4d9ddd7d3543f8f5792646c",
    "baseline_driver": "7a674bee15ccee2502848a55198a5ee9fdc1338ba6d436e95b14814991e3d954",
    "first_review": "359605848c0d36ebb36799919eb01d5d3fba2e0be92e886f4113dc8c37d0886b",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def integral(poly: Polynomial) -> float:
    """Exact monomial antiderivative evaluated on [-1,1]."""
    return float(sum(value * (1 - (-1) ** (power + 1)) / (power + 1)
        for power, value in enumerate(poly.coef)))


def exact_1d(count: int):
    bubbles = []
    factor = Polynomial([1.0, 0.0, -1.0])
    for index in range(count):
        legendre = Legendre.basis(2 * index).convert(kind=Polynomial)
        bubbles.append(factor * legendre)
    mass = np.array([[integral(a * b) for b in bubbles] for a in bubbles])
    stiffness = np.array([[integral(a.deriv() * b.deriv()) for b in bubbles] for a in bubbles])
    forcing = np.array([integral(item) for item in bubbles])
    boundary = max(abs(item(endpoint)) for item in bubbles for endpoint in (-1, 1))
    # For curl(psi)=(d_v psi,-d_u psi), form both derivatives through
    # separate coefficient paths and check div(curl(psi)) coefficientwise.
    divergence = 0.0
    for a in bubbles:
        for b in bubbles:
            ju = np.outer(a.coef, b.deriv().coef)
            jv = -np.outer(a.deriv().coef, b.coef)
            du_ju = np.arange(1, ju.shape[0])[:, None] * ju[1:]
            dv_jv = np.arange(1, jv.shape[1])[None, :] * jv[:, 1:]
            divergence = max(divergence, float(np.max(abs(du_ju + dv_jv))))
    return mass, stiffness, forcing, boundary, divergence


def exact_energy(a: float, c: float, count: int):
    mass, stiffness, forcing, boundary, divergence = exact_1d(count)
    matrix = np.kron(stiffness, mass) * c / a + np.kron(mass, stiffness) * a / c
    rhs = np.kron(forcing, forcing) * a * c / 4
    coefficients = np.linalg.solve(matrix, rhs)
    residual = float(np.linalg.norm(matrix @ coefficients - rhs) / np.linalg.norm(rhs))
    energy = float(rhs @ coefficients)
    # This is also the exact coefficient integral of |curl psi|^2.
    gradient_energy = float(coefficients @ matrix @ coefficients)
    return dict(energy=energy, residual=residual, boundary=boundary,
        divergence=divergence, gradient_energy_relative=abs(gradient_energy / energy - 1),
        minimum_eigenvalue=float(np.linalg.eigvalsh(matrix).min()))


def run(output: Path):
    assert not output.exists()
    paths = {
        "driver": DRIVER,
        "result": RESULT,
        "baseline_result": ROOT / "outputs/research/astra-box-eddy-poisson-limit-01/result.json",
        "baseline_driver": ROOT / "outputs/research/astra-box-eddy-poisson-limit-01/driver-at-run.py",
        "first_review": ROOT / "outputs/research/astra-box-eddy-polynomial-basis-review-01/independent-review.json",
    }
    for name, path in paths.items():
        assert sha(path) == PINS[name], path
    saved = json.loads(RESULT.read_bytes())
    assert saved["status"] == "COMPLETE_BOX_EDDY_POLYNOMIAL_BASIS_DIAGNOSTIC"
    assert saved["script_sha256"] == PINS["driver"]
    assert saved["dimensions_m"] == [0.0001, 0.0001, 2.4999999999999998e-05]
    checks = []
    maximum_energy_relative = maximum_residual = maximum_gradient_relative = 0.0
    maximum_boundary = maximum_divergence = 0.0
    for case in saved["cases"]:
        assert case["magnetic_axis"] in "xyz" and len(case["cross_section_axes"]) == 2
        a, c = case["dimensions_m"]
        assert abs(a * c * case["extrusion_m"] / np.prod(saved["dimensions_m"]) - 1) < 1e-15
        energies = []
        for count in (1, 2, 4):
            rebuilt = exact_energy(a, c, count)
            mode = next(item for item in case["modes"] if item["stream_modes"] == count * count)
            error = abs(rebuilt["energy"] / mode["poisson_energy_m4"] - 1)
            maximum_energy_relative = max(maximum_energy_relative, error)
            maximum_residual = max(maximum_residual, rebuilt["residual"])
            maximum_gradient_relative = max(maximum_gradient_relative, rebuilt["gradient_energy_relative"])
            maximum_boundary = max(maximum_boundary, rebuilt["boundary"])
            maximum_divergence = max(maximum_divergence, rebuilt["divergence"])
            energies.append(dict(stream_modes=count * count, coefficient_energy_m4=rebuilt["energy"],
                saved_relative=error, minimum_eigenvalue=rebuilt["minimum_eigenvalue"]))
        saved_energy = np.array([item["poisson_energy_m4"] for item in case["modes"]])
        assert np.all(np.diff(saved_energy) > 0)
        assert np.all(saved_energy <= case["reference"]["integral_upper_m4"] * (1 + 1e-12))
        checks.append(dict(magnetic_axis=case["magnetic_axis"], cross_section_axes=case["cross_section_axes"],
            extrusion_m=case["extrusion_m"], coefficient_checks=energies,
            sixteen_mode_deficit=case["modes"][3]["loss_deficit_relative"],
            eighty_one_mode_deficit=case["modes"][-1]["loss_deficit_relative"],
            maximum_saved_quadrature_refinement=max(item["quadrature_refinement_relative"] for item in case["modes"])))
    assert maximum_energy_relative < 1e-13
    assert maximum_residual < 1e-12 and maximum_gradient_relative < 1e-12
    assert maximum_boundary < 1e-13 and maximum_divergence < 1e-13
    review = dict(program="SPD Decap PI Evaluator", version="0.23.1",
        status="ACCEPT_BOX_EDDY_POLYNOMIAL_COEFFICIENT_REVIEW", pins=PINS,
        reviewer_sha256=sha(Path(__file__)), checks=dict(cases=checks,
            maximum_coefficient_energy_relative=maximum_energy_relative,
            maximum_mass_solve_residual=maximum_residual,
            maximum_gradient_energy_relative=maximum_gradient_relative,
            maximum_boundary_stream_value=maximum_boundary,
            maximum_coefficient_divergence=maximum_divergence),
        scope="Independent monomial-coefficient integration of the 1, 4 and 16-mode even-Legendre stream spaces. This validates the frozen omega-to-zero rectangular Poisson approximation only; it does not assemble mixed-axis 3D full-wave currents, skin/retardation, material interfaces, ports, a board model or PowerSI accuracy.")
    output.mkdir(parents=True)
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": review["status"], "review_sha256": sha(output / "independent-review.json")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
