from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult
from scipy.sparse import csr_matrix

import spd_decap_pi.distribution as distribution_module
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.domain import (
    CapModel,
    ConfidenceLevel,
    MLOOutline,
    PlaneCell,
    PlanePairSuggestion,
    PlanePartitionSpec,
    PinKind,
    PinRecord,
    ProjectSpec,
    RailSpec,
    StackupLayer,
    TerminalKind,
    ViaLoopTemplate,
    ViaPathKind,
)
from spd_decap_pi.distribution import (
    DistributionDistanceMode,
    DistributionError,
    DistributionOptimizationPolicy,
    DistributionPlanStatus,
    _is_feasible_milp_start,
    apply_distribution_plan,
    compute_distribution_plan,
    distribution_csv_rows,
    distribution_assignable_counts,
    distribution_inventory_table,
    distribution_present_counts,
    distribution_target_table,
    validate_distribution_targets,
)
from spd_decap_pi._core.io.spd import SpdPlaneGeometry
from spd_decap_pi.eligibility import PlaneEligibilityIndex
from spd_decap_pi.scenario import (
    DecapConnectionKind,
    RailEligibility,
    RoutingObstacleAssetRef,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    ScenarioViaLanding,
    ScenarioViaPathEvidence,
    ScenarioViaSegment,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadConnectionAnalysis,
    SHARED_PAD_ANALYSIS_VERSION,
    SourceIdentity,
)
from spd_decap_pi.routing_obstacles import (
    PlannedViaProfile,
    REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE,
    REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_MESSAGE,
    RoutingLayerCompleteness,
    RoutingNetRole,
    RoutingObjectProvenance,
    RoutingObstacleAsset,
    RoutingTraceSegment,
    SignalTraceAvoidancePolicy,
    TraceWidthSource,
    decode_routing_obstacle_asset,
    encode_routing_obstacle_asset,
    routing_attachment_name,
    stackup_fingerprint,
)
from spd_decap_pi.scenario_io import load_scenario, save_scenario


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


def _eligibility(
    rail_id: str,
    *,
    allowed: bool = True,
    destination_layer: str | None = None,
) -> RailEligibility:
    return RailEligibility(
        rail_id=rail_id,
        net=f"V{rail_id[-1]}",
        pwr_layer="PWR",
        gnd_layer="GND",
        destination_pwr_layer=destination_layer,
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


def _with_vertical_path(
    landing: ScenarioViaLanding,
    target_layer: str,
    *,
    start_layer: str = "TOP",
) -> ScenarioViaLanding:
    """Add clean same-XY PWR-side Via evidence to a synthetic landing."""

    x_um = landing.x_um
    y_um = landing.y_um
    segment = ScenarioViaSegment(
        via_id=landing.via_id,
        padstack=landing.padstack,
        drill_diameter_um=1.0,
        start_layer=start_layer,
        end_layer=target_layer,
        length_um=1.0,
        end_x_um=x_um,
        end_y_um=y_um,
    )
    return landing.model_copy(
        update={
            "path_evidence": (
                ScenarioViaPathEvidence(
                    target_layer=target_layer,
                    target_node_id=f"TARGET-{landing.via_id}",
                    target_padstack=landing.padstack,
                    target_pad_kind="CIRCLE",
                    target_pad_width_um=1.0,
                    target_pad_height_um=1.0,
                    x_um=x_um,
                    y_um=y_um,
                    segments=(segment,),
                ),
            ),
        }
    )


def _with_power_via_path(
    scenario: ScenarioSpec,
    refdes: str,
    target_layer: str,
) -> ScenarioSpec:
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections[refdes]
    replaced = connection.model_copy(
        update={
            "power_vias": tuple(
                _with_vertical_path(landing, target_layer)
                for landing in connection.power_vias
            )
        }
    )
    connections = dict(scenario.connection_analysis.connections)
    connections[refdes] = replaced
    return scenario.model_copy(
        update={
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={"connections": connections}
            )
        }
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


def test_legacy_v3_scenario_is_readable_but_distribution_requires_reanalysis() -> None:
    payload = _shared_chain_scenario().model_dump(mode="python")
    payload["connection_analysis"]["version"] = "DIRECT_TOP_COPPER_PATH_V3"
    legacy = ScenarioSpec.model_validate(payload)

    with pytest.raises(DistributionError) as error:
        compute_distribution_plan(
            legacy, {("R1", "M1"): 1, ("R2", "M1"): 2}
        )

    assert error.value.code == "CONNECTION_ANALYSIS_UPGRADE_REQUIRED"
    assert "V4/V5 finite-pad/ordered-boolean" in str(error.value)


def test_distribution_accepts_connected_v4_and_v5_shared_pad_analyses() -> None:
    v5 = _shared_chain_scenario()
    targets = {("R1", "M1"): 1, ("R2", "M1"): 2}

    v5_plan = compute_distribution_plan(v5, targets)

    payload = v5.model_dump(mode="python")
    payload["connection_analysis"]["version"] = "DIRECT_TOP_COPPER_PATH_V4"
    v4 = ScenarioSpec.model_validate(payload)
    v4_plan = compute_distribution_plan(v4, targets)
    assert v4_plan.status == v5_plan.status
    assert v4_plan.moves == v5_plan.moves


def test_distribution_v5_disconnected_ground_graph_matches_pwr_only_plan() -> None:
    connected = _shared_chain_scenario()
    assert connected.connection_analysis is not None
    cluster = connected.connection_analysis.clusters[0].model_copy(
        update={"ground_edges": ()}
    )
    disconnected = connected.model_copy(
        update={
            "connection_analysis": connected.connection_analysis.model_copy(
                update={"clusters": (cluster,)}
            )
        }
    )

    targets = {("R1", "M1"): 1, ("R2", "M1"): 2}
    connected_plan = compute_distribution_plan(connected, targets)
    disconnected_plan = compute_distribution_plan(disconnected, targets)

    assert disconnected_plan.status == connected_plan.status
    assert disconnected_plan.moves == connected_plan.moves
    assert disconnected_plan.sacrifices == connected_plan.sacrifices


def test_distribution_power_projection_repairs_gnd_unresolved_cluster_for_atomic_move() -> None:
    projected = _shared_chain_scenario()
    assert projected.connection_analysis is not None
    unresolved_connections = {
        refdes: connection.model_copy(
            update={
                "kind": DecapConnectionKind.UNRESOLVED,
                "reason": (
                    "GND TOP component has no source Via anchor: "
                    f"{refdes}"
                ),
            }
        )
        for refdes, connection in projected.connection_analysis.connections.items()
    }
    unresolved_cluster = projected.connection_analysis.clusters[0].model_copy(
        update={
            "state": SharedPadClusterState.UNRESOLVED,
            "ground_edges": (),
            "isolation_gap_refdes": (),
            "reason": (
                "GND TOP component has no source Via anchor: A0, D1, A2"
            ),
            "eligibility": {},
            "via_eligibility": {},
        }
    )
    unresolved = ScenarioSpec.model_validate(
        {
            **projected.model_dump(mode="python"),
            "connection_analysis": projected.connection_analysis.model_copy(
                update={
                    "connections": unresolved_connections,
                    "clusters": (unresolved_cluster,),
                }
            ),
        }
    )
    projection = distribution_module._DistributionPowerProjection(
        source_sha256=unresolved.source.sha256,
        input_design_fingerprint=unresolved.design_fingerprint,
        input_revision=unresolved.revision,
        canonical_targets=(("r1", "m1", 0), ("r2", "m1", 3)),
        canonical_tolerances=(("r1", "m1", 0.0), ("r2", "m1", 0.0)),
        source_decaps=tuple(unresolved.decaps),
        projected_decaps=tuple(unresolved.decaps),
        source_analysis=unresolved.connection_analysis,
        projected_analysis=projected.connection_analysis,
        promoted_cluster_ids=("CHAIN",),
    )

    plan = compute_distribution_plan(
        unresolved,
        {("R1", "M1"): 0, ("R2", "M1"): 3},
        power_projection=projection,
    )
    result = apply_distribution_plan(
        unresolved,
        plan,
        power_projection=projection,
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert set(plan.assignment_map) == {"A0", "D1", "A2"}
    assert {item.current_rail_id for item in result.decaps} == {"R2"}
    assert result.connection_analysis is not None
    assert {
        item.kind for item in result.connection_analysis.connections.values()
    } == {
        DecapConnectionKind.SHARED_ANCHOR,
        DecapConnectionKind.SHARED_DUMMY,
    }
    assert result.connection_analysis.clusters[0].state == SharedPadClusterState.ANCHORED


def test_power_projection_is_bound_to_canonical_targets_and_tolerances() -> None:
    """A TOP proof cannot be replayed for an unproved non-TOP request."""

    scenario = _direct_scenario(
        (("C1", 5.0, ("R1", "R3")),),
        rail_ids=("R1", "R2", "R3"),
    )
    layers = [
        StackupLayer(
            name="TOP",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V1", "V2"],
        ),
        StackupLayer(name="D1", thickness_um=20.0, dk=4.0, df=0.01),
        StackupLayer(
            name="PWR_ALT",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V3"],
        ),
        StackupLayer(
            name="GND",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["DGND"],
        ),
    ]
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": layers,
            "rails": [
                rail.model_copy(
                    update={
                        "pwr_layer": (
                            "PWR_ALT" if rail.rail_id == "R3" else "TOP"
                        ),
                        "gnd_layer": "GND",
                    }
                )
                for rail in scenario.base_project.rails
            ],
        }
    )
    decap = scenario.decaps[0].model_copy(
        update={
            "eligibility": {
                rail_id: item.model_copy(
                    update={
                        "pwr_layer": "PWR_ALT" if rail_id == "R3" else "TOP",
                        "gnd_layer": "GND",
                    }
                )
                for rail_id, item in scenario.decaps[0].eligibility.items()
            }
        }
    )
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    landing = connection.power_vias[0]
    microvia = ScenarioViaSegment(
        via_id=landing.via_id,
        padstack=landing.padstack,
        drill_diameter_um=100.0,
        start_layer="TOP",
        end_layer="PWR_ALT",
        length_um=20.0,
        end_x_um=landing.x_um,
        end_y_um=landing.y_um,
        padstack_material="COPPER",
    )
    landing = landing.model_copy(
        update={
            "path_evidence": (
                ScenarioViaPathEvidence(
                    target_layer="PWR_ALT",
                    target_node_id="TARGET-C1",
                    target_padstack=landing.padstack,
                    target_pad_kind="CIRCLE",
                    target_pad_width_um=300.0,
                    target_pad_height_um=300.0,
                    x_um=landing.x_um,
                    y_um=landing.y_um,
                    segments=(microvia,),
                ),
            )
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
            "decaps": [decap.model_dump(mode="python")],
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={
                    "connections": {
                        "C1": connection.model_copy(update={"power_vias": (landing,)})
                    }
                }
            ).model_dump(mode="python"),
        }
    )
    top_plane = SpdPlaneGeometry(
        layer="TOP",
        net="V2",
        positive_polygons_um=(
            ((-10.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-10.0, 10.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    build_targets = {
        (" r3 ", "m1"): 0,
        ("r2", "M1"): 1,
        ("R1", "m1"): 0,
    }
    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(top_plane,),
        targets=build_targets,
        tolerances={("r2", "m1"): 0.0},
    )

    assert projection is not None
    same_request = compute_distribution_plan(
        scenario,
        {
            ("R1", "M1"): 0,
            ("R2", "M1"): 1,
            ("R3", "M1"): 0,
        },
        power_projection=projection,
    )
    assert same_request.status == DistributionPlanStatus.FULL
    assert same_request.assignment_map == {"C1": "R2"}
    validate_distribution_targets(
        scenario,
        {
            ("R1", "M1"): 0,
            ("R2", "M1"): 1,
            ("R3", "M1"): 0,
        },
        power_projection=projection,
    )

    with pytest.raises(DistributionError) as changed_target:
        compute_distribution_plan(
            scenario,
            {
                ("R1", "M1"): 0,
                ("R2", "M1"): 0,
                ("R3", "M1"): 1,
            },
            power_projection=projection,
        )
    assert changed_target.value.code == "POWER_PROJECTION_STALE"
    with pytest.raises(DistributionError) as validated_changed_target:
        validate_distribution_targets(
            scenario,
            {
                ("R1", "M1"): 0,
                ("R2", "M1"): 0,
                ("R3", "M1"): 1,
            },
            power_projection=projection,
        )
    assert validated_changed_target.value.code == "POWER_PROJECTION_STALE"

    with pytest.raises(DistributionError) as changed_tolerance:
        compute_distribution_plan(
            scenario,
            {
                ("R1", "M1"): 0,
                ("R2", "M1"): 1,
                ("R3", "M1"): 0,
            },
            tolerances={("R2", "M1"): 1.0},
            power_projection=projection,
        )
    assert changed_tolerance.value.code == "POWER_PROJECTION_STALE"
    with pytest.raises(DistributionError) as validated_changed_tolerance:
        validate_distribution_targets(
            scenario,
            {
                ("R1", "M1"): 0,
                ("R2", "M1"): 1,
                ("R3", "M1"): 0,
            },
            {("R2", "M1"): 1.0},
            power_projection=projection,
        )
    assert validated_changed_tolerance.value.code == "POWER_PROJECTION_STALE"


def test_distribution_projection_via_eligibility_uses_source_physical_landing() -> None:
    """A source-classified PWR landing, not its old Via span, authorizes XY."""

    rail = _rail("R1")
    geometry = SpdPlaneGeometry(
        layer="PWR",
        net="V1",
        positive_polygons_um=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    index = PlaneEligibilityIndex(
        (geometry,),
        _project(("R1",)).stackup_layers,
        selected_pairs=(
            PlanePairSuggestion(
                rail_net="V1",
                pwr_layer="PWR",
                gnd_layer="GND",
                pwr_index=0,
                gnd_index=1,
                separation_um=1.0,
            ),
        ),
    )
    choices = {("v1", "pwr", "gnd"): ((rail, "VT1"),)}
    legacy = ScenarioViaLanding(
        via_id="V1",
        net="V1",
        endpoint_node_id="N1",
        padstack="P1",
        x_um=5.0,
        y_um=5.0,
    )
    evidence = ScenarioViaPathEvidence(
        target_layer="PWR",
        target_node_id="N2",
        target_padstack="P2",
        target_pad_kind="CIRCLE",
        target_pad_width_um=1.0,
        target_pad_height_um=1.0,
        x_um=5.0,
        y_um=5.0,
        segments=(
            ScenarioViaSegment(
                via_id="V1",
                padstack="P1",
                drill_diameter_um=1.0,
                start_layer="TOP",
                end_layer="PWR",
                length_um=1.0,
                end_x_um=5.0,
                end_y_um=5.0,
            ),
        ),
    )

    evaluation_path_only = legacy.model_copy(
        update={"path_evidence": (evidence,)}
    )
    legacy_result = distribution_module._distribution_via_eligibility(
        index, evaluation_path_only, choices, pwr_layer_order={"top": 0, "pwr": 1}
    )
    assert set(legacy_result) == {"R1"}
    source_result = distribution_module._distribution_via_eligibility(
        index, legacy, choices, pwr_layer_order={"top": 0, "pwr": 1}
    )
    assert set(source_result) == {"R1"}
    assert source_result["R1"].via_template_id == "VT1"


def test_direct_multi_via_component_accepts_one_target_intersection_root() -> None:
    """One physical PWR Via root is enough for one connected direct PWR pad."""

    result = distribution_module._distribution_component_eligibility(
        (
            {"R2": _eligibility("R2")},
            {"R1": _eligibility("R1")},  # second Via is blind for R2
        )
    )

    assert set(result) == {"R1", "R2"}


def test_direct_multi_via_component_rejects_target_without_any_intersection_root() -> None:
    result = distribution_module._distribution_component_eligibility(
        ({"R1": _eligibility("R1")}, {"R1": _eligibility("R1")})
    )

    assert "R2" not in result


def test_routing_protection_requires_all_retained_via_columns() -> None:
    result = distribution_module._distribution_component_eligibility(
        (
            {"R2": _eligibility("R2")},
            {"R1": _eligibility("R1")},
        ),
        require_all_vias=True,
    )

    assert set(result) == set()


def test_routing_protection_rejects_disjoint_destination_layers() -> None:
    result = distribution_module._distribution_component_eligibility(
        (
            {"R2": _eligibility("R2", destination_layer="PWR1")},
            {"R2": _eligibility("R2", destination_layer="PWR3")},
        ),
        require_all_vias=True,
    )

    assert result == {}


def test_routing_protection_selects_nearest_common_safe_layer() -> None:
    first_pwr1 = _eligibility("R2", destination_layer="PWR1")
    first_pwr3 = _eligibility("R2", destination_layer="PWR3")
    second_pwr3 = _eligibility("R2", destination_layer="PWR3")
    result = distribution_module._distribution_component_eligibility(
        (
            {"R2": first_pwr1},
            {"R2": second_pwr3},
        ),
        require_all_vias=True,
        per_via_layer_candidates=(
            {("r2", "pwr1"): first_pwr1, ("r2", "pwr3"): first_pwr3},
            {("r2", "pwr3"): second_pwr3},
        ),
    )

    assert result["R2"].destination_pwr_layer == "PWR3"


def _scenario_with_routing_asset(
    *,
    trace_y_um: float,
    incomplete_layer: str | None = None,
) -> tuple[ScenarioSpec, dict[str, bytes], SpdPlaneGeometry]:
    scenario = _direct_scenario(
        (("C1", 0.0, ("R1", "R2")),), rail_ids=("R1", "R2")
    )
    layers = [
        StackupLayer(
            name="TOP",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V1", "V2"],
        ),
        StackupLayer(
            name="SIG1",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=[],
        ),
        StackupLayer(
            name="PWR_ALT",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V2"],
        ),
        StackupLayer(
            name="GND",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["DGND"],
        ),
    ]
    rails = [
        rail.model_copy(update={"pwr_layer": "TOP", "gnd_layer": "GND"})
        for rail in scenario.base_project.rails
    ]
    via_templates = [
        ViaLoopTemplate(
            template_id=f"VT-{rail.rail_id}",
            pwr_reference_layer="TOP",
            gnd_reference_layer="GND",
            path_kind=ViaPathKind.STACKED,
            finite_port_width_um=100.0,
            finite_port_height_um=100.0,
        )
        for rail in rails
    ]
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": layers,
            "rails": rails,
            "via_templates": via_templates,
        }
    )
    conductor_layers = tuple(item.name for item in layers)
    asset = RoutingObstacleAsset(
        source_sha256=scenario.source.sha256,
        stackup_fingerprint=stackup_fingerprint(layers),
        conductor_layers=conductor_layers,
        segments=(
            RoutingTraceSegment(
                trace_id="Trace-SIG1",
                net="SIG_A",
                layer="SIG1",
                x1_um=-100.0,
                y1_um=trace_y_um,
                x2_um=100.0,
                y2_um=trace_y_um,
                width_um=20.0,
                width_source=TraceWidthSource.INLINE,
                net_role=RoutingNetRole.SIGNAL,
                provenance=RoutingObjectProvenance.PHYSICAL_ROUTING,
            ),
        ),
        layer_completeness=tuple(
            RoutingLayerCompleteness(
                layer=layer,
                unresolved_width_count=(1 if layer == incomplete_layer else 0),
                unresolved_codes=(
                    ("TRACE_WIDTH_UNRESOLVED",)
                    if layer == incomplete_layer
                    else ()
                ),
            )
            for layer in conductor_layers
        ),
        via_profiles=tuple(
            PlannedViaProfile(
                profile_id=f"VT-R{index}",
                radius_um_by_layer=tuple(
                    (layer, 50.0) for layer in conductor_layers
                ),
            )
            for index in (1, 2)
        ),
    )
    payload = encode_routing_obstacle_asset(asset)
    decoded = decode_routing_obstacle_asset(payload)
    name = routing_attachment_name(payload)
    digest = sha256(payload).hexdigest()
    reference = RoutingObstacleAssetRef(
        attachment_name=name,
        attachment_sha256=digest,
        content_sha256=str(decoded.content_sha256),
        schema_version=decoded.schema_version,
        source_sha256=decoded.source_sha256,
        stackup_fingerprint=decoded.stackup_fingerprint,
        scope=decoded.scope.value,
        compiler_policy=decoded.compiler_policy,
        production_ready=decoded.production_ready,
        scope_limitation=decoded.scope_limitation,
        via_profile_ids=tuple(item.profile_id for item in decoded.via_profiles),
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
            "attachment_names": [name],
            "attachment_hashes": {name: digest},
            "routing_obstacle_asset": reference.model_dump(mode="python"),
        }
    )
    scenario = _with_power_via_path(scenario, "C1", "PWR_ALT")
    plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((-200.0, -200.0), (200.0, -200.0), (200.0, 200.0), (-200.0, 200.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    return scenario, {name: payload}, plane


def test_distribution_routing_protection_off_preserves_baseline_and_on_blocks() -> None:
    scenario, attachments, plane = _scenario_with_routing_asset(trace_y_um=0.0)
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}

    unprotected_projection = distribution_module.build_distribution_power_projection(
        scenario,
        attachments,
        plane_geometries=(plane,),
        targets=targets,
    )
    assert unprotected_projection is not None
    unprotected = compute_distribution_plan(
        scenario, targets, power_projection=unprotected_projection
    )
    assert unprotected.status == DistributionPlanStatus.FULL
    assert unprotected.assignment_map == {"C1": "R2"}
    assert unprotected.routing_summary is None

    policy = SignalTraceAvoidancePolicy.fixed(0.0)
    protected_projection = distribution_module.build_distribution_power_projection(
        scenario,
        attachments,
        plane_geometries=(plane,),
        targets=targets,
        routing_policy=policy,
    )
    assert protected_projection is not None
    protected = compute_distribution_plan(
        scenario,
        targets,
        power_projection=protected_projection,
        routing_policy=policy,
    )
    assert protected.status == DistributionPlanStatus.PARTIAL
    assert protected.assignment_map == {}
    assert protected.routing_summary is not None
    assert protected.routing_summary.blocked_count >= 1
    assert any(
        item.code == "IMMUTABLE_SIGNAL_CLEARANCE_BLOCKED"
        for item in protected.diagnostics
    )


def test_routing_protection_off_matches_v021_distribution_regression_matrix() -> None:
    """Freeze the legacy v0.21.0 planner outcomes when protection is OFF."""

    direct_trap = _direct_scenario(
        (
            ("FLEX", 0.0, ("R1", "R2", "R3")),
            ("ONLY_R2", 100.0, ("R1", "R2")),
        ),
        bump_x={"R2": 0.0, "R3": 0.0},
    )
    distance = _direct_scenario(
        (
            ("NEAR", 0.0, ("R1", "R2")),
            ("MID", 50.0, ("R1", "R2")),
            ("FAR", 100.0, ("R1", "R2")),
        ),
        rail_ids=("R1", "R2"),
        bump_x={"R2": 0.0},
    )
    shared = _shared_chain_scenario()
    exchange = _shared_exchange_scenario()
    count_neutral = _with_initial_rails(
        _direct_scenario(
            (
                ("A0", 0.0, ("R1", "R2")),
                ("A1", 10.0, ("R1", "R2")),
                ("B0", 100.0, ("R2", "R3")),
                ("B1", 110.0, ("R2", "R3")),
            ),
            bump_x={"R2": 100.0, "R3": 200.0},
        ),
        {"B0": "R2", "B1": "R2"},
    )
    cases = (
        (
            "direct_trap",
            direct_trap,
            {("R1", "M1"): 0, ("R2", "M1"): 1, ("R3", "M1"): 1},
            {},
            DistributionDistanceMode.NEAREST,
            ("FULL", 2, {"FLEX": "R3", "ONLY_R2": "R2"}, (), ()),
        ),
        (
            "direct_nearest",
            distance,
            {("R1", "M1"): 2, ("R2", "M1"): 1},
            {},
            DistributionDistanceMode.NEAREST,
            ("FULL", 1, {"NEAR": "R2"}, (), ()),
        ),
        (
            "direct_farthest",
            distance,
            {("R1", "M1"): 2, ("R2", "M1"): 1},
            {},
            DistributionDistanceMode.FARTHEST,
            ("FULL", 1, {"FAR": "R2"}, (), ()),
        ),
        (
            "shared_chain",
            shared,
            {("R1", "M1"): 1, ("R2", "M1"): 2},
            {},
            DistributionDistanceMode.NEAREST,
            (
                "PARTIAL",
                1,
                {"A0": "R2"},
                ("D1",),
                ("PHYSICAL_CAPACITY_SHORTAGE",),
            ),
        ),
        (
            "shared_exchange_partial",
            exchange,
            {("R1", "M1"): 0, ("R2", "M1"): 3, ("R3", "M1"): 2},
            {("R2", "M1"): 67.0},
            DistributionDistanceMode.NEAREST,
            (
                "PARTIAL",
                1,
                {"A0": "R3", "X0": "R2", "X1": "R2"},
                ("D1",),
                ("PHYSICAL_CAPACITY_SHORTAGE",),
            ),
        ),
        (
            "shared_exchange_full",
            exchange,
            {("R1", "M1"): 0, ("R2", "M1"): 3, ("R3", "M1"): 1},
            {("R2", "M1"): 67.0},
            DistributionDistanceMode.NEAREST,
            (
                "FULL",
                1,
                {"A0": "R3", "X0": "R2", "X1": "R2"},
                ("D1",),
                (),
            ),
        ),
        (
            "count_neutral_exchange",
            count_neutral,
            {("R1", "M1"): 1, ("R2", "M1"): 2, ("R3", "M1"): 1},
            {("R2", "M1"): 50.0},
            DistributionDistanceMode.NEAREST,
            ("FULL", 1, {"A1": "R2", "B1": "R3"}, (), ()),
        ),
    )

    for name, scenario, targets, tolerances, mode, expected in cases:
        default_off = compute_distribution_plan(
            scenario,
            targets,
            mode,
            tolerances=tolerances,
        )
        explicit_off = compute_distribution_plan(
            scenario,
            targets,
            mode,
            tolerances=tolerances,
            routing_policy=SignalTraceAvoidancePolicy.disabled(),
        )
        assert explicit_off == default_off, name
        assert default_off.routing_summary is None, name
        actual = (
            default_off.status.value,
            default_off.fulfilled_count,
            default_off.assignment_map,
            default_off.isolation_gap_refdes,
            tuple(item.code for item in default_off.diagnostics),
        )
        assert actual == expected, name


def test_distribution_routing_unknown_is_hard_blocked_before_milp() -> None:
    scenario, attachments, plane = _scenario_with_routing_asset(
        trace_y_um=1_000.0, incomplete_layer="SIG1"
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    policy = SignalTraceAvoidancePolicy.fixed(0.0)

    projection = distribution_module.build_distribution_power_projection(
        scenario,
        attachments,
        plane_geometries=(plane,),
        targets=targets,
        routing_policy=policy,
    )
    assert projection is not None
    plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=projection,
        routing_policy=policy,
    )

    assert plan.status == DistributionPlanStatus.PARTIAL
    assert plan.assignment_map == {}
    assert plan.routing_summary is not None
    assert plan.routing_summary.unknown_count >= 1
    assert any(item.code == "TRACE_WIDTH_UNRESOLVED" for item in plan.diagnostics)


def test_protected_plan_apply_requires_the_same_routing_projection() -> None:
    scenario, attachments, plane = _scenario_with_routing_asset(trace_y_um=1_000.0)
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    policy = SignalTraceAvoidancePolicy.fixed(0.0)
    protected_projection = distribution_module.build_distribution_power_projection(
        scenario,
        attachments,
        plane_geometries=(plane,),
        targets=targets,
        routing_policy=policy,
    )
    assert protected_projection is not None
    protected_plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=protected_projection,
        routing_policy=policy,
    )

    applied = apply_distribution_plan(
        scenario,
        protected_plan,
        power_projection=protected_projection,
    )
    assert applied.decaps[0].current_rail_id == "R2"
    with pytest.raises(DistributionError, match="modes do not match"):
        apply_distribution_plan(scenario, protected_plan)

    assert protected_projection.routing_summary is not None
    stale_projection = replace(
        protected_projection,
        routing_summary=replace(
            protected_projection.routing_summary,
            asset_content_sha256="0" * 64,
        ),
    )
    with pytest.raises(DistributionError, match="asset or clearance policy changed"):
        apply_distribution_plan(
            scenario,
            protected_plan,
            power_projection=stale_projection,
        )

    off_projection = distribution_module.build_distribution_power_projection(
        scenario,
        attachments,
        plane_geometries=(plane,),
        targets=targets,
    )
    assert off_projection is not None
    off_plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=off_projection,
    )
    with pytest.raises(DistributionError, match="modes do not match"):
        apply_distribution_plan(
            scenario,
            off_plan,
            power_projection=protected_projection,
        )


def test_protected_projection_is_bound_to_the_current_scenario_routing_asset() -> None:
    safe_scenario, safe_attachments, plane = _scenario_with_routing_asset(
        trace_y_um=1_000.0
    )
    blocked_scenario, _blocked_attachments, _blocked_plane = (
        _scenario_with_routing_asset(trace_y_um=0.0)
    )
    assert blocked_scenario.design_fingerprint == safe_scenario.design_fingerprint
    assert blocked_scenario.revision == safe_scenario.revision
    assert (
        blocked_scenario.routing_obstacle_asset
        != safe_scenario.routing_obstacle_asset
    )

    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    policy = SignalTraceAvoidancePolicy.fixed(0.0)
    safe_projection = distribution_module.build_distribution_power_projection(
        safe_scenario,
        safe_attachments,
        plane_geometries=(plane,),
        targets=targets,
        routing_policy=policy,
    )
    assert safe_projection is not None
    safe_plan = compute_distribution_plan(
        safe_scenario,
        targets,
        power_projection=safe_projection,
        routing_policy=policy,
    )
    assert safe_plan.assignment_map == {"C1": "R2"}

    with pytest.raises(DistributionError) as compute_error:
        compute_distribution_plan(
            blocked_scenario,
            targets,
            power_projection=safe_projection,
            routing_policy=policy,
        )
    assert compute_error.value.code == "ROUTING_PROJECTION_STALE"

    with pytest.raises(DistributionError) as apply_error:
        apply_distribution_plan(
            blocked_scenario,
            safe_plan,
            power_projection=safe_projection,
        )
    assert apply_error.value.code == "ROUTING_PROJECTION_STALE"

    # Routing evidence is intentionally outside the electrical fingerprint.
    # OFF plans therefore remain reusable across evidence-only attachment drift.
    off_projection = distribution_module.build_distribution_power_projection(
        safe_scenario,
        safe_attachments,
        plane_geometries=(plane,),
        targets=targets,
    )
    assert off_projection is not None
    off_plan = compute_distribution_plan(
        safe_scenario,
        targets,
        power_projection=off_projection,
    )
    off_applied = apply_distribution_plan(
        blocked_scenario,
        off_plan,
        power_projection=off_projection,
    )
    assert off_applied.decaps[0].current_rail_id == "R2"


def test_anchored_cluster_cannot_reuse_another_members_legacy_protected_rail() -> None:
    base = _shared_chain_scenario()
    first_plan = compute_distribution_plan(
        base,
        {("R1", "M1"): 1, ("R2", "M1"): 2},
    )
    existing = apply_distribution_plan(base, first_plan)
    layers = [
        StackupLayer(
            name="TOP",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V1", "V2"],
        ),
        StackupLayer(
            name="PWR",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V1", "V2"],
        ),
        StackupLayer(
            name="SIG1",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=[],
        ),
        StackupLayer(
            name="PWR_ALT",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V2"],
        ),
        StackupLayer(
            name="GND",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["DGND"],
        ),
    ]
    via_templates = [
        ViaLoopTemplate(
            template_id=f"VT-R{index}",
            pwr_reference_layer="PWR",
            gnd_reference_layer="GND",
            path_kind=ViaPathKind.STACKED,
            finite_port_width_um=2.0,
            finite_port_height_um=2.0,
        )
        for index in (1, 2)
    ]
    project = existing.base_project.model_copy(
        update={"stackup_layers": layers, "via_templates": via_templates}
    )
    conductor_layers = tuple(item.name for item in layers)
    asset = RoutingObstacleAsset(
        source_sha256=existing.source.sha256,
        stackup_fingerprint=stackup_fingerprint(layers),
        conductor_layers=conductor_layers,
        segments=(
            RoutingTraceSegment(
                trace_id="Trace-SIG",
                net="SIG_A",
                layer="SIG1",
                x1_um=9.5,
                y1_um=0.0,
                x2_um=10.5,
                y2_um=0.0,
                width_um=1.0,
                width_source=TraceWidthSource.INLINE,
                net_role=RoutingNetRole.SIGNAL,
                provenance=RoutingObjectProvenance.PHYSICAL_ROUTING,
            ),
        ),
        layer_completeness=tuple(
            RoutingLayerCompleteness(layer=layer) for layer in conductor_layers
        ),
        via_profiles=tuple(
            PlannedViaProfile(
                profile_id=f"VT-R{index}",
                radius_um_by_layer=tuple(
                    (layer, 1.0) for layer in conductor_layers
                ),
            )
            for index in (1, 2)
        ),
    )
    payload = encode_routing_obstacle_asset(asset)
    decoded = decode_routing_obstacle_asset(payload)
    name = routing_attachment_name(payload)
    digest = sha256(payload).hexdigest()
    reference = RoutingObstacleAssetRef(
        attachment_name=name,
        attachment_sha256=digest,
        content_sha256=str(decoded.content_sha256),
        schema_version=decoded.schema_version,
        source_sha256=decoded.source_sha256,
        stackup_fingerprint=decoded.stackup_fingerprint,
        scope=decoded.scope.value,
        compiler_policy=decoded.compiler_policy,
        production_ready=decoded.production_ready,
        scope_limitation=decoded.scope_limitation,
        via_profile_ids=tuple(item.profile_id for item in decoded.via_profiles),
    )
    scenario = ScenarioSpec.model_validate(
        {
            **existing.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
            "attachment_names": [name],
            "attachment_hashes": {name: digest},
            "routing_obstacle_asset": reference.model_dump(mode="python"),
        }
    )
    for refdes in ("A0", "A2"):
        scenario = _with_power_via_path(scenario, refdes, "PWR_ALT")
    plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 2}
    policy = SignalTraceAvoidancePolicy.fixed(0.0)

    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {name: payload},
        plane_geometries=(plane,),
        targets=targets,
        routing_policy=policy,
    )
    assert projection is not None
    assert projection.routing_summary is not None
    assert projection.routing_summary.safe_count == 1
    assert projection.routing_summary.blocked_count == 1
    plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=projection,
        routing_policy=policy,
    )

    assert plan.status == DistributionPlanStatus.PARTIAL
    assert plan.assignment_map == {}


def test_distribution_projects_physical_landing_without_existing_column_span() -> None:
    """A retained target plane may be reached by a rebuilt filled-Cu stack."""

    scenario = _direct_scenario(
        (("C1", 5.0, ("R1", "R2")),), rail_ids=("R1", "R2")
    )
    receiver_plane = SpdPlaneGeometry(
        layer="PWR",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(receiver_plane,),
        targets=targets,
    )

    assert projection is not None
    assert projection.projected_decaps[0].eligibility["R2"].allowed
    plan = compute_distribution_plan(scenario, targets, power_projection=projection)

    assert plan.status == DistributionPlanStatus.FULL
    assert len(plan.moves) == 1
    assert plan.fulfilled_count == 1


def test_projection_scrubs_legacy_destination_eligibility_for_anchored_cluster() -> None:
    """A shared cluster cannot retain an Evaluation-only receiver permission."""

    scenario = _shared_chain_scenario()
    receiver_plane = SpdPlaneGeometry(
        layer="PWR",
        net="V2",
        positive_polygons_um=(
            ((100.0, -10.0), (120.0, -10.0), (120.0, 10.0), (100.0, 10.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 3}

    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(receiver_plane,),
        targets=targets,
    )

    assert projection is not None
    cluster = projection.projected_analysis.clusters[0]
    assert set(cluster.eligibility) == {"R1"}
    assert all(set(values) == {"R1"} for values in cluster.via_eligibility.values())
    plan = compute_distribution_plan(scenario, targets, power_projection=projection)
    assert plan.moves == ()
    assert plan.fulfilled_count == 0


def test_distribution_rail_choices_include_nonadjacent_retained_target_plane() -> None:
    """Distribution is not constrained by Evaluation's adjacent PWR/GND pair."""

    scenario = _direct_scenario(
        (("C1", 5.0, ("R1", "R2")),), rail_ids=("R1", "R2")
    )
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": [
                StackupLayer(
                    name="TOP",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V1", "V2"],
                ),
                StackupLayer(name="D1", thickness_um=20.0, dk=4.0, df=0.01),
                StackupLayer(
                    name="GND1",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["DGND"],
                ),
                StackupLayer(
                    name="PWR_ALT",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V2"],
                ),
            ],
            "rails": [
                rail.model_copy(update={"pwr_layer": "TOP", "gnd_layer": "GND1"})
                for rail in scenario.base_project.rails
            ],
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
        }
    )

    choices, _ = distribution_module._distribution_rail_choices(
        scenario, {"r2"}, alternate_rail_keys={"r2"}
    )

    assert ("v2", "pwr_alt", "gnd1") in choices


def test_distribution_projection_expands_only_actual_receiver_rails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workbook rows unchanged from Present must not load their plane artwork."""

    scenario = _direct_scenario(
        (("C1", 5.0, ("R1",)),),
        rail_ids=("R1", "R2", "R3"),
    )
    seen: dict[str, set[str]] = {}

    def capture_choices(
        _scenario: ScenarioSpec,
        required_rail_keys: set[str],
        *,
        alternate_rail_keys: set[str] | None = None,
    ):
        seen["required"] = set(required_rail_keys)
        seen["alternate"] = set(alternate_rail_keys or ())
        return {}, ()

    monkeypatch.setattr(
        distribution_module, "_distribution_rail_choices", capture_choices
    )

    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        targets={
            ("R1", "M1"): 0,
            ("R2", "M1"): 1,
            ("R3", "M1"): 0,
        },
        tolerances={
            ("R1", "M1"): 0.0,
            ("R2", "M1"): 0.0,
            ("R3", "M1"): 0.0,
        },
    )

    assert projection is None
    assert seen == {"required": {"r2"}, "alternate": {"r2"}}


def test_distribution_projection_expands_only_whole_decap_exchange_rails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tolerance participates only when it permits at least one whole decap."""

    scenario = _with_initial_rails(
        _direct_scenario(
            (
                ("C1", 5.0, ("R1", "R2", "R3")),
                ("C2", 15.0, ("R1", "R2", "R3")),
            ),
            rail_ids=("R1", "R2", "R3"),
        ),
        {"C1": "R1", "C2": "R2"},
    )
    seen: dict[str, set[str]] = {}

    def capture_choices(
        _scenario: ScenarioSpec,
        required_rail_keys: set[str],
        *,
        alternate_rail_keys: set[str] | None = None,
    ):
        seen["required"] = set(required_rail_keys)
        seen["alternate"] = set(alternate_rail_keys or ())
        return {}, ()

    monkeypatch.setattr(
        distribution_module, "_distribution_rail_choices", capture_choices
    )

    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        targets={
            ("R1", "M1"): 0,
            ("R2", "M1"): 1,
            ("R3", "M1"): 0,
        },
        tolerances={
            ("R1", "M1"): 0.0,
            ("R2", "M1"): 100.0,
            # R3 has no Present decap, so even 100% rounds to zero.
            ("R3", "M1"): 100.0,
        },
    )

    assert projection is None
    assert seen == {"required": {"r2"}, "alternate": {"r2"}}


def test_batch_via_eligibility_uses_immutable_landing_not_bent_path_endpoint() -> None:
    """A lower-layer endpoint cannot move a physical decap landing sideways."""

    geometry = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V1",
        positive_polygons_um=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    landing = ScenarioViaLanding(
        via_id="V1", net="V1", endpoint_node_id="N1", padstack="P", x_um=20.0, y_um=5.0,
        path_evidence=(
            ScenarioViaPathEvidence(
                target_layer="PWR_ALT",
                target_node_id="N2",
                target_padstack="P2",
                target_pad_kind="CIRCLE",
                target_pad_width_um=1.0,
                target_pad_height_um=1.0,
                x_um=5.0,
                y_um=5.0,
                segments=(
                    ScenarioViaSegment(
                        via_id="V1",
                        padstack="P",
                        drill_diameter_um=1.0,
                        start_layer="TOP",
                        end_layer="PWR_ALT",
                        length_um=1.0,
                        end_x_um=5.0,
                        end_y_um=5.0,
                    ),
                ),
            ),
        ),
    )
    choices = {("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),)}

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,), (landing,), choices
    )

    assert result == {"V1": {}}


def test_batch_mlo_transition_gate_blocks_only_non_top_candidates() -> None:
    """An unresolved MLO recipe must not poison conventional landings."""

    geometry = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V1",
        positive_polygons_um=(
            ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    conventional = ScenarioViaLanding(
        via_id="V-CONV",
        net="V1",
        endpoint_node_id="N1",
        padstack="P",
        x_um=5.0,
        y_um=5.0,
    )
    mlo = ScenarioViaLanding(
        via_id="V-MLO",
        net="V1",
        endpoint_node_id="N2",
        padstack="P",
        x_um=6.0,
        y_um=6.0,
    )
    choices = {("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),)}
    evidence: list[object] = []
    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,),
        (conventional, mlo),
        choices,
        mlo_transition_required_via_ids=("V-MLO",),
        mlo_transition_evidence=evidence,
        top_layer="TOP",
    )

    assert set(result["V-CONV"]) == {"R1"}
    assert result["V-MLO"] == {}
    assert evidence and evidence[0].detail.code == "MLO_TRANSITION_RECIPE_REQUIRED"


def test_batch_mlo_gate_survives_missing_optional_geometry_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    landing = ScenarioViaLanding(
        via_id="V-MLO",
        net="V1",
        endpoint_node_id="N1",
        padstack="P",
        x_um=5.0,
        y_um=5.0,
    )
    choices = {("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),)}
    evidence: list[object] = []
    blocked_vias: set[str] = set()
    real_import = builtins.__import__

    def fail_shapely(name: str, *args: object, **kwargs: object):
        if name == "shapely":
            raise ImportError("simulated missing Shapely")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_shapely)
    result = distribution_module._distribution_batch_via_eligibility(
        (),
        (landing,),
        choices,
        mlo_transition_required_via_ids=(landing.via_id,),
        mlo_transition_evidence=evidence,
        mlo_transition_blocked_via_ids=blocked_vias,
        top_layer="TOP",
    )

    assert result == {"V-MLO": {}}
    assert blocked_vias == {"V-MLO"}
    assert evidence and evidence[0].detail.code == "MLO_TRANSITION_RECIPE_REQUIRED"


def _non_top_direct_transition_scenario(
    *,
    path_kind: str | None,
    real_import: bool = False,
    fresh_policy: bool = False,
) -> ScenarioSpec:
    scenario = _direct_scenario(
        (("C1", 5.0, ("R1", "R2")),),
        rail_ids=("R1", "R2"),
    )
    layers = [
        StackupLayer(
            name="TOP",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V1"],
        ),
        StackupLayer(name="D1", thickness_um=20.0, dk=4.0, df=0.01),
        StackupLayer(
            name="PWR_ALT",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["V2"],
        ),
        StackupLayer(
            name="GND",
            thickness_um=18.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["DGND"],
        ),
    ]
    metadata: dict[str, object] = {}
    if real_import:
        metadata["spd_import"] = {"source_sha256": scenario.source.sha256}
    if fresh_policy:
        metadata["spd_mlo_transition_policy"] = {
            "policy_version": "MLO_TRANSITION_RECIPE_GATE_V1",
            "transition_required": False,
            "translated_recipe_validated": False,
            "evidence_codes": [],
            "source_sha256": scenario.source.sha256,
        }
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": layers,
            "rails": [
                rail.model_copy(
                    update={
                        "pwr_layer": "TOP" if rail.rail_id == "R1" else "PWR_ALT",
                        "gnd_layer": "GND",
                    }
                )
                for rail in scenario.base_project.rails
            ],
            "metadata": metadata,
        }
    )
    decap = scenario.decaps[0].model_copy(
        update={
            "eligibility": {
                rail_id: item.model_copy(
                    update={
                        "pwr_layer": "TOP" if rail_id == "R1" else "PWR_ALT",
                        "gnd_layer": "GND",
                    }
                )
                for rail_id, item in scenario.decaps[0].eligibility.items()
            }
        }
    )
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    landing = connection.power_vias[0]
    if path_kind is not None:
        segment = ScenarioViaSegment(
            via_id=landing.via_id,
            padstack=landing.padstack,
            drill_diameter_um=100.0 if path_kind == "microvia" else 300.0,
            start_layer="TOP",
            end_layer="PWR_ALT",
            length_um=20.0,
            end_x_um=landing.x_um,
            end_y_um=landing.y_um,
            padstack_material="COPPER",
        )
        landing = landing.model_copy(
            update={
                "path_evidence": (
                    ScenarioViaPathEvidence(
                        target_layer="PWR_ALT",
                        target_node_id="TARGET-C1",
                        target_padstack=landing.padstack,
                        target_pad_kind="CIRCLE",
                        target_pad_width_um=300.0,
                        target_pad_height_um=300.0,
                        x_um=landing.x_um,
                        y_um=landing.y_um,
                        segments=(segment,),
                    ),
                )
            }
        )
    connections = dict(scenario.connection_analysis.connections)
    connections["C1"] = connection.model_copy(update={"power_vias": (landing,)})
    return ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
            "decaps": [decap.model_dump(mode="python")],
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={"connections": connections}
            ).model_dump(mode="python"),
        }
    )


def test_direct_planner_blocks_observed_microvia_without_projection() -> None:
    scenario = _non_top_direct_transition_scenario(path_kind="microvia")
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}

    with pytest.raises(DistributionError) as caught:
        compute_distribution_plan(scenario, targets)

    assert caught.value.code == "POWER_PROJECTION_REQUIRED"
    assert [item.code for item in caught.value.diagnostics] == [
        "MLO_TRANSITION_RECIPE_REQUIRED"
    ]
    assert "build_distribution_power_projection" in str(caught.value)


def test_legacy_missing_policy_and_path_requires_source_reimport() -> None:
    """A pre-v0.22 landing cannot silently become an immutable via column."""

    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
    )
    assert "spd_mlo_transition_policy" not in scenario.base_project.metadata
    assert scenario.connection_analysis is not None
    legacy_landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    assert legacy_landing.path_evidence == ()
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}

    rejection = distribution_module._mlo_transition_rejection_for_landing(
        scenario,
        legacy_landing,
        stackup_layers=scenario.base_project.stackup_layers,
    )
    assert rejection is not None
    assert rejection[0] == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE
    with pytest.raises(DistributionError) as caught:
        compute_distribution_plan(scenario, targets)
    assert caught.value.code == "POWER_PROJECTION_REQUIRED"
    assert [item.code for item in caught.value.diagnostics] == [
        REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE
    ]
    plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(plane,),
        targets=targets,
    )

    assert projection is not None
    assert [item.code for item in projection.mlo_transition_diagnostics] == [
        REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE
    ]
    assert projection.mlo_transition_diagnostics[0].actual_count == 1
    plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=projection,
    )
    assert plan.status == DistributionPlanStatus.PARTIAL
    assert plan.assignment_map == {}
    assert any(
        item.code == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE
        for item in plan.diagnostics
    )


def test_legacy_conventional_path_without_policy_remains_eligible() -> None:
    """Explicit continuous evidence is sufficient without board-level policy."""

    scenario = _non_top_direct_transition_scenario(
        path_kind="conventional",
        real_import=True,
    )
    assert "spd_mlo_transition_policy" not in scenario.base_project.metadata
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    assert landing.path_evidence
    assert (
        distribution_module._mlo_transition_rejection_for_landing(
            scenario,
            landing,
            stackup_layers=scenario.base_project.stackup_layers,
        )
        is None
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    direct_plan = compute_distribution_plan(scenario, targets)
    assert direct_plan.status == DistributionPlanStatus.FULL
    assert direct_plan.assignment_map == {"C1": "R2"}
    plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(plane,),
        targets=targets,
    )

    assert projection is not None
    assert projection.mlo_transition_diagnostics == ()
    plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=projection,
    )
    assert plan.status == DistributionPlanStatus.FULL
    assert plan.assignment_map == {"C1": "R2"}


def _with_landing_certificate(
    scenario: ScenarioSpec,
    landing: ScenarioViaLanding,
    row: dict[str, object],
) -> ScenarioSpec:
    ids = [landing.via_id.casefold()]
    metadata = {
        "certificate_version": "MLO_LANDING_CERTIFICATE_V1",
        "source_sha256": scenario.source.sha256,
        "landing_count": 1,
        "landing_ids_sha256": sha256(
            (json.dumps(ids, separators=(",", ":")) + "\n").encode()
        ).hexdigest(),
        "conventional_count": int(
            row.get("classification") == "CONVENTIONAL_THROUGH_VIA"
        ),
        "short_span_count": int(row.get("classification") == "SHORT_SPAN_VIA"),
        "by_via_id": {landing.via_id: row},
    }
    row.setdefault("padstack_material", "COPPER")
    metadata["claims_sha256"] = distribution_module.mlo_landing_certificate_claims_sha256(
        metadata["by_via_id"]
    )
    project = scenario.base_project.model_copy(
        update={
            "metadata": {
                **scenario.base_project.metadata,
                "spd_mlo_landing_certificates": metadata,
            }
        }
    )
    return scenario.model_copy(
        update={"normalized_project": project.model_dump(mode="python")}
    )


def test_landing_certificate_requires_complete_and_known_claims() -> None:
    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    # The row is deliberately crafted with only a classification.  Even with
    # matching source/count metadata it cannot authorize a pathless landing.
    incomplete = _with_landing_certificate(
        scenario,
        landing,
        {"classification": "CONVENTIONAL_THROUGH_VIA"},
    )
    assert distribution_module._mlo_transition_rejection_for_landing(
        incomplete,
        incomplete.connection_analysis.connections["C1"].power_vias[0],
        stackup_layers=incomplete.base_project.stackup_layers,
    )[0] == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE

    # Unknown row claims must also fail closed, rather than being ignored by a
    # permissive decoder.
    unknown = _with_landing_certificate(
        scenario,
        landing,
        {
            "classification": "CONVENTIONAL_THROUGH_VIA",
            "padstack": landing.padstack,
            "span_layers": ["TOP", "GND"],
            "drill_diameter_um": 300.0,
            "unexpected": True,
        },
    )
    assert distribution_module._mlo_transition_rejection_for_landing(
        unknown,
        unknown.connection_analysis.connections["C1"].power_vias[0],
        stackup_layers=unknown.base_project.stackup_layers,
    )[0] == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE


def test_landing_certificate_binds_padstack_and_conductor_span() -> None:
    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    mismatch = _with_landing_certificate(
        scenario,
        landing,
        {
            "classification": "CONVENTIONAL_THROUGH_VIA",
            "padstack": "DIFFERENT_PADSTACK",
            "span_layers": ["TOP", "GND"],
            "drill_diameter_um": 300.0,
        },
    )
    assert distribution_module._mlo_transition_rejection_for_landing(
        mismatch,
        mismatch.connection_analysis.connections["C1"].power_vias[0],
        stackup_layers=mismatch.base_project.stackup_layers,
    )[0] == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE

    non_copper = _with_landing_certificate(
        scenario,
        landing,
        {
            "classification": "CONVENTIONAL_THROUGH_VIA",
            "padstack": landing.padstack,
            "span_layers": ["TOP", "GND"],
            "drill_diameter_um": 300.0,
            "padstack_material": "ALUMINUM",
        },
    )
    assert distribution_module._mlo_transition_rejection_for_landing(
        non_copper,
        non_copper.connection_analysis.connections["C1"].power_vias[0],
        stackup_layers=non_copper.base_project.stackup_layers,
    )[0] == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE

    false_span = _with_landing_certificate(
        scenario,
        landing,
        {
            "classification": "CONVENTIONAL_THROUGH_VIA",
            "padstack": landing.padstack,
            "span_layers": ["TOP", "PWR_ALT"],
            "drill_diameter_um": 300.0,
        },
    )
    assert distribution_module._mlo_transition_rejection_for_landing(
        false_span,
        false_span.connection_analysis.connections["C1"].power_vias[0],
        stackup_layers=false_span.base_project.stackup_layers,
    )[0] == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE


def test_landing_certificate_claim_digest_and_count_are_strict() -> None:
    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    valid_scenario = _with_landing_certificate(
        scenario,
        landing,
        {
            "classification": "CONVENTIONAL_THROUGH_VIA",
            "padstack": landing.padstack,
            "span_layers": ["TOP", "GND"],
            "drill_diameter_um": 300.0,
        },
    )
    raw = dict(
        valid_scenario.base_project.metadata["spd_mlo_landing_certificates"]
    )
    with pytest.raises(ValueError):
        distribution_module.parse_mlo_landing_certificates(
            {**raw, "claims_sha256": "0" * 64},
            expected_source_sha256=scenario.source.sha256,
        )
    with pytest.raises(ValueError):
        distribution_module.parse_mlo_landing_certificates(
            {**raw, "conventional_count": 0},
            expected_source_sha256=scenario.source.sha256,
        )


def test_short_span_landing_is_recipe_required_not_eligible() -> None:
    """DR-0102-like TOP-to-intermediate spans never grant a PWR retarget."""

    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    short_span = _with_landing_certificate(
        scenario,
        landing,
        {
            "classification": "SHORT_SPAN_VIA",
            "padstack": landing.padstack,
            "span_layers": ["TOP", "PWR_ALT"],
            "drill_diameter_um": 100.0,
        },
    )
    rejection = distribution_module._mlo_transition_rejection_for_landing(
        short_span,
        short_span.connection_analysis.connections["C1"].power_vias[0],
        stackup_layers=short_span.base_project.stackup_layers,
    )
    assert rejection is not None
    assert rejection[0] == "MLO_TRANSITION_RECIPE_REQUIRED"
    plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    projection = distribution_module.build_distribution_power_projection(
        short_span,
        {},
        plane_geometries=(plane,),
        targets=targets,
    )
    assert projection is not None
    assert [item.code for item in projection.mlo_transition_diagnostics] == [
        "MLO_TRANSITION_RECIPE_REQUIRED"
    ]
    assert projection.mlo_transition_diagnostics[0].actual_count == 1
    plan = compute_distribution_plan(
        short_span,
        targets,
        power_projection=projection,
    )
    assert plan.status == DistributionPlanStatus.PARTIAL
    assert plan.assignment_map == {}


def test_source_bound_conventional_landing_certificate_allows_pathless_pth() -> None:
    """Importer PTH certificate permits one pathless landing, not the board."""

    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    source_sha = scenario.source.sha256
    import hashlib, json

    ids = [landing.via_id.casefold()]
    metadata = {
        "certificate_version": "MLO_LANDING_CERTIFICATE_V1",
        "source_sha256": source_sha,
        "landing_count": 1,
        "landing_ids_sha256": hashlib.sha256(
            (json.dumps(ids, separators=(",", ":")) + "\n").encode()
        ).hexdigest(),
        "by_via_id": {
            landing.via_id: {
                "classification": "CONVENTIONAL_THROUGH_VIA",
                "padstack": landing.padstack,
                "span_layers": ["TOP", "GND"],
                "drill_diameter_um": 300.0,
                "padstack_material": "COPPER",
            }
        },
        "conventional_count": 1,
        "short_span_count": 0,
    }
    metadata["claims_sha256"] = distribution_module.mlo_landing_certificate_claims_sha256(
        metadata["by_via_id"]
    )
    scenario = scenario.model_copy(
        update={
            "normalized_project": scenario.base_project.model_copy(
                update={
                    "metadata": {
                        **scenario.base_project.metadata,
                        "spd_mlo_landing_certificates": metadata,
                    }
                }
            ).model_dump(mode="python")
        }
    )
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    assert distribution_module._mlo_transition_rejection_for_landing(
        scenario,
        landing,
        stackup_layers=scenario.base_project.stackup_layers,
    ) is None

    # The same certificate must not override explicit microvia evidence.
    mlo_landing = _with_vertical_path(landing, "PWR_ALT")
    mlo_evidence = mlo_landing.path_evidence[0].model_copy(
        update={
            "segments": (
                mlo_landing.path_evidence[0].segments[0].model_copy(
                    update={
                        "drill_diameter_um": 100.0,
                        "padstack_material": "COPPER",
                    }
                ),
            )
        }
    )
    mlo_landing = mlo_landing.model_copy(update={"path_evidence": (mlo_evidence,)})
    mlo_connection = scenario.connection_analysis.connections["C1"].model_copy(
        update={"power_vias": (mlo_landing,)}
    )
    scenario = scenario.model_copy(
        update={
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={"connections": {"C1": mlo_connection}}
            )
        }
    )
    assert distribution_module._mlo_transition_rejection_for_landing(
        scenario,
        mlo_landing,
        stackup_layers=scenario.base_project.stackup_layers,
    )[0] == "MLO_TRANSITION_RECIPE_REQUIRED"


def test_power_projection_parses_transition_metadata_once_per_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many landings share one immutable, source-bound transition context."""

    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    source_landing = connection.power_vias[0]
    landings = tuple(
        source_landing.model_copy(update={"via_id": f"V-C1-{index}"})
        for index in range(4)
    )
    landing_ids = sorted(item.via_id.casefold() for item in landings)
    certificates = {
        "certificate_version": "MLO_LANDING_CERTIFICATE_V1",
        "source_sha256": scenario.source.sha256,
        "landing_count": len(landing_ids),
        "landing_ids_sha256": sha256(
            (json.dumps(landing_ids, separators=(",", ":")) + "\n").encode()
        ).hexdigest(),
        "by_via_id": {
            landing.via_id: {
                "classification": "CONVENTIONAL_THROUGH_VIA",
                "padstack": landing.padstack,
                "span_layers": ["TOP", "GND"],
                "drill_diameter_um": 300.0,
                "padstack_material": "COPPER",
            }
            for landing in landings
        },
        "conventional_count": len(landing_ids),
        "short_span_count": 0,
    }
    certificates["claims_sha256"] = distribution_module.mlo_landing_certificate_claims_sha256(
        certificates["by_via_id"]
    )
    project = scenario.base_project.model_copy(
        update={
            "metadata": {
                **scenario.base_project.metadata,
                "spd_mlo_landing_certificates": certificates,
            }
        }
    )
    connections = dict(scenario.connection_analysis.connections)
    connections["C1"] = connection.model_copy(update={"power_vias": landings})
    scenario = scenario.model_copy(
        update={
            "normalized_project": project.model_dump(mode="python"),
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={"connections": connections}
            ),
        }
    )

    calls = {"policy": 0, "certificates": 0}
    parse_policy = distribution_module.parse_mlo_transition_policy
    parse_certificates = distribution_module.parse_mlo_landing_certificates

    def counted_policy(*args: object, **kwargs: object) -> object:
        calls["policy"] += 1
        return parse_policy(*args, **kwargs)

    def counted_certificates(*args: object, **kwargs: object) -> object:
        calls["certificates"] += 1
        return parse_certificates(*args, **kwargs)

    monkeypatch.setattr(
        distribution_module, "parse_mlo_transition_policy", counted_policy
    )
    monkeypatch.setattr(
        distribution_module,
        "parse_mlo_landing_certificates",
        counted_certificates,
    )
    plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )

    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(plane,),
        targets={("R1", "M1"): 0, ("R2", "M1"): 1},
    )

    assert projection is not None
    assert projection.mlo_transition_diagnostics == ()
    assert calls == {"policy": 1, "certificates": 1}


def test_pathless_landing_certificate_source_mismatch_is_fail_closed() -> None:
    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]
    metadata = {
        "certificate_version": "MLO_LANDING_CERTIFICATE_V1",
        "source_sha256": "0" * 64,
        "landing_count": 0,
        "landing_ids_sha256": sha256(b"[]\n").hexdigest(),
        "conventional_count": 0,
        "short_span_count": 0,
        "claims_sha256": distribution_module.mlo_landing_certificate_claims_sha256({}),
        "by_via_id": {},
    }
    project = scenario.base_project.model_copy(
        update={
            "metadata": {
                **scenario.base_project.metadata,
                "spd_mlo_landing_certificates": metadata,
            }
        }
    )
    scenario = scenario.model_copy(
        update={"normalized_project": project.model_dump(mode="python")}
    )
    assert distribution_module._mlo_transition_rejection_for_landing(
        scenario,
        landing,
        stackup_layers=scenario.base_project.stackup_layers,
    )[0] == REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE


def test_current_negative_policy_does_not_certify_pathless_landing() -> None:
    """A fresh board-level negative cannot grant per-landing permission."""

    scenario = _non_top_direct_transition_scenario(
        path_kind=None,
        real_import=True,
        fresh_policy=True,
    )
    assert scenario.connection_analysis is not None
    landing = scenario.connection_analysis.connections["C1"].power_vias[0]

    assert (
        distribution_module._mlo_transition_rejection_for_landing(
            scenario,
            landing,
            stackup_layers=scenario.base_project.stackup_layers,
        )
        == (
            REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE,
            REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_MESSAGE,
        )
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    with pytest.raises(DistributionError) as caught:
        compute_distribution_plan(scenario, targets)
    assert caught.value.code == "POWER_PROJECTION_REQUIRED"
    assert [item.code for item in caught.value.diagnostics] == [
        REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE
    ]

    plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(plane,),
        targets=targets,
    )
    assert projection is not None
    assert [item.code for item in projection.mlo_transition_diagnostics] == [
        REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE
    ]
    plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=projection,
    )
    assert plan.status == DistributionPlanStatus.PARTIAL
    assert plan.assignment_map == {}


@pytest.mark.parametrize(
    "raw_policy",
    (
        "false",
        {
            "policy_version": "MLO_TRANSITION_RECIPE_GATE_V1",
            "transition_required": "false",
            "translated_recipe_validated": "true",
        },
        {
            "policy_version": "MLO_TRANSITION_RECIPE_GATE_V999",
            "transition_required": True,
            "translated_recipe_validated": True,
        },
    ),
)
def test_mlo_policy_metadata_is_fail_closed_when_malformed(raw_policy: object) -> None:
    project = _project(("R1",)).model_copy(
        update={"metadata": {"spd_mlo_transition_policy": raw_policy}}
    )
    scenario = SimpleNamespace(
        base_project=project,
        source=SimpleNamespace(sha256=sha256(b"source").hexdigest()),
    )
    landing = ScenarioViaLanding(
        via_id="V-MLO",
        net="V1",
        endpoint_node_id="N1",
        padstack="P",
        x_um=5.0,
        y_um=5.0,
    )

    assert distribution_module._mlo_transition_required_for_landing(
        scenario,
        landing,
        stackup_layers=project.stackup_layers,
    )


def test_batch_via_eligibility_accepts_target_copper_below_existing_via_span() -> None:
    """A filled-Cu microvia rebuild may target deeper exact PWR copper."""

    geometry = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V1",
        positive_polygons_um=(
            ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    landing = ScenarioViaLanding(
        via_id="V1",
        net="V1",
        endpoint_node_id="N1",
        padstack="P",
        x_um=5.0,
        y_um=5.0,
        path_evidence=(
            ScenarioViaPathEvidence(
                target_layer="PWR",
                target_node_id="N2",
                target_padstack="P2",
                target_pad_kind="CIRCLE",
                target_pad_width_um=1.0,
                target_pad_height_um=1.0,
                x_um=5.0,
                y_um=5.0,
                segments=(
                    ScenarioViaSegment(
                        via_id="V1",
                        padstack="P",
                        drill_diameter_um=1.0,
                        start_layer="TOP",
                        end_layer="PWR",
                        length_um=1.0,
                        end_x_um=5.0,
                        end_y_um=5.0,
                    ),
                ),
            ),
        ),
    )
    choices = {("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),)}

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,),
        (landing,),
        choices,
        pwr_layer_order={"top": 0, "pwr": 1, "pwr_alt": 3},
    )

    assert set(result["V1"]) == {"R1"}


def test_batch_via_eligibility_does_not_depend_on_existing_via_span() -> None:
    """A prior same-XY segment is tolerated but is not permission evidence."""

    geometry = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V1",
        positive_polygons_um=(
            ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    landing = ScenarioViaLanding(
        via_id="V1",
        net="V1",
        endpoint_node_id="N1",
        padstack="P",
        x_um=5.0,
        y_um=5.0,
        path_evidence=(
            ScenarioViaPathEvidence(
                # The recovered path terminates farther down-stack at the
                # same XY, so its vertical segment proves this existing Via
                # crosses PWR_ALT.
                target_layer="PWR_DEEP",
                target_node_id="N2",
                target_padstack="P2",
                target_pad_kind="CIRCLE",
                target_pad_width_um=1.0,
                target_pad_height_um=1.0,
                x_um=5.0,
                y_um=5.0,
                segments=(
                    ScenarioViaSegment(
                        via_id="V1",
                        padstack="P",
                        drill_diameter_um=1.0,
                        start_layer="TOP",
                        end_layer="PWR_DEEP",
                        length_um=1.0,
                        end_x_um=5.0,
                        end_y_um=5.0,
                    ),
                ),
            ),
        ),
    )
    choices = {("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),)}

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,),
        (landing,),
        choices,
        pwr_layer_order={"top": 0, "pwr_alt": 3, "pwr_deep": 5},
    )

    assert set(result["V1"]) == {"R1"}


def test_batch_via_eligibility_ignores_lateral_path_evidence() -> None:
    """A lateral path neither moves nor blocks an otherwise valid landing."""

    geometry = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V1",
        positive_polygons_um=(
            ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    landing = ScenarioViaLanding(
        via_id="V1",
        net="V1",
        endpoint_node_id="N1",
        padstack="P",
        x_um=5.0,
        y_um=5.0,
        path_evidence=(
            ScenarioViaPathEvidence(
                target_layer="PWR_ALT",
                target_node_id="N2",
                target_padstack="P2",
                target_pad_kind="CIRCLE",
                target_pad_width_um=1.0,
                target_pad_height_um=1.0,
                x_um=20.0,
                y_um=20.0,
                segments=(
                    ScenarioViaSegment(
                        via_id="V1",
                        padstack="P",
                        drill_diameter_um=1.0,
                        start_layer="TOP",
                        end_layer="PWR_ALT",
                        length_um=1.0,
                        end_x_um=20.0,
                        end_y_um=20.0,
                    ),
                ),
            ),
        ),
    )
    choices = {("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),)}

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,),
        (landing,),
        choices,
        pwr_layer_order={"top": 0, "pwr_alt": 3},
    )

    assert set(result["V1"]) == {"R1"}


def test_batch_via_eligibility_ignores_trace_assisted_path_evidence() -> None:
    """Trace metadata does not alter immutable-XY copper permission."""

    geometry = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V1",
        positive_polygons_um=(
            ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    landing = ScenarioViaLanding(
        via_id="V1",
        net="V1",
        endpoint_node_id="N1",
        padstack="P",
        x_um=5.0,
        y_um=5.0,
        path_evidence=(
            ScenarioViaPathEvidence(
                target_layer="PWR_ALT",
                target_node_id="N2",
                target_padstack="P2",
                target_pad_kind="CIRCLE",
                target_pad_width_um=1.0,
                target_pad_height_um=1.0,
                x_um=5.0,
                y_um=5.0,
                segments=(
                    ScenarioViaSegment(
                        via_id="V1",
                        padstack="P",
                        drill_diameter_um=1.0,
                        start_layer="TOP",
                        end_layer="PWR_ALT",
                        length_um=1.0,
                        end_x_um=5.0,
                        end_y_um=5.0,
                    ),
                ),
                trace_hops=1,
            ),
        ),
    )
    choices = {("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),)}

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,),
        (landing,),
        choices,
        pwr_layer_order={"top": 0, "pwr_alt": 3},
    )

    assert set(result["V1"]) == {"R1"}


def test_batch_via_eligibility_rejects_outer_and_negative_hole_tolerance_boundaries() -> None:
    geometry = SpdPlaneGeometry(
        layer="PWR",
        net="V1",
        positive_polygons_um=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),),
        negative_polygons_um=(((4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)),),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0), ("negative_polygon", 0)),
    )
    rail = _rail("R1")
    choices = {("v1", "pwr", "gnd"): ((rail, "VT1"),)}
    outer_near_edge = ScenarioViaLanding(
        via_id="OUTER", net="V1", endpoint_node_id="N1", padstack="P", x_um=1.0e-7, y_um=5.0
    )
    hole_near_edge = ScenarioViaLanding(
        via_id="HOLE", net="V1", endpoint_node_id="N2", padstack="P", x_um=3.9999995, y_um=5.0
    )

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,), (outer_near_edge, hole_near_edge), choices
    )

    assert result == {"OUTER": {}, "HOLE": {}}


def test_batch_via_eligibility_keeps_all_ground_pairs_for_one_power_plane() -> None:
    geometry = SpdPlaneGeometry(
        layer="PWR",
        net="V1",
        positive_polygons_um=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    r1 = _rail("R1").model_copy(update={"gnd_layer": "GND1"})
    r2 = _rail("R2").model_copy(update={"gnd_layer": "GND2"})
    choices = {
        ("v1", "pwr", "gnd1"): ((r1, "VT1"),),
        ("v1", "pwr", "gnd2"): ((r2, "VT2"),),
    }
    landing = _with_vertical_path(
        ScenarioViaLanding(
            via_id="V1",
            net="V1",
            endpoint_node_id="N1",
            padstack="P",
            x_um=5.0,
            y_um=5.0,
        ),
        "PWR",
    )

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry,), (landing,), choices
    )

    assert set(result["V1"]) == {"R1", "R2"}
    assert result["V1"]["R1"].gnd_layer == "GND1"
    assert result["V1"]["R2"].gnd_layer == "GND2"


def test_batch_via_eligibility_keeps_legacy_off_order_and_protected_mount_side() -> None:
    """OFF keeps v0.21 ordering; ON selects the nearest layer from the mount side."""

    def geometry(layer: str) -> SpdPlaneGeometry:
        return SpdPlaneGeometry(
            layer=layer,
            net="V1",
            positive_polygons_um=(
                ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
            ),
            negative_polygons_um=(),
            positive_circles_um=(),
            negative_circles_um=(),
            primitive_order=(("positive_polygon", 0),),
        )

    landing = _with_vertical_path(
        ScenarioViaLanding(
            via_id="V1",
            net="V1",
            endpoint_node_id="N1",
            padstack="P",
            x_um=5.0,
            y_um=5.0,
        ),
        "PWR_ALT",
    )
    choices = {
        ("v1", "top", "gnd"): ((_rail("R1"), "VT1"),),
        ("v1", "pwr_alt", "gnd"): ((_rail("R1"), "VT1"),),
    }

    result = distribution_module._distribution_batch_via_eligibility(
        (geometry("PWR_ALT"), geometry("TOP")),
        (landing,),
        choices,
        pwr_layer_order={"top": 0, "pwr_alt": 3},
    )

    assert result["V1"]["R1"].pwr_layer == "PWR"
    assert result["V1"]["R1"].destination_pwr_layer == "TOP"
    assert "TOP" in str(result["V1"]["R1"].reason)

    bottom = distribution_module._distribution_batch_via_eligibility(
        (geometry("PWR_ALT"), geometry("TOP")),
        (landing,),
        choices,
        pwr_layer_order={"top": 0, "pwr_alt": 3},
        mount_side_by_via={"v1": "BOTTOM"},
    )

    assert bottom["V1"]["R1"].destination_pwr_layer == "TOP"
    assert "TOP" in str(bottom["V1"]["R1"].reason)

    asset = RoutingObstacleAsset(
        source_sha256="a" * 64,
        stackup_fingerprint="b" * 64,
        conductor_layers=("TOP", "PWR_ALT", "GND"),
        segments=(),
        layer_completeness=tuple(
            RoutingLayerCompleteness(layer=name)
            for name in ("TOP", "PWR_ALT", "GND")
        ),
        via_profiles=(
            PlannedViaProfile(
                profile_id="VT1",
                radius_um_by_layer=(("TOP", 1.0), ("PWR_ALT", 1.0)),
                fallback_barrel_radius_um=1.0,
            ),
        ),
    )
    protected_bottom = distribution_module._distribution_batch_via_eligibility(
        (geometry("PWR_ALT"), geometry("TOP")),
        (landing,),
        choices,
        pwr_layer_order={"top": 0, "pwr_alt": 3},
        routing_asset=asset,
        routing_policy=SignalTraceAvoidancePolicy.fixed(0.0),
        mount_side_by_via={"v1": "BOTTOM"},
    )

    assert protected_bottom["V1"]["R1"].destination_pwr_layer == "PWR_ALT"
    assert "PWR_ALT" in str(protected_bottom["V1"]["R1"].reason)


def test_timeout_incumbent_requires_feasibility_and_full_receiver_demand() -> None:
    bounds = Bounds(np.zeros(2), np.ones(2))
    integrality = np.ones(2, dtype=np.uint8)
    constraints = LinearConstraint(
        np.asarray([[1.0, 1.0]]), np.asarray([1.0]), np.asarray([1.0])
    )
    assert distribution_module._valid_timeout_incumbent(
        np.asarray([1.0, 0.0]),
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        fulfilled_count=2,
        receiver_demand_total=2,
    )
    for candidate in (np.asarray([0.5, 0.5]), np.asarray([1.0, 1.0])):
        assert not distribution_module._valid_timeout_incumbent(
            candidate,
            integrality=integrality,
            bounds=bounds,
            constraints=constraints,
            fulfilled_count=2,
            receiver_demand_total=2,
        )
    assert not distribution_module._valid_timeout_incumbent(
        np.asarray([1.0, 0.0]),
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        fulfilled_count=1,
        receiver_demand_total=2,
    )


def test_shared_chain_full_demand_accepts_feasible_status_one_incumbent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_solver = distribution_module._milp_with_optional_start
    timed_out = False

    def status_one_feasible_incumbent(c, **kwargs):
        nonlocal timed_out
        result = original_solver(c, **kwargs)
        if (
            not timed_out
            and np.any(np.asarray(kwargs["integrality"]) != 0)
                and result.x is not None
        ):
            timed_out = True
            result.status = 1
            result.success = False
            result.message = "fixture feasible fulfillment timeout"
        return result

    monkeypatch.setattr(
        distribution_module, "_milp_with_optional_start", status_one_feasible_incumbent
    )
    plan = compute_distribution_plan(
        _shared_chain_scenario(),
        {("R1", "M1"): 1, ("R2", "M1"): 1},
    )

    assert timed_out
    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 1


def test_secondary_stage_timeout_without_incumbent_retains_validated_prior_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_solver = distribution_module._milp_with_optional_start
    timeout_count = 0
    gap_timeout_active = False

    def timeout_gap_proof_without_incumbent(c, **kwargs):
        nonlocal gap_timeout_active, timeout_count
        objective = np.asarray(c, dtype=float)
        integrality = np.asarray(kwargs["integrality"])
        nonzero = objective[objective != 0.0]
        if (
            np.any(integrality != 0)
            and not np.any(objective != 0.0)
        ):
            gap_timeout_active = True
            timeout_count += 1
            return OptimizeResult(
                status=1,
                success=False,
                message="fixture secondary proof timeout without incumbent",
                x=None,
                fun=None,
            )
        if (
            gap_timeout_active
            and np.any(integrality != 0)
            and kwargs.get("start") is not None
            and bool(nonzero.size)
            and np.all(nonzero == 1.0)
        ):
            timeout_count += 1
            return OptimizeResult(
                status=1,
                success=False,
                message="fixture exact gap timeout without incumbent",
                x=None,
                fun=None,
            )
        return original_solver(c, **kwargs)

    monkeypatch.setattr(
        distribution_module,
        "_milp_with_optional_start",
        timeout_gap_proof_without_incumbent,
    )
    plan = compute_distribution_plan(
        _shared_chain_scenario(),
        {("R1", "M1"): 1, ("R2", "M1"): 1},
        optimization_policy=DistributionOptimizationPolicy.MIN_GAPS,
    )

    assert timeout_count == 2
    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 1
    assert any(
        item.code == "GAP_OPTIMIZATION_FALLBACK" for item in plan.diagnostics
    )


def test_gap_deadline_without_result_retains_exact_validated_prior_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _shared_chain_scenario()
    original_solver = distribution_module._milp_with_optional_start
    clock = 0.0
    gap_probe_exhausted_deadline = False

    def fake_monotonic() -> float:
        return clock

    def exhaust_deadline_during_gap_probe(c, **kwargs):
        nonlocal clock, gap_probe_exhausted_deadline
        objective = np.asarray(c, dtype=float)
        integrality = np.asarray(kwargs["integrality"])
        if (
            not gap_probe_exhausted_deadline
            and np.any(integrality != 0)
            and not np.any(objective != 0.0)
        ):
            gap_probe_exhausted_deadline = True
            # The gap stage has a 30-second deadline for this small fixture.
            # Advancing beyond it leaves `result` as None, which is the exact
            # path observed in the real replay rather than a returned timeout
            # result with x=None.
            clock = 100.0
            return OptimizeResult(
                status=1,
                success=False,
                message="fixture gap proof exhausted the block deadline",
                x=None,
                fun=None,
            )
        return original_solver(c, **kwargs)

    monkeypatch.setattr(distribution_module, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        distribution_module,
        "_milp_with_optional_start",
        exhaust_deadline_during_gap_probe,
    )

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 1},
        optimization_policy=DistributionOptimizationPolicy.MIN_GAPS,
    )

    assert gap_probe_exhausted_deadline
    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 1
    assert plan.assignment_map == {"A0": "R2"}
    assert plan.isolation_gap_refdes == ("D1",)
    assert any(
        item.code == "GAP_OPTIMIZATION_FALLBACK" for item in plan.diagnostics
    )

    applied = apply_distribution_plan(scenario, plan)
    by_refdes = {item.refdes: item for item in applied.decaps}
    assert by_refdes["A0"].current_rail_id == "R2"
    assert by_refdes["D1"].pad_state.value == "ISOLATION_GAP"
    assert applied.design_fingerprint == plan.output_design_fingerprint


def test_distribution_projection_uses_unselected_internal_power_plane_for_direct_donor(
    tmp_path: Path,
) -> None:
    """Distribution must not discard a real lower plane because Evaluation chose TOP."""

    scenario = _direct_scenario(
        (("C1", 5.0, ("R1",)),),
        rail_ids=("R1", "R2"),
        bump_x={"R1": 0.0, "R2": 10.0},
    )
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": [
                StackupLayer(
                    name="TOP",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V1", "V2"],
                ),
                StackupLayer(name="D1", thickness_um=20.0, dk=4.0, df=0.01),
                StackupLayer(
                    name="GND1",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["DGND"],
                ),
                StackupLayer(name="D_MID", thickness_um=20.0, dk=4.0, df=0.01),
                StackupLayer(
                    name="PWR_ALT",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V2"],
                ),
                StackupLayer(name="D2", thickness_um=20.0, dk=4.0, df=0.01),
                StackupLayer(
                    name="GND_ALT",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["DGND"],
                ),
            ],
            "rails": [
                rail.model_copy(update={"pwr_layer": "TOP", "gnd_layer": "GND1"})
                for rail in scenario.base_project.rails
            ],
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
        }
    )
    scenario = _with_power_via_path(scenario, "C1", "PWR_ALT")
    alternate = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}

    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(alternate,),
        targets=targets,
    )

    assert projection is not None
    projected = projection.projected_decaps[0].eligibility["R2"]
    assert projected.pwr_layer == "TOP"
    assert projected.gnd_layer == "GND1"
    assert projected.destination_pwr_layer == "PWR_ALT"
    assert "PWR_ALT" in str(projected.reason)
    plan = compute_distribution_plan(
        scenario,
        targets,
        power_projection=projection,
    )
    assert plan.status == DistributionPlanStatus.FULL
    assert plan.assignment_map == {"C1": "R2"}
    applied = apply_distribution_plan(
        scenario,
        plan,
        power_projection=projection,
    )
    archive = save_scenario(applied, tmp_path / "projected-exact-layer.spdpi")
    restored = load_scenario(archive)
    assert restored.decaps[0].current_rail_id == "R2"
    assert (
        restored.decaps[0].eligibility["R2"].destination_pwr_layer
        == "PWR_ALT"
    )


def test_distribution_plane_data_is_immutable_after_projection_plan_apply() -> None:
    """Distribution relabels decaps only; it never edits the SPD plane assets."""

    scenario = _direct_scenario(
        (("C1", 5.0, ("R1",)),),
        rail_ids=("R1", "R2"),
        bump_x={"R1": 0.0, "R2": 10.0},
    )
    plane_records = [
        {
            "layer": "PWR_ALT",
            "net": "V2",
            "asset": "planes/pwr-alt.json.z",
            "asset_sha256": "a" * 64,
        }
    ]
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": [
                StackupLayer(
                    name="TOP",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V1", "V2"],
                ),
                StackupLayer(name="D1", thickness_um=20.0, dk=4.0, df=0.01),
                StackupLayer(
                    name="GND1",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["DGND"],
                ),
                StackupLayer(
                    name="PWR_ALT",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V2"],
                ),
            ],
            "rails": [
                rail.model_copy(update={"pwr_layer": "TOP", "gnd_layer": "GND1"})
                for rail in scenario.base_project.rails
            ],
            "metadata": {"spd_import": {"plane_geometries": plane_records}},
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
        }
    )
    scenario = _with_power_via_path(scenario, "C1", "PWR_ALT")
    alternate = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V2",
        positive_polygons_um=(
            ((0.0, -5.0), (10.0, -5.0), (10.0, 5.0), (0.0, 5.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    targets = {("R1", "M1"): 0, ("R2", "M1"): 1}
    stackup_before = json.dumps(
        [item.model_dump(mode="json") for item in scenario.base_project.stackup_layers],
        sort_keys=True,
    )
    planes_before = json.dumps(
        scenario.base_project.metadata["spd_import"]["plane_geometries"],
        sort_keys=True,
    )
    attachment_hashes_before = tuple(
        item["asset_sha256"]
        for item in scenario.base_project.metadata["spd_import"]["plane_geometries"]
    )
    ground_before = tuple(
        connection.ground_vias
        for connection in scenario.connection_analysis.connections.values()
    )

    projection = distribution_module.build_distribution_power_projection(
        scenario, {}, plane_geometries=(alternate,), targets=targets
    )
    assert projection is not None
    plan = compute_distribution_plan(scenario, targets, power_projection=projection)
    result = apply_distribution_plan(scenario, plan, power_projection=projection)

    assert plan.status == DistributionPlanStatus.FULL
    assert result.decaps[0].current_rail_id == "R2"
    assert json.dumps(
        [item.model_dump(mode="json") for item in result.base_project.stackup_layers],
        sort_keys=True,
    ) == stackup_before
    assert json.dumps(
        result.base_project.metadata["spd_import"]["plane_geometries"],
        sort_keys=True,
    ) == planes_before
    assert tuple(
        item["asset_sha256"]
        for item in result.base_project.metadata["spd_import"]["plane_geometries"]
    ) == attachment_hashes_before
    assert tuple(
        connection.ground_vias
        for connection in result.connection_analysis.connections.values()
    ) == ground_before
    assert result.decaps[0].pwr_pad == scenario.decaps[0].pwr_pad
    assert result.decaps[0].gnd_pad == scenario.decaps[0].gnd_pad
    assert result.decaps[0].attach_layer == scenario.decaps[0].attach_layer


def test_distribution_plane_geometry_uses_partition_fallback_per_missing_pair() -> None:
    """A valid retained asset must not hide another pair's exact partition art."""

    def source_cell(cell_id: str, net: str, x_min_um: float) -> PlaneCell:
        polygon = [
            (x_min_um, -5.0),
            (x_min_um + 5.0, -5.0),
            (x_min_um + 5.0, 5.0),
            (x_min_um, 5.0),
        ]
        return PlaneCell(
            cell_id=cell_id,
            row=0,
            column=0,
            x_min_um=x_min_um,
            x_max_um=x_min_um + 5.0,
            y_min_um=-5.0,
            y_max_um=5.0,
            source_net=net,
            source_positive_polygons_um=[polygon],
            source_primitive_order=[("positive_polygon", 0)],
            solver_geometry="spd_axis_aligned_rectangle",
        )

    # PWR/V1 is present as an authoritative retained asset.  Its partition
    # geometry is intentionally different so a fallback overwrite is visible.
    retained_content, _ = core_services._compress_spd_geometry_payload(
        layer="PWR",
        net="V1",
        positive_polygons=(((100.0, -5.0), (105.0, -5.0), (105.0, 5.0), (100.0, 5.0)),),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=1,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )
    retained_digest = sha256(retained_content).hexdigest()
    scenario = _direct_scenario((("C1", 0.0, ("R1", "R2")),), rail_ids=("R1", "R2"))
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": [
                *scenario.base_project.stackup_layers,
                StackupLayer(
                    name="PWR_ALT",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V2"],
                ),
            ],
            "rails": [
                rail.model_copy(
                    update={"pwr_layer": "PWR_ALT"}
                    if rail.rail_id == "R2"
                    else {}
                )
                for rail in scenario.base_project.rails
            ],
            "metadata": {
                "spd_import": {
                    "plane_geometries": [
                        {
                            "layer": "PWR",
                            "net": "V1",
                            "asset": "geometry/v1.spdgeom.zlib",
                            "asset_sha256": retained_digest,
                        }
                    ]
                }
            },
            "partitions": [
                PlanePartitionSpec(
                    layer="PWR",
                    rows=1,
                    columns=1,
                    domain_to_cell={"R1": "PWR-V1"},
                    cells=[source_cell("PWR-V1", "V1", 0.0)],
                    split_gap_um=0.0,
                    confidence=ConfidenceLevel.HIGH,
                    method="spd_actual",
                ),
                PlanePartitionSpec(
                    layer="PWR_ALT",
                    rows=1,
                    columns=1,
                    domain_to_cell={"R2": "PWR-ALT-V2"},
                    cells=[source_cell("PWR-ALT-V2", "V2", 20.0)],
                    split_gap_um=0.0,
                    confidence=ConfidenceLevel.HIGH,
                    method="spd_actual",
                ),
            ],
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
        }
    )

    planes = distribution_module._distribution_plane_geometries(
        scenario,
        {"geometry/v1.spdgeom.zlib": retained_content},
        wanted_pairs={("v1", "pwr"), ("v2", "pwr_alt")},
    )

    assert [(item.net, item.layer) for item in planes] == [
        ("V1", "PWR"),
        ("V2", "PWR_ALT"),
    ]
    # The valid retained geometry wins for its pair.
    assert planes[0].positive_polygons_um[0][0] == (100.0, -5.0)
    # The unrelated missing pair still receives its exact partition fallback.
    assert planes[1].positive_polygons_um[0][0] == (20.0, -5.0)


def test_distribution_plane_geometry_uses_partition_when_metadata_is_malformed() -> None:
    """A bad digest is unavailable metadata, not a reason to suppress fallback."""

    scenario = _direct_scenario((("C1", 0.0, ("R1",)),), rail_ids=("R1",))
    cell = PlaneCell(
        cell_id="PWR-V1",
        row=0,
        column=0,
        x_min_um=0.0,
        x_max_um=5.0,
        y_min_um=-5.0,
        y_max_um=5.0,
        source_net="V1",
        source_positive_polygons_um=[
            [(0.0, -5.0), (5.0, -5.0), (5.0, 5.0), (0.0, 5.0)]
        ],
        source_primitive_order=[("positive_polygon", 0)],
        solver_geometry="spd_axis_aligned_rectangle",
    )
    project = scenario.base_project.model_copy(
        update={
            "metadata": {
                "spd_import": {
                    "plane_geometries": [
                        {
                            "layer": "PWR",
                            "net": "V1",
                            "asset": "geometry/bad.spdgeom.zlib",
                            "asset_sha256": "0" * 64,
                        }
                    ]
                }
            },
            "partitions": [
                PlanePartitionSpec(
                    layer="PWR",
                    rows=1,
                    columns=1,
                    domain_to_cell={"R1": "PWR-V1"},
                    cells=[cell],
                    split_gap_um=0.0,
                    confidence=ConfidenceLevel.HIGH,
                    method="spd_actual",
                )
            ],
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
        }
    )

    planes = distribution_module._distribution_plane_geometries(
        scenario,
        {"geometry/bad.spdgeom.zlib": b"not-a-valid-geometry-asset"},
        wanted_pairs={("v1", "pwr")},
    )

    assert len(planes) == 1
    assert planes[0].positive_polygons_um[0][0] == (0.0, -5.0)


def test_missing_target_bump_uses_canonical_distribution_distance_fallback() -> None:
    """Exact target copper remains assignable when bump data is absent."""

    scenario = _direct_scenario(
        (("C1", 5.0, ("R1", "R2")),),
        rail_ids=("R1", "R2"),
        bump_x={"R1": 0.0},
    )

    plan = compute_distribution_plan(
        scenario, {("R1", "M1"): 0, ("R2", "M1"): 1}
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.assignment_map == {"C1": "R2"}
    assert plan.moves[0].bump_distance_um == 0.0
    assert any(
        item.code == "MISSING_TARGET_BUMP_CANONICAL_FALLBACK"
        for item in plan.diagnostics
    )


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
    assert distribution_assignable_counts(scenario) == {
        ("R1", "M1"): 1,
        ("R2", "M1"): 0,
    }
    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 2, ("R2", "M1"): 1},
    )
    assert plan.assignment_map == {"MOVABLE": "R2"}
    assert plan.status == DistributionPlanStatus.FULL
    assert _cell(plan, "R1").actual_count == 2
    assert _cell(plan, "R2").actual_count == 1
    evaluation_blocker = next(
        item
        for item in plan.diagnostics
        if item.code == "PREEXISTING_UNRESOLVED_EVALUATION_RAILS"
    )
    assert evaluation_blocker.rail_id == "R1"
    assert evaluation_blocker.requested_count == 1
    assert evaluation_blocker.actual_count == 0
    assert "count/topology preview is valid" in evaluation_blocker.message
    assert "PDN evaluation remains blocked" in evaluation_blocker.message
    assert "UNRESOLVED" in evaluation_blocker.message
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
    headers, rows = distribution_target_table(plan)
    assert "M1\nAssignment Failed" in headers
    failed_column = headers.index("M1\nAssignment Failed")
    assert next(row for row in rows if row[0] == "V1 (R1)")[failed_column] == 0
    assert next(row for row in rows if row[0] == "V2 (R2)")[failed_column] == 1
    applied = apply_distribution_plan(scenario, plan)
    assert applied.revision == scenario.revision + 1
    assert next(item for item in applied.decaps if item.refdes == "C1").current_rail_id == "R2"


def test_missing_bump_keeps_exact_plane_assignment_eligible() -> None:
    scenario = _direct_scenario(
        (("C1", 0.0, ("R1", "R2")),),
        rail_ids=("R1", "R2"),
        bump_x={},
    )

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 0, ("R2", "M1"): 1},
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 1
    assert [item.refdes for item in plan.moves] == ["C1"]
    assert any(
        item.code == "MISSING_TARGET_BUMP_CANONICAL_FALLBACK"
        for item in plan.diagnostics
    )


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


def test_shared_component_needs_one_eligible_physical_power_via_root() -> None:
    """One valid Via roots the component; sibling Vias need not hit the plane."""

    scenario = _shared_chain_scenario()
    assert scenario.connection_analysis is not None
    connections = dict(scenario.connection_analysis.connections)
    a0 = connections["A0"]
    connections["A0"] = a0.model_copy(
        update={
            "power_vias": (
                *a0.power_vias,
                _via("VP0-BLIND", "V1", 1.0),
            )
        }
    )
    cluster = scenario.connection_analysis.clusters[0]
    via_eligibility = dict(cluster.via_eligibility)
    via_eligibility["VP0-BLIND"] = {"R1": _eligibility("R1")}
    scenario = scenario.model_copy(
        update={
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={
                    "connections": connections,
                    "clusters": (
                        cluster.model_copy(
                            update={"via_eligibility": via_eligibility}
                        ),
                    ),
                }
            )
        }
    )

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 0, ("R2", "M1"): 3},
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert plan.fulfilled_count == 3
    changed = apply_distribution_plan(scenario, plan)
    assert {item.current_rail_id for item in changed.decaps} == {"R2"}


@pytest.mark.parametrize(
    ("mode", "expected_refdes"),
    (
        (DistributionDistanceMode.NEAREST, "A0"),
        (DistributionDistanceMode.FARTHEST, "A2"),
    ),
)
def test_fixed_separator_distance_fallback_still_honors_mode(
    monkeypatch: pytest.MonkeyPatch,
    mode: DistributionDistanceMode,
    expected_refdes: str,
) -> None:
    scenario = _shared_chain_scenario()
    original_solver = distribution_module._milp_with_optional_start
    distance_call_count = 0

    def joint_timeout_then_fixed_incumbent(c, **kwargs):
        nonzero = np.asarray(c)[np.asarray(c) != 0.0]
        is_distance_objective = bool(nonzero.size) and not bool(
            np.all(np.abs(nonzero) == 1.0)
        )
        if not is_distance_objective:
            return original_solver(c, **kwargs)

        nonlocal distance_call_count
        distance_call_count += 1
        if distance_call_count == 1:
            # Simulate the real failure mode: the joint assignment/separator
            # distance stage expires without returning an incumbent.
            return OptimizeResult(
                status=1,
                success=False,
                message="fixture joint distance timeout",
                x=None,
                fun=None,
            )

        # The fixed-separator stage does find a valid mode-specific incumbent,
        # but reaches its time limit before proving the conditional optimum.
        result = original_solver(c, **kwargs)
        result.status = 1
        result.success = False
        result.message = "fixture fixed-separator distance timeout"
        return result

    monkeypatch.setattr(
        distribution_module,
        "_milp_with_optional_start",
        joint_timeout_then_fixed_incumbent,
    )

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 2},
        mode,
        optimization_policy="MIN_GAPS",
    )

    assert distance_call_count == 2
    assert plan.moves[0].refdes == expected_refdes
    assert plan.isolation_gap_refdes == ("D1",)
    diagnostic_codes = {item.code for item in plan.diagnostics}
    assert "DISTANCE_JOINT_OPTIMIZATION_DEFERRED" in diagnostic_codes
    assert "DISTANCE_FIXED_SEPARATOR_FALLBACK" in diagnostic_codes
    assert "DISTANCE_OPTIMIZATION_FALLBACK" not in diagnostic_codes


@pytest.mark.parametrize(
    ("optimization_policy", "gap_penalty_um", "expected_distance_calls"),
    (
        (DistributionOptimizationPolicy.BALANCED_AUTO, None, 1),
        (DistributionOptimizationPolicy.BALANCED_CUSTOM, 1_000.0, 1),
        (DistributionOptimizationPolicy.MIN_GAPS, None, 2),
    ),
)
def test_distance_no_incumbent_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    optimization_policy: DistributionOptimizationPolicy,
    gap_penalty_um: float | None,
    expected_distance_calls: int,
) -> None:
    original_solver = distribution_module._milp_with_optional_start
    distance_call_count = 0

    def no_distance_incumbent(c, **kwargs):
        nonlocal distance_call_count
        objective = np.asarray(c, dtype=float)
        nonzero = objective[objective != 0.0]
        is_distance_objective = bool(nonzero.size) and not bool(
            np.all(np.abs(nonzero) == 1.0)
        )
        if not is_distance_objective:
            return original_solver(c, **kwargs)
        distance_call_count += 1
        return OptimizeResult(
            status=1,
            success=False,
            message="fixture distance timeout without incumbent",
            x=None,
            fun=None,
        )

    monkeypatch.setattr(
        distribution_module,
        "_milp_with_optional_start",
        no_distance_incumbent,
    )

    with pytest.raises(DistributionError) as caught:
        compute_distribution_plan(
            _shared_chain_scenario(),
            {("R1", "M1"): 1, ("R2", "M1"): 2},
            optimization_policy=optimization_policy,
            gap_penalty_um=gap_penalty_um,
        )

    assert distance_call_count == expected_distance_calls
    assert caught.value.code == "DISTANCE_OPTIMIZER_FAILED"
    assert "no arbitrary assignment was emitted" in str(caught.value)


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
    assert r2_row[4:] == (0, 0, 1)


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


def test_lazy_cut_rejects_a_stale_prior_milp_incumbent() -> None:
    start = np.asarray([1.0, 0.0])
    bounds = Bounds(np.zeros(2), np.ones(2))
    integrality = np.ones(2, dtype=np.uint8)
    original = LinearConstraint(
        csr_matrix([[1.0, 1.0]]), np.asarray([1.0]), np.asarray([1.0])
    )
    with_lazy_cut = LinearConstraint(
        csr_matrix([[1.0, 1.0], [1.0, 0.0]]),
        np.asarray([1.0, -np.inf]),
        np.asarray([1.0, 0.0]),
    )

    assert _is_feasible_milp_start(
        start,
        integrality=integrality,
        bounds=bounds,
        constraints=original,
    )
    assert not _is_feasible_milp_start(
        start,
        integrality=integrality,
        bounds=bounds,
        constraints=with_lazy_cut,
    )


def test_fixed_assignment_gap_refinement_preserves_preexisting_gap() -> None:
    payload = _shared_chain_scenario().model_dump(mode="python")
    d1 = next(item for item in payload["decaps"] if item["refdes"] == "D1")
    d1["enabled"] = False
    d1["pad_state"] = "ISOLATION_GAP"
    scenario = ScenarioSpec.model_validate(payload)

    plan = compute_distribution_plan(
        scenario,
        {("R1", "M1"): 1, ("R2", "M1"): 1},
    )

    assert plan.status == DistributionPlanStatus.FULL
    assert len(plan.moves) == 1
    assert plan.isolation_gap_refdes == ()
    changed = apply_distribution_plan(scenario, plan)
    gap = next(item for item in changed.decaps if item.refdes == "D1")
    assert gap.pad_state.value == "ISOLATION_GAP"
    assert not gap.enabled


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
        "M1\nAssignment Failed",
        "M1\nIsolation Gaps",
    )
    assert target_rows == (
        ("V1 (R1)", 2, 1, 0.0, -1, 0, 0),
        ("V2 (R2)", 0, 1, 0.0, 1, 0, 0),
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


def test_projection_proves_existing_exchange_site_for_next_receiver() -> None:
    """Every hop needs proof, including an exchange site's next receiver."""

    scenario = _with_initial_rails(
        _direct_scenario(
            (
                ("A0", 0.0, ("R1", "R2")),
                ("B0", 100.0, ("R2",)),
            ),
            rail_ids=("R1", "R2", "R3"),
            bump_x={"R2": 0.0, "R3": 100.0},
        ),
        {"B0": "R2"},
    )
    project = scenario.base_project.model_copy(
        update={
            "stackup_layers": [
                *scenario.base_project.stackup_layers,
                StackupLayer(
                    name="PWR_ALT",
                    thickness_um=18.0,
                    conductivity_s_m=5.8e7,
                    pwr_nets=["V3"],
                ),
            ]
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
        }
    )
    # Both hops now require target-independent raw-SPD PWR column proof.
    # A0 proves its existing PWR landing reaches R2; B0 proves the alternate
    # R3 plane.  Cached rail eligibility alone is intentionally insufficient.
    scenario = _with_power_via_path(scenario, "A0", "PWR")
    scenario = _with_power_via_path(scenario, "B0", "PWR_ALT")
    r2_plane = SpdPlaneGeometry(
        layer="PWR",
        net="V2",
        positive_polygons_um=(
            ((-10.0, -5.0), (10.0, -5.0), (10.0, 5.0), (-10.0, 5.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    r3_plane = SpdPlaneGeometry(
        layer="PWR_ALT",
        net="V3",
        positive_polygons_um=(
            ((90.0, -5.0), (110.0, -5.0), (110.0, 5.0), (90.0, 5.0)),
        ),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
    )
    targets = {
        ("R1", "M1"): 0,
        ("R2", "M1"): 1,
        ("R3", "M1"): 1,
    }
    tolerances = {("R2", "M1"): 100.0}

    without_projection = compute_distribution_plan(
        scenario, targets, tolerances=tolerances
    )
    projection = distribution_module.build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=(r2_plane, r3_plane),
        targets=targets,
        tolerances=tolerances,
    )
    assert projection is not None
    projected_b0 = next(
        item for item in projection.projected_decaps if item.refdes == "B0"
    )
    projected_a0 = next(
        item for item in projection.projected_decaps if item.refdes == "A0"
    )
    assert projected_a0.eligibility["R2"].allowed
    assert projected_b0.eligibility["R3"].allowed
    with_projection = compute_distribution_plan(
        scenario,
        targets,
        tolerances=tolerances,
        power_projection=projection,
    )

    assert without_projection.status == DistributionPlanStatus.PARTIAL
    assert without_projection.fulfilled_count == 0
    assert with_projection.status == DistributionPlanStatus.FULL
    assert with_projection.fulfilled_count == 1
    assert {item.previous_rail_id for item in with_projection.moves} == {"R1", "R2"}
    assert {item.new_rail_id for item in with_projection.moves} == {"R2", "R3"}


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
