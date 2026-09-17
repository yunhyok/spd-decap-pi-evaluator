#!/usr/bin/env python
"""EXP-11: EXP-8 cavity-wall run with the two [implementation difference] flags of the plan.

  --variant none : frozen model (must reproduce the exp8 receipt bit for bit)
             a    : eps_table=True   -- eps_r and tand from the material table at every frequency
             b    : c_all_refs=True  -- cells referenced to a non-adjacent candidate layer also get C
             ab   : both
             c    : zs_cell=True    -- EXP-12: Zs2/Zs1 chosen per cell (both endpoint cells two-sided -> Zs2)
             d    : zs_wall=True    -- EXP-12: the return plane's Zs added to the rail edge Zs

Same code path as exp8/run8.run(): traces patch "b", model3.TwoSided = TwoSidedAny, pipeline.prepare,
the LADDER + 21-point dense ladder, ModelB(h=200, fh=50, top_h=50, sub 20/10/10, fringe) and the same
receipt schema; the flags are the only change.  Output: WORK_DIR/exp11 (none/a/b/ab) or WORK_DIR/exp12 (c/d)
/result_{tag}_{port}_any_{variant}.json
(never overwritten).  --smoke solves the single reference frequency nearest 1 MHz and compares with the
committed exp8 receipt, like common/smoke_port18.py; it writes nothing.

  --ref any (default) : model3.TwoSided = TwoSidedAny (unchanged default)
       gnd            : model3.TwoSided is left as-is (exp1b.TwoSided, DGND-only search, the EXP-5 rule).
                         Receipt gets `ref_mode`; the filename variant tag gets a `_gnd` suffix
                         (result_{tag}_{port}_any_{variant}_gnd.json -- '_any_' names the port-search
                         mode and stays for glob compatibility with the ref=any receipts).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("../exp8", "../exp5", "../exp4", "../exp3", "../exp1", "../common"):
    sys.path.insert(0, os.path.join(HERE, p))
import pipeline as P5  # noqa: E402
import model3 as M3  # noqa: E402
import model as M  # noqa: E402
import run4 as R4  # noqa: E402
import run8 as R8  # noqa: E402
from paths import REPO_DIR, peak_rss_mb, work_dir  # noqa: E402

FLAGS = dict(eps_table=False, c_all_refs=False, zs_cell=False, zs_wall=False, c_unit_fix=False,
             fringe_no_thresh=False, fringe_no_cap=False, homog_L_noG=False, via_area_exact=False, via_L_twowire=False,
             zs_wall_skin=False, zs_wall_skin_re=False, via_R_skin=False, homog_face_fix=False,
             via_len_surface=False, void_fill_um=0.0)
VARIANTS = {"none": {}, "a": dict(eps_table=True), "b": dict(c_all_refs=True), "ab": dict(eps_table=True, c_all_refs=True),
            "c": dict(zs_cell=True), "d": dict(zs_wall=True),
            "j": dict(c_unit_fix=True), "jab": dict(c_unit_fix=True, eps_table=True, c_all_refs=True),
            # EXP-14: all on top of c_unit_fix (the new baseline), compared with the exp13 j receipts
            "e1": dict(c_unit_fix=True, fringe_no_thresh=True), "e2": dict(c_unit_fix=True, fringe_no_cap=True),
            "f": dict(c_unit_fix=True, homog_L_noG=True), "g": dict(c_unit_fix=True, via_area_exact=True),
            "h": dict(c_unit_fix=True, via_L_twowire=True),
            # EXP-18: return-plane skin-only term (DC removed) on top of c_unit_fix, compared with exp13/j
            "k": dict(c_unit_fix=True, zs_wall_skin=True),
            # EXP-18b: real part only of the skin-only term (skin R, no added internal L), compared with exp13/j
            "k2": dict(c_unit_fix=True, zs_wall_skin_re=True),
            # EXP-20: frequency-dependent via R (skin effect), and combined with the EXP-18b wall skin R
            "m": dict(c_unit_fix=True, via_R_skin=True),
            "mk": dict(c_unit_fix=True, via_R_skin=True, zs_wall_skin_re=True),
            # EXP-28: homogenisation source/sink on the first/last metal column, on top of c_unit_fix
            "p": dict(c_unit_fix=True, homog_face_fix=True),
            # EXP-32: rail-via length from layer centres to layer surfaces, on top of exp28/p
            "q": dict(c_unit_fix=True, homog_face_fix=True, via_len_surface=True),
            # EXP-36: PowerSI "Special Void" (voids < 1500 um filled with metal), on top of exp28/p
            "pv": dict(c_unit_fix=True, homog_face_fix=True, void_fill_um=1500.0)}
VARIANTS = {k: {**FLAGS, **v} for k, v in VARIANTS.items()}
EXP = {"c": "exp12", "d": "exp12", "j": "exp13", "jab": "exp13",
       "e1": "exp14", "e2": "exp14", "f": "exp14", "g": "exp14", "h": "exp14",
       "k": "exp18", "k2": "exp18", "m": "exp20", "mk": "exp20", "p": "exp28", "q": "exp32", "pv": "exp36"}  # EXP-12 variants write to WORK_DIR/exp12, EXP-13 to WORK_DIR/exp13, EXP-14 to WORK_DIR/exp14, EXP-18 to WORK_DIR/exp18, the EXP-11 ones to WORK_DIR/exp11


VARIANTS["pmk"] = {**FLAGS, **dict(c_unit_fix=True, homog_face_fix=True, via_R_skin=True, zs_wall_skin_re=True)}  # EXP-35 (full flag dict)
EXP["pmk"] = "exp35"

def receipt8(tag, port):
    return REPO_DIR / "docs" / "research-claude" / "2026-09-15" / "results" / "exp8" / f"result_{tag}_{port}_any.json"


def dielectric_used(ex, eps_table):
    """eps_r / tand actually used, per distinct dielectric material."""
    out = {}
    for r in ex["stackup"]:
        if r["conductivity"] is not None or str(r["material"]) in out:
            continue
        if eps_table:
            out[str(r["material"])] = {f"{f:.0e}": dict(eps_r=M.eps_tand(r, f)[0], tand=M.eps_tand(r, f)[1])
                                       for f in (1e3, 1e6, 1e7, 1e8)}
        else:
            out[str(r["material"])] = dict(eps_r=float(r["dk"] or M.eps_tand(r, 1e6)[0]), tand=M.eps_tand(r, 1e6)[1],
                                           frequency_independent=True)
    return out


def setup(tag, port, variant, ref="any"):
    R4.patch_traces("b")
    if ref == "any":
        M3.TwoSided = R8.TwoSidedAny  # model3 builds its reference search through this name
    # ref == "gnd": leave model3's own TwoSided (exp1b.TwoSided, gnd_net="DGND" default) --
    # the EXP-5 DGND-only reference search, no reassignment needed.
    ex, shapes, fine_box = P5.prepare(tag, port)
    mdl = P5.ModelB(ex, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10,
                    fringe=True, **VARIANTS[variant])
    return ex, mdl


def ref_of(tag, port):
    ref = np.load(P5.REF[tag], allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == port][0]
    return ref["freq"], ref["Zdiag"][:, col]


def vtag_of(variant, ref):
    """Filename variant tag: 'j' for ref=any, 'j_gnd' for ref=gnd (keeps '_any_' in the
    filename for glob compatibility -- it names the port-search mode, not the ref mode)."""
    return variant if ref == "any" else f"{variant}_gnd"


def out_file(tag, port, variant, outdir=None, ref="any"):
    fn = os.path.join(work_dir(outdir or EXP.get(variant, "exp11")), f"result_{tag}_{port}_any_{vtag_of(variant, ref)}.json")
    return fn if not os.path.exists(fn) else fn[:-5] + time.strftime("_%H%M%S") + ".json"


def flag_stats(mdl, variant):
    """variant c: fraction of rail edges using Zs2; variant d: fraction with a wall, median wall thickness;
    EXP-14 e1/e2/f: fringing fraction and mean weff/wid per sheet; g: plated barrels scaled;
    EXP-18 k: wall_edge_fraction (reused from d) plus wall_skin_R_sq_mOhm at 1e5/1e7/1e8 Hz."""
    out = {}
    v = VARIANTS[variant]
    if v["fringe_no_thresh"] or v["fringe_no_cap"] or v["homog_L_noG"]:
        fr, ratio = {}, {}
        for L, sh in mdl.sheets.items():
            weff, use = mdl.weff_of(sh)
            fr[L] = float(np.mean(use)) if len(use) else 0.0
            ratio[L] = float(np.mean(weff / sh.edges[3])) if len(weff) else 0.0
        out["fringe_edge_fraction"] = fr
        out["weff_over_wid_mean"] = ratio
    if v["via_area_exact"]:
        out["via_area_exact_scaled"] = mdl.info.get("via_area_exact_scaled")
        out["via_total"] = mdl.info.get("via_total")
    if VARIANTS[variant]["zs_cell"]:
        out["two_sided_edge_fraction"] = {L: float(np.mean(sh.two_edge)) if len(sh.two_edge) else 0.0
                                          for L, sh in mdl.sheets.items()}
    if v["zs_wall"] or v["zs_wall_skin"] or v["zs_wall_skin_re"]:
        out["wall_edge_fraction"] = {L: float(np.mean((sh.wall_rows_edge >= 0).any(axis=0))) if sh.wall_rows_edge.size else 0.0
                                     for L, sh in mdl.sheets.items()}
        rows = np.concatenate([sh.wall_rows_edge.ravel() for sh in mdl.sheets.values()])
        rows = rows[rows >= 0]
        out["wall_thickness_um_median"] = float(np.median(mdl.row_t[rows]) * 1e6) if len(rows) else None
    if v["zs_wall_skin"] or v["zs_wall_skin_re"]:
        out["wall_skin_R_sq_mOhm"] = {}
        for f in (1e5, 1e7, 1e8):
            vals = np.concatenate([mdl.wall_zs(sh, f, skin=True) for sh in mdl.sheets.values()])
            out["wall_skin_R_sq_mOhm"][f"{f:.0e}"] = float(np.median(vals.real)) * 1e3 if len(vals) else None
    # EXP-28: per rail sheet cells / edges / edge-connected patches (reference-excluded, sheet edges only)
    patches = {}
    for L, sh in mdl.sheets.items():
        cells = sh.cells[mdl.map(sh.cells) >= 0]          # reference/pruned cells dropped
        if not len(cells):
            patches[L] = [0, 0, 0]
            continue
        lut = np.full(int(max(cells.max(), sh.edges[0].max(initial=0), sh.edges[1].max(initial=0))) + 1, -1, np.int64)
        lut[cells] = np.arange(len(cells))
        a, b = lut[sh.edges[0]], lut[sh.edges[1]]
        ok = (a >= 0) & (b >= 0)
        g = M3.sparse.coo_matrix((np.ones(int(ok.sum())), (a[ok], b[ok])), shape=(len(cells), len(cells)))
        patches[L] = [len(cells), int(ok.sum()), int(M3.csgraph.connected_components(g, directed=False)[0])]
    out["sheet_patches"] = patches
    if v["via_len_surface"]:  # EXP-32
        r = mdl.via_len_ratio
        out["via_len_ratio"] = (dict(median=float(np.median(r)), mean=float(np.mean(r)), n=int(len(r)))
                                if len(r) else None)
        kind = mdl.via_skin[0]
        out["via_kind_counts"] = {"SOLID": int((kind == M3.VIA_SOLID).sum()), "HOLLOW": int((kind == M3.VIA_HOLLOW).sum()),
                                  "NONE": int((kind == M3.VIA_NONE).sum())}
    if v.get("void_fill_um"):  # EXP-36: voids removed per rail plane layer (sheet_patches below has cells/edges)
        out["void_fill_removed"] = mdl.info.get("void_fill_removed")
    if v["via_R_skin"]:  # EXP-20
        kind, _, _, _, Rdc = mdl.via_skin
        out["via_kind_counts"] = {"SOLID": int((kind == M3.VIA_SOLID).sum()), "HOLLOW": int((kind == M3.VIA_HOLLOW).sum()),
                                  "NONE": int((kind == M3.VIA_NONE).sum())}
        rail = kind != M3.VIA_NONE  # pad links carry no skin term
        out["via_R_ratio_median"] = {f"{f:.0e}": float(np.median(mdl.via_R(f)[rail] / Rdc[rail])) if rail.any() else None
                                     for f in (1e6, 1e7, 1e8)}
    return out


def selfcheck_via():
    """EXP-20 implementation self-check on two synthetic vias (no data files, nothing written)."""
    mdl = M3.Model3.__new__(M3.Model3)
    mdl.via_R_skin = True; mdl.via_area_exact = False
    sig = M3.COPPER_CONDUCTIVITY_S_PER_M
    kind = np.array([M3.VIA_SOLID, M3.VIA_HOLLOW], np.int8)
    D = np.array([60.0, 200.0]); ell = np.array([30.0, 300.0])
    tp = np.minimum(20.0, D / 4.0)
    area = np.where(kind == M3.VIA_SOLID, np.pi * (D / 2.0) ** 2, np.pi * D * tp) * 1e-12
    Rdc = ell * 1e-6 / (sig * area)
    mdl.via_skin = (kind, D, ell, tp, Rdc)
    r = {f: mdl.via_R(f) for f in (1e3, 1e8)}
    print(f"selfcheck SOLID  D=60um l=30um   Rdc={Rdc[0]:.6e} Ohm  R(1e3)={r[1e3][0]:.6e} "
          f"rel={abs(r[1e3][0]/Rdc[0]-1):.3e}  R(1e8)/Rdc={r[1e8][0]/Rdc[0]:.4f}")
    print(f"selfcheck HOLLOW D=200um l=300um Rdc={Rdc[1]:.6e} Ohm  R(1e3)={r[1e3][1]:.6e} "
          f"rel={abs(r[1e3][1]/Rdc[1]-1):.3e}  R(1e8)/Rdc={r[1e8][1]/Rdc[1]:.4f}")
    ok = (abs(r[1e3][0] / Rdc[0] - 1) <= 1e-6 and 2.0 <= r[1e8][0] / Rdc[0] <= 3.0
          and abs(r[1e3][1] / Rdc[1] - 1) <= 1e-6 and 1.5 <= r[1e8][1] / Rdc[1] <= 4.0)
    print("SELFCHECK", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def selfcheck_void():
    """EXP-36 implementation self-check on a synthetic layer geometry (no data files, nothing written).

    A 10 mm square of metal with one 1000 um-diameter circle, one 2000 um-diameter circle and one
    1000x1000 um square void.  void_fill_um = 1500 must drop exactly the two sub-1500 voids, keep the
    2000 um one, leave `order` indices valid (raster_image must still run) and raise the metal fraction.
    """
    geom = dict(pos_polys=[np.array([0.0, 0.0, 1e4, 0.0, 1e4, 1e4, 0.0, 1e4])],
                pos_circles=[], neg_circles=[(2000.0, 2000.0, 500.0), (6000.0, 6000.0, 1000.0)],
                neg_polys=[np.array([1000.0, 7000.0, 2000.0, 7000.0, 2000.0, 8000.0, 1000.0, 8000.0])],
                order=[("positive_polygon", 0), ("negative_circle", 0), ("negative_circle", 1), ("negative_polygon", 0)])
    g, n = M3.fill_small_voids(geom, 1500.0)
    kept = [st for st in g["order"] if st[0].startswith("negative")]
    a0 = M3.H.raster_image(geom, [], 0.0, 0.0, 500, 500, 20.0).sum()
    a1 = M3.H.raster_image(g, [], 0.0, 0.0, 500, 500, 20.0).sum()
    print(f"selfcheck removed={n} kept={kept} metal cells {a0} -> {a1} (of 250000)")
    ok = n == 2 and kept == [("negative_circle", 1)] and a1 > a0 and a1 < 250000
    print("SELFCHECK", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def baseline_file(tag, port, baseline):
    """--smoke-baseline exp8 (the frozen receipt) or exp13:j (the EXP-14 baseline, WORK_DIR/exp13)."""
    if baseline == "exp8":
        return receipt8(tag, port)
    exp, _, var = baseline.partition(":")
    return work_dir(exp) / f"result_{tag}_{port}_any_{var}.json"


def smoke(tag, port, variant, baseline="exp8", ref="any"):
    rec = json.loads(baseline_file(tag, port, baseline).read_text(encoding="utf-8"))
    k = int(np.argmin(np.abs(np.array(rec["freq"]) - 1e6)))
    z_rec = complex(rec["Z_re"][k], rec["Z_im"][k])
    ex, mdl = setup(tag, port, variant, ref)
    fref, zref = ref_of(tag, port)
    i = int(np.argmin(np.abs(fref - 1e6)))
    Z, _ = mdl.solve([fref[i]], verbose=False)
    z = complex(Z[0]); zr = complex(zref[i])
    rel = abs(z - z_rec) / abs(z_rec)
    print(f"variant={variant} ref={ref} flags={VARIANTS[variant]} f={fref[i]:.6g} Hz unknowns={mdl.N} (receipt {rec['unknowns']})")
    print(f"Z model   = {z.real*1e3:+.6f} {z.imag*1e3:+.6f}j mOhm")
    print(f"Z receipt = {z_rec.real*1e3:+.6f} {z_rec.imag*1e3:+.6f}j mOhm  ({baseline})  rel diff {rel:.2e}")
    print(f"err vs PowerSI = {100*abs(z-zr)/abs(zr):.2f} %  (receipt err_1MHz {100*rec['err_1MHz']:.2f} %)")
    print(f"plane_C_total_nF = {mdl.info['plane_C_total_nF']:.6g}")
    print("dielectric_used", json.dumps(dielectric_used(ex, VARIANTS[variant]["eps_table"])))
    for k, v in flag_stats(mdl, variant).items():
        print(k, json.dumps(v))
    print(f"peak RSS {peak_rss_mb():.0f} MB")
    if ref == "any" and (variant == "none" or baseline.endswith(":" + variant)):  # comparing against this variant's own receipt
        ok = rel <= 1e-9 and mdl.N == rec["unknowns"]
        print("SMOKE", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    print("SMOKE: not comparing against this variant/ref's own receipt -- no PASS/FAIL, informational only")
    return 0


def run(tag, port, variant, outdir=None, ref="any"):
    T0 = time.time()
    ex, mdl = setup(tag, port, variant, ref)
    fref, zref = ref_of(tag, port)
    dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
    idx = sorted({int(np.argmin(abs(fref - x))) for x in list(P5.LADDER) + list(dense)})
    freqs = fref[idx]; zr = zref[idx]
    Z, st = mdl.solve(freqs)
    rsel = [k for k, f in enumerate(freqs) if 1e5 <= f <= 1e8]
    fr_m, zm = R8.resonance(freqs[rsel], Z[rsel])
    ridx = [i for i in range(len(fref)) if 1e5 <= fref[i] <= 1e8]
    fr_r, zmr = R8.resonance(fref[ridx], zref[ridx])
    _, _, Vs = mdl.solve([1e5], verbose=False, want_v=True)
    bd = R4.split_breakdown(mdl, 1e5, Vs[0])
    k1 = int(np.argmin(abs(freqs - 1e6))); k100 = int(np.argmin(abs(freqs - 1e5)))
    out = dict(mode="cavity-wall (PowerSI convention)", variant=variant, ref_mode=ref, flags=VARIANTS[variant],
               dielectric_used=dielectric_used(ex, VARIANTS[variant]["eps_table"]),
               tag=tag, port=port, rail=ex["rail_net"], unknowns=mdl.N,
               two_sided_layers=sorted(mdl.two_sided_layers),
               reference_search={L: [{k: v for k, v in b.items() if k != "sides"} for b in r["blocks"]] for L, r in mdl.info["reference_search"].items()},
               freq=[float(x) for x in freqs], Z_re=[float(x) for x in Z.real], Z_im=[float(x) for x in Z.imag],
               Zref_re=[float(x) for x in zr.real], Zref_im=[float(x) for x in zr.imag],
               fit_dL_pH=R8.fit_dl(freqs, Z.imag - zr.imag), dL_1MHz_pH=float((Z.imag - zr.imag)[k1] / (2 * np.pi * 1e6) * 1e12),
               dRe_100k_mOhm=float((Z.real - zr.real)[k100] * 1e3), dRe_1MHz_mOhm=float((Z.real - zr.real)[k1] * 1e3),
               err_1MHz=float(abs(Z[k1] - zr[k1]) / abs(zr[k1])), f_res_model=fr_m, f_res_ref=fr_r,
               ladder_gates=R8.ladder_gates(freqs, Z, zr, fr_m, fr_r), breakdown_100k=bd, stats=st,
               plane_C_total_nF=mdl.info["plane_C_total_nF"],
               peak_rss_MB=peak_rss_mb(), wall_seconds=time.time() - T0, **flag_stats(mdl, variant))
    fn = out_file(tag, port, variant, outdir, ref)
    json.dump(out, open(fn, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("RESULT", tag, port, "fitdL", round(out["fit_dL_pH"], 1), "dL1M", round(out["dL_1MHz_pH"], 1), "dRe100k", round(out["dRe_100k_mOhm"], 3),
          "err1M", round(out["err_1MHz"], 4), "fres", round(fr_m / 1e6, 3), round(fr_r / 1e6, 3), "two-sided", out["two_sided_layers"],
          "wall", round(out["wall_seconds"]), "variant", variant, "ref", ref, "->", os.path.basename(fn), flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="260729")
    ap.add_argument("--port", default="Port18_SITE0")
    ap.add_argument("--variant", default="none", choices=list(VARIANTS))
    ap.add_argument("--selfcheck-via", action="store_true", help="EXP-20 synthetic via R(f) check, no data files")
    ap.add_argument("--selfcheck-homog", action="store_true", help="EXP-28 synthetic homog face_fix check, no data files")
    ap.add_argument("--selfcheck-void", action="store_true", help="EXP-36 synthetic void_fill_um check, no data files")
    ap.add_argument("--smoke", action="store_true", help="single frequency near 1 MHz vs the baseline receipt, no output file")
    ap.add_argument("--smoke-baseline", default="exp8",
                    help="exp8: the frozen receipt (default); or <exp>:<variant>, e.g. exp13:j "
                         "(WORK_DIR/exp13/result_{tag}_{port}_any_j.json) or exp15:j for the 92-port receipts")
    ap.add_argument("--outdir", default=None, help="write the receipt to WORK_DIR/<outdir> instead of the variant's default directory")
    ap.add_argument("--ref", default="any", choices=["any", "gnd"],
                    help="any (default): model3.TwoSided = TwoSidedAny (any net). gnd: leave model3's own "
                         "TwoSided (exp1b.TwoSided, DGND-only, the EXP-5 rule). Receipt gets ref_mode; "
                         "filename gets a _gnd suffix on the variant tag.")
    a = ap.parse_args()
    if a.selfcheck_via:
        sys.exit(selfcheck_via())
    if a.selfcheck_homog:
        sys.exit(M3.H.selfcheck_face_fix())
    if a.selfcheck_void:
        sys.exit(selfcheck_void())
    sys.exit(smoke(a.tag, a.port, a.variant, a.smoke_baseline, a.ref) if a.smoke else run(a.tag, a.port, a.variant, a.outdir, a.ref))
