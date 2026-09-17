#!/usr/bin/env python
"""EXP-25: element-wise R breakdown of the discordant SITE0/SITE1 twin pairs (receipts + extract
only, no model run). See WORK_DIR/exp25/EXP25_PLAN.md for the frozen definitions/thresholds --
do not edit that file, do not change the numbers here.

    python an25.py

Reads WORK_DIR/exp24/exp24.json (pair list with s_mod, s_ref, decaps_S0), then per selected pair
per port: WORK_DIR/exp15/result_260729_{port}_any_j.json (breakdown_100k, two_sided_layers) and
WORK_DIR/exp5/extract_260729_{port}.pkl (rail_nodes, rail_traces, rail_vias, port_pos_nodes; only
these keys are pulled out, the rest of the pickle is dropped immediately). Writes
WORK_DIR/exp25/exp25.json and exp25_breakdown.png; prints a markdown summary.
"""
from __future__ import annotations

import gc
import json
import math
import os
import pickle
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import WORK_DIR, work_file  # noqa: E402

TAG = "260729"

# frozen thresholds, EXP25_PLAN.md Section 1-2
N_DISCORDANT = 8
CONTROL_TOL = 0.105        # |ln(s_mod/s_ref)| <= this for control-eligible
CONTROL_DECAP_MAX = 10
N_CONTROL = 4
H_TR_CONTRIB_MIN = 0.5     # |contribution| >= this for a "strong" traces dominance
H_TR_MAJORITY = 5          # out of 8
H_W_RATIO_HI = 2.0
H_W_RATIO_LO = 0.5
H_W_MAJORITY = 5           # out of 8


def load_exp24_pairs():
    d = json.load(open(work_file("exp24", "exp24.json"), encoding="utf-8"))
    return d["pairs"]


def select_pairs(pairs):
    """Section 1: discordant = top 8 by |ln(s_mod/s_ref)|; control = 4 smallest among
    |ln(s_mod/s_ref)| <= CONTROL_TOL and decaps_S0 <= CONTROL_DECAP_MAX."""
    scored = []
    for p in pairs:
        s_mod, s_ref = p.get("s_mod"), p.get("s_ref")
        if s_mod is None or s_ref is None:
            continue
        if not (np.isfinite(s_mod) and np.isfinite(s_ref)) or s_mod <= 0 or s_ref <= 0:
            continue
        metric = abs(math.log(s_mod / s_ref))
        scored.append((metric, p))

    scored.sort(key=lambda t: t[0], reverse=True)
    discordant = scored[:N_DISCORDANT]

    control_pool = sorted(
        (t for t in scored if t[0] <= CONTROL_TOL and t[1]["decaps_S0"] <= CONTROL_DECAP_MAX),
        key=lambda t: t[0],
    )
    control_note = None
    if len(control_pool) < N_CONTROL:
        control_note = (f"only {len(control_pool)} control-eligible pairs found "
                         f"(|ln(s_mod/s_ref)|<={CONTROL_TOL}, decaps_S0<={CONTROL_DECAP_MAX}), "
                         f"wanted {N_CONTROL}")
        print(f"[an25] WARNING: {control_note}")
    control = control_pool[:N_CONTROL]

    return discordant, control, control_note


def load_receipt_breakdown(port):
    fn = work_file("exp15", f"result_{TAG}_{port}_any_j.json")
    r = json.loads(open(fn, encoding="utf-8").read())
    return r["breakdown_100k"], r["two_sided_layers"]


def load_extract_slim(port):
    """Pull only the keys an25 needs out of the exp5 pickle, then drop the rest."""
    fn = work_file("exp5", f"extract_{TAG}_{port}.pkl")
    with open(fn, "rb") as fh:
        d = pickle.load(fh)
    out = dict(
        rail_nodes=d["rail_nodes"],
        rail_traces=d["rail_traces"],
        rail_vias=d["rail_vias"],
        port_pos_nodes=d["port_pos_nodes"],
    )
    del d
    gc.collect()
    return out


def trace_length_um(t, rail_nodes):
    n0, n1, _w = t
    p0, p1 = rail_nodes.get(n0), rail_nodes.get(n1)
    if p0 is None or p1 is None:
        return None
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])


def port_stats(port):
    """Per-port R element breakdown (exp15/j) + extract geometry/topology stats (exp5)."""
    bd, two_sided_layers = load_receipt_breakdown(port)
    ex = load_extract_slim(port)

    layer_R = {k: v["R_mOhm"] for k, v in bd.items() if k not in ("rail_traces", "rail_vias")}
    planes_mOhm = sum(layer_R.values())
    vias_mOhm = bd["rail_vias"]["mOhm"]
    traces_mOhm = bd["rail_traces"]["mOhm"]
    total_mOhm = planes_mOhm + vias_mOhm + traces_mOhm

    traces = ex["rail_traces"]
    nowidth = [t for t in traces if not t[2]]
    lens_all = [trace_length_um(t, ex["rail_nodes"]) for t in traces]
    lens_all = [x for x in lens_all if x is not None]
    lens_nowidth = [trace_length_um(t, ex["rail_nodes"]) for t in nowidth]
    lens_nowidth = [x for x in lens_nowidth if x is not None]

    port_layer_dist = {}
    for nid in ex["port_pos_nodes"]:
        n = ex["rail_nodes"].get(nid)
        lay = n[2] if n is not None else "?"
        port_layer_dist[lay] = port_layer_dist.get(lay, 0) + 1

    return dict(
        port=port,
        R=dict(planes_mOhm=planes_mOhm, vias_mOhm=vias_mOhm, traces_mOhm=traces_mOhm,
                total_mOhm=total_mOhm, per_layer_mOhm=layer_R),
        extract=dict(
            n_traces=len(traces), n_traces_nowidth=len(nowidth),
            len_nowidth_total_um=sum(lens_nowidth), len_all_total_um=sum(lens_all),
            n_vias=len(ex["rail_vias"]), n_port_pos_nodes=len(ex["port_pos_nodes"]),
            n_rail_nodes=len(ex["rail_nodes"]), port_layer_dist=port_layer_dist,
            two_sided_layers=two_sided_layers,
        ),
        # kept only for the H_geo computation below, not serialized as-is
        _port_pos_xy={nid: ex["rail_nodes"][nid][:2] for nid in ex["port_pos_nodes"] if nid in ex["rail_nodes"]},
        _rail_nodes_xy=[(v[0], v[1]) for v in ex["rail_nodes"].values()],
    )


def median_nn_distance(src_xy, dst_xy):
    """Median nearest-neighbor distance from each point in src_xy to the point set dst_xy."""
    tree = cKDTree(dst_xy)
    dist, _ = tree.query(src_xy, k=1)
    return float(np.median(dist))


def compute_geo(s0, s1):
    """Section 4 (H_geo): fold SITE0 port-pos nodes through the package centre, compare to SITE1."""
    s0_xy = np.array(list(s0["_port_pos_xy"].values()), dtype=float)
    s1_xy = np.array(list(s1["_port_pos_xy"].values()), dtype=float)
    all_xy = np.array(s0["_rail_nodes_xy"] + s1["_rail_nodes_xy"], dtype=float)
    cx = (all_xy[:, 0].min() + all_xy[:, 0].max()) / 2.0
    cy = (all_xy[:, 1].min() + all_xy[:, 1].max()) / 2.0

    reflections = {
        "x": np.stack([2 * cx - s0_xy[:, 0], s0_xy[:, 1]], axis=1),
        "y": np.stack([s0_xy[:, 0], 2 * cy - s0_xy[:, 1]], axis=1),
        "xy": np.stack([2 * cx - s0_xy[:, 0], 2 * cy - s0_xy[:, 1]], axis=1),
    }
    med_by_axis = {ax: median_nn_distance(xy, s1_xy) for ax, xy in reflections.items()}
    best_axis = min(med_by_axis, key=med_by_axis.get)
    best_dist = med_by_axis[best_axis]

    # median NN spacing among S0 points themselves (self-distance, k=2 to skip the zero self-match)
    tree0 = cKDTree(s0_xy)
    d0, _ = tree0.query(s0_xy, k=2)
    s0_spacing = float(np.median(d0[:, 1]))

    return dict(centre_xy=[cx, cy], median_dist_by_axis=med_by_axis, best_axis=best_axis,
                best_median_dist_um=best_dist, s0_median_spacing_um=s0_spacing,
                symmetric=bool(best_dist < s0_spacing))


def compute_delta(s0, s1):
    """Section 3: ΔR per element, contribution shares, dominant element."""
    els = ("planes", "vias", "traces")
    dR = {el: s1["R"][f"{el}_mOhm"] - s0["R"][f"{el}_mOhm"] for el in els}
    total = sum(dR.values())
    contrib = {el: (dR[el] / total if total != 0 else float("nan")) for el in els}
    dominant = max(els, key=lambda el: abs(dR[el]))
    return dict(dR_mOhm=dR, dR_abs_mOhm={el: abs(v) for el, v in dR.items()},
                sum_dR_mOhm=total, contribution=contrib, dominant=dominant)


def build_pair_record(k_pair, group):
    metric, p = k_pair
    s0 = port_stats(p["port_S0"])
    s1 = port_stats(p["port_S1"])
    delta = compute_delta(s0, s1)
    geo = compute_geo(s0, s1)

    nowidth_s0 = s0["extract"]["len_nowidth_total_um"]
    nowidth_s1 = s1["extract"]["len_nowidth_total_um"]
    if nowidth_s0 == 0:
        nowidth_ratio = float("inf") if nowidth_s1 > 0 else float("nan")
    else:
        nowidth_ratio = nowidth_s1 / nowidth_s0

    for s in (s0, s1):
        del s["_port_pos_xy"], s["_rail_nodes_xy"]

    return dict(k=p["k"], group=group, port_S0=p["port_S0"], port_S1=p["port_S1"],
                decaps_S0=p["decaps_S0"], s_mod=p["s_mod"], s_ref=p["s_ref"],
                abs_ln_ratio=metric, S0=s0, S1=s1, delta=delta, geo=geo,
                nowidth_len_ratio_S1_over_S0=nowidth_ratio)


def run_verdicts(records):
    discordant = [r for r in records if r["group"] == "discordant"]
    n = len(discordant)

    dom_counts = {"planes": 0, "vias": 0, "traces": 0}
    traces_strong = 0
    for r in discordant:
        dom_counts[r["delta"]["dominant"]] += 1
        if r["delta"]["dominant"] == "traces" and abs(r["delta"]["contribution"]["traces"]) >= H_TR_CONTRIB_MIN:
            traces_strong += 1
    if traces_strong >= H_TR_MAJORITY:
        h_tr_verdict = "traces"
    elif dom_counts["vias"] >= H_TR_MAJORITY:
        h_tr_verdict = "vias"
    elif dom_counts["planes"] >= H_TR_MAJORITY:
        h_tr_verdict = "planes"
    else:
        h_tr_verdict = "mixed"
    h_tr = dict(n=n, dominant_counts=dom_counts, n_traces_strong=traces_strong, verdict=h_tr_verdict)

    n_w = 0
    for r in discordant:
        ratio = r["nowidth_len_ratio_S1_over_S0"]
        # nan (both-zero) comparisons are False in Python, so those are correctly skipped;
        # +inf (S0 zero, S1 nonzero) correctly satisfies ratio >= H_W_RATIO_HI.
        if ratio >= H_W_RATIO_HI or ratio <= H_W_RATIO_LO:
            n_w += 1
    h_w = dict(n=n, n_ratio_ge2_or_le0p5=n_w, verdict="adopt" if n_w >= H_W_MAJORITY else "reject")

    n_sym = sum(1 for r in discordant if r["geo"]["symmetric"])
    h_geo = dict(n=n, n_symmetric=n_sym, n_asymmetric=n - n_sym,
                 verdict="symmetric" if n_sym >= H_W_MAJORITY else "asymmetric_or_mixed")

    return dict(H_tr=h_tr, H_w=h_w, H_geo=h_geo)


def make_plot(records, out_png):
    els = ["planes", "vias", "traces"]
    colors = {"planes": "steelblue", "vias": "darkorange", "traces": "seagreen"}
    disc = [r for r in records if r["group"] == "discordant"]
    ctrl = [r for r in records if r["group"] == "control"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), sharey=False,
                              gridspec_kw=dict(width_ratios=[max(len(disc), 1), max(len(ctrl), 1)]))
    for ax, group_records, title in ((axes[0], disc, "discordant (top 8)"), (axes[1], ctrl, "control")):
        x = np.arange(len(group_records))
        w = 0.25
        for i, el in enumerate(els):
            vals = [r["delta"]["dR_mOhm"][el] for r in group_records]
            ax.bar(x + (i - 1) * w, vals, width=w, color=colors[el], label=el)
        ax.axhline(0, color="black", lw=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels([f"k{r['k']}" for r in group_records], rotation=45, ha="right")
        ax.set_ylabel("ΔR = R(S1) - R(S0)  [mOhm]")
        ax.set_title(title)
    axes[0].legend()
    fig.suptitle("EXP-25: element-wise ΔR breakdown, SITE1 - SITE0 @100kHz")
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def fmt(x, nd=4):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def main():
    pairs = load_exp24_pairs()
    discordant, control, control_note = select_pairs(pairs)

    records = [build_pair_record(t, "discordant") for t in discordant]
    records += [build_pair_record(t, "control") for t in control]

    verdicts = run_verdicts(records)

    out = dict(
        params=dict(N_DISCORDANT=N_DISCORDANT, CONTROL_TOL=CONTROL_TOL,
                    CONTROL_DECAP_MAX=CONTROL_DECAP_MAX, N_CONTROL=N_CONTROL,
                    H_TR_CONTRIB_MIN=H_TR_CONTRIB_MIN, H_TR_MAJORITY=H_TR_MAJORITY,
                    H_W_RATIO_HI=H_W_RATIO_HI, H_W_RATIO_LO=H_W_RATIO_LO, H_W_MAJORITY=H_W_MAJORITY),
        n_pairs_total=len(pairs), n_discordant=len(discordant), n_control=len(control),
        control_note=control_note,
        pairs=records, verdicts=verdicts,
    )

    json_fn = work_file("exp25", "exp25.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp25", "exp25_breakdown.png")
    make_plot(records, png_fn)

    print(f"\n### EXP-25 element-wise R breakdown (n_discordant={len(discordant)}, n_control={len(control)})\n")
    if control_note:
        print(f"note: {control_note}\n")

    print("| pair k | ports | decaps | R_tot S0 | R_tot S1 | dR planes | dR vias | dR traces | "
          "dominant | nowidth len S0 | nowidth len S1 | geo dist | geo spacing |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in records:
        d = r["delta"]
        print(f"| {r['k']} ({r['group']}) | {r['port_S0']} / {r['port_S1']} | {r['decaps_S0']} | "
              f"{fmt(r['S0']['R']['total_mOhm'])} | {fmt(r['S1']['R']['total_mOhm'])} | "
              f"{fmt(d['dR_mOhm']['planes'])} | {fmt(d['dR_mOhm']['vias'])} | {fmt(d['dR_mOhm']['traces'])} | "
              f"{d['dominant']} | {fmt(r['S0']['extract']['len_nowidth_total_um'], 1)} | "
              f"{fmt(r['S1']['extract']['len_nowidth_total_um'], 1)} | "
              f"{fmt(r['geo']['best_median_dist_um'], 1)} | {fmt(r['geo']['s0_median_spacing_um'], 1)} |")

    print("\n#### verdicts\n")
    print("| hypothesis | stat | verdict |")
    print("|---|---|---|")
    ht, hw, hg = verdicts["H_tr"], verdicts["H_w"], verdicts["H_geo"]
    print(f"| H_tr | dominant counts={ht['dominant_counts']}, traces-strong={ht['n_traces_strong']}/{ht['n']} | {ht['verdict']} |")
    print(f"| H_w | nowidth-ratio>=2-or<=0.5 count={hw['n_ratio_ge2_or_le0p5']}/{hw['n']} | {hw['verdict']} |")
    print(f"| H_geo | symmetric={hg['n_symmetric']}/{hg['n']} | {hg['verdict']} |")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
