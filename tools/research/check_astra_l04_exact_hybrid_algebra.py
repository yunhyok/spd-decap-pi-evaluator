"""SPD Decap PI Evaluator v0.23.1: two-cell exact RT0/trace/MNA check only."""
import numpy as np

import prepare_astra_l02_hybrid_cell_geometry as cells
import prepare_astra_l02_restricted_hybrid_cells as restricted
import preflight_astra_l04_sheet_aware_native_preconditioner as primal
import solve_astra_l04_full_contact_coupled as coupled
import assemble_astra_l04_exact_hybrid_trace as assembled


def check():
    # Two cells share one face; each has one contact and one zero-flux wall.
    vertices = np.array([[[0, 0], [2, 0], [0, 1]],
                         [[2, 0], [2, 1], [0, 1]]], float) * 1e-3
    active = np.array([[True, True, False], [True, True, False]])
    local_r, _, _ = cells.local._batch_local_rt0(vertices, cells.CONDUCTANCE)
    local_h, _, _ = restricted.restricted_parameters(vertices, active, cells.CONDUCTANCE)
    # Shared flux points cell0->cell1; the two contact fluxes point outward.
    local_from_global = (np.array([[1, 0, 0], [0, 1, 0.]]),
                         np.array([[0, 0, 1], [-1, 0, 0.]]))
    resistance = sum(p.T @ r[:2, :2] @ p
                     for p, r in zip(local_from_global, local_r))
    incidence = np.array([[1, 1, 0], [-1, 0, 1], [0, -1, 0], [0, 0, -1.]])
    graph_y = incidence @ np.linalg.solve(resistance, incidence.T)
    graph_keep = np.array([0, 1, 3])  # contact0 is the sole sheet gauge.
    graph_phi = np.zeros(4)
    graph_phi[graph_keep] = np.linalg.solve(graph_y[np.ix_(graph_keep, graph_keep)], [0, 0, 1])
    original_q = np.linalg.solve(resistance, incidence.T @ graph_phi)

    # Trace order: shared face, contact0, contact1. Walls have no unknown.
    terminal = (np.array([0, 1]), np.array([2, 0]))
    trace_y = np.zeros((3, 3))
    for ids, h in zip(terminal, local_h):
        trace_y[np.ix_(ids, ids)] += h[:2, :2]
    # Check production mapping/scatter with the shared face in different local slots.
    facets = np.array([[0, 1, -1], [2, 0, -1]])
    signs = np.array([[1, 1, 0], [1, -1, 0]])
    mapped, branch_trace, degree = assembled.trace_index(
        facets, signs, np.array([0, 0, 1]), np.array([1, 2, 3]), 2, 2)
    ii, jj = np.triu_indices(3)
    production_y, expected_row_sum, _, _ = assembled.assemble_trace_h(
        mapped, active, local_h[:, ii, jj], ii, jj, 3)
    assert np.array_equal(mapped, facets) and np.array_equal(branch_trace, [0, 1, 2])
    assert np.array_equal(degree, [2, 1, 1])
    assert np.linalg.norm(production_y.toarray() - trace_y) < 2e-13 * np.linalg.norm(trace_y)
    assert np.linalg.norm(production_y @ np.ones(3) - expected_row_sum) < 2e-13 * np.linalg.norm(trace_y)
    trace_keep = np.array([0, 2])
    h = trace_y[np.ix_(trace_keep, trace_keep)]
    trace_phi = np.zeros(3)
    trace_phi[trace_keep] = np.linalg.solve(h, [0, 1])
    local_q = [-matrix[:2, :2] @ trace_phi[ids]
               for matrix, ids in zip(local_h, terminal)]
    recovered_q = np.array([local_q[0][0], local_q[0][1], local_q[1][0]])
    assert abs(local_q[0][0] + local_q[1][1]) < 2e-13
    assert np.linalg.norm(recovered_q - original_q) < 2e-13
    assert abs(trace_phi[2] - graph_phi[3]) < 2e-13 * abs(graph_phi[3])
    # Recover the same traces from the saved-style mixed cell dual and current.
    for cell, (ids, r, mapping) in enumerate(zip(terminal, local_r, local_from_global)):
        recovered_trace = graph_phi[cell] - r[:2, :2] @ (mapping @ original_q)
        assert np.linalg.norm(recovered_trace - trace_phi[ids]) < 2e-13 * np.linalg.norm(trace_phi)
    assert abs(np.vdot(original_q, resistance @ original_q)
               - np.vdot(trace_phi, trace_y @ trace_phi)) < 2e-13 * abs(graph_phi[3])

    # The original current-interface operator and exact trace primal inverse agree.
    a = np.array([[3+.2j, -.4], [-.4, 2+.3j]])
    u = np.array([[.3+.1j], [-.1+.2j]])
    d = np.array([1.2+.4j])
    contact = np.array([1])
    robin = h.astype(complex); robin[1, 1] += d[0]
    p = np.column_stack([primal.primal_full_action(lambda x: a @ x, u, robin,
                         contact, 2, np.eye(4)[:, i]) for i in range(4)])
    interface = np.column_stack([coupled.interface_apply(lambda x: a @ x, u, d,
                                lambda g: graph_phi[3] * g,
                                np.eye(3)[:2, i], np.eye(3)[2:, i], 2)
                                for i in range(3)])
    lifted = np.column_stack([primal.lifted_mna_preconditioner(np.eye(3)[:, i],
                              np.ones(3), np.ones(4), u, d, contact,
                              lambda rhs: np.linalg.solve(p, rhs), 2, 2)
                              for i in range(3)])
    assert np.linalg.norm(interface @ lifted - np.eye(3)) < 2e-12
    assert np.linalg.norm(p-p.T) < 2e-13
    print('PASS_TWO_CELL_RESTRICTED_RT0_TRACE_CURRENT_AND_MNA_EQUIVALENCE')


if __name__ == '__main__':
    check()
