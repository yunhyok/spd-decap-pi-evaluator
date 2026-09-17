"""EXP-3 S1: sub-cell rasterisation (PIL, ordered PowerSI booleans + traces drawn as
metal) and batched window-conductance homogenisation.

For a grid of cells of size h with sub-tiles of size s (h = n*s), the conductance of the
edge between two neighbouring cells is computed on the h-wide window running from
the centre of one cell to the centre of the other (4-neighbour resistor network on the
filled sub-tiles, faces held at 1/0 V).  G_rel = I * nx / ny is the conductance relative
to a full sheet of the same window (1 = solid copper).  Solved by batched Jacobi-
preconditioned CG over all mixed windows at once; full/empty windows are 1/0.

EXP-38 adds two opt-in accelerations to `batched_gx`; with neither flag set the path below is the
original one, line for line.  (A) SPD_PI_FAST=1: windows with the same bit pattern are solved once
(exact -- see the `batched_gx` docstring).  (B) SPD_PI_SOLVER=cudss: the same kernel runs on the
A2000 through cupy, falling back to numpy with one log line.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from fast_assemble import ON as FAST  # noqa: E402  (EXP-37/38 flag: SPD_PI_FAST=1)

_GPU = None


def _gpu():
    """cupy when SPD_PI_SOLVER=cudss and the A2000 answers, else None (EXP-38 (B), logged once).

    Same opt-in flag as the cuDSS factorisation -- it states the intent to use the GPU.  The batch
    is the caller's chunk as-is (window_conductance: 6e6 tiles = 48 MB per float64 array, ~15 live
    arrays -> well under 1 GB of the 8 GB VRAM), so no extra chunking is needed and the GPU solves
    exactly the batch the CPU would have, which keeps the CG iteration count the same.
    """
    global _GPU
    if _GPU is None:
        _GPU = False
        if os.environ.get("SPD_PI_SOLVER", "") == "cudss":
            try:
                import cupy
                float(cupy.zeros(1, np.float64).sum())
                _GPU = cupy
            except Exception as e:  # no cupy, no driver, no device -> numpy
                print(f"[homog] cupy unavailable ({type(e).__name__}: {e}); batched_gx stays on numpy", flush=True)
    return _GPU or None


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


def batched_gx(W, tol=1e-7, maxit=600, face_fix=False):
    """W bool (N, ny, nx). Relative x-conductance of each window.

    face_fix (EXP-28): inject/extract on the first/last column that has any metal instead of
    columns 0 / nx-1, so a window whose centre-line faces are bare copper-free does not read G = 0.
    Windows with metal on both faces (k0 = 0, k1 = nx-1) are bit-identical to the default path.

    EXP-38 (A), SPD_PI_FAST=1: windows with the same bit pattern are solved once and the result is
    copied back.  Every operation below is elementwise or a sum over axes (1, 2), i.e. each window's
    CG is independent of the others, so a duplicate follows a bit-identical trajectory and dropping
    it leaves the set of distinct residuals -- hence the batch-wide `np.all(rn <= tol*bnorm)` stop
    and the iteration count -- unchanged.  Verified bit-identical on the dumped Port14/Port18 window
    sets (WORK_DIR/exp38)."""
    W = W.astype(bool)
    N, ny, nx = W.shape
    if N == 0:
        return np.zeros(0)
    if FAST:
        _, first, inv = np.unique(np.packbits(W.reshape(N, -1), axis=1), axis=0,
                                  return_index=True, return_inverse=True)
        if len(first) < N:
            return _run(W[first], tol, maxit, face_fix)[inv.ravel()]
    return _run(W, tol, maxit, face_fix)


def _run(W, tol, maxit, face_fix):
    """One batch on the A2000 when EXP-38 (B) is on, on numpy if the card runs out of memory
    (four builds can share the 8 GB card).  Both give the same iteration count, cf. the unit check."""
    g = _gpu()
    if g is not None:
        try:
            return _gx(W, tol, maxit, face_fix, g)
        except MemoryError:                    # cupy.cuda.memory.OutOfMemoryError
            g.get_default_memory_pool().free_all_blocks()
            print(f"[homog] out of VRAM on a batch of {len(W)} windows; this batch on numpy", flush=True)
    return _gx(W, tol, maxit, face_fix, np)


def _gx(W, tol, maxit, face_fix, xp):
    """batched_gx's kernel on one batch, with xp = numpy or (EXP-38 (B)) cupy."""
    if xp is not np:
        W = xp.asarray(W)
    N, ny, nx = W.shape
    Wf = W.astype(np.float64)
    gh = Wf[:, :, :-1] * Wf[:, :, 1:]          # horizontal bonds
    gv = Wf[:, :-1, :] * Wf[:, 1:, :]          # vertical bonds
    diag = xp.zeros_like(Wf)
    diag[:, :, :-1] += gh; diag[:, :, 1:] += gh
    diag[:, :-1, :] += gv; diag[:, 1:, :] += gv
    if face_fix:
        colany = W.any(axis=1)                             # (N, nx): column has metal
        an = xp.arange(N)
        k0 = colany.argmax(axis=1)
        k1 = nx - 1 - colany[:, ::-1].argmax(axis=1)
        diag[an, :, k0] += 2.0 * Wf[an, :, k0]
        diag[an, :, k1] += 2.0 * Wf[an, :, k1]
    else:
        diag[:, :, 0] += 2.0 * Wf[:, :, 0]; diag[:, :, -1] += 2.0 * Wf[:, :, -1]
    active = diag > 0
    dinv = xp.where(active, 1.0 / xp.where(active, diag, 1.0), 0.0)
    bfull = xp.zeros_like(Wf)
    if face_fix:
        bfull[an, :, k0] = 2.0 * Wf[an, :, k0]
    else:
        bfull[:, :, 0] = 2.0 * Wf[:, :, 0]

    def A(v):
        out = diag * v
        dh = gh * v[:, :, 1:]; out[:, :, :-1] -= dh
        dh = gh * v[:, :, :-1]; out[:, :, 1:] -= dh
        dvv = gv * v[:, 1:, :]; out[:, :-1, :] -= dvv
        dvv = gv * v[:, :-1, :]; out[:, 1:, :] -= dvv
        return out

    x = xp.zeros_like(Wf)
    r = bfull - A(x)
    z = dinv * r
    p = z.copy()
    rz = xp.sum(r * z, axis=(1, 2))
    bnorm = xp.sqrt(xp.sum(bfull * bfull, axis=(1, 2))) + 1e-300
    it = -1
    for it in range(maxit):
        Ap = A(p)
        pAp = xp.sum(p * Ap, axis=(1, 2))
        alpha = xp.where(pAp > 0, rz / xp.where(pAp > 0, pAp, 1), 0.0)
        x += alpha[:, None, None] * p
        r -= alpha[:, None, None] * Ap
        rn = xp.sqrt(xp.sum(r * r, axis=(1, 2)))
        if bool(xp.all(rn <= tol * bnorm)):
            break
        z = dinv * r
        rz_new = xp.sum(r * z, axis=(1, 2))
        beta = xp.where(rz > 0, rz_new / xp.where(rz > 0, rz, 1), 0.0)
        p = z + beta[:, None, None] * p
        rz = rz_new
    if face_fix:
        I = xp.sum(2.0 * (1.0 - x[an, :, k0]) * Wf[an, :, k0], axis=1)
    else:
        I = xp.sum(2.0 * (1.0 - x[:, :, 0]) * Wf[:, :, 0], axis=1)
    G = I * nx / ny
    _gx.iters = it + 1                                     # for the EXP-38 unit check only
    return G if xp is np else xp.asnumpy(G)


def window_conductance(windows, chunk_tiles=6_000_000, face_fix=False):
    """windows bool (N, ny, nx) -> relative x conductance; exact 1/0 for full/empty."""
    N = windows.shape[0]
    out = np.zeros(N)
    fill = windows.reshape(N, -1).mean(1)
    full = fill == 1.0
    out[full] = 1.0
    mixed = np.nonzero((fill > 0) & ~full)[0]
    per = windows.shape[1] * windows.shape[2]
    step = max(1, chunk_tiles // per)
    for k in range(0, len(mixed), step):
        sel = mixed[k:k + step]
        out[sel] = batched_gx(windows[sel], face_fix=face_fix)
    g = _gpu()
    if g is not None:  # hand the chunk's VRAM back between sheets, so cuDSS gets the card intact
        g.get_default_memory_pool().free_all_blocks()
    return out


def cell_edges(img, sub, face_fix=False):
    """img (ny*sub, nx*sub) -> fill (ny,nx), Gx (ny, nx-1) between (j,i)-(j,i+1),
    Gy (ny-1, nx) between (j,i)-(j+1,i). Windows span centre-to-centre, width = one cell."""
    H, Wd = img.shape
    ny, nx = H // sub, Wd // sub
    im = img[: ny * sub, : nx * sub]
    fill = im.reshape(ny, sub, nx, sub).mean(axis=(1, 3))
    half = sub // 2
    # x windows: columns [i*sub+half, (i+1)*sub+half), rows of cell j
    if nx > 1:
        core = im[:, half: half + (nx - 1) * sub]
        wx = core.reshape(ny, sub, nx - 1, sub).transpose(0, 2, 1, 3).reshape(-1, sub, sub)
        Gx = window_conductance(wx, face_fix=face_fix).reshape(ny, nx - 1)
    else:
        Gx = np.zeros((ny, 0))
    if ny > 1:
        core = im[half: half + (ny - 1) * sub, :]
        wy = core.reshape(ny - 1, sub, nx, sub).transpose(0, 2, 3, 1).reshape(-1, sub, sub)  # rotate: x<->y
        Gy = window_conductance(wy, face_fix=face_fix).reshape(ny - 1, nx)
    else:
        Gy = np.zeros((0, nx))
    return fill, Gx, Gy


def selfcheck_face_fix():
    """EXP-28 synthetic check of batched_gx(face_fix=...).  Nothing is read or written."""
    ny = nx = 20
    strip = np.zeros((1, ny, nx), bool); strip[0, :, 3:17] = True      # columns 3..16, full rows
    g0 = batched_gx(strip)[0]
    g1 = batched_gx(strip, face_fix=True)[0]
    m = 17 - 3                                                        # conducting columns k0..k1
    # series: 1/2 (source) + (m-1) bonds + 1/2 (sink) per row -> I = ny/m, G = I*nx/ny = nx/m
    print(f"[selfcheck] strip cols 3..16  G_orig={g0:.12f}  G_face_fix={g1:.12f}  "
          f"analytic nx/m={nx/m:.12f}  (plan section 2 predicted 1.0)")

    hole = np.ones((1, ny, nx), bool); hole[0, 8:12, 5:15] = False     # full faces, hole in the middle
    h0 = batched_gx(hole)[0]
    h1 = batched_gx(hole, face_fix=True)[0]
    print(f"[selfcheck] full-face w/ hole G_orig={h0:.12f}  G_face_fix={h1:.12f}  maxabsdiff={abs(h0-h1):.3e}")

    ok = (g0 == 0.0) and abs(g1 - nx / m) <= 1e-9 and abs(h0 - h1) == 0.0
    assert ok, (g0, g1, nx / m, h0, h1)
    print("SELFCHECK-HOMOG", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selfcheck_face_fix())
