from __future__ import annotations

from dataclasses import replace
from time import perf_counter

import pytest

from spd_decap_pi._core.domain import (
    CapModel,
    MLOOutline,
    PinKind,
    PinRecord,
    ProjectSpec,
    RailSpec,
    StackupLayer,
    TerminalKind,
)
from spd_decap_pi.distribution import (
    DistributionDistanceMode,
    DistributionError,
    DistributionPlanStatus,
    apply_distribution_plan,
    compute_distribution_plan,
    distribution_csv_rows,
    distribution_inventory_table,
    distribution_present_counts,
    distribution_target_table,
    validate_distribution_targets,
)
from spd_decap_pi.scenario import (
    DecapConnectionKind,
    RailEligibility,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    ScenarioViaLanding,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadConnectionAnalysis,
    SHARED_PAD_ANALYSIS_VERSION,
    SourceIdentity,
)


def _rail(rail_id: str) -> RailSpec:
    return RailSpec(
        rail_id=rail_id,
        family="V",
        domain=rail_id,
        net=f"V{rail_id[-1]}",
        site="S0",
        pwr_layer="PWR",
        gnd_layer="GND",
    )


def _project(
    rail_ids: tuple[str, ...],
    *,
    bump_x: dict[str, float] | None = None,
) -> ProjectSpec:
    bump_x = bump_x or {}
    pins = [
        PinRecord(
            refdes=f"U-{rail_id}",
            pin="1",
            net=f"V{rail_id[-1]}",
            x_um=x_um,
            y_um=0.0,
            kind=PinKind.DEVICE_BUMP,
            terminal=TerminalKind.PWR,
        )
        for rail_id, x_um in bump_x.items()
    ]
    return ProjectSpec(
        name="Distribution fixture",
        outline=MLOOutline(width_um=10_000.0, height_um=8_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(
                name="PWR",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=[f"V{item[-1]}" for item in rail_ids],
            ),
            StackupLayer(
                name="GND",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        rails=[_rail(item) for item in rail_ids],
        pins=pins,
        cap_models=[
            CapModel(
                model_id="M1",
                capacitance_f=1e-6,
                esr_ohm=0.01,
                esl_h=0.5e-9,
                footprint="0402",
                inventory=100,
                source_hash="m1",
            )
        ],
    )


def _eligibility(rail_id: str, *, allowed: bool = True) -> RailEligibility:
    return RailEligibility(
        rail_id=rail_id,
        net=f"V{rail_id[-1]}",
        pwr_layer="PWR",
        gnd_layer="GND",
        via_template_id=f"VT-{rail_id}",
        allowed=allowed,
        reason=None if allowed else "no target PWR plane at Via landing",
    )


def _via(via_id: str, net: str, x_um: float) -> ScenarioViaLanding:
    return ScenarioViaLanding(
        via_id=via_id,
        net=net,
        endpoint_node_id=f"NODE-{via_id}",
        x_um=x_um,
        y_um=0.0,
        padstack="VIA",
    )


def _decap(
    refdes: str,
    x_um: float,
    rail_ids: tuple[str, ...],
    *,
    allowed: tuple[str, ...] | None = None,
) -> ScenarioDecap:
    allowed_keys = {item.casefold() for item in (allowed or rail_ids)}
    return ScenarioDecap(
        refdes=refdes,
        center=ScenarioPoint(x_um=x_um, y_um=0.0),
        pwr_pad=ScenarioPad(x_um=x_um - 10.0, y_um=0.0, layer="TOP"),
        gnd_pad=ScenarioPad(x_um=x_um + 10.0, y_um=0.0, layer="TOP"),
        side="TOP",
        start_layer="TOP",
        attach_layer="TopAir",
        footprint="0402",
        source_net="V1",
        current_net="V1",
        source_rail_id="R1",
        current_rail_id="R1",
        source_model_id="M1",
        model_id="M1",
        enabled=True,
        source_mounted=True,
        eligibility={
            rail_id: _eligibility(
                rail_id, allowed=rail_id.casefold() in allowed_keys
            )
            for rail_id in rail_ids
        },
    )


def _direct_scenario(
    specs: tuple[tuple[str, float, tuple[str, ...]], ...],
    *,
    rail_ids: tuple[str, ...] = ("R1", "R2", "R3"),
    bump_x: dict[str, float] | None = None,
    revision: int = 4,
) -> ScenarioSpec:
    decaps = [
        _decap(refdes, x_um, rail_ids, allowed=allowed)
        for refdes, x_um, allowed in specs
    ]
    connections = {
        item.refdes: ScenarioDecapConnection(
            refdes=item.refdes,
            kind=DecapConnectionKind.DIRECT,
            power_vias=(_via(f"VP-{item.refdes}", "V1", item.x_um),),
            ground_vias=(_via(f"VG-{item.refdes}", "DGND", item.x_um + 5.0),),
        )
        for item in decaps
    }
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixture/distribution.spd",
            name="distribution.spd",
            size_bytes=100,
            sha256="d" * 64,
        ),
        normalized_project=_project(
            rail_ids,
            bump_x=bump_x
            if bump_x is not None
            else {rail_id: index * 100.0 for index, rail_id in enumerate(rail_ids)},
        ),
        decaps=decaps,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="d" * 64,
            connections=connections,
            clusters=(),
        ),
        revision=revision,
    )


def _with_initial_rails(
    scenario: ScenarioSpec,
    rail_by_refdes: dict[str, str],
) -> ScenarioSpec:
    decaps = []
    for decap in scenario.decaps:
        rail_id = rail_by_refdes.get(decap.refdes, decap.current_rail_id)
        net = f"V{rail_id[-1]}"
        decaps.append(
            decap.model_copy(
                update={
                    "source_net": net,
                    "current_net": net,
                    "source_rail_id": rail_id,
                    "current_rail_id": rail_id,
                }
            )
        )
    return scenario.model_copy(update={"decaps": decaps})


def _shared_chain_scenario() -> ScenarioSpec:
    rail_ids = ("R1", "R2")
    positions = {"A0": 0.0, "D1": 100.0, "A2": 10.0}
    decaps = [_decap(refdes, x_um, rail_ids) for refdes, x_um in positions.items()]
    connections = {
        "A0": ScenarioDecapConnection(
            refdes="A0",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CHAIN",
            power_vias=(_via("VP0", "V1", 0.0),),
            ground_vias=(_via("VG0", "DGND", 5.0),),
        ),
        "D1": ScenarioDecapConnection(
            refdes="D1",
            kind=DecapConnectionKind.SHARED_DUMMY,
            cluster_id="CHAIN",
        ),
        "A2": ScenarioDecapConnection(
            refdes="A2",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CHAIN",
            power_vias=(_via("VP2", "V1", 10.0),),
        ),
    }
    cluster = SharedPadCluster(
        cluster_id="CHAIN",
        state=SharedPadClusterState.ANCHORED,
        member_refdes=("A0", "D1", "A2"),
        anchor_refdes=("A0", "A2"),
        dummy_refdes=("D1",),
        power_net="V1",
        ground_net="DGND",
        layer="TOP",
        power_edges=(("A0", "D1"), ("D1", "A2")),
        ground_edges=(("A0", "D1"), ("D1", "A2")),
        isolation_gap_refdes=("A0", "D1", "A2"),
        eligibility={"R1": _eligibility("R1"), "R2": _eligibility("R2")},
        via_eligibility={
            via_id: {"R1": _eligibility("R1"), "R2": _eligibility("R2")}
            for via_id in ("VP0", "VP2")
        },
    )
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixture/chain.spd",
            name="chain.spd",
            size_bytes=200,
            sha256="e" * 64,
        ),
        normalized_project=_project(rail_ids, bump_x={"R2": 0.0}),
        decaps=decaps,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="e" * 64,
            connections=connections,
            clusters=(cluster,),
        ),
        revision=9,
    )


def _shared_exchange_scenario() -> ScenarioSpec:
    rail_ids = ("R1", "R2", "R3")
    positions = {"A0": 0.0, "D1": 100.0, "A2": 10.0}
    cluster_decaps = [
        _decap(refdes, x_um, rail_ids).model_copy(
            update={
                "source_net": "V2",
                "current_net": "V2",
                "source_rail_id": "R2",
                "current_rail_id": "R2",
            }
        )
        for refdes, x_um in positions.items()
    ]
    donor_decaps = [
        _decap(refdes, x_um, rail_ids, allowed=("R1", "R2"))
        for refdes, x_um in (("X0", 200.0), ("X1", 210.0))
    ]
    decaps = [*cluster_decaps, *donor_decaps]
    connections = {
        "A0": ScenarioDecapConnection(
            refdes="A0",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="EXCHANGE-CHAIN",
            power_vias=(_via("VP0", "V2", 0.0),),
            ground_vias=(_via("VG0", "DGND", 5.0),),
        ),
        "D1": ScenarioDecapConnection(
            refdes="D1",
            kind=DecapConnectionKind.SHARED_DUMMY,
            cluster_id="EXCHANGE-CHAIN",
        ),
        "A2": ScenarioDecapConnection(
            refdes="A2",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="EXCHANGE-CHAIN",
            power_vias=(_via("VP2", "V2", 10.0),),
        ),
    }
    connections.update(
        {
            item.refdes: ScenarioDecapConnection(
                refdes=item.refdes,
                kind=DecapConnectionKind.DIRECT,
                power_vias=(_via(f"VP-{item.refdes}", "V1", item.x_um),),
                ground_vias=(
                    _via(f"VG-{item.refdes}", "DGND", item.x_um + 5.0),
                ),
            )
            for item in donor_decaps
        }
    )
    eligibility = {rail_id: _eligibility(rail_id) for rail_id in rail_ids}
    cluster = SharedPadCluster(
        cluster_id="EXCHANGE-CHAIN",
        state=SharedPadClusterState.ANCHORED,
        member_refdes=("A0", "D1", "A2"),
        anchor_refdes=("A0", "A2"),
        dummy_refdes=("D1",),
        power_net="V2",
        ground_net="DGND",
        layer="TOP",
        power_edges=(("A0", "D1"), ("D1", "A2")),
        ground_edges=(("A0", "D1"), ("D1", "A2")),
        isolation_gap_refdes=("A0", "D1", "A2"),
        eligibility=eligibility,
        via_eligibility={
            via_id: dict(eligibility) for via_id in ("VP0", "VP2")
        },
    )
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixture/exchange-chain.spd",
            name="exchange-chain.spd",
            size_bytes=300,
            sha256="f" * 64,
        ),
        normalized_project=_project(
            rail_ids, bump_x={"R2": 0.0, "R3": 0.0}
        ),
        decaps=decaps,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="f" * 64,
            connections=connections,
            clusters=(cluster,),
        ),
        revision=10,
    )


def _cell(plan, rail_id: str):
    return next(
        item
        for item in plan.cells
        if item.rail_id == rail_id and item.model_id == "M1"
    )


def test_numeric_shortage_is_a_hard_fail_without_mutation() -> None:
    scenario = _direct_scenario(
        (
            ("C1", 0.0, ("R1", "R2")),
            ("C2", 10.0, ("R1", "R2")),
            ("C3", 20.0, ("R1", "R2")),
        ),
        rail_ids=("R1", "R2"),
    )
    fingerprint = scenario.design_fingerprint

    with pytest.raises(DistributionError) as error:
        compute_distribution_plan(
            scenario,
            {("R1", "M1"): 2, ("R2", "M1"): 2},
        )

    assert error.value.code == "NUMERIC_SUPPLY_SHORTAGE"
    assert error.value.diagnostics[0].actual_count == 1
    assert error.value.diagnostics[0].requested_count == 2
    assert scenario.design_fingerprint == fingerprint
    assert scenario.revision == 4


def test_legacy_v2_scenario_is_readable_but_distribution_requires_reanalysis() -> None:
    payload = _shared_chain_scenario().model_dump(mode="python")
    payload["connection_analysis"]["version"] = "DIRECT_TOP_PAD_GRAPH_V2"
    payload["connection_analysis"]["clusters"][0][
        "isolation_gap_refdes"
    ] = ()
    legacy = ScenarioSpec.model_validate(payload)

    with pytest.raises(DistributionError) as error:
        compute_distribution_plan(
            legacy, {("R1", "M1"): 1, ("R2", "M1"): 2}
        )

    assert error.value.code == "CONNECTION_ANALYSIS_UPGRADE_REQUIRED"


def test_present_matrix_and_public_numeric_preflight_exclude_disabled_parts() -> None:
    scenario = _direct_scenario(
        (
            ("C1", 0.0, ("R1", "R2")),
            ("C2", 10.0, ("R1", "R2")),
        ),
        rail_ids=("R1", "R2"),
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": [
                scenario.decaps[0],
                scenario.decaps[1].model_copy(update={"enabled": False}),
            ],
        }
    )

    assert distribution_present_counts(scenario) == {
        ("R1", "M1"): 1,
        ("R2", "M1"): 0,
    }
    validate_distribution_targets(
        scenario, {("R1", "M1"): 0, ("R2", "M1"): 1}
    )
    with pytest.raises(DistributionError) as error:
        validate_distribution_targets(
            scenario, {("R1", "M1"): 0, ("R2", "M1"): 2}
        )
    assert error.value.code == "NUMERIC_SUPPLY_SHORTAGE"


def test_physical_present_counts_fixed_parts_but_donor_capacity_does_not() -> None:
    scenario = _direct_scenario(
        (
            ("MOVABLE", 0.0, ("R1", "R2")),
            ("FLOATING", 10.0, ("R1", "R2")),
            ("UNRESOLVED", 20.0, ("R1", "R2")),
        ),
        rail_ids=("R1", "R2"),
    )
    assert scenario.connection_analysis is not None
    connections = dict(scenario.connection_analysis.connections)
    connections["FLOATING"] = ScenarioDecapConnection(
        refdes="FLOATING",
        kind=DecapConnectionKind.FLOATING_DUMMY,
    )
    connections["UNRESOLVED"] = ScenarioDecapConnection(
        refdes="UNRESOLVED",
        kind=DecapConnectionKind.UNRESOLVED,
        reason="source topology is ambiguous",
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={"connections": connections}
            ),
        }
    )

    assert distribution_present_counts(scenario) == {
        ("R1", "M1"): 3,
        ("R2", "M1"): 0,
    }
    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 2, ("R2", "M1"): 1},
    )
    assert plan.assignment_map == {"MOVABLE": "R2"}
    assert _cell(plan, "R1").actual_count == 2
    assert _cell(plan, "R2").actual_count == 1
    headers, rows = distribution_inventory_table(plan)
    assert headers[-1] == "Reconciliation Delta"
    assert rows == (("M1", 3, 3, 1, 1, 1, 0, 0, 0, 0, 0),)

    with pytest.raises(DistributionError) as error:
        validate_distribution_targets(
            scenario,
            {("R1", "M1"): 1, ("R2", "M1"): 2},
        )
    assert error.value.code == "NUMERIC_SUPPLY_SHORTAGE"
    assert error.value.diagnostics[0].actual_count == 1
    assert error.value.diagnostics[0].requested_count == 2


def test_physical_shortage_returns_maximum_partial_plan() -> None:
    scenario = _direct_scenario(
        (
            ("C1", 0.0, ("R1", "R2")),
            ("C2", 10.0, ("R1",)),
            ("C3", 20.0, ("R1",)),
        ),
        rail_ids=("R1", "R2"),
        bump_x={"R2": 0.0},
    )

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 2},
    )

    assert plan.status == DistributionPlanStatus.PARTIAL
    assert (plan.requested_count, plan.fulfilled_count, plan.shortfall_count) == (
        2,
        1,
        1,
    )
    assert [item.refdes for item in plan.moves] == ["C1"]
    assert _cell(plan, "R1").actual_count == 2
    assert _cell(plan, "R2").actual_count == 1
    assert any(item.code == "PHYSICAL_CAPACITY_SHORTAGE" for item in plan.diagnostics)
    applied = apply_distribution_plan(scenario, plan)
    assert applied.revision == scenario.revision + 1
    assert next(item for item in applied.decaps if item.refdes == "C1").current_rail_id == "R2"


def test_missing_bump_is_partial_not_a_hard_failure() -> None:
    scenario = _direct_scenario(
        (("C1", 0.0, ("R1", "R2")),),
        rail_ids=("R1", "R2"),
        bump_x={},
    )

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 0, ("R2", "M1"): 1},
    )

    assert plan.status == DistributionPlanStatus.PARTIAL
    assert plan.fulfilled_count == 0
    assert plan.moves == ()
    assert any(item.code == "MISSING_TARGET_BUMP" for item in plan.diagnostics)


def test_exact_assignment_avoids_greedy_eligibility_trap() -> None:
    scenario = _direct_scenario(
        (
            ("FLEX", 0.0, ("R1", "R2", "R3")),
            ("ONLY_R2", 100.0, ("R1", "R2")),
        ),
        bump_x={"R2": 0.0, "R3": 0.0},
    )

    plan = compute_distribution_plan(
        scenario,
        {
            ("R1", "M1"): 0,
            ("R2", "M1"): 1,
            ("R3", "M1"): 1,
        },
        DistributionDistanceMode.NEAREST,
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.assignment_map == {"FLEX": "R3", "ONLY_R2": "R2"}


def test_nearest_and_farthest_choose_opposite_direct_decaps() -> None:
    scenario = _direct_scenario(
        (
            ("NEAR", 0.0, ("R1", "R2")),
            ("MID", 50.0, ("R1", "R2")),
            ("FAR", 100.0, ("R1", "R2")),
        ),
        rail_ids=("R1", "R2"),
        bump_x={"R2": 0.0},
    )
    targets = {("R1", "M1"): 2, ("R2", "M1"): 1}

    nearest = compute_distribution_plan(
        scenario, targets, DistributionDistanceMode.NEAREST
    )
    farthest = compute_distribution_plan(
        scenario, targets, DistributionDistanceMode.FARTHEST
    )

    assert nearest.moves[0].refdes == "NEAR"
    assert farthest.moves[0].refdes == "FAR"
    assert _cell(nearest, "R1").actual_count == 2
    # Donor target 2 is a minimum residual, not a command to exhaust every
    # possible donation when receiver demand is only one.
    assert _cell(nearest, "R1").target_count == 2


def test_shared_pad_optimizer_avoids_dummy_residual_and_applies_once() -> None:
    scenario = _shared_chain_scenario()

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 2},
        DistributionDistanceMode.NEAREST,
    )

    assert plan.status == DistributionPlanStatus.PARTIAL
    # The receiver can gain only one cap: a second moved cap would also require
    # a separator and push the donor below its minimum target.
    assert plan.assignment_map == {"A0": "R2"}
    assert plan.isolation_gap_refdes == ("D1",)
    assert plan.fulfilled_count == 1
    assert _cell(plan, "R1").actual_count == 1
    assert _cell(plan, "R1").sacrificed_count == 1
    changed = apply_distribution_plan(scenario, plan)
    assert changed.revision == scenario.revision + 1
    assert {item.refdes: item.current_rail_id for item in changed.decaps} == {
        "A0": "R2",
        "D1": "R1",
        "A2": "R1",
    }
    assert next(item for item in changed.decaps if item.refdes == "D1").pad_state.value == "ISOLATION_GAP"
    assert not next(item for item in changed.decaps if item.refdes == "D1").enabled
    d1_export = next(item for item in plan.export_rows if item.refdes == "D1")
    assert d1_export.previous_net == "V1"
    assert d1_export.new_net == "UNUSED (ISOLATION GAP)"
    assert scenario.revision == 9


def test_exchange_tolerance_counts_cluster_members_and_never_strands_dummy() -> None:
    scenario = _shared_exchange_scenario()
    targets = {
        ("R1", "M1"): 0,
        ("R2", "M1"): 3,
        ("R3", "M1"): 2,
    }

    too_small = compute_distribution_plan(
        scenario, targets, tolerances={("R2", "M1"): 34.0}
    )
    enough = compute_distribution_plan(
        scenario, targets, tolerances={("R2", "M1"): 67.0}
    )

    assert _cell(too_small, "R2").tolerance_count == 1
    assert too_small.status == DistributionPlanStatus.PARTIAL
    assert too_small.fulfilled_count == 0
    assert _cell(too_small, "R2").actual_count == 3
    assert too_small.changed_count == 0

    assert enough.status == DistributionPlanStatus.PARTIAL
    assert enough.fulfilled_count == 1
    assert enough.changed_count == 4
    exchange_cell = _cell(enough, "R2")
    assert exchange_cell.actual_count == 3
    assert exchange_cell.tolerance_count == 2
    assert (
        exchange_cell.sent_count,
        exchange_cell.sacrificed_count,
        exchange_cell.received_count,
    ) == (1, 1, 2)
    moved_cluster_members = {
        item.refdes for item in enough.moves if item.previous_rail_id == "R2"
    }
    assert moved_cluster_members <= {"A0", "A2"}
    assert len(moved_cluster_members) == 1
    assert enough.isolation_gap_refdes == ("D1",)
    _headers, target_rows = distribution_target_table(enough)
    r2_row = next(row for row in target_rows if row[0] == "V2 (R2)")
    assert r2_row[4:] == (0, 4, 1)


def test_exchange_can_be_full_when_donor_covers_move_and_separator_loss() -> None:
    scenario = _shared_exchange_scenario()
    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 0, ("R2", "M1"): 3, ("R3", "M1"): 1},
        tolerances={("R2", "M1"): 67.0},
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 1
    assert plan.changed_count == 4
    assert len(plan.moves) == 3
    assert len(plan.sacrifices) == 1


def test_shared_physical_via_owners_must_remain_in_one_label_component() -> None:
    payload = _shared_chain_scenario().model_dump(mode="python")
    connections = payload["connection_analysis"]["connections"]
    connections["A2"]["power_vias"] = connections["A0"]["power_vias"]
    cluster = payload["connection_analysis"]["clusters"][0]
    cluster["via_eligibility"] = {
        "VP0": cluster["via_eligibility"]["VP0"]
    }
    scenario = ScenarioSpec.model_validate(payload)

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 2},
    )

    # Moving the two Via owners while leaving D1 behind would make two R2
    # components claim VP0.  It must be rejected inside the optimizer rather
    # than surfacing as INTERNAL_PLAN_INVALID after selection.
    assert plan.status == DistributionPlanStatus.PARTIAL
    assert plan.fulfilled_count == 0
    assert plan.moves == ()


def test_plan_is_stale_safe_tamper_safe_and_exports_every_decap() -> None:
    scenario = _direct_scenario(
        (
            ("C1", 0.0, ("R1", "R2")),
            ("C2", 100.0, ("R1", "R2")),
        ),
        rail_ids=("R1", "R2"),
        bump_x={"R2": 0.0},
    )
    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 1},
    )

    rows = distribution_csv_rows(plan)
    assert rows[0] == (
        "Component",
        "REFDES",
        "Before NET",
        "After NET",
        "X (um)",
        "Y (um)",
    )
    assert len(rows) == len(scenario.decaps) + 1
    assert rows[1][:4] == ("M1", "C1", "V1", "V2")
    assert rows[2][:4] == ("M1", "C2", "V1", "V1")

    target_headers, target_rows = distribution_target_table(plan)
    assert target_headers == (
        "PWR NET",
        "M1\nPresent",
        "M1\nTarget",
        "M1\nTolerance (%)",
        "M1\nActual Delta",
        "M1\nActual Changed",
        "M1\nIsolation Gaps",
    )
    assert target_rows == (
        ("V1 (R1)", 2, 1, 0.0, -1, 1, 0),
        ("V2 (R2)", 0, 1, 0.0, 1, 1, 0),
    )

    stale = scenario.model_copy(update={"revision": scenario.revision + 1})
    with pytest.raises(DistributionError, match="changed") as stale_error:
        apply_distribution_plan(stale, plan)
    assert stale_error.value.code == "PLAN_STALE"

    tampered = replace(plan, output_design_fingerprint="0" * 64)
    with pytest.raises(DistributionError) as tampered_error:
        apply_distribution_plan(scenario, tampered)
    assert tampered_error.value.code == "PLAN_TAMPERED"


@pytest.mark.parametrize("value", [True, -0.01, 100.01, float("nan"), float("inf")])
def test_tolerance_validation_rejects_invalid_percentages(value: object) -> None:
    scenario = _direct_scenario(
        (("C1", 0.0, ("R1", "R2")),), rail_ids=("R1", "R2")
    )

    with pytest.raises(DistributionError) as error:
        validate_distribution_targets(
            scenario,
            {("R1", "M1"): 1, ("R2", "M1"): 0},
            {("R1", "M1"): value},
        )

    assert error.value.code == "TOLERANCE_INVALID"


def test_equal_target_tolerance_enables_exact_count_neutral_exchange_path() -> None:
    scenario = _direct_scenario(
        (
            ("A0", 0.0, ("R1", "R2")),
            ("A1", 10.0, ("R1", "R2")),
            ("B0", 100.0, ("R2", "R3")),
            ("B1", 110.0, ("R2", "R3")),
        ),
        bump_x={"R2": 100.0, "R3": 200.0},
    )
    scenario = _with_initial_rails(scenario, {"B0": "R2", "B1": "R2"})
    targets = {
        ("R1", "M1"): 1,
        ("R2", "M1"): 2,
        ("R3", "M1"): 1,
    }

    excluded = compute_distribution_plan(
        scenario, targets, tolerances={("R2", "M1"): 0.0}
    )
    exchanged = compute_distribution_plan(
        scenario, targets, tolerances={("R2", "M1"): 50.0}
    )

    assert excluded.status == DistributionPlanStatus.PARTIAL
    assert excluded.fulfilled_count == 0
    assert excluded.moves == ()
    assert exchanged.status == DistributionPlanStatus.FULL
    assert exchanged.fulfilled_count == 1
    assert exchanged.changed_count == 2
    exchange_cell = _cell(exchanged, "R2")
    assert exchange_cell.actual_count == exchange_cell.target_count == 2
    assert exchange_cell.tolerance_percent == 50.0
    assert exchange_cell.tolerance_count == 1
    assert (exchange_cell.sent_count, exchange_cell.received_count) == (1, 1)
    assert {item.previous_rail_id for item in exchanged.moves} == {"R1", "R2"}
    assert {item.new_rail_id for item in exchanged.moves} == {"R2", "R3"}
    headers, rows = distribution_target_table(exchanged)
    assert "M1\nTolerance (%)" in headers
    assert next(row for row in rows if row[0] == "V2 (R2)")[3] == 50.0


def test_tolerance_floor_and_turnover_cap_limit_physical_fulfillment() -> None:
    specs = (
        ("A0", 0.0, ("R1", "R2")),
        ("A1", 10.0, ("R1", "R2")),
        ("B0", 100.0, ("R2", "R3")),
        ("B1", 110.0, ("R2", "R3")),
    )
    scenario = _with_initial_rails(
        _direct_scenario(specs, bump_x={"R2": 100.0, "R3": 200.0}),
        {"B0": "R2", "B1": "R2"},
    )
    targets = {
        ("R1", "M1"): 0,
        ("R2", "M1"): 2,
        ("R3", "M1"): 2,
    }

    plan = compute_distribution_plan(
        scenario, targets, tolerances={("R2", "M1"): 50.0}
    )

    assert plan.status == DistributionPlanStatus.PARTIAL
    assert (plan.requested_count, plan.fulfilled_count) == (2, 1)
    exchange_cell = _cell(plan, "R2")
    assert exchange_cell.tolerance_count == 1
    assert (exchange_cell.sent_count, exchange_cell.received_count) == (1, 1)
    assert exchange_cell.actual_count == 2

    rounds_to_zero = compute_distribution_plan(
        scenario, targets, tolerances={("R2", "M1"): 49.9}
    )
    assert rounds_to_zero.fulfilled_count == 0
    assert any(
        item.code == "TOLERANCE_ROUNDS_TO_ZERO"
        for item in rounds_to_zero.diagnostics
    )


def test_exchange_without_receiver_demand_never_creates_a_cycle() -> None:
    scenario = _with_initial_rails(
        _direct_scenario(
            (
                ("A", 0.0, ("R1", "R2")),
                ("B", 100.0, ("R1", "R2")),
            ),
            rail_ids=("R1", "R2"),
            bump_x={"R1": 0.0, "R2": 100.0},
        ),
        {"B": "R2"},
    )

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 1},
        DistributionDistanceMode.FARTHEST,
        tolerances={("R1", "M1"): 100.0, ("R2", "M1"): 100.0},
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.requested_count == 0
    assert plan.moves == ()


def test_distance_timeout_preserves_stage_one_maximum_feasible_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _direct_scenario(
        (
            ("C1", 0.0, ("R1", "R2")),
            ("C2", 100.0, ("R1", "R2")),
        ),
        rail_ids=("R1", "R2"),
        bump_x={"R2": 0.0},
    )

    def timeout(*_args, **_kwargs):
        raise DistributionError("OPTIMIZER_TIMEOUT", "fixture timeout")

    monkeypatch.setattr(
        "spd_decap_pi.distribution._direct_distance_selection", timeout
    )
    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 1},
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 1
    assert len(plan.moves) == 1
    assert any(
        item.code == "DISTANCE_OPTIMIZATION_FALLBACK"
        for item in plan.diagnostics
    )


def test_eleven_thousand_direct_candidates_use_fast_exact_distance_flow() -> None:
    scenario = _direct_scenario(
        tuple(
            (f"C{index:05d}", float(index), ("R1", "R2"))
            for index in range(11_000)
        ),
        rail_ids=("R1", "R2"),
        bump_x={"R2": 0.0},
    )

    solver_time_limit_s = 10.0
    full_call_performance_budget_s = 30.0
    started = perf_counter()
    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 10_900, ("R2", "M1"): 100},
        DistributionDistanceMode.NEAREST,
        time_limit_s=solver_time_limit_s,
    )
    elapsed = perf_counter() - started

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 100
    assert [item.refdes for item in plan.moves] == [
        f"C{index:05d}" for index in range(100)
    ]
    assert not any(
        item.code == "DISTANCE_OPTIMIZATION_FALLBACK"
        for item in plan.diagnostics
    )
    # Solver time_limit_s applies independently to multiple optimizer stages;
    # this separate, deliberately looser end-to-end target retains coverage of
    # post-solve validation, export-row creation, and scenario fingerprinting.
    assert elapsed < full_call_performance_budget_s


def test_eleven_thousand_direct_exchange_candidates_use_fast_exact_flow() -> None:
    donor_count = 5_500
    exchange_count = 5_500
    scenario = _direct_scenario(
        tuple(
            (f"A{index:05d}", float(index), ("R1", "R2"))
            for index in range(donor_count)
        )
        + tuple(
            (
                f"B{index:05d}",
                10_000.0 + float(index),
                ("R2", "R3"),
            )
            for index in range(exchange_count)
        ),
        bump_x={"R2": 10_000.0, "R3": 20_000.0},
    )
    scenario = _with_initial_rails(
        scenario,
        {f"B{index:05d}": "R2" for index in range(exchange_count)},
    )

    solver_time_limit_s = 10.0
    full_call_performance_budget_s = 30.0
    started = perf_counter()
    plan = compute_distribution_plan(
        scenario,
        {
            ("R1", "M1"): donor_count - 100,
            ("R2", "M1"): exchange_count,
            ("R3", "M1"): 100,
        },
        DistributionDistanceMode.NEAREST,
        tolerances={("R2", "M1"): 2.0},
        time_limit_s=solver_time_limit_s,
    )
    elapsed = perf_counter() - started

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 100
    assert plan.changed_count == 200
    assert "A05499" in plan.assignment_map
    assert "B05499" in plan.assignment_map
    assert "A00000" not in plan.assignment_map
    assert "B00000" not in plan.assignment_map
    assert not any(
        item.code == "DISTANCE_OPTIMIZATION_FALLBACK"
        for item in plan.diagnostics
    )
    assert elapsed < full_call_performance_budget_s
