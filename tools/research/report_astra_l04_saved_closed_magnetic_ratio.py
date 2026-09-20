"""SPD Decap PI Evaluator v0.23.1: one saved closed mode's magnetic/resistive ratio."""
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse

from check_astra_l04_two_direction_saved_fit import digest, TOTAL, PRIOR


def main():
    started = perf_counter()
    path = PRIOR / 'closed-current-direction-screen-arrays.npz'
    expected = '11a36fa73db93d930439839af4d8cb1a0025649bade1901d2f8d7fa2f468615b'
    assert digest(path) == expected
    with np.load(path, allow_pickle=False) as z:
        psi, q, flux = z['dpsi_closed_coordinate'], z['dq04_closed_current_a'], z['f04d_joint_flux_wb']
        h_rhs = -z['r0_scaled'][TOTAL:] / z['scales_sh']
    assert psi.shape == h_rhs.shape == (644870,) and q.shape == flux.shape == (2272974,)
    assert all(np.isfinite(v).all() for v in (psi,q,flux,h_rhs))
    # Saved qualified H solve: H*psi = -r_closed/sh; no new H/FMM operation.
    resistance_power = complex(np.vdot(psi, h_rhs))
    magnetic_quadratic = complex(np.vdot(q, flux))
    assert resistance_power.real > 0 and abs(resistance_power.imag) <= 2e-8 * resistance_power.real
    self_path = Path(__file__).resolve().parents[2] / 'outputs/research/astra-l04-rt0-self-magnetic-01/rt0-self-magnetic.npz'
    self_sha = '20d2cfe73a5170b082cf5378e7e4c18c505f8667d808bb022c74d2828377c65c'
    assert digest(self_path) == self_sha
    with np.load(self_path, allow_pickle=False) as z:
        local = sparse.csc_matrix((z['lself_data'],z['lself_indices'],z['lself_indptr']),shape=tuple(z['lself_shape']))
    assert local.shape == (len(q),len(q))
    local_quadratic = complex(np.vdot(q, local @ q))
    assert local_quadratic.real > 0 and abs(local_quadratic.imag) <= 2e-8 * local_quadratic.real
    omega = 2 * np.pi * 1e7
    report = {'program':'SPD Decap PI Evaluator', 'version':'0.23.1',
              'status':'SAVED_SINGLE_CLOSED_MODE_MAGNETIC_RESISTIVE_DIAGNOSTIC',
              'artifact_sha256':expected, 'frequency_hz':1e7,
              'resistance_power_w':[resistance_power.real,resistance_power.imag],
              'magnetic_quadratic_a_wb':[magnetic_quadratic.real,magnetic_quadratic.imag],
              'self_matrix_sha256':self_sha,
              'self_magnetic_quadratic_a_wb':[local_quadratic.real,local_quadratic.imag],
              'omega_self_over_resistance_magnitude':float(omega*abs(local_quadratic)/abs(resistance_power)),
              'self_over_full_magnetic_magnitude':float(abs(local_quadratic)/abs(magnetic_quadratic)),
              'omega_magnetic_over_resistance_magnitude':float(omega * abs(magnetic_quadratic)/abs(resistance_power)),
              'magnetic_quadratic_imaginary_fraction':float(abs(magnetic_quadratic.imag)/max(abs(magnetic_quadratic),np.finfo(float).tiny)),
              'elapsed_s':perf_counter()-started,
              'scope':'One saved closed direction and one sparse product with the saved local self matrix. No new full field/H/FMM action, spectral bound, PSD proof, convergence cause, preconditioner qualification, or accuracy claim.'}
    target = PRIOR / 'hq-saved-closed-magnetic-ratio.json'
    target.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report,allow_nan=False))


if __name__ == '__main__':
    main()
