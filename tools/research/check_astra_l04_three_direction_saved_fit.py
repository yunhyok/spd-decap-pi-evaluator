"""SPD Decap PI Evaluator v0.23.1: independent saved three-column replay."""
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from check_astra_l04_two_direction_saved_fit import digest, NV, NX, TOTAL, PRIOR
from probe_astra_l04_10mhz_two_direction_complete_current import legacy

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'outputs/research/astra-l04-10mhz-three-direction-complete-current-01'


def main():
    started = perf_counter()
    result, guard = (json.loads((OUT/name).read_bytes()) for name in ('result.json','external-budget.json'))
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
    assert guard['driver_sha256'] == digest(OUT/'driver-at-run.py')
    artifact = OUT/'three-direction-fit-before-gates.npz'
    assert digest(artifact) == result['artifact']['sha256']
    with np.load(artifact, allow_pickle=False) as z:
        r0, r3, coefficients = z['r0'], z['r3'], z['coefficients']
        columns = np.column_stack((z['adpsi'],z['ad2'],z['ad3']))
        base, psi = z['candidate_base_scaled'],z['candidate_psi']
    assert r0.shape == r3.shape == (4465281,) and columns.shape == (4465281,3)
    assert coefficients.shape == (3,) and base.shape == (TOTAL,) and psi.shape == (644870,)
    assert all(np.isfinite(v).all() for v in (r0,r3,columns,coefficients,base,psi))
    norms = np.linalg.norm(columns,axis=0)
    assert np.all(norms > 0)
    q,r = np.linalg.qr(columns/norms,mode='reduced')
    qr_coefficients = np.linalg.solve(r,q.conj().T@r0)/norms
    coefficient_relative = float(np.linalg.norm(qr_coefficients-coefficients)/np.linalg.norm(coefficients))
    replay = r0-columns@coefficients
    replay_relative = float(np.linalg.norm(replay-r3)/np.linalg.norm(r0))
    assert coefficient_relative <= 2e-8 and replay_relative <= 2e-8
    rows = {'potential':slice(0,NV),'l25':slice(NV,NX),'contact':slice(NX,TOTAL),
            'original_nonclosed':slice(0,TOTAL),'closed':slice(TOTAL,None),'full':slice(None)}
    metrics = {}
    for name,row in rows.items():
        initial, candidate = float(np.linalg.norm(r0[row])),float(np.linalg.norm(replay[row]))
        assert initial > 0
        assert np.isclose(initial,result['metrics']['norms']['initial_'+name],rtol=2e-12,atol=0)
        assert np.isclose(candidate,result['metrics']['norms']['candidate_'+name],rtol=2e-12,atol=0)
        metrics[name] = {'initial':initial,'candidate':candidate,'ratio':candidate/initial}
    assert digest(PRIOR/'closed-current-direction-screen-arrays.npz') == '11a36fa73db93d930439839af4d8cb1a0025649bade1901d2f8d7fa2f468615b'
    assert digest(PRIOR/'initial-complete-model-residual.npz') == '16fd56e355f950b6ac5127a979758d4135343713d9a5b432fa2d2c57b664dd1b'
    with np.load(PRIOR/'closed-current-direction-screen-arrays.npz',allow_pickle=False) as z:
        sh = z['scales_sh']
    with np.load(PRIOR/'initial-complete-model-residual.npz',allow_pickle=False) as z:
        rhs = z['rhs_scaled']
    assert rhs.shape == base.shape and sh.shape == psi.shape and np.all(sh>0)
    x,b = np.r_[base,psi/sh],np.r_[rhs,np.zeros(len(sh))]
    raw, stationary = complex(np.dot(b,x)),legacy._stationary(b,x,r3)
    assert abs(raw-complex(*result['raw_z_unvalidated_change_only_from_c2_c3']['candidate'])) <= 2e-14
    reference_path = ROOT/'docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json'
    assert digest(reference_path) == '761d61334678ceaca0db55bc8c5539bb080ee36363eae33a2c419a1ea5c06430'
    reference = complex(*next(p['reference_zdd_ohm'] for p in json.loads(reference_path.read_bytes())['points'] if p['frequency_hz']==1e7))
    report = {'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'PASS_SAVED_THREE_DIRECTION_REPLAY',
              'result_sha256':digest(OUT/'result.json'),'guard_sha256':digest(OUT/'external-budget.json'),
              'artifact_sha256':result['artifact']['sha256'],'coefficient_qr_relative':coefficient_relative,
              'residual_replay_relative':replay_relative,'verified_blocks':metrics,
              'raw_z_unvalidated':[raw.real,raw.imag],'stationary_j_unvalidated':[stationary.real,stationary.imag],
              'raw_reference_error_unvalidated':float(abs(raw-reference)/abs(reference)),
              'elapsed_s':perf_counter()-started,
              'scope':'Saved arrays only; inherited info1 remains. No new field action, error bound, symmetry proof, physical acceptance, original Touchstone access or reference calibration.'}
    (OUT/'hq-saved-three-direction-verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report,allow_nan=False))


if __name__ == '__main__':
    main()
