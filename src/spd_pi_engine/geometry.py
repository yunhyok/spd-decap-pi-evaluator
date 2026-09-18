"""Pure geometry for the PI engine: PowerSI ordered-boolean rasterisation at cell centres
(EXP-1 `model.rasterize`), whole-layer mask reuse (EXP-39 `fast_layer_mask`), the PIL sub-cell
artwork used by the homogenisation (EXP-3 `homog.raster_image`) and the scanline point-in-path
evaluator (EXP-40 `common/scanline.py`).

Ported line for line from `tools/research-claude/`.  The only change is plan C8: the import-time
`SPD_PI_FAST` latch (`scanline.ON`, re-exported as `model._SCAN_ON`) is now the explicit `fast=`
argument of `rasterize` / `fast_layer_mask`, whose default False is the research default path.

Scanline evaluator -- the rule it reproduces
--------------------------------------------
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

import math

import numpy as np
from matplotlib.path import Path as MplPath
from PIL import Image, ImageDraw

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


# ----------------------------------------------------------------------------
# rasterisation (EXP-1)
# ----------------------------------------------------------------------------
def geom_bbox(geom):
    allp = np.vstack([p.reshape(-1, 2) for p in geom["pos_polys"]] +
                     [np.array([[c[0] - c[2], c[1] - c[2]], [c[0] + c[2], c[1] + c[2]]]) for c in geom["pos_circles"]])
    return allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max()


def rasterize(geom, h, window=None, fast=False):
    """Evaluate the ordered PowerSI boolean artwork at cell centres of a uniform
    grid. ``window`` = (x0, y0, x1, y1) grid-aligned extent; default = bbox.

    ``fast`` (plan C8, was the import-time ``model._SCAN_ON``) routes polygons with >= V0
    vertices through the scanline evaluator above; False = matplotlib, the research default."""
    if window is None:
        bx = geom_bbox(geom)
        x0 = math.floor(bx[0] / h) * h; y0 = math.floor(bx[1] / h) * h
        nx = int(math.ceil((bx[2] - x0) / h)); ny = int(math.ceil((bx[3] - y0) / h))
    else:
        x0, y0 = window[0], window[1]
        nx = int(round((window[2] - window[0]) / h)); ny = int(round((window[3] - window[1]) / h))
    mask = np.zeros((ny, nx), bool)
    xc = x0 + (np.arange(nx) + 0.5) * h; yc = y0 + (np.arange(ny) + 0.5) * h
    col = {"positive_polygon": geom["pos_polys"], "negative_polygon": geom["neg_polys"],
           "positive_circle": geom["pos_circles"], "negative_circle": geom["neg_circles"]}
    for kind, k in geom["order"]:
        item = col[kind][k]
        if kind.endswith("polygon"):
            pts = item.reshape(-1, 2)
            bx0, by0 = pts.min(0); bx1, by1 = pts.max(0)
        else:
            bx0, by0, bx1, by1 = item[0] - item[2], item[1] - item[2], item[0] + item[2], item[1] + item[2]
        i0 = max(0, int((bx0 - x0) / h - 0.5)); i1 = min(nx, int((bx1 - x0) / h + 1.5))
        j0 = max(0, int((by0 - y0) / h - 0.5)); j1 = min(ny, int((by1 - y0) / h + 1.5))
        if i1 <= i0 or j1 <= j0:
            continue
        hit = None
        if fast and kind.endswith("polygon") and len(pts) >= V0:   # EXP-40
            hit = inside(pts, xc[i0:i1], yc[j0:j1])
        if hit is None:
            X, Y = np.meshgrid(xc[i0:i1], yc[j0:j1])
            if kind.endswith("polygon"):
                hit = MplPath(pts).contains_points(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
            else:
                hit = (X - item[0]) ** 2 + (Y - item[1]) ** 2 <= item[2] ** 2
        if kind.startswith("positive"):
            mask[j0:j1, i0:i1] |= hit
        else:
            mask[j0:j1, i0:i1] &= ~hit
    return dict(x0=x0, y0=y0, nx=nx, ny=ny, h=h, mask=mask)



def fast_layer_mask(owner, key, geom, blk, fast=False):
    """EXP-39 (opt-in, SPD_PI_FAST=1): one block's mask cut out of a whole-layer raster.

    Returns None when the caller must rasterise the block itself -- that is the default path, and
    it is what happens for every block unless the slice is provably the same array.

    The cached raster is ``rasterize(geom, h)`` verbatim, so its cell centres are
    ``floor(bbox/h)*h + (k+0.5)h``.  A block whose origin sits on that same lattice samples the
    same centres, and every cell outside the layer bbox is outside every primitive, i.e. False --
    so a block that sticks out of the raster is zero-filled there, exactly as
    ``rasterize(window=block)`` would leave it.  Off-lattice blocks fall back.

    The raster is only built when the layer bbox already fits inside the requesting block, in
    which case it evaluates a subset of that block's own points and so is free.  Building it for a
    net wider than the window would not be: ``contains_points`` is O(points x vertices) and these
    planes carry 45k-vertex outlines (EXP-39 §1), so one oversized raster costs more than all the
    repeat calls it could ever save.
    """
    h = blk["h"]
    store = owner.__dict__.setdefault("_layer_rasters", {})
    r = store.get(key)
    if r is None:
        bx = geom_bbox(geom)
        x0 = math.floor(bx[0] / h) * h; y0 = math.floor(bx[1] / h) * h
        nx = int(math.ceil((bx[2] - x0) / h)); ny = int(math.ceil((bx[3] - y0) / h))
        if not (blk["x0"] <= x0 and blk["y0"] <= y0 and x0 + nx * h <= blk["x0"] + blk["nx"] * h
                and y0 + ny * h <= blk["y0"] + blk["ny"] * h):
            return None
        r = store[key] = rasterize(geom, h, fast=fast)
    di = (blk["x0"] - r["x0"]) / h; dj = (blk["y0"] - r["y0"]) / h
    if abs(di - round(di)) > 1e-9 or abs(dj - round(dj)) > 1e-9:
        return None
    di = int(round(di)); dj = int(round(dj))
    m = np.zeros((blk["ny"], blk["nx"]), bool)
    i0 = max(di, 0); j0 = max(dj, 0)
    i1 = min(di + blk["nx"], r["nx"]); j1 = min(dj + blk["ny"], r["ny"])
    if i1 > i0 and j1 > j0:
        m[j0 - dj:j1 - dj, i0 - di:i1 - di] = r["mask"][j0:j1, i0:i1]
    return m



# ----------------------------------------------------------------------------
# sub-cell artwork (EXP-3)
# ----------------------------------------------------------------------------
def raster_image(geom, traces, x0, y0, nx, ny, s):
    """uint8 image (ny, nx): 1 = metal. geom: ordered primitives dict or None;
    traces: list of (xa, ya, xb, yb, w_um) drawn as metal after the boolean artwork."""
    img = Image.new("L", (nx, ny), 0)
    dr = ImageDraw.Draw(img)

    def P(x, y):
        return ((x - x0) / s, (y - y0) / s)  # offset 0 best matches cell-centre sampling (checked)

    if geom is not None:
        col = {"positive_polygon": geom["pos_polys"], "negative_polygon": geom["neg_polys"],
               "positive_circle": geom["pos_circles"], "negative_circle": geom["neg_circles"]}
        for kind, k in geom["order"]:
            item = col[kind][k]
            fill = 1 if kind.startswith("positive") else 0
            if kind.endswith("polygon"):
                pts = item.reshape(-1, 2)
                dr.polygon([P(x, y) for x, y in pts], fill=fill)
            else:
                cx, cy, r = item
                (a, b), (c, d) = P(cx - r, cy - r), P(cx + r, cy + r)
                dr.ellipse([a, b, c, d], fill=fill)
    for xa, ya, xb, yb, w in traces:
        wp = max(1, int(round(w / s)))
        dr.line([P(xa, ya), P(xb, yb)], fill=1, width=wp)
        r = w / 2
        for (x, y) in ((xa, ya), (xb, yb)):
            (a, b), (c, d) = P(x - r, y - r), P(x + r, y + r)
            dr.ellipse([a, b, c, d], fill=1)
    return np.asarray(img, dtype=bool)


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
    # rasterize / raster_image on one square: both rules see the same 10 x 10 um of metal
    sq = np.array([[0.0, 0.0, 10.0, 0.0, 10.0, 10.0, 0.0, 10.0]])
    geom = dict(pos_polys=[sq[0]], neg_polys=[], pos_circles=[], neg_circles=[],
                order=[("positive_polygon", 0)])
    r = rasterize(geom, 1.0)
    assert r["mask"].shape == (10, 10) and r["mask"].all(), r["mask"]
    assert (rasterize(geom, 1.0, fast=True)["mask"] == r["mask"]).all()
    img = raster_image(geom, [], 0.0, 0.0, 20, 20, 0.5)
    assert img.shape == (20, 20) and img.all(), img
    print(f"  square: {r['nx']}x{r['ny']} cells, raster_image {img.sum()} of {img.size} sub-tiles")
    print("DEMO PASS")


if __name__ == "__main__":
    demo()
