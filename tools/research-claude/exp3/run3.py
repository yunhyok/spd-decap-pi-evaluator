#!/usr/bin/env python
"""EXP-3 ablation ladder runner.

  --step S0  EXP-1b model (variant B + two-sided d_eff), recomputed at the ladder frequencies
  --step S1  + homogenised plane conductance (traces/pads drawn into planes)
  --step S2  + fringing (w+2d where w < 5 d)
  --step S3  + explicit resistive DGND sheets / GND via network (no window cut)
  --sweep    standard 25-point sweep + resonance points instead of the ladder frequencies
  --fine-h / --top-h for the S4 grid check (--freqs 1e6)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import resource
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "exp1"))
import model as M  # noqa: E402
import model3 as M3  # noqa: E402
from exp1b import TwoSided  # noqa: E402
from run_exp1 import pick_freqs, plot, resonance, gates  # noqa: E402

OUT = "/home/claude/work/exp3"
LADDER = [3.0e4, 1.0e5, 3.0e5, 1.0e6, 2.5e6, 1.0e7, 1.0e8]


def ladder_gates(freq, Z, zr, fres_m, fres_r):
    w = 2 * np.pi * freq
    d = {}
    lo = (freq >= 1e3) & (freq <= 1.001e5)
    d["G1_max_abs_dRe_mOhm"] = float(np.max(np.abs(Z.real - zr.real)[lo]) * 1e3)
    m2 = (freq >= 0.999e5) & (freq <= 1.001e6)
    dl = (Z.imag - zr.imag)[m2] / w[m2] * 1e12
    d["G2_dL_pH_range"] = [float(dl.min()), float(dl.max())]
    i1 = int(np.argmin(abs(freq - 1e6)))
    d["G3_rel_err_1MHz"] = float(abs(Z[i1] - zr[i1]) / abs(zr[i1]))
    m4 = (freq >= 0.999e6) & (freq <= 1.001e7)
    d["G4_max_rel_err"] = float(np.max(np.abs(Z - zr)[m4] / np.abs(zr)[m4]))
    d["G4_f_res_model_Hz"] = fres_m
    d["G4_f_res_rel_err_vs_1.585MHz"] = float(abs(fres_m - 1.585e6) / 1.585e6)
    m5 = freq > 1.001e7
    d["G5_rel_err"] = [float(x) for x in (np.abs(Z - zr)[m5] / np.abs(zr)[m5])]
    d["PASS"] = dict(G1=bool(d["G1_max_abs_dRe_mOhm"] <= 0.05), G2=bool(max(abs(dl.min()), abs(dl.max())) <= 5.0), G3=bool(d["G3_rel_err_1MHz"] < 0.10),
                     G4=bool(d["G4_max_rel_err"] < 0.20 and d["G4_f_res_rel_err_vs_1.585MHz"] < 0.10))
    return d


class OneB:
    """S0 wrapper around model.build_model with the EXP-1b two-sided reference."""

    def __init__(self, ex, shapes, fine_box):
        self.ex = ex
        self.m = M.build_model(ex, 200.0, top_h_um=50.0, fine_box=fine_box, fine_h_um=50.0, include_gnd_sheet_r=False,
                               gnd_via_r_factor=0.0, verbose=False, cell_ref=TwoSided(ex, shapes, 3))
        self.info = {k: v for k, v in self.m.info.items() if k != "rasters"}

    def solve(self, freqs, verbose=True, want_v=False):
        return M.solve(self.m, self.ex, freqs, verbose=verbose, want_voltages=want_v)

    def breakdown(self, f, V):
        return M.loss_breakdown(self.m, self.ex, f, V)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=["S0", "S1", "S2", "S3"])
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--freqs", default="")
    ap.add_argument("--h", type=float, default=200.0)
    ap.add_argument("--fine-h", type=float, default=50.0)
    ap.add_argument("--top-h", type=float, default=50.0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--gnd-h", type=float, default=400.0)
    ap.add_argument("--gnd-fine-h", type=float, default=100.0)
    a = ap.parse_args()
    T0 = time.time()
    ex = pickle.load(open("/home/claude/work/exp1/extract_port18.pkl", "rb"))
    shapes = pickle.load(open("/home/claude/work/exp1/neighbour_shapes.pkl", "rb"))
    fine_box = json.load(open("/home/claude/work/exp1/result.json"))["discretisation"]["fine_box_um"]
    ref = np.load("/home/claude/data/S4LB002_260729_Zdiag.npz", allow_pickle=True)
    fref = ref["freq"]; zref = ref["Zdiag"][:, 17]
    if a.freqs:
        idx = sorted({int(np.argmin(abs(fref - float(x)))) for x in a.freqs.split(",")})
    elif a.sweep:
        idx = sorted(set(pick_freqs(fref, 25)) | {i for i in range(len(fref)) if 0.5e6 <= fref[i] <= 5e6})
    else:
        idx = sorted({int(np.argmin(abs(fref - x))) for x in LADDER} | {i for i in range(len(fref)) if 0.9e6 <= fref[i] <= 2.6e6})
    freqs = fref[idx]; zr = zref[idx]
    if a.step == "S0":
        mdl = OneB(ex, shapes, fine_box)
    else:
        gnd = pickle.load(open(os.path.join(OUT, "extract_gnd3.pkl"), "rb")) if a.step == "S3" else None
        mdl = M3.Model3(ex, shapes, h=a.h, fh=a.fine_h, top_h=a.top_h, fine_box=fine_box,
                        sub_c=int(round(a.h / 10)), sub_f=int(round(a.fine_h / 5)), sub_top=int(round(a.top_h / 5)),
                        fringe=a.step in ("S2", "S3"), gnd=gnd, gnd_h=a.gnd_h, gnd_fh=a.gnd_fine_h)
    t = time.time()
    Z, stats = mdl.solve(freqs)
    out = dict(step=a.step, h=a.h, fine_h=a.fine_h, top_h=a.top_h, info={k: v for k, v in mdl.info.items() if k != "reference_search"},
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zr.real], Zref_im=[float(x) for x in zr.imag],
               rel_err=[float(x) for x in np.abs(Z - zr) / np.abs(zr)],
               dRe_mOhm=[float(x) for x in (Z.real - zr.real) * 1e3],
               dL_pH=[float(x) for x in (Z.imag - zr.imag) / (2 * np.pi * freqs) * 1e12],
               per_freq_stats=stats, solve_seconds=time.time() - t)
    if len(freqs) > 3:
        rsel = [k for k, f in enumerate(freqs) if 0.5e6 <= f <= 5e6]
        fr_m, _ = resonance(freqs[rsel], Z[rsel])
        out["ladder_gates"] = ladder_gates(freqs, Z, zr, fr_m, 1.585e6)
        if a.sweep:
            ridx = [i for i in idx if 0.5e6 <= fref[i] <= 5e6]; sidx = pick_freqs(fref, 25)
            sel = [idx.index(i) for i in sidx]
            fr_r, zr_r = resonance(fref[ridx], zref[ridx]); fr_mm, zm = resonance(freqs[rsel], Z[rsel])
            out["gates"] = gates(freqs[sel], Z[sel], zref[sidx], fr_mm, zm, fr_r, zr_r)
        print("GATES", json.dumps(out["ladder_gates"], default=str), flush=True)
    bd = {}
    for fb in (1e5, 1e6) if not a.freqs else ():
        _, _, Vs = mdl.solve([fb], verbose=False, want_v=True)
        bd[f"{fb:.0e}"] = mdl.breakdown(fb, Vs[0])
        print("BREAKDOWN", fb, {k: (round(v["mOhm"], 4), round(v.get("pH", 0), 2)) for k, v in bd[f"{fb:.0e}"].items() if isinstance(v, dict) and "mOhm" in v and (abs(v["mOhm"]) > 0.002 or abs(v.get("pH", 0)) > 0.2)}, flush=True)
    out["breakdown"] = bd
    out["peak_rss_MB"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    out["wall_seconds"] = time.time() - T0
    fn = os.path.join(OUT, f"result_{a.step}{'_sweep' if a.sweep else ''}{a.tag}.json")
    json.dump(out, open(fn, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if a.sweep:
        plot(os.path.join(OUT, f"exp3_Z18_{a.step}{a.tag}.png"), {f"EXP-3 {a.step}": dict(freq=out["freq"], Z_re=out["Z_re"], Z_im=out["Z_im"])}, fref, zref)
    print("done", fn, out["wall_seconds"], out["peak_rss_MB"], flush=True)


if __name__ == "__main__":
    main()
