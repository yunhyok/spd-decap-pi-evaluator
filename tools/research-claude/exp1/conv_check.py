"""Mesh-halving check at 1 MHz, 10 MHz and across the ~9-15 MHz antiresonance
for variants A and B; merges into result.json["convergence"]."""
import json
import os
import pickle
import resource
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from run_exp1 import VARIANTS  # noqa: E402

OUT = "/home/claude/work/exp1"
ex = pickle.load(open(os.path.join(OUT, "extract_port18.pkl"), "rb"))
ref = np.load("/home/claude/data/S4LB002_260729_Zdiag.npz", allow_pickle=True)
fref = ref["freq"]; zref = ref["Zdiag"][:, 17]
res = json.load(open(os.path.join(OUT, "result.json")))
box = res["discretisation"]["fine_box_um"]
pk = [float(f) for f in fref if 6e6 <= f <= 16e6]
freqs = np.array(sorted(set([1e6, 1e7] + pk)))
zr = np.array([zref[int(np.argmin(abs(fref - f)))] for f in freqs])
conv = {}
for v in ("A", "B"):
    opt = VARIANTS[v]
    zz = {}
    for tag, (h, fh, th) in {"h": (200.0, 50.0, 50.0), "h_half": (100.0, 25.0, 25.0)}.items():
        t = time.time()
        m = M.build_model(ex, h, top_h_um=th, fine_box=box, fine_h_um=fh, gnd_via_r_factor=opt["gnd_via_r_factor"],
                          include_gnd_sheet_r=opt["include_gnd_sheet_r"], verbose=False)
        Z, st = M.solve(m, ex, freqs, verbose=False)
        zz[tag] = dict(unknowns=m.n, Z=Z, sec=time.time() - t, nnz_LU=st[0]["nnz_LU"], factor_s_mean=float(np.mean([s["factor_s"] for s in st])))
        print(v, tag, m.n, f"{time.time()-t:.1f}s", flush=True)
        del m
    Zh, Zh2 = zz["h"]["Z"], zz["h_half"]["Z"]
    kpk = [i for i, f in enumerate(freqs) if 6e6 <= f <= 16e6]
    def peak(Z):
        i = kpk[int(np.argmax(Z.real[kpk]))]
        return float(freqs[i]), float(Z.real[i])
    conv[v] = dict(
        h=200.0, fine_h=50.0, top_h=50.0, h_half=100.0, fine_h_half=25.0, top_h_half=25.0,
        unknowns_h=zz["h"]["unknowns"], unknowns_h_half=zz["h_half"]["unknowns"],
        nnz_LU_h=zz["h"]["nnz_LU"], nnz_LU_h_half=zz["h_half"]["nnz_LU"],
        factor_s_mean_h=zz["h"]["factor_s_mean"], factor_s_mean_h_half=zz["h_half"]["factor_s_mean"],
        freq=[float(x) for x in freqs],
        Z_h=[[float(z.real), float(z.imag)] for z in Zh], Z_h_half=[[float(z.real), float(z.imag)] for z in Zh2],
        rel_change=[float(abs(a - b) / abs(b)) for a, b in zip(Zh, Zh2)],
        rel_err_ref_h=[float(abs(a - b) / abs(b)) for a, b in zip(Zh, zr)],
        rel_err_ref_h_half=[float(abs(a - b) / abs(b)) for a, b in zip(Zh2, zr)],
        antiresonance_ReZ_peak_h=peak(Zh), antiresonance_ReZ_peak_h_half=peak(Zh2), antiresonance_ReZ_peak_ref=peak(zr),
    )
    i1 = list(freqs).index(1e6); i10 = list(freqs).index(1e7)
    print(v, "dZ(1MHz)", conv[v]["rel_change"][i1], "dZ(10MHz)", conv[v]["rel_change"][i10],
          "peaks", conv[v]["antiresonance_ReZ_peak_h"], conv[v]["antiresonance_ReZ_peak_h_half"], conv[v]["antiresonance_ReZ_peak_ref"], flush=True)
res["convergence"] = conv
res["peak_rss_MB_convergence_run"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
json.dump(res, open(os.path.join(OUT, "result.json"), "w"), indent=1)
print("peak RSS MB", res["peak_rss_MB_convergence_run"])
