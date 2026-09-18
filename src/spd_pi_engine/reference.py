"""Per-cell plane reference search: `exp1/exp1b.TwoSided` + `exp8/run8.TwoSidedAny` as one class.

Plan C2/C3/C5/C10.  For every rail plane cell the search walks up to `max_layers` conductor
layers above and below and takes the first layer whose reference artwork covers the cell:

    mode="physical-gnd"         the DGND net only          (exp1b.TwoSided, the EXP-5 rule)
    mode="powersi-compatible"   every net except the rail  (run8.TwoSidedAny, EXP-8 cavity wall)

    d_side = sum of the stackup row thicknesses strictly between the two coppers
    d_eff  = d_up d_dn / (d_up + d_dn)   (one side: d_side; neither: the variant-B fallback)
    C_cell = eps0 / sum(t_k / eps_r,k) per side, only for the adjacent conductor (k == 0)
             unless c_all_refs

Ported line for line from the research files.  The three flags that `model3.build` used to inject
after construction (`eps_table`, `c_all_refs`, `c_unit_fix` -- TwoSidedAny's signature was frozen)
are constructor arguments (C3), the block mask goes through `geometry` with an explicit `fast`
(C8), and the two caches (`cache`, `_layer_rasters`) are explicit attributes (C10).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .backend import DEFAULT
from .geometry import fast_layer_mask, rasterize

EPS0 = 8.8541878128e-12


def default_is_gnd(name: str) -> bool:
    """`exp1/model.Stack.is_gnd`: 260729/260804 artwork 'Signal$L02(DGND)', s5m6585 'Plane$IN43_DGND'."""
    return "(DGND)" in name or name.endswith("_DGND") or name.split("$")[-1] == "DGND"


@dataclass
class DesignConventions:
    """Plan C5: the layer/net names the frozen chain hardcodes.  Defaults = the 260729/260804 ones.

    `gnd_sheets` is `model3.GND_SHEETS` (the explicit-GND S3 option only; the frozen rail-only
    path never reads it), `top_layer_name` is the `"Signal$TOP"` special case in `model3.build`
    and `_build_gnd`, `gnd_net` is `TwoSided`'s `gnd_net="DGND"` default and `is_gnd` is
    `Stack.is_gnd`, the fallback rule.
    """

    top_layer_name: str = "Signal$TOP"
    gnd_net: str = "DGND"
    gnd_sheets: tuple = ("Signal$L02(DGND)", "Signal$L13(DGND)", "Signal$L16(DGND)",
                         "Signal$L18(DGND)", "Signal$L24(DGND)", "Signal$L27(DGND)")
    is_gnd: Callable[[str], bool] = default_is_gnd


DEFAULT_CONVENTIONS = DesignConventions()


@dataclass
class Stack:
    """`exp1/model.Stack`, with `is_gnd` taken from the design conventions instead of hardcoded."""

    rows: list  # dicts from extract
    is_gnd_rule: Callable[[str], bool] = default_is_gnd
    cond_names: list = field(default_factory=list)
    z_center_um: dict = field(default_factory=dict)

    def __post_init__(self):
        z = 0.0
        for r in self.rows:
            if r["conductivity"] is not None:
                self.cond_names.append(r["name"])
                self.z_center_um[r["name"]] = z + r["thickness_um"] / 2.0
            z += r["thickness_um"]

    def row(self, name):
        for r in self.rows:
            if r["name"] == name:
                return r
        raise KeyError(name)

    def idx(self, name):
        return [r["name"] for r in self.rows].index(name)

    def is_gnd(self, name):
        return self.is_gnd_rule(name)

    def neighbours(self, name):
        """[(side, conductor_name, dielectric_row, gap_um)] for adjacent conductors."""
        i = self.idx(name)
        out = []
        for side, step in (("above", -1), ("below", 1)):
            j = i + step
            diel = []
            while 0 <= j < len(self.rows) and self.rows[j]["conductivity"] is None:
                diel.append(self.rows[j]); j += step
            if 0 <= j < len(self.rows) and diel:
                out.append((side, self.rows[j]["name"], diel[0], sum(r["thickness_um"] for r in diel)))
        return out

    def nearest_gnd(self, name):
        """(gnd layer, gap_um) nearest GND-named conductor in each direction; gap
        counts dielectric AND intervening conductor thickness."""
        i = self.idx(name)
        res = []
        for step in (-1, 1):
            j = i + step; gap = 0.0; diel = None
            while 0 <= j < len(self.rows):
                r = self.rows[j]
                if r["conductivity"] is not None and self.is_gnd(r["name"]):
                    res.append((r["name"], gap, diel)); break
                if r["conductivity"] is None and diel is None:
                    diel = r
                gap += r["thickness_um"]; j += step
        return res


def eps_tand(row, f):
    """`exp1/model.eps_tand`: (eps_r, tand) of one stackup row at f from its material table."""
    props = row.get("props") or []
    if props:
        fr = np.array([p[0] for p in props]); dk = np.array([p[1] for p in props]); df = np.array([p[2] for p in props])
        lf = np.log10(np.clip(f, fr.min(), fr.max()))
        return float(np.interp(lf, np.log10(fr), dk)), float(np.interp(lf, np.log10(fr), df))
    return float(row["dk"] or 4.0), float(row["df"] or 0.0)


def diel_rows(between):
    return [r for r in between if r["conductivity"] is None]


class ReferenceSearch:
    """`exp1b.TwoSided` and `run8.TwoSidedAny` merged; `mode` picks the reference net set (C2)."""

    def __init__(self, ex, shapes, max_layers=3, mode="powersi-compatible", eps_table=False,
                 c_all_refs=False, c_unit_fix=False, conventions=DEFAULT_CONVENTIONS, backend=DEFAULT):
        if mode not in ("powersi-compatible", "physical-gnd"):
            raise ValueError(f"reference mode {mode!r}")
        self.ex = ex
        self.mode = mode
        self.conv = conventions
        self.backend = backend
        self.st = Stack(ex["stackup"], conventions.is_gnd)
        self.rows = ex["stackup"]
        self.names = [r["name"] for r in self.rows]
        self.shapes = shapes
        self.max_layers = max_layers
        self.gnd = "__ANY__" if mode == "powersi-compatible" else conventions.gnd_net
        self.rail = ex["rail_net"]
        self.report = {}
        self.cache = {}            # C10: (layer, net, block) -> mask
        self._layer_rasters = {}   # C10: EXP-39 whole-layer rasters, read by geometry.fast_layer_mask
        self.eps_table = eps_table
        self.c_all_refs = c_all_refs
        self.c_unit_fix = c_unit_fix

    def candidates(self, L):
        i = self.names.index(L)
        res = {}
        for side, step in (("up", -1), ("down", 1)):
            lst = []
            j = i + step; between = []
            while 0 <= j < len(self.rows) and len(lst) < self.max_layers:
                r = self.rows[j]
                if r["conductivity"] is not None:
                    lst.append((r["name"], list(between)))
                between.append(r)
                j += step
            res[side] = lst
        return res

    def _net_mask(self, layer, net, blk):
        key = (layer, net, blk["x0"], blk["y0"], blk["nx"], blk["ny"], blk["h"])
        if key not in self.cache:
            g = self.shapes.get(layer, {}).get(net)
            if g is None or not g["order"]:
                m = np.zeros((blk["ny"], blk["nx"]), bool)
            else:
                fast = self.backend.fast
                m = fast_layer_mask(self, (layer, net, blk["h"]), g, blk, fast=fast) if fast else None  # EXP-39
                if m is None:
                    m = rasterize(g, blk["h"], window=(blk["x0"], blk["y0"], blk["x0"] + blk["nx"] * blk["h"],
                                                       blk["y0"] + blk["ny"] * blk["h"]), fast=fast)["mask"]
            self.cache[key] = m
        return self.cache[key]

    def mask(self, layer, net, blk):
        """"__ANY__" = the union of every net's artwork on that layer except the rail (run8)."""
        if net != "__ANY__":
            return self._net_mask(layer, net, blk)
        key = (layer, "__ANY__", blk["x0"], blk["y0"], blk["nx"], blk["ny"], blk["h"])
        if key not in self.cache:
            m = np.zeros((blk["ny"], blk["nx"]), bool)
            for nt in self.shapes.get(layer, {}):
                if nt != self.rail:
                    m |= self._net_mask(layer, nt, blk)
            self.cache[key] = m
        return self.cache[key]

    def __call__(self, L, blk):
        shape = (blk["ny"], blk["nx"])
        rail = blk["mask"]
        cand = self.candidates(L)
        d_side = {}; c_side = {}; td_side = {}; who = {}
        cents = {}  # eps_table: [(cells, dielectric rows)] per side, one entry per C assignment
        rep = self.report.setdefault(L, {"blocks": []})
        brep = {"h": blk["h"], "cells": int(rail.sum()), "sides": {}}
        for side in ("up", "down"):
            d = np.full(shape, np.nan); c = np.zeros(shape); td = np.zeros(shape); assigned = np.zeros(shape, bool)
            who_side = np.full(shape, -1, np.int64)  # stackup row index of the assigned conductor, -1 = fallback
            layer_rep = []; ents = []
            for k, (cl, between) in enumerate(cand[side]):
                gm = self.mask(cl, self.gnd, blk) & rail & ~assigned
                cov_total = float((self.mask(cl, self.gnd, blk) & rail).sum() / max(rail.sum(), 1))
                # other nets on that layer over the rail cells (report only)
                others = []
                for net in self.shapes.get(cl, {}):
                    if net == self.gnd:
                        continue
                    om = self.mask(cl, net, blk) & rail
                    fr = float(om.sum() / max(rail.sum(), 1))
                    if fr > 0.01:
                        others.append((net, round(fr, 3)))
                others.sort(key=lambda t: -t[1])
                gap = sum(r["thickness_um"] for r in between)
                layer_rep.append(dict(layer=cl, gap_um=gap, gnd_cover_of_rail=round(cov_total, 3),
                                      newly_assigned=round(float(gm.sum() / max(rail.sum(), 1)), 3), other_nets_cover=others[:4]))
                d[gm] = gap
                who_side[gm] = self.names.index(cl)
                diel = diel_rows(between)
                if (k == 0 or self.c_all_refs) and diel:  # adjacent conductor (k == 0): dielectric-only gap -> capacitance
                    inv = sum(r["thickness_um"] / float(r["dk"] or eps_tand(r, 1e6)[0]) for r in diel)
                    er_dummy, tdv = eps_tand(diel[0], 1e6)
                    if self.c_unit_fix:
                        c[gm] = EPS0 * 1e-12 / inv * 1e6
                    else:
                        c[gm] = EPS0 * 1e-12 / inv
                    td[gm] = tdv
                    if self.eps_table:
                        ents.append((gm, diel))  # gm is never mutated after this point
                assigned |= gm
            d_side[side], c_side[side], td_side[side] = d, c, td
            who[side] = who_side
            cents[side] = ents
            brep["sides"][side] = layer_rep
        du, dd = d_side["up"], d_side["down"]
        both = ~np.isnan(du) & ~np.isnan(dd)
        d_eff = np.where(both, du * dd / np.where(both, du + dd, 1), np.where(np.isnan(du), dd, du))
        none = np.isnan(d_eff)
        d_eff = np.where(none, self.fallback(L), d_eff)
        c_um2 = c_side["up"] + c_side["down"]
        tand = np.where(c_um2 > 0, (c_side["up"] * td_side["up"] + c_side["down"] * td_side["down"]) / np.where(c_um2 > 0, c_um2, 1), 0)
        rc = rail
        brep.update(two_sided=round(float((both & rc).sum() / max(rc.sum(), 1)), 3),
                    single_up=round(float((~np.isnan(du) & np.isnan(dd) & rc).sum() / max(rc.sum(), 1)), 3),
                    single_down=round(float((np.isnan(du) & ~np.isnan(dd) & rc).sum() / max(rc.sum(), 1)), 3),
                    fallback_none=round(float((none & rc).sum() / max(rc.sum(), 1)), 3),
                    d_up_um_median=float(np.nanmedian(du[rc])) if np.any(~np.isnan(du[rc])) else None,
                    d_down_um_median=float(np.nanmedian(dd[rc])) if np.any(~np.isnan(dd[rc])) else None,
                    d_eff_um_median=float(np.median(d_eff[rc])), d_eff_um_mean=float(np.mean(d_eff[rc])),
                    d_B_um=self.fallback(L))
        rep["blocks"].append(brep)
        # per-cell reference info (EXP-12): which conductor row is the wall on each side, and two-sidedness
        out = dict(d_eff=d_eff, c_um2=c_um2, tand=tand, two_sided=both, wall_up=who["up"], wall_dn=who["down"])
        if self.eps_table:
            out["c_tand_at"] = self._c_tand_fn(shape, cents, self.c_unit_fix)
        return out

    @staticmethod
    def _c_tand_fn(shape, cents, c_unit_fix=False):
        """eps_table: f -> (c_um2, tand) with eps_r and tand read from the material table at f."""
        def at(f):
            cs = {}; tds = {}
            for side in ("up", "down"):
                c = np.zeros(shape); td = np.zeros(shape)
                for gm, diel in cents.get(side, []):
                    inv = sum(r["thickness_um"] / eps_tand(r, f)[0] for r in diel)
                    if c_unit_fix:
                        c[gm] = EPS0 * 1e-12 / inv * 1e6
                    else:
                        c[gm] = EPS0 * 1e-12 / inv
                    td[gm] = eps_tand(diel[0], f)[1]
                cs[side], tds[side] = c, td
            cu = cs["up"] + cs["down"]
            return cu, np.where(cu > 0, (cs["up"] * tds["up"] + cs["down"] * tds["down"]) / np.where(cu > 0, cu, 1), 0)
        return at

    def fallback(self, L):
        # variant-B rule (model.build_model): adjacent DGND-named layers, else nearest by name
        nb = self.st.neighbours(L)
        adj = [gap for side, c, diel, gap in nb if self.st.is_gnd(c)]
        if adj:
            return 1.0 / sum(1.0 / x for x in adj)
        return min(g[1] for g in self.st.nearest_gnd(L))
