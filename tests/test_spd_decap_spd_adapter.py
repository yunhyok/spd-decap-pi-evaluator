from pathlib import Path
from dataclasses import replace
from hashlib import sha256
import json
import math
from types import SimpleNamespace

import pytest

from test_io_spd import MINI_SPD
from test_spd_decap_evaluation import _scenario

from spd_decap_pi import spd_adapter
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.domain import (
    MLOOutline,
    MixedReferenceCertificate,
    PinKind,
    PinRecord,
    ProjectSpec,
    StackupLayer,
    TerminalKind,
)
from spd_decap_pi._core.plane_pairs import suggest_effective_plane_pairs
from spd_decap_pi._core.io.shared_pad import (
    SpdDecapConnection,
    SpdSharedPadCluster,
)
from spd_decap_pi._core.io.spd import (
    SpdFiniteViaQuotientCoverage,
    SpdFiniteViaScenarioIsolationCoverage,
    SpdImportError,
    SpdPlaneGeometry,
    SpdViaIslandPairCoverage,
    analyze_spd,
)
from spd_decap_pi._core.services import WorkspaceState, import_cap_spice
from spd_decap_pi.scenario import (
    SHARED_PAD_ANALYSIS_VERSION,
    mixed_reference_ground_landing_identity,
)
from spd_decap_pi.scenario_io import ScenarioFormatError, save_scenario
from spd_decap_pi.raw_spatial_contact_asset import (
    RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY,
    load_raw_spatial_contact_asset,
)
from spd_decap_pi.compiled_topology_asset import (
    COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
    load_compiled_topology_asset,
)
from spd_decap_pi.eligibility import IndexedPlaneGeometry
from spd_decap_pi.routing_obstacles import (
    MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA,
    MLO_LANDING_CLASS_SHORT_SPAN_VIA,
    RoutingCandidateState,
    SignalTraceAvoidancePolicy,
    decode_routing_obstacle_asset,
    evaluate_routing_candidate,
    parse_mlo_landing_certificates,
)
from spd_decap_pi.spd_adapter import (
    FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
    _prepare_post_plan_selection,
    _build_mlo_landing_certificates,
    _finite_port_inside_solver_bounds,
    _raise_for_rejected_mixed_reference_landings,
    _strict_source_plane_pairs,
    _scenario_via_landing,
    _via_target_layers_by_net,
    import_spd_scenario,
)
from spd_decap_pi.surface_certificate_asset import (
    SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
    SURFACE_CERTIFICATE_METADATA_KEY,
)


def _canonical_owner_digest(*owner_ids: str) -> str:
    digest = sha256()
    for owner_id in sorted(owner_ids, key=lambda value: (value.casefold(), value)):
        encoded = owner_id.casefold().encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def test_third_plan_selection_drops_orphan_failed_net_and_restores_known_pair() -> None:
    suggestion = SimpleNamespace(pwr_layer="PWR1", gnd_layer="GND1")
    selected = {"good": suggestion, "orphan": suggestion}
    provenance = {"good": {}, "orphan": {}}
    restored = _prepare_post_plan_selection(
        selected,
        provenance,
        {"good": suggestion},
        {"good", "orphan"},
        {"good"},
    )
    assert set(restored) == {"good"}
    assert set(provenance) == {"good", "orphan"}
    assert provenance["orphan"]["source_graph_pair_unresolved"] is True
    assert provenance["orphan"]["selection_mode"] == "LEGACY_PREEXISTING_PAIR_UNRESOLVED"
    with pytest.raises(SpdImportError):
        _prepare_post_plan_selection(
            {"known": suggestion},
            {"known": {}},
            {},
            {"known"},
            {"known"},
        )


def test_preselection_pair_suggestion_retains_mixed_reference_certificate() -> None:
    layers = (
        StackupLayer(name="PWR", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=["VDD"]),
        StackupLayer(name="D1", thickness_um=100.0, dk=4.0, df=0.01),
        StackupLayer(name="MIX", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=["DGND", "SIG"]),
    )
    certificate = MixedReferenceCertificate(
        rail_net="VDD",
        gnd_net="DGND",
        pwr_layer="PWR",
        gnd_layer="MIX",
        pwr_asset_sha256="a" * 64,
        gnd_asset_sha256="b" * 64,
        overlap_fraction=0.5,
        dominant_overlap_component_fraction=0.5,
    )
    suggestions = suggest_effective_plane_pairs(
        layers,
        rail_net="VDD",
        gnd_aliases=("DGND",),
        mixed_reference_certificates=(certificate,),
    )
    assert any(
        item.pwr_layer == "PWR"
        and item.gnd_layer == "MIX"
        and item.mixed_reference_certificate == certificate
        for item in suggestions
    )
from spd_decap_pi.evaluation import _terminal_footprint


def test_graph_target_node_contract_separates_exact_artwork_from_solver_port() -> None:
    bounds = (0.0, 100.0, 0.0, 100.0)
    # Graph-only target nodes are exact point evidence.  The analytical
    # finite port may cross detailed artwork, but must remain inside the
    # rectangular solver cavity and is disclosed LOW confidence.
    assert _finite_port_inside_solver_bounds(10.0, 50.0, 60.0, 20.0, bounds) is False
    assert _finite_port_inside_solver_bounds(50.0, 50.0, 60.0, 20.0, bounds) is True
    assert _finite_port_inside_solver_bounds(0.0, 50.0, 1.0, 1.0, bounds) is False
    assert _finite_port_inside_solver_bounds(50.0, 50.0, 100.0, 20.0, bounds) is False


def test_strict_source_plane_pairs_memoizes_repeated_exact_coverage_points() -> None:
    class CountingPlane:
        def __init__(self, layer: str, net: str) -> None:
            self.layer = layer
            self.net = net
            self.positive_bounds = (0.0, 100.0, 0.0, 100.0)
            self.geometry = SimpleNamespace(
                positive_polygons_um=(),
                negative_polygons_um=(),
                positive_circles_um=(),
                negative_circles_um=(),
                primitive_order=(),
            )
            self.contains_calls = 0

        def contains(self, _x: float, _y: float) -> str:
            self.contains_calls += 1
            return "inside"

        def covers_footprint(self, *_args: float) -> bool:
            return True

    pwr = CountingPlane("PWR", "VDD")
    gnd = CountingPlane("GND", "DGND")
    source_sha = "a" * 64
    pwr_via = SimpleNamespace(
        via_id="P0", endpoint_node_id="NP", net="VDD", padstack="P",
        x_um=10.0, y_um=10.0,
    )
    gnd_via = SimpleNamespace(
        via_id="G0", endpoint_node_id="NG", net="DGND", padstack="P",
        x_um=10.0, y_um=10.0,
    )
    connection = SimpleNamespace(
        refdes="C1", power_vias=(pwr_via, pwr_via), ground_vias=(gnd_via, gnd_via)
    )
    analysis = SimpleNamespace(
        plane_geometries=(pwr, gnd),
        cap_instances=(),
        decap_connections=(connection,),
        power_plane_nets=("VDD",),
        pins=(),
        padstacks=(),
        counts={"source_graph_capability": "SOURCE_GRAPH_AVAILABLE"},
        source=SimpleNamespace(sha256=source_sha),
    )
    project = SimpleNamespace(
        gnd_aliases=("DGND",),
        rails=(SimpleNamespace(net="VDD", pwr_layer="PWR", gnd_layer="GND"),),
        stackup_layers=(
            StackupLayer(name="PWR", thickness_um=10.0, conductivity_s_m=1.0, pwr_nets=["VDD"]),
            StackupLayer(name="D", thickness_um=10.0, dk=4.0),
            StackupLayer(name="GND", thickness_um=10.0, conductivity_s_m=1.0, pwr_nets=["DGND"]),
        ),
        via_templates=(
            SimpleNamespace(
                pwr_reference_layer="PWR", gnd_reference_layer="GND",
                finite_port_width_um=1.0, finite_port_height_um=1.0,
            ),
        ),
        metadata={"spd_import": {"source_sha256": source_sha}},
    )
    path_recovery = SimpleNamespace(evidence_by_via={})
    connectivity = SimpleNamespace(
        statistics={"requested": 1, "node_section_passes": 1, "via_edges": 1, "trace_edges": 0},
        reaches=lambda *_args: True,
        target_contacts_by_key={
            ("p0", "np", "pwr"): (("NP", 10.0, 10.0),),
            ("g0", "ng", "gnd"): (("NG", 10.0, 10.0),),
        },
        target_contact_count_by_key={},
        target_contact_hash_by_key={},
    )
    selected, provenance = _strict_source_plane_pairs(
        project,
        analysis,
        path_recovery,
        connectivity,
        indexed_override={("pwr", "vdd"): pwr, ("gnd", "dgnd"): gnd},
    )
    assert selected, provenance
    assert selected["vdd"].pwr_layer == "PWR"
    assert selected["vdd"].gnd_layer == "GND"
    assert provenance["vdd"]["pwr_layer"] == "PWR"
    assert provenance["vdd"]["gnd_layer"] == "GND"
    assert pwr.contains_calls == 1
    assert gnd.contains_calls == 1


def test_adapter_persists_structural_only_via_evidence() -> None:
    landing = SimpleNamespace(
        via_id="V1",
        net="VDD",
        endpoint_node_id="N1",
        x_um=10.0,
        y_um=20.0,
        padstack="P1",
        rotation_degrees=0.0,
    )
    recovery = SimpleNamespace(
        evidence_by_via={},
        structural_evidence_by_via={
            "v1": (
                SimpleNamespace(
                    target_layer="L2",
                    target_node_id="N2",
                    target_x_um=11.0,
                    target_y_um=21.0,
                    segments=(
                        SimpleNamespace(
                            via_id="V1",
                            padstack="P1",
                            drill_diameter_um=100.0,
                            start_layer="TOP",
                            end_layer="L2",
                            length_um=120.0,
                            end_x_um=11.0,
                            end_y_um=21.0,
                            rotation_degrees=0.0,
                            padstack_material="COPPER",
                        ),
                    ),
                    trace_hops=1,
                    trace_alternate_exit=False,
                ),
            )
        },
    )
    converted = _scenario_via_landing(landing, recovery)
    assert converted.path_evidence == ()
    assert len(converted.structural_evidence) == 1
    assert converted.structural_evidence[0].target_layer == "L2"
    assert converted.structural_evidence[0].x_um == 11.0
    assert converted.structural_evidence[0].trace_hops == 1


def test_graph_contact_remap_preserves_source_landing_and_localizes_solver_port() -> None:
    landing = SimpleNamespace(
        via_id="V_GRAPH",
        net="VDD",
        endpoint_node_id="NODE_SOURCE",
        x_um=10.0,
        y_um=20.0,
        padstack="P1",
        rotation_degrees=0.0,
    )
    contacts = (("NODE_TARGET", 110.0, 220.0),)
    contact_hash = sha256(repr(contacts).encode("utf-8")).hexdigest()
    recovery = SimpleNamespace(evidence_by_via={}, structural_evidence_by_via={})
    graph = SimpleNamespace(
        target_contacts_by_key={("v_graph", "node_source", "l09"): contacts},
        target_contact_count_by_key={("v_graph", "node_source", "l09"): 1},
        target_contact_hash_by_key={("v_graph", "node_source", "l09"): contact_hash},
    )
    converted = _scenario_via_landing(landing, recovery, graph, "a" * 64)
    assert (converted.x_um, converted.y_um) == (10.0, 20.0)
    evidence = converted.graph_contact_for_layer("L09")
    assert evidence is not None
    assert (evidence.x_um, evidence.y_um) == (110.0, 220.0)
    footprint = _terminal_footprint(
        converted,
        "L09",
        SimpleNamespace(finite_port_width_um=20.0, finite_port_height_um=20.0),
    )
    assert (footprint.x_um, footprint.y_um) == (110.0, 220.0)


def test_graph_contact_lookup_preserves_multiple_target_layers_without_global_scan() -> None:
    landing = SimpleNamespace(
        via_id="V_LOOKUP",
        net="VDD",
        endpoint_node_id="NODE_SOURCE",
        x_um=10.0,
        y_um=20.0,
        padstack="P1",
        rotation_degrees=0.0,
    )
    recovery = SimpleNamespace(evidence_by_via={}, structural_evidence_by_via={})
    contacts = {
        ("v_lookup", "node_source", "L09"): (("N09", 110.0, 220.0),),
        ("v_lookup", "node_source", "L08"): (("N08", 111.0, 221.0),),
    }
    graph = SimpleNamespace(
        target_contacts_by_key=contacts,
        target_contact_count_by_key={key: 1 for key in contacts},
        target_contact_hash_by_key={key: sha256(repr(value).encode()).hexdigest() for key, value in contacts.items()},
    )
    lookup = (
        ("L09", contacts[("v_lookup", "node_source", "L09")]),
        ("L08", contacts[("v_lookup", "node_source", "L08")]),
    )
    converted = _scenario_via_landing(
        landing, recovery, graph, "b" * 64,
        contact_lookup={("v_lookup", "node_source"): lookup},
    )
    assert {
        item.target_layer: (item.target_node_id, item.x_um, item.y_um)
        for item in converted.graph_contact_evidence
    } == {
        "L09": ("N09", 110.0, 220.0),
        "L08": ("N08", 111.0, 221.0),
    }


def test_raw_pin_source_identity_is_retained_for_graph_witnesses(tmp_path: Path) -> None:
    source = tmp_path / "pin-source.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    analysis = analyze_spd(source, scope="decap_scenario")
    device_pins = [item for item in analysis.pins if item.kind == PinKind.DEVICE_BUMP]
    assert device_pins
    assert all(item.source_node_id for item in device_pins)


def test_explicit_source_pair_map_missing_rail_fails_closed() -> None:
    project = SimpleNamespace(
        stackup_layers=(
            StackupLayer(name="TOP", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=["VDD"]),
            StackupLayer(name="D1", thickness_um=100.0, dk=4.0, df=0.01),
            StackupLayer(name="PWR", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=["DGND"]),
        ),
        gnd_aliases=("DGND",),
        rails=(),
        pins=(
            PinRecord(
                refdes="SITE0",
                pin="1",
                net="VDD",
                x_um=1.0,
                y_um=1.0,
                kind=PinKind.DEVICE_BUMP,
                terminal=TerminalKind.PWR,
            ),
        ),
    )
    with pytest.raises(ValueError, match="source-proven plane-pair selection is missing"):
        core_services._derive_rails(
            project,
            selected_pairs={"OTHER": SimpleNamespace(pwr_layer="TOP", gnd_layer="PWR")},
        )


def test_importer_persists_strict_conventional_landing_certificate() -> None:
    metadata = _build_mlo_landing_certificates(
        (
            SimpleNamespace(
                via_id="V-PTH",
                padstack="PTH",
            ),
        ),
        padstacks=(
            SimpleNamespace(
                name="PTH",
                layers=("TOP", "GND"),
                drill_diameter_um=250.0,
                material="COPPER",
            ),
        ),
        stackup_layers=(
            SimpleNamespace(name="TOP", is_conductor=True),
            SimpleNamespace(name="GND", is_conductor=True),
        ),
        source_sha256="a" * 64,
    )
    parsed = parse_mlo_landing_certificates(
        metadata,
        expected_source_sha256="a" * 64,
    )
    certificate = parsed["v-pth"]
    assert certificate.classification == MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA
    assert certificate.padstack == "PTH"
    assert certificate.span_layers == ("GND", "TOP")
    assert certificate.drill_diameter_um == 250.0
    assert certificate.padstack_material == "COPPER"


def test_importer_classifies_real_shaped_short_span_padstack() -> None:
    metadata = _build_mlo_landing_certificates(
        (SimpleNamespace(via_id="V-MLO", padstack="DR-0102"),),
        padstacks=(
            SimpleNamespace(
                name="DR-0102",
                layers=("TOP", "L02"),
                drill_diameter_um=100.0,
                material="COPPER",
            ),
        ),
        stackup_layers=(
            SimpleNamespace(name="TOP", is_conductor=True),
            SimpleNamespace(name="L02", is_conductor=True),
            SimpleNamespace(name="GND", is_conductor=True),
        ),
        source_sha256="b" * 64,
    )
    parsed = parse_mlo_landing_certificates(
        metadata,
        expected_source_sha256="b" * 64,
    )
    assert parsed["v-mlo"].classification == MLO_LANDING_CLASS_SHORT_SPAN_VIA
    assert metadata["short_span_count"] == 1


@pytest.mark.parametrize("material", [None, "ALUMINUM"])
def test_importer_does_not_certify_unknown_or_non_copper_pth(material: str | None) -> None:
    metadata = _build_mlo_landing_certificates(
        (SimpleNamespace(via_id="V-NONCOPPER", padstack="PTH"),),
        padstacks=(
            SimpleNamespace(
                name="PTH",
                layers=("TOP", "GND"),
                drill_diameter_um=250.0,
                material=material,
            ),
        ),
        stackup_layers=(
            SimpleNamespace(name="TOP", is_conductor=True),
            SimpleNamespace(name="GND", is_conductor=True),
        ),
        source_sha256="c" * 64,
    )
    parsed = parse_mlo_landing_certificates(
        metadata,
        expected_source_sha256="c" * 64,
    )
    assert parsed["v-noncopper"].classification == "UNRESOLVED"
    assert metadata["conventional_count"] == 0


def test_read_only_spd_import_builds_top_side_editable_scenario(tmp_path: Path):
    source = tmp_path / "source.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    before = source.read_bytes()

    imported = import_spd_scenario(source)

    assert source.read_bytes() == before
    assert imported.scenario.source.path == str(source.resolve())
    assert len(imported.scenario.source.sha256) == 64
    assert imported.scenario.connection_analysis is not None
    assert imported.scenario.connection_analysis.version == SHARED_PAD_ANALYSIS_VERSION
    assert [item.refdes for item in imported.scenario.decaps] == ["C1", "C2"]
    decap = imported.scenario.decaps[0]
    assert decap.pwr_pad.x_um == 1_000.0
    assert decap.pwr_pad.y_um == 2_000.0
    assert decap.center.x_um == 1_100.0
    assert decap.enabled
    assert decap.eligibility[decap.current_rail_id].allowed
    assert imported.scenario.net_colors[decap.current_net].startswith("#")
    assert imported.scenario.base_project.placements == []
    assert imported.scenario.base_project.topology_maps == []
    assert all(item.confirmed for item in imported.scenario.base_project.partitions)
    spd_import = imported.scenario.base_project.metadata["spd_import"]
    assert spd_import["final_template_footprint_validation"]["version"] == (
        FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION
    )
    assert spd_import["selected_plane_pair_provenance"]["VDD_CORE/0"][
        "final_template_footprint"
    ]
    assert (
        spd_import["selected_plane_pair_provenance"]["VDD_CORE/0"][
            "final_template_footprint"
        ]["contract_version"]
        == FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION
    )


def test_forced_post_plan_rebuild_inputs_summary_matches_final_rails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "forced-rebuild.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    monkeypatch.setattr(
        spd_adapter,
        "_finite_port_inside_solver_bounds",
        lambda *_args: False,
    )
    geometry_builds = 0
    original_geometry_assets = core_services._spd_plane_geometry_assets

    def counted_geometry_assets(*args, **kwargs):
        nonlocal geometry_builds
        geometry_builds += 1
        return original_geometry_assets(*args, **kwargs)

    monkeypatch.setattr(
        core_services, "_spd_plane_geometry_assets", counted_geometry_assets
    )
    imported = import_spd_scenario(source)
    assert geometry_builds == 1
    input_assets = [
        payload
        for name, payload in imported.attachments.items()
        if name.startswith("inputs/") and name.endswith("-spd-import.json")
    ]
    assert len(input_assets) == 1
    summary = json.loads(input_assets[0].decode("utf-8"))
    final_rails = [
        item.model_dump(mode="json")
        for item in imported.scenario.base_project.rails
    ]
    assert summary["extracted"]["rails"] == final_rails


def test_adapter_clears_gap_certificate_when_cluster_is_demoted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "demoted-shared-pad.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    parsed = analyze_spd(source, scope="decap_scenario")
    by_refdes = {item.refdes: item for item in parsed.decap_connections}
    cluster_id = "SPDCL:SOURCE-CERTIFIED-GAP"
    source_cluster = SpdSharedPadCluster(
        cluster_id=cluster_id,
        state="ANCHORED",
        member_refdes=("C1", "C2"),
        anchor_refdes=("C1",),
        dummy_refdes=("C2",),
        power_net="VDD_CORE/0",
        ground_net="DGND",
        layer="Signal$TOP",
        power_edges=(("C1", "C2"),),
        ground_edges=(("C1", "C2"),),
        isolation_gap_refdes=("C1", "C2"),
    )
    source_connections = (
        replace(
            by_refdes["C1"],
            kind="SHARED_ANCHOR",
            cluster_id=cluster_id,
        ),
        SpdDecapConnection(
            refdes="C2",
            kind="SHARED_DUMMY",
            cluster_id=cluster_id,
        ),
    )
    source_analysis = replace(
        parsed,
        decap_connections=source_connections,
        shared_pad_clusters=(source_cluster,),
    )
    monkeypatch.setattr(
        spd_adapter,
        "analyze_spd",
        lambda *_args, **_kwargs: source_analysis,
    )

    imported = import_spd_scenario(source)

    analysis = imported.scenario.connection_analysis
    assert analysis is not None
    assert len(analysis.clusters) == 1
    cluster = analysis.clusters[0]
    assert cluster.state.value == "UNRESOLVED"
    assert cluster.reason == (
        "shared-pad members resolve to different source rail identities"
    )
    assert cluster.isolation_gap_refdes == ()
    assert cluster.eligibility == {}
    assert cluster.via_eligibility == {}
    assert {
        connection.kind.value for connection in analysis.connections.values()
    } == {"UNRESOLVED"}


def test_source_unselected_physical_powernet_reaches_project_rails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-unselected-power.spd"
    payload = MINI_SPD.replace(
        "VDD_CORE/0 -> PowerNets Voltage = 0\n"
        "VDD_DROP/0::Unselected||DropShape",
        "VDD_MARKER/0 -> PowerNets Voltage = 0\n"
        "VDD_CORE/0::Unselected||DropShape\n"
        "VDD_DROP/0::Unselected||DropShape",
    )
    source.write_text(payload, encoding="ascii")

    imported = import_spd_scenario(source)

    assert [item.net for item in imported.scenario.base_project.rails] == [
        "VDD_CORE/0"
    ]
    assert imported.scenario.base_project.metadata["spd_import"][
        "selected_power_nets"
    ] == ["VDD_CORE/0"]


def test_optional_routing_asset_bounds_do_not_break_legacy_spd_import(
    tmp_path: Path,
) -> None:
    source = tmp_path / "out-of-range-signal-trace.spd"
    payload = MINI_SPD.replace(
        "* Via description lines",
        "NodeRoute1::SIG_A X = 2000000000mm Y = 0mm "
        "Layer = Signal$TOP\n"
        "NodeRoute2::SIG_A X = 0mm Y = 0mm Layer = Signal$TOP\n"
        "* Trace description lines\n"
        "TraceRoute::SIG_A StartingNode = NodeRoute1::SIG_A "
        "EndingNode = NodeRoute2::SIG_A Width = 20um\n"
        "* Via description lines",
    ).replace(
        ".NetList\nDGND -> GroundNets",
        ".NetList\nSIG_A\nDGND -> GroundNets",
    )
    source.write_text(payload, encoding="ascii")

    imported = import_spd_scenario(source)

    assert [item.refdes for item in imported.scenario.decaps] == ["C1", "C2"]
    reference = imported.scenario.routing_obstacle_asset
    assert reference is not None
    asset = decode_routing_obstacle_asset(
        imported.attachments[reference.attachment_name],
        expected_source_sha256=imported.scenario.source.sha256,
        expected_stackup_fingerprint=reference.stackup_fingerprint,
    )
    top = next(
        item for item in asset.layer_completeness if item.layer == "Signal$TOP"
    )
    assert "TRACE_GEOMETRY_OUT_OF_RANGE" in top.unresolved_codes
    assert asset.via_profiles
    proof = evaluate_routing_candidate(
        asset,
        x_um=1_000.0,
        y_um=2_000.0,
        destination_layer="Signal$PWR",
        mount_side="TOP",
        profile_id=asset.via_profiles[0].profile_id,
        policy=SignalTraceAvoidancePolicy.fixed(0.0),
    )
    assert proof.state == RoutingCandidateState.UNKNOWN


def test_optional_routing_attachment_failure_does_not_break_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "optional-routing-attachment-failure.spd"
    payload = MINI_SPD.replace(
        "* Via description lines",
        "NodeRoute1::SIG_A X = 0mm Y = 0mm Layer = Signal$TOP\n"
        "NodeRoute2::SIG_A X = 1mm Y = 0mm Layer = Signal$TOP\n"
        "* Trace description lines\n"
        "TraceRoute::SIG_A StartingNode = NodeRoute1::SIG_A "
        "EndingNode = NodeRoute2::SIG_A Width = 20um\n"
        "* Via description lines",
    ).replace(
        ".NetList\nDGND -> GroundNets",
        ".NetList\nSIG_A\nDGND -> GroundNets",
    )
    source.write_text(payload, encoding="ascii")
    monkeypatch.setattr(
        spd_adapter,
        "encode_routing_obstacle_asset",
        lambda _asset: (_ for _ in ()).throw(ValueError("fixture asset failure")),
    )

    imported = import_spd_scenario(source)

    assert [item.refdes for item in imported.scenario.decaps] == ["C1", "C2"]
    assert imported.scenario.routing_obstacle_asset is None
    assert any(
        item.code == "SPD_SIGNAL_ROUTING_RESEARCH_ASSET_UNAVAILABLE"
        for item in imported.diagnostics
    )


def test_adapter_preserves_many_to_many_cluster_via_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "many-to-many.spd"
    payload = MINI_SPD.replace(
        "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
        "Node4!!2::DGND X = 1.6mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
    ).replace(
        "Node5!!1::VDD_DROP/0 X = 3mm Y = 2mm "
        "Layer = Signal$TOP PadStack = CAP",
        "Node5!!1::VDD_CORE/0 X = 1mm Y = 2.35mm "
        "Layer = Signal$TOP PadStack = CAP",
    ).replace(
        "Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
        "Node6!!2::DGND X = 1.6mm Y = 2.35mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node7!!1::VDD_CORE/0 X = 1mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node8!!2::DGND X = 1.6mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node101::VDD_CORE/0 X = 1mm Y = 2.175mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node102::VDD_CORE/0 X = 1mm Y = 2.175mm "
        "Layer = Signal$PWR PadStack = DR-0102_60\n"
        "Node103::VDD_CORE/0 X = 1mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node104::VDD_CORE/0 X = 1mm Y = 2.70mm "
        "Layer = Signal$PWR PadStack = DR-0102_60\n"
        "Node105::DGND X = 1.6mm Y = 2mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node106::DGND X = 1.6mm Y = 2mm "
        "Layer = Signal$GND PadStack = DR-0102_60\n"
        "Node107::DGND X = 1.6mm Y = 2.525mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node108::DGND X = 1.6mm Y = 2.525mm "
        "Layer = Signal$GND PadStack = DR-0102_60\n"
        "Node109::DGND X = 1.6mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node110::DGND X = 1.6mm Y = 2.70mm "
        "Layer = Signal$GND PadStack = DR-0102_60",
    ).replace(
        "Via1::VDD_CORE/0 UpperNode = Node1 LowerNode = Node3 "
        "PadStack = DR-0102_60\n"
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 "
        "PadStack = DR-0102_60",
        "ViaP_AB::VDD_CORE/0 UpperNode = Node101::VDD_CORE/0 "
        "LowerNode = Node102::VDD_CORE/0 PadStack = DR-0102_60\n"
        "ViaP_C::VDD_CORE/0 UpperNode = Node103::VDD_CORE/0 "
        "LowerNode = Node104::VDD_CORE/0 PadStack = DR-0102_60\n"
        "ViaG_A::DGND UpperNode = Node105::DGND "
        "LowerNode = Node106::DGND PadStack = DR-0102_60\n"
        "ViaG_BC::DGND UpperNode = Node107::DGND "
        "LowerNode = Node108::DGND PadStack = DR-0102_60\n"
        "ViaG_C::DGND UpperNode = Node109::DGND "
        "LowerNode = Node110::DGND PadStack = DR-0102_60",
    ).replace(
        "Regular Square 0.10mm",
        "Regular Square 0.40mm",
    ).replace(
        ".Connect C2 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node5!!1::VDD_DROP/0\n"
        "2 $Package.Node6!!2::DGND\n"
        ".EndC",
        ".Connect C2 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node5!!1::VDD_CORE/0\n"
        "2 $Package.Node6!!2::DGND\n"
        ".EndC\n"
        ".Connect C3 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node7!!1::VDD_CORE/0\n"
        "2 $Package.Node8!!2::DGND\n"
        ".EndC",
    ).replace(
        ".Component C1 1.1mm 2mm Rotation = 90 StartLayer = Signal$TOP\n"
        ".Component C2 3.1mm 2mm StartLayer = Signal$TOP",
        ".Component C1 1.3mm 2mm StartLayer = Signal$TOP\n"
        ".Component C2 1.3mm 2.35mm StartLayer = Signal$TOP\n"
        ".Component C3 1.3mm 2.70mm StartLayer = Signal$TOP",
    ).replace(
        "VDD_DROP/0::Unselected||DropShape\n",
        "",
    )
    source.write_text(payload, encoding="ascii")

    imported = import_spd_scenario(source)

    analysis = imported.scenario.connection_analysis
    assert analysis is not None
    assert len(analysis.clusters) == 1
    cluster = analysis.clusters[0]
    assert cluster.anchor_refdes == ("C1", "C2", "C3")
    selected_provenance = (
        imported.scenario.normalized_project.get("metadata", {})
        .get("spd_import", {})
        .get("selected_plane_pair_provenance", {})
    )
    assert selected_provenance["VDD_CORE/0"]["pwr_layer"] == "Signal$PWR"
    assert selected_provenance["VDD_CORE/0"]["gnd_layer"] == "Signal$GND"
    assert selected_provenance["VDD_CORE/0"]["route_witnesses"]
    assert set(cluster.via_eligibility) == {"ViaP_AB", "ViaP_C"}
    assert all(
        set(item) == {"VDD_CORE/0"}
        for item in cluster.via_eligibility.values()
    )
    assert not {"ViaG_A", "ViaG_BC", "ViaG_C"}.intersection(
        cluster.via_eligibility
    )
    by_refdes = analysis.connections
    power_ownership = {
        refdes: tuple(landing.via_id for landing in connection.power_vias)
        for refdes, connection in by_refdes.items()
    }
    ground_ownership = {
        refdes: tuple(landing.via_id for landing in connection.ground_vias)
        for refdes, connection in by_refdes.items()
    }
    assert power_ownership == {
        "C1": ("ViaP_AB",),
        "C2": ("ViaP_AB",),
        "C3": ("ViaP_C",),
    }
    assert ground_ownership == {
        "C1": ("ViaG_A",),
        "C2": ("ViaG_BC",),
        "C3": ("ViaG_BC", "ViaG_C"),
    }
    assert set(
        landing.via_id.casefold()
        for connection in by_refdes.values()
        for landing in connection.power_vias
    ) == {"viap_ab", "viap_c"}
    assert set(
        landing.via_id.casefold()
        for connection in by_refdes.values()
        for landing in connection.ground_vias
    ) == {"viag_a", "viag_bc", "viag_c"}
    # Shared ownership appears once per owning connection.  There is no
    # PWR/GND Cartesian pairing or duplicate evidence inside one owner.
    assert sum(len(item.power_vias) for item in by_refdes.values()) == 3
    assert sum(len(item.ground_vias) for item in by_refdes.values()) == 4
    assert all(
        len(connection.power_vias)
        == len({landing.via_id.casefold() for landing in connection.power_vias})
        and len(connection.ground_vias)
        == len({landing.via_id.casefold() for landing in connection.ground_vias})
        for connection in by_refdes.values()
    )


def test_spd_import_reports_monotonic_stage_progress_and_nonpersistent_timings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "timed-source.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    progress: list[tuple[int, str]] = []

    imported = import_spd_scenario(
        source,
        progress=lambda value, message: progress.append((value, message)),
    )

    values = [value for value, _message in progress]
    assert values == sorted(values)
    messages = "\n".join(message for _value, message in progress)
    assert "Parsed the SPD" in messages
    assert "Normalized exact plane geometry" in messages
    assert "Indexed " in messages
    assert "Recovering source-proven vertical Via paths" in messages
    assert "Selecting mixed-reference GND witness landings" in messages
    assert "Checking exact PWR-plane eligibility" in messages
    assert "validating scenario" in messages
    assert progress[-1][0] == 100

    timings = imported.timings
    stages = (
        timings.analyze_s,
        timings.plan_s,
        timings.index_s,
        timings.recovery_s,
        timings.mixed_witness_selection_s,
        timings.ground_recovery_s,
        timings.eligibility_s,
        timings.finalize_s,
    )
    assert all(math.isfinite(value) and value >= 0.0 for value in stages)
    assert timings.total_s >= sum(stages)
    assert "timings" not in imported.scenario.model_dump(mode="json")


def test_spd_import_reuses_one_ground_graph_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "single-ground-graph-pass.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    calls: list[dict[str, object]] = []
    original = spd_adapter.recover_spd_ground_reachability

    def wrapped(*args, **kwargs):
        calls.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(spd_adapter, "recover_spd_ground_reachability", wrapped)
    imported = import_spd_scenario(source)

    assert len(calls) == 1
    assert imported.timings.recovery_s > 0.0
    mixed = imported.scenario.base_project.metadata["spd_via_path_recovery"][
        "mixed_reference_ground_reachability"
    ]
    assert mixed["requested"] == 0
    assert mixed["reachable"] == 0
    assert mixed["unreachable"] == 0
    assert mixed["node_section_passes"] == 0
    assert mixed["trace_section_passes"] == 0
    assert mixed["via_section_passes"] == 0
    assert mixed["shared_source_graph_reused"] is True


def test_mixed_witness_selection_batches_direct_and_shared_candidates_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inverted traversal must preserve the former rail-major witnesses."""

    def landing(via_id: str, net: str) -> SimpleNamespace:
        return SimpleNamespace(
            via_id=via_id,
            net=net,
            endpoint_node_id=f"NODE-{via_id}",
        )

    def allowed(rail_id: str) -> SimpleNamespace:
        return SimpleNamespace(rail_id=rail_id, allowed=True)

    r1 = SimpleNamespace(
        rail_id="R1",
        mixed_reference_certificate=SimpleNamespace(
            gnd_net="DGND", gnd_layer="L10"
        ),
    )
    r2 = SimpleNamespace(
        rail_id="R2",
        mixed_reference_certificate=SimpleNamespace(
            gnd_net="AGND", gnd_layer="L12"
        ),
    )
    mixed_rails = {"r1": r1, "r2": r2}
    connections = {
        "d1": SimpleNamespace(
            kind="DIRECT", cluster_id=None,
            power_vias=(landing("P-D1", "VDD"),),
            ground_vias=(landing("G-D1-D", "DGND"), landing("G-D1-A", "AGND")),
        ),
        "s1": SimpleNamespace(
            kind="SHARED_ANCHOR", cluster_id="CL-A",
            power_vias=(landing("P-S1", "VDD"),),
            ground_vias=(landing("G-S1-D", "DGND"), landing("G-S1-A", "AGND")),
        ),
        "s2": SimpleNamespace(
            kind="SHARED_ANCHOR", cluster_id="CL-A",
            power_vias=(landing("P-S2", "VDD"),),
            ground_vias=(landing("G-S2-A", "AGND"),),
        ),
        "d4": SimpleNamespace(
            kind="DIRECT", cluster_id=None,
            power_vias=(landing("P-D4", "VDD"),),
            ground_vias=(landing("G-D4-A", "AGND"),),
        ),
    }
    clusters = {
        "cl-a": SimpleNamespace(cluster_id="CL-A", member_refdes=("S1", "S2")),
    }
    top_instances = tuple(
        SimpleNamespace(refdes=refdes) for refdes in ("D1", "S2", "S1", "D4")
    )
    direct_calls: list[tuple[str, ...]] = []
    shared_calls: list[str] = []

    def fake_common_at_landings(_index, power_vias, _choices):
        keys = tuple(item.via_id for item in power_vias)
        direct_calls.append(keys)
        return {
            "P-D1": {"R1": allowed("R1"), "R2": allowed("R2")},
            "P-D4": {"R2": allowed("R2")},
        }[keys[0]]

    def fake_eligibility_for_via(_index, value, _choices):
        shared_calls.append(value.via_id)
        return {
            "P-S1": {"R1": allowed("R1"), "R2": allowed("R2")},
            "P-S2": {"R2": allowed("R2")},
        }[value.via_id]

    monkeypatch.setattr(spd_adapter, "_scenario_via_landing", lambda value, _recovery: value)
    monkeypatch.setattr(
        spd_adapter, "_common_eligibility_at_landings", fake_common_at_landings
    )
    monkeypatch.setattr(
        spd_adapter, "_eligibility_for_via_landing", fake_eligibility_for_via
    )

    def rail_major_reference():
        result = {key: [] for key in mixed_rails}
        targets: dict[str, set[str]] = {}
        shared_cluster_rail_eligibility: dict[tuple[str, str], bool] = {}
        for rail_key, rail in mixed_rails.items():
            processed_shared_clusters: set[str] = set()
            certificate = rail.mixed_reference_certificate
            targets.setdefault(certificate.gnd_net.casefold(), set()).add(
                certificate.gnd_layer
            )
            for instance in top_instances:
                connection = connections.get(instance.refdes.casefold())
                if connection is None or connection.kind not in {
                    "DIRECT", "SHARED_ANCHOR"
                }:
                    continue
                if connection.kind == "DIRECT":
                    power_vias = tuple(
                        spd_adapter._scenario_via_landing(item, object())
                        for item in connection.power_vias
                    )
                    eligible = spd_adapter._common_eligibility_at_landings(
                        object(), power_vias, {}
                    )
                    candidate = next(
                        (
                            item for rail_id, item in eligible.items()
                            if rail_id.casefold() == rail_key
                        ),
                        None,
                    )
                    if candidate is None or not candidate.allowed:
                        continue
                else:
                    cluster_key = connection.cluster_id.casefold()
                    cache_key = (cluster_key, rail_key)
                    candidate_allowed = shared_cluster_rail_eligibility.get(cache_key)
                    if candidate_allowed is None:
                        cluster = clusters.get(cluster_key)
                        if cluster is None:
                            candidate_allowed = False
                        else:
                            power_landings = {
                                item.via_id.casefold(): spd_adapter._scenario_via_landing(
                                    item, object()
                                )
                                for member in cluster.member_refdes
                                for item in connections[member.casefold()].power_vias
                            }
                            common = spd_adapter._common_eligibility_maps(
                                tuple(
                                    spd_adapter._eligibility_for_via_landing(
                                        object(), item, {}
                                    )
                                    for item in power_landings.values()
                                )
                            )
                            candidate = next(
                                (
                                    item for rail_id, item in common.items()
                                    if rail_id.casefold() == rail_key
                                ),
                                None,
                            )
                            candidate_allowed = bool(
                                candidate is not None and candidate.allowed
                            )
                        shared_cluster_rail_eligibility[cache_key] = candidate_allowed
                    if not candidate_allowed:
                        continue
                    cluster = clusters[cluster_key]
                    if cluster_key in processed_shared_clusters:
                        continue
                    processed_shared_clusters.add(cluster_key)
                    owner = f"cluster:{cluster.cluster_id}"
                    for member in cluster.member_refdes:
                        for item in connections[member.casefold()].ground_vias:
                            if item.net.casefold() == certificate.gnd_net.casefold():
                                result[rail_key].append((owner, item))
                    continue
                owner = instance.refdes
                for item in connection.ground_vias:
                    if item.net.casefold() == certificate.gnd_net.casefold():
                        result[rail_key].append((owner, item))
        return result, targets

    reference, reference_targets = rail_major_reference()
    direct_calls.clear()
    shared_calls.clear()
    actual, targets = spd_adapter._select_mixed_reference_ground_landings(
        mixed_rails=mixed_rails,
        top_instances=top_instances,
        parsed_connection_by_key=connections,
        parsed_cluster_by_key=clusters,
        path_recovery=object(),
        eligibility_index=object(),
        rail_choices_by_pair={},
    )

    assert actual == reference
    assert targets == reference_targets == {
        "dgnd": {"L10"}, "agnd": {"L12"}
    }
    assert direct_calls == [("P-D1",), ("P-D4",)]
    assert shared_calls == ["P-S1", "P-S2"]
    assert {
        key: tuple(
            mixed_reference_ground_landing_identity(owner, value)
            for owner, value in entries
        )
        for key, entries in actual.items()
    } == {
        "r1": ("d1|gnd|g-d1-d|dgnd|node-g-d1-d",),
        "r2": (
            "d1|gnd|g-d1-a|agnd|node-g-d1-a",
            "cluster:cl-a|gnd|g-s1-a|agnd|node-g-s1-a",
            "cluster:cl-a|gnd|g-s2-a|agnd|node-g-s2-a",
            "d4|gnd|g-d4-a|agnd|node-g-d4-a",
        ),
    }


def test_spd_via_recovery_metadata_is_deterministic_and_excludes_wall_time(
    tmp_path: Path,
) -> None:
    source = tmp_path / "deterministic-recovery.spd"
    source.write_text(MINI_SPD, encoding="ascii")

    first = import_spd_scenario(source).scenario
    second = import_spd_scenario(source).scenario

    first_metadata = first.base_project.metadata["spd_via_path_recovery"]
    second_metadata = second.base_project.metadata["spd_via_path_recovery"]
    assert "elapsed_s" not in first_metadata
    assert first_metadata == second_metadata
    assert first.design_fingerprint == second.design_fingerprint


def test_dnp_is_included_but_initially_disabled_when_source_marks_unmounted(
    tmp_path: Path,
):
    source = tmp_path / "dnp.spd"
    source.write_text(
        MINI_SPD.replace(
            ".Connect C1 CAP_0402_100NF Checked = 1",
            ".Connect C1 CAP_0402_100NF_NOT_MOUNTED Usage = 0b111000",
        ),
        encoding="ascii",
    )

    imported = import_spd_scenario(source)

    assert len(imported.scenario.decaps) == 2
    c1 = next(item for item in imported.scenario.decaps if item.refdes == "C1")
    assert c1.source_mounted is False
    assert c1.enabled is False


def test_mounted_unmodeled_source_decap_stays_enabled_for_model_assignment(
    tmp_path: Path,
):
    source = tmp_path / "unmodeled.spd"
    source.write_text(
        MINI_SPD.replace(
            ".Connect C1 CAP_0402_100NF Checked = 1",
            ".Connect C1 CAP_UNMODELED_0402 Checked = 1",
        ),
        encoding="ascii",
    )

    imported = import_spd_scenario(source)

    c1 = next(item for item in imported.scenario.decaps if item.refdes == "C1")
    assert c1.source_mounted is True
    assert c1.enabled is True
    assert c1.source_model_id is None
    assert c1.model_id is None


def test_incomplete_selected_plane_geometry_blocks_editable_scenario(
    tmp_path: Path,
):
    source = tmp_path / "malformed.spd"
    source.write_text(
        MINI_SPD.replace(
            ".EndShape\n* Layer description lines",
            "Circle99::VDD_CORE/0- Sub-element 1mm 2mm\n"
            ".EndShape\n* Layer description lines",
        ),
        encoding="ascii",
    )

    with pytest.raises(SpdImportError, match="exact evaluation geometry"):
        import_spd_scenario(source)


def test_oversized_plane_artwork_is_blocked_before_scenario_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "oversized-artwork.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    monkeypatch.setattr(
        core_services,
        "_SPD_GEOMETRY_MAX_UNCOMPRESSED_BYTES",
        128,
    )

    with pytest.raises(
        SpdImportError,
        match="SPD_PLANE_GEOMETRY_ASSET_TOO_LARGE",
    ):
        import_spd_scenario(source)


def test_rejected_l11_pair_is_recovered_and_blocks_fallback_top_pair() -> None:
    """Rejected pair targets must be requested before fallback can hide them."""

    failure = {
        "rail_net": "VCPU",
        "pwr_layer": "L11",
        "gnd_layer": "L10",
        "gnd_net": "DGND",
        "reason": "coverage below v1 threshold",
    }
    project = SimpleNamespace(
        rails=(
            SimpleNamespace(
                net="VCPU",
                pwr_layer="TOP",
                gnd_layer="L02",
                mixed_reference_certificate=None,
            ),
        ),
        stackup_layers=(SimpleNamespace(name="L02", pwr_nets=("DGND",)),),
        gnd_aliases=("DGND", "GND"),
        metadata={
            "spd_import": {
                "mixed_reference_certificate_failures": [failure],
            }
        },
    )

    targets = _via_target_layers_by_net(project)

    assert targets["vcpu"] == ("L11", "TOP")
    assert targets["dgnd"] == ("L02",)
    landing = SimpleNamespace(via_id="VP-VCPU", net="VCPU")
    recovery = SimpleNamespace(
        evidence_by_via={
            "vp-vcpu": (SimpleNamespace(target_layer="L11"),),
        }
    )
    with pytest.raises(SpdImportError, match=r"VCPU L11/L10"):
        _raise_for_rejected_mixed_reference_landings(
            project,
            (landing,),
            recovery,
        )


def test_nonblocking_mixed_reference_failure_does_not_abort_legacy_path() -> None:
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "mixed_reference_certificate_failures": [
                    {
                        "rail_net": "VCPU",
                        "pwr_layer": "L11",
                        "gnd_layer": "L10",
                        "blocking": False,
                    }
                ]
            }
        }
    )
    landing = SimpleNamespace(via_id="VP-VCPU", net="VCPU")
    recovery = SimpleNamespace(
        evidence_by_via={
            "vp-vcpu": (SimpleNamespace(target_layer="L11"),),
        }
    )
    _raise_for_rejected_mixed_reference_landings(project, (landing,), recovery)


def test_via_target_layers_include_all_retained_same_net_planes() -> None:
    project = SimpleNamespace(
        rails=(
            SimpleNamespace(
                net="VCPU",
                pwr_layer="L12",
                gnd_layer="L02",
                mixed_reference_certificate=None,
            ),
        ),
        stackup_layers=(SimpleNamespace(name="L02", pwr_nets=("DGND",)),),
        gnd_aliases=("DGND",),
        metadata={},
    )
    geometries = (
        SimpleNamespace(net="VCPU", layer="L09"),
        SimpleNamespace(net="VCPU", layer="L11"),
        SimpleNamespace(net="OTHER", layer="L14"),
        SimpleNamespace(net="DGND", layer="L03"),
        SimpleNamespace(net="DGND", layer="L04"),
    )
    targets = _via_target_layers_by_net(project, plane_geometries=geometries)
    assert targets["vcpu"] == ("L09", "L11", "L12")
    assert targets["other"] == ("L14",)
    assert targets["dgnd"] == ("L02",)


def test_certified_ground_attachment_tamper_cannot_be_saved(
    tmp_path: Path,
) -> None:
    source = tmp_path / "certified-mixed.spd"
    payload = MINI_SPD.replace(
        "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
        "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
        "Node7!!7::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
    ).replace(
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
        "Via7::DGND UpperNode = Node4 LowerNode = Node7 PadStack = DR-0102_60",
    ).replace(
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
        "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
    )
    source.write_text(payload, encoding="ascii")
    imported = import_spd_scenario(source)
    rail = imported.scenario.base_project.rails[0]
    certificate = rail.mixed_reference_certificate
    assert certificate is not None
    records = imported.scenario.base_project.metadata["spd_import"][
        "plane_geometries"
    ]
    gnd_record = next(
        item for item in records
        if item["layer"] == rail.gnd_layer and item["net"] == certificate.gnd_net
    )
    asset = gnd_record["asset"]
    tampered = dict(imported.attachments)
    tampered[asset] = tampered[asset] + b"tampered"

    with pytest.raises(
        ScenarioFormatError,
        match="certified artwork",
    ):
        save_scenario(
            imported.scenario,
            tmp_path / "tampered.spdpi",
            attachments=tampered,
        )


def test_colliding_safe_model_ids_keep_distinct_source_assets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "colliding-model-ids.spd"
    payload = MINI_SPD.replace(
        ".PartialCkt CAP_0402_100NF_NOT_MOUNTED ExtNode = 1 2\n"
        ".EndPartialCkt",
        ".PartialCkt CAP-0402-100NF ExtNode = 1 2\n"
        "R1 1 X 0.02\n"
        "L1 X Y 0.3n\n"
        "C1 Y 2 1u\n"
        ".EndPartialCkt",
    )
    source.write_text(payload, encoding="ascii")

    imported = import_spd_scenario(source)
    sources = imported.scenario.base_project.metadata["cap_model_sources"]
    first = sources["CAP_0402_100NF"]["source_asset"]
    second = sources["CAP-0402-100NF"]["source_asset"]

    assert first != second
    assert first in imported.attachments
    assert second in imported.attachments
    assert b".SUBCKT CAP_0402_100NF " in imported.attachments[first]
    assert b".SUBCKT CAP-0402-100NF " in imported.attachments[second]

    extra = tmp_path / "extra.lib"
    extra.write_text(
        ".SUBCKT EXTRA_CAP 1 2\n"
        "R1 1 X 0.01\n"
        "L1 X Y 0.2n\n"
        "C1 Y 2 10n\n"
        ".ENDS EXTRA_CAP\n",
        encoding="ascii",
    )
    state = WorkspaceState(
        project=imported.scenario.base_project,
        attachments=imported.attachments,
    )
    import_cap_spice(state, extra)
    assert any(item.model_id == "EXTRA_CAP" for item in state.project.cap_models)


def test_retained_surface_artwork_is_boundary_inclusive_and_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polygon = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    second_polygon = ((20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0))
    geometry = SpdPlaneGeometry(
        layer="L1",
        net="VDD",
        positive_polygons_um=(polygon, second_polygon),
        negative_polygons_um=(),
        primitive_order=(("positive_polygon", 0), ("positive_polygon", 1)),
    )
    compressed, _size = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=(polygon, second_polygon),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0), ("positive_polygon", 1)),
        positive_subelement_count=2,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )
    asset_name = "geometry/vdd-l1.json.zlib"
    digest = sha256(compressed).hexdigest()
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "plane_geometries": [
                    {
                        "layer": "L1",
                        "net": "VDD",
                        "asset": asset_name,
                        "asset_sha256": digest,
                    }
                ]
            }
        }
    )
    indexed = IndexedPlaneGeometry.build(geometry)
    assert indexed is not None
    cold_ordered_boolean_builds = 0
    component_spools: list[object] = []
    real_temporary_file = spd_adapter.TemporaryFile

    def tracked_temporary_file():
        spool = real_temporary_file()
        component_spools.append(spool)
        return spool

    monkeypatch.setattr(spd_adapter, "TemporaryFile", tracked_temporary_file)
    real_artwork_components = IndexedPlaneGeometry._artwork_components

    def count_cold_ordered_boolean_builds(
        current: IndexedPlaneGeometry,
    ) -> object:
        nonlocal cold_ordered_boolean_builds
        if -1 not in current._shape_cache:
            cold_ordered_boolean_builds += 1
        return real_artwork_components(current)

    monkeypatch.setattr(
        IndexedPlaneGeometry,
        "_artwork_components",
        count_cold_ordered_boolean_builds,
    )

    retained = spd_adapter._retained_surface_artwork(
        project,
        {asset_name: compressed},
        (geometry,),
        indexed_geometry_by_key={("l1", "vdd"): indexed},
    )
    expected = retained.island_ids_by_surface[("vdd", "l1")]

    assert -1 not in indexed._shape_cache
    assert retained.surface_resolver_batch(
        "VDD",
        "L1",
        ("edge", "inside", "second"),
        ((0.0, 5.0), (5.0, 5.0), (25.0, 5.0)),
    ) == (expected[0], expected[0], expected[1])
    assert retained.artwork_components_batch(
        "VDD", "L1", ((0.0, 5.0), (5.0, 5.0), (25.0, 5.0))
    ) == (None, 0, 1)
    retained.release("VDD", "L1")
    assert -1 not in indexed._shape_cache
    assert retained.surface_resolver(
        "VDD", "L1", "inside-again", 5.0, 5.0
    ) == expected[0]
    retained.release("VDD", "L1")
    assert cold_ordered_boolean_builds == 1
    assert component_spools[0].tell() > 0
    component_spools[0].truncate(0)
    with pytest.raises(SpdImportError, match="retained ordered artwork cannot be restored"):
        retained.surface_resolver("VDD", "L1", "truncated", 5.0, 5.0)
    retained.close()
    assert component_spools[0].closed


def test_retarget_destination_hash_does_not_duplicate_request_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    landing_count = 4097
    project = SimpleNamespace(
        rails=(
            SimpleNamespace(
                rail_id="R1", net="VDD", pwr_layer="L1", gnd_layer="L2"
            ),
            SimpleNamespace(
                rail_id="R2", net="VDD", pwr_layer="L1", gnd_layer="L2"
            ),
        ),
        stackup_layers=(
            SimpleNamespace(
                name="L1", is_conductor=True, pwr_nets=("VDD",)
            ),
        ),
        via_templates=(),
        metadata={},
    )
    connection = SimpleNamespace(
        refdes="C1",
        power_vias=tuple(
            SimpleNamespace(
                via_id=f"V{index:04d}",
                endpoint_node_id=f"N{index:04d}",
                net="VDD",
                x_um=float(index),
                y_um=float(index + 1),
            )
            for index in reversed(range(landing_count))
        ),
    )
    real_hash = core_services._canonical_metadata_sha256
    hashed_keys: list[tuple[str, ...]] = []

    def bounded_hash(payload: object) -> str:
        if isinstance(payload, dict):
            hashed_keys.append(tuple(sorted(payload)))
            assert "destinations" not in payload
        return real_hash(payload)

    monkeypatch.setattr(
        core_services, "_canonical_metadata_sha256", bounded_hash
    )
    released: list[tuple[str, str]] = []
    strict_batch_sizes: list[int] = []

    def strict_batch(
        _net: str,
        _layer: str,
        node_ids: tuple[str, ...],
        _points: tuple[tuple[float, float], ...],
    ) -> tuple[str, ...]:
        strict_batch_sizes.append(len(node_ids))
        return ("island-1",) * len(node_ids)

    requests, coverage = (
        spd_adapter._compile_retarget_landing_destination_requests(
            project=project,
            decap_connections=(connection,),
            geometry_assets=(
                {"net": "VDD", "layer": "L1", "asset_sha256": "a" * 64},
            ),
            strict_island_resolver=lambda *_args: "island-1",
            strict_island_resolver_batch=strict_batch,
            release_surface=lambda net, layer: released.append((net, layer)),
        )
    )

    identities = [
        {
            "refdes": "c1",
            "via_id": f"v{index:04d}",
            "endpoint_node_id": f"n{index:04d}",
            "destination_net": "vdd",
            "destination_layer": "l1",
            "destination_island_id": "island-1",
            "target_rail_id": rail_id.casefold(),
        }
        for index in range(landing_count)
        for rail_id in ("R1", "R2")
    ]
    assert isinstance(requests, list)
    assert strict_batch_sizes == [4096, 1]
    assert len(requests) == coverage["covered_destination_count"] == len(identities)
    assert [
        (item.via_id, item.target_rail_id) for item in requests
    ] == [
        (f"V{index:04d}", rail_id)
        for index in range(landing_count)
        for rail_id in ("R1", "R2")
    ]
    assert coverage["covered_destination_ids_sha256"] == real_hash(
        {"destinations": identities}
    )
    assert hashed_keys == [("landings",), ("surfaces",)]
    assert released == [("VDD", "L1")]


def test_bind_certified_surface_islands_is_exact_and_nonmutating() -> None:
    project_rows = [
        {
            "net": "VDD",
            "layer": "L1",
            "asset": "geometry/vdd.json",
            "asset_sha256": "a" * 64,
            "primitive_count": 7,
        },
        {
            "net": "DGND",
            "layer": "L2",
            "asset": "geometry/gnd.json",
            "asset_sha256": "b" * 64,
            "custom": {"kept": True},
        },
    ]
    certified_rows = [
        {
            "net": "dgnd",
            "layer": "l2",
            "asset": "geometry/gnd.json",
            "asset_sha256": "B" * 64,
            "island_ids": ["island:gnd:2", "island:gnd:1"],
        },
        {
            "net": "vdd",
            "layer": "l1",
            "asset": "geometry/vdd.json",
            "asset_sha256": "A" * 64,
            "island_ids": ["island:vdd"],
        },
    ]
    original = [dict(item) for item in project_rows]

    bound = spd_adapter._bind_certified_surface_islands(
        project_rows, reversed(certified_rows)
    )
    assert [item["asset"] for item in bound] == [
        "geometry/vdd.json",
        "geometry/gnd.json",
    ]
    assert bound[0]["island_ids"] == ["island:vdd"]
    assert bound[1]["island_ids"] == ["island:gnd:2", "island:gnd:1"]
    assert bound[0]["primitive_count"] == 7
    assert bound[1]["custom"] == {"kept": True}
    assert project_rows == original

    with pytest.raises(
        SpdImportError,
        match="^SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_MISMATCH",
    ):
        spd_adapter._bind_certified_surface_islands(project_rows, certified_rows[:1])


def test_raw_spatial_member_merge_is_collision_safe_and_nonmutating() -> None:
    project = ProjectSpec(
        name="raw-spatial-merge",
        outline=MLOOutline(width_um=100.0, height_um=100.0),
        split_gap_um=0.0,
        metadata={"spd_import": {"source_sha256": "a" * 64}},
    )
    asset_name = "spatial/raw-spatial-contact-v2-aaaaaaaaaaaaaaaa.sqlite.zlib"
    manifest = {"asset_name": asset_name, "source_sha256": "a" * 64}

    updated, attachments = spd_adapter._merge_raw_spatial_contact_asset(
        project, {}, manifest, (asset_name, b"raw-spatial")
    )

    assert RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY not in project.metadata["spd_import"]
    assert updated.metadata["spd_import"][
        RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    ] == manifest
    assert attachments == {asset_name: b"raw-spatial"}
    with pytest.raises(SpdImportError, match="^RAW_SPATIAL_ASSET_COLLISION"):
        spd_adapter._merge_raw_spatial_contact_asset(
            project,
            {asset_name.swapcase(): b"raw-spatial"},
            manifest,
            (asset_name, b"raw-spatial"),
        )


def test_import_runs_one_union_reachability_pass_and_persists_surface_certificate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "surface-connectivity.spd"
    source.write_text(
        MINI_SPD.replace(
            "* Via description lines",
            "* Trace description lines\n* Via description lines",
        ).replace(
            "* PadStack collection description lines",
            "* PadStack collection description lines\n"
            ".PadStackDef DUT 0.00mm Material = COPPER\n"
            ".EndPadStackDef",
        ),
        encoding="ascii",
    )
    calls: list[dict[str, object]] = []
    phase_order: list[str] = []
    retarget_request_lists: list[list[object]] = []
    retarget_request_counts: list[int] = []
    original_retarget_compile = (
        spd_adapter._compile_retarget_landing_destination_requests
    )

    def wrapped_retarget_compile(**kwargs):
        phase_order.append("retarget")
        pwr_asset = next(
            item
            for item in kwargs["geometry_assets"]
            if str(item["net"]).casefold() == "vdd_core/0"
        )
        destination_island_id = str(pwr_asset["island_ids"][0])
        kwargs["strict_island_resolver_batch"] = (
            lambda net, _layer, node_ids, _points: (
                (destination_island_id,) * len(node_ids)
                if net.casefold() == "vdd_core/0"
                else (None,) * len(node_ids)
            )
        )
        result = original_retarget_compile(**kwargs)
        retarget_request_counts.append(len(result[0]))
        retarget_request_lists.append(result[0])
        return result

    def fake_compile(_project: ProjectSpec, _rail_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            device=SimpleNamespace(
                branches=(
                    SimpleNamespace(
                        branch_id="DEVICE-BRANCH",
                        source_power_pin_id="SITE0:101",
                        source_ground_pin_id="SITE0:102",
                    ),
                )
            )
        )

    class FakeReachability:
        statistics = {
            "requested": 2,
            "reachable": 2,
            "unreachable": 0,
            "node_section_passes": 1,
            "trace_section_passes": 1,
            "via_section_passes": 2,
            "components": 2,
            "terminal_owned_via_ids_supplied": 1,
            "terminal_owned_via_id_count": 2,
            "terminal_owned_via_observed_count": 2,
            "via_island_pair_terminal_owned_record_count": 2,
        }
        surface_components = ()
        surface_layers_by_landing = {
            ("via1", "node1"): ("Signal$PWR",),
            ("via2", "node2"): ("Signal$GND",),
        }
        def __init__(self, inventory: object) -> None:
            assert isinstance(inventory, dict)
            pwr = tuple(inventory[("vdd_core/0", "signal$pwr")])
            gnd = tuple(inventory[("dgnd", "signal$gnd")])
            self.reachable_keys = frozenset(
                {
                    ("via1", "node1", "signal$pwr"),
                    ("via2", "node2", "signal$gnd"),
                }
            )
            self.surface_islands_by_landing = {
                ("via1", "node1"): pwr,
                ("via2", "node2"): gnd,
            }
            self.surface_equivalence_proofs = (
                SimpleNamespace(
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    island_ids=pwr,
                    contacted_island_ids=pwr,
                    graph_component_count=1,
                    status="complete",
                ),
                SimpleNamespace(
                    net="DGND",
                    layer="Signal$GND",
                    island_ids=gnd,
                    contacted_island_ids=gnd,
                    graph_component_count=1,
                    status="complete",
                ),
            )
            self.surface_equivalence_components = (
                SimpleNamespace(
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    island_ids=pwr,
                ),
                SimpleNamespace(
                    net="DGND",
                    layer="Signal$GND",
                    island_ids=gnd,
                ),
            )
            self.landing_surface_contacts = (
                SimpleNamespace(
                    via_id="Via1",
                    endpoint_node_id="Node1",
                    net="VDD_CORE/0",
                    contact_island_ids_by_layer={"Signal$PWR": pwr},
                    internal_endpoint_node_id="Node3",
                    terminal_owner_kind="device",
                    external_endpoint_layer="Signal$TOP",
                    padstack="DR-0102_60",
                    drill_diameter_um=20.0,
                    material="COPPER",
                    segments=(
                        SimpleNamespace(
                            ordinal=0,
                            start_layer="Signal$TOP",
                            end_layer="Signal$PWR",
                            length_um=120.0,
                        ),
                    ),
                    physical_model_status="complete",
                    physical_model_issues=(),
                ),
                SimpleNamespace(
                    via_id="Via2",
                    endpoint_node_id="Node2",
                    net="DGND",
                    contact_island_ids_by_layer={"Signal$GND": gnd},
                    internal_endpoint_node_id="Node4",
                    terminal_owner_kind="device",
                    external_endpoint_layer="Signal$TOP",
                    padstack="DR-0102_60",
                    drill_diameter_um=20.0,
                    material="COPPER",
                    segments=(
                        SimpleNamespace(
                            ordinal=0,
                            start_layer="Signal$TOP",
                            end_layer="Signal$GND",
                            length_um=240.0,
                        ),
                    ),
                    physical_model_status="complete",
                    physical_model_issues=(),
                ),
            )
            self.via_island_pair_aggregates = (
                SimpleNamespace(
                    net="VDD_CORE/0",
                    padstack="DR-0102_60",
                    start_layer="Signal$PWR",
                    end_layer="Signal$PWR",
                    start_island_id=pwr[0],
                    end_island_id=pwr[0],
                    count=1,
                    via_ids_sha256="c" * 64,
                    terminal_owned_count=1,
                    substrate_count=0,
                ),
                SimpleNamespace(
                    net="DGND",
                    padstack="DR-0102_60",
                    start_layer="Signal$GND",
                    end_layer="Signal$GND",
                    start_island_id=gnd[0],
                    end_island_id=gnd[0],
                    count=1,
                    via_ids_sha256="d" * 64,
                    terminal_owned_count=1,
                    substrate_count=0,
                ),
            )
            self.via_island_pair_coverage = SpdViaIslandPairCoverage(
                raw_target_via_count=2,
                paired_via_count=2,
                terminal_owned_unpaired_count=0,
                terminal_owned_unpaired_via_ids_sha256=(
                    sha256(b"").hexdigest()
                ),
                unsupported_missing_endpoint_count=0,
                unsupported_missing_endpoint_via_ids_sha256=(
                    sha256(b"").hexdigest()
                ),
                outside_retained_interface_scope_count=0,
                outside_retained_interface_scope_via_ids_sha256=(
                    sha256(b"").hexdigest()
                ),
                model_relevant_via_count=2,
                terminal_owned_ids_supplied=True,
                terminal_owned_declared_count=2,
                terminal_owned_observed_count=2,
                paired_terminal_owned_count=2,
                paired_substrate_count=0,
            )
            top_pwr_vertex = "spd-finite-via-vertex:fake-top-pwr"
            other_pwr_vertex = "spd-finite-via-vertex:fake-other-pwr"
            pwr_vertex = "spd-finite-via-vertex:fake-pwr"
            top_gnd_vertex = "spd-finite-via-vertex:fake-top-gnd"
            gnd_vertex = "spd-finite-via-vertex:fake-gnd"
            self.finite_via_vertices = (
                SimpleNamespace(
                    vertex_id=top_pwr_vertex,
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    representative_node_id="Node1",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node1").hexdigest(),
                    roles=("retained_surface", "terminal"),
                    retained_component_island_ids_by_layer={
                        "Signal$PWR": pwr
                    },
                    terminal_ids=("SITE0:101",),
                ),
                SimpleNamespace(
                    vertex_id=other_pwr_vertex,
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    representative_node_id="Node5",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node5").hexdigest(),
                    roles=("retained_surface",),
                    retained_component_island_ids_by_layer={
                        "Signal$PWR": pwr
                    },
                    terminal_ids=(),
                ),
                SimpleNamespace(
                    vertex_id=pwr_vertex,
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    representative_node_id="Node3",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node3").hexdigest(),
                    roles=("retarget_cut", "terminal"),
                    retained_component_island_ids_by_layer={},
                    terminal_ids=("decap-via:via1:node3",),
                ),
                SimpleNamespace(
                    vertex_id=top_gnd_vertex,
                    net="DGND",
                    layer="Signal$GND",
                    representative_node_id="Node2",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node2").hexdigest(),
                    roles=("retained_surface", "terminal"),
                    retained_component_island_ids_by_layer={
                        "Signal$GND": gnd
                    },
                    terminal_ids=("SITE0:102",),
                ),
                SimpleNamespace(
                    vertex_id=gnd_vertex,
                    net="DGND",
                    layer="Signal$GND",
                    representative_node_id="Node4",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node4").hexdigest(),
                    roles=("retarget_cut", "terminal"),
                    retained_component_island_ids_by_layer={},
                    terminal_ids=("decap-via:via2:node4",),
                ),
            )

            def finite_edge(
                edge_id: str,
                net: str,
                start_vertex_id: str,
                end_vertex_id: str,
                via_id: str,
                start_layer: str,
                end_layer: str,
                length_um: float,
            ) -> SimpleNamespace:
                segment = SimpleNamespace(
                    ordinal=0,
                    start_layer=start_layer,
                    end_layer=end_layer,
                    length_um=length_um,
                )
                term = SimpleNamespace(
                    ordinal=0,
                    count=1,
                    padstack="DR-0102_60",
                    start_layer=start_layer,
                    end_layer=end_layer,
                    drill_diameter_um=20.0,
                    material="COPPER",
                    segments=(segment,),
                    resistance_ohm=1.0e-3,
                    inductance_h=1.0e-9,
                    length_um=length_um,
                    physical_model_status="complete",
                    physical_model_issues=(),
                )
                return SimpleNamespace(
                    edge_id=edge_id,
                    net=net,
                    start_vertex_id=start_vertex_id,
                    end_vertex_id=end_vertex_id,
                    parallel_path_count=1,
                    per_path_via_count=1,
                    raw_via_count=1,
                    raw_via_ids_sha256=sha256(
                        via_id.casefold().encode("utf-8")
                    ).hexdigest(),
                    owner_ids=(f"via:{via_id}",),
                    series_terms=(term,),
                    resistance_ohm=1.0e-3,
                    inductance_h=1.0e-9,
                    length_um=length_um,
                    mode="retained_explicit",
                    physical_model_status="complete",
                    physical_model_issues=(),
                )

            pwr_edge = "spd-finite-via-edge:fake-pwr"
            gnd_edge = "spd-finite-via-edge:fake-gnd"
            self.finite_via_edges = (
                finite_edge(
                    pwr_edge,
                    "VDD_CORE/0",
                    top_pwr_vertex,
                    pwr_vertex,
                    "Via1",
                    "Signal$TOP",
                    "Signal$PWR",
                    120.0,
                ),
                finite_edge(
                    gnd_edge,
                    "DGND",
                    top_gnd_vertex,
                    gnd_vertex,
                    "Via2",
                    "Signal$TOP",
                    "Signal$GND",
                    240.0,
                ),
            )
            self.finite_via_vertex_id_by_landing = {
                ("via1", "node1"): top_pwr_vertex,
                ("via1", "node3"): pwr_vertex,
                ("via2", "node2"): top_gnd_vertex,
                ("via2", "node4"): gnd_vertex,
            }
            self.finite_via_edge_id_by_landing = {
                ("via1", "node1"): pwr_edge,
                ("via1", "node3"): pwr_edge,
                ("via2", "node2"): gnd_edge,
                ("via2", "node4"): gnd_edge,
            }
            self.finite_via_coverage = SpdFiniteViaQuotientCoverage(
                raw_target_via_count=2,
                modeled_global_via_count=2,
                outside_scope_via_count=0,
                pruned_dangling_via_count=0,
                physical_complete_via_count=2,
                physical_incomplete_via_count=0,
                raw_target_via_ids_sha256=sha256(b"raw-vias").hexdigest(),
                modeled_global_via_ids_sha256=sha256(
                    b"modeled-vias"
                ).hexdigest(),
                modeled_owner_ledger_sha256=sha256(
                    b"owner-ledger"
                ).hexdigest(),
                modeled_owner_canonical_sha256=_canonical_owner_digest(
                    "via:Via1", "via:Via2"
                ),
                outside_scope_via_ids_sha256=sha256(b"").hexdigest(),
            )
            self.finite_via_scenario_isolated_landing_keys = frozenset(
                {("via1", "node3"), ("via2", "node4")}
            )
            self.finite_via_scenario_isolation_coverage = (
                SpdFiniteViaScenarioIsolationCoverage(
                    requested_landing_count=2,
                    isolated_landing_count=2,
                    isolated_node_count=2,
                    suppressed_artwork_contact_count=2,
                    suppressed_trace_edge_count=0,
                    requested_landing_ids_sha256=sha256(
                        b"isolated-landings"
                    ).hexdigest(),
                    isolated_landing_ids_sha256=sha256(
                        b"isolated-landings"
                    ).hexdigest(),
                )
            )

        @staticmethod
        def reaches(landing: object, target_layer: str) -> bool:
            return target_layer.casefold() in {
                item.casefold()
                for item in FakeReachability.surface_layers_by_landing.get(
                    (
                        str(getattr(landing, "via_id")).casefold(),
                        str(getattr(landing, "endpoint_node_id")).casefold(),
                    ),
                    (),
                )
            }

    def fake_recover(
        _path: Path,
        *,
        landings: object,
        target_layers_by_net: object,
        target_node_predicate: object,
        **_kwargs: object,
    ) -> FakeReachability:
        assert phase_order == []
        phase_order.append("recovery")
        retained = tuple(landings)
        requested_targets = {
            tuple(key): frozenset(layers)
            for key, layers in _kwargs[
                "requested_target_layers_by_landing"
            ].items()
        }
        calls.append(
            {
                "landings": retained,
                "terminal_landings": tuple(
                    _kwargs["terminal_contact_landings"]
                ),
                "terminal_owned_via_ids": frozenset(
                    _kwargs["terminal_owned_via_ids"]
                ),
                "targets": {
                    str(net): frozenset(layers)
                    for net, layers in target_layers_by_net.items()
                },
                "requested_targets": requested_targets,
                "predicate": target_node_predicate,
            }
        )
        assert target_node_predicate is None
        assert callable(_kwargs["target_node_surface_resolver"])
        assert _kwargs["target_surface_island_ids"]
        return FakeReachability(_kwargs["target_surface_island_ids"])

    monkeypatch.setattr(
        spd_adapter, "compile_project_evaluation_template", fake_compile
    )
    monkeypatch.setattr(
        spd_adapter, "recover_spd_ground_reachability", fake_recover
    )
    monkeypatch.setattr(
        spd_adapter,
        "_compile_retarget_landing_destination_requests",
        wrapped_retarget_compile,
    )

    inline_certificate: dict[str, object] = {}
    real_externalize = spd_adapter.externalize_project_surface_certificate

    def capture_inline_certificate(
        project: ProjectSpec,
        attachments: object,
        **kwargs: object,
    ) -> object:
        raw_certificate = project.metadata["spd_import"][
            SURFACE_CERTIFICATE_METADATA_KEY
        ]
        assert isinstance(raw_certificate, dict)
        inline_certificate.update(raw_certificate)
        return real_externalize(project, attachments, **kwargs)

    monkeypatch.setattr(
        spd_adapter,
        "externalize_project_surface_certificate",
        capture_inline_certificate,
    )

    imported = import_spd_scenario(source)

    assert len(calls) == 1
    assert phase_order == ["recovery", "retarget"]
    assert retarget_request_counts == [1]
    assert retarget_request_lists == [[]]
    landing_ids = {
        (item.via_id, item.endpoint_node_id)
        for item in calls[0]["landings"]
    }
    assert {("Via1", "Node1"), ("Via2", "Node2")} <= landing_ids
    terminal_landing_ids = {
        (item.via_id, item.endpoint_node_id)
        for item in calls[0]["terminal_landings"]
    }
    assert {("Via1", "Node1"), ("Via2", "Node2")} <= terminal_landing_ids
    assert calls[0]["terminal_owned_via_ids"] == {"Via1", "Via2"}
    targets = calls[0]["targets"]
    assert targets == {
        "vdd_core/0": frozenset({"Signal$PWR"}),
        "dgnd": frozenset({"Signal$GND"}),
    }
    assert calls[0]["requested_targets"] == {
        ("via1", "node1", "vdd_core/0"): frozenset({"Signal$PWR"}),
        ("via2", "node2", "dgnd"): frozenset({"Signal$GND"}),
    }
    certificate = inline_certificate
    assert certificate["schema_version"] == (
        "spd-layer-surface-connectivity-v4"
    )
    assert certificate["compiler_id"] == (
        "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
    )
    assert not certificate["scenario_decap_terminal_topology"]["issues"], (
        certificate["scenario_decap_terminal_topology"]["issues"]
    )
    assert certificate["scenario_decap_terminal_topology"]["status"] == (
        "complete"
    ), certificate["scenario_decap_terminal_topology"]
    retarget_bindings = certificate["scenario_decap_terminal_topology"][
        "retarget_landing_xy_bindings"
    ]
    assert len(retarget_bindings) == 1
    assert retarget_bindings[0]["status"] == "complete"
    assert retarget_bindings[0]["destination_vertex_id"] == (
        retarget_bindings[0]["destination_representative_island_id"]
    )
    assert certificate["finite_via_quotient"]["status"] == "complete", (
        certificate["finite_via_quotient"]
    )
    assert certificate["status"] == "complete", {
        "finite_status": certificate["finite_via_quotient"]["status"],
        "coverage_status": certificate["finite_via_quotient"]["coverage"][
            "status"
        ],
        "owner_status": certificate["finite_via_quotient"]["coverage"][
            "owner_ledger_status"
        ],
        "vertices": [
            (
                item["vertex_id"],
                item["component_binding_status"],
                item["component_binding_issues"],
            )
            for item in certificate["finite_via_quotient"]["vertices"]
        ],
        "edges": [
            (item["edge_id"], item["status"], item["physical_model_issues"])
            for item in certificate["finite_via_quotient"]["edges"]
        ],
        "bindings": [
            (
                item["landing_key"],
                item["global_quotient_binding_status"],
                item["global_quotient_binding_issues"],
                item["retarget_cut_status"],
            )
            for item in certificate["finite_via_quotient"]["terminal_bindings"]
        ],
        "scenario_status": certificate["scenario_decap_terminal_topology"][
            "status"
        ],
        "scenario_issues": certificate["scenario_decap_terminal_topology"][
            "issues"
        ],
        "terminals": [
            (item["pin_id"], item["status"], item["issues"])
            for item in certificate["terminal_contacts"]
        ],
    }
    assert certificate["compile_failures"] == []
    assert len(certificate["rail_anchor_bindings"]) == 2
    assert all(
        item["status"] == "complete"
        for item in certificate["terminal_contacts"]
    )
    assert [
        item["net"] for item in certificate["via_island_pair_aggregates"]
    ] == ["DGND", "VDD_CORE/0"]
    assert all(
        item["terminal_owned_count"] == 1
        and item["substrate_count"] == 0
        for item in certificate["via_island_pair_aggregates"]
    )
    assert all(
        item["status"] == "complete"
        and item["landing_key"]
        == [item["via_id"].casefold(), item["external_endpoint_node_id"].casefold()]
        and item["physical_model_status"] == "complete"
        and item["physical_model_issues"] == []
        and len(item["contact_island_ids_by_layer"]) == 1
        and item["endpoint_layer"] is not None
        and item["endpoint_island_id"] is not None
        for item in certificate["terminal_landing_contacts"]
    )
    payload = dict(certificate)
    evidence_sha256 = payload.pop("evidence_sha256")
    assert evidence_sha256 == core_services._canonical_metadata_sha256(payload)
    persisted_project = imported.scenario.base_project
    persisted_spd_import = persisted_project.metadata["spd_import"]
    persisted_certificate = persisted_spd_import[
        SURFACE_CERTIFICATE_METADATA_KEY
    ]
    assert persisted_certificate["storage_schema"] == (
        SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA
    )
    compiled_manifest = persisted_spd_import[
        COMPILED_TOPOLOGY_ASSET_METADATA_KEY
    ]
    assert compiled_manifest["asset_name"] in imported.attachments
    assert all(
        "layerwise-surface-connectivity-v4-" not in name
        for name in imported.attachments
    )
    persisted_geometries = persisted_spd_import["plane_geometries"]
    certified_islands = {
        (
            str(item["net"]).casefold(),
            str(item["layer"]).casefold(),
            str(item["asset"]),
            str(item["asset_sha256"]).casefold(),
        ): tuple(item["island_ids"])
        for item in certificate["geometry_assets"]
    }
    assert all(
        tuple(item["island_ids"])
        == certified_islands[
            (
                str(item["net"]).casefold(),
                str(item["layer"]).casefold(),
                str(item["asset"]),
                str(item["asset_sha256"]).casefold(),
            )
        ]
        for item in persisted_geometries
    )
    artwork_node_ids = tuple(
        str(island_id)
        for row in persisted_geometries
        for island_id in row["island_ids"]
    )
    assert load_compiled_topology_asset(
        persisted_project,
        imported.attachments,
        artwork_node_ids,
    ) is not None
    raw_manifest = persisted_spd_import[
        RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    ]
    assert raw_manifest["asset_name"] in imported.attachments
    assert raw_manifest["source_sha256"] == imported.scenario.source.sha256
    assert raw_manifest["project_binding_sha256"] == compiled_manifest[
        "project_binding_sha256"
    ]
    assert raw_manifest["certificate_evidence_sha256"] == compiled_manifest[
        "certificate_evidence_sha256"
    ]
    assert raw_manifest["compiled_topology_identity_sha256"] == (
        compiled_manifest["topology_identity_sha256"]
    )
    with load_raw_spatial_contact_asset(
        raw_manifest,
        imported.attachments,
        expected_source_sha256=raw_manifest["source_sha256"],
        expected_project_binding_sha256=raw_manifest[
            "project_binding_sha256"
        ],
        expected_certificate_evidence_sha256=raw_manifest[
            "certificate_evidence_sha256"
        ],
        expected_compiled_topology_identity_sha256=raw_manifest[
            "compiled_topology_identity_sha256"
        ],
        expected_geometry_identity_sha256=raw_manifest[
            "geometry_identity_sha256"
        ],
    ) as raw_asset:
        assert raw_asset.get_via("VDD_CORE/0", "Via1") is not None
        assert raw_asset.get_via("DGND", "Via2") is not None
    # A pure-reference rail has no provisional witness either; the separate
    # mixed test above proves the ephemeral copy path explicitly.
    assert all(
        rail.mixed_reference_ground_witness is None
        for rail in imported.scenario.base_project.rails
    )
