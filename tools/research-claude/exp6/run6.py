#!/usr/bin/env python
"""EXP-6: void (perforation) handling audit on the frozen adopted model (EXP-5 pipeline, S2 + (b)).

Modes
  V0         : all voids as in the SPD (adopted model)
  V1         : fill voids tagged SmallHole_* on rail planes -> the 260729 SPD has NO tagged voids
               (0 'SmallHole'/'Sub-element' primitive records), so V1 == V0 (not run separately)
  V3(D)      : DIAGNOSTIC ONLY - fill every negative primitive (void) on the rail plane layers whose
               equivalent diameter 2*sqrt(A/pi) < D um (rail artwork only; DGND artwork used for the
               d_eff search is unchanged)
--stats      : void statistics per layer (rail planes of the port + adjacent DGND planes)
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("../exp5", "../exp4", "../exp3", "../exp1"):
    sys.path.insert(0, os.path.join(HERE, p))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import work_dir  # noqa: E402
import pipeline as P5  # noqa: E402
import homog as H  # noqa: E402
import run4 as R4  # noqa: E402
from run3 import ladder_gates  # noqa: E402
from run_exp1 import resonance  # noqa: E402

OUT = work_dir("exp6")


def poly_area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def void_sizes(geom):
    """(order_index, kind, D_eq_um, area_um2) for every negative primitive."""
    out = []
    for k, (kind, i) in enumerate(geom["order"]):
        if kind == "negative_polygon":
            a = poly_area(geom["neg_polys"][i].reshape(-1, 2))
        elif kind == "negative_circle":
            a = math.pi * geom["neg_circles"][i][2] ** 2
        else:
            continue
        out.append((k, kind, 2 * math.sqrt(a / math.pi), a))
    return out


def fill_small(geom, dmax):
    g = dict(geom)
    drop = {k for k, kind, d, a in void_sizes(geom) if d < dmax}
    g["order"] = [st for k, st in enumerate(geom["order"]) if k not in drop]
    return g, len(drop)


def layer_stats(geom, s=20.0):
    pts = np.vstack([p.reshape(-1, 2) for p in geom["pos_polys"]] +
                    [np.array([[c[0] - c[2], c[1] - c[2]], [c[0] + c[2], c[1] + c[2]]]) for c in geom["pos_circles"]])
    x0, y0 = pts.min(0); x1, y1 = pts.max(0)
    nx = int((x1 - x0) / s) + 2; ny = int((y1 - y0) / s) + 2
    pos_only = dict(geom); pos_only["order"] = [st for st in geom["order"] if st[0].startswith("positive")]
    full = H.raster_image(pos_only, [], x0, y0, nx, ny, s).sum()
    res = {}
    vs = void_sizes(geom)
    for D in (0.0, 200.0, 400.0, 1500.0):
        g, n = fill_small(geom, D)
        # metal with voids >= D kept
        res[f"metal_fraction_fill_lt_{int(D)}"] = float(H.raster_image(g, [], x0, y0, nx, ny, s).sum() / full)
    d = np.array([v[2] for v in vs]) if vs else np.zeros(0)
    res.update(n_voids=len(vs), n_neg_polygons=sum(1 for v in vs if v[1] == "negative_polygon"), n_neg_circles=sum(1 for v in vs if v[1] == "negative_circle"),
               D_eq_um_percentiles={q: float(np.percentile(d, q)) for q in (5, 25, 50, 75, 95)} if len(d) else {},
               n_lt_200=int((d < 200).sum()), n_200_400=int(((d >= 200) & (d < 400)).sum()), n_400_1500=int(((d >= 400) & (d < 1500)).sum()), n_ge_1500=int((d >= 1500).sum()),
               tagged_smallhole=0)
    res["void_area_fraction_all"] = 1.0 - res["metal_fraction_fill_lt_0"]
    res["note"] = "fractions relative to the union of the positive artwork (outer outline incl. large cut-outs)"
    return res


def fit_dl(f, Z, Zr):
    w = 2 * np.pi * f
    m = (f >= 2.9e4) & (f <= 1.001e6)
    A = np.column_stack([w[m], -1.0 / w[m]])
    c, *_ = np.linalg.lstsq(A, (Z.imag - Zr.imag)[m], rcond=None)
    return float(c[0] * 1e12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="Port18_SITE0")
    ap.add_argument("--dmax", type=float, default=0.0, help="0 = V0; >0 = V3 diagnostic fill threshold (um)")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    T0 = time.time()
    R4.patch_traces("b")
    ex, shapes, fine_box = P5.prepare("260729", a.port)
    if a.stats:
        st = {}
        rail_layers = [g["layer"] for g in ex["rail_geoms"]]
        for g in ex["rail_geoms"]:
            st["rail:" + g["layer"]] = layer_stats(g)
            print(g["layer"], st["rail:" + g["layer"]], flush=True)
        for L in ["Signal$L02(DGND)", "Signal$L13(DGND)", "Signal$L16(DGND)", "Signal$L18(DGND)", "Signal$L24(DGND)", "Signal$L27(DGND)"]:
            g = shapes.get(L, {}).get("DGND")
            if g is not None:
                st["gnd:" + L] = layer_stats(g, s=25.0)
                print(L, st["gnd:" + L], flush=True)
        json.dump(st, open(os.path.join(OUT, f"void_stats_{a.port}.json"), "w"), indent=1)
        return
    ex2 = copy.copy(ex)
    filled = {}
    if a.dmax > 0:
        ex2["rail_geoms"] = []
        for g in ex["rail_geoms"]:
            g2, n = fill_small(g, a.dmax)
            ex2["rail_geoms"].append(g2); filled[g["layer"]] = n
    ref = np.load(P5.REF["260729"], allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == a.port][0]
    fref = ref["freq"]; zref = ref["Zdiag"][:, col]
    dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
    idx = sorted({int(np.argmin(abs(fref - x))) for x in list(P5.LADDER) + list(dense)})
    freqs = fref[idx]; zr = zref[idx]
    mdl = P5.ModelB(ex2, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10, fringe=True)
    Z, stt = mdl.solve(freqs)
    rsel = [k for k, f in enumerate(freqs) if 1e5 <= f <= 1e8]
    fr_m, zm = resonance(freqs[rsel], Z[rsel])
    ridx = [i for i in range(len(fref)) if 1e5 <= fref[i] <= 1e8]
    fr_r, zmr = resonance(fref[ridx], zref[ridx])
    bd = {}
    for fb in (1e5, 1e6):
        _, _, Vs = mdl.solve([fb], verbose=False, want_v=True)
        bd[f"{fb:.0e}"] = R4.split_breakdown(mdl, fb, Vs[0])
    plane_L = {L: v["L_ext_pH"] + v["L_int_pH"] for L, v in bd["1e+05"].items() if isinstance(v, dict) and "L_ext_pH" in v}
    k1 = int(np.argmin(abs(freqs - 1e6)))
    fdl = fit_dl(freqs, Z, zr)
    out = dict(port=a.port, mode="V0" if a.dmax == 0 else f"V3_D{int(a.dmax)}", dmax_um=a.dmax, voids_filled=filled,
               two_sided_layers=sorted(mdl.two_sided_layers), unknowns=mdl.N,
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zr.real], Zref_im=[float(x) for x in zr.imag],
               dL_pH=[float(x) for x in (Z.imag - zr.imag) / (2 * np.pi * freqs) * 1e12],
               dRe_mOhm=[float(x) for x in (Z.real - zr.real) * 1e3], rel_err=[float(x) for x in np.abs(Z - zr) / np.abs(zr)],
               fit_dL_pH=fdl, plane_L_100k=plane_L, plane_L_total_100k=float(sum(plane_L.values())), ratio_dL_over_planeL=fdl / float(sum(plane_L.values())),
               err_1MHz=float(abs(Z[k1] - zr[k1]) / abs(zr[k1])), dRe_100k_mOhm=float((Z.real - zr.real)[int(np.argmin(abs(freqs - 1e5)))] * 1e3),
               f_res_model=fr_m, f_res_ref=fr_r, gates=ladder_gates(freqs, Z, zr, fr_m, fr_r), breakdown=bd, wall_seconds=time.time() - T0)
    fn = os.path.join(OUT, f"result_{a.port}_{out['mode']}.json")
    json.dump(out, open(fn, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("RESULT", a.port, out["mode"], "filled", filled, "fitdL", round(fdl, 1), "planeL", round(out["plane_L_total_100k"], 1),
          "ratio", round(out["ratio_dL_over_planeL"], 3), "err1M", round(out["err_1MHz"], 4), "dRe100k", round(out["dRe_100k_mOhm"], 3),
          "fres", round(fr_m / 1e6, 3), round(fr_r / 1e6, 3), "wall", round(out["wall_seconds"]), flush=True)


if __name__ == "__main__":
    main()
