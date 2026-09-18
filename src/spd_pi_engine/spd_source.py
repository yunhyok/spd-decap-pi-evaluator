"""SPD source: port enumeration, extraction and the cached `prepare` (plan C13/C14/C16/C19).

`exp1/extract.py` + `exp1b.load_layer_shapes` + `exp5/pipeline.prepare`, ported line for line.
The design id table (`common/paths.DESIGNS`, `pipeline.SPD/REF`) is gone: every entry point takes
an SPD path (C13), the port list comes from the SPD port block instead of the reference npz (C19),
and the cache is `cache.CacheDir` keyed by the SPD sha256 (C14).

Every private call into the product parser (C16) is in the adapter section at the top of this
module; `SPD_PARSER_SYMBOLS` names them and `check_parser_api()` (run at import) fails early with
the missing names if the product parser changes.  No file is written at import time (C12).
"""
from __future__ import annotations

import hashlib
import mmap
import re
import time
from pathlib import Path

import numpy as np

from .cache import CacheDir
from .reference import DEFAULT_CONVENTIONS, ReferenceSearch

# ---------------------------------------------------------------------------
# C16 adapter -- the ONLY place the engine touches the product's private SPD API.
# ---------------------------------------------------------------------------
from spd_decap_pi._core.io import spd as _P  # noqa: E402
from spd_decap_pi._core.models.spice import SpiceModelError, parse_passive_subcircuit  # noqa: E402

#: Private `spd_decap_pi._core.io.spd` symbols this module calls (research: `extract.py:28,98-254`,
#: `exp1b.py:35,44-67`).  `check_parser_api()` asserts they all still exist.
SPD_PARSER_SYMBOLS = (
    "_parse_materials",      # src/spd_decap_pi/_core/io/spd.py:3292
    "_parse_layers",         # :3378
    "_parse_shapes",         # :2962
    "_parse_padstacks",      # :3473
    "_parse_partial_circuits",  # :3576 -> models.spice.parse_passive_subcircuit
    "_parse_metadata",       # :3660
    "_find_line",
    "_line_end",
    "_length_um",
    "_Reporter",
)


def check_parser_api() -> None:
    """Raise with the missing names if the product SPD parser no longer exposes what we call."""
    missing = [s for s in SPD_PARSER_SYMBOLS if not hasattr(_P, s)]
    if missing:
        raise RuntimeError(
            "spd_decap_pi._core.io.spd is missing %s -- the product SPD parser changed; "
            "update the adapter section of spd_pi_engine/spd_source.py (plan C16). Present: %s"
            % (", ".join(missing), ", ".join(sorted(n for n in dir(_P) if n.startswith("_parse"))))
        )


check_parser_api()


def _reporter(reporter=None):
    return reporter if reporter is not None else _P._Reporter(None, None)


def _mm_um(tok: bytes) -> float:
    return _P._length_um(tok)


# ---------------------------------------------------------------------------


def sha256_of(path) -> str:
    """sha256 of a file, read in 1 MiB chunks (the cache key, plan C14)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_RX_PORT_HDR = re.compile(rb"(?m)^(Port\d+_(\S+?)::(\S+))\s")


def list_ports(spd_path) -> list[str]:
    """Port names from the SPD `.Port` block, in file order (plan C19).

    Same header regex as `extract` (`exp1/extract.py:101-116`); the returned short name
    ("Port18_SITE0") is what `extract`/`prepare` take and what the reference npz `port_names`
    become when split on "::".
    """
    with open(spd_path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as d:
        pstart = d.find(b"* Port description lines")
        if pstart < 0:
            raise ValueError(f"{spd_path}: no '* Port description lines' section")
        pend = d.find(b".EndPort", pstart)
        pend = len(d) if pend < 0 else pend
        return [m.group(1).split(b"::")[0].decode() for m in _RX_PORT_HDR.finditer(d, pstart, pend)]


# ---------------------------------------------------------------- subckt fallback
# spd._parse_partial_circuits wraps a .PartialCkt body in ".SUBCKT name n1 n2 ... .ENDS"
# and hands it to parse_passive_subcircuit.  That rejects the s5m6585 flavour, where the
# body is "xcall 1 2 sub_X" plus a nested ".SUBCKT sub_X port1 port2 ... .ENDS" holding the
# same flat Murata R/L/C ladder ("nested .SUBCKT is unsupported").  Below we lift that inner
# subcircuit out and feed it to the very same parser, so the model object, its MNA solver and
# its validation are the product's -- no second evaluator.
_RX_PARTIAL = re.compile(
    rb"^\.PartialCkt\s+(\S+)\s+ExtNode\s*=([^\n]*(?:\n\+[^\n]*)*)\n(.*?)^\.EndPartialCkt",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RX_XCALL = re.compile(r"^\s*xcall\s+(\S+)\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_RX_SUBCKT = re.compile(
    r"^\s*\.SUBCKT\s+(\S+)\s+(\S+)\s+(\S+)\s*$(.*?)^\s*\.ENDS\b[^\n]*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def subckt_fallback_models(data, start, end, canonical, empty, freqs=(1e3, 1e6), log=None) -> dict:
    """Two-port PartialCkts the product parser skipped, recovered via their inner .SUBCKT.

    Returns {model_id: (PassiveSubcircuitModel, subckt_text)} for names not already in
    ``canonical``/``empty``.  The text is what cache v2 stores instead of the object (C15).
    """
    log = log or (lambda *a, **k: None)
    found = {}
    for m in _RX_PARTIAL.finditer(bytes(data[start:end])):
        name = m.group(1).decode()
        key = name.casefold()
        if key in canonical or key in empty:
            continue
        ext = m.group(2).replace(b"\n+", b" ").split()
        if len(ext) != 2:
            continue
        body = m.group(3).decode("utf-8", errors="replace")
        call = _RX_XCALL.search(body)
        sub = _RX_SUBCKT.search(body)
        if not (call and sub and sub.group(1).casefold() == call.group(3).casefold()):
            continue
        # xcall arg i wires ExtNode name -> inner port i; keep the PartialCkt ExtNode order
        port_of = {call.group(1): sub.group(2), call.group(2): sub.group(3)}
        try:
            terminals = [port_of[e.decode()] for e in ext]
        except KeyError:
            continue
        text = ".SUBCKT %s %s %s\n%s\n.ENDS\n" % (name, terminals[0], terminals[1], sub.group(4))
        try:
            model = parse_passive_subcircuit(text, source_name="subckt_fallback:" + name)
            model.impedance(freqs)
        except (SpiceModelError, ValueError) as exc:
            log("[extract] fallback rejected %s: %s" % (name, exc))
            continue
        found[model.model_id] = (model, text)
    return found


def extract(spd_path, port, gnd_net: str = "DGND", reporter=None, log=None) -> dict:
    """Geometry + circuit extraction for one rail (one SPD port): `exp1/extract.extract`.

    `reporter` is the product's progress reporter (`spd._Reporter`, default a silent one),
    `log` the engine's progress callback (default no-op; the research code printed).
    Adds `model_texts` (model_id -> .SUBCKT source) so cache v2 can drop the product objects.
    """
    log = log or (lambda *a, **k: None)
    spd_path = str(spd_path)
    port_name = str(port)
    t0 = time.time()
    out: dict = {"spd_path": spd_path, "port_name": port_name, "gnd_net": gnd_net}
    diags: list = []
    rep = _reporter(reporter)
    with open(spd_path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as d:
        # ---------------- port definition --------------------------------
        pstart = d.find(b"* Port description lines")
        # port header line: "Port18_SITE0::NET Auto ..." ; accept either the
        # short name, or the "2nd_SITE0-NET/0" alias via net+site
        m = None
        rx_hdr = _RX_PORT_HDR
        headers = [(mm.start(), mm) for mm in rx_hdr.finditer(d, pstart)]
        want = port_name.encode()
        for i, (pos, mm) in enumerate(headers):
            full = mm.group(1)
            alias = b"2nd_" + mm.group(2) + b"-" + mm.group(3)
            if want in (full, alias, full.split(b"::")[0]):
                end = headers[i + 1][0] if i + 1 < len(headers) else d.find(b".EndPort", pos)
                m = (pos, end, mm)
                break
        if m is None:
            raise KeyError(f"port {port_name} not found in {spd_path}")
        pos, end, mm = m
        block = d[pos:end]
        rail = mm.group(3).decode()
        out["rail_net"] = rail
        out["port_header"] = mm.group(1).decode()
        neg_i = block.find(b"NegativeTerminal")
        pos_blk, neg_blk = block[:neg_i], block[neg_i:]
        rx_ref = re.compile(rb"\$Package\.(Node\d+)(?:!![^:\s]*)?::(\S+)")
        out["port_pos_nodes"] = [a.group(1).decode() for a in rx_ref.finditer(pos_blk)]
        out["port_neg_nodes"] = [a.group(1).decode() for a in rx_ref.finditer(neg_blk)]
        out["port_pos_nets"] = sorted({a.group(2).decode() for a in rx_ref.finditer(pos_blk)})
        out["port_neg_nets"] = sorted({a.group(2).decode() for a in rx_ref.finditer(neg_blk)})
        log(f"[extract] port {out['port_header']}: +{len(out['port_pos_nodes'])} -{len(out['port_neg_nodes'])}")

        # ---------------- materials / stackup ----------------------------
        mat0 = d.find(b"* Material description lines")
        mat0 = 0 if mat0 < 0 else mat0
        mend = _P._find_line(d, b".EndMaterial", mat0)
        mend = len(d) if mend < 0 else _P._line_end(d, mend, len(d))
        dielectrics, metals = _P._parse_materials(d, mat0, mend, diags)
        lay0 = d.find(b"* Layer description lines")
        lay1 = d.find(b"* ConformalLayer description lines")
        layers = _P._parse_layers(d, lay0, lay1, {}, dielectrics, metals, set(), diags)
        out["stackup"] = [
            dict(
                name=L.name,
                thickness_um=L.thickness_um,
                conductivity=L.conductivity_s_m,
                dk=L.dk,
                df=L.df,
                material=L.material,
                props=[(p.frequency_hz, p.dk, p.df) for p in L.dielectric_properties],
            )
            for L in layers
        ]
        out["stackup_layers_obj"] = layers  # pydantic objects (for via_model); cache v2 stores dicts
        # default trace width per conductor layer ("Width = ..." on the layer row
        # continuation) used for Trace records that carry no Width attribute
        lw = {}
        for a in re.finditer(rb"(?m)^(Signal\$\S+) Thickness[^\n]*\n\+[^\n]*Width = (\S+)", d[lay0:lay1]):
            lw[a.group(1).decode()] = _mm_um(a.group(2))
        out["layer_default_width_um"] = lw
        log(f"[extract] stackup {len(layers)} rows, {time.time()-t0:.1f}s")

        # ---------------- rail shapes -----------------------------------
        first_shape = _P._find_line(d, b".Shape")
        _outline, _ln, _nets, geoms = _P._parse_shapes(
            d, first_shape, lay0, {rail.casefold()}, {rail.casefold()}, rep, diags
        )
        out["rail_geoms"] = [
            dict(
                layer=g.layer,
                pos_polys=[np.asarray(list(p), float) for p in g.positive_polygons_um],
                neg_polys=[np.asarray(list(p), float) for p in g.negative_polygons_um],
                pos_circles=list(g.positive_circles_um),
                neg_circles=list(g.negative_circles_um),
                order=list(g.primitive_order),
            )
            for g in geoms
        ]
        log(f"[extract] rail shapes on {[g.layer for g in geoms]}, {time.time()-t0:.1f}s")

        # ---------------- nodes ------------------------------------------
        n0 = d.find(b"* Node description lines")
        t_0 = d.find(b"* Trace description lines")
        v_0 = d.find(b"* Via description lines")
        w_0 = d.find(b"* WirebondDefinition description lines")
        rnet = re.escape(rail.encode())
        rx_node = re.compile(
            rb"(?m)^(Node\d+)(?:!![^:\s]*)?::(" + rnet + rb"|" + re.escape(gnd_net.encode()) +
            rb") X = (\S+) Y = (\S+) Layer = (\S+)(?: PadStack = (\S+))?"
        )
        rail_nodes: dict[str, tuple] = {}
        gnd_xy: dict[str, list] = {}
        gnd_nodes_needed = set(out["port_neg_nodes"])
        gnd_nodes: dict[str, tuple] = {}
        for a in rx_node.finditer(d, n0, t_0):
            x = _mm_um(a.group(3)); y = _mm_um(a.group(4)); lay = a.group(5).decode()
            nid = a.group(1).decode()
            if a.group(2) == rail.encode():
                rail_nodes[nid] = (x, y, lay, a.group(6).decode() if a.group(6) else None)
            else:
                gnd_xy.setdefault(lay, []).append((x, y))
                if nid in gnd_nodes_needed:
                    gnd_nodes[nid] = (x, y, lay, a.group(6).decode() if a.group(6) else None)
        out["rail_nodes"] = rail_nodes
        out["gnd_xy_by_layer"] = {k: np.asarray(v, float) for k, v in gnd_xy.items()}
        out["gnd_port_nodes"] = gnd_nodes
        log(f"[extract] rail nodes {len(rail_nodes)}, gnd nodes {sum(len(v) for v in gnd_xy.values())}, {time.time()-t0:.1f}s")

        # ---------------- traces (width may be on continuation line) ----
        rx_tr = re.compile(
            rb"(?m)^Trace\d+::" + rnet + rb" StartingNode = (Node\d+)\S* EndingNode = (Node\d+)\S*([^\n]*)\n(\+[^\n]*)?"
        )
        traces = []
        for a in rx_tr.finditer(d, t_0, v_0):
            tail = (a.group(3) or b"") + b" " + (a.group(4) or b"")
            wm = re.search(rb"Width = (\S+)", tail)
            traces.append((a.group(1).decode(), a.group(2).decode(), _mm_um(wm.group(1)) if wm else None))
        out["rail_traces"] = traces
        log(f"[extract] rail traces {len(traces)} (no width: {sum(t[2] is None for t in traces)}), {time.time()-t0:.1f}s")

        # ---------------- vias -------------------------------------------
        rx_via = re.compile(rb"(?m)^Via\d+::" + rnet + rb" UpperNode = (Node\d+)\S* LowerNode = (Node\d+)\S* PadStack = (\S+)")
        out["rail_vias"] = [(a.group(1).decode(), a.group(2).decode(), a.group(3).decode()) for a in rx_via.finditer(d, v_0, w_0)]
        log(f"[extract] rail vias {len(out['rail_vias'])}, {time.time()-t0:.1f}s")

        # ---------------- padstacks --------------------------------------
        ps0 = d.find(b"* PadStack collection description lines")
        ps1 = _P._find_line(d, b".PartialCkt", ps0)
        pads = _P._parse_padstacks(d, ps0, ps1, diags)
        out["padstacks"] = {
            p.name: dict(drill_um=p.drill_diameter_um, pad_w=p.pad_width_um, pad_h=p.pad_height_um,
                         layers=list(p.layers), material=p.material)
            for p in pads
        }

        # ---------------- decap models + connections ---------------------
        c0 = d.find(b"* Circuit description lines")
        c0 = ps1 if c0 < 0 else c0
        conn0 = _P._find_line(d, b".Connect", c0)
        models, assets, canonical, empty, _n = _P._parse_partial_circuits(
            d, c0, conn0, (1e3, 1e6), Path(spd_path).name, rep, diags
        )
        texts = {mid: assets[f"{mid}.lib"] for mid in models}
        fallback = subckt_fallback_models(d, c0, conn0, canonical, empty, log=log)
        for name, (mdl, text) in fallback.items():
            models[mdl.model_id] = mdl
            texts[mdl.model_id] = text
            canonical[name.casefold()] = mdl.model_id
        # standard = flat multi-element ladder, ideal = one R/L/C between the ext nodes
        model_source = {
            mid: "subckt" if mid in fallback else ("ideal" if len(m.elements) == 1 else "standard")
            for mid, m in models.items()
        }
        if fallback:
            log(f"[extract] subckt fallback models: {sorted(fallback)}")
        cend = _P._find_line(d, b".EndCompCollection", conn0)
        cend = len(d) if cend < 0 else _P._line_end(d, cend, len(d))
        parts, comps, conns = _P._parse_metadata(d, conn0, cend, diags)
        decaps = []
        for c in conns:
            nets = {p.net for p in c.ports}
            if rail not in nets:
                continue
            key = c.part_name.casefold()
            if key not in canonical:
                diags.append(("skip_component_no_2port_model", c.refdes, c.part_name))
                continue
            if len(c.ports) != 2:
                continue
            rp = [p for p in c.ports if p.net == rail]
            gp = [p for p in c.ports if p.net != rail]
            decaps.append(dict(refdes=c.refdes, part=c.part_name, model_id=canonical[key],
                               model_source=model_source[canonical[key]],
                               rail_node=rp[0].node_id, gnd_node=gp[0].node_id if gp else None,
                               gnd_net=gp[0].net if gp else None, usage=c.usage))
        out["decaps"] = decaps
        # research: `{x["model_id"] for x in decaps}` -- a set, so the dict order of `models`
        # varied per process (PYTHONHASHSEED).  Same content, but a cache file should be
        # reproducible, so keep the decap order.
        used = list(dict.fromkeys(x["model_id"] for x in decaps))
        out["models"] = {mid: models[mid] for mid in used}
        out["model_texts"] = {mid: texts[mid] for mid in used}  # cache v2 (C15)
        out["other_rail_components"] = [
            (c.refdes, c.part_name, len(c.ports)) for c in conns
            if rail in {p.net for p in c.ports} and c.part_name.casefold() not in canonical
        ]
        log(f"[extract] decaps on rail: {len(decaps)}; other comps: {len(out['other_rail_components'])}; {time.time()-t0:.1f}s")
    out["diagnostics"] = [str(x) for x in diags if not hasattr(x, "severity") or x.severity != "info"]
    out["extract_seconds"] = time.time() - t0
    return out


def load_layer_shapes(spd_path, layers, reporter=None, log=None) -> dict:
    """All-net ordered shape primitives for the given conductor layers (`exp1b.load_layer_shapes`;
    section-limited call of spd._parse_shapes, src/spd_decap_pi/_core/io/spd.py:2962)."""
    log = log or (lambda *a, **k: None)
    out = {}
    rep = _reporter(reporter)
    with open(spd_path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as d:
        for L in layers:
            hdr = f".Shape {L}pkgshape".encode()
            s0 = _P._find_line(d, hdr)
            if s0 < 0:
                out[L] = {}
                continue
            s1 = _P._find_line(d, b".EndShape", s0)
            diags = []
            _o, _ln, _nets, geoms = _P._parse_shapes(d, s0, s1, set(), None, rep, diags)
            out[L] = {
                g.net: dict(layer=g.layer, pos_polys=[np.asarray(list(p), float) for p in g.positive_polygons_um],
                            neg_polys=[np.asarray(list(p), float) for p in g.negative_polygons_um],
                            pos_circles=list(g.positive_circles_um), neg_circles=list(g.negative_circles_um),
                            order=list(g.primitive_order))
                for g in geoms
            }
            log(f"[shapes] {L}: nets {len(out[L])}")
    return out


def prepare(spd_path, port, cache_dir, max_layers=3, log=None, conventions=DEFAULT_CONVENTIONS,
            reporter=None):
    """`(ex, shapes, fine_box)` for one SPD port, through the engine cache (`exp5/pipeline.prepare`).

    Cache v2 (`cache.CacheDir`) replaces the design-id pkl names: the extract is keyed by the SPD
    sha256 + port + max_layers, the neighbour-layer shapes by the SPD sha256 + layer, so the shapes
    are shared by every port of a design and concurrent writers never touch the same file.
    """
    log = log or (lambda *a, **k: None)
    spd_path = str(spd_path)
    cache = CacheDir(cache_dir)
    sha = sha256_of(spd_path)

    ex = cache.load_extract(sha, port, max_layers)
    if ex is None:
        ex = extract(spd_path, port, gnd_net=conventions.gnd_net, reporter=reporter, log=log)
        cache.save_extract(sha, port, max_layers, ex)
    # the candidate walk is stackup-only, so the reference mode does not matter here (exp1b.TwoSided)
    ts = ReferenceSearch(ex, {}, max_layers, mode="physical-gnd", conventions=conventions)
    need = set()
    for g in ex["rail_geoms"]:
        for side, lst in ts.candidates(g["layer"]).items():
            need.update(cl for cl, _ in lst)
    shapes = {}
    miss = []
    for L in sorted(need):
        g = cache.load_shapes(sha, L)
        if g is None:
            miss.append(L)
        else:
            shapes[L] = g
    if miss:
        fresh = load_layer_shapes(spd_path, miss, reporter=reporter, log=log)
        shapes.update(fresh)
        for L, g in fresh.items():
            cache.save_shapes(sha, L, g)
    rn = ex["rail_nodes"]
    P = np.array([(rn[x][0], rn[x][1]) for x in ex["port_pos_nodes"] if x in rn])
    fine_box = (P[:, 0].min() - 1000.0, P[:, 1].min() - 1000.0, P[:, 0].max() + 1000.0, P[:, 1].max() + 1000.0)
    return ex, shapes, fine_box
