#!/usr/bin/env python
"""EXP-1: plane-pair + circuit hybrid Z(f) for one SPD port vs PowerSI.

Example:
  python run_exp1.py --spd /home/claude/data/S4LB002-2Para_260729_1_injected.spd \
      --port Port18_SITE0 --h 200 --h-check 100 --variant A --variant B
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract as EX  # noqa: E402
import model as M  # noqa: E402

VARIANTS = {
    # pre-registered primary: standard M-FDM unit cell, both plates' surface
    # impedance in series, GND via return R equal to rail via R
    "A": dict(include_gnd_sheet_r=True, gnd_via_r_factor=1.0,
              label="A: M-FDM both plates (rail+adjacent GND sheet Zs), GND via R = rail via R"),
    # diagnosis: ideal (multi-layer) GND return: rail-side R only
    "B": dict(include_gnd_sheet_r=False, gnd_via_r_factor=0.0,
              label="B: ideal GND return (rail sheet Zs + rail via R only)"),
}


def pick_freqs(fref, n=25, lo=1e3, hi=1e8, extra=(1e6,)):
    targets = np.logspace(math.log10(lo), math.log10(hi), n)
    idx = sorted({int(np.argmin(abs(fref - t))) for t in list(targets) + list(extra)})
    return idx


def rel(a, b):
    return float(abs(a - b) / abs(b))


def resonance(f, z):
    """min |Z| with a parabolic refinement in (log f, log|Z|)."""
    k = int(np.argmin(abs(z)))
    if 0 < k < len(f) - 1:
        x = np.log(f[k - 1:k + 2]); y = np.log(abs(z[k - 1:k + 2]))
        c = np.polyfit(x, y, 2)
        if c[0] > 0:
            xm = -c[1] / (2 * c[0])
            return float(np.exp(xm)), float(np.exp(np.polyval(c, xm)))
    return float(f[k]), float(abs(z[k]))


def gates(freq, Zm, Zr, fres_m, zres_m, fres_r, zres_r):
    w = 2 * np.pi * freq
    g = {}
    s1 = (freq >= 1e3 * 0.999) & (freq <= 1e5 * 1.001)
    d1 = np.abs(Zm.real - Zr.real)[s1] * 1e3
    g["G1"] = dict(desc="1-100 kHz |dRe| <= 0.05 mOhm at every point", max_abs_dRe_mOhm=float(d1.max()),
                   dRe_mOhm=[float(x) for x in ((Zm.real - Zr.real)[s1] * 1e3)], PASS=bool(d1.max() <= 0.05))
    s2 = (freq >= 1e5 * 0.999) & (freq <= 1e6 * 1.001)
    dl = ((Zm.imag - Zr.imag) / w)[s2] * 1e12
    g["G2"] = dict(desc="100 kHz-1 MHz dIm/w within +-5 pH", dL_pH=[float(x) for x in dl], max_abs_pH=float(abs(dl).max()),
                   PASS=bool(abs(dl).max() <= 5.0))
    i1 = int(np.argmin(abs(freq - 1e6)))
    e3 = rel(Zm[i1], Zr[i1])
    g["G3"] = dict(desc="complex rel err at 1 MHz < 10%", f=float(freq[i1]), rel_err=e3, Zm=[Zm[i1].real, Zm[i1].imag],
                   Zr=[Zr[i1].real, Zr[i1].imag], PASS=bool(e3 < 0.10))
    s4 = (freq >= 1e6 * 0.999) & (freq <= 1e7 * 1.001)
    e4 = np.abs(Zm - Zr)[s4] / np.abs(Zr)[s4]
    fr_err = abs(fres_m - fres_r) / fres_r
    g["G4"] = dict(desc="1-10 MHz complex rel err < 20% at every point AND f_res within 10%", max_rel_err=float(e4.max()),
                   rel_err=[float(x) for x in e4], f_res_model=fres_m, zmin_model=zres_m, f_res_ref=fres_r, zmin_ref=zres_r,
                   f_res_rel_err=float(fr_err), PASS=bool(e4.max() < 0.20 and fr_err < 0.10))
    s5 = freq > 1e7 * 1.001
    g["G5"] = dict(desc="10-100 MHz report only", f=[float(x) for x in freq[s5]],
                   rel_err=[float(x) for x in (np.abs(Zm - Zr) / np.abs(Zr))[s5]])
    return g


def plot(path, results, fref_all, zref_all):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    m = (fref_all >= 1e3) & (fref_all <= 1e8)
    fr, zr = fref_all[m], zref_all[m]
    ax[0, 0].semilogx(fr, zr.real * 1e3, "k-", lw=2, label="PowerSI ref")
    ax[0, 1].semilogx(fr, zr.imag * 1e3, "k-", lw=2, label="PowerSI ref")
    ax[1, 0].loglog(fr, abs(zr), "k-", lw=2, label="PowerSI ref")
    ax[1, 1].semilogx(fr, np.degrees(np.angle(zr)), "k-", lw=2, label="PowerSI ref")
    for name, r in results.items():
        f = np.array(r["freq"]); z = np.array(r["Z_re"]) + 1j * np.array(r["Z_im"])
        kw = dict(marker="o", ms=3, lw=1.2, label=name)
        ax[0, 0].semilogx(f, z.real * 1e3, **kw)
        ax[0, 1].semilogx(f, z.imag * 1e3, **kw)
        ax[1, 0].loglog(f, abs(z), **kw)
        ax[1, 1].semilogx(f, np.degrees(np.angle(z)), **kw)
    ax[0, 0].set_ylabel("Re Z [mOhm]"); ax[0, 0].set_ylim(0, 4)
    ax[0, 1].set_ylabel("Im Z [mOhm]"); ax[0, 1].set_yscale("symlog", linthresh=0.5)
    ax[1, 0].set_ylabel("|Z| [Ohm]"); ax[1, 1].set_ylabel("phase [deg]")
    for a in ax.ravel():
        a.grid(True, which="both", alpha=0.3); a.set_xlabel("f [Hz]"); a.legend(fontsize=8)
    fig.suptitle("EXP-1 Z18: plane-pair + circuit hybrid vs PowerSI")
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spd", default="/home/claude/data/S4LB002-2Para_260729_1_injected.spd")
    ap.add_argument("--ref", default="/home/claude/data/S4LB002_260729_Zdiag.npz")
    ap.add_argument("--port", default="Port18_SITE0")
    ap.add_argument("--port-index", type=int, default=18, help="one-based column in the reference npz")
    ap.add_argument("--h", type=float, default=200.0, help="plane grid cell [um]")
    ap.add_argument("--top-h", type=float, default=50.0, help="grid cell for Signal$TOP pad polygons [um]")
    ap.add_argument("--h-check", type=float, default=100.0, help="halved grid for convergence check (0=skip); fine-h and top-h are halved too")
    ap.add_argument("--fine-h", type=float, default=50.0, help="local grid inside the port pin-field box [um] (0=off)")
    ap.add_argument("--fine-margin", type=float, default=1000.0, help="margin around the port +terminal bbox for the fine box [um]")
    ap.add_argument("--skip-sweep", action="store_true", help="only run the convergence check")
    ap.add_argument("--nfreq", type=int, default=25)
    ap.add_argument("--freqs", type=str, default="", help="comma list overriding the log sweep")
    ap.add_argument("--variant", action="append", default=None, choices=sorted(VARIANTS))
    ap.add_argument("--out", default="/home/claude/work/exp1")
    ap.add_argument("--cache", default="/home/claude/work/exp1/extract_port18.pkl")
    a = ap.parse_args()
    variants = a.variant or ["A"]
    os.makedirs(a.out, exist_ok=True)
    T0 = time.time()
    if os.path.exists(a.cache):
        ex = pickle.load(open(a.cache, "rb"))
        if ex["spd_path"] != a.spd or a.port not in (ex["port_header"], ex["port_header"].split("::")[0]):
            ex = None
    else:
        ex = None
    if ex is None:
        ex = EX.extract(a.spd, a.port)
        pickle.dump(ex, open(a.cache, "wb"))
    ref = np.load(a.ref, allow_pickle=True)
    fref = ref["freq"]; zref = ref["Zdiag"][:, a.port_index - 1]
    pname = str(ref["port_names"][a.port_index - 1])
    if a.freqs:
        idx = sorted({int(np.argmin(abs(fref - float(x)))) for x in a.freqs.split(",")})
    else:
        idx = pick_freqs(fref, a.nfreq)
    ridx = [i for i in range(len(fref)) if 0.5e6 <= fref[i] <= 5.0e6]  # dense points for resonance
    all_idx = sorted(set(idx) | set(ridx))
    freqs = fref[all_idx]
    result = dict(spd=a.spd, port=a.port, ref_port_name=pname, rail_net=ex["rail_net"], extract_seconds=ex["extract_seconds"],
                  sweep_freq=[float(fref[i]) for i in idx], resonance_freq=[float(fref[i]) for i in ridx],
                  Zref={"freq": [float(x) for x in freqs], "re": [float(x) for x in zref[all_idx].real], "im": [float(x) for x in zref[all_idx].imag]},
                  model_definition=M.__doc__, variants={}, convergence={})
    plots = {}
    fr_ref, zmin_ref = resonance(fref[ridx], zref[ridx])
    rn = ex["rail_nodes"]
    P = np.array([(rn[x][0], rn[x][1]) for x in ex["port_pos_nodes"]])
    fine_box = (P[:, 0].min() - a.fine_margin, P[:, 1].min() - a.fine_margin, P[:, 0].max() + a.fine_margin, P[:, 1].max() + a.fine_margin)
    fine_h = a.fine_h if a.fine_h > 0 else None
    result["discretisation"] = dict(h=a.h, fine_h=fine_h, fine_box_um=list(fine_box), top_h=a.top_h)
    for v in variants:
        opt = VARIANTS[v]
        print(f"=== variant {v}: {opt['label']} h={a.h}", flush=True)
        if a.skip_sweep:
            prev = json.load(open(os.path.join(a.out, "result.json")))
            result = prev
            Z = np.array(prev["variants"][v]["Z_re"]) + 1j * np.array(prev["variants"][v]["Z_im"])
            freqs = np.array(prev["variants"][v]["freq"])
        else:
          mdl = M.build_model(ex, a.h, top_h_um=a.top_h, gnd_via_r_factor=opt["gnd_via_r_factor"],
                            include_gnd_sheet_r=opt["include_gnd_sheet_r"], verbose=True, fine_box=fine_box, fine_h_um=fine_h)
          t = time.time()
          Z, stats = M.solve(mdl, ex, freqs, verbose=True)
          tsolve = time.time() - t
          sel = np.array([all_idx.index(i) for i in idx])
          fr_m, zmin_m = resonance(freqs[[all_idx.index(i) for i in ridx]], Z[[all_idx.index(i) for i in ridx]])
          g = gates(freqs[sel], Z[sel], zref[idx], fr_m, zmin_m, fr_ref, zmin_ref)
          # breakdown at 10 kHz and 1 MHz
          bd = {}
          for fb in (1e4, 1e6):
              Zb, _, Vs = M.solve(mdl, ex, [fb], verbose=False, want_voltages=True)
              bd[f"{fb:.0e}"] = M.loss_breakdown(mdl, ex, fb, Vs[0])
          info = {k: vv for k, vv in mdl.info.items()}
          result["variants"][v] = dict(label=opt["label"], options=opt, h_um=a.h, top_h_um=a.top_h, unknowns=mdl.n, model_info=info,
                                       freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
                                       rel_err=[float(x) for x in np.abs(Z - zref[all_idx]) / np.abs(zref[all_idx])],
                                       dRe_mOhm=[float(x) for x in (Z.real - zref[all_idx].real) * 1e3],
                                       dL_pH=[float(x) for x in (Z.imag - zref[all_idx].imag) / (2 * np.pi * freqs) * 1e12],
                                       gates=g, solve_seconds_total=tsolve, per_freq_stats=stats, breakdown=bd)
          plots[f"{v}: {VARIANTS[v]['label'].split(':')[1][:40]} (h={a.h:g}/{a.fine_h:g}um)"] = dict(freq=result["variants"][v]["freq"], Z_re=result["variants"][v]["Z_re"], Z_im=result["variants"][v]["Z_im"])
          print({k: (gg.get("PASS"), {kk: vv for kk, vv in gg.items() if kk in ("max_abs_dRe_mOhm", "max_abs_pH", "rel_err", "max_rel_err", "f_res_model", "f_res_rel_err")} if k != "G5" else None) for k, gg in g.items()}, flush=True)
        if a.h_check > 0:
            cf = np.array([1e6, 1e7])
            if not a.skip_sweep:
                del mdl
            ratio = a.h_check / a.h
            mdl2 = M.build_model(ex, a.h_check, top_h_um=a.top_h * ratio if a.top_h else None, gnd_via_r_factor=opt["gnd_via_r_factor"],
                                 include_gnd_sheet_r=opt["include_gnd_sheet_r"], verbose=True, fine_box=fine_box,
                                 fine_h_um=fine_h * ratio if fine_h else None)
            Z2, st2 = M.solve(mdl2, ex, cf, verbose=True)
            Z1 = np.array([Z[int(np.argmin(abs(freqs - x)))] for x in cf])
            result["convergence"][v] = dict(h=a.h, h_half=a.h_check, top_h=a.top_h, top_h_half=a.top_h * ratio, fine_h=fine_h,
                                            fine_h_half=fine_h * ratio if fine_h else None, unknowns_h=result["variants"][v]["unknowns"],
                                            unknowns_h_half=mdl2.n, freq=list(cf), Z_h=[[z.real, z.imag] for z in Z1],
                                            Z_h_half=[[z.real, z.imag] for z in Z2], rel_change=[rel(z1, z2) for z1, z2 in zip(Z1, Z2)],
                                            rel_err_h_half=[rel(z2, zref[int(np.argmin(abs(fref - x)))]) for z2, x in zip(Z2, cf)],
                                            stats_h_half=st2)
            print("convergence", result["convergence"][v]["rel_change"], flush=True)
            del mdl2
    result["peak_rss_MB" + ("_convergence_run" if a.skip_sweep else "")] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    result["wall_seconds" + ("_convergence_run" if a.skip_sweep else "")] = time.time() - T0
    tag = "_".join(variants)
    with open(os.path.join(a.out, "result.json" if tag == "A_B" or tag == "A" else f"result_{tag}.json"), "w") as fh:
        json.dump(result, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if not a.skip_sweep:
        plot(os.path.join(a.out, "exp1_Z18.png"), plots, fref, zref)
    print("done", result["wall_seconds"], "s; peak RSS MB", result["peak_rss_MB"])


if __name__ == "__main__":
    main()
