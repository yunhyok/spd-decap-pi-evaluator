"""SPD Decap PI Evaluator v0.23.1: bounded actual-height Hankel rank pilot.

Uses the frozen104 height list. No board-grid allocation or shared-kernel edits.
"""
from pathlib import Path
from time import perf_counter
import json
import hashlib
import traceback
import numpy as np
from scipy.linalg import svd
from scipy.special import roots_legendre, j0
from scipy import fft
from astra_stratified_charge_green import source_background, point_subtractions, spectral_kg, EPS0

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-fixed-vertical-hankel-20260912-01'
COST = R/'astra-general-stratified-cost-20260912/cost.json'
COST_SHA = '7d5efc01dfc5f6f4c5f6b1e15620cb2045d603c807bd8ccbcaa96f77f232a1c1'
RANKS = [20, 24, 32]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run():
    started = perf_counter()
    assert sha(COST) == COST_SHA
    OUT.mkdir(exist_ok=False)
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='IN_PROGRESS_BOUNDED_ACTUAL_HEIGHT_HANKEL_PILOT',
                  cost_sha256=COST_SHA, source_sha256=sha(Path(__file__)), phases=[])

    def checkpoint(phase, **data):
        report['elapsed_s'] = perf_counter()-started
        report['phases'].append(dict(phase=phase, elapsed_s=report['elapsed_s'], **data))
        (OUT/'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report['phases'][-1]), flush=True)

    try:
        frozen = json.loads(COST.read_text())
        z = np.asarray(frozen['combined_z_m'])
        interfaces, eps, background = source_background()
        assert len(z) == 104 and np.array_equal(z, np.unique(z))
        report['source_background'] = background
        report['exact_heights_m'] = z.tolist()
        terms, ij = [], []
        for a in range(len(z)):
            for b in range(a, len(z)):
                terms.append(point_subtractions(z[a], z[b], interfaces, eps)[2])
                ij.append((a, b))

        def family(k):
            assert perf_counter()-started < 112, 'Bounded112s numerical phase budget'
            out = np.empty((104, 104, len(k)), complex)
            for (a, b), sub in zip(ij, terms):
                value = spectral_kg(k, z[a], z[b], interfaces, eps)
                value -= sum(t['coefficient']*np.exp(-k*t['vertical_distance_m']) for t in sub)
                out[a, b] = value; out[b, a] = value
            return out

        train_k = np.asarray(frozen['spectral_sample_k_per_m'])
        train = family(train_k)
        unfolded = np.concatenate([train.real.reshape(104, -1), train.imag.reshape(104, -1)], axis=1)
        basis, singular, _ = svd(unfolded, full_matrices=False, check_finite=False, lapack_driver='gesdd')
        del unfolded, train
        np.savez_compressed(OUT/'basis.npz', exact_z_m=z, basis_real=basis[:, :32],
                            training_k_per_m=train_k, singular_values=singular, ranks=np.asarray(RANKS))
        checkpoint('fixed_shared_basis', basis_shape=[104, 32], training_nodes=len(train_k))

        def plane(um):
            i = int(np.argmin(abs(z-um*1e-6)))
            assert abs(z[i]-um*1e-6) < 2e-15
            return i

        # Heights are frozen actual conductor points/faces. Lateral values are
        # declared probes, including the actual PWR/G joint separation and board span.
        pwr_g_rho = np.hypot(11890-8405, 17277-20368)*1e-6
        specs = [
            ('actual_PWR_G_lateral', pwr_g_rho, 20.83333333333333, 59.22649730810374),
            ('L02_55_75_near', 35e-6, 55., 75.),
            ('interface25_pair', 225e-6, 25., 25.),
            ('L08_L09_close', 5e-6, 375., 405.),
            ('L08_L09_intermediate', 225e-6, 375., 405.),
            ('L08_L09_far', 99.4e-3, 375., 405.),
            ('L08_same_face_near', 1e-6, 375., 375.),
            ('L08_same_face_far', 99.4e-3, 375., 375.),
            ('upper_ABF_deep', 1e-3, 375., 1005.),
            ('upper_ABF_EL_near', 5e-6, 1005., 1040.),
            ('upper_ABF_EL_far', 99.4e-3, 1005., 1040.),
            ('EL_near_pair', 35e-6, 1040., 1110.),
            ('EL_across', 1e-3, 1040., 1882.),
            ('EL_lower_ABF_near', 5e-6, 1882., 1917.),
            ('lower_ABF_same_face', 1e-6, 1917., 1917.),
            ('lower_ABF_span', 225e-6, 1917., 2847.),
            ('upper_lower_ABF_far', 99.4e-3, 375., 2847.),
            ('source_to_last_face', pwr_g_rho, 20.83333333333333, 2847.),
        ]
        cases = [dict(name=n, rho_m=float(rho), a=plane(a), b=plane(b), kind='physical_point')
                 for n, rho, a, b in specs]
        nphysical = len(cases)
        grid = (7, 5); spacing = 125e-6
        grid_planes = [plane(x) for x in [375., 1040., 1917.]]
        for a in range(3):
            for b in range(a, 3):
                for x in range(grid[0]):
                    for y in range(grid[1]):
                        cases.append(dict(name=f'finite_kernel_{a}_{b}_{x}_{y}', rho_m=float(np.hypot(x, y)*spacing),
                                          a=grid_planes[a], b=grid_planes[b], kind='finite_smooth_remainder',
                                          plane_a=a, plane_b=b, x=x, y=y))
        aidx = np.asarray([c['a'] for c in cases]); bidx = np.asarray([c['b'] for c in cases])
        rho = np.asarray([c['rho_m'] for c in cases])
        used = np.unique(np.r_[aidx, bidx]); ia = np.searchsorted(used, aidx); ib = np.searchsorted(used, bidx)
        analytic = np.zeros(len(cases), complex)
        for i, case in enumerate(cases[:nphysical]):
            _, _, sub = point_subtractions(z[case['a']], z[case['b']], interfaces, eps)
            analytic[i] = sum(t['coefficient']/np.hypot(case['rho_m'], t['vertical_distance_m'])
                              for t in sub)/(2*np.pi*EPS0)
        report['physical_probes'] = [dict(name=c['name'], rho_m=c['rho_m'], z_m=float(z[c['a']]),
                                          z_source_m=float(z[c['b']])) for c in cases[:nphysical]]
        report['lateral_scope'] = 'PWR/G separation is from frozen joint coordinates. Other rho values are declared probes at actual heights, not claims of restored polygon point membership.'
        reciprocity_max = 0.
        results = []
        configs = [(16., 1536), (32., 3072), (32., 4096)]
        for cutoff, order in configs:
            t0 = perf_counter(); gx, gw = roots_legendre(order)
            kmax = cutoff/min(np.diff(interfaces)); kk = (gx+1)*kmax/2; ww = gw*kmax/2
            integral = np.zeros((4, len(cases)), complex)
            for first in range(0, order, 512):
                k, w = kk[first:first+512], ww[first:first+512]
                whole = family(k)
                ref = whole[aidx, bidx]
                values = [ref]
                core32 = np.einsum('ar,abk,bs->rsk', basis[:, :32], whole, basis[:, :32], optimize=True)
                for rank in RANKS:
                    compact = np.einsum('ar,rsk,bs->abk', basis[used, :rank], core32[:rank, :rank],
                                        basis[used, :rank], optimize=True)
                    values.append(compact[ia, ib])
                hankel = j0(rho[:, None]*k)*w/(2*np.pi*EPS0)
                for row, value in enumerate(values):
                    integral[row] += np.sum(hankel*value, axis=1)
                for c in cases[:nphysical]:
                    reverse = spectral_kg(k, z[c['b']], z[c['a']], interfaces, eps)
                    _, _, sub = point_subtractions(z[c['b']], z[c['a']], interfaces, eps)
                    reverse -= sum(t['coefficient']*np.exp(-k*t['vertical_distance_m']) for t in sub)
                    reciprocity_max = max(reciprocity_max, float(abs(reverse-whole[c['a'], c['b']]).max()))
                del whole, core32
            results.append(integral)
            artifact = OUT/f'hankel-cut{int(cutoff)}-order{order}.npz'
            np.savez_compressed(artifact, remainder_v_per_c=integral, analytic_v_per_c=analytic,
                                rho_m=rho, plane_a=aidx, plane_b=bidx, exact_z_m=z)
            checkpoint('hankel_checkpoint', cutoff_factor=cutoff, order=order, seconds=perf_counter()-t0,
                       artifact_sha256=sha(artifact))

        ref = results[-1][0]; physical = ref[:nphysical]+analytic[:nphysical]
        point_rows = []
        for i, c in enumerate(cases[:nphysical]):
            fine = physical[i]
            point_rows.append(dict(**report['physical_probes'][i], reference_real=float(fine.real),
                reference_imag=float(fine.imag), remainder_abs=float(abs(ref[i])),
                cutoff_change_relative_full=float(abs(results[1][0, i]-results[0][0, i])/abs(fine)),
                order_change_relative_full=float(abs(results[2][0, i]-results[1][0, i])/abs(fine)),
                compression=[dict(rank=rank, relative_full=float(abs(results[-1][j+1, i]-ref[i])/abs(fine)),
                                  absolute_v_per_c=float(abs(results[-1][j+1, i]-ref[i])),
                                  relative_remainder=float(abs(results[-1][j+1, i]-ref[i])/max(abs(ref[i]), 1e-300)))
                             for j, rank in enumerate(RANKS)]))
        report['point_results'] = point_rows
        report['independently_evaluated_reverse_spectral_max_absolute'] = reciprocity_max

        # Only three selected planes on a7x5 grid: exact signed differences,
        # no full104-plane or board grid. Smooth remainder includes its diagonal.
        shape = tuple(fft.next_fast_len(2*n-1) for n in grid)
        rng = np.random.default_rng(20260912)
        q = rng.normal(size=(2, 3)+grid)+1j*rng.normal(size=(2, 3)+grid)
        q -= q.mean(axis=(1, 2, 3), keepdims=True)
        actions, ffmismatches, reversals = [], [], []
        ids = np.indices(grid).reshape(2, -1).T
        for row in range(4):
            kernels = np.empty((3, 3)+grid, complex)
            for i, c in enumerate(cases[nphysical:], nphysical):
                kernels[c['plane_a'], c['plane_b'], c['x'], c['y']] = results[-1][row, i]
                kernels[c['plane_b'], c['plane_a'], c['x'], c['y']] = results[-1][row, i]
            action = np.zeros_like(q)
            dense = np.empty((3*np.prod(grid),)*2, complex)
            fsource = [[fft.fftn(q[channel, a], s=shape) for a in range(3)] for channel in range(2)]
            for a in range(3):
                accum = [np.zeros(shape, complex), np.zeros(shape, complex)]
                for b in range(3):
                    embedded = np.zeros(shape, complex)
                    for x in range(-grid[0]+1, grid[0]):
                        for y in range(-grid[1]+1, grid[1]):
                            embedded[x % shape[0], y % shape[1]] = kernels[a, b, abs(x), abs(y)]
                    fk = fft.fftn(embedded)
                    for channel in range(2):
                        accum[channel] += fk*fsource[channel][b]
                    dx = abs(ids[:, 0, None]-ids[None, :, 0]); dy = abs(ids[:, 1, None]-ids[None, :, 1])
                    n = len(ids)
                    dense[a*n:(a+1)*n, b*n:(b+1)*n] = kernels[a, b, dx, dy]
                for channel in range(2):
                    action[channel, a] = fft.ifftn(accum[channel])[:grid[0], :grid[1]]
            direct = np.einsum('ij,cj->ci', dense, q.reshape(2, -1))
            ffmismatches.append(float(np.linalg.norm(action.reshape(2, -1)-direct)/np.linalg.norm(direct)))
            reversals.append(float(abs(q[0].ravel()@action[1].ravel()-q[1].ravel()@action[0].ravel())/
                                   max(abs(q[0].ravel()@action[1].ravel()), 1e-300)))
            actions.append(action)
        report['finite_convolution'] = dict(grid_shape=grid, padded_shape=shape, planes_um=[375, 1040, 1917],
            scalar_grid_dofs=105, witnesses=2, source_charge_sum_max=float(abs(q.sum(axis=(1, 2, 3))).max()),
            relative_fft_vs_dense=ffmismatches, ordinary_reciprocity=reversals,
            rank_remainder_action_relative=[dict(rank=r, relative=float(np.linalg.norm(actions[j+1]-actions[0])/np.linalg.norm(actions[0]))) for j, r in enumerate(RANKS)],
            scope='Three actual planes, smooth remainder only; finite difference embedding tested against dense action. Analytic direct/image terms remain exactly unchanged and separately owned.')
        reference_good = all(x['cutoff_change_relative_full'] < 1e-7 and x['order_change_relative_full'] < 1e-8 for x in point_rows)
        candidates = [rank for j, rank in enumerate(RANKS)
                      if max(x['compression'][j]['relative_full'] for x in point_rows) < 5e-5
                      and report['finite_convolution']['rank_remainder_action_relative'][j]['relative'] < 5e-5]
        assert max(ffmismatches) < 2e-12 and max(reversals) < 2e-12
        report.update(status='PASS_BOUNDED_FIXED_VERTICAL_HANKEL_PILOT' if reference_good and candidates else 'FAIL_BOUNDED_FIXED_VERTICAL_HANKEL_PILOT',
                      reference_refinement_pass=reference_good, smallest_sampled_candidate=min(candidates) if reference_good and candidates else None,
                      limits=['These are directional quadrature/rank refinement indicators and selected point/action checks, not rigorous uniform-k or full-board error bounds.',
                              'No full104-plane FFT, board-grid allocation, full Green action, port solve, or support quadrature certification.',
                              'Near-zero remainder relative errors are reported separately; acceptance of physical point errors uses the complete analytic-plus-remainder kernel.',
                              'Fixed real B and the same transpose gather preserve reciprocity; finite padding is retained. All analytic direct/image singularities remain uncompressed.',
                              'The full source-background assumptions are unchanged: infinite lateral dielectric strata, declared Cu-void resin fill, no background copper as ground.'])
        checkpoint('complete', status=report['status'], smallest_sampled_candidate=report['smallest_sampled_candidate'],
                   reference_refinement_pass=reference_good)
    except Exception:
        report.update(status='FAILED_PRESERVED_BOUNDED_PILOT', traceback=traceback.format_exc(), elapsed_s=perf_counter()-started)
        (OUT/'failure.json').write_text(json.dumps(report, indent=2))
        raise


if __name__ == '__main__':
    run()
