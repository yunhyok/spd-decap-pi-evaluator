"""SPD Decap PI Evaluator v0.23.1: saved public-GCROT evidence review, no field action."""
import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from check_astra_l04_two_direction_saved_fit import digest, NV, NX, TOTAL


def check(output, paired_baseline=None, best_of=None):
    started = perf_counter()
    result = json.loads((output / 'result.json').read_bytes())
    guard = json.loads((output / 'external-budget.json').read_bytes())
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
    assert digest(output / 'driver-at-run.py') == guard['driver_sha256']
    assert guard['sampled_peak_private_bytes'] <= guard['max_memory_bytes']
    assert guard['sampled_peak_working_set_bytes'] <= guard['max_memory_bytes']
    final_path = output / 'complete-current-final.npz'
    capture_path = output / 'gcrot-captures-before-final.npz'
    capture_receipt = output / 'gcrot-captures-before-final.json'
    assert digest(final_path) == result['artifact']['sha256']
    assert digest(capture_receipt) == result['captures']['sha256']
    capture_report = json.loads(capture_receipt.read_bytes())
    assert digest(capture_path) == capture_report['artifact']['sha256']
    with np.load(capture_path, allow_pickle=False) as z:
        directions, actions, correction = z['Z'], z['W'], z['correction']
        reported_ad = z['A_delta']
    with np.load(final_path, allow_pickle=False) as z:
        warm, candidate = z['warm_scaled'], z['candidate_scaled']
        rhs, true_a, residual = z['original_rhs_scaled'], z['final_true_action'], z['final_true_residual']
        algebraic = z['algebraic_residual']
        assert np.array_equal(correction, z['correction_scaled'])
        assert z['candidate_base_physical'].shape == (TOTAL,)
        assert z['candidate_psi_physical'].shape == (644870,)
        assert z['l04_branch_current_a'].shape == (2272974,)
    assert directions.shape == actions.shape == (4465281, result['counts']['M'])
    assert all(np.isfinite(v).all() for v in (directions, actions, correction, candidate, rhs, true_a, residual))
    assert np.array_equal(warm + correction, candidate)
    assert np.array_equal(rhs - true_a, residual)
    norm = np.linalg.norm
    column_norms = norm(directions, axis=0)
    q, r = np.linalg.qr(directions / column_norms, mode='reduced')
    coefficients = np.linalg.solve(r, q.conj().T @ correction) / column_norms
    correction_relative = float(norm(directions @ coefficients - correction) / norm(correction))
    ad = actions @ coefficients
    action_relative = float(norm(ad - reported_ad) / norm(reported_ad))
    assert correction_relative <= 2e-8 and action_relative <= 2e-8
    root = Path(__file__).resolve().parents[2]
    third = root / 'outputs/research/astra-l04-10mhz-three-direction-complete-current-01/three-direction-fit-before-gates.npz'
    assert digest(third) == '79c3ff3f9ab87591c5c762851f3baff1b4d76a2143e433fcb456989c123987de'
    with np.load(third, allow_pickle=False) as z:
        warm_r = z['r3']
        assert np.array_equal(warm[:TOTAL], z['candidate_base_scaled'])
    independent_algebraic = warm_r - ad
    denominator = max(float(norm(residual)), float(norm(independent_algebraic)), np.finfo(float).tiny)
    replay_relative = float(norm(residual - independent_algebraic) / denominator)
    assert replay_relative <= 2e-8
    assert norm(algebraic - independent_algebraic) / denominator <= 2e-8
    raw = complex(np.dot(rhs, candidate))
    stationary = raw + complex(np.dot(candidate, residual))
    assert abs(raw - complex(*result['raw_z_unvalidated'])) <= 2e-14
    assert abs(stationary - complex(*result['stationary_j_unvalidated'])) <= 2e-14
    gap = abs(stationary - raw)
    assert np.isclose(gap, result['stationary_raw_gap_ohm'], rtol=2e-10, atol=2e-14)
    blocks = {}
    for name, rows in {'potential':slice(0,NV), 'l25':slice(NV,NX), 'contact':slice(NX,TOTAL),
                       'original_nonclosed':slice(0,TOTAL), 'closed':slice(TOTAL,None), 'full':slice(None)}.items():
        value = float(norm(residual[rows]))
        assert np.isclose(value, result['metrics']['norms']['candidate_' + name], rtol=2e-12, atol=0)
        blocks[name] = value
    full_cap, gap_cap = 1.1171139319683658, 0.00016061933005263093
    paired = None
    if paired_baseline is not None:
        baseline_path = paired_baseline / 'result.json'
        baseline_guard_path = paired_baseline / 'external-budget.json'
        assert digest(baseline_path) == '17eec05e5f710020703a40ec62916d3a01c563fa895d3cde1463979e1a2c4f25'
        assert digest(baseline_guard_path) == '19ade392a5e26be674e0b26bd580a7c5bda112e4c8440ad4221e544da4492fb7'
        baseline = json.loads(baseline_path.read_bytes())
        baseline_guard = json.loads(baseline_guard_path.read_bytes())
        full_cap = .8 * baseline['metrics']['norms']['candidate_full']
        gap_cap = .8 * baseline['stationary_raw_gap_ohm']
        assert digest(paired_baseline / 'complete-current-final.npz') == '85e8db88de6a07024a7076549d53e7fc4060789cffbca39f229e025ff4fdaa91'
        with np.load(paired_baseline / 'complete-current-final.npz',allow_pickle=False) as z:
            assert np.array_equal(warm,z['warm_scaled'])
            assert np.array_equal(rhs,z['original_rhs_scaled'])
        paired = {'full_residual_factor':blocks['full']/baseline['metrics']['norms']['candidate_full'],
                  'stationary_gap_factor':gap/baseline['stationary_raw_gap_ohm'],
                  'elapsed_factor':guard['elapsed_s']/baseline_guard['elapsed_s'],
                  'elapsed_lte_110_percent':bool(guard['elapsed_s']<=1.1*baseline_guard['elapsed_s'])}
        if best_of is not None:
            comparison_path = best_of / 'result.json'
            assert digest(comparison_path) == 'ef23ddf9c0a81f5ef0e234932592ea6403685f022c7e3dc1c04bd4517c488f03'
            assert digest(best_of / 'external-budget.json') == '1d87f15f23219a26f2e690f26ae8ad12b4bbdbd006f8d08155aef4e63bb1c111'
            assert digest(best_of / 'complete-current-final.npz') == 'db4a1c4a1d1b1a1279616419900b2a98380c4ac2d8a3a25727f97fb108dc07f1'
            comparison = json.loads(comparison_path.read_bytes())
            with np.load(best_of / 'complete-current-final.npz',allow_pickle=False) as z:
                assert np.array_equal(warm,z['warm_scaled']) and np.array_equal(rhs,z['original_rhs_scaled'])
            full_cap = .8 * min(baseline['metrics']['norms']['candidate_full'],comparison['metrics']['norms']['candidate_full'])
            gap_cap = .8 * min(baseline['stationary_raw_gap_ohm'],comparison['stationary_raw_gap_ohm'])
            paired['second_comparator_result_sha256'] = digest(comparison_path)
            paired['best_prior_full_factor'] = blocks['full'] / (full_cap/.8)
            paired['best_prior_gap_factor'] = gap / (gap_cap/.8)
    else:
        assert best_of is None, '--best-of requires --paired-baseline'
    assert result['positive_progress_screen'] == bool(blocks['full'] <= full_cap and gap <= gap_cap)
    rhs_norm = float(norm(rhs))
    assert np.isclose(rhs_norm, 0.00100108140073312, rtol=2e-12, atol=0)
    assert result['original_numerical_gate'] == bool(result['raw_gcrotmk_info'] == 0 and blocks['full'] <= 1.0010814007331201e-12)
    report = {'program':'SPD Decap PI Evaluator', 'version':'0.23.1',
              'status':'PASS_SAVED_COMPLETE_CURRENT_GCROTMK_REVIEW',
              'result_sha256':digest(output / 'result.json'), 'guard_sha256':digest(output / 'external-budget.json'),
              'final_artifact_sha256':digest(final_path), 'capture_artifact_sha256':digest(capture_path),
              'qr_correction_relative':correction_relative, 'qr_action_relative':action_relative,
              'independent_explicit_replay_relative':replay_relative, 'replay_denominator':denominator,
              'verified_block_norms':blocks, 'original_rhs_relative_residual':blocks['full']/rhs_norm,
              'raw_z_unvalidated':[raw.real,raw.imag], 'stationary_j_unvalidated':[stationary.real,stationary.imag],
              'stationary_gap_ohm':gap, 'raw_gcrotmk_info':result['raw_gcrotmk_info'],
              'expected_progress_caps':{'full':full_cap,'gap_ohm':gap_cap}, 'paired_comparison':paired,
              'positive_progress_screen':result['positive_progress_screen'], 'elapsed_s':perf_counter()-started,
              'scope':'Saved evidence only. No new H/FMM, physical acceptance, accuracy claim, or original source/reference access.'}
    (output / 'hq-saved-gcrotmk-verification.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report, allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--paired-baseline',type=Path)
    parser.add_argument('--best-of',type=Path)
    args=parser.parse_args()
    check(args.output.resolve(), args.paired_baseline.resolve() if args.paired_baseline else None,
          args.best_of.resolve() if args.best_of else None)
