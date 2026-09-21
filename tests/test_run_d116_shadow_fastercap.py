from __future__ import annotations
import importlib.util, subprocess
from hashlib import sha256
from pathlib import Path
import numpy as np, pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("d116run", ROOT / "tools/research/run_d116_shadow_fastercap.py")
MODULE = importlib.util.module_from_spec(SPEC); assert SPEC.loader is not None; SPEC.loader.exec_module(MODULE)

MATRIX = np.array([[2.0, -0.2, -0.1], [-0.2, 3.0, -0.3], [-0.1, -0.3, 4.0]])
BASE = """FasterCap version 6.0.7
3D Solver Engine invoked
Output capacitance matrix to file (-e)
Solution scheme (-g): Collocation
GMRES tolerance (-t): 0.005
Out-of-core free memory to link memory condition (-f): 5
Potential interaction coefficient to mesh refinement ratio (-d): 1
Mesh curvature (-mc): 3
Precond Type(s) (-p): Jacobi
Auto calculation with max error: 0.01
Remark: Auto option overrides all other Manual settings
Dimension 3 x 3
g1_A_GND_259 1 0 0
g2_A_PWR_264 0 1 0
g3_F_DDRL_262 0 0 1
Dimension 3 x 3
{rows}
Weighted Frobenius norm of the difference between capacitance (auto option): 0.001
Number of input panels: 250 of which 250 conductors and 0 dielectric
Number of input panels to solver engine: 336
Number of panels after refinement: 336
"""
ROWS = "\n".join(f"{label} {' '.join('%g' % x for x in row)}" for label, row in zip(MODULE.LABELS, MATRIX))
OPTIONS = BASE.format(rows=ROWS)

class FakeProcess:
    returncode = 0
    def __init__(self, argv, **kwargs):
        self.kwargs = kwargs; self._done = True
        Path(kwargs["cwd"], "d116_shadow_gap_coupon.csv").write_text("\n".join(",".join("%g" % x for x in r) for r in MATRIX) + "\n", encoding="utf-8")
        kwargs["stdout"].write(OPTIONS.encode()); kwargs["stdout"].flush()
    def poll(self): return 0 if self._done else None
    def wait(self, timeout=None): self._done = True; return 0
    def kill(self): self._done = True

class BadProcess(FakeProcess):
    def __init__(self, argv, **kwargs):
        super().__init__(argv, **kwargs); kwargs["stdout"].seek(0); kwargs["stdout"].truncate(); kwargs["stdout"].write(OPTIONS.replace("g3_F_DDRL_262", "wrong").encode()); kwargs["stdout"].flush()

def test_mocked_runner_seals_raw_matrix_without_real_launch(tmp_path, monkeypatch):
    source = tmp_path / "input-source"; source.mkdir()
    for name in MODULE.FILES: (source / name).write_bytes(b"sealed")
    solver = tmp_path / "FasterCap.exe"; solver.write_bytes(b"mock")
    monkeypatch.setattr(MODULE, "validate_preflight", lambda *args: {"status": "PASS_D116_SHADOW_GAP_COUPON"})
    monkeypatch.setattr(MODULE, "OUTPUT_ROOT", tmp_path / "run")
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "FILE_META", {n: (len(b"sealed"), MODULE.digest(source / n)) for n in MODULE.FILES})
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=FakeProcess)
    assert result["status"] == "PASS_D116_SHADOW_RAW_C_MATRIX"; assert result["matrix_raw"] == MATRIX.tolist(); assert result["numerical_invocations"] == 1
    assert result["scope"]["full_domain"] is False and result["scope"]["artificial_w0_crop_walls"] is True
    assert result["conductor_order"] == ["A_GND_259", "A_PWR_264", "F_DDRL_262"] and result["expected_solver_labels"] == list(MODULE.LABELS)
    assert result["stdout_sha256"] and result["stderr_sha256"] and not result["registry_diagnostic_whitelisted"] and not result["nonzero_return_whitelisted"]
    assert result["matrix_stats"]["input_panels"] == 250 and result["matrix_stats"]["solver_engine_panels"] == 336 and result["matrix_stats"]["comparison_atol"] > 0
    assert result["csv_artifact"]["filename"] == "input/d116_shadow_gap_coupon.csv" and result["csv_artifact"]["size_bytes"] > 0 and result["csv_artifact"]["sha256"]
    assert result["stdout_artifact"]["size_bytes"] > 0 and result["stderr_artifact"]["size_bytes"] == 0 and result["matrix_units"] == "F" and result["solver_version"] == "6.0.7" and result["partition"] == {"A":[259,264],"F":[262]}
    assert result["argv"] == [str(solver.resolve()), "-b", str((tmp_path / "run" / "input" / "d116_shadow_gap_coupon.lst").resolve()), "-a0.01", "-mc3", "-d1", "-f5", "-pj", "-e", "-i"]

def test_parser_uses_last_block_and_maxwell_signed_offdiagonals():
    parsed, stats = MODULE._parse_stdout(OPTIONS)
    np.testing.assert_allclose(parsed, MATRIX); assert stats["refined_panels"] == 336; assert MODULE._maxwell(MATRIX)["eigmin_sym"] > 0
    final_row = ROWS.splitlines()[-1]
    broken = OPTIONS.rsplit(final_row, 1)[0] + final_row.replace("g3_F_DDRL_262", "wrong", 1) + OPTIONS.rsplit(final_row, 1)[1]
    with pytest.raises(ValueError, match="labels"): MODULE._parse_stdout(broken)
    with pytest.raises(ValueError, match="convergence"): MODULE._parse_stdout(OPTIONS.replace("0.001", "0.02"))
    with pytest.raises(ValueError, match="preconditioner"): MODULE._parse_stdout(OPTIONS + "Precond Type(s) (-p): None\n")

def test_monitor_samples_exited_process_handle(monkeypatch, tmp_path):
    class Exited:
        pid = 1; _handle = 1
        def poll(self): return 0
    samples = iter([100, 200]); monkeypatch.setattr(MODULE, "_working_set", lambda p: next(samples)); monkeypatch.setattr(MODULE.os, "name", "nt")
    out = MODULE._monitor(Exited(), tmp_path, MODULE.time.time()); assert out["peak_working_set_bytes"] == 200
    samples = iter([100, 25 * 1024**3]); monkeypatch.setattr(MODULE, "_working_set", lambda p: next(samples))
    with pytest.raises(MemoryError): MODULE._monitor(Exited(), tmp_path, MODULE.time.time())

def test_terminate_tree_records_failed_taskkill(monkeypatch):
    class P:
        pid = 3
        def __init__(self): self.alive = True; self.returncode = -9
        def poll(self): return None if self.alive else self.returncode
        def kill(self): self.alive = False
        def wait(self, timeout=None): self.alive = False
    p = P(); monkeypatch.setattr(MODULE.os, "name", "nt"); monkeypatch.setattr(MODULE.subprocess, "run", lambda *a, **k: type("R", (), {"returncode":1})())
    ev, detail = MODULE._terminate_tree(p); assert ev["taskkill_return_code"] == 1 and ev["primary_termination_confirmed"] and not ev["tree_termination_confirmed"] and not ev["termination_confirmed"] and "tree termination unconfirmed" in detail

def test_launched_parser_failure_seals_stop_receipt(tmp_path, monkeypatch):
    source = tmp_path / "input-source"; source.mkdir()
    for name in MODULE.FILES: (source / name).write_bytes(b"sealed")
    solver = tmp_path / "FasterCap.exe"; solver.write_bytes(b"mock")
    monkeypatch.setattr(MODULE, "validate_preflight", lambda *args: {"status": "PASS_D116_SHADOW_GAP_COUPON"})
    monkeypatch.setattr(MODULE, "OUTPUT_ROOT", tmp_path / "run")
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "FILE_META", {n: (len(b"sealed"), MODULE.digest(source / n)) for n in MODULE.FILES})
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=BadProcess)
    assert result["status"] == "STOP_D116_SHADOW" and result["numerical_invocations"] == 1
    sealed = (tmp_path / "run" / "d116_shadow_fastercap_receipt.json"); assert sealed.exists(); payload = __import__("json").loads(sealed.read_text()); assert payload["status"] == "STOP_D116_SHADOW"; assert payload["csv_artifact"]["filename"] == "input/d116_shadow_gap_coupon.csv" and payload["csv_artifact"]["size_bytes"] > 0 and payload["csv_artifact"]["sha256"]

def test_registry_pair_allows_nonzero_only_after_complete_evidence():
    text = OPTIONS.replace("Weighted", "Error : Cannot find FastFieldSolvers settings in the Registry\nPlease try installing the software again\nWeighted")
    _, stats = MODULE._parse_stdout(text, ""); assert stats["registry_warning_whitelisted"]
    with pytest.raises(ValueError): MODULE._parse_stdout(text.replace("Please try installing the software again", "unknown warning"))

def test_preconditioner_none_in_stderr_is_rejected():
    with pytest.raises(ValueError, match="preconditioner"):
        MODULE._parse_stdout(OPTIONS, "Precond Type(s) (-p): None\n")
    with pytest.raises(ValueError, match="preconditioner"):
        MODULE._parse_stdout(OPTIONS, "Precond Type(s): None\n")

def test_cli_help_is_cp949_safe():
    completed = subprocess.run(["python", str(ROOT / "tools/research/run_d116_shadow_fastercap.py"), "--help"], capture_output=True, env={"PYTHONIOENCODING":"cp949"})
    assert completed.returncode == 0; assert b"SPD Decap PI Evaluator v0.23.1 D116-SHADOW" in completed.stdout

def test_cli_requires_manifest_approval_sha():
    completed = subprocess.run(["python", str(ROOT / "tools/research/run_d116_shadow_fastercap.py")], capture_output=True)
    assert completed.returncode == 2 and b"approval-sha256" in completed.stderr

def _seed_run(tmp_path, monkeypatch):
    source = tmp_path / "input-source"; source.mkdir()
    for name in MODULE.FILES: (source / name).write_bytes(b"sealed")
    solver = tmp_path / "FasterCap.exe"; solver.write_bytes(b"mock")
    monkeypatch.setattr(MODULE, "validate_preflight", lambda *args: {"status": "PASS_D116_SHADOW_GAP_COUPON"})
    monkeypatch.setattr(MODULE, "FILE_META", {n: (len(b"sealed"), MODULE.digest(source / n)) for n in MODULE.FILES})
    return source, solver

def test_windows_lifecycle_uses_suspended_flags_and_closes_job_after_evidence(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch); events = []
    approval = sha256(b"a").hexdigest()
    manifest = {"_approved_size_bytes": 1, "_approved_sha256": approval,
                "runner": {"size_bytes": 1, "sha256": approval},
                "tests": {"size_bytes": 1, "sha256": approval}}
    monkeypatch.setattr(MODULE, "validate_preflight", lambda *args: {"status": "PASS_D116_SHADOW_GAP_COUPON", "approval_manifest": manifest})
    monkeypatch.setattr(MODULE.os, "name", "nt")
    monkeypatch.setattr(MODULE.subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(MODULE.subprocess, "CREATE_SUSPENDED", 0x4, raising=False)
    job = object()
    monkeypatch.setattr(MODULE, "_create_job", lambda: events.append("create") or job)
    monkeypatch.setattr(MODULE, "_assign_job", lambda j, p: events.append("assign"))
    monkeypatch.setattr(MODULE, "_resume_primary_thread", lambda p: events.append("resume"))
    monkeypatch.setattr(MODULE, "_job_active_count", lambda j: events.append("active0") or 0)
    monkeypatch.setattr(MODULE, "_close_handle", lambda h: events.append("close"))
    evidence = [{"source": p, "copy": "evidence/" + p, "size_bytes": 1, "sha256": approval} for p in ("tools/research/run_d116_shadow_fastercap.py", "tests/test_run_d116_shadow_fastercap.py", "tools/research/d116_shadow_fastercap_approval_manifest.json")]
    def copy_evidence(output, *args):
        for item in evidence:
            path = output / item["copy"]; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"a")
        return evidence
    monkeypatch.setattr(MODULE, "_copy_evidence", copy_evidence)
    class P(FakeProcess):
        pid = 9; _handle = 99
    def factory(argv, **kwargs):
        events.append(("Popen", kwargs.get("creationflags"), kwargs.get("stdin"))); return P(argv, **kwargs)
    def monitor(process, root, started, passed_job, observations):
        events.append("monitor"); assert passed_job is job; observations["memory_sample_count"] = 2
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=factory, monitor_factory=monitor, approval_sha256=approval)
    assert result["status"] == "PASS_D116_SHADOW_RAW_C_MATRIX"
    assert result["evidence_artifacts"] == evidence and len(result["evidence_artifacts"]) == 3
    receipt = tmp_path / "run" / "d116_shadow_fastercap_receipt.json"
    sidecar = receipt.with_name(receipt.name + ".sha256")
    assert sidecar.read_text().startswith(MODULE.digest(receipt) + "  " + receipt.name)
    token_payload = __import__("json").loads((tmp_path / "run" / "invocation_token.json").read_text())
    assert token_payload["argv"] == result["argv"] and token_payload["approval_manifest_size_bytes"] == 1
    assert token_payload["approval_sha256_requested"] == approval and token_payload["approval_sha256_observed"] == approval
    assert token_payload["evidence_artifacts"] == evidence and len(token_payload["evidence_artifacts"]) == 3 and all(x["source"] and x["copy"] and x["size_bytes"] and x["sha256"] for x in token_payload["evidence_artifacts"])
    assert token_payload["input_receipt_sha256"] == MODULE.RECEIPT_SHA and token_payload["solver_sha256"] == MODULE.SOLVER_SHA and token_payload["gomp_sha256"] == MODULE.GOMP_SHA
    assert result["token_artifact"]["filename"] == "invocation_token.json" and result["token_revalidation_passed"]
    sealed = __import__("json").loads(receipt.read_text())
    assert sealed["token_artifact"] == result["token_artifact"]
    assert sealed["approval_sha256_requested"] == result["approval_sha256_requested"] == approval
    assert sealed["approval_sha256_observed"] == result["approval_sha256_observed"] == approval
    assert sealed["token_revalidation_passed"] is result["token_revalidation_passed"]
    assert events == ["create", ("Popen", 0xC, MODULE.subprocess.DEVNULL), "assign", "resume", "monitor", "active0", "close"]

def test_terminate_tree_rc0_but_job_still_active_is_not_confirmed(monkeypatch):
    class P:
        pid = 3
        def __init__(self): self.alive = True; self.returncode = -9
        def poll(self): return None if self.alive else self.returncode
        def kill(self): self.alive = False
        def wait(self, timeout=None): self.alive = False
    class K:
        def TerminateJobObject(self, job, code): return 1
    monkeypatch.setattr(MODULE.os, "name", "nt")
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(MODULE.ctypes, "windll", type("W", (), {"kernel32": K()})(), raising=False)
    monkeypatch.setattr(MODULE, "_job_active_count", lambda job: 2)
    ev, detail = MODULE._terminate_tree(P(), object())
    assert ev["taskkill_return_code"] == 0 and ev["primary_termination_confirmed"]
    assert ev["final_job_active_processes"] == 2 and not ev["tree_termination_confirmed"] and not ev["termination_confirmed"]
    assert "tree termination unconfirmed" in detail

def test_monitor_fast_exit_collects_initial_and_final_ws(monkeypatch, tmp_path):
    class Exited:
        pid = 1; _handle = 1
        def poll(self): return 0
    monkeypatch.setattr(MODULE.os, "name", "nt")
    samples = iter([100, 200]); monkeypatch.setattr(MODULE, "_working_set", lambda p: next(samples))
    monkeypatch.setattr(MODULE, "_job_active_count", lambda j: 0)
    obs = {}; out = MODULE._monitor(Exited(), tmp_path, MODULE.time.time(), object(), obs)
    assert out is obs and obs["memory_sample_count"] == 2 and obs["peak_working_set_bytes"] == 200

def test_monitor_normal_run_records_running_and_final_job_evidence(monkeypatch, tmp_path):
    class RunningThenExited:
        pid = 1; _handle = 1
        def __init__(self): self.poll_calls = 0
        def poll(self):
            self.poll_calls += 1
            return None if self.poll_calls < 4 else 0
    process = RunningThenExited(); samples = iter([10, 20, 30]); active = iter([1, 0, 0])
    monkeypatch.setattr(MODULE.os, "name", "nt")
    monkeypatch.setattr(MODULE, "_working_set", lambda p: next(samples))
    monkeypatch.setattr(MODULE, "_job_active_count", lambda j: next(active))
    monkeypatch.setattr(MODULE, "_descendants", lambda pid: set())
    monkeypatch.setattr(MODULE.time, "sleep", lambda _: None)
    obs = {}; MODULE._monitor(process, tmp_path, MODULE.time.time(), object(), obs)
    assert obs["memory_sample_count"] == 3 and obs["slow_scan_count"] == 2
    assert obs["last_scratch_bytes"] == 0 and obs["peak_scratch_bytes"] == 0
    assert obs["last_job_active_processes"] == 0 and obs["max_job_active_processes"] == 1

def test_monitor_exception_keeps_caller_observations(monkeypatch, tmp_path):
    class Running:
        pid = 1; _handle = 1
        def poll(self): return None
    monkeypatch.setattr(MODULE.os, "name", "nt")
    monkeypatch.setattr(MODULE, "_working_set", lambda p: 100)
    monkeypatch.setattr(MODULE, "_descendants", lambda pid: set())
    monkeypatch.setattr(MODULE, "_job_active_count", lambda job: 2)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _: None)
    obs = {}
    with pytest.raises(RuntimeError, match="active job process count"): MODULE._monitor(Running(), tmp_path, MODULE.time.time(), object(), obs)
    assert obs["memory_sample_count"] >= 2 and obs["slow_scan_count"] == 1 and obs["last_scratch_bytes"] == 0
    assert obs["last_job_active_processes"] == 2 and obs["max_job_active_processes"] == 2

@pytest.mark.parametrize("active", [0, 2])
def test_monitor_rejects_invalid_active_job_count_while_running(monkeypatch, tmp_path, active):
    class Running:
        pid = 1; _handle = 1
        def poll(self): return None
    monkeypatch.setattr(MODULE.os, "name", "nt")
    monkeypatch.setattr(MODULE, "_working_set", lambda p: 100)
    monkeypatch.setattr(MODULE, "_descendants", lambda pid: set())
    monkeypatch.setattr(MODULE, "_job_active_count", lambda j: active)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="active job process count"): MODULE._monitor(Running(), tmp_path, MODULE.time.time(), object(), {})

def test_approval_manifest_declares_narrow_gate_and_frozen_tail():
    manifest = __import__("json").loads((ROOT / "tools/research/d116_shadow_fastercap_approval_manifest.json").read_text())
    assert manifest["purpose"] == "narrow D116-SHADOW raw C-matrix approval"
    assert manifest["head"] == MODULE.HEAD and manifest["branch"] == "main"
    assert manifest["output_root"] == str(MODULE.OUTPUT_ROOT)
    assert manifest["argv_tail"] == ["-a0.01", "-mc3", "-d1", "-f5", "-pj", "-e", "-i"]

def test_git_gate_requires_exact_untracked_allowlist(monkeypatch):
    outputs = [MODULE.HEAD + "\n", "main\n", "\n".join("?? " + p for p in sorted(MODULE.EXPECTED_UNTRACKED)) + "\n"]
    monkeypatch.setattr(MODULE.subprocess, "check_output", lambda *args, **kwargs: outputs.pop(0))
    MODULE._git(MODULE.AUTHORITATIVE_REPO)
    outputs.extend([MODULE.HEAD + "\n", "main\n", "?? extra.txt\n"])
    with pytest.raises(ValueError, match="untracked"): MODULE._git(MODULE.AUTHORITATIVE_REPO)

def test_manifest_rejects_tampered_runner_hash(monkeypatch):
    original = MODULE.digest
    monkeypatch.setattr(MODULE, "digest", lambda path: "0" * 64 if path.name == "run_d116_shadow_fastercap.py" else original(path))
    anchor = sha256((ROOT / "tools/research/d116_shadow_fastercap_approval_manifest.json").read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="runner identity"): MODULE._approval_manifest(MODULE.AUTHORITATIVE_REPO, MODULE.OUTPUT_ROOT, anchor)

def test_manifest_rejects_alternate_output_even_when_manifest_claims_it(monkeypatch, tmp_path):
    alternate = tmp_path / "alternate-output"
    with pytest.raises(ValueError, match="fixed output"): MODULE._approval_manifest(MODULE.AUTHORITATIVE_REPO, alternate, "0" * 64)

def test_approval_sha_anchor_requires_correct_lowercase_hex():
    anchor = sha256((ROOT / "tools/research/d116_shadow_fastercap_approval_manifest.json").read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="SHA anchor"): MODULE._approval_manifest(MODULE.AUTHORITATIVE_REPO, MODULE.OUTPUT_ROOT, None)
    with pytest.raises(ValueError, match="SHA anchor"): MODULE._approval_manifest(MODULE.AUTHORITATIVE_REPO, MODULE.OUTPUT_ROOT, "A" * 64)
    approved = MODULE._approval_manifest(MODULE.AUTHORITATIVE_REPO, MODULE.OUTPUT_ROOT, anchor)
    assert approved["_requested_sha256"] == anchor and approved["_approved_sha256"] == anchor

def test_invocation_token_is_exclusive_and_fsync_sealed(tmp_path):
    token = tmp_path / "invocation_token.json"; seen = {}
    real_link = MODULE.os.link
    def observe_publish(source, target):
        seen["target_absent"] = not Path(target).exists(); seen["payload"] = Path(source).read_bytes(); return real_link(source, target)
    monkeypatch = pytest.MonkeyPatch(); monkeypatch.setattr(MODULE.os, "link", observe_publish)
    MODULE._seal_exclusive(token, b"token\n"); monkeypatch.undo()
    assert token.read_bytes() == b"token\n"
    assert seen == {"target_absent": True, "payload": b"token\n"}
    with pytest.raises(FileExistsError): MODULE._seal_exclusive(token, b"second\n")
    assert token.read_bytes() == b"token\n"

def test_source_mutation_after_run_forces_stop_receipt(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "_copy_evidence", lambda *args: [])
    class Mutating(FakeProcess):
        def __init__(self, argv, **kwargs):
            super().__init__(argv, **kwargs); (source / MODULE.FILES[0]).write_bytes(b"mutated")
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=Mutating)
    assert result["status"] == "STOP_D116_SHADOW" and not result["source_revalidation_passed"]
    assert (tmp_path / "run" / "invocation_token.json").exists()

def test_copied_input_mutation_forces_stop_receipt_and_sidecar(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None); monkeypatch.setattr(MODULE, "_copy_evidence", lambda *args: [])
    class Mutating(FakeProcess):
        def __init__(self, argv, **kwargs):
            super().__init__(argv, **kwargs); Path(kwargs["cwd"], MODULE.FILES[0]).write_bytes(b"mutated")
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=Mutating)
    assert result["status"] == "STOP_D116_SHADOW" and not result["copied_revalidation_passed"]
    receipt = tmp_path / "run" / "d116_shadow_fastercap_receipt.json"; assert receipt.exists() and receipt.with_name(receipt.name + ".sha256").exists()

def test_keyboard_interrupt_after_launch_is_stopped_and_token_preserved(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None); monkeypatch.setattr(MODULE, "_copy_evidence", lambda *args: [])
    class Running(FakeProcess):
        def __init__(self, argv, **kwargs): super().__init__(argv, **kwargs); self._done = False
    def stop(process, root, started, job, observations): raise KeyboardInterrupt()
    monkeypatch.setattr(MODULE, "_terminate_tree", lambda process, job=None: ({"primary_termination_confirmed": True, "tree_termination_confirmed": True, "termination_confirmed": True}, ""))
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=Running, monitor_factory=stop)
    assert result["status"] == "STOP_D116_SHADOW" and result["stop_reason"] == "user interrupt"
    assert (tmp_path / "run" / "invocation_token.json").exists()

def test_evidence_copies_are_byte_verified(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    files = {"tools/research/run_d116_shadow_fastercap.py": b"runner", "tests/test_run_d116_shadow_fastercap.py": b"tests", "tools/research/d116_shadow_fastercap_approval_manifest.json": b"manifest"}
    for relative, payload in files.items():
        path = repo / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
    manifest = {"runner": {"path": "tools/research/run_d116_shadow_fastercap.py", "size_bytes": 6, "sha256": MODULE.digest(repo / "tools/research/run_d116_shadow_fastercap.py")}, "tests": {"path": "tests/test_run_d116_shadow_fastercap.py", "size_bytes": 5, "sha256": MODULE.digest(repo / "tests/test_run_d116_shadow_fastercap.py")}}
    manifest["_approved_size_bytes"] = (repo / "tools/research/d116_shadow_fastercap_approval_manifest.json").stat().st_size
    manifest["_approved_sha256"] = MODULE.digest(repo / "tools/research/d116_shadow_fastercap_approval_manifest.json")
    records = MODULE._copy_evidence(tmp_path / "out", repo, manifest)
    assert [r["source"] for r in records] == list(files)
    assert all((tmp_path / "out" / r["copy"]).read_bytes() == files[r["source"]] for r in records)
    (repo / "tools/research/d116_shadow_fastercap_approval_manifest.json").write_bytes(b"mutated")
    with pytest.raises(ValueError, match="manifest"): MODULE._copy_evidence(tmp_path / "out2", repo, manifest)

def test_sigint_is_replayed_only_after_receipt_and_sidecar(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch); pending = []; restores = []
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    monkeypatch.setattr(MODULE, "_install_sigint_deferral", lambda: ("state", pending))
    def restore(state, replay=False):
        restores.append((state, replay))
        assert replay and not any(not was_replayed for _, was_replayed in restores)
        receipt = tmp_path / "run" / "d116_shadow_fastercap_receipt.json"
        sidecar = receipt.with_name(receipt.name + ".sha256")
        assert receipt.exists() and sidecar.exists()
        assert sidecar.read_text().startswith(MODULE.digest(receipt) + "  " + receipt.name)
        raise KeyboardInterrupt()
    monkeypatch.setattr(MODULE, "_restore_sigint_deferral", restore)
    real_seal = MODULE._seal_exclusive
    def seal(path, payload):
        if path.name.endswith(".sha256"): pending.append(True)
        return real_seal(path, payload)
    monkeypatch.setattr(MODULE, "_seal_exclusive", seal)
    with pytest.raises(KeyboardInterrupt):
        MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=FakeProcess)
    assert pending and restores == [(('state', pending), True)]

def test_sidecar_disk_failure_propagates_and_preserves_token(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch); restores = []
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    monkeypatch.setattr(MODULE, "_install_sigint_deferral", lambda: ("state", []))
    monkeypatch.setattr(MODULE, "_restore_sigint_deferral", lambda state, replay=False: restores.append((state, replay)))
    real_seal = MODULE._seal_exclusive
    def seal(path, payload):
        if path.name.endswith(".sha256"): raise OSError("disk full")
        return real_seal(path, payload)
    monkeypatch.setattr(MODULE, "_seal_exclusive", seal)
    with pytest.raises(OSError, match="disk full"):
        MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=FakeProcess)
    assert (tmp_path / "run" / "invocation_token.json").exists() and restores == [(('state', []), False)]

def test_csv_directory_seals_stop_with_log_hashes(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    class CsvDirectory(FakeProcess):
        def __init__(self, argv, **kwargs):
            super().__init__(argv, **kwargs)
            csv = Path(kwargs["cwd"], "d116_shadow_gap_coupon.csv")
            csv.unlink(); csv.mkdir()
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=CsvDirectory)
    receipt = tmp_path / "run" / "d116_shadow_fastercap_receipt.json"
    assert result["status"] == "STOP_D116_SHADOW" and result["stdout_sha256"] and result["stderr_sha256"]
    assert result["csv_artifact"]["filename"].endswith("d116_shadow_gap_coupon.csv") and "error" in result["csv_artifact"]
    assert receipt.exists() and receipt.with_name(receipt.name + ".sha256").exists()

def test_token_mutation_seals_stop(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    class MutatingToken(FakeProcess):
        def __init__(self, argv, **kwargs):
            super().__init__(argv, **kwargs)
            Path(kwargs["cwd"]).parent.joinpath("invocation_token.json").write_bytes(b"mutated\n")
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=MutatingToken)
    assert result["status"] == "STOP_D116_SHADOW" and not result["token_revalidation_passed"]
    assert result["token_artifact"]["sha256"] != result["token_final_observation"].get("sha256")
    assert "invocation token changed" in result["artifact_error"]

def test_job_close_backstop_wait_is_recorded_without_tree_claim(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch); events = []
    monkeypatch.setattr(MODULE, "_create_job", lambda: object())
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    monkeypatch.setattr(MODULE.os, "name", "nt")
    monkeypatch.setattr(MODULE, "_assign_job", lambda *args: None)
    monkeypatch.setattr(MODULE, "_resume_primary_thread", lambda *args: None)
    monkeypatch.setattr(MODULE, "_close_handle", lambda handle: events.append("close"))
    monkeypatch.setattr(MODULE, "_terminate_tree", lambda *args: ({"termination_confirmed": False, "tree_termination_confirmed": False, "primary_termination_confirmed": False}, "tree termination unconfirmed"))
    def monitor(*args): raise RuntimeError("monitor failed")
    class P(FakeProcess):
        def wait(self, timeout=None): events.append(("wait", timeout)); return super().wait(timeout)
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=P, monitor_factory=monitor)
    obs = result["resource_observations"]
    assert result["status"] == "STOP_D116_SHADOW" and obs["job_close_attempted"] and obs["job_close_succeeded"]
    assert obs["backstop_used"] and obs["post_close_wait_attempted"] and obs["post_close_wait_confirmed"]
    assert not obs["tree_termination_confirmed"] and ("wait", 30) in events

def test_final_receipt_uses_single_parser_snapshots_after_disk_mutation(tmp_path, monkeypatch):
    source, solver = _seed_run(tmp_path, monkeypatch)
    monkeypatch.setattr(MODULE, "_create_job", lambda: None)
    monkeypatch.setattr(MODULE, "_working_set", lambda process: 100)
    approval = sha256(b"a").hexdigest()
    manifest = {"_approved_size_bytes": 1, "_approved_sha256": approval,
                "runner": {"size_bytes": 1, "sha256": approval},
                "tests": {"size_bytes": 1, "sha256": approval}}
    monkeypatch.setattr(MODULE, "validate_preflight", lambda *args: {"status": "PASS_D116_SHADOW_GAP_COUPON", "approval_manifest": manifest})
    evidence = [{"source": p, "copy": "evidence/" + p, "size_bytes": 1, "sha256": approval} for p in ("tools/research/run_d116_shadow_fastercap.py", "tests/test_run_d116_shadow_fastercap.py", "tools/research/d116_shadow_fastercap_approval_manifest.json")]
    def copy_evidence(output, *args):
        for item in evidence:
            path = output / item["copy"]; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"a")
        return evidence
    monkeypatch.setattr(MODULE, "_copy_evidence", copy_evidence)
    original_parse = MODULE._parse_stdout
    def parse_and_mutate(stdout, stderr):
        run_input = tmp_path / "run" / "input"
        (run_input / "stdout.txt").write_bytes(b"mutated stdout")
        (run_input / "d116_shadow_gap_coupon.csv").write_bytes(b"mutated csv")
        (run_input / MODULE.FILES[0]).write_bytes(b"mutated input")
        (tmp_path / "run" / evidence[0]["copy"]).write_bytes(b"mutated evidence")
        return original_parse(stdout, stderr)
    monkeypatch.setattr(MODULE, "_parse_stdout", parse_and_mutate)
    result = MODULE.run(tmp_path / "run", input_root=source, solver=solver, process_factory=FakeProcess, approval_sha256=approval)
    receipt = __import__("json").loads((tmp_path / "run" / "d116_shadow_fastercap_receipt.json").read_text())
    expected_stdout = OPTIONS.encode()
    expected_csv = ("\r\n".join(",".join("%g" % x for x in r) for r in MATRIX) + "\r\n").encode()
    assert result["status"] == "STOP_D116_SHADOW"
    assert result["stdout_artifact"]["sha256"] == sha256(expected_stdout).hexdigest()
    assert result["csv_artifact"]["sha256"] == sha256(expected_csv).hexdigest()
    assert receipt["matrix_raw"] == MATRIX.tolist() and receipt["stdout_sha256"] == result["stdout_artifact"]["sha256"]
    assert result["evidence_artifacts"][0]["sha256"] == sha256(b"mutated evidence").hexdigest()
    assert receipt["evidence_artifacts"] == result["evidence_artifacts"]
    assert result["copied_revalidation_passed"] and all(result["input_file_artifacts"][n]["sha256"] == MODULE.FILE_META[n][1] for n in MODULE.FILES)
