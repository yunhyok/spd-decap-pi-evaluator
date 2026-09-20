"""SPD Decap PI Evaluator v0.23.1: all-source mixed FMM with exact near correction.

Retarded point action + analytic static near - identical static point near.
The finite -ik coincident remainder is restored once. No source is removed.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as fmm
from qualify_astra_tetra_volume_green import tetra_inner, triangle_moments
from qualify_astra_tetra_charge_green import quadrature, measure


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified.py': '3ad0e14937943f4c7941828f1e1bae345b05b5c88b812ddbdb68e69c83ca60d4',
    'outputs/research/astra-joint-complete-static-rows-02/result.json': '736e14ddb75dc02d37792b72059ba5c2a539f939ddabede3204dd5a3162deb22',
    'outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz': '331f7a8216681b4c7625970b0b3962e120e3ae0bfb6e0e82b0cabc4ce4921eae',
}


def prepare_sources(order=3):
    fmm.verify_inputs()
    for path, pin in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
    joint = fmm.load_joint()
    with np.load(ROOT/list(PINS)[2], allow_pickle=False) as d:
        current = d['source_face_currents']
        charge = d['source_charge_coefficients']
        centers = d['source_current_centers']
        radial = d['source_current_radial']
    with np.load(fmm.MESH, allow_pickle=False) as d:
        boundary = d['boundary_face_ids']
        triangles = d['vertices_local_um'][d['face_vertices'][boundary]]*1e-6
    source = fmm.build_rt0_volume_sources(current, order, joint)
    nc = len(joint['tetrahedra_m'])
    ppc = int(source['points_per_cell'][0])
    volume_weight = source['source_weights_m3'].reshape(nc, ppc)/joint['cell_volumes_m3'][:, None]
    volume_channels = np.column_stack((source['source_weighted_current_a_m'].reshape(-1, 6),
        (volume_weight[:, :, None]*charge[:nc, None]).reshape(-1, 2)))
    points = [source['source_points_m']]
    channels = [volume_channels]
    offsets = list(np.arange(nc+1)*ppc)
    for face, triangle in enumerate(triangles):
        p, w = quadrature(triangle, order)
        channels.append(np.column_stack((np.zeros((len(p), 6)), w[:, None]*charge[nc+face][None])))
        points.append(p)
        offsets.append(offsets[-1]+len(p))
    entities = list(joint['tetrahedra_m'])+list(triangles)
    entity_centers = np.array([x.mean(axis=0) for x in entities])
    radii = np.array([np.linalg.norm(x-c, axis=1).max() for x, c in zip(entities, entity_centers, strict=True)])
    return dict(points=np.vstack(points), channels=np.vstack(channels), offsets=np.asarray(offsets),
        entities=entities, entity_centers=entity_centers, radii=radii,
        current_centers=centers, current_radial=radial, charge=charge, nc=nc,
        moment_error=float(source['moment_relative_error'][0]), source_order=order)


def corrected_action(source, targets, frequency_hz, deadline, radius_factor=3.):
    k = 2*np.pi*frequency_hz/fmm.C0
    points, channels = source['points'], source['channels']
    length = float(np.ptp(np.vstack((points, targets)), axis=0).max())
    threshold = length*np.finfo(float).eps
    before = monotonic()
    answer = fmm.fmm3dpy.hfmm3d(eps=1e-12, zk=complex(k),
        sources=np.asfortranarray(points.T), charges=np.asfortranarray(channels.conj().T),
        targets=np.asfortranarray(targets.T), pgt=1, nd=channels.shape[1])
    assert answer.ier == 0 and np.isfinite(answer.pottarg).all()
    point_action = 4*np.pi*np.asarray(answer.pottarg).conj().T
    fmm_elapsed = monotonic()-before
    correction = np.zeros_like(point_action)
    coincident = np.zeros_like(point_action)
    near_count = omitted_count = 0
    for entity, vertices in enumerate(source['entities']):
        assert monotonic() < deadline, 'mixed near correction deadline'
        ids = np.flatnonzero(np.linalg.norm(targets-source['entity_centers'][entity], axis=1) <= radius_factor*source['radii'][entity])
        if not len(ids):
            continue
        near_count += len(ids)
        p = targets[ids]
        origin = vertices[0]
        exact = np.zeros((len(ids), channels.shape[1]), complex)
        if entity < source['nc']:
            potential, moment = tetra_inner(vertices-origin, p-origin)
            centered = moment+(p-vertices.mean(axis=0))*potential[:, None]
            current = potential[:, None, None]*source['current_centers'][entity][None]
            current += centered[:, None, :]*source['current_radial'][entity][None, :, None]
            exact[:, :6] = current.reshape(len(ids), 6)
        else:
            potential = triangle_moments(vertices-origin, p-origin)[0]
        exact[:, 6:] = potential[:, None]*(source['charge'][entity]/measure(vertices))[None]
        first, last = source['offsets'][entity:entity+2]
        distance = np.linalg.norm(p[:, None]-points[None, first:last], axis=2)
        nonzero = distance > threshold
        inverse = np.divide(1., distance, out=np.zeros_like(distance), where=nonzero)
        correction[ids] += exact-inverse @ channels[first:last]
        omitted_count += int(np.count_nonzero(~nonzero))
        coincident[ids] += (~nonzero) @ channels[first:last]
    corrected = point_action+correction-1j*k*coincident
    assert np.isfinite(corrected).all()
    return corrected, dict(point_action=point_action, static_correction=correction,
        coincident_weighted_density=coincident), dict(fmm_s=fmm_elapsed,
        near_correction_s=monotonic()-before-fmm_elapsed, near_point_entity_pairs=near_count,
        omitted_point_pairs=omitted_count, coincidence_threshold_m=threshold, radius_factor=radius_factor)


def regular_reference(source, targets, frequency_hz, deadline):
    k = 2*np.pi*frequency_hz/fmm.C0
    result = np.zeros((len(targets), source['channels'].shape[1]), complex)
    for first in range(0, len(source['points']), 2048):
        assert monotonic() < deadline, 'direct regular remainder deadline'
        last = first+2048
        distance = np.linalg.norm(targets[:, None]-source['points'][None, first:last], axis=2)
        kernel = np.full(distance.shape, -1j*k, complex)
        np.divide(np.expm1(-1j*k*distance), distance, out=kernel, where=distance != 0)
        result += kernel @ source['channels'][first:last]
    return result


def run(output):
    start = monotonic()
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    source = prepare_sources(3)
    with np.load(ROOT/list(PINS)[2], allow_pickle=False) as d:
        targets = d['target_points_order4']
        static_reference = np.column_stack((d['vector_potential_order4'].reshape(len(targets), 6), d['scalar_potential_order4']))
    arrays = dict(target_points_m=targets, analytic_static_reference=static_reference,
        source_points_m=source['points'], source_weighted_channels=source['channels'],
        source_entity_offsets=source['offsets'])
    failure = None
    try:
        frequency = 1e8
        actual, pieces, timings = corrected_action(source, targets, frequency, start+240)
        regular = regular_reference(source, targets, frequency, start+240)
        reference = static_reference+regular
        relative = np.linalg.norm(actual-reference, axis=0)/np.linalg.norm(reference, axis=0)
        point_relative = np.linalg.norm(pieces['point_action']-reference, axis=0)/np.linalg.norm(reference, axis=0)
        arrays.update(pieces, corrected_action=actual, direct_regular_reference=regular,
            complete_reference=reference, relative_error_by_channel=relative,
            uncorrected_error_by_channel=point_relative)
        accepted = bool(relative.max() < 5e-5)
    except Exception:
        failure = traceback.format_exc()
        accepted = False
        relative = point_relative = np.array([])
        timings = {}
    artifact = output/'mixed-point-action.npz'
    np.savez_compressed(artifact, **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='ACCEPT_ALL_SOURCE_MIXED_NEAR_CORRECTED_POINT_ACTION' if accepted else 'STOP_MIXED_POINT_CORRECTION_GATE',
        elapsed_s=monotonic()-start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), source_points=len(source['points']),
        source_entities=len(source['entities']), target_points=len(targets), frequency_hz=1e8,
        source_quadrature_order=3, source_current_moment_error=source['moment_error'],
        timings=timings, relative_error_by_channel=relative.tolist(), uncorrected_error_by_channel=point_relative.tolist(),
        fixed_relative_gate=5e-5, failure=failure,
        scope='All actual5304 current volumes and9180 charge supports. Eight complex point-density '
        'channels contain two full-affine currents and two independent test charges. Source-radius3 '
        'near treatment changes integration only; no pair is dropped. Positive-k/conjugation FMM '
        'includes the full retarded kernel; exact static near replaces identical static point near, '
        'with finite -ik coincident remainder restored. Reference uses saved exact-inner full-source '
        'static potentials plus independently summed regular retarded point remainder. This gate '
        'isolates accelerated source integration at matched target points; positive outer integration, '
        'retarded quadrature convergence, all-row residual, physical contacts and board solve remain.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'elapsed_s', 'relative_error_by_channel', 'timings', 'failure')}), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
