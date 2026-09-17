#!/usr/bin/env python
"""EXP-27: model connectivity audit -- decaps galvanically cut off from the port (92 ports of 260729).

Build only (no solve).  Per port the exp13/j model is built exactly like run11 variant j
(R4.patch_traces("b"), M3.TwoSided = R8.TwoSidedAny, P5.prepare, P5.ModelB(..., c_unit_fix=True)),
then the *element* graph is assembled from mdl.sheets[*].edges + mdl.traces + mdl.vias + mdl.gnd_r
(empty in ideal-GND mode) mapped through mdl.map, dropping every edge that touches the reference
(map < 0) and every decap stamp.  scipy.sparse.csgraph.connected_components on that graph gives the
same reachability test EXP-26 did on Y - Y_dec, without factorising anything.

A decap is "isolated" when its rail node is not in the port's (mdl.P) component.  For every isolated
decap the extract is traced: rail node layer / xy / padstack, same-net rail nodes, via endpoints and
trace endpoints within 300 um, and whether the node sits on a plane sheet layer and was snapped to a
cell (Sheet.snap) -- the EXP27_PLAN section 2 H_mech classification
("missing_element" / "snap_fail" / "layout_isolated").

  python audit27.py --one Port65_SITE1        # one worker, writes WORK_DIR/exp27/cache/{port}.json
  python audit27.py --jobs 7                  # parent: 92 workers as subprocesses, then aggregate
  python audit27.py --dry                     # list what would run
  python audit27.py --selfcheck               # graph-logic self test, no model build

Output: WORK_DIR/exp27/exp27.json + a printed markdown summary.  Nothing else is written.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph
from scipy.spatial import cKDTree
from scipy.stats import mannwhitneyu

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("../exp11", "../common"):
    sys.path.insert(0, os.path.join(HERE, p))
from paths import peak_rss_mb, ref_npz, work_dir  # noqa: E402

TAG = "260729"      # module-level default; --tag overrides it (EXP-30: s5m6585)
VARIANT = "j"  # module-level default; --variant overrides it (EXP-28: p)
F_C = 1e3       # capacitance estimate frequency (plan section 1)
F_ESR = 1e5     # ESR / receipt frequency
RADIUS = 300.0  # um, plan sections 1/2
H_FREQ_MIN = 19  # >= 20 % of 92 ports


def all_ports():
    ref = np.load(ref_npz(TAG), allow_pickle=True)
    return [str(n).split("::")[0] for n in ref["port_names"]]


def cache_file(port):
    """Cache name gets a _{variant} suffix for every variant but the default j."""
    d = os.path.join(work_dir("exp27"), "cache")
    os.makedirs(d, exist_ok=True)
    suf = "" if VARIANT == "j" else f"_{VARIANT}"
    suf += "" if TAG == "260729" else f"_{TAG}"
    return os.path.join(d, f"{port}{suf}.json")


# ---------------------------------------------------------------- element graph
def element_components(mdl):
    """Connected components of the reference-excluded element graph (no decap stamps).

    Edges: plane-sheet cell edges, off-plane rail traces, rail vias + pad links, explicit GND
    resistors (empty in ideal-GND mode).  Node ids go through mdl.map; map < 0 means the node is the
    reference or was pruned, so the edge touches the reference and is dropped -- exactly what
    model3.assemble does with its stamps."""
    rows, cols = [], []
    for sh in mdl.sheets.values():
        rows.append(sh.edges[0])
        cols.append(sh.edges[1])
    for el in (mdl.traces, mdl.vias, mdl.gnd_r):
        rows.append(el[0])
        cols.append(el[1])
    cat = lambda xs: np.concatenate([np.asarray(x, np.int64) for x in xs])  # noqa: E731
    a = mdl.map(cat(rows))
    b = mdl.map(cat(cols))
    ok = (a >= 0) & (b >= 0)
    G = sparse.coo_matrix((np.ones(int(ok.sum())), (a[ok], b[ok])), shape=(mdl.N, mdl.N))
    return csgraph.connected_components(G, directed=False)


def selfcheck():
    """Graph logic on a hand-made 5-node model: 0-1-2 chain (port 0), 3-4 chain, 4 -> reference."""
    class FakeSheet:
        edges = [np.array([0, 3]), np.array([1, 4])]

    class Fake:
        sheets = {"L": FakeSheet()}
        traces = [np.array([1]), np.array([2])]
        vias = [np.array([], np.int64), np.array([], np.int64)]
        gnd_r = [np.array([4]), np.array([-1])]  # edge to the reference: must be dropped
        N, P = 5, 0
        map = staticmethod(lambda x: np.asarray(x, np.int64))

    ncomp, lab = element_components(Fake())
    assert ncomp == 2, ncomp
    assert lab[1] == lab[0] and lab[2] == lab[0], lab
    assert lab[3] != lab[0] and lab[4] == lab[3], lab  # 3-4 isolated, 4's reference edge dropped
    print("selfcheck OK")
    return 0


# ---------------------------------------------------------------- extract trace
def near_counts(ex, nid):
    """Same-net neighbours of rail node nid within RADIUS um (every rail_node is on the rail net)."""
    rn = ex["rail_nodes"]
    ids = list(rn)
    xy = np.array([(rn[i][0], rn[i][1]) for i in ids], float)
    x, y, lay, _ps = rn[nid]
    hit = [ids[k] for k in cKDTree(xy).query_ball_point([x, y], RADIUS) if ids[k] != nid]
    via_ep, trace_ep = set(), set()
    for up, lo, _p in ex["rail_vias"]:
        via_ep.add(up)
        via_ep.add(lo)
    for s_, e_, _w in ex["rail_traces"]:
        trace_ep.add(s_)
        trace_ep.add(e_)
    dist = {i: float(np.hypot(rn[i][0] - x, rn[i][1] - y)) for i in hit}
    same = [i for i in hit if rn[i][2] == lay]
    return dict(
        n_rail_nodes_300um=len(hit), n_rail_nodes_300um_same_layer=len(same),
        n_via_endpoints_300um=sum(i in via_ep for i in hit),
        n_via_endpoints_300um_same_layer=sum(i in via_ep for i in same),
        n_trace_endpoints_300um=sum(i in trace_ep for i in hit),
        n_trace_endpoints_300um_same_layer=sum(i in trace_ep for i in same),
        self_is_via_endpoint=nid in via_ep, self_is_trace_endpoint=nid in trace_ep,
        nearest_um=(min(dist.values()) if hit else None),
        neighbours=[dict(node=i, layer=rn[i][2], dist_um=dist[i], dx_um=rn[i][0] - x, dy_um=rn[i][1] - y,
                         via_endpoint=i in via_ep, trace_endpoint=i in trace_ep, padstack=rn[i][3])
                    for i in sorted(hit, key=dist.get)[:20]])


def trace_node(ex, mdl, nid):
    """Plan section 1 extract trace + section 2 H_mech class for one isolated decap's rail node."""
    x, y, lay, ps = ex["rail_nodes"][nid]
    on_sheet = lay in mdl.sheets
    snap = mdl.sheets[lay].snap(x, y) if on_sheet else None
    t = dict(rail_node=nid, layer=lay, x_um=x, y_um=y, padstack=ps,
             on_sheet_layer=on_sheet, snapped=snap is not None,
             snap_cell=(int(snap[0]) if snap else None), snap_h_um=(float(snap[1]) if snap else None),
             padstack_info=ex["padstacks"].get(ps))
    t.update(near_counts(ex, nid))
    if t["n_via_endpoints_300um"] or t["n_trace_endpoints_300um"]:
        t["h_mech_class"] = "missing_element"
    elif on_sheet and snap is None:
        t["h_mech_class"] = "snap_fail"
    else:
        t["h_mech_class"] = "layout_isolated"
    return t


# ---------------------------------------------------------------- component trace
def rebuild_index(ex, mdl):
    """Reproduce model3.build's raw node numbering (idx + the port supernode remap) read-only.

    Rail nodes that Sheet.snap places on a plane sheet get that cell's id, the others get a fresh id
    counting on from the last sheet cell -- exactly the `idx` / `Rm` of model3.build."""
    rn = ex["rail_nodes"]
    n = max(sh.n for sh in mdl.sheets.values())
    idx, snapped = {}, {}
    for nid, (x, y, lay, _ps) in rn.items():
        r = mdl.sheets[lay].snap(x, y) if lay in mdl.sheets else None
        if r is not None:
            idx[nid] = int(r[0])
        else:
            idx[nid] = n
            n += 1
        snapped[nid] = r is not None
    port = n
    pos = {idx[x] for x in ex["port_pos_nodes"] if x in idx}
    return idx, snapped, (lambda i: port if i in pos else i), port


def rebuild_elements(ex, mdl, idx, Rm):
    """The off-plane traces / vias / pad links of model3.build, in the same order, with their
    extract node ids kept.  Lengths are asserted against mdl.traces / mdl.vias."""
    rn = ex["rail_nodes"]
    drawn = {k for k, (s_, e_, _w) in enumerate(ex["rail_traces"])
             if s_ in rn and e_ in rn and rn[s_][2] == rn[e_][2] and rn[s_][2] in mdl.sheets}
    tr = []
    for k, (s_, e_, _w) in enumerate(ex["rail_traces"]):
        if k in drawn or s_ not in rn or e_ not in rn:
            continue
        a_, b_ = Rm(idx[s_]), Rm(idx[e_])
        if a_ != b_:
            tr.append(("trace", a_, b_, s_, e_, None))
    vi = []
    for up, lo, ps in ex["rail_vias"]:
        if up not in rn or lo not in rn:
            continue
        a_, b_ = Rm(idx[up]), Rm(idx[lo])
        if a_ != b_:
            vi.append(("via", a_, b_, up, lo, ps))
    pl = [("pad_link", Rm(idx[a_]), Rm(idx[b_]), a_, b_, None)
          for a_, b_, _r in mdl._pad_links(rn) if Rm(idx[a_]) != Rm(idx[b_])]
    assert len(tr) == len(mdl.traces[0]), (len(tr), len(mdl.traces[0]))
    assert len(vi) + len(pl) == len(mdl.vias[0]), (len(vi), len(pl), len(mdl.vias[0]))
    return tr, vi, pl


def cell_probe(sh, x, y):
    """Where (x, y) lands on sheet sh: the snapped cell (None = not snapped), the raster fill of the
    cell that contains the point, and the nearest existing (fill > 0) cell with its fill."""
    r = sh.snap(x, y)
    out = dict(layer=sh.layer, snapped=r is not None, snap_cell=(int(r[0]) if r else None),
               snap_h_um=(float(r[1]) if r else None), fill_at_xy=None, in_block=None,
               nearest_cell=None, nearest_cell_um=None, nearest_cell_fill=None, nearest_cell_h_um=None)
    order = [1, 0] if len(sh.blocks) == 2 else [0]
    for bi in order:
        b = sh.blocks[bi]
        i = int((x - b["x0"]) // b["h"]); j = int((y - b["y0"]) // b["h"])
        if 0 <= i < b["nx"] and 0 <= j < b["ny"]:
            out["fill_at_xy"] = float(b["fill"][j, i])
            out["in_block"] = "fine" if bi == 1 else "coarse"
            break
    best = None
    for bi in order:
        b = sh.blocks[bi]
        tree, ids = sh.trees[bi]
        if tree is None:
            continue
        d, k = tree.query([x, y])
        if best is None or d < best[0]:
            jj, ii = np.nonzero(b["mask"])
            best = (float(d), int(ids[k]), float(b["fill"][jj[k], ii[k]]), float(b["h"]))
    if best:
        out.update(nearest_cell_um=best[0], nearest_cell=best[1], nearest_cell_fill=best[2],
                   nearest_cell_h_um=best[3])
    return out


def component_trace(port):
    """--component-trace: for every isolated decap of `port`, dump its whole element component."""
    import run11 as R11  # heavy import: worker process only

    t0 = time.time()
    ex, mdl = R11.setup(TAG, port, VARIANT)
    ncomp, lab = element_components(mdl)
    rn = ex["rail_nodes"]
    idx, snapped, Rm, raw_port = rebuild_index(ex, mdl)
    meta = [dc for dc in ex["decaps"] if dc["rail_node"] in rn]
    assert all(Rm(idx[m["rail_node"]]) == d[0] for m, d in zip(meta, mdl.dec)), "idx rebuild mismatch"
    tr, vi, pl = rebuild_elements(ex, mdl, idx, Rm)

    # raw node id -> matrix index, and back (the port supernode has several raw ids)
    m_of = mdl.map(np.arange(mdl.n_raw + 1))
    raws_of = {}
    for r, m in enumerate(m_of):
        if m >= 0:
            raws_of.setdefault(int(m), []).append(int(r))
    nodes_of_raw = {}
    for nid in rn:
        nodes_of_raw.setdefault(Rm(idx[nid]), []).append(nid)
    # raw id ranges of each plane sheet's cells
    ranges = []
    lo = 0
    for L, sh in mdl.sheets.items():
        ranges.append((lo, sh.n, L))
        lo = sh.n

    def sheet_of(raw):
        return next((L for a, b, L in ranges if a <= raw < b), None)

    def node_info(raw):
        d = dict(raw=int(raw), sheet_layer=sheet_of(raw),
                 rail_nodes=[dict(node=nid, layer=rn[nid][2], x_um=rn[nid][0], y_um=rn[nid][1],
                                  padstack=rn[nid][3], snapped_to_sheet=snapped[nid])
                             for nid in nodes_of_raw.get(raw, [])])
        d["kind"] = ("sheet_cell" if d["sheet_layer"] and not d["rail_nodes"] else
                     "rail_node_on_sheet_cell" if d["sheet_layer"] else "rail_node")
        return d

    pad_pairs = mdl._pad_links(rn)
    out = dict(port=port, tag=TAG, variant=VARIANT, unknowns=int(mdl.N), port_index=int(mdl.P),
               port_component=int(lab[mdl.P]), n_components=int(ncomp),
               n_pad_links_total=len(pad_pairs), components=[])

    for k, ((ra, _ga, mid), m) in enumerate(zip(mdl.dec, meta)):
        i = int(mdl.map([ra])[0])
        if i < 0 or lab[i] == lab[mdl.P]:
            continue
        comp = int(lab[i])
        mem = np.flatnonzero(lab == comp)
        mset = set(int(x) for x in mem)
        nid0 = m["rail_node"]

        cnt = {f"sheet:{L}": int(np.isin(mdl.map(sh.edges[0]), list(mset)).sum()) if len(sh.edges[0]) else 0
               for L, sh in mdl.sheets.items()}
        cnt = {k2: v for k2, v in cnt.items() if v}
        inside = {"trace": [], "via": [], "pad_link": []}
        for kind, a_, b_, na, nb, ps in tr + vi + pl:
            ma, mb = int(mdl.map([a_])[0]), int(mdl.map([b_])[0])
            if ma in mset or mb in mset:
                inside[kind].append((na, nb, ps, ma, mb))
        cnt.update({f"{k2}s_in_component": len(v) for k2, v in inside.items()})

        # every via of the component whose endpoint sits on a rail sheet layer: snapped? cell fill?
        via_probe = []
        for na, nb, ps, ma, mb in inside["via"]:
            e = dict(padstack=ps, upper=na, lower=nb, both_endpoints_in_component=(ma in mset and mb in mset))
            for side, nn in (("upper", na), ("lower", nb)):
                x, y, lay, nps = rn[nn]
                p = dict(node=nn, layer=lay, x_um=x, y_um=y, padstack=nps,
                         on_rail_sheet_layer=lay in mdl.sheets, raw=int(Rm(idx[nn])),
                         matrix_index=int(mdl.map([Rm(idx[nn])])[0]))
                p["in_component"] = p["matrix_index"] in mset
                if lay in mdl.sheets:
                    p.update(cell_probe(mdl.sheets[lay], x, y))
                e[side + "_info"] = p
            via_probe.append(e)

        big = len(mem) > 2000
        out["components"].append(dict(
            decap=dict(refdes=m["refdes"], part=m["part"], model_id=mid, rail_node=nid0,
                       layer=rn[nid0][2], x_um=rn[nid0][0], y_um=rn[nid0][1], padstack=rn[nid0][3],
                       snapped_to_sheet=snapped[nid0], matrix_index=i, component=comp),
            n_nodes=int(len(mem)), element_counts=cnt,
            rail_node_pad_links=[dict(other=(b_ if a_ == nid0 else a_),
                                      other_layer=rn[b_ if a_ == nid0 else a_][2],
                                      other_padstack=rn[b_ if a_ == nid0 else a_][3])
                                 for a_, b_, _r in pad_pairs if nid0 in (a_, b_)],
            rail_node_in_pad_link_list=any(nid0 in (a_, b_) for a_, b_, _r in pad_pairs),
            nodes_truncated=big,
            nodes=[node_info(r) for mi in mem[:2000] for r in raws_of.get(int(mi), [])],
            vias=via_probe,
            traces=[dict(a=na, b=nb, a_layer=rn[na][2], b_layer=rn[nb][2]) for na, nb, _p, _x, _y in inside["trace"]],
            pad_links=[dict(a=na, b=nb, layer=rn[na][2]) for na, nb, _p, _x, _y in inside["pad_link"]][:200],
        ))
    out["wall_seconds"] = time.time() - t0
    out["peak_rss_MB"] = peak_rss_mb()
    return out


def component_markdown(o):
    L = ["", f"## EXP-27 component trace -- {o['port']} (tag {o['tag']}, variant {o['variant']})", "",
         f"- model unknowns {o['unknowns']}, element-graph components {o['n_components']}, "
         f"port component {o['port_component']}, pad links in the model {o['n_pad_links_total']}"]
    for c in o["components"]:
        d = c["decap"]
        L += ["", f"### {d['refdes']} ({d['model_id']}) -- component {d['component']}, {c['n_nodes']} nodes", "",
              f"- rail node {d['rail_node']} layer `{d['layer']}` at ({d['x_um']:.1f}, {d['y_um']:.1f}) um, "
              f"padstack `{d['padstack']}`, snapped to a rail sheet: {d['snapped_to_sheet']}",
              f"- in `_pad_links`: {c['rail_node_in_pad_link_list']} "
              f"({len(c['rail_node_pad_links'])} pad links: "
              + (", ".join(f"{p['other']}@{p['other_layer']}" for p in c["rail_node_pad_links"][:8]) or "-") + ")",
              "- elements inside the component: "
              + (", ".join(f"{k} {v}" for k, v in sorted(c["element_counts"].items())) or "none"),
              "", "| node (raw) | kind | sheet layer | rail nodes (layer, xy, snapped) |", "|---|---|---|---|"]
        for n in c["nodes"][:60]:
            rl = "; ".join(f"{r['node']} {r['layer']} ({r['x_um']:.1f}, {r['y_um']:.1f}) "
                           f"snap={r['snapped_to_sheet']} ps={r['padstack']}" for r in n["rail_nodes"]) or "-"
            L.append(f"| {n['raw']} | {n['kind']} | {n['sheet_layer'] or '-'} | {rl} |")
        if c["nodes_truncated"] or len(c["nodes"]) > 60:
            L.append(f"| ... | ({len(c['nodes'])} listed, {c['n_nodes']} in the component) | | |")
        if c["vias"]:
            L += ["", "| via padstack | endpoint | node | layer | on rail sheet | snapped | fill@xy | "
                  "nearest cell (um) | nearest fill | in component |", "|---|---|---|---|---|---|---|---|---|---|"]
            for v in c["vias"]:
                for side in ("upper", "lower"):
                    p = v[side + "_info"]
                    fx = "-" if p.get("fill_at_xy") is None else f"{p['fill_at_xy']:.3f}"
                    nd = "-" if p.get("nearest_cell_um") is None else f"{p['nearest_cell_um']:.1f}"
                    nf = "-" if p.get("nearest_cell_fill") is None else f"{p['nearest_cell_fill']:.3f}"
                    L.append(f"| {v['padstack']} | {side} | {p['node']} | {p['layer']} | "
                             f"{p['on_rail_sheet_layer']} | {p.get('snapped', '-')} | {fx} | {nd} | {nf} | "
                             f"{p['in_component']} |")
        if c["traces"]:
            L += ["", "traces in the component: "
                  + ", ".join(f"{t['a']}@{t['a_layer']}-{t['b']}@{t['b_layer']}" for t in c["traces"][:20])]
        if c["pad_links"]:
            L += ["", "pad links in the component: "
                  + ", ".join(f"{p['a']}-{p['b']}@{p['layer']}" for p in c["pad_links"][:20])]
    return "\n".join(L)


# ---------------------------------------------------------------- raster probe
def _block_of(sh, cid):
    """(block index, j, i) of a sheet cell id."""
    for bi, b in enumerate(sh.blocks):
        w = np.argwhere(b["ids"] == cid)
        if len(w):
            return bi, int(w[0][0]), int(w[0][1])
    return None


def sheet_patches(sh):
    """Connected components of the sheet's own cell edges (global cell id -> patch label)."""
    cells = np.sort(sh.cells)
    loc = lambda g: np.searchsorted(cells, g)  # noqa: E731
    a, b = loc(sh.edges[0]), loc(sh.edges[1])
    G = sparse.coo_matrix((np.ones(len(a)), (a, b)), shape=(len(cells), len(cells)))
    _, lab = csgraph.connected_components(G, directed=False)
    return cells, lab, loc


def raster_probe(port, layer, x0, y0, rad):
    """--raster-probe: the rasterised sheet around (x0, y0): cells, fills, edge G values, which
    cell each rail node snapped to, and a cell-by-cell walk between the two nodes' cells."""
    import run11 as R11  # heavy import: worker process only

    t0 = time.time()
    ex, mdl = R11.setup(TAG, port, VARIANT)
    sh = mdl.sheets[layer]
    cells, lab, loc = sheet_patches(sh)
    ea, eb, ell, ewid, eg = sh.edges

    # cells whose centre is within rad of (x0, y0)
    cl = []
    for bi, b in enumerate(sh.blocks):
        jj, ii = np.nonzero(b["mask"])
        cx = b["x0"] + (ii + 0.5) * b["h"]
        cy = b["y0"] + (jj + 0.5) * b["h"]
        d = np.hypot(cx - x0, cy - y0)
        for k in np.flatnonzero(d <= rad):
            cid = int(b["ids"][jj[k], ii[k]])
            cl.append(dict(cell=cid, block=("fine" if bi else "coarse"), j=int(jj[k]), i=int(ii[k]),
                           x_um=float(cx[k]), y_um=float(cy[k]), h_um=float(b["h"]),
                           fill=float(b["fill"][jj[k], ii[k]]), patch=int(lab[loc(cid)]),
                           dist_um=float(d[k])))
    S = {c["cell"] for c in cl}
    sel = np.flatnonzero(np.isin(ea, list(S)) | np.isin(eb, list(S)))
    edges = [dict(a=int(ea[k]), b=int(eb[k]), G=float(eg[k]), ell_um=float(ell[k]), w_um=float(ewid[k]))
             for k in sel]
    touch = {}
    for e in edges:
        touch.setdefault(e["a"], []).append(e)
        touch.setdefault(e["b"], []).append(e)
    for c in cl:
        c["edges"] = [dict(other=(e["b"] if e["a"] == c["cell"] else e["a"]), G=e["G"])
                      for e in touch.get(c["cell"], [])]
    cl.sort(key=lambda c: c["dist_um"])

    # rail nodes of this layer inside the radius -> snapped cell (None = not snapped)
    nodes = []
    for nid, (nx_, ny_, lay, ps) in ex["rail_nodes"].items():
        if lay != layer or np.hypot(nx_ - x0, ny_ - y0) > rad:
            continue
        r = sh.snap(nx_, ny_)
        nodes.append(dict(node=nid, x_um=nx_, y_um=ny_, padstack=ps, dist_um=float(np.hypot(nx_ - x0, ny_ - y0)),
                          snap_cell=(int(r[0]) if r else None),
                          patch=(int(lab[loc(int(r[0]))]) if r else None)))
    nodes.sort(key=lambda n: n["dist_um"])

    out = dict(port=port, layer=layer, x_um=x0, y_um=y0, radius_um=rad,
               sheet_cells=int(len(sh.cells)), sheet_edges=int(len(ea)),
               sheet_patches=int(lab.max() + 1) if len(lab) else 0,
               patch_sizes=dict(collections.Counter(int(v) for v in lab).most_common(20)),
               blocks=[dict(kind=("fine" if bi else "coarse"), h_um=b["h"], nx=b["nx"], ny=b["ny"],
                            x0=b["x0"], y0=b["y0"]) for bi, b in enumerate(sh.blocks)],
               cells=cl, nodes=nodes, walks=[])

    # cell-by-cell walk between the snapped cells of every pair of distinct patches among the nodes
    seen = set()
    named = [n for n in nodes if n["snap_cell"] is not None]
    for na in named:
        for nb in named:
            key = (na["patch"], nb["patch"])
            if na["patch"] == nb["patch"] or key in seen or key[::-1] in seen:
                continue
            seen.add(key)
            out["walks"].append(walk_cells(sh, na, nb))
    # sub-tile raster under the first few distinct broken steps: is the metal there at all?
    done = set()
    for w in out["walks"]:
        for s in w["steps"]:
            key = (tuple(s["frm"]), tuple(s["to"]))
            if s["edge_in_model"] or key in done or key[::-1] in done or len(done) >= 6:
                continue
            done.add(key)
            out.setdefault("subtiles", []).append(subtile_dump(ex, mdl, sh, layer, s))
    out["wall_seconds"] = time.time() - t0
    out["peak_rss_MB"] = peak_rss_mb()
    return out


def subtile_dump(ex, mdl, sh, layer, step):
    """Re-raster the artwork under one broken cell pair at the sheet's own sub-tile size.

    G == 0 exactly means homog.window_conductance saw an empty window (fill == 0), so this shows
    whether the metal is missing from the raster or merely narrow."""
    import homog as H
    import model3 as M3

    bi = int(step.get("block_index", 0))
    b = sh.blocks[bi]
    sub = mdl.sub[2] if layer == "Signal$TOP" else (mdl.sub[0] if bi == 0 else mdl.sub[1])
    s = b["h"] / sub
    (j1, i1), (j2, i2) = step["frm"], step["to"]
    i0, iN = min(i1, i2), max(i1, i2) + 1
    j0, jN = min(j1, j2), max(j1, j2) + 1
    x0 = b["x0"] + i0 * b["h"]
    y0 = b["y0"] + j0 * b["h"]
    nx = int((iN - i0) * sub)
    ny = int((jN - j0) * sub)

    rn = ex["rail_nodes"]
    geoms = {g["layer"]: g for g in ex["rail_geoms"]}
    lwd = ex.get("layer_default_width_um", {})
    tr = [(rn[a][0], rn[a][1], rn[c][0], rn[c][1], w or lwd.get(layer, 25.0))
          for a, c, w in ex["rail_traces"]
          if a in rn and c in rn and rn[a][2] == layer and rn[c][2] == layer and layer in geoms]
    tr += M3.pad_traces(rn, ex["padstacks"], layer)
    img = H.raster_image(geoms[layer], tr, x0, y0, nx, ny, s)  # (ny, nx) bool, row 0 = low y

    half = sub // 2
    # homog.cell_edges' window runs centre-to-centre; batched_gx drives current between its two end
    # faces, so an end face with no metal gives exactly I = 0 -> G = 0 regardless of the window fill.
    if step["axis"] == "x":
        win = img[:, half:half + sub]
        f_lo, f_hi = img[:, half], img[:, half + sub - 1]
    else:
        win = img[half:half + sub, :]
        f_lo, f_hi = img[half, :], img[half + sub - 1, :]
    rows = ["".join("#" if v else "." for v in img[r]) for r in range(ny - 1, -1, -1)]
    return dict(axis=step["axis"], frm=step["frm"], to=step["to"], sub_um=s, sub=sub,
                box_um=[x0, y0, x0 + nx * s, y0 + ny * s], img_shape=[int(ny), int(nx)],
                img_fill=float(img.mean()), window_fill=float(win.mean()),
                face_fill_low_cell=float(f_lo.mean()), face_fill_high_cell=float(f_hi.mean()),
                low_cell=[int(j0), int(i0)], high_cell=[int(jN - 1), int(iN - 1)],
                window_shape=[int(win.shape[0]), int(win.shape[1])],
                metal_rows_y_um=[float(y0 + (r + 0.5) * s) for r in np.flatnonzero(img.any(axis=1))],
                metal_cols_x_um=[float(x0 + (c + 0.5) * s) for c in np.flatnonzero(img.any(axis=0))],
                ascii_top_down=rows)


def walk_cells(sh, na, nb):
    """March cell by cell from na's cell to nb's cell and report where the model has no edge."""
    A, B = _block_of(sh, na["snap_cell"]), _block_of(sh, nb["snap_cell"])
    w = dict(from_node=na["node"], to_node=nb["node"], from_cell=na["snap_cell"], to_cell=nb["snap_cell"],
             from_patch=na["patch"], to_patch=nb["patch"], steps=[], first_break=None)
    if A is None or B is None or A[0] != B[0]:
        w["note"] = "endpoints are in different raster blocks; no in-block walk"
        return w
    bi, j, i = A
    _, jb, ib = B
    b = sh.blocks[bi]
    img_fill, ids, mask = b["fill"], b["ids"], b["mask"]
    Gx, Gy = b["Gx"], b["Gy"]
    while (j, i) != (jb, ib):
        dj, di = jb - j, ib - i
        if abs(di) >= abs(dj):
            i2, j2 = i + (1 if di > 0 else -1), j
            G = float(Gx[j, min(i, i2)])
            axis = "x"
        else:
            j2, i2 = j + (1 if dj > 0 else -1), i
            G = float(Gy[min(j, j2), i])
            axis = "y"
        s = dict(axis=axis, block_index=bi, frm=[j, i], to=[j2, i2],
                 from_cell=(int(ids[j, i]) if mask[j, i] else None),
                 to_cell=(int(ids[j2, i2]) if mask[j2, i2] else None),
                 from_fill=float(img_fill[j, i]), to_fill=float(img_fill[j2, i2]),
                 from_metal=bool(mask[j, i]), to_metal=bool(mask[j2, i2]), G=G)
        s["edge_in_model"] = bool(s["from_metal"] and s["to_metal"] and G > 1e-6)
        s["why"] = ("ok" if s["edge_in_model"] else
                    "no metal in one cell (fill = 0: rasterisation gap)" if not (s["from_metal"] and s["to_metal"]) else
                    "G <= 1e-6 threshold" if G <= 1e-6 else "?")
        w["steps"].append(s)
        if w["first_break"] is None and not s["edge_in_model"]:
            w["first_break"] = s
        j, i = j2, i2
    w["n_steps"] = len(w["steps"])
    w["n_breaks"] = sum(not s["edge_in_model"] for s in w["steps"])
    return w


def raster_markdown(o):
    L = ["", f"## EXP-27 raster probe -- {o['port']} / `{o['layer']}` around "
         f"({o['x_um']:.0f}, {o['y_um']:.0f}) um, R = {o['radius_um']:.0f} um", "",
         f"- sheet: {o['sheet_cells']} cells, {o['sheet_edges']} edges, "
         f"**{o['sheet_patches']} edge-connected patches** (largest: "
         + ", ".join(f"#{k} x{v}" for k, v in list(o["patch_sizes"].items())[:8]) + ")",
         "- blocks: " + ", ".join(f"{b['kind']} h={b['h_um']:g} um {b['nx']}x{b['ny']}" for b in o["blocks"]),
         "", "### rail nodes of this layer inside R", "",
         "| node | xy (um) | dist (um) | snapped cell | patch |", "|---|---|---|---|---|"]
    for n in o["nodes"][:40]:
        L.append(f"| {n['node']} | ({n['x_um']:.1f}, {n['y_um']:.1f}) | {n['dist_um']:.1f} | "
                 f"{n['snap_cell'] if n['snap_cell'] is not None else 'NOT SNAPPED'} | "
                 f"{n['patch'] if n['patch'] is not None else '-'} |")
    L += ["", f"### cells within R ({len(o['cells'])}), nearest first", "",
          "| cell | block | j,i | centre (um) | h | fill | patch | edges (other:G) |",
          "|---|---|---|---|---|---|---|---|"]
    for c in o["cells"][:60]:
        L.append(f"| {c['cell']} | {c['block']} | {c['j']},{c['i']} | ({c['x_um']:.0f}, {c['y_um']:.0f}) | "
                 f"{c['h_um']:g} | {c['fill']:.3f} | {c['patch']} | "
                 + (", ".join(f"{e['other']}:{e['G']:.4f}" for e in c["edges"]) or "**none**") + " |")
    if len(o["cells"]) > 60:
        L.append(f"| ... | ({len(o['cells'])} cells within R) | | | | | | |")
    for w in o["walks"]:
        L += ["", f"### walk {w['from_node']} (cell {w['from_cell']}, patch {w['from_patch']}) -> "
              f"{w['to_node']} (cell {w['to_cell']}, patch {w['to_patch']})", ""]
        if w.get("note"):
            L.append(w["note"])
            continue
        L += [f"- {w['n_steps']} cell steps, {w['n_breaks']} of them without an edge in the model", "",
              "| step | axis | j,i -> j,i | from cell/fill | to cell/fill | G | edge? | why |",
              "|---|---|---|---|---|---|---|---|"]
        for k, s in enumerate(w["steps"]):
            L.append(f"| {k} | {s['axis']} | {s['frm'][0]},{s['frm'][1]} -> {s['to'][0]},{s['to'][1]} | "
                     f"{s['from_cell']}/{s['from_fill']:.3f} | {s['to_cell']}/{s['to_fill']:.3f} | "
                     f"{s['G']:.6f} | {'yes' if s['edge_in_model'] else '**NO**'} | {s['why']} |")
    return "\n".join(L)


# ---------------------------------------------------------------- one port
def run_one(port):
    import run11 as R11  # heavy import: worker process only

    t0 = time.time()
    ex, mdl = R11.setup(TAG, port, VARIANT)
    t_build = time.time() - t0
    ncomp, lab = element_components(mdl)

    # model3.build keeps exactly the decaps whose rail_node is a known rail node, in order (as EXP-26)
    meta = [dc for dc in ex["decaps"] if dc["rail_node"] in ex["rail_nodes"]]
    assert len(meta) == len(mdl.dec), (len(meta), len(mdl.dec))
    assert all(m["model_id"] == d[2] for m, d in zip(meta, mdl.dec))
    zc = {mid: ex["models"][mid].impedance([F_C, F_ESR]) for mid in {d[2] for d in mdl.dec}}

    decs, iso = [], []
    for k, ((ra, _ga, mid), m) in enumerate(zip(mdl.dec, meta)):
        i = int(mdl.map([ra])[0])
        z0, z1 = complex(zc[mid][0]), complex(zc[mid][1])
        C = -1.0 / (2 * np.pi * F_C * z0.imag) if z0.imag < 0 else float("nan")
        x, y, lay, ps = ex["rail_nodes"][m["rail_node"]]
        d = dict(k=k, refdes=m["refdes"], part=m["part"], model_id=mid, rail_node=m["rail_node"],
                 matrix_index=i, component=(int(lab[i]) if i >= 0 else -1),
                 isolated=bool(i < 0 or lab[i] != lab[mdl.P]), pruned=bool(i < 0),
                 C_F=float(C), ESR_mOhm=z1.real * 1e3, layer=lay, x_um=x, y_um=y, padstack=ps)
        if d["isolated"]:
            d["trace"] = trace_node(ex, mdl, m["rail_node"])
            iso.append(d)
        decs.append(d)

    Ct = float(np.nansum([d["C_F"] for d in decs]))
    Ci = float(np.nansum([d["C_F"] for d in iso]))
    out = dict(port=port, tag=TAG, variant=VARIANT, unknowns=int(mdl.N), port_index=int(mdl.P),
               n_components=int(ncomp), port_component=int(lab[mdl.P]),
               n_decaps=len(decs), n_isolated=len(iso),
               C_total_F=Ct, C_isolated_F=Ci, c_iso=(Ci / Ct if Ct else 0.0),
               isolated=[dict(refdes=d["refdes"], model_id=d["model_id"], C_F=d["C_F"], ESR_mOhm=d["ESR_mOhm"],
                              rail_node=d["rail_node"], layer=d["layer"], xy_um=[d["x_um"], d["y_um"]],
                              component=d["component"], h_mech_class=d["trace"]["h_mech_class"],
                              trace=d["trace"]) for d in iso],
               decaps=[{k: v for k, v in d.items() if k != "trace"} for d in decs],
               rail_nodes_snapped=mdl.info.get("rail_nodes_snapped"), rail_nodes=len(ex["rail_nodes"]),
               decaps_connected_info=mdl.info.get("decaps_connected"),
               build_seconds=t_build, wall_seconds=time.time() - t0, peak_rss_MB=peak_rss_mb())
    print(f"[exp27] {port} N={mdl.N} comps={ncomp} decaps={len(decs)} isolated={len(iso)} "
          f"c_iso={out['c_iso']:.4f} "
          + ", ".join(f"{d['refdes']}({d['trace']['h_mech_class']})" for d in iso)
          + f" build {t_build:.0f}s", flush=True)
    return out


# ---------------------------------------------------------------- aggregate
def receipt_ratio(port):
    """|dRe_100k| / Re Zref(100 kHz) from the exp15 variant-j receipt (dimensionless)."""
    fn = os.path.join(work_dir("exp15"), f"result_{TAG}_{port}_any_{VARIANT}.json")
    if not os.path.exists(fn):
        return None
    d = json.load(open(fn, encoding="utf-8"))
    k = int(np.argmin(np.abs(np.array(d["freq"]) - F_ESR)))
    zr = d["Zref_re"][k] * 1e3
    return dict(dRe_100k_mOhm=d["dRe_100k_mOhm"], ReZref_100k_mOhm=zr, f=d["freq"][k],
                ratio=(abs(d["dRe_100k_mOhm"]) / zr if zr else None))


def aggregate(res):
    iso_ports = [r for r in res if r["n_isolated"] >= 1]
    v = {"H_freq": dict(n_ports=len(res), n_ports_with_isolated=len(iso_ports), threshold=H_FREQ_MIN,
                        adopt=bool(len(iso_ports) >= H_FREQ_MIN),
                        fraction=(len(iso_ports) / len(res) if res else 0.0),
                        ports=[r["port"] for r in iso_ports])}

    for r in res:
        r["receipt"] = receipt_ratio(r["port"])
    a = [r["receipt"]["ratio"] for r in res if r["c_iso"] > 0 and r["receipt"] and r["receipt"]["ratio"] is not None]
    b = [r["receipt"]["ratio"] for r in res if r["c_iso"] == 0 and r["receipt"] and r["receipt"]["ratio"] is not None]
    hc = dict(n_c_iso_pos=len(a), n_c_iso_zero=len(b),
              median_c_iso_pos=(float(np.median(a)) if a else None),
              median_c_iso_zero=(float(np.median(b)) if b else None))
    if a and b:
        u, p = mannwhitneyu(a, b, alternative="greater")
        hc.update(U=float(u), p=float(p), adopt=bool(p < 0.05))
    else:
        hc.update(U=None, p=None, adopt=False, note="one group empty")
    v["H_corr"] = hc

    hist = {}
    for r in res:
        for d in r["isolated"]:
            hist[d["h_mech_class"]] = hist.get(d["h_mech_class"], 0) + 1
    p65 = next((d for r in res if r["port"] == "Port65_SITE1"
                for d in r["isolated"] if d["refdes"] == "C10105_1"), None)
    v["H_mech"] = dict(class_histogram=hist, n_isolated_total=sum(r["n_isolated"] for r in res),
                       P65_C10105_1=p65, P65_C10105_1_class=(p65 or {}).get("h_mech_class"))
    return v


def markdown(res, v):
    res = sorted(res, key=lambda r: r["port"])
    top = sorted(res, key=lambda r: -r["c_iso"])[:10]
    ci = np.array([r["c_iso"] for r in res], float)
    fmt = lambda x: "n/a" if x is None else f"{x:.4g}"  # noqa: E731
    L = ["", f"## EXP-27 -- model connectivity audit (tag {TAG}, variant {VARIANT}, {len(res)} ports)", "",
         "### Verdicts (plan section 2)", ""]
    h = v["H_freq"]
    L.append(f"- **H_freq: {'ADOPT' if h['adopt'] else 'REJECT'}** -- {h['n_ports_with_isolated']}/{h['n_ports']} "
             f"ports ({h['fraction']*100:.1f} %) have >= 1 isolated decap (threshold >= {h['threshold']}).")
    h = v["H_corr"]
    L.append(f"- **H_corr: {'ADOPT' if h['adopt'] else 'REJECT'}** -- median |dRe_100k|/ReZref: c_iso>0 "
             f"{fmt(h['median_c_iso_pos'])} (n={h['n_c_iso_pos']}) vs c_iso=0 {fmt(h['median_c_iso_zero'])} "
             f"(n={h['n_c_iso_zero']}); Mann-Whitney U={fmt(h['U'])} p={fmt(h['p'])} (alt=greater, p<0.05).")
    h = v["H_mech"]
    L.append(f"- **H_mech (Port65_SITE1 / C10105_1): {h['P65_C10105_1_class'] or 'not isolated / not found'}** -- "
             f"class histogram over all {h['n_isolated_total']} isolated decaps: "
             + (", ".join(f"{k} {n}" for k, n in sorted(h["class_histogram"].items(), key=lambda x: -x[1])) or "none"))

    L += ["", "### c_iso distribution", "",
          f"- ports with >= 1 isolated decap: {v['H_freq']['n_ports_with_isolated']}/{len(res)}; "
          f"isolated decaps total {h['n_isolated_total']}",
          f"- c_iso: mean {ci.mean():.4f}, median {np.median(ci):.4f}, max {ci.max():.4f}; "
          f"p75 {np.quantile(ci, .75):.4f}, p90 {np.quantile(ci, .90):.4f}, p99 {np.quantile(ci, .99):.4f}",
          "- bins: " + ", ".join(f"{lo:g}-{hi:g}: {int(((ci > lo) & (ci <= hi)).sum())}"
                                 for lo, hi in ((-1e-9, 0.0), (0.0, .01), (.01, .05), (.05, .1),
                                                (.1, .25), (.25, .5), (.5, 1.0))),
          "", "### 10 ports with the largest c_iso", "",
          "| port | decaps | isolated | C_total (uF) | C_isolated (uF) | c_iso | dRe_100k (mOhm) | "
          "ReZref_100k (mOhm) | \\|dRe\\|/ReZref | isolated decaps (class) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in top:
        rc = r.get("receipt") or {}
        L.append(f"| {r['port']} | {r['n_decaps']} | {r['n_isolated']} | {r['C_total_F']*1e6:.2f} | "
                 f"{r['C_isolated_F']*1e6:.2f} | {r['c_iso']:.4f} | {fmt(rc.get('dRe_100k_mOhm'))} | "
                 f"{fmt(rc.get('ReZref_100k_mOhm'))} | {fmt(rc.get('ratio'))} | "
                 + (", ".join(f"{d['refdes']} [{d['h_mech_class']}]" for d in r["isolated"]) or "-") + " |")

    p65 = v["H_mech"]["P65_C10105_1"]
    L += ["", "### Port65_SITE1 / C10105_1 trace (plan section 2 H_mech)", ""]
    if p65 is None:
        L.append("C10105_1 is **not** isolated in Port65_SITE1 (or that port is missing from this run).")
    else:
        t = p65["trace"]
        L += [f"- decap {p65['refdes']} model {p65['model_id']}: C = {p65['C_F']*1e6:.3f} uF, "
              f"ESR = {p65['ESR_mOhm']:.3f} mOhm, element component {p65['component']} != port component",
              f"- rail node {t['rail_node']} on layer `{t['layer']}` at ({t['x_um']:.1f}, {t['y_um']:.1f}) um, "
              f"padstack `{t['padstack']}`",
              f"- on a plane sheet layer: {t['on_sheet_layer']}; snapped to a cell: {t['snapped']} "
              f"(cell {t['snap_cell']}, h {t['snap_h_um']} um)",
              f"- within {RADIUS:g} um: {t['n_rail_nodes_300um']} other rail nodes "
              f"({t['n_rail_nodes_300um_same_layer']} same layer), {t['n_via_endpoints_300um']} via endpoints "
              f"({t['n_via_endpoints_300um_same_layer']} same layer), {t['n_trace_endpoints_300um']} trace "
              f"endpoints ({t['n_trace_endpoints_300um_same_layer']} same layer); nearest rail node "
              f"{fmt(t['nearest_um'])} um",
              f"- the node itself is a via endpoint: {t['self_is_via_endpoint']}, "
              f"trace endpoint: {t['self_is_trace_endpoint']}",
              f"- **class: {t['h_mech_class']}**"]
        if t["neighbours"]:
            L += ["", "| neighbour node | layer | dist (um) | via ep | trace ep | padstack |",
                  "|---|---|---|---|---|---|"]
            for nb in t["neighbours"][:10]:
                L.append(f"| {nb['node']} | {nb['layer']} | {nb['dist_um']:.1f} | {nb['via_endpoint']} | "
                         f"{nb['trace_endpoint']} | {nb['padstack']} |")
    return "\n".join(L)


def worker(port, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    log_fn = os.path.join(log_dir, f"audit_{port}.log")
    t0 = time.time()
    with open(log_fn, "w", encoding="utf-8") as f:
        p = subprocess.run([sys.executable, "audit27.py", "--one", port, "--variant", VARIANT, "--tag", TAG], cwd=HERE, env=os.environ.copy(),
                           stdout=f, stderr=subprocess.STDOUT)
    return port, p.returncode == 0 and os.path.exists(cache_file(port)), p.returncode, time.time() - t0, log_fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--ports", default=None, help="comma-separated subset; default every port of --tag")
    ap.add_argument("--one", default=None, help="worker mode: build this port and write its cache")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true", help="rebuild ports that already have a cache")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--variant", default="j", help="run11 --variant used for the model build (default j; EXP-28: p)")
    ap.add_argument("--tag", default="260729", help="design id (default 260729; EXP-30: s5m6585) -- port list comes from its reference npz")
    ap.add_argument("--component-trace", default=None, metavar="PORT",
                    help="dump the whole element component of every isolated decap of PORT")
    ap.add_argument("--raster-probe", nargs=5, default=None, metavar=("PORT", "LAYER", "X", "Y", "R"),
                    help="dump the rasterised sheet LAYER of PORT within R um of (X, Y)")
    a = ap.parse_args()
    global VARIANT, TAG
    VARIANT = a.variant
    TAG = a.tag

    if a.selfcheck:
        return selfcheck()
    if a.raster_probe:
        p, L, x, y, r = a.raster_probe
        o = raster_probe(p, L, float(x), float(y), float(r))
        fn = os.path.join(work_dir("exp27"),
                          f"raster_probe_{p}_{L.replace('$', '_').replace('(', '_').replace(')', '')}.json")
        json.dump(o, open(fn, "w", encoding="utf-8"), indent=1,
                  default=lambda z: z.item() if hasattr(z, "item") else str(z))
        print(raster_markdown(o))
        print(f"\nwrote {fn}  wall {o['wall_seconds']:.0f}s")
        return 0
    if a.component_trace:
        o = component_trace(a.component_trace)
        fn = os.path.join(work_dir("exp27"), f"component_trace_{a.component_trace}.json")
        json.dump(o, open(fn, "w", encoding="utf-8"), indent=1,
                  default=lambda x: x.item() if hasattr(x, "item") else str(x))
        print(component_markdown(o))
        print(f"\nwrote {fn}  wall {o['wall_seconds']:.0f}s")
        return 0
    if a.one:
        r = run_one(a.one)
        json.dump(r, open(cache_file(a.one), "w", encoding="utf-8"), indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
        return 0

    T0 = time.time()
    ports = a.ports.split(",") if a.ports else all_ports()
    todo = [p for p in ports if a.force or not os.path.exists(cache_file(p))]
    if a.dry:
        print(f"{len(ports)} ports requested, {len(ports)-len(todo)} cached, {len(todo)} to run:")
        print("\n".join("  " + p for p in todo))
        return 0
    if todo:
        log_dir = os.path.join(work_dir("exp27"), "logs")
        n_ok = n_fail = 0
        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(worker, p, log_dir): p for p in todo}
            for fut in as_completed(futs):
                port, ok, rc, wall, log_fn = fut.result()
                n_ok += ok
                n_fail += not ok
                print(f"[{n_ok+n_fail}/{len(todo)}] {'OK  ' if ok else 'FAIL'} {port} wall {wall:.0f}s"
                      + ("" if ok else f" rc={rc} see {log_fn}"), flush=True)
        print(f"builds: {n_ok} ok, {n_fail} failed")

    res = [json.load(open(cache_file(p), encoding="utf-8")) for p in ports if os.path.exists(cache_file(p))]
    v = aggregate(res)
    out = dict(experiment="exp27", tag=TAG, variant=VARIANT, radius_um=RADIUS, f_C=F_C, f_ESR=F_ESR,
               n_ports=len(res), verdicts=v, ports=res,
               wall_seconds=time.time() - T0, peak_rss_MB=peak_rss_mb())
    name = "exp27" + ("" if VARIANT == "j" else f"_{VARIANT}") + ("" if TAG == "260729" else f"_{TAG}")
    fn = os.path.join(work_dir("exp27"), name + ".json")
    json.dump(out, open(fn, "w", encoding="utf-8"), indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(markdown(res, v))
    print(f"\nwrote {fn}  ({len(res)} ports)  total wall {time.time()-T0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
