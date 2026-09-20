"""SPD Decap PI Evaluator v0.23.1: saved outer self subtraction discriminator."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
import qualify_astra_joint_mixed_point_correction as mixed
from qualify_astra_tetra_charge_green import measure
from qualify_astra_tetra_volume_green import tetra_inner, triangle_moments, tetra_pair
from qualify_astra_source_joint_charge_green import scalar_pair
from qualify_astra_conforming_power_joint_sparse_current import local_mass


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/qualify_astra_joint_mixed_point_correction.py': 'a498bbde49974620abb81e6ace23fb566c0ac022f98bbdb4dde21fef7147c82b',
    'outputs/research/astra-joint-composite-outer-action-01/composite-action.npz': '325a5bb7c06c307d67a60d005be55222d09027f28b7ef0c8890d70438fb8fbb9',
    'outputs/research/astra-joint-adaptive-outer-action-01/adaptive-action.npz': 'c16ba79d8033fe6fe4c197fa3bca362ca2c6d990d0da93c9b8a5231b758a2bf5',
    'outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz': 'fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd',
    'outputs/research/astra-joint-composite-outer-patch-errors-03/patch-attribution.npz': 'e377a7ddb652c788f853953473a0c9b1952d348f4e9fe5ea5721f6b922bec93c',
}


def load(path):
    with np.load(path, allow_pickle=False) as d:
        return {key: d[key] for key in d.files}


def relative(a, b):
    return float(np.linalg.norm(a-b)/np.linalg.norm(b))


def run(touching=False, pair_reference=None):
    start = monotonic()
    output = ROOT/('outputs/research/astra-outer-static-touching-replacement-02' if pair_reference else 'outputs/research/astra-outer-static-touching-replacement-01' if touching
        else 'outputs/research/astra-outer-static-self-replacement-02')
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
    base, adaptive, diagonal, patches = [load(ROOT/path) for path in list(PINS)[1:]]
    source = mixed.prepare_sources(3)
    joint = mixed.fmm.load_joint()
    nc = source['nc']
    cols, signs = joint['local_face_columns'], joint['local_face_signs']
    faces = base['tested_current_faces']
    scalar_cell = int(base['tested_scalar_volume'][0])
    scalar_face = int(base['tested_scalar_face'][0])
    charge_face_entity = nc+int(np.flatnonzero(diagonal['boundary_face_ids'] == scalar_face)[0])
    with np.load(ROOT/list(mixed.PINS)[2], allow_pickle=False) as d:
        current = d['source_face_currents']
    observers = list(map(int, base['tested_current_cells']))+[charge_face_entity]
    neighbors = {entity: np.array([entity]) for entity in observers}
    extra_pins = {}
    if touching:
        census = ROOT/'outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz'
        extra_pins[str(census.relative_to(ROOT))] = '3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1'
        assert sha256(census.read_bytes()).hexdigest() == next(iter(extra_pins.values()))
        ids = load(census)['entity_vertex_ids']
        for entity in observers:
            neighbors[entity] = np.flatnonzero(np.isin(ids, ids[entity][ids[entity] >= 0]).any(axis=1))

    def integrate(entity, p, w, values):
        v, s = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
        if entity < nc:
            vertices = source['entities'][entity]
            volume = measure(vertices)
            for row, face in enumerate(faces):
                slots = np.flatnonzero(cols[entity] == face)
                if len(slots):
                    slot = int(slots[0])
                    basis = signs[entity, slot]*(p-vertices[slot])/(3*volume)
                    for channel in range(2):
                        v[row, channel] = 1e-7*np.sum(volume*w*np.sum(basis*values[:, 3*channel:3*channel+3], axis=1))
            if entity == scalar_cell:
                s[0] = w @ values[:, 6:]
        else:
            assert entity == charge_face_entity
            s[1] = w @ values[:, 6:]
        return v, s

    def single_static(entity, p):
        vertices = source['entities'][entity]
        origin = vertices[0]
        values = np.zeros((len(p), 8), complex)
        if entity < nc:
            potential, moment = tetra_inner(vertices-origin, p-origin)
            centered = moment+(p-vertices.mean(axis=0))*potential[:, None]
            values[:, :6] = (potential[:, None, None]*source['current_centers'][entity][None]
                + centered[:, None, :]*source['current_radial'][entity][None, :, None]).reshape(-1, 6)
        else:
            potential = triangle_moments(vertices-origin, p-origin)[0]
        values[:, 6:] = potential[:, None]*source['charge'][entity][None]/measure(vertices)
        outside = np.linalg.norm(p-source['entity_centers'][entity], axis=1) > 3*source['radii'][entity]
        if outside.any():
            first, last = source['offsets'][entity:entity+2]
            distance = np.linalg.norm(p[outside, None]-source['points'][None, first:last], axis=2)
            assert distance.min() > 0
            values[outside] = (1/distance) @ source['channels'][first:last]
        return values

    def own_static(entity, p):
        values = np.zeros((len(p), 8), complex)
        for other in neighbors[entity]:
            values += single_static(other, p)
        return values

    exact_v, exact_s = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
    pair_records, vector_blocks, scalar_values = [], [], []
    cached_pairs = {}
    if pair_reference:
        receipt = json.loads((pair_reference/'result.json').read_text(encoding='utf-8'))
        assert receipt['pins'] == PINS and receipt['replace_all_touching'] and not receipt['failed_pairs']
        assert receipt['extra_pins'] == extra_pins
        for name, field in [('touching-pair-references.npz', 'static_pair_artifact_sha256'), ('pair-records.json', 'pair_records_sha256')]:
            actual_hash = sha256((pair_reference/name).read_bytes()).hexdigest()
            assert actual_hash == receipt[field]
            extra_pins[str(pair_reference/name)] = actual_hash
        extra_pins[str(pair_reference/'result.json')] = sha256((pair_reference/'result.json').read_bytes()).hexdigest()
        cached = load(pair_reference/'touching-pair-references.npz')
        counts = dict(vector=0, scalar=0)
        for record in json.loads((pair_reference/'pair-records.json').read_text(encoding='utf-8')):
            kind = record['kind']
            key = 'vector_forward_reverse_h' if kind == 'vector' else 'scalar_forward_reverse_per_m'
            cached_pairs[kind, record['observer'], record['source']] = record, cached[key][counts[kind]]
            counts[kind] += 1
    whitening = {}
    for entity in set(int(i) for e in observers for i in neighbors[e] if i < nc):
        vertices = source['entities'][entity]
        whitening[entity] = np.linalg.solve(np.linalg.cholesky(local_mass(vertices, measure(vertices))).T, np.eye(4))
    for entity in base['tested_current_cells']:
        for other in neighbors[entity]:
            if other >= nc:
                continue
            order = int(diagonal['final_order'][entity])
            change = reciprocity = 0.
            if ('vector', int(entity), int(other)) in cached_pairs:
                record, values = cached_pairs['vector', int(entity), int(other)]
                order, change, reciprocity = record['order'], record['change'], record['reciprocity']
                block, reverse = values
            elif entity == other:
                block = diagonal['static_vector_self_h'][entity]
                reverse = block.T
            else:
                old = None
                wa, wb = whitening[entity], whitening[other]
                for order in (8, 16, 32, 64):
                    assert monotonic()-start < 300, 'bounded touching pair deadline'
                    block = tetra_pair(source['entities'][entity], source['entities'][other], order)
                    reverse = tetra_pair(source['entities'][other], source['entities'][entity], order)
                    scaled = np.array([wa.T @ block @ wb, wa.T @ reverse.T @ wb])
                    change = np.inf if old is None else float(np.linalg.norm(scaled-old)/np.linalg.norm(scaled))
                    reciprocity = float(np.linalg.norm(scaled[0]-scaled[1])/np.linalg.norm(scaled[0]))
                    old = scaled
                    if max(change, reciprocity) < 5e-5:
                        break
            local_current = signs[other, :, None]*current[cols[other]]
            local = block @ local_current
            for row, face in enumerate(faces):
                for slot in np.flatnonzero(cols[entity] == face):
                    exact_v[row] += signs[entity, slot]*local[slot]
            vector_blocks.append([block, reverse])
            pair_records.append(dict(kind='vector', observer=int(entity), source=int(other), order=order,
                change=change, reciprocity=reciprocity, accepted=max(change, reciprocity) < 5e-5))
    for row, entity in enumerate((scalar_cell, charge_face_entity)):
        for other in neighbors[entity]:
            order = int(diagonal['final_order'][entity])
            change = reciprocity = 0.
            if ('scalar', int(entity), int(other)) in cached_pairs:
                record, values = cached_pairs['scalar', int(entity), int(other)]
                order, change, reciprocity = record['order'], record['change'], record['reciprocity']
                ab, ba = values
            elif entity == other:
                ab = ba = diagonal['static_scalar_self_per_m'][entity]
            else:
                old = None
                for order in (8, 16, 32, 64):
                    assert monotonic()-start < 300, 'bounded touching pair deadline'
                    ab = scalar_pair(source['entities'][entity], source['entities'][other], order)
                    ba = scalar_pair(source['entities'][other], source['entities'][entity], order)
                    values = np.array([ab, ba])
                    assert min(ab, ba) > 0 and np.isfinite(values).all()
                    change = np.inf if old is None else float(np.linalg.norm(values-old)/np.linalg.norm(values))
                    reciprocity = float(abs(ab-ba)/max(ab, ba))
                    old = values
                    if max(change, reciprocity) < 5e-5:
                        break
            exact_s[row] += ab*source['charge'][other]
            scalar_values.append([ab, ba])
            pair_records.append(dict(kind='scalar', observer=int(entity), source=int(other), order=order,
                change=change, reciprocity=reciprocity, accepted=max(change, reciprocity) < 5e-5))
    pair_artifact = output/'touching-pair-references.npz'
    np.savez_compressed(pair_artifact, vector_forward_reverse_h=np.array(vector_blocks),
        scalar_forward_reverse_per_m=np.array(scalar_values))
    (output/'pair-records.json').write_text(json.dumps(pair_records, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(stage='static_pair_references', pairs=len(pair_records),
        rejected=sum(not r['accepted'] for r in pair_records), elapsed_s=monotonic()-start)), flush=True)
    own_v, own_s = {}, {}
    base_static = np.zeros_like(base['corrected_action'])
    adaptive_static = np.zeros_like(adaptive['corrected_action'])
    reconstruction = {}
    for depth in (3, 5, 7):
        ov, os = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
        rv, rs = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
        for i in np.flatnonzero(base['record_depth'] == depth):
            entity = int(base['record_entity'][i])
            sl = slice(int(base['record_first'][i]), int(base['record_last'][i]))
            p, w = base['target_points_m'][sl], base['normalized_outer_weights'][sl]
            base_static[sl] = own_static(entity, p)
            v, s = integrate(entity, p, w, base_static[sl])
            ov += v; os += s
            v, s = integrate(entity, p, w, base['corrected_action'][sl])
            rv += v; rs += s
        own_v[str(depth)], own_s[str(depth)] = ov, os
        reconstruction[str(depth)] = [relative(rv, base[f'vector_rows_depth{depth}']), relative(rs, base[f'scalar_rows_depth{depth}'])]
    av, ass = own_v['7'].copy(), own_s['7'].copy()
    rebuilt_v, rebuilt_s = patches['fine_vector_contribution'].copy(), patches['fine_scalar_contribution'].copy()
    for i, patch in enumerate(adaptive['selected_patch_ids']):
        entity = int(patches['entity_id'][patch])
        first, last = patches['depth7_target_spans'][patch]
        p, w = base['target_points_m'][first:last], base['normalized_outer_weights'][first:last]
        v, s = integrate(entity, p, w, base_static[first:last])
        av -= v; ass -= s
        first, last = adaptive['record_first'][i], adaptive['record_last'][i]
        p, w = adaptive['target_points_m'][first:last], adaptive['normalized_outer_weights'][first:last]
        adaptive_static[first:last] = own_static(entity, p)
        v, s = integrate(entity, p, w, adaptive_static[first:last])
        av += v; ass += s
        rebuilt_v[patch], rebuilt_s[patch] = integrate(entity, p, w, adaptive['corrected_action'][first:last])
    reconstruction['adaptive'] = [relative(rebuilt_v, adaptive['vector_contribution']), relative(rebuilt_s, adaptive['scalar_contribution'])]
    assert max(x for pair in reconstruction.values() for x in pair) < 3e-14
    own_v['adaptive'], own_s['adaptive'] = av, ass
    rows_v = {str(d): base[f'vector_rows_depth{d}'] for d in (3, 5, 7)}
    rows_s = {str(d): base[f'scalar_rows_depth{d}'] for d in (3, 5, 7)}
    rows_v['adaptive'], rows_s['adaptive'] = adaptive['vector_rows'], adaptive['scalar_rows']
    arrays, histories = {}, []
    previous = None
    for key in ('3', '5', '7', 'adaptive'):
        v, s = rows_v[key]-own_v[key]+exact_v, rows_s[key]-own_s[key]+exact_s
        arrays.update({f'own_static_vector_{key}': own_v[key], f'own_static_scalar_{key}': own_s[key],
            f'replaced_vector_{key}': v, f'replaced_scalar_{key}': s})
        change = None if previous is None else dict(vector_by_density=(np.linalg.norm(v-previous[0], axis=0)/np.linalg.norm(v, axis=0)).tolist(),
            scalar_by_density=(np.linalg.norm(s-previous[1], axis=0)/np.linalg.norm(s, axis=0)).tolist())
        histories.append(dict(level=key, changes=change))
        previous = v, s
    patch_static_v, patch_static_s = {}, {}
    for depth in (5, 7):
        vs, ss = [], []
        for patch, (first, last) in enumerate(patches[f'depth{depth}_target_spans']):
            entity = int(patches['entity_id'][patch])
            p, w = base['target_points_m'][first:last], base['normalized_outer_weights'][first:last]
            v, s = integrate(entity, p, w, base_static[first:last])
            vs.append(v); ss.append(s)
        patch_static_v[depth], patch_static_s[depth] = np.array(vs), np.array(ss)
    error_v = patches['vector_signed_error']-(patch_static_v[7]-patch_static_v[5])
    error_s = patches['scalar_signed_error']-(patch_static_s[7]-patch_static_s[5])
    static_v_adaptive, static_s_adaptive = patch_static_v[7].copy(), patch_static_s[7].copy()
    for i, patch in enumerate(adaptive['selected_patch_ids']):
        first, last = adaptive['record_first'][i], adaptive['record_last'][i]
        entity = int(patches['entity_id'][patch])
        p, w = adaptive['target_points_m'][first:last], adaptive['normalized_outer_weights'][first:last]
        v, s = integrate(entity, p, w, adaptive_static[first:last])
        error_v[patch] = adaptive['vector_signed_error'][patch]-(v-patch_static_v[7][patch])
        error_s[patch] = adaptive['scalar_signed_error'][patch]-(s-patch_static_s[7][patch])
        static_v_adaptive[patch], static_s_adaptive[patch] = v, s
    normalized_error = np.column_stack([np.linalg.norm(error_v, axis=1)/np.linalg.norm(previous[0], axis=0),
        np.linalg.norm(error_s, axis=1)/np.linalg.norm(previous[1], axis=0)])
    estimator = normalized_error.sum(axis=0)
    arrays.update(base_subtracted_static_action=base_static, adaptive_subtracted_static_action=adaptive_static,
        patch_static_vector_depth5=patch_static_v[5], patch_static_vector_depth7=patch_static_v[7],
        patch_static_scalar_depth5=patch_static_s[5], patch_static_scalar_depth7=patch_static_s[7],
        patch_static_vector_adaptive=static_v_adaptive, patch_static_scalar_adaptive=static_s_adaptive,
        remaining_vector_signed_error=error_v, remaining_scalar_signed_error=error_s,
        normalized_remaining_patch_error=normalized_error)
    artifact = output/'self-replacement.npz'
    np.savez_compressed(artifact, exact_static_self_vector=exact_v, exact_static_self_scalar=exact_s, **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='COMPLETE_STATIC_REPLACEMENT_DISCRIMINATOR' if all(r['accepted'] for r in pair_records) else 'STOP_STATIC_TOUCHING_PAIR_GATE', elapsed_s=monotonic()-start,
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), saved_row_reconstruction_relative=reconstruction,
        replace_all_touching=touching, extra_pins=extra_pins,
        static_pair_artifact_sha256=sha256(pair_artifact.read_bytes()).hexdigest(),
        pair_records_sha256=sha256((output/'pair-records.json').read_bytes()).hexdigest(),
        pair_count=len(pair_records), failed_pairs=[r for r in pair_records if not r['accepted']],
        static_touching_entity_counts={str(e): len(neighbors[e]) for e in observers},
        remaining_patch_estimator_l1=estimator.tolist(), reused_static_pairs=bool(pair_reference),
        signed_and_patch_change_gate_pass=bool(max(histories[-1]['changes']['vector_by_density']
            +histories[-1]['changes']['scalar_by_density']+estimator.tolist()) < 5e-5),
        refinement_after_static_self_replacement=histories,
        scope='Saved mixed outer samples only; subtract the selected static source action actually used '
        '(analytic within radius3, identical source quadrature beyond it) and add '
        'high-order pair references once, reusing qualified self blocks. Retarded self and all other source interactions '
        'remain. This isolates own-self outer error without a new FMM, field solve or mesh. '
        'The high-order self blocks remain numerical references, not exact continuum integrals. '
        'The touching option includes all shared-face/edge/vertex entities. No operator accuracy is promoted.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--touching', action='store_true')
    parser.add_argument('--pair-reference', type=Path)
    args = parser.parse_args()
    assert args.pair_reference is None or args.touching
    run(args.touching, args.pair_reference.resolve() if args.pair_reference else None)
