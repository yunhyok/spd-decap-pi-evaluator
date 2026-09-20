"""Conservative affine-current grid moments for separated-layer magnetic research."""
import argparse
import json
from time import perf_counter

import numpy as np
import shapely


def project(vertices_um, mean_a_per_m, alpha_a_per_m2, origin_um, pitch_um, shape_yx, *, budget=None):
    """Return per-square integral J dA (A m); retain each original triangle's moments."""
    vertices = np.asarray(vertices_um, dtype=float)
    mean = np.asarray(mean_a_per_m, dtype=complex)
    alpha = np.asarray(alpha_a_per_m2, dtype=complex)
    origin = np.asarray(origin_um, dtype=float)
    n = len(vertices)
    shape = np.asarray(shape_yx)
    assert shape.shape == (2,) and shape.dtype.kind in 'iu' and np.all(shape > 0)
    shape_yx = tuple(map(int, shape))
    assert vertices.shape == (n, 3, 2) and mean.shape == (n, 2) and alpha.shape == (n,)
    assert n > 0 and origin.shape == (2,) and np.isfinite(pitch_um) and pitch_um > 0
    assert all(np.isfinite(a).all() for a in (vertices, mean, alpha, origin))
    anchor = vertices[:, 0]
    local = vertices-anchor[:, None]
    area = abs(local[:, 1, 0]*local[:, 2, 1]-local[:, 1, 1]*local[:, 2, 0])/2
    assert np.all(area > 0)
    center = local.mean(1)
    radius = np.linalg.norm(local-center[:, None], axis=2).max(1)
    first = np.floor((vertices.min(1)-origin)/pitch_um).astype(np.int64)
    last = np.floor((vertices.max(1)-origin)/pitch_um).astype(np.int64)
    assert np.all(first >= 0) and np.all(last < np.array(shape_yx)[::-1])
    widths = last-first+1
    counts = widths.prod(1)
    grid = np.zeros((int(np.prod(shape_yx)), 2), complex)
    area_sum = np.zeros(n)
    centered_moment_sum = np.zeros((n, 2))
    inside = np.flatnonzero(counts == 1)
    inside_cell = first[inside, 1]*shape_yx[1]+first[inside, 0]
    np.add.at(grid, inside_cell, area[inside, None]*1e-12*mean[inside])
    area_sum[inside] = area[inside]
    crossing = np.flatnonzero(counts > 1)
    cumulative = np.cumsum(counts[crossing])
    total = int(cumulative[-1]) if len(cumulative) else 0
    tested = positive = 0
    started = perf_counter()
    # ponytail: bounded native geometry batches; cache sparse transfer only if repeated actions require it.
    for offset in range(0, total, 50_000):
        flat = np.arange(offset, min(offset+50_000, total), dtype=np.int64)
        owner = np.searchsorted(cumulative, flat, side='right')
        tri = crossing[owner]
        relative = flat-(cumulative[owner]-counts[tri])
        ix = first[tri, 0]+relative % widths[tri, 0]
        iy = first[tri, 1]+relative // widths[tri, 0]
        lower = origin+pitch_um*np.column_stack((ix, iy))-anchor[tri]
        boxes = shapely.box(lower[:, 0], lower[:, 1], lower[:, 0]+pitch_um, lower[:, 1]+pitch_um)
        cuts = shapely.intersection(shapely.polygons(local[tri]), boxes)
        cut_area = shapely.area(cuts)
        assert np.isfinite(cut_area).all() and np.all(cut_area >= 0)
        keep = cut_area > 0
        ids, a = tri[keep], cut_area[keep]
        centroid = shapely.get_coordinates(shapely.centroid(cuts[keep]))
        assert centroid.shape == (len(a), 2) and np.isfinite(centroid).all()
        delta = centroid-center[ids]
        integral = a[:, None]*1e-12*(mean[ids]+alpha[ids, None]*delta*1e-6)
        np.add.at(grid, iy[keep]*shape_yx[1]+ix[keep], integral)
        np.add.at(area_sum, ids, a)
        np.add.at(centered_moment_sum, ids, a[:, None]*delta)
        tested += len(flat)
        positive += len(a)
        if budget is not None:
            budget.check('native triangle-square affine current projection')
    area_relative = abs(area_sum-area)/area
    moment_relative = np.linalg.norm(centered_moment_sum, axis=1)/(area*radius)
    assert np.max(area_relative) < 1e-7, ('triangle area lost', float(np.max(area_relative)))
    assert np.max(moment_relative) < 1e-7, ('triangle first moment lost', float(np.max(moment_relative)))
    expected_current = np.sum(area[:, None]*1e-12*mean, axis=0)
    actual_current = grid.sum(0)
    norm_bound = np.sum(area*1e-12*(np.linalg.norm(mean, axis=1)+abs(alpha)*radius*1e-6))
    error = float(np.linalg.norm(actual_current-expected_current))
    assert error <= max(norm_bound*1e-7, 1e-30)
    assert np.isfinite(grid).all()
    metrics = {'triangles': n, 'contained_triangles': len(inside), 'bbox_candidates': int(counts.sum()),
               'clipped_candidates': tested, 'positive_clipped_polygons': positive,
               'maximum_triangle_area_relative_error': float(area_relative.max()),
               'maximum_triangle_first_moment_scaled_error': float(moment_relative.max()),
               'integrated_current_error_a_m': error, 'current_error_scale_a_m': float(norm_bound),
               'native_clipping_elapsed_s': perf_counter()-started,
               'normalization_applied': False}
    return grid.reshape((*shape_yx, 2)), metrics


def self_check():
    base = np.array([[[.1, .2], [2.6, .3], [.2, 1.9]],
                     [[.1, .2], [1000.1, .2], [1000.1, .2000001]]])
    mean = np.array([[2.+3j, -1.+4j], [.1-.2j, .4+.3j]])
    alpha = np.array([2e5+3e4j, -5e3+1e4j])
    rows = []
    for shift in (np.zeros(2), np.array([1e5, -1e5])):
        vertices = base+shift
        origin = np.floor(vertices.min((0, 1)))
        shape = tuple((np.floor(vertices.max((0, 1))-origin).astype(int)+1)[::-1])
        grid, metrics = project(vertices, mean, alpha, origin, 1., shape)
        reverse, _ = project(vertices[:, [0, 2, 1]], mean, alpha, origin, 1., shape)
        assert np.max(abs(grid-reverse)) < 1e-20
        rows.append(metrics)
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true', required=True)
    parser.parse_args()
    print(json.dumps({'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
                      'status': 'PASS_GRID_AFFINE_MOMENT_SELF_CHECK', 'cases': self_check()}))
