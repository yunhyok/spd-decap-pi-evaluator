"""Small dense support integration of the existing smooth layered remainder.

Reuse the same spectral and radial interpolation definitions without building
a board-sized FFT grid. Each P0 quadrature integrates to one coulomb.
"""
from time import perf_counter

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.special import j0, roots_legendre

from astra_layered_charge_action import deep_spectral
from astra_stratified_charge_green import EPS0, source_background
from measure_astra_l02_adaptive_nonself import tensor_rule
from qualify_astra_tetra_charge_green import quadrature


def return_quadratures(parts, domains, long_order):
    result = []
    for pieces, domain in zip(parts, domains):
        if domain is None:
            triangle = pieces[0]
            base = triangle[:, :2]
            bounds = np.array([triangle[0, 2], triangle[0, 2]])
        else:
            assert len(domain) == 1
            base, bounds = domain[0]
        cap = domain is None
        if len(base) == 3:
            a, b = np.unravel_index(np.argmax(np.linalg.norm(base[:, None]-base[None], axis=2)), (3, 3))
            c = int(np.setdiff1d(np.arange(3), [a, b])[0])
            base = base[[a, b, c]]
            unit, weight = tensor_rule((long_order, 4) if cap else (long_order, 4, 4))
            u, v = unit[:, :2].T
            xy = (1-v[:, None])*((1-u[:, None])*base[0]+u[:, None]*base[1])+v[:, None]*base[2]
            weight = weight*2*(1-v)
        else:
            unit, weight = tensor_rule((long_order, 4))
            xy = (1-unit[:, :1])*base[0]+unit[:, :1]*base[1]
        height = np.full(len(weight), bounds[0]) if cap else bounds[0]+unit[:, -1]*(bounds[1]-bounds[0])
        assert abs(weight.sum()-1) < 2e-13
        result.append((np.column_stack((xy, height)), weight))
    return result


def dense_remainder(support_rules, radial_count=1024):
    started = perf_counter()
    interfaces, eps, background = source_background()
    both = np.vstack([p for p, _ in support_rules])
    assert np.isfinite(both).all() and np.min(both[:,2]) >= -1e-16 and np.max(both[:,2]) <= 75e-6+1e-16
    for points, weights in support_rules:
        assert points.shape == (len(weights), 3) and np.all(weights > 0)
        assert abs(weights.sum()-1) < 2e-13
    top = interfaces[0]
    pieces = [np.linspace(0., top, 4), np.linspace(top, 75e-6, 4)]
    z_nodes = np.unique(np.concatenate(pieces))
    radius_max = float(np.linalg.norm(np.ptp(both[:, :2], axis=0)))*(1+1e-14)
    assert radius_max > 0
    radius = radius_max*np.linspace(0., 1., radial_count)**2
    kmax = 16/(interfaces[1]-75e-6)
    order = int(max(96, np.ceil(kmax*radius_max/2)+48))
    x, w = roots_legendre(order)
    wave, weight = (x+1)*kmax/2, w*kmax/2
    amplitude, modes = deep_spectral(wave, z_nodes, interfaces, eps)
    bessel = j0(radius[:, None]*wave)
    tables = {}
    for a in range(len(z_nodes)):
        for b in range(a, len(z_nodes)):
            values = bessel@(weight*amplitude*modes[a]*modes[b])/(2*np.pi*EPS0)
            tables[a, b] = CubicSpline(radius, values)
    weighted = []
    active = []
    for points, weights in support_rules:
        wz = np.zeros((len(points), len(z_nodes)))
        for part, nodes in enumerate(pieces):
            selected = points[:,2] <= top if part == 0 else points[:,2] > top
            ids = np.searchsorted(z_nodes, nodes)
            for j, node in enumerate(nodes):
                other = np.delete(nodes, j)
                wz[selected, ids[j]] = np.prod((points[selected,2,None]-other)/(node-other), axis=1)
        assert np.max(abs(wz.sum(axis=1)-1)) < 1e-12
        weighted.append(weights[:,None]*wz)
        active.append(np.flatnonzero(np.any(wz != 0, axis=0)))
    matrix = np.empty((len(support_rules), len(support_rules)), complex)
    last = perf_counter()
    # ponytail: dense pair integration is intentionally confined to the 172-charge fixture.
    for a, (observer, _) in enumerate(support_rules):
        for b in range(a, len(support_rules)):
            source = support_rules[b][0]
            distance = np.linalg.norm(observer[:,None,:2]-source[None,:,:2], axis=2)
            value = 0j
            for za in active[a]:
                for zb in active[b]:
                    kernel = tables[min(za,zb),max(za,zb)](distance)
                    value += weighted[a][:,za]@kernel@weighted[b][:,zb]
            matrix[a,b] = matrix[b,a] = value
        if perf_counter()-last > 25:
            print(f'Smooth layered charge rows {a+1}/{len(support_rules)}',flush=True)
            last = perf_counter()
    assert np.isfinite(matrix).all()
    return matrix, dict(elapsed_s=perf_counter()-started, radial_count=radial_count,
                        spectral_order=order, radial_max_m=radius_max, source_background=background,
                        point_count=len(both), quadrature_scope='Smooth deep remainder only; ordinary reciprocal Galerkin integration with the existing cubic-z/radial tables, no conjugation.')


def self_check():
    from astra_stratified_charge_green import point_green, halfspace_green

    points = np.array([[0.,0.,10e-6],[.0004,.0002,65e-6]])
    value, _ = dense_remainder([(p[None],np.ones(1)) for p in points])
    interfaces, eps, _ = source_background()
    rho = np.linalg.norm(points[0,:2]-points[1,:2])
    reference = point_green(rho,points[0,2],points[1,2],interfaces,eps)
    assert reference['converged']
    exact = reference['value_v_per_c']-halfspace_green(rho,points[0,2]-interfaces[0],points[1,2]-interfaces[0],eps[0],eps[1])
    assert abs(value[0,1]-exact)/abs(exact) < 2e-5
    print('PASS: same layered point Green remainder',flush=True)


if __name__ == '__main__':
    self_check()
