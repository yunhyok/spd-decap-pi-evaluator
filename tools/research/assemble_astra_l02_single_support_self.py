"""Actual single-prism and single-wall L02 self; unions remain explicitly separate."""
import argparse
import ast
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import shutil
from pathlib import Path
from time import monotonic
import traceback

import numpy as np

from astra_prism_covariogram_self import prism_self, wall_self, refine_self
from astra_stratified_charge_green import source_background
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]; R = ROOT/'outputs/research'
PINS = {
    R/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz':'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
    R/'astra-retained-sheet-current-green-20260912-02/current-support.npz':'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
    ROOT/'tools/research/astra_prism_covariogram_self.py':'71f055b52712230e9f255671bb7e7b3a4c709f9f347598ba4ecea53fbe313a15'}


def prism_groups(triangles, material_factor):
    opposite = np.sum((triangles[:, [1, 2, 0]]-triangles[:, [2, 0, 1]])**2, axis=2)
    vertex_order = np.argsort(opposite, axis=1, kind='stable')
    canonical = np.take_along_axis(triangles, vertex_order[:, :, None], axis=1)
    key = np.rint(np.sort(opposite, axis=1)*1e20)
    assert np.max(abs(key)) < np.iinfo(np.int64).max
    _, representatives, group = np.unique(key.astype(np.int64), axis=0, return_index=True, return_inverse=True)
    bound = np.empty(len(triangles))
    for first in range(0, len(triangles), 4096):
        last = min(first+4096, len(triangles)); indices = np.arange(first, last)
        rep = representatives[group[first:last]]; reference = canonical[rep]; member = canonical[first:last]
        ref_edges = reference[:, 1:]-reference[:, :1]; edges = member[:, 1:]-member[:, :1]
        singular = np.linalg.svd(ref_edges, compute_uv=False)
        conditioning = singular[:, 0]/singular[:, 1]
        transform = np.linalg.solve(ref_edges, edges)
        reconstruction = np.linalg.norm(ref_edges@transform-edges, axis=(1, 2))/np.linalg.norm(edges, axis=(1, 2))
        singular_f = np.linalg.svd(transform, compute_uv=False)
        scale = np.maximum(np.max(abs(reference), axis=(1, 2)), np.max(abs(member), axis=(1, 2)))
        margin = 512*np.finfo(float).eps*(conditioning+scale/singular[:, 1])
        eta = np.max(abs(singular_f-1), axis=1)+margin
        usable = (conditioning < 1e8) & (reconstruction < 1e-12) & (eta < 1)
        beta = np.full(len(indices), np.inf)
        beta[usable] = material_factor*eta[usable]/(1-eta[usable])
        beta[indices == rep] = 0.  # Identical stored support; no geometry transfer.
        bound[first:last] = beta
    split = np.flatnonzero((bound > 1e-6) | ~np.isfinite(bound))
    group[split] = len(representatives)+np.arange(len(split))
    representatives = np.r_[representatives, split]; bound[split] = 0.
    group_bound = np.zeros(len(representatives)); np.maximum.at(group_bound, group, bound)
    return canonical[representatives], group, group_bound, bound, int(len(split))


def registry():
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    with np.load(list(PINS)[0], allow_pickle=False) as z:
        payload = {k:z[k] for k in ('charge_row_ids', 'contact_charge_columns', 'exterior_segment_offsets',
                                   'exterior_segment_vertices_um', 'free_charge_row_ids', 'z_interval_um')}
    with np.load(list(PINS)[1], allow_pickle=False) as z:
        pieces = z['piece_triangle_xy_m']; parent = z['piece_parent_free_ordinal']
    rows = payload['charge_row_ids']; assert np.all(np.diff(rows) > 0)
    counts = np.bincount(parent); single = counts[parent] == 1
    prism_columns = np.searchsorted(rows, parent[single])
    assert np.array_equal(rows[prism_columns], parent[single])
    _, eps, _ = source_background(); reflection = abs((eps[1]-eps[0])/(eps[1]+eps[0]))
    factor = (1+reflection)/(1-reflection)
    representatives, group, group_bound, bounds, split_count = prism_groups(pieces[single], factor)
    offsets = payload['exterior_segment_offsets']; wall_indices = np.flatnonzero(np.diff(offsets) == 1)
    segment = payload['exterior_segment_vertices_um'][offsets[wall_indices]]*1e-6
    lengths = np.linalg.norm(segment[:, 1]-segment[:, 0], axis=1); assert np.all(lengths > 0)
    unique_lengths, wall_group = np.unique(lengths, return_inverse=True)
    wall_columns = len(payload['free_charge_row_ids'])+len(payload['contact_charge_columns'])+wall_indices
    columns = np.r_[prism_columns, wall_columns]
    assert np.unique(columns).size == len(columns)
    unresolved = np.setdiff1d(np.arange(len(rows)), columns)
    wall_margin = 128*np.finfo(float).eps*factor
    return dict(columns=columns, original_rows=rows[columns], group=np.r_[group, len(representatives)+wall_group],
                member_bound=np.r_[bounds, np.full(len(wall_columns), wall_margin)],
                group_bound=np.r_[group_bound, np.full(len(unique_lengths), wall_margin)],
                prisms=representatives, wall_lengths=unique_lengths, z_bounds=payload['z_interval_um']*1e-6,
                prism_columns=len(prism_columns), wall_columns=len(wall_columns), split_count=split_count,
                unresolved_columns=unresolved, q_count=len(rows))


def evaluate_batch(jobs, z_bounds):
    top, eps, _ = source_background(); results = []
    for index, kind, geometry, bound in jobs:
        try:
            remaining = 5e-5*(1-bound)-bound
            assert remaining > 0
            evaluator = (lambda order: prism_self(geometry, z_bounds, top[0], order)) if kind == 'prism' else (
                lambda order: wall_self(float(geometry), z_bounds, top[0], order))
            value, history = refine_self(evaluator, z_bounds, top[0], eps, gate=remaining/(1+remaining))
            change = history[-1]['complete_self_refinement']
            combined = (change/(1-change)+bound)/(1-bound)
            assert combined <= 5e-5
            results.append((index, value, history[-1]['order'], change, None))
        except Exception:
            results.append((index, complex(np.nan, np.nan), 0, np.nan, traceback.format_exc()))
    return results


def run(full):
    started = monotonic(); out = R/('astra-l02-single-support-self-20260912-full' if full else 'astra-l02-single-support-self-20260912-smoke')
    out.mkdir(exist_ok=False); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    failure = None; failures = []
    try:
        if full:
            smoke = R/'astra-l02-single-support-self-20260912-smoke'
            assert sha(smoke/'registry.npz') == 'e2e4627ae4ab9b60876b74dc99f8951890fec5865a31a6184c6dfa1fc9e76c6b'
            assert sha(smoke/'result.json') == 'dac43f396658176836214c641747fb1c2eff14f4b1a0449165a7cef9fc88181b'
            def geometry_ast(path):
                return [ast.dump(n, include_attributes=False) for n in ast.parse(path.read_text(encoding='utf-8')).body
                        if isinstance(n, ast.FunctionDef) and n.name in ('registry', 'prism_groups')]
            assert geometry_ast(Path(__file__)) == geometry_ast(smoke/'driver-at-run.py')
            for path, digest in PINS.items():
                assert sha(path) == digest, path
            with np.load(smoke/'registry.npz', allow_pickle=False) as z:
                data = {key:(z[key].item() if z[key].ndim == 0 else z[key]) for key in z.files}
            shutil.copyfile(smoke/'registry.npz', out/'registry.npz')
        else:
            data = registry()
            np.savez_compressed(out/'registry.npz', **data)
        nprism = len(data['prisms']); ngroup = len(data['group_bound'])
        values = np.full(ngroup, np.nan+1j*np.nan); orders = np.zeros(ngroup, np.int16); changes = np.full(ngroup, np.nan)
        chosen = np.arange(ngroup) if full else np.unique(np.r_[np.linspace(0, ngroup-1, 24, dtype=int),
                      data['group'][np.flatnonzero(data['columns'] == 0)], data['group'][data['prism_columns']],
                      data['group'][-1]])
        jobs = [(int(i), 'prism' if i < nprism else 'wall', data['prisms'][i] if i < nprism else data['wall_lengths'][i-nprism],
                 float(data['group_bound'][i])) for i in chosen]
        print(json.dumps(dict(stage='registry_ready', groups=ngroup, chosen=len(chosen),
                              single_prism_q=data['prism_columns'], single_wall_q=data['wall_columns'],
                              split_members=data['split_count'], elapsed_s=monotonic()-started)), flush=True)
        last_save = monotonic(); completed = 0
        def save():
            temporary = out/'checkpoint.partial.npz'
            np.savez_compressed(temporary, physical_self=values, order=orders, refinement=changes)
            temporary.replace(out/'checkpoint.npz')
            (out/'failures.json').write_text(json.dumps(failures, indent=2)+'\n', encoding='utf-8')
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(evaluate_batch, jobs[first:first+64], data['z_bounds']) for first in range(0, len(jobs), 64)]
            for future in as_completed(futures):
                for index, value, order, change, error in future.result():
                    values[index], orders[index], changes[index] = value, order, change
                    if error:
                        failures.append(dict(group=index, traceback=error))
                    completed += 1
                if monotonic()-last_save > 5:
                    save(); last_save = monotonic()
                    print(json.dumps(dict(stage='self_progress', completed=completed, total=len(chosen), failures=len(failures),
                                          elapsed_s=monotonic()-started)), flush=True)
        save()
        assert not failures, 'Unqualified groups are preserved in failures.json'
        if not full:
            reference_path = R/'astra-l02-finite-charge-self-20260912-02/result.json'
            assert sha(reference_path) == 'ae5e542c598f9b432dbc6194155802ab66567c4393a9a7f4c5868fed7a5372ba'
            references = {c['charge_column']:complex(*c['physical_self_per_f']) for c in json.loads(reference_path.read_text())['cases']}
            for column in (0, 1622616, 2440491):
                member = int(np.flatnonzero(data['columns'] == column)[0]); computed = values[data['group'][member]]
                assert np.isfinite(computed) and abs(computed-references[column])/abs(references[column]) < 5e-5
        else:
            from astra_l02_point_self import point_self
            with np.load(list(PINS)[0], allow_pickle=False) as z:
                selected_q = np.zeros(data['q_count'], bool); selected_q[data['columns']] = True
                col = z['spread_col']; mask = selected_q[col]
                point_all = point_self(z['quadrature_points_um'][mask]*1e-6, col[mask], z['spread_data'][mask], data['q_count'])
            point = point_all[data['columns']]; actual = values[data['group']]
            refinement = changes[data['group']]; beta = data['member_bound']
            combined = (refinement/(1-refinement)+beta)/(1-beta)
            assert np.isfinite(point).all() and np.isfinite(actual).all() and np.all(actual.real > 0) and np.all(combined <= 5e-5)
            np.savez_compressed(out/'single-support-self.npz', charge_columns=data['columns'], original_charge_rows=data['original_rows'],
                                physical_halfspace_self_p=actual, point_halfspace_self_p=point,
                                physical_halfspace_self_delta_p=actual-point, combined_refinement_geometry_indicator=combined,
                                unresolved_charge_columns=data['unresolved_columns'])
        report = dict(status='PASS_ALL_SINGLE_SUPPORT_SELF' if full else 'PASS_SINGLE_SUPPORT_SMOKE', groups=ngroup,
                      computed_groups=len(chosen), single_prism_q=data['prism_columns'], single_wall_q=data['wall_columns'],
                      unresolved_q=len(data['unresolved_columns']), split_members=data['split_count'],
                      source_pins={str(p):h for p,h in PINS.items()}, failures=failures)
    except Exception:
        failure = traceback.format_exc(); report = dict(status='STOP_SINGLE_SUPPORT_SELF', failure=failure, failures=failures)
    report.update(driver_sha256=sha(Path(__file__)), elapsed_s=monotonic()-started,
                  scope='Single-prism and single-rectangle retained L02 q only. Actual affine shape maps qualify reuse, with a floating margin; failed candidates become independent representatives. Geometry plus successive quadrature is a numerical indicator, not an interval-certified integral bound. Actual point self is subtracted per q. Contact and multi-primitive unions, nonself terms and port solve remain separate.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)
    return 0 if failure is None else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--smoke', action='store_true'); mode.add_argument('--full', action='store_true')
    raise SystemExit(run(parser.parse_args().full))
