"""Measure only the actual conditional L02 principal-block LU under an external guard."""
import argparse
import json
from pathlib import Path
import time

import numpy as np

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

PINS = {
    'numerical_helper': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'conditional_operator': base.PINS['conditional'],
    'budget_helper': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}


def run(output):
    for name, (path, digest) in PINS.items():
        assert base.sha(path) == digest, name
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    budget = recon._Budget.create(180., 24.)
    events = base.Events(output/'progress.jsonl')
    with np.load(PINS['conditional_operator'][0], allow_pickle=False) as archive:
        y, b = base.csc(archive, 'y'), base.csc(archive, 'b')
        indices = archive['conditional_global_active_index']
    assert np.array_equal(base.partition(indices)[3], indices-1)
    row_norm = np.asarray(abs(y[1:, 1:]).sum(1)).ravel()+np.asarray(abs(b[1:]).sum(1)).ravel()
    scale = 1/np.sqrt(row_norm[indices-1])
    assert np.all(np.isfinite(scale)) and np.all(scale > 0)
    matrix = base.scaled_block(y[indices, :][:, indices], scale, scale)
    del y, b, row_norm, scale, indices
    assert matrix.shape == (1694809, 1694809) and matrix.nnz == 7917807
    difference = matrix-matrix.T
    symmetry = base.max_abs(difference.data)/max(base.max_abs(matrix.data), np.finfo(float).tiny)
    assert symmetry <= 1e-12
    del difference
    preflight = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
                 'status': 'READY_ISOLATED_CONDITIONAL_L02_FACTOR_ONLY',
                 'driver': base.receipt(Path(__file__)),
                 'inputs': {name: base.receipt(path) for name, (path, _) in PINS.items()},
                 'rows': matrix.shape[0], 'nnz': matrix.nnz, 'matrix_transpose_symmetry_relative': symmetry,
                 'ordering': 'COLAMD', 'row_scaling': 'exact gauge-kept Y+B mixed potential row norm',
                 'scope': 'Only the conditional L02 principal block. No other factor, global iteration, field, physical validation, FMM or reference comparison. Different matrix from the earlier full mixed LU resource failure.'}
    base.atomic_json(output/'preflight.json', preflight)
    budget.check('actual principal block ready')
    factors, reports = {}, {}
    # Reuse the frozen COLAMD LU, finite-pivot and actual 2e-8 solve-probe contract.
    base.factor_block('conditional_l02_exact', matrix, factors, reports, output, events)
    index = np.arange(matrix.shape[0])
    a = ((index%31)-15+1j*((index%37)-18)).astype(complex); a /= np.linalg.norm(a)
    b = ((index%41)-20+1j*((index%43)-21)).astype(complex); b /= np.linalg.norm(b)
    sa = factors['conditional_l02_exact'].solve(a)
    started = time.perf_counter(); sb = factors['conditional_l02_exact'].solve(b)
    apply_elapsed = time.perf_counter()-started
    inverse_symmetry = float(abs(np.dot(a, sb)-np.dot(sa, b))/max(abs(np.dot(a, sb))+abs(np.dot(sa, b)), np.finfo(float).tiny))
    result = {**preflight, 'status': 'DIAGNOSTIC_ISOLATED_CONDITIONAL_L02_FACTOR_BEFORE_FINAL_GATES',
              'factor': reports['conditional_l02_exact'], 'one_apply_elapsed_s': apply_elapsed,
              'inverse_transpose_symmetry_relative': inverse_symmetry, 'budget': budget.receipt()}
    base.atomic_json(output/'result.json', result)
    assert inverse_symmetry <= 2e-8
    budget.check('actual LU solve and transpose probes')
    result.update(status='PASS_ISOLATED_CONDITIONAL_L02_FACTOR_AND_COST_ONLY', budget=budget.receipt())
    base.atomic_json(output/'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.output.resolve())
    except Exception as error:
        if args.output.is_dir() and not (args.output/'failure.json').exists():
            base.atomic_json(args.output/'failure.json', {'status': 'STOP_ISOLATED_CONDITIONAL_L02_FACTOR', 'error': f'{type(error).__name__}: {error}'})
        raise
