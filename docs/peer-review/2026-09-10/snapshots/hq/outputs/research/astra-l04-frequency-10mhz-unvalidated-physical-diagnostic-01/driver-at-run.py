"""SPD Decap PI Evaluator v0.23.1: diagnose an unvalidated 10 MHz checkpoint."""
import argparse
import ast
import ctypes
import ctypes.wintypes
import json
from pathlib import Path
from time import perf_counter
import shutil
import subprocess
import sys
import time
import traceback
import numpy as np
from scipy import sparse
import run_astra_l14_sheet_r_shadow as l14
import validate_astra_l04_full_contact_coupled_field as original
from validate_astra_l04_full_contact_coupled_field import (
    ROOT, R, PROGRAM, VERSION, N, POSITIVE, NEGATIVE, GAUGE, L25_BRANCHES,
    L04_FREE_CELLS, fd28, recon, prior, receipt, _load_csc, _max_abs, _pair,
    _l04_replay, _branch_action)

RUN_RELEASED = True  # HQ audited source-frequency replay and the unchanged 23 physical gates.
FREQUENCY_HZ = 10_000_000.0
EXTERNAL_SECONDS, MEMORY_GIB = 330.0, 8.0
MEMORY_COUNTER_SOURCE_SHA = "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"
SOURCE_SHA = "713c02702f0d1e9629f4485af83c3b2946d6e8daf4cccaa6b028c67a39bea466"
RECOVERY_DIR = R / 'astra-l04-frequency-10mhz-unvalidated-recovery-01'
PINS = {k:v for k,v in original.PINS.items() if not k.startswith('coupled_solver_')}
PINS.update({
    'original_validator': (Path(original.__file__), SOURCE_SHA),
    'recovery_result': (RECOVERY_DIR / 'result.json', 'b059ed5a067b0244fca004c51d9797fc478e93df2397fdf30f0daafa64c6e4f4'),
    'recovery_driver': (RECOVERY_DIR / 'driver-at-run.py', '0334087871401cf664f4eea7cf66b97cdb37fdb1e49a0a06dfc1e8e0cdd6ba95'),
    'recovery_external': (RECOVERY_DIR / 'external-budget.json', '1025a11ca14a0111894667fcd4dfb223225ec2d42fa351a0de84c74a96fadba6'),
    'recovery_field': (RECOVERY_DIR / 'recovered-unvalidated-full-contact-l04-10mhz-field.npz', 'c60f3f67ac7b8914ab36fa1e9d273c4be20f259817347517cb8117c032163443'),
    'frequency_operator_result': (R/'astra-full-contact-frequency-operators-02/result.json', '7efcd3d999162b27311136a462ef5335071c00e371c6bbe6ac1333d7a98f6555'),
    'frequency_operator_driver': (R/'astra-full-contact-frequency-operators-02/driver-at-run.py', '9218fdd6712f9ed4c9bf5c30815f3730d02bab50cc22ac93c9e665d4a2b779e4'),
    'frequency_operator_external': (R/'astra-full-contact-frequency-operators-02/external-budget.json', 'ff816c7f857cdd4d6a77f6f57a5331943770b9c3af29f5b7a691143b9fdc0a1c'),
    'frequency_operator': (R/'astra-full-contact-frequency-operators-02/frequency-10000000-conditional-operator.npz', '7d528de4ced4e24e6fbf1c0ba437276ce8ccc706684e36028f63494685171701'),
    'frequency_bridge_result': (R/'astra-l04-frequency-partial-inputs-01/result.json', 'a0117fe7cfe66646ac09d2450ed2be283e7754bce728dc8c33d9916c4b2d2958'),
    'frequency_bridge_driver': (R/'astra-l04-frequency-partial-inputs-01/driver-at-run.py', 'b32c738ba610319492be737d488c91bea77a3cc79a8063ab9d63bfac3256158f'),
    'frequency_bridge': (R/'astra-l04-frequency-partial-inputs-01/frequency-10000000-partial-inputs.npz', 'f5fff0c74c667d8243852b709ed07238585eec427a878bb074519d995f5f680a'),
    'frequency_cell_result': (R/'astra-l02-frequency-cell-operators-01/result.json', 'e1f31aa4ee180f2fe1a6969accb663af16b3c0a61625b694860a6e448a2261b6'),
    'frequency_cell_driver': (R/'astra-l02-frequency-cell-operators-01/driver-at-run.py', 'fadcb7e20f1eb47ac3207d837fe438a359b42fab0d684d042487dc850d5065d0'),
    'frequency_cell_external': (R/'astra-l02-frequency-cell-operators-01/external-budget.json', 'e69c1c6ae93233f19013eabb1732a0182fc22277c7f694c6439dbf39cb210641'),
    'frequency_cell_pack': (R/'astra-l02-frequency-gc-inputs-01/frequency-10000000-l02-cell-pack.npz', '3adae825cf5be0691997044bcc98f01eed9430f7cc1a6f4147de0e8c037c6b9e'),
    'raw': (R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz', '6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7'),
    'derived': (R/'astra-native-loaded-vtrip-field-02/derived-field-observation.npz', 'be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0'),
    'inventory14': (R/'astra-native-loaded-vtrip-field-02/l14-gc-projection-inventory.json', '4409cdccc3b3d488d8f3abdd2f1e8b09a488d3c18a5bace010c765a240bc397f'),
    'inventory25': (R/'astra-l25-source-sheet-01/l25-gc-projection-inventory.json', '703e8cdf9988805a2fb05a40ff659bc9890ba999e2eef00c741a112aa5976a77'),
    'l14_frequency_helper': (Path(l14.__file__), 'ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803'),
})
sha = prior.sha
NATIVE_N = 1_483_296
L25_GC_TARGET = 258_027

def _forward_ratio(difference, bound):
    # Complex division by subnormal real bounds can produce NaN even at zero.
    # Magnitudes first are algebraically identical; bounds remain unchanged.
    assert np.all(np.isfinite(bound)) and np.all(bound > 0)
    return float(np.max(np.abs(difference) / bound))

def pending():
    return [name for name, (_, digest) in PINS.items() if digest.startswith('PENDING_')]

def _assert_pinned_inputs():
    assert not pending(), pending()
    inputs = {k:receipt(p, h) for k,(p,h) in PINS.items()}
    operator = json.loads(PINS['frequency_operator_result'][0].read_bytes())
    assert operator['status'] == 'ASSEMBLED_CONDITIONAL_FULL_CONTACT_FREQUENCY_OPERATORS_NO_SOLVE'
    assert operator['driver']['sha256'] == PINS['frequency_operator_driver'][1]
    point = next(row for row in operator['points'] if row['frequency_hz'] == FREQUENCY_HZ)
    assert point['artifact']['sha256'] == PINS['frequency_operator'][1]
    assert point['l04_bridge_inputs']['sha256'] == PINS['frequency_bridge'][1]
    operator_external = json.loads(PINS['frequency_operator_external'][0].read_bytes())
    assert operator_external['status'] == 'COMPLETED_NATIVE_WORKER' and operator_external['exit_code'] == 0
    assert operator_external['driver_sha256'] == PINS['frequency_operator_driver'][1]
    bridge = json.loads(PINS['frequency_bridge_result'][0].read_bytes())
    assert bridge['status'] == 'PREPARED_PARTIAL_FREQUENCY_INPUTS_NO_SOLVE'
    assert bridge['driver']['sha256'] == PINS['frequency_bridge_driver'][1]
    bridge_point = next(row for row in bridge['points'] if row['frequency_hz'] == FREQUENCY_HZ)
    assert bridge_point['artifact']['sha256'] == PINS['frequency_bridge'][1]
    cells = json.loads(PINS['frequency_cell_result'][0].read_bytes())
    assert cells['status'] == 'ASSEMBLED_L02_FREQUENCY_CELL_OPERATORS_NO_BOARD_SOLVE'
    assert cells['driver']['sha256'] == PINS['frequency_cell_driver'][1]
    cell_point = next(row for row in cells['points'] if row['frequency_hz'] == FREQUENCY_HZ)
    assert cell_point['cell_pack']['sha256'] == PINS['frequency_cell_pack'][1]
    cell_external = json.loads(PINS['frequency_cell_external'][0].read_bytes())
    assert cell_external['status'] == 'COMPLETED_NATIVE_WORKER' and cell_external['exit_code'] == 0
    assert cell_external['driver_sha256'] == PINS['frequency_cell_driver'][1]
    return inputs

def _require_recovery_contract(result_path, result_sha256, field_path, field_sha256):
    result_path, field_path = Path(result_path), Path(field_path)
    rr, fr = receipt(result_path, result_sha256), receipt(field_path, field_sha256)
    assert rr['sha256'] == PINS['recovery_result'][1]
    assert fr['sha256'] == PINS['recovery_field'][1]
    result = json.loads(result_path.read_text())
    assert result['status'] == 'RECOVERED_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT'
    assert result['field']['sha256'] == fr['sha256']
    assert result['frequency_hz'] == FREQUENCY_HZ and result['source_current_amplitude_a'] == 1.0
    for name in ('bridge_result', 'stream_result'):
        assert result['geometry_approximation'] == json.loads(PINS[name][0].read_bytes())['geometry_approximation']
    assert result['driver']['sha256'] == PINS['recovery_driver'][1]
    external_path, external_sha = PINS['recovery_external']
    external_receipt = receipt(external_path, external_sha)
    external = json.loads(external_path.read_bytes())
    assert external_path.parent == result_path.parent
    assert external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0
    assert external['driver_sha256'] == result['driver']['sha256']
    for key in ('solver_result', 'solver_driver', 'solver_external', 'solver_field'):
        item = result['inputs'][key]
        receipt(item['path'], item['sha256'])
    solver = json.loads(Path(result['inputs']['solver_result']['path']).read_bytes())
    raw = result['numerical']
    assert raw['info'] == solver['lgmres_info']
    assert raw['algebraically_converged'] == solver['algebraically_converged']
    assert raw['initial_scaled_residual_relative'] == solver['initial_relative']
    assert raw['final_scaled_residual_relative'] == solver['final_relative']
    assert raw['field_scaled_residual_relative'] == solver['best_relative']
    assert solver['best_field']['sha256'] == result['inputs']['solver_field']['sha256']
    selected_is_last = solver['history'][-1]['field']['sha256'] == solver['best_field']['sha256']
    numerical_gate = (raw['info'] == 0 and raw['algebraically_converged'] is True
                      and selected_is_last and raw['field_scaled_residual_relative'] <= 1e-9)
    assert result['original_numerical_gate'] == numerical_gate
    return result, rr, fr, external_receipt

def _finite_replay_frequency(voltage, w, bridge, frequency_bridge, old_y, output):
    """Replay the same physical finite ownership with target-frequency admittances."""
    with np.load(PINS['pack'][0], allow_pickle=False) as pack:
        old_first, old_second = (pack['finite_' + key] for key in ('first_active_index', 'second_active_index'))
    hidden = np.asarray(bridge['hidden_parent_finite_rows'], dtype=np.int64)
    assert hidden.shape == (10,) and len(np.unique(hidden)) == len(hidden)
    assert old_y.shape == old_first.shape
    keep = np.ones(len(old_first), dtype=bool); keep[hidden] = False
    old_contact = np.full(len(old_first), -1, dtype=np.int64)
    old_sign = np.zeros(len(old_first), dtype=np.int8)
    unchanged = bridge['l04_subleg_index'] < 0
    mapped = bridge['final_finite_row'][unchanged]
    assert np.all(mapped >= 0) and len(np.unique(mapped)) == len(mapped)
    old_contact[mapped] = bridge['contact_index'][unchanged]
    old_sign[mapped] = bridge['contact_drop_sign'][unchanged]
    new = bridge['l04_subleg_index'] >= 0
    new_y = np.asarray(frequency_bridge['l04_finite_admittance_s'])
    assert new_y.shape == bridge['resistance_ohm'].shape
    first = np.r_[old_first[keep], bridge['first_active_index'][new]]
    second = np.r_[old_second[keep], bridge['second_active_index'][new]]
    admittance = np.r_[old_y[keep], new_y[new]]
    contact = np.r_[old_contact[keep], bridge['contact_index'][new]]
    sign = np.r_[old_sign[keep], bridge['contact_drop_sign'][new]]
    old_row = np.r_[np.flatnonzero(keep), np.full(np.count_nonzero(new), -1, dtype=np.int64)]
    bridge_parent = np.r_[np.full(np.count_nonzero(keep), -1, dtype=np.int64), bridge['previous_parent_finite_row'][new]]
    bridge_subleg = np.r_[np.full(np.count_nonzero(keep), -1, dtype=np.int8), bridge['l04_subleg_index'][new]]
    assert len(first) == len(old_first) - 10 + 20 == 1_692_419
    drop = voltage[first] - voltage[second]; terminal = contact >= 0
    drop[terminal] += sign[terminal] * w[contact[terminal]]
    action, current = _branch_action(first, second, admittance, drop)
    contact_finite = np.zeros(len(w), dtype=np.complex128)
    np.add.at(contact_finite, contact[terminal], sign[terminal] * current[terminal])
    artifact = output / 'finite-full-contact-reconstructed.npz'
    prior.atomic_npz(artifact, first_active_index=first, second_active_index=second, admittance_s=admittance,
                     physical_drop_v=drop, finite_current_first_to_second_a=current, contact_index=contact,
                     contact_drop_sign=sign, old_finite_row=old_row, bridge_parent_finite_row=bridge_parent,
                     bridge_subleg_index=bridge_subleg, hidden_parent_finite_rows=hidden,
                     frequency_hz=np.asarray([FREQUENCY_HZ]))
    pair_magnitude = abs(admittance)*(abs(voltage[first])+abs(voltage[second]))
    pair_magnitude[terminal] += abs(admittance[terminal])*abs(w[contact[terminal]])
    magnitude = np.zeros(N); np.add.at(magnitude, first, pair_magnitude); np.add.at(magnitude, second, pair_magnitude)
    degree = 4*np.bincount(np.r_[first, second], minlength=N)
    power = complex(np.sum(np.conj(admittance)*abs(drop)**2))
    return action, current, contact_finite, power, receipt(artifact), magnitude, degree

def reconstruct_frequency(voltage, terminal, output, budget):
    """Copy the qualified L02 replay, replacing only its owner-GC input pack."""
    with np.load(PINS['restricted'][0], allow_pickle=False) as z:
        active, upper = z['active_local_facet_mask'], z['dc_trace_h_upper_s']
        rho, weight = z['unit_divergence_resistance_ohm'], z['unit_divergence_flux_weights']
        free, exterior = z['free_triangle_indices'], z['zero_flux_exterior_branch_indices']
        facets = z['local_facet_branch_index']
    with np.load(PINS['frequency_cell_pack'][0], allow_pickle=False) as z:
        assert np.array_equal(z['frequency_hz'], [FREQUENCY_HZ])
        owner_count = z['free_cell_owner_count']
        owner_index, owner_g = z['free_cell_owner_external_active_index'], z['free_cell_gc_admittance_s']
    with np.load(PINS['l02_mesh'][0], allow_pickle=False) as z:
        xy, triangles = z['node_xy_um'], z['triangles']
    with np.load(PINS['l02_space'][0], allow_pickle=False) as z:
        signs = z['local_outward_flux_sign']
        assert np.array_equal(facets, z['local_facet_branch_index'])
        resistance = sparse.csr_matrix((z['r_data'], z['r_indices'], z['r_indptr']), shape=tuple(z['r_shape']))
    assert terminal.shape == active.shape == weight.shape == (1_583_840, 3)
    assert upper.shape == (1_583_840, 6) and len(free) == len(rho) == len(owner_count)
    assert np.all(terminal[active] >= 0) and np.all(terminal[~active] == -1)
    q = np.empty((len(free), 3), dtype=complex); u = np.empty(len(free), dtype=complex)
    divergence_target = np.empty(len(free), dtype=complex)
    action = np.zeros(N, complex); magnitude = np.zeros(N); degree = np.zeros(N, np.int64)
    rhs_r = np.zeros(resistance.shape[0], complex)
    cell_kcl_max = local_constitutive_max = 0.; local_joule = 0.; gc_power = 0j
    ii, jj = np.triu_indices(3)
    for start in range(0, len(free), 50_000):
        sl = slice(start, min(start+50_000, len(free)))
        mask, ports = active[sl], terminal[sl]
        lam = np.zeros(ports.shape, complex); lam[mask] = voltage[ports[mask]]
        g = owner_g[sl].copy(); owners = owner_index[sl]
        valid = np.arange(g.shape[1])[None] < owner_count[sl, None]
        assert np.all(g[~valid] == 0) and np.all(owners[valid] >= 0)
        V = np.zeros(g.shape, complex); V[valid] = voltage[owners[valid]]
        h = np.zeros((len(lam), 3, 3)); h[:, ii, jj] = upper[sl]; h[:, jj, ii] = upper[sl]
        w, r = weight[sl], rho[sl]
        c, drive = g.sum(1), np.sum(g*V, axis=1)
        wl = np.sum(w*lam, axis=1); den = 1+r*c
        assert np.all(den.real > 0) and np.all(np.isfinite(den))
        cell_u = (wl+r*drive)/den
        cell_q = -np.einsum('nij,nj->ni', h, lam)+w*((drive-c*wl)/den)[:, None]
        assert np.all(cell_q[~mask] == 0)
        cell_gc = g*(V-cell_u[:, None]); divergence_target[sl] = cell_gc.sum(1)
        cell_kcl_max = max(cell_kcl_max, prior.max_abs(cell_q.sum(1)-divergence_target[sl]))
        q[sl], u[sl] = cell_q, cell_u
        np.add.at(action, ports[mask], -cell_q[mask]); np.add.at(action, owners[valid], cell_gc[valid])
        abs_wl = np.sum(abs(w)*abs(lam), axis=1); abs_drive = np.sum(abs(g)*abs(V), axis=1)
        face_magnitude = np.einsum('nij,nj->ni', abs(h), abs(lam))
        face_magnitude += abs(w)*((abs_drive+abs(c)*abs_wl)/abs(den))[:, None]
        owner_magnitude = abs(g)*(abs(V)+((abs_wl+r*abs_drive)/abs(den))[:, None])
        np.add.at(magnitude, ports[mask], face_magnitude[mask]); np.add.at(magnitude, owners[valid], owner_magnitude[valid])
        np.add.at(degree, ports[mask], 16); np.add.at(degree, owners[valid], 16)
        p = xy[triangles[free[sl]]].copy(); p -= p[:, :1].copy(); p *= 1e-6
        rcell, _, _ = fd28.local_r._batch_local_rt0(p, 59.59e6*20e-6)
        rq = np.einsum('nij,nj->ni', rcell, cell_q)
        local_constitutive_max = max(local_constitutive_max, prior.max_abs((rq-cell_u[:, None]+lam)[mask]))
        local_joule += float(np.sum(np.conj(cell_q)*rq).real)
        gc_power += complex(np.sum(np.conj(g)*abs(V-cell_u[:, None])**2))
        np.add.at(rhs_r, facets[sl][mask], (signs[sl]*(cell_u[:, None]-lam))[mask])
        budget.check('batched 10 MHz physical cell replay')
    branch = facets.ravel(); oriented = (signs*q).ravel()
    first = np.full(resistance.shape[0], len(branch), dtype=np.int64)
    np.minimum.at(first, branch, np.arange(len(branch))); assert np.all(first < len(branch))
    current = oriented[first]; jump = prior.max_abs(oriented-current[branch])
    global_cell_kcl = prior.max_abs(np.sum(signs*current[facets], axis=1)-divergence_target)
    assert np.all(current[exterior] == 0)
    rj = resistance@current; active_branch = np.ones(len(current), bool); active_branch[exterior] = False
    constitutive = prior.max_abs((rj-rhs_r)[active_branch]); joule = complex(np.vdot(current, rj))
    artifact = output/'l02-reconstructed-field.npz'
    prior.atomic_npz(artifact, cell_potential_v=u, cell_outward_flux_a=q, l02_branch_current_a=current,
                     zero_flux_exterior_branch_indices=exterior, frequency_hz=np.asarray([FREQUENCY_HZ]))
    metrics = {'cell_kcl_max_abs_a': cell_kcl_max, 'shared_facet_jump_max_abs_a': jump,
               'global_current_cell_kcl_max_abs_a': global_cell_kcl,
               'local_rt0_constitutive_max_abs_v': local_constitutive_max,
               'global_rt0_constitutive_max_abs_v': constitutive,
               'inactive_exterior_current_max_abs_a': 0., 'local_rt0_joule_w': local_joule,
               'global_rt0_joule_w': prior.pair(joule), 'local_global_joule_difference_ohm': abs(joule-local_joule),
               'reconstructed_field': prior.receipt(artifact)}
    return action, magnitude, degree, {'l02_rt0_resistance': joule, 'l02_cell_gc': gc_power}, metrics

def _frequency_category_matrices(current_coeff, term_y):
    inventory14 = json.loads(PINS['inventory14'][0].read_bytes())
    inventory25 = json.loads(PINS['inventory25'][0].read_bytes())
    with np.load(PINS['raw'][0], allow_pickle=False) as raw:
        baseline = raw['partial_actual_1mhz_dispersion_admittance_scale_s']
        names = fd28.recon._decode_text_vector(raw['surface_node_ids'], 'surface')
        lookup = {name:index for index,name in enumerate(names)}
        surface_active = raw['global_to_active_indices'][raw['surface_to_reduced_indices']]
        retained_coeff = current_coeff.copy(); retained_coeff[0] = 0
        retained, evidence = l14.retained_partials(raw, inventory14, lookup, surface_active, N,
                                                   coefficients=retained_coeff)
    removed25 = 0
    for partial in inventory25['original_gc']['partials']:
        ordinal = int(partial['ordinal']); assert ordinal in (13, 14)
        owners = partial['owners']; removed25 += len(owners)
        capacitance = np.asarray([float.fromhex(owner['nominal_capacitance_f_hex']) for owner in owners])
        external = np.asarray([owner['external_active_index'] for owner in owners], dtype=np.int64)
        retained -= prior.branch_laplacian(np.full(len(owners), L25_GC_TARGET), external,
                                            capacitance*current_coeff[ordinal], N)
    assert removed25 == 4 and evidence == [{'ordinal': 6, 'removed': 110, 'retained': 336},
                                           {'ordinal': 7, 'removed': 114, 'retained': 412}]
    retained.eliminate_zeros()
    with np.load(PINS['derived'][0], allow_pickle=False) as derived:
        tp = derived['termination_positive_active_indices']; tn = derived['termination_negative_active_indices']
    termination = prior.branch_laplacian(tp, tn, term_y, N)
    ratio14 = current_coeff[[6, 7]]/baseline[[6, 7]]
    ratio25 = current_coeff[[13, 14]]/baseline[[13, 14]]
    assert np.max(abs(ratio14-ratio14[0])) <= 64*np.finfo(float).eps*abs(ratio14[0])
    assert np.max(abs(ratio25-ratio25[0])) <= 64*np.finfo(float).eps*abs(ratio25[0])
    return retained, termination, complex(ratio14[0]), complex(ratio25[0])

def diagnose_checkpoint(solver_result_path, solver_result_sha256, field_path, field_sha256, output):
    """Measure all original physical gates; retain raw numerical failure independently."""
    output = Path(output); assert output.is_dir() and not (output/'physical-diagnostic.json').exists()
    started = perf_counter(); inputs = _assert_pinned_inputs()
    frozen_validator = output / 'physical-validator-at-run.py'
    frozen_validator.write_bytes(Path(__file__).read_bytes())
    validator_receipt = receipt(frozen_validator)
    result, result_receipt, field_receipt, external_receipt = _require_recovery_contract(solver_result_path, solver_result_sha256, field_path, field_sha256)
    inputs['validator_driver'] = validator_receipt
    budget = recon._Budget.create(300., 8.); budget.check('full-contact validator start')
    with np.load(field_path, allow_pickle=False) as field:
        assert np.array_equal(field['source_positive_negative_gauge_active_indices'], [POSITIVE, NEGATIVE, GAUGE])
        assert np.array_equal(field['source_current_amplitude_a'], [1.0])
        assert np.array_equal(field['frequency_hz'], [FREQUENCY_HZ])
        assert str(field['frequency_operator_sha256_utf8'][0]) == PINS['frequency_operator'][1]
        assert str(field['frequency_bridge_sha256_utf8'][0]) == PINS['frequency_bridge'][1]
        voltage = np.asarray(field['active_voltage_v'], dtype=np.complex128); q25 = np.asarray(field['l25_branch_current_a'], dtype=np.complex128)
        assert voltage.shape == (N,) and q25.shape == (L25_BRANCHES,) and voltage[GAUGE] == 0
        assert np.all(np.isfinite(voltage)) and np.all(np.isfinite(q25))
        with np.load(PINS['bridge'][0], allow_pickle=False) as bridge_archive:
            bridge = {name: bridge_archive[name] for name in bridge_archive.files}
        with np.load(PINS['frequency_bridge'][0], allow_pickle=False) as bridge_archive:
            frequency_bridge = {name: bridge_archive[name] for name in bridge_archive.files}
        assert np.array_equal(frequency_bridge['frequency_hz'], [FREQUENCY_HZ])
        assert np.array_equal(frequency_bridge['source_current_a'], [1.0])
        with np.load(PINS['frequency_operator'][0], allow_pickle=False) as op:
            old_frequency_y = np.asarray(op['finite_admittance_s'])
        l04 = _l04_replay(field, bridge, output)
        finite_action, _, contact_finite, finite_power, finite_artifact, finite_physical_magnitude, finite_physical_degree = _finite_replay_frequency(
            voltage, l04['w'], bridge, frequency_bridge, old_frequency_y, output)
    budget.check('L04 and physical-finite replay saved')
    with np.load(PINS['frequency_operator'][0], allow_pickle=False) as op:
        assert np.array_equal(op['frequency_hz'], [FREQUENCY_HZ]) and np.array_equal(op['source_current_a'], [1.0])
        y, r25, b = (_load_csc(op, name) for name in ('y', 'r', 'b'))
        old_frequency_y = np.asarray(op['finite_admittance_s'])
        term_y = np.asarray(op['termination_admittance_s'])
        current_coeff = np.asarray(op['native_partial_dispersion_s_per_f'])
        ratio14_saved = complex(op['l14_gc_scale'][0]); ratio25_saved = complex(op['l25_gc_scale'][0])
    with np.load(PINS['operator'][0], allow_pickle=False) as base_operator:
        terminal = base_operator['conditional_local_facet_global_active_index']
    l02_action, magnitude, degree, power, l02_metrics = reconstruct_frequency(voltage, terminal, output, budget)
    source = l02_action + finite_action; power['finite_full_contact'] = finite_power
    # Keep the physical, post-bridge construction side of both source envelopes.
    magnitude += finite_physical_magnitude
    degree += finite_physical_degree
    with np.load(PINS['frequency_cell_pack'][0], allow_pickle=False) as pack:
        cf, cs, cy = (pack['contact_gc_' + key] for key in ('first_active_index', 'second_active_index', 'admittance_s'))
        action, _ = _branch_action(cf, cs, cy, voltage[cf]-voltage[cs]); source += action
        power['l02_contact_gc'] = complex(np.sum(np.conj(cy)*abs(voltage[cf]-voltage[cs])**2))
        contact_pair = abs(cy)*(abs(voltage[cf])+abs(voltage[cs]))
        np.add.at(magnitude, cf, contact_pair); np.add.at(magnitude, cs, contact_pair)
        degree += 4*np.bincount(np.r_[cf, cs], minlength=N)
    retained, termination, ratio14, ratio25 = _frequency_category_matrices(current_coeff, term_y)
    assert ratio14 == ratio14_saved and ratio25 == ratio25_saved
    for name, matrix in (('retained_gc', retained), ('termination', termination)):
        action = matrix @ voltage; source += action
        magnitude += abs(matrix)@abs(voltage); degree += prior.row_degree(matrix)
        power[name] = complex(np.conj(np.vdot(voltage, action)))
        del matrix, action
    del retained, termination
    with np.load(PINS['pack'][0], allow_pickle=False) as pack:
        names = json.loads(pack['category_names_json_utf8'].tobytes())
        assert names == ['retained_gc', 'termination', 'l14_sheet_dc', 'l14_distributed_gc', 'l25_distributed_gc']
        for name, scale in (('l14_sheet_dc', 1.), ('l14_distributed_gc', ratio14),
                            ('l25_distributed_gc', ratio25)):
            matrix = _load_csc(pack, 'category_' + name) * scale
            action = matrix @ voltage[:NATIVE_N]
            source[:NATIVE_N] += action
            magnitude[:NATIVE_N] += abs(matrix)@abs(voltage[:NATIVE_N])
            degree[:NATIVE_N] += prior.row_degree(matrix)
            power[name] = complex(np.conj(np.vdot(voltage[:NATIVE_N], action)))
            del matrix, action
    delta = _load_csc(frequency_bridge, 'l04_delta')
    u = _load_csc(frequency_bridge, 'l04_u')
    root = int(bridge['root_contact_index'][0]); w_nonroot = np.delete(l04['w'], root)
    matrix_potential = y@voltage + delta@voltage + u@w_nonroot
    with np.load(PINS['pack'][0], allow_pickle=False) as pack:
        old_first, old_second = (pack['finite_' + key] for key in ('first_active_index', 'second_active_index'))
    old_admittance = old_frequency_y
    old_finite = prior.branch_action(old_first, old_second, old_admittance, voltage)
    assembled_finite = old_finite + delta@voltage + u@w_nonroot
    # Each envelope contains both independently constructed sides.  In
    # particular, cancellation on a skinny L02 row cannot erase the source
    # side's operation count before it is compared with Y+deltaY+U*w.
    finite_matrix_magnitude = np.zeros(N)
    finite_matrix_degree = np.bincount(np.r_[old_first, old_second], minlength=N) * 4
    old_pair = abs(old_admittance)*(abs(voltage[old_first])+abs(voltage[old_second]))
    np.add.at(finite_matrix_magnitude, old_first, old_pair); np.add.at(finite_matrix_magnitude, old_second, old_pair)
    finite_matrix_magnitude += abs(delta)@abs(voltage) + abs(u)@abs(w_nonroot)
    finite_matrix_degree += prior.row_degree(delta) + prior.row_degree(u)
    finite_difference = finite_action - assembled_finite
    finite_bound = prior.gamma(16*(finite_physical_degree+finite_matrix_degree+32))*np.maximum(
        finite_physical_magnitude+finite_matrix_magnitude, np.finfo(float).tiny)
    finite_forward = _forward_ratio(finite_difference, finite_bound)
    source_difference = source - matrix_potential
    matrix_magnitude = abs(y)@abs(voltage) + abs(delta)@abs(voltage) + abs(u)@abs(w_nonroot)
    matrix_degree = prior.row_degree(y) + prior.row_degree(delta) + prior.row_degree(u)
    source_bound = prior.gamma(16*(degree+matrix_degree+64))*np.maximum(
        magnitude+matrix_magnitude, np.finfo(float).tiny)
    source_forward = _forward_ratio(source_difference, source_bound)
    del old_finite, old_pair, finite_physical_magnitude, finite_physical_degree, finite_matrix_magnitude
    del finite_matrix_degree, finite_bound, magnitude, degree, matrix_magnitude, matrix_degree, source_bound
    rhs = np.zeros(N, dtype=np.complex128); rhs[POSITIVE], rhs[NEGATIVE] = 1., -1.
    bq25 = b@q25; physical_kcl = source + bq25 - rhs; matrix_kcl = matrix_potential + bq25 - rhs
    r25q = r25@q25; constitutive25 = r25q-b.T@voltage
    power['l25_rt0_resistance'] = complex(np.vdot(q25, r25q)); power['l04_rt0_resistance'] = l04['joule']
    zdd = complex(voltage[POSITIVE]-voltage[NEGATIVE])
    physical_total = sum(power.values()); physical_error = abs(physical_total-zdd)
    electrode_error = abs(l04['electrode_work_ohm']-l04['joule'])
    matrix_power = complex(np.conj(np.vdot(voltage, matrix_potential)) + np.vdot(q25, r25q))
    matrix_power_error = abs(matrix_power-zdd)
    contact_kcl = contact_finite + l04['bq'][L04_FREE_CELLS:]
    gates = {
        'matrix_global_circuit_kcl': _max_abs(matrix_kcl) < 1e-7,
        'physical_source_global_circuit_kcl': _max_abs(physical_kcl) < 1e-7,
        'finite_source_assembled_forward_envelope': finite_forward <= 1.,
        'full_source_assembled_forward_envelope': source_forward <= 1.,
        'l25_constitutive': _max_abs(constitutive25) < 1e-7,
        'l04_bq_scatter': l04['bq_kcl_max_abs_a'] < 1e-7,
        'l04_bq_divergence_kcl': l04['bq_divergence_kcl_max_abs_a'] < 1e-7,
        'l04_all_contact_kcl': _max_abs(contact_kcl) < 1e-7,
        'l04_constitutive': l04['constitutive_max_abs_v'] < 1e-7,
        'l04_inherited_dual_rq_relative': l04['constitutive_rq_relative'] <= 1e-7,
        'l04_energy_stationarity': l04['energy_scaled_stationarity_relative'] < 2e-8,
        'l04_exterior_zero': l04['exterior_local_facet_current_max_abs_a'] == 0.,
        'l02_cell_kcl': l02_metrics['cell_kcl_max_abs_a'] < 1e-7,
        'l02_global_rt0_kcl': l02_metrics['global_current_cell_kcl_max_abs_a'] < 1e-7,
        'l02_shared_jump': l02_metrics['shared_facet_jump_max_abs_a'] < 1e-7,
        'l02_local_constitutive': l02_metrics['local_rt0_constitutive_max_abs_v'] < 1e-7,
        'l02_constitutive': l02_metrics['global_rt0_constitutive_max_abs_v'] < 1e-7,
        'l02_local_global_joule_identity': l02_metrics['local_global_joule_difference_ohm'] <= 1e-7*max(
            abs(power['l02_rt0_resistance']), abs(l02_metrics['local_rt0_joule_w']), np.finfo(float).tiny),
        'l02_local_joule_passivity': np.isfinite(l02_metrics['local_rt0_joule_w']) and l02_metrics['local_rt0_joule_w'] >= -1e-10,
        'physical_passivity': zdd.real >= -1e-12 and all(
            np.isfinite(value) and value.real >= -1e-10 for value in power.values()),
        'physical_power_closure': physical_error <= 1e-7*max(abs(zdd), np.finfo(float).tiny),
        'l04_electrode_work': electrode_error <= 1e-7*max(abs(l04['joule']), np.finfo(float).tiny),
        'matrix_identity_power': matrix_power_error <= 1e-7*max(abs(zdd), np.finfo(float).tiny),
    }
    diagnostic = {'program': PROGRAM, 'version': VERSION,
        'status': 'DIAGNOSTIC_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT',
        'original_numerical_gate': result['original_numerical_gate'],
        'physical_gates_all_pass': bool(all(gates.values())),
        'validator_sha256': prior.sha(Path(__file__)), 'validator_driver': validator_receipt, 'inputs': inputs,
        'artifacts': {'finite_reconstruction': finite_artifact, 'l04_reconstruction': l04['artifact'], 'l02_reconstruction': l02_metrics['reconstructed_field']},
        'solver_numerical': result['numerical'],
        'geometry_approximation': result['geometry_approximation'], 'frequency_hz': FREQUENCY_HZ, 'source_current_a': 1.0,
        'physical': {**l02_metrics, 'zdd_ohm': _pair(zdd), 'matrix_kcl_max_abs_a': _max_abs(matrix_kcl),
          'physical_kcl_max_abs_a': _max_abs(physical_kcl), 'finite_forward_envelope_ratio': finite_forward, 'full_source_forward_envelope_ratio': source_forward,
          'l25_constitutive_max_abs_v': _max_abs(constitutive25), 'l04_bq_scatter_max_abs_a': l04['bq_kcl_max_abs_a'], 'l04_bq_divergence_kcl_max_abs_a': l04['bq_divergence_kcl_max_abs_a'],
          'l04_contact_kcl_max_abs_a': _max_abs(contact_kcl), 'l04_constitutive_max_abs_v': l04['constitutive_max_abs_v'],
          'l04_constitutive_rq_relative': l04['constitutive_rq_relative'],
          'l04_energy_scaled_stationarity_relative': l04['energy_scaled_stationarity_relative'],
          'l04_electrode_work_ohm': _pair(l04['electrode_work_ohm']), 'power_contributions_ohm': {k:_pair(v) for k,v in power.items()},
          'physical_power_closure_error_ohm': physical_error, 'matrix_power_closure_error_ohm': matrix_power_error},
        'gates': {name: bool(value) for name, value in gates.items()}, 'budget': budget.receipt(), 'elapsed_seconds': perf_counter()-started,
        'scope': 'Diagnostic only; no acceptance even if all physical gates pass. Exact saved fixed-R full-contact L04 RT0 action with target-frequency finite, termination, native partial, L02 cell/contact, and guarded L14/L25 GC stamps at 10 MHz. No magnetic/FMM, broad-band, mesh, or PowerSI claim.'}
    prior.atomic_json(output/'physical-diagnostic.json', diagnostic)
    return diagnostic

def launch(output: Path) -> int:
    assert not pending(), f"unfilled pins: {pending()}"
    import probe_astra_l25_rt0_p1_pair as counter
    assert sha(Path(counter.__file__)) == MEMORY_COUNTER_SOURCE_SHA
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "driver-at-run.py"
    shutil.copy2(Path(__file__), frozen)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    started = time.monotonic(); private = working = 0; reason = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        print(f"bounded physical diagnostic watchdog: owned PID={process.pid},{EXTERNAL_SECONDS}s/8GiB", flush=True)
        while process.poll() is None:
            counters = counter._MemoryCounters(); counters.cb = ctypes.sizeof(counters)
            if not getter(int(process._handle), ctypes.byref(counters), counters.cb):
                if process.poll() is None: reason = "STOP_PROCESS_MEMORY_QUERY"
            else:
                private = max(private, int(counters.private_usage)); working = max(working, int(counters.working_set))
            if time.monotonic() - started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
            if max(private, working) > int(MEMORY_GIB * 2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
            if reason:
                process.kill(); break
            time.sleep(.5)
        code = process.wait(timeout=10)
    status = reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT")
    (output / "external-budget.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
        "status": status, "exit_code": code, "driver_sha256": sha(frozen),
        "external_seconds": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30),
        "printed_memory_cap_gib": MEMORY_GIB, "sampled_peak_private_bytes": private,
        "sampled_peak_working_set_bytes": working, "sampling_interval_s": .5}, indent=2), encoding="utf-8")
    return 0 if status == "COMPLETED_NATIVE_WORKER" and code == 0 else 2


def self_check():
    assert _forward_ratio(np.array([0j, 1e-320+0j]), np.array([1e-320, 1e-320])) == 1.
    assert _forward_ratio(np.array([3+4j]), np.array([2.])) == 2.5
    def gates(text):
        tree = ast.parse(text)
        return next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'gates' for t in node.targets))
    current = Path(__file__).read_text()
    baseline = Path(original.__file__).read_text()
    assert sha(Path(original.__file__)) == SOURCE_SHA
    assert ast.dump(gates(current)) == ast.dump(gates(baseline))
    assert len(gates(current).keys) == 23
    assert "'status': 'DIAGNOSTIC_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT'" in current
    print('PASS_DIAGNOSTIC_PRESERVES_23_PHYSICAL_GATE_EXPRESSIONS')

def worker(output):
    assert {p.name for p in output.iterdir()} == {'driver-at-run.py'}
    assert sha(output/'driver-at-run.py') == sha(Path(__file__))
    try:
        result = diagnose_checkpoint(*PINS['recovery_result'], *PINS['recovery_field'], output)
        print(json.dumps({'status':result['status'], 'gates':result['gates'],
                          'original_numerical_gate':result['original_numerical_gate']}), flush=True)
    except BaseException:
        (output/'failure.json').write_text(json.dumps({'status':'STOP_PHYSICAL_DIAGNOSTIC',
                                                     'traceback':traceback.format_exc()}, indent=2))
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check', action='store_true')
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--native-worker', action='store_true')
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    if args.self_check:
        self_check(); return
    assert RUN_RELEASED and not pending()
    assert args.output is not None
    if args.native_worker:
        worker(args.output.resolve())
    else:
        raise SystemExit(launch(args.output.resolve()))

if __name__ == '__main__':
    main()
