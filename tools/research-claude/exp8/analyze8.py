"""EXP-8 side-by-side table (GND-only EXP-5 vs cavity-wall ANY) and overlay plots."""
import json, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from run8 import CASES, exp5_file, fit_dl
OUT = "/home/claude/work/exp8"


def metrics(fn):
    r = json.load(open(fn))
    f = np.array(r["freq"]); Z = np.array(r["Z_re"]) + 1j * np.array(r["Z_im"]); Zr = np.array(r["Zref_re"]) + 1j * np.array(r["Zref_im"])
    k1 = int(np.argmin(abs(f - 1e6))); k100 = int(np.argmin(abs(f - 1e8)))
    lo = (f >= 1e3) & (f <= 1.001e5)
    return dict(G1=float(np.max(np.abs(Z.real - Zr.real)[lo]) * 1e3), fit_dL=fit_dl(f, Z.imag - Zr.imag), err1M=float(abs(Z[k1] - Zr[k1]) / abs(Zr[k1])),
                dL1M=float((Z.imag - Zr.imag)[k1] / (2 * np.pi * 1e6) * 1e12), dRe1M=float((Z.real - Zr.real)[k1] * 1e3),
                fres_m=r["f_res_model"], fres_r=r["f_res_ref"], G5=float(abs(Z[k100] - Zr[k100]) / abs(Zr[k100]))), (f, Z, Zr)


rows = []
for tag, port in CASES:
    fa = os.path.join(OUT, f"result_{tag}_{port}_any.json")
    if not os.path.exists(fa):
        continue
    mg, (fg, Zg, Zrg) = metrics(exp5_file(tag, port)); ma, (fa_, Za, Zra) = metrics(fa)
    rows.append(dict(tag=tag, port=port, gnd=mg, any=ma))
    ref = np.load(f"/home/claude/data/S4LB002_{tag}_Zdiag.npz", allow_pickle=True)
    col = [i for i, n in enumerate(ref["port_names"]) if str(n).split("::")[0] == port][0]
    fr = ref["freq"]; mm = (fr >= 1e4) & (fr <= 1e8); zr = ref["Zdiag"][mm, col]; fr = fr[mm]
    fig, ax = plt.subplots(2, 2, figsize=(12, 8))
    for a_, fy, lab in ((ax[0, 0], lambda v: v.real * 1e3, "Re Z [mOhm]"), (ax[0, 1], lambda v: v.imag * 1e3, "Im Z [mOhm]"),
                        (ax[1, 0], np.abs, "|Z| [Ohm]"), (ax[1, 1], lambda v: np.degrees(np.angle(v)), "phase [deg]")):
        a_.plot(fr, fy(zr), "k-", lw=2, label="PowerSI")
        a_.plot(fg, fy(Zg), "o-", ms=3, label="model GND-only reference (EXP-5)")
        a_.plot(fa_, fy(Za), "s-", ms=3, label="model cavity-wall (PowerSI convention)")
        a_.set_xscale("log"); a_.set_ylabel(lab); a_.grid(alpha=.3); a_.legend(fontsize=7)
    ax[0, 1].set_yscale("symlog", linthresh=max(0.1, float(np.min(np.abs(zr.imag))) * 1e3)); ax[1, 0].set_yscale("log")
    ax[0, 0].set_ylim(0, float(np.percentile(np.r_[zr.real, Za.real[fa_ > 2e4]], 97)) * 1.3e3)
    fig.suptitle(f"EXP-8 {tag} {port}"); fig.tight_layout(); fig.savefig(os.path.join(OUT, f"Z_{tag}_{port}.png"), dpi=100); plt.close(fig)
pa = {(r["tag"], r["port"]): r for r in rows}
if ("260729", "Port18_SITE0") in pa and ("260804", "Port18_SITE0") in pa:
    a, b = pa[("260729", "Port18_SITE0")], pa[("260804", "Port18_SITE0")]
    rr = dict(ratio_model_any=b["any"]["fres_m"] / a["any"]["fres_m"], ratio_model_gnd=b["gnd"]["fres_m"] / a["gnd"]["fres_m"],
              ratio_ref=b["any"]["fres_r"] / a["any"]["fres_r"], ratio_ref_grid=1.445 / 1.585)
    rr["rel_err_any_vs_ref"] = rr["ratio_model_any"] / rr["ratio_ref"] - 1; rr["rel_err_any_vs_grid"] = rr["ratio_model_any"] / rr["ratio_ref_grid"] - 1
    print("RATIO", rr)
else:
    rr = None
json.dump(dict(rows=rows, resonance_ratio=rr), open(os.path.join(OUT, "table.json"), "w"), indent=1)
for r in rows:
    g, a = r["gnd"], r["any"]
    print(f"{r['tag']} {r['port']:13s} G1 {g['G1']:.3f}->{a['G1']:.3f} | fitdL {g['fit_dL']:+.1f}->{a['fit_dL']:+.1f} | dL1M {g['dL1M']:+.1f}->{a['dL1M']:+.1f} | "
          f"dRe1M {g['dRe1M']:+.3f}->{a['dRe1M']:+.3f} | err1M {100*g['err1M']:.1f}->{100*a['err1M']:.1f}% | fres {g['fres_m']/1e6:.3f}->{a['fres_m']/1e6:.3f} (ref {a['fres_r']/1e6:.3f}) | G5 {100*g['G5']:.0f}->{100*a['G5']:.0f}%")
