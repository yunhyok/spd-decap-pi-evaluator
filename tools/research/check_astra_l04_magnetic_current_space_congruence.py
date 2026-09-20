"""SPD Decap PI Evaluator v0.23.1: tiny current-space magnetic congruence check."""
import json
import numpy as np


def check():
    # Five return branches; node 0 is an interior node, 1/2/3 are contacts.
    b = np.array([[1, 1, 1, 0, 0], [-1, 0, 0, 1, 0],
                  [0, -1, 0, -1, 1], [0, 0, -1, 0, -1]], float)
    c = np.array([[1, 0], [-1, 1], [0, -1], [1, 0], [0, 1]], float)
    t = np.array([[1, 1], [-1, 0], [0, -1], [0, 0], [0, 0]], float)
    a = np.arange(1., 26.).reshape(5, 5) / 50
    r04 = .01 * (np.diag([2, 3, 4, 5, 6]) + a.T @ a)
    h = c.T @ r04 @ c
    p = t - c @ np.linalg.solve(h, c.T @ r04 @ t)
    e = np.array([[0, 0], [-1, -1], [1, 0], [0, 1]], float)
    assert np.allclose(b @ p, e) and np.allclose(b @ c, 0)
    assert np.linalg.norm(p.T @ r04 @ c) < 1e-15

    r = np.zeros((7, 7))
    r[:2, :2] = np.array([[.04, .003], [.003, .06]])
    r[2:, 2:] = r04
    a = np.sin(.31 * np.arange(49)).reshape(7, 7)
    inductance = 1e-9 * (a.T @ a + np.eye(7))
    jw = 2j * np.pi * 1e7
    z = r + jw * inductance
    transform = np.zeros((7, 6))
    transform[:2, :2] = np.eye(2)
    transform[2:, 2:4], transform[2:, 4:] = p, c
    constraint = np.r_[np.zeros(2), b[0]][None, :]
    force = np.r_[[.01+.003j, -.002+.001j], b.T @ [0, 0, .002, -.001j]]
    kkt = np.block([[z, constraint.T], [constraint, np.zeros((1, 1))]])
    reference = np.linalg.solve(kkt, np.r_[force, 0])[:7]
    reduced = transform.T @ z @ transform
    coordinates = np.linalg.solve(reduced, transform.T @ force)
    current = transform @ coordinates
    relative = float(np.linalg.norm(current-reference)/np.linalg.norm(reference))
    assert relative < 1e-12 and np.linalg.norm(constraint @ current) < 1e-14
    q25, g, psi = coordinates[:2], coordinates[2:4], coordinates[4:]
    magnetic04 = inductance[2:, :2] @ q25 + inductance[2:, 2:] @ current[2:]
    contact = p.T @ r04 @ p @ g + jw * p.T @ magnetic04 - p.T @ force[2:]
    closed = h @ psi + jw * c.T @ magnetic04 - c.T @ force[2:]
    assert np.linalg.norm(contact) < 1e-14 and np.linalg.norm(closed) < 1e-14
    assert np.linalg.norm(reduced-reduced.T) < 1e-14
    # Dropping psi changes the solution even while contact divergence is retained.
    restricted = transform[:, :4]
    restricted_current = restricted @ np.linalg.solve(restricted.T @ z @ restricted, restricted.T @ force)
    omitted_closed_residual = float(np.linalg.norm(c.T @ (z @ restricted_current-force)[2:]))
    assert omitted_closed_residual > 1e-5
    return {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "PASS_TINY_MAGNETIC_CURRENT_SPACE_CONGRUENCE",
            "current_relative_difference": relative,
            "contact_residual_v": float(np.linalg.norm(contact)),
            "closed_residual_v": float(np.linalg.norm(closed)),
            "omitted_closed_residual_v": omitted_closed_residual,
            "scope": "Synthetic algebra only. Complete for fixed interior divergence constraint; no board, FMM, quadrature, distributed charge extension or accuracy claim."}


if __name__ == "__main__":
    print(json.dumps(check(), indent=2, allow_nan=False))
