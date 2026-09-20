"""Finite-depth RT0 local 3x3 magnetic self block via overlap moments.

All three currents use J_i=(x-v_i)/(2*A*h), prior to facet orientation signs.
Same-prism off-diagonal basis entries are included. Cross-prism terms remain
separate even when two prism pieces belong to the same current basis.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.special import roots_legendre

from astra_prism_covariogram_self import depth_direct, triangle_difference_sectors, prism_self
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def prism_rt0_self(triangle, height, order, *, angular_transform='linear'):
    triangle = np.asarray(triangle, float)
    assert triangle.shape == (3, 2) and np.isfinite(triangle).all()
    assert height > 0 and np.isfinite(height) and isinstance(order, int) and order >= 4
    assert angular_transform in ('linear', 'hyperbolic')
    # Translation is irrelevant to J and avoids cancellation in overlap moments.
    triangle = triangle-triangle[0]
    left, right, factors = triangle_difference_sectors(triangle)
    centroid = triangle.mean(axis=0)
    covariance_trace = np.sum((triangle-centroid)**2)/12
    inverse_edges = np.linalg.inv(triangle[1:])
    nodes, weights = roots_legendre(order)
    t, wt = (nodes+1)/2, weights/2
    radial = t*t
    radial_weights = wt*2*t**3*(1-radial)**2
    block = np.zeros((3, 3))
    for a, b, factor in zip(left, right, factors):
        edge = b-a
        closest = float(np.clip(-a@edge/(edge@edge), 0, 1))
        cuts = np.unique([0., closest, 1.])
        for first, last in zip(cuts[:-1], cuts[1:]):
            angular = first+(last-first)*t
            if angular_transform == 'hyperbolic':
                # Resolve the narrow angular region of a long, thin triangle.
                # The positive change of variables keeps the integral intact.
                scale = np.linalg.norm(a+closest*edge)/np.linalg.norm(edge)
                assert scale > 0
                extent = np.arcsinh((last-first)/scale)
                direction = 1 if first == closest else -1
                angular = closest+direction*scale*np.sinh(t*extent)
                angular_weights = wt*scale*extent*np.cosh(t*extent)
            displacement = (a+angular[:, None]*edge)[:, None, :]*radial[None, :, None]
            bary12 = displacement@inverse_edges
            bary_delta = np.concatenate([-bary12.sum(axis=-1, keepdims=True), bary12], axis=-1)
            lower = np.maximum(-bary_delta, 0)
            alpha = 1-lower.sum(axis=-1)
            assert np.max(abs(alpha-(1-radial)[None, :])) < 5e-12
            mean = lower@triangle+alpha[:, :, None]*centroid
            test = mean[:, :, None, :]-triangle
            trial = mean[:, :, None, :]+displacement[:, :, None, :]-triangle
            moment = np.einsum('abid,abjd->abij', test, trial)
            moment += (alpha**2*covariance_trace)[:, :, None, None]
            rho = np.linalg.norm(displacement, axis=-1)
            measure = factor*(last-first)*wt[:, None]*radial_weights
            if angular_transform == 'hyperbolic':
                measure = factor*angular_weights[:, None]*radial_weights
            block += np.einsum('ab,abij->ij', measure*depth_direct(rho, height), moment)
    block *= 1e-7/4
    assert np.isfinite(block).all()
    return block


def reference(triangle, height, order):
    from assemble_astra_g_cut_mixed_magnetic import affine_pair
    from assemble_astra_l02_finite_charge_self import prism_union
    tet, _ = prism_union(np.asarray(triangle)[None], np.array([55e-6, 55e-6+height]))
    area2 = abs(np.linalg.det(triangle[1:]-triangle[0]))
    values = []
    for vertices in tet:
        field = np.zeros((4, 3, 3))
        field[:, :, :2] = (vertices[:, None, :2]-triangle[None, :, :])/(area2*height)
        values.append(field)
    return sum((affine_pair(a, av, b, bv, order) for a, av in zip(tet, values)
                for b, bv in zip(tet, values)), np.zeros((3, 3)))


def check(output):
    started = monotonic()
    path = R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
    assert sha(path) == '24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b'
    out = Path(output)
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(path, allow_pickle=False) as z:
        pieces = z['piece_triangle_xy_m']
    # Actual first thin-long prism plus a small actual prism, without new mesh.
    edges = np.linalg.norm(pieces[:, [1, 2, 0]]-pieces, axis=-1)
    small = int(np.argmin(abs(edges.max(axis=1)-40e-6)))
    report = dict(status='IN_PROGRESS', cases=[], driver_sha256=sha(Path(__file__)))
    for index in (0, small):
        triangle = pieces[index]
        history = []
        previous = None
        for order in (8, 16, 32, 64, 128):
            block = prism_rt0_self(triangle, 20e-6, order)
            change = None if previous is None else float(np.linalg.norm(block-previous)/np.linalg.norm(block))
            history.append(dict(order=order, relative_change=change))
            if change is not None and change < 5e-5:
                break
            previous = block
        assert change is not None and change < 5e-5
        symmetry = float(np.linalg.norm(block-block.T)/np.linalg.norm(block))
        assert symmetry < 1e-10 and np.linalg.eigvalsh((block+block.T)/2).min() > 0
        # Constant J: choose divergence-free facet currents reproducing J exactly.
        local = triangle-triangle[0]
        area2 = abs(np.linalg.det(local[1:]))
        density = np.array([.7, -.3])
        currents = np.linalg.solve(np.vstack([np.ones(3), local.T]), np.r_[0., -area2*20e-6*density])
        charge_self = prism_self(triangle, [55e-6, 75e-6], 25e-6, order)[0]
        expected = 1e-7*np.dot(density, density)*(area2*.5*20e-6)**2*charge_self
        uniform_error = float(abs(currents@block@currents-expected)/expected)
        assert uniform_error < 1e-10
        ref_history = []
        for ref_order in (8, 16, 32):
            ref = reference(triangle, 20e-6, ref_order)
            ref_error = float(np.linalg.norm(block-ref)/np.linalg.norm(block))
            ref_history.append(dict(order=ref_order, relative_to_covariogram=ref_error))
            if ref_error < 5e-5:
                break
        case = dict(piece_index=index, block_h=block.tolist(), refinement=history,
                    reciprocity=symmetry, constant_current_error=uniform_error,
                    independent_affine_tetra_reference=ref_history)
        report['cases'].append(case)
        report['elapsed_s'] = monotonic()-started
        (out/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(case), flush=True)
        assert ref_error < 5e-5
    report['status'] = 'PASS_TWO_ACTUAL_PRISM_RT0_SELF_BLOCKS'
    report['scope'] = 'Same-prism finite-thickness current self only. No cross-prism correction, global L or board port solve.'
    (out/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    check(parser.parse_args().output)
