"""SPD Decap PI Evaluator v0.23.1: bounded precision diagnosis, not a board solver.

Reassemble the same Fourier current/charge equations with mpmath, including
quadrature and incident terms. Solving an already rounded matrix cannot test
whether its assembly lost the weak conductor/dielectric interface response.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import mpmath as mp
import numpy as np
import qualify_astra_fourier_current_charge as control


def array(a):
    return np.array(a.tolist(), dtype=complex)


def mp_current_charge(thickness, eps, sigma, omega, kx, n, digits):
    mp.mp.dps = digits
    length = [mp.mpf(float(x)) for x in thickness]
    omega, kx, eps0, mu0 = map(mp.mpf, (float(omega), float(kx), control.EPS0, control.MU0))
    gamma = [mp.mpf(float(s))+1j*omega*mp.mpc(complex(e)) for s, e in zip(sigma, eps)]
    kappa = [g-1j*omega*eps0 for g in gamma]
    ends = [mp.mpf(0)]
    for x in length:
        ends.append(ends[-1]+x)
    nodes = [ends[m]+length[m]*i/n for m in range(3) for i in range(n+1)]
    cell = [(m*(n+1)+e, m*(n+1)+e+1) for m in range(3) for e in range(n)]
    _, _, _, nb, nc, nd = control.geometry(thickness, n)
    b, c, d = [mp.matrix(a.tolist()) for a in (nb, nc, nd)]
    count, ncells = len(nodes), len(cell)
    alpha = mp.sqrt(kx*kx-omega*omega*mu0*eps0)
    gx, gw = mp.gauss_quadrature(8, 'legendre')
    u, w = [(x+1)/2 for x in gx], [x/2 for x in gw]
    plus, minus, dxplus, dxminus = [mp.zeros(ncells, count) for _ in range(4)]
    mass, magnetic, incident = mp.zeros(count), mp.zeros(count), mp.zeros(count, 2)
    mids = []
    for e, ids in enumerate(cell):
        h = nodes[ids[1]]-nodes[ids[0]]
        mid = (nodes[ids[1]]+nodes[ids[0]])/2
        mids.append(mid)
        derivative = [-1/(kx*h), 1/(kx*h)]
        for a in range(2):
            shape = [1-x if a == 0 else x for x in u]
            plus[e, ids[a]] = h*mp.fsum(wi*mp.exp(alpha*h*(ui-mp.mpf('.5')))*v for ui, wi, v in zip(u, w, shape))
            minus[e, ids[a]] = h*mp.fsum(wi*mp.exp(-alpha*h*(ui-mp.mpf('.5')))*v for ui, wi, v in zip(u, w, shape))
            dxplus[e, ids[a]] = h*derivative[a]*mp.fsum(wi*mp.exp(alpha*h*(ui-mp.mpf('.5'))) for ui, wi in zip(u, w))
            dxminus[e, ids[a]] = h*derivative[a]*mp.fsum(wi*mp.exp(-alpha*h*(ui-mp.mpf('.5'))) for ui, wi in zip(u, w))
            for side in (0, 1):
                incident[ids[a], side] += h*mp.fsum(wi*v*mp.exp(-alpha*(nodes[ids[0]]+ui*h if side == 0 else ends[-1]-nodes[ids[0]]-ui*h)) for ui, wi, v in zip(u, w, shape))
            for aa in range(2):
                mass[ids[a], ids[aa]] += h*(derivative[a]*derivative[aa]+mp.mpf(1)/(3 if a == aa else 6))/kappa[e//n]
        local = mp.zeros(2)
        for s, ws in zip(u, w):
            for t, wt in zip(u, w):
                weight = h*h*s*ws*wt*mp.exp(-alpha*h*s*(1-t))/(2*alpha)
                left, right = [1-s, s], [1-s*t, s*t]
                for a in range(2):
                    for aa in range(2):
                        local[a, aa] += weight*(left[a]*right[aa]+derivative[a]*derivative[aa])
        for a in range(2):
            for aa in range(2):
                magnetic[ids[a], ids[aa]] += local[a, aa]+local[aa, a]
    for e in range(ncells):
        for ee in range(e+1, ncells):
            weight = mp.exp(-alpha*(mids[ee]-mids[e]))/(2*alpha)
            for i in cell[e]:
                for j in cell[ee]:
                    value = weight*(plus[e, i]*minus[ee, j]+dxplus[e, i]*dxminus[ee, j])
                    magnetic[i, j] += value
                    magnetic[j, i] += value
    z = mass+1j*omega*mu0*magnetic
    cc, cd, dd = c.T*z*c, c.T*z*d, d.T*z*d
    p = mp.matrix([[mp.exp(-alpha*abs(x-y))/(2*alpha) for y in ends] for x in ends])
    size = cc.rows
    matrix = mp.zeros(size+4)
    matrix[:size, :size], matrix[:size, size:] = cc, -1j*cd
    matrix[size:, :size], matrix[size:, size:] = omega*eps0*cd.T, -1j*omega*eps0*dd-p
    hprime = mp.matrix([[-alpha*mp.exp(-alpha*x), alpha*mp.exp(-alpha*(ends[-1]-x))] for x in ends])
    rhs = mp.zeros(size+4, 2)
    rhs[:size, :] = omega*mu0/kx*c.T*incident
    rhs[size:, :] = -hprime/kx+omega*omega*mu0*eps0/kx*d.T*incident
    solution = mp.zeros(size+4, 2)
    for side in (0, 1):
        solution[:, side] = mp.lu_solve(matrix, rhs[:, side])
    residual = matrix*solution-rhs
    backward = max(abs(residual[i, side])/(mp.fsum(abs(matrix[i, j]*solution[j, side]) for j in range(size+4))+abs(rhs[i, side])) for i in range(size+4) for side in (0, 1))
    j = c*solution[:size, :]-1j*d*solution[size:, :]
    charge = solution[size:, :]/omega
    h = mp.matrix([[gamma[i//(n+1)]/(1j*kx*kappa[i//(n+1)])*j[i, side] for side in (0, 1)] for i in range(count)])
    drive = mp.matrix([[h[0, 0], h[0, 1]], [-h[count-1, 0], -h[count-1, 1]]])
    transform = drive**-1
    j, charge, h = j*transform, charge*transform, h*transform
    continuity = max(mp.norm((b*j+1j*omega*charge)[i, :])/(mp.norm((b*j)[i, :])+omega*mp.norm(charge[i, :])) for i in range(4))
    ports = mp.zeros(2)
    ports[0, :] = -(-3*h[0, :]+4*h[1, :]-h[2, :])/(2*length[0]/n*gamma[0])
    ports[1, :] = -(3*h[count-1, :]-4*h[count-2, :]+h[count-3, :])/(2*length[-1]/n*gamma[-1])
    return dict(j=array(j), charge=array(charge), h=array(h), z=array(ports), kappa=np.array([complex(x) for x in kappa]),
        backward=float(backward), continuity=float(continuity))


def run(output):
    started = monotonic()
    assert not output.exists()
    output.mkdir(parents=True)
    rows, props = control.reference.source_inputs()
    cases, arrays = [], {}
    for period in control.reference.PERIODS:
        for f in (1e3, 1e8):
            kx, omega = 2*np.pi/period, 2*np.pi*f
            thickness, eps, sigma, gamma = control.reference.materials(rows, props, f)
            q, exact_h, exact_z, _, _ = control.reference.analytic(thickness, gamma, omega, kx)
            history = []
            for n in (8, 16):
                assert monotonic()-started < 180, 'precision diagnosis limit180s'
                candidate = mp_current_charge(thickness, eps, sigma, omega, kx, n, 50)
                metrics = control.compare(thickness, gamma, omega, kx, q, exact_h, candidate, n)
                metrics.update(cells_per_layer=n, precision_decimal_digits=50, impedance_entry_relative_error=float(np.max(np.abs(candidate['z']-exact_z)/np.abs(exact_z))), backward=candidate['backward'], continuity=candidate['continuity'])
                history.append(metrics)
            prefix = f'case_{len(cases):02d}_'
            arrays.update({prefix+key:candidate[key] for key in ('j', 'charge', 'h', 'z')})
            cases.append(dict(frequency_hz=f, period_m=period, refinement=history))
    with (output/'fields.npz').open('xb') as stream:
        np.savez_compressed(stream, **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='PRECISION_DIAGNOSTIC_ONLY',
        mpmath_version=mp.__version__, elapsed_s=monotonic()-started, cases=cases,
        script_sha256=control.reference.sha(Path(__file__)), control_sha256=control.reference.sha(Path(control.__file__)),
        reference_sha256=control.REFERENCE_SHA, fields_sha256=control.reference.sha(output/'fields.npz'),
        scope='Same Galerkin equations assembled and solved at50 decimal digits; n8/16 endpoint frequencies only. Diagnoses assembly/solve cancellation; does not accept the float64 implementation or prescribe arbitrary precision for the general board model.')
    with (output/'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(dict(status=result['status'], elapsed_s=result['elapsed_s'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
