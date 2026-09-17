#!/usr/bin/env python
"""EXP-4: audit of conductor internal inductance in the sheet surface impedance.

A1 (code facts): mfdm.copper_surface_impedance (src/spd_decap_pi/_core/solver/mfdm.py:782)
returns the ONE-face finite-thickness impedance Zc*coth(gamma*t), Zc = (1+j)/(sigma*delta),
gamma = (1+j)/delta  ->  low-f limit 1/(sigma t) + j w mu0 t/3.  EXP-3 model3.py applies it
complex to rail plane edges (edge_z, :452), to explicit GND sheet edges (edge_z rail=False,
complex R), and to off-plane rail traces (:486, :533); vias are R only.

Modes (all on the EXP-3 S2 base unless stated):
  orig : as S2 (one-sided complex Zs everywhere)
  a    : internal inductance removed: rail planes and rail traces use Re(Zs_one-sided)
  b    : symmetric two-sided sheet impedance Zs2 = 1/2 Zc coth(gamma t/2) on rail planes whose
         return is two-sided (L14, L25: >=98 % of coarse cells two-sided in EXP-1b); one-sided
         form kept on TOP, L20, L21 and traces
  c    : S3 (explicit GND) + (b) on rail planes; GND sheets R only (Re Zs2) and scaled by
         6/N_gnd (N_gnd = 13 DGND layers TOP..L28 in the stack) as a bounded approximation of
         all DGND layers in parallel
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exp3"))
sys.path.insert(0, os.path.join(HERE, "..", "exp1"))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import peak_rss_mb, ref_npz, work_dir, work_file  # noqa: E402
import model3 as M3  # noqa: E402
from run3 import ladder_gates  # noqa: E402
from run_exp1 import resonance, plot  # noqa: E402
from spd_decap_pi._core.solver.mfdm import copper_surface_impedance as zs_one  # noqa: E402

OUT = work_dir("exp4")
MU0 = M3.MU0
TWO_SIDED = {"Signal$L14(MAIN_POWER4)", "Signal$L25(MAIN_POWER4)"}
N_GND = 13  # L02 L04 L06 L08 L10 L13 L16 L18 L20 L21 L24 L27 L28


def zs_two(f, sigma, t):
    """Symmetric two-sided sheet impedance 1/2 Zc coth(gamma t/2); low f: 1/(sigma t) + j w mu0 t/12."""
    w = 2 * math.pi * f
    k = complex(math.sqrt(w * MU0 * sigma / 2), math.sqrt(w * MU0 * sigma / 2))  # (1+j)/delta
    x = k * t / 2
    if abs(x) < 1e-3:
        coth = 1 / x + x / 3 - x ** 3 / 45
    else:
        coth = 1 / np.tanh(x)
    zc = k / sigma  # (1+j)/(sigma delta)
    return complex(0.5 * zc * coth)


class Model4(M3.Model3):
    mode = "orig"
    gnd_scale = 1.0

    def zs_plane(self, f, sh, rail):
        one = complex(zs_one(f, sh.sigma, sh.t))
        if self.mode == "orig":
            return one
        if self.mode == "a":
            return complex(one.real, 0.0) if rail else one
        # b / c
        if rail:
            return zs_two(f, sh.sigma, sh.t) if sh.layer in TWO_SIDED else one
        return complex(zs_two(f, sh.sigma, sh.t).real, 0.0) if self.mode == "c" else one

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
            return R * (self.gnd_scale if self.mode == "c" else 1.0), np.zeros(len(a))
        w = 2 * math.pi * f
        weff = M3.FA.weff(self, sh) if M3.FA.ON else self.weff_of(sh)[0]  # EXP-37: weff has no f
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


def patch_traces(mode):
    """rail traces use the module-level copper_surface_impedance inside model3."""
    if mode == "a":
        M3.copper_surface_impedance = lambda f, s, t: complex(complex(zs_one(f, s, t)).real, 0.0)
    else:
        M3.copper_surface_impedance = zs_one


def split_breakdown(mdl, f, V):
    """plane L split into external (geometry) and internal (Im Zs) parts, per layer."""
    w = 2 * math.pi * f
    Vx = np.append(V, 0.0)
    gv = lambda idx: Vx[np.where(mdl.map(idx) < 0, mdl.N, mdl.map(idx))]  # noqa: E731
    out = {}
    for L, sh in mdl.sheets.items():
        R, XL = mdl.edge_z(sh, f)
        i2 = np.abs((gv(sh.edges[0]) - gv(sh.edges[1])) / (R + 1j * XL)) ** 2
        out[L] = dict(R_mOhm=float(np.sum(i2 * R.real) * 1e3), L_ext_pH=float(np.sum(i2 * XL) / w * 1e12),
                      L_int_pH=float(np.sum(i2 * R.imag) / w * 1e12))
    for L, sh in mdl.gsheets.items():
        R, _ = mdl.edge_z(sh, f, rail=False)
        i2 = np.abs((gv(sh.edges[0]) - gv(sh.edges[1])) / R) ** 2
        out["GND:" + L] = dict(R_mOhm=float(np.sum(i2 * R.real) * 1e3), L_int_pH=float(np.sum(i2 * R.imag) / w * 1e12))
    base = mdl.breakdown(f, V)
    for k in ("rail_traces", "rail_vias", "gnd_network_R(vias/traces/pads)"):
        if k in base:
            out[k] = base[k]
    return out


def run_mode(mdl, mode, freqs, zr, fref, zref, tag):
    mdl.mode = mode
    patch_traces(mode)
    t = time.time()
    Z, st = mdl.solve(freqs)
    rsel = [k for k, f in enumerate(freqs) if 0.5e6 <= f <= 5e6]
    fr_m, _ = resonance(freqs[rsel], Z[rsel])
    g = ladder_gates(freqs, Z, zr, fr_m, 1.585e6)
    bd = {}
    for fb in (1e5, 1e6):
        _, _, Vs = mdl.solve([fb], verbose=False, want_v=True)
        bd[f"{fb:.0e}"] = split_breakdown(mdl, fb, Vs[0])
    res = dict(mode=mode, freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               dL_pH=[float(x) for x in (Z.imag - zr.imag) / (2 * np.pi * freqs) * 1e12],
               dRe_mOhm=[float(x) for x in (Z.real - zr.real) * 1e3], rel_err=[float(x) for x in np.abs(Z - zr) / np.abs(zr)],
               gates=g, breakdown=bd, solve_seconds=time.time() - t, info={k: v for k, v in mdl.info.items() if k != "reference_search"},
               peak_rss_MB=peak_rss_mb(), stats=st)
    json.dump(res, open(os.path.join(OUT, f"result_{tag}.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("GATES", tag, json.dumps(g, default=str), flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", default="ab", choices=["ab", "c", "s4b", "s4c"])
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    ex = pickle.load(open(work_file("exp1", "extract_port18.pkl"), "rb"))
    shapes = pickle.load(open(work_file("exp1", "neighbour_shapes.pkl"), "rb"))
    fine_box = json.load(open(work_file("exp1", "result.json")))["discretisation"]["fine_box_um"]
    ref = np.load(ref_npz("260729"), allow_pickle=True)
    fref = ref["freq"]; zref = ref["Zdiag"][:, 17]
    lad = [3.0e4, 1.0e5, 3.0e5, 1.0e6, 2.5e6, 1.0e7, 1.0e8]
    if a.which == "ab":
        idx = sorted({int(np.argmin(abs(fref - x))) for x in lad} | {i for i in range(len(fref)) if 0.9e6 <= fref[i] <= 2.6e6})
        freqs = fref[idx]; zr = zref[idx]
        mdl = Model4(ex, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10, fringe=True)
        for mode in ("orig", "a", "b"):
            run_mode(mdl, mode, freqs, zr, fref, zref, f"S2_{mode}")
    elif a.which == "c":
        idx = sorted({int(np.argmin(abs(fref - x))) for x in lad + [1.2e6, 1.445e6, 1.585e6, 1.738e6, 2.0e6]})
        freqs = fref[idx]; zr = zref[idx]
        gnd = pickle.load(open(work_file("exp3", "extract_gnd3.pkl"), "rb"))
        mdl = Model4(ex, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10, fringe=True,
                     gnd=gnd, gnd_h=400.0, gnd_fh=100.0)
        mdl.gnd_scale = 6.0 / N_GND
        run_mode(mdl, "c", freqs, zr, fref, zref, "S3_c")
    else:
        mode = "b" if a.which == "s4b" else "c"
        freqs = np.array([1e6]); zr = np.array([zref[int(np.argmin(abs(fref - 1e6)))]])
        mdl = Model4(ex, shapes, h=200.0, fh=25.0, top_h=25.0, fine_box=fine_box, sub_c=20, sub_f=5, sub_top=5, fringe=True)
        mdl.mode = mode; patch_traces(mode)
        Z, st = mdl.solve(freqs)
        json.dump(dict(mode=mode, fine_h=25, top_h=25, Z=[Z[0].real, Z[0].imag], rel_err=float(abs(Z[0] - zr[0]) / abs(zr[0])), stats=st,
                       unknowns=mdl.N), open(os.path.join(OUT, f"result_S4_{mode}.json"), "w"), indent=1)
        print("S4", Z[0], abs(Z[0] - zr[0]) / abs(zr[0]))


if __name__ == "__main__":
    main()
