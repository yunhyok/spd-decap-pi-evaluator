"""SPD Decap PI Evaluator v0.23.1: refine saved outer patches without dropping sources."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import qualify_astra_joint_composite_outer_action as composite


ROOT = Path(__file__).resolve().parents[2]
ATTR = ROOT/'outputs/research/astra-joint-composite-outer-patch-errors-03'
GATE = 5e-5
PINS = {
    'tools/research/qualify_astra_joint_composite_outer_action.py': '80d1b7c33e36448fbfe58c0938e0579bd089e9c0c83989093ccdb2fef577da74',
    'tools/research/qualify_astra_joint_mixed_point_correction.py': 'a498bbde49974620abb81e6ace23fb566c0ac022f98bbdb4dde21fef7147c82b',
    'outputs/research/astra-joint-composite-outer-patch-errors-03/result.json': '0816ee8dbd84eee038883b0aa0d96ff335ba3f581aed757b90b975daafbcd436',
    'outputs/research/astra-joint-composite-outer-patch-errors-03/patch-attribution.npz': 'e377a7ddb652c788f853953473a0c9b1952d348f4e9fe5ea5721f6b922bec93c',
    'outputs/research/astra-joint-composite-outer-action-01/composite-action.npz': '325a5bb7c06c307d67a60d005be55222d09027f28b7ef0c8890d70438fb8fbb9',
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def normalize_errors(vector_error, scalar_error, vector_rows, scalar_rows):
    return np.column_stack([
        np.linalg.norm(vector_error, axis=1)/np.linalg.norm(vector_rows, axis=0),
        np.linalg.norm(scalar_error, axis=1)/np.linalg.norm(scalar_rows, axis=0)])


def run(output, previous=None):
    start = monotonic()
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.items():
        assert digest(ROOT/path) == expected, path
    for path, expected in composite.PINS.items():
        assert digest(ROOT/path) == expected, path
    with np.load(ATTR/'patch-attribution.npz', allow_pickle=False) as d:
        patches = {key: d[key] for key in d.files}
    with np.load(ROOT/list(PINS)[-1], allow_pickle=False) as d:
        test_faces = d['tested_current_faces']
        scalar_cell, scalar_face = int(d['tested_scalar_volume'][0]), int(d['tested_scalar_face'][0])
    vector = patches['fine_vector_contribution'].copy()
    scalar = patches['fine_scalar_contribution'].copy()
    vector_error, scalar_error = patches['vector_signed_error'].copy(), patches['scalar_signed_error'].copy()
    depth = np.full(len(vector), 7, dtype=int)
    previous_pins = {}
    if previous is not None:
        receipt = json.loads((previous/'result.json').read_text(encoding='utf-8'))
        assert receipt['failure'] is None
        previous_pins = {str(previous/'result.json'): digest(previous/'result.json'),
            str(previous/'adaptive-action.npz'): digest(previous/'adaptive-action.npz')}
        assert previous_pins[str(previous/'adaptive-action.npz')] == receipt['artifact_sha256']
        assert receipt['pins'] == PINS and receipt['fixed_relative_gate'] == GATE
        with np.load(previous/'adaptive-action.npz', allow_pickle=False) as d:
            assert np.array_equal(d['patch_id'], patches['patch_id'])
            vector, scalar = d['vector_contribution'], d['scalar_contribution']
            vector_error, scalar_error = d['vector_signed_error'], d['scalar_signed_error']
            depth = d['patch_depth']
    baseline_vector, baseline_scalar = vector.sum(axis=0), scalar.sum(axis=0)
    estimated = normalize_errors(vector_error, scalar_error, baseline_vector, baseline_scalar)
    selected = np.zeros(len(vector), dtype=bool)
    # ponytail: parentwise refinement reuses existing spans; switch to leafwise only if cost fails.
    for channel in range(4):
        values = estimated[:, channel]
        total = values.sum()
        if total > GATE/4:
            order = np.argsort(-values, kind='stable')
            count = int(np.searchsorted(np.cumsum(values[order]), total-GATE/4))+1
            selected[order[:count]] = True
    assert selected.any(), 'saved estimator already within budget; no redundant calculation'
    untouched_l1 = estimated[~selected].sum(axis=0)
    assert untouched_l1.max() <= GATE/4*(1+1e-12)
    mixed = composite.mixed
    source = mixed.prepare_sources(3)
    joint = mixed.fmm.load_joint()
    points, weights, firsts, lasts = [], [], [], []
    offset = 0
    for patch in np.flatnonzero(selected):
        parent = patches['parent_vertices_m'][patch, :patches['parent_vertex_count'][patch]]
        entity = patches['entity_id'][patch]
        p, w = composite.composite_rule(parent, int(depth[patch]+2-5))
        fraction = composite.measure(parent)/composite.measure(source['entities'][entity])
        w *= fraction
        assert abs(fraction-1/32) < 2e-14
        assert abs(w.sum()-fraction) < 2e-14 and w.min() > 0
        firsts.append(offset)
        lasts.append(offset+len(p))
        offset += len(p)
        points.append(p)
        weights.append(w)
    targets, outer_weights = np.vstack(points), np.concatenate(weights)
    assert len(targets) <= 250000, 'outer target budget; use saved patch costs before another run'
    arrays = dict(patch_id=patches['patch_id'], selected_patch_ids=np.flatnonzero(selected),
        target_points_m=targets, normalized_outer_weights=outer_weights,
        record_first=np.array(firsts), record_last=np.array(lasts),
        baseline_vector_rows=baseline_vector, baseline_scalar_rows=baseline_scalar,
        baseline_patch_depth=depth.copy(), untouched_estimator_l1=untouched_l1)
    print(json.dumps(dict(stage='adaptive_targets_prepared', selected_parents=int(selected.sum()),
        target_points=len(targets), source_points=len(source['points']),
        untouched_estimator_l1=untouched_l1.tolist())), flush=True)
    failure, timings, accepted = None, {}, False
    changes, estimator = {}, []
    try:
        actual, pieces, timings = mixed.corrected_action(source, targets, 1e8, start+1200)
        arrays.update(pieces, corrected_action=actual)
        np.savez_compressed(output/'adaptive-action.npz', **arrays)
        cols, signs = joint['local_face_columns'], joint['local_face_signs']
        for patch, first, last in zip(np.flatnonzero(selected), firsts, lasts, strict=True):
            entity = int(patches['entity_id'][patch])
            p, w, values = targets[first:last], outer_weights[first:last], actual[first:last]
            new_vector, new_scalar = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
            if entity < source['nc']:
                original = source['entities'][entity]
                weighted = w[:, None, None]*(p[:, None]-original[None])/3
                local = 1e-7*np.einsum('pid,pmd->im', weighted, values[:, :6].reshape(-1, 2, 3))
                for row, face in enumerate(test_faces):
                    slots = np.flatnonzero(cols[entity] == face)
                    if len(slots):
                        assert len(slots) == 1
                        slot = int(slots[0])
                        new_vector[row] = signs[entity, slot]*local[slot]
                if entity == scalar_cell:
                    new_scalar[0] = w @ values[:, 6:]
            else:
                assert int(patches['mesh_tetra_or_face_id'][patch]) == scalar_face
                new_scalar[1] = w @ values[:, 6:]
            vector_error[patch] = new_vector-vector[patch]
            scalar_error[patch] = new_scalar-scalar[patch]
            vector[patch], scalar[patch] = new_vector, new_scalar
            depth[patch] += 2
        rows_v, rows_s = vector.sum(axis=0), scalar.sum(axis=0)
        normalized = normalize_errors(vector_error, scalar_error, rows_v, rows_s)
        estimator = normalized.sum(axis=0).tolist()
        changes = dict(vector_by_density=(np.linalg.norm(rows_v-baseline_vector, axis=0)/np.linalg.norm(rows_v, axis=0)).tolist(),
            scalar_by_density=(np.linalg.norm(rows_s-baseline_scalar, axis=0)/np.linalg.norm(rows_s, axis=0)).tolist())
        arrays.update(patch_depth=depth, vector_contribution=vector, scalar_contribution=scalar,
            vector_signed_error=vector_error, scalar_signed_error=scalar_error,
            vector_rows=rows_v, scalar_rows=rows_s, normalized_patch_error=normalized)
        assert np.isfinite(rows_v).all() and np.isfinite(rows_s).all()
        accepted = max(changes['vector_by_density']+changes['scalar_by_density']+estimator) < GATE
    except Exception:
        failure = traceback.format_exc()
    artifact = output/'adaptive-action.npz'
    np.savez_compressed(artifact, **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_SELECTED_ROWS_ADAPTIVE_OUTER_ESTIMATOR' if accepted else 'STOP_ADAPTIVE_OUTER_ESTIMATOR_GATE',
        elapsed_s=monotonic()-start, driver_sha256=digest(Path(__file__)), pins=PINS,
        previous_pins=previous_pins, artifact_sha256=digest(artifact), frequency_hz=1e8,
        fmm_eps=1e-12, omp_num_threads=os.environ.get('OMP_NUM_THREADS'), source_points=len(source['points']),
        source_quadrature_order=3, outer_quadrature_order=3, selected_parents=int(selected.sum()),
        target_points=len(targets), fixed_relative_gate=GATE, untouched_estimator_l1=untouched_l1.tolist(),
        selected_row_changes=changes, patch_estimator_l1=estimator, timings=timings, failure=failure,
        scope='All5304 current and9180 charge entities remain in the outgoing mixed action. '
        'Only positive outer quadrature is refined; unselected patch contributions are reused exactly. '
        'Both signed row change and summed last patch changes must pass5e-5. The latter is an adaptive '
        'diagnostic estimator, not a rigorous error bound. Matched-point near/source gates are inherited '
        'only for previously checked targets; new full target accuracy is not independently proved. '
        'No physical current-space convergence, all-row residual, contact solve or board accuracy claim.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'elapsed_s', 'selected_row_changes', 'patch_estimator_l1', 'timings', 'failure')}), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.output.resolve(), args.previous.resolve() if args.previous else None))
