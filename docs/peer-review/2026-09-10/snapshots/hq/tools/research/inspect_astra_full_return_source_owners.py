"""Bind all conditional port-incident finite edges to cached source owners; no field."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
from time import monotonic

import numpy as np
import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    'numeric_helper': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'budget_helper': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
    'pack': (R/'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz', '01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c'),
    'map': (R/'astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz', 'a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469'),
    'raw': (R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz', '6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7'),
    'inventory': (R/'astra-3d-source-domain-inventory-01/result.json', 'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663'),
}


def endpoint_complement(endpoints, matches, landing_nodes, outward_sign):
    upper = 0 if outward_sign == 1 else 1
    if (len(endpoints) != 2 or len(matches[upper]) != 1 or matches[1-upper]
            or matches[upper][0] not in endpoints & landing_nodes):
        return None
    top = matches[upper][0]
    return [top, next(iter(endpoints-{top}))]


def run(output):
    started = monotonic()
    budget = recon._Budget.create(90, 4)
    for name, (path, digest) in PINS.items():
        assert base.sha(path) == digest, name
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(PINS['pack'][0], allow_pickle=False) as z:
        first, second, admittance = (z[k] for k in ('finite_first_active_index', 'finite_second_active_index', 'finite_admittance_s'))
    with np.load(PINS['map'][0], allow_pickle=False) as z:
        native_rows, legs = (z[k] for k in ('final_finite_native_active_row', 'final_finite_split_leg'))
        assert np.array_equal(admittance, z['final_finite_admittance_s'])
        old_first, old_second = z['final_finite_first_active_index'], z['final_finite_second_active_index']
    assert len(first) == len(native_rows) == 1692409
    edges = []
    for role, port in (('power', 2699), ('ground', 2656)):
        rows = np.flatnonzero((first == port) ^ (second == port))
        assert np.array_equal(rows, np.flatnonzero((old_first == port) ^ (old_second == port)))
        assert np.all(np.isin(legs[rows], (-1, 0, 1))) and len(np.unique(native_rows[rows])) == len(rows)
        for row in rows:
            edges.append({'role': role, 'port': port, 'final_finite_edge_index': int(row),
                          'native_active_edge_index': int(native_rows[row]), 'existing_split_leg': int(legs[row]),
                          'first_active_index': int(first[row]), 'second_active_index': int(second[row]),
                          'outward_sign': 1 if first[row] == port else -1})
    selected_native = np.asarray([edge['native_active_edge_index'] for edge in edges])
    assert np.all(selected_native >= 0) and len(np.unique(selected_native)) == len(edges)
    del admittance, native_rows, legs, old_first, old_second, first, second
    with np.load(PINS['raw'][0], allow_pickle=False) as z:
        original = z['finite_active_original_indices'][selected_native]
        counts = z['finite_count'][selected_native]
        assert np.all(counts == counts.astype(np.int64))
        raw_first, raw_second = z['finite_first_active_indices'][selected_native], z['finite_second_active_indices'][selected_native]
        for edge, f, s in zip(edges, raw_first, raw_second):
            assert (f == edge['port']) == (edge['first_active_index'] == edge['port'])
            assert (s == edge['port']) == (edge['second_active_index'] == edge['port'])
        for key, target in (('all_finite_link_ids', 'quotient_edge_id'),
                            ('all_finite_first_node_ids', 'first_quotient_vertex'),
                            ('all_finite_second_node_ids', 'second_quotient_vertex'),
                            ('all_finite_link_owner_ids_json', 'source_owners')):
            text = recon._decode_text_vector(z[key], key)
            for edge, index in zip(edges, original):
                edge[target] = json.loads(text[index]) if target == 'source_owners' else text[index]
            del text
        for edge, count in zip(edges, counts):
            edge['native_parallel_count'] = int(count)
    budget.check('actual all-port edge identity')
    inventory = json.loads(PINS['inventory'][0].read_bytes())
    connections = []
    try:
        for name, meta_name in (('raw_spatial', 'raw_meta'), ('compiled_topology', 'compiled_meta')):
            info = inventory['inputs'][name]
            path = ROOT/info['path']
            assert path.stat().st_size == info['size_bytes']
            connection = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)
            connections.append(connection)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.set_progress_handler(lambda: int(monotonic()-started > 90), 10000)
            meta = dict(connection.execute('SELECT key,value FROM meta'))
            assert all(meta[key] == value for key, value in inventory['cache_identity'][meta_name].items())
        raw, compiled = connections
        via_ids = {owner[4:] for edge in edges for owner in edge['source_owners'] if owner.startswith('via:')}
        vias, nodes = {}, {}
        for key in sorted(via_ids):
            found = raw.execute('SELECT * FROM vias WHERE via_id_fold=?', (key,)).fetchone()
            assert found is not None, key
            vias[key] = dict(found)
            for endpoint in (found['start_node_id_fold'], found['end_node_id_fold']):
                if endpoint not in nodes:
                    node = raw.execute('SELECT * FROM nodes WHERE node_id_fold=?', (endpoint,)).fetchone()
                    assert node is not None, endpoint
                    nodes[endpoint] = dict(node)
            budget.check('indexed source via/node lookup')
        wanted = {(owner[4:], edge['quotient_edge_id']) for edge in edges
                  for owner in edge['source_owners'] if owner.startswith('via:')}
        landing, vertices = defaultdict(set), defaultdict(set)
        # ponytail: one streaming scan per kind; add a saved subset only if repeated reuse warrants it.
        for row in compiled.execute('SELECT first_key,second_key,value FROM landing_bindings WHERE kind=1'):
            if (row['first_key'], row['value']) in wanted:
                landing[row['first_key'], row['value']].add(row['second_key'])
        needed_nodes = set(nodes) | {node for group in landing.values() for node in group}
        for row in compiled.execute('SELECT second_key,value FROM landing_bindings WHERE kind=0'):
            if row['second_key'] in needed_nodes:
                vertices[row['second_key']].add(row['value'])
        for edge in edges:
            own = edge['source_owners']
            edge['binding_status'] = 'UNRESOLVED_MULTIPLE_OR_NONVIA_OWNER'
            if edge['existing_split_leg'] != -1:
                edge['binding_status'] = 'EXISTING_SPLIT_COMPOSITE_LEG_SOURCE_PENDING'
            elif len(own) == 1 and own[0].startswith('via:') and edge['native_parallel_count'] == 1:
                via = own[0][4:]
                assert vias[via]['via_id_fold'] == via
                assert edge['first_quotient_vertex'] != edge['second_quotient_vertex']
                candidates = landing[via, edge['quotient_edge_id']]
                endpoints = {vias[via]['start_node_id_fold'], vias[via]['end_node_id_fold']}
                edge['compiled_landing_source_node_ids'] = sorted(candidates)
                matches = [[node for node in sorted(endpoints) if edge[key] in vertices[node]]
                           for key in ('first_quotient_vertex', 'second_quotient_vertex')]
                edge['source_endpoint_candidates'] = matches
                if candidates & endpoints and all(len(group) == 1 for group in matches) and {group[0] for group in matches} == endpoints:
                    edge['binding_status'] = 'BOUND_SINGLE_SOURCE_VIA_ENDPOINTS'
                    edge['outward_source_node_ids'] = [group[0] for group in (matches if edge['outward_sign'] == 1 else matches[::-1])]
                else:
                    edge['binding_status'] = 'UNRESOLVED_SOURCE_ENDPOINT_JOIN'
                    derived = endpoint_complement(endpoints, matches, candidates, edge['outward_sign'])
                    if derived is not None:
                        edge['binding_status'] = 'DERIVED_SINGLE_VIA_ENDPOINT_COMPLEMENT'
                        edge['outward_source_node_ids'] = derived
    finally:
        for connection in connections:
            connection.close()
    budget.check('source ownership and endpoint joins')
    target = output/'source-owner-inventory.json'
    base.atomic_json(target, {'edges': edges, 'source_vias': vias, 'source_nodes': nodes})
    result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
              'status': 'COMPLETED_ALL_PORT_SOURCE_OWNER_INVENTORY_NO_FIELD',
              'driver': base.receipt(Path(__file__)),
              'inputs': {name: base.receipt(path) for name, (path, _) in PINS.items()},
              'cache_identity': inventory['cache_identity'], 'cache_byte_hash_recomputed': False,
              'role_counts': dict(Counter(edge['role'] for edge in edges)),
              'binding_counts': dict(Counter(edge['binding_status'] for edge in edges)),
              'unique_via_count': len(vias), 'source_node_count': len(nodes),
              'endpoint_complement_rule': 'The exact singleton/count-one finite edge has two distinct quotient endpoints and one source via with two distinct raw endpoints. Its unique outward terminal landing binds one endpoint; the other endpoint is its complement. The lower connection is derived, not a direct landing-cache row.',
              'output': base.receipt(target), 'budget': budget.receipt(),
              'scope': 'Field-independent source identity and endpoint inventory for every conditional P/G-port finite edge. No reused current, cylinder approximation, padstack material qualification, source mesh, magnetic coupling, physical field acceptance or reference comparison. Unresolved owners are explicit.'}
    base.atomic_json(output/'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    assert endpoint_complement({'a', 'b'}, [['a'], []], {'a'}, 1) == ['a', 'b']
    assert endpoint_complement({'a', 'b'}, [[], ['b']], {'b'}, -1) == ['b', 'a']
    assert endpoint_complement({'a', 'b'}, [[], []], {'a'}, 1) is None
    assert endpoint_complement({'a', 'b'}, [['a', 'b'], []], {'a'}, 1) is None
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.output.resolve())
    except Exception as error:
        if args.output.is_dir() and not (args.output/'failure.json').exists():
            base.atomic_json(args.output/'failure.json', {'status': 'STOP_PORT_SOURCE_OWNER_INVENTORY', 'error': repr(error)})
        raise
