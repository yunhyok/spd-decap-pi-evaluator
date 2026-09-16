"""EXP-3 model: EXP-1b (variant B) base with
  S1  homogenised plane conductance (10 µm / 5 µm sub-tiles, traces + pads drawn as metal,
      window conductance per cell edge, fill-weighted capacitance),
  S2  fringing for narrow conductors in plane cells (effective width w+2d when w < 5 d),
  S3  explicit resistive DGND sheets (nearest DGND planes, same homogenisation, R only)
      stitched by DGND vias/traces, port- pins = reference, decap GND pins into the sheets.
Unchanged from EXP-1/1b: via R (via_model.estimate_via_segment_rl) and coax L,
off-plane rail traces, pad links, decap SPICE Y(f), port+ supernode, per-cell two-sided
d_eff (exp1b.TwoSided), dielectric tables (model.eps_tand).
"""
from __future__ import annotations

import collections
import math
import os
import sys
import time

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph
from scipy.sparse.linalg import splu
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exp1"))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import peak_rss_mb  # noqa: E402
import model as M  # noqa: E402
import homog as H  # noqa: E402
from exp1b import TwoSided  # noqa: E402
from spd_decap_pi._core.solver.mfdm import copper_surface_impedance  # noqa: E402
from spd_decap_pi._core.via_model import estimate_via_segment_rl  # noqa: E402

MU0, EPS0 = M.MU0, M.EPS0
GND_SHEETS = ["Signal$L02(DGND)", "Signal$L13(DGND)", "Signal$L16(DGND)", "Signal$L18(DGND)",
              "Signal$L24(DGND)", "Signal$L27(DGND)"]


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


class Sheet:
    """Homogenised conductor sheet for one layer: coarse block (+ optional fine block)."""

    def __init__(self, layer, geom, traces, h, sub_c, fh, sub_f, fine_box, bbox, n0, log=print):
        self.layer = layer
        self.iface = None
        t0 = time.time()
        X0 = math.floor(bbox[0] / h) * h; Y0 = math.floor(bbox[1] / h) * h
        nx = int(math.ceil((bbox[2] - X0) / h)); ny = int(math.ceil((bbox[3] - Y0) / h))
        s = h / sub_c
        img = H.raster_image(geom, traces, X0, Y0, nx * sub_c, ny * sub_c, s)
        fill, Gx, Gy = H.cell_edges(img, sub_c)
        c = dict(x0=X0, y0=Y0, nx=nx, ny=ny, h=h, fill=fill, Gx=Gx, Gy=Gy)
        self.blocks = [c]
        f = None
        if fine_box is not None and fh:
            fx0 = max(math.floor(fine_box[0] / h) * h, X0); fy0 = max(math.floor(fine_box[1] / h) * h, Y0)
            fx1 = min(math.ceil(fine_box[2] / h) * h, X0 + nx * h); fy1 = min(math.ceil(fine_box[3] / h) * h, Y0 + ny * h)
            if fx1 > fx0 and fy1 > fy0:
                fnx = int(round((fx1 - fx0) / fh)); fny = int(round((fy1 - fy0) / fh))
                fimg = H.raster_image(geom, traces, fx0, fy0, fnx * sub_f, fny * sub_f, fh / sub_f)
                ffill, fGx, fGy = H.cell_edges(fimg, sub_f)
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
        g = H.window_conductance(W)
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


class Model3:
    def __init__(self, ex, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=None, sub_c=20, sub_f=10, sub_top=10,
                 fringe=False, fringe_wd=5.0, gnd=None, gnd_h=None, gnd_fh=None, log=print):
        self.gnd_h = gnd_h or h; self.gnd_fh = gnd_fh or fh
        self.ex, self.shapes, self.log = ex, shapes, log
        self.h, self.fh, self.top_h, self.fine_box = h, fh, top_h, fine_box
        self.fringe, self.fringe_wd = fringe, fringe_wd
        self.gnd = gnd  # None (ideal GND, variant B) or extract dict for S3
        self.st = M.Stack(ex["stackup"])
        self.info = dict(h=h, fine_h=fh, top_h=top_h, sub_um_coarse=h / sub_c, sub_um_fine=fh / sub_f, sub_um_top=top_h / sub_top,
                         fringe=fringe, fringe_w_over_d_max=fringe_wd, explicit_gnd=gnd is not None,
                         gnd_sheet_h=gnd_h or h, gnd_sheet_fine_h=gnd_fh or fh)
        self.sub = (sub_c, sub_f, sub_top)
        self.build()

    # ------------------------------------------------------------------
    def build(self):
        t0 = time.time()
        ex, st = self.ex, self.st
        rn = ex["rail_nodes"]
        lwd = ex.get("layer_default_width_um", {})
        geoms = {g["layer"]: g for g in ex["rail_geoms"]}
        plane_layers = list(geoms)
        # rail traces drawn into their plane layer
        drawn = set()
        traces_by_layer = collections.defaultdict(list)
        for k, (s_, e_, w) in enumerate(ex["rail_traces"]):
            if s_ in rn and e_ in rn and rn[s_][2] == rn[e_][2] and rn[s_][2] in geoms:
                L = rn[s_][2]
                traces_by_layer[L].append((rn[s_][0], rn[s_][1], rn[e_][0], rn[e_][1], w or lwd.get(L, 25.0)))
                drawn.add(k)
        ts = TwoSided(ex, self.shapes, 3)
        n = 0
        self.sheets = {}
        for L in plane_layers:
            tr = traces_by_layer[L] + pad_traces(rn, ex["padstacks"], L)
            pts = np.vstack([p.reshape(-1, 2) for p in geoms[L]["pos_polys"]] + [np.array([[t[0], t[1]], [t[2], t[3]]]) for t in tr])
            pad = 500.0
            bbox = (pts[:, 0].min() - pad, pts[:, 1].min() - pad, pts[:, 0].max() + pad, pts[:, 1].max() + pad)
            if L == "Signal$TOP":
                sh = Sheet(L, geoms[L], tr, self.top_h, self.sub[2], None, None, None, bbox, n, self.log)
            else:
                sh = Sheet(L, geoms[L], tr, self.h, self.sub[0], self.fh, self.sub[1], self.fine_box, bbox, n, self.log)
            n = sh.n
            # two-sided d_eff per cell (EXP-1b rule), capacitance per metal area
            base = sh.cells.min() if len(sh.cells) else 0
            dloc = np.zeros(n - base); cloc = np.zeros(n - base); tloc = np.zeros(n - base)
            for b in sh.blocks:
                blk = dict(x0=b["x0"], y0=b["y0"], nx=b["nx"], ny=b["ny"], h=b["h"], mask=b["mask"])
                r = ts(L, blk)
                m = b["mask"]
                dloc[b["ids"][m] - base] = r["d_eff"][m]; cloc[b["ids"][m] - base] = r["c_um2"][m]; tloc[b["ids"][m] - base] = r["tand"][m]
            a, bb, ell, wid, g = sh.edges
            sh.d_edge = 0.5 * (dloc[a - base] + dloc[bb - base])
            sh.c_cell = cloc[sh.cells - base]; sh.tand_cell = tloc[sh.cells - base]
            row = st.row(L); sh.sigma = row["conductivity"]; sh.t = row["thickness_um"] * 1e-6
            self.sheets[L] = sh
        self.info["reference_search"] = ts.report
        self.info["rail_traces_drawn_into_planes"] = len(drawn)
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
        rc = {}
        for up, lo, ps in ex["rail_vias"]:
            if up not in rn or lo not in rn:
                continue
            xu, yu, Lu, _ = rn[up]; _, _, Ll, _ = rn[lo]
            a_, b_ = Rm(idx[up]), Rm(idx[lo])
            if a_ == b_:
                continue
            ln = abs(st.z_center_um[Ll] - st.z_center_um[Lu])
            pad = ex["padstacks"].get(ps, {}); drill = pad.get("drill_um") or 40.0
            key = (ps, Lu, Ll)
            if key not in rc:
                rc[key] = estimate_via_segment_rl(length_um=ln, drill_diameter_um=drill, padstack_material=pad.get("material"),
                                                  start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"]).resistance_ohm
            sd = min(max(float(gtrees[Lu].query([xu, yu])[0]) if Lu in gtrees else 1000.0, drill), 1000.0)
            va.append(a_); vb.append(b_); vR.append(rc[key]); vL.append(MU0 / (2 * math.pi) * ln * 1e-6 * math.log(sd / (drill / 2)))
        nvia = len(va)
        for a_, b_, r_ in self._pad_links(rn):
            a2, b2 = Rm(idx[a_]), Rm(idx[b_])
            if a2 != b2:
                va.append(a2); vb.append(b2); vR.append(r_); vL.append(1e-15)
        self.vias = [np.array(x) for x in (va, vb, vR, vL)]
        self.info.update(rail_vias=nvia, rail_pad_links=len(va) - nvia, off_plane_traces=len(ta))
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
        sheet_layers = GND_SHEETS + ["Signal$TOP"]
        for k, (s_, e_, w) in enumerate(g["traces"]):
            if gn[s_][2] == gn[e_][2] and gn[s_][2] in sheet_layers:
                L = gn[s_][2]; tbl[L].append((gn[s_][0], gn[s_][1], gn[e_][0], gn[e_][1], w or lwd.get(L, 25.0))); drawn.add(k)
        for L in sheet_layers:
            geom = self.shapes.get(L, {}).get("DGND")
            tr = tbl[L] + pad_traces(gn, ex["padstacks"], L)
            if L == "Signal$TOP":
                # TOP DGND only where the rail TOP sheet is (DUT + decap area)
                b0 = self.sheets["Signal$TOP"].blocks[0]
                bbox = (b0["x0"], b0["y0"], b0["x0"] + b0["nx"] * b0["h"], b0["y0"] + b0["ny"] * b0["h"])
                sh = Sheet("GND:" + L, geom, tr, self.top_h, self.sub[2], None, None, None, bbox, n, self.log)
            else:
                sh = Sheet("GND:" + L, geom, tr, self.gnd_h, int(round(self.gnd_h / 10)), self.gnd_fh, int(round(self.gnd_fh / 5)), self.fine_box, win, n, self.log)
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
        for up, lo, ps in g["vias"]:
            Lu = gn[up][2]; Ll = gn[lo][2]
            ln = abs(st.z_center_um[Ll] - st.z_center_um[Lu])
            pad = ex["padstacks"].get(ps, {}); drill = pad.get("drill_um") or 40.0
            key = (ps, Lu, Ll)
            if key not in rc:
                rc[key] = estimate_via_segment_rl(length_um=ln, drill_diameter_um=drill, padstack_material=pad.get("material"),
                                                  start_layer=Lu, end_layer=Ll, stackup_layers=ex["stackup_layers_obj"]).resistance_ohm
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
        rsq_top = 1.0 / (st.row("Signal$TOP")["conductivity"] * st.row("Signal$TOP")["thickness_um"] * 1e-6)
        a = [gidx[x] for x, y, r in red] + ref_links
        b = [gidx[y] for x, y, r in red] + [-1] * len(ref_links)
        rr = [r for x, y, r in red] + [0.5 * rsq_top] * len(ref_links)
        self.gnd_r = [np.array(a, np.int64), np.array(b, np.int64), np.array(rr)]
        self.info.update(gnd_nodes=len(gn), gnd_traces_drawn=len(drawn), gnd_elements_reduced=len(red), gnd_ref_links=len(ref_links),
                         gnd_sheet_layers=sheet_layers)
        return n

    # ------------------------------------------------------------------
    def edge_z(self, sh, f, rail=True):
        """Edge impedance array for a sheet."""
        a, b, ell, wid, G = sh.edges
        zs = complex(copper_surface_impedance(f, sh.sigma, sh.t))
        R = zs * (ell / wid) / G
        if not rail:
            return R, np.zeros(len(a))
        w = 2 * math.pi * f
        weff = G * wid
        d = sh.d_edge
        if self.fringe:
            use = weff < self.fringe_wd * d
            weff = np.where(use, np.minimum(weff + 2 * d, wid), weff)
        XL = w * MU0 * d * 1e-6 * ell / weff
        return R, XL

    def assemble(self, f):
        w = 2 * math.pi * f
        rows, cols, vals = [], [], []
        N = self.N

        def st_(a, b, y):
            a = self.map(a); b = self.map(b)
            a = np.where(a < 0, N, a); b = np.where(b < 0, N, b)
            y = np.broadcast_to(y, a.shape)
            rows.extend([a, b, a, b]); cols.extend([a, b, b, a]); vals.extend([y, y, -y, -y])
        diag_rows, diag_vals = [], []
        for sh in self.sheets.values():
            R, XL = self.edge_z(sh, f)
            st_(sh.edges[0], sh.edges[1], 1.0 / (R + 1j * XL))
            y = (w * sh.c_cell * sh.tand_cell + 1j * w * sh.c_cell) * sh.cell_area
            diag_rows.append(self.map(sh.cells)); diag_vals.append(y)
        for sh in self.gsheets.values():
            R, _ = self.edge_z(sh, f, rail=False)
            st_(sh.edges[0], sh.edges[1], 1.0 / R)
        a, b, sq, sig, t, Lt = self.traces
        if len(a):
            zst = np.array([complex(copper_surface_impedance(f, s_, t_)) for s_, t_ in zip(sig, t)])
            st_(a, b, 1.0 / (zst * sq + 1j * w * Lt))
        a, b, Rv, Lv = self.vias
        st_(a, b, 1.0 / (Rv + 1j * w * Lv))
        a, b, Rg = self.gnd_r
        if len(a):
            st_(a, b, (1.0 / Rg).astype(complex))
        ys = {mid: 1.0 / self.ex["models"][mid].impedance([f])[0] for mid in {d[2] for d in self.dec}}
        st_(np.array([d[0] for d in self.dec]), np.array([d[1] for d in self.dec]), np.array([ys[d[2]] for d in self.dec]))
        R_ = np.concatenate([np.ravel(x) for x in rows] + [np.ravel(x) for x in diag_rows])
        C_ = np.concatenate([np.ravel(x) for x in cols] + [np.ravel(x) for x in diag_rows])
        V_ = np.concatenate([np.ravel(x) for x in vals] + [np.ravel(x) for x in diag_vals])
        ok = (R_ >= 0) & (C_ >= 0) & (R_ < N) & (C_ < N)
        return sparse.coo_matrix((V_[ok], (R_[ok], C_[ok])), shape=(N, N)).tocsc()

    def solve(self, freqs, verbose=True, want_v=False):
        Z, stats, Vs = [], [], []
        for f in freqs:
            t0 = time.time(); Y = self.assemble(f); t1 = time.time()
            lu = splu(Y, permc_spec="COLAMD"); t2 = time.time()
            rhs = np.zeros(self.N, complex); rhs[self.P] = 1.0
            V = lu.solve(rhs); Z.append(V[self.P])
            stats.append(dict(f=float(f), assemble_s=t1 - t0, factor_s=t2 - t1, nnz_LU=int(lu.nnz),
                              rss_MB=peak_rss_mb()))
            if want_v:
                Vs.append(V)
            if verbose:
                self.log(f"  f={f:11.4e} Z={Z[-1].real:+.4e}{Z[-1].imag:+.4e}j asm {t1-t0:.1f}s fact {t2-t1:.1f}s nnzLU {lu.nnz/1e6:.1f}M")
            del lu
        return (np.array(Z), stats, Vs) if want_v else (np.array(Z), stats)

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
        z = np.array([complex(copper_surface_impedance(f, s_, t_)) for s_, t_ in zip(sig, t)]) * sq + 1j * w * Lt
        i = (gv(a) - gv(b)) / z; out["rail_traces"] = np.sum(np.abs(i) ** 2 * z)
        a, b, Rv, Lv = self.vias
        z = Rv + 1j * w * Lv; i = (gv(a) - gv(b)) / z
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
