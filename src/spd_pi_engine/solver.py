"""Linear-algebra backends for the per-frequency solve, and the frequency-independent caches.

`YPattern` / `trace_zs` / `weff` are EXP-37 (`common/fast_assemble.py`); `CudssLU` is the optional
cuDSS (A2000) stand-in for `scipy.splu` (`common/cudss_solver.py`).  Ported line for line.

Plan C8/C9: the import-time `SPD_PI_FAST` module flag `ON` and the runtime `SPD_PI_SOLVER` read
are gone -- the caller decides with `Backend(fast=…, solver=…)`, and `CudssLU` takes a `backend`
for the two cuDSS knobs that were hardcoded (host_nthreads, ir_steps).


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

cuDSS: all nvmath/CUDA imports are lazy and local to this module, so nothing here is touched
unless the caller asks for it.  The per-frequency matrices share one sparsity pattern (the mesh
does not depend on f), so the analysis/reordering phase runs once per model and only the numerical
factorization repeats.  Requires (global Python): nvmath-python, nvidia-cudss-cu12,
cuda-bindings==12.*.
"""
from __future__ import annotations

import glob
import os
import time

import numpy as np
from scipy import sparse

from .backend import DEFAULT, Backend  # noqa: F401  (Backend re-exported for callers)


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



# ----------------------------------------------------------------------------
# cuDSS
# ----------------------------------------------------------------------------
def _mtlayer():
    """Path to the cuDSS CPU multithreading layer shipped in the nvidia-cudss-cu12 wheel.

    Without it cuDSS runs the (host-side) reordering single-threaded and host_nthreads cannot
    be set at all.  `nvidia` is a namespace package, so go through __path__, not __file__.
    """
    try:
        import nvidia
        roots = list(getattr(nvidia, "__path__", []))
    except ImportError:
        return None
    for pat in ("cudss_mtlayer_*.dll", "libcudss_mtlayer_*.so*"):
        for root in roots:
            hits = sorted(glob.glob(os.path.join(root, "**", pat), recursive=True))
            if hits:
                return hits[0]
    return None


def _device_name(device_id=0):
    try:
        from cuda.bindings import runtime as cudart
        err, props = cudart.cudaGetDeviceProperties(device_id)
        name = props.name
        return name.decode() if isinstance(name, bytes) else str(name)
    except Exception:
        return f"cuda:{device_id}"


class CudssLU:
    """One cuDSS DirectSolver per model: plan once, refactorize + solve per frequency.

    `solve(Y)` returns (V, stats) for the single RHS e_P, matching what splu(Y).solve(e_P)
    returns in Model3.solve().
    """

    def __init__(self, Y_csc, P, log=print, device_id=0, backend=DEFAULT):
        import nvmath.sparse.advanced as A  # lazy: pulls in CUDA

        self._A = A
        self.log = log
        self.P = int(P)
        self.device = _device_name(device_id)
        self._replanned = False

        self._a = Y_csc.tocsr()
        self._pattern = (self._a.indptr.tobytes(), self._a.indices.tobytes())
        rhs = np.zeros(self._a.shape[0], complex)
        rhs[self.P] = 1.0

        mt = _mtlayer()
        self._solver = A.DirectSolver(
            self._a, rhs,
            options=dict(sparse_system_type=A.DirectSolverMatrixType.GENERAL,
                         sparse_system_view=A.DirectSolverMatrixViewType.FULL,
                         multithreading_lib=mt),
            execution=A.ExecutionCUDA(device_id=device_id))
        if mt:
            self._solver.plan_config.host_nthreads = backend.host_nthreads
        # cuDSS does no iterative refinement by default.  One step costs ~10 % of a factor+solve and
        # takes the largest ports from ~3e-8 to ~1e-9 relative on Z, i.e. back inside the 1e-8 GPU
        # accuracy budget (EXP-37: 260729 Port7, N 586k, 3.02e4 Hz -- 3.34e-8 -> 1.05e-9; a second
        # step buys 1.4e-10 for another 10 %, which the budget does not need).
        self._solver.solution_config.ir_num_steps = backend.ir_steps
        self._solver.plan()

    def solve(self, Y_csc):
        a = Y_csc.tocsr()
        pattern = (a.indptr.tobytes(), a.indices.tobytes())
        same = pattern == self._pattern
        t0 = time.time()
        if same:
            # nvmath keeps its own device copy of a host operand, so mutating self._a.data is
            # not enough -- reset_operands re-uploads it.  Same buffers => the plan survives.
            self._a.data[:] = a.data
        else:
            if not self._replanned:
                self.log("[solver] cudss: sparsity pattern changed, re-planning")
                self._replanned = True
            self._a, self._pattern = a, pattern
        self._solver.reset_operands(a=self._a)
        if not same:
            self._solver.plan()
        info = self._solver.factorize()
        nnz_lu = int(info.lu_nnz)  # must be read before free()
        t1 = time.time()
        V = np.asarray(self._solver.solve()).copy()
        t2 = time.time()
        return V, dict(factor_s=t1 - t0, solve_s=t2 - t1, nnz_LU=nnz_lu, solver="cudss")

    def free(self):
        """Drop the solver without tearing it down.

        cuDSS 0.8.0.10 faults (0xC0000005) inside DirectSolver.free(); how often depends on the
        matrix size and on how many factorizations ran, so there is no safe count to gate on.
        Every run that skips the teardown is clean, so the handle is left to process exit.  The
        caller keeps one solver per model, so at most one is leaked per process.
        """
        self._solver = None


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


def demo_cudss():
    """Self-check against scipy.splu on two small systems with a shared sparsity pattern."""
    from scipy import sparse
    from scipy.sparse.linalg import splu

    n, P = 400, 7
    rng = np.random.default_rng(0)
    base = sparse.random(n, n, density=0.02, format="csr", random_state=1)
    pat = ((base + base.T) != 0).astype(complex) + sparse.eye(n, dtype=complex, format="csr")
    rhs = np.zeros(n, complex)
    rhs[P] = 1.0

    gpu = CudssLU(pat.tocsc(), P, backend=Backend(solver="cudss"))
    for k in range(2):
        vals = pat.copy()
        vals.data = vals.data * (1.0 + 0.5 * k) + 1j * rng.normal(size=vals.nnz)
        Y = vals.tocsc()
        V, st = gpu.solve(Y)
        ref = splu(Y, permc_spec="COLAMD").solve(rhs)
        rel = np.max(np.abs(V - ref)) / np.max(np.abs(ref))
        print(f"  pass {k}: rel {rel:.2e} nnz_LU {st['nnz_LU']} factor {st['factor_s']:.3f}s")
        assert rel < 1e-8, rel
        assert st["solver"] == "cudss"
    gpu.free()
    print(f"DEMO PASS on {gpu.device}")


if __name__ == "__main__":
    demo()
