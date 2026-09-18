"""Backward elimination of decap sites against a |Z(f)| mask (APPS_PLAN_2026-09-18.md A1).

The engine gives the pieces this needs and this module adds no numerics:

    basis = mdl.decap_basis(freqs)      # W9/W12-b: one factorization per frequency, then
    basis.Z(config).Z                   #     a configuration costs a dense Schur closure
    mdl.set_decaps(cfg, replace=True); mdl.solve(freqs)   # the direct solve, which stays the truth
    mask_margin(freqs, Z, mask)         # W12-c: min(Z_target/|Z|) over freqs, same rule everywhere
    attach_mask(receipt, mask)          # W12-c: mask/mask_ratio/mask_margin/mask_pass on a receipt

A mask is `[(f_hz, Zmax_ohm), ...]` -- piecewise constant, each value holding from its own
frequency up to the next; below the first breakpoint it does not constrain.  `parse_mask` turns
the CLI's `"1e5:2e-3,1e6:1.5e-3"` into that shape; `mask_from_full` builds the A1 demo mask
(`factor` x the all-mounted |Z|).  Both are plain data -- the engine does the margin arithmetic.

`backward_eliminate` starts from every site mounted and walks down.  It does NOT re-rank the
remaining sites at every step: that is N^2/2 closures (about 88 000 for Port18's 421 sites, 1.5-2 h
at the measured 0.24 s each), well past A1's 10 minute budget.  It ranks every site once by the
margin its removal alone leaves, then walks that order and drops a site whenever the mask holds --
2N closures.  See the "한계" section of README.md.

Run `python apps/decap_search/search.py` for the self-check (no engine, no data).
"""
from __future__ import annotations

import time

import numpy as np

from spd_pi_engine import attach_mask, mask_margin


def parse_mask(spec: str) -> list:
    """`"1e5:2e-3,1e6:1.5e-3"` -> `[(f_hz, Zmax_ohm), ...]`, the shape `mask_margin`/`attach_mask`
    take.  CLI-string parsing only -- the margin math itself is the engine's (W12-c)."""
    return [(float(f), float(z))
            for f, z in (p.split(":") for p in spec.replace(" ", "").split(",") if p)]


def mask_from_full(freqs, Z, factor: float) -> list:
    """`factor` x the all-mounted |Z| as a table -- A1's demo mask."""
    return [(float(f), float(factor * abs(z))) for f, z in zip(freqs, Z)]


def backward_eliminate(basis, mask: list, freqs, log=print) -> dict:
    """Rank the sites by the margin their removal alone leaves, then drop them in that order for as
    long as the mask holds.  `basis` is a `spd_pi_engine.DecapBasis` (or anything with `refdes`,
    `freqs` and `Z(config)`); a site that stays mounted is simply skipped and the walk continues.

    Returns the search log: `rank`, `steps` (one per candidate, with the margin), `final_config`
    (partial `{refdes: None}`, ready for `set_decaps`), `removed`, `n_mounted`.
    """
    freqs = np.asarray(freqs, float)
    if not np.allclose(freqs, np.asarray(basis.freqs, float)):
        raise ValueError("freqs must be the frequencies the basis was built on")
    sites = list(basis.refdes)
    t0 = time.time()
    m0 = mask_margin(freqs, basis.Z({}).Z, mask)
    log(f"[search] {len(sites)} sites, {len(freqs)} freqs, start margin {m0:.4g}")

    rank = []
    for k, r in enumerate(sites):                       # N closures: importance of each site alone
        m = mask_margin(freqs, basis.Z({r: None}).Z, mask)
        rank.append((m, r))
        if (k + 1) % 50 == 0 or k + 1 == len(sites):
            log(f"[search]  rank {k + 1}/{len(sites)} {time.time() - t0:7.1f}s")
    rank.sort(key=lambda t: (-t[0], t[1]))              # largest margin left = least important
    t_rank = time.time() - t0

    cfg, removed, steps = {}, [], []                    # N closures: commit what still fits
    for m_solo, r in rank:
        trial = {**cfg, r: None}
        m = mask_margin(freqs, basis.Z(trial).Z, mask)
        ok = m >= 1.0
        if ok:
            cfg, _ = trial, removed.append(r)
        steps.append(dict(refdes=r, solo_margin=m_solo, margin=m, removed=ok,
                          n_mounted=len(sites) - len(removed)))
    m_end = mask_margin(freqs, basis.Z(cfg).Z, mask)
    log(f"[search] removed {len(removed)}/{len(sites)} -> {len(sites) - len(removed)} mounted, "
        f"margin {m_end:.4g}, {time.time() - t0:.1f}s")
    return dict(n_sites=len(sites), start_margin=m0,
                order=[r for _, r in rank], removed=removed,
                n_mounted=len(sites) - len(removed), final_margin=m_end,
                final_config=cfg, steps=steps, mask=[[float(f), float(z)] for f, z in mask],
                rank_seconds=t_rank, seconds=time.time() - t0, n_closures=2 * len(sites) + 2)


def validate(model, basis, configs, mask: list = None, log=print) -> list:
    """A1's contract: re-solve each configuration with `set_decaps(cfg, replace=True)` + a direct
    solve and compare it with the basis closure -- max |dZ|/|Z| and, with a mask, the verdict on
    both paths.  Both sides' records are light engine receipts (`Result.receipt(light=True)`),
    with the mask fields attached by `attach_mask` (W12-c) instead of the app inventing its own.

    `configs` is `[(name, partial_config), ...]`.  `model` must be the one `basis` was built on
    (`basis.resolve` checks refdes/model_id); `set_decaps(cfg, replace=True)` re-validates against
    the SPD's own configuration each time, so a rejected call leaves the model untouched.
    """
    freqs = np.asarray(basis.freqs, float)
    out = []
    for name, cfg in configs:
        zb = basis.Z(cfg)
        model.set_decaps(cfg, replace=True)
        t0 = time.time()
        zd = model.solve(freqs, verbose=False)
        dt = time.time() - t0
        # breakdown_100k=False: freqs includes 100 kHz and the default would re-solve for node
        # voltages on every call (APP_decap_search_REPORT §4-3 / README §9 gotcha) -- not wanted here.
        rb, rd = zb.receipt(light=True), zd.receipt(breakdown_100k=False, light=True)
        if mask is not None:
            attach_mask(rb, mask)
            attach_mask(rd, mask)
        rel = np.abs(zb.Z - zd.Z) / np.abs(zd.Z)
        row = dict(name=name, n_mounted=sum(1 for v in basis.resolve(cfg).values() if v is not None),
                   max_rel=float(rel.max()), f_at_max=float(freqs[int(rel.argmax())]),
                   direct_seconds=dt, solver=model.backend.solver,
                   basis_sha=rb["decap_config_sha256"], direct_sha=rd["decap_config_sha256"],
                   same_config=rb["decap_config_sha256"] == rd["decap_config_sha256"],
                   basis_receipt=rb, direct_receipt=rd)
        if mask is not None:
            row.update(margin_basis=rb["mask_margin"], margin_direct=rd["mask_margin"],
                       verdict_basis=rb["mask_pass"], verdict_direct=rd["mask_pass"],
                       verdict_equal=rb["mask_pass"] == rd["mask_pass"])
        log(f"[validate] {name:10s} mounted {row['n_mounted']:4d} max|dZ|/|Z| {row['max_rel']:.3e} "
            f"@ {row['f_at_max']:.4g} Hz  direct {dt:.1f}s  sha {'=' if row['same_config'] else '!'}")
        out.append(row)
    model.reset_decaps()
    return out


def demo() -> None:
    """Self-check with a lumped stand-in for the basis: no engine, no data, no SPD."""
    import types

    freqs = np.array([1e5, 1e6, 1e7])

    class FakeBasis:                                     # Z = 1 / (plane + mounted decaps)
        refdes = ["C1", "C2", "C3", "C4"]
        w = dict(C1=1.0, C2=1.0, C3=1.0, C4=8.0)

        def __init__(self):
            self.freqs = freqs

        def resolve(self, config):
            return {r: (None if r in (config or {}) else "M") for r in self.refdes}

        def Z(self, config=None):
            y = 0.5 + sum(v for r, v in self.w.items() if r not in (config or {}))
            return types.SimpleNamespace(Z=np.full(len(freqs), 1.0 / y + 0j))

    m = parse_mask("1e6:2e-3,1e7:5e-3")
    assert mask_margin([1e5], [1e-3], m) == float("inf")     # below the first breakpoint: inf
    assert mask_margin([3e6], [1e-3], m) == 2.0               # target(3e6) = 2e-3
    assert mask_margin([1e8], [1e-3], m) == 5.0               # target(1e8) = 5e-3 (last breakpoint)

    b = FakeBasis()
    mask = mask_from_full(freqs, b.Z().Z, 1.5)
    assert abs(mask_margin(freqs, b.Z().Z, mask) - 1.5) < 1e-12   # the mask is the start, x1.5
    r = backward_eliminate(b, mask, freqs, log=lambda *a: None)
    assert r["order"] == ["C1", "C2", "C3", "C4"], r["order"]     # C4 carries the rail: last
    assert r["removed"] == ["C1", "C2", "C3"] and r["n_mounted"] == 1
    assert r["final_config"] == {"C1": None, "C2": None, "C3": None}
    assert r["steps"][-1]["removed"] is False and r["steps"][-1]["margin"] < 1.0
    assert r["final_margin"] >= 1.0 and len(r["steps"]) == 4
    print("search.demo OK:", r["n_mounted"], "mounted, margin", round(r["final_margin"], 4))


if __name__ == "__main__":
    demo()
