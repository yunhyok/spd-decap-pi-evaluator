"""Independent saved-space review of the polynomial closed-current enrichment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from numpy.polynomial import Legendre, Polynomial

import qualify_astra_tetra_volume_green as static


ROOT = Path(__file__).resolve().parents[2]
SPACE_DIR = ROOT / "outputs/research/astra-box-polynomial-current-space-01"
PINS = {
    "driver": (SPACE_DIR / "driver-at-run.py", "a7d68c398158704e994232976abffa1312279b19ebcc9612281b8b44ee9ad17b"),
    "result": (SPACE_DIR / "result.json", "51c06b1d990409daa0ddc6352b0569f15e95abddb9d99537bb88eb5c63ba3620"),
    "space": (SPACE_DIR / "space.npz", "41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9"),
    "polynomial_result": (ROOT / "outputs/research/astra-box-eddy-polynomial-basis-01/result.json", "85252d666b3f2fd0fee9ae54341da11ea790882cde10abe75f547f3cbd40eda5"),
    "coefficient_review": (ROOT / "outputs/research/astra-box-eddy-polynomial-basis-review-02/independent-review.json", "b008a3ca8542011fa86cadf8748a102ffa178a6afdd6f22b8aed906690f6f1ea"),
    "kernel48": (ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz", "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02"),
    "field48": (ROOT / "outputs/research/astra-constant-current-48-field-01/fields.npz", "04af5c36e1d158ca951ff199ab235a2358f2aaeed6518988ede5296ce12e3a72"),
    "kernel384": (ROOT / "outputs/research/astra-constant-current-box-kernels-01/kernels.npz", "abd6a03542cf9c315690ed9751b5134f41c293eef6201e75cd59b1acbcf024b8"),
    "field384": (ROOT / "outputs/research/astra-constant-current-384-field-01/fields.npz", "9fc218ca9fab49c8d14599b3d226aeb82b30ac1e7db4346cd1fa1acfb37b4c84"),
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(actual, expected) -> float:
    return float(np.linalg.norm(actual - expected) / max(np.linalg.norm(expected), np.finfo(float).tiny))


def integrate(poly: Polynomial) -> float:
    return float(sum(value * (1 - (-1) ** (degree + 1)) / (degree + 1)
        for degree, value in enumerate(poly.coef)))


def basis(count: int):
    factor = Polynomial([1.0, 0.0, -1.0])
    return [factor * Legendre.basis(2 * index).convert(kind=Polynomial) for index in range(count)]


def exact_bubble_forms(dimensions, polynomials):
    mass = np.array([[integrate(a * b) for b in polynomials] for a in polynomials])
    stiffness = np.array([[integrate(a.deriv() * b.deriv()) for b in polynomials] for a in polynomials])
    forcing = np.array([integrate(item) for item in polynomials])
    count = len(polynomials)
    modes = count * count
    scale = max(dimensions)
    gram = np.zeros((3 * modes, 3 * modes))
    rhs = np.zeros((3 * modes, 3))
    for axis in range(3):
        a, b = (axis + 1) % 3, (axis + 2) % 3
        block = slice(axis * modes, (axis + 1) * modes)
        gram[block, block] = scale**2 * dimensions[axis] * (
            np.kron(stiffness, mass) * dimensions[b] / dimensions[a]
            + np.kron(mass, stiffness) * dimensions[a] / dimensions[b])
        rhs[block, axis] = scale * np.prod(dimensions) * np.kron(forcing, forcing) / 4
    boundary = max(abs(item(endpoint)) for item in polynomials for endpoint in (-1, 1))
    divergence = 0.0
    for a in polynomials:
        for b in polynomials:
            ju = np.outer(a.coef, b.deriv().coef)
            jv = -np.outer(a.deriv().coef, b.coef)
            divergence = max(divergence, float(np.max(abs(
                np.arange(1, ju.shape[0])[:, None] * ju[1:]
                + np.arange(1, jv.shape[1])[None, :] * jv[:, 1:]))))
    return gram, rhs, boundary, divergence


def values(coordinate, polynomials):
    return (np.stack([item(coordinate) for item in polynomials], axis=1),
        np.stack([item.deriv()(coordinate) for item in polynomials], axis=1))


def cell_moments(tetrahedron, center, dimensions, polynomials, order=12):
    points, weights = static.tetra_quadrature(tetrahedron, order)
    scaled = 2 * (points - center) / dimensions
    evaluated = [values(scaled[:, axis], polynomials) for axis in range(3)]
    count = len(polynomials)
    modes = count * count
    output = np.zeros((3, 3 * modes))
    stream_scale = max(dimensions)
    for axis in range(3):
        a, b = (axis + 1) % 3, (axis + 2) % 3
        va, da = evaluated[a]
        vb, db = evaluated[b]
        block = slice(axis * modes, (axis + 1) * modes)
        output[a, block] = (2 * stream_scale / dimensions[b]
            * np.einsum("p,pi,pj->ij", weights, va, db).ravel())
        output[b, block] = (-2 * stream_scale / dimensions[a]
            * np.einsum("p,pi,pj->ij", weights, da, vb).ravel())
    return output


def review_case(cells, old_split, kernel_path, field_path, saved, space, polynomial):
    with np.load(kernel_path, allow_pickle=False) as bundle:
        tetrahedra = bundle["tetrahedra_m"]
    with np.load(field_path, allow_pickle=False) as fields:
        old_h = fields["cell_integrated_current_map"]
        q = fields["boundary_divergence_range"]
    assert len(tetrahedra) == cells and old_h.shape[2] - old_split == q.shape[1]
    dimensions = np.ptp(tetrahedra.reshape(-1, 3), axis=0)
    center = (tetrahedra.reshape(-1, 3).min(axis=0) + tetrahedra.reshape(-1, 3).max(axis=0)) / 2
    volumes = np.array([static.faces(item)[0] for item in tetrahedra])
    polynomials = basis(4)
    bubble_gram, bubble_rhs, boundary, divergence = exact_bubble_forms(dimensions, polynomials)
    bubble_moments = np.asarray([cell_moments(item, center, dimensions, polynomials) for item in tetrahedra])
    zero_moment = float(np.linalg.norm(bubble_moments.sum(axis=0)) / np.linalg.norm(bubble_moments))
    assert zero_moment < 1e-12
    base_gram = np.einsum("tdi,tdj,t->ij", old_h, old_h, 1 / volumes)
    cross = np.einsum("tdi,tdj,t->ij", old_h, bubble_moments, 1 / volumes)
    raw = np.block([[base_gram, cross], [cross.T, bubble_gram]])
    modes = bubble_gram.shape[0]
    order = np.r_[np.arange(old_split), np.arange(old_h.shape[2], old_h.shape[2] + modes),
        np.arange(old_split, old_h.shape[2])]
    gram = raw[np.ix_(order, order)]
    combined = np.concatenate((old_h, bubble_moments), axis=2)[:, :, order]
    centerpoints = tetrahedra.mean(axis=1) - center
    incident = np.stack([0.5 * np.cross(np.eye(3)[axis], centerpoints) for axis in range(3)], axis=2)
    base_rhs = np.einsum("tdn,tds->ns", old_h, incident)
    rhs = np.vstack((base_rhs, bubble_rhs))[order]
    split = old_split + modes
    full_scale = 1 / np.sqrt(np.diag(gram))
    full_normalized = full_scale[:, None] * gram * full_scale[None, :]
    full_eigenvalues = np.linalg.eigvalsh(full_normalized)
    scale = 1 / np.sqrt(np.diag(gram[:split, :split]))
    normalized = scale[:, None] * gram[:split, :split] * scale[None, :]
    normalized_eigenvalues = np.linalg.eigvalsh(normalized)
    coefficients = scale[:, None] * np.linalg.solve(normalized, scale[:, None] * rhs[:split])
    residual = float(np.linalg.norm(gram[:split, :split] @ coefficients - rhs[:split]) / np.linalg.norm(rhs[:split]))
    response = rhs[:split].T @ coefficients
    base = normalized[:old_split, :old_split]
    coupling = normalized[:old_split, old_split:]
    bubble = normalized[old_split:, old_split:]
    schur = bubble - coupling.T @ np.linalg.solve(base, coupling)
    schur_eigenvalues = np.linalg.eigvalsh((schur + schur.T) / 2)
    exact = np.array([polynomial["cases"][axis]["reference"]["integral_upper_m4"] * dimensions[axis]
        for axis in range(3)])
    ratio = response / np.sqrt(exact[:, None] * exact[None, :])
    deficit = 1 - np.diag(ratio)

    prefix = f"n{cells}_"
    saved_mass = space[prefix + "mass"]
    saved_map = space[prefix + "cell_integrated_current_map"]
    saved_rhs = space[prefix + "uniform_curl_rhs"]
    saved_solution = space[prefix + "closed_uniform_curl_solution"]
    saved_order = space[prefix + "coordinate_order"]
    saved_q = space[prefix + "boundary_divergence_range"]
    checks = dict(tetrahedra=cells, total_coordinates=len(gram), closed_coordinates=split,
        old_closed_coordinates=old_split, polynomial_coordinates=modes, charge_ranges=q.shape[1],
        mass_rebuild_relative=relative(gram, saved_mass), map_rebuild_relative=relative(combined, saved_map),
        rhs_rebuild_relative=relative(rhs, saved_rhs), solution_rebuild_relative=relative(coefficients, saved_solution),
        coordinate_order_exact=bool(np.array_equal(order, saved_order)), charge_range_exact=bool(np.array_equal(q, saved_q)),
        minimum_full_normalized_mass_eigenvalue=float(full_eigenvalues.min()),
        maximum_full_normalized_mass_eigenvalue=float(full_eigenvalues.max()),
        full_normalized_condition=float(full_eigenvalues.max() / full_eigenvalues.min()),
        minimum_closed_normalized_mass_eigenvalue=float(normalized_eigenvalues.min()),
        maximum_closed_normalized_mass_eigenvalue=float(normalized_eigenvalues.max()),
        schur_minimum_eigenvalue=float(schur_eigenvalues.min()), schur_maximum_eigenvalue=float(schur_eigenvalues.max()),
        schur_rank=int(np.count_nonzero(schur_eigenvalues > saved["schur_rank_tolerance"])),
        direct_full_projection_residual=residual, response_rebuild_relative=relative(response, saved["geometric_response_m5"]),
        response_eigenvalue_rebuild_relative=relative(np.linalg.eigvalsh(ratio), saved["response_eigenvalues"]),
        loss_deficit_rebuild_relative=relative(deficit, saved["loss_deficit_relative"]),
        maximum_off_axis_normalized=float(np.max(abs(ratio - np.diag(np.diag(ratio))))),
        bubble_integrated_current_relative=zero_moment,
        boundary_stream_maximum=boundary, divergence_coefficient_maximum=divergence)
    assert checks["total_coordinates"] == saved["combined_current_coordinates"]
    assert checks["closed_coordinates"] == saved["closed_current_coordinates"]
    assert checks["polynomial_coordinates"] == 48 and checks["schur_rank"] == 48
    assert min(full_eigenvalues) > 1e-10 and min(normalized_eigenvalues) > 1e-10
    assert min(schur_eigenvalues) > saved["schur_rank_tolerance"]
    assert abs(checks["minimum_full_normalized_mass_eigenvalue"] / saved["minimum_normalized_mass_eigenvalue"] - 1) < 1e-10
    assert abs(checks["full_normalized_condition"] / saved["normalized_mass_condition"] - 1) < 1e-10
    assert max(checks[key] for key in ("mass_rebuild_relative", "map_rebuild_relative", "rhs_rebuild_relative",
        "solution_rebuild_relative", "response_rebuild_relative", "response_eigenvalue_rebuild_relative",
        "loss_deficit_rebuild_relative")) < 1e-10
    assert checks["coordinate_order_exact"] and checks["charge_range_exact"]
    assert residual < 1e-10 and boundary < 1e-13 and divergence < 1e-13
    assert np.linalg.eigvalsh(ratio).min() > 0 and np.linalg.eigvalsh(ratio).max() <= 1 + 1e-11
    return checks


def run(output: Path):
    assert not output.exists()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    result = json.loads(PINS["result"][0].read_bytes())
    polynomial = json.loads(PINS["polynomial_result"][0].read_bytes())
    assert result["status"] == "PASS_BOX_POLYNOMIAL_CURRENT_SPACE"
    assert result["space_sha256"] == PINS["space"][1]
    assert result["script_sha256"] == PINS["driver"][1]
    for path, expected in result["pins"].items():
        assert sha(ROOT / path) == expected
    with np.load(PINS["space"][0], allow_pickle=False) as space:
        cases = [review_case(48, 25, PINS["kernel48"][0], PINS["field48"][0], result["cases"][0], space, polynomial),
            review_case(384, 289, PINS["kernel384"][0], PINS["field384"][0], result["cases"][1], space, polynomial)]
    review = dict(program="SPD Decap PI Evaluator", version="0.23.1",
        status="ACCEPT_BOX_POLYNOMIAL_CURRENT_SPACE_SAVED_REVIEW",
        pins={name: expected for name, (_, expected) in PINS.items()}, reviewer_sha256=sha(Path(__file__)), checks=cases,
        scope="Saved-space review only. The centered homogeneous-box 3x16 global curl bubbles are zero-normal, divergence-free, zero-mean closed currents and improve the omega-to-zero uniform-curl projection. This is not a complete 3D enrichment, a finite-k Green/current-charge solve, skin/material-interface convergence, a port/board model or PowerSI accuracy.")
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
