"""SPD Decap PI Evaluator v0.23.1: selected actual finite-support cross pairs."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.spatial import cKDTree
from assemble_astra_l02_finite_charge_self import prism_union
from qualify_astra_tetra_volume_green import tetra_inner, tetra_quadrature
from check_astra_l14_l25_point_rows import SOURCE, PIN


def integrated(tri0, z0, k0, tri1, z1, k1, alpha, order, reverse=False):
    origin = np.r_[tri0.mean(axis=0), z0.mean()]
    p0, _ = prism_union([tri0], z0)
    p1, _ = prism_union([tri1], z1)
    p0 -= origin; p1 -= origin
    c1 = tri1.mean(axis=0)-origin[:2]
    j0 = k0/np.diff(z0)[0]
    h1 = np.diff(z1)[0]
    ordinary = hermitian = 0j
    for obs in (p1 if reverse else p0):
        points, weights = tetra_quadrature(obs, order)
        scalar = np.zeros(len(points))
        moment = np.zeros_like(points)
        for src in (p0 if reverse else p1):
            s, m = tetra_inner(src, points)
            scalar += s; moment += m
        j1 = (k1+alpha*(points[:, :2]-c1))/h1
        if reverse:
            inner = scalar[:, None]*j0
            ordinary += np.einsum('p,pd,pd->', weights, j1, inner)
            hermitian += np.einsum('p,pd,pd->', weights, j1.conj(), inner)
        else:
            inner = scalar[:, None]*j1+alpha*moment[:, :2]/h1
            ordinary += np.einsum('p,d,pd->', weights, j0, inner)
            hermitian += np.einsum('p,d,pd->', weights, j0.conj(), inner)
    return 1e-7*np.array([ordinary, hermitian])


def pair(x):
    return [float(x.real), float(x.imag)]


def main(out):
    started = perf_counter()
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == PIN
    with np.load(SOURCE, allow_pickle=False) as z:
        t0 = z['l14_triangle_vertices_um']*1e-6
        t1 = z['l25_triangle_vertices_um']*1e-6
        z0 = z['l14_slab_z_um']*1e-6; z1 = z['l25_slab_z_um']*1e-6
        k0 = z['l14_sheet_current_density_a_per_m']
        k1 = z['l25_average_current_a_per_m']
        alpha = z['l25_affine_coefficient_a_per_m2']
    def area(t):
        e = t[:, 1:]-t[:, :1]
        return abs(e[:, 0, 0]*e[:, 1, 1]-e[:, 0, 1]*e[:, 1, 0])/2
    a0, a1 = area(t0), area(t1)
    c0, c1 = t0.mean(axis=1), t1.mean(axis=1)
    q0, q1 = a0[:, None]*k0, a1[:, None]*k1
    strong0 = np.argsort(np.linalg.norm(q0, axis=1))[-64:]
    strong1 = np.argsort(np.linalg.norm(q1, axis=1))[-64:]
    distance = np.sqrt(np.sum((c0[strong0, None]-c1[strong1])**2, axis=2)+(z0.mean()-z1.mean())**2)
    score = np.linalg.norm(q0[strong0], axis=1)[:, None]*np.linalg.norm(q1[strong1], axis=1)/distance
    ids = np.unravel_index(np.argsort(score.ravel())[-4:], score.shape)
    selected = set(zip(strong0[ids[0]].tolist(), strong1[ids[1]].tolist()))
    nearest = cKDTree(c1).query(c0[strong0[-2:]])[1]
    selected.update(zip(strong0[-2:].tolist(), nearest.tolist()))
    cases = []
    for i, j in sorted(selected):
        low = integrated(t0[i], z0, k0[i], t1[j], z1, k1[j], alpha[j], 8)
        high = integrated(t0[i], z0, k0[i], t1[j], z1, k1[j], alpha[j], 16)
        back = integrated(t0[i], z0, k0[i], t1[j], z1, k1[j], alpha[j], 16, True)
        r = np.sqrt(np.sum((c0[i]-c1[j])**2)+(z0.mean()-z1.mean())**2)
        centroid = 1e-7*np.array([q0[i]@q1[j], q0[i].conj()@q1[j]])/r
        scale = max(np.linalg.norm(high), np.finfo(float).tiny)
        refinement = float(np.linalg.norm(high-low)/scale)
        reciprocity = float(np.linalg.norm(high-np.array([back[0], back[1].conjugate()]))/scale)
        cases.append(dict(l14_row=i, l25_row=j, ordinary_h=pair(high[0]), hermitian_h=pair(high[1]),
                          centroid_ordinary_h=pair(centroid[0]), centroid_hermitian_h=pair(centroid[1]),
                          refinement_relative=refinement, reciprocal_relative=reciprocity,
                          both_direction_ordinary_delta_ohm=pair(2j*2*np.pi*1e6*(high[0]-centroid[0])),
                          both_direction_hermitian_delta_ohm=float(2*2*np.pi*1e6*(high[1]-centroid[1]).real)))
    out.mkdir(parents=True, exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    passed = all(max(c['refinement_relative'],c['reciprocal_relative']) < 1e-4 for c in cases)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_SELECTED_FINITE_SUPPORT_CROSS_INDICATOR' if passed else 'UNRESOLVED_SELECTED_CROSS_QUADRATURE',
                  descriptor_sha256=PIN, cases=cases, elapsed_s=perf_counter()-started,
                  scope='Selected strong-moment and nearest actual pairs only; 3D constant L14 and affine L25 volume currents. No global error bound or correction insertion. Ordinary delta includes both reciprocal directions; Hermitian delta uses twice real part.')
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
