"""SPD Decap PI Evaluator v0.23.1: Fourier volume-current/charge research control.

Vacuum Green potentials own all magnetic fields; no added internal or gap L.
P1 normal-current hats in each homogeneous layer imply zero bulk divergence.
Four interface contrast charges are coupled explicitly, including outer faces.
This reduced-dimensional control is not a general 3D VIE implementation.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.linalg import solve, lu_factor, lu_solve
import qualify_astra_joint_tm_boundary as reference

EPS0, MU0 = reference.EPS0, reference.MU0
REFERENCE_SHA = "10032e17838f0f6f0105051424679c29de3ef83acc63cdc9d8075523368ed247"


def geometry(thickness, n):
    ends = np.r_[0, np.cumsum(thickness)]
    edges = np.concatenate([np.linspace(ends[m], ends[m+1], n+1) for m in range(3)])
    cell = np.concatenate([np.column_stack((np.arange(m*(n+1), m*(n+1)+n), np.arange(m*(n+1)+1, (m+1)*(n+1)))) for m in range(3)])
    b = np.zeros((4, len(edges)))
    b[0, 0], b[-1, -1] = 1, -1
    for m in (1, 2):
        b[m, m*(n+1)-1], b[m, m*(n+1)] = -1, 1
    c = np.zeros((len(edges), 3*n-1))
    for m in range(3):
        for local in range(n+1):
            index = m*n+local
            if 0 < index < 3*n:
                c[m*(n+1)+local, index-1] = 1
    d = b.T/np.sum(b*b, axis=1)
    assert np.array_equal(b @ c, np.zeros((4, 3*n-1)))
    assert np.array_equal(b @ d, np.eye(4))
    return ends, edges, cell, b, c, d


def operators(thickness, kappa, omega, kx, n, quadrature=8):
    ends, nodes, cell, b, c, d = geometry(thickness, n)
    h = nodes[cell[:, 1]]-nodes[cell[:, 0]]
    mid = nodes[cell].mean(axis=1)
    alpha = np.sqrt(complex(kx*kx-omega*omega*MU0*EPS0))
    assert alpha.real > 0  # Frozen evanescent controls, not a propagating-mode policy.
    x, w = leggauss(quadrature)
    u, w = (x+1)/2, w/2
    phi = np.column_stack((1-u, u))
    derivative = np.array([-1., 1.])[None, :]/(kx*h[:, None])
    plus, minus, dxplus, dxminus = [np.zeros((len(cell), len(nodes)), complex) for _ in range(4)]
    mass = np.zeros((len(nodes), len(nodes)), complex)
    magnetic = np.zeros_like(mass)
    scalar = np.zeros_like(mass)
    overlap = np.zeros_like(mass)
    surface_derivative = np.zeros((4, len(nodes)), complex)
    incident = np.zeros((len(nodes), 2), complex)
    for e, ids in enumerate(cell):
        exp_plus = np.exp(alpha*h[e]*(u-.5))
        exp_minus = np.exp(-alpha*h[e]*(u-.5))
        plus[e, ids] = h[e]*(w*exp_plus) @ phi
        minus[e, ids] = h[e]*(w*exp_minus) @ phi
        dxplus[e, ids] = h[e]*np.sum(w*exp_plus)*derivative[e]
        dxminus[e, ids] = h[e]*np.sum(w*exp_minus)*derivative[e]
        mass[np.ix_(ids, ids)] += h[e]*(np.outer(derivative[e], derivative[e])+np.array([[1/3, 1/6], [1/6, 1/3]]))/kappa[e//n]
        overlap[np.ix_(ids, ids)] += h[e]*np.array([[1/3, 1/6], [1/6, 1/3]])
        z = mid[e]+h[e]*(u-.5)
        incident[ids] += phi.T @ ((h[e]*w)[:, None]*np.column_stack((np.exp(-alpha*z), np.exp(-alpha*(ends[-1]-z)))))
        distance = ends[:, None]-z[None, :]
        surface_derivative[:, ids] += (-np.sign(distance)*np.exp(-alpha*np.abs(distance))/2*(h[e]*w)[None, :]) @ phi
        # Split the self integral at z=z': the Green cusp never crosses a quadrature cell.
        s, t = u[:, None], u[None, :]
        kernel_weight = h[e]**2*s*w[:, None]*w[None, :]*np.exp(-alpha*h[e]*s*(1-t))/(2*alpha)
        left = np.stack((np.broadcast_to(1-s, kernel_weight.shape), np.broadcast_to(s, kernel_weight.shape)), axis=-1)
        right = np.stack((1-s*t, s*t), axis=-1)
        local = np.einsum('ij,ija,ijb->ab', kernel_weight, left, right)
        scalar[np.ix_(ids, ids)] += local+local.T
        local += np.sum(kernel_weight)*np.outer(derivative[e], derivative[e])
        magnetic[np.ix_(ids, ids)] += local+local.T
    separation = mid[None, :]-mid[:, None]
    upper = np.triu(np.exp(-alpha*np.abs(separation))/(2*alpha), 1)
    scalar_off = plus.T @ upper @ minus
    scalar += scalar_off+scalar_off.T
    off = scalar_off + dxplus.T @ upper @ dxminus
    magnetic += off+off.T
    potential = np.exp(-alpha*np.abs(ends[:, None]-ends[None, :]))/(2*alpha)
    return ends, nodes, cell, b, c, d, mass, magnetic, potential, incident, alpha, scalar, overlap, surface_derivative


def current_charge(thickness, eps, sigma, omega, kx, n):
    assert omega > 0 and kx != 0, 'DC and kx=0 require a separately qualified basis'
    gamma = sigma+1j*omega*eps
    kappa = gamma-1j*omega*EPS0
    ends, nodes, cell, b, c, d, mass, magnetic, p, incident, alpha, scalar, overlap, surface_derivative = operators(thickness, kappa, omega, kx, n)
    # Conforming total normal flux: the two contrast-current traces share J/gamma*kappa.
    # This changes the trial/test subspace, preserving the Maxwell interface condition.
    c = np.zeros((len(nodes), 3*(n-1)), complex)
    for material in range(3):
        c[material*(n+1)+1:(material+1)*(n+1)-1, material*(n-1):(material+1)*(n-1)] = np.eye(n-1)
    d = np.zeros((len(nodes), 4), complex)
    d[0, 0], d[-1, -1] = 1, -1
    ratio = 1-1j*omega*EPS0/gamma
    differences = []
    for interface in (1, 2):
        difference = 1j*omega*EPS0*(1/gamma[interface-1]-1/gamma[interface])
        assert abs(difference) > 128*np.finfo(float).eps*max(abs(ratio[interface-1]), abs(ratio[interface])), 'merge or separately qualify equal-material interfaces'
        differences.append(abs(difference))
        d[interface*(n+1)-1, interface] = ratio[interface-1]/difference
        d[interface*(n+1), interface] = ratio[interface]/difference
        assert np.allclose(d[interface*(n+1)-1, interface]/ratio[interface-1], d[interface*(n+1), interface]/ratio[interface], rtol=1e-14, atol=0)
    assert np.allclose(b @ c, 0, atol=1e-15)
    assert np.allclose(b @ d, np.eye(4), rtol=1e-14, atol=1e-15)
    assert np.array_equal(c.T @ c, np.eye(3*(n-1)))  # Together with BC=0, BD=I proves full column rank.
    z = mass+1j*omega*MU0*magnetic
    # Exact integration by parts: the two O(Q) loop contributions otherwise
    # cancel to O(k0^2 Q/kx^2). Keep that small retarded term explicitly.
    k0_squared = omega*omega*MU0*EPS0
    common = overlap+k0_squared*scalar
    lcc = c.T @ common @ c/(kx*kx)
    lcd = (c.T @ common @ d-c.T @ surface_derivative.T)/(kx*kx)
    ldd = (d.T @ common @ d+p-surface_derivative @ d-d.T @ surface_derivative.T)/(kx*kx)
    cc = c.T @ mass @ c+1j*omega*MU0*lcc
    cd = c.T @ mass @ d+1j*omega*MU0*lcd
    dd = d.T @ mass @ d+1j*omega*MU0*ldd
    identity_magnetic = (common+b.T @ p @ b-b.T @ surface_derivative-surface_derivative.T @ b)/(kx*kx)
    magnetic_identity_difference = reference.relative(identity_magnetic, magnetic)
    matrix = np.block([[cc, -1j*cd], [omega*EPS0*cd.T, -1j*omega*EPS0*dd-p]])
    # Reciprocal Fourier testing uses (-i*v'/kx,v) against (+i*v'/kx,v).
    # An explicit reference charge cancels the large incident gradient analytically.
    # t=t0+tau is exact: tau retains all finite-conductivity/retardation corrections.
    t0 = np.zeros((4, 2), complex)
    t0[0, 0], t0[-1, 1] = -2*kx, 2*kx
    incident_remainder = k0_squared/(kx*alpha)*np.column_stack((-np.exp(-alpha*ends), np.exp(-alpha*(ends[-1]-ends))))
    rhs = np.vstack((omega*MU0/kx*(c.T @ incident)+1j*cd @ t0,
        incident_remainder+k0_squared/kx*(d.T @ incident)+1j*omega*EPS0*dd @ t0))
    # Enforce prescribed H before solving: avoid subtracting two solved incident
    # fields to recover a transfer response many orders below their drive fields.
    open_matrix = matrix.copy()
    incident_operator = rhs.copy()
    incident_base = np.diag([kappa[0]/(2*gamma[0]), -kappa[-1]/(2*gamma[-1])])
    incident_correction = np.zeros((2, 4), complex)
    incident_correction[0, 0], incident_correction[1, 3] = 1/(2*kx), -1/(2*kx)
    matrix[:, -4:] -= incident_operator @ incident_correction
    rhs = incident_operator @ incident_base
    row_scale = np.maximum(np.max(np.abs(matrix), axis=1), np.finfo(float).tiny)
    row_scaled = matrix/row_scale[:, None]
    column_scale = np.maximum(np.max(np.abs(row_scaled), axis=0), np.finfo(float).tiny)
    scaled = row_scaled/column_scale[None, :]
    lu = lu_factor(scaled)
    solution = lu_solve(lu, rhs/row_scale[:, None])/column_scale[:, None]
    refinement_history = []
    for refinement in range(6):
        residual = rhs-matrix @ solution
        denominator = np.abs(matrix) @ np.abs(solution)+np.abs(rhs)
        backward = float(np.max(np.abs(residual)/np.maximum(denominator, np.finfo(float).tiny)))
        refinement_history.append(backward)
        if backward < 1e-14 or refinement == 5:
            break
        solution += lu_solve(lu, residual/row_scale[:, None])/column_scale[:, None]
    incident_amplitudes = incident_base+incident_correction @ solution[-4:]
    full_t = solution[-4:].copy()
    full_t[0] = [-kx*kappa[0]/gamma[0], 0]
    full_t[-1] = [0, -kx*kappa[-1]/gamma[-1]]
    full_solution = solution.copy()
    full_solution[-4:] = full_t
    hprime = np.column_stack((-alpha*np.exp(-alpha*ends), alpha*np.exp(-alpha*(ends[-1]-ends))))
    original_rhs = np.vstack((omega*MU0/kx*(c.T @ incident), -hprime/kx+k0_squared/kx*(d.T @ incident))) @ incident_amplitudes
    original_denominator = np.abs(open_matrix) @ np.abs(full_solution)+np.abs(original_rhs)
    original_backward = float(np.max(np.abs(open_matrix @ full_solution-original_rhs)/np.maximum(original_denominator, np.finfo(float).tiny)))
    j = c @ solution[:-4]-1j*d @ full_t
    charge = full_t/omega
    normal_to_h = gamma/(1j*kx*kappa)
    h = np.repeat(normal_to_h, n+1)[:, None]*j
    k = np.vstack((h[0], -h[-1]))
    assert np.allclose(k, np.eye(2), rtol=1e-14, atol=1e-16)
    current_norm = np.abs(b) @ np.abs(j)+omega*np.abs(charge)
    continuity = float(np.max(np.linalg.norm(b @ j+1j*omega*charge, axis=1)/np.maximum(np.linalg.norm(current_norm, axis=1), np.finfo(float).tiny)))
    # Second-order one-sided derivative; both this recovered port field and volume E are checked.
    lower = -(-3*h[0]+4*h[1]-h[2])/(2*(thickness[0]/n)*gamma[0])
    upper = -(3*h[-1]-4*h[-2]+h[-3])/(2*(thickness[-1]/n)*gamma[-1])
    return dict(j=j, charge=charge, h=h, z=np.vstack((lower, upper)), kappa=kappa,
        primary_charge_scaled=t0 @ incident_amplitudes, correction_charge_scaled=solution[-4:], incident_amplitudes=incident_amplitudes,
        backward=backward, original_backward=original_backward, refinement_history=refinement_history,
        continuity=continuity, condition=float(np.linalg.cond(scaled)),
        drive_condition=float(np.linalg.cond(k)), operator_symmetry=reference.relative(z, z.T),
        magnetic_identity_relative_difference=magnetic_identity_difference,
        material_ratio_jump_magnitudes=differences,
        nodes=nodes, cell=cell)


def compare(thickness, gamma, omega, kx, q, exact_h, candidate, n):
    u, w = leggauss(8)
    u, w = (u+1)/2, w/2
    errors, h_errors = [], []
    # Dielectric loss and conductor sigma are supplied as Re(gamma); Im(gamma)=omega*Re(epsilon).
    drives = np.array([[1, 0, 1], [0, 1, 1j]])
    power = np.zeros((3, 3), complex)
    for m, length in enumerate(thickness):
        err = norm = herr = hnorm = 0.
        for e in range(n):
            hl, hr = candidate['h'][m*(n+1)+e:m*(n+1)+e+2]
            jl, jr = candidate['j'][m*(n+1)+e:m*(n+1)+e+2]
            exact, ex = reference.analytic_field(q, exact_h, gamma, thickness, m, (e+u)*length/n)
            ez = 1j*kx*exact/gamma[m]
            ch = (1-u[:, None])*hl+u[:, None]*hr
            cz = ((1-u[:, None])*jl+u[:, None]*jr)/candidate['kappa'][m]
            cx = 1j*(jr-jl)/(kx*(length/n)*candidate['kappa'][m])
            weight = (w*length/n)[:, None]
            err += np.sum(weight*(np.abs(cx-ex)**2+np.abs(cz-ez)**2))
            norm += np.sum(weight*(np.abs(ex)**2+np.abs(ez)**2))
            herr += np.sum(weight*np.abs(ch-exact)**2)
            hnorm += np.sum(weight*np.abs(exact)**2)
            electric = np.abs(cx @ drives)**2+np.abs(cz @ drives)**2
            power[0] += gamma[m].real*np.sum(weight*electric, axis=0)
            power[1] += 1j*omega*MU0*np.sum(weight*np.abs(ch @ drives)**2, axis=0)
            power[2] -= 1j*gamma[m].imag*np.sum(weight*electric, axis=0)
        errors.append(float(np.sqrt(err/norm)))
        h_errors.append(float(np.sqrt(herr/hnorm)))
    _, _, _, b, _, _ = geometry(thickness, n)
    exact_j = np.concatenate([candidate['kappa'][m]/gamma[m]*1j*kx*np.linspace(exact_h[m], exact_h[m+1], n+1) for m in range(3)])
    exact_charge = -(b @ exact_j)/(1j*omega)
    normal_jump = max(reference.relative(candidate['h'][m*(n+1)], candidate['h'][m*(n+1)-1]) for m in (1, 2))
    left, right, reference_fields = [], [], []
    for m in (1, 2):
        i = m*(n+1)
        left.append(1j*(candidate['j'][i-1]-candidate['j'][i-2])/(kx*(thickness[m-1]/n)*candidate['kappa'][m-1]))
        right.append(1j*(candidate['j'][i+1]-candidate['j'][i])/(kx*(thickness[m]/n)*candidate['kappa'][m]))
        reference_fields.append(reference.analytic_field(q, exact_h, gamma, thickness, m, np.array([0.]))[1][0])
    left, right = np.array(left), np.array(right)
    tangent_scale = max(np.linalg.norm(reference_fields), .5*(np.linalg.norm(left)+np.linalg.norm(right)))
    pin = np.diag(drives.conj().T @ candidate['z'] @ drives)
    power_closure = float(np.max(np.abs(pin-power.sum(axis=0))/(np.abs(pin)+np.sum(np.abs(power), axis=0))))
    return dict(electric_field_relative_l2_by_material=errors, magnetic_field_relative_l2_by_material=h_errors,
        charge_relative_error=reference.relative(candidate['charge'], exact_charge),
        inner_charge_relative_error=reference.relative(candidate['charge'][1:3], exact_charge[1:3]),
        normal_total_current_jump=normal_jump, one_sided_tangential_e_relative_jump=float(np.linalg.norm(left-right)/tangent_scale),
        recovered_port_poynting_relative_closure=power_closure,
        complex_combination_poynting_relative_closure=float(abs(pin[-1]-power[:, -1].sum())/(abs(pin[-1])+np.abs(power[:, -1]).sum())),
        reciprocity_relative_frobenius=reference.relative(candidate['z'], candidate['z'].T),
        off_diagonal_reciprocity=float(abs(candidate['z'][0, 1]-candidate['z'][1, 0])/(abs(candidate['z'][0, 1])+abs(candidate['z'][1, 0]))),
        minimum_port_resistance_eigenvalue=float(np.linalg.eigvalsh((candidate['z']+candidate['z'].conj().T)/2).min()))


def gates(end):
    return dict(impedance=end['impedance_entry_relative_error'] < 1e-3,
        material_fields=max(end['electric_field_relative_l2_by_material']) < .02,
        interface_charge=end['inner_charge_relative_error'] < 1e-3,
        normal_current=end['normal_total_current_jump'] < 1e-3,
        tangential_e=end['one_sided_tangential_e_relative_jump'] < .05,
        recovered_port_poynting=end['recovered_port_poynting_relative_closure'] < 1e-3,
        reciprocity=end['off_diagonal_reciprocity'] < 1e-3,
        reciprocity_frobenius=end['reciprocity_relative_frobenius'] < 1e-10,
        passivity=end['minimum_port_resistance_eigenvalue'] >= 0,
        equations=max(end['backward'], end['original_backward']) < 1e-12,
        continuity=end['continuity'] < 1e-12, symmetry=end['operator_symmetry'] < 1e-12,
        magnetic_identity=end['magnetic_identity_relative_difference'] < 1e-11)


def run(output, diagnostic):
    started = monotonic()
    assert reference.sha(Path(reference.__file__)) == REFERENCE_SHA
    assert not output.exists(), 'output must be new'
    output.mkdir(parents=True)
    rows, props = reference.source_inputs()
    frequencies = (1e3, 1e8) if diagnostic else reference.FREQUENCIES
    meshes = (8, 16, 32) if diagnostic else (16, 32, 64, 128, 256)
    cases, arrays = [], {}
    for period in reference.PERIODS:
        kx = 2*np.pi/period
        for frequency in frequencies:
            assert monotonic()-started < 180, 'bounded control exceeded180s'
            omega = 2*np.pi*frequency
            thickness, eps, sigma, gamma = reference.materials(rows, props, frequency)
            q, exact_h, exact_z, _, _ = reference.analytic(thickness, gamma, omega, kx)
            history = []
            for n in meshes:
                candidate = current_charge(thickness, eps, sigma, omega, kx, n)
                metrics = compare(thickness, gamma, omega, kx, q, exact_h, candidate, n)
                metrics.update(cells_per_layer=n, impedance_entry_relative_error=float(np.max(np.abs(candidate['z']-exact_z)/np.abs(exact_z))))
                metrics.update({key:candidate[key] for key in ('backward', 'original_backward', 'refinement_history', 'continuity', 'condition', 'drive_condition', 'operator_symmetry', 'magnetic_identity_relative_difference', 'material_ratio_jump_magnitudes')})
                history.append(metrics)
                if n >= 128:
                    failed = {name for name, ok in gates(metrics).items() if not ok}
                    discretization = {'impedance', 'material_fields', 'interface_charge', 'normal_current', 'tangential_e', 'recovered_port_poynting'}
                    if not failed or not failed <= discretization:
                        break
            prefix = f'case_{len(cases):02d}_'
            arrays.update({prefix+key:candidate[key] for key in ('j', 'charge', 'h', 'z', 'primary_charge_scaled', 'correction_charge_scaled', 'incident_amplitudes')})
            cases.append(dict(frequency_hz=frequency, period_m=period, refinement=history))
    failures = []
    for i, case in enumerate(cases):
        end = case['refinement'][-1]
        checks = gates(end)
        checks['impedance_refinement'] = end['impedance_entry_relative_error'] < .1*case['refinement'][0]['impedance_entry_relative_error']
        checks['tangential_refinement'] = end['one_sided_tangential_e_relative_jump'] < .2*case['refinement'][0]['one_sided_tangential_e_relative_jump']
        failures.extend(f'case{i}:{name}' for name, ok in checks.items() if not ok)
    with (output/'fields.npz').open('xb') as stream:
        np.savez_compressed(stream, **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='DIAGNOSTIC_ONLY' if diagnostic else ('PASS_FOURIER_CURRENT_CHARGE_CONTROL' if not failures else 'STOP_FOURIER_CURRENT_CHARGE_CONTROL'),
        failures=failures, elapsed_s=monotonic()-started, cases=cases, script_sha256=reference.sha(Path(__file__)),
        reference_script_sha256=REFERENCE_SHA, source_pins=reference.PINS, fields_sha256=reference.sha(output/'fields.npz'),
        scope='Explicit current/contrast-charge vacuum-Green Fourier Galerkin control with P1 normal-current hats and a congruent trial/test restriction enforcing common total normal flux across each material interface. This changes the nonconforming discrete subspace while preserving the continuum Maxwell interface condition. Current-charge continuity and normal-total-current continuity are constructed identities; tangent E, per-material fields, inner charges, port response and volume Poynting comparisons remain independent tests. An exact loop/range transform and retarded alpha are retained. The particular charge t0=[-2kx*aL,0,0,2kx*aR] cancels the incident potential analytically; its full correction tau remains unknown, so no perfect-conductor or quasistatic approximation is made. Prescribed outer H is enforced by exact algebraic condensation of the two incident amplitudes before the solve. Outer tau coordinates describe incident amplitudes; physical outer charges are formed directly from K. No 3D singular quadrature, finite board edge, aperture, source port, via/pad junction, full-board Z or PowerSI accuracy qualification. The source material profile and endpoint-clamp assumptions are inherited from the pinned joint-boundary reference.')
    with (output/'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({key:result[key] for key in ('status', 'failures', 'elapsed_s')}))
    return 0 if diagnostic or not failures else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostic', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run(args.output.resolve(), args.diagnostic))
