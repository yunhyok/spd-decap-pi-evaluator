"""Execution backend: the two research environment flags and the GPU latch, as one object.

Plan C7/C8/C9.  Nothing in the engine reads `os.environ`; the caller passes a `Backend`.

    SPD_PI_FAST=1      -> Backend(fast=True)     (EXP-37/38/39/40 accelerations)
    SPD_PI_SOLVER=cudss -> Backend(solver="cudss")  (cuDSS factorisation + cupy homogenisation)
    homog._GPU (module-global, process-wide latch) -> Backend.gpu(), per instance

`Backend()` is the research DEFAULT path: scipy.splu, no accelerations, no GPU.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Backend:
    """solver: "splu" (default, research path) | "cudss" (A2000) | "auto" (cudss, splu on failure).

    fast: the EXP-37..40 accelerations (bit-identical, verified per experiment).
    host_nthreads / ir_steps: cuDSS knobs, previously hardcoded in `common/cudss_solver.py`.

    W13: `host_nthreads=None` means "size it for this box" -- `OMP_NUM_THREADS` when the worker's
    parent set one (`cli.sweep` does, from `hardware.plan_threads`), else `plan_threads` on the
    detected profile.  The default stays **4**, the value every receipt so far was produced with:
    the cuDSS host-side reordering thread count can move the factorization, and this engine does
    not change numerics to tidy an API up.
    """

    solver: str = "splu"
    fast: bool = False
    host_nthreads: "int | None" = 4
    ir_steps: int = 1
    _gpu: object = field(default=None, init=False, repr=False, compare=False)

    def gpu(self):
        """cupy when the solver asks for the GPU and the A2000 answers, else None (logged once).

        Was `homog._gpu()`, a process-wide latch on `SPD_PI_SOLVER`; the latch now lives on the
        backend instance.  The batch handed to the kernel is unchanged, so the CG iteration count
        is the same as on numpy.
        """
        if self._gpu is None:
            self._gpu = False
            if self.solver in ("cudss", "auto"):
                try:
                    import cupy
                    float(cupy.zeros(1, np.float64).sum())
                    self._gpu = cupy
                except Exception as e:  # no cupy, no driver, no device -> numpy
                    print(f"[backend] cupy unavailable ({type(e).__name__}: {e}); "
                          f"batched_gx stays on numpy", flush=True)
        return self._gpu or None


DEFAULT = Backend()
"""The research default path.  Shared: it holds no state beyond the (never taken) GPU latch."""
