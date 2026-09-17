#!/usr/bin/env python
"""EXP-5: frozen adopted model (EXP-3 S2 + EXP-4 (b)) applied to other SPDs / ports.

Frozen parameters (no tuning): coarse h = 200 µm (10 µm sub-tiles), fine box = port +terminal
bbox + 1 mm at 50 µm (5 µm sub-tiles), TOP 50 µm (5 µm), per-cell two-sided d_eff search over
3 conductor layers on DGND artwork (exp1b.TwoSided), fringing w+2d for w < 5 d, via R from
via_model + coax L, pad links, decap SPICE models, ideal GND reference, one-sided Zs except the
symmetric two-sided Zs on rail planes whose coarse cells are majority two-sided (EXP-4 (b)
rule; for 260729 port 18 this selects exactly L14 and L25).
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("../exp4", "../exp3", "../exp1"):
    sys.path.insert(0, os.path.join(HERE, p))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import DESIGNS, peak_rss_mb, ref_npz, spd_path, work_dir, work_file  # noqa: E402
import extract as EX  # noqa: E402
import run4 as R4  # noqa: E402
from exp1b import TwoSided, load_layer_shapes  # noqa: E402
from run3 import ladder_gates  # noqa: E402
from run_exp1 import gates, pick_freqs, plot, resonance  # noqa: E402

OUT = work_dir("exp5")
SPD = {k: str(spd_path(k)) for k in DESIGNS}
REF = {k: str(ref_npz(k)) for k in SPD}
LADDER = [3.0e4, 1.0e5, 3.0e5, 1.0e6, 2.5e6, 1.0e7, 1.0e8]


class ModelB(R4.Model4):
    """Model4 in mode 'b' with the two-sided layer set derived by the frozen majority rule."""

    def build(self):
        super().build()
        rep = self.info.get("reference_search", {})
        ts = set()
        frac = {}
        for L, r in rep.items():
            cells = sum(b["cells"] for b in r["blocks"] if b["h"] >= 200.0)
            two = sum(b["cells"] * b["two_sided"] for b in r["blocks"] if b["h"] >= 200.0)
            frac[L] = two / cells if cells else 0.0
            if cells and two / cells > 0.5:
                ts.add(L)
        self.two_sided_layers = ts
        self.info["two_sided_fraction_coarse"] = frac
        self.info["two_sided_layers"] = sorted(ts)
        self.mode = "b"

    def zs_plane(self, f, sh, rail):
        one = complex(R4.zs_one(f, sh.sigma, sh.t))
        if rail and sh.layer in self.two_sided_layers:
            return R4.zs_two(f, sh.sigma, sh.t)
        return one


def prepare(tag, port):
    ex_fn = os.path.join(OUT, f"extract_{tag}_{port}.pkl")
    if os.path.exists(ex_fn):
        ex = pickle.load(open(ex_fn, "rb"))
    else:
        ex = EX.extract(SPD[tag], port)
        pickle.dump(ex, open(ex_fn, "wb"))
    sh_fn = os.path.join(OUT, f"shapes_{tag}.pkl") if tag != "260729" else str(work_file("exp1", "neighbour_shapes.pkl"))
    shapes = pickle.load(open(sh_fn, "rb")) if os.path.exists(sh_fn) else {}
    ts = TwoSided(ex, {}, 3)
    need = set()
    for g in ex["rail_geoms"]:
        for side, lst in ts.candidates(g["layer"]).items():
            need.update(cl for cl, _ in lst)
    miss = [L for L in sorted(need) if L not in shapes]
    if miss:
        shapes.update(load_layer_shapes(SPD[tag], miss))
        pickle.dump(shapes, open(sh_fn, "wb"))
    rn = ex["rail_nodes"]
    P = np.array([(rn[x][0], rn[x][1]) for x in ex["port_pos_nodes"] if x in rn])
    fine_box = (P[:, 0].min() - 1000.0, P[:, 1].min() - 1000.0, P[:, 0].max() + 1000.0, P[:, 1].max() + 1000.0)
    return ex, shapes, fine_box


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="260729", choices=list(SPD))
    ap.add_argument("--port", default="Port18_SITE0")
    ap.add_argument("--freqset", default="sweep", choices=["sweep", "ladder"])
    a = ap.parse_args()
    T0 = time.time()
    R4.patch_traces("b")  # traces keep one-sided Zs (identity patch)
    ex, shapes, fine_box = prepare(a.tag, a.port)
    ref = np.load(REF[a.tag], allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == a.port][0]
    fref = ref["freq"]; zref = ref["Zdiag"][:, col]
    if a.freqset == "sweep":
        idx = sorted(set(pick_freqs(fref, 25)) | {i for i in range(len(fref)) if 0.5e6 <= fref[i] <= 5e6})
    else:
        dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
        idx = sorted({int(np.argmin(abs(fref - x))) for x in list(LADDER) + list(dense)})
    freqs = fref[idx]; zr = zref[idx]
    mdl = ModelB(ex, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10, fringe=True)
    Z, st = mdl.solve(freqs)
    # resonance (min |Z|) over the band where the reference has its minimum
    kr = int(np.argmin(np.abs(zref[(fref >= 1e5) & (fref <= 1e8)]))); fr_band = fref[(fref >= 1e5) & (fref <= 1e8)]
    ridx_ref = [i for i in range(len(fref)) if 1e5 <= fref[i] <= 1e8]
    fr_r, zmin_r = resonance(fref[ridx_ref], zref[ridx_ref])
    rsel = [k for k, f in enumerate(freqs) if 1e5 <= f <= 1e8]
    fr_m, zmin_m = resonance(freqs[rsel], Z[rsel])
    out = dict(tag=a.tag, port=a.port, ref_name=names[col], rail=ex["rail_net"], decaps=len(ex["decaps"]),
               decaps_connected=mdl.info.get("decaps_connected"), info={k: v for k, v in mdl.info.items() if k != "reference_search"},
               reference_search={L: [dict({k: v for k, v in b.items() if k != "sides"}) for b in r["blocks"]] for L, r in mdl.info["reference_search"].items()},
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zr.real], Zref_im=[float(x) for x in zr.imag],
               dL_pH=[float(x) for x in (Z.imag - zr.imag) / (2 * np.pi * freqs) * 1e12],
               dRe_mOhm=[float(x) for x in (Z.real - zr.real) * 1e3], rel_err=[float(x) for x in np.abs(Z - zr) / np.abs(zr)],
               f_res_model=fr_m, zmin_model=zmin_m, f_res_ref=fr_r, zmin_ref=zmin_r, stats=st)
    out["ladder_gates"] = ladder_gates(freqs, Z, zr, fr_m, fr_r)
    if a.freqset == "sweep":
        sidx = pick_freqs(fref, 25); sel = [idx.index(i) for i in sidx]
        out["gates"] = gates(freqs[sel], Z[sel], zref[sidx], fr_m, zmin_m, fr_r, zmin_r)
    bd = {}
    for fb in (1e5, 1e6):
        _, _, Vs = mdl.solve([fb], verbose=False, want_v=True)
        bd[f"{fb:.0e}"] = R4.split_breakdown(mdl, fb, Vs[0])
    out["breakdown"] = bd
    out["peak_rss_MB"] = peak_rss_mb()
    out["wall_seconds"] = time.time() - T0
    fn = os.path.join(OUT, f"result_{a.tag}_{a.port}_{a.freqset}.json")
    json.dump(out, open(fn, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    plot(os.path.join(OUT, f"Z_{a.tag}_{a.port}.png"), {f"model S2+(b) {a.tag} {a.port}": dict(freq=out["freq"], Z_re=out["Z_re"], Z_im=out["Z_im"])}, fref, zref)
    print("RESULT", a.tag, a.port, ex["rail_net"], "decaps", len(ex["decaps"]), "two-sided", sorted(mdl.two_sided_layers),
          "G", json.dumps(out["ladder_gates"], default=str), "fres", fr_m, fr_r, "wall", out["wall_seconds"], flush=True)


if __name__ == "__main__":
    main()
