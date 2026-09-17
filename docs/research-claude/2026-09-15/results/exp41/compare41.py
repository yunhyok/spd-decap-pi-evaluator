#!/usr/bin/env python
"""EXP-41 step 2: port-matched comparison of 260804/p (exp41) vs 260729/p (exp28), by port NAME.

  python compare41.py

Reads WORK_DIR/exp41/result_260804_*_any_p.json and WORK_DIR/exp28/result_260729_*_any_p.json,
matches by port name string (same convention as exp35/compare92.py's r_ratio), and writes
WORK_DIR/exp41/compare41_260804_vs_260729.json: a list of dicts with the same schema as
compare92.py's rows (eb/eq err_1MHz, rb/rq R ratio @100kHz, pb/pq PASS dict) plus a 'tag' field
identifying which side is which ('260804' for the *_q fields, '260729' for the *_b fields), and
prints the summary requested in EXP41_PLAN.md: G1-G4 PASS counts for 260804, median/IQR err and R
ratio, Pearson/Spearman correlation of err_1MHz between designs, and unmatched port count.
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "..", "tools", "research-claude", "common"))
# fallback: locate common/ relative to repo, since this script lives under WORK_DIR not the repo
for cand in [
    os.path.join(HERE, "..", "..", "..", "..", "tools", "research-claude", "common"),
]:
    if os.path.isdir(cand):
        sys.path.insert(0, cand)


def load_ports(pattern, prefix, suffix):
    out = {}
    for fn in sorted(glob.glob(pattern)):
        base = os.path.basename(fn)
        if not (base.startswith(prefix) and base.endswith(suffix)):
            continue
        port = base[len(prefix):-len(suffix)]
        out[port] = json.loads(open(fn, encoding="utf-8").read())
    return out


def r_ratio(rec):
    """Re(Zref)/Re(Zmodel) at the ladder point nearest 100 kHz (same def as exp35/compare92.py)."""
    f = np.asarray(rec["freq"])
    i = int(np.argmin(np.abs(f - 1e5)))
    return float(np.asarray(rec["Zref_re"])[i] / np.asarray(rec["Z_re"])[i])


def iqr(a):
    a = np.asarray(a)
    return float(np.median(a)), float(np.percentile(a, 25)), float(np.percentile(a, 75))


def main():
    work_dir = os.path.dirname(HERE)  # WORK_DIR
    exp41_dir = HERE
    exp28_dir = os.path.join(work_dir, "exp28")

    new = load_ports(os.path.join(exp41_dir, "result_260804_*_any_p.json"), "result_260804_", "_any_p.json")
    old = load_ports(os.path.join(exp28_dir, "result_260729_*_any_p.json"), "result_260729_", "_any_p.json")

    matched = sorted(set(new) & set(old))
    unmatched_260804 = sorted(set(new) - set(old))
    unmatched_260729 = sorted(set(old) - set(new))

    rows = []
    for port in matched:
        n, o = new[port], old[port]
        rows.append(dict(port=port, tag="260804",
                         eb=o["err_1MHz"], eq=n["err_1MHz"],
                         rb=r_ratio(o), rq=r_ratio(n),
                         pb=o["ladder_gates"]["PASS"], pq=n["ladder_gates"]["PASS"]))

    out_fn = os.path.join(exp41_dir, "compare41_260804_vs_260729.json")
    json.dump(rows, open(out_fn, "w", encoding="utf-8"), indent=1)
    print("wrote", out_fn)

    n = len(rows)
    print(f"\n### compare41: 260804 (n={len(new)}) vs 260729/p exp28 (n={len(old)}), matched={n}")
    print(f"unmatched 260804-only: {len(unmatched_260804)} {unmatched_260804}")
    print(f"unmatched 260729-only: {len(unmatched_260729)} {unmatched_260729}")

    if n == 0:
        print("no matched ports; nothing further to compute")
        return 0

    eq = [r["eq"] for r in rows]
    eb = [r["eb"] for r in rows]
    rq = [r["rq"] for r in rows]

    print("\n### G1-G4 PASS counts, 260804 (matched ports)")
    for g in ("G1", "G2", "G3", "G4"):
        c = sum(1 for r in rows if r["pq"][g])
        print(f"  {g}: {c}/{n} = {c/n:.1%}")

    m, q1, q3 = iqr(eq)
    print(f"\nerr_1MHz (260804): median={m:.4f} IQR=[{q1:.4f},{q3:.4f}]")
    m, q1, q3 = iqr(eb)
    print(f"err_1MHz (260729/exp28 matched subset): median={m:.4f} IQR=[{q1:.4f},{q3:.4f}]")
    m, q1, q3 = iqr(rq)
    print(f"R ratio @100kHz (260804): median={m:.4f} IQR=[{q1:.4f},{q3:.4f}]")

    pear = stats.pearsonr(eb, eq)
    spear = stats.spearmanr(eb, eq)
    print(f"\nerr_1MHz correlation (260729 exp28/p vs 260804), matched ports:")
    print(f"  Pearson r={pear[0]:.4f} p={pear[1]:.2e}")
    print(f"  Spearman rho={spear.correlation:.4f} p={spear.pvalue:.2e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
