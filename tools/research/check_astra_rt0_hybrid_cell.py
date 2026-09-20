"""Check exact RT0/P0 cell condensation with owned complex G/C terminals.

Cell fluxes point outward. Resistance contains only flux coordinates that
remain active: an insulating q=0 facet is removed BEFORE taking the inverse.
This algebra check adds no source geometry or physical accuracy claim.
"""
import json

import numpy as np


def condense_cell(resistance, admittance):
    r = np.asarray(resistance, dtype=float)
    g = np.asarray(admittance, dtype=complex)
    assert r.ndim == 2 and r.shape[0] == r.shape[1] and len(r) > 0
    assert g.ndim == 1 and np.all(np.isfinite(r)) and np.all(np.isfinite(g))
    assert np.allclose(r, r.T, rtol=1e-14, atol=0) and np.all(g.real >= 0)
    np.linalg.cholesky(r)
    inverse = np.linalg.solve(r, np.eye(len(r)))
    a = inverse.sum(axis=1)
    denominator = a.sum()+g.sum()
    assert denominator.real > 0 and np.isfinite(denominator)
    weights = np.r_[a, g]
    y = np.zeros((len(weights), len(weights)), dtype=complex)
    y[:len(r), :len(r)] = inverse
    y[len(r):, len(r):] = np.diag(g)
    y -= np.outer(weights, weights)/denominator
    return y, inverse, weights, denominator


def recover_cell(inverse, weights, denominator, terminal_voltage):
    potential = weights@terminal_voltage/denominator
    flux = inverse@(potential-terminal_voltage[:len(inverse)])
    return potential, flux


def add_block(matrix, indices, block):
    # Repeated facet indices correctly represent a shared aggregate contact.
    np.add.at(matrix, (np.asarray(indices)[:, None], np.asarray(indices)[None, :]), block)


def solve_grounded(matrix, rhs, gauge):
    kept = np.delete(np.arange(len(rhs)), gauge)
    solution = np.zeros(len(rhs), dtype=complex)
    solution[kept] = np.linalg.solve(matrix[np.ix_(kept, kept)], rhs[kept])
    return solution


def self_check():
    # Two cells share one facet. Each also touches one electrode and has one
    # insulating facet; restricting the full local mass imposes q_ext=0.
    full_r = [np.array([[2., .3, .2], [.3, 1.4, -.1], [.2, -.1, 1.1]]),
              np.array([[1.7, -.2, .1], [-.2, 2.2, .4], [.1, .4, 1.3]])]
    local_r = [r[:2, :2] for r in full_r]
    gc = [np.array([.02+.04j, .01+.03j]), np.array([.03+.02j, .015+.06j])]
    # Full mixed potentials: cell0, cell1, contact0, contact1, external0, external1.
    incidence = np.array([[1., 1., 0.], [-1., 0., 1.], [0., -1., 0.],
                          [0., 0., -1.], [0., 0., 0.], [0., 0., 0.]])
    local_maps = [np.array([[1., 0., 0.], [0., 1., 0.]]),
                  np.array([[-1., 0., 0.], [0., 0., 1.]])]
    resistance = sum(m.T@r@m for m, r in zip(local_maps, local_r))
    y = np.zeros((6, 6), dtype=complex)
    for cell, values in enumerate(gc):
        for external, value in zip((4, 5), values):
            add_block(y, [cell, external], value*np.array([[1., -1.], [-1., 1.]]))
    loads = [(4, 3, .2+.1j), (5, 2, .3+.2j)]
    for a, b, value in loads:
        add_block(y, [a, b], value*np.array([[1., -1.], [-1., 1.]]))
    mixed = np.block([[y, incidence], [incidence.T, -resistance]])
    rhs = np.zeros(9); rhs[2], rhs[3] = 1, -1
    exact = solve_grounded(mixed, rhs, 3)

    # Hybrid potentials: shared facet, contact0, contact1, external0, external1.
    hybrid = np.zeros((5, 5), dtype=complex)
    cell_ports = [[0, 1, 3, 4], [0, 2, 3, 4]]
    pieces = [condense_cell(r, g) for r, g in zip(local_r, gc)]
    for ports, (block, *_rest) in zip(cell_ports, pieces):
        add_block(hybrid, ports, block)
    for a, b, value in loads:
        add_block(hybrid, [a-1, b-1], value*np.array([[1., -1.], [-1., 1.]]))
    hybrid_rhs = np.zeros(5); hybrid_rhs[1], hybrid_rhs[2] = 1, -1
    trace = solve_grounded(hybrid, hybrid_rhs, 2)
    recovered = [recover_cell(*piece[1:], trace[ports]) for piece, ports in zip(pieces, cell_ports)]
    voltage = np.r_[[entry[0] for entry in recovered], trace[1:]]
    flux0, flux1 = recovered[0][1], recovered[1][1]
    current = np.array([flux0[0], flux0[1], flux1[1]])
    physical = np.r_[voltage, current]
    assert abs(flux0[0]+flux1[0]) < 1e-12
    assert np.max(abs(physical-exact)) < 1e-12
    assert np.max(abs(mixed@physical-rhs)) < 1e-12
    power = np.vdot(current, resistance@current)+np.conj(np.vdot(voltage, y@voltage))
    assert abs(power-(voltage[2]-voltage[3])) < 1e-12
    assert np.max(abs(hybrid-hybrid.T)) < 1e-14
    assert np.max(abs(hybrid.sum(axis=1))) < 1e-14
    # Inverting before dropping an insulating facet is a different operator.
    wrong = np.linalg.inv(full_r[0])[:2, :2]
    assert np.max(abs(wrong-pieces[0][1])) > 1e-3
    repeated = np.zeros((2, 2)); add_block(repeated, [0, 0], np.ones((2, 2)))
    assert repeated[0, 0] == 4
    return {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "PASS_TWO_CELL_RT0_P0_HYBRID_RC_ALGEBRA",
            "solution_max_abs_error": float(np.max(abs(physical-exact))),
            "physical_residual_max_abs": float(np.max(abs(mixed@physical-rhs))),
            "power_closure_abs": float(abs(power-(voltage[2]-voltage[3]))),
            "scope": "Exact algebra with two coupled cells, two owned G/C terminals per cell, passive external loads and insulating facets. No real-mesh, memory-cost, magnetic or board-accuracy acceptance."}


if __name__ == "__main__":
    print(json.dumps(self_check(), allow_nan=False))
