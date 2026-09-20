"""SPD Decap PI Evaluator v0.23.1: strict review of all-face div-free projection."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy.sparse import bmat, csr_matrix
from scipy.sparse.linalg import splu


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-boundary-joint-divfree-projection-review-01"
PINS = {
    "tools/research/prepare_astra_boundary_joint_divfree_projection.py":
        "6a01d6b98c5fd29aa79480ac5cfecd4698b5742ad5320b21d8c046eed0a18994",
    "outputs/research/astra-boundary-joint-divfree-projection-04/driver-at-run.py":
        "6a01d6b98c5fd29aa79480ac5cfecd4698b5742ad5320b21d8c046eed0a18994",
    "outputs/research/astra-boundary-joint-divfree-projection-04/result.json":
        "8343a9f8e143a6b9edfbbbe244695799bf179b97f39c41ed58dc1ff351edb58b",
    "outputs/research/astra-boundary-joint-divfree-projection-04/projection.npz":
        "978956fc03eeb73d5e3fcb300822f55c7829d40d72a2560a9f270ad8eb1df997",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz":
        "331f7a8216681b4c7625970b0b3962e120e3ae0bfb6e0e82b0cabc4ce4921eae",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def sparse(source: np.lib.npyio.NpzFile, prefix: str) -> csr_matrix:
    return csr_matrix(
        (source[f"{prefix}_data"], source[f"{prefix}_col"], source[f"{prefix}_row_ptr"]),
        shape=tuple(source[f"{prefix}_shape"]),
    )


def energy(matrix: csr_matrix, values: np.ndarray) -> np.ndarray:
    return np.real(np.sum(values.conj() * (matrix @ values), axis=0))


def main() -> None:
    assert not OUTPUT.exists()
    assert {path: digest(ROOT / path) for path in PINS} == PINS
    producer_result = json.loads(
        (ROOT / "outputs/research/astra-boundary-joint-divfree-projection-04/result.json")
        .read_text(encoding="utf-8")
    )
    assert producer_result["status"] == "PASS_BOUNDARY_JOINT_ALL_FACE_DIVFREE_PROJECTION"
    assert all(producer_result["gates"].values())

    with np.load(
        ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz",
        allow_pickle=False,
    ) as source:
        resistance = csr_matrix(
            (source["resistance_data_ohm"], source["mass_col"], source["mass_row_ptr"]),
            shape=tuple(source["mass_shape"]),
        )
        divergence = sparse(source, "volume_b")
        distributional = sparse(source, "distributional_b")
        boundary = source["boundary_face_ids"]
        columns = source["local_rt0_face_columns"]
        signs = source["local_rt0_face_signs"]
    with np.load(
        ROOT / "outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz",
        allow_pickle=False,
    ) as source:
        original = source["source_face_currents"]
        original_centers = source["source_current_centers"]
        original_radial = source["source_current_radial"]
    with np.load(
        ROOT / "outputs/research/astra-boundary-joint-divfree-projection-04/projection.npz",
        allow_pickle=False,
    ) as source:
        projected = source["projected_face_currents"]
        multipliers = source["lagrange_multipliers"]
        saved_boundary = source["projected_boundary_outward_currents"]
        saved_distributional = source["projected_distributional_divergence"]
        saved_centers = source["projected_current_centers"]
        saved_radial = source["projected_current_radial"]
        scale = float(source["resistance_scale_ohm"][0])
    with np.load(
        ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz",
        allow_pickle=False,
    ) as source:
        vertices = source["vertices_local_um"] * 1e-6
        tetrahedra = vertices[source["cells"]]
        volumes = source["cell_volume_um3"] * 1e-18

    face_count = resistance.shape[0]
    cell_count = divergence.shape[0]
    kkt = bmat(
        [[resistance / scale, divergence.T], [divergence, None]], format="csc"
    )
    rhs = np.vstack(
        (resistance @ original / scale, np.zeros((cell_count, original.shape[1])))
    )
    solution = np.vstack((projected, multipliers))
    kkt_relative = float(np.linalg.norm(kkt @ solution - rhs) / np.linalg.norm(rhs))

    factor = splu(kkt)

    def project(values: np.ndarray) -> np.ndarray:
        right = np.vstack(
            (resistance @ values / scale, np.zeros((cell_count, values.shape[1])))
        )
        answer = factor.solve(right.real) + 1j * factor.solve(right.imag)
        return answer[:face_count]

    repeated = project(original)
    repeated_relative = float(np.linalg.norm(repeated - projected) / np.linalg.norm(projected))
    rng = np.random.default_rng(20260910)
    x = rng.standard_normal((face_count, 2)) + 1j * rng.standard_normal((face_count, 2))
    y = rng.standard_normal((face_count, 2)) + 1j * rng.standard_normal((face_count, 2))
    px = project(x)
    py = project(y)
    ppx = project(px)
    ex = energy(resistance, x)
    ey = energy(resistance, y)
    idempotence = float(np.max(np.sqrt(energy(resistance, ppx - px) / ex)))
    adjoint = float(
        np.max(
            np.abs(x.conj().T @ (resistance @ py) - px.conj().T @ (resistance @ y))
            / np.sqrt(ex[:, None] * ey[None, :])
        )
    )

    error = original - projected
    e0 = energy(resistance, original)
    ep = energy(resistance, projected)
    ee = energy(resistance, error)
    hermitian_orthogonality = float(
        np.max(np.abs(error.conj().T @ (resistance @ projected)) / np.sqrt(e0[:, None] * e0[None]))
    )
    pythagoras = float(np.max(np.abs(e0 - ep - ee) / e0))
    smooth_reproduction = float(np.sqrt(ee[0] / e0[0]))

    divergence_relative = float(
        np.max(
            np.abs(divergence @ projected)
            / np.maximum(np.max(np.abs(projected), axis=0), np.finfo(float).tiny)[None]
        )
    )
    exterior = projected[boundary]
    exterior_flux_relative = float(
        np.max(
            np.abs(exterior.sum(axis=0))
            / np.maximum(np.sum(np.abs(exterior), axis=0), np.finfo(float).tiny)
        )
    )
    distributional_value = distributional @ projected
    boundary_sign_relative = float(
        np.max(np.abs(distributional_value[cell_count:] + exterior))
        / np.max(np.abs(exterior))
    )
    assert np.array_equal(saved_boundary, exterior)
    assert np.array_equal(saved_distributional, distributional_value)

    centers = tetrahedra.mean(axis=1)
    local_flux = signs[:, :, None] * projected[columns]
    reconstructed_centers = np.einsum(
        "cim,cid->cmd", local_flux, centers[:, None] - tetrahedra
    ) / (3 * volumes[:, None, None])
    reconstructed_radial = local_flux.sum(axis=1) / (3 * volumes[:, None])
    center_saved_relative = float(
        np.linalg.norm(reconstructed_centers - saved_centers)
        / np.linalg.norm(reconstructed_centers)
    )
    radial_scale = np.linalg.norm(reconstructed_centers)
    radii = np.linalg.norm(tetrahedra - centers[:, None], axis=2).max(axis=1)
    radial_saved_physical = float(
        np.linalg.norm((reconstructed_radial - saved_radial) * radii[:, None])
        / radial_scale
    )
    points = np.einsum(
        "qi,cid->cqd",
        np.array([[0.15, 0.25, 0.35, 0.25], [0.4, 0.1, 0.2, 0.3]]),
        tetrahedra,
    )
    direct = np.einsum(
        "cim,cqid->cqmd",
        local_flux,
        (points[:, :, None] - tetrahedra[:, None])
        / (3 * volumes[:, None, None, None]),
    )
    affine = saved_centers[:, None] + saved_radial[:, None, :, None] * (
        points[:, :, None] - centers[:, None, None]
    )
    affine_relative = float(np.linalg.norm(direct - affine) / np.linalg.norm(direct))
    smooth_original = original_centers[:, None, 0] + original_radial[:, None, 0, None] * (
        points - centers[:, None]
    )
    smooth_projected = saved_centers[:, None, 0] + saved_radial[:, None, 0, None] * (
        points - centers[:, None]
    )
    smooth_field_relative = float(
        np.linalg.norm(smooth_projected - smooth_original) / np.linalg.norm(smooth_original)
    )

    metrics = {
        "kkt_relative_residual": kkt_relative,
        "saved_projection_repeat_relative": repeated_relative,
        "random_R_projector_idempotence": idempotence,
        "random_R_projector_self_adjointness": adjoint,
        "hermitian_R_orthogonality_source_scale": hermitian_orthogonality,
        "R_energy_pythagoras_relative": pythagoras,
        "smooth_R_reproduction": smooth_reproduction,
        "fine_density_projected_change_R_norm": float(np.sqrt(ee[1] / e0[1])),
        "volume_divergence_relative": divergence_relative,
        "global_exterior_flux_relative": exterior_flux_relative,
        "distributional_boundary_sign_relative": boundary_sign_relative,
        "exterior_nonzero_entries": int(np.count_nonzero(exterior)),
        "saved_center_reconstruction_relative": center_saved_relative,
        "saved_radial_reconstruction_physical_scale": radial_saved_physical,
        "independent_pointwise_affine_relative": affine_relative,
        "smooth_projected_vs_original_field_relative": smooth_field_relative,
    }
    assert max(
        metrics[key]
        for key in (
            "kkt_relative_residual",
            "saved_projection_repeat_relative",
            "random_R_projector_idempotence",
            "random_R_projector_self_adjointness",
            "hermitian_R_orthogonality_source_scale",
            "R_energy_pythagoras_relative",
            "smooth_R_reproduction",
            "volume_divergence_relative",
            "global_exterior_flux_relative",
            "distributional_boundary_sign_relative",
            "saved_center_reconstruction_relative",
            "saved_radial_reconstruction_physical_scale",
            "independent_pointwise_affine_relative",
            "smooth_projected_vs_original_field_relative",
        )
    ) < 1e-12
    assert kkt.shape == (face_count + cell_count, face_count + cell_count)
    assert metrics["exterior_nonzero_entries"] > 0

    OUTPUT.mkdir(parents=True)
    arrays = OUTPUT / "strict-review-arrays.npz"
    np.savez_compressed(
        arrays,
        reconstructed_projected_current_centers=reconstructed_centers,
        reconstructed_projected_current_radial=reconstructed_radial,
        reconstructed_distributional_divergence=distributional_value,
        source_energy=e0,
        projected_energy=ep,
        discarded_energy=ee,
    )
    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_ALL_FACE_HOMOGENEOUS_CU_DIVFREE_PROJECTION_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "artifact_sha256": digest(arrays),
        "metrics": metrics,
        "scope": (
            "Independent sparse KKT and affine-RT0 reconstruction for two saved stress currents "
            "on one homogeneous-Cu 5,304-cell joint. All 12,546 face currents are free and exterior "
            "distributional flux remains available for later contact/noncontact charge treatment. "
            "Cellwise divergence-free projection is physically applicable only to homogeneous "
            "nonzero gamma without impressed volume charge. This does not impose unlike-material "
            "normal continuity, delete surface charge, define a boundary condition, or approve a "
            "Green action, reduced solver, field, impedance, board response, or PowerSI accuracy."
        ),
    }
    (OUTPUT / "independent-review.json").write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
    )
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
