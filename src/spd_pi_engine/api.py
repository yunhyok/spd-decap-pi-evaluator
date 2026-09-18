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
from datetime import datetime
from pathlib import Path

import numpy as np

from .backend import DEFAULT
from .model import Model, ModelOptions
from .receipt import Result
from .reference import DEFAULT_CONVENTIONS
from .spd_source import list_ports, prepare, sha256_of


def _capacitance_F(model, f: float = 1e3) -> float | None:
    """C = -1/(2*pi*f*Im Z(f)) at f=1 kHz -- `apps.site_decision.decide.capacitance`'s assumption,
    ported here (W12-c, APP_site_decision_REPORT §7-2): the decap models are series R-L-C ladders,
    so at 1 kHz Im Z is normally the capacitive branch.  `None` when there is no model, or Im Z is
    not negative there (not capacitive at 1 kHz) -- callers should not divide a resistive/inductive
    reading into a fake farad value.
    """
    if model is None:
        return None
    z = complex(model.impedance([f])[0])
    return None if z.imag >= 0 else float(-1.0 / (2 * np.pi * f * z.imag))


@dataclass(frozen=True)
class DecapSite:
    """One decap on the rail (`ex["decaps"]`), as W8's `set_decaps` will address it.

    W12-c: `xy`/`layer`/`capacitance_F`/`impedance()` so an app can rank or place sites without
    reaching into `rail.ex` (APP_decap_search_REPORT §6-5, APP_site_decision_REPORT §7-1/2).
    Least-invasive design: `DecapSite` stays a plain frozen dataclass, with no back-reference to
    `Rail` -- the new fields are plain data (defaults `None`, so a `DecapSite` built any other way,
    e.g. a test fake, is unchanged) and `_model` is the one exception, a private field (excluded
    from `repr`/`==`, so equality and hashing of the original 8 fields is unchanged) that stashes
    the decap's own model object so `impedance()` has something to delegate to.  `Rail.decaps`
    fills all of this in eagerly, from `ex["rail_nodes"]`/`ex["models"]` it already holds; the new
    `Rail.site(refdes)` is the one-site lookup an app that already knows its refdes wants.
    """

    refdes: str
    part: str
    model_id: str
    model_source: str          # "standard" | "ideal" | "subckt"
    node: str                  # the rail-side node id
    gnd_node: str | None
    gnd_net: str | None
    usage: str | None
    xy: tuple[float, float] | None = None      # (x, y) um -- ex["rail_nodes"][node][:2]
    layer: str | None = None                   # ex["rail_nodes"][node][2]
    capacitance_F: float | None = None         # _capacitance_F(model); None if not capacitive
    _model: object = dataclasses.field(default=None, repr=False, compare=False)

    def impedance(self, freqs):
        """This site's decap model impedance (Ohms, complex) -- `ex["models"][model_id].impedance`.

        Raises if this `DecapSite` was not built by `Rail.decaps`/`Rail.site` (no model bound).
        """
        if self._model is None:
            raise ValueError(f"{self.refdes}: no bound model (DecapSite built outside Rail.decaps)")
        return self._model.impedance(freqs)


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

    def multiport(self, ports, cache_dir, options=None, backend=DEFAULT, max_layers=3,
                  conventions=None, log=None):
        """W10: the built k-port model of several ports that share ONE rail.

        `multiport.MultiRail.open(...).build(...)`.  Raises when the ports are not on the same
        rail_net -- `multiport.rail_groups(spd)` says which ports could share a model.  The fine
        mesh box is the union of the ports' pin boxes, so the diagonal is not a single-port
        build's Z unless `options.fine_box` forces it (docs/engine/W10_REPORT.md).
        """
        from .multiport import MultiRail

        mr = MultiRail.open(self, ports, cache_dir, max_layers=max_layers,
                            conventions=conventions, log=log)
        return mr.build(options, backend, log)


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
        rn = self.ex["rail_nodes"]
        models = self.ex.get("models", {})
        out = []
        for d in self.ex["decaps"]:
            rec = rn.get(d["rail_node"])
            xy = (float(rec[0]), float(rec[1])) if rec is not None else None
            layer = rec[2] if rec is not None else None
            model = models.get(d["model_id"])
            out.append(DecapSite(refdes=d["refdes"], part=d["part"], model_id=d["model_id"],
                                 model_source=d.get("model_source", ""), node=d["rail_node"],
                                 gnd_node=d["gnd_node"], gnd_net=d["gnd_net"], usage=d["usage"],
                                 xy=xy, layer=layer, capacitance_F=_capacitance_F(model),
                                 _model=model))
        return out

    @functools.cached_property
    def _decaps_by_refdes(self) -> dict:
        return {d.refdes: d for d in self.decaps}

    def site(self, refdes) -> DecapSite:
        """The one `DecapSite` named `refdes` (W12-c) -- `self.decaps` already built the whole
        list, so this is just the lookup an app that knows its refdes wants."""
        return self._decaps_by_refdes[str(refdes)]

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


# ---------------------------------------------------------------- util (W12-c, public now)
#: LADDER + 21 log points 3e5..3e7 (`exp5/pipeline.LADDER`).  Was `cli.LADDER` only; `cli` now
#: imports this same tuple so `spd_pi_engine.cli.LADDER` still works (APP_decap_search_REPORT §6-6).
LADDER = (3.0e4, 1.0e5, 3.0e5, 1.0e6, 2.5e6, 1.0e7, 1.0e8)


def ladder_freqs(ref_freq=None) -> np.ndarray:
    """LADDER + 21 log points 3e5..3e7; snapped to `ref_freq` when given, else raw and sorted.

    Moved here from `cli.py` (W12-c): both apps already used this as their standard frequency set
    and had to import the CLI module just to get it (APP_decap_search_REPORT §6-6).  `cli.py`
    keeps working -- it imports this same function.
    """
    dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
    pts = list(LADDER) + list(dense)
    if ref_freq is None:
        return np.array(sorted(set(pts)), dtype=float)
    fref = np.asarray(ref_freq, dtype=float)
    idx = sorted({int(np.argmin(abs(fref - x))) for x in pts})
    return fref[idx]


def unique_path(path) -> Path:
    """Never overwrite (CLAUDE.md rule 2): `_HHMMSS` before the suffix if `path` already exists.

    Moved here from `cli.py` (W12-c), same behaviour, public now.
    """
    path = Path(path)
    if not path.exists():
        return path
    return path.with_name(f"{path.stem}_{datetime.now().strftime('%H%M%S')}{path.suffix}")


def find_site_pair(spd_path, port) -> str | None:
    """The partner port whose rail net differs from `port`'s only in the trailing `/0` <-> `/1`
    (ported from `apps.site_decision.decide.find_site_pair`, W12-c, APP_site_decision_REPORT §7-3).

    Reads the `.Port` headers only (`multiport.port_rails`), not the extraction -- ~1 s even on a
    92-port design.  Returns `None` instead of raising when `port`'s net has no `/0`-`/1` suffix,
    or when the partner net does not have exactly one port on it (decide.py raised in both cases;
    an app that does not know in advance whether a SITE pair exists can just check the result).
    """
    from .multiport import port_rails

    rails = port_rails(spd_path)
    net = rails[str(port)]
    base, sep, site = net.rpartition("/")
    if not sep or site not in ("0", "1"):
        return None
    want = f"{base}/{'1' if site == '0' else '0'}"
    hits = [p for p, r in rails.items() if r == want]
    return hits[0] if len(hits) == 1 else None


def match_sites(rail0, rail1, rule: str = "refdes-suffix") -> dict:
    """`{"mapping": {refdes on rail0: refdes on rail1}, "unmatched": [refdes on rail0 with no pair]}`
    for one physical-site-pair rule between two `Rail`s (ported from
    `apps.site_decision.decide.match_sites`'s adopted rule, W12-c, APP_site_decision_REPORT §7-3).

    `rule="refdes-suffix"` (the only rule ported -- `decide.py`'s `"geometry"` fallback is app-side
    policy, not an engine rule, and was measured *wrong* on the one design tried, see `decide.py`):
    each SITE net's own trailing `/0`/`/1` is also the refdes suffix (`C2001_0` on `.../0` pairs
    with `C2001_1` on `.../1`); strip the suffix and match the stem.  `unmatched` is a list, not a
    `KeyError`, so an app checks it once instead of once per site.
    """
    if rule != "refdes-suffix":
        raise ValueError(f"rule must be 'refdes-suffix', not {rule!r}")

    def _site(rail_net: str) -> str:
        return rail_net.rpartition("/")[2]

    def _stem(refdes: str, site: str) -> str:
        tail = "_" + site
        return refdes[: -len(tail)] if refdes.endswith(tail) else refdes

    s0, s1 = _site(rail0.rail_net), _site(rail1.rail_net)
    k1 = {_stem(d.refdes, s1): d.refdes for d in rail1.decaps}
    mapping = {d.refdes: k1[_stem(d.refdes, s0)] for d in rail0.decaps if _stem(d.refdes, s0) in k1}
    unmatched = [d.refdes for d in rail0.decaps if d.refdes not in mapping]
    return dict(mapping=mapping, unmatched=unmatched)


__all__ = ["DecapSite", "Design", "LADDER", "Rail", "Result", "find_site_pair", "ladder_freqs",
          "match_sites", "unique_path"]
