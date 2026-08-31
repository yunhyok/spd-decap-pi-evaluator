from __future__ import annotations

from pathlib import Path
from hashlib import sha256
from dataclasses import asdict
from copy import deepcopy
import json

import pytest

from test_io_spd import MINI_SPD
from spd_decap_pi import spd_adapter
from spd_decap_pi import raw_spatial_contact_compiler
from spd_decap_pi.raw_spatial_contact_compiler import (
    RawSpatialCompilerError,
    compile_raw_spatial_contact_asset,
)
from spd_decap_pi.source_plane_ownership_ir import (
    SourcePlaneOwnershipIRError,
    load_source_plane_ownership_ir,
    validate_project_source_plane_ownership_ir_envelope,
)
from spd_decap_pi._core.io.spd import SpdImportError
from spd_decap_pi.canonical_json import concrete_canonical_json_bytes
from spd_decap_pi.raw_spatial_contact_asset import RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
from spd_decap_pi.raw_spatial_contact_asset import load_raw_spatial_contact_asset
from spd_decap_pi.compiled_topology_asset import COMPILED_TOPOLOGY_ASSET_METADATA_KEY
from spd_decap_pi.surface_certificate_asset import canonical_surface_certificate_sha256


def _ownership_fixture_payload() -> str:
    return (
        MINI_SPD.replace("LEGACY_SOURCE_GRAPH_UNAVAILABLE", "TRACE_VIA_COMPONENTS_AVAILABLE")
        .replace("VDD_CORE/0", "VDD_CORE/1")
        .replace(
            "Node1!!101::VDD_CORE/1 X = 0mm Y = 0mm",
            "Node1!!101::VDD_CORE/1 X = 0.5mm Y = 0mm",
        )
        .replace(
            "Node1!!101::VDD_CORE/1 X = 0.5mm Y = 0mm Layer = Signal$TOP PadStack = DUT",
            "Node1!!101::VDD_CORE/1 X = 0.5mm Y = 0mm Layer = Signal$TOP PadStack = DR-0102_60",
        )
        .replace(
            "Node2!!102::DGND X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = DUT",
            "Node2!!102::DGND X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = DR-0102_60",
        )
        .replace(
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
            "Node7!!7::VDD_CORE/1 X = 0.5mm Y = 0mm Layer = Signal$PWR PadStack = DR-0102_60\n"
            "Node8!!8::VDD_CORE/1 X = 1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60\n"
            "Node9!!9::DGND X = 0.1mm Y = 0mm Layer = Signal$GND PadStack = DR-0102_60\n"
            "Node10!!10::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60\n"
            "Node11!!11::VDD_CORE/1 X = 1.5mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60\n"
            "Node12!!12::VDD_CORE/1 X = 1.5mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60",
        )
        .replace(
            "Via1::VDD_CORE/1 UpperNode = Node1 LowerNode = Node3 PadStack = DR-0102_60",
            "Via1::VDD_CORE/1 UpperNode = Node1 LowerNode = Node7 PadStack = DR-0102_60",
        )
        .replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node9 PadStack = DR-0102_60\n"
            "Via7::VDD_CORE/1 UpperNode = Node3 LowerNode = Node8 PadStack = DR-0102_60\n"
            "Via9::DGND UpperNode = Node4 LowerNode = Node10 PadStack = DR-0102_60\n"
            "Via11::VDD_CORE/1 UpperNode = Node12 LowerNode = Node11 PadStack = DR-0102_60 AbsoluteRotation = 4.5",
        )
        .replace(
            "* Via description lines",
            "* Trace description lines\n"
            "Trace11::VDD_CORE/1 StartingNode = Node1 EndingNode = Node12 Width = 0.10mm\n"
            "* Via description lines",
        )
        .replace("Medium$D1 Thickness = 0.10mm Material = ABF", "Medium$D1 Thickness = 0.10mm Material = abf")
        .replace(
            ".PadDef Signal$PWR\nRegular Circle 0.03mm\n.EndPadDef",
            ".PadDef Signal$PWR\nRegular Circle 0.03mm\n.EndPadDef\n"
            ".PadDef Signal$GND\nRegular Circle 0.03mm\n.EndPadDef",
        )
    )


def test_ownership_request_and_callback_are_atomic_pair() -> None:
    missing = Path("does-not-exist.spd")
    callback = lambda *_args: None
    with pytest.raises(RawSpatialCompilerError, match="request and callback"):
        compile_raw_spatial_contact_asset(
            missing,
            analysis=object(),
            project=object(),
            attachments={},
            source_plane_ownership_request={},
        )
    with pytest.raises(RawSpatialCompilerError, match="request and callback"):
        compile_raw_spatial_contact_asset(
            missing,
            analysis=object(),
            project=object(),
            attachments={},
            source_plane_ownership_callback=callback,
        )


def test_ownership_selection_bound_is_checked_before_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_spatial_contact_compiler, "MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS", 1)
    with pytest.raises(RawSpatialCompilerError, match="ownership selection exceeds"):
        raw_spatial_contact_compiler._ownership_selection(
            {
                "surface_keys": [("PWR", "L1"), ("GND", "L2")],
                "node_keys": [], "via_keys": [], "pad_keys": [],
                "layer_keys": [], "material_keys": [],
            }
        )


def test_ownership_record_append_bound_stops_before_second_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_spatial_contact_compiler, "MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS", 1)
    records: list[dict[str, object]] = []
    raw_spatial_contact_compiler._ownership_record_append(records, {"record_id": "r0"})
    with pytest.raises(RawSpatialCompilerError, match="ownership source-record bound exceeded"):
        raw_spatial_contact_compiler._ownership_record_append(records, {"record_id": "r1"})
    assert len(records) == 1


def test_source_plane_ownership_component_layer_is_authoritative_for_mismatched_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "ownership-component-layer.spd"
    source.write_text(_ownership_fixture_payload(), encoding="ascii")
    original_certificate = spd_adapter._layer_surface_connectivity_certificate
    original_compile = spd_adapter.compile_raw_spatial_contact_asset
    original_build = spd_adapter.build_source_plane_ownership_ir
    captured: dict[str, object] = {}
    compile_calls = {"count": 0}

    def mutate_certificate(*args: object, **kwargs: object):
        certificate = original_certificate(*args, **kwargs)
        target_pins = {
            str(item.get("pin_id", "")).strip().casefold()
            for item in certificate.get("rail_anchor_bindings", ())
            if isinstance(item, dict)
            and str(item.get("rail_id", "")).strip().casefold() == "vdd_core/1"
        }
        changed = False
        for contact in certificate.get("terminal_contacts", ()):
            if (
                isinstance(contact, dict)
                and str(contact.get("pin_id", "")).strip().casefold() in target_pins
                and str(contact.get("contact_component_layer", "")).strip().casefold()
                != "signal$top"
            ):
                contact["endpoint_layer"] = "Signal$TOP"
                changed = True
        assert changed
        certificate["evidence_sha256"] = canonical_surface_certificate_sha256(
            {key: value for key, value in certificate.items() if key != "evidence_sha256"}
        )
        captured["certificate"] = certificate
        return certificate

    def capture_request(*args: object, **kwargs: object):
        compile_calls["count"] += 1
        request = kwargs.get("source_plane_ownership_request")
        if isinstance(request, dict):
            request = deepcopy(request)
            snapshot = request.get("certificate_snapshot")
            assert isinstance(snapshot, dict)
            target_pins = {
                str(item.get("pin_id", "")).strip().casefold()
                for item in snapshot.get("rail_anchor_bindings", ())
                if isinstance(item, dict)
                and str(item.get("rail_id", "")).strip().casefold() == "vdd_core/1"
            }
            changed = False
            for contact in snapshot.get("terminal_contacts", ()):
                if not isinstance(contact, dict) or str(contact.get("pin_id", "")).strip().casefold() not in target_pins:
                    continue
                component_id = str(contact.get("contact_component_id", "")).strip()
                base_component = next(
                    item for item in snapshot["surface_equivalence_components"]
                    if str(item.get("component_id", "")).strip().casefold() == component_id.casefold()
                )
                cross_layer = deepcopy(base_component)
                cross_layer["layer"] = "Signal$TOP"
                cross_layer["component_id"] = f"{component_id}-cross-layer-{str(contact['pin_id']).strip()}"
                cross_layer["component_evidence_sha256"] = sha256(cross_layer["component_id"].encode()).hexdigest()
                snapshot["surface_equivalence_components"].append(cross_layer)
                contact["contact_component_ids"] = [component_id, cross_layer["component_id"]]
                contact["reachable_required_component_ids"] = list(contact["contact_component_ids"])
                contact["contact_component_evidence_sha256s"] = [base_component["component_evidence_sha256"], cross_layer["component_evidence_sha256"]]
                contact["contact_component_id"] = None
                contact["contact_component_evidence_sha256"] = None
                contact["representative_island_id"] = None
                contact["contact_component_layer"] = None
                contact["contact_component_island_ids"] = sorted({str(island_id) for island_id in base_component["island_ids"]})
                changed = True
            assert changed
            captured["request"] = deepcopy(request)
            kwargs = {**kwargs, "source_plane_ownership_request": request}
        return original_compile(*args, **kwargs)

    def capture_draft(*args: object, **kwargs: object):
        draft = args[0] if args else kwargs.get("draft")
        captured["draft"] = deepcopy(draft)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(
        spd_adapter, "_layer_surface_connectivity_certificate", mutate_certificate
    )
    monkeypatch.setattr(
        spd_adapter, "compile_raw_spatial_contact_asset", capture_request
    )
    monkeypatch.setattr(spd_adapter, "build_source_plane_ownership_ir", capture_draft)

    imported = spd_adapter.import_spd_scenario(
        source, source_plane_ownership_rail_id="VDD_CORE/1"
    )
    request = captured["request"]
    assert isinstance(request, dict)
    snapshot = request["certificate_snapshot"]
    assert isinstance(snapshot, dict)
    snapshot_components = {
        str(item["component_id"]).casefold(): item
        for item in snapshot["surface_equivalence_components"]
    }
    contacts = {
        str(item["pin_id"]).casefold(): item
        for item in snapshot["terminal_contacts"]
    }
    mismatched = [
        item
        for item in contacts.values()
        if str(item.get("endpoint_layer", "")).casefold()
        != str(item.get("contact_component_layer", "")).casefold()
    ]
    assert mismatched
    assert all(
        str(item["endpoint_layer"]).casefold() == "signal$top"
        for item in mismatched
    )
    raw_selection = request["raw_selection"]
    assert isinstance(raw_selection, dict)
    raw_pad_keys = {
        tuple(str(value).casefold() for value in key)
        for key in raw_selection["pad_keys"]
    }
    assert all(
        (
            str(item["incident_padstack"]).casefold(),
            str(item["endpoint_layer"]).casefold(),
        )
        in raw_pad_keys
        for item in mismatched
    )
    draft = captured["draft"]
    assert isinstance(draft, dict)
    terminal_rows = list(draft["terminal_bindings"])
    assert terminal_rows
    source_records = {
        str(row["record_id"]).casefold(): row
        for row in draft["source_records"]
    }
    assert all(
        str(source_records[str(row[field]).casefold()]["layer"]).casefold()
        == "signal$top"
        for row in terminal_rows
        for field in ("paddef_source_record_id", "regular_source_record_id")
    )
    assert all(
        str(row["layer"]).casefold()
        == str(snapshot_components[str(row["component_id"]).casefold()]["layer"]).casefold()
        for row in terminal_rows
    )
    multi_contacts = [
        item for item in contacts.values()
        if len(item["contact_component_ids"]) > 1
    ]
    assert multi_contacts
    assert all(
        item["contact_component_id"] is None
        and item["contact_component_evidence_sha256"] is None
        and item["representative_island_id"] is None
        and item["contact_component_layer"] is None
        for item in multi_contacts
    )
    assert all(
        str(component_id).casefold() in snapshot_components
        for item in multi_contacts
        for component_id in item["contact_component_ids"]
    )
    assert all(
        str(row["island_id"]).casefold()
        == str(snapshot_components[str(row["component_id"]).casefold()]["representative_island_id"]).casefold()
        for row in terminal_rows
    )
    assert all(
        str(row["layer"]).casefold() in {"signal$pwr", "signal$gnd"}
        for row in terminal_rows
    )
    assert imported.scenario.base_project.metadata["spd_import"]

    def candidate_certificate(mode: str, *args: object, **kwargs: object):
        certificate = original_certificate(*args, **kwargs)
        target_pins = {
            str(item.get("pin_id", "")).strip().casefold()
            for item in certificate.get("rail_anchor_bindings", ())
            if isinstance(item, dict)
            and str(item.get("rail_id", "")).strip().casefold() == "vdd_core/1"
        }
        for contact in certificate.get("terminal_contacts", ()):
            if not isinstance(contact, dict) or str(contact.get("pin_id", "")).strip().casefold() not in target_pins:
                continue
            component_id = str(contact.get("contact_component_id", "")).strip()
            if mode == "zero":
                contact["contact_component_ids"] = []
                contact["reachable_required_component_ids"] = []
                contact["contact_component_id"] = None
                contact["contact_component_evidence_sha256s"] = []
                contact["contact_component_evidence_sha256"] = None
            else:
                base_component = next(
                    item
                    for item in certificate["surface_equivalence_components"]
                    if str(item.get("component_id", "")).strip().casefold()
                    == component_id.casefold()
                )
                duplicate = deepcopy(base_component)
                duplicate["component_id"] = (
                    f"{component_id}-ambiguous-{str(contact['pin_id']).strip()}"
                )
                duplicate["component_evidence_sha256"] = sha256(
                    duplicate["component_id"].encode()
                ).hexdigest()
                certificate["surface_equivalence_components"].append(duplicate)
                contact["contact_component_ids"] = [
                    component_id,
                    duplicate["component_id"],
                ]
                contact["reachable_required_component_ids"] = list(
                    contact["contact_component_ids"]
                )
                contact["contact_component_evidence_sha256s"] = [
                    base_component["component_evidence_sha256"],
                    duplicate["component_evidence_sha256"],
                ]
                contact["contact_component_id"] = None
                contact["contact_component_evidence_sha256"] = None
                contact["representative_island_id"] = None
                contact["contact_component_layer"] = None
                contact["contact_component_island_ids"] = sorted(
                    {
                        str(island_id)
                        for island_id in base_component["island_ids"]
                    }
                )
        certificate["evidence_sha256"] = canonical_surface_certificate_sha256(
            {key: value for key, value in certificate.items() if key != "evidence_sha256"}
        )
        return certificate

    for mode, expected in (("zero", "candidates are zero"), ("multiple", "candidates are multiple")):
        compile_calls["count"] = 0

        def invalid_certificate(*args: object, _mode: str = mode, **kwargs: object):
            # Keep the original producer invocation and mutate only its
            # retained certificate witness; no raw compiler should be reached.
            return candidate_certificate(_mode, *args, **kwargs)

        monkeypatch.setattr(
            spd_adapter, "_layer_surface_connectivity_certificate", invalid_certificate
        )
        with pytest.raises(SpdImportError, match=expected):
            spd_adapter.import_spd_scenario(
                source, source_plane_ownership_rail_id="VDD_CORE/1"
            )
        assert compile_calls["count"] == 0

    def mismatch_certificate(*args: object, **kwargs: object):
        certificate = original_certificate(*args, **kwargs)
        target_pins = {
            str(item.get("pin_id", "")).strip().casefold()
            for item in certificate.get("rail_anchor_bindings", ())
            if isinstance(item, dict)
            and str(item.get("rail_id", "")).strip().casefold() == "vdd_core/1"
        }
        for contact in certificate.get("terminal_contacts", ()):
            if (
                isinstance(contact, dict)
                and str(contact.get("pin_id", "")).strip().casefold() in target_pins
            ):
                component_id = str(contact.get("contact_component_id", "")).strip().casefold()
                component = next(
                    item
                    for item in certificate["surface_equivalence_components"]
                    if str(item.get("component_id", "")).strip().casefold()
                    == component_id
                )
                component["representative_island_id"] = "tampered-island"
                certificate["evidence_sha256"] = canonical_surface_certificate_sha256(
                    {key: value for key, value in certificate.items() if key != "evidence_sha256"}
                )
                return certificate
        raise AssertionError("target rail contact is absent")

    compile_calls["count"] = 0
    monkeypatch.setattr(
        spd_adapter, "_layer_surface_connectivity_certificate", mismatch_certificate
    )
    with pytest.raises(SpdImportError, match="component row evidence is incomplete"):
        spd_adapter.import_spd_scenario(
            source, source_plane_ownership_rail_id="VDD_CORE/1"
        )
    assert compile_calls["count"] == 0


@pytest.mark.parametrize("case_id", ["unrelated_global", "projected_over_cap"])
def test_source_plane_ownership_filters_global_quotient_before_selected_row_bound(
    case_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / f"ownership-quotient-{case_id}.spd"
    source.write_text(_ownership_fixture_payload(), encoding="ascii")
    original_certificate = spd_adapter._layer_surface_connectivity_certificate
    original_compile = spd_adapter.compile_raw_spatial_contact_asset
    original_build = spd_adapter.build_source_plane_ownership_ir
    captured: dict[str, object] = {}
    compile_calls = {"count": 0}
    build_calls = {"count": 0}

    def mutate_certificate(*args: object, **kwargs: object):
        certificate = deepcopy(original_certificate(*args, **kwargs))
        quotient = certificate["finite_via_quotient"]
        vertices = list(quotient["vertices"])
        anchors = [
            item for item in certificate.get("rail_anchor_bindings", ())
            if isinstance(item, dict) and str(item.get("rail_id", "")).casefold() == "vdd_core/1"
        ]
        contacts = {
            str(item.get("pin_id", "")).strip().casefold(): item
            for item in certificate.get("terminal_contacts", ())
            if isinstance(item, dict)
        }
        role_components = {
            str(component_id).strip().casefold()
            for anchor in anchors
            for component_id in contacts.get(str(anchor.get("pin_id", "")).strip().casefold(), {}).get("contact_component_ids", ())
            if isinstance(component_id, str) and component_id.strip()
        }
        selected = [
            item for item in vertices
            if isinstance(item, dict)
            and any(str(value).casefold() in role_components for value in item.get("retained_component_ids", ()))
        ]
        assert selected
        if case_id == "unrelated_global":
            monkeypatch.setattr(spd_adapter, "MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS", 1024)
            template = deepcopy(vertices[0])
            template["vertex_id"] = "spd-finite-via-vertex:unrelated-global-template"
            template["roles"] = []
            template["terminal_ids"] = []
            template["retained_component_ids"] = []
            template["retained_component_evidence_sha256s"] = []
            template["retained_component_island_ids_by_layer"] = {}
            template["component_binding_status"] = "complete"
            template["component_binding_issues"] = []
            for index in range(1025):
                extra = deepcopy(template)
                extra["vertex_id"] = f"spd-finite-via-vertex:unrelated-global-{index}"
                vertices.append(extra)
        else:
            selected_ids = {
                str(item["vertex_id"]).casefold()
                for item in selected
            }
            required_ids = set(selected_ids)
            boundary_edges = []
            for edge in quotient.get("edges", ()):
                if not isinstance(edge, dict):
                    continue
                endpoints = {
                    str(edge.get("start_vertex_id", "")).casefold(),
                    str(edge.get("end_vertex_id", "")).casefold(),
                }
                if len(endpoints & selected_ids) == 1:
                    required_ids.update(endpoints)
                    boundary_edges.append(edge)
            anchor_ids = {
                str(contact.get("exposed_quotient_vertex_id", "")).casefold()
                for anchor in anchors
                for contact in [contacts[str(anchor.get("pin_id", "")).strip().casefold()]]
                if contact.get("exposed_quotient_vertex_id")
            }
            required_ids.update(anchor_ids)
            cap = len(required_ids) - 1
            coverage_count = sum(
                len(edge.get("owner_ids", ()))
                for edge in boundary_edges
            )
            assert len(selected_ids) <= cap
            assert len(boundary_edges) <= cap
            assert coverage_count <= cap
            assert len(required_ids) > cap
            monkeypatch.setattr(spd_adapter, "MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS", cap)
        quotient["vertices"] = vertices
        certificate["evidence_sha256"] = canonical_surface_certificate_sha256(
            {key: value for key, value in certificate.items() if key != "evidence_sha256"}
        )
        captured["certificate"] = deepcopy(certificate)
        return certificate

    def capture_request(*args: object, **kwargs: object):
        compile_calls["count"] += 1
        request = kwargs.get("source_plane_ownership_request")
        if isinstance(request, dict):
            captured["request"] = deepcopy(request)
        return original_compile(*args, **kwargs)

    def capture_build(*args: object, **kwargs: object):
        build_calls["count"] += 1
        return original_build(*args, **kwargs)

    monkeypatch.setattr(spd_adapter, "_layer_surface_connectivity_certificate", mutate_certificate)
    monkeypatch.setattr(spd_adapter, "compile_raw_spatial_contact_asset", capture_request)
    monkeypatch.setattr(spd_adapter, "build_source_plane_ownership_ir", capture_build)

    if case_id == "projected_over_cap":
        with pytest.raises(
            SpdImportError,
            match="SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED: projected quotient vertex materialization exceeds bound",
        ):
            spd_adapter.import_spd_scenario(source, source_plane_ownership_rail_id="VDD_CORE/1")
        assert compile_calls["count"] == 0
        assert build_calls["count"] == 0
        return

    imported = spd_adapter.import_spd_scenario(source, source_plane_ownership_rail_id="VDD_CORE/1")
    assert compile_calls["count"] == 1
    assert build_calls["count"] == 1
    request = captured["request"]
    certificate = captured["certificate"]
    assert isinstance(request, dict) and isinstance(certificate, dict)
    snapshot = request["certificate_snapshot"]
    assert isinstance(snapshot, dict)
    snapshot_vertices = snapshot["finite_via_quotient"]["vertices"]
    snapshot_ids = {str(item["vertex_id"]).casefold() for item in snapshot_vertices}
    quotient = certificate["finite_via_quotient"]
    vertices = quotient["vertices"]
    anchors = [
        item for item in certificate["rail_anchor_bindings"]
        if str(item.get("rail_id", "")).casefold() == "vdd_core/1"
    ]
    contacts = {
        str(item["pin_id"]).casefold(): item
        for item in certificate["terminal_contacts"]
    }
    role_components = {
        str(component_id).casefold()
        for anchor in anchors
        for component_id in contacts[str(anchor["pin_id"]).casefold()].get("contact_component_ids", ())
    }
    selected_ids = {
        str(item["vertex_id"]).casefold()
        for item in vertices
        if any(str(value).casefold() in role_components for value in item.get("retained_component_ids", ()))
    }
    adjacent_ids = set(selected_ids)
    for edge in quotient["edges"]:
        endpoints = {
            str(edge.get("start_vertex_id", "")).casefold(),
            str(edge.get("end_vertex_id", "")).casefold(),
        }
        if endpoints & selected_ids:
            adjacent_ids.update(endpoints)
    anchor_ids = {
        str(contact.get("exposed_quotient_vertex_id", "")).casefold()
        for anchor in anchors
        for contact in [contacts[str(anchor["pin_id"]).casefold()]]
        if contact.get("exposed_quotient_vertex_id")
    }
    assert snapshot_ids == selected_ids | adjacent_ids | anchor_ids
    assert not any(":unrelated-global-" in item for item in snapshot_ids)
    assert imported.scenario.base_project.metadata["spd_import"]


def test_source_plane_ownership_producer_roundtrip_and_atomic_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _ownership_fixture_payload()
    source = tmp_path / "ownership-producer.spd"
    source.write_text(payload, encoding="ascii")
    original = source.read_bytes()

    ownership_capture: dict[str, object] = {}
    original_compile = spd_adapter.compile_raw_spatial_contact_asset
    original_build = spd_adapter.build_source_plane_ownership_ir

    def capture_ownership_request(*args: object, **kwargs: object):
        request = kwargs.get("source_plane_ownership_request")
        if isinstance(request, dict):
            ownership_capture["certificate_snapshot"] = request.get("certificate_snapshot")
        return original_compile(*args, **kwargs)

    monkeypatch.setattr(spd_adapter, "compile_raw_spatial_contact_asset", capture_ownership_request)

    def capture_ownership_draft(*args: object, **kwargs: object):
        draft = args[0] if args else kwargs.get("draft")
        ownership_capture["draft"] = deepcopy(draft)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(spd_adapter, "build_source_plane_ownership_ir", capture_ownership_draft)
    imported = spd_adapter.import_spd_scenario(
        source, source_plane_ownership_rail_id="VDD_CORE/1"
    )
    project = imported.scenario.base_project
    manifest = project.metadata["spd_import"]["source_plane_ownership_ir"]
    assert validate_project_source_plane_ownership_ir_envelope(project, imported.attachments) == manifest
    raw_manifest = project.metadata["spd_import"][RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY]
    compiled_manifest = project.metadata["spd_import"][COMPILED_TOPOLOGY_ASSET_METADATA_KEY]
    raw_manifest_hash = sha256(concrete_canonical_json_bytes(dict(raw_manifest))).hexdigest()
    with load_raw_spatial_contact_asset(
        raw_manifest,
        imported.attachments,
        expected_source_sha256=raw_manifest["source_sha256"],
        expected_project_binding_sha256=compiled_manifest["project_binding_sha256"],
        expected_certificate_evidence_sha256=compiled_manifest["certificate_evidence_sha256"],
        expected_compiled_topology_identity_sha256=compiled_manifest["topology_identity_sha256"],
        expected_geometry_identity_sha256=raw_manifest["geometry_identity_sha256"],
        require_plane_sheet_payload=True,
    ) as raw_asset:
        raw_stackup = list(raw_asset.iter_stackup_layers())
        raw_points = list(raw_asset.iter_dielectric_points())
        raw_stack_hash = {
            row.layer_ordinal: sha256(concrete_canonical_json_bytes(asdict(row))).hexdigest()
            for row in raw_stackup
        }
        raw_point_hash = {
            index: sha256(concrete_canonical_json_bytes(asdict(row))).hexdigest()
            for index, row in enumerate(raw_points)
        }
    with load_source_plane_ownership_ir(
        manifest,
        imported.attachments,
        expected_source_sha256=raw_manifest["source_sha256"],
        expected_project_binding_sha256=compiled_manifest["project_binding_sha256"],
        expected_certificate_evidence_sha256=compiled_manifest["certificate_evidence_sha256"],
        expected_compiled_topology_identity_sha256=compiled_manifest["topology_identity_sha256"],
        expected_raw_manifest_sha256=raw_manifest_hash,
        expected_raw_geometry_identity_sha256=raw_manifest["geometry_identity_sha256"],
        expected_raw_logical_rows_sha256=raw_manifest["logical_rows_sha256"],
        expected_raw_plane_sheet_sha256=raw_manifest["plane_sheet_payload_sha256"],
        expected_app_version=manifest["app_version"],
    ) as loaded:
        expected_sections = {
            "source_records", "surfaces", "primitives", "islands",
            "primitive_island_edges", "stackup_layers", "dielectric_points",
            "rail_bindings", "terminal_bindings", "retained_owner_refs",
            "plane_owner_scopes", "replacement_ledger", "replacement_ledger_members",
            "contact_boundary",
        }
        assert set(manifest["counts"]) == expected_sections
        assert all(manifest["counts"][name] > 0 for name in expected_sections)
        assert len(list(loaded.iter_section("rail_bindings"))) == 2
        assert len(list(loaded.iter_section("plane_owner_scopes"))) == 2
        assert len(list(loaded.iter_section("terminal_bindings"))) >= 2
        assert all(row["status"] == "complete" for row in loaded.iter_section("terminal_bindings"))
        contacts = list(loaded.iter_section("contact_boundary"))
        assert contacts
        assert any(str(row["owner_kind"]).casefold() == "decap" for row in contacts)
        generic = [row for row in contacts if str(row["via_id"]).casefold() == "via11"]
        assert len(generic) == 1
        generic = generic[0]
        assert generic["owner_kind"] == "other"
        assert [str(owner).casefold() for owner in json.loads(generic["owner_ids_json"])] == ["via:via11"]
        assert generic["plane_endpoint_node_id"] == "Node11"
        assert generic["external_endpoint_node_id"] == "Node12"
        assert generic["opposite_endpoint_node_id"] == "Node12"
        assert generic["source_node_record_id"].startswith("node:Node11:")
        assert generic["plane_endpoint_node_record_id"] == generic["source_node_record_id"]
        assert generic["external_endpoint_node_record_id"].startswith("node:Node12:")
        assert generic["opposite_endpoint_node_record_id"] == generic["external_endpoint_node_record_id"]
        assert float(generic["rotation_degrees"]) == 4.5
        assert generic["padstack_id"] == "DR-0102_60"
        assert all(row["status"] == "complete" and row["issues_json"] == "[]" for row in contacts)
        assert all(row["owner_ids_json"].startswith("[") for row in contacts)
        certificate_snapshot = ownership_capture.get("certificate_snapshot")
        assert isinstance(certificate_snapshot, dict)
        boundary_rows = certificate_snapshot.get("contact_boundary")
        assert isinstance(boundary_rows, list) and boundary_rows
        generic_boundary = [
            item for item in boundary_rows
            if str(item.get("via_id", "")).casefold() == "via11"
        ]
        assert len(generic_boundary) == 1
        generic_boundary = generic_boundary[0]
        assert [str(owner).casefold() for owner in generic_boundary["owner_ids"]] == ["via:via11"]
        quotient = certificate_snapshot["finite_via_quotient"]
        quotient_edges = [
            item for item in quotient["edges"]
            if str(item.get("edge_id", "")).casefold()
            == str(generic_boundary["finite_edge_id"]).casefold()
        ]
        assert len(quotient_edges) == 1
        generic_edge = quotient_edges[0]
        assert [str(owner).casefold() for owner in generic_edge["owner_ids"]] == ["via:via11"]
        assert len(generic_edge["series_terms"]) == 1
        assert generic_edge["parallel_path_count"] == 1
        assert str(generic_edge["mode"]).casefold() == "retained_explicit"
        expected_authority = [
            [
                str(item["component_id"]).casefold(),
                str(item["island_id"]).casefold(),
                str(item["finite_vertex_id"]).casefold(),
                str(item["finite_edge_id"]).casefold(),
                str(owner).casefold(),
            ]
            for item in boundary_rows
            for owner in item["owner_ids"]
        ]
        actual_authority = [
            [
                str(row["component_id"]).casefold(),
                str(row["island_id"]).casefold(),
                str(row["finite_vertex_id"]).casefold(),
                str(row["finite_edge_id"]).casefold(),
                str(owner).casefold(),
            ]
            for row in contacts
            for owner in json.loads(row["owner_ids_json"])
        ]
        assert expected_authority == actual_authority
        assert set(map(tuple, expected_authority)) == set(map(tuple, actual_authority))
        coverage = certificate_snapshot.get("contact_boundary_coverage")
        assert isinstance(coverage, dict)
        assert len(expected_authority) == len(actual_authority) == int(coverage["count"])
        assert sha256(concrete_canonical_json_bytes(expected_authority)).hexdigest() == coverage["sha256"]
        captured_draft = ownership_capture.get("draft")
        assert isinstance(captured_draft, dict)
        source_records = captured_draft["source_records"]
        assert isinstance(source_records, list)
        draft_contact = next(row for row in captured_draft["contact_boundary"] if str(row["via_id"]).casefold() == "via11")
        other_via = next(row["record_id"] for row in source_records if str(row.get("kind", "")).casefold() == "via" and str(row["record_id"]).casefold() != str(draft_contact["via_record_id"]).casefold())
        other_node = next(row["record_id"] for row in source_records if str(row.get("kind", "")).casefold() == "node" and str(row["record_id"]).casefold() != str(draft_contact["source_node_record_id"]).casefold())
        other_paddef = next(row["record_id"] for row in source_records if str(row.get("kind", "")).casefold() == "paddef" and str(row.get("layer", "")).casefold() != str(draft_contact["endpoint_layer"]).casefold())
        other_regular = next(row["record_id"] for row in source_records if str(row.get("kind", "")).casefold() == "regular" and str(row.get("layer", "")).casefold() != str(draft_contact["endpoint_layer"]).casefold())

        def assert_contact_rejected(mutate):
            candidate = deepcopy(captured_draft)
            contact = next(row for row in candidate["contact_boundary"] if str(row["via_id"]).casefold() == "via11")
            mutate(contact)
            with pytest.raises(SourcePlaneOwnershipIRError) as failure:
                original_build(candidate)
            assert failure.value.code == "SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID"

        for mutate in (
            lambda contact: contact.__setitem__("net", "ALTERED_NET"),
            lambda contact: contact.__setitem__("plane_endpoint_node_id", contact["external_endpoint_node_id"]),
            lambda contact: contact.__setitem__("opposite_endpoint_node_id", contact["endpoint_node_id"]),
            lambda contact: contact.__setitem__("via_record_id", other_via),
            lambda contact: (contact.__setitem__("source_node_record_id", other_node), contact.__setitem__("plane_endpoint_node_record_id", other_node)),
            lambda contact: contact.__setitem__("padstack_id", "ALTERED_PADSTACK"),
            lambda contact: (contact.__setitem__("paddef_source_record_id", other_paddef), contact.__setitem__("regular_source_record_id", other_regular)),
            lambda contact: contact.__setitem__("rotation_degrees", 180.0),
        ):
            assert_contact_rejected(mutate)
        original_bytes = source.read_bytes()
        kinds = {
            row["record_id"].casefold(): row["kind"]
            for row in loaded.iter_section("source_records")
        }
        assert {"Layer", "Material", "Node", "Via", "PadDef", "Regular", "Shape"} <= set(kinds.values())
        for row in loaded.iter_section("source_records"):
            assert sha256(original_bytes[row["source_offset"]:row["source_end"]]).hexdigest() == row["source_record_sha256"]
        for row in loaded.iter_section("stackup_layers"):
            for field in ("thickness", "conductivity", "material"):
                origin = row.get(f"{field}_origin")
                ref = row.get(f"{field}_source_record_id")
                if ref is not None:
                    assert kinds[ref.casefold()] == ("Material" if origin == "material_model" else "Layer")
        assert any(row["record_id"] == "material:ABF" for row in loaded.iter_section("source_records"))
        assert any(row["material_source_record_id"] == "material:ABF" for row in loaded.iter_section("stackup_layers"))
        ir_stackup = list(loaded.iter_section("stackup_layers"))
        ir_points = list(loaded.iter_section("dielectric_points"))
        assert all(row["raw_layer_sha256"] == raw_stack_hash[row["raw_layer_ordinal"]] for row in ir_stackup)
        assert all(row["raw_dielectric_sha256"] == raw_point_hash[row["raw_dielectric_ordinal"]] for row in ir_points)
        assert [row["raw_dielectric_ordinal"] for row in ir_points] == sorted(row["raw_dielectric_ordinal"] for row in ir_points)

    merge_calls = {"raw": 0, "ir": 0}
    original_raw_merge = spd_adapter._merge_raw_spatial_contact_asset
    original_ir_merge = spd_adapter._merge_source_plane_ownership_ir_asset
    monkeypatch.setattr(spd_adapter, "_merge_raw_spatial_contact_asset", lambda *args, **kwargs: (merge_calls.__setitem__("raw", merge_calls["raw"] + 1) or original_raw_merge(*args, **kwargs)))
    monkeypatch.setattr(spd_adapter, "_merge_source_plane_ownership_ir_asset", lambda *args, **kwargs: (merge_calls.__setitem__("ir", merge_calls["ir"] + 1) or original_ir_merge(*args, **kwargs)))
    monkeypatch.setattr(
        spd_adapter,
        "build_source_plane_ownership_ir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SpdImportError("forced IR failure")),
    )
    with pytest.raises(SpdImportError, match="forced IR failure"):
        spd_adapter.import_spd_scenario(
            source, source_plane_ownership_rail_id="VDD_CORE/1"
        )
    assert source.read_bytes() == original
    assert merge_calls == {"raw": 0, "ir": 0}
