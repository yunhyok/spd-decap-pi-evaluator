"""SPD Decap PI Evaluator v0.23.1: exact sheet overlap operator subtraction.

The retained space is the restriction of the original RT0 fields to outside
polygons. No polygon is promoted to a new, unconstrained RT0 element.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from shapely import from_wkb
from shapely.geometry import LineString
from project_astra_l14_gc_mass import geometry_moments
from project_astra_l14_gc_mass import _polygon_parts
from assemble_astra_l25_rt0_resistance import _batch_local_rt0
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
OUT = R / 'astra-g-window-l02-overlap-removal-20260912-03'
PINS = {
    R/'astra-g-window-l02-copper-partition-20260912/copper-partition.npz': 'ea737beba15bb927ea48b0dcb65751bd836c58b73cce95f90516e5185b98d0e4',
    R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz': '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9',
    R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz': '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f',
    R/'astra-g-window-l02-rt0-trace-20260912/trace-and-overlap.npz': '347422e14e8819b2cff70fea366b4c8a58cb5df5e1a1240719dd6f45d45b954e',
    R/'astra-selected-g-l02-two-post-neighborhood-01/selected-two-pad-full-owner.wkb': 'd350ac7a5ca64801b4b9e35cda27fcf1c199d83d554c9cec8ad9295342f25cec',
    R/'astra-l02-hybrid-right-correction-01/l02-reconstructed-field.npz': 'b715867457410d6e2ba5154143b479f4d0868f263a3d487d5780c2c77c6c6f19',
    ROOT/'tools/research/project_astra_l14_gc_mass.py': 'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3',
    ROOT/'tools/research/assemble_astra_l25_rt0_resistance.py': 'ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010',
}


def polygon_mass(vertices, polygon):
    v = vertices - vertices[0]
    area, mx, my, mxx, _, myy = geometry_moments(polygon, vertices[0])
    twice_area = abs(np.linalg.det(v[1:]))
    linear = v[:, 0]*mx + v[:, 1]*my
    return (mxx+myy-linear[:, None]-linear[None, :]+area*(v@v.T))/(1191.8*twice_area**2)


def pack(payload, name, matrix):
    matrix = matrix.tocsr()
    payload.update({name+'_shape': np.array(matrix.shape), name+'_row_ptr': matrix.indptr,
                    name+'_col': matrix.indices, name+'_data': matrix.data})


def retained_edge_fraction(vertices, clipped):
    """Project existing clipped boundary edges; tolerate coordinate roundoff only."""
    origin, end = vertices
    edge = end-origin; length = np.linalg.norm(edge); tangent = edge/length
    intervals = []
    for poly in _polygon_parts(clipped):
        for ring in [poly.exterior, *poly.interiors]:
            points = np.asarray(ring.coords)-origin
            along = points@tangent
            normal = points@np.array([-tangent[1], tangent[0]])
            for i in range(len(points)-1):
                if max(abs(normal[i]), abs(normal[i+1])) < 1e-8:
                    lo, hi = sorted(along[i:i+2])
                    lo, hi = max(lo, 0.), min(hi, length)
                    if hi > lo:
                        intervals.append((lo, hi))
    merged = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1]+1e-8:
            merged[-1][1] = max(hi, merged[-1][1])
        else:
            merged.append([lo, hi])
    return sum(hi-lo for lo, hi in merged)/length


def run():
    started = monotonic()
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    paths = list(PINS)
    with np.load(paths[0], allow_pickle=False) as z:
        tids = z['original_triangle_ids']; ordinals = z['free_triangle_ordinals']
        fractions = z['inside_area_fraction']; classes = z['ownership_class']
        inside = z['inside_wkb_hex']; outside = z['outside_wkb_hex']
    with np.load(paths[1], allow_pickle=False) as z:
        xy = z['node_xy_um']; triangles = z['triangles']
    with np.load(paths[2], allow_pickle=False) as z:
        free = z['free_triangle_indices']; local = z['local_facet_branch_index']; signs = z['local_outward_flux_sign']
        first = z['branch_first_node']; second = z['branch_second_node']; edges = z['branch_mesh_edges']
        contact = z['triangle_contact_index']; potential = z['triangle_potential_index']
        original_d_shape = z['distributional_b_shape']; original_r_shape = z['r_shape']
    with np.load(paths[3], allow_pickle=False) as z:
        trace = sparse.csr_matrix((z['trace_data'], z['trace_col'], z['trace_row_ptr']), shape=tuple(z['trace_shape']))
    owner = from_wkb(paths[4].read_bytes())
    with np.load(paths[5], allow_pickle=False) as z:
        saved_current = z['l02_branch_current_a']
    nf, nb = len(free), len(first)
    nc = int(contact.max())+1
    free_mask = ordinals >= 0
    fi = ordinals[free_mask]; branch = local[fi]; orientation = signs[fi]
    v = xy[triangles[tids[free_mask]]]
    reference, _, _ = _batch_local_rt0((v-v[:, :1])*1e-6, 1191.8)
    mass_inside = np.array([polygon_mass(a, from_wkb(bytes.fromhex(b))) for a, b in zip(v, inside[free_mask])])
    mass_outside = np.array([polygon_mass(a, from_wkb(bytes.fromhex(b))) for a, b in zip(v, outside[free_mask])])
    mass_error = float(np.max(np.linalg.norm(mass_inside+mass_outside-reference, axis=(1, 2))/np.linalg.norm(reference, axis=(1, 2))))
    assert mass_error < 1e-10, mass_error
    eigen_min = float(min(np.linalg.eigvalsh(mass_inside).min(), np.linalg.eigvalsh(mass_outside).min()))
    assert eigen_min > -1e-14
    affected_branches = np.unique(branch)
    compact_branch = np.searchsorted(affected_branches, branch)
    rr = np.broadcast_to(compact_branch[:, :, None], mass_inside.shape).ravel()
    cc = np.broadcast_to(compact_branch[:, None, :], mass_inside.shape).ravel()
    values = mass_inside*orientation[:, :, None]*orientation[:, None, :]
    remove_r = sparse.coo_matrix((values.ravel(), (rr, cc)), shape=(len(affected_branches),)*2).tocsr()
    wholly_free = ordinals[classes == 'WHOLLY_REMOVED_FREE']
    support = np.bincount(local.ravel(), minlength=nb)
    removed_support = np.bincount(local[wholly_free].ravel(), minlength=nb)
    dropped_branches = np.flatnonzero(support == removed_support)
    assert np.all(support > 0)
    assert np.intersect1d(dropped_branches, trace.indices).size == 0
    contact_ids = np.unique(contact[tids[~free_mask]])
    assert len(contact_ids) == 2
    for ci in contact_ids:
        assert np.array_equal(np.sort(tids[contact[tids] == ci]), np.flatnonzero(contact == ci))
    contact_rows = nf+contact_ids
    # Exterior pseudo nodes own the original sheet edge distributional charge.
    # Its normal RT0 trace is constant: restrict by exact edge length, not area.
    exterior_branches = affected_branches[second[affected_branches] >= nf+nc]
    assert not np.any(first[affected_branches] >= nf+nc)
    inside_by_row = {int(oi): from_wkb(bytes.fromhex(wkb)) for oi, wkb in zip(fi, inside[free_mask])}
    exterior_fraction = np.array([retained_edge_fraction(xy[edges[b]], inside_by_row[int(first[b])]) for b in exterior_branches])
    dead_exterior = np.isin(exterior_branches, dropped_branches)
    residual_length = (1-exterior_fraction[dead_exterior])*np.linalg.norm(np.diff(xy[edges[exterior_branches[dead_exterior]]], axis=1)[:, 0], axis=1)
    assert np.max(abs(residual_length), initial=0.) < 1e-8
    exterior_fraction[dead_exterior] = 1.  # Its entire supporting free cell was already removed.
    keep_exterior = exterior_fraction > 0
    exterior_branches = exterior_branches[keep_exterior]; exterior_fraction = exterior_fraction[keep_exterior]
    changed_rows = np.r_[fi, contact_rows, second[exterior_branches]]
    removed_fraction = np.r_[fractions[free_mask], np.ones(len(contact_rows)), exterior_fraction]
    assert len(np.unique(changed_rows)) == len(changed_rows)
    order = np.argsort(changed_rows); changed_rows = changed_rows[order]; removed_fraction = removed_fraction[order]
    incident = np.unique(np.r_[affected_branches, np.flatnonzero(np.isin(first, contact_rows)|np.isin(second, contact_rows))])
    assert np.array_equal(incident, affected_branches)
    row_map = {int(row): i for i, row in enumerate(changed_rows)}
    dr, dc, dd = [], [], []
    for ci, b in enumerate(affected_branches):
        for row, sign in ((first[b], 1.), (second[b], -1.)):
            if int(row) in row_map:
                ri = row_map[int(row)]; dr.append(ri); dc.append(ci); dd.append(sign*removed_fraction[ri])
    remove_d = sparse.coo_matrix((dd, (dr, dc)), shape=(len(changed_rows), len(affected_branches))).tocsr()
    cut_sum = np.asarray(trace[:, affected_branches].sum(axis=0)).ravel()
    divergence_error = float(np.max(abs(np.asarray(remove_d.sum(axis=0)).ravel()-cut_sum)))
    assert divergence_error < 1e-9, divergence_error
    removed_rows = changed_rows[removed_fraction >= 1-1e-12]
    # Sparse correction is executable without copying the 3-million-DOF matrix:
    # R_out q = R_old q - scatter(remove_r @ q[affected_branches]); discard dead columns.
    # D_out q = D_old q - scatter_rows(remove_d @ q[affected_branches]); discard removed rows.
    qlocal = saved_current[branch]*orientation
    energy_inside = float(np.einsum('ni,nij,nj->', qlocal.conj(), mass_inside, qlocal).real)
    energy_outside = float(np.einsum('ni,nij,nj->', qlocal.conj(), mass_outside, qlocal).real)
    scatter_energy = float(np.vdot(saved_current[affected_branches], remove_r@saved_current[affected_branches]).real)
    energy_error = abs(scatter_energy-energy_inside)/max(abs(energy_inside), 1e-30)
    assert energy_error < 1e-12
    payload = dict(affected_branch_ids=affected_branches, dropped_current_branch_ids=dropped_branches,
                   changed_charge_row_ids=changed_rows, removed_charge_row_ids=removed_rows,
                   row_inside_fraction=removed_fraction, original_d_shape=original_d_shape, original_r_shape=original_r_shape,
                   removed_contact_ids=contact_ids, removed_contact_row_ids=contact_rows,
                   exterior_branch_ids=exterior_branches, exterior_inside_length_fraction=exterior_fraction,
                   free_triangle_ids=tids[free_mask], free_triangle_ordinals=fi,
                   local_branch_ids=branch, local_outward_signs=orientation,
                   resistance_inside_local_ohm=mass_inside, resistance_outside_local_ohm=mass_outside,
                   contact_triangle_ids=tids[~free_mask], contact_triangle_potential_rows=potential[tids[~free_mask]])
    pack(payload, 'remove_resistance', remove_r); pack(payload, 'remove_divergence', remove_d)
    OUT.mkdir(exist_ok=False)
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(OUT/'overlap-operators.npz', **payload)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='PASS_EXACT_SHEET_OVERLAP_OPERATOR_REMOVAL',
                  elapsed_s=monotonic()-started, pins={str(p): h for p, h in PINS.items()},
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(OUT/'overlap-operators.npz'),
                  counts=dict(free_cells=len(fi), partial_free_cells=int(np.count_nonzero(classes == 'PARTIAL_CUT_FREE')),
                              affected_branches=len(affected_branches), dropped_branches=len(dropped_branches),
                              removed_charge_rows=len(removed_rows), changed_charge_rows=len(changed_rows),
                              removed_contact_reservoirs=len(contact_ids), exterior_charge_rows=len(exterior_branches)),
                  metrics=dict(local_mass_partition_relative=mass_error, minimum_local_mass_eigenvalue_ohm=eigen_min,
                               removed_divergence_equals_outward_cut_max_abs=divergence_error,
                               saved_current_removed_energy_w=energy_inside, saved_current_retained_cut_cell_energy_w=energy_outside,
                               sparse_energy_relative=energy_error),
                  scope='Executable exact R/D subtraction with dead-current and removed charge-row selectors; 20 outside polygons survive. Two old contact reservoirs and inside portions of exterior edge charge are removed. L/P kernels and source/native contact reconnection are not replaced by these operators. Saved-current Joule readout is not a coupled response or board impedance.')
    (OUT/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
