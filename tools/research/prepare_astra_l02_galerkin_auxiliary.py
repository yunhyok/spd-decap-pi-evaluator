"""Save the actual restricted Galerkin auxiliary operator after zero-column removal."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]


def run(output):
    assert base.sha(Path(base.__file__)) == 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'
    budget = recon._Budget.create(120., 4.)
    pins = {name: base.PINS[name] for name in ('conditional', 'transfer')}
    for name, (path, digest) in pins.items():
        assert base.sha(path) == digest, name
    with np.load(pins['conditional'][0], allow_pickle=False) as z:
        indices, rows = z['conditional_global_active_index'], z['conditional_to_full_face_transfer_row_index']
        y = base.csc(z, 'y')[indices, :][:, indices]
    with np.load(pins['transfer'][0], allow_pickle=False) as z:
        transfer = base.csr(z, 'transfer')[rows]
        old_indices = z['p1_combined_global_active_indices']
    count = np.bincount(transfer.indices, minlength=transfer.shape[1])
    kept = np.flatnonzero(count); removed = np.flatnonzero(count == 0)
    assert len(kept) == 761791 and len(removed) == 94983
    transfer = transfer[:, kept].tocsr()
    # Identity contact rows anchor every connected endpoint-average component.
    degree = np.diff(transfer.indptr); start = transfer.indptr[:-1]
    assert np.all((degree == 1) | (degree == 2))
    anchors = transfer.indices[start[degree == 1]]
    pair_start = start[degree == 2]
    a, b = transfer.indices[pair_start], transfer.indices[pair_start+1]
    assert np.all(transfer.data[start[degree == 1]] == 1.)
    assert np.all(transfer.data[pair_start] == .5) and np.all(transfer.data[pair_start+1] == .5)
    graph = sparse.coo_matrix((np.ones(len(a), np.int8), (a, b)), shape=(len(kept), len(kept))).tocsr()
    components, labels = connected_components(graph, directed=False)
    anchor_count = np.bincount(labels[anchors], minlength=components)
    assert np.all(anchor_count > 0), 'unanchored transfer component requires a separate rank proof'
    # For Pv=0 an anchor implies v_i=0; each average row then forces its neighbor0.
    assert np.array_equal(transfer@np.ones(len(kept)), np.ones(transfer.shape[0]))
    del graph, labels, a, b, pair_start, anchors, degree, start
    galerkin = (transfer.T@(y@transfer)).tocsc()
    galerkin.sum_duplicates(); galerkin.eliminate_zeros()
    norm = np.asarray(abs(galerkin).sum(1)).ravel()
    assert np.all(np.isfinite(galerkin.data)) and np.all(norm > 0)
    symmetry = base.max_abs((galerkin-galerkin.T).data)/base.max_abs(galerkin.data)
    assert symmetry < 2e-12 and np.all(abs(galerkin.diagonal()) > 0)
    witness = np.sin(np.arange(len(kept))*.001)+1j*np.cos(np.arange(len(kept))*.0007)
    direct = transfer.T@(y@(transfer@witness))
    magnitude = abs(transfer.T)@(abs(y)@(abs(transfer)@abs(witness)))
    replay = float(np.linalg.norm(galerkin@witness-direct)/np.linalg.norm(magnitude))
    assert replay < 1e-13
    budget.check('actual Galerkin operator and anchored-transfer proof')
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    artifact = output/'galerkin-auxiliary.npz'
    base.atomic_npz(artifact, transfer_data=transfer.data, transfer_indices=transfer.indices,
                    transfer_indptr=transfer.indptr, transfer_shape=np.asarray(transfer.shape),
                    galerkin_data=galerkin.data, galerkin_indices=galerkin.indices,
                    galerkin_indptr=galerkin.indptr, galerkin_shape=np.asarray(galerkin.shape),
                    coarse_row_scale=1/np.sqrt(norm), kept_p1_column_indices=kept,
                    removed_p1_column_indices=removed, old_combined_active_indices=old_indices[kept],
                    conditional_global_active_indices=indices)
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'PASS_ACTUAL_RESTRICTED_GALERKIN_AUXILIARY_NO_FACTOR',
              'driver': base.receipt(Path(__file__)), 'inputs': {name: base.receipt(path) for name, (path, _) in pins.items()},
              'output': base.receipt(artifact), 'transfer_shape': list(transfer.shape), 'transfer_nnz': transfer.nnz,
              'removed_zero_columns': len(removed), 'coarse_rows': len(kept), 'coarse_nnz': galerkin.nnz,
              'endpoint_graph_components': int(components), 'minimum_identity_anchors_per_component': int(anchor_count.min()),
              'full_column_rank_argument': 'Every endpoint-average graph component contains an exact identity row. Pv=0 therefore forces all kept nodal values to zero.',
              'coarse_transpose_symmetry_relative': symmetry, 'galerkin_action_roundoff_relative': replay,
              'budget': budget.receipt(),
              'scope': 'Exact coarse operator for the unchanged conditional Y_L02, with only zero transfer columns removed. Full column rank of transfer is checked combinatorially; no LU, new global field or physical/PowerSI acceptance.'}
    base.atomic_json(output/'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
