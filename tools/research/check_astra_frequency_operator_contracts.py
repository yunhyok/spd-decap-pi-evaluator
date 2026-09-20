"""SPD Decap PI Evaluator v0.23.1: saved frequency contracts, no solve."""
import json
from pathlib import Path
import time
import numpy as np
import assemble_astra_full_contact_frequency_operators_v2 as source


def main():
    started = time.monotonic()
    result_path = source.R/'astra-full-contact-frequency-operators-02/result.json'
    guard_path = result_path.with_name('external-budget.json')
    inputs = {
        'result': source.receipt(result_path, '7efcd3d999162b27311136a462ef5335071c00e371c6bbe6ac1333d7a98f6555'),
        'guard': source.receipt(guard_path, 'ff816c7f857cdd4d6a77f6f57a5331943770b9c3af29f5b7a691143b9fdc0a1c'),
        'source': source.receipt(Path(source.__file__), '9218fdd6712f9ed4c9bf5c30815f3730d02bab50cc22ac93c9e665d4a2b779e4'),
        'original': source.receipt(*source.PINS['old_operator']),
        'stamps': source.receipt(*source.PINS['stamps']),
    }
    result = json.loads(result_path.read_bytes())
    guard = json.loads(guard_path.read_bytes())
    assert result['status'] == 'ASSEMBLED_CONDITIONAL_FULL_CONTACT_FREQUENCY_OPERATORS_NO_SOLVE'
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
    assert guard['driver_sha256'] == inputs['source']['sha256'] == result['driver']['sha256']
    budget = source.recon._Budget.create(45., 3.)
    points = []
    with np.load(source.PINS['old_operator'][0], allow_pickle=False) as old, np.load(source.PINS['stamps'][0], allow_pickle=False) as stamps:
        topology = ['conditional_global_active_index', 'conditional_to_full_face_transfer_row_index',
                    'positive_active_index', 'negative_active_index', 'gauge_active_index']
        fixed_keys = [prefix+'_'+suffix for prefix in ('r', 'b') for suffix in ('data', 'indices', 'indptr', 'shape')]
        original = {key: old[key] for key in topology+fixed_keys}
        for point in result['points']:
            f = point['frequency_hz']
            source.receipt(Path(point['artifact']['path']), point['artifact']['sha256'])
            source.receipt(Path(point['l04_bridge_inputs']['path']), point['l04_bridge_inputs']['sha256'])
            with np.load(point['artifact']['path'], allow_pickle=False) as board, np.load(point['l04_bridge_inputs']['path'], allow_pickle=False) as bridge:
                assert np.array_equal(board['frequency_hz'], [f]) and np.array_equal(bridge['frequency_hz'], [f])
                assert np.array_equal(board['source_current_a'], [1.]) and np.array_equal(bridge['source_current_a'], [1.])
                assert all(np.array_equal(board[key], value) for key, value in original.items())
                index = int(np.flatnonzero(stamps['frequencies_hz'] == f).item())
                assert index in (1, 2)
                for key, saved_key, bridge_key in (
                    ('native_partial_dispersion_s_per_f', 'partial_dispersion_s_per_f_by_frequency', 'native_partial_dispersion_s_per_f'),
                    ('termination_admittance_s', 'termination_admittance_s_by_frequency', 'native_termination_admittance_s'),
                ):
                    assert np.array_equal(board[key], stamps[saved_key][index])
                    assert np.array_equal(board[key], bridge[bridge_key])
                    assert not np.array_equal(board[key], stamps[saved_key][0])
                assert np.all(np.isfinite(board['finite_admittance_s'])) and np.all(board['finite_admittance_s'].real > 0)
                u = source.cells.read_csc(bridge, 'l04_u')
                delta = source.cells.read_csc(bridge, 'l04_delta')
                d = bridge['l04_contact_diagonal_admittance_s']
                assert u.shape == (source.N, 38277) and delta.shape == (source.N, source.N)
                assert np.all(np.isfinite(d)) and np.all(d.real > 0)
                u_kcl = float(np.max(abs(np.asarray(u.sum(axis=0)))) / np.max(abs(u.data)))
                delta_kcl = float(np.max(abs(np.asarray(delta.sum(axis=1)))) / np.max(abs(delta.data)))
                assert max(u_kcl, delta_kcl) < 2e-12
                assert source.difference(delta, delta.T) < 2e-13
                points.append({'frequency_hz': f, 'fixed_topology_r_b_bitwise_equal': True,
                               'frequency_sources_bitwise_equal': True,
                               'ungauged_l04_u_column_kcl_relative': u_kcl,
                               'ungauged_l04_delta_row_kcl_relative': delta_kcl})
            budget.check('saved frequency contracts')
    output = source.R/'astra-frequency-operator-contract-check-20260910.json'
    report = {'program': source.PROGRAM, 'version': source.VERSION,
              'status': 'PASS_SAVED_FREQUENCY_CONTRACTS_NO_SOLVE',
              'driver': source.receipt(Path(__file__)), 'inputs': inputs, 'points': points,
              'elapsed_seconds': time.monotonic()-started, 'budget': budget.receipt(),
              'scope': 'Metadata, fixed topology/R/B and actual frequency input consistency only; no field, physical acceptance or accuracy claim.'}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
