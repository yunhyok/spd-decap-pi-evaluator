#!/usr/bin/env python
"""EXP-34: is Im(Z) zero-crossing f0 a better G4 f_res-gate stand-in than min|Z| f_res?
See WORK_DIR/exp34/EXP34_PLAN.md for the frozen definitions (Section 1) and verdict
thresholds (Section 2) -- do not edit that file, do not change the numbers here. No model
is run; this only re-reads existing receipts plus the reference full-grid Zdiag npz.

    python an34.py

Reads WORK_DIR/exp28/result_260729_*_any_p.json (A, 92 ports), WORK_DIR/exp32/result_260729_
*_any_q.json (B, 92 ports), WORK_DIR/exp30/result_s5m6585_*_any_p.json (C, 160 ports); same
glob + timestamp-skip pattern as exp29/an29.py and exp33/an33.py. Reference Im Z on the full
grid comes from paths.ref_npz(tag) (freq already in Hz, Zdiag complex, port_names
"Port..::Rail" -> matched on the "Port.." prefix).

Writes WORK_DIR/exp34/exp34.json and exp34_fres_vs_f0.png; prints a markdown summary.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import WORK_DIR, work_file, ref_npz  # noqa: E402

# frozen definitions / thresholds, EXP34_PLAN.md Section 1-2
F0_LO_HZ, F0_HI_HZ = 3e5, 3e7
F_C_HZ = 1e5
L_TARGET_CAP_HZ = 3e7
ELIG_F0_REF_MAX = 15e6
COND_TOL = 0.1
EXISTENCE_MIN = 0.8
COND_GAIN_MIN = 0.1
G4_MAX_RELERR_MAX = 0.2

SETS = [
    ("A", "exp28", "260729", "p", 92),
    ("B", "exp32", "260729", "q", 92),
    ("C", "exp30", "s5m6585", "p", 160),
]


def _load_glob(dir_, tag, variant, n_expect):
    """result_{tag}_*_any_{variant}.json under WORK_DIR/dir_; the receipt_re anchors on
    "..._any_{variant}.json$", which a timestamp suffix never matches, so resume duplicates
    are excluded for free (same pattern as exp29/an29.py, exp33/an33.py)."""
    receipt_re = re.compile(rf"^result_{re.escape(tag)}_(.+)_any_{re.escape(variant)}\.json$")
    d = WORK_DIR / dir_
    rows = []
    for fn in sorted(glob.glob(os.path.join(d, f"result_{tag}_*_any_{variant}.json"))):
        base = os.path.basename(fn)
        if not receipt_re.match(base):
            continue
        rows.append(json.loads(open(fn, encoding="utf-8").read()))
    if n_expect is not None and len(rows) != n_expect:
        print(f"WARNING: {dir_}/{tag}/{variant} expected {n_expect} receipts, found {len(rows)}", file=sys.stderr)
    return rows


_REF_CACHE: dict[str, tuple] = {}


def load_ref(tag):
    """(freq_hz, Zdiag, port->col map) for a design tag, cached. npz freq is already Hz
    (verified against Zfull_sel_freq/Zfull_sel_idx, e.g. freq[100] == 1e3 Hz)."""
    if tag not in _REF_CACHE:
        npz = np.load(ref_npz(tag), allow_pickle=True)
        freq_hz = npz["freq"].astype(float)
        Zdiag = npz["Zdiag"]
        col = {str(pn).split("::")[0]: i for i, pn in enumerate(npz["port_names"])}
        _REF_CACHE[tag] = (freq_hz, Zdiag, col)
    return _REF_CACHE[tag]


def nearest_idx(freq, target):
    return int(np.argmin(np.abs(freq - target)))


def find_f0(freq, im, lo=F0_LO_HZ, hi=F0_HI_HZ):
    """First negative->positive crossing of im(freq) within [lo, hi], log-f linear interp
    between the bracketing samples. None if no crossing."""
    freq = np.asarray(freq, dtype=float)
    im = np.asarray(im, dtype=float)
    idx = np.where((freq >= lo) & (freq <= hi))[0]
    if len(idx) < 2:
        return None
    for a, b in zip(idx[:-1], idx[1:]):
        y1, y2 = im[a], im[b]
        if y1 < 0 and y2 >= 0:
            f1, f2 = freq[a], freq[b]
            t = -y1 / (y2 - y1)
            return math.exp(math.log(f1) + t * (math.log(f2) - math.log(f1)))
    return None


def per_port_row(r):
    tag = r["tag"]
    freq = np.asarray(r["freq"], dtype=float)
    z_im = np.asarray(r["Z_im"], dtype=float)
    zref_im = np.asarray(r["Zref_im"], dtype=float)

    f_res_model = float(r["f_res_model"]) if r.get("f_res_model") is not None else None
    f_res_ref = float(r["f_res_ref"]) if r.get("f_res_ref") is not None else None
    r_fres = (f_res_model / f_res_ref) if (f_res_model is not None and f_res_ref) else None

    f0_mod = find_f0(freq, z_im)

    ref_freq, ref_Zdiag, ref_col = load_ref(tag)
    col = ref_col.get(r["port"])
    if col is None:
        raise KeyError(f"port {r['port']!r} (tag {tag}) not found in {ref_npz(tag)} port_names")
    f0_ref = find_f0(ref_freq, ref_Zdiag[:, col].imag)

    r_f0 = (f0_mod / f0_ref) if (f0_mod is not None and f0_ref) else None
    existence = f0_mod is not None and f0_ref is not None

    # r_C: 100kHz C_eff, model/ref, nearest ladder point (same idx for both, receipt's own grid)
    ic = nearest_idx(freq, F_C_HZ)
    fc = float(freq[ic])
    im_mod_c, im_ref_c = float(z_im[ic]), float(zref_im[ic])
    c_mod_ok, c_ref_ok = im_mod_c < 0, im_ref_c < 0
    C_mod = -1.0 / (2 * math.pi * fc * im_mod_c) if c_mod_ok else None
    C_ref = -1.0 / (2 * math.pi * fc * im_ref_c) if c_ref_ok else None
    r_C = (C_mod / C_ref) if (C_mod is not None and C_ref is not None and C_ref != 0) else None

    # r_L: L_eff at the ladder point nearest to 2*f0_ref (capped at 3e7); needs f0_ref
    l_ok = False
    r_L = None
    if f0_ref is not None:
        il = nearest_idx(freq, min(2 * f0_ref, L_TARGET_CAP_HZ))
        fl = float(freq[il])
        im_mod_l, im_ref_l = float(z_im[il]), float(zref_im[il])
        l_ok = im_mod_l > 0 and im_ref_l > 0
        if l_ok:
            L_mod = im_mod_l / (2 * math.pi * fl)
            L_ref = im_ref_l / (2 * math.pi * fl)
            r_L = (L_mod / L_ref) if L_ref != 0 else None

    eligible = (f0_ref is not None) and (f0_ref < ELIG_F0_REF_MAX) and l_ok and (r_L is not None) and (r_L > 0)

    r_pred = None
    if eligible and r_C is not None and r_C > 0:
        r_pred = (r_L * r_C) ** -0.5

    cond_fres_ok = eligible and r_pred is not None and r_fres is not None and abs(r_fres - r_pred) <= COND_TOL
    cond_f0_ok = eligible and r_pred is not None and r_f0 is not None and abs(r_f0 - r_pred) <= COND_TOL

    g4_max_rel_err = r.get("ladder_gates", {}).get("G4_max_rel_err")
    g4_old = bool(r.get("ladder_gates", {}).get("PASS", {}).get("G4"))
    g4_new = (g4_max_rel_err is not None and g4_max_rel_err < G4_MAX_RELERR_MAX
              and r_f0 is not None and abs(r_f0 - 1.0) <= COND_TOL)

    return dict(
        port=r["port"], tag=tag, rail=r.get("rail"),
        f_res_model=f_res_model, f_res_ref=f_res_ref, r_fres=r_fres,
        f0_mod=f0_mod, f0_ref=f0_ref, r_f0=r_f0, existence=existence,
        r_C=r_C, r_L=r_L, r_pred=r_pred, eligible=eligible,
        cond_fres_ok=cond_fres_ok, cond_f0_ok=cond_f0_ok,
        g4_max_rel_err=g4_max_rel_err, g4_old=g4_old, g4_new=g4_new,
    )


def quartiles(vals):
    vals = [v for v in vals if v is not None and np.isfinite(v)]
    if not vals:
        return dict(n=0, median=None, q1=None, q3=None)
    q1, med, q3 = np.percentile(vals, [25, 50, 75])
    return dict(n=len(vals), median=float(med), q1=float(q1), q3=float(q3))


def set_summary(rows):
    n = len(rows)
    n_elig = sum(1 for p in rows if p["eligible"])
    existence_rate = sum(1 for p in rows if p["existence"]) / n if n else None
    cond_fres = sum(1 for p in rows if p["cond_fres_ok"]) / n_elig if n_elig else None
    cond_f0 = sum(1 for p in rows if p["cond_f0_ok"]) / n_elig if n_elig else None
    return dict(
        n_ports=n, n_eligible=n_elig, existence_rate=existence_rate,
        cond_fres=cond_fres, cond_f0=cond_f0,
        r_fres=quartiles([p["r_fres"] for p in rows]),
        r_f0=quartiles([p["r_f0"] for p in rows]),
        g4_old_pass=sum(1 for p in rows if p["g4_old"]),
        g4_new_pass=sum(1 for p in rows if p["g4_new"]),
    )


def plot_fres_f0(all_rows, out_png):
    fig, axes = plt.subplots(1, len(all_rows), figsize=(6 * len(all_rows), 5.5), squeeze=False)
    axes = axes[0]
    for ax, (name, rows) in zip(axes, all_rows.items()):
        elig = [p for p in rows if p["eligible"] and p["r_pred"] is not None]
        xs_pred = [p["r_pred"] for p in elig]
        ys_fres = [p["r_fres"] for p in elig]
        ys_f0 = [p["r_f0"] for p in elig]
        ax.scatter(xs_pred, ys_fres, s=24, alpha=0.7, edgecolor="black", linewidth=0.3,
                   color="steelblue", label="r_fres")
        ax.scatter([x for x, y in zip(xs_pred, ys_f0) if y is not None],
                   [y for y in ys_f0 if y is not None], s=24, alpha=0.7, edgecolor="black",
                   linewidth=0.3, color="darkorange", label="r_f0")
        lo = min([1.0] + [v for v in xs_pred + ys_fres + [y for y in ys_f0 if y is not None]])
        hi = max([1.0] + [v for v in xs_pred + ys_fres + [y for y in ys_f0 if y is not None]])
        ax.plot([lo, hi], [lo, hi], color="gray", lw=1, ls="--")
        ax.set_xlabel("r_pred = (r_L*r_C)^-1/2")
        ax.set_ylabel("r_fres / r_f0")
        ax.set_title(f"set {name} (n_eligible={len(elig)})")
        ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    all_rows = {}
    summaries = {}
    for name, dir_, tag, variant, n_expect in SETS:
        receipts = _load_glob(dir_, tag, variant, n_expect)
        rows = [per_port_row(r) for r in receipts]
        all_rows[name] = rows
        summaries[name] = set_summary(rows)

    existence_ok = all(summaries[n]["existence_rate"] is not None and summaries[n]["existence_rate"] >= EXISTENCE_MIN
                        for n in ("A", "B", "C"))
    cond_gain_ok = all(summaries[n]["cond_f0"] is not None and summaries[n]["cond_fres"] is not None
                        and (summaries[n]["cond_f0"] - summaries[n]["cond_fres"]) >= COND_GAIN_MIN
                        for n in ("A", "B", "C"))
    adopt_f0 = existence_ok and cond_gain_ok
    verdict = "adopt_f0" if adopt_f0 else "keep_current"

    out = dict(
        definitions=dict(f0_window_Hz=[F0_LO_HZ, F0_HI_HZ], f_C_Hz=F_C_HZ,
                          L_target_cap_Hz=L_TARGET_CAP_HZ, elig_f0_ref_max_Hz=ELIG_F0_REF_MAX,
                          cond_tol=COND_TOL, existence_min=EXISTENCE_MIN, cond_gain_min=COND_GAIN_MIN,
                          g4_max_relerr_max=G4_MAX_RELERR_MAX),
        sets={n: dict(summary=summaries[n], ports=all_rows[n]) for n in ("A", "B", "C")},
        verdict=dict(adopt_f0=adopt_f0, verdict=verdict, existence_ok=existence_ok, cond_gain_ok=cond_gain_ok),
    )

    json_fn = work_file("exp34", "exp34.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp34", "exp34_fres_vs_f0.png")
    plot_fres_f0(all_rows, png_fn)

    # ---- printed markdown summary ----
    print(f"\n### EXP-34 f_res (min|Z|) vs f0 (Im Z zero-crossing) as the G4 gate metric\n")
    print("| set | n | existence(f0) | cond(fres) | cond(f0) | cond gain | r_fres median [Q1,Q3] | "
          "r_f0 median [Q1,Q3] | G4 PASS old | G4 PASS new |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for n in ("A", "B", "C"):
        s = summaries[n]
        rc, rf0 = s["r_fres"], s["r_f0"]
        gain = (s["cond_f0"] - s["cond_fres"]) if (s["cond_f0"] is not None and s["cond_fres"] is not None) else None
        print(f"| {n} | {s['n_ports']} | {s['existence_rate']:.3f} | {s['cond_fres']:.3f} | "
              f"{s['cond_f0']:.3f} | {gain:+.3f} | "
              f"{rc['median']:.4f} [{rc['q1']:.4f},{rc['q3']:.4f}] (n={rc['n']}) | "
              f"{rf0['median']:.4f} [{rf0['q1']:.4f},{rf0['q3']:.4f}] (n={rf0['n']}) | "
              f"{s['g4_old_pass']}/{s['n_ports']} | {s['g4_new_pass']}/{s['n_ports']} |")

    print(f"\n**Verdict: {verdict}** (existence>=0.8 all sets: {existence_ok}; "
          f"cond(f0)-cond(fres)>=0.1 all sets: {cond_gain_ok})")
    if adopt_f0:
        print("f0 adopted as the G4 f_res-gate stand-in; G4 PASS \"new\" column above is the "
              "recomputed gate (G4_max_rel_err<0.2 AND |r_f0-1|<=0.1), from receipts only "
              "(receipts themselves not modified). \"old\" column is the receipts' existing "
              "ladder_gates.PASS.G4.")
    else:
        print("f0 not adopted; current G4 definition stands. \"new\" column above is shown for "
              "reference only (what G4 would be if f0 were substituted).")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
