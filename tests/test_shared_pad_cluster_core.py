from __future__ import annotations

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
from spd_decap_pi._core.models.impedance import ConstantImpedanceModel
from spd_decap_pi._core.solver.evaluator import (
    EvaluationError,
    _placement_shunts,
    sensitivity_port_id,
)
from spd_decap_pi._core.solver.modal import (
    CoupledShuntGroup,
    DeviceBranch,
    DeviceConnection,
    FinitePort,
    RectangularCavitySolver,
    RectangularPlane,
    ShuntGroup,
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
