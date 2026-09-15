#!/usr/bin/env python
"""Follow-up (1): extended final table for the 7 cases, GND-only (EXP-5) vs cavity-wall (EXP-8).

Adds to the EXP-8 metrics
  err_1_10MHz_max : max |Z-Zref|/|Zref| over the solved frequencies in [1, 10] MHz (G4 error part)
  f0_imz          : Im Z zero crossing (first - to + crossing above 100 kHz), linear interpolation of Im Z
                    in log f between the solved points; the reference value is taken from the full
                    826-point Touchstone AND from the reference resampled at the model's points (to show
                    the interpolation error of the sparse model grid)
  f_res (min |Z|) : as stored in the result JSONs (run_exp1.resonance, parabolic refinement)
No new solves.
"""
from __future__ import annotations

import json
import os

import numpy as np

OUT = "/home/claude/work/exp9"
CASES = [("260729", "Port18_SITE0"), ("260804", "Port18_SITE0"), ("260729", "Port16_SITE0"), ("260729", "Port1_SITE0"),
         ("260729", "Port14_SITE0"), ("260729", "Port7_SITE0"), ("260729", "Port19_SITE0")]
EXP5 = {("260729", "Port18_SITE0"): "result_260729_Port18_SITE0_sweep.json", ("260804", "Port18_SITE0"): "result_260804_Port18_SITE0_sweep.json"}
REF = {k: f"/home/claude/data/S4LB002_{k}_Zdiag.npz" for k in ("260729", "260804")}


def zero_cross(f, im, fmin=1e5):
    f = np.asarray(f); im = np.asarray(im)
    for k in range(len(f) - 1):
        if f[k] >= fmin and im[k] < 0 <= im[k + 1]:
            lf = np.log10(f[k]) + (0 - im[k]) * (np.log10(f[k + 1]) - np.log10(f[k])) / (im[k + 1] - im[k])
            return float(10 ** lf)
    return float("nan")


def q_factor(f, z, f0):
    """|Z| shape Q estimate at the Im-zero crossing: Q = X'(w0)*w0/(2R) with X' from the dense reference."""
    w = 2 * np.pi * np.asarray(f)
    k = int(np.argmin(abs(np.asarray(f) - f0)))
    k0, k1 = max(k - 3, 0), min(k + 3, len(f) - 1)
    dxdw = (z[k1].imag - z[k0].imag) / (w[k1] - w[k0])
    return float(dxdw * 2 * np.pi * f0 / (2 * z[k].real))


def metrics(fn, fref, zref):
    r = json.load(open(fn))
    f = np.array(r["freq"]); Z = np.array(r["Z_re"]) + 1j * np.array(r["Z_im"]); Zr = np.array(r["Zref_re"]) + 1j * np.array(r["Zref_im"])
    band = (f >= 0.999e6) & (f <= 10.01e6)
    err = np.abs(Z - Zr) / np.abs(Zr)
    kmax = int(np.argmax(np.where(band, err, -1)))
    return dict(n_points_1_10MHz=int(band.sum()), err_1_10MHz_max=float(err[kmax]), f_at_max_err=float(f[kmax]),
                f0_imz_model=zero_cross(f, Z.imag), f0_imz_ref_sparse=zero_cross(f, Zr.imag),
                fres_minZ_model=float(r["f_res_model"]), fres_minZ_ref=float(r["f_res_ref"]))


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for tag, port in CASES:
        ref = np.load(REF[tag], allow_pickle=True)
        names = [str(x) for x in ref["port_names"]]
        col = [i for i, n in enumerate(names) if n.split("::")[0] == port][0]
        fref = ref["freq"]; zref = ref["Zdiag"][:, col]
        f0r = zero_cross(fref, zref.imag)
        row = dict(tag=tag, port=port, f0_imz_ref_dense=f0r, Q_ref=q_factor(fref, zref, f0r))
        for mode, fn in (("gnd", os.path.join("/home/claude/work/exp5", EXP5.get((tag, port), f"result_{tag}_{port}_ladder.json"))),
                         ("any", f"/home/claude/work/exp8/result_{tag}_{port}_any.json")):
            m = metrics(fn, fref, zref)
            m["f0_err"] = m["f0_imz_model"] / f0r - 1
            m["f0_err_vs_sparse_ref"] = m["f0_imz_model"] / m["f0_imz_ref_sparse"] - 1
            m["fres_err"] = m["fres_minZ_model"] / m["fres_minZ_ref"] - 1
            row[mode] = m
        rows.append(row)
    json.dump(rows, open(os.path.join(OUT, "table_ext.json"), "w"), indent=1)
    hdr = "| case | Q_ref | mode | max err 1-10 MHz (@f) | f0 ImZ=0 model / ref [MHz] (err) | ref f0 sparse-grid interp. err | f_res min|Z| model / ref (err) |"
    lines = [hdr, "|" + "---|" * 7]
    for r in rows:
        for mode in ("gnd", "any"):
            m = r[mode]
            lines.append(f"| {r['tag']} {r['port'].split('_')[0]} | {r['Q_ref']:.2f} | {mode} | {100*m['err_1_10MHz_max']:.1f}% (@{m['f_at_max_err']/1e6:.2f}) | "
                         f"{m['f0_imz_model']/1e6:.3f} / {r['f0_imz_ref_dense']/1e6:.3f} ({100*m['f0_err']:+.1f}%) | "
                         f"{100*(m['f0_imz_ref_sparse']/r['f0_imz_ref_dense']-1):+.1f}% | "
                         f"{m['fres_minZ_model']/1e6:.3f} / {m['fres_minZ_ref']/1e6:.3f} ({100*m['fres_err']:+.1f}%) |")
    open(os.path.join(OUT, "table_ext.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
