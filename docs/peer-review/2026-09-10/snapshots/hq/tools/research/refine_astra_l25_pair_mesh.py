"""Conforming midpoint refinement of selected saved-mesh edges; research only."""
from __future__ import annotations

import itertools

import numpy as np


def refine_marked_edges(points, triangles, marked_edges):
    """Split every incident triangle; share one midpoint per marked edge.

    The three-mark rule is the existing tri_fem_sheet._subdivide_triangles
    1-to-4 rule, applied to node indices. One/two-mark closures keep all other
    edges intact. The caller must restrict marks to free/free interior edges
    when electrode and natural-boundary subdivisions must remain unchanged.
    """
    points = np.asarray(points, dtype=float)
    triangles = np.asarray(triangles, dtype=np.int64)
    marked_edges = np.unique(np.sort(np.asarray(marked_edges, dtype=np.int64), axis=1), axis=0)
    if points.ndim != 2 or points.shape[1] != 2 or not np.all(np.isfinite(points)):
        raise ValueError("finite planar points required")
    if triangles.ndim != 2 or triangles.shape[1] != 3 or np.any(triangles < 0) or np.any(triangles >= len(points)):
        raise ValueError("triangle node indices out of range")
    if marked_edges.ndim != 2 or marked_edges.shape[1] != 2 or not len(marked_edges):
        raise ValueError("at least one marked edge required")
    if np.any(marked_edges < 0) or np.any(marked_edges >= len(points)) or np.any(marked_edges[:, 0] == marked_edges[:, 1]):
        raise ValueError("marked edge node indices invalid")
    node_count = len(points)
    if node_count*node_count >= np.iinfo(np.int64).max:
        raise ValueError("edge index encoding overflow")
    keys = marked_edges[:, 0]*node_count+marked_edges[:, 1]
    edges = np.sort(triangles[:, [[1, 2], [2, 0], [0, 1]]], axis=2)
    all_keys = edges[:, :, 0]*node_count+edges[:, :, 1]
    positions = np.searchsorted(keys, all_keys)
    found = (positions < len(keys)) & (keys[np.minimum(positions, len(keys)-1)] == all_keys)
    coverage = np.bincount(positions[found], minlength=len(keys))
    if np.any(coverage < 1) or np.any(coverage > 2):
        raise ValueError("marked edge is absent or nonmanifold")
    mids = np.where(found, node_count+positions, -1)
    affected = np.flatnonzero(found.any(axis=1))
    keep = np.flatnonzero(~found.any(axis=1))
    children, parents = [], []
    for parent in affected:
        tri, mid = triangles[parent], mids[parent]
        marked = np.flatnonzero(mid >= 0)
        if len(marked) == 1:
            i = int(marked[0])
            a, b, c, m = tri[(i+1) % 3], tri[(i+2) % 3], tri[i], mid[i]
            parts = ((c, a, m), (c, m, b))
        elif len(marked) == 2:
            i = int(np.flatnonzero(mid < 0)[0])
            j, k = (i+1) % 3, (i+2) % 3
            parts = ((tri[i], mid[k], mid[j]), (tri[j], tri[k], mid[j]), (tri[j], mid[j], mid[k]))
        else:
            a, b, c = tri
            bc, ca, ab = mid
            parts = ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))
        children.extend(parts)
        parents.extend([int(parent)]*len(parts))
    refined_points = np.vstack((points, (points[marked_edges[:, 0]]+points[marked_edges[:, 1]])*.5))
    refined_triangles = np.vstack((triangles[keep], np.asarray(children, dtype=np.int64)))
    parent_indices = np.r_[keep, np.asarray(parents, dtype=np.int64)]
    return refined_points, refined_triangles, parent_indices, marked_edges, coverage


def signed_twice_areas(points, triangles):
    vertices = points[triangles]
    a, b = vertices[:, 1]-vertices[:, 0], vertices[:, 2]-vertices[:, 0]
    return a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0]


def _self_check():
    points = np.array([[0., 0.], [2., .1], [.3, 1.], [2.3, 1.1]])
    original = np.array([[0, 1, 2], [1, 3, 2]])
    checked = 0
    for flipped in (False, True):
        triangles = original[:, [0, 2, 1]] if flipped else original
        candidates = np.unique(np.sort(triangles[:, [[1, 2], [2, 0], [0, 1]]].reshape(-1, 2), axis=1), axis=0)
        original_areas = signed_twice_areas(points, triangles)
        for count in range(1, len(candidates)+1):
            for selection in itertools.combinations(range(len(candidates)), count):
                new_points, new_triangles, parents, edges, coverage = refine_marked_edges(points, triangles, candidates[list(selection)])
                areas = signed_twice_areas(new_points, new_triangles)
                assert np.all(areas*original_areas[parents] > 0)
                assert np.allclose(np.bincount(parents, weights=areas), original_areas, rtol=1e-13, atol=0)
                assert len(new_triangles) == len(triangles)+int(coverage.sum())
                assert np.array_equal(new_points[:len(points)], points)
                assert np.array_equal(new_points[len(points):], points[edges].mean(axis=1))
                # Every geometric interior edge remains paired; no hanging-edge crack.
                child_edges = np.sort(new_triangles[:, [[1, 2], [2, 0], [0, 1]]].reshape(-1, 2), axis=1)
                unique, counts = np.unique(child_edges, axis=0, return_counts=True)
                assert np.all(counts <= 2)
                old_edges, old_counts = np.unique(np.sort(triangles[:, [[1, 2], [2, 0], [0, 1]]].reshape(-1, 2), axis=1), axis=0, return_counts=True)
                boundary = {tuple(edge) for edge in old_edges[old_counts == 1]}
                expected_boundary = set(boundary)
                for index, edge in enumerate(edges):
                    key = tuple(edge)
                    if key in boundary:
                        expected_boundary.remove(key)
                        mid = len(points)+index
                        expected_boundary.update((tuple(sorted((int(edge[0]), mid))), tuple(sorted((int(edge[1]), mid)))))
                assert {tuple(edge) for edge in unique[counts == 1]} == expected_boundary
                checked += 1
    print(f"SPD Decap PI Evaluator v0.23.1: marked-edge refinement SELF_CHECK PASS ({checked} cases)")


if __name__ == "__main__":
    _self_check()
