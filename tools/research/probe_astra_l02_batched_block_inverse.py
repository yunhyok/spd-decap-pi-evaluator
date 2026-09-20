"""Reuse saved small components; invert by size and apply with one CSR matvec."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import probe_astra_l02_symmetric_relaxation as previous
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
BLOCK = ROOT/'outputs/research/astra-l02-strong-block-smoother-01/strong-block-smoother.npz'
BLOCK_SHA = '117a4d48f0286b556938dfc00b622e0824c7317171e294a70b5d059f60c6cd31'


def block_inverse(block, labels):
    sizes = np.bincount(labels)
    # ponytail: dense batches are bounded to 64 rows; larger components need another smoother.
    assert sizes.min() > 0 and sizes.max() <= 64
    assert len(labels) == block.shape[0] == block.shape[1]
    order = np.argsort(labels, kind='stable')
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    position = np.empty(len(labels), dtype=np.int32)
    position[order] = np.arange(len(labels))-np.repeat(starts, sizes)
    coo = block.tocoo()
    assert np.array_equal(labels[coo.row], labels[coo.col])
    entry_component = labels[coo.row]
    entry_size = sizes[entry_component]
    indptr = np.r_[0, np.cumsum(sizes[labels])]
    indices = np.empty(indptr[-1], dtype=np.int32)
    values = np.empty(indptr[-1], dtype=complex)
    batch_index = np.empty(len(sizes), dtype=np.int32)
    for size in np.unique(sizes):
        components = np.flatnonzero(sizes == size)
        batch_index[components] = np.arange(len(components))
        nodes = order[starts[components, None]+np.arange(size)]
        selected = entry_size == size
        dense = np.zeros((len(components), size, size), dtype=complex)
        dense[batch_index[entry_component[selected]], position[coo.row[selected]],
              position[coo.col[selected]]] = coo.data[selected]
        inverse = np.linalg.inv(dense)
        assert np.all(np.isfinite(inverse))
        slots = indptr[nodes, None]+np.arange(size)
        indices[slots] = nodes[:, None, :]
        values[slots] = inverse
    result = sparse.csr_matrix((values, indices, indptr), shape=block.shape)
    result.sort_indices()
    return result


def self_check():
    a = np.array([[3+1j, .5-.2j], [.5-.2j, 4+2j]])
    b = np.array([[4+2j, .1j, .2], [.1j, 3+1j, .3j], [.2, .3j, 2+1j]])
    matrix = sparse.block_diag(([2+.4j], a, b), format='csr')
    permutation = np.array([4, 1, 0, 5, 2, 3])
    matrix = matrix[permutation, :][:, permutation]
    labels = np.repeat(np.arange(3), [1, 2, 3])[permutation]
    actual = block_inverse(matrix, labels).toarray()
    assert np.max(abs(actual-np.linalg.inv(matrix.toarray()))) < 1e-13
    assert np.max(abs(actual-actual.T)) < 1e-13


def run(output):
    self_check()
    assert base.sha(Path(previous.__file__)) == '102f50b86a9e13f59e2c93219ee5f07cb396d931881b5d9e14e7529c0ae983a3'
    for name, (path, digest) in previous.PINS.items():
        assert base.sha(path) == digest, name
    assert base.sha(BLOCK) == BLOCK_SHA
    budget = recon._Budget.create(120., 4.)
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(BLOCK, allow_pickle=False) as z:
        block = base.csc(z, 'block')
        indices, scale, labels = (z[key] for key in
            ('conditional_global_active_indices', 'l02_mixed_row_scale', 'component_labels'))
        assert np.array_equal(z['coupling_threshold'], [.5])
    start = time.perf_counter()
    inverse = block_inverse(block, labels)
    setup_elapsed = time.perf_counter()-start
    budget.check('batched component inverses')
    with np.load(previous.PINS['operator'][0], allow_pickle=False) as z:
        y, b = base.csc(z, 'y'), base.csc(z, 'b')
        assert np.array_equal(indices, z['conditional_global_active_index'])
    row_norm = np.asarray(abs(y[1:, 1:]).sum(1)).ravel()+np.asarray(abs(b[1:]).sum(1)).ravel()
    assert np.array_equal(scale, 1/np.sqrt(row_norm[indices-1]))
    matrix = base.scaled_block(y[indices, :][:, indices], scale, scale)
    with np.load(previous.PINS['failed_field'][0], allow_pickle=False) as z:
        kcl = y@z['active_voltage_v']+b@z['l25_branch_current_a']
    kcl[2699] -= 1.; kcl[2656] += 1.
    residual = -scale*kcl[indices]
    del y, b, row_norm, kcl
    index = np.arange(len(indices))
    probe = ((index%31)-15+1j*((index%37)-18)).astype(complex)
    solved = inverse@probe
    probe_error = float(np.linalg.norm(block@solved-probe)/np.linalg.norm(probe))
    start = time.perf_counter(); correction = inverse@residual
    apply_elapsed = time.perf_counter()-start
    remaining = residual-matrix@correction
    a = probe/np.linalg.norm(probe)
    b = ((index%41)-20+1j*((index%43)-21)).astype(complex); b /= np.linalg.norm(b)
    sa, sb = inverse@a, inverse@b
    symmetry = float(abs(np.dot(a, sb)-np.dot(sa, b))/max(abs(np.dot(a, sb))+abs(np.dot(sa, b)), np.finfo(float).tiny))
    assert np.all(np.isfinite(correction)) and np.all(np.isfinite(remaining))
    artifact = output/'block-inverse.npz'
    base.atomic_npz(artifact, inverse_data=inverse.data, inverse_indices=inverse.indices,
                    inverse_indptr=inverse.indptr, inverse_shape=np.asarray(inverse.shape),
                    conditional_global_active_indices=indices, l02_mixed_row_scale=scale)
    budget.check('saved inverse and actual residual application')
    sizes = np.bincount(labels)
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'DIAGNOSTIC_BOUNDED_BLOCK_INVERSE_BEFORE_GATES',
              'driver': base.receipt(Path(__file__)), 'block_input': base.receipt(BLOCK),
              'inputs': {name: base.receipt(path) for name, (path, _) in previous.PINS.items()},
              'output': base.receipt(artifact), 'rows': len(indices), 'block_nnz': block.nnz,
              'inverse_nnz': inverse.nnz, 'inverse_sparse_bytes': sum(v.nbytes for v in (inverse.data, inverse.indices, inverse.indptr)),
              'component_count': len(sizes), 'largest_component_size': int(sizes.max()),
              'setup_elapsed_s': setup_elapsed, 'apply_elapsed_s': apply_elapsed,
              'inverse_probe_relative_residual': probe_error,
              'sampled_transpose_symmetry_relative': symmetry,
              'remaining_residual_norm_ratio': float(np.linalg.norm(remaining)/np.linalg.norm(residual)),
              'remaining_diagonal_weighted_norm_ratio': float(np.linalg.norm(remaining/np.sqrt(abs(matrix.diagonal())))/np.linalg.norm(residual/np.sqrt(abs(matrix.diagonal())))),
              'budget': budget.receipt(),
              'scope': 'Batched exact inverses of saved size-limited components. No full solve, coarse factor, physical field acceptance, or PowerSI comparison. Physical matrix remains unchanged.'}
    base.atomic_json(output/'result.json', result)
    assert probe_error <= 2e-8 and symmetry <= 2e-10
    result['status'] = 'PASS_ACTUAL_L02_BATCHED_BLOCK_INVERSE_ARITHMETIC_AND_COST_ONLY'
    base.atomic_json(output/'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
