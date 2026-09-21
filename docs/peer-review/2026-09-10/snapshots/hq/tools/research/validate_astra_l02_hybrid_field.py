"""Validate a saved conditional hybrid field against its physical source equations."""
import gc
import json
from pathlib import Path

import numpy as np
from scipy import sparse

import assemble_astra_l25_rt0_resistance as local_r
import reconstruct_astra_native_loaded_field as recon
import solve_astra_l02_l14_l25_combined_block_lgmres as prior

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    'operator': (R/'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz', '5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'),
    'pack': (R/'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz', '01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c'),
    'restricted': (R/'astra-l02-restricted-hybrid-cell-geometry-02/restricted-hybrid-cell-geometry.npz', 'e03297ce292d082422585e71e47a05c3d3caed8ccfe0cd24e99d06ebeb7eac0b'),
    'space': (R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz', '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f'),
    'mesh': (R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz', '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9'),
    'prior_helper': (Path(prior.__file__), '853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692'),
    'local_r_helper': (Path(local_r.__file__), 'ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010'),
    'budget_helper': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}
RESULTS = {
    'operator': ('604a44f12ce843b4ccbf2d268ae4a6887b95cf9c3133c8f55576a484b49bc7b5', 'PASS_CONDITIONAL_L02_RT0_P0_HYBRID_GLOBAL_OPERATOR_NO_SOLVE', ('output', 'sha256')),
    'pack': ('9b40449be75122903a0014e688c2ad228910fa11b2c704701e167a0f18ff78d7', 'PASS_NO_SOLVE_L02_FULL_FACE_HYBRID_OPERATOR_PACK', ('output', 'sha256')),
    'restricted': ('7599746f63ab032bd6b78ec83e1e14fd1051b99f5aa716fe76facb2075d4b4b3', 'PASS_REAL_L02_RESTRICTED_HYBRID_CELLS_CONDITIONAL_ZERO_EXTERIOR_FLUX', ('artifact', 'sha256')),
    'space': ('eb7357b118cec6a4579f958fc0cba189ce0a06a4656c05711c058848366906b5', 'PASS_CONDITIONAL_L02_RT0_SPARSE_CURRENT_SPACE_WITH_EXTERIOR_SUPPORTS', ('artifact_sha256',)),
    'mesh': ('2ff4183b727373d8f6d9dbd32a39fcfc55a92b19bc55328b7e79082e766bfe59', 'COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT', ('snapshot', 'sha256')),
}
N = 3178104
POSITIVE, NEGATIVE, GAUGE = 2699, 2656, 0


def reconstruct(voltage, terminal, output, budget):
    """Read each compressed array once; replay actual cells in bounded batches."""
    with np.load(PINS['restricted'][0], allow_pickle=False) as z:
        active, upper = z['active_local_facet_mask'], z['dc_trace_h_upper_s']
        rho, weight = z['unit_divergence_resistance_ohm'], z['unit_divergence_flux_weights']
        free, exterior = z['free_triangle_indices'], z['zero_flux_exterior_branch_indices']
        facets = z['local_facet_branch_index']
    with np.load(PINS['pack'][0], allow_pickle=False) as z:
        owner_count = z['free_cell_owner_count']
        owner_index, owner_g = z['free_cell_owner_external_active_index'], z['free_cell_gc_admittance_s']
    with np.load(PINS['mesh'][0], allow_pickle=False) as z:
        xy, triangles = z['node_xy_um'], z['triangles']
    with np.load(PINS['space'][0], allow_pickle=False) as z:
        signs = z['local_outward_flux_sign']
        assert np.array_equal(facets, z['local_facet_branch_index'])
        resistance = sparse.csr_matrix((z['r_data'], z['r_indices'], z['r_indptr']), shape=tuple(z['r_shape']))
    assert terminal.shape == active.shape == weight.shape == (1583840, 3)
    assert upper.shape == (1583840, 6) and len(free) == len(rho) == len(owner_count)
    assert np.all(terminal[active] >= 0) and np.all(terminal[~active] == -1)
    q = np.empty((len(free), 3), dtype=complex); u = np.empty(len(free), dtype=complex)
    divergence_target = np.empty(len(free), dtype=complex)
    action = np.zeros(N, complex); magnitude = np.zeros(N); degree = np.zeros(N, np.int64)
    rhs_r = np.zeros(resistance.shape[0], complex)
    cell_kcl_max = local_constitutive_max = 0.
    local_joule = 0.; gc_power = 0j
    ii, jj = np.triu_indices(3)
    for start in range(0, len(free), 50000):
        sl = slice(start, min(start+50000, len(free)))
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
        assert np.all(cell_q[~mask] == 0), 'inactive flux must follow the equation'
        cell_gc = g*(V-cell_u[:, None])
        divergence_target[sl] = cell_gc.sum(1)
        kcl = cell_q.sum(1)-divergence_target[sl]
        cell_kcl_max = max(cell_kcl_max, prior.max_abs(kcl))
        q[sl], u[sl] = cell_q, cell_u
        np.add.at(action, ports[mask], -cell_q[mask])
        np.add.at(action, owners[valid], cell_gc[valid])
        # Bound both block construction and reconstructed action before cancellation.
        abs_wl = np.sum(abs(w)*abs(lam), axis=1)
        abs_drive = np.sum(abs(g)*abs(V), axis=1)
        face_magnitude = np.einsum('nij,nj->ni', abs(h), abs(lam))
        face_magnitude += abs(w)*((abs_drive+abs(c)*abs_wl)/abs(den))[:, None]
        owner_magnitude = abs(g)*(abs(V)+((abs_wl+r*abs_drive)/abs(den))[:, None])
        np.add.at(magnitude, ports[mask], face_magnitude[mask])
        np.add.at(magnitude, owners[valid], owner_magnitude[valid])
        np.add.at(degree, ports[mask], 16)
        np.add.at(degree, owners[valid], 16)
        p = xy[triangles[free[sl]]].copy(); p -= p[:, :1].copy(); p *= 1e-6
        rcell, _, _ = local_r._batch_local_rt0(p, 59.59e6*20e-6)
        rq = np.einsum('nij,nj->ni', rcell, cell_q)
        local_constitutive_max = max(local_constitutive_max, prior.max_abs((rq-cell_u[:, None]+lam)[mask]))
        local_joule += float(np.sum(np.conj(cell_q)*rq).real)
        gc_power += complex(np.sum(np.conj(g)*abs(V-cell_u[:, None])**2))
        np.add.at(rhs_r, facets[sl][mask], (signs[sl]*(cell_u[:, None]-lam))[mask])
        budget.check('batched physical cell replay')
    # Select one occurrence, then measure the full other-side jump. Never average.
    branch = facets.ravel(); oriented = (signs*q).ravel()
    first = np.full(resistance.shape[0], len(branch), dtype=np.int64)
    np.minimum.at(first, branch, np.arange(len(branch)))
    assert np.all(first < len(branch))
    current = oriented[first]
    jump = prior.max_abs(oriented-current[branch])
    global_cell_kcl = prior.max_abs(np.sum(signs*current[facets], axis=1)-divergence_target)
    assert np.all(current[exterior] == 0)
    rj = resistance@current
    active_branch = np.ones(len(current), bool); active_branch[exterior] = False
    constitutive = prior.max_abs((rj-rhs_r)[active_branch])
    joule = complex(np.vdot(current, rj))
    artifact = output/'l02-reconstructed-field.npz'
    prior.atomic_npz(artifact, cell_potential_v=u, cell_outward_flux_a=q, l02_branch_current_a=current,
                     zero_flux_exterior_branch_indices=exterior)
    metrics = {'cell_kcl_max_abs_a': cell_kcl_max, 'shared_facet_jump_max_abs_a': jump,
               'global_current_cell_kcl_max_abs_a': global_cell_kcl,
               'local_rt0_constitutive_max_abs_v': local_constitutive_max,
               'global_rt0_constitutive_max_abs_v': constitutive,
               'inactive_exterior_current_max_abs_a': 0.,
               'local_rt0_joule_w': local_joule, 'global_rt0_joule_w': prior.pair(joule),
               'local_global_joule_difference_ohm': abs(joule-local_joule),
               'reconstructed_field': prior.receipt(artifact)}
    return action, magnitude, degree, {'l02_rt0_resistance': joule, 'l02_cell_gc': gc_power}, metrics


def validate_field(voltage, current_l25, output):
    """Write diagnostics before gating; numerical convergence remains caller-owned."""
    output = Path(output)
    assert output.is_dir() and not (output/'physical-diagnostic.json').exists()
    voltage = np.asarray(voltage, dtype=np.complex128)
    current_l25 = np.asarray(current_l25, dtype=np.complex128)
    assert voltage.shape == (N,) and current_l25.shape == (604031,)
    assert np.all(np.isfinite(voltage)) and np.all(np.isfinite(current_l25)) and voltage[GAUGE] == 0
    budget = recon._Budget.create(180., 8.)
    budget.check('caller must release factors before physical replay')
    inputs = {}
    for name, (path, digest) in PINS.items():
        assert prior.sha(path) == digest, name
        inputs[name] = {'path': str(path), 'sha256': digest}
    for name, (digest, status, keys) in RESULTS.items():
        path = PINS[name][0].parent/'result.json'
        assert prior.sha(path) == digest, name+' result receipt'
        document = json.loads(path.read_text(encoding='utf-8'))
        assert document['status'] == status, name+' accepted status'
        artifact_sha = document
        for key in keys:
            artifact_sha = artifact_sha[key]
        assert artifact_sha == PINS[name][1], name+' accepted artifact'
        inputs[name+'_result'] = {'path': str(path), 'sha256': digest, 'status': status}
    (output/'physical-validator-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(PINS['operator'][0], allow_pickle=False) as z:
        y, r25, b = (prior.read_csc(z, name) for name in ('y', 'r', 'b'))
        terminal = z['conditional_local_facet_global_active_index']
    source, magnitude, degree, power, cell_metrics = reconstruct(voltage, terminal, output, budget)
    del terminal
    gc.collect()
    with np.load(PINS['pack'][0], allow_pickle=False) as pack:
        for name, prefix in (('finite_branches', 'finite'), ('l02_contact_gc', 'contact_gc')):
            first, second, admittance = (pack[prefix+'_'+key] for key in ('first_active_index', 'second_active_index', 'admittance_s'))
            action = prior.branch_action(first, second, admittance, voltage)
            source += action
            pair_magnitude = abs(admittance)*(abs(voltage[first])+abs(voltage[second]))
            np.add.at(magnitude, first, pair_magnitude); np.add.at(magnitude, second, pair_magnitude)
            count = np.bincount(np.r_[first, second], minlength=N)
            degree += 4*count
            power[name] = complex(np.sum(np.conj(admittance)*abs(voltage[first]-voltage[second])**2))
            if name == 'finite_branches':
                matrix = prior.branch_laplacian(first, second, admittance, N)
                fm = abs(matrix)@abs(voltage)
                bound = prior.gamma(16*(count+prior.row_degree(matrix)+10))*(fm+2*pair_scale(first, second, pair_magnitude))
                finite_ratio = prior.max_abs((action-matrix@voltage)/np.maximum(bound, np.finfo(float).tiny))
                del matrix, fm, bound
        names = json.loads(pack['category_names_json_utf8'].tobytes())
        assert names == ['retained_gc', 'termination', 'l14_sheet_dc', 'l14_distributed_gc', 'l25_distributed_gc']
        for name in names:
            matrix = prior.read_csc(pack, 'category_'+name)
            assert matrix.shape == (1483296, 1483296)
            action = matrix@voltage[:1483296]
            source[:1483296] += action
            magnitude[:1483296] += abs(matrix)@abs(voltage[:1483296])
            degree[:1483296] += prior.row_degree(matrix)
            power[name] = complex(np.conj(np.vdot(voltage[:1483296], action)))
            del matrix, action
    yv = y@voltage
    magnitude += abs(y)@abs(voltage)
    degree += prior.row_degree(y)
    # Counts include original premerge cell/branch contributions and CSC matvec.
    source_bound = prior.gamma(16*(degree+32))*magnitude
    source_ratio = prior.max_abs((source-yv)/np.maximum(source_bound, np.finfo(float).tiny))
    rhs = np.zeros(N, complex); rhs[POSITIVE], rhs[NEGATIVE] = 1., -1.
    csc_kcl = yv+b@current_l25-rhs
    physical_kcl = source+b@current_l25-rhs
    constitutive25 = r25@current_l25-b.T@voltage
    power['l25_rt0_resistance'] = complex(np.vdot(current_l25, r25@current_l25))
    zdd = complex(voltage[POSITIVE]-voltage[NEGATIVE])
    power_error = abs(sum(power.values())-zdd)
    matrix_power_error = abs(np.conj(np.vdot(voltage, yv))+power['l25_rt0_resistance']-zdd)
    # Keep the prior gauge-eliminated backward-error denominator exactly.
    y_kept, b_kept = y[1:, 1:], b[1:, :]
    frobenius = np.sqrt(np.sum(abs(y_kept.data)**2)+2*np.sum(abs(b_kept.data)**2)+np.sum(abs(r25.data)**2))
    del y_kept, b_kept
    residual_norm = np.hypot(np.linalg.norm(csc_kcl[1:]), np.linalg.norm(constitutive25))
    solution_norm = np.hypot(np.linalg.norm(voltage[1:]), np.linalg.norm(current_l25))
    backward = float(residual_norm/(frobenius*solution_norm+np.sqrt(2.)))
    gates = {
        'csc_kcl': prior.max_abs(csc_kcl) < 1e-7,
        'physical_source_kcl': prior.max_abs(physical_kcl) < 1e-7,
        'l25_constitutive': prior.max_abs(constitutive25) < 1e-7,
        'l02_local_constitutive': cell_metrics['local_rt0_constitutive_max_abs_v'] < 1e-7,
        'l02_global_constitutive': cell_metrics['global_rt0_constitutive_max_abs_v'] < 1e-7,
        'cell_kcl': cell_metrics['cell_kcl_max_abs_a'] < 1e-7,
        'global_current_cell_kcl': cell_metrics['global_current_cell_kcl_max_abs_a'] < 1e-7,
        'shared_facet_jump_before_averaging': cell_metrics['shared_facet_jump_max_abs_a'] < 1e-7,
        'local_global_rt0_joule': cell_metrics['local_global_joule_difference_ohm'] <= 1e-7*max(abs(power['l02_rt0_resistance']), abs(cell_metrics['local_rt0_joule_w']), np.finfo(float).tiny),
        'local_rt0_joule_passivity': np.isfinite(cell_metrics['local_rt0_joule_w']) and cell_metrics['local_rt0_joule_w'] >= -1e-10,
        'backward': backward <= 1e-9,
        'finite_forward_bound': finite_ratio <= 1.,
        'source_forward_bound': source_ratio <= 1.,
        'passivity': zdd.real >= -1e-12 and all(np.isfinite(value) and value.real >= -1e-10 for value in power.values()),
        'physical_power': power_error <= max(abs(zdd), np.finfo(float).tiny)*1e-7,
        'matrix_power': matrix_power_error <= max(abs(zdd), np.finfo(float).tiny)*1e-7,
    }
    diagnostic = {
        'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
        'status': 'PASS_CONDITIONAL_HYBRID_PHYSICAL_FIELD' if all(gates.values()) else 'UNVALIDATED_CONDITIONAL_HYBRID_PHYSICAL_FIELD',
        'validator_sha256': prior.sha(Path(__file__)), 'inputs': inputs,
        'physical': {**cell_metrics, 'zdd_ohm': prior.pair(zdd),
                     'csc_kcl_max_abs_a': prior.max_abs(csc_kcl), 'physical_kcl_max_abs_a': prior.max_abs(physical_kcl),
                     'l25_constitutive_max_abs_v': prior.max_abs(constitutive25), 'normalized_backward_residual': backward,
                     'finite_forward_bound_ratio': finite_ratio, 'source_forward_bound_ratio': source_ratio,
                     'power_contributions_ohm': {name: prior.pair(value) for name, value in power.items()},
                     'power_closure_error_ohm': power_error, 'matrix_power_closure_error_ohm': matrix_power_error},
        'gates': {name: bool(value) for name, value in gates.items()}, 'budget': budget.receipt(),
        'scope': 'Conditional finite2D RT0/P0 R/GC field only. Original finite/contact/five category actions and reconstructed cell currents are compared to saved CSC. No magnetic, mesh, broadband or PowerSI acceptance; numerical info/residual is separately required.'}
    prior.atomic_json(output/'physical-diagnostic.json', diagnostic)
    assert all(gates.values()), {name: value for name, value in gates.items() if not value}
    return diagnostic


def pair_scale(first, second, values):
    result = np.zeros(N)
    np.add.at(result, first, values); np.add.at(result, second, values)
    return result


def check_warm_transfer(output):
    """Exercise all physical replay on the accepted P1 field, without a solve."""
    warm_path = R/'astra-l02-l14-l25-combined-block-lgmres-02/field.npz'
    transfer_path = R/'astra-l02-hybrid-p1-transfer-01/p1-hybrid-transfer.npz'
    assert prior.sha(warm_path) == 'c7360f92732f956c6c63c3e564cd97315b26a09a9a79414446bdcd4a6fce76f3'
    assert prior.sha(transfer_path) == '7db244bf196f7c2195a0ce79e7abed848ae0c2404809ea2c25dcedec23528a68'
    assert prior.sha(PINS['operator'][0]) == PINS['operator'][1]
    with np.load(warm_path, allow_pickle=False) as z:
        old_v, q25 = z['active_voltage_v'], z['l25_branch_current_a']
    with np.load(transfer_path, allow_pickle=False) as z:
        transfer = sparse.csr_matrix((z['transfer_data'], z['transfer_indices'], z['transfer_indptr']), shape=tuple(z['transfer_shape']))
        old_indices = z['p1_combined_global_active_indices']
    with np.load(PINS['operator'][0], allow_pickle=False) as z:
        rows, indices = z['conditional_to_full_face_transfer_row_index'], z['conditional_global_active_index']
    v = np.zeros(N, complex); v[:1483296] = old_v[:1483296]
    v[indices] = (transfer@old_v[old_indices])[rows]
    del transfer, old_v, rows, indices, old_indices
    output.mkdir(parents=True, exist_ok=False)
    try:
        validate_field(v, q25, output)
    except AssertionError:
        if not (output/'physical-diagnostic.json').exists():
            raise
    diagnostic = json.loads((output/'physical-diagnostic.json').read_text(encoding='utf-8'))
    required = ('l25_constitutive', 'l02_local_constitutive', 'cell_kcl', 'finite_forward_bound', 'source_forward_bound')
    assert all(diagnostic['gates'][name] for name in required), diagnostic['gates']
    assert not diagnostic['gates']['physical_source_kcl'], 'transferred P1 field must not be accepted as the new hybrid solution'
    assert not diagnostic['gates']['global_current_cell_kcl'], 'selected warm global current is not cell-conservative'
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_TRANSFERRED_P1_PHYSICAL_REPLAY_CHECK_ONLY',
              'physical_diagnostic': prior.receipt(output/'physical-diagnostic.json'),
              'warm_field': prior.receipt(warm_path), 'transfer': prior.receipt(transfer_path),
              'required_algebra_checks': list(required),
              'scope': 'No factorization or field solve. Transferred P1 field is intentionally unvalidated for the new hybrid equations.'}
    prior.atomic_json(output/'check-result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-warm-transfer', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check_warm_transfer(args.output.resolve())
