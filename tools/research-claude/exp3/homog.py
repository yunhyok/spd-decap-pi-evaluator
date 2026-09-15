"""EXP-3 S1: sub-cell rasterisation (PIL, ordered PowerSI booleans + traces drawn as
metal) and batched window-conductance homogenisation.

For a grid of cells of size h with sub-tiles of size s (h = n*s), the conductance of the
edge between two neighbouring cells is computed on the h-wide window running from
the centre of one cell to the centre of the other (4-neighbour resistor network on the
filled sub-tiles, faces held at 1/0 V).  G_rel = I * nx / ny is the conductance relative
to a full sheet of the same window (1 = solid copper).  Solved by batched Jacobi-
preconditioned CG over all mixed windows at once; full/empty windows are 1/0.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw


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


def batched_gx(W, tol=1e-7, maxit=600):
    """W bool (N, ny, nx). Relative x-conductance of each window."""
    W = W.astype(bool)
    N, ny, nx = W.shape
    if N == 0:
        return np.zeros(0)
    Wf = W.astype(np.float64)
    gh = Wf[:, :, :-1] * Wf[:, :, 1:]          # horizontal bonds
    gv = Wf[:, :-1, :] * Wf[:, 1:, :]          # vertical bonds
    diag = np.zeros_like(Wf)
    diag[:, :, :-1] += gh; diag[:, :, 1:] += gh
    diag[:, :-1, :] += gv; diag[:, 1:, :] += gv
    diag[:, :, 0] += 2.0 * Wf[:, :, 0]; diag[:, :, -1] += 2.0 * Wf[:, :, -1]
    active = diag > 0
    dinv = np.where(active, 1.0 / np.where(active, diag, 1.0), 0.0)
    b = 2.0 * Wf[:, :, 0].copy()
    bfull = np.zeros_like(Wf); bfull[:, :, 0] = b

    def A(v):
        out = diag * v
        dh = gh * v[:, :, 1:]; out[:, :, :-1] -= dh
        dh = gh * v[:, :, :-1]; out[:, :, 1:] -= dh
        dvv = gv * v[:, 1:, :]; out[:, :-1, :] -= dvv
        dvv = gv * v[:, :-1, :]; out[:, 1:, :] -= dvv
        return out

    x = np.zeros_like(Wf)
    r = bfull - A(x)
    z = dinv * r
    p = z.copy()
    rz = np.sum(r * z, axis=(1, 2))
    bnorm = np.sqrt(np.sum(bfull * bfull, axis=(1, 2))) + 1e-300
    for it in range(maxit):
        Ap = A(p)
        pAp = np.sum(p * Ap, axis=(1, 2))
        alpha = np.where(pAp > 0, rz / np.where(pAp > 0, pAp, 1), 0.0)
        x += alpha[:, None, None] * p
        r -= alpha[:, None, None] * Ap
        rn = np.sqrt(np.sum(r * r, axis=(1, 2)))
        if np.all(rn <= tol * bnorm):
            break
        z = dinv * r
        rz_new = np.sum(r * z, axis=(1, 2))
        beta = np.where(rz > 0, rz_new / np.where(rz > 0, rz, 1), 0.0)
        p = z + beta[:, None, None] * p
        rz = rz_new
    I = np.sum(2.0 * (1.0 - x[:, :, 0]) * Wf[:, :, 0], axis=1)
    return I * nx / ny


def window_conductance(windows, chunk_tiles=6_000_000):
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
        out[sel] = batched_gx(windows[sel])
    return out


def cell_edges(img, sub):
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
        Gx = window_conductance(wx).reshape(ny, nx - 1)
    else:
        Gx = np.zeros((ny, 0))
    if ny > 1:
        core = im[half: half + (ny - 1) * sub, :]
        wy = core.reshape(ny - 1, sub, nx, sub).transpose(0, 2, 3, 1).reshape(-1, sub, sub)  # rotate: x<->y
        Gy = window_conductance(wy).reshape(ny - 1, nx)
    else:
        Gy = np.zeros((0, nx))
    return fill, Gx, Gy
