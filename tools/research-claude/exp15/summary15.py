#!/usr/bin/env python
"""EXP-15 summary: aggregate the 92-port run11 --variant j receipts (see EXP15_PLAN.md §3).

  python summary15.py
  python summary15.py --dir exp21 --ref gnd

Reads WORK_DIR/exp15/result_{tag}_*_any_j.json + failures.json, writes summary15.json, prints the
per-port markdown table and the aggregates, and plots exp15_err1M_hist.png.

  --dir DIR (default exp15) : WORK_DIR subdir to summarise instead of exp15 (still under WORK_DIR/DIR).
  --variant V (default j)   : run11 --variant to summarise.
  --tag TAG (default 260729): design id; receipts are result_{TAG}_*_any_{variant}[_gnd].json.  The
    SITE0/SITE1 pair agreement is n/a for designs without SITE port names (e.g. s5m6585).
  --ref any|gnd (default any) : run11 --ref to summarise; receipts are result_{tag}_*_any_{variant}[_gnd].json.
    The reproduction check vs exp13/j is skipped (reported n/a) when ref=gnd -- it's a different physics
    mode, not a reproduction of exp13/j.
  Output filenames get a _DIR suffix when --dir != exp15 (e.g. summary15_exp21.json), still written
  under WORK_DIR/DIR.
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
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import work_dir  # noqa: E402

TAG = "260729"
REPRO_PORTS = ["Port1_SITE0", "Port7_SITE0", "Port14_SITE0", "Port16_SITE0", "Port18_SITE0", "Port19_SITE0"]
PORT_NUM_RE = re.compile(r"^Port(\d+)_")


def vtag_of(variant, ref):
    return variant if ref == "any" else f"{variant}_gnd"


def port_num(port):
    m = PORT_NUM_RE.match(port)
    return int(m.group(1)) if m else -1


def load_receipts(out, vtag):
    receipts = {}
    for fn in sorted(glob.glob(os.path.join(out, f"result_{TAG}_*_any_{vtag}.json"))):
        r = json.loads(open(fn, encoding="utf-8").read())
        receipts[r["port"]] = r
    return receipts


def reproduction_check(receipts):
    """6 ports also present in WORK_DIR/exp13: max relative |dZ|/|Z| must be <= 1e-9."""
    rows = []
    for port in REPRO_PORTS:
        exp13_fn = work_dir("exp13") / f"result_{TAG}_{port}_any_j.json"
        if port not in receipts:
            rows.append(dict(port=port, status="no exp15 receipt"))
            continue
        if not exp13_fn.is_file():
            rows.append(dict(port=port, status="no exp13 receipt for this port"))
            continue
        r15 = receipts[port]
        r13 = json.loads(exp13_fn.read_text(encoding="utf-8"))
        z15 = np.array(r15["Z_re"]) + 1j * np.array(r15["Z_im"])
        z13 = np.array(r13["Z_re"]) + 1j * np.array(r13["Z_im"])
        if z15.shape != z13.shape:
            rows.append(dict(port=port, status="SHAPE MISMATCH"))
            continue
        max_rel = float(np.max(np.abs(z15 - z13) / np.abs(z13)))
        ok = max_rel <= 1e-9
        rows.append(dict(port=port, status="PASS" if ok else "FAIL", max_rel_dZ=max_rel))
    ok_rows = [r for r in rows if r["status"] in ("PASS", "FAIL")]
    reproduction_ok = bool(ok_rows) and all(r["status"] == "PASS" for r in ok_rows)
    print("\n### reproduction check vs exp13/j\n")
    for r in rows:
        extra = f"  max_rel_dZ={r['max_rel_dZ']:.2e}" if "max_rel_dZ" in r else ""
        print(f"  {r['port']:20s} {r['status']}{extra}")
    print(f"reproduction_ok={reproduction_ok}")
    return rows, reproduction_ok


def g3_fail_class(r):
    g = r["ladder_gates"]
    if g["PASS"]["G3"]:
        return None
    if r["dRe_100k_mOhm"] < -0.05:
        return "R-deficit"
    if r["fit_dL_pH"] > 5:
        return "L-excess"
    return "other"


def per_port_table(receipts):
    rows = []
    for port in sorted(receipts, key=port_num):
        r = receipts[port]
        g = r["ladder_gates"]
        rows.append(dict(port=port, num=port_num(port), rail=r["rail"], unknowns=r["unknowns"],
                         G1_mOhm=g["G1_max_abs_dRe_mOhm"], G2_dL=g["G2_dL_pH_range"], G3_err=g["G3_rel_err_1MHz"],
                         G4_err=g["G4_max_rel_err"], f_res_model=r["f_res_model"], f_res_ref=r["f_res_ref"],
                         PASS=g["PASS"], dRe_100k=r["dRe_100k_mOhm"], fit_dL=r["fit_dL_pH"], wall_s=r["wall_seconds"],
                         peak_rss_MB=max((s["rss_MB"] for s in r["stats"]), default=float("nan")),
                         g3_fail_class=g3_fail_class(r)))
    print("\n### per-port (sorted by port number)\n")
    print("| port | rail | unknowns | G1 mOhm | G2 dL pH | G3 err | G4 err | f_res model/ref MHz | PASS | dRe_100k mOhm | fit_dL pH | wall s | peak rss MB |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        p = r["PASS"]
        pass_str = "".join(g[1] for g in (("G1", "1" if p["G1"] else "."), ("G2", "2" if p["G2"] else "."),
                                          ("G3", "3" if p["G3"] else "."), ("G4", "4" if p["G4"] else ".")))
        print(f"| {r['port']} | {r['rail']} | {r['unknowns']} | {r['G1_mOhm']:.4f} | "
              f"{r['G2_dL'][0]:.2f}..{r['G2_dL'][1]:.2f} | {r['G3_err']:.4f} | {r['G4_err']:.4f} | "
              f"{r['f_res_model']/1e6:.3f}/{r['f_res_ref']/1e6:.3f} | {pass_str} | {r['dRe_100k']:.4f} | "
              f"{r['fit_dL']:.2f} | {r['wall_s']:.0f} | {r['peak_rss_MB']:.0f} |")
    return rows


def aggregates(receipts, rows, n_failed):
    n_total = len(rows) + n_failed
    pass_counts = {g: sum(1 for r in rows if r["PASS"][g]) for g in ("G1", "G2", "G3", "G4")}
    pass_rates = {g: (pass_counts[g] / n_total if n_total else float("nan")) for g in pass_counts}
    err = np.array([r["G3_err"] for r in rows]) * 100.0
    err_stats = dict(median=float(np.median(err)), q1=float(np.percentile(err, 25)), q3=float(np.percentile(err, 75)),
                     max=float(np.max(err))) if len(err) else dict(median=None, q1=None, q3=None, max=None)
    g3_fail_classes = {}
    for r in rows:
        c = r["g3_fail_class"]
        if c:
            g3_fail_classes[c] = g3_fail_classes.get(c, 0) + 1

    # SITE0/SITE1 agreement: Port k (SITE0, k=1..46) vs Port k+46 (SITE1).  Designs without SITE
    # port names (e.g. s5m6585's Port1_U1_0) have no such pairs -- reported as n/a.
    by_num = {r["num"]: r for r in rows if "_SITE" in r["port"]}
    pairs_checked = pairs_agree = 0
    for k in range(1, 47):
        a, b = by_num.get(k), by_num.get(k + 46)
        if a is None or b is None:
            continue
        pairs_checked += 1
        if a["PASS"] == b["PASS"]:
            pairs_agree += 1

    agg = dict(n_total=n_total, n_success=len(rows), n_failed=n_failed,
              pass_counts=pass_counts, pass_rates=pass_rates, err_1MHz_pct=err_stats,
              g3_fail_classes=g3_fail_classes, site_agreement=dict(pairs_checked=pairs_checked, pairs_agree=pairs_agree,
                                                                    rate=(pairs_agree / pairs_checked if pairs_checked else None)))
    print("\n### aggregates\n")
    print(f"n_total={n_total} n_success={len(rows)} n_failed={n_failed}")
    for g in ("G1", "G2", "G3", "G4"):
        print(f"  PASS {g}: {pass_counts[g]}/{n_total} = {pass_rates[g]:.1%}")
    print(f"  err_1MHz %: median={err_stats['median']} q1={err_stats['q1']} q3={err_stats['q3']} max={err_stats['max']}")
    print(f"  G3-FAIL classes: {g3_fail_classes}")
    print(f"  SITE0/SITE1 PASS-dict agreement: " + (f"{pairs_agree}/{pairs_checked}" if pairs_checked else "n/a (no SITE port pairs)"))
    return agg


def plot_hist(rows, fn):
    err = np.array([r["G3_err"] for r in rows]) * 100.0
    if not len(err):
        return
    lo, hi = max(err.min(), 1e-3), max(err.max(), 10.1)
    bins = np.logspace(np.log10(lo), np.log10(hi), 30)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(err, bins=bins, color="#4C72B0", edgecolor="white")
    ax.axvline(10.0, color="crimson", linestyle="--", label="G3 threshold (10%)")
    ax.set_xscale("log")
    ax.set_xlabel("err_1MHz (%)")
    ax.set_ylabel("port count")
    ax.set_title(f"EXP-15: 1 MHz error distribution, {TAG} {len(err)} ports")
    ax.legend()
    fig.tight_layout()
    fig.savefig(fn, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="exp15", help="WORK_DIR subdir to summarise (default exp15)")
    ap.add_argument("--variant", default="j", help="run11 --variant to summarise (default j)")
    ap.add_argument("--ref", default="any", choices=["any", "gnd"], help="run11 --ref to summarise (default any)")
    ap.add_argument("--tag", default="260729", help="design id whose receipts to summarise (default 260729)")
    a = ap.parse_args()
    global TAG
    TAG = a.tag

    out = work_dir(a.dir)
    vtag = vtag_of(a.variant, a.ref)
    suffix = f"_{a.dir}" if a.dir != "exp15" else ""

    receipts = load_receipts(out, vtag)
    failures = json.loads(open(os.path.join(out, "failures.json"), encoding="utf-8").read()) if os.path.exists(os.path.join(out, "failures.json")) else []
    if a.ref == "gnd" or a.variant != "j" or TAG != "260729":
        why = ("ref=gnd is a different reference-search mode" if a.ref == "gnd"
               else f"variant {a.variant} is not j" if a.variant != "j" else f"tag {TAG} is not 260729")
        print(f"\n### reproduction check vs exp13/j: n/a ({why})\n")
        repro_rows, reproduction_ok = [], "n/a"
    else:
        repro_rows, reproduction_ok = reproduction_check(receipts)
    rows = per_port_table(receipts)
    agg = aggregates(receipts, rows, len(failures))
    summary = dict(reproduction=repro_rows, reproduction_ok=reproduction_ok, per_port=rows, aggregates=agg,
                  failures=failures)
    fn = os.path.join(out, f"summary15{suffix}.json")
    json.dump(summary, open(fn, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("\nwrote", fn)
    hist_fn = os.path.join(out, f"exp15_err1M_hist{suffix}.png")
    plot_hist(rows, hist_fn)
    print("wrote", hist_fn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
