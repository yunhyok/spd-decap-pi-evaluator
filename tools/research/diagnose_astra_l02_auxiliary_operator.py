"""Measure restricted-transfer support and its actual Galerkin coarse operator."""
import argparse
import json
from pathlib import Path

import numpy as np

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]


def run(output):
    assert base.sha(Path(base.__file__)) == 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'
    budget = recon._Budget.create(120., 4.)
    pins = {name: base.PINS[name] for name in ('conditional', 'transfer', 'old_operator', 'accepted_field')}
    for name, (path, digest) in pins.items():
        assert base.sha(path) == digest, name
    with np.load(pins['conditional'][0], allow_pickle=False) as z:
        indices = z['conditional_global_active_index']
        rows = z['conditional_to_full_face_transfer_row_index']
        y = base.csc(z, 'y')[indices, :][:, indices]
    with np.load(pins['transfer'][0], allow_pickle=False) as z:
        transfer = base.csr(z, 'transfer')[rows]
        old_indices = z['p1_combined_global_active_indices']
    with np.load(pins['old_operator'][0], allow_pickle=False) as z:
        old = base.csc(z, 'y')[old_indices, :][:, old_indices]
    with np.load(pins['accepted_field'][0], allow_pickle=False) as z:
        old_voltage = z['active_voltage_v'][old_indices]
    count = np.bincount(transfer.indices, minlength=transfer.shape[1])
    galerkin = (transfer.T@(y@transfer)).tocsc()
    galerkin.sum_duplicates(); galerkin.eliminate_zeros()
    delta = (galerkin-old).tocsc(); delta.eliminate_zeros()
    constant = np.ones(transfer.shape[1])
    assert np.array_equal(transfer@constant, np.ones(transfer.shape[0]))
    witness = old_voltage
    action_direct = transfer.T@(y@(transfer@witness))
    action_matrix = galerkin@witness
    roundoff_scale = np.linalg.norm(abs(transfer.T)@(abs(y)@(abs(transfer)@abs(witness))))
    replay = float(np.linalg.norm(action_direct-action_matrix)/max(roundoff_scale, np.finfo(float).tiny))
    assert replay < 1e-13
    budget.check('Galerkin support and old-operator comparison')
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_RESTRICTED_AUXILIARY_OPERATOR_DIAGNOSIS_NO_FACTOR',
              'driver': base.receipt(Path(__file__)), 'inputs': {name: base.receipt(path) for name, (path, _) in pins.items()},
              'transfer_shape': list(transfer.shape), 'zero_transfer_columns': int(np.count_nonzero(count == 0)),
              'zero_galerkin_diagonals': int(np.count_nonzero(galerkin.diagonal() == 0)),
              'galerkin_nnz': galerkin.nnz, 'old_coarse_nnz': old.nnz,
              'coarse_difference_frobenius_relative': float(np.linalg.norm(delta.data)/np.linalg.norm(old.data)),
              'old_field_coarse_energy': base.pair(np.vdot(witness, old@witness)),
              'old_field_restricted_galerkin_energy': base.pair(np.vdot(witness, action_direct)),
              'galerkin_action_roundoff_relative': replay, 'budget': budget.receipt(),
              'scope': 'Support and operator mismatch only. No LU, nullspace-rank proof, new field, physical acceptance or PowerSI comparison. The prior full-face P1 energy identity does not establish equality after exterior elimination.'}
    base.atomic_json(output/'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
