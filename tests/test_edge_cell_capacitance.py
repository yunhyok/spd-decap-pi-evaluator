from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from spd_decap_pi._core.solver.edge_cell_capacitance import (
    EPSILON_0_F_PER_M,
    EdgeCellCapacitanceError,
    EdgeCellConductor,
    EdgeCellDielectric,
    EdgeCellProblem,
    convergence_check,
    solve_edge_cell,
)


def _problem(*, p_x0: float = -100e-6, p_x1: float = 100e-6, dk: float = 3.4, h: float = 10e-6, max_cells: int = 20_000) -> EdgeCellProblem:
    # 20 um G / 30 um ABF / 20 um P / 30 um ABF / 20 um G.
    # The named G sheets are deliberately the explicit physical return.
    return EdgeCellProblem(
        x_min_m=-100e-6, x_max_m=100e-6, z_min_m=0.0, z_max_m=120e-6, cell_size_m=h,
        dielectrics=(EdgeCellDielectric(0.0, 120e-6, dk, "ABF"),),
        conductors=(
            EdgeCellConductor("G", -100e-6, 100e-6, 0.0, 20e-6),
            EdgeCellConductor("P", p_x0, p_x1, 50e-6, 70e-6),
            EdgeCellConductor("G", -100e-6, 100e-6, 100e-6, 120e-6),
        ), reference_terminal="G", max_cells=max_cells,
    )


def _index(result, terminal: str) -> int:
    return result.terminal_names.index(terminal)


def test_uniform_parallel_plate_detail_minus_baseline_is_zero() -> None:
    expected = EPSILON_0_F_PER_M * 3.4 * 200e-6 * (1.0 / 30e-6 + 1.0 / 30e-6)
    for h in (10e-6, 5e-6):
        result = solve_edge_cell(_problem(h=h))
        p = _index(result, "P")
        # This absolute gate catches the h/2 conductor-surface distance; a
        # delta-only test could let identically wrong raw/baseline terms pass.
        assert result.raw_maxwell_f_per_m[p, p] == pytest.approx(expected, rel=2e-12)
        assert np.max(np.abs(result.delta_maxwell_f_per_m)) < 5e-23
        assert result.diagnostics.charge_energy_relative < 2e-8
        assert result.diagnostics.reciprocity_relative < 2e-8
        assert result.diagnostics.row_sum_relative < 2e-8
        assert result.diagnostics.min_eigenvalue_f_per_m >= -1e-20


def test_open_plane_edge_has_positive_target_self_fringe_increment() -> None:
    result = solve_edge_cell(_problem(p_x0=-100e-6, p_x1=0.0))
    p = _index(result, "P")
    assert result.delta_maxwell_f_per_m[p, p] > 0.0
    assert np.max(np.abs(result.delta_maxwell_f_per_m - result.delta_maxwell_f_per_m.T)) < 2e-20


def test_dielectric_and_lateral_geometry_scaling() -> None:
    low = solve_edge_cell(_problem(p_x0=-100e-6, p_x1=0.0, dk=2.0))
    high = solve_edge_cell(_problem(p_x0=-100e-6, p_x1=0.0, dk=4.0))
    p = _index(low, "P")
    assert high.delta_maxwell_f_per_m[p, p] == pytest.approx(2.0 * low.delta_maxwell_f_per_m[p, p], rel=5e-11)
    narrower = solve_edge_cell(_problem(p_x0=-100e-6, p_x1=-50e-6, dk=4.0))
    assert narrower.vertical_baseline_f_per_m[p, p] < high.vertical_baseline_f_per_m[p, p]


def test_terminal_relabel_is_a_permutation_not_a_physics_change() -> None:
    original = _problem(p_x0=-100e-6, p_x1=0.0)
    changed = replace(original, conductors=tuple(replace(c, terminal={"P": "X", "G": "R"}[c.terminal]) for c in original.conductors), reference_terminal="R")
    left, right = solve_edge_cell(original), solve_edge_cell(changed)
    np.testing.assert_allclose(left.raw_maxwell_f_per_m, right.raw_maxwell_f_per_m, rtol=1e-12, atol=1e-23)
    np.testing.assert_allclose(left.delta_maxwell_f_per_m, right.delta_maxwell_f_per_m, rtol=1e-12, atol=1e-23)


def test_mesh_crop_convergence_api_and_resource_fail_closed() -> None:
    # Keep P away from the artificial boundary so the R->2R extension is
    # physically defined; use a small mesh for the unit-test budget.
    p = _problem(p_x0=-50e-6, p_x1=0.0, h=10e-6, max_cells=20_000)
    convergence = convergence_check(p)
    assert convergence.mesh_relative < 0.12
    assert convergence.crop_relative < 0.08
    with pytest.raises(EdgeCellCapacitanceError, match="resource gate"):
        solve_edge_cell(replace(p, cell_size_m=2e-6, max_cells=100))
    with pytest.raises(EdgeCellCapacitanceError, match="runtime gate"):
        solve_edge_cell(replace(p, max_runtime_s=1e-12))
    with pytest.raises(EdgeCellCapacitanceError, match="top and bottom"):
        solve_edge_cell(replace(p, conductors=tuple(c for c in p.conductors if not (c.terminal == "G" and c.z_min_m == 0.0))))


def test_cache_result_arrays_cannot_be_poisoned_by_setflags_mutation() -> None:
    problem = _problem(p_x0=-100e-6, p_x1=0.0)
    first = solve_edge_cell(problem)
    expected = np.array(first.raw_maxwell_f_per_m, copy=True)
    first.raw_maxwell_f_per_m.setflags(write=True)
    first.raw_maxwell_f_per_m[:] = 123.0
    second = solve_edge_cell(problem)
    np.testing.assert_array_equal(second.raw_maxwell_f_per_m, expected)
    assert not second.raw_maxwell_f_per_m.flags.writeable
    assert not np.shares_memory(first.raw_maxwell_f_per_m, second.raw_maxwell_f_per_m)
