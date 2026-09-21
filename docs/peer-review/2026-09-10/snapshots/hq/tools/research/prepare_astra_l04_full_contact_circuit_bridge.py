"""Bind every L04 contact to the existing hybrid circuit, including ten split paths."""
import argparse
import json
from pathlib import Path
import sys
import traceback

import numpy as np
from scipy.sparse import coo_matrix

import project_astra_l14_gc_mass as mass
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
NODE, POTENTIAL_COUNT, OLD_FINITE_COUNT = 71610, 3178104, 1692409
RUN_RELEASED = True  # Sol/root accepted the full-contact algebra and provenance.
PINS = {
    'current_result': (R / 'astra-l04-fixed-contact-currents-01/result.json',
                       '6c03c997c724da002e12bef0845a08cdae409f76d33428ab1bfba2f875a3a46f'),
    'currents': (R / 'astra-l04-fixed-contact-currents-01/l04-fixed-contact-currents.npz',
                 '5c2fd3bd30a45d9d92ccc47a5ad2d553f0d122acb4282a1046be09d7b4e51adb'),
    'owners': (R / 'astra-all-finite-current-l-ownership-01/all-finite-current-l-ownership.npz',
               '1b908c62e385ea1c227e77b1ccc37dd5c4b3376cce6f67d7b4e5c4a0c20508e6'),
    'pack': (R / 'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz',
             '01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c'),
    'splits': (R / 'astra-l04-cached-path-splits-02/result.json',
               '17ca350bbb4913283315812e746b547d3ee01c4fec85133d411fb0e8602fe48d'),
    'split_review': (R / 'astra-l04-cached-path-splits-02/independent-review.json',
                     '79d4810633fc84655914be1c2a7abc5ed3e5ff6997e2b9a2b90a896967a83103'),
    'stream': (R / 'astra-l04-fixed-contact-stream-01/result.json',
               '3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1'),
    'stream_guard': (R / 'astra-l04-fixed-contact-stream-01/external-budget.json',
                     '016bd3039ada75e8475a23ab328b2b0f135f641235bf5c9088e62636c0d04efc'),
    'mass': (Path(mass.__file__), 'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3'),
    'recon': (Path(recon.__file__), '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}


def receipt(path):
    return dict(path=str(path.resolve()), sha256=recon._sha256_file(path), size_bytes=path.stat().st_size)


def coupling(first, second, contact, sign, admittance, root, count, size):
    active = contact != root
    columns = contact[active] - (contact[active] > root)
    values = sign[active] * admittance[active]
    u = coo_matrix((np.r_[values, -values],
        (np.r_[first[active], second[active]], np.tile(columns, 2))), shape=(size, count - 1)).tocsc()
    u.sum_duplicates(); u.eliminate_zeros(); u.sort_indices()
    diagonal = np.zeros(count - 1, complex)
    np.add.at(diagonal, columns, admittance[active])
    return u, diagonal


def self_check():
    first, second = np.array([0, 2, 1]), np.array([2, 1, 2])
    contact, sign = np.array([0, 1, 1]), np.array([-1, 1, -1])
    y = np.array([2 + .2j, 3 + .3j, 4 + .4j])
    x, w = np.array([.2 + .4j, -.5j, .1]), np.array([.7 - .2j])
    u, d = coupling(first, second, contact, sign, y, 0, 2, 3)
    all_w = np.r_[0j, w]
    drop = x[first] - x[second] + sign * all_w[contact]
    i = y * drop
    extra = np.zeros(3, complex)
    np.add.at(extra, first, y * sign * all_w[contact]); np.add.at(extra, second, -y * sign * all_w[contact])
    assert np.allclose(u @ w, extra, rtol=1e-14, atol=1e-14)
    assert np.allclose(u.T @ x + d * w, [np.sum(sign[contact == 1] * i[contact == 1])], rtol=1e-14, atol=1e-14)
    return 'PASS_FINITE_CONTACT_BLOCK_SCATTER_CHECK'


def worker(output):
    frozen = output / 'driver-at-run.py'
    assert output.is_dir() and not (output / 'result.json').exists()
    assert recon._sha256_file(frozen) == recon._sha256_file(Path(__file__))
    budget = recon._Budget.create(60, 4)
    try:
        inputs = {}
        for name, (path, digest) in PINS.items():
            inputs[name] = receipt(path)
            assert inputs[name]['sha256'] == digest, name
        source = json.loads(PINS['stream'][0].read_bytes())
        current_result = json.loads(PINS['current_result'][0].read_bytes())
        assert current_result['status'] == 'COMPLETED_L04_FIXED_CONTACT_CURRENTS_SOURCE_AGGREGATION'
        assert current_result['driver']['sha256'] == '1331d6824e1b7ebc56216fda1093eeb517211c4b5bf311e1a982ed053b5d5427'
        assert current_result['artifact']['sha256'] == PINS['currents'][1]
        guard = json.loads(PINS['stream_guard'][0].read_bytes())
        assert source['status'] == 'PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'
        assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
        space_meta = source['space']
        inputs['space'] = receipt(Path(space_meta['path']))
        assert inputs['space']['sha256'] == space_meta['sha256']
        with np.load(space_meta['path'], allow_pickle=False) as z:
            supports = z['contact_support_index']
            source_groups = z['contact_source_group_index']
            ncell = len(z['free_triangle_indices'])
        count = len(supports)
        root = source['metrics']['gauge_contact_graph_row'] - ncell
        assert count == 38278 and 0 <= root < count
        group_to_contact = np.empty(count, dtype=np.int64)
        assert np.array_equal(np.sort(source_groups), np.arange(count))
        group_to_contact[source_groups] = np.arange(count)
        with np.load(PINS['currents'][0], allow_pickle=False) as z:
            category, parent = z['category'], z['expanded_branch_index']
            native, side = z['active_finite_index'], z['original_l04_target_side']
            starts = z['l04_endpoint_is_start']
            source_contact_rows, source_records = z['source_contact_row_index'], z['source_record_index']
            contacts = group_to_contact[z['coincident_group_index']]
            assert np.array_equal(z['group_to_drill_support_index'][source_groups], supports)
        with np.load(PINS['owners'][0], allow_pickle=False) as z:
            first, second = z['first_active_node'], z['second_active_node']
            resistance, inductance = z['resistance_ohm'], z['inductance_h']
            native_rows, legs = z['native_active_current_row'], z['final_split_leg']
            assert np.array_equal(z['final_active_current_row'], np.arange(OLD_FINITE_COUNT))
        assert first.shape == second.shape == resistance.shape == inductance.shape == (OLD_FINITE_COUNT,)
        assert np.all(resistance > 0) and np.all(inductance >= 0)
        zero_categories = {}
        with np.load(PINS['pack'][0], allow_pickle=False) as z:
            assert np.array_equal(first, z['finite_first_active_index']) and np.array_equal(second, z['finite_second_active_index'])
            original_y = z['finite_admittance_s']
            y_error = np.max(abs(original_y * (resistance + 2j * np.pi * 1e6 * inductance) - 1))
            assert y_error < 2e-12
            names = json.loads(z['category_names_json_utf8'].tobytes())
            assert names == ['retained_gc', 'termination', 'l14_sheet_dc', 'l14_distributed_gc', 'l25_distributed_gc']
            for name in names:
                prefix = 'category_' + name
                indices, ptr = z[prefix + '_indices'], z[prefix + '_indptr']
                assert ptr[NODE + 1] == ptr[NODE] and not np.any(indices == NODE), name
                zero_categories[name] = dict(row_nnz=0, column_nnz=0)
            for key in ('contact_gc_first_active_index', 'contact_gc_second_active_index',
                        'owner_external_active_indices', 'free_cell_owner_external_active_index', 'contact_global_active_index'):
                assert not np.any(z[key] == NODE), key
        assert np.bincount(category).tolist() == [76136, 10, 20]
        terminal = category < 2
        rows = parent[terminal]
        assert len(np.unique(rows)) == len(rows) == 76146
        assert np.array_equal(np.sort(rows), np.flatnonzero((first == NODE) ^ (second == NODE)))
        assert np.array_equal(native_rows[parent], native)
        assert np.all(legs[parent[category == 0]] == -1) and np.all(legs[parent[category > 0]] == 0)
        assert np.all(np.where(side[terminal] == 0, first[rows], second[rows]) == NODE)
        assert np.all(np.isin(side[terminal], [0, 1]))
        new_first, new_second = list(first[rows]), list(second[rows])
        new_contact, new_sign = list(contacts[terminal]), list(np.where(side[terminal] == 0, 1, -1))
        new_r, new_l, new_rows = list(resistance[rows]), list(inductance[rows]), list(rows)
        new_parent, subleg = list(rows), [-1] * len(rows)
        split_result = json.loads(PINS['splits'][0].read_bytes())
        assert split_result['status'] == 'COMPLETED_CONDITIONAL_L04_CACHED_PATH_SERIES_SPLIT_QUALIFICATION'
        paths = {p['original_active_finite_index']: p for p in split_result['paths'] if 'new_three_legs' in p}
        hidden = np.unique(parent[category == 2])
        assert len(hidden) == len(paths) == 10
        split_errors, changed_paths = [], []
        for j, old in enumerate(hidden):
            selection = (category == 2) & (parent == old)
            assert selection.sum() == 2 and len(np.unique(contacts[selection])) == 1
            assert np.all(side[selection] == -1) and len(np.unique(starts[selection])) == 2
            assert len(np.unique(source_records[selection])) == len(np.unique(source_contact_rows[selection])) == 2
            contact = int(contacts[selection][0])
            path = paths[int(native_rows[old])]
            raw_first, raw_second = path['original_active_endpoints']
            assert first[old] == raw_first and NODE not in (first[old], second[old])
            assert old >= 1692369 and old < 1692389
            retained = old + 20
            assert legs[retained] == 1 and native_rows[retained] == native_rows[old]
            assert first[retained] == second[old] and second[retained] == raw_second
            parts = path['new_three_legs']
            assert parts[0]['via_ids'] + parts[1]['via_ids'] == path['old_l02_legs'][0]['via_ids']
            assert parts[0]['source_record_sha256'] + parts[1]['source_record_sha256'] == path['old_l02_legs'][0]['source_record_sha256']
            for key, values in (('resistance_ohm', resistance), ('inductance_h', inductance)):
                error = abs(parts[0][key] + parts[1][key] - values[old]) / values[old]
                assert error < 2e-12 and abs(parts[2][key] - values[retained]) <= values[retained] * 2e-12
                split_errors.append(error)
            new_first.extend([first[old], NODE]); new_second.extend([NODE, second[old]])
            new_contact.extend([contact, contact]); new_sign.extend([-1, 1])
            new_r.extend([parts[0]['resistance_ohm'], parts[1]['resistance_ohm']])
            new_l.extend([parts[0]['inductance_h'], parts[1]['inductance_h']])
            new_rows.extend([old, OLD_FINITE_COUNT + j]); new_parent.extend([old, old]); subleg.extend([0, 1])
            changed_paths.append(dict(parent_row=int(old), retained_leg1_row=int(retained), contact_index=contact,
                appended_finite_row=OLD_FINITE_COUNT + j, original_active_finite_index=int(native_rows[old])))
        arrays = {key: np.asarray(value, dtype=np.int64) for key, value in dict(
            first_active_index=new_first, second_active_index=new_second, contact_index=new_contact,
            contact_drop_sign=new_sign, final_finite_row=new_rows, previous_parent_finite_row=new_parent,
            l04_subleg_index=subleg).items()}
        arrays.update(resistance_ohm=np.asarray(new_r), inductance_h=np.asarray(new_l))
        assert len(new_rows) == len(np.unique(new_rows)) == 76166
        retained_rows = hidden + 20
        assert len(retained_rows) == 10 and not np.any(np.isin(retained_rows, new_rows))
        assert OLD_FINITE_COUNT - len(hidden) + 2 * len(hidden) == 1692419
        assert np.array_equal(np.unique(new_contact), np.arange(count))
        assert np.all(arrays['resistance_ohm'] > 0) and np.all(arrays['inductance_h'] >= 0)
        assert np.all(np.where(arrays['contact_drop_sign'] == 1, arrays['first_active_index'], arrays['second_active_index']) == NODE)
        y = 1 / (arrays['resistance_ohm'] + 2j * np.pi * 1e6 * arrays['inductance_h'])
        u, diagonal = coupling(arrays['first_active_index'], arrays['second_active_index'], arrays['contact_index'],
            arrays['contact_drop_sign'], y, root, count, POTENTIAL_COUNT)
        assert np.all(diagonal.real > 0) and np.max(abs(np.asarray(u.sum(axis=0)))) < np.max(abs(u.data)) * 2e-12
        budget.check('once-owned finite replacement and full-contact coupling')
        artifact = output / 'l04-full-contact-circuit-bridge.npz'
        mass.atomic_npz(artifact, **arrays, contact_support_index=supports, contact_source_group_index=source_groups,
            root_contact_index=np.array([root]), hidden_parent_finite_rows=hidden,
            old_hidden_first_active_index=first[hidden], old_hidden_second_active_index=second[hidden],
            old_hidden_resistance_ohm=resistance[hidden], old_hidden_inductance_h=inductance[hidden],
            retained_leg1_finite_rows=retained_rows, retained_leg1_first_active_index=first[retained_rows],
            retained_leg1_second_active_index=second[retained_rows], retained_leg1_resistance_ohm=resistance[retained_rows],
            retained_leg1_inductance_h=inductance[retained_rows],
            source_contact_row_index=source_contact_rows, source_record_index=source_records,
            source_l04_endpoint_is_start=starts, source_record_contact_index=contacts,
            u_data=u.data, u_indices=u.indices, u_indptr=u.indptr, u_shape=np.array(u.shape),
            contact_diagonal_admittance_s=diagonal)
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE', driver=receipt(frozen), inputs=inputs,
            artifact=receipt(artifact), geometry_approximation=source['geometry_approximation'], frequency_hz=1e6,
            contacts=count, independent_contacts=count - 1, root_contact_index=root, uniform_active_index=NODE,
            potential_count=POTENTIAL_COUNT, old_finite_count=OLD_FINITE_COUNT, new_finite_count=OLD_FINITE_COUNT + 10,
            removed_old_leg0_count=len(hidden), inserted_subleg_count=2 * len(hidden), retained_leg1_count=len(retained_rows),
            terminal_incidence_count=len(rows), affected_old_parent_count=len(rows) + len(hidden), new_affected_branch_count=len(new_rows),
            changed_paths=changed_paths, nonfinite_71610=zero_categories,
            metrics=dict(original_impedance_relative=float(y_error), split_parameter_relative=max(split_errors), u_nnz=u.nnz),
            self_check=self_check(), budget=budget.receipt(),
            scope='Source finite endpoint bridge for full conditional L04 contacts, not a solve. '
                  'Ten old leg0 rows become twenty sublegs; their leg1 rows and all source R/L remain once-owned. '
                  'A future circuit must replace the ten uniform-coordinate stars as well as adding U/contact D. '
                  'Stored nonfinite71610 blocks are zero; missing physical G/C is not zero by inference. '
                  'No reduced-contact projection, NtD approximation, new board Z, magnetics or accuracy claim.')
        mass.atomic_json(output / 'result.json', report)
        print(json.dumps(dict(status=report['status'], metrics=report['metrics'], budget=report['budget'])), flush=True)
    except BaseException:
        mass.atomic_json(output / 'failure.json', dict(status='STOP_L04_FULL_CONTACT_BRIDGE',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check', action='store_true')
    modes.add_argument('--native-worker', action='store_true')
    modes.add_argument('--run', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        assert args.output is None and not args.native_worker
        print(self_check())
    elif args.native_worker:
        assert RUN_RELEASED and args.output is not None
        worker(args.output.resolve())
    else:
        assert RUN_RELEASED and args.run and args.output is not None
        import probe_astra_fmm3d_runtime as guard
        assert recon._sha256_file(Path(guard.__file__)) == '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(output)]
        raise SystemExit(guard.guarded_source_worker(output, worker_command=command, max_runtime_s=90))
