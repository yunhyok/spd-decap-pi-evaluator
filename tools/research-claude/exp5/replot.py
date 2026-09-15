"""Per-rail overlay plots with auto-scaled Re axis (run_exp1.plot fixes Re to 0-4 mOhm)."""
import glob, json, os
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
OUT = "/home/claude/work/exp5"
for fn in glob.glob(os.path.join(OUT, "result_*_Port*_*.json")):
    r = json.load(open(fn))
    ref = np.load(f"/home/claude/data/S4LB002_{r['tag']}_Zdiag.npz", allow_pickle=True)
    col = [i for i, n in enumerate(ref["port_names"]) if str(n).split("::")[0] == r["port"]][0]
    fr = ref["freq"]; m = (fr >= 1e3) & (fr <= 1e8); zr = ref["Zdiag"][m, col]; fr = fr[m]
    f = np.array(r["freq"]); z = np.array(r["Z_re"]) + 1j * np.array(r["Z_im"])
    fig, ax = plt.subplots(2, 2, figsize=(12, 8))
    for a_, fy, lab in ((ax[0, 0], lambda v: v.real * 1e3, "Re Z [mOhm]"), (ax[0, 1], lambda v: v.imag * 1e3, "Im Z [mOhm]"),
                        (ax[1, 0], np.abs, "|Z| [Ohm]"), (ax[1, 1], lambda v: np.degrees(np.angle(v)), "phase [deg]")):
        a_.plot(fr, fy(zr), "k-", lw=2, label="PowerSI"); a_.plot(f, fy(z), "o-", ms=3, label="model S2+(b)")
        a_.set_xscale("log"); a_.set_ylabel(lab); a_.grid(alpha=.3); a_.legend(fontsize=8)
    ax[0, 1].set_yscale("symlog", linthresh=max(0.1, float(np.min(np.abs(zr.imag))) * 1e3)); ax[1, 0].set_yscale("log")
    ax[0, 0].set_ylim(0, float(np.percentile(np.r_[zr.real[fr > 2e4], z.real[f > 2e4]], 98)) * 1.3e3)
    fig.suptitle(f"EXP-5 {r['tag']} {r['port']} {r['rail']} (decaps {r['decaps']})")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, f"Z_{r['tag']}_{r['port']}.png"), dpi=110); plt.close(fig)
print("ok")
