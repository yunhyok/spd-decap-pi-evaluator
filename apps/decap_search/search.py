"""Backward elimination of decap sites against a |Z(f)| mask (APPS_PLAN_2026-09-18.md A1).

The engine gives the two pieces this needs and this module adds no numerics:

    basis = mdl.decap_basis(freqs)      # W9: one factorization per frequency, then
    basis.Z(config).Z                   #     a configuration costs a dense Schur closure
    mdl.set_decaps(config); mdl.solve(freqs)   # W8: the direct solve, which stays the truth

`Mask` is the target |Z(f)|: piecewise constant from a spec string, or a table (one point per
evaluated frequency, e.g. 1.5 x the all-mounted |Z|).  Both are the same object -- a table is a
piecewise-constant mask whose breakpoints are the evaluated frequencies.

`backward_eliminate` starts from every site mounted and walks down.  It does NOT re-rank the
remaining sites at every step: that is N^2/2 closures (about 88 000 for Port18's 421 sites, 1.5-2 h
at the measured 0.24 s each), well past A1's 10 minute budget.  It ranks every site once by the
margin its removal alone leaves, then walks that order and drops a site whenever the mask holds --
2N closures.
See the "한계" section of README.md.

Run `python apps/decap_search/search.py` for the self-check (no engine, no data).
"""
from __future__ import annotations

import time

import numpy as np


class Mask:
    """Target |Z(f)|: `points` = [(f_Hz, Z_target_ohm)], each value holding from its own frequency
    up to the next one.  Below the first breakpoint the mask does not constrain (inf)."""

    def __init__(self, points, source="points"):
        pts = sorted((float(f), float(z)) for f, z in points)
        if not pts:
            raise ValueError("empty mask")
        if pts[0][1] <= 0:
            raise ValueError(f"mask target must be > 0, got {pts[0][1]}")
        self.f = np.array([p[0] for p in pts])
        self.z = np.array([p[1] for p in pts])
        self.source = source

    def __repr__(self):
        return f"Mask({len(self.f)} points, {self.f[0]:.4g}-{self.f[-1]:.4g} Hz, {self.source})"

    @classmethod
    def parse(cls, spec: str) -> "Mask":
        """`"1e5:2e-3,1e6:1.5e-3"` -> piecewise-constant mask."""
        return cls([p.split(":") for p in spec.replace(" ", "").split(",") if p], f"spec {spec}")

    @classmethod
    def from_full(cls, freqs, Z, factor: float) -> "Mask":
        """`factor` x the all-mounted |Z| as a table -- A1's demo mask."""
        return cls(zip(freqs, factor * np.abs(Z)), f"all-mounted |Z| x {factor}")

    def target(self, freqs) -> np.ndarray:
        i = np.searchsorted(self.f, np.asarray(freqs, float), side="right") - 1
        return np.where(i < 0, np.inf, self.z[np.clip(i, 0, None)])

    def margin(self, freqs, Z) -> tuple:
        """`(min over f of Z_target/|Z|, the f where it is reached)`.  >= 1 means the mask holds."""
        t = self.target(freqs)
        a = np.abs(np.asarray(Z))
        r = np.where(np.isinf(t) | (a <= 0), np.inf, t / np.where(a > 0, a, 1.0))
        k = int(np.argmin(r))
        return float(r[k]), float(np.asarray(freqs, float)[k])

    def to_dict(self) -> dict:
        return dict(source=self.source, points=[[float(f), float(z)] for f, z in zip(self.f, self.z)])


def backward_eliminate(basis, mask: Mask, freqs, log=print) -> dict:
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
    m0, f0 = mask.margin(freqs, basis.Z({}).Z)
    log(f"[search] {len(sites)} sites, {len(freqs)} freqs, start margin {m0:.4g} @ {f0:.4g} Hz")

    rank = []
    for k, r in enumerate(sites):                       # N closures: importance of each site alone
        m, fb = mask.margin(freqs, basis.Z({r: None}).Z)
        rank.append((m, fb, r))
        if (k + 1) % 50 == 0 or k + 1 == len(sites):
            log(f"[search]  rank {k + 1}/{len(sites)} {time.time() - t0:7.1f}s")
    rank.sort(key=lambda t: (-t[0], t[2]))              # largest margin left = least important
    t_rank = time.time() - t0

    cfg, removed, steps = {}, [], []                    # N closures: commit what still fits
    for m_solo, _, r in rank:
        trial = {**cfg, r: None}
        m, fb = mask.margin(freqs, basis.Z(trial).Z)
        ok = m >= 1.0
        if ok:
            cfg, _ = trial, removed.append(r)
        steps.append(dict(refdes=r, solo_margin=m_solo, margin=m, f_bind=fb, removed=ok,
                          n_mounted=len(sites) - len(removed)))
    m_end, f_end = mask.margin(freqs, basis.Z(cfg).Z)
    log(f"[search] removed {len(removed)}/{len(sites)} -> {len(sites) - len(removed)} mounted, "
        f"margin {m_end:.4g} @ {f_end:.4g} Hz, {time.time() - t0:.1f}s")
    return dict(n_sites=len(sites), start_margin=m0, start_f_bind=f0,
                order=[r for _, _, r in rank], removed=removed,
                n_mounted=len(sites) - len(removed), final_margin=m_end, final_f_bind=f_end,
                final_config=cfg, steps=steps, mask=mask.to_dict(),
                rank_seconds=t_rank, seconds=time.time() - t0, n_closures=2 * len(sites) + 2)


def validate(model, basis, configs, mask: Mask = None, log=print) -> list:
    """A1's contract: re-solve each configuration with `set_decaps` + a direct solve and compare it
    with the basis closure -- max |dZ|/|Z| and, with a mask, the verdict on both paths.

    `configs` is `[(name, partial_config), ...]`.  The model must be the one the basis was built on
    (`DecapBasis.load` checks that); on the GPU it must be a *different process* from the basis
    build, because cuDSS plans one RHS width per model (W9_REPORT §2-1).
    """
    freqs = np.asarray(basis.freqs, float)
    out = []
    for name, cfg in configs:
        zb = basis.Z(cfg)
        model.reset_decaps().set_decaps(cfg)
        t0 = time.time()
        zd = model.solve(freqs, verbose=False)
        dt = time.time() - t0
        rel = np.abs(zb.Z - zd.Z) / np.abs(zd.Z)
        row = dict(name=name, n_mounted=sum(1 for v in basis.resolve(cfg).values() if v is not None),
                   max_rel=float(rel.max()), f_at_max=float(freqs[int(rel.argmax())]),
                   direct_seconds=dt, solver=model.backend.solver,
                   basis_sha=zb.receipt()["decap_config_sha256"],
                   direct_sha=zd.receipt(breakdown_100k=False)["decap_config_sha256"],
                   Z_re=[float(x) for x in zd.Z.real], Z_im=[float(x) for x in zd.Z.imag])
        row["same_config"] = row["basis_sha"] == row["direct_sha"]
        if mask is not None:
            mb, _ = mask.margin(freqs, zb.Z)
            md, fd = mask.margin(freqs, zd.Z)
            row.update(margin_basis=mb, margin_direct=md, f_bind_direct=fd,
                       verdict_basis=mb >= 1.0, verdict_direct=md >= 1.0,
                       verdict_equal=(mb >= 1.0) == (md >= 1.0))
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

    m = Mask.parse("1e6:2e-3,1e7:5e-3")
    assert np.isinf(m.target([1e5])[0]) and m.target([3e6])[0] == 2e-3 and m.target([1e8])[0] == 5e-3
    assert m.margin([1e6], [1e-3]) == (2.0, 1e6)

    b = FakeBasis()
    mask = Mask.from_full(freqs, b.Z().Z, 1.5)
    assert abs(mask.margin(freqs, b.Z().Z)[0] - 1.5) < 1e-12      # the mask is the start, x1.5
    r = backward_eliminate(b, mask, freqs, log=lambda *a: None)
    assert r["order"] == ["C1", "C2", "C3", "C4"], r["order"]     # C4 carries the rail: last
    assert r["removed"] == ["C1", "C2", "C3"] and r["n_mounted"] == 1
    assert r["final_config"] == {"C1": None, "C2": None, "C3": None}
    assert r["steps"][-1]["removed"] is False and r["steps"][-1]["margin"] < 1.0
    assert r["final_margin"] >= 1.0 and len(r["steps"]) == 4
    print("search.demo OK:", r["n_mounted"], "mounted, margin", round(r["final_margin"], 4))


if __name__ == "__main__":
    demo()
