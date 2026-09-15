"""EXP-2: DGND Node/Via/Trace extraction inside a crop window (new regex pass;
the product parser has no Trace/Via graph reader for this purpose)."""
import mmap
import pickle
import re
import sys
import time

import numpy as np

from spd_decap_pi._core.io import spd as P


def extract_gnd(spd_path, window, layers, gnd=b"DGND"):
    t0 = time.time()
    x0, y0, x1, y1 = window
    lay_set = {l.encode() for l in layers}
    out = {}
    with open(spd_path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as d:
        n0 = d.find(b"* Node description lines"); t_0 = d.find(b"* Trace description lines")
        v_0 = d.find(b"* Via description lines"); w_0 = d.find(b"* WirebondDefinition description lines")
        rx = re.compile(rb"(?m)^(Node\d+)(?:!![^:\s]*)?::" + gnd + rb" X = (\S+) Y = (\S+) Layer = (\S+)(?: PadStack = (\S+))?")
        nodes = {}
        for a in rx.finditer(d, n0, t_0):
            if a.group(4) not in lay_set:
                continue
            x = P._length_um(a.group(2)); y = P._length_um(a.group(3))
            if x0 <= x <= x1 and y0 <= y <= y1:
                nodes[a.group(1).decode()] = (x, y, a.group(4).decode(), a.group(5).decode() if a.group(5) else None)
        print(f"[gnd] nodes {len(nodes)} {time.time()-t0:.1f}s", flush=True)
        rxt = re.compile(rb"(?m)^Trace\d+::" + gnd + rb" StartingNode = (Node\d+)\S* EndingNode = (Node\d+)\S*([^\n]*)\n(\+[^\n]*)?")
        traces = []
        for a in rxt.finditer(d, t_0, v_0):
            s = a.group(1).decode(); e = a.group(2).decode()
            if s in nodes and e in nodes:
                tail = (a.group(3) or b"") + b" " + (a.group(4) or b"")
                wm = re.search(rb"Width = (\S+)", tail)
                traces.append((s, e, P._length_um(wm.group(1)) if wm else None))
        print(f"[gnd] traces {len(traces)} {time.time()-t0:.1f}s", flush=True)
        rxv = re.compile(rb"(?m)^Via\d+::" + gnd + rb" UpperNode = (Node\d+)\S* LowerNode = (Node\d+)\S* PadStack = (\S+)")
        vias = []
        for a in rxv.finditer(d, v_0, w_0):
            u = a.group(1).decode(); l = a.group(2).decode()
            if u in nodes and l in nodes:
                vias.append((u, l, a.group(3).decode()))
        print(f"[gnd] vias {len(vias)} {time.time()-t0:.1f}s", flush=True)
    return dict(window=window, nodes=nodes, traces=traces, vias=vias, seconds=time.time() - t0)


if __name__ == "__main__":
    ex = pickle.load(open("/home/claude/work/exp1/extract_port18.pkl", "rb"))
    rn = ex["rail_nodes"]
    pts = [(v[0], v[1]) for v in ex["gnd_port_nodes"].values()]
    pts += [(rn[x][0], rn[x][1]) for x in ex["port_pos_nodes"]]
    pts += [(rn[dc["rail_node"]][0], rn[dc["rail_node"]][1]) for dc in ex["decaps"]]
    pts += [(rn[u][0], rn[u][1]) for u, l, p in ex["rail_vias"] if p == "DR-2128_350"]
    pts = np.array(pts)
    m = 2000.0
    window = (pts[:, 0].min() - m, pts[:, 1].min() - m, pts[:, 0].max() + m, pts[:, 1].max() + m)
    names = [r["name"] for r in ex["stackup"] if r["conductivity"] is not None]
    layers = names[: names.index("Signal$L28(DGND)") + 1]
    g = extract_gnd(ex["spd_path"], window, layers)
    pickle.dump(g, open("/home/claude/work/exp2/extract_gnd.pkl", "wb"))
    print("window", window)
