"""SPD Decap PI Evaluator v0.23.1: saved bilinear residual attribution; no field solve."""
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from check_astra_l04_two_direction_saved_fit import digest, NV, NX, TOTAL

RUNS = {
    'real_h': ('astra-l04-10mhz-complete-current-gcrotmk-01',
               '17eec05e5f710020703a40ec62916d3a01c563fa895d3cde1463979e1a2c4f25',
               '85e8db88de6a07024a7076549d53e7fc4060789cffbca39f229e025ff4fdaa91'),
    'diagonal_homega': ('astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01',
                        'ef23ddf9c0a81f5ef0e234932592ea6403685f022c7e3dc1c04bd4517c488f03',
                        'db4a1c4a1d1b1a1279616419900b2a98380c4ac2d8a3a25727f97fb108dc07f1'),
    'forward_homega': ('astra-l04-10mhz-forward-closed-gcrotmk-01',
                       '30045d8ac4540b096f9b26c75238c5d9e98e1375efc3fb1c5f4b888141fd2128',
                       'f971eacc2f05b8cc4508960dc56b219a0de9ab9fb03e1d19bf0252d73b5ea2f2'),
}
BLOCKS = {'potential': slice(0, NV), 'l25': slice(NV, NX),
          'contact': slice(NX, TOTAL), 'closed': slice(TOTAL, None)}


def pair(value):
    return [float(value.real), float(value.imag)]


def attribute(x, residual, blocks):
    result = {}
    full_squared = float(np.vdot(residual, residual).real)
    for name, rows in blocks.items():
        term = np.dot(x[rows], residual[rows])  # Ordinary transpose, as in stationary J.
        norm = float(np.linalg.norm(residual[rows]))
        result[name] = {'bilinear_ohm': pair(term), 'absolute_ohm': float(abs(term)),
                        'residual_norm': norm, 'residual_squared_share': norm**2 / full_squared}
    total = sum(complex(*row['bilinear_ohm']) for row in result.values())
    assert abs(total - np.dot(x, residual)) <= 2e-14 * max(1.0, abs(total))
    assert abs(sum(row['residual_squared_share'] for row in result.values()) - 1.0) <= 2e-12
    return result, total


def main():
    started = perf_counter()
    # Complex counterexample fails if attribution accidentally uses a Hermitian dot.
    x = np.array([1+2j, 3-1j]); residual = np.array([2-3j, -1+4j])
    _, total = attribute(x, residual, {'a': slice(0, 1), 'b': slice(1, None)})
    assert abs(total - (9+14j)) < 1e-13 and abs(total - np.vdot(x, residual)) > 1
    root = Path(__file__).resolve().parents[2] / 'outputs/research'
    report = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1', 'runs': {}}
    for name, (folder, result_sha, final_sha) in RUNS.items():
        output = root / folder
        assert digest(output / 'result.json') == result_sha
        assert digest(output / 'complete-current-final.npz') == final_sha
        result = json.loads((output / 'result.json').read_bytes())
        with np.load(output / 'complete-current-final.npz', allow_pickle=False) as z:
            x, residual = z['candidate_scaled'], z['final_true_residual']
        assert x.shape == residual.shape == (4465281,)
        assert np.isfinite(x).all() and np.isfinite(residual).all()
        blocks, gap = attribute(x, residual, BLOCKS)
        reported_gap = complex(*result['stationary_j_unvalidated']) - complex(*result['raw_z_unvalidated'])
        assert abs(gap - reported_gap) <= 2e-14
        assert np.isclose(np.linalg.norm(residual), result['metrics']['norms']['candidate_full'], rtol=2e-12)
        report['runs'][name] = {'result_sha256': result_sha, 'final_sha256': final_sha,
                               'blocks': blocks, 'total_j_minus_z_ohm': pair(gap),
                               'gap_ohm': float(abs(gap)),
                               'sum_absolute_terms_over_gap': sum(v['absolute_ohm'] for v in blocks.values()) / abs(gap)}
        del x, residual
    report.update(status='PASS_SAVED_BILINEAR_ATTRIBUTION', elapsed_s=perf_counter()-started,
                  scope='Algebraic attribution only; no causal isolation, physical acceptance, or accuracy claim. No new H/FMM/full A.')
    target = root / RUNS['forward_homega'][0] / 'hq-saved-stationary-blocks.json'
    assert not target.exists(), 'Do not overwrite an existing audit.'
    target.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report, allow_nan=False))


if __name__ == '__main__':
    main()
