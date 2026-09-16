#!/usr/bin/env python
"""EXP-9 (b)/(d) model-side audit: which rail Trace/Via/pad elements the discretised model shorts.

Builds the frozen model (run9.build) and, for every SPD rail trace drawn into a plane raster
(model3.py:230-234) and every via / off-plane trace skipped because both ends map to the same
unknown (model3.py:282, 299), reports its would-be DC resistance, grouped by site (DUT pins / each decap).
Also reports which plane cell size each decap-site node snapped with (coarse 200 um vs fine 50 um).
"""
import collections, json, math, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))
import run9 as R9
from paths import work_file

tag, port = "260729", sys.argv[1] if len(sys.argv) > 1 else "Port19_SITE0"
ex, mdl = R9.build(tag, port)
rn = ex["rail_nodes"]; st = mdl.st; lwd = ex.get("layer_default_width_um", {})
sites = {"DUT": [(rn[n][0], rn[n][1]) for n in ex["port_pos_nodes"]]}
for d in ex["decaps"]:
    sites[d["refdes"]] = [(rn[d["rail_node"]][0], rn[d["rail_node"]][1])]
def site(x, y):
    best = min(((min(math.hypot(x - a, y - b) for a, b in v), k) for k, v in sites.items()))
    return best[1] if best[0] < 3000 else "other"
snap = {}
for nid, (x, y, L, ps) in rn.items():
    if L in mdl.sheets:
        r = mdl.sheets[L].snap(x, y)
        snap[nid] = r
res = collections.defaultdict(lambda: collections.defaultdict(float)); cnt = collections.Counter()
for s_, e_, w in ex["rail_traces"]:
    L = rn[s_][2]
    if L != rn[e_][2] or L not in mdl.sheets:
        continue
    w = w or lwd.get(L, 25.0); ln = math.hypot(rn[s_][0] - rn[e_][0], rn[s_][1] - rn[e_][1])
    r = ln / (w * st.row(L)["conductivity"] * st.row(L)["thickness_um"] * 1e-6)
    a, b = snap.get(s_), snap.get(e_)
    same = a is not None and b is not None and a[0] == b[0]
    k = site(rn[s_][0], rn[s_][1]); h = a[1] if a else None
    key = f"{L.replace('Signal$','')} h={h}"
    res[k][key + (" SAME-CELL(shorted)" if same else " drawn")] += r * 1e3
    cnt[(k, key, same)] += 1
out = {k: dict(v) for k, v in res.items()}
print(json.dumps(out, indent=1))
print({str(k): v for k, v in cnt.items()})
# nodes whose snap fails (standalone)
print("standalone plane-layer nodes:", sum(1 for v in snap.values() if v is None))
json.dump(dict(series_sum_mOhm=out, counts={str(k): v for k, v in cnt.items()}), open(work_file("exp9", f"audit_{tag}_{port}.json"), "w"), indent=1)
