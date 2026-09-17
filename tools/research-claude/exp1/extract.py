"""EXP-1 geometry/circuit extraction for one rail + one SPD port.

Reuses the product's mmap SPD parser helpers (read-only imports):
  - spd._parse_materials          src/spd_decap_pi/_core/io/spd.py:3292
  - spd._parse_layers             src/spd_decap_pi/_core/io/spd.py:3378
  - spd._parse_shapes             src/spd_decap_pi/_core/io/spd.py:2962
  - spd._parse_padstacks          src/spd_decap_pi/_core/io/spd.py:3473
  - spd._parse_partial_circuits   src/spd_decap_pi/_core/io/spd.py:3576
    (-> models.spice.parse_passive_subcircuit, _core/models/spice.py:374)
  - spd._parse_metadata           src/spd_decap_pi/_core/io/spd.py:3660
Node / Trace / Via / .Port records are parsed here with small regexes
(the product parser has no PortBegin/PositiveTerminal reader).

Output: a pickle with plain python/numpy data (no product objects except
PassiveSubcircuitModel instances, which are needed to evaluate Z(f)).
"""
from __future__ import annotations

import argparse
import mmap
import pickle
import re
import time
from pathlib import Path

import numpy as np

from spd_decap_pi._core.io import spd as P
from spd_decap_pi._core.models.spice import SpiceModelError, parse_passive_subcircuit

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import spd_path as design_spd, work_file  # noqa: E402


def _mm_um(tok: bytes) -> float:
    return P._length_um(tok)


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


def subckt_fallback_models(data, start, end, canonical, empty, freqs=(1e3, 1e6)) -> dict:
    """Two-port PartialCkts the product parser skipped, recovered via their inner .SUBCKT.

    Returns {model_id: PassiveSubcircuitModel} for names not already in ``canonical``/``empty``.
    """
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
            print("[extract] fallback rejected %s: %s" % (name, exc), flush=True)
            continue
        found[model.model_id] = model
    return found


def extract(spd_path: str, port_name: str, gnd_net: str = "DGND") -> dict:
    t0 = time.time()
    out: dict = {"spd_path": spd_path, "port_name": port_name, "gnd_net": gnd_net}
    diags: list = []
    rep = P._Reporter(None, None)
    with open(spd_path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as d:
        # ---------------- port definition --------------------------------
        pstart = d.find(b"* Port description lines")
        # port header line: "Port18_SITE0::NET Auto ..." ; accept either the
        # short name, or the "2nd_SITE0-NET/0" alias via net+site
        m = None
        rx_hdr = re.compile(rb"(?m)^(Port\d+_(\S+?)::(\S+))\s")
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
            raise SystemExit(f"port {port_name} not found")
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
        print(f"[extract] port {out['port_header']}: +{len(out['port_pos_nodes'])} -{len(out['port_neg_nodes'])}", flush=True)

        # ---------------- materials / stackup ----------------------------
        mat0 = d.find(b"* Material description lines")
        mat0 = 0 if mat0 < 0 else mat0
        mend = P._find_line(d, b".EndMaterial", mat0)
        mend = len(d) if mend < 0 else P._line_end(d, mend, len(d))
        dielectrics, metals = P._parse_materials(d, mat0, mend, diags)
        lay0 = d.find(b"* Layer description lines")
        lay1 = d.find(b"* ConformalLayer description lines")
        layers = P._parse_layers(d, lay0, lay1, {}, dielectrics, metals, set(), diags)
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
        out["stackup_layers_obj"] = layers  # pydantic objects (for via_model)
        # default trace width per conductor layer ("Width = ..." on the layer row
        # continuation) used for Trace records that carry no Width attribute
        lw = {}
        for a in re.finditer(rb"(?m)^(Signal\$\S+) Thickness[^\n]*\n\+[^\n]*Width = (\S+)", d[lay0:lay1]):
            lw[a.group(1).decode()] = _mm_um(a.group(2))
        out["layer_default_width_um"] = lw
        print(f"[extract] stackup {len(layers)} rows, {time.time()-t0:.1f}s", flush=True)

        # ---------------- rail shapes -----------------------------------
        first_shape = P._find_line(d, b".Shape")
        _outline, _ln, _nets, geoms = P._parse_shapes(
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
        print(f"[extract] rail shapes on {[g.layer for g in geoms]}, {time.time()-t0:.1f}s", flush=True)

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
        print(f"[extract] rail nodes {len(rail_nodes)}, gnd nodes {sum(len(v) for v in gnd_xy.values())}, {time.time()-t0:.1f}s", flush=True)

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
        print(f"[extract] rail traces {len(traces)} (no width: {sum(t[2] is None for t in traces)}), {time.time()-t0:.1f}s", flush=True)

        # ---------------- vias -------------------------------------------
        rx_via = re.compile(rb"(?m)^Via\d+::" + rnet + rb" UpperNode = (Node\d+)\S* LowerNode = (Node\d+)\S* PadStack = (\S+)")
        out["rail_vias"] = [(a.group(1).decode(), a.group(2).decode(), a.group(3).decode()) for a in rx_via.finditer(d, v_0, w_0)]
        print(f"[extract] rail vias {len(out['rail_vias'])}, {time.time()-t0:.1f}s", flush=True)

        # ---------------- padstacks --------------------------------------
        ps0 = d.find(b"* PadStack collection description lines")
        ps1 = P._find_line(d, b".PartialCkt", ps0)
        pads = P._parse_padstacks(d, ps0, ps1, diags)
        out["padstacks"] = {
            p.name: dict(drill_um=p.drill_diameter_um, pad_w=p.pad_width_um, pad_h=p.pad_height_um,
                         layers=list(p.layers), material=p.material)
            for p in pads
        }

        # ---------------- decap models + connections ---------------------
        c0 = d.find(b"* Circuit description lines")
        c0 = ps1 if c0 < 0 else c0
        conn0 = P._find_line(d, b".Connect", c0)
        models, _assets, canonical, empty, _n = P._parse_partial_circuits(
            d, c0, conn0, (1e3, 1e6), Path(spd_path).name, rep, diags
        )
        fallback = subckt_fallback_models(d, c0, conn0, canonical, empty)
        for name, mdl in fallback.items():
            models[mdl.model_id] = mdl
            canonical[name.casefold()] = mdl.model_id
        # standard = flat multi-element ladder, ideal = one R/L/C between the ext nodes
        model_source = {
            mid: "subckt" if mid in fallback else ("ideal" if len(m.elements) == 1 else "standard")
            for mid, m in models.items()
        }
        if fallback:
            print(f"[extract] subckt fallback models: {sorted(fallback)}", flush=True)
        cend = P._find_line(d, b".EndCompCollection", conn0)
        cend = len(d) if cend < 0 else P._line_end(d, cend, len(d))
        parts, comps, conns = P._parse_metadata(d, conn0, cend, diags)
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
        out["models"] = {mid: models[mid] for mid in {x["model_id"] for x in decaps}}
        out["other_rail_components"] = [
            (c.refdes, c.part_name, len(c.ports)) for c in conns
            if rail in {p.net for p in c.ports} and c.part_name.casefold() not in canonical
        ]
        print(f"[extract] decaps on rail: {len(decaps)}; other comps: {len(out['other_rail_components'])}; {time.time()-t0:.1f}s", flush=True)
    out["diagnostics"] = [str(x) for x in diags if not hasattr(x, "severity") or x.severity != "info"]
    out["extract_seconds"] = time.time() - t0
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spd", default=str(design_spd("260729")))
    ap.add_argument("--port", default="Port18_SITE0")
    ap.add_argument("--out", default=str(work_file("exp1", "extract_port18.pkl")))
    a = ap.parse_args()
    res = extract(a.spd, a.port)
    with open(a.out, "wb") as fh:
        pickle.dump(res, fh)
    print("saved", a.out)


if __name__ == "__main__":
    main()
