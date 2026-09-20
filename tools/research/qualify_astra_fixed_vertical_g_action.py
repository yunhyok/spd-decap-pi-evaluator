"""SPD Decap PI Evaluator v0.23.1: actual G cached-basis pilot,120s budget."""
from pathlib import Path
from time import perf_counter
import hashlib, json, traceback, sys
import numpy as np
from astra_fixed_vertical_charge_action import FixedVerticalRemainderAction
from astra_layered_charge_action import StratifiedPlaneRemainderAction
from apply_astra_owned_3d_green import TET_BARY, TRI_BARY

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-fixed-vertical-g-action-20260912-01'
SPACINGS = [125e-6, 62.5e-6]
PINS = {
    R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz': '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
    R/'astra-g-general-stratified-charge-action-20260912/action.npz': 'b295b66e974862cfddedb04d40ee2f0aee69aefa6485d24a071a026916049a77',
    R/'astra-halfspace-charge-self-full-20260912/full-pwr-g-halfspace-self.npz': '8f11ba2ee16f4a06d05b743209e4e4462be1abd1e6ddbe6016a8b2311b3c94b9',
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def direct_remainder(actor, density, targets):
    """Exact source-point/target-point distances, uncompressed Hankel tables."""
    source_plane = np.searchsorted(actor.z_nodes, actor.points[:, 2])
    answer = np.zeros((len(targets), density.shape[1]), complex)
    for i, target in enumerate(targets):
        a = int(np.argmin(abs(actor.z_nodes-target[2])))
        assert abs(actor.z_nodes[a]-target[2]) < 2e-15
        for b in np.unique(source_plane):
            selected = np.flatnonzero(source_plane == b)
            radius = np.linalg.norm(actor.points[selected, :2]-target[:2], axis=1)
            kernel = actor.tables[min(a, b), max(a, b)](radius)
            answer[i] += kernel@density[selected]
    return answer


def run():
    started = perf_counter(); OUT.mkdir(exist_ok=False)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='IN_PROGRESS_ACTUAL_G_FIXED_BASIS', checks=[])
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    def checkpoint(stage, **data):
        report['elapsed_s'] = perf_counter()-started
        (OUT/'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(dict(stage=stage, elapsed_s=report['elapsed_s'], **data)), flush=True)
    try:
        for path, pin in PINS.items():
            assert sha(path) == pin
        geom, reference_path, self_path = PINS
        with np.load(geom) as z:
            tetra, faces = z['volume_charge_vertices_um']*1e-6, z['surface_charge_vertices_um']*1e-6
        nc, nf = len(tetra), len(faces)
        assert (nc, nf) == (6078, 3008)
        points = np.concatenate([np.einsum('qi,tid->tqd', TET_BARY, tetra).reshape(-1, 3),
                                 np.einsum('qi,tid->tqd', TRI_BARY, faces).reshape(-1, 3)])
        columns = np.r_[np.repeat(np.arange(nc), 4), np.repeat(nc+np.arange(nf), 3)]
        weights = np.r_[np.full(4*nc, .25), np.full(3*nf, 1/3)]
        with np.load(reference_path) as z:
            charge, saved = z['charge'], z['potential_v']
            selected, saved_exact = z['direct_check_rows'], z['direct_check_potential_v']
            full_rows = z['complete_pwr_g_charge_rows']
        assert charge.shape == (9086, 2)
        assert np.array_equal(full_rows, np.r_[288804+np.arange(nc), 288804+nc+145516+np.arange(nf)])
        with np.load(self_path) as z:
            delta = z['physical_halfspace_self_delta_p'][full_rows]
        assert np.isfinite(delta).all()
        density = weights[:, None]*charge[columns]
        def gather(value):
            result = np.zeros_like(charge)
            np.add.at(result, columns, weights[:, None]*value[:len(points)])
            return result
        old = StratifiedPlaneRemainderAction(points, spacing_m=62.5e-6, radial_count=768)
        old_point = old.apply(density)
        old_q = gather(old_point)
        # The identical analytical action and exact owned self are retained
        # algebraically from the frozen full action; no FMM/self reintegration.
        unchanged = saved-old_q
        selected_mask = np.isin(columns, selected)
        target_direct = direct_remainder(old, density, old.points[selected_mask])
        exact = saved_exact-old_q[selected]
        np.add.at(exact, np.searchsorted(selected, columns[selected_mask]), weights[selected_mask, None]*target_direct)
        # Exercise actual upper-ABF/EL/lower-ABF height rows in the fast actor.
        # XY are declared probes over the actual G patch, not restored electrode membership.
        center = points[:, :2].mean(axis=0)
        cross_targets = np.c_[np.tile(center, (3, 1)), np.asarray([375., 1040., 1917.])*1e-6]
        cross_ref = StratifiedPlaneRemainderAction(points, targets=cross_targets, spacing_m=62.5e-6, radial_count=768)
        cross_exact = direct_remainder(cross_ref, density, cross_ref.targets)
        targets = np.vstack((points, cross_targets))
        report.update(charges=9086, points=33336, charge_witnesses=2, exact_target_charge_rows=selected.tolist(),
                      cross_target_points_m=cross_targets.tolist(), pins={str(p.relative_to(ROOT)): h for p, h in PINS.items()},
                      unchanged_analytic_self='Frozen general-stratified potential minus its recomputed smooth remainder. Original charge witnesses and physical halfspace self are retained exactly; no FMM or self integration.',
                      direct_target_reference='Frozen exact analytic target action plus directly evaluated uncompressed Hankel-table remainder at original source and target points. Cubature/self ownership unchanged.')
        checkpoint('references_ready')
        saved_actions = {}
        for spacing in SPACINGS:
            assert perf_counter()-started < 100, '120s whole pilot budget'
            actor = FixedVerticalRemainderAction(points, targets=targets, spacing_m=spacing, max_rank=24)
            this_checks = []
            for rank in [20, 24]:
                before = actor.kernel_transform_count
                compressed = actor.apply(density, rank=rank)
                assert actor.kernel_transform_count == before == 300
                answer = unchanged+gather(compressed)
                check = dict(rank=rank, spacing_um=spacing*1e6, setup=actor.receipt.copy(), apply=actor.last_apply.copy(),
                    full_saved_action_relative=float(np.linalg.norm(answer-saved)/np.linalg.norm(saved)),
                    smooth_remainder_saved_relative=float(np.linalg.norm(gather(compressed)-old_q)/np.linalg.norm(old_q)),
                    exact_target_full_relative=float(np.linalg.norm(answer[selected]-exact)/np.linalg.norm(exact)),
                    cross_target_remainder_relative=float(np.linalg.norm(compressed[-3:]-cross_exact)/np.linalg.norm(cross_exact)),
                    ordinary_reciprocity_relative=float(abs(charge[:, 0]@answer[:, 1]-charge[:, 1]@answer[:, 0])/
                                                       max(abs(charge[:, 0]@answer[:, 1]), 1e-300)))
                check['pass'] = all(check[k] < 5e-5 for k in ['full_saved_action_relative', 'smooth_remainder_saved_relative',
                                                            'exact_target_full_relative', 'cross_target_remainder_relative'])
                assert check['ordinary_reciprocity_relative'] < 2e-10
                report['checks'].append(check); this_checks.append(check)
                saved_actions[f'potential_rank{rank}_spacing{spacing*1e6:g}um'] = answer
                saved_actions[f'cross_rank{rank}_spacing{spacing*1e6:g}um'] = compressed[-3:]
                checkpoint('actual_g_rank_check', **{k: v for k, v in check.items() if k not in ('setup', 'apply')},
                           setup_seconds=actor.receipt['setup_seconds'], apply_seconds=actor.last_apply['elapsed_s'])
            if all(c['pass'] for c in this_checks):
                break
        np.savez_compressed(OUT/'action.npz', charge=charge, reference=saved, direct_targets=exact,
                            reference_remainder_charge_v=old_q, unchanged_analytic_self_v=unchanged,
                            cross_exact=cross_exact, selected_charge_rows=selected, **saved_actions)
        report.update(status='PASS_ACTUAL_G_CACHED_FIXED_VERTICAL_ACTION' if any(c['pass'] for c in report['checks']) else 'FAIL_ACTUAL_G_CACHED_FIXED_VERTICAL_ACTION',
                      driver_sha256=sha(Path(__file__)), actor_sha256=sha(ROOT/'tools/research/astra_fixed_vertical_charge_action.py'),
                      artifact_sha256=sha(OUT/'action.npz'),
                      limitations=['Selected actual G domain and three cross-stratum target probes only; no full-board error claim.',
                                   'Direct targets use existing point cubature and owned physical self; mutual-near support quadrature remains a separate unresolved error.',
                                   'The in-memory actor explicitly rejects oversized kernel caches before allocation. Full-board cached storage/streaming still needs its own preflight.',
                                   'Hankel, basis and grid refinement indicators are not rigorous uniform operator bounds.'])
        checkpoint('complete', status=report['status'])
    except Exception:
        report.update(status='FAILED_PRESERVED_ACTUAL_G_PILOT', traceback=traceback.format_exc(), elapsed_s=perf_counter()-started)
        (OUT/'failure.json').write_text(json.dumps(report, indent=2))
        raise


if __name__ == '__main__':
    if '--fine-only' in sys.argv:
        OUT = R/'astra-fixed-vertical-g-action-20260912-02-fine'
        SPACINGS = [62.5e-6]
    run()
