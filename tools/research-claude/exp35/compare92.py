#!/usr/bin/env python
"""EXP-35: reusable 92-port comparison between two run11 receipt sets (reproduces the ad-hoc
compare92_*.json files from EXP-28/32, whose one-off script was never saved).

  python compare92.py --exp exp32 --variant q --baseline exp28:p
  python compare92.py --exp exp28 --variant p --baseline exp15:j
  python compare92.py --selftest

Reads WORK_DIR/<exp>/result_{tag}_{port}_any_{variant}.json (the "variant" receipts) and
WORK_DIR/<baseline exp>/result_{tag}_{port}_any_{baseline variant}.json (the baseline receipts).
The port set is whatever baseline receipts exist, found by globbing the exact "..._any_{W}.json"
suffix -- this already excludes resume duplicates, which run11 saves as "..._any_{W}_HHMMSS.json"
(a 6-digit time suffix) alongside the real file, so the plain glob never matches them.

Per port, reverse-engineered from matching compare92_q_vs_p.json / compare92_p_vs_j.json exactly:
  eb/eq : err_1MHz, baseline/variant (same value as ladder_gates.G3_rel_err_1MHz)
  rb/rq : Re(Zref)/Re(Zmodel) at the ladder point nearest 100 kHz, baseline/variant -- this is the
          "Re Z_ref/Re Z_model" ratio the EXP-28/32 reports quote at the 100 kHz gate point (matches
          dRe_100k_mOhm's frequency). Despite reports calling it a "median", it is a single-point
          ratio: confirmed by exact (<1e-9) match against both reference files, whereas medians over
          <=10MHz or the full band do not match.
  pb/pq : ladder_gates.PASS dict, baseline/variant

Writes compare92_{variant}_vs_{baseline_variant}.json to WORK_DIR/<exp>/ and never overwrites an
existing file (adds a _HHMMSS suffix instead). Prints a markdown summary.

H3 (10-100 MHz) metric: pooled over every ladder point with 10 MHz <= f <= 100 MHz across all ports,
dR = Re(Z_model) - Re(Z_ref). Reported: fraction of those (port, point) pairs with dR < 0, and the
median of |dR|/Re(Z_ref) over the same pooled set. Computed separately for baseline and variant.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import work_dir  # noqa: E402


def load_ports(exp, tag, variant):
    """{port: receipt} for the exact (non-resume-duplicate) receipts in WORK_DIR/<exp>."""
    pat = os.path.join(str(work_dir(exp)), f"result_{tag}_*_any_{variant}.json")
    out = {}
    prefix, suffix = f"result_{tag}_", f"_any_{variant}.json"
    for fn in sorted(glob.glob(pat)):
        port = os.path.basename(fn)[len(prefix):-len(suffix)]
        out[port] = json.loads(open(fn, encoding="utf-8").read())
    return out


def r_ratio(rec):
    """Re(Zref)/Re(Zmodel) at the ladder point nearest 100 kHz."""
    f = np.asarray(rec["freq"])
    i = int(np.argmin(np.abs(f - 1e5)))
    return float(np.asarray(rec["Zref_re"])[i] / np.asarray(rec["Z_re"])[i])


def h3_points(rec):
    """(dR, Re(Zref)) pairs for ladder points with 10 MHz <= f <= 100 MHz."""
    f = np.asarray(rec["freq"])
    band = (f >= 1e7) & (f <= 1e8)
    zre, zref = np.asarray(rec["Z_re"])[band], np.asarray(rec["Zref_re"])[band]
    return zre - zref, zref


def build_rows(exp, variant, base_exp, base_variant, tag):
    variants = load_ports(exp, tag, variant)
    baselines = load_ports(base_exp, tag, base_variant)
    missing = [p for p in baselines if p not in variants]
    if missing:
        print(f"MISSING {exp}/{variant} receipts for ports: {missing}", file=sys.stderr)
        sys.exit(1)
    rows = []
    for port, b in baselines.items():
        v = variants[port]
        rows.append(dict(port=port, eb=b["err_1MHz"], eq=v["err_1MHz"],
                         rb=r_ratio(b), rq=r_ratio(v),
                         pb=b["ladder_gates"]["PASS"], pq=v["ladder_gates"]["PASS"]))
    return rows, baselines, variants


def h3_summary(receipts):
    """(fraction of pooled 10-100MHz points with dR<0, median |dR|/Re(Zref) over those points)."""
    dRs, zrefs = [], []
    for rec in receipts.values():
        dR, zref = h3_points(rec)
        dRs.append(dR); zrefs.append(zref)
    dR = np.concatenate(dRs) if dRs else np.array([])
    zref = np.concatenate(zrefs) if zrefs else np.array([])
    if not len(dR):
        return None
    return float(np.mean(dR < 0)), float(np.median(np.abs(dR) / np.abs(zref)))


def iqr(a):
    a = np.asarray(a)
    return float(np.median(a)), float(np.percentile(a, 25)), float(np.percentile(a, 75))


def print_summary(rows, baselines, variants, variant, base_variant):
    n = len(rows)
    eb, eq = [r["eb"] for r in rows], [r["eq"] for r in rows]
    rb, rq = [r["rb"] for r in rows], [r["rq"] for r in rows]
    print(f"\n### compare92: {variant} vs {base_variant} ({n} ports)\n")
    mb, q1b, q3b = iqr(eb); mq, q1q, q3q = iqr(eq)
    print(f"err_1MHz: baseline median {mb:.4f} (IQR {q1b:.4f}-{q3b:.4f})  ->  "
          f"{base_variant} variant {mq:.4f} (IQR {q1q:.4f}-{q3q:.4f})")
    improved = sum(1 for r in rows if r["eq"] < r["eb"])
    worsened = sum(1 for r in rows if r["eq"] > r["eb"])
    print(f"improved: {improved}   worsened: {worsened}   unchanged: {n - improved - worsened}")
    mb, q1b, q3b = iqr(rb); mq, q1q, q3q = iqr(rq)
    print(f"R ratio (Re Zref/Re Zmodel @100kHz): baseline median {mb:.4f} (IQR {q1b:.4f}-{q3b:.4f})  ->  "
          f"variant median {mq:.4f} (IQR {q1q:.4f}-{q3q:.4f})")
    print("\n| gate | PASS base | PASS variant | FAIL->PASS | PASS->FAIL |")
    print("|---|---|---|---|---|")
    for g in ("G1", "G2", "G3", "G4"):
        pb = sum(1 for r in rows if r["pb"][g]); pq = sum(1 for r in rows if r["pq"][g])
        f2p = sum(1 for r in rows if not r["pb"][g] and r["pq"][g])
        p2f = sum(1 for r in rows if r["pb"][g] and not r["pq"][g])
        print(f"| {g} | {pb}/{n} | {pq}/{n} | {f2p} | {p2f} |")
    hb, hv = h3_summary(baselines), h3_summary(variants)
    print("\nH3 (10-100 MHz, pooled ladder points, dR = Re(Zmodel)-Re(Zref)):")
    if hb and hv:
        print(f"  baseline: frac(dR<0)={hb[0]:.3f}  median|dR|/Re(Zref)={hb[1]:.4f}")
        print(f"  variant : frac(dR<0)={hv[0]:.3f}  median|dR|/Re(Zref)={hv[1]:.4f}")
    else:
        print("  n/a: no ladder points fall in [10 MHz, 100 MHz] for these receipts")


def write_output(rows, exp, variant, base_variant, out_path):
    if out_path:
        fn = out_path
    else:
        fn = os.path.join(str(work_dir(exp)), f"compare92_{variant}_vs_{base_variant}.json")
        if os.path.exists(fn):
            stamped = fn[:-5] + "_" + datetime.now().strftime("%H%M%S") + ".json"
            print(f"{fn} already exists; writing {stamped} instead")
            fn = stamped
    json.dump(rows, open(fn, "w", encoding="utf-8"), indent=1)
    print("wrote", fn)


def selftest():
    """Reproduce WORK_DIR/exp32/compare92_q_vs_p.json exactly (mandatory), and check what's
    comparable against WORK_DIR/exp28/compare92_p_vs_j.json -- an older ad-hoc file that used a
    different R-field convention (db/dp = dRe_100k_mOhm, an absolute delta) instead of a ratio, so
    that one field cannot match by construction; eb/e<variant> and pb/p<variant> still must.
    """
    cases = [
        ("exp32", "q", "exp28", "p", True),   # exact match required: eb,eq,rb,rq,pb,pq
        ("exp28", "p", "exp15", "j", False),  # legacy file uses eb,ep,pb,pp,db,dp,zr (no ratio field)
    ]
    ok = True
    for exp, variant, base_exp, base_variant, exact_r in cases:
        ref_fn = os.path.join(str(work_dir(exp)), f"compare92_{variant}_vs_{base_variant}.json")
        if not os.path.isfile(ref_fn):
            print(f"SKIP {ref_fn}: not found"); continue
        ref = json.loads(open(ref_fn, encoding="utf-8").read())
        rows, _, _ = build_rows(exp, variant, base_exp, base_variant, "260729")
        same_order = [r["port"] for r in rows] == [r["port"] for r in ref]
        ekey, pkey = f"e{variant}", f"p{variant}"  # legacy per-variant key names
        close = same_order and all(
            abs(a["eb"] - b["eb"]) < 1e-9 and abs(a["eq"] - b[ekey]) < 1e-9 and
            a["pb"] == b["pb"] and a["pq"] == b[pkey]
            for a, b in zip(rows, ref))
        if exact_r:
            close = close and all(abs(a["rb"] - b["rb"]) < 1e-9 and abs(a["rq"] - b["rq"]) < 1e-9
                                   for a, b in zip(rows, ref))
            r_note = "rb/rq matched exactly"
        else:
            r_note = "R field skipped: legacy file's db/dp is dRe_100k_mOhm (absolute mOhm delta), not a ratio"
        print(f"{'PASS' if close else 'FAIL'}: {exp} {variant} vs {base_exp}:{base_variant} "
              f"({len(rows)} rows, order {'ok' if same_order else 'MISMATCH'}, {r_note})")
        ok = ok and close
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", help="variant results dir, e.g. exp32")
    ap.add_argument("--variant", help="variant tag, e.g. q")
    ap.add_argument("--baseline", help="expMM:W, e.g. exp28:p")
    ap.add_argument("--tag", default="260729")
    ap.add_argument("--out", default=None, help="override output path")
    ap.add_argument("--selftest", action="store_true", help="reproduce the known compare92_*.json files and exit")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(0 if selftest() else 1)

    if not (a.exp and a.variant and a.baseline):
        ap.error("--exp/--variant/--baseline are required (or use --selftest)")
    base_exp, _, base_variant = a.baseline.partition(":")
    rows, baselines, variants = build_rows(a.exp, a.variant, base_exp, base_variant, a.tag)
    print_summary(rows, baselines, variants, a.variant, base_variant)
    write_output(rows, a.exp, a.variant, base_variant, a.out)


if __name__ == "__main__":
    main()
