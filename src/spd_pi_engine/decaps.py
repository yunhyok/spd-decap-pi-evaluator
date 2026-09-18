"""Decap sweep, stage 2: one multi-port basis, then a Schur closure per configuration (W9).

Plan `docs/engine/ENGINE_PLAN_2026-09-18.md` §2-4 (2단계) and §4 W9.  Ported from
`tools/research-claude/exp9/run9.py:146-183` (`multiport` / `close`), which the research tree
verified against the direct solve at Rx = 0 (EXP-8 reproduction).  The research tree is read-only,
so this is a copy, not an import.

Stage 1 (W8, `Model.set_decaps`) re-stamps the decap admittances and re-factorizes: exact, and
~2.1 s per configuration on 260729 Port18 (GPU).  Stage 2 factorizes once per frequency, with a
unit current at the port node and at every decap terminal as right-hand sides,

    Z = [[Z_PP, Z_Pd], [Z_dP, Z_dd]]        (1 + Nd) x (1 + Nd), per frequency

and every configuration afterwards is one dense solve of size (number of mounted decaps):

    Z_port = Z_PP - Z_Pd (Z_dd + diag(z_dec))^-1 Z_dP

An unmounted decap (`None`) leaves its terminal open, which is exactly the y = 0 that `assemble`
stamps for it -- its row and column are dropped from the closure instead of getting an infinite
impedance on the diagonal.  So stage 2 answers the same question as stage 1 and the two agree to
the solver's accuracy (W9_REPORT §4); the direct path stays the truth.

Cost: the basis is (1 + Nd) solves x len(freqs) with one factorization per frequency, so it pays
for itself past a few dozen configurations (W9_REPORT §5).  It is (1 + Nd)^2 complex numbers per
frequency in memory -- 421 decaps x 7 frequencies = 20 MB.

The closure is one dense LU of order (mounted decaps) per frequency: 0.1 s for Nd = 421 x 7
frequencies, and under a millisecond for the 35 of Port14.  Measured 5-20x that with the default
OpenBLAS thread count on this laptop (16 threads on a 421 x 421 matrix, unstable), so a driver
that sweeps many configurations is better off with OPENBLAS_NUM_THREADS=4 -- an environment
setting, not something a library may do to its caller (W9_REPORT §5-3).
"""
from __future__ import annotations

import json
import time

import numpy as np
from scipy.sparse.linalg import splu

from .receipt import Result, decap_config_sha256, numerics_id_of


class BasisResult(Result):
    """What `DecapBasis.Z(config)` returns: a `Result` whose Z came from the closure.

    No breakdown: that needs the node voltages, which the basis does not keep (solve the
    configuration directly for one).  `receipt()` carries `method="decap_basis"` and the closed
    configuration, and defaults to no 100 kHz breakdown for the same reason.
    """

    basis = None          # the DecapBasis this came out of (set by DecapBasis.Z)

    def breakdown(self, f, split=True):
        raise NotImplementedError(
            "decap_basis gives Z(f) only; set_decaps(config) + solve() for a breakdown")

    def receipt(self, breakdown_100k=False):
        rec = super().receipt(breakdown_100k)
        if self.basis is not None:
            rec["decap_basis"] = self.basis.receipt()
        return rec


class DecapBasis:
    """The (1 + Nd)-port impedance matrix of the rail with every decap removed, per frequency.

        basis = mdl.decap_basis(freqs)          # one factorization per frequency
        basis.Z({"C1234": None}).Z              # ms; config is partial, like set_decaps
        basis.Z_many([cfg1, cfg2, ...])
        basis.receipt(); basis.save(path); DecapBasis.load(path, mdl)

    `config` is read the way `Model.set_decaps` reads it: a partial `{refdes: model_id | None}`
    over the configuration the model had when the basis was built (`base_config`).
    """

    def __init__(self, model, freqs, Zs, refdes, ports, base_config, chunk, build_seconds,
                 unstamped=()):
        self.model = model
        self.freqs = np.asarray(freqs, float)
        self.Zs = Zs                          # (nf, 1 + Nd, 1 + Nd), port 0 = the DUT port
        self.refdes = list(refdes)            # basis column k + 1
        self.ports = [tuple(p) for p in ports]
        self.base_config = dict(base_config)
        self.chunk = int(chunk)
        self.build_seconds = float(build_seconds)
        self.unstamped = list(unstamped)      # decaps outside the solved network (never stamped)
        self._z: dict = {}                    # (model_id, frequency index) -> decap impedance

    def __repr__(self):
        return (f"DecapBasis(port={getattr(self.model, 'P', '?')}, n_decaps={len(self.refdes)}, "
                f"freqs={len(self.freqs)}, {self.Zs.nbytes / 1e6:.1f} MB)")

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, model, freqs, chunk=24):
        """`run9.multiport` on an engine `Model`: assemble with y = 0 at every decap, solve for the
        port and every decap terminal in chunks of `chunk` right-hand sides.

        The model's configuration is restored on the way out.  With a decap on an explicit GND node
        (`gnd_node >= 0`) the terminal is the node pair, as in `run9.port_vectors`; with the ideal
        reference (`-1`) only the rail node enters.  Both are handled here.
        """
        freqs = np.asarray(freqs, float)
        base = model.decap_config
        ra = model.map(np.array([d[0] for d in model.dec], np.int64))
        ga = model.map(np.array([d[1] for d in model.dec], np.int64))   # < 0 = ideal reference
        refdes, ports, unstamped = [], [(int(model.P), -1)], []
        for k, r in enumerate(model._dec_refdes):
            if ra[k] < 0 and ga[k] < 0:      # pruned away: `assemble` drops this decap's stamp too
                unstamped.append(r)
                continue
            refdes.append(r)
            ports.append((int(ra[k]), int(ga[k])))
        K, N = len(ports), int(model.N)
        Zs = np.zeros((len(freqs), K, K), complex)
        t0 = time.time()
        try:
            model.set_decaps({r: None for r in base})          # the bare board
            for i, f in enumerate(freqs):
                Y = model.assemble(float(f))
                gpu = model._gpu_solver(Y, nrhs=chunk)
                lu = None if gpu else splu(Y, permc_spec="COLAMD")
                B = np.zeros((N, chunk), complex, order="F")    # width fixed: cuDSS plans on it
                for c0 in range(0, K, chunk):
                    m = min(chunk, K - c0)
                    B[:] = 0.0
                    for j in range(m):
                        pa, pb = ports[c0 + j]
                        if pa >= 0:
                            B[pa, j] += 1.0
                        if pb >= 0:
                            B[pb, j] -= 1.0
                    V = gpu.solve(Y, B)[0] if gpu else lu.solve(B)
                    for i2, (pa, pb) in enumerate(ports):
                        Zs[i, i2, c0:c0 + m] = ((V[pa, :m] if pa >= 0 else 0.0)
                                                - (V[pb, :m] if pb >= 0 else 0.0))
                    del V
                del lu
                model.log(f"  basis f={f:11.4e} K={K} {time.time() - t0:7.1f}s")
        finally:
            model.set_decaps(base)
        return cls(model, freqs, Zs, refdes, ports, base, chunk, time.time() - t0, unstamped)

    # ------------------------------------------------------------------ closure
    def resolve(self, config) -> dict:
        """`{refdes: model_id | None}` for every decap: `base_config` updated by `config`."""
        for r, mid in (config or {}).items():
            if r not in self.base_config:
                raise KeyError(f"unknown decap refdes {r!r}")
            if mid is not None and mid not in self.model.ex["models"]:
                raise KeyError(f"unknown decap model_id {mid!r} "
                               f"(have {sorted(self.model.ex['models'])})")
        return {**self.base_config, **(config or {})}

    def _zdec(self, mid, i):
        """The decap impedance at frequency i -- the same call `assemble` makes, memoised."""
        key = (mid, i)
        if key not in self._z:
            self._z[key] = complex(self.model.ex["models"][mid].impedance([float(self.freqs[i])])[0])
        return self._z[key]

    def Z(self, config=None) -> BasisResult:
        """Close the basis with one configuration (`run9.close`) -> `BasisResult` over `freqs`."""
        t0 = time.time()
        full = self.resolve(config)
        sel = [k for k, r in enumerate(self.refdes) if full[r] is not None]
        idx = np.array([k + 1 for k in sel], np.int64)
        Z = np.empty(len(self.freqs), complex)
        for i in range(len(self.freqs)):
            M = self.Zs[i]
            if len(idx):
                A = M[np.ix_(idx, idx)] + np.diag([self._zdec(full[self.refdes[k]], i) for k in sel])
                Z[i] = M[0, 0] - M[0, idx] @ np.linalg.solve(A, M[idx, 0])
            else:
                Z[i] = M[0, 0]                                  # nothing mounted: the bare board
        dt = time.time() - t0
        stats = [dict(f=float(f), method="decap_basis", n_mounted=len(sel), closure_s=dt)
                 for f in self.freqs]
        res = BasisResult(self.model, self.freqs, Z, stats, None, dt,
                          dict(method="decap_basis", decap_config=full,
                               decap_config_sha256=decap_config_sha256(full), closure_seconds=dt))
        res.basis = self            # `receipt()` adds the basis receipt from it
        return res

    def Z_many(self, configs) -> list:
        return [self.Z(c) for c in configs]

    # ------------------------------------------------------------------ receipt / storage
    def receipt(self) -> dict:
        m = self.model
        return dict(method="decap_basis", numerics_id=numerics_id_of(m),
                    freq=[float(f) for f in self.freqs], n_decaps=len(self.refdes),
                    n_decaps_unstamped=len(self.unstamped), chunk=self.chunk, unknowns=int(m.N),
                    backend=dict(solver=m.backend.solver, fast=m.backend.fast,
                                 ir_steps=m.backend.ir_steps,
                                 device=getattr(m._cudss, "device", None) or None),
                    prune_basis=getattr(m, "prune_basis", "all_mounted"),
                    base_config_sha256=decap_config_sha256(self.base_config),
                    build_seconds=self.build_seconds, basis_MB=self.Zs.nbytes / 1e6)

    def save(self, path):
        """Store the basis as npz.  `load` needs a model again -- the decap impedances come from
        `ex["models"]`, and the receipt must name the numerics that produced the basis."""
        np.savez_compressed(
            path, Z=self.Zs, freqs=self.freqs, ports=np.asarray(self.ports, np.int64),
            meta=json.dumps(dict(refdes=self.refdes, unstamped=self.unstamped,
                                 base_config=self.base_config, chunk=self.chunk,
                                 build_seconds=self.build_seconds, unknowns=int(self.model.N),
                                 numerics_id=numerics_id_of(self.model))))
        return path

    @classmethod
    def load(cls, path, model):
        """Reload onto a model built the same way (checked: `numerics_id` and the unknown count)."""
        d = np.load(path)
        meta = json.loads(str(d["meta"].item()))
        got = (numerics_id_of(model), int(model.N))
        if got != (meta["numerics_id"], meta["unknowns"]):
            raise ValueError(f"basis {path} was built from other numerics/model "
                             f"({meta['numerics_id'][:8]}…, N={meta['unknowns']}) than this one "
                             f"({got[0][:8]}…, N={got[1]})")
        return cls(model, d["freqs"], d["Z"], meta["refdes"], d["ports"], meta["base_config"],
                   meta["chunk"], meta["build_seconds"], meta["unstamped"])


__all__ = ["BasisResult", "DecapBasis"]
