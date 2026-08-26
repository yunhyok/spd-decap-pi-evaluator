import copy
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path


def _load_audit():
    path = Path(__file__).parents[1] / "scripts" / "audit_terminal_via_vs_spatial.py"
    spec = importlib.util.spec_from_file_location("audit_terminal_via_vs_spatial", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _landing(via_id, net, offset):
    return {
        "x_um": 10.0 + offset, "y_um": 20.0 + offset, "via_id": via_id,
        "net": net, "endpoint_node_id": f"{via_id}-NODE", "padstack": "PS",
        "path_evidence": [{
            "x_um": 10.0 + offset, "y_um": 20.0 + offset,
            "target_layer": "L1" if net == "PWR" else "L3", "target_node_id": f"{via_id}-TARGET",
            "target_padstack": "PS", "target_pad_kind": "PLANE",
            "target_pad_width_um": 80.0, "target_pad_height_um": 80.0,
            "segments": [
                {"via_id": via_id, "padstack": "PS", "drill_diameter_um": 20.0,
                 "start_layer": "L1", "end_layer": "L2", "length_um": 100.0,
                 "end_x_um": 10.0 + offset, "end_y_um": 20.0 + offset, "padstack_material": "COPPER"},
                {"via_id": via_id, "padstack": "PS", "drill_diameter_um": 20.0,
                 "start_layer": "L2", "end_layer": "L3", "length_um": 120.0,
                 "end_x_um": 10.0 + offset, "end_y_um": 20.0 + offset, "padstack_material": "COPPER"},
            ], "trace_hops": 0, "trace_alternate_exit": False,
        }],
    }


def _decap(refdes, rail, index, enabled=True):
    return {
        "refdes": refdes, "center": {"x_um": 100.0 + index, "y_um": 200.0 + index},
        "pwr_pad": {"x_um": 100.0 + index, "y_um": 200.0 + index, "layer": "L1", "padstack": "PS"},
        "gnd_pad": {"x_um": 101.0 + index, "y_um": 200.0 + index, "layer": "L1", "padstack": "PS"},
        "side": "TOP", "footprint": "0402", "source_net": rail,
        "current_net": rail, "source_rail_id": rail, "current_rail_id": rail,
        "source_model_id": "CAP", "model_id": "CAP", "enabled": enabled,
        "pad_state": "NORMAL", "source_mounted": True,
    }


def _write_fixture(root, audit):
    root.mkdir(parents=True, exist_ok=True)
    rails = list(audit.LOADED_RAILS)
    decaps = [_decap(f"C{index}", rail, index) for index, rail in enumerate(rails)]
    # Two repeated-evidence anchors plus one enabled shared dummy exercise the
    # derived component map without adding a second physical Via path.
    decaps[3] = _decap("C3", rails[3], 3)
    decaps.append(_decap("C3A", rails[3], 31, enabled=True))
    decaps.append(_decap("C3D", rails[3], 30, enabled=True))
    decaps.append(_decap("C3X", rails[3], 32, enabled=False))
    connections = {}
    for index, decap in enumerate(decaps):
        if decap["refdes"] in {"C3D", "C3X"}:
            connections[decap["refdes"]] = {
                "refdes": decap["refdes"], "kind": "UNRESOLVED" if decap["refdes"] == "C3D" else "SHARED_DUMMY", "cluster_id": "CL3",
                "power_vias": [], "ground_vias": [],
            }
            continue
        kind = "SHARED_ANCHOR" if decap["refdes"] in {"C3", "C3A"} else "DIRECT"
        shared_vias = kind == "SHARED_ANCHOR"
        connections[decap["refdes"]] = {
            "refdes": decap["refdes"], "kind": kind,
            **({"cluster_id": "CL3"} if kind == "SHARED_ANCHOR" else {}),
            "power_vias": [_landing("V3P", "PWR", 3)] if shared_vias else ([_landing("V0P", "PWR", 0), _landing("V0P2", "PWR", 0.25)] if decap["refdes"] == "C0" else [_landing(f"V{index}P", "PWR", index)]),
            "ground_vias": [_landing("V3G", "GND", 3.5)] if shared_vias else ([_landing("V0G", "GND", 0.5), _landing("V0G2", "GND", 0.75)] if decap["refdes"] == "C0" else [_landing(f"V{index}G", "GND", index + 0.5)]),
        }
    connections["C1"]["power_vias"][0]["path_evidence"] = []
    connections["C2"]["ground_vias"][0]["path_evidence"][0]["trace_hops"] = 1
    cluster = {
        "cluster_id": "CL3", "state": "UNRESOLVED", "reason": "fixture cluster evidence is unresolved", "member_refdes": ["C3", "C3A", "C3D", "C3X"],
        "anchor_refdes": ["C3", "C3A"], "dummy_refdes": ["C3D", "C3X"], "power_net": rails[3],
        "ground_net": "GND", "layer": "L1", "power_edges": [["C3", "C3A"], ["C3A", "C3D"], ["C3D", "C3X"]],
        "ground_edges": [["C3", "C3A"], ["C3A", "C3D"], ["C3D", "C3X"]],
    }
    for refdes in ("C3", "C3A", "C3D", "C3X"):
        connections[refdes]["kind"] = "UNRESOLVED"
        connections[refdes]["reason"] = "fixture connection evidence is unresolved"
    source_sha = audit.EXPECTED_SOURCE["sha256"]
    scenario = {
        "metadata": {"note": 'quoted \\"brace}:colon:'},
        "decaps": decaps,
        "connection_analysis": {
            "version": "DIRECT_TOP_COPPER_PATH_VIA_CHAIN_V5", "source_sha256": source_sha,
            "connections": connections, "clusters": [cluster],
        },
        "normalized_project": {
            "rails": [{"rail_id": rail, "family": rail.split("/")[0], "domain": "VDD",
                        "net": rail, "site": rail.rsplit("/", 1)[1], "pwr_layer": "L1",
                        "gnd_layer": "L3"} for rail in rails],
            "stackup_layers": [
                {"name": "L1", "thickness_um": 35.0, "conductivity_s_m": 58000000.0},
                {"name": "L2", "thickness_um": 100.0, "dk": 4.0, "df": 0.02},
                {"name": "L3", "thickness_um": 35.0, "conductivity_s_m": 58000000.0},
            ],
        },
    }
    scenario_raw = json.dumps(scenario, separators=(",", ":")).encode()
    cap_raw = b".SUBCKT CAP 1 2\nR1 1 2 1\n.ENDS CAP\n"
    cap_hash = hashlib.sha256(cap_raw).hexdigest()
    attachments = [{"name": f"cap_models/CAP-{suffix}.lib", "path": f"attachments/cap_models/CAP-{suffix}.lib", "size": len(cap_raw), "sha256": cap_hash} for suffix in ("aaaa", "bbbb", "cccc")]
    manifest = {"format": "spd-decap-pi-scenario", "format_version": 1, "schema_version": "0.1", "app_version": "0.23.0", "raw_spd_embedded": False, "scenario_file": "scenario.json", "scenario_size": len(scenario_raw), "scenario_sha256": hashlib.sha256(scenario_raw).hexdigest(), "attachments": attachments}
    candidate = root / audit.EXPECTED_CANDIDATE["basename"]
    with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
        archive.writestr("scenario.json", scenario_raw)
        for entry in attachments:
            archive.writestr(entry["path"], cap_raw)
    candidate_bytes = candidate.read_bytes()
    candidate_identity = {"basename": candidate.name, "size_bytes": len(candidate_bytes), "sha256": hashlib.sha256(candidate_bytes).hexdigest()}
    source = {"basename": "source.spd", "size_bytes": 1, "sha256": audit.EXPECTED_SOURCE["sha256"]}
    import_report = root / audit.EXPECTED_IMPORT["basename"]
    import_value = {"schema_version": "candidate-import-save-validation-v1", "report_version": 1, "app_version": "0.23.0", "mode": "import_save_only", "status": "passed", "frequency_solves_executed": 0, "touchstone_read": False, "source": source, "candidate_bundle": {**candidate_identity, "reuse_requested": False}, "import": {"mode": "fresh_import"}, "atomic_save_load_validation": {key: True for key in ("archive_manifest_and_member_hashes_validated", "scenario_schema_validated_after_reload", "source_identity_validated_after_reload", "attachment_hashes_validated_after_reload")}, "candidate_import_report_binding": {"status": "validated_same_process_fresh_import", "fresh_import_mode": True}}
    import_report.write_text(json.dumps(import_value, separators=(",", ":")), encoding="utf-8")
    import_identity = {"basename": import_report.name, "sha256": hashlib.sha256(import_report.read_bytes()).hexdigest()}
    correlation = root / audit.EXPECTED_CORRELATION["basename"]
    correlation_value = {"source": source, "candidate_bundle": candidate_identity, "candidate_import_report_binding": {"status": "validated", "basename": import_identity["basename"], "sha256": import_identity["sha256"], "fresh_import_mode": True}}
    correlation.write_text(json.dumps(correlation_value, separators=(",", ":")), encoding="utf-8")
    previous = root / audit.EXPECTED_PREVIOUS_W7["basename"]
    previous_raw = json.dumps({"selected_cap_count": 8, "diagnostic_failures": ["cap-only first-peak bin mismatch"] * 6, "cap_only": {}, "site_pair_deltas_db": {}, "rails": {}}, separators=(",", ":")).encode()
    previous.write_bytes(previous_raw)
    audit.EXPECTED_CANDIDATE = candidate_identity
    audit.EXPECTED_IMPORT = import_identity
    audit.EXPECTED_CORRELATION = {"basename": correlation.name, "sha256": hashlib.sha256(correlation.read_bytes()).hexdigest()}
    audit.EXPECTED_SOURCE = source
    audit._MOUNTED.EXPECTED_SOURCE = source
    audit.EXPECTED_PREVIOUS_W7 = {"basename": previous.name, "size_bytes": len(previous_raw), "sha256": hashlib.sha256(previous_raw).hexdigest()}
    audit.EXPECTED_SELECTED_COUNT = 8
    audit.EXPECTED_PREVIOUS_SELECTED_COUNT = 8
    return candidate, import_report, correlation, previous, scenario


def _rewrite_candidate(candidate, scenario, audit, import_report, correlation):
    raw = json.dumps(scenario, separators=(",", ":")).encode()
    with zipfile.ZipFile(candidate) as source:
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
    manifest = json.loads(members["manifest.json"].decode())
    manifest["scenario_size"] = len(raw)
    manifest["scenario_sha256"] = hashlib.sha256(raw).hexdigest()
    members["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in members.items():
            target.writestr(name, raw if name == "scenario.json" else content)
    data = candidate.read_bytes()
    audit.EXPECTED_CANDIDATE.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    imported = json.loads(import_report.read_text(encoding="utf-8"))
    imported["candidate_bundle"] = {**audit.EXPECTED_CANDIDATE, "reuse_requested": False}
    import_report.write_text(json.dumps(imported, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_IMPORT["sha256"] = hashlib.sha256(import_report.read_bytes()).hexdigest()
    bound = json.loads(correlation.read_text(encoding="utf-8"))
    bound["candidate_bundle"] = audit.EXPECTED_CANDIDATE.copy()
    bound["candidate_import_report_binding"]["sha256"] = audit.EXPECTED_IMPORT["sha256"]
    correlation.write_text(json.dumps(bound, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_CORRELATION["sha256"] = hashlib.sha256(correlation.read_bytes()).hexdigest()


def _rewrite_candidate_raw(candidate, raw, audit, import_report, correlation):
    with zipfile.ZipFile(candidate) as source:
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
    manifest = json.loads(members["manifest.json"].decode())
    manifest["scenario_size"] = len(raw)
    manifest["scenario_sha256"] = hashlib.sha256(raw).hexdigest()
    members["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in members.items():
            target.writestr(name, raw if name == "scenario.json" else content)
    data = candidate.read_bytes()
    audit.EXPECTED_CANDIDATE.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    imported = json.loads(import_report.read_text(encoding="utf-8"))
    imported["candidate_bundle"] = {**audit.EXPECTED_CANDIDATE, "reuse_requested": False}
    import_report.write_text(json.dumps(imported, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_IMPORT["sha256"] = hashlib.sha256(import_report.read_bytes()).hexdigest()
    bound = json.loads(correlation.read_text(encoding="utf-8"))
    bound["candidate_bundle"] = audit.EXPECTED_CANDIDATE.copy()
    bound["candidate_import_report_binding"]["sha256"] = audit.EXPECTED_IMPORT["sha256"]
    correlation.write_text(json.dumps(bound, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_CORRELATION["sha256"] = hashlib.sha256(correlation.read_bytes()).hexdigest()


def test_terminal_via_vs_spatial_contract_table(tmp_path, capsys):
    audit = _load_audit()
    audit._git_identity = lambda expected: {"branch": "main", "clean": True, "expected": expected, "observed": expected}
    candidate, import_report, correlation, previous, scenario = _write_fixture(tmp_path / "valid", audit)
    expected_head = "a" * 40
    output = tmp_path / "valid.json"
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--previous-w7-audit", str(previous), "--output", str(output), "--expected-head", expected_head]) == 2
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["schema"] == "powersi-terminal-via-vs-spatial-audit-v2"
    assert result["status"] == "diagnostic_complete"
    assert result["selected_investigation_block"] is None
    assert result["audited_scope"] == "terminal_landing_spatial_core"
    assert result["terminal_via_numeric_accuracy"] == "N/A"
    assert result["causal_owner"] is None and result["owner_status"] == "unclassified"
    assert result["physics_authorized"] is False and result["exit_code"] == 2
    assert set(result["diagnostic_failures"]) == {"independent Via signature unavailable", "pad/antipad/current-spreading numeric evidence unavailable"}
    assert result["terminal_via_inventory"]["selected_loaded_decap_count"] == 8
    first = result["terminal_via_inventory"]["per_rail"][audit.LOADED_RAILS[0]]
    assert len(first["pwr_vias"]) == 2 and len(first["gnd_vias"]) == 2
    assert first["segments"] == 8 and sum(row["count"] for row in first["segment_classifications"]) == 8
    assert result["terminal_via_inventory"]["coverage_summary"] == {"available": 12, "missing": 1, "trace_NA": 1, "total_inventoried_terminal_vias": 14, "available_numeric_vias": 12, "state_classification_complete": True, "coverage_complete": False}
    missing = result["terminal_via_inventory"]["per_rail"][audit.LOADED_RAILS[1]]
    traced = result["terminal_via_inventory"]["per_rail"][audit.LOADED_RAILS[2]]
    assert missing["path_coverage"]["PWR"]["missing"] == 1 and missing["segments"] == 2 and len(missing["segment_classifications"]) == 2
    assert traced["path_coverage"]["GND"]["trace_NA"] == 1 and traced["segments"] == 2 and len(traced["segment_classifications"]) == 2
    assert all(isinstance(rail["path_coverage"][net]["state_evidence_sha256"], str) and len(rail["path_coverage"][net]["state_evidence_sha256"]) == 64 and rail["path_coverage"][net]["state_evidence_count"] == sum(rail["path_coverage"][net][status] for status in ("available", "missing", "trace_NA")) == len(rail["pwr_vias"] if net == "PWR" else rail["gnd_vias"]) for rail in result["terminal_via_inventory"]["per_rail"].values() for net in ("PWR", "GND"))
    shared = result["terminal_via_inventory"]["per_rail"][audit.LOADED_RAILS[3]]
    assert len(shared["pwr_vias"]) == 1 and len(shared["gnd_vias"]) == 1
    assert shared["segments"] == 4 and len(shared["segment_classifications"]) == 4
    assert sum(row["count"] for row in shared["segment_classifications"]) == 4
    assert all(row["padstack_material"] == "COPPER" and row["resistance_ohm"] > 0 and row["inductance_h"] > 0 for row in shared["segment_classifications"])
    sentinel = tmp_path / "pre-existing.json"
    sentinel.write_text("sentinel", encoding="utf-8")
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--previous-w7-audit", str(previous), "--output", str(sentinel), "--expected-head", expected_head]) == 2
    assert sentinel.read_text(encoding="utf-8") == "sentinel"
    cases = []
    broken = json.loads(json.dumps(scenario))
    broken["decaps"][0]["refdes"] = broken["decaps"][1]["refdes"]
    cases.append(("duplicate-refdes", broken))
    broken = json.loads(json.dumps(scenario))
    broken["connection_analysis"]["connections"]["C0"]["power_vias"][0]["via_id"] = "V1P"
    cases.append(("conflicting-via", broken))
    broken = json.loads(json.dumps(scenario))
    broken["connection_analysis"]["connections"]["C0"]["ground_vias"][0]["via_id"] = "V0P"
    cases.append(("via-terminal-crossover", broken))
    broken = json.loads(json.dumps(scenario))
    broken.pop("connection_analysis")
    cases.append(("missing-target-fragment", broken))
    broken = json.loads(json.dumps(scenario))
    broken["connection_analysis"]["connections"]["C0"]["power_vias"][0]["path_evidence"].append(broken["connection_analysis"]["connections"]["C0"]["power_vias"][0]["path_evidence"][0])
    cases.append(("duplicate-target-fragment", broken))
    broken = json.loads(json.dumps(scenario))
    broken["connection_analysis"] = []
    cases.append(("wrong-fragment-type", broken))
    broken = json.loads(json.dumps(scenario))
    broken["normalized_project"]["rails"].append(copy.deepcopy(broken["normalized_project"]["rails"][0]))
    cases.append(("duplicate-normalized-rail", broken))
    for name, mutation in cases:
        case_root = tmp_path / name
        case_root.mkdir()
        case_candidate, case_import, case_corr, case_previous, _ = _write_fixture(case_root, audit)
        _rewrite_candidate(case_candidate, mutation, audit, case_import, case_corr)
        out = case_root / "audit.json"
        assert audit.main(["--candidate", str(case_candidate), "--import-report", str(case_import), "--correlation-report", str(case_corr), "--previous-w7-audit", str(case_previous), "--output", str(out), "--expected-head", expected_head]) == 2
        assert not out.exists()

    for kind, reason, clear_vias in (("OUT_OF_SCOPE", "standalone out-of-scope", False), ("FLOATING_DUMMY", "standalone floating dummy", True)):
        case_root = tmp_path / f"non-actionable-{kind.lower()}"
        case_root.mkdir()
        case_candidate, case_import, case_corr, case_previous, case_scenario = _write_fixture(case_root, audit)
        connection = case_scenario["connection_analysis"]["connections"]["C0"]
        connection["kind"] = kind
        connection["reason"] = reason
        connection.pop("cluster_id", None)
        if clear_vias:
            connection["power_vias"] = []
            connection["ground_vias"] = []
        _rewrite_candidate(case_candidate, case_scenario, audit, case_import, case_corr)
        out = case_root / "audit.json"
        capsys.readouterr()
        assert audit.main(["--candidate", str(case_candidate), "--import-report", str(case_import), "--correlation-report", str(case_corr), "--previous-w7-audit", str(case_previous), "--output", str(out), "--expected-head", expected_head]) == 2
        assert capsys.readouterr().err == "integrity failure: selected decap connection is not actionable\n"
        assert not out.exists()

    raw_root = tmp_path / "duplicate-target-scalar"
    raw_root.mkdir()
    raw_candidate, raw_import, raw_corr, raw_previous, raw_scenario = _write_fixture(raw_root, audit)
    raw = json.dumps(raw_scenario, separators=(",", ":")).encode().replace(b'"decaps":[', b'"decaps":null,"decaps":[', 1)
    _rewrite_candidate_raw(raw_candidate, raw, audit, raw_import, raw_corr)
    raw_out = raw_root / "audit.json"
    assert audit.main(["--candidate", str(raw_candidate), "--import-report", str(raw_import), "--correlation-report", str(raw_corr), "--previous-w7-audit", str(raw_previous), "--output", str(raw_out), "--expected-head", expected_head]) == 2
    assert not raw_out.exists()

    gateway_root = tmp_path / "duplicate-normalized-project-key"
    gateway_root.mkdir()
    gateway_candidate, gateway_import, gateway_corr, gateway_previous, gateway_scenario = _write_fixture(gateway_root, audit)
    gateway_raw = json.dumps(gateway_scenario, separators=(",", ":")).encode().replace(b'"normalized_project":{', b'"normalized_project":{},"normalized_project":{', 1)
    _rewrite_candidate_raw(gateway_candidate, gateway_raw, audit, gateway_import, gateway_corr)
    gateway_out = gateway_root / "audit.json"
    assert audit.main(["--candidate", str(gateway_candidate), "--import-report", str(gateway_import), "--correlation-report", str(gateway_corr), "--previous-w7-audit", str(gateway_previous), "--output", str(gateway_out), "--expected-head", expected_head]) == 2
    assert not gateway_out.exists()

    limit_root = tmp_path / "fragment-limit"
    limit_root.mkdir()
    limit_candidate, limit_import, limit_corr, limit_previous, _ = _write_fixture(limit_root, audit)
    limit_out = limit_root / "audit.json"
    old_limit = audit._FRAGMENT_LIMITS[("decaps",)]
    audit._FRAGMENT_LIMITS[("decaps",)] = 1
    try:
        assert audit.main(["--candidate", str(limit_candidate), "--import-report", str(limit_import), "--correlation-report", str(limit_corr), "--previous-w7-audit", str(limit_previous), "--output", str(limit_out), "--expected-head", expected_head]) == 2
        assert not limit_out.exists()
    finally:
        audit._FRAGMENT_LIMITS[("decaps",)] = old_limit
