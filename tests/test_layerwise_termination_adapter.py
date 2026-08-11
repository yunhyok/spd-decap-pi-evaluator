from __future__ import annotations

from hashlib import sha256
import json
from types import SimpleNamespace

import numpy as np
import pytest

from spd_decap_pi._core.domain import (
    CapModel,
    MLOOutline,
    ProjectSpec,
    RailSpec,
    StackupLayer,
    ViaLoopTemplate,
    ViaPathKind,
)
from spd_decap_pi._core.models.impedance import (
    ConstantImpedanceModel,
    SeriesRLCModel,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationError,
)
from spd_decap_pi.layerwise_termination_adapter import (
    LayerwiseScenarioTerminationFactory,
    build_layerwise_scenario_termination_inputs,
    compile_layerwise_scenario_terminations,
)
from spd_decap_pi.evaluation import _rail_builder_input_sha256_by_rail
from spd_decap_pi.scenario import (
    DecapConnectionKind,
    RailEligibility,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    ScenarioViaLanding,
    SHARED_PAD_ANALYSIS_VERSION,
    SharedPadConnectionAnalysis,
    SourceIdentity,
)


_VDD1_ISLAND = "spd-surface-island:" + "1" * 24
_VDD2_ISLAND = "spd-surface-island:" + "2" * 24
_GND_ISLAND = "spd-surface-island:" + "3" * 24
_VDD2_ISLAND_B = "spd-surface-island:" + "4" * 24


def _component(
    net: str,
    layer: str,
    island_ids: tuple[str, ...],
    *,
    source_sha256: str = "a" * 64,
) -> dict[str, object]:
    canonical_islands = tuple(sorted(island_ids))
    payload = {
        "source_sha256": source_sha256.casefold(),
        "net": net.casefold(),
        "layer": layer.casefold(),
        "island_ids": list(canonical_islands),
    }
    evidence = sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "component_id": f"spd-surface-equivalence-component:{evidence[:24]}",
        "net": net,
        "layer": layer,
        "island_ids": list(canonical_islands),
        "representative_island_id": canonical_islands[0],
        "component_evidence_sha256": evidence,
        "contact_status": "complete",
    }


def _surface(layer: str, net: str) -> str:
    layer_key, net_key = layer.casefold(), net.casefold()
    return f"surface|{len(layer_key)}:{layer_key}|{len(net_key)}:{net_key}"


def _landing_contact(
    via_id: str,
    external_endpoint_node_id: str,
    internal_endpoint_node_id: str,
    *,
    net: str,
    layer: str,
    island_node_id: str,
    component_island_ids: tuple[str, ...] | None = None,
    status: str = "complete",
    owner_kind: str = "decap",
) -> dict[str, object]:
    component = _component(
        net,
        layer,
        component_island_ids or (island_node_id,),
    )
    representative = str(component["representative_island_id"])
    length_um = 126.5 if layer == "PWR" else 224.5
    return {
        "landing_key": [
            via_id.casefold(),
            external_endpoint_node_id.casefold(),
        ],
        "via_id": via_id,
        "endpoint_node_id": external_endpoint_node_id,
        "external_endpoint_node_id": external_endpoint_node_id,
        "external_endpoint_layer": "TOP",
        "internal_endpoint_node_id": internal_endpoint_node_id,
        "net": net,
        "endpoint_layer": layer,
        "endpoint_island_id": representative,
        "contact_island_ids_by_layer": {
            layer: list(component["island_ids"])
        },
        "contact_path_kind": "direct_via_landing",
        "endpoint_resolution_kind": "same_layer_trace_artwork_component",
        "contact_component_id": component["component_id"],
        "component_layer": layer,
        "component_island_ids": list(component["island_ids"]),
        "representative_island_id": representative,
        "component_evidence_sha256": component[
            "component_evidence_sha256"
        ],
        "candidate_component_ids": [component["component_id"]],
        "status": status,
        "terminal_owner_kind": owner_kind,
        "drill_diameter_um": 100.0,
        "padstack": "VIA",
        "material": None,
        "segments": [
            {
                "ordinal": 0,
                "start_layer": "TOP",
                "end_layer": layer,
                "length_um": length_um,
            }
        ],
        "physical_model_status": "complete",
        "physical_model_issues": [],
    }


def _signed_certificate(
    contacts: list[dict[str, object]],
    *,
    source_sha256: str = "a" * 64,
    status: str = "complete",
) -> dict[str, object]:
    components: dict[str, dict[str, object]] = {}
    for contact in contacts:
        net = str(contact.get("net", ""))
        layer = str(
            contact.get("component_layer")
            or contact.get("endpoint_layer")
            or ""
        )
        raw_islands = contact.get("component_island_ids", ())
        if net and layer and isinstance(raw_islands, list) and raw_islands:
            component = _component(
                net,
                layer,
                tuple(str(item) for item in raw_islands),
                source_sha256=source_sha256,
            )
            components[str(component["component_id"])] = component
    components_by_surface: dict[
        tuple[str, str], list[dict[str, object]]
    ] = {}
    for component in components.values():
        components_by_surface.setdefault(
            (
                str(component["net"]).casefold(),
                str(component["layer"]).casefold(),
            ),
            [],
        ).append(component)
    proofs = []
    for rows in components_by_surface.values():
        islands = sorted(
            str(island_id)
            for component in rows
            for island_id in component["island_ids"]
        )
        proofs.append(
            {
                "net": rows[0]["net"],
                "layer": rows[0]["layer"],
                "island_ids": islands,
                "contacted_island_ids": islands,
                "graph_component_count": len(rows),
                "partition_kind": (
                    "single_component" if len(rows) == 1 else "split_components"
                ),
                "status": "complete",
            }
        )
    payload: dict[str, object] = {
        "schema_version": "spd-layer-surface-connectivity-v3",
        "compiler_id": "powersi-same-layer-trace-island-terminal-via-pair-v3",
        "source_sha256": source_sha256,
        "surface_equivalence_components": sorted(
            components.values(),
            key=lambda item: str(item["component_id"]),
        ),
        "surface_equivalence_proofs": sorted(
            proofs,
            key=lambda item: (
                str(item["net"]).casefold(),
                str(item["layer"]).casefold(),
            ),
        ),
        "terminal_landing_contacts": contacts,
        "status": status,
    }
    evidence = sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {**payload, "evidence_sha256": evidence}


def _certificate_contacts() -> list[dict[str, object]]:
    return [
        _landing_contact(
            "VP1",
            "NP1",
            "NP1-INTERNAL",
            net="VDD2",
            layer="PWR",
            island_node_id=_VDD2_ISLAND,
        ),
        _landing_contact(
            "VG1",
            "NG1",
            "NG1-INTERNAL",
            net="DGND",
            layer="GND",
            island_node_id=_GND_ISLAND,
        ),
    ]


def _with_certificate(
    project: ProjectSpec,
    certificate: dict[str, object],
) -> ProjectSpec:
    metadata = dict(project.metadata)
    spd_import = dict(metadata.get("spd_import", {}))
    spd_import["source_sha256"] = "a" * 64
    spd_import["layerwise_surface_connectivity_certificate"] = certificate
    metadata["spd_import"] = spd_import
    return project.model_copy(update={"metadata": metadata})


def _fixture(*, current_rail: str = "R2") -> tuple[ScenarioSpec, ProjectSpec, object, object]:
    project = ProjectSpec(
        name="layerwise termination adapter fixture",
        outline=MLOOutline(width_um=10_000.0, height_um=8_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(name="TOP", thickness_um=35.0, conductivity_s_m=5.8e7),
            StackupLayer(name="D1", thickness_um=100.0, dk=4.0),
            StackupLayer(
                name="PWR",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD1", "VDD2"],
            ),
            StackupLayer(name="D2", thickness_um=80.0, dk=4.0),
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
                family="VDD1",
                domain="VDD1",
                net="VDD1",
                site="SITE0",
                pwr_layer="PWR",
                gnd_layer="GND",
            ),
            RailSpec(
                rail_id="R2",
                family="VDD2",
                domain="VDD2",
                net="VDD2",
                site="SITE0",
                pwr_layer="PWR",
                gnd_layer="GND",
            ),
        ],
        cap_models=[
            CapModel(
                model_id="M1",
                capacitance_f=1.0e-6,
                esr_ohm=0.01,
                esl_h=0.5e-9,
                footprint="0402",
                inventory=1,
                source_hash="fixture",
            )
        ],
        via_templates=[
            ViaLoopTemplate(
                template_id="VR1",
                pwr_reference_layer="PWR",
                gnd_reference_layer="GND",
                path_kind=ViaPathKind.DIRECT,
                finite_port_width_um=100.0,
                finite_port_height_um=100.0,
                loop_resistance_ohm=0.02,
                loop_inductance_h=0.5e-9,
            ),
        ],
        metadata={
            "spd_via_template_provenance": {
                "VR1": {"rail_id": "R1"},
                # Both rails intentionally share the same calibrated physical
                # layer-pair template in this compact fixture.
            },
            "spd_import": {
                "source_sha256": "a" * 64,
                "layerwise_surface_connectivity_certificate": (
                    _signed_certificate(_certificate_contacts())
                ),
            },
        },
    )
    # Provenance takes precedence but R2 would otherwise see the same unique
    # layer-pair template.  R1 and R2 are therefore both deterministic.
    rail = next(item for item in project.rails if item.rail_id == current_rail)
    eligibility = RailEligibility(
        rail_id=rail.rail_id,
        net=rail.net,
        pwr_layer=rail.pwr_layer,
        gnd_layer=rail.gnd_layer,
        via_template_id="VR1",
        allowed=True,
    )
    decap = ScenarioDecap(
        refdes="C1",
        center=ScenarioPoint(x_um=1_050.0, y_um=2_000.0),
        pwr_pad=ScenarioPad(x_um=1_000.0, y_um=2_000.0, layer="TOP", padstack="CP"),
        gnd_pad=ScenarioPad(x_um=1_100.0, y_um=2_000.0, layer="TOP", padstack="CG"),
        footprint="0402",
        source_net="VDD2",
        current_net=rail.net,
        source_rail_id="R2",
        current_rail_id=rail.rail_id,
        source_model_id="M1",
        model_id="M1",
        enabled=True,
        source_mounted=True,
        eligibility={rail.rail_id: eligibility},
    )
    power = ScenarioViaLanding(
        via_id="VP1",
        net="VDD2",
        endpoint_node_id="NP1",
        x_um=1_000.0,
        y_um=2_000.0,
        padstack="VIA",
    )
    ground = ScenarioViaLanding(
        via_id="VG1",
        net="DGND",
        endpoint_node_id="NG1",
        x_um=1_100.0,
        y_um=2_000.0,
        padstack="VIA",
    )
    scenario = ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/direct.spd",
            name="direct.spd",
            size_bytes=10,
            sha256="a" * 64,
        ),
        normalized_project=project,
        decaps=[decap],
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="a" * 64,
            connections={
                "C1": ScenarioDecapConnection(
                    refdes="C1",
                    kind=DecapConnectionKind.DIRECT,
                    power_vias=(power,),
                    ground_vias=(ground,),
                )
            },
        ),
    )
    cap = SeriesRLCModel("M1", 1.0e-6, 0.01, 0.5e-9)
    via = ConstantImpedanceModel("VR1", 0.04 + 0.06j)
    template = SimpleNamespace(cap_models={"M1": cap}, via_models={"VR1": via})
    nodes = (
        _VDD1_ISLAND,
        _VDD2_ISLAND,
        _GND_ISLAND,
        _VDD2_ISLAND_B,
    )
    substrate = SimpleNamespace(network=SimpleNamespace(surface_node_ids=nodes))
    return scenario, project, template, substrate


def test_direct_current_rail_is_bound_to_exact_internal_islands_and_loop_model() -> None:
    scenario, project, template, substrate = _fixture()

    inputs = build_layerwise_scenario_termination_inputs(scenario, project, template)

    assert inputs.source_via_to_surface_node == {
        "VP1": _VDD2_ISLAND,
        "VG1": _GND_ISLAND,
    }
    assert inputs.source_via_to_internal_endpoint_node == {
        "VP1": "NP1-INTERNAL",
        "VG1": "NG1-INTERNAL",
    }
    assert set(inputs.terminal_via_model_by_id) == {"VP1", "VG1"}
    assert len(inputs.connectivity_certificate_evidence_sha256) == 64
    assert _surface("PWR", "VDD2") not in inputs.source_via_to_surface_node.values()

    manifest = compile_layerwise_scenario_terminations(
        scenario, project, substrate, template
    )
    with pytest.raises(LayerSurfaceTerminationError) as error:
        LayerwiseScenarioTerminationFactory(scenario, project)(substrate, template)
    assert error.value.code == "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED"
    evaluated = manifest.evaluate(
        np.asarray([1.0e6]),
        selected_rail_id="R1",
        base_network_identity_sha256="b" * 64,
    )
    assert len(evaluated.stamps) == 1
    assert evaluated.stamps[0].rail_id == "R2"
    assert evaluated.diagnostics.external_port_termination_count == 0


def test_production_factory_requires_a_surface_connectivity_certificate() -> None:
    scenario, project, template, substrate = _fixture()
    project = project.model_copy(update={"metadata": {}})

    with pytest.raises(LayerSurfaceTerminationError) as error:
        LayerwiseScenarioTerminationFactory(scenario, project)(substrate, template)

    assert error.value.code == "SURFACE_CONNECTIVITY_CERTIFICATE_MISSING"


def test_redistributed_assignment_without_current_eligibility_fails_closed() -> None:
    scenario, project, template, _substrate = _fixture(current_rail="R1")
    decap = scenario.decaps[0].model_copy(update={"eligibility": {}})
    scenario = scenario.model_copy(update={"decaps": (decap,)})

    with pytest.raises(LayerSurfaceTerminationError) as error:
        build_layerwise_scenario_termination_inputs(scenario, project, template)

    assert error.value.code == "CURRENT_RAIL_ELIGIBILITY_MISSING"


def test_unchanged_source_unresolved_landing_uses_source_via_proof() -> None:
    scenario, project, template, substrate = _fixture()
    connection = scenario.connection_analysis.connections["C1"].model_copy(
        update={"kind": DecapConnectionKind.UNRESOLVED}
    )
    analysis = scenario.connection_analysis.model_copy(
        update={"connections": {"C1": connection}}
    )
    decap = scenario.decaps[0].model_copy(update={"eligibility": {}})
    scenario = scenario.model_copy(
        update={"decaps": (decap,), "connection_analysis": analysis}
    )

    inputs = build_layerwise_scenario_termination_inputs(
        scenario, project, template
    )
    assert set(inputs.source_via_to_surface_node) == {"VP1", "VG1"}
    manifest = compile_layerwise_scenario_terminations(
        scenario, project, substrate, template
    )
    assert len(manifest.clusters) == 1


def test_exact_certificate_island_controls_binding_without_layer_net_projection() -> None:
    scenario, project, template, _substrate = _fixture()
    contacts = _certificate_contacts()
    contacts[0] = _landing_contact(
        "VP1",
        "NP1",
        "NP1-INTERNAL",
        net="VDD2",
        layer="PWR",
        island_node_id=_VDD2_ISLAND_B,
    )
    project = _with_certificate(project, _signed_certificate(contacts))

    inputs = build_layerwise_scenario_termination_inputs(
        scenario, project, template
    )

    assert inputs.source_via_to_surface_node["VP1"] == _VDD2_ISLAND_B
    assert inputs.source_via_to_surface_node["VP1"] != _surface("PWR", "VDD2")


def test_trace_certified_multi_island_component_binds_to_representative() -> None:
    scenario, project, template, _substrate = _fixture()
    contacts = _certificate_contacts()
    contacts[0] = _landing_contact(
        "VP1",
        "NP1",
        "NP1-INTERNAL",
        net="VDD2",
        layer="PWR",
        island_node_id=_VDD2_ISLAND,
        component_island_ids=(_VDD2_ISLAND, _VDD2_ISLAND_B),
    )
    project = _with_certificate(project, _signed_certificate(contacts))

    inputs = build_layerwise_scenario_termination_inputs(
        scenario, project, template
    )

    assert inputs.source_via_to_surface_node["VP1"] == _VDD2_ISLAND


def test_terminal_cannot_bind_component_declared_uncontacted() -> None:
    scenario, project, template, _substrate = _fixture()
    certificate = _signed_certificate(_certificate_contacts())
    proof = next(
        item
        for item in certificate["surface_equivalence_proofs"]
        if str(item["net"]).casefold() == "vdd2"
    )
    proof["contacted_island_ids"] = []
    proof["graph_component_count"] = 0
    proof["partition_kind"] = "split_components"
    proof["status"] = "uncontacted_island"
    component = next(
        item
        for item in certificate["surface_equivalence_components"]
        if str(item["net"]).casefold() == "vdd2"
    )
    component["contact_status"] = "uncontacted"
    unsigned = {
        key: value for key, value in certificate.items() if key != "evidence_sha256"
    }
    certificate["evidence_sha256"] = sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    project = _with_certificate(project, certificate)

    with pytest.raises(LayerSurfaceTerminationError) as captured:
        build_layerwise_scenario_termination_inputs(
            scenario, project, template
        )

    assert captured.value.code == "TERMINAL_LANDING_CONTACT_INCOMPLETE"


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        ("source", "SURFACE_CONNECTIVITY_SOURCE_MISMATCH"),
        ("evidence", "SURFACE_CONNECTIVITY_CERTIFICATE_INTEGRITY_FAILED"),
        ("status", "SURFACE_CONNECTIVITY_CERTIFICATE_INCOMPLETE"),
    ),
)
def test_v3_certificate_source_evidence_and_status_fail_closed(
    failure: str,
    expected_code: str,
) -> None:
    scenario, project, template, _substrate = _fixture()
    if failure == "source":
        certificate = _signed_certificate(
            _certificate_contacts(), source_sha256="b" * 64
        )
    elif failure == "status":
        certificate = _signed_certificate(
            _certificate_contacts(), status="incomplete"
        )
    else:
        certificate = _signed_certificate(_certificate_contacts())
        certificate["status"] = "tampered-without-resigning"
    project = _with_certificate(project, certificate)

    with pytest.raises(LayerSurfaceTerminationError) as captured:
        build_layerwise_scenario_termination_inputs(
            scenario, project, template
        )

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        ("missing", "TERMINAL_LANDING_CONTACT_MISSING"),
        ("ambiguous", "TERMINAL_LANDING_CONTACT_AMBIGUOUS"),
        ("incomplete", "TERMINAL_LANDING_CONTACT_INCOMPLETE"),
        ("uncertified_multi_island", "TERMINAL_LANDING_CONTACT_INCOMPLETE"),
        ("owner", "TERMINAL_LANDING_OWNER_MISMATCH"),
        ("owner_unknown", "TERMINAL_LANDING_OWNER_MISMATCH"),
        (
            "physical",
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
        ),
        (
            "physical_chain",
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
        ),
        (
            "physical_cycle",
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
        ),
        (
            "stale_stackup_length",
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
        ),
    ),
)
def test_exact_decap_landing_contact_failures_are_deterministic(
    failure: str,
    expected_code: str,
) -> None:
    scenario, project, template, _substrate = _fixture()
    contacts = _certificate_contacts()
    if failure == "missing":
        contacts = contacts[1:]
    elif failure == "ambiguous":
        contacts.append(dict(contacts[0]))
    elif failure == "incomplete":
        contacts[0] = {
            **contacts[0],
            "contact_island_ids_by_layer": {},
            "endpoint_layer": None,
            "endpoint_island_id": None,
            "status": "incomplete",
        }
    elif failure == "uncertified_multi_island":
        contacts[0] = {
            **contacts[0],
            "contact_island_ids_by_layer": {
                "PWR": [_VDD2_ISLAND, _VDD2_ISLAND_B]
            },
        }
    elif failure == "owner":
        contacts[0] = {**contacts[0], "terminal_owner_kind": "device"}
    elif failure == "owner_unknown":
        contacts[0] = {**contacts[0], "terminal_owner_kind": "unknown"}
    elif failure == "physical":
        contacts[0] = {
            **contacts[0],
            "physical_model_status": "incomplete",
            "physical_model_issues": ["source segment missing"],
        }
    elif failure == "physical_chain":
        contacts[0] = {
            **contacts[0],
            "segments": [
                {
                    "ordinal": 0,
                    "start_layer": "WRONG_EXTERNAL_LAYER",
                    "end_layer": "PWR",
                    "length_um": 100.0,
                }
            ],
        }
    elif failure == "physical_cycle":
        contacts[0] = {
            **contacts[0],
            "segments": [
                {
                    "ordinal": 0,
                    "start_layer": "TOP",
                    "end_layer": "GND",
                    "length_um": 224.5,
                },
                {
                    "ordinal": 1,
                    "start_layer": "GND",
                    "end_layer": "PWR",
                    "length_um": 98.0,
                },
            ],
        }
    else:
        contacts[0] = {
            **contacts[0],
            "segments": [
                {
                    "ordinal": 0,
                    "start_layer": "TOP",
                    "end_layer": "PWR",
                    "length_um": 100.0,
                }
            ],
        }
    project = _with_certificate(project, _signed_certificate(contacts))

    with pytest.raises(LayerSurfaceTerminationError) as captured:
        build_layerwise_scenario_termination_inputs(
            scenario, project, template
        )

    assert captured.value.code == expected_code


def test_layerwise_preflight_identity_includes_other_rail_decap_state() -> None:
    class DecapStub:
        def __init__(self, refdes: str, rail_id: str, enabled: bool) -> None:
            self.refdes = refdes
            self.current_rail_id = rail_id
            self.enabled = enabled

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "refdes": self.refdes,
                "current_rail_id": self.current_rail_id,
                "enabled": self.enabled,
            }

    first = SimpleNamespace(
        decaps=(DecapStub("C1", "R1", True), DecapStub("C2", "R2", True)),
        connection_analysis=None,
    )
    changed = SimpleNamespace(
        decaps=(DecapStub("C1", "R1", True), DecapStub("C2", "R2", False)),
        connection_analysis=None,
    )

    legacy_first = _rail_builder_input_sha256_by_rail(first, ("R1",))
    legacy_changed = _rail_builder_input_sha256_by_rail(changed, ("R1",))
    layerwise_first = _rail_builder_input_sha256_by_rail(
        first, ("R1",), include_all_decaps=True
    )
    layerwise_changed = _rail_builder_input_sha256_by_rail(
        changed, ("R1",), include_all_decaps=True
    )

    assert legacy_first == legacy_changed
    assert layerwise_first != layerwise_changed
