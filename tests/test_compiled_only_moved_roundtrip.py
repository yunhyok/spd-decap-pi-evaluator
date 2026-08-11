from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_solver_profiles import _geometry_asset
from test_surface_certificate_asset import _full_v4_roundtrip_fixture

from spd_decap_pi._core.domain import (
    CapModel,
    RailSpec,
    ViaLoopTemplate,
    ViaPathKind,
)
from spd_decap_pi._core.models.impedance import SeriesRLCModel
import spd_decap_pi._core.solver.layerwise_network as layerwise_network
from spd_decap_pi._core.solver.layerwise_network import (
    LayerwiseScenarioNetworkBinding,
)
from spd_decap_pi._core.solver.multilayer_capacitance import (
    capacitance_model_from_project,
)
from spd_decap_pi.compiled_topology_asset import (
    COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
)
import spd_decap_pi.layerwise_scenario_adapter as scenario_adapter
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
)
from spd_decap_pi.scenario_io import load_scenario_bundle, save_scenario
from spd_decap_pi.surface_certificate_asset import (
    SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
    SURFACE_CERTIFICATE_METADATA_KEY,
    canonical_surface_certificate_sha256,
)


SOURCE_RAIL = "VDD/0"
TARGET_RAIL = "R2"
TARGET_NET = "VDD2"
TARGET_VERTEX = "spd-finite-via-vertex:pwr2-destination"
TARGET_TEMPLATE = "TARGET_LOOP"
CAP_MODEL = "M1"


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _signed_row(value: dict[str, object]) -> dict[str, object]:
    row = dict(value)
    row["binding_evidence_sha256"] = _canonical_sha256(row)
    return row


def _conditional_contact(
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


def _compiled_only_moved_fixture() -> tuple[
    ScenarioSpec,
    dict[str, bytes],
    str,
    str,
]:
    """Build a production-complete two-rail v4 scenario with one moved cap."""

    base_scenario, attachments = _full_v4_roundtrip_fixture()
    project = base_scenario.base_project
    source_sha256 = base_scenario.source.sha256

    target_asset_name = "geometry/pwr-vdd2.json.zlib"
    target_asset, target_asset_sha256 = _geometry_asset(
        "PWR",
        TARGET_NET,
        [
            [500.0, 0.0],
            [1000.0, 0.0],
            [1000.0, 1000.0],
            [500.0, 1000.0],
        ],
    )
    attachments = {**attachments, target_asset_name: target_asset}

    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    geometry_records = [
        {
            key: value
            for key, value in dict(record).items()
            if key != "island_ids"
        }
        for record in spd_import["plane_geometries"]
    ]
    geometry_records.append(
        {
            "layer": "PWR",
            "net": TARGET_NET,
            "asset": target_asset_name,
            "asset_sha256": target_asset_sha256,
        }
    )
    spd_import["plane_geometries"] = geometry_records
    metadata["spd_import"] = spd_import
    geometry_project = project.model_copy(update={"metadata": metadata})
    decoded = capacitance_model_from_project(
        geometry_project,
        attachments,
        ("PWR", "GND"),
        validate_artwork=False,
        island_resolved=True,
    )
    island_by_surface = {
        (item.layer, item.net): str(item.node_name) for item in decoded.artwork
    }
    assert set(island_by_surface) == {
        ("PWR", "VDD"),
        ("PWR", TARGET_NET),
        ("GND", "DGND"),
    }
    geometry_records = [
        {
            **record,
            "island_ids": [
                island_by_surface[(str(record["layer"]), str(record["net"]))]
            ],
        }
        for record in geometry_records
    ]
    target_island = island_by_surface[("PWR", TARGET_NET)]

    certificate = deepcopy(
        project.metadata["spd_import"][SURFACE_CERTIFICATE_METADATA_KEY]
    )
    certificate["geometry_assets"] = deepcopy(geometry_records)
    target_component_id, target_component_sha256 = (
        layerwise_network._surface_component_identity(
            source_sha256,
            TARGET_NET,
            "PWR",
            (target_island,),
        )
    )
    certificate["surface_equivalence_components"].append(
        {
            "component_id": target_component_id,
            "net": TARGET_NET,
            "layer": "PWR",
            "representative_island_id": target_island,
            "component_evidence_sha256": target_component_sha256,
            "contact_status": "complete",
            "island_ids": [target_island],
        }
    )
    certificate["surface_equivalence_proofs"].append(
        {
            "layer": "PWR",
            "net": TARGET_NET,
            "island_ids": [target_island],
            "contacted_island_ids": [target_island],
            "graph_component_count": 1,
            "status": "complete",
        }
    )

    quotient = certificate["finite_via_quotient"]
    quotient["vertices"].append(
        {
            "vertex_id": TARGET_VERTEX,
            "net": TARGET_NET,
            "layer": "PWR",
            "source_node_count": 1,
            "source_node_ids_sha256": "4" * 64,
            "component_binding_status": "complete",
            "component_binding_issues": [],
            "retained_component_ids": [target_component_id],
            "retained_component_evidence_sha256s": [target_component_sha256],
            "retained_component_island_ids_by_layer": {
                "PWR": [target_island]
            },
        }
    )
    source_edge = next(
        edge
        for edge in quotient["edges"]
        if any(
            str(owner).casefold() == "via:vp1"
            for owner in edge["owner_ids"]
        )
    )
    ground_edge = next(
        edge
        for edge in quotient["edges"]
        if any(
            str(owner).casefold() == "via:vg1"
            for owner in edge["owner_ids"]
        )
    )
    source_vertex = str(source_edge["start_vertex_id"])
    ground_vertex = str(ground_edge["start_vertex_id"])
    destination = _signed_row(
        {
            "binding_kind": "landing_xy_exact_artwork",
            "refdes": "C1",
            "role": "power",
            "source_net": "VDD",
            "source_landing_key": ["vp1", "node-pwr-top"],
            "source_landing_vertex_id": source_vertex,
            "source_first_via_edge_id": str(source_edge["edge_id"]),
            "source_first_via_owner_id": "via:VP1",
            "landing_x_um": 700.0,
            "landing_y_um": 500.0,
            "target_rail_id": TARGET_RAIL,
            "target_net": TARGET_NET,
            "target_layer": "PWR",
            "via_template_id": TARGET_TEMPLATE,
            "destination_net": TARGET_NET,
            "destination_layer": "PWR",
            "destination_pwr_layer": "PWR",
            "destination_island_id": target_island,
            "destination_island_ids": [target_island],
            "destination_component_id": target_component_id,
            "destination_representative_island_id": target_island,
            "destination_component_evidence_sha256": target_component_sha256,
            "destination_vertex_id": TARGET_VERTEX,
            "geometry_asset_sha256": target_asset_sha256,
            "status": "complete",
            "issues": [],
        }
    )
    quotient["retarget_destination_bindings"] = [destination]
    quotient["retarget_landing_xy_bindings"] = []
    topology = certificate["scenario_decap_terminal_topology"]
    topology["conditional_contacts"] = [
        _conditional_contact(
            refdes="C1",
            role="power",
            via_id="VP1",
            endpoint_node_id="NODE-PWR-TOP",
            vertex_id=source_vertex,
            edge_id=str(source_edge["edge_id"]),
        ),
        _conditional_contact(
            refdes="C1",
            role="ground",
            via_id="VG1",
            endpoint_node_id="NODE-GND-TOP",
            vertex_id=ground_vertex,
            edge_id=str(ground_edge["edge_id"]),
        ),
    ]
    topology["retarget_destination_bindings"] = [destination]
    topology["retarget_landing_xy_bindings"] = []
    topology["retarget_landing_xy_coverage"] = {
        "requested_binding_count": 1,
        "resolved_binding_count": 1,
        "missing_binding_count": 0,
        "status": "complete",
    }
    certificate.pop("evidence_sha256", None)
    certificate["evidence_sha256"] = canonical_surface_certificate_sha256(
        certificate
    )

    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["plane_geometries"] = geometry_records
    spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = certificate
    metadata["spd_import"] = spd_import
    target_rail = RailSpec(
        rail_id=TARGET_RAIL,
        family=TARGET_NET,
        domain=TARGET_NET,
        net=TARGET_NET,
        site="SITE0",
        pwr_layer="PWR",
        gnd_layer="GND",
    )
    target_via = ViaLoopTemplate(
        template_id=TARGET_TEMPLATE,
        pwr_reference_layer="PWR",
        gnd_reference_layer="GND",
        path_kind=ViaPathKind.DIRECT,
        finite_port_width_um=50.0,
        finite_port_height_um=50.0,
        loop_resistance_ohm=0.04,
        loop_inductance_h=0.8e-9,
    )
    cap_model = CapModel(
        model_id=CAP_MODEL,
        capacitance_f=1.0e-6,
        esr_ohm=0.01,
        esl_h=0.5e-9,
        footprint="0402",
        inventory=1,
        source_hash="fixture",
    )
    stackup_layers = [
        (
            layer.model_copy(update={"pwr_nets": [*layer.pwr_nets, TARGET_NET]})
            if layer.name == "PWR"
            else layer
        )
        for layer in project.stackup_layers
    ]
    project = project.model_copy(
        update={
            "metadata": metadata,
            "rails": [*project.rails, target_rail],
            "via_templates": [*project.via_templates, target_via],
            "cap_models": [cap_model],
            "stackup_layers": stackup_layers,
        }
    )

    power_landing = ScenarioViaLanding(
        via_id="VP1",
        net="VDD",
        endpoint_node_id="NODE-PWR-TOP",
        x_um=700.0,
        y_um=500.0,
        padstack="VIA",
    )
    ground_landing = ScenarioViaLanding(
        via_id="VG1",
        net="DGND",
        endpoint_node_id="NODE-GND-TOP",
        x_um=750.0,
        y_um=500.0,
        padstack="VIA",
    )
    decap = ScenarioDecap(
        refdes="C1",
        center=ScenarioPoint(x_um=725.0, y_um=500.0),
        pwr_pad=ScenarioPad(
            x_um=700.0,
            y_um=500.0,
            layer="TOP",
            padstack="CP",
        ),
        gnd_pad=ScenarioPad(
            x_um=750.0,
            y_um=500.0,
            layer="TOP",
            padstack="CG",
        ),
        footprint="0402",
        source_net="VDD",
        current_net=TARGET_NET,
        source_rail_id=SOURCE_RAIL,
        current_rail_id=TARGET_RAIL,
        source_model_id=CAP_MODEL,
        model_id=CAP_MODEL,
        enabled=True,
        source_mounted=True,
        eligibility={
            TARGET_RAIL: RailEligibility(
                rail_id=TARGET_RAIL,
                net=TARGET_NET,
                pwr_layer="PWR",
                gnd_layer="GND",
                destination_pwr_layer="PWR",
                via_template_id=TARGET_TEMPLATE,
                allowed=True,
            )
        },
    )
    scenario = ScenarioSpec(
        source=base_scenario.source,
        normalized_project=project,
        decaps=[decap],
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256=source_sha256,
            connections={
                "C1": ScenarioDecapConnection(
                    refdes="C1",
                    kind=DecapConnectionKind.DIRECT,
                    power_vias=(power_landing,),
                    ground_vias=(ground_landing,),
                )
            },
        ),
    )
    return scenario, attachments, str(source_edge["edge_id"]), target_island


def test_compiled_only_saved_moved_distribution_roundtrip_uses_sqlite_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, attachments, source_edge_id, target_island = (
        _compiled_only_moved_fixture()
    )
    archive = save_scenario(
        scenario,
        tmp_path / "compiled-only-moved.spdpi",
        attachments=attachments,
    )
    loaded = load_scenario_bundle(archive)
    project = loaded.scenario.base_project
    spd_import = project.metadata["spd_import"]
    retained = spd_import[SURFACE_CERTIFICATE_METADATA_KEY]
    assert retained["storage_schema"] == SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA
    compiled_manifest = spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY]
    assert compiled_manifest["asset_name"] in loaded.attachments

    decoded_assets: list[Any] = []
    original_loader = layerwise_network.load_compiled_topology_asset

    def counted_sqlite_decode(*args: object, **kwargs: object) -> Any:
        decoded = original_loader(*args, **kwargs)
        decoded_assets.append(decoded)
        return decoded

    def hydration_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("compiled-only moved roundtrip attempted JSON hydration")

    monkeypatch.setattr(
        layerwise_network,
        "load_compiled_topology_asset",
        counted_sqlite_decode,
    )
    monkeypatch.setattr(
        layerwise_network,
        "hydrate_surface_certificate",
        hydration_forbidden,
    )
    monkeypatch.setattr(
        scenario_adapter,
        "hydrate_surface_certificate",
        hydration_forbidden,
    )
    layerwise_network.clear_layerwise_substrate_cache()
    try:
        substrate = layerwise_network.compile_layerwise_substrate(
            project,
            loaded.attachments,
            required_rail_id=SOURCE_RAIL,
        )
    finally:
        layerwise_network.clear_layerwise_substrate_cache()

    assert len(decoded_assets) == 1
    assert decoded_assets[0] is not None
    assert substrate.scenario_certificate_view is not None
    assert TARGET_VERTEX in substrate.network.surface_node_ids
    assert substrate.network.surfaces_share_ideal_node(
        TARGET_VERTEX,
        target_island,
    )

    template = SimpleNamespace(
        cap_models={
            CAP_MODEL: SeriesRLCModel(
                CAP_MODEL,
                capacitance_f=1.0e-6,
                esr_ohm=0.01,
                esl_h=0.5e-9,
            )
        },
        via_models={},
    )
    binding = LayerwiseScenarioTerminationFactory(
        loaded.scenario,
        project,
        attachments=loaded.attachments,
    )(substrate, template)
    assert isinstance(binding, LayerwiseScenarioNetworkBinding)

    links = {item.link_id: item for item in binding.network.via_links}
    route = links["scenario-retarget:VP1:R2"]
    assert source_edge_id not in links
    assert route.first_node_id == "scenario-terminal:C1:PWR"
    assert route.second_node_id == TARGET_VERTEX
    assert route.owner_ids == ("via:VP1",)
    assert sum(
        owner.casefold() == "via:vp1"
        for link in binding.network.via_links
        for owner in link.owner_ids
    ) == 1
    assert not binding.network.surfaces_share_ideal_node(
        "scenario-terminal:C1:PWR",
        "spd-finite-via-vertex:pwr-top",
    )
    cluster = binding.termination_manifest.clusters[0]
    assert cluster.rail_id == TARGET_RAIL
    assert binding.provenance["scenario_retarget_route_count"] == 1
    assert binding.provenance["scenario_suppressed_base_cut_count"] == 1
