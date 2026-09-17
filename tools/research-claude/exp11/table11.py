#!/usr/bin/env python
"""EXP-11/12 comparison: variant receipts (WORK_DIR/exp11 or, with --exp exp12, WORK_DIR/exp12)
vs the frozen exp8 receipts (docs/.../results/exp8).

  python table11.py --variant a
  python table11.py --exp exp12 --variant c

Prints a markdown table and writes WORK_DIR/exp11/compare_{variant}.json with, per case, the ladder-point
max |dZ|/|Zbase| (f <= 10 MHz and at the point nearest 100 MHz), both G1-G5 metric sets, the PASS dicts and
err_1MHz, plus the plan's two verdicts:
  h_null_confirmed : every case has max rel dZ (f <= 10 MHz) <= 1e-3 and an unchanged PASS dict
  adopt            : no PASS->FAIL anywhere and (some FAIL->PASS or the median G3 error drops by >= 0.01)
Missing receipts are listed and the script exits 1.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import REPO_DIR, work_dir  # noqa: E402

CASES = [("260729", "Port1_SITE0"), ("260729", "Port7_SITE0"), ("260729", "Port14_SITE0"), ("260729", "Port16_SITE0"),
         ("260729", "Port18_SITE0"), ("260729", "Port19_SITE0"), ("260804", "Port18_SITE0")]


def base_file(tag, port, baseline):
    """exp8: the frozen receipt; exp13:j: WORK_DIR/exp13/result_{tag}_{port}_any_j.json (the EXP-14 baseline)."""
    if baseline == "exp8":
        return REPO_DIR / "docs" / "research-claude" / "2026-09-15" / "results" / "exp8" / f"result_{tag}_{port}_any.json"
    exp, _, var = baseline.partition(":")
    return work_dir(exp) / f"result_{tag}_{port}_any_{var}.json"


def load(tag, port, variant, out, baseline):
    """newest matching variant receipt (run11 adds _HHMMSS instead of overwriting), and the baseline"""
    hits = sorted(glob.glob(os.path.join(out, f"result_{tag}_{port}_any_{variant}*.json")), key=os.path.getmtime)
    base = base_file(tag, port, baseline)
    if not hits or not base.is_file():
        return None, str(base if not base.is_file() else ""), hits
    return json.loads(open(hits[-1], encoding="utf-8").read()), json.loads(base.read_text(encoding="utf-8")), hits[-1]


def zof(r):
    return np.array(r["Z_re"]) + 1j * np.array(r["Z_im"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="a", choices=["a", "b", "ab", "c", "d", "j", "jab", "e1", "e2", "f", "g", "h", "k", "k2", "m", "mk", "p", "q", "pmk", "pv"])
    ap.add_argument("--exp", default="exp11", choices=["exp11", "exp12", "exp13", "exp14", "exp18", "exp20", "exp28", "exp32", "exp35", "exp36"],
                    help="results directory WORK_DIR/<exp> (c/d live in exp12, j/jab in exp13, e1/e2/f/g/h in exp14, k/k2 in exp18, m/mk in exp20, p in exp28, q in exp32, pv in exp36)")
    ap.add_argument("--baseline", default="exp8", choices=["exp8", "exp13:j", "exp28:p"],
                    help="exp8: the frozen receipts (default); exp13:j: the EXP-13 variant j receipts (EXP-14 baseline); exp28:p: the EXP-28 variant p receipts (EXP-32 baseline)")
    a = ap.parse_args()
    out = work_dir(a.exp)
    rows = []; missing = []
    for tag, port in CASES:
        v, b, fn = load(tag, port, a.variant, out, a.baseline)
        if v is None:
            missing.append(f"{tag} {port}: {a.baseline + ' baseline ' + b if b else 'no %s receipt result_%s_%s_any_%s*.json' % (a.exp, tag, port, a.variant)}")
            continue
        f = np.array(v["freq"]); fb = np.array(b["freq"])
        if f.shape != fb.shape or not np.array_equal(f, fb):
            print(f"FREQ MISMATCH {tag} {port}"); return 1
        Z, Zb = zof(v), zof(b)
        rel = np.abs(Z - Zb) / np.abs(Zb)
        lo = f <= 1.001e7
        k100 = int(np.argmin(np.abs(f - 1e8)))
        g, gb = v["ladder_gates"], b["ladder_gates"]
        chg = {k: [gb["PASS"][k], g["PASS"][k]] for k in gb["PASS"] if gb["PASS"][k] != g["PASS"][k]}
        rows.append(dict(tag=tag, port=port, receipt=os.path.basename(fn),
                         max_rel_dZ_le_10MHz=float(rel[lo].max()), f_at_max_le_10MHz=float(f[lo][int(np.argmax(rel[lo]))]),
                         rel_dZ_at_100MHz=float(rel[k100]), f_100MHz=float(f[k100]),
                         err_1MHz=[b["err_1MHz"], v["err_1MHz"]],
                         gates_base={k: gb[k] for k in gb}, gates_variant={k: g[k] for k in g}, pass_changes=chg))
    if missing:
        print("MISSING RECEIPTS:"); [print("  " + m) for m in missing]; return 1
    g3b = np.median([r["gates_base"]["G3_rel_err_1MHz"] for r in rows])
    g3v = np.median([r["gates_variant"]["G3_rel_err_1MHz"] for r in rows])
    p2f = any(k for r in rows for k, (o, n) in r["pass_changes"].items() if o and not n)
    f2p = any(k for r in rows for k, (o, n) in r["pass_changes"].items() if n and not o)
    res = dict(variant=a.variant, baseline=a.baseline, cases=rows, median_G3_base=float(g3b), median_G3_variant=float(g3v),
               h_null_confirmed=bool(all(r["max_rel_dZ_le_10MHz"] <= 1e-3 and not r["pass_changes"] for r in rows)),
               any_pass_to_fail=bool(p2f), any_fail_to_pass=bool(f2p),
               adopt=bool((not p2f) and (f2p or (g3b - g3v) >= 0.01)))
    print(f"\n### {a.exp} variant {a.variant} vs {a.baseline} baseline\n")
    print("| case | max relΔZ (f≤10MHz) | at f | relΔZ @100MHz | err1M base→var | G1 mΩ | G2 dL pH | G3 | G4 | G5 max | PASS change |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        g, gb = r["gates_variant"], r["gates_base"]
        pair = lambda k, fmt: f"{fmt.format(gb[k])}→{fmt.format(g[k])}"  # noqa: E731
        g2 = lambda d: max(abs(d["G2_dL_pH_range"][0]), abs(d["G2_dL_pH_range"][1]))  # noqa: E731
        g5 = lambda d: max(d["G5_rel_err"]) if d["G5_rel_err"] else float("nan")  # noqa: E731
        print(f"| {r['tag']} {r['port']} | {r['max_rel_dZ_le_10MHz']:.2e} | {r['f_at_max_le_10MHz']:.3g} | {r['rel_dZ_at_100MHz']:.2e} | "
              f"{100*r['err_1MHz'][0]:.2f}%→{100*r['err_1MHz'][1]:.2f}% | {pair('G1_max_abs_dRe_mOhm', '{:.4f}')} | "
              f"{g2(gb):.2f}→{g2(g):.2f} | {pair('G3_rel_err_1MHz', '{:.4f}')} | {pair('G4_max_rel_err', '{:.4f}')} | "
              f"{g5(gb):.3f}→{g5(g):.3f} | {r['pass_changes'] or '-'} |")
    print(f"\nmedian G3: {g3b:.4f} -> {g3v:.4f}   h_null_confirmed={res['h_null_confirmed']}   adopt={res['adopt']}")
    fn = os.path.join(out, f"compare_{a.variant}.json")
    json.dump(res, open(fn, "w"), indent=1)
    print("wrote", fn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
