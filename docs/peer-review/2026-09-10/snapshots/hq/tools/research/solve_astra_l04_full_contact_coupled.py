"""One conditional 1 MHz solve with every L04 contact and the saved exact NtD."""
import argparse
import gc
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gmres

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import prepare_astra_l04_contact_ntd_action as ntd
import reconstruct_astra_native_loaded_field as recon

ROOT, R = base.ROOT, base.R
RUN_RELEASED = True  # Sol accepted902e6f mechanics; root verified qualifier03/source identity pins.
NV, NI, NC = 3178103, 604031, 38277
NX, TOTAL = NV + NI, NV + NI + NC
RTOL, RESTART, MAX_CYCLES = 1e-9, 12, 10
MEMORY_BYTES = 32 * 2**30
PINS = {
    'base_helper': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'balanced_helper': (ROOT / 'tools/research/solve_astra_l02_l14_l25_combined_block_lgmres.py',
                        '853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692'),
    'operator': (R / 'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz',
                 '5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'),
    'warm_result': (R / 'astra-l02-hybrid-right-correction-01/result.json',
                    '7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3'),
    'warm_field': (R / 'astra-l02-hybrid-right-correction-01/field.npz',
                   '960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b'),
    'bridge_result': (R / 'astra-l04-full-contact-circuit-bridge-01/result.json',
                      '1583612f90fca3c97f7b9fb4f8c3a0a004d6ae8d84ccc1f7bbc8f382247d266f'),
    'bridge_guard': (R / 'astra-l04-full-contact-circuit-bridge-01/external-budget.json',
                     '756adf0057f6e9da39e5d8e3f34c67c4917080a455b77860dd5d54a52bd0e3d8'),
    'bridge': (R / 'astra-l04-full-contact-circuit-bridge-01/l04-full-contact-circuit-bridge.npz',
               '02508f2e1588fe5e6ad4231b9c128a3324a88baf0229293d6a8b70ea10380e57'),
    'ntd_helper': (Path(ntd.__file__), 'e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8'),
    'ntd_result': (R / 'astra-l04-contact-ntd-action-03/result.json',
                    'a583a96e466f8f5eee14c0b8d6cc23e120512b65a0c19c687886d5a28d3dce2c'),
    'ntd_qualifier': (ROOT / 'tools/research/qualify_astra_l04_contact_ntd_saved_diagnostic.py',
                       '6eb11fcb97289aed527b3ce36f671bf5a776a390f4cadd84781a98f90e3f14eb'),
    'ntd_guard': (R / 'astra-l04-contact-ntd-action-02/external-budget.json',
                   'd51acba383cb5bda0d09fcf10793be6a31de4a048567911bcef020d6bd124266'),
    'ntd_diagnostic': (R / 'astra-l04-contact-ntd-action-02/saved-rhs-replay-diagnostics.json',
                        '4d20a2542666f5b8e122712d4849307d7ce4132f64cc6ca8f51476adfca0d02e'),
    'budget': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
    'guard': (ROOT / 'tools/research/probe_astra_fmm3d_runtime.py',
              '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'),
    'memory_counter': (ROOT / 'tools/research/probe_astra_l25_rt0_p1_pair.py',
                       '35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20'),
}


def interface_apply(a_apply, u, diagonal, z_apply, x, g, nvoltage):
    """Symmetric MNA after w=Zg: no dense contact matrix or rank reduction."""
    drop = (u.T @ x[:nvoltage] + g) / diagonal
    top = a_apply(x)
    top[:nvoltage] -= u @ drop
    return np.r_[top, -drop - z_apply(g)]


def interface_precondition(solve_a, u, diagonal, rx, rg, nvoltage):
    """Exact inverse for Z=0 if solve_a is exact; otherwise a preconditioner."""
    rhs = np.array(rx, dtype=np.complex128, copy=True)
    rhs[:nvoltage] -= u @ rg
    x = solve_a(rhs)
    return np.r_[x, -u.T @ x[:nvoltage] - diagonal * rg]


def changed_star(bridge, potential_count=NV+1):
    select = bridge['l04_subleg_index'] >= 0
    first = np.r_[bridge['old_hidden_first_active_index'], bridge['first_active_index'][select]]
    second = np.r_[bridge['old_hidden_second_active_index'], bridge['second_active_index'][select]]
    old = 1 / (bridge['old_hidden_resistance_ohm'] + 2j*np.pi*1e6*bridge['old_hidden_inductance_h'])
    new = 1 / (bridge['resistance_ohm'][select] + 2j*np.pi*1e6*bridge['inductance_h'][select])
    y = np.r_[-old, new]
    delta = sparse.coo_matrix((np.tile(y, 4) * np.repeat([1, 1, -1, -1], len(y)),
        (np.r_[first, second, first, second], np.r_[first, second, second, first])), shape=(potential_count, potential_count)).tocsc()
    delta.sum_duplicates(); delta.eliminate_zeros()
    return delta[1:, 1:].tocsc()


def self_check():
    # Compare the complete 3-block equations with their symmetric elimination.
    a = np.array([[8+.2j, -1, .3], [-1, 6+.4j, -.2], [.3, -.2, -2]], complex)
    u = np.array([[1+.1j, -.3], [.2, .8+.2j]], complex)
    ue = np.vstack([u, np.zeros((1, 2))])
    d = np.array([3+.4j, 4+.2j])
    z = np.array([[.5, .1], [.1, .8]])
    full = np.block([[a, ue, np.zeros((3, 2))],
                     [ue.T, np.diag(d), np.eye(2)],
                     [np.zeros((2, 3)), np.eye(2), -z]])
    rhs = np.r_[np.array([1, -1, 0]), np.zeros(4)]
    expected = np.linalg.solve(full, rhs)
    apply = lambda v: interface_apply(lambda x: a@x, u, d, lambda g: z@g, v[:3], v[3:], 2)
    matrix = np.column_stack([apply(v) for v in np.eye(5)])
    solved = np.linalg.solve(matrix, rhs[:5])
    assert np.max(abs(matrix-matrix.T)) < 2e-14
    assert np.max(abs(solved-np.r_[expected[:3], expected[5:]])) < 2e-14
    zero = np.column_stack([interface_apply(lambda x: a@x, u, d, lambda g: 0*g,
                            v[:3], v[3:], 2) for v in np.eye(5)])
    pre = np.column_stack([interface_precondition(lambda r: np.linalg.solve(a, r), u, d,
                          v[:3], v[3:], 2) for v in np.eye(5)])
    assert np.max(abs(zero@pre-np.eye(5))) < 2e-14
    assert np.max(abs(pre-pre.T)) < 2e-14
    bridge = {k: np.array(v) for k, v in dict(l04_subleg_index=[0, 1],
        old_hidden_first_active_index=[1], old_hidden_second_active_index=[2],
        old_hidden_resistance_ohm=[1.], old_hidden_inductance_h=[0.],
        first_active_index=[1, 3], second_active_index=[3, 2],
        resistance_ohm=[.4, .6], inductance_h=[0., 0.]).items()}
    delta = changed_star(bridge, 4).toarray()
    d0, d1, d2 = np.array([1., -1., 0]), np.array([1., 0, -1.]), np.array([0., -1., 1.])
    assert np.max(abs(delta-(-np.outer(d0,d0)+np.outer(d1,d1)/.4+np.outer(d2,d2)/.6))) < 2e-14
    print('PASS_FULL_CONTACT_MNA_ELIMINATION_AND_Z0_PRECONDITIONER')


def residual_metrics(residual, sv, parts, rhs_norm):
    names = ('native', 'l14', 'l25', 'l02')
    squared = {name: float(np.vdot(residual[rows], residual[rows]).real) for name, rows in zip(names, parts)}
    squared.update(l25_current=float(np.vdot(residual[NV:NX], residual[NV:NX]).real),
                   l04_contact=float(np.vdot(residual[NX:], residual[NX:]).real))
    physical = residual[:NV]/sv
    top = np.argpartition(abs(physical), -16)[-16:]
    top = top[np.argsort(abs(physical[top]))[::-1]]
    return dict(scaled_residual_relative=float(np.linalg.norm(residual)/rhs_norm),
        scaled_residual_squared_norms=squared, top_physical_potential_row_indices=(top+1).tolist(),
        top_physical_potential_row_residuals_a=[base.pair(physical[i]) for i in top],
        scope='Top residuals belong to the eliminated MNA potential equations; full physical KCL is checked separately.')


def worker(output):
    budget = recon._Budget.create(840, 32)
    events = base.Events(output / 'progress.jsonl')
    try:
        frozen = output / 'driver-at-run.py'
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = {key: ntd.receipt(path, digest) for key, (path, digest) in PINS.items()}
        bridge_result = json.loads(PINS['bridge_result'][0].read_bytes())
        ntd_result = json.loads(PINS['ntd_result'][0].read_bytes())
        warm_result = json.loads(PINS['warm_result'][0].read_bytes())
        assert bridge_result['status'] == 'PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE'
        assert bridge_result['artifact']['sha256'] == PINS['bridge'][1]
        assert ntd_result['status'] == 'QUALIFIED_SAVED_L04_CONTACT_NTD_DIAGNOSTIC_NO_REPLAY'
        assert ntd_result['driver']['sha256'] == PINS['ntd_qualifier'][1]
        assert ntd_result['algorithm_identity']['canonical_action_sha256'] == PINS['ntd_helper'][1]
        assert ntd_result['algorithm_identity']['normalized_source_equals_frozen_probe02'] is True
        assert ntd_result['inputs']['probe02_external']['sha256'] == PINS['ntd_guard'][1]
        assert ntd_result['inputs']['probe02_diagnostics']['sha256'] == PINS['ntd_diagnostic'][1]
        assert warm_result['field']['sha256'] == PINS['warm_field'][1] and warm_result['lgmres']['info'] == 0
        guard_result = json.loads(PINS['bridge_guard'][0].read_bytes())
        assert guard_result['status'] == 'COMPLETED_NATIVE_WORKER' and guard_result['exit_code'] == 0
        ntd_guard = json.loads(PINS['ntd_guard'][0].read_bytes())
        assert ntd_guard['status'] == 'STOP_NATIVE_WORKER_EXIT' and ntd_guard['exit_code'] == 1
        _, stream_result, _ = ntd.verify_contract()
        assert bridge_result['geometry_approximation'] == stream_result['geometry_approximation']
        assert bridge_result['frequency_hz'] == stream_result['frequency_hz'] == 1e6
        with np.load(PINS['operator'][0], allow_pickle=False) as archive:
            y = base.csc(archive, 'y')[1:, 1:].tocsc()
            b = base.csc(archive, 'b')[1:, :].tocsc()
            r = base.csc(archive, 'r')
            parts = base.partition(archive['conditional_global_active_index'])
            positive, negative, gauge = (int(archive[k][0]) for k in
                ('positive_active_index', 'negative_active_index', 'gauge_active_index'))
        assert (positive, negative, gauge) == (2699, 2656, 0)
        assert y.shape == (NV, NV) and b.shape == (NV, NI) and r.shape == (NI, NI)
        with np.load(PINS['bridge'][0], allow_pickle=False) as archive:
            u = base.csc(archive, 'u')[1:, :].tocsc()
            diagonal = archive['contact_diagonal_admittance_s']
            delta = changed_star(archive)
            assert int(archive['root_contact_index'][0]) == stream_result['metrics']['gauge_contact_graph_row']-ntd.FREE_CELL_COUNT
            with np.load(ntd.PINS['stream_space'][0], allow_pickle=False) as space:
                assert np.array_equal(archive['contact_support_index'], space['contact_support_index'])
        assert u.shape == (NV, NC) and diagonal.shape == (NC,) and np.all(diagonal.real > 0)
        sv = 1 / np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel()+np.asarray(abs(b).sum(axis=1)).ravel())
        si = 1 / np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel()+np.asarray(abs(r).sum(axis=1)).ravel())
        sg = np.sqrt(abs(diagonal))
        sx, scales = np.r_[sv, si], np.r_[sv, si, sg]
        assert np.all(np.isfinite(scales)) and np.all(scales > 0)
        native, l14, l25, l02 = parts
        factors, factor_reports = {}, {}
        y25 = base.scaled_block(y[l25, :][:, l25], sv[l25], sv[l25])
        b25 = base.scaled_block(b[l25, :], sv[l25], si)
        r25 = base.scaled_block(r, si, si)
        block = sparse.bmat([[y25, b25], [b25.T, -r25]], format='csc')
        del y25, b25, r25
        base.factor_block('l25_current', block, factors, factor_reports, output, events)
        del block
        for name, rows in (('native', native), ('l14', l14), ('l02_exact', l02)):
            block = base.scaled_block(y[rows, :][:, rows], sv[rows], sv[rows])
            base.factor_block(name, block, factors, factor_reports, output, events)
            del block
            budget.check(name+' factor')
        # Avoid overlapping the large base LU construction with NtD setup temporaries.
        action = ntd.load_action(stream_result)
        events.emit('ntd_factor_ready', factor_seconds=action.factor_seconds, setup_seconds=action.setup_seconds)
        def old_a(x):
            return np.r_[y@x[:NV]+b@x[NV:], b.T@x[:NV]-r@x[NV:]]
        def new_a(x):
            value = old_a(x)
            value[:NV] += delta@x[:NV]
            return value
        def solve_base_scaled(value):
            solved = np.empty_like(value)
            for name, rows in (('native', native), ('l14', l14), ('l02_exact', l02)):
                solved[rows] = factors[name].solve(value[rows])
            local = factors['l25_current'].solve(np.r_[value[l25], value[NV:]])
            solved[l25], solved[NV:] = local[:len(l25)], local[len(l25):]
            return solved
        def solve_base_physical(value):
            return sx * solve_base_scaled(sx * value)
        calls = {'operator': 0, 'preconditioner': 0}
        def operator(value):
            budget.check('full contact operator')
            calls['operator'] += 1
            physical = scales * value
            return scales * interface_apply(new_a, u, diagonal, action.apply, physical[:NX], physical[NX:], NV)
        def pre_base(value):
            physical = value / scales
            return interface_precondition(solve_base_physical, u, diagonal, physical[:NX], physical[NX:], NV) / scales
        with np.load(PINS['warm_field'][0], allow_pickle=False) as warm:
            warm_x = np.r_[warm['active_voltage_v'][1:], warm['l25_branch_current_a']]
        with np.load(ntd.PINS['dual_artifact'][0], allow_pickle=False) as dual:
            warm_g = dual['original_contact_target_into_sheet_a'][action.independent_contacts]
        initial = np.r_[warm_x, warm_g] / scales
        assert initial.shape == (TOTAL,) and np.all(np.isfinite(initial))
        # These directions accelerate iteration only; every contact remains an unknown.
        columns, harmonic = [], {}
        for name, rows in (('l14', l14), ('l25', l25), ('l02', l02)):
            v = np.zeros(NX, complex); v[rows] = 1/sv[rows]
            first = sx*old_a(sx*v)
            v[native] = -factors['native'].solve(first[native])
            correction = np.zeros(NX, complex); correction[native] = v[native]
            corrected = (sx*old_a(sx*correction))[native]
            relative = np.linalg.norm(first[native]+corrected)/max(np.linalg.norm(first[native])+np.linalg.norm(corrected), 1e-300)
            assert relative < 2e-9
            harmonic[name] = float(relative)
            mode = np.r_[v, -(u.T@(sv*v[:NV]))/sg]
            columns.append(mode/np.linalg.norm(mode))
        for current in (warm_g.real, warm_g.imag):
            mode = np.r_[np.zeros(NX, complex), current/sg]
            assert np.linalg.norm(mode) > 0
            columns.append(mode/np.linalg.norm(mode))
        cv = np.column_stack(columns)
        del columns, v, correction, corrected, first, mode, warm_x
        cw = np.column_stack([operator(cv[:, i]) for i in range(cv.shape[1])])
        coarse = cv.T@cw
        singular = np.linalg.svd(coarse, compute_uv=False)
        symmetry = np.max(abs(coarse-coarse.T))/max(np.max(abs(coarse)), 1e-300)
        coarse_report = dict(harmonic_old_base_relative=harmonic, singular_values=singular.tolist(),
            symmetry_relative=float(symmetry), condition_2=float(singular[0]/singular[-1]),
            mode_names=['l14_constant', 'l25_constant', 'l02_constant', 'saved_l04_current_real', 'saved_l04_current_imag'],
            scope='Preconditioning only; full 38277 independent L04 contact drives remain in the true operator.')
        base.atomic_json(output / 'coarse-diagnostic.json', coarse_report)
        assert symmetry < 2e-8 and np.all(singular > 0) and singular[0]/singular[-1] < 1e12
        def pre(value):
            calls['preconditioner'] += 1
            return base.balanced_apply(pre_base, cv, cw, coarse, value)
        mode_error = max(np.linalg.norm(pre(cw[:, i])-cv[:, i]) for i in range(cv.shape[1]))
        assert mode_error < 2e-8
        rhs = np.zeros(TOTAL, complex)
        rhs[positive-1], rhs[negative-1] = sv[positive-1], -sv[negative-1]
        rhs_norm = np.linalg.norm(rhs)
        residual0 = rhs-operator(initial)
        initial_relative = float(np.linalg.norm(residual0)/rhs_norm)
        history, latest = [], initial.copy()
        solve_started = perf_counter()
        def right_apply(value):
            if perf_counter()-solve_started > 660:
                raise TimeoutError('660 second iteration budget; preserve the last completed restart')
            return operator(pre(value))
        def callback(z):
            nonlocal latest
            latest = initial+pre(z)
            residual = operator(latest)-rhs
            history.append(dict(cycle=len(history)+1, **residual_metrics(residual, sv, parts, rhs_norm),
                                solve_seconds=perf_counter()-solve_started, calls=calls.copy()))
            physical = scales*latest
            base.atomic_npz(output / 'latest-unvalidated-restart-field.npz', active_voltage_v=np.r_[0j, physical[:NV]],
                l25_branch_current_a=physical[NV:NX], l04_independent_contact_current_into_sheet_a=physical[NX:])
            events.emit('gmres_restart', **history[-1])
        events.emit('gmres_start', initial_scaled_residual_relative=initial_relative,
            restart=RESTART, max_cycles=MAX_CYCLES, iteration_budget_s=660, mode_error=float(mode_error))
        try:
            z, info = gmres(LinearOperator((TOTAL, TOTAL), matvec=right_apply, dtype=np.complex128),
                residual0, x0=np.zeros(TOTAL, complex), rtol=0, atol=RTOL*rhs_norm,
                restart=RESTART, maxiter=MAX_CYCLES, callback=callback, callback_type='x')
            solved = initial+pre(z)
        except TimeoutError:
            solved, info = latest, -660
        final_residual = operator(solved)-rhs
        final_relative = float(np.linalg.norm(final_residual)/rhs_norm)
        final_blocks = residual_metrics(final_residual, sv, parts, rhs_norm)
        physical = scales*solved
        voltage, q25, g = np.r_[0j, physical[:NV]], physical[NV:NX].copy(), physical[NX:].copy()
        # Full L04 recovery is a separate memory phase, after every large base/Krylov owner is gone.
        del right_apply, callback, pre, pre_base, solve_base_physical, solve_base_scaled, operator, new_a, old_a
        del factors, y, b, r, delta, cv, cw, coarse, latest, solved, initial, residual0, final_residual, physical
        del scales, sx, sv, si, sg, rhs, parts, native, l14, l25, l02
        if 'z' in locals():
            del z
        gc.collect()
        budget.check('released circuit factors before L04 field recovery')
        final_l04 = action.apply(g, return_field=True)
        w = final_l04.dual_potential_v[action.free_cell_count:]
        circuit_contact_kcl = u.T@voltage[1:]+diagonal*w[action.independent_contacts]+g
        checkpoint = output / 'unvalidated-field.npz'
        base.atomic_npz(checkpoint, active_voltage_v=voltage, l25_branch_current_a=q25,
            l04_independent_contact_current_into_sheet_a=g, l04_contact_potential_v=w,
            l04_branch_current_a=final_l04.branch_current_a, l04_dual_potential_v=final_l04.dual_potential_v,
            l04_target_into_sheet_a=final_l04.target_bq_a,
            source_positive_negative_gauge_active_indices=np.array([positive, negative, gauge]),
            source_current_amplitude_a=np.array([1.0]), frequency_hz=np.array([1e6]))
        numerical = dict(info=int(info), initial_scaled_residual_relative=initial_relative,
            final_scaled_residual_relative=final_relative, iteration_history=history,
            final_residual_blocks=final_blocks,
            solve_seconds=perf_counter()-solve_started, calls=calls,
            independent_contact_kcl_max_abs_a=float(np.max(abs(circuit_contact_kcl))),
            l04_final_field=final_l04.metrics, restart=RESTART, max_cycles=MAX_CYCLES, rtol=RTOL)
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_NUMERICAL_CONDITIONAL_FULL_CONTACT_L04_1MHZ_AWAITING_PHYSICAL_VALIDATION'
                if info == 0 and final_relative <= RTOL else 'STOP_UNCONVERGED_FULL_CONTACT_L04_1MHZ',
            driver=base.receipt(frozen), inputs=inputs, field=base.receipt(checkpoint), numerical=numerical,
            geometry_approximation=bridge_result['geometry_approximation'], frequency_hz=1e6,
            source_current_a=1.0, zdd_ohm_unvalidated=base.pair(voltage[positive]-voltage[negative]),
            factor=factor_reports, coarse=coarse_report, budget=budget.receipt(),
            resource_limits=dict(external_runtime_s=900, max_memory_bytes=MEMORY_BYTES,
                rationale='Existing exact-L02 measured peak25,198,362,624B plus NtD3,359,879,168B exceeds the old24GiB guard; this run has an explicit32GiB cap.'),
            scope='New full-contact coupled conditional L04 R model. Exact saved NtD, all contact drives, '
                  'and once-owned ten-path finite replacement. Old A blocks are a preconditioner only. '
                  'Warm solve time is not a cold solve. Missing physical G/C, magnetic self/mutual '
                  'and adaptive/broadband qualifications remain open. No accuracy acceptance before separate physical validation.')
        base.atomic_json(output / 'result.json', report)
        events.emit('numerical_complete', status=report['status'], info=int(info), relative=final_relative)
    except BaseException:
        base.atomic_json(output / 'failure.json', dict(status='STOP_FULL_CONTACT_L04_COUPLED_WORKER',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise
    finally:
        gc.collect()


def launch(output, self_check=False):
    """Owned-child watchdog adapted from pinned guard2961; this run explicitly permits32GiB."""
    import ctypes
    import ctypes.wintypes
    import subprocess
    import time
    import probe_astra_l25_rt0_p1_pair as counter
    assert base.sha(PINS['guard'][0]) == PINS['guard'][1]
    assert base.sha(Path(counter.__file__)) == PINS['memory_counter'][1]
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ('total', 'available', 'totalpage', 'availablepage',
                                                   'totalvirtual', 'availablevirtual', 'extended')]
    memory = Memory(); memory.length = ctypes.sizeof(memory)
    assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    available = dict(total_physical_bytes=memory.total, available_physical_bytes=memory.available,
                     available_commit_bytes=memory.availablepage)
    if not self_check:
        assert min(memory.available, memory.availablepage) >= MEMORY_BYTES+2*2**30, available
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    output.mkdir(parents=True, exist_ok=False)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(output)]
    runtime = .5 if self_check else 900.
    if self_check:
        ready = output / 'native-hold-started.txt'
        code = f"import ctypes;from pathlib import Path;Path({str(ready)!r}).write_text('PyDLL Sleep entry');ctypes.PyDLL('kernel32').Sleep(10000)"
        command = [sys.executable, '-B', '-c', code]
    started, private, working, reason = time.monotonic(), 0, 0, None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW) as child:
        print(f'L04 coupled guard: owned PID={child.pid},{runtime}s/32GiB', flush=True)
        try:
            while child.poll() is None:
                counters = counter._MemoryCounters(); counters.cb = ctypes.sizeof(counters)
                if not getter(int(child._handle), ctypes.byref(counters), counters.cb):
                    if child.poll() is not None:
                        break
                    reason = 'STOP_PROCESS_MEMORY_QUERY'
                else:
                    private, working = max(private, int(counters.private_usage)), max(working, int(counters.working_set))
                if time.monotonic()-started >= runtime:
                    reason = 'STOP_EXTERNAL_RUNTIME_BUDGET'
                if max(private, working) > MEMORY_BYTES:
                    reason = 'STOP_EXTERNAL_MEMORY_BUDGET'
                if reason:
                    child.kill()
                    break
                time.sleep(.5)
            code = child.wait(timeout=10)
        except BaseException:
            if child.poll() is None:
                child.kill(); child.wait(timeout=10)
            raise
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status=reason or ('COMPLETED_NATIVE_WORKER' if code == 0 else 'STOP_NATIVE_WORKER_EXIT'),
            owned_pid=child.pid, exit_code=code, elapsed_s=time.monotonic()-started,
            sampled_peak_private_bytes=private, sampled_peak_working_set_bytes=working,
            max_runtime_s=runtime, max_memory_bytes=MEMORY_BYTES, sampling_interval_s=.5,
            driver_sha256=base.sha(Path(__file__)), worker_command=command, memory_at_launch=available,
            adapted_guard_sha256=PINS['guard'][1])
    if self_check:
        assert ready.exists() and reason == 'STOP_EXTERNAL_RUNTIME_BUDGET' and report['elapsed_s'] < 3, report
        report['self_check'] = 'PASS_EXTERNAL_NATIVE_HOLD_TIMEOUT'
    base.atomic_json(output / 'external-budget.json', report)
    print(json.dumps(report), flush=True)
    return 0 if self_check or (reason is None and code == 0) else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check', action='store_true')
    modes.add_argument('--guard-self-check', action='store_true')
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--native-worker', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.guard_self_check:
        assert args.output is not None
        raise SystemExit(launch(args.output.resolve(), self_check=True))
    else:
        assert RUN_RELEASED and args.output is not None, 'Held pending Sol/root source and NtD review'
        output = args.output.resolve()
        if args.native_worker:
            worker(output)
        else:
            raise SystemExit(launch(output))
