"""SPD Decap PI Evaluator v0.23.1: saved-geometry FMM precision/cost discriminator."""
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic
import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as fmm


def main():
    root = Path(__file__).resolve().parents[2]
    out = root/'outputs/research/astra-mixed-fmm-precision-cost-01'
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    source = root/'outputs/research/astra-joint-mixed-point-correction-01/mixed-point-action.npz'
    pin = '3a2f6d5a77d2235474907069ef765b4ba30daeb2842a43c5daa38e8de2184a73'
    assert sha256(source.read_bytes()).hexdigest() == pin
    fmm.verify_inputs()
    with np.load(source, allow_pickle=False) as d:
        points = d['source_points_m']
        charge = d['source_weighted_channels']
        targets = d['target_points_m']
        reference = d['point_action']
    fields = dict(reference_eps1e12=reference)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='RUNNING_SINGLE_FREQUENCY_FMM_PRECISION_COST',
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), source_artifact_sha256=pin,
        frequency_hz=1e8, source_points=len(points), target_points=len(targets),
        omp_num_threads=os.environ.get('OMP_NUM_THREADS'), cases=[])
    for eps in (1e-8, 1e-10):
        before = monotonic()
        answer = fmm.fmm3dpy.hfmm3d(eps=eps, zk=2*np.pi*1e8/fmm.C0,
            sources=np.asfortranarray(points.T), charges=np.asfortranarray(charge.conj().T),
            targets=np.asfortranarray(targets.T), pgt=1, nd=charge.shape[1])
        elapsed = monotonic()-before
        assert answer.ier == 0 and np.isfinite(answer.pottarg).all()
        value = 4*np.pi*np.asarray(answer.pottarg).conj().T
        errors = np.linalg.norm(value-reference, axis=0)/np.linalg.norm(reference, axis=0)
        result['cases'].append(dict(eps=eps, elapsed_s=elapsed, relative_error_by_channel=errors.tolist(),
            accepted_at_1e7_matched_point_gate=bool(errors.max() < 1e-7)))
        fields['eps_'+str(eps)] = value
        np.savez_compressed(out/'fields.npz', **fields)
        (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps(result['cases'][-1]), flush=True)
    result['status'] = 'COMPLETED_SINGLE_FREQUENCY_FMM_PRECISION_COST'
    result['fields_sha256'] = sha256((out/'fields.npz').read_bytes()).hexdigest()
    result['scope'] = ('Same178092 complex source densities and208 targets at100MHz; comparison to frozen '
        'eps1e-12 point action only. OMP4 differs from its OMP1 baseline, so timings do not isolate '
        'precision alone. Does not qualify tiny low-frequency imaginary moments, another geometry '
        'or all-row solver accuracy, and does not change the accepted helper default.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
