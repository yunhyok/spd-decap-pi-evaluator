"""SPD Decap PI Evaluator v0.23.1: separated-source compression experiment.

Tensor interpolation changes Green integration, not the physical current space.
No FMM tree, near interaction, field solve or production default is created.
"""
import argparse
from hashlib import sha256
import itertools
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from numpy.polynomial.chebyshev import chebvander
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
from qualify_astra_tetra_charge_green import quadrature
from qualify_astra_joint_complete_static_rows import complete_potentials

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified.py': '3ad0e14937943f4c7941828f1e1bae345b05b5c88b812ddbdb68e69c83ca60d4',
    'tools/research/qualify_astra_tetra_charge_green.py': 'aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9',
    'tools/research/qualify_astra_tetra_volume_green.py': '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    'tools/research/qualify_astra_joint_complete_static_rows.py': '84d47b60d3aca399fde4fdbc46a669207118747eef4525d4bcfd2db24277781e',
    'outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz': '331f7a8216681b4c7625970b0b3962e120e3ae0bfb6e0e82b0cabc4ce4921eae',
    'outputs/research/astra-boundary-joint-divfree-projection-04/projection.npz': '978956fc03eeb73d5e3fcb300822f55c7829d40d72a2560a9f270ad8eb1df997',
    'outputs/research/astra-boundary-joint-divfree-projection-review-01/independent-review.json': 'dceb70d383e1650cd58c207737972cf40665bf617ccc7d59b79bbf1277e8ac51',
    'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz': '9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92',
    'outputs/research/astra-shared-interface-interior-lifts-review-03/independent-review.json': '8e3780a4f8c04d53bf008320e7c2c53f006995d9aaa47e38263c9b330cae663f',
}
FREQUENCIES = (1e3, 1e6, 1e8)
GATE = 5e-5


def load_densities():
    geometry.verify_inputs()
    for path, pin in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
    joint = geometry.load_joint()
    with np.load(ROOT/'outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz') as data:
        original, charge = data['source_face_currents'], data['source_charge_coefficients']
    with np.load(ROOT/'outputs/research/astra-boundary-joint-divfree-projection-04/projection.npz') as data:
        projected = data['projected_face_currents']
    with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as data:
        lifts = data['face_flux_basis']
    with np.load(geometry.MESH) as data:
        triangles = data['vertices_local_um'][data['face_vertices'][data['boundary_face_ids']]]*1e-6
    currents = np.column_stack((original, projected, lifts))
    local = joint['local_face_signs'][:, :, None]*currents[joint['local_face_columns']]
    tets, volumes = joint['tetrahedra_m'], joint['cell_volumes_m3']
    center = np.einsum('cim,cid->cmd', local, tets.mean(axis=1)[:, None]-tets)/(3*volumes[:, None, None])
    radial = local.sum(axis=1)/(3*volumes[:, None])
    total = np.r_[(center*volumes[:, None, None]).sum(axis=0).ravel(), charge.sum(axis=0)]
    return joint, triangles, currents, charge, center, radial, total


def source_rule(order, joint, triangles, currents, charge):
    source = geometry.build_rt0_volume_sources(currents, order, joint)
    nc, ppc = len(joint['cells']), order**3
    volume_weights = source['source_weights_m3'].reshape(nc, ppc)/joint['cell_volumes_m3'][:, None]
    channels = [np.column_stack((source['source_weighted_current_a_m'].reshape(-1, 27),
        (volume_weights[:, :, None]*charge[:nc, None]).reshape(-1, 2)))]
    points = [source['source_points_m']]
    for face, triangle in enumerate(triangles):
        p, w = quadrature(triangle, order)
        points.append(p)
        channels.append(np.column_stack((np.zeros((len(p), 27)), w[:, None]*charge[nc+face][None])))
    return np.vstack(points), np.vstack(channels)


def interpolate_sources(points, channels, lower, upper, order, deadline):
    nodes = np.cos(np.pi*(np.arange(order)+.5)/order)
    degrees = np.arange(order)
    transform = np.cos(np.outer(degrees, np.arccos(nodes)))*(2/order)
    transform[0] *= .5
    proxies = np.array(list(itertools.product(nodes, repeat=3)))*(upper-lower)/2+(upper+lower)/2
    weights = np.zeros((order**3, channels.shape[1]), complex)
    partition_error = coordinate_error = 0.
    # ponytail: bounded streamed tensor anterpolation; reuse saved weights for repeated matvecs.
    for first in range(0, len(points), 2048):
        assert monotonic() < deadline, 'compression deadline'
        x = 2*(points[first:first+2048]-(upper+lower)/2)/(upper-lower)
        assert np.abs(x).max() <= 1+1e-13
        basis = [chebvander(x[:, axis], order-1) @ transform for axis in range(3)]
        partition_error = max(partition_error, *(float(np.max(np.abs(b.sum(axis=1)-1))) for b in basis))
        coordinate_error = max(coordinate_error, *(float(np.max(np.abs(b @ nodes-x[:, a]))) for a, b in enumerate(basis)))
        tensor = np.einsum('pi,pj,pk->pijk', *basis).reshape(len(x), -1)
        weights += tensor.T @ channels[first:first+2048]
    scale = np.maximum(np.abs(channels).sum(axis=0), 1e-300)
    monopole = np.max(np.abs(weights.sum(axis=0)-channels.sum(axis=0))/scale)
    first_moment = np.max(np.abs((proxies-(upper+lower)/2).T @ weights
        -(points-(upper+lower)/2).T @ channels)/(np.linalg.norm(upper-lower)*scale[None]))
    assert max(partition_error, coordinate_error, monopole, first_moment) < 1e-12
    return proxies, weights, dict(partition=partition_error, coordinate=coordinate_error,
        monopole=float(monopole), first_moment=float(first_moment))


def direct_parts(points, channels, targets, total, deadline):
    """Full outgoing kernel with exact authoritative monopole and stable sine tail."""
    real = np.zeros((3, len(targets), channels.shape[1]), complex)
    tail = np.zeros_like(real)
    for first in range(0, len(points), 2048):
        assert monotonic() < deadline, 'direct reference deadline'
        distance = np.linalg.norm(targets[:, None]-points[None, first:first+2048], axis=2)
        assert distance.min() > 0
        density = channels[first:first+2048]
        for fi, frequency in enumerate(FREQUENCIES):
            k = 2*np.pi*frequency/geometry.C0
            x = k*distance
            assert x.max() < 1, 'bounded stable series scope'
            term = k*x*x/6
            remainder = term.copy()
            for n in range(2, 13):
                term = -term*x*x/((2*n)*(2*n+1))
                remainder += term
            real[fi] += (np.cos(x)/distance) @ density
            tail[fi] += remainder @ density
    imaginary = tail-(2*np.pi*np.asarray(FREQUENCIES)/geometry.C0)[:, None, None]*total[None, None]
    return real, imaginary, tail


def grouped_relative(actual, reference):
    if actual.ndim == 3:
        assert actual.shape[:2] == reference.shape[:2] == (3, 78)
        return [error for fi in range(3) for start in (0, 26, 52)
            for error in grouped_relative(actual[fi, start:start+26], reference[fi, start:start+26])]
    values = []
    for ids in [slice(3*i, 3*i+3) for i in range(9)]+[slice(27, 28), slice(28, 29)]:
        values.append(float(np.linalg.norm((actual-reference)[..., ids])/max(np.linalg.norm(reference[..., ids]), 1e-300)))
    return values


def run(output):
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    start, failure, arrays, history = monotonic(), None, {}, []
    try:
        joint, triangles, currents, charge, centers, radial, total = load_densities()
        vertices = joint['tetrahedra_m'].reshape(-1, 3)
        lower, upper = vertices.min(axis=0), vertices.max(axis=0)
        origin = (lower+upper)/2
        radius = np.linalg.norm(upper-lower)/2
        directions = np.array([p for p in itertools.product((-1., 0., 1.), repeat=3) if any(p)])
        directions /= np.linalg.norm(directions, axis=1)[:, None]
        distances = np.array([3*radius, 5*radius, .05])
        targets = np.concatenate([origin+r*directions for r in distances])
        arrays.update(targets_m=targets, lower_m=lower, upper_m=upper, authoritative_monopole=total,
            source_face_currents=currents, source_charge_coefficients=charge)
        reference = None
        source_count = {}
        for qorder in (3, 4):
            points, channels = source_rule(qorder, joint, triangles, currents, charge)
            source_count[qorder] = len(points)
            before = monotonic()
            parts = direct_parts(points, channels, targets, total, start+300)
            arrays.update({f'direct_{name}_q{qorder}': value for name, value in zip(('real', 'imaginary', 'sine_tail'), parts)})
            print(json.dumps(dict(stage='direct', qorder=qorder, elapsed_s=monotonic()-before)), flush=True)
            if qorder == 3:
                reference = parts
                for order in (4, 6, 8):
                    before = monotonic()
                    proxies, weights, moments = interpolate_sources(points, channels, lower, upper, order, start+300)
                    action = direct_parts(proxies, weights, targets, total, start+300)
                    errors = {name: grouped_relative(value, ref) for name, value, ref in zip(('real', 'imaginary', 'sine_tail'), action, parts)}
                    record = dict(order=order, points=len(proxies), elapsed_s=monotonic()-before, moments=moments, errors=errors)
                    history.append(record)
                    arrays.update({f'proxy_points_n{order}': proxies, f'proxy_weights_n{order}': weights})
                    arrays.update({f'proxy_{name}_n{order}': value for name, value in zip(('real', 'imaginary', 'sine_tail'), action)})
                    print(json.dumps(record), flush=True)
            else:
                source_errors = {name: grouped_relative(value, ref) for name, value, ref in zip(('real', 'imaginary', 'sine_tail'), reference, parts)}
        wide_charge = np.zeros((len(charge), 9), complex)
        wide_charge[:, :2] = charge
        vec, scalar = complete_potentials(joint['tetrahedra_m'], triangles, centers, radial, wide_charge, targets[:26], start+300)
        static = np.column_stack((vec.reshape(26, 27), scalar[:, :2]))
        # At 1kHz cos(kR)-1 is < 4e-17 on these targets, below the fixed gate.
        static_errors = grouped_relative(arrays['direct_real_q4'][0, :26], static)
        arrays['analytic_static_closest_targets'] = static
        final = history[-1]
        accepted = max(sum(final['errors'].values(), [])+sum(source_errors.values(), [])+static_errors) < GATE
        result = dict(status='ACCEPT_SEPARATED_CHEBYSHEV_SOURCE_SAMPLE' if accepted else 'STOP_SEPARATED_SOURCE_GATE',
            source_points=source_count, targets=len(targets), source_current_modes=9, independent_charge_modes=2,
            box_radius_m=float(radius), target_radii_m=distances.tolist(), frequencies_hz=FREQUENCIES,
            error_layout='frequency-major, radius-major, then9 current-vector groups and2 scalar groups; 26 directions per norm',
            history=history, q3_q4_errors=source_errors, analytic_static_q4_errors=static_errors)
    except Exception:
        accepted = False
        failure = traceback.format_exc()
        result = dict(status='STOP_SEPARATED_SOURCE_EXCEPTION', history=history)
    artifact = output/'compression.npz'
    np.savez_compressed(artifact, **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-start,
        pins=PINS, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), fixed_gate=GATE, failure=failure,
        scope='All5304 current cells and9180 independent charge supports; original smooth/fine currents, '
        'their homogeneous-Cu projections and5conforming lifts. Frequency-independent tensor interpolation '
        'of source quadrature weights only, full outgoing kernel with authoritative integrated monopole '
        'and separately stable imaginary tail. Sampled separated targets only: no uniform/operator bound, '
        'near replacement, new FMM, global assembly, space convergence, contact solve, Z or board accuracy.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
