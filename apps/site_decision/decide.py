"""A2 -- may a decap site stay unmounted?  Judged across a SITE0 / SITE1 pair.

Spec: `docs/engine/APPS_PLAN_2026-09-18.md` A2.  SITE0 and SITE1 are separate copies of one net
(`ADC_VDD_075_VTRIP_SRAM/0` vs `.../1`, `docs/engine/W10_REPORT.md` §3), so each SITE is its own
rail and its own model.  This app builds both, unmounts one site at a time on each (W8
`set_decaps` -- no rebuild), and asks whether the two SITEs still agree within delta.

    from apps.site_decision import decide
    partner = decide.find_site_pair(spd, "Port18_SITE0")        # -> "Port64_SITE1"
    out = decide.evaluate(spd, "Port18_SITE0", partner, targets=10, cache_dir=CACHE)
    out["sites"][0]["verdict"]                                  # "unmount_ok" | "keep"

Cost: 2 builds + 2 x (1 + len(targets)) solves.  Each SITE runs in its own process because
cuDSS 0.8 allows one `DirectSolver` per process (W8 §4, W10 §4) -- two independent GPU models,
unlike `apps.decap_search`'s single model doing basis + direct solve (W12-b).

W12-c: SITE-pair lookup, site matching and decap coordinates/capacitance are now engine API
(`spd_pi_engine.find_site_pair`/`match_sites`, `DecapSite.xy`/`.capacitance_F`) instead of this
app reaching into `rail.ex["rail_nodes"]`/`rail.ex["models"]` -- see APP_site_decision_REPORT §8.
The `"geometry"` matching rule stays app-local: the engine only ported `"refdes-suffix"` (the rule
this design needs), `"geometry"` is kept here purely as `match_report`'s cross-check diagnostic.

Self-check without SPD data: `python apps/site_decision/decide.py`.
"""
from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from spd_pi_engine import Backend, Design, FLAGS_P, ModelOptions, find_site_pair, match_sites
from spd_pi_engine.cli import LADDER, read_npz_ref

#: The D7 baseline (variant p) as W8/W10 ran it; `fine_box` is each rail's own (`Rail.build`).
OPTIONS = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0,
                       top_h=50.0, sub=(20, 10, 10), fringe=True)


# ---------------------------------------------------------------- frequencies
def ladder(ref_npz=None, port=None) -> np.ndarray:
    """The 7 `LADDER` points (`spd_pi_engine.cli.LADDER` = `exp5/pipeline.LADDER`).

    Snapped to the PowerSI reference grid when an npz is given -- the same snapping
    `tests/engine/ladder.py` does, and the same 7 frequencies W8 §4 and W10 §4 compared
    configurations on, so the all-mounted SITE deviation here is comparable with W10's 0.345 %.
    """
    f = np.array(LADDER, float)
    if ref_npz is None:
        return f
    fref = np.asarray(read_npz_ref(ref_npz, port)[0], float)
    return fref[sorted({int(np.argmin(abs(fref - x))) for x in f})]


# ---------------------------------------------------------------- SITE / site matching diagnostics
def _geometry_match(rail0, rail1) -> dict:
    """{refdes on rail0: refdes on rail1} by same `model_id` + nearest (x, y) after translating
    SITE0 by the centroid difference.  This is the fallback the engine did NOT port (W12C_REPORT
    §3, APP_site_decision_REPORT §7-3): on this design it is *wrong* -- SITE1's decap field is not
    a rigid copy of SITE0's (20 distinct per-pair offsets), so it reproduces only 35 of the 421
    refdes-suffix pairs and is not injective.  Kept only as `match_report`'s cross-check.
    """
    xy0 = {d.refdes: np.array(d.xy, float) for d in rail0.decaps}
    xy1 = {d.refdes: np.array(d.xy, float) for d in rail1.decaps}
    shift = np.mean(list(xy1.values()), 0) - np.mean(list(xy0.values()), 0)
    # ponytail: O(n*n) nearest match (421 sites = 0.2 s); a KD-tree per model_id if it ever grows.
    out = {}
    for d in rail0.decaps:
        t = xy0[d.refdes] + shift
        cand = [e.refdes for e in rail1.decaps if e.model_id == d.model_id]
        out[d.refdes] = min(cand, key=lambda r: float(np.hypot(*(xy1[r] - t))))
    return out


def match_report(rail0, rail1) -> dict:
    """Both rules measured on this pair -- the receipt's and the report's matching evidence.

    The adopted rule (`spd_pi_engine.match_sites`, `rule="refdes-suffix"`) is engine API now; the
    `"geometry"` fallback stays app-local (see `_geometry_match`).
    """
    m = match_sites(rail0, rail1)                   # {"mapping": {...}, "unmatched": [...]}
    mapping, unmatched = m["mapping"], m["unmatched"]
    g = _geometry_match(rail0, rail1)
    mid0 = {d.refdes: d.model_id for d in rail0.decaps}
    mid1 = {d.refdes: d.model_id for d in rail1.decaps}
    xy0 = {d.refdes: np.array(d.xy, float) for d in rail0.decaps}
    xy1 = {d.refdes: np.array(d.xy, float) for d in rail1.decaps}
    offsets = {tuple(np.round(xy1[q] - xy0[r], 3)) for r, q in mapping.items()}
    return dict(
        rule="refdes_site_suffix",
        n_sites_site0=len(rail0.decaps), n_sites_site1=len(rail1.decaps),
        matched=len(mapping), unmatched=unmatched,
        model_id_mismatches=[r for r, q in mapping.items() if mid0[r] != mid1[q]],
        injective=len(set(mapping.values())) == len(mapping),
        distinct_offsets=len(offsets),
        geometry_rule=dict(agrees_with_refdes=sum(1 for r in g if g[r] == mapping.get(r)),
                           injective=len(set(g.values())) == len(g)))


# ---------------------------------------------------------------- target sites
def capacitance(rail) -> dict:
    """{refdes: `DecapSite.capacitance_F`} -- effective C at 1 kHz from the decap model's own
    impedance (W12-c: was `rail.ex["models"][mid].impedance([1e3])` read by hand).  `None` for a
    site that is not capacitive at 1 kHz (see `DecapSite.capacitance_F`'s docstring)."""
    return {d.refdes: d.capacitance_F for d in rail.decaps}


def rank_sites(rail, n: int | None = None) -> list:
    """Refdes by capacitance, largest first (`None` last); ties keep `rail.decaps` order (`sorted`
    is stable)."""
    c = capacitance(rail)
    ranked = sorted((d.refdes for d in rail.decaps), key=lambda r: -(c[r] or 0.0))
    return ranked if n is None else ranked[:n]


# ---------------------------------------------------------------- mask (optional, A1 format)
def mask_limits(mask, freqs) -> np.ndarray | None:
    """Z_target(f): `mask` is one number (flat), or [(f_hi, z), ...] read as a step function."""
    if mask is None:
        return None
    if np.isscalar(mask):
        return np.full(len(freqs), float(mask))
    pts = sorted((float(a), float(b)) for a, b in mask)
    return np.array([next((z for fh, z in pts if f <= fh), pts[-1][1]) for f in freqs])


# ---------------------------------------------------------------- one SITE, one process
def _z(Z) -> dict:
    return dict(re=[float(x) for x in np.real(Z)], im=[float(x) for x in np.imag(Z)])


def _c(d) -> np.ndarray:
    return np.array(d["re"], float) + 1j * np.array(d["im"], float)


def site_curves(spd, port, targets, freqs, cache_dir, options, backend_args, max_layers=3) -> dict:
    """Build one SITE once, then solve all-mounted and one configuration per target site.

    Runs in a worker process (`_in_own_process`); everything it takes and returns is picklable.
    """
    t0 = time.time()
    rail = Design.open(spd).rail(port, cache_dir, max_layers=max_layers)
    mdl = rail.build(options, Backend(*backend_args))
    t_build = time.time() - t0
    res = mdl.solve(freqs, verbose=False)
    out = dict(port=str(port), rail_net=rail.rail_net, unknowns=int(mdl.N),
               n_decaps=len(rail.decaps), freq=[float(f) for f in freqs], Z_all=_z(res.Z),
               Z_off={}, receipt=res.receipt(breakdown_100k=False, light=True),
               prepare_seconds=rail.prepare_seconds, build_seconds=t_build,
               solve_seconds=[res.solve_seconds])
    for refdes in targets:
        r = mdl.set_decaps({refdes: None}).solve(freqs, verbose=False)
        out["Z_off"][refdes] = _z(r.Z)
        out["solve_seconds"].append(r.solve_seconds)
        mdl.reset_decaps()
    out["wall_seconds"] = time.time() - t0
    return out


def _in_own_process(fn, *args):
    """cuDSS 0.8 allows one `DirectSolver` per process (W8 §4), so each SITE gets a fresh one."""
    # ponytail: a pool of one per call -- the isolation is the point, not the pooling.
    with ProcessPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


# ---------------------------------------------------------------- the decision
def _dev(a, b) -> float:
    """max over f of ||b| - |a|| / |a| -- the |Z| deviation the A2 rule is written on."""
    return float(np.max(np.abs(np.abs(b) - np.abs(a)) / np.abs(a)))


def _dev_complex(a, b) -> float:
    """max over f of |b - a| / |a| -- what W10 §4-4 reported as 0.345 % for the all-mounted pair."""
    return float(np.max(np.abs(b - a) / np.abs(a)))


def evaluate(spd, port0, port1=None, targets=10, freqs=None, delta=0.05, backend=None,
             cache_dir=None, options=OPTIONS, mask=None, max_layers=3) -> dict:
    """The A2 decision for every target site.  Returns the `decision_receipt.json` payload.

    `targets`: refdes on SITE0, or an int N meaning "the N largest-capacitance sites".
    `delta`: the allowed SITE-to-SITE |Z| deviation (0.05 = 5 %).
    Rule (APPS_PLAN A2): a site may stay unmounted when, with it unmounted on BOTH SITEs, the two
    SITEs' |Z| still agree within `delta` -- and, when a `mask` is given, both stay under it.
    """
    port0 = str(port0)
    if port1 is None:
        port1 = find_site_pair(spd, port0)          # W12-c: None (not an exception) on no pair
        if port1 is None:
            raise ValueError(f"{port0}: no SITE pair (rail net must end in /0 or /1 with exactly "
                             f"one port on the partner net) -- see match_report")
    port1 = str(port1)
    freqs = np.asarray(ladder() if freqs is None else freqs, float)
    backend = backend or Backend(solver="auto", fast=True)
    design = Design.open(spd)
    rail0 = design.rail(port0, cache_dir, max_layers=max_layers)
    rail1 = design.rail(port1, cache_dir, max_layers=max_layers)
    pairs = match_sites(rail0, rail1)["mapping"]
    if isinstance(targets, int):
        targets = rank_sites(rail0, targets)
    targets = [str(t) for t in targets]
    unknown = [t for t in targets if t not in pairs]
    if unknown:
        raise KeyError(f"no SITE1 partner for {unknown} (see match_report)")
    cap, mid = capacitance(rail0), {d.refdes: d.model_id for d in rail0.decaps}

    args = (backend.solver, backend.fast, backend.host_nthreads, backend.ir_steps)
    t0 = time.time()
    s0 = _in_own_process(site_curves, spd, port0, targets, freqs, cache_dir, options, args,
                         max_layers)
    s1 = _in_own_process(site_curves, spd, port1, [pairs[t] for t in targets], freqs, cache_dir,
                         options, args, max_layers)
    wall = time.time() - t0

    Z0, Z1 = _c(s0["Z_all"]), _c(s1["Z_all"])
    lim = mask_limits(mask, freqs)
    rows = []
    for t in targets:
        a, b = _c(s0["Z_off"][t]), _c(s1["Z_off"][pairs[t]])
        dev = _dev(a, b)
        ok_mask = bool(lim is None or (np.all(np.abs(a) <= lim) and np.all(np.abs(b) <= lim)))
        rows.append(dict(
            refdes=t, partner=pairs[t], model_id=mid[t], capacitance_F=cap[t],
            absZ_site0=[float(x) for x in np.abs(a)], absZ_site1=[float(x) for x in np.abs(b)],
            dev_pct=100 * dev, dev_complex_pct=100 * _dev_complex(a, b),
            increase_site0_pct=100 * float(np.max(np.abs(a) / np.abs(Z0) - 1.0)),
            increase_site1_pct=100 * float(np.max(np.abs(b) / np.abs(Z1) - 1.0)),
            mask_ok=ok_mask, unmount_ok=bool(dev <= delta and ok_mask),
            verdict="unmount_ok" if (dev <= delta and ok_mask) else "keep"))

    return dict(
        app="site_decision", spec="docs/engine/APPS_PLAN_2026-09-18.md A2",
        rule=("unmount_ok = max_f ||Z_SITE1| - |Z_SITE0|| / |Z_SITE0| <= delta with the site "
              "unmounted on both SITEs (and |Z| <= mask on both, when a mask is given)"),
        spd=str(spd), ports=[port0, port1], rails=[rail0.rail_net, rail1.rail_net],
        freq=[float(f) for f in freqs], delta=float(delta), mask=mask,
        match=match_report(rail0, rail1),
        all_mounted=dict(
            absZ_site0=[float(x) for x in np.abs(Z0)], absZ_site1=[float(x) for x in np.abs(Z1)],
            dev_pct=100 * _dev(Z0, Z1), dev_complex_pct=100 * _dev_complex(Z0, Z1),
            dev_complex_per_f_pct=[100 * float(x) for x in np.abs(Z1 - Z0) / np.abs(Z0)],
            w10_reported_pct=0.345),
        sites=rows,
        timing=dict(wall_seconds=wall, builds=2, solves=2 * (1 + len(targets)),
                    site0={k: s0[k] for k in ("prepare_seconds", "build_seconds", "solve_seconds",
                                              "wall_seconds")},
                    site1={k: s1[k] for k in ("prepare_seconds", "build_seconds", "solve_seconds",
                                              "wall_seconds")}),
        unknowns=[s0["unknowns"], s1["unknowns"]],
        backend=dict(solver=backend.solver, fast=backend.fast),
        validity=s0["receipt"]["validity"],
        engine_receipts=[s0["receipt"], s1["receipt"]])


# ---------------------------------------------------------------- data-free self-check
def demo() -> None:
    """Assert the pure parts on a fake pair: matching, ranking, mask, deviation.

    Fake decaps carry `.xy`/`.capacitance_F` directly (what `Rail.decaps` -- real `DecapSite`s --
    would fill in), not a raw `ex` dict: this app no longer reaches into `rail.ex` itself (W12-c).
    """
    from types import SimpleNamespace as NS

    C = {"BIG": 1e-5, "SMALL": 1e-7}

    def rail(site, xs, mids):
        dec = [NS(refdes=f"C{i + 1}_{site}", model_id=m, xy=(x, 10.0 * int(site)),
                  capacitance_F=C[m]) for i, (x, m) in enumerate(zip(xs, mids))]
        return NS(rail_net=f"NET/{site}", decaps=dec)

    mids = ["SMALL", "BIG", "SMALL"]
    r0, r1 = rail("0", [0.0, 1.0, 2.0], mids), rail("1", [5.0, 6.0, 7.0], mids)
    m = match_sites(r0, r1)
    assert m["mapping"] == {"C1_0": "C1_1", "C2_0": "C2_1", "C3_0": "C3_1"}, m
    assert m["unmatched"] == [], m
    rep = match_report(r0, r1)
    assert rep["matched"] == 3 and rep["unmatched"] == [] and rep["injective"], rep
    assert rep["model_id_mismatches"] == [] and rep["distinct_offsets"] == 1, rep
    assert rep["geometry_rule"]["agrees_with_refdes"] == 3, rep   # a rigid copy: both rules agree
    r2 = rail("1", [7.0, 6.0, 5.0], mids)                         # mirrored copy: geometry flips
    assert match_report(r0, r2)["geometry_rule"]["agrees_with_refdes"] == 1
    assert rank_sites(r0, 2) == ["C2_0", "C1_0"], rank_sites(r0, 2)  # big first, then input order
    assert abs(capacitance(r0)["C2_0"] - 1e-5) < 1e-12
    f = np.array([1e3, 1e6])
    assert list(mask_limits(0.01, f)) == [0.01, 0.01]
    assert list(mask_limits([(1e5, 0.005), (1e8, 0.02)], f)) == [0.005, 0.02]
    a = np.array([1.0 + 0j, 2.0 + 0j])
    assert abs(_dev(a, 1.05 * a) - 0.05) < 1e-12
    assert abs(_dev_complex(a, a + 0.1j * a) - 0.1) < 1e-12
    print("site_decision.decide: demo OK")


if __name__ == "__main__":
    demo()
