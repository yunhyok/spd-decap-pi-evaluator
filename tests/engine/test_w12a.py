"""W12-a / W12-b: the basis and the direct solve in one process, and a basis without a model.

Data-free
    * `set_decaps(config, replace=True)` = reset + apply, validated before anything changes.
    * `release_solver()` empties the solver slot (and leaves the "no GPU here" latch alone).
    * `save(with_impedances=True)` -> `load(path)` with NO model closes the same configurations.

`--gpu` (260729 Port14)
    * one process: a cuDSS basis (nrhs = chunk) AND `set_decaps` + a cuDSS direct solve (nrhs = 1)
      on the same model, agreeing to 1e-6 -- what W12-b showed a second `DirectSolver` allows.
    * the CPU (exact) basis vs the GPU basis, 1e-5: the contract in `decaps.DecapBasis`.
"""
from __future__ import annotations

import numpy as np
import pytest

from spd_pi_engine import Backend, DecapBasis, Design, FLAGS_P, ModelOptions
from spd_pi_engine.model import Model
from test_decap_sweep import REFDES, TinyModel


class Tiny(TinyModel):
    """`TinyModel` (no SPD) plus the two things W12 touches: `ex["decaps"]` and `release_solver`."""

    release_solver = Model.release_solver

    def __init__(self, seed=0):
        super().__init__(seed)
        self.ex["decaps"] = [dict(refdes=r, model_id="M1") for r in REFDES]


def test_set_decaps_replace():
    m = Tiny()
    m.set_decaps({"C1": None, "C2": "M2"})
    assert m.decap_config == {"C1": None, "C2": "M2", "C3": "M1"}

    m.set_decaps({"C3": "M2"}, replace=True)            # reset to the SPD config, then apply
    assert m.decap_config == {"C1": "M1", "C2": "M1", "C3": "M2"}
    assert m.set_decaps({}, replace=True).decap_config == {r: "M1" for r in REFDES}

    m.set_decaps({"C1": None})
    with pytest.raises(KeyError):                       # rejected: nothing changes, not even reset
        m.set_decaps({"C2": "NO_SUCH_MODEL"}, replace=True)
    assert m.decap_config["C1"] is None
    assert [d[2] for d in m.dec] == [None, "M1", "M1"]  # the stamped values agree with the config


def test_release_solver():
    m = Tiny()
    assert m.release_solver() is None                   # nothing planned yet
    sentinel = m._cudss = object()
    assert m.release_solver() is sentinel and m._cudss is None
    m._cudss = False                                    # the "cuDSS is unusable here" latch
    assert m.release_solver() is False and m._cudss is False


def test_basis_save_load_without_model(tmp_path, monkeypatch):
    """A basis saved with the decap impedances closes configurations with no model at all."""
    import spd_pi_engine.decaps as D

    monkeypatch.setattr(D, "numerics_id_of", lambda mdl: "tiny")
    m = Tiny()
    freqs = np.array([1e4, 1e6, 1e8])
    basis = m.decap_basis(freqs, chunk=2)
    p = basis.save(tmp_path / "basis.npz")

    free = DecapBasis.load(p)                           # no model
    assert free.model is None and free.model_ids == {"M1", "M2"}
    assert free.receipt()["loaded_without_model"] and free.receipt()["numerics_id"] == "tiny"
    for cfg in ({}, {"C1": None}, {r: None for r in REFDES}, {"C2": "M2"}):
        got = free.Z(cfg)
        assert np.array_equal(got.Z, basis.Z(cfg).Z), cfg   # same closure, bit for bit
        rec = got.receipt()                                 # no model: the basis' own receipt
        assert rec["decap_config"] == basis.resolve(cfg) and rec["method"] == "decap_basis"
        assert rec["decap_basis"]["loaded_without_model"]
    with pytest.raises(KeyError):
        free.Z({"C1": "NO_SUCH_MODEL"})

    q = basis.save(tmp_path / "w9.npz", with_impedances=False)   # the old format still loads...
    assert np.array_equal(DecapBasis.load(q, m).Zs, basis.Zs)
    with pytest.raises(ValueError):                              # ...but not without a model
        DecapBasis.load(q)


# --------------------------------------------------------------------------- data-backed (--gpu)
OPT = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True)
FREQS = np.array([1e5, 1e6, 1e7])


@pytest.fixture(scope="module")
def port14(spd_path, cache_dir):
    return Design.open(spd_path("260729")).rail("Port14_SITE0", cache_dir)


@pytest.mark.gpu
def test_basis_and_direct_solve_in_one_process(port14):
    """W12-b: the basis (nrhs = chunk) and the direct solve (nrhs = 1) on ONE cuDSS model."""
    mdl = port14.build(OPT, Backend(solver="cudss", fast=True))
    basis = mdl.decap_basis(FREQS, chunk=12)
    assert basis.receipt()["backend"]["solver"] == "cudss"

    refdes = [d.refdes for d in port14.decaps]
    other = [i for i in port14.ex["models"] if i != mdl.decap_config[refdes[0]]][0]
    for cfg in ({}, {refdes[0]: None}, {r: other for r in refdes}):
        mdl.set_decaps(cfg, replace=True)
        direct = mdl.solve(FREQS, verbose=False).Z          # same model, same process, nrhs = 1
        assert mdl._cudss and mdl._cudss.nrhs == 1
        rel = np.max(np.abs(basis.Z(cfg).Z - direct) / np.abs(direct))
        assert rel <= 1e-6, (cfg, rel)
    mdl.reset_decaps()


@pytest.mark.gpu
def test_cpu_basis_vs_gpu_basis(port14):
    """The exact path: `decap_basis(..., backend=Backend("splu", fast=True))` on a cuDSS model."""
    mdl = port14.build(OPT, Backend(solver="cudss", fast=True))
    gpu = mdl.decap_basis(FREQS, chunk=12)
    cpu = mdl.decap_basis(FREQS, chunk=12, backend=Backend(solver="splu", fast=True))
    assert cpu.receipt()["backend"]["solver"] == "splu" and mdl.backend.solver == "cudss"
    for cfg in ({}, {[d.refdes for d in port14.decaps][0]: None}):
        rel = np.max(np.abs(gpu.Z(cfg).Z - cpu.Z(cfg).Z) / np.abs(cpu.Z(cfg).Z))
        assert rel <= 1e-5, (cfg, rel)
