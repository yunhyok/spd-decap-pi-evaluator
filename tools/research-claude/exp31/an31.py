#!/usr/bin/env python
"""EXP-31: scale-free element regression for the package R deficit (92 ports, exp28/p only).

See WORK_DIR/exp31/EXP31_PLAN.md for the frozen definitions (Section 1) and hypothesis
thresholds (Section 2) -- do not edit that file, do not change the numbers here. No model is
run; this only re-reads existing receipts from EXP-28 (exp28/p), tag 260729, 92 ports.

At the 100 kHz ladder point, per port:
    y = Re Zref/Re Zmodel - 1
    s_planes, s_vias, s_traces = R_el/Re Zmodel  (R_el in the same units as Re Zmodel)
    s_rem = 1 - (s_planes + s_vias + s_traces)
Fit y = sum_el c_el * s_el through the origin with scipy.optimize.nnls (c_el >= 0).
R^2 = 1 - SS_res/SS_tot, SS_tot about the mean of y.
Bootstrap: 1000 resamples of ports (seed 0), distribution of each c_el and of R^2.
Run for (A) all 92 ports and (B) excluding ports with Re Zref/Re Zmodel < 1.

    python an31.py

Reads WORK_DIR/exp28/result_260729_*_any_p.json (same glob + skip pattern as exp19/an19.py,
exp29/an29.py). Writes WORK_DIR/exp31/exp31.json and exp31_coeffs.png; prints a markdown summary.
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
from scipy.optimize import nnls  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import WORK_DIR, work_file  # noqa: E402

RECEIPT_RE = re.compile(r"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_p\.json$")

ELEMS = ("planes", "vias", "traces", "rem")

# frozen thresholds, EXP31_PLAN.md Section 2
H_VIA_C_VIAS_LO = 0.5
H_VIA_C_PLANES_HI = 0.3
H_PLANE_C_PLANES_LO = 0.3
H_REM_C_REM_LO = 0.3
H_FIT_R2_MIN = 0.5

N_BOOT = 1000
BOOT_SEED = 0

KNOWN_PORTS = ["Port1_SITE0", "Port7_SITE0", "Port14_SITE0", "Port16_SITE0", "Port18_SITE0", "Port19_SITE0"]
KNOWN_LABELS = {"Port1_SITE0": "P1", "Port7_SITE0": "P7", "Port14_SITE0": "P14",
                "Port16_SITE0": "P16", "Port18_SITE0": "P18", "Port19_SITE0": "P19"}


def load_receipts():
    d = WORK_DIR / "exp28"
    rows = []
    for fn in sorted(glob.glob(os.path.join(d, "result_260729_*_any_p.json"))):
        base = os.path.basename(fn)
        if not RECEIPT_RE.match(base):
            continue
        rows.append(json.loads(open(fn, encoding="utf-8").read()))
    return rows


def per_port_row(r):
    f = np.asarray(r["freq"], dtype=float)
    i100k = int(np.argmin(np.abs(f - 1e5)))

    re_model_mOhm = float(r["Z_re"][i100k]) * 1e3
    re_ref_mOhm = float(r["Zref_re"][i100k]) * 1e3

    bd = r["breakdown_100k"]
    R_planes = sum(v["R_mOhm"] for k, v in bd.items() if k not in ("rail_vias", "rail_traces"))
    R_vias = bd["rail_vias"]["mOhm"]
    R_traces = bd["rail_traces"]["mOhm"]
    R_path = R_planes + R_vias + R_traces
    R_rem = re_model_mOhm - R_path

    y = re_ref_mOhm / re_model_mOhm - 1.0
    s_planes = R_planes / re_model_mOhm
    s_vias = R_vias / re_model_mOhm
    s_traces = R_traces / re_model_mOhm
    s_rem = 1.0 - (s_planes + s_vias + s_traces)

    return dict(port=r["port"], tag=r["tag"], rail=r["rail"], f_ladder_Hz=float(f[i100k]),
                Re_Zmodel_mOhm=re_model_mOhm, Re_Zref_mOhm=re_ref_mOhm,
                ref_over_model=re_ref_mOhm / re_model_mOhm,
                R_planes_mOhm=R_planes, R_vias_mOhm=R_vias, R_traces_mOhm=R_traces, R_rem_mOhm=R_rem,
                y=y, s_planes=s_planes, s_vias=s_vias, s_traces=s_traces, s_rem=s_rem)


def r2_about_mean(y, yhat):
    y = np.asarray(y, dtype=float)
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def fit_nnls(S, y):
    coefs, _ = nnls(S, y)
    yhat = S @ coefs
    return coefs, r2_about_mean(y, yhat)


def bootstrap_nnls(S, y, n_boot=N_BOOT, seed=BOOT_SEED):
    rng = np.random.default_rng(seed)
    n = len(y)
    boot_coefs = np.empty((n_boot, S.shape[1]))
    boot_r2 = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        coefs, r2 = fit_nnls(S[idx], y[idx])
        boot_coefs[b] = coefs
        boot_r2[b] = r2
    return boot_coefs, boot_r2


def percentiles(a):
    p2_5, p50, p97_5 = np.percentile(a, [2.5, 50, 97.5], axis=0)
    return p2_5, p50, p97_5


def analyze_subset(rows):
    S = np.array([[p["s_planes"], p["s_vias"], p["s_traces"], p["s_rem"]] for p in rows])
    y = np.array([p["y"] for p in rows])
    coefs, r2 = fit_nnls(S, y)
    boot_coefs, boot_r2 = bootstrap_nnls(S, y)
    c_lo, c_med, c_hi = percentiles(boot_coefs)
    r2_lo, r2_med, r2_hi = percentiles(boot_r2)
    fit = dict(n=len(rows), r2_point=float(r2),
               coefs=dict(zip(ELEMS, (float(c) for c in coefs))),
               bootstrap=dict(
                   coefs={el: dict(p2_5=float(c_lo[i]), p50=float(c_med[i]), p97_5=float(c_hi[i]))
                          for i, el in enumerate(ELEMS)},
                   r2=dict(p2_5=float(r2_lo), p50=float(r2_med), p97_5=float(r2_hi))))
    return fit, S, y, coefs, boot_coefs, boot_r2


def h_via_verdict(fit):
    b = fit["bootstrap"]["coefs"]
    cond_via = b["vias"]["p2_5"] >= H_VIA_C_VIAS_LO
    cond_planes_capped = b["planes"]["p97_5"] <= H_VIA_C_PLANES_HI
    cond_plane = b["planes"]["p2_5"] >= H_PLANE_C_PLANES_LO
    cond_rem = b["rem"]["p2_5"] >= H_REM_C_REM_LO
    conditions = dict(via_lower95_ge_0p5=cond_via, planes_upper95_le_0p3=cond_planes_capped,
                       planes_lower95_ge_0p3=cond_plane, rem_lower95_ge_0p3=cond_rem)
    if cond_via and cond_planes_capped:
        verdict = "H_via adopt"
    elif cond_plane:
        verdict = "plane"
    elif cond_rem:
        verdict = "rem"
    else:
        verdict = "indeterminate"
    return verdict, conditions


def h_fit_verdict(fit):
    med_r2 = fit["bootstrap"]["r2"]["p50"]
    return "adopt" if med_r2 >= H_FIT_R2_MIN else "reject", med_r2


def plot_coeffs(bootA, bootB, out_png):
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), sharey=True)
    for ax, boot, title in ((axes[0], bootA, "A: all 92 ports"), (axes[1], bootB, "B: excl. ref/model < 1")):
        data = [boot[:, i] for i in range(len(ELEMS))]
        parts = ax.violinplot(data, showmedians=False, showextrema=False)
        for pc in parts["bodies"]:
            pc.set_facecolor("steelblue")
            pc.set_alpha(0.35)
        ax.boxplot(data, widths=0.15, showfliers=False,
                   medianprops=dict(color="crimson", lw=2))
        ax.set_xticks(range(1, len(ELEMS) + 1))
        ax.set_xticklabels(ELEMS)
        ax.axhline(0, color="gray", lw=0.7)
        ax.set_title(title)
        ax.set_ylabel("bootstrap c_el")
    fig.suptitle("EXP-31: bootstrap distributions of c_el (nnls, 1000 resamples)")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    receipts = load_receipts()
    rows = [per_port_row(r) for r in receipts]
    n = len(rows)

    under_mask = [p["ref_over_model"] < 1 for p in rows]
    rows_A = rows
    rows_B = [p for p, u in zip(rows, under_mask) if not u]
    n_excl = sum(under_mask)

    fitA, SA, yA, coefsA, bootA_c, bootA_r2 = analyze_subset(rows_A)
    fitB, SB, yB, coefsB, bootB_c, bootB_r2 = analyze_subset(rows_B)

    via_verdict, via_conditions = h_via_verdict(fitA)
    fit_verdict, fit_med_r2 = h_fit_verdict(fitA)

    residA = yA - SA @ coefsA
    order = np.argsort(-np.abs(residA))[:8]
    top_resid = [dict(port=rows_A[i]["port"], rail=rows_A[i]["rail"], y=float(yA[i]),
                       yhat=float((SA @ coefsA)[i]), residual=float(residA[i]))
                 for i in order]

    known = {}
    for pname in KNOWN_PORTS:
        match = [p for p in rows if p["port"] == pname]
        if match:
            p = match[0]
            known[KNOWN_LABELS[pname]] = dict(port=pname, s_planes=p["s_planes"], s_vias=p["s_vias"],
                                                s_traces=p["s_traces"], s_rem=p["s_rem"], y=p["y"])
    known["804"] = "not present -- 260729 only"

    out = dict(
        n_ports=n,
        n_excluded_ref_lt_model=int(n_excl),
        elements=list(ELEMS),
        fits=dict(A=fitA, B=fitB),
        verdicts=dict(H_via=dict(verdict=via_verdict, conditions=via_conditions),
                      H_fit=dict(verdict=fit_verdict, median_bootstrap_r2=fit_med_r2)),
        top8_residual_A=top_resid,
        known_cases=known,
        ports=rows,
    )

    json_fn = work_file("exp31", "exp31.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp31", "exp31_coeffs.png")
    plot_coeffs(bootA_c, bootB_c, png_fn)

    print(f"\n### EXP-31 element-scale regression (exp28/p, tag 260729, n={n} ports)\n")

    for label, fit in (("A: all ports", fitA), (f"B: excl. {n_excl} ports w/ ref/model < 1", fitB)):
        print(f"\n#### {label} (n={fit['n']})\n")
        print("| element | c_el (point) | boot p2.5 | boot p50 | boot p97.5 |")
        print("|---|---|---|---|---|")
        for el in ELEMS:
            c = fit["coefs"][el]
            b = fit["bootstrap"]["coefs"][el]
            print(f"| {el} | {c:.4f} | {b['p2_5']:.4f} | {b['p50']:.4f} | {b['p97_5']:.4f} |")
        r2b = fit["bootstrap"]["r2"]
        print(f"\nR2 (point) = {fit['r2_point']:.4f}; bootstrap R2 p2.5/p50/p97.5 = "
              f"{r2b['p2_5']:.4f}/{r2b['p50']:.4f}/{r2b['p97_5']:.4f}")

    print(f"\n**H_via verdict (on A): {via_verdict}**")
    print(f"conditions: {via_conditions}")
    print(f"\n**H_fit verdict (on A): {fit_verdict}** (median bootstrap R2 = {fit_med_r2:.4f} vs threshold {H_FIT_R2_MIN})")

    print("\n#### 8 ports with largest |residual| under the A point-fit (y - S@c)\n")
    print("| port | rail | y | yhat | residual |")
    print("|---|---|---|---|---|")
    for t in top_resid:
        print(f"| {t['port']} | {t['rail']} | {t['y']:.4f} | {t['yhat']:.4f} | {t['residual']:.4f} |")

    print("\n#### share vector s for the known cases (260729 only; 804 not present)\n")
    print("| case | port | s_planes | s_vias | s_traces | s_rem | y |")
    print("|---|---|---|---|---|---|---|")
    for label in ["P1", "P7", "P14", "P16", "P18", "P19"]:
        k = known.get(label)
        if isinstance(k, dict):
            print(f"| {label} | {k['port']} | {k['s_planes']:.4f} | {k['s_vias']:.4f} | "
                  f"{k['s_traces']:.4f} | {k['s_rem']:.4f} | {k['y']:.4f} |")
        else:
            print(f"| {label} | -- | -- | -- | -- | -- | -- |")
    print("| 804 | not present -- 260729 only | | | | | |")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
