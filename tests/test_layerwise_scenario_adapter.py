from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
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
from spd_decap_pi._core.models.impedance import SeriesRLCModel
from spd_decap_pi._core.solver.finite_via_layerwise import (
    FINITE_VIA_QUOTIENT_SCHEMA,
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
)
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfacePort,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationError,
)
from spd_decap_pi._core.solver.layerwise_network import (
    LayerwiseNetworkSubstrate,
    LayerwiseScenarioNetworkBinding,
)
from spd_decap_pi._core.solver.modal import DielectricDispersion
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap
from spd_decap_pi.compiled_topology_asset import (
    _freeze_owned_compact_certificate_view,
    compact_finite_certificate_views,
    freeze_compact_certificate_view,
)
from spd_decap_pi.layerwise_scenario_adapter import (
    clear_layerwise_scenario_binding_cache,
    compile_v4_layerwise_scenario_binding,
)
import spd_decap_pi.layerwise_scenario_adapter as scenario_adapter_module
from spd_decap_pi.layerwise_termination_adapter import (
    LayerwiseScenarioTerminationFactory,
)
from spd_decap_pi.scenario import (
    SHARED_PAD_ANALYSIS_VERSION,
    DecapConnectionKind,
    RailEligibility,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    ScenarioViaLanding,
    SharedPadConnectionAnalysis,
    SourceIdentity,
)
from spd_decap_pi.scenario_topology_plan import ScenarioTopologyPlanError
from spd_decap_pi.surface_certificate_asset import (
    compiled_only_surface_certificate_stub,
    externalize_surface_certificate,
)


SOURCE_SHA = "a" * 64
GEOMETRY_SHA = "b" * 64
COMPONENT_SHA = "c" * 64
ROW_SHA = "d" * 64
BASE_IDENTITY_SHA = "e" * 64

PWR_TOP = "spd-finite-via-vertex:pwr-top"
PWR1_DEEP = "spd-finite-via-vertex:pwr1-deep"
GND_TOP = "spd-finite-via-vertex:gnd-top"
GND_DEEP = "spd-finite-via-vertex:gnd-deep"
PWR2_DESTINATION = "spd-finite-via-vertex:pwr2-destination"
PWR_EDGE = "spd-finite-via-edge:vp1"
GND_EDGE = "spd-finite-via-edge:vg1"


def _canonical_sha256(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sign_row(row: dict[str, object]) -> dict[str, object]:
    signed = dict(row)
    signed["binding_evidence_sha256"] = _canonical_sha256(signed)
    return signed


def _resign_certificate(certificate: dict[str, object]) -> None:
    certificate.pop("evidence_sha256", None)
    certificate["evidence_sha256"] = _canonical_sha256(certificate)


def _contact(
    *,
    refdes: str,
    role: str,
    via_id: str,
    endpoint_node_id: str,
    vertex_id: str,
    edge_id: str,
) -> dict[str, object]:
    return {
        "landing_key": [via_id.casefold(), endpoint_node_id.casefold()],
        "refdes": refdes,
        "role": role,
        "landing_vertex_id": vertex_id,
        "first_via_edge_id": edge_id,
        "first_via_owner_id": f"via:{via_id}",
        "contact_id": f"scenario-contact:{via_id}",
        "status": "complete",
        "issues": [],
    }


def _destination_row() -> dict[str, object]:
    # This is the landing-XY schema emitted by the current producer.  The
    # adapter must prefer these explicit rail/NET/layer fields over legacy
    # aliases and must never perform a nearest-island fallback.
    return _sign_row(
        {
            "binding_kind": "landing_xy_exact_artwork",
            "refdes": "C1",
            "role": "power",
            "source_net": "VDD1",
            "source_landing_key": ["vp1", "np1"],
            "source_landing_vertex_id": PWR_TOP,
            "source_first_via_edge_id": PWR_EDGE,
            "source_first_via_owner_id": "via:VP1",
            "landing_x_um": 1_000.0,
            "landing_y_um": 2_000.0,
            "target_rail_id": "R2",
            "target_net": "VDD2",
            "target_layer": "PWR2",
            "via_template_id": "VR2",
            "destination_net": "VDD2",
            "destination_layer": "PWR2",
            "destination_pwr_layer": "PWR2",
            "destination_island_id": "artwork:VDD2:PWR2:1",
            "destination_island_ids": ["artwork:VDD2:PWR2:1"],
            "destination_component_id": "surface-component:vdd2",
            "destination_representative_island_id": "artwork:VDD2:PWR2:1",
            "destination_component_evidence_sha256": COMPONENT_SHA,
            "destination_vertex_id": PWR2_DESTINATION,
            "geometry_asset_sha256": GEOMETRY_SHA,
            "status": "complete",
            "issues": [],
        }
    )


def _signed_v4_certificate() -> dict[str, object]:
    contacts = [
        _contact(
            refdes="C1",
            role="power",
            via_id="VP1",
            endpoint_node_id="NP1",
            vertex_id=PWR_TOP,
            edge_id=PWR_EDGE,
        ),
        _contact(
            refdes="C1",
            role="ground",
            via_id="VG1",
            endpoint_node_id="NG1",
            vertex_id=GND_TOP,
            edge_id=GND_EDGE,
        ),
    ]
    destination = _destination_row()
    certificate: dict[str, object] = {
        "schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": SOURCE_SHA,
        "geometry_assets": [],
        "surface_equivalence_components": [],
        "finite_via_quotient": {
            "schema_version": FINITE_VIA_QUOTIENT_SCHEMA,
            "status": "complete",
            "vertices": [
                {"vertex_id": PWR_TOP, "net": "VDD1", "layer": "TOP"},
                {"vertex_id": PWR1_DEEP, "net": "VDD1", "layer": "PWR1"},
                {"vertex_id": GND_TOP, "net": "DGND", "layer": "TOP"},
                {"vertex_id": GND_DEEP, "net": "DGND", "layer": "GND"},
                {
                    "vertex_id": PWR2_DESTINATION,
                    "net": "VDD2",
                    "layer": "PWR2",
                },
            ],
            "edges": [
                {
                    "edge_id": PWR_EDGE,
                    "start_vertex_id": PWR_TOP,
                    "end_vertex_id": PWR1_DEEP,
                    "owner_ids": ["via:VP1"],
                    "resistance_ohm": 0.006,
                    "inductance_h": 0.31e-9,
                    "status": "complete",
                },
                {
                    "edge_id": GND_EDGE,
                    "start_vertex_id": GND_TOP,
                    "end_vertex_id": GND_DEEP,
                    "owner_ids": ["via:VG1"],
                    "resistance_ohm": 0.006,
                    "inductance_h": 0.31e-9,
                    "status": "complete",
                },
            ],
            "terminal_bindings": [],
            "retarget_destination_bindings": [destination],
            "coverage": {"status": "complete"},
        },
        "scenario_decap_terminal_topology": {
            "schema_version": "spd-scenario-decap-terminal-topology-v1",
            "status": "complete",
            "conditional_contacts": contacts,
            "retarget_destination_bindings": [destination],
            "retarget_landing_xy_coverage": {
                "requested_binding_count": 1,
                "resolved_binding_count": 1,
                "missing_binding_count": 0,
                "status": "complete",
            },
        },
        "status": "complete",
    }
    _resign_certificate(certificate)
    return certificate


def _project(certificate: dict[str, object]) -> ProjectSpec:
    return ProjectSpec(
        name="v4 scenario adapter fixture",
        outline=MLOOutline(width_um=10_000.0, height_um=8_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(name="TOP", thickness_um=35.0, conductivity_s_m=5.8e7),
            StackupLayer(name="D1", thickness_um=100.0, dk=4.0),
            StackupLayer(
                name="PWR1",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD1"],
            ),
            StackupLayer(name="D2", thickness_um=80.0, dk=4.0),
            StackupLayer(
                name="PWR2",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD2"],
            ),
            StackupLayer(name="D3", thickness_um=80.0, dk=4.0),
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
                pwr_layer="PWR1",
                gnd_layer="GND",
            ),
            RailSpec(
                rail_id="R2",
                family="VDD2",
                domain="VDD2",
                net="VDD2",
                site="SITE0",
                pwr_layer="PWR2",
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
                template_id="VR2",
                pwr_reference_layer="PWR2",
                gnd_reference_layer="GND",
                path_kind=ViaPathKind.DIRECT,
                finite_port_width_um=100.0,
                finite_port_height_um=100.0,
                loop_resistance_ohm=0.04,
                loop_inductance_h=0.8e-9,
            )
        ],
        metadata={
            "spd_import": {
                "source_sha256": SOURCE_SHA,
                "layerwise_surface_connectivity_certificate": certificate,
            }
        },
    )


def _partial() -> DispersiveAdjacentGap:
    matrix = np.asarray([[2.0e-9, -2.0e-9], [-2.0e-9, 2.0e-9]])
    return DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer="PWR1",
            lower_layer="GND",
            nominal_relative_permittivity=4.0,
            net_names=(PWR1_DEEP, GND_DEEP),
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )


def _substrate(certificate: dict[str, object]) -> LayerwiseNetworkSubstrate:
    network = compile_layer_surface_network(
        (PWR_TOP, PWR1_DEEP, GND_TOP, GND_DEEP, PWR2_DESTINATION),
        partials=(_partial(),),
        via_links=(
            LayerSurfaceViaLink(
                link_id=PWR_EDGE,
                first_node_id=PWR_TOP,
                second_node_id=PWR1_DEEP,
                count=1,
                mode="finite_parallel_rl",
                resistance_ohm_per_via=0.006,
                inductance_h_per_via=0.31e-9,
                owner_ids=("via:VP1",),
            ),
            LayerSurfaceViaLink(
                link_id=GND_EDGE,
                first_node_id=GND_TOP,
                second_node_id=GND_DEEP,
                count=1,
                mode="finite_parallel_rl",
                resistance_ohm_per_via=0.006,
                inductance_h_per_via=0.31e-9,
                owner_ids=("via:VG1",),
            ),
        ),
        ports=(
            LayerSurfacePort("R1", PWR1_DEEP, GND_DEEP),
            LayerSurfacePort("R2", PWR2_DESTINATION, GND_DEEP),
        ),
    )
    ports = {item.port_id: item for item in network.ports}
    return LayerwiseNetworkSubstrate(
        network=network,
        port_by_rail_key={"r1": ports["R1"], "r2": ports["R2"]},
        selected_net_by_rail_key={"r1": "VDD1", "r2": "VDD2"},
        reference_net_by_rail_key={"r1": "DGND", "r2": "DGND"},
        layer_blocks=(("TOP", "PWR1", "PWR2", "GND"),),
        substrate_identity_sha256=BASE_IDENTITY_SHA,
        provenance={
            "source_sha256": SOURCE_SHA,
            "surface_connectivity_evidence_sha256": certificate[
                "evidence_sha256"
            ],
        },
    )


def _fixture(
    *,
    moved: bool,
    certificate: dict[str, object] | None = None,
) -> tuple[ScenarioSpec, ProjectSpec, LayerwiseNetworkSubstrate, object]:
    certificate = deepcopy(certificate or _signed_v4_certificate())
    project = _project(certificate)
    current_rail = "R2" if moved else "R1"
    current_net = "VDD2" if moved else "VDD1"
    eligibility = {
        "R2": RailEligibility(
            rail_id="R2",
            net="VDD2",
            pwr_layer="PWR2",
            gnd_layer="GND",
            destination_pwr_layer="PWR2",
            via_template_id="VR2",
            allowed=True,
        )
    }
    decap = ScenarioDecap(
        refdes="C1",
        center=ScenarioPoint(x_um=1_050.0, y_um=2_000.0),
        pwr_pad=ScenarioPad(
            x_um=1_000.0, y_um=2_000.0, layer="TOP", padstack="CP"
        ),
        gnd_pad=ScenarioPad(
            x_um=1_100.0, y_um=2_000.0, layer="TOP", padstack="CG"
        ),
        footprint="0402",
        source_net="VDD1",
        current_net=current_net,
        source_rail_id="R1",
        current_rail_id=current_rail,
        source_model_id="M1",
        model_id="M1",
        enabled=True,
        source_mounted=True,
        eligibility=eligibility,
    )
    power = ScenarioViaLanding(
        via_id="VP1",
        net="VDD1",
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
            path="C:/fixtures/v4-direct.spd",
            name="v4-direct.spd",
            size_bytes=10,
            sha256=SOURCE_SHA,
        ),
        normalized_project=project,
        decaps=[decap],
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256=SOURCE_SHA,
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
    template = SimpleNamespace(
        cap_models={"M1": SeriesRLCModel("M1", 1.0e-6, 0.01, 0.5e-9)},
        via_models={},
    )
    return scenario, project, _substrate(certificate), template


def _factory_binding(
    scenario: ScenarioSpec,
    project: ProjectSpec,
    substrate: LayerwiseNetworkSubstrate,
    template: object,
) -> LayerwiseScenarioNetworkBinding:
    result = LayerwiseScenarioTerminationFactory(scenario, project)(
        substrate, template
    )
    assert isinstance(result, LayerwiseScenarioNetworkBinding)
    return result


def test_v4_factory_compiles_unchanged_direct_contacts_and_cap_body() -> None:
    scenario, project, substrate, template = _fixture(moved=False)

    binding = _factory_binding(scenario, project, substrate, template)
    network = binding.network
    terminal_pwr = "scenario-terminal:C1:PWR"
    terminal_gnd = "scenario-terminal:C1:GND"

    assert network.surfaces_share_ideal_node(terminal_pwr, PWR_TOP)
    assert network.surfaces_share_ideal_node(terminal_gnd, GND_TOP)
    assert {item.link_id for item in network.via_links}.issuperset(
        {PWR_EDGE, GND_EDGE}
    )
    assert len(binding.termination_manifest.clusters) == 1
    cluster = binding.termination_manifest.clusters[0]
    assert cluster.source.cluster_id == "scenario-cap:C1"
    assert cluster.source.positive_surface_node_id == terminal_pwr
    assert cluster.source.negative_surface_node_id == terminal_gnd
    assert binding.provenance["scenario_retarget_route_count"] == 0
    assert binding.provenance["scenario_cap_body_count"] == 1


def test_v4_factory_hydrates_hash_bound_certificate_attachment() -> None:
    scenario, project, substrate, template = _fixture(moved=False)
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    stub, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    name, compressed = generated
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["layerwise_surface_connectivity_certificate"] = stub
    metadata["spd_import"] = spd_import
    externalized_project = project.model_copy(update={"metadata": metadata})

    binding = LayerwiseScenarioTerminationFactory(
        scenario,
        externalized_project,
        attachments={name: compressed},
    )(substrate, template)

    assert isinstance(binding, LayerwiseScenarioNetworkBinding)
    assert binding.provenance["scenario_cap_body_count"] == 1
    with pytest.raises(LayerSurfaceTerminationError) as error:
        LayerwiseScenarioTerminationFactory(
            scenario, externalized_project
        )(substrate, template)
    assert error.value.code == "SURFACE_CERTIFICATE_ASSET_MISSING"


def test_v4_binding_cache_reuses_92_calls_and_invalidates_exact_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_layerwise_scenario_binding_cache()
    scenario, project, substrate, template = _fixture(moved=False)
    original_compile = scenario_adapter_module.compile_layerwise_scenario_network
    compile_calls = 0

    def counted_compile(**kwargs: object) -> object:
        nonlocal compile_calls
        compile_calls += 1
        return original_compile(**kwargs)

    monkeypatch.setattr(
        scenario_adapter_module,
        "compile_layerwise_scenario_network",
        counted_compile,
    )
    results = [
        LayerwiseScenarioTerminationFactory(scenario, project)(
            substrate, template
        )
        for _index in range(92)
    ]
    assert compile_calls == 1
    assert all(item is results[0] for item in results)

    moved_scenario, moved_project, _moved_substrate, moved_template = _fixture(
        moved=True
    )
    LayerwiseScenarioTerminationFactory(moved_scenario, moved_project)(
        substrate, moved_template
    )
    assert compile_calls == 2

    changed_template = SimpleNamespace(
        cap_models={
            "M1": SeriesRLCModel("M1", 2.0e-6, 0.01, 0.5e-9)
        },
        via_models={},
    )
    LayerwiseScenarioTerminationFactory(scenario, project)(
        substrate, changed_template
    )
    assert compile_calls == 3

    changed_substrate = replace(
        substrate,
        substrate_identity_sha256="f" * 64,
    )
    LayerwiseScenarioTerminationFactory(scenario, project)(
        changed_substrate, template
    )
    assert compile_calls == 4
    clear_layerwise_scenario_binding_cache()


def test_v4_factory_moved_root_uses_exact_destination_and_half_loop_rl() -> None:
    scenario, project, substrate, template = _fixture(moved=True)

    # Exercise both the public factory dispatch and the direct adapter entry.
    direct = compile_v4_layerwise_scenario_binding(
        scenario, project, substrate, template
    )
    binding = _factory_binding(scenario, project, substrate, template)
    assert isinstance(direct, LayerwiseScenarioNetworkBinding)
    assert direct.scenario_identity_sha256 == binding.scenario_identity_sha256

    links = {item.link_id: item for item in binding.network.via_links}
    route = links["scenario-retarget:VP1:R2"]
    assert PWR_EDGE not in links
    assert route.first_node_id == "scenario-terminal:C1:PWR"
    assert route.second_node_id == PWR2_DESTINATION
    assert route.owner_ids == ("via:VP1",)
    assert route.mode == "finite_parallel_rl"
    assert route.resistance_ohm_per_via == pytest.approx(0.02)
    assert route.inductance_h_per_via == pytest.approx(0.4e-9)
    assert sum(
        owner.casefold() == "via:vp1"
        for link in binding.network.via_links
        for owner in link.owner_ids
    ) == 1
    assert binding.network.surfaces_share_ideal_node(
        "scenario-terminal:C1:GND", GND_TOP
    )
    assert not binding.network.surfaces_share_ideal_node(
        "scenario-terminal:C1:PWR", PWR_TOP
    )
    assert binding.termination_manifest.clusters[0].rail_id == "R2"
    assert binding.provenance["scenario_retarget_route_count"] == 1
    assert binding.provenance["scenario_suppressed_base_cut_count"] == 1


def test_v4_retarget_manifest_is_bucketed_before_destination_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = _signed_v4_certificate()
    topology = certificate["scenario_decap_terminal_topology"]
    rows = topology["retarget_destination_bindings"]
    rows.extend(
        {
            "source_landing_key": [f"unrelated-via-{index}", "unrelated-node"],
            "role": "power",
            "target_layer": "PWR2",
            "status": "complete",
            "issues": [],
        }
        for index in range(4_096)
    )
    _resign_certificate(certificate)
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=certificate
    )
    observed_candidate_counts: list[int] = []
    observed_eligibility_candidate_counts: list[tuple[int, int]] = []
    observed_landing_candidate_counts: list[int] = []
    original = scenario_adapter_module._destination_binding
    original_eligibility = scenario_adapter_module._eligibility_for_root
    original_landing = scenario_adapter_module._landing_for_root

    def counted_candidates(
        candidate_rows: object, **kwargs: object
    ) -> object:
        observed_candidate_counts.append(len(candidate_rows))
        return original(candidate_rows, **kwargs)

    monkeypatch.setattr(
        scenario_adapter_module,
        "_destination_binding",
        counted_candidates,
    )

    def counted_eligibility(
        scenario: ScenarioSpec,
        root: scenario_adapter_module._CurrentPowerRoot,
        *,
        candidate_index: (
            scenario_adapter_module._ScenarioRootCandidateIndex | None
        ) = None,
    ) -> object:
        assert candidate_index is not None
        refdes_key = root.source_refdes.casefold()
        observed_eligibility_candidate_counts.append(
            (
                len(candidate_index.clusters_by_refdes.get(refdes_key, ())),
                len(candidate_index.decaps_by_refdes.get(refdes_key, ())),
            )
        )
        return original_eligibility(
            scenario,
            root,
            candidate_index=candidate_index,
        )

    def counted_landing(
        scenario: ScenarioSpec,
        root: scenario_adapter_module._CurrentPowerRoot,
        *,
        candidate_index: (
            scenario_adapter_module._ScenarioRootCandidateIndex | None
        ) = None,
    ) -> object:
        assert candidate_index is not None
        refdes_key = root.source_refdes.casefold()
        observed_landing_candidate_counts.append(
            len(candidate_index.connections_by_refdes.get(refdes_key, ()))
        )
        return original_landing(
            scenario,
            root,
            candidate_index=candidate_index,
        )

    monkeypatch.setattr(
        scenario_adapter_module,
        "_eligibility_for_root",
        counted_eligibility,
    )
    monkeypatch.setattr(
        scenario_adapter_module,
        "_landing_for_root",
        counted_landing,
    )

    binding = compile_v4_layerwise_scenario_binding(
        scenario, project, substrate, template
    )

    assert binding.provenance["scenario_retarget_route_count"] == 1
    assert observed_candidate_counts == [1]
    assert observed_eligibility_candidate_counts == [(0, 1)]
    assert observed_landing_candidate_counts == [1]


def test_v4_root_candidate_index_excludes_large_unrelated_inventories() -> None:
    target_eligibility = object()
    target_landing = SimpleNamespace(
        via_id="VP1",
        endpoint_node_id="NP1",
    )
    target_cluster = SimpleNamespace(
        member_refdes=("C1",),
        via_eligibility={"vp1": {"r2": target_eligibility}},
    )
    target_decap = SimpleNamespace(
        refdes="C1",
        eligibility={"R2": object()},
    )
    target_connection = SimpleNamespace(
        refdes="C1",
        power_vias=(target_landing,),
    )
    unrelated_clusters = tuple(
        SimpleNamespace(
            member_refdes=(f"unrelated-{index}",),
            via_eligibility={},
        )
        for index in range(4_096)
    )
    unrelated_decaps = tuple(
        SimpleNamespace(refdes=f"unrelated-{index}", eligibility={})
        for index in range(4_096)
    )
    unrelated_connections = {
        f"unrelated-{index}": SimpleNamespace(
            refdes=f"unrelated-{index}",
            power_vias=(),
        )
        for index in range(4_096)
    }
    scenario = SimpleNamespace(
        connection_analysis=SimpleNamespace(
            clusters=(*unrelated_clusters, target_cluster),
            connections={
                **unrelated_connections,
                "C1": target_connection,
            },
        ),
        decaps=(*unrelated_decaps, target_decap),
    )
    root = scenario_adapter_module._CurrentPowerRoot(
        via_id="VP1",
        source_refdes="c1",
        current_rail_id="R2",
        current_net="VDD2",
        moved=True,
    )

    candidate_index = scenario_adapter_module._scenario_root_candidate_index(
        scenario
    )

    assert len(candidate_index.clusters_by_refdes["c1"]) == 1
    assert len(candidate_index.decaps_by_refdes["c1"]) == 1
    assert len(candidate_index.connections_by_refdes["c1"]) == 1
    assert (
        scenario_adapter_module._eligibility_for_root(
            scenario,
            root,
            candidate_index=candidate_index,
        )
        is target_eligibility
    )
    assert (
        scenario_adapter_module._landing_for_root(
            scenario,
            root,
            candidate_index=candidate_index,
        )
        is target_landing
    )


def test_v4_destination_bucket_keeps_specificity_and_ambiguity_semantics() -> None:
    root = scenario_adapter_module._CurrentPowerRoot(
        via_id="VP1",
        source_refdes="C1",
        current_rail_id="R2",
        current_net="VDD2",
        moved=True,
    )
    landing = SimpleNamespace(via_id="VP1", endpoint_node_id="NP1")
    specific = _destination_row()
    generic = deepcopy(specific)
    for key in (
        "target_rail_id",
        "target_net",
        "refdes",
        "via_template_id",
        "binding_evidence_sha256",
    ):
        generic.pop(key, None)
    generic["destination_vertex_id"] = PWR1_DEEP
    rows = (generic, specific)
    index = scenario_adapter_module._destination_binding_index(rows)
    bucket = index[("vp1", "np1", "power", "pwr2")]

    selected = scenario_adapter_module._destination_binding(
        bucket,
        root=root,
        landing=landing,
        target_layer="PWR2",
        via_template_id="VR2",
    )

    assert len(bucket) == 2
    assert selected is specific

    conflicting = deepcopy(specific)
    conflicting["destination_vertex_id"] = PWR1_DEEP
    with pytest.raises(LayerSurfaceTerminationError) as error:
        scenario_adapter_module._destination_binding(
            (specific, conflicting),
            root=root,
            landing=landing,
            target_layer="PWR2",
            via_template_id="VR2",
        )
    assert error.value.code == "RETARGET_DESTINATION_AMBIGUOUS"


def _compact_view_ready_v4_certificate() -> dict[str, object]:
    certificate = _signed_v4_certificate()
    certificate["rail_anchor_bindings"] = [
        {"rail_id": "R1", "branch_id": "B1", "role": "power", "pin_id": "U1:P"},
        {"rail_id": "R1", "branch_id": "B1", "role": "ground", "pin_id": "U1:G"},
    ]
    certificate["terminal_contacts"] = [
        {
            "pin_id": "U1:P",
            "net": "VDD1",
            "exposed_quotient_vertex_id": PWR_TOP,
            "status": "complete",
            "incident_via_id": "VP1",
            "first_via_quotient_edge_id": PWR_EDGE,
        },
        {
            "pin_id": "U1:G",
            "net": "DGND",
            "exposed_quotient_vertex_id": GND_TOP,
            "status": "complete",
            "incident_via_id": "VG1",
            "first_via_quotient_edge_id": GND_EDGE,
        },
    ]
    # Match the production v4 producer, which writes both retarget inventories
    # under the scenario topology and the historical quotient fallback.
    topology = certificate["scenario_decap_terminal_topology"]
    quotient = certificate["finite_via_quotient"]
    assert isinstance(topology, dict)
    assert isinstance(quotient, dict)
    topology.setdefault("retarget_landing_xy_bindings", [])
    quotient.setdefault("retarget_landing_xy_bindings", [])
    _resign_certificate(certificate)
    return certificate


def _legacy_v1_scenario_view(
    certificate: dict[str, object],
) -> dict[str, object]:
    _surface, current_view, _external = compact_finite_certificate_views(
        certificate
    )
    legacy = deepcopy(current_view)
    legacy["view_schema"] = "spd-layerwise-scenario-certificate-view-v1"
    legacy_topology = legacy["scenario_decap_terminal_topology"]
    legacy_quotient = legacy["finite_via_quotient"]
    source_topology = certificate["scenario_decap_terminal_topology"]
    source_quotient = certificate["finite_via_quotient"]
    assert isinstance(legacy_topology, dict)
    assert isinstance(legacy_quotient, dict)
    assert isinstance(source_topology, dict)
    assert isinstance(source_quotient, dict)
    for key in (
        "retarget_destination_bindings",
        "retarget_landing_xy_bindings",
    ):
        rows = (
            source_topology[key]
            if key in source_topology
            else source_quotient[key]
        )
        legacy_topology[key] = deepcopy(rows)
        # Old persisted views may carry the historical duplicate fallback.
        legacy_quotient[key] = deepcopy(rows)
    legacy["view_evidence_sha256"] = _canonical_sha256(
        {
            key: value
            for key, value in legacy.items()
            if key != "view_evidence_sha256"
        }
    )
    return legacy


def _compiled_only_project_and_substrate(
    project: ProjectSpec,
    substrate: LayerwiseNetworkSubstrate,
    certificate: dict[str, object],
    scenario_view: dict[str, object],
) -> tuple[ProjectSpec, LayerwiseNetworkSubstrate]:
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["layerwise_surface_connectivity_certificate"] = (
        compiled_only_surface_certificate_stub(certificate)
    )
    metadata["spd_import"] = spd_import
    return (
        project.model_copy(update={"metadata": metadata}),
        replace(
            substrate,
            scenario_certificate_view=_freeze_owned_compact_certificate_view(
                scenario_view
            ),
        ),
    )


def test_compiled_only_compact_view_drives_moved_distribution_without_hydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete_certificate = _compact_view_ready_v4_certificate()
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=complete_certificate
    )
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    _surface, scenario_view, _external = compact_finite_certificate_views(
        certificate
    )
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["layerwise_surface_connectivity_certificate"] = (
        compiled_only_surface_certificate_stub(certificate)
    )
    metadata["spd_import"] = spd_import
    retained_project = project.model_copy(update={"metadata": metadata})
    loaded_substrate = replace(
        substrate,
        scenario_certificate_view=_freeze_owned_compact_certificate_view(
            scenario_view
        ),
    )

    monkeypatch.setattr(
        scenario_adapter_module,
        "hydrate_surface_certificate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("compiled-only moved binding attempted hydration")
        ),
    )
    binding = _factory_binding(
        scenario, retained_project, loaded_substrate, template
    )
    links = {item.link_id: item for item in binding.network.via_links}
    assert "scenario-retarget:VP1:R2" in links
    assert PWR_EDGE not in links
    assert binding.termination_manifest.clusters[0].rail_id == "R2"
    assert binding.provenance["scenario_retarget_route_count"] == 1
    assert binding.provenance["scenario_suppressed_base_cut_count"] == 1


def test_scenario_compact_view_v1_v2_preserve_plan_and_route_identity() -> None:
    complete_certificate = _compact_view_ready_v4_certificate()
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=complete_certificate
    )
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    _surface, v2_view, _external = compact_finite_certificate_views(certificate)
    v1_view = _legacy_v1_scenario_view(certificate)
    retained_project, v2_substrate = _compiled_only_project_and_substrate(
        project, substrate, certificate, v2_view
    )
    _retained_project, v1_substrate = _compiled_only_project_and_substrate(
        project, substrate, certificate, v1_view
    )

    clear_layerwise_scenario_binding_cache()
    v1_binding = _factory_binding(
        scenario, retained_project, v1_substrate, template
    )
    clear_layerwise_scenario_binding_cache()
    v2_binding = _factory_binding(
        scenario, retained_project, v2_substrate, template
    )
    clear_layerwise_scenario_binding_cache()

    assert v1_binding.plan_sha256 == v2_binding.plan_sha256
    assert (
        v1_binding.scenario_identity_sha256
        == v2_binding.scenario_identity_sha256
    )
    v1_route = next(
        item
        for item in v1_binding.network.via_links
        if item.link_id == "scenario-retarget:VP1:R2"
    )
    v2_route = next(
        item
        for item in v2_binding.network.via_links
        if item.link_id == "scenario-retarget:VP1:R2"
    )
    assert v1_route == v2_route


@pytest.mark.parametrize("view_version", ("v1", "v2"))
def test_scenario_compact_view_full_hash_is_cached_for_92_rails(
    view_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete_certificate = _compact_view_ready_v4_certificate()
    scenario, project, substrate, _template = _fixture(
        moved=True, certificate=complete_certificate
    )
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    _surface, v2_view, _external = compact_finite_certificate_views(certificate)
    view = (
        _legacy_v1_scenario_view(certificate)
        if view_version == "v1"
        else v2_view
    )
    retained_project, loaded_substrate = _compiled_only_project_and_substrate(
        project,
        substrate,
        certificate,
        view,
    )
    frozen_view = loaded_substrate.scenario_certificate_view
    assert frozen_view is not None

    original = scenario_adapter_module._canonical_sha256
    full_view_hash_calls = 0

    def counted(payload: object) -> str:
        nonlocal full_view_hash_calls
        if (
            isinstance(payload, Mapping)
            and set(payload)
            == scenario_adapter_module._SCENARIO_COMPACT_VIEW_KEYS
            - {"view_evidence_sha256"}
        ):
            full_view_hash_calls += 1
        return original(payload)

    clear_layerwise_scenario_binding_cache()
    monkeypatch.setattr(scenario_adapter_module, "_canonical_sha256", counted)
    try:
        for _rail_index in range(92):
            assert (
                scenario_adapter_module._validated_v4_certificate(
                    scenario,
                    retained_project,
                    loaded_substrate,
                    {},
                )
                is frozen_view
            )
        assert full_view_hash_calls == 1

        clear_layerwise_scenario_binding_cache()
        scenario_adapter_module._validated_v4_certificate(
            scenario,
            retained_project,
            loaded_substrate,
            {},
        )
        assert full_view_hash_calls == 2

        # Equal content in another owned wrapper must not inherit trust from
        # the first object's identity.
        distinct_substrate = replace(
            loaded_substrate,
            scenario_certificate_view=_freeze_owned_compact_certificate_view(
                deepcopy(view)
            ),
        )
        scenario_adapter_module._validated_v4_certificate(
            scenario,
            retained_project,
            distinct_substrate,
            {},
        )
        assert full_view_hash_calls == 3

        # Reusing the exact view against a different retained certificate hash
        # is a cache miss and remains fail-closed after a fresh canonical hash.
        metadata = dict(retained_project.metadata)
        spd_import = dict(metadata["spd_import"])
        retained = dict(
            spd_import["layerwise_surface_connectivity_certificate"]
        )
        retained["evidence_sha256"] = "f" * 64
        spd_import["layerwise_surface_connectivity_certificate"] = retained
        metadata["spd_import"] = spd_import
        changed_project = retained_project.model_copy(
            update={"metadata": metadata}
        )
        with pytest.raises(LayerSurfaceTerminationError) as error:
            scenario_adapter_module._validated_v4_certificate(
                scenario,
                changed_project,
                loaded_substrate,
                {},
            )
        assert error.value.code == "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_MISMATCH"
        assert full_view_hash_calls == 4

        # A read-only facade over caller-owned mutable containers is never
        # identity-cached.  Repeated calls rehash, and a later nested mutation
        # is observed instead of inheriting trust from the first call.
        caller_owned = deepcopy(view)
        caller_owned_substrate = replace(
            loaded_substrate,
            scenario_certificate_view=freeze_compact_certificate_view(
                caller_owned
            ),
        )
        scenario_adapter_module._validated_v4_certificate(
            scenario,
            retained_project,
            caller_owned_substrate,
            {},
        )
        scenario_adapter_module._validated_v4_certificate(
            scenario,
            retained_project,
            caller_owned_substrate,
            {},
        )
        assert full_view_hash_calls == 6
        caller_owned["status"] = "tampered"
        with pytest.raises(LayerSurfaceTerminationError) as error:
            scenario_adapter_module._validated_v4_certificate(
                scenario,
                retained_project,
                caller_owned_substrate,
                {},
            )
        assert (
            error.value.code
            == "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_INTEGRITY_FAILED"
        )
        assert full_view_hash_calls == 7
    finally:
        clear_layerwise_scenario_binding_cache()


def test_scenario_v2_projection_tamper_fails_even_after_outer_resign() -> None:
    complete_certificate = _compact_view_ready_v4_certificate()
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=complete_certificate
    )
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    _surface, scenario_view, _external = compact_finite_certificate_views(
        certificate
    )
    changed = deepcopy(scenario_view)
    changed["scenario_decap_terminal_topology"][
        "retarget_destination_bindings"
    ][0]["destination_vertex_id"] = "vertex:tampered"
    changed["view_evidence_sha256"] = _canonical_sha256(
        {
            key: value
            for key, value in changed.items()
            if key != "view_evidence_sha256"
        }
    )
    retained_project, loaded_substrate = _compiled_only_project_and_substrate(
        project, substrate, certificate, changed
    )

    clear_layerwise_scenario_binding_cache()
    with pytest.raises(LayerSurfaceTerminationError) as error:
        _factory_binding(
            scenario, retained_project, loaded_substrate, template
        )
    clear_layerwise_scenario_binding_cache()
    assert error.value.code == "RETARGET_DESTINATION_INTEGRITY_FAILED"


def test_scenario_v2_runtime_rejects_stale_source_sha() -> None:
    complete_certificate = _compact_view_ready_v4_certificate()
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=complete_certificate
    )
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    _surface, scenario_view, _external = compact_finite_certificate_views(
        certificate
    )
    changed = deepcopy(scenario_view)
    changed["source_sha256"] = "f" * 64
    changed["view_evidence_sha256"] = _canonical_sha256(
        {
            key: value
            for key, value in changed.items()
            if key != "view_evidence_sha256"
        }
    )
    retained_project, loaded_substrate = _compiled_only_project_and_substrate(
        project, substrate, certificate, changed
    )

    clear_layerwise_scenario_binding_cache()
    with pytest.raises(LayerSurfaceTerminationError) as error:
        _factory_binding(
            scenario, retained_project, loaded_substrate, template
        )
    clear_layerwise_scenario_binding_cache()
    assert error.value.code == "SURFACE_CONNECTIVITY_SOURCE_MISMATCH"


def test_legacy_compact_view_with_quotient_retarget_duplicates_still_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete_certificate = _compact_view_ready_v4_certificate()
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=complete_certificate
    )
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    legacy_view = _legacy_v1_scenario_view(certificate)

    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["layerwise_surface_connectivity_certificate"] = (
        compiled_only_surface_certificate_stub(certificate)
    )
    metadata["spd_import"] = spd_import
    retained_project = project.model_copy(update={"metadata": metadata})
    loaded_substrate = replace(
        substrate,
        scenario_certificate_view=_freeze_owned_compact_certificate_view(
            legacy_view
        ),
    )
    monkeypatch.setattr(
        scenario_adapter_module,
        "hydrate_surface_certificate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy compact view attempted hydration")
        ),
    )

    clear_layerwise_scenario_binding_cache()
    binding = _factory_binding(
        scenario, retained_project, loaded_substrate, template
    )
    clear_layerwise_scenario_binding_cache()
    links = {item.link_id: item for item in binding.network.via_links}
    assert "scenario-retarget:VP1:R2" in links
    assert PWR_EDGE not in links


def test_compact_view_present_empty_topology_does_not_revive_stale_quotient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete_certificate = _compact_view_ready_v4_certificate()
    topology = complete_certificate["scenario_decap_terminal_topology"]
    quotient = complete_certificate["finite_via_quotient"]
    assert isinstance(topology, dict)
    assert isinstance(quotient, dict)
    assert quotient["retarget_destination_bindings"]
    topology["retarget_destination_bindings"] = []
    _resign_certificate(complete_certificate)
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=complete_certificate
    )
    certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    _surface, scenario_view, _external = compact_finite_certificate_views(
        certificate
    )
    assert (
        scenario_view["scenario_decap_terminal_topology"][
            "retarget_destination_bindings"
        ]
        == []
    )
    assert (
        "retarget_destination_bindings"
        not in scenario_view["finite_via_quotient"]
    )

    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["layerwise_surface_connectivity_certificate"] = (
        compiled_only_surface_certificate_stub(certificate)
    )
    metadata["spd_import"] = spd_import
    retained_project = project.model_copy(update={"metadata": metadata})
    loaded_substrate = replace(
        substrate,
        scenario_certificate_view=_freeze_owned_compact_certificate_view(
            scenario_view
        ),
    )
    monkeypatch.setattr(
        scenario_adapter_module,
        "hydrate_surface_certificate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("present-empty compact view attempted hydration")
        ),
    )

    clear_layerwise_scenario_binding_cache()
    with pytest.raises(
        (LayerSurfaceTerminationError, ScenarioTopologyPlanError)
    ) as error:
        _factory_binding(scenario, retained_project, loaded_substrate, template)
    clear_layerwise_scenario_binding_cache()
    assert error.value.code == "RETARGET_ROOT_MISSING"


def test_v4_destination_binding_inner_hash_tamper_fails_closed() -> None:
    certificate = _signed_v4_certificate()
    topology = certificate["scenario_decap_terminal_topology"]
    row = topology["retarget_destination_bindings"][0]
    row["landing_x_um"] = 9_999.0
    # Re-sign the outer certificate while deliberately retaining the stale
    # row signature.  The failure must therefore come from the inner binding.
    _resign_certificate(certificate)
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=certificate
    )

    with pytest.raises(LayerSurfaceTerminationError) as error:
        LayerwiseScenarioTerminationFactory(scenario, project)(substrate, template)

    assert error.value.code == "RETARGET_DESTINATION_INTEGRITY_FAILED"


def test_v4_moved_eligible_root_without_destination_fails_closed() -> None:
    certificate = _signed_v4_certificate()
    topology = certificate["scenario_decap_terminal_topology"]
    topology["retarget_destination_bindings"] = []
    _resign_certificate(certificate)
    scenario, project, substrate, template = _fixture(
        moved=True, certificate=certificate
    )

    with pytest.raises(
        (LayerSurfaceTerminationError, ScenarioTopologyPlanError)
    ) as error:
        LayerwiseScenarioTerminationFactory(scenario, project)(substrate, template)

    assert error.value.code == "RETARGET_ROOT_MISSING"
