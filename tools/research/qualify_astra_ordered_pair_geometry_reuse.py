"""SPD Decap PI Evaluator v0.23.1: verify rigid reuse of ordered RT0 pair geometry."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_conforming_power_joint_sparse_current import local_mass

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':
        '14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',
    'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz':
        '0288c88834314667fb48c993a148bd57974ea1ed9db6bcc069ace252854d8ebc',
    'tools/research/qualify_astra_tetra_volume_green.py':
        '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    'tools/research/qualify_astra_conforming_power_joint_sparse_current.py':
        'dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e',
}


def group_pairs(tets_um, rows, cols, deadline):
    lookup, representatives = {}, []
    group = np.empty(len(rows), dtype=np.int64)
    i, j = np.triu_indices(8, 1)
    for first in range(0, len(rows), 4096):
        assert monotonic() < deadline
        p = np.concatenate((tets_um[rows[first:first+4096]], tets_um[cols[first:first+4096]]), axis=1)
        keys = np.rint(np.square(p[:, i]-p[:, j]).sum(axis=2)*1e8).astype(np.int64)
        # ponytail: rounded distances find candidates only; every reuse is checked below.
        for offset, key in enumerate(keys):
            tag = key.tobytes()
            if tag not in lookup:
                lookup[tag] = len(representatives)
                representatives.append(first+offset)
            group[first+offset] = lookup[tag]
    representatives = np.asarray(representatives, dtype=np.int64)
    fit = np.empty(len(rows))
    volume_error = 0.
    for first in range(0, len(rows), 4096):
        assert monotonic() < deadline
        selected = slice(first, first+4096)
        rep = representatives[group[selected]]
        x = np.concatenate((tets_um[rows[rep]], tets_um[cols[rep]]), axis=1)
        y = np.concatenate((tets_um[rows[selected]], tets_um[cols[selected]]), axis=1)
        x -= x.mean(axis=1, keepdims=True)
        y -= y.mean(axis=1, keepdims=True)
        u, _, vh = np.linalg.svd(x.transpose(0, 2, 1) @ y)
        rotated = x @ (u @ vh)
        fit[selected] = np.linalg.norm(rotated-y, axis=(1, 2))/np.linalg.norm(y, axis=(1, 2))
        for start in (0, 4):
            vx = np.abs(np.linalg.det(x[:, start+1:start+4]-x[:, start:start+1]))
            vy = np.abs(np.linalg.det(y[:, start+1:start+4]-y[:, start:start+1]))
            volume_error = max(volume_error, float(np.max(np.abs(vx/vy-1))))
    assert np.all(np.isfinite(fit)) and fit.max() < 1e-12
    assert volume_error < 1e-10
    return representatives, group, fit, volume_error


def main():
    output = ROOT/'outputs/research/astra-ordered-pair-geometry-reuse-01'
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, arrays = monotonic(), {}
    try:
        for path, expected in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        with np.load(ROOT/list(PINS)[0]) as data:
            tets_um = data['vertices_local_um'][data['cells']]
        with np.load(ROOT/list(PINS)[1]) as data:
            rows, cols = data['pair_source_cell'], data['pair_observer_cell']
        assert len(rows) == 416525 and np.all(rows < cols)
        rep, group, fit, volume_error = group_pairs(tets_um, rows, cols, started+120)
        counts = np.bincount(group)
        chosen = []
        for pair in np.argsort(-fit):
            g = group[pair]
            if pair != rep[g] and g not in [group[i] for i in chosen]:
                chosen.append(int(pair))
            if len(chosen) == 8:
                break
        for g in np.argsort(-counts)[:8]:
            if counts[g] > 1:
                chosen.append(int(np.flatnonzero(group == g)[-1]))
        chosen = np.unique(chosen)
        tets = tets_um*1e-6
        original, member, errors = [], [], []
        for pair in chosen:
            assert monotonic() < started+120
            other = rep[group[pair]]
            a, b = rows[other], cols[other]
            value = tetra_pair(tets[a], tets[b], 16)
            check = tetra_pair(tets[rows[pair]], tets[cols[pair]], 16)
            wa, wb = [np.linalg.inv(np.linalg.cholesky(local_mass(tets[k],
                      abs(np.linalg.det(tets[k, 1:]-tets[k, :1]))/6)).T) for k in (a, b)]
            errors.append(float(np.linalg.norm(wa.T @ (value-check) @ wb)/
                                np.linalg.norm(wa.T @ value @ wb)))
            original.append(value)
            member.append(check)
        assert max(errors) < 1e-10
        arrays.update(pair_a=rows, pair_b=cols, representative_pair_index=rep,
                      pair_group=group, group_multiplicity=counts, relative_rigid_fit=fit,
                      tested_member_pair_indices=chosen, tested_representative_h=np.array(original),
                      tested_member_h=np.array(member), tested_local_mass_relative=np.array(errors))
        result = dict(status='QUALIFIED_ORDERED_RIGID_PAIR_REUSE', pair_count=len(rows),
                      group_count=len(rep), maximum_multiplicity=int(counts.max()),
                      maximum_relative_rigid_fit=float(fit.max()), maximum_relative_volume_change=volume_error,
                      same_order_green_sample_count=len(chosen), same_order_green_max_relative=max(errors),
                      gates=dict(all_member_rigid_fit=True, all_member_volume=True, sampled_green_covariance=True),
                      failure=None)
    except Exception:
        result = dict(status='STOP_ORDERED_RIGID_PAIR_REUSE', failure=traceback.format_exc())
    np.savez_compressed(output/'pair-geometry-registry.npz', **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', pins=PINS,
                  elapsed_s=monotonic()-started, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  artifact_sha256=sha256((output/'pair-geometry-registry.npz').read_bytes()).hexdigest(),
                  scope='Ordered vertices retain each tetra local RT0 face ordering. All member geometry '
                  'fits an orthogonal map and translation within fixed gates; isotropic static 1/R and '
                  'RT0 dot products are invariant under that common map. Same-order samples check implementation '
                  'covariance, not quadrature convergence. Each representative still requires its own '
                  'forward/reverse integration qualification. No arbitrary vertex permutation, near error '
                  'bound, retarded/material-interface, field or board qualification.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['failure'] is None else 2


if __name__ == '__main__':
    raise SystemExit(main())
