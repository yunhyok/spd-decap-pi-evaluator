"""SPD Decap PI Evaluator v0.23.1: invert the accepted direct-pin cache contract.

Recover source pad positions, not an equipotential electrode-area prescription.
No raw SPD or cache reread; the prior selected contact/landing joins are reused.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'src/spd_decap_pi/spd_adapter.py': '843b39b5943ce7fbd9e9b0b2f4907dc4ca061e599646d02fb54cea105ab842b2',
    'src/spd_decap_pi/_core/io/spd.py': 'fc17618801367294d1d2c1eabcef1d54db2603cfe88e2d6bfc191a3b484761fe',
    'src/spd_decap_pi/compiled_topology_asset.py': 'd315fd28edc8ec9c6359689325a0a9c75ddf3688098929dc96e2baa578add02f',
    'outputs/research/astra-device-first-via-sources-01/result.json': 'a755ffc21cbb5470dcae7941c5c9301c62bfb2f41f009247d7c524cb2b66b5f8',
    'outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json': '35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303',
}


def contract_check():
    from spd_decap_pi.spd_adapter import _anchor_graph_landings
    from spd_decap_pi._core.io.spd import _parse_vias
    pin = SimpleNamespace(pin_id='SITE0:1', refdes='SITE0', pin='1', terminal='PWR', net='PWR',
        source_node_id='NodePin', source_layer='TOP', source_padstack='DUT', x_um=0., y_um=0.)
    direct = b'Via1::PWR UpperNode = NodePin LowerNode = NodeOther PadStack = DR\n'
    cases = {'direct': direct, 'remote': direct.replace(b'NodePin', b'NodeRemote'),
             'ambiguous': direct + direct.replace(b'Via1', b'Via2')}
    report = {}
    for label, data in cases.items():
        endpoints = _parse_vias(data, 0, len(data), set(), set(), {}, SimpleNamespace(report=lambda *args: None),
            top_layer='TOP', padstacks=(), device_pins=(pin,))[-1]
        landings, contacts = _anchor_graph_landings([{'pin_id': pin.pin_id}], endpoints)
        contact = contacts[pin.pin_id.casefold()]
        assert landings[pin.pin_id.casefold()].endpoint_node_id == pin.source_node_id
        assert bool(contact['incident_via_id']) == (label == 'direct')
        assert contact['contact_path_kind'] == ('direct_via_landing' if label == 'direct' else 'trace_component')
        assert bool(contact['issues']) == (label != 'direct')
        report[label] = dict(status=endpoints[0].status, incident_via_id=contact['incident_via_id'],
                             contact_path_kind=contact['contact_path_kind'], issues=contact['issues'])
    return report


def run(output):
    start = monotonic()
    for path, digest in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == digest, path
    checks = contract_check()
    ledger = json.loads((ROOT/list(PINS)[-1]).read_bytes())
    nodes = {row['node_id_fold']: row for row in ledger['source_nodes']}
    vias = {row['via_id_fold']: row for row in ledger['source_vias']}
    shapes = [row for row in ledger['pad_shapes'] if row['padstack_id_fold'] == 'dut'
              and row['layer_id_fold'] == 'signal$top']
    assert len(shapes) == 1 and shapes[0]['shape_kind'] == 'CIRCLE' and shapes[0]['width_pm'] == 100_000_000
    pads = []
    for row in ledger['records']:
        contact = row['contact']
        assert contact['status'] == 'complete' and contact['incident_via_id']
        node = nodes[row['first_via_source_node_id']]
        via = vias[contact['incident_via_id'].casefold()]
        assert node['node_id_fold'] in (via['start_node_id_fold'], via['end_node_id_fold'])
        assert node['net_fold'] == via['net_fold'] == contact['net'].casefold()
        assert node['layer_id_fold'] == 'signal$top' and node['padstack_id_fold'] == 'dut'
        pads.append(dict(pin_id=contact['pin_id'], role=row['role'], branch_id=row['anchor']['branch_id'],
            net=contact['net'], source_node_id=node['node_id'], source_node_record_sha256=node['source_record_sha256'],
            contact_path_kind='direct_via_landing', recovery_basis='accepted_v4_compiler_contract_and_unique_cached_landing_inverse',
            x_pm=node['x_pm'], y_pm=node['y_pm'], diameter_pm=shapes[0]['width_pm'], layer=node['layer_id'],
            source_pad_shape_ordinal=shapes[0]['ordinal'], source_pad_shape_record_sha256=shapes[0]['source_record_sha256'],
            via_id=via['via_id'], via_record_sha256=via['source_record_sha256'],
            exposed_quotient_vertex_id=contact['exposed_quotient_vertex_id'], first_via_quotient_edge_id=contact['first_via_quotient_edge_id']))
    assert len(pads) == len({row['pin_id'] for row in pads}) == len({row['source_node_id'] for row in pads}) == 1956
    assert all(sum(row['role'] == role for row in pads) == 978 for role in ('power', 'ground'))
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    payload = json.dumps(dict(pads=pads, source_pad_shape=shapes[0]), indent=2, allow_nan=False).encode('utf-8')
    (output/'device-terminal-pads.json').write_bytes(payload)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='RECOVERED_DEVICE_SOURCE_PADS_UNDER_ACCEPTED_COMPILER_CONTRACT', elapsed_s=monotonic()-start,
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        contract_checks=checks, pad_count=1956, power_pad_count=978, ground_pad_count=978,
        pad_diameter_um=100., pads_sha256=sha256(payload).hexdigest(), pads_size_bytes=len(payload),
        scope='The audited parser assigns incident_via_id only at the actual pin source node, '
              'not through a trace walk. Nonempty contact via IDs therefore imply direct_via_landing. '
              'The previously verified unique (via,first-edge) inverse with exposed-vertex and raw '
              'endpoint/net agreement reconstructs each selected source pin node. This supersedes '
              'the earlier conservative position-gap hypothesis, not the frozen ledger bytes. '
              'It relies on accepted source/certificate provenance and the pinned compiler contract; '
              'raw SPD was not revalidated. A source circular pad is not yet a prescribed equipotential '
              'electrode area, complete conductor volume, mesh, solved field or PowerSI accuracy result.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')


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
        (destination/'failure.json').write_text(json.dumps(dict(error=repr(error))), encoding='utf-8')
        raise
