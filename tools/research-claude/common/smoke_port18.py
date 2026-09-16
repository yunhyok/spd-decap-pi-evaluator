#!/usr/bin/env python
"""Smoke test: recompute the EXP-8 adopted model (S2 + (b) + cavity-wall reference) for 260729 Port18_SITE0
at 1 MHz only and compare with the committed receipt
docs/research-claude/2026-09-15/results/exp8/result_260729_Port18_SITE0_any.json.

Uses WORK_DIR/exp5/extract_260729_Port18_SITE0.pkl and WORK_DIR/exp1/neighbour_shapes.pkl when present
(otherwise pipeline.prepare re-extracts from the SPD in DATA_DIR, several minutes).  Same code path as
exp8/run8.py run(); no parameter is changed.  PASS = |Z - Z_receipt| / |Z_receipt| <= 1e-6.
Expected: err vs PowerSI = 2.69 %, rel diff 0, ~1.3 GB peak RSS, ~3 min on the cloud container (build 155 s dominates).
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("../exp8", "../exp5", "../exp4", "../exp3", "../exp1", "."):
    sys.path.insert(0, os.path.join(HERE, p))
import pipeline as P5  # noqa: E402
import model3 as M3  # noqa: E402
import run4 as R4  # noqa: E402
import run8 as R8  # noqa: E402
from paths import REPO_DIR, peak_rss_mb, ref_npz  # noqa: E402

TAG, PORT, F = "260729", "Port18_SITE0", 1.0e6


def main() -> int:
    t0 = time.time()
    rec_fn = REPO_DIR / "docs" / "research-claude" / "2026-09-15" / "results" / "exp8" / f"result_{TAG}_{PORT}_any.json"
    rec = json.loads(rec_fn.read_text(encoding="utf-8"))
    k = int(np.argmin(np.abs(np.array(rec["freq"]) - F)))
    z_rec = complex(rec["Z_re"][k], rec["Z_im"][k]); zr_rec = complex(rec["Zref_re"][k], rec["Zref_im"][k])

    R4.patch_traces("b")
    M3.TwoSided = R8.TwoSidedAny
    ex, shapes, fine_box = P5.prepare(TAG, PORT)
    ref = np.load(ref_npz(TAG), allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == PORT][0]
    fref = ref["freq"]; i = int(np.argmin(np.abs(fref - F)))
    zr = complex(ref["Zdiag"][i, col])
    mdl = P5.ModelB(ex, shapes, h=200.0, fh=50.0, top_h=50.0, fine_box=fine_box, sub_c=20, sub_f=10, sub_top=10, fringe=True)
    Z, _ = mdl.solve([fref[i]], verbose=False)
    z = complex(Z[0])
    rel_vs_receipt = abs(z - z_rec) / abs(z_rec)
    err_ref = abs(z - zr) / abs(zr)
    ok = rel_vs_receipt <= 1e-6 and mdl.N == rec["unknowns"] and abs(zr - zr_rec) <= 1e-12 * abs(zr_rec)
    print(f"f={fref[i]:.6g} Hz unknowns={mdl.N} (receipt {rec['unknowns']})")
    print(f"Z model   = {z.real*1e3:+.6f} {z.imag*1e3:+.6f}j mOhm")
    print(f"Z receipt = {z_rec.real*1e3:+.6f} {z_rec.imag*1e3:+.6f}j mOhm  rel diff {rel_vs_receipt:.2e}")
    print(f"err vs PowerSI = {100*err_ref:.2f} %  (receipt err_1MHz {100*rec['err_1MHz']:.2f} %)")
    print(f"wall {time.time()-t0:.0f} s, peak RSS {peak_rss_mb():.0f} MB")
    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
