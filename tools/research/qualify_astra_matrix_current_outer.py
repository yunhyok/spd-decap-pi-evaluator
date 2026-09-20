"""SPD Decap PI Evaluator v0.23.1: seven actual matrix-current pairs,120s."""
from pathlib import Path
from time import monotonic, perf_counter
import hashlib, json, traceback
import numpy as np
from astra_matrix_current_outer import MatrixWhiteOuter, pair_energy_matrix, generalized_difference
from assemble_astra_l02_finite_charge_self import prism_union
from prepare_astra_retained_sheet_current_green import chunks

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-matrix-current-outer-20260912-01'
PINS = {
    R/'astra-retained-sheet-current-green-20260912-02/current-support.npz': '24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
    R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz': '5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05',
    R/'astra-l02-shared-edge-reuse-preflight-20260912-01/pair-candidates.npz': '58d8fc3ffefb7bb5e940b50fd70a0e3aa28c6e91cc09cfee519635a62ff1b21f',
    R/'astra-l02-shared-edge-reuse-preflight-20260912-01/tail-owner-metadata.npz': '008b434c0850ac61a35388f28c2e95f08113c3cd00dd8979c0d23f0a125c5868',
    R/'astra-l02-rows0-75-whitened-current-cross-20260912-04/rows0-75-whitened-current-cross.npz': '85207af698e98dccb444db19361167664f10b03c1e2e25c795a41d19d773e48c',
    ROOT/'tools/research/qualify_astra_tetra_volume_green.py': '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    ROOT/'tools/research/measure_astra_l02_adaptive_nonself.py': '6294cd99aabbed5a8b9756fd0c5acaf13e9edd03170c79c9f924d19ac1969504',
    ROOT/'tools/research/check_astra_l02_shared_edge_current.py': '999b0f58db4b5211796abdb5dc6b0e80d9fcd4801c46fc6eb7bd333f31456e15',
}
GATE = 5e-5


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for b in iter(lambda: stream.read(8*1024**2), b''):
            h.update(b)
    return h.hexdigest()


def run():
    started = perf_counter(); OUT.mkdir(exist_ok=False)
    for source in [Path(__file__), Path(__file__).with_name('astra_matrix_current_outer.py')]:
        (OUT/source.name).write_bytes(source.read_bytes())
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='IN_PROGRESS_MATRIX_CURRENT_PILOT', cases=[])
    def save(stage, **details):
        report['elapsed_s'] = perf_counter()-started
        (OUT/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        print(json.dumps(dict(stage=stage, elapsed_s=report['elapsed_s'], **details)), flush=True)
    try:
        for path, pin in PINS.items():
            assert sha(path) == pin, path
        paths = list(PINS)
        with np.load(paths[0]) as z:
            triangle = z['original_free_triangle_xy_m']; columns = z['compact_local_columns']; signs = z['local_signs']
        with np.load(paths[2]) as z:
            eligible = z['eligible_interior_pair_index']; rep = eligible[z['candidate_representative_eligible_index']]
            a, b = z['original_cell_a'][rep], z['original_cell_b'][rep]; multiplicity = z['candidate_multiplicity']
        with np.load(paths[3]) as z:
            owner = z['original_cell_ids']; length = np.zeros(len(triangle)); aspect = np.zeros(len(triangle))
            length[owner] = z['maximum_piece_edge_m']; aspect[owner] = z['maximum_piece_aspect']
        plen, paspect = np.maximum(length[a], length[b]), np.maximum(aspect[a], aspect[b])
        picks = [('saved_rows0_75', 0, 75, -1)]
        used = {(0, 75)}
        def choose(label, mask, score):
            for i in np.flatnonzero(mask)[np.argsort(score[mask])[::-1]]:
                pair = (int(a[i]), int(b[i]))
                if pair not in used:
                    used.add(pair); picks.append((label, *pair, int(i))); return
            raise ValueError('No distinct actual candidate: '+label)
        choose('most_frequent_candidate', np.ones(len(a), bool), multiplicity)
        choose('ordinary_small', (plen < 100e-6) & (paspect < 10), -plen)
        choose('ordinary_median_size', (plen < 1e-3) & (paspect < 100), -abs(plen-np.median(plen)))
        choose('over1mm', (plen > 1e-3) & (plen <= 1e-2), multiplicity)
        choose('over10mm', plen > 1e-2, multiplicity)
        choose('largest_aspect', np.ones(len(a), bool), paspect)
        selected_rows = np.unique([r for _, a, b, _ in picks for r in [a, b]])
        with np.load(paths[1]) as z:
            self_rows = z['original_free_ordinals']; row_lookup = np.full(len(triangle), -1, np.int64)
            row_lookup[self_rows] = np.arange(len(self_rows)); assert np.all(row_lookup[selected_rows] >= 0)
            selected_blocks = z['physical_block_h'][row_lookup[selected_rows]]
        self_map = {int(row): block for row, block in zip(selected_rows, selected_blocks)}
        with np.load(paths[4]) as z:
            oracle_w = z['arithmetic_reciprocal_w']; oracle_h = z['reconstructed_candidate_cross_h']
            oracle_local = z['physical_local_h']
        assert np.array_equal(oracle_local[:3, :3], self_map[0]) and np.array_equal(oracle_local[3:, 3:], self_map[75])
        report.update(pins={str(p.relative_to(ROOT)): h for p, h in PINS.items()},
                      selected_pairs=[dict(label=name, original_rows=[a, b], candidate_group=g,
                                           maximum_edge_m=float(max(length[a], length[b])), maximum_aspect=float(max(aspect[a], aspect[b])))
                                      for name, a, b, g in picks],
                      oracle_ownership='Only white04 physical matrices are used. Its mislabeled sparse-correction fields are never read or applied.')
        save('selection_ready')
        global_deadline = monotonic()+115.
        bounds = np.array([55e-6, 75e-6])
        for name, row0, row1, group in picks:
            pair_started = perf_counter(); case = dict(label=name, original_rows=[row0, row1], candidate_group=group, history=[])
            try:
                assert monotonic() < global_deadline, 'Whole115s numerical pilot budget'
                deadline = min(global_deadline, monotonic()+18.)
                tri = triangle[[row0, row1]]; ss = signs[[row0, row1]]
                ll = [np.linalg.cholesky(self_map[row]) for row in [row0, row1]]
                domains = [[(t, bounds)] for t in tri]
                tetra = [prism_union(t[None], bounds)[0] for t in tri]
                forward = MatrixWhiteOuter(domains[0], tetra[1], tri[0], tri[1], ll[0], ll[1], ss[0], ss[1], deadline)
                reverse = MatrixWhiteOuter(domains[1], tetra[0], tri[1], tri[0], ll[1], ll[0], ss[1], ss[0], deadline)
                previous, tolerance = None, 5e-5
                accepted = False
                for stage in range(8):
                    fw, rv = forward.refine_absolute(tolerance), reverse.refine_absolute(tolerance)
                    w = (fw+rv.T)/2; current = pair_energy_matrix(w)
                    mineig = float(np.linalg.eigvalsh(current).min())
                    if mineig <= 0:
                        case['history'].append(dict(absolute_matrix_tolerance=tolerance, minimum_pair_energy_eigenvalue=mineig))
                        tolerance /= 4
                        continue
                    reciprocal = generalized_difference(pair_energy_matrix(fw), pair_energy_matrix(rv.T), current)
                    refinement = None if previous is None else generalized_difference(previous, current, current)
                    aggregate = (forward.error+reverse.error)/(2*mineig)
                    case['history'].append(dict(absolute_matrix_tolerance=tolerance,
                        minimum_pair_energy_eigenvalue=mineig, generalized6_reciprocity=reciprocal,
                        generalized6_refinement=refinement, aggregate_energy_indicator=float(aggregate),
                        forward=forward.receipt(), reverse=reverse.receipt()))
                    if previous is not None and reciprocal <= GATE and refinement <= GATE and aggregate <= GATE:
                        accepted = True; break
                    previous = current.copy()
                    tolerance = min(tolerance/4, GATE*mineig/4)
                if not accepted:
                    raise RuntimeError('Matrix pilot failed generalized6 refinement/reciprocity/aggregate gate')
                physical = ll[0]@w@ll[1].T
                case.update(status='PASS_MATRIX_PAIR', physical_cross_h=physical.tolist(), whitened_cross=w.tolist(),
                            forward_receipt=forward.receipt(), reverse_receipt=reverse.receipt())
                if (row0, row1) == (0, 75):
                    oracle_error = generalized_difference(current, pair_energy_matrix(oracle_w), pair_energy_matrix(oracle_w))
                    case.update(saved_white04_generalized6_error=oracle_error,
                                saved_white04_cross_frobenius_relative=float(np.linalg.norm(physical-oracle_h)/np.linalg.norm(oracle_h)))
                    assert oracle_error <= GATE, 'Frozen accepted physical matrix comparison'
            except Exception:
                case.update(status='FAILED_PRESERVED_MATRIX_PAIR', traceback=traceback.format_exc())
            case['elapsed_s'] = perf_counter()-pair_started
            report['cases'].append(case)
            (OUT/f'pair-{row0}-{row1}.json').write_text(json.dumps(case, indent=2, allow_nan=False))
            save('pair_complete', label=name, original_rows=[row0, row1], status=case['status'], pair_seconds=case['elapsed_s'])
        accepted = [case for case in report['cases'] if case['status'] == 'PASS_MATRIX_PAIR']
        report.update(status='PASS_ALL_BOUNDED_MATRIX_CURRENT_PAIRS' if len(accepted) == len(picks) else 'PARTIAL_BOUNDED_MATRIX_CURRENT_COST_DISTRIBUTION',
                      accepted_pairs=len(accepted), selected_pair_count=len(picks),
                      helper_sha256=sha(Path(__file__).with_name('astra_matrix_current_outer.py')),
                      driver_sha256=sha(Path(__file__)),
                      scope='Seven actual two-prism current cross matrices only. One adaptive partition per direction and one tetra_inner batch serves all9 entries. No scalar oracle rerun, sparse correction assembly, representative reuse certification, or full field solve.',
                      limits=['Directional4/8-rule aggregate is an indicator, not a certified quadrature bound.',
                              'Generalized6 tests use existing qualified self matrices; their numerical uncertainty remains separate.',
                              'Failures and per-pair time budgets are preserved; no extrapolation assumes all144804 candidates have the same cost.'])
        save('complete', status=report['status'], accepted_pairs=len(accepted))
    except Exception:
        report.update(status='FAILED_PRESERVED_MATRIX_PILOT_SETUP', traceback=traceback.format_exc(), elapsed_s=perf_counter()-started)
        (OUT/'failure.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        raise


if __name__ == '__main__':
    run()
