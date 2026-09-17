#!/usr/bin/env python
"""EXP-29: is the R deficit dRe = Re Zref - Re Zmodel (at the 100 kHz ladder point) proportional
to the model's path R (planes+vias+traces), or to the remainder (decap ESR + pad links)? See
WORK_DIR/exp29/EXP29_PLAN.md for the frozen definitions (Section 1) and thresholds (Section 2) --
do not edit that file, do not change the numbers here. No model is run; this only re-reads
existing receipts from EXP-28 (exp28/p), 92 ports.

    python an29.py

Reads WORK_DIR/exp28/result_260729_*_any_p.json (same glob + skip pattern as exp19/an19.py).
Writes WORK_DIR/exp29/exp29.json and exp29_dR_vs_Rpath.png; prints a markdown summary.
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import WORK_DIR, work_file  # noqa: E402

RECEIPT_RE = re.compile(r"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_p\.json$")

# frozen thresholds, EXP29_PLAN.md Section 2
M1_R2_MIN = 0.8
M1_ALPHA_LO, M1_ALPHA_HI = 0.3, 1.0
ELEM_UNIFORM_LO, ELEM_UNIFORM_HI = 0.5, 2.0
ELEM_DOMINANCE_RATIO = 2.0


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
    dR = re_ref_mOhm - re_model_mOhm

    return dict(port=r["port"], tag=r["tag"], rail=r["rail"], f_ladder_Hz=float(f[i100k]),
                Re_Zmodel_mOhm=re_model_mOhm, Re_Zref_mOhm=re_ref_mOhm,
                R_planes_mOhm=R_planes, R_vias_mOhm=R_vias, R_traces_mOhm=R_traces,
                R_path_mOhm=R_path, R_rem_mOhm=R_rem, dR_mOhm=dR,
                ref_over_model=(re_ref_mOhm / re_model_mOhm) if re_model_mOhm != 0 else float("nan"))


def r2_about_mean(y, yhat):
    y = np.asarray(y, dtype=float)
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def fit_through_origin(X, y):
    """OLS through the origin via lstsq. X: (n,k), y: (n,). Returns (coefs, r2)."""
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ coefs
    return coefs, r2_about_mean(y, yhat)


def robust_ratio_stats(dR, R_path):
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(R_path != 0, dR / R_path, np.nan)
    ratio = ratio[np.isfinite(ratio)]
    if ratio.size == 0:
        return dict(n=0, median=None, q1=None, q3=None, iqr=None)
    q1, med, q3 = np.percentile(ratio, [25, 50, 75])
    return dict(n=int(ratio.size), median=float(med), q1=float(q1), q3=float(q3), iqr=float(q3 - q1))


def h_path_verdict(r2_m1, alpha, r2_m2):
    adopt = r2_m1 >= M1_R2_MIN and M1_ALPHA_LO <= alpha <= M1_ALPHA_HI
    reject = r2_m2 > r2_m1
    if adopt:
        return "adopt"
    if reject:
        return "reject"
    return "indeterminate"


def h_elem_verdict(a_p, a_v, a_t):
    coefs = dict(a_p=a_p, a_v=a_v, a_t=a_t)
    negatives = [k for k, v in coefs.items() if v < 0]
    pos_vals = [v for v in coefs.values() if v > 0]

    uniform = False
    if len(pos_vals) >= 2:
        ratios = [pos_vals[i] / pos_vals[j] for i in range(len(pos_vals)) for j in range(len(pos_vals)) if i != j]
        uniform = all(ELEM_UNIFORM_LO <= r <= ELEM_UNIFORM_HI for r in ratios)

    largest_key = max(coefs, key=lambda k: coefs[k])
    if uniform:
        verdict = "uniform"
    elif largest_key == "a_v" and max(a_p, a_t) != 0 and a_v / max(a_p, a_t) > ELEM_DOMINANCE_RATIO:
        verdict = "via"
    elif largest_key == "a_p" and max(a_v, a_t) != 0 and a_p / max(a_v, a_t) > ELEM_DOMINANCE_RATIO:
        verdict = "plane"
    else:
        verdict = "mixed"
    return verdict, negatives


def plot_dR_vs_Rpath(rows, coefs_m1, out_png):
    R_path = np.array([p["R_path_mOhm"] for p in rows])
    dR = np.array([p["dR_mOhm"] for p in rows])
    ok = (R_path > 0) & (dR > 0)  # log-log axes need positive values
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(R_path[ok], dR[ok], color="steelblue", edgecolor="black", s=28, zorder=3)
    n_dropped = int((~ok).sum())
    if n_dropped:
        ax.text(0.02, 0.02, f"{n_dropped} port(s) with non-positive R_path or dR omitted (log-log axes)",
                 transform=ax.transAxes, fontsize=7, color="gray")
    xs = np.array([max(R_path[ok].min(), 1e-6), R_path[ok].max()])
    ax.plot(xs, coefs_m1[0] * xs, color="crimson", lw=2, label=f"M1: dR = {coefs_m1[0]:.3f}*R_path")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("R_path = R_planes + R_vias + R_traces (mOhm, @100kHz ladder pt)")
    ax.set_ylabel("dR = Re Zref - Re Zmodel (mOhm)")
    ax.set_title("EXP-29: dR vs R_path (92 ports, exp28/p)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    receipts = load_receipts()
    rows = [per_port_row(r) for r in receipts]
    n = len(rows)

    R_path = np.array([p["R_path_mOhm"] for p in rows])
    R_rem = np.array([p["R_rem_mOhm"] for p in rows])
    R_planes = np.array([p["R_planes_mOhm"] for p in rows])
    R_vias = np.array([p["R_vias_mOhm"] for p in rows])
    R_traces = np.array([p["R_traces_mOhm"] for p in rows])
    dR = np.array([p["dR_mOhm"] for p in rows])

    (alpha,), r2_m1 = fit_through_origin(R_path[:, None], dR)
    (beta,), r2_m2 = fit_through_origin(R_rem[:, None], dR)
    (alpha3, beta3), r2_m3 = fit_through_origin(np.column_stack([R_path, R_rem]), dR)
    (a_p, a_v, a_t), r2_elem = fit_through_origin(np.column_stack([R_planes, R_vias, R_traces]), dR)

    path_verdict = h_path_verdict(r2_m1, alpha, r2_m2)
    elem_verdict, elem_negatives = h_elem_verdict(a_p, a_v, a_t)

    under_ratio_mask = np.array([p["ref_over_model"] < 1 for p in rows])
    n_under = int(under_ratio_mask.sum())

    robust_all = robust_ratio_stats(dR, R_path)
    robust_excl = robust_ratio_stats(dR[~under_ratio_mask], R_path[~under_ratio_mask])

    out = dict(
        n_ports=n,
        fits=dict(
            M1=dict(formula="dR = alpha*R_path", alpha=float(alpha), r2=float(r2_m1)),
            M2=dict(formula="dR = beta*R_rem", beta=float(beta), r2=float(r2_m2)),
            M3=dict(formula="dR = alpha*R_path + beta*R_rem", alpha=float(alpha3), beta=float(beta3), r2=float(r2_m3)),
            element=dict(formula="dR = a_p*R_planes + a_v*R_vias + a_t*R_traces",
                         a_p=float(a_p), a_v=float(a_v), a_t=float(a_t), r2=float(r2_elem),
                         negative_coefs=elem_negatives),
        ),
        robust=dict(
            all_ports=robust_all,
            excl_ref_lt_model=dict(n_excluded=n_under, **robust_excl),
        ),
        verdicts=dict(H_path=path_verdict, H_elem=elem_verdict),
        ports=rows,
    )

    json_fn = work_file("exp29", "exp29.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp29", "exp29_dR_vs_Rpath.png")
    plot_dR_vs_Rpath(rows, (alpha,), png_fn)

    print(f"\n### EXP-29 dR vs R_path structure (exp28/p, n={n} ports)\n")
    print("| fit | coefs | R2 |")
    print("|---|---|---|")
    print(f"| M1: dR=alpha*R_path | alpha={alpha:.4f} | {r2_m1:.4f} |")
    print(f"| M2: dR=beta*R_rem | beta={beta:.4f} | {r2_m2:.4f} |")
    print(f"| M3: dR=alpha*R_path+beta*R_rem | alpha={alpha3:.4f}, beta={beta3:.4f} | {r2_m3:.4f} |")
    print(f"| element: dR=a_p*R_planes+a_v*R_vias+a_t*R_traces | a_p={a_p:.4f}, a_v={a_v:.4f}, "
          f"a_t={a_t:.4f} | {r2_elem:.4f} |")
    print(f"\nnegative element coefficients: {elem_negatives if elem_negatives else 'none'}")

    print(f"\n**H_path verdict: {path_verdict}** (R2(M1)={r2_m1:.4f} vs threshold {M1_R2_MIN}, "
          f"alpha={alpha:.4f} vs [{M1_ALPHA_LO},{M1_ALPHA_HI}]; R2(M2)={r2_m2:.4f})")
    print(f"**H_elem verdict: {elem_verdict}**")

    print("\n#### robust dR/R_path ratio\n")
    print("| subset | n | median | Q1 | Q3 | IQR |")
    print("|---|---|---|---|---|---|")
    ra = robust_all
    print(f"| all ports | {ra['n']} | {ra['median']} | {ra['q1']} | {ra['q3']} | {ra['iqr']} |")
    re_ = robust_excl
    print(f"| excl. {n_under} ports w/ Re Zref/Re Zmodel < 1 | {re_['n']} | {re_['median']} | "
          f"{re_['q1']} | {re_['q3']} | {re_['iqr']} |")

    print("\n#### 8 ports with largest |residual| under M1 (dR - alpha*R_path)\n")
    print("| port | rail | R_path (mOhm) | dR (mOhm) | resid (mOhm) |")
    print("|---|---|---|---|---|")
    resid = dR - alpha * R_path
    order = np.argsort(-np.abs(resid))[:8]
    for idx in order:
        p = rows[idx]
        print(f"| {p['port']} | {p['rail']} | {p['R_path_mOhm']:.4f} | {p['dR_mOhm']:.4f} | {resid[idx]:.4f} |")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
