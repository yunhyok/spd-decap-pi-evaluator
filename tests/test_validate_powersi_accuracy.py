import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "validate_powersi_accuracy.py"
SPEC = importlib.util.spec_from_file_location("validate_powersi_accuracy_test", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
CONTROLLER_SPEC = importlib.util.spec_from_file_location("run_powersi_retrospective_baseline_test", ROOT / "scripts" / "run_powersi_retrospective_baseline.py")
assert CONTROLLER_SPEC and CONTROLLER_SPEC.loader
controller = importlib.util.module_from_spec(CONTROLLER_SPEC)
CONTROLLER_SPEC.loader.exec_module(controller)


def _case():
    policy, _ = module._load_policy()
    return policy["cases"]["260729"]


def test_policy_is_canonical_and_has_exact_rails():
    policy, digest = module._load_policy()
    assert digest == module._sha_bytes(module._canonical(policy))
    assert policy["schema_version"] == "powersi-accuracy-policy-v1"
    assert len(policy["rails"]["ordered"]) == 16
    assert policy["rails"]["ordered"][:10][-1].endswith("/1")
    assert policy["rails"]["bare_vqps_count"] == 10
    assert policy["rails"]["loaded_count"] == 6


def test_phase_argv_is_exact_two_phase_and_ordered():
    phase1, phase2 = module.build_phase_argv(_case(), Path("import"), Path("correlation"))
    assert phase1[1] == str(module.BENCHMARK_ADAPTER)
    assert phase1[-3:] == ["--solver-profile", "layerwise_admittance_v1", "--import-save-only"]
    assert phase2.count("--rail") == 16
    assert [phase2[index + 1] for index, item in enumerate(phase2) if item == "--rail"] == list(module.RAILS)
    assert phase2[phase2.index("--modal-max-index") + 1] == "10"
    assert phase2[phase2.index("--modal-ceiling-index") + 1] == "12"
    assert "--require-terminal-complete-reuse" in phase2


def test_score_report_applies_low_frequency_and_macro_gates():
    policy, _ = module._load_policy()
    rails = {}
    for rail in module.RAILS:
        rails[rail] = {
            "metrics": {
                "grid": {"low_hz": 100000.0, "high_hz": 100000000.0, "points": 241},
                "complex": {"rms_uohm": 0.1, "median_uohm": 0.1, "p95_uohm": 0.1, "max_uohm": 0.1},
                "anchors": {
                    "0.1MHz": {"signed_magnitude_error_db": 0.1},
                    "1MHz": {"signed_magnitude_error_db": 0.1},
                },
                "magnitude_db": {"max_abs_db": 0.1, "rms_db": 0.1},
                "phase_deg": {"rms_deg": 0.1, "max_abs_deg": 0.1},
                "resonances": {"model_local_peaks": [], "reference_local_peaks": [], "model_imaginary_zero_crossings_hz": [], "reference_imaginary_zero_crossings_hz": []},
                "sub_1_mohm": {"reference_grid_samples": 0},
            }
        }
    report = {"runs": {"candidate": {"10": {"rails": rails}}}}
    result = module.score_report(policy, "260729", report)
    assert result["status"] == "PASS"
    assert result["counts"]["by_stratum"]["bare"]["sub_1_mohm"]["N/A"] == 10
    assert result["counts"]["by_stratum"]["loaded"]["sub_1_mohm"]["N/A"] == 6


def test_blocked_tombstone_is_fail_closed_and_no_scoring(tmp_path):
    result = controller._blocked(tmp_path, "cancelled", phase="correlation", policy_sha256="a" * 64)
    assert result == 2
    tombstone = json.loads((tmp_path / "blocked_partial.json").read_text(encoding="utf-8"))
    assert tombstone["status"] == "blocked_partial"
    assert tombstone["phase"] == "correlation"
    assert tombstone["scoring"] == "refused"
    assert tombstone["artifacts"] == []


def test_score_rejects_missing_report_metric():
    policy, _ = module._load_policy()
    with pytest.raises(module.IntegrityError):
        module.score_report(policy, "260729", {"runs": {"candidate": {"10": {"rails": {}}}}})


def _report_with_resonance(reference, model):
    policy, _ = module._load_policy()
    rails = {}
    for rail in module.RAILS:
        rails[rail] = {
            "metrics": {
                "grid": {"low_hz": 100000.0, "high_hz": 100000000.0, "points": 241},
                "complex": {"rms_uohm": 0.1, "median_uohm": 0.1, "p95_uohm": 0.1, "max_uohm": 0.1},
                "magnitude_db": {"max_abs_db": 0.1, "rms_db": 0.1},
                "phase_deg": {"rms_deg": 0.1, "max_abs_deg": 0.1},
                "anchors": {"0.1MHz": {"signed_magnitude_error_db": 0.1}, "1MHz": {"signed_magnitude_error_db": 0.1}},
                "resonances": {"model_local_peaks": model, "reference_local_peaks": reference, "model_imaginary_zero_crossings_hz": [], "reference_imaginary_zero_crossings_hz": []},
                "sub_1_mohm": {"reference_grid_samples": 0},
            }
        }
    return policy, {"runs": {"candidate": {"10": {"rails": rails}}}}


@pytest.mark.parametrize(
    ("reference", "model", "expected"),
    [
        ([{"frequency_hz": 1.0e6, "magnitude_ohm": 2.0}], [], "FAIL"),
        ([], [{"frequency_hz": 1.0e6, "magnitude_ohm": 2.0}], "PASS"),
        ([{"frequency_hz": 1.0e6, "magnitude_ohm": 2.0}, {"frequency_hz": 1.1e6, "magnitude_ohm": 2.0}], [{"frequency_hz": 1.0e6, "magnitude_ohm": 2.0}], "PASS"),
    ],
)
def test_resonance_reference_missing_model_missing_and_tie_lowest_frequency(reference, model, expected):
    policy, report = _report_with_resonance(reference, model)
    assert module.score_report(policy, "260729", report)["status"] == expected


def test_malformed_nonfinite_metric_is_integrity_error():
    policy, report = _report_with_resonance([], [])
    report["runs"]["candidate"]["10"]["rails"][module.RAILS[0]]["metrics"]["magnitude_db"]["rms_db"] = float("nan")
    with pytest.raises(module.IntegrityError, match="malformed/nonfinite"):
        module.score_report(policy, "260729", report)


def test_logged_process_cancellation_terminates_without_retry(tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self, *_args, **_kwargs):
            self.terminated = False
            self.killed = False

        def wait(self, **kwargs):
            if not self.terminated:
                raise KeyboardInterrupt
            return 130

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    monkeypatch.setattr(controller.subprocess, "Popen", FakeProcess)
    with pytest.raises(controller.accuracy.CancelledError):
        controller._run_logged(["fake"], env={}, cwd=tmp_path, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")


def test_blocked_tombstone_does_not_overwrite_existing(tmp_path):
    controller._blocked(tmp_path, "first", phase="phase1", policy_sha256="a" * 64)
    controller._blocked(tmp_path, "second", phase="phase2", policy_sha256="b" * 64)
    assert "first" in (tmp_path / "blocked_partial.json").read_text(encoding="utf-8")


def test_policy_duplicate_and_nonfinite_snapshots_fail_closed(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"powersi-accuracy-policy-v1","schema_version":"x"}', encoding="utf-8")
    with pytest.raises(module.IntegrityError, match="duplicate"):
        module._json(duplicate)
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(module.IntegrityError, match="non-finite"):
        module._json(nonfinite)


@pytest.mark.parametrize("gate", ["low", "magnitude", "phase_rms", "phase_max", "sub_rms", "sub_p95", "macro"])
def test_each_approved_gate_is_numeric_fail_with_counts(gate):
    policy, report = _report_with_resonance([], [])
    rails = report["runs"]["candidate"]["10"]["rails"]
    if gate == "low":
        rails[module.RAILS[0]]["metrics"]["anchors"]["0.1MHz"]["signed_magnitude_error_db"] = 3.0
    elif gate == "magnitude":
        rails[module.RAILS[0]]["metrics"]["magnitude_db"]["max_abs_db"] = 3.0
    elif gate == "phase_rms":
        rails[module.RAILS[0]]["metrics"]["phase_deg"]["rms_deg"] = 8.0
    elif gate == "phase_max":
        rails[module.RAILS[0]]["metrics"]["phase_deg"]["max_abs_deg"] = 16.0
    elif gate in {"sub_rms", "sub_p95"}:
        sub = rails[module.RAILS[0]]["metrics"]["sub_1_mohm"]
        sub.update(reference_grid_samples=1, complex_rms_uohm=101.0 if gate == "sub_rms" else 1.0, complex_p95_uohm=201.0 if gate == "sub_p95" else 1.0)
    else:
        for rail in module.RAILS[:10]:
            rails[rail]["metrics"]["magnitude_db"]["rms_db"] = 1.1
    result = module.score_report(policy, "260729", report)
    assert result["status"] == "FAIL"
    if gate == "macro":
        assert result["macro"]["bare"]["status"] == "FAIL"
    elif gate.startswith("sub"):
        assert result["counts"]["by_stratum"]["bare"]["sub_1_mohm"]["fail"] == 1
        assert result["sub_1_mohm_sample_totals"]["bare"] == 1
    else:
        assert any(item["name"] == module.RAILS[0] for item in result["failures"])


@pytest.mark.parametrize("field,value", [("reference_grid_samples", -1), ("reference_grid_samples", "1"), ("complex_rms_uohm", None)])
def test_malformed_sub_one_mohm_fields_are_integrity_errors(field, value):
    policy, report = _report_with_resonance([], [])
    sub = report["runs"]["candidate"]["10"]["rails"][module.RAILS[0]]["metrics"]["sub_1_mohm"]
    sub["reference_grid_samples"] = 1
    sub["complex_rms_uohm"] = 1.0
    sub["complex_p95_uohm"] = 1.0
    sub[field] = value
    with pytest.raises(module.IntegrityError):
        module.score_report(policy, "260729", report)


@pytest.mark.parametrize("reference,model,expected", [
    ([{"frequency_hz": 1.0e6, "magnitude_ohm": 1.0}], [{"frequency_hz": 1.2e6, "magnitude_ohm": 1.0}], "FAIL"),
    ([{"frequency_hz": 1.0e6, "magnitude_ohm": 1.0}], [{"frequency_hz": 1.0e6, "magnitude_ohm": 2.0}], "FAIL"),
])
def test_resonance_frequency_and_amplitude_limits(reference, model, expected):
    policy, report = _report_with_resonance(reference, model)
    assert module.score_report(policy, "260729", report)["status"] == expected


def test_manifest_path_rejects_escape_and_json_snapshot_rejects_nan(tmp_path):
    with pytest.raises(module.IntegrityError):
        module._manifest_path(tmp_path, "../escape.json")
    with pytest.raises(module.IntegrityError):
        module._json_bytes(b'{"x": NaN}', "snapshot")


def test_frozen_historical_validation_assets_remain_byte_identical():
    paths = ["scripts/validate_correlation_v5.py", "scripts/validate_known_case_nonregression.py", "validation-policies/known_case_nonregression_v1.json"]
    for path in paths:
        current = subprocess.run(["git", "hash-object", path], capture_output=True, text=True, check=True).stdout.strip()
        baseline = subprocess.run(["git", "show", f"027ac7a:{path}"], capture_output=True, check=True).stdout
        assert current == subprocess.run(["git", "hash-object", "--stdin"], input=baseline, capture_output=True, check=True).stdout.decode().strip()
    assert subprocess.run(["git", "diff", "--quiet", "027ac7a", "--", "validation-fixtures/known-case-nonregression-v1"], check=False).returncode == 0
    v6_text = (ROOT / "scripts" / "validate_correlation_v6.py").read_text(encoding="utf-8")
    assert 'SOLVER_VERSION = "modal-mvp-0.8.5"' in v6_text and 'POLICY = "adaptive-frequency-modal-v5"' in v6_text and 'V6_PASS_MARKER = "PASS: v6 current correlation structure/conditioning validated"' in v6_text and "MAX_FACTOR_PIVOT_RATIO" in v6_text and "BLAS" not in v6_text
    policy, _ = module._load_policy()
    assert policy["validator_v6"]["sha256"] == module._normalized_sha(ROOT / "scripts" / "validate_correlation_v6.py")


def test_verify_existing_complete_snapshot_and_tamper_bindings(tmp_path, monkeypatch):
    import copy
    import subprocess as process
    source = {"basename": "non_sample_design.spd", "path": "D:/non_sample_design.spd", "size_bytes": 1, "sha256": "a" * 64}
    touchstone = {"basename": "sample.s92p", "path": "D:/sample.s92p", "size_bytes": 1, "sha256": "b" * 64, "ports": 92}
    case = {"role": "retrospective_development", "source": source, "touchstone": touchstone, "candidate": "absent/must_be_generated", "strata": ["site0", "site1", "loaded"]}
    policy = {"cases": {"260729": case}}
    monkeypatch.setattr(module, "_load_policy", lambda _path: (policy, "c" * 64))
    monkeypatch.setattr(module, "score_report", lambda *_args: {"status": "PASS", "case_id": "260729", "claim_scope": "retrospective_accuracy_not_unseen_generalization", "counts": {}, "sub_1_mohm_sample_totals": {}, "macro": {}, "failures": []})
    root = tmp_path
    (root / "import").mkdir(); (root / "correlation").mkdir()
    candidate = root / "import" / "non_sample_design_candidate.spdpi"; candidate.write_bytes(b"candidate")
    import_report_path = root / "import" / "import_save_validation_report.json"
    import_report = {"report_version": 1, "mode": "import_save_only", "status": "passed", "import": {"mode": "fresh_import"}, "source": source, "candidate_bundle": {"basename": candidate.name, "size_bytes": candidate.stat().st_size, "sha256": module._sha_file(candidate), "reuse_requested": False}, "atomic_save_load_validation": {name: True for name in ("archive_manifest_and_member_hashes_validated", "scenario_schema_validated_after_reload", "source_identity_validated_after_reload", "attachment_hashes_validated_after_reload")}}
    import_report_path.write_text(json.dumps(import_report), encoding="utf-8")
    report = {"source": source, "touchstone": {"basename": touchstone["basename"], "ports": 92, "full_file_sha256": touchstone["sha256"]}, "candidate_bundle": {"basename": candidate.name, "size_bytes": candidate.stat().st_size, "sha256": module._sha_file(candidate)}, "identity": {"source_candidate_match": True, "source_sha256": source["sha256"], "candidate_source_sha256": source["sha256"]}, "candidate_import_report_binding": {"status": "validated", "basename": import_report_path.name, "sha256": module._sha_file(import_report_path)}, "score_split": {"vqps_development": list(module.RAILS[:5]), "vqps_holdout": list(module.RAILS[5:10]), "loaded_final_holdout": list(module.RAILS[10:])}, "blas_runtime": {"status": "validated", "pools": [{"user_api": "blas", "internal_api": "openblas", "version": "0.3", "num_threads": 1}]}, "runs": {}}
    report_path = root / "correlation" / "correlation_report.json"; report_path.write_text(json.dumps(report), encoding="utf-8")
    logs = []
    for name in ("phase1.stdout.log", "phase1.stderr.log", "phase2.stdout.log", "phase2.stderr.log", "v6.stdout.log", "v6.stderr.log"):
        path = root / name; path.write_text("", encoding="utf-8"); logs.append(path)
    phase1, phase2 = module.build_phase_argv(case, root / "import", root / "correlation")
    def artifact(path):
        return {"basename": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": module._sha_file(path), "exit_code": 0, "cancelled": False}
    blas_path = root / "correlation" / "blas_runtime_evidence.json"; blas_path.write_text(json.dumps({"schema_version": "powersi-blas-runtime-evidence-v1", "status": "validated", "base_source_sha256": module._normalized_sha(module.BENCHMARK), "adapter_source_sha256": module._normalized_sha(module.BENCHMARK_ADAPTER), "required_num_threads": 1, "resolved_num_threads": 1, "pools": [{"user_api": "blas", "internal_api": "openblas", "version": "0.3", "threading_layer": None, "architecture": None, "filepath": None, "num_threads": 1}]}), encoding="utf-8")
    evidence = {"schema_version": "powersi-retrospective-execution-evidence-v1", "policy": {"path": str(module.POLICY_PATH), "sha256": "c" * 64}, "benchmark_base": {"path": str(module.BENCHMARK), "sha256": module._normalized_sha(module.BENCHMARK)}, "benchmark_adapter": {"path": str(module.BENCHMARK_ADAPTER), "sha256": module._normalized_sha(module.BENCHMARK_ADAPTER)}, "validator_v6": {"path": str(module.V6_VALIDATOR), "sha256": module._normalized_sha(module.V6_VALIDATOR)}, "git": {"expected_head": "d" * 40, "observed_head": "d" * 40, "branch": "main", "clean": True}, "runtime": {"worker": 1, "retries": 0, "env": {"SPD_DECAP_PI_BLAS_THREADS": "1"}, "rails": list(module.RAILS), "modes": [10, 12]}, "inputs": {"source": source, "touchstone": touchstone}, "phase1": {"status": "passed", "argv": phase1, "exit_code": 0, "cancelled": False, "stdout": artifact(logs[0]), "stderr": artifact(logs[1]), "import_report": artifact(import_report_path), "candidate": artifact(candidate)}, "phase2": {"status": "passed", "argv": phase2, "exit_code": 0, "cancelled": False, "stdout": artifact(logs[2]), "stderr": artifact(logs[3]), "correlation_report": artifact(report_path), "blas_runtime": artifact(blas_path)}, "v6": {"status": "passed", "argv": [module.sys.executable, str(module.V6_VALIDATOR), "<owned-report-snapshot>"], "exit_code": 0, "cancelled": False, "stdout": artifact(logs[4]), "stderr": artifact(logs[5])}}
    evidence["accuracy_validator"] = {"path": str(module.Path(module.__file__)), "sha256": module._normalized_sha(module.Path(module.__file__))}
    sidecar = {"schema_version": "powersi-accuracy-sidecar-v1", "status": "PASS", "claim_scope": "retrospective_accuracy_not_unseen_generalization", "policy_sha256": "c" * 64, "validator_v6_sha256": module._normalized_sha(module.V6_VALIDATOR), "report_sha256": module._sha_file(report_path), "candidate_sha256": module._sha_file(candidate), "evidence_sha256": module._sha_bytes(module._canonical(evidence)), "score": {"status": "PASS", "case_id": "260729", "claim_scope": "retrospective_accuracy_not_unseen_generalization", "counts": {}, "sub_1_mohm_sample_totals": {}, "macro": {}, "failures": []}}
    sidecar_path = root / "accuracy_sidecar.json"; sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    artifacts = [artifact(candidate), artifact(import_report_path), artifact(report_path), artifact(blas_path)] + [artifact(path) for path in logs] + [artifact(sidecar_path)]
    manifest = {"schema_version": "powersi-retrospective-run-manifest-v1", "status": "completed", "case_id": "260729", "evidence": evidence, "evidence_sha256": module._sha_bytes(module._canonical(evidence)), "artifacts": artifacts, "accuracy_sidecar": {"basename": sidecar_path.name, "bytes": sidecar_path.stat().st_size, "sha256": module._sha_file(sidecar_path)}, "logs": [item for item in artifacts if item["basename"].endswith(".log")], "score_status": "PASS"}
    manifest_path = root / "run_manifest.json"; manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (root / "run_manifest.json.sha256").write_bytes((module._sha_file(manifest_path) + "  run_manifest.json\n").encode("ascii"))
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: process.CompletedProcess(args[0], 0, stdout="PASS: v6 current correlation structure/conditioning validated", stderr=""))
    assert module.verify_existing(policy_path=module.POLICY_PATH, case_id="260729", manifest_path=manifest_path, report_path=report_path, sidecar_path=sidecar_path)["status"] == "PASS"
    original_manifest = copy.deepcopy(manifest)
    original_sidecar = copy.deepcopy(sidecar)
    original_blas = blas_path.read_bytes()
    mutations = []
    tampered = copy.deepcopy(original_manifest); tampered["evidence"]["phase2"]["argv"] = ["bad"]; tampered["evidence_sha256"] = module._sha_bytes(module._canonical(tampered["evidence"])); mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["evidence"]["phase1"]["stdout"]["sha256"] = "f" * 64; tampered["evidence_sha256"] = module._sha_bytes(module._canonical(tampered["evidence"])); mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["artifacts"][0]["sha256"] = "f" * 64; mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["artifacts"].append(copy.deepcopy(tampered["artifacts"][0])); mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["artifacts"][0]["exit_code"] = 1; mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["artifacts"][0]["cancelled"] = True; mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["logs"] = list(reversed(tampered["logs"])); mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["artifacts"][-1]["basename"] = "../escape.log"; mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["score_status"] = "FAIL"; mutations.append((tampered, original_sidecar))
    tampered = copy.deepcopy(original_manifest); tampered["accuracy_sidecar"]["sha256"] = "f" * 64; mutations.append((tampered, original_sidecar))
    tampered_sidecar = copy.deepcopy(original_sidecar); tampered_sidecar["candidate_sha256"] = "f" * 64; mutations.append((original_manifest, tampered_sidecar))
    tampered_sidecar = copy.deepcopy(original_sidecar); tampered_sidecar["status"] = "FAIL"; mutations.append((original_manifest, tampered_sidecar))
    for mutated_manifest, mutated_sidecar in mutations:
        manifest_path.write_text(json.dumps(mutated_manifest), encoding="utf-8")
        sidecar_path.write_text(json.dumps(mutated_sidecar), encoding="utf-8")
        (root / "run_manifest.json.sha256").write_bytes((module._sha_file(manifest_path) + "  run_manifest.json\n").encode("ascii"))
        with pytest.raises(module.IntegrityError):
            module.verify_existing(policy_path=module.POLICY_PATH, case_id="260729", manifest_path=manifest_path, report_path=report_path, sidecar_path=sidecar_path)
    manifest_path.write_text(json.dumps(original_manifest), encoding="utf-8")
    sidecar_path.write_text(json.dumps(original_sidecar), encoding="utf-8")
    (root / "run_manifest.json.sha256").write_bytes((module._sha_file(manifest_path) + "  run_manifest.json\n").encode("ascii"))
    blas_path.write_text(json.dumps({"schema_version": "powersi-blas-runtime-evidence-v1", "status": "validated", "base_source_sha256": "f" * 64, "adapter_source_sha256": module._normalized_sha(module.BENCHMARK_ADAPTER), "required_num_threads": 1, "resolved_num_threads": 1, "pools": [{"user_api": "blas", "internal_api": "openblas", "version": "0.3", "threading_layer": None, "architecture": None, "filepath": None, "num_threads": 1}]}), encoding="utf-8")
    with pytest.raises(module.IntegrityError):
        module.verify_existing(policy_path=module.POLICY_PATH, case_id="260729", manifest_path=manifest_path, report_path=report_path, sidecar_path=sidecar_path)
    valid_blas = json.loads(original_blas)
    blas_mutations = []
    top_bool = copy.deepcopy(valid_blas); top_bool["required_num_threads"] = True; blas_mutations.append(top_bool)
    extra_pool = copy.deepcopy(valid_blas); extra_pool["pools"][0]["extra"] = None; blas_mutations.append(extra_pool)
    unsorted = copy.deepcopy(valid_blas); unsorted["pools"] = [dict(unsorted["pools"][0], internal_api="z"), dict(unsorted["pools"][0], internal_api="a")]; blas_mutations.append(unsorted)
    for payload in blas_mutations:
        with pytest.raises(module.IntegrityError):
            module._validate_blas_artifact(blas_path, raw=json.dumps(payload).encode("utf-8"))
    blas_path.write_bytes(original_blas)
    (root / "run_manifest.json.sha256").write_bytes((module._sha_file(manifest_path) + "  run_manifest.json\n").encode("ascii"))
    digest_path = root / "run_manifest.json.sha256"
    digest_path.unlink()
    with pytest.raises(module.IntegrityError):
        module.verify_existing(policy_path=module.POLICY_PATH, case_id="260729", manifest_path=manifest_path, report_path=report_path, sidecar_path=sidecar_path)
