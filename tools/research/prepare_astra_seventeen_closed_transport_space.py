"""SPD Decap PI Evaluator v0.23.1: retain14 and restore three closed transport projections."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import csr_matrix
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz': '382137f5398cb7c1532c12ba264653172d59f90f2baabdc6462b4e25fff3de49',
    'outputs/research/astra-fourteen-closed-response-01/closed-response.npz': 'e4d58d63b91fd5c7a0144c667ff13dbbd74670d4cb26058669615ef6bae3846f',
    'outputs/research/astra-boundary-joint-current-space-01/current-space.npz': '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5',
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def run():
    started = monotonic()
    out = ROOT/'outputs/research/astra-seventeen-closed-transport-space-01'
    assert not out.exists()
    geometry.verify_inputs()
    for path, expected in PINS.items():
        assert digest(ROOT/path) == expected, path
    with np.load(ROOT/'outputs/research/astra-fourteen-closed-response-01/closed-response.npz') as z:
        q, qc, cq = z['face_currents'], z['closed_projection_of_q'], z['closed_q_gram']
    with np.load(ROOT/'outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz') as z:
        assert np.array_equal(q, z['face_currents'])
        terminal = np.c_[z['terminal_flux'], np.zeros((4, 3))]
        actions = {n: z[f'{n}_full_action'] for n in ('jacobi', 'xg31')}
    with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:
        r = csr_matrix((z['resistance_data_ohm'], z['mass_col'], z['mass_row_ptr']), shape=tuple(z['mass_shape']))
        d = csr_matrix((z['volume_b_data'], z['volume_b_col'], z['volume_b_row_ptr']), shape=tuple(z['volume_b_shape']))
        bd = z['boundary_face_ids']
    eig, vec = np.linalg.eigh((cq+cq.T)/2)
    selected = (eig > 1e-8) & (eig < 1e-3)
    assert selected.sum() == 3 and np.all(eig[~selected] > 1-1e-9)
    raw = qc@vec[:, selected]
    raw_gram = raw.T@(r@raw)
    normalizer = np.linalg.inv(np.linalg.cholesky((raw_gram+raw_gram.T)/2).T)
    new = raw@normalizer
    assert np.linalg.norm(new.T@(r@new)-np.eye(3), 2) < 1e-9
    assert np.max(np.abs(new[bd])) == 0
    divergence = float(np.max(np.abs(d@new))/np.max(np.abs(new)))
    assert divergence < 1e-10
    closed_old = q@vec[:, ~selected]
    old_closed_cross = float(np.linalg.norm(closed_old.T@(r@new)))
    assert old_closed_cross < 1e-8
    basis = np.c_[q, new]
    gram = basis.T@(r@basis)
    gram_eigenvalues = np.linalg.eigvalsh((gram+gram.T)/2)
    assert gram_eigenvalues.min() > 0.99 and gram_eigenvalues.max() < 1.01
    # Retain the real R cross block to the three transport currents.
    assert np.linalg.norm(gram[:14, 14:]) > 1e-4
    projection17 = np.c_[qc, new]
    closed_gram17 = basis.T@(r@projection17)
    closed_spectrum = np.linalg.eigvalsh((closed_gram17+closed_gram17.T)/2)
    assert (closed_spectrum > 1e-8).sum() == 14
    boundary_singular = np.linalg.svd(basis[bd], compute_uv=False)
    assert (boundary_singular > boundary_singular[0]*1e-10).sum() == 3
    joint = geometry.load_joint(); tets = joint['tetrahedra_m']
    local = joint['local_face_signs'][:, :, None]*new[joint['local_face_columns']]
    cell_moment = np.einsum('cin,cid->cnd', local, tets.mean(1)[:, None]-tets)/3
    moment_relative = float(np.linalg.norm(cell_moment.sum(0))/np.linalg.norm(np.linalg.norm(cell_moment, axis=2).sum(0)))
    assert moment_relative < 1e-9
    arrays = dict(face_currents=basis, resistance_gram=gram, terminal_flux=terminal,
        selected_closed_q_eigenvalues=eig[selected], selected_directions=vec[:, selected],
        raw_closed_projection=raw, raw_r_gram=raw_gram, normalizer=normalizer,
        closed_projection_of_seventeen=projection17, closed_projection_gram=closed_gram17,
        new_cell_integrated_current=cell_moment, new_integrated_current=cell_moment.sum(0))
    for name, action in actions.items():
        arrays[f'{name}_known_new_old_magnetic_cross'] = new.T@action
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'seventeen-current-space.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PREPARED_SEVENTEEN_CLOSED_TRANSPORT_SPACE_NEW_MAGNETIC_COLUMNS_PENDING',
        pins=PINS, elapsed_s=monotonic()-started, driver_sha256=digest(Path(__file__)),
        artifact_sha256=digest(out/'seventeen-current-space.npz'),
        checks=dict(new_closed_divergence_relative=divergence, new_old_closed_r_cross=old_closed_cross,
            physical_r_eigenvalues=gram_eigenvalues.tolist(),
            retained_transport_r_cross_norm=float(np.linalg.norm(gram[:14, 14:])),
            new_closed_integrated_current_relative=moment_relative,
            total_dimension=17, retained_closed_dimension=14, boundary_trace_rank=3,
            closed_projection_rank=14),
        scope='Old14 exact; adds three normalized closed projections of transport directions. '
        'Their nonzero physical R cross terms remain. Dimension/rank tests show the R-orthogonal '
        'closed complement now completes span(Q17)+all same-mesh closed currents. '
        'No new magnetic columns or local response yet, no mesh/charge/exterior/board accuracy claim.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    run()
