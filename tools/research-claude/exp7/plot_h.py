"""Plot dL(h) and dRe(h) for the coarse-mesh emulation (port 18, port 1) with the adopted-model point."""
import glob, json
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
OUT = "/home/claude/work/exp7"


def fitdl(fn, lo=2.9e4, hi=3.1e5):
    r = json.load(open(fn)); f = np.array(r["freq"]); Z = np.array(r["Z_re"]) + 1j * np.array(r["Z_im"]); Zr = np.array(r["Zref_re"]) + 1j * np.array(r["Zref_im"])
    w = 2 * np.pi * f; m = (f >= lo) & (f <= hi); A = np.column_stack([w[m], -1 / w[m]]); c, *_ = np.linalg.lstsq(A, (Z.imag - Zr.imag)[m], rcond=None)
    k = int(np.argmin(abs(f - 1e5)))
    return c[0] * 1e12, (Z.real - Zr.real)[k] * 1e3


fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
rows = {}
for port, adopted in (("Port18_SITE0", "/home/claude/work/exp6/result_Port18_SITE0_V0.json"), ("Port1_SITE0", "/home/claude/work/exp6/result_Port1_SITE0_V0.json")):
    pts = [(50.0, *fitdl(adopted))]
    for fn in glob.glob(f"{OUT}/coarse_{port}_B_h*.json"):
        r = json.load(open(fn)); pts.append((r["h_um"], r["fit_dL_30k_300k_pH"], r["dRe_100k_mOhm"]))
    pts.sort()
    rows[port] = pts
    h = [p[0] for p in pts]
    ax[0].semilogx(h, [p[1] for p in pts], "o-", label=port); ax[1].semilogx(h, [p[2] for p in pts], "o-", label=port)
for a_, lab in ((ax[0], "fitted dL 30-300 kHz [pH]"), (ax[1], "dRe at 100 kHz [mOhm]")):
    a_.axhline(0, color="k", lw=1); a_.set_xlabel("plane grid h [um] (50 = adopted 200/50 um fine-box model)"); a_.set_ylabel(lab); a_.grid(alpha=.3); a_.legend()
fig.suptitle("EXP-7 coarse-mesh emulation (frozen S2+(b) physics, ideal GND)")
fig.tight_layout(); fig.savefig(f"{OUT}/exp7_dL_dRe_vs_h.png", dpi=110)
json.dump(rows, open(f"{OUT}/h_series.json", "w"), indent=1)
print(rows)
