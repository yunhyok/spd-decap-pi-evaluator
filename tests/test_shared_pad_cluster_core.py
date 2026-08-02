from __future__ import annotations

import gc
import tracemalloc
import weakref

import numpy as np
import pytest
from pydantic import ValidationError

from spd_decap_pi._core.domain import (
    CapModel,
    MLOOutline,
    PlacementAssignment,
    ProjectSpec,
    RailSpec,
    SharedPadClusterSpec,
    SharedPadPowerComponentSpec,
    SharedPadViaPath,
    StackupLayer,
    TerminalKind,
    TopologyKind,
    TopologyMap,
    ViaLoopTemplate,
    ViaPathKind,
)
from spd_decap_pi._core.models.circuit import (
    CircuitModelError,
    DirectBranchModel,
    SharedPadClusterModel,
)
from spd_decap_pi._core.models.impedance import (
    ConstantImpedanceModel,
    SampledImpedanceModel,
    SeriesRLModel,
)
from spd_decap_pi._core.solver.evaluator import (
    EvaluationError,
    EvaluationRequest,
    _planes_from_project,
    _placement_shunts,
    compile_evaluation_kernel,
    evaluate_shunt_sensitivity,
    sensitivity_port_id,
)
from spd_decap_pi._core.solver.metrics import (
    ConfidenceCategory,
    ConfidenceInputs,
    ConfidenceLevel,
    TargetMask,
    assess_confidence,
)
from spd_decap_pi._core.solver.modal import (
    CoupledShuntGroup,
    DeviceBranch,
    DeviceConnection,
    FinitePort,
    RectangularCavitySolver,
    RectangularPlane,
    ModalSolverError,
    ShuntGroup,
    SolverDiagnostics,
)


def _constant(model_id: str, value: complex) -> ConstantImpedanceModel:
    return ConstantImpedanceModel(model_id, value)


def test_shared_pad_one_power_one_ground_matches_direct_branch() -> None:
    frequencies = np.geomspace(1.0e3, 1.0e9, 41)
    via = _constant("VIA", 0.01 + 0.03j)
    cap = _constant("CAP", 0.02 - 0.2j)
    cluster = SharedPadClusterModel("CLUSTER", (via,), (via,), (cap,))
    direct = DirectBranchModel("DIRECT", cap, via)

    # Colocated PWR/GND populations are identical, so the modal stamp is the
    # sum of all transformed two-terminal admittance entries.
    actual = np.sum(cluster.admittance_matrix(frequencies), axis=(1, 2))

    np.testing.assert_allclose(actual, direct.admittance(frequencies), rtol=1e-13)


def test_shared_pad_multiport_matches_explicit_two_supernode_reduction() -> None:
    frequencies = np.asarray([1.0e6, 2.0e7])
    via_a = _constant("VA", 0.02 + 0.04j)
    via_b = _constant("VB", 0.03 + 0.08j)
    via_c = _constant("VC", 0.01 + 0.06j)
    cap_a = _constant("CA", 0.01 - 0.20j)
    cap_b = _constant("CB", 0.02 - 0.45j)
    cluster = SharedPadClusterModel(
        "CLUSTER", (via_a, via_b), (via_b, via_c), (cap_a, cap_b)
    )

    actual = cluster.admittance_matrix(frequencies)
    for index, frequency in enumerate(frequencies):
        y_power = np.asarray(
            [
                2.0 / via_a.impedance([frequency])[0],
                2.0 / via_b.impedance([frequency])[0],
            ]
        )
        y_ground = np.asarray(
            [
                2.0 / via_b.impedance([frequency])[0],
                2.0 / via_c.impedance([frequency])[0],
            ]
        )
        y_cap = (
            1.0 / cap_a.impedance([frequency])[0]
            + 1.0 / cap_b.impedance([frequency])[0]
        )
        # Retained order is P1,P2,G1,G2 followed by internal top-P/top-G.
        full = np.zeros((6, 6), dtype=np.complex128)
        full[:2, :2] = np.diag(y_power)
        full[2:4, 2:4] = np.diag(y_ground)
        full[:2, 4] = -y_power
        full[4, :2] = -y_power
        full[2:4, 5] = -y_ground
        full[5, 2:4] = -y_ground
        full[4:, 4:] = np.asarray(
            [
                [np.sum(y_power) + y_cap, -y_cap],
                [-y_cap, np.sum(y_ground) + y_cap],
            ]
        )
        raw = full[:4, :4] - full[:4, 4:] @ np.linalg.solve(
            full[4:, 4:], full[4:, :4]
        )
        transform = np.diag([0.5, 0.5, -0.5, -0.5])
        expected = transform @ raw @ transform
        np.testing.assert_allclose(actual[index], expected, rtol=1e-13)
        np.testing.assert_allclose(actual[index], actual[index].T, rtol=1e-13)


def test_shared_pad_multiple_power_components_match_full_nodal_kron() -> None:
    frequencies = np.asarray([1.0e6, 2.0e7])
    power_loops = (
        _constant("P1", 0.020 + 0.040j),
        _constant("P2", 0.035 + 0.065j),
        _constant("P3", 0.018 + 0.055j),
    )
    ground_loops = (
        _constant("G1", 0.025 + 0.050j),
        _constant("G2", 0.030 + 0.075j),
    )
    capacitors = (
        _constant("C1", 0.010 - 0.20j),
        _constant("C2", 0.015 - 0.35j),
        _constant("C3", 0.020 - 0.55j),
    )
    power_components = (0, 0, 1)
    capacitor_components = (0, 1, 1)
    cluster = SharedPadClusterModel(
        "SPLIT-PWR-COMMON-GND",
        power_loops,
        ground_loops,
        capacitors,
        power_component_indices=power_components,
        capacitor_component_indices=capacitor_components,
    )

    actual = cluster.admittance_matrix(frequencies)
    retained_count = len(power_loops) + len(ground_loops)
    # Internal order is P-component 0, P-component 1, common GND.
    internal_offset = retained_count
    for frequency_index, frequency in enumerate(frequencies):
        full = np.zeros((retained_count + 3, retained_count + 3), dtype=np.complex128)

        def stamp_branch(first: int, second: int, admittance: complex) -> None:
            full[first, first] += admittance
            full[second, second] += admittance
            full[first, second] -= admittance
            full[second, first] -= admittance

        for path_index, (loop, component_index) in enumerate(
            zip(power_loops, power_components, strict=True)
        ):
            stamp_branch(
                path_index,
                internal_offset + component_index,
                2.0 / loop.impedance([frequency])[0],
            )
        for path_index, loop in enumerate(ground_loops):
            stamp_branch(
                len(power_loops) + path_index,
                internal_offset + 2,
                2.0 / loop.impedance([frequency])[0],
            )
        for capacitor, component_index in zip(
            capacitors, capacitor_components, strict=True
        ):
            stamp_branch(
                internal_offset + component_index,
                internal_offset + 2,
                1.0 / capacitor.impedance([frequency])[0],
            )

        retained = full[:retained_count, :retained_count]
        coupling = full[:retained_count, retained_count:]
        internal = full[retained_count:, retained_count:]
        raw = retained - coupling @ np.linalg.solve(internal, coupling.T)
        transform = np.diag([0.5] * len(power_loops) + [-0.5] * len(ground_loops))
        expected = transform @ raw @ transform

        np.testing.assert_allclose(actual[frequency_index], expected, rtol=1e-12)
        np.testing.assert_allclose(
            actual[frequency_index], actual[frequency_index].T, rtol=1e-13
        )


def test_shared_pad_multiple_ground_components_match_full_nodal_kron() -> None:
    frequencies = np.asarray([1.0e6, 2.0e7])
    power_loops = (
        _constant("P1", 0.020 + 0.040j),
        _constant("P2", 0.035 + 0.065j),
    )
    ground_loops = (
        _constant("G1", 0.025 + 0.050j),
        _constant("G2", 0.030 + 0.075j),
    )
    capacitors = (
        _constant("C1", 0.010 - 0.20j),
        _constant("C2", 0.015 - 0.35j),
    )
    power_components = (0, 1)
    ground_components = (0, 1)
    # Cross-map the two physical top-PWR/GND buses: this is the topology that
    # cannot be reduced with the historic common-GND arrowhead algebra.
    capacitor_power_components = (0, 1)
    capacitor_ground_components = (1, 0)
    cluster = SharedPadClusterModel(
        "SPLIT-PWR-SPLIT-GND",
        power_loops,
        ground_loops,
        capacitors,
        power_component_indices=power_components,
        capacitor_component_indices=capacitor_power_components,
        ground_component_indices=ground_components,
        capacitor_ground_component_indices=capacitor_ground_components,
    )

    actual = cluster.admittance_matrix(frequencies)
    retained_count = len(power_loops) + len(ground_loops)
    power_node_offset = retained_count
    ground_node_offset = power_node_offset + 2
    for frequency_index, frequency in enumerate(frequencies):
        full = np.zeros((retained_count + 4, retained_count + 4), dtype=np.complex128)

        def stamp_branch(first: int, second: int, admittance: complex) -> None:
            full[first, first] += admittance
            full[second, second] += admittance
            full[first, second] -= admittance
            full[second, first] -= admittance

        for path_index, (loop, component_index) in enumerate(
            zip(power_loops, power_components, strict=True)
        ):
            stamp_branch(
                path_index,
                power_node_offset + component_index,
                2.0 / loop.impedance([frequency])[0],
            )
        for path_index, (loop, component_index) in enumerate(
            zip(ground_loops, ground_components, strict=True)
        ):
            stamp_branch(
                len(power_loops) + path_index,
                ground_node_offset + component_index,
                2.0 / loop.impedance([frequency])[0],
            )
        for capacitor, power_component, ground_component in zip(
            capacitors,
            capacitor_power_components,
            capacitor_ground_components,
            strict=True,
        ):
            stamp_branch(
                power_node_offset + power_component,
                ground_node_offset + ground_component,
                1.0 / capacitor.impedance([frequency])[0],
            )
        retained = full[:retained_count, :retained_count]
        coupling = full[:retained_count, retained_count:]
        internal = full[retained_count:, retained_count:]
        raw = retained - coupling @ np.linalg.solve(internal, coupling.T)
        transform = np.diag([0.5] * len(power_loops) + [-0.5] * len(ground_loops))
        expected = transform @ raw @ transform
        np.testing.assert_allclose(actual[frequency_index], expected, rtol=1e-12)
        np.testing.assert_allclose(
            actual[frequency_index], actual[frequency_index].T, rtol=1e-13
        )

    # Explicit one-GND IDs intentionally preserve the V4 arrowhead result.
    legacy = SharedPadClusterModel(
        "LEGACY-GND",
        power_loops,
        ground_loops,
        capacitors,
        power_component_indices=power_components,
        capacitor_component_indices=capacitor_power_components,
    )
    explicit_one_ground = SharedPadClusterModel(
        "EXPLICIT-ONE-GND",
        power_loops,
        ground_loops,
        capacitors,
        power_component_indices=power_components,
        capacitor_component_indices=capacitor_power_components,
        ground_component_indices=(0, 0),
        capacitor_ground_component_indices=(0, 0),
    )
    np.testing.assert_allclose(
        legacy.admittance_matrix(frequencies),
        explicit_one_ground.admittance_matrix(frequencies),
        rtol=1e-13,
    )


def test_shared_pad_multiple_ground_components_singular_block_fails_closed() -> None:
    # The PWR/GND-0 internal submatrix is singular; the independent GND-1
    # terminal ensures this exercises the explicit multi-GND Kron path.
    cluster = SharedPadClusterModel(
        "SPLIT-GND-SINGULAR",
        (_constant("P", 1.0j),),
        (_constant("G0", 1.0j), _constant("G1", 1.0j)),
        (_constant("C", -1.0j),),
        ground_component_indices=(0, 1),
        capacitor_ground_component_indices=(0,),
    )

    with pytest.raises(CircuitModelError, match="internal admittance block is singular"):
        cluster.admittance_matrix([1.0e6])


def test_shared_pad_120_port_smoke_is_finite_and_memory_bounded() -> None:
    frequencies = np.geomspace(1.0e5, 1.0e9, 17)
    via = _constant("VIA", 0.01 + 0.04j)
    cap = _constant("CAP", 0.02 - 0.30j)
    power_component_indices = tuple(index // 20 for index in range(60))
    capacitor_component_indices = tuple(index // 20 for index in range(60))
    cluster = SharedPadClusterModel(
        "DENSE-120-PORT",
        (via,) * 60,
        (via,) * 60,
        (cap,) * 60,
        power_component_indices=power_component_indices,
        capacitor_component_indices=capacitor_component_indices,
    )

    matrix = cluster.admittance_matrix(frequencies)

    assert matrix.shape == (17, 120, 120)
    assert matrix.nbytes == 17 * 120 * 120 * np.dtype(np.complex128).itemsize
    assert matrix.nbytes < 4_000_000
    assert np.all(np.isfinite(matrix))
    np.testing.assert_allclose(matrix, np.swapaxes(matrix, 1, 2), rtol=1e-13)


def test_shared_pad_zero_enabled_caps_has_correct_limit() -> None:
    frequencies = np.asarray([1.0e6])
    via_a = _constant("VA", 0.02 + 0.04j)
    single = SharedPadClusterModel("ONE", (via_a,), (via_a,), ())
    np.testing.assert_allclose(single.admittance_matrix(frequencies), 0.0, atol=1e-14)

    via_b = _constant("VB", 0.03 + 0.08j)
    coupled = SharedPadClusterModel("TWO", (via_a, via_b), (via_a,), ())
    matrix = coupled.admittance_matrix(frequencies)[0]
    assert np.linalg.norm(matrix) > 0.0
    np.testing.assert_allclose(matrix @ np.ones(3), 0.0, atol=1e-13)


def test_shared_pad_ground_via_multiplicity_changes_admittance() -> None:
    frequencies = np.asarray([1.0e6])
    via = _constant("VIA", 0.01 + 0.03j)
    cap = _constant("CAP", 0.02 - 0.2j)
    one_ground = SharedPadClusterModel("G1", (via,), (via,), (cap,))
    three_ground = SharedPadClusterModel(
        "G3", (via,), (via, via, via), (cap,)
    )

    one_effective = np.sum(one_ground.admittance_matrix(frequencies)[0])
    three_effective = np.sum(three_ground.admittance_matrix(frequencies)[0])

    assert not np.isclose(one_effective, three_effective)


def test_shared_pad_singular_common_node_fails_closed() -> None:
    # y_via = -j and y_cap = +j make the common-node block singular.
    cluster = SharedPadClusterModel(
        "SINGULAR",
        (_constant("VIA", 1.0j),),
        (_constant("VIA-G", 1.0j),),
        (_constant("CAP", -1.0j),),
    )

    with pytest.raises(CircuitModelError, match="admittance is singular"):
        cluster.admittance_matrix([1.0e6])


def _plane_solver_fixture() -> tuple[
    RectangularCavitySolver, DeviceConnection, np.ndarray
]:
    plane = RectangularPlane(
        width_m=0.02,
        height_m=0.015,
        separation_m=100.0e-6,
        relative_permittivity=3.4,
        loss_tangent=0.005,
    )
    solver = RectangularCavitySolver(plane, max_mode_x=2, max_mode_y=2)
    device = DeviceConnection(
        (
            DeviceBranch(
                "DUT",
                FinitePort(0.010, 0.0075, 100.0e-6, 100.0e-6),
                _constant("DUT_PATH", 0.01 + 0.02j),
            ),
        )
    )
    return solver, device, np.geomspace(1.0e5, 1.0e8, 13)


def _shared_pwr_modal_expected(
    solver: RectangularCavitySolver, frequencies: np.ndarray
) -> np.ndarray:
    shunt_admittance = sum(
        solver.component_shunt_admittances(frequencies),
        start=np.zeros(frequencies.shape, dtype=np.complex128),
    )
    return_admittance = sum(
        (1.0 / values for values in solver.component_return_sheet_impedances(frequencies)),
        start=np.zeros(frequencies.shape, dtype=np.complex128),
    )
    sheet_impedance = solver.plane.power_sheet_resistance_ohm + 1.0 / return_admittance
    denominator = solver._wave_numbers_squared[None, :] - (
        -sheet_impedance * shunt_admittance
    )[:, None]
    expected = sheet_impedance[:, None] / solver.plane.area_m2 / denominator
    expected[:, solver.mode_index(0, 0)] = 1.0 / (
        solver.plane.area_m2 * shunt_admittance
    )
    return expected


def test_one_parallel_component_preserves_single_plane_modal_impedance() -> None:
    solver, _device, frequencies = _plane_solver_fixture()

    direct = solver.modal_impedance(frequencies)
    explicit = solver._component_modal_impedance(frequencies, solver.plane)

    np.testing.assert_array_equal(direct, explicit)


def test_rectangular_plane_legacy_and_split_conductivity_sheet_resistance() -> None:
    legacy = RectangularPlane(
        width_m=0.02,
        height_m=0.015,
        separation_m=100.0e-6,
        relative_permittivity=3.4,
        conductivity_s_per_m=4.0e7,
        power_thickness_m=20.0e-6,
        ground_thickness_m=30.0e-6,
    )
    split = RectangularPlane(
        width_m=legacy.width_m,
        height_m=legacy.height_m,
        separation_m=legacy.separation_m,
        relative_permittivity=legacy.relative_permittivity,
        conductivity_s_per_m=9.0e7,
        power_thickness_m=legacy.power_thickness_m,
        ground_thickness_m=legacy.ground_thickness_m,
        power_conductivity_s_per_m=5.0e7,
        ground_conductivity_s_per_m=6.0e7,
    )

    assert legacy.effective_power_conductivity_s_per_m == 4.0e7
    assert legacy.effective_ground_conductivity_s_per_m == 4.0e7
    assert legacy.sheet_resistance_ohm == pytest.approx(
        1.0 / (4.0e7 * 20.0e-6) + 1.0 / (4.0e7 * 30.0e-6)
    )
    assert split.power_sheet_resistance_ohm == pytest.approx(1.0 / (5.0e7 * 20.0e-6))
    assert split.ground_sheet_resistance_ohm == pytest.approx(1.0 / (6.0e7 * 30.0e-6))


def test_shared_pwr_rejects_mismatched_power_layer_properties() -> None:
    solver, _device, _frequencies = _plane_solver_fixture()
    mismatched_power = RectangularPlane(
        width_m=solver.plane.width_m,
        height_m=solver.plane.height_m,
        separation_m=solver.plane.separation_m,
        relative_permittivity=solver.plane.relative_permittivity,
        loss_tangent=solver.plane.loss_tangent,
        conductivity_s_per_m=solver.plane.conductivity_s_per_m,
        power_thickness_m=2.0 * solver.plane.power_thickness_m,
        ground_thickness_m=solver.plane.ground_thickness_m,
        power_conductivity_s_per_m=solver.plane.conductivity_s_per_m / 2.0,
    )
    assert mismatched_power.power_sheet_resistance_ohm == pytest.approx(
        solver.plane.power_sheet_resistance_ohm
    )

    with pytest.raises(ModalSolverError, match="same power thickness and conductivity"):
        RectangularCavitySolver(solver.plane, parallel_planes=(mismatched_power,))


def test_identical_parallel_components_keep_one_finite_power_sheet() -> None:
    solver, _device, frequencies = _plane_solver_fixture()
    parallel = RectangularCavitySolver(
        solver.plane,
        parallel_planes=(solver.plane,),
        max_mode_x=2,
        max_mode_y=2,
    )

    combined = parallel.modal_impedance(frequencies)

    np.testing.assert_allclose(combined, _shared_pwr_modal_expected(parallel, frequencies), rtol=1e-14)
    assert not np.allclose(combined, solver.modal_impedance(frequencies) / 2.0, rtol=1e-5)


def test_identical_parallel_components_approach_half_with_zero_power_sheet() -> None:
    solver, _device, frequencies = _plane_solver_fixture()
    nearly_ideal_power = RectangularPlane(
        width_m=solver.plane.width_m,
        height_m=solver.plane.height_m,
        separation_m=solver.plane.separation_m,
        relative_permittivity=solver.plane.relative_permittivity,
        loss_tangent=solver.plane.loss_tangent,
        conductivity_s_per_m=solver.plane.conductivity_s_per_m,
        power_thickness_m=solver.plane.power_thickness_m,
        ground_thickness_m=solver.plane.ground_thickness_m,
        power_conductivity_s_per_m=1.0e30,
        ground_conductivity_s_per_m=solver.plane.conductivity_s_per_m,
    )
    single = RectangularCavitySolver(nearly_ideal_power, max_mode_x=2, max_mode_y=2)
    parallel = RectangularCavitySolver(
        nearly_ideal_power,
        parallel_planes=(nearly_ideal_power,),
        max_mode_x=2,
        max_mode_y=2,
    )

    np.testing.assert_allclose(
        parallel.modal_impedance(frequencies),
        single.modal_impedance(frequencies) / 2.0,
        rtol=1.0e-12,
        atol=1.0e-18,
    )


def test_asymmetric_parallel_components_sum_modal_admittances() -> None:
    solver, _device, frequencies = _plane_solver_fixture()
    second = RectangularPlane(
        width_m=solver.plane.width_m,
        height_m=solver.plane.height_m,
        separation_m=65.0e-6,
        relative_permittivity=4.1,
        loss_tangent=0.012,
        conductivity_s_per_m=4.2e7,
        power_thickness_m=solver.plane.power_thickness_m,
        ground_thickness_m=25.0e-6,
        power_conductivity_s_per_m=solver.plane.conductivity_s_per_m,
        ground_conductivity_s_per_m=4.2e7,
    )
    parallel = RectangularCavitySolver(
        solver.plane,
        parallel_planes=(second,),
        max_mode_x=2,
        max_mode_y=2,
    )

    np.testing.assert_allclose(
        parallel.modal_impedance(frequencies),
        _shared_pwr_modal_expected(parallel, frequencies),
        rtol=1e-14,
    )


def test_parallel_component_confidence_uses_the_lowest_vertical_cutoff() -> None:
    solver, _device, _frequencies = _plane_solver_fixture()
    lower_cutoff = RectangularPlane(
        width_m=solver.plane.width_m,
        height_m=solver.plane.height_m,
        separation_m=10.0e-3,
        relative_permittivity=4.0,
        power_thickness_m=solver.plane.power_thickness_m,
        ground_thickness_m=25.0e-6,
    )
    frequencies = np.asarray([1.0e8, 1.0e10])
    diagnostics = SolverDiagnostics(
        condition_numbers=np.ones(frequencies.size),
        relative_residuals=np.full(frequencies.size, 1.0e-12),
        mode_count=1,
    )
    inputs = ConfidenceInputs(
        model_valid_min_hz=1.0e5,
        model_valid_max_hz=1.0e12,
        model_validity_known=True,
        cavity_cutoff_safety_factor=1.0,
    )

    one_plane = assess_confidence(frequencies, diagnostics, solver.plane, inputs)
    shared_pwr = assess_confidence(
        frequencies,
        diagnostics,
        solver.plane,
        inputs,
        parallel_planes=(lower_cutoff,),
    )
    coverage = lambda values: next(
        item.level for item in values if item.category == ConfidenceCategory.MODEL_COVERAGE
    )
    assert coverage(one_plane) == ConfidenceLevel.HIGH
    assert coverage(shared_pwr) == ConfidenceLevel.MEDIUM


def test_kernel_and_sensitivity_use_parallel_components() -> None:
    solver, device, frequencies = _plane_solver_fixture()
    request = EvaluationRequest(
        rail_id="R1",
        frequencies_hz=frequencies,
        plane=solver.plane,
        parallel_planes=(solver.plane,),
        device=device,
        shunts=(),
        target=TargetMask.constant(1.0e-9, start_hz=frequencies[0], stop_hz=frequencies[-1]),
    )
    kernel = compile_evaluation_kernel(request)
    single_plane_request = EvaluationRequest(
        rail_id="R1",
        frequencies_hz=frequencies,
        plane=solver.plane,
        device=device,
        shunts=(),
        target=request.target,
    )

    with pytest.raises(EvaluationError, match="does not match"):
        from spd_decap_pi._core.solver.evaluator import evaluate_rail

        evaluate_rail(single_plane_request, kernel=kernel)

    sensitivity = evaluate_shunt_sensitivity(request)
    expected = RectangularCavitySolver(
        solver.plane,
        parallel_planes=(solver.plane,),
        max_mode_x=request.max_mode_x,
        max_mode_y=request.max_mode_y,
    ).solve_device(frequencies, device)
    np.testing.assert_allclose(
        sensitivity.baseline_max_violation_db,
        20.0 * np.log10(np.max(np.abs(expected.impedance_ohm)) / 1.0e-9),
    )


def test_adjacent_ground_sandwich_is_auto_detected_but_power_neighbor_is_not() -> None:
    project = _cluster_project()
    payload = project.model_dump(mode="python")
    payload["stackup_layers"] = [
        StackupLayer(
            name="L08_DGND",
            thickness_um=20.0,
            conductivity_s_m=4.5e7,
            pwr_nets=["DGND"],
        ),
        StackupLayer(name="D08", thickness_um=30.0, dk=3.7, df=0.012),
        StackupLayer(
            name="PWR",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["VDD"],
        ),
        StackupLayer(name="D10", thickness_um=30.0, dk=4.0, df=0.006),
        StackupLayer(
            name="GND",
            thickness_um=25.0,
            conductivity_s_m=5.2e7,
            pwr_nets=["DGND"],
        ),
    ]
    sandwich = ProjectSpec.model_validate(payload)
    rail = sandwich.rails[0]

    primary, parallel, _origin, _confirmed, assumptions = _planes_from_project(
        sandwich, rail
    )

    assert primary.separation_m == pytest.approx(30.0e-6)
    assert len(parallel) == 1
    assert parallel[0].relative_permittivity == pytest.approx(3.7)
    assert any("PWR/GND" in text and "PWR/L08_DGND" in text for text in assumptions)
    assert any("shared-PWR" in text for text in assumptions)
    assert any("ideal common reference" in text for text in assumptions)

    payload["stackup_layers"][0] = StackupLayer(
        name="L08_PWR",
        thickness_um=20.0,
        conductivity_s_m=4.5e7,
        pwr_nets=["VCPU"],
    )
    one_sided = ProjectSpec.model_validate(payload)
    _primary, parallel, _origin, _confirmed, assumptions = _planes_from_project(
        one_sided, one_sided.rails[0]
    )
    assert parallel == ()
    assert assumptions == ()


def _homogeneous_cluster_groups(
    *,
    homogeneous: bool,
    seed: int = 7,
) -> tuple[CoupledShuntGroup, ...]:
    """Create numerically identical eligible and dense-fallback clusters."""

    rng = np.random.default_rng(seed)
    shared_via = _constant("SHARED-VIA", 0.012 + 0.035j)
    groups: list[CoupledShuntGroup] = []
    for cluster_index in range(4):
        power_count = int(rng.integers(1, 4))
        ground_count = int(rng.integers(1, 4))
        if homogeneous:
            power = tuple(shared_via for _ in range(power_count))
            ground = tuple(shared_via for _ in range(ground_count))
        else:
            # Equal impedance values but distinct objects must deliberately
            # take the established dense path.
            power = tuple(
                _constant(f"P-{cluster_index}-{index}", 0.012 + 0.035j)
                for index in range(power_count)
            )
            ground = tuple(
                _constant(f"G-{cluster_index}-{index}", 0.012 + 0.035j)
                for index in range(ground_count)
            )
        capacitors = tuple(
            _constant(f"C-{cluster_index}-{index}", 0.006 - 0.25j)
            for index in range(int(rng.integers(1, 4)))
        )
        network = SharedPadClusterModel(
            f"CLUSTER-{cluster_index}", power, ground, capacitors
        )
        ports = tuple(
            FinitePort(
                float(rng.uniform(0.001, 0.019)),
                float(rng.uniform(0.001, 0.014)),
                100.0e-6,
                100.0e-6,
                f"{cluster_index}-{port_index}",
            )
            for port_index in range(power_count + ground_count)
        )
        groups.append(CoupledShuntGroup(f"CLUSTER-{cluster_index}", ports, network))
    return tuple(groups)


def test_homogeneous_shared_pad_batch_matches_dense_curve_randomized() -> None:
    solver, device, frequencies = _plane_solver_fixture()
    batched_groups = _homogeneous_cluster_groups(homogeneous=True)
    dense_groups = _homogeneous_cluster_groups(homogeneous=False)

    batch_data = solver._shunt_data(frequencies, batched_groups)
    dense_data = solver._shunt_data(frequencies, dense_groups)
    assert len(batch_data) == 1
    assert type(batch_data[0]).__name__ == "_HomogeneousSharedPadBatchData"
    assert all(type(item).__name__ == "_CoupledShuntData" for item in dense_data)

    batched = solver.solve_device(frequencies, device, shunts=batched_groups)
    dense = solver.solve_device(frequencies, device, shunts=dense_groups)
    np.testing.assert_allclose(
        batched.impedance_ohm,
        dense.impedance_ohm,
        rtol=3.0e-12,
        atol=3.0e-12,
    )


def test_homogeneous_shared_pad_batches_partition_a_mixed_shunt_solve_exactly() -> None:
    solver, _device, frequencies = _plane_solver_fixture()
    common_groups = _homogeneous_cluster_groups(homogeneous=True)[:2]

    second_via = _constant("SECOND-VIA", 0.019 + 0.052j)
    second_network = SharedPadClusterModel(
        "SECOND-BATCH",
        (second_via, second_via),
        (second_via,),
        (_constant("SECOND-CAP", 0.011 - 0.41j),),
    )
    second_group = CoupledShuntGroup(
        "SECOND-BATCH",
        (
            FinitePort(0.0120, 0.0040, 100.0e-6, 100.0e-6, "B-P1"),
            FinitePort(0.0128, 0.0040, 100.0e-6, 100.0e-6, "B-P2"),
            FinitePort(0.0124, 0.0048, 100.0e-6, 100.0e-6, "B-G1"),
        ),
        second_network,
    )

    mixed_power_via = _constant("MIXED-P", 0.014 + 0.041j)
    mixed_ground_via = _constant("MIXED-G", 0.021 + 0.066j)
    mixed_group = CoupledShuntGroup(
        "MIXED-VIA",
        (
            FinitePort(0.0030, 0.0110, 100.0e-6, 100.0e-6, "M-P"),
            FinitePort(0.0038, 0.0110, 100.0e-6, 100.0e-6, "M-G"),
        ),
        SharedPadClusterModel(
            "MIXED-VIA",
            (mixed_power_via,),
            (mixed_ground_via,),
            (_constant("MIXED-CAP", 0.009 - 0.31j),),
        ),
    )

    split_via = _constant("SPLIT-VIA", 0.017 + 0.048j)
    split_group = CoupledShuntGroup(
        "SPLIT-PWR",
        (
            FinitePort(0.0150, 0.0100, 100.0e-6, 100.0e-6, "S-P1"),
            FinitePort(0.0158, 0.0100, 100.0e-6, 100.0e-6, "S-P2"),
            FinitePort(0.0154, 0.0108, 100.0e-6, 100.0e-6, "S-G"),
        ),
        SharedPadClusterModel(
            "SPLIT-PWR",
            (split_via, split_via),
            (split_via,),
            (
                _constant("SPLIT-C1", 0.008 - 0.22j),
                _constant("SPLIT-C2", 0.010 - 0.37j),
            ),
            power_component_indices=(0, 1),
            capacitor_component_indices=(0, 1),
        ),
    )
    scalar_group = ShuntGroup(
        "DIRECT",
        (FinitePort(0.0090, 0.0030, 100.0e-6, 100.0e-6, "DIRECT"),),
        _constant("DIRECT", 0.025 - 0.18j),
    )
    shunts = (
        common_groups[0],
        scalar_group,
        mixed_group,
        common_groups[1],
        split_group,
        second_group,
    )

    data = solver._shunt_data(frequencies, shunts)
    names = [type(item).__name__ for item in data]
    assert names.count("_HomogeneousSharedPadBatchData") == 2
    assert names.count("_CoupledShuntData") == 2
    assert names.count("_ScalarShuntData") == 1

    expected = np.zeros(
        (frequencies.size, solver.mode_count, solver.mode_count),
        dtype=np.complex128,
    )
    for group in shunts:
        population = solver.population_matrix(group)
        if isinstance(group, CoupledShuntGroup):
            expected += np.einsum(
                "mp,fpq,nq->fmn",
                population,
                group.network.admittance_matrix(frequencies),
                population,
                optimize=True,
            )
        else:
            expected += (
                1.0 / group.network.impedance(frequencies)
            )[:, None, None] * (population @ population.T)[None, :, :]

    zero_plane = np.zeros(
        (frequencies.size, solver.mode_count), dtype=np.complex128
    )
    actual = np.stack(
        [
            solver._base_matrix(index, zero_plane, data)
            for index in range(frequencies.size)
        ]
    )
    np.testing.assert_allclose(actual, expected, rtol=5.0e-12, atol=5.0e-12)


def test_homogeneous_shared_pad_batch_avoids_full_frequency_mode_group_temp() -> None:
    base_solver, _device, _frequencies = _plane_solver_fixture()
    solver = RectangularCavitySolver(
        base_solver.plane, max_mode_x=6, max_mode_y=6
    )
    frequencies = np.geomspace(1.0e5, 1.0e8, 29)
    groups = _homogeneous_cluster_groups(homogeneous=True) * 128

    gc.collect()
    tracemalloc.start()
    try:
        data = solver._shunt_data(frequencies, groups)
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(data) == 1
    assert type(data[0]).__name__ == "_HomogeneousSharedPadBatchData"
    forbidden_temporary_bytes = (
        frequencies.size
        * solver.mode_count
        * len(groups)
        * np.dtype(np.complex128).itemsize
    )
    # A full F x M x G weighted population alone would exceed this bound;
    # retained F x M x M stamp storage and bounded M x G work arrays do not.
    assert peak_bytes < 0.75 * forbidden_temporary_bytes


class _MutableImpedance:
    def __init__(self, value: complex) -> None:
        self.value = value

    def impedance(self, frequencies_hz: object) -> np.ndarray:
        frequencies = np.asarray(frequencies_hz, dtype=np.float64)
        return np.full(frequencies.shape, self.value, dtype=np.complex128)


def test_homogeneous_shared_pad_batch_is_rebuilt_after_model_mutation() -> None:
    solver, _device, frequencies = _plane_solver_fixture()
    via = _MutableImpedance(0.012 + 0.035j)
    capacitor = _MutableImpedance(0.006 - 0.25j)
    ports = (
        FinitePort(0.006, 0.006, 100.0e-6, 100.0e-6, "P1"),
        FinitePort(0.007, 0.006, 100.0e-6, 100.0e-6, "P2"),
        FinitePort(0.0065, 0.007, 100.0e-6, 100.0e-6, "G1"),
    )
    group = CoupledShuntGroup(
        "MUTABLE",
        ports,
        SharedPadClusterModel("MUTABLE", (via, via), (via,), (capacitor,)),
    )

    first = solver._shunt_data(frequencies, (group,))[0]
    first_stamp = first.stamp.copy()  # type: ignore[attr-defined]
    retained_stamp = weakref.ref(first.stamp)  # type: ignore[attr-defined]

    via.value = 0.019 + 0.052j
    capacitor.value = 0.011 - 0.41j
    second = solver._shunt_data(frequencies.copy(), (group,))[0]
    assert not np.allclose(second.stamp, first_stamp)  # type: ignore[attr-defined]

    dense_network = SharedPadClusterModel(
        "DENSE",
        (
            _constant("DP1", via.value),
            _constant("DP2", via.value),
        ),
        (_constant("DG1", via.value),),
        (_constant("DC1", capacitor.value),),
    )
    dense_group = CoupledShuntGroup("DENSE", ports, dense_network)
    dense = solver._shunt_data(frequencies, (dense_group,))[0]
    population = dense.population  # type: ignore[attr-defined]
    expected = np.einsum(
        "mp,fpq,nq->fmn",
        population,
        dense.admittance,  # type: ignore[attr-defined]
        population,
        optimize=True,
    )
    np.testing.assert_allclose(
        second.stamp,  # type: ignore[attr-defined]
        expected,
        rtol=3.0e-12,
        atol=3.0e-12,
    )

    # The solver retains no frequency x mode x mode stamps between requests.
    del first
    gc.collect()
    assert retained_stamp() is None
    assert not hasattr(solver, "_homogeneous_batch_cache")


def test_shared_pair_missing_partner_topology_fails_with_evaluation_error() -> None:
    via_template = ViaLoopTemplate(
        template_id="VIA",
        pwr_reference_layer="PWR",
        gnd_reference_layer="GND",
        path_kind=ViaPathKind.DIRECT,
        finite_port_width_um=100.0,
        finite_port_height_um=100.0,
        loop_resistance_ohm=0.01,
        loop_inductance_h=0.1e-9,
    )
    topologies = {
        "A": TopologyMap(
            slot_id="A",
            x_um=5_000.0,
            y_um=5_000.0,
            allowed_rail_ids=["R1"],
            topology=TopologyKind.SHARED_PAIR,
            via_template_id="VIA",
            horizontal_template_id="HORIZONTAL",
            satellite_slot_id="B",
        )
    }
    placements = {
        slot_id: PlacementAssignment(
            slot_id=slot_id,
            topology=TopologyKind.SHARED_PAIR,
            rail_id="R1",
            cap_model_id="CAP",
        )
        for slot_id in ("A", "B")
    }

    with pytest.raises(EvaluationError, match="references an unknown topology slot"):
        _placement_shunts(
            "R1",
            "PWR",
            "GND",
            RectangularPlane(
                width_m=0.02,
                height_m=0.015,
                separation_m=100.0e-6,
                relative_permittivity=3.4,
                loss_tangent=0.005,
            ),
            (0.0, 0.0),
            placements,
            topologies,
            {"CAP": _constant("CAP", 0.01 - 0.2j)},
            {"VIA": via_template},
            {"VIA": _constant("VIA", 0.01 + 0.03j)},
            {},
        )


def test_coupled_cluster_sensitivity_removes_one_atomic_group() -> None:
    solver, device, frequencies = _plane_solver_fixture()
    power_loops = (
        _constant("V1", 0.02 + 0.04j),
        _constant("V2", 0.03 + 0.05j),
    )
    ground_loops = (_constant("VG", 0.02 + 0.04j),)
    network = SharedPadClusterModel(
        "CLUSTER",
        power_loops,
        ground_loops,
        (_constant("C1", 0.01 - 0.30j),),
    )
    physical_network = SharedPadClusterModel(
        "CLUSTER-PHYSICAL", power_loops, ground_loops, ()
    )
    ports = (
        FinitePort(0.004, 0.004, 100.0e-6, 100.0e-6, "PATH-1"),
        FinitePort(0.016, 0.011, 100.0e-6, 100.0e-6, "PATH-2"),
        FinitePort(0.010, 0.008, 100.0e-6, 100.0e-6, "PATH-G"),
    )
    group = CoupledShuntGroup(
        "CLUSTER",
        ports,
        network,
        sensitivity_port_id("SHARED_PAD_CLUSTER", ("C1", "C2", "C3")),
        sensitivity_without_network=physical_network,
    )
    physical_group = CoupledShuntGroup(
        "CLUSTER-PHYSICAL",
        ports,
        physical_network,
        "PHYSICAL-NOT-A-CANDIDATE",
    )

    result = solver.solve_device_shunt_leave_one_out(
        frequencies, device, shunts=(group,)
    )
    baseline = solver.solve_device(frequencies, device, shunts=(group,))
    removed = solver.solve_device(frequencies, device, shunts=(physical_group,))
    physically_deleted = solver.solve_device(frequencies, device, shunts=())

    assert result.port_ids == (group.sensitivity_id,)
    assert result.unit_ids == result.port_ids
    np.testing.assert_allclose(result.baseline_impedance_ohm, baseline.impedance_ohm)
    np.testing.assert_allclose(result.without_impedance_ohm[0], removed.impedance_ohm)
    assert np.max(
        np.abs(result.without_impedance_ohm[0] - physically_deleted.impedance_ohm)
    ) > 1.0e-9


def test_physical_only_cluster_is_not_a_sensitivity_candidate() -> None:
    solver, device, frequencies = _plane_solver_fixture()
    network = SharedPadClusterModel(
        "PHYSICAL-ONLY",
        (
            _constant("VP1", 0.02 + 0.04j),
            _constant("VP2", 0.03 + 0.05j),
        ),
        (_constant("VG", 0.02 + 0.04j),),
        (),
    )
    group = CoupledShuntGroup(
        "PHYSICAL-ONLY",
        (
            FinitePort(0.004, 0.004, 100.0e-6, 100.0e-6, "PATH-1"),
            FinitePort(0.016, 0.011, 100.0e-6, 100.0e-6, "PATH-2"),
            FinitePort(0.010, 0.008, 100.0e-6, 100.0e-6, "PATH-G"),
        ),
        network,
    )

    result = solver.solve_device_shunt_leave_one_out(
        frequencies, device, shunts=(group,)
    )
    baseline = solver.solve_device(frequencies, device, shunts=(group,))

    assert result.unit_ids == ()
    assert result.without_impedance_ohm.shape == (0, frequencies.size)
    np.testing.assert_allclose(result.baseline_impedance_ohm, baseline.impedance_ohm)


def test_scalar_leave_one_out_compatibility_is_unchanged() -> None:
    solver, device, frequencies = _plane_solver_fixture()
    direct = DirectBranchModel(
        "DIRECT",
        _constant("CAP", 0.02 - 0.2j),
        _constant("VIA", 0.01 + 0.03j),
    )
    group = ShuntGroup(
        "DIRECT",
        (
            FinitePort(0.004, 0.004, 100.0e-6, 100.0e-6, "D1"),
            FinitePort(0.016, 0.011, 100.0e-6, 100.0e-6, "D2"),
        ),
        direct,
    )

    result = solver.solve_device_shunt_leave_one_out(
        frequencies, device, shunts=(group,)
    )
    first_only = ShuntGroup("DIRECT", (group.ports[1],), direct)
    expected_without_first = solver.solve_device(
        frequencies, device, shunts=(first_only,)
    )

    assert result.port_ids == ("D1", "D2")
    np.testing.assert_allclose(
        result.without_impedance_ohm[0], expected_without_first.impedance_ohm
    )


def _cluster_project() -> ProjectSpec:
    pwr = StackupLayer(
        name="PWR", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=["VDD"]
    )
    gnd = StackupLayer(
        name="GND", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=["DGND"]
    )
    rail = RailSpec(
        rail_id="R1",
        family="CORE",
        domain="D0",
        net="VDD",
        site="S0",
        pwr_layer="PWR",
        gnd_layer="GND",
    )
    via = ViaLoopTemplate(
        template_id="VIA",
        pwr_reference_layer="PWR",
        gnd_reference_layer="GND",
        path_kind=ViaPathKind.DIRECT,
        finite_port_width_um=100.0,
        finite_port_height_um=100.0,
        loop_resistance_ohm=0.01,
        loop_inductance_h=1.0e-9,
    )
    cap = CapModel(
        model_id="CAP",
        capacitance_f=1.0e-6,
        esr_ohm=0.01,
        esl_h=1.0e-9,
        footprint="0402",
        inventory=2,
        source_hash="fixture",
    )
    maps = [
        TopologyMap(
            slot_id=slot,
            x_um=x,
            y_um=1000.0,
            allowed_rail_ids=["R1"],
            allowed_footprints=["0402"],
            topology=TopologyKind.SHARED_PAD_CLUSTER,
            cluster_id="CL1",
        )
        for slot, x in (("S1", 1000.0), ("S2", 1200.0))
    ]
    return ProjectSpec(
        name="cluster",
        outline=MLOOutline(width_um=10_000.0, height_um=10_000.0),
        split_gap_um=0.0,
        stackup_layers=[pwr, gnd],
        rails=[rail],
        cap_models=[cap],
        via_templates=[via],
        topology_maps=maps,
        shared_pad_clusters=[
            SharedPadClusterSpec(
                cluster_id="CL1",
                rail_id="R1",
                member_slot_ids=["S1", "S2"],
                via_paths=[
                    SharedPadViaPath(
                        path_id="VP1",
                        terminal=TerminalKind.PWR,
                        x_um=1000.0,
                        y_um=1000.0,
                        via_template_id="VIA",
                        source_via_id="Via42",
                    ),
                    SharedPadViaPath(
                        path_id="VG1",
                        terminal=TerminalKind.GND,
                        x_um=1100.0,
                        y_um=1000.0,
                        via_template_id="VIA",
                        source_via_id="Via43",
                    ),
                ],
                power_components=[
                    SharedPadPowerComponentSpec(
                        component_id="PC1",
                        member_slot_ids=["S1", "S2"],
                        power_path_ids=["VP1"],
                    )
                ],
            )
        ],
        placements=[
            PlacementAssignment(
                slot_id="S1",
                topology=TopologyKind.SHARED_PAD_CLUSTER,
                rail_id="R1",
                cap_model_id="CAP",
            )
        ],
    )


def test_shared_pad_domain_accepts_disabled_members_without_fake_vias() -> None:
    project = _cluster_project()

    assert len(project.shared_pad_clusters) == 1
    assert project.topology_maps[1].via_template_id is None
    assert [item.slot_id for item in project.placements] == ["S1"]


def test_evaluator_builds_one_coupled_group_without_member_direct_paths() -> None:
    project = _cluster_project()
    rail = project.rails[0]
    cap_model = _constant("CAP", 0.02 - 0.2j)
    via_model = _constant("VIA", 0.01 + 0.03j)

    groups = _placement_shunts(
        rail.rail_id,
        rail.pwr_layer,
        rail.gnd_layer,
        RectangularPlane(
            width_m=0.01,
            height_m=0.01,
            separation_m=100.0e-6,
            relative_permittivity=3.4,
            loss_tangent=0.005,
        ),
        (0.0, 0.0),
        {item.slot_id: item for item in project.placements},
        {item.slot_id: item for item in project.topology_maps},
        {"CAP": cap_model},
        {"VIA": project.via_templates[0]},
        {"VIA": via_model},
        {"CL1": project.shared_pad_clusters[0]},
    )

    assert len(groups) == 1
    assert isinstance(groups[0], CoupledShuntGroup)
    assert groups[0].network.port_count == 2
    assert len(groups[0].network.power_via_loops) == 1
    assert len(groups[0].network.ground_via_loops) == 1
    assert groups[0].network.capacitors == (cap_model,)


def test_shared_pad_source_terminal_geometry_rl_and_sampled_fallback() -> None:
    payload = _cluster_project().model_dump(mode="json")
    power_path, ground_path = payload["shared_pad_clusters"][0]["via_paths"]
    power_path.update(
        {
            "x_um": 2_000.0,
            "y_um": 3_000.0,
            "landing_layer": "PWR",
            "landing_padstack": "PWR_PAD",
            "landing_pad_width_um": 180.0,
            "landing_pad_height_um": 120.0,
            "terminal_resistance_ohm": 0.004,
            "terminal_inductance_h": 0.30e-9,
            "terminal_provenance": "SOURCE_PROVEN_SEGMENT_RL",
        }
    )
    ground_path.update(
        {
            "x_um": 2_100.0,
            "y_um": 3_100.0,
            "landing_layer": "GND",
            "landing_padstack": "GND_PAD",
            "landing_pad_width_um": 140.0,
            "landing_pad_height_um": 160.0,
            "terminal_resistance_ohm": 0.006,
            "terminal_inductance_h": 0.50e-9,
            "terminal_provenance": "SOURCE_PROVEN_SEGMENT_RL",
        }
    )
    project = ProjectSpec.model_validate(payload)
    rail = project.rails[0]
    plane = RectangularPlane(
        width_m=0.01,
        height_m=0.01,
        separation_m=100.0e-6,
        relative_permittivity=3.4,
        loss_tangent=0.005,
    )
    cap_model = _constant("CAP", 0.02 - 0.2j)

    def groups(via_model):
        return _placement_shunts(
            rail.rail_id,
            rail.pwr_layer,
            rail.gnd_layer,
            plane,
            (0.0, 0.0),
            {item.slot_id: item for item in project.placements},
            {item.slot_id: item for item in project.topology_maps},
            {"CAP": cap_model},
            {"VIA": project.via_templates[0]},
            {"VIA": via_model},
            {"CL1": project.shared_pad_clusters[0]},
        )

    raw_groups = groups(_constant("VIA", 0.01 + 0.03j))
    assert len(raw_groups) == 1
    raw_group = raw_groups[0]
    assert isinstance(raw_group, CoupledShuntGroup)
    assert (raw_group.ports[0].x_m, raw_group.ports[0].y_m) == (
        2.0e-3,
        3.0e-3,
    )
    assert (raw_group.ports[0].width_m, raw_group.ports[0].height_m) == pytest.approx(
        (180.0e-6, 120.0e-6)
    )
    assert (raw_group.ports[1].width_m, raw_group.ports[1].height_m) == pytest.approx(
        (140.0e-6, 160.0e-6)
    )
    raw_network = raw_group.network
    assert isinstance(raw_network.power_via_loops[0], SeriesRLModel)
    assert isinstance(raw_network.ground_via_loops[0], SeriesRLModel)
    assert raw_network.power_via_loops[0].resistance_ohm == pytest.approx(0.008)
    assert raw_network.power_via_loops[0].inductance_h == pytest.approx(0.60e-9)
    assert raw_network.ground_via_loops[0].resistance_ohm == pytest.approx(0.012)
    assert raw_network.ground_via_loops[0].inductance_h == pytest.approx(1.00e-9)

    sampled = SampledImpedanceModel(
        "VIA_SAMPLED",
        np.asarray([1.0e6, 1.0e8]),
        np.asarray([0.01 + 0.03j, 0.02 + 0.30j]),
    )
    sampled_group = groups(sampled)[0]
    assert isinstance(sampled_group, CoupledShuntGroup)
    assert sampled_group.network.power_via_loops[0] is sampled
    assert sampled_group.network.ground_via_loops[0] is sampled
    assert sampled_group.network.homogeneous_one_component_via_model() is sampled


def test_full_solver_distinguishes_one_vs_three_ground_vias() -> None:
    one_ground = _cluster_project()
    payload = one_ground.model_dump(mode="json")
    ground = payload["shared_pad_clusters"][0]["via_paths"][1]
    for index, x_um in ((2, 1600.0), (3, 2200.0)):
        extra = dict(ground)
        extra.update(
            {
                "path_id": f"VG{index}",
                "x_um": x_um,
                "source_via_id": f"Via4{index + 2}",
            }
        )
        payload["shared_pad_clusters"][0]["via_paths"].append(extra)
    three_ground = ProjectSpec.model_validate(payload)
    plane = RectangularPlane(
        width_m=0.01,
        height_m=0.01,
        separation_m=100.0e-6,
        relative_permittivity=3.4,
        loss_tangent=0.005,
    )
    cap_model = _constant("CAP", 0.02 - 0.2j)
    via_model = _constant("VIA", 0.01 + 0.03j)

    def groups(project: ProjectSpec) -> tuple[ShuntGroup | CoupledShuntGroup, ...]:
        rail = project.rails[0]
        return _placement_shunts(
            rail.rail_id,
            rail.pwr_layer,
            rail.gnd_layer,
            plane,
            (0.0, 0.0),
            {item.slot_id: item for item in project.placements},
            {item.slot_id: item for item in project.topology_maps},
            {"CAP": cap_model},
            {"VIA": project.via_templates[0]},
            {"VIA": via_model},
            {"CL1": project.shared_pad_clusters[0]},
        )

    solver = RectangularCavitySolver(plane, max_mode_x=2, max_mode_y=2)
    device = DeviceConnection(
        (
            DeviceBranch(
                "DUT",
                FinitePort(0.005, 0.005, 100.0e-6, 100.0e-6),
                _constant("DUT_PATH", 0.01 + 0.02j),
            ),
        )
    )
    frequencies = np.geomspace(1.0e5, 1.0e8, 9)

    result_one = solver.solve_device(frequencies, device, shunts=groups(one_ground))
    result_three = solver.solve_device(
        frequencies, device, shunts=groups(three_ground)
    )

    assert not np.allclose(result_one.impedance_ohm, result_three.impedance_ohm)


def test_shared_pad_domain_rejects_nonreciprocal_member_mapping() -> None:
    payload = _cluster_project().model_dump(mode="json")
    payload["topology_maps"][1]["cluster_id"] = "OTHER"

    with pytest.raises(ValidationError, match="unknown cluster|does not reference cluster"):
        ProjectSpec.model_validate(payload)


def test_shared_pad_domain_rejects_reused_physical_via() -> None:
    payload = _cluster_project().model_dump(mode="json")
    duplicate = dict(payload["shared_pad_clusters"][0]["via_paths"][0])
    duplicate["path_id"] = "VP2"
    payload["shared_pad_clusters"][0]["via_paths"].append(duplicate)
    payload["shared_pad_clusters"][0]["power_components"][0][
        "power_path_ids"
    ].append("VP2")

    with pytest.raises(ValidationError, match="source via.*reused"):
        ProjectSpec.model_validate(payload)


@pytest.mark.parametrize("retained_terminal", [TerminalKind.PWR, TerminalKind.GND])
def test_shared_pad_domain_requires_both_via_terminals(
    retained_terminal: TerminalKind,
) -> None:
    payload = _cluster_project().model_dump(mode="json")
    paths = payload["shared_pad_clusters"][0]["via_paths"]
    payload["shared_pad_clusters"][0]["via_paths"] = [
        item for item in paths if item["terminal"] == retained_terminal.value
    ]

    with pytest.raises(ValidationError, match="requires both PWR and GND"):
        ProjectSpec.model_validate(payload)


def test_shared_pad_domain_rejects_empty_via_paths() -> None:
    payload = _cluster_project().model_dump(mode="json")
    payload["shared_pad_clusters"][0]["via_paths"] = []

    with pytest.raises(ValidationError, match="requires both PWR and GND"):
        ProjectSpec.model_validate(payload)


def test_shared_pad_sensitivity_id_is_injective_for_variable_members() -> None:
    first = sensitivity_port_id("SHARED_PAD_CLUSTER", ("A+B", "C"))
    second = sensitivity_port_id("SHARED_PAD_CLUSTER", ("A", "B+C"))

    assert first != second
