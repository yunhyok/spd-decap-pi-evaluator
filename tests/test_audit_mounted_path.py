import copy
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import numpy as np


def _load_audit():
    path = Path(__file__).parents[1] / "scripts" / "audit_mounted_path.py"
    spec = importlib.util.spec_from_file_location("audit_mounted_path", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeCap:
    def impedance(self, frequencies):
        grid = np.asarray(frequencies, dtype=float)
        phase = np.log10(grid)
        real = 1.0 + 0.5 * np.exp(-((phase - 6.5) / 0.08) ** 2)
        imag = phase - 7.0
        return real + 1j * imag


def _write_fixture(tmp_path, audit):
    tmp_path.mkdir(parents=True, exist_ok=True)
    rails = tuple(audit.LOADED_RAILS)
    counts = {"ADC_VDD_055_VTRIP": 2, "ADC_VDD_070_VINT": 3, "ADC_VDD_075_VCPU": 4}
    model_ids = ("CAP", "CAP2", "CAP3")
    decaps = []
    for rail in rails:
        for index in range(counts[rail.rsplit("/", 1)[0]]):
            decaps.append({"current_rail_id": rail, "source_rail_id": rail, "current_net": rail, "source_net": rail, "source_mounted": True, "pad_state": "NORMAL", "enabled": True, "model_id": model_ids[index % len(model_ids)]})
    scenario = json.dumps({"metadata": {"decaps": [{"current_rail_id": "NOT_SELECTED"}]}, "decaps": decaps}, separators=(",", ":")).encode()
    cap_text = b".SUBCKT CAP 1 2\nR1 1 2 1\n.ENDS CAP\n"
    cap_hash = hashlib.sha256(cap_text).hexdigest()
    manifest = {"format": "spd-decap-pi-scenario", "format_version": 1, "schema_version": "0.1", "app_version": "0.23.0", "raw_spd_embedded": False, "scenario_file": "scenario.json", "scenario_size": len(scenario), "scenario_sha256": hashlib.sha256(scenario).hexdigest(), "attachments": [
        {"name": "cap_models/CAP-aaaa.lib", "path": "attachments/cap_models/CAP-aaaa.lib", "size": len(cap_text), "sha256": cap_hash},
        {"name": "cap_models/CAP2-bbbb.lib", "path": "attachments/cap_models/CAP2-bbbb.lib", "size": len(cap_text), "sha256": cap_hash},
        {"name": "cap_models/CAP3-cccc.lib", "path": "attachments/cap_models/CAP3-cccc.lib", "size": len(cap_text), "sha256": cap_hash},
    ]}
    candidate = tmp_path / audit.EXPECTED_CANDIDATE["basename"]
    with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
        archive.writestr("scenario.json", scenario)
        archive.writestr("attachments/cap_models/CAP-aaaa.lib", cap_text)
        archive.writestr("attachments/cap_models/CAP2-bbbb.lib", cap_text)
        archive.writestr("attachments/cap_models/CAP3-cccc.lib", cap_text)
    candidate_bytes = candidate.read_bytes()
    audit.EXPECTED_CANDIDATE = {"basename": candidate.name, "size_bytes": len(candidate_bytes), "sha256": hashlib.sha256(candidate_bytes).hexdigest()}
    source = {"basename": "source.spd", "size_bytes": 1, "sha256": "a" * 64}
    import_report = tmp_path / audit.EXPECTED_IMPORT["basename"]
    import_report.write_text(json.dumps({
        "schema_version": "candidate-import-save-validation-v1", "report_version": 1, "app_version": "0.23.0", "mode": "import_save_only", "status": "passed", "frequency_solves_executed": 0, "touchstone_read": False,
        "source": source,
        "candidate_bundle": {**audit.EXPECTED_CANDIDATE, "reuse_requested": False},
        "import": {"mode": "fresh_import"},
        "atomic_save_load_validation": {"archive_manifest_and_member_hashes_validated": True, "scenario_schema_validated_after_reload": True, "source_identity_validated_after_reload": True, "attachment_hashes_validated_after_reload": True},
        "candidate_import_report_binding": {"status": "validated_same_process_fresh_import", "fresh_import_mode": True},
    }, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_IMPORT = {"basename": import_report.name, "sha256": hashlib.sha256(import_report.read_bytes()).hexdigest()}
    audit.EXPECTED_SOURCE = source
    report = {
        "source": source,
        "candidate_bundle": audit.EXPECTED_CANDIDATE.copy(),
        "candidate_import_report_binding": {"status": "validated", "basename": import_report.name, "sha256": audit.EXPECTED_IMPORT["sha256"], "fresh_import_mode": True},
        "runs": {"candidate": {"10": {"rails": {}}}},
    }
    for rail in rails:
        report["runs"]["candidate"]["10"]["rails"][rail] = {
            "status": "completed",
            "solver_provenance": {
                "scenario_raw_via_owner_exact_once": True,
                "scenario_retarget_route_count": 0,
                "scenario_suppressed_base_cut_count": 0,
                "terminal_surface_contact_proof_status": "proven",
                "terminal_artwork_proof_status": "proven",
                "source_trace_width_available": False,
                "finite_trace_rl_link_count": 0,
                "finite_via_rl_link_count": 1,
                "scenario_cap_body_count": 2,
                "termination_active_selected_rail_cluster_count": {"ADC_VDD_055_VTRIP": 2, "ADC_VDD_070_VINT": 3, "ADC_VDD_075_VCPU": 4}[rail.rsplit("/", 1)[0]],
                "spatial_scope": "secondary-layer lateral spreading; via mutual; pad/antipad exact-core",
                "scenario_plan_sha256": "b" * 64,
                "termination_manifest_sha256": "c" * 64,
            },
            "metrics": {
                "anchors": {
                    "0.1MHz": {"model_magnitude_ohm": 1.01 if rail.endswith("/0") else 1.0, "reference_magnitude_ohm": 1.2 if rail.endswith("/0") else 1.0, "signed_magnitude_error_db": -0.1},
                    "1MHz": {"model_magnitude_ohm": 1.01 if rail.endswith("/0") else 1.0, "reference_magnitude_ohm": 1.2 if rail.endswith("/0") else 1.0, "signed_magnitude_error_db": -0.2},
                    "10MHz": {"model_magnitude_ohm": 1.01 if rail.endswith("/0") else 1.0, "reference_magnitude_ohm": 1.2 if rail.endswith("/0") else 1.0, "signed_magnitude_error_db": -0.4},
                    "100MHz": {"model_magnitude_ohm": 1.01 if rail.endswith("/0") else 1.0, "reference_magnitude_ohm": 1.2 if rail.endswith("/0") else 1.0, "signed_magnitude_error_db": -0.3},
                },
                "resonances": {
                    "model_local_peaks": [{"frequency_hz": 3.16e6, "magnitude_ohm": 1.0}],
                    "reference_local_peaks": [{"frequency_hz": 2.5e6, "magnitude_ohm": 1.0}],
                    "model_imaginary_zero_crossings_hz": [1.0e7],
                    "reference_imaginary_zero_crossings_hz": [1.0e6],
                },
            },
        }
    correlation = tmp_path / audit.EXPECTED_CORRELATION["basename"]
    correlation.write_text(json.dumps(report, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_CORRELATION = {"basename": correlation.name, "sha256": hashlib.sha256(correlation.read_bytes()).hexdigest()}
    audit.parse_passive_subcircuit = lambda *args, **kwargs: _FakeCap()
    return candidate, import_report, correlation, report


def _rewrite_report(path, audit, report):
    path.write_text(json.dumps(report, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_CORRELATION["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_candidate(path, audit, decaps):
    with zipfile.ZipFile(path, "r") as source:
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
    scenario = json.dumps({"decaps": decaps}, separators=(",", ":")).encode()
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    manifest["scenario_size"] = len(scenario)
    manifest["scenario_sha256"] = hashlib.sha256(scenario).hexdigest()
    members["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in members.items():
            target.writestr(name, scenario if name == "scenario.json" else content)
    data = path.read_bytes()
    audit.EXPECTED_CANDIDATE.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def _break_pair_rms(value, audit):
    anchors = value["runs"]["candidate"]["10"]["rails"]["ADC_VDD_055_VTRIP/0"]["metrics"]["anchors"]
    for row in anchors.values():
        row["model_magnitude_ohm"] = 10.0


def _rebind_candidate(audit, candidate, import_report, correlation, report):
    import_value = json.loads(import_report.read_text(encoding="utf-8"))
    import_value["candidate_bundle"] = {**audit.EXPECTED_CANDIDATE, "reuse_requested": False}
    import_report.write_text(json.dumps(import_value, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_IMPORT["sha256"] = hashlib.sha256(import_report.read_bytes()).hexdigest()
    report["candidate_bundle"] = audit.EXPECTED_CANDIDATE.copy()
    report["candidate_import_report_binding"]["sha256"] = audit.EXPECTED_IMPORT["sha256"]
    _rewrite_report(correlation, audit, report)


def _run_valid_and_cap_mix(tmp_path):
    audit = _load_audit()
    candidate, import_report, correlation, _ = _write_fixture(tmp_path / "valid", audit)
    output = tmp_path / "valid.json"
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--output", str(output)]) == 2
    valid = json.loads(output.read_text(encoding="utf-8"))
    assert valid["status"] == "diagnostic_pass"
    assert valid["selected_investigation_block"] == "terminal_landing_spatial_core"
    assert valid["causal_owner"] is None and valid["owner_status"] == "unclassified"
    assert valid["exclusive_causality_unproven"] is True
    assert sum(valid["active_loaded_decap_counts"].values()) == 18
    representative = valid["rails"][audit.LOADED_RAILS[0]]["mounted_path_provenance"]
    assert representative["scenario_raw_via_owner_exact_once"] is True
    assert representative["finite_via_rl_link_count"] == 1
    assert "secondary-layer lateral spreading" in representative["spatial_scope"]
    audit = _load_audit()
    candidate, import_report, correlation, report = _write_fixture(tmp_path / "cap-mix", audit)
    decaps = []
    counts = {"ADC_VDD_055_VTRIP": 2, "ADC_VDD_070_VINT": 3, "ADC_VDD_075_VCPU": 4}
    for rail in audit.LOADED_RAILS:
        for index in range(counts[rail.rsplit("/", 1)[0]]):
            decaps.append({"current_rail_id": rail, "source_rail_id": rail, "current_net": rail, "source_net": rail, "source_mounted": True, "pad_state": "NORMAL", "enabled": True, "model_id": ("CAP", "CAP2", "CAP3")[index % 3]})
    next(item for item in decaps if item["current_rail_id"] == "ADC_VDD_055_VTRIP/1" and item["model_id"] == "CAP2")["model_id"] = "CAP3"
    _rewrite_candidate(candidate, audit, decaps)
    _rebind_candidate(audit, candidate, import_report, correlation, report)
    output = tmp_path / "cap-mix.json"
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--output", str(output)]) == 2
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["selected_investigation_block"] is None
    assert any("cap mix differs" in failure for failure in result["diagnostic_failures"])
    assert result["owner_status"] == "unclassified"


def test_mounted_path_audit_contract_table(tmp_path):
    _run_valid_and_cap_mix(tmp_path)
    audit = _load_audit()
    candidate, import_report, correlation, report = _write_fixture(tmp_path, audit)

    # Whole-candidate tamper is global integrity failure and cannot publish JSON.
    candidate.write_bytes(candidate.read_bytes() + b"tamper")
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--output", str(tmp_path / "candidate-tamper.json")]) == 2
    assert not (tmp_path / "candidate-tamper.json").exists()
    candidate, import_report, correlation, report = _write_fixture(tmp_path / "global", audit)
    with zipfile.ZipFile(candidate, "r") as source:
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
    members["scenario.json"] = members["scenario.json"] + b" "
    with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in members.items():
            target.writestr(name, content)
    data = candidate.read_bytes()
    audit.EXPECTED_CANDIDATE.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    _rebind_candidate(audit, candidate, import_report, correlation, report)
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--output", str(tmp_path / "scenario-tamper.json")]) == 2
    assert not (tmp_path / "scenario-tamper.json").exists()
    import_value = json.loads(import_report.read_text(encoding="utf-8"))
    import_value["atomic_save_load_validation"]["attachment_hashes_validated_after_reload"] = False
    import_report.write_text(json.dumps(import_value, separators=(",", ":")), encoding="utf-8")
    audit.EXPECTED_IMPORT["sha256"] = hashlib.sha256(import_report.read_bytes()).hexdigest()
    report["candidate_import_report_binding"]["sha256"] = audit.EXPECTED_IMPORT["sha256"]
    _rewrite_report(correlation, audit, report)
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--output", str(tmp_path / "import-tamper.json")]) == 2
    assert not (tmp_path / "import-tamper.json").exists()
    candidate, import_report, correlation, report = _write_fixture(tmp_path / "member", audit)

    # A manifest/member hash tamper remains a global integrity failure even when
    # the outer candidate identity is updated to the tampered archive.
    with zipfile.ZipFile(candidate, "r") as source:
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
    tampered_manifest = json.loads(members["manifest.json"].decode("utf-8"))
    tampered_manifest["attachments"][0]["sha256"] = "0" * 64
    members["manifest.json"] = json.dumps(tampered_manifest, separators=(",", ":")).encode()
    with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in members.items():
            target.writestr(name, content)
    candidate_bytes = candidate.read_bytes()
    audit.EXPECTED_CANDIDATE.update(size_bytes=len(candidate_bytes), sha256=hashlib.sha256(candidate_bytes).hexdigest())
    _rebind_candidate(audit, candidate, import_report, correlation, report)
    assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--output", str(tmp_path / "member-tamper.json")]) == 2
    assert not (tmp_path / "member-tamper.json").exists()
    candidate, import_report, correlation, report = _write_fixture(tmp_path / "diagnostic", audit)

    # Each diagnostic mutation is allowed to emit a complete unclassified report.
    for label, mutate in (
        ("crossing", lambda value: value["runs"]["candidate"]["10"]["rails"][audit.LOADED_RAILS[0]]["metrics"]["resonances"].update({"model_imaginary_zero_crossings_hz": [1.0e5]})),
        ("provenance", lambda value: value["runs"]["candidate"]["10"]["rails"][audit.LOADED_RAILS[0]]["solver_provenance"].update({"terminal_surface_contact_proof_status": "unproven"})),
        ("omission", lambda value: value["runs"]["candidate"]["10"]["rails"][audit.LOADED_RAILS[0]]["solver_provenance"].update({"spatial_scope": "via mutual"})),
        ("pair-rms", lambda value: _break_pair_rms(value, audit)),
    ):
        mutated = copy.deepcopy(report)
        mutate(mutated)
        _rewrite_report(correlation, audit, mutated)
        output = tmp_path / f"{label}.json"
        assert audit.main(["--candidate", str(candidate), "--import-report", str(import_report), "--correlation-report", str(correlation), "--output", str(output)]) == 2
        result = json.loads(output.read_text(encoding="utf-8"))
        assert result["selected_investigation_block"] is None
        assert result["diagnostic_failures"]
        assert result["owner_status"] == "unclassified" and result["causal_owner"] is None
        output.unlink()
        _rewrite_report(correlation, audit, report)
