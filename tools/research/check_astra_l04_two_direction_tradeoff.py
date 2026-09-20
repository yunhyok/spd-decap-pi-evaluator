"""SPD Decap PI Evaluator v0.23.1: saved two-column convex tradeoff check."""
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.optimize import brentq

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'outputs/research/astra-l04-10mhz-two-direction-complete-current-01'
PRIOR = ROOT/'outputs/research/astra-l04-10mhz-closed-current-direction-01/result.json'
TOTAL = 3820411


def pinned(path, expected):
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    assert actual == expected, str(path)
    return actual


def main():
    started = perf_counter()
    result_sha = pinned(OUT/'result.json', '3239c406102b43a1ae413190cf40e375b54c27083a66b1e135a0ff9882fbc306')
    pinned(PRIOR, '63ba3212c49e73cdbbb576e1a213d3b89ac24cdfb5f4655469bcc995ac2c0c47')
    result, prior = json.loads((OUT/'result.json').read_bytes()), json.loads(PRIOR.read_bytes())
    artifact = OUT/'two-direction-fit-before-gates.npz'
    artifact_sha = pinned(artifact, '1ca4ee00d75da9fdd0dafb90d0de524a4a70ca81bf65734bc2b3e275758570ac')
    with np.load(artifact, allow_pickle=False) as z:
        r0, columns = z['r0'], np.column_stack((z['adpsi'], z['ad2']))
    assert r0.shape == (4465281,) and columns.shape == (4465281,2)
    column_norms = np.linalg.norm(columns, axis=0)
    normalized = columns/column_norms
    full_cap = .8*prior['metrics']['norms']['optimized_full']
    closed_cap = .8*result['metrics']['norms']['initial_closed']
    af, rf = normalized/full_cap, r0/full_cap
    ac, rc = normalized[TOTAL:]/closed_cap, r0[TOTAL:]/closed_cap
    gf, hf, cf = af.conj().T@af, af.conj().T@rf, float(np.vdot(rf,rf).real)
    gc, hc, cc = ac.conj().T@ac, ac.conj().T@rc, float(np.vdot(rc,rc).real)
    gf, gc = (gf+gf.conj().T)/2, (gc+gc.conj().T)/2
    assert np.linalg.eigvalsh(gf).min() > 0 and np.linalg.eigvalsh(gc).min() > 0
    def solve(weight):
        return np.linalg.solve(gf+weight*gc, hf+weight*hc)
    def closed_constraint(weight):
        x = solve(weight)
        return float(cc-2*np.vdot(x,hc).real+np.vdot(x,gc@x).real-1)
    assert closed_constraint(0) > 0
    high = 1.
    while closed_constraint(high) > 0:
        high *= 10
        assert high <= 1e12
    weight = brentq(closed_constraint, 0, high, xtol=1e-12, rtol=1e-13)
    x = solve(weight)
    coefficients = x/column_norms
    residual = r0-columns@coefficients
    full_norm, closed_norm = float(np.linalg.norm(residual)), float(np.linalg.norm(residual[TOTAL:]))
    objective = (full_norm/full_cap)**2
    dual = float(cf+weight*(cc-1)-np.vdot(hf+weight*hc,x).real)
    stationarity = float(np.linalg.norm((gf+weight*gc)@x-(hf+weight*hc))/np.linalg.norm(hf+weight*hc))
    gap = objective-dual
    assert np.isfinite(residual).all() and weight >= 0 and stationarity <= 2e-10
    assert abs(closed_norm/closed_cap-1) <= 2e-8 and abs(gap) <= 1e-7*max(1,objective)
    report = {'program':'SPD Decap PI Evaluator','version':'0.23.1',
              'status':'SAVED_CONVEX_FULL_CLOSED_TRADEOFF','result_sha256':result_sha,
              'artifact_sha256':artifact_sha,'lagrange_weight':weight,
              'coefficients':[[v.real,v.imag] for v in coefficients],
              'full_cap':full_cap,'closed_cap':closed_cap,'constrained_full_norm':full_norm,
              'constrained_closed_norm':closed_norm,'full_objective_normalized_squared':objective,
              'dual_lower_bound_normalized_squared':dual,'primal_dual_gap':gap,
              'stationarity_relative':stationarity,
              'excludes_simultaneous_full_closed_caps_with_numerical_margin':bool(dual>1+1e-7),
              'elapsed_s':perf_counter()-started,
              'scope':'Floating-point convex dual certificate within the two saved columns only. A necessary pair of original caps; no relaxed gate, new FMM, candidate acceptance, or omitted-physics attribution.'}
    (OUT/'hq-saved-two-direction-tradeoff.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report,allow_nan=False))


if __name__ == '__main__':
    main()
