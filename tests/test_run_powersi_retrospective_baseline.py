import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "run_powersi_retrospective_baseline.py"
SPEC = importlib.util.spec_from_file_location("run_powersi_retrospective_baseline_test", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_entrypoint_uses_approved_controller():
    assert module.main.__module__ == module.__name__
    assert "run_controller" in module.main.__globals__


def test_controller_stops_after_phase1_failure_and_records_exact_env(tmp_path, monkeypatch):
    case = {"source": {"basename": "sample.spd", "path": "D:/sample.spd", "size_bytes": 1, "sha256": "a" * 64}, "touchstone": {"basename": "sample.s92p", "path": "D:/sample.s92p", "size_bytes": 1, "sha256": "b" * 64, "ports": 92}}
    policy = {"cases": {"260729": case}}
    calls = []
    monkeypatch.setattr(module.accuracy, "_load_policy", lambda _path: (policy, "c" * 64))
    monkeypatch.setattr(module, "_git_state", lambda _head: {"expected_head": "d" * 40, "observed_head": "d" * 40, "branch": "main", "clean": True})
    monkeypatch.setattr(module, "_registered_input", lambda *_args: Path("registered"))
    monkeypatch.setattr(module.accuracy, "build_phase_argv", lambda *_args: (["phase1"], ["phase2"]))
    def fake_run(argv, *, env, cwd, stdout_path, stderr_path):
        calls.append((argv, env["SPD_DECAP_PI_BLAS_THREADS"]))
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 7)
    monkeypatch.setattr(module, "_run_logged", fake_run)
    assert module.run_controller(policy_path=module.POLICY_PATH, case_id="260729", expected_head="d" * 40, out_root=tmp_path) == 2
    assert calls == [(["phase1"], "1")]
    tombstone = json.loads((tmp_path / "blocked_partial.json").read_text(encoding="utf-8"))
    assert tombstone["phase_status"]["phase1"]["exit_code"] == 7


def test_controller_cancel_terminates_then_kills(tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self, *_args, **_kwargs):
            self.terminated = False
            self.killed = False
        def wait(self, **kwargs):
            if not self.terminated:
                raise KeyboardInterrupt
            if self.terminated and not self.killed:
                raise subprocess.TimeoutExpired("fake", 5)
            return 130
        def terminate(self):
            self.terminated = True
        def kill(self):
            self.killed = True
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    with pytest.raises(module.accuracy.CancelledError):
        module._run_logged(["phase"], env={}, cwd=tmp_path, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")


def test_controller_success_manifest_lifecycle_and_bindings(tmp_path, monkeypatch):
    source = {"basename": "sample.spd", "path": "D:/sample.spd", "size_bytes": 1, "sha256": "a" * 64}
    touchstone = {"basename": "sample.s92p", "path": "D:/sample.s92p", "size_bytes": 1, "sha256": "b" * 64, "ports": 92}
    case = {"source": source, "touchstone": touchstone}
    policy = {"cases": {"260729": case}}
    report = {"source": source, "touchstone": {"basename": touchstone["basename"], "ports": 92, "full_file_sha256": touchstone["sha256"]}, "candidate_bundle": {}, "identity": {"source_candidate_match": True, "source_sha256": source["sha256"], "candidate_source_sha256": source["sha256"]}, "candidate_import_report_binding": {}, "score_split": {"vqps_development": list(module.accuracy.RAILS[:5]), "vqps_holdout": list(module.accuracy.RAILS[5:10]), "loaded_final_holdout": list(module.accuracy.RAILS[10:])}, "blas_runtime": {"status": "validated", "pools": [{"user_api": "blas", "internal_api": "openblas", "version": "0.3", "num_threads": 1}]}}
    monkeypatch.setattr(module.accuracy, "_load_policy", lambda _path: (policy, "c" * 64))
    monkeypatch.setattr(module, "_git_state", lambda head: {"expected_head": head, "observed_head": head, "branch": "main", "clean": True})
    monkeypatch.setattr(module, "_registered_input", lambda *_args: Path("registered"))
    monkeypatch.setattr(module.accuracy, "build_phase_argv", lambda *_args: (["phase1"], ["phase2"]))
    candidate_bytes = b"candidate"
    calls = []
    def fake_run(argv, *, env, cwd, stdout_path, stderr_path):
        calls.append((argv, env["SPD_DECAP_PI_BLAS_THREADS"]))
        stdout_path.parent.mkdir(parents=True, exist_ok=True); stdout_path.write_text("", encoding="utf-8"); stderr_path.write_text("", encoding="utf-8")
        if argv == ["phase1"]:
            (stdout_path.parent / "import").mkdir(exist_ok=True)
            candidate_path = stdout_path.parent / "import" / "sample_candidate.spdpi"; candidate_path.write_bytes(candidate_bytes)
            (stdout_path.parent / "import" / "import_save_validation_report.json").write_text("{}", encoding="utf-8")
        elif argv == ["phase2"]:
            (stdout_path.parent / "correlation").mkdir(exist_ok=True)
            (stdout_path.parent / "correlation" / "correlation_report.json").write_text("{}", encoding="utf-8")
            (stdout_path.parent / "correlation" / "blas_runtime_evidence.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(module, "_run_logged", fake_run)
    monkeypatch.setattr(module.accuracy, "_validate_import_binding", lambda path, candidate, case: {"sha256": "d" * 64, "basename": path.name, "bytes": path.stat().st_size, "candidate_sha256": module.accuracy._sha_file(candidate), "candidate_basename": candidate.name, "candidate_bytes": candidate.stat().st_size})
    monkeypatch.setattr(module.accuracy, "_validate_correlation_binding", lambda path, binding, case, report=None: {"sha256": module.accuracy._sha_file(path), "basename": path.name, "bytes": path.stat().st_size})
    monkeypatch.setattr(module.accuracy, "_validate_blas_artifact", lambda path: {"sha256": module.accuracy._sha_file(path), "basename": path.name, "bytes": path.stat().st_size, "payload": {"status": "validated", "pools": []}})
    monkeypatch.setattr(module.accuracy, "_json_bytes", lambda raw, source: report)
    monkeypatch.setattr(module.accuracy, "score_report", lambda policy, case_id, report: {"status": "PASS", "case_id": case_id, "claim_scope": "retrospective_accuracy_not_unseen_generalization", "counts": {}, "sub_1_mohm_sample_totals": {}, "macro": {}, "failures": []})
    out_root = tmp_path / "run"
    assert module.run_controller(policy_path=module.POLICY_PATH, case_id="260729", expected_head="d" * 40, out_root=out_root) == 0
    assert calls[:2] == [(["phase1"], "1"), (["phase2"], "1")]
    assert len(calls) == 3 and calls[2][1] == "1" and calls[2][0][:2] == [module.sys.executable, str(module.accuracy.V6_VALIDATOR)] and Path(calls[2][0][2]).name.startswith("owned-report-")
    manifest = out_root / "run_manifest.json"
    assert manifest.is_file() and (out_root / "run_manifest.json.sha256").read_text(encoding="ascii") == module.accuracy._sha_file(manifest) + "  run_manifest.json\n"
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_data["status"] == "completed" and manifest_data["score_status"] == "PASS"
    assert {item["basename"] for item in manifest_data["artifacts"]} == {"import/sample_candidate.spdpi", "import/import_save_validation_report.json", "correlation/correlation_report.json", "correlation/blas_runtime_evidence.json", "phase1.stdout.log", "phase1.stderr.log", "phase2.stdout.log", "phase2.stderr.log", "v6.stdout.log", "v6.stderr.log", "accuracy_sidecar.json"}
    for item in manifest_data["artifacts"]:
        path = out_root / item["basename"]
        assert item["bytes"] == path.stat().st_size and item["sha256"] == module.accuracy._sha_file(path)
    sidecar_data = json.loads((out_root / "accuracy_sidecar.json").read_text(encoding="utf-8"))
    assert sidecar_data["status"] == "PASS" and sidecar_data["evidence_sha256"] == manifest_data["evidence_sha256"]
    assert manifest_data["evidence"]["phase1"]["status"] == manifest_data["evidence"]["phase2"]["status"] == manifest_data["evidence"]["v6"]["status"] == "passed"
    assert module.run_controller(policy_path=module.POLICY_PATH, case_id="260729", expected_head="d" * 40, out_root=out_root) == 2
    assert len(calls) == 3


def test_controller_rejects_unpinned_accuracy_validator_before_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "EXPECTED_ACCURACY_VALIDATOR_SHA256", "f" * 64)
    assert module.run_controller(policy_path=module.POLICY_PATH, case_id="260729", expected_head="d" * 40, out_root=tmp_path) == 2
    assert json.loads((tmp_path / "blocked_partial.json").read_text(encoding="utf-8"))["phase"] == "preflight"
