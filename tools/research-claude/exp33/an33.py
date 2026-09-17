#!/usr/bin/env python
"""EXP-33: decompose the f_res bias into a low-frequency C part and a high-frequency L part,
package (260729/p, 92 ports) vs PCB (s5m6585/p, 160 ports). See WORK_DIR/exp33/EXP33_PLAN.md
for the frozen definitions (Section 1) and thresholds (Section 2) -- do not edit that file,
do not change the numbers here. No model is run; this only re-reads existing receipts.

    python an33.py

Reads WORK_DIR/exp28/result_260729_*_any_p.json (92, package) and
WORK_DIR/exp30/result_s5m6585_*_any_p.json (160, PCB), same glob + timestamp-skip pattern as
exp29/an29.py. PCB decap counts (N_d) come from WORK_DIR/exp30/extract_stats.json
(n_decaps per port); package N_d is not loaded (the plan only needs N_d for the PCB, and
exp5/extract_260729_{port}.pkl decap-list loads for 92 ports are slow -- see exp16/corr16.py's
decap_count() for the same pattern if that is ever wanted).

Writes WORK_DIR/exp33/exp33.json and exp33_rL_rC.png; prints a markdown summary.
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
from paths import WORK_DIR, work_file  # noqa: E402

# frozen thresholds / definitions, EXP33_PLAN.md Section 1-2
F_C_HZ = 1e5
F_L_HZ = 1e7
ELIG_FRES_REF_MAX = 5e6
CONSISTENCY_TOL = 0.2
H_C_LO, H_C_HI = 0.95, 1.05
H_LPCB_RL_MAX = 0.85
H_LPCB_DL_LO_H, H_LPCB_DL_HI_H = -1.5e-9, -0.3e-9  # N_d*ΔL_abs, henries
H_LPKG_RL_MIN = 1.1
H_LPKG_REJECT_MAX = 1.0

PCB_RAILS = ["ADC_AVDD08_LO", "ADC_DVDD08_CORE", "ADC_DVDDQ12", "ADC_DVDDQ18", "ADC_IO_BUCK13"]

# the 7-case regression set (EXP-13 PLAN/REPORT): 6 x 260729 SITE0 ports + 804-P18
KNOWN_7 = [("260729", "Port1_SITE0", "P1"), ("260729", "Port7_SITE0", "P7"),
           ("260729", "Port14_SITE0", "P14"), ("260729", "Port16_SITE0", "P16"),
           ("260729", "Port18_SITE0", "P18"), ("260729", "Port19_SITE0", "P19"),
           ("260804", "Port18_SITE0", "804-P18")]


def _load_glob(dir_, tag, n_expect):
    """result_{tag}_*_any_p.json under WORK_DIR/dir_, same regex-after-glob timestamp-skip as
    exp29/an29.py: the receipt_re anchors on "..._any_p.json$", which a timestamp suffix
    ("..._any_p_HHMMSS.json") never matches, so resume duplicates are excluded for free."""
    receipt_re = re.compile(rf"^result_{re.escape(tag)}_(.+)_any_p\.json$")
    d = WORK_DIR / dir_
    rows = []
    for fn in sorted(glob.glob(os.path.join(d, f"result_{tag}_*_any_p.json"))):
        base = os.path.basename(fn)
        if not receipt_re.match(base):
            continue
        rows.append(json.loads(open(fn, encoding="utf-8").read()))
    if n_expect is not None and len(rows) != n_expect:
        print(f"WARNING: {dir_}/{tag} expected {n_expect} receipts, found {len(rows)}", file=sys.stderr)
    return rows


def load_package():
    return _load_glob("exp28", "260729", 92)


def load_pcb():
    return _load_glob("exp30", "s5m6585", 160)


def load_pcb_ndecaps():
    with open(WORK_DIR / "exp30" / "extract_stats.json", encoding="utf-8") as fh:
        stats = json.load(fh)
    return {port: v["n_decaps"] for port, v in stats.items()}


def nearest_idx(freq, target):
    return int(np.argmin(np.abs(freq - target)))


def per_port_row(r, n_decaps=None):
    f = np.asarray(r["freq"], dtype=float)
    ic = nearest_idx(f, F_C_HZ)
    il = nearest_idx(f, F_L_HZ)
    fc, fl = float(f[ic]), float(f[il])

    im_mod_c = float(r["Z_im"][ic])
    im_ref_c = float(r["Zref_im"][ic])
    im_mod_l = float(r["Z_im"][il])
    im_ref_l = float(r["Zref_im"][il])

    c_mod_ok = im_mod_c < 0
    c_ref_ok = im_ref_c < 0
    C_mod = -1.0 / (2 * math.pi * fc * im_mod_c) if c_mod_ok else None
    C_ref = -1.0 / (2 * math.pi * fc * im_ref_c) if c_ref_ok else None

    L_mod = im_mod_l / (2 * math.pi * fl)
    L_ref = im_ref_l / (2 * math.pi * fl)

    f_res_model = float(r["f_res_model"]) if r.get("f_res_model") is not None else None
    f_res_ref = float(r["f_res_ref"]) if r.get("f_res_ref") is not None else None

    cond_fres = f_res_ref is not None and f_res_ref < ELIG_FRES_REF_MAX
    cond_zref = im_ref_l > 0
    cond_zmod = im_mod_l > 0
    eligible = cond_fres and cond_zref and cond_zmod

    r_C = (C_mod / C_ref) if (C_mod is not None and C_ref is not None and C_ref != 0) else None
    r_L = (L_mod / L_ref) if (eligible and L_ref != 0) else None
    r_f = (f_res_model / f_res_ref) if (f_res_model is not None and f_res_ref) else None

    consistent = None
    if r_C is not None and r_L is not None and r_f is not None and r_L * r_C >= 0:
        pred = r_f * math.sqrt(r_L * r_C)
        consistent = abs(pred - 1.0) <= CONSISTENCY_TOL

    dL_abs = (L_mod - L_ref) if (eligible) else None  # H, PCB only uses this
    nd = n_decaps
    nd_dL_abs_H = (nd * dL_abs) if (dL_abs is not None and nd is not None) else None

    return dict(
        port=r["port"], tag=r["tag"], rail=r.get("rail"),
        f_C_Hz=fc, f_L_Hz=fl,
        Z_im_model_C=im_mod_c, Z_im_ref_C=im_ref_c,
        Z_im_model_L=im_mod_l, Z_im_ref_L=im_ref_l,
        C_mod_F=C_mod, C_ref_F=C_ref, c_mod_ok=c_mod_ok, c_ref_ok=c_ref_ok,
        L_mod_H=L_mod, L_ref_H=L_ref,
        f_res_model=f_res_model, f_res_ref=f_res_ref,
        cond_fres_ref_lt_5M=cond_fres, cond_Zref_im_L_pos=cond_zref, cond_Zmod_im_L_pos=cond_zmod,
        eligible=eligible,
        r_C=r_C, r_L=r_L, r_f=r_f, consistent=consistent,
        N_d=nd, dL_abs_H=dL_abs, Nd_dL_abs_H=nd_dL_abs_H,
        Nd_dL_abs_nH=(nd_dL_abs_H * 1e9 if nd_dL_abs_H is not None else None),
    )


def quartiles(vals):
    vals = [v for v in vals if v is not None and np.isfinite(v)]
    if not vals:
        return dict(n=0, median=None, q1=None, q3=None)
    q1, med, q3 = np.percentile(vals, [25, 50, 75])
    return dict(n=len(vals), median=float(med), q1=float(q1), q3=float(q3))


def consistency_stats(rows):
    checked = [p["consistent"] for p in rows if p["consistent"] is not None]
    n_ok = sum(1 for c in checked if c)
    return dict(n_checked=len(checked), n_within_tol=n_ok,
                frac_within_tol=(n_ok / len(checked) if checked else None))


def eligibility_stats(rows):
    n = len(rows)
    return dict(
        n_ports=n,
        fail_fres_ref=sum(1 for p in rows if not p["cond_fres_ref_lt_5M"]),
        fail_Zref_im_L=sum(1 for p in rows if not p["cond_Zref_im_L_pos"]),
        fail_Zmod_im_L=sum(1 for p in rows if not p["cond_Zmod_im_L_pos"]),
        n_eligible=sum(1 for p in rows if p["eligible"]),
    )


def set_summary(rows):
    return dict(
        n_ports=len(rows),
        eligibility=eligibility_stats(rows),
        r_C=quartiles([p["r_C"] for p in rows]),
        r_L=quartiles([p["r_L"] for p in rows]),
        r_f=quartiles([p["r_f"] for p in rows]),
        consistency=consistency_stats(rows),
        n_c_flagged=sum(1 for p in rows if not p["c_mod_ok"] or not p["c_ref_ok"]),
    )


def rail_of(rail_field):
    return (rail_field or "").split("/")[0]


def pcb_rail_summary(rows):
    out = {}
    for rail in PCB_RAILS:
        rr = [p for p in rows if rail_of(p["rail"]) == rail]
        out[rail] = dict(
            n_ports=len(rr),
            r_C=quartiles([p["r_C"] for p in rr]),
            r_L=quartiles([p["r_L"] for p in rr]),
            r_f=quartiles([p["r_f"] for p in rr]),
            Nd_dL_abs_nH=quartiles([p["Nd_dL_abs_nH"] for p in rr]),
        )
    return out


def h_c_verdict(pkg_summary, pcb_summary):
    pkg_med, pcb_med = pkg_summary["r_C"]["median"], pcb_summary["r_C"]["median"]
    pkg_ok = pkg_med is not None and H_C_LO <= pkg_med <= H_C_HI
    pcb_ok = pcb_med is not None and H_C_LO <= pcb_med <= H_C_HI
    if pkg_ok and pcb_ok:
        return "adopt", []
    failing = [name for name, ok in (("package", pkg_ok), ("pcb", pcb_ok)) if not ok]
    return "reject", failing


def h_lpcb_verdict(pcb_summary, nd_dl_med_H):
    rl_med = pcb_summary["r_L"]["median"]
    rl_cond = rl_med is not None and rl_med < H_LPCB_RL_MAX
    dl_cond = nd_dl_med_H is not None and H_LPCB_DL_LO_H <= nd_dl_med_H <= H_LPCB_DL_HI_H
    if rl_cond and dl_cond:
        return "adopt"
    if rl_med is not None and rl_med >= 1.0:
        return "reject"
    return "indeterminate"


def h_lpkg_verdict(pkg_summary):
    rl_med = pkg_summary["r_L"]["median"]
    if rl_med is None:
        return "indeterminate"
    if rl_med > H_LPKG_RL_MIN:
        return "adopt"
    if rl_med <= H_LPKG_REJECT_MAX:
        return "reject"
    return "indeterminate"


def plot_rL_rC(pkg_rows, pcb_rows, out_png):
    fig, ax = plt.subplots(figsize=(7, 6))
    for rows, label, color in ((pkg_rows, "package (260729)", "steelblue"),
                                (pcb_rows, "PCB (s5m6585)", "darkorange")):
        xs = [p["r_C"] for p in rows if p["r_C"] is not None and p["r_L"] is not None]
        ys = [p["r_L"] for p in rows if p["r_C"] is not None and p["r_L"] is not None]
        ax.scatter(xs, ys, s=24, alpha=0.7, edgecolor="black", linewidth=0.3, color=color, label=label)
    ax.axhline(1.0, color="gray", lw=1, ls="--")
    ax.axvline(1.0, color="gray", lw=1, ls="--")
    ax.set_xlabel("r_C = C_mod/C_ref @100kHz")
    ax.set_ylabel("r_L = L_mod/L_ref @10MHz (eligible ports)")
    ax.set_title("EXP-33: r_L vs r_C, package vs PCB")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def known7_rows(pkg_rows_by_tag_port):
    out = []
    for tag, port, label in KNOWN_7:
        row = pkg_rows_by_tag_port.get((tag, port))
        if row is None:
            fn = WORK_DIR / "exp28" / f"result_{tag}_{port}_any_p.json"
            if fn.is_file():
                row = per_port_row(json.loads(open(fn, encoding="utf-8").read()))
            else:
                out.append(dict(label=label, tag=tag, port=port, missing=True))
                continue
        row = dict(row)
        row["label"] = label
        out.append(row)
    return out


def main():
    pkg_receipts = load_package()
    pcb_receipts = load_pcb()
    nd_map = load_pcb_ndecaps()

    pkg_rows = [per_port_row(r) for r in pkg_receipts]
    pcb_rows = [per_port_row(r, n_decaps=nd_map.get(r["port"])) for r in pcb_receipts]

    n_nd_missing = sum(1 for r in pcb_receipts if nd_map.get(r["port"]) is None)

    pkg_summary = set_summary(pkg_rows)
    pcb_summary = set_summary(pcb_rows)
    pcb_rails = pcb_rail_summary(pcb_rows)

    nd_dl_all_H = [p["Nd_dL_abs_H"] for p in pcb_rows if p["Nd_dL_abs_H"] is not None]
    nd_dl_q_H = quartiles(nd_dl_all_H)
    nd_dl_q_nH = quartiles([p["Nd_dL_abs_nH"] for p in pcb_rows if p["Nd_dL_abs_nH"] is not None])
    nd_dl_med_H = nd_dl_q_H["median"]

    v_hc, hc_failing = h_c_verdict(pkg_summary, pcb_summary)
    v_hlpcb = h_lpcb_verdict(pcb_summary, nd_dl_med_H)
    v_hlpkg = h_lpkg_verdict(pkg_summary)

    pkg_rows_by_tag_port = {(p["tag"], p["port"]): p for p in pkg_rows}
    known7 = known7_rows(pkg_rows_by_tag_port)

    out = dict(
        definitions=dict(f_C_Hz=F_C_HZ, f_L_Hz=F_L_HZ, elig_fres_ref_max_Hz=ELIG_FRES_REF_MAX,
                          consistency_tol=CONSISTENCY_TOL),
        package=dict(summary=pkg_summary, ports=pkg_rows),
        pcb=dict(summary=pcb_summary, rails=pcb_rails, Nd_dL_abs_H=nd_dl_q_H, Nd_dL_abs_nH=nd_dl_q_nH,
                 n_ports_missing_Nd=n_nd_missing, ports=pcb_rows),
        known7_cases=known7,
        verdicts=dict(
            H_C=dict(verdict=v_hc, failing_sets=hc_failing,
                     package_median_rC=pkg_summary["r_C"]["median"], pcb_median_rC=pcb_summary["r_C"]["median"]),
            H_Lpcb=dict(verdict=v_hlpcb, pcb_median_rL=pcb_summary["r_L"]["median"],
                        pcb_median_Nd_dL_abs_H=nd_dl_med_H, pcb_median_Nd_dL_abs_nH=nd_dl_q_nH["median"]),
            H_Lpkg=dict(verdict=v_hlpkg, package_median_rL=pkg_summary["r_L"]["median"]),
        ),
    )

    json_fn = work_file("exp33", "exp33.json")
    with open(json_fn, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    png_fn = work_file("exp33", "exp33_rL_rC.png")
    plot_rL_rC(pkg_rows, pcb_rows, png_fn)

    # ---- printed markdown summary ----
    print(f"\n### EXP-33 f_res bias decomposition: package (260729/p, n={len(pkg_rows)}) "
          f"vs PCB (s5m6585/p, n={len(pcb_rows)})\n")

    print("#### eligibility (L_eff @10MHz: f_res_ref<5MHz, Im Zref(1e7)>0, Im Zmod(1e7)>0)\n")
    print("| set | n | fail f_res_ref<5M | fail Im Zref>0 | fail Im Zmod>0 | eligible |")
    print("|---|---|---|---|---|---|")
    for name, s in (("package", pkg_summary), ("pcb", pcb_summary)):
        e = s["eligibility"]
        print(f"| {name} | {e['n_ports']} | {e['fail_fres_ref']} | {e['fail_Zref_im_L']} | "
              f"{e['fail_Zmod_im_L']} | {e['n_eligible']} |")

    print("\n#### r_C, r_L, r_f medians (IQR) per set\n")
    print("| set | r_C median [Q1,Q3] (n) | r_L median [Q1,Q3] (n) | r_f median [Q1,Q3] (n) | "
          "consistency (|r_f*sqrt(r_L*r_C)-1|<=0.2) |")
    print("|---|---|---|---|---|")
    for name, s in (("package", pkg_summary), ("pcb", pcb_summary)):
        rc, rl, rf, c = s["r_C"], s["r_L"], s["r_f"], s["consistency"]
        print(f"| {name} | {rc['median']:.4f} [{rc['q1']:.4f},{rc['q3']:.4f}] (n={rc['n']}) | "
              f"{rl['median']:.4f} [{rl['q1']:.4f},{rl['q3']:.4f}] (n={rl['n']}) | "
              f"{rf['median']:.4f} [{rf['q1']:.4f},{rf['q3']:.4f}] (n={rf['n']}) | "
              f"{c['n_within_tol']}/{c['n_checked']} |")

    print(f"\n{pkg_summary['n_c_flagged']} package / {pcb_summary['n_c_flagged']} pcb port(s) flagged: "
          f"Im Z >= 0 at the 100kHz point (C_eff not meaningful there).")

    print(f"\n#### PCB N_d*ΔL_abs (ΔL_abs = L_mod-L_ref, eligible ports, n_decaps missing for "
          f"{n_nd_missing} port(s))\n")
    print(f"median {nd_dl_q_nH['median']} nH, Q1 {nd_dl_q_nH['q1']} nH, Q3 {nd_dl_q_nH['q3']} nH "
          f"(n={nd_dl_q_nH['n']})")

    print("\n#### PCB per-rail medians (5 rails)\n")
    print("| rail | n | r_C median | r_L median | r_f median | N_d*ΔL_abs median (nH) |")
    print("|---|---|---|---|---|---|")
    for rail in PCB_RAILS:
        rs = pcb_rails[rail]
        print(f"| {rail} | {rs['n_ports']} | {rs['r_C']['median']} | {rs['r_L']['median']} | "
              f"{rs['r_f']['median']} | {rs['Nd_dL_abs_nH']['median']} |")

    print("\n#### 7 known package cases (EXP-13 regression set)\n")
    print("| case | tag | port | r_C | r_L | r_f | eligible |")
    print("|---|---|---|---|---|---|---|")
    for row in known7:
        if row.get("missing"):
            print(f"| {row['label']} | {row['tag']} | {row['port']} | (receipt missing) | | | |")
            continue
        print(f"| {row['label']} | {row['tag']} | {row['port']} | {row['r_C']} | {row['r_L']} | "
              f"{row['r_f']} | {row['eligible']} |")

    print(f"\n**H_C verdict: {v_hc}** (package median r_C={pkg_summary['r_C']['median']}, "
          f"pcb median r_C={pcb_summary['r_C']['median']}, band [{H_C_LO},{H_C_HI}]"
          + (f"; failing: {hc_failing}" if hc_failing else "") + ")")
    print(f"**H_Lpcb verdict: {v_hlpcb}** (pcb median r_L={pcb_summary['r_L']['median']} vs "
          f"<{H_LPCB_RL_MAX}, median N_d*ΔL_abs={nd_dl_med_H} H vs "
          f"[{H_LPCB_DL_LO_H},{H_LPCB_DL_HI_H}] H)")
    print(f"**H_Lpkg verdict: {v_hlpkg}** (package median r_L={pkg_summary['r_L']['median']} vs "
          f">{H_LPKG_RL_MIN} adopt / <={H_LPKG_REJECT_MAX} reject)")

    print(f"\nwrote {json_fn}")
    print(f"wrote {png_fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
