"""SPD Decap PI Evaluator v0.23.1: saved-field affine residual diagnostic; no solve/FMM."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path('C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator')
RUN = ROOT/'outputs/research/astra-finite-joint-p-20260914-01'
BASE = ROOT/'outputs/research/astra-joint-p-arbitrary-source-20260914-01/fixed-current-point-action.npz'
PINS = {
    RUN/'finite-driver-at-run.py': '6f7b89e087fbfbe2ea8dc8f524739942b8d5b3713c65cdcd523d00929d20e719',
    RUN/'finite/raw-finite-magnetic-field-before-gates.npz': '38207bc8c0dbe9d9886dcb61d64ed83b00b7dcc46a76046f88ebc2a93b61e78c',
    BASE: 'b81741ed93848dd687a1e7ea1a5d35b3f7bece5eb209b4d27d5d211de92a2bdd',
}


def pair(z):
    return [float(z.real), float(z.imag)]


def main():
    for p, expected in PINS.items():
        with p.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == expected
    spec = importlib.util.spec_from_file_location('frozen_finite', RUN/'finite-driver-at-run.py')
    d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
    d.pins(strict=True)
    lift, v0, e0, q0 = d.load_lift()
    with np.load(d.PINS['accepted_core'][0], allow_pickle=False) as z:
        y, b, r = (d.csc(z, key+'_') for key in ('y', 'b', 'r'))
    with np.load(BASE, allow_pickle=False) as z:
        f0, h0 = z['force14_h_a_m'], z['flux25_wb']
    with np.load(RUN/'finite/raw-finite-magnetic-field-before-gates.npz', allow_pickle=False) as z:
        v1, q1, e1 = z['active_voltage_v'], z['l25_branch_current_a'], z['eta_l14_v']
        f1, h1 = z['l14_magnetic_force_wb_m'], z['l25_magnetic_flux_linkage_wb']
    dv = 1/np.sqrt(np.maximum(np.asarray(abs(y[1:, 1:]).sum(1)).ravel()+np.asarray(abs(b[1:]).sum(1)).ravel(), 1e-30))
    dq = 1/np.sqrt(np.maximum(np.asarray(abs(b).sum(0)).ravel()+np.asarray(abs(r).sum(1)).ravel(), 1e-30))
    de = dv[lift.active[1:]-1]
    rhs = np.zeros(len(v0), complex); rhs[2699] = 1; rhs[2656] = -1
    norm_rhs = np.linalg.norm(dv*rhs[1:])

    def residual(v, q, e, force, flux):
        ev = v[lift.active][1:]-v[lift.active][0]
        rv = y@v+b@q+lift.f(e-ev, len(v))-rhs
        rq = b.T@v-r@q-2j*np.pi*1e6*flux
        re = lift.ft(v)-lift.rg(e)-2j*np.pi*1e6*lift.wt(force)
        blocks = (dv*rv[1:], dq*rq, de*re)
        return np.concatenate(blocks), [float(np.linalg.norm(x)/norm_rhs) for x in blocks]

    a, blocks0 = residual(v0, q0, e0, f0, h0)
    end, blocks1 = residual(v1, q1, e1, f1, h1)
    delta = end-a
    t = -np.vdot(delta, a)/np.vdot(delta, delta)
    projected = a+t*delta
    assert np.linalg.norm(projected) <= min(np.linalg.norm(a), np.linalg.norm(end))*(1+1e-10)
    assert abs(np.vdot(delta, projected)) <= 1e-10*np.linalg.norm(delta)*np.linalg.norm(a)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='COMPLETED_SAVED_AFFINE_RESIDUAL_DIAGNOSTIC_NOT_A_SOLUTION',
        initial_scaled_residual=float(np.linalg.norm(a)/norm_rhs),
        final_scaled_residual=float(np.linalg.norm(end)/norm_rhs),
        initial_block_scaled_norms=blocks0, final_block_scaled_norms=blocks1,
        affine_optimal_complex_coefficient=pair(t),
        affine_minimum_scaled_residual=float(np.linalg.norm(projected)/norm_rhs),
        residual_reduction_factor=float(np.linalg.norm(a)/np.linalg.norm(end)),
        final_vs_affine_optimum=float(np.linalg.norm(end)/np.linalg.norm(projected)),
        source_pins={str(p): h for p, h in PINS.items()},
        scope='One-dimensional span of saved baseline and failed final iterate under the same linear operator. No new FMM, factors or Krylov solve. No accepted field, error bound or convergence prediction.')
    (RUN/'saved-affine-residual-diagnostic.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (RUN/'saved-affine-residual-driver.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(report))


if __name__ == '__main__':
    main()
