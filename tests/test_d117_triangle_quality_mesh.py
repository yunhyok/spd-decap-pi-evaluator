from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box

from tools.research import d117_triangle_quality_mesh as pilot


SITE = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d117-triangle-w0-research-pilot-01\site")


def _triangle():
    return pilot.load_triangle_site(SITE)


def test_positive_square_mesh_has_closed_3d_certificate_and_deterministic_bytes():
    shape = box(0, 0, 100, 100)
    first = pilot.mesh_polygon(shape, _triangle(), 20.0)
    second = pilot.mesh_polygon(shape, _triangle(), 20.0)
    cert = first["certificate"]
    assert cert["options"] == pilot.TRIANGLE_OPTIONS
    assert cert["edge_incidence_all_two"] and cert["top_bottom_opposite"]
    assert cert["all_faces_triangles"] and cert["finite_vertices_and_faces"] and cert["nonzero_face_areas"] and cert["boundary_marker_chains_complete"]
    assert not cert["duplicate_2d_vertices"] and not cert["duplicate_2d_faces"] and not cert["duplicate_3d_vertices"] and not cert["duplicate_3d_faces"]
    assert cert["signed_volume"] == pytest.approx(shape.area * 20.0)
    assert first["canonical"] == second["canonical"]
    assert cert["canonical_sha256"] == pilot.hashlib.sha256(first["canonical"]).hexdigest()


def test_hole_pslg_has_representative_point_and_genus_certificate():
    shape = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)], [[(40, 40), (60, 40), (60, 60), (40, 60)]])
    pslg, originals = pilot.build_pslg(shape, 20.0)
    assert len(pslg["holes"]) == 1 and Polygon(shape.interiors[0]).contains(Point(pslg["holes"][0]))
    assert len({m for m in pslg["segment_markers"]}) == len(originals)
    mesh = pilot.mesh_polygon(shape, _triangle(), 20.0)
    assert mesh["certificate"]["expected_genus"] == 1


def test_segment_chain_and_source_ceiling_refuse_invalid_data():
    with pytest.raises(pilot.Refusal, match="marker"):
        pilot._check_segment_chains([(0.0, 0.0), (1.0, 0.0)], [], [], [], [((0.0, 0.0), (1.0, 0.0), 1)])
    assert pilot.MAX_SOURCE_VERTICES < 15_297


def test_tampered_identity_and_output_escape_refuse_without_writing(tmp_path):
    outside = tmp_path / "outside"
    rc = pilot.main(["--d115c", str(tmp_path / "tampered.json"), "--d103", "x", "--d104-root", "x", "--triangle-site", str(SITE), "--output", str(outside)])
    assert rc == 2 and not outside.exists()


def test_visible_product_and_version(capsys):
    with pytest.raises(SystemExit):
        pilot.main(["--version"])
    assert f"{pilot.PRODUCT} v{pilot.VERSION}" in capsys.readouterr().out


def test_canonical_signed_zero_and_face_rotation_invariance():
    a = pilot._canonical_mesh([(0.0, 0.0, -0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [(0, 1, 2)])
    b = pilot._canonical_mesh([(-0.0, 1.0, 0.0), (0.0, 0.0, -0.0), (1.0, 0.0, 0.0)], [(2, 0, 1)])
    assert a == b and b"-0" not in a


def test_single_polygon_and_ceiling_gate():
    with pytest.raises(pilot.Refusal, match="single Polygon"):
        pilot.build_pslg(MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]), 20.0)
    assert pilot.MAX_ORIGINAL_VERTICES < 15_297 and pilot.MAX_PSLG_VERTICES < 15_297


def test_nonpositive_thickness_and_final_split_endpoint_refuse_or_exact():
    with pytest.raises(pilot.Refusal):
        pilot.build_pslg(box(0, 0, 10, 10), 0)
    with pytest.raises(pilot.Refusal):
        pilot.build_pslg(box(0, 0, 10, 10), -1)
    vertices, _ = pilot.build_pslg(Polygon([(0.0, 0.0), (100.0, 0.0), (100.0, 1.0)]), 20.0)
    assert (100.0, 0.0) in vertices["vertices"]


def test_duplicate_zero_segment_and_malformed_external_result_refuse():
    with pytest.raises(pilot.Refusal):
        pilot._check_segment_chains([(0.0, 0.0), (1.0, 0.0)], [], [(0, 1), (0, 1)], [1, 1], [((0.0, 0.0), (1.0, 0.0), 1)])
    with pytest.raises(pilot.Refusal, match="boundary segment"):
        pilot._check_segment_chains([(0.0, 0.0), (0.0, 0.0)], [], [(0, 1)], [1], [((0.0, 0.0), (1.0, 0.0), 1)])

    class Malformed:
        def triangulate(self, *_):
            return {"vertices": [[0, 0]], "triangles": [[0, 0, 0]], "segments": [[0, 0]], "segment_markers": [[1]]}

    with pytest.raises(pilot.Refusal):
        pilot.mesh_polygon(box(0, 0, 10, 10), Malformed(), 20.0)

    class Fractional:
        def triangulate(self, *_):
            import numpy as np
            return {"vertices": np.zeros((3, 2)), "triangles": np.array([[0.5, 1, 2]]), "segments": np.array([[0, 1]]), "segment_markers": np.array([[1]])}

    with pytest.raises(pilot.Refusal, match="integral"):
        pilot.mesh_polygon(box(0, 0, 10, 10), Fractional(), 20.0)


def test_translated_prism_records_stable_volume_origin():
    shape = box(1.0e9, 1.0e9, 1.0e9 + 100, 1.0e9 + 100)
    mesh = pilot.mesh_polygon(shape, _triangle(), 20.0)
    cert = mesh["certificate"]
    assert cert["signed_volume"] == pytest.approx(shape.area * 20.0)
    assert "volume_origin_um" in cert


def test_pilot_scope_status_fields_are_distinct():
    assert pilot.EXPECTED_STOP == "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT"
    assert pilot.PRODUCT == "SPD Decap PI Evaluator" and pilot.VERSION == "0.23.1"
    assert pilot.TRIANGLE_OPTIONS == "pq15Cz"


def test_closed_face_cap_is_checked_before_extrusion():
    old = pilot.MAX_MESH_FACES
    pilot.MAX_MESH_FACES = 3
    try:
        with pytest.raises(pilot.Refusal, match="closed-face estimate"):
            pilot.mesh_polygon(box(0, 0, 100, 100), _triangle(), 20.0)
    finally:
        pilot.MAX_MESH_FACES = old


def test_validate_inputs_actual_receipt_schema_fixture(tmp_path, monkeypatch):
    import hashlib, json
    from types import SimpleNamespace
    from shapely import wkb
    source = tmp_path / "cell_0264.wkb"; source.write_bytes(box(0, 0, 10, 10).wkb)
    d104 = tmp_path / "geometry_receipt.json"; d103 = tmp_path / "stackup_material_receipt.json"; d115 = tmp_path / "d115c.json"
    bounds = [0.0, 0.0, 10.0, 10.0]; iwkb = wkb.dumps(box(0, 0, 10, 10).intersection(box(*bounds)))
    def put(path, payload): path.write_text(json.dumps(payload), encoding="utf-8"); return path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()
    ssz, sha = source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest(); isz, iha = len(iwkb), hashlib.sha256(iwkb).hexdigest()
    d104_payload = {"status":"PASS","cells":[{"ordinal":264,"layer":pilot.EXPECTED_LAYER,"net":pilot.EXPECTED_NET,"island_id":pilot.EXPECTED_ISLAND,"geometry":{"filename":"cell_0264.wkb","wkb_size_bytes":ssz,"wkb_sha256":sha}}]}
    d103_payload = {"status":"PASS","stackup_layers":[{"layer_name":pilot.EXPECTED_LAYER,"ordinal":58,"raw_layer_ordinal":58,"layer_kind":"conductor","thickness_origin":"source","thickness_um":20.0}]}
    d104.write_text(json.dumps(d104_payload), encoding="utf-8"); d103.write_text(json.dumps(d103_payload), encoding="utf-8")
    d4s, d4h = d104.stat().st_size, hashlib.sha256(d104.read_bytes()).hexdigest(); d3s, d3h = d103.stat().st_size, hashlib.sha256(d103.read_bytes()).hexdigest()
    d115_payload = {"status":pilot.EXPECTED_STOP,"code":pilot.EXPECTED_STOP,"inputs":{"d103":{"path":str(d103),"size_bytes":d3s,"sha256":d3h},"d104":{"path":str(d104),"size_bytes":d4s,"sha256":d4h},"d104_root":{"path":str(tmp_path)}},"w0":{"snapped_bounds_pm":[0,0,10,10]},"d104_cells":[{"ordinal":264,"layer":pilot.EXPECTED_LAYER,"net":pilot.EXPECTED_NET,"island_id":pilot.EXPECTED_ISLAND,"source_wkb":{"filename":"cell_0264.wkb","size_bytes":ssz,"sha256":sha,"wkb_size_bytes":ssz,"wkb_sha256":sha},"boundary_contact":True,"intersection_bbox_um":bounds,"intersection_wkb_sha256":iha,"intersection_wkb_size_bytes":isz,"intersection_area_um2":100.0,"intersection":{"boundary_contact":True,"nonempty":True,"bbox_um":bounds,"wkb_sha256":iha,"wkb_size_bytes":isz,"area_um2":100.0}}]}
    d115.write_text(json.dumps(d115_payload), encoding="utf-8"); d1s, d1h = d115.stat().st_size, hashlib.sha256(d115.read_bytes()).hexdigest()
    for name, value in (("EXPECTED_D115C_SIZE",d1s),("EXPECTED_D115C_SHA",d1h),("EXPECTED_D103_SIZE",d3s),("EXPECTED_D103_SHA",d3h),("EXPECTED_D104_SIZE",d4s),("EXPECTED_D104_SHA",d4h),("EXPECTED_SOURCE_SIZE",ssz),("EXPECTED_SOURCE_SHA",sha),("EXPECTED_INTERSECTION_SIZE",isz),("EXPECTED_INTERSECTION_SHA",iha),("EXPECTED_BOUNDS",bounds),("EXPECTED_AREA",100.0),("EXPECTED_SNAPPED_PM",[0,0,10,10])): monkeypatch.setattr(pilot,name,value)
    shape, thickness, ident = pilot._validate_inputs(SimpleNamespace(d115c=d115,d103=d103,d104_root=tmp_path))
    assert shape.geom_type == "Polygon" and thickness == 20.0 and ident["source"]["sha256"] == sha and ident["ordinal"] == 264
    original_d115 = d115.read_bytes(); badroot = json.loads(original_d115); badroot["inputs"]["d104_root"] = str(tmp_path); d115.write_text(json.dumps(badroot), encoding="utf-8"); bsz, bh = d115.stat().st_size, hashlib.sha256(d115.read_bytes()).hexdigest(); monkeypatch.setattr(pilot,"EXPECTED_D115C_SIZE",bsz); monkeypatch.setattr(pilot,"EXPECTED_D115C_SHA",bh)
    with pytest.raises(pilot.Refusal, match="linked receipt"):
        pilot._validate_inputs(SimpleNamespace(d115c=d115,d103=d103,d104_root=tmp_path))
    d115.write_bytes(original_d115); monkeypatch.setattr(pilot,"EXPECTED_D115C_SIZE",d1s); monkeypatch.setattr(pilot,"EXPECTED_D115C_SHA",d1h)
    tampered = json.loads(d104.read_text(encoding="utf-8")); tampered["cells"].append(dict(tampered["cells"][0])); d104.write_text(json.dumps(tampered), encoding="utf-8")
    d4s2, d4h2 = d104.stat().st_size, hashlib.sha256(d104.read_bytes()).hexdigest(); monkeypatch.setattr(pilot,"EXPECTED_D104_SIZE",d4s2); monkeypatch.setattr(pilot,"EXPECTED_D104_SHA",d4h2)
    d115j = json.loads(d115.read_text(encoding="utf-8")); d115j["inputs"]["d104"].update(size_bytes=d4s2,sha256=d4h2); d115.write_text(json.dumps(d115j), encoding="utf-8"); d1s2, d1h2 = d115.stat().st_size, hashlib.sha256(d115.read_bytes()).hexdigest(); monkeypatch.setattr(pilot,"EXPECTED_D115C_SIZE",d1s2); monkeypatch.setattr(pilot,"EXPECTED_D115C_SHA",d1h2)
    with pytest.raises(pilot.Refusal, match="D104 source identity"):
        pilot._validate_inputs(SimpleNamespace(d115c=d115,d103=d103,d104_root=tmp_path))


def test_make_payload_structured_scope_runtime_and_provenance():
    runtime = {"implementation":"CPython","python":"3.12.5","platform":"win32","machine":"AMD64","numpy":"2.0","shapely":"2.0","triangle":"20250106"}
    payload = pilot._make_payload({"ordinal":264},{"signed_volume":1.0},runtime)
    assert payload["supply_chain"]["metadata_url"] == pilot.PYPI_METADATA_URL and payload["supply_chain"]["wheel_url"] == pilot.WHEEL_URL
    assert payload["license_boundary"]["wrapper_license_url"] == pilot.WRAPPER_LICENSE_URL and payload["license_boundary"]["upstream_notice_url"] == pilot.UPSTREAM_LICENSE_URL
    assert payload["runtime"] == runtime and payload["ceilings"]["full_domain"] is False and payload["quality_gates"]["options"] == pilot.TRIANGLE_OPTIONS and payload["quality_gates"]["deterministic_canonicalization"] is True
