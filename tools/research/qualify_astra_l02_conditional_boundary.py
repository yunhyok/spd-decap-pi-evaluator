"""Bind saved exterior geometry to the existing conditional 2D terminal model."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
PINS = {
    'boundary': ('astra-l02-rt0-boundary-continuations-04/boundary-continuation-map.npz', 'bee3df63c4178620aa330c19e06c4d8351d7bd2531a7d724f33abdddeb0cb24e'),
    'binding': ('astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz', '61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020'),
    'binding_result': ('astra-l02-circuit-contact-binding-01/result.json', '1ef00f9c88553faf71f6cd87b4899c23d4a1be598a18a08c19b8776148690efc'),
    'combined': ('astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz', 'a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469'),
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(output):
    start = time.perf_counter()
    inputs = {}
    for name, (relative, expected) in PINS.items():
        path = R / relative
        assert sha(path) == expected, name
        inputs[name] = {'path': str(path), 'sha256': expected}
    with (np.load(inputs['boundary']['path'], allow_pickle=False) as boundary,
          np.load(inputs['binding']['path'], allow_pickle=False) as binding,
          np.load(inputs['combined']['path'], allow_pickle=False) as combined):
        exterior = boundary['exterior_branch_index']
        assert len(np.unique(exterior)) == 817918
        assert np.all(boundary['source_ring_exact_match_count'] == 1)
        assert np.array_equal(np.sort(boundary['source_ring_witness_segment_index']), np.arange(817918))
        assert not boundary['source_ring_tolerance_match_mask'].any()
        assert not np.intersect1d(exterior, boundary['rt0_contact_rim_branch_indices']).size
        assert np.array_equal(np.bincount(boundary['boundary_class_code'], minlength=5), [8, 816756, 1154, 0, 0])
        supports = binding['contact_support_index']
        assert np.array_equal(supports, boundary['contact_support_index'])
        assert np.array_equal(supports[binding['native_contact_ordinal']], binding['native_drill_support_index'])
        contact = combined['l02_contact_active_indices']
        assert len(np.unique(contact)) == 38856 and contact[0] == 349710
        rows, legs = combined['final_finite_native_active_row'], combined['final_finite_split_leg']
        keys = rows * 3 + legs.astype(np.int64) + 1
        order = np.argsort(keys)
        sorted_keys = keys[order]
        assert np.all(np.diff(sorted_keys) > 0)
        first, second, admittance = (combined['final_finite_' + key] for key in ('first_active_index', 'second_active_index', 'admittance_s'))

        def lookup(native_rows, split_leg):
            expected = np.asarray(native_rows) * 3 + split_leg + 1
            positions = np.searchsorted(sorted_keys, expected)
            assert np.all(positions < len(sorted_keys))
            assert np.array_equal(sorted_keys[positions], expected)
            return order[positions]

        native = lookup(binding['active_finite_index'], -1)
        side = binding['target_side']
        assert np.array_equal(np.where(side == 0, first[native], second[native]), contact[binding['native_contact_ordinal']])
        assert np.array_equal(np.where(side == 0, second[native], first[native]),
                              np.where(side == 0, binding['second_active_index'], binding['first_active_index']))
        expected_y = binding['native_count'] / (binding['resistance_ohm'] + 2j*np.pi*1e6*binding['inductance_h'])
        assert np.array_equal(admittance[native], expected_y)
        junctions = json.loads(binding['junctions_json_utf8'].tobytes())
        assert len(junctions) == 20
        used = []
        for junction in junctions:
            a, b = [int(lookup([junction['replaced_active_finite_index']], leg)[0]) for leg in (0, 1)]
            terminal = contact[junction['contact_ordinal']]
            assert second[a] == first[b] == terminal
            assert [int(first[a]), int(second[b])] == junction['original_active_endpoints']
            for index, prefix in ((a, 'first_leg_'), (b, 'second_leg_')):
                expected = 1 / (junction[prefix+'resistance_ohm'] + 2j*np.pi*1e6*junction[prefix+'inductance_h'])
                assert abs(admittance[index]-expected) <= 4*np.finfo(float).eps*abs(expected)
            used.extend(junction['source_owner_pair'])
        assert len(set(used)) == len(used) == 40
        assert np.array_equal(np.unique(np.r_[binding['native_drill_support_index'],
                                             [j['drill_support_index'] for j in junctions]]), supports)
        meta = json.loads(Path(inputs['binding_result']['path']).read_bytes())
        leaves = meta['excluded_leaves']
        assert {row['source_via_id'] for row in leaves} == {'via1312784', 'via1612214'}
        assert all(row['shares_native_contact_support'] and row['drill_support_index'] in binding['native_drill_support_index'] for row in leaves)
        assert np.array_equal(np.unique(boundary['excluded_drill_support_index']),
                              np.sort([j['drill_support_index'] for j in junctions] + [row['drill_support_index'] for row in leaves]))
    result = {
        'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
        'status': 'QUALIFIED_CONDITIONAL_2D_L02_INSULATING_LATERAL_BOUNDARY',
        'driver_sha256': sha(Path(__file__)), 'inputs': inputs,
        'checks': {'exact_exterior_segments': 817918, 'rim_segments_disjoint': 621696,
                   'native_rl_terminal_joins': 76139, 'composite_leg_joins': 40,
                   'all_drill_contacts_bound': 38856, 'excluded_owner_count': 42},
        'decision': 'For the existing finite 2D conductor domain, prescribe zero normal sheet conduction on these source-ring exterior facets. Retain every drill terminal and voltage-dependent G/C owner. This is an explicit conditional modelling law, not a flux value inferred from geometry alone.',
        'limitations': ['Two source leaf vias retain their inherited circuit exclusion; no claim of electromagnetic irrelevance.',
                       'Ideal contact interiors, finite-domain geometry, P0 G/C projection and missing 3D/fringing/magnetic physics remain conditional.',
                       'No global field, factorization, mesh convergence or PowerSI accuracy acceptance.'],
        'elapsed_s': time.perf_counter()-start,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'checks': result['checks'], 'elapsed_s': result['elapsed_s'], 'result_sha256': sha(output/'result.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
