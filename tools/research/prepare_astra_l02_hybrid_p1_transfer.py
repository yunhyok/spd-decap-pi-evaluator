"""Save the actual P1-to-full-face-hybrid auxiliary transfer; no field solve."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse

import run_astra_l02_sheet_r_shadow as p1
from reconstruct_astra_native_loaded_field import _Budget

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
PINS = {
    'mesh': (R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz', '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9'),
    'space': (R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz', '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f'),
    'pack': (R/'astra-l02-full-face-hybrid-operator-05/hybrid-operator-pack.npz', '29be4c9e5b0fb586c94cc7e0bbeeb1c56738b3b96a0a4d0410e60aaa83a76756'),
    'map': (R/'astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz', 'a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469'),
    'p1_helper': (Path(p1.__file__), '8437a7eb0398fdef919704c33a6ba2e9498c810b79e04b5df533462c8ce55105'),
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(output):
    start = time.perf_counter()
    budget = _Budget.create(max_runtime_s=120., max_rss_gib=4.)
    inputs = {}
    for name, (path, expected) in PINS.items():
        assert sha(path) == expected, name
        inputs[name] = {'path': str(path), 'sha256': expected}
        budget.check('pins')
    with np.load(PINS['mesh'][0], allow_pickle=False) as mesh:
        nodes = len(mesh['node_xy_um'])
        contraction, free = p1.contact_contraction(nodes, mesh['contact_node_indices'], mesh['contact_node_indptr'], 38856)
        stiffness = p1.read_csc(mesh, 'stiffness') * p1.SHEET_CONDUCTANCE_S
    with np.load(PINS['space'][0], allow_pickle=False) as space:
        edges = space['branch_mesh_edges']
        rim = space['electrode_rim_branch_indices']
        rim_contact = space['electrode_rim_contact_index']
    with np.load(PINS['pack'][0], allow_pickle=False) as pack:
        trace = pack['trace_branch_index']
        contacts = pack['contact_global_active_index']
        new_active = np.r_[contacts, pack['trace_global_active_index'][trace]]
        exterior = pack['exterior_branch_index']
        local_terminal = pack['local_facet_terminal_global_index']
        h = pack['hybrid_h_upper_s']
    with np.load(PINS['map'][0], allow_pickle=False) as mapping:
        old_active = mapping['l02_combined_sheet_active_indices']
    assert len(old_active) == len(free)+38856 == 856774
    assert len(new_active) == 2512727 and len(np.unique(new_active)) == len(new_active)
    assert np.array_equal(new_active[:38856], old_active[:38856])
    assert np.array_equal(contraction[edges[rim]], np.repeat(rim_contact[:, None], 2, axis=1))
    trace_columns = contraction[edges[trace]]
    rows = np.r_[np.arange(38856), np.repeat(38856+np.arange(len(trace)), 2)]
    columns = np.r_[np.arange(38856), trace_columns.ravel()]
    weights = np.r_[np.ones(38856), np.full(2*len(trace), .5)]
    transfer = sparse.coo_matrix((weights, (rows, columns)), shape=(len(new_active), len(old_active))).tocsr()
    transfer.sum_duplicates()
    del rows, columns, weights, trace_columns
    assert np.array_equal(transfer @ np.ones(len(old_active)), np.ones(len(new_active)))
    assert transfer[:38856, :38856].nnz == 38856
    budget.check('transfer')

    # Actual mesh check: CR facet averages reproduce every continuous P1 field.
    voltage = np.sin(np.arange(len(old_active), dtype=float)*.173)
    trace_voltage = transfer @ voltage
    global_to_local = np.full(3996022, -1, dtype=np.int64)
    global_to_local[new_active] = np.arange(len(new_active))
    cell_local = global_to_local[local_terminal]
    assert np.all(cell_local >= 0)
    nodal = voltage[contraction]
    p1_energy = float(nodal @ (stiffness @ nodal))
    del stiffness, nodal, contraction
    total = 0.
    ii, jj = np.triu_indices(3)
    for first in range(0, len(cell_local), 100000):
        cell = trace_voltage[cell_local[first:first+100000]]
        terms = h[first:first+100000]*cell[:, ii]*cell[:, jj]
        terms[:, ii != jj] *= 2
        total += float(terms.sum())
        budget.check('actual_energy')
    difference = abs(total-p1_energy)/abs(p1_energy)
    assert difference < 2e-12, difference
    exterior_local = 38856 + np.searchsorted(trace, exterior)
    assert np.array_equal(trace[exterior_local-38856], exterior)
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    artifact = output/'p1-hybrid-transfer.npz'
    np.savez_compressed(artifact, transfer_data=transfer.data, transfer_indices=transfer.indices,
                        transfer_indptr=transfer.indptr, transfer_shape=np.asarray(transfer.shape),
                        p1_combined_global_active_indices=old_active,
                        hybrid_full_face_global_active_indices=new_active,
                        hybrid_full_face_exterior_local_indices=exterior_local)
    result = {
        'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
        'status': 'PASS_ACTUAL_L02_P1_TO_FULL_FACE_HYBRID_TRANSFER',
        'driver_sha256': sha(Path(__file__)), 'inputs': inputs,
        'output': {'path': str(artifact), 'sha256': sha(artifact), 'size_bytes': artifact.stat().st_size},
        'shape': list(transfer.shape), 'nnz': transfer.nnz,
        'checks': {'all_constants_exact': True, 'all_contact_rims_endpoint_identity': True,
                   'actual_p1_energy': p1_energy, 'actual_hybrid_energy': total,
                   'actual_energy_relative_difference': difference},
        'scope': 'Auxiliary interpolation only: continuous P1 endpoint averages map to hybrid facet potentials, with exact contact identity. Transpose restricts residuals. Exterior rows remain explicit and can be selected out after qualified boundary elimination. This does not assert equality of P1 and P0 G/C or solve the hybrid equations.',
        'solver_limit': 'Reuse as an empirical preconditioner only; no mesh-independent bound for these skinny cells or complex coupled circuit, and no field/convergence/accuracy acceptance.',
        'elapsed_s': time.perf_counter()-start, 'budget': budget.receipt(),
    }
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('status', 'shape', 'nnz', 'checks', 'elapsed_s', 'output')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
