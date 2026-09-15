#!/usr/bin/env python
"""EXP-2: multiconductor stacked-plane (M-FDM-consistent) model of port 18.

Explicit column conductors on one common 2-D grid (coarse h + fine box):
  L13 DGND, L14 rail, L15 other-net (ADC_VDD_055_VTRIP/0), L16 DGND,
  L24 DGND, L25 rail, L26 other-net, L27 DGND.
Per grid edge ("column"), the present layers carry branch currents I_k with
  Z_col = diag(Zs_k(f)) * sq + j w mu0 * G * sq,   G_kl = s_B - max(s_k, s_l)
(s = cumulative dielectric gap coordinate from the top explicit layer, s_B that of
the bottom explicit layer; loop L of any adjacent pair = mu0 * gap; G is PSD).
Y_col = inv(Z_col) is stamped on the 2m end nodes (M-FDM multiconductor unit cell,
cf. src/spd_decap_pi/_core/solver/mfdm.py:1067-1158).  Capacitance only between
vertically adjacent explicit layers with no conductor in between.
Rail TOP pads / L20 / L21 islands: series R + j w mu0 d_eff (EXP-1b per-cell d_eff).
Rail traces / vias / pad links / decaps: as EXP-1 (model.py).
GND: DGND nodes snap to explicit GND planes; other DGND nodes are explicit,
connected by DGND vias (R, via_model.estimate_via_segment_rl) and DGND traces
(R); inductance of vertical/trace loops stays on the rail side (as EXP-1).
Port: + = 978 rail DUT bumps shorted; - = 10,919 DGND DUT bumps = reference.
Decaps connect rail pin node <-> their own DGND pin node.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import pickle
import resource
import sys
import time

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph
from scipy.sparse.linalg import splu
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exp1"))
import model as M  # noqa: E402
from exp1b import TwoSided  # noqa: E402
from run_exp1 import gates, pick_freqs, plot, resonance  # noqa: E402
from spd_decap_pi._core.solver.mfdm import copper_surface_impedance  # noqa: E402
from spd_decap_pi._core.via_model import estimate_via_segment_rl  # noqa: E402

MU0 = M.MU0
EPS0 = M.EPS0
RAIL_NET = "ADC_VDD_075_VTRIP_SRAM/0"
OTHER = "ADC_VDD_055_VTRIP/0"
EXPLICIT = [("Signal$L13(DGND)", "G"), ("Signal$L14(MAIN_POWER4)", "R"), ("Signal$L15(MAIN_POWER5)", "O"),
            ("Signal$L16(DGND)", "G"), ("Signal$L24(DGND)", "G"), ("Signal$L25(MAIN_POWER4)", "R"),
            ("Signal$L26(MAIN_POWER5)", "O"), ("Signal$L27(DGND)", "G")]
SERIES_RAIL = ["Signal$TOP", "Signal$L20(DGND)", "Signal$L21(DGND)"]
# diagnosis set: every DGND plane layer from L02 to L28 explicit, L20/L21 carry rail islands and DGND
EXPLICIT_ALLGND = [("Signal$L02(DGND)", "G"), ("Signal$L04(DGND)", "G"), ("Signal$L06(DGND)", "G"), ("Signal$L08(DGND)", "G"),
                   ("Signal$L10(DGND)", "G"), ("Signal$L13(DGND)", "G"), ("Signal$L14(MAIN_POWER4)", "R"),
                   ("Signal$L15(MAIN_POWER5)", "O"), ("Signal$L16(DGND)", "G"), ("Signal$L18(DGND)", "G"),
                   ("Signal$L20(DGND)", "RG"), ("Signal$L21(DGND)", "RG"), ("Signal$L24(DGND)", "G"),
                   ("Signal$L25(MAIN_POWER4)", "R"), ("Signal$L26(MAIN_POWER5)", "O"), ("Signal$L27(DGND)", "G"),
                   ("Signal$L28(DGND)", "G")]


def blocks_for(geom, h, fh, win, fbox):
    X0 = math.floor(win[0] / h) * h; Y0 = math.floor(win[1] / h) * h
    X1 = math.ceil(win[2] / h) * h; Y1 = math.ceil(win[3] / h) * h
    c = M.rasterize(geom, h, window=(X0, Y0, X1, Y1)) if geom else dict(x0=X0, y0=Y0, nx=int(round((X1 - X0) / h)), ny=int(round((Y1 - Y0) / h)), h=h, mask=None)
    if c["mask"] is None:
        c["mask"] = np.zeros((c["ny"], c["nx"]), bool)
    fx0 = math.floor(fbox[0] / h) * h; fy0 = math.floor(fbox[1] / h) * h
    fx1 = math.ceil(fbox[2] / h) * h; fy1 = math.ceil(fbox[3] / h) * h
    f = M.rasterize(geom, fh, window=(fx0, fy0, fx1, fy1)) if geom else dict(x0=fx0, y0=fy0, nx=int(round((fx1 - fx0) / fh)), ny=int(round((fy1 - fy0) / fh)), h=fh, mask=None)
    if f["mask"] is None:
        f["mask"] = np.zeros((f["ny"], f["nx"]), bool)
    ci0 = int(round((fx0 - X0) / h)); ci1 = int(round((fx1 - X0) / h)); cj0 = int(round((fy0 - Y0) / h)); cj1 = int(round((fy1 - Y0) / h))
    c["mask"][cj0:cj1, ci0:ci1] = False
    return [c, f]


class Builder:
    def __init__(self, ex, gnd, shapes, h, fh, top_h, win, fbox, verbose=True, decouple_groups=False, gauge="bottom"):
        self.gauge = gauge
        self.decouple = decouple_groups
        self.ex, self.gnd, self.shapes = ex, gnd, shapes
        self.h, self.fh, self.top_h, self.win, self.fbox = h, fh, top_h, win, fbox
        self.st = M.Stack(ex["stackup"])
        self.rows = ex["stackup"]
        self.names = [r["name"] for r in self.rows]
        self.n = 0
        self.info = {}
        self.verbose = verbose

    def new(self, k):
        s = self.n; self.n += k
        return s

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def build(self):
        t0 = time.time()
        ex = self.ex
        rail_geoms = {g["layer"]: g for g in ex["rail_geoms"]}
        # ---------------- explicit column layers --------------------------
        K = len(EXPLICIT)
        self.blocks = []  # per layer list of blocks with ids
        for L, kind in EXPLICIT:
            parts = []
            if "R" in kind:
                parts.append((1, rail_geoms.get(L)))
            if "G" in kind:
                parts.append((2, self.shapes[L].get("DGND")))
            if "O" in kind:
                parts.append((3, self.shapes[L].get(OTHER)))
            bl = None
            for lab, g in parts:
                blp = blocks_for(g, self.h, self.fh, self.win, self.fbox)
                if bl is None:
                    bl = blp
                    for b in bl:
                        b["label"] = np.where(b["mask"], lab, 0)
                else:
                    for b, bp in zip(bl, blp):
                        new_ = bp["mask"] & (b["label"] == 0)
                        b["label"][new_] = lab
                        b["mask"] = b["label"] > 0
            for b in bl:
                ids = -np.ones(b["mask"].shape, np.int64)
                cnt = int(b["mask"].sum()); ids[b["mask"]] = np.arange(self.new(cnt), self.n) if cnt else []
                b["ids"] = ids
            self.blocks.append(bl)
            self.log(f"[exp2] {L} ({kind}): cells {sum(int(b['mask'].sum()) for b in bl)}")
        self.info["column_cells"] = {L: int(sum(int(b["mask"].sum()) for b in bl)) for (L, _), bl in zip(EXPLICIT, self.blocks)}
        # gap coordinate
        idx = [self.names.index(L) for L, _ in EXPLICIT]
        s = [0.0]
        self.pair_c = []  # (k, k+1, dielectric rows) when only dielectrics between
        for k in range(K - 1):
            between = self.rows[idx[k] + 1: idx[k + 1]]
            s.append(s[-1] + sum(r["thickness_um"] for r in between))
            if all(r["conductivity"] is None for r in between):
                self.pair_c.append((k, k + 1, between))
        self.s_um = np.array(s)
        if self.gauge == "bottom":   # z0 below the lowest explicit layer (coordinator spec)
            self.G = (self.s_um[-1] - np.maximum.outer(self.s_um, self.s_um)) * 1e-6  # m
        elif self.gauge == "top":    # z0 above the highest explicit layer
            self.G = np.minimum.outer(self.s_um, self.s_um) * 1e-6
        else:                        # mid: average of both
            self.G = 0.5 * ((self.s_um[-1] - np.maximum.outer(self.s_um, self.s_um)) + np.minimum.outer(self.s_um, self.s_um)) * 1e-6
        self.info["gauge"] = self.gauge
        self.groups = [list(range(K))]
        if self.decouple:
            # two magnetically independent stacks (L13-L16 | L24-L27): the omitted
            # DGND planes L18/L20/L21 between them are taken as a shield
            self.groups = [[0, 1, 2, 3], [4, 5, 6, 7]]
            G = np.zeros((K, K))
            for grp in self.groups:
                sb = self.s_um[grp[-1]]
                for i in grp:
                    for j in grp:
                        G[i, j] = (sb - max(self.s_um[i], self.s_um[j])) * 1e-6
            self.G = G
        self.info["magnetic_groups"] = [[EXPLICIT[k][0] for k in g_] for g_ in self.groups]
        self.info["gap_coordinate_um"] = dict(zip([L for L, _ in EXPLICIT], [float(x) for x in s]))
        self.sigma_t = [(self.st.row(L)["conductivity"], self.st.row(L)["thickness_um"] * 1e-6) for L, _ in EXPLICIT]
        # column edges
        A, B, SQ, LA, LB = [], [], [], [], []
        for bi in range(2):
            ids = np.stack([bl[bi]["ids"] for bl in self.blocks])  # K,ny,nx
            lab = np.stack([bl[bi]["label"] for bl in self.blocks])
            A.append(ids[:, :, :-1].reshape(K, -1)); B.append(ids[:, :, 1:].reshape(K, -1))
            LA.append(lab[:, :, :-1].reshape(K, -1)); LB.append(lab[:, :, 1:].reshape(K, -1))
            SQ.append(np.ones(A[-1].shape[1]))
            A.append(ids[:, :-1, :].reshape(K, -1)); B.append(ids[:, 1:, :].reshape(K, -1))
            LA.append(lab[:, :-1, :].reshape(K, -1)); LB.append(lab[:, 1:, :].reshape(K, -1))
            SQ.append(np.ones(A[-1].shape[1]))
        # interface fine boundary <-> coarse neighbour (all layers share geometry of the grid)
        c0, f0 = self.blocks[0][0], self.blocks[0][1]
        hf, hc = f0["h"], c0["h"]
        ia_f, ia_c = [], []
        for dj, di in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            if di == -1: jj, ii = np.meshgrid(np.arange(f0["ny"]), [0], indexing="ij")
            elif di == 1: jj, ii = np.meshgrid(np.arange(f0["ny"]), [f0["nx"] - 1], indexing="ij")
            elif dj == -1: jj, ii = np.meshgrid([0], np.arange(f0["nx"]), indexing="ij")
            else: jj, ii = np.meshgrid([f0["ny"] - 1], np.arange(f0["nx"]), indexing="ij")
            jj = jj.ravel(); ii = ii.ravel()
            x = f0["x0"] + (ii + 0.5) * hf + di * (hf / 2 + hc / 2); y = f0["y0"] + (jj + 0.5) * hf + dj * (hf / 2 + hc / 2)
            ci = np.floor((x - c0["x0"]) / hc).astype(int); cj = np.floor((y - c0["y0"]) / hc).astype(int)
            ok = (ci >= 0) & (ci < c0["nx"]) & (cj >= 0) & (cj < c0["ny"])
            ia_f.append((jj[ok], ii[ok])); ia_c.append((cj[ok], ci[ok]))
        fj = np.concatenate([a[0] for a in ia_f]); fi = np.concatenate([a[1] for a in ia_f])
        cj = np.concatenate([a[0] for a in ia_c]); ci = np.concatenate([a[1] for a in ia_c])
        A.append(np.stack([bl[1]["ids"][fj, fi] for bl in self.blocks])); B.append(np.stack([bl[0]["ids"][cj, ci] for bl in self.blocks]))
        LA.append(np.stack([bl[1]["label"][fj, fi] for bl in self.blocks])); LB.append(np.stack([bl[0]["label"][cj, ci] for bl in self.blocks]))
        SQ.append(np.full(len(fj), (hf / 2 + hc / 2) / hf))
        A = np.concatenate(A, axis=1); B = np.concatenate(B, axis=1); SQ = np.concatenate(SQ)
        LA = np.concatenate(LA, axis=1); LB = np.concatenate(LB, axis=1)
        pres = (A >= 0) & (B >= 0) & (LA == LB)
        A = np.where(pres, A, -1); B = np.where(pres, B, -1)
        code = (pres * (1 << np.arange(K))[:, None]).sum(0)
        keep = code > 0
        self.colA, self.colB, self.colSQ, self.colcode = A[:, keep], B[:, keep], SQ[keep], code[keep]
        self.patterns = {}
        for c in np.unique(self.colcode):
            sel = np.nonzero(self.colcode == c)[0]
            lay = [k for k in range(K) if c >> k & 1]
            self.patterns[int(c)] = (lay, sel)
        self.info["column_edges"] = int(keep.sum()); self.info["column_patterns"] = len(self.patterns)
        self.log(f"[exp2] column edges {keep.sum()} patterns {len(self.patterns)} {time.time()-t0:.1f}s")
        # capacitance cells
        self.caps = []
        for k, l, between in self.pair_c:
            for bi in range(2):
                ik = self.blocks[k][bi]["ids"]; il = self.blocks[l][bi]["ids"]
                both = (ik >= 0) & (il >= 0)
                self.caps.append((ik[both], il[both], self.blocks[k][bi]["h"] ** 2, between))
        # ---------------- series rail layers (TOP, L20, L21) -------------
        ts = TwoSided(ex, self.shapes, 3)
        self.series_edges = []  # (a, b, sq, d_um, sigma, t)
        self.series_blocks = {}
        explicit_rail = {L for L, kind in EXPLICIT if "R" in kind}
        for L in [x for x in SERIES_RAIL if x not in explicit_rail]:
            g = rail_geoms[L]
            if L == "Signal$TOP":
                b = M.rasterize(g, self.top_h); bl = [b]
            else:
                bl = blocks_for(g, self.h, self.fh, self.win, self.fbox)
            row = self.st.row(L)
            for b in bl:
                ids = -np.ones(b["mask"].shape, np.int64); cnt = int(b["mask"].sum())
                if cnt:
                    ids[b["mask"]] = np.arange(self.new(cnt), self.n)
                b["ids"] = ids
                if cnt == 0:
                    continue
                d = ts(L, b)["d_eff"]
                m = b["mask"]
                for (sa, sb) in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))), ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
                    pr = m[sa] & m[sb]
                    self.series_edges.append((ids[sa][pr], ids[sb][pr], np.ones(int(pr.sum())), 0.5 * (d[sa][pr] + d[sb][pr]), row["conductivity"], row["thickness_um"] * 1e-6))
            if len(bl) == 2 and bl[0]["mask"].any() and bl[1]["mask"].any():
                c0_, f0_ = bl
                jj, ii = np.nonzero(f0_["mask"])
                ea, eb = [], []
                for dj, di in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    x = f0_["x0"] + (ii + 0.5) * self.fh + di * (self.fh / 2 + self.h / 2); y = f0_["y0"] + (jj + 0.5) * self.fh + dj * (self.fh / 2 + self.h / 2)
                    cii = np.floor((x - c0_["x0"]) / self.h).astype(int); cjj = np.floor((y - c0_["y0"]) / self.h).astype(int)
                    inside_f = (np.floor((x - f0_["x0"]) / self.fh) >= 0) & (np.floor((x - f0_["x0"]) / self.fh) < f0_["nx"]) & (np.floor((y - f0_["y0"]) / self.fh) >= 0) & (np.floor((y - f0_["y0"]) / self.fh) < f0_["ny"])
                    ok = (~inside_f) & (cii >= 0) & (cii < c0_["nx"]) & (cjj >= 0) & (cjj < c0_["ny"])
                    ok[ok] &= c0_["mask"][cjj[ok], cii[ok]]
                    ea.append(f0_["ids"][jj[ok], ii[ok]]); eb.append(c0_["ids"][cjj[ok], cii[ok]])
                ea = np.concatenate(ea); eb = np.concatenate(eb)
                self.series_edges.append((ea, eb, np.full(len(ea), (self.fh / 2 + self.h / 2) / self.fh), np.full(len(ea), 30.0), row["conductivity"], row["thickness_um"] * 1e-6))
            self.series_blocks[L] = bl
        self.info["series_rail_cells"] = {L: int(sum(int(b["mask"].sum()) for b in bl)) for L, bl in self.series_blocks.items()}
        self.info["reference_search_series_layers"] = ts.report
        # ---------------- TOP DGND sheet (R only) -------------------------
        gtop = self.shapes["Signal$TOP"].get("DGND")
        tw = (math.floor(self.win[0] / self.top_h) * self.top_h, math.floor(self.win[1] / self.top_h) * self.top_h,
              math.ceil(self.win[2] / self.top_h) * self.top_h, math.ceil(self.win[3] / self.top_h) * self.top_h)
        bt = M.rasterize(gtop, self.top_h, window=tw)  # cropped to the model window
        ids = -np.ones(bt["mask"].shape, np.int64); cnt = int(bt["mask"].sum()); ids[bt["mask"]] = np.arange(self.new(cnt), self.n); bt["ids"] = ids
        rowt = self.st.row("Signal$TOP"); rsq = 1.0 / (rowt["conductivity"] * rowt["thickness_um"] * 1e-6)
        ga, gb = [], []
        m = bt["mask"]
        for (sa, sb) in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))), ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
            pr = m[sa] & m[sb]; ga.append(ids[sa][pr]); gb.append(ids[sb][pr])
        self.top_gnd_edges = (np.concatenate(ga), np.concatenate(gb), rsq)
        self.gnd_top_block = [bt]
        self.info["top_gnd_cells"] = cnt
        # ---------------- snapping helpers --------------------------------
        def make_snap(layer_blocks, lab=None):
            out = []
            for b in layer_blocks:
                jj, ii = np.nonzero(b["mask"] if lab is None else (b["label"] == lab))
                if len(jj) == 0:
                    out.append(None); continue
                out.append((b, cKDTree(np.column_stack([b["x0"] + (ii + 0.5) * b["h"], b["y0"] + (jj + 0.5) * b["h"]])), b["ids"][jj, ii]))
            return out
        snaps = {}
        for (L, kind), bl in zip(EXPLICIT, self.blocks):
            if "R" in kind:
                snaps[(L, "R")] = make_snap(bl, 1)
            if "G" in kind:
                snaps[(L, "G")] = make_snap(bl, 2)
        snaps.update({(L, "R"): make_snap(bl) for L, bl in self.series_blocks.items()})
        gsnap_top = make_snap(self.gnd_top_block)
        kinds = dict(EXPLICIT)

        def snap(L, x, y):
            if L not in snaps:
                return None
            lst = snaps[L]
            if all(it is None for it in lst):
                return None
            order = [1, 0] if len(lst) == 2 else [0]
            for bi in order:
                it = lst[bi]
                if it is None:
                    continue
                b, tree, idsarr = it
                inside = b["x0"] <= x < b["x0"] + b["nx"] * b["h"] and b["y0"] <= y < b["y0"] + b["ny"] * b["h"]
                if len(lst) == 2 and bi == 1 and not inside:
                    continue
                dist, k = tree.query([x, y])
                if dist <= 0.75 * b["h"] * math.sqrt(2) + 1e-9:
                    return int(idsarr[k]), b["h"]
                if len(lst) == 2 and bi == 1 and inside:
                    return None
            return None

        # ---------------- rail nodes / traces / vias / pads ---------------
        rn = ex["rail_nodes"]
        ridx = {}; rh = {}
        for nid, (x, y, lay, ps) in rn.items():
            if not (self.win[0] <= x <= self.win[2] and self.win[1] <= y <= self.win[3]):
                continue
            r = snap((lay, "R"), x, y)
            if r is not None:
                ridx[nid], rh[nid] = r
            else:
                ridx[nid] = self.new(1)
        self.port_pos = self.new(1)
        pos = {ridx[x] for x in ex["port_pos_nodes"] if x in ridx}
        self.info["port_pos_found"] = sum(1 for x in ex["port_pos_nodes"] if x in ridx)
        remap = {p: self.port_pos for p in pos}
        R_ = lambda i: remap.get(i, i)  # noqa: E731
        # rail traces
        ta, tb, tsq, tsig, tt, tL = [], [], [], [], [], []
        lwd = ex.get("layer_default_width_um", {})
        for s_, e_, w in ex["rail_traces"]:
            if s_ not in ridx or e_ not in ridx:
                continue
            a_, b_ = R_(ridx[s_]), R_(ridx[e_])
            if a_ == b_:
                continue
            xs, ys, Ls, _ = rn[s_]; xe, ye, _, _ = rn[e_]
            ln = math.hypot(xe - xs, ye - ys)
            if s_ in rh and e_ in rh and ln <= 1.5 * max(rh[s_], rh[e_]):
                continue
            w = w or lwd.get(Ls, 25.0); ln = max(ln, 1.0)
            row = self.st.row(Ls); ng = self.st.nearest_gnd(Ls); d = min(g_[1] for g_ in ng) if ng else 30.0
            ta.append(a_); tb.append(b_); tsq.append(ln / w); tsig.append(row["conductivity"]); tt.append(row["thickness_um"] * 1e-6)
            tL.append(MU0 * d * 1e-6 * ln / (w + 2 * d))
        self.rail_traces = (np.array(ta, np.int64), np.array(tb, np.int64), np.array(tsq), np.array(tsig), np.array(tt), np.array(tL))
        # rail vias (R + coax L) and pads
        gxy = ex["gnd_xy_by_layer"]; gtrees = {L: cKDTree(v) for L, v in gxy.items() if len(v)}
        va, vb, vR, vL = [], [], [], []
        rc = {}
        for up, lo, ps in ex["rail_vias"]:
            if up not in ridx or lo not in ridx:
                continue
            xu, yu, Lu, _ = rn[up]; _, _, Ll, _ = rn[lo]
            a_, b_ = R_(ridx[up]), R_(ridx[lo])
            if a_ == b_:
                continue
            ln = abs(self.st.z_center_um[Ll] - self.st.z_center_um[Lu])
            pad = ex["padstacks"].get(ps, {}); drill = pad.get("drill_um") or 40.0
            key = (ps, Lu, Ll)
            if key not in rc:
                rc[key] = estimate_via_segment_rl(length_um=ln, drill_diameter_um=drill, padstack_material=pad.get("material"), start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"]).resistance_ohm
            sdist = min(max(float(gtrees[Lu].query([xu, yu])[0]) if Lu in gtrees else 1000.0, drill), 1000.0)
            va.append(a_); vb.append(b_); vR.append(rc[key]); vL.append(MU0 / (2 * math.pi) * ln * 1e-6 * math.log(sdist / (drill / 2)))
        pl = self._pad_links(rn, ridx, R_)
        for a_, b_, r_ in pl:
            va.append(a_); vb.append(b_); vR.append(r_); vL.append(1e-15)
        self.rail_rl = (np.array(va, np.int64), np.array(vb, np.int64), np.array(vR), np.array(vL))
        self.info.update(rail_traces=len(ta), rail_vias=len(va) - len(pl), rail_pad_links=len(pl))
        # ---------------- GND network -----------------------------------
        gn = self.gnd["nodes"]
        gidx = {}; nsnap = 0
        neg = set(ex["port_neg_nodes"])
        top_links = []
        for nid, (x, y, lay, ps) in gn.items():
            rt = None
            if lay == "Signal$TOP" and gsnap_top[0] is not None:
                b_, tree_, ida_ = gsnap_top[0]
                dd_, kk_ = tree_.query([x, y])
                if dd_ <= 0.75 * b_["h"] * math.sqrt(2) + 1e-9:
                    rt = int(ida_[kk_])
            if nid in neg:
                gidx[nid] = -1
                if rt is not None:
                    top_links.append(rt)
                continue
            if rt is not None:
                gidx[nid] = rt; nsnap += 1; continue
            r = snap((lay, "G"), x, y)
            if r is not None:
                gidx[nid] = r[0]; nsnap += 1
        # R-only GND elements over node *names*; unresolved names become nodes after chain reduction
        elems = []
        for s_, e_, w in self.gnd["traces"]:
            xs, ys, Ls, _ = gn[s_]; xe, ye, _, _ = gn[e_]
            ln = max(math.hypot(xe - xs, ye - ys), 1.0); w = w or lwd.get(Ls, 25.0)
            row = self.st.row(Ls)
            elems.append((s_, e_, ln / (w * row["conductivity"] * row["thickness_um"] * 1e-6)))
        gc = {}
        for up, lo, ps in self.gnd["vias"]:
            Lu = gn[up][2]; Ll = gn[lo][2]
            ln = abs(self.st.z_center_um[Ll] - self.st.z_center_um[Lu])
            pad = ex["padstacks"].get(ps, {}); drill = pad.get("drill_um") or 40.0
            key = (ps, Lu, Ll)
            if key not in gc:
                gc[key] = estimate_via_segment_rl(length_um=ln, drill_diameter_um=drill, padstack_material=pad.get("material"), start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"]).resistance_ohm
            elems.append((up, lo, gc[key]))
        gidx_tmp = dict(gidx)
        for a_, b_, r_ in self._pad_links(gn, None, None, names_only=True):
            elems.append((a_, b_, r_))
        # terminals that must survive reduction: decap GND pins
        term = {dc["gnd_node"] for dc in ex["decaps"] if dc["gnd_node"] in gn}
        elems = self._reduce(elems, fixed=set(gidx_tmp) | term)
        for a_, b_, r_ in elems:
            for x in (a_, b_):
                if x not in gidx:
                    gidx[x] = self.new(1)
        ea_ = [gidx[a] for a, b, r in elems] + list(self.top_gnd_edges[0]) + top_links
        eb_ = [gidx[b] for a, b, r in elems] + list(self.top_gnd_edges[1]) + [-1] * len(top_links)
        er_ = [r for a, b, r in elems] + [self.top_gnd_edges[2]] * len(self.top_gnd_edges[0]) + [0.5 * self.top_gnd_edges[2]] * len(top_links)
        self.gnd_r = (np.array(ea_, np.int64), np.array(eb_, np.int64), np.array(er_))
        self.info["top_gnd_port_links"] = len(top_links)
        self.info.update(gnd_nodes_in_window=len(gn), gnd_nodes_snapped_to_planes=nsnap, gnd_elements_after_reduction=len(elems),
                         gnd_standalone_nodes=sum(1 for k, v in gidx.items() if v >= 0 and k not in gidx_tmp))
        # ---------------- decaps ------------------------------------------
        self.dec = []
        miss = 0
        for dc in ex["decaps"]:
            if dc["rail_node"] not in ridx or dc["gnd_node"] not in gidx:
                miss += 1; continue
            self.dec.append((R_(ridx[dc["rail_node"]]), gidx[dc["gnd_node"]], dc["model_id"]))
        self.info["decaps_missing_nodes"] = miss
        self.remap = remap
        self._prune()
        self.info["build_seconds"] = time.time() - t0
        self.log("[exp2] info", {k: v for k, v in self.info.items() if k != "reference_search_series_layers"})

    def _pad_links(self, nodes, idx, R_, names_only=False):
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
                if not ((dx <= pw / 2 + 1 and dy <= ph / 2 + 1) or (dx <= ph / 2 + 1 and dy <= pw / 2 + 1)):
                    continue
                if names_only:
                    out.append((nid, o, rl))
                else:
                    if nid not in idx or o not in idx:
                        continue
                    a_, b_ = R_(idx[nid]), R_(idx[o])
                    if a_ != b_:
                        out.append((a_, b_, rl))
        return out

    @staticmethod
    def _reduce(elems, fixed):
        """Series-merge degree-2 non-fixed nodes and drop dangling non-fixed leaves (R-only)."""
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
                if m not in fixed and len(adj[m]) <= 2: stack.append(m)
            elif len(nb) == 2:
                (m1, r1), (m2, r2) = nb.items()
                del adj[m1][n]; del adj[m2][n]; del adj[n]
                add(m1, m2, r1 + r2)
                for m in (m1, m2):
                    if m not in fixed and len(adj[m]) <= 2: stack.append(m)
        out = []
        for a, nb in adj.items():
            for b, r in nb.items():
                if str(a) < str(b):
                    out.append((a, b, r))
        return out

    def _prune(self):
        n = self.n + 1  # last = ground (-1)
        gi = self.n
        def fix(x):
            x = np.asarray(x, np.int64); return np.where(x < 0, gi, x)
        rows, cols = [], []
        for c, (lay, sel) in self.patterns.items():
            for k in lay:
                rows.append(self.colA[k, sel]); cols.append(self.colB[k, sel])
        for a, b, _, _, _, _ in self.series_edges:
            rows.append(a); cols.append(b)
        for a, b, *_ in (self.rail_traces, self.rail_rl, self.gnd_r):
            rows.append(fix(a)); cols.append(fix(b))
        for a, b, _, _ in self.caps:
            rows.append(a); cols.append(b)
        if self.dec:
            rows.append(fix([d[0] for d in self.dec])); cols.append(fix([d[1] for d in self.dec]))
        rr = np.concatenate([fix(np.asarray(r)) for r in rows]); cc = np.concatenate([fix(np.asarray(c)) for c in cols])
        if self.remap:
            mp = np.arange(n);
            for k_, v_ in self.remap.items(): mp[k_] = v_
            rr = mp[rr]; cc = mp[cc]
        else:
            mp = np.arange(n)
        Gm = sparse.coo_matrix((np.ones(len(rr)), (rr, cc)), shape=(n, n))
        _, lab = csgraph.connected_components(Gm, directed=False)
        keep = (lab == lab[self.port_pos]) & (np.arange(n) != gi)
        self.info["ground_in_port_component"] = bool(lab[gi] == lab[self.port_pos])
        new = -np.ones(n, np.int64); new[keep] = np.arange(int(keep.sum())); new[gi] = -1
        self.map = lambda x: new[mp[fix(x)]]  # noqa: E731
        self.N = int(keep.sum())
        self.info["unknowns"] = self.N; self.info["nodes_before_prune"] = self.n
        self.P = self.map([self.port_pos])[0]
        self.info["decaps_connected"] = int(sum(1 for d in self.dec if keep[mp[d[0]]]))


class Solver:
    def __init__(self, b: Builder):
        self.b = b

    def assemble(self, f):
        b = self.b; w = 2 * math.pi * f
        rows, cols, vals = [], [], []
        diag = np.zeros(b.N + 1, complex)  # last = ground sink
        def st(a, c, y):
            a = np.where(a < 0, b.N, a); c = np.where(c < 0, b.N, c)
            rows.extend([a, c, a, c]); cols.extend([a, c, c, a]); vals.extend([y, y, -y, -y])
        zs = np.array([complex(copper_surface_impedance(f, s, t)) for s, t in b.sigma_t])
        self.colY = {}
        for c, (lay, sel) in b.patterns.items():
            Z = np.diag(zs[lay]) + 1j * w * MU0 * b.G[np.ix_(lay, lay)]
            Yp = np.linalg.inv(Z)
            self.colY[c] = Yp
            sq = b.colSQ[sel]
            An = [b.map(b.colA[k, sel]) for k in lay]; Bn = [b.map(b.colB[k, sel]) for k in lay]
            for i_, k in enumerate(lay):
                for j_, l in enumerate(lay):
                    if Yp[i_, j_] == 0:
                        continue
                    y = Yp[i_, j_] / sq
                    a1 = np.where(An[i_] < 0, b.N, An[i_]); a2 = np.where(An[j_] < 0, b.N, An[j_])
                    b1 = np.where(Bn[i_] < 0, b.N, Bn[i_]); b2 = np.where(Bn[j_] < 0, b.N, Bn[j_])
                    rows += [a1, b1, a1, b1]; cols += [a2, b2, b2, a2]; vals += [y, y, -y, -y]
        for a, c, sq, d, s, t in b.series_edges:
            y = 1.0 / ((complex(copper_surface_impedance(f, s, t)) + 1j * w * MU0 * d * 1e-6) * sq)
            st(b.map(a), b.map(c), y)
        a, c, sq, sig, t, Lt = b.rail_traces
        zst = np.array([complex(copper_surface_impedance(f, s_, t_)) for s_, t_ in zip(sig, t)])
        st(b.map(a), b.map(c), 1.0 / (zst * sq + 1j * w * Lt))
        a, c, Rv, Lv = b.rail_rl
        st(b.map(a), b.map(c), 1.0 / (Rv + 1j * w * Lv))
        a, c, Rg = b.gnd_r
        st(b.map(a), b.map(c), (1.0 / Rg).astype(complex))
        for ik, il, area, between in b.caps:
            inv = sum(r["thickness_um"] * 1e-6 / M.eps_tand(r, f)[0] for r in between)
            td = M.eps_tand(between[0], f)[1]
            C = EPS0 * area * 1e-12 / inv
            st(b.map(ik), b.map(il), np.full(len(ik), w * C * td + 1j * w * C))
        if b.dec:
            ys = {mid: 1.0 / b.ex["models"][mid].impedance([f])[0] for mid in {d[2] for d in b.dec}}
            st(b.map([d[0] for d in b.dec]), b.map([d[1] for d in b.dec]), np.array([ys[d[2]] for d in b.dec]))
        R = np.concatenate([np.asarray(x).ravel() for x in rows]); C_ = np.concatenate([np.asarray(x).ravel() for x in cols])
        V = np.concatenate([np.broadcast_to(np.asarray(x), np.asarray(r).shape).ravel() for x, r in zip(vals, rows)])
        ok = (R < b.N) & (C_ < b.N)
        Y = sparse.coo_matrix((V[ok], (R[ok], C_[ok])), shape=(b.N, b.N)).tocsc()
        return Y

    def solve(self, freqs, verbose=True, want_v=False):
        Z = []; stats = []; Vs = []
        for f in freqs:
            t0 = time.time()
            Y = self.assemble(f)
            t1 = time.time()
            lu = splu(Y, permc_spec="COLAMD")
            t2 = time.time()
            rhs = np.zeros(self.b.N, complex); rhs[self.b.P] = 1.0
            V = lu.solve(rhs)
            Z.append(V[self.b.P])
            nnz = lu.nnz
            stats.append(dict(f=float(f), assemble_s=t1 - t0, factor_s=t2 - t1, nnz_Y=int(Y.nnz), nnz_LU=int(nnz),
                              rss_MB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
            if want_v:
                Vs.append(V)
            if verbose:
                print(f"  f={f:11.4e} Z={Z[-1].real:+.4e}{Z[-1].imag:+.4e}j asm {t1-t0:.1f}s fact {t2-t1:.1f}s nnzLU {nnz/1e6:.1f}M rss {stats[-1]['rss_MB']:.0f}MB", flush=True)
            del lu
        return (np.array(Z), stats, Vs) if want_v else (np.array(Z), stats)

    def breakdown(self, f, V):
        """Re Z / Im Z/w split: column layers R, column magnetic energy per gap, series rail, traces, vias, GND R, decaps."""
        b = self.b; w = 2 * math.pi * f
        Vx = np.append(V, 0.0)
        g = lambda idx: Vx[np.where(idx < 0, b.N, idx)]  # noqa: E731
        out = collections.OrderedDict()
        zs = np.array([complex(copper_surface_impedance(f, s, t)) for s, t in b.sigma_t])
        K = len(EXPLICIT)
        gaps = np.diff(b.s_um) * 1e-6
        for c, (lay, sel) in b.patterns.items():
            Yp = self.colY[c]
            dV = np.stack([g(b.map(b.colA[k, sel])) - g(b.map(b.colB[k, sel])) for k in lay])  # m x E
            I = (Yp @ dV) / b.colSQ[sel]  # currents
            sq = b.colSQ[sel]
            for i_, k in enumerate(lay):
                key = f"col_R:{EXPLICIT[k][0]}"
                out[key] = out.get(key, 0) + np.sum(np.abs(I[i_]) ** 2 * sq) * zs[k]
            full = np.zeros((K, I.shape[1]), complex); full[lay] = I
            for grp in b.groups:
                cum = np.cumsum(full[grp], axis=0)
                for q in range(len(grp) - 1):
                    gi_ = grp[q]
                    key = f"col_L_gap:{EXPLICIT[gi_][0][7:10]}-{EXPLICIT[grp[q+1]][0][7:10]}"
                    out[key] = out.get(key, 0) + 1j * w * MU0 * gaps[gi_] * np.sum(np.abs(cum[q]) ** 2 * sq)
        for a, c, sq, d, s, t in b.series_edges:
            z = (complex(copper_surface_impedance(f, s, t)) + 1j * w * MU0 * d * 1e-6) * sq
            i = (g(b.map(a)) - g(b.map(c))) / z
            out["series_rail_planes"] = out.get("series_rail_planes", 0) + np.sum(np.abs(i) ** 2 * z)
        a, c, sq, sig, t, Lt = b.rail_traces
        z = np.array([complex(copper_surface_impedance(f, s_, t_)) for s_, t_ in zip(sig, t)]) * sq + 1j * w * Lt
        i = (g(b.map(a)) - g(b.map(c))) / z; out["rail_traces"] = np.sum(np.abs(i) ** 2 * z)
        a, c, Rv, Lv = b.rail_rl
        z = Rv + 1j * w * Lv; i = (g(b.map(a)) - g(b.map(c))) / z; out["rail_vias_pads"] = np.sum(np.abs(i) ** 2 * z)
        a, c, Rg = b.gnd_r
        i = (g(b.map(a)) - g(b.map(c))) / Rg; out["gnd_vias_traces_R"] = np.sum(np.abs(i) ** 2 * Rg)
        s = 0
        for ik, il, area, between in b.caps:
            inv = sum(r["thickness_um"] * 1e-6 / M.eps_tand(r, f)[0] for r in between); td = M.eps_tand(between[0], f)[1]
            y = w * EPS0 * area * 1e-12 / inv * (td + 1j)
            dv = g(b.map(ik)) - g(b.map(il)); s += np.sum(np.abs(dv) ** 2 * np.conj(y))
        out["plane_caps"] = s
        ys = {mid: 1.0 / b.ex["models"][mid].impedance([f])[0] for mid in {d[2] for d in b.dec}}
        s = 0
        for ra, ga, mid in b.dec:
            dv = g(b.map([ra]))[0] - g(b.map([ga]))[0]; s += abs(dv) ** 2 * np.conj(ys[mid])
        out["decaps"] = s
        res = {k: dict(mOhm=float(np.real(v)) * 1e3, pH=float(np.imag(v)) / w * 1e12) for k, v in out.items()}
        res["_total"] = dict(mOhm=float(sum(np.real(v) for v in out.values())) * 1e3, Zport=[float(V[b.P].real), float(V[b.P].imag)])
        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=float, default=250.0)
    ap.add_argument("--fine-h", type=float, default=100.0)
    ap.add_argument("--top-h", type=float, default=50.0)
    ap.add_argument("--freqs", default="")
    ap.add_argument("--h-check", action="store_true", help="solve h/2 (h, fine-h halved) at 1 MHz")
    ap.add_argument("--tag", default="")
    ap.add_argument("--decouple-groups", action="store_true")
    ap.add_argument("--all-gnd", action="store_true", help="diagnosis: every DGND plane L02-L28 explicit")
    ap.add_argument("--solver", default="superlu", choices=["superlu", "pardiso"])
    ap.add_argument("--out", default="/home/claude/work/exp2")
    a = ap.parse_args()
    T0 = time.time()
    ex = pickle.load(open("/home/claude/work/exp1/extract_port18.pkl", "rb"))
    gnd = pickle.load(open(os.path.join(a.out, "extract_gnd.pkl"), "rb"))
    shapes = pickle.load(open("/home/claude/work/exp1/neighbour_shapes.pkl", "rb"))
    res1 = json.load(open("/home/claude/work/exp1/result.json"))
    fbox = res1["discretisation"]["fine_box_um"]
    win = gnd["window"]
    ref = np.load("/home/claude/data/S4LB002_260729_Zdiag.npz", allow_pickle=True)
    fref = ref["freq"]; zref = ref["Zdiag"][:, 17]
    global EXPLICIT
    if a.all_gnd:
        EXPLICIT = EXPLICIT_ALLGND
        need = [L for L, _ in EXPLICIT if L not in shapes]
        if need:
            from exp1b import load_layer_shapes
            shapes.update(load_layer_shapes(ex["spd_path"], need))
            pickle.dump(shapes, open("/home/claude/work/exp1/neighbour_shapes.pkl", "wb"))
    b = Builder(ex, gnd, shapes, a.h, a.fine_h, a.top_h, win, fbox, decouple_groups=a.decouple_groups)
    b.build()
    S = Solver(b)
    if a.freqs:
        idx = sorted({int(np.argmin(abs(fref - float(x)))) for x in a.freqs.split(",")}); ridx = []
    else:
        idx = pick_freqs(fref, 25)
        ridx = [i for i in range(len(fref)) if 0.8e6 <= fref[i] <= 3.0e6]
    all_idx = sorted(set(idx) | set(ridx)); freqs = fref[all_idx]
    t = time.time()
    Z, stats = S.solve(freqs)
    out = dict(model=__doc__, h=a.h, fine_h=a.fine_h, top_h=a.top_h, window_um=list(win), fine_box_um=fbox, info=b.info,
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zref[all_idx].real], Zref_im=[float(x) for x in zref[all_idx].imag],
               rel_err=[float(x) for x in np.abs(Z - zref[all_idx]) / np.abs(zref[all_idx])],
               dRe_mOhm=[float(x) for x in (Z.real - zref[all_idx].real) * 1e3],
               dL_pH=[float(x) for x in (Z.imag - zref[all_idx].imag) / (2 * np.pi * freqs) * 1e12],
               per_freq_stats=stats, solve_seconds=time.time() - t)
    if not a.freqs:
        sel = np.array([all_idx.index(i) for i in idx])
        rsel = [all_idx.index(i) for i in ridx]
        fr_m, zm = resonance(freqs[rsel], Z[rsel]); fr_r, zr_ = resonance(fref[ridx], zref[ridx])
        out["gates"] = gates(freqs[sel], Z[sel], zref[idx], fr_m, zm, fr_r, zr_)
        print({k: (gg.get("PASS"), gg.get("max_abs_dRe_mOhm", gg.get("max_abs_pH", gg.get("rel_err", gg.get("max_rel_err"))))) for k, gg in out["gates"].items() if k != "G5"}, flush=True)
    bd = {}
    for fb in (1e4, 1e6):
        _, _, Vs = S.solve([fb], verbose=False, want_v=True)
        bd[f"{fb:.0e}"] = S.breakdown(fb, Vs[0])
        print(fb, {k: (round(v["mOhm"], 4), round(v.get("pH", 0), 2)) for k, v in bd[f"{fb:.0e}"].items()}, flush=True)
    out["breakdown"] = bd
    if a.h_check:
        del S, b
        b2 = Builder(ex, gnd, shapes, a.h / 2, a.fine_h / 2, a.top_h / 2, win, fbox, decouple_groups=a.decouple_groups)
        b2.build()
        Z2, st2 = Solver(b2).solve([1e6])
        i1 = list(freqs).index(1e6) if 1e6 in list(freqs) else None
        out["convergence_1MHz"] = dict(h_half=a.h / 2, fine_h_half=a.fine_h / 2, unknowns_h_half=b2.N, Z_h_half=[float(Z2[0].real), float(Z2[0].imag)],
                                       rel_change=float(abs(Z[i1] - Z2[0]) / abs(Z2[0])) if i1 is not None else None,
                                       rel_err_h_half=float(abs(Z2[0] - zref[np.argmin(abs(fref - 1e6))]) / abs(zref[np.argmin(abs(fref - 1e6))])), stats=st2)
        print("conv", out["convergence_1MHz"], flush=True)
    out["peak_rss_MB"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    out["wall_seconds"] = time.time() - T0
    fn = os.path.join(a.out, f"result{a.tag}.json")
    json.dump(out, open(fn, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if not a.freqs:
        r1b = json.load(open("/home/claude/work/exp1/result_exp1b.json"))
        plot(os.path.join(a.out, f"exp2_Z18{a.tag}.png"), {
            "EXP-1b (two-sided d_eff, ideal GND)": dict(freq=r1b["freq"], Z_re=r1b["Z_re"], Z_im=r1b["Z_im"]),
            f"EXP-2 multiconductor (h={a.h:g}/{a.fine_h:g}um)": dict(freq=out["freq"], Z_re=out["Z_re"], Z_im=out["Z_im"])}, fref, zref)
    print("done", out["wall_seconds"], out["peak_rss_MB"])


if __name__ == "__main__":
    main()
