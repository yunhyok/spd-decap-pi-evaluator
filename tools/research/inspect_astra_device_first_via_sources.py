"""SPD Decap PI Evaluator v0.23.1: recover cached Device first-via sources.

This does not identify physical pin pads: the compact contact view omits the
original endpoint and direct-via/trace-path distinction. No raw SPD is read.
"""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = 'outputs/research/astra-3d-source-domain-inventory-01/result.json'
INVENTORY_SHA = 'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663'
EXTERNAL_SHA = '9152f371671f71b8c310684c898705e4f2e78c8a36e17355a5df9beeba349117'
RAIL = 'ADC_VDD_075_VTRIP_SRAM/0'


def run(output):
    start = monotonic()
    inventory_bytes = (ROOT / INVENTORY).read_bytes()
    assert sha256(inventory_bytes).hexdigest() == INVENTORY_SHA
    inventory = json.loads(inventory_bytes)
    output.mkdir()
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    connections = []
    try:
        for name, meta_name in (('raw_spatial', 'raw_meta'), ('compiled_topology', 'compiled_meta')):
            info = inventory['inputs'][name]
            path = ROOT / info['path']
            assert path.stat().st_size == info['size_bytes']
            connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
            connections.append(connection)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.set_progress_handler(lambda: int(monotonic() - start > 30), 10000)
            meta = dict(connection.execute('SELECT key,value FROM meta'))
            assert all(meta[key] == value for key, value in inventory['cache_identity'][meta_name].items())
        raw, compiled = connections
        port = dict(compiled.execute('SELECT * FROM rail_ports WHERE rail_id=?', (RAIL,)).fetchone())
        assert port['ordinal'] == 34 and port['selected_net'] == RAIL and port['reference_net'] == 'DGND'
        members = {kind: {row[0] for row in compiled.execute(
            'SELECT value FROM rail_port_members WHERE port_ordinal=? AND kind=?', (34, kind))} for kind in range(4)}
        assert tuple(map(len, members.values())) == (978, 978, 45, 1)
        payload, size, digest = compiled.execute(
            "SELECT payload,payload_size,payload_sha256 FROM views WHERE name='external'").fetchone()
        assert len(payload) == size and sha256(payload).hexdigest() == digest == EXTERNAL_SHA
        external = json.loads(payload)
        anchors = [row for row in external['rail_anchor_bindings'] if row['rail_id'] == RAIL]
        contacts = defaultdict(list)
        for row in external['terminal_contacts']:
            if row['pin_id'] in members[0] | members[1]:
                contacts[row['pin_id'], row['net']].append(row)
        vertices = defaultdict(set)
        edges = defaultdict(list)
        for row in compiled.execute('SELECT * FROM landing_bindings ORDER BY kind,ordinal'):
            if row['kind'] == 0:
                vertices[row['second_key']].add(row['value'])
            else:
                edges[row['first_key'], row['value']].append(row['second_key'])
        records, summaries, node_rows, via_rows = [], {}, {}, {}
        for kind, role, net in ((0, 'power', RAIL), (1, 'ground', 'DGND')):
            selected = [row for row in anchors if row['role'] == role]
            assert len(selected) == len(members[kind]) and {row['pin_id'] for row in selected} == members[kind]
            role_records = []
            for anchor in sorted(selected, key=lambda row: row['pin_id']):
                matching = contacts[anchor['pin_id'], net]
                assert len(matching) == 1 and matching[0]['status'] == 'complete'
                contact = matching[0]
                key = contact['incident_via_id'].casefold()
                if key not in via_rows:
                    found = raw.execute('SELECT * FROM vias WHERE via_id_fold=?', (key,)).fetchone()
                    assert found is not None
                    via_rows[key] = dict(found)
                via = via_rows[key]
                assert via['net_fold'] == net.casefold()
                candidates = edges[key, contact['first_via_quotient_edge_id']]
                assert len(candidates) == 1, (anchor['pin_id'], candidates)
                node_key = candidates[0]
                assert node_key in (via['start_node_id_fold'], via['end_node_id_fold'])
                assert contact['exposed_quotient_vertex_id'] in vertices[node_key]
                for endpoint in (via['start_node_id_fold'], via['end_node_id_fold']):
                    if endpoint not in node_rows:
                        found = raw.execute('SELECT * FROM nodes WHERE node_id_fold=?', (endpoint,)).fetchone()
                        assert found is not None
                        node_rows[endpoint] = dict(found)
                    assert node_rows[endpoint]['net_fold'] == net.casefold()
                node = node_rows[node_key]
                record = dict(role=role, anchor=anchor, contact=contact, first_via_source_node_id=node_key,
                              first_via_layer=node['layer_id'], first_via_padstack=node['padstack_id'],
                              first_via_x_pm=node['x_pm'], first_via_y_pm=node['y_pm'],
                              physical_pin_source_node_proven=False)
                records.append(record)
                role_records.append(record)
            assert {row['contact']['exposed_quotient_vertex_id'] for row in role_records} == members[kind + 2]
            summaries[role] = dict(pin_count=len(role_records),
                unique_first_vias=len({row['contact']['incident_via_id'] for row in role_records}),
                unique_first_via_source_nodes=len({row['first_via_source_node_id'] for row in role_records}),
                layer_counts=dict(Counter(row['first_via_layer'] for row in role_records)),
                padstack_counts=dict(Counter(row['first_via_padstack'] for row in role_records)),
                xy_bounds_pm=[[min(row[key] for row in role_records), max(row[key] for row in role_records)]
                              for key in ('first_via_x_pm', 'first_via_y_pm')])
        pad_ids = sorted({row['padstack_id_fold'] for row in list(node_rows.values()) + list(via_rows.values())
                          if row['padstack_id_fold'] is not None})
        stacks = [dict(raw.execute('SELECT * FROM padstacks WHERE padstack_id_fold=?', (key,)).fetchone()) for key in pad_ids]
        shapes = [dict(row) for key in pad_ids for row in raw.execute(
            'SELECT * FROM pad_shapes WHERE padstack_id_fold=? ORDER BY ordinal', (key,))]
    finally:
        for connection in connections:
            connection.close()
    ledger = dict(records=records, source_nodes=list(node_rows.values()), source_vias=list(via_rows.values()),
                  padstacks=stacks, pad_shapes=shapes)
    ledger_bytes = json.dumps(ledger, indent=2, allow_nan=False).encode('utf-8')
    (output / 'selected-device-first-via-sources.json').write_bytes(ledger_bytes)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='VERIFIED_SELECTED_DEVICE_FIRST_VIA_SOURCES__PHYSICAL_PIN_LOCATION_NOT_PROVEN',
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), elapsed_s=monotonic()-start,
        inventory_sha256=INVENTORY_SHA, external_view_sha256=EXTERNAL_SHA,
        cache_identity=inventory['cache_identity']['raw_meta'], cache_byte_hash_recomputed=False,
        ledger_sha256=sha256(ledger_bytes).hexdigest(), ledger_size_bytes=len(ledger_bytes),
        port=port, summaries=summaries, source_node_count=len(node_rows), source_via_count=len(via_rows),
        scope='Exact selected pin/contact/first-via edge/source-node joins, not a physical electrode map. '
              'The compact cache omits contact_path_kind and external_endpoint_node_id: a first via '
              'may be reached through source traces. Matching pad/net/count/quotient membership cannot '
              'prove pin location. No selected ground pin inferred from all DGND pads, no geometry '
              'substitution, new field solve, board result, or PowerSI error attribution.')
    (output / 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    try:
        run(destination)
    except Exception as error:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'failure.json').write_text(json.dumps(dict(error=repr(error))), encoding='utf-8')
        raise
