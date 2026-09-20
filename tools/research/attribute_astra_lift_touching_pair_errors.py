"""SPD Decap PI Evaluator v0.23.1: saved-matrix touching-pair error attribution.

No FMM or field solve. The difference of two point rules is an estimator,
not a bound on the uncomputed continuous integral.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry

ROOT = geometry.ROOT
PINS = {
    'tools/research/apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified.py': '3ad0e14937943f4c7941828f1e1bae345b05b5c88b812ddbdb68e69c83ca60d4',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-lift-static-self-matrix-01/driver-at-run.py': '3d22d47953cadb396051afbbf84ab8f01036d5b21ecb0a6e6b1d0b2fb0688b9e',
}


def touching_pairs(cells, vertex_count):
    incidence = [[] for _ in range(vertex_count)]
    for cell, vertices in enumerate(cells):
        for vertex in vertices:
            incidence[vertex].append(cell)
    labels = []
    for owners in incidence:
        if len(owners) > 1:
            a, b = np.triu_indices(len(owners), 1)
            owners = np.asarray(owners)
            labels.append(owners[a]*len(cells)+owners[b])
    labels = np.unique(np.concatenate(labels))
    return labels//len(cells), labels % len(cells)


def run(output):
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, arrays, failure = monotonic(), {}, None
    try:
        geometry.verify_inputs()
        for path, pin in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
        joint = geometry.load_joint()
        with np.load(ROOT/'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz') as d:
            currents = d['whitened_face_currents']
            baseline = d['corrected_matrix_q3']
            full_difference = baseline-d['corrected_matrix_q2']
        assert np.linalg.norm(baseline-baseline.T)/np.linalg.norm(baseline) < 1e-10
        energy_scale = np.linalg.inv(np.linalg.cholesky((baseline+baseline.T)/2))
        rows, columns = touching_pairs(joint['cells'], len(joint['vertices_m']))
        assert len(rows) < 2_000_000 and np.all(rows < columns)
        print(json.dumps(dict(stage='pair_ownership', unordered_touching_pairs=len(rows))), flush=True)
        rules = []
        for order in (2, 3):
            rule = geometry.build_rt0_volume_sources(currents, order, joint)
            rules.append((rule['source_points_m'].reshape(len(joint['cells']), -1, 3),
                rule['source_weighted_current_a_m'].reshape(len(joint['cells']), -1, 5, 3)))
        differences = np.empty((len(rows), 5, 5))
        for first in range(0, len(rows), 256):
            assert monotonic() < started+180, 'touching attribution deadline'
            a, b = rows[first:first+256], columns[first:first+256]
            delta = np.zeros((len(a), 5, 5))
            for sign, (points, weighted) in zip((-1, 1), rules, strict=True):
                distance = np.linalg.norm(points[a, :, None]-points[b, None, :], axis=3)
                assert distance.min() > 0
                potential = np.einsum('pij,pjna->pina', 1/distance, weighted[b], optimize=True)
                directed = np.einsum('pima,pina->pmn', weighted[a], potential, optimize=True)
                delta += sign*1e-7*(directed+directed.transpose(0, 2, 1))
            differences[first:first+len(a)] = delta
        scaled = energy_scale[None] @ differences @ energy_scale.T[None]
        scores = np.linalg.norm(scaled, axis=(1, 2))
        ranking = np.argsort(-scores)
        total = differences.sum(axis=0)
        remainder = full_difference-total
        prefix = np.cumsum(scores[ranking])
        choices = {str(fraction): int(np.searchsorted(prefix, fraction*prefix[-1])+1) for fraction in (.9, .99, .999, .9999)}
        budget_count = int(np.searchsorted(prefix, max(prefix[-1]-5e-5, 0))+1)
        arrays.update(pair_source_cell=rows, pair_observer_cell=columns,
            pair_q3_minus_q2_matrix=differences, baseline_energy_scaling=energy_scale,
            normalized_pair_frobenius=scores, descending_pair_indices=ranking,
            full_q3_minus_q2=full_difference, touching_q3_minus_q2=total,
            nontouching_q3_minus_q2=remainder, baseline_q3_matrix=baseline)
        result = dict(status='COMPLETE_TOUCHING_TWO_RULE_ATTRIBUTION', unordered_touching_pairs=len(rows),
            full_R_coordinate_relative=float(np.linalg.norm(full_difference, 2)/np.linalg.norm(baseline, 2)),
            full_baseline_energy_relative=float(np.linalg.norm(energy_scale @ full_difference @ energy_scale.T, 2)),
            touching_baseline_energy_relative=float(np.linalg.norm(energy_scale @ total @ energy_scale.T, 2)),
            nontouching_baseline_energy_relative=float(np.linalg.norm(energy_scale @ remainder @ energy_scale.T, 2)),
            l1_pair_estimator=float(prefix[-1]), cumulative_fraction_pair_counts=choices,
            pair_count_for_untouched_estimator_l1_below_5e_5=budget_count,
            maximum_pair_estimator=float(scores.max()))
        print(json.dumps(result), flush=True)
    except Exception:
        failure = traceback.format_exc()
        result = dict(status='STOP_TOUCHING_ATTRIBUTION_EXCEPTION')
    artifact = output/'pair-attribution.npz'
    np.savez_compressed(artifact, **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
        pins=PINS, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), failure=failure,
        scope='All unordered current-cell pairs sharing at least one actual mesh vertex. Direct q2/q3 '
        'point integration of the same full affine5lift currents; both directed interactions included. '
        'Signed sum is compared to the frozen full-matrix difference. Baseline-energy normalization '
        'protects weak modes. The empirical two-rule L1 tail is not a continuous-integral error bound, '
        'near correction, field solve or board accuracy claim.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(status=result['status'], elapsed_s=result['elapsed_s'], failure=failure)), flush=True)
    return 0 if failure is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
