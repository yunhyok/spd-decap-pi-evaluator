#!/usr/bin/env python
"""EXP-16: R-deficit correlation diagnostics over the EXP-15 92-port cavity-wall (variant j) receipts.
See WORK_DIR/exp16/EXP16_PLAN.md for the frozen definitions and thresholds (do not edit that file).

    python corr16.py
    python corr16.py --dir exp21 --ref gnd

Reads WORK_DIR/exp15/result_260729_*_any_j.json (skips resume duplicates like *_any_j_HHMMSS.json).
Writes WORK_DIR/exp16/exp16_corr.json and exp16_scatter.png; prints a markdown summary. Runs on however
many receipts currently exist (n can be as low as 2); every hypothesis verdict reports its n so a partial
run is self-describing, and n < ~5 is reported as "indeterminate (insufficient n)" rather than computed.

  --dir DIR (default exp15), --variant V (default j), --ref any|gnd (default any) : which WORK_DIR/DIR
    result_260729_*_any_{variant}[_gnd].json receipts to load (H_a..H_d run on these). Outputs get a
    _DIR suffix when DIR != exp15 (e.g. exp16_corr_exp21.json), still written under WORK_DIR/exp16.
  --dir exp21 also adds H_b_prime: EXP-15/j (cavity) vs this run's GND-only dRe_100k_mOhm median over the
    ports that are R-deficit (dRe_100k < -0.05) in EXP-15 -- see EXP21_PLAN.md §2. Runs gracefully with
    0 exp21 receipts (prints n=0, writes nothing).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pickle
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

TAG = "260729"
PORT_NUM_RE = re.compile(r"^Port(\d+)_SITE([01])")
MIN_N = 5  # below this, hypothesis stats are not meaningful; report indeterminate instead of a number


def vtag_of(variant, ref):
    return variant if ref == "any" else f"{variant}_gnd"


def net_of(layer_name):
    """'Signal$L15(MAIN_POWER5)' -> 'MAIN_POWER5'; 'Signal$TOP' (no plane net in the name) -> None."""
    m = re.search(r"\(([^)]+)\)$", layer_name)
    return m.group(1) if m else None


def decap_count(tag, port):
    """WORK_DIR/exp5/extract_{tag}_{port}.pkl 'decaps' list length, or None if the pickle isn't cached."""
    pkl = WORK_DIR / "exp5" / f"extract_{tag}_{port}.pkl"
    if not pkl.is_file():
        return None
    with open(pkl, "rb") as f:
        d = pickle.load(f)
    return len(d.get("decaps") or [])


def load_receipts(dir_, vtag):
    """Raw receipt dicts from WORK_DIR/dir_/result_260729_*_any_{vtag}.json (skips resume duplicates,
    which the regex below excludes on top of the glob: they end "..._HHMMSS.json")."""
    receipt_re = re.compile(rf"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_{re.escape(vtag)}\.json$")
    rows = []
    for fn in sorted(glob.glob(os.path.join(WORK_DIR / dir_, f"result_260729_*_any_{vtag}.json"))):
        base = os.path.basename(fn)
        if not receipt_re.match(base):
            continue
        rows.append(json.loads(open(fn, encoding="utf-8").read()))
    return rows


def per_port_row(r):
    port = r["port"]
    m = PORT_NUM_RE.match(port)
    num, site = int(m.group(1)), int(m.group(2))
    pair_id = num if num <= 46 else num - 46  # SITE0 num <-> SITE1 num+46 share this id

    bd = r["breakdown_100k"]
    layer_keys = [k for k in bd if k not in ("rail_vias", "rail_traces")]
    r_layers = sum(bd[k]["R_mOhm"] for k in layer_keys)
    r_via = bd["rail_vias"]["mOhm"]
    r_trace = bd["rail_traces"]["mOhm"]
    r_total = r_layers + r_via + r_trace
    via_trace_share = (r_via + r_trace) / r_total if r_total else float("nan")

    # Non-GND-reference flag: reference_search's keys are the candidate plane layers themselves
    # (checked against an actual receipt: no separate up/down "sides" list in this schema, contrary
    # to the a-priori guess -- the net is the parenthesised suffix of the layer name, e.g.
    # "Signal$L15(MAIN_POWER5)" vs "Signal$L20(DGND)"; "Signal$TOP" carries no net = not a plane).
    # Flag is True if any candidate plane's net is present and isn't DGND.
    nets = [net_of(k) for k in r["reference_search"]]
    non_gnd = any(n is not None and n != "DGND" for n in nets)

    dRe = r["dRe_100k_mOhm"]
    f = np.asarray(r["freq"], dtype=float)
    Zre = np.asarray(r["Z_re"], dtype=float)
    Zrefre = np.asarray(r["Zref_re"], dtype=float)
    k = int(np.argmin(np.abs(f - 1e5)))
    rez_ratio = Zrefre[k] / Zre[k] if Zre[k] else float("nan")

    return dict(port=port, tag=r["tag"], port_num=num, site=site, pair_id=pair_id, rail=r["rail"],
                dRe_100k_mOhm=dRe, r_deficit=bool(dRe < -0.05), via_trace_R_share=via_trace_share,
                non_gnd_ref=non_gnd, ref_nets=sorted({n for n in nets if n}),
                decaps=decap_count(r["tag"], port), unknowns=r["unknowns"], reZ_ratio_100k=rez_ratio)


def verdict(adopt, reject, n, min_n=MIN_N):
    if n < min_n:
        return "indeterminate (insufficient n)"
    if adopt:
        return "adopt"
    if reject:
        return "reject"
    return "indeterminate"


def hyp_a(rows):
    x = np.array([abs(r["dRe_100k_mOhm"]) for r in rows])
    y = np.array([r["via_trace_R_share"] for r in rows])
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 3:
        return dict(n=n, rho=None, p=None, verdict=verdict(False, False, n))
    rho, p = stats.spearmanr(x, y)
    return dict(n=n, rho=float(rho), p=float(p), verdict=verdict(rho >= 0.5 and p < 0.01, rho < 0.2, n))


def hyp_b(rows):
    non_gnd = np.array([r["dRe_100k_mOhm"] for r in rows if r["non_gnd_ref"]])
    gnd = np.array([r["dRe_100k_mOhm"] for r in rows if not r["non_gnd_ref"]])
    n = min(len(non_gnd), len(gnd))
    if n < 2:
        return dict(n_non_gnd=len(non_gnd), n_gnd=len(gnd), p=None, median_non_gnd=None, median_gnd=None,
                     verdict=verdict(False, False, n))
    stat, p = stats.mannwhitneyu(non_gnd, gnd, alternative="two-sided")
    med_ng, med_g = float(np.median(non_gnd)), float(np.median(gnd))
    return dict(n_non_gnd=len(non_gnd), n_gnd=len(gnd), p=float(p), median_non_gnd=med_ng, median_gnd=med_g,
                verdict=verdict(p < 0.01 and med_ng < med_g, p >= 0.05, n))


def hyp_c(rows):
    by_pair = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["site"]] = r["dRe_100k_mOhm"]
    pairs = [(pid, d[0], d[1]) for pid, d in sorted(by_pair.items()) if 0 in d and 1 in d]
    n = len(pairs)
    if n < 3:
        return dict(n_pairs=n, fraction_consistent=None, pairs=pairs, verdict=verdict(False, False, n))
    consistent = 0
    for _, a, b in pairs:
        same_sign = (a >= 0) == (b >= 0)
        close = abs(a - b) <= 0.3 * max(abs(a), abs(b), 1e-12)
        if same_sign and close:
            consistent += 1
    frac = consistent / n
    return dict(n_pairs=n, fraction_consistent=frac, pairs=pairs,
                verdict=verdict(frac >= 0.8, frac < 0.5, n, min_n=3))


def hyp_d(rows):
    vals = np.array([r["reZ_ratio_100k"] for r in rows if r["r_deficit"] and np.isfinite(r["reZ_ratio_100k"])])
    n = len(vals)
    if n < 3:
        return dict(n=n, iqr=None, verdict=verdict(False, False, n, min_n=3))
    iqr = float(np.percentile(vals, 75) - np.percentile(vals, 25))
    return dict(n=n, iqr=iqr, verdict=verdict(iqr < 0.5, iqr >= 1.0, n, min_n=3))


def gate_summary(receipts):
    """G1-G4 PASS counts, err_1MHz median, fit_dL_pH quartiles over a raw-receipt list (EXP21_PLAN §3)."""
    n = len(receipts)
    pass_counts = {g: sum(1 for r in receipts if r["ladder_gates"]["PASS"][g]) for g in ("G1", "G2", "G3", "G4")}
    err = np.array([r["err_1MHz"] for r in receipts]) * 100.0
    fit_dl = np.array([r["fit_dL_pH"] for r in receipts])
    return dict(n=n, pass_counts=pass_counts,
                err_1MHz_median=float(np.median(err)) if n else None,
                fit_dL_q1=float(np.percentile(fit_dl, 25)) if n else None,
                fit_dL_median=float(np.median(fit_dl)) if n else None,
                fit_dL_q3=float(np.percentile(fit_dl, 75)) if n else None)


def hyp_b_prime(exp21_receipts, exp15_receipts):
    """H_b': cavity (exp15/j) vs GND-only (exp21) median dRe_100k_mOhm over the ports that are
    R-deficit in exp15 (EXP21_PLAN.md §2). Adopt if |median_exp21| <= 0.7*|median_exp15|,
    reject if |median_exp21| >= 0.9*|median_exp15|, else indeterminate."""
    deficit_ports = {r["port"] for r in exp15_receipts if r["dRe_100k_mOhm"] < -0.05}
    d15 = [r["dRe_100k_mOhm"] for r in exp15_receipts if r["port"] in deficit_ports]
    by_port21 = {r["port"]: r for r in exp21_receipts}
    d21 = [by_port21[p]["dRe_100k_mOhm"] for p in deficit_ports if p in by_port21]
    n = min(len(d15), len(d21))
    med15 = float(np.median(d15)) if d15 else None
    med21 = float(np.median(d21)) if d21 else None
    v = "indeterminate (insufficient n)"
    if n >= MIN_N and med15:
        ratio = abs(med21) / abs(med15)
        v = "adopt" if ratio <= 0.7 else "reject" if ratio >= 0.9 else "indeterminate"
    return dict(n_deficit_ports_exp15=len(deficit_ports), n_exp15=len(d15), n_exp21=len(d21),
               median_dRe_100k_exp15=med15, median_dRe_100k_exp21=med21, verdict=v,
               exp15_summary=gate_summary(exp15_receipts), exp21_summary=gate_summary(exp21_receipts))


def make_plot(rows, out_png):
    fig, axes = plt.subplots(2, 2, figsize=(11, 9))

    ax = axes[0, 0]
    x = [r["via_trace_R_share"] for r in rows]
    y = [abs(r["dRe_100k_mOhm"]) for r in rows]
    ax.scatter(x, y, c=["crimson" if r["r_deficit"] else "steelblue" for r in rows])
    ax.set_xlabel("(via+trace R) / total R  @100kHz")
    ax.set_ylabel("|dRe_100k| (mOhm)")
    ax.set_title("H_a: |dRe| vs via+trace R share")

    ax = axes[0, 1]
    ng = [r["dRe_100k_mOhm"] for r in rows if r["non_gnd_ref"]]
    g = [r["dRe_100k_mOhm"] for r in rows if not r["non_gnd_ref"]]
    groups = [v for v in (g, ng) if v]
    labels = [lbl for v, lbl in ((g, "GND only"), (ng, "non-GND ref")) if v]
    if groups:
        ax.boxplot(groups, tick_labels=labels, showmeans=True)
    for i, v in enumerate(groups, start=1):
        ax.scatter(np.full(len(v), i) + np.random.uniform(-0.05, 0.05, len(v)), v, alpha=0.6, s=15, color="black")
    ax.axhline(0, color="gray", lw=0.7)
    ax.set_ylabel("dRe_100k (mOhm)")
    ax.set_title("H_b: reference-net group")

    ax = axes[1, 0]
    by_pair = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["site"]] = r["dRe_100k_mOhm"]
    pairs = [(d[0], d[1]) for d in by_pair.values() if 0 in d and 1 in d]
    if pairs:
        a, b = zip(*pairs)
        lim = max(1e-6, max(abs(v) for v in a + b))
        ax.plot([-lim, lim], [-lim, lim], color="gray", lw=0.7, ls="--")
        ax.scatter(a, b, color="darkgreen")
    ax.set_xlabel("SITE0 dRe_100k (mOhm)")
    ax.set_ylabel("SITE1 dRe_100k (mOhm)")
    ax.set_title(f"H_c: SITE0 vs SITE1 ({len(pairs)} pairs)")

    ax = axes[1, 1]
    vals = [r["reZ_ratio_100k"] for r in rows if r["r_deficit"] and np.isfinite(r["reZ_ratio_100k"])]
    if vals:
        ax.hist(vals, bins=min(10, max(3, len(vals))), color="darkorange", edgecolor="black")
    ax.set_xlabel("Re(Zref)/Re(Zmodel) @100kHz")
    ax.set_title(f"H_d: ReZ ratio, R-deficit ports (n={len(vals)})")

    fig.suptitle(f"EXP-16: R-deficit correlation diagnostics (n={len(rows)} ports)")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="exp15", help="WORK_DIR subdir to load receipts from (default exp15)")
    ap.add_argument("--variant", default="j", help="run11 --variant to load (default j)")
    ap.add_argument("--ref", default="any", choices=["any", "gnd"], help="run11 --ref to load (default any)")
    a = ap.parse_args()

    vtag = vtag_of(a.variant, a.ref)
    suffix = f"_{a.dir}" if a.dir != "exp15" else ""
    receipts = load_receipts(a.dir, vtag)
    n = len(receipts)
    if n == 0:
        print(f"no receipts found yet under WORK_DIR/{a.dir}/result_260729_*_any_{vtag}.json -- nothing to do (n=0)")
        return 0
    rows = [per_port_row(r) for r in receipts]

    ha, hb, hc, hd = hyp_a(rows), hyp_b(rows), hyp_c(rows), hyp_d(rows)
    hypotheses = dict(H_a_via_trace_R_share=ha, H_b_non_gnd_reference=hb,
                      H_c_site_pair_consistency=hc, H_d_reZ_ratio_iqr=hd)
    hbp = None
    if a.dir == "exp21":
        exp15_receipts = load_receipts("exp15", "j")
        hbp = hyp_b_prime(receipts, exp15_receipts)
        hypotheses["H_b_prime_gnd_vs_cavity"] = hbp
    out = dict(n=n, ports=rows, hypotheses=hypotheses)

    json_fn = work_file("exp16", f"exp16_corr{suffix}.json")
    with open(json_fn, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    png_fn = work_file("exp16", f"exp16_scatter{suffix}.png")
    make_plot(rows, png_fn)

    n_deficit = sum(1 for r in rows if r["r_deficit"])
    print(f"\n### EXP-16 correlation diagnostics (dir={a.dir} ref={a.ref}, n={n} ports, {n_deficit} R-deficit)\n")
    print("| hypothesis | stat | verdict |")
    print("|---|---|---|")
    print(f"| H_a via+trace R share | rho={ha['rho']}, p={ha['p']} (n={ha['n']}) | {ha['verdict']} |")
    print(f"| H_b non-GND reference | p={hb['p']}, med_non_gnd={hb['median_non_gnd']}, "
          f"med_gnd={hb['median_gnd']} (n_ng={hb['n_non_gnd']}, n_g={hb['n_gnd']}) | {hb['verdict']} |")
    print(f"| H_c SITE pair consistency | fraction={hc['fraction_consistent']} (n_pairs={hc['n_pairs']}) | {hc['verdict']} |")
    print(f"| H_d ReZ ratio IQR (R-deficit) | IQR={hd['iqr']} (n={hd['n']}) | {hd['verdict']} |")
    if hbp is not None:
        print(f"| H_b_prime GND vs cavity dRe_100k median | exp15={hbp['median_dRe_100k_exp15']}, "
              f"exp21={hbp['median_dRe_100k_exp21']} (n_exp15={hbp['n_exp15']}, n_exp21={hbp['n_exp21']}) | {hbp['verdict']} |")
    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
