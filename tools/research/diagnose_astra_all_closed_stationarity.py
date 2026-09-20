"""SPD Decap PI Evaluator v0.23.1: actual contact residual on ALL closed tests."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import csr_matrix

ROOT = Path(__file__).resolve().parents[2]
BASE = 'outputs/research/astra-fourteen-closed-response-01'
ACTION = 'outputs/research/astra-fourteen-current-incremental-action-01'
PINS = {
    BASE+'/result.json': 'fa57c97cbd558207edb1c808a5d0318a070f67a50c85da77e96f59e7d921c838',
    BASE+'/closed-response.npz': 'e4d58d63b91fd5c7a0144c667ff13dbbd74670d4cb26058669615ef6bae3846f',
    ACTION+'/fourteen-actions.npz': '382137f5398cb7c1532c12ba264653172d59f90f2baabdc6462b4e25fff3de49',
    'outputs/research/astra-boundary-joint-current-space-01/current-space.npz': '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5',
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def run():
    started = monotonic()
    out = ROOT/'outputs/research/astra-all-closed-stationarity-01'
    assert not out.exists()
    for path, expected in PINS.items():
        assert digest(ROOT/path) == expected, path
    with np.load(ROOT/BASE/'closed-response.npz') as z:
        saved = {k: z[k] for k in z.files}
    with np.load(ROOT/ACTION/'fourteen-actions.npz') as z:
        terminal = z['terminal_flux']
    with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:
        r = csr_matrix((z['resistance_data_ohm'], z['mass_col'], z['mass_row_ptr']), shape=tuple(z['mass_shape']))
        d = csr_matrix((z['volume_b_data'], z['volume_b_col'], z['volume_b_row_ptr']), shape=tuple(z['volume_b_shape']))
        bd = z['boundary_face_ids']
    q, qc, cq = saved['face_currents'], saved['closed_projection_of_q'], saved['closed_q_gram']
    eig, vec = np.linalg.eigh((cq+cq.T)/2)
    closed_retained = q@vec[:, eig > 1-1e-9]
    assert closed_retained.shape[1] == 11
    assert np.linalg.norm(closed_retained[bd]) < 1e-9
    assert np.linalg.norm(terminal@vec[:, eig > 1-1e-9]) < 1e-8
    arrays, checks = {}, {}
    for name in ('jacobi', 'xg31'):
        rows = []
        for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
            omega = 2*np.pi*frequency
            u = saved[f'{name}_response_{int(frequency)}'][:, 7:]
            # Terminal virtual work and the -ik moment vanish on any closed test.
            # Keep both RQ and LQ; projecting LQ alone misses the contact residual.
            all_closed = qc@u+1j*omega*saved[f'{name}_closed_force']@u
            restricted = 1j*omega*saved[f'{name}_witness']@u
            remainder = all_closed-restricted
            norm = lambda v: float(np.sqrt(np.trace(v.conj().T@(r@v)).real))
            current_norm = norm(q@u)
            relative_div = float(np.max(np.abs(d@all_closed))/np.max(np.abs(all_closed)))
            retained_orth = float(np.linalg.norm(closed_retained.T@(r@all_closed))/norm(all_closed))
            cross = float(abs(np.trace(restricted.conj().T@(r@remainder)))/max(norm(all_closed)**2, 1e-300))
            assert np.max(np.abs(all_closed[bd])) == 0 and relative_div < 1e-8
            assert retained_orth < 1e-7 and cross < 1e-8
            rows.append(dict(frequency_hz=frequency,
                all_closed_residual_over_current=norm(all_closed)/current_norm,
                restricted_residual_over_current=norm(restricted)/current_norm,
                previously_excluded_projection_residual_over_current=norm(remainder)/current_norm,
                retained_closed_stationarity_relative=retained_orth,
                divergence_relative=relative_div, orthogonal_decomposition_cross_relative=cross))
            arrays[f'{name}_all_closed_residual_{int(frequency)}'] = all_closed
        checks[name] = rows
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'all-closed-residual.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='COMPLETED_ALL_CLOSED_CONTACT_STATIONARITY_DIAGNOSTIC', pins=PINS,
        elapsed_s=monotonic()-started, checks=checks,
        retained_closed_dimension=11, omitted_transport_projection_dimension=3,
        driver_sha256=digest(Path(__file__)), artifact_sha256=digest(out/'all-closed-residual.npz'),
        scope='All same-mesh closed tests, including three closed projections omitted by Q14 R-orthogonality. '
        'Actual conditional four-contact residual RQ*u+jwLQ*u is projected; boundary-only contact forcing '
        'vanishes on closed tests. These are stationarity residuals, not actual current or board errors, '
        'nor bounds for the unassembled full contact/charge/exterior/dielectric system. No new FMM/Green.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    run()
