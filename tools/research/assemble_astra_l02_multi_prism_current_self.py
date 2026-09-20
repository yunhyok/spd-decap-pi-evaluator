"""All internal magnetic interactions of the15 clipped L02 current cells."""
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.linalg import eigvalsh, solve_triangular

from assemble_astra_l02_finite_charge_self import prism_union
from assemble_astra_g_cut_mixed_magnetic import affine_pair
from prepare_astra_retained_sheet_current_green import chunks
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def run(clipped_single=False):
    started = monotonic()
    out = R/('astra-l02-clipped-single-current-self-20260912-01' if clipped_single else 'astra-l02-multi-prism-current-self-20260912-01')
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    source = R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
    assert sha(source) == '24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b'
    with np.load(source, allow_pickle=False) as z:
        pieces, parent = z['piece_triangle_xy_m'], z['piece_parent_free_ordinal']
        original, signs, columns = z['original_free_triangle_xy_m'], z['local_signs'], z['compact_local_columns']
    counts = np.bincount(parent, minlength=len(original))
    if clipped_single:
        candidates = np.flatnonzero(counts[parent] == 1)
        selected = candidates[np.any(pieces[candidates] != original[parent[candidates]], axis=(1, 2))]
        rows = parent[selected]
        assert rows.tolist() == [1368287, 1368290, 1371074, 1373742, 1373745]
    else:
        rows = np.flatnonzero(counts > 1)
        assert len(rows) == 15 and counts[rows].sum() == 34
        selected = np.flatnonzero(counts[parent] > 1)
    # Only relevant pieces enter subsequent lookups, rather than full scans.
    deadline = monotonic()+120
    cases = []
    try:
        for row in rows:
            part = pieces[selected[parent[selected] == row]]
            triangle = original[row]
            payload = dict(piece_triangle_xy_m=part, piece_parent_free_ordinal=np.zeros(len(part), int),
                           original_free_triangle_xy_m=triangle[None], local_signs=signs[row][None],
                           compact_local_columns=columns[row][None])
            points, _, basis, weights = next(chunks(payload))
            points, basis, weights = points.reshape(-1, 3), basis.reshape(-1, 3, 3), weights.ravel()
            distance = np.linalg.norm(points[:, None]-points[None, :], axis=-1)
            inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > 0)
            point = 1e-7*np.einsum('q,qic,qr,rjc,r->ij', weights, basis, inverse, basis, weights)
            tetra, _ = prism_union(part, np.array([55e-6, 75e-6]))
            area2 = abs(np.linalg.det(triangle[1:]-triangle[0]))
            fields = []
            for vertices in tetra:
                field = np.zeros((4, 3, 3))
                field[:, :, :2] = (vertices[:, None, :2]-triangle[None])/(area2*20e-6)
                field *= signs[row][None, :, None]
                fields.append(field)
            history = []
            previous = None
            for order in (4, 8, 16, 32, 64):
                raw = np.zeros((3, 3))
                for a, av in zip(tetra, fields):
                    for b, bv in zip(tetra, fields):
                        assert monotonic() < deadline, '120s multi-prism current execution boundary'
                        raw += affine_pair(a, av, b, bv, order)
                physical = (raw+raw.T)/2
                chol = np.linalg.cholesky(physical)
                skew = solve_triangular(chol, raw-raw.T, lower=True)
                skew = solve_triangular(chol, skew.T, lower=True).T
                reciprocity = float(np.linalg.norm(skew, 2))
                change = None if previous is None else float(np.max(abs(eigvalsh(previous-physical, physical))))
                history.append(dict(order=order, generalized_energy_change=change,
                                    generalized_raw_reciprocity=reciprocity))
                if change is not None and change <= 5e-5 and reciprocity <= 5e-5:
                    break
                previous = physical
            assert change is not None and change <= 5e-5 and reciprocity <= 5e-5
            case = dict(original_free_row=int(row), prism_count=len(part), tetra_count=len(tetra),
                        point_count=len(points), current_columns=columns[row].tolist(),
                        physical_self_h=physical.tolist(), point_self_h=point.tolist(),
                        self_delta_h=(physical-point).tolist(), history=history)
            cases.append(case)
            (out/'partial-cases.json').write_text(json.dumps(cases, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            print(json.dumps(dict(row=int(row), completed=len(cases), order=order,
                                  energy_indicator=change, reciprocity=reciprocity, elapsed_s=monotonic()-started)), flush=True)
        target = out/'multi-current-self.npz'
        np.savez_compressed(target, original_free_rows=rows,
                            current_columns=np.asarray([c['current_columns'] for c in cases]),
                            physical_self_h=np.asarray([c['physical_self_h'] for c in cases]),
                            point_self_h=np.asarray([c['point_self_h'] for c in cases]),
                            self_delta_h=np.asarray([c['self_delta_h'] for c in cases]))
        with np.load(target, allow_pickle=False) as z:
            assert np.array_equal(z['physical_self_h']-z['point_self_h'], z['self_delta_h'])
            assert np.array_equal(z['original_free_rows'], rows)
        report = dict(status='PASS_FIVE_CLIPPED_SINGLE_CURRENT_SELF_BLOCKS' if clipped_single else 'PASS_FIFTEEN_MULTI_PRISM_CURRENT_SELF_BLOCKS', cases=cases,
                      artifact_sha256=sha(target), elapsed_s=monotonic()-started,
                      scope='All internal prism/tetra pairs using ORIGINAL RT0 fields, even when a clipped cell has only one prism. Remaining between-cell near interactions and global L remain separate.')
    except Exception:
        report = dict(status='STOP_MULTI_PRISM_CURRENT_SELF', traceback=traceback.format_exc(),
                      completed_cases=cases, elapsed_s=monotonic()-started)
        (out/'failure.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        raise
    finally:
        if 'report' in locals():
            (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], elapsed_s=report['elapsed_s'])), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--clipped-single', action='store_true')
    run(parser.parse_args().clipped_single)
