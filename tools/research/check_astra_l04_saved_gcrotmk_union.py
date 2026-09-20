"""SPD Decap PI Evaluator v0.23.1: independent saved-union QR review; no field solve."""
import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from check_astra_l04_two_direction_saved_fit import digest, NV, NX, TOTAL


def check(output):
    started = perf_counter()
    result = json.loads((output / 'result.json').read_bytes())
    guard = json.loads((output / 'external-budget.json').read_bytes())
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
    assert guard['sampled_peak_private_bytes'] <= 8 * 2**30
    assert guard['sampled_peak_working_set_bytes'] <= 8 * 2**30
    assert digest(output / 'driver-at-run.py') == result['inputs']['screen_driver']['sha256']
    arrays = []
    for label in ('base', 'pair'):
        item = result['inputs'][label + '_capture']
        path = Path(item['path'])
        assert digest(path) == item['sha256']
        with np.load(path, allow_pickle=False) as z:
            arrays.append((z['Z'], z['W']))
    z = np.column_stack([a[0] for a in arrays])
    w = np.column_stack([a[1] for a in arrays])
    item = result['inputs']['base_final']
    path = Path(item['path'])
    assert digest(path) == item['sha256']
    with np.load(path, allow_pickle=False) as a:
        warm, rhs = a['warm_scaled'], a['original_rhs_scaled']
    root = Path(__file__).resolve().parents[2]
    third = root / 'outputs/research/astra-l04-10mhz-three-direction-complete-current-01/three-direction-fit-before-gates.npz'
    assert digest(third) == '79c3ff3f9ab87591c5c762851f3baff1b4d76a2143e433fcb456989c123987de'
    with np.load(third, allow_pickle=False) as a:
        warm_residual = a['r3']
    norms = np.linalg.norm(w, axis=0)
    q, r = np.linalg.qr(w / norms, mode='reduced')
    scaled_coefficients, _, rank, singular = np.linalg.lstsq(r, q.conj().T @ warm_residual, rcond=1e-12)
    coefficients = scaled_coefficients / norms
    reported = np.array([complex(*v) for v in result['coefficients']])
    coefficient_relative = float(np.linalg.norm(coefficients-reported) / np.linalg.norm(coefficients))
    assert coefficient_relative <= 2e-8
    candidate = warm + z @ coefficients
    residual = warm_residual - w @ coefficients
    blocks = {}
    for name, rows in {'potential':slice(0,NV), 'l25':slice(NV,NX), 'contact':slice(NX,TOTAL),
                       'original_nonclosed':slice(0,TOTAL), 'closed':slice(TOTAL,None), 'full':slice(None)}.items():
        blocks[name] = float(np.linalg.norm(residual[rows]))
        assert np.isclose(blocks[name], result['metrics']['norms']['candidate_' + name], rtol=2e-8, atol=1e-13)
    raw = complex(np.dot(rhs, candidate))
    stationary = raw + complex(np.dot(candidate, residual))
    gap = abs(stationary - raw)
    assert abs(raw-complex(*result['raw_z_scaled_rhs_dot_unvalidated'])) <= 2e-13
    assert abs(stationary-complex(*result['stationary_j_unvalidated'])) <= 2e-13
    assert np.isclose(gap, result['stationary_raw_gap_ohm'], rtol=2e-8, atol=2e-13)
    caps = result['common_comparators']['caps']
    assert np.isclose(caps['full_residual_norm'], .8 * .6898180959975525, rtol=2e-14)
    assert np.isclose(caps['stationary_raw_gap_ohm'], .8 * .00012849617775574531, rtol=2e-14)
    assert result['original_numerical_gate'] is False and result['raw_gcrotmk_info_from_each_source'] == [1, 1]
    assert result['recommend_fresh_true_a_replay'] == bool(all(result['evidence_gates'].values()) and
        blocks['full'] <= caps['full_residual_norm'] and gap <= caps['stationary_raw_gap_ohm'])
    if result['candidate_artifact'] is not None:
        path = Path(result['candidate_artifact']['path'])
        assert digest(path) == result['candidate_artifact']['sha256']
        with np.load(path, allow_pickle=False) as a:
            assert np.linalg.norm(a['candidate_scaled']-candidate)/np.linalg.norm(candidate) <= 2e-8
            assert np.linalg.norm(a['algebraic_residual']-residual)/np.linalg.norm(residual) <= 2e-8
    report = {'status':'PASS_SAVED_UNION_QR_REVIEW', 'result_sha256':digest(output/'result.json'),
              'guard_sha256':digest(output/'external-budget.json'), 'rank':int(rank),
              'normalized_condition':float(singular[0]/singular[-1]), 'qr_coefficient_relative':coefficient_relative,
              'verified_block_norms':blocks, 'original_rhs_relative_residual':blocks['full']/float(np.linalg.norm(rhs)),
              'raw_z_unvalidated':[raw.real,raw.imag], 'stationary_j_unvalidated':[stationary.real,stationary.imag],
              'gap_ohm':gap, 'recommend_fresh_true_a_replay':result['recommend_fresh_true_a_replay'],
              'elapsed_s':perf_counter()-started, 'scope':'Saved QR evidence only; no fresh A, H or FMM and no numerical or physical acceptance.'}
    (output/'hq-saved-union-qr-verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report,allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    check(parser.parse_args().output.resolve())
