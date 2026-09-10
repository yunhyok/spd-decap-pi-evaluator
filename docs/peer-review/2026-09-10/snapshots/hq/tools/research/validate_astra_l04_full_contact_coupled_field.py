"""Validate a future full-contact L04 coupled field without reusing fd28 wholesale.

This module is deliberately API-only.  The coupled solver's result/field hashes are
arguments because no full-contact solve has been accepted when this source is frozen.
"""
import argparse
import gc
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse

import validate_astra_l02_hybrid_field as fd28
import assemble_astra_l25_rt0_resistance as local_r
import reconstruct_astra_native_loaded_field as recon
import solve_astra_l02_l14_l25_combined_block_lgmres as prior

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
PROGRAM = 'SPD Decap PI Evaluator'
VERSION = '0.23.1'
N = 3_178_104
POSITIVE, NEGATIVE, GAUGE = 2699, 2656, 0
L04_UNIFORM_ACTIVE = 71610
L04_CONTACTS = 38_278
L04_FREE_CELLS = 1_589_827
L25_BRANCHES = 604_031
# Solver receipts deliberately remain runtime arguments.  These immutable inputs are
# sufficient to replay the original source and the new L04 sheet exactly.
PINS = {
    'fd28_validator_source': (ROOT / 'tools/research/validate_astra_l02_hybrid_field.py',
        'fd28d5a3eef17d7d85e0fab52de1a88e1910faa2ad46a4cde3b16d3607c8abd9'),
    'coupled_solver_source': (ROOT / 'tools/research/solve_astra_l04_full_contact_coupled.py',
        'PENDING_FINAL_COUPLED_SOLVER_DRIVER'),
    'coupled_solver_external': (R / 'astra-l04-full-contact-coupled-PENDING/external-budget.json',
        'PENDING_FINAL_COUPLED_SOLVER_EXTERNAL'),
    'operator': (R / 'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz',
        '5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'),
    'pack': (R / 'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz',
        '01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c'),
    'restricted': (R / 'astra-l02-restricted-hybrid-cell-geometry-02/restricted-hybrid-cell-geometry.npz',
        'e03297ce292d082422585e71e47a05c3d3caed8ccfe0cd24e99d06ebeb7eac0b'),
    'l02_space': (R / 'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz',
        '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f'),
    'l02_mesh': (R / 'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz',
        '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9'),
    'bridge_result': (R / 'astra-l04-full-contact-circuit-bridge-01/result.json',
        '1583612f90fca3c97f7b9fb4f8c3a0a004d6ae8d84ccc1f7bbc8f382247d266f'),
    'bridge': (R / 'astra-l04-full-contact-circuit-bridge-01/l04-full-contact-circuit-bridge.npz',
        '02508f2e1588fe5e6ad4231b9c128a3324a88baf0229293d6a8b70ea10380e57'),
    'ntd_action_source': (ROOT / 'tools/research/prepare_astra_l04_contact_ntd_action.py',
        'e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8'),
    'ntd_qualification': (R / 'astra-l04-contact-ntd-action-03/result.json',
        'a583a96e466f8f5eee14c0b8d6cc23e120512b65a0c19c687886d5a28d3dce2c'),
    'stream_result': (R / 'astra-l04-fixed-contact-stream-01/result.json',
        '3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1'),
    'stream_guard': (R / 'astra-l04-fixed-contact-stream-01/external-budget.json',
        '016bd3039ada75e8475a23ab328b2b0f135f641235bf5c9088e62636c0d04efc'),
    'stream_space': (R / 'astra-l04-fixed-contact-stream-01/l04-fixed-contact-rt0-space.npz',
        '5d31b3c6183eb4f43723eb80d1a545953a42fb2c9320be425d3ebc034cb51bb6'),
    'stream_system': (R / 'astra-l04-fixed-contact-stream-01/l04-fixed-contact-stream-system.npz',
        'b299d67995b58b7fa698b080380cda495032472d0f426ed2dd9e4b78542fb34a'),
    'prior_solver_source': (Path(prior.__file__),
        '853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692'),
    'l25_local_source': (Path(local_r.__file__),
        'ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010'),
    'budget_source': (Path(recon.__file__),
        '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}


def receipt(path, digest=None):
    path = Path(path)
    found = prior.sha(path)
    if digest is not None:
        assert found == digest, str(path)
    return {'path': str(path), 'sha256': found, 'size_bytes': path.stat().st_size}


def _load_csc(archive, prefix):
    return sparse.csc_matrix((archive[prefix + '_data'], archive[prefix + '_indices'],
                              archive[prefix + '_indptr']), shape=tuple(archive[prefix + '_shape']))



def _load_csr(archive, prefix):
    return sparse.csr_matrix((archive[prefix + '_data'], archive[prefix + '_indices'],
                              archive[prefix + '_indptr']), shape=tuple(archive[prefix + '_shape']))

def _max_abs(value):
    return float(np.max(np.abs(value))) if np.size(value) else 0.0


def _pair(value):
    return {'real': float(np.real(value)), 'imag': float(np.imag(value))}


def _assert_pinned_inputs():
    result = {}
    for name, (path, digest) in PINS.items():
        if digest.startswith('PENDING_'):
            result[name] = {'path': str(path), 'sha256': digest, 'status': 'PENDING_FINAL_SOLVER_PIN'}
        else:
            result[name] = receipt(path, digest)
    bridge = json.loads(PINS['bridge_result'][0].read_text(encoding='utf-8'))
    assert bridge['status'] == 'PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE'
    assert bridge['artifact']['sha256'] == PINS['bridge'][1]
    qualification = json.loads(PINS['ntd_qualification'][0].read_text(encoding='utf-8'))
    assert qualification['status'] == 'QUALIFIED_SAVED_L04_CONTACT_NTD_DIAGNOSTIC_NO_REPLAY'
    stream = json.loads(PINS['stream_result'][0].read_text(encoding='utf-8'))
    guard = json.loads(PINS['stream_guard'][0].read_text(encoding='utf-8'))
    assert stream['status'] == 'PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'
    assert stream['frequency_hz'] == 1e6 and stream['source_current_a'] == 1.0
    assert int(stream['metrics']['gauge_contact_graph_row']) - L04_FREE_CELLS == 25440
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
    return result


def _changed_star(bridge):
    """The exact ten old-leg removals and twenty bridge-subleg insertions."""
    select = bridge['l04_subleg_index'] >= 0
    first = np.r_[bridge['old_hidden_first_active_index'], bridge['first_active_index'][select]]
    second = np.r_[bridge['old_hidden_second_active_index'], bridge['second_active_index'][select]]
    old = 1 / (bridge['old_hidden_resistance_ohm'] + 2j*np.pi*1e6*bridge['old_hidden_inductance_h'])
    new = 1 / (bridge['resistance_ohm'][select] + 2j*np.pi*1e6*bridge['inductance_h'][select])
    values = np.r_[-old, new]
    matrix = sparse.coo_matrix((np.tile(values, 4) * np.repeat([1, 1, -1, -1], len(values)),
        (np.r_[first, second, first, second], np.r_[first, second, second, first])), shape=(N, N)).tocsc()
    matrix.sum_duplicates(); matrix.eliminate_zeros()
    return matrix


def _branch_action(first, second, admittance, drop, size=N):
    current = admittance * drop
    result = np.zeros(size, dtype=np.complex128)
    np.add.at(result, first, current)
    np.add.at(result, second, -current)
    return result, current




def _finite_replay(voltage, w, bridge, output):
    """Own each physical finite exactly once: old-minus-ten plus twenty sublegs."""
    with np.load(PINS['pack'][0], allow_pickle=False) as z:
        old_first, old_second, old_y = (z['finite_' + key] for key in ('first_active_index', 'second_active_index', 'admittance_s'))
    hidden = np.asarray(bridge['hidden_parent_finite_rows'], dtype=np.int64)
    assert hidden.shape == (10,) and len(np.unique(hidden)) == len(hidden)
    keep = np.ones(len(old_first), dtype=bool); keep[hidden] = False
    # Existing terminal rows carry w; new bridge rows replace the ten hidden parents.
    old_contact = np.full(len(old_first), -1, dtype=np.int64); old_sign = np.zeros(len(old_first), dtype=np.int8)
    unchanged = bridge['l04_subleg_index'] < 0
    mapped = bridge['final_finite_row'][unchanged]
    assert np.all(mapped >= 0) and len(np.unique(mapped)) == len(mapped)
    old_contact[mapped] = bridge['contact_index'][unchanged]; old_sign[mapped] = bridge['contact_drop_sign'][unchanged]
    new = bridge['l04_subleg_index'] >= 0
    first = np.r_[old_first[keep], bridge['first_active_index'][new]]
    second = np.r_[old_second[keep], bridge['second_active_index'][new]]
    admittance = np.r_[old_y[keep], 1/(bridge['resistance_ohm'][new] + 2j*np.pi*1e6*bridge['inductance_h'][new])]
    contact = np.r_[old_contact[keep], bridge['contact_index'][new]]
    sign = np.r_[old_sign[keep], bridge['contact_drop_sign'][new]]
    old_row = np.r_[np.flatnonzero(keep), np.full(np.count_nonzero(new), -1, dtype=np.int64)]
    bridge_parent = np.r_[np.full(np.count_nonzero(keep), -1, dtype=np.int64), bridge['previous_parent_finite_row'][new]]
    bridge_subleg = np.r_[np.full(np.count_nonzero(keep), -1, dtype=np.int8), bridge['l04_subleg_index'][new]]
    assert len(first) == len(old_first) - 10 + 20 == 1_692_419
    drop = voltage[first] - voltage[second]; terminal = contact >= 0
    drop[terminal] += sign[terminal] * w[contact[terminal]]
    action, current = _branch_action(first, second, admittance, drop)
    contact_finite = np.zeros(L04_CONTACTS, dtype=np.complex128)
    np.add.at(contact_finite, contact[terminal], sign[terminal] * current[terminal])
    artifact = output / 'finite-full-contact-reconstructed.npz'
    prior.atomic_npz(artifact, first_active_index=first, second_active_index=second, admittance_s=admittance,
                     physical_drop_v=drop, finite_current_first_to_second_a=current, contact_index=contact,
                     contact_drop_sign=sign, old_finite_row=old_row, bridge_parent_finite_row=bridge_parent,
                     bridge_subleg_index=bridge_subleg, hidden_parent_finite_rows=hidden)
    pair_magnitude = abs(admittance)*(abs(voltage[first])+abs(voltage[second]))
    pair_magnitude[terminal] += abs(admittance[terminal])*abs(w[contact[terminal]])
    magnitude = np.zeros(N); np.add.at(magnitude, first, pair_magnitude); np.add.at(magnitude, second, pair_magnitude)
    degree = 4*np.bincount(np.r_[first, second], minlength=N)
    power = complex(np.sum(np.conj(admittance)*abs(drop)**2))
    return action, current, contact_finite, power, receipt(artifact), magnitude, degree


def _l04_replay(field, bridge, output):
    """Independently scatter Bq and reconstruct RT0 facet currents before averaging."""
    q = np.asarray(field['l04_branch_current_a'], dtype=np.complex128)
    dual = np.asarray(field['l04_dual_potential_v'], dtype=np.complex128)
    target = np.asarray(field['l04_target_into_sheet_a'], dtype=np.complex128)
    g = np.asarray(field['l04_independent_contact_current_into_sheet_a'], dtype=np.complex128)
    w = np.asarray(field['l04_contact_potential_v'], dtype=np.complex128)
    assert q.shape == (2_272_974,) and dual.shape == target.shape == (1_628_105,)
    assert g.shape == (L04_CONTACTS-1,) and w.shape == (L04_CONTACTS,)
    assert all(np.all(np.isfinite(value)) for value in (q, dual, target, g, w))
    with np.load(PINS['stream_space'][0], allow_pickle=False) as z:
        first, second = z['branch_first_node'], z['branch_second_node']
        edges = z['branch_mesh_edges']; labels = z['mesh_node_stream_index']; orientation = z['branch_stream_orientation']
        facets, signs = z['local_facet_branch_index'], z['local_outward_flux_sign']
        support = z['contact_support_index']
    with np.load(PINS['stream_space'][0], allow_pickle=False) as z:
        resistance = _load_csr(z, 'r')
    with np.load(PINS['stream_system'][0], allow_pickle=False) as z:
        h = _load_csr(z, 'h')
    root = int(bridge['root_contact_index'][0]); assert root == 25440 and w[root] == 0
    assert np.array_equal(support, bridge['contact_support_index'])
    bq = np.zeros(len(target), dtype=np.complex128); np.add.at(bq, first, q); np.add.at(bq, second, -q)
    expected = np.zeros_like(target)
    independent = np.delete(np.arange(L04_CONTACTS), root)
    expected[L04_FREE_CELLS + independent] = g; expected[L04_FREE_CELLS + root] = -np.sum(g)
    assert np.array_equal(target, expected), 'saved L04 target includes no cell-source rows'
    assert np.array_equal(w, dual[L04_FREE_CELLS:]), 'saved L04 contact offset/dual mismatch'
    rq = resistance @ q; constitutive = rq - (dual[first]-dual[second])
    ctz = np.zeros(h.shape[0], dtype=np.complex128)
    assert h.shape == (644_870, 644_870) and ctz.shape == (h.shape[0],)
    # C^T z is an ordinary signed scatter on retained stream labels; label zero is gauge.
    edge_first = labels[edges[:, 0]]
    edge_second = labels[edges[:, 1]]
    zbranch = rq * orientation
    first_stream = edge_first > 0; second_stream = edge_second > 0
    np.add.at(ctz, edge_first[first_stream]-1, -zbranch[first_stream])
    np.add.at(ctz, edge_second[second_stream]-1, zbranch[second_stream])
    h_diagonal = h.diagonal()
    assert np.all(np.isfinite(h_diagonal)) and np.all(h_diagonal > 0)
    hscale = 1/np.sqrt(h_diagonal)
    energy = float(np.real(np.vdot(q, rq))); stationarity = float(np.linalg.norm(hscale*ctz)/np.sqrt(max(energy, np.finfo(float).tiny)))
    facet_q = np.zeros_like(facets, dtype=np.complex128); active = facets >= 0; facet_q[active] = signs[active]*q[facets[active]]
    assert np.all(facet_q[~active] == 0)
    actual_contact = field['active_voltage_v'][L04_UNIFORM_ACTIVE] + w
    artifact = output / 'l04-full-contact-reconstructed.npz'
    prior.atomic_npz(artifact, l04_branch_current_a=q, l04_dual_potential_v=dual, l04_target_into_sheet_a=target,
                     independently_scattered_bq_a=bq, local_facet_outward_flux_a=facet_q,
                     contact_potential_offset_v=w, actual_contact_voltage_v=actual_contact,
                     contact_support_index=support, root_contact_index=np.asarray([root], dtype=np.int64))
    return {'bq': bq, 'target': target, 'q': q, 'rq': rq, 'w': w, 'actual_contact': actual_contact,
            'joule': complex(np.vdot(q, rq)), 'artifact': receipt(artifact),
            'bq_kcl_max_abs_a': _max_abs(bq-target), 'bq_divergence_kcl_max_abs_a': _max_abs(bq[:L04_FREE_CELLS]-target[:L04_FREE_CELLS]),
            'contact_target_kcl_max_abs_a': _max_abs(bq[L04_FREE_CELLS:]-expected[L04_FREE_CELLS:]),
            'constitutive_max_abs_v': _max_abs(constitutive),
            'constitutive_rq_relative': float(np.linalg.norm(constitutive)/max(np.linalg.norm(rq), np.finfo(float).tiny)),
            'energy_scaled_stationarity_relative': stationarity,
            'exterior_local_facet_current_max_abs_a': _max_abs(facet_q[~active]),
            'electrode_work_ohm': complex(np.vdot(actual_contact, bq[L04_FREE_CELLS:]))}


def _require_solver_contract(result_path, result_sha256, field_path, field_sha256):
    result_path, field_path = Path(result_path), Path(field_path)
    assert len(result_sha256) == len(field_sha256) == 64
    assert prior.sha(result_path) == result_sha256 and prior.sha(field_path) == field_sha256
    result = json.loads(result_path.read_text(encoding='utf-8'))
    assert result['status'] == 'PASS_NUMERICAL_CONDITIONAL_FULL_CONTACT_L04_1MHZ_AWAITING_PHYSICAL_VALIDATION'
    numerical = result['numerical']; assert numerical['info'] == 0 and numerical['final_scaled_residual_relative'] <= 1e-9
    assert result['field']['sha256'] == field_sha256 and result['frequency_hz'] == 1e6 and result['source_current_a'] == 1.0
    for name in ('bridge_result', 'stream_result'):
        assert result['geometry_approximation'] == json.loads(PINS[name][0].read_bytes())['geometry_approximation']
    driver_sha = PINS['coupled_solver_source'][1]
    external_path, external_sha = PINS['coupled_solver_external']
    assert result['driver']['sha256'] == driver_sha, 'solver result must name frozen final driver'
    assert external_path.parent == result_path.parent, 'external receipt must be sibling of accepted solver result'
    external_receipt = receipt(external_path, external_sha)
    external = json.loads(external_path.read_text(encoding='utf-8'))
    assert external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0
    assert external['driver_sha256'] == driver_sha, 'guard/solver frozen driver mismatch'
    return result, receipt(result_path), receipt(field_path), external_receipt


def validate_coupled_field(solver_result_path, solver_result_sha256, field_path, field_sha256, output):
    """Validate a numerical-pass full-contact field and write artifacts before physics gates."""
    output = Path(output); assert output.is_dir() and not (output/'physical-diagnostic.json').exists()
    assert not PINS['coupled_solver_source'][1].startswith('PENDING_'), 'root must freeze final coupled solver driver first'
    assert not PINS['coupled_solver_external'][1].startswith('PENDING_'), 'root must freeze final coupled solver external receipt first'
    started = perf_counter(); inputs = _assert_pinned_inputs()
    frozen_validator = output / 'physical-validator-at-run.py'
    frozen_validator.write_bytes(Path(__file__).read_bytes())
    validator_receipt = receipt(frozen_validator)
    result, result_receipt, field_receipt, external_receipt = _require_solver_contract(solver_result_path, solver_result_sha256, field_path, field_sha256)
    inputs['solver_result'] = result_receipt; inputs['solver_field'] = field_receipt
    inputs['solver_external'] = external_receipt; inputs['validator_driver'] = validator_receipt
    budget = recon._Budget.create(300., 8.); budget.check('full-contact validator start')
    with np.load(field_path, allow_pickle=False) as field:
        assert np.array_equal(field['source_positive_negative_gauge_active_indices'], [POSITIVE, NEGATIVE, GAUGE])
        assert np.array_equal(field['source_current_amplitude_a'], [1.0])
        assert np.array_equal(field['frequency_hz'], [1e6])
        voltage = np.asarray(field['active_voltage_v'], dtype=np.complex128); q25 = np.asarray(field['l25_branch_current_a'], dtype=np.complex128)
        assert voltage.shape == (N,) and q25.shape == (L25_BRANCHES,) and voltage[GAUGE] == 0
        assert np.all(np.isfinite(voltage)) and np.all(np.isfinite(q25))
        with np.load(PINS['bridge'][0], allow_pickle=False) as bridge_archive:
            bridge = {name: bridge_archive[name] for name in bridge_archive.files}
        l04 = _l04_replay(field, bridge, output)
        finite_action, _, contact_finite, finite_power, finite_artifact, finite_physical_magnitude, finite_physical_degree = _finite_replay(voltage, l04['w'], bridge, output)
    budget.check('L04 and physical-finite replay saved')
    with np.load(PINS['operator'][0], allow_pickle=False) as op:
        y, r25, b = (_load_csc(op, name) for name in ('y', 'r', 'b'))
        terminal = op['conditional_local_facet_global_active_index']
    l02_action, magnitude, degree, power, l02_metrics = fd28.reconstruct(voltage, terminal, output, budget)
    source = l02_action + finite_action; power['finite_full_contact'] = finite_power
    # Keep the physical, post-bridge construction side of both source envelopes.
    magnitude += finite_physical_magnitude
    degree += finite_physical_degree
    with np.load(PINS['pack'][0], allow_pickle=False) as pack:
        cf, cs, cy = (pack['contact_gc_' + key] for key in ('first_active_index', 'second_active_index', 'admittance_s'))
        action, _ = _branch_action(cf, cs, cy, voltage[cf]-voltage[cs]); source += action
        power['l02_contact_gc'] = complex(np.sum(np.conj(cy)*abs(voltage[cf]-voltage[cs])**2))
        contact_pair = abs(cy)*(abs(voltage[cf])+abs(voltage[cs]))
        np.add.at(magnitude, cf, contact_pair); np.add.at(magnitude, cs, contact_pair)
        degree += 4*np.bincount(np.r_[cf, cs], minlength=N)
        names = json.loads(pack['category_names_json_utf8'].tobytes())
        assert names == ['retained_gc', 'termination', 'l14_sheet_dc', 'l14_distributed_gc', 'l25_distributed_gc']
        for name in names:
            matrix = _load_csc(pack, 'category_' + name); action = matrix @ voltage[:1_483_296]
            source[:1_483_296] += action
            magnitude[:1_483_296] += abs(matrix)@abs(voltage[:1_483_296])
            degree[:1_483_296] += prior.row_degree(matrix)
            power[name] = complex(np.conj(np.vdot(voltage[:1_483_296], action)))
            del matrix, action
    delta = _changed_star(bridge)
    u = _load_csc(bridge, 'u'); root = int(bridge['root_contact_index'][0]); w_nonroot = np.delete(l04['w'], root)
    matrix_potential = y@voltage + delta@voltage + u@w_nonroot
    old_first, old_second, old_admittance = _finite_old_tuple()
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
    finite_forward = _max_abs(finite_difference/finite_bound)
    source_difference = source - matrix_potential
    matrix_magnitude = abs(y)@abs(voltage) + abs(delta)@abs(voltage) + abs(u)@abs(w_nonroot)
    matrix_degree = prior.row_degree(y) + prior.row_degree(delta) + prior.row_degree(u)
    source_bound = prior.gamma(16*(degree+matrix_degree+64))*np.maximum(
        magnitude+matrix_magnitude, np.finfo(float).tiny)
    source_forward = _max_abs(source_difference/source_bound)
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
        'status': 'PASS_FULL_CONTACT_L04_COUPLED_PHYSICAL_FIELD' if all(gates.values()) else 'UNVALIDATED_FULL_CONTACT_L04_COUPLED_PHYSICAL_FIELD',
        'validator_sha256': prior.sha(Path(__file__)), 'validator_driver': validator_receipt, 'inputs': inputs,
        'artifacts': {'finite_reconstruction': finite_artifact, 'l04_reconstruction': l04['artifact'], 'l02_reconstruction': l02_metrics['reconstructed_field']},
        'solver_numerical': result['numerical'],
        'geometry_approximation': result['geometry_approximation'], 'frequency_hz': 1e6, 'source_current_a': 1.0,
        'physical': {**l02_metrics, 'zdd_ohm': _pair(zdd), 'matrix_kcl_max_abs_a': _max_abs(matrix_kcl),
          'physical_kcl_max_abs_a': _max_abs(physical_kcl), 'finite_forward_envelope_ratio': finite_forward, 'full_source_forward_envelope_ratio': source_forward,
          'l25_constitutive_max_abs_v': _max_abs(constitutive25), 'l04_bq_scatter_max_abs_a': l04['bq_kcl_max_abs_a'], 'l04_bq_divergence_kcl_max_abs_a': l04['bq_divergence_kcl_max_abs_a'],
          'l04_contact_kcl_max_abs_a': _max_abs(contact_kcl), 'l04_constitutive_max_abs_v': l04['constitutive_max_abs_v'],
          'l04_constitutive_rq_relative': l04['constitutive_rq_relative'],
          'l04_energy_scaled_stationarity_relative': l04['energy_scaled_stationarity_relative'],
          'l04_electrode_work_ohm': _pair(l04['electrode_work_ohm']), 'power_contributions_ohm': {k:_pair(v) for k,v in power.items()},
          'physical_power_closure_error_ohm': physical_error, 'matrix_power_closure_error_ohm': matrix_power_error},
        'gates': {name: bool(value) for name, value in gates.items()}, 'budget': budget.receipt(), 'elapsed_seconds': perf_counter()-started,
        'scope': 'Exact saved full-contact L04 RT0 action and ten-star finite bridge at 1 MHz. No magnetic/FMM, broad-band, mesh, or PowerSI claim.'}
    prior.atomic_json(output/'physical-diagnostic.json', diagnostic)
    assert all(gates.values()), {k:v for k,v in gates.items() if not v}
    return diagnostic


def _finite_old_tuple():
    with np.load(PINS['pack'][0], allow_pickle=False) as z:
        return tuple(z['finite_' + key] for key in ('first_active_index', 'second_active_index', 'admittance_s'))


def self_check():
    """Synthetic sign/power/once-owner check only; never opens saved numerical fields."""
    old_first = np.array([1, 2]); old_second = np.array([2, 3]); old_y = np.array([2+1j, 3-2j])
    v = np.array([0, 1+.5j, -.25j, .75, .1-.3j], complex); w = np.array([.3-.2j])
    # Remove old 1->2 and replace by two sublegs whose contact signs are opposite at the split.
    physical_first = np.array([2, 1, 4]); physical_second = np.array([3, 4, 2]); physical_y = np.array([old_y[1], 4+0j, 4+0j])
    drops = v[physical_first]-v[physical_second]; drops[1:] += np.array([1, -1])*w[0]
    action, current = _branch_action(physical_first, physical_second, physical_y, drops, 5)
    zero_action, _ = _branch_action(physical_first, physical_second, physical_y,
                                    v[physical_first]-v[physical_second], 5)
    expected_uw, _ = _branch_action(physical_first[1:], physical_second[1:], physical_y[1:],
                                    np.array([w[0], -w[0]]), 5)
    # Contact term separates exactly as U*w; no original hidden leg remains.
    assert np.max(abs(action-zero_action-expected_uw)) < 1e-14
    power = np.sum(np.conj(physical_y)*abs(drops)**2)
    assert power.real > 0 and len(current) == 3
    print('PASS_FULL_CONTACT_VALIDATOR_SYNTHETIC_SIGN_POWER_ONCE_OWNER')


def main():
    parser = argparse.ArgumentParser(description=f'{PROGRAM} {VERSION}: disabled future full-contact validator')
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check:
        self_check(); return
    raise SystemExit('API-only: call validate_coupled_field with accepted solver result and field receipts.')


if __name__ == '__main__':
    main()
