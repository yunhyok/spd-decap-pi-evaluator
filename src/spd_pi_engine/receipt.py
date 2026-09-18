"""Receipt v1 and the `Result` a solve returns (plan §2-3 / §4 W4).

The receipt is `exp11/run11.run()`'s output with the reference-dependent fields made optional:
everything the engine knows by itself is always there, and `attach_reference()` adds `Zref_*` and
the G1-G5 `ladder_gates` when a PowerSI reference is available.  `resonance`, `fit_dl` and
`ladder_gates` are ported verbatim from `exp1/run_exp1.py`, `exp8/run8.py` and `exp3/run3.py` so a
receipt can be gated without the research tree (plan §0: the research code stays untouched).

`numerics_id` is the fingerprint an app compares across engine versions: the reference mode, the
16 flags, the mesh settings, the design conventions and the source bytes of the five numeric
modules.  Two receipts with the same `numerics_id` came out of the same numerics.

`validity` is a contract field (plan risk §5-7): the apps must display it, because the engine is
validated for PCB and large-plane package rails only.
"""
from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .reference import eps_tand

RECEIPT_VERSION = 1

#: The modules whose bytes define the numerics (plan §2-3).
NUMERIC_MODULES = ("geometry.py", "homogenise.py", "solver.py", "reference.py", "model.py")

#: Plan risk §5-7 / PROGRESS_SUMMARY_2026-09-18 §4.  Fixed text; an app shows it next to Z(f).
VALIDITY_NOTES = [
    "PCB (s5m6585, held-out, 160 ports): 1 MHz err median 0.58 % (IQR 0.34-1.29), G3 PASS 128/160.",
    "Package large-plane rails: err <= 3 % (260729/260804 representative rails 0.8-3 %).",
    "Package overall: 1 MHz err median 31.7 %, Re Z_ref/Re Z_model @100 kHz median 1.46 "
    "(feed-path R convention unresolved -- PowerSI microvia model definition needed).",
    "G4 f_res bias +5-14 % on every case (design-independent, cause not established).",
]


# ---------------------------------------------------------------- numerics_id
@functools.lru_cache(maxsize=1)
def source_hashes() -> tuple:
    """sha256/16 of each numeric module's bytes (lazy: nothing is read at import time)."""
    here = Path(__file__).parent
    return tuple((n, hashlib.sha256((here / n).read_bytes()).hexdigest()[:16]) for n in NUMERIC_MODULES)


def conventions_dict(conv) -> dict:
    """`DesignConventions` as JSON (the `is_gnd` rule by name -- it is a function)."""
    return dict(top_layer_name=conv.top_layer_name, gnd_net=conv.gnd_net,
                gnd_sheets=list(conv.gnd_sheets),
                is_gnd=getattr(conv.is_gnd, "__qualname__", repr(conv.is_gnd)))


def numerics_id(reference_mode: str, flags: dict, mesh: dict, conventions: dict) -> str:
    """sha256 over the reference mode, the sorted flags, the mesh, the conventions and the source.

    `fine_box` is excluded: it is the port's own geometry, not a numeric rule, and an id that
    changed per port could not answer "did the numerics change" (W4 deviation 1).
    """
    payload = dict(reference_mode=reference_mode, flags=sorted(flags.items()),
                   mesh=sorted((k, v) for k, v in mesh.items() if k != "fine_box"),
                   conventions=sorted(conventions.items()), sources=source_hashes())
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def decap_config_sha256(config: dict) -> str:
    """sha256 of `{refdes: model_id | None}`.

    W8's `set_decaps(config)` hands this the same dict shape, so an unmounted decap (None) gives a
    different id than a mounted one and a receipt identifies its decap configuration exactly.
    """
    return hashlib.sha256(json.dumps(sorted((str(k), v) for k, v in config.items()),
                                     default=str).encode()).hexdigest()


def dielectric_used(ex: dict, eps_table: bool) -> dict:
    """`run11.dielectric_used`: eps_r / tand actually used, per distinct dielectric material."""
    out = {}
    for r in ex["stackup"]:
        if r["conductivity"] is not None or str(r["material"]) in out:
            continue
        if eps_table:
            out[str(r["material"])] = {f"{f:.0e}": dict(eps_r=eps_tand(r, f)[0], tand=eps_tand(r, f)[1])
                                       for f in (1e3, 1e6, 1e7, 1e8)}
        else:
            out[str(r["material"])] = dict(eps_r=float(r["dk"] or eps_tand(r, 1e6)[0]), tand=eps_tand(r, 1e6)[1],
                                           frequency_independent=True)
    return out


# ---------------------------------------------------------------- reference gates (verbatim ports)
def resonance(f, z):
    """`exp1/run_exp1.resonance`: min |Z| with a parabolic refinement in (log f, log|Z|)."""
    k = int(np.argmin(abs(z)))
    if 0 < k < len(f) - 1:
        x = np.log(f[k - 1:k + 2]); y = np.log(abs(z[k - 1:k + 2]))
        c = np.polyfit(x, y, 2)
        if c[0] > 0:
            xm = -c[1] / (2 * c[0])
            return float(np.exp(xm)), float(np.exp(np.polyval(c, xm)))
    return float(f[k]), float(abs(z[k]))


def fit_dl(f, dIm, lo=2.9e4, hi=1.001e6):
    """`exp8/run8.fit_dl`: least-squares dL over the 29 kHz - 1 MHz window."""
    w = 2 * np.pi * f
    m = (f >= lo) & (f <= hi)
    A = np.column_stack([w[m], -1.0 / w[m]])
    c, *_ = np.linalg.lstsq(A, dIm[m], rcond=None)
    return float(c[0] * 1e12)


def ladder_gates(freq, Z, zr, fres_m, fres_r):
    """`exp3/run3.ladder_gates` verbatim: G1-G5 and the frozen PASS rule (D3, CLAUDE.md §3)."""
    w = 2 * np.pi * freq
    d = {}
    lo = (freq >= 1e3) & (freq <= 1.001e5)
    d["G1_max_abs_dRe_mOhm"] = float(np.max(np.abs(Z.real - zr.real)[lo]) * 1e3)
    m2 = (freq >= 0.999e5) & (freq <= 1.001e6)
    dl = (Z.imag - zr.imag)[m2] / w[m2] * 1e12
    d["G2_dL_pH_range"] = [float(dl.min()), float(dl.max())]
    i1 = int(np.argmin(abs(freq - 1e6)))
    d["G3_rel_err_1MHz"] = float(abs(Z[i1] - zr[i1]) / abs(zr[i1]))
    m4 = (freq >= 0.999e6) & (freq <= 1.001e7)
    d["G4_max_rel_err"] = float(np.max(np.abs(Z - zr)[m4] / np.abs(zr)[m4]))
    d["G4_f_res_model_Hz"] = fres_m
    d["G4_f_res_rel_err_vs_1.585MHz"] = float(abs(fres_m - 1.585e6) / 1.585e6)
    m5 = freq > 1.001e7
    d["G5_rel_err"] = [float(x) for x in (np.abs(Z - zr)[m5] / np.abs(zr)[m5])]
    d["PASS"] = dict(G1=bool(d["G1_max_abs_dRe_mOhm"] <= 0.05), G2=bool(max(abs(dl.min()), abs(dl.max())) <= 5.0),
                     G3=bool(d["G3_rel_err_1MHz"] < 0.10),
                     G4=bool(d["G4_max_rel_err"] < 0.20 and d["G4_f_res_rel_err_vs_1.585MHz"] < 0.10))
    return d


def attach_reference(receipt: dict, ref_freq, ref_Z) -> dict:
    """Add `Zref_re/Zref_im`, `ladder_gates` and the `run11` error scalars to a receipt, in place.

    `ref_freq`/`ref_Z` are the *full* PowerSI grid (the reference npz columns): the receipt's own
    frequencies are matched to it by nearest point and `f_res_ref` is taken over the whole grid in
    1e5..1e8 Hz, exactly as `run11.run` does.
    """
    fref = np.asarray(ref_freq, float)
    zall = np.asarray(ref_Z, complex)
    freqs = np.asarray(receipt["freq"], float)
    Z = np.asarray(receipt["Z_re"], float) + 1j * np.asarray(receipt["Z_im"], float)
    idx = [int(np.argmin(abs(fref - f))) for f in freqs]
    zr = zall[idx]

    rsel = [k for k, f in enumerate(freqs) if 1e5 <= f <= 1e8]
    fr_m, _ = resonance(freqs[rsel], Z[rsel])
    ridx = [i for i in range(len(fref)) if 1e5 <= fref[i] <= 1e8]
    fr_r, _ = resonance(fref[ridx], zall[ridx])
    k1 = int(np.argmin(abs(freqs - 1e6))); k100 = int(np.argmin(abs(freqs - 1e5)))
    w = 2 * np.pi

    receipt.update(
        Zref_re=[float(x) for x in zr.real], Zref_im=[float(x) for x in zr.imag],
        fit_dL_pH=fit_dl(freqs, Z.imag - zr.imag),
        dL_1MHz_pH=float((Z.imag - zr.imag)[k1] / (w * 1e6) * 1e12),
        dRe_100k_mOhm=float((Z.real - zr.real)[k100] * 1e3),
        dRe_1MHz_mOhm=float((Z.real - zr.real)[k1] * 1e3),
        err_1MHz=float(abs(Z[k1] - zr[k1]) / abs(zr[k1])),
        f_res_model=fr_m, f_res_ref=fr_r,
        ladder_gates=ladder_gates(freqs, Z, zr, fr_m, fr_r))
    return receipt


# ---------------------------------------------------------------- Result
@dataclass
class Result:
    """What `Model.solve` returns: the sweep plus everything needed to write a receipt.

    It still unpacks as the research `(Z, stats)` / `(Z, stats, Vs)` tuple, so the W2/W3 gate
    scripts and any research driver keep working unchanged.
    """

    model: object
    freq: np.ndarray
    Z: np.ndarray
    stats: list
    V: list | None = None
    solve_seconds: float = 0.0
    _extra: dict = field(default_factory=dict, repr=False)

    def __iter__(self):
        return iter((self.Z, self.stats, self.V) if self.V is not None else (self.Z, self.stats))

    def _voltages(self, f):
        """The node voltages at f: reuse the stored ones when this solve kept them, else re-solve."""
        if self.V is not None and len(self.freq):
            k = int(np.argmin(np.abs(self.freq - f)))
            if self.freq[k] == f:
                return self.V[k]
        return self.model.solve([f], verbose=False, want_v=True).V[0]

    def breakdown(self, f, split=True):
        """Per-sheet / per-element R and L at f (`split=True` = `run11`'s `breakdown_100k`)."""
        V = self._voltages(f)
        return self.model.split_breakdown(f, V) if split else self.model.breakdown(f, V)

    def receipt(self, breakdown_100k=None) -> dict:
        """Receipt v1.  `breakdown_100k`: None = only when 100 kHz is in `freq`, True = force."""
        from .model import FLAGS, peak_rss_mb  # local: model imports this module

        m = self.model
        opt = m.options
        ex = m.ex
        rail = getattr(m, "rail", None)
        flags = {k: m.info[k] for k in FLAGS}
        mesh = dict(h=opt.h, fh=opt.fh, top_h=opt.top_h, sub=list(opt.sub), fringe=opt.fringe,
                    fringe_wd=opt.fringe_wd, max_layers=opt.max_layers,
                    fine_box=list(opt.fine_box) if opt.fine_box else None)
        conv = conventions_dict(opt.conventions)
        config = {d["refdes"]: d["model_id"] for d in ex["decaps"]}
        prepare_s = getattr(rail, "prepare_seconds", 0.0)
        build_s = float(m.info.get("build_seconds", 0.0))

        rec = dict(
            receipt_version=RECEIPT_VERSION,
            engine_version=_engine_version(),
            numerics_id=numerics_id(opt.reference, flags, mesh, conv),
            spd_path=str(getattr(rail, "spd_path", ex.get("spd_path", ""))),
            spd_sha256=str(getattr(rail, "spd_sha256", "") or ""),
            port=str(getattr(rail, "port", ex.get("port_name", ""))),
            rail=ex["rail_net"],
            reference_mode=opt.reference,
            flags=flags,
            mesh=mesh,
            backend=dict(solver=m.backend.solver, fast=m.backend.fast, ir_steps=m.backend.ir_steps,
                         device=getattr(m._cudss, "device", None) or None),
            conventions=conv,
            decap_config_sha256=decap_config_sha256(config),
            unknowns=int(m.N),
            nodes_before_prune=int(m.info["nodes_before_prune"]),
            decaps_connected=int(m.info["decaps_connected"]),
            two_sided_layers=sorted(m.two_sided_layers),
            plane_C_total_nF=m.info["plane_C_total_nF"],
            dielectric_used=dielectric_used(ex, flags["eps_table"]),
            reference_search={L: [{k: v for k, v in b.items() if k != "sides"} for b in r["blocks"]]
                              for L, r in m.info["reference_search"].items()},
            build_info={k: v for k, v in m.info.items() if k != "reference_search"},
            freq=[float(x) for x in self.freq],
            Z_re=[float(x) for x in self.Z.real], Z_im=[float(x) for x in self.Z.imag],
            stats=self.stats,
            wall_seconds=prepare_s + build_s + self.solve_seconds,
            peak_rss_MB=peak_rss_mb(),
            validity=dict(design_class="unknown", notes=list(VALIDITY_NOTES)))

        want = (breakdown_100k if breakdown_100k is not None
                else bool(len(self.freq) and np.any(self.freq == 1e5)))
        if want:
            rec["breakdown_100k"] = self.breakdown(1e5)
        rec.update(self._extra)
        return rec


def _engine_version() -> str:
    import spd_pi_engine

    return spd_pi_engine.__version__


def demo() -> None:
    """Self-check without SPD data: the gate metrics, numerics_id and decap_config_sha256."""
    f = np.array([1e3, 1e4, 1e5, 1e6, 5e6, 1e7, 1e8])           # series R-L-C, f0 = 1.59 MHz
    zr = 1e-3 + 1j * (2 * np.pi * f * 1e-9 - 1.0 / (2 * np.pi * f * 1e-5))
    g = ladder_gates(f, zr.copy(), zr, 1.585e6, 1.585e6)
    assert g["PASS"] == dict(G1=True, G2=True, G3=True, G4=True), g
    assert abs(g["G3_rel_err_1MHz"]) < 1e-15 and abs(fit_dl(f, np.zeros_like(f))) < 1e-9
    fr, _ = resonance(f, zr)
    assert 1e6 < fr < 3e6, fr
    rec = dict(freq=[float(x) for x in f], Z_re=[float(x) for x in zr.real],
               Z_im=[float(x) for x in zr.imag])
    attach_reference(rec, f, zr)
    assert rec["ladder_gates"]["PASS"]["G3"] and rec["err_1MHz"] < 1e-15
    assert rec["Zref_re"] == rec["Z_re"]
    mesh = dict(h=200.0, fh=50.0, top_h=50.0, sub=[20, 10, 10], fringe=True, fringe_wd=5.0,
                max_layers=3, fine_box=[0.0, 0.0, 1.0, 1.0])
    a = numerics_id("powersi-compatible", dict(c_unit_fix=True), mesh, dict(gnd_net="DGND"))
    b = numerics_id("powersi-compatible", dict(c_unit_fix=True), {**mesh, "fine_box": None},
                    dict(gnd_net="DGND"))
    c = numerics_id("physical-gnd", dict(c_unit_fix=True), mesh, dict(gnd_net="DGND"))
    assert a == b, "fine_box must not enter numerics_id"
    assert a != c, "the reference mode must"
    assert (decap_config_sha256({"C1": "M1", "C2": None})
            == decap_config_sha256({"C2": None, "C1": "M1"}) != decap_config_sha256({"C1": "M1", "C2": "M1"}))
    print("DEMO PASS (receipt v1: G1-G5 gates, attach_reference, numerics_id, decap_config_sha256)")


if __name__ == "__main__":
    demo()
