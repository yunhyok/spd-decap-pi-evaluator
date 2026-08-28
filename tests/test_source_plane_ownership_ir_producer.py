from __future__ import annotations

from pathlib import Path
from hashlib import sha256
from dataclasses import asdict

import pytest

from test_io_spd import MINI_SPD
from spd_decap_pi import spd_adapter
from spd_decap_pi import raw_spatial_contact_compiler
from spd_decap_pi.raw_spatial_contact_compiler import (
    RawSpatialCompilerError,
    compile_raw_spatial_contact_asset,
)
from spd_decap_pi.source_plane_ownership_ir import (
    load_source_plane_ownership_ir,
    validate_project_source_plane_ownership_ir_envelope,
)
from spd_decap_pi._core.io.spd import SpdImportError
from spd_decap_pi.canonical_json import concrete_canonical_json_bytes
from spd_decap_pi.raw_spatial_contact_asset import RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
from spd_decap_pi.raw_spatial_contact_asset import load_raw_spatial_contact_asset
from spd_decap_pi.compiled_topology_asset import COMPILED_TOPOLOGY_ASSET_METADATA_KEY


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


def test_source_plane_ownership_producer_roundtrip_and_atomic_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = (
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
            "Node10!!10::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
        )
        .replace(
            "Via1::VDD_CORE/1 UpperNode = Node1 LowerNode = Node3 PadStack = DR-0102_60",
            "Via1::VDD_CORE/1 UpperNode = Node1 LowerNode = Node7 PadStack = DR-0102_60",
        )
        .replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node9 PadStack = DR-0102_60\n"
            "Via7::VDD_CORE/1 UpperNode = Node3 LowerNode = Node8 PadStack = DR-0102_60\n"
            "Via9::DGND UpperNode = Node4 LowerNode = Node10 PadStack = DR-0102_60",
        )
        .replace("* Via description lines", "* Trace description lines\n* Via description lines")
        .replace("Medium$D1 Thickness = 0.10mm Material = ABF", "Medium$D1 Thickness = 0.10mm Material = abf")
        .replace(
            ".PadDef Signal$PWR\nRegular Circle 0.03mm\n.EndPadDef",
            ".PadDef Signal$PWR\nRegular Circle 0.03mm\n.EndPadDef\n"
            ".PadDef Signal$GND\nRegular Circle 0.03mm\n.EndPadDef",
        )
    )
    source = tmp_path / "ownership-producer.spd"
    source.write_text(payload, encoding="ascii")
    original = source.read_bytes()

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
        }
        assert set(manifest["counts"]) == expected_sections
        assert all(manifest["counts"][name] > 0 for name in expected_sections)
        assert len(list(loaded.iter_section("rail_bindings"))) == 2
        assert len(list(loaded.iter_section("plane_owner_scopes"))) == 2
        assert len(list(loaded.iter_section("terminal_bindings"))) >= 2
        assert all(row["status"] == "complete" for row in loaded.iter_section("terminal_bindings"))
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
