"""Print the EXP-3 ablation tables (markdown) from /home/claude/work/exp3/result_S*.json."""
import json
import os
import sys

OUT = "/home/claude/work/exp3"
steps = sys.argv[1:] or ["S0", "S1", "S2", "S3"]
LAD = [3.0e4, 1.2e5, 3.02e5, 1.0e6, 2.51e6, 9.12e6, 1.0e8]
rows = []
print("| step | G1 max|dRe| mΩ | G2 dL pH | G3 1 MHz | G4 max err / f_res | G5 100 MHz | unknowns |")
print("|---|---|---|---|---|---|---|")
res = {}
for s in steps:
    fn = os.path.join(OUT, f"result_{s}.json")
    if not os.path.exists(fn):
        continue
    r = json.load(open(fn)); res[s] = r; g = r["ladder_gates"]; p = g["PASS"]
    tag = lambda k: "PASS" if p[k] else "FAIL"  # noqa: E731
    print(f"| {s} | {tag('G1')} {g['G1_max_abs_dRe_mOhm']:.3f} | {tag('G2')} {g['G2_dL_pH_range'][0]:+.1f}…{g['G2_dL_pH_range'][1]:+.1f} | "
          f"{tag('G3')} {100*g['G3_rel_err_1MHz']:.1f}% | {tag('G4')} {100*g['G4_max_rel_err']:.0f}% / {g['G4_f_res_model_Hz']/1e6:.3f} MHz | "
          f"{100*g['G5_rel_err'][-1]:.0f}% | {r['info'].get('unknowns', r['info'].get('nodes_after_prune'))} |")
print()
print("| step | " + " | ".join(f"{f/1e6:g} MHz dL/dRe" for f in LAD) + " |")
print("|---|" + "---|" * len(LAD))
for s, r in res.items():
    cells = []
    for f in LAD:
        k = min(range(len(r["freq"])), key=lambda i: abs(r["freq"][i] - f))
        cells.append(f"{r['dL_pH'][k]:+.1f} / {r['dRe_mOhm'][k]:+.3f}")
    print(f"| {s} | " + " | ".join(cells) + " |")
print()
for fb in ("1e+05", "1e+06"):
    print(f"L/R by class at {fb} Hz (pH / mΩ)")
    keys = {}
    for s, r in res.items():
        for k, v in r["breakdown"].get(fb, {}).items():
            if isinstance(v, dict) and "mOhm" in v:
                kk = k.replace("plane_rail_R", "plane_R").replace("trace_", "rail_traces_").replace("via_", "rail_vias_")
                keys.setdefault(kk, {})[s] = v
    for k, d in keys.items():
        vals = [f"{d[s]['mOhm']:.3f} mΩ / {d[s].get('pH', 0):.1f} pH" if s in d else "—" for s in res]
        if any(s in d and (abs(d[s]['mOhm']) > 0.003 or abs(d[s].get('pH', 0)) > 0.3) for s in res) and not k.startswith("decaps") and not k.startswith("_"):
            print(f"| {k} | " + " | ".join(vals) + " |")
    print()
