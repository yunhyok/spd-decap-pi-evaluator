"""SPD Decap PI Evaluator v0.23.1: saved static tetra-charge review.

Rebuild the frozen six-tetra charge topology and sample its singular scalar
integrals at a lower order. The q32 producer is not rerun.
"""
from pathlib import Path
import argparse
import hashlib
import json

import numpy as np

import qualify_astra_tetra_volume_green as static
import qualify_astra_tetra_charge_green as charge


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "producer": (ROOT / "tools/research/qualify_astra_tetra_charge_green.py", "aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9"),
    "result": (ROOT / "outputs/research/astra-tetra-charge-green-01/result.json", "ad2dfe4c8aa6e7c9f6e6d7afd554469655f926f920a50969295665d010983b90"),
    "charge": (ROOT / "outputs/research/astra-tetra-charge-green-01/charge.npz", "ed22fe446522687f89f8021fe6d122110bbb4b0282c97377d4a8984d33b02b60"),
    "static": (ROOT / "tools/research/qualify_astra_tetra_volume_green.py", "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb"),
    "static_blocks": (ROOT / "outputs/research/astra-tetra-static-green-01/blocks.npz", "d1c0b21d327328d1d42f7c12d1a2bf3009b61addb2d83593fce175044105c654"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not bool(condition):
        raise AssertionError(message)


def relative(a, b):
    return float(np.linalg.norm(a - b) /
                 max(np.linalg.norm(a), np.linalg.norm(b), np.finfo(float).tiny))


def topology(tetrahedra):
    faces, ids = [], {}
    divergence = np.zeros((24, 24), dtype=np.int8)
    divergence[:6] = np.repeat(np.eye(6, dtype=np.int8), 4, axis=1)
    for ti, tetra in enumerate(tetrahedra):
        for fi, (face, _, _) in enumerate(static.faces(tetra)[1]):
            key = tuple(sorted(map(tuple, face)))
            if key not in ids:
                ids[key] = len(faces)
                faces.append(face)
            divergence[6 + ids[key], 4 * ti + fi] = -1
    require(len(faces) == 18, "face count")
    return np.asarray(faces), divergence


def measure(entity):
    if len(entity) == 4:
        return static.faces(entity)[0]
    return np.linalg.norm(np.cross(entity[1] - entity[0], entity[2] - entity[0])) / 2


def sample_pair(observer, source, order=8):
    points, weights = charge.quadrature(observer, order)
    origin = source[0]
    inner = static.tetra_inner if len(source) == 4 else static.triangle_moments
    return weights @ (inner(source - origin, points - origin)[0] / measure(source))


def run(output):
    require(not output.exists(), "review output exists")
    for name, (path, expected) in PINS.items():
        require(sha(path) == expected, f"pin mismatch: {name}")
    receipt = json.loads(PINS["result"][0].read_bytes())
    require(receipt["status"] == "PASS_TETRA_CHARGE_GREEN_CONTROL", "producer status")
    require(receipt["charge_sha256"] == PINS["charge"][1], "receipt charge binding")
    require(all(receipt["gates"].values()), "producer gate false")
    with np.load(PINS["charge"][0], allow_pickle=False) as saved:
        arrays = {key: saved[key] for key in saved.files}
    with np.load(PINS["static_blocks"][0], allow_pickle=False) as prior:
        tetrahedra = prior["tetrahedra_m"]
    faces, divergence = topology(tetrahedra)
    require(np.array_equal(arrays["tetrahedra_m"], tetrahedra), "tetrahedra")
    require(np.array_equal(arrays["triangles_m"], faces), "face geometry/order")
    require(np.array_equal(arrays["distributional_divergence"], divergence), "divergence topology")
    require(np.array_equal(divergence.sum(axis=0), np.zeros(24)), "neutral columns")
    matrix = arrays["normalized_coulomb_matrix_per_m"]
    require(matrix.shape == (24, 24) and np.all(np.isfinite(matrix)), "static matrix")
    factor = 1 / (4 * np.pi * static.source.EPS0)
    coefficient_error = relative(arrays["potential_coefficients_per_f"], matrix * factor)
    flux = np.asarray([[area * normal for _, normal, area in static.faces(tetra)[1]]
                       for tetra in tetrahedra]).reshape(24, 3)
    surface = divergence @ flux
    volumes = np.asarray([static.faces(tetra)[0] for tetra in tetrahedra])
    density = np.r_[volumes, np.zeros(18)]
    require(relative(arrays["uniform_current_flux"], flux) < 1e-15, "uniform flux")
    require(relative(arrays["uniform_current_charge"], surface) < 1e-15, "uniform charge")
    require(relative(arrays["uniform_volume_density_charge"], density) < 1e-15, "volume density")
    entities = list(tetrahedra) + list(faces)
    selected = ((0, 0), (0, 1), (0, 6), (5, 23), (6, 6), (6, 7), (11, 17), (23, 23))
    sample_errors = []
    for a, b in selected:
        low = .5 * (sample_pair(entities[a], entities[b]) +
                    sample_pair(entities[b], entities[a]))
        high = .5 * (matrix[a, b] + matrix[b, a])
        sample_errors.append(abs(low - high) / abs(high))
    symmetric = (matrix + matrix.T) / 2
    energy = np.diag(surface.T @ matrix @ surface)
    reference = arrays["polarization_reference_m3"]
    box_reference, _ = static.box_self_reference(np.ptp(tetrahedra.reshape(-1, 3), axis=0))
    centroids = np.asarray([entity.mean(axis=0) for entity in entities])
    moments = (tetrahedra.mean(axis=1)[:, None, :] - tetrahedra).reshape(24, 3) / 3
    metrics = {
        "coefficient_relative": coefficient_error,
        "raw_reciprocity_relative": relative(matrix, matrix.T),
        "minimum_symmetric_eigenvalue_per_m": float(np.linalg.eigvalsh(symmetric).min()),
        "maximum_sampled_q8_to_saved_q32_relative": float(max(sample_errors)),
        "polarization_relative": relative(energy, reference),
        "trace_delta_self_relative": float(abs(energy.sum() /
                                                  (4 * np.pi * np.sum(volumes)) - 1)),
        "uniform_volume_relative": float(abs(density @ matrix @ density / box_reference - 1)),
        "first_moment_relative": relative(centroids.T @ divergence, -moments.T),
    }
    gates = {
        "topology": True,
        "coefficient": coefficient_error < 1e-15,
        "reciprocity_integration": metrics["raw_reciprocity_relative"] < 1e-5,
        "positive_symmetric_kernel": metrics["minimum_symmetric_eigenvalue_per_m"] > 0,
        "sampled_lower_order": metrics["maximum_sampled_q8_to_saved_q32_relative"] < 1e-3,
        "independent_saved_energy": metrics["polarization_relative"] < 1e-5 and metrics["trace_delta_self_relative"] < 1e-5 and metrics["uniform_volume_relative"] < 1e-5,
        "first_moment": metrics["first_moment_relative"] < 1e-14,
    }
    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_TETRA_STATIC_CHARGE_SAVED_CONTROL" if all(gates.values()) else "REJECT_TETRA_STATIC_CHARGE_SAVED_CONTROL",
        "gates": gates,
        "metrics": metrics,
        "pins": {name: {"path": str(path.relative_to(ROOT)), "sha256": expected}
                 for name, (path, expected) in PINS.items()},
        "reviewer_sha256": sha(Path(__file__)),
        "scope": "Saved q32 six-tetra scalar-charge control. Geometry, normalized distributional divergence, coefficients and energy identities were rebuilt; eight deterministic entries were checked with a new paired q8 rule. The q32 producer was not replayed. This does not qualify a retarded/material/source-solid/terminal/board model or PowerSI accuracy.",
    }
    output.mkdir(parents=False)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"status": review["status"], "gates": gates, "metrics": metrics}), flush=True)
    return 0 if all(gates.values()) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
