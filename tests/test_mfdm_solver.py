from __future__ import annotations

import numpy as np
import pytest

import spd_decap_pi._core.solver.mfdm as mfdm_module
from spd_decap_pi._core.solver.mfdm import (
    EPSILON_0_F_PER_M,
    MfdmCutCellGeometry,
    MfdmEdgeStrip,
    MfdmMaterial,
    MfdmNode,
    MfdmPort,
    MfdmSolverError,
    compile_mfdm_operator,
    copper_surface_impedance,
    copper_two_face_surface_impedance,
    solve_mfdm,
    _system_matrix,
)
from spd_decap_pi._core.solver.modal import FinitePort, RectangularCavitySolver, RectangularPlane


def _geometry(ny: int = 1, nx: int = 1, *, three_conductors: bool = False) -> MfdmCutCellGeometry:
    fractions = np.zeros((3, ny, nx), dtype=float)
    fractions[: 3 if three_conductors else 2] = 1.0
    labels = fractions.astype(np.int64)
    horizontal = np.zeros((3, ny, max(nx - 1, 0)), dtype=float)
    vertical = np.zeros((3, max(ny - 1, 0), nx), dtype=float)
    horizontal[: 3 if three_conductors else 2] = 1.0
    vertical[: 3 if three_conductors else 2] = 1.0
    overlaps = np.zeros((2, ny, nx), dtype=float)
    overlaps[0] = 1.0
    if three_conductors:
        overlaps[1] = 1.0
    return MfdmCutCellGeometry(
        cell_areas_m2=np.full((ny, nx), 1.0e-6 / (ny * nx)),
        conductor_area_fractions=fractions,
        conductor_labels=labels,
        horizontal_edge_fractions=horizontal,
        vertical_edge_fractions=vertical,
        gap_overlap_fractions=overlaps,
        dx_m=1.0e-3 / nx,
        dy_m=1.0e-3 / ny,
    )


def _material() -> MfdmMaterial:
    return MfdmMaterial(
        upper_target_gap_m=100e-6,
        target_lower_gap_m=125e-6,
        upper_target_relative_permittivity=4.0,
        target_lower_relative_permittivity=3.6,
        upper_target_loss_tangent=0.015,
        target_lower_loss_tangent=0.02,
    )


def test_single_cell_parallel_plate_capacitance_is_analytic() -> None:
    operator = compile_mfdm_operator(_geometry(), _material())
    frequency = 7.5e6
    result = solve_mfdm(operator, frequency, (MfdmPort("p", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),))
    capacitance = EPSILON_0_F_PER_M * 4.0 * 1.0e-6 / 100e-6
    expected = 1.0 / (2j * np.pi * frequency * capacitance + 2 * np.pi * frequency * capacitance * 0.015)
    np.testing.assert_allclose(result.impedance_ohm[0, 0], expected, rtol=2e-12, atol=1e-12)
    assert result.diagnostics.residual_relative < 1e-12
    assert not operator.geometry.cell_areas_m2.flags.writeable
    assert not operator.node_index.flags.writeable
    assert not result.impedance_ohm.flags.writeable
    with pytest.raises(ValueError):
        result.impedance_ohm[0, 0] = 0.0


def test_raw_validation_failure_never_returns_an_impedance_curve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = compile_mfdm_operator(_geometry(), _material())
    monkeypatch.setattr(
        mfdm_module,
        "_raw_pairwise_reciprocity",
        lambda _matrix: (1.0, 1.0e-3),
    )
    with pytest.raises(MfdmSolverError, match="raw solve validation failed"):
        solve_mfdm(
            operator,
            1e6,
            (MfdmPort("p", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),),
        )


def test_nonpassive_port_matrix_never_returns_an_impedance_curve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = compile_mfdm_operator(_geometry(), _material())
    monkeypatch.setattr(
        mfdm_module.np.linalg,
        "eigvalsh",
        lambda _matrix: np.asarray((-1.0,)),
    )
    with pytest.raises(MfdmSolverError, match="open-port impedance is non-passive"):
        solve_mfdm(
            operator,
            1e6,
            (MfdmPort("p", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),),
        )


def test_three_conductor_cell_matches_hand_dense_mna() -> None:
    operator = compile_mfdm_operator(_geometry(three_conductors=True), _material())
    frequency = 2.0e6
    ports = (
        MfdmPort("ut", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),
        MfdmPort("tl", MfdmNode(1, 0, 0), MfdmNode(2, 0, 0)),
    )
    result = solve_mfdm(operator, frequency, ports)
    omega = 2 * np.pi * frequency
    c_ut = EPSILON_0_F_PER_M * 4.0 * 1e-6 / 100e-6
    c_tl = EPSILON_0_F_PER_M * 3.6 * 1e-6 / 125e-6
    y_ut = omega * c_ut * 0.015 + 1j * omega * c_ut
    y_tl = omega * c_tl * 0.02 + 1j * omega * c_tl
    dense = np.asarray(((y_ut, -y_ut, 0), (-y_ut, y_ut + y_tl, -y_tl), (0, -y_tl, y_tl)), dtype=complex)
    b = np.asarray(((1, 0), (-1, 1), (0, -1)), dtype=complex)
    # Same one-node gauge convention as the sparse kernel.
    voltage = np.zeros((3, 2), dtype=complex)
    voltage[1:] = np.linalg.solve(dense[1:, 1:], b[1:])
    expected = b.T @ voltage
    np.testing.assert_allclose(result.impedance_ohm, expected, rtol=2e-12, atol=1e-12)


def test_two_face_sheet_owners_are_explicit_and_cannot_be_duplicated() -> None:
    geometry = _geometry(three_conductors=True)
    operator = compile_mfdm_operator(
        geometry,
        _material(),
        sheet_owner_ids=("face:TOP", "face:MID", "face:BOT"),
    )
    assert operator.sheet_owner_ids == ("face:TOP", "face:MID", "face:BOT")
    assert operator.two_face_sheet_owner_ids == ("face:MID",)
    result = solve_mfdm(
        operator,
        1e6,
        (MfdmPort("p", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),),
    )
    assert result.diagnostics.physical_sheet_count == 3
    assert result.diagnostics.two_face_sheet_count == 1
    with pytest.raises(MfdmSolverError, match="must be unique"):
        compile_mfdm_operator(
            geometry,
            _material(),
            sheet_owner_ids=("face:TOP", "face:MID", "face:MID"),
        )
    with pytest.raises(MfdmSolverError, match="non-negative integer"):
        MfdmNode(0.5, 0, 0)  # type: ignore[arg-type]


def test_surface_impedance_has_dc_and_skin_effect_limits() -> None:
    sigma, thickness = 5.8e7, 35e-6
    assert copper_surface_impedance(0.0, sigma, thickness) == pytest.approx(1.0 / (sigma * thickness))
    frequency = 100e9
    value = copper_surface_impedance(frequency, sigma, thickness)
    asymptote = (1.0 + 1.0j) * np.sqrt(2 * np.pi * frequency * 4e-7 * np.pi / (2 * sigma))
    np.testing.assert_allclose(value, asymptote, rtol=2e-4)
    low = copper_surface_impedance(10.0, sigma, thickness)
    assert low.real == pytest.approx(1.0 / (sigma * thickness), rel=1e-8)
    assert low.imag > 0.0


def test_two_face_surface_impedance_has_dc_transfer_and_thick_skin_limits() -> None:
    sigma, thickness = 5.8e7, 35e-6
    dc = copper_two_face_surface_impedance(0.0, sigma, thickness)
    resistance = 1.0 / (sigma * thickness)
    np.testing.assert_allclose(dc, resistance * np.ones((2, 2)), rtol=2e-13, atol=2e-13)
    near_dc = copper_two_face_surface_impedance(10.0, sigma, thickness)
    np.testing.assert_allclose(near_dc.real, resistance * np.ones((2, 2)), rtol=2e-8, atol=2e-13)
    high = copper_two_face_surface_impedance(100e9, sigma, thickness)
    assert abs(high[0, 1]) < abs(high[0, 0]) * 1e-8
    np.testing.assert_allclose(high, high.T, rtol=2e-13, atol=2e-13)


def test_open_multiport_with_large_raw_reciprocity_error_fails_closed() -> None:
    operator = compile_mfdm_operator(_geometry(2, 2, three_conductors=True), _material())
    ports = (
        MfdmPort("ut-a", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),
        MfdmPort("ut-b", MfdmNode(0, 1, 1), MfdmNode(1, 1, 1)),
        MfdmPort("tl", MfdmNode(1, 0, 1), MfdmNode(2, 0, 1)),
    )
    # This coarse mixed-gap case historically returned a visibly
    # non-reciprocal raw matrix (about 8.8 micro-ohm absolute mismatch).
    # It is a useful fail-closed regression, not a valid product curve.
    with pytest.raises(MfdmSolverError, match="raw solve validation failed"):
        solve_mfdm(operator, 30e6, ports)


def test_rectangular_pair_zero_mode_anchor_agrees_with_existing_modal_solver() -> None:
    # A one-cell whole-board port is precisely the rectangular cavity's
    # constant mode.  Localised cut-cell ports are intentionally covered by a
    # separate non-zero-mode test below, not averaged into this zero-mode check.
    frequency = 100e6
    analytic_c = EPSILON_0_F_PER_M * 4.0 * 1.0e-6 / 100e-6
    target = abs(1.0 / (2j * np.pi * frequency * analytic_c))
    operator = compile_mfdm_operator(_geometry(), _material())
    result = solve_mfdm(operator, frequency, (MfdmPort("p", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),))
    modal = RectangularCavitySolver(
        RectangularPlane(1e-3, 1e-3, 100e-6, 4.0, loss_tangent=0.015),
        mode_count=1,
    ).solve_ideal_port(np.asarray([frequency]), FinitePort(0.5e-3, 0.5e-3, 1e-3, 1e-3))
    assert abs(abs(result.impedance_ohm[0, 0]) - target) / target < 2e-4
    np.testing.assert_allclose(abs(modal.impedance_ohm[0]), target, rtol=2e-4)


def _capacitive_part(operator, frequency: float) -> np.ndarray:
    matrix = np.zeros((operator.node_count, operator.node_count), dtype=np.complex128)
    _, _, tangents = operator.material.gap_properties(operator.geometry.conductor_area_fractions.shape[0] - 1)
    for gap, node_a, node_b, capacitance in operator.capacitance_stamps:
        y = 2 * np.pi * frequency * capacitance * (tangents[int(gap)] + 1j)
        indices = (int(node_a), int(node_b))
        matrix[np.ix_(indices, indices)] += y * np.asarray(((1.0, -1.0), (-1.0, 1.0)))
    return matrix


def _loop_impedance(operator, frequency: float, gap: int, factor: float) -> complex:
    separations, _, _ = operator.material.gap_properties(operator.geometry.conductor_area_fractions.shape[0] - 1)
    conductivities, thicknesses = operator.material.conductor_properties(operator.geometry.conductor_area_fractions.shape[0])
    return (
        copper_surface_impedance(frequency, conductivities[gap], thicknesses[gap])
        + copper_surface_impedance(frequency, conductivities[gap + 1], thicknesses[gap + 1])
        + 2j * np.pi * frequency * operator.material.permeability_h_per_m * separations[gap]
    ) * factor


def test_lateral_loop_is_exact_rank_one_four_port_and_order_invariant() -> None:
    operator = compile_mfdm_operator(_geometry(1, 2), _material())
    frequency = 3e8
    loop_only = _system_matrix(operator, frequency).toarray() - _capacitive_part(operator, frequency)
    gap, n0, n1, n2, n3, factor = operator.loop_stamps[0]
    nodes = tuple(int(value) for value in (n0, n1, n2, n3))
    admittance = 1.0 / _loop_impedance(operator, frequency, int(gap), float(factor))
    expected_local = admittance * np.outer((1.0, -1.0, 1.0, -1.0), (1.0, -1.0, 1.0, -1.0))
    np.testing.assert_allclose(loop_only[np.ix_(nodes, nodes)], expected_local, rtol=2e-13, atol=2e-13)
    assert np.linalg.matrix_rank(loop_only, tol=abs(admittance) * 1e-11) == 1
    # Paper ordering [top_a, top_b, bottom_a, bottom_b] has the lower pair
    # swapped and therefore the equivalent signs (+,-,-,+).
    paper_nodes = (nodes[0], nodes[1], nodes[3], nodes[2])
    paper_local = admittance * np.outer((1.0, -1.0, -1.0, 1.0), (1.0, -1.0, -1.0, 1.0))
    np.testing.assert_allclose(loop_only[np.ix_(paper_nodes, paper_nodes)], paper_local, rtol=2e-13, atol=2e-13)


def test_three_layer_shared_middle_uses_coupled_k_and_conserves_sheet_loss() -> None:
    operator = compile_mfdm_operator(_geometry(1, 2, three_conductors=True), _material())
    frequency = 3e8
    loop_only = _system_matrix(operator, frequency).toarray() - _capacitive_part(operator, frequency)
    gap_ids = operator.coupled_edge_gap_ids[0]
    assert tuple(gap_ids) == (0, 1)
    conductivities, thicknesses = operator.material.conductor_properties(3)
    faces = [
        copper_two_face_surface_impedance(frequency, conductivity, thickness)
        for conductivity, thickness in zip(conductivities, thicknesses, strict=True)
    ]
    separations, _, _ = operator.material.gap_properties(2)
    maps = (
        np.asarray(((0.0, 0.0), (1.0, 0.0))),
        np.asarray(((-1.0, 0.0), (0.0, 1.0))),
        np.asarray(((0.0, -1.0), (0.0, 0.0))),
    )
    k = np.diag(
        [
            2j * np.pi * frequency * operator.material.permeability_h_per_m * separation * factor
            for separation, factor in zip(separations, operator.coupled_edge_gap_factors[0], strict=True)
        ]
    )
    for face, mapping, factor in zip(faces, maps, operator.coupled_edge_conductor_factors[0], strict=True):
        k += factor * mapping.T @ face @ mapping
    # The blocking term: shared middle conductor yields -Zc*csch(gamma*t),
    # approaching the former -R sheet term at DC and zero at thick skin.
    middle_transfer = faces[1][0, 1] * operator.coupled_edge_conductor_factors[0, 1]
    np.testing.assert_allclose(k[0, 1], -middle_transfer, rtol=2e-13, atol=2e-13)
    node_ids = tuple(dict.fromkeys(int(node) for nodes in operator.coupled_edge_nodes[0] for node in nodes))
    g = np.zeros((len(node_ids), 2))
    local = {node: index for index, node in enumerate(node_ids)}
    for column, nodes in enumerate(operator.coupled_edge_nodes[0]):
        for node, sign in zip(nodes, (1.0, -1.0, 1.0, -1.0), strict=True):
            g[local[int(node)], column] += sign
    expected = np.zeros_like(loop_only)
    expected[np.ix_(node_ids, node_ids)] = g @ np.linalg.solve(k, g.T)
    np.testing.assert_allclose(loop_only, expected, rtol=2e-13, atol=2e-13)
    q = np.asarray((0.7 + 0.2j, -0.3 + 0.9j))
    lhs = np.vdot(q, k.real @ q).real
    rhs = sum(
        np.vdot(mapping @ q, (factor * face.real) @ (mapping @ q)).real
        for face, mapping, factor in zip(faces, maps, operator.coupled_edge_conductor_factors[0], strict=True)
    )
    assert lhs == pytest.approx(rhs, rel=2e-13, abs=2e-13)


def _partial_edge_geometry(*, common_support: float) -> MfdmCutCellGeometry:
    """A geometrically valid three-layer partition with exact strips."""
    # Both-gap width is ``common_support``.  The remaining active strips are
    # deliberately disjoint, so the core must not average them into one K.
    strips = tuple(
        ([MfdmEdgeStrip(common_support, (1, 1, 1))] if common_support else [])
        + [
            MfdmEdgeStrip(0.35, (1, 1, 0)),
            MfdmEdgeStrip(0.25, (0, 1, 1)),
            MfdmEdgeStrip(0.40 - common_support, (0, 0, 0)),
        ]
    )
    return MfdmCutCellGeometry(
        cell_areas_m2=np.asarray([[0.5e-6, 0.5e-6]]),
        conductor_area_fractions=np.ones((3, 1, 2)),
        conductor_labels=np.ones((3, 1, 2), dtype=np.int64),
        horizontal_edge_fractions=np.asarray([[[0.35 + common_support]], [[0.60 + common_support]], [[0.25 + common_support]]]),
        vertical_edge_fractions=np.empty((3, 0, 2)),
        gap_overlap_fractions=np.ones((2, 1, 2)),
        horizontal_gap_edge_overlap_fractions=np.asarray([[[0.35 + common_support]], [[0.25 + common_support]]]),
        vertical_gap_edge_overlap_fractions=np.empty((2, 0, 2)),
        horizontal_adjacent_gap_overlap_fractions=np.asarray([[[common_support]]]),
        vertical_adjacent_gap_overlap_fractions=np.empty((1, 0, 2)),
        horizontal_edge_strips=((strips,),),
        vertical_edge_strips=(),
        dx_m=2e-3,
        dy_m=1e-3,
    )


def _unequal_sheet_material() -> MfdmMaterial:
    return MfdmMaterial(
        upper_target_gap_m=100e-6,
        target_lower_gap_m=125e-6,
        upper_target_relative_permittivity=4.0,
        target_lower_relative_permittivity=3.6,
        conductivity_s_per_m=(4.2e7, 5.8e7, 3.1e7),
        copper_thickness_m=(20e-6, 35e-6, 50e-6),
    )


def _edge_loop_stamp_from_analytic_k(operator, frequency: float, k: np.ndarray) -> np.ndarray:
    """Analytic four-terminal edge stamp, independent of solver factors."""
    edge_nodes = operator.coupled_edge_nodes[0]
    node_ids = tuple(dict.fromkeys(int(node) for nodes in edge_nodes for node in nodes))
    g = np.zeros((len(node_ids), 2))
    local = {node: index for index, node in enumerate(node_ids)}
    for column, nodes in enumerate(edge_nodes):
        for node, sign in zip(nodes, (1.0, -1.0, 1.0, -1.0), strict=True):
            g[local[int(node)], column] += sign
    expected = np.zeros((operator.node_count, operator.node_count), dtype=complex)
    expected[np.ix_(node_ids, node_ids)] = g @ np.linalg.solve(k, g.T)
    return expected


def test_partial_shared_edge_uses_individual_face_widths_and_exact_common_support() -> None:
    geometry = _partial_edge_geometry(common_support=0.25)
    material = _unequal_sheet_material()
    operator = compile_mfdm_operator(geometry, material)
    frequency = 10.0
    length, width = geometry.dx_m, geometry.dy_m
    # one upper-only, one both, and one lower-only strip -- no scalar
    # aggregate exists in the compiled operator.
    assert operator.coupled_edge_gap_ids.shape[0] == 3
    assert tuple(operator.coupled_edge_gap_ids[0]) == (0, 1)
    assert tuple(operator.coupled_edge_gap_ids[1]) == (0, -1)
    assert tuple(operator.coupled_edge_gap_ids[2]) == (1, -1)
    np.testing.assert_allclose(operator.coupled_edge_gap_factors[0], (length / (width * 0.25), length / (width * 0.25)))
    assert operator.coupled_edge_gap_factors[1, 0] == pytest.approx(length / (width * 0.35))
    assert operator.coupled_edge_gap_factors[2, 0] == pytest.approx(length / (width * 0.25))
    assert operator.coupled_edge_cross_factors[0, 0] == pytest.approx(length / (width * 0.25), rel=2e-13)
    loop_only = _system_matrix(operator, frequency).toarray() - _capacitive_part(operator, frequency)
    np.testing.assert_allclose(loop_only, loop_only.T, rtol=2e-12, atol=2e-12)
    assert np.linalg.eigvalsh(loop_only.real).min() >= -1e-12


def test_maximal_edge_strips_match_independent_reference_and_reject_scalar_aggregate() -> None:
    """35% upper-only, 25% both, 25% lower-only is not one edge K matrix."""
    geometry = _partial_edge_geometry(common_support=0.25)
    material = _unequal_sheet_material()
    operator = compile_mfdm_operator(geometry, material)
    frequency = 10.0
    exact = _system_matrix(operator, frequency).toarray() - _capacitive_part(operator, frequency)
    conductivity, thickness = material.conductor_properties(3)
    face = [copper_two_face_surface_impedance(frequency, sigma, depth) for sigma, depth in zip(conductivity, thickness, strict=True)]
    separations, _epsilon, _loss = material.gap_properties(2)
    base = geometry.dx_m / geometry.dy_m
    g0 = np.asarray((1, -1, -1, 1, 0, 0), dtype=float)[:, None]
    g1 = np.asarray((0, 0, 1, -1, -1, 1), dtype=float)[:, None]

    def strip_stamp(width: float, gaps: tuple[int, ...]) -> np.ndarray:
        factor = base / width
        g = np.concatenate(tuple(g0 if gap == 0 else g1 for gap in gaps), axis=1)
        k = np.diag([1j * 2 * np.pi * frequency * material.permeability_h_per_m * separations[gap] * factor for gap in gaps]).astype(complex)
        if 0 in gaps:
            position = gaps.index(0)
            k[position, position] += factor * (face[0][1, 1] + face[1][0, 0])
        if 1 in gaps:
            position = gaps.index(1)
            k[position, position] += factor * (face[1][1, 1] + face[2][0, 0])
        if gaps == (0, 1):
            k[0, 1] -= factor * face[1][0, 1]
            k[1, 0] -= factor * face[1][1, 0]
        return g @ np.linalg.solve(k, g.T)

    independent = strip_stamp(0.35, (0,)) + strip_stamp(0.25, (0, 1)) + strip_stamp(0.25, (1,))
    np.testing.assert_allclose(exact, independent, rtol=2e-12, atol=2e-12)

    # Historical scalar aggregation has widths g0=.60, g1=.50, shared=.25.
    # Its single inverse differs by about 3.05% at low frequency for this
    # material (and the difference collapses as skin-effect transfer fades);
    # that is a topology error, not a mesh-convergence error.
    f0, f1, fc = base / 0.60, base / 0.50, base * 0.25 / (0.60 * 0.50)
    # This is the *actual* historical equation: each face self term used its
    # gap-support factor; only the middle transfer term used the scalar common
    # support correction.  Conductor total coverage was collected but did not
    # enter the assembled face geometry.
    aggregate_k = np.asarray(((
        f0 * (face[0][1, 1] + face[1][0, 0])
        + 1j * 2 * np.pi * frequency * material.permeability_h_per_m * separations[0] * f0,
        -fc * face[1][0, 1],
    ), (
        -fc * face[1][1, 0],
        f1 * (face[1][1, 1] + face[2][0, 0])
        + 1j * 2 * np.pi * frequency * material.permeability_h_per_m * separations[1] * f1,
    )))
    aggregate_g = np.concatenate((g0, g1), axis=1)
    aggregate = aggregate_g @ np.linalg.solve(aggregate_k, aggregate_g.T)
    relative_difference = np.linalg.norm(independent - aggregate) / np.linalg.norm(independent)
    assert 0.029 < relative_difference < 0.032


def test_disjoint_adjacent_gap_support_has_no_shared_sheet_transfer() -> None:
    operator = compile_mfdm_operator(_partial_edge_geometry(common_support=0.0), _unequal_sheet_material())
    assert np.all(operator.coupled_edge_cross_factors == 0.0)
    result = solve_mfdm(
        operator,
        10e6,
        (
            MfdmPort("upper", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),
            MfdmPort("lower", MfdmNode(1, 0, 1), MfdmNode(2, 0, 1)),
        ),
    )
    # Adjacent-gap coordinates reveal the two exact electrical blocks.  A
    # node-deletion gauge mixes these modes and used to create a spurious
    # ~8.5 micro-ohm cross impedance through sparse-LU cancellation.
    system = _system_matrix(operator, 10e6).toarray()
    np.testing.assert_allclose(system, system.T, rtol=2e-13, atol=2e-13)
    assert result.impedance_ohm[0, 1] == 0.0j
    assert result.impedance_ohm[1, 0] == 0.0j
    assert result.diagnostics.structural_component_count == 2
    assert result.diagnostics.raw_reciprocity_pair_max_relative == 0.0
    assert result.diagnostics.raw_validation_passed
    # This deliberately ill-conditioned small example is usable but does not
    # claim a sub-0.1 micro-ohm forward-error certificate.
    assert not result.diagnostics.solve_quality_estimate_passed
    assert result.diagnostics.reciprocity_relative < 1e-9
    assert result.diagnostics.passive


def test_weak_shared_face_coupling_with_unequal_sheet_widths_is_not_erased() -> None:
    """A real small common face stays reciprocal; only true blocks are zeroed."""

    operator = compile_mfdm_operator(_partial_edge_geometry(common_support=0.01), _unequal_sheet_material())
    result = solve_mfdm(
        operator,
        10e6,
        (
            MfdmPort("upper", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),
            MfdmPort("lower", MfdmNode(1, 0, 1), MfdmNode(2, 0, 1)),
        ),
    )
    assert result.diagnostics.structural_component_count == 1
    assert abs(result.impedance_ohm[0, 1]) > 1e-7
    assert result.diagnostics.raw_reciprocity_pair_max_relative <= 1e-3
    assert result.diagnostics.raw_validation_passed
    np.testing.assert_allclose(result.impedance_ohm, result.impedance_ohm.T, rtol=0.0, atol=0.0)


def test_partial_scalar_edge_coverage_without_exact_support_fails_closed() -> None:
    with pytest.raises(MfdmSolverError, match="required for partial conductor edge coverage"):
        MfdmCutCellGeometry(
            cell_areas_m2=np.asarray([[0.5e-6, 0.5e-6]]),
            conductor_area_fractions=np.ones((2, 1, 2)),
            conductor_labels=np.ones((2, 1, 2), dtype=np.int64),
            horizontal_edge_fractions=np.asarray([[[0.5]], [[0.5]]]),
            vertical_edge_fractions=np.empty((2, 0, 2)),
            gap_overlap_fractions=np.ones((1, 1, 2)),
            dx_m=1e-3,
            dy_m=1e-3,
        )


def test_nonzero_lateral_mode_correlates_with_rectangular_cavity_and_gauge_solve() -> None:
    # Two half-board differential ports excite the first lateral spatial mode.
    # The two-cell MFDM is deliberately coarse, so the assertion is a bounded
    # correlation check rather than a false claim of mesh-converged agreement.
    frequency = 1e9
    operator = compile_mfdm_operator(_geometry(1, 2), _material())
    ports = (
        MfdmPort("left", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),
        MfdmPort("right", MfdmNode(0, 0, 1), MfdmNode(1, 0, 1)),
    )
    result = solve_mfdm(operator, frequency, ports)
    weights = np.asarray((0.5, -0.5))
    mfdm_mode = weights @ result.impedance_ohm @ weights
    cavity = RectangularCavitySolver(
        RectangularPlane(1e-3, 1e-3, 100e-6, 4.0, loss_tangent=0.015),
        max_mode_x=1,
        max_mode_y=0,
    )
    left = cavity.basis_vector(FinitePort(0.25e-3, 0.5e-3, 0.5e-3, 1e-3))
    right = cavity.basis_vector(FinitePort(0.75e-3, 0.5e-3, 0.5e-3, 1e-3))
    source = 0.5 * (left - right)
    modal_mode = source @ np.diag(cavity.modal_impedance(np.asarray((frequency,)))[0]) @ source
    assert result.diagnostics.residual_relative < 2e-9
    assert result.diagnostics.reciprocity_relative < 1e-8
    assert mfdm_mode.imag > 0.0 and modal_mode.imag > 0.0
    assert 0.5 < abs(mfdm_mode / modal_mode) < 2.0


def test_local_reference_choice_leaves_open_port_z_invariant_with_direct_lu() -> None:
    geometry = _geometry(1, 2, three_conductors=True)
    ports = (
        MfdmPort("ut", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),
        MfdmPort("tl", MfdmNode(1, 0, 1), MfdmNode(2, 0, 1)),
    )
    upper_reference = compile_mfdm_operator(
        geometry,
        _material(),
        reference_conductors=np.asarray([[0, 0]]),
    )
    target_reference = compile_mfdm_operator(
        geometry,
        _material(),
        reference_conductors=np.asarray([[1, 1]]),
    )
    upper_result = solve_mfdm(upper_reference, 1e9, ports)
    target_result = solve_mfdm(target_reference, 1e9, ports)
    assert upper_result.diagnostics.local_reference_count == 2
    assert target_result.diagnostics.local_reference_count == 2
    assert np.isfinite(upper_result.diagnostics.condition_estimate)
    assert np.isfinite(target_result.diagnostics.condition_estimate)
    np.testing.assert_allclose(upper_result.impedance_ohm, target_result.impedance_ohm, rtol=2e-10, atol=2e-10)


def test_port_spanning_local_reference_columns_fails_closed() -> None:
    operator = compile_mfdm_operator(_geometry(1, 2), _material())
    with pytest.raises(MfdmSolverError, match="spans raster columns"):
        solve_mfdm(
            operator,
            1e9,
            (MfdmPort("bad", MfdmNode(0, 0, 0), MfdmNode(1, 0, 1)),),
        )


def test_different_labels_are_not_silently_unioned() -> None:
    geometry = _geometry(1, 2)
    labels = np.array(geometry.conductor_labels, copy=True)
    labels[0, 0, 1] = 2
    with pytest.raises(MfdmSolverError, match="differently labelled"):
        MfdmCutCellGeometry(
            cell_areas_m2=geometry.cell_areas_m2,
            conductor_area_fractions=geometry.conductor_area_fractions,
            conductor_labels=labels,
            horizontal_edge_fractions=geometry.horizontal_edge_fractions,
            vertical_edge_fractions=geometry.vertical_edge_fractions,
            gap_overlap_fractions=geometry.gap_overlap_fractions,
            dx_m=geometry.dx_m,
            dy_m=geometry.dy_m,
        )


def test_unresolved_mixed_artwork_cell_fails_closed() -> None:
    geometry = _geometry()
    with pytest.raises(MfdmSolverError, match="multiple artworks"):
        MfdmCutCellGeometry(
            cell_areas_m2=geometry.cell_areas_m2,
            conductor_area_fractions=geometry.conductor_area_fractions,
            conductor_labels=geometry.conductor_labels,
            horizontal_edge_fractions=geometry.horizontal_edge_fractions,
            vertical_edge_fractions=geometry.vertical_edge_fractions,
            gap_overlap_fractions=geometry.gap_overlap_fractions,
            dx_m=geometry.dx_m,
            dy_m=geometry.dy_m,
            unresolved_mixed_artwork_mask=np.asarray([[[True]], [[False]], [[False]]]),
        )


def test_bounded_four_layer_slab_accepts_explicit_gap_materials() -> None:
    geometry = MfdmCutCellGeometry(
        cell_areas_m2=np.asarray([[1e-6]]),
        conductor_area_fractions=np.ones((4, 1, 1)),
        conductor_labels=np.ones((4, 1, 1), dtype=np.int64),
        horizontal_edge_fractions=np.empty((4, 1, 0)),
        vertical_edge_fractions=np.empty((4, 0, 1)),
        gap_overlap_fractions=np.ones((3, 1, 1)),
        dx_m=1e-3,
        dy_m=1e-3,
    )
    material = MfdmMaterial(
        100e-6, 125e-6, 4.0, 3.6,
        gap_separations_m=(100e-6, 125e-6, 90e-6),
        gap_relative_permittivities=(4.0, 3.6, 3.8),
        gap_loss_tangents=(0.01, 0.015, 0.02),
    )
    result = solve_mfdm(
        compile_mfdm_operator(geometry, material),
        5e6,
        (MfdmPort("outer", MfdmNode(0, 0, 0), MfdmNode(3, 0, 0)),),
    )
    assert result.diagnostics.passive
    assert result.impedance_ohm[0, 0].imag < 0.0


def test_four_layer_lateral_k_has_nearest_shared_face_couplings_only() -> None:
    geometry = MfdmCutCellGeometry(
        cell_areas_m2=np.asarray([[5e-7, 5e-7]]),
        conductor_area_fractions=np.ones((4, 1, 2)),
        conductor_labels=np.ones((4, 1, 2), dtype=np.int64),
        horizontal_edge_fractions=np.ones((4, 1, 1)),
        vertical_edge_fractions=np.empty((4, 0, 2)),
        gap_overlap_fractions=np.ones((3, 1, 2)),
        dx_m=0.5e-3,
        dy_m=1e-3,
    )
    material = MfdmMaterial(
        100e-6, 125e-6, 4.0, 3.6,
        gap_separations_m=(100e-6, 125e-6, 90e-6),
        gap_relative_permittivities=(4.0, 3.6, 3.8),
        gap_loss_tangents=(0.01, 0.015, 0.02),
    )
    operator = compile_mfdm_operator(geometry, material)
    assert tuple(operator.coupled_edge_gap_ids[0]) == (0, 1, 2)
    frequency = 2e9
    conductivity, thickness = material.conductor_properties(4)
    faces = [
        copper_two_face_surface_impedance(frequency, sigma, t)
        for sigma, t in zip(conductivity, thickness, strict=True)
    ]
    factor = operator.coupled_edge_conductor_factors[0]
    maps = (
        np.asarray(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))),
        np.asarray(((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0))),
        np.asarray(((0.0, -1.0, 0.0), (0.0, 0.0, 1.0))),
        np.asarray(((0.0, 0.0, -1.0), (0.0, 0.0, 0.0))),
    )
    k_sheet = sum(
        edge_factor * mapping.T @ face @ mapping
        for face, mapping, edge_factor in zip(faces, maps, factor, strict=True)
    )
    # q0/q1 share layer 1; q1/q2 share layer 2; q0/q2 share no sheet.
    np.testing.assert_allclose(k_sheet[0, 1], -faces[1][0, 1] * factor[1], rtol=2e-13, atol=2e-13)
    np.testing.assert_allclose(k_sheet[1, 2], -faces[2][0, 1] * factor[2], rtol=2e-13, atol=2e-13)
    assert k_sheet[0, 2] == pytest.approx(0.0)
    assert operator.coupled_edge_gap_ids.shape[1] == 3
    result = solve_mfdm(
        operator,
        frequency,
        (MfdmPort("outer", MfdmNode(0, 0, 0), MfdmNode(3, 0, 0)),),
    )
    assert result.diagnostics.passive
    assert result.diagnostics.reciprocity_absolute_ohm == pytest.approx(0.0)


def test_disconnected_balanced_plane_pairs_have_exact_zero_cross_transfer() -> None:
    geometry = _geometry(1, 2)
    horizontal = np.array(geometry.horizontal_edge_fractions, copy=True)
    horizontal[:2, 0, 0] = 0.0
    separated = MfdmCutCellGeometry(
        cell_areas_m2=geometry.cell_areas_m2,
        conductor_area_fractions=geometry.conductor_area_fractions,
        conductor_labels=geometry.conductor_labels,
        horizontal_edge_fractions=horizontal,
        vertical_edge_fractions=geometry.vertical_edge_fractions,
        gap_overlap_fractions=geometry.gap_overlap_fractions,
        dx_m=geometry.dx_m,
        dy_m=geometry.dy_m,
    )
    operator = compile_mfdm_operator(separated, _material())
    frequency = 10e6
    result = solve_mfdm(operator, frequency, (
        MfdmPort("left", MfdmNode(0, 0, 0), MfdmNode(1, 0, 0)),
        MfdmPort("right", MfdmNode(0, 0, 1), MfdmNode(1, 0, 1)),
    ))
    capacitance = EPSILON_0_F_PER_M * 4.0 * 0.5e-6 / 100e-6
    expected = 1.0 / (2j * np.pi * frequency * capacitance + 2 * np.pi * frequency * capacitance * 0.015)
    np.testing.assert_allclose(np.diag(result.impedance_ohm), (expected, expected), rtol=2e-12, atol=1e-12)
    assert result.impedance_ohm[0, 1] == 0.0j
    assert result.impedance_ohm[1, 0] == 0.0j
    fractions = np.array(geometry.conductor_area_fractions, copy=True)
    fractions[0, 0, 0] = 1e-12
    with pytest.raises(MfdmSolverError, match="non-sliver"):
        MfdmCutCellGeometry(
            cell_areas_m2=geometry.cell_areas_m2,
            conductor_area_fractions=fractions,
            conductor_labels=geometry.conductor_labels,
            horizontal_edge_fractions=geometry.horizontal_edge_fractions,
            vertical_edge_fractions=geometry.vertical_edge_fractions,
            gap_overlap_fractions=geometry.gap_overlap_fractions,
            dx_m=geometry.dx_m,
            dy_m=geometry.dy_m,
        )


def test_single_active_layer_raster_column_fails_closed_before_projection() -> None:
    geometry = MfdmCutCellGeometry(
        cell_areas_m2=np.asarray([[1.0e-6]]),
        conductor_area_fractions=np.asarray([[[1.0]], [[0.0]]]),
        conductor_labels=np.asarray([[[1]], [[0]]]),
        horizontal_edge_fractions=np.empty((2, 1, 0)),
        vertical_edge_fractions=np.empty((2, 0, 1)),
        gap_overlap_fractions=np.zeros((1, 1, 1)),
        dx_m=1e-3,
        dy_m=1e-3,
    )
    with pytest.raises(MfdmSolverError, match="only one active conductor"):
        compile_mfdm_operator(geometry, _material())
