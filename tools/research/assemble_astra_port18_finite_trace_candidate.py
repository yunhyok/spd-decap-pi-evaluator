"""SPD Decap PI Evaluator v0.23.1: source-owned finite Trace split candidate.

Preserves the existing full/conditional coordinate domains. Does not modify or
solve a board operator; exports a15-current/16-voltage local constitutive block.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run():
    boundary = R / 'astra-port18-path-boundary-20260912/coupling.npz'
    geometry = R / 'astra-port18-selected-pin-geometry-20260912.json'
    artwork = R / 'astra-port18-artwork-joins-20260912.json'
    dc = R / 'astra-port18-source-trace-dc-20260912-02/result.json'
    pack_path = R / 'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz'
    conditional_path = R / 'astra-full-contact-frequency-operators-02/frequency-10000000-conditional-operator.npz'
    raw_path = R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz'
    # Native/pack/conditional digests are from the saved frequency assembler
    # receipt; the four new artifacts are fixed completed-run receipts.
    pins = {
        boundary:'8c96f3dfec3ad077e45c673836388872437dab4043cdc158c8a106ddb17a406e',
        geometry:'a84a2e6a4febc8a4cc45b25168cd1394c117b6a631b940fe920bc3bc3dc7f211',
        artwork:'135dd9106035b997609d62e876b1e6b1659fc4a1578df7af2f9390a05902aa91',
        dc:'9aad2e6eccde871e24d57bd56c4298d6a7657e9c4bfb7ba973bbbe32924971a1',
        pack_path:'01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c',
        conditional_path:'7d528de4ced4e24e6fbf1c0ba437276ce8ccc706684e36028f63494685171701',
        raw_path:'6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7',
    }
    for path, expected in pins.items():
        assert sha(path) == expected, path
    g, a, d = [json.loads(p.read_bytes()) for p in (geometry, artwork, dc)]
    assert sha(artwork) == d['source']['sha256']
    with np.load(boundary, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    source_ids = arrays['source_node_ids'].tolist()
    full_map = arrays['retained_active_indices'][arrays['source_voltage_prolongation'].argmax(axis=1)]
    conditional_source_map = full_map.copy()
    edits, trace_owners = [], []
    with np.load(pack_path, allow_pickle=False) as pack, np.load(conditional_path, allow_pickle=False) as op:
        full_count = int(pack['potential_count'][0])
        conditional_count = int(op['y_shape'][0])
        # This array is an L02 field-reconstruction subset, not a circuit map.
        l02_reconstruction_nodes = op['conditional_global_active_index']
        retained_count = int(pack['category_retained_gc_shape'][0])
        assert np.all(full_map < retained_count)
        for index, (join, case) in enumerate(zip(a['joins'], d['cases'], strict=True)):
            assert join['trace']['trace_id'] == case['trace_id'] and case['passes_1pct_pair_screen']
            assert join['domain_isolation']['no_additional_conductor_contacts']
            binding = join['active_quotient_binding']
            upper = source_ids.index(binding['from_end_node_id'])
            lower = source_ids.index(binding['to_start_node_id'])
            old = int(full_map[upper])
            assert old == full_map[lower]
            # Only two finite currents touch this ideal join in the actual pack.
            for prefix in ('category_retained_gc', 'category_termination', 'category_l14_sheet_dc',
                           'category_l14_distributed_gc', 'category_l25_distributed_gc'):
                assert not np.any(pack[prefix+'_indices'] == old)
                ptr = pack[prefix+'_indptr']
                assert ptr[old+1] == ptr[old]
            for key in ('contact_gc_first_active_index', 'contact_gc_second_active_index',
                        'contact_global_active_index', 'trace_global_active_index',
                        'exterior_trace_global_active_index', 'local_facet_terminal_global_index',
                        'owner_external_active_indices', 'free_cell_owner_external_active_index'):
                assert not np.any(pack[key] == old)
            first, second = pack['finite_first_active_index'], pack['finite_second_active_index']
            incident = np.flatnonzero((first == old) | (second == old))
            assert len(incident) == 2
            segment = next(s for s in g['segments'] if s['via']['via_id'] == binding['to_via_id'])
            row = segment['chain_row']['pack_row']
            assert row in incident and (first[row] == old) != (second[row] == old)
            new_full, new_conditional = full_count+index, conditional_count+index
            full_map[lower] = new_full
            conditional_source_map[lower] = new_conditional
            assert old < retained_count and not np.any(l02_reconstruction_nodes == old)
            edits.append(dict(trace_owner=join['trace']['owner_id'],
                              moved_via_owner=segment['via']['owner_id'], pack_row=row,
                              endpoint='first' if first[row] == old else 'second',
                              old_full_active=old, new_full_active=new_full,
                              old_conditional_index=old, new_conditional_index=new_conditional,
                              resistance_ohm=case['resistance_candidate_ohm']))
            trace_owners.append(join['trace']['owner_id'])
    assert len(np.unique(full_map)) == 16
    h = arrays['source_incidence']
    resistance = np.r_[[s['chain_row']['resistance_ohm'] for s in g['segments']],
                       [c['resistance_candidate_ohm'] for c in d['cases']]]
    inductance = np.r_[[s['chain_row']['inductance_h'] for s in g['segments']], [0., 0.]]
    with np.load(raw_path, allow_pickle=False) as raw:
        assert np.all(raw['finite_count'][[s['chain_row']['native_row'] for s in g['segments']]] == 1)
    assert h.shape == (16, 15) and np.linalg.matrix_rank(h) == 15 and np.all(resistance > 0)
    # One assembled DC path check: no internal accumulation and actual finite
    # Trace voltage drops. This is not a repeated native branch Schur identity.
    current = np.ones(15)
    voltage = np.linalg.lstsq(h.T, -resistance*current, rcond=None)[0]
    demand = -h @ current
    assert np.count_nonzero(demand) == 2
    assert demand[source_ids.index('Node29224')] == 1
    assert demand[source_ids.index('Node2222482')] == -1
    power = float(voltage @ demand)
    assert abs(power-resistance.sum()) < 1e-12*resistance.sum()
    assert np.max(abs(h.T@voltage + resistance)) < 1e-12*resistance.max()
    for i in (13, 14):
        assert abs(-(h[:, i]@voltage)-resistance[i]) < 1e-12*resistance[i]
    out = R/'astra-port18-finite-trace-candidate-20260912-02'
    out.mkdir(exist_ok=False)
    owners = arrays['edge_owners'].tolist()[:13] + trace_owners
    np.savez_compressed(out/'candidate.npz', source_node_ids=arrays['source_node_ids'],
                        source_incidence=h, edge_owners=np.array(owners),
                        source_to_full_active=full_map, resistance_ohm=resistance,
                        source_to_conditional_active=conditional_source_map,
                        existing_via_inductance_h=inductance,
                        **{k: v for k, v in arrays.items() if k.startswith(('l14_', 'boundary_', 'electrode_'))})
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='ASSEMBLED_FINITE_SOURCE_TRACE_DC_SPLIT_CANDIDATE_NOT_BOARD_SOLVED',
                  edits=edits, original_full_count=full_count, expanded_full_count=full_count+2,
                  original_conditional_count=conditional_count, expanded_conditional_count=conditional_count+2,
                  checks=dict(all_other_incident_terms_absent_at_split=True,
                              finite_trace_voltage_drops_nonzero=True, assembled_dc_path_power_pass=True),
                  diagnostic_one_amp_path_dc_drop_v=power,
                  diagnostic_added_trace_drop_v=float(resistance[-2:].sum()),
                  application='In the chosen full or conditional representation move only the indicated finite-via endpoint to its appended voltage coordinate and insert its source-owned Trace resistor to the old node. The two representations have different counts; the L02 reconstruction subset remains unchanged. Keep existing via RL, every GC/termination/sheet row and the global return. Local equation: diag(R+jwL)i + H.T v=0; board current demand=-H i. No separate charge is added.',
                  limitations=['Current one-amp path fixture is not actual pin current or board port Z.',
                               'Trace zero in the inductance array means no new magnetic term modeled, not zero physical inductance.',
                               'DC sheet candidates do not establish broadband accuracy, spatial convergence or3D closure.',
                               'The full board operator and product remain unchanged; no solve or accuracy comparison performed.'],
                  inputs={str(p):expected for p, expected in pins.items()},
                  driver_sha256=sha(Path(__file__)), candidate_sha256=sha(out/'candidate.npz'))
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','edits','diagnostic_one_amp_path_dc_drop_v','diagnostic_added_trace_drop_v')}))


if __name__ == '__main__':
    run()
