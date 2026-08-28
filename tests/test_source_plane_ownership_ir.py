from __future__ import annotations
from hashlib import sha256
import json, sqlite3, tempfile, zlib
from pathlib import Path
import pytest
from spd_decap_pi.source_plane_ownership_ir import SourcePlaneOwnershipIRError, build_source_plane_ownership_ir, load_source_plane_ownership_ir, validate_project_source_plane_ownership_ir_envelope, SOURCE_PLANE_OWNERSHIP_IR_METADATA_KEY

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

def test_roundtrip_and_complete_chains():
    m,a=build_source_plane_ownership_ir(draft()); assert m["app_version"]=="0.23.1"
    with load_source_plane_ownership_ir(m,{a[0]:a[1]},expected_app_version="0.23.1",**binds(m)) as db:
        assert len(list(db.iter_section("terminal_bindings")))==2
    assert build_source_plane_ownership_ir(draft())== (m,a)

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
