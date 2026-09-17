#!/usr/bin/env python
"""EXP-10: is the PowerSI reference Z_ii(f) in 1 Hz-100 kHz a low-order rational fit or per-frequency
solved data? Vector-fits (Gustavsen-Semlyen) each port's Z_ii(f) with n_p in {1,2,3,4} poles and judges
FIT / SOLVED / INDETERMINATE per the frozen criteria in EXP10_PLAN.md SS5. Reference-data-only; no model.

Run:  python fit_lowfreq.py [--design 260729] [--fit-s] [--selfcheck]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import ref_npz, spd_path, work_file, peak_rss_mb, DATA_DIR  # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

WSCALE = 2 * np.pi * 1e3          # rad/s at 1 kHz: scales s and poles for conditioning
FLO, FHI = 1.0, 1.0e5             # primary band, Hz
NP_LIST = [1, 2, 3, 4]
NP_EXTRA = [6, 8]                 # Port18 sensitivity only
RMS_THR, MAX_THR = 1e-6, 1e-5
MEDIAN_THR = 1e-4
VF_MAXIT, VF_TOL = 20, 1e-10
S92P = os.path.join(str(DATA_DIR), "S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- vector fitting (real-coefficient Gustavsen-Semlyen relocating VF) ----------

def init_poles(n_p, flo=FLO, fhi=FHI):
    fk = np.array([np.sqrt(flo * fhi)]) if n_p == 1 else np.logspace(np.log10(flo), np.log10(fhi), n_p)
    return (-2 * np.pi * fk / WSCALE).astype(complex)


def pole_blocks(poles):
    """poles: unique repr (reals once, complex pairs once with Im>0). Returns real A(N,N), b(N,), kinds[list]."""
    N = sum(1 if p.imag == 0 else 2 for p in poles)
    A = np.zeros((N, N)); b = np.zeros(N); kinds = []
    i = 0
    for p in poles:
        if p.imag == 0:
            A[i, i] = p.real; b[i] = 1.0; kinds.append(("r", i)); i += 1
        else:
            pr, pi = p.real, p.imag
            A[i, i] = pr; A[i, i + 1] = pi; A[i + 1, i] = -pi; A[i + 1, i + 1] = pr
            b[i] = 2.0; kinds.append(("c", i)); i += 2
    return A, b, kinds


def phi_matrix(s, poles, kinds, N):
    Phi = np.zeros((s.size, N), dtype=complex)
    for p, (kind, i) in zip(poles, kinds):
        if kind == "r":
            Phi[:, i] = 1.0 / (s - p.real)
        else:
            Phi[:, i] = 1.0 / (s - p) + 1.0 / (s - np.conj(p))
            Phi[:, i + 1] = 1j / (s - p) - 1j / (s - np.conj(p))
    return Phi


def expand_full(poles):
    out = []
    for p in poles:
        out.append(p)
        if p.imag != 0:
            out.append(np.conj(p))
    return np.array(out)


def reduce_poles(eigs):
    """Pair up conjugate eigenvalues of a real matrix into the unique repr (Im>0 kept, near-real -> real)."""
    eigs = np.asarray(eigs, dtype=complex)
    is_real = np.abs(eigs.imag) < 1e-9 * np.maximum(np.abs(eigs.real), 1e-6)
    used = np.zeros(len(eigs), dtype=bool)
    out = []
    order = np.argsort(-eigs.imag)
    for idx in order:
        if used[idx]:
            continue
        p = eigs[idx]
        if is_real[idx]:
            out.append(complex(p.real, 0.0)); used[idx] = True
        else:
            cand = np.where(~used)[0]
            j = cand[np.argmin(np.abs(eigs[cand] - np.conj(p)))]
            out.append(p if p.imag > 0 else np.conj(p))
            used[idx] = True; used[j] = True
    return out


def wlstsq(cols, Y, w):
    """Weighted (relative, w=1/|Y|) real-valued LS for complex columns/rhs; real & imag rows stacked."""
    A = cols * w[:, None]
    Amat = np.vstack([A.real, A.imag])
    rhs = np.concatenate([(Y * w).real, (Y * w).imag])
    x, *_ = np.linalg.lstsq(Amat, rhs, rcond=None)
    return x


def vectfit(f_hz, Y, n_p, maxit=VF_MAXIT, tol=VF_TOL):
    """Fit Y(j*2*pi*f) ~= d + s*e + sum r_k/(s-p_k), real coefficients. Returns dict with poles (unique,
    physical rad/s), d, e, r (scaled-domain, ohms), Yfit, n_iter, converged."""
    s = 1j * 2 * np.pi * f_hz / WSCALE
    w = 1.0 / np.abs(Y)
    ones = np.ones((s.size, 1), complex)
    poles = list(init_poles(n_p, f_hz.min(), f_hz.max()))
    converged = False
    it = 0
    for it in range(maxit):
        A, b, kinds = pole_blocks(poles)
        N = A.shape[0]
        Phi = phi_matrix(s, poles, kinds, N)
        x = wlstsq(np.concatenate([ones, s[:, None], Phi, -Y[:, None] * Phi], axis=1), Y, w)
        rt = x[2 + N:2 + 2 * N]
        eigs = np.linalg.eigvals(A - np.outer(b, rt))
        eigs = np.where(eigs.real > 0, -eigs.real + 1j * eigs.imag, eigs)  # stabilize unstable poles
        new_poles = reduce_poles(eigs)
        old_full = np.sort_complex(expand_full(poles))
        new_full = np.sort_complex(expand_full(new_poles))
        rel = np.max(np.abs(new_full - old_full) / np.maximum(np.abs(old_full), 1e-300))
        poles = new_poles
        if rel < tol:
            converged = True
            it += 1
            break
    # final residue solve (d, e, r only) at the settled poles
    A, b, kinds = pole_blocks(poles)
    N = A.shape[0]
    Phi = phi_matrix(s, poles, kinds, N)
    x = wlstsq(np.concatenate([ones, s[:, None], Phi], axis=1), Y, w)
    d, e, r = x[0], x[1], x[2:]
    Yfit = d + s * e + Phi @ r
    poles_phys = expand_full(poles) * WSCALE
    return dict(d=d, e=e, r=r, poles=poles, poles_phys=poles_phys, Yfit=Yfit, n_iter=it, converged=converged)


def dc_limit(fit):
    """Zfit(s=0) = d + sum r_k*phi_k(0), i.e. d - sum(r_k/p_k) (residues already in ohms, poles dimensionless)."""
    _, _, kinds = pole_blocks(fit["poles"])
    Phi0 = phi_matrix(np.array([0j]), fit["poles"], kinds, len(fit["r"]))
    return complex(fit["d"] + (Phi0 @ fit["r"])[0])


# ---------- complex-coefficient vector fit (EXP-10b): no conjugate pairing, direct complex lstsq ----------

def wlstsq_complex(cols, Y, w):
    A = cols * w[:, None]
    x, *_ = np.linalg.lstsq(A, Y * w, rcond=None)
    return x


def vectfit_complex(f_hz, Y, n_p, maxit=VF_MAXIT, tol=VF_TOL):
    """Same relative weighting/iteration/convergence as vectfit(), but d, e, r_k, p_k are all unconstrained
    complex: no conjugate pairing, no real/imag row stacking. Poles start real (init_poles), then relocate
    as eigenvalues of the (complex) sigma model each iteration; unstable poles are flipped to the LHP."""
    s = 1j * 2 * np.pi * f_hz / WSCALE
    w = 1.0 / np.abs(Y)
    ones = np.ones((s.size, 1), complex)
    poles = init_poles(n_p, f_hz.min(), f_hz.max())
    converged = False
    it = 0
    for it in range(maxit):
        Phi = 1.0 / (s[:, None] - poles[None, :])
        x = wlstsq_complex(np.concatenate([ones, s[:, None], Phi, -Y[:, None] * Phi], axis=1), Y, w)
        rt = x[2 + n_p:2 + 2 * n_p]
        A = np.diag(poles)
        eigs = np.linalg.eigvals(A - np.outer(np.ones(n_p, complex), rt))
        eigs = np.where(eigs.real > 0, -eigs.real + 1j * eigs.imag, eigs)
        old_sorted, new_sorted = np.sort_complex(poles), np.sort_complex(eigs)
        rel = np.max(np.abs(new_sorted - old_sorted) / np.maximum(np.abs(old_sorted), 1e-300))
        poles = eigs
        if rel < tol:
            converged = True
            it += 1
            break
    Phi = 1.0 / (s[:, None] - poles[None, :])
    x = wlstsq_complex(np.concatenate([ones, s[:, None], Phi], axis=1), Y, w)
    d, e, r = x[0], x[1], x[2:]
    Yfit = d + s * e + Phi @ r
    poles_phys = poles * WSCALE
    return dict(d=d, e=e, r=r, poles=poles, poles_phys=poles_phys, Yfit=Yfit, n_iter=it, converged=converged)


def dc_limit_complex(fit):
    """Zfit(s=0) = d + sum r_k/(-p_k), poles dimensionless (scaled), residues in ohms."""
    return complex(fit["d"] + np.sum(fit["r"] / (-fit["poles"])))


def band_tag(lo, hi):
    def fmt(hz):
        if hz >= 1e6 and hz % 1e6 == 0:
            return f"{int(hz / 1e6)}M"
        if hz >= 1e3 and hz % 1e3 == 0:
            return f"{int(hz / 1e3)}k"
        if hz == int(hz):
            return str(int(hz))
        return str(hz).replace(".", "p")
    return f"band{fmt(lo)}{fmt(hi)}"


def grep_first_line(path, needle: bytes):
    """Stream a (possibly huge) text file as bytes, line by line, and return the first line containing
    needle (decoded), or None. Used for the .DC_BBS_Setting line, which sits ~9.6M lines into a ~10M-line SPD."""
    with open(path, "rb") as f:
        for line in f:
            if needle in line:
                return line.decode("ascii", errors="replace").rstrip("\r\n")
    return None


# ---------- Touchstone (.s92p) parsing, only used for the S-fit fallback ----------

def parse_s92p(path, n_ports=92):
    text = open(path, "r", encoding="ascii", errors="ignore").read()
    tokens = [ln for ln in text.splitlines() if ln and ln[0] not in "!#"]
    text = " ".join(tokens)
    arr = np.array(text.split(), dtype=float)
    per = 1 + n_ports * n_ports * 2
    nf = arr.size // per
    arr = arr[:nf * per].reshape(nf, per)
    freq = arr[:, 0]
    S = (arr[:, 1::2] + 1j * arr[:, 2::2]).reshape(nf, n_ports, n_ports)
    return freq, S


# ---------- port-level fit sweep ----------

def fit_port(f_hz, Y, n_p_list, resid_denom):
    by_np, best = {}, None
    for n_p in n_p_list:
        fit = vectfit(f_hz, Y, n_p)
        eps = np.abs(fit["Yfit"] - Y) / resid_denom(Y)
        rms, mx = float(np.sqrt(np.mean(eps ** 2))), float(np.max(eps))
        by_np[n_p] = dict(rms=rms, max=mx, converged=fit["converged"], poles=fit["poles_phys"])
        if best is None or rms < by_np[best]["rms"]:
            best = n_p
    return best, by_np


def fit_port_complex(f_hz, Y, n_p_list, resid_denom):
    by_np, best = {}, None
    for n_p in n_p_list:
        fit = vectfit_complex(f_hz, Y, n_p)
        eps = np.abs(fit["Yfit"] - Y) / resid_denom(Y)
        rms, mx = float(np.sqrt(np.mean(eps ** 2))), float(np.max(eps))
        by_np[n_p] = dict(rms=rms, max=mx, converged=fit["converged"], poles=fit["poles_phys"])
        if best is None or rms < by_np[best]["rms"]:
            best = n_p
    return best, by_np


def denom_z(Y):
    return np.abs(Y)


def denom_s(Y):
    return np.abs(1.0 + Y)


def out_path(exp, name):
    p = work_file(exp, name)
    if p.exists():
        hhmmss = time.strftime("%H%M%S")
        stem, ext = os.path.splitext(name)
        p2 = work_file(exp, f"{stem}_{hhmmss}{ext}")
        print(f"WARNING: {p} already exists, writing {p2} instead")
        return p2
    return p


# ---------- self-check (no data files) ----------

def selfcheck():
    f = np.logspace(0, 5, 126)
    R, C, L = 1e-3, 200e-6, 20e-12
    p_extra = -2 * np.pi * 3e4
    r_extra = 1e-3 * abs(p_extra) / 50
    s = 1j * 2 * np.pi * f
    Z = R + s * L + 1.0 / (s * C) + r_extra / (s - p_extra)
    fit2 = vectfit(f, Z, 2)
    fit3 = vectfit(f, Z, 3)
    eps2 = np.abs(fit2["Yfit"] - Z) / np.abs(Z)
    eps3 = np.abs(fit3["Yfit"] - Z) / np.abs(Z)
    rms2, rms3 = np.sqrt(np.mean(eps2 ** 2)), np.sqrt(np.mean(eps3 ** 2))
    print(f"selfcheck n_p=2 RMS={rms2:.3e}  n_p=3 RMS={rms3:.3e}")
    rel_pole = min(abs(p - p_extra) / abs(p_extra) for p in fit3["poles_phys"])
    print(f"selfcheck n_p=3 best pole match to p_extra: rel={rel_pole:.3e}")
    ok = rms3 <= 1e-10 and rel_pole < 1e-6

    # complex-VF check: Y=(G+jB)+jwC synthetic admittance on the same 1 Hz-100 kHz grid
    G, B, Cc = 2.2e-3, 1.26e-3, 264e-6
    omega = 2 * np.pi * f
    Y = (G + 1j * B) + 1j * omega * Cc
    Zc = 1.0 / Y
    fitc1 = vectfit_complex(f, Zc, 1)
    epsc1 = np.abs(fitc1["Yfit"] - Zc) / np.abs(Zc)
    rmsc1 = float(np.sqrt(np.mean(epsc1 ** 2)))
    fitr4 = vectfit(f, Zc, 4)
    epsr4 = np.abs(fitr4["Yfit"] - Zc) / np.abs(Zc)
    rmsr4 = float(np.sqrt(np.mean(epsr4 ** 2)))
    print(f"selfcheck complex VF n_p=1 RMS={rmsc1:.3e} (real VF n_p=4 RMS={rmsr4:.3e}, no assert)")
    ok = ok and (rmsc1 <= 1e-10)

    print("SELFCHECK", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---------- main analysis ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="260729")
    ap.add_argument("--fit-s", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--band", type=float, nargs=2, default=[FLO, FHI], metavar=("LO", "HI"),
                     help="primary band in Hz for the per-port fits (default 1.0 1e5)")
    ap.add_argument("--complex", action="store_true", dest="complex_mode",
                     help="also run a complex-coefficient VF (EXP-10b); skips the S-fit fallback")
    ap.add_argument("--lowfreq-diag", action="store_true",
                     help="0.1-2 Hz linear Y=(G+jB)+jwC diagnostic over all three designs; no VF, no band/complex")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

    if args.lowfreq_diag:
        return lowfreq_diag_main()

    lo, hi = args.band
    is_default_band = (lo, hi) == (FLO, FHI)

    t0 = time.time()
    npz_path = ref_npz(args.design)
    d = np.load(npz_path, allow_pickle=True)
    freq_full = d["freq"]; Zdiag = d["Zdiag"]; names = [str(x) for x in d["port_names"]]
    n_ports = len(names)
    band = (freq_full >= lo) & (freq_full <= hi)
    f_band = freq_full[band]
    print(f"npz {npz_path}  n_band={f_band.size}  n_ports={n_ports}  band=[{lo:g},{hi:g}] Hz")
    port18_idx = [i for i, n in enumerate(names) if n.startswith("Port18_SITE0")][0]
    name18 = names[port18_idx]

    if args.complex_mode:
        return complex_mode_main(args, npz_path, freq_full, Zdiag, names, n_ports, band, f_band, lo, hi,
                                  is_default_band, port18_idx, name18, t0)

    ports = []
    rms_np4_z = []
    for i, name in enumerate(names):
        Z = Zdiag[band, i]
        best_np, by_np = fit_port(f_band, Z, NP_LIST, denom_z)
        rms_np4_z.append(by_np[4]["rms"])
        fit_level = by_np[best_np]["rms"] <= RMS_THR and by_np[best_np]["max"] <= MAX_THR
        ports.append(dict(name=name, best_np=best_np, rms=by_np[best_np]["rms"], max=by_np[best_np]["max"],
                           rms_by_np={str(k): v["rms"] for k, v in by_np.items()},
                           poles_best=[[float(p.real), float(p.imag)] for p in by_np[best_np]["poles"]],
                           fit_level=bool(fit_level),
                           converged=by_np[best_np]["converged"], s=None))
        print(f"{name:55s} best_np={best_np} rms={by_np[best_np]['rms']:.3e} max={by_np[best_np]['max']:.3e} "
              f"fit_level={fit_level}")

    n_fit_level = sum(p["fit_level"] for p in ports)
    median_rms_np4_z = float(np.median(rms_np4_z))
    z_only = "FIT" if n_fit_level >= 83 else ("SOLVED" if median_rms_np4_z > MEDIAN_THR else "INDETERMINATE")
    print(f"Z-only verdict: {z_only}  n_fit_level={n_fit_level}/92  median_rms_np4={median_rms_np4_z:.3e}")

    s92p_path = None; s92p_sha = None; median_rms_np4_s = None
    run_s = (z_only != "FIT") or args.fit_s
    if run_s:
        print(f"running S-fit fallback, parsing {S92P} ...")
        freq_s, S = parse_s92p(S92P, n_ports)
        band_s = (freq_s >= FLO) & (freq_s <= FHI)
        assert band_s.sum() == f_band.size, f"S touchstone band mismatch: {band_s.sum()} vs {f_band.size}"
        s92p_path = S92P; s92p_sha = sha256(S92P)
        rms_np4_s = []
        for i, name in enumerate(names):
            Sd = S[band_s, i, i]
            best_np, by_np = fit_port(freq_s[band_s], Sd, NP_LIST, denom_s)
            rms_np4_s.append(by_np[4]["rms"])
            s_level = by_np[best_np]["rms"] <= RMS_THR and by_np[best_np]["max"] <= MAX_THR
            ports[i]["s"] = dict(best_np=best_np, rms=by_np[best_np]["rms"], max=by_np[best_np]["max"],
                                  rms_by_np={str(k): v["rms"] for k, v in by_np.items()}, fit_level=bool(s_level))
            ports[i]["fit_level"] = bool(ports[i]["fit_level"] or s_level)
        median_rms_np4_s = float(np.median(rms_np4_s))
        n_fit_level = sum(p["fit_level"] for p in ports)
        if n_fit_level >= 83:
            final_verdict = "FIT"
        elif median_rms_np4_z > MEDIAN_THR and median_rms_np4_s > MEDIAN_THR:
            final_verdict = "SOLVED"
        else:
            final_verdict = "INDETERMINATE"
        print(f"combined verdict: {final_verdict}  n_fit_level={n_fit_level}/92  median_rms_np4_s={median_rms_np4_s:.3e}")
    else:
        final_verdict = z_only

    # ---- Port18 diagnostics (sliding window, Z(0)): skipped for a non-default --band ----
    window = []
    if is_default_band:
        Z18 = Zdiag[band, port18_idx]
        best18 = ports[port18_idx]["best_np"]
        fit_best18 = vectfit(f_band, Z18, best18)
        extra_np = {}
        for n_p in NP_EXTRA:
            fit = vectfit(f_band, Z18, n_p)
            eps = np.abs(fit["Yfit"] - Z18) / np.abs(Z18)
            extra_np[n_p] = float(np.sqrt(np.mean(eps ** 2)))
        print(f"Port18 {name18}: extra n_p rms " + ", ".join(f"np{k}={v:.3e}" for k, v in extra_np.items()))

        # sliding one-decade window, every 5th grid point, f0 in [1,1e6]
        grid_mask = (freq_full >= 1.0) & (freq_full <= 1.0e6)
        grid_f0 = freq_full[grid_mask][::5]
        for f0 in grid_f0:
            wmask = (freq_full >= f0) & (freq_full <= 10 * f0)
            if wmask.sum() < 8:
                continue
            fw = freq_full[wmask]; Zw = Zdiag[wmask, port18_idx]
            fitw = vectfit(fw, Zw, 4)
            epsw = np.abs(fitw["Yfit"] - Zw) / np.abs(Zw)
            window.append((float(f0), float(np.sqrt(np.mean(epsw ** 2)))))
        print(f"Port18 window: {len(window)} windows, rms range "
              f"[{min(w[1] for w in window):.3e}, {max(w[1] for w in window):.3e}]")

        z0_ref = complex(Zdiag[0, port18_idx])
        z0_fit = dc_limit(fit_best18)
        print(f"Port18 Z(0): ref={z0_ref.real:+.6e}{z0_ref.imag:+.6e}j  fit(np={best18})={z0_fit.real:+.6e}{z0_fit.imag:+.6e}j")
        port18_result = dict(freq=f_band.tolist(), Z_re=Z18.real.tolist(), Z_im=Z18.imag.tolist(),
                              Zfit_re=fit_best18["Yfit"].real.tolist(), Zfit_im=fit_best18["Yfit"].imag.tolist(),
                              best_np=best18, rms_np6=extra_np[6], rms_np8=extra_np[8], window=window,
                              Z0_ref=[z0_ref.real, z0_ref.imag], Z0_fit=[z0_fit.real, z0_fit.imag])
    else:
        print("Port18 sliding-window / Z(0) diagnostics skipped (non-default --band)")
        port18_result = dict(skipped="non-default --band")

    # ---- global diagnostics ----
    f_all = freq_full[1:]  # drop the 0 Hz point
    ratios = f_all[1:] / f_all[:-1]
    expect = 10 ** (1 / 25)
    bad = ~np.isclose(ratios[1:], expect, rtol=1e-6)
    log_grid_end_hz = float(f_all[np.argmax(bad) + 1]) if bad.any() else float(f_all[-1])
    print(f"grid regularity breaks at/after f={log_grid_end_hz:.6g} Hz" if bad.any() else "grid regular throughout")

    neg_re = []
    re_neg_mask = Zdiag.real < 0
    for i, name in enumerate(names):
        fneg = freq_full[re_neg_mask[:, i]]
        if fneg.size:
            neg_re.append(dict(name=name, max_f=float(fneg.max())))
    print(f"ports with Re Z<0 somewhere: {len(neg_re)}/92")

    wall = time.time() - t0
    rss = peak_rss_mb()
    print(f"wall {wall:.1f} s, peak RSS {rss:.0f} MB")

    result = dict(
        exp="exp10", design=args.design,
        inputs=dict(npz_path=str(npz_path), npz_sha256=sha256(npz_path),
                    s92p_path=s92p_path, s92p_sha256=s92p_sha),
        params=dict(band_hz=[lo, hi], n_p_list=NP_LIST, vf_max_iter=VF_MAXIT,
                    weighting="relative 1/|Y|", thresholds=dict(rms=RMS_THR, max=MAX_THR, median=MEDIAN_THR)),
        verdict=dict(z_only=z_only, final=final_verdict, n_fit_level=n_fit_level,
                     median_rms_np4_z=median_rms_np4_z, median_rms_np4_s=median_rms_np4_s),
        ports=[{k: v for k, v in p.items()} for p in ports],
        port18=port18_result,
        grid=dict(log_grid_end_hz=log_grid_end_hz, n_points_total=int(freq_full.size), n_band=int(f_band.size)),
        negative_re=neg_re,
        stats=dict(wall_seconds=wall, peak_rss_mb=rss),
    )

    tag = band_tag(lo, hi)
    json_name = f"result_exp10_{args.design}.json" if is_default_band else f"result_exp10b_{args.design}_{tag}.json"
    jpath = out_path("exp10", json_name)
    jpath.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {jpath}")

    # ---- plots ----
    fig, ax = plt.subplots(figsize=(7, 5))
    for p in ports:
        xs = sorted(int(k) for k in p["rms_by_np"])
        ys = [p["rms_by_np"][str(k)] for k in xs]
        is18 = p["name"] == name18
        ax.plot(xs, ys, color="crimson" if is18 else "steelblue", lw=2.2 if is18 else 0.5,
                alpha=1.0 if is18 else 0.4, zorder=3 if is18 else 1)
    ax.axhline(RMS_THR, ls="--", color="gray", label=f"threshold {RMS_THR:.0e}")
    ax.set_yscale("log"); ax.set_xlabel("n_p"); ax.set_ylabel("RMS relative residual")
    ax.set_title(f"exp10: Z_ii VF residual vs n_p ({args.design}, all {n_ports} ports, Port18 red)")
    ax.legend()
    fig.tight_layout()
    p1_name = "exp10_residual_vs_np.png" if is_default_band else f"exp10b_residual_vs_np_{tag}.png"
    p1 = out_path("exp10", p1_name)
    fig.savefig(p1, dpi=130); plt.close(fig)
    print(f"wrote {p1}")

    if window:
        fig, ax = plt.subplots(figsize=(7, 5))
        f0s = [w[0] for w in window]; rmss = [w[1] for w in window]
        ax.plot(f0s, rmss, marker=".", lw=1)
        ax.axhline(RMS_THR, ls="--", color="gray")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel("window start f0 (Hz)"); ax.set_ylabel("RMS relative residual (n_p=4)")
        ax.set_title(f"exp10: Port18 sliding 1-decade window residual ({args.design})")
        fig.tight_layout()
        p2 = out_path("exp10", "exp10_window_residual.png")
        fig.savefig(p2, dpi=130); plt.close(fig)
        print(f"wrote {p2}")

    print(f"VERDICT: z_only={z_only} final={final_verdict}")
    return 0


# ---------- EXP-10b: --complex mode (real vs complex VF, band-limited, no S-fit) ----------

def complex_mode_main(args, npz_path, freq_full, Zdiag, names, n_ports, band, f_band, lo, hi,
                       is_default_band, port18_idx, name18, t0):
    print(f"EXP10B --complex mode: design={args.design} band=[{lo:g},{hi:g}] Hz n_band={f_band.size} n_ports={n_ports}")
    ports = []
    rms_np4_real, rms_np4_complex = [], []
    for i, name in enumerate(names):
        Z = Zdiag[band, i]
        best_r, by_r = fit_port(f_band, Z, NP_LIST, denom_z)
        best_c, by_c = fit_port_complex(f_band, Z, NP_LIST, denom_z)
        if name == name18:
            for n_p in NP_EXTRA:
                fr = vectfit(f_band, Z, n_p)
                epsr = np.abs(fr["Yfit"] - Z) / denom_z(Z)
                by_r[n_p] = dict(rms=float(np.sqrt(np.mean(epsr ** 2))), max=float(np.max(epsr)),
                                  converged=fr["converged"], poles=fr["poles_phys"])
                fc = vectfit_complex(f_band, Z, n_p)
                epsc = np.abs(fc["Yfit"] - Z) / denom_z(Z)
                by_c[n_p] = dict(rms=float(np.sqrt(np.mean(epsc ** 2))), max=float(np.max(epsc)),
                                  converged=fc["converged"], poles=fc["poles_phys"])
        rms_np4_real.append(by_r[4]["rms"]); rms_np4_complex.append(by_c[4]["rms"])
        fit_level = ((by_r[best_r]["rms"] <= RMS_THR and by_r[best_r]["max"] <= MAX_THR) or
                     (by_c[best_c]["rms"] <= RMS_THR and by_c[best_c]["max"] <= MAX_THR))
        ratio_np4 = by_r[4]["rms"] / by_c[4]["rms"] if by_c[4]["rms"] > 0 else float("inf")
        ports.append(dict(
            name=name,
            real=dict(best_np=best_r, rms=by_r[best_r]["rms"], max=by_r[best_r]["max"],
                      rms_by_np={str(k): v["rms"] for k, v in by_r.items()},
                      poles_best=[[float(p.real), float(p.imag)] for p in by_r[best_r]["poles"]]),
            complex=dict(best_np=best_c, rms=by_c[best_c]["rms"], max=by_c[best_c]["max"],
                         rms_by_np={str(k): v["rms"] for k, v in by_c.items()},
                         poles_best=[[float(p.real), float(p.imag)] for p in by_c[best_c]["poles"]]),
            ratio_np4=float(ratio_np4),
            fit_level=bool(fit_level),
        ))
        print(f"{name:55s} real np={best_r} rms={by_r[best_r]['rms']:.3e}  "
              f"complex np={best_c} rms={by_c[best_c]['rms']:.3e}  fit_level={fit_level}")

    n_fit_level = sum(p["fit_level"] for p in ports)
    median_rms_np4_real = float(np.median(rms_np4_real))
    median_rms_np4_complex = float(np.median(rms_np4_complex))
    median_ratio_np4 = float(np.median([p["ratio_np4"] for p in ports]))
    asym_dominates = median_ratio_np4 > 10
    if n_fit_level >= 83:
        verdict = "FIT_1_100k"
    elif median_rms_np4_real > MEDIAN_THR and median_rms_np4_complex > MEDIAN_THR:
        verdict = "SOLVED_1_100k"
    else:
        verdict = "INDETERMINATE"
    print(f"EXP10B verdict: {verdict}  n_fit_level={n_fit_level}/{n_ports}  "
          f"median_rms_np4_real={median_rms_np4_real:.3e}  median_rms_np4_complex={median_rms_np4_complex:.3e}  "
          f"median_ratio_np4={median_ratio_np4:.3g}  asym_dominates={asym_dominates}")

    p18 = next(p for p in ports if p["name"] == name18)
    print(f"Port18 {name18}: real rms_by_np={p18['real']['rms_by_np']}  complex rms_by_np={p18['complex']['rms_by_np']}")

    wall = time.time() - t0
    rss = peak_rss_mb()
    result = dict(
        exp="exp10b", design=args.design,
        inputs=dict(npz_path=str(npz_path), npz_sha256=sha256(npz_path)),
        params=dict(band_hz=[lo, hi], n_p_list=NP_LIST, np_extra_port18=NP_EXTRA, vf_max_iter=VF_MAXIT,
                    weighting="relative 1/|Y|", thresholds=dict(rms=RMS_THR, max=MAX_THR, median=MEDIAN_THR)),
        verdict=dict(verdict=verdict, n_fit_level=n_fit_level, n_ports=n_ports,
                     median_rms_np4_real=median_rms_np4_real, median_rms_np4_complex=median_rms_np4_complex,
                     median_ratio_np4=median_ratio_np4, asym_dominates=bool(asym_dominates)),
        ports=ports,
        stats=dict(wall_seconds=wall, peak_rss_mb=rss),
    )

    tag = band_tag(lo, hi)
    json_name = f"result_exp10b_{args.design}.json" if is_default_band else f"result_exp10b_{args.design}_{tag}.json"
    jpath = out_path("exp10", json_name)
    jpath.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {jpath}")

    fig, ax = plt.subplots(figsize=(6, 6))
    xs = np.array([p["real"]["rms_by_np"]["4"] for p in ports])
    ys = np.array([p["complex"]["rms_by_np"]["4"] for p in ports])
    is18 = np.array([p["name"] == name18 for p in ports])
    ax.scatter(xs[~is18], ys[~is18], s=18, alpha=0.6, color="steelblue", label="ports")
    if is18.any():
        ax.scatter(xs[is18], ys[is18], s=90, color="crimson", marker="*", label="Port18", zorder=5)
    ax_lo, ax_hi = 1e-12, float(max(xs.max(), ys.max()) * 2)
    ax.plot([ax_lo, ax_hi], [ax_lo, ax_hi], ls="--", color="gray", lw=1, label="diagonal")
    ax.axvline(RMS_THR, ls=":", color="gray", lw=1)
    ax.axhline(RMS_THR, ls=":", color="gray", lw=1, label=f"{RMS_THR:.0e} threshold")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlim(ax_lo, ax_hi); ax.set_ylim(ax_lo, ax_hi)
    ax.set_xlabel("real VF RMS (n_p=4)"); ax.set_ylabel("complex VF RMS (n_p=4)")
    ax.set_title(f"exp10b: real vs complex VF residual, n_p=4 ({args.design}, band [{lo:g},{hi:g}] Hz)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    ppath = out_path("exp10", "exp10b_residual_real_vs_complex.png")
    fig.savefig(ppath, dpi=130); plt.close(fig)
    print(f"wrote {ppath}")

    print(f"VERDICT: {verdict}")
    return 0


# ---------- EXP-10b: --lowfreq-diag mode (0.1-2 Hz linear Y model, all three designs, no VF) ----------

def lowfreq_diag_main():
    t0 = time.time()
    B_THR_MS = 0.01
    designs = ["260729", "260804", "s5m6585"]
    result = dict(exp="exp10b_lowfreq_diag", params=dict(fit_band_hz=[0.1, 2.0], B_threshold_mS=B_THR_MS), designs={})
    frac_b_over = {}
    summary_rows = []

    for design in designs:
        npz_path = ref_npz(design)
        d = np.load(npz_path, allow_pickle=True)
        freq_full = d["freq"]; Zdiag = d["Zdiag"]; names = [str(x) for x in d["port_names"]]
        n_ports = len(names)
        fit_mask = (freq_full >= 0.1) & (freq_full <= 2.0)
        f_fit = freq_full[fit_mask]
        omega = 2 * np.pi * f_fit
        A = np.stack([np.ones_like(f_fit), omega], axis=1).astype(complex)
        Z0_file = Zdiag[0, :]
        re_neg_mask_full = Zdiag.real < 0

        port_rows = []
        G_mS = np.empty(n_ports); B_mS = np.empty(n_ports); C_uF = np.empty(n_ports)
        max_resid = np.empty(n_ports); z0_reldiff = np.empty(n_ports)
        for i, name in enumerate(names):
            Y = 1.0 / Zdiag[fit_mask, i]
            x, *_ = np.linalg.lstsq(A, Y, rcond=None)
            x0, x1 = x[0], x[1]
            G_mS[i] = x0.real * 1e3
            B_mS[i] = x0.imag * 1e3
            C_uF[i] = x1.imag * 1e6
            eps = np.abs(A @ x - Y) / np.abs(Y)
            max_resid[i] = float(np.max(eps))
            z0_reldiff[i] = float(np.abs(Z0_file[i] - 1.0 / x0) / np.abs(Z0_file[i]))
            fneg = freq_full[re_neg_mask_full[:, i]]
            reZ_neg_max_f = float(fneg.max()) if fneg.size else None
            port_rows.append(dict(name=name, G_mS=float(G_mS[i]), B_mS=float(B_mS[i]), C_uF=float(C_uF[i]),
                                   max_resid=float(max_resid[i]), reZ_neg_max_f=reZ_neg_max_f))

        n_b_over = int(np.sum(np.abs(B_mS) > B_THR_MS))
        frac_b_over[design] = n_b_over / n_ports
        n_g_neg = int(np.sum(G_mS < 0))
        neg_ports = [p for p in port_rows if p["reZ_neg_max_f"] is not None]
        max_reZ_neg_f = max((p["reZ_neg_max_f"] for p in neg_ports), default=None)

        spd = spd_path(design)
        dc_bbs_line = grep_first_line(spd, b".DC_BBS_Setting")

        summ = dict(
            n_ports=n_ports,
            B_mS_stats=dict(min=float(np.min(B_mS)), median=float(np.median(B_mS)), max=float(np.max(B_mS))),
            n_B_over_0p01mS=n_b_over, frac_B_over_0p01mS=frac_b_over[design],
            G_mS_stats=dict(min=float(np.min(G_mS)), median=float(np.median(G_mS)), max=float(np.max(G_mS))),
            n_G_negative=n_g_neg,
            n_reZ_negative=len(neg_ports), max_reZ_negative_f_hz=max_reZ_neg_f,
            median_max_relative_residual=float(np.median(max_resid)),
            median_z0_relative_diff=float(np.median(z0_reldiff)),
            dc_bbs_setting_line=dc_bbs_line,
            spd_path=str(spd), npz_path=str(npz_path), npz_sha256=sha256(npz_path),
            ports=port_rows,
        )
        result["designs"][design] = summ
        summary_rows.append((design, summ))

    H_common = all(v >= 0.5 for v in frac_b_over.values())
    result["H_common"] = bool(H_common)

    print(f"{'design':10s} {'n_ports':>7s} {'B min/med/max mS':>24s} {'n|B|>.01mS':>11s} {'frac':>6s} "
          f"{'nG<0':>5s} {'nReZ<0':>7s} {'maxF(ReZ<0)':>12s} {'medMaxResid':>12s} {'medZ0reldiff':>13s}")
    for design, s in summary_rows:
        bstats = s["B_mS_stats"]
        bstr = f"{bstats['min']:.3g}/{bstats['median']:.3g}/{bstats['max']:.3g}"
        maxf_str = f"{s['max_reZ_negative_f_hz']:.3g}" if s["max_reZ_negative_f_hz"] is not None else "n/a"
        print(f"{design:10s} {s['n_ports']:7d} {bstr:>24s} {s['n_B_over_0p01mS']:11d} {s['frac_B_over_0p01mS']:6.2f} "
              f"{s['n_G_negative']:5d} {s['n_reZ_negative']:7d} {maxf_str:>12s} "
              f"{s['median_max_relative_residual']:12.3e} {s['median_z0_relative_diff']:13.3e}")
        print(f"    DC_BBS_Setting: {s['dc_bbs_setting_line']}")
    print(f"H_common (all three frac(|B|>0.01mS)>=0.5): {H_common}")

    wall = time.time() - t0
    result["stats"] = dict(wall_seconds=wall, peak_rss_mb=peak_rss_mb())
    jpath = out_path("exp10", "result_exp10b_lowfreq_diag.json")
    jpath.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {jpath}")
    print(f"wall {wall:.1f} s, peak RSS {result['stats']['peak_rss_mb']:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
