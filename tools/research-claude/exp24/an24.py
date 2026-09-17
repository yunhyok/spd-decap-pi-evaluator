#!/usr/bin/env python
"""EXP-24: site-asymmetry diagnosis for the 46 twin-net SITE0/SITE1 pairs (92-port dataset).
See WORK_DIR/exp24/EXP24_PLAN.md for the frozen definitions (Section 1) and thresholds
(Section 2) -- do not edit that file, do not change the numbers here. No model is run; this only
re-reads existing receipts (exp15/j) and exp5 extract pickles.

    python an24.py

Reads WORK_DIR/exp15/result_260729_*_any_j.json (92 receipts) and, per port, WORK_DIR/exp5/
extract_260729_{port}.pkl (only the keys needed: rail_nodes, rail_vias, rail_traces, decaps,
port_pos_nodes, rail_geoms, rail_net -- each pickle is dereferenced immediately after use so only
one is resident at a time). Writes WORK_DIR/exp24/exp24.json and exp24_site_ratio.png; prints a
markdown summary.
"""
from __future__ import annotations

import glob
import gc
import json
import os
import pickle
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import WORK_DIR, work_file  # noqa: E402

TAG = "260729"
PORT_NUM_RE = re.compile(r"^Port(\d+)_SITE([01])")

# frozen thresholds, EXP24_PLAN.md Section 2
SYM_TOL = 0.1          # H_sym_mod: |s_mod - 1| <= this counts as symmetric
SYM_ADOPT_FRAC = 0.8
ASYM_TOL = 0.2         # H_asym_ref: |s_ref - 1| > this counts as asymmetric
ASYM_ADOPT_FRAC = 0.3
ASYM_REJECT_FRAC = 0.1
SRC_DECAP_MAX = 10     # H_asym_src: decaps <= this = "small rail"
SRC_ADOPT_N = 7        # out of top-10 |s_ref-1| pairs


def load_receipts():
    d = WORK_DIR / "exp15"
    receipt_re = re.compile(r"^result_260729_(Port\d+_SITE[01](?:_\w+)?)_any_j\.json$")
    rows = {}
    for fn in sorted(glob.glob(os.path.join(d, "result_260729_*_any_j.json"))):
        base = os.path.basename(fn)
        if not receipt_re.match(base):
            continue
        r = json.loads(open(fn, encoding="utf-8").read())
        rows[r["port"]] = r
    return rows


def receipt_row(r):
    f = np.asarray(r["freq"], dtype=float)
    Zm = np.asarray(r["Z_re"], dtype=float) + 1j * np.asarray(r["Z_im"], dtype=float)
    Zr = np.asarray(r["Zref_re"], dtype=float) + 1j * np.asarray(r["Zref_im"], dtype=float)
    i100k = int(np.argmin(np.abs(f - 1e5)))
    i1m = int(np.argmin(np.abs(f - 1e6)))
    return dict(port=r["port"], tag=r["tag"], rail=r["rail"],
                R_mod=float(Zm[i100k].real), R_ref=float(Zr[i100k].real),
                absZ_mod_1M=float(abs(Zm[i1m])), absZ_ref_1M=float(abs(Zr[i1m])),
                dRe_100k_mOhm=float(r["dRe_100k_mOhm"]))


def extract_counts(tag, port):
    """Load WORK_DIR/exp5/extract_{tag}_{port}.pkl, pull only the needed keys, free the rest."""
    pkl = WORK_DIR / "exp5" / f"extract_{tag}_{port}.pkl"
    with open(pkl, "rb") as fh:
        d = pickle.load(fh)
    out = dict(
        n_rail_nodes=len(d["rail_nodes"]),
        n_rail_vias=len(d["rail_vias"]),
        n_rail_traces=len(d["rail_traces"]),
        n_decaps=len(d["decaps"]),
        n_port_pos_nodes=len(d["port_pos_nodes"]),
        layers=sorted({g["layer"] for g in d["rail_geoms"]}),
        rail_net=d["rail_net"],
    )
    del d
    gc.collect()
    return out


COUNT_KEYS = ["n_rail_nodes", "n_rail_vias", "n_rail_traces", "n_decaps", "n_port_pos_nodes"]


def build_pairs(receipts):
    by_num_site = {}
    for port, r in receipts.items():
        m = PORT_NUM_RE.match(port)
        if not m:
            continue
        by_num_site[(int(m.group(1)), int(m.group(2)))] = port

    pairs = []
    for k in range(1, 47):
        p0 = by_num_site.get((k, 0))
        p1 = by_num_site.get((k + 46, 1))
        if p0 is None or p1 is None:
            continue
        rr0 = receipt_row(receipts[p0])
        rr1 = receipt_row(receipts[p1])
        ex0 = extract_counts(TAG, p0)
        ex1 = extract_counts(TAG, p1)

        diffs = [ck for ck in COUNT_KEYS if ex0[ck] != ex1[ck]]
        layers_equal = ex0["layers"] == ex1["layers"]
        if not layers_equal:
            diffs.append("layers")
        iso = len(diffs) == 0

        s_mod = rr1["R_mod"] / rr0["R_mod"] if rr0["R_mod"] else float("nan")
        s_ref = rr1["R_ref"] / rr0["R_ref"] if rr0["R_ref"] else float("nan")
        s_mod_1M = rr1["absZ_mod_1M"] / rr0["absZ_mod_1M"] if rr0["absZ_mod_1M"] else float("nan")
        s_ref_1M = rr1["absZ_ref_1M"] / rr0["absZ_ref_1M"] if rr0["absZ_ref_1M"] else float("nan")

        pairs.append(dict(
            k=k, port_S0=p0, port_S1=p1, rail_S0=receipts[p0]["rail"], rail_S1=receipts[p1]["rail"],
            rail_net_S0=ex0["rail_net"], rail_net_S1=ex1["rail_net"],
            counts_S0={ck: ex0[ck] for ck in COUNT_KEYS}, counts_S1={ck: ex1[ck] for ck in COUNT_KEYS},
            layers_S0=ex0["layers"], layers_S1=ex1["layers"],
            iso=iso, diffs=diffs,
            R_mod_S0_mOhm=rr0["R_mod"] * 1e3, R_mod_S1_mOhm=rr1["R_mod"] * 1e3,
            R_ref_S0_mOhm=rr0["R_ref"] * 1e3, R_ref_S1_mOhm=rr1["R_ref"] * 1e3,
            dRe_100k_S0_mOhm=rr0["dRe_100k_mOhm"], dRe_100k_S1_mOhm=rr1["dRe_100k_mOhm"],
            s_mod=s_mod, s_ref=s_ref, s_mod_1MHz=s_mod_1M, s_ref_1MHz=s_ref_1M,
            decaps_S0=ex0["n_decaps"], decaps_S1=ex1["n_decaps"],
        ))
    return pairs


def verdict(adopt, reject):
    if adopt:
        return "adopt"
    if reject:
        return "reject"
    return "indeterminate"


def run_hypotheses(pairs):
    iso_pairs = [p for p in pairs if p["iso"]]
    n_iso = len(iso_pairs)

    s_mod = np.array([p["s_mod"] for p in iso_pairs])
    ok = np.isfinite(s_mod)
    frac_sym = float(np.mean(np.abs(s_mod[ok] - 1) <= SYM_TOL)) if ok.any() else None
    h_sym_mod = dict(n=int(ok.sum()), fraction_within_tol=frac_sym,
                      verdict=verdict(frac_sym is not None and frac_sym >= SYM_ADOPT_FRAC, False))

    s_ref = np.array([p["s_ref"] for p in iso_pairs])
    ok2 = np.isfinite(s_ref)
    frac_asym = float(np.mean(np.abs(s_ref[ok2] - 1) > ASYM_TOL)) if ok2.any() else None
    h_asym_ref = dict(n=int(ok2.sum()), fraction_asymmetric=frac_asym,
                       verdict=verdict(frac_asym is not None and frac_asym >= ASYM_ADOPT_FRAC,
                                        frac_asym is not None and frac_asym < ASYM_REJECT_FRAC))

    h_asym_src = None
    if h_asym_ref["verdict"] == "adopt":
        ranked = sorted((p for p in iso_pairs if np.isfinite(p["s_ref"])),
                         key=lambda p: abs(p["s_ref"] - 1), reverse=True)[:10]
        n_small = sum(1 for p in ranked if p["decaps_S0"] <= SRC_DECAP_MAX)
        classification = "feed-path-dominated small rails" if n_small >= SRC_ADOPT_N else "mixed"
        h_asym_src = dict(n_top=len(ranked), n_small_decap=n_small, classification=classification,
                           top_pairs_k=[p["k"] for p in ranked])

    return dict(n_iso=n_iso, H_sym_mod=h_sym_mod, H_asym_ref=h_asym_ref, H_asym_src=h_asym_src)


def make_plot(pairs, out_png):
    fig, ax = plt.subplots(figsize=(7, 7))
    for p in pairs:
        if not (np.isfinite(p["s_mod"]) and np.isfinite(p["s_ref"])):
            continue
        style = dict(facecolors="steelblue" if p["iso"] else "none",
                     edgecolors="steelblue" if p["iso"] else "crimson", s=45, linewidths=1.3)
        ax.scatter(p["s_mod"], p["s_ref"], **style)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.axhline(1, color="gray", lw=0.7)
    ax.axvline(1, color="gray", lw=0.7)
    ax.set_xlabel("s_mod = R_mod(SITE1) / R_mod(SITE0)  @100kHz")
    ax.set_ylabel("s_ref = R_ref(SITE1) / R_ref(SITE0)  @100kHz")
    n_iso = sum(1 for p in pairs if p["iso"])
    ax.set_title(f"EXP-24: site ratio, model vs reference (n={len(pairs)} pairs, {n_iso} extract-isomorphic)")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def main():
    receipts = load_receipts()
    pairs = build_pairs(receipts)
    hyps = run_hypotheses(pairs)

    non_iso = [p for p in pairs if not p["iso"]]
    top10 = sorted((p for p in pairs if p["iso"] and np.isfinite(p["s_ref"])),
                    key=lambda p: abs(p["s_ref"] - 1), reverse=True)[:10]

    out = dict(pairs=pairs, n_iso=hyps["n_iso"], hypotheses=dict(
        H_sym_mod=hyps["H_sym_mod"], H_asym_ref=hyps["H_asym_ref"], H_asym_src=hyps["H_asym_src"]))

    json_fn = work_file("exp24", "exp24.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp24", "exp24_site_ratio.png")
    make_plot(pairs, png_fn)

    print(f"\n### EXP-24 site-asymmetry diagnosis (n={len(pairs)} pairs, n_iso={hyps['n_iso']})\n")
    print("| hypothesis | stat | verdict |")
    print("|---|---|---|")
    hsm, har = hyps["H_sym_mod"], hyps["H_asym_ref"]
    print(f"| H_sym_mod | frac(|s_mod-1|<={SYM_TOL})={hsm['fraction_within_tol']} (n={hsm['n']}) | {hsm['verdict']} |")
    print(f"| H_asym_ref | frac(|s_ref-1|>{ASYM_TOL})={har['fraction_asymmetric']} (n={har['n']}) | {har['verdict']} |")
    if hyps["H_asym_src"] is not None:
        has_ = hyps["H_asym_src"]
        print(f"| H_asym_src | {has_['n_small_decap']}/{has_['n_top']} top pairs have decaps<={SRC_DECAP_MAX} | {has_['classification']} |")
    else:
        print("| H_asym_src | not evaluated (H_asym_ref not adopted) | n/a |")

    print(f"\n#### non-isomorphic pairs ({len(non_iso)})\n")
    if non_iso:
        print("| k | port_S0 | port_S1 | diffs |")
        print("|---|---|---|---|")
        for p in non_iso:
            print(f"| {p['k']} | {p['port_S0']} | {p['port_S1']} | {', '.join(p['diffs'])} |")
    else:
        print("(none)")

    print(f"\n#### 10 pairs with largest |s_ref-1| (iso pairs)\n")
    print("| k | port_S0 | port_S1 | decaps(S0) | s_mod | s_ref | R_ref_S0 (mOhm) | R_ref_S1 (mOhm) |")
    print("|---|---|---|---|---|---|---|---|")
    for p in top10:
        print(f"| {p['k']} | {p['port_S0']} | {p['port_S1']} | {p['decaps_S0']} | {p['s_mod']:.4f} | "
              f"{p['s_ref']:.4f} | {p['R_ref_S0_mOhm']:.4f} | {p['R_ref_S1_mOhm']:.4f} |")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
