"""SPD Decap PI Evaluator v0.23.1: measure an unvalidated checkpoint, never accept it."""
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
import validate_astra_l04_full_contact_coupled_field as original
from validate_astra_l04_full_contact_coupled_field import (
    ROOT, R, PROGRAM, VERSION, N, POSITIVE, NEGATIVE, GAUGE, L25_BRANCHES,
    L04_FREE_CELLS, fd28, recon, prior, receipt, _load_csc, _max_abs, _pair,
    _l04_replay, _finite_replay, _changed_star, _finite_old_tuple, _branch_action)

RUN_RELEASED = True
EXTERNAL_SECONDS, MEMORY_GIB = 330.0, 8.0
MEMORY_COUNTER_SOURCE_SHA = "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"
SOURCE_SHA = "713c02702f0d1e9629f4485af83c3b2946d6e8daf4cccaa6b028c67a39bea466"
RECOVERY_DIR = R / 'astra-l04-unvalidated-checkpoint-recovery-01'
PINS = {k:v for k,v in original.PINS.items() if not k.startswith('coupled_solver_')}
PINS.update({
    'original_validator': (Path(original.__file__), SOURCE_SHA),
    'recovery_result': (RECOVERY_DIR / 'result.json', 'c76eb48a051827b5b9753089013f33342197babb90abef00c2e65e95fc1e254e'),
    'recovery_driver': (RECOVERY_DIR / 'driver-at-run.py', 'c057e25597038594495f7c493db84f5d14741bdc6c4dae38d2140cae39c21daf'),
    'recovery_external': (RECOVERY_DIR / 'external-budget.json', '44d0c536a8c5e651f3ee65583d67a61b175db6efb78c5f88d71df8d7c7d587a9'),
    'recovery_field': (RECOVERY_DIR / 'recovered-unvalidated-full-contact-l04-field.npz', '20371026f09b0c2807156bffecd4ff9a254a7e547e21a46d03e3244dd1527bda'),
})
sha = prior.sha

def pending():
    return [name for name, (_, digest) in PINS.items() if digest.startswith('PENDING_')]

def _assert_pinned_inputs():
    assert not pending(), pending()
    return {k:receipt(p, h) for k,(p,h) in PINS.items()}

def _require_recovery_contract(result_path, result_sha256, field_path, field_sha256):
    result_path, field_path = Path(result_path), Path(field_path)
    rr, fr = receipt(result_path, result_sha256), receipt(field_path, field_sha256)
    assert rr['sha256'] == PINS['recovery_result'][1]
    assert fr['sha256'] == PINS['recovery_field'][1]
    result = json.loads(result_path.read_text())
    assert result['status'] == 'RECOVERED_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT'
    assert result['field']['sha256'] == fr['sha256']
    assert result['frequency_hz'] == 1e6 and result['source_current_a'] == 1.0
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
        'status': 'DIAGNOSTIC_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT',
        'original_numerical_gate': result['original_numerical_gate'],
        'physical_gates_all_pass': bool(all(gates.values())),
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
        'scope': 'Diagnostic only; no acceptance even if all physical gates pass. Exact saved full-contact L04 RT0 action and ten-star finite bridge at 1 MHz. No magnetic/FMM, broad-band, mesh, or PowerSI claim.'}
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
