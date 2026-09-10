"""Disabled exact conditional-L04 hybrid trace assembly and saved-field replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from pathlib import Path

import numpy as np
from scipy import sparse

import assemble_astra_l25_rt0_resistance as local
import reconstruct_astra_native_loaded_field as recon


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
NCELL, NINTERNAL, NRIM, NCONTACT = 1_589_827, 1_660_526, 612_448, 38_278
NTRACE, GAUGE_CONTACT = NINTERNAL + NCONTACT, 25_440
PINS = {
    "static_cells_result": (R / "astra-l04-exact-hybrid-cells-01/result.json", "fddf240b045663dce7ce61f2bc258976f4499b8e5b1b4d20d5b8e81c0cfa95b9"),
    "static_cells_artifact": (R / "astra-l04-exact-hybrid-cells-01/l04-exact-hybrid-cells.npz", "8f51d808397cf5ba5764e0df51b41e44ed9af82924dc1a766b515524279cb5c0"),
    "static_cells_driver": (R / "astra-l04-exact-hybrid-cells-01/driver-at-run.py", "cd925c921034fa84762ca887f02a05faa01a0fa2c0576b446a8217de4c332d39"),
    "stream_result": (R / "astra-l04-fixed-contact-stream-01/result.json", "3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1"),
    "stream_driver": (R / "astra-l04-fixed-contact-stream-01/driver-at-run.py", "65c48fe18db408a9c38ee28a86b50f57c470e3286b7dab975ca0a77f1c6b8c27"),
    "stream_external": (R / "astra-l04-fixed-contact-stream-01/external-budget.json", "016bd3039ada75e8475a23ab328b2b0f135f641235bf5c9088e62636c0d04efc"),
    "stream_field": (R / "astra-l04-fixed-contact-stream-01/l04-fixed-contact-current.npz", "dec07e1681c8400c43b6c79ab7a25ef37f7c19443441ad369016f6daefbfe9fd"),
    "stream_space": (R / "astra-l04-fixed-contact-stream-01/l04-fixed-contact-rt0-space.npz", "5d31b3c6183eb4f43723eb80d1a545953a42fb2c9320be425d3ebc034cb51bb6"),
    "stream_system": (R / "astra-l04-fixed-contact-stream-01/l04-fixed-contact-stream-system.npz", "b299d67995b58b7fa698b080380cda495032472d0f426ed2dd9e4b78542fb34a"),
    "saved_dual_result": (R / "astra-l04-saved-contact-potentials-01/result.json", "97e36e08295f2d8eeeb15c508598842d552ee6f7c2601fdac51d8ba5aa6eb6b4"),
    "saved_dual_external": (R / "astra-l04-saved-contact-potentials-01/external-budget.json", "60676fd4b5f5d937f0668d21dae39d9e78f6f9d22338ce02dae9dc039a1ecb41"),
    "saved_dual_artifact": (R / "astra-l04-saved-contact-potentials-01/l04-saved-contact-dual-potentials.npz", "d638dcf046a22709111bceea53f720477430c78d46ed2f79d8dc1b60b52f0fd2"),
    "saved_dual_driver": (R / "astra-l04-saved-contact-potentials-01/driver-at-run.py", "e3b0ff15c28b2ecd9f132e311a843225cd9ac6834915c63cc61ef84df57c2c39"),
    "mesh": (R / "astra-l04-conditional-sheet-mesh-02/l04-conditional-sheet-mesh-before-stiffness.npz", "6f2f396fe2319d60ad4b1586fd7043960e42f1d85c29f28a1d9c082302a3a211"),
    "local_rt0": (Path(local.__file__), "ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010"),
    "budget": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "conditional_scatter_pattern": (ROOT / "tools/research/assemble_astra_l02_conditional_hybrid_operator.py", "79ab74ff53bef21347038a8ab0c243260049cd97dfea5e707320f84de19210d3"),
    "tree_potential_helper": (ROOT / "tools/research/recover_astra_l04_saved_contact_potentials.py", "e3b0ff15c28b2ecd9f132e311a843225cd9ac6834915c63cc61ef84df57c2c39"),
}


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def csc_parts(matrix: sparse.csc_matrix, prefix: str) -> dict:
    matrix = matrix.tocsc(); matrix.sum_duplicates(); matrix.eliminate_zeros(); matrix.sort_indices()
    return {prefix + "_data": matrix.data, prefix + "_indices": matrix.indices, prefix + "_indptr": matrix.indptr, prefix + "_shape": np.array(matrix.shape, dtype=np.int64)}


def trace_index(facets: np.ndarray, signs: np.ndarray, first: np.ndarray, second: np.ndarray, ncell: int, ncontact: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Map each active local facet to its internal or rim contact trace row."""
    active = facets >= 0
    require(np.all(facets[~active] == -1) and np.all(signs[~active] == 0), "inactive local facets")
    require(np.all(np.isin(signs[active], [-1, 1])) and np.all((0 <= facets[active]) & (facets[active] < len(first))), "active facet range/sign")
    degree = np.bincount(facets[active], minlength=len(first))
    require(np.all((degree == 1) | (degree == 2)), "branch occurrence degree")
    internal = degree == 2; rim = degree == 1
    require(np.all((first[internal] < ncell) & (second[internal] < ncell)), "internal endpoints")
    require(np.all((first[rim] < ncell) ^ (second[rim] < ncell)) and not np.any((first >= ncell) & (second >= ncell)), "rim endpoints")
    branch_trace = np.full(len(first), -1, dtype=np.int64)
    branch_trace[internal] = np.arange(np.count_nonzero(internal), dtype=np.int64)
    rim_contact_node = np.where(first[rim] >= ncell, first[rim], second[rim]) - ncell
    require(np.all((0 <= rim_contact_node) & (rim_contact_node < ncontact)) and len(np.unique(rim_contact_node)) == ncontact, "rim/contact identity")
    branch_trace[rim] = np.count_nonzero(internal) + rim_contact_node
    local_trace = np.full(facets.shape, -1, dtype=np.int64); local_trace[active] = branch_trace[facets[active]]
    cell = np.broadcast_to(np.arange(len(facets), dtype=np.int64)[:, None], facets.shape)[active]
    endpoint = np.where(signs[active] > 0, first[facets[active]], second[facets[active]])
    require(np.array_equal(cell, endpoint), "saved active sign-incidence")
    return local_trace, branch_trace, degree


def assemble_trace_h(local_trace: np.ndarray, active: np.ndarray, upper: np.ndarray, ii: np.ndarray, jj: np.ndarray, ntrace: int) -> tuple[sparse.csc_matrix, np.ndarray, np.ndarray, np.ndarray]:
    h = np.zeros((len(local_trace), 3, 3), dtype=np.float64)
    h[:, ii, jj] = upper; h[:, jj, ii] = upper
    rows, cols, values = [], [], []
    expected = int(np.sum(active.sum(axis=1) ** 2))
    for a in range(3):
        for b in range(3):
            mask = active[:, a] & active[:, b]
            rows.append(local_trace[mask, a]); cols.append(local_trace[mask, b]); values.append(h[mask, a, b])
    row, col, data = np.concatenate(rows), np.concatenate(cols), np.concatenate(values)
    require(len(data) == expected, "active local COO entry count")
    matrix = sparse.coo_matrix((data, (row, col)), shape=(ntrace, ntrace)).tocsc()
    matrix.sum_duplicates(); matrix.eliminate_zeros(); matrix.sort_indices()
    local_row_sum = h.sum(axis=2)
    row_expected, row_operand = np.zeros(ntrace), np.zeros(ntrace)
    np.add.at(row_expected, local_trace[active], local_row_sum[active])
    np.add.at(row_operand, local_trace[active], np.broadcast_to(abs(h).sum(axis=2), active.shape)[active])
    row_operations = np.bincount(row, minlength=ntrace)  # All active COO terms, including stored zeros.
    return matrix, row_expected, row_operand, row_operations


def topology_components(local_trace: np.ndarray, ntrace: int) -> int:
    """Exact local k>=2 incidence topology; independent of numerical H coefficients."""
    rows, cols = [], []
    for a, b in ((0, 1), (0, 2), (1, 2)):
        mask = (local_trace[:, a] >= 0) & (local_trace[:, b] >= 0)
        rows.append(local_trace[mask, a]); cols.append(local_trace[mask, b])
    row, col = np.concatenate(rows), np.concatenate(cols)
    graph = sparse.coo_matrix((np.ones(2 * len(row), dtype=np.uint8), (np.r_[row, col], np.r_[col, row])), shape=(ntrace, ntrace)).tocsr()
    return int(sparse.csgraph.connected_components(graph, directed=False, return_labels=False))


def tree_potential(first: np.ndarray, second: np.ndarray, parent: np.ndarray, drop: np.ndarray, root: int) -> np.ndarray:
    """Defer the pinned saved-contact helper import so the synthetic trace check stays lightweight."""
    from recover_astra_l04_saved_contact_potentials import tree_potential as recovered
    return recovered(first, second, parent, drop, root)


def self_check() -> None:
    # Two cells share branch0; each has a rim contact branch.  This uses production mapping/scatter functions.
    facets = np.array([[0, 1, -1], [0, 2, -1]], dtype=np.int64)
    signs = np.array([[1, 1, 0], [-1, 1, 0]], dtype=np.int8)
    first, second = np.array([0, 0, 1]), np.array([1, 2, 3])
    local_trace, branch_trace, degree = trace_index(facets, signs, first, second, 2, 2)
    require(degree.tolist() == [2, 1, 1] and branch_trace.tolist() == [0, 1, 2], "synthetic trace order")
    upper = np.tile(np.array([1., -1., 0., 1., 0., 0.]), (2, 1)); ii, jj = np.triu_indices(3)
    h, row_expected, _, _ = assemble_trace_h(local_trace, facets >= 0, upper, ii, jj, 3)
    require(np.max(abs((h-h.T).data), initial=0.) == 0 and np.max(abs(h @ np.ones(3))) == 0, "synthetic scatter symmetry/null")
    require(np.max(abs(h @ np.ones(3)-row_expected)) < 2e-14, "synthetic row-sum scatter")
    require(topology_components(local_trace, 3) == 1, "synthetic hypergraph connectivity")
    print(f"{PROGRAM} v{VERSION}: PASS_L04_TWO_CELL_TRACE_INDEX_AND_SCATTER")


def run(output: Path) -> None:
    require(RUN_RELEASED, "RUN_RELEASED=False: exact trace preparation is held")
    require(not output.exists(), "output already exists")
    budget = recon._Budget.create(180, 8)
    for name, (path, digest) in PINS.items(): require(sha(path) == digest, f"SHA differs: {name}")
    inputs = {name: receipt(path) for name, (path, _) in PINS.items()}
    static_result, stream_result = (json.loads(PINS[key][0].read_text(encoding="utf-8")) for key in ("static_cells_result", "stream_result"))
    dual_result = json.loads(PINS["saved_dual_result"][0].read_text(encoding="utf-8"))
    stream_external = json.loads(PINS["stream_external"][0].read_text(encoding="utf-8"))
    dual_external = json.loads(PINS["saved_dual_external"][0].read_text(encoding="utf-8"))
    require(static_result["status"] == "PREPARED_STATIC_CONDITIONAL_L04_EXACT_HYBRID_CELLS" and stream_result["status"] == "PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM" and dual_result["status"] == "PASS_SAVED_L04_RT0_CONTACT_DUAL_POTENTIALS", "input statuses")
    require(static_result["geometry_approximation"] == stream_result["geometry_approximation"] == dual_result["geometry_approximation"], "geometry identity")
    require(static_result["artifact"]["sha256"] == PINS["static_cells_artifact"][1]
            and static_result["driver"]["sha256"] == PINS["static_cells_driver"][1], "static receipt correspondence")
    require(dual_result["artifact"]["sha256"] == PINS["saved_dual_artifact"][1]
            and dual_result["driver"]["sha256"] == PINS["saved_dual_driver"][1], "saved dual receipt correspondence")
    require(stream_result["space"]["sha256"] == PINS["stream_space"][1]
            and stream_result["field"]["sha256"] == PINS["stream_field"][1]
            and stream_result["stream_system"]["sha256"] == PINS["stream_system"][1], "stream receipt correspondence")
    require(stream_result["driver_sha256"] == PINS["stream_driver"][1], "stream frozen driver")
    for record, key in ((static_result["artifact"], "static_cells_artifact"), (static_result["driver"], "static_cells_driver"),
                        (dual_result["artifact"], "saved_dual_artifact"), (dual_result["driver"], "saved_dual_driver"),
                        (stream_result["field"], "stream_field"), (stream_result["space"], "stream_space"),
                        (stream_result["stream_system"], "stream_system")):
        require(Path(record["path"]).resolve() == PINS[key][0].resolve(), "nested receipt path: " + key)
    require(stream_external["status"] == dual_external["status"] == "COMPLETED_NATIVE_WORKER" and stream_external["exit_code"] == dual_external["exit_code"] == 0, "completed saved external guards")
    with np.load(PINS["static_cells_artifact"][0], allow_pickle=False) as cells, np.load(PINS["stream_space"][0], allow_pickle=False) as space:
        free, facets, signs = cells["free_triangle_indices"], cells["local_facet_branch_index"], cells["local_outward_flux_sign"]
        active, upper, rho, weights, ii, jj = cells["active_local_facet_mask"], cells["dc_trace_h_upper_s"], cells["unit_divergence_resistance_ohm"], cells["unit_divergence_flux_weights"], cells["upper_rows"], cells["upper_columns"]
        first, second = space["branch_first_node"], space["branch_second_node"]
        contact_degree = space["contact_branch_degree"]
        contact_support = space["contact_support_index"]
        require(np.array_equal(free, space["free_triangle_indices"]) and np.array_equal(facets, space["local_facet_branch_index"]) and np.array_equal(signs, space["local_outward_flux_sign"]) and np.array_equal(active, facets >= 0), "static/space identity")
    require(len(free) == NCELL and len(first) == NINTERNAL + NRIM and int(np.sum(active.sum(axis=1) ** 2)) == 10_314_116, "fixed dimensions")
    local_trace, branch_trace, degree = trace_index(facets, signs, first, second, NCELL, NCONTACT)
    require(np.count_nonzero(degree == 2) == NINTERNAL and np.count_nonzero(degree == 1) == NRIM, "branch counts")
    require(np.array_equal(contact_degree, np.full(NCONTACT, 16)) and int(contact_degree.sum()) == NRIM, "saved contact branch degree")
    h_full, row_expected, row_operand, row_operations = assemble_trace_h(local_trace, active, upper, ii, jj, NTRACE)
    gauge = NINTERNAL + GAUGE_CONTACT
    keep = np.r_[np.arange(gauge), np.arange(gauge + 1, NTRACE)]
    h_gauged = h_full[keep, :][:, keep].tocsc()
    symmetry = float(np.max(abs((h_full-h_full.T).data), initial=0.) / max(np.max(abs(h_full.data)), 1e-300))
    full_row_sum = h_full @ np.ones(NTRACE)
    eps = np.finfo(float).eps
    operations = 2 * row_operations + 4  # Local row sums, duplicate reduction, and sparse row sum.
    gamma = operations * eps / (1 - operations * eps)
    row_bound = gamma * (row_operand + abs(row_expected))
    row_sum_ratio = float(np.max(abs(full_row_sum-row_expected) / np.maximum(row_bound, np.finfo(float).tiny)))
    components = topology_components(local_trace, NTRACE)
    structural_gates = dict(symmetry=symmetry < 2e-12, row_sum_scatter=row_sum_ratio <= 1,
                            connected=components == 1, positive_diagonal=bool(np.all(h_gauged.diagonal() > 0)),
                            no_zero_rows=bool(np.all(np.diff(h_gauged.tocsr().indptr) > 0)),
                            finite=bool(np.all(np.isfinite(h_full.data))))
    output.mkdir(parents=True); frozen = output / "driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes())
    checkpoint = output / "structural-checkpoint.npz"
    independent_contacts = np.delete(np.arange(NCONTACT, dtype=np.int64), GAUGE_CONTACT)
    np.savez_compressed(checkpoint, local_trace_index=local_trace, branch_trace_index=branch_trace,
                        branch_occurrence_degree=degree, contact_support_index=contact_support,
                        independent_contact_indices=independent_contacts, gauged_contact_trace_rows=NINTERNAL + independent_contacts - (independent_contacts > GAUGE_CONTACT),
                        gauge_contact_index=np.array([GAUGE_CONTACT]), full_row_sum=full_row_sum,
                        direct_row_sum_expected=row_expected, row_sum_operand_magnitude=row_operand,
                        row_sum_roundoff_bound=row_bound, row_sum_term_count=row_operations,
                        gauge_trace_index=np.array([gauge]), **csc_parts(h_full, "h_full"), **csc_parts(h_gauged, "h_gauged"))
    checkpoint_result = {"program": PROGRAM, "version": VERSION, "status": "MEASURED_L04_EXACT_TRACE_STRUCTURAL_CHECKPOINT", "gates": structural_gates, "budget": budget.receipt(),
                         "driver": receipt(frozen), "inputs": inputs, "checkpoint": receipt(checkpoint),
                         "counts": {"internal_trace_rows": NINTERNAL, "rim_branches": NRIM, "contact_trace_rows": NCONTACT,
                                    "full_trace_rows": NTRACE, "gauged_trace_rows": NTRACE-1, "local_coo_entries": 10_314_116},
                         "structural": {"symmetry_relative": symmetry, "row_sum_scatter_roundoff_ratio": row_sum_ratio,
                                        "hypergraph_components": components, "nullity_certificate": "exact-formula connectivity/nullity certificate only; no eigenvalue was measured"}}
    (output / "structural-checkpoint.json").write_text(json.dumps(checkpoint_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    budget.check("full exact trace H assembly and structure")
    require(all(structural_gates.values()), "H structural gates; diagnostics preserved")
    with np.load(PINS["stream_field"][0], allow_pickle=False) as field, np.load(PINS["stream_space"][0], allow_pickle=False) as space, np.load(PINS["stream_system"][0], allow_pickle=False) as system, np.load(PINS["saved_dual_artifact"][0], allow_pickle=False) as dual:
        q, target = field["branch_current_a"], field["original_target_a"]
        rmat = sparse.csr_matrix((space["r_data"], space["r_indices"], space["r_indptr"]), shape=tuple(space["r_shape"]))
        parent = system["parent_branch"]
        root = int(stream_result["metrics"]["gauge_contact_graph_row"])
        saved_contact_u = dual["contact_dual_potential_v"]
        require(np.array_equal(target, space["original_target_a"]) and np.array_equal(target[NCELL:], dual["original_contact_target_into_sheet_a"]), "field/space/dual target identity")
        require(int(dual["gauge_contact_index"][0]) == GAUGE_CONTACT, "saved dual gauge contact")
        require(contact_support.shape == (NCONTACT,) and np.all(np.diff(contact_support) > 0)
                and np.array_equal(contact_support, dual["contact_support_index"]), "same ordered contact supports")
    require(root - NCELL == GAUGE_CONTACT and 0 <= root < len(parent), "stream gauge contact")
    require(q.shape == first.shape == second.shape == (NINTERNAL + NRIM,) and rmat.shape == (len(q), len(q))
            and target.shape == parent.shape == (NCELL + NCONTACT,), "saved field dimensions")
    require(np.all(np.isfinite(q)) and np.all(np.isfinite(rmat.data)) and np.all(target[:NCELL] == 0), "saved finite data/zero cell source")
    drop = rmat @ q; voltage = tree_potential(first, second, parent, drop, root)
    full_r_dual_error = voltage[first] - voltage[second] - drop
    full_r_dual_relative = float(np.linalg.norm(full_r_dual_error) / np.linalg.norm(drop))
    require(np.array_equal(voltage[NCELL:], saved_contact_u), "saved contact dual replay")
    lambda_local = np.empty((NCELL, 3), dtype=np.complex128); q_local = np.zeros((NCELL, 3), dtype=np.complex128)
    raw_q = np.zeros((NCELL, 3), dtype=np.complex128); qout_all = np.zeros((NCELL, 3), dtype=np.complex128)
    cell_d = np.empty(NCELL, dtype=np.complex128); cell_u = np.empty(NCELL, dtype=np.complex128); direct_expected = np.zeros(NTRACE, dtype=np.complex128)
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh:
        xy, triangles = mesh["node_xy_um"], mesh["triangles"]
    local_operand_max = 0.0
    for start in range(0, NCELL, 50_000):
        rows = np.arange(start, min(start + 50_000, NCELL)); p = xy[triangles[free[rows]]].copy(); p -= p[:, :1].copy(); p *= 1e-6
        rk, _, _ = local._batch_local_rt0(p, float(static_result["material"]["sheet_conductance_s"]))
        qout = np.zeros((len(rows), 3), dtype=np.complex128); qout[active[rows]] = signs[rows][active[rows]] * q[facets[rows][active[rows]]]
        d = qout.sum(axis=1)
        local_drop = np.einsum("nij,nj->ni", rk, qout)
        lambda_local[rows] = voltage[rows, None] - local_drop
        local_operand_max = max(local_operand_max, float(np.max(np.einsum("nij,nj->ni", abs(rk), abs(qout)))))
        h = np.zeros((len(rows), 3, 3)); h[:, ii, jj] = upper[rows]; h[:, jj, ii] = upper[rows]
        u = rho[rows] * d + np.einsum("ni,ni->n", weights[rows], lambda_local[rows])
        # Use the original local drop to avoid subtracting nearly equal common voltages twice.
        q_local[rows] = weights[rows] * d[:, None] + np.einsum("nij,nj->ni", h, local_drop)
        raw_q[rows] = weights[rows] * d[:, None] - np.einsum("nij,nj->ni", h, lambda_local[rows])
        qout_all[rows], cell_d[rows], cell_u[rows] = qout, d, u
        np.add.at(direct_expected, local_trace[rows][active[rows]], (weights[rows] * d[:, None] - qout)[active[rows]])
        budget.check("saved local R/lambda/q trace replay")
    trace_lambda = np.zeros(NTRACE, dtype=np.complex128); trace_multiplicity = np.zeros(NTRACE, dtype=np.int64)
    np.add.at(trace_lambda, local_trace[active], lambda_local[active]); np.add.at(trace_multiplicity, local_trace[active], 1)
    require(np.array_equal(trace_multiplicity[:NINTERNAL], np.full(NINTERNAL, 2)) and np.array_equal(trace_multiplicity[NINTERNAL:], contact_degree), "trace multiplicity")
    trace_lambda /= trace_multiplicity
    gauge_shift = complex(trace_lambda[gauge])
    trace_lambda -= gauge_shift
    require(trace_lambda[gauge] == 0, "exact stored trace gauge")
    occurrence = lambda_local[active] - trace_lambda[local_trace[active]]
    h_lambda = h_full @ trace_lambda
    ideal_expected = np.r_[np.zeros(NINTERNAL, complex), target[NCELL:]]
    raw_q_defect = raw_q[active] - signs[active] * q[facets[active]]
    q_defect = q_local[active] - qout_all[active]
    u_identity = cell_u - voltage[:NCELL]
    q_from_trace = np.zeros_like(qout_all)
    for start in range(0, NCELL, 50_000):
        rows = np.arange(start, min(start + 50_000, NCELL)); mask = active[rows]
        shared = np.zeros((len(rows), 3), dtype=np.complex128)
        shared[mask] = trace_lambda[local_trace[rows][mask]]
        h = np.zeros((len(rows), 3, 3)); h[:, ii, jj] = upper[rows]; h[:, jj, ii] = upper[rows]
        q_from_trace[rows] = weights[rows] * cell_d[rows, None] - np.einsum("nij,nj->ni", h, shared-(voltage[rows, None]-gauge_shift))
        budget.check("current reconstructed from the assembled shared trace")
    shared_q_defect = q_from_trace[active] - qout_all[active]
    energy = np.array([[q.real @ drop.real, q.real @ drop.imag], [q.imag @ drop.real, q.imag @ drop.imag]])
    bilinear, hermitian = np.dot(q, drop), np.vdot(q, drop)
    h_bilinear, h_hermitian = np.dot(trace_lambda, h_lambda), np.vdot(trace_lambda, h_lambda)
    correction_bilinear, correction_hermitian = np.dot(rho, cell_d * cell_d), np.dot(rho, abs(cell_d) ** 2)
    h_energy = np.array([[trace_lambda.real @ h_lambda.real, trace_lambda.real @ h_lambda.imag], [trace_lambda.imag @ h_lambda.real, trace_lambda.imag @ h_lambda.imag]])
    d_energy = np.array([[rho @ (cell_d.real * cell_d.real), rho @ (cell_d.real * cell_d.imag)], [rho @ (cell_d.imag * cell_d.real), rho @ (cell_d.imag * cell_d.imag)]])
    contact_average_defect = trace_lambda[NINTERNAL:] - saved_contact_u
    raw_hlambda_direct = h_lambda - direct_expected
    # An internal pair differs by its original full-R dual defect. Contact averaging
    # adds at most two such defects; the remainder is bounded by actual operands.
    max_r_terms = int(np.diff(rmat.indptr).max())
    nops = 2 * max_r_terms + 2 * int(trace_multiplicity.max()) + 16
    trace_gamma = nops * eps / (1 - nops * eps)
    trace_arithmetic_bound = trace_gamma * (float(abs(voltage).max()) + local_operand_max
        + float(np.max(abs(rmat) @ abs(q))) + float(abs(lambda_local[active]).max()))
    trace_comparison_bound = 2 * float(abs(full_r_dual_error).max()) + trace_arithmetic_bound + abs(gauge_shift)
    direct_ideal = direct_expected - ideal_expected
    equivalence = output / "saved-field-equivalence.npz"
    np.savez_compressed(equivalence, trace_lambda_v=trace_lambda, trace_multiplicity=trace_multiplicity, local_lambda_v=lambda_local,
                        local_q_reconstructed_outward_a=q_local, local_q_raw_hlambda_outward_a=raw_q, local_q_outward_a=qout_all,
                        local_q_from_shared_trace_outward_a=q_from_trace,
                        cell_divergence_a=cell_d, cell_hybrid_voltage_v=cell_u, saved_cell_dual_voltage_v=voltage[:NCELL],
                        saved_full_r_dual_error_v=full_r_dual_error, saved_contact_dual_voltage_v=saved_contact_u,
                        h_lambda_a=h_lambda, trace_gauge_shift_v=np.array([gauge_shift]),
                        direct_expected_h_lambda_a=direct_expected, ideal_expected_h_lambda_a=ideal_expected,
                        raw_hlambda_minus_direct_a=raw_hlambda_direct, direct_minus_ideal_a=direct_ideal,
                        full_r_bilinear_a2_ohm=np.array([bilinear]), full_r_hermitian_a2_ohm=np.array([hermitian]),
                        hybrid_h_bilinear_a2_ohm=np.array([h_bilinear]), hybrid_h_hermitian_a2_ohm=np.array([h_hermitian]),
                        rho_correction_bilinear_a2_ohm=np.array([correction_bilinear]), rho_correction_hermitian_a2_ohm=np.array([correction_hermitian]),
                        full_real_imag_energy=energy, hybrid_real_imag_energy=h_energy, rho_real_imag_correction=d_energy)
    q_scale, u_scale = np.linalg.norm(qout_all[active]), np.linalg.norm(voltage[:NCELL])
    require(q_scale > 0 and u_scale > 0 and abs(bilinear) > 0 and abs(hermitian) > 0 and np.linalg.norm(energy) > 0, "nonzero dimensional reference scales")
    metrics = {"lambda_occurrence_max_abs_v": float(abs(occurrence).max()), "contact_average_vs_saved_max_abs_v": float(abs(contact_average_defect).max()),
               "shifted_q_replay_max_abs_a": float(abs(q_defect).max()), "u_identity_max_abs_v": float(abs(u_identity).max()),
               "raw_q_common_voltage_cancellation_max_abs_a": float(abs(raw_q_defect).max()),
               "raw_hlambda_minus_direct_max_abs_a": float(abs(raw_hlambda_direct).max()), "raw_hlambda_minus_direct_relative": float(np.linalg.norm(raw_hlambda_direct) / max(np.linalg.norm(direct_expected), 1e-300)),
               "direct_minus_ideal_max_abs_a": float(abs(direct_ideal).max()), "direct_root_residual_a": [float(direct_ideal[gauge].real), float(direct_ideal[gauge].imag)],
               "direct_cell_residual_l2_a": float(np.linalg.norm(direct_ideal[:NINTERNAL])), "direct_contact_residual_l2_a": float(np.linalg.norm(direct_ideal[NINTERNAL:])),
               "full_r_bilinear": [float(bilinear.real), float(bilinear.imag)], "hybrid_h_bilinear": [float(h_bilinear.real), float(h_bilinear.imag)], "rho_correction_bilinear": [float(correction_bilinear.real), float(correction_bilinear.imag)],
               "full_r_hermitian": [float(hermitian.real), float(hermitian.imag)], "hybrid_h_hermitian": [float(h_hermitian.real), float(h_hermitian.imag)], "rho_correction_hermitian": float(correction_hermitian),
               "energy_full": energy.tolist(), "energy_hybrid": h_energy.tolist(), "energy_rho_correction": d_energy.tolist(),
               "bilinear_corrected_difference": [float((bilinear-h_bilinear-correction_bilinear).real), float((bilinear-h_bilinear-correction_bilinear).imag)],
               "hermitian_corrected_difference": [float((hermitian-h_hermitian-correction_hermitian).real), float((hermitian-h_hermitian-correction_hermitian).imag)],
               "energy_corrected_difference_max_abs": float(abs(energy-h_energy-d_energy).max())}
    metrics.update(full_r_dual_relative=full_r_dual_relative,
        direct_vs_original_target_relative=float(np.linalg.norm(direct_ideal) / np.linalg.norm(ideal_expected)),
        local_q_reconstruction_relative=float(np.linalg.norm(q_defect) / q_scale),
        shared_trace_q_reconstruction_relative=float(np.linalg.norm(shared_q_defect) / q_scale),
        shared_trace_q_reconstruction_max_abs_a=float(abs(shared_q_defect).max()),
        independent_cell_u_relative=float(np.linalg.norm(u_identity) / u_scale),
        bilinear_corrected_relative=float(abs(bilinear-h_bilinear-correction_bilinear) / abs(bilinear)),
        hermitian_corrected_relative=float(abs(hermitian-h_hermitian-correction_hermitian) / abs(hermitian)),
        energy_matrix_corrected_relative=float(np.linalg.norm(energy-h_energy-d_energy) / np.linalg.norm(energy)),
        trace_comparison_bound_v=float(trace_comparison_bound), trace_arithmetic_bound_v=float(trace_arithmetic_bound),
        trace_bound_operation_count=nops, saved_full_r_dual_max_abs_v=float(abs(full_r_dual_error).max()))
    relative_names = ("full_r_dual_relative", "direct_vs_original_target_relative", "local_q_reconstruction_relative", "shared_trace_q_reconstruction_relative",
        "independent_cell_u_relative", "raw_hlambda_minus_direct_relative", "bilinear_corrected_relative",
        "hermitian_corrected_relative", "energy_matrix_corrected_relative")
    gates = {name: bool(np.isfinite(metrics[name]) and metrics[name] <= 1e-7) for name in relative_names}
    gates.update(trace_occurrences=bool(np.isfinite(trace_comparison_bound) and metrics["lambda_occurrence_max_abs_v"] <= trace_comparison_bound),
                 contact_potentials=bool(metrics["contact_average_vs_saved_max_abs_v"] <= trace_comparison_bound),
                 finite_arrays=all(bool(np.all(np.isfinite(value))) for value in
                     (lambda_local, trace_lambda, q_local, q_from_trace, cell_u, direct_expected, h_lambda, energy, h_energy)))
    preliminary = {"program": PROGRAM, "version": VERSION, "status": "MEASURED_L04_EXACT_TRACE_SAVED_FIELD_EQUIVALENCE_PREACCEPTANCE",
                   "driver": receipt(frozen), "checkpoint": receipt(checkpoint), "artifact": receipt(equivalence),
                   "metrics": metrics, "gates": gates, "relative_threshold": 1e-7, "budget": budget.receipt()}
    (output / "equivalence-preacceptance.json").write_text(json.dumps(preliminary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    budget.check("saved trace equivalence arrays and raw metrics")
    require(all(gates.values()), "saved-field equivalence gates; arrays and metrics preserved")
    result = {"program": PROGRAM, "version": VERSION, "status": "PREPARED_STATIC_L04_EXACT_HYBRID_TRACE_SAVED_FIELD_EQUIVALENCE", "run_released": RUN_RELEASED, "driver": receipt(frozen), "inputs": inputs, "structural_checkpoint": receipt(checkpoint), "artifact": receipt(equivalence), "geometry_approximation": stream_result["geometry_approximation"], "material": static_result["material"], "counts": {"internal_trace_rows": NINTERNAL, "rim_branches": NRIM, "contact_trace_rows": NCONTACT, "full_trace_rows": NTRACE, "gauged_trace_rows": NTRACE-1, "local_coo_entries": 10_314_116, "gauge_contact_index": GAUGE_CONTACT, "future_total_sparse_primal_dimension": 5_480_937, "future_native_l04_factor_block": 2_455_688}, "structural": {"symmetry_relative": symmetry, "row_sum_scatter_roundoff_ratio": row_sum_ratio, "hypergraph_components": components, "nullity_certificate": "exact-formula connectivity/nullity certificate only; no eigenvalue was measured"}, "saved_field_equivalence": metrics, "equivalence_gates": gates, "relative_threshold": 1e-7, "budget": budget.receipt(), "scope": "Static exact L04 trace assembly plus saved-field equivalence only. No native/global circuit matrix, P_h factor, solve, NtD replay, new Z, or accuracy claim."}
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}: disabled exact L04 hybrid trace")
    modes = parser.add_mutually_exclusive_group(required=True); modes.add_argument("--self-check", action="store_true"); modes.add_argument("--assemble", action="store_true")
    parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check()
    else:
        require(args.output is not None, "--assemble requires --output")
        output = args.output.resolve(); existed = output.exists()
        try:
            run(output)
        except BaseException:
            if not existed and output.is_dir():
                (output / "failure.json").write_text(json.dumps(dict(status="STOP_L04_EXACT_TRACE_PREPARATION",
                    traceback=traceback.format_exc(), driver=receipt(Path(__file__))), indent=2) + "\n", encoding="utf-8")
            raise


if __name__ == "__main__": main()
