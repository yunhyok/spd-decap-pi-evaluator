"""SPD Decap PI Evaluator v0.23.1: executable sheet/volume R, D, C, H.

The original large R is reused through a sparse correction. L and P remain
required physical inputs to a subsequent coupled frequency-domain operator.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator
from prepare_astra_l14_rt0_reconstruction_space import sha
from assemble_astra_g_window_l02_overlap_removal import pack

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-g-window-sheet-volume-coupling-20260912'
PINS = {
    R/'astra-g-window-l02-overlap-removal-20260912-03/overlap-operators.npz': 'c6fc6286081e44f1e41533c5c8f27b000de273a06deb5f090faccd587aecf866',
    R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz': '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f',
    R/'astra-g-window-l02-rt0-trace-20260912/trace-and-overlap.npz': '347422e14e8819b2cff70fea366b4c8a58cb5df5e1a1240719dd6f45d45b954e',
    R/'astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz': '600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5',
    R/'astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz': 'fee5084bfddc44771a16850eeefef2a2731ec29d4a132c6a03f3f42c40c719f1',
}


def unpack(z, prefix):
    return sparse.csr_matrix((z[prefix+'_data'], z[prefix+'_col'], z[prefix+'_row_ptr']), shape=tuple(z[prefix+'_shape']))


def assemble():
    """Return executable resistance action, charge incidence, cut and source traces."""
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    paths = list(PINS)
    with np.load(paths[0], allow_pickle=False) as z:
        affected = z['affected_branch_ids']; dropped = z['dropped_current_branch_ids']
        changed = z['changed_charge_row_ids']; removed_rows = z['removed_charge_row_ids']
        fractions = z['row_inside_fraction']; remove_r = unpack(z, 'remove_resistance')
    with np.load(paths[1], allow_pickle=False) as z:
        r_old = sparse.csr_matrix((z['r_data'], z['r_indices'], z['r_indptr']), shape=tuple(z['r_shape']))
        d_old = sparse.csr_matrix((z['distributional_b_data'], z['distributional_b_indices'], z['distributional_b_indptr']), shape=tuple(z['distributional_b_shape']))
    nb = r_old.shape[0]
    current_keep_mask = np.ones(nb, bool); current_keep_mask[dropped] = False
    current_keep = np.flatnonzero(current_keep_mask)
    row_keep_mask = np.ones(d_old.shape[0], bool); row_keep_mask[removed_rows] = False
    row_keep = np.flatnonzero(row_keep_mask)
    row_scale = np.ones(d_old.shape[0]); row_scale[changed] -= fractions
    d_sheet = d_old[row_keep][:, current_keep].multiply(row_scale[row_keep, None]).tocsr()
    assert np.all(np.diff(d_sheet.indptr) > 0), 'an empty charge unknown survived'
    with np.load(paths[2], allow_pickle=False) as z:
        trace = unpack(z, 'trace')[:, current_keep]
        cut_ids = z['cut_face_ids']
    with np.load(paths[3], allow_pickle=False) as z:
        xyz = z['vertices_um']; cells = z['cells']; faces = z['face_vertices']
        boundary = z['boundary_face_ids']; state = z['boundary_state']
        source_ids = np.r_[z['top_electrode_face_ids'], z['lower_r20_next_via_contact_face_ids']]
        source_body = z['cell_body'][z['first_owner_cell'][source_ids]]
        assert np.all(np.isin(source_body, [0, 1]))
        pins = z['selected_pad_pin_ids'][source_body]
        source_kind = np.r_[np.full(len(z['top_electrode_face_ids']), 'TOP_ELECTRODE'),
                            np.full(len(z['lower_r20_next_via_contact_face_ids']), 'LOWER_R20_NEXT_VIA')]
    with np.load(paths[4], allow_pickle=False) as z:
        r_volume = unpack(z, 'resistance_ohm'); d_volume = unpack(z, 'volume_d')
        assert np.array_equal(boundary, z['boundary_face_ids'])
        current_face_ids = z['cell_face_ids']; current_face_signs = z['cell_face_signs']
    nv = r_volume.shape[0]; ns = len(current_keep); ncurrent = ns+nv
    free_boundary = boundary[~np.isin(boundary, np.r_[cut_ids, source_ids])]
    surface = sparse.coo_matrix((-np.ones(len(free_boundary)), (np.arange(len(free_boundary)), free_boundary)), shape=(len(free_boundary), nv)).tocsr()
    d_local = sparse.vstack([d_volume, surface], format='csr')
    divergence = sparse.block_diag([d_sheet, d_local], format='csr')
    selector = sparse.coo_matrix((np.ones(len(cut_ids)), (np.arange(len(cut_ids)), cut_ids)), shape=(len(cut_ids), nv)).tocsr()
    constraint = sparse.hstack([-trace, selector], format='csr')
    # Source faces stay independent. Pin/kind metadata permits later justified
    # electrode binding, and never merges the twelve artificial cut voltages.
    source = sparse.coo_matrix((np.ones(len(source_ids)), (np.arange(len(source_ids)), ns+source_ids)), shape=(len(source_ids), ncurrent)).tocsr()

    def resistance_action(q):
        q = np.asarray(q).reshape(-1)
        assert q.shape == (ncurrent,)
        full = np.zeros(nb, dtype=q.dtype); full[current_keep] = q[:ns]
        sheet_value = r_old@full
        sheet_value[affected] -= remove_r@full[affected]
        return np.r_[sheet_value[current_keep], r_volume@q[ns:]]

    resistance = LinearOperator((ncurrent, ncurrent), matvec=resistance_action,
                                rmatvec=resistance_action, dtype=np.float64)
    payload = dict(retained_sheet_current_ids=current_keep, retained_sheet_charge_row_ids=row_keep,
                   retained_sheet_charge_row_scale=row_scale[row_keep], source_face_ids=source_ids,
                   source_pin_ids=pins, source_kinds=source_kind, cut_face_ids=cut_ids,
                   free_3d_surface_face_ids=free_boundary,
                   volume_charge_vertices_um=xyz[cells], surface_charge_vertices_um=xyz[faces[free_boundary]],
                   source_face_vertices_um=xyz[faces[source_ids]],
                   local_current_face_ids=current_face_ids, local_current_face_signs=current_face_signs,
                   sheet_current_count=np.array([ns]), sheet_charge_count=np.array([len(row_keep)]))
    pack(payload, 'local_resistance', r_volume); pack(payload, 'local_divergence', d_local)
    pack(payload, 'cut_constraint', constraint); pack(payload, 'source_outward', source)
    return resistance, divergence, constraint, source, payload


def run():
    started = monotonic()
    resistance, divergence, constraint, source, payload = assemble()
    # Operator identity applies to every current, including arbitrary original
    # exterior charge currents: sum(D)-sum(H)=sum(C). No zero flux is imposed.
    identity = np.asarray(divergence.sum(axis=0)-source.sum(axis=0)-constraint.sum(axis=0)).ravel()
    conservation = float(np.max(abs(identity)))
    assert conservation < 1e-9, conservation
    ns = int(payload['sheet_current_count'][0]); n = resistance.shape[0]
    rng = np.random.default_rng(20260912)
    x = np.zeros(n, complex); y = np.zeros(n, complex)
    sheet_used = np.unique(constraint[:, :ns].indices)
    active = np.r_[sheet_used, ns+np.arange(n-ns)]
    x[active] = rng.normal(size=len(active))+1j*rng.normal(size=len(active))
    y[active] = rng.normal(size=len(active))+1j*rng.normal(size=len(active))
    # Set each of the twelve 3D cut fluxes to its traced exterior sheet flux.
    for q in (x, y):
        q[ns+payload['cut_face_ids']] -= constraint@q
    constraint_residual = float(max(np.max(abs(constraint@x)), np.max(abs(constraint@y))))
    rx = resistance@x; ry = resistance@y
    reciprocity = float(abs(x@ry-y@rx)/max(abs(x@ry), abs(y@rx), 1e-30))
    joule = float(np.vdot(x, rx).real)
    charge_balance = float(abs(np.sum(divergence@x)-np.sum(source@x)))
    voltages = rng.normal(size=12)+1j*rng.normal(size=12)
    # Use an unconstrained field for a nonzero independent virtual-work check.
    trial = x.copy(); trial[ns+payload['cut_face_ids']] += np.arange(12)+1j
    work = float(abs(voltages@(constraint@trial)-(constraint.T@voltages)@trial))
    assert constraint_residual < 1e-10 and reciprocity < 1e-10 and joule > 0
    assert charge_balance < 1e-8 and work < 1e-9
    OUT.mkdir(exist_ok=False)
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(OUT/'sheet-volume-coupling.npz', **payload)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_EXECUTABLE_SHEET_VOLUME_R_D_C_H', elapsed_s=monotonic()-started,
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(OUT/'sheet-volume-coupling.npz'),
                  pins={str(p): h for p, h in PINS.items()},
                  counts=dict(retained_sheet_currents=ns, local_3d_currents=n-ns, total_currents=n,
                              total_charge_rows=divergence.shape[0], local_free_surface_charge_rows=len(payload['free_3d_surface_face_ids']),
                              independent_cut_constraints=constraint.shape[0], independent_source_faces=source.shape[0]),
                  metrics=dict(operator_charge_conservation_max_abs=conservation,
                               constrained_current_residual=constraint_residual,
                               resistance_reciprocity_relative=reciprocity, witness_joule_w=joule,
                               constrained_charge_balance_abs=charge_balance, independent_cut_dual_work_abs=work),
                  equations=['(R+jwL)i-D.T Pq+C.T lambda+H.T v_source=0', 'D i+jwq=0', 'C i=0'],
                  scope='Original sheet R/D are restricted with exact copper/exterior-charge subtraction and 149 dead currents removed. The complete 3D R/D joins through twelve independent cut constraints. Source TOP and lower-r20 physical faces remain independent and mapped to their original pins. assemble() returns executable operators without writing another full sheet matrix. L/P support replacement, removal of overlapping native circuit elements and external source admittance are still required for a physical complex response; no frequency solve or board accuracy claim.')
    (OUT/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
