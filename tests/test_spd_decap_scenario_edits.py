from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

from pydantic import ValidationError
import pytest

from spd_decap_pi._core.domain import (
    CapModel,
    MLOOutline,
    ProjectSpec,
    RailSpec,
    StackupLayer,
)
from spd_decap_pi.scenario import (
    BaselineModelBinding,
    DecapConnectionKind,
    DecapPadState,
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
    derive_shared_pad_current_components,
    shared_pad_component_eligibility,
)
from spd_decap_pi.scenario_edits import (
    ScenarioEditError,
    analyze_cluster_selection,
    analyze_rail_assignment,
    analyze_restore_selection,
    assign_model_atomic,
    assign_rail_atomic,
    assign_rails_and_isolation_gaps_atomic,
    assignment_options_for_selection,
    restore_source_atomic,
    selection_presentation_analysis,
    selection_with_required_cluster_members,
    set_enabled_atomic,
)


def _project() -> ProjectSpec:
    return ProjectSpec(
        name="Shared-pad edit fixture",
        outline=MLOOutline(width_um=10_000.0, height_um=8_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(
                name="PWR",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["V1", "V2"],
            ),
            StackupLayer(
                name="GND",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        rails=[
            RailSpec(
                rail_id="R1",
                family="V",
                domain="V1",
                net="V1",
                site="S0",
                pwr_layer="PWR",
                gnd_layer="GND",
            ),
            RailSpec(
                rail_id="R2",
                family="V",
                domain="V2",
                net="V2",
                site="S0",
                pwr_layer="PWR",
                gnd_layer="GND",
            ),
        ],
        cap_models=[
            CapModel(
                model_id="M1",
                capacitance_f=1e-6,
                esr_ohm=0.01,
                esl_h=0.5e-9,
                footprint="0402",
                inventory=20,
                source_hash="m1",
            ),
            CapModel(
                model_id="M2",
                capacitance_f=2e-6,
                esr_ohm=0.01,
                esl_h=0.5e-9,
                footprint="0402",
                inventory=20,
                source_hash="m2",
            ),
        ],
    )


def _eligibility(rail_id: str, net: str) -> RailEligibility:
    return RailEligibility(
        rail_id=rail_id,
        net=net,
        pwr_layer="PWR",
        gnd_layer="GND",
        via_template_id=f"VT-{rail_id}",
        allowed=True,
    )


def _decap(refdes: str, offset: float) -> ScenarioDecap:
    return ScenarioDecap(
        refdes=refdes,
        center=ScenarioPoint(x_um=offset + 50.0, y_um=100.0),
        pwr_pad=ScenarioPad(x_um=offset, y_um=100.0, layer="TOP"),
        gnd_pad=ScenarioPad(x_um=offset + 100.0, y_um=100.0, layer="TOP"),
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
            "R1": _eligibility("R1", "V1"),
            "R2": _eligibility("R2", "V2"),
        },
    )


def _via(via_id: str, net: str, x_um: float) -> ScenarioViaLanding:
    return ScenarioViaLanding(
        via_id=via_id,
        net=net,
        endpoint_node_id=f"NODE-{via_id}",
        x_um=x_um,
        y_um=100.0,
        padstack="VIA",
    )


def _scenario(*, with_analysis: bool = True) -> ScenarioSpec:
    decaps = [
        _decap("A", 100.0),
        _decap("B", 300.0),
        _decap("D", 500.0),
        _decap("F", 700.0),
        _decap("X", 900.0),
    ]
    analysis = None
    if with_analysis:
        connections = {
            "A": ScenarioDecapConnection(
                refdes="A",
                kind=DecapConnectionKind.SHARED_ANCHOR,
                cluster_id="CL1",
                power_vias=(_via("VP-A", "V1", 100.0),),
            ),
            "B": ScenarioDecapConnection(
                refdes="B",
                kind=DecapConnectionKind.SHARED_ANCHOR,
                cluster_id="CL1",
                ground_vias=(_via("VG-B", "DGND", 400.0),),
            ),
            "D": ScenarioDecapConnection(
                refdes="D",
                kind=DecapConnectionKind.SHARED_DUMMY,
                cluster_id="CL1",
            ),
            "F": ScenarioDecapConnection(
                refdes="F",
                kind=DecapConnectionKind.FLOATING_DUMMY,
                reason="no via-backed member shares both pads",
            ),
            "X": ScenarioDecapConnection(
                refdes="X",
                kind=DecapConnectionKind.DIRECT,
                power_vias=(_via("VP-X", "V1", 900.0),),
                ground_vias=(_via("VG-X", "DGND", 1000.0),),
            ),
        }
        analysis = SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="a" * 64,
            connections=connections,
            clusters=(
                SharedPadCluster(
                    cluster_id="CL1",
                    state=SharedPadClusterState.ANCHORED,
                    member_refdes=("A", "B", "D"),
                    anchor_refdes=("A", "B"),
                    dummy_refdes=("D",),
                    power_net="V1",
                    ground_net="DGND",
                    layer="TOP",
                    power_edges=(("A", "D"), ("B", "D")),
                    ground_edges=(("A", "D"), ("B", "D")),
                    isolation_gap_refdes=("A", "B", "D"),
                    eligibility={
                        "R1": _eligibility("R1", "V1"),
                        "R2": _eligibility("R2", "V2"),
                    },
                    via_eligibility={
                        "VP-A": {
                            "R1": _eligibility("R1", "V1"),
                            "R2": _eligibility("R2", "V2"),
                        }
                    },
                ),
            ),
        )
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixture/shared.spd",
            name="shared.spd",
            size_bytes=100,
            sha256="a" * 64,
        ),
        normalized_project=_project(),
        decaps=decaps,
        connection_analysis=analysis,
        revision=7,
    )


def _diagram_scenario() -> ScenarioSpec:
    member_refdes = ("A0", "D1", "A2", "D3", "A4")
    decaps = [
        _decap(refdes, 100.0 + index * 200.0)
        for index, refdes in enumerate(member_refdes)
    ]
    connections = {
        "A0": ScenarioDecapConnection(
            refdes="A0",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CHAIN",
            power_vias=(_via("VP0", "V1", 100.0),),
            ground_vias=(_via("VG0", "DGND", 200.0),),
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
            power_vias=(_via("VP2", "V1", 500.0),),
        ),
        "D3": ScenarioDecapConnection(
            refdes="D3",
            kind=DecapConnectionKind.SHARED_DUMMY,
            cluster_id="CHAIN",
        ),
        "A4": ScenarioDecapConnection(
            refdes="A4",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CHAIN",
            power_vias=(_via("VP4", "V1", 900.0),),
        ),
    }
    edges = tuple(zip(member_refdes, member_refdes[1:]))
    cluster = SharedPadCluster(
        cluster_id="CHAIN",
        state=SharedPadClusterState.ANCHORED,
        member_refdes=member_refdes,
        anchor_refdes=("A0", "A2", "A4"),
        dummy_refdes=("D1", "D3"),
        power_net="V1",
        ground_net="DGND",
        layer="TOP",
        power_edges=edges,
        ground_edges=edges,
        isolation_gap_refdes=member_refdes,
        eligibility={
            "R1": _eligibility("R1", "V1"),
            "R2": _eligibility("R2", "V2"),
        },
        via_eligibility={
            via_id: {
                "R1": _eligibility("R1", "V1"),
                "R2": _eligibility("R2", "V2"),
            }
            for via_id in ("VP0", "VP2", "VP4")
        },
    )
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixture/diagram.spd",
            name="diagram.spd",
            size_bytes=200,
            sha256="b" * 64,
        ),
        normalized_project=_project(),
        decaps=decaps,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="b" * 64,
            connections=connections,
            clusters=(cluster,),
        ),
        revision=3,
    )


def _same_net_alias_scenario() -> ScenarioSpec:
    """Return the diagram fixture with R2 as a second identity for NET V1."""

    payload = _diagram_scenario().model_dump(mode="python")
    for rail in payload["normalized_project"]["rails"]:
        if rail["rail_id"] == "R2":
            rail["net"] = "V1"
    for decap in payload["decaps"]:
        decap["eligibility"]["R2"]["net"] = "V1"
    cluster = payload["connection_analysis"]["clusters"][0]
    cluster["eligibility"]["R2"]["net"] = "V1"
    for eligibility in cluster["via_eligibility"].values():
        eligibility["R2"]["net"] = "V1"
    return ScenarioSpec.model_validate(payload)


def test_optional_analysis_preserves_legacy_fingerprints() -> None:
    legacy = _scenario(with_analysis=False)
    explicit_none = ScenarioSpec.model_validate(
        {**legacy.model_dump(mode="python"), "connection_analysis": None}
    )

    assert explicit_none.design_fingerprint == legacy.design_fingerprint
    assert explicit_none.source_state_fingerprint == legacy.source_state_fingerprint
    with pytest.raises(ScenarioEditError, match="must be analyzed") as error:
        assign_rail_atomic(legacy, ("A",), "R2")
    assert error.value.code == "CONNECTION_ANALYSIS_REQUIRED"


def test_legacy_normalized_project_without_cluster_field_keeps_hash_identity() -> None:
    current = _scenario(with_analysis=False)
    legacy_payload = current.model_dump(mode="python")
    legacy_project = legacy_payload["normalized_project"]
    legacy_project.pop("shared_pad_clusters")
    for row in legacy_project["stackup_layers"]:
        row.pop("material", None)
        row.pop("dielectric_properties", None)
    for rail in legacy_project.get("rails", []):
        rail.pop("mixed_reference_certificate", None)
        rail.pop("mixed_reference_ground_witness", None)

    legacy = ScenarioSpec.model_validate(legacy_payload)

    assert "shared_pad_clusters" not in legacy.normalized_project

    def legacy_hash(payload: object) -> str:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def legacy_decap_payload(item: ScenarioDecap) -> dict[str, object]:
        payload = item.model_dump(mode="json")
        for eligibility in payload.get("eligibility", {}).values():
            if isinstance(eligibility, dict) and eligibility.get(
                "destination_pwr_layer"
            ) is None:
                eligibility.pop("destination_pwr_layer", None)
        return payload

    expected_design_fingerprint = legacy_hash(
        {
            "schema_version": legacy.schema_version,
            "source": {
                "size_bytes": legacy.source.size_bytes,
                "sha256": legacy.source.sha256,
            },
            "normalized_project": legacy_project,
                "decaps": [
                    legacy_decap_payload(item)
                for item in sorted(
                    legacy.decaps, key=lambda entry: entry.refdes.casefold()
                )
            ],
            "attachment_hashes": {},
        }
    )
    assert legacy.design_fingerprint == expected_design_fingerprint

    bindings = tuple(
        BaselineModelBinding(refdes=item.refdes, model_id="M1")
        for item in legacy.decaps
    )
    baseline_project = ProjectSpec.model_validate(legacy_project).model_dump(
        mode="json"
    )
    baseline_project.pop("shared_pad_clusters")
    for row in baseline_project["stackup_layers"]:
        row.pop("material", None)
        row.pop("dielectric_properties", None)
    baseline_project.pop("attachment_names", None)
    baseline_project.pop("metadata", None)
    baseline_project["cap_models"] = [
        item.model_dump(mode="json")
        for item in legacy.base_project.cap_models
        if item.model_id == "M1"
    ]
    expected_baseline_fingerprint = legacy_hash(
        {
            "rail_id": "R1",
            "project": baseline_project,
            "source_state_sha256": legacy.source_state_fingerprint,
            "model_bindings": [
                item.model_dump(mode="json")
                for item in sorted(bindings, key=lambda value: value.refdes.casefold())
            ],
        }
    )
    assert (
        legacy.baseline_evaluation_input_fingerprint("R1", bindings)
        == expected_baseline_fingerprint
    )

    round_tripped = ScenarioSpec.model_validate(legacy.model_dump(mode="python"))
    assert "shared_pad_clusters" not in round_tripped.normalized_project
    assert round_tripped.design_fingerprint == legacy.design_fingerprint
    assert (
        round_tripped.baseline_evaluation_input_fingerprint("R1", bindings)
        == expected_baseline_fingerprint
    )


def test_v1_shared_pad_analysis_is_rejected_after_graph_contract_bump() -> None:
    payload = _scenario().model_dump(mode="python")
    payload["connection_analysis"]["version"] = "DIRECT_TOP_PAD_OVERLAP_V1"

    with pytest.raises(
        ValidationError, match="unsupported shared-pad analysis version"
    ):
        ScenarioSpec.model_validate(payload)


def test_v2_requires_exact_per_physical_power_via_eligibility() -> None:
    missing_payload = _diagram_scenario().model_dump(mode="python")
    missing_cluster = missing_payload["connection_analysis"]["clusters"][0]
    del missing_cluster["via_eligibility"]["VP2"]
    with pytest.raises(ValidationError, match="every exact physical PWR Via"):
        ScenarioSpec.model_validate(missing_payload)

    extra_payload = _diagram_scenario().model_dump(mode="python")
    extra_cluster = extra_payload["connection_analysis"]["clusters"][0]
    extra_cluster["via_eligibility"]["NOT-A-SOURCE-VIA"] = {
        "R1": _eligibility("R1", "V1")
    }
    with pytest.raises(ValidationError, match="every exact physical PWR Via"):
        ScenarioSpec.model_validate(extra_payload)


@pytest.mark.parametrize("mode", ("missing", "disallowed"))
def test_cluster_aggregate_rail_requires_one_allowed_physical_via(
    mode: str,
) -> None:
    payload = _diagram_scenario().model_dump(mode="python")
    cluster = payload["connection_analysis"]["clusters"][0]
    for eligibility in cluster["via_eligibility"].values():
        if mode == "missing":
            eligibility.pop("R2")
        else:
            eligibility["R2"].update(
                {"allowed": False, "reason": "does not cross target PWR plane"}
            )

    with pytest.raises(
        ValidationError, match="supported by any physical PWR Via"
    ):
        ScenarioSpec.model_validate(payload)


def test_cluster_aggregate_rail_accepts_one_allowed_physical_via() -> None:
    payload = _diagram_scenario().model_dump(mode="python")
    cluster = payload["connection_analysis"]["clusters"][0]
    for via_id, eligibility in cluster["via_eligibility"].items():
        if via_id != "VP4":
            eligibility["R2"].update(
                {"allowed": False, "reason": "does not cross target PWR plane"}
            )

    validated = ScenarioSpec.model_validate(payload)

    assert validated.connection_analysis is not None


def test_any_via_component_eligibility_does_not_depend_on_blind_via_order() -> None:
    scenario = _diagram_scenario()
    assert scenario.connection_analysis is not None
    cluster = scenario.connection_analysis.clusters[0].model_copy(
        update={
            "via_eligibility": {
                "VP0": {},
                "VP2": {"R2": _eligibility("R2", "V2")},
                "VP4": {},
            }
        }
    )
    connection_by_key = {
        refdes.casefold(): connection
        for refdes, connection in scenario.connection_analysis.connections.items()
    }
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    component = derive_shared_pad_current_components(
        cluster,
        {key: decap_by_key[key] for key in connection_by_key},
        connection_by_key,
        analysis_version=scenario.connection_analysis.version,
    ).components[0]

    forward = shared_pad_component_eligibility(
        cluster, component, require_all_vias=False
    )
    reverse = shared_pad_component_eligibility(
        cluster,
        replace(component, power_vias=tuple(reversed(component.power_vias))),
        require_all_vias=False,
    )

    assert set(forward) == {"R2"}
    assert set(reverse) == {"R2"}


def test_persisted_unisolated_cross_net_short_is_rejected() -> None:
    payload = _diagram_scenario().model_dump(mode="python")
    d1 = next(item for item in payload["decaps"] if item["refdes"] == "D1")
    d1["current_rail_id"] = "R2"
    d1["current_net"] = "V2"

    with pytest.raises(ValidationError, match="remains active across different NETs"):
        ScenarioSpec.model_validate(payload)


def test_floating_source_cluster_with_unavailable_rail_remains_loadable() -> None:
    payload = _diagram_scenario().model_dump(mode="python")
    cluster = payload["connection_analysis"]["clusters"][0]
    cluster.update(
        {
            "state": SharedPadClusterState.FLOATING,
            "anchor_refdes": (),
            "dummy_refdes": cluster["member_refdes"],
            "isolation_gap_refdes": (),
            "eligibility": {},
            "via_eligibility": {},
        }
    )
    for connection in payload["connection_analysis"]["connections"].values():
        connection.update(
            {
                "kind": DecapConnectionKind.FLOATING_DUMMY,
                "power_vias": (),
                "ground_vias": (),
            }
        )
    for decap in payload["decaps"]:
        decap["source_rail_id"] = "UNAVAILABLE::V1"
        decap["current_rail_id"] = "UNAVAILABLE::V1"

    floating = ScenarioSpec.model_validate(payload)

    assert floating.connection_analysis is not None
    assert floating.connection_analysis.clusters[0].state == (
        SharedPadClusterState.FLOATING
    )
    assert floating.electrically_connected_refdes == ()


def test_analysis_requires_complete_per_decap_classification() -> None:
    scenario = _scenario()
    payload = scenario.model_dump(mode="python")
    del payload["connection_analysis"]["connections"]["F"]

    with pytest.raises(ValidationError, match="classify every scenario decap"):
        ScenarioSpec.model_validate(payload)


def test_one_physical_via_evidence_can_be_reused_inside_one_cluster() -> None:
    payload = _scenario().model_dump(mode="python")
    connections = payload["connection_analysis"]["connections"]
    connections["B"]["power_vias"] = connections["A"]["power_vias"]

    scenario = ScenarioSpec.model_validate(payload)
    assert scenario.connection_analysis is not None
    by_refdes = scenario.connection_analysis.connections
    assert by_refdes["A"].power_vias == by_refdes["B"].power_vias


def test_one_physical_via_cannot_be_reused_across_independent_units() -> None:
    payload = _scenario().model_dump(mode="python")
    connections = payload["connection_analysis"]["connections"]
    connections["X"]["power_vias"] = connections["A"]["power_vias"]

    with pytest.raises(ValidationError, match="reused across independent"):
        ScenarioSpec.model_validate(payload)


def test_cluster_via_reuse_requires_identical_terminal_and_landing_evidence() -> None:
    payload = _scenario().model_dump(mode="python")
    connections = payload["connection_analysis"]["connections"]
    conflicting = dict(connections["A"]["power_vias"][0])
    conflicting["x_um"] += 1.0
    connections["B"]["power_vias"] = [conflicting]

    with pytest.raises(ValidationError, match="conflicting terminal or landing"):
        ScenarioSpec.model_validate(payload)


def test_anchored_cluster_net_must_match_its_current_rail() -> None:
    payload = _scenario().model_dump(mode="python")
    for refdes in ("A", "B", "D"):
        payload["decaps"][next(
            index
            for index, item in enumerate(payload["decaps"])
            if item["refdes"] == refdes
        )]["current_net"] = "BOGUS"

    with pytest.raises(ValidationError, match="current NET does not match rail"):
        ScenarioSpec.model_validate(payload)


def test_partial_dummy_island_is_blocked_with_safe_expansion_suggestion() -> None:
    scenario = _scenario()
    state = analyze_cluster_selection(scenario, ("A",))

    assert state.pwr_editable
    assert state.incomplete_clusters == ()
    assert selection_with_required_cluster_members(scenario, ("A",)) == (
        "A",
        "B",
        "D",
    )
    proposal = analyze_rail_assignment(scenario, ("A",), "R2")
    assert not proposal.valid
    assert proposal.invalid_islands[0].member_refdes == ("A", "D")
    assert proposal.invalid_islands[0].reason == "ACTIVE_PAD_SHORT"
    assert proposal.missing_refdes == ("B", "D")
    with pytest.raises(ScenarioEditError) as error:
        assign_rail_atomic(scenario, ("A",), "R2")
    assert error.value.code == "SHARED_PAD_ACTIVE_SHORT"
    assert error.value.missing_refdes == ("B", "D")
    assert error.value.invalid_islands == proposal.invalid_islands
    assert scenario.revision == 7
    assert all(item.current_rail_id == "R1" for item in scenario.decaps)


@pytest.mark.parametrize(
    ("assignments", "gaps"),
    [
        ({"A0": "R2"}, ("D1",)),
        ({"A0": "R2", "D1": "R2"}, ("A2",)),
        ({"D1": "R2", "A2": "R2", "D3": "R2"}, ("A0", "A4")),
    ],
    ids=("edge-anchor", "dummy-plus-anchor", "dummy-anchor-dummy"),
)
def test_diagram_partial_cluster_cases_require_and_commit_physical_gaps(
    assignments: dict[str, str],
    gaps: tuple[str, ...],
) -> None:
    scenario = _diagram_scenario()

    selection = tuple(assignments)
    assert not analyze_rail_assignment(scenario, selection, "R2").valid
    changed = assign_rails_and_isolation_gaps_atomic(
        scenario, assignments, gaps
    )

    assert changed.revision == scenario.revision + 1
    selected_keys = {item.casefold() for item in assignments}
    gap_keys = {item.casefold() for item in gaps}
    assert all(
        item.current_rail_id == (
            "R2" if item.refdes.casefold() in selected_keys else "R1"
        )
        for item in changed.decaps
    )
    assert all(
        item.pad_state
        == (
            DecapPadState.ISOLATION_GAP
            if item.refdes.casefold() in gap_keys
            else DecapPadState.NORMAL
        )
        for item in changed.decaps
    )
    assert all(
        not item.enabled for item in changed.decaps if item.refdes.casefold() in gap_keys
    )


def test_diagram_case_three_rejects_dummy_only_island() -> None:
    scenario = _diagram_scenario()

    with pytest.raises(ScenarioEditError) as error:
        assign_rails_and_isolation_gaps_atomic(
            scenario, {"D1": "R2"}, ("A0", "A2")
        )
    assert error.value.code == "SHARED_PAD_DUMMY_ISLAND"
    assert error.value.invalid_islands[0].member_refdes == ("D1",)
    assert error.value.invalid_islands[0].power_via_ids == ()


def test_disabled_dnp_cell_remains_conductive_and_cannot_replace_a_gap() -> None:
    scenario = set_enabled_atomic(_diagram_scenario(), ("D1",), False)

    with pytest.raises(ScenarioEditError) as error:
        assign_rail_atomic(scenario, ("A0",), "R2")

    assert error.value.code == "SHARED_PAD_ACTIVE_SHORT"
    d1 = next(item for item in scenario.decaps if item.refdes == "D1")
    assert not d1.enabled
    assert d1.pad_state == DecapPadState.NORMAL


def test_isolation_gap_requires_source_proven_split_topology() -> None:
    payload = _diagram_scenario().model_dump(mode="python")
    payload["connection_analysis"]["clusters"][0][
        "isolation_gap_refdes"
    ] = ()
    scenario = ScenarioSpec.model_validate(payload)

    with pytest.raises(ScenarioEditError) as error:
        assign_rails_and_isolation_gaps_atomic(
            scenario, {"A0": "R2"}, ("D1",)
        )

    assert error.value.code == "ISOLATION_GAP_NOT_PROVEN"


def test_component_eligibility_uses_only_its_unique_physical_power_vias() -> None:
    payload = _diagram_scenario().model_dump(mode="python")
    cluster = payload["connection_analysis"]["clusters"][0]
    del cluster["via_eligibility"]["VP2"]["R2"]
    cluster["eligibility"].pop("R2")
    scenario = ScenarioSpec.model_validate(payload)

    changed = assign_rails_and_isolation_gaps_atomic(
        scenario, {"A0": "R2"}, ("D1",)
    )
    assert next(item for item in changed.decaps if item.refdes == "A0").current_rail_id == "R2"
    with pytest.raises(ScenarioEditError) as error:
        assign_rails_and_isolation_gaps_atomic(
            scenario,
            {"D1": "R2", "A2": "R2", "D3": "R2"},
            ("A0", "A4"),
        )
    assert error.value.code == "RAIL_INELIGIBLE_AT_PWR_VIA"
    assert error.value.invalid_islands[0].power_via_ids == ("VP2",)


def test_shared_physical_power_via_cannot_cross_post_edit_components() -> None:
    payload = _diagram_scenario().model_dump(mode="python")
    connections = payload["connection_analysis"]["connections"]
    connections["A2"] = {
        **connections["A2"],
        "power_vias": connections["A0"]["power_vias"],
    }
    cluster = payload["connection_analysis"]["clusters"][0]
    cluster["via_eligibility"] = {
        "VP0": cluster["via_eligibility"]["VP0"],
        "VP4": cluster["via_eligibility"]["VP4"],
    }
    scenario = ScenarioSpec.model_validate(payload)

    with pytest.raises(ScenarioEditError) as error:
        assign_rails_and_isolation_gaps_atomic(
            scenario, {"A0": "R2"}, ("D1",)
        )
    assert error.value.code == "SHARED_PAD_PWR_VIA_SPLIT"
    conflict = error.value.shared_power_via_conflicts[0]
    assert conflict.via_id == "VP0"
    assert conflict.owner_refdes == ("A0", "A2")


def test_restore_uses_same_post_edit_component_rule() -> None:
    tuned = assign_rails_and_isolation_gaps_atomic(
        _diagram_scenario(),
        {"D1": "R2", "A2": "R2", "D3": "R2"},
        ("A0", "A4"),
    )

    invalid = analyze_restore_selection(tuned, ("A2",))
    assert not invalid.valid
    assert invalid.invalid_islands[0].reason == "ACTIVE_PAD_SHORT"
    with pytest.raises(ScenarioEditError) as error:
        restore_source_atomic(tuned, ("A2",))
    assert error.value.code == "SHARED_PAD_ACTIVE_SHORT"

    whole_cluster = ("A0", "D1", "A2", "D3", "A4")
    restored = restore_source_atomic(tuned, whole_cluster)
    assert analyze_restore_selection(tuned, whole_cluster).valid
    assert all(item.current_rail_id == "R1" for item in restored.decaps)
    assert all(item.pad_state == DecapPadState.NORMAL for item in restored.decaps)
    assert restored.revision == tuned.revision + 1


def test_same_net_rail_alias_cannot_split_one_physically_shorted_component() -> None:
    scenario = _same_net_alias_scenario()

    proposal = analyze_rail_assignment(scenario, ("A0",), "R2")

    assert not proposal.valid
    assert len(proposal.invalid_islands) == 1
    conflict = proposal.invalid_islands[0]
    assert conflict.reason == "MIXED_RAIL_IDS_ON_SAME_NET"
    assert conflict.net == "V1"
    assert conflict.member_refdes == ("A0", "A2", "A4", "D1", "D3")
    assert assignment_options_for_selection(scenario, ("A0",)) == ("R1",)
    with pytest.raises(ScenarioEditError) as error:
        assign_rail_atomic(scenario, ("A0",), "R2")
    assert error.value.code == "SHARED_PAD_RAIL_ALIAS_MIX"

    whole_cluster = tuple(item.refdes for item in scenario.decaps)
    changed = assign_rail_atomic(scenario, whole_cluster, "R2")
    assert all(item.current_net == "V1" for item in changed.decaps)
    assert all(item.current_rail_id == "R2" for item in changed.decaps)


def test_selection_presentation_batch_matches_public_rail_and_restore_analysis() -> None:
    scenario = _scenario()
    selected = ("A", "B", "D", "X")
    batch = selection_presentation_analysis(scenario, selected)
    assert batch.selected_refdes == selected
    assert batch.valid_rail_ids == assignment_options_for_selection(scenario, selected)
    by_rail = {proposal.rail_id: proposal for proposal in batch.rail_proposals}
    for rail in scenario.base_project.rails:
        public = analyze_rail_assignment(scenario, selected, rail.rail_id)
        proposal = by_rail[rail.rail_id]
        assert proposal == public
    assert batch.restore_proposal == analyze_restore_selection(scenario, selected)


def test_persisted_same_net_component_with_mixed_rail_ids_is_rejected() -> None:
    payload = _same_net_alias_scenario().model_dump(mode="python")
    payload["decaps"][0]["current_rail_id"] = "R2"

    with pytest.raises(ValidationError, match="mixed rail IDs"):
        ScenarioSpec.model_validate(payload)


def test_full_cluster_and_direct_assignment_is_one_atomic_revision_and_noop() -> None:
    scenario = _scenario()
    selection = ("A", "B", "D", "X")

    assert assignment_options_for_selection(scenario, selection) == ("R1", "R2")
    changed = assign_rail_atomic(scenario, selection, "r2")

    assert changed.revision == scenario.revision + 1
    assert {
        item.refdes: item.current_rail_id for item in changed.decaps
    } == {"A": "R2", "B": "R2", "D": "R2", "F": "R1", "X": "R2"}
    assert all(
        item.current_net == "V2"
        for item in changed.decaps
        if item.refdes in selection
    )
    assert assign_rail_atomic(changed, selection, "R2") is changed


def test_floating_dummy_blocks_pwr_but_allows_component_local_edits() -> None:
    scenario = _scenario()

    with pytest.raises(ScenarioEditError) as error:
        assign_rail_atomic(scenario, ("F",), "R2")
    assert error.value.code == "FLOATING_DUMMY"

    modeled = assign_model_atomic(scenario, ("F",), "M2")
    disabled = set_enabled_atomic(modeled, ("F",), False)
    assert modeled.revision == 8
    assert next(item for item in modeled.decaps if item.refdes == "F").model_id == "M2"
    assert disabled.revision == 9
    assert not next(item for item in disabled.decaps if item.refdes == "F").enabled


def test_restore_requires_full_cluster_and_commits_once() -> None:
    scenario = assign_rail_atomic(_scenario(), ("A", "B", "D"), "R2")
    scenario = assign_model_atomic(scenario, ("D",), "M2")
    scenario = set_enabled_atomic(scenario, ("D",), False)

    with pytest.raises(ScenarioEditError) as error:
        restore_source_atomic(scenario, ("D",))
    assert error.value.code == "SHARED_PAD_ACTIVE_SHORT"
    assert not analyze_restore_selection(scenario, ("D",)).valid

    restored = restore_source_atomic(scenario, ("A", "B", "D"))
    assert restored.revision == scenario.revision + 1
    restored_by_refdes = {item.refdes: item for item in restored.decaps}
    assert all(restored_by_refdes[item].current_rail_id == "R1" for item in "ABD")
    assert restored_by_refdes["D"].model_id == "M1"
    assert restored_by_refdes["D"].enabled


def test_baseline_capture_excludes_electrically_floating_dummy() -> None:
    scenario = _scenario()

    assert scenario.electrically_connected_refdes == ("A", "B", "D", "X")
    captured = scenario.with_baseline_captures(("R1",))

    assert tuple(
        item.refdes for item in captured.baseline_captures["R1"].model_bindings
    ) == ("A", "B", "D", "X")


def test_cluster_members_can_use_different_current_rails_when_components_anchor() -> None:
    changed = assign_rails_and_isolation_gaps_atomic(
        _diagram_scenario(), {"A0": "R2"}, ("D1",)
    )
    assert {item.refdes: item.current_rail_id for item in changed.decaps} == {
        "A0": "R2",
        "D1": "R1",
        "A2": "R1",
        "D3": "R1",
        "A4": "R1",
    }
    assert changed.connection_analysis is not None
    assert next(item for item in changed.decaps if item.refdes == "D1").pad_state == DecapPadState.ISOLATION_GAP
    derivation = derive_shared_pad_current_components(
        changed.connection_analysis.clusters[0],
        {item.refdes: item for item in changed.decaps},
        changed.connection_analysis.connections,
    )
    assert [item.member_refdes for item in derivation.components] == [
        ("A0",),
        ("A2", "A4", "D3"),
    ]
    assert all(item.power_vias for item in derivation.components)
