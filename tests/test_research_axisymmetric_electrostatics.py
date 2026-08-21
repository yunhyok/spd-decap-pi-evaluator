from __future__ import annotations

from math import log, pi

import numpy as np
import pytest

from spd_decap_pi.research_axisymmetric_electrostatics import (
    EPSILON_0_F_PER_M,
    AxisymmetricConductor,
    AxisymmetricElectrostaticError,
    DetailCorrectionResult,
    DielectricLayer,
    build_axisymmetric_problem,
    detail_minus_baseline,
    map_correction_to_global,
    radial_coupling_correction,
    require_mesh_crop_convergence,
    solve_axisymmetric,
    solve_vertical_column_baseline,
)


def _coax(*, epsilon: float = 3.4, p_name: str = "P", g_name: str = "G"):
    return build_axisymmetric_problem(
        r_max_m=5e-6, z_min_m=-10e-6, z_max_m=10e-6, dr_m=.2e-6, dz_m=.2e-6,
        dielectrics=(DielectricLayer(-10e-6, 10e-6, epsilon),),
        conductors=(AxisymmetricConductor(p_name, 0, 1e-6, -7e-6, 7e-6), AxisymmetricConductor(g_name, 2e-6, 3e-6, -9e-6, 9e-6)),
    )


def test_coaxial_cylinder_and_passive_raw_maxwell_matrix() -> None:
    result = solve_axisymmetric(_coax())
    assert result.diagnostics.status == "computed", result.diagnostics.reason
    index = {name: position for position, name in enumerate(result.terminals)}
    analytic = 2 * pi * EPSILON_0_F_PER_M * 3.4 * 14e-6 / log(2.0)
    assert result.maxwell_capacitance_f[index["P"], index["P"]] == pytest.approx(analytic, rel=.20)
    assert result.diagnostics.min_eigenvalue_f is not None and result.diagnostics.min_eigenvalue_f >= 0.0
    assert result.diagnostics.row_sum_error_f is not None and result.diagnostics.row_sum_error_f < 1e-20


def test_parallel_disks_follow_love_small_gap_scale() -> None:
    problem = build_axisymmetric_problem(
        r_max_m=20e-6, z_min_m=-10e-6, z_max_m=10e-6, dr_m=.25e-6, dz_m=.25e-6,
        dielectrics=(DielectricLayer(-10e-6, 10e-6, 3.4),),
        conductors=(AxisymmetricConductor("P", 0, 5e-6, .3e-6, .55e-6), AxisymmetricConductor("G", 0, 5e-6, -.55e-6, -.3e-6)),
    )
    result = solve_axisymmetric(problem)
    index = {name: position for position, name in enumerate(result.terminals)}
    computed = (result.maxwell_capacitance_f[index["P"], index["P"]] - result.maxwell_capacitance_f[index["P"], index["G"]]) * .5
    eps, radius, gap = EPSILON_0_F_PER_M * 3.4, 5e-6, .6e-6
    love = eps*pi*radius*radius/gap + eps*radius*(log(16*pi*radius/gap)-1)
    assert computed == pytest.approx(love, rel=.20)


def test_homogeneous_scaling_and_conductor_relabel_symmetry() -> None:
    first, doubled = solve_axisymmetric(_coax(epsilon=2.0)), solve_axisymmetric(_coax(epsilon=4.0))
    np.testing.assert_allclose(doubled.maxwell_capacitance_f, 2.0 * first.maxwell_capacitance_f, rtol=2e-12)
    renamed = solve_axisymmetric(_coax(epsilon=2.0, p_name="X", g_name="Y"))
    old = {name: index for index, name in enumerate(first.terminals)}; new = {name: index for index, name in enumerate(renamed.terminals)}
    np.testing.assert_allclose(first.maxwell_capacitance_f[np.ix_([old["OUTER"], old["G"], old["P"]], [old["OUTER"], old["G"], old["P"]])], renamed.maxwell_capacitance_f[np.ix_([new["OUTER"], new["Y"], new["X"]], [new["OUTER"], new["Y"], new["X"]])])


def test_detail_minus_baseline_maps_to_global_and_convergence_fails_closed() -> None:
    common = dict(r_max_m=10e-6, z_min_m=-5e-6, z_max_m=5e-6, dr_m=.5e-6, dz_m=.5e-6, dielectrics=(DielectricLayer(-5e-6, 5e-6, 3.4),))
    baseline_problem = build_axisymmetric_problem(**common, conductors=(AxisymmetricConductor("P", 0, 2e-6, .5e-6, 1.5e-6), AxisymmetricConductor("G", 0, 8e-6, -1.5e-6, -.5e-6)))
    detail_problem = build_axisymmetric_problem(**common, conductors=(AxisymmetricConductor("P", 0, 2e-6, .5e-6, 1.5e-6), AxisymmetricConductor("G", 3e-6, 8e-6, -1.5e-6, -.5e-6)))
    correction = detail_minus_baseline(solve_axisymmetric(detail_problem), solve_axisymmetric(baseline_problem), detail_problem=detail_problem, baseline_problem=baseline_problem)
    nodes, mapped = map_correction_to_global(correction, {"OUTER": "GLOB_G", "P": "GLOB_P", "G": "GLOB_G"})
    assert nodes == ("GLOB_G", "GLOB_P") and mapped.shape == (2, 2)
    changed = DetailCorrectionResult(correction.terminals, correction.correction_maxwell_capacitance_f * 2, correction.interface_signature, correction.detail_signature, correction.baseline_signature)
    with pytest.raises(AxisymmetricElectrostaticError, match="mesh/crop convergence"):
        require_mesh_crop_convergence(coarse=changed, fine=correction, expanded=correction, relative_tolerance=.01)


def test_resource_gate_is_deterministic() -> None:
    result = solve_axisymmetric(_coax(), max_cells=10)
    assert result.diagnostics.status == "blocked_fail_closed"
    assert "resource gate" in str(result.diagnostics.reason)


def _four_layer_aperture(*, opening: bool, h: float = 5e-6, radius: float = 300e-6):
    z_min, z_max = -125e-6, 125e-6
    conductors = [
        AxisymmetricConductor("G", 0, radius, 75e-6, 95e-6),
        AxisymmetricConductor("P", 127e-6 if opening else 0, radius, 25e-6, 45e-6),
        AxisymmetricConductor("OTHER", 0, radius, -25e-6, -5e-6),
        AxisymmetricConductor("G", 0, radius, -75e-6, -55e-6),
    ]
    if opening:
        conductors.extend((
            AxisymmetricConductor("OTHER", 0, 30e-6, -25e-6, 45e-6),
            AxisymmetricConductor("OTHER", 0, 50e-6, 25e-6, 45e-6),
        ))
    return build_axisymmetric_problem(
        r_max_m=radius, z_min_m=z_min, z_max_m=z_max, dr_m=h, dz_m=h,
        dielectrics=(DielectricLayer(z_min, z_max, 3.4),),
        conductors=tuple(conductors), outer_terminal="G",
        boundary_semantics="open_r_explicit_convergence_top_bottom_outer_conductor",
    )


def test_same_geometry_vertical_columns_remove_only_radial_coupling() -> None:
    uniform = _four_layer_aperture(opening=False)
    full = solve_axisymmetric(uniform)
    vertical = solve_vertical_column_baseline(uniform)
    correction = radial_coupling_correction(full, vertical, problem=uniform)
    # Radially uniform sheets have no radial electric field; the numerical
    # correction is round-off, not a changed-geometry bulk subtraction.
    assert np.max(np.abs(correction.correction_maxwell_capacitance_f)) < 1e-24
    assert vertical.diagnostics.omitted_radial_columns == 0
    assert vertical.diagnostics.coupling_model == "vertical_columns_only"
    assert full.terminals == vertical.terminals
    assert np.max(np.abs(vertical.maxwell_capacitance_f.sum(axis=0))) < 1e-20
    assert solve_vertical_column_baseline(uniform).diagnostics.cache_signature == vertical.diagnostics.cache_signature
    assert solve_vertical_column_baseline(uniform, tolerance=1e-9).diagnostics.cache_signature != vertical.diagnostics.cache_signature


def test_circular_opening_and_foreign_filled_via_have_positive_target_fringe() -> None:
    problem = _four_layer_aperture(opening=True)
    full = solve_axisymmetric(problem)
    vertical = solve_vertical_column_baseline(problem)
    correction = radial_coupling_correction(full, vertical, problem=problem)
    index = {name: position for position, name in enumerate(correction.terminals)}
    assert correction.correction_maxwell_capacitance_f[index["P"], index["P"]] > 0.0
    assert full.diagnostics.row_sum_error_f is not None and full.diagnostics.row_sum_error_f < 1e-20
    assert full.diagnostics.min_eigenvalue_f is not None and full.diagnostics.min_eigenvalue_f >= -1e-24


def test_vertical_columns_omit_unconnected_annuli_and_fail_when_none_connect() -> None:
    z_min, z_max, h, radius = -50e-6, 50e-6, 5e-6, 100e-6
    local = build_axisymmetric_problem(
        r_max_m=radius, z_min_m=z_min, z_max_m=z_max, dr_m=h, dz_m=h,
        dielectrics=(DielectricLayer(z_min, z_max, 2.0),),
        conductors=(AxisymmetricConductor("P", 0, 20e-6, -5e-6, 5e-6),),
        boundary_semantics="open_r_explicit_convergence_top_bottom_outer_conductor",
    )
    result = solve_vertical_column_baseline(local)
    assert result.diagnostics.status == "computed"
    assert result.diagnostics.omitted_radial_columns == 16
    empty = build_axisymmetric_problem(
        r_max_m=radius, z_min_m=z_min, z_max_m=z_max, dr_m=h, dz_m=h,
        dielectrics=(DielectricLayer(z_min, z_max, 2.0),), conductors=(),
        boundary_semantics="open_r_explicit_convergence_top_bottom_outer_conductor",
    )
    blocked = solve_vertical_column_baseline(empty)
    assert blocked.diagnostics.status == "blocked_fail_closed"
    assert "no column connected" in str(blocked.diagnostics.reason)
