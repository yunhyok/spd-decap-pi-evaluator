"""SPD Decap PI Evaluator v0.23.1: saved-array review of joint energy lifts."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-power-joint-energy-lifts-review-02"
PINS = {
    "tools/research/prepare_astra_power_joint_energy_lifts.py": "fc61381cd8a1d369edc805b0fbb1f36d442ced3c7479cad5034029fc171b58c2",
    "outputs/research/astra-power-joint-energy-lifts-01/result.json": "f530c60ab0e31ad8e5635a1a2aabef21682ea911030b4d759242e312bca89aad",
    "outputs/research/astra-power-joint-energy-lifts-01/energy-lifts.npz": "825aa29ad6d882e99776310eef2fa247a1e515f7f40e026599dd119f02634d6d",
    "outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz": "01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38",
}


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    observed = {name: digest(ROOT / name) for name in PINS}
    require(observed == PINS, "input pin mismatch")
    with np.load(ROOT / "outputs/research/astra-power-joint-energy-lifts-01/energy-lifts.npz", allow_pickle=False) as z:
        lift = {name: z[name] for name in z.files}
    with np.load(ROOT / "outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz", allow_pickle=False) as z:
        mass = {name: z[name] for name in z.files}

    nf, nc = map(int, mass["mass_shape"])
    require((nf, nc) == (18994, 18994), "mass shape")
    cell_count = int(mass["cell_count"][0])
    require(cell_count == 8064, "cell count")
    resistance = csr_matrix(
        (mass["resistance_data_ohm"], mass["mass_col"], mass["mass_row_ptr"]),
        shape=(nf, nf),
    )
    divergence = csr_matrix(
        (mass["volume_b_data"], mass["volume_b_col"], mass["volume_b_row_ptr"]),
        shape=tuple(mass["volume_b_shape"]),
    )

    flux = lift["face_flux_basis"]
    active = lift["active_face_ids"]
    inactive = lift["inactive_face_ids"]
    patch_ids = lift["patch_face_ids"]
    patch_rows = lift["patch_face_row"]
    targets = lift["patch_outward_flux_targets"]
    require(flux.shape == (nf, 3), "lift dimensions")
    require(np.array_equal(np.sort(np.r_[active, inactive]), np.arange(nf)), "active partition")
    require(np.intersect1d(active, inactive).size == 0, "active overlap")
    internal = np.setdiff1d(np.arange(nf), mass["boundary_face_ids"])
    require(np.array_equal(active, np.union1d(internal, patch_ids)), "active support contract")
    require(np.count_nonzero(flux[inactive]) == 0, "inactive face flux")

    patch_flux = np.zeros((4, 3))
    np.add.at(patch_flux, patch_rows, flux[patch_ids])
    divergence_value = divergence @ flux
    patch_error = float(np.max(np.abs(patch_flux - targets)))
    divergence_error = float(np.max(np.abs(divergence_value)))
    require(patch_error < 1e-12, "patch targets")
    require(divergence_error < 1e-12, "cell divergence")
    require(np.max(np.abs(targets.sum(axis=0))) == 0, "compatible target totals")

    gram = flux.T @ (resistance @ flux)
    gram_error = float(np.max(np.abs(gram - lift["local_joule_gram_ohm"])))
    eigenvalues = np.linalg.eigvalsh(gram)
    require(gram_error < 1e-18, "saved Gram")
    require(float(eigenvalues.min()) > 0, "positive lift Gram")

    multipliers = lift["lagrange_multipliers_scaled"]
    require(multipliers.shape == (cell_count + 3, 3), "multiplier dimensions")
    metric_scale = float(lift["metric_scale_ohm"])
    primal_gradient = resistance[active][:, active] @ flux[active]
    dual_gradient = metric_scale * (divergence[:, active].T @ multipliers[:cell_count])
    patch_term = np.zeros_like(primal_gradient)
    for row in range(3):
        selected = patch_ids[patch_rows == row]
        positions = np.searchsorted(active, selected)
        require(np.array_equal(active[positions], selected), "patch face missing from active set")
        patch_term[positions] += multipliers[cell_count + row]
    dual_gradient += metric_scale * patch_term
    stationarity = primal_gradient + dual_gradient
    stationarity_error = float(np.max(np.abs(stationarity)))
    stationarity_scale = float(max(np.max(np.abs(primal_gradient)), np.max(np.abs(dual_gradient))))
    stationarity_relative = stationarity_error / stationarity_scale
    require(stationarity_relative < 1e-12, "KKT stationarity")

    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": observed,
        "recomputed": {
            "fine_face_dofs": nf,
            "cell_constraints": cell_count,
            "patch_constraints": 4,
            "active_face_dofs": int(active.size),
            "inactive_face_dofs": int(inactive.size),
            "maximum_cell_integrated_divergence_a": divergence_error,
            "maximum_patch_flux_error_a": patch_error,
            "gram_reconstruction_max_error_ohm": gram_error,
            "minimum_gram_eigenvalue_ohm": float(eigenvalues.min()),
            "maximum_kkt_stationarity_ohm_a": stationarity_error,
            "relative_kkt_stationarity": stationarity_relative,
        },
        "scope": (
            "The three columns are minimum-resistance lifts within the saved active-face subspace. "
            "Zero flux on other boundary faces defines these reusable basis functions only; it is not "
            "a physical insulation condition. Fine exterior current, charge and circulation modes remain "
            "required. This review does not qualify Green coupling, impedance, the 45-chain rail, or board accuracy."
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=False)
    destination = OUTPUT / "independent-review.json"
    destination.write_text(json.dumps(review, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": review["status"], "receipt_sha256": digest(destination)}))


if __name__ == "__main__":
    main()
