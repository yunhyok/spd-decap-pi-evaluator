"""W9: the decap basis (`Model.decap_basis` -> `DecapBasis.Z(config)`) against the direct solve.

The direct path (`set_decaps` + `solve`) is the truth; the basis only reorganises the same linear
system (plan §2-4 2단계).  Two checks:

  * data-free: a 200-node synthetic Y with three decap terminals -- one on an explicit GND node,
    two on the ideal reference -- closure vs stamping, to the round-off (1e-12).
  * `--gpu`: 260729 Port14 (35 decaps), basis on cuDSS vs the direct splu sweep, 1e-6.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import sparse
from scipy.sparse.linalg import splu

from spd_pi_engine import Backend, DecapBasis, Design, FLAGS_P, ModelOptions
from spd_pi_engine.model import Model

# --------------------------------------------------------------------------- data-free stand-in
N_TINY = 200
DEC = [(37, -1), (88, -1), (155, 42)]          # (rail node, gnd node); -1 = ideal reference
REFDES = ["C1", "C2", "C3"]


class _Cap:
    """A decap model with the one method the engine calls on it (`ex["models"][id].impedance`)."""

    def __init__(self, c, esr, esl):
        self.c, self.esr, self.esl = c, esr, esl

    def impedance(self, freqs):
        return np.array([self.esr + 1j * (2 * math.pi * f * self.esl - 1.0 / (2 * math.pi * f * self.c))
                         for f in freqs])


class TinyModel:
    """Enough of `Model` for `DecapBasis`: the real decap bookkeeping, a synthetic Y.

    `set_decaps` / `decap_config` / `_gpu_solver` / `decap_basis` are `Model`'s own methods -- they
    touch nothing an SPD would have provided.
    """

    set_decaps = Model.set_decaps
    reset_decaps = Model.reset_decaps
    decap_config = Model.decap_config
    _gpu_solver = Model._gpu_solver
    decap_basis = Model.decap_basis

    def __init__(self, seed=0):
        self.N, self.P = N_TINY, 3
        self.backend = Backend()                       # splu: `_gpu_solver` returns None
        self._cudss = None
        self.log = lambda *a: None
        self.dec = [(a, b, "M1") for a, b in DEC]
        self._dec_refdes = list(REFDES)
        self._dec_cfg = {r: "M1" for r in REFDES}
        self.ex = dict(models={"M1": _Cap(1e-7, 2e-3, 5e-10), "M2": _Cap(1e-6, 5e-3, 8e-10)})
        self.Y0 = self._base(seed)

    def map(self, x):                                  # no pruning: identity, -1 stays -1
        return np.asarray(x, np.int64)

    def _base(self, seed):
        """A 10 x 20 resistive-inductive mesh with a shunt to the reference on every node."""
        rng = np.random.default_rng(seed)
        r, c = [], []
        for k in range(N_TINY):
            i, j = divmod(k, 20)
            if i < 9:
                r.append(k); c.append(k + 20)
            if j < 19:
                r.append(k); c.append(k + 1)
        r, c = np.array(r), np.array(c)
        y = 1.0 / (0.01 + 1j * rng.uniform(0.5, 1.5, len(r)))
        g = 1e-3 + 1j * rng.uniform(1e-3, 2e-3, N_TINY)
        d = np.arange(N_TINY)
        rows = np.concatenate([r, c, r, c, d]); cols = np.concatenate([r, c, c, r, d])
        vals = np.concatenate([y, y, -y, -y, g])
        return sparse.coo_matrix((vals, (rows, cols)), shape=(N_TINY, N_TINY)).tocsc()

    def assemble(self, f):
        """`Model.assemble`'s decap line on top of the fixed mesh (`mid is None` -> y = 0)."""
        y = np.array([0.0 if mid is None else 1.0 / self.ex["models"][mid].impedance([f])[0]
                      for _, _, mid in self.dec], complex)
        a = np.array([d[0] for d in self.dec]); b = np.array([d[1] for d in self.dec])
        keep = b >= 0
        rows = np.concatenate([a, b[keep], a[keep], b[keep]])
        cols = np.concatenate([a, b[keep], b[keep], a[keep]])
        vals = np.concatenate([y, y[keep], -y[keep], -y[keep]])
        return (self.Y0 + sparse.coo_matrix((vals, (rows, cols)), shape=(N_TINY, N_TINY))).tocsc()

    def direct(self, config, freqs):
        """The truth: stamp the configuration and solve for the port, one RHS per frequency."""
        keep = self.decap_config
        self.set_decaps(config)
        rhs = np.zeros(self.N, complex); rhs[self.P] = 1.0
        z = np.array([splu(self.assemble(float(f)), permc_spec="COLAMD").solve(rhs)[self.P]
                      for f in freqs])
        self.set_decaps(keep)
        return z


def test_decap_basis_closure_vs_stamping():
    """Closure == direct stamping for mounted, unmounted and swapped configurations."""
    from spd_pi_engine.receipt import decap_config_sha256

    m = TinyModel()
    freqs = np.array([1e4, 1e6, 1e8])
    basis = m.decap_basis(freqs, chunk=2)               # chunk < 1 + Nd: exercises the padding
    assert basis.Zs.shape == (3, 4, 4) and basis.refdes == REFDES and not basis.unstamped
    assert m.decap_config == {r: "M1" for r in REFDES}, "the build must restore the configuration"

    configs = [{}, {"C1": None}, {"C3": None}, {r: None for r in REFDES},
               {r: "M2" for r in REFDES}, {"C1": None, "C2": "M2"}]
    for cfg in configs:
        got = basis.Z(cfg)
        ref = m.direct(cfg, freqs)
        rel = np.max(np.abs(got.Z - ref) / np.abs(ref))
        assert rel <= 1e-12, (cfg, rel)
        assert got._extra["method"] == "decap_basis"
        assert got._extra["decap_config"] == {**{r: "M1" for r in REFDES}, **cfg}
        assert got._extra["decap_config_sha256"] == decap_config_sha256(got._extra["decap_config"])
    assert [r.Z[0] for r in basis.Z_many(configs[:2])] == [basis.Z(configs[0]).Z[0],
                                                           basis.Z(configs[1]).Z[0]]


def test_decap_basis_rejects_unknown_names():
    m = TinyModel()
    basis = m.decap_basis([1e6], chunk=4)
    with pytest.raises(KeyError):
        basis.Z({"NO_SUCH_REFDES": None})
    with pytest.raises(KeyError):
        basis.Z({"C1": "NO_SUCH_MODEL"})


def test_decap_basis_save_load(tmp_path, monkeypatch):
    """`save`/`load` round-trip, and a basis from other numerics is refused."""
    import spd_pi_engine.decaps as D

    m = TinyModel()
    monkeypatch.setattr(D, "numerics_id_of", lambda mdl: "tiny")
    basis = m.decap_basis([1e5, 1e7], chunk=3)
    p = basis.save(tmp_path / "basis.npz")
    back = DecapBasis.load(p, m)
    assert np.array_equal(back.Zs, basis.Zs) and back.refdes == basis.refdes
    assert np.allclose(back.Z({"C2": None}).Z, basis.Z({"C2": None}).Z, rtol=0, atol=0)
    monkeypatch.setattr(D, "numerics_id_of", lambda mdl: "other")
    with pytest.raises(ValueError):
        DecapBasis.load(p, m)


# --------------------------------------------------------------------------- data-backed (--gpu)
@pytest.mark.gpu
def test_decap_basis_port14(spd_path, cache_dir):
    """260729 Port14: basis on cuDSS vs the direct sweep (splu) -- unmount 1 and swap all.

    The basis solver is planned for `chunk` right-hand sides and cuDSS 0.8 allows one
    `DirectSolver` per process, so the direct sweep runs on the CPU here (W8 did the same); the
    1e-6 bound covers the GPU-vs-CPU contract (1e-8) with room to spare.
    """
    rail = Design.open(spd_path("260729")).rail("Port14_SITE0", cache_dir)
    opt = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0,
                       top_h=50.0, sub=(20, 10, 10), fringe=True)
    mdl = rail.build(opt, Backend(solver="cudss", fast=True))
    freqs = np.array([1e5, 1e6, 1e7])

    basis = mdl.decap_basis(freqs, chunk=12)
    rec = basis.receipt()
    assert rec["method"] == "decap_basis" and rec["n_decaps"] == len(basis.refdes)
    assert rec["chunk"] == 12 and rec["unknowns"] == int(mdl.N)

    refdes = [d.refdes for d in rail.decaps]
    other = [i for i in rail.ex["models"] if i != mdl.decap_config[refdes[0]]][0]
    configs = dict(unmount_1={refdes[0]: None}, swap_all={r: other for r in refdes})
    closed = {k: basis.Z(c) for k, c in configs.items()}

    mdl.backend = Backend(solver="splu", fast=True)   # see the docstring
    for k, cfg in configs.items():
        mdl.set_decaps(cfg)
        direct = mdl.solve(freqs, verbose=False).Z
        mdl.reset_decaps()
        rel = np.max(np.abs(closed[k].Z - direct) / np.abs(direct))
        assert rel <= 1e-6, (k, rel)
    r0 = closed["unmount_1"].receipt()
    assert r0["method"] == "decap_basis" and r0["decap_config"][refdes[0]] is None
    assert r0["decap_config_sha256"] != closed["swap_all"].receipt()["decap_config_sha256"]
