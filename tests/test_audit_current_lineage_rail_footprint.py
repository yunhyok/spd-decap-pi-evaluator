import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_current_lineage_rail_footprint.py"
spec = importlib.util.spec_from_file_location("audit_current_lineage_rail_footprint", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_owner_bijection_is_deterministic_and_six_rows_can_be_joined():
    rows = tuple({"via_id": f"V{i}", "net_name": "OTHER_POWER1", "owner_id": f"via:other_power1:v{i}"} for i in range(6))
    owners = {f"via:other_power1:v{i}": f"edge-{i}" for i in range(6)}
    joined = module._validate_owner_bijection(rows, owners)
    assert tuple(item["first_edge_id"] for item in joined) == tuple(f"edge-{i}" for i in range(6))


def test_owner_bijection_stops_on_duplicate_owner():
    row = {"via_id": "V1", "net_name": "OTHER_POWER1", "owner_id": "via:other_power1:v1"}
    with pytest.raises(ValueError, match="duplicated"):
        module._validate_owner_bijection((row, row), {"via:other_power1:v1": "edge-1"})


def test_footprint_boundary_and_other_net_overlap_stop():
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import box

    footprint = module._footprint_shape(shape_kind="RECTANGLE", x_um=1.0, y_um=1.0, width_pm=1_000_000, height_pm=1_000_000)
    assert footprint.bounds == pytest.approx((0.5, 0.5, 1.5, 1.5))
    with pytest.raises(ValueError, match="properly contained"):
        module._validate_footprint_containment(footprint, box(0.5, 0.5, 1.5, 1.5))
    with pytest.raises(ValueError, match="overlaps"):
        module._validate_footprint_containment(footprint, box(0, 0, 2, 2), (box(0.9, 0.9, 1.1, 1.1),))


def test_endpoint_join_requires_matching_vertex_and_first_edge():
    via = SimpleNamespace(via_id="v1", owner_id="via:other_power1:v1", start_node_id="n1", end_node_id="n2")
    topology = SimpleNamespace(
        vertex_by_landing_key={("v1", "n1"): "spd-finite-via-vertex:a"},
        first_edge_by_landing_key={("v1", "n1"): "edge-1"},
        owner_edge_by_id={"via:other_power1:v1": "edge-1"},
    )
    assert module._resolve_landing_endpoint(
        topology, via, {"exposed_quotient_vertex_id": "spd-finite-via-vertex:a", "first_via_quotient_edge_id": "edge-1"}
    ) == (("v1", "n1"), "spd-finite-via-vertex:a", "n1")
    with pytest.raises(ValueError, match="lacks exposed"):
        module._resolve_landing_endpoint(
            topology, via, {"exposed_quotient_vertex_id": "not-finite-via:a", "first_via_quotient_edge_id": "edge-1"}
        )


def test_surface_bbox_selects_only_same_layer_competitor():
    digest = "a" * 64
    surfaces = (
        SimpleNamespace(layer_id="L30", net_name="OTHER_POWER1", min_x_pm=0, min_y_pm=0, max_x_pm=2_000_000, max_y_pm=2_000_000, surface_id="p", artwork_asset_sha256=digest, source_record_sha256="s", island_manifest_sha256="i"),
        SimpleNamespace(layer_id="L30", net_name="NOISE", min_x_pm=500_000, min_y_pm=500_000, max_x_pm=1_500_000, max_y_pm=1_500_000, surface_id="n", artwork_asset_sha256=digest, source_record_sha256="s", island_manifest_sha256="i"),
        SimpleNamespace(layer_id="L29", net_name="DGND", min_x_pm=0, min_y_pm=0, max_x_pm=2_000_000, max_y_pm=2_000_000, surface_id="g", artwork_asset_sha256=digest, source_record_sha256="s", island_manifest_sha256="i"),
    )
    raw = SimpleNamespace(iter_surfaces=lambda *, layer_id: (row for row in surfaces if row.layer_id.casefold() == layer_id.casefold()))
    project = SimpleNamespace(metadata={"spd_import": {"plane_geometries": [
        {"layer": "L30", "net": "OTHER_POWER1", "asset_sha256": digest},
        {"layer": "L29", "net": "DGND", "asset_sha256": digest},
        {"layer": "L30", "net": "NOISE", "asset_sha256": digest},
    ]}})
    keys, _ = module._select_surface_keys(raw, project, (("L30", "OTHER_POWER1"), ("L29", "DGND")), (("l30", (0.75, 0.75, 1.25, 1.25)), ("l29", (0.75, 0.75, 1.25, 1.25))))
    assert keys == (("l29", "dgnd"), ("l30", "noise"), ("l30", "other_power1"))


def test_plane_indices_never_decodes_unselected_record(monkeypatch):
    calls = []
    digest = "b" * 64
    records = [
        {"layer": "L30", "net": "OTHER_POWER1", "asset": "p", "asset_sha256": digest, "uncompressed_bytes": 1},
        {"layer": "L30", "net": "NOISE", "asset": "n", "asset_sha256": digest, "uncompressed_bytes": 1},
        {"layer": "L30", "net": "UNSELECTED", "asset": "u", "asset_sha256": digest, "uncompressed_bytes": 1},
    ]
    project = SimpleNamespace(metadata={"spd_import": {"plane_geometries": records}})
    payload = {"layer": "L30", "net": "OTHER_POWER1", "positive_polygons_um": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]], "negative_polygons_um": [], "positive_circles_um": [], "negative_circles_um": [], "primitive_order": [["positive_polygon", 0]]}
    monkeypatch.setattr(module, "spd_plane_geometry_record_payload", lambda record, attachments: (calls.append(record["net"]) or dict(payload, net=record["net"])))
    monkeypatch.setattr(module.IndexedPlaneGeometry, "build", classmethod(lambda cls, geometry: SimpleNamespace(geometry=geometry, _artwork_components=lambda: ([object()], None, None))))
    indices, _ = module._plane_indices(project, {}, (("L30", "OTHER_POWER1"), ("L30", "NOISE")), (("L30", "OTHER_POWER1"),))
    assert set(calls) == {"OTHER_POWER1", "NOISE"}
    assert "UNSELECTED" not in calls
    assert set(indices) == {("l30", "other_power1")}


def test_six_seam_rows_rejects_non_three_pin_expected_sets():
    rail = SimpleNamespace(positive_pin_ids=("P1", "P2"), negative_pin_ids=("G1", "G2", "G3"), positive_anchor_node_ids=(module.EXPECTED_POSITIVE_PORT_VERTEX,), negative_anchor_node_ids=(module.EXPECTED_NEGATIVE_PORT_VERTEX,), positive_node_id=module.EXPECTED_POSITIVE_PORT_VERTEX, negative_node_id=module.EXPECTED_NEGATIVE_PORT_VERTEX)
    bindings = tuple(
        {"rail_id": module.RAIL_ID, "branch_id": f"B{((i - 1) % 3) + 1}", "role": role, "pin_id": pin}
        for i, (role, pin) in enumerate((("power", "P1"), ("power", "P2"), ("power", "P3"), ("ground", "G1"), ("ground", "G2"), ("ground", "G3")), 1)
    )
    contacts = tuple(
        {"pin_id": pin, "status": "complete", "incident_via_id": f"V{i}", "first_via_quotient_edge_id": f"E{i}", "exposed_quotient_vertex_id": f"spd-finite-via-vertex:{i}", "net": module.POWER_NET if role == "power" else module.RETURN_NET, "incident_net": module.POWER_NET if role == "power" else module.RETURN_NET}
        for i, (role, pin) in enumerate((("power", "P1"), ("power", "P2"), ("power", "P3"), ("ground", "G1"), ("ground", "G2"), ("ground", "G3")), 1)
    )
    with pytest.raises(ValueError, match="pin sets"):
        module._six_seam_rows(bindings, contacts, rail)


def _valid_six_fixture():
    pairs = (("power", "P1"), ("power", "P2"), ("power", "P3"), ("ground", "G1"), ("ground", "G2"), ("ground", "G3"))
    rail = SimpleNamespace(positive_pin_ids=("P1", "P2", "P3"), negative_pin_ids=("G1", "G2", "G3"), positive_anchor_node_ids=(module.EXPECTED_POSITIVE_PORT_VERTEX,), negative_anchor_node_ids=(module.EXPECTED_NEGATIVE_PORT_VERTEX,), positive_node_id=module.EXPECTED_POSITIVE_PORT_VERTEX, negative_node_id=module.EXPECTED_NEGATIVE_PORT_VERTEX)
    bindings = tuple({"rail_id": module.RAIL_ID, "branch_id": f"B{((i - 1) % 3) + 1}", "role": role, "pin_id": pin} for i, (role, pin) in enumerate(pairs, 1))
    contacts = tuple({"pin_id": pin, "status": "complete", "incident_via_id": f"V{i}", "first_via_quotient_edge_id": f"E{i}", "exposed_quotient_vertex_id": module.EXPECTED_POSITIVE_PORT_VERTEX if role == "power" else module.EXPECTED_NEGATIVE_PORT_VERTEX, "net": module.POWER_NET if role == "power" else module.RETURN_NET, "incident_net": module.POWER_NET if role == "power" else module.RETURN_NET} for i, (role, pin) in enumerate(pairs, 1))
    return rail, list(bindings), list(contacts)


def test_six_seam_rows_rejects_branch_with_two_same_role_rows():
    rail, bindings, contacts = _valid_six_fixture()
    bindings[2]["branch_id"] = "B1"
    with pytest.raises(ValueError, match="three distinct branches|one PWR"):
        module._six_seam_rows(bindings, contacts, rail)


def test_six_seam_rows_rejects_anchor_identity_mismatch():
    rail, bindings, contacts = _valid_six_fixture()
    rail.positive_anchor_node_ids = ("spd-finite-via-vertex:wrong",)
    with pytest.raises(ValueError, match="anchor identity"):
        module._six_seam_rows(bindings, contacts, rail)


def test_surface_selection_rejects_missing_or_duplicate_project_keys():
    digest = "a" * 64
    surface = SimpleNamespace(layer_id="L30", net_name="OTHER_POWER1", min_x_pm=0, min_y_pm=0, max_x_pm=2_000_000, max_y_pm=2_000_000, surface_id="p", artwork_asset_sha256=digest, source_record_sha256="s", island_manifest_sha256="i")
    raw = SimpleNamespace(iter_surfaces=lambda *, layer_id: iter((surface,)))
    duplicate = SimpleNamespace(metadata={"spd_import": {"plane_geometries": [{"layer": "L30", "net": "OTHER_POWER1", "asset_sha256": digest}, {"layer": "L30", "net": "OTHER_POWER1", "asset_sha256": digest}]}})
    with pytest.raises(ValueError, match="duplicate project"):
        module._select_surface_keys(raw, duplicate, (("L30", "OTHER_POWER1"), ("L29", "DGND")), (("l30", (0.0, 0.0, 1.0, 1.0)),))
    missing = SimpleNamespace(metadata={"spd_import": {"plane_geometries": [{"layer": "L30", "net": "OTHER_POWER1", "asset_sha256": digest}, {"layer": "L29", "net": "DGND", "asset_sha256": digest}]}})
    with pytest.raises(ValueError, match="bijective"):
        module._select_surface_keys(SimpleNamespace(iter_surfaces=lambda *, layer_id: iter(())), missing, (("L30", "OTHER_POWER1"), ("L29", "DGND")), ())


def test_plane_indices_rejects_false_selected_competitor(monkeypatch):
    digest = "b" * 64
    project = SimpleNamespace(metadata={"spd_import": {"plane_geometries": [{"layer": "L30", "net": "OTHER_POWER1", "asset": "p", "asset_sha256": digest, "uncompressed_bytes": 1}, {"layer": "L30", "net": "NOISE", "asset": "n", "asset_sha256": digest, "uncompressed_bytes": 1}]}})
    payload = {"layer": "L30", "net": "OTHER_POWER1", "positive_polygons_um": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]], "primitive_order": [["positive_polygon", 0]]}
    monkeypatch.setattr(module, "spd_plane_geometry_record_payload", lambda record, attachments: dict(payload, net=record["net"]))
    monkeypatch.setattr(module.IndexedPlaneGeometry, "build", classmethod(lambda cls, geometry: SimpleNamespace(geometry=geometry, _artwork_components=lambda: False)))
    with pytest.raises(ValueError, match="competitor artwork"):
        module._plane_indices(project, {}, (("L30", "OTHER_POWER1"), ("L30", "NOISE")), (("L30", "OTHER_POWER1"),))


def test_audit_loaded_raw_synthetic_pass(monkeypatch):
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import box
    pad = SimpleNamespace(shape_kind="RECTANGLE", width_pm=1_000_000, height_pm=1_000_000, source_record_sha256="p")
    prepared = tuple({"item": {"branch_id": f"B{i}", "pin_id": f"P{i}", "role": "power"}, "via": SimpleNamespace(net_name=module.POWER_NET, via_id=f"V{i}", status="EXACT", source_record_sha256="v", padstack_id=f"PS{i}"), "node": SimpleNamespace(node_id=f"N{i}", layer_id="L30", x_pm=1_000_000 + i, y_pm=1_000_000, rotation_microdegrees=0, source_record_sha256="n"), "landing_key": (f"V{i}", f"N{i}"), "vertex": module.EXPECTED_POSITIVE_PORT_VERTEX, "owner": {"owner_id": f"via:other_power1:v{i}", "first_edge_id": f"E{i}"}, "pad": pad, "target_layer": "L30", "footprint": box(0.5 + i * 0.001, 0.5, 1.5 + i * 0.001, 1.5)} for i in range(6))
    indexed = SimpleNamespace(_artwork_components=lambda: ([box(0, 0, 2, 2)], None, None), geometry=SimpleNamespace())
    monkeypatch.setattr(module, "_six_seam_rows", lambda bindings, contacts, rail: ())
    monkeypatch.setattr(module, "_prepare_seam_rows", lambda raw, topology, seam, rail, **kwargs: prepared)
    monkeypatch.setattr(module, "_select_surface_keys", lambda raw, project, pairs, bounds: ((("l30", "other_power1"),), {"targets": {}, "competitors": {}}))
    monkeypatch.setattr(module, "_plane_indices", lambda project, attachments, keys, pairs, **kwargs: ({("l30", "other_power1"): indexed}, {}))
    monkeypatch.setattr(module, "_plane_header_evidence", lambda *args, **kwargs: {"primitive_counts": {"L30|OTHER_POWER1": 1}})
    rail_port = SimpleNamespace(positive_node_id=module.EXPECTED_POSITIVE_PORT_VERTEX, negative_node_id=module.EXPECTED_NEGATIVE_PORT_VERTEX, positive_anchor_node_ids=(module.EXPECTED_POSITIVE_PORT_VERTEX,), negative_anchor_node_ids=(module.EXPECTED_NEGATIVE_PORT_VERTEX,))
    result = module._audit_loaded_raw(SimpleNamespace(), bundle=SimpleNamespace(attachments={}), project=SimpleNamespace(), compiled=SimpleNamespace(external_port_proof_view={}, topology=SimpleNamespace()), rail_port=rail_port, rail=SimpleNamespace(pwr_layer="L30", gnd_layer="L30"), source_sha="s", git={"head": "h", "branch": "main"}, raw_manifest={}, compiled_manifest={}, plane_sheet_sha="p", plane_sheet_counts={}, started=module.monotonic(), deadline_s=10_000.0)
    assert result["result"] == "PASS_LOCAL_SEAM_ONLY"
    assert result["schema"] == module.AUDIT_SCHEMA


def test_evidence_stamp_changes_with_schema_head_and_branch():
    base = {"schema": module.AUDIT_SCHEMA, "result": "PASS_LOCAL_SEAM_ONLY", "app": "SPD Decap PI Evaluator", "version": "v", "git": {"head": "h", "branch": "main", "tracked_clean": True, "allowed_untracked": []}}
    stamp = module._evidence_stamp(base)
    assert module._evidence_stamp({**base, "schema": "other"}) != stamp
    assert module._evidence_stamp({**base, "git": {**base["git"], "head": "other"}}) != stamp
    assert module._evidence_stamp({**base, "git": {**base["git"], "branch": "other"}}) != stamp


def test_plane_header_primitive_order_mismatch_stops():
    digest = "c" * 64
    payload = {"layer": "L30", "net": "OTHER_POWER1", "positive_polygons_um": [[[0.0, 0.0], [2.0, 0.0], [0.0, 2.0]]], "primitive_order": [["positive_polygon", 0]]}
    indexed = module.IndexedPlaneGeometry.build(module._geometry_from_payload(payload))
    raw = SimpleNamespace(iter_plane_primitives=lambda **kwargs: iter((SimpleNamespace(layer_name="L30", net_name="OTHER_POWER1", source_asset_name="asset", source_asset_sha256=digest, primitive_sha256="bad"),)))
    project = SimpleNamespace(metadata={"spd_import": {"plane_geometries": [{"layer": "L30", "net": "OTHER_POWER1", "asset": "asset", "asset_sha256": digest}]}})
    with pytest.raises(ValueError, match="SHA sequence"):
        module._plane_header_evidence(raw, project, {("l30", "other_power1"): indexed}, (("L30", "OTHER_POWER1"),))


def test_main_uses_exclusive_output_creation(monkeypatch, tmp_path):
    output = tmp_path / "report.json"
    output.write_text("existing", encoding="utf-8")
    calls = []
    monkeypatch.setattr(module, "audit_candidate", lambda *args: calls.append(args) or {"schema": module.AUDIT_SCHEMA, "exit_code": 0})
    assert module.main(["--candidate", "c", "--report", "r", "--output", str(output), "--expected-head", "h"]) == 2
    assert calls == []


def test_temp_space_guard_stops_before_candidate_hash(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(module, "_check_temp_space", lambda: (_ for _ in ()).throw(ValueError("low temp")))
    monkeypatch.setattr(module, "_sha256_file", lambda path: calls.append(path))
    with pytest.raises(ValueError, match="low temp"):
        module.audit_candidate(tmp_path / "candidate", tmp_path / "report", "head")
    assert calls == []
