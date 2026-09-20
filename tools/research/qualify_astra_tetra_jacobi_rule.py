"""SPD Decap PI Evaluator v0.23.1: positive collapsed Jacobi tetrahedron rule."""
from hashlib import sha256
from math import factorial
from itertools import permutations
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.special import roots_jacobi
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry

ROOT = geometry.ROOT
PINS = {
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-leading-lift-touching-correction-02/pair-correction.npz': '7c89bcafe7ab81915289ec9eb548c57c6e18429ea15ac6c3317f4f0d2666d27d',
    'outputs/research/astra-leading-lift-nontouch-correction-01/pair-correction.npz': '98fa35e653acc04d618775c953168604c00836427a3e6f2689d22018107208c9',
}
SOURCES = [
    'https://raw.githubusercontent.com/FEniCS/basix/main/cpp/basix/quadrature.cpp',
    'https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.roots_jacobi.html',
]


def rule(order, kind='jacobi'):
    assert isinstance(order, int) and order >= 1
    if kind == 'xiao_gimbutas':
        assert order == 5
        # Degree-5 orbit data from the MIT-licensed FEniCS Basix table, listed in SOURCES.
        a, b, c = .45449629587435035, .09273525031089128, .3108859192633006
        orbits = [((a, a, .5-a, .5-a), .007091003462846912),
                  ((b, b, b, 1-3*b), .012248840519393654),
                  ((c, c, c, 1-3*c), .018781320953002635)]
        points, weights = [], []
        for coordinates, weight in orbits:
            orbit = sorted(set(permutations(coordinates)))
            points.extend(orbit)
            weights.extend([6*weight]*len(orbit))
        assert len(points) == 14
        return np.asarray(points), np.asarray(weights)
    assert kind in ('jacobi', 'legendre')
    jacobi = kind == 'jacobi'
    axes = [roots_jacobi(order, alpha, 0) if jacobi else leggauss(order) for alpha in (2, 1, 0)]
    u, v, t = np.meshgrid(*[(p+1)/2 for p, _ in axes], indexing='ij')
    bary = np.c_[((1-u)*(1-v)*(1-t)).ravel(), u.ravel(), ((1-u)*v).ravel(), ((1-u)*(1-v)*t).ravel()]
    weights = axes[0][1][:, None, None]*axes[1][1][None, :, None]*axes[2][1][None, None, :]
    weights = weights*6/64 if jacobi else weights*6/8*(1-u)**2*(1-v)
    weights = weights.ravel()
    assert np.all(bary > 0) and np.all(weights > 0)
    assert abs(weights.sum()-1) < 1e-13
    return bary, weights


def sources(tets, local_flux, order, kind='jacobi'):
    bary, weights = rule(order, kind)
    points = np.einsum('pi,cid->cpd', bary, tets)
    # J times dV: the RT0 1/(3V) and physical V cancel exactly.
    weighted_current = np.einsum('cim,cpid->cpmd', local_flux,
                                points[:, :, None]-tets[:, None])/3*weights[None, :, None, None]
    return points, weighted_current


def main():
    out = ROOT/'outputs/research/astra-tetra-jacobi-rule-02'
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, arrays = monotonic(), {}
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        moments = []
        specs = [(kind, q) for kind in ('legendre', 'jacobi') for q in (2, 3, 4)] + [('xiao_gimbutas', 5)]
        for kind, q in specs:
                bary, weights = rule(q, kind)
                degree = 5 if kind == 'xiao_gimbutas' else 2*q-(1 if kind == 'jacobi' else 3)
                error = 0.
                assert np.all(bary > 0) and np.all(weights > 0) and abs(weights.sum()-1) < 1e-13
                for a in range(degree+1):
                    for b in range(degree-a+1):
                        for c in range(degree-a-b+1):
                            exact = 6*factorial(a)*factorial(b)*factorial(c)/factorial(a+b+c+3)
                            actual = np.dot(weights, bary[:, 1]**a*bary[:, 2]**b*bary[:, 3]**c)
                            error = max(error, abs(actual/exact-1))
                assert error < 1e-12
                moments.append(dict(rule=kind, q=q, points=len(weights), exact_total_degree=degree, maximum_relative_moment_error=error))
        joint = geometry.load_joint()
        with np.load(ROOT/list(PINS)[0]) as d:
            current = d['whitened_face_currents']
            base = d['corrected_matrix_q3']
        E = np.linalg.inv(np.linalg.cholesky((base+base.T)/2))
        local = joint['local_face_signs'][:, :, None]*current[joint['local_face_columns']]
        cases = []
        for label, path in zip(('touch', 'nontouch'), list(PINS)[1:], strict=True):
            with np.load(ROOT/path) as d:
                a, b = d['pair_a'], d['pair_b']
                reference = d['exact_canonical_reciprocal_pair_matrices']
            for kind, q in specs:
                    p, w = sources(joint['tetrahedra_m'], local, q, kind)
                    values = np.empty_like(reference)
                    for first in range(0, len(a), 64):
                        assert monotonic() < started+120
                        sl = slice(first, first+64)
                        distance = np.linalg.norm(p[a[sl], :, None]-p[b[sl], None, :], axis=3)
                        assert distance.min() > 0
                        potential = np.einsum('pij,pjnd->pind', 1/distance, w[b[sl]], optimize=True)
                        value = 1e-7*np.einsum('pimd,pind->pmn', w[a[sl]], potential, optimize=True)
                        values[sl] = value+value.transpose(0, 2, 1)
                    scaled = E[None] @ (values-reference) @ E.T[None]
                    cases.append(dict(kind=label, rule=kind, q=q, points_per_cell=p.shape[1],
                                      signed_error_original_energy=float(np.linalg.norm(scaled.sum(axis=0), 2)),
                                      error_l1_original_energy=float(np.linalg.norm(scaled, axis=(1, 2)).sum())))
                    arrays[f'{label}_{kind}_q{q}'] = values
            arrays[f'{label}_pair_a'], arrays[f'{label}_pair_b'] = a, b
            arrays[f'{label}_reference'] = reference
        result = dict(status='QUALIFIED_POLYNOMIAL_RULE_PAIR_ACCURACY_DIAGNOSTIC', moments=moments, cases=cases, failure=None)
    except Exception:
        result = dict(status='STOP_TETRA_JACOBI_RULE', failure=traceback.format_exc())
    np.savez_compressed(out/'rule-comparison.npz', **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
                  driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS, primary_sources=SOURCES,
                  artifact_sha256=sha256((out/'rule-comparison.npz').read_bytes()).hexdigest(),
                  scope='Positive normalized polynomial quadrature qualified by every monomial through '
                  'its stated total degree. Previously qualified full-affine static pairs provide a '
                  'diagnostic of point-rule near error. Polynomial exactness does not bound 1/R error; '
                  'this does not replace singular self/touching correction or qualify FMM/field/board.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['failure'] is None else 2


if __name__ == '__main__':
    raise SystemExit(main())
