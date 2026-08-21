from __future__ import annotations

from math import log, pi

import numpy as np
import pytest
from shapely.geometry import Point, box

from spd_decap_pi.fft_bem_capacitance import (
    EPSILON_0_F_PER_M,
    FFTPulseBEM,
    SurfaceConductor,
    UniformXYGrid,
    rasterize_shapely_surfaces,
    rectangular_cell_green_integral,
)


def _two_plate(*, pitch: float, side_cells: int, gap: float, dk: float = 1.0) -> FFTPulseBEM:
    shape = (side_cells + 4, side_cells + 4)
    mask = np.zeros(shape, dtype=bool)
    mask[2:-2, 2:-2] = True
    return FFTPulseBEM(
        UniformXYGrid(0.0, 0.0, pitch, shape),
        (SurfaceConductor("top", "P", gap, mask), SurfaceConductor("bottom", "DGND", 0.0, mask)),
        dk,
        max_unknowns=10_000,
    )


def test_exact_square_self_green_term_and_off_axis_symmetry() -> None:
    pitch = 2.0e-6
    expected = 4.0 * pitch * np.arcsinh(1.0)
    assert rectangular_cell_green_integral(0.0, 0.0, 0.0, pitch) == pytest.approx(expected)
    assert rectangular_cell_green_integral(3 * pitch, -2 * pitch, 0.7 * pitch, pitch) == pytest.approx(
        rectangular_cell_green_integral(-3 * pitch, 2 * pitch, -0.7 * pitch, pitch)
    )


def test_aligned_rectangle_reaches_parallel_plate_limit_and_passivity_gates() -> None:
    pitch, cells, gap, dk = 1.0e-6, 20, 0.05e-6, 3.3
    result = _two_plate(pitch=pitch, side_cells=cells, gap=gap, dk=dk).maxwell_capacitance()
    assert result.diagnostics.status == "computed", result.diagnostics.reason
    parallel_plate = EPSILON_0_F_PER_M * dk * (cells * pitch) ** 2 / gap
    # At d / side = 1/400, the pulse BEM includes a positive fringe term but
    # must retain the leading exact parallel-plate limit.
    assert result.maxwell_capacitance_f[0, 0] == pytest.approx(parallel_plate, rel=0.04)
    assert result.diagnostics.reciprocity_error is not None
    assert result.diagnostics.min_energy_eigenvalue_f is not None
    assert result.diagnostics.min_energy_eigenvalue_f >= 0.0
    assert max(result.diagnostics.relative_residuals) < 2.0e-8


def test_love_small_gap_asymptotic_for_oppositely_driven_disks() -> None:
    # Kirchhoff/Love small-gap form for two equal circular disks in a uniform
    # dielectric: eps*pi*a^2/d + eps*a*(ln(16*pi*a/d)-1) + o(a).
    pitch_um, radius_um, gap_um, dk = 0.25, 5.0, 0.30, 3.3
    count = 48
    grid = UniformXYGrid(-6e-6, -6e-6, pitch_um * 1e-6, (count, count))
    x = -6.0 + (np.arange(count) + 0.5) * pitch_um
    xx, yy = np.meshgrid(x, x)
    mask = xx * xx + yy * yy <= radius_um * radius_um
    result = FFTPulseBEM(
        grid,
        (SurfaceConductor("upper disk", "P", gap_um * 1e-6, mask), SurfaceConductor("lower disk", "DGND", 0.0, mask)),
        dk,
        max_unknowns=5_000,
    ).maxwell_capacitance()
    assert result.diagnostics.status == "computed", result.diagnostics.reason
    # Q/V for Vp=+1/2, Vg=-1/2; this removes the common-mode capacitance to infinity.
    computed = (result.maxwell_capacitance_f[0, 0] - result.maxwell_capacitance_f[0, 1]) * 0.5
    eps = EPSILON_0_F_PER_M * dk
    radius, gap = radius_um * 1e-6, gap_um * 1e-6
    love = eps * pi * radius * radius / gap + eps * radius * (log(16.0 * pi * radius / gap) - 1.0)
    assert computed == pytest.approx(love, rel=0.04)


def test_hole_is_preserved_and_floating_charge_constraint_closes() -> None:
    # Centre-cell inclusion must retain the aperture: no bbox replacement.
    outer = box(0, 0, 10, 10).difference(Point(5, 5).buffer(1.2))
    grid, surfaces = rasterize_shapely_surfaces(
        (("perforated", "P", 1e-6, outer), ("return", "DGND", 0.0, box(0, 0, 10, 10))), pitch_um=1.0
    )
    assert not surfaces[0].mask[6, 6]
    # Embed the small floating mask into the original grid.
    full_float = np.zeros(grid.shape, dtype=bool); full_float[3:8, 3:8] = True
    result = FFTPulseBEM(grid, (surfaces[0], surfaces[1], SurfaceConductor("floating", "F", 0.5e-6, full_float)), 3.3, max_unknowns=5_000).maxwell_capacitance(floating_nets=("F",))
    assert result.diagnostics.status == "computed", result.diagnostics.reason
    assert result.net_names == ("P", "DGND")
    assert result.diagnostics.charge_conservation_error_f is not None
    assert result.diagnostics.charge_conservation_error_f < 1e-18


def test_resource_gate_is_fail_closed_and_deterministic() -> None:
    mask = np.ones((8, 8), dtype=bool)
    model = FFTPulseBEM(
        UniformXYGrid(0, 0, 1e-6, (8, 8)),
        (SurfaceConductor("a", "A", 1e-6, mask), SurfaceConductor("b", "B", 0, mask)),
        1.0,
        max_unknowns=10,
    )
    first = model.maxwell_capacitance()
    second = model.maxwell_capacitance()
    assert first.diagnostics.status == second.diagnostics.status == "blocked_fail_closed"
    assert first.diagnostics.reason == second.diagnostics.reason
