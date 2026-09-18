"""The public API (plan §2-3): `Design` -> `Rail` -> `Model` -> `Result`.

    from spd_pi_engine import Design, ModelOptions, Backend, FLAGS_P
    d = Design.open(r"...\\S4LB002-2Para_260729_1_injected.spd")
    d.ports()                                     # from the SPD .Port blocks (C19)
    rail = d.rail("Port18_SITE0", cache_dir=r"...\\engine_cache")
    rail.rail_net, rail.decaps, rail.n_nodes, rail.estimate_cost()
    opt = ModelOptions(flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0, sub=(20, 10, 10),
                       fringe=True, fine_box=rail.fine_box)
    mdl = rail.build(opt, Backend(solver="cudss", fast=True))
    res = mdl.solve(freqs); res.freq; res.Z; res.stats; res.breakdown(1e5); res.receipt()

Everything here is a thin shell over W1-W3: `Design.rail` is `spd_source.prepare`, `Rail.build` is
`Model.build` and `Model.solve` already returns the `Result` that writes the receipt.  No numerics
live in this module.

W8 (`model.py`) adds the decap configuration on top of a built model -- no rebuild, no re-prune:

    mdl.set_decaps({"C1234": None, "C1235": "CAP_0402_100NF"})   # None = unmounted
    mdl.decap_config                                             # {refdes: model_id | None}
    mdl.reset_decaps()                                           # back to the SPD's own
    mdl.add_decap_model("MY_CAP", subckt_text)                   # register a .SUBCKT two-port
    res2 = mdl.solve(res.freq)     # same YPattern, same cuDSS plan: refactorize only

`Result.receipt()` then carries `decap_config`, `decap_config_sha256` and `prune_basis`.

W9 (`decaps.py`) adds stage 2 for sweeps over many configurations: one multi-port basis, then a
Schur closure per configuration in milliseconds.

    basis = mdl.decap_basis(freqs)        # 1 + Nd right-hand sides, one factorization per f
    basis.Z({"C1234": None}).Z            # partial config, read as in set_decaps
    basis.Z_many([cfg1, cfg2, ...]); basis.receipt(); basis.save(path)

On the GPU a model is either a basis model or a sweep model, not both in one process
(`Model._gpu_solver`: cuDSS 0.8 allows one DirectSolver, and its RHS width is fixed at plan time).
"""
from __future__ import annotations

import dataclasses
import functools
import time
from dataclasses import dataclass
from pathlib import Path

from .backend import DEFAULT
from .model import Model, ModelOptions
from .receipt import Result
from .reference import DEFAULT_CONVENTIONS
from .spd_source import list_ports, prepare, sha256_of


@dataclass(frozen=True)
class DecapSite:
    """One decap on the rail (`ex["decaps"]`), as W8's `set_decaps` will address it."""

    refdes: str
    part: str
    model_id: str
    model_source: str          # "standard" | "ideal" | "subckt"
    node: str                  # the rail-side node id
    gnd_node: str | None
    gnd_net: str | None
    usage: str | None


class Design:
    """One SPD file.  Lazy: `open` touches nothing, the file is read by `ports()`/`sha256`/`rail`."""

    def __init__(self, spd_path):
        self.path = Path(spd_path)

    @classmethod
    def open(cls, spd_path) -> "Design":
        return cls(spd_path)

    def __repr__(self):
        return f"Design({str(self.path)!r})"

    @functools.cached_property
    def sha256(self) -> str:
        """sha256 of the SPD bytes -- the cache key and the receipt's provenance field."""
        return sha256_of(self.path)

    def ports(self) -> list[str]:
        """Short port names in SPD `.Port` order (matches the reference npz `port_names` order)."""
        return list_ports(self.path)

    def rail(self, port, cache_dir, max_layers=3, conventions=None, log=None) -> "Rail":
        """Extract one port's rail (through the engine cache) -- `spd_source.prepare`."""
        return Rail(self, port, cache_dir, max_layers, conventions, log)


class Rail:
    """One extracted rail: the model input (`ex`, `shapes`, `fine_box`) plus what an app asks
    before it decides to build (`decaps`, sizes, `estimate_cost`)."""

    def __init__(self, design: Design, port, cache_dir, max_layers=3, conventions=None, log=None):
        self.design = design
        self.port = str(port)
        self.cache_dir = Path(cache_dir)
        self.max_layers = int(max_layers)
        self.conventions = conventions or DEFAULT_CONVENTIONS
        t0 = time.time()
        self.ex, self.shapes, self.fine_box = prepare(design.path, self.port, self.cache_dir,
                                                      max_layers=self.max_layers, log=log,
                                                      conventions=self.conventions)
        self.prepare_seconds = time.time() - t0

    def __repr__(self):
        return (f"Rail({self.port!r}, rail_net={self.rail_net!r}, nodes={self.n_nodes}, "
                f"decaps={len(self.decaps)})")

    # -------------------------------------------------- provenance
    @property
    def spd_path(self) -> str:
        return str(self.design.path)

    @property
    def spd_sha256(self) -> str:
        return self.design.sha256

    # -------------------------------------------------- content
    @property
    def rail_net(self) -> str:
        return self.ex["rail_net"]

    @functools.cached_property
    def decaps(self) -> list[DecapSite]:
        return [DecapSite(refdes=d["refdes"], part=d["part"], model_id=d["model_id"],
                          model_source=d.get("model_source", ""), node=d["rail_node"],
                          gnd_node=d["gnd_node"], gnd_net=d["gnd_net"], usage=d["usage"])
                for d in self.ex["decaps"]]

    @property
    def n_nodes(self) -> int:
        return len(self.ex["rail_nodes"])

    @property
    def n_vias(self) -> int:
        return len(self.ex["rail_vias"])

    @property
    def n_traces(self) -> int:
        return len(self.ex["rail_traces"])

    def estimate_cost(self) -> dict:
        """The extraction stats an app sizes a job with (plan §5-5; `exp30/extract_stats.json`).

        Cheap -- it is what `prepare` already produced.  There is no cost estimate before that:
        the extraction is the expensive step (`runall15.py:120-122`).
        """
        return dict(rail_net=self.rail_net, n_rail_nodes=self.n_nodes, n_rail_vias=self.n_vias,
                    n_rail_traces=self.n_traces, n_decaps=len(self.ex["decaps"]),
                    rail_layers=[g["layer"] for g in self.ex["rail_geoms"]],
                    prepare_seconds=round(self.prepare_seconds, 1))

    # -------------------------------------------------- build
    def build(self, options: ModelOptions | None = None, backend=DEFAULT, log=None) -> Model:
        """`Model.build` on this rail.  `options.fine_box` defaults to the rail's own box."""
        opt = options or ModelOptions()
        if opt.fine_box is None:
            opt = dataclasses.replace(opt, fine_box=self.fine_box)
        mdl = Model.build(self.ex, self.shapes, opt, backend, log)
        mdl.rail = self  # the receipt's provenance (spd_path, sha256, port, prepare_seconds)
        return mdl


__all__ = ["DecapSite", "Design", "Rail", "Result"]
