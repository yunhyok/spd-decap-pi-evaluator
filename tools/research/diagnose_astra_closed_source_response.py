"""SPD Decap PI Evaluator v0.23.1: same-mesh closed-current completeness and contact residual."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import bmat, csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

ROOT = Path(__file__).resolve().parents[2]
MESH = 'outputs/research/astra-boundary-joint-current-space-01/current-space.npz'
MESH_SHA = '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def run(source, source_sha, out):
    started = monotonic()
    assert not out.exists() and sha(source) == source_sha and sha(ROOT/MESH) == MESH_SHA
    with np.load(source) as z:
        data = {k: z[k] for k in z.files}
    with np.load(ROOT/MESH) as z:
        r = csr_matrix((z['resistance_data_ohm'], z['mass_col'], z['mass_row_ptr']), shape=tuple(z['mass_shape']))
        d = csr_matrix((z['volume_b_data'], z['volume_b_col'], z['volume_b_row_ptr']), shape=tuple(z['volume_b_shape']))
        bd = z['boundary_face_ids']
    q, t = data['face_currents'], data['terminal_flux']
    n = q.shape[1]; rg = q.T@(r@q)
    assert q.shape[0] == 12546 and np.linalg.eigvalsh(rg).min() > 0
    active = np.setdiff1d(np.arange(12546), bd); incidence = d[:, active]
    assert connected_components(incidence@incidence.T, directed=False)[0] == 1
    assert np.all(np.diff(incidence.tocsc().indptr) == 2) and np.max(np.abs(np.asarray(incidence.sum(0)))) == 0
    keep = np.arange(5303); ri = r[active][:, active]
    scale = float(np.median(ri.diagonal()))
    kkt = bmat([[ri/scale, incidence[keep].T], [incidence[keep], None]], format='csc')
    factor = splu(kkt)

    def project(f):
        rhs = np.r_[f[active]/scale, np.zeros((len(keep), f.shape[1]))]
        sol = factor.solve(rhs)
        error = float(np.linalg.norm(kkt@sol-rhs)/np.linalg.norm(rhs))
        assert error < 1e-10
        x = np.zeros_like(f); x[active] = sol[:len(active)]
        return x, sol[len(active):], error

    qc, qdual, qerr = project(r@q)
    cq = q.T@(r@qc); cqs = (cq+cq.T)/2
    eig, vec = np.linalg.eigh(cqs)
    assert eig.min() > -1e-9 and not np.any((eig > 1e-10) & (eig < 1e-7))
    positive = eig > 1e-7
    inverse = (vec[:, positive]/eig[positive])@vec[:, positive].T
    singular = np.linalg.svd(q[bd], compute_uv=False)
    boundary_rank = int((singular > singular[0]*1e-10).sum())
    closed_retained_rank = n-boundary_rank
    complete = closed_retained_rank == int(positive.sum())
    assert complete, 'Retained closed projection directions still missing; do not claim complete extension'
    arrays = dict(face_currents=q, r_gram=rg, closed_q=qc, closed_q_dual=qdual,
                  closed_q_gram=cq, closed_q_eigenvalues=eig, closed_q_rank_mask=positive,
                  active_face_ids=active, retained_divergence_rows=keep)
    checks = dict(basis_count=n, closed_retained_rank=closed_retained_rank,
        boundary_trace_rank=boundary_rank, closed_projection_rank=int(positive.sum()),
        complete_closed_extension=complete, q_kkt_relative=qerr)
    residuals = {}
    for name in ('jacobi', 'xg31'):
        force, matrix = data[f'{name}_full_action'], data[f'{name}_matrix']
        closed, dual, ferr = project(force)
        coefficient = inverse@(q.T@(r@closed))
        witness = closed-qc@coefficient
        wg = witness.T@(r@witness)
        orth = float(np.linalg.norm(q.T@(r@witness))/np.sqrt(np.trace(wg)))
        div = float(np.max(np.abs(d@witness))/np.max(np.abs(witness)))
        identity = float(np.linalg.norm(witness.T@force-wg)/np.linalg.norm(wg))
        assert orth < 1e-9 and div < 1e-9 and identity < 1e-8
        assert np.max(np.abs(witness[bd])) == 0
        arrays.update({f'{name}_closed_force': closed, f'{name}_force_dual': dual,
                       f'{name}_projection_coefficients': coefficient, f'{name}_witness': witness,
                       f'{name}_witness_gram': wg})
        rows = []
        for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
            omega = 2*np.pi*frequency
            z = rg+1j*omega*matrix+omega**2/299792458.0*data['minus_ik_coefficient']
            u = np.linalg.solve(z, t.T)
            assert np.linalg.norm(z@u-t.T)/np.linalg.norm(t) < 1e-10
            residual = qc@u+1j*omega*closed@u
            restricted = 1j*omega*witness@u
            norm = lambda v: float(np.sqrt(np.trace(v.conj().T@(r@v)).real))
            total = norm(q@u); size = norm(residual)
            replay = norm(residual-restricted)/max(size, 1e-300)
            residual_orth = float(np.linalg.norm(q.T@(r@residual))/max(size, 1e-300))
            # At very low frequency roundoff in canceled closed components dominates.
            assert norm(residual-restricted)/total < 1e-9 and residual_orth*size/total < 1e-9
            ratio = size/total
            rows.append(dict(frequency_hz=frequency, primary_band=frequency <= 1e8,
                closed_residual_over_current=ratio,
                conditional_relative_current_error_bound=ratio/(1-ratio) if ratio < 1 else None,
                all_vs_orthogonal_closed_residual_relative=replay,
                residual_r_orthogonality_relative=residual_orth))
            arrays[f'{name}_response_{int(frequency)}'] = u
            arrays[f'{name}_all_closed_residual_{int(frequency)}'] = residual
            residuals[name, frequency] = residual
        checks[name] = dict(kkt_relative=ferr, r_orthogonality=orth,
            divergence_relative=div, riesz_identity_relative=identity, contact_frequency_diagnostics=rows)
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'closed-source-response.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='COMPLETED_SAME_MESH_CLOSED_SOURCE_RESPONSE',
        pins={str(source.relative_to(ROOT)): source_sha, MESH: MESH_SHA},
        driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'closed-source-response.npz'),
        elapsed_s=monotonic()-started, checks=checks,
        scope='Saved same-mesh R+jwL contact diagnostic including all closed-current extensions. '
        'Dimension and actual residual checks certify only that restricted trial-space decomposition. '
        'Coercive-R residual bounds assume this real symmetric L operator and its exact fine action; '
        'they exclude quadrature, mesh, variable contact-face profiles, exterior/charge/dielectric closure '
        'and the physical board. No new FMM/Green or measured board/current error.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.source_sha, args.output.resolve())
