#!/usr/bin/env python
"""EXP-7 step 2/3: coarse-mesh emulation with the frozen S2+(b) physics.

Uniform plane grid h for every rail plane (no fine box) and TOP at the same h; SPD nodes (vias, pins,
decap pads) snap to the nearest grid node (EXP-3 snapping rule); homogenised conductance with
sub-tiles of min(20 um, h/25).  Variant 'B' = ideal GND (adopted model); variant 'C' = explicit GND
sheets (EXP-4 (c): six nearest DGND sheets, R only, scaled by 6/13, same h, GND via/trace network).
DIAGNOSTIC of discretisation convention, not a tuning.
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
from paths import work_dir, work_file  # noqa: E402
import pipeline as P5  # noqa: E402
import run4 as R4  # noqa: E402
from run_exp1 import resonance  # noqa: E402

OUT = work_dir("exp7")


class ModelC(P5.ModelB):
    def build(self):
        super().build()
        self.mode = "c"
        self.gnd_scale = 6.0 / 13.0

    def zs_plane(self, f, sh, rail):
        if not rail:
            return complex(R4.zs_two(f, sh.sigma, sh.t).real, 0.0)
        return super().zs_plane(f, sh, rail)


def fit_dl(f, dIm, lo, hi):
    w = 2 * np.pi * f
    m = (f >= lo) & (f <= hi)
    A = np.column_stack([w[m], -1.0 / w[m]])
    c, *_ = np.linalg.lstsq(A, dIm[m], rcond=None)
    return float(c[0] * 1e12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="Port18_SITE0")
    ap.add_argument("--h", type=float, required=True, help="grid um")
    ap.add_argument("--variant", default="B", choices=["B", "C"])
    a = ap.parse_args()
    T0 = time.time()
    R4.patch_traces("b")
    ex, shapes, _fb = P5.prepare("260729", a.port)
    ref = np.load(P5.REF["260729"], allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == a.port][0]
    fref = ref["freq"]; zref = ref["Zdiag"][:, col]
    dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
    idx = sorted({int(np.argmin(abs(fref - x))) for x in list(P5.LADDER) + list(dense)})
    freqs = fref[idx]; zr = zref[idx]
    st = min(20.0, a.h / 25.0)
    sub = int(round(a.h / st))
    kw = dict(h=a.h, fh=a.h, top_h=a.h, fine_box=None, sub_c=sub, sub_f=sub, sub_top=sub, fringe=True)
    if a.variant == "B":
        mdl = P5.ModelB(ex, shapes, **kw)
    else:
        gnd = pickle.load(open(work_file("exp3", "extract_gnd3.pkl"), "rb"))
        mdl = ModelC(ex, shapes, gnd=gnd, gnd_h=a.h, gnd_fh=a.h, **kw)
    Z, stt = mdl.solve(freqs)
    w = 2 * np.pi * freqs
    dIm = Z.imag - zr.imag
    rsel = [k for k, f in enumerate(freqs) if 1e5 <= f <= 1e8]
    fr_m, _ = resonance(freqs[rsel], Z[rsel])
    ridx = [i for i in range(len(fref)) if 1e5 <= fref[i] <= 1e8]
    fr_r, _ = resonance(fref[ridx], zref[ridx])
    k1 = int(np.argmin(abs(freqs - 1e6))); k100 = int(np.argmin(abs(freqs - 1e5)))
    _, _, Vs = mdl.solve([1e5], verbose=False, want_v=True)
    bd = R4.split_breakdown(mdl, 1e5, Vs[0])
    plane_L = float(sum(v["L_ext_pH"] + v["L_int_pH"] for v in bd.values() if isinstance(v, dict) and "L_ext_pH" in v))
    out = dict(port=a.port, h_um=a.h, variant=a.variant, subtile_um=st, unknowns=mdl.N,
               fit_dL_30k_300k_pH=fit_dl(freqs, dIm, 2.9e4, 3.1e5), fit_dL_30k_1M_pH=fit_dl(freqs, dIm, 2.9e4, 1.001e6),
               dL_1MHz_pH=float(dIm[k1] / w[k1] * 1e12), dRe_100k_mOhm=float((Z.real - zr.real)[k100] * 1e3),
               dRe_1MHz_mOhm=float((Z.real - zr.real)[k1] * 1e3), err_1MHz=float(abs(Z[k1] - zr[k1]) / abs(zr[k1])),
               f_res_model=fr_m, f_res_ref=fr_r, plane_L_100k_pH=plane_L, breakdown_100k=bd,
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zr.real], Zref_im=[float(x) for x in zr.imag], stats=stt, wall_seconds=time.time() - T0)
    json.dump(out, open(os.path.join(OUT, f"coarse_{a.port}_{a.variant}_h{int(a.h)}.json"), "w"), indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("RESULT", a.port, a.variant, "h", a.h, "unk", mdl.N, "fitdL", round(out["fit_dL_30k_300k_pH"], 1), "dL1M", round(out["dL_1MHz_pH"], 1),
          "dRe100k", round(out["dRe_100k_mOhm"], 3), "err1M", round(out["err_1MHz"], 4), "fres", round(fr_m / 1e6, 3), round(fr_r / 1e6, 3),
          "planeL", round(plane_L, 1), "wall", round(out["wall_seconds"]), flush=True)


if __name__ == "__main__":
    main()
