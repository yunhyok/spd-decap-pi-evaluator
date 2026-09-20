"""SPD Decap PI Evaluator v0.23.1: 3D affine-current static Green building block.

Exact triangle/volume inner moments, positive outer tetrahedral quadrature.
This qualifies the singular static part of a retarded volume operator; it does
not select a quasistatic board model or qualify the regular retarded remainder.
"""
from pathlib import Path
from time import monotonic
from itertools import permutations
import argparse
import json
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.integrate import quad
from scipy.special import erf
from probe_astra_triangle_static_potential import triangle_potential
import qualify_astra_joint_tm_boundary as source


def triangle_moments(vertices, points):
    """Vectorized integrals of 1/R and R over a flat triangle in 3D."""
    vertices, points = np.asarray(vertices, float), np.asarray(points, float)
    edge = vertices[[1, 2, 0]]-vertices
    lengths = np.linalg.norm(edge, axis=1)
    normal = np.cross(edge[0], -edge[2])
    normal /= np.linalg.norm(normal)
    tangent = edge/lengths[:, None]
    outward = np.cross(tangent, normal)
    delta = vertices[None, :, :]-points[:, None, :]
    h = np.einsum('ped,ed->pe', delta, outward)
    lower = np.einsum('ped,ed->pe', delta, tangent)
    upper = lower+lengths
    height = np.abs((vertices[0]-points) @ normal)[:, None]
    r0 = np.hypot(h, height)
    safe = np.where(r0 == 0, 1., r0)
    delta_asinh = np.arcsinh(upper/safe)-np.arcsinh(lower/safe)
    delta_asinh[r0 == 0] = 0
    ru, rl = np.hypot(r0, upper), np.hypot(r0, lower)
    du, dl = r0*r0+height*ru, r0*r0+height*rl
    au = np.arctan(np.divide(h*upper, du, out=np.zeros_like(h), where=du != 0))
    al = np.arctan(np.divide(h*lower, dl, out=np.zeros_like(h), where=dl != 0))
    inverse_radius = np.sum(h*delta_asinh-height*(au-al), axis=1)
    delta_radius = lengths*(upper+lower)/(ru+rl)
    line_radius = .5*(lengths*ru+lower*delta_radius+r0*r0*delta_asinh)
    radius = (np.sum(h*line_radius, axis=1)+height[:, 0]**2*inverse_radius)/3
    return inverse_radius, radius


def faces(tetra):
    result = []
    volume = abs(np.linalg.det((tetra[1:]-tetra[0]).T))/6
    assert volume > 0 and np.isfinite(volume)
    for opposite in range(4):
        face = np.delete(tetra, opposite, axis=0)
        normal = np.cross(face[1]-face[0], face[2]-face[0])
        if normal @ (tetra[opposite]-face[0]) > 0:
            face = face[[0, 2, 1]]
            normal = -normal
        area = np.linalg.norm(normal)/2
        result.append((face, normal/(2*area), area))
    return volume, result


def tetra_inner(tetra, points):
    """Return ∫V 1/R and ∫V (r'-observer)/R, without a radius cutoff."""
    _, boundary = faces(tetra)
    scalar = np.zeros(len(points))
    moment = np.zeros((len(points), 3))
    for face, normal, _ in boundary:
        inv, radius = triangle_moments(face, points)
        distance = (face[0]-points) @ normal
        scalar += .5*distance*inv
        moment += radius[:, None]*normal
    return scalar, moment


def tetra_quadrature(tetra, order):
    x, w = leggauss(order)
    x, w = (x+1)/2, w/2
    u, v, t = np.meshgrid(x, x, x, indexing='ij')
    weight = (w[:, None, None]*w[None, :, None]*w[None, None, :]*(1-u)**2*(1-v)).ravel()
    bary = np.column_stack(((1-u).ravel()*(1-v).ravel()*(1-t).ravel(), u.ravel(), ((1-u)*v).ravel(), ((1-u)*(1-v)*t).ravel()))
    volume, _ = faces(tetra)
    weight *= 6*volume
    assert np.isclose(weight.sum(), volume, rtol=1e-13, atol=0)
    return bary @ tetra, weight


def tetra_pair(observer, source_tetra, order):
    origin = observer[0].copy()
    observer, source_tetra = observer-origin, source_tetra-origin
    ov, _ = faces(observer)
    sv, _ = faces(source_tetra)
    points, weights = tetra_quadrature(observer, order)
    scalar, moment = tetra_inner(source_tetra, points)
    test = (points[:, None, :]-observer[None, :, :])/(3*ov)
    trial = ((points[:, None, :]-source_tetra[None, :, :])*scalar[:, None, None]+moment[:, None, :])/(3*sv)
    return 1e-7*np.einsum('p,pid,pjd->ij', weights, test, trial)


def box_tetrahedra(dimensions):
    cube = []
    for order in permutations(range(3)):
        vertices = np.vstack((np.zeros(3), np.cumsum(np.eye(3)[list(order)], axis=0)))
        cube.append(vertices*np.asarray(dimensions))
    return cube


def box_self_reference(dimensions):
    """Independent positive 1D Gaussian-convolution integral of box×box 1/R."""
    dimensions = np.asarray(dimensions)
    length = dimensions.max()
    volume = dimensions.prod()
    def integrand(t):
        u = t*dimensions/length
        small = u < 1e-3
        factor = np.ones(3)
        us = u[small]
        factor[small] = 1-us**2/6+us**4/30-us**6/168
        ul = u[~small]
        factor[~small] = np.sqrt(np.pi)*erf(ul)/ul+np.expm1(-ul*ul)/(ul*ul)
        return float(factor.prod())
    value, error = quad(integrand, 0, np.inf, epsabs=1e-12, epsrel=1e-12, limit=160)
    assert error < 1e-10*value
    return 2/np.sqrt(np.pi)*volume*volume/length*value, error/value


def run(output):
    started = monotonic()
    assert not output.exists()
    output.mkdir(parents=True)
    assert source.sha(Path(__file__).with_name('probe_astra_triangle_static_potential.py')) == '75f55868574d16236b5e6dc14370c3fefffda2d3c171707f330eda1af8561b1e'
    rows, _ = source.source_inputs()
    dimensions = np.array([1e-4, 1e-4, rows[0]['thickness_um']*1e-6])
    cube = box_tetrahedra(dimensions)
    scalar_reference, reference_error = box_self_reference(dimensions)
    flux = np.array([[area*normal for _, normal, area in faces(tetra)[1]] for tetra in cube])
    for tetra, current in zip(cube, flux):
        volume, _ = faces(tetra)
        values = (tetra.mean(axis=0)-tetra)/(3*volume)
        assert np.allclose(values.T @ current, np.eye(3), rtol=1e-13, atol=1e-13)
    # The pinned planar oracle is reused only on new 3D face projections.
    sample_triangle = np.array([[0, 0, 0], [1, 0, 0], [.2, .7, 0]])*1e-4
    sample_points = np.array([[.3, .2, 0], [.3, .2, .1], [1.3, .2, .2], [.5, 0, 0]])*1e-4
    actual, _ = triangle_moments(sample_triangle, sample_points)
    oracle = np.array([triangle_potential(sample_triangle[:, :2], p[:2], p[2])[0] for p in sample_points])
    triangle_error = float(np.max(abs(actual-oracle)/abs(oracle)))
    histories, arrays = [], {}
    for order in (8, 16, 32):
        assert monotonic()-started < 120, 'bounded tetrahedral control exceeded120s'
        blocks = np.array([[tetra_pair(a, b, order) for b in cube] for a in cube])
        energy = np.einsum('aid,abij,bjd->d', flux, blocks, flux)
        inverse = blocks.transpose(1, 0, 3, 2)
        histories.append(dict(outer_order=order, box_uniform_current_relative_error=float(np.max(abs(energy-1e-7*scalar_reference)/(1e-7*scalar_reference))),
            raw_reciprocity=float(np.linalg.norm(blocks-inverse)/np.linalg.norm(blocks)),
            minimum_symmetric_eigenvalue=float(np.linalg.eigvalsh((blocks.transpose(0,2,1,3).reshape(24,24)+inverse.transpose(0,2,1,3).reshape(24,24))/2).min())))
    # Independent scalar quadrature also tests the affine-current partition identity.
    arrays['box_local_rt0_blocks'] = blocks
    arrays['tetrahedra_m'] = np.array(cube)
    arrays['uniform_current_face_flux'] = flux
    with (output/'blocks.npz').open('xb') as stream:
        np.savez_compressed(stream, **arrays)
    end = histories[-1]
    gates = dict(planar_oracle=triangle_error < 1e-11, uniform_box=end['box_uniform_current_relative_error'] < 1e-5,
        raw_reciprocity=end['raw_reciprocity'] < 1e-5, positive_kernel=end['minimum_symmetric_eigenvalue'] > 0,
        refinement=end['box_uniform_current_relative_error'] < histories[0]['box_uniform_current_relative_error']*.1)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='PASS_TETRA_STATIC_GREEN_CONTROL' if all(gates.values()) else 'STOP_TETRA_STATIC_GREEN_CONTROL',
        gates=gates, script_sha256=source.sha(Path(__file__)), blocks_sha256=source.sha(output/'blocks.npz'),
        dimensions_m=dimensions.tolist(), source_pins=source.PINS, planar_oracle_relative_error=triangle_error,
        gaussian_convolution_reference_relative_error=reference_error, box_scalar_self_integral_m5=scalar_reference,
        refinement=histories, elapsed_s=monotonic()-started,
        scope='Six tetrahedra covering a controlled100um-square box with retained source TOP25um thickness; its lateral box is not an extracted source conductor. Affine unit-outward-flux RT0 volume-current static Green blocks include self and common-face/common-edge pairs. No kernel symmetrization, eigenvalue clipping or radius cutoff. This is a singular-part primitive for a retarded volume reference, not acceptance of a static board approximation, skin-effect discretization, charge coupling, general3D VIE, via/pad material interface, board Z or PowerSI accuracy.')
    with (output/'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({key:result[key] for key in ('status','gates','elapsed_s')}))
    return 0 if all(gates.values()) else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
