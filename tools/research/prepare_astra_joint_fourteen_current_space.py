"""SPD Decap PI Evaluator v0.23.1: retained7 plus corrected closed-current witnesses."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic
import traceback

import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
import numpy as np
from scipy.sparse import bmat, csr_matrix
from scipy.sparse.linalg import splu

ROOT = geometry.ROOT
PINS = {
    'tools/research/probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
    'outputs/research/astra-seven-touch-group-correction-01/result.json': 'a3f465db16a86db24eb4c0d8ed0df1c4e8e964a3b498c169bf2dbe1a2450c3e6',
    'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz': 'a58b0943bdef0baccb6669882d33e500bbb5edb668113d17858bff5e7c5f7c0a',
    'outputs/research/astra-seven-basis-closed-complement-01/closed-complement.npz': '74564971f04e60be40f916c64e91f94003fdff8600d5521afbfd968c2a6d37df',
    'outputs/research/astra-seven-basis-closed-complement-review-01/independent-review.json': 'cae4b04dbc2a73dae155a06871c273bf21e4eccfa4984e33a23192786be9d277',
}


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def run(out):
    started, arrays, checks = monotonic(), {}, {}
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha(ROOT/path) == expected, path
        with np.load(ROOT/'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz') as z:
            q = z['face_currents']
            actions = {name: z[f'{name}_full_action'] for name in ('jacobi', 'xg31')}
            matrices = {name: z[f'{name}_matrix'] for name in actions}
        with np.load(ROOT/'outputs/research/astra-seven-basis-closed-complement-01/closed-complement.npz') as z:
            assert np.array_equal(q, z['face_currents'])
            qc = z['closed_projection_of_q']
            active, keep = z['active_face_ids'], z['retained_divergence_rows']
        with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:
            r = csr_matrix((z['resistance_data_ohm'], z['mass_col'], z['mass_row_ptr']), shape=tuple(z['mass_shape']))
            d = csr_matrix((z['volume_b_data'], z['volume_b_col'], z['volume_b_row_ptr']), shape=tuple(z['volume_b_shape']))
            boundary = z['boundary_face_ids']
        assert np.array_equal(active, np.setdiff1d(np.arange(12546), boundary))
        assert np.array_equal(keep, np.arange(5303))
        ri, di = r[active][:, active], d[keep][:, active]
        scale = float(np.median(ri.diagonal()))
        kkt = bmat([[ri/scale, di.T], [di, None]], format='csc')
        factor = splu(kkt)
        cq = q.T@(r@qc)
        spectrum, directions = np.linalg.eigh((cq+cq.T)/2)
        assert spectrum.min() > 1e-8 and spectrum.max() < 1+1e-10
        inverse = (directions/spectrum)@directions.T
        witnesses = {}
        for name, force in actions.items():
            rhs = np.vstack((force[active]/scale, np.zeros((len(keep), 7))))
            solution = factor.solve(rhs)
            residual = float(np.linalg.norm(kkt@solution-rhs)/np.linalg.norm(rhs))
            assert residual < 1e-10
            closed = np.zeros((12546, 7)); closed[active] = solution[:len(active)]
            coefficient = inverse@(q.T@(r@closed))
            witness = closed-qc@coefficient
            gram = witness.T@(r@witness)
            identity = float(np.linalg.norm(witness.T@force-gram)/np.linalg.norm(gram))
            orth = float(np.linalg.norm(q.T@(r@witness))/np.sqrt(np.trace(gram)))
            divergence = float(np.max(np.abs(d@witness))/np.max(np.abs(witness)))
            assert identity < 1e-9 and orth < 1e-10 and divergence < 1e-10
            assert np.max(np.abs(witness[boundary])) == 0
            normalizer = np.linalg.inv(np.linalg.cholesky((gram+gram.T)/2).T)
            normalized = witness@normalizer
            assert np.linalg.norm(normalized.T@(r@normalized)-np.eye(7), 2) < 1e-8
            witnesses[name] = witness
            rows = []
            for frequency in (1e3, 1e6, 1e7, 1e8, 1e9):
                omega = 2*np.pi*frequency
                response = np.linalg.solve(np.eye(7)+1j*omega*matrices[name], np.eye(7))
                force_gram = omega**2*response.conj().T@gram@response
                rows.append(dict(frequency_hz=frequency, unit_modal_omitted_force_norm=float(np.sqrt(np.linalg.norm(force_gram, 2)))))
            checks[name] = dict(kkt_relative=residual, r_orthogonality=orth, riesz_identity_relative=identity,
                                divergence_relative=divergence, witness_gram_eigenvalues=np.linalg.eigvalsh((gram+gram.T)/2).tolist(),
                                unit_modal_RL_sensitivity=rows)
            arrays.update({f'{name}_closed_force': closed, f'{name}_force_dual': solution[len(active):],
                           f'{name}_projection_coefficients': coefficient, f'{name}_witness': witness,
                           f'{name}_witness_gram': gram, f'{name}_normalized_witness': normalized,
                           f'{name}_witness_normalizer': normalizer, f'{name}_retained_action': force,
                           f'{name}_retained_matrix': matrices[name]})
        # Retain all seven independent witness directions; no empirical rank cut.
        new = arrays['xg31_normalized_witness']
        basis = np.column_stack((q, new))
        all_gram = basis.T@(r@basis)
        gram_error = float(np.linalg.norm(all_gram-np.eye(14), 2))
        assert np.array_equal(basis[:, :7], q) and gram_error < 1e-9
        assert np.max(np.abs(new[boundary])) == 0
        joint = geometry.load_joint()
        local = joint['local_face_signs'][:, :, None]*new[joint['local_face_columns']]
        tets = joint['tetrahedra_m']
        integrated_cells = np.einsum('cin,cid->cnd', local, tets.mean(1)[:, None]-tets)/3
        integrated_closed = integrated_cells.sum(0)
        moment_relative = float(np.linalg.norm(integrated_closed)/np.linalg.norm(np.linalg.norm(integrated_cells, axis=2).sum(0)))
        assert moment_relative < 1e-10
        delta = witnesses['xg31']-witnesses['jacobi']
        delta_gram = delta.T@(r@delta)
        old_gram = arrays['xg31_witness_gram']
        e = np.linalg.inv(np.linalg.cholesky((old_gram+old_gram.T)/2))
        difference = float(np.sqrt(np.linalg.norm(e@delta_gram@e.T, 2)))
        arrays.update(face_flux_basis=basis, physical_r_gram=all_gram, closed_projection_of_retained_q=qc,
                      closed_q_gram=cq, active_face_ids=active, retained_divergence_rows=keep,
                      new_integrated_cell_current=integrated_cells, new_integrated_closed_current=integrated_closed,
                      updated_two_rule_witness_difference=delta)
        for name, action in actions.items():
            arrays[f'{name}_known_new_old_magnetic_cross'] = new.T@action
        checks.update(fourteen_r_gram_identity_relative=gram_error,
                      new_closed_integrated_current_relative=moment_relative,
                      updated_two_rule_witness_difference=difference)
        status, failure = 'PREPARED_FOURTEEN_CURRENT_SPACE_NEW_MAGNETIC_BLOCK_PENDING', None
    except Exception:
        status, failure = 'STOP_FOURTEEN_CURRENT_SPACE', traceback.format_exc()
    np.savez_compressed(out/'fourteen-current-space.npz', **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status=status, failure=failure,
                  pins=PINS, driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'fourteen-current-space.npz'),
                  checks=checks, elapsed_s=monotonic()-started,
                  scope='Retained7 unchanged plus7 R-normalized closed witnesses from updated150584-pair actions. '
                  'New currents have zero exterior flux and Dv=0 to measured tolerance; their scalar/contact coupling vanishes within that test definition. '
                  'Known new-old magnetic cross comes from saved L@Q7. The new-new magnetic block is NOT computed. '
                  'Two-rule witness agreement does not prove full14 integration/space convergence; no AC current, return, port, or board solve.')
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
    assert sha(ROOT/'tools/research/probe_astra_fmm3d_runtime.py') == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=120, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--output', str(out)]))
