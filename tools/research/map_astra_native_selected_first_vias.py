"""Map the 1,956 selected physical first vias to the saved native 1 MHz field.

This produces an interface/removal ledger, not a new board solution. The native
snapshot predates the conditional 23.7629% candidate; its sensitivity is local
to that saved circuit and does not bound a physical-model replacement.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from time import monotonic

import numpy as np

import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'outputs/research'
PINS = {
    'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz':
        '6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7',
    'astra-device-first-via-sources-01/selected-device-first-via-sources.json':
        '35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303',
}


def pair(value):
    return [float(value.real), float(value.imag)]


def run(output):
    start = monotonic()
    budget = recon._Budget.create(45, 2)
    assert not output.exists()
    for path, digest in PINS.items():
        assert recon._sha256_file(BASE / path) == digest, path
    budget.check('input hashes')
    source = json.loads((BASE / next(p for p in PINS if p.endswith('.json'))).read_text())
    records = source['records']
    assert len(records) == 1956
    assert Counter(r['role'] for r in records) == {'power': 978, 'ground': 978}
    assert len({r['contact']['pin_id'] for r in records}) == 1956
    wanted = {r['contact']['first_via_quotient_edge_id'] for r in records}
    assert len(wanted) == 1956
    vias = {r['via_id_fold']: r for r in source['source_vias']}
    nodes = {r['node_id_fold']: r for r in source['source_nodes']}

    with np.load(BASE / next(p for p in PINS if p.endswith('.npz'))) as raw:
        ids = recon._decode_text_vector(raw['all_finite_link_ids'], 'finite IDs')
        selected = {key: i for i, key in enumerate(ids) if key in wanted}
        assert len(selected) == 1956
        original = np.array([selected[r['contact']['first_via_quotient_edge_id']]
                             for r in records], dtype=np.int64)
        del ids
        active_original = raw['finite_active_original_indices']
        order = np.argsort(active_original)
        where = np.searchsorted(active_original[order], original)
        assert np.all(where < len(order))
        active_edge = order[where]
        assert np.array_equal(active_original[active_edge], original)
        first_ids = recon._decode_text_vector(raw['all_finite_first_node_ids'], 'first IDs')
        first = [first_ids[i] for i in original]
        del first_ids
        second_ids = recon._decode_text_vector(raw['all_finite_second_node_ids'], 'second IDs')
        second = [second_ids[i] for i in original]
        del second_ids
        owners = recon._decode_text_vector(raw['all_finite_link_owner_ids_json'], 'owners')
        selected_owners = [json.loads(owners[i]) for i in original]
        del owners
        budget.check('selected native identities')
        orientation = np.empty(1956, dtype=np.int64)
        details = []
        for i, record in enumerate(records):
            contact = record['contact']
            assert contact['status'] == 'complete'
            top = contact['exposed_quotient_vertex_id']
            assert (first[i] == top) != (second[i] == top)
            orientation[i] = 1 if first[i] == top else -1
            via = vias[contact['incident_via_id'].casefold()]
            assert selected_owners[i] == ['via:' + via['via_id_fold']]
            top_node = nodes[record['first_via_source_node_id']]
            assert top_node['layer_id'] == 'Signal$TOP'
            endpoints = {via['start_node_id_fold'], via['end_node_id_fold']}
            assert record['first_via_source_node_id'] in endpoints and len(endpoints) == 2
            lower_node = nodes[(endpoints - {record['first_via_source_node_id']}).pop()]
            assert lower_node['layer_id'] == 'Signal$L02(DGND)'
            assert top_node['net_fold'] == lower_node['net_fold'] == contact['net'].casefold()
            assert (top_node['x_pm'], top_node['y_pm']) == (lower_node['x_pm'], lower_node['y_pm'])
            details.append(dict(pin_id=contact['pin_id'], role=record['role'],
                via_id=via['via_id'], edge_id=contact['first_via_quotient_edge_id'],
                upper_source_node=top_node['node_id'], lower_source_node=lower_node['node_id'],
                xy_pm=[top_node['x_pm'], top_node['y_pm']], source_record_sha256=via['source_record_sha256']))

        f = raw['finite_first_active_indices'][active_edge]
        s = raw['finite_second_active_indices'][active_edge]
        top_row = np.where(orientation == 1, f, s)
        lower_row = np.where(orientation == 1, s, f)
        assert np.all(top_row != lower_row)
        global_to_active = raw['global_to_active_indices']
        for key, expected in [('all_finite_first_global_reduced_indices', f),
                              ('all_finite_second_global_reduced_indices', s)]:
            assert np.array_equal(global_to_active[raw[key][original]], expected)
        count = raw['finite_count'][active_edge]
        resistance = raw['finite_resistance_ohm_per_via'][active_edge]
        inductance = raw['finite_inductance_h_per_via'][active_edge]
        assert np.all(count == 1) and np.all(resistance > 0) and np.all(inductance > 0)
        for key, expected in [('all_finite_count', count),
                              ('all_finite_resistance_ohm_per_via', resistance),
                              ('all_finite_inductance_h_per_via', inductance)]:
            assert np.array_equal(raw[key][original], expected)
        frequency = float(raw['frequency_hz'][0])
        assert frequency == 1e6
        voltage = raw['active_voltage'][:, 0]
        assert raw['batch_port_indices'].tolist() == [0]
        port_rows = global_to_active[raw['solve_port_reduced_nodes'][0]]
        retained = raw['retained_active_indices']
        rhs = np.zeros(len(voltage))
        rhs[port_rows] = [1, -1]
        assert np.array_equal((raw['row_scale'] * rhs[retained])[:, None], raw['scaled_rhs'])
        port_z = voltage[port_rows[0]] - voltage[port_rows[1]]
        assert abs(port_z - recon.EXPECTED_ZDD_OHM) < 1e-15
        series_z = (resistance + 2j * np.pi * frequency * inductance) / count
        current = (voltage[top_row] - voltage[lower_row]) / series_z
        native_current = (voltage[f] - voltage[s]) / series_z
        assert np.allclose(current, orientation * native_current, rtol=1e-14, atol=0)
        incident_metrics = {}
        external_arrays = {}
        all_f, all_s = raw['finite_first_active_indices'], raw['finite_second_active_indices']
        all_r, all_l = raw['finite_resistance_ohm_per_via'], raw['finite_inductance_h_per_via']
        all_n = raw['finite_count']
        for role, port, drive in [('power', port_rows[0], 1), ('ground', port_rows[1], -1)]:
            incident = np.flatnonzero((all_f == port) ^ (all_s == port))
            outside = incident[~np.isin(incident, active_edge)]
            role_values = {}
            for label, ix in [('all', incident), ('outside_selected', outside)]:
                z = (all_r[ix] + 2j * np.pi * frequency * all_l[ix]) / all_n[ix]
                direction = np.where(all_f[ix] == port, 1, -1)
                branch_i = direction * (voltage[all_f[ix]] - voltage[all_s[ix]]) / z
                role_values[label] = dict(edge_count=len(ix),
                    outward_current_sum_a=pair(branch_i.sum()),
                    joule_w=float(np.sum(z.real * abs(branch_i) ** 2)))
                if label == 'all':
                    assert abs(branch_i.sum() - drive) < 1e-8
                else:
                    external_arrays[role + '_outside_active_edge_index'] = ix
                    external_arrays[role + '_outside_top_outward_current_a'] = branch_i
            incident_metrics[role] = role_values
        budget.check('selected currents')

    roles = np.array([r['role'] for r in records])
    metrics = {}
    for role, port in [('power', port_rows[0]), ('ground', port_rows[1])]:
        mask = roles == role
        assert np.all(top_row[mask] == port)
        currents = current[mask]
        metrics[role] = dict(pin_count=int(mask.sum()),
            unique_native_top_rows=int(len(np.unique(top_row[mask]))),
            unique_native_lower_rows=int(len(np.unique(lower_row[mask]))),
            top_outward_current_sum_a=pair(currents.sum()),
            rms_pin_current_a=float(np.sqrt(np.mean(abs(currents) ** 2))),
            first_via_joule_w=float(np.sum(resistance[mask] * abs(currents) ** 2)),
            log_series_z_scale_derivative_ohm=pair(np.sum(series_z[mask] * currents ** 2)))
    # Keep every pin column, including identical native G columns. Coalescing
    # these columns would lose the independent physical interface identities.
    incidence_rows = np.stack([top_row, lower_row])
    incidence_values = np.tile([[1], [-1]], (1, 1956))
    assert np.all(incidence_values.sum(axis=0) == 0)
    joule = float(np.sum(resistance * abs(current) ** 2))
    sensitivity = np.sum(series_z * current ** 2)
    output.mkdir(parents=True)
    np.savez_compressed(output / 'selected-native-first-vias.npz',
        original_edge_index=original, active_edge_index=active_edge,
        native_orientation_from_top=orientation, native_top_row=top_row,
        native_lower_row=lower_row, native_incidence_rows=incidence_rows,
        native_incidence_values=incidence_values, pin_id=np.array([d['pin_id'] for d in details]),
        role=roles, resistance_ohm=resistance, inductance_h=inductance,
        count=count, native_top_to_lower_current_a=current, port_rows=port_rows,
        native_active_node_count=np.array([len(voltage)]), **external_arrays)
    (output / 'pin-edge-map.json').write_text(json.dumps(details, indent=2) + '\n')
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='VERIFIED_NATIVE_INTERFACE_MAP_AND_SAVED_FIELD_DIAGNOSTIC',
        input_sha256=PINS, driver_sha256=recon._sha256_file(Path(__file__)),
        frequency_hz=frequency, saved_baseline_zdd_ohm=pair(port_z), roles=metrics,
        actual_native_port_incident_finite_edges=incident_metrics,
        selected_first_via_joule_w=joule,
        selected_first_via_joule_fraction_of_port_real_power=joule / port_z.real,
        log_selected_series_z_scale_derivative_ohm=pair(sensitivity),
        derivative_magnitude_over_saved_port_z=float(abs(sensitivity) / abs(port_z)),
        elapsed_s=monotonic() - start, budget=budget.receipt(),
        artifacts={p.name: recon._sha256_file(p) for p in output.iterdir()},
        scope='No board solve, new operator stamp or accuracy claim. Exact 1956 pin/first-via '
              'edge/source-node/native-row join with separate columns. Native first-via scalar '
              'series stamps are identified for removal, but their removal alone is not a '
              'complete replacement: new pad/plane, P-G mutual, charge and external-continuation '
              'operators are required. Native G lower rows are ideal-potential quotient rows, '
              'not distinct physical contacts. All native external G paths remain recorded; '
              'their old current split does not predict the split in a new physical model. '
              'Joule is saved-circuit dissipation only; '
              'sum(I^2 Z) is the infinitesimal reciprocal-circuit derivative for scaling '
              'these existing series impedances, not a finite-change bound or the derivative '
              'of missing geometry/coupling. Original first-via cache alone does not prove '
              'physical pin-pad location; subsequent pad evidence is separate.')
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
