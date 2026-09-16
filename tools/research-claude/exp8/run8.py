#!/usr/bin/env python
"""EXP-8: "cavity-wall (PowerSI convention)" reference rule.

Mode ANY: for every rail plane cell, the reference on each side is the nearest conductor layer (within
3 conductor layers) whose artwork of ANY net other than the rail itself covers the cell (cell-wise;
where it does not cover, fall back to the next metal layer).  d_eff = d_up d_dn/(d_up+d_dn); cell C to
the adjacent wall on each side (to the ideal reference, as in 1b).  Everything else is the frozen
EXP-5 model (S2 + (b); the (b) two-sided-Zs layer set follows the same majority rule, now evaluated on
the any-net reference).  NOT a physical claim: a floating other-net plane with delta > t is
magnetically transparent; this mode tests whether PowerSI computes as if it were an opaque wall.

  --predict  : prediction table (no solve) from EXP-5 per-layer L and cell-wise d_eff ratios
  --tag T --port P : run the ANY mode at the ladder frequencies
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
for p in ("../exp5", "../exp4", "../exp3", "../exp1"):
    sys.path.insert(0, os.path.join(HERE, p))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import work_dir  # noqa: E402
import pipeline as P5  # noqa: E402
import model3 as M3  # noqa: E402
import model as M  # noqa: E402
import run4 as R4  # noqa: E402
from exp1b import TwoSided  # noqa: E402
from run3 import ladder_gates  # noqa: E402
from run_exp1 import resonance  # noqa: E402

OUT = work_dir("exp8")
CASES = [("260729", "Port18_SITE0"), ("260729", "Port16_SITE0"), ("260729", "Port1_SITE0"), ("260729", "Port14_SITE0"),
         ("260729", "Port7_SITE0"), ("260729", "Port19_SITE0"), ("260804", "Port18_SITE0")]
EXP5 = {("260729", "Port18_SITE0"): "result_260729_Port18_SITE0_sweep.json", ("260804", "Port18_SITE0"): "result_260804_Port18_SITE0_sweep.json"}


class TwoSidedAny(TwoSided):
    """Reference = union of all nets' artwork except the rail net itself."""

    def __init__(self, ex, shapes, max_layers=3, gnd_net="__ANY__"):
        super().__init__(ex, shapes, max_layers, gnd_net="__ANY__")
        self.rail = ex["rail_net"]

    def mask(self, layer, net, blk):
        if net != "__ANY__":
            return super().mask(layer, net, blk)
        key = (layer, "__ANY__", blk["x0"], blk["y0"], blk["nx"], blk["ny"], blk["h"])
        if key not in self.cache:
            m = np.zeros((blk["ny"], blk["nx"]), bool)
            for nt in self.shapes.get(layer, {}):
                if nt != self.rail:
                    m |= super().mask(layer, nt, blk)
            self.cache[key] = m
        return self.cache[key]


def fit_dl(f, dIm, lo=2.9e4, hi=1.001e6):
    w = 2 * np.pi * f
    m = (f >= lo) & (f <= hi)
    A = np.column_stack([w[m], -1.0 / w[m]])
    c, *_ = np.linalg.lstsq(A, dIm[m], rcond=None)
    return float(c[0] * 1e12)


def exp5_file(tag, port):
    return os.path.join(work_dir("exp5"), EXP5.get((tag, port), f"result_{tag}_{port}_ladder.json"))


def predict():
    rows = []
    for tag, port in CASES:
        ex, shapes, _ = P5.prepare(tag, port)
        r5 = json.load(open(exp5_file(tag, port)))
        f = np.array(r5["freq"]); Z = np.array(r5["Z_re"]) + 1j * np.array(r5["Z_im"]); Zr = np.array(r5["Zref_re"]) + 1j * np.array(r5["Zref_im"])
        dl = fit_dl(f, Z.imag - Zr.imag)
        bd = r5["breakdown"]["1e+05"]
        tg = TwoSided(ex, shapes, 3); ta = TwoSidedAny(ex, shapes, 3)
        layers = []
        Lext_new = 0.0; Ltot = 0.0
        for g in ex["rail_geoms"]:
            L = g["layer"]
            hh = 50.0 if L == "Signal$TOP" else 200.0
            ras = M.rasterize(g, hh)
            blk = dict(x0=ras["x0"], y0=ras["y0"], nx=ras["nx"], ny=ras["ny"], h=hh, mask=ras["mask"])
            m = blk["mask"]
            if not m.any():
                continue
            dg = tg(L, blk)["d_eff"][m]; da = ta(L, blk)["d_eff"][m]
            ratio = float(np.mean(da / dg))
            # which metal is nearest on each side under both rules (coarse report, largest newly_assigned)
            def nearest(rep):
                out = {}
                for side, lst in rep["blocks"][-1]["sides"].items():
                    best = max(lst, key=lambda x: x["newly_assigned"]) if lst else None
                    out[side] = (best["layer"].replace("Signal$", ""), best["gap_um"], best["newly_assigned"],
                                 (best["other_nets_cover"][0][0] if best["other_nets_cover"] else "DGND")) if best and best["newly_assigned"] > 0 else None
                return out
            v = bd.get(L, {})
            lext = v.get("L_ext_pH", 0.0); lint = v.get("L_int_pH", 0.0)
            Lext_new += lext * ratio + lint; Ltot += lext + lint
            layers.append(dict(layer=L.replace("Signal$", ""), cells=int(m.sum()), d_eff_gnd=float(np.median(dg)), d_eff_any=float(np.median(da)),
                               mean_ratio_any_over_gnd=ratio, nearest_gnd_rule=nearest(tg.report[L]), nearest_any_rule=nearest(ta.report[L]),
                               L_ext_pH=lext, L_int_pH=lint))
        pred = Lext_new / Ltot if Ltot else float("nan")
        rows.append(dict(tag=tag, port=port, rail=ex["rail_net"], decaps=len(ex["decaps"]), layers=layers, L_plane_pH=Ltot,
                         fit_dL_pH=dl, predicted_plane_L_ratio=pred, predicted_dL_change_pH=Lext_new - Ltot,
                         measured_ratio_1_minus=1 - dl / Ltot, measured_ratio_coord=1 / (1 + dl / Ltot)))
        print("PRED", tag, port, "Lplane", round(Ltot, 1), "fitdL", round(dl, 1), "pred ratio", round(pred, 3), "pred dL change", round(Lext_new - Ltot, 1),
              "meas 1-dL/L", round(1 - dl / Ltot, 3), "meas 1/(1+dL/L)", round(1 / (1 + dl / Ltot), 3),
              [(x["layer"], round(x["d_eff_gnd"], 1), round(x["d_eff_any"], 1), round(x["mean_ratio_any_over_gnd"], 3)) for x in layers], flush=True)
    p = np.array([r["predicted_plane_L_ratio"] for r in rows]); m1 = np.array([r["measured_ratio_1_minus"] for r in rows]); m2 = np.array([r["measured_ratio_coord"] for r in rows])
    summary = dict(rows=rows, pearson_pred_vs_1_minus=float(np.corrcoef(p, m1)[0, 1]), pearson_pred_vs_coord=float(np.corrcoef(p, m2)[0, 1]),
                   pred_dL_change=[r["predicted_dL_change_pH"] for r in rows], fit_dL=[r["fit_dL_pH"] for r in rows])
    summary["pearson_pred_dLchange_vs_minus_fitdL"] = float(np.corrcoef(summary["pred_dL_change"], -np.array(summary["fit_dL"]))[0, 1])
    json.dump(summary, open(os.path.join(OUT, "prediction.json"), "w"), indent=1)
    print("CORR", summary["pearson_pred_vs_1_minus"], summary["pearson_pred_vs_coord"], summary["pearson_pred_dLchange_vs_minus_fitdL"])


def run(tag, port):
    T0 = time.time()
    R4.patch_traces("b")
    M3.TwoSided = TwoSidedAny  # model3 builds its reference search through this name
    ex, shapes, fine_box = P5.prepare(tag, port)
    ref = np.load(P5.REF[tag], allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == port][0]
    fref = ref["freq"]; zref = ref["Zdiag"][:, col]
    dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
    idx = sorted({int(np.argmin(abs(fref - x))) for x in list(P5.LADDER) + list(dense)})
    freqs = fref[idx]; zr = zref[idx]
    mdl = P5.ModelB(ex, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10, fringe=True)
    Z, st = mdl.solve(freqs)
    rsel = [k for k, f in enumerate(freqs) if 1e5 <= f <= 1e8]
    fr_m, zm = resonance(freqs[rsel], Z[rsel])
    ridx = [i for i in range(len(fref)) if 1e5 <= fref[i] <= 1e8]
    fr_r, zmr = resonance(fref[ridx], zref[ridx])
    _, _, Vs = mdl.solve([1e5], verbose=False, want_v=True)
    bd = R4.split_breakdown(mdl, 1e5, Vs[0])
    k1 = int(np.argmin(abs(freqs - 1e6))); k100 = int(np.argmin(abs(freqs - 1e5)))
    out = dict(mode="cavity-wall (PowerSI convention)", tag=tag, port=port, rail=ex["rail_net"], unknowns=mdl.N,
               two_sided_layers=sorted(mdl.two_sided_layers),
               reference_search={L: [{k: v for k, v in b.items() if k != "sides"} for b in r["blocks"]] for L, r in mdl.info["reference_search"].items()},
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zr.real], Zref_im=[float(x) for x in zr.imag],
               fit_dL_pH=fit_dl(freqs, Z.imag - zr.imag), dL_1MHz_pH=float((Z.imag - zr.imag)[k1] / (2 * np.pi * 1e6) * 1e12),
               dRe_100k_mOhm=float((Z.real - zr.real)[k100] * 1e3), dRe_1MHz_mOhm=float((Z.real - zr.real)[k1] * 1e3),
               err_1MHz=float(abs(Z[k1] - zr[k1]) / abs(zr[k1])), f_res_model=fr_m, f_res_ref=fr_r,
               ladder_gates=ladder_gates(freqs, Z, zr, fr_m, fr_r), breakdown_100k=bd, stats=st, wall_seconds=time.time() - T0)
    json.dump(out, open(os.path.join(OUT, f"result_{tag}_{port}_any.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("RESULT", tag, port, "fitdL", round(out["fit_dL_pH"], 1), "dL1M", round(out["dL_1MHz_pH"], 1), "dRe100k", round(out["dRe_100k_mOhm"], 3),
          "err1M", round(out["err_1MHz"], 4), "fres", round(fr_m / 1e6, 3), round(fr_r / 1e6, 3), "two-sided", out["two_sided_layers"],
          "wall", round(out["wall_seconds"]), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--predict", action="store_true")
    ap.add_argument("--tag", default="260729")
    ap.add_argument("--port", default="Port18_SITE0")
    a = ap.parse_args()
    if a.predict:
        predict()
    else:
        run(a.tag, a.port)
