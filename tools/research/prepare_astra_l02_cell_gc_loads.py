"""Recover L02 source-owned cell C and saved-P1 local loads without a solve."""
import argparse
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import coo_matrix

import reconstruct_astra_native_loaded_field as recon
import run_astra_l02_sheet_r_shadow as sheet
from prepare_astra_l02_rt0_current_space import sparse_arrays

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
PINS = {
    'mesh': (R / 'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz',
             '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9'),
    'gc': (R / 'astra-l02-gc-mass-04/l02-gc-capacitance-block.npz',
           '920dc89580bf123df00343d1a59f149c5fd96158694c391179528a02fe1af184'),
    'space': (R / 'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz',
              '7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f'),
    'field': (R / 'astra-l02-sheet-r-board-1mhz-01/epsilon-1-field.npz',
              '40ff6697ba5a0b1f21c558d71c788b49a37d6ccc878dfd12e3aa7c3aafbac18a'),
    'field_result': (R / 'astra-l02-sheet-r-board-1mhz-01/result.json',
                     'a491c951b7ec8c209fb194cb8dfa82d25a94230abefffbb97c94af55179b0e9b'),
    'field_review': (R / 'astra-l02-sheet-r-board-1mhz-01/independent-review.json',
                     '816ba18a3aa1aed252f3dedfb965983d00e8544f5c697e0cb73b4f919db8ef4d'),
    'raw': (R / 'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz',
            '6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7'),
    'sheet_helper': (Path(sheet.__file__),
                     '8437a7eb0398fdef919704c33a6ba2e9498c810b79e04b5df533462c8ce55105'),
    'space_helper': (ROOT / 'tools/research/prepare_astra_l02_rt0_current_space.py',
                     'a5b8ecc5998a7ec9f5685a89eefaaa06cdc9c7382eb40de042ebac5a9b487d5e'),
}


def scatter_nodes(triangles, values, count):
    return (np.bincount(triangles.ravel(), weights=values.real.ravel(), minlength=count)
            + 1j * np.bincount(triangles.ravel(), weights=values.imag.ravel(), minlength=count))


def run(output):
    started = monotonic()
    budget = recon._Budget.create(120, 3)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for name, (path, digest) in PINS.items():
            assert recon._sha256_file(path) == digest, name
        with np.load(PINS['mesh'][0]) as mesh:
            xy, triangles = mesh['node_xy_um'], mesh['triangles']
            contact_nodes = mesh['contact_node_indices']
            contact_ptr = mesh['contact_node_indptr']
            supports = mesh['contact_support_index']
            stiffness = sheet.read_csc(mesh, 'stiffness')
        count, cells = len(xy), len(triangles)
        assert count == 1439614 and cells == 2127824 and len(supports) == 38856
        assert count ** 3 < np.iinfo(np.int64).max and np.all(np.diff(triangles, axis=1) > 0)
        contraction, free_nodes = sheet.contact_contraction(count, contact_nodes, contact_ptr, len(supports))
        with np.load(PINS['field'][0]) as field:
            voltage = field['active_voltage']
            active = field['sheet_active_indices']
            assert np.array_equal(active, np.r_[sheet.TARGET, np.arange(sheet.NATIVE_SIZE, len(voltage))])
            assert np.array_equal(supports, field['contact_support_index'])
            assert field['mesh_npz_sha256_utf8'].tobytes().decode() == PINS['mesh'][1]
            assert field['gc_npz_sha256_utf8'].tobytes().decode() == PINS['gc'][1]
            phi = voltage[active[contraction]]
        with np.load(PINS['space'][0]) as space:
            contact = space['triangle_contact_index']
            free_cells = space['free_triangle_indices']
            cell_potential = space['triangle_potential_index']
        assert np.array_equal(free_cells, np.flatnonzero(contact < 0))
        with np.load(PINS['raw'][0]) as raw:
            coefficient = complex(raw['partial_actual_1mhz_dispersion_admittance_scale_s'][0])

        with np.load(PINS['gc'][0]) as mass:
            rows = mass['owner_mass_row'].reshape(-1, 6)
            cols = mass['owner_mass_col'].reshape(-1, 6)
            values = mass['owner_mass_data_um2'].reshape(-1, 6)
            owners = mass['owner_mass_owner_index'].reshape(-1, 6)
            assert rows.shape == cols.shape == values.shape == owners.shape == (1041341, 6)
            assert np.all(owners == owners[:, :1]) and np.all(np.isfinite(values))
            owner = owners[:, 0].copy()
            nodes = rows[:, [0, 3, 5]].copy()
            pairs = np.array([[0, 0], [0, 1], [0, 2], [1, 1], [1, 2], [2, 2]])
            assert np.array_equal(rows, nodes[:, pairs[:, 0]])
            assert np.array_equal(cols, nodes[:, pairs[:, 1]])
            del rows, cols, owners
            def keys(t):
                return (t[:, 0].astype(np.int64) * count + t[:, 1]) * count + t[:, 2]
            triangle_keys = keys(triangles)
            order = np.argsort(triangle_keys)
            sorted_keys = triangle_keys[order]
            assert np.all(sorted_keys[1:] > sorted_keys[:-1])
            cut_keys = keys(nodes)
            pos = np.searchsorted(sorted_keys, cut_keys)
            assert np.all(pos < cells) and np.array_equal(sorted_keys[pos], cut_keys)
            cut_cell = order[pos]
            assert np.array_equal(triangles[cut_cell], nodes)
            cut_area = values[:, [0, 3, 5]].sum(axis=1) + 2 * values[:, [1, 2, 4]].sum(axis=1)
            assert np.all(cut_area > 0)
            density, capacitance = mass['owner_density_f_per_um2'], mass['owner_capacitance_f']
            external_owner, external = mass['owner_external_active_indices'], mass['external_active_indices']
            area_map = coo_matrix((cut_area, (owner, cut_cell)), shape=(2064, cells)).tocsr()
            area_map.sum_duplicates()
            area_map.sort_indices()
            totals = np.asarray(area_map.sum(axis=1)).ravel()
            area_error = float(np.max(abs(totals - mass['owner_source_overlap_area_um2']) /
                                      mass['owner_source_overlap_area_um2']))
            cap_error = float(np.max(abs(totals * density - capacitance) / capacitance))
            assert area_error < 2e-9 and cap_error < 2e-9
            # Exact saved clipped P1 element loads; no centroid/constant-load replacement.
            local_load = np.zeros((cells, 3), dtype=np.complex128)
            owner_out = np.zeros(2064, dtype=np.complex128)
            for begin in range(0, len(cut_cell), 50000):
                end = min(begin + 50000, len(cut_cell))
                a = values[begin:end]
                u = phi[nodes[begin:end]] - voltage[external_owner[owner[begin:end]], None]
                action = np.column_stack((a[:, 0]*u[:, 0] + a[:, 1]*u[:, 1] + a[:, 2]*u[:, 2],
                                          a[:, 1]*u[:, 0] + a[:, 3]*u[:, 1] + a[:, 4]*u[:, 2],
                                          a[:, 2]*u[:, 0] + a[:, 4]*u[:, 1] + a[:, 5]*u[:, 2]))
                action *= (coefficient * density[owner[begin:end]])[:, None]
                np.add.at(local_load, cut_cell[begin:end], -action)
                np.add.at(owner_out, owner[begin:end], action.sum(axis=1))
                budget.check('saved local mass load')
            cap_matrix = sheet.read_csc(mass, 'capacitance', data_key='capacitance_data_f')
            sparse_gc_action = coefficient * (cap_matrix @ np.r_[phi, voltage[external]])
            node_load = scatter_nodes(triangles, local_load, count)
            recovered_external = np.zeros(len(external), dtype=np.complex128)
            np.add.at(recovered_external, np.searchsorted(external, external_owner), -owner_out)
            replay = np.r_[-node_load, recovered_external]
            gc_error = float(np.linalg.norm(replay - sparse_gc_action) / np.linalg.norm(sparse_gc_action))
            assert gc_error < 2e-10
            fingerprints = mass['owner_fingerprints_json_utf8']
        del values, nodes, cap_matrix, sorted_keys, triangle_keys, order, pos, cut_keys
        budget.check('exact source owner and GC action replay')

        # J=-g grad(phi); int grad(psi_z).J = -(K_K phi)_z.
        # Subtract the local reference potential before gradients for stability.
        local_sheet = np.zeros_like(local_load)
        current = np.zeros((cells, 2), dtype=np.complex128)
        areas = np.empty(cells)
        for begin in range(0, cells, 50000):
            end = min(begin + 50000, cells)
            tri = triangles[begin:end]
            p = xy[tri]
            p = (p - p[:, :1]) * 1e-6
            det = p[:, 1, 0]*p[:, 2, 1] - p[:, 1, 1]*p[:, 2, 0]
            assert np.all(abs(det) > 0)
            gradients = np.stack((p[:, [1, 2, 0], 1] - p[:, [2, 0, 1], 1],
                                  p[:, [2, 0, 1], 0] - p[:, [1, 2, 0], 0]), axis=2) / det[:, None, None]
            u = phi[tri] - phi[tri[:, :1]]
            grad_phi = np.einsum('ni,nid->nd', u, gradients)
            areas[begin:end] = abs(det) / 2
            current[begin:end] = -sheet.SHEET_CONDUCTANCE_S * grad_phi
            local_sheet[begin:end] = (sheet.SHEET_CONDUCTANCE_S * areas[begin:end, None] *
                                      np.einsum('nid,nd->ni', gradients, grad_phi))
            budget.check('P1 local source gradients')
        coverage = area_map.data / (areas[area_map.indices] * 1e12)
        assert float(coverage.max()) < 1 + 2e-9
        sheet_action = scatter_nodes(triangles, local_sheet, count)
        reference_sheet = sheet.SHEET_CONDUCTANCE_S * (stiffness @ phi)
        sheet_replay_abs = float(abs(sheet_action - reference_sheet).max())
        assert sheet_replay_abs < 1e-9
        patch_load = local_load - local_sheet
        compatibility = scatter_nodes(triangles, patch_load, count)
        free_compatibility = float(abs(compatibility[free_nodes]).max())
        assert free_compatibility < 1e-9
        # Contact nodes are electrodes, not independently source-free weak rows.
        contact_gc = np.zeros(38856, dtype=np.complex128)
        contact_cell = np.flatnonzero(contact >= 0)
        np.add.at(contact_gc, contact[contact_cell], -local_load[contact_cell].sum(axis=1))
        constant_partition_error = float(abs(patch_load.sum(axis=1) - local_load.sum(axis=1)).max())
        assert constant_partition_error < 1e-9
        contact_c_fraction = float(np.sum(area_map[:, contact_cell].sum(axis=1).A.ravel() * density)
                                   / capacitance.sum())
        target = output / 'l02-cell-gc-loads.npz'
        np.savez_compressed(target, **sparse_arrays(area_map, 'owner_cell_area_um2'),
            owner_density_f_per_um2=density, owner_capacitance_f=capacitance,
            owner_external_active_indices=external_owner, owner_fingerprints_json_utf8=fingerprints,
            triangle_potential_index=cell_potential, triangle_contact_index=contact,
            free_triangle_indices=free_cells, cell_area_m2=areas,
            p1_local_gc_injection_a=local_load, p1_vertex_patch_divergence_load_a=patch_load,
            p1_cell_current_a_per_m=current, p1_free_node_compatibility_a=compatibility[free_nodes],
            free_mesh_node_indices=free_nodes, owner_gc_outgoing_current_a=owner_out,
            contact_interior_gc_outgoing_current_a=contact_gc,
            frequency_hz=np.array([1e6]), gc_frequency_scale_s=np.array([coefficient]))
        budget.check('saved source cell loads')
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_SAVED_L02_CELL_GC_TRANSFER_AND_P1_PATCH_LOAD_PREPARATION',
            input_sha256={name: dict(path=str(p), sha256=h) for name, (p, h) in PINS.items()},
            driver_sha256=recon._sha256_file(Path(__file__)), artifact_sha256=recon._sha256_file(target),
            elapsed_s=monotonic()-started, budget=budget.receipt(),
            counts=dict(triangles=cells, source_cuts=len(cut_cell), owners=2064,
                        owner_cell_nonzeros=area_map.nnz, free_nodes=len(free_nodes)),
            metrics=dict(owner_area_relative=area_error, owner_total_c_relative=cap_error,
                total_capacitance_f=float(capacitance.sum()), maximum_owner_cell_coverage=float(coverage.max()),
                saved_sparse_gc_action_relative=gc_error, geometric_sheet_action_max_difference_a=sheet_replay_abs,
                free_vertex_patch_compatibility_max_a=free_compatibility,
                partition_of_unity_max_difference_a=constant_partition_error,
                contact_interior_capacitance_fraction=contact_c_fraction),
            scope='Exact saved clipped P1 loads and source-owned P0 area transfer only. '
                  'No new geometry integration, LU, FMM, recovered H(div) field, magnetic action '
                  'or board response. Patch compatibility is measured to floating-point tolerance, '
                  'not projected to zero. The stored P1 gradient is NOT an admissible conservative '
                  'magnetic source. Contact interior G/C is retained separately; a P0 potential '
                  'changes discretization despite preserving source ownership and constant C. '
                  'This is the old L02-only 1MHz field, not the latest 23.7629% candidate.')
        (output / 'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2), flush=True)
    except Exception:
        (output / 'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc()), indent=2))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
