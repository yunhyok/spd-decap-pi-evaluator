from pathlib import Path

import math
from types import SimpleNamespace

import pytest

from test_io_spd import MINI_SPD

from spd_decap_pi import spd_adapter
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.io.spd import SpdImportError
from spd_decap_pi._core.services import WorkspaceState, import_cap_spice
from spd_decap_pi.scenario import (
    SHARED_PAD_ANALYSIS_VERSION,
    mixed_reference_ground_landing_identity,
)
from spd_decap_pi.scenario_io import ScenarioFormatError, save_scenario
from spd_decap_pi.routing_obstacles import (
    RoutingCandidateState,
    SignalTraceAvoidancePolicy,
    decode_routing_obstacle_asset,
    evaluate_routing_candidate,
)
from spd_decap_pi.spd_adapter import (
    _raise_for_rejected_mixed_reference_landings,
    _via_target_layers_by_net,
    import_spd_scenario,
)


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
