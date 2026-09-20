"""SPD Decap PI Evaluator v0.23.1: all-face homogeneous-Cu div-free projection."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import bmat, csr_matrix
from scipy.sparse.linalg import splu


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-boundary-joint-current-space-01/result.json":
        "44fe27c9326105143ca7b608fae681f93b3b80e6a271f3582829f9a7f64ff186",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-joint-complete-static-rows-02/result.json":
        "736e14ddb75dc02d37792b72059ba5c2a539f939ddabede3204dd5a3162deb22",
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


def run(output: Path) -> int:
    started = monotonic()
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    failure = None
    try:
        actual_pins = {path: digest(ROOT / path) for path in PINS}
        assert actual_pins == PINS
        with np.load(
            ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz",
            allow_pickle=False,
        ) as source:
            resistance = csr_matrix(
                (
                    source["resistance_data_ohm"],
                    source["mass_col"],
                    source["mass_row_ptr"],
                ),
                shape=tuple(source["mass_shape"]),
            )
            volume_divergence = sparse(source, "volume_b")
            distributional_divergence = sparse(source, "distributional_b")
            boundary_faces = source["boundary_face_ids"]
            local_columns = source["local_rt0_face_columns"]
            local_signs = source["local_rt0_face_signs"]
            conductivity = float(source["conductivity_s_m"][0])
        with np.load(
            ROOT / "outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz",
            allow_pickle=False,
        ) as source:
            original = source["source_face_currents"]
            original_centers = source["source_current_centers"]
            original_radial = source["source_current_radial"]
        with np.load(
            ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz",
            allow_pickle=False,
        ) as source:
            vertices = source["vertices_local_um"] * 1e-6
            cells = source["cells"]
            volumes = source["cell_volume_um3"] * 1e-18

        face_count = resistance.shape[0]
        cell_count = volume_divergence.shape[0]
        assert resistance.shape == (12546, 12546)
        assert volume_divergence.shape == (5304, 12546)
        assert distributional_divergence.shape == (9180, 12546)
        assert original.shape == (face_count, 2)
        assert len(boundary_faces) == 3876
        distributional_volume = distributional_divergence[:cell_count].tocsr()
        assert np.array_equal(distributional_volume.indptr, volume_divergence.indptr)
        assert np.array_equal(distributional_volume.indices, volume_divergence.indices)
        assert np.array_equal(distributional_volume.data, volume_divergence.data)

        positive_diagonal = resistance.diagonal()
        scale = float(np.median(positive_diagonal[positive_diagonal > 0]))
        kkt = bmat(
            [[resistance / scale, volume_divergence.T], [volume_divergence, None]],
            format="csc",
        )
        right = np.vstack(
            (resistance @ original / scale, np.zeros((cell_count, original.shape[1])))
        )
        factor_started = monotonic()
        factor = splu(kkt)
        factor_s = monotonic() - factor_started

        def project(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            rhs = np.vstack(
                (resistance @ values / scale, np.zeros((cell_count, values.shape[1])))
            )
            solution = factor.solve(rhs.real) + 1j * factor.solve(rhs.imag)
            return solution[:face_count], solution[face_count:]

        solve_started = monotonic()
        projected, multipliers = project(original)
        solve_s = monotonic() - solve_started
        solution = np.vstack((projected, multipliers))
        kkt_residual = float(
            np.linalg.norm(kkt @ solution - right)
            / max(np.linalg.norm(right), np.finfo(float).tiny)
        )

        difference = original - projected
        source_energy = energy(resistance, original)
        projected_energy = energy(resistance, projected)
        difference_energy = energy(resistance, difference)
        assert np.all(source_energy > 0)
        energy_scale = np.sqrt(source_energy[:, None] * source_energy[None, :])
        hermitian_orthogonality = float(
            np.max(np.abs(difference.conj().T @ (resistance @ projected)) / energy_scale)
        )
        pythagoras = float(
            np.max(np.abs(source_energy - projected_energy - difference_energy) / source_energy)
        )
        smooth_l2_reproduction = float(
            np.linalg.norm(difference[:, 0]) / np.linalg.norm(original[:, 0])
        )
        smooth_energy_reproduction = float(
            np.sqrt(difference_energy[0] / source_energy[0])
        )
        fine_energy_change = float(np.sqrt(difference_energy[1] / source_energy[1]))

        divergence = volume_divergence @ projected
        divergence_scale = np.maximum(np.max(np.abs(projected), axis=0), np.finfo(float).tiny)
        divergence_relative = float(np.max(np.abs(divergence) / divergence_scale[None]))
        boundary_current = projected[boundary_faces]
        global_exterior_flux = boundary_current.sum(axis=0)
        exterior_l1 = np.sum(np.abs(boundary_current), axis=0)
        global_exterior_flux_relative = float(
            np.max(np.abs(global_exterior_flux) / np.maximum(exterior_l1, np.finfo(float).tiny))
        )

        tetrahedra = vertices[cells]
        centers = tetrahedra.mean(axis=1)
        local_flux = local_signs[:, :, None] * projected[local_columns]
        projected_centers = np.einsum(
            "cim,cid->cmd", local_flux, centers[:, None, :] - tetrahedra
        ) / (3 * volumes[:, None, None])
        projected_radial = local_flux.sum(axis=1) / (3 * volumes[:, None])
        barycentric = np.array(
            [[0.4, 0.2, 0.3, 0.1], [0.1, 0.3, 0.2, 0.4]], dtype=float
        )
        points = np.einsum("qi,cid->cqd", barycentric, tetrahedra)
        direct = np.einsum(
            "cim,cqid->cqmd",
            local_flux,
            (points[:, :, None, :] - tetrahedra[:, None, :, :])
            / (3 * volumes[:, None, None, None]),
        )
        affine = (
            projected_centers[:, None]
            + projected_radial[:, None, :, None]
            * (points[:, :, None, :] - centers[:, None, None, :])
        )
        affine_scale = max(float(np.linalg.norm(direct)), np.finfo(float).tiny)
        affine_reconstruction = float(np.linalg.norm(affine - direct) / affine_scale)
        projected_smooth_field = (
            projected_centers[:, None, 0]
            + projected_radial[:, None, 0, None]
            * (points - centers[:, None])
        )
        original_smooth_field = (
            original_centers[:, None, 0]
            + original_radial[:, None, 0, None]
            * (points - centers[:, None])
        )
        original_affine_reproduction = float(
            np.linalg.norm(projected_smooth_field - original_smooth_field)
            / max(np.linalg.norm(original_smooth_field), np.finfo(float).tiny)
        )

        rng = np.random.default_rng(20260909)
        probe_x = rng.standard_normal((face_count, 2)) + 1j * rng.standard_normal((face_count, 2))
        probe_y = rng.standard_normal((face_count, 2)) + 1j * rng.standard_normal((face_count, 2))
        projected_x, _ = project(probe_x)
        projected_y, _ = project(probe_y)
        projected_twice, _ = project(projected_x)
        probe_x_energy = energy(resistance, probe_x)
        probe_y_energy = energy(resistance, probe_y)
        idempotence = float(
            np.max(
                np.sqrt(energy(resistance, projected_twice - projected_x) / probe_x_energy)
            )
        )
        left = probe_x.conj().T @ (resistance @ projected_y)
        right_adjoint = projected_x.conj().T @ (resistance @ probe_y)
        adjoint_scale = np.sqrt(probe_x_energy[:, None] * probe_y_energy[None, :])
        self_adjoint = float(np.max(np.abs(left - right_adjoint) / adjoint_scale))

        distributional = distributional_divergence @ projected
        boundary_rows = distributional[cell_count:]
        boundary_sign_error = float(
            np.max(np.abs(boundary_rows + boundary_current))
            / max(np.max(np.abs(boundary_current)), np.finfo(float).tiny)
        )
        gates = {
            "kkt": kkt_residual < 1e-12,
            "divergence": divergence_relative < 1e-12,
            "smooth_reproduction": max(smooth_l2_reproduction, smooth_energy_reproduction) < 1e-12,
            "hermitian_energy": max(hermitian_orthogonality, pythagoras) < 1e-12,
            "projector": max(idempotence, self_adjoint) < 1e-12,
            "affine_coefficients": max(affine_reconstruction, original_affine_reproduction) < 1e-12,
            "boundary_contract": max(global_exterior_flux_relative, boundary_sign_error) < 1e-12,
            "all_faces_free": bool(
                kkt.shape[0] == face_count + cell_count
                and np.count_nonzero(boundary_current) > 0
            ),
        }
        accepted = all(gates.values())

        artifact = output / "projection.npz"
        np.savez_compressed(
            artifact,
            projected_face_currents=projected,
            lagrange_multipliers=multipliers,
            boundary_face_ids=boundary_faces,
            projected_boundary_outward_currents=boundary_current,
            projected_distributional_divergence=distributional,
            projected_current_centers=projected_centers,
            projected_current_radial=projected_radial,
            resistance_scale_ohm=np.asarray([scale]),
            source_energy_ohm=source_energy,
            projected_energy_ohm=projected_energy,
            discarded_energy_ohm=difference_energy,
        )
        metrics = {
            "face_count": face_count,
            "cell_count": cell_count,
            "boundary_face_count": int(len(boundary_faces)),
            "factor_s": factor_s,
            "solve_s": solve_s,
            "kkt_relative_residual": kkt_residual,
            "volume_divergence_relative": divergence_relative,
            "smooth_l2_reproduction": smooth_l2_reproduction,
            "smooth_energy_reproduction": smooth_energy_reproduction,
            "fine_density_projected_change_R_norm": fine_energy_change,
            "hermitian_R_orthogonality_source_scale": hermitian_orthogonality,
            "R_energy_pythagoras_relative": pythagoras,
            "random_probe_R_projector_idempotence": idempotence,
            "random_probe_R_self_adjointness": self_adjoint,
            "projected_affine_reconstruction_relative": affine_reconstruction,
            "smooth_projected_vs_original_affine_relative": original_affine_reproduction,
            "global_exterior_flux_relative": global_exterior_flux_relative,
            "distributional_boundary_sign_relative": boundary_sign_error,
            "exterior_nonzero_entries": int(np.count_nonzero(boundary_current)),
        }
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": (
                "PASS_BOUNDARY_JOINT_ALL_FACE_DIVFREE_PROJECTION"
                if accepted
                else "STOP_BOUNDARY_JOINT_ALL_FACE_DIVFREE_PROJECTION"
            ),
            "elapsed_s": monotonic() - started,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifact_sha256": digest(artifact),
            "conductivity_s_m": conductivity,
            "gates": gates,
            "metrics": metrics,
            "failure": failure,
            "scope": (
                "R-orthogonal projection of two saved fine-face currents onto cellwise D j=0 "
                "for one homogeneous-Cu joint with no impressed volume charge. All 12,546 face "
                "DOFs, including every exterior face, are free in the KKT solve. Distributional "
                "exterior flux is retained for later independent noncontact/contact charge handling; "
                "no surface charge is deleted. The projected affine RT0 coefficients are reconstructed "
                "from the projected face currents. This is a physical stress-current preparation and "
                "projector qualification, not a Green action, boundary condition, reduced default, "
                "field solution, impedance, heterogeneous-material rule, board model, or PowerSI accuracy."
            ),
        }
    except Exception:
        accepted = False
        failure = traceback.format_exc()
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "STOP_BOUNDARY_JOINT_ALL_FACE_DIVFREE_PROJECTION",
            "elapsed_s": monotonic() - started,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "failure": failure,
        }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps(result, allow_nan=False))
    return 0 if accepted else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    raise SystemExit(run(destination))
