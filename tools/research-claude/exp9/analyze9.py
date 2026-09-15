#!/usr/bin/env python
"""EXP-9 analysis of the bare-board multiport files (run9.py).  DIAGNOSTIC hypothesis tests only.

For each mp_*.npz:
  check    : closed with the SPICE decaps (Rx = 0) vs the stored EXP-8 result at common frequencies
  bare     : decaps removed
  +24 mOhm : +24 mOhm series R in every decap branch
  fits     : one-parameter least squares on Re(Z - Zref) over 1 kHz .. 1 MHz (diagnostic, not adopted)
             H1 common series R at the port;  H2 equal extra R per decap;  H3 extra R on the largest-C decap only
             residual RMS over 1 kHz .. 3 MHz reported to discriminate the shapes
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exp1"))
OUT = "/home/claude/work/exp9"


def close(Zm, zdec, rx):
    A = Zm[1:, 1:] + np.diag(zdec + rx)
    return Zm[0, 0] - Zm[0, 1:] @ np.linalg.solve(A, Zm[1:, 0])


def sweep(d, rx_fn, common=0.0):
    return np.array([close(d["Zmp"][k], d["zdec"][k], rx_fn(k)) + common for k in range(len(d["freq"]))])


def fit1(f, dre_obs, shape, lo=9e2, hi=1.001e6):
    m = (f >= lo) & (f <= hi)
    a = float(np.dot(shape[m], dre_obs[m]) / np.dot(shape[m], shape[m]))
    return a


def analyse(fn):
    d = dict(np.load(fn, allow_pickle=True))
    base = os.path.basename(fn)[3:-4]
    tag, port = base.split("_")[0], "_".join(base.split("_")[1:3])
    gnd = "_".join(base.split("_")[3:])
    f = d["freq"]; zr = d["zref"]; Nd = d["zdec"].shape[1]
    mids = [str(x) for x in d["mids"]]
    Z0 = sweep(d, lambda k: np.zeros(Nd))
    out = dict(tag=tag, port=port, gnd=gnd, n_decaps=Nd, unknowns=int(d["unknowns"]), freq=f.tolist())
    e8 = f"/home/claude/work/exp8/result_{tag}_{port}_any.json"
    if gnd.startswith("none") and os.path.exists(e8):
        r = json.load(open(e8)); f8 = np.array(r["freq"]); z8 = np.array(r["Z_re"]) + 1j * np.array(r["Z_im"])
        dev = [abs(Z0[int(np.argmin(abs(f - x)))] - z) / abs(z) for x, z in zip(f8, z8) if np.min(abs(f - x)) < 1]
        out["check_vs_exp8_max_rel"] = float(max(dev)) if dev else None
    zb = np.array([d["Zmp"][k][0, 0] for k in range(len(f))])
    Z24 = sweep(d, lambda k: np.full(Nd, 0.024))
    dre0 = (Z0.real - zr.real) * 1e3
    out["dRe_model_mOhm"] = dre0.tolist()
    out["dRe_plus24_mOhm"] = ((Z24.real - zr.real) * 1e3).tolist()
    out["dIm_model_mOhm"] = ((Z0.imag - zr.imag) * 1e3).tolist(); out["dIm_plus24_mOhm"] = ((Z24.imag - zr.imag) * 1e3).tolist()
    out["Zbare"] = [[float(z.real), float(z.imag)] for z in zb]
    out["Z_re_model"] = Z0.real.tolist(); out["Zref_re"] = zr.real.tolist()
    out["err_model"] = (abs(Z0 - zr) / abs(zr)).tolist(); out["err_plus24"] = (abs(Z24 - zr) / abs(zr)).tolist()
    # shapes (per 1 mOhm)
    s_common = np.full(len(f), 1.0)
    Z1 = sweep(d, lambda k: np.full(Nd, 1e-3)); s_equal = (Z1.real - Z0.real) * 1e3
    csum = [np.sum(np.abs(1 / d["zdec"][k].imag)) for k in range(len(f))]
    big = int(np.argmax(np.abs(1 / d["zdec"][0].imag)))  # largest C at the lowest frequency
    Zb1 = sweep(d, lambda k: np.where(np.arange(Nd) == big, 1e-3, 0.0)); s_big = (Zb1.real - Z0.real) * 1e3
    fits = {}
    m3 = (f >= 9e2) & (f <= 3.1e6)
    for name, sh in (("H1_common_port_R", s_common), ("H2_equal_R_per_decap", s_equal), ("H3_R_on_largest_C_decap", s_big)):
        a = fit1(f, -dre0, sh)
        res = dre0 + a * sh
        fits[name] = dict(R_fit_mOhm=a, rms_residual_1k_3M_mOhm=float(np.sqrt(np.mean(res[m3] ** 2))),
                          max_abs_residual_1k_3M=float(np.max(abs(res[m3]))), sensitivity_at_1e4=float(sh[int(np.argmin(abs(f - 1e4)))]),
                          sensitivity_at_1e6=float(sh[int(np.argmin(abs(f - 1e6)))]))
    out["largest_C_decap"] = mids[big]
    out["fits"] = fits
    out["rms_dRe_model_1k_3M"] = float(np.sqrt(np.mean(dre0[m3] ** 2)))
    json.dump(out, open(os.path.join(OUT, f"analysis_{base}.json"), "w"), indent=1)
    sel = [k for k, x in enumerate(f) if x < 3.2e6]
    print(f"== {base}  Nd={Nd} unknowns={out['unknowns']} check_vs_exp8={out.get('check_vs_exp8_max_rel')}")
    print("   f[kHz]  ReZref  dRe_model  dRe_+24/decap  C_bare[nF]  dIm_model dIm_+24")
    for k in sel:
        print(f"   {f[k]/1e3:9.1f} {zr[k].real*1e3:7.3f} {dre0[k]:8.3f} {out['dRe_plus24_mOhm'][k]:8.3f} {-1/(2*np.pi*f[k]*zb[k].imag)*1e9:8.3f} {out['dIm_model_mOhm'][k]:8.3f} {out['dIm_plus24_mOhm'][k]:8.3f}")
    for n, v in fits.items():
        print("  ", n, {k: round(x, 3) for k, x in v.items()})
    return out


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(OUT, "mp_*.npz")))
    for fn in files:
        analyse(fn)
