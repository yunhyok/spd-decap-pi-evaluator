"""SPD Decap PI Evaluator v0.23.1 saved tetra static-Green review.

This reads the frozen six-tetra arrays. It independently checks source pins,
RT0 face fluxes, the box tensor oracle, reciprocity/passivity, and one local
translated/permuted pair. It never calls the producer or builds a board model.
"""
from __future__ import annotations

import argparse
from hashlib import file_digest
from itertools import permutations
import json
from pathlib import Path
from time import monotonic

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.integrate import quad
from scipy.special import erf


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
PINS = {
    "driver": (RESEARCH / "astra-tetra-static-green-01/driver-at-run.py", "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb"),
    "result": (RESEARCH / "astra-tetra-static-green-01/result.json", "d4e7d1dbe65d5f016833f7c14b6c72af6bce5b0f91cf943cdf7e3161f106ad02"),
    "blocks": (RESEARCH / "astra-tetra-static-green-01/blocks.npz", "d1c0b21d327328d1d42f7c12d1a2bf3009b61addb2d83593fce175044105c654"),
    "source": (RESEARCH / "astra-native-frequency-stamps-01/source-frequency-inputs.json", "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc"),
    "low": (RESEARCH / "astra-native-low-band-stamps-01/receipt.json", "1968b9091d7eab184c2c0ad195fdc847661377484624a4ac4985d06e42216299"),
    "high": (RESEARCH / "astra-native-frequency-stamps-01/receipt.json", "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856"),
    "low_review": (RESEARCH / "astra-native-low-band-stamps-01/independent-review.json", "1fed75a2227ffb90f937914ebf2b112614736b632b9fec053ac9876125168228"),
    "high_review": (RESEARCH / "astra-native-frequency-stamps-01/independent-review.json", "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def relative(actual: np.ndarray, expected: np.ndarray) -> float:
    return float(np.linalg.norm(actual - expected) / max(np.linalg.norm(expected), np.finfo(float).tiny))


def tetra_faces(tetra: np.ndarray) -> tuple[float, np.ndarray]:
    signed = np.linalg.det((tetra[1:] - tetra[0]).T) / 6
    require(abs(signed) > 0 and np.isfinite(signed), "degenerate tetrahedron")
    flux = []
    for opposite in range(4):
        ids = [index for index in range(4) if index != opposite]
        face = tetra[ids]
        cross = np.cross(face[1] - face[0], face[2] - face[0]) / 2
        if cross @ (tetra[opposite] - face[0]) > 0:
            cross = -cross
        flux.append(cross)
    return abs(signed), np.asarray(flux)


def triangle_moments(vertices: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edge = vertices[[1, 2, 0]] - vertices
    lengths = np.linalg.norm(edge, axis=1)
    normal = np.cross(edge[0], -edge[2])
    normal /= np.linalg.norm(normal)
    tangent = edge / lengths[:, None]
    outward = np.cross(tangent, normal)
    delta = vertices[None, :, :] - points[:, None, :]
    h = np.einsum("ped,ed->pe", delta, outward)
    lower = np.einsum("ped,ed->pe", delta, tangent)
    upper = lower + lengths
    height = np.abs((vertices[0] - points) @ normal)[:, None]
    radius0 = np.hypot(h, height)
    safe = np.where(radius0 == 0, 1.0, radius0)
    dasinh = np.arcsinh(upper / safe) - np.arcsinh(lower / safe)
    dasinh[radius0 == 0] = 0
    upper_radius, lower_radius = np.hypot(radius0, upper), np.hypot(radius0, lower)
    du = radius0 * radius0 + height * upper_radius
    dl = radius0 * radius0 + height * lower_radius
    au = np.arctan(np.divide(h * upper, du, out=np.zeros_like(h), where=du != 0))
    al = np.arctan(np.divide(h * lower, dl, out=np.zeros_like(h), where=dl != 0))
    inverse = np.sum(h * dasinh - height * (au - al), axis=1)
    dr = lengths * (upper + lower) / (upper_radius + lower_radius)
    line_radius = 0.5 * (lengths * upper_radius + lower * dr + radius0 * radius0 * dasinh)
    radius = (np.sum(h * line_radius, axis=1) + height[:, 0] ** 2 * inverse) / 3
    return inverse, radius


def tetra_inner(tetra: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    scalar = np.zeros(len(points))
    moment = np.zeros((len(points), 3))
    for opposite in range(4):
        ids = [index for index in range(4) if index != opposite]
        face = tetra[ids]
        cross = np.cross(face[1] - face[0], face[2] - face[0])
        if cross @ (tetra[opposite] - face[0]) > 0:
            face = face[[0, 2, 1]]
            cross = -cross
        area = np.linalg.norm(cross) / 2
        normal = cross / (2 * area)
        inverse, radius = triangle_moments(face, points)
        scalar += 0.5 * ((face[0] - points) @ normal) * inverse
        moment += radius[:, None] * normal
    return scalar, moment


def tetra_quadrature(tetra: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = leggauss(order)
    x, w = (x + 1) / 2, w / 2
    u, v, t = np.meshgrid(x, x, x, indexing="ij")
    weights = (w[:, None, None] * w[None, :, None] * w[None, None, :] * (1 - u) ** 2 * (1 - v)).ravel()
    bary = np.column_stack(((1 - u).ravel() * (1 - v).ravel() * (1 - t).ravel(), u.ravel(), ((1 - u) * v).ravel(), ((1 - u) * (1 - v) * t).ravel()))
    volume, _ = tetra_faces(tetra)
    weights *= 6 * volume
    require(abs(weights.sum() - volume) < 2e-13 * volume, "tetra quadrature volume")
    return bary @ tetra, weights


def local_pair(observer: np.ndarray, source: np.ndarray, order: int = 32) -> np.ndarray:
    origin = observer[0].copy()
    observer, source = observer - origin, source - origin
    observer_volume, _ = tetra_faces(observer)
    source_volume, _ = tetra_faces(source)
    points, weights = tetra_quadrature(observer, order)
    scalar, moment = tetra_inner(source, points)
    test = (points[:, None, :] - observer[None, :, :]) / (3 * observer_volume)
    trial = ((points[:, None, :] - source[None, :, :]) * scalar[:, None, None] + moment[:, None, :]) / (3 * source_volume)
    return 1e-7 * np.einsum("p,pid,pjd->ij", weights, test, trial)


def box_reference(dimensions: np.ndarray) -> float:
    length = float(dimensions.max())
    volume = float(dimensions.prod())

    def integrand(t: float) -> float:
        u = t * dimensions / length
        factor = np.empty(3)
        small = u < 1e-3
        us = u[small]
        factor[small] = 1 - us * us / 6 + us ** 4 / 30 - us ** 6 / 168
        ul = u[~small]
        factor[~small] = np.sqrt(np.pi) * erf(ul) / ul + np.expm1(-ul * ul) / (ul * ul)
        return float(factor.prod())

    value, error = quad(integrand, 0, np.inf, epsabs=1e-12, epsrel=1e-12, limit=160)
    require(error < 1e-10 * value, "box oracle quadrature")
    return 2 / np.sqrt(np.pi) * volume * volume / length * value


def expected_cube(dimensions: np.ndarray) -> np.ndarray:
    return np.asarray([np.vstack((np.zeros(3), np.cumsum(np.eye(3)[list(order)], axis=0))) * dimensions for order in permutations(range(3))])


def review(output: Path) -> int:
    started = monotonic()
    for label, (path, expected) in PINS.items():
        require(sha256(path) == expected, f"{label} hash changed")
    result = json.loads(PINS["result"][0].read_text(encoding="utf-8"))
    require(result["program"] == PROGRAM and result["version"] == VERSION and result["status"] == "PASS_TETRA_STATIC_GREEN_CONTROL", "producer identity/status")
    require(result["script_sha256"] == PINS["driver"][1] and result["blocks_sha256"] == PINS["blocks"][1] and all(result["gates"].values()), "producer bindings/gates")
    for label in ("source", "low", "high", "low_review", "high_review"):
        relative_path, expected = result["source_pins"][label]
        require(expected == PINS[label][1] and (RESEARCH / relative_path).resolve() == PINS[label][0].resolve(), f"source pin {label}")

    with np.load(PINS["blocks"][0], allow_pickle=False) as archive:
        require(set(archive.files) == {"box_local_rt0_blocks", "tetrahedra_m", "uniform_current_face_flux"}, "saved array names")
        blocks = np.asarray(archive["box_local_rt0_blocks"], dtype=float)
        tetrahedra = np.asarray(archive["tetrahedra_m"], dtype=float)
        flux = np.asarray(archive["uniform_current_face_flux"], dtype=float)
    require(blocks.shape == (6, 6, 4, 4) and tetrahedra.shape == flux.shape == (6, 4, 3), "saved shapes")
    require(np.all(np.isfinite(blocks)) and np.all(np.isfinite(tetrahedra)) and np.all(np.isfinite(flux)), "saved finite arrays")
    dimensions = np.asarray(result["dimensions_m"], dtype=float)
    require(relative(tetrahedra, expected_cube(dimensions)) == 0.0, "Kuhn tetrahedra")

    maximum_flux_relative = 0.0
    maximum_partition_relative = 0.0
    for tetra, saved_flux in zip(tetrahedra, flux, strict=True):
        volume, independent_flux = tetra_faces(tetra)
        maximum_flux_relative = max(maximum_flux_relative, relative(saved_flux, independent_flux))
        require(relative(saved_flux.sum(axis=0), np.zeros(3)) < 2e-13, "closed tetra flux")
        values = (tetra.mean(axis=0) - tetra) / (3 * volume)
        maximum_partition_relative = max(maximum_partition_relative, relative(values.T @ saved_flux, np.eye(3)))

    reciprocal = blocks.transpose(1, 0, 3, 2)
    reciprocity = relative(blocks, reciprocal)
    matrix = blocks.transpose(0, 2, 1, 3).reshape(24, 24)
    symmetric = (matrix + matrix.T) / 2
    minimum_eigenvalue = float(np.linalg.eigvalsh(symmetric).min())
    energy = np.einsum("aid,abij,bjd->d", flux, blocks, flux)
    oracle = 1e-7 * box_reference(dimensions)
    tensor_relative = float(np.max(np.abs(energy - oracle) / oracle))

    pair = local_pair(tetrahedra[0], tetrahedra[1])
    shift = np.array([3.125e-4, -1.75e-4, 2.25e-4])
    translated = local_pair(tetrahedra[0] + shift, tetrahedra[1] + shift)
    translation_relative = relative(translated, pair)
    permutation = np.array([0, 2, 1, 3])
    permuted = local_pair(tetrahedra[0][permutation], tetrahedra[1][permutation])
    winding_relative = relative(permuted, pair[np.ix_(permutation, permutation)])

    require(maximum_flux_relative < 2e-13 and maximum_partition_relative < 2e-13, "independent RT0 flux/partition")
    require(reciprocity < 1e-10 and minimum_eigenvalue > 0, "saved reciprocity/positive kernel")
    require(tensor_relative < 2e-11, "independent box tensor oracle")
    require(translation_relative < 2e-12 and winding_relative < 2e-11, "translated/winding local pair")
    require(result["refinement"][-1]["box_uniform_current_relative_error"] < 0.1 * result["refinement"][0]["box_uniform_current_relative_error"], "reported refinement")

    review_result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_TETRA_STATIC_GREEN_CONTROL_INDEPENDENT_REVIEW",
        "reviewer_sha256": sha256(Path(__file__)),
        "inputs": {label: {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": expected} for label, (path, expected) in PINS.items()},
        "metrics": {
            "maximum_saved_face_flux_relative": maximum_flux_relative,
            "maximum_affine_partition_relative": maximum_partition_relative,
            "raw_reciprocity_relative": reciprocity,
            "minimum_symmetric_eigenvalue_h": minimum_eigenvalue,
            "uniform_box_tensor_relative_error": tensor_relative,
            "uniform_box_component_energies_h": energy.tolist(),
            "uniform_box_oracle_h": oracle,
            "translated_pair_relative": translation_relative,
            "permuted_winding_pair_relative": winding_relative,
        },
        "gates": {
            "producer_and_source_hashes_bound": True,
            "saved_array_schema_and_finiteness": True,
            "independent_outward_face_flux_and_affine_partition": True,
            "saved_reciprocity_and_positive_symmetric_part": True,
            "independent_positive_gaussian_box_tensor_oracle": True,
            "fresh_local_translation_and_vertex_winding_check": True,
            "reported_order_refinement": True,
        },
        "findings": [],
        "scope": "Accepts the frozen static scalar-Green affine-RT0 control on six Kuhn tetrahedra covering a synthetic 100um by100um box with source-pinned TOP thickness25um. The lateral box is not extracted board geometry. This does not qualify the retarded remainder, charge coupling, material current conformity, general three-dimensional VIE/PEEC, via/pad interfaces, a board port, or PowerSI accuracy.",
        "elapsed_s": monotonic() - started,
    }
    output.mkdir(parents=False, exist_ok=False)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review_result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"status": review_result["status"], "metrics": review_result["metrics"], "elapsed_s": review_result["elapsed_s"]}, allow_nan=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(review(parser.parse_args().output.resolve()))
