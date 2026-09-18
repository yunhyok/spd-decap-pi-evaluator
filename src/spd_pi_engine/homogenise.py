"""Window-conductance homogenisation (EXP-3 S1, face_fix EXP-28, GPU/dedup EXP-38).

For a grid of cells of size h with sub-tiles of size s (h = n*s), the conductance of the
edge between two neighbouring cells is computed on the h-wide window running from
the centre of one cell to the centre of the other (4-neighbour resistor network on the
filled sub-tiles, faces held at 1/0 V).  G_rel = I * nx / ny is the conductance relative
to a full sheet of the same window (1 = solid copper).  Solved by batched Jacobi-
preconditioned CG over all mixed windows at once; full/empty windows are 1/0.

Ported line for line from `tools/research-claude/exp3/homog.py`.  Plan C7/C8/C9: the two
EXP-38 accelerations, which were the import-time `SPD_PI_FAST` latch (A: duplicate windows
solved once) and the `SPD_PI_SOLVER=cudss` process-global `_GPU` latch (B: the same kernel on
the A2000 through cupy), are now `backend`; `Backend()` is the original path, line for line.
The artwork rasteriser `raster_image` lives in `geometry.py`.
"""
from __future__ import annotations

import numpy as np

from .backend import DEFAULT, Backend  # noqa: F401  (Backend re-exported for callers)


def batched_gx(W, tol=1e-7, maxit=600, face_fix=False, backend=DEFAULT):
    """W bool (N, ny, nx). Relative x-conductance of each window.

    face_fix (EXP-28): inject/extract on the first/last column that has any metal instead of
    columns 0 / nx-1, so a window whose centre-line faces are bare copper-free does not read G = 0.
    Windows with metal on both faces (k0 = 0, k1 = nx-1) are bit-identical to the default path.

    EXP-38 (A), backend.fast (was SPD_PI_FAST=1): windows with the same bit pattern are solved once and the result is
    copied back.  Every operation below is elementwise or a sum over axes (1, 2), i.e. each window's
    CG is independent of the others, so a duplicate follows a bit-identical trajectory and dropping
    it leaves the set of distinct residuals -- hence the batch-wide `np.all(rn <= tol*bnorm)` stop
    and the iteration count -- unchanged.  Verified bit-identical on the dumped Port14/Port18 window
    sets (WORK_DIR/exp38)."""
    W = W.astype(bool)
    N, ny, nx = W.shape
    if N == 0:
        return np.zeros(0)
    if backend.fast:
        _, first, inv = np.unique(np.packbits(W.reshape(N, -1), axis=1), axis=0,
                                  return_index=True, return_inverse=True)
        if len(first) < N:
            return _run(W[first], tol, maxit, face_fix, backend)[inv.ravel()]
    return _run(W, tol, maxit, face_fix, backend)


def _run(W, tol, maxit, face_fix, backend=DEFAULT):
    """One batch on the A2000 when EXP-38 (B) is on, on numpy if the card runs out of memory
    (four builds can share the 8 GB card).  Both give the same iteration count, cf. the unit check."""
    g = backend.gpu()
    if g is not None:
        try:
            return _gx(W, tol, maxit, face_fix, g)
        except MemoryError:                    # cupy.cuda.memory.OutOfMemoryError
            g.get_default_memory_pool().free_all_blocks()
            print(f"[homogenise] out of VRAM on a batch of {len(W)} windows; this batch on numpy", flush=True)
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



def window_conductance(windows, chunk_tiles=6_000_000, face_fix=False, backend=DEFAULT):
    """windows bool (N, ny, nx) -> relative x conductance; exact 1/0 for full/empty.

    `chunk_tiles="auto"` sizes the batch for this box (`hardware.plan_chunk_tiles`: 6e6 per 8 GB
    of card, i.e. exactly the default on the A2000 and 6x that on a 48 GB A6000).  It is **opt-in
    and not numerics-neutral**: `batched_gx` iterates until every window in the batch has
    converged, so a bigger batch changes the iteration count and with it the last bits of G.
    The default is the 6e6 every receipt in `docs/engine/` was produced with.
    """
    if chunk_tiles == "auto":
        from .hardware import HardwareProfile, plan_chunk_tiles
        chunk_tiles = plan_chunk_tiles(HardwareProfile.detect())
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
        out[sel] = batched_gx(windows[sel], face_fix=face_fix, backend=backend)
    g = backend.gpu()
    if g is not None:  # hand the chunk's VRAM back between sheets, so cuDSS gets the card intact
        g.get_default_memory_pool().free_all_blocks()
    return out


def cell_edges(img, sub, face_fix=False, backend=DEFAULT):
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
        Gx = window_conductance(wx, face_fix=face_fix, backend=backend).reshape(ny, nx - 1)
    else:
        Gx = np.zeros((ny, 0))
    if ny > 1:
        core = im[half: half + (ny - 1) * sub, :]
        wy = core.reshape(ny - 1, sub, nx, sub).transpose(0, 2, 3, 1).reshape(-1, sub, sub)  # rotate: x<->y
        Gy = window_conductance(wy, face_fix=face_fix, backend=backend).reshape(ny - 1, nx)
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


def demo():
    """selfcheck_face_fix (verbatim) plus one check that cell_edges still walks a solid sheet."""
    rc = selfcheck_face_fix()
    solid = np.ones((20, 20), bool)                                   # solid copper: every G = 1
    fill, Gx, Gy = cell_edges(solid, 10)
    assert fill.shape == (2, 2) and (fill == 1.0).all() and (Gx == 1.0).all() and (Gy == 1.0).all()
    print(f"[demo] cell_edges on a solid 20x20 image, sub 10: fill {fill.shape} Gx {Gx.shape} "
          f"Gy {Gy.shape}, all 1")
    return rc


if __name__ == "__main__":
    raise SystemExit(selfcheck_face_fix())
