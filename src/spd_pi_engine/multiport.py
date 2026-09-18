"""W10 -- the k-port Z(f) matrix of several ports on ONE rail (plan §2-5, §4 W10).

One extraction, one model, k excitations:

    from spd_pi_engine import Backend, Design, FLAGS_P, ModelOptions, rail_groups
    rail_groups(spd)                       # {rail_net: [port, ...]} -- who can share a model
    mp = Design.open(spd).multiport(["PortA", "PortB"], cache_dir,
                                    ModelOptions(flags=FLAGS_P, fringe=True),
                                    Backend(solver="cudss", fast=True))
    res = mp.solve(freqs)                  # res.Z is (nf, k, k)
    res.reciprocity(); res.shorted(); res.receipt()

Why this needs no change in `model.py`
--------------------------------------
`Model._build` shorts `ex["port_pos_nodes"]` into ONE unknown and records it as `self.P`; every
other rail node keeps its own unknown.  So a model built from port A's extraction already contains
port B's pins -- as separate unknowns, not as a port.  `short_ports()` finishes the job on the
built model: it shorts each remaining group into one unknown and re-indexes, by rewriting the only
three things `assemble` / `solve` / `breakdown` reach the unknown vector through (`map`, `N`, `P`).
Nothing is re-meshed, re-pruned or re-assembled, and for k = 1 the rewrite is the identity, which
is what `tests/engine/test_multiport.py` checks first.

Two consequences the caller must know
-------------------------------------
1. `fine_box` is the UNION of the ports' pin boxes (+/- 1 mm), so the fine mesh region of a k-port
   build is larger than any of the single-port builds it is compared against.  The diagonal
   therefore does NOT reproduce the single-port Z bit for bit; pass `ModelOptions(fine_box=...)`
   explicitly to force one.  Measured effect: `docs/engine/W10_REPORT.md` §4-3.
2. Ports on DIFFERENT rails cannot share a model at all.  The extraction is by net name
   (`spd_source.extract`), and with the frozen ideal-GND reference (variant B) two nets share no
   unknown, so their mutual Z is identically zero.  `MultiRail.open` refuses the combination
   instead of returning a diagonal matrix that looks like a result.  In all three designs on hand
   every SPD port sits on its own net -- see the report §3.
"""
from __future__ import annotations

import dataclasses
import mmap
import time
from dataclasses import dataclass

import numpy as np
from scipy.sparse.linalg import splu

from .backend import DEFAULT
from .model import Model, ModelOptions, peak_rss_mb
from .receipt import Result
from .reference import DEFAULT_CONVENTIONS
from .spd_source import _RX_PORT_HDR, prepare, sha256_of

#: `spd_source.prepare`: the port's pin bounding box is grown by 1 mm to make the fine-mesh box.
FINE_MARGIN_UM = 1000.0


# ---------------------------------------------------------------- port grouping (C19)
def group_ports_by_rail(port_rails) -> dict:
    """`{port: rail_net}` -> `{rail_net: [port, ...]}`, both in input order.

    A group with more than one port is a candidate for `MultiRail`; a group of one is an ordinary
    single-port rail.  Pure -- `tests/engine/test_multiport.py` runs it without any data.
    """
    out: dict = {}
    for port, rail in port_rails.items():
        out.setdefault(str(rail), []).append(str(port))
    return out


def port_rails(spd_path, ports=None) -> dict:
    """`{port: rail_net}` for every SPD port, from the `.Port` headers.

    Same regex and same capture group as `spd_source.extract`, which sets
    `ex["rail_net"] = mm.group(3)`, so this is that value without paying for k extractions
    (a cold extract is 25-30 s; there are 92-160 ports per design).
    """
    with open(spd_path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as d:
        start = d.find(b"* Port description lines")
        if start < 0:
            raise ValueError(f"{spd_path}: no '* Port description lines' section")
        end = d.find(b".EndPort", start)
        out = {m.group(1).split(b"::")[0].decode(): m.group(3).decode()
               for m in _RX_PORT_HDR.finditer(d, start, len(d) if end < 0 else end)}
    return out if ports is None else {str(p): out[str(p)] for p in ports}


def rail_groups(spd_path, ports=None) -> dict:
    """`{rail_net: [port, ...]}` for one SPD -- the ports that could share a multi-port model."""
    return group_ports_by_rail(port_rails(spd_path, ports))


# ---------------------------------------------------------------- node indices / shorting
def rail_node_indices(mdl, nodes) -> dict:
    """`{SPD node id: pre-prune node index}` exactly as `Model._build` assigned them.

    `_build` keeps its `idx` dict local, so it is rebuilt here: a rail node takes its sheet cell
    (`Sheet.snap`), and one that does not snap takes the running counter, in `ex["rail_nodes"]`
    order.  The counter's start is recovered from `mdl.port`, which `_build` set to the counter's
    value once every rail node had an index.  Fast path: no rail-node scan at all when every
    requested node snaps (port pins normally do).
    """
    rn = mdl.ex["rail_nodes"]
    out, miss = {}, []
    for nid in nodes:
        x, y, lay, _ps = rn[nid]
        sh = mdl.sheets.get(lay)
        r = sh.snap(x, y) if sh is not None else None
        if r is None:
            miss.append(nid)
        else:
            out[nid] = int(r[0])
    if miss:
        off = [n for n, (x, y, lay, _p) in rn.items()
               if mdl.sheets.get(lay) is None or mdl.sheets[lay].snap(x, y) is None]
        n0 = int(mdl.port) - len(off)
        rank = {n: k for k, n in enumerate(off)}
        for nid in miss:
            out[nid] = n0 + rank[nid]
    return out


def short_ports(mdl, groups) -> list:
    """Short each group of rail node ids into one unknown; return the unknown index per group.

    Consumes `mdl`: it is the k-port model afterwards, not the single-port one.  Only `map`, `N`
    and `P` are rewritten -- `assemble`, `solve` and `breakdown` reach the unknown vector through
    nothing else, so the mesh, the reference search, the pruning and every stamped element stay
    exactly as `_build` left them.

    Group 0 must be the model's own port (`_build` already shorted it): that it comes back as the
    single unknown `mdl.P` is the self-check on `rail_node_indices`.  `P` itself still moves, by
    the number of unknowns the other groups merged away below it.
    """
    base = mdl.map
    N = int(mdl.N)
    p_in = int(mdl.P)
    q = np.arange(N, dtype=np.int64)
    reps, taken = [], {}
    for gi, nodes in enumerate(groups):
        idx = rail_node_indices(mdl, nodes)
        p = np.unique(base(np.array([idx[n] for n in nodes], np.int64)))
        if gi == 0 and p.tolist() != [p_in]:
            raise AssertionError(
                f"group 0 maps to {p.tolist()[:4]}... instead of the model's own port unknown "
                f"{p_in}: `rail_node_indices` no longer reproduces `Model._build`'s node indices")
        if len(p) == 0 or (p < 0).any():
            raise ValueError(f"port {gi}: {int((p < 0).sum())} of {len(nodes)} nodes are outside "
                             "the port's connected component (pruned away) and cannot carry a "
                             "port current")
        for s in p.tolist():
            if s in taken:
                raise ValueError(f"ports {taken[s]} and {gi} share unknown {s}: the node groups "
                                 "overlap, so they are one port, not two")
            taken[s] = gi
        reps.append(int(p[0]))
        q[p] = int(p[0])
    keep = q == np.arange(N)
    new = -np.ones(N, np.int64)
    new[keep] = np.arange(int(keep.sum()))
    q2 = new[q]

    def map2(x, _base=base, _q2=q2, _n=N):
        m = _base(x)
        return np.where(m < 0, -1, _q2[np.clip(m, 0, _n - 1)])

    mdl.map = map2
    mdl.N = int(keep.sum())
    mdl._fa_pat = None                       # the YPattern cache was built for the old N
    ports = [int(q2[r]) for r in reps]
    mdl.P = ports[0]
    mdl.info.update(multiport_ports=len(ports), unknowns=mdl.N, unknowns_single_port=N)
    return ports


# ---------------------------------------------------------------- rail
def _node_box(ex, nodes) -> tuple:
    """`spd_source.prepare`'s box for an arbitrary node group (pin bbox +/- 1 mm)."""
    rn = ex["rail_nodes"]
    P = np.array([(rn[x][0], rn[x][1]) for x in nodes if x in rn])
    if not len(P):
        raise ValueError("none of the group's nodes is a rail node of this extraction")
    return (P[:, 0].min() - FINE_MARGIN_UM, P[:, 1].min() - FINE_MARGIN_UM,
            P[:, 0].max() + FINE_MARGIN_UM, P[:, 1].max() + FINE_MARGIN_UM)


def _union(boxes) -> tuple:
    b = np.asarray(list(boxes), float)
    return (float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 2].max()), float(b[:, 3].max()))


class MultiRail:
    """k ports on ONE rail: one extraction plus the node group of each port.

    Also carries the provenance fields `receipt.Result.receipt()` reads off `model.rail`
    (`spd_path`, `spd_sha256`, `port`, `prepare_seconds`), so a multi-port receipt has the same
    provenance as a single-port one.
    """

    def __init__(self, ex, shapes, groups, fine_box=None, spd_path="", spd_sha256="",
                 prepare_seconds=0.0):
        self.ex, self.shapes = ex, shapes
        self.groups = {str(k): list(v) for k, v in groups.items()}
        if not self.groups:
            raise ValueError("a multiport needs at least one port")
        self.fine_box = tuple(fine_box) if fine_box is not None else \
            _union(_node_box(ex, g) for g in self.groups.values())
        self.spd_path = str(spd_path or ex.get("spd_path", ""))
        self.spd_sha256 = str(spd_sha256 or "")
        self.prepare_seconds = float(prepare_seconds)

    # -------------------------------------------------- constructors
    @classmethod
    def open(cls, design, ports, cache_dir, max_layers=3, conventions=None, log=None) -> "MultiRail":
        """Extract every port and keep the first one's rail.  Refuses ports on different rails."""
        conv = conventions or DEFAULT_CONVENTIONS
        names = [str(p) for p in ports]
        cls._one_rail(port_rails(design.path, names))    # before paying for k extractions
        t0 = time.time()
        first, rails, groups, boxes = None, {}, {}, []
        for p in names:
            ex, shapes, box = prepare(design.path, p, cache_dir, max_layers=max_layers, log=log,
                                      conventions=conv)
            rails[p] = ex["rail_net"]
            groups[p] = list(ex["port_pos_nodes"])
            boxes.append(box)
            if first is None:
                first = (ex, shapes)
        cls._one_rail(rails)                             # the header said so; the extraction agrees
        return cls(first[0], first[1], groups, _union(boxes), design.path,
                   getattr(design, "sha256", "") or sha256_of(design.path), time.time() - t0)

    @staticmethod
    def _one_rail(rails: dict) -> None:
        if len(set(rails.values())) > 1:
            raise ValueError(
                "multiport needs one rail: " + ", ".join(f"{p} on {r}" for p, r in rails.items()) +
                ". The extraction is by net name and with the frozen ideal-GND reference two nets "
                "share no unknown, so their mutual Z would be identically zero "
                "(docs/engine/W10_REPORT.md §3)")

    @classmethod
    def from_rail(cls, rail, groups) -> "MultiRail":
        """k ports defined as node-id groups on one already-extracted `api.Rail`.

        This is the general form: an SPD port is just a group (`ex["port_pos_nodes"]`), and any
        other set of rail nodes -- a subset of the pins, a decap site, a connector field -- is a
        port too.  The frozen designs have one SPD port per net, so this is also the only way to
        build a k>1 multiport on them.
        """
        return cls(rail.ex, rail.shapes, groups, None, rail.spd_path, rail.spd_sha256,
                   getattr(rail, "prepare_seconds", 0.0))

    # -------------------------------------------------- content
    def __repr__(self):
        return f"MultiRail({list(self.groups)!r}, rail_net={self.rail_net!r})"

    @property
    def rail_net(self) -> str:
        return self.ex["rail_net"]

    @property
    def ports(self) -> list:
        return list(self.groups)

    @property
    def port(self) -> str:
        """The model's own port (group 0) -- what `Result.receipt()` records as `port`."""
        return self.ports[0]

    def build(self, options=None, backend=DEFAULT, log=None) -> "MultiModel":
        return MultiModel(self, options, backend, log)


# ---------------------------------------------------------------- model
class MultiModel:
    """One `Model` whose k port groups are each shorted into one unknown."""

    def __init__(self, mrail: MultiRail, options=None, backend=DEFAULT, log=None):
        opt = options or ModelOptions()
        if opt.fine_box is None:
            opt = dataclasses.replace(opt, fine_box=mrail.fine_box)
        self.mrail = mrail
        self.names = mrail.ports
        # the build shorts group 0 for us; short_ports() does the rest on the built model
        ex = dict(mrail.ex, port_pos_nodes=list(mrail.groups[self.names[0]]))
        self.model = Model.build(ex, mrail.shapes, opt, backend, log)
        self.model.rail = mrail
        self.P = short_ports(self.model, [mrail.groups[n] for n in self.names])

    def __repr__(self):
        return f"MultiModel({self.names!r}, unknowns={self.model.N})"

    def solve(self, freqs, verbose=True) -> "MultiResult":
        """Z(f) as (nf, k, k): one factorization per frequency, k right-hand sides on it."""
        mdl = self.model
        log = mdl.log
        k = len(self.P)
        freqs = np.asarray(freqs, float)
        rhs = np.zeros((int(mdl.N), k), complex, order="F")   # W9: cuDSS wants the dense RHS column-major
        rhs[self.P, np.arange(k)] = 1.0
        Z = np.zeros((len(freqs), k, k), complex)
        stats = []
        t_all = time.time()
        for fi, f in enumerate(freqs):
            t0 = time.time()
            Y = mdl.assemble(f)
            t1 = time.time()
            gpu = mdl._gpu_solver(Y, nrhs=k)     # W9: one factorization, k right-hand sides
            if gpu:
                V, st = gpu.solve(Y, rhs)
                Vs = list(V.T)
                t2 = time.time()
                stats.append(dict(f=float(f), assemble_s=t1 - t0, rss_MB=peak_rss_mb(), n_rhs=k, **st))
            else:
                lu = splu(Y, permc_spec="COLAMD")
                t2 = time.time()
                Vs = list(lu.solve(rhs).T)
                stats.append(dict(f=float(f), assemble_s=t1 - t0, factor_s=t2 - t1, nnz_LU=int(lu.nnz),
                                  rss_MB=peak_rss_mb(), solver="splu", n_rhs=k))
                del lu
            for j in range(k):
                Z[fi, :, j] = np.asarray(Vs[j])[self.P]
            del Vs
            if verbose:
                log(f"  f={f:11.4e} k={k} |Z11|={abs(Z[fi, 0, 0]):.4e} "
                    f"asm {t1-t0:.1f}s fact+solve {t2-t1:.1f}s")
        return MultiResult(self, freqs, Z, stats, time.time() - t_all)


# ---------------------------------------------------------------- result
@dataclass
class MultiResult:
    """The k-port sweep.  `Z[f, i, j]` = V_i when 1 A is injected at port j."""

    model: MultiModel
    freq: np.ndarray
    Z: np.ndarray
    stats: list
    solve_seconds: float = 0.0

    @property
    def ports(self) -> list:
        return list(self.model.names)

    def diagonal(self) -> np.ndarray:
        """(k, nf): each port's own Z(f), the other ports open."""
        return np.array([self.Z[:, i, i] for i in range(self.Z.shape[1])])

    def reciprocity(self) -> float:
        """max |Z_ij - Z_ji| / |Z_ij| over every frequency and pair (the network is reciprocal)."""
        d = np.abs(self.Z - np.swapaxes(self.Z, 1, 2))
        return float(np.max(d / np.abs(self.Z))) if self.Z.size else 0.0

    def coupling(self) -> np.ndarray:
        """(nf, k, k): |Z_ij| / sqrt(|Z_ii| |Z_jj|), 1 = the ports see the same node."""
        d = np.sqrt(np.abs(np.einsum("fii->fi", self.Z)))
        return np.abs(self.Z) / (d[:, :, None] * d[:, None, :])

    def shorted(self) -> np.ndarray:
        """Z(f) with every port tied together: 1 / sum(Z^-1), i.e. the single-port equivalent.

        For a k-port that is a split of one SPD port this must reproduce the single-port engine
        result exactly (same mesh, same `fine_box`) -- the W10 gate.
        """
        return np.array([1.0 / np.linalg.inv(Zf).sum() for Zf in self.Z])

    def receipt(self) -> dict:
        """Receipt v1 with `method="multiport"` and a (nf, k, k) Z (plan §2-3)."""
        mdl = self.model.model
        rec = Result(mdl, self.freq, self.Z[:, 0, 0], self.stats,
                     None, self.solve_seconds).receipt(breakdown_100k=False)
        rec.update(
            method="multiport",
            ports=self.ports,
            port_groups={n: list(g) for n, g in self.model.mrail.groups.items()},
            port_unknown_index=[int(p) for p in self.model.P],
            fine_box=list(mdl.options.fine_box) if mdl.options.fine_box else None,
            Z_re=[[[float(v.real) for v in row] for row in Zf] for Zf in self.Z],
            Z_im=[[[float(v.imag) for v in row] for row in Zf] for Zf in self.Z],
            reciprocity_max_rel=self.reciprocity(),
        )
        return rec


def demo() -> None:
    """Data-free self-check of the two pieces that carry logic: grouping and shorting."""
    g = group_ports_by_rail({"P1": "VDD/0", "P2": "VDD/1", "P3": "VDD/0"})
    assert g == {"VDD/0": ["P1", "P3"], "VDD/1": ["P2"]}, g

    class FakeModel:                                  # the three attributes short_ports rewrites
        N, P, port = 6, 0, 99
        info: dict = {}
        _fa_pat = None
        ex = {"rail_nodes": {}}
        sheets: dict = {}

        @staticmethod
        def map(x):
            return np.asarray(x, np.int64)            # identity, -1 stays -1

    m = FakeModel()
    m.info = {}
    m.ex = {"rail_nodes": {f"N{i}": (0.0, 0.0, "L", None) for i in range(6)}}
    m.port = 6                                        # no node snaps -> indices 0..5 in dict order
    ports = short_ports(m, [["N0"], ["N3", "N4"]])    # group 0 = the model's own port (P = 0)
    assert ports == [0, 3], ports                     # {3,4} -> 3, so 5 shifts down to 4
    assert m.N == 5 and m.P == 0, (m.N, m.P)
    assert list(m.map([0, 1, 2, 3, 4, 5, -1])) == [0, 1, 2, 3, 3, 4, -1], m.map(np.arange(6))
    try:
        short_ports(m, [["N1"], ["N2"]])              # group 0 is not the model's port -> refused
        raise SystemExit("short_ports accepted a wrong group 0")
    except AssertionError:
        pass
    print("DEMO PASS")


if __name__ == "__main__":
    demo()
