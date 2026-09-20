"""Independent saved-array review of the seven-basis closed complement diagnostic."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/research/astra-seven-basis-closed-complement-01"
OUT = ROOT / "outputs/research/astra-seven-basis-closed-complement-review-01"
PINS = {
    "tools/research/diagnose_astra_seven_basis_closed_complement.py": "888a5ef1a7b56b1afabb42fdb5f3ce15b0bc2ed614e6fb001122fb54127c0520",
    "outputs/research/astra-seven-basis-closed-complement-01/result.json": "b4172c8fd177cf305c4918b6ff2d57b2987c9ba40bc4457391f8e99aac8bed75",
    "outputs/research/astra-seven-basis-closed-complement-01/closed-complement.npz": "74564971f04e60be40f916c64e91f94003fdff8600d5521afbfd968c2a6d37df",
    "outputs/research/astra-seven-basis-closed-complement-01/external-budget.json": "bf63edb21480058b7be610f6c5fbaf128709cf5ae2f0c08952a8746df2d3fbe9",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-seven-basis-full-action-01/full-actions.npz": "13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b",
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def relative(a, b=None):
    if b is None:
        b = a
    return float(np.linalg.norm(a) / max(np.linalg.norm(b), 1e-300))


def main():
    assert not OUT.exists(), OUT
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name
    receipt = json.loads((SOURCE / "result.json").read_text())
    assert receipt["failure"] is None
    with np.load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz") as z:
        R = csr_matrix((z["resistance_data_ohm"], z["mass_col"], z["mass_row_ptr"]), shape=tuple(z["mass_shape"]))
        D = csr_matrix((z["volume_b_data"], z["volume_b_col"], z["volume_b_row_ptr"]), shape=tuple(z["volume_b_shape"]))
        boundary = z["boundary_face_ids"]
    with np.load(ROOT / "outputs/research/astra-seven-basis-full-action-01/full-actions.npz") as z:
        forces = {k: z[f"{k}_full_action"] for k in ("jacobi", "xg31")}
    with np.load(SOURCE / "closed-complement.npz") as z:
        a = {k: z[k] for k in z.files}

    q = a["face_currents"]
    active = a["active_face_ids"]
    keep = a["retained_divergence_rows"]
    closed_q = a["closed_projection_of_q"]
    assert q.shape == closed_q.shape == (12546, 7)
    assert np.array_equal(active, np.setdiff1d(np.arange(q.shape[0]), boundary))
    assert np.array_equal(keep, np.arange(5303))
    incidence = D[:, active]
    components, labels = connected_components(incidence @ incidence.T, directed=False)
    assert components == 1 and np.unique(labels).size == 1
    assert np.all(np.diff(incidence.tocsc().indptr) == 2)
    assert np.max(np.abs(np.asarray(incidence.sum(axis=0)))) == 0
    dropped_q_relative = relative(D[[5303]] @ closed_q, closed_q)
    assert dropped_q_relative < 1e-10

    Ri = R[active][:, active]
    scale = float(np.median(Ri.diagonal()))
    A = D[keep][:, active]
    def top_residual(x, dual, force):
        return (Ri @ x[active]) / scale + A.T @ dual - force[active] / scale
    def div_residual(x):
        return D @ x
    q_top = top_residual(closed_q, a["closed_q_dual"], R @ q)
    q_kkt = relative(q_top, (R @ q)[active] / scale)
    q_div = relative(div_residual(closed_q), closed_q)
    q_boundary = float(np.max(np.abs(closed_q[boundary])))
    gram = q.T @ (R @ closed_q)
    gram_saved_err = relative(gram - a["closed_q_gram"], gram)
    symmetric_err = relative(gram - gram.T, gram)
    vals, vecs = np.linalg.eigh((gram + gram.T) / 2)
    mask = vals > 1e-10
    assert np.array_equal(mask, a["closed_q_rank_mask"]) and mask.sum() == 7
    assert q_kkt < 1e-10 and q_div < 1e-10 and q_boundary == 0.0
    assert gram_saved_err < 1e-12 and symmetric_err < 1e-10

    metrics = {"q_kkt_relative": q_kkt, "q_divergence_relative": q_div,
               "q_boundary_max": q_boundary, "q_gram_saved_relative": gram_saved_err,
               "q_gram_symmetry_relative": symmetric_err, "q_eigenvalues": vals.tolist(),
               "internal_components": int(components), "dropped_divergence_row": 5303,
               "dropped_q_divergence_relative": dropped_q_relative}
    for name, force in forces.items():
        closed = a[f"{name}_closed_force"]
        dual = a[f"{name}_force_dual"]
        coef = a[f"{name}_projection_coefficients"]
        witness = a[f"{name}_witness"]
        top = top_residual(closed, dual, force)
        kkt = relative(top, force[active] / scale)
        div = relative(div_residual(closed), closed)
        boundary_max = float(np.max(np.abs(closed[boundary])))
        projection = closed_q @ coef
        decomposition = relative(closed - projection - witness, closed)
        qorth = float(np.linalg.norm(q.T @ (R @ witness)) / max(np.sqrt(np.trace(witness.T @ (R @ witness))), 1e-300))
        witness_div = relative(div_residual(witness), witness)
        witness_boundary = float(np.max(np.abs(witness[boundary])))
        witness_gram = witness.T @ (R @ witness)
        coupling = witness.T @ force
        identity = relative(coupling - witness_gram, witness_gram)
        closed_energy = closed.T @ (R @ closed)
        pythagorean = relative(closed_energy - (coef.T @ gram @ coef + witness_gram), closed_energy)
        riesz = relative(force.T @ closed - closed_energy, closed_energy)
        spec, directions = np.linalg.eigh((witness_gram + witness_gram.T) / 2)
        normalized = witness @ (directions / np.sqrt(spec))
        normalized_err = relative(normalized.T @ (R @ normalized) - np.eye(7), np.eye(7))
        saved_gram = relative(witness_gram - a[f"{name}_witness_gram"], witness_gram)
        saved_coupling = relative(coupling - a[f"{name}_witness_coupling"], coupling)
        saved_eigs = float(np.max(np.abs(spec - a[f"{name}_witness_eigenvalues"])))
        checks_max = max(kkt, div, decomposition, qorth, witness_div, identity, pythagorean, riesz, normalized_err, saved_gram, saved_coupling)
        assert checks_max < 1e-9, (name, kkt, div, decomposition, qorth, witness_div, identity, pythagorean, riesz, normalized_err, saved_gram, saved_coupling)
        assert boundary_max == 0.0 and witness_boundary == 0.0 and spec.min() > 0 and saved_eigs < 1e-20
        metrics[name] = dict(kkt_relative=kkt, closed_divergence_relative=div,
                             closed_boundary_max=boundary_max, decomposition_relative=decomposition,
                             r_orthogonality_relative=qorth, witness_divergence_relative=witness_div,
                             witness_boundary_max=witness_boundary, energy_identity_relative=identity,
                             pythagorean_relative=pythagorean, riesz_energy_relative=riesz,
                             normalized_orthonormality_relative=normalized_err,
                             witness_gram_saved_relative=saved_gram, coupling_saved_relative=saved_coupling,
                             witness_eigenvalues=spec.tolist(), witness_eigenvalue_saved_max=saved_eigs)
    delta = a["xg31_witness"] - a["jacobi_witness"]
    delta_err = relative(delta - a["witness_difference"], delta)
    delta_gram = delta.T @ (R @ delta)
    delta_gram_err = relative(delta_gram - a["witness_difference_gram"], delta_gram)
    base = a["xg31_witness_gram"]
    L = np.linalg.cholesky((base + base.T) / 2)
    witness_difference = float(np.sqrt(np.linalg.norm(np.linalg.solve(L, delta_gram) @ np.linalg.inv(L.T), 2)))
    assert delta_err == 0.0 and delta_gram_err < 1e-12
    metrics.update(witness_difference_saved_relative=delta_err,
                   witness_difference_gram_saved_relative=delta_gram_err,
                   relative_worst_combination_two_rule_witness_difference=witness_difference)
    OUT.mkdir(parents=True)
    report = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="ACCEPT_WITH_SCOPE",
                  reviewer="independent saved sparse/KKT arithmetic; producer is not imported",
                  pins=PINS, source_status=receipt["status"], metrics=metrics,
                  scope="Accepts only saved closed-complement algebra: a zero-exterior, divergence-free omitted-test subspace and its static Riesz witnesses. The 0.0051558273598471685 Jacobi/XG31 witness difference is retained. It measures omitted closed-force sensitivity, not an AC current, insulation condition, scalar/contact/exterior/material/return effect, field, port, or board error.")
    (OUT / "independent-review.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
