"""SPD Decap PI Evaluator v0.23.1: strict saved review of interface lifts."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-shared-interface-interior-lifts-review-03"
PINS = {
    "outputs/research/astra-shared-interface-interior-lifts-04/driver-at-run.py":
        "e8af1206d19b9f4cbbbe8c8c63a7cb9f486493316f641f443ea3a21a6e9ce44d",
    "outputs/research/astra-shared-interface-interior-lifts-04/result.json":
        "2df58fa459a0e016916b8c6f3e23c8c44cedd58d191b69111a2ad6688e131eb8",
    "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz":
        "9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92",
    "outputs/research/astra-shared-interface-flux-modes-01/shared-flux-modes.npz":
        "73bfbd26d75a98e137f12e01f3fdd765cfbbfcd4b1393c91cf7a623b93da22c0",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def load_npz(path: str) -> dict[str, np.ndarray]:
    with np.load(ROOT / path, allow_pickle=False) as source:
        return {key: source[key] for key in source.files}


def cell_components(cell_count: int, pairs: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
    graph: list[list[int]] = [[] for _ in range(cell_count)]
    for left, right in pairs:
        graph[int(left)].append(int(right))
        graph[int(right)].append(int(left))
    labels = np.full(cell_count, -1, dtype=np.int64)
    groups: list[np.ndarray] = []
    for seed in range(cell_count):
        if labels[seed] >= 0:
            continue
        pending = [seed]
        labels[seed] = len(groups)
        group: list[int] = []
        while pending:
            cell = pending.pop()
            group.append(cell)
            for other in graph[cell]:
                if labels[other] < 0:
                    labels[other] = labels[seed]
                    pending.append(other)
        groups.append(np.asarray(sorted(group), dtype=np.int64))
    return labels, groups


def normalized_stationarity(primal: np.ndarray, dual: np.ndarray) -> float:
    return float(
        np.linalg.norm(primal + dual)
        / max(np.linalg.norm(primal), np.linalg.norm(dual), np.finfo(float).tiny)
    )


def main() -> None:
    actual_pins = {path: digest(ROOT / path) for path in PINS}
    assert actual_pins == PINS
    lift = load_npz("outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz")
    trace = load_npz("outputs/research/astra-shared-interface-flux-modes-01/shared-flux-modes.npz")
    mesh = load_npz("outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz")
    space = load_npz("outputs/research/astra-boundary-joint-current-space-01/current-space.npz")

    resistance = csr_matrix(
        (space["resistance_data_ohm"], space["mass_col"], space["mass_row_ptr"]),
        shape=tuple(space["mass_shape"]),
    )
    divergence = csr_matrix(
        (space["volume_b_data"], space["volume_b_col"], space["volume_b_row_ptr"]),
        shape=tuple(space["volume_b_shape"]),
    )
    basis = lift["face_flux_basis"]
    fixed = lift["template_interface_face_ids"]
    fixed_flux = trace["shared_integrated_flux_modes"]
    assert basis.shape == (12546, 5) and fixed_flux.shape == (32, 5)
    interface_error = float(np.max(np.abs(basis[fixed] - fixed_flux)))

    # Reconstruct the cell graph after the fixed interface is removed.
    internal = mesh["internal_face_ids"].astype(np.int64)
    pairs = mesh["internal_owner_cells"].astype(np.int64)
    free_mask = ~np.isin(internal, fixed)
    labels, groups = cell_components(len(mesh["cells"]), pairs[free_mask])
    component_sizes = np.asarray([len(group) for group in groups], dtype=np.int64)
    independently_dropped = np.asarray([group[0] for group in groups], dtype=np.int64)
    assert np.array_equal(component_sizes, lift["components"])
    assert np.array_equal(independently_dropped, lift["dropped_divergence_rows"])
    keep = np.setdiff1d(np.arange(len(mesh["cells"])), independently_dropped)
    assert np.array_equal(keep, lift["kept_divergence_rows"])

    # Map each shared global face to its two local outward RT0 coefficients.
    columns = space["local_rt0_face_columns"].astype(np.int64)
    signs = space["local_rt0_face_signs"].astype(np.int8)
    body = mesh["cell_body"].astype(np.int8)
    post_trace = np.empty_like(fixed_flux)
    bridge_trace = np.empty_like(fixed_flux)
    interface_owner_cells = np.empty((len(fixed), 2), dtype=np.int64)
    interface_owner_signs = np.empty((len(fixed), 2), dtype=np.int8)
    for row, face in enumerate(fixed):
        local = np.argwhere(columns == face)
        assert local.shape == (2, 2)
        owners = [(int(body[cell]), int(cell), int(slot), int(signs[cell, slot])) for cell, slot in local]
        assert {owner[0] for owner in owners} == {0, 2}
        post_owner = next(owner for owner in owners if owner[0] == 0)
        bridge_owner = next(owner for owner in owners if owner[0] == 2)
        post_trace[row] = post_owner[3] * basis[face]
        bridge_trace[row] = bridge_owner[3] * basis[face]
        interface_owner_cells[row] = [post_owner[1], bridge_owner[1]]
        interface_owner_signs[row] = [post_owner[3], bridge_owner[3]]
    post_trace_error = float(np.max(np.abs(post_trace - trace["post_local_outward_flux"])))
    bridge_trace_error = float(np.max(np.abs(bridge_trace - trace["bridge_local_outward_flux"])))
    interface_jump = float(np.max(np.abs(post_trace + bridge_trace)))

    # Exterior support and the explicit ideal-fixture balance for the constant column.
    boundary = space["boundary_face_ids"].astype(np.int64)
    left = lift["electrode_left_ids"].astype(np.int64)
    right = lift["electrode_right_ids"].astype(np.int64)
    other_boundary = np.setdiff1d(boundary, np.r_[left, right])
    zero_mode_exterior = float(np.max(np.abs(basis[boundary, 1:])))
    constant_other_exterior = float(np.max(np.abs(basis[other_boundary, 0])))
    terminal_totals = np.array([basis[left, 0].sum(), basis[right, 0].sum()])
    terminal_error = float(np.max(np.abs(terminal_totals - np.array([-1.0, 1.0]))))
    total_exterior = float(abs(basis[boundary, 0].sum()))
    first_owner = mesh["first_owner_cell"].astype(np.int64)
    assert np.all(body[first_owner[left]] == 0) and np.all(body[first_owner[right]] == 1)
    assert np.unique(labels[first_owner[left]]).size == np.unique(labels[first_owner[right]]).size == 1
    assert labels[first_owner[left[0]]] != labels[first_owner[right[0]]]

    complete_divergence = divergence @ basis
    max_divergence = float(np.max(np.abs(complete_divergence)))
    dropped_divergence = float(np.max(np.abs(complete_divergence[independently_dropped])))

    # Reconstruct both KKT stationarity equations from the saved primal basis,
    # sparse R/D and saved duals. R[A]@J includes the fixed R_ib contribution.
    active_zero = lift["active_zero"].astype(np.int64)
    scale_zero = float(lift["scale_zero"][0])
    zero_primal = resistance[active_zero] @ basis[:, 1:] / scale_zero
    zero_dual = divergence[keep][:, active_zero].T @ lift["dual_zero"]
    zero_stationarity = normalized_stationarity(zero_primal, zero_dual)
    zero_constraint = float(np.max(np.abs(divergence[keep] @ basis[:, 1:])))

    active_constant = lift["active_constant"].astype(np.int64)
    electrode_columns = np.r_[left, right]
    electrode_rows = np.r_[np.zeros(len(left), dtype=np.int64), np.ones(len(right), dtype=np.int64)]
    electrode_local_columns = np.searchsorted(active_constant, electrode_columns)
    assert np.array_equal(active_constant[electrode_local_columns], electrode_columns)
    electrode = coo_matrix(
        (np.ones(len(electrode_columns)), (electrode_rows, electrode_local_columns)),
        shape=(2, len(active_constant)),
    ).tocsr()
    scale_constant = float(lift["scale_constant"][0])
    constant_primal = resistance[active_constant] @ basis[:, :1] / scale_constant
    constant_dual = (
        divergence[keep][:, active_constant].T @ lift["dual_div_constant"]
        + electrode.T @ lift["dual_patch_constant"]
    )
    constant_stationarity = normalized_stationarity(constant_primal, constant_dual)
    constant_div_constraint = float(np.max(np.abs(divergence[keep] @ basis[:, :1])))
    constant_patch_constraint = float(
        np.max(np.abs(electrode @ basis[active_constant, :1] - lift["electrode_targets_a"]))
    )

    # Independent degree-2 tetra rule for a deterministic mixed basis current.
    vertices = mesh["vertices_local_um"] * 1e-6
    tetrahedra = vertices[mesh["cells"]]
    volumes = mesh["cell_volume_um3"] * 1e-18
    coefficients = np.array([1.0, 0.25, -0.5, 0.75, -0.3])
    face_current = basis @ coefficients
    local_flux = signs * face_current[columns]
    a = (5 + 3 * np.sqrt(5)) / 20
    b = (5 - np.sqrt(5)) / 20
    barycentric = np.full((4, 4), b)
    np.fill_diagonal(barycentric, a)
    points = np.einsum("qi,cid->cqd", barycentric, tetrahedra)
    rt0 = (points[:, :, None, :] - tetrahedra[:, None, :, :]) / (3 * volumes[:, None, None, None])
    current = np.einsum("ci,cqid->cqd", local_flux, rt0)
    quadrature_joule = float(np.sum(volumes[:, None] * np.sum(current * current, axis=2) / 4) / 59.59e6)
    sparse_joule = float(face_current @ (resistance @ face_current))
    quadrature_energy_error = abs(quadrature_joule - sparse_joule) / sparse_joule

    gram = basis.T @ (resistance @ basis)
    gram_error = float(np.max(np.abs(gram - lift["energy_gram_ohm"])))
    diagonal_scale = 1 / np.sqrt(np.diag(gram))
    normalized_gram = diagonal_scale[:, None] * gram * diagonal_scale[None]
    normalized_eigenvalues = np.linalg.eigvalsh((normalized_gram + normalized_gram.T) / 2)
    gram_symmetry = float(np.max(np.abs(gram - gram.T)))

    metrics = {
        "component_sizes": component_sizes.tolist(),
        "dropped_divergence_rows": independently_dropped.tolist(),
        "interface_fixed_flux_max_abs": interface_error,
        "post_trace_max_abs": post_trace_error,
        "bridge_trace_max_abs": bridge_trace_error,
        "interface_owner_jump_max_abs": interface_jump,
        "zero_mode_exterior_max_abs_a": zero_mode_exterior,
        "constant_other_exterior_max_abs_a": constant_other_exterior,
        "constant_terminal_totals_a": terminal_totals.tolist(),
        "constant_terminal_max_abs_error_a": terminal_error,
        "constant_total_exterior_abs_a": total_exterior,
        "complete_divergence_max_abs_a": max_divergence,
        "dropped_row_divergence_max_abs_a": dropped_divergence,
        "zero_full_Rib_stationarity_relative": zero_stationarity,
        "zero_kept_divergence_max_abs_a": zero_constraint,
        "constant_full_Rib_stationarity_relative": constant_stationarity,
        "constant_kept_divergence_max_abs_a": constant_div_constraint,
        "constant_patch_constraint_max_abs_a": constant_patch_constraint,
        "saved_gram_max_abs_error_ohm": gram_error,
        "gram_symmetry_max_abs_ohm": gram_symmetry,
        "normalized_gram_eigenvalues": normalized_eigenvalues.tolist(),
        "independent_degree2_joule_relative_error": quadrature_energy_error,
        "independent_sparse_joule_ohm": sparse_joule,
    }
    assert len(groups) == 2 and np.array_equal(component_sizes, [2604, 2700])
    assert interface_error == post_trace_error == bridge_trace_error == interface_jump == 0
    assert zero_mode_exterior == constant_other_exterior == 0
    assert terminal_error < 1e-14 and total_exterior < 1e-14
    assert max_divergence < 2e-14 and dropped_divergence < 2e-14
    assert zero_stationarity < 2e-13 and constant_stationarity < 2e-13
    assert zero_constraint < 2e-14 and constant_div_constraint < 2e-14
    assert constant_patch_constraint < 1e-14
    assert gram_error < 2e-18 and gram_symmetry < 2e-18
    assert normalized_eigenvalues.min() > 1e-3
    assert quadrature_energy_error < 2e-13

    OUTPUT.mkdir(parents=True, exist_ok=False)
    artifact = OUTPUT / "strict-review-arrays.npz"
    np.savez_compressed(
        artifact,
        interface_owner_cells=interface_owner_cells,
        interface_owner_signs=interface_owner_signs,
        independently_reconstructed_cell_component=labels,
        post_outward_flux=post_trace,
        bridge_outward_flux=bridge_trace,
        reconstructed_energy_gram_ohm=gram,
        normalized_energy_gram=normalized_gram,
    )
    receipt = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SHARED_INTERFACE_MINIMUM_R_LIFTS_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": actual_pins,
        "artifact_sha256": digest(artifact),
        "basis_shape": list(basis.shape),
        "trace_mode_count": int(basis.shape[1]),
        "constant_balance_contract": {
            "interface_net_outward_from_body0_a": float(fixed_flux[:, 0].sum()),
            "left_declared_top_fixture_a": float(terminal_totals[0]),
            "right_declared_top_fixture_a": float(terminal_totals[1]),
            "other_exterior_flux_a": constant_other_exterior,
        },
        "metrics": metrics,
        "superseded_reviews": {
            "review01": "limited D/Gram readback",
            "review02": "reviewer wrong-column P1 was corrected, but receipt omitted KKT, owner-sign, component and local-energy reconstruction",
        },
        "scope": (
            "Five minimum-R basis columns on one actual two-post plus bridge mesh. The constant trace uses "
            "the declared whole-top ideal-fixture electrodes for explicit -1/+1 A balance. Zero exterior "
            "flux in the other four columns is a basis-function definition only; all complementary fine "
            "current, exterior current, independent charge and circulation spaces remain available. This "
            "review approves saved sparse R/D lifting algebra, not Green coupling, field convergence, "
            "contact equivalence, replicated chains, impedance, board physics or PowerSI accuracy."
        ),
    }
    receipt_path = OUTPUT / "independent-review.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "reviewer_sha256": receipt["reviewer_sha256"],
        "receipt_sha256": digest(receipt_path),
        "metrics": metrics,
    }))


if __name__ == "__main__":
    main()
