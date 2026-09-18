"""The frozen plane-pair + circuit model: `exp3.Model3` + `exp4.Model4` + `exp5.ModelB` as one class.

Plan §4 W2.  The three research classes were a three-deep inheritance chain whose behaviour was
finished by two monkeypatches and two attributes assigned after construction; here they are one
`Model` and one `ModelOptions`:

    C1  run4.patch_traces(mode)  (rebinds model3.copper_surface_impedance)  -> options.trace_zs_mode
    C2  model3.TwoSided = run8.TwoSidedAny                                  -> options.reference
    C3  ts.eps_table / c_all_refs / c_unit_fix injected after construction  -> ReferenceSearch args
    C4  Model4.mode / gnd_scale class attributes, ModelB.build's self.mode  -> options.zs_mode
    C5  GND_SHEETS / "Signal$TOP" / Stack.is_gnd                            -> DesignConventions
    C6  ModelB's `b["h"] >= 200.0` majority threshold                       -> options.two_sided_majority_h
    C8  fast_assemble.ON (import-time SPD_PI_FAST)                          -> backend.fast
    C9  os.environ["SPD_PI_SOLVER"]                                         -> backend.solver
    C10 _fa_pat / _cudss / _fa_tr / _fa_weff / _layer_rasters hidden caches -> explicit fields

Every numeric line is copied verbatim from the research files and the operation order is
unchanged (plan §0); the gates are the exp8 and exp28/p receipts.
"""
from __future__ import annotations

import collections
import math
import sys
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph
from scipy.sparse.linalg import splu
from scipy.spatial import cKDTree
from scipy.special import jv

from spd_decap_pi._core.solver.mfdm import MU_0_H_PER_M, copper_surface_impedance
from spd_decap_pi._core.via_model import (
    COPPER_CONDUCTIVITY_S_PER_M, HOLLOW_PLATED_BARREL, classify_via_conductor, estimate_via_segment_rl)

from . import homogenise, solver as SOL
from .backend import DEFAULT
from .geometry import raster_image
from .receipt import Result
from .reference import DesignConventions, ReferenceSearch, Stack

MU0 = MU_0_H_PER_M
VIA_NONE, VIA_SOLID, VIA_HOLLOW = 0, 1, 2  # EXP-20 via_R_skin conductor kinds (NONE = pad link, no skin)

#: `exp11/run11.py:44-47`, same defaults.  All off / 0.0 = the EXP-8 frozen model.
FLAGS = dict(eps_table=False, c_all_refs=False, zs_cell=False, zs_wall=False, c_unit_fix=False,
             fringe_no_thresh=False, fringe_no_cap=False, homog_L_noG=False, via_area_exact=False, via_L_twowire=False,
             zs_wall_skin=False, zs_wall_skin_re=False, via_R_skin=False, homog_face_fix=False,
             via_len_surface=False, void_fill_um=0.0)
FLAGS_LEGACY = dict(FLAGS)                                                              # EXP-8 receipt
FLAGS_P = {**FLAGS, "c_unit_fix": True, "homog_face_fix": True}                          # EXP-28 p = D7 baseline
FLAGS_Q = {**FLAGS_P, "via_len_surface": True}                                           # EXP-32 q
FLAGS_PMK = {**FLAGS_P, "via_R_skin": True, "zs_wall_skin_re": True}                     # EXP-35 pmk


@dataclass
class ModelOptions:
    """Everything the research chain fixed in class bodies, monkeypatches and call sites.

    Defaults = the frozen path, except `flags`, which the caller picks (FLAGS_LEGACY reproduces
    the EXP-8 receipt, FLAGS_P the exp28/p baseline).
    """

    reference: str = "powersi-compatible"   # C2: "powersi-compatible" (any net) | "physical-gnd" (DGND)
    flags: dict = field(default_factory=lambda: dict(FLAGS))
    # mesh
    h: float = 200.0
    fh: float = 50.0
    top_h: float = 50.0
    sub: tuple = (20, 10, 10)               # sub-tiles per cell: coarse, fine, top
    fringe: bool = False
    fringe_wd: float = 5.0
    fine_box: tuple | None = None
    max_layers: int = 3
    # explicit GND sheets (S3); None = ideal GND reference, the frozen path
    gnd: dict | None = None
    gnd_h: float | None = None
    gnd_fh: float | None = None
    # C4 / C1 / C6
    zs_mode: str = "b"                      # "b" = frozen (two-sided Zs on majority-two-sided planes)
    trace_zs_mode: str = "one_sided"        # C1: "one_sided" (frozen) | "real_only" (run4 mode "a")
    two_sided_majority_h: float = 200.0
    conventions: DesignConventions = field(default_factory=DesignConventions)


def peak_rss_mb() -> float:
    """`common/paths.peak_rss_mb` (receipt field `rss_MB`): peak resident memory of this process."""
    try:
        import resource  # POSIX only
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / (1024.0 * 1024.0) if sys.platform == "darwin" else r / 1024.0
    except ImportError:
        pass
    try:
        import psutil  # type: ignore
        mi = psutil.Process().memory_info()
        return getattr(mi, "peak_wset", mi.rss) / (1024.0 * 1024.0)
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32"); psapi = ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return c.PeakWorkingSetSize / (1024.0 * 1024.0)
    return float("nan")


def zs_one(f, sigma, t):
    """The product's one-face finite-thickness copper surface impedance (`run4.zs_one`)."""
    return copper_surface_impedance(f, sigma, t)


def zs_two(f, sigma, t):
    """`run4.zs_two`: symmetric two-sided sheet impedance 1/2 Zc coth(gamma t/2)."""
    w = 2 * math.pi * f
    k = complex(math.sqrt(w * MU0 * sigma / 2), math.sqrt(w * MU0 * sigma / 2))  # (1+j)/delta
    x = k * t / 2
    if abs(x) < 1e-3:
        coth = 1 / x + x / 3 - x ** 3 / 45
    else:
        coth = 1 / np.tanh(x)
    zc = k / sigma  # (1+j)/(sigma delta)
    return complex(0.5 * zc * coth)


def pad_traces(nodes, padstacks, layer):
    """Pad footprints of nodes on `layer` as short 'traces' (filled rectangles/circles)."""
    out = []
    for nid, (x, y, lay, ps) in nodes.items():
        if lay != layer or not ps or ps not in padstacks:
            continue
        pd = padstacks[ps]
        if layer not in pd["layers"] or not pd["pad_w"]:
            continue
        pw, ph = pd["pad_w"], pd["pad_h"] or pd["pad_w"]
        if pw >= ph:
            out.append((x - (pw - ph) / 2, y, x + (pw - ph) / 2, y, ph))
        else:
            out.append((x, y - (ph - pw) / 2, x, y + (ph - pw) / 2, pw))
    return out


def fill_small_voids(geom, dmax):
    """EXP-36: drop the negative primitives of one layer geometry smaller than `dmax` um.

    Size measure copied verbatim from EXP-6 `run6.fill_small` / `void_sizes`: the area-equivalent
    diameter 2*sqrt(A/pi) (for a circle exactly its diameter 2r; for a polygon NOT the bbox max side).
    Only `order` is filtered -- `raster_image` walks `order` and indexes the (untouched)
    pos_*/neg_* lists through it, so the indices must stay valid.  Returns (filtered copy, n_removed).
    """
    drop = set()
    for k, (kind, i) in enumerate(geom["order"]):
        if kind == "negative_polygon":
            p = geom["neg_polys"][i].reshape(-1, 2)
            x, y = p[:, 0], p[:, 1]
            a = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        elif kind == "negative_circle":
            a = math.pi * geom["neg_circles"][i][2] ** 2
        else:
            continue
        if 2.0 * math.sqrt(a / math.pi) < dmax:
            drop.add(k)
    g = dict(geom)
    g["order"] = [st for k, st in enumerate(geom["order"]) if k not in drop]
    return g, len(drop)


def reduce_series(elems, fixed):
    adj = collections.defaultdict(dict)

    def add(a, b, r):
        if a == b:
            return
        if b in adj[a]:
            g = 1 / adj[a][b] + 1 / r; adj[a][b] = adj[b][a] = 1 / g
        else:
            adj[a][b] = r; adj[b][a] = r
    for a, b, r in elems:
        add(a, b, r)
    stack = [n for n in list(adj) if n not in fixed and len(adj[n]) <= 2]
    while stack:
        n = stack.pop()
        if n in fixed or n not in adj:
            continue
        nb = adj[n]
        if len(nb) == 0:
            del adj[n]
        elif len(nb) == 1:
            (m, r), = nb.items(); del adj[m][n]; del adj[n]
            if m not in fixed and len(adj[m]) <= 2:
                stack.append(m)
        elif len(nb) == 2:
            (m1, r1), (m2, r2) = nb.items()
            del adj[m1][n]; del adj[m2][n]; del adj[n]
            add(m1, m2, r1 + r2)
            for m in (m1, m2):
                if m not in fixed and len(adj[m]) <= 2:
                    stack.append(m)
    return [(a, b, r) for a, nb in adj.items() for b, r in nb.items() if str(a) < str(b)]


class Sheet:
    """Homogenised conductor sheet for one layer: coarse block (+ optional fine block)."""

    def __init__(self, layer, geom, traces, h, sub_c, fh, sub_f, fine_box, bbox, n0, log=print,
                 face_fix=False, backend=DEFAULT):
        self.layer = layer
        self.iface = None
        self.backend = backend
        self._fa_weff = None  # C10: EXP-37 per-sheet weff cache, filled by solver.weff
        t0 = time.time()
        X0 = math.floor(bbox[0] / h) * h; Y0 = math.floor(bbox[1] / h) * h
        nx = int(math.ceil((bbox[2] - X0) / h)); ny = int(math.ceil((bbox[3] - Y0) / h))
        s = h / sub_c
        img = raster_image(geom, traces, X0, Y0, nx * sub_c, ny * sub_c, s)
        fill, Gx, Gy = homogenise.cell_edges(img, sub_c, face_fix=face_fix, backend=backend)
        c = dict(x0=X0, y0=Y0, nx=nx, ny=ny, h=h, fill=fill, Gx=Gx, Gy=Gy)
        self.blocks = [c]
        f = None
        if fine_box is not None and fh:
            fx0 = max(math.floor(fine_box[0] / h) * h, X0); fy0 = max(math.floor(fine_box[1] / h) * h, Y0)
            fx1 = min(math.ceil(fine_box[2] / h) * h, X0 + nx * h); fy1 = min(math.ceil(fine_box[3] / h) * h, Y0 + ny * h)
            if fx1 > fx0 and fy1 > fy0:
                fnx = int(round((fx1 - fx0) / fh)); fny = int(round((fy1 - fy0) / fh))
                fimg = raster_image(geom, traces, fx0, fy0, fnx * sub_f, fny * sub_f, fh / sub_f)
                ffill, fGx, fGy = homogenise.cell_edges(fimg, sub_f, face_fix=face_fix, backend=backend)
                f = dict(x0=fx0, y0=fy0, nx=fnx, ny=fny, h=fh, fill=ffill, Gx=fGx, Gy=fGy)
                ci0 = int(round((fx0 - X0) / h)); ci1 = int(round((fx1 - X0) / h)); cj0 = int(round((fy0 - Y0) / h)); cj1 = int(round((fy1 - Y0) / h))
                c["fill"] = c["fill"].copy(); c["fill"][cj0:cj1, ci0:ci1] = 0.0
                # interface windows on the coarse image: fine-cell centre -> coarse-cell centre
                self._interface(img, sub_c, s, c, f, ci0, ci1, cj0, cj1)
                self.blocks.append(f)
        self.n = n0
        for b in self.blocks:
            m = b["fill"] > 0
            ids = -np.ones(m.shape, np.int64); cnt = int(m.sum()); ids[m] = np.arange(self.n, self.n + cnt); self.n += cnt
            b["ids"] = ids; b["mask"] = m
        # edges: (a, b, ell_um, width_um, G)
        ea, eb, el, ew, eg, fa = [], [], [], [], [], []
        for b in self.blocks:
            ids, m = b["ids"], b["mask"]
            ok = m[:, :-1] & m[:, 1:] & (b["Gx"] > 1e-6)
            ea.append(ids[:, :-1][ok]); eb.append(ids[:, 1:][ok]); eg.append(b["Gx"][ok])
            el.append(np.full(int(ok.sum()), b["h"])); ew.append(np.full(int(ok.sum()), b["h"]))
            ok = m[:-1, :] & m[1:, :] & (b["Gy"] > 1e-6)
            ea.append(ids[:-1, :][ok]); eb.append(ids[1:, :][ok]); eg.append(b["Gy"][ok])
            el.append(np.full(int(ok.sum()), b["h"])); ew.append(np.full(int(ok.sum()), b["h"]))
        if f is not None and self.iface:
            fj, fi, cj, ci, g, ell = self.iface
            ok = f["mask"][fj, fi] & c["mask"][cj, ci] & (g > 1e-6)
            ea.append(f["ids"][fj, fi][ok]); eb.append(c["ids"][cj, ci][ok]); eg.append(g[ok])
            el.append(np.full(int(ok.sum()), ell)); ew.append(np.full(int(ok.sum()), f["h"]))
        self.edges = [np.concatenate(x) for x in (ea, eb, el, ew, eg)]
        self.cells = np.concatenate([b["ids"][b["mask"]] for b in self.blocks])
        self.cell_area = np.concatenate([(b["h"] ** 2) * b["fill"][b["mask"]] for b in self.blocks])
        log(f"[sheet] {layer}: cells {len(self.cells)} edges {len(self.edges[0])} "
            f"(mean G {np.mean(self.edges[4]) if len(self.edges[4]) else 0:.3f}) {time.time()-t0:.1f}s")
        self.trees = []
        for b in self.blocks:
            jj, ii = np.nonzero(b["mask"])
            self.trees.append((cKDTree(np.column_stack([b["x0"] + (ii + 0.5) * b["h"], b["y0"] + (jj + 0.5) * b["h"]])) if len(jj) else None, b["ids"][jj, ii]))

    def _interface(self, img, sub_c, s, c, f, ci0, ci1, cj0, cj1):
        h = c["h"]; fh = f["h"]
        ell = (h + fh) / 2
        L = int(round(ell / s)); Wp = max(1, int(round(fh / s)))
        wins, keys = [], []
        H_, W_ = img.shape
        for j in range(f["ny"]):
            for side in ("L", "R"):
                i = 0 if side == "L" else f["nx"] - 1
                ci = ci0 - 1 if side == "L" else ci1
                if not (0 <= ci < c["nx"]):
                    continue
                cy = f["y0"] + (j + 0.5) * fh; cj = int(math.floor((cy - c["y0"]) / h))
                xf = f["x0"] + (i + 0.5) * fh; xc = c["x0"] + (ci + 0.5) * h
                xa = min(xf, xc); r0 = int(round((cy - c["y0"]) / s - Wp / 2)); c0 = int(round((xa - c["x0"]) / s))
                if r0 < 0 or r0 + Wp > H_ or c0 < 0 or c0 + L > W_:
                    continue
                wins.append(img[r0:r0 + Wp, c0:c0 + L]); keys.append((j, i, cj, ci))
        for i in range(f["nx"]):
            for side in ("B", "T"):
                j = 0 if side == "B" else f["ny"] - 1
                cj = cj0 - 1 if side == "B" else cj1
                if not (0 <= cj < c["ny"]):
                    continue
                cx = f["x0"] + (i + 0.5) * fh; ci = int(math.floor((cx - c["x0"]) / h))
                yf = f["y0"] + (j + 0.5) * fh; yc = c["y0"] + (cj + 0.5) * h
                ya = min(yf, yc); c0 = int(round((cx - c["x0"]) / s - Wp / 2)); r0 = int(round((ya - c["y0"]) / s))
                if r0 < 0 or r0 + L > H_ or c0 < 0 or c0 + Wp > W_:
                    continue
                wins.append(img[r0:r0 + L, c0:c0 + Wp].T); keys.append((j, i, cj, ci))
        if not wins:
            self.iface = None; return
        W = np.stack(wins)
        g = homogenise.window_conductance(W, backend=self.backend)
        k = np.array(keys)
        self.iface = (k[:, 0], k[:, 1], k[:, 2], k[:, 3], g, ell)

    def snap(self, x, y):
        order = [1, 0] if len(self.blocks) == 2 else [0]
        for bi in order:
            b = self.blocks[bi]
            inside = b["x0"] <= x < b["x0"] + b["nx"] * b["h"] and b["y0"] <= y < b["y0"] + b["ny"] * b["h"]
            if bi == 1 and not inside:
                continue
            tree, ids = self.trees[bi]
            if tree is None:
                if bi == 1:
                    return None
                continue
            d, k = tree.query([x, y])
            if d <= 0.75 * b["h"] * math.sqrt(2) + 1e-9:
                return int(ids[k]), b["h"]
            if bi == 1:
                return None
        return None


class Model:
    """`Model3` + `Model4` + `ModelB`, built from one `ModelOptions` and one `Backend`."""

    def __init__(self, ex, shapes, options=None, backend=DEFAULT, log=None):
        opt = self.options = options or ModelOptions()
        self.backend = backend
        self.log = log or (lambda *a, **k: None)
        unknown = set(opt.flags) - set(FLAGS)
        if unknown:
            raise ValueError(f"unknown flags: {sorted(unknown)}")
        for k, v in FLAGS.items():
            setattr(self, k, opt.flags.get(k, v))
        if opt.zs_mode not in ("b", "orig", "a"):
            raise ValueError(f"zs_mode {opt.zs_mode!r}")
        if opt.trace_zs_mode not in ("one_sided", "real_only"):
            raise ValueError(f"trace_zs_mode {opt.trace_zs_mode!r}")
        self.mode = opt.zs_mode
        # C1: was run4.patch_traces rebinding model3's module-global copper_surface_impedance.
        # Consumed by assemble (off-plane traces), via_R (hollow barrels) and breakdown.
        self._zs_fn = (zs_one if opt.trace_zs_mode == "one_sided"
                       else (lambda f, s, t: complex(complex(zs_one(f, s, t)).real, 0.0)))
        self.conv = opt.conventions
        h, fh, top_h = opt.h, opt.fh, opt.top_h
        sub_c, sub_f, sub_top = opt.sub
        gnd, gnd_h, gnd_fh = opt.gnd, opt.gnd_h, opt.gnd_fh
        self.gnd_h = gnd_h or h; self.gnd_fh = gnd_fh or fh
        self.ex, self.shapes = ex, shapes
        self.h, self.fh, self.top_h, self.fine_box = h, fh, top_h, opt.fine_box
        self.fringe, self.fringe_wd = opt.fringe, opt.fringe_wd
        self.gnd = gnd  # None (ideal GND, variant B) or extract dict for S3
        self.st = Stack(ex["stackup"], self.conv.is_gnd)
        self.info = dict(h=h, fine_h=fh, top_h=top_h, sub_um_coarse=h / sub_c, sub_um_fine=fh / sub_f, sub_um_top=top_h / sub_top,
                         fringe=opt.fringe, fringe_w_over_d_max=opt.fringe_wd, explicit_gnd=gnd is not None,
                         gnd_sheet_h=gnd_h or h, gnd_sheet_fine_h=gnd_fh or fh,
                         **{k: getattr(self, k) for k in FLAGS})
        self.sub = (sub_c, sub_f, sub_top)
        # C10: the hidden per-model caches, now explicit
        self._fa_pat = None    # EXP-37 COO -> CSC pattern (assemble)
        self._fa_tr = None     # EXP-37 distinct (sigma, t) of the off-plane traces (solver.trace_zs)
        self._cudss = None     # one cuDSS solver per model (cuDSS 0.8 faults on a second one)
        self._build()

    @classmethod
    def build(cls, ex, shapes, options=None, backend=DEFAULT, log=None):
        """Build the model for one extracted rail (the engine's entry point, plan §2-3)."""
        return cls(ex, shapes, options, backend, log)

    # ------------------------------------------------------------------
    def _build(self):
        t0 = time.time()
        ex, st = self.ex, self.st
        rn = ex["rail_nodes"]
        lwd = ex.get("layer_default_width_um", {})
        geoms = {g["layer"]: g for g in ex["rail_geoms"]}
        # EXP-36: PowerSI "Special Void" convention -- small voids are metal-filled before rasterising.
        # Rail plane artwork only; ex is not mutated, so the reference search keeps the real voids.
        if self.void_fill_um > 0:
            removed = {}
            for L in list(geoms):
                geoms[L], removed[L] = fill_small_voids(geoms[L], self.void_fill_um)
            self.info["void_fill_removed"] = removed
        plane_layers = list(geoms)
        # rail traces drawn into their plane layer
        drawn = set()
        traces_by_layer = collections.defaultdict(list)
        for k, (s_, e_, w) in enumerate(ex["rail_traces"]):
            if s_ in rn and e_ in rn and rn[s_][2] == rn[e_][2] and rn[s_][2] in geoms:
                L = rn[s_][2]
                traces_by_layer[L].append((rn[s_][0], rn[s_][1], rn[e_][0], rn[e_][1], w or lwd.get(L, 25.0)))
                drawn.add(k)
        ts = ReferenceSearch(ex, self.shapes, self.options.max_layers, mode=self.options.reference,
                             eps_table=self.eps_table, c_all_refs=self.c_all_refs, c_unit_fix=self.c_unit_fix,
                             conventions=self.conv, backend=self.backend)
        self.reference_search = ts
        # stackup row index -> (sigma, t) for the wall impedance (EXP-12 (d)); non-conductor rows never referenced
        self.row_sigma = np.array([r["conductivity"] or 0.0 for r in ex["stackup"]], float)
        self.row_t = np.array([r["thickness_um"] * 1e-6 for r in ex["stackup"]], float)
        n = 0
        self.sheets = {}
        for L in plane_layers:
            tr = traces_by_layer[L] + pad_traces(rn, ex["padstacks"], L)
            pts = np.vstack([p.reshape(-1, 2) for p in geoms[L]["pos_polys"]] + [np.array([[t[0], t[1]], [t[2], t[3]]]) for t in tr])
            pad = 500.0
            bbox = (pts[:, 0].min() - pad, pts[:, 1].min() - pad, pts[:, 0].max() + pad, pts[:, 1].max() + pad)
            if L == self.conv.top_layer_name:
                sh = Sheet(L, geoms[L], tr, self.top_h, self.sub[2], None, None, None, bbox, n, self.log,
                           self.homog_face_fix, self.backend)
            else:
                sh = Sheet(L, geoms[L], tr, self.h, self.sub[0], self.fh, self.sub[1], self.fine_box, bbox, n, self.log,
                           self.homog_face_fix, self.backend)
            n = sh.n
            # two-sided d_eff per cell (EXP-1b rule), capacitance per metal area
            base = sh.cells.min() if len(sh.cells) else 0
            dloc = np.zeros(n - base); cloc = np.zeros(n - base); tloc = np.zeros(n - base)
            twoloc = np.zeros(n - base, bool); wuloc = np.full(n - base, -1, np.int16); wdloc = np.full(n - base, -1, np.int16)
            ct = []
            for b in sh.blocks:
                blk = dict(x0=b["x0"], y0=b["y0"], nx=b["nx"], ny=b["ny"], h=b["h"], mask=b["mask"])
                r = ts(L, blk)
                m = b["mask"]
                dloc[b["ids"][m] - base] = r["d_eff"][m]; cloc[b["ids"][m] - base] = r["c_um2"][m]; tloc[b["ids"][m] - base] = r["tand"][m]
                twoloc[b["ids"][m] - base] = r["two_sided"][m]
                wuloc[b["ids"][m] - base] = r["wall_up"][m]; wdloc[b["ids"][m] - base] = r["wall_dn"][m]
                if self.eps_table:
                    ct.append((b["ids"][m], m, r["c_tand_at"]))
            a, bb, ell, wid, g = sh.edges
            sh.d_edge = 0.5 * (dloc[a - base] + dloc[bb - base])
            sh.c_cell = cloc[sh.cells - base]; sh.tand_cell = tloc[sh.cells - base]
            # EXP-12: per-cell two-sidedness and wall rows (cheap, kept regardless of the flags)
            sh.two_cell = twoloc[sh.cells - base]
            sh.wall_up_cell = wuloc[sh.cells - base]; sh.wall_dn_cell = wdloc[sh.cells - base]
            sh.two_edge = twoloc[a - base] & twoloc[bb - base]
            # per edge: stackup rows of the up/down wall at endpoint a and at endpoint b (-1 = no wall)
            sh.wall_rows_edge = np.stack([wuloc[a - base], wdloc[a - base], wuloc[bb - base], wdloc[bb - base]])
            if self.eps_table:
                sh.ct_blocks, sh.ct_base, sh.ct_size = ct, base, n - base
            row = st.row(L); sh.sigma = row["conductivity"]; sh.t = row["thickness_um"] * 1e-6
            self.sheets[L] = sh
        self.info["reference_search"] = ts.report
        self.info["rail_traces_drawn_into_planes"] = len(drawn)
        # total shunt plane C at the fixed-eps_r default (nF); reflects c_unit_fix
        self.info["plane_C_total_nF"] = float(sum((sh.c_cell * sh.cell_area).sum() for sh in self.sheets.values())) * 1e9
        # ---------------- rail nodes ----------------------------------
        idx = {}; nh = {}; snapped = 0
        for nid, (x, y, lay, ps) in rn.items():
            r = self.sheets[lay].snap(x, y) if lay in self.sheets else None
            if r is not None:
                idx[nid], nh[nid] = r; snapped += 1
            else:
                idx[nid] = n; n += 1
        port = n; n += 1
        pos = {idx[x] for x in ex["port_pos_nodes"] if x in idx}
        remap = {p: port for p in pos}
        Rm = lambda i: remap.get(i, i)  # noqa: E731
        self.info.update(rail_nodes_snapped=snapped, port_pos=len(pos))
        # off-plane traces
        ta, tb, tsq, tsig, tt, tL = [], [], [], [], [], []
        for k, (s_, e_, w) in enumerate(ex["rail_traces"]):
            if k in drawn or s_ not in rn or e_ not in rn:
                continue
            a_, b_ = Rm(idx[s_]), Rm(idx[e_])
            if a_ == b_:
                continue
            xs, ys, Ls, _ = rn[s_]; xe, ye, _, _ = rn[e_]
            ln = max(math.hypot(xe - xs, ye - ys), 1.0); w = w or lwd.get(Ls, 25.0)
            row = st.row(Ls); ng = st.nearest_gnd(Ls); d = min(g_[1] for g_ in ng) if ng else 30.0
            ta.append(a_); tb.append(b_); tsq.append(ln / w); tsig.append(row["conductivity"]); tt.append(row["thickness_um"] * 1e-6)
            tL.append(MU0 * d * 1e-6 * ln / (w + 2 * d))
        self.traces = [np.array(x) for x in (ta, tb, tsq, tsig, tt, tL)]
        # rail vias (R, coax L) + pad links
        gxy = ex["gnd_xy_by_layer"]; gtrees = {L: cKDTree(v) for L, v in gxy.items() if len(v)}
        va, vb, vR, vL = [], [], [], []
        vkind, vD, vell, vtp = [], [], [], []  # EXP-20 (via_R_skin): per-via R(f) inputs
        rc = {}
        scaled_keys = set(); n_scaled = 0  # EXP-14 (g)
        vlr = []  # EXP-32: ell'/ell per rail via
        for up, lo, ps in ex["rail_vias"]:
            if up not in rn or lo not in rn:
                continue
            xu, yu, Lu, _ = rn[up]; _, _, Ll, _ = rn[lo]
            a_, b_ = Rm(idx[up]), Rm(idx[lo])
            if a_ == b_:
                continue
            ln = abs(st.z_center_um[Ll] - st.z_center_um[Lu])
            if self.via_len_surface:  # EXP-32: layer centres -> layer surfaces; one length for R, L and the skin term
                ln0 = ln
                ln += 0.5 * (st.row(Lu)["thickness_um"] + st.row(Ll)["thickness_um"])
                if ln0 > 0:
                    vlr.append(ln / ln0)
            pad = ex["padstacks"].get(ps, {}); drill = pad.get("drill_um") or 40.0
            key = (ps, Lu, Ll, ln)
            if key not in rc:
                r_ = estimate_via_segment_rl(length_um=ln, drill_diameter_um=drill, padstack_material=pad.get("material"),
                                             start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"]).resistance_ohm
                cl = classify_via_conductor(drill_diameter_um=drill, padstack_material=pad.get("material"),
                                            start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"])
                hollow = cl.conductor_model == HOLLOW_PLATED_BARREL
                if self.via_area_exact and hollow:  # EXP-14 (g): pi*D*t_p -> pi*(D*t_p - t_p^2) on plated barrels
                    r_ *= drill / (drill - min(20.0, drill / 4.0))
                    scaled_keys.add(key)
                rc[key] = (r_, VIA_HOLLOW if hollow else VIA_SOLID)
            sd = min(max(float(gtrees[Lu].query([xu, yu])[0]) if Lu in gtrees else 1000.0, drill), 1000.0)
            n_scaled += key in scaled_keys
            mu_2pi = MU0 / math.pi if self.via_L_twowire else MU0 / (2 * math.pi)  # EXP-14 (h)
            va.append(a_); vb.append(b_); vR.append(rc[key][0]); vL.append(mu_2pi * ln * 1e-6 * math.log(sd / (drill / 2)))
            vkind.append(rc[key][1]); vD.append(drill); vell.append(ln); vtp.append(min(20.0, drill / 4.0))
        self.via_len_ratio = np.array(vlr)  # EXP-32 diagnostics (empty when the flag is off)
        nvia = len(va)
        for a_, b_, r_ in self._pad_links(rn):
            a2, b2 = Rm(idx[a_]), Rm(idx[b_])
            if a2 != b2:
                va.append(a2); vb.append(b2); vR.append(r_); vL.append(1e-15)
                vkind.append(VIA_NONE); vD.append(0.0); vell.append(0.0); vtp.append(0.0)
        self.vias = [np.array(x) for x in (va, vb, vR, vL)]
        # EXP-20: aligned with self.vias[2]; Rdc is that same (already via_area_exact-scaled) DC value
        self.via_skin = (np.array(vkind, np.int8), np.array(vD, float), np.array(vell, float),
                         np.array(vtp, float), self.vias[2])
        self.info.update(rail_vias=nvia, rail_pad_links=len(va) - nvia, off_plane_traces=len(ta))
        if self.via_area_exact:
            self.info.update(via_area_exact_scaled=n_scaled, via_total=nvia)
        # ---------------- GND (S3) ------------------------------------
        self.gsheets = {}
        self.gnd_r = [np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0)]
        gidx = {}
        if self.gnd is not None:
            n = self._build_gnd(n, gidx)
        # ---------------- decaps --------------------------------------
        self.dec = []
        for dc in ex["decaps"]:
            if dc["rail_node"] not in idx:
                continue
            gnode = gidx.get(dc["gnd_node"], -1) if self.gnd is not None else -1
            self.dec.append((Rm(idx[dc["rail_node"]]), gnode, dc["model_id"]))
        self.info["decaps_gnd_to_reference_directly"] = sum(1 for d in self.dec if d[1] < 0)
        # ---------------- prune ---------------------------------------
        self.n_raw = n; self.port = port; self.remap = remap
        gi = n
        mp = np.arange(n + 1)
        for k_, v_ in remap.items():
            mp[k_] = v_
        fix = lambda x: np.where(np.asarray(x, np.int64) < 0, gi, np.asarray(x, np.int64))  # noqa: E731
        rows, cols = [], []
        for sh in list(self.sheets.values()) + list(self.gsheets.values()):
            rows.append(sh.edges[0]); cols.append(sh.edges[1])
        rows += [fix(self.traces[0]), fix(self.vias[0]), fix(self.gnd_r[0])]
        cols += [fix(self.traces[1]), fix(self.vias[1]), fix(self.gnd_r[1])]
        rows.append(fix([d[0] for d in self.dec])); cols.append(fix([d[1] for d in self.dec]))
        rr = mp[np.concatenate([np.asarray(r, np.int64) for r in rows])]; cc = mp[np.concatenate([np.asarray(c, np.int64) for c in cols])]
        G = sparse.coo_matrix((np.ones(len(rr)), (rr, cc)), shape=(n + 1, n + 1))
        _, lab = csgraph.connected_components(G, directed=False)
        keep = (lab == lab[port]) & (np.arange(n + 1) != gi)
        new = -np.ones(n + 1, np.int64); new[keep] = np.arange(int(keep.sum()))
        self.map = lambda x: new[mp[fix(x)]]  # noqa: E731
        self.N = int(keep.sum()); self.P = int(self.map([port])[0])
        self.info.update(unknowns=self.N, nodes_before_prune=n, decaps_connected=int(sum(1 for d in self.dec if keep[mp[d[0]]])),
                         build_seconds=time.time() - t0)
        self.log("[model3] " + str({k: v for k, v in self.info.items() if k != "reference_search"}))
        self._two_sided_majority()

    def _two_sided_majority(self):
        """`exp5.ModelB.build`'s post-pass (C6): the plane layers that get the two-sided Zs."""
        rep = self.info.get("reference_search", {})
        ts = set()
        frac = {}
        hmin = self.options.two_sided_majority_h
        for L, r in rep.items():
            cells = sum(b["cells"] for b in r["blocks"] if b["h"] >= hmin)
            two = sum(b["cells"] * b["two_sided"] for b in r["blocks"] if b["h"] >= hmin)
            frac[L] = two / cells if cells else 0.0
            if cells and two / cells > 0.5:
                ts.add(L)
        self.two_sided_layers = ts
        self.info["two_sided_fraction_coarse"] = frac
        self.info["two_sided_layers"] = sorted(ts)

    def _pad_links(self, nodes):
        by = collections.defaultdict(list)
        for nid, (x, y, lay, ps) in nodes.items():
            by[lay].append(nid)
        trees = {L: (cKDTree(np.array([(nodes[i][0], nodes[i][1]) for i in ids])), ids) for L, ids in by.items()}
        out = []
        for nid, (x, y, lay, ps) in nodes.items():
            if not ps or ps not in self.ex["padstacks"]:
                continue
            pd = self.ex["padstacks"][ps]
            if lay not in pd["layers"] or not pd["pad_w"]:
                continue
            pw, ph = pd["pad_w"], pd["pad_h"] or pd["pad_w"]
            tree, ids = trees[lay]
            row = self.st.row(lay); rl = 0.5 / (row["conductivity"] * row["thickness_um"] * 1e-6)
            for k in tree.query_ball_point([x, y], r=math.hypot(pw, ph) / 2 + 1.0):
                o = ids[k]
                if o == nid:
                    continue
                dx = abs(nodes[o][0] - x); dy = abs(nodes[o][1] - y)
                if (dx <= pw / 2 + 1 and dy <= ph / 2 + 1) or (dx <= ph / 2 + 1 and dy <= pw / 2 + 1):
                    out.append((nid, o, rl))
        return out

    def _build_gnd(self, n, gidx):
        ex, st, g = self.ex, self.st, self.gnd
        gn = g["nodes"]; lwd = ex.get("layer_default_width_um", {})
        win = g["window"]
        drawn = set(); tbl = collections.defaultdict(list)
        sheet_layers = list(self.conv.gnd_sheets) + [self.conv.top_layer_name]
        for k, (s_, e_, w) in enumerate(g["traces"]):
            if gn[s_][2] == gn[e_][2] and gn[s_][2] in sheet_layers:
                L = gn[s_][2]; tbl[L].append((gn[s_][0], gn[s_][1], gn[e_][0], gn[e_][1], w or lwd.get(L, 25.0))); drawn.add(k)
        for L in sheet_layers:
            geom = self.shapes.get(L, {}).get(self.conv.gnd_net)
            tr = tbl[L] + pad_traces(gn, ex["padstacks"], L)
            if L == self.conv.top_layer_name:
                # TOP DGND only where the rail TOP sheet is (DUT + decap area)
                b0 = self.sheets[self.conv.top_layer_name].blocks[0]
                bbox = (b0["x0"], b0["y0"], b0["x0"] + b0["nx"] * b0["h"], b0["y0"] + b0["ny"] * b0["h"])
                sh = Sheet("GND:" + L, geom, tr, self.top_h, self.sub[2], None, None, None, bbox, n, self.log,
                           self.homog_face_fix, self.backend)
            else:
                sh = Sheet("GND:" + L, geom, tr, self.gnd_h, int(round(self.gnd_h / 10)), self.gnd_fh, int(round(self.gnd_fh / 5)),
                           self.fine_box, win, n, self.log, self.homog_face_fix, self.backend)
            n = sh.n
            row = st.row(L); sh.sigma = row["conductivity"]; sh.t = row["thickness_um"] * 1e-6
            self.gsheets[L] = sh
        neg = set(ex["port_neg_nodes"])
        ref_links = []
        for nid, (x, y, lay, ps) in gn.items():
            r = self.gsheets[lay].snap(x, y) if lay in self.gsheets else None
            if nid in neg:
                gidx[nid] = -1
                if r is not None:
                    ref_links.append(r[0])
            elif r is not None:
                gidx[nid] = r[0]
        elems = []
        for k, (s_, e_, w) in enumerate(g["traces"]):
            if k in drawn:
                continue
            xs, ys, Ls, _ = gn[s_]; xe, ye, _, _ = gn[e_]
            ln = max(math.hypot(xe - xs, ye - ys), 1.0); w = w or lwd.get(Ls, 25.0); row = st.row(Ls)
            elems.append((s_, e_, ln / (w * row["conductivity"] * row["thickness_um"] * 1e-6), "trace"))
        rc = {}
        scaled_keys = set()
        for up, lo, ps in g["vias"]:
            Lu = gn[up][2]; Ll = gn[lo][2]
            ln = abs(st.z_center_um[Ll] - st.z_center_um[Lu])
            pad = ex["padstacks"].get(ps, {}); drill = pad.get("drill_um") or 40.0
            key = (ps, Lu, Ll)
            if key not in rc:
                r_ = estimate_via_segment_rl(length_um=ln, drill_diameter_um=drill, padstack_material=pad.get("material"),
                                             start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"]).resistance_ohm
                cl = classify_via_conductor(drill_diameter_um=drill, padstack_material=pad.get("material"),
                                            start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"])
                hollow = cl.conductor_model == HOLLOW_PLATED_BARREL
                if self.via_area_exact and hollow:  # EXP-14 (g): pi*D*t_p -> pi*(D*t_p - t_p^2) on plated barrels
                    r_ *= drill / (drill - min(20.0, drill / 4.0))
                    scaled_keys.add(key)
                rc[key] = (r_, VIA_HOLLOW if hollow else VIA_SOLID)
            elems.append((up, lo, rc[key], "via"))
        for a_, b_, r_ in self._pad_links(gn):
            elems.append((a_, b_, r_, "pad"))
        fixed = set(gidx) | {dc["gnd_node"] for dc in ex["decaps"]}
        # keep class tag through reduction by reducing per class-free R network (tags lost on merge -> 'chain')
        red = reduce_series([(a, b, r) for a, b, r, _ in elems], fixed)
        for a, b, r in red:
            for x in (a, b):
                if x not in gidx:
                    gidx[x] = n; n += 1
        for dc in ex["decaps"]:
            if dc["gnd_node"] not in gidx:
                gidx[dc["gnd_node"]] = n; n += 1
        top = self.conv.top_layer_name
        rsq_top = 1.0 / (st.row(top)["conductivity"] * st.row(top)["thickness_um"] * 1e-6)
        a = [gidx[x] for x, y, r in red] + ref_links
        b = [gidx[y] for x, y, r in red] + [-1] * len(ref_links)
        rr = [r for x, y, r in red] + [0.5 * rsq_top] * len(ref_links)
        self.gnd_r = [np.array(a, np.int64), np.array(b, np.int64), np.array(rr)]
        self.info.update(gnd_nodes=len(gn), gnd_traces_drawn=len(drawn), gnd_elements_reduced=len(red), gnd_ref_links=len(ref_links),
                         gnd_sheet_layers=sheet_layers)
        return n

    # ------------------------------------------------------------------
    def zs_plane(self, f, sh, rail):
        """`ModelB.zs_plane` (zs_mode "b", the frozen rule) / `Model4.zs_plane` modes orig and a."""
        one = complex(zs_one(f, sh.sigma, sh.t))
        if self.mode == "orig":
            return one
        if self.mode == "a":
            return complex(one.real, 0.0) if rail else one
        if rail and sh.layer in self.two_sided_layers:
            return zs_two(f, sh.sigma, sh.t)
        return one

    def wall_zs(self, sh, f, skin=False):
        """EXP-12 (d): per rail edge, the mean over its two endpoint cells of the return-plane Zs
        (one wall: that layer's Zs1; two walls: parallel; no wall: 0).
        EXP-18: skin=True uses Zs1 - 1/(sigma t) per wall (DC term removed, complex) before the
        parallel combination and edge averaging."""
        rows = sh.wall_rows_edge  # 4 x nedge: up/dn at endpoint a, up/dn at endpoint b
        z = np.zeros(rows.shape, complex)
        for i in np.unique(rows[rows >= 0]):
            zi = complex(zs_one(f, self.row_sigma[i], self.row_t[i]))
            if skin:
                zi = zi - 1.0 / (self.row_sigma[i] * self.row_t[i])
            z[rows == i] = zi
        out = np.zeros(rows.shape[1], complex)
        for k in (0, 2):
            zu, zd = z[k], z[k + 1]
            both = (rows[k] >= 0) & (rows[k + 1] >= 0)
            out += np.where(both, zu * zd / np.where(both, zu + zd, 1.0), zu + zd)
        return 0.5 * out

    def edge_z(self, sh, f, rail=True):
        """Edge impedance array for a sheet (`Model4.edge_z`)."""
        a, b, ell, wid, G = sh.edges
        zs = self.zs_plane(f, sh, rail)
        if rail and self.zs_cell:  # EXP-12 (c): cell-wise Zs2/Zs1 instead of the layer-majority rule
            zs = np.where(sh.two_edge, zs_two(f, sh.sigma, sh.t), complex(zs_one(f, sh.sigma, sh.t)))
        if rail and self.zs_wall:  # EXP-12 (d): add the return-plane (wall) Zs
            zs = zs + self.wall_zs(sh, f)
        if rail and self.zs_wall_skin:  # EXP-18: add the return-plane skin-only term (DC removed)
            zs = zs + self.wall_zs(sh, f, skin=True)
        if rail and self.zs_wall_skin_re:  # EXP-18b: real part only of the skin-only term (skin R, no added L)
            zs = zs + self.wall_zs(sh, f, skin=True).real
        R = zs * (ell / wid) / G
        if not rail:
            return R, np.zeros(len(a))
        w = 2 * math.pi * f
        weff = SOL.weff(self, sh) if self.backend.fast else self.weff_of(sh)[0]  # EXP-37: weff has no f
        XL = w * MU0 * sh.d_edge * 1e-6 * ell / weff
        return R, XL

    def weff_of(self, sh):
        """(effective width per rail edge, fringing-applied mask).  EXP-14 flags are additive:
        fringe_no_thresh (apply to every edge), fringe_no_cap (no min(., wid)), homog_L_noG (start from wid)."""
        _, _, ell, wid, G = sh.edges
        d = sh.d_edge
        weff = wid if self.homog_L_noG else G * wid
        use = np.zeros(len(wid), bool)
        if self.fringe:
            use = np.ones(len(wid), bool) if self.fringe_no_thresh else (weff < self.fringe_wd * d)
            grown = weff + 2 * d if self.fringe_no_cap else np.minimum(weff + 2 * d, wid)
            weff = np.where(use, grown, weff)
        return weff, use

    def sheet_c_tand(self, sh, f):
        """eps_table: (c_cell, tand_cell) for this sheet with the dielectric table evaluated at f."""
        cloc = np.zeros(sh.ct_size); tloc = np.zeros(sh.ct_size)
        for ids, m, at in sh.ct_blocks:
            c, td = at(f)
            cloc[ids - sh.ct_base] = c[m]; tloc[ids - sh.ct_base] = td[m]
        return cloc[sh.cells - sh.ct_base], tloc[sh.cells - sh.ct_base]

    def via_R(self, f):
        """Per-via series R at f.  Flag off -> the frozen DC array itself (bit-identical).

        EXP-20 (via_R_skin): SOLID = round-wire exact solution l*Re[gamma/(2 pi a sigma) J0(gamma a)/J1(gamma a)],
        HOLLOW = Re[Zs1(f, sigma, t_p)]*l/(pi D) (times the via_area_exact factor D/(D-t_p) when that flag is on),
        NONE (pad links) = the DC value.  Both AC forms tend to their DC value as f -> 0.
        """
        if not self.via_R_skin:
            return self.vias[2]
        kind, D_um, ell_um, tp_um, Rdc = self.via_skin
        R = Rdc.copy()
        if f <= 0:
            return R
        sig = COPPER_CONDUCTIVITY_S_PER_M
        gam = (1.0 + 1j) * math.sqrt(math.pi * f * MU0 * sig)  # (1+j)/delta
        k = np.nonzero(kind == VIA_SOLID)[0]
        if len(k):
            a = D_um[k] * 0.5e-6
            ga = gam * a
            ok = np.abs(ga) >= 1e-3  # below that J0/J1 -> 2/(gamma a) numerically: keep Rdc
            k, a, ga = k[ok], a[ok], ga[ok]
            if len(k):
                R[k] = ell_um[k] * 1e-6 * (gam / (2 * math.pi * a * sig) * jv(0, ga) / jv(1, ga)).real
        k = np.nonzero(kind == VIA_HOLLOW)[0]
        if len(k):
            for tp in np.unique(tp_um[k]):
                kk = k[tp_um[k] == tp]
                zs = complex(self._zs_fn(f, sig, tp * 1e-6)).real
                R[kk] = zs * (ell_um[kk] * 1e-6) / (math.pi * D_um[kk] * 1e-6)
            if self.via_area_exact:
                R[k] *= D_um[k] / (D_um[k] - tp_um[k])
        return R

    def assemble(self, f):
        w = 2 * math.pi * f
        rows, cols, vals = [], [], []
        N = self.N
        fast = self.backend.fast
        pat = self._fa_pat if fast else None  # EXP-37: cached -> skip rows/cols

        def st_(a, b, y):
            if pat is not None:  # same value order, rows/cols already in the cached pattern
                y = np.broadcast_to(y, np.shape(a))
                vals.extend([y, y, -y, -y]); return
            a = self.map(a); b = self.map(b)
            a = np.where(a < 0, N, a); b = np.where(b < 0, N, b)
            y = np.broadcast_to(y, a.shape)
            rows.extend([a, b, a, b]); cols.extend([a, b, b, a]); vals.extend([y, y, -y, -y])
        diag_rows, diag_vals = [], []
        for sh in self.sheets.values():
            R, XL = self.edge_z(sh, f)
            st_(sh.edges[0], sh.edges[1], 1.0 / (R + 1j * XL))
            c_cell, tand_cell = self.sheet_c_tand(sh, f) if self.eps_table else (sh.c_cell, sh.tand_cell)
            y = (w * c_cell * tand_cell + 1j * w * c_cell) * sh.cell_area
            if pat is None:
                diag_rows.append(self.map(sh.cells))
            diag_vals.append(y)
        for sh in self.gsheets.values():
            R, _ = self.edge_z(sh, f, rail=False)
            st_(sh.edges[0], sh.edges[1], 1.0 / R)
        a, b, sq, sig, t, Lt = self.traces
        if len(a):
            zst = (SOL.trace_zs(self, self._zs_fn, f, sig, t) if fast else
                   np.array([complex(self._zs_fn(f, s_, t_)) for s_, t_ in zip(sig, t)]))
            st_(a, b, 1.0 / (zst * sq + 1j * w * Lt))
        a, b, _, Lv = self.vias
        Rv = self.via_R(f)
        st_(a, b, 1.0 / (Rv + 1j * w * Lv))
        a, b, Rg = self.gnd_r
        if len(a):
            st_(a, b, (1.0 / Rg).astype(complex))
        ys = {mid: 1.0 / self.ex["models"][mid].impedance([f])[0] for mid in {d[2] for d in self.dec}}
        st_(np.array([d[0] for d in self.dec]), np.array([d[1] for d in self.dec]), np.array([ys[d[2]] for d in self.dec]))
        if fast:  # EXP-37: values only; the COO -> CSC map is frequency-independent
            V_ = np.concatenate([np.ravel(x) for x in vals] + [np.ravel(x) for x in diag_vals])
            if pat is None:
                pat = self._fa_pat = SOL.YPattern(
                    np.concatenate([np.ravel(x) for x in rows] + [np.ravel(x) for x in diag_rows]),
                    np.concatenate([np.ravel(x) for x in cols] + [np.ravel(x) for x in diag_rows]), N)
            return pat.csc(V_)
        R_ = np.concatenate([np.ravel(x) for x in rows] + [np.ravel(x) for x in diag_rows])
        C_ = np.concatenate([np.ravel(x) for x in cols] + [np.ravel(x) for x in diag_rows])
        V_ = np.concatenate([np.ravel(x) for x in vals] + [np.ravel(x) for x in diag_vals])
        ok = (R_ >= 0) & (C_ >= 0) & (R_ < N) & (C_ < N)
        return sparse.coo_matrix((V_[ok], (R_[ok], C_[ok])), shape=(N, N)).tocsc()

    def solve(self, freqs, verbose=True, want_v=False):
        """The sweep, as a `Result` (W4).  A `Result` unpacks as the research `(Z, stats)` /
        `(Z, stats, Vs)` tuple, so every existing caller keeps working."""
        t_solve = time.time()
        Z, stats, Vs = [], [], []
        gpu = None  # backend.solver in (cudss, auto): a CudssLU once it is up, False once known unusable
        for f in freqs:
            t0 = time.time(); Y = self.assemble(f); t1 = time.time()
            if gpu is None and self.backend.solver in ("cudss", "auto"):
                gpu = self._cudss                    # one solver per model, reused by every
                if gpu is None:                      # solve() call: cuDSS 0.8 faults on a
                    try:                             # second solver and on an early free()
                        gpu = SOL.CudssLU(Y, self.P, self.log, backend=self.backend)
                        self.log(f"[solver] cudss on {gpu.device}")
                    except Exception as e:  # unattended 92-port drivers must not die on the GPU
                        gpu = False
                        self.log(f"[solver] cudss unavailable ({type(e).__name__}: {e}) -- using splu")
                    self._cudss = gpu
            if gpu:
                V, st = gpu.solve(Y); t2 = time.time()
                Z.append(V[self.P])
                stats.append(dict(f=float(f), assemble_s=t1 - t0, rss_MB=peak_rss_mb(), **st))
            else:
                lu = splu(Y, permc_spec="COLAMD"); t2 = time.time()
                rhs = np.zeros(self.N, complex); rhs[self.P] = 1.0
                V = lu.solve(rhs); Z.append(V[self.P])
                stats.append(dict(f=float(f), assemble_s=t1 - t0, factor_s=t2 - t1, nnz_LU=int(lu.nnz),
                                  rss_MB=peak_rss_mb(), solver="splu"))
                del lu
            if want_v:
                Vs.append(V)
            if verbose:
                self.log(f"  f={f:11.4e} Z={Z[-1].real:+.4e}{Z[-1].imag:+.4e}j asm {t1-t0:.1f}s fact {t2-t1:.1f}s nnzLU {stats[-1]['nnz_LU']/1e6:.1f}M")
        return Result(self, np.asarray(freqs, float), np.array(Z), stats, Vs if want_v else None,
                      time.time() - t_solve)

    def breakdown(self, f, V):
        w = 2 * math.pi * f
        Vx = np.append(V, 0.0)
        gv = lambda idx: Vx[np.where(self.map(idx) < 0, self.N, self.map(idx))]  # noqa: E731
        out = collections.OrderedDict()
        for L, sh in self.sheets.items():
            R, XL = self.edge_z(sh, f)
            i = (gv(sh.edges[0]) - gv(sh.edges[1])) / (R + 1j * XL)
            i2 = np.abs(i) ** 2
            out[f"plane_R:{L}"] = np.sum(i2 * R.real); out[f"plane_L:{L}"] = 1j * np.sum(i2 * (XL + R.imag))
        for L, sh in self.gsheets.items():
            R, _ = self.edge_z(sh, f, rail=False)
            i = (gv(sh.edges[0]) - gv(sh.edges[1])) / R
            out[f"gnd_sheet_R:{L}"] = np.sum(np.abs(i) ** 2 * R)
        a, b, sq, sig, t, Lt = self.traces
        z = np.array([complex(self._zs_fn(f, s_, t_)) for s_, t_ in zip(sig, t)]) * sq + 1j * w * Lt
        i = (gv(a) - gv(b)) / z; out["rail_traces"] = np.sum(np.abs(i) ** 2 * z)
        a, b, _, Lv = self.vias
        z = self.via_R(f) + 1j * w * Lv; i = (gv(a) - gv(b)) / z
        pl = Lv < 1e-14
        out["rail_vias"] = np.sum(np.abs(i[~pl]) ** 2 * z[~pl]); out["rail_pad_links"] = np.sum(np.abs(i[pl]) ** 2 * z[pl])
        a, b, Rg = self.gnd_r
        if len(a):
            i = (gv(a) - gv(b)) / Rg
            isref = b < 0
            out["gnd_network_R(vias/traces/pads)"] = np.sum(np.abs(i[~isref]) ** 2 * Rg[~isref])
            out["gnd_port_pin_entry_R"] = np.sum(np.abs(i[isref]) ** 2 * Rg[isref])
        ys = {mid: 1.0 / self.ex["models"][mid].impedance([f])[0] for mid in {d[2] for d in self.dec}}
        s = 0
        for ra, ga, mid in self.dec:
            dv = gv(np.array([ra]))[0] - gv(np.array([ga]))[0]; s += abs(dv) ** 2 * np.conj(ys[mid])
        out["decaps"] = s
        res = {k: dict(mOhm=float(np.real(v)) * 1e3, pH=float(np.imag(v)) / w * 1e12) for k, v in out.items()}
        res["_Zport"] = [float(V[self.P].real), float(V[self.P].imag)]
        return res

    def split_breakdown(self, f, V):
        """`run4.split_breakdown`: plane L split into external (geometry) and internal (Im Zs) parts."""
        w = 2 * math.pi * f
        Vx = np.append(V, 0.0)
        gv = lambda idx: Vx[np.where(self.map(idx) < 0, self.N, self.map(idx))]  # noqa: E731
        out = {}
        for L, sh in self.sheets.items():
            R, XL = self.edge_z(sh, f)
            i2 = np.abs((gv(sh.edges[0]) - gv(sh.edges[1])) / (R + 1j * XL)) ** 2
            out[L] = dict(R_mOhm=float(np.sum(i2 * R.real) * 1e3), L_ext_pH=float(np.sum(i2 * XL) / w * 1e12),
                          L_int_pH=float(np.sum(i2 * R.imag) / w * 1e12))
        for L, sh in self.gsheets.items():
            R, _ = self.edge_z(sh, f, rail=False)
            i2 = np.abs((gv(sh.edges[0]) - gv(sh.edges[1])) / R) ** 2
            out["GND:" + L] = dict(R_mOhm=float(np.sum(i2 * R.real) * 1e3), L_int_pH=float(np.sum(i2 * R.imag) / w * 1e12))
        base = self.breakdown(f, V)
        for k in ("rail_traces", "rail_vias", "gnd_network_R(vias/traces/pads)"):
            if k in base:
                out[k] = base[k]
        return out
