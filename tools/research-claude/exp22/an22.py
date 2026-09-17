#!/usr/bin/env python
"""EXP-22: decompose EXP-19's H1' D' (+135 pH median) into reference-L-vs-frequency decay vs
model-L-vs-frequency behavior, and check whether the decap SPICE model (common to both) drives it.
See WORK_DIR/exp22/EXP22_PLAN.md for the frozen definitions (Section 1) and thresholds (Section 2)
-- do not edit that file, do not change the numbers here. No model is run; this only re-reads
existing receipts from EXP-15 (exp15/j, 92 ports) and decap counts cached by EXP-5.

    python an22.py

Reads WORK_DIR/exp15/result_260729_*_any_j.json (same glob + skip pattern as exp19/an19.py) and
WORK_DIR/exp5/extract_260729_{port}.pkl (decap count, via exp16/corr16.decap_count). Writes
WORK_DIR/exp22/exp22.json and exp22_Lratio.png; prints a markdown summary.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
sys.path.insert(0, os.path.join(HERE, "..", "exp16"))
from paths import WORK_DIR, work_file  # noqa: E402
from corr16 import decap_count  # noqa: E402

RECEIPT_RE = re.compile(r"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_j\.json$")
FRES_ELIGIBLE_MAX = 5e6   # f_res_ref must be <= this for a port to be eligible, EXP22_PLAN.md S1
F1_FLOOR = 5e6
F2_TARGET = 3e7


def load_receipts():
    d = WORK_DIR / "exp15"
    rows = []
    for fn in sorted(glob.glob(os.path.join(d, "result_260729_*_any_j.json"))):
        if not RECEIPT_RE.match(os.path.basename(fn)):
            continue
        rows.append(json.loads(open(fn, encoding="utf-8").read()))
    return rows


def per_port_row(r):
    f = np.asarray(r["freq"], dtype=float)
    Zim = np.asarray(r["Z_im"], dtype=float)
    Zrefim = np.asarray(r["Zref_im"], dtype=float)
    f_res_ref = float(r["f_res_ref"])

    i1 = int(np.argmin(np.abs(f - max(2 * f_res_ref, F1_FLOOR))))
    i2 = int(np.argmin(np.abs(f - F2_TARGET)))

    L_ref_pH = Zrefim / (2 * np.pi * f) * 1e12
    L_mod_pH = Zim / (2 * np.pi * f) * 1e12
    L_ref_f1, L_ref_f2 = float(L_ref_pH[i1]), float(L_ref_pH[i2])
    L_mod_f1, L_mod_f2 = float(L_mod_pH[i1]), float(L_mod_pH[i2])

    eligible = bool(f_res_ref <= FRES_ELIGIBLE_MAX and L_ref_f1 > 0 and L_mod_f1 > 0)
    if eligible:
        rho_ref = L_ref_f2 / L_ref_f1
        rho_mod = L_mod_f2 / L_mod_f1
        dL_ref = L_ref_f2 - L_ref_f1
        dL_mod = L_mod_f2 - L_mod_f1
    else:
        rho_ref = rho_mod = dL_ref = dL_mod = None

    return dict(port=r["port"], tag=r["tag"], rail=r["rail"], f_res_ref_Hz=f_res_ref,
                f1_Hz=float(f[i1]), f2_Hz=float(f[i2]),
                L_ref_f1_pH=L_ref_f1, L_ref_f2_pH=L_ref_f2,
                L_mod_f1_pH=L_mod_f1, L_mod_f2_pH=L_mod_f2,
                rho_ref=rho_ref, rho_mod=rho_mod, dL_ref_pH=dL_ref, dL_mod_pH=dL_mod,
                eligible=eligible, decaps=decap_count(r["tag"], r["port"]))


def hyp_Hr(rows):
    vals = np.array([p["rho_ref"] for p in rows if p["eligible"]])
    n = len(vals)
    if n == 0:
        return dict(n=0, median=None, frac_below_1=None, verdict="indeterminate")
    med = float(np.median(vals))
    frac = float(np.mean(vals < 1))
    if med < 0.9 and frac >= 0.7:
        v = "adopt"
    elif med >= 0.97:
        v = "reject"
    else:
        v = "indeterminate"
    return dict(n=n, median=med, frac_below_1=frac, verdict=v)


def hyp_Hm(rows):
    vals = np.array([p["rho_mod"] for p in rows if p["eligible"]])
    n = len(vals)
    if n == 0:
        return dict(n=0, median=None, verdict="reject")
    med = float(np.median(vals))
    return dict(n=n, median=med, verdict="adopt" if 0.97 <= med <= 1.03 else "reject")


def hyp_Hdec(rows):
    pts = [(p["dL_ref_pH"], p["decaps"]) for p in rows if p["eligible"] and p["decaps"] is not None]
    n = len(pts)
    if n < 3:
        return dict(n=n, rho=None, p=None, verdict="indeterminate (insufficient n)")
    dL = np.array([x[0] for x in pts])
    dec = np.array([x[1] for x in pts])
    rho, pval = stats.spearmanr(dL, dec)
    a = abs(rho)
    v = "adopt" if a < 0.3 else "reject" if a >= 0.5 else "indeterminate"
    return dict(n=n, rho=float(rho), p=float(pval), verdict=v)


def plot_Lratio(rows, out_png):
    elig = [p for p in rows if p["eligible"]]
    x = np.array([p["rho_ref"] for p in elig])
    y = np.array([p["rho_mod"] for p in elig])
    med_x = float(np.median(x)) if len(x) else float("nan")
    med_y = float(np.median(y)) if len(y) else float("nan")

    fig, ax = plt.subplots(figsize=(7, 6.5))
    ax.scatter(x, y, color="steelblue", edgecolor="black", s=32)
    ax.axhline(1, color="gray", lw=0.8, ls="--")
    ax.axvline(1, color="gray", lw=0.8, ls="--")
    ax.set_xlabel("rho_ref = L_ref(f2)/L_ref(f1)")
    ax.set_ylabel("rho_mod = L_mod(f2)/L_mod(f1)")
    ax.set_title(f"EXP-22: L ratio (n={len(x)})\nmedian rho_ref={med_x:.4f}, median rho_mod={med_y:.4f}")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    receipts = load_receipts()
    rows = [per_port_row(r) for r in receipts]
    n_eligible = sum(1 for p in rows if p["eligible"])

    hr = hyp_Hr(rows)
    hm = hyp_Hm(rows)
    hdec = hyp_Hdec(rows)

    rho_ref_vals = np.array([p["rho_ref"] for p in rows if p["eligible"]])
    rho_mod_vals = np.array([p["rho_mod"] for p in rows if p["eligible"]])
    q_ref = [float(x) for x in np.percentile(rho_ref_vals, [25, 50, 75])] if len(rho_ref_vals) else [None] * 3
    q_mod = [float(x) for x in np.percentile(rho_mod_vals, [25, 50, 75])] if len(rho_mod_vals) else [None] * 3

    out = dict(n=len(rows), n_eligible=n_eligible, ports=rows,
               hypotheses=dict(H_r=hr, H_m=hm, H_dec=hdec),
               rho_ref_quartiles=q_ref, rho_mod_quartiles=q_mod)

    json_fn = work_file("exp22", "exp22.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp22", "exp22_Lratio.png")
    plot_Lratio(rows, png_fn)

    print(f"\n### EXP-22 L(f) decomposition (n={len(rows)} ports, n_eligible={n_eligible})\n")
    print("| hypothesis | stat | verdict |")
    print("|---|---|---|")
    print(f"| H_r rho_ref decreasing | median={hr['median']}, frac<1={hr['frac_below_1']} (n={hr['n']}) | {hr['verdict']} |")
    print(f"| H_m rho_mod ~ constant | median={hm['median']} (n={hm['n']}) | {hm['verdict']} |")
    print(f"| H_dec rho(dL_ref, decaps) | rho={hdec['rho']}, p={hdec['p']} (n={hdec['n']}) | {hdec['verdict']} |")
    print(f"\nrho_ref quartiles (Q1/median/Q3): {q_ref}")
    print(f"rho_mod quartiles (Q1/median/Q3): {q_mod}")
    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
