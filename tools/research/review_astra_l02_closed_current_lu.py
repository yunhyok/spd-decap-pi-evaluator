"""Independent saved-array review of the L02 complete closed RT0 stream minimum."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    "producer": (ROOT / "tools/research/solve_astra_l02_saved_closed_stream.py",
                 "3cac72d30c33a5393e08ccc71b15048aad5835d97a0e71ba2cf5c063c0dce8ef"),
    "result": (R / "astra-l02-closed-current-lu-01/result.json",
               "9042844e95e228721c0ee4a3abb0f6af3aa1fbdf7fc5c5ac921b00bf76bd5d6b"),
    "minimum": (R / "astra-l02-closed-current-lu-01/l02-minimum-closed-current.npz",
                "9c14d02efcc828c7fd34c9487ba155bf77d74cd1de5e2f7dc8725b8f10690903"),
    "system": (R / "astra-l02-closed-current-01/closed-stream-system.npz",
               "8c3116dbfa400a5cab52a0f3e3b6fadb31de8c4d3ae2ab98f561a33a2521a5c8"),
    "mesh": (R / "astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz",
             "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9"),
    "space": (R / "astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz",
              "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f"),
    "loads": (R / "astra-l02-cell-gc-loads-01/l02-cell-gc-loads.npz",
              "12ab38f5d692c442a18d6ec7df913c6d96108be73850f27c7014f6f26c9c2459"),
    "old_lift": (R / "astra-l02-patch-current-01/l02-recovered-patch-current.npz",
                 "e4d8832fedd5e9eb3bd5f612fe84ec610160e7a5fe6581bf9e18fb3304d5e7d5"),
}
CONDUCTANCE = 59.59e6 * 20e-6


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sparse(z: np.lib.npyio.NpzFile, prefix: str) -> csr_matrix:
    return csr_matrix((z[prefix + "_data"], z[prefix + "_indices"], z[prefix + "_indptr"]),
                      shape=tuple(z[prefix + "_shape"]))


def rt0_action_and_energy(xy_um: np.ndarray, tri: np.ndarray, ids: np.ndarray,
                          signs: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, float]:
    """Direct centered affine RT0 mass action, independent of the saved CSR R."""
    out = np.zeros(q.size, dtype=np.complex128)
    energy = 0.0
    for begin in range(0, len(tri), 50_000):
        end = min(begin + 50_000, len(tri))
        p = xy_um[tri[begin:end]] * 1e-6
        center = p.mean(axis=1)
        twice_area = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                      - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0]))
        area = np.abs(twice_area) / 2
        assert np.all(area > 0)
        v = center[:, None, :] - p
        variance_trace = np.sum((p - center[:, None, :]) ** 2, axis=(1, 2)) / 12
        local_m = (np.einsum("nia,nja->nij", v, v) + variance_trace[:, None, None]) / (
            4 * area[:, None, None] * CONDUCTANCE)
        local_q = signs[begin:end] * q[ids[begin:end]]
        local_rq = np.einsum("nij,nj->ni", local_m, local_q)
        np.add.at(out, ids[begin:end].ravel(), (signs[begin:end] * local_rq).ravel())
        energy += float(np.real(np.vdot(local_q.reshape(-1, 3),
                                        np.einsum("nij,nj->ni", local_m, local_q))))
    return out, energy


def run(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    for name, (path, digest) in PINS.items():
        assert sha(path) == digest, name
    with np.load(PINS["mesh"][0], allow_pickle=False) as z:
        xy, triangles, contact_nodes = z["node_xy_um"], z["triangles"], z["contact_node_indices"]
    with np.load(PINS["space"][0], allow_pickle=False) as z:
        free, ids, signs = z["free_triangle_indices"], z["local_facet_branch_index"], z["local_outward_flux_sign"]
        edges, first, second, exterior = (z["branch_mesh_edges"], z["branch_first_node"],
                                          z["branch_second_node"], z["retained_exterior_branch_indices"])
        d, b = sparse(z, "d"), sparse(z, "distributional_b")
    with np.load(PINS["system"][0], allow_pickle=False) as z:
        h, rhs = sparse(z, "h"), z["rhs_a"]
        labels_saved, orientation_saved = z["mesh_node_stream_index"], z["branch_stream_orientation"]
    with np.load(PINS["old_lift"][0], allow_pickle=False) as z:
        old_q, f = z["branch_current_a"], z["cell_gc_injection_a"]
    with np.load(PINS["minimum"][0], allow_pickle=False) as z:
        q, delta, psi, gradient_saved, f_saved = (z["branch_current_a"], z["closed_current_correction_a"],
                                                    z["stream_potential"], z["complete_stream_gradient"],
                                                    z["cell_gc_injection_a"])
    result = json.loads(PINS["result"][0].read_text(encoding="utf-8"))
    ncell, ncontact, nbranch = len(free), 38856, len(edges)
    assert (ncell, nbranch, len(psi), h.shape, len(rhs)) == (1_583_840, 3_095_567, 654_955,
                                                              (654_954, 654_954), 654_954)
    assert np.array_equal(f, f_saved) and np.all(h.diagonal() > 0)

    # Actual boundary-component labels and each local RT0 orientation.
    boundary_edges = edges[exterior]
    bg = coo_matrix((np.ones(2 * len(exterior)),
                     (np.r_[boundary_edges[:, 0], boundary_edges[:, 1]],
                      np.r_[boundary_edges[:, 1], boundary_edges[:, 0]])), shape=(len(xy), len(xy))).tocsr()
    nstream, labels = connected_components(bg, directed=False)
    is_contact = np.zeros(len(xy), dtype=bool); is_contact[contact_nodes] = True
    degree = np.diff(bg.indptr)
    assert np.all(degree[is_contact] == 0) and np.all(degree[~is_contact] == 2)
    # Component integers need not have a mathematical ordering; their equality partition must agree.
    # Compare component partitions without materializing an infeasible node-by-node matrix.
    partition_pairs = np.unique(np.c_[labels, labels_saved], axis=0)
    assert len(partition_pairs) == nstream
    tri = triangles[free]
    det = ((xy[tri[:, 1], 0] - xy[tri[:, 0], 0]) * (xy[tri[:, 2], 1] - xy[tri[:, 0], 1])
           - (xy[tri[:, 1], 1] - xy[tri[:, 0], 1]) * (xy[tri[:, 2], 0] - xy[tri[:, 0], 0]))
    assert np.all(np.diff(tri, axis=1) > 0) and np.all(det != 0)
    local_orientation = signs * np.sign(det)[:, None] * np.asarray((1, -1, 1))
    counts = np.bincount(ids.ravel(), minlength=nbranch)
    orientation_sum = np.zeros(nbranch); np.add.at(orientation_sum, ids.ravel(), local_orientation.ravel())
    orientation = (orientation_sum / counts).astype(np.int8)
    assert np.all(np.abs(orientation) == 1) and np.array_equal(orientation, orientation_saved)

    branch = np.arange(nbranch)
    c_full = coo_matrix((np.r_[-orientation, orientation],
                         (np.r_[branch, branch],
                          np.r_[labels_saved[edges[:, 0]], labels_saved[edges[:, 1]]])),
                        shape=(nbranch, nstream)).tocsr()
    c_full.sum_duplicates(); c_full.eliminate_zeros()
    c = c_full[:, 1:].tocsr()
    dc_nnz = (d @ c).nnz
    bc_nnz = (b[ncell:] @ c).nnz
    assert c_full[exterior].nnz == 0 and dc_nnz == 0 and bc_nnz == 0

    # Exact graph/topological rank proof, including every insulating hole.
    retained = np.ones(nbranch, dtype=bool); retained[exterior] = False
    graph = coo_matrix((np.ones(2 * retained.sum()),
                        (np.r_[first[retained], second[retained]], np.r_[second[retained], first[retained]])),
                       shape=(ncell + ncontact, ncell + ncontact)).tocsr()
    retained_components = connected_components(graph, directed=False, return_labels=False)
    # Build the quotient edges directly from each retained branch's two stream labels.
    qe = labels_saved[edges[retained]]
    quotient = coo_matrix((np.ones(2 * len(qe)), (np.r_[qe[:, 0], qe[:, 1]], np.r_[qe[:, 1], qe[:, 0]])),
                         shape=(nstream, nstream)).tocsr()
    quotient_components = connected_components(quotient, directed=False, return_labels=False)
    nullity = int(retained.sum()) - (ncell + ncontact - 1)
    holes = nstream - len(contact_nodes)
    assert (nstream, holes, retained_components, quotient_components, nullity, c.shape[1]) == (
        654_955, 33_259, 1, 1, 654_954, 654_954)

    # Saved correction is precisely C psi and preserves every constrained row.
    delta_from_psi = orientation * (psi[labels_saved[edges[:, 1]]] - psi[labels_saved[edges[:, 0]]])
    delta_error = float(np.max(np.abs(delta - delta_from_psi)))
    q_error = float(np.max(np.abs(q - old_q - delta)))
    div_error = float(np.max(np.abs(d @ q - f)))
    boundary_change = float(np.max(np.abs((b @ delta)[ncell:])))
    assert delta_error == 0.0 and q_error < 1e-15 and div_error < 1e-12 and boundary_change < 1e-12
    assert np.all(q[exterior] == 0)

    # Independent local affine mass action proves the saved LU result, without refactorization.
    rq_old, old_energy = rt0_action_and_energy(xy, tri, ids, signs, old_q)
    rq_new, new_energy = rt0_action_and_energy(xy, tri, ids, signs, q)
    rq_delta, delta_energy = rt0_action_and_energy(xy, tri, ids, signs, delta)
    rhs_direct = -(c.T @ rq_old)
    hpsi_direct = c.T @ rq_delta
    gradient_direct = c_full.T @ rq_new
    scale = 1 / np.sqrt(h.diagonal())
    rhs_error = float(np.linalg.norm(rhs_direct - rhs) / np.linalg.norm(rhs))
    h_action_error = float(np.linalg.norm(hpsi_direct - h @ psi[1:]) / np.linalg.norm(hpsi_direct))
    equation_relative = float(np.linalg.norm(h @ psi[1:] - rhs) / np.linalg.norm(rhs))
    gradient_error = float(np.linalg.norm(gradient_direct - gradient_saved) / np.linalg.norm(rhs))
    stationarity = float(np.linalg.norm(scale * gradient_direct[1:]) / np.linalg.norm(scale * rhs))
    predicted_energy = old_energy + 2 * np.real(np.vdot(delta, rq_old)) + delta_energy
    energy_identity = float(abs(new_energy - predicted_energy) / new_energy)
    assert rhs_error < 2e-12 and h_action_error < 2e-12 and equation_relative < 2e-12
    assert gradient_error < 2e-12 and stationarity < 2e-12 and energy_identity < 2e-12

    metrics = {
        "d_c_nnz": int(dc_nnz), "boundary_c_nnz": int(bc_nnz), "c_nnz": int(c.nnz),
        "stream_count": int(nstream), "closed_dimension": int(nullity), "insulating_holes": int(holes),
        "retained_incidence_components": int(retained_components), "quotient_components": int(quotient_components),
        "delta_from_stream_max_abs_a": delta_error, "q_reconstruction_max_abs_a": q_error,
        "divergence_max_abs_a": div_error, "electrode_exterior_change_max_abs_a": boundary_change,
        "rhs_direct_relative": rhs_error, "h_stream_action_relative": h_action_error,
        "saved_equation_relative": equation_relative, "gradient_saved_relative": gradient_error,
        "direct_scaled_stationarity": stationarity, "energy_identity_relative": energy_identity,
        "old_energy_w": old_energy, "minimum_energy_w": new_energy,
    }
    receipt = {
        "program": "SPD Decap PI Evaluator", "status": "ACCEPT_WITH_SCOPE",
        "review": "independent saved-array closed-stream RT0/LU audit", "inputs": {k: {"path": str(p), "sha256": h} for k, (p, h) in PINS.items()},
        "producer_status": result["status"], "metrics": metrics,
        "scope": "Numerically stationary minimum only in the complete pinned same-mesh finite RT0 subspace with fixed cell GC and fixed electrode/exterior rows. It does not establish P1/continuum equivalence, a coupled circuit response, Green or magnetic action, mesh convergence, PowerSI accuracy, or board accuracy.",
    }
    target = output / "independent-review.json"
    target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    run(R / "astra-l02-closed-current-lu-review-04")
