"""SPD Decap PI Evaluator v0.23.1: retained14 residual on omitted closed currents."""
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
SOURCE = 'outputs/research/astra-fourteen-current-incremental-action-01'
PINS = {
    SOURCE+'/result.json': 'a88bfe63450ee4f3e47ba2b187b904d2aae007de7946f34df83cacaf006de07d',
    SOURCE+'/fourteen-actions.npz': '382137f5398cb7c1532c12ba264653172d59f90f2baabdc6462b4e25fff3de49',
    'outputs/research/astra-boundary-joint-current-space-01/current-space.npz': '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5',
    'tools/research/probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def run(out):
    started, arrays, checks = monotonic(), {}, {}
    try:
        for path, expected in PINS.items():
            assert digest(ROOT/path) == expected, path
        with np.load(ROOT/SOURCE/'fourteen-actions.npz') as z:
            data = {k: z[k] for k in z.files}
        with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:
            r = csr_matrix((z['resistance_data_ohm'], z['mass_col'], z['mass_row_ptr']), shape=tuple(z['mass_shape']))
            d = csr_matrix((z['volume_b_data'], z['volume_b_col'], z['volume_b_row_ptr']), shape=tuple(z['volume_b_shape']))
            boundary = z['boundary_face_ids']
        q = data['face_currents']; gram = q.T@(r@q)
        assert q.shape == (12546, 14) and np.linalg.norm(gram-np.eye(14), 2) < 1e-9
        active = np.setdiff1d(np.arange(12546), boundary)
        incidence = d[:, active]
        assert np.all(np.diff(incidence.tocsc().indptr) == 2)
        assert np.max(np.abs(np.asarray(incidence.sum(0)))) == 0
        assert connected_components(incidence@incidence.T, directed=False)[0] == 1
        keep = np.arange(5303)
        ri = r[active][:, active]; scale = float(np.median(ri.diagonal()))
        kkt = bmat([[ri/scale, incidence[keep].T], [incidence[keep], None]], format='csc')
        factor = splu(kkt)

        def project(force):
            rhs = np.vstack((force[active]/scale, np.zeros((len(keep), force.shape[1]))))
            solution = factor.solve(rhs)
            error = float(np.linalg.norm(kkt@solution-rhs)/np.linalg.norm(rhs))
            assert error < 1e-10
            value = np.zeros_like(force); value[active] = solution[:len(active)]
            return value, solution[len(active):], error

        qc, dualq, qerr = project(r@q)
        cq = q.T@(r@qc)
        eigenvalues = np.linalg.eigvalsh((cq+cq.T)/2)
        assert eigenvalues.min() > 1e-8 and eigenvalues.max() < 1+1e-10
        arrays.update(face_currents=q, r_gram=gram, closed_projection_of_q=qc,
                      closed_q_dual=dualq, closed_q_gram=cq, active_face_ids=active,
                      retained_divergence_rows=keep)
        checks.update(q_kkt_relative=qerr, closed_q_eigenvalues=eigenvalues.tolist())
        responses = {}
        for name in ('jacobi', 'xg31'):
            action, matrix = data[f'{name}_full_action'], data[f'{name}_matrix']
            closed, dual, error = project(action)
            coefficient = np.linalg.solve(cq, q.T@(r@closed))
            witness = closed-qc@coefficient
            wg = witness.T@(r@witness)
            orth = float(np.linalg.norm(q.T@(r@witness))/np.sqrt(np.trace(wg)))
            divergence = float(np.max(np.abs(d@witness))/np.max(np.abs(witness)))
            identity = float(np.linalg.norm(witness.T@action-wg)/np.linalg.norm(wg))
            assert max(orth, divergence) < 1e-10 and identity < 1e-9
            assert np.max(np.abs(witness[boundary])) == 0
            # Old seven XG columns already generated the seven added closed modes.
            old_cancellation = float(np.linalg.norm(witness[:, :7])/np.linalg.norm(witness[:, 7:]))
            if name == 'xg31':
                assert old_cancellation < 1e-8
            arrays.update({f'{name}_closed_force': closed, f'{name}_force_dual': dual,
                           f'{name}_projection_coefficients': coefficient, f'{name}_witness': witness,
                           f'{name}_witness_gram': wg})
            rows = []
            for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
                omega = 2*np.pi*frequency
                z = gram+1j*omega*matrix+omega**2/299792458.0*data['minus_ik_coefficient']
                forcing = np.c_[np.r_[np.eye(7), np.zeros((7, 7))], data['terminal_flux'].T]
                response = np.linalg.solve(z, forcing)
                assert np.linalg.norm(z@response-forcing)/np.linalg.norm(forcing) < 1e-10
                responses[name, frequency] = response
                row = dict(frequency_hz=frequency, primary_band=frequency <= 1e8)
                for label, sl in (('unit_old_modal', slice(0, 7)), ('four_contact', slice(7, 11))):
                    current = q@response[:, sl]
                    residual = omega*witness@response[:, sl]
                    jnorm = float(np.sqrt(np.trace(current.conj().T@(r@current)).real))
                    residual_norm = float(np.sqrt(np.trace(residual.conj().T@(r@residual)).real))
                    ratio = residual_norm/jnorm
                    # ponytail: only a conditional same-mesh closed-complement bound;
                    # full material/contact/exterior closure needs a larger physical operator.
                    row[label] = dict(current_r_frobenius=jnorm,
                        omitted_force_r_dual_frobenius=residual_norm,
                        residual_over_retained_current=ratio,
                        conditional_relative_current_error_bound=ratio/(1-ratio) if ratio < 1 else None)
                rows.append(row)
                arrays[f'{name}_response_{int(frequency)}'] = response
            checks[name] = dict(kkt_relative=error, r_orthogonality=orth,
                divergence_relative=divergence, riesz_identity_relative=identity,
                old_seven_witness_cancellation=old_cancellation, frequency_diagnostics=rows)
        comparisons = []
        for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
            x, j = responses['xg31', frequency], responses['jacobi', frequency]
            t = data['terminal_flux']
            yx, yj = t@x[:, 7:], t@j[:, 7:]
            comparisons.append(dict(frequency_hz=frequency,
                four_contact_admittance_rule_difference=float(np.linalg.norm(yx-yj)/np.linalg.norm(yx)),
                modal_response_rule_difference=float(np.linalg.norm(x[:, :7]-j[:, :7])/np.linalg.norm(x[:, :7]))))
        checks['two_rule_conditional_response_comparison'] = comparisons
        status, failure = 'COMPLETED_FOURTEEN_CLOSED_RESPONSE_DIAGNOSTIC', None
    except Exception:
        status, failure = 'STOP_FOURTEEN_CLOSED_RESPONSE_DIAGNOSTIC', traceback.format_exc()
    np.savez_compressed(out/'closed-response.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status=status,
        failure=failure, pins=PINS, elapsed_s=monotonic()-started, checks=checks,
        driver_sha256=digest(Path(__file__)), artifact_sha256=digest(out/'closed-response.npz'),
        scope='No new Green/FMM. Closed tests: Dv=0, zero exterior flux, Q14^T Rv=0. '
        'Residual norms are conditional diagnostics in span(Q14)+U, where U contains only '
        'closed fine RT0 currents satisfying Q14^T Rv=0. This is a restricted extension, '
        'not all closed fine RT0 currents or the full source-terminal problem. '
        'Relative bounds assume the assembled operator represents that same real symmetric L '
        'and positive-R problem; they exclude quadrature error, mesh error, scalar/contact charge, '
        'external return, dielectric physics and board validation. No absolute physical accuracy bound.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if failure is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args(); out = args.output.resolve()
    if args.worker:
        raise SystemExit(run(out))
    assert digest(ROOT/'tools/research/probe_astra_fmm3d_runtime.py') == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=120, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]))
