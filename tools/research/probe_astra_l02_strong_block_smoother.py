"""Build and test a small-component block smoother on the actual failed residual."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import probe_astra_l02_symmetric_relaxation as previous
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]


def run(output):
    assert base.sha(Path(previous.__file__)) == '102f50b86a9e13f59e2c93219ee5f07cb396d931881b5d9e14e7529c0ae983a3'
    for name, (path, digest) in previous.PINS.items():
        assert base.sha(path) == digest, name
    budget = recon._Budget.create(120., 4.)
    with np.load(previous.PINS['operator'][0], allow_pickle=False) as z:
        y, b = base.csc(z, 'y'), base.csc(z, 'b')
        indices = z['conditional_global_active_index']
    with np.load(previous.PINS['failed_field'][0], allow_pickle=False) as z:
        voltage, current = z['active_voltage_v'], z['l25_branch_current_a']
    row_norm = np.asarray(abs(y[1:, 1:]).sum(1)).ravel()+np.asarray(abs(b[1:]).sum(1)).ravel()
    scale = 1/np.sqrt(row_norm[indices-1])
    kcl = y@voltage+b@current
    kcl[2699] -= 1.; kcl[2656] += 1.
    residual = -scale*kcl[indices]
    matrix = base.scaled_block(y[indices, :][:, indices], scale, scale)
    del y, b, voltage, current, row_norm, kcl
    diagonal = abs(matrix.diagonal())
    assert np.all(diagonal > 0)
    upper = sparse.triu(matrix, k=1, format='coo')
    ratio = abs(upper.data)/np.sqrt(diagonal[upper.row]*diagonal[upper.col])
    assert np.all(np.isfinite(ratio))
    selected = ratio >= .5
    graph = sparse.coo_matrix((np.ones(np.count_nonzero(selected), np.int8),
                              (upper.row[selected], upper.col[selected])), shape=matrix.shape).tocsr()
    components, labels = connected_components(graph, directed=False)
    sizes = np.bincount(labels)
    assert int(sizes.max()) <= 64, 'component factor cost ceiling exceeded'
    coo = matrix.tocoo(); within = labels[coo.row] == labels[coo.col]
    # Keep all original entries within each selected component, not just strong edges.
    block = sparse.coo_matrix((coo.data[within], (coo.row[within], coo.col[within])), shape=matrix.shape).tocsc()
    assert np.array_equal(block.diagonal(), matrix.diagonal())
    del upper, ratio, selected, graph, coo, within
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    artifact = output/'strong-block-smoother.npz'
    base.atomic_npz(artifact, block_data=block.data, block_indices=block.indices,
                    block_indptr=block.indptr, block_shape=np.asarray(block.shape),
                    conditional_global_active_indices=indices, l02_mixed_row_scale=scale,
                    component_labels=labels, coupling_threshold=np.asarray((.5,)))
    budget.check('component matrix assembled and saved')
    start = time.perf_counter(); factor = splu(block, permc_spec='NATURAL')
    factor_elapsed = time.perf_counter()-start
    pivots = abs(factor.U.diagonal())
    assert np.all(np.isfinite(pivots)) and np.all(pivots > 0)
    index = np.arange(matrix.shape[0])
    probe = ((index%31)-15+1j*((index%37)-18)).astype(complex)
    solved = factor.solve(probe)
    probe_error = float(np.linalg.norm(block@solved-probe)/np.linalg.norm(probe))
    assert probe_error <= 2e-8
    start = time.perf_counter(); correction = factor.solve(residual)
    apply_elapsed = time.perf_counter()-start
    remaining = residual-matrix@correction
    a = probe/np.linalg.norm(probe)
    b = ((index%41)-20+1j*((index%43)-21)).astype(complex); b /= np.linalg.norm(b)
    sa, sb = factor.solve(a), factor.solve(b)
    symmetry = float(abs(np.dot(a, sb)-np.dot(sa, b))/max(abs(np.dot(a, sb))+abs(np.dot(sa, b)), np.finfo(float).tiny))
    assert symmetry <= 2e-10
    budget.check('single small-block factor and actual residual application')
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_ACTUAL_L02_STRONG_BLOCK_SMOOTHER_FACTOR_AND_COST_ONLY',
              'driver': base.receipt(Path(__file__)),
              'inputs': {name: base.receipt(path) for name, (path, _) in previous.PINS.items()},
              'output': base.receipt(artifact), 'rows': matrix.shape[0], 'matrix_nnz': matrix.nnz,
              'block_nnz': block.nnz, 'threshold': .5, 'component_count': int(components),
              'largest_component_size': int(sizes.max()), 'singleton_components': int(np.count_nonzero(sizes == 1)),
              'factor_elapsed_s': factor_elapsed, 'factor_l_nnz': factor.L.nnz, 'factor_u_nnz': factor.U.nnz,
              'factor_probe_relative_residual': probe_error, 'factor_minimum_pivot_abs': float(pivots.min()),
              'apply_elapsed_s': apply_elapsed, 'sampled_transpose_symmetry_relative': symmetry,
              'remaining_residual_norm_ratio': float(np.linalg.norm(remaining)/np.linalg.norm(residual)),
              'remaining_diagonal_weighted_norm_ratio': float(np.linalg.norm(remaining/np.sqrt(diagonal))/np.linalg.norm(residual/np.sqrt(diagonal))),
              'budget': budget.receipt(),
              'scope': 'Only the preconditioner changes: all physical matrix entries remain in the original operator. One residual application is not full convergence or physical/PowerSI acceptance. The .5 coupling threshold is an algebraic cost choice independent of reference data.'}
    base.atomic_json(output/'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
