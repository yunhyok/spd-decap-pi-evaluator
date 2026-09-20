"""SPD Decap PI Evaluator v0.23.1: qualify two missing axial box modes.

This is a mass-only qualification on the accepted 48-tetrahedron homogeneous
box.  It neither assembles a Green operator nor performs a frequency solve.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import traceback

import numpy as np

import qualify_astra_box_polynomial_current_space as space


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = space.ROOT
field = space.field
PINS = {
    "tools/research/qualify_astra_box_polynomial_current_space.py":
        "a7d68c398158704e994232976abffa1312279b19ebcc9612281b8b44ee9ad17b",
    "outputs/research/astra-box-polynomial-current-space-01/result.json":
        "51c06b1d990409daa0ddc6352b0569f15e95abddb9d99537bb88eb5c63ba3620",
    "outputs/research/astra-box-polynomial-current-space-01/space.npz":
        "41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9",
    "outputs/research/astra-box-polynomial-current-space-review-02/independent-review.json":
        "59d2fe7698d7496ae82fe914f01d77194977f20b9e03074eb1d7fe05ee2ddc2b",
    "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz":
        "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02",
    "outputs/research/astra-box-eddy-polynomial-basis-01/result.json":
        "85252d666b3f2fd0fee9ae54341da11ea790882cde10abe75f547f3cbd40eda5",
}
MODE_NAMES = [
    "A_y=L0*phi0(sx)*P2(sy)*phi0(sz)*e_y",
    "A_x=L0*P1(sx)*phi1(sy)*phi0(sz)*e_x",
]


def _one_write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def _polynomials(s):
    p1 = s
    p2 = (3 * s * s - 1) / 2
    phi0 = 1 - s * s
    dphi0 = -2 * s
    phi1 = s * (1 - s * s)
    dphi1 = 1 - 3 * s * s
    return p1, p2, phi0, dphi0, phi1, dphi1


def axial_values(points_m, center_m, dimensions_m):
    """Return J for [A_y, A_x] as (point, xyz, mode).

    The normalized coordinates are s=2*(r-center)/L and L0=max(L).
    This public low-level function is also the exact candidate contract used by
    the subsequent Green qualification.
    """
    points = np.asarray(points_m)
    s = 2 * (points.reshape(-1, 3) - center_m) / dimensions_m
    px = _polynomials(s[:, 0])
    py = _polynomials(s[:, 1])
    pz = _polynomials(s[:, 2])
    p1x, _, phi0x, dphi0x, _, _ = px
    _, p2y, _, _, phi1y, dphi1y = py
    p1z, _, phi0z, dphi0z, _, _ = pz
    l0 = float(np.max(dimensions_m))
    result = np.zeros((len(s), 3, 2), dtype=np.result_type(points, float))
    # curl(A_y e_y)=(-d_z A_y,0,+d_x A_y)
    result[:, 0, 0] = -2 * l0 / dimensions_m[2] * phi0x * p2y * dphi0z
    result[:, 2, 0] = +2 * l0 / dimensions_m[0] * dphi0x * p2y * phi0z
    # curl(A_x e_x)=(0,+d_z A_x,-d_y A_x)
    result[:, 1, 1] = +2 * l0 / dimensions_m[2] * p1x * phi1y * dphi0z
    result[:, 2, 1] = -2 * l0 / dimensions_m[1] * p1x * dphi1y * phi0z
    return result


def _dependent_az_values(points_m, center_m, dimensions_m):
    points = np.asarray(points_m)
    s = 2 * (points.reshape(-1, 3) - center_m) / dimensions_m
    px = _polynomials(s[:, 0])
    py = _polynomials(s[:, 1])
    pz = _polynomials(s[:, 2])
    _, _, phi0x, dphi0x, _, _ = px
    _, _, _, _, phi1y, dphi1y = py
    p1z = pz[0]
    l0 = float(np.max(dimensions_m))
    result = np.zeros((len(s), 3), dtype=np.result_type(points, float))
    # curl(A_z e_z)=(+d_y A_z,-d_x A_z,0)
    result[:, 0] = +2 * l0 / dimensions_m[1] * phi0x * dphi1y * p1z
    result[:, 1] = -2 * l0 / dimensions_m[0] * dphi0x * phi1y * p1z
    return result


def _old_bubble_values(points_m, center_m, dimensions_m, count=4):
    points = np.asarray(points_m).reshape(-1, 3)
    s = 2 * (points - center_m) / dimensions_m
    values = [space.bubbles(s[:, axis], count) for axis in range(3)]
    n = count * count
    l0 = float(np.max(dimensions_m))
    result = np.zeros((len(points), 3, 3 * n))
    for axis in range(3):
        a, b = (axis + 1) % 3, (axis + 2) % 3
        va, da = values[a]
        vb, db = values[b]
        block = slice(axis * n, (axis + 1) * n)
        result[:, a, block] = (
            2 * l0 / dimensions_m[b]
            * np.einsum("pi,pj->pij", va, db).reshape(len(points), n)
        )
        result[:, b, block] = (
            -2 * l0 / dimensions_m[a]
            * np.einsum("pi,pj->pij", da, vb).reshape(len(points), n)
        )
    return result


def _integrate(tetrahedra_m, center_m, dimensions_m, order):
    cell_moments = np.zeros((len(tetrahedra_m), 3, 2))
    new_mass = np.zeros((2, 2))
    bubble_cross = np.zeros((48, 2))
    uniform_curl_rhs = np.zeros((2, 3))
    axes = np.eye(3)
    for index, tetra in enumerate(tetrahedra_m):
        points, weights = field.static.tetra_quadrature(tetra, order)
        new = axial_values(points, center_m, dimensions_m)
        old = _old_bubble_values(points, center_m, dimensions_m)
        relative = points - center_m
        forcing = np.stack(
            [0.5 * np.cross(axes[axis], relative) for axis in range(3)],
            axis=2,
        )
        cell_moments[index] = np.einsum("p,pdi->di", weights, new)
        new_mass += np.einsum("p,pdi,pdj->ij", weights, new, new)
        bubble_cross += np.einsum("p,pdk,pdi->ki", weights, old, new)
        uniform_curl_rhs += np.einsum("p,pdi,pds->is", weights, new, forcing)
    return {
        "cell_moments": cell_moments,
        "new_mass": new_mass,
        "bubble_cross": bubble_cross,
        "uniform_curl_rhs": uniform_curl_rhs,
    }


def _integrate_box(center_m, dimensions_m, order=12):
    """Independent tensor-product polynomial integral over the full box."""
    x, w = np.polynomial.legendre.leggauss(order)
    grid = np.stack(np.meshgrid(x, x, x, indexing="ij"), axis=-1).reshape(-1, 3)
    weights = (
        np.einsum("i,j,k->ijk", w, w, w).ravel()
        * np.prod(dimensions_m)
        / 8
    )
    points = center_m + grid * dimensions_m / 2
    new = axial_values(points, center_m, dimensions_m)
    old = _old_bubble_values(points, center_m, dimensions_m)
    axes = np.eye(3)
    relative = points - center_m
    forcing = np.stack(
        [0.5 * np.cross(axes[axis], relative) for axis in range(3)],
        axis=2,
    )
    return {
        "mean": np.einsum("p,pdi->di", weights, new),
        "new_mass": np.einsum("p,pdi,pdj->ij", weights, new, new),
        "bubble_cross": np.einsum("p,pdk,pdi->ki", weights, old, new),
        "uniform_curl_rhs": np.einsum("p,pdi,pds->is", weights, new, forcing),
    }


def _relative_norm(a, b):
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), np.finfo(float).tiny))


def _validate_inputs():
    for relative, expected in PINS.items():
        actual = field.source.sha(ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"pin mismatch {relative}: {actual}")
    base_result = json.loads(
        (ROOT / "outputs/research/astra-box-polynomial-current-space-01/result.json").read_bytes()
    )
    base_review = json.loads(
        (ROOT / "outputs/research/astra-box-polynomial-current-space-review-02/independent-review.json").read_bytes()
    )
    if base_result["status"] != "PASS_BOX_POLYNOMIAL_CURRENT_SPACE":
        raise RuntimeError("base current-space status")
    if not str(base_review["status"]).startswith("ACCEPT"):
        raise RuntimeError("base independent-review status")
    with np.load(
        ROOT / "outputs/research/astra-box-polynomial-current-space-01/space.npz",
        allow_pickle=False,
    ) as saved:
        mass = saved["n48_mass"]
        current_map = saved["n48_cell_integrated_current_map"]
        divergence_range = saved["n48_boundary_divergence_range"]
        old_solution = saved["n48_closed_uniform_curl_solution"]
        old_rhs = saved["n48_uniform_curl_rhs"]
        old_order = saved["n48_coordinate_order"]
    expected_order = np.r_[np.arange(25), np.arange(72, 120), np.arange(25, 72)]
    if (
        mass.shape != (120, 120)
        or current_map.shape != (48, 3, 120)
        or divergence_range.shape != (48, 47)
        or old_solution.shape != (73, 3)
        or old_rhs.shape != (120, 3)
        or not np.array_equal(old_order, expected_order)
    ):
        raise RuntimeError("base saved-array layout")
    with np.load(
        ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz",
        allow_pickle=False,
    ) as kernels:
        tetrahedra = kernels["tetrahedra_m"]
    if tetrahedra.shape != (48, 4, 3) or not np.isfinite(tetrahedra).all():
        raise RuntimeError("48-tetrahedron geometry")
    return (
        base_result,
        mass,
        current_map,
        divergence_range,
        old_solution,
        old_rhs,
        old_order,
        tetrahedra,
    )


def self_check():
    dimensions = np.array([5.0, 3.0, 2.0])
    center = np.array([0.25, -0.5, 0.75])
    rng = np.random.default_rng(24681357)
    points = center + (rng.random((128, 3)) - 0.5) * dimensions
    selected = axial_values(points, center, dimensions)
    dependent = _dependent_az_values(points, center, dimensions)
    relation = (
        selected[:, :, 1] / dimensions[0]
        + selected[:, :, 0] / dimensions[1]
        + dependent / dimensions[2]
    )
    relation_relative = np.linalg.norm(relation) / np.linalg.norm(selected)
    assert relation_relative < 1e-14
    # Each face normal component vanishes exactly as a polynomial identity.
    for axis in range(3):
        for sign in (-1.0, 1.0):
            face = points.copy()
            face[:, axis] = center[axis] + sign * dimensions[axis] / 2
            assert np.max(np.abs(axial_values(face, center, dimensions)[:, axis])) < 1e-13
    # Direct tensor quadrature verifies zero mean and the exact two-mode rank.
    x, w = np.polynomial.legendre.leggauss(8)
    grid = np.stack(np.meshgrid(x, x, x, indexing="ij"), axis=-1).reshape(-1, 3)
    weights = np.einsum("i,j,k->ijk", w, w, w).ravel() * np.prod(dimensions) / 8
    physical = center + grid * dimensions / 2
    values = axial_values(physical, center, dimensions)
    mean = np.einsum("p,pdi->di", weights, values)
    mass = np.einsum("p,pdi,pdj->ij", weights, values, values)
    assert np.linalg.norm(mean) < 1e-13 * np.sqrt(np.trace(mass) * np.prod(dimensions))
    assert np.linalg.eigvalsh(mass).min() > 0
    print("PASS_BOX_AXIAL_CURRENT_SPACE_SELF_CHECK")


def dry_check():
    data = _validate_inputs()
    print(json.dumps({
        "status": "PASS_BOX_AXIAL_CURRENT_SPACE_DRY_CHECK",
        "base_mass_shape": list(data[1].shape),
        "base_closed_coordinates": int(data[4].shape[0]),
        "tetrahedra": int(data[-1].shape[0]),
        "candidate_order": MODE_NAMES,
    }))


def run(output):
    start = monotonic()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        (
            base_result,
            old_mass,
            old_map,
            divergence_range,
            old_solution,
            old_rhs,
            old_order,
            tetrahedra,
        ) = _validate_inputs()
        vertices = tetrahedra.reshape(-1, 3)
        dimensions = np.ptp(vertices, axis=0)
        center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
        volumes = np.array([field.static.faces(tetra)[0] for tetra in tetrahedra])
        if not (np.all(volumes > 0) and abs(volumes.sum() - np.prod(dimensions)) < 1e-12 * np.prod(dimensions)):
            raise RuntimeError("tetrahedron volumes")

        q6 = _integrate(tetrahedra, center, dimensions, 6)
        q8 = _integrate(tetrahedra, center, dimensions, 8)
        tensor = _integrate_box(center, dimensions, 12)
        mass_refinement = _relative_norm(q8["new_mass"], tensor["new_mass"])
        moment_refinement = _relative_norm(q6["cell_moments"], q8["cell_moments"])
        if max(mass_refinement, moment_refinement) >= 1e-11:
            raise RuntimeError("polynomial integration refinement")

        new_mass = tensor["new_mass"]
        moments = q8["cell_moments"]
        bubble_cross = tensor["bubble_cross"]
        bubble_scale = np.sqrt(
            np.diag(old_mass)[25:73, None] * np.diag(new_mass)[None, :]
        )
        bubble_cross_relative = float(np.max(np.abs(bubble_cross) / bubble_scale))
        if bubble_cross_relative >= 1e-11:
            raise RuntimeError("new modes are not orthogonal to saved global bubbles")
        mean_relative = np.linalg.norm(moments.sum(axis=0), axis=0) / np.maximum(
            np.sum(np.linalg.norm(moments, axis=1), axis=0), np.finfo(float).tiny
        )
        tensor_mean_relative = np.linalg.norm(tensor["mean"], axis=0) / np.maximum(
            np.sqrt(np.diag(new_mass) * np.prod(dimensions)), np.finfo(float).tiny
        )
        if max(float(np.max(mean_relative)), float(np.max(tensor_mean_relative))) >= 1e-11:
            raise RuntimeError("candidate integrated current")

        # Existing closed-loop and boundary-range RT0 currents are cellwise
        # constant.  Their cross mass is therefore exactly H_old/V dot int Jnew.
        cross = np.einsum("tdi,tdj,t->ij", old_map, moments, 1 / volumes)
        cell_average_bubble_cross = cross[25:73].copy()
        cross[25:73] = bubble_cross
        appended_mass = np.block([[old_mass, cross], [cross.T, new_mass]])
        appended_map = np.concatenate((old_map, moments), axis=2)
        appended_rhs = np.vstack((old_rhs, tensor["uniform_curl_rhs"]))

        # Old order is [25 loops,48 global bubbles,47 surface ranges].
        # Insert the two new closed modes immediately before the ranges.
        coordinate_order = np.r_[np.arange(73), np.arange(120, 122), np.arange(73, 120)]
        mass = appended_mass[np.ix_(coordinate_order, coordinate_order)]
        current_map = appended_map[:, :, coordinate_order]
        rhs = appended_rhs[coordinate_order]
        closed = 75
        scale = 1 / np.sqrt(np.diag(mass))
        normalized = scale[:, None] * mass * scale[None, :]
        eig = np.linalg.eigvalsh((normalized + normalized.T) / 2)
        if eig.min() <= 1e-10:
            raise RuntimeError("combined normalized mass is not positive definite")

        a = normalized[:73, :73]
        b = normalized[:73, 73:75]
        d = normalized[73:75, 73:75]
        a_inverse_b = np.linalg.solve(a, b)
        schur = d - b.T @ a_inverse_b
        schur = (schur + schur.T) / 2
        schur_eigenvalues = np.linalg.eigvalsh(schur)
        quadrature_error = max(mass_refinement, moment_refinement, bubble_cross_relative)
        rank_tolerance = float(
            100
            * (np.finfo(float).eps * np.linalg.cond(a) + quadrature_error)
            * (np.linalg.norm(d, 2) + np.linalg.norm(b.T @ a_inverse_b, 2))
        )
        if np.count_nonzero(schur_eigenvalues > rank_tolerance) != 2:
            raise RuntimeError("two-mode Schur rank")

        scaled_rhs = scale[:closed, None] * rhs[:closed]
        normalized_solution = np.linalg.solve(normalized[:closed, :closed], scaled_rhs)
        solution = scale[:closed, None] * normalized_solution
        residual = float(
            np.linalg.norm(mass[:closed, :closed] @ solution - rhs[:closed])
            / np.linalg.norm(rhs[:closed])
        )
        response = rhs[:closed].T @ solution
        old_response = old_rhs[:73].T @ old_solution
        projection_gain = (response + response.T - old_response - old_response.T) / 2
        gain_eigenvalues = np.linalg.eigvalsh(projection_gain)
        gain_tolerance = 100 * np.finfo(float).eps * np.linalg.norm(response, 2)
        if gain_eigenvalues.min() < -gain_tolerance or residual >= 1e-10:
            raise RuntimeError("Poisson variational projection")

        oracle_result = json.loads(
            (ROOT / "outputs/research/astra-box-eddy-polynomial-basis-01/result.json").read_bytes()
        )
        exact = np.array([
            oracle_result["cases"][axis]["reference"]["integral_upper_m4"] * dimensions[axis]
            for axis in range(3)
        ])
        old_deficit = 1 - np.diag(old_response) / exact
        new_deficit = 1 - np.diag(response) / exact
        direct_rhs_scale = np.sqrt(np.diag(new_mass))[:, None] * np.sqrt(exact)[None, :]
        direct_rhs_relative = float(
            np.max(np.abs(tensor["uniform_curl_rhs"]) / direct_rhs_scale)
        )
        if direct_rhs_relative >= 1e-11:
            raise RuntimeError("candidate direct uniform-curl forcing")

        with (output / "space.npz").open("xb") as stream:
            np.savez_compressed(
                stream,
                mass=mass,
                cell_integrated_current_map=current_map,
                boundary_divergence_range=divergence_range,
                uniform_curl_rhs=rhs,
                closed_uniform_curl_solution=solution,
                coordinate_order=coordinate_order,
                new_cell_integrated_current_map=moments,
                new_mass=new_mass,
                old_to_new_cross_mass=cross,
                exact_global_bubble_cross_mass=bubble_cross,
                q6_cell_integrated_current_map=q6["cell_moments"],
                q8_tetra_new_mass=q8["new_mass"],
                tensor_new_mean=tensor["mean"],
            )

        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "PASS_BOX_AXIAL_CURRENT_SPACE",
            "pins": PINS,
            "script_sha256": field.source.sha(Path(__file__)),
            "space_sha256": field.source.sha(output / "space.npz"),
            "candidate_order": MODE_NAMES,
            "candidate_contract": {
                "normalized_coordinates": "s=2*(r-center)/L",
                "stream_scale_m": float(np.max(dimensions)),
                "potential_relation":
                    "A_x/Lx + A_y/Ly + A_z/Lz = -(L0/4)*grad(phi0x*phi1y*phi0z)",
                "curl_relation":
                    "J_Ax/Lx + J_Ay/Ly + J_Az/Lz = 0; A_z is excluded",
            },
            "tetrahedra": 48,
            "old_coordinates": 120,
            "old_closed_coordinates": 73,
            "new_coordinates": 2,
            "combined_coordinates": 122,
            "combined_closed_coordinates": closed,
            "surface_charge_ranges": int(divergence_range.shape[1]),
            "q8_tetra_tensor_new_mass_relative": mass_refinement,
            "q6_q8_cell_moment_relative": moment_refinement,
            "maximum_new_mean_current_relative": float(np.max(mean_relative)),
            "maximum_tensor_new_mean_current_relative": float(np.max(tensor_mean_relative)),
            "maximum_exact_old_bubble_cross_relative": bubble_cross_relative,
            "cell_average_old_bubble_cross_norm": float(np.linalg.norm(cell_average_bubble_cross)),
            "direct_uniform_curl_rhs_relative": direct_rhs_relative,
            "minimum_combined_normalized_mass_eigenvalue": float(eig.min()),
            "combined_normalized_mass_condition": float(eig.max() / eig.min()),
            "new_mode_schur_eigenvalues": schur_eigenvalues.tolist(),
            "new_mode_schur_rank_tolerance": rank_tolerance,
            "new_mode_schur_numerical_rank": int(
                np.count_nonzero(schur_eigenvalues > rank_tolerance)
            ),
            "mass_projection_residual": residual,
            "old_geometric_response_m5": old_response.tolist(),
            "new_geometric_response_m5": response.tolist(),
            "projection_gain_eigenvalues_m5": gain_eigenvalues.tolist(),
            "old_loss_deficit_relative": old_deficit.tolist(),
            "new_loss_deficit_relative": new_deficit.tolist(),
            "loss_deficit_reduction": (old_deficit - new_deficit).tolist(),
            "elapsed_s": monotonic() - start,
            "scope":
                "Mass-only qualification of two global polynomial curl modes on the accepted "
                "48-tetrahedron homogeneous 100x100x25um box. It checks their exact gauge "
                "dependence, closed/zero-mean character, orthogonality to the existing 48 "
                "global bubbles, independent Schur rank, and omega-to-zero uniform-curl "
                "projection. It performs no Green, material, frequency, field, port, raw-SPD, "
                "scenario, or board solve and does not establish complete 3D or broadband "
                "convergence.",
        }
        _one_write_json(output / "result.json", result)
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"]}))
    except Exception as exc:
        failure = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "STOP_BOX_AXIAL_CURRENT_SPACE",
            "script_sha256": field.source.sha(Path(__file__)),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "elapsed_s": monotonic() - start,
        }
        _one_write_json(output / "failure.json", failure)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.dry_check:
        dry_check()
    elif args.output is not None:
        run(args.output.resolve())
    else:
        parser.error("choose --self-check, --dry-check, or --output")
