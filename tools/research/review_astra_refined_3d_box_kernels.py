"""SPD Decap PI Evaluator v0.23.1: saved refined-box kernel review.

Read a frozen producer receipt and bundle. Rebuild its geometry/topology and
low-order deterministic sample oracles without rerunning the dense producer.
"""
from __future__ import annotations

from pathlib import Path
from math import factorial
import argparse
import hashlib
import json

import numpy as np

import qualify_astra_tetra_volume_green as static
import qualify_astra_tetra_charge_green as charge


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PRODUCER_SHA = "67145b502bd924b514b6d3173701ca11521f20a2ef8d845a17e9454c5c6a664d"
PINS = {
    "tools/research/qualify_astra_tetra_volume_green.py": "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb",
    "outputs/research/astra-tetra-static-green-01/blocks.npz": "d1c0b21d327328d1d42f7c12d1a2bf3009b61addb2d83593fce175044105c654",
    "outputs/research/astra-tetra-retarded-green-02/blocks.npz": "c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2",
}
DEGREE = 8


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not bool(condition):
        raise AssertionError(message)


def relative(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), np.finfo(float).tiny))


def geometry(dimensions):
    unit = np.asarray(static.box_tetrahedra(np.ones(3)))
    half = np.asarray(dimensions) / 2
    result, subcube, local = [], [], []
    for ix in range(2):
        for iy in range(2):
            for iz in range(2):
                result.extend((unit + np.array([ix, iy, iz])) * half)
                subcube.extend([4 * ix + 2 * iy + iz] * 6)
                local.extend(range(6))
    return np.asarray(result), np.asarray(subcube), np.asarray(local)


def topology(tetrahedra):
    triangles, ids = [], {}
    divergence = np.zeros((168, 192), dtype=np.int8)
    divergence[:48] = np.repeat(np.eye(48, dtype=np.int8), 4, axis=1)
    for ti, tetra in enumerate(tetrahedra):
        for fi, (face, _, _) in enumerate(static.faces(tetra)[1]):
            key = tuple(sorted(map(tuple, face)))
            if key not in ids:
                ids[key] = len(triangles)
                triangles.append(face)
            divergence[48 + ids[key], 4 * ti + fi] = -1
    require(len(triangles) == 120, "independent face count")
    return np.asarray(triangles), divergence


def vector_data(tetra, order):
    points, weights = static.tetra_quadrature(tetra, order)
    volume, _ = static.faces(tetra)
    return points, weights[:, None, None] * (points[:, None, :] - tetra) / (3 * volume)


def vector_pair(a, b, length, order):
    pa, wa = vector_data(a, order)
    pb, wb = vector_data(b, order)
    radius = np.linalg.norm(pa[:, None] - pb[None], axis=2) / length
    values = np.zeros((7, 4, 4))
    power = radius.copy()
    for p in range(7):
        for axis in range(3):
            values[p] += wa[:, :, axis].T @ (power @ wb[:, :, axis])
        power *= radius
    return values


def entity_measure(entity):
    if len(entity) == 4:
        return static.faces(entity)[0]
    return np.linalg.norm(np.cross(entity[1] - entity[0], entity[2] - entity[0])) / 2


def entity_quadrature(entity, order):
    return charge.quadrature(entity, order)


def exact_inner(source, points):
    origin = source[0]
    method = static.tetra_inner if len(source) == 4 else static.triangle_moments
    return method(source - origin, points - origin)[0] / entity_measure(source)


def scalar_static_pair(a, b, order):
    pa, wa = entity_quadrature(a, order)
    pb, wb = entity_quadrature(b, order)
    return .5 * (wa @ exact_inner(b, pa) + wb @ exact_inner(a, pb))


def scalar_tail_pair(a, b, length, order):
    pa, wa = entity_quadrature(a, order)
    pb, wb = entity_quadrature(b, order)
    radius = np.linalg.norm(pa[:, None] - pb[None], axis=2) / length
    weights = wa[:, None] * wb[None]
    values = np.empty(7)
    power = radius.copy()
    for p in range(7):
        values[p] = np.sum(weights * power)
        power *= radius
    return values


def quadrature_mass_moment(tetra):
    points, weights = static.tetra_quadrature(tetra, 4)
    volume, _ = static.faces(tetra)
    basis = (points[:, None, :] - tetra) / (3 * volume)
    mass = np.einsum("p,pid,pjd->ij", weights, basis, basis)
    moment = np.einsum("p,pid->id", weights, basis)
    return mass, moment


def run(result_path, expected_result, kernels_path, expected_kernels, output):
    require(not output.exists(), "review output exists")
    require(sha(result_path) == expected_result, "producer result hash")
    require(sha(kernels_path) == expected_kernels, "kernel hash")
    require(sha(ROOT / "tools/research/prepare_astra_refined_3d_box_kernels.py") == PRODUCER_SHA,
            "producer helper hash")
    for path, expected in PINS.items():
        require(sha(ROOT / path) == expected, f"support pin {path}")
    receipt = json.loads(result_path.read_bytes())
    require(receipt["status"] == "PASS_REFINED_3D_BOX_KERNEL_CONTROL", "producer status")
    require(receipt["script_sha256"] == PRODUCER_SHA, "receipt producer hash")
    require(receipt["kernels_sha256"] == expected_kernels, "receipt kernel hash")
    require(all(receipt["gates"].values()), "producer gate false")
    with np.load(kernels_path, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}
    expected_shapes = {
        "tetrahedra_m": (48, 4, 3), "triangles_m": (120, 3, 3),
        "distributional_divergence": (168, 192), "geometric_mass": (192, 192),
        "exact_basis_volume_moments": (192, 3), "static_magnetic_h": (192, 192),
        "magnetic_tail_h": (6, 192, 192), "static_scalar_per_m": (168, 168),
        "scalar_tail_per_m": (6, 168, 168), "frequencies_hz": (6,),
        "raw_static_magnetic_h": (192, 192), "raw_static_scalar_per_m": (168, 168),
        "vector_distance_moments": (7, 192, 192), "scalar_distance_moments": (7, 168, 168),
    }
    for name, shape in expected_shapes.items():
        require(name in arrays and arrays[name].shape == shape, f"shape {name}")
        require(np.all(np.isfinite(arrays[name])), f"finite {name}")
    dimensions = np.asarray(receipt["geometry"]["dimensions_m"])
    tetrahedra, subcube, local = geometry(dimensions)
    triangles, divergence = topology(tetrahedra)
    require(np.array_equal(arrays["tetrahedra_m"], tetrahedra), "tetra geometry")
    require(np.array_equal(arrays["triangles_m"], triangles), "face geometry/order")
    require(np.array_equal(arrays["distributional_divergence"], divergence), "B topology")
    require(np.array_equal(divergence.sum(axis=0), np.zeros(192)), "B neutrality")

    mass_errors, moment_errors = [], []
    for ti, tetra in enumerate(tetrahedra):
        mass, moment = quadrature_mass_moment(tetra)
        sl = slice(4 * ti, 4 * ti + 4)
        mass_errors.append(relative(arrays["geometric_mass"][sl, sl], mass))
        moment_errors.append(relative(arrays["exact_basis_volume_moments"][sl], moment))
    off = arrays["geometric_mass"].copy()
    for ti in range(48):
        off[4 * ti:4 * ti + 4, 4 * ti:4 * ti + 4] = 0
    require(np.count_nonzero(off) == 0, "mass off-block entries")

    with np.load(ROOT / "outputs/research/astra-tetra-static-green-01/blocks.npz", allow_pickle=False) as data:
        old_static = data["box_local_rt0_blocks"]
    with np.load(ROOT / "outputs/research/astra-tetra-retarded-green-02/blocks.npz", allow_pickle=False) as data:
        old_moments = data["distance_moments"]
    within_static, within_vector = [], []
    for a, b in ((0, 0), (0, 1), (5, 2), (12, 17), (42, 47)):
        require(subcube[a] == subcube[b], "within sample selection")
        sa, sb = slice(4 * a, 4 * a + 4), slice(4 * b, 4 * b + 4)
        expected = .25 * (old_static[local[a], local[b]] + old_static[local[b], local[a]].T)
        within_static.append(relative(arrays["static_magnetic_h"][sa, sb], expected))
        expected_m = np.asarray([.5 ** (p + 3) * .5 *
                                 (old_moments[p, local[a], local[b]] +
                                  old_moments[p, local[b], local[a]].T)
                                 for p in range(7)])
        within_vector.append(relative(arrays["vector_distance_moments"][:, sa, sb], expected_m))

    length = float(np.linalg.norm(dimensions))
    cross_pairs = ((0, 6), (0, 12), (5, 47), (23, 40))
    cross_static, cross_vector = [], []
    for a, b in cross_pairs:
        sa, sb = slice(4 * a, 4 * a + 4), slice(4 * b, 4 * b + 4)
        low = (static.tetra_pair(tetrahedra[a], tetrahedra[b], 8) +
               static.tetra_pair(tetrahedra[b], tetrahedra[a], 8).T) / 2
        cross_static.append(relative(arrays["static_magnetic_h"][sa, sb], low))
        cross_vector.append(relative(arrays["vector_distance_moments"][:, sa, sb],
                                     vector_pair(tetrahedra[a], tetrahedra[b], length, 4)))

    entities = list(tetrahedra) + list(triangles)
    entity_pairs = ((0, 48), (0, 119), (47, 167), (48, 49), (93, 132))
    cross_scalar, cross_scalar_tail = [], []
    for a, b in entity_pairs:
        cross_scalar.append(abs(arrays["static_scalar_per_m"][a, b] -
                                scalar_static_pair(entities[a], entities[b], 8)) /
                            abs(arrays["static_scalar_per_m"][a, b]))
        cross_scalar_tail.append(relative(arrays["scalar_distance_moments"][:, a, b],
                                          scalar_tail_pair(entities[a], entities[b], length, 6)))

    ltail = np.empty_like(arrays["magnetic_tail_h"])
    ptail = np.empty_like(arrays["scalar_tail_per_m"])
    wave = 2 * np.pi * arrays["frequencies_hz"] * np.sqrt(static.source.MU0 * static.source.EPS0)
    for fi, k in enumerate(wave):
        ltail[fi] = 0
        ptail[fi] = 0
        for n in range(2, 9):
            ltail[fi] += ((-1j * k * length) ** n / factorial(n)
                          * arrays["vector_distance_moments"][n - 2] / length * 1e-7)
            ptail[fi] += ((-1j * k * length) ** n / factorial(n)
                          * arrays["scalar_distance_moments"][n - 2] / length)

    uniform = arrays["uniform_current_face_flux"]
    surface = divergence @ uniform
    volumes = np.asarray([static.faces(tetra)[0] for tetra in tetrahedra])
    density = np.r_[volumes, np.zeros(120)]
    box_reference, _ = static.box_self_reference(dimensions)
    polarization_reference = charge.uniform_polarization_reference(dimensions)
    magnetic_energy = np.diag(uniform.T @ arrays["static_magnetic_h"] @ uniform)
    polarization_energy = np.diag(surface.T @ arrays["static_scalar_per_m"] @ surface)
    metrics = {
        "maximum_mass_quadrature_relative": max(mass_errors),
        "maximum_moment_quadrature_relative": max(moment_errors),
        "maximum_within_static_reuse_relative": max(within_static),
        "maximum_within_vector_reuse_relative": max(within_vector),
        "maximum_cross_static_q8_relative": max(cross_static),
        "maximum_cross_vector_q4_relative": max(cross_vector),
        "maximum_cross_scalar_q8_relative": float(max(cross_scalar)),
        "maximum_cross_scalar_tail_q6_relative": max(cross_scalar_tail),
        "full_magnetic_tail_reconstruction_relative": relative(arrays["magnetic_tail_h"], ltail),
        "full_scalar_tail_reconstruction_relative": relative(arrays["scalar_tail_per_m"], ptail),
        "paired_static_magnetic_identity_relative": relative(arrays["static_magnetic_h"],
                                                               (arrays["raw_static_magnetic_h"] + arrays["raw_static_magnetic_h"].T) / 2),
        "paired_static_scalar_identity_relative": relative(arrays["static_scalar_per_m"],
                                                             (arrays["raw_static_scalar_per_m"] + arrays["raw_static_scalar_per_m"].T) / 2),
        "static_magnetic_minimum_eigenvalue_h": float(np.linalg.eigvalsh(arrays["static_magnetic_h"]).min()),
        "static_scalar_minimum_eigenvalue_per_m": float(np.linalg.eigvalsh(arrays["static_scalar_per_m"]).min()),
        "uniform_box_magnetic_relative": relative(magnetic_energy, np.full(3, 1e-7 * box_reference)),
        "uniform_volume_scalar_relative": float(abs(density @ arrays["static_scalar_per_m"] @ density / box_reference - 1)),
        "polarization_relative": relative(polarization_energy, polarization_reference),
    }
    gates = {
        "geometry_topology": True,
        "mass_moment_quadrature": metrics["maximum_mass_quadrature_relative"] < 2e-13 and metrics["maximum_moment_quadrature_relative"] < 2e-13,
        "frozen_subcube_reuse": metrics["maximum_within_static_reuse_relative"] < 1e-15 and metrics["maximum_within_vector_reuse_relative"] < 1e-15,
        "sampled_cross_quadrature": metrics["maximum_cross_static_q8_relative"] < 2e-3 and metrics["maximum_cross_vector_q4_relative"] < 3e-3 and metrics["maximum_cross_scalar_q8_relative"] < 3e-3 and metrics["maximum_cross_scalar_tail_q6_relative"] < 3e-3,
        "tail_reconstruction": metrics["full_magnetic_tail_reconstruction_relative"] < 1e-15 and metrics["full_scalar_tail_reconstruction_relative"] < 1e-15,
        "paired_orientation": metrics["paired_static_magnetic_identity_relative"] == 0 and metrics["paired_static_scalar_identity_relative"] == 0,
        "positive_static": metrics["static_magnetic_minimum_eigenvalue_h"] > 0 and metrics["static_scalar_minimum_eigenvalue_per_m"] > 0,
        "independent_box_controls": metrics["uniform_box_magnetic_relative"] < 1e-5 and metrics["uniform_volume_scalar_relative"] < 1e-5 and metrics["polarization_relative"] < 1e-4,
    }
    output.mkdir(parents=False)
    review = {
        "program": PROGRAM, "version": VERSION,
        "status": "ACCEPT_REFINED_3D_BOX_KERNEL_SAVED_CONTROL" if all(gates.values()) else "REJECT_REFINED_3D_BOX_KERNEL_SAVED_CONTROL",
        "gates": gates, "metrics": metrics,
        "producer_result_sha256": expected_result, "kernels_sha256": expected_kernels,
        "producer_script_sha256": PRODUCER_SHA, "reviewer_sha256": sha(Path(__file__)),
        "scope": "Saved-array review only. Independently rebuilt mesh incidence and quadrature mass/moments, checked frozen half-scale reuse, sampled new cross-pair rules at lower order, and reconstructed all stored regular tails. It did not rerun the dense producer, prove mesh convergence, qualify source solids/material interfaces, or establish terminal, board, or PowerSI accuracy.",
    }
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"status": review["status"], "gates": gates, "metrics": metrics}), flush=True)
    return 0 if all(gates.values()) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-result", type=Path, required=True)
    parser.add_argument("--expected-result-sha256", required=True)
    parser.add_argument("--kernels", type=Path, required=True)
    parser.add_argument("--expected-kernels-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.producer_result.resolve(), args.expected_result_sha256,
                         args.kernels.resolve(), args.expected_kernels_sha256,
                         args.output.resolve()))
