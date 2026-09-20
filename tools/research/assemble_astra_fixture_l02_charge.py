"""Eight finite return charges for the small source-derived 1MHz fixture."""
import json
from pathlib import Path
from time import monotonic, perf_counter
import traceback

import numpy as np

from assemble_astra_l02_finite_charge_self import prism_union, wall_union
from measure_astra_l02_adaptive_nonself import AdaptiveOuter, AnisotropicOuter

ROOT = Path(__file__).resolve().parents[2]


def supports():
    with np.load(ROOT/'outputs/research/astra-retained-sheet-current-green-20260912-02/current-support.npz', allow_pickle=False) as z:
        triangles = z['original_free_triangle_xy_m'][[0, 75]]
        bounds = z['z_bounds_m']
    parts, domains, names = [], [], []
    for triangle, row in zip(triangles, (0, 75)):
        parts.append(prism_union([triangle], bounds)[0])
        domains.append([(triangle, bounds)])
        names.append(f'volume{row}')
    for triangle, opposite, name in ((triangles[0], 0, 'wall81'), (triangles[1], 2, 'wall74')):
        segment = np.delete(triangle, opposite, axis=0)
        parts.append(wall_union([segment], bounds)[0])
        domains.append([(segment, bounds)])
        names.append(name)
    for triangle, row in zip(triangles, (0, 75)):
        for height, side in zip(bounds, ('bottom', 'top')):
            parts.append(np.array([np.column_stack((triangle, np.full(3, height)))]))
            domains.append(None)
            names.append(f'cap{row}_{side}')
    return names, parts, domains


def run():
    out = ROOT/'outputs/research/astra-fixture-l02-charge-20260914-01'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    names, parts, domains = supports()
    matrix = np.full((8, 8), np.nan+1j*np.nan)
    coarse = matrix.copy()
    receipts = []
    start = perf_counter()
    deadline = monotonic()+180
    failure = None
    try:
        # Existing exact same prism P0 halfspace self, without deep remainder.
        saved = ROOT/'outputs/research/astra-l02-complete-self-20260912/complete-self.npz'
        with np.load(saved, allow_pickle=False) as z:
            ids = z['original_charge_rows']
            indices = [int(np.flatnonzero(ids == row)[0]) for row in (0, 75)]
            values = z['physical_halfspace_self_p'][indices]
        for a in range(2):
            matrix[a, a] = coarse[a, a] = values[a]
            receipts.append(dict(pair=[a, a], reused=str(saved)))
        for a in range(8):
            for b in range(8):
                if np.isfinite(matrix[a, b]):
                    continue
                # Cap self will be supplied by the independent exact depth/covariance check.
                if a == b and a >= 4:
                    continue
                if domains[a] is None:
                    actor = AdaptiveOuter(parts[a], parts[b], deadline)
                else:
                    actor = AnisotropicOuter(domains[a], parts[b], deadline)
                coarse[a, b] = actor.refine(1e-4)
                matrix[a, b] = actor.refine(2e-5)
                receipts.append(dict(pair=[a, b], refinement_relative=float(abs(matrix[a,b]-coarse[a,b])/abs(matrix[a,b])), receipt=actor.receipt()))
            np.savez_compressed(out/'partial.npz', names=np.array(names), halfspace_p=matrix, coarse_halfspace_p=coarse)
            print(json.dumps(dict(completed_observer=names[a], elapsed_s=perf_counter()-start)), flush=True)
    except Exception:
        failure = traceback.format_exc()
    np.savez_compressed(out/'halfspace-charge.npz', names=np.array(names), halfspace_p=matrix, coarse_halfspace_p=coarse)
    report = dict(status='HALFSPACE_EXCEPT_FOUR_CAP_SELF' if failure is None else 'STOP_HALFSPACE',
                  names=names, receipts=receipts, failure=failure, elapsed_s=perf_counter()-start,
                  missing_entries=np.argwhere(~np.isfinite(matrix)).tolist(),
                  scope='Actual finite L02 rows0/75, source wall0 and load wall749 excluded from free charge. Eight P0 supports; halfspace only, four cap self and complete deep remainder still required. No port solve.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','elapsed_s','missing_entries','failure')}), flush=True)


if __name__ == '__main__':
    run()
