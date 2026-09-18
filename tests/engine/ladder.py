"""The frequency ladder the exp28/exp30 receipts were computed on (plan C17).

`run11.run` (= `WORK_DIR/engine_w2/w2_gates.ladder_freqs` = `WORK_DIR/engine_w4/w4_gate.py`):
the 7-point `exp5/pipeline.LADDER` plus 21 log-spaced points over 3e5..3e7, each snapped to the
nearest point of the PowerSI reference grid, de-duplicated and sorted.  27 points for every case
so far.  The engine itself takes an arbitrary frequency array; only the reproduction tests snap.
"""
from __future__ import annotations

import numpy as np

LADDER = [3.0e4, 1.0e5, 3.0e5, 1.0e6, 2.5e6, 1.0e7, 1.0e8]  # exp5/pipeline.LADDER


def ladder_freqs(fref) -> np.ndarray:
    fref = np.asarray(fref, float)
    dense = np.logspace(np.log10(3e5), np.log10(3e7), 21)
    idx = sorted({int(np.argmin(abs(fref - x))) for x in list(LADDER) + list(dense)})
    return fref[idx]


def ref_of(npz_path, port):
    """(full PowerSI grid, this port's Zdiag column) -- `run11.ref_of` / `w4_gate.ref_of`."""
    ref = np.load(npz_path, allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == port][0]
    return ref["freq"], ref["Zdiag"][:, col]
