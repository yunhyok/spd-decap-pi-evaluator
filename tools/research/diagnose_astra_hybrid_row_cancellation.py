"""Compare two algebraically identical saved-Y actions; never accept or solve a field."""
import argparse
from decimal import Decimal, localcontext
import json
from math import fsum
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse
import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    'numeric_helper': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'budget_helper': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
    'operator': (R/'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz', '5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'),
    'd5_final': (R/'astra-l02-conditional-hybrid-release-basis-lgmres-01/unvalidated-field.npz', 'd5bd9859cb6ea8c46745051da9c026f79c8d9bfa705bba9a213f349a060057ca'),
    'd5_json': (R/'astra-l02-conditional-hybrid-release-basis-lgmres-01/unvalidated-field.json', 'e77b5f7054d49ab8b2e4a7c388162c3da23fcf3acded6b50b399a121004bfc79'),
    'ed_final': (R/'astra-l02-conditional-hybrid-release-basis-continuation-01/unvalidated-field.npz', 'ed1f8390c9c2408a839a81356f3605ce67dd2ce05c8f3206b40c5f9443ea4540'),
    'ed_json': (R/'astra-l02-conditional-hybrid-release-basis-continuation-01/unvalidated-field.json', '9b4c8460d7b0b0c541e9c29c9c0332a75da23b5099cc249a3ec87f247e73b349'),
}


def row_sums(matrix, budget=None):
    result = np.empty(matrix.shape[0], dtype=np.complex128)
    real, imag, ptr = matrix.data.real, matrix.data.imag, matrix.indptr
    for row in range(len(result)):
        a, b = ptr[row:row+2]
        result[row] = complex(fsum(real[a:b]), fsum(imag[a:b]))
        if budget is not None and row % 50000 == 0:
            budget.check('exact stored row sums')
    return result


def difference_action(matrix, sums, value, tile=50000):
    answer = sums*value
    for first in range(0, len(value), tile):
        last = min(first+tile, len(value))
        ptr = matrix.indptr[first:last+1]
        count = np.diff(ptr)
        nonempty = np.flatnonzero(count)
        if not len(nonempty):
            continue
        rows = np.repeat(np.arange(first, last), count)
        a, b = int(ptr[0]), int(ptr[-1])
        terms = matrix.data[a:b]*(value[matrix.indices[a:b]]-value[rows])
        answer[first+nonempty] += np.add.reduceat(terms, ptr[nonempty]-a)
    return answer


def decimal_row(matrix, row, value):
    """50-digit products and sum of exactly converted binary64 inputs."""
    re, im = Decimal(0), Decimal(0)
    for k in range(matrix.indptr[row], matrix.indptr[row+1]):
        a, b = matrix.data[k], value[matrix.indices[k]]
        ar, ai = Decimal.from_float(float(a.real)), Decimal.from_float(float(a.imag))
        br, bi = Decimal.from_float(float(b.real)), Decimal.from_float(float(b.imag))
        re += ar*br-ai*bi
        im += ar*bi+ai*br
    return re, im


def self_check():
    matrix = sparse.csr_matrix(np.array([[3+.1j, -2, 0, 0], [0, 0, 0, 0],
                                        [1j, 0, 2, -1], [0, .2j, 0, 1]], complex))
    value = np.array([1+.3j, 2-.2j, .5+1j, -.2j])
    sums = row_sums(matrix)
    assert np.max(abs(difference_action(matrix, sums, value, 2)-matrix@value)) < 2e-15
    assert sums[0] == 1+.1j and sums[1] == 0


def run(output):
    budget = recon._Budget.create(90, 4)
    self_check()
    for name, (path, digest) in PINS.items():
        assert base.sha(path) == digest, name
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(PINS['operator'][0], allow_pickle=False) as z:
        y = base.csc(z, 'y')[1:, 1:].tocsc()
        b = base.csc(z, 'b')[1:, :].tocsc()
        resistance = base.csc(z, 'r')
        parts = base.partition(z['conditional_global_active_index'])
    scale = 1/np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel()+np.asarray(abs(b).sum(axis=1)).ravel())
    qscale = 1/np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel()+np.asarray(abs(resistance).sum(axis=1)).ravel())
    rhs = np.zeros(y.shape[0], dtype=complex)
    rhs[2698], rhs[2655] = 1, -1
    norm_rhs = np.linalg.norm(scale*rhs)
    csr, bcsr = y.tocsr(), b.tocsr()
    started = perf_counter()
    sums = row_sums(csr, budget)
    sums_seconds = perf_counter()-started
    reports = {}
    for name in ('d5', 'ed'):
        saved = json.loads(PINS[name+'_json'][0].read_bytes())
        assert saved['status'] == 'UNVALIDATED_CONDITIONAL_HYBRID_BLOCK_LGMRES_FIELD_BEFORE_PHYSICAL_GATES'
        assert saved['field']['sha256'] == PINS[name+'_final'][1]
        with np.load(PINS[name+'_final'][0], allow_pickle=False) as z:
            full, q = z['active_voltage_v'], z['l25_branch_current_a']
            assert full.shape == (3178104,) and full[0] == 0 and q.shape == (604031,)
            assert np.all(np.isfinite(full)) and np.all(np.isfinite(q))
            assert np.array_equal(z['source_positive_negative_gauge_active_indices'], (2699, 2656, 0))
            assert np.array_equal(z['source_current_amplitude_a'], (1.,))
            assert z['conditional_operator_sha256_utf8'].tobytes().decode() == PINS['operator'][1]
            # Reproduce the solver's saved-voltage -> scaled -> physical round trip.
            v, q = scale*(full[1:]/scale), qscale*(q/qscale)
        ordinary = y@v+b@q-rhs
        started = perf_counter()
        stable = difference_action(csr, sums, v)+b@q-rhs
        seconds = perf_counter()-started
        constitutive = qscale*(b.T@v-resistance@q)
        metrics = {}
        for label, residual in (('ordinary', ordinary), ('difference', stable)):
            weighted = scale*residual
            metrics[label] = {'scaled_residual_relative': float(np.linalg.norm(np.r_[weighted, constitutive])/norm_rhs),
                'max_kcl_a': base.max_abs(residual),
                'block_squared_norms': {key: float(np.vdot(weighted[ix], weighted[ix]).real)
                    for key, ix in zip(('native', 'l14', 'l25', 'l02'), parts, strict=True)}}
        top = np.unique(np.r_[2655, *(ix[np.argmax(abs(scale[ix]*ordinary[ix]))] for ix in parts)])
        witnesses = []
        with localcontext() as context:
            context.prec = 50
            for row in top:
                yr, yi = decimal_row(csr, row, v)
                br, bi = decimal_row(bcsr, row, q)
                exact = complex(float(yr+br-Decimal.from_float(float(rhs[row].real))), float(yi+bi))
                witnesses.append({'active_row': int(row+1), 'decimal50_residual_a': base.pair(exact),
                    'ordinary_error_a': base.pair(ordinary[row]-exact), 'difference_error_a': base.pair(stable[row]-exact),
                    'stored_y_row_sum_s': base.pair(sums[row])})
        reports[name] = {'metrics': metrics, 'difference_action_seconds': seconds,
                         'action_difference_scaled_relative': float(np.linalg.norm(scale*(ordinary-stable))/norm_rhs),
                         'decimal50_witnesses': witnesses}
        budget.check('two frozen final field actions')
    target = output/'stored-y-row-sums.npz'
    base.atomic_npz(target, row_sums_s=sums)
    report = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
        'status': 'COMPLETED_UNVALIDATED_HYBRID_ROW_CANCELLATION_DIAGNOSTIC',
        'driver': base.receipt(output/'driver-at-run.py'), 'inputs': {k: base.receipt(p) for k, (p, _) in PINS.items()},
        'row_sum_seconds': sums_seconds, 'reports': reports, 'row_sums': base.receipt(target),
        'budget': budget.receipt(), 'scope': 'Same coalesced stored Y entries, actual row sums retained, same gauge and B/R/source; different floating summation only. Decimal50 uses exact binary64 inputs for selected products and sums. No solve, physical acceptance, gate relaxation, reference access or accuracy claim.'}
    base.atomic_json(output/'result.json', report)
    print(json.dumps(report, allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
