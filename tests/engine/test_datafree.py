"""Data-free engine checks (plan §3, last bullet): these always run, need no SPD and are fast.

They are the module self-checks the W1-W4 sessions gated on, wired into pytest so a regression in
the numeric core shows up without the 1.1 GB SPD files.
"""
from __future__ import annotations

import numpy as np
import pytest

from spd_pi_engine import Backend, cache, geometry, homogenise, receipt, solver, spd_source
from spd_pi_engine.model import FLAGS_P
from spd_pi_engine.receipt import conventions_dict, numerics_id
from spd_pi_engine.reference import DEFAULT_CONVENTIONS

MESH = dict(h=200.0, fh=50.0, top_h=50.0, sub=(20, 10, 10), fringe=True, fringe_wd=None,
            max_layers=3)


def test_homogenise_selfcheck_face_fix(capsys):
    """EXP-28 `homog.selfcheck_face_fix`: face_fix restores the analytic strip conductance."""
    assert homogenise.selfcheck_face_fix() == 0
    assert "SELFCHECK-HOMOG PASS" in capsys.readouterr().out


def test_homogenise_demo():
    assert homogenise.demo() == 0


def test_solver_demo(capsys):
    """`YPattern` reproduces `coo_matrix(...).tocsc()` bit for bit, duplicates summed."""
    solver.demo()
    assert "DEMO PASS" in capsys.readouterr().out


def test_geometry_demo(capsys):
    """Scanline vs matplotlib on 60 synthetic polygons: 0 differing cells (plan §5-2)."""
    geometry.demo()
    out = capsys.readouterr().out
    assert "differing 0" in out and "DEMO PASS" in out


def test_geometry_vertices_on_grid_centres():
    """The case the scanline rule exists for: cell centres landing exactly on vertices and edges.

    A divided-out crossing x would round differently here; `geometry.inside` evaluates the C++
    predicate of matplotlib's `point_in_path_impl`, so it must agree cell for cell.
    """
    from matplotlib.path import Path as MplPath

    poly = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [1.0, 1.0], [0.0, 2.0]])  # concave notch
    xc = np.arange(-1.0, 4.0, 1.0)      # every centre is on a vertex row/column
    yc = np.arange(-1.0, 4.0, 1.0)
    got = geometry.inside(poly, xc, yc)
    X, Y = np.meshgrid(xc, yc)
    ref = MplPath(poly).contains_points(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
    assert got is not None
    assert np.array_equal(got, ref), np.argwhere(got != ref)


def test_cache_demo(capsys):
    """Cache v2 round-trip: no product objects in the pickle, stale sha / truncation -> miss."""
    cache.demo()
    assert "DEMO PASS" in capsys.readouterr().out


def test_receipt_demo(capsys):
    """G1-G5, `attach_reference`, `numerics_id`, `decap_config_sha256` on synthetic curves."""
    receipt.demo()
    assert "DEMO PASS" in capsys.readouterr().out


def test_parser_api():
    """C16: the private `spd_decap_pi._core.io.spd` symbols the adapter calls still exist."""
    spd_source.check_parser_api()
    assert spd_source.SPD_PARSER_SYMBOLS


def test_numerics_id_stable_and_flag_sensitive():
    conv = conventions_dict(DEFAULT_CONVENTIONS)
    a = numerics_id("powersi-compatible", FLAGS_P, MESH, conv)
    assert a == numerics_id("powersi-compatible", dict(FLAGS_P), dict(MESH), dict(conv))
    flipped = dict(FLAGS_P)
    key = "homog_face_fix"
    flipped[key] = not flipped[key]
    assert numerics_id("powersi-compatible", flipped, MESH, conv) != a, f"{key} did not move the id"
    assert numerics_id("physical-gnd", FLAGS_P, MESH, conv) != a


def test_backend_defaults():
    """`Backend()` is the research default path (plan C8/C9): splu, no accelerations, IR 1."""
    b = Backend()
    assert (b.solver, b.fast, b.ir_steps) == ("splu", False, 1)
    assert b.gpu() is None


@pytest.mark.gpu
def test_solver_demo_cudss(capsys):
    """cuDSS against scipy.splu on two systems sharing a sparsity pattern (needs the A2000)."""
    solver.demo_cudss()
