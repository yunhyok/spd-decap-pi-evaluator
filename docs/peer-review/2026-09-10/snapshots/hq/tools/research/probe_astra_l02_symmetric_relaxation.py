"""Check actual L02 triangular relaxation cost on the frozen failed residual."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve_triangular

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    'numeric_helper': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'operator': base.PINS['conditional'],
    'failed_field': (R/'astra-l02-conditional-hybrid-block-lgmres-01/unvalidated-field.npz', '9fd9fae38a4c1cb016b61f450ee188461650fa19cdcc89ea14ac4f81c6a5b3b4'),
    'failed_diagnostic': (R/'astra-l02-conditional-hybrid-block-lgmres-01/unvalidated-field.json', '458d8afca94ccbdd5227c7fffb1f7b6049848d6c677a2b194bd50c3d1a9c2fab'),
    'budget_helper': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}


def prepare_lower(matrix):
    diagonal = matrix.diagonal()
    assert np.all(np.isfinite(diagonal)) and np.all(diagonal != 0)
    lower = sparse.tril(matrix, format='csc')
    lower.data /= diagonal[lower.indices]
    lower.setdiag(1.)
    return lower, diagonal


def forward(lower, diagonal, rhs):
    return spsolve_triangular(lower, rhs/diagonal, lower=True, unit_diagonal=True)


def backward(lower, diagonal, rhs):
    return spsolve_triangular(lower.T, rhs, lower=False, unit_diagonal=True)/diagonal


def symmetric_cycle(matrix, lower, diagonal, rhs, coarse=None):
    """Forward relaxation, optional symmetric coarse correction, then transpose."""
    result = forward(lower, diagonal, rhs)
    if coarse is not None:
        result += coarse(rhs-matrix@result)
    return result+backward(lower, diagonal, rhs-matrix@result)


def self_check():
    rng = np.random.default_rng(31415)
    raw = rng.normal(size=(7, 7))+1j*rng.normal(size=(7, 7))
    a = raw+raw.T+20*np.eye(7)
    matrix = sparse.csc_matrix(a); lower, diagonal = prepare_lower(matrix)
    f_inv = np.linalg.inv(np.tril(a))
    expected = f_inv.T@np.diag(diagonal)@f_inv
    actual = np.column_stack([symmetric_cycle(matrix, lower, diagonal, e) for e in np.eye(7)])
    assert np.max(abs(actual-expected)) < 1e-13
    p = rng.normal(size=(7, 3)); c = p@np.linalg.inv(p.T@a@p)@p.T
    identity = np.eye(7)
    expected += (identity-f_inv.T@a)@c@(identity-a@f_inv)
    actual = np.column_stack([symmetric_cycle(matrix, lower, diagonal, e, lambda x: c@x) for e in identity])
    assert np.max(abs(actual-expected)) < 1e-13 and np.max(abs(actual-actual.T)) < 1e-13


def run(output):
    self_check()
    budget = recon._Budget.create(120., 4.)
    for name, (path, digest) in PINS.items():
        assert base.sha(path) == digest, name
    diagnostic = json.loads(PINS['failed_diagnostic'][0].read_text(encoding='utf-8'))
    assert diagnostic['lgmres']['info'] == 20 and diagnostic['field']['sha256'] == PINS['failed_field'][1]
    with np.load(PINS['operator'][0], allow_pickle=False) as z:
        y, b = base.csc(z, 'y'), base.csc(z, 'b')
        indices = z['conditional_global_active_index']
    with np.load(PINS['failed_field'][0], allow_pickle=False) as z:
        voltage, current = z['active_voltage_v'], z['l25_branch_current_a']
    row_norm = np.asarray(abs(y[1:, 1:]).sum(1)).ravel()+np.asarray(abs(b[1:]).sum(1)).ravel()
    scale = 1/np.sqrt(row_norm[indices-1])
    kcl = y@voltage+b@current
    kcl[2699] -= 1.; kcl[2656] += 1.
    residual = -scale*kcl[indices]
    matrix = base.scaled_block(y[indices, :][:, indices], scale, scale)
    del y, b, voltage, current, row_norm, scale, kcl
    start = time.perf_counter(); lower, diagonal = prepare_lower(matrix)
    setup_elapsed = time.perf_counter()-start
    results = {}
    for name in ('jacobi', 'symmetric_gauss_seidel'):
        start = time.perf_counter()
        correction = residual/diagonal if name == 'jacobi' else symmetric_cycle(matrix, lower, diagonal, residual)
        elapsed = time.perf_counter()-start
        remaining = residual-matrix@correction
        assert np.all(np.isfinite(correction)) and np.all(np.isfinite(remaining))
        results[name] = {'apply_elapsed_s': elapsed,
                         'remaining_residual_norm_ratio': float(np.linalg.norm(remaining)/np.linalg.norm(residual)),
                         'remaining_diagonal_weighted_norm_ratio': float(np.linalg.norm(remaining/np.sqrt(abs(diagonal)))/np.linalg.norm(residual/np.sqrt(abs(diagonal)))),
                         'max_abs_correction_scaled': base.max_abs(correction)}
        budget.check(name)
    index = np.arange(len(residual))
    a = ((index%31)-15+1j*((index%37)-18)).astype(complex); a /= np.linalg.norm(a)
    b = ((index%41)-20+1j*((index%43)-21)).astype(complex); b /= np.linalg.norm(b)
    sa, sb = (symmetric_cycle(matrix, lower, diagonal, value) for value in (a, b))
    symmetry = float(abs(np.dot(a, sb)-np.dot(sa, b))/max(abs(np.dot(a, sb))+abs(np.dot(sa, b)), np.finfo(float).tiny))
    assert symmetry <= 2e-10
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_ACTUAL_L02_SYMMETRIC_RELAXATION_ARITHMETIC_AND_COST_ONLY',
              'driver': base.receipt(Path(__file__)), 'inputs': {name: base.receipt(path) for name, (path, _) in PINS.items()},
              'rows': matrix.shape[0], 'matrix_nnz': matrix.nnz, 'lower_nnz': lower.nnz,
              'setup_elapsed_s': setup_elapsed, 'sampled_transpose_symmetry_relative': symmetry,
              'failed_l02_scaled_residual_norm': float(np.linalg.norm(residual)), 'relaxation': results,
              'budget': budget.receipt(),
              'scope': 'One actual residual application and two symmetry probes, without LU/coarse solve or a new field iteration. Residual contraction here does not establish full preconditioned convergence or physical accuracy.'}
    base.atomic_json(output/'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
