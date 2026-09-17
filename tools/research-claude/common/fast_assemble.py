"""EXP-37: opt-in per-frequency assemble acceleration.  Off unless SPD_PI_FAST=1.

No physics change.  Everything cached here is frequency-independent, so each frequency only
recomputes the value vector:

  YPattern  the COO (rows, cols) of Y do not depend on f (the mesh does not), nor does the
            in-range mask, so keep them and per frequency rebuild only the value vector.  This is
            what lets assemble() skip self.map / np.where on every edge array at every frequency.
  trace_zs  copper_surface_impedance takes a scalar (sigma, t), so model3.assemble calls it once
            per off-plane trace.  There are only a handful of distinct (sigma, t) pairs; call it
            once per pair and gather.  Same scalar call, so the values are bit-identical.
  weff      run4.Model4.weff_of (the fringe branch over every sheet edge) has no f in it, but
            edge_z rebuilds it at every frequency.

With the flag unset none of this is reachable: model3.assemble / run4.edge_z keep every original
line, so the default (splu) path stays byte-identical.

Y itself is bit-identical either way -- every cached quantity is an input to the same arithmetic,
not a refolding of it.  That matters: at the lowest frequencies the system amplifies a 1e-16 in Y
into ~8e-9 in Z (measured on 260804 Port18 at 3.02e4 Hz), which would eat the whole 1e-8 accuracy
budget that the cuDSS factorisation already uses half of.

EXP-37 verification (docs/research-claude/2026-09-15/results/exp37): Port14 at 1e6 and 1e8 Hz,
same CSC indptr/indices and max |dY| = 0; 7 cases vs the exp28/p receipts, max |dZ|/|Z| <= 1e-8.
"""
from __future__ import annotations

import os

import numpy as np
from scipy import sparse

ON = os.environ.get("SPD_PI_FAST", "") == "1"
MU0 = 4e-7 * np.pi


class YPattern:
    """The kept (row, col) of Y, built once; per frequency only the values are rebuilt.

    scipy still does the COO -> CSC conversion, on exactly the triplet the default path would have
    passed it, so Y is bit-identical.  Summing the duplicates here instead (np.add.reduceat over a
    lexsorted copy) is ~2x faster again but cannot match scipy bit for bit -- csr_sort_indices uses
    an unstable sort, so the order within a (row, col) slot is not reproducible -- and the 1e-16 it
    leaves in Y comes out as ~8e-9 in Z at the lowest frequencies (260804 Port18 at 3.02e4 Hz).
    """

    def __init__(self, rows, cols, N):
        self.take = np.nonzero((rows >= 0) & (cols >= 0) & (rows < N) & (cols < N))[0]
        self.rows, self.cols = rows[self.take], cols[self.take]
        self.shape = (N, N)

    def csc(self, V):
        return sparse.coo_matrix((V[self.take], (self.rows, self.cols)), shape=self.shape).tocsc()


def trace_zs(mdl, zs, f, sig, t):
    """[complex(zs(f, s, t)) for s, t in zip(sig, t)] with one call per distinct (sigma, t)."""
    g = getattr(mdl, "_fa_tr", None)
    if g is None:
        _, first, inv = np.unique(np.column_stack([sig, t]), axis=0, return_index=True, return_inverse=True)
        g = mdl._fa_tr = (first, inv.ravel())
    first, inv = g
    return np.array([complex(zs(f, sig[k], t[k])) for k in first])[inv]


def weff(mdl, sh):
    """mdl.weff_of(sh)[0] of one rail sheet, built once.

    edge_z then does its original arithmetic on it, so R and XL stay bit-identical.  Folding the
    constants in instead (e.g. R = zs*((ell/wid)/G)) would move them by one ulp -- see the module
    docstring for why that is not free.  GND sheets never reach this: edge_z returns before
    weff_of when rail=False.
    """
    q = getattr(sh, "_fa_weff", None)
    if q is None:
        q = sh._fa_weff = mdl.weff_of(sh)[0]
    return q


def demo():
    """Self-check: YPattern reproduces coo_matrix(...).tocsc() including duplicate summing."""
    rng = np.random.default_rng(0)
    N, M = 60, 4000
    rows = rng.integers(-3, N + 2, M)          # out-of-range entries must be dropped, like `ok`
    cols = rng.integers(-3, N + 2, M)
    V = rng.normal(size=M) + 1j * rng.normal(size=M)
    ok = (rows >= 0) & (cols >= 0) & (rows < N) & (cols < N)
    ref = sparse.coo_matrix((V[ok], (rows[ok], cols[ok])), shape=(N, N)).tocsc()
    p = YPattern(rows, cols, N)
    got = p.csc(V)
    assert (got.indptr == ref.indptr).all() and (got.indices == ref.indices).all()
    assert np.array_equal(got.data, ref.data), np.max(np.abs(got.data - ref.data))
    # the same cached pattern must stay right for a second, different value vector
    V2 = V * 3.0 + 1.0
    ref2 = sparse.coo_matrix((V2[ok], (rows[ok], cols[ok])), shape=(N, N)).tocsc()
    assert np.array_equal(p.csc(V2).data, ref2.data)
    print(f"  nnz {ref.nnz} of {M} COO entries, bit-identical to coo_matrix(...).tocsc()")
    print("DEMO PASS")


if __name__ == "__main__":
    demo()
