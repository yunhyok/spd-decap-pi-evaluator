"""EXP-40: matplotlib's point-in-path rule, evaluated one grid row at a time.  Off unless SPD_PI_FAST=1.

No physics change.  `model.rasterize` samples a *uniform* grid, so instead of asking matplotlib
O(points x vertices) questions (EXP-39 §1-3: one 102,456-vertex plane outline costs 338 s of a
395 s build) the polygon is walked once per grid row.  What follows is the rule this module
reproduces, transcribed from the installed matplotlib 3.10.9 `src/_path.h::point_in_path_impl`
(sdist sha256 fd66508e...2358, the PyPI sdist of the installed version).

Transcription
-------------
`Path(pts)` is built with `codes=None`, so `mpl::PathIterator::vertex` (src/py_adaptors.h) emits
MOVETO for pts[0], LINETO for pts[1:], then STOP; `has_codes()` is false, so `PathNanRemover` is a
pass-through for finite vertices and `conv_curve` has no curve code to expand.  `contains_points`
passes radius 0 and (transform=None ->) the identity affine, so `points_in_path` runs
`point_in_path_impl` on the vertices verbatim, after `if (path.total_vertices() < 3) return;`
(fewer than 3 vertices => everything False).

`point_in_path_impl` keeps (vtx0, vty0) and (vtx1, vty1) one vertex behind the fetched vertex, so
for pts[0..n-1] it tests the n edges pts[0]->pts[1], ..., pts[n-2]->pts[n-1] and -- in the block
after the inner loop, where STOP has reset (x, y) to the saved subpath start (sx, sy) -- the
closing edge pts[n-1]->pts[0].  That closing edge is what "the path is always treated as closed"
means: a no-codes `Path` is one implicitly closed subpath, n vertices giving exactly n edges.  (The
very first inner iteration runs with vty0 == vty1 == pts[0][1], a degenerate pts[0]->pts[0] edge
that can never straddle, so it contributes nothing.)  For each edge and each test point (tx, ty):

    yflag0 = (vty0 >= ty)                                   // non-strict, on the *edge* y
    yflag1 = (vty1 >= ty)                                   // non-strict
    if (yflag0 != yflag1)                                   // straddle: horizontal and zero-length
                                                            //   edges drop out here by themselves
        if (((vty1 - ty) * (vtx0 - vtx1) >= (vtx1 - tx) * (vty0 - vty1)) == yflag1)
            subpath_flag ^= 1                               // even-odd crossing count

    inside_flag |= subpath_flag                             // per subpath, OR'ed together

Two subtractions and one multiplication per side, then one `>=`, each separately rounded -- this
module evaluates that expression, not a divided-out crossing x.  `x1 + (ty-y1)*(x0-x1)/(y0-y1)`
answers the same question in exact arithmetic but rounds differently, and "exactly" here means the
same bit for a cell centre that lands on an edge.  Written out, the C++ predicate says `tx <= xcross`
when yflag1 is true (an upward edge, vty0 < ty <= vty1) and `tx < xcross` when it is false (a
downward edge) -- the asymmetry that makes a shared edge between two polygons count once.

The row reduction
-----------------
For a fixed edge and row, `fl(vtx1 - tx)` is monotone in tx and multiplying it by the fixed double
`(vty0 - vty1)` is monotone too (both correctly rounded), so the C++ predicate is monotone in tx:
the cells it flips are exactly a *prefix* of an ascending xc.  So one number per (row, edge) is
enough -- k, the length of that prefix -- and it is found by bisecting the predicate itself, which
keeps every comparison bit-identical to the C++.  Then the crossing count of column i is the number
of edges with k > i (a suffix sum of the k histogram), and the cell is inside when that is odd.

Cost: O(rows x edges) for the straddle test plus O(straddling pairs x log nx) plus O(rows x nx),
against O(rows x nx x edges) for `contains_points`.
"""
from __future__ import annotations

import numpy as np

from fast_assemble import ON  # noqa: F401  (SPD_PI_FAST=1; re-exported for model.rasterize)

V0 = 64             # speed-only threshold: below this the matplotlib call is already cheap
_PAIR_BUDGET = 1 << 23   # row x edge booleans held at once
_CELL_BUDGET = 1 << 21   # row x column histogram entries held at once


def inside(pts, xc, yc):
    """`MplPath(pts).contains_points(meshgrid(xc, yc))`, reshaped to (len(yc), len(xc)).

    `xc` must be strictly ascending (a uniform grid with h > 0 is).  Returns None when the caller
    must fall back to matplotlib: a non-finite vertex (matplotlib would run PathNanRemover's slow
    path), a non-ascending xc, or a degenerate vertex array.
    """
    v = np.asarray(pts, np.float64)                 # Path.__init__ -> _to_unmasked_float_array
    if v.ndim != 2 or v.shape[1] != 2 or v.shape[0] < 3 or not np.isfinite(v).all():
        return None
    xc = np.ascontiguousarray(xc, np.float64)
    yc = np.ascontiguousarray(yc, np.float64)
    nx, ny, ne = xc.size, yc.size, v.shape[0]
    if nx == 0 or ny == 0:
        return np.zeros((ny, nx), bool)
    if not (np.diff(xc) > 0).all():
        return None

    ex0, ey0 = v[:, 0], v[:, 1]
    ex1, ey1 = np.roll(ex0, -1), np.roll(ey0, -1)   # ..., pts[n-1] -> pts[0]: the implicit close
    out = np.empty((ny, nx), bool)
    step = max(1, min(_PAIR_BUDGET // ne, _CELL_BUDGET // (nx + 1)))
    nbits = int(nx).bit_length()                    # enough bisections to close [lo, hi)
    for j in range(0, ny, step):
        ty = yc[j:j + step]
        f0 = ey0[None, :] >= ty[:, None]            # yflag0 / yflag1, non-strict, as in _path.h
        f1 = ey1[None, :] >= ty[:, None]
        ri, ei = np.nonzero(f0 != f1)
        t = ty[ri]
        xa, xb = ex0[ei], ex1[ei]
        A = (ey1[ei] - t) * (xa - xb)               # (vty1 - ty) * (vtx0 - vtx1)
        D = ey0[ei] - ey1[ei]                       # (vty0 - vty1)
        up = f1[ri, ei]
        lo = np.zeros(ri.size, np.intp)
        hi = np.full(ri.size, nx, np.intp)
        for _ in range(nbits):
            mid = (lo + hi) >> 1
            act = mid < hi
            flip = ((A >= (xb - xc[np.minimum(mid, nx - 1)]) * D) == up) & act
            lo = np.where(flip, mid + 1, lo)
            hi = np.where(act & ~flip, mid, hi)
        cnt = np.bincount(ri * (nx + 1) + lo, minlength=ty.size * (nx + 1)).reshape(ty.size, nx + 1)
        s = np.cumsum(cnt[:, ::-1], axis=1)[:, ::-1]     # s[:, i] = #edges with k >= i
        out[j:j + step] = (s[:, 1:] & 1).astype(bool)    # column i is flipped by every k > i
    return out


def demo():
    """Self-check: 60 awkward polygons vs matplotlib on grids that hit vertices and edges."""
    from matplotlib.path import Path as MplPath

    rng = np.random.default_rng(40)
    bad = total = 0
    for case in range(60):
        n = int(rng.integers(3, 40))
        p = rng.integers(-6, 7, (n, 2)).astype(float) + (0.5 if case % 3 else 0.0)
        if case % 5 == 0:                                   # duplicate / collinear vertices
            p = np.repeat(p, 2, axis=0)
        if case % 7 == 0:
            p[:, 1] = np.round(p[:, 1])                     # many horizontal edges
        xc = np.arange(-7.0, 7.0, 0.5) + 0.25 * (case % 2)  # cell centres on and off the vertices
        yc = np.arange(-7.0, 7.0, 0.5) + 0.25 * (case % 2)
        got = inside(p, xc, yc)
        X, Y = np.meshgrid(xc, yc)
        ref = MplPath(p).contains_points(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
        bad += int(np.count_nonzero(got != ref))
        total += ref.size
    assert inside(np.zeros((2, 2)), np.arange(3.0), np.arange(3.0)) is None      # < 3 vertices
    assert inside(np.array([[0.0, 0.0], [1, np.nan], [1, 1]]), np.arange(3.0), np.arange(3.0)) is None
    print(f"  {total:,} cells over 60 polygons, differing {bad}")
    assert bad == 0
    print("DEMO PASS")


if __name__ == "__main__":
    demo()
