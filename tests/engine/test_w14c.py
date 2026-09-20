"""W14-c: the mesh convergence manager (plan `docs/engine/W14_PLAN_2026-09-20.md` §W14-c).

`check_mesh_convergence` is pure orchestration over W14-a's `Rail.mesh`/`MeshedRail.solve`, so the
two things that can break are (1) the reference solve is not the plain one any more -- a caller
that asks for the check must get exactly the Z(f) it would have got without it -- and (2) the
variant grid is not the W14-b grid.  Both are asserted here, on the same case W14-b measured:
260729 Port14_SITE0, `"coarse"` = h 200 -> 400 um with `sub_c` 20 -> 40 (N 34 424 -> 22 566,
`docs/engine/W14B_REPORT.md` §3).

The metric itself (delta_db / RMS / max) is a pure function and is tested on synthetic arrays --
no data, no build.

Cost: three builds (the check's two plus the plain reference it is compared against) on
`Backend(solver="splu", fast=True)`, which is bit-identical to the slow path (EXP-37/38/40).

    pytest tests/engine -q -k w14c
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from spd_pi_engine import Backend, Design, FLAGS_P, ModelOptions, check_mesh_convergence
from spd_pi_engine.convergence import MAX_DB_TOL, RMS_DB_TOL, mesh_delta, variant_options

#: the exp28/p receipt mesh (`test_reproduction.OPT`, `test_w14a.OPT`)
OPT = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True)
FREQS = np.array([1.0e5, 1.0e6, 1.0e7])
BACKEND = Backend(solver="splu", fast=True)

#: what the product's convergence gate reads (`_core/solver` W11-b), plus the mesh provenance
PRODUCT_KEYS = {"converged", "frequency_converged", "modal_converged", "note",
                "frequency_rms_delta_db", "frequency_max_delta_db", "modal_rms_delta_db",
                "modal_max_delta_db", "mesh_pair", "h_ref", "h_var", "tolerance_db"}


# ---------------------------------------------------------------- pure metric (no data)
def test_mesh_delta_is_the_w14b_metric():
    """delta_i = 20*log10(|Z_var,i| / |Z_ref,i|), RMS over the points, max over |delta|."""
    z_ref = np.array([1.0, 2.0, 4.0], dtype=complex)
    z_var = np.array([10.0, 2.0, 4.0j], dtype=complex)      # x10, x1, x1 (rotated -> same |Z|)

    d, rms, mx = mesh_delta(z_var, z_ref)
    assert d.shape == (3,)
    assert np.allclose(d, [20.0, 0.0, 0.0])
    assert rms == pytest.approx(20.0 / np.sqrt(3.0))        # sqrt(mean([400, 0, 0]))
    assert mx == pytest.approx(20.0)

    # the sign is kept per point; RMS and max are not sign-sensitive
    d2, rms2, mx2 = mesh_delta(z_ref, z_var)
    assert np.allclose(d2, -d)
    assert (rms2, mx2) == pytest.approx((rms, mx))

    # a half-amplitude variant is -6.02 dB everywhere
    d3, rms3, mx3 = mesh_delta(z_ref / 2.0, z_ref)
    assert np.allclose(d3, -20.0 * np.log10(2.0))
    assert rms3 == pytest.approx(20.0 * np.log10(2.0)) == mx3


def test_variant_options_keeps_the_sub_tile_size():
    coarse, fine = variant_options(OPT, "coarse"), variant_options(OPT, "fine")
    assert (coarse.h, coarse.sub) == (400.0, (40, 10, 10))
    assert (fine.h, fine.sub) == (100.0, (10, 10, 10))
    assert coarse.h / coarse.sub[0] == fine.h / fine.sub[0] == OPT.h / OPT.sub[0] == 10.0
    for k in ("reference", "flags", "fh", "top_h", "fringe", "fringe_wd", "max_layers", "fine_box"):
        assert getattr(coarse, k) == getattr(OPT, k) == getattr(fine, k)
    with pytest.raises(ValueError):
        variant_options(OPT, "finer")


# ---------------------------------------------------------------- the check itself (260729 P14)
def test_coarse_check_does_not_disturb_the_reference_solve(spd_path, cache_dir):
    rail = Design.open(spd_path("260729")).rail("Port14_SITE0", Path(cache_dir))
    conv = check_mesh_convergence(rail, OPT, FREQS, pair="coarse", backend=BACKEND)

    # (1) the reference Result is the plain one: same grid, same options, one solve
    plain = rail.mesh(OPT, BACKEND).solve(FREQS)
    assert np.array_equal(conv.ref.Z, plain.Z), (
        f"max |dZ| {np.abs(conv.ref.Z - plain.Z).max():.3e}, must be exactly 0 -- the manager must "
        f"not alter the options or re-solve")
    assert conv.ref.model.N == 34424                       # exp28/p fixture `unknowns`

    # (2) the variant is the W14-b coarse grid
    assert (conv.h_ref, conv.h_var) == (200.0, 400.0)
    assert tuple(conv.var.model.options.sub) == (40, 10, 10)
    assert conv.var.model.N == 22566                       # W14B_REPORT §3, 260729 Port14 h=400
    assert conv.cost["ref"]["unknowns"] == conv.ref.model.N
    assert conv.cost["var"]["unknowns"] == conv.var.model.N
    assert conv.cost["ref"]["wall_seconds"] > 0 and conv.cost["var"]["wall_seconds"] > 0

    # (3) the metric and the frozen threshold rule
    d = conv.delta_db
    assert d.shape == FREQS.shape and np.all(np.isfinite(d))
    assert conv.rms_db == pytest.approx(float(np.sqrt(np.mean(d ** 2))))
    assert conv.max_db == pytest.approx(float(np.abs(d).max()))
    assert (conv.rms_db_tol, conv.max_db_tol) == (RMS_DB_TOL, MAX_DB_TOL) == (0.2, 0.5)
    assert conv.converged == (conv.rms_db <= conv.rms_db_tol and conv.max_db <= conv.max_db_tol)
    assert isinstance(conv.f_res_shift_pct, float) and np.isfinite(conv.f_res_shift_pct)

    # (4) the product-format dict -- shape only; nothing in the product reads it yet (verdict R3)
    p = conv.product_dict()
    assert set(p) == PRODUCT_KEYS
    assert p["converged"] == p["frequency_converged"] == p["modal_converged"] == conv.converged
    assert "modal" in p["note"] and "mesh" in p["note"]
    assert p["frequency_rms_delta_db"] == p["modal_rms_delta_db"] == conv.rms_db
    assert p["frequency_max_delta_db"] == p["modal_max_delta_db"] == conv.max_db
    assert p["mesh_pair"] == "coarse" and (p["h_ref"], p["h_var"]) == (200.0, 400.0)
    assert p["tolerance_db"] == dict(rms=RMS_DB_TOL, max=MAX_DB_TOL)
