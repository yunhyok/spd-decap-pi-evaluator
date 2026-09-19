"""SPD Decap PI Evaluator v0.23.1: saved-only two-direction verification."""
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/research/astra-l04-10mhz-two-direction-complete-current-01'
PRIOR = ROOT / 'outputs/research/astra-l04-10mhz-closed-current-direction-01'
NV, NX, TOTAL = 3178103, 3782134, 3820411


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    started = perf_counter()
    result = json.loads((OUT / 'result.json').read_bytes())
    guard = json.loads((OUT / 'external-budget.json').read_bytes())
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
    assert guard['driver_sha256'] == digest(OUT / 'driver-at-run.py')
    artifact = Path(result['artifact']['path'])
    assert artifact.parent.resolve() == OUT.resolve()
    assert digest(artifact) == result['artifact']['sha256']
    with np.load(artifact, allow_pickle=False) as z:
        r0, a1, a2, r2, coefficients = (z[k] for k in ('r0', 'adpsi', 'ad2', 'r2', 'coefficients'))
        physical_d2, base_candidate = z['d2_physical'], z['candidate_base_scaled']
    assert r0.shape == a1.shape == a2.shape == r2.shape == (4465281,)
    assert coefficients.shape == (2,) and physical_d2.shape == base_candidate.shape == (TOTAL,)
    assert all(np.isfinite(v).all() for v in (r0, a1, a2, r2, coefficients, physical_d2, base_candidate))
    columns = np.column_stack((a1, a2))
    norms = np.linalg.norm(columns, axis=0)
    assert np.all(norms > 0) and np.linalg.norm(r0) > 0
    normalized = columns / norms
    q, triangular = np.linalg.qr(normalized, mode='reduced')
    qr_coefficients = np.linalg.solve(triangular, q.conj().T @ r0) / norms
    coefficient_relative = float(np.linalg.norm(qr_coefficients-coefficients) / np.linalg.norm(coefficients))
    replay = r0-columns@coefficients
    replay_relative = float(np.linalg.norm(replay-r2) / np.linalg.norm(r0))
    assert coefficient_relative <= 2e-8 and replay_relative <= 2e-8
    rows = {'potential': slice(0,NV), 'l25': slice(NV,NX), 'contact': slice(NX,TOTAL),
            'original_nonclosed': slice(0,TOTAL), 'closed': slice(TOTAL,None), 'full': slice(None)}
    verified, span_minima = {}, {}
    for name, row in rows.items():
        initial, candidate = float(np.linalg.norm(r0[row])), float(np.linalg.norm(replay[row]))
        assert initial > 0
        reported = result['metrics']['norms']
        assert np.isclose(initial, reported['initial_'+name], rtol=2e-12, atol=0)
        assert np.isclose(candidate, reported['candidate_'+name], rtol=2e-12, atol=0)
        verified[name] = {'initial': initial, 'candidate': candidate, 'ratio': candidate/initial}
        block = normalized[row]
        block_norms = np.linalg.norm(block, axis=0)
        block = block[:, block_norms > 0] / block_norms[block_norms > 0]
        best, _, rank, singular = np.linalg.lstsq(block, r0[row], rcond=1e-12)
        minimum = float(np.linalg.norm(r0[row]-block@best))
        span_minima[name] = {'minimum_norm': minimum, 'minimum_ratio': minimum/initial,
                             'rank': int(rank), 'normalized_singular_values': singular.tolist()}
    assert digest(PRIOR/'closed-current-direction-screen-arrays.npz') == '11a36fa73db93d930439839af4d8cb1a0025649bade1901d2f8d7fa2f468615b'
    with np.load(PRIOR/'closed-current-direction-screen-arrays.npz', allow_pickle=False) as z:
        sv = z['scales_sv']
    assert sv.shape == (NV,)
    port = result['raw_z_unvalidated_change_only_from_coeff2_dv']
    def voltage(vector, index):
        assert isinstance(index, int) and -1 <= index < NV
        return 0j if index == -1 else complex(vector[index])
    candidate_z = voltage(sv*base_candidate[:NV], port['positive_index']) - voltage(sv*base_candidate[:NV], port['negative_index'])
    delta_z = coefficients[1]*(voltage(physical_d2[:NV], port['positive_index'])-voltage(physical_d2[:NV], port['negative_index']))
    assert abs(candidate_z-complex(*port['candidate'])) <= 2e-14
    assert abs(delta_z-complex(*port['delta'])) <= 2e-14
    reference_path = ROOT/'docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json'
    assert digest(reference_path) == '761d61334678ceaca0db55bc8c5539bb080ee36363eae33a2c419a1ea5c06430'
    reference = json.loads(reference_path.read_bytes())
    reference_z = complex(*next(p['reference_zdd_ohm'] for p in reference['points'] if p['frequency_hz'] == 1e7))
    report = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_SAVED_TWO_DIRECTION_REPLAY', 'artifact_sha256': digest(artifact),
              'result_sha256': digest(OUT/'result.json'), 'guard_sha256': digest(OUT/'external-budget.json'),
              'coefficient_qr_relative': coefficient_relative, 'residual_replay_relative': replay_relative,
              'verified_blocks': verified, 'separate_block_span_minima': span_minima,
              'candidate_z_scalar': [candidate_z.real, candidate_z.imag],
              'raw_reference_error_unvalidated': float(abs(candidate_z-reference_z)/abs(reference_z)),
              'reference_usage': 'Frozen loaded 10MHz development point, diagnostic only after coefficient fit; no original Touchstone access or calibration.',
              'elapsed_s': perf_counter()-started,
              'scope': 'Saved arrays only; per-block minima are separate necessary bounds, not simultaneous feasibility, a new operator replay, or accuracy acceptance.'}
    (OUT/'hq-saved-two-direction-verification.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report, allow_nan=False))


if __name__ == '__main__':
    main()
