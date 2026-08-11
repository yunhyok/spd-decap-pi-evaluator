from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import box

from spd_decap_pi._core.models.impedance import SeriesRLModel
from spd_decap_pi._core.solver.modal import (
    DeviceBranch,
    DeviceConnection,
    DielectricDispersion,
    FinitePort,
    ModalSolverError,
    RectangularCavitySolver,
    RectangularPlane,
    ShuntGroup,
)
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
    CapacitanceArtwork,
    DielectricGap,
    MultilayerCapacitanceModel,
    extract_multilayer_bulk_capacitance,
)
from spd_decap_pi._core.solver.uniform_c00 import (
    DispersiveAdjacentGap,
    UniformC00Assembly,
    UniformC00Error,
    UniformLoadBlock,
    UniformPortConnectivityEvidence,
    assemble_adjacent_bulk_admittance,
    assemble_uniform_effective_admittance,
    prepare_external_uniform_input_from_assembly,
    replace_modal_c00_admittance,
    replace_modal_c00_from_assembly,
    replace_prepared_external_uniform_input_from_assembly,
)


def _c(*branches: tuple[int, int, float], size: int = 3) -> np.ndarray:
    value = np.zeros((size, size), dtype=float)
    for left, right, cap in branches:
        value[left, left] += cap
        value[right, right] += cap
        value[left, right] -= cap
        value[right, left] -= cap
    return value


def _partial(matrix: np.ndarray, nominal: float = 4.0) -> AdjacentGapMaxwellPartial:
    return AdjacentGapMaxwellPartial("L1", "L2", nominal, ("DGND", "A", "B"), matrix)


def _disp(dk: float = 4.0, df: float = 0.0) -> DielectricDispersion:
    return DielectricDispersion((1.0e3, 1.0e9), (dk, dk), (df, df))


def _tie(ok: bool = True) -> UniformPortConnectivityEvidence:
    return UniformPortConnectivityEvidence(
        "DGND", "A", ok, ok, "independent source/reference landing certificates"
    )


def test_gap_partials_reconstruct_raw_exact_artwork_matrix_and_are_immutable() -> None:
    gap = DielectricGap(100e-6, 4.0)
    result = extract_multilayer_bulk_capacitance(
        MultilayerCapacitanceModel(
            ("L0", "L1", "L2"), (gap, gap),
            (
                CapacitanceArtwork("L0", "DGND", box(0, 0, 1000, 1000)),
                CapacitanceArtwork("L1", "A", box(0, 0, 1000, 1000)),
                CapacitanceArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
            ),
        )
    )
    rebuilt = sum((part.maxwell_capacitance_f for part in result.adjacent_gap_partials), start=np.zeros_like(result.maxwell_capacitance_f))
    np.testing.assert_allclose(rebuilt, result.maxwell_capacitance_f)
    with pytest.raises(ValueError):
        result.adjacent_gap_partials[0].maxwell_capacitance_f[0, 0] = 0.0


def test_two_material_dk_df_scale_analytically_and_loss_is_passive() -> None:
    one = _c((0, 1, 2.0), size=2)
    p1 = AdjacentGapMaxwellPartial("L0", "L1", 2.0, ("DGND", "A"), one)
    p2 = AdjacentGapMaxwellPartial("L1", "L2", 4.0, ("DGND", "A"), 3.0 * one)
    f = 1.0e6
    result = assemble_adjacent_bulk_admittance(
        (DispersiveAdjacentGap(p1, _disp(4.0, 0.1)), DispersiveAdjacentGap(p2, _disp(2.0, 0.2))), (f,)
    )
    expected = 1j * 2 * np.pi * f * ((4 * (1 - 1j * 0.1) / 2) * one + (2 * (1 - 1j * 0.2) / 4) * 3 * one)
    np.testing.assert_allclose(result[0], expected)
    assert np.linalg.eigvalsh(result[0].real).min() >= -1e-10


def test_loaded_frequency_schur_matches_direct_mna_and_never_uses_c_only_schur() -> None:
    # DGND-A=2 F, A-B=3 F, B-DGND=5 F. A 7 S source-proven B load.
    partials = (DispersiveAdjacentGap(_partial(_c((0, 1, 2.0), (1, 2, 3.0), (0, 2, 5.0))), _disp()),)
    load = UniformLoadBlock(("B",), lambda _f: np.asarray(((7.0 + 0j,),)), "source decap network")
    frequency = 2.0
    output = assemble_uniform_effective_admittance(partials, (frequency,), reference_net="DGND", selected_nets=("A",), port_connectivity=_tie(), loads=(load,))
    raw = assemble_adjacent_bulk_admittance(partials, (frequency,))[0][1:, 1:]
    direct = raw[0, 0] - raw[0, 1] ** 2 / (raw[1, 1] + 7.0)
    assert output.status == "ok"
    np.testing.assert_allclose(output.effective_admittance_s[0, 0, 0], direct)


def test_floating_frequency_schur_and_disconnected_reference_block() -> None:
    partials = (DispersiveAdjacentGap(_partial(_c((0, 1, 2.0), (1, 2, 3.0), (0, 2, 5.0))), _disp()),)
    output = assemble_uniform_effective_admittance(partials, (2.0,), reference_net="DGND", selected_nets=("A",), port_connectivity=_tie())
    raw = assemble_adjacent_bulk_admittance(partials, (2.0,))[0][1:, 1:]
    np.testing.assert_allclose(output.effective_admittance_s[0, 0, 0], raw[0, 0] - raw[0, 1] ** 2 / raw[1, 1])
    blocked = assemble_uniform_effective_admittance(partials, (2.0,), reference_net="DGND", selected_nets=("A",), port_connectivity=_tie(False))
    assert blocked.status == "blocked_fail_closed"
    wrong_source = assemble_uniform_effective_admittance(
        partials, (2.0,), reference_net="DGND", selected_nets=("A",),
        port_connectivity=UniformPortConnectivityEvidence(
            "DGND", "B", True, True, "unrelated source components"
        ),
    )
    assert wrong_source.status == "blocked_fail_closed"
    with pytest.raises(UniformC00Error, match="reference net"):
        assemble_uniform_effective_admittance(partials, (2.0,), reference_net="NOPE", selected_nets=("A",), port_connectivity=_tie())


def test_modal_c00_is_replaced_not_added_and_bad_projection_or_multirail_fails() -> None:
    old = np.asarray(((1 + 2j, 5 + 6j), (2 + 3j, 7 + 8j)), dtype=complex)
    replacement = np.asarray((9 + 10j, 11 + 12j))
    new = replace_modal_c00_admittance(old, modes=((0, 0), (1, 0)), modal_uniform_projection=(1.0, 0.0), legacy_c00_admittance=old[:, 0], replacement_uniform_admittance=replacement)
    np.testing.assert_allclose(new[:, 0], replacement)
    np.testing.assert_allclose(new[:, 1], old[:, 1])
    full_old = np.asarray((((1 + 2j, 3 + 4j), (3 + 4j, 5 + 6j)),), dtype=complex)
    full_new = replace_modal_c00_admittance(full_old, modes=((0, 0), (1, 0)), modal_uniform_projection=(1.0, 0.0), legacy_c00_admittance=(1 + 2j,), replacement_uniform_admittance=(9 + 10j,))
    np.testing.assert_allclose(full_new[0], ((9 + 10j, 3 + 4j), (3 + 4j, 5 + 6j)))
    with pytest.raises(UniformC00Error, match="uniform projection"):
        replace_modal_c00_admittance(old, modes=((0, 0), (1, 0)), modal_uniform_projection=(1.0, 0.1), legacy_c00_admittance=old[:, 0], replacement_uniform_admittance=replacement)
    with pytest.raises(UniformC00Error, match="double-count"):
        replace_modal_c00_admittance(old, modes=((0, 0), (1, 0)), modal_uniform_projection=(1.0, 0.0), legacy_c00_admittance=np.zeros(2), replacement_uniform_admittance=replacement)
    with pytest.raises(UniformC00Error, match="non-passive"):
        replace_modal_c00_admittance(
            old, modes=((0, 0), (1, 0)), modal_uniform_projection=(1.0, 0.0),
            legacy_c00_admittance=old[:, 0], replacement_uniform_admittance=(-1.0 + 0j, -1.0 + 0j),
        )
    partials = (DispersiveAdjacentGap(_partial(_c((0, 1, 1.0), (0, 2, 1.0))), _disp()),)
    multi = assemble_uniform_effective_admittance(partials, (1e6, 2e6), reference_net="DGND", selected_nets=("A", "B"), port_connectivity=_tie())
    with pytest.raises(UniformC00Error, match="multi-rail"):
        replace_modal_c00_from_assembly(old, modes=((0, 0), (1, 0)), modal_uniform_projection=(1.0, 0.0), legacy_c00_admittance=old[:, 0], assembly=multi)


def test_fail_closed_assembly_never_leaks_invalid_frequency_or_usable_data() -> None:
    partials = (DispersiveAdjacentGap(_partial(_c((0, 1, 1.0))), _disp()),)
    with pytest.raises(UniformC00Error, match="frequencies"):
        assemble_uniform_effective_admittance(
            partials, (0.0,), reference_net="DGND", selected_nets=("A",), port_connectivity=_tie(False),
        )


def test_terminal_complete_uniform_input_is_externalized_without_device_c00_double_stamp() -> None:
    frequencies = np.asarray((1.0e6, 10.0e6), dtype=float)
    solver = RectangularCavitySolver(
        RectangularPlane(1.0e-3, 1.0e-3, 50.0e-6, 4.0, loss_tangent=0.01),
        max_mode_x=0,
        max_mode_y=0,
    )
    device = DeviceConnection(
        (
            DeviceBranch(
                "B1",
                FinitePort(0.5e-3, 0.5e-3, 50.0e-6, 50.0e-6, "P1"),
                SeriesRLModel("legacy-device-loop", 0.025, 1.2e-9),
            ),
        )
    )
    prepared = solver.prepare_device(frequencies, device)
    external_y = np.asarray((2.0 + 0.25j, 3.0 + 0.5j), dtype=complex)
    assembly = UniformC00Assembly(
        status="ok",
        reason=None,
        selected_net_names=("VDD",),
        frequencies_hz=frequencies,
        effective_admittance_s=external_y[:, None, None],
        raw_admittance_s=external_y[:, None, None],
        reference_evidence=_tie(),
    )

    externalized = replace_prepared_external_uniform_input_from_assembly(
        solver, prepared, assembly
    )
    assert externalized.external_uniform_admittance_s is not None
    np.testing.assert_array_equal(
        externalized.external_uniform_admittance_s, external_y
    )
    for original, retained in zip(
        prepared.branch_data, externalized.branch_data, strict=True
    ):
        for left, right in zip(original, retained, strict=True):
            if isinstance(left, np.ndarray):
                np.testing.assert_array_equal(left, right)
            else:
                assert left == right

    result = solver.solve_prepared_device(externalized)
    np.testing.assert_allclose(result.impedance_ohm, 1.0 / external_y)
    with pytest.raises(UniformC00Error, match="already has"):
        replace_prepared_external_uniform_input_from_assembly(
            solver, externalized, assembly
        )


def test_terminal_complete_uniform_input_has_direct_prepare_without_modal_terms() -> None:
    frequencies = np.asarray((1.0e6, 10.0e6), dtype=float)
    solver = RectangularCavitySolver(
        RectangularPlane(1.0e-3, 1.0e-3, 50.0e-6, 4.0, loss_tangent=0.01),
        max_mode_x=4,
        max_mode_y=4,
    )
    external_y = np.asarray((2.0 + 0.25j, 3.0 + 0.5j), dtype=complex)
    assembly = UniformC00Assembly(
        status="ok",
        reason=None,
        selected_net_names=("VDD",),
        frequencies_hz=frequencies,
        effective_admittance_s=external_y[:, None, None],
        raw_admittance_s=external_y[:, None, None],
        reference_evidence=_tie(),
    )

    prepared = prepare_external_uniform_input_from_assembly(
        solver, frequencies, assembly
    )

    assert prepared.branch_data == ()
    assert prepared.legacy_uniform_c00_term is None
    np.testing.assert_array_equal(
        prepared.plane_admittance,
        np.zeros((frequencies.size, solver.mode_count), dtype=complex),
    )
    np.testing.assert_array_equal(prepared.external_uniform_admittance_s, external_y)
    result = solver.solve_prepared_device(prepared)
    np.testing.assert_allclose(result.impedance_ohm, 1.0 / external_y)
    np.testing.assert_array_equal(result.diagnostics.condition_numbers, (1.0, 1.0))
    np.testing.assert_array_equal(result.diagnostics.relative_residuals, (0.0, 0.0))

    mismatched = np.asarray((1.0e6, 11.0e6), dtype=float)
    with pytest.raises(UniformC00Error, match="frequency grid"):
        prepare_external_uniform_input_from_assembly(solver, mismatched, assembly)


def test_terminal_complete_external_input_rejects_local_shunt_double_stamp() -> None:
    """The global terminal manifest already owns every mounted decap."""

    frequencies = np.asarray((1.0e6,), dtype=float)
    solver = RectangularCavitySolver(
        RectangularPlane(1.0e-3, 1.0e-3, 50.0e-6, 4.0, loss_tangent=0.01),
        max_mode_x=0,
        max_mode_y=0,
    )
    device = DeviceConnection(
        (
            DeviceBranch(
                "B1",
                FinitePort(0.5e-3, 0.5e-3, 50.0e-6, 50.0e-6, "P1"),
                SeriesRLModel("legacy-device-loop", 0.025, 1.2e-9),
            ),
        )
    )
    selected_shunt = ShuntGroup(
        "selected-rail-decap",
        (FinitePort(0.25e-3, 0.25e-3, 50.0e-6, 50.0e-6, "C1"),),
        SeriesRLModel("selected-rail-series", 0.01, 0.4e-9),
    )
    prepared = solver.prepare_device(frequencies, device)
    external_y = np.asarray((2.0 + 0.25j,), dtype=complex)
    assembly = UniformC00Assembly(
        status="ok",
        reason=None,
        selected_net_names=("VDD",),
        frequencies_hz=frequencies,
        effective_admittance_s=external_y[:, None, None],
        raw_admittance_s=external_y[:, None, None],
        reference_evidence=_tie(),
    )

    externalized = replace_prepared_external_uniform_input_from_assembly(
        solver, prepared, assembly
    )
    with pytest.raises(ModalSolverError, match="double-stamp"):
        solver.solve_prepared_device(externalized, shunts=(selected_shunt,))


def test_terminal_complete_external_input_is_not_mixed_with_active_modal_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Higher legacy modes cannot be stamped as an arbitrary one-port delta."""

    frequencies = np.asarray((432_342_610.839,), dtype=float)
    solver = RectangularCavitySolver(
        RectangularPlane(30.0e-3, 25.0e-3, 100.0e-6, 4.1, loss_tangent=0.015),
        max_mode_x=8,
        max_mode_y=8,
    )
    device = DeviceConnection(
        (
            DeviceBranch(
                "B1",
                FinitePort(1.0e-3, 1.0e-3, 0.3e-3, 0.3e-3, "P1"),
                SeriesRLModel("legacy-device-loop", 0.02, 0.5e-9),
            ),
        )
    )
    prepared = solver.prepare_device(frequencies, device)
    external_y = np.asarray((20.0 + 0.0j,), dtype=complex)
    assembly = UniformC00Assembly(
        status="ok",
        reason=None,
        selected_net_names=("VDD",),
        frequencies_hz=frequencies,
        effective_admittance_s=external_y[:, None, None],
        raw_admittance_s=external_y[:, None, None],
        reference_evidence=_tie(),
    )
    externalized = replace_prepared_external_uniform_input_from_assembly(
        solver, prepared, assembly
    )

    def unexpected_legacy_factorization(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("terminal-complete solve touched the legacy modal matrix")

    monkeypatch.setattr(
        "spd_decap_pi._core.solver.modal._factorized_solve",
        unexpected_legacy_factorization,
    )

    result = solver.solve_prepared_device(externalized)

    np.testing.assert_allclose(result.impedance_ohm, 1.0 / external_y)
    assert np.all(result.impedance_ohm.real >= 0.0)
    np.testing.assert_array_equal(result.diagnostics.condition_numbers, (1.0,))
    np.testing.assert_array_equal(result.diagnostics.relative_residuals, (0.0,))
