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
setting, not something a library may do to its caller (W9_REPORT §5-3).  Note that the same
setting moves the last bits of `splu`: the W4 Port14 CPU receipt reproduces bit-for-bit at the
default thread count and by 2.7e-13 at OPENBLAS_NUM_THREADS=4 (W12A_REPORT §5), so a bit-for-bit
comparison has to fix the thread count as well.


ACCURACY CONTRACT (W12-a, measured on 260729 Port18: N = 275 218, 421 decaps, 8 basis builds)
---------------------------------------------------------------------------------------------
    CPU basis, `Backend("splu", fast=True)`   exact and reproducible: the closure equals the
                                              direct solve to round-off (W9: exactly 0.0 on
                                              Port14) and repeated builds agree bit for bit.
    GPU basis, cuDSS                          <= 1e-5 over 30 kHz - 100 MHz, NON-DETERMINISTIC.

The GPU number is not an error bar around a fixed value.  Two builds of the SAME basis differ by
up to 5.7e-7 (27 points from 100 kHz) and 1.0e-5 (7 points from 30.2 kHz), and the scatter is the
same whether the two builds share a process, share one cuDSS solver and plan, run in separate
processes, or run while another process uses the same card -- contention is not the cause.  What
varies is cuDSS's numerical factorization; the assembled Y is bit-identical (W8_REPORT §4-2).
Measured worst case over the 8 builds, against the CPU basis: 5.8e-6 at 30.2 kHz, 2.5e-6 at
100 kHz, ~3e-7 at 500 kHz, ~1e-7 at 1 MHz, ~1e-9 at 10 MHz, ~2e-11 at 100 MHz.  The same value at
one frequency moves by 20x between builds, so the per-frequency envelope is a bound, not a
correction.  It follows the conditioning of the bare board -- the basis always factorizes the rail
with every decap removed (W8_REPORT §4-2, W9_REPORT §4-2) -- so it is worst at the lowest
frequency and shrinks fast with f.

Practically: mask verdicts and configuration sweeps are safe on the GPU (the engine's own model
error is >= 0.8 %, four orders of magnitude larger, and A1 found all 8 mask verdicts identical),
but anything that needs 1e-6 or better -- a receipt compared against another receipt, a
convergence study, a regression gate -- builds the basis on the CPU:

    basis = mdl.decap_basis(freqs, backend=Backend("splu", fast=True))   # exact, deterministic

Cost of that exactness on Port18 (27 frequencies x 422 right-hand sides): 553 s total, 20.5 s per
frequency, against 275-354 s (10.2-13.1 s per frequency) on cuDSS -- 1.6-2.0x, one factorization
per frequency either way.  On Port14 (35 decaps, N = 34k) both are seconds.  The direct path
(`set_decaps` + `solve`) is unaffected: on the GPU it stays at 1e-10 against the CPU, because it
factorizes the mounted board (APP_decap_search_REPORT §4-2).
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

    def receipt(self, breakdown_100k=False, light=False):
        if self.model is None:      # closed from a basis loaded without a model (W12-c)
            rec = dict(freq=[float(f) for f in self.freq], Z_re=[float(x) for x in self.Z.real],
                       Z_im=[float(x) for x in self.Z.imag], **self._extra)
            if light:
                rec.pop("decap_config", None)
        else:
            rec = super().receipt(breakdown_100k, light)
        if self.basis is not None:
            rec["decap_basis"] = self.basis.receipt()
        return rec


class DecapBasis:
    """The (1 + Nd)-port impedance matrix of the rail with every decap removed, per frequency.

        basis = mdl.decap_basis(freqs)          # one factorization per frequency
        basis.Z({"C1234": None}).Z              # ms; config is partial, like set_decaps
        basis.Z_many([cfg1, cfg2, ...])
        basis.receipt(); basis.save(path); DecapBasis.load(path, mdl)
        DecapBasis.load(path)                   # no model: the decap impedances came in the npz

    `config` is read the way `Model.set_decaps` reads it: a partial `{refdes: model_id | None}`
    over the configuration the model had when the basis was built (`base_config`).

    Accuracy: the module docstring's contract -- CPU basis exact, GPU basis <= 1e-5 and
    non-deterministic, `decap_basis(..., backend=Backend("splu", fast=True))` for the exact path.
    """

    def __init__(self, model, freqs, Zs, refdes, ports, base_config, chunk, build_seconds,
                 unstamped=(), model_ids=None, receipt=None):
        self.model = model                    # None = loaded standalone (`load(path)`)
        self.freqs = np.asarray(freqs, float)
        self.Zs = Zs                          # (nf, 1 + Nd, 1 + Nd), port 0 = the DUT port
        self.refdes = list(refdes)            # basis column k + 1
        self.ports = [tuple(p) for p in ports]
        self.base_config = dict(base_config)
        self.chunk = int(chunk)
        self.build_seconds = float(build_seconds)
        self.unstamped = list(unstamped)      # decaps outside the solved network (never stamped)
        self._z: dict = {}                    # (model_id, frequency index) -> decap impedance
        self.device = None                    # the GPU that built it, if any (set by `build`)
        self.backend = None                   # the backend the build ran on (`decap_basis` may
        #                                       override the model's for the basis only, W12-b)
        self._mids = model_ids                # standalone: the decap models the npz carries
        self._receipt = receipt               # standalone: the receipt of the build that saved it

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
            # the backend/device this build ran on: `decap_basis` restores the model's afterwards
            dev, be = getattr(model._cudss, "device", None), model.backend
        finally:
            model.set_decaps(base)
        b = cls(model, freqs, Zs, refdes, ports, base, chunk, time.time() - t0, unstamped)
        b.device, b.backend = dev, be
        return b

    # ------------------------------------------------------------------ closure
    def resolve(self, config) -> dict:
        """`{refdes: model_id | None}` for every decap: `base_config` updated by `config`."""
        for r, mid in (config or {}).items():
            if r not in self.base_config:
                raise KeyError(f"unknown decap refdes {r!r}")
            if mid is not None and mid not in self.model_ids:
                raise KeyError(f"unknown decap model_id {mid!r} (have {sorted(self.model_ids)})")
        return {**self.base_config, **(config or {})}

    @property
    def model_ids(self) -> set:
        """The decap models a configuration may name: the model's, or the npz's when standalone."""
        return set(self.model.ex["models"]) if self.model is not None else set(self._mids or ())

    def _zdec(self, mid, i):
        """The decap impedance at frequency i -- the same call `assemble` makes, memoised.

        A standalone basis (`load(path)` with no model) has the whole table memoised already, so
        this never needs `ex["models"]`.
        """
        key = (mid, i)
        if key not in self._z:
            if self.model is None:
                raise KeyError(f"this basis was loaded without a model and carries no impedance "
                               f"for {mid!r}; load(path, model) or re-save with_impedances=True")
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
        if m is None:               # standalone: the receipt of the build that saved the npz
            return dict(self._receipt or {}, loaded_without_model=True)
        be = self.backend or m.backend
        return dict(method="decap_basis", numerics_id=numerics_id_of(m),
                    freq=[float(f) for f in self.freqs], n_decaps=len(self.refdes),
                    n_decaps_unstamped=len(self.unstamped), chunk=self.chunk, unknowns=int(m.N),
                    backend=dict(solver=be.solver, fast=be.fast, ir_steps=be.ir_steps,
                                 device=self.device or getattr(m._cudss, "device", None) or None),
                    prune_basis=getattr(m, "prune_basis", "all_mounted"),
                    base_config_sha256=decap_config_sha256(self.base_config),
                    build_seconds=self.build_seconds, basis_MB=self.Zs.nbytes / 1e6)

    def save(self, path, with_impedances=True):
        """Store the basis as npz, by default with the per-frequency impedance of every decap model
        the rail knows (`zdec`, (nf, n_models) -- a few kB), which is what `ex["models"]` was the
        only source of.  `load(path)` then closes configurations without a model, so a search
        driver does not pay the extraction + build (W12-c).  `with_impedances=False` writes the
        W9 format, whose `load` needs the model.

        Either way the meta carries the build's receipt, so a standalone basis still reports the
        `numerics_id` and backend that produced it.
        """
        rec = self.receipt()
        mids = sorted(self.model_ids) if with_impedances else []
        z = np.array([[self._zdec(m, i) for m in mids] for i in range(len(self.freqs))], complex)
        np.savez_compressed(
            path, Z=self.Zs, freqs=self.freqs, ports=np.asarray(self.ports, np.int64), zdec=z,
            meta=json.dumps(dict(refdes=self.refdes, unstamped=self.unstamped,
                                 base_config=self.base_config, chunk=self.chunk,
                                 build_seconds=self.build_seconds, unknowns=rec["unknowns"],
                                 numerics_id=rec["numerics_id"], model_ids=mids, receipt=rec)))
        return path

    @classmethod
    def load(cls, path, model=None):
        """Reload onto a model built the same way (checked: `numerics_id` and the unknown count),
        or with no model at all when the npz was saved `with_impedances=True`."""
        d = np.load(path)
        meta = json.loads(str(d["meta"].item()))
        if model is not None:
            got = (numerics_id_of(model), int(model.N))
            if got != (meta["numerics_id"], meta["unknowns"]):
                raise ValueError(f"basis {path} was built from other numerics/model "
                                 f"({meta['numerics_id'][:8]}…, N={meta['unknowns']}) than this one "
                                 f"({got[0][:8]}…, N={got[1]})")
        elif not meta.get("model_ids"):
            raise ValueError(f"basis {path} was saved without the decap impedances (W9 format); "
                             f"pass the model, or re-save it with_impedances=True")
        b = cls(model, d["freqs"], d["Z"], meta["refdes"], d["ports"], meta["base_config"],
                meta["chunk"], meta["build_seconds"], meta["unstamped"],
                meta.get("model_ids"), meta.get("receipt"))
        if model is None:
            b._z = {(m, i): complex(d["zdec"][i, k]) for k, m in enumerate(meta["model_ids"])
                    for i in range(len(b.freqs))}
        return b


__all__ = ["BasisResult", "DecapBasis"]
