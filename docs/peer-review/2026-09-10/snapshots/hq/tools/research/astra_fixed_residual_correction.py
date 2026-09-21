"""One fixed residual correction for SPD Decap PI Evaluator v0.23.1 auxiliaries."""
import numpy as np


def corrected_solve(matrix, rhs, solve):
    """Apply B1 = 2B - BAB; a fixed linear map, with no adaptive stopping."""
    first = solve(rhs)
    return first + solve(rhs - matrix @ first)


def self_check():
    a = np.array([[3+.2j, -.4+.1j], [-.4+.1j, 2+.3j]])
    b = .97*np.linalg.inv(a)
    expected = 2*b-b@a@b
    actual = corrected_solve(a, np.eye(2), lambda rhs: b@rhs)
    assert np.max(abs(actual-expected)) < 1e-15
    assert np.max(abs(actual-actual.T)) < 1e-15
    x, y = np.array([1+.5j, -.3j]), np.array([.2, .8-.1j])
    alpha = .7+.4j
    assert np.max(abs(corrected_solve(a, x+alpha*y, lambda r: b@r)
                      - actual@x-alpha*(actual@y))) < 1e-15
    assert np.linalg.norm(a@actual-np.eye(2)) < np.linalg.norm(a@b-np.eye(2))
    print('PASS_FIXED_CORRECTION_LINEAR_ORDINARY_TRANSPOSE')


if __name__ == '__main__':
    self_check()
