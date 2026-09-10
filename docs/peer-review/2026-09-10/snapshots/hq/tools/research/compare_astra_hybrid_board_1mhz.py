"""Compare only a frozen, physically accepted hybrid field at the existing development point."""
import argparse
import json
from pathlib import Path

import numpy as np
from compare_astra_combined_board_1mhz import sha
from compare_astra_loaded_development_points import errors

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT/'docs/evaluation-research/astra_combined_r_gc_1mhz_comparison_2026-09-09.json'
CONTROL_SHA = 'bb0e8daa519ffd3be44d8b8cb149e082e14badca4b9962c2986ca276c0656f06'
OPERATOR_SHA = '5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'
VALIDATOR_SHA = 'fd28d5a3eef17d7d85e0fab52de1a88e1910faa2ad46a4cde3b16d3607c8abd9'
DRIVER_SHA = '91e792487f371d14c21e9ce6729f17b34b93386f9f1d0c599302b129d2d755a6'
GATES = {'csc_kcl', 'physical_source_kcl', 'l25_constitutive', 'l02_local_constitutive',
         'l02_global_constitutive', 'cell_kcl', 'global_current_cell_kcl',
         'shared_facet_jump_before_averaging', 'local_global_rt0_joule',
         'local_rt0_joule_passivity', 'backward', 'finite_forward_bound',
         'source_forward_bound', 'passivity', 'physical_power', 'matrix_power'}


def require_accepted(actual):
    if (actual.get('status') != 'COMPLETED_CONDITIONAL_HYBRID_BLOCK_LGMRES_1MHZ'
            or actual.get('lgmres', {}).get('info') != 0
            or not actual['lgmres']['final_scaled_residual_relative'] <= 1e-9
            or actual['frequency_hz'] != 1e6):
        raise ValueError('A converged hybrid field is required before reference access')
    physical = actual['physical']
    if (physical['status'] != 'PASS_CONDITIONAL_HYBRID_PHYSICAL_FIELD'
            or physical['validator_sha256'] != VALIDATOR_SHA
            or set(physical['gates']) != GATES
            or not all(value is True for value in physical['gates'].values())
            or actual['inputs']['conditional']['sha256'] != OPERATOR_SHA
            or actual['inputs']['physical_validator']['sha256'] != VALIDATOR_SHA
            or physical['inputs']['operator']['sha256'] != OPERATOR_SHA):
        raise ValueError('Every pinned physical gate must pass before reference access')


def self_check():
    for name in ('astra-l02-conditional-hybrid-block-lgmres-01',
                 'astra-l02-conditional-hybrid-galerkin-lgmres-01',
                 'astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01',
                 'astra-l02-conditional-hybrid-release-basis-lgmres-01'):
        actual = json.loads((ROOT/'outputs/research'/name/'unvalidated-field.json').read_bytes())
        try:
            require_accepted(actual)
        except ValueError:
            continue
        raise AssertionError('A real failed field was accepted')


def load_accepted_field(result, result_sha256):
    """Verify the numerical, physical and resource receipts before loading a field."""
    assert sha(result) == result_sha256
    actual = json.loads(result.read_bytes())
    require_accepted(actual)
    for name, digest in (('compare_astra_combined_board_1mhz.py', '08f6f671d2419ce57816ba278bc71d40ea60bd3b4e875a63ff9cd844a5286c25'),
                         ('compare_astra_loaded_development_points.py', '0e12464d1297caa9964404a2a0777d22964d6817069f752641b2d7945c8f4f4f'),
                         ('validate_astra_l02_hybrid_field.py', VALIDATOR_SHA)):
        assert sha(Path(__file__).with_name(name)) == digest
    folder = result.parent
    assert sha(folder/'driver-at-run.py') == actual['driver']['sha256'] == DRIVER_SHA
    assert json.loads((folder/'physical-diagnostic.json').read_bytes()) == actual['physical']
    reconstructed_receipt = actual['physical']['physical']['reconstructed_field']
    reconstructed = Path(reconstructed_receipt['path'])
    assert reconstructed.resolve() == (folder/'l02-reconstructed-field.npz').resolve()
    assert reconstructed.stat().st_size == reconstructed_receipt['size_bytes']
    assert sha(reconstructed) == reconstructed_receipt['sha256']
    external = json.loads((folder/'external-budget.json').read_bytes())
    assert external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0
    assert external['elapsed_s'] <= external['max_runtime_s']
    assert max(external['sampled_peak_private_bytes'], external['sampled_peak_working_set_bytes']) <= external['max_memory_bytes']
    field = Path(actual['field']['path'])
    assert field.resolve() == (folder/'field.npz').resolve() and sha(field) == actual['field']['sha256']
    p, n = 2699, 2656
    with np.load(field, allow_pickle=False) as archive:
        v = archive['active_voltage_v']
        assert v.shape == (3178104,) and np.all(np.isfinite(v)) and v[0] == 0
        assert np.array_equal(archive['source_positive_negative_gauge_active_indices'], (p, n, 0))
        assert np.array_equal(archive['source_current_amplitude_a'], (1.,))
        assert archive['conditional_operator_sha256_utf8'].tobytes().decode() == OPERATOR_SHA
        z = complex(v[p]-v[n])
    assert abs(z-complex(*actual['physical']['physical']['zdd_ohm'])) < 1e-15
    return actual, v


def main(args):
    assert not args.output.exists()
    actual, v = load_accepted_field(args.result, args.result_sha256)
    folder = args.result.parent
    p, n = 2699, 2656
    z = complex(v[p]-v[n])
    assert sha(CONTROL) == CONTROL_SHA
    control = json.loads(CONTROL.read_bytes())
    assert control['status'] == 'COMPLETED_CONDITIONAL_COMBINED_R_GC_1MHZ_COMPARISON'
    assert control['reference_port_one_based'] == 18 and control['frequency_hz'] == 1e6
    assert control['device_active_indices'] == [p, n]
    reference = complex(*control['zdd_ohm']['reference'])
    baseline = complex(*control['zdd_ohm']['l02_l14_l25_r_gc'])
    before, after = errors(baseline, reference), errors(z, reference)
    assert before == control['errors']['l02_l14_l25_r_gc']
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'COMPLETED_CONDITIONAL_HYBRID_R_GC_1MHZ_COMPARISON',
              'frequency_hz': 1e6, 'rail_id': control['rail_id'], 'reference_port_one_based': 18,
              'device_active_indices': [p, n], 'driver_sha256': sha(Path(__file__)),
              'inputs': {'result': {'path': str(args.result.resolve()), 'sha256': args.result_sha256},
                         'control': {'path': str(CONTROL), 'sha256': CONTROL_SHA},
                         'external': {'path': str(folder/'external-budget.json'), 'sha256': sha(folder/'external-budget.json')}},
              'field': actual['field'],
              'zdd_ohm': {'reference': [reference.real, reference.imag],
                          'p1_p1_rt0': [baseline.real, baseline.imag], 'hybrid_p1_rt0': [z.real, z.imag]},
              'errors': {'p1_p1_rt0': before, 'hybrid_p1_rt0': after},
              'complex_relative_error_reduction_percentage_points': 100*(before['complex_relative_error']-after['complex_relative_error']),
              'scope': 'Post-freeze comparison at one previously used development point. L02 RT0/P0 R/G/C replaces P1; no fitted coefficient, new magnetic operator, mesh convergence, independent holdout or broadband acceptance. The earlier magnetic candidate is a different model.'}
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False); stream.write('\n')
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--result', type=Path)
    parser.add_argument('--result-sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check(); print('PASS_REAL_UNVALIDATED_FIELDS_REJECTED_WITHOUT_REFERENCE_ACCESS')
    else:
        assert args.result and args.result_sha256 and args.output
        main(args)
