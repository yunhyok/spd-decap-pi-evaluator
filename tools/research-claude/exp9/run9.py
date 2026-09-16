#!/usr/bin/env python
"""EXP-9: R deficit on few-decap rails (port 19 discrimination).

Model = frozen adopted model (EXP-5 ModelB = S2 + (b)) with the EXP-8 cavity-wall reference rule
(exp8/run8.py TwoSidedAny), ideal GND unless --gnd.  Decaps are removed from the nodal matrix and the
board is characterised as a (1+Nd)-port impedance matrix at each frequency (one LU per frequency,
exp3/model3.py Model3.assemble/solve reused with self.dec = []).  Decap models (+ any diagnostic series
R) are then closed analytically:  Zp = Z_PP - Z_Pd (Z_dd + diag(Zdec+Rx))^-1 Z_dP.
With Rx = 0 this must reproduce the EXP-8 result (checked).

  --tag T --port P [--freqs ladder|few] [--gnd none|s3|s3scaled] [--loops]
     writes WORK_DIR/exp9/mp_{tag}_{port}_{gnd}.npz  (freq, Zmp, labels, decap models)
     --loops: per-decap loop (port+ -> decap pad, 1 A in, 1 A out) power breakdown at 10 kHz
  --trace  : SPD/extract path audit for port 19 decaps and port pins (no solve)
DIAGNOSTIC ONLY: the +R per decap / common-R cases are hypothesis tests, not model changes.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import pickle
import sys
import time

import numpy as np
from scipy.sparse.linalg import splu

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("../exp8", "../exp5", "../exp4", "../exp3", "../exp1"):
    sys.path.insert(0, os.path.join(HERE, p))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import peak_rss_mb, work_dir, work_file  # noqa: E402
import pipeline as P5  # noqa: E402
import model3 as M3  # noqa: E402
import model as M  # noqa: E402
import run4 as R4  # noqa: E402
import run8 as R8  # noqa: E402
from spd_decap_pi._core.via_model import estimate_via_segment_rl  # noqa: E402

OUT = work_dir("exp9")


class ModelB9(P5.ModelB):
    gnd_scale9 = 1.0

    def build(self):
        P5.R4.Model4.build(self)
        rep = self.info.get("reference_search", {})
        ts = set(); frac = {}
        thr = min(self.h, 200.0)  # same majority rule as pipeline.ModelB, on the coarsest blocks of this mesh
        for L, r in rep.items():
            cells = sum(b["cells"] for b in r["blocks"] if b["h"] >= thr)
            two = sum(b["cells"] * b["two_sided"] for b in r["blocks"] if b["h"] >= thr)
            frac[L] = two / cells if cells else 0.0
            if cells and two / cells > 0.5:
                ts.add(L)
        self.two_sided_layers = ts
        self.info["two_sided_fraction_coarse"] = frac
        self.info["two_sided_layers"] = sorted(ts)
        self.mode = "b"

    def _build_gnd(self, n, gidx):
        """model3._build_gnd sizes the TOP DGND sheet from the rail TOP sheet (model3.py:392); rails without TOP
        artwork (port 19) get a 1-cell placeholder there, so TOP DGND is not meshed: DUT GND bumps stay the ideal
        reference (port - pins) and decap GND pads reach the L02.. sheets through their explicit GND vias."""
        if "Signal$TOP" in self.sheets:
            return super()._build_gnd(n, gidx)
        rn = self.ex["rail_nodes"]; P = np.array([(rn[x][0], rn[x][1]) for x in self.ex["port_pos_nodes"]])
        cx, cy = P.mean(0)

        class _Dummy:
            blocks = [dict(x0=cx, y0=cy, nx=1, ny=1, h=self.top_h)]
        self.sheets["Signal$TOP"] = _Dummy()
        try:
            return super()._build_gnd(n, gidx)
        finally:
            del self.sheets["Signal$TOP"]
            self.info["gnd_top_sheet"] = "1-cell placeholder (no rail TOP artwork)"

    def edge_z(self, sh, f, rail=True):
        R, XL = super().edge_z(sh, f, rail)
        if not rail:
            return R * self.gnd_scale9, XL
        return R, XL


ISLAND_H = {}


class SheetMesh(M3.Sheet):
    """DIAGNOSTIC mesh override: listed layers meshed uniformly at ISLAND_H[layer] (5 um sub-tiles)."""

    def __init__(self, layer, geom, traces, h, sub_c, fh, sub_f, fine_box, bbox, n0, log=print):
        if layer in ISLAND_H:
            hh = ISLAND_H[layer]
            super().__init__(layer, geom, traces, hh, int(round(hh / 5.0)), None, None, None, bbox, n0, log)
        else:
            super().__init__(layer, geom, traces, h, sub_c, fh, sub_f, fine_box, bbox, n0, log)


_ORIG_SHEET = M3.Sheet


def build(tag, port, gnd="none", nonewidth=None, mesh="std"):
    R4.patch_traces("b")
    M3.TwoSided = R8.TwoSidedAny
    ex, shapes, fine_box = P5.prepare(tag, port)
    if nonewidth is not None:  # DIAGNOSTIC: width assumed for SPD Trace records without a Width attribute
        n0 = sum(1 for t in ex["rail_traces"] if t[2] is None)
        if nonewidth == "drop":
            ex["rail_traces"] = [t for t in ex["rail_traces"] if t[2] is not None]
        else:
            ex["rail_traces"] = [(s, e, float(nonewidth) if w is None else w) for s, e, w in ex["rail_traces"]]
        print("[diag] width-less traces", n0, "->", nonewidth, flush=True)
    kw = dict(h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10, fringe=True)
    M3.Sheet = _ORIG_SHEET; ISLAND_H.clear()
    if mesh != "std":  # DIAGNOSTIC mesh variants
        kw.update(h=100.0, sub_c=10)
        if mesh == "h100_isl50":
            M3.Sheet = SheetMesh
            ISLAND_H.update({g["layer"]: 50.0 for g in ex["rail_geoms"] if g["layer"] != "Signal$TOP" and sum(len(p) for p in g["neg_polys"]) < 1 and len(g["pos_polys"]) < 50})
        print("[diag] mesh", mesh, ISLAND_H, flush=True)
    if gnd != "none":
        g = pickle.load(open(work_file("exp3", "extract_gnd3.pkl"), "rb"))
        w = g["window"]
        pts = [(v[0], v[1]) for v in ex["rail_nodes"].values()]
        assert all(w[0] <= x <= w[2] and w[1] <= y <= w[3] for x, y in pts), "rail outside S3 GND window"
        kw.update(gnd=g, gnd_h=400.0, gnd_fh=100.0)
    ModelB9.gnd_scale9 = 6.0 / R4.N_GND if gnd == "s3scaled" else 1.0
    mdl = ModelB9(ex, shapes, **kw)
    return ex, mdl


def port_vectors(mdl):
    """(+idx, -idx) per port: 0 = DUT port, 1.. = decaps (bare-board terminals)."""
    ports = [(mdl.P, -1)]
    for ra, ga, mid in mdl.dec:
        a = int(mdl.map(np.array([ra]))[0]); b = int(mdl.map(np.array([ga]))[0]) if ga >= 0 else -1
        ports.append((a, b))
    return ports


def multiport(mdl, freqs, loops_f=None, chunk=24):
    dec = list(mdl.dec)
    ports = port_vectors(mdl)
    mdl.dec = []
    K = len(ports); N = mdl.N
    Zs, loopV = [], {}
    for f in freqs:
        t0 = time.time()
        Y = mdl.assemble(f)
        lu = splu(Y, permc_spec="COLAMD")
        Zm = np.zeros((K, K), complex)
        for c0 in range(0, K, chunk):
            cs = list(range(c0, min(K, c0 + chunk)))
            B = np.zeros((N, len(cs)), complex)
            for j, k in enumerate(cs):
                a, b = ports[k]
                if a >= 0: B[a, j] += 1.0
                if b >= 0: B[b, j] -= 1.0
            V = lu.solve(B)
            for i, (a, b) in enumerate(ports):
                Zm[i, cs] = (V[a, :] if a >= 0 else 0) - (V[b, :] if b >= 0 else 0)
            if loops_f is not None and abs(f - loops_f) < 1 and c0 == 0:
                loopV["V"] = V[:, :K].copy()
            del V, B
        del lu
        Zs.append(Zm)
        print(f"  f={f:.4e} K={K} {time.time()-t0:.1f}s rss {peak_rss_mb():.0f}MB", flush=True)
    mdl.dec = dec
    return np.array(Zs), ports, loopV


def close(Zm, zdec, rx=None, common=0.0):
    """Port impedance with decap impedances zdec (Nd,) + series rx (Nd,) closed on the bare multiport."""
    if Zm.shape[0] == 1:
        return Zm[0, 0] + common
    A = Zm[1:, 1:] + np.diag(zdec + (0 if rx is None else rx))
    return Zm[0, 0] - Zm[0, 1:] @ np.linalg.solve(A, Zm[1:, 0]) + common


def run_mp(tag, port, freqset, gnd, loops, nonewidth=None, mesh="std"):
    T0 = time.time()
    ex, mdl = build(tag, port, gnd, nonewidth, mesh)
    ref = np.load(P5.REF[tag], allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == port][0]
    fref = ref["freq"]; zref = ref["Zdiag"][:, col]
    if freqset == "ladder":
        dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
        targ = [1e3, 3e3, 1e4] + list(P5.LADDER) + list(dense)
    else:
        targ = [1e4, 3e4, 1e5, 1e6, 2.5e6]
    idx = sorted({int(np.argmin(abs(fref - x))) for x in targ})
    freqs = fref[idx]
    f_loop = float(fref[int(np.argmin(abs(fref - 1e4)))]) if loops else None
    Zm, ports, loopV = multiport(mdl, freqs, f_loop)
    mids = [d[2] for d in mdl.dec]
    zdec = np.array([[ex["models"][m].impedance([f])[0] for m in mids] for f in freqs]).reshape(len(freqs), len(mids))
    out = dict(freq=freqs, Zmp=Zm, zdec=zdec, zref=zref[idx], mids=np.array(mids), unknowns=mdl.N, wall=time.time() - T0,
               info=json.dumps({k: v for k, v in mdl.info.items() if k != "reference_search"}, default=str))
    tagg = f"{tag}_{port}_{gnd}" + (f"-w{nonewidth}" if nonewidth is not None else "") + (f"-{mesh}" if mesh != "std" else "")
    np.savez(os.path.join(OUT, f"mp_{tagg}.npz"), **out)
    if loops and "V" in loopV:
        V = loopV["V"]; res = {}
        mdl.dec = []
        refdes = [d["refdes"] for d in ex["decaps"] if d["rail_node"] in ex["rail_nodes"]]
        for k in range(1, len(ports)):
            v = V[:, 0] - V[:, k]
            bd = R4.split_breakdown(mdl, f_loop, v); base = mdl.breakdown(f_loop, v)
            a, b = ports[k]
            zl = (v[0 if False else ports[0][0]]) - (v[a] - (v[b] if b >= 0 else 0))
            res[refdes[k - 1] if k - 1 < len(refdes) else str(k)] = dict(loop_Z=[float(np.real(zl)), float(np.imag(zl))],
                breakdown={kk: vv for kk, vv in bd.items()}, pad_links=base.get("rail_pad_links"), rail_vias=base.get("rail_vias"),
                rail_traces=base.get("rail_traces"), gnd_net=base.get("gnd_network_R(vias/traces/pads)"), gnd_pin_entry=base.get("gnd_port_pin_entry_R"))
        # capacitance-share weighted loop: 1 A in at the port, w_k A out at decap k (w_k = C_k/sum C at f_loop).
        # For a resistive network fed through capacitors this power split is the low-f Re Z_port decomposition.
        cf = np.array([-1.0 / (2 * np.pi * f_loop * ex["models"][m].impedance([f_loop])[0].imag) for m in mids])
        wk = cf / cf.sum()
        vw = V[:, 0] - V[:, 1:] @ wk
        bdw = R4.split_breakdown(mdl, f_loop, vw); basew = mdl.breakdown(f_loop, vw)
        res["_weighted"] = dict(weights=wk.tolist(), breakdown=bdw, base={k: v for k, v in basew.items() if not k.startswith("plane_L")})
        json.dump(res, open(os.path.join(OUT, f"loops_{tagg}.json"), "w"), indent=1, default=float)
        print("WEIGHTED", {k: (round(v["mOhm"], 3) if isinstance(v, dict) and "mOhm" in v else v) for k, v in basew.items() if not k.startswith("plane_L")}, flush=True)
        for kk, vv in res.items():
            if kk.startswith("_"):
                continue
            print("LOOP", kk, "Z(mOhm)", round(vv["loop_Z"][0] * 1e3, 3), {L.replace("Signal$", ""): round(x["R_mOhm"], 3) for L, x in vv["breakdown"].items() if isinstance(x, dict) and "R_mOhm" in x},
                  "vias", vv["rail_vias"], "pads", vv["pad_links"], "traces", vv["rail_traces"], "gnd", vv["gnd_net"], vv["gnd_pin_entry"], flush=True)
    print("DONE", tagg, "unknowns", mdl.N, "wall", round(time.time() - T0), flush=True)


# ----------------------------------------------------------------------------------------------
def trace(tag="260729", port="Port19_SITE0"):
    ex, shapes, fine_box = P5.prepare(tag, port)
    rn = ex["rail_nodes"]; st = M.Stack(ex["stackup"])
    planes = {g["layer"] for g in ex["rail_geoms"]}
    from matplotlib.path import Path
    polys = {g["layer"]: ([Path(p) for p in g["pos_polys"]], [Path(p) for p in g["neg_polys"]]) for g in ex["rail_geoms"]}

    def in_metal(L, x, y):
        pos, neg = polys[L]
        return any(p.contains_point((x, y)) for p in pos) and not any(p.contains_point((x, y)) for p in neg)

    vadj = collections.defaultdict(list); tadj = collections.defaultdict(list)
    for u, l, p in ex["rail_vias"]:
        vadj[u].append((l, p)); vadj[l].append((u, p))
    for s, e, w in ex["rail_traces"]:
        tadj[s].append((e, w)); tadj[e].append((s, w))
    rcache = {}

    def via_r(u, l, p):
        Lu, Ll = rn[u][2], rn[l][2]
        key = (p, Lu, Ll)
        if key not in rcache:
            ln = abs(st.z_center_um[Ll] - st.z_center_um[Lu]); pad = ex["padstacks"].get(p, {}); d = pad.get("drill_um") or 40.0
            rcache[key] = (estimate_via_segment_rl(length_um=ln, drill_diameter_um=d, padstack_material=pad.get("material"), start_layer=Lu,
                                                   end_layer=Ll, stackup_layers=ex["stackup_layers_obj"]).resistance_ohm, ln, d)
        return rcache[key]

    by_layer = collections.defaultdict(list)
    for nid, v in rn.items():
        by_layer[v[2]].append(nid)
    rep = dict(decaps=[], port_pins=[])

    def stack_down(n0, seen):
        """follow vias downward from n0 until a plane layer node; returns list of segments."""
        segs = []; cur = n0
        while True:
            nxt = [(o, p) for o, p in vadj[cur] if o not in seen and st.z_center_um[rn[o][2]] > st.z_center_um[rn[cur][2]]]
            if not nxt:
                return segs, cur
            o, p = nxt[0]; seen.add(o)
            r, ln, d = via_r(cur, o, p)
            segs.append(dict(frm=rn[cur][2].replace("Signal$", ""), to=rn[o][2].replace("Signal$", ""), padstack=p, len_um=ln, drill_um=d, R_mOhm=r * 1e3,
                             n_parallel_here=len([1 for oo, pp in vadj[cur] if st.z_center_um[rn[oo][2]] > st.z_center_um[rn[cur][2]]])))
            cur = o
            if rn[o][2] in planes:
                return segs, cur

    for d in ex["decaps"]:
        n0 = d["rail_node"]; x, y, L, ps = rn[n0]
        pd = ex["padstacks"][ps]; pw, ph = pd["pad_w"], pd["pad_h"]
        inpad = [o for o in by_layer[L] if o != n0 and abs(rn[o][0] - x) <= pw / 2 + 1 and abs(rn[o][1] - y) <= ph / 2 + 1]
        rl = 0.5 / (st.row(L)["conductivity"] * st.row(L)["thickness_um"] * 1e-6)
        chains = []
        for o in inpad:
            segs, land = stack_down(o, {o})
            lx, ly, LL, _ = rn[land]
            tr = [(rn[e][2].replace("Signal$", ""), w, round(math.hypot(rn[e][0] - lx, rn[e][1] - ly))) for e, w in tadj[land]]
            chains.append(dict(top_node=o, dxdy=(rn[o][0] - x, rn[o][1] - y), segments=segs, R_stack_mOhm=sum(s["R_mOhm"] for s in segs),
                               landing_layer=LL.replace("Signal$", ""), landing_xy=(lx, ly), landing_inside_rail_polygon=bool(LL in planes and in_metal(LL, lx, ly)),
                               traces_at_landing=tr, further_vias_below=[(rn[oo][2].replace("Signal$", ""), pp) for oo, pp in vadj[land] if st.z_center_um[rn[oo][2]] > st.z_center_um[LL]]))
        par = 1.0 / sum(1.0 / (c["R_stack_mOhm"] + rl * 1e3) for c in chains) if chains else float("nan")
        rep["decaps"].append(dict(refdes=d["refdes"], model=d["model_id"], pad=ps, xy=(x, y), rail_traces_at_pad=len(tadj[n0]), vias_at_pad_node=len(vadj[n0]),
                                  pad_link_R_mOhm=rl * 1e3, n_via_stacks=len(chains), chains=chains, series_R_pad_to_plane_mOhm=par,
                                  gnd_node=d["gnd_node"], dist_to_port_centroid_mm=None))
    P = np.array([(rn[n][0], rn[n][1]) for n in ex["port_pos_nodes"]])
    cx, cy = P.mean(0)
    for dd in rep["decaps"]:
        dd["dist_to_port_centroid_mm"] = math.hypot(dd["xy"][0] - cx, dd["xy"][1] - cy) / 1e3
    for n0 in ex["port_pos_nodes"]:
        segs, land = stack_down(n0, {n0})
        rep["port_pins"].append(dict(node=n0, xy=rn[n0][:2], layer=rn[n0][2], pad=rn[n0][3], segments=len(segs), R_stack_mOhm=sum(s["R_mOhm"] for s in segs),
                                     landing=rn[land][2].replace("Signal$", ""), landing_inside=bool(rn[land][2] in planes and in_metal(rn[land][2], *rn[land][:2]))))
    # connectivity audit: trace endpoints / via nodes not in rail geoms or unconnected
    comp_adj = collections.defaultdict(set)
    for u, l, p in ex["rail_vias"]:
        comp_adj[u].add(l); comp_adj[l].add(u)
    for s, e, w in ex["rail_traces"]:
        comp_adj[s].add(e); comp_adj[e].add(s)
    rep["port_header"] = ex.get("port_header")
    rep["n_pos_pins"] = len(ex["port_pos_nodes"]); rep["n_neg_pins"] = len(ex["port_neg_nodes"])
    rep["rail_geoms"] = {g["layer"]: dict(pos=len(g["pos_polys"]), neg=len(g["neg_polys"]),
                                          area_mm2=float(sum(abs(poly_area(p)) for p in g["pos_polys"]) / 1e6)) for g in ex["rail_geoms"]}
    rep["rail_traces"] = len(ex["rail_traces"]); rep["rail_vias"] = len(ex["rail_vias"])
    rep["traces_with_none_width"] = sum(1 for s, e, w in ex["rail_traces"] if w is None)
    rep["plane_nodes_outside_rail_polygon"] = [(n, v[2].replace("Signal$", ""), v[:2]) for n, v in rn.items() if v[2] in planes and not in_metal(v[2], v[0], v[1])]
    rep["other_rail_components"] = ex["other_rail_components"]
    rep["diagnostics"] = ex.get("diagnostics")
    json.dump(rep, open(os.path.join(OUT, "trace_port19.json"), "w"), indent=1, default=str)
    for dd in rep["decaps"]:
        print("DECAP", dd["refdes"], dd["model"], dd["pad"], "dist", round(dd["dist_to_port_centroid_mm"], 1), "mm traces@pad", dd["rail_traces_at_pad"], "stacks", dd["n_via_stacks"],
              "pad-link", round(dd["pad_link_R_mOhm"], 3), "Rseries pad->plane", round(dd["series_R_pad_to_plane_mOhm"], 3))
        for c in dd["chains"]:
            print("   chain", c["dxdy"], len(c["segments"]), "segs", [s["padstack"] for s in c["segments"]], "R", round(c["R_stack_mOhm"], 3), "land", c["landing_layer"],
                  "inside", c["landing_inside_rail_polygon"], "traces", c["traces_at_landing"], "below", c["further_vias_below"][:3])
    for pp in rep["port_pins"]:
        print("PIN", pp["node"], pp["segments"], round(pp["R_stack_mOhm"], 3), pp["landing"], pp["landing_inside"])
    print("GEOMS", rep["rail_geoms"], "none-width traces", rep["traces_with_none_width"], "plane nodes outside polygon", len(rep["plane_nodes_outside_rail_polygon"]))
    print("HEADER", rep["port_header"])


def poly_area(p):
    p = np.asarray(p).reshape(-1, 2)
    return 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--tag", default="260729")
    ap.add_argument("--port", default="Port19_SITE0")
    ap.add_argument("--freqs", default="ladder", choices=["ladder", "few"])
    ap.add_argument("--gnd", default="none", choices=["none", "s3", "s3scaled"])
    ap.add_argument("--loops", action="store_true")
    ap.add_argument("--mesh", default="std", choices=["std", "h100", "h100_isl50"])
    ap.add_argument("--nonewidth", default=None, help="DIAGNOSTIC width (um) or 'drop' for Trace records without Width")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.trace:
        trace(a.tag, a.port)
    else:
        run_mp(a.tag, a.port, a.freqs, a.gnd, a.loops, a.nonewidth, a.mesh)
