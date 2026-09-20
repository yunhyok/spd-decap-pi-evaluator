"""SPD Decap PI Evaluator v0.23.1: strict saved review of boundary seed modes."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-power-joint-boundary-seed-modes-review-03"
PINS = {
    "tools/research/qualify_astra_power_joint_boundary_seed_modes.py": "34d18684fa24f741db9bba313e0dfc9580fb83c9afafced1e8723337ce22ac05",
    "outputs/research/astra-power-joint-boundary-seed-modes-02/result.json": "95a690b270eda45beea27a66a3f8575586f518793f824f7b7f5c4e586bf4484d",
    "outputs/research/astra-power-joint-boundary-seed-modes-02/boundary-seed-modes.npz": "3bc4a092141df388d7ad15f53d85f015703da3f58ed68065357ef86147ad587b",
    "outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz": "01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38",
    "outputs/research/astra-conforming-power-joint-01/joint-template.npz": "6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c",
}


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def analytic_fields(points, center, length):
    u = (points - center) / length
    zeros = np.zeros(len(points))
    fields = [
        np.column_stack((np.ones(len(points)), zeros, zeros)),
        np.column_stack((zeros, np.ones(len(points)), zeros)),
        np.column_stack((zeros, zeros, np.ones(len(points)))),
        np.column_stack((u[:, 0], -u[:, 1], zeros)),
        np.column_stack((u[:, 0], zeros, -u[:, 2])),
        np.column_stack((u[:, 1], u[:, 0], zeros)),
        np.column_stack((u[:, 2], zeros, u[:, 0])),
        np.column_stack((zeros, u[:, 2], u[:, 1])),
    ]
    fields.extend(np.cross(np.broadcast_to(axis, u.shape), u) for axis in np.eye(3))
    return np.stack(fields, axis=2)


def main():
    observed = {name: digest(ROOT / name) for name in PINS}
    require(observed == PINS, "input pin mismatch")
    with np.load(ROOT / "outputs/research/astra-power-joint-boundary-seed-modes-02/boundary-seed-modes.npz", allow_pickle=False) as z:
        seed = {name: z[name] for name in z.files}
    with np.load(ROOT / "outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz", allow_pickle=False) as z:
        mass = {name: z[name] for name in z.files}
    with np.load(ROOT / "outputs/research/astra-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as z:
        joint = {name: z[name] for name in z.files}

    nf = int(mass["face_count"][0])
    nc = int(mass["cell_count"][0])
    require((nf, nc) == (18994, 8064), "joint dimensions")
    resistance = csr_matrix(
        (mass["resistance_data_ohm"], mass["mass_col"], mass["mass_row_ptr"]),
        shape=tuple(mass["mass_shape"]),
    )
    divergence = csr_matrix(
        (mass["volume_b_data"], mass["volume_b_col"], mass["volume_b_row_ptr"]),
        shape=tuple(mass["volume_b_shape"]),
    )
    boundary = seed["boundary_face_ids"]
    interior = seed["interior_face_ids"]
    require(np.array_equal(boundary, mass["boundary_face_ids"]), "boundary identity")
    require(np.array_equal(np.sort(np.r_[boundary, interior]), np.arange(nf)), "face partition")

    vertices = joint["vertices_local_um"] * 1e-6
    centroids = vertices[joint["face_vertices"]].mean(axis=1)
    area_vectors = joint["face_area_vector_um2"] * 1e-12
    values = analytic_fields(
        centroids,
        seed["center_m"],
        float(seed["normalization_length_m"][0]),
    )
    direct_flux = np.einsum("fi,fim->fm", area_vectors, values)
    boundary_error = float(np.max(np.abs(direct_flux[boundary] - seed["direct_boundary_flux"])))
    compatibility = float(np.max(np.abs(seed["direct_boundary_flux"].sum(axis=0))))
    require(boundary_error < 1e-21, "analytic boundary flux")
    require(compatibility < 1e-20, "global boundary compatibility")

    extension = seed["minimum_M_extensions"]
    loops = seed["rotational_zero_boundary_loops"]
    modes = seed["face_flux_seed_modes"]
    require(extension.shape == (nf, 11), "extension dimensions")
    require(loops.shape == (nf, 3), "loop dimensions")
    require(np.array_equal(modes[:, :8], extension[:, :8]), "gradient mode ordering")
    require(np.max(np.abs(extension[boundary] - seed["direct_boundary_flux"])) == 0, "fixed boundary")
    loop_identity = float(np.max(np.abs(loops - (direct_flux[:, 8:] - extension[:, 8:]))))
    require(loop_identity < 1e-20, "rotation residual identity")
    require(np.array_equal(modes[:, 8:], loops), "rotation mode ordering")

    extension_divergence = divergence @ extension
    loop_divergence = divergence @ loops
    full_divergence = float(np.max(np.abs(extension_divergence)))
    dropped_row = float(np.max(np.abs(extension_divergence[-1])))
    loop_divergence_error = float(np.max(np.abs(loop_divergence)))
    loop_boundary_error = float(np.max(np.abs(loops[boundary])))
    require(full_divergence < 1e-20, "full extension divergence")
    require(loop_divergence_error < 1e-20, "loop divergence")
    require(loop_boundary_error < 1e-20, "loop boundary")

    metric = resistance[interior][:, interior]
    multipliers = seed["lagrange_multipliers_scaled"]
    require(multipliers.shape == (nc - 1, 11), "multiplier dimensions")
    scale = float(seed["metric_scale_ohm"][0])
    primal = resistance[interior] @ extension
    dual = scale * (divergence[:-1, interior].T @ multipliers)
    stationarity = primal + dual
    stationarity_absolute = float(np.max(np.abs(stationarity)))
    stationarity_scale = float(max(np.max(np.abs(primal)), np.max(np.abs(dual))))
    stationarity_relative = stationarity_absolute / stationarity_scale
    constraint_residual = divergence[:-1, interior] @ extension[interior]
    constraint_residual += divergence[:-1, boundary] @ seed["direct_boundary_flux"]
    constraint_error = float(np.max(np.abs(constraint_residual)))
    require(stationarity_relative < 1e-12, "KKT stationarity")
    require(constraint_error < 1e-20, "retained KKT constraints")

    gram = modes.T @ (resistance @ modes)
    gram_error = float(np.max(np.abs(gram - seed["energy_gram_ohm"])))
    eigenvalues = np.linalg.eigvalsh(gram)
    rank = int(np.linalg.matrix_rank(gram, tol=float(eigenvalues.max()) * 1e-11))
    rotation_extension = extension[:, 8:]
    rotation_projection = rotation_extension.T @ (resistance @ loops)
    rotation_projection_error = float(np.max(np.abs(rotation_projection)))
    rotation_extension_energy = np.diag(rotation_extension.T @ (resistance @ rotation_extension))
    rotation_loop_energy = np.diag(loops.T @ (resistance @ loops))
    rotation_projection_relative = float(
        np.max(np.abs(rotation_projection) / np.sqrt(rotation_extension_energy[:, None] * rotation_loop_energy[None, :]))
    )
    require(gram_error < 1e-30, "saved energy Gram")
    require(rank == 11 and float(eigenvalues.min()) > 0, "seed rank")
    require(rotation_projection_relative < 1e-10, "minimum extension projection")

    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": observed,
        "recomputed": {
            "seed_count": 11,
            "boundary_face_count": int(boundary.size),
            "analytic_boundary_flux_max_error_a": boundary_error,
            "global_boundary_compatibility_max_a": compatibility,
            "rotation_residual_identity_max_error_a": loop_identity,
            "maximum_full_extension_divergence_a": full_divergence,
            "dropped_cell_divergence_a": dropped_row,
            "maximum_rotation_loop_divergence_a": loop_divergence_error,
            "maximum_rotation_loop_boundary_flux_a": loop_boundary_error,
            "retained_constraint_max_error_a": constraint_error,
            "relative_kkt_stationarity": stationarity_relative,
            "rotation_extension_projection_max_ohm": rotation_projection_error,
            "rotation_extension_projection_relative": rotation_projection_relative,
            "energy_gram_max_error_ohm": gram_error,
            "energy_rank": rank,
            "minimum_energy_gram_eigenvalue_ohm": float(eigenvalues.min()),
        },
        "scope": (
            "These eleven columns are candidate seeds on one frozen conforming joint. The eight gradient "
            "extensions retain their prescribed exterior flux; only the three rotational residual columns "
            "have zero exterior flux. Neither property imposes a physical boundary condition on the later "
            "fine space. Fine exterior current, charge and additional circulation modes remain required. "
            "No Green operator, impedance, modal convergence, rail closure, or board accuracy is qualified."
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=False)
    destination = OUTPUT / "independent-review.json"
    destination.write_text(json.dumps(review, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": review["status"], "receipt_sha256": digest(destination)}))


if __name__ == "__main__":
    main()
