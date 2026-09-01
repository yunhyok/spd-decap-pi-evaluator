from __future__ import annotations
from hashlib import sha256
import json, sqlite3, tempfile, zlib
from pathlib import Path
import pytest
import spd_decap_pi.source_plane_ownership_ir as ownership_ir
from spd_decap_pi.source_plane_ownership_ir import SourcePlaneOwnershipIRError, build_source_plane_ownership_ir, build_source_plane_ownership_ir_from_spool, load_source_plane_ownership_ir, validate_project_source_plane_ownership_ir_envelope, SOURCE_PLANE_OWNERSHIP_IR_METADATA_KEY, _COLUMNS_V2, _SourcePlaneOwnershipSpool, _V2_SECTIONS, _create_source_plane_ownership_v2_tables

def h(ch: str) -> str: return ch * 64
def sh(values): return sha256(json.dumps(sorted(values), separators=(",", ":")).encode()).hexdigest()
def draft():
    records = [
        {"ordinal":0,"record_id":"node-p","kind":"Node","source_offset":0,"source_end":10,"source_record_sha256":h("0")},
        {"ordinal":1,"record_id":"node-g","kind":"Node","source_offset":10,"source_end":20,"source_record_sha256":h("1")},
        {"ordinal":2,"record_id":"shape-p0","kind":"Shape","source_offset":20,"source_end":30,"source_record_sha256":h("2")},
        {"ordinal":3,"record_id":"shape-p1","kind":"Shape","source_offset":30,"source_end":40,"source_record_sha256":h("3")},
        {"ordinal":4,"record_id":"shape-p2","kind":"Shape","source_offset":40,"source_end":50,"source_record_sha256":h("4")},
        {"ordinal":5,"record_id":"shape-g0","kind":"Shape","source_offset":50,"source_end":60,"source_record_sha256":h("5")},
        {"ordinal":6,"record_id":"via-a","kind":"Via","source_offset":60,"source_end":70,"source_record_sha256":h("6")},
        {"ordinal":7,"record_id":"via-b","kind":"Via","source_offset":70,"source_end":80,"source_record_sha256":h("7")},
        {"ordinal":8,"record_id":"layer-1","kind":"Layer","source_offset":80,"source_end":90,"source_record_sha256":h("8")},
        {"ordinal":9,"record_id":"material-1","kind":"Material","source_offset":90,"source_end":100,"source_record_sha256":h("9")},
        {"ordinal":10,"record_id":"layer-2","kind":"Layer","source_offset":100,"source_end":110,"source_record_sha256":h("a")},
        {"ordinal":11,"record_id":"paddef-p","kind":"PadDef","source_offset":110,"source_end":120,"source_record_sha256":h("b")},
        {"ordinal":12,"record_id":"regular-p","kind":"Regular","source_offset":120,"source_end":130,"source_record_sha256":h("c")},
        {"ordinal":13,"record_id":"paddef-g","kind":"PadDef","source_offset":130,"source_end":140,"source_record_sha256":h("d")},
        {"ordinal":14,"record_id":"regular-g","kind":"Regular","source_offset":140,"source_end":150,"source_record_sha256":h("e")},
    ]
    def prim(pid,surface,local,raw,pol,kind,effect,src,asset): return {"primitive_id":pid,"surface_id":surface,"local_ordinal":local,"raw_primitive_ordinal":raw,"polarity":pol,"kind":kind,"effect_status":effect,"source_record_id":src,"source_asset_name":asset,"source_asset_sha256":h("a"),"primitive_sha256":h("b")}
    return {"app_version":"0.23.1","source_sha256":h("c"),"source_size_bytes":1000,"target_rail_id":"RAIL/0","project_binding_sha256":h("d"),"certificate_evidence_sha256":h("e"),"compiled_topology_identity_sha256":h("f"),"raw_manifest_sha256":h("0"),"raw_geometry_identity_sha256":h("1"),"raw_logical_rows_sha256":h("2"),"raw_plane_sheet_sha256":h("3"),"source_records":records,
      "surfaces":[{"ordinal":0,"surface_id":"surface:p","artwork_net":"ART_P","layer":"L1","geometry_asset_name":"geometry/p","geometry_asset_sha256":h("4"),"island_manifest_sha256":h("5"),"source_lineage_sha256":sh(["shape-p0","shape-p1","shape-p2"]),"component_count":1},{"ordinal":1,"surface_id":"surface:g","artwork_net":"ART_G","layer":"L1","geometry_asset_name":"geometry/g","geometry_asset_sha256":h("6"),"island_manifest_sha256":h("7"),"source_lineage_sha256":sh(["shape-g0"]),"component_count":1}],
      "primitives":[prim("p0","surface:p",0,0,"+","Polygon","retained","shape-p0","geometry/p"),prim("p1","surface:p",1,1,"-","PolygonTrace","retained","shape-p1","geometry/p"),prim("p2","surface:p",2,2,"+","Circle","no_survivor","shape-p2","geometry/p"),prim("g0","surface:g",0,3,"+","Polygon","retained","shape-g0","geometry/g")],
      "islands":[{"ordinal":0,"island_id":"island:p","surface_id":"surface:p","component_id":"comp:p","representative":1,"positive_witness_count":1},{"ordinal":1,"island_id":"island:g","surface_id":"surface:g","component_id":"comp:g","representative":1,"positive_witness_count":1}],
      "primitive_island_edges":[{"primitive_id":"p0","island_id":"island:p","witness_kind":"positive_area_witness","relation_sha256":h("8")},{"primitive_id":"p1","island_id":"island:p","witness_kind":"negative_boundary_witness","relation_sha256":h("9")},{"primitive_id":"g0","island_id":"island:g","witness_kind":"positive_area_witness","relation_sha256":h("a")}],
      "stackup_layers":[{"ordinal":0,"layer_name":"L1","layer_kind":"conductor","raw_layer_ordinal":0,"raw_layer_sha256":h("b"),"thickness_um":35.0,"thickness_origin":"source","thickness_source_record_id":"layer-1","conductivity_s_per_m":58000000.0,"conductivity_origin":"source","conductivity_source_record_id":"layer-1","material_name":"Copper","material_origin":"source","material_source_record_id":"layer-1"},{"ordinal":1,"layer_name":"L2","layer_kind":"dielectric","raw_layer_ordinal":1,"raw_layer_sha256":h("c"),"thickness_um":100.0,"thickness_origin":"source","thickness_source_record_id":"layer-2","conductivity_s_per_m":None,"conductivity_origin":"unavailable","conductivity_source_record_id":None,"material_name":"FR4","material_origin":"material_model","material_source_record_id":"material-1"}],
      "dielectric_points":[{"ordinal":0,"layer_name":"L2","point_ordinal":0,"raw_dielectric_ordinal":0,"raw_dielectric_sha256":h("d"),"frequency_hz":1e9,"frequency_origin":"material_model","frequency_source_record_id":"material-1","epsilon_r":4.0,"epsilon_origin":"layer_override","epsilon_source_record_id":"layer-2","loss_tangent":0.01,"loss_tangent_origin":"material_model","loss_tangent_source_record_id":"material-1"}],
      "rail_bindings":[{"ordinal":0,"rail_id":"RAIL/0","role":"power","logical_net":"VDD","artwork_net":"ART_P","layer":"L1","surface_id":"surface:p","island_id":"island:p","pair_evidence_sha256":h("d"),"state":"source_bound"},{"ordinal":1,"rail_id":"RAIL/0","role":"ground","logical_net":"GND","artwork_net":"ART_G","layer":"L1","surface_id":"surface:g","island_id":"island:g","pair_evidence_sha256":h("e"),"state":"source_bound"}],
      "terminal_bindings":[{"ordinal":0,"terminal_id":"term:p","owner_kind":"component","rail_id":"RAIL/0","branch_id":"branch:p","pin_id":"P1","role":"power","source_node_record_id":"node-p","via_record_required":1,"via_record_id":"via-a","endpoint_node_id":"end-p","island_id":"island:p","component_id":"comp:p","layer":"L1","padstack_id":"pad:p","paddef_source_record_id":"paddef-p","regular_source_record_id":"regular-p","raw_pad_shape_ordinal":0,"raw_pad_shape_sha256":h("f"),"finite_vertex_id":"v:p","finite_edge_id":"edge-a","via_owner_id":"ViaA","status":"complete","issues_json":"[]"},{"ordinal":1,"terminal_id":"term:g","owner_kind":"component","rail_id":"RAIL/0","branch_id":"branch:g","pin_id":"G1","role":"ground","source_node_record_id":"node-g","via_record_required":1,"via_record_id":"via-b","endpoint_node_id":"end-g","island_id":"island:g","component_id":"comp:g","layer":"L1","padstack_id":"pad:g","paddef_source_record_id":"paddef-g","regular_source_record_id":"regular-g","raw_pad_shape_ordinal":1,"raw_pad_shape_sha256":h("1"),"finite_vertex_id":"v:g","finite_edge_id":"edge-b","via_owner_id":"ViaB","status":"complete","issues_json":"[]"}],
      "retained_owner_refs":[{"ordinal":0,"owner_id":"ViaA","namespace":"raw-via","owner_kind":"via","rail_id":"RAIL/0","edge_id":"edge-a","island_id":"island:p","state":"retained"},{"ordinal":1,"owner_id":"ViaB","namespace":"raw-via","owner_kind":"via","rail_id":"RAIL/0","edge_id":"edge-b","island_id":"island:g","state":"retained"}],
      "plane_owner_scopes":[{"ordinal":0,"scope_id":"scope:p","namespace":"plane","compiler_owner_id":"PlaneOwner0","rail_id":"RAIL/0","role":"power","artwork_net":"ART_P","layer":"L1","state":"declared_unconsumed","owner_count":1}],
      "replacement_ledger":[{"ordinal":0,"ledger_id":"ledger:0","replaced_set_sha256":sh({"planeowner0"}),"retained_set_sha256":sh({"viaa","viab"}),"intersection_count":0,"replaced_count":1,"retained_count":2,"status":"prerequisite_only"}],
      "replacement_ledger_members":[{"ledger_id":"ledger:0","owner_id":"PlaneOwner0","action":"replaced"},{"ledger_id":"ledger:0","owner_id":"ViaA","action":"retained"},{"ledger_id":"ledger:0","owner_id":"ViaB","action":"retained"}]}

def binds(m): return {"expected_"+k:m[k] for k in ("source_sha256","project_binding_sha256","certificate_evidence_sha256","compiled_topology_identity_sha256","raw_manifest_sha256","raw_geometry_identity_sha256","raw_logical_rows_sha256","raw_plane_sheet_sha256")}

def _v2_binding(value):
    return {key: value[key] for key in ("app_version", "source_sha256", "source_size_bytes", "target_rail_id", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")}

def _contact_draft():
    value = draft(); value["contact_boundary"] = [{"ordinal": 0, "contact_id": "contact:0", "owner_kind": "other", "net": "VDD", "via_id": "via11", "endpoint_node_id": "Node11", "opposite_endpoint_node_id": "Node12", "plane_endpoint_node_id": "Node11", "external_endpoint_node_id": "Node12", "endpoint_layer": "L1", "island_id": "island:p", "component_id": "comp:p", "padstack_id": "DR-0102_60", "rotation_degrees": 4.5, "source_node_record_id": "node:Node11:VDD", "opposite_endpoint_node_record_id": "node:Node12:VDD", "plane_endpoint_node_record_id": "node:Node11:VDD", "external_endpoint_node_record_id": "node:Node12:VDD", "via_record_id": "via:via11:VDD", "paddef_source_record_id": "paddef:DR-0102_60:L1:0", "regular_source_record_id": "regular:DR-0102_60:L1:0", "raw_pad_shape_ordinal": 0, "raw_pad_shape_sha256": h("f"), "finite_vertex_id": "vertex:contact", "finite_edge_id": "edge:contact", "owner_ids_json": '["via:via11"]', "status": "complete", "issues_json": "[]"}]
    value["source_records"].extend((
        {"ordinal": 15, "record_id": "node:Node11:VDD", "kind": "Node", "source_offset": 200, "source_end": 210, "source_record_sha256": h("0"), "logical_net": "VDD", "layer": "L1"},
        {"ordinal": 16, "record_id": "node:Node12:VDD", "kind": "Node", "source_offset": 210, "source_end": 220, "source_record_sha256": h("1"), "logical_net": "VDD", "layer": "L1"},
        {"ordinal": 17, "record_id": "via:via11:VDD", "kind": "Via", "source_offset": 220, "source_end": 230, "source_record_sha256": h("2"), "logical_net": "VDD", "layer": "L1"},
        {"ordinal": 18, "record_id": "paddef:DR-0102_60:L1:0", "kind": "PadDef", "source_offset": 230, "source_end": 240, "source_record_sha256": h("3"), "logical_net": None, "layer": "L1"},
        {"ordinal": 19, "record_id": "regular:DR-0102_60:L1:0", "kind": "Regular", "source_offset": 240, "source_end": 250, "source_record_sha256": h("4"), "logical_net": None, "layer": "L1"},
    ))
    value["retained_owner_refs"].append({"ordinal": 2, "owner_id": "via:via11", "namespace": "finite-via", "owner_kind": "via", "rail_id": "RAIL/0", "edge_id": "edge:contact", "island_id": "island:p", "state": "retained"})
    value["replacement_ledger"][0].update(retained_set_sha256=sh({"viaa", "viab", "via:via11"}), retained_count=3)
    value["replacement_ledger_members"].append({"ledger_id": "ledger:0", "owner_id": "via:via11", "action": "retained"})
    return value

def _spool(tmp_path, value, *, internal=False):
    path = tmp_path / "ownership-spool.sqlite"; connection = sqlite3.connect(path); _create_source_plane_ownership_v2_tables(connection)
    for section in _V2_SECTIONS:
        columns = _COLUMNS_V2[section]; names = ",".join(columns)
        for row in value.get(section, ()):
            connection.execute(f"INSERT INTO {section} ({names}) VALUES ({','.join('?' for _ in columns)})", [row.get(column) for column in columns])
    if internal: connection.execute("CREATE TABLE internal_only(value TEXT)")
    connection.commit(); connection.close(); return path

def test_v2_pressure_vector_uses_bounded_generators(tmp_path):
    """Focused acceptance fixture: 77,852 Node + 38,926 Via, 122,146 source, 249,635 final."""
    path = tmp_path / "pressure"; connection = sqlite3.connect(path); _create_source_plane_ownership_v2_tables(connection)
    def batches(rows, size=1000):
        batch = []
        for row in rows:
            batch.append(row)
            if len(batch) == size:
                yield batch; batch = []
        if batch: yield batch
    def digest(value): return sha256(value.encode()).hexdigest()
    def canonical_names(prefix, count, *, padded=False, width=5, folded=False):
        value = sha256(b"[")
        for i in range(count):
            if i: value.update(b",")
            name = f"{prefix}{i:0{width}d}" if padded else f"{prefix}{i}"; value.update(json.dumps(name.casefold() if folded else name, ensure_ascii=False, separators=(",", ":")).encode())
        value.update(b"]"); return value.hexdigest()
    def records():
        ordinal = 0; offset = 0
        groups = (("Node", "node", 77852), ("Via", "via", 38926), ("Shape", "shape", 5356), ("Layer", "layer", 2), ("Material", "material", 1), ("PadDef", "paddef", 2), ("Regular", "regular", 2), ("Aux", "aux", 5))
        for kind, prefix, count in groups:
            for index in range(count):
                if prefix == "shape": record_id = f"shape-{index:04d}"
                elif prefix == "paddef": record_id = f"paddef:pad:{'p' if index == 0 else 'g'}:L1:{index}"
                elif prefix == "regular": record_id = f"regular:pad:{'p' if index == 0 else 'g'}:L1:{index}"
                else: record_id = f"{prefix}-{index}"
                yield (ordinal, record_id, kind, offset, offset + 10, digest(record_id), None, None); ordinal += 1; offset += 10
    def insert(section, rows):
        columns = _COLUMNS_V2[section]; names = ",".join(columns); sql = f"INSERT INTO {section} ({names}) VALUES ({','.join('?' for _ in columns)})"
        for batch in batches(rows): connection.executemany(sql, batch)
    insert("source_records", records())
    insert("surfaces", ((0, "surface:p", "ART_P", "L1", "geometry/p", h("4"), h("5"), canonical_names("shape-", 5356, padded=True, width=4), 1), (1, "surface:unused", "ART_UNUSED", "L1", "geometry/unused", h("7"), h("8"), canonical_names("", 0), 0)))
    insert("primitives", ((f"p-{i}", "surface:p", i, i, "+", "Polygon", "retained", f"shape-{i:04d}", "geometry/p", h("a"), h("b")) for i in range(5356)))
    insert("islands", ((0, "island:p", "surface:p", "comp:p", 1, 5356),))
    insert("primitive_island_edges", ((f"p-{i}", "island:p", "positive_area_witness", h("c")) for i in range(5356)))
    insert("stackup_layers", ((0, "L1", "conductor", 0, h("d"), 35.0, "source", "layer-0", 58000000.0, "source", "layer-0", "Copper", "source", "layer-0"), (1, "L2", "dielectric", 1, h("e"), 100.0, "source", "layer-1", None, "unavailable", None, "FR4", "material_model", "material-0")))
    insert("dielectric_points", ((0, "L2", 0, 0, h("f"), 1e9, "material_model", "material-0", 4.0, "layer_override", "layer-1", 0.01, "material_model", "material-0"),))
    insert("rail_bindings", ((0, "RAIL/0", "power", "VDD", "ART_P", "L1", "surface:p", "island:p", h("0"), "source_bound"), (1, "RAIL/0", "ground", "GND", "ART_P", "L1", "surface:p", "island:p", h("1"), "source_bound")))
    insert("terminal_bindings", ((0, "term:p", "component", "RAIL/0", "branch:p", "P1", "power", "node-0", 1, "via-0", "end-p", "island:p", "comp:p", "L1", "pad:p", "paddef:pad:p:L1:0", "regular:pad:p:L1:0", 0, h("2"), "v:p", "edge-0", "ViaOwner00000", "complete", "[]"), (1, "term:g", "component", "RAIL/0", "branch:g", "G1", "ground", "node-1", 1, "via-1", "end-g", "island:p", "comp:p", "L1", "pad:g", "paddef:pad:g:L1:1", "regular:pad:g:L1:1", 1, h("3"), "v:g", "edge-1", "ViaOwner00001", "complete", "[]")))
    retained = 58382
    insert("retained_owner_refs", ((i, f"ViaOwner{i:05d}", "raw-via", "via", "RAIL/0", f"edge-{i}", "island:p", "retained") for i in range(retained)))
    insert("plane_owner_scopes", ((0, "scope:p", "plane", "PlaneOwner0", "RAIL/0", "power", "ART_P", "L1", "declared_unconsumed", 1),))
    insert("replacement_ledger", ((0, "ledger:0", canonical_names("PlaneOwner", 1, folded=True), canonical_names("ViaOwner", retained, padded=True, folded=True), 0, 1, retained, "prerequisite_only"),))
    def members():
        yield ("ledger:0", "PlaneOwner0", "replaced")
        for i in range(retained): yield ("ledger:0", f"ViaOwner{i:05d}", "retained")
    insert("replacement_ledger_members", members())
    connection.commit()
    counts = {section: connection.execute(f"SELECT COUNT(*) FROM {section}").fetchone()[0] for section in _V2_SECTIONS}; assert counts["source_records"] == 122146; assert connection.execute("SELECT COUNT(*) FROM source_records WHERE kind='Node'").fetchone()[0] == 77852; assert connection.execute("SELECT COUNT(*) FROM source_records WHERE kind='Via'").fetchone()[0] == 38926; assert sum(counts.values()) == 249635
    connection.close()
    binding = {"app_version": "0.23.1", "source_sha256": h("c"), "source_size_bytes": 2_000_000, "target_rail_id": "RAIL/0", "project_binding_sha256": h("d"), "certificate_evidence_sha256": h("e"), "compiled_topology_identity_sha256": h("f"), "raw_manifest_sha256": h("0"), "raw_geometry_identity_sha256": h("1"), "raw_logical_rows_sha256": h("2"), "raw_plane_sheet_sha256": h("3")}
    manifest, asset = build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), binding, batch_rows=1000)
    assert manifest["counts"]["source_records"] == 122146 and sum(manifest["counts"].values()) == 249635
    with load_source_plane_ownership_ir(manifest, {asset[0]: asset[1]}, expected_app_version=binding["app_version"], **{f"expected_{key}": binding[key] for key in ("source_sha256", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")} ) as loaded:
        loaded_kinds = {}
        for row in loaded.iter_section("source_records"):
            loaded_kinds[row["kind"]] = loaded_kinds.get(row["kind"], 0) + 1
        assert sum(loaded_kinds.values()) == 122146 and loaded_kinds["Node"] == 77852 and loaded_kinds["Via"] == 38926

def test_roundtrip_and_complete_chains():
    m,a=build_source_plane_ownership_ir(draft()); assert m["app_version"]=="0.23.1"
    with load_source_plane_ownership_ir(m,{a[0]:a[1]},expected_app_version="0.23.1",**binds(m)) as db:
        assert len(list(db.iter_section("terminal_bindings")))==2
    assert build_source_plane_ownership_ir(draft())== (m,a)

def test_v2_mapping_and_spool_are_logically_equivalent_and_exclude_internal_tables(tmp_path):
    value = draft(); value["contact_boundary"] = []
    mapping_manifest, _ = build_source_plane_ownership_ir(value)
    binding = _v2_binding(value)
    spool_manifest, spool_asset = build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(_spool(tmp_path, value, internal=True)), binding)
    repeat_path = tmp_path / "repeat"; repeat_path.mkdir()
    repeat_manifest, repeat_asset = build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(_spool(repeat_path, value)), binding)
    assert spool_manifest["logical_rows_sha256"] == mapping_manifest["logical_rows_sha256"]
    assert spool_manifest == repeat_manifest
    assert spool_asset == repeat_asset
    assert b"internal_only" not in zlib.decompress(spool_asset[1])

def test_spool_caps_and_cancellation_fail_closed(tmp_path, monkeypatch):
    value = draft(); value["contact_boundary"] = []
    monkeypatch.setattr(ownership_ir, "MAX_SOURCE_PLANE_OWNERSHIP_IR_SECTION_ROWS", 1)
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(value)
    monkeypatch.setattr(ownership_ir, "MAX_SOURCE_PLANE_OWNERSHIP_IR_SECTION_ROWS", 150_000)
    monkeypatch.setattr(ownership_ir, "MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS", 1)
    cap_path = tmp_path / "cap"; cap_path.mkdir()
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(_spool(cap_path, value)), _v2_binding(value))
    monkeypatch.setattr(ownership_ir, "MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS", 300_000)
    cancel_path = tmp_path / "cancel"; cancel_path.mkdir()
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(_spool(cancel_path, value)), _v2_binding(value), is_cancelled=lambda: True)
    monkeypatch.setattr(ownership_ir, "MAX_SOURCE_PLANE_OWNERSHIP_IR_COMPRESSED_BYTES", 1)
    payload_path = tmp_path / "payload"; payload_path.mkdir()
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(_spool(payload_path, value)), _v2_binding(value))
    monkeypatch.setattr(ownership_ir, "MAX_SOURCE_PLANE_OWNERSHIP_IR_COMPRESSED_BYTES", 512 * 1024 * 1024)
    monkeypatch.setattr(ownership_ir, "MAX_SOURCE_PLANE_OWNERSHIP_IR_UNCOMPRESSED_BYTES", 1)
    uncompressed_path = tmp_path / "uncompressed"; uncompressed_path.mkdir()
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(_spool(uncompressed_path, value)), _v2_binding(value))

def test_spool_sql_validation_rejects_provenance_mutations(tmp_path):
    value = draft(); value["contact_boundary"] = []
    value["retained_owner_refs"].append({"ordinal": 2, "owner_id": "Orphan", "namespace": "raw-via", "owner_kind": "via", "rail_id": "RAIL/0", "edge_id": "edge-orphan", "island_id": "island:p", "state": "retained"})
    value["replacement_ledger"][0].update(retained_set_sha256=sh({"viaa", "viab", "orphan"}), retained_count=3)
    value["replacement_ledger_members"].append({"ledger_id": "ledger:0", "owner_id": "Orphan", "action": "retained"})
    mutations = (
        ("UPDATE surfaces SET source_lineage_sha256=? WHERE surface_id=?", (h("f"), "surface:p")),
        ("UPDATE stackup_layers SET thickness_origin='material_model' WHERE layer_name='L1'", ()),
        ("UPDATE dielectric_points SET epsilon_origin='material_model' WHERE layer_name='L2'", ()),
        ("UPDATE terminal_bindings SET source_node_record_id='via-a' WHERE terminal_id='term:p'", ()),
        ("UPDATE terminal_bindings SET via_owner_id='missing' WHERE terminal_id='term:p'", ()),
        ("UPDATE retained_owner_refs SET edge_id=NULL WHERE owner_id='ViaA'", ()),
        ("UPDATE retained_owner_refs SET rail_id=NULL WHERE owner_id='ViaA'", ()),
        ("UPDATE retained_owner_refs SET island_id=NULL WHERE owner_id='ViaA'", ()),
        ("UPDATE retained_owner_refs SET rail_id=NULL WHERE owner_id='Orphan'", ()),
        ("UPDATE retained_owner_refs SET edge_id=NULL WHERE owner_id='Orphan'", ()),
        ("UPDATE retained_owner_refs SET island_id=NULL WHERE owner_id='Orphan'", ()),
        ("UPDATE replacement_ledger SET retained_set_sha256=? WHERE ledger_id='ledger:0'", (h("0"),)),
        ("UPDATE replacement_ledger_members SET action='replaced' WHERE ledger_id='ledger:0' AND owner_id='ViaA'", ()),
    )
    for index, (sql, params) in enumerate(mutations):
        case = tmp_path / f"mutation-{index}"; case.mkdir()
        path = _spool(case, value); connection = sqlite3.connect(path); connection.execute(sql, params); connection.commit(); connection.close()
        with pytest.raises(SourcePlaneOwnershipIRError) as error: build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), _v2_binding(value))
        if "owner_id='Orphan'" in sql:
            assert error.value.code == "SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID"

def test_spool_sql_contact_identity_and_edge_owner_rejections(tmp_path):
    value = _contact_draft(); base = tmp_path / "contact-base"; base.mkdir()
    manifest, _ = build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(_spool(base, value)), _v2_binding(value))
    assert manifest["counts"]["contact_boundary"] == 1
    mutations = (
        ("UPDATE contact_boundary SET endpoint_node_id='Node12' WHERE contact_id='contact:0'", ()),
        ("UPDATE contact_boundary SET via_record_id='node:Node11:VDD' WHERE contact_id='contact:0'", ()),
        ("UPDATE retained_owner_refs SET edge_id=NULL WHERE owner_id='via:via11'", ()),
        ("UPDATE contact_boundary SET owner_ids_json='[\"ViaA\"]' WHERE contact_id='contact:0'", ()),
        ("UPDATE source_records SET record_id='paddef:DR-0102X60:L1:0' WHERE record_id='paddef:DR-0102_60:L1:0'", ()),
        ("UPDATE contact_boundary SET padstack_id='DR-0102%60' WHERE contact_id='contact:0'", ()),
    )
    for index, (sql, params) in enumerate(mutations):
        case = tmp_path / f"contact-mutation-{index}"; case.mkdir()
        path = _spool(case, value); connection = sqlite3.connect(path); connection.execute(sql, params); connection.commit(); connection.close()
        if sql.startswith("UPDATE source_records SET record_id='paddef:DR-0102X60"):
            connection = sqlite3.connect(path); connection.execute("UPDATE source_records SET record_id='regular:DR-0102X60:L1:0' WHERE record_id='regular:DR-0102_60:L1:0'"); connection.execute("UPDATE contact_boundary SET paddef_source_record_id='paddef:DR-0102X60:L1:0',regular_source_record_id='regular:DR-0102X60:L1:0' WHERE contact_id='contact:0'"); connection.commit(); connection.close()
        elif sql.startswith("UPDATE contact_boundary SET padstack_id='DR-0102%60"):
            connection = sqlite3.connect(path); connection.execute("UPDATE source_records SET record_id='paddef:DR-0102X60:L1:0' WHERE record_id='paddef:DR-0102_60:L1:0'"); connection.execute("UPDATE source_records SET record_id='regular:DR-0102X60:L1:0' WHERE record_id='regular:DR-0102_60:L1:0'"); connection.execute("UPDATE contact_boundary SET paddef_source_record_id='paddef:DR-0102X60:L1:0',regular_source_record_id='regular:DR-0102X60:L1:0' WHERE contact_id='contact:0'"); connection.commit(); connection.close()
        with pytest.raises(SourcePlaneOwnershipIRError) as error: build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), _v2_binding(value))
        if sql.startswith("UPDATE retained_owner_refs SET edge_id=NULL"):
            assert error.value.code == "SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID"
        if sql.startswith("UPDATE source_records SET record_id='paddef:DR-0102X60") or sql.startswith("UPDATE contact_boundary SET padstack_id='DR-0102%60"):
            assert error.value.code == "SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID"

def test_spool_sql_rejects_origin_scalar_casefold_and_ordinal_holes(tmp_path):
    value = draft(); value["contact_boundary"] = []
    mutations = (
        ("UPDATE stackup_layers SET conductivity_origin='bogus' WHERE layer_name='L1'", ()),
        ("UPDATE stackup_layers SET conductivity_origin='material_model' WHERE layer_name='L1'", ()),
        ("UPDATE stackup_layers SET conductivity_origin=NULL WHERE layer_name='L2'", ()),
        ("UPDATE dielectric_points SET frequency_origin='unavailable' WHERE layer_name='L2'", ()),
        ("UPDATE dielectric_points SET epsilon_origin='source',epsilon_source_record_id='material-1' WHERE layer_name='L2'", ()),
        ("UPDATE terminal_bindings SET endpoint_node_id='' WHERE terminal_id='term:p'", ()),
        ("UPDATE terminal_bindings SET raw_pad_shape_ordinal=-1 WHERE terminal_id='term:p'", ()),
        ("UPDATE terminal_bindings SET raw_pad_shape_sha256=? WHERE terminal_id='term:p'", ("g" * 64,)),
        ("UPDATE terminal_bindings SET terminal_id=CASE terminal_id WHEN 'term:p' THEN 'İ' WHEN 'term:g' THEN 'i̇' END", ()),
        ("UPDATE primitives SET raw_primitive_ordinal=-1 WHERE primitive_id='p0'", ()),
        ("INSERT INTO replacement_ledger SELECT ordinal+1,'İ',replaced_set_sha256,retained_set_sha256,intersection_count,replaced_count,retained_count,status FROM replacement_ledger WHERE ledger_id='ledger:0'", ()),
        ("INSERT INTO plane_owner_scopes SELECT ordinal+1,'İ',namespace,'PlaneOwner1',rail_id,role,artwork_net,layer,state,owner_count FROM plane_owner_scopes", ()),
    )
    for index, (sql, params) in enumerate(mutations):
        case = tmp_path / f"scalar-mutation-{index}"; case.mkdir()
        path = _spool(case, value); connection = sqlite3.connect(path); connection.execute(sql, params); connection.commit(); connection.close()
        if sql.startswith("INSERT INTO replacement_ledger"):
            connection = sqlite3.connect(path); connection.execute("UPDATE replacement_ledger SET ledger_id='i̇' WHERE ledger_id='ledger:0'"); connection.execute("UPDATE replacement_ledger_members SET ledger_id='i̇' WHERE ledger_id='ledger:0'"); connection.commit(); connection.close()
        if sql.startswith("INSERT INTO plane_owner_scopes"):
            connection = sqlite3.connect(path); connection.execute("UPDATE plane_owner_scopes SET scope_id='i̇' WHERE scope_id='scope:p'"); connection.commit(); connection.close()
        with pytest.raises(SourcePlaneOwnershipIRError) as error: build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), _v2_binding(value))
        if sql.startswith("UPDATE terminal_bindings SET terminal_id") or sql.startswith("INSERT INTO replacement_ledger"):
            assert error.value.code == "SOURCE_PLANE_OWNERSHIP_IR_ID_COLLISION"
        if sql.startswith("INSERT INTO plane_owner_scopes"):
            assert error.value.code == "SOURCE_PLANE_OWNERSHIP_IR_OWNER_COLLISION"

def test_spool_validation_balanced_relations_use_indexed_lookups(tmp_path, monkeypatch):
    value = draft(); value["contact_boundary"] = []
    path = _spool(tmp_path, value)
    plans = []
    real_connect = sqlite3.connect

    class InstrumentedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if isinstance(sql, str) and sql.lstrip().upper().startswith("SELECT"):
                folded = sql.casefold()
                targets = tuple((signature, target, alias) for signature, target, alias in (
                    ("from _ir_surfaces where surface_fold", "surface", "_ir_surfaces"),
                    ("join _ir_layers l on lower(d.layer_name)=l.layer_fold", "layer", "l"),
                    ("from _ir_ledgers where ledger_fold", "ledger", "_ir_ledgers"),
                ) if signature in folded)
                if targets:
                    for row in sqlite3.Connection.execute(self, "EXPLAIN QUERY PLAN " + sql, parameters).fetchall():
                        plans.extend((target, alias, row[3].replace("USING COVERING INDEX", "USING INDEX")) for _, target, alias in targets)
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        kwargs["factory"] = InstrumentedConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(ownership_ir.sqlite3, "connect", connect)
    build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), _v2_binding(value))
    for target, alias in (("surface", "_ir_surfaces"), ("layer", "l"), ("ledger", "_ir_ledgers")):
        details = [detail for planned_target, planned_alias, detail in plans if planned_target == target and planned_alias == alias]
        assert details and any(f"SEARCH {alias} USING INDEX" in detail for detail in details)
        assert all(f"SCAN {alias}" not in detail for detail in details)

def test_spool_sql_rejects_contact_opposite_external_node_chain(tmp_path):
    value = _contact_draft()
    mutations = (
        ("UPDATE contact_boundary SET opposite_endpoint_node_record_id='node:Node11:VDD' WHERE contact_id='contact:0'", ()),
        ("UPDATE contact_boundary SET external_endpoint_node_record_id='node:Node11:VDD' WHERE contact_id='contact:0'", ()),
        ("UPDATE contact_boundary SET finite_vertex_id='' WHERE contact_id='contact:0'", ()),
        ("UPDATE contact_boundary SET raw_pad_shape_ordinal=-1 WHERE contact_id='contact:0'", ()),
        ("UPDATE contact_boundary SET raw_pad_shape_sha256=? WHERE contact_id='contact:0'", ("g" * 64,)),
        ("INSERT INTO contact_boundary SELECT ordinal+1,'i̇',owner_kind,net,via_id,endpoint_node_id,opposite_endpoint_node_id,plane_endpoint_node_id,external_endpoint_node_id,endpoint_layer,island_id,component_id,padstack_id,rotation_degrees,source_node_record_id,opposite_endpoint_node_record_id,plane_endpoint_node_record_id,external_endpoint_node_record_id,via_record_id,paddef_source_record_id,regular_source_record_id,raw_pad_shape_ordinal,raw_pad_shape_sha256,finite_vertex_id,finite_edge_id,owner_ids_json,status,issues_json FROM contact_boundary", ()),
    )
    for index, (sql, params) in enumerate(mutations):
        case = tmp_path / f"contact-chain-mutation-{index}"; case.mkdir()
        path = _spool(case, value); connection = sqlite3.connect(path); connection.execute(sql, params); connection.commit(); connection.close()
        if sql.startswith("INSERT INTO contact_boundary"):
            connection = sqlite3.connect(path); connection.execute("UPDATE contact_boundary SET contact_id='İ' WHERE contact_id='contact:0'"); connection.commit(); connection.close()
        with pytest.raises(SourcePlaneOwnershipIRError) as error: build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), _v2_binding(value))
        if sql.startswith("INSERT INTO contact_boundary"):
            assert error.value.code == "SOURCE_PLANE_OWNERSHIP_IR_ID_COLLISION"

def _spool_with_counts(tmp_path, *, source_count, surface_count):
    value = draft(); value["contact_boundary"] = []; path = _spool(tmp_path, value)
    connection = sqlite3.connect(path)
    connection.executemany("INSERT INTO source_records (ordinal,record_id,kind,source_offset,source_end,source_record_sha256,logical_net,layer) VALUES (?,?,?,?,?,?,?,?)", ((ordinal, f"extra-node-{ordinal:06d}", "Node", 200 + ordinal * 10, 210 + ordinal * 10, h("0"), None, None) for ordinal in range(15, source_count)))
    empty_lineage = sha256(b"[]").hexdigest()
    connection.executemany("INSERT INTO surfaces (ordinal,surface_id,artwork_net,layer,geometry_asset_name,geometry_asset_sha256,island_manifest_sha256,source_lineage_sha256,component_count) VALUES (?,?,?,?,?,?,?,?,?)", ((ordinal, f"surface:extra:{ordinal:06d}", "ART_EXTRA", "L1", f"geometry/extra/{ordinal:06d}", h("8"), h("9"), empty_lineage, 0) for ordinal in range(2, surface_count)))
    connection.commit(); connection.close()
    value["source_size_bytes"] = 2_000_000
    return path, _v2_binding(value)

@pytest.mark.parametrize(("source_count", "surface_count", "expected_error", "expected_total"), ((150_000, 2, False, 150_025), (150_001, 2, True, None), (150_000, 149_977, False, 300_000), (150_000, 149_978, True, None)))
def test_v2_exact_section_and_total_caps(tmp_path, source_count, surface_count, expected_error, expected_total):
    path, binding = _spool_with_counts(tmp_path, source_count=source_count, surface_count=surface_count)
    if expected_error:
        with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), binding)
    else:
        manifest, _ = build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), binding, batch_rows=1000)
        assert manifest["counts"]["source_records"] == source_count and sum(manifest["counts"].values()) == expected_total

def test_spool_temp_artifacts_are_removed_after_cancel_and_exception(tmp_path, monkeypatch):
    value = draft(); value["contact_boundary"] = []; path = _spool(tmp_path, value); binding = _v2_binding(value)
    real_directory = ownership_ir.tempfile.TemporaryDirectory; seen = []
    class TrackingDirectory(real_directory):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs); seen.append(Path(self.name))
    monkeypatch.setattr(ownership_ir.tempfile, "TemporaryDirectory", TrackingDirectory)
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), binding, is_cancelled=lambda: True)
    def fail(*args, **kwargs): raise RuntimeError("injected spool failure")
    monkeypatch.setattr(ownership_ir, "_validate_spooled_rows", fail)
    with pytest.raises(RuntimeError): build_source_plane_ownership_ir_from_spool(_SourcePlaneOwnershipSpool(path), binding)
    assert seen and all(not directory.exists() for directory in seen)

def test_primitive_and_material_provenance_failures():
    for mutate in (lambda d:d["primitives"][0].update(polarity="x"),lambda d:d["primitives"][2].update(effect_status="retained"),lambda d:d["primitives"][1].update(kind="polygon"),lambda d:d["primitives"][0].update(source_record_id="shape-p1"),lambda d:d["dielectric_points"][0].update(epsilon_origin="material_model"),lambda d:d["stackup_layers"][1].update(conductivity_source_record_id="layer-2")):
        d=draft(); mutate(d)
        with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(d)

def test_rail_owner_replacement_failures():
    for key,val in (("role","ground"),("artwork_net","BAD"),("layer","L2")):
        d=draft(); d["plane_owner_scopes"][0][key]=val
        with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(d)
    d=draft(); d["terminal_bindings"][0]["via_owner_id"]="missing"
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(d)
    d=draft(); d["replacement_ledger"][0]["status"]="replacement_ready"
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(d)

def test_manifest_tamper_replay_and_schema():
    m,(name,payload)=build_source_plane_ownership_ir(draft())
    with pytest.raises(SourcePlaneOwnershipIRError): load_source_plane_ownership_ir({**m,"raw_manifest_sha256":h("z")},{name:payload},**{**binds(m),"expected_raw_manifest_sha256":h("z")})
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"db"; path.write_bytes(zlib.decompress(payload)); c=sqlite3.connect(path); c.execute("UPDATE surfaces SET artwork_net='X'"); c.commit(); c.close(); changed=zlib.compress(path.read_bytes(),6)
    cm={**m,"compressed_size_bytes":len(changed),"compressed_sha256":sha256(changed).hexdigest(),"uncompressed_size_bytes":len(zlib.decompress(changed)),"uncompressed_sha256":sha256(zlib.decompress(changed)).hexdigest()}
    with pytest.raises(SourcePlaneOwnershipIRError): load_source_plane_ownership_ir(cm,{name:changed},**binds(m))

def test_cancellation_and_bounded_attachment():
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(draft(),is_cancelled=lambda:True)
    m,(name,payload)=build_source_plane_ownership_ir(draft())
    with pytest.raises(SourcePlaneOwnershipIRError): load_source_plane_ownership_ir(m,{name:payload},is_cancelled=lambda:True,**binds(m))
    assert b"coordinates" not in zlib.decompress(payload)

def test_remaining_contract_regressions():
    cases = []
    d=draft(); d["rail_bindings"][0]["artwork_net"]="BAD"; cases.append(d)
    d=draft(); d["terminal_bindings"][0]["island_id"]="island:g"; cases.append(d)
    d=draft(); d["terminal_bindings"][1]["branch_id"]="branch:p"; d["terminal_bindings"][1]["pin_id"]="P1"; d["terminal_bindings"][1]["role"]="power"; cases.append(d)
    d=draft(); d["terminal_bindings"][0]["status"]="incomplete"; cases.append(d)
    d=draft(); d["stackup_layers"][0]["thickness_um"]=0; cases.append(d)
    d=draft(); d["dielectric_points"][0]["frequency_hz"]=0; cases.append(d)
    d=draft(); d["stackup_layers"][0]["conductivity_origin"]="unavailable"; cases.append(d)
    d=draft(); d["terminal_bindings"][0]["paddef_source_record_id"]="missing"; cases.append(d)
    d=draft(); d["terminal_bindings"][0]["regular_source_record_id"]="layer-1"; cases.append(d)
    d=draft(); d["rail_bindings"][0]["state"]="consumed"; cases.append(d)
    d=draft(); d["replacement_ledger_members"][0]["action"]="retained"; cases.append(d)
    d=draft(); d["replacement_ledger_members"].append({"ledger_id":"ledger:other","owner_id":"ViaA","action":"retained"}); d["replacement_ledger"].append({"ordinal":1,"ledger_id":"ledger:other","replaced_set_sha256":sh(set()),"retained_set_sha256":sh({"viaa"}),"intersection_count":0,"replaced_count":0,"retained_count":1,"status":"prerequisite_only"}); cases.append(d)
    for bad in cases:
        with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(bad)
    d=draft(); d["replacement_ledger_members"] = d["replacement_ledger_members"][:-1]; cases.append(d)
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(d)
    d=draft(); d["islands"].append({"ordinal":2,"island_id":"island:competitor","surface_id":"surface:p","component_id":"comp:other","representative":1,"positive_witness_count":1}); d["surfaces"][0]["component_count"]=2; d["primitive_island_edges"].append({"primitive_id":"p0","island_id":"island:competitor","witness_kind":"positive_area_witness","relation_sha256":h("f")}); d["terminal_bindings"][0]["island_id"]="island:competitor"; d["terminal_bindings"][0]["component_id"]="comp:other"; d["retained_owner_refs"][0]["island_id"]="island:competitor"; cases.append(d)
    with pytest.raises(SourcePlaneOwnershipIRError): build_source_plane_ownership_ir(d)

def test_envelope_bounds_zlib_and_schema_ddl():
    m,(name,payload)=build_source_plane_ownership_ir(draft()); project={"metadata":{"spd_import":{SOURCE_PLANE_OWNERSHIP_IR_METADATA_KEY:m}}}
    assert validate_project_source_plane_ownership_ir_envelope(project,{name:payload})["asset_name"]==name
    for attachments in ({}, {name:"wrong"}, {name:payload[:-1]}, {name:payload, "ownership/sibling":b"x"}):
        with pytest.raises(SourcePlaneOwnershipIRError): validate_project_source_plane_ownership_ir_envelope(project,attachments)
    orphan={"metadata":{"spd_import":{}}}
    with pytest.raises(SourcePlaneOwnershipIRError): validate_project_source_plane_ownership_ir_envelope(orphan,{name:payload})
    over={**m,"counts":{**m["counts"],"source_records":100001}}
    with pytest.raises(SourcePlaneOwnershipIRError): load_source_plane_ownership_ir(over,{name:payload},**binds(m))
    broken={**m,"compressed_size_bytes":3,"compressed_sha256":sha256(b"bad").hexdigest()}
    with pytest.raises(SourcePlaneOwnershipIRError): load_source_plane_ownership_ir(broken,{name:b"bad"},**binds(m))
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"db"; path.write_bytes(zlib.decompress(payload)); c=sqlite3.connect(path); c.execute("PRAGMA writable_schema=ON"); c.execute("UPDATE sqlite_master SET sql=replace(sql,'NOT NULL','NULL') WHERE name='meta'"); c.commit(); c.close(); changed=zlib.compress(path.read_bytes(),6)
    cm={**m,"compressed_size_bytes":len(changed),"compressed_sha256":sha256(changed).hexdigest(),"uncompressed_size_bytes":len(zlib.decompress(changed)),"uncompressed_sha256":sha256(zlib.decompress(changed)).hexdigest()}
    with pytest.raises(SourcePlaneOwnershipIRError): load_source_plane_ownership_ir(cm,{name:changed},**binds(m))
