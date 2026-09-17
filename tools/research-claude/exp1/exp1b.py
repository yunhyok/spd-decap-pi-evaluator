#!/usr/bin/env python
"""EXP-1b: variant B (ideal GND return) with a per-cell TWO-SIDED plane reference.

For every rail plane cell, search up to --max-layers conductor layers above and
below; the first layer whose DGND artwork (actual shapes, ordered booleans,
rasterised on the same cell grid) covers the cell is that side's reference.
  d_side  = sum of stackup row thicknesses strictly between the two coppers
  L_edge  = mu0 * d_eff per square,  d_eff = d_up*d_dn/(d_up+d_dn)  (one side: d_side)
  C_cell  = sum over sides of eps0 / sum(t_k/eps_r,k) * A, only when the GND is the
            immediately adjacent conductor (an intervening other-net plane takes
            that capacitance instead; not modelled, reported).
Cells with no GND within reach on either side fall back to variant B's d.
Everything else is identical to variant B.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import mmap
import os
import pickle
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from fast_assemble import ON as FAST_ON  # noqa: E402
from paths import peak_rss_mb, ref_npz, spd_path, work_dir  # noqa: E402
import model as M  # noqa: E402
from run_exp1 import VARIANTS, gates, pick_freqs, plot, resonance  # noqa: E402
from spd_decap_pi._core.io import spd as P  # noqa: E402

EPS0 = M.EPS0


def diel_rows(between):
    return [r for r in between if r["conductivity"] is None]


def load_layer_shapes(spd_path, layers):
    """All-net ordered shape primitives for the given conductor layers (section-limited
    call of spd._parse_shapes, src/spd_decap_pi/_core/io/spd.py:2962)."""
    out = {}
    rep = P._Reporter(None, None)
    with open(spd_path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as d:
        for L in layers:
            hdr = f".Shape {L}pkgshape".encode()
            s0 = P._find_line(d, hdr)
            if s0 < 0:
                out[L] = {}
                continue
            s1 = P._find_line(d, b".EndShape", s0)
            diags = []
            _o, _ln, _nets, geoms = P._parse_shapes(d, s0, s1, set(), None, rep, diags)
            out[L] = {
                g.net: dict(layer=g.layer, pos_polys=[np.asarray(list(p), float) for p in g.positive_polygons_um],
                            neg_polys=[np.asarray(list(p), float) for p in g.negative_polygons_um],
                            pos_circles=list(g.positive_circles_um), neg_circles=list(g.negative_circles_um),
                            order=list(g.primitive_order))
                for g in geoms
            }
            print(f"[1b] shapes {L}: nets {len(out[L])}", flush=True)
    return out


class TwoSided:
    # EXP-11 flags, class-level so subclasses with a frozen __init__ signature (run8.TwoSidedAny)
    # inherit the default and callers can set them on the instance after construction.
    eps_table = False   # per-frequency eps_r/tand from the material table instead of the fixed dk / 1 MHz tand
    c_all_refs = False  # also give C to cells whose reference is a non-adjacent candidate layer (k > 0)
    c_unit_fix = False  # EXP-13: multiply cell C by 1e6 (missing gap um->m in the frozen path); default False = frozen (bugged) behaviour

    def __init__(self, ex, shapes, max_layers=3, gnd_net="DGND", eps_table=False, c_all_refs=False, c_unit_fix=False):
        self.ex = ex
        self.st = M.Stack(ex["stackup"])
        self.rows = ex["stackup"]
        self.names = [r["name"] for r in self.rows]
        self.shapes = shapes
        self.max_layers = max_layers
        self.gnd = gnd_net
        self.report = {}
        self.cache = {}
        self.eps_table = eps_table
        self.c_all_refs = c_all_refs
        self.c_unit_fix = c_unit_fix

    def candidates(self, L):
        i = self.names.index(L)
        res = {}
        for side, step in (("up", -1), ("down", 1)):
            lst = []
            j = i + step; between = []
            while 0 <= j < len(self.rows) and len(lst) < self.max_layers:
                r = self.rows[j]
                if r["conductivity"] is not None:
                    lst.append((r["name"], list(between)))
                between.append(r)
                j += step
            res[side] = lst
        return res

    def mask(self, layer, net, blk):
        key = (layer, net, blk["x0"], blk["y0"], blk["nx"], blk["ny"], blk["h"])
        if key not in self.cache:
            g = self.shapes.get(layer, {}).get(net)
            if g is None or not g["order"]:
                m = np.zeros((blk["ny"], blk["nx"]), bool)
            else:
                m = M.fast_layer_mask(self, (layer, net, blk["h"]), g, blk) if FAST_ON else None  # EXP-39
                if m is None:
                    m = M.rasterize(g, blk["h"], window=(blk["x0"], blk["y0"], blk["x0"] + blk["nx"] * blk["h"], blk["y0"] + blk["ny"] * blk["h"]))["mask"]
            self.cache[key] = m
        return self.cache[key]

    def __call__(self, L, blk):
        shape = (blk["ny"], blk["nx"])
        rail = blk["mask"]
        cand = self.candidates(L)
        d_side = {}; c_side = {}; td_side = {}; who = {}
        cents = {}  # eps_table: [(cells, dielectric rows)] per side, one entry per C assignment
        rep = self.report.setdefault(L, {"blocks": []})
        brep = {"h": blk["h"], "cells": int(rail.sum()), "sides": {}}
        for side in ("up", "down"):
            d = np.full(shape, np.nan); c = np.zeros(shape); td = np.zeros(shape); assigned = np.zeros(shape, bool)
            who_side = np.full(shape, -1, np.int64)  # stackup row index of the assigned conductor, -1 = fallback
            layer_rep = []; ents = []
            for k, (cl, between) in enumerate(cand[side]):
                gm = self.mask(cl, self.gnd, blk) & rail & ~assigned
                cov_total = float((self.mask(cl, self.gnd, blk) & rail).sum() / max(rail.sum(), 1))
                # other nets on that layer over the rail cells (report only)
                others = []
                for net in self.shapes.get(cl, {}):
                    if net == self.gnd:
                        continue
                    om = self.mask(cl, net, blk) & rail
                    fr = float(om.sum() / max(rail.sum(), 1))
                    if fr > 0.01:
                        others.append((net, round(fr, 3)))
                others.sort(key=lambda t: -t[1])
                gap = sum(r["thickness_um"] for r in between)
                layer_rep.append(dict(layer=cl, gap_um=gap, gnd_cover_of_rail=round(cov_total, 3),
                                      newly_assigned=round(float(gm.sum() / max(rail.sum(), 1)), 3), other_nets_cover=others[:4]))
                d[gm] = gap
                who_side[gm] = self.names.index(cl)
                diel = diel_rows(between)
                if (k == 0 or self.c_all_refs) and diel:  # adjacent conductor (k == 0): dielectric-only gap -> capacitance
                    inv = sum(r["thickness_um"] / float(r["dk"] or M.eps_tand(r, 1e6)[0]) for r in diel)
                    er_dummy, tdv = M.eps_tand(diel[0], 1e6)
                    if self.c_unit_fix:
                        c[gm] = EPS0 * 1e-12 / inv * 1e6
                    else:
                        c[gm] = EPS0 * 1e-12 / inv
                    td[gm] = tdv
                    if self.eps_table:
                        ents.append((gm, diel))  # gm is never mutated after this point
                assigned |= gm
            d_side[side], c_side[side], td_side[side] = d, c, td
            who[side] = who_side
            cents[side] = ents
            brep["sides"][side] = layer_rep
        du, dd = d_side["up"], d_side["down"]
        both = ~np.isnan(du) & ~np.isnan(dd)
        d_eff = np.where(both, du * dd / np.where(both, du + dd, 1), np.where(np.isnan(du), dd, du))
        none = np.isnan(d_eff)
        d_eff = np.where(none, self.fallback(L), d_eff)
        c_um2 = c_side["up"] + c_side["down"]
        tand = np.where(c_um2 > 0, (c_side["up"] * td_side["up"] + c_side["down"] * td_side["down"]) / np.where(c_um2 > 0, c_um2, 1), 0)
        rc = rail
        brep.update(two_sided=round(float((both & rc).sum() / max(rc.sum(), 1)), 3),
                    single_up=round(float((~np.isnan(du) & np.isnan(dd) & rc).sum() / max(rc.sum(), 1)), 3),
                    single_down=round(float((np.isnan(du) & ~np.isnan(dd) & rc).sum() / max(rc.sum(), 1)), 3),
                    fallback_none=round(float((none & rc).sum() / max(rc.sum(), 1)), 3),
                    d_up_um_median=float(np.nanmedian(du[rc])) if np.any(~np.isnan(du[rc])) else None,
                    d_down_um_median=float(np.nanmedian(dd[rc])) if np.any(~np.isnan(dd[rc])) else None,
                    d_eff_um_median=float(np.median(d_eff[rc])), d_eff_um_mean=float(np.mean(d_eff[rc])),
                    d_B_um=self.fallback(L))
        rep["blocks"].append(brep)
        # per-cell reference info (EXP-12): which conductor row is the wall on each side, and two-sidedness
        out = dict(d_eff=d_eff, c_um2=c_um2, tand=tand, two_sided=both, wall_up=who["up"], wall_dn=who["down"])
        if self.eps_table:
            out["c_tand_at"] = self._c_tand_fn(shape, cents, self.c_unit_fix)
        return out

    @staticmethod
    def _c_tand_fn(shape, cents, c_unit_fix=False):
        """eps_table: f -> (c_um2, tand) with eps_r and tand read from the material table at f."""
        def at(f):
            cs = {}; tds = {}
            for side in ("up", "down"):
                c = np.zeros(shape); td = np.zeros(shape)
                for gm, diel in cents.get(side, []):
                    inv = sum(r["thickness_um"] / M.eps_tand(r, f)[0] for r in diel)
                    if c_unit_fix:
                        c[gm] = EPS0 * 1e-12 / inv * 1e6
                    else:
                        c[gm] = EPS0 * 1e-12 / inv
                    td[gm] = M.eps_tand(diel[0], f)[1]
                cs[side], tds[side] = c, td
            cu = cs["up"] + cs["down"]
            return cu, np.where(cu > 0, (cs["up"] * tds["up"] + cs["down"] * tds["down"]) / np.where(cu > 0, cu, 1), 0)
        return at

    def fallback(self, L):
        # variant-B rule (model.build_model): adjacent DGND-named layers, else nearest by name
        nb = self.st.neighbours(L)
        adj = [gap for side, c, diel, gap in nb if self.st.is_gnd(c)]
        if adj:
            return 1.0 / sum(1.0 / x for x in adj)
        return min(g[1] for g in self.st.nearest_gnd(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spd", default=str(spd_path("260729")))
    ap.add_argument("--ref", default=str(ref_npz("260729")))
    ap.add_argument("--port-index", type=int, default=18)
    ap.add_argument("--h", type=float, default=200.0)
    ap.add_argument("--fine-h", type=float, default=50.0)
    ap.add_argument("--top-h", type=float, default=50.0)
    ap.add_argument("--max-layers", type=int, default=3)
    ap.add_argument("--h-check", action="store_true", help="also solve h/2 at 1 MHz")
    ap.add_argument("--out", default=str(work_dir("exp1")))
    ap.add_argument("--tag", default="", help="suffix for result/plot file names")
    a = ap.parse_args()
    T0 = time.time()
    ex = pickle.load(open(os.path.join(a.out, "extract_port18.pkl"), "rb"))
    res1 = json.load(open(os.path.join(a.out, "result.json")))
    fine_box = res1["discretisation"]["fine_box_um"]
    names = [r["name"] for r in ex["stackup"]]
    need = set()
    ts0 = TwoSided(ex, {}, a.max_layers)
    for g in ex["rail_geoms"]:
        for side, lst in ts0.candidates(g["layer"]).items():
            need.update(cl for cl, _ in lst)
    shapes_cache = os.path.join(a.out, "neighbour_shapes.pkl")
    if os.path.exists(shapes_cache):
        shapes = pickle.load(open(shapes_cache, "rb"))
    else:
        shapes = load_layer_shapes(a.spd, sorted(need, key=names.index))
        pickle.dump(shapes, open(shapes_cache, "wb"))
    ts = TwoSided(ex, shapes, a.max_layers)
    opt = VARIANTS["B"]
    mdl = M.build_model(ex, a.h, top_h_um=a.top_h, fine_box=fine_box, fine_h_um=a.fine_h, include_gnd_sheet_r=False,
                        gnd_via_r_factor=0.0, verbose=True, cell_ref=ts)
    ref = np.load(a.ref, allow_pickle=True)
    fref = ref["freq"]; zref = ref["Zdiag"][:, a.port_index - 1]
    idx = pick_freqs(fref, 25)
    ridx = [i for i in range(len(fref)) if 0.5e6 <= fref[i] <= 5.0e6]
    all_idx = sorted(set(idx) | set(ridx))
    freqs = fref[all_idx]
    t = time.time()
    Z, stats = M.solve(mdl, ex, freqs, verbose=True)
    tsolve = time.time() - t
    sel = np.array([all_idx.index(i) for i in idx])
    fr_m, zmin_m = resonance(freqs[[all_idx.index(i) for i in ridx]], Z[[all_idx.index(i) for i in ridx]])
    fr_r, zmin_r = resonance(fref[ridx], zref[ridx])
    g = gates(freqs[sel], Z[sel], zref[idx], fr_m, zmin_m, fr_r, zmin_r)
    bd = {}
    for fb in (1e4, 1e6):
        _, _, Vs = M.solve(mdl, ex, [fb], verbose=False, want_voltages=True)
        bd[f"{fb:.0e}"] = M.loss_breakdown(mdl, ex, fb, Vs[0])
    out = dict(label="1b: variant B + per-cell two-sided DGND reference (d_eff = d_up*d_dn/(d_up+d_dn), C = sum of adjacent sides)",
               h_um=a.h, fine_h_um=a.fine_h, top_h_um=a.top_h, fine_box_um=fine_box, max_layers=a.max_layers,
               unknowns=mdl.n, model_info={k: v for k, v in mdl.info.items()}, reference_search=ts.report,
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zref[all_idx].real], Zref_im=[float(x) for x in zref[all_idx].imag],
               rel_err=[float(x) for x in np.abs(Z - zref[all_idx]) / np.abs(zref[all_idx])],
               dRe_mOhm=[float(x) for x in (Z.real - zref[all_idx].real) * 1e3],
               dL_pH=[float(x) for x in (Z.imag - zref[all_idx].imag) / (2 * np.pi * freqs) * 1e12],
               gates=g, solve_seconds_total=tsolve, per_freq_stats=stats, breakdown=bd)
    print({k: (gg.get("PASS"), gg.get("max_abs_dRe_mOhm", gg.get("max_abs_pH", gg.get("rel_err", gg.get("max_rel_err"))))) for k, gg in g.items() if k != "G5"}, flush=True)
    if a.h_check:
        del mdl
        m2 = M.build_model(ex, a.h / 2, top_h_um=a.top_h / 2, fine_box=fine_box, fine_h_um=a.fine_h / 2, include_gnd_sheet_r=False,
                           gnd_via_r_factor=0.0, verbose=False, cell_ref=TwoSided(ex, shapes, a.max_layers))
        Z2, st2 = M.solve(m2, ex, [1e6], verbose=True)
        i1 = list(freqs).index(1e6)
        out["convergence_1MHz"] = dict(unknowns_h_half=m2.n, Z_h=[Z[i1].real, Z[i1].imag], Z_h_half=[Z2[0].real, Z2[0].imag],
                                       rel_change=float(abs(Z[i1] - Z2[0]) / abs(Z2[0])),
                                       rel_err_h_half=float(abs(Z2[0] - zref[all_idx][i1]) / abs(zref[all_idx][i1])), stats=st2)
        print("conv", out["convergence_1MHz"]["rel_change"], out["convergence_1MHz"]["rel_err_h_half"])
    out["peak_rss_MB"] = peak_rss_mb()
    out["wall_seconds"] = time.time() - T0
    json.dump(out, open(os.path.join(a.out, f"result_exp1b{a.tag}.json"), "w"), indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    plots = {
        "B (single ref, EXP-1)": dict(freq=res1["variants"]["B"]["freq"], Z_re=res1["variants"]["B"]["Z_re"], Z_im=res1["variants"]["B"]["Z_im"]),
        "1b (two-sided ref)": dict(freq=out["freq"], Z_re=out["Z_re"], Z_im=out["Z_im"]),
    }
    plot(os.path.join(a.out, f"exp1b_Z18{a.tag}.png"), plots, fref, zref)
    print("done", out["wall_seconds"], out["peak_rss_MB"])


if __name__ == "__main__":
    main()
