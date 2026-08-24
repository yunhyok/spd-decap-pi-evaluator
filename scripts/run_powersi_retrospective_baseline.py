"""Controller entry point for the approved two-phase retrospective baseline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_powersi_accuracy as accuracy

POLICY_PATH = accuracy.POLICY_PATH
ACCURACY_VALIDATOR = Path(accuracy.__file__).resolve()
EXPECTED_ACCURACY_VALIDATOR_SHA256 = "18dd2010b85dd9cf4a355ff6214119ed16fbec3f63f6fe6fa5834ed1bf732caa"


def _write_new_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _write_new(path: Path, value) -> None:
    _write_new_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def _git_state(expected_head: str) -> dict[str, object]:
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=accuracy.ROOT, capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=accuracy.ROOT, capture_output=True, text=True, check=True).stdout.strip()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=accuracy.ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if branch != "main" or status or head.casefold() != expected_head.casefold():
        raise accuracy.IntegrityError(f"expected clean main at {expected_head}, got {branch}/{head} status={status!r}")
    return {"expected_head": expected_head, "observed_head": head, "branch": branch, "clean": True}


def _registered_input(policy, case_id: str, key: str) -> Path:
    artifact = policy["cases"][case_id][key]
    path = Path(artifact["path"])
    if not path.is_file() or path.stat().st_size != artifact["size_bytes"] or accuracy._sha_file(path) != artifact["sha256"]:
        raise accuracy.IntegrityError(f"registered {key} identity is unavailable or mismatched: {path}")
    return path


def _run_logged(argv, *, env, cwd, stdout_path, stderr_path):
    with stdout_path.open("x", encoding="utf-8", newline="") as stdout, stderr_path.open("x", encoding="utf-8", newline="") as stderr:
        process = subprocess.Popen(argv, cwd=cwd, env=dict(env), stdout=stdout, stderr=stderr, text=True)
        try:
            return_code = process.wait()
        except KeyboardInterrupt as exc:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise accuracy.CancelledError(f"cancelled: {' '.join(argv)}") from exc
        return subprocess.CompletedProcess(argv, return_code)


def _blocked(out_root: Path, reason: str, *, phase: str, policy_sha256: str, phase_state: dict | None = None) -> int:
    out_root.mkdir(parents=True, exist_ok=True)
    tombstone = out_root / "blocked_partial.json"
    if not tombstone.exists():
        artifacts = []
        for path in sorted(out_root.rglob("*")):
            if path.is_file() and path != tombstone:
                basename = path.relative_to(out_root).as_posix()
                owner = "phase1" if basename.startswith("import/") or basename.startswith("phase1.") else "phase2" if basename.startswith("correlation/") or basename.startswith("phase2.") else "v6" if basename.startswith("v6.") else phase
                state = (phase_state or {}).get(owner, {})
                artifacts.append({"basename": basename, "bytes": path.stat().st_size, "sha256": accuracy._sha_file(path), "phase": owner, "exit_code": state.get("exit_code"), "cancelled": state.get("cancelled", False)})
        _write_new(tombstone, {"status": "blocked_partial", "phase": phase, "reason": reason, "policy_sha256": policy_sha256, "scoring": "refused", "phase_status": phase_state or {"phase": phase, "exit_code": None, "cancelled": phase == "cancelled"}, "artifacts": artifacts, "logs": [item for item in artifacts if item["basename"].endswith(".log")]})
    return 2


def run_controller(*, policy_path: Path, case_id: str, expected_head: str, out_root: Path) -> int:
    policy_sha256 = accuracy.EXPECTED_POLICY_SHA256
    phase_state = {"phase1": {"status": "not_started", "exit_code": None, "cancelled": False}, "phase2": {"status": "not_started", "exit_code": None, "cancelled": False}, "v6": {"status": "not_started", "exit_code": None, "cancelled": False}}
    try:
        if policy_path.resolve() != accuracy.POLICY_PATH.resolve():
            raise accuracy.IntegrityError("policy path is not the canonical approved policy")
        policy_path = accuracy.POLICY_PATH.resolve()
        if accuracy._normalized_sha(ACCURACY_VALIDATOR) != EXPECTED_ACCURACY_VALIDATOR_SHA256:
            raise accuracy.IntegrityError("accuracy validator source hash is not the pinned current file")
        policy, policy_sha256 = accuracy._load_policy(policy_path)
        case = policy["cases"][case_id]
        git_state = _git_state(expected_head)
        _registered_input(policy, case_id, "source")
        _registered_input(policy, case_id, "touchstone")
        if out_root.exists() and any(out_root.iterdir()):
            raise accuracy.IntegrityError("out-root must be new or empty")
        out_root.mkdir(parents=True, exist_ok=True)
    except (OSError, accuracy.IntegrityError, KeyError, ValueError) as exc:
        return _blocked(out_root, str(exc), phase="preflight", policy_sha256=policy_sha256, phase_state=phase_state)
    import_dir, correlation_dir = out_root / "import", out_root / "correlation"
    phase1, phase2 = accuracy.build_phase_argv(case, import_dir, correlation_dir)
    env = os.environ.copy()
    env["SPD_DECAP_PI_BLAS_THREADS"] = "1"
    try:
        p1 = _run_logged(phase1, env=env, cwd=accuracy.ROOT, stdout_path=out_root / "phase1.stdout.log", stderr_path=out_root / "phase1.stderr.log")
        phase_state["phase1"] = {"status": "passed" if p1.returncode == 0 else "failed", "exit_code": p1.returncode, "cancelled": False}
        if p1.returncode != 0:
            return _blocked(out_root, f"phase1 exit {p1.returncode}", phase="import", policy_sha256=policy_sha256, phase_state=phase_state)
        candidate = import_dir / f"{Path(case['source']['basename']).stem}_candidate.spdpi"
        import_report = import_dir / "import_save_validation_report.json"
        if not candidate.is_file() or not import_report.is_file():
            return _blocked(out_root, "phase1 artifacts missing", phase="import", policy_sha256=policy_sha256, phase_state=phase_state)
        import_binding = accuracy._validate_import_binding(import_report, candidate, case)
        p2 = _run_logged(phase2, env=env, cwd=accuracy.ROOT, stdout_path=out_root / "phase2.stdout.log", stderr_path=out_root / "phase2.stderr.log")
        phase_state["phase2"] = {"status": "passed" if p2.returncode == 0 else "failed", "exit_code": p2.returncode, "cancelled": False}
        if p2.returncode != 0:
            return _blocked(out_root, f"phase2 exit {p2.returncode}", phase="correlation", policy_sha256=policy_sha256, phase_state=phase_state)
        blas_path = correlation_dir / "blas_runtime_evidence.json"
        blas_binding = accuracy._validate_blas_artifact(blas_path)
        report_path = correlation_dir / "correlation_report.json"
        report_raw = report_path.read_bytes()
        report = accuracy._json_bytes(report_raw, report_path)
        correlation_binding = accuracy._validate_correlation_binding(report_path, import_binding, case, report=report)
        snapshot_fd, snapshot_name = tempfile.mkstemp(prefix="owned-report-", suffix=".json", dir=out_root)
        try:
            with os.fdopen(snapshot_fd, "wb") as snapshot:
                snapshot.write(report_raw); snapshot.flush(); os.fsync(snapshot.fileno())
            v6 = _run_logged([sys.executable, str(accuracy.V6_VALIDATOR), snapshot_name], env=env, cwd=accuracy.ROOT, stdout_path=out_root / "v6.stdout.log", stderr_path=out_root / "v6.stderr.log")
        finally:
            try: os.unlink(snapshot_name)
            except FileNotFoundError: pass
        phase_state["v6"] = {"status": "passed" if v6.returncode == 0 else "failed", "exit_code": v6.returncode, "cancelled": False}
        if v6.returncode != 0:
            return _blocked(out_root, f"v6 exit {v6.returncode}", phase="validation", policy_sha256=policy_sha256, phase_state=phase_state)
        score = accuracy.score_report(policy, case_id, report)
        artifact = accuracy._artifact
        evidence = {"schema_version": "powersi-retrospective-execution-evidence-v1", "policy": {"path": str(policy_path), "sha256": policy_sha256}, "benchmark_base": {"path": str(accuracy.BENCHMARK), "sha256": accuracy._normalized_sha(accuracy.BENCHMARK)}, "benchmark_adapter": {"path": str(accuracy.BENCHMARK_ADAPTER), "sha256": accuracy._normalized_sha(accuracy.BENCHMARK_ADAPTER)}, "validator_v6": {"path": str(accuracy.V6_VALIDATOR), "sha256": accuracy._normalized_sha(accuracy.V6_VALIDATOR)}, "git": git_state, "runtime": {"worker": 1, "retries": 0, "env": {"SPD_DECAP_PI_BLAS_THREADS": "1"}, "rails": list(accuracy.RAILS), "modes": [10, 12]}, "inputs": {"source": case["source"], "touchstone": case["touchstone"]}, "phase1": {"status": "passed", "argv": phase1, "exit_code": p1.returncode, "cancelled": False, "stdout": artifact(out_root / "phase1.stdout.log", root=out_root), "stderr": artifact(out_root / "phase1.stderr.log", root=out_root), "import_report": artifact(import_report, root=out_root), "candidate": artifact(candidate, root=out_root)}, "phase2": {"status": "passed", "argv": phase2, "exit_code": p2.returncode, "cancelled": False, "stdout": artifact(out_root / "phase2.stdout.log", root=out_root), "stderr": artifact(out_root / "phase2.stderr.log", root=out_root), "correlation_report": artifact(report_path, root=out_root), "blas_runtime": artifact(blas_path, root=out_root)}, "v6": {"status": "passed", "argv": [sys.executable, str(accuracy.V6_VALIDATOR), "<owned-report-snapshot>"], "exit_code": v6.returncode, "cancelled": False, "stdout": artifact(out_root / "v6.stdout.log", root=out_root), "stderr": artifact(out_root / "v6.stderr.log", root=out_root)}}
        evidence["accuracy_validator"] = {"path": str(ACCURACY_VALIDATOR), "sha256": EXPECTED_ACCURACY_VALIDATOR_SHA256}
        evidence_sha256 = accuracy._sha_bytes(accuracy._canonical(evidence))
        sidecar = {"schema_version": "powersi-accuracy-sidecar-v1", "status": score["status"], "claim_scope": score["claim_scope"], "policy_sha256": policy_sha256, "validator_v6_sha256": accuracy._normalized_sha(accuracy.V6_VALIDATOR), "report_sha256": accuracy._sha_bytes(report_raw), "candidate_sha256": accuracy._sha_file(candidate), "evidence_sha256": evidence_sha256, "score": score}
        sidecar_path = out_root / "accuracy_sidecar.json"; _write_new(sidecar_path, sidecar)
        artifact_paths = [candidate, import_report, report_path, blas_path, out_root / "phase1.stdout.log", out_root / "phase1.stderr.log", out_root / "phase2.stdout.log", out_root / "phase2.stderr.log", out_root / "v6.stdout.log", out_root / "v6.stderr.log", sidecar_path]
        artifacts = [artifact(path, root=out_root) for path in artifact_paths]
        manifest = {"schema_version": "powersi-retrospective-run-manifest-v1", "status": "completed", "case_id": case_id, "evidence": evidence, "evidence_sha256": evidence_sha256, "artifacts": artifacts, "accuracy_sidecar": {"basename": sidecar_path.relative_to(out_root).as_posix(), "bytes": sidecar_path.stat().st_size, "sha256": accuracy._sha_file(sidecar_path)}, "logs": [item for item in artifacts if item["basename"].endswith(".log")], "score_status": score["status"]}
        manifest_path = out_root / "run_manifest.json"; _write_new(manifest_path, manifest)
        _write_new_bytes(out_root / "run_manifest.json.sha256", (accuracy._sha_file(manifest_path) + "  run_manifest.json\n").encode("ascii"))
        return 0 if score["status"] == "PASS" else 2
    except accuracy.CancelledError as exc:
        for state in phase_state.values():
            if state["status"] == "not_started":
                state.update(status="cancelled", cancelled=True)
        return _blocked(out_root, str(exc), phase="cancelled", policy_sha256=policy_sha256, phase_state=phase_state)
    except (OSError, subprocess.SubprocessError, accuracy.IntegrityError, KeyError, ValueError) as exc:
        return _blocked(out_root, str(exc), phase="controller", policy_sha256=policy_sha256, phase_state=phase_state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--case", required=True, choices=("260729", "260804"))
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--out-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        return run_controller(policy_path=args.policy, case_id=args.case, expected_head=args.expected_head, out_root=args.out_root)
    except (OSError, subprocess.SubprocessError, accuracy.IntegrityError, KeyError, ValueError) as exc:
        print(f"BLOCKED_PARTIAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
