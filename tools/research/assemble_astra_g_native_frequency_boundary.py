"""SPD Decap PI Evaluator v0.23.1: native finite-edge/source H at fixed 1 MHz.

This consumes a real endpoint replacement and retires the two overlapping
first-via admittances. It does not fold the old GC stamps into new volume P.
"""
import argparse
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator
from prepare_astra_l14_rt0_reconstruction_space import sha
from assemble_astra_g_window_l02_overlap_removal import pack
from assemble_astra_g_window_sheet_volume_coupling import unpack

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
HYBRID = R/'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz'
HYBRID_SHA = '01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c'
COUPLING = R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz'
COUPLING_SHA = '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf'
OUT = R/'astra-g-native-frequency-boundary-20260912'


def assemble(binding, binding_sha):
    assert sha(binding) == binding_sha and sha(HYBRID) == HYBRID_SHA and sha(COUPLING) == COUPLING_SHA
    with np.load(binding, allow_pickle=False) as z:
        keep = z['kept_final_finite_row']; first = z['patched_first_active_index']; second = z['patched_second_active_index']
        original_n = int(z['original_potential_count'].ravel()[0]); n = int(z['new_potential_count'].ravel()[0])
        terminals = z['source_terminal_active_coordinate']; restriction = unpack(z, 'h_face_to_terminal_restriction')
        removed = z['old_final_finite_row']; contact_ids = z['old_contact_ordinal']
    with np.load(HYBRID, allow_pickle=False) as z:
        old_y = z['finite_admittance_s']; old_first = z['finite_first_active_index']; old_second = z['finite_second_active_index']
        declared_n = int(z['potential_count'][0]); old_contact_nodes = z['contact_global_active_index'][contact_ids]
        gc_first = z['contact_gc_first_active_index']; gc_second = z['contact_gc_second_active_index']; gc_y = z['contact_gc_admittance_s']
    assert original_n == declared_n and n == declared_n+2
    assert np.array_equal(terminals, [2656, declared_n, declared_n+1])
    assert np.array_equal(keep, np.setdiff1d(np.arange(len(old_y)), removed))
    assert len(first) == len(second) == len(keep)
    assert np.count_nonzero(first != old_first[keep]) == 2 and np.array_equal(second, old_second[keep])
    assert not np.isin(first, old_contact_nodes).any() and not np.isin(second, old_contact_nodes).any()
    assert np.all((first >= 0)&(first < n)&(second >= 0)&(second < n)&(first != second))
    admittance = old_y[keep]
    assert np.isfinite(admittance).all() and np.all(admittance.real > 0)
    with np.load(COUPLING, allow_pickle=False) as z:
        h_face = unpack(z, 'source_outward')
    h = (restriction@h_face).tocsr()
    incidence = sparse.coo_matrix((np.r_[np.ones(len(keep)), -np.ones(len(keep))],
                                   (np.r_[first, second], np.tile(np.arange(len(keep)), 2))),
                                  shape=(n, len(keep))).tocsr()

    def native_y_action(voltage):
        voltage = np.asarray(voltage).reshape(-1)
        assert voltage.shape == (n,)
        return incidence@(admittance*(incidence.T@voltage))

    y = LinearOperator((n, n), matvec=native_y_action, dtype=np.complex128)
    gc_occurrences = np.flatnonzero(np.isin(gc_first, old_contact_nodes)|np.isin(gc_second, old_contact_nodes))
    metadata = dict(native_potential_count=np.array([n]), source_terminal_active_coordinate=terminals,
                    retained_finite_row_ids=keep, retired_finite_row_ids=removed,
                    retired_finite_admittance_s=old_y[removed], retained_finite_admittance_s=admittance,
                    retired_contact_gc_stamp_ids=gc_occurrences,
                    retired_contact_gc_first_active_index=gc_first[gc_occurrences],
                    retired_contact_gc_second_active_index=gc_second[gc_occurrences],
                    retired_contact_gc_admittance_s=gc_y[gc_occurrences])
    pack(metadata, 'source_outward_terminal', h)
    return y, incidence, h, metadata


def run(binding, binding_sha):
    started = monotonic()
    y, incidence, h, payload = assemble(binding, binding_sha)
    terminals = payload['source_terminal_active_coordinate']; n = y.shape[0]
    rng = np.random.default_rng(20260912)
    # Exercise both fresh lower ends and their complete actual finite-edge
    # neighborhood; no gauge or boundary value is imposed on a field solve.
    incident = np.unique(incidence[terminals].indices)
    neighbors = np.unique(incidence[:, incident].nonzero()[0])
    v = np.zeros(n, complex); u = np.zeros(n, complex)
    v[neighbors] = rng.normal(size=len(neighbors))+1j*rng.normal(size=len(neighbors))
    u[neighbors] = rng.normal(size=len(neighbors))+1j*rng.normal(size=len(neighbors))
    yv, yu = y@v, y@u
    reciprocity = float(abs(v@yu-u@yv)/max(abs(v@yu), abs(u@yv), 1e-30))
    drop = incidence.T@v
    edge_work = np.vdot(drop, payload['retained_finite_admittance_s']*drop)
    nodal_work = np.vdot(v, yv)
    work_error = float(abs(edge_work-nodal_work)/max(abs(edge_work), 1e-30))
    balance = float(abs(yv.sum())/max(np.linalg.norm(yv, 1), 1e-30))
    assert reciprocity < 1e-10 and work_error < 1e-10 and balance < 1e-12 and edge_work.real > 0
    q = np.zeros(h.shape[1], complex)
    q[np.unique(h.indices)] = rng.normal(size=h.nnz)+1j*rng.normal(size=h.nnz)
    source_current = h@q
    source_work = float(abs(v[terminals]@source_current-(h.T@v[terminals])@q))
    assert source_work < 1e-10
    OUT.mkdir(exist_ok=False)
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    # Source binding owns the patched endpoint arrays. Keep that pinned input
    # rather than creating a second large copy of the same geometry/incidence.
    np.savez_compressed(OUT/'native-frequency-boundary.npz', **payload)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_EXECUTABLE_NATIVE_FINITE_Y_AND_SOURCE_H', frequency_hz=1e6,
                  elapsed_s=monotonic()-started, driver_sha256=sha(Path(__file__)),
                  artifact_sha256=sha(OUT/'native-frequency-boundary.npz'),
                  pins={str(binding): binding_sha, str(HYBRID): HYBRID_SHA, str(COUPLING): COUPLING_SHA},
                  counts=dict(retained_finite_branches=incidence.shape[1], native_potentials=n,
                              retired_first_vias=2, source_terminals=h.shape[0],
                              source_incident_retained_finite_branches=len(incident),
                              retired_contact_gc_stamps=len(payload['retired_contact_gc_stamp_ids'])),
                  metrics=dict(complex_y_reciprocity_relative=reciprocity, edge_nodal_work_relative=work_error,
                               normalized_current_balance=balance, source_current_voltage_dual_work_abs=source_work),
                  equation='Y_native v - scatter(source_terminal_active_coordinate, H_terminal i) + other_owned_boundary_terms = b',
                  scope='Actual fixed1MHz native finite admittances after two first-via retirements and exact separate lower endpoint reconnection. All other finite branches/admittances are preserved. NativeY and H source dual action execute; no solve or ground imposed. Old GC occurrences touching retired contacts are enumerated, not silently reused. Complete field L/P, free-cell GC ownership replacement, other circuit categories and external port closure are still required for full A and complex port response.')
    (OUT/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', type=Path, required=True); parser.add_argument('--binding-sha', required=True)
    args = parser.parse_args(); run(args.binding, args.binding_sha)
