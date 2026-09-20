"""W14-c: the mesh convergence manager (plan `docs/engine/W14_PLAN_2026-09-20.md` §W14-c).

Pure orchestration on top of W14-a's `Rail.mesh` -> `MeshedRail.solve`: mesh and solve the rail
twice -- once on the options it was given, once on a variant grid -- and compare |Z| in dB.  No
numerics live here; the five numeric modules are untouched and `numerics_id` does not change.

    conv = check_mesh_convergence(rail, opt, freqs, pair="coarse")
    conv.rms_db, conv.max_db, conv.converged, conv.f_res_shift_pct, conv.cost
    conv.ref                # the `Result` a plain `rail.mesh(opt).solve(freqs)` gives -- the same
                            # solve, not a second one, so the check costs one extra mesh+solve
    conv.product_dict()     # the product's `convergence` dict shape

The variant keeps the sub-tile physical size (h / sub_c = 10 um on the frozen grid), exactly as the
W14-b study did: `"coarse"` is h*2 with sub_c*2, `"fine"` is h/2 with sub_c//2.  Every other option
is passed through unchanged (`dataclasses.replace`), including `fine_box`, which `Rail.mesh` fills
from the rail itself when it is `None` -- so both grids see the same fine box.

**This is a measurement, not a gate the engine passes by default.**  W14-b measured both pairs on
the nine reproduction cases and neither converges (verdict R3, `docs/engine/W14B_REPORT.md` §4):
(400 vs 200) fails 1/9 cases, (100 vs 200) fails 8/9.  The product-side wiring (a
`convergence_check` profile option, removing the W11-b exemption) is therefore **deferred to the
owner** together with the default-mesh decision -- `product_dict()` exists so that wiring is a
call, not a rewrite, and nothing in `src/spd_decap_pi` imports this module.

Cost (W14B §6): the check is one extra mesh + solve, i.e. about +113..129 % wall on package rails
and +60 % on PCB rails for `"coarse"`; `"fine"` is more (h/2 doubles the unknowns).
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass

import numpy as np

from .backend import DEFAULT
from .model import ModelOptions

PAIRS = ("coarse", "fine")

#: the product's amplitude tolerances (`_core/solver/evaluator.py` DEFAULT_RMS/MAX_TOLERANCE_DB),
#: applied to every frequency point -- the frozen W14-b rule.
RMS_DB_TOL, MAX_DB_TOL = 0.2, 0.5


def mesh_delta(z_var, z_ref):
    """`(delta_db, rms_db, max_db)` for delta_i = 20*log10(|Z_var,i| / |Z_ref,i|).

    The W14-b metric as a pure function (`tools/engine_studies/w14b_mesh_sensitivity.metrics`):
    per-point dB ratio, RMS over the points, max |delta|.
    """
    d = 20.0 * np.log10(np.abs(np.asarray(z_var)) / np.abs(np.asarray(z_ref)))
    return d, float(np.sqrt(np.mean(d ** 2))), float(np.abs(d).max())


def variant_options(options: ModelOptions, pair: str) -> ModelOptions:
    """The variant grid's options: h*2 / sub_c*2 ("coarse") or h/2 / sub_c//2 ("fine").

    The sub-tile physical size h/sub_c is held constant, so only the coarse cell pitch moves and
    the homogenisation raster resolution does not (plan §W14-b).
    """
    if pair not in PAIRS:
        raise ValueError(f"pair must be one of {PAIRS}, not {pair!r}")
    sub = tuple(options.sub)
    if pair == "coarse":
        return dataclasses.replace(options, h=options.h * 2.0, sub=(sub[0] * 2,) + sub[1:])
    return dataclasses.replace(options, h=options.h / 2.0, sub=(sub[0] // 2,) + sub[1:])


@dataclass
class MeshConvergence:
    """One mesh refinement check: the reference solve, the variant solve and the dB metric."""

    pair: str                   # "coarse" | "fine"
    h_ref: float
    h_var: float
    rms_db: float
    max_db: float
    converged: bool             # rms_db <= rms_db_tol and max_db <= max_db_tol
    delta_db: np.ndarray        # per frequency, 20*log10(|Z_var| / |Z_ref|)
    f_res_shift_pct: float      # informational: min |Z| frequency, variant vs reference
    ref: object                 # `Result` -- what `rail.mesh(options).solve(freqs)` returns
    var: object                 # `Result` of the variant grid
    cost: dict                  # {"ref"/"var": {"unknowns", "wall_seconds"}}
    rms_db_tol: float = RMS_DB_TOL
    max_db_tol: float = MAX_DB_TOL

    def __repr__(self):
        return (f"MeshConvergence(pair={self.pair!r}, h={self.h_ref}->{self.h_var}, "
                f"rms={self.rms_db:.3f} dB, max={self.max_db:.3f} dB, converged={self.converged})")

    def product_dict(self) -> dict:
        """The product's `convergence` dict shape (W11-b's fail-closed gate reads these keys).

        The engine has no modal order -- one 2-D mesh, direct sparse LU, no modal basis to truncate
        -- so the `modal_*` keys mirror the mesh check and `note` says so.  Nothing in the product
        calls this yet (W14-b verdict R3, see the module docstring).
        """
        return dict(
            converged=self.converged,
            frequency_converged=self.converged,
            modal_converged=self.converged,
            note=("the engine has no modal order (one 2-D mesh, direct sparse LU), so "
                  "modal_converged mirrors the mesh refinement check rather than a mode sweep"),
            frequency_rms_delta_db=self.rms_db,
            frequency_max_delta_db=self.max_db,
            modal_rms_delta_db=self.rms_db,
            modal_max_delta_db=self.max_db,
            mesh_pair=self.pair,
            h_ref=self.h_ref,
            h_var=self.h_var,
            tolerance_db=dict(rms=self.rms_db_tol, max=self.max_db_tol))


def check_mesh_convergence(rail, options: ModelOptions | None, freqs, pair: str = "coarse",
                           backend=DEFAULT, rms_db_tol: float = RMS_DB_TOL,
                           max_db_tol: float = MAX_DB_TOL, log=None) -> MeshConvergence:
    """Solve `rail` on `options`' grid and on the `pair` variant grid, and compare |Z| in dB.

    `conv.ref` is the reference solve itself, so a caller that wanted the Z(f) anyway pays for one
    extra mesh+solve and not two.  The variant's solver is released afterwards (`release_solver`);
    the reference's is left alone, because `conv.ref`'s model is the one the caller keeps.
    """
    opt = options or ModelOptions()
    t0 = time.time()
    ref_mesh = rail.mesh(opt, backend, log)
    ref = ref_mesh.solve(freqs)
    t1 = time.time()
    var_mesh = rail.mesh(variant_options(opt, pair), backend, log)
    var = var_mesh.solve(freqs)
    var_mesh.release_solver()
    t2 = time.time()

    delta, rms, mx = mesh_delta(var.Z, ref.Z)
    f = np.asarray(ref.freq, float)
    f_ref = float(f[int(np.argmin(np.abs(ref.Z)))])
    f_var = float(f[int(np.argmin(np.abs(var.Z)))])
    return MeshConvergence(
        pair=pair, h_ref=float(ref_mesh.options.h), h_var=float(var_mesh.options.h),
        rms_db=rms, max_db=mx, converged=bool(rms <= rms_db_tol and mx <= max_db_tol),
        delta_db=delta, f_res_shift_pct=(f_var - f_ref) / f_ref * 100.0, ref=ref, var=var,
        cost=dict(ref=dict(unknowns=int(ref_mesh.summary["unknowns"]), wall_seconds=t1 - t0),
                  var=dict(unknowns=int(var_mesh.summary["unknowns"]), wall_seconds=t2 - t1)),
        rms_db_tol=rms_db_tol, max_db_tol=max_db_tol)


__all__ = ["MAX_DB_TOL", "MeshConvergence", "PAIRS", "RMS_DB_TOL", "check_mesh_convergence",
           "mesh_delta", "variant_options"]
