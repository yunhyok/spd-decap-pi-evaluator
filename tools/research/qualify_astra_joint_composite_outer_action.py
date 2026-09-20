"""SPD Decap PI Evaluator v0.23.1: positive composite outer mixed action.

Longest-edge integration subdivisions leave the source mesh/current space intact.
All refinement levels share one full-source FMM call and point-near correction.
"""
import argparse
from hashlib import sha256
from itertools import combinations
import json
import os
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import qualify_astra_joint_mixed_point_correction as mixed
from qualify_astra_joint_complete_static_rows import complete_potentials
from qualify_astra_tetra_charge_green import quadrature, measure


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/qualify_astra_joint_mixed_point_correction.py': 'a498bbde49974620abb81e6ace23fb566c0ac022f98bbdb4dde21fef7147c82b',
    'tools/research/qualify_astra_joint_complete_static_rows.py': '84d47b60d3aca399fde4fdbc46a669207118747eef4525d4bcfd2db24277781e',
    'outputs/research/astra-joint-mixed-point-correction-01/result.json': '7f7b3eb021d680495af657d446a961db96963067f80dc395a67e9bf6b6780511',
}


def composite_rule(vertices, depth, order=3):
    pieces = [vertices]
    edges = np.array(list(combinations(range(len(vertices)), 2)))
    for _ in range(depth):
        children = []
        for parent in pieces:
            lengths = np.linalg.norm(parent[edges[:, 0]]-parent[edges[:, 1]], axis=1)
            a, b = edges[int(np.argmax(lengths))]
            middle = (parent[a]+parent[b])/2
            left, right = parent.copy(), parent.copy()
            left[b], right[a] = middle, middle
            children.extend((left, right))
        pieces = children
    points, weights = [], []
    original_measure = measure(vertices)
    for piece in pieces:
        p, w = quadrature(piece, order)
        points.append(p)
        weights.append(w*measure(piece)/original_measure)
    p, w = np.vstack(points), np.concatenate(weights)
    assert w.min() > 0 and abs(w.sum()-1) < 1e-12
    assert np.linalg.norm(w @ p-vertices.mean(axis=0))/np.ptp(vertices, axis=0).max() < 1e-12
    return p, w


def run(output):
    start = monotonic()
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, pin in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
    source = mixed.prepare_sources(3)
    joint = mixed.fmm.load_joint()
    with np.load(ROOT/list(mixed.PINS)[2], allow_pickle=False) as d:
        test_faces = d['tested_global_current_face_ids']
        test_cells = d['tested_current_cell_ids']
        scalar_cell = int(d['tested_scalar_volume_cell_ids'][0])
        scalar_face = int(d['tested_scalar_boundary_face_ids'][0])
    with np.load(mixed.fmm.MESH, allow_pickle=False) as d:
        boundary = d['boundary_face_ids']
    scalar_face_entity = source['nc']+int(np.flatnonzero(boundary == scalar_face)[0])
    entity_ids = list(map(int, test_cells))+[scalar_face_entity]
    points, weights, records = [], [], []
    offset = 0
    levels = (3, 5, 7)
    for depth in levels:
        for entity in entity_ids:
            p, w = composite_rule(source['entities'][entity], depth)
            records.append(dict(depth=depth, entity=entity, first=offset, last=offset+len(p)))
            offset += len(p)
            points.append(p)
            weights.append(w)
    # Actual quadrature-source locations explicitly exercise finite -ik self restoration.
    source_indices = source['offsets'][[scalar_cell, scalar_face_entity]]
    coincidence_points = source['points'][source_indices]
    targets = np.vstack(points+[coincidence_points])
    outer_weights = np.concatenate(weights)
    arrays = dict(target_points_m=targets, normalized_outer_weights=outer_weights,
        record_depth=np.array([r['depth'] for r in records]), record_entity=np.array([r['entity'] for r in records]),
        record_first=np.array([r['first'] for r in records]), record_last=np.array([r['last'] for r in records]),
        coincidence_source_point_ids=source_indices, tested_current_faces=test_faces, tested_current_cells=test_cells,
        tested_scalar_volume=np.array([scalar_cell]), tested_scalar_face=np.array([scalar_face]))
    print(json.dumps(dict(stage='prepared_all_composite_levels', target_points=len(targets), source_points=len(source['points']), levels=levels)), flush=True)
    histories, failure, timings = [], None, {}
    accepted = False
    try:
        actual, pieces, timings = mixed.corrected_action(source, targets, 1e8, start+720)
        arrays.update(pieces, corrected_action=actual)
        # Save the completed expensive action before any later accuracy gate.
        np.savez_compressed(output/'composite-action.npz', **arrays)
        previous = None
        cols, signs = joint['local_face_columns'], joint['local_face_signs']
        for depth in levels:
            vector_rows = np.zeros((len(test_faces), 2), complex)
            scalar_rows = []
            for record in records:
                if record['depth'] != depth:
                    continue
                entity = record['entity']
                sl = slice(record['first'], record['last'])
                p, w, values = targets[sl], outer_weights[sl], actual[sl]
                if entity < source['nc']:
                    vertices = source['entities'][entity]
                    weighted = w[:, None, None]*(p[:, None]-vertices[None])/3
                    local_rows = 1e-7*np.einsum('pid,pmd->im', weighted, values[:, :6].reshape(-1, 2, 3))
                    for row, face in enumerate(test_faces):
                        slots = np.flatnonzero(cols[entity] == face)
                        if len(slots):
                            slot = int(slots[0])
                            vector_rows[row] += signs[entity, slot]*local_rows[slot]
                    if entity == scalar_cell:
                        scalar_rows.append(w @ values[:, 6:])
                else:
                    scalar_rows.append(w @ values[:, 6:])
            scalar_rows = np.array(scalar_rows)
            changes = None if previous is None else dict(
                vector_by_density=(np.linalg.norm(vector_rows-previous[0], axis=0)/np.linalg.norm(vector_rows, axis=0)).tolist(),
                scalar_by_density=(np.linalg.norm(scalar_rows-previous[1], axis=0)/np.linalg.norm(scalar_rows, axis=0)).tolist())
            histories.append(dict(depth=depth, pieces_per_entity=2**depth, changes=changes))
            arrays.update({f'vector_rows_depth{depth}': vector_rows, f'scalar_rows_depth{depth}': scalar_rows})
            previous = vector_rows, scalar_rows
        expected_coincident = source['channels'][source_indices]
        actual_coincident = pieces['coincident_weighted_density'][-2:]
        assert np.allclose(actual_coincident, expected_coincident, rtol=1e-13, atol=0)
        nc = source['nc']
        a, p = complete_potentials(source['entities'][:nc], source['entities'][nc:],
            source['current_centers'], source['current_radial'], source['charge'], coincidence_points, start+720)
        exact_static = np.column_stack((a.reshape(2, 6), p))
        exact_regular = mixed.regular_reference(source, coincidence_points, 1e8, start+720)
        coincidence_reference = exact_static+exact_regular
        coincidence_error = np.linalg.norm(actual[-2:]-coincidence_reference, axis=0)/np.linalg.norm(coincidence_reference, axis=0)
        arrays.update(coincidence_reference=coincidence_reference, coincidence_error_by_channel=coincidence_error)
        last = histories[-1]['changes']
        accepted = max(last['vector_by_density']+last['scalar_by_density']) < 5e-5 and coincidence_error.max() < 5e-5
    except Exception:
        failure = traceback.format_exc()
    artifact = output/'composite-action.npz'
    np.savez_compressed(artifact, **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='ACCEPT_COMPOSITE_OUTER_SELECTED_MIXED_ROWS' if accepted else 'STOP_COMPOSITE_OUTER_SELECTED_ROWS_GATE',
        elapsed_s=monotonic()-start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), source_points=len(source['points']), target_points=len(targets),
        omp_num_threads=os.environ.get('OMP_NUM_THREADS'), source_quadrature_order=3, outer_order=3,
        frequency_hz=1e8, fixed_relative_gate=5e-5, timings=timings, refinement=histories, failure=failure,
        scope='Two complete global fine-current rows and two charge rows retain every source. '
        'Positive longest-edge outer integration subdivisions at depths3/5/7 do not change geometry '
        'or current DOFs. One full-source outgoing FMM and analytic point-near correction serves '
        'all levels. Two actual source quadrature targets test per-target omission and finite -ik '
        'self restoration against exact-inner static plus direct regular reference. Source near '
        'radius3/q3 accuracy and outer change are separate measured gates, not a full operator error '
        'bound. No all-row residual, contact solve, physical current convergence or board accuracy.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'elapsed_s', 'timings', 'refinement', 'failure')}), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
