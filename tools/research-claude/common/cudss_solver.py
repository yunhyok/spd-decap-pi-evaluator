"""Optional cuDSS (NVIDIA GPU) stand-in for scipy.splu in the per-frequency solve loop.

Opt in with SPD_PI_SOLVER=cudss; the default path stays scipy.splu.  All nvmath/CUDA imports
are lazy and local to this module, so nothing here is touched unless the flag is set.

The per-frequency matrices share one sparsity pattern (the mesh does not depend on f), so the
analysis/reordering phase runs once per model and only the numerical factorization repeats.

Requires (global Python): nvmath-python, nvidia-cudss-cu12, cuda-bindings==12.*
"""
from __future__ import annotations

import glob
import os
import time

import numpy as np


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

    def __init__(self, Y_csc, P, log=print, device_id=0):
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
            self._solver.plan_config.host_nthreads = 4
        # cuDSS does no iterative refinement by default.  One step costs ~10 % of a factor+solve and
        # takes the largest ports from ~3e-8 to ~1e-9 relative on Z, i.e. back inside the 1e-8 GPU
        # accuracy budget (EXP-37: 260729 Port7, N 586k, 3.02e4 Hz -- 3.34e-8 -> 1.05e-9; a second
        # step buys 1.4e-10 for another 10 %, which the budget does not need).
        self._solver.solution_config.ir_num_steps = 1
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
    """Self-check against scipy.splu on two small systems with a shared sparsity pattern."""
    from scipy import sparse
    from scipy.sparse.linalg import splu

    n, P = 400, 7
    rng = np.random.default_rng(0)
    base = sparse.random(n, n, density=0.02, format="csr", random_state=1)
    pat = ((base + base.T) != 0).astype(complex) + sparse.eye(n, dtype=complex, format="csr")
    rhs = np.zeros(n, complex)
    rhs[P] = 1.0

    gpu = CudssLU(pat.tocsc(), P)
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
