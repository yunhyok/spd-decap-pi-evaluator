"""SPD Decap PI Evaluator v0.23.1: selected source path to retained L14 P1 boundary.

Exports incidence and the existing electrode KCL row, without solving or adding
physical coefficients. The two artwork links remain explicit unresolved links.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from run_astra_l14_sheet_r_shadow import read_csc

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run():
    geometry_path = R / 'astra-port18-selected-pin-geometry-20260912.json'
    assert digest(geometry_path) == 'a84a2e6a4febc8a4cc45b25168cd1394c117b6a631b940fe920bc3bc3dc7f211'
    geometry = json.loads(geometry_path.read_bytes())
    segments = geometry['segments']
    source_nodes, active_by_source, edges, owners = [], {}, [], []
    for index, segment in enumerate(segments):
        via, row = segment['via'], segment['chain_row']
        start, end = via['start_node_id'], via['end_node_id']
        for node, active in ((start, row['from_active']),
                             (end, 757823 if index == 12 else row['to_active'])):
            if node not in source_nodes:
                source_nodes.append(node)
            assert active_by_source.get(node, active) == active
            active_by_source[node] = active
        edges.append((start, end))
        owners.append(via['via_id'])
    for join in geometry['source_node_continuity']:
        if not join['exact_source_node_match']:
            edges.append((join['from_end_node_id'], join['to_start_node_id']))
            owners.append('unresolved-artwork:' + join['from_end_node_id'])
    assert len(source_nodes) == 16 and len(edges) == 15
    active = np.unique(list(active_by_source.values()))
    assert len(active) == 14 and 718402 not in active and 757823 in active
    incidence = np.zeros((16, 15))
    for column, (start, end) in enumerate(edges):
        incidence[source_nodes.index(start), column] = -1
        incidence[source_nodes.index(end), column] = 1
    prolongation = np.zeros((16, 14))
    for row, node in enumerate(source_nodes):
        prolongation[row, np.searchsorted(active, active_by_source[node])] = 1
    # Quotient compatibility only: the physical artwork impedances are unknown.
    projected = prolongation.T @ incidence
    assert np.array_equal(projected[:, 13:], np.zeros((14, 2)))
    assert np.array_equal(incidence.sum(axis=0), np.zeros(15))
    assert np.linalg.matrix_rank(incidence) == 15
    assert np.linalg.matrix_rank(projected[:, :13]) == 13

    raw_path = R / 'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz'
    pack_path = R / 'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz'
    with np.load(pack_path, allow_pickle=False) as pack:
        native_rows = pack['final_finite_native_active_row']
        first, second = pack['finite_first_active_index'], pack['finite_second_active_index']
        split = pack['final_finite_split_leg']
        for index, segment in enumerate(segments):
            row = segment['chain_row']
            matches = np.flatnonzero(native_rows == row['native_row'])
            assert matches.tolist() == [row['pack_row']]
            j = matches[0]
            assert split[j] == -1
            oriented = np.zeros(14)
            oriented[np.searchsorted(active, first[j])] = -row['direction']
            oriented[np.searchsorted(active, second[j])] = row['direction']
            assert np.array_equal(oriented, projected[:, index])

    mass_receipt_path = R / 'astra-l14-gc-mass-05/receipt.json'
    receipt = json.loads(mass_receipt_path.read_bytes())
    mass_path = Path(receipt['output']['path'])
    assert digest(mass_path) == receipt['output']['sha256']
    drive_path = R / 'astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz'
    assert digest(drive_path) == '05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d'
    with np.load(drive_path, allow_pickle=False) as drive:
        full_to_contracted = drive['full_to_contracted']
        sheet = read_csc(drive, 'conductance')
    with np.load(mass_path, allow_pickle=False) as archive:
        mass = sparse.csc_matrix((archive['physical_gc_y_data_s'],
                                 archive['physical_gc_y_indices'],
                                 archive['physical_gc_y_indptr']),
                                shape=tuple(archive['physical_gc_y_shape']))
    with np.load(raw_path, allow_pickle=False) as raw:
        external = raw['global_to_active_indices'][receipt['owner_binding']['external_global_reduced_indices']]
        positive, negative = raw['global_to_active_indices'][raw['solve_port_reduced_nodes'][int(raw['batch_port_indices'][0])]]
        gauge = int(raw['gauge_active_index'][0])
    assert (positive, negative, gauge) == (2699, 2656, 0)
    sheet_active = np.r_[718402, np.arange(756889, 903945)]
    assert sheet.shape == (len(sheet_active), len(sheet_active))
    mapping = np.r_[sheet_active[full_to_contracted], external]
    support = np.flatnonzero(full_to_contracted == 935)
    expected = geometry['l14_terminal_pad']['conditional_2d_filled_core_electrode']['full_basis_ids']
    assert support.tolist() == expected and len(support) == 16
    assert np.all(mapping[support] == 757823)
    # Sum covectors, never divide the electrode current equally across P1 nodes.
    gc_full_row = sparse.csr_matrix(mass[support, :].sum(axis=0))
    gc_coo = gc_full_row.tocoo()
    dc_row = sheet.getrow(935).tocoo()
    boundary_active = np.unique(np.r_[mapping[gc_coo.col], sheet_active[dc_row.col], 757823])
    gc_row = sparse.coo_matrix((gc_coo.data, (np.zeros(gc_coo.nnz, dtype=int),
                              np.searchsorted(boundary_active, mapping[gc_coo.col]))),
                              shape=(1, len(boundary_active))).tocsr()
    dc = sparse.coo_matrix((dc_row.data, (np.zeros(dc_row.nnz, dtype=int),
                          np.searchsorted(boundary_active, sheet_active[dc_row.col]))),
                          shape=gc_row.shape).tocsr()
    # Deterministic nonconstant voltage exercises the actual trace/adjoint map.
    values = lambda ids: np.sin(ids * .0017) + 1j * np.cos(ids * .0023)
    voltage = values(boundary_active)
    direct = complex((mass @ values(mapping))[support].sum())
    contracted = complex((gc_row @ voltage)[0])
    gc_error = abs(direct - contracted) / max(abs(direct), 1e-30)
    assert gc_error < 2e-12
    assert abs(complex(gc_row.sum())) < 2e-12 * np.abs(gc_row.data).sum()
    assert abs(float(dc.sum())) < 2e-12 * np.abs(dc.data).sum()
    # Existing frequency stamp only: Im(Y)/omega is its capacitance, not a
    # frequency-independent material model for extrapolation to other bands.
    capacitance_row = gc_row.imag / (2 * np.pi * receipt['frequency_hz'])
    q = complex((capacitance_row @ voltage)[0])
    leakage = complex((gc_row.real @ voltage)[0])
    continuity_error = abs(contracted - leakage - 2j*np.pi*receipt['frequency_hz']*q)
    assert continuity_error < 2e-12 * max(abs(contracted), 1e-30)
    output = R / 'astra-port18-path-boundary-20260912'
    output.mkdir(exist_ok=False)
    np.savez_compressed(output / 'coupling.npz', source_node_ids=np.array(source_nodes),
                        edge_owners=np.array(owners), source_incidence=incidence,
                        source_voltage_prolongation=prolongation,
                        retained_active_indices=active, retained_via_incidence=projected[:, :13],
                        l14_full_to_active=mapping, l14_electrode_full_basis=support,
                        boundary_active_indices=boundary_active,
                        electrode_gc_row_s=gc_row.toarray()[0], electrode_dc_row_s=dc.toarray()[0],
                        electrode_capacitance_row_f=capacitance_row.toarray()[0])
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='ASSEMBLED_SOURCE_PATH_AND_EXISTING_L14_BOUNDARY_NOT_FIELD_REPLACEMENT',
                  source_nodes=16, via_currents=13, unresolved_artwork_currents=2,
                  retained_voltage_nodes=14, l14_global=757823, l14_electrode=935,
                  electrode_basis_nodes=16, boundary_columns=len(boundary_active),
                  external_gc_active_indices=external.tolist(), frequency_hz=receipt['frequency_hz'],
                  checks=dict(source_path_connected=True, current_conservation=True,
                              thirteen_existing_pack_orientations_match=True,
                              electrode_trace_adjoint_relative_error=gc_error,
                              charge_continuity_absolute_error_a=continuity_error),
                  application='Use retained_via_incidence for the 13 oriented currents; at node 757823 add the saved sheet DC and GC boundary row actions. These are existing terms, not extra parallel stamps. Keep all other board KCL rows, source injection and global return unchanged.',
                  field_replacement_ready=False,
                  limitations=['Two artwork links have no physical impedance assigned; their existing quotient collapse is explicit.',
                               'TOP electrode current distribution is unresolved; pin2699 remains the existing shared source node.',
                               'L14 boundary is the existing 20um core, not the restored 30um pad.',
                               'Other L14 contact currents and all nonselected branch/GC/termination rows remain external and must be preserved.',
                               'No source charge is added on top of the existing GC owners; no 3D closure, new Z, convergence or accuracy claim.'],
                  inputs={p.name: dict(path=str(p), sha256=digest(p)) for p in
                          (geometry_path, drive_path, mass_receipt_path, mass_path)},
                  driver_sha256=digest(Path(__file__)), output_sha256=digest(output / 'coupling.npz'))
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('status', 'boundary_columns', 'external_gc_active_indices', 'checks')}))


if __name__ == '__main__':
    run()
