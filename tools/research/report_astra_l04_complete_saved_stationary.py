"""SPD Decap PI Evaluator v0.23.1: saved complete-current port indicators."""
import json
from time import perf_counter

import numpy as np
import probe_astra_l04_10mhz_two_direction_complete_current as two
from check_astra_l04_two_direction_tradeoff import OUT, PRIOR, pinned


def main():
    started = perf_counter()
    pinned(OUT/'result.json', '3239c406102b43a1ae413190cf40e375b54c27083a66b1e135a0ff9882fbc306')
    pinned(PRIOR, '63ba3212c49e73cdbbb576e1a213d3b89ac24cdfb5f4655469bcc995ac2c0c47')
    for key in ('prior_initial','prior_arrays','magnetic_driver'):
        pinned(*two.PINS[key])
    artifact = OUT/'two-direction-fit-before-gates.npz'
    pinned(artifact, '1ca4ee00d75da9fdd0dafb90d0de524a4a70ca81bf65734bc2b3e275758570ac')
    with np.load(two.PINS['prior_initial'][0], allow_pickle=False) as z:
        baseline, rhs = z['baseline_scaled_state'], z['rhs_scaled']
    with np.load(two.PINS['prior_arrays'][0], allow_pickle=False) as z:
        psi, sh, alpha, psi_residual = z['dpsi_closed_coordinate'],z['scales_sh'],z['alpha'][0],z['r1_scaled']
    with np.load(artifact, allow_pickle=False) as z:
        r0, r2, base2, psi2 = z['r0'], z['r2'], z['candidate_base_scaled'], z['candidate_psi']
    assert baseline.shape == rhs.shape == (3820411,) and sh.shape == (644870,)
    b = np.r_[rhs,np.zeros(len(sh))]
    cases = {'baseline':(np.r_[baseline,np.zeros(len(sh))],r0),
             'psi_only':(np.r_[baseline,alpha*psi/sh],psi_residual),
             'two_direction':(np.r_[base2,psi2/sh],r2)}
    report = {'program':'SPD Decap PI Evaluator','version':'0.23.1','cases':{}}
    for name,(x,r) in cases.items():
        assert x.shape == b.shape == r.shape and np.isfinite(x).all() and np.isfinite(r).all()
        raw = complex(np.dot(b,x))
        correction = complex(np.dot(x,r))
        stationary = two.legacy._stationary(b,x,r)
        assert abs(stationary-raw-correction) <= 2e-14 and np.isfinite(stationary)
        report['cases'][name] = {'rhs_transpose_state':[raw.real,raw.imag],
                                'state_transpose_residual':[correction.real,correction.imag],
                                'stationary_j':[stationary.real,stationary.imag],
                                'scaled_residual_norm':float(np.linalg.norm(r))}
    expected = json.loads((OUT/'result.json').read_bytes())['raw_z_unvalidated_change_only_from_coeff2_dv']
    for name,key in (('baseline','initial'),('two_direction','candidate')):
        assert abs(complex(*report['cases'][name]['rhs_transpose_state'])-complex(*expected[key])) <= 2e-14
    report.update(elapsed_s=perf_counter()-started,scope='Saved complete-current ordinary-transpose indicator only. No new field action, symmetry proof, error bound, physical validation, or PowerSI accuracy acceptance.')
    (OUT/'hq-complete-saved-stationary.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report,allow_nan=False))


if __name__ == '__main__':
    main()
