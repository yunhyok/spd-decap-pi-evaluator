"""SPD Decap PI Evaluator v0.23.1: uniform q8 outer mixed rows with touching replacement."""
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic
import numpy as np
import qualify_astra_joint_mixed_point_correction as mixed
from qualify_astra_joint_complete_static_rows import complete_potentials
from qualify_astra_tetra_charge_green import quadrature
from review_astra_joint_mixed_point_correction import regular_reference


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/qualify_astra_joint_mixed_point_correction.py': 'a498bbde49974620abb81e6ace23fb566c0ac022f98bbdb4dde21fef7147c82b',
    'tools/research/qualify_astra_joint_complete_static_rows.py': '84d47b60d3aca399fde4fdbc46a669207118747eef4525d4bcfd2db24277781e',
    'outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz': '331f7a8216681b4c7625970b0b3962e120e3ae0bfb6e0e82b0cabc4ce4921eae',
    'outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz': '3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1',
    'outputs/research/astra-outer-static-touching-replacement-02/self-replacement.npz': 'bcf2202698cefd0c256a3cd20152259ccc1bd50d602ec8bb6d4527e2914a1655',
    'outputs/research/astra-complete-static-touching-outer-orders-01/static-outer-orders.npz': 'd4bc0218cd367532b108251d803994eb260c5dd15103d367e6d318ce8be0025c',
}


def load(path):
    with np.load(path, allow_pickle=False) as d:
        return {key: d[key] for key in d.files}


def changes(a, b):
    return (np.linalg.norm(a-b, axis=0)/np.linalg.norm(b, axis=0)).tolist()


def run():
    start = monotonic()
    output = ROOT/'outputs/research/astra-uniform-touching-mixed-rows-01'
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
    saved, census, adaptive, static_orders = [load(ROOT/path) for path in list(PINS)[2:]]
    source = mixed.prepare_sources(3)
    joint = mixed.fmm.load_joint()
    targets = saved['target_points_order8']
    static_reference = np.column_stack((saved['vector_potential_order8'].reshape(len(targets), 6), saved['scalar_potential_order8']))
    nc = source['nc']
    ids = census['entity_vertex_ids']
    faces, cells = saved['tested_global_current_face_ids'], saved['tested_current_cell_ids']
    scalar_cell = int(saved['tested_scalar_volume_cell_ids'][0])
    scalar_face = int(saved['tested_scalar_boundary_face_ids'][0])
    scalar_entity = nc+int(np.flatnonzero(census['boundary_face_ids'] == scalar_face)[0])
    records, weights, offset = [], [], 0
    for entity in list(cells)+[scalar_entity]:
        p, w = quadrature(source['entities'][entity], 8)
        assert np.allclose(p, targets[offset:offset+len(p)], rtol=0, atol=2e-19)
        assert w.min() > 0 and abs(w.sum()-1) < 2e-14
        vertices = source['entities'][entity]
        center = vertices.mean(axis=0)
        variance = np.sum((vertices-center)**2)/(len(vertices)*(len(vertices)+1))
        assert abs(w @ np.sum((p-center)**2, axis=1)/variance-1) < 2e-13
        records.append((int(entity), offset, offset+len(p)))
        weights.append(w)
        offset += len(p)
    assert offset == len(targets) == 1600
    weights = np.concatenate(weights)
    print(json.dumps(dict(stage='uniform_q8_prepared', target_points=len(targets), source_points=len(source['points']))), flush=True)
    actual, pieces, timings = mixed.corrected_action(source, targets, 1e8, start+600)
    arrays = dict(target_points_m=targets, normalized_outer_weights=weights, corrected_point_action=actual,
        record_entity=np.array([r[0] for r in records]), record_first=np.array([r[1] for r in records]),
        record_last=np.array([r[2] for r in records]), **pieces)
    artifact = output/'uniform-mixed-rows.npz'
    np.savez_compressed(artifact, **arrays)
    print(json.dumps(dict(stage='mixed_point_action_saved', elapsed_s=monotonic()-start, timings=timings)), flush=True)
    before_reference = monotonic()
    regular = regular_reference(source['points'], source['channels'], targets, 1e8)
    reference = static_reference+regular
    point_errors = np.linalg.norm(actual-reference, axis=0)/np.linalg.norm(reference, axis=0)
    subtracted = np.zeros_like(actual)
    for entity, first, last in records:
        p = targets[first:last]
        touching = np.flatnonzero(np.isin(ids, ids[entity][ids[entity] >= 0]).any(axis=1))
        for other in touching:
            v_ids = [other] if other < nc else []
            s_ids = [] if other < nc else [other]
            av, ass = complete_potentials([source['entities'][i] for i in v_ids], [source['entities'][i] for i in s_ids],
                source['current_centers'][v_ids], source['current_radial'][v_ids], source['charge'][[other]], p, start+600)
            values = np.column_stack((av.reshape(len(p), 6), ass))
            outside = np.linalg.norm(p-source['entity_centers'][other], axis=1) > 3*source['radii'][other]
            if outside.any():
                sf, sl = source['offsets'][other:other+2]
                distance = np.linalg.norm(p[outside, None]-source['points'][None, sf:sl], axis=2)
                assert distance.min() > 0
                values[outside] = (1/distance) @ source['channels'][sf:sl]
            subtracted[first:last] += values

    def integrate(values):
        vector, scalar = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
        for entity, first, last in records:
            p, w = targets[first:last], weights[first:last]
            if entity < nc:
                vertices = source['entities'][entity]
                for row, face in enumerate(faces):
                    for slot in np.flatnonzero(joint['local_face_columns'][entity] == face):
                        basis = joint['local_face_signs'][entity, slot]*w[:, None]*(p-vertices[slot])/3
                        vector[row] += 1e-7*np.einsum('pd,pmd->m', basis, values[first:last, :6].reshape(-1, 2, 3))
                if entity == scalar_cell:
                    scalar[0] = w @ values[first:last, 6:]
            else:
                scalar[1] = w @ values[first:last, 6:]
        return vector, scalar

    vr, sr = integrate(actual-subtracted)
    vr += adaptive['exact_static_self_vector']
    sr += adaptive['exact_static_self_scalar']
    regular_v, regular_s = integrate(regular)
    reference_v = static_orders['replaced_vector_order8']+regular_v
    reference_s = static_orders['replaced_scalar_order8']+regular_s
    errors = dict(uniform_vs_adaptive_vector=changes(vr, adaptive['replaced_vector_adaptive']),
        uniform_vs_adaptive_scalar=changes(sr, adaptive['replaced_scalar_adaptive']),
        source_integration_vector=changes(vr, reference_v), source_integration_scalar=changes(sr, reference_s))
    # For real k: |exp(-ikR)-1+ikR|/R <= k^2 R/2. Any two positive
    # degree-2-exact outer rules have identical constant/linear moments. Cauchy
    # bounds their regular-remainder difference using exact simplex second moments.
    k = 2*np.pi*1e8/mixed.fmm.C0
    cv = np.linalg.norm(source['channels'][:, :6].reshape(-1, 2, 3), axis=2)
    cq = np.abs(source['channels'][:, 6:])
    bound_v, bound_s = np.zeros((2, 2)), np.zeros((2, 2))
    for entity, first, last in records:
        vertices = source['entities'][entity]
        center = vertices.mean(axis=0)
        variance = np.sum((vertices-center)**2)/(len(vertices)*(len(vertices)+1))
        rms_distance = np.sqrt(np.sum((source['points']-center)**2, axis=1)+variance)
        if entity < nc:
            for row, face in enumerate(faces):
                for slot in np.flatnonzero(joint['local_face_columns'][entity] == face):
                    test_rms = np.sqrt(variance+np.sum((center-vertices[slot])**2))/3
                    bound_v[row] += 1e-7*k*k*test_rms*(rms_distance @ cv)
            if entity == scalar_cell:
                bound_s[0] = k*k*(rms_distance @ cq)
        else:
            bound_s[1] = k*k*(rms_distance @ cq)
    normalized_bounds = dict(vector=(np.linalg.norm(bound_v, axis=0)/np.linalg.norm(vr, axis=0)).tolist(),
        scalar=(np.linalg.norm(bound_s, axis=0)/np.linalg.norm(sr, axis=0)).tolist())
    arrays.update(subtracted_static_touching=subtracted, complete_point_reference=reference,
        direct_regular_reference=regular, vector_rows=vr, scalar_rows=sr,
        exact_static_plus_regular_vector_rows=reference_v, exact_static_plus_regular_scalar_rows=reference_s,
        point_error_by_channel=point_errors, regular_outer_two_rule_absolute_bound_vector=bound_v,
        regular_outer_two_rule_absolute_bound_scalar=bound_s)
    np.savez_compressed(artifact, **arrays)
    accepted = max(point_errors.tolist()+[x for values in errors.values() for x in values]) < 5e-5
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_UNIFORM_Q8_TOUCHING_MIXED_SELECTED_ROWS' if accepted else 'STOP_UNIFORM_Q8_TOUCHING_MIXED_GATE',
        elapsed_s=monotonic()-start, reference_and_review_s=monotonic()-before_reference,
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), target_points=len(targets),
        source_points=len(source['points']), frequency_hz=1e8, outer_order=8, source_order=3,
        fmm_eps=1e-12, omp_num_threads=os.environ.get('OMP_NUM_THREADS'), timings=timings,
        point_error_by_channel=point_errors.tolist(), relative_row_checks=errors,
        regular_outer_two_rule_bound=normalized_bounds, fixed_relative_gate=5e-5,
        scope='All source supports; four selected global current/charge rows and two complex densities. '
        'Uniform positive q8 replaces the large composite target set; static touching references are reused. '
        'Every new target is compared to complete analytic static plus directly summed regular point remainder. '
        'Comparison to saved adaptive mixed rows and analytic q8 source integrals uses5e-5. '
        'The Taylor/Cauchy bound concerns outer integration of the fixed point-source regular kernel only; '
        'the full retarded term remains in the computation. No retarded source-quadrature, full-operator, '
        'physical field, skin/current-space or board convergence claim.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    raise SystemExit(run())
