"""SPD Decap PI Evaluator v0.23.1: strict saved review of boundary-joint currents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, vstack


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-boundary-joint-current-space-review-03"
PINS = {
    "outputs/research/astra-boundary-joint-current-space-01/driver-at-run.py": "79fbc4a43db19e4b679352fcdb38a001252155f056d48b978d8a0a493832455c",
    "outputs/research/astra-boundary-joint-current-space-01/result.json": "44fe27c9326105143ca7b608fae681f93b3b80e6a271f3582829f9a7f64ff186",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "tools/research/qualify_astra_power_joint_boundary_seed_modes.py": "34d18684fa24f741db9bba313e0dfc9580fb83c9afafced1e8723337ce22ac05",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def npz(relative: str) -> dict[str, np.ndarray]:
    with np.load(ROOT / relative, allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


def csr(prefix: str, data: dict[str, np.ndarray]) -> csr_matrix:
    return csr_matrix(
        (data[prefix + "_data"], data[prefix + "_col"], data[prefix + "_row_ptr"]),
        shape=tuple(map(int, data[prefix + "_shape"])),
    )


def sparse_max_abs(matrix: csr_matrix) -> float:
    return 0.0 if matrix.nnz == 0 else float(np.max(np.abs(matrix.data)))


def analytic_fields(points: np.ndarray, center: np.ndarray, length: float) -> np.ndarray:
    """Eight gradients followed by three centered rotations."""
    u = (points - center) / length
    fields = []
    for axis in range(3):
        value = np.zeros_like(u)
        value[:, axis] = 1.0
        fields.append(value)
    fields.extend(
        (
            np.c_[u[:, 0], -u[:, 1], np.zeros(len(u))],
            np.c_[u[:, 0], np.zeros(len(u)), -u[:, 2]],
            np.c_[u[:, 1], u[:, 0], np.zeros(len(u))],
            np.c_[u[:, 2], np.zeros(len(u)), u[:, 0]],
            np.c_[np.zeros(len(u)), u[:, 2], u[:, 1]],
        )
    )
    for axis in range(3):
        unit = np.zeros(3)
        unit[axis] = 1.0
        fields.append(np.cross(np.broadcast_to(unit, u.shape), u))
    return np.stack(fields, axis=2)


def main() -> None:
    observed = {name: digest(ROOT / name) for name in PINS}
    require(observed == PINS, "input pin mismatch")
    saved = npz("outputs/research/astra-boundary-joint-current-space-01/current-space.npz")
    mesh = npz("outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz")

    nc = int(saved["cell_count"][0])
    nf = int(saved["face_count"][0])
    require((nc, nf) == (5304, 12546), "fine dimensions")
    columns = saved["local_rt0_face_columns"]
    signs = saved["local_rt0_face_signs"]
    require(columns.shape == signs.shape == (nc, 4), "local lift")

    stored_mass = csr("mass", saved)
    stored_d = csr("volume_b", saved)
    stored_b = csr("distributional_b", saved)
    require(stored_mass.nnz == 76194, "mass nnz")
    boundary = saved["boundary_face_ids"]
    require(len(boundary) == 3876, "boundary charge supports")

    vertices = mesh["vertices_local_um"] * 1e-6
    tetrahedra = vertices[mesh["cells"]]
    determinants = np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])
    volumes = np.abs(determinants) / 6.0
    a = 0.5854101966249685
    b = 0.1381966011250105
    barycentric = np.full((4, 4), b)
    np.fill_diagonal(barycentric, a)
    points = np.einsum("qi,cid->cqd", barycentric, tetrahedra)
    basis = (points[:, :, None, :] - tetrahedra[:, None, :, :]) / (
        3.0 * volumes[:, None, None, None]
    )
    local_mass = np.einsum("c,cqid,cqjd->cij", volumes / 4.0, basis, basis)
    signed_mass = local_mass * signs[:, :, None] * signs[:, None, :]
    independent_mass = coo_matrix(
        (
            signed_mass.ravel(),
            (
                np.broadcast_to(columns[:, :, None], (nc, 4, 4)).ravel(),
                np.broadcast_to(columns[:, None, :], (nc, 4, 4)).ravel(),
            ),
        ),
        shape=(nf, nf),
    ).tocsr()
    mass_error = sparse_max_abs(independent_mass - stored_mass) / float(np.max(np.abs(stored_mass.data)))
    require(mass_error < 1e-12, "independent physical mass")

    independent_d = coo_matrix(
        (
            signs.ravel(),
            (np.repeat(np.arange(nc), 4), columns.ravel()),
        ),
        shape=(nc, nf),
    ).tocsr()
    d_error = sparse_max_abs(independent_d - stored_d)
    require(d_error == 0.0, "independent volume divergence")
    exterior = coo_matrix(
        (-np.ones(len(boundary)), (np.arange(len(boundary)), boundary)),
        shape=(len(boundary), nf),
    ).tocsr()
    independent_b = vstack((independent_d, exterior), format="csr")
    b_error = sparse_max_abs(independent_b - stored_b)
    require(b_error == 0.0, "independent distributional divergence")

    resistance = csr_matrix(
        (saved["resistance_data_ohm"], saved["mass_col"], saved["mass_row_ptr"]),
        shape=(nf, nf),
    )
    conductivity = float(saved["conductivity_s_m"][0])
    resistance_error = sparse_max_abs(resistance - stored_mass / conductivity)
    require(resistance_error < 1e-25, "resistance scaling")

    modes = saved["face_flux_seed_modes"]
    require(modes.shape == (nf, 14), "mode shape")
    expected_names = [
        "LEFT_TOP_TO_LOWER",
        "RIGHT_TOP_TO_LOWER",
        "LEFT_TOP_TO_RIGHT_TOP",
        "uniform_gradient_x",
        "uniform_gradient_y",
        "uniform_gradient_z",
        "x2_minus_y2",
        "x2_minus_z2",
        "xy",
        "xz",
        "yz",
        "rotation_x",
        "rotation_y",
        "rotation_z",
    ]
    require(saved["seed_names"].tolist() == expected_names, "mode order")

    # Rebuild the complete transport KKT equations, including the fourth
    # dependent patch sum used as a post-solve check.
    patch = coo_matrix(
        (
            np.ones(len(saved["patch_face_ids"])),
            (saved["patch_face_rows"], saved["patch_face_ids"]),
        ),
        shape=(4, nf),
    ).tocsr()
    targets = saved["patch_flux_targets"]
    active = saved["transport_active_face_ids"]
    transport = modes[:, :3]
    transport_constraint = vstack((stored_d[:, active], patch[:3, active]), format="csr")
    transport_lambda = saved["transport_lagrange_multipliers"]
    transport_scale = float(saved["transport_metric_scale"])
    transport_top = (
        resistance[active][:, active] @ transport[active] / transport_scale
        + transport_constraint.T @ transport_lambda
    )
    transport_bottom = transport_constraint @ transport[active] - np.vstack(
        (np.zeros((nc, 3)), targets[:3])
    )
    transport_kkt_scale = max(
        float(np.linalg.norm(resistance[active][:, active] @ transport[active] / transport_scale)),
        float(np.linalg.norm(transport_constraint.T @ transport_lambda)),
        float(np.linalg.norm(targets[:3])),
    )
    transport_kkt_relative = float(
        np.hypot(np.linalg.norm(transport_top), np.linalg.norm(transport_bottom)) / transport_kkt_scale
    )
    transport_patch_error = float(np.max(np.abs(patch @ transport - targets)))
    transport_divergence = float(np.max(np.abs(stored_d @ transport)))
    inactive = np.setdiff1d(np.arange(nf), active)
    transport_inactive = float(np.max(np.abs(transport[inactive])))
    require(transport_kkt_relative < 1e-12, "transport KKT")
    require(transport_patch_error < 1e-12, "transport patch sums")
    require(transport_divergence < 1e-12 and transport_inactive == 0.0, "transport support/divergence")

    # Reconstruct analytic face fluxes and the full R_ib extension equations.
    centers = vertices[mesh["face_vertices"]].mean(axis=1)
    area_vectors = mesh["face_area_vector_um2"] * 1e-12
    analytic = np.einsum(
        "fi,fim->fm",
        area_vectors,
        analytic_fields(centers, saved["seed_center_m"], float(saved["seed_length_m"])),
    )
    analytic_error = float(np.max(np.abs(analytic - saved["analytic_face_flux"])))
    require(analytic_error < 1e-22, "analytic face flux")
    extension = saved["minimum_energy_extensions"]
    interior = saved["seed_interior_face_ids"]
    require(float(np.max(np.abs(extension[boundary] - analytic[boundary]))) == 0.0, "fixed boundary")
    seed_scale = float(saved["seed_metric_scale"])
    seed_lambda = saved["seed_lagrange_multipliers"]
    seed_a = stored_d[:-1, interior]
    seed_primal = resistance[interior] @ extension / seed_scale
    seed_dual = seed_a.T @ seed_lambda
    stationarity_relative = float(
        np.linalg.norm(seed_primal + seed_dual)
        / max(np.linalg.norm(seed_primal), np.linalg.norm(seed_dual), 1e-300)
    )
    seed_constraint = stored_d[:-1] @ extension
    dropped_constraint = stored_d[-1:] @ extension
    seed_divergence = float(np.max(np.abs(stored_d @ extension)))
    require(stationarity_relative < 1e-12, "full R_ib seed stationarity")
    require(float(np.max(np.abs(seed_constraint))) < 1e-20, "seed retained constraints")
    require(float(np.max(np.abs(dropped_constraint))) < 1e-20, "seed dropped constraint")

    loops = analytic[:, 8:] - extension[:, 8:]
    extension_mode_error = float(np.max(np.abs(modes[:, 3:11] - extension[:, :8])))
    rotation_identity_error = float(np.max(np.abs(modes[:, 11:] - loops)))
    loop_boundary = float(np.max(np.abs(loops[boundary])))
    loop_divergence = float(np.max(np.abs(stored_d @ loops)))
    projection = extension[:, 8:].T @ (resistance @ loops)
    extension_energy = np.diag(extension[:, 8:].T @ (resistance @ extension[:, 8:]))
    loop_energy = np.diag(loops.T @ (resistance @ loops))
    rotation_energy_orthogonality = float(
        np.max(np.abs(projection) / np.sqrt(extension_energy[:, None] * loop_energy[None, :]))
    )
    require(extension_mode_error == 0.0 and rotation_identity_error == 0.0, "mode decomposition")
    require(loop_boundary == 0.0 and loop_divergence < 1e-20, "rotation loop constraints")
    require(rotation_energy_orthogonality < 1e-12, "rotation energy projection")

    gram = modes.T @ (resistance @ modes)
    gram_error = float(np.max(np.abs(gram - saved["local_joule_gram_ohm"])))
    scales = saved["energy_column_scales"]
    scaled = modes * scales
    scaled_gram = scaled.T @ (resistance @ scaled)
    scaled_gram_error = float(np.max(np.abs(scaled_gram - saved["scaled_gram"])))
    transform = saved["energy_orthonormal_transform"]
    orthonormal = scaled @ transform
    orthonormal_array_error = float(np.max(np.abs(orthonormal - saved["energy_orthonormal_face_flux"])))
    orthonormality = float(np.max(np.abs(orthonormal.T @ (resistance @ orthonormal) - np.eye(14))))
    require(gram_error < 1e-18 and scaled_gram_error < 1e-12, "saved Gram")
    require(orthonormal_array_error < 1e-15 and orthonormality < 1e-12, "orthonormal modes")

    local_flux = signs[:, :, None] * modes[columns]
    centers_tet = tetrahedra.mean(axis=1)
    current_center = np.einsum(
        "cim,cid->cmd", local_flux, centers_tet[:, None, :] - tetrahedra
    ) / (3.0 * volumes[:, None, None])
    radial = local_flux.sum(axis=1) / (3.0 * volumes[:, None])
    current_center_absolute = float(np.max(np.abs(current_center - saved["cell_current_center_per_m2"])))
    radial_absolute = float(np.max(np.abs(radial - saved["cell_rt0_radial_coefficient_per_m3"])))
    current_center_error = float(
        np.linalg.norm(current_center - saved["cell_current_center_per_m2"])
        / np.linalg.norm(saved["cell_current_center_per_m2"])
    )
    radial_error = float(
        np.linalg.norm(radial - saved["cell_rt0_radial_coefficient_per_m3"])
        / np.linalg.norm(saved["cell_rt0_radial_coefficient_per_m3"])
    )
    require(current_center_error < 1e-12 and radial_error < 1e-12, "saved affine current map")

    receipt = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE",
        "review": "boundary-only joint sparse physical mass and fourteen initial currents",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": observed,
        "recomputed": {
            "cells": nc,
            "face_current_dofs": nf,
            "mass_nnz": int(stored_mass.nnz),
            "boundary_charge_supports": int(len(boundary)),
            "independent_degree2_mass_relative": mass_error,
            "independent_volume_b_max_absolute": d_error,
            "independent_distributional_b_max_absolute": b_error,
            "resistance_scaling_max_absolute_ohm": resistance_error,
            "transport_kkt_relative": transport_kkt_relative,
            "transport_patch_max_absolute_a": transport_patch_error,
            "transport_divergence_max_absolute_a": transport_divergence,
            "transport_inactive_face_max_absolute_a": transport_inactive,
            "analytic_face_flux_max_absolute_a": analytic_error,
            "full_Rib_seed_stationarity_relative": stationarity_relative,
            "seed_divergence_max_absolute_a": seed_divergence,
            "dropped_cell_constraint_max_absolute_a": float(np.max(np.abs(dropped_constraint))),
            "extension_mode_max_absolute_a": extension_mode_error,
            "rotation_identity_max_absolute_a": rotation_identity_error,
            "rotation_boundary_max_absolute_a": loop_boundary,
            "rotation_divergence_max_absolute_a": loop_divergence,
            "rotation_energy_orthogonality": rotation_energy_orthogonality,
            "joule_gram_max_absolute_ohm": gram_error,
            "scaled_gram_max_absolute": scaled_gram_error,
            "energy_orthonormal_array_max_absolute_a": orthonormal_array_error,
            "energy_orthonormality_max_absolute": orthonormality,
            "cell_center_current_relative": current_center_error,
            "cell_center_current_max_absolute_a_per_m2": current_center_absolute,
            "cell_radial_coefficient_relative": radial_error,
            "cell_radial_coefficient_max_absolute_a_per_m3": radial_absolute,
            "fine_distributional_rows_retained": int(stored_b.shape[0]),
        },
        "scope": (
            "The complete fine sparse mass, volume divergence, and exterior charge rows remain available. "
            "The fourteen columns are verified initial transport/gradient/circulation trial or preconditioning vectors, "
            "not a converged truncation or a physical zero-flux boundary condition. No self/near/far Green, scalar/contact, "
            "field, global chain, port impedance, PowerSI agreement, or board accuracy is approved."
        ),
    }
    require(not OUTPUT.exists(), "review output exists")
    OUTPUT.mkdir(parents=True)
    target = OUTPUT / "independent-review.json"
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt_sha256": digest(target)}))


if __name__ == "__main__":
    main()
