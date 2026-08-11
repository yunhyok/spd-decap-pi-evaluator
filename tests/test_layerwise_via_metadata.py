from __future__ import annotations

import json
from types import SimpleNamespace

from spd_decap_pi._core.domain import (
    MLOOutline,
    PinKind,
    PinRecord,
    ProjectSpec,
    StackupLayer,
    TerminalKind,
)
from spd_decap_pi._core.io.shared_pad import (
    SpdDecapConnection,
    SpdViaLanding,
)
from spd_decap_pi._core.io.spd import (
    SpdDeviceTerminalViaEndpoint,
    SpdPadStack,
    SpdViaUsage,
)
from spd_decap_pi._core.services import (
    _canonical_metadata_sha256,
    _spd_layerwise_device_terminal_via_certificate,
    _spd_layerwise_via_group_certificate,
)


SOURCE_SHA256 = "a" * 64


def _device_pin(
    *,
    pin: str,
    net: str,
    terminal: TerminalKind,
    node: str,
    x_um: float,
) -> PinRecord:
    return PinRecord(
        refdes="SITE0",
        pin=pin,
        net=net,
        x_um=x_um,
        y_um=20.0,
        kind=PinKind.DEVICE_BUMP,
        terminal=terminal,
        site="SITE0",
        source_node_id=node,
        source_layer="TOP",
        source_padstack="DUT",
    )


def _device_endpoint(
    pin: PinRecord,
    *,
    via_id: str,
    padstack: str,
) -> SpdDeviceTerminalViaEndpoint:
    return SpdDeviceTerminalViaEndpoint(
        pin_id=pin.pin_id,
        refdes=pin.refdes,
        pin=pin.pin,
        terminal=str(pin.terminal),
        net=pin.net,
        source_node_id=pin.source_node_id,
        source_layer=pin.source_layer,
        source_padstack=pin.source_padstack,
        source_x_um=pin.x_um,
        source_y_um=pin.y_um,
        status="complete",
        issues=(),
        candidate_count=1,
        candidate_via_ids=(via_id,),
        incident_via_id=via_id,
        incident_net=pin.net,
        incident_padstack=padstack,
        incident_opposite_node_id=f"DEEP-{via_id}",
    )


def _stackup() -> tuple[StackupLayer, ...]:
    return (
        StackupLayer(
            name="TOP", thickness_um=20.0, conductivity_s_m=5.959e7
        ),
        StackupLayer(name="D01", thickness_um=30.0, dk=3.4),
        StackupLayer(
            name="L01", thickness_um=20.0, conductivity_s_m=5.959e7
        ),
        StackupLayer(name="D02", thickness_um=40.0, dk=3.4),
        StackupLayer(
            name="L02", thickness_um=20.0, conductivity_s_m=5.959e7
        ),
    )


def _terminal_certificate(
    *,
    pins: tuple[PinRecord, ...] = (),
    endpoints: tuple[SpdDeviceTerminalViaEndpoint, ...] = (),
    power_nets: tuple[str, ...] = ("VDD",),
    ground_nets: tuple[str, ...] = ("DGND",),
) -> dict[str, object]:
    certificate, _diagnostics = _spd_layerwise_device_terminal_via_certificate(
        SimpleNamespace(device_terminal_via_endpoints=endpoints),
        device_pins=pins,
        selected_power_nets=power_nets,
        ground_nets=ground_nets,
        source_sha256=SOURCE_SHA256,
    )
    return certificate


def test_device_terminal_certificate_is_source_bound_deterministic_and_keeps_vqps() -> None:
    power = _device_pin(
        pin="10015",
        net="VQPS_075",
        terminal=TerminalKind.PWR,
        node="Node80628",
        x_um=12.5,
    )
    ground = _device_pin(
        pin="10016",
        net="DGND",
        terminal=TerminalKind.GND,
        node="Node80629",
        x_um=13.5,
    )
    endpoints = (
        _device_endpoint(
            power, via_id="Via1493181", padstack="DR-0102_60"
        ),
        _device_endpoint(
            ground, via_id="Via1493182", padstack="DR-0102_60"
        ),
    )
    analysis = SimpleNamespace(device_terminal_via_endpoints=endpoints)

    first, first_diagnostics = (
        _spd_layerwise_device_terminal_via_certificate(
            analysis,
            device_pins=(power, ground),
            selected_power_nets=("VQPS_075",),
            ground_nets=("DGND",),
            source_sha256=SOURCE_SHA256,
        )
    )
    second, second_diagnostics = (
        _spd_layerwise_device_terminal_via_certificate(
            SimpleNamespace(
                device_terminal_via_endpoints=tuple(reversed(endpoints))
            ),
            device_pins=(ground, power),
            selected_power_nets=("VQPS_075",),
            ground_nets=("DGND",),
            source_sha256=SOURCE_SHA256,
        )
    )

    assert first == second
    assert first_diagnostics == second_diagnostics == ()
    assert first["schema_version"] == "spd-layerwise-device-terminal-vias-v1"
    assert first["compiler_id"] == "powersi-direct-device-top-via-v1"
    assert first["source_sha256"] == SOURCE_SHA256
    assert first["raw_spd_embedded"] is False
    assert first["status"] == "complete"
    assert first["terminal_count"] == first["complete_terminal_count"] == 2
    assert first["scope"]["selected_power_nets"] == ["VQPS_075"]
    power_row = next(
        item for item in first["terminals"] if item["net"] == "VQPS_075"
    )
    assert power_row["pin_id"] == "SITE0:10015"
    assert power_row["source_node_id"] == "Node80628"
    assert power_row["source_layer"] == "TOP"
    assert power_row["source_padstack"] == "DUT"
    assert power_row["source_x_um"] == 12.5
    assert power_row["source_y_um"] == 20.0
    assert power_row["incident_via_id"] == "Via1493181"
    assert power_row["incident_padstack"] == "DR-0102_60"
    assert power_row["candidate_count"] == 1
    assert power_row["status"] == "complete"
    assert len(power_row["candidate_via_ids_sha256"]) == 64
    payload = dict(first)
    evidence_sha256 = payload.pop("evidence_sha256")
    assert evidence_sha256 == _canonical_metadata_sha256(payload)


def test_device_terminal_certificate_fails_closed_for_missing_or_ambiguous_endpoint() -> None:
    power = _device_pin(
        pin="1",
        net="VQPS_EMPTY",
        terminal=TerminalKind.PWR,
        node="Node1",
        x_um=1.0,
    )
    ambiguous = SpdDeviceTerminalViaEndpoint(
        pin_id=power.pin_id,
        refdes=power.refdes,
        pin=power.pin,
        terminal=str(power.terminal),
        net=power.net,
        source_node_id=power.source_node_id,
        source_layer=power.source_layer,
        source_padstack=power.source_padstack,
        source_x_um=power.x_um,
        source_y_um=power.y_um,
        status="ambiguous_incident_via",
        issues=("ambiguous_incident_via",),
        candidate_count=2,
        candidate_via_ids=("Via1", "Via2"),
    )

    ambiguous_certificate, ambiguous_diagnostics = (
        _spd_layerwise_device_terminal_via_certificate(
            SimpleNamespace(device_terminal_via_endpoints=(ambiguous,)),
            device_pins=(power,),
            selected_power_nets=(power.net,),
            ground_nets=("DGND",),
            source_sha256=SOURCE_SHA256,
        )
    )
    missing_certificate, missing_diagnostics = (
        _spd_layerwise_device_terminal_via_certificate(
            SimpleNamespace(device_terminal_via_endpoints=()),
            device_pins=(power,),
            selected_power_nets=(power.net,),
            ground_nets=("DGND",),
            source_sha256=SOURCE_SHA256,
        )
    )

    assert ambiguous_certificate["status"] == "incomplete"
    assert ambiguous_certificate["terminals"][0]["status"] == (
        "ambiguous_incident_via"
    )
    assert ambiguous_certificate["terminals"][0]["incident_via_id"] is None
    assert missing_certificate["status"] == "incomplete"
    assert missing_certificate["terminals"][0]["status"] == (
        "endpoint_evidence_missing"
    )
    assert all(
        any(item.code == "SPD_LAYERWISE_DEVICE_TERMINAL_VIA_INCOMPLETE" for item in diagnostics)
        for diagnostics in (ambiguous_diagnostics, missing_diagnostics)
    )


def test_pin_record_without_source_attachment_fields_remains_json_compatible() -> None:
    legacy = PinRecord.model_validate(
        {
            "refdes": "SITE0",
            "pin": "1",
            "net": "VDD",
            "x_um": 1.0,
            "y_um": 2.0,
            "kind": "DEVICE_BUMP",
            "terminal": "PWR",
        }
    )

    assert legacy.source_node_id is None
    assert legacy.source_layer is None
    assert legacy.source_padstack is None
    assert PinRecord.model_validate_json(legacy.model_dump_json()) == legacy


def _landing(via_id: str, net: str, padstack: str) -> SpdViaLanding:
    return SpdViaLanding(
        via_id=via_id,
        net=net,
        endpoint_node_id=f"NODE-{via_id}",
        x_um=100.0,
        y_um=200.0,
        padstack=padstack,
    )


def _analysis(*, reverse: bool = False) -> SimpleNamespace:
    padstacks = [
        SpdPadStack(
            "P_DEEP",
            60.0,
            100.0,
            110.0,
            ("L02", "TOP", "L01"),
            material="COPPER",
        ),
        SpdPadStack(
            "G_NEAR",
            80.0,
            120.0,
            120.0,
            ("L01", "TOP"),
            material="COPPER",
        ),
        SpdPadStack(
            "SIGNAL_ONLY",
            90.0,
            130.0,
            130.0,
            ("TOP", "L01"),
            material="COPPER",
        ),
    ]
    usages = [
        SpdViaUsage("VDD", "P_DEEP", 3),
        SpdViaUsage("DGND", "G_NEAR", 5),
        SpdViaUsage("SIGNAL", "SIGNAL_ONLY", 99),
    ]
    connections = [
        SpdDecapConnection(
            refdes="C1",
            kind="DIRECT",
            power_vias=(_landing("VIA-P-1", "VDD", "P_DEEP"),),
            ground_vias=(_landing("VIA-G-1", "DGND", "G_NEAR"),),
        )
    ]
    if reverse:
        padstacks.reverse()
        usages.reverse()
        connections.reverse()
    return SimpleNamespace(
        padstacks=tuple(padstacks),
        via_usage=tuple(usages),
        decap_connections=tuple(connections),
        pins=(),
    )


def test_certificate_is_deterministic_and_retains_ordered_segment_ownership() -> None:
    first, first_diagnostics = _spd_layerwise_via_group_certificate(
        _analysis(),
        _stackup(),
        selected_power_nets=["VDD"],
        ground_nets=["DGND"],
        source_sha256=SOURCE_SHA256,
        device_terminal_via_certificate=_terminal_certificate(),
    )
    second, second_diagnostics = _spd_layerwise_via_group_certificate(
        _analysis(reverse=True),
        _stackup(),
        selected_power_nets=["VDD"],
        ground_nets=["DGND"],
        source_sha256=SOURCE_SHA256,
        device_terminal_via_certificate=_terminal_certificate(),
    )

    assert first == second
    assert first_diagnostics == second_diagnostics == ()
    assert first["schema_version"] == "spd-layerwise-via-groups-v2"
    assert first["compiler_id"] == (
        "powersi-via-usage-padstack-terminal-ownership-v2"
    )
    assert first["source_sha256"] == SOURCE_SHA256
    assert first["raw_spd_embedded"] is False
    assert first["status"] == "complete"
    assert first["group_count"] == 2
    assert all(item["net"] != "SIGNAL" for item in first["groups"])

    power = next(item for item in first["groups"] if item["net"] == "VDD")
    assert power["role"] == "power"
    assert power["count"] == 3
    assert power["declared_layers"] == ["L02", "TOP", "L01"]
    assert power["conductor_layers"] == ["TOP", "L01", "L02"]
    assert power["start_layer"] == "TOP"
    assert power["end_layer"] == "L02"
    assert power["physical_layer_span"] == ["TOP", "L01", "L02"]
    assert power["drill_diameter_um"] == 60.0
    assert power["pad_width_um"] == 100.0
    assert power["pad_height_um"] == 110.0
    assert power["material"] == "COPPER"
    assert [item["length_um"] for item in power["segments"]] == [50.0, 60.0]
    assert [item["count"] for item in power["segments"]] == [3, 3]
    assert all(item["owner_id"] == power["owner_id"] for item in power["segments"])
    assert power["terminal_owned_count"] == 1
    assert power["ownership_status"] == "complete"
    assert power["substrate_count"] == 2

    payload = dict(first)
    evidence_sha256 = payload.pop("evidence_sha256")
    assert evidence_sha256 == _canonical_metadata_sha256(payload)
    assert len(evidence_sha256) == 64
    assert power["group_id"].startswith("spd-via-group:")
    assert power["owner_id"].startswith("source-via-population:")


def test_complete_device_incident_via_is_exactly_subtracted_from_its_group() -> None:
    pin = _device_pin(
        pin="1",
        net="VDD",
        terminal=TerminalKind.PWR,
        node="NODE-DEVICE-PWR",
        x_um=10.0,
    )
    endpoint = _device_endpoint(pin, via_id="VIA-P-2", padstack="P_DEEP")
    terminal_certificate = _terminal_certificate(
        pins=(pin,), endpoints=(endpoint,)
    )

    certificate, diagnostics = _spd_layerwise_via_group_certificate(
        _analysis(),
        _stackup(),
        selected_power_nets=["VDD"],
        ground_nets=["DGND"],
        source_sha256=SOURCE_SHA256,
        device_terminal_via_certificate=terminal_certificate,
    )

    assert diagnostics == ()
    assert certificate["status"] == "complete"
    assert certificate["complete_device_terminal_owned_via_count"] == 1
    assert certificate["device_terminal_via_evidence_sha256"] == (
        terminal_certificate["evidence_sha256"]
    )
    power = next(item for item in certificate["groups"] if item["net"] == "VDD")
    assert power["terminal_owned_count"] == 2
    assert power["terminal_owned_via_ids_sha256"] == _canonical_metadata_sha256(
        ["via-p-1", "via-p-2"]
    )
    assert power["terminal_ownership_method"] == (
        "exact-decap-landing-and-device-incident-via-id-v2"
    )
    assert power["ownership_status"] == "complete"
    assert power["substrate_count"] == 1


def test_incomplete_device_endpoint_blocks_only_its_observed_group() -> None:
    pin = _device_pin(
        pin="1",
        net="VDD",
        terminal=TerminalKind.PWR,
        node="NODE-DEVICE-PWR",
        x_um=10.0,
    )
    endpoint = SpdDeviceTerminalViaEndpoint(
        pin_id=pin.pin_id,
        refdes=pin.refdes,
        pin=pin.pin,
        terminal=str(pin.terminal),
        net=pin.net,
        source_node_id=pin.source_node_id,
        source_layer=pin.source_layer,
        source_padstack=pin.source_padstack,
        source_x_um=pin.x_um,
        source_y_um=pin.y_um,
        status="source_pin_not_top",
        issues=("source_pin_not_top",),
        candidate_count=1,
        candidate_via_ids=("VIA-P-2",),
        incident_via_id="VIA-P-2",
        incident_net="VDD",
        incident_padstack="P_DEEP",
        incident_opposite_node_id="DEEP-VIA-P-2",
    )
    terminal_certificate = _terminal_certificate(
        pins=(pin,), endpoints=(endpoint,)
    )

    certificate, diagnostics = _spd_layerwise_via_group_certificate(
        _analysis(),
        _stackup(),
        selected_power_nets=["VDD"],
        ground_nets=["DGND"],
        source_sha256=SOURCE_SHA256,
        device_terminal_via_certificate=terminal_certificate,
    )

    assert certificate["ownership_resolved_group_count"] == 1
    assert certificate[
        "affecting_incomplete_device_terminal_endpoint_count"
    ] == 1
    assert certificate[
        "nonaffecting_incomplete_device_terminal_endpoint_count"
    ] == 0
    power = next(item for item in certificate["groups"] if item["net"] == "VDD")
    ground = next(item for item in certificate["groups"] if item["net"] == "DGND")
    assert power["ownership_status"] == "unresolved"
    assert power["ownership_issues"] == [
        "incomplete_device_terminal_endpoint_affects_group"
    ]
    assert power["substrate_count"] is None
    assert ground["ownership_status"] == "complete"
    assert ground["substrate_count"] == 4
    assert any(
        item.code == "SPD_LAYERWISE_VIA_OWNERSHIP_UNRESOLVED"
        for item in diagnostics
    )


def test_incomplete_device_endpoint_without_incident_group_is_nonaffecting() -> None:
    pin = _device_pin(
        pin="1",
        net="VDD",
        terminal=TerminalKind.PWR,
        node="NODE-DEVICE-PWR",
        x_um=10.0,
    )
    endpoint = SpdDeviceTerminalViaEndpoint(
        pin_id=pin.pin_id,
        refdes=pin.refdes,
        pin=pin.pin,
        terminal=str(pin.terminal),
        net=pin.net,
        source_node_id=pin.source_node_id,
        source_layer=pin.source_layer,
        source_padstack=pin.source_padstack,
        source_x_um=pin.x_um,
        source_y_um=pin.y_um,
        status="missing_incident_via",
        issues=("missing_incident_via",),
        candidate_count=0,
        candidate_via_ids=(),
    )
    terminal_certificate = _terminal_certificate(
        pins=(pin,), endpoints=(endpoint,)
    )

    certificate, diagnostics = _spd_layerwise_via_group_certificate(
        _analysis(),
        _stackup(),
        selected_power_nets=["VDD"],
        ground_nets=["DGND"],
        source_sha256=SOURCE_SHA256,
        device_terminal_via_certificate=terminal_certificate,
    )

    assert certificate["ownership_resolved_group_count"] == 2
    assert certificate[
        "affecting_incomplete_device_terminal_endpoint_count"
    ] == 0
    assert certificate[
        "nonaffecting_incomplete_device_terminal_endpoint_count"
    ] == 1
    assert all(
        item["ownership_status"] == "complete"
        for item in certificate["groups"]
    )
    assert any(
        item.code == "SPD_LAYERWISE_DEVICE_TERMINAL_ENDPOINT_NONAFFECTING"
        for item in diagnostics
    )


def test_incomplete_source_and_device_ownership_are_explicit_not_fabricated() -> None:
    analysis = SimpleNamespace(
        padstacks=(
            SpdPadStack(
                "P_BAD",
                None,
                None,
                None,
                ("TOP", "UNKNOWN"),
                material=None,
            ),
        ),
        via_usage=(
            SpdViaUsage("VDD", "P_BAD", 3),
            SpdViaUsage("DGND", "NO_SUCH_PADSTACK", 2),
        ),
        decap_connections=(
            SpdDecapConnection(
                refdes="C1",
                kind="DIRECT",
                power_vias=(_landing("VIA-P-1", "VDD", "P_BAD"),),
            ),
        ),
        pins=(SimpleNamespace(kind=PinKind.DEVICE_BUMP, net="VDD"),),
    )

    certificate, diagnostics = _spd_layerwise_via_group_certificate(
        analysis,
        _stackup(),
        selected_power_nets=["VDD", "VDD_WITHOUT_USAGE"],
        ground_nets=["DGND"],
        source_sha256=SOURCE_SHA256,
        device_terminal_via_certificate=_terminal_certificate(
            power_nets=("VDD", "VDD_WITHOUT_USAGE")
        ),
    )

    assert certificate["status"] == "incomplete"
    assert certificate["complete_group_count"] == 0
    assert certificate["ownership_resolved_group_count"] == 2
    assert certificate["missing_power_nets"] == ["VDD_WITHOUT_USAGE"]

    power = next(item for item in certificate["groups"] if item["net"] == "VDD")
    assert power["status"] == "incomplete"
    assert power["drill_diameter_um"] is None
    assert power["pad_width_um"] is None
    assert power["pad_height_um"] is None
    assert power["material"] is None
    assert power["start_layer"] == "TOP"
    assert power["end_layer"] == "TOP"
    assert power["segments"] == []
    assert "padstack_conductor_layer_missing_or_ambiguous" in power["issues"]
    assert "fewer_than_two_physical_conductor_layers" in power["issues"]
    assert "drill_diameter_um_missing" in power["issues"]
    assert power["terminal_owned_count"] == 1
    assert power["ownership_status"] == "complete"
    assert power["ownership_issues"] == []
    assert power["substrate_count"] == 2

    ground = next(item for item in certificate["groups"] if item["net"] == "DGND")
    assert ground["status"] == "incomplete"
    assert ground["start_layer"] is None
    assert ground["end_layer"] is None
    assert ground["segments"] == []
    assert "padstack_definition_missing" in ground["issues"]
    assert ground["substrate_count"] == 2

    codes = {item.code for item in diagnostics}
    assert "SPD_LAYERWISE_VIA_GROUP_INCOMPLETE" in codes
    assert "SPD_LAYERWISE_VIA_OWNERSHIP_UNRESOLVED" not in codes
    assert "SPD_LAYERWISE_POWER_VIA_GROUP_MISSING" in codes


def test_certificate_survives_project_json_without_a_raw_spd_path() -> None:
    certificate, diagnostics = _spd_layerwise_via_group_certificate(
        _analysis(),
        _stackup(),
        selected_power_nets=["VDD"],
        ground_nets=["DGND"],
        source_sha256=SOURCE_SHA256,
        device_terminal_via_certificate=_terminal_certificate(),
    )
    assert not diagnostics
    project = ProjectSpec(
        name="persisted-via-certificate",
        outline=MLOOutline(width_um=1_000.0, height_um=1_000.0),
        gnd_aliases=["DGND"],
        split_gap_um=0.0,
        stackup_layers=list(_stackup()),
        metadata={
            "spd_import": {
                "source_sha256": SOURCE_SHA256,
                "raw_spd_embedded": False,
                "layerwise_via_group_certificate": certificate,
            }
        },
    )

    restored = ProjectSpec.model_validate_json(project.model_dump_json())
    restored_certificate = restored.metadata["spd_import"][
        "layerwise_via_group_certificate"
    ]
    assert restored_certificate == certificate
    encoded = json.dumps(restored_certificate, sort_keys=True)
    assert "source_path" not in encoded
    assert "raw_source" not in encoded
    assert restored_certificate["raw_spd_embedded"] is False
