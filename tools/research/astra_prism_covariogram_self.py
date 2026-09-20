"""SPD Decap PI Evaluator v0.23.1: finite prism/rectangle halfspace self.

Uniform integrated-charge supports, with the existing q/R/D ownership intact.
Triangle-prism depth is integrated analytically; the exact triangle overlap
covariogram reduces the remaining self integral to six two-dimensional sectors.
Rectangle direct self is analytic and its image is one-dimensional quadrature.
Each routine returns normalized bare 1/R coefficients, before physical epsilon.
No cross terms of a multi-primitive charge union are supplied by these routines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic, perf_counter
import traceback

import numpy as np
from scipy.special import roots_legendre

from astra_stratified_charge_green import EPS0, ROOT, source_background


def depth_direct(rho, height):
    """Normalized integral over two identical depth intervals; rho must be >0."""
    rho = np.asarray(rho, float)
    assert height > 0 and np.isfinite(rho).all() and np.all(rho > 0)
    return 2*(np.arcsinh(height/rho)/height-1/(np.hypot(rho, height)+rho))


def depth_image(rho, z_bounds, interface):
    """Normalized integral over a depth interval and its reflected interval."""
    rho = np.asarray(rho, float)
    a, b = np.asarray(z_bounds, float)
    assert b > a and (b < interface or a > interface)
    assert np.isfinite(rho).all() and np.all(rho > 0)
    lo, hi = sorted((abs(a-interface), abs(b-interface)))
    # The common -rho drops out of this second difference. Rationalizing the
    # square-root difference also avoids losing the far-field 1/rho response.
    def primitive(u):
        return u*np.arcsinh(u/rho)-u*u/(np.hypot(rho, u)+rho)
    result = (primitive(2*hi)-2*primitive(hi+lo)+primitive(2*lo))/(b-a)**2
    assert np.isfinite(result).all() and np.all(result > 0)
    return result


def triangle_difference_sectors(triangle):
    """Six ordered edge pairs of T-T; sector factors are |cross|/area(T)."""
    triangle = np.asarray(triangle, float)
    assert triangle.shape == (3, 2) and np.isfinite(triangle).all()
    edges = triangle[1:]-triangle[0]
    area = abs(edges[0, 0]*edges[1, 1]-edges[0, 1]*edges[1, 0])/2
    assert area > 0
    vertices = np.array([triangle[a]-triangle[b] for a in range(3) for b in range(3) if a != b])
    vertices = vertices[np.argsort(np.arctan2(vertices[:, 1], vertices[:, 0]))]
    next_vertices = np.roll(vertices, -1, axis=0)
    factors = (vertices[:, 0]*next_vertices[:, 1]-vertices[:, 1]*next_vertices[:, 0])/area
    assert np.all(factors > 0) and abs(factors.sum()/12-1) < 2e-12
    return vertices, next_vertices, factors


def prism_self(triangle, z_bounds, interface, order):
    """Normalized bare direct/image prism self with one fixed quadrature order.

    T intersection (T+u) has area A*(1-gamma(u))**2, where gamma is the
    piecewise-linear gauge of the hexagon T-T. On each edge sector u=r*v(s),
    gamma=r. Thus the measure is |cross(v0,v1)|/A*r*(1-r)**2 dr ds.
    r=t**2 resolves the thin-prism radial transition. Angular panels split at
    the edge's nearest point to the origin, resolving long thin triangles.
    """
    assert isinstance(order, int) and order >= 4
    z_bounds = np.asarray(z_bounds, float)
    height = z_bounds[1]-z_bounds[0]
    assert height > 0 and (z_bounds[1] < interface or z_bounds[0] > interface)
    left, right, factors = triangle_difference_sectors(triangle)
    nodes, weights = roots_legendre(order)
    t, wt = (nodes+1)/2, weights/2
    radial = t*t
    radial_weights = wt*2*t**3*(1-t*t)**2
    direct, image, normalization = 0., 0., 0.
    for a, b, factor in zip(left, right, factors):
        delta = b-a
        closest = float(np.clip(-a@delta/(delta@delta), 0., 1.))
        cuts = np.unique([0., closest, 1.])
        for first, last in zip(cuts[:-1], cuts[1:]):
            angular = first+(last-first)*t
            angular_weights = (last-first)*wt
            radius = np.linalg.norm(a+angular[:, None]*delta, axis=1)
            rho = radius[:, None]*radial
            pair_weights = factor*angular_weights[:, None]*radial_weights
            direct += np.sum(pair_weights*depth_direct(rho, height))
            image += np.sum(pair_weights*depth_image(rho, z_bounds, interface))
            normalization += pair_weights.sum()
    assert abs(normalization-1) < 2e-12 and direct > 0 and image > 0
    return float(direct), float(image)


def rectangle_direct_self(length, height):
    """Exact normalized 1/R self of a uniform rectangle, stable at small h/L."""
    assert length > 0 and height > 0
    large, small = max(length, height), min(length, height)
    ratio = small/large
    hyp = np.hypot(1., ratio)
    value = (2*np.arcsinh(ratio)/ratio+2*np.arcsinh(1/ratio)
             +(2/3)*(ratio-(2+ratio*ratio+hyp)/(1+hyp)))/large
    assert np.isfinite(value) and value > 0
    return float(value)


def wall_self(length, z_bounds, interface, order):
    """Normalized bare direct/image self of one vertical rectangular wall."""
    assert length > 0 and isinstance(order, int) and order >= 4
    a, b = np.asarray(z_bounds, float)
    direct = rectangle_direct_self(length, b-a)
    nodes, weights = roots_legendre(order)
    t, wt = (nodes+1)/2, weights/2
    # x=L*t*t maps 2*(L-x)/L**2 dx to 4*t*(1-t*t) dt.
    image = np.sum(wt*4*t*(1-t*t)*depth_image(length*t*t, z_bounds, interface))
    assert image > 0 and np.isfinite(image)
    return direct, float(image)


def physical_halfspace_self(bare, z_bounds, interface, eps):
    a, b = np.asarray(z_bounds, float)
    assert b < interface or a > interface
    host, other = (eps[0], eps[1]) if b < interface else (eps[1], eps[0])
    reflection = (host-other)/(host+other)
    return (bare[0]+reflection*bare[1])/(4*np.pi*EPS0*host)


def refine_self(evaluator, z_bounds, interface, eps, *, gate=5e-5, max_order=512):
    previous = None
    history = []
    for order in (8, 16, 32, 64, 128, 256, 512):
        if order > max_order:
            break
        started = perf_counter()
        bare = evaluator(order)
        value = physical_halfspace_self(bare, z_bounds, interface, eps)
        change = None if previous is None else float(abs(value-previous)/abs(value))
        history.append(dict(order=order, seconds=perf_counter()-started,
                            direct_bare_per_m=bare[0], image_bare_per_m=bare[1],
                            physical_self_per_f=[value.real, value.imag], complete_self_refinement=change))
        if change is not None and change < gate:
            return value, history
        previous = value
    raise RuntimeError(f'Covariogram self refinement did not meet {gate}: {history}')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selected_check(out):
    from scipy.integrate import quad
    from shapely.geometry import Polygon
    from shapely.affinity import translate
    from assemble_astra_l02_finite_charge_self import prism_union, union_self

    started = monotonic()
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    research = ROOT/'outputs/research'
    pins = {
        research/'astra-retained-sheet-current-green-20260912-02/current-support.npz':'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
        research/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz':'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
        research/'astra-l02-finite-charge-self-20260912-02/result.json':'ae5e542c598f9b432dbc6194155802ab66567c4393a9a7f4c5868fed7a5372ba'}
    cases, failure = [], None
    try:
        for path, digest in pins.items():
            assert sha(path) == digest, path
        with np.load(list(pins)[0], allow_pickle=False) as data:
            triangles = data['piece_triangle_xy_m']; parents = data['piece_parent_free_ordinal']
        with np.load(list(pins)[1], allow_pickle=False) as data:
            z_bounds = data['z_interval_um']*1e-6
            segments = data['exterior_segment_vertices_um']*1e-6
            offsets = data['exterior_segment_offsets']
        frozen = json.loads(list(pins)[2].read_text(encoding='utf-8'))
        interfaces, eps, background = source_background()
        top, height = interfaces[0], z_bounds[1]-z_bounds[0]
        # Independent one-dimensional depth integrals at both thin/far and
        # near-axis separations validate the normalization and image signs.
        depth_checks = []
        for rho in (1e-9, 3e-6, 1e-3, .2):
            direct_ref = 2*quad(lambda u:(height-u)/np.hypot(rho,u),0,height,epsabs=1e-14,epsrel=1e-11)[0]/height**2
            mid = float(z_bounds.sum()-2*top)
            image_ref = quad(lambda d:(height-abs(d))/np.hypot(rho,mid+d),-height,height,
                             points=[0.],epsabs=1e-14,epsrel=1e-11)[0]/height**2
            direct_error = float(abs(depth_direct(rho,height)/direct_ref-1))
            image_error = float(abs(depth_image(rho,z_bounds,top)/image_ref-1))
            assert max(direct_error,image_error) < 1e-9
            depth_checks.append(dict(rho_m=rho,direct_relative=direct_error,image_relative=image_error))
        selected = [(0,triangles[parents==0][0]),(1368285,triangles[parents==1368285][0])]
        covariance_checks = []
        for row, triangle in selected:
            left, right, factors = triangle_difference_sectors(triangle)
            polygon = Polygon(triangle)
            maximum = 0.
            for a,b in zip(left,right):
                for radial,angular in ((.13,.21),(.77,.83),(1.1,.31)):
                    displacement = radial*((1-angular)*a+angular*b)
                    measured = polygon.intersection(translate(polygon,*displacement)).area/polygon.area
                    expected = max(0.,1-radial)**2
                    maximum = max(maximum,abs(measured-expected))
            assert maximum < 1e-10, maximum
            covariance_checks.append(dict(original_charge_row=row,normalized_area_error=maximum,sector_factor_sum=float(factors.sum())))
            before = monotonic()
            value, history = refine_self(lambda order:prism_self(triangle,z_bounds,top,order),z_bounds,top,eps)
            if row == 0:
                reference = complex(*frozen['cases'][0]['physical_self_per_f'])
                reference_scope = 'Pinned actual whole q0 = one triangular prism'
            else:
                parts, fraction = prism_union([triangle],z_bounds)
                reference, ref_history = union_self(parts,fraction,started+120)
                reference_scope = 'First actual clipped primitive only; fresh existing 3tet x 3tet normalized self'
            relative = float(abs(value-reference)/abs(reference))
            case = dict(kind='prism',original_charge_row=row,triangle_xy_m=triangle.tolist(),
                        history=history,reference_per_f=[reference.real,reference.imag],reference_scope=reference_scope,
                        relative_to_reference=relative,seconds=monotonic()-before)
            if row:
                case['reference_history'] = ref_history
            cases.append(case)
            assert relative < 5e-5, case
        for index, ref_case in zip((0,len(offsets)-2),frozen['cases'][-2:]):
            members = segments[offsets[index]:offsets[index+1]]
            assert len(members) == 1, 'Multi-segment q requires every internal cross term'
            length = float(np.linalg.norm(members[0,1]-members[0,0]))
            before = monotonic()
            value, history = refine_self(lambda order:wall_self(length,z_bounds,top,order),z_bounds,top,eps)
            reference = complex(*ref_case['physical_self_per_f'])
            relative = float(abs(value-reference)/abs(reference))
            case = dict(kind='wall',original_charge_row=ref_case['original_charge_row'],length_m=length,
                        history=history,reference_per_f=[reference.real,reference.imag],
                        reference_scope='Pinned actual one-rectangle sidewall, existing 2triangle x 2triangle self',
                        relative_to_reference=relative,seconds=monotonic()-before)
            cases.append(case)
            assert relative < 5e-5, case
    except Exception:
        failure = traceback.format_exc()
    report = dict(status='PASS_SELECTED_COVARIOGRAM_PRISM_RECTANGLE_SELF' if failure is None else 'STOP_SELECTED_COVARIOGRAM_SELF',
                  driver_sha256=sha(Path(__file__)),source_pins={str(p.relative_to(ROOT)):v for p,v in pins.items()},
                  elapsed_s=monotonic()-started,cases=cases,failure=failure,
                  depth_checks=locals().get('depth_checks',[]),covariogram_checks=locals().get('covariance_checks',[]),
                  source_background=locals().get('background'),
                  scope='Selected actual individual prism/rectangle supports. Refinement and independent reference comparisons use unchanged 5e-5 gate. Multi-primitive q unions require all internal cross terms; no full L02 self, near, basis or board-port accuracy claim.')
    (out/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report,allow_nan=False),flush=True)
    return 0 if failure is None else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    raise SystemExit(selected_check(parser.parse_args().output))
