"""Focused checks for the experimental absolute-node sparse MNA kernel."""

from __future__ import annotations

from math import pi

import numpy as np
import pytest
from scipy import sparse

from spd_decap_pi._core.solver.global_mna import (
    DifferentialPort,
    EquipotentialConstraint,
    FilledMicroviaBranch,
    FilledMicroviaDiagnostic,
    GlobalMnaError,
    NodalAdmittanceBlock,
    SeriesBranchBlock,
    compile_global_mna,
    filled_microvia_branch_block,
    inspect_filled_microvia_reference,
)
from spd_decap_pi._core.solver.via_peec import (
    FilledMicroviaSegment,
    compile_via_peec,
    solid_cylinder_internal_impedance,
    solve_via_peec,
    straight_wire_external_self_inductance,
)


SIGMA = 5.959e7


def _branch(
    branch_id: str,
    positive: str,
    negative: str,
    impedance: complex | callable,
) -> SeriesBranchBlock:
    return SeriesBranchBlock((branch_id,), (positive,), (negative,), lambda f: np.asarray(((impedance(f) if callable(impedance) else impedance,),), dtype=np.complex128), f"source-{branch_id}")


def test_analytic_series_rlc_open_port_impedance() -> None:
    resistance, inductance, capacitance, frequency = 0.021, 1.7e-9, 470e-9, 13.0e6
    expected = resistance + 1j * 2.0 * pi * frequency * inductance + 1.0 / (1j * 2.0 * pi * frequency * capacitance)
    operator = compile_global_mna(
        ("P", "G"),
        branch_blocks=(_branch("rlc", "P", "G", lambda f: resistance + 1j * 2.0 * pi * f * inductance + 1.0 / (1j * 2.0 * pi * f * capacitance)),),
        ports=(DifferentialPort("port", "P", "G"),),
    )
    result = operator.solve(frequency)
    assert result.impedance_ohm[0, 0] == pytest.approx(expected, rel=1e-11, abs=1e-14)
    assert result.diagnostics.backward_relative_residual < 1e-12
    assert result.diagnostics.nodal_kcl_relative_residual < 1e-12
    assert result.diagnostics.branch_equation_relative_residual < 1e-12
    assert result.diagnostics.equipotential_relative_residual < 1e-12
    assert result.diagnostics.gauge_relative_residual < 1e-12


def test_two_coupled_vias_solve_as_one_coupled_branch_block() -> None:
    z = np.asarray(((0.020 + 1j * 0.08, 1j * 0.025), (1j * 0.025, 0.032 + 1j * 0.11)), dtype=np.complex128)
    operator = compile_global_mna(
        ("P", "G"),
        branch_blocks=(SeriesBranchBlock(("v0", "v1"), ("P", "P"), ("G", "G"), z, "coupled-vias"),),
        ports=(DifferentialPort("port", "P", "G"),),
    )
    result = operator.solve(1.0e6)
    expected = 1.0 / (np.ones(2) @ np.linalg.solve(z, np.ones(2)))
    assert result.impedance_ohm[0, 0] == pytest.approx(expected, rel=1e-11, abs=1e-14)
    np.testing.assert_allclose(result.branch_currents_a_per_a[:, 0], np.linalg.solve(z, np.ones(2)) * expected, rtol=1e-10, atol=1e-12)


def test_stacked_microvia_reference_is_diagnostic_not_a_global_branch() -> None:
    branches = (
        FilledMicroviaBranch("lower", "P", "MID", 0.0, 0.0, 0.0, 35e-6, 8e-6, SIGMA),
        FilledMicroviaBranch("upper", "MID", "G", 0.0, 0.0, 35e-6, 80e-6, 8e-6, SIGMA),
    )
    reference = inspect_filled_microvia_reference(branches)
    assert reference.branch_impedance_ohm(100e6).shape == (2, 2)
    assert reference.global_mna_composable is False
    with pytest.raises(GlobalMnaError, match="diagnostic-only"):
        reference.require_global_mna_composable()
    with pytest.raises(GlobalMnaError, match="non-composable"):
        filled_microvia_branch_block(branches)


def test_parallel_microvia_current_sharing_matches_legacy_equal_endpoint_reducer() -> None:
    source = (
        FilledMicroviaBranch("a", "P", "G", -40e-6, 0.0, 0.0, 80e-6, 10e-6, SIGMA),
        FilledMicroviaBranch("b", "P", "G", 40e-6, 0.0, 0.0, 80e-6, 10e-6, SIGMA),
    )
    reference = inspect_filled_microvia_reference(source)
    legacy_segments = tuple(
        FilledMicroviaSegment(item.branch_id, item.x_m, item.y_m, item.z0_m, item.z1_m, item.radius_m, item.conductivity_s_per_m, "P", "P")
        for item in source
    )
    legacy = solve_via_peec(compile_via_peec(legacy_segments), 50e6)
    branch = reference.branch_impedance_ohm(50e6)
    expected = 1.0 / (np.ones(2) @ np.linalg.solve(branch, np.ones(2)))
    assert expected == pytest.approx(legacy.impedance_ohm[0, 0], rel=1e-10, abs=1e-14)


def test_floating_conductor_gauge_is_explicit_and_node_order_invariant() -> None:
    def solve(nodes: tuple[str, ...]) -> complex:
        return compile_global_mna(nodes, branch_blocks=(_branch("r", "P", "G", 0.4),), ports=(DifferentialPort("port", "P", "G"),)).solve(1e6).impedance_ohm[0, 0]
    assert solve(("P", "G")) == pytest.approx(solve(("G", "P")), rel=1e-12)


def test_disconnected_balanced_components_have_zero_cross_impedance() -> None:
    operator = compile_global_mna(
        ("P0", "G0", "P1", "G1"),
        branch_blocks=(_branch("a", "P0", "G0", 0.3), _branch("b", "P1", "G1", 0.7)),
        ports=(DifferentialPort("a", "P0", "G0"), DifferentialPort("b", "P1", "G1")),
    )
    result = operator.solve(1e6)
    np.testing.assert_allclose(result.impedance_ohm, np.diag((0.3, 0.7)), rtol=1e-11, atol=1e-13)
    assert result.diagnostics.component_count == 2


def test_equipotential_pad_enforces_unknown_current_sharing() -> None:
    operator = compile_global_mna(
        ("SRC", "PAD_A", "PAD_B", "G"),
        branch_blocks=(
            _branch("left", "SRC", "PAD_A", 0.1),
            _branch("right", "SRC", "PAD_B", 0.3),
            _branch("a_to_g", "PAD_A", "G", 0.2),
            _branch("b_to_g", "PAD_B", "G", 0.2),
        ),
        equipotential_constraints=(EquipotentialConstraint("pad", "PAD_A", "PAD_B"),),
        ports=(DifferentialPort("port", "SRC", "G"),),
    )
    result = operator.solve(1e6)
    index = {name: number for number, name in enumerate(operator.node_ids)}
    assert result.node_voltages_v_per_a[index["PAD_A"], 0] == pytest.approx(result.node_voltages_v_per_a[index["PAD_B"], 0], abs=1e-13)
    # The unequal incoming paths share current by the solved pad voltage, not an equal-split rule.
    assert abs(result.branch_currents_a_per_a[0, 0]) > abs(result.branch_currents_a_per_a[1, 0])


def test_redundant_constraints_unbalanced_ports_reciprocity_and_passivity_fail_closed() -> None:
    with pytest.raises(GlobalMnaError, match="redundant"):
        compile_global_mna(("A", "B", "C"), equipotential_constraints=(EquipotentialConstraint("ab", "A", "B"), EquipotentialConstraint("bc", "B", "C"), EquipotentialConstraint("ca", "C", "A")), ports=(DifferentialPort("p", "A", "B"),))
    with pytest.raises(GlobalMnaError, match="separate structural"):
        compile_global_mna(("A", "B"), ports=(DifferentialPort("p", "A", "B"),)).solve(1e6)
    nonsymmetric = SeriesBranchBlock(("x", "y"), ("P", "P"), ("G", "G"), np.asarray(((1.0, 0.2), (0.0, 1.0)), dtype=np.complex128), "bad-reciprocity")
    with pytest.raises(GlobalMnaError, match="reciprocity"):
        compile_global_mna(("P", "G"), branch_blocks=(nonsymmetric,), ports=(DifferentialPort("p", "P", "G"),)).solve(1e6)
    with pytest.raises(GlobalMnaError, match="non-passive"):
        compile_global_mna(("P", "G"), branch_blocks=(_branch("negative_r", "P", "G", -1.0),), ports=(DifferentialPort("p", "P", "G"),)).solve(1e6)


def test_ill_conditioned_nodal_system_and_nonfinite_stamps_fail_closed() -> None:
    nearly_singular = SeriesBranchBlock(("ordinary", "tiny"), ("P", "P"), ("G", "G"), np.asarray(((1.0, 0.0), (0.0, 1e-15)), dtype=np.complex128), "ill-conditioned")
    with pytest.raises(GlobalMnaError, match="ill-conditioned|singular"):
        compile_global_mna(("P", "G"), branch_blocks=(nearly_singular,), ports=(DifferentialPort("p", "P", "G"),)).solve(1e6)
    nonfinite = NodalAdmittanceBlock(("P", "G"), lambda _f: np.asarray(((np.nan, 0.0), (0.0, 1.0))), "nonfinite")
    with pytest.raises(GlobalMnaError, match="finite"):
        compile_global_mna(("P", "G"), nodal_admittances=(nonfinite,), ports=(DifferentialPort("p", "P", "G"),)).solve(1e6)


def test_grounded_reduced_nodal_stamp_is_not_misinterpreted_as_floating() -> None:
    grounded = NodalAdmittanceBlock(
        ("P", "G"),
        sparse.csc_matrix(np.asarray(((2.0, -1.0), (-1.0, 2.0)))),
        "grounded-reduced-source",
    )
    with pytest.raises(GlobalMnaError, match="zero row and column sums"):
        compile_global_mna(
            ("P", "G"),
            nodal_admittances=(grounded,),
            ports=(DifferentialPort("p", "P", "G"),),
        ).solve(1e6)
    with pytest.raises(GlobalMnaError, match="floating_indefinite"):
        compile_global_mna(
            ("P", "G"),
            ports=(DifferentialPort("p", "P", "G"),),
            reference_semantics="grounded_reduced",  # type: ignore[arg-type]
        )


def test_large_sparse_complex_chain_never_calls_sparse_toarray(monkeypatch: pytest.MonkeyPatch) -> None:
    count = 300
    diagonal = np.full(count, 2.0 + 0.6j)
    diagonal[[0, -1]] = 1.0 + 0.3j
    off = np.full(count - 1, -1.0 - 0.3j)
    laplacian = sparse.diags((off, diagonal, off), (-1, 0, 1), format="csc")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a global sparse matrix was densified")

    monkeypatch.setattr(sparse.csc_matrix, "toarray", forbidden)
    nodes = tuple(f"n{index}" for index in range(count))
    result = compile_global_mna(
        nodes,
        nodal_admittances=(NodalAdmittanceBlock(nodes, lambda _f: laplacian, "complex-chain"),),
        ports=(DifferentialPort("end-to-end", nodes[0], nodes[-1]),),
    ).solve(1e6)
    expected = (count - 1) / (1.0 + 0.3j)
    assert result.impedance_ohm[0, 0] == pytest.approx(expected, rel=2e-9)
    assert result.diagnostics.scaled_saddle_condition_estimate < 1e13


def test_many_independent_branch_blocks_remain_linear_in_nnz() -> None:
    branch_count = 320
    blocks = tuple(
        SeriesBranchBlock(
            (f"r{index}",),
            ("P",),
            ("G",),
                sparse.csc_matrix(np.asarray(((1.0,),))),
            f"source-r{index}",
        )
        for index in range(branch_count)
    )
    result = compile_global_mna(
        ("P", "G"),
        branch_blocks=blocks,
        ports=(DifferentialPort("port", "P", "G"),),
    ).solve(1e6)
    assert result.impedance_ohm[0, 0] == pytest.approx(1.0 / branch_count, rel=1e-10)
    assert result.diagnostics.branch_impedance_nnz == branch_count
    assert result.diagnostics.saddle_nnz <= 7 * branch_count + 10


def test_insulated_mutually_inductive_loops_keep_independent_gauges() -> None:
    frequency = 80e6
    impedance = np.asarray(
        (
            (0.02 + 1j * 2 * pi * frequency * 0.8e-9, 1j * 2 * pi * frequency * 0.18e-9),
            (1j * 2 * pi * frequency * 0.18e-9, 0.03 + 1j * 2 * pi * frequency * 1.0e-9),
        )
    )
    result = compile_global_mna(
        ("P0", "G0", "P1", "G1"),
        branch_blocks=(
            SeriesBranchBlock(
                ("loop0", "loop1"),
                ("P0", "P1"),
                ("G0", "G1"),
                impedance,
                "mutual-field-source",
            ),
        ),
        ports=(
            DifferentialPort("port0", "P0", "G0"),
            DifferentialPort("port1", "P1", "G1"),
        ),
    ).solve(frequency)
    np.testing.assert_allclose(result.impedance_ohm, impedance, rtol=1e-10, atol=1e-13)
    assert result.diagnostics.component_count == 2
    assert abs(result.impedance_ohm[0, 1]) > 0.0


def test_reversed_via_reference_orientation_is_an_explicit_basis_transform() -> None:
    ordinary = (
        FilledMicroviaBranch("a", "P", "G", -40e-6, 0.0, 0.0, 80e-6, 10e-6, SIGMA),
        FilledMicroviaBranch("b", "P", "G", 40e-6, 0.0, 0.0, 80e-6, 10e-6, SIGMA),
    )
    reversed_second = (
        ordinary[0],
        FilledMicroviaBranch("b", "G", "P", 40e-6, 0.0, 0.0, 80e-6, 10e-6, SIGMA, -1),
    )
    direct = inspect_filled_microvia_reference(ordinary).branch_impedance_ohm(100e6)
    reversed_matrix = inspect_filled_microvia_reference(reversed_second).branch_impedance_ohm(100e6)
    basis = np.diag((1.0, -1.0))
    np.testing.assert_allclose(basis @ reversed_matrix @ basis, direct, rtol=1e-12, atol=1e-15)


def test_two_stacked_microvia_columns_cannot_bypass_global_ownership() -> None:
    vias = (
        FilledMicroviaBranch("a0", "P", "MA", 0.0, 0.0, 0.0, 40e-6, 8e-6, SIGMA),
        FilledMicroviaBranch("a1", "MA", "G", 0.0, 0.0, 40e-6, 80e-6, 8e-6, SIGMA),
        FilledMicroviaBranch("b0", "P", "MB", 60e-6, 0.0, 0.0, 40e-6, 8e-6, SIGMA),
        FilledMicroviaBranch("b1", "MB", "G", 60e-6, 0.0, 40e-6, 80e-6, 8e-6, SIGMA),
    )
    reference = inspect_filled_microvia_reference(vias)
    assert np.all(np.isfinite(reference.branch_impedance_ohm(100e6)))
    with pytest.raises(GlobalMnaError, match="actual exact/core matrices"):
        filled_microvia_branch_block(vias, block_id="stacked-array-source")


@pytest.mark.parametrize("radius_um", (2.0, 5.0, 10.0, 20.0))
def test_same_axis_microvia_total_impedance_is_invariant_to_arbitrary_splits(
    radius_um: float,
) -> None:
    radius = radius_um * 1e-6
    length = 80e-6

    def make_block(cuts_um: tuple[float, ...], source: str) -> tuple[FilledMicroviaDiagnostic, tuple[FilledMicroviaBranch, ...]]:
        cuts = tuple(value * 1e-6 for value in cuts_um)
        nodes = ("P",) + tuple(f"{source}-n{index}" for index in range(1, len(cuts) - 1)) + ("G",)
        segments = tuple(
            FilledMicroviaBranch(
                f"{source}-s{index}",
                nodes[index],
                nodes[index + 1],
                0.0,
                0.0,
                cuts[index],
                cuts[index + 1],
                radius,
                SIGMA,
            )
            for index in range(len(cuts) - 1)
        )
        return inspect_filled_microvia_reference(segments), segments

    unsplit, unsplit_segments = make_block((0.0, 80.0), f"whole-{radius_um:g}")
    split_two, two_segments = make_block((0.0, 23.0, 80.0), f"two-{radius_um:g}")
    split_four, four_segments = make_block(
        (0.0, 7.0, 31.0, 63.0, 80.0), f"four-{radius_um:g}"
    )
    expected_external = straight_wire_external_self_inductance(length, radius)

    for frequency in (1.0, 100e6, 2e9):
        totals: list[complex] = []
        for block, segments in (
            (unsplit, unsplit_segments),
            (split_two, two_segments),
            (split_four, four_segments),
        ):
            impedance = block.branch_impedance_ohm(frequency)
            currents = np.ones(len(segments), dtype=np.complex128)
            total = complex(currents @ impedance @ currents)
            totals.append(total)
            internal = sum(
                solid_cylinder_internal_impedance(
                    frequency,
                    length_m=segment.length_m,
                    radius_m=segment.radius_m,
                    conductivity_s_per_m=segment.conductivity_s_per_m,
                )
                for segment in segments
            )
            recovered_external = (total - internal) / (1j * 2 * pi * frequency)
            assert recovered_external.real == pytest.approx(
                expected_external, rel=3e-12, abs=1e-22
            )
            assert recovered_external.imag == pytest.approx(0.0, abs=1e-20)
        assert totals[1] == pytest.approx(totals[0], rel=3e-12, abs=1e-14)
        assert totals[2] == pytest.approx(totals[0], rel=3e-12, abs=1e-14)

    dc_resistance = length / (SIGMA * pi * radius**2)
    for block, segments in (
        (unsplit, unsplit_segments),
        (split_two, two_segments),
        (split_four, four_segments),
    ):
        impedance = block.branch_impedance_ohm(0.0)
        currents = np.ones(len(segments), dtype=np.complex128)
        assert complex(currents @ impedance @ currents).real == pytest.approx(
            dc_resistance, rel=2e-14
        )


def test_same_axis_unequal_radius_segments_fail_without_volume_regularization() -> None:
    with pytest.raises(GlobalMnaError, match="unequal radii"):
        inspect_filled_microvia_reference(
            (
                FilledMicroviaBranch("lower", "P", "M", 0.0, 0.0, 0.0, 40e-6, 5e-6, SIGMA),
                FilledMicroviaBranch("upper", "M", "G", 0.0, 0.0, 40e-6, 80e-6, 6e-6, SIGMA),
            ),
        )


def test_duplicate_global_ids_and_missing_source_ids_fail_closed() -> None:
    first = _branch("duplicate", "P", "G", 1.0)
    second = SeriesBranchBlock(("duplicate",), ("P",), ("G",), np.asarray(((2.0,),)), "second-source")
    with pytest.raises(GlobalMnaError, match="globally unique"):
        compile_global_mna(("P", "G"), branch_blocks=(first, second), ports=(DifferentialPort("p", "P", "G"),))
    with pytest.raises(GlobalMnaError, match="constraint_id"):
        compile_global_mna(
            ("A", "B", "C", "D"),
            equipotential_constraints=(
                EquipotentialConstraint("duplicate", "A", "B"),
                EquipotentialConstraint("duplicate", "C", "D"),
            ),
            ports=(DifferentialPort("p", "A", "B"),),
        )


def test_explicit_physical_ownership_ledger_blocks_double_stamping() -> None:
    nodal = NodalAdmittanceBlock(
        ("P", "G"),
        np.asarray(((1.0, -1.0), (-1.0, 1.0))),
        "gap-field",
        owner_ids=("physical-gap:TOP-BOT",),
    )
    duplicate = SeriesBranchBlock(
        ("replacement",),
        ("P",),
        ("G",),
        np.asarray(((1.0,),)),
        "duplicate-local-block",
        owner_ids=("physical-gap:TOP-BOT",),
    )
    with pytest.raises(GlobalMnaError, match="physical owner.*stamped"):
        compile_global_mna(
            ("P", "G"),
            nodal_admittances=(nodal,),
            branch_blocks=(duplicate,),
            ports=(DifferentialPort("p", "P", "G"),),
        )

    operator = compile_global_mna(
        ("P", "G"),
        branch_blocks=(
            SeriesBranchBlock(
                ("r",), ("P",), ("G",), np.asarray(((1.0,),)), "via",
                owner_ids=("physical-via:1",),
            ),
        ),
        ports=(DifferentialPort("p", "P", "G"),),
    )
    assert operator.ownership_ledger == {"physical-via:1": "via"}
    with pytest.raises(TypeError):
        operator.ownership_ledger["new"] = "bad"  # type: ignore[index]
    with pytest.raises(GlobalMnaError, match="block_id"):
        SeriesBranchBlock(("r",), ("P",), ("G",), np.asarray(((1.0,),)), "")
    with pytest.raises(GlobalMnaError, match="source block_id"):
        compile_global_mna(
            ("P", "G"),
            branch_blocks=(
                SeriesBranchBlock(("a",), ("P",), ("G",), np.asarray(((1.0,),)), "same-source"),
                SeriesBranchBlock(("b",), ("P",), ("G",), np.asarray(((2.0,),)), "same-source"),
            ),
            ports=(DifferentialPort("p", "P", "G"),),
        )


def test_unstamped_structural_island_fails_closed() -> None:
    with pytest.raises(GlobalMnaError, match="singular structural island"):
        compile_global_mna(
            ("P", "G", "UNUSED"),
            branch_blocks=(_branch("r", "P", "G", 1.0),),
            ports=(DifferentialPort("p", "P", "G"),),
        ).solve(1e6)


def test_hidden_negative_modes_in_local_blocks_fail_before_port_reduction() -> None:
    hidden_bad_branch = SeriesBranchBlock(
        ("visible", "hidden"),
        ("P0", "P1"),
        ("G0", "G1"),
        sparse.diags((1.0, -0.1), format="csc"),
        "hidden-negative-branch",
    )
    with pytest.raises(GlobalMnaError, match="hidden non-passive"):
        compile_global_mna(
            ("P0", "G0", "P1", "G1"),
            branch_blocks=(hidden_bad_branch,),
            ports=(DifferentialPort("visible", "P0", "G0"),),
        ).solve(1e6)
    positive = sparse.csc_matrix(np.asarray(((1.0, -1.0), (-1.0, 1.0))))
    hidden_bad_y = NodalAdmittanceBlock(
        ("P0", "G0", "P1", "G1"),
        sparse.block_diag((positive, -0.1 * positive), format="csc"),
        "hidden-negative-nodal",
    )
    with pytest.raises(GlobalMnaError, match="hidden non-passive"):
        compile_global_mna(
            ("P0", "G0", "P1", "G1"),
            nodal_admittances=(hidden_bad_y,),
            ports=(DifferentialPort("visible", "P0", "G0"),),
        ).solve(1e6)
