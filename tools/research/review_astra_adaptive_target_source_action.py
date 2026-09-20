"""SPD Decap PI Evaluator v0.23.1: direct source checks at new adaptive targets."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
import qualify_astra_joint_mixed_point_correction as mixed
from qualify_astra_joint_complete_static_rows import complete_potentials
from review_astra_joint_mixed_point_correction import regular_reference


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/qualify_astra_joint_mixed_point_correction.py': 'a498bbde49974620abb81e6ace23fb566c0ac022f98bbdb4dde21fef7147c82b',
    'tools/research/qualify_astra_joint_complete_static_rows.py': '84d47b60d3aca399fde4fdbc46a669207118747eef4525d4bcfd2db24277781e',
    'tools/research/review_astra_joint_mixed_point_correction.py': '70e8dca6c7f5066b896089cfcaae910f6525268d14da38902604cade1e4b4401',
    'outputs/research/astra-joint-adaptive-outer-action-01/adaptive-action.npz': 'c16ba79d8033fe6fe4c197fa3bca362ca2c6d990d0da93c9b8a5231b758a2bf5',
    'outputs/research/astra-outer-static-touching-replacement-02/self-replacement.npz': 'bcf2202698cefd0c256a3cd20152259ccc1bd50d602ec8bb6d4527e2914a1655',
}


def run():
    start = monotonic()
    output = ROOT/'outputs/research/astra-adaptive-target-source-review-01'
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
    with np.load(ROOT/list(PINS)[-2], allow_pickle=False) as d:
        saved = {key: d[key] for key in d.files}
    with np.load(ROOT/list(PINS)[-1], allow_pickle=False) as d:
        priority = d['normalized_remaining_patch_error'].max(axis=1)
    selected = saved['selected_patch_ids']
    records = np.argsort(-priority[selected], kind='stable')[:12]
    # Highest remaining patch errors, with a deterministic interior quadrature point each.
    target_ids = (saved['record_first'][records]+saved['record_last'][records]-1)//2
    targets = saved['target_points_m'][target_ids]
    source = mixed.prepare_sources(3)
    nc = source['nc']
    vector, scalar = complete_potentials(source['entities'][:nc], source['entities'][nc:],
        source['current_centers'], source['current_radial'], source['charge'], targets, start+120)
    analytic_static = np.column_stack((vector.reshape(len(targets), 6), scalar))
    regular = regular_reference(source['points'], source['channels'], targets, 1e8)
    reference = analytic_static+regular
    source_error = np.linalg.norm(saved['corrected_action'][target_ids]-reference, axis=0)/np.linalg.norm(reference, axis=0)
    k = 2*np.pi*1e8/mixed.fmm.C0
    threshold = float(np.ptp(np.vstack((source['points'], saved['target_points_m'])), axis=0).max())*np.finfo(float).eps
    direct = np.zeros_like(reference)
    coincident = np.zeros_like(reference)
    for first in range(0, len(source['points']), 2048):
        distance = np.linalg.norm(targets[:, None]-source['points'][None, first:first+2048], axis=2)
        mask = distance > threshold
        kernel = np.divide(np.exp(-1j*k*distance), distance, out=np.zeros(distance.shape, complex), where=mask)
        direct += kernel @ source['channels'][first:first+2048]
        coincident += (~mask) @ source['channels'][first:first+2048]
    point_error = np.linalg.norm(saved['point_action'][target_ids]-direct, axis=0)/np.linalg.norm(direct, axis=0)
    assert np.array_equal(coincident, saved['coincident_weighted_density'][target_ids])
    algebra = saved['point_action']+saved['static_correction']-1j*k*saved['coincident_weighted_density']
    algebra_error = float(np.linalg.norm(algebra-saved['corrected_action'])/np.linalg.norm(algebra))
    assert algebra_error < 2e-15
    accepted = bool(source_error.max() < 5e-5 and point_error.max() < 5e-11)
    artifact = output/'direct-target-checks.npz'
    np.savez_compressed(artifact, target_ids=target_ids, patch_ids=selected[records], target_points_m=targets,
        complete_analytic_static=analytic_static, direct_regular=regular, complete_reference=reference,
        direct_point_action=direct, actual_mixed_action=saved['corrected_action'][target_ids],
        actual_point_action=saved['point_action'][target_ids], mixed_error_by_channel=source_error,
        point_error_by_channel=point_error)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_SAMPLED_NEW_ADAPTIVE_SOURCE_ACTION' if accepted else 'STOP_SAMPLED_ADAPTIVE_SOURCE_GATE',
        elapsed_s=monotonic()-start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), target_count=len(targets),
        target_ids=target_ids.tolist(), patch_ids=selected[records].tolist(),
        mixed_error_by_channel=source_error.tolist(), direct_point_error_by_channel=point_error.tolist(),
        complete_saved_algebra_relative=algebra_error, frequency_hz=1e8,
        scope='Twelve new quadrature targets from the largest remaining outer-error patches. '
        'Full5304 affine-current and9180 scalar supports are integrated analytically for static reference; '
        'the regular remainder and point kernel are summed directly on the original178092 sources. '
        'No new FMM, pair integration, mesh or field solve. This independently checks accelerated source '
        'accuracy at these targets only; it is not an all-target, retarded quadrature or board guarantee.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result))
    return 0 if accepted else 2


if __name__ == '__main__':
    raise SystemExit(run())
