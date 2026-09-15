"""EXP-1 plane-pair + circuit hybrid model (sparse nodal admittance).

Unknowns = node voltages w.r.t. an ideal reference (the GND return).
Elements
  * plane cells (uniform grid h per rail plane layer, ordered PowerSI booleans
    evaluated at cell centres) ; 4-neighbour edges carry
        Z_edge = Zs_rail(f) + Zs_gnd(f) + j w mu0 d          (one square)
    (M-FDM/TMM unit cell, both plates' surface impedance, finite-thickness
    copper surface impedance reused from mfdm.copper_surface_impedance,
    src/spd_decap_pi/_core/solver/mfdm.py:782)
    and each cell has shunt  Y = (j w + w tan d) eps0 eps_r(f) h^2 / d_k  to each
    adjacent GND conductor k.
  * SPD Traces (rail): Z = Zs_rail * len / w + j w mu0 d len/(w + 2d).
  * SPD Vias (rail) : R from via_model.estimate_via_segment_rl
    (src/spd_decap_pi/_core/via_model.py:132; solid-filled microvia or plated
    barrel classification), multiplied by (1 + gnd_via_r_factor) for the
    symmetric GND via return; L = mu0/(2 pi) len ln(s/r) with s = distance to
    the nearest DGND node on the via's upper layer (coax-like return).
  * decaps: Y_dec(f) = 1/Z(f) from the SPD PartialCkt SPICE ladders via
    PassiveSubcircuitModel.impedance (src/spd_decap_pi/_core/models/spice.py:185),
    connected rail pin node -> reference.
  * port: all PositiveTerminal nodes shorted into one supernode, 1 A injected,
    NegativeTerminal = ideal reference.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from matplotlib.path import Path as MplPath
from scipy import sparse
from scipy.sparse import csgraph
from scipy.sparse.linalg import splu
from scipy.spatial import cKDTree

from spd_decap_pi._core.solver.mfdm import copper_surface_impedance, MU_0_H_PER_M
from spd_decap_pi._core.via_model import estimate_via_segment_rl

EPS0 = 8.8541878128e-12
MU0 = MU_0_H_PER_M


# ----------------------------------------------------------------------------
# stack-up helpers
# ----------------------------------------------------------------------------
@dataclass
class Stack:
    rows: list  # dicts from extract
    cond_names: list = field(default_factory=list)
    z_center_um: dict = field(default_factory=dict)

    def __post_init__(self):
        z = 0.0
        for r in self.rows:
            if r["conductivity"] is not None:
                self.cond_names.append(r["name"])
                self.z_center_um[r["name"]] = z + r["thickness_um"] / 2.0
            z += r["thickness_um"]

    def row(self, name):
        for r in self.rows:
            if r["name"] == name:
                return r
        raise KeyError(name)

    def idx(self, name):
        return [r["name"] for r in self.rows].index(name)

    @staticmethod
    def is_gnd(name):
        return "(DGND)" in name

    def neighbours(self, name):
        """[(side, conductor_name, dielectric_row, gap_um)] for adjacent conductors."""
        i = self.idx(name)
        out = []
        for side, step in (("above", -1), ("below", 1)):
            j = i + step
            diel = []
            while 0 <= j < len(self.rows) and self.rows[j]["conductivity"] is None:
                diel.append(self.rows[j]); j += step
            if 0 <= j < len(self.rows) and diel:
                out.append((side, self.rows[j]["name"], diel[0], sum(r["thickness_um"] for r in diel)))
        return out

    def nearest_gnd(self, name):
        """(gnd layer, gap_um) nearest GND-named conductor in each direction; gap
        counts dielectric AND intervening conductor thickness."""
        i = self.idx(name)
        res = []
        for step in (-1, 1):
            j = i + step; gap = 0.0; diel = None
            while 0 <= j < len(self.rows):
                r = self.rows[j]
                if r["conductivity"] is not None and self.is_gnd(r["name"]):
                    res.append((r["name"], gap, diel)); break
                if r["conductivity"] is None and diel is None:
                    diel = r
                gap += r["thickness_um"]; j += step
        return res


def eps_tand(row, f):
    props = row.get("props") or []
    if props:
        fr = np.array([p[0] for p in props]); dk = np.array([p[1] for p in props]); df = np.array([p[2] for p in props])
        lf = np.log10(np.clip(f, fr.min(), fr.max()))
        return float(np.interp(lf, np.log10(fr), dk)), float(np.interp(lf, np.log10(fr), df))
    return float(row["dk"] or 4.0), float(row["df"] or 0.0)


# ----------------------------------------------------------------------------
# rasterisation
# ----------------------------------------------------------------------------
def geom_bbox(geom):
    allp = np.vstack([p.reshape(-1, 2) for p in geom["pos_polys"]] +
                     [np.array([[c[0] - c[2], c[1] - c[2]], [c[0] + c[2], c[1] + c[2]]]) for c in geom["pos_circles"]])
    return allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max()


def rasterize(geom, h, window=None):
    """Evaluate the ordered PowerSI boolean artwork at cell centres of a uniform
    grid. ``window`` = (x0, y0, x1, y1) grid-aligned extent; default = bbox."""
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
        X, Y = np.meshgrid(xc[i0:i1], yc[j0:j1])
        if kind.endswith("polygon"):
            inside = MplPath(pts).contains_points(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
        else:
            inside = (X - item[0]) ** 2 + (Y - item[1]) ** 2 <= item[2] ** 2
        if kind.startswith("positive"):
            mask[j0:j1, i0:i1] |= inside
        else:
            mask[j0:j1, i0:i1] &= ~inside
    return dict(x0=x0, y0=y0, nx=nx, ny=ny, h=h, mask=mask)


def _cell_of(blk, x, y):
    i = int(math.floor((x - blk["x0"]) / blk["h"])); j = int(math.floor((y - blk["y0"]) / blk["h"]))
    if 0 <= i < blk["nx"] and 0 <= j < blk["ny"]:
        return j, i
    return None


# ----------------------------------------------------------------------------
# model assembly
# ----------------------------------------------------------------------------
@dataclass
class Model:
    n: int
    port_node: int
    info: dict
    # frequency-independent element lists
    plane_edges: dict       # layer -> (a, b) arrays
    plane_cells: dict       # layer -> node index array
    plane_params: dict      # layer -> dict(sigma,t, gnd:[(sigma,t)], d_um, caps:[(row, gap_um)])
    rl_edges: tuple         # (a, b, R, L)  (trace: R part handled by Zs scale)
    trace_edges: tuple      # (a, b, len/w (squares), sigma, t, L)
    decap_nodes: np.ndarray
    decap_models: list
    options: dict


def build_model(ex: dict, h_um: float, top_h_um: float | None = None, gnd_via_r_factor: float = 1.0,
                include_gnd_sheet_r: bool = True, verbose: bool = True, drop_decaps: bool = False,
                layers_include: list | None = None, fine_box=None, fine_h_um: float | None = None,
                cell_ref=None) -> Model:
    """cell_ref (EXP-1b): optional callable (layer, block) -> dict(d_eff=(ny,nx) um,
    c_um2=(ny,nx) F/um^2, tand=(ny,nx)) giving a per-cell two-sided reference.
    When None the original single-reference behaviour (variants A/B) is kept."""
    t0 = time.time()
    st = Stack(ex["stackup"])
    rail_nodes = ex["rail_nodes"]
    info = {"h_um": h_um}
    idx_of = {}
    n = 0
    plane_cells, plane_edges, plane_params = {}, {}, {}
    rasters = {}
    for g in ex["rail_geoms"]:
        L = g["layer"]
        if layers_include is not None and L not in layers_include:
            continue
        hh = top_h_um if (top_h_um and L == "Signal$TOP") else h_um
        blocks = []
        coarse = rasterize(g, hh)
        fine = None
        if fine_box is not None and fine_h_um and L != "Signal$TOP" and fine_h_um < hh:
            bx = geom_bbox(g)
            fx0 = max(math.floor(fine_box[0] / hh) * hh, coarse["x0"]); fy0 = max(math.floor(fine_box[1] / hh) * hh, coarse["y0"])
            fx1 = min(math.ceil(fine_box[2] / hh) * hh, coarse["x0"] + coarse["nx"] * hh)
            fy1 = min(math.ceil(fine_box[3] / hh) * hh, coarse["y0"] + coarse["ny"] * hh)
            if fx1 > fx0 and fy1 > fy0 and not (bx[2] < fx0 or bx[0] > fx1 or bx[3] < fy0 or bx[1] > fy1):
                fine = rasterize(g, fine_h_um, window=(fx0, fy0, fx1, fy1))
                ci0 = int(round((fx0 - coarse["x0"]) / hh)); ci1 = int(round((fx1 - coarse["x0"]) / hh))
                cj0 = int(round((fy0 - coarse["y0"]) / hh)); cj1 = int(round((fy1 - coarse["y0"]) / hh))
                coarse["mask"][cj0:cj1, ci0:ci1] = False
                coarse["hole"] = (ci0, ci1, cj0, cj1)
        blocks.append(coarse)
        if fine is not None:
            blocks.append(fine)
        ea, eb, esq = [], [], []
        cells, areas = [], []
        cd, cc, ct = [], [], []
        cnt_tot = 0
        for blk in blocks:
            mask = blk["mask"]
            ids = -np.ones(mask.shape, np.int64)
            cnt = int(mask.sum())
            ids[mask] = np.arange(n, n + cnt)
            n += cnt; cnt_tot += cnt
            blk["ids"] = ids
            hor = mask[:, :-1] & mask[:, 1:]
            ver = mask[:-1, :] & mask[1:, :]
            ea += [ids[:, :-1][hor], ids[:-1, :][ver]]; eb += [ids[:, 1:][hor], ids[1:, :][ver]]
            esq.append(np.ones(int(hor.sum() + ver.sum())))
            cells.append(ids[mask]); areas.append(np.full(cnt, blk["h"] ** 2))
            if cell_ref is not None:
                ref_ = cell_ref(L, blk)
                blk["d_eff"] = ref_["d_eff"]
                cd.append(ref_["d_eff"][mask]); cc.append(ref_["c_um2"][mask]); ct.append(ref_["tand"][mask])
        if fine is not None:
            # interface edges fine boundary cell <-> coarse neighbour cell
            hf, hc = fine["h"], coarse["h"]
            fm = fine["mask"]; jj, ii = np.nonzero(fm)
            ia, ib = [], []
            for dj, di, edge in ((0, -1, ii == 0), (0, 1, ii == fine["nx"] - 1), (-1, 0, jj == 0), (1, 0, jj == fine["ny"] - 1)):
                for j, i in zip(jj[edge], ii[edge]):
                    x = fine["x0"] + (i + 0.5) * hf + di * (hf / 2 + hc / 2)
                    y = fine["y0"] + (j + 0.5) * hf + dj * (hf / 2 + hc / 2)
                    c = _cell_of(coarse, x, y)
                    if c is not None and coarse["mask"][c]:
                        ia.append(fine["ids"][j, i]); ib.append(coarse["ids"][c])
            ea.append(np.array(ia, np.int64)); eb.append(np.array(ib, np.int64))
            esq.append(np.full(len(ia), (hf / 2 + hc / 2) / hf))
        rasters[L] = blocks
        plane_edges[L] = (np.concatenate(ea), np.concatenate(eb), np.concatenate(esq))
        plane_cells[L] = (np.concatenate(cells), np.concatenate(areas))
        if cell_ref is not None:
            cid = plane_cells[L][0]; base = cid.min()
            dloc = np.zeros(cid.max() - base + 1); dloc[cid - base] = np.concatenate(cd)
            A_, B_, S_ = plane_edges[L]
            plane_edges[L] = (A_, B_, S_, 0.5 * (dloc[A_ - base] + dloc[B_ - base]))
            plane_cells[L] = (cid, plane_cells[L][1], np.concatenate(cc), np.concatenate(ct))
        row = st.row(L)
        ng = st.nearest_gnd(L)
        nb = st.neighbours(L)
        adj_gnd = [(c, diel, gap) for side, c, diel, gap in nb if st.is_gnd(c)]
        if adj_gnd:
            ds = [gap for _, _, gap in adj_gnd]
            d_um = 1.0 / sum(1.0 / x for x in ds)
            gnd_rows = [st.row(c) for c, _, _ in adj_gnd]
            gnd_w = [(1.0 / x) / sum(1.0 / y for y in ds) for x in ds]  # current share
        else:
            c, gap, diel = min(ng, key=lambda t: t[1])
            d_um = gap; gnd_rows = [st.row(c)]; gnd_w = [1.0]
        caps = [(diel, gap) for c, diel, gap in adj_gnd]
        plane_params[L] = dict(sigma=row["conductivity"], t=row["thickness_um"] * 1e-6, d_um=d_um,
                               gnd=[(r["conductivity"], r["thickness_um"] * 1e-6, w, r["name"]) for r, w in zip(gnd_rows, gnd_w)],
                               caps=caps, h=hh, fine_h=fine["h"] if fine is not None else None, cells=cnt_tot,
                               edges=int(len(plane_edges[L][0])))
        if verbose:
            print(f"[model] {L}: h={hh}um{' + fine '+str(fine_h_um)+'um box' if fine is not None else ''} cells {cnt_tot} edges {plane_params[L]['edges']} "
                  f"d={d_um:.1f}um ref={[r['name'] for r in gnd_rows]} caps={[(c[0]['name'], c[1]) for c in caps]}", flush=True)
    info["plane_unknowns"] = n

    # --- map SPD nodes ------------------------------------------------------
    snapped = 0; standalone = 0
    node_h = {}
    trees = {}
    for L, blocks in rasters.items():
        for bi, blk in enumerate(blocks):
            m = blk["mask"]; jj, ii = np.nonzero(m)
            cx = blk["x0"] + (ii + 0.5) * blk["h"]; cy = blk["y0"] + (jj + 0.5) * blk["h"]
            trees[(L, bi)] = (cKDTree(np.column_stack([cx, cy])) if len(cx) else None, blk["ids"][jj, ii])
    for nid, (x, y, lay, ps) in rail_nodes.items():
        if lay in rasters:
            blocks = rasters[lay]
            # choose block: fine if inside its window
            bi = 0
            if len(blocks) > 1:
                fb = blocks[1]
                if fb["x0"] <= x < fb["x0"] + fb["nx"] * fb["h"] and fb["y0"] <= y < fb["y0"] + fb["ny"] * fb["h"]:
                    bi = 1
            blk = blocks[bi]
            tree, idsarr = trees[(lay, bi)]
            if tree is not None:
                dist, k = tree.query([x, y])
                if dist <= 0.75 * blk["h"] * math.sqrt(2) + 1e-9:  # containing or touching cell
                    idx_of[nid] = int(idsarr[k]); snapped += 1; node_h[nid] = blk["h"]
                    continue
        idx_of[nid] = n; n += 1; standalone += 1
    info.update(nodes_snapped_to_plane=snapped, standalone_nodes=standalone)

    # --- port supernode -----------------------------------------------------
    pos = [idx_of[x] for x in ex["port_pos_nodes"] if x in idx_of]
    info["port_pos_found"] = len(pos)
    info["port_pos_total"] = len(ex["port_pos_nodes"])
    port = n; n += 1
    remap = {p: port for p in set(pos)}

    def R(i):
        return remap.get(i, i)

    # --- traces -------------------------------------------------------------
    layer_width_default = ex.get("layer_default_width_um", {})
    ta, tb, tsq, tsig, tt, tL = [], [], [], [], [], []
    skipped_inside = 0; no_width = 0
    for s, e, w in ex["rail_traces"]:
        if s not in rail_nodes or e not in rail_nodes:
            continue
        xs, ys, Ls_, _ = rail_nodes[s]; xe, ye, Le_, _ = rail_nodes[e]
        a_, b_ = R(idx_of[s]), R(idx_of[e])
        if a_ == b_:
            skipped_inside += 1; continue
        ln = math.hypot(xe - xs, ye - ys)
        if s in node_h and e in node_h and ln <= 1.5 * max(node_h[s], node_h[e]):
            skipped_inside += 1; continue  # both ends on the plane in adjacent cells: plane already carries it
        if w is None:
            w = layer_width_default.get(Ls_, 25.0); no_width += 1
        ln = max(ln, 1.0)
        row = st.row(Ls_)
        ng = st.nearest_gnd(Ls_)
        d = min(g[1] for g in ng) if ng else 30.0
        ta.append(a_); tb.append(b_); tsq.append(ln / w); tsig.append(row["conductivity"]); tt.append(row["thickness_um"] * 1e-6)
        tL.append(MU0 * d * 1e-6 * ln / (w + 2 * d))
    info.update(traces_used=len(ta), traces_skipped_on_plane=skipped_inside, traces_default_width=no_width)

    # --- vias ---------------------------------------------------------------
    gxy = ex["gnd_xy_by_layer"]
    gtrees = {L: cKDTree(v) for L, v in gxy.items() if len(v)}
    layers_obj = ex["stackup_layers_obj"]
    va, vb, vR, vL = [], [], [], []
    svals = []
    rcache = {}
    for up, lo, ps in ex["rail_vias"]:
        if up not in rail_nodes or lo not in rail_nodes:
            continue
        xu, yu, Lu, _ = rail_nodes[up]; xl, yl, Ll, _ = rail_nodes[lo]
        a_, b_ = R(idx_of[up]), R(idx_of[lo])
        if a_ == b_:
            continue
        ln = abs(st.z_center_um[Ll] - st.z_center_um[Lu])
        pad = ex["padstacks"].get(ps, {})
        drill = pad.get("drill_um") or 40.0
        key = (ps, Lu, Ll)
        if key not in rcache:
            rcache[key] = estimate_via_segment_rl(length_um=ln, drill_diameter_um=drill, padstack_material=pad.get("material"),
                                                  start_layer=Lu, end_layer=Ll, stackup_layers=layers_obj).resistance_ohm
        r_via = drill / 2.0
        s = 1000.0
        if Lu in gtrees:
            dd, _ = gtrees[Lu].query([xu, yu], k=1)
            s = float(dd)
        s = min(max(s, 2.0 * r_via), 1000.0)
        svals.append(s)
        va.append(a_); vb.append(b_); vR.append(rcache[key] * (1.0 + gnd_via_r_factor))
        vL.append(MU0 / (2 * math.pi) * ln * 1e-6 * math.log(s / r_via))
    info.update(vias_used=len(va), via_return_distance_um_median=float(np.median(svals)) if svals else None,
                via_return_distance_um_p10_p90=[float(np.percentile(svals, 10)), float(np.percentile(svals, 90))] if svals else None)

    # --- pad contacts: component/bump pads (PadStack on the node) short every
    # same-net node on that layer lying inside the pad footprint (PowerSI
    # connects overlapping same-net metal; e.g. 1608 decap pads sit on via
    # arrays with no Trace record). Link = half a square of pad copper, L = 0.
    by_layer = {}
    for nid, (x, y, lay, ps) in rail_nodes.items():
        by_layer.setdefault(lay, []).append(nid)
    ktrees = {L: (cKDTree(np.array([(rail_nodes[i][0], rail_nodes[i][1]) for i in ids])), ids) for L, ids in by_layer.items()}
    pad_links = 0
    for nid, (x, y, lay, ps) in rail_nodes.items():
        if not ps or ps not in ex["padstacks"]:
            continue
        pd = ex["padstacks"][ps]
        if lay not in pd["layers"] or not pd["pad_w"]:
            continue
        pw, ph = pd["pad_w"], pd["pad_h"] or pd["pad_w"]
        tree, ids = ktrees[lay]
        row = st.row(lay)
        rlink = 0.5 / (row["conductivity"] * row["thickness_um"] * 1e-6)
        for k in tree.query_ball_point([x, y], r=math.hypot(pw, ph) / 2 + 1.0):
            other = ids[k]
            if other == nid:
                continue
            dx = abs(rail_nodes[other][0] - x); dy = abs(rail_nodes[other][1] - y)
            if not ((dx <= pw / 2 + 1 and dy <= ph / 2 + 1) or (dx <= ph / 2 + 1 and dy <= pw / 2 + 1)):
                continue
            a_, b_ = R(idx_of[nid]), R(idx_of[other])
            if a_ == b_:
                continue
            va.append(a_); vb.append(b_); vR.append(rlink); vL.append(1e-15); pad_links += 1
    info["pad_contact_links"] = pad_links

    # --- decaps -------------------------------------------------------------
    dn, dm = [], []
    if not drop_decaps:
        for dc in ex["decaps"]:
            if dc["rail_node"] in idx_of:
                dn.append(R(idx_of[dc["rail_node"]])); dm.append(dc["model_id"])
    # --- connectivity pruning -------------------------------------------------
    Rmap = np.arange(n, dtype=np.int64)
    for k_, v_ in remap.items():
        Rmap[k_] = v_
    A = []; B = []
    for L, et in plane_edges.items():
        A.append(Rmap[et[0]]); B.append(Rmap[et[1]])
    A.append(np.array(ta, np.int64)); B.append(np.array(tb, np.int64))
    A.append(np.array(va, np.int64)); B.append(np.array(vb, np.int64))
    Aall = np.concatenate(A); Ball = np.concatenate(B)
    G = sparse.coo_matrix((np.ones(len(Aall)), (Aall, Ball)), shape=(n, n))
    ncomp, lab = csgraph.connected_components(G, directed=False)
    keep = lab == lab[port]
    newidx = -np.ones(n, np.int64); newidx[keep] = np.arange(int(keep.sum()))
    info["nodes_before_prune"] = n
    info["nodes_after_prune"] = int(keep.sum())
    info["decaps_connected"] = int(sum(keep[x] for x in dn))
    info["decaps_total"] = len(ex["decaps"])
    info["build_seconds"] = time.time() - t0
    for L in plane_cells:
        c = plane_cells[L][0]
        info.setdefault("plane_cells_connected", {})[L] = int(keep[Rmap[c]].sum()) if len(c) else 0

    def mapab(a, b):
        a = newidx[a]; b = newidx[b]; ok = (a >= 0) & (b >= 0)
        return a, b, ok

    pe = {}
    for L, et in plane_edges.items():
        a3, b3, ok = mapab(Rmap[et[0]], Rmap[et[1]])
        ok &= a3 != b3
        pe[L] = (a3[ok], b3[ok]) + tuple(x[ok] for x in et[2:])
    pc = {}
    for L, ct_ in plane_cells.items():
        c2 = newidx[Rmap[ct_[0]]]
        pc[L] = (c2[c2 >= 0],) + tuple(x[c2 >= 0] for x in ct_[1:])
    ta_, tb_, okt = mapab(np.array(ta, np.int64), np.array(tb, np.int64))
    va_, vb_, okv = mapab(np.array(va, np.int64), np.array(vb, np.int64))
    dn_ = newidx[np.array(dn, np.int64)] if dn else np.array([], np.int64)
    okd = dn_ >= 0
    model = Model(
        n=int(keep.sum()), port_node=int(newidx[port]), info=info,
        plane_edges=pe, plane_cells=pc, plane_params=plane_params,
        rl_edges=(va_[okv], vb_[okv], np.array(vR)[okv], np.array(vL)[okv]),
        trace_edges=(ta_[okt], tb_[okt], np.array(tsq)[okt], np.array(tsig)[okt], np.array(tt)[okt], np.array(tL)[okt]),
        decap_nodes=dn_[okd], decap_models=[m for m, o in zip(dm, okd) if o],
        options=dict(gnd_via_r_factor=gnd_via_r_factor, include_gnd_sheet_r=include_gnd_sheet_r, drop_decaps=drop_decaps),
    )
    model.info["rasters"] = {L: [dict(nx=r["nx"], ny=r["ny"], h=r["h"], x0=r["x0"], y0=r["y0"]) for r in blks] for L, blks in rasters.items()}
    if verbose:
        print(f"[model] unknowns {model.n} (before prune {n}); decaps connected {info['decaps_connected']}/{len(ex['decaps'])}; "
              f"port +nodes {len(pos)}/{len(ex['port_pos_nodes'])}; build {info['build_seconds']:.1f}s", flush=True)
        print("[model] info", {k: v for k, v in info.items() if k not in ('rasters',)}, flush=True)
    return model


def p_d(model, L):
    return model.plane_params[L]["d_um"]


def decap_admittances(ex, model, freqs):
    ys = {}
    for mid in set(model.decap_models):
        ys[mid] = 1.0 / ex["models"][mid].impedance(freqs)
    return ys


def solve(model: Model, ex: dict, freqs, verbose=True, want_voltages=False):
    freqs = np.asarray(freqs, float)
    ydec = decap_admittances(ex, model, freqs)
    Z = np.zeros(len(freqs), complex)
    stats = []
    st = Stack(ex["stackup"])
    Vs = []
    for fi, f in enumerate(freqs):
        t0 = time.time()
        w = 2 * math.pi * f
        rows = []; cols = []; vals = []

        def stamp(a, b, y):
            rows.extend([a, b, a, b]); cols.extend([a, b, b, a]); vals.extend([y, y, -y, -y])

        diag = np.zeros(model.n, complex)
        for L, et in model.plane_edges.items():
            a, b, sq = et[:3]
            d_e = et[3] if len(et) > 3 else p_d(model, L)
            p = model.plane_params[L]
            zs = complex(copper_surface_impedance(f, p["sigma"], p["t"]))
            if model.options["include_gnd_sheet_r"]:
                # parallel GND returns carry current shares w_k -> sum w_k^2 Zs_k
                zs += sum(wk * wk * complex(copper_surface_impedance(f, sg, tg)) for sg, tg, wk, _ in p["gnd"])
            ze = zs + 1j * w * MU0 * d_e * 1e-6
            y = (1.0 / ze) / sq
            rows.append(a); cols.append(a); vals.append(y)
            rows.append(b); cols.append(b); vals.append(y)
            rows.append(a); cols.append(b); vals.append(-y)
            rows.append(b); cols.append(a); vals.append(-y)
            if len(model.plane_cells[L]) > 2:
                cidx, carea, c_um2, tdc = model.plane_cells[L]
                np.add.at(diag, cidx, (w * c_um2 * tdc + 1j * w * c_um2) * carea)
            else:
                ycell = 0j
                for diel, gap in p["caps"]:
                    er, td = eps_tand(diel, f)
                    c = EPS0 * er * 1e-12 / (gap * 1e-6)   # per um^2
                    ycell += w * c * td + 1j * w * c
                cidx, carea = model.plane_cells[L]
                np.add.at(diag, cidx, ycell * carea)
        a, b, sq, sig, t, Lt = model.trace_edges
        if len(a):
            zs_t = np.array([complex(copper_surface_impedance(f, s_, t_)) for s_, t_ in zip(sig, t)]) if len(set(zip(sig, t))) > 1 else \
                np.full(len(a), complex(copper_surface_impedance(f, sig[0], t[0])))
            y = 1.0 / (zs_t * sq + 1j * w * Lt)
            rows += [a, b, a, b]; cols += [a, b, b, a]; vals += [y, y, -y, -y]
        a, b, Rv, Lv = model.rl_edges
        if len(a):
            y = 1.0 / (Rv + 1j * w * Lv)
            rows += [a, b, a, b]; cols += [a, b, b, a]; vals += [y, y, -y, -y]
        for node, mid in zip(model.decap_nodes, model.decap_models):
            diag[node] += ydec[mid][fi]
        rows.append(np.arange(model.n)); cols.append(np.arange(model.n)); vals.append(diag)
        Y = sparse.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(model.n, model.n)).tocsc()
        t1 = time.time()
        lu = splu(Y, permc_spec="COLAMD")
        t2 = time.time()
        rhs = np.zeros(model.n, complex); rhs[model.port_node] = 1.0
        V = lu.solve(rhs)
        Z[fi] = V[model.port_node]
        if want_voltages:
            Vs.append(V)
        nnz_lu = lu.L.nnz + lu.U.nnz
        stats.append(dict(f=f, assemble_s=t1 - t0, factor_s=t2 - t1, solve_s=time.time() - t2, nnz_Y=Y.nnz, nnz_LU=nnz_lu,
                          lu_mem_MB=nnz_lu * 16 / 1e6 * 1.5))
        if verbose:
            print(f"  f={f:11.4e} Z={Z[fi].real:+.4e}{Z[fi].imag:+.4e}j  asm {t1-t0:.2f}s fact {t2-t1:.2f}s nnzLU {nnz_lu/1e6:.1f}M", flush=True)
        del lu
    return (Z, stats, Vs) if want_voltages else (Z, stats)


def loss_breakdown(model: Model, ex: dict, f: float, V: np.ndarray, gnd_via_r_factor: float | None = None) -> dict:
    """Split Re Z (= total complex power for 1 A, P = sum V_e conj(I_e)) and
    Im Z / w by element class. Returns mOhm and pH contributions."""
    w = 2 * math.pi * f
    out = {}

    def add(key, dv, z):
        i = dv / z
        s = np.sum(np.abs(i) ** 2 * z)  # complex power into element
        out[key] = out.get(key, 0) + s

    for L, et in model.plane_edges.items():
        a, b, sq = et[:3]
        d_e = et[3] if len(et) > 3 else p_d(model, L)
        p = model.plane_params[L]
        zr = complex(copper_surface_impedance(f, p["sigma"], p["t"]))
        zg = sum(wk * wk * complex(copper_surface_impedance(f, sg, tg)) for sg, tg, wk, _ in p["gnd"]) if model.options["include_gnd_sheet_r"] else 0
        ze = zr + zg + 1j * w * MU0 * d_e * 1e-6
        i = (V[a] - V[b]) / (ze * sq)
        i2 = np.abs(i) ** 2 * sq
        out[f"plane_rail_R:{L}"] = np.sum(i2) * zr.real
        out[f"plane_gnd_R:{L}"] = np.sum(i2) * complex(zg).real
        out[f"plane_L:{L}"] = 1j * np.sum(i2 * (w * MU0 * d_e * 1e-6 + zr.imag + complex(zg).imag))
    a, b, sq, sig, t, Lt = model.trace_edges
    zs_t = np.array([complex(copper_surface_impedance(f, s_, t_)) for s_, t_ in zip(sig, t)])
    z = zs_t * sq + 1j * w * Lt
    i = (V[a] - V[b]) / z
    out["trace_R"] = np.sum(np.abs(i) ** 2 * z.real)
    out["trace_L"] = 1j * np.sum(np.abs(i) ** 2 * z.imag)
    a, b, Rv, Lv = model.rl_edges
    z = Rv + 1j * w * Lv
    i = (V[a] - V[b]) / z
    padlink = Lv < 1e-14
    out["via_R"] = np.sum(np.abs(i[~padlink]) ** 2 * Rv[~padlink])
    out["via_L"] = 1j * np.sum(np.abs(i[~padlink]) ** 2 * w * Lv[~padlink])
    out["padlink_R"] = np.sum(np.abs(i[padlink]) ** 2 * Rv[padlink])
    ys = {mid: 1.0 / ex["models"][mid].impedance([f])[0] for mid in set(model.decap_models)}
    sdec = 0
    for node, mid in zip(model.decap_nodes, model.decap_models):
        ii = V[node] * ys[mid]
        sdec += np.abs(ii) ** 2 / np.conj(ys[mid]) if False else V[node] * np.conj(ii)
    out["decaps"] = sdec
    tot = sum(out.values())
    res = {k: dict(mOhm=float(np.real(v)) * 1e3, pH=float(np.imag(v)) / w * 1e12) for k, v in out.items()}
    res["_total"] = dict(mOhm=float(np.real(tot)) * 1e3, imag=float(np.imag(tot)), Zport=complex(V[model.port_node]).__repr__())
    return res
