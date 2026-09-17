#!/usr/bin/env python
"""EXP-17: frequency structure of the 10-100 MHz error -- DeltaL(f)/DeltaR(f) spectra over the EXP-15
92-port cavity-wall (variant j) receipts. See WORK_DIR/exp17/EXP17_PLAN.md for the frozen definitions
and thresholds (do not edit that file). No model is run; this only re-reads existing receipts.

    python dl17.py

Reads WORK_DIR/exp15/result_260729_*_any_j.json (skips resume duplicates), plus the 7 exp13/j baseline
vs exp14/h (via_L_twowire) receipts for the side table. Writes WORK_DIR/exp17/exp17_spectra.json,
exp17_dL_spectrum.png and exp17_D_vs_viaL.png; prints a markdown summary. Runs on however many EXP-15
receipts currently exist (n can be as low as 2); every hypothesis verdict reports its n, and n below a
handful is reported as "indeterminate (insufficient n)" instead of a computed number.
"""
from __future__ import annotations

import argparse
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
from paths import WORK_DIR, work_dir, work_file  # noqa: E402

RECEIPT_RE_TMPL = r"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_{}\.json$"
MIN_N = 5

LF_BAND = (1e5, 1e6)
HF_BAND = (1e7, 3e7)
ANTIRES_BAND = (9e6, 1.5e7)
DR_BAND = (1e7, 1e8)

# the 7 known EXP-13/j baseline <-> EXP-14/h (via_L_twowire) cases, same as exp11/table11.py CASES
SIDE_CASES = [("260729", "Port1_SITE0"), ("260729", "Port7_SITE0"), ("260729", "Port14_SITE0"),
              ("260729", "Port16_SITE0"), ("260729", "Port18_SITE0"), ("260729", "Port19_SITE0"),
              ("260804", "Port18_SITE0")]


def load_receipts(dir_="exp15", variant="j"):
    d = WORK_DIR / dir_
    receipt_re = re.compile(RECEIPT_RE_TMPL.format(re.escape(variant)))
    rows = []
    for fn in sorted(glob.glob(os.path.join(d, f"result_260729_*_any_{variant}.json"))):
        base = os.path.basename(fn)
        if not receipt_re.match(base):
            continue
        rows.append(json.loads(open(fn, encoding="utf-8").read()))
    return rows


def spectrum(r):
    """f (Hz), dL(f) [pH], dR(f) [mOhm], dR_frac(f) = (Zre-Zrefre)/Zrefre [unitless] for one receipt."""
    f = np.asarray(r["freq"], dtype=float)
    Zm = np.asarray(r["Z_re"], dtype=float) + 1j * np.asarray(r["Z_im"], dtype=float)
    Zr = np.asarray(r["Zref_re"], dtype=float) + 1j * np.asarray(r["Zref_im"], dtype=float)
    dZ = Zm - Zr
    dL_pH = dZ.imag / (2 * np.pi * f) * 1e12
    dR_mOhm = dZ.real * 1e3
    with np.errstate(divide="ignore", invalid="ignore"):
        dR_frac = np.where(Zr.real != 0, dZ.real / Zr.real, np.nan)
    return f, dL_pH, dR_mOhm, dR_frac, Zm, Zr


def band_median(f, v, band):
    m = (f >= band[0]) & (f <= band[1])
    return float(np.median(v[m])) if m.any() else float("nan")


def per_port_row(r):
    f, dL, dR, dR_frac, Zm, Zr = spectrum(r)
    dL_lf = band_median(f, dL, LF_BAND)
    dL_hf = band_median(f, dL, HF_BAND)
    D = dL_hf - dL_lf

    m_ar = (f >= ANTIRES_BAND[0]) & (f <= ANTIRES_BAND[1])
    if m_ar.any():
        ratio_ar = np.abs(Zm[m_ar]) / np.abs(Zr[m_ar])
        max_ratio_ar = float(np.max(ratio_ar))
        i_peak = int(np.argmax(np.abs(Zr[m_ar])))
        ratio_at_ref_peak = float(ratio_ar[i_peak])
        f_ref_peak = float(f[m_ar][i_peak])
    else:
        max_ratio_ar = ratio_at_ref_peak = f_ref_peak = float("nan")

    dR_frac_med = band_median(f, dR_frac, DR_BAND)
    r_short = bool(dR_frac_med < 0) if np.isfinite(dR_frac_med) else False

    via_pH = r["breakdown_100k"]["rail_vias"]["pH"]

    return dict(port=r["port"], tag=r["tag"], rail=r["rail"], dL_LF_pH=dL_lf, dL_HF_pH=dL_hf, D_pH=D,
                antires_max_ratio=max_ratio_ar, antires_ratio_at_ref_peak=ratio_at_ref_peak,
                antires_f_ref_peak_Hz=f_ref_peak, dR_frac_median_1e7_1e8=dR_frac_med, r_short=r_short,
                via_L_pH=via_pH)


def verdict(adopt, reject, n, min_n=MIN_N):
    if n < min_n:
        return "indeterminate (insufficient n)"
    if adopt:
        return "adopt"
    if reject:
        return "reject"
    return "indeterminate"


def hyp1(rows):
    D = np.array([r["D_pH"] for r in rows if np.isfinite(r["D_pH"])])
    n = len(D)
    if n == 0:
        return dict(n=0, fraction_negative=None, median_D_pH=None, verdict=verdict(False, False, n))
    frac_neg = float(np.mean(D < 0))
    med = float(np.median(D))
    return dict(n=n, fraction_negative=frac_neg, median_D_pH=med,
                verdict=verdict(frac_neg >= 0.6 and med <= -3.0, frac_neg < 0.5 or med > 0.0, n))


def hyp2(rows):
    D = np.array([r["D_pH"] for r in rows])
    via = np.array([r["via_L_pH"] for r in rows])
    ok = np.isfinite(D) & np.isfinite(via)
    D, via = D[ok], via[ok]
    n = len(D)
    if n < 3:
        return dict(n=n, rho=None, p=None, median_absD_pH=None, median_viaL_pH=None,
                     within_factor3=None, verdict=verdict(False, False, n))
    rho, p = stats.spearmanr(np.abs(D), via)
    med_absD, med_via = float(np.median(np.abs(D))), float(np.median(via))
    within3 = bool(med_via > 0 and (1 / 3 <= med_absD / med_via <= 3))
    return dict(n=n, rho=float(rho), p=float(p), median_absD_pH=med_absD, median_viaL_pH=med_via,
                within_factor3=within3, verdict=verdict(rho >= 0.5 and p < 0.01 and within3, rho < 0.2, n))


def hyp3(rows):
    frac = np.array([r["dR_frac_median_1e7_1e8"] for r in rows if np.isfinite(r["dR_frac_median_1e7_1e8"])])
    n = len(frac)
    if n == 0:
        return dict(n=0, fraction_r_short=None, median_abs_dR_frac=None, verdict=verdict(False, False, n))
    frac_short = float(np.mean(frac < 0))
    med_abs = float(np.median(np.abs(frac)))
    return dict(n=n, fraction_r_short=frac_short, median_abs_dR_frac=med_abs,
                verdict=verdict(frac_short > 0.5 and med_abs >= 0.2, frac_short <= 0.5, n))


def side_table():
    """D for the 7 known cases, EXP-13/j baseline vs EXP-14/h (via_L_twowire, 2x via L): shows what
    doubling via L does to D. Missing receipts (e.g. exp14/h not yet run for a case) are reported, not fatal."""
    rows = []
    for tag, port in SIDE_CASES:
        base_fn = WORK_DIR / "exp13" / f"result_{tag}_{port}_any_j.json"
        h_fn = WORK_DIR / "exp14" / f"result_{tag}_{port}_any_h.json"
        row = dict(tag=tag, port=port, baseline_receipt=str(base_fn), h_receipt=str(h_fn))
        if base_fn.is_file():
            f, dL, *_ = spectrum(json.loads(base_fn.read_text(encoding="utf-8")))
            row["D_baseline_pH"] = band_median(f, dL, HF_BAND) - band_median(f, dL, LF_BAND)
        else:
            row["D_baseline_pH"] = None
        if h_fn.is_file():
            f, dL, *_ = spectrum(json.loads(h_fn.read_text(encoding="utf-8")))
            row["D_h_pH"] = band_median(f, dL, HF_BAND) - band_median(f, dL, LF_BAND)
        else:
            row["D_h_pH"] = None
        if row["D_baseline_pH"] is not None and row["D_h_pH"] is not None:
            row["delta_D_pH"] = row["D_h_pH"] - row["D_baseline_pH"]
        else:
            row["delta_D_pH"] = None
        rows.append(row)
    return rows


def plot_dL_spectrum(receipts, out_png, clip=50.0):
    fig, ax = plt.subplots(figsize=(9, 6))
    all_f = None
    curves = []
    for r in receipts:
        f, dL, *_ = spectrum(r)
        ax.plot(f, np.clip(dL, -clip, clip), color="steelblue", lw=0.6, alpha=0.35)
        curves.append((f, dL))
        if all_f is None or len(f) > len(all_f):
            all_f = f
    if curves:
        # median across ports at each of the shared ladder frequencies (all receipts share the same grid)
        f0 = curves[0][0]
        same_grid = all(np.array_equal(f0, f) for f, _ in curves)
        if same_grid:
            stacked = np.vstack([dL for _, dL in curves])
            med = np.median(stacked, axis=0)
            ax.plot(f0, np.clip(med, -clip, clip), color="crimson", lw=2.2, label=f"median (n={len(curves)})")
    ax.set_xscale("log")
    ax.axhline(0, color="gray", lw=0.7)
    ax.set_xlabel("f (Hz)")
    ax.set_ylabel(f"dL(f) (pH), clipped to +/-{clip:g}")
    ax.set_title(f"EXP-17: dL(f) = Im(Zmodel-Zref)/(2 pi f), all ports (n={len(receipts)})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def plot_D_vs_viaL(rows, h2, out_png):
    fig, ax = plt.subplots(figsize=(7, 6))
    absD = np.array([abs(r["D_pH"]) for r in rows])
    via = np.array([r["via_L_pH"] for r in rows])
    ok = (absD > 0) & (via > 0) & np.isfinite(absD) & np.isfinite(via)
    ax.scatter(via[ok], absD[ok], color="darkorange", edgecolor="black")
    if ok.sum() < len(rows):
        ax.set_xlabel(f"via L, breakdown_100k.rail_vias.pH  ({(~ok).sum()} port(s) with D=0 or viaL<=0 omitted, log scale)")
    else:
        ax.set_xlabel("via L, breakdown_100k.rail_vias.pH")
    ax.set_ylabel("|D| = |dL_HF - dL_LF| (pH)")
    if ok.sum() >= 2:
        ax.set_xscale("log")
        ax.set_yscale("log")
    rho_s = f"{h2['rho']:.3f}" if h2["rho"] is not None else "n/a"
    p_s = f"{h2['p']:.3g}" if h2["p"] is not None else "n/a"
    ax.set_title(f"EXP-17: |D| vs via L (Spearman rho={rho_s}, p={p_s}, n={h2['n']})")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="exp15", help="WORK_DIR sub-directory to read receipts from (default exp15)")
    ap.add_argument("--variant", default="j", help="run11.py variant tag in the receipt filenames (default j)")
    a = ap.parse_args()
    suffix = f"_{a.dir}" if a.dir != "exp15" else ""

    receipts = load_receipts(a.dir, a.variant)
    n = len(receipts)
    if n == 0:
        print(f"no receipts found yet under WORK_DIR/{a.dir} (variant {a.variant}) -- nothing to do")
        return 0
    rows = [per_port_row(r) for r in receipts]

    h1, h2, h3 = hyp1(rows), hyp2(rows), hyp3(rows)
    side = side_table()
    out = dict(n=n, ports=rows, hypotheses=dict(H1_hf_L_deficit=h1, H2_via_L_scale=h2, H3_r_short=h3),
               side_table_exp13j_vs_exp14h=side)

    json_fn = work_file("exp17", f"exp17_spectra{suffix}.json")
    with open(json_fn, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    dl_png = work_file("exp17", f"exp17_dL_spectrum{suffix}.png")
    plot_dL_spectrum(receipts, dl_png)
    d_png = work_file("exp17", f"exp17_D_vs_viaL{suffix}.png")
    plot_D_vs_viaL(rows, h2, d_png)

    print(f"\n### EXP-17 dL(f)/dR(f) spectra (n={n} ports)\n")
    print("| hypothesis | stat | verdict |")
    print("|---|---|---|")
    print(f"| H1 HF L deficit | frac(D<0)={h1['fraction_negative']}, median D={h1['median_D_pH']} pH (n={h1['n']}) | {h1['verdict']} |")
    print(f"| H2 via L scale | rho={h2['rho']}, p={h2['p']}, medianD={h2['median_absD_pH']}, "
          f"medianViaL={h2['median_viaL_pH']}, within3x={h2['within_factor3']} (n={h2['n']}) | {h2['verdict']} |")
    print(f"| H3 R short (10-100MHz) | frac_short={h3['fraction_r_short']}, median|dRfrac|={h3['median_abs_dR_frac']} (n={h3['n']}) | {h3['verdict']} |")

    print("\n#### side table: D, EXP-13/j baseline vs EXP-14/h (via_L_twowire, 2x via L)\n")
    print("| tag | port | D_baseline (pH) | D_h (pH) | delta D (pH) |")
    print("|---|---|---|---|---|")
    for r in side:
        print(f"| {r['tag']} | {r['port']} | {r['D_baseline_pH']} | {r['D_h_pH']} | {r['delta_D_pH']} |")

    print(f"\nwrote {json_fn}")
    print(f"wrote {dl_png}")
    print(f"wrote {d_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
