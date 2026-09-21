from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import box
from shapely.wkb import dumps

import tools.research.audit_source_l29_l30_port_window as audit
from spd_decap_pi.source_plane_ownership_ir import build_source_plane_ownership_ir, load_source_plane_ownership_ir, validate_project_source_plane_ownership_ir_envelope
from tests.test_source_plane_ownership_ir import binds, draft


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_records(tmp_path: Path) -> tuple[Path, dict[str, dict[str, object]]]:
    rows = []
    records: dict[str, dict[str, object]] = {}
    terminals = (("Node1", "20122", audit.EXPECTED_RAIL), ("Node2", "19958", "DGND"), ("Node3", "20576", audit.EXPECTED_RAIL), ("Node4", "20612", "DGND"), ("Node5", "20589", audit.EXPECTED_RAIL), ("Node6", "19973", "DGND"))
    for node, pin, net in terminals:
        rows.append((f"node:{node}:{net}", "Node", f"{node}!!{pin}::{net} X = 0.000000e+00mm Y = 0.000000e+00mm Layer = Signal$TOP\n"))
    for i, net in enumerate((audit.EXPECTED_RAIL, "DGND", audit.EXPECTED_RAIL, "DGND", audit.EXPECTED_RAIL, "DGND"), 7):
        rows.append((f"node:Node{i}:{net}", "Node", f"Node{i}::{net} X = 0.000000e+00mm Y = 0.000000e+00mm Layer = Signal$L02(DGND)\n"))
    rows.append(("paddef:DR-0102_60:Signal$TOP:0", "PadDef", ".PadDef Signal$TOP\n"))
    rows.append(("regular:DR-0102_60:Signal$TOP:0", "Regular", "Regular Circle 3.000000e-02mm\n"))
    for via_id, upper, lower, net in (("Via0", "Node1", "Node7", audit.EXPECTED_RAIL), ("Via1", "Node2", "Node8", "DGND"), ("Via2", "Node3", "Node9", audit.EXPECTED_RAIL), ("Via3", "Node4", "Node10", "DGND"), ("Via4", "Node5", "Node11", audit.EXPECTED_RAIL), ("Via5", "Node6", "Node12", "DGND")):
        rows.append((f"via:{via_id}:{net}", "Via", f"{via_id}::{net} UpperNode = {upper} LowerNode = {lower} PadStack = {audit.EXPECTED_PADSTACK}\n"))
    payload = bytearray()
    for record_id, kind, text in rows:
        start = len(payload)
        raw = text.encode("utf-8")
        payload.extend(raw)
        records[record_id] = {"record_id": record_id, "kind": kind, "source_offset": start, "source_end": start + len(raw), "source_record_sha256": hashlib.sha256(raw).hexdigest()}
        if kind in {"Node", "Via"}:
            net = record_id.rsplit(":", 1)[-1]
            records[record_id]["logical_net"] = net
            if kind == "Node":
                records[record_id]["layer"] = "Signal$L02(DGND)" if int(record_id.split(":", 2)[1].removeprefix("Node")) >= 7 else "Signal$TOP"
    source = tmp_path / "source.spd"
    source.write_bytes(payload)
    terminals_out = []
    for i, (node, pin, net) in enumerate(terminals):
        role = "power" if net == audit.EXPECTED_RAIL else "ground"
        terminals_out.append({"pin_id": f"SITE0:{pin}", "role": role, "source_node_record_id": f"node:{node}:{net}", "paddef_source_record_id": "paddef:DR-0102_60:Signal$TOP:0", "regular_source_record_id": "regular:DR-0102_60:Signal$TOP:0"})
    return source, records


def test_framed_hash_and_numeric_node_token_are_unambiguous() -> None:
    assert audit._framed_sha256(["A", "bc"]) != audit._framed_sha256(["ab", "C"])
    row = {"source_node_record_id": "node:Node19552:ADC_VDD_180_VQPS_SYS_1_AON/0", "pin_id": "SITE0:20122"}
    assert audit._token_from_row(row) == "$Package.Node19552!!20122::ADC_VDD_180_VQPS_SYS_1_AON/0"
    with pytest.raises(audit.AuditStop, match="STOP_IR_BINDING_MISMATCH"):
        audit._token_from_row({"source_node_record_id": "node:NodeP:VDD", "pin_id": "SITE0:P1"})


def test_port_records_hash_of_exact_raw_header_bytes(tmp_path: Path) -> None:
    header = f"{audit.EXPECTED_PORT} Auto\r\n".encode("utf-8")
    payload = header + b"+ PositiveTerminal $Package.Node1!!1::VDD\r\n.EndPort\r\n"
    source = tmp_path / "port44.spd"
    source.write_bytes(payload)
    result = audit._port(source, audit.EXPECTED_PORT, 1, 0, {"$Package.Node1!!1::VDD"}, set(), 10**20)
    assert result["header_sha256"] == hashlib.sha256(header).hexdigest()
    assert result["header_byte_end"] - result["header_byte_start"] == len(header)


def test_port_terminates_at_consecutive_next_header(tmp_path: Path) -> None:
    first_header = f"{audit.EXPECTED_PORT} Auto\n".encode("utf-8")
    first_body = b"+ PositiveTerminal $Package.Node1!!1::VDD\n+ NegativeTerminal $Package.Node2!!2::DGND\n"
    next_header = b"Port45_SITE0::OTHER Auto\n+ PositiveTerminal $Package.Node99!!99::OTHER\n"
    source = tmp_path / "consecutive_ports.spd"
    source.write_bytes(first_header + first_body + next_header)
    result = audit._port(source, audit.EXPECTED_PORT, 1, 1, {"$Package.Node1!!1::VDD"}, {"$Package.Node2!!2::DGND"}, 10**20)
    expected_section = first_header + first_body
    assert result["section_terminator"] == "next_port_header"
    assert result["section_byte_bounds"] == [0, len(expected_section)]
    assert result["section_line_end"] == 3
    assert result["section_sha256"] == hashlib.sha256(expected_section).hexdigest()
    assert result["positive_terminals"] == ["$Package.Node1!!1::VDD"]
    assert result["negative_count"] == 1


def test_derive_w0_reads_records_with_radius_semantics(tmp_path: Path) -> None:
    source, records = _source_records(tmp_path)
    terminals = []
    for i, (node, pin, net) in enumerate((("Node1", "20122", audit.EXPECTED_RAIL), ("Node2", "19958", "DGND"), ("Node3", "20576", audit.EXPECTED_RAIL), ("Node4", "20612", "DGND"), ("Node5", "20589", audit.EXPECTED_RAIL), ("Node6", "19973", "DGND"))):
        terminals.append({"pin_id": f"SITE0:{pin}", "source_node_record_id": f"node:{node}:{net}", "paddef_source_record_id": "paddef:DR-0102_60:Signal$TOP:0", "regular_source_record_id": "regular:DR-0102_60:Signal$TOP:0"})
    result = audit._derive_w0(terminals, records, source, [-1_000_000_000, -1_000_000_000, 1_000_000_000, 1_000_000_000], 10**20, gap_pm=30_000_000)
    assert result["footprint_count"] == 6
    assert result["footprints"][0]["radius_pm"] == 30_000_000
    assert result["footprints"][0]["diameter_pm"] == 60_000_000
    assert result["padding_pm"] == 240_000_000
    with pytest.raises(audit.AuditStop, match="STOP_W0_ASSERTION_MISMATCH"):
        audit._derive_w0(terminals, records, source, [0, 0, 1, 1], 10**20, gap_pm=30_000_000)


def test_selected_via_authority_is_deterministic_and_fail_closed(tmp_path: Path) -> None:
    source, records = _source_records(tmp_path)
    terminals = []
    for i, (node, endpoint, pin, net, layer) in enumerate((
        ("Node1", "Node1", "20122", audit.EXPECTED_RAIL, "Signal$L30(OTHER_POWER1)"),
        ("Node2", "Node2", "19958", "DGND", "Signal$L29(DGND)"),
        ("Node3", "Node3", "20576", audit.EXPECTED_RAIL, "Signal$L30(OTHER_POWER1)"),
        ("Node4", "Node4", "20612", "DGND", "Signal$L29(DGND)"),
        ("Node5", "Node5", "20589", audit.EXPECTED_RAIL, "Signal$L30(OTHER_POWER1)"),
        ("Node6", "Node6", "19973", "DGND", "Signal$L29(DGND)"),
    )):
        terminals.append({"ordinal": i, "terminal_id": f"term-{i}", "pin_id": f"SITE0:{pin}", "role": "power" if net == audit.EXPECTED_RAIL else "ground", "source_node_record_id": f"node:{node}:{net}", "via_record_id": f"via:Via{i}:{net}", "endpoint_node_id": endpoint, "layer": layer, "padstack_id": audit.EXPECTED_PADSTACK})

    class Loaded:
        def iter_section(self, section):
            return iter(records.values())

    authority = audit._selected_via_source_authority(terminals, records, Loaded(), source, 10**20)
    assert authority == audit._selected_via_source_authority(terminals, records, Loaded(), source, 10**20)
    assert authority["status"] == "PARTIAL" and authority["target_layer_traversal_proven"] is False
    assert len(authority["rows"]) == 6
    assert all(row["source_endpoint_layer"] == "Signal$TOP" for row in authority["rows"])
    assert all(row["opposite_endpoint_layer"] == "Signal$L02(DGND)" for row in authority["rows"])
    assert all(row["selected_plane_layer"] not in {row["source_endpoint_layer"], row["opposite_endpoint_layer"]} for row in authority["rows"])
    missing = dict(records)
    missing.pop("via:Via0:" + audit.EXPECTED_RAIL)
    with pytest.raises(audit.AuditStop, match="STOP_W0_VIA_SOURCE_EVIDENCE_MISSING"):
        audit._selected_via_source_authority(terminals, missing, Loaded(), source, 10**20)
    tampered = tmp_path / "tampered.spd"
    data = bytearray(source.read_bytes())
    index = data.index(b"PadStack")
    data[index + len(b"PadStac")] = ord("x")
    tampered.write_bytes(data)
    with pytest.raises(audit.AuditStop, match="STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH"):
        audit._selected_via_source_authority(terminals, records, Loaded(), tampered, 10**20)

    obsolete_shorthand = {record_id: dict(record) for record_id, record in records.items()}
    obsolete_shorthand["node:Node7:" + audit.EXPECTED_RAIL]["layer"] = "Signal$L02"
    with pytest.raises(audit.AuditStop, match="STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH"):
        audit._selected_via_source_authority(terminals, obsolete_shorthand, Loaded(), source, 10**20)


def _d104_fixture(tmp_path: Path, *, empty_ordinal: int | None = None) -> tuple[Path, dict]:
    root = tmp_path / "wkb"
    root.mkdir()
    cells = []
    for ordinal in range(258, 274):
        if ordinal in audit.D104_EXPECTED:
            shape = box(1000, 1000, 1001, 1001) if ordinal == empty_ordinal else box(0, 0, 10, 10)
            payload = dumps(shape)
            filename = f"cell_{ordinal:04d}.wkb"
            (root / filename).write_bytes(payload)
            layer, net, island = audit.D104_EXPECTED[ordinal]
            cells.append({"ordinal": ordinal, "layer": layer, "net": net, "island_id": island, "geometry": {"filename": filename, "wkb_sha256": hashlib.sha256(payload).hexdigest(), "wkb_size_bytes": len(payload), "bbox_um": list(shape.bounds), "area_um2": shape.area}})
        else:
            cells.append({"ordinal": ordinal})
    return root, {"status": "PASS", "version": audit.VERSION, "layer_counts": {"L28": 1, "L29": 1, "L30": 7, "L31": 7}, "cells": cells}


def test_membership_passes_with_large_target_and_separated_other_islands() -> None:
    cells = [
        {"ordinal": 259, "layer": "Signal$L29(DGND)", "net": "DGND", "_shape": box(-100, -100, 100, 100)},
        {"ordinal": 264, "layer": "Signal$L30(OTHER_POWER1)", "net": audit.EXPECTED_RAIL, "_shape": box(-100, -100, 100, 100)},
    ]
    for ordinal in (260, 261, 262, 263, 265, 266):
        cells.append({"ordinal": ordinal, "layer": "Signal$L30(OTHER_POWER1)", "net": f"OTHER/{ordinal}", "_shape": box(1000 + ordinal, 1000, 1100 + ordinal, 1100)})
    footprints = [
        {"pin_id": pin, "center_pm": [0, 0], "radius_pm": 30_000_000}
        for pin in ("SITE0:20122", "SITE0:20576", "SITE0:20589", "SITE0:19958", "SITE0:20612", "SITE0:19973")
    ]
    result = audit._membership(cells, footprints)
    assert result["passed"] is True
    assert result["violations"] == []
    assert len(result["terminals"]) == 6


def test_d104_exact_mapping_and_eight_row_coverage() -> None:
    root = Path(__file__).parent
    data = {"layer_counts": {"L28": 1, "L29": 1, "L30": 7, "L31": 7}, "cells": []}
    for ordinal in range(258, 274):
        if ordinal in audit.D104_EXPECTED:
            layer, net, island = audit.D104_EXPECTED[ordinal]
            data["cells"].append({"ordinal": ordinal, "layer": layer, "net": net, "island_id": island, "geometry": {"filename": f"cell_{ordinal:04d}.wkb", "wkb_sha256": "0" * 64, "wkb_size_bytes": 1, "bbox_um": [0, 0, 1, 1]}})
        else:
            data["cells"].append({"ordinal": ordinal})
    for field in ("layer", "net", "island_id"):
        bad = json.loads(json.dumps(data))
        bad["cells"][1][field] = "WRONG"
        with pytest.raises(audit.AuditStop, match="STOP_D104_CENSUS_MISMATCH"):
            audit._geometry(bad, root, [0, 0, 1, 1], 10**20)
    bad = json.loads(json.dumps(data))
    bad["cells"][0]["ordinal"] = "258"
    with pytest.raises(audit.AuditStop, match="STOP_D104_CENSUS_MISMATCH"):
        audit._geometry(bad, root, [0, 0, 1, 1], 10**20)


def test_real_ownership_loader_is_used_for_candidate_envelope() -> None:
    manifest, (asset_name, payload) = build_source_plane_ownership_ir(draft())
    project = {"metadata": {"spd_import": {"source_plane_ownership_ir": manifest}}}
    assert validate_project_source_plane_ownership_ir_envelope(project, {asset_name: payload}) == manifest
    with load_source_plane_ownership_ir(manifest, {asset_name: payload}, expected_app_version=audit.VERSION, **binds(manifest)) as loaded:
        assert len(list(loaded.iter_section("terminal_bindings"))) == 2


def test_coverage_conflict_writes_stop_receipt_and_requires_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, records = _source_records(tmp_path)
    port = f"{audit.EXPECTED_PORT} Auto\n+ PositiveTerminal $Package.Node1!!20122::{audit.EXPECTED_RAIL} $Package.Node3!!20576::{audit.EXPECTED_RAIL} $Package.Node5!!20589::{audit.EXPECTED_RAIL}\n+ NegativeTerminal $Package.Node2!!19958::DGND $Package.Node4!!20612::DGND\n+ $Package.Node6!!19973::DGND\n.EndPort\n"
    source.write_bytes(port.encode() + source.read_bytes())
    # Rebuild offsets after prepending the Port44 block.
    records = {key: {**value, "source_offset": int(value["source_offset"]) + len(port.encode()), "source_end": int(value["source_end"]) + len(port.encode())} for key, value in records.items()}
    d103 = tmp_path / "d103.json"
    d103.write_text(json.dumps({"status": "PASS", "version": audit.VERSION, "source": {"path": str(source), "size_bytes": source.stat().st_size, "sha256": _sha(source)}, "stackup_layers": [{"ordinal": 56, "layer_name": "Signal$L29(DGND)", "layer_kind": "conductor", "thickness_um": 20.0, "depth_from_stack_top_um": {"top": 1947.0, "bottom": 1967.0}}, {"ordinal": 57, "layer_name": "Medium$DR2930", "layer_kind": "dielectric", "thickness_um": 30.0, "depth_from_stack_top_um": {"top": 1967.0, "bottom": 1997.0}, "material_name": "ABF-GL102", "material_origin": "material_model", "material_source_record_id": "material:ABF-GL102"}, {"ordinal": 58, "layer_name": "Signal$L30(OTHER_POWER1)", "layer_kind": "conductor", "thickness_um": 20.0, "depth_from_stack_top_um": {"top": 1997.0, "bottom": 2017.0}}], "dielectric_points": [{"layer_name": "Medium$DR2930", "frequency_hz": 1000000.0, "epsilon_r": 3.4, "epsilon_origin": "material_model", "epsilon_source_record_id": "material:ABF-GL102", "frequency_origin": "material_model", "frequency_source_record_id": "material:ABF-GL102"}]}), encoding="utf-8")
    d104_root, d104_value = _d104_fixture(tmp_path, empty_ordinal=259)
    d104 = d104_root / "geometry_receipt.json"
    d104.write_text(json.dumps({"source": {"path": str(source), "size_bytes": source.stat().st_size, "sha256": _sha(source)}, **d104_value}), encoding="utf-8")
    candidate = tmp_path / "candidate.spdpi"; candidate.write_bytes(b"candidate")
    rails = [{"ordinal": 0, "rail_id": audit.EXPECTED_RAIL, "role": "power", "logical_net": audit.EXPECTED_RAIL, "artwork_net": audit.EXPECTED_RAIL, "layer": "Signal$L30(OTHER_POWER1)", "island_id": audit.D104_EXPECTED[264][2], "state": "source_bound"}, {"ordinal": 1, "rail_id": audit.EXPECTED_RAIL, "role": "ground", "logical_net": "DGND", "artwork_net": "DGND", "layer": "Signal$L29(DGND)", "island_id": audit.D104_EXPECTED[259][2], "state": "source_bound"}]
    terminals = []
    for i, (pin, role, node, endpoint, net, branch) in enumerate((("20122", "power", "Node1", "Node1", audit.EXPECTED_RAIL, "b1"), ("19958", "ground", "Node2", "Node2", "DGND", "b1"), ("20576", "power", "Node3", "Node3", audit.EXPECTED_RAIL, "b2"), ("20612", "ground", "Node4", "Node4", "DGND", "b2"), ("20589", "power", "Node5", "Node5", audit.EXPECTED_RAIL, "b3"), ("19973", "ground", "Node6", "Node6", "DGND", "b3"))):
        terminals.append({"ordinal": i, "terminal_id": f"term-{i}", "owner_kind": "device", "rail_id": audit.EXPECTED_RAIL, "branch_id": branch, "pin_id": f"SITE0:{pin}", "role": role, "source_node_record_id": f"node:{node}:{net}", "via_record_required": 1, "via_record_id": f"via:Via{i}:{net}", "endpoint_node_id": endpoint, "island_id": audit.D104_EXPECTED[264 if role == "power" else 259][2], "component_id": f"comp-{i}", "layer": audit.D104_EXPECTED[264 if role == "power" else 259][0], "padstack_id": audit.EXPECTED_PADSTACK, "paddef_source_record_id": "paddef:DR-0102_60:Signal$TOP:0", "regular_source_record_id": "regular:DR-0102_60:Signal$TOP:0", "raw_pad_shape_ordinal": 12, "raw_pad_shape_sha256": audit.EXPECTED_RAW_PAD_SHA, "finite_vertex_id": "spd-finite-via-vertex:aef72c164348031705f8bec6" if role == "power" else "spd-finite-via-vertex:a0993f5ab9a6ce414dbfe97c", "finite_edge_id": f"edge-{i}", "via_owner_id": f"owner-{i}", "status": "complete", "issues_json": "[]"})
    manifest = {"app_version": audit.VERSION, "source_sha256": _sha(source), "source_size_bytes": source.stat().st_size, "target_rail_id": audit.EXPECTED_RAIL, **{key: "a" * 64 for key in ("project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")}}
    d115b_value = {"product": audit.PRODUCT, "version": audit.VERSION, "schema_version": "d115b-source-plane-ownership-materialization-receipt-v1", "status": "PASS", "disposition": "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED", "contract_head": audit.EXPECTED_HEAD, "rail_id": audit.EXPECTED_RAIL, "source": {"path": str(source), "size_bytes": source.stat().st_size, "sha256": _sha(source)}, "source_path": str(source), "source_size_bytes": source.stat().st_size, "candidate": {"path": str(candidate), "present": True, "size_bytes": candidate.stat().st_size, "sha256": _sha(candidate), "rail_count": 2, "terminal_count": 6, "rail_rows": rails, "terminal_rows": terminals}}
    d115b = tmp_path / "d115b.json"; d115b.write_text(json.dumps(d115b_value), encoding="utf-8")
    monkeypatch.setattr(audit, "_git_identity", lambda *args: {"root": str(tmp_path), "head": audit.EXPECTED_HEAD, "branch": "main", "tracked_clean": True})
    fake_bundle = SimpleNamespace(scenario=SimpleNamespace(normalized_project={}), attachments={})
    class FakeIR:
        def iter_section(self, section):
            return iter(rails if section == "rail_bindings" else terminals if section == "terminal_bindings" else records.values())
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
    fake_ir = FakeIR()
    monkeypatch.setattr(audit, "load_scenario_bundle", lambda *args, **kwargs: fake_bundle)
    monkeypatch.setattr(audit, "validate_project_source_plane_ownership_ir_envelope", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(audit, "load_source_plane_ownership_ir", lambda *args, **kwargs: fake_ir)
    output = tmp_path / "receipt.json"
    kwargs = {"source_path": source, "d103_path": d103, "d104_path": d104, "d104_root": d104_root, "d115b_path": d115b, "candidate_path": candidate, "output_path": output, "repo_root": tmp_path, "expected_source_sha256": _sha(source), "expected_d103_sha256": _sha(d103), "expected_d104_sha256": _sha(d104), "expected_d115b_sha256": _sha(d115b), "expected_candidate_sha256": _sha(candidate), "expected_head": audit.EXPECTED_HEAD, "window_pm": [-1_000_000_000, -1_000_000_000, 1_000_000_000, 1_000_000_000], "expected_positive_count": 3, "expected_negative_count": 3}
    with pytest.raises(audit.AuditStop, match="STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT"):
        audit.audit_source_local_l29_l30_port_window(**kwargs)
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == receipt["code"] == "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT"
    assert receipt["coverage_conflict"]["empty_ordinals"] == [259]
    assert receipt["membership"]["evaluated"] is True
    assert len(receipt["membership"]["terminals"]) == 6
    assert receipt["membership"]["violations"]
    authority = receipt["selected_via_source_authority"]
    assert authority["status"] == "PARTIAL"
    assert authority["target_layer_traversal_proven"] is False
    assert authority["row_count"] == len(authority["rows"]) == 6
    assert [row["via_record_id"] for row in authority["rows"]] == [f"via:Via{i}:{audit.EXPECTED_RAIL if i % 2 == 0 else 'DGND'}" for i in range(6)]
    with pytest.raises(TypeError):
        audit.audit_source_local_l29_l30_port_window(source_path=source, d103_path=d103, d104_path=d104, d104_root=d104_root, candidate_path=candidate, output_path=tmp_path / "x", repo_root=tmp_path, expected_source_sha256=kwargs["expected_source_sha256"], expected_d103_sha256=kwargs["expected_d103_sha256"], expected_d104_sha256=kwargs["expected_d104_sha256"], expected_candidate_sha256=kwargs["expected_candidate_sha256"], expected_head=audit.EXPECTED_HEAD, window_pm=kwargs["window_pm"])
