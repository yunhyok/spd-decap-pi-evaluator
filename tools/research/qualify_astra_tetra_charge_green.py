"""SPD Decap PI Evaluator v0.23.1: tetra/triangle charge Coulomb control.

Normalized P0 volume and face charge densities are joined by the distributional
divergence of local RT0 current. This singular scalar primitive will be combined
with a retarded regular part; it is not a quasistatic board model selection.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.integrate import quad
from scipy.special import erf
import qualify_astra_tetra_volume_green as static
import qualify_astra_tetra_retarded_green as retarded


def charge_entities(tetrahedra):
    faces, ids = [], {}
    divergence = np.zeros((24, 24))
    divergence[:6] = np.repeat(np.eye(6), 4, axis=1)
    for ti, tetra in enumerate(tetrahedra):
        for fi, (face, _, _) in enumerate(static.faces(tetra)[1]):
            key = tuple(sorted(map(tuple, face)))
            if key not in ids:
                ids[key] = len(faces)
                faces.append(face)
            divergence[6+ids[key], 4*ti+fi] = -1
    assert len(faces) == 18
    assert np.array_equal(divergence.sum(axis=0), np.zeros(24))
    return list(tetrahedra)+faces, divergence


def measure(vertices):
    if len(vertices) == 4:
        return static.faces(vertices)[0]
    return np.linalg.norm(np.cross(vertices[1]-vertices[0], vertices[2]-vertices[0]))/2


def quadrature(vertices, order):
    if len(vertices) == 4:
        points, weights = static.tetra_quadrature(vertices, order)
    else:
        u, w = leggauss(order)
        u, w = (u+1)/2, w/2
        a, b = np.meshgrid(u, u, indexing='ij')
        bary = np.column_stack(((1-a).ravel()*(1-b).ravel(), a.ravel(), ((1-a)*b).ravel()))
        points = bary @ vertices
        weights = (2*measure(vertices)*w[:, None]*w[None, :]*(1-a)).ravel()
    weights /= measure(vertices)
    assert abs(weights.sum()-1) < 1e-13
    return points, weights


def potential_matrix(entities, order, deadline):
    result = np.zeros((24, 24))
    for a, observer in enumerate(entities):
        points, weights = quadrature(observer, order)
        for b, source in enumerate(entities):
            assert monotonic() < deadline, 'charge integral deadline'
            origin = source[0]
            inner = static.tetra_inner if len(source) == 4 else static.triangle_moments
            value = inner(source-origin, points-origin)[0]/measure(source)
            result[a, b] = weights @ value
    return result


def uniform_polarization_reference(dimensions):
    """Independent Gaussian integral for each opposite rectangle-face pair."""
    result = []
    for axis, separation in enumerate(dimensions):
        sides = np.delete(dimensions, axis)
        length, area = sides.max(), sides.prod()
        def integrand(t):
            u = t*sides/length
            small = u < 1e-3
            factor = np.zeros(2)
            v = u[small]
            factor[small] = 1-v*v/6+v**4/30-v**6/168
            v = u[~small]
            factor[~small] = np.sqrt(np.pi)*erf(v)/v+np.expm1(-v*v)/(v*v)
            return float(-np.expm1(-(t*separation/length)**2)*factor.prod())
        value, error = quad(integrand, 0, np.inf, epsabs=1e-12, epsrel=1e-12, limit=160)
        assert error < 1e-10*value
        result.append(4/np.sqrt(np.pi)*area*area/length*value)
    result = np.array(result)
    assert abs(result.sum()/(4*np.pi*dimensions.prod())-1) < 1e-12
    return result


def run(output):
    started = monotonic()
    assert not output.exists()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in retarded.PINS.values():
        assert static.source.sha(retarded.ROOT/path) == expected, path
    with np.load(retarded.ROOT/retarded.PINS['static_blocks'][0], allow_pickle=False) as data:
        tetrahedra = data['tetrahedra_m']
        uniform = data['uniform_current_face_flux'].reshape(24,3)
    entities, divergence = charge_entities(tetrahedra)
    dimensions = np.ptp(tetrahedra.reshape(-1,3), axis=0)
    volumes = np.array([measure(tetra) for tetra in tetrahedra])
    density = np.r_[volumes, np.zeros(18)]
    box_reference, _ = static.box_self_reference(dimensions)
    surface = divergence @ uniform
    assert np.linalg.norm(surface[:6]) < 1e-14*np.linalg.norm(surface)
    loop = retarded.face_loop(tetrahedra)
    assert np.linalg.norm(divergence @ loop) < 2e-15
    reference = uniform_polarization_reference(dimensions)
    history = []
    for order in (8, 16, 32):
        matrix = potential_matrix(entities, order, started+110)
        energy = np.diag(surface.T @ matrix @ surface)
        history.append(dict(order=order, polarization_energy_m3=energy.tolist(),
            polarization_relative_error=float(np.max(abs(energy-reference)/reference)),
            trace_delta_self_relative_error=float(abs(energy.sum()/(4*np.pi*dimensions.prod())-1)),
            uniform_volume_relative_error=float(abs(density @ matrix @ density/box_reference-1)),
            raw_reciprocity=float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix)),
            minimum_symmetric_eigenvalue_per_m=float(np.linalg.eigvalsh((matrix+matrix.T)/2).min())))
        print(json.dumps(history[-1]), flush=True)
    end = history[-1]
    gates = dict(polarization=end['polarization_relative_error'] < 1e-5,
        delta_self=end['trace_delta_self_relative_error'] < 1e-5,
        volume=end['uniform_volume_relative_error'] < 1e-5,
        raw_reciprocity=end['raw_reciprocity'] < 1e-5,
        positive_scalar_kernel=end['minimum_symmetric_eigenvalue_per_m'] > 0,
        refinement=end['polarization_relative_error'] < history[0]['polarization_relative_error']*.1)
    factor = 1/(4*np.pi*static.source.EPS0)
    with (output/'charge.npz').open('xb') as stream:
        np.savez_compressed(stream, tetrahedra_m=tetrahedra, triangles_m=np.array(entities[6:]),
            normalized_coulomb_matrix_per_m=matrix, potential_coefficients_per_f=matrix*factor,
            distributional_divergence=divergence, uniform_current_flux=uniform,
            uniform_volume_density_charge=density, uniform_current_charge=surface,
            loop_flux=loop, polarization_reference_m3=reference)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_TETRA_CHARGE_GREEN_CONTROL' if all(gates.values()) else 'STOP_TETRA_CHARGE_GREEN_CONTROL',
        gates=gates, script_sha256=static.source.sha(Path(__file__)), charge_sha256=static.source.sha(output/'charge.npz'),
        pins=retarded.PINS, entity_count={'volumes':6,'faces':18}, refinement=history,
        independent_polarization_energy_m3=reference.tolist(), elapsed_s=monotonic()-started,
        scope='Static singular scalar Green primitive on a controlled six-tetra box. P0 volume/face shapes have unit integrated charge; RT0 distributional divergence has +1 volume and -1 outward-face integrated flux. Every current column has zero total distributional charge. Includes volume-volume, face-volume and face-face self/near interactions without symmetry repair or clipping. Independent Gaussian opposite-face energy and sum=4pi*volume delta-self identity. Retarded scalar remainder, material solve, source geometry, port and board accuracy are not qualified.')
    with (output/'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({key:result[key] for key in ('status','gates','elapsed_s')}), flush=True)
    return 0 if all(gates.values()) else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
