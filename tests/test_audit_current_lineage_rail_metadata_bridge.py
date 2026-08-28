import importlib.util
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from types import MappingProxyType
from zipfile import ZipFile

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_current_lineage_rail_metadata_bridge.py"
spec = importlib.util.spec_from_file_location("metadata_bridge", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def _fixture():
    pwr_vertex, gnd_vertex = "q-pwr", "q-gnd"
    links = []
    vertex_map, edge_map, owners = {}, {}, {}
    bindings, contacts = [], []
    for i in range(3):
        for role, pin, net, vertex, layer, island, edge in (("power", f"P{i}", "ADC_VDD_180_VQPS_SYS_1_AON/0", pwr_vertex, "L30", "island-p", f"e-p{i}"), ("ground", f"G{i}", "DGND", gnd_vertex, "L29", "island-g", f"e-g{i}")):
            via, node = f"v-{role[0]}{i}", f"n-{role[0]}{i}"
            owner = f"via:{via}"
            vertex_map[(via, node)] = vertex
            edge_map[(via, node)] = edge
            owners[owner] = edge
            if i == 0:
                links.append(SimpleNamespace(link_id=f"surface-link-{i}-{role[0]}", first_node_id=vertex, owner_ids=(f"finite-vertex-surface:{vertex}:island",), count=1, mode="topology_only_ideal", second_node_id=f"island-{role[0]}"))
            bindings.append({"rail_id": module.RAIL_ID, "branch_id": f"B{i}", "role": role, "pin_id": pin})
            contacts.append({"pin_id": pin, "net": net, "status": "complete", "incident_via_id": via, "exposed_quotient_vertex_id": vertex, "first_via_quotient_edge_id": edge})
    project = SimpleNamespace(
        rails=[SimpleNamespace(rail_id=module.RAIL_ID, net="ADC_VDD_180_VQPS_SYS_1_AON/0", pwr_layer="L30", gnd_layer="L29")],
        metadata={"spd_import": {
            "selected_plane_pair_provenance": {"ADC_VDD_180_VQPS_SYS_1_AON/0": {"rail_net": "ADC_VDD_180_VQPS_SYS_1_AON/0", "pwr_layer": "L30", "gnd_layer": "L29", "source_sha256": module.EXPECTED_SOURCE_SHA256, "pwr_artwork_net": "OTHER_POWER1", "gnd_artwork_net": "DGND", "pwr_artwork_sha256": "1" * 64, "gnd_artwork_sha256": "2" * 64, "pwr_artwork_proven": True, "gnd_artwork_proven": True, "source_graph_pair_unresolved": False}},
            "plane_geometries": [{"layer": "L30", "net": "OTHER_POWER1", "asset": "p.bin", "asset_sha256": "a" * 64, "island_ids": ["island-p"]}, {"layer": "L29", "net": "DGND", "asset": "g.bin", "asset_sha256": "b" * 64, "island_ids": ["island-g"]}],
        }},
    )
    port = SimpleNamespace(rail_id=module.RAIL_ID, selected_net="ADC_VDD_180_VQPS_SYS_1_AON/0", reference_net="DGND", positive_node_id=pwr_vertex, negative_node_id=gnd_vertex, positive_pin_ids=("P0", "P1", "P2"), negative_pin_ids=("G0", "G1", "G2"), positive_anchor_node_ids=(pwr_vertex,), negative_anchor_node_ids=(gnd_vertex,))
    cert = {"surface_equivalence_components": [{"vertex_id": "island-p", "component_id": "cp", "representative_island_id": "island-p", "component_evidence_sha256": "c" * 64, "net": "OTHER_POWER1", "layer": "L30", "contact_status": "complete", "island_ids": ["island-p"]}, {"vertex_id": "island-g", "component_id": "cg", "representative_island_id": "island-g", "component_evidence_sha256": "d" * 64, "net": "DGND", "layer": "L29", "contact_status": "complete", "island_ids": ["island-g"]}], "geometry_assets": [{"layer": "L30", "net": "OTHER_POWER1", "asset": "p.bin", "asset_sha256": "a" * 64, "island_ids": ["island-p"]}, {"layer": "L29", "net": "DGND", "asset": "g.bin", "asset_sha256": "b" * 64, "island_ids": ["island-g"]}]}
    topology = SimpleNamespace(rail_ports=(port,), vertex_by_landing_key=vertex_map, first_edge_by_landing_key=edge_map, owner_edge_by_id=owners, topology_links=tuple(links), certificate=cert)
    compiled = SimpleNamespace(topology=topology, external_port_proof_view={"rail_anchor_bindings": bindings, "terminal_contacts": contacts})
    return project, compiled


def test_logical_physical_separation_bridge_passes():
    project, compiled = _fixture()
    evidence = module._bridge_rows(project, compiled)
    assert len(evidence["contact_rows"]) == 6
    assert {row["net"] for row in evidence["contact_rows"] if row["role"] == "power"} == {"ADC_VDD_180_VQPS_SYS_1_AON/0"}
    assert {row["net"] for row in evidence["surface_identities"] if row["role"] == "power"} == {"OTHER_POWER1"}


def test_bridge_rejects_missing_landing():
    project, compiled = _fixture()
    compiled.external_port_proof_view["terminal_contacts"][0]["incident_via_id"] = "missing"
    with pytest.raises(ValueError, match="landing"):
        module._bridge_rows(project, compiled)


def test_bridge_rejects_surface_link_mismatch():
    project, compiled = _fixture()
    compiled.topology.topology_links = tuple(link for link in compiled.topology.topology_links if link.first_node_id != "q-pwr")
    with pytest.raises(ValueError, match="topology link"):
        module._bridge_rows(project, compiled)


def test_bridge_rejects_provenance_mismatch():
    project, compiled = _fixture()
    project.metadata["spd_import"]["selected_plane_pair_provenance"][module.RAIL_ID]["rail_net"] = "OTHER_POWER1"
    with pytest.raises(ValueError, match="provenance"):
        module._bridge_rows(project, compiled)


def test_stamp_sensitivity():
    base = {"schema": module.SCHEMA, "git": {"head": "h", "branch": "main"}, "result": "PASS_METADATA_BRIDGE_ONLY"}
    assert module._stamp({**base, "schema": "other"}) != module._stamp(base)
    assert module._stamp({**base, "git": {"head": "h2", "branch": "main"}}) != module._stamp(base)


def test_mapping_proxy_and_unrelated_contacts_are_ignored():
    project, compiled = _fixture()
    view = compiled.external_port_proof_view
    view["rail_anchor_bindings"] = tuple(MappingProxyType(x) for x in view["rail_anchor_bindings"]) + ({"rail_id": "other", "pin_id": "X"},)
    view["terminal_contacts"] = tuple(MappingProxyType(x) for x in view["terminal_contacts"]) + ({"pin_id": "X", "net": "NOISE"},)
    evidence = module._bridge_rows(project, compiled)
    assert len(evidence["contact_rows"]) == 6


def test_bridge_rejects_ambiguous_landing():
    project, compiled = _fixture()
    key = next(iter(compiled.topology.vertex_by_landing_key))
    compiled.topology.vertex_by_landing_key[(key[0], key[1] + "-duplicate")] = compiled.topology.vertex_by_landing_key[key]
    compiled.topology.first_edge_by_landing_key[(key[0], key[1] + "-duplicate")] = compiled.topology.first_edge_by_landing_key[key]
    with pytest.raises(ValueError, match="landing"):
        module._bridge_rows(project, compiled)


def test_existing_output_fast_guard_does_not_audit(monkeypatch, tmp_path):
    output = tmp_path / "out.json"
    output.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(module, "audit_candidate", lambda *args, **kwargs: pytest.fail("audit must not run"))
    assert module.main(["--candidate", "missing", "--report", "missing", "--output", str(output), "--expected-head", "h"]) == 2


def test_typed_stop_envelope_includes_version(monkeypatch, tmp_path):
    output = tmp_path / "out.json"
    monkeypatch.setattr(module, "audit_candidate", lambda *args, **kwargs: (_ for _ in ()).throw(module.AuditStop("STOP_RESOURCE_OR_CANCELLED", "temporary space")))
    assert module.main(["--candidate", "missing", "--report", "missing", "--output", str(output), "--expected-head", "h"]) == 2
    payload = output.read_text(encoding="utf-8")
    assert module.SCHEMA in payload and module.APP_VERSION in payload and "STOP_RESOURCE_OR_CANCELLED" in payload


def test_zip_tracker_opens_only_manifest_scenario_and_compiled(tmp_path):
    path = tmp_path / "tiny.zip"
    with ZipFile(path, "w") as archive:
        for name in ("manifest.json", "scenario.json", module.EXPECTED_COMPILED_PATH, "raw-spatial.sqlite.zlib"):
            archive.writestr(name, name.encode())
    with ZipFile(path) as raw:
        tracker = module._ZipOpenTracker(raw)
        for name in ("manifest.json", "scenario.json", module.EXPECTED_COMPILED_PATH):
            module._read_archive_member(tracker, name, is_cancelled=None)
        assert [name.casefold() for name in tracker.events] == [
            "manifest.json", "scenario.json", module.EXPECTED_COMPILED_PATH.casefold()
        ]
        assert set(tracker.payloads) == {"manifest.json"}
        assert all("raw-spatial" not in name.casefold() for name in tracker.events)


def _tiny_selected(monkeypatch, tmp_path, *, mutate=None):
    compiled = b"compiled"
    scenario_raw = {"schema_version": module.SCENARIO_SCHEMA_VERSION, "app_version": module.APP_VERSION, "normalized_project": {}, "attachment_names": [module.EXPECTED_COMPILED_NAME, "raw-spatial.sqlite.zlib"], "attachment_hashes": {module.EXPECTED_COMPILED_NAME: sha256(compiled).hexdigest(), "raw-spatial.sqlite.zlib": sha256(b"raw").hexdigest()}}
    scenario_bytes = json.dumps(scenario_raw, separators=(",", ":")).encode()
    fp = "f" * 64
    manifest = {"format": "spd-decap-pi-scenario", "format_version": 1, "scenario_file": "scenario.json", "raw_spd_embedded": False, "schema_version": module.SCENARIO_SCHEMA_VERSION, "app_version": module.APP_VERSION, "scenario_size": len(scenario_bytes), "scenario_sha256": sha256(scenario_bytes).hexdigest(), "design_fingerprint": fp, "attachments": [{"name": module.EXPECTED_COMPILED_NAME, "path": module.EXPECTED_COMPILED_PATH, "size": len(compiled), "sha256": sha256(compiled).hexdigest()}, {"name": "raw-spatial.sqlite.zlib", "path": "attachments/raw-spatial.sqlite.zlib", "size": 3, "sha256": sha256(b"raw").hexdigest()}]}
    if mutate: mutate(manifest, scenario_raw)
    scenario_bytes = json.dumps(scenario_raw, separators=(",", ":")).encode()
    manifest["scenario_size"] = len(scenario_bytes)
    manifest["scenario_sha256"] = sha256(scenario_bytes).hexdigest()
    manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode()
    candidate = tmp_path / "candidate.sppdi"; report = tmp_path / "report.json"
    with ZipFile(candidate, "w") as archive:
        archive.writestr("manifest.json", manifest_bytes); archive.writestr("scenario.json", scenario_bytes); archive.writestr(module.EXPECTED_COMPILED_PATH, compiled); archive.writestr("attachments/raw-spatial.sqlite.zlib", b"raw")
    report.write_bytes(b"report")
    monkeypatch.setattr(module, "EXPECTED_CANDIDATE_SIZE", candidate.stat().st_size); monkeypatch.setattr(module, "EXPECTED_CANDIDATE_SHA256", sha256(candidate.read_bytes()).hexdigest()); monkeypatch.setattr(module, "EXPECTED_REPORT_SIZE", report.stat().st_size); monkeypatch.setattr(module, "EXPECTED_REPORT_SHA256", sha256(report.read_bytes()).hexdigest()); monkeypatch.setattr(module, "EXPECTED_MANIFEST_SIZE", len(manifest_bytes)); monkeypatch.setattr(module, "EXPECTED_SCENARIO_SIZE", len(scenario_bytes)); monkeypatch.setattr(module, "EXPECTED_SCENARIO_SHA256", sha256(scenario_bytes).hexdigest()); monkeypatch.setattr(module, "EXPECTED_COMPILED_SIZE", len(compiled)); monkeypatch.setattr(module, "EXPECTED_COMPILED_SHA256", sha256(compiled).hexdigest())
    class FakeScenario:
        def __init__(self, raw):
            self.attachment_names = raw["attachment_names"]
            self.attachment_hashes = raw["attachment_hashes"]
            self.normalized_project = raw["normalized_project"]
        def _design_fingerprint(self, memo): return fp
    monkeypatch.setattr(module.ScenarioSpec, "model_validate", staticmethod(lambda raw, context=None: FakeScenario(raw)))
    monkeypatch.setattr(module.ProjectSpec, "model_validate", staticmethod(lambda raw: raw))
    return candidate, report


def test_real_read_selected_is_three_ordered_opens_and_no_raw(monkeypatch, tmp_path):
    candidate, report = _tiny_selected(monkeypatch, tmp_path)
    _manifest, _scenario, _attachments, opened = module._read_selected(candidate, report, module.monotonic(), 30)
    assert opened["opened_events"] == ["manifest.json", "scenario.json", module.EXPECTED_COMPILED_PATH]
    assert set(opened["opened_unique_names"]) == set(opened["opened_events"])


@pytest.mark.parametrize("mutation", ["name", "hash", "fingerprint"])
def test_real_read_selected_mutations_stop(monkeypatch, tmp_path, mutation):
    def mutate(manifest, scenario):
        if mutation == "name": scenario["attachment_names"] = ["wrong"]
        elif mutation == "hash": scenario["attachment_hashes"][module.EXPECTED_COMPILED_NAME] = "0" * 64
        else: manifest["design_fingerprint"] = "0" * 64
    candidate, report = _tiny_selected(monkeypatch, tmp_path, mutate=mutate)
    with pytest.raises(module.AuditStop):
        module._read_selected(candidate, report, module.monotonic(), 30)


def test_real_read_selected_rejects_fourth_open(monkeypatch, tmp_path):
    candidate, report = _tiny_selected(monkeypatch, tmp_path)
    original = module._read_archive_member
    def read_then_probe(archive, member, *, is_cancelled):
        result = original(archive, member, is_cancelled=is_cancelled)
        if str(member).endswith(module.EXPECTED_COMPILED_PATH):
            original(archive, "attachments/raw-spatial.sqlite.zlib", is_cancelled=is_cancelled)
        return result
    monkeypatch.setattr(module, "_read_archive_member", read_then_probe)
    with pytest.raises(module.AuditStop):
        module._read_selected(candidate, report, module.monotonic(), 30)


def test_real_read_selected_candidate_report_mutation_stop(monkeypatch, tmp_path):
    candidate, report = _tiny_selected(monkeypatch, tmp_path)
    candidate.write_bytes(candidate.read_bytes() + b"x")
    with pytest.raises(module.AuditStop) as exc:
        module._read_selected(candidate, report, module.monotonic(), 30)
    assert exc.value.code == "STOP_INPUT_IDENTITY"
    candidate, report = _tiny_selected(monkeypatch, tmp_path)
    report.write_bytes(report.read_bytes() + b"x")
    with pytest.raises(module.AuditStop) as exc:
        module._read_selected(candidate, report, module.monotonic(), 30)
    assert exc.value.code == "STOP_INPUT_IDENTITY"


def test_bridge_requires_single_finite_surface_via(tmp_path):
    project, compiled = _fixture()
    compiled.topology.topology_links[0].count = 2
    with pytest.raises(module.AuditStop, match="topology link"):
        module._bridge_rows(project, compiled)


def test_component_partition_and_asset_inventory_must_be_authoritative():
    project, compiled = _fixture()
    compiled.topology.certificate["surface_equivalence_components"][1]["island_ids"] = ["island-p"]
    with pytest.raises(module.AuditStop):
        module._bridge_rows(project, compiled)
    project, compiled = _fixture()
    project.metadata["spd_import"]["plane_geometries"][0]["island_ids"] = ["island-p", "missing"]
    with pytest.raises(module.AuditStop):
        module._bridge_rows(project, compiled)


def test_geometry_asset_net_is_explicit_and_exact():
    project, compiled = _fixture()
    del compiled.topology.certificate["geometry_assets"][0]["net"]
    with pytest.raises(module.AuditStop):
        module._bridge_rows(project, compiled)
    project, compiled = _fixture()
    compiled.topology.certificate["geometry_assets"][0]["net"] = "WRONG"
    with pytest.raises(module.AuditStop):
        module._bridge_rows(project, compiled)


def test_positive_component_partition_subset_passes():
    project, compiled = _fixture()
    p_record = project.metadata["spd_import"]["plane_geometries"][0]
    p_record["island_ids"] = ["island-p", "island-p-extra"]
    p_asset = compiled.topology.certificate["geometry_assets"][0]
    p_asset["island_ids"] = ["island-p", "island-p-extra"]
    compiled.topology.certificate["surface_equivalence_components"].append({"component_id": "cp-extra", "representative_island_id": "island-p-extra", "component_evidence_sha256": "e" * 64, "net": "OTHER_POWER1", "layer": "L30", "contact_status": "complete", "island_ids": ["island-p-extra"]})
    evidence = module._bridge_rows(project, compiled)
    assert evidence["bridge_rows"][0]["component_id"] in {"cp", "cg"}
    
    


def test_surface_link_rejects_unrelated_owner():
    project, compiled = _fixture()
    link = compiled.topology.topology_links[0]
    link.owner_ids = (link.owner_ids[0], "unrelated-owner")
    with pytest.raises(module.AuditStop):
        module._bridge_rows(project, compiled)


def test_anchor_supernode_mismatch_stops():
    project, compiled = _fixture()
    port = compiled.topology.rail_ports[0]
    port.positive_node_id = "external-power-supernode"
    port.negative_node_id = "external-ground-supernode"
    with pytest.raises(module.AuditStop) as exc:
        module._bridge_rows(project, compiled)
    assert exc.value.code == "STOP_CONTRACT_UNSUPPORTED"


def test_bridge_traversal_deadline_stops():
    project, compiled = _fixture()
    with pytest.raises(module.AuditStop) as exc:
        module._bridge_rows(project, compiled, started=module.monotonic() - 1, deadline_s=0)
    assert exc.value.code == "STOP_RESOURCE_OR_CANCELLED"


def test_loader_cancellation_maps_to_resource_stop(monkeypatch):
    project, _compiled = _fixture()
    project.metadata["spd_import"]["source_sha256"] = module.EXPECTED_SOURCE_SHA256
    scenario = SimpleNamespace(source=SimpleNamespace(sha256=module.EXPECTED_SOURCE_SHA256), base_project=project)
    monkeypatch.setattr(module, "_git_state", lambda expected: {"head": expected, "branch": "main", "tracked_clean": True, "allowed_untracked": []})
    monkeypatch.setattr(module, "_read_selected", lambda *args: ({}, scenario, {}, {}))
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=8 * 1024**3))
    monkeypatch.setattr(module, "load_compiled_topology_asset", lambda *args, **kwargs: (_ for _ in ()).throw(module.CompiledTopologyAssetError("COMPILED_TOPOLOGY_CANCELLED", "cancelled")))
    with pytest.raises(module.AuditStop) as exc:
        module.audit_candidate(Path("candidate"), Path("report"), "head")
    assert exc.value.code == "STOP_RESOURCE_OR_CANCELLED"


def _audit_stub(monkeypatch):
    project, compiled = _fixture()
    spd = project.metadata["spd_import"]
    spd["source_sha256"] = module.EXPECTED_SOURCE_SHA256
    spd["layerwise_compiled_topology_asset"] = {"source_sha256": module.EXPECTED_SOURCE_SHA256, "project_binding_sha256": module.EXPECTED_PROJECT_BINDING_SHA256, "certificate_evidence_sha256": module.EXPECTED_CERTIFICATE_SHA256, "topology_identity_sha256": module.EXPECTED_TOPOLOGY_SHA256}
    compiled.topology.source_sha256 = module.EXPECTED_SOURCE_SHA256
    compiled.topology.certificate_evidence_sha256 = module.EXPECTED_CERTIFICATE_SHA256
    compiled.topology.topology_identity_sha256 = module.EXPECTED_TOPOLOGY_SHA256
    scenario = SimpleNamespace(source=SimpleNamespace(sha256=module.EXPECTED_SOURCE_SHA256), base_project=project)
    opened = {"archive_member_count": 4, "opened_events": ["manifest.json", "scenario.json", module.EXPECTED_COMPILED_PATH], "opened_unique_names": ["manifest.json", "scenario.json", module.EXPECTED_COMPILED_PATH], "manifest_size": 1, "manifest_sha256": "a" * 64, "scenario_size": 2, "scenario_sha256": "b" * 64, "design_fingerprint": "c" * 64}
    monkeypatch.setattr(module, "_git_state", lambda expected: {"head": expected, "branch": "main", "tracked_clean": True, "allowed_untracked": []})
    monkeypatch.setattr(module, "_read_selected", lambda *args: ({}, scenario, {module.EXPECTED_COMPILED_NAME: b"compiled"}, opened))
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=8 * 1024**3))
    monkeypatch.setattr(module, "load_compiled_topology_asset", lambda *args, **kwargs: compiled)


def test_audit_candidate_pass_stamp_reconstructs(monkeypatch):
    _audit_stub(monkeypatch)
    result = module.audit_candidate(Path("candidate"), Path("report"), "head")
    assert result["result"] == "PASS_METADATA_BRIDGE_ONLY"
    assert result["evidence_sha256"] == module._stamp(result["stamp_input"])
    assert result["archive_member_count"] == 4
    assert result["design_fingerprint"] == "c" * 64


def test_audit_candidate_loaded_topology_identity_mutation_stops(monkeypatch):
    _audit_stub(monkeypatch)
    original = module.load_compiled_topology_asset
    def mutated(*args, **kwargs):
        compiled = original(*args, **kwargs)
        compiled.topology.topology_identity_sha256 = "0" * 64
        return compiled
    monkeypatch.setattr(module, "load_compiled_topology_asset", mutated)
    with pytest.raises(module.AuditStop) as exc:
        module.audit_candidate(Path("candidate"), Path("report"), "head")
    assert exc.value.code == "STOP_INPUT_IDENTITY"


def test_limitations_are_stamped_and_sensitive():
    base = {"result": "PASS_METADATA_BRIDGE_ONLY", "limitations": module.LIMITATIONS}
    assert module._stamp(base) != module._stamp({**base, "limitations": ["changed"]})
