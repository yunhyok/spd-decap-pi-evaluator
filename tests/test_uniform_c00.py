from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from spd_decap_pi._core.solver.modal import DielectricDispersion
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
    CapacitanceArtwork,
    DielectricGap,
    MultilayerCapacitanceModel,
    extract_multilayer_bulk_capacitance,
)
from spd_decap_pi._core.solver.uniform_c00 import (
    DispersiveAdjacentGap,
    UniformC00Error,
    UniformLoadBlock,
    UniformPortConnectivityEvidence,
    assemble_adjacent_bulk_admittance,
    assemble_uniform_effective_admittance,
    replace_modal_c00_admittance,
    replace_modal_c00_from_assembly,
    replace_prepared_uniform_c00_from_assembly,
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


def test_prepared_replacement_requires_the_prepared_frequency_grid() -> None:
    partials = (DispersiveAdjacentGap(_partial(_c((0, 1, 2.0), (1, 2, 3.0), (0, 2, 5.0))), _disp()),)
    assembly = assemble_uniform_effective_admittance(
        partials, (1e6, 2e6), reference_net="DGND", selected_nets=("A",), port_connectivity=_tie()
    )
    replaced = object()

    class _Solver:
        def __init__(self) -> None:
            self.calls: list[np.ndarray] = []

        def replace_uniform_c00_term(self, prepared, replacement_admittance_s):
            self.calls.append(np.asarray(replacement_admittance_s))
            return replaced

    solver = _Solver()
    shifted = SimpleNamespace(frequencies_hz=np.asarray([1e6, 3e6], dtype=float))
    with pytest.raises(UniformC00Error, match="different frequency grid"):
        replace_prepared_uniform_c00_from_assembly(solver, shifted, assembly)
    assert solver.calls == []

    matched = SimpleNamespace(frequencies_hz=np.asarray([1e6, 2e6], dtype=float))
    assert replace_prepared_uniform_c00_from_assembly(solver, matched, assembly) is replaced
    assert len(solver.calls) == 1


def test_fail_closed_assembly_never_leaks_invalid_frequency_or_usable_data() -> None:
    partials = (DispersiveAdjacentGap(_partial(_c((0, 1, 1.0))), _disp()),)
    with pytest.raises(UniformC00Error, match="frequencies"):
        assemble_uniform_effective_admittance(
            partials, (0.0,), reference_net="DGND", selected_nets=("A",), port_connectivity=_tie(False),
        )
