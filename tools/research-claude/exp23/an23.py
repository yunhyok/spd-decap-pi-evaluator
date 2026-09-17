#!/usr/bin/env python
"""EXP-23: anti-resonance position/height/width vs the reference, 1-30 MHz. See
WORK_DIR/exp23/EXP23_PLAN.md for the frozen definitions (Section 1) and thresholds (Section 2) --
do not edit that file, do not change the numbers here. No model is run; this only re-reads
existing receipts from EXP-15 (exp15/j, set A) and EXP-20 (exp20/mk, set B), 92 ports each.

    python an23.py

Reads WORK_DIR/exp15/result_260729_*_any_j.json and WORK_DIR/exp20/result_260729_*_any_mk.json
(same glob + skip pattern as exp19/an19.py -- the exp20 directory also holds a 260804 design's
receipts under the same *_any_mk.json glob; the design-id regex filters those out). Writes
WORK_DIR/exp23/exp23.json and exp23_antires.png; prints a markdown summary.
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

RECEIPT_RE_TMPL = r"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_{}\.json$"

# frozen window / thresholds, EXP23_PLAN.md Section 1-2
ANTIRES_F_MIN = 3e6           # "restrict to points with f >= 3 MHz"
ANTIRES_F_MAX = 3e7
FRES_ELIGIBLE_MAX = 5e6       # f_s,ref must be <= this for a port to be eligible

VARIANT_SETS = {"A": ("exp15", "j"), "B": ("exp20", "mk")}


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


def _local_max(f, zabs, mask):
    """Highest interior point of `zabs` (strictly above both neighbours) whose frequency is in
    `mask`. Returns (peak_index, window_lo_index, window_hi_index) or None."""
    idx = np.where(mask)[0]
    if idx.size == 0:
        return None
    candidates = [i for i in idx if 0 < i < len(f) - 1 and zabs[i] > zabs[i - 1] and zabs[i] > zabs[i + 1]]
    if not candidates:
        return None
    i_peak = max(candidates, key=lambda i: zabs[i])
    return i_peak, int(idx.min()), int(idx.max())


def _log_cross(f1, y1, f2, y2, thr):
    """Frequency where a line from (log10 f1, y1) to (log10 f2, y2) crosses y=thr."""
    x1, x2 = np.log10(f1), np.log10(f2)
    frac = (thr - y1) / (y2 - y1)
    return float(10 ** (x1 + frac * (x2 - x1)))


def _width(f, zabs, i_peak, lo, hi):
    """-3dB-style (1/sqrt2) width around i_peak, searching only within [lo, hi] (the anti-res
    window). None if a side never drops below the threshold before the window edge."""
    thr = zabs[i_peak] / np.sqrt(2)
    f_lo = None
    for j in range(i_peak, lo, -1):
        if zabs[j] >= thr and zabs[j - 1] < thr:
            f_lo = _log_cross(f[j - 1], zabs[j - 1], f[j], zabs[j], thr)
            break
    f_hi = None
    for j in range(i_peak, hi):
        if zabs[j] >= thr and zabs[j + 1] < thr:
            f_hi = _log_cross(f[j], zabs[j], f[j + 1], zabs[j + 1], thr)
            break
    if f_lo is None or f_hi is None:
        return None
    return f_hi - f_lo


def per_port_row(r):
    f = np.asarray(r["freq"], dtype=float)
    Zm_abs = np.abs(np.asarray(r["Z_re"], dtype=float) + 1j * np.asarray(r["Z_im"], dtype=float))
    Zr_abs = np.abs(np.asarray(r["Zref_re"], dtype=float) + 1j * np.asarray(r["Zref_im"], dtype=float))
    f_res_ref = float(r["f_res_ref"])
    f_res_model = float(r["f_res_model"])
    fs_ratio = f_res_model / f_res_ref if f_res_ref else None

    mask = (f > f_res_ref) & (f >= ANTIRES_F_MIN) & (f <= ANTIRES_F_MAX)
    ref_peak = _local_max(f, Zr_abs, mask)
    mod_peak = _local_max(f, Zm_abs, mask)
    local_max_ref, local_max_mod = ref_peak is not None, mod_peak is not None
    eligible = bool(f_res_ref <= FRES_ELIGIBLE_MAX and local_max_ref and local_max_mod)

    f_a_ref = height_a_ref = width_ref = None
    f_a_mod = height_a_mod = width_mod = None
    if local_max_ref:
        i_r, lo_r, hi_r = ref_peak
        f_a_ref, height_a_ref = float(f[i_r]), float(Zr_abs[i_r])
        width_ref = _width(f, Zr_abs, i_r, lo_r, hi_r)
    if local_max_mod:
        i_m, lo_m, hi_m = mod_peak
        f_a_mod, height_a_mod = float(f[i_m]), float(Zm_abs[i_m])
        width_mod = _width(f, Zm_abs, i_m, lo_m, hi_m)

    pos_ratio = height_ratio = width_ratio = None
    if eligible:
        pos_ratio = f_a_mod / f_a_ref
        height_ratio = height_a_mod / height_a_ref
        if width_ref is not None and width_mod is not None:
            width_ratio = width_mod / width_ref

    return dict(port=r["port"], tag=r["tag"], rail=r["rail"], f_res_ref_Hz=f_res_ref,
                f_res_model_Hz=f_res_model, fs_ratio=fs_ratio,
                f_a_ref_Hz=f_a_ref, height_a_ref_Ohm=height_a_ref, width_ref_Hz=width_ref,
                f_a_mod_Hz=f_a_mod, height_a_mod_Ohm=height_a_mod, width_mod_Hz=width_mod,
                pos_ratio=pos_ratio, height_ratio=height_ratio, width_ratio=width_ratio,
                local_max_ref=local_max_ref, local_max_mod=local_max_mod, eligible=eligible)


def _stats(vals):
    arr = np.asarray([v for v in vals if v is not None], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return dict(n=0, q1=None, median=None, q3=None)
    q1, med, q3 = (float(x) for x in np.percentile(arr, [25, 50, 75]))
    return dict(n=int(arr.size), q1=q1, median=med, q3=q3)


def hyp_pos(rows_a):
    s = _stats(p["pos_ratio"] for p in rows_a if p["eligible"])
    if s["median"] is None:
        return dict(**s, verdict="indeterminate", direction=None)
    if 0.9 <= s["median"] <= 1.1:
        return dict(**s, verdict="adopt", direction=None)
    return dict(**s, verdict="reject", direction="high" if s["median"] > 1.1 else "low")


def hyp_height(rows_a):
    s = _stats(p["height_ratio"] for p in rows_a if p["eligible"])
    if s["median"] is None:
        v = "indeterminate"
    elif s["median"] > 1.15:
        v = "over (adopt)"
    elif s["median"] < 0.87:
        v = "under (adopt)"
    else:
        v = "indeterminate"
    return dict(**s, verdict=v)


def hyp_width(rows_a):
    s = _stats(p["width_ratio"] for p in rows_a if p["eligible"])
    if s["median"] is None:
        v = "indeterminate"
    elif s["median"] < 0.85:
        v = "narrow (adopt)"
    elif s["median"] > 1.15:
        v = "wide (adopt)"
    else:
        v = "indeterminate"
    return dict(**s, verdict=v)


def hyp_skin(rows_a, rows_b):
    s_a = _stats(p["height_ratio"] for p in rows_a if p["eligible"])
    s_b = _stats(p["height_ratio"] for p in rows_b if p["eligible"])
    if s_a["median"] is None or s_b["median"] is None:
        return dict(A=s_a, B=s_b, verdict="indeterminate")
    dev_a, dev_b = abs(s_a["median"] - 1), abs(s_b["median"] - 1)
    verdict = "adopt" if dev_b <= 0.8 * dev_a else "reject"
    return dict(A=s_a, B=s_b, dev_A=dev_a, dev_B=dev_b, verdict=verdict)


def plot_antires(rows_a, rows_b, out_png):
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    specs = [("pos_ratio", "position ratio f_a,mod/f_a,ref"),
             ("height_ratio", "height ratio |Z_a,mod|/|Z_a,ref|"),
             ("width_ratio", "width ratio W_mod/W_ref")]
    for ax, (key, label) in zip(axes, specs):
        a = np.asarray([p[key] for p in rows_a if p["eligible"] and p[key] is not None], dtype=float)
        b = np.asarray([p[key] for p in rows_b if p["eligible"] and p[key] is not None], dtype=float)
        if a.size:
            bins = np.histogram_bin_edges(a, bins=min(20, max(5, a.size // 3)))
            ax.hist(a, bins=bins, color="steelblue", edgecolor="black", alpha=0.75, label=f"A (n={a.size})")
            ax.axvline(float(np.median(a)), color="navy", lw=2, label=f"A median={np.median(a):.3f}")
        if b.size:
            bins_b = np.histogram_bin_edges(b, bins=min(20, max(5, b.size // 3))) if not a.size else bins
            ax.hist(b, bins=bins_b, histtype="step", color="darkorange", lw=2, label=f"B (n={b.size})")
            ax.axvline(float(np.median(b)), color="darkorange", lw=2, ls="--", label=f"B median={np.median(b):.3f}")
        ax.axvline(1.0, color="gray", lw=0.7)
        ax.set_title(label)
        ax.set_xlabel(key)
        ax.set_ylabel("count")
        ax.legend(fontsize=8)
    fig.suptitle("EXP-23: anti-resonance ratios (A=exp15/j filled, B=exp20/mk outline)")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    rows = {}
    for key, (dir_, variant) in VARIANT_SETS.items():
        receipts = load_receipts(dir_, variant)
        rows[key] = [per_port_row(r) for r in receipts]

    n_eligible = {k: sum(1 for p in rows[k] if p["eligible"]) for k in VARIANT_SETS}

    h_pos = hyp_pos(rows["A"])
    h_h = hyp_height(rows["A"])
    h_w = hyp_width(rows["A"])
    h_skin = hyp_skin(rows["A"], rows["B"])

    out = dict(
        variant_sets={k: dict(dir=v[0], variant=v[1], n=len(rows[k])) for k, v in VARIANT_SETS.items()},
        ports={k: rows[k] for k in VARIANT_SETS},
        n_eligible=n_eligible,
        stats=dict(
            pos_ratio_A=_stats(p["pos_ratio"] for p in rows["A"] if p["eligible"]),
            height_ratio_A=_stats(p["height_ratio"] for p in rows["A"] if p["eligible"]),
            width_ratio_A=_stats(p["width_ratio"] for p in rows["A"] if p["eligible"]),
            pos_ratio_B=_stats(p["pos_ratio"] for p in rows["B"] if p["eligible"]),
            height_ratio_B=_stats(p["height_ratio"] for p in rows["B"] if p["eligible"]),
            width_ratio_B=_stats(p["width_ratio"] for p in rows["B"] if p["eligible"]),
        ),
        hypotheses=dict(H_pos=h_pos, H_h=h_h, H_w=h_w, H_skin=h_skin),
    )

    json_fn = work_file("exp23", "exp23.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp23", "exp23_antires.png")
    plot_antires(rows["A"], rows["B"], png_fn)

    print(f"\n### EXP-23 anti-resonance position/height/width "
          f"(A=exp15/j n={len(rows['A'])}, B=exp20/mk n={len(rows['B'])}, "
          f"n_eligible A={n_eligible['A']} B={n_eligible['B']})\n")
    print("| hypothesis | stat (set A) | verdict |")
    print("|---|---|---|")
    print(f"| H_pos median(pos_ratio) | median={h_pos['median']}, "
          f"Q1/Q3={h_pos['q1']}/{h_pos['q3']} (n={h_pos['n']}) | "
          f"{h_pos['verdict']}" + (f" ({h_pos['direction']})" if h_pos['direction'] else "") + " |")
    print(f"| H_h median(height_ratio) | median={h_h['median']}, "
          f"Q1/Q3={h_h['q1']}/{h_h['q3']} (n={h_h['n']}) | {h_h['verdict']} |")
    print(f"| H_w median(width_ratio) | median={h_w['median']}, "
          f"Q1/Q3={h_w['q1']}/{h_w['q3']} (n={h_w['n']}) | {h_w['verdict']} |")
    print(f"| H_skin height_ratio dev B vs A | dev_A={h_skin.get('dev_A')}, "
          f"dev_B={h_skin.get('dev_B')} (A n={h_skin['A']['n']}, B n={h_skin['B']['n']}) | {h_skin['verdict']} |")

    print("\n#### 8 ports with largest height_ratio (set A, exp15/j)\n")
    print("| port | rail | height_ratio | pos_ratio | width_ratio | f_s,ref (Hz) |")
    print("|---|---|---|---|---|---|")
    a_sorted = sorted((p for p in rows["A"] if p["eligible"] and p["height_ratio"] is not None),
                       key=lambda p: p["height_ratio"], reverse=True)[:8]
    for p in a_sorted:
        wr = f"{p['width_ratio']:.4f}" if p["width_ratio"] is not None else "n/a"
        print(f"| {p['port']} | {p['rail']} | {p['height_ratio']:.4f} | {p['pos_ratio']:.4f} | "
              f"{wr} | {p['f_res_ref_Hz']:.3g} |")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
