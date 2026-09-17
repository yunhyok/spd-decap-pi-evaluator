#!/usr/bin/env python
"""EXP-19: (1) correlation of the low-freq R deficit with the 10-100 MHz R shortfall, (2) a
redefined HF DeltaL' metric anchored past f_res_ref. See WORK_DIR/exp19/EXP19_PLAN.md for the
frozen definitions (Section 1) and thresholds (Section 2) -- do not edit that file, do not change
the numbers here. No model is run; this only re-reads existing receipts from EXP-15 (exp15/j) and
EXP-18b (exp18/k2), 92 ports each.

    python an19.py

Reads WORK_DIR/exp15/result_260729_*_any_j.json and WORK_DIR/exp18/result_260729_*_any_k2.json
(same glob + skip pattern as exp17/dl17.py). Writes WORK_DIR/exp19/exp19.json,
exp19_rLF_vs_rHF.png and exp19_Dprime_hist.png; prints a markdown summary.
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
from paths import WORK_DIR, work_file  # noqa: E402

RECEIPT_RE_TMPL = r"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_{}\.json$"

# frozen bands / thresholds, EXP19_PLAN.md Section 1-2
LF_BAND = (1e5, 1e6)          # dL_LF median band
HF_R_BAND = (1e7, 1e8)        # r_HF median band
DPRIME_HI = 3e7               # dL_HF' upper bound
FRES_ELIGIBLE_MAX = 5e6       # f_res_ref must be <= this for a port to be eligible
MIN_HF_PRIME_PTS = 3          # at least this many points in [2*f_res_ref, 3e7]

VARIANT_SETS = {"A": ("exp15", "j"), "B": ("exp18", "k2")}


def load_receipts(dir_, variant):
    d = WORK_DIR / dir_
    receipt_re = re.compile(RECEIPT_RE_TMPL.format(re.escape(variant)))
    rows = []
    for fn in sorted(glob.glob(os.path.join(d, f"result_260729_*_any_{variant}.json"))):
        base = os.path.basename(fn)
        if not receipt_re.match(base):
            continue
        rows.append(json.loads(open(fn, encoding="utf-8").read()))
    return rows


def per_port_row(r):
    f = np.asarray(r["freq"], dtype=float)
    Zm = np.asarray(r["Z_re"], dtype=float) + 1j * np.asarray(r["Z_im"], dtype=float)
    Zr = np.asarray(r["Zref_re"], dtype=float) + 1j * np.asarray(r["Zref_im"], dtype=float)
    f_res_ref = float(r["f_res_ref"])

    # r_LF: dRe_100k_mOhm vs Re(Zref) at the receipt frequency nearest 1e5 Hz
    i_100k = int(np.argmin(np.abs(f - 1e5)))
    zref_re_100k = Zr[i_100k].real
    r_LF = (r["dRe_100k_mOhm"] * 1e-3) / zref_re_100k if zref_re_100k != 0 else float("nan")

    # r_HF: median (ReZm-ReZr)/ReZr over 1e7 <= f <= 1e8
    m_hf = (f >= HF_R_BAND[0]) & (f <= HF_R_BAND[1])
    with np.errstate(divide="ignore", invalid="ignore"):
        dR_frac = np.where(Zr.real != 0, (Zm.real - Zr.real) / Zr.real, np.nan)
    r_HF = float(np.median(dR_frac[m_hf])) if m_hf.any() else float("nan")

    # dL(f) = Im(Zm-Zr)/(2 pi f), pH
    dL_pH = (Zm - Zr).imag / (2 * np.pi * f) * 1e12
    m_lf = (f >= LF_BAND[0]) & (f <= LF_BAND[1])
    dL_LF = float(np.median(dL_pH[m_lf])) if m_lf.any() else float("nan")

    m_hfp = (f >= 2 * f_res_ref) & (f <= DPRIME_HI)
    eligible = bool(f_res_ref <= FRES_ELIGIBLE_MAX and m_hfp.sum() >= MIN_HF_PRIME_PTS)
    if eligible:
        dL_HFp = float(np.median(dL_pH[m_hfp]))
        D_prime = dL_HFp - dL_LF
    else:
        dL_HFp = None
        D_prime = None

    via_L_pH = r["breakdown_100k"]["rail_vias"]["pH"]

    return dict(port=r["port"], tag=r["tag"], rail=r["rail"], f_res_ref_Hz=f_res_ref,
                r_LF=r_LF, r_HF=r_HF, dL_LF_pH=dL_LF, dL_HFprime_pH=dL_HFp, D_prime_pH=D_prime,
                n_hfprime_pts=int(m_hfp.sum()), eligible=eligible, via_L_pH=via_L_pH)


def verdict(adopt, reject):
    if adopt:
        return "adopt"
    if reject:
        return "reject"
    return "indeterminate"


def hyp_He(rows):
    r_lf = np.array([p["r_LF"] for p in rows])
    r_hf = np.array([p["r_HF"] for p in rows])
    ok = np.isfinite(r_lf) & np.isfinite(r_hf)
    n = int(ok.sum())
    if n < 3:
        return dict(n=n, rho=None, p=None)
    rho, p = stats.spearmanr(r_lf[ok], r_hf[ok])
    return dict(n=n, rho=float(rho), p=float(p))


def hyp_H1prime(rows):
    D = np.array([p["D_prime_pH"] for p in rows if p["eligible"]])
    n = len(D)
    if n == 0:
        return dict(n=0, fraction_negative=None, median_D_prime_pH=None)
    frac_neg = float(np.mean(D < 0))
    med = float(np.median(D))
    return dict(n=n, fraction_negative=frac_neg, median_D_prime_pH=med)


def hyp_H2prime(rows):
    D = np.array([p["D_prime_pH"] for p in rows if p["eligible"]])
    via = np.array([p["via_L_pH"] for p in rows if p["eligible"]])
    n = len(D)
    if n < 3:
        return dict(n=n, rho=None, p=None)
    rho, p = stats.spearmanr(np.abs(D), via)
    return dict(n=n, rho=float(rho), p=float(p))


def plot_rLF_vs_rHF(rows_by_set, out_png):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    for ax, key in zip(axes, ("A", "B")):
        rows = rows_by_set[key]
        r_lf = np.array([p["r_LF"] for p in rows])
        r_hf = np.array([p["r_HF"] for p in rows])
        ok = np.isfinite(r_lf) & np.isfinite(r_hf)
        ax.scatter(r_lf[ok], r_hf[ok], color="steelblue", edgecolor="black", s=28)
        # symlog if the data spans a wide dynamic range across sign changes
        for arr, setter in ((r_lf[ok], ax.set_xscale), (r_hf[ok], ax.set_yscale)):
            nz = arr[arr != 0]
            wide = nz.size > 0 and (np.max(np.abs(nz)) / np.min(np.abs(nz))) > 100
            if wide:
                setter("symlog", linthresh=max(np.min(np.abs(nz)), 1e-4))
        ax.axhline(0, color="gray", lw=0.6)
        ax.axvline(0, color="gray", lw=0.6)
        rho_p = hyp_He(rows)
        rho_s = f"{rho_p['rho']:.3f}" if rho_p["rho"] is not None else "n/a"
        p_s = f"{rho_p['p']:.3g}" if rho_p["p"] is not None else "n/a"
        variant_dir, variant_tag = VARIANT_SETS[key]
        ax.set_title(f"{key} ({variant_dir}/{variant_tag}): rho={rho_s}, p={p_s}, n={rho_p['n']}")
        ax.set_xlabel("r_LF = dRe_100k / Re Zref(100kHz)")
        ax.set_ylabel("r_HF = median dR_frac, 10-100 MHz")
    fig.suptitle("EXP-19: r_LF vs r_HF")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def plot_Dprime_hist(rows_by_set, out_png):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    for ax, key in zip(axes, ("A", "B")):
        rows = rows_by_set[key]
        D = np.array([p["D_prime_pH"] for p in rows if p["eligible"]])
        variant_dir, variant_tag = VARIANT_SETS[key]
        if len(D) == 0:
            ax.set_title(f"{key} ({variant_dir}/{variant_tag}): n_eligible=0")
            continue
        ax.hist(D, bins=min(20, max(5, len(D) // 3)), color="darkorange", edgecolor="black")
        med = float(np.median(D))
        ax.axvline(med, color="crimson", lw=2, label=f"median={med:.2f} pH")
        ax.axvline(0, color="gray", lw=0.7)
        ax.set_title(f"{key} ({variant_dir}/{variant_tag}): D' distribution (n={len(D)})")
        ax.set_xlabel("D' = dL_HF' - dL_LF (pH)")
        ax.set_ylabel("count")
        ax.legend()
    fig.suptitle("EXP-19: D' histogram (eligible ports)")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    rows_by_set = {}
    for key, (dir_, variant) in VARIANT_SETS.items():
        receipts = load_receipts(dir_, variant)
        rows_by_set[key] = [per_port_row(r) for r in receipts]

    he = {k: hyp_He(rows_by_set[k]) for k in VARIANT_SETS}
    h1 = {k: hyp_H1prime(rows_by_set[k]) for k in VARIANT_SETS}
    h2 = {k: hyp_H2prime(rows_by_set[k]) for k in VARIANT_SETS}

    he_verdict = verdict(he["A"]["rho"] is not None and he["A"]["rho"] >= 0.5 and he["A"]["p"] < 0.01,
                          he["A"]["rho"] is not None and he["A"]["rho"] < 0.2)
    h1_verdict = verdict(h1["A"]["fraction_negative"] is not None and h1["A"]["fraction_negative"] >= 0.6
                          and h1["A"]["median_D_prime_pH"] <= -3.0,
                          h1["A"]["fraction_negative"] is not None and
                          (h1["A"]["fraction_negative"] < 0.5 or h1["A"]["median_D_prime_pH"] > 0.0))
    h2_verdict = verdict(h2["A"]["rho"] is not None and h2["A"]["rho"] >= 0.5 and h2["A"]["p"] < 0.01,
                          h2["A"]["rho"] is not None and h2["A"]["rho"] < 0.2)

    out = dict(
        variant_sets={k: dict(dir=v[0], variant=v[1], n=len(rows_by_set[k])) for k, v in VARIANT_SETS.items()},
        ports={k: rows_by_set[k] for k in VARIANT_SETS},
        n_eligible={k: h1[k]["n"] for k in VARIANT_SETS},
        hypotheses=dict(
            H_e=dict(A=he["A"], B=he["B"], verdict=he_verdict),
            H1_prime=dict(A=h1["A"], B=h1["B"], verdict=h1_verdict),
            H2_prime=dict(A=h2["A"], B=h2["B"], verdict=h2_verdict),
        ),
    )

    json_fn = work_file("exp19", "exp19.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png1 = work_file("exp19", "exp19_rLF_vs_rHF.png")
    plot_rLF_vs_rHF(rows_by_set, png1)
    png2 = work_file("exp19", "exp19_Dprime_hist.png")
    plot_Dprime_hist(rows_by_set, png2)

    print(f"\n### EXP-19 r_LF/r_HF correlation and D' redefinition "
          f"(A=exp15/j n={len(rows_by_set['A'])}, B=exp18/k2 n={len(rows_by_set['B'])})\n")
    print("| hypothesis | A (primary) | B (reported) | verdict (from A) |")
    print("|---|---|---|---|")
    print(f"| H_e rho(r_LF,r_HF) | rho={he['A']['rho']}, p={he['A']['p']} (n={he['A']['n']}) "
          f"| rho={he['B']['rho']}, p={he['B']['p']} (n={he['B']['n']}) | {he_verdict} |")
    print(f"| H1' D'<0 frac / median | frac={h1['A']['fraction_negative']}, "
          f"median={h1['A']['median_D_prime_pH']} pH (n_elig={h1['A']['n']}) "
          f"| frac={h1['B']['fraction_negative']}, median={h1['B']['median_D_prime_pH']} pH "
          f"(n_elig={h1['B']['n']}) | {h1_verdict} |")
    print(f"| H2' rho(|D'|,via L) | rho={h2['A']['rho']}, p={h2['A']['p']} (n_elig={h2['A']['n']}) "
          f"| rho={h2['B']['rho']}, p={h2['B']['p']} (n_elig={h2['B']['n']}) | {h2_verdict} |")

    print("\n#### 10 ports with most negative r_HF (set A, exp15/j) and their r_LF\n")
    print("| port | rail | r_HF | r_LF |")
    print("|---|---|---|---|")
    a_sorted = sorted((p for p in rows_by_set["A"] if np.isfinite(p["r_HF"])), key=lambda p: p["r_HF"])[:10]
    for p in a_sorted:
        print(f"| {p['port']} | {p['rail']} | {p['r_HF']:.4f} | {p['r_LF']:.4f} |")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png1}")
    print(f"wrote {png2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
