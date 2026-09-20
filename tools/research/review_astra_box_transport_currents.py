"""SPD Decap PI Evaluator v0.23.1: saved two-current transport review.

Reconstruct the polynomial currents, mass, cell moments, Schur complement,
saved-current overlaps, and analytic Fourier amplitudes.  This performs no
Green integration and no field solve.
"""
from pathlib import Path
from math import factorial
import argparse
import hashlib
import json

import numpy as np
import qualify_astra_box_transport_currents as producer


ROOT = Path(__file__).resolve().parents[2]
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * np.pi
PINS = {
    "producer": ("tools/research/qualify_astra_box_transport_currents.py", "e88282d18041dabb7faf5629e4932e7f057d2642296830e13789d7c1b1ab1d82"),
    "driver": ("outputs/research/astra-box-transport-current-space-01/driver-at-run.py", "e88282d18041dabb7faf5629e4932e7f057d2642296830e13789d7c1b1ab1d82"),
    "result": ("outputs/research/astra-box-transport-current-space-01/result.json", "51cdc23617df0cfd20adc8356605805cd083c7c2e59b7cdce78da0e5fbb819d0"),
    "space": ("outputs/research/astra-box-transport-current-space-01/space.npz", "ecba005f8b8edfa81913f54396b6d23ebc2348878bebdee84d52fb4fadbe9d35"),
    "kernels": ("outputs/research/astra-refined-3d-box-kernels-01/kernels.npz", "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02"),
    "terminal_fields": ("outputs/research/astra-box-potential-terminal-full-green-01/fields.npz", "11c8904ba937fdf7afc5d141c2418e26dbd3e5142efe251c40f871b65b878a39"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(actual, expected):
    return float(np.linalg.norm(actual - expected) / max(float(np.linalg.norm(expected)), np.finfo(float).tiny))


def gauss01(order):
    x, w = np.polynomial.legendre.leggauss(order)
    return (x + 1) / 2, w / 2


def tetra_quadrature(tetrahedron, order):
    u, wu = gauss01(order)
    v, wv = gauss01(order)
    z, wz = gauss01(order)
    u, v, z = np.meshgrid(u, v, z, indexing="ij")
    wu, wv, wz = np.meshgrid(wu, wv, wz, indexing="ij")
    a, b, c, d = tetrahedron
    points = (
        a
        + u[..., None] * (b - a)
        + ((1 - u) * v)[..., None] * (c - a)
        + ((1 - u) * (1 - v) * z)[..., None] * (d - a)
    ).reshape(-1, 3)
    determinant = abs(np.linalg.det(np.stack((b - a, c - a, d - a), axis=1)))
    weights = (determinant * wu * wv * wz * (1 - u) ** 2 * (1 - v)).ravel()
    return points, weights


def values(points, center, dimensions):
    """Direct curls of Ay=L phi0(x)phi1(z), Az=L phi0(x)phi1(y)."""
    s = 2 * (np.asarray(points) - center) / dimensions
    x, y, z = s.T
    length = float(max(dimensions))
    result = np.zeros((len(s), 3, 2))
    result[:, 0, 0] = -2 * length / dimensions[2] * (1 - x * x) * (1 - 3 * z * z)
    result[:, 2, 0] = -4 * length / dimensions[0] * x * (z - z**3)
    result[:, 0, 1] = +2 * length / dimensions[1] * (1 - x * x) * (1 - 3 * y * y)
    result[:, 1, 1] = +4 * length / dimensions[0] * x * (y - y**3)
    return result


def box_tensor(center, dimensions, order):
    x, w = np.polynomial.legendre.leggauss(order)
    grid = np.stack(np.meshgrid(x, x, x, indexing="ij"), axis=-1).reshape(-1, 3)
    weights = np.einsum("i,j,k->ijk", w, w, w).ravel() * np.prod(dimensions) / 8
    points = center + grid * dimensions / 2
    return points, weights


def poly_transform(coefficients, alpha):
    """Stable monomial/Taylor integral of p(s) exp(-i alpha s), s in [-1,1]."""
    value = 0j
    for order in range(25):
        moment = sum(
            coefficient * 2 / (degree + order + 1)
            for degree, coefficient in enumerate(coefficients)
            if (degree + order) % 2 == 0
        )
        if moment:
            value += moment * (-1j * alpha) ** order / factorial(order)
    return value


def fourier_reference(dimensions, k, directions):
    phi0 = [1.0, 0.0, -1.0]
    phi1 = [0.0, 1.0, 0.0, -1.0]
    one = [1.0]
    potentials = [(1, (phi0, one, phi1)), (2, (phi0, phi1, one))]
    alpha = k * directions * dimensions / 2
    result = np.empty((len(directions), 3, 2), complex)
    factor = max(dimensions) * np.prod(dimensions) / 8
    for mode, (axis, polynomials) in enumerate(potentials):
        transformed = np.full(len(directions), factor, complex)
        for coordinate, coefficients in enumerate(polynomials):
            transformed *= np.array([poly_transform(coefficients, a) for a in alpha[:, coordinate]])
        result[:, :, mode] = 1j * k * np.cross(directions, np.eye(3)[axis]) * transformed[:, None]
    return result


def run(output):
    assert not output.exists()
    for path, expected in PINS.values():
        assert sha(ROOT / path) == expected, path
    record = json.loads((ROOT / PINS["result"][0]).read_bytes())
    assert record["status"] == "PASS_TRANSPORT_TWO_CURRENT_SPACE"
    assert record["script_sha256"] == PINS["producer"][1]
    assert record["space_sha256"] == PINS["space"][1]
    assert record["program"] == "SPD Decap PI Evaluator" and record["version"] == "0.23.1"

    with np.load(ROOT / PINS["kernels"][0], allow_pickle=False) as kernel, np.load(
        ROOT / PINS["terminal_fields"][0], allow_pickle=False
    ) as terminal, np.load(ROOT / PINS["space"][0], allow_pickle=False) as saved:
        tetrahedra = kernel["tetrahedra_m"]
        frequencies = kernel["frequencies_hz"]
        old_map = terminal["cell_current_map"]
        old_mass = terminal["geometric_mass"]
        saved_mass = saved["mass"]
        saved_moments = saved["new_cell_integrated_current_map"]
        powers = saved["polynomial_distance_powers"]

        assert tetrahedra.shape == (48, 4, 3) and old_map.shape == (48, 3, 72)
        assert saved_mass.shape == (74, 74) and saved_moments.shape == (48, 3, 2) and powers.shape == (9, 2, 2)
        lower = tetrahedra.reshape(-1, 3).min(axis=0)
        dimensions = np.ptp(tetrahedra.reshape(-1, 3), axis=0)
        center = lower + dimensions / 2
        volume = float(np.prod(dimensions))
        length = float(max(dimensions))

        points, weights = box_tensor(center, dimensions, 12)
        current = values(points, center, dimensions)
        new_mass = np.einsum("p,pdi,pdj->ij", weights, current, current)
        mean = np.einsum("p,pdi->di", weights, current)
        first = np.einsum("p,pa,pdi->adi", weights, points - center, current)
        constant_moment = mean.T @ mean
        squared_distance_moment = -2 * sum(moment.T @ moment for moment in first) / length**2

        moment_runs = []
        for order in (10, 12):
            moment_runs.append(
                np.array([
                    np.einsum("p,pdi->di", w, values(p, center, dimensions))
                    for p, w in (tetra_quadrature(tetrahedron, order) for tetrahedron in tetrahedra)
                ])
            )
        volumes = np.array([
            abs(np.linalg.det((tetrahedron[1:] - tetrahedron[0]).T)) / 6
            for tetrahedron in tetrahedra
        ])
        cross = np.einsum("tdi,tdj,t->ij", old_map, moment_runs[-1], 1 / volumes)
        rebuilt_mass = np.block([[old_mass, cross], [cross.T, new_mass]])
        roots = np.sqrt(np.diag(rebuilt_mass))
        normalized = rebuilt_mass / roots[:, None] / roots[None, :]
        schur = normalized[72:, 72:] - normalized[72:, :72] @ np.linalg.solve(
            normalized[:72, :72], normalized[:72, 72:]
        )

        max_overlap_difference = 0.0
        for index, entry in enumerate(record["response_overlap"]):
            assert float(frequencies[index]) == float(entry["frequency_hz"])
            driven = terminal[f"case_{index:02d}_current"] @ np.array([0.5, -0.5])
            calculated = abs(cross.T @ driven / np.sqrt(new_mass.diagonal()) / np.sqrt(np.vdot(driven, old_mass @ driven).real))
            max_overlap_difference = max(max_overlap_difference, float(np.max(abs(calculated - entry["saved72_current_normalized_mass_overlap"]))))

        sample_directions = np.array([[1, 2, 3], [-2, 1, 4], [3, -4, 1], [2, 3, -1]], float)
        sample_directions /= np.linalg.norm(sample_directions, axis=1)[:, None]
        fourier_error = 0.0
        for frequency in frequencies:
            k = 2 * np.pi * frequency * np.sqrt(MU0 * EPS0)
            actual = producer.fourier(dimensions, k, sample_directions)
            expected = fourier_reference(dimensions, k, sample_directions)
            fourier_error = max(fourier_error, relative(actual, expected))

    metrics = {
        "new_mass_tensor_relative": relative(saved_mass[72:, 72:], new_mass),
        "cell_moment_q10_q12_relative": relative(moment_runs[0], moment_runs[1]),
        "saved_cell_moment_relative": relative(saved_moments, moment_runs[1]),
        "saved_old_new_mass_cross_relative": relative(saved_mass[:72, 72:], cross),
        "saved_full_mass_relative": relative(saved_mass, rebuilt_mass),
        "minimum_normalized_full_mass_eigenvalue": float(np.linalg.eigvalsh(normalized).min()),
        "new2_schur_eigenvalues": np.linalg.eigvalsh(schur).tolist(),
        "mean_scaled_max": float(np.max(abs(mean)) / np.sqrt(volume) / np.sqrt(new_mass.diagonal()).min()),
        "constant_distance_moment_scaled_max": float(np.max(abs(powers[1] - constant_moment)) / volume**2),
        "squared_distance_moment_scaled_max": float(np.max(abs(powers[3] - squared_distance_moment)) / volume**2),
        "polynomial_reciprocity_relative": float(np.linalg.norm(powers - powers.transpose(0, 2, 1)) / np.linalg.norm(powers)),
        "minimum_normalized_static_eigenvalue": float(np.linalg.eigvalsh(powers[0] / np.sqrt(powers[0].diagonal())[:, None] / np.sqrt(powers[0].diagonal())[None, :]).min()),
        "saved_response_overlap_absolute": max_overlap_difference,
        "analytic_fourier_taylor_relative": fourier_error,
    }
    gates = {
        "mass_and_moments": max(metrics[key] for key in (
            "new_mass_tensor_relative", "cell_moment_q10_q12_relative", "saved_cell_moment_relative",
            "saved_old_new_mass_cross_relative", "saved_full_mass_relative")) < 2e-12,
        "rank": metrics["minimum_normalized_full_mass_eigenvalue"] > 1e-6 and min(metrics["new2_schur_eigenvalues"]) > 1e-6,
        "closed_moments": metrics["mean_scaled_max"] < 1e-12 and metrics["constant_distance_moment_scaled_max"] < 1e-12,
        "distance_squared_identity": metrics["squared_distance_moment_scaled_max"] < 2e-12,
        "polynomial_saved_structure": metrics["polynomial_reciprocity_relative"] < 1e-12 and metrics["minimum_normalized_static_eigenvalue"] > 1e-6,
        "saved_response": metrics["saved_response_overlap_absolute"] < 2e-14,
        "fourier": metrics["analytic_fourier_taylor_relative"] < 2e-12,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    print(json.dumps({"gates": gates, "metrics": metrics}, allow_nan=False))
    assert all(gates.values()), gates
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_TWO_CURRENT_TRANSPORT_MASS_POLYNOMIAL_FOURIER_WITH_SCOPE",
        "reviewer_sha256": sha(Path(__file__)),
        "pins": PINS,
        "gates": gates,
        "metrics": metrics,
        "analytic_scope": {
            "current_definition": "Direct curls of Ay=L*phi0(x)*phi1(z) and Az=L*phi0(x)*phi1(y).",
            "closed_current": "Divergence and boundary-normal closure follow exactly from curl form and phi0/phi1 face zeros; zero mean follows from endpoint-equal derivative integrals.",
            "old64_cross": "A separate component-parity audit proves all old64-new2 mass and scalar-kernel vector-Green crosses exactly zero on the centered reflection-symmetric box.",
            "fourier": "Producer spherical-Bessel amplitudes are compared to an independent monomial/Taylor transform through order24 at all saved frequencies and four non-axis directions.",
        },
        "scope": (
            "Read-only reconstruction from the frozen saved space and terminal field. No RT0 Green column is integrated and no field system is solved. "
            "The polynomial static block is checked for symmetry/positive definiteness and its constant and R^2 moments are independently reconstructed; "
            "the q28-to-q36 static refinement remains producer-reported. Saved72 mass overlaps measure excitation relevance only, not a full residual. "
            "This does not qualify the two transport Green columns, terminal response, continuum convergence, physical return lead, multilayer source, board Z, or PowerSI accuracy."
        ),
    }
    output.mkdir(parents=True)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": review["status"], "gates": gates, "metrics": metrics}, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
