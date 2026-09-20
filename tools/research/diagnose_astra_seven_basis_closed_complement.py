"""SPD Decap PI Evaluator v0.23.1: omitted closed-current forcing from saved L@Q."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import bmat, csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

ROOT = Path(__file__).resolve().parents[2]
ACTION = 'outputs/research/astra-seven-basis-full-action-01'
PINS = {
    'tools/research/probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
    'tools/research/probe_astra_seven_basis_full_action.py': 'cee40c743a43ecd25ca80532e30ad2f2a7edfa121d5484d9183990a2f83e92ca',
    'outputs/research/astra-boundary-joint-current-space-01/current-space.npz': '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5',
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def run(out, result_sha, artifact_sha):
    # Receipt hashes are explicit launch inputs, checked before reading arrays.
    pins = {**PINS, ACTION+'/result.json': result_sha, ACTION+'/full-actions.npz': artifact_sha}
    assert out.is_dir() and not (out/'result.json').exists()
    started, arrays, checks = monotonic(), {}, {}
    try:
        for name, expected in pins.items():
            assert len(expected) == 64 and digest(ROOT/name) == expected, name
        source_receipt = json.loads((ROOT/ACTION/'result.json').read_text())
        assert source_receipt['failure'] is None
        with np.load(ROOT/ACTION/'full-actions.npz') as z:
            q = z['face_currents']
            actions = {name: z[f'{name}_full_action'] for name in ('jacobi', 'xg31')}
            matrices = {name: z[f'{name}_matrix'] for name in actions}
        with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:
            r = csr_matrix((z['resistance_data_ohm'], z['mass_col'], z['mass_row_ptr']), shape=tuple(z['mass_shape']))
            d = csr_matrix((z['volume_b_data'], z['volume_b_col'], z['volume_b_row_ptr']), shape=tuple(z['volume_b_shape']))
            boundary = z['boundary_face_ids']
        assert q.shape == (12546, 7) and len(boundary) == 3876 and d.shape == (5304, 12546)
        assert np.linalg.norm(q.T@(r@q)-np.eye(7), 2) < 1e-10
        active = np.setdiff1d(np.arange(12546), boundary)
        incidence = d[:, active]
        assert np.all(np.diff(incidence.tocsc().indptr) == 2) and np.max(np.abs(np.asarray(incidence.sum(0)))) == 0
        components, labels = connected_components(incidence@incidence.T, directed=False)
        assert components == 1
        # A closed connected cell graph has one redundant divergence row.
        keep = np.arange(5303)
        a = incidence[keep]
        ri = r[active][:, active].tocsc()
        scale = float(np.median(ri.diagonal()))
        kkt = bmat([[ri/scale, a.T], [a, None]], format='csc')
        factor = splu(kkt)

        def project_force(force):
            rhs = np.vstack((force[active]/scale, np.zeros((len(keep), force.shape[1]))))
            solution = factor.solve(rhs)
            residual = float(np.linalg.norm(kkt@solution-rhs)/np.linalg.norm(rhs))
            value = np.zeros_like(force)
            value[active] = solution[:len(active)]
            assert residual < 1e-10
            assert np.max(np.abs(d@value))/max(np.max(np.abs(value)), 1e-300) < 1e-10
            return value, solution[len(active):], residual

        closed_q, q_dual, q_residual = project_force(r@q)
        overlap = q.T@(r@closed_q)
        assert np.linalg.norm(overlap-overlap.T)/np.linalg.norm(overlap) < 1e-10
        values, vectors = np.linalg.eigh((overlap+overlap.T)/2)
        assert values.min() > -1e-10 and values.max() < 1+1e-10
        retained = values > 1e-10
        # ponytail: rank uses a separated numerical spectrum; stop on ambiguous eigenvalues.
        assert not np.any((values > 1e-12) & (values < 1e-8)), values
        inverse = (vectors[:, retained]/values[retained])@vectors[:, retained].T
        arrays.update(face_currents=q, closed_projection_of_q=closed_q, closed_q_dual=q_dual,
                      closed_q_gram=overlap, closed_q_eigenvalues=values,
                      closed_q_rank_mask=retained, active_face_ids=active, retained_divergence_rows=keep)
        checks['q_projection_kkt_relative'] = q_residual
        checks['closed_q_eigenvalues'] = values.tolist()
        checks['closed_q_rank'] = int(retained.sum())
        witnesses = {}
        for name, force in actions.items():
            closed_force, dual, residual = project_force(force)
            coeff = inverse@(q.T@(r@closed_force))
            witness = closed_force-closed_q@coeff
            witness_gram = witness.T@(r@witness)
            coupling = witness.T@force
            orthogonality = float(np.linalg.norm(q.T@(r@witness))/max(np.sqrt(np.trace(witness_gram)), 1e-300))
            identity = float(np.linalg.norm(coupling-witness_gram)/np.linalg.norm(witness_gram))
            divergence = float(np.max(np.abs(d@witness))/np.max(np.abs(witness)))
            assert orthogonality < 1e-10 and identity < 1e-9 and divergence < 1e-10
            assert np.max(np.abs(witness[boundary])) == 0
            spectrum, directions = np.linalg.eigh((witness_gram+witness_gram.T)/2)
            assert spectrum.min() > 0
            normalized = witness@(directions/np.sqrt(spectrum))
            assert np.linalg.norm(normalized.T@(r@normalized)-np.eye(7), 2) < 1e-8
            witnesses[name] = witness
            arrays.update({f'{name}_closed_force': closed_force, f'{name}_force_dual': dual,
                           f'{name}_projection_coefficients': coeff, f'{name}_witness': witness,
                           f'{name}_witness_gram': witness_gram, f'{name}_normalized_witness': normalized,
                           f'{name}_witness_coupling': coupling, f'{name}_witness_eigenvalues': spectrum})
            # Unit modal forcing sensitivity of R+jwL7; no scalar/contact/board solve.
            sensitivity = []
            for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
                omega = 2*np.pi*frequency
                response = np.linalg.solve(np.eye(7)+1j*omega*matrices[name], np.eye(7))
                gram_response = omega**2*response.conj().T@witness_gram@response
                sensitivity.append(dict(frequency_hz=frequency, omitted_closed_force_r_dual_norm=float(np.sqrt(np.linalg.norm(gram_response, 2)))))
            checks[name] = dict(kkt_relative=residual, r_orthogonality=orthogonality,
                                riesz_energy_identity_relative=identity, divergence_relative=divergence,
                                witness_eigenvalues=spectrum.tolist(), unit_modal_RL_sensitivity=sensitivity)
        delta = witnesses['xg31']-witnesses['jacobi']
        delta_gram = delta.T@(r@delta)
        base = arrays['xg31_witness_gram']
        metric = np.linalg.inv(np.linalg.cholesky((base+base.T)/2))
        difference = float(np.sqrt(np.linalg.norm(metric@delta_gram@metric.T, 2)))
        arrays.update(witness_difference=delta, witness_difference_gram=delta_gram)
        checks['relative_worst_combination_two_rule_witness_difference'] = difference
        status, failure = 'COMPLETED_SEVEN_BASIS_CLOSED_COMPLEMENT_DIAGNOSTIC', None
    except Exception:
        status, failure = 'STOP_SEVEN_BASIS_CLOSED_COMPLEMENT_DIAGNOSTIC', traceback.format_exc()
    np.savez_compressed(out/'closed-complement.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status=status, failure=failure,
                  pins=pins, driver_sha256=digest(Path(__file__)), artifact_sha256=digest(out/'closed-complement.npz'),
                  elapsed_s=monotonic()-started, checks=checks,
                  scope='Saved-action diagnostic. Closed test currents have Dv=0, zero all exterior flux, and Q7^T Rv=0. '
                  'These homogeneous restrictions define omitted test witnesses, not physical insulation. '
                  'Witnesses are Riesz responses to static LQ forcing, not an AC current solution. '
                  'Unit-modal R+jwL7 sensitivity omits scalar/contact/exterior/material/return physics. '
                  'No new Green integrals, FMM, current-space convergence, field/port/board claim.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if failure is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--result-sha', required=True)
    parser.add_argument('--artifact-sha', required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.worker:
        raise SystemExit(run(out, args.result_sha, args.artifact_sha))
    assert digest(ROOT/'tools/research/probe_astra_fmm3d_runtime.py') == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=120, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out),
        '--result-sha', args.result_sha, '--artifact-sha', args.artifact_sha]))
