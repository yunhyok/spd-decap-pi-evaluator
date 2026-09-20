"""SPD Decap PI Evaluator v0.23.1: local vector-Green retardation remainder bound.

For G_k=exp(-ik r)/r, |G_k-1/r+ik| <= k^2 r/2. On a conductor of
volume V and diameter <= D, Cauchy-Schwarz bounds the bilinear remainder by
1e-7 k^2 D V ||J||_L2 ||K||_L2 / 2. Since R(J,J)=||J||_L2^2/sigma,
the R-whitened vector-Green operator bound is 1e-7 k^2 D V sigma/2.
The retained -ik term is exactly -i k 1e-7 M^T M, M_j=integral J_j.
This bounds only the local retarded-minus-static vector block, for any current
subspace on this owned volume. It says nothing about static quadrature error,
charge/potential blocks, missing exterior conductors or full-field convergence.
"""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry

ROOT = geometry.ROOT
PINS = {
    'tools/research/apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified.py': '3ad0e14937943f4c7941828f1e1bae345b05b5c88b812ddbdb68e69c83ca60d4',
    'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz': '14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
}


def main():
    out = ROOT/'outputs/research/astra-joint-local-retardation-bound-01'
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started = monotonic()
    geometry.verify_inputs()
    for path, expected in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
    joint = geometry.load_joint()
    tets, volume = joint['tetrahedra_m'], joint['cell_volumes_m3']
    sigma, c0 = 59.59e6, 299792458.
    total_volume = float(volume.sum())
    diameter_upper = float(np.linalg.norm(np.ptp(joint['vertices_m'], axis=0)))
    assert len(tets) == 5304 and np.all(volume > 0) and total_volume > 0 and diameter_upper > 0
    with np.load(ROOT/'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz') as d:
        currents = d['whitened_face_currents']
    local = joint['local_face_signs'][:, :, None]*currents[joint['local_face_columns']]
    centered = tets-tets.mean(axis=1)[:, None]
    mass = (np.einsum('cid,cjd->cij', centered, centered)+np.square(centered).sum((1, 2))[:, None, None]/20)/(9*volume[:, None, None])
    r_gram = np.einsum('cim,cij,cjn->mn', local, mass, local)/sigma
    r_error = float(np.linalg.norm(r_gram-np.eye(5), 2))
    assert r_error < 1e-10
    integrated_current = -np.einsum('cim,cid->md', local, centered)/3
    rank_term = 1e-7*integrated_current @ integrated_current.T
    assert np.linalg.norm(rank_term-rank_term.T) < 1e-20
    assert np.linalg.eigvalsh(rank_term).min() >= -1e-14*np.linalg.norm(rank_term, 2)
    # A runnable check of the scalar remainder inequality; the analytic argument
    # in the docstring, rather than these sampled points, provides the bound.
    xs = np.geomspace(1e-10, 2*np.pi*1e9/c0*diameter_upper, 101)
    remainder = -2*np.sin(xs/2)**2+1j*(xs-np.sin(xs))
    ratio = 2*np.abs(remainder)/xs**2
    assert np.max(ratio) <= 1+1e-12
    rows = []
    for f in (1e3, 1e6, 1e7, 1e8, 1e9):
        omega, k = 2*np.pi*f, 2*np.pi*f/c0
        l_bound = 1e-7*k*k*diameter_upper*total_volume*sigma/2
        rows.append(dict(frequency_hz=f, primary_band=f <= 1e8, k_diameter_upper=k*diameter_upper,
                         r_metric_L_remainder_bound_h_per_ohm=l_bound,
                         r_metric_RL_operator_remainder_bound=omega*l_bound,
                         meets_5e_minus5_local_operator_budget=bool(omega*l_bound < 5e-5)))
    assert all(r['meets_5e_minus5_local_operator_budget'] for r in rows if r['primary_band'])
    np.savez_compressed(out/'local-retardation.npz', exact_integrated_current=integrated_current,
                        minus_ik_coefficient_h_m_per_ohm=rank_term, verified_r_gram=r_gram,
                        sampled_x=xs, sampled_scalar_remainder_ratio=ratio)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='BOUNDED_LOCAL_VECTOR_RETARDATION_PRIMARY_BAND', elapsed_s=monotonic()-started,
                  driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
                  artifact_sha256=sha256((out/'local-retardation.npz').read_bytes()).hexdigest(),
                  volume_m3=total_volume, diameter_upper_m=diameter_upper, conductivity_s_per_m=sigma,
                  r_whitening_relative=r_error, rows=rows,
                  approximation='L(k)=L(0)-i*k*Q+E; Q=1e-7*(integral J)^T*(integral J). j*w times the retained -i*k term is a positive semidefinite real contribution.',
                  scope='Analytic local vector-block remainder bound for any R-normalized current subspace on the single-owned homogeneous Cu joint volume. '
                  'The five saved modes only instantiate its exact rank<=3 term. Static integration, physical current completeness, charge, dielectric, '
                  'other conductor interactions, field/port solve and board accuracy are not covered. 1GHz fails the same local budget.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
