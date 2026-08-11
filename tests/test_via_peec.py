"""Focused physics and constraint tests for experimental filled-via PEEC."""

from __future__ import annotations

from math import pi

import numpy as np
import pytest
from scipy.integrate import dblquad

from spd_decap_pi._core.solver.via_peec import (
    MU_0_H_PER_M,
    FilledMicroviaSegment,
    ViaPeecError,
    ViaPeecOperator,
    ViaPeecOwnership,
    compile_via_peec,
    parallel_finite_wire_mutual_inductance,
    solid_cylinder_internal_impedance,
    solve_via_peec,
    straight_wire_external_self_inductance,
)


SIGMA = 5.959e7


def _via(
    segment_id: str,
    x_um: float,
    y_um: float,
    *,
    group: str = "P",
    terminal: str = "P",
    sign: int = 1,
    z0_um: float = 0.0,
    z1_um: float = 80.0,
    radius_um: float = 12.5,
) -> FilledMicroviaSegment:
    return FilledMicroviaSegment(
        segment_id,
        x_um * 1.0e-6,
        y_um * 1.0e-6,
        z0_um * 1.0e-6,
        z1_um * 1.0e-6,
        radius_um * 1.0e-6,
        SIGMA,
        group,
        terminal,
        sign,
    )


def test_solid_cylinder_dc_and_internal_inductance_limits() -> None:
    length = 75.0e-6
    radius = 15.0e-6
    dc = solid_cylinder_internal_impedance(
        0.0, length_m=length, radius_m=radius, conductivity_s_per_m=SIGMA
    )
    expected_resistance = length / (SIGMA * pi * radius**2)
    assert dc == pytest.approx(expected_resistance, rel=1.0e-14)
    near_dc = solid_cylinder_internal_impedance(
        1.0, length_m=length, radius_m=radius, conductivity_s_per_m=SIGMA
    )
    expected_internal_l = MU_0_H_PER_M * length / (8.0 * pi)
    assert near_dc.real == pytest.approx(expected_resistance, rel=1.0e-10)
    assert near_dc.imag / (2.0 * pi) == pytest.approx(expected_internal_l, rel=2.0e-7)


def test_solid_cylinder_skin_effect_increases_loss_and_reduces_internal_l() -> None:
    values = solid_cylinder_internal_impedance(
        np.asarray((1.0e3, 1.0e9, 1.0e12)),
        length_m=100.0e-6,
        radius_m=25.0e-6,
        conductivity_s_per_m=SIGMA,
    )
    assert values[2].real > values[1].real > values[0].real
    internal_l = values.imag / (2.0 * pi * np.asarray((1.0e3, 1.0e9, 1.0e12)))
    assert internal_l[2] < internal_l[1] < internal_l[0]


def test_single_branch_combined_low_and_high_frequency_total_l_limits() -> None:
    via = _via("single", 0.0, 0.0, z1_um=100.0, radius_um=10.0)
    operator = compile_via_peec((via,))
    # Independent fixed anchor for the finite physical-radius expression at
    # l=100 um and a=10 um.  Do not derive the expected value from the operator.
    external_l = 4.186470776371762e-11
    assert straight_wire_external_self_inductance(100.0e-6, 10.0e-6) == pytest.approx(
        external_l, rel=2.0e-14
    )
    assert operator.external_partial_inductance_h[0, 0] == pytest.approx(
        external_l, rel=2.0e-14
    )
    dc_internal_l = MU_0_H_PER_M * via.length_m / (8.0 * pi)
    low_frequency = 1.0
    high_frequency = 1.0e15
    low_z = solve_via_peec(operator, low_frequency).impedance_ohm[0, 0]
    high_z = solve_via_peec(operator, high_frequency).impedance_ohm[0, 0]
    low_total_l = low_z.imag / (2.0 * pi * low_frequency)
    high_total_l = high_z.imag / (2.0 * pi * high_frequency)
    assert low_total_l == pytest.approx(external_l + dc_internal_l, rel=3.0e-8)
    assert high_total_l > external_l
    assert high_total_l == pytest.approx(external_l, rel=6.0e-5)


def test_finite_parallel_neumann_loop_and_separation_limit() -> None:
    first = _via("P", 0.0, 0.0, group="P", terminal="P")
    near = _via("G-near", 60.0, 0.0, group="G", terminal="G", sign=-1)
    far = _via("G-far", 600.0, 0.0, group="G", terminal="G", sign=-1)
    operator = compile_via_peec((first, near))
    inductance = operator.external_partial_inductance_h
    loop_l = inductance[0, 0] + inductance[1, 1] - 2.0 * inductance[0, 1]
    assert loop_l > 0.0
    assert loop_l == pytest.approx(np.asarray((1.0, -1.0)) @ inductance @ np.asarray((1.0, -1.0)))
    assert parallel_finite_wire_mutual_inductance(first, far) < parallel_finite_wire_mutual_inductance(first, near)
    assert straight_wire_external_self_inductance(first.length_m, first.radius_m) == pytest.approx(inductance[0, 0])


def test_unequal_z_mutual_matches_fixed_value_and_independent_quadrature() -> None:
    first = _via("A", 0.0, 0.0, group="A", terminal="A", z0_um=0.0, z1_um=80.0, radius_um=10.0)
    second = _via("B", 75.0, 0.0, group="B", terminal="B", z0_um=20.0, z1_um=140.0, radius_um=10.0)
    actual = parallel_finite_wire_mutual_inductance(first, second)
    assert actual == pytest.approx(1.0705624645081326e-11, rel=2.0e-14)
    separation = 75.0e-6
    integral = dblquad(
        lambda z_second, z_first: 1.0
        / np.sqrt(separation**2 + (z_first - z_second) ** 2),
        first.z0_m,
        first.z1_m,
        lambda _: second.z0_m,
        lambda _: second.z1_m,
        epsabs=1.0e-12,
        epsrel=1.0e-12,
    )[0]
    quadrature = MU_0_H_PER_M / (4.0 * pi) * integral
    assert actual == pytest.approx(quadrature, rel=2.0e-13)


def test_identical_parallel_vias_share_exactly_and_are_permutation_invariant() -> None:
    vias = (
        _via("A", -50.0, 0.0),
        _via("B", 50.0, 0.0),
        _via("C", 0.0, 86.6025404),
    )
    direct = solve_via_peec(compile_via_peec(vias), 100.0e6)
    permuted = solve_via_peec(compile_via_peec(tuple(reversed(vias))), 100.0e6)
    np.testing.assert_allclose(direct.branch_currents_per_group_amp[:, 0], np.full(3, 1.0 / 3.0), rtol=1e-10, atol=1e-11)
    np.testing.assert_allclose(direct.impedance_ohm, permuted.impedance_ohm, rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(
        direct.diagnostics.kcl_relative_residual,
        0.0,
        atol=1.0e-12,
    )


def test_unequal_power_ground_array_keeps_return_sign_in_constraint_not_partial_l() -> None:
    vias = (
        _via("P0", -70.0, 0.0, group="P", terminal="P"),
        _via("P1", 0.0, 0.0, group="P", terminal="P"),
        _via("P2", 70.0, 0.0, group="P", terminal="P"),
        _via("G0", -35.0, 100.0, group="G", terminal="G", sign=-1),
        _via("G1", 35.0, 100.0, group="G", terminal="G", sign=-1),
    )
    operator = compile_via_peec(vias)
    assert np.min(np.linalg.eigvalsh(operator.external_partial_inductance_h)) >= -1.0e-20
    result = solve_via_peec(operator, 50.0e6)
    # One amp in each named group yields +z P and -z G branch currents.
    currents = result.branch_currents_per_group_amp @ np.asarray((1.0, 1.0))
    assert np.all(currents[:3].real > 0.0)
    assert np.all(currents[3:].real < 0.0)
    assert (np.asarray((1.0, 1.0)) @ result.impedance_ohm @ np.asarray((1.0, 1.0))).imag > 0.0


def test_dense_constraint_reference_reciprocity_and_passivity() -> None:
    vias = (
        _via("P0", 0.0, 0.0, group="P", terminal="P"),
        _via("P1", 60.0, 0.0, group="P", terminal="P"),
        _via("G0", 0.0, 80.0, group="G", terminal="G", sign=-1),
    )
    operator = compile_via_peec(vias)
    frequency = 2.0e9
    result = solve_via_peec(operator, frequency)
    internal = np.diag(
        [
            solid_cylinder_internal_impedance(
                frequency,
                length_m=item.length_m,
                radius_m=item.radius_m,
                conductivity_s_per_m=item.conductivity_s_per_m,
            )
            for item in vias
        ]
    )
    branch = internal + 1j * 2.0 * pi * frequency * operator.external_partial_inductance_h
    reference = np.linalg.inv(operator.group_current_constraint.T @ np.linalg.inv(branch) @ operator.group_current_constraint)
    np.testing.assert_allclose(result.impedance_ohm, reference, rtol=1.0e-11, atol=1.0e-14)
    np.testing.assert_allclose(result.impedance_ohm, result.impedance_ohm.T, rtol=0.0, atol=1.0e-14)
    assert result.diagnostics.min_hermitian_impedance_eigenvalue_ohm >= -result.diagnostics.passivity_tolerance_ohm
    assert result.diagnostics.raw_terminal_admittance_reciprocity_relative_error < 1.0e-12
    assert result.diagnostics.raw_terminal_impedance_reciprocity_relative_error < 1.0e-12
    assert result.diagnostics.branch_solve_backward_error < 1.0e-12
    assert result.diagnostics.terminal_solve_backward_error < 1.0e-12
    assert result.diagnostics.equipotential_voltage_relative_residual < 1.0e-12


def test_peec_ownership_is_explicit_and_manual_constraint_cannot_change_current_direction() -> None:
    via = _via("P", 0.0, 0.0)
    isolated = compile_via_peec((via,))
    assert isolated.ownership.role == "isolated_reference"
    assert isolated.ownership.global_mna_composable is False
    with pytest.raises(ViaPeecError, match="diagnostic-only"):
        isolated.require_global_mna_composable()
    intent = compile_via_peec(
        (via,),
        ownership=ViaPeecOwnership(
            "subtraction_intent_diagnostic",
            "plate-local-via:P",
            "SPD via and core ledger",
        ),
    )
    assert intent.ownership.core_local_block_id == "plate-local-via:P"
    assert intent.ownership.global_mna_composable is False
    with pytest.raises(ViaPeecError, match="actual exact/core data"):
        intent.require_global_mna_composable()
    # The historical role string is not accepted even when a caller supplies
    # a plausible core ID; a string is not subtraction data.
    with pytest.raises(ViaPeecError, match="without actual subtraction data"):
        ViaPeecOwnership(  # type: ignore[arg-type]
            "exact_minus_core", "plate-local-via:P", "forged role claim"
        )
    with pytest.raises(ViaPeecError, match="stable core"):
        ViaPeecOwnership("subtraction_intent_diagnostic")
    with pytest.raises(ViaPeecError, match="constraint"):
        ViaPeecOperator(
            (via,), ("P",), ("P",), np.asarray(((-1.0,),)),
            isolated.external_partial_inductance_h,
            isolated.partial_inductance_min_eigenvalue_h,
            isolated.partial_inductance_max_eigenvalue_h,
        )


@pytest.mark.parametrize(
    "segments",
    [
        (_via("A", 0.0, 0.0), _via("B", 0.0, 0.0, group="G", terminal="G")),
        (_via("A", 0.0, 0.0), _via("B", 20.0, 0.0, group="G", terminal="G")),
    ],
)
def test_coincident_or_overlapping_vias_fail_closed(segments: tuple[FilledMicroviaSegment, ...]) -> None:
    with pytest.raises(ViaPeecError, match="overlap or coincide"):
        compile_via_peec(segments)


def test_invalid_segment_orientation_fails_closed() -> None:
    with pytest.raises(ViaPeecError, match="z1_m"):
        FilledMicroviaSegment("bad", 0.0, 0.0, 1.0, 0.0, 1.0e-6, SIGMA, "P", "P")


def test_stacked_or_mismatched_spans_within_group_fail_as_unsupported_topology() -> None:
    stacked = (
        _via("lower", 0.0, 0.0, z0_um=0.0, z1_um=40.0),
        _via("upper", 0.0, 0.0, z0_um=40.0, z1_um=80.0),
    )
    with pytest.raises(ViaPeecError, match="unsupported stacked/series"):
        compile_via_peec(stacked)
    mismatched = (
        _via("short", 0.0, 0.0, z0_um=0.0, z1_um=80.0),
        _via("long", 100.0, 0.0, z0_um=0.0, z1_um=81.0),
    )
    with pytest.raises(ViaPeecError, match="unsupported stacked/series"):
        compile_via_peec(mismatched)


def test_ill_conditioned_and_nonfinite_problems_fail_closed() -> None:
    ordinary = _via("ordinary", 0.0, 0.0)
    extreme = FilledMicroviaSegment(
        "extreme",
        100.0e-6,
        0.0,
        ordinary.z0_m,
        ordinary.z1_m,
        ordinary.radius_m,
        SIGMA * 1.0e13,
        "P",
        "P",
    )
    with pytest.raises(ViaPeecError, match="ill-conditioned"):
        solve_via_peec(compile_via_peec((ordinary, extreme)), 0.0)
    operator = compile_via_peec((ordinary,))
    with pytest.raises(ViaPeecError, match="frequency_hz"):
        solve_via_peec(operator, float("nan"))
    with pytest.raises(ViaPeecError, match="x_m must be finite"):
        FilledMicroviaSegment("nonfinite", float("inf"), 0.0, 0.0, 1.0e-6, 0.1e-6, SIGMA, "P", "P")


@pytest.mark.parametrize(
    ("partial_l", "minimum", "maximum", "message"),
    [
        (
            np.asarray(((2.0e-11, 0.5e-11), (0.6e-11, 2.0e-11))),
            1.45e-11,
            2.55e-11,
            "exactly symmetric",
        ),
        (
            np.asarray(((2.0e-11, np.inf), (np.inf, 2.0e-11))),
            0.0,
            0.0,
            "must be finite",
        ),
        (
            np.asarray(((1.0e-11, 2.0e-11), (2.0e-11, 1.0e-11))),
            -1.0e-11,
            3.0e-11,
            "positive semidefinite",
        ),
    ],
)
def test_manual_operator_rejects_invalid_partial_inductance_before_solve(
    partial_l: np.ndarray,
    minimum: float,
    maximum: float,
    message: str,
) -> None:
    segments = (
        _via("A", 0.0, 0.0, group="A", terminal="A"),
        _via("B", 80.0, 0.0, group="B", terminal="B"),
    )
    with pytest.raises(ViaPeecError, match=message):
        ViaPeecOperator(
            segments,
            ("A", "B"),
            ("A", "B"),
            np.eye(2),
            partial_l,
            minimum,
            maximum,
        )
