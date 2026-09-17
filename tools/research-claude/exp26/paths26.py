#!/usr/bin/env python
"""EXP-26: per-decap current split and port->decap path R inside the exp13/j model.

Diagnostic only -- no model file is touched.  The model is built exactly like run11 variant j
(cavity-wall reference, c_unit_fix=True) via run11.setup(), then at f = 100 kHz:

  (a) normal solve (1 A into the port node, reference = 0 V): per-decap branch current I_d,
      current share |I_d|/sum|I_d|, and the "remainder" sum |I_d|^2 Re Z_d which is the part of
      Re Z_port that the exp15 receipt breakdown (planes+vias+traces) does not account for.
  (b) decaps removed from Y (Y_nodecap = Y - Y_dec), then one two-terminal solve per decap
      (+1 A at the port node, -1 A at the decap rail node): R_path,d = Re(V[P] - V[i_d]).
      mdl.breakdown / run4.split_breakdown on the worst decap's solution splits that path R
      into planes / vias / traces / pad links.

Y_nodecap is non-singular because every rail plane cell carries a shunt C (+G) to the reference;
if a factorisation still fails, a 1e-9 S shunt is added at the port node and flagged in the JSON.

Output: WORK_DIR/exp26/exp26.json  + a printed markdown summary.  Nothing else is written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph
from scipy.sparse.linalg import splu

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("../exp11", "../exp4", "../common"):
    sys.path.insert(0, os.path.join(HERE, p))
import run11 as R11  # noqa: E402
import run4 as R4  # noqa: E402
from paths import peak_rss_mb, work_dir  # noqa: E402

F = 1e5
TAG = "260729"
PORTS = ["Port19_SITE0", "Port65_SITE1", "Port29_SITE0", "Port75_SITE1"]
VARIANT = "j"
ELEMS = ("planes", "rail_vias", "rail_traces", "rail_pad_links")


def receipt(tag, port):
    """exp15 receipt: Re Z at the ladder point nearest 100 kHz and the breakdown sum (planes+vias+traces)."""
    fn = work_dir("exp15") / f"result_{tag}_{port}_any_{VARIANT}.json"
    d = json.load(open(fn, encoding="utf-8"))
    k = int(np.argmin(np.abs(np.array(d["freq"]) - F)))
    bd = d["breakdown_100k"]
    s = sum(v.get("R_mOhm", v.get("mOhm", 0.0)) for v in bd.values())
    return dict(file=os.path.basename(str(fn)), f=float(d["freq"][k]), ReZ_mOhm=d["Z_re"][k] * 1e3,
                breakdown_sum_mOhm=s, remainder_mOhm=d["Z_re"][k] * 1e3 - s, breakdown=bd)


def dec_table(mdl, ex):
    """Per decap of mdl.dec: matrix index of the rail node, of the gnd node (-1 = reference), Z_d, model id."""
    zc = {mid: complex(ex["models"][mid].impedance([F])[0]) for mid in {d[2] for d in mdl.dec}}
    # model3.build keeps exactly the decaps whose rail_node is a known rail node, in order
    meta = [dc for dc in ex["decaps"] if dc["rail_node"] in ex["rail_nodes"]]
    assert len(meta) == len(mdl.dec), (len(meta), len(mdl.dec))
    assert all(m["model_id"] == d[2] for m, d in zip(meta, mdl.dec))
    out = []
    for k, (ra, ga, mid) in enumerate(mdl.dec):
        m = meta[k]
        out.append(dict(k=k, i=int(mdl.map([ra])[0]), g=int(mdl.map([ga])[0]), Z=zc[mid], model_id=mid,
                        raw_rail=int(ra), refdes=m.get("refdes"), part=m.get("part")))
    return out


def y_dec(mdl, dec, N):
    """The sparse decap stamp that assemble() adds (entries to the reference are dropped, as there)."""
    r, c, v = [], [], []
    for d in dec:
        y = 1.0 / d["Z"]
        for a in (d["i"], d["g"]):
            if a >= 0:
                r.append(a); c.append(a); v.append(y)
        if d["i"] >= 0 and d["g"] >= 0:
            r += [d["i"], d["g"]]; c += [d["g"], d["i"]]; v += [-y, -y]
    if not r:
        return sparse.csc_matrix((N, N), dtype=complex)
    return sparse.coo_matrix((v, (r, c)), shape=(N, N)).tocsc()


def elem_split(mdl, f, V):
    """planes / vias / traces / pad-links R (mOhm) of one solution, from mdl.breakdown."""
    bd = mdl.breakdown(f, V)
    out = {"planes": sum(v["mOhm"] for k, v in bd.items() if k.startswith("plane_R:"))}
    for k in ("rail_vias", "rail_traces", "rail_pad_links"):
        out[k] = bd.get(k, {}).get("mOhm", 0.0)
    return out, bd


def run_port(port, tag=TAG):
    t0 = time.time()
    ex, mdl = R11.setup(tag, port, VARIANT)
    t_build = time.time() - t0
    N, P = mdl.N, mdl.P
    dec = dec_table(mdl, ex)
    conn = [d for d in dec if d["i"] >= 0]

    # ---- (a) normal solve --------------------------------------------------
    t1 = time.time()
    Y = mdl.assemble(F)
    lu = splu(Y, permc_spec="COLAMD")
    rhs = np.zeros(N, complex); rhs[P] = 1.0
    V = lu.solve(rhs)
    del lu
    Zport = complex(V[P])
    for d in conn:
        vg = 0.0 if d["g"] < 0 else V[d["g"]]
        d["I"] = complex(V[d["i"]] - vg) / d["Z"]
    tot = sum(abs(d["I"]) for d in conn)
    for d in conn:
        d["share"] = abs(d["I"]) / tot if tot else 0.0
    remainder = float(sum(abs(d["I"]) ** 2 * d["Z"].real for d in conn)) * 1e3
    t_a = time.time() - t1

    # ---- (b) path R with every decap removed -------------------------------
    t1 = time.time()
    Ynd = (Y - y_dec(mdl, dec, N)).tocsc()
    # Galvanic reachability: with every decap stamp removed, is the decap rail node still in the port's
    # connected component of the series-branch graph?  If not it hangs on a dead-end stub whose only tie
    # to the reference is the plane-cell shunt C, so it draws ~0 A and its "R_path" is a leakage artefact.
    A = Ynd.tocsr(); A.eliminate_zeros()
    A = A - sparse.diags(A.diagonal())
    A.eliminate_zeros()
    ncomp, lab = csgraph.connected_components(A, directed=False)
    for d in conn:
        sl = slice(A.indptr[d["i"]], A.indptr[d["i"] + 1])
        d["offdiag_nnz_nodecap"] = int(len(A.indices[sl]))
        d["isolated"] = bool(lab[d["i"]] != lab[P])
    del A
    shunt = False
    try:
        lund = splu(Ynd, permc_spec="COLAMD")
    except RuntimeError:
        shunt = True
        Ynd = (Ynd + sparse.coo_matrix(([1e-9], ([P], [P])), shape=(N, N))).tocsc()
        lund = splu(Ynd, permc_spec="COLAMD")
    for d in conn:
        if d["i"] == P:
            d["R_path_mOhm"] = 0.0
            continue
        r2 = np.zeros(N, complex); r2[P] = 1.0; r2[d["i"]] = -1.0
        v2 = lund.solve(r2)
        d["R_path_mOhm"] = float((v2[P] - v2[d["i"]]).real) * 1e3
        d["_V"] = v2
    del lund
    real = [d for d in conn if not d["isolated"]] or conn  # isolated decaps have no galvanic path at all
    worst = max(real, key=lambda d: d["R_path_mOhm"])
    worst_any = max(conn, key=lambda d: d["R_path_mOhm"])
    split, bd_full = elem_split(mdl, F, worst["_V"])
    sbd = R4.split_breakdown(mdl, F, worst["_V"])
    for d in conn:
        d.pop("_V", None)
    t_b = time.time() - t1

    rp = [d["R_path_mOhm"] for d in conn]
    rpc = [d["R_path_mOhm"] for d in real]
    spread = (max(rp) / min(rp)) if rp and min(rp) > 0 else None
    spread_conn = (max(rpc) / min(rpc)) if rpc and min(rpc) > 0 else None
    rec = receipt(tag, port)
    esr = [d["Z"].real for d in conn]
    out = dict(
        port=port, tag=tag, variant=VARIANT, flags=R11.VARIANTS[VARIANT], f=F,
        unknowns=N, port_index=P, decaps_total=len(mdl.dec), decaps_connected=len(conn),
        decaps_connected_info=mdl.info.get("decaps_connected"),
        decaps_gnd_to_reference_directly=mdl.info.get("decaps_gnd_to_reference_directly"),
        Z_port=[Zport.real, Zport.imag], ReZ_model_mOhm=Zport.real * 1e3,
        remainder_a_mOhm=remainder,
        path_R_total_mOhm=Zport.real * 1e3 - remainder,
        receipt=rec,
        path_R_total_vs_receipt_breakdown_mOhm=[Zport.real * 1e3 - remainder, rec["breakdown_sum_mOhm"]],
        remainder_a_vs_receipt_remainder_mOhm=[remainder, rec["remainder_mOhm"]],
        distortion_metric=remainder / (float(np.mean(esr)) * 1e3 / len(conn)) if conn else None,
        top1_share=max((d["share"] for d in conn), default=0.0),
        spread=spread, spread_non_isolated=spread_conn,
        decaps_isolated=[d["refdes"] for d in conn if d["isolated"]],
        worst_decap=dict(refdes=worst["refdes"], model_id=worst["model_id"], R_path_mOhm=worst["R_path_mOhm"],
                         isolated=worst["isolated"],
                         element_split_mOhm=split, dominant_element=max(ELEMS, key=lambda k: split[k]),
                         split_breakdown=sbd, breakdown=bd_full),
        worst_decap_any=dict(refdes=worst_any["refdes"], R_path_mOhm=worst_any["R_path_mOhm"],
                             isolated=worst_any["isolated"]),
        decaps=[dict(refdes=d["refdes"], part=d["part"], model_id=d["model_id"], matrix_index=d["i"],
                     raw_rail_node=d["raw_rail"], Z_re=d["Z"].real, Z_im=d["Z"].imag, ESR_mOhm=d["Z"].real * 1e3,
                     I_abs=abs(d["I"]), I_re=d["I"].real, I_im=d["I"].imag, share=d["share"],
                     R_path_mOhm=d["R_path_mOhm"], offdiag_nnz_nodecap=d["offdiag_nnz_nodecap"],
                     isolated=d["isolated"]) for d in conn],
        tiny_shunt_added=shunt,
        build_seconds=t_build, solve_a_seconds=t_a, solve_b_seconds=t_b,
        wall_seconds=time.time() - t0, peak_rss_MB=peak_rss_mb())
    print(f"[exp26] {port} N={N} decaps={len(conn)}/{len(mdl.dec)} ReZ={out['ReZ_model_mOhm']:.3f} "
          f"rem(a)={remainder:.3f} (receipt {rec['remainder_mOhm']:.3f}) pathR={out['path_R_total_mOhm']:.3f} "
          f"(receipt bd {rec['breakdown_sum_mOhm']:.3f}) top1={out['top1_share']:.3f} "
          f"spread={spread if spread is None else round(spread, 3)} "
          f"spread_ni={spread_conn if spread_conn is None else round(spread_conn, 3)} "
          f"isolated={out['decaps_isolated']} worst={worst['refdes']} "
          f"dom={out['worst_decap']['dominant_element']} build {t_build:.0f}s a {t_a:.0f}s b {t_b:.0f}s", flush=True)
    return out


def verdicts(res):
    g = {r["port"]: r for r in res}
    v = {}
    p65, p19 = g.get("Port65_SITE1"), g.get("Port19_SITE0")
    if p65 and p19:
        rem = p65["remainder_a_mOhm"]
        c = dict(p65_remainder_ge_18=bool(rem >= 18.0),
                 p65_remainder_within_20pct_of_20p3=bool(abs(rem - 20.3) / 20.3 <= 0.20),
                 p65_top1_ge_0p6=bool(p65["top1_share"] >= 0.6),
                 p19_top1_le_0p4=bool(p19["top1_share"] <= 0.4))
        v["H_split"] = dict(adopt=all(c.values()), checks=c, p65_remainder_mOhm=rem,
                            p65_top1=p65["top1_share"], p19_top1=p19["top1_share"])
        c = dict(p65_spread_ge_3=bool((p65["spread"] or 0) >= 3.0), p19_spread_le_2=bool((p19["spread"] or 9e9) <= 2.0))
        v["H_spread"] = dict(adopt=all(c.values()), checks=c, p65_spread=p65["spread"], p19_spread=p19["spread"],
                             p65_spread_non_isolated=p65["spread_non_isolated"],
                             p19_spread_non_isolated=p19["spread_non_isolated"],
                             note="spread is dominated by decaps flagged isolated (no galvanic path); "
                                  "spread_non_isolated excludes them",
                             generality={p: dict(spread=r["spread"], spread_non_isolated=r["spread_non_isolated"],
                                                 isolated=r["decaps_isolated"])
                                         for p, r in g.items()})
        v["isolated_decaps"] = {p: r["decaps_isolated"] for p, r in g.items()}
    v["H_elem"] = {p: dict(dominant=r["worst_decap"]["dominant_element"],
                           split_mOhm=r["worst_decap"]["element_split_mOhm"],
                           worst_decap=r["worst_decap"]["refdes"], R_path_mOhm=r["worst_decap"]["R_path_mOhm"])
                   for p, r in g.items() if p in ("Port65_SITE1", "Port75_SITE1")}
    return v


def markdown(res, v):
    L = ["", "## EXP-26 -- per-decap current split and path R (tag 260729, variant j, f = 100 kHz)", "",
         "| port | N | decaps | Re Z model (mOhm) | remainder (a) | receipt remainder | path R total | "
         "receipt bd sum | top-1 share | spread | spread (non-isolated) | isolated decaps |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    fmt = lambda x: "n/a" if x is None else f"{x:.2f}"  # noqa: E731
    for r in res:
        L.append(f"| {r['port']} | {r['unknowns']} | {r['decaps_connected']} | {r['ReZ_model_mOhm']:.3f} | "
                 f"{r['remainder_a_mOhm']:.3f} | {r['receipt']['remainder_mOhm']:.3f} | "
                 f"{r['path_R_total_mOhm']:.3f} | {r['receipt']['breakdown_sum_mOhm']:.3f} | "
                 f"{r['top1_share']:.3f} | {fmt(r['spread'])} | {fmt(r['spread_non_isolated'])} | "
                 f"{', '.join(r['decaps_isolated']) or '-'} |")
    for r in res:
        L += ["", f"### {r['port']} decaps", "",
              "| refdes | model_id | Re Z_d ESR (mOhm) | abs(I_d) (A) | share | R_path (mOhm) | off-diag nnz | isolated |",
              "|---|---|---|---|---|---|---|---|"]
        for d in sorted(r["decaps"], key=lambda x: -x["share"]):
            L.append(f"| {d['refdes']} | {d['model_id']} | {d['ESR_mOhm']:.3f} | {d['I_abs']:.4e} | "
                     f"{d['share']:.3f} | {d['R_path_mOhm']:.3f} | {d['offdiag_nnz_nodecap']} | "
                     f"{'YES' if d['isolated'] else 'no'} |")
        w = r["worst_decap"]
        L.append("")
        L.append(f"worst non-isolated path decap **{w['refdes']}** R_path = {w['R_path_mOhm']:.3f} mOhm; split "
                 + ", ".join(f"{k} {w['element_split_mOhm'][k]:.3f}" for k in ELEMS)
                 + f" -> dominant **{w['dominant_element']}**")
    L += ["", "### Verdicts (plan section 2)", ""]
    for h in ("H_split", "H_spread"):
        if h in v:
            L.append(f"- **{h}: {'ADOPT' if v[h]['adopt'] else 'REJECT'}** -- "
                     + ", ".join(f"{k}={'ok' if val else 'FAIL'}" for k, val in v[h]["checks"].items()))
    if "H_spread" in v:
        for p, gg in v["H_spread"]["generality"].items():
            L.append(f"  - {p}: spread {fmt(gg['spread'])}, non-isolated {fmt(gg['spread_non_isolated'])}, "
                     f"isolated {', '.join(gg['isolated']) or '-'}")
    for p, d in v.get("H_elem", {}).items():
        L.append(f"- **H_elem {p}**: worst non-isolated decap {d['worst_decap']} "
                 f"(R_path {d['R_path_mOhm']:.3f} mOhm) -> dominant element **{d['dominant']}**")
    iso = v.get("isolated_decaps", {})
    if any(iso.values()):
        L += ["", "**Isolated decaps** (with every decap stamp removed the rail node is no longer in the port's "
                  "connected component: no galvanic path to the port, carries ~0 A, and its R_path is a "
                  "plane-C leakage artefact): "
              + "; ".join(f"{p}: {', '.join(x)}" for p, x in iso.items() if x)]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", default=",".join(PORTS))
    ap.add_argument("--tag", default=TAG)
    ap.add_argument("--force", action="store_true", help="rebuild even if a per-port cache exists")
    a = ap.parse_args()
    T0 = time.time()
    res = []
    for p in a.ports.split(","):
        # per-port cache so the 4 builds can be split over several foreground runs
        cf = os.path.join(work_dir("exp26"), f"_port_{a.tag}_{p}.json")
        if os.path.exists(cf) and not a.force:
            res.append(json.load(open(cf, encoding="utf-8")))
            print(f"[exp26] {p} loaded from cache {os.path.basename(cf)}", flush=True)
            continue
        r = run_port(p, a.tag)
        json.dump(r, open(cf, "w", encoding="utf-8"), indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
        res.append(r)
    v = verdicts(res)
    out = dict(experiment="exp26", tag=a.tag, variant=VARIANT, f=F, ports=res, verdicts=v,
               wall_seconds=time.time() - T0, peak_rss_MB=peak_rss_mb())
    fn = os.path.join(work_dir("exp26"), "exp26.json")
    json.dump(out, open(fn, "w", encoding="utf-8"), indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(markdown(res, v))
    print(f"\nwrote {fn}  total wall {time.time()-T0:.0f}s  peak RSS {peak_rss_mb():.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
