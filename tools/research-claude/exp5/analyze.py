"""EXP-5 analysis: Part A (260804 held-out + delta tracking) and Part B (multi-rail table)."""
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exp1"))
from run_exp1 import plot  # noqa: E402

OUT = "/home/claude/work/exp5"


def load(fn):
    r = json.load(open(fn))
    f = np.array(r["freq"]); Z = np.array(r["Z_re"]) + 1j * np.array(r["Z_im"]); Zr = np.array(r["Zref_re"]) + 1j * np.array(r["Zref_im"])
    return r, f, Z, Zr


def part_a():
    fa = os.path.join(OUT, "result_260729_Port18_SITE0_sweep.json"); fb = os.path.join(OUT, "result_260804_Port18_SITE0_sweep.json")
    if not (os.path.exists(fa) and os.path.exists(fb)):
        return None
    ra, f1, Z1, R1 = load(fa); rb, f2, Z2, R2 = load(fb)
    common = sorted(set(f1) & set(f2)); i1 = [list(f1).index(x) for x in common]; i2 = [list(f2).index(x) for x in common]
    f = np.array(common); Z1, R1, Z2, R2 = Z1[i1], R1[i1], Z2[i2], R2[i2]
    dm = np.abs(Z2 - Z1) / np.abs(Z1); dr = np.abs(R2 - R1) / np.abs(R1)
    rows = []
    for x in (1e5, 1e6, 2.51e6, 1e7, 1e8):
        k = int(np.argmin(abs(f - x)))
        rows.append(dict(f=float(f[k]), model_delta=float(dm[k]), ref_delta=float(dr[k]),
                         delta_vec_err=float(abs((Z2[k] - Z1[k]) - (R2[k] - R1[k])) / abs(R2[k] - R1[k]))))
    res = dict(f_res_model_729=ra["f_res_model"], f_res_model_804=rb["f_res_model"], f_res_ref_729=ra["f_res_ref"], f_res_ref_804=rb["f_res_ref"],
               ratio_model=rb["f_res_model"] / ra["f_res_model"], ratio_ref=rb["f_res_ref"] / ra["f_res_ref"],
               gates_729=ra["gates"], gates_804=rb["gates"], delta_rows=rows,
               grid_points_ref_min=dict(r729=float(f1[int(np.argmin(np.abs(R1)))]) if False else None))
    res["ratio_rel_err"] = res["ratio_model"] / res["ratio_ref"] - 1
    json.dump(res, open(os.path.join(OUT, "partA_summary.json"), "w"), indent=1, default=str)
    # delta overlay plot
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    ax[0].semilogx(f, 100 * dr, "k-", lw=2, label="PowerSI |Z804-Z729|/|Z729|"); ax[0].semilogx(f, 100 * dm, "o-", label="model")
    ax[0].set_ylabel("%"); ax[0].legend(); ax[0].grid(alpha=.3)
    ax[1].loglog(f, np.abs(R1), "k-", label="ref 260729"); ax[1].loglog(f, np.abs(R2), "k--", label="ref 260804")
    ax[1].loglog(f, np.abs(Z1), "C0o-", ms=3, label="model 260729"); ax[1].loglog(f, np.abs(Z2), "C1o-", ms=3, label="model 260804")
    ax[1].legend(); ax[1].grid(alpha=.3); ax[1].set_ylabel("|Z| Ohm")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "partA_delta.png"), dpi=110)
    return res


def part_b():
    rows = []
    for fn in sorted(glob.glob(os.path.join(OUT, "result_260729_Port*_ladder.json")) + [os.path.join(OUT, "result_260729_Port18_SITE0_sweep.json"), os.path.join(OUT, "result_260804_Port18_SITE0_sweep.json")]):
        if not os.path.exists(fn):
            continue
        r, f, Z, Zr = load(fn)
        w = 2 * np.pi * f
        lo = (f >= 2.9e4) & (f <= 3.1e5)
        k1 = int(np.argmin(abs(f - 1e6)))
        dl = (Z.imag - Zr.imag) / w * 1e12
        layers = {}
        for L, blocks in r["reference_search"].items():
            cb = [b for b in blocks if b["cells"]]
            if cb:
                layers[L.replace("Signal$", "")] = dict(d_eff=round(float(np.average([b["d_eff_um_median"] for b in cb], weights=[b["cells"] for b in cb])), 1),
                                                        cells=int(sum(b["cells"] for b in cb)))
        ext = {}
        for fb in ("1e+05",):
            for L, v in r["breakdown"].get(fb, {}).items():
                if isinstance(v, dict) and "L_ext_pH" in v:
                    ext[L.replace("Signal$", "")] = round(v["L_ext_pH"] + v["L_int_pH"], 1)
        # 2-parameter fit dIm = w*dL - dS/w over 30 kHz - 1 MHz (separates a C/1-over-wC mismatch from L)
        fm = (f >= 2.9e4) & (f <= 1.001e6)
        A = np.column_stack([w[fm], -1.0 / w[fm]])
        coef, *_ = np.linalg.lstsq(A, (Z.imag - Zr.imag)[fm], rcond=None)
        resid = (Z.imag - Zr.imag)[fm] - A @ coef
        plane_tot_1M = sum(v["L_ext_pH"] + v["L_int_pH"] for v in r["breakdown"].get("1e+06", {}).values() if isinstance(v, dict) and "L_ext_pH" in v)
        plane_tot_100k = sum(v["L_ext_pH"] + v["L_int_pH"] for v in r["breakdown"].get("1e+05", {}).values() if isinstance(v, dict) and "L_ext_pH" in v)
        vias = r["breakdown"].get("1e+05", {}).get("rail_vias", {}).get("pH")
        Cref = float(-1.0 / (w[0] * Zr.imag[0]))
        rows.append(dict(tag=r["tag"], port=r["port"], rail=r["rail"], decaps=r["decaps"], layers=layers, plane_L_100k=ext,
                         fit_dL_pH=float(coef[0] * 1e12), fit_dInvC_perF=float(coef[1]), fit_rel_dC=float(coef[1] * Cref), fit_resid_max_uOhm=float(np.max(np.abs(resid)) * 1e6),
                         plane_L_total_100k_pH=plane_tot_100k, plane_L_total_1M_pH=plane_tot_1M, via_L_100k_pH=vias, C_ref_lowest_f_F=Cref,
                         dRe_100k_x_decaps_mOhm=float((Z.real - Zr.real)[int(np.argmin(abs(f - 1e5)))] * 1e3 * r["decaps"]),
                         two_sided=r["info"].get("two_sided_layers"),
                         G1_max_abs_dRe_mOhm=float(np.max(np.abs(Z.real - Zr.real)[(f >= 1e3) & (f <= 1.001e5)]) * 1e3),
                         dRe_30k_300k_mOhm=[float(x) for x in ((Z.real - Zr.real)[lo] * 1e3)],
                         dL_30k_300k_pH=[float(x) for x in dl[lo]], dL_1MHz_pH=float(dl[k1]), dRe_1MHz_mOhm=float((Z.real - Zr.real)[k1] * 1e3),
                         err_1MHz=float(abs(Z[k1] - Zr[k1]) / abs(Zr[k1])), f_res_model=r["f_res_model"], f_res_ref=r["f_res_ref"],
                         zmin_model=r["zmin_model"], zmin_ref=r["zmin_ref"], Zref_1MHz=[float(Zr[k1].real), float(Zr[k1].imag)],
                         unknowns=r["info"]["unknowns"]))
    json.dump(rows, open(os.path.join(OUT, "partB_table.json"), "w"), indent=1)
    return rows


if __name__ == "__main__":
    a = part_a()
    if a:
        print(json.dumps({k: v for k, v in a.items() if k not in ("gates_729", "gates_804")}, indent=1, default=str))
    for row in part_b():
        print(json.dumps(row))
