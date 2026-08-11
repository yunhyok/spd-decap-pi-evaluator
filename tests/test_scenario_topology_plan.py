from __future__ import annotations

from dataclasses import replace

import pytest

from spd_decap_pi.scenario import (
    SHARED_PAD_ANALYSIS_VERSION,
    DecapConnectionKind,
    DecapPadState,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioViaLanding,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadConnectionAnalysis,
)
from spd_decap_pi.scenario_topology_plan import (
    RetargetRouteEvidenceSet,
    ScenarioTopologyPlanError,
    SourceContactDisposition,
    SourceContactEvidenceSet,
    SourceViaContactEvidence,
    TargetRouteDecision,
    TopologyLinkKind,
    compile_scenario_topology_plan,
    connection_analysis_evidence_sha256,
)


SOURCE_SHA = "a" * 64
ROUTE_CERT_SHA = "b" * 64
CONTACT_SHA = "c" * 64
TARGET_SHA = "d" * 64


def _landing(refdes: str, terminal: str, suffix: str = "") -> ScenarioViaLanding:
    return ScenarioViaLanding(
        via_id=f"V-{terminal}-{refdes}{suffix}",
        net="VDD1" if terminal == "PWR" else "DGND",
        endpoint_node_id=f"N-{terminal}-{refdes}{suffix}",
        padstack="VIA",
        x_um=100.0,
        y_um=200.0,
    )


def _decap(
    refdes: str,
    *,
    source_rail: str = "R1",
    source_net: str = "VDD1",
    current_rail: str | None = None,
    current_net: str | None = None,
    enabled: bool = True,
    pad_state: DecapPadState = DecapPadState.NORMAL,
) -> ScenarioDecap:
    return ScenarioDecap(
        refdes=refdes,
        center=ScenarioPoint(x_um=100.0, y_um=200.0),
        pwr_pad=ScenarioPad(
            x_um=90.0, y_um=200.0, layer="TOP", padstack="PWR"
        ),
        gnd_pad=ScenarioPad(
            x_um=110.0, y_um=200.0, layer="TOP", padstack="GND"
        ),
        footprint="0402",
        source_net=source_net,
        current_net=current_net or source_net,
        source_rail_id=source_rail,
        current_rail_id=current_rail or source_rail,
        source_model_id="M1",
        model_id="M1",
        enabled=enabled,
        pad_state=pad_state,
        source_mounted=True,
    )


def _direct_connection(
    refdes: str,
    *,
    power_count: int = 1,
) -> ScenarioDecapConnection:
    return ScenarioDecapConnection(
        refdes=refdes,
        kind=DecapConnectionKind.DIRECT,
        power_vias=tuple(
            _landing(refdes, "PWR", f"-{index}")
            for index in range(1, power_count + 1)
        ),
        ground_vias=(_landing(refdes, "GND"),),
    )


def _analysis(
    connections: dict[str, ScenarioDecapConnection],
    clusters: tuple[SharedPadCluster, ...] = (),
) -> SharedPadConnectionAnalysis:
    return SharedPadConnectionAnalysis(
        version=SHARED_PAD_ANALYSIS_VERSION,
        source_sha256=SOURCE_SHA,
        connections=connections,
        clusters=clusters,
    )


def _source_contacts(
    analysis: SharedPadConnectionAnalysis,
    *,
    reverse: bool = False,
) -> SourceContactEvidenceSet:
    contacts: list[SourceViaContactEvidence] = []
    seen: set[str] = set()
    for connection in analysis.connections.values():
        for terminal, landings in (
            ("PWR", connection.power_vias),
            ("GND", connection.ground_vias),
        ):
            for landing in landings:
                key = landing.via_id.casefold()
                if key in seen:
                    continue
                seen.add(key)
                contacts.append(
                    SourceViaContactEvidence(
                        via_id=landing.via_id,
                        terminal=terminal,
                        source_refdes=connection.refdes,
                        exposed_node_id=f"base:top:{landing.via_id}",
                        contact_owner_id=f"owner:{landing.via_id}",
                        cut_link_id=f"cut:{landing.via_id}",
                        cut_owner_id=f"via:{landing.via_id}",
                        contact_evidence_sha256=CONTACT_SHA,
                    )
                )
    if reverse:
        contacts.reverse()
    return SourceContactEvidenceSet.create(
        source_sha256=SOURCE_SHA,
        connection_evidence_sha256=connection_analysis_evidence_sha256(analysis),
        finite_route_certificate_sha256=ROUTE_CERT_SHA,
        contacts=contacts,
    )


def _targets(
    analysis: SharedPadConnectionAnalysis,
    decisions: list[TargetRouteDecision] | None = None,
    *,
    reverse: bool = False,
) -> RetargetRouteEvidenceSet:
    values = list(decisions or [])
    if reverse:
        values.reverse()
    return RetargetRouteEvidenceSet.create(
        source_sha256=SOURCE_SHA,
        connection_evidence_sha256=connection_analysis_evidence_sha256(analysis),
        finite_route_certificate_sha256=ROUTE_CERT_SHA,
        decisions=values,
    )


def _eligible(via_id: str, rail_id: str = "R2") -> TargetRouteDecision:
    return TargetRouteDecision(
        via_id=via_id,
        rail_id=rail_id,
        eligible=True,
        target_node_id=f"surface:{rail_id}:{via_id}",
        route_id=f"retarget:{rail_id}:{via_id}",
        route_owner_id=f"via:{via_id}",
        target_net="VDD2",
        target_layer="PWR2",
        via_template_id="VT2",
        resistance_ohm=0.012,
        inductance_h=0.44e-9,
        route_evidence_sha256=TARGET_SHA,
    )


def _ineligible(via_id: str, rail_id: str = "R2") -> TargetRouteDecision:
    return TargetRouteDecision(
        via_id=via_id,
        rail_id=rail_id,
        eligible=False,
        reason="target copper does not contain this root",
    )


def _compile(
    decaps: dict[str, ScenarioDecap],
    analysis: SharedPadConnectionAnalysis,
    decisions: list[TargetRouteDecision] | None = None,
):
    return compile_scenario_topology_plan(
        decap_by_refdes=decaps,
        connection_analysis=analysis,
        source_contacts=_source_contacts(analysis),
        retarget_routes=_targets(analysis, decisions),
    )


def test_direct_multi_via_move_suppresses_only_eligible_cut_but_removes_all_source_contacts() -> None:
    decap = _decap(
        "C1", current_rail="R2", current_net="VDD2"
    )
    connection = _direct_connection("C1", power_count=2)
    analysis = _analysis({"C1": connection})
    first, second = (landing.via_id for landing in connection.power_vias)

    plan = _compile(
        {"C1": decap},
        analysis,
        [_eligible(first), _ineligible(second)],
    )

    assert len(plan.active_retarget_routes) == 1
    assert plan.active_retarget_routes[0].via_id == first
    assert plan.active_retarget_routes[0].route_owner_id == f"via:{first}"
    assert plan.suppressed_base_cut_ids == (f"cut:{first}",)
    assert not any(
        link.kind == TopologyLinkKind.SOURCE_PWR_CONTACT
        for link in plan.active_topology_links
    )
    assert sum(
        link.kind == TopologyLinkKind.SOURCE_GND_CONTACT
        for link in plan.active_topology_links
    ) == 1
    assert len(plan.cap_body_requests) == 1
    assert {
        item.via_id: item.disposition
        for item in plan.source_owner_partition
        if item.terminal == "PWR"
    } == {
        first: SourceContactDisposition.SUPPRESSED_FOR_RETARGET,
        second: (
            SourceContactDisposition.MOVED_CONTACT_REMOVED_BASE_RETAINED
        ),
    }
    assert {
        item.via_id: item.cut_owner_id
        for item in plan.source_owner_partition
        if item.terminal == "PWR"
    } == {first: f"via:{first}", second: f"via:{second}"}


def _shared_fixture(
    *,
    gap_middle: bool = False,
    middle_enabled: bool = False,
    moved: bool = False,
    two_anchors: bool = True,
    unresolved: bool = False,
) -> tuple[
    dict[str, ScenarioDecap],
    SharedPadConnectionAnalysis,
    list[TargetRouteDecision],
]:
    current_rail = "R2" if moved else "R1"
    current_net = "VDD2" if moved else "VDD1"
    decaps = {
        "A": _decap("A", current_rail=current_rail, current_net=current_net),
        "B": _decap(
            "B",
            current_rail=current_rail,
            current_net=current_net,
            enabled=middle_enabled,
            pad_state=(
                DecapPadState.ISOLATION_GAP
                if gap_middle
                else DecapPadState.NORMAL
            ),
        ),
        "C": _decap("C", current_rail=current_rail, current_net=current_net),
    }
    a_power = _landing("A", "PWR")
    a_ground = _landing("A", "GND")
    c_power = (_landing("C", "PWR"),) if two_anchors else ()
    c_ground = (_landing("C", "GND"),) if two_anchors else ()
    unresolved_reason = (
        "source rail is not present beneath every exact PWR Via landing"
        if unresolved
        else None
    )
    connections = {
        "A": ScenarioDecapConnection(
            refdes="A",
            kind=(
                DecapConnectionKind.UNRESOLVED
                if unresolved
                else DecapConnectionKind.SHARED_ANCHOR
            ),
            cluster_id="CL1",
            power_vias=(a_power,),
            ground_vias=(a_ground,),
            reason=unresolved_reason,
        ),
        "B": ScenarioDecapConnection(
            refdes="B",
            kind=(
                DecapConnectionKind.UNRESOLVED
                if unresolved
                else DecapConnectionKind.SHARED_DUMMY
            ),
            cluster_id="CL1",
            reason=unresolved_reason,
        ),
        "C": ScenarioDecapConnection(
            refdes="C",
            kind=(
                DecapConnectionKind.UNRESOLVED
                if unresolved
                else (
                    DecapConnectionKind.SHARED_ANCHOR
                    if two_anchors
                    else DecapConnectionKind.SHARED_DUMMY
                )
            ),
            cluster_id="CL1",
            power_vias=c_power,
            ground_vias=c_ground,
            reason=unresolved_reason,
        ),
    }
    anchors = ("A", "C") if two_anchors else ("A",)
    dummies = ("B",) if two_anchors else ("B", "C")
    chain = (("A", "B"), ("B", "C"))
    cluster = SharedPadCluster(
        cluster_id="CL1",
        state=(
            SharedPadClusterState.UNRESOLVED
            if unresolved
            else SharedPadClusterState.ANCHORED
        ),
        member_refdes=("A", "B", "C"),
        anchor_refdes=anchors,
        dummy_refdes=dummies,
        power_net="VDD1",
        ground_net="DGND",
        layer="TOP",
        power_edges=chain,
        ground_edges=chain,
        isolation_gap_refdes=() if unresolved else ("B",),
        reason=unresolved_reason,
    )
    analysis = _analysis(connections, (cluster,))
    decisions = (
        [_eligible(a_power.via_id)]
        + ([_eligible(c_power[0].via_id)] if c_power else [])
        if moved
        else []
    )
    return decaps, analysis, decisions


def test_shared_moved_component_suppresses_only_eligible_root_and_retains_ineligible_base_owner() -> None:
    decaps, analysis, decisions = _shared_fixture(
        moved=True, two_anchors=True
    )
    first, second = (item.via_id for item in decisions)
    decisions = [_eligible(first), _ineligible(second)]

    plan = _compile(decaps, analysis, decisions)

    assert len(plan.active_retarget_routes) == 1
    assert plan.active_retarget_routes[0].via_id == first
    assert plan.suppressed_base_cut_ids == (f"cut:{first}",)
    assert not any(
        link.kind == TopologyLinkKind.SOURCE_PWR_CONTACT
        for link in plan.active_topology_links
    )
    assert sum(
        link.kind == TopologyLinkKind.SHARED_PWR_PAD
        for link in plan.active_topology_links
    ) == 2
    assert {
        item.via_id: item.disposition
        for item in plan.source_owner_partition
        if item.terminal == "PWR"
    } == {
        first: SourceContactDisposition.SUPPRESSED_FOR_RETARGET,
        second: (
            SourceContactDisposition.MOVED_CONTACT_REMOVED_BASE_RETAINED
        ),
    }


def test_unchanged_source_unresolved_shared_cluster_uses_exact_v4_contacts() -> None:
    decaps, analysis, _decisions = _shared_fixture(unresolved=True)

    plan = _compile(decaps, analysis)

    assert {item.refdes for item in plan.cap_body_requests} == {"A", "C"}
    assert sum(
        item.kind == TopologyLinkKind.SOURCE_PWR_CONTACT
        for item in plan.active_topology_links
    ) == 2
    assert sum(
        item.kind == TopologyLinkKind.SOURCE_GND_CONTACT
        for item in plan.active_topology_links
    ) == 2
    assert sum(
        item.kind
        in {TopologyLinkKind.SHARED_PWR_PAD, TopologyLinkKind.SHARED_GND_PAD}
        for item in plan.active_topology_links
    ) == 4
    assert {
        item.disposition for item in plan.source_owner_partition
    } == {SourceContactDisposition.ACTIVE_SOURCE_CONTACT}
    assert plan.suppressed_base_cut_ids == ()


@pytest.mark.parametrize(
    "failure_kind",
    ("membership", "state", "moved", "power_graph", "ground_graph"),
)
def test_source_unresolved_shared_cluster_requires_every_narrow_gate(
    failure_kind: str,
) -> None:
    decaps, analysis, decisions = _shared_fixture(
        unresolved=True,
        moved=failure_kind == "moved",
    )
    cluster = analysis.clusters[0]
    connections = dict(analysis.connections)
    if failure_kind == "membership":
        connections["A"] = connections["A"].model_copy(
            update={"cluster_id": None}
        )
    elif failure_kind == "state":
        cluster = cluster.model_copy(
            update={"state": SharedPadClusterState.FLOATING}
        )
    elif failure_kind == "power_graph":
        cluster = cluster.model_copy(update={"power_edges": (("A", "B"),)})
    elif failure_kind == "ground_graph":
        cluster = cluster.model_copy(update={"ground_edges": (("A", "B"),)})
    broken = analysis.model_copy(
        update={"connections": connections, "clusters": (cluster,)}
    )

    with pytest.raises(ScenarioTopologyPlanError) as error:
        _compile(decaps, broken, decisions)

    assert error.value.code == "CONNECTION_UNMODELABLE"


@pytest.mark.parametrize(
    "kind",
    (
        DecapConnectionKind.UNRESOLVED,
        DecapConnectionKind.OUT_OF_SCOPE,
        DecapConnectionKind.FLOATING_DUMMY,
    ),
)
def test_unclustered_unmodelable_connections_stay_fail_closed(
    kind: DecapConnectionKind,
) -> None:
    decap = _decap("C1")
    direct = _direct_connection("C1")
    connection = ScenarioDecapConnection(
        refdes="C1",
        kind=kind,
        power_vias=(
            ()
            if kind == DecapConnectionKind.FLOATING_DUMMY
            else direct.power_vias
        ),
        ground_vias=(
            ()
            if kind == DecapConnectionKind.FLOATING_DUMMY
            else direct.ground_vias
        ),
        reason=(
            "source classification is not modelable"
            if kind
            in {
                DecapConnectionKind.UNRESOLVED,
                DecapConnectionKind.OUT_OF_SCOPE,
            }
            else None
        ),
    )
    analysis = _analysis({"C1": connection})

    with pytest.raises(ScenarioTopologyPlanError) as error:
        _compile({"C1": decap}, analysis)

    assert error.value.code == "CONNECTION_UNMODELABLE"


def test_source_unresolved_shared_cluster_still_requires_exact_contact_inventory() -> None:
    decaps, analysis, _decisions = _shared_fixture(unresolved=True)
    complete = _source_contacts(analysis)
    incomplete = SourceContactEvidenceSet.create(
        source_sha256=SOURCE_SHA,
        connection_evidence_sha256=connection_analysis_evidence_sha256(
            analysis
        ),
        finite_route_certificate_sha256=ROUTE_CERT_SHA,
        contacts=complete.contacts[:-1],
    )

    with pytest.raises(ScenarioTopologyPlanError) as error:
        compile_scenario_topology_plan(
            decap_by_refdes=decaps,
            connection_analysis=analysis,
            source_contacts=incomplete,
            retarget_routes=_targets(analysis),
        )

    assert error.value.code == "SOURCE_CONTACT_INVENTORY_INCOMPLETE"


def test_source_unresolved_shared_cluster_still_rejects_owner_conflicts() -> None:
    decaps, analysis, _decisions = _shared_fixture(unresolved=True)
    complete = _source_contacts(analysis)
    conflicting_contacts = (
        complete.contacts[0],
        replace(
            complete.contacts[1],
            contact_owner_id=complete.contacts[0].contact_owner_id,
        ),
        *complete.contacts[2:],
    )
    conflicting = SourceContactEvidenceSet.create(
        source_sha256=SOURCE_SHA,
        connection_evidence_sha256=connection_analysis_evidence_sha256(
            analysis
        ),
        finite_route_certificate_sha256=ROUTE_CERT_SHA,
        contacts=conflicting_contacts,
    )

    with pytest.raises(ScenarioTopologyPlanError) as error:
        compile_scenario_topology_plan(
            decap_by_refdes=decaps,
            connection_analysis=analysis,
            source_contacts=conflicting,
            retarget_routes=_targets(analysis),
        )

    assert error.value.code == "SOURCE_CONTACT_EVIDENCE_CONFLICT"


def _has_topology_path(plan, start: str, finish: str, kind: TopologyLinkKind) -> bool:
    adjacency: dict[str, set[str]] = {}
    for link in plan.active_topology_links:
        if link.kind != kind:
            continue
        adjacency.setdefault(link.first_node_id, set()).add(link.second_node_id)
        adjacency.setdefault(link.second_node_id, set()).add(link.first_node_id)
    pending = [start]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == finish:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency.get(node, set()) - visited)
    return False


def test_a_b_c_middle_gap_removes_body_incident_links_and_any_a_c_pad_path() -> None:
    decaps, analysis, decisions = _shared_fixture(
        gap_middle=True, middle_enabled=False, two_anchors=True
    )

    plan = _compile(decaps, analysis, decisions)
    nodes = {item.refdes: item for item in plan.terminal_nodes}

    assert {item.refdes for item in plan.cap_body_requests} == {"A", "C"}
    assert not any(
        link.kind
        in {TopologyLinkKind.SHARED_PWR_PAD, TopologyLinkKind.SHARED_GND_PAD}
        for link in plan.active_topology_links
    )
    assert not _has_topology_path(
        plan,
        nodes["A"].power_node_id,
        nodes["C"].power_node_id,
        TopologyLinkKind.SHARED_PWR_PAD,
    )
    assert {
        item.disposition
        for item in plan.source_owner_partition
    } == {SourceContactDisposition.ACTIVE_SOURCE_CONTACT}
    assert plan.suppressed_base_cut_ids == ()


def test_disabled_normal_is_dnp_but_keeps_pad_links_while_gap_cuts_them() -> None:
    dnp_decaps, dnp_analysis, _ = _shared_fixture(
        gap_middle=False, middle_enabled=False, two_anchors=True
    )
    gap_decaps, gap_analysis, _ = _shared_fixture(
        gap_middle=True, middle_enabled=False, two_anchors=True
    )

    dnp = _compile(dnp_decaps, dnp_analysis)
    gap = _compile(gap_decaps, gap_analysis)

    assert "B" not in {item.refdes for item in dnp.cap_body_requests}
    assert "B" not in {item.refdes for item in gap.cap_body_requests}
    assert sum(
        link.kind
        in {TopologyLinkKind.SHARED_PWR_PAD, TopologyLinkKind.SHARED_GND_PAD}
        for link in dnp.active_topology_links
    ) == 4
    assert sum(
        link.kind
        in {TopologyLinkKind.SHARED_PWR_PAD, TopologyLinkKind.SHARED_GND_PAD}
        for link in gap.active_topology_links
    ) == 0
    assert dnp.suppressed_base_cut_ids == ()
    assert gap.suppressed_base_cut_ids == ()
    assert {
        item.disposition for item in dnp.source_owner_partition
    } == {SourceContactDisposition.ACTIVE_SOURCE_CONTACT}


def test_direct_isolation_gap_removes_body_and_both_incident_contacts() -> None:
    decap = _decap(
        "C1", enabled=False, pad_state=DecapPadState.ISOLATION_GAP
    )
    connection = _direct_connection("C1")
    analysis = _analysis({"C1": connection})

    plan = _compile({"C1": decap}, analysis)

    assert plan.cap_body_requests == ()
    assert plan.active_topology_links == ()
    assert plan.suppressed_base_cut_ids == ()
    assert {
        item.disposition for item in plan.source_owner_partition
    } == {SourceContactDisposition.CONTACT_REMOVED_BY_GAP}


def test_permutation_invariant_plan_sha() -> None:
    first = _decap("C1", enabled=False)
    second = _decap("C2", enabled=False)
    c1 = _direct_connection("C1", power_count=2)
    c2 = _direct_connection("C2", power_count=1)
    analysis_a = _analysis({"C1": c1, "C2": c2})
    analysis_b = _analysis({"C2": c2, "C1": c1})

    plan_a = compile_scenario_topology_plan(
        decap_by_refdes={"C1": first, "C2": second},
        connection_analysis=analysis_a,
        source_contacts=_source_contacts(analysis_a),
        retarget_routes=_targets(analysis_a),
    )
    plan_b = compile_scenario_topology_plan(
        decap_by_refdes={"C2": second, "C1": first},
        connection_analysis=analysis_b,
        source_contacts=_source_contacts(analysis_b, reverse=True),
        retarget_routes=_targets(analysis_b, reverse=True),
    )

    assert plan_a == plan_b
    assert plan_a.plan_sha256 == plan_b.plan_sha256


def test_missing_target_decision_fails_closed_instead_of_assuming_ineligible() -> None:
    decap = _decap("C1", current_rail="R2", current_net="VDD2")
    connection = _direct_connection("C1", power_count=2)
    analysis = _analysis({"C1": connection})

    with pytest.raises(ScenarioTopologyPlanError) as error:
        _compile(
            {"C1": decap},
            analysis,
            [_eligible(connection.power_vias[0].via_id)],
        )

    assert error.value.code == "TARGET_ROUTE_DECISION_MISSING"


def test_moved_component_with_no_eligible_root_fails_closed() -> None:
    decap = _decap("C1", current_rail="R2", current_net="VDD2")
    connection = _direct_connection("C1", power_count=2)
    analysis = _analysis({"C1": connection})

    with pytest.raises(ScenarioTopologyPlanError) as error:
        _compile(
            {"C1": decap},
            analysis,
            [_ineligible(item.via_id) for item in connection.power_vias],
        )

    assert error.value.code == "RETARGET_ROOT_MISSING"


def test_missing_source_contact_fails_closed() -> None:
    decap = _decap("C1", enabled=False)
    connection = _direct_connection("C1")
    analysis = _analysis({"C1": connection})
    complete = _source_contacts(analysis)
    incomplete = SourceContactEvidenceSet.create(
        source_sha256=SOURCE_SHA,
        connection_evidence_sha256=connection_analysis_evidence_sha256(analysis),
        finite_route_certificate_sha256=ROUTE_CERT_SHA,
        contacts=complete.contacts[:-1],
    )

    with pytest.raises(ScenarioTopologyPlanError) as error:
        compile_scenario_topology_plan(
            decap_by_refdes={"C1": decap},
            connection_analysis=analysis,
            source_contacts=incomplete,
            retarget_routes=_targets(analysis),
        )

    assert error.value.code == "SOURCE_CONTACT_INVENTORY_INCOMPLETE"


def test_hash_bound_source_manifest_rejects_content_tampering() -> None:
    analysis = _analysis({"C1": _direct_connection("C1")})
    evidence = _source_contacts(analysis)

    with pytest.raises(ValueError, match="does not match"):
        replace(
            evidence,
            contacts=(
                replace(evidence.contacts[0], exposed_node_id="tampered"),
                *evidence.contacts[1:],
            ),
        )
