"""Offline verifier for one approved, hash-bound retrospective run snapshot.

This module never launches the benchmark.  The controller entry point is
``run_powersi_retrospective_baseline.py``; this CLI only verifies a manifest,
sidecar, and their owned artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "validation-policies" / "powersi_accuracy_v1.json"
BENCHMARK = ROOT / "scripts" / "benchmark_raw_spd_powersi_correlation.py"
BENCHMARK_ADAPTER = ROOT / "scripts" / "benchmark_raw_spd_powersi_correlation_v6.py"
V6_VALIDATOR = ROOT / "scripts" / "validate_correlation_v6.py"
EXPECTED_POLICY_SHA256 = "c362acb01ef28cefbbd1d32753f86bccafbdd53355b42eda83c03a6ea810698b"
RAILS = (
    "ADC_VDD_180_VQPS_OTP_TOP_AON/0",
    "ADC_VDD_180_VQPS_SYS_0_AON/0",
    "ADC_VDD_180_VQPS_SYS_1_AON/0",
    "ADC_VDD_180_VQPS_SYS_2_AON/0",
    "ADC_VDD_180_VQPS_SYS_3_AON/0",
    "ADC_VDD_180_VQPS_OTP_TOP_AON/1",
    "ADC_VDD_180_VQPS_SYS_0_AON/1",
    "ADC_VDD_180_VQPS_SYS_1_AON/1",
    "ADC_VDD_180_VQPS_SYS_2_AON/1",
    "ADC_VDD_180_VQPS_SYS_3_AON/1",
    "ADC_VDD_055_VTRIP/0",
    "ADC_VDD_055_VTRIP/1",
    "ADC_VDD_070_VINT/0",
    "ADC_VDD_070_VINT/1",
    "ADC_VDD_075_VCPU/0",
    "ADC_VDD_075_VCPU/1",
)
EXPECTED_LOG_BASENAMES = (
    "phase1.stdout.log", "phase1.stderr.log", "phase2.stdout.log",
    "phase2.stderr.log", "v6.stdout.log", "v6.stderr.log",
)


def _expected_artifact_basenames(source_basename: str) -> tuple[str, ...]:
    stem = Path(source_basename).stem
    return (
        f"import/{stem}_candidate.spdpi",
        "import/import_save_validation_report.json",
        "correlation/correlation_report.json",
        "correlation/blas_runtime_evidence.json",
        *EXPECTED_LOG_BASENAMES,
        "accuracy_sidecar.json",
    )


class IntegrityError(ValueError):
    """Raised when trusted input identity or output binding is invalid."""


class CancelledError(RuntimeError):
    """Raised when a benchmark process is cancelled by the operator."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise IntegrityError(f"non-finite JSON constant {value!r}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _artifact(path: Path, *, exit_code: int | None = 0, cancelled: bool = False, root: Path | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise IntegrityError(f"missing artifact: {path}")
    return {"basename": path.relative_to(root).as_posix() if root is not None else path.name, "bytes": path.stat().st_size, "sha256": _sha_file(path), "exit_code": exit_code, "cancelled": cancelled}


def _normalized_sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def _json(path: Path) -> dict[str, Any]:
    return _json_bytes(path.read_bytes(), path)


def _json_bytes(raw: bytes, source: Path | str) -> dict[str, Any]:
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_constant,
    )
    if type(value) is not dict:
        raise IntegrityError(f"{source} must contain one JSON object")
    return value


def _manifest_path(root: Path, relative_name: str) -> Path:
    if type(relative_name) is not str or not relative_name or Path(relative_name).is_absolute():
        raise IntegrityError("manifest artifact path must be relative")
    candidate = (root / Path(relative_name)).resolve()
    if candidate != root.resolve() and root.resolve() not in candidate.parents:
        raise IntegrityError("manifest artifact path escapes output root")
    return candidate


def _load_policy(path: Path = POLICY_PATH) -> tuple[dict[str, Any], str]:
    if path.resolve() != POLICY_PATH.resolve():
        raise IntegrityError("policy path is not the canonical approved policy")
    raw = path.read_bytes()
    policy = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_constant)
    if type(policy) is not dict or policy.get("schema_version") != "powersi-accuracy-policy-v1":
        raise IntegrityError("policy schema is not powersi-accuracy-policy-v1")
    canonical_sha = _sha_bytes(_canonical(policy))
    if EXPECTED_POLICY_SHA256 != "UNASSIGNED_UNTIL_FINAL_SOURCE" and canonical_sha != EXPECTED_POLICY_SHA256:
        raise IntegrityError("policy canonical SHA does not match the pinned validator identity")
    _require_policy_shape(policy)
    return policy, canonical_sha


def _require_policy_shape(policy: Mapping[str, Any]) -> None:
    def exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
        if type(value) is not dict or set(value) != keys:
            raise IntegrityError(f"{label} keys/types are not exact")
        return value

    required = {"schema_version", "policy_name", "policy_hash_semantics", "approval_basis", "release_identity", "benchmark_base_source", "benchmark_adapter", "validator_v6", "run_manifest_schema", "runtime", "grids", "rails", "metrics", "cases", "partition", "execution"}
    if set(policy) != required:
        raise IntegrityError("policy top-level keys are not exact")
    if policy["policy_hash_semantics"] != "canonical_json_utf8_sort_keys_compact":
        raise IntegrityError("unsupported policy hash semantics")
    approval = exact(policy["approval_basis"], {"commit", "product_purpose_document_version", "evaluation_accuracy_document_version", "claim_scope", "excluded_scope"}, "approval_basis")
    if approval["commit"] != "027ac7a09a3eded15f45c41860945f9d4c7f488d" or approval["claim_scope"] != "retrospective_accuracy_not_unseen_generalization" or approval["excluded_scope"] != ["P5", "unseen", "final_signoff"]:
        raise IntegrityError("approval basis mismatch")
    if type(approval["product_purpose_document_version"]) is not str or type(approval["evaluation_accuracy_document_version"]) is not str:
        raise IntegrityError("approval document versions are invalid")
    identity = exact(policy["release_identity"], {"app_version", "solver_version", "solver_profile", "convergence_policy", "compiler_version", "static_compiler_sha256"}, "release_identity")
    if identity != {"app_version": "0.23.0", "solver_version": "modal-mvp-0.8.4", "solver_profile": "layerwise_admittance_v1", "convergence_policy": "adaptive-frequency-modal-v5", "compiler_version": "layer-surface-adjacent-y-island-finite-via-termination-kron-v8", "static_compiler_sha256": "3894d6174dd6765f5f830bc19511cd1bb843f3f88b1b305ceb5be32c52056615"}:
        raise IntegrityError("release identity mismatch")
    benchmark_source = exact(policy["benchmark_base_source"], {"path", "hash_semantics", "sha256"}, "benchmark_base_source")
    benchmark_adapter = exact(policy["benchmark_adapter"], {"path", "hash_semantics", "sha256"}, "benchmark_adapter")
    validator_v6 = exact(policy["validator_v6"], {"path", "hash_semantics", "sha256"}, "validator_v6")
    for label, identity, expected_path in (("benchmark_base_source", benchmark_source, "scripts/benchmark_raw_spd_powersi_correlation.py"), ("benchmark_adapter", benchmark_adapter, "scripts/benchmark_raw_spd_powersi_correlation_v6.py"), ("validator_v6", validator_v6, "scripts/validate_correlation_v6.py")):
        if identity["path"] != expected_path or identity["hash_semantics"] != "utf8_lf_normalized" or type(identity["sha256"]) is not str or len(identity["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in identity["sha256"]):
            raise IntegrityError(f"{label} identity shape is invalid")
    runtime = exact(policy["runtime"], {"expected_head_contract", "branch", "clean_required", "worker", "blas_env", "blas_required_num_threads", "retries", "modal_modes", "modal_ceiling_index", "modal_source_index", "terminal_reuse_index", "no_fallback", "partial_status"}, "runtime")
    if runtime["expected_head_contract"] != "required_cli_exact_current_main_commit" or runtime["branch"] != "main" or runtime["clean_required"] is not True or runtime["blas_env"] != {"SPD_DECAP_PI_BLAS_THREADS": "1"} or runtime["modal_modes"] != [10, 12] or runtime["modal_ceiling_index"] != 12 or runtime["modal_source_index"] != 10 or runtime["terminal_reuse_index"] != 12 or runtime["no_fallback"] is not True:
        raise IntegrityError("runtime policy mismatch")
    grids = exact(policy["grids"], {"critical", "diagnostic"}, "grids")
    if grids != {"critical": {"low_hz": 100000.0, "high_hz": 100000000.0, "points": 241, "spacing": "log"}, "diagnostic": {"low_hz": 1000.0, "high_hz": 1000000000.0, "points": 481, "spacing": "log"}}:
        raise IntegrityError("grid policy mismatch")
    rails = exact(policy["rails"], {"ordered", "bare_vqps_count", "loaded_count"}, "rails")
    if type(rails) is not dict or tuple(rails.get("ordered", ())) != RAILS or rails.get("bare_vqps_count") != 10 or rails.get("loaded_count") != 6:
        raise IntegrityError("policy rail manifest is not exact")
    if runtime.get("worker") != 1 or runtime.get("retries") != 0 or runtime.get("blas_required_num_threads") != 1 or runtime.get("partial_status") != "blocked_partial":
        raise IntegrityError("policy runtime contract is invalid")
    if benchmark_source.get("sha256") != _normalized_sha(BENCHMARK):
        raise IntegrityError("benchmark base source hash does not match policy")
    if benchmark_adapter.get("sha256") != _normalized_sha(BENCHMARK_ADAPTER):
        raise IntegrityError("benchmark adapter hash does not match policy")
    if policy["validator_v6"].get("sha256") != _normalized_sha(V6_VALIDATOR):
        raise IntegrityError("v6 validator hash does not match policy")
    metrics = exact(policy["metrics"], {"magnitude_db", "phase_deg", "complex_uohm", "rail_gates", "resonance", "macro"}, "metrics")
    gates = exact(metrics["rail_gates"], {"low_frequency_offset_db", "magnitude_max_abs_db", "phase_rms_deg", "phase_max_abs_deg", "strict_reference_below_ohm", "complex_rms_uohm", "complex_p95_uohm", "zero_samples"}, "rail_gates")
    if gates != {
        "low_frequency_offset_db": {"max": 1.0},
        "magnitude_max_abs_db": {"max": 2.0},
        "phase_rms_deg": {"max": 7.0},
        "phase_max_abs_deg": {"max": 15.0},
        "strict_reference_below_ohm": 0.001,
        "complex_rms_uohm": {"max": 100.0},
        "complex_p95_uohm": {"max": 200.0},
        "zero_samples": "N/A and excluded from pass/fail and macro",
    }:
        raise IntegrityError("low-Z policy mismatch")
    if metrics["resonance"] != {"grid": "critical", "interior_local_maximum": "magnitude >= both neighbors; band edges excluded", "dominant": "maximum magnitude; ties choose lowest frequency", "reference_without_peak": "N/A", "reference_peak_model_without_peak": "FAIL", "frequency_error_percent_max": 10.0, "amplitude_error_db_max": 2.0}:
        raise IntegrityError("resonance policy mismatch")
    macro = exact(metrics["macro"], {"aggregation", "260729", "260804", "counts_required"}, "macro")
    if macro["counts_required"] != ["applicable", "pass", "fail", "N/A"]:
        raise IntegrityError("macro count policy mismatch")
    if macro["260729"] != {"bare_vqps_db_max": 1.0, "loaded_db_max": 1.0} or macro["260804"] != {"bare_vqps_db_max": 1.25, "loaded_db_max": 1.25}:
        raise IntegrityError("macro threshold policy mismatch")
    cases = policy["cases"]
    if set(cases) != {"260729", "260804"}:
        raise IntegrityError("case set mismatch")
    for case_id, case in cases.items():
        exact(case, {"role", "source", "touchstone", "candidate", "strata"}, f"case {case_id}")
        exact(case["source"], {"basename", "path", "size_bytes", "sha256"}, f"case {case_id}.source")
        exact(case["touchstone"], {"basename", "path", "size_bytes", "sha256", "ports"}, f"case {case_id}.touchstone")
        expected_role = {"260729": "retrospective_development", "260804": "retrospective_design_holdout"}[case_id]
        if case["role"] != expected_role or case["candidate"] != "absent/must_be_generated" or case["strata"] != ["site0", "site1", "loaded"]:
            raise IntegrityError(f"case {case_id} candidate/strata mismatch")
        for label, artifact in (("source", case["source"]), ("touchstone", case["touchstone"])):
            if type(artifact["basename"]) is not str or type(artifact["path"]) is not str or type(artifact["size_bytes"]) is not int or isinstance(artifact["size_bytes"], bool) or artifact["size_bytes"] <= 0 or type(artifact["sha256"]) is not str or len(artifact["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in artifact["sha256"]):
                raise IntegrityError(f"case {case_id}.{label} registered identity shape is invalid")
        if case["touchstone"]["ports"] != 92:
            raise IntegrityError(f"case {case_id}.touchstone port count mismatch")
    partition = exact(policy["partition"], {"p1", "p2", "p3_p4", "p5"}, "partition")
    if partition != {
        "p1": "blocked_missing_160_port_runner_or_memory_safe_preprocessing",
        "p2": "blocked_weighting_reference_plane_deembedding",
        "p3_p4": "blocked_reference_unavailable_and_multifactor_solver_state_confound",
        "p5": "missing_mandatory_for_final_generalization",
    }:
        raise IntegrityError("partition policy is not exact")
    execution = exact(policy["execution"], {"fresh_import_args", "correlation_args", "output_dirs", "argv_order", "cancel_error_resource", "candidate_binding", "blas_artifact"}, "execution")
    if execution != {
        "fresh_import_args": ["--import-save-only"],
        "correlation_args": ["--reuse-candidate", "--reuse-candidate-import-report", "--solver-profile", "layerwise_admittance_v1", "--modal-max-index", "10", "--modal-max-index", "12", "--modal-ceiling-index", "12", "--require-all-converged", "--require-terminal-complete-reuse"],
        "output_dirs": "new_empty",
        "argv_order": "exact_two_phase_templates_and_rails_ordered",
        "cancel_error_resource": "blocked_partial",
        "candidate_binding": "fresh_import_then_cross_bound_to_correlation",
        "blas_artifact": "correlation/blas_runtime_evidence.json",
    }:
        raise IntegrityError("execution policy contract is not exact")


def build_phase_argv(case: Mapping[str, Any], import_dir: Path, correlation_dir: Path) -> tuple[list[str], list[str]]:
    source = case["source"]
    touchstone = case["touchstone"]
    candidate = import_dir / f"{Path(source['basename']).stem}_candidate.spdpi"
    import_report = import_dir / "import_save_validation_report.json"
    phase1 = [sys.executable, str(BENCHMARK_ADAPTER), "--spd", source["path"], "--out-dir", str(import_dir), "--solver-profile", "layerwise_admittance_v1", "--import-save-only"]
    phase2 = [sys.executable, str(BENCHMARK_ADAPTER), "--spd", source["path"], "--touchstone", touchstone["path"], "--out-dir", str(correlation_dir), "--reuse-candidate", str(candidate), "--reuse-candidate-import-report", str(import_report), "--solver-profile", "layerwise_admittance_v1", "--modal-max-index", "10", "--modal-max-index", "12", "--modal-ceiling-index", "12", "--require-all-converged", "--require-terminal-complete-reuse"]
    for rail in RAILS:
        phase2.extend(("--rail", rail))
    return phase1, phase2


def verify_existing(*, policy_path: Path, case_id: str, manifest_path: Path, report_path: Path, sidecar_path: Path) -> dict[str, Any]:
    """Verify one complete owned snapshot without launching a process."""

    policy, policy_sha256 = _load_policy(policy_path)
    manifest_raw = manifest_path.read_bytes()
    manifest = _json_bytes(manifest_raw, manifest_path)
    manifest_digest_path = manifest_path.with_name("run_manifest.json.sha256")
    if not manifest_digest_path.is_file():
        raise IntegrityError("manifest SHA sibling is missing")
    manifest_digest_raw = manifest_digest_path.read_bytes()
    expected_manifest_digest = f"{_sha_bytes(manifest_raw)}  run_manifest.json\n".encode("ascii")
    if manifest_digest_raw != expected_manifest_digest:
        raise IntegrityError("manifest SHA sibling mismatch")
    report_raw = report_path.read_bytes()
    report = _json_bytes(report_raw, report_path)
    sidecar_raw = sidecar_path.read_bytes()
    sidecar = _json_bytes(sidecar_raw, sidecar_path)
    required_manifest = {"schema_version", "status", "case_id", "evidence", "evidence_sha256", "artifacts", "accuracy_sidecar", "logs", "score_status"}
    if set(manifest) != required_manifest:
        raise IntegrityError("manifest keys are not exact")
    if manifest["schema_version"] != "powersi-retrospective-run-manifest-v1" or manifest["status"] != "completed" or manifest["case_id"] != case_id:
        raise IntegrityError("manifest identity/status mismatch")
    evidence = manifest["evidence"]
    if type(evidence) is not dict or set(evidence) != {"schema_version", "policy", "benchmark_base", "benchmark_adapter", "validator_v6", "accuracy_validator", "git", "runtime", "inputs", "phase1", "phase2", "v6"}:
        raise IntegrityError("execution evidence keys are not exact")
    for label, expected_keys in (("phase1", {"status", "argv", "exit_code", "cancelled", "stdout", "stderr", "import_report", "candidate"}), ("phase2", {"status", "argv", "exit_code", "cancelled", "stdout", "stderr", "correlation_report", "blas_runtime"}), ("v6", {"status", "argv", "exit_code", "cancelled", "stdout", "stderr"})):
        if type(evidence[label]) is not dict or set(evidence[label]) != expected_keys:
            raise IntegrityError(f"{label} evidence keys are not exact")
    for label, expected_path, expected_sha in (
        ("policy", POLICY_PATH, policy_sha256),
        ("benchmark_base", BENCHMARK, _normalized_sha(BENCHMARK)),
        ("benchmark_adapter", BENCHMARK_ADAPTER, _normalized_sha(BENCHMARK_ADAPTER)),
        ("validator_v6", V6_VALIDATOR, _normalized_sha(V6_VALIDATOR)),
        ("accuracy_validator", Path(__file__).resolve(), _normalized_sha(Path(__file__).resolve())),
    ):
        identity = evidence[label]
        if type(identity) is not dict or set(identity) != {"path", "sha256"} or type(identity.get("path")) is not str or type(identity.get("sha256")) is not str or not re.fullmatch(r"[0-9a-f]{64}", identity.get("sha256")) or identity["path"] != str(expected_path.resolve()) or identity["sha256"] != expected_sha:
            raise IntegrityError(f"evidence {label} identity mismatch")
    evidence_sha256 = _sha_bytes(_canonical(evidence))
    if manifest["evidence_sha256"] != evidence_sha256:
        raise IntegrityError("manifest evidence hash mismatch")
    if evidence.get("schema_version") != "powersi-retrospective-execution-evidence-v1":
        raise IntegrityError("evidence policy/benchmark/v6 identity mismatch")
    runtime = evidence.get("runtime", {})
    if type(runtime) is not dict or set(runtime) != {"worker", "retries", "env", "rails", "modes"} or runtime.get("worker") != 1 or runtime.get("retries") != 0 or runtime.get("env") != {"SPD_DECAP_PI_BLAS_THREADS": "1"} or tuple(runtime.get("rails", ())) != RAILS or runtime.get("modes") != [10, 12]:
        raise IntegrityError("evidence runtime contract mismatch")
    case_for_argv = policy["cases"][case_id]
    expected_phase1, expected_phase2 = build_phase_argv(case_for_argv, manifest_path.parent / "import", manifest_path.parent / "correlation")
    if evidence.get("phase1", {}).get("argv") != expected_phase1 or evidence.get("phase2", {}).get("argv") != expected_phase2:
        raise IntegrityError("evidence phase argv differs from exact controller argv")
    expected_v6 = [sys.executable, str(V6_VALIDATOR), "<owned-report-snapshot>"]
    if evidence["v6"].get("argv") != expected_v6:
        raise IntegrityError("evidence v6 argv differs from exact validator argv")
    git = evidence.get("git", {})
    if type(git) is not dict or set(git) != {"expected_head", "observed_head", "branch", "clean"} or type(git.get("expected_head")) is not str or not re.fullmatch(r"[0-9a-f]{40}", git.get("expected_head")) or git.get("observed_head") != git.get("expected_head") or git.get("branch") != "main" or git.get("clean") is not True:
        raise IntegrityError("evidence git state is not clean expected main")
    for phase in ("phase1", "phase2", "v6"):
        block = evidence.get(phase, {})
        if block.get("status") != "passed" or block.get("exit_code") != 0 or block.get("cancelled") is not False:
            raise IntegrityError(f"evidence {phase} status is incomplete")
    if evidence.get("phase1", {}).get("exit_code") != 0 or evidence.get("phase2", {}).get("exit_code") != 0 or evidence.get("v6", {}).get("exit_code") != 0:
        raise IntegrityError("evidence phase exit status is not complete")
    if evidence.get("phase1", {}).get("cancelled") or evidence.get("phase2", {}).get("cancelled") or evidence.get("v6", {}).get("cancelled"):
        raise IntegrityError("evidence records a cancelled completed run")
    if sidecar.get("evidence_sha256") != evidence_sha256 or sidecar.get("policy_sha256") != policy_sha256:
        raise IntegrityError("sidecar evidence/policy binding mismatch")
    if sidecar.get("report_sha256") != _sha_bytes(report_raw):
        raise IntegrityError("sidecar report hash mismatch")
    if sidecar.get("validator_v6_sha256") != _normalized_sha(V6_VALIDATOR):
        raise IntegrityError("sidecar v6 hash mismatch")
    if sidecar.get("status") not in {"PASS", "FAIL"}:
        raise IntegrityError("sidecar status invalid")
    sidecar_keys = {"schema_version", "status", "claim_scope", "policy_sha256", "validator_v6_sha256", "report_sha256", "candidate_sha256", "evidence_sha256", "score"}
    if set(sidecar) != sidecar_keys or sidecar.get("schema_version") != "powersi-accuracy-sidecar-v1":
        raise IntegrityError("sidecar keys/schema are not exact")
    artifacts = manifest["artifacts"]
    expected_source = policy["cases"][case_id]["source"]
    expected_artifact_basenames = _expected_artifact_basenames(expected_source["basename"])
    if type(artifacts) is not list or tuple(item.get("basename") for item in artifacts if type(item) is dict) != expected_artifact_basenames or len(artifacts) != len(expected_artifact_basenames):
        raise IntegrityError("manifest artifact set/order is not exact")
    artifact_snapshots: dict[str, bytes] = {}
    artifact_items: dict[str, dict[str, Any]] = {}
    for item, expected_basename in zip(artifacts, expected_artifact_basenames):
        if type(item) is not dict or set(item) != {"basename", "bytes", "sha256", "exit_code", "cancelled"}:
            raise IntegrityError("manifest artifact identity schema mismatch")
        if type(item["basename"]) is not str or type(item["bytes"]) is not int or isinstance(item["bytes"], bool) or item["bytes"] < 0 or type(item["sha256"]) is not str or len(item["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in item["sha256"]):
            raise IntegrityError("manifest artifact identity types are invalid")
        if item["basename"] != expected_basename or item["exit_code"] != 0 or item["cancelled"] is not False:
            raise IntegrityError("manifest artifact phase status is not exact")
        path = _manifest_path(manifest_path.parent, item["basename"])
        if item["basename"] == "accuracy_sidecar.json":
            raw = sidecar_raw
        elif item["basename"] == "correlation/correlation_report.json":
            raw = report_raw
        else:
            raw = path.read_bytes()
        artifact_snapshots[item["basename"]] = raw
        artifact_items[item["basename"]] = item
        if len(raw) != item["bytes"] or _sha_bytes(raw) != item["sha256"]:
            raise IntegrityError(f"manifest artifact mismatch: {path}")
    sidecar_identity = manifest.get("accuracy_sidecar")
    sidecar_basename = sidecar_path.relative_to(manifest_path.parent).as_posix()
    if type(sidecar_identity) is not dict or set(sidecar_identity) != {"basename", "bytes", "sha256"} or type(sidecar_identity.get("basename")) is not str or type(sidecar_identity.get("bytes")) is not int or isinstance(sidecar_identity.get("bytes"), bool) or type(sidecar_identity.get("sha256")) is not str or Path(sidecar_identity["basename"]).as_posix() != sidecar_basename or sidecar_identity["bytes"] != len(sidecar_raw) or sidecar_identity["sha256"] != _sha_bytes(sidecar_raw):
        raise IntegrityError("manifest accuracy sidecar binding mismatch")
    if report_path.resolve() != _manifest_path(manifest_path.parent, "correlation/correlation_report.json"):
        raise IntegrityError("report path is not the exact manifest correlation artifact")
    if sidecar_path.resolve() != _manifest_path(manifest_path.parent, "accuracy_sidecar.json"):
        raise IntegrityError("sidecar path is not the exact manifest sidecar artifact")
    import_item = artifact_items["import/import_save_validation_report.json"]
    import_path = _manifest_path(manifest_path.parent, import_item["basename"])
    import_report = _json_bytes(artifact_snapshots[import_item["basename"]], import_path)
    if import_report.get("report_version") != 1 or import_report.get("mode") != "import_save_only" or import_report.get("status") != "passed" or import_report.get("import", {}).get("mode") != "fresh_import" or import_report.get("candidate_bundle", {}).get("reuse_requested") is not False:
        raise IntegrityError("import report is not a passed fresh import")
    import_source = import_report.get("source", {})
    if (import_source.get("basename"), import_source.get("size_bytes"), import_source.get("sha256")) != (expected_source["basename"], expected_source["size_bytes"], expected_source["sha256"]):
        raise IntegrityError("import report source binding mismatch")
    atomic = import_report.get("atomic_save_load_validation", {})
    for field in ("archive_manifest_and_member_hashes_validated", "scenario_schema_validated_after_reload", "source_identity_validated_after_reload", "attachment_hashes_validated_after_reload"):
        if atomic.get(field) is not True:
            raise IntegrityError(f"import report atomic flag missing: {field}")
    candidate_item = next((item for item in artifacts if item["basename"] == f"import/{Path(expected_source['basename']).stem}_candidate.spdpi"), None)
    if candidate_item is None:
        raise IntegrityError("manifest candidate artifact missing")
    candidate_file = _manifest_path(manifest_path.parent, candidate_item["basename"])
    candidate_binding = import_report.get("candidate_bundle", {})
    candidate_raw = artifact_snapshots[candidate_item["basename"]]
    if (candidate_binding.get("basename"), candidate_binding.get("size_bytes"), candidate_binding.get("sha256"), candidate_binding.get("reuse_requested")) != (candidate_file.name, len(candidate_raw), _sha_bytes(candidate_raw), False):
        raise IntegrityError("candidate file/import report binding mismatch")
    phase1_evidence = evidence["phase1"]
    if phase1_evidence.get("stdout") != artifact_items["phase1.stdout.log"] or phase1_evidence.get("stderr") != artifact_items["phase1.stderr.log"] or phase1_evidence.get("import_report") != import_item or phase1_evidence.get("candidate") != candidate_item:
        raise IntegrityError("phase1 evidence does not bind import/candidate artifacts")
    report_item = artifact_items["correlation/correlation_report.json"]
    phase2_evidence = evidence["phase2"]
    if phase2_evidence.get("stdout") != artifact_items["phase2.stdout.log"] or phase2_evidence.get("stderr") != artifact_items["phase2.stderr.log"] or phase2_evidence.get("correlation_report") != report_item:
        raise IntegrityError("phase2 evidence does not bind correlation artifact")
    blas_item = artifact_items["correlation/blas_runtime_evidence.json"]
    blas_path = _manifest_path(manifest_path.parent, blas_item["basename"])
    blas_binding = _validate_blas_artifact(blas_path, raw=artifact_snapshots[blas_item["basename"]])
    if phase2_evidence.get("blas_runtime") != blas_item or evidence["v6"].get("stdout") != artifact_items["v6.stdout.log"] or evidence["v6"].get("stderr") != artifact_items["v6.stderr.log"]:
        raise IntegrityError("phase2 evidence does not bind BLAS artifact")
    expected_logs = [artifact_items[name] for name in EXPECTED_LOG_BASENAMES]
    if manifest["logs"] != expected_logs:
        raise IntegrityError("manifest log subset is not deterministic")
    snapshot_fd, snapshot_name = tempfile.mkstemp(prefix="verify-owned-report-", suffix=".json", dir=manifest_path.parent)
    try:
        with os.fdopen(snapshot_fd, "wb") as snapshot:
            snapshot.write(report_raw)
            snapshot.flush()
            os.fsync(snapshot.fileno())
        v6 = subprocess.run([sys.executable, str(V6_VALIDATOR), snapshot_name], cwd=ROOT, capture_output=True, text=True)
    finally:
        try:
            os.unlink(snapshot_name)
        except FileNotFoundError:
            pass
    if v6.returncode != 0 or "PASS: v6 current correlation structure/conditioning validated" not in v6.stdout:
        raise IntegrityError("hash-pinned v6 validation did not pass")
    source = report.get("source", {})
    touchstone = report.get("touchstone", {})
    case = policy["cases"][case_id]
    if type(evidence.get("inputs")) is not dict or set(evidence["inputs"]) != {"source", "touchstone"} or evidence["inputs"].get("source") != case["source"] or evidence["inputs"].get("touchstone") != case["touchstone"]:
        raise IntegrityError("evidence registered input identity mismatch")
    if (source.get("basename"), source.get("size_bytes"), source.get("sha256")) != (case["source"]["basename"], case["source"]["size_bytes"], case["source"]["sha256"]):
        raise IntegrityError("report source identity differs from registered case")
    if (touchstone.get("basename"), touchstone.get("ports"), touchstone.get("full_file_sha256")) != (case["touchstone"]["basename"], case["touchstone"]["ports"], case["touchstone"]["sha256"]):
        raise IntegrityError("report reference identity differs from registered case")
    binding = report.get("candidate_import_report_binding")
    if not isinstance(binding, dict) or binding.get("status") not in {"validated", "validated_same_process_fresh_import"} or binding.get("basename") != import_item["basename"].split("/")[-1] or binding.get("sha256") != import_item["sha256"]:
        raise IntegrityError("candidate import report binding is not validated")
    score = score_report(policy, case_id, report)
    if sidecar.get("status") != score["status"] or manifest.get("score_status") != score["status"] or sidecar.get("candidate_sha256") != candidate_item["sha256"]:
        raise IntegrityError("score/candidate status binding mismatch")
    if sidecar.get("score") != score:
        raise IntegrityError("sidecar score snapshot mismatch")
    return {"status": score["status"], "policy_sha256": policy_sha256, "evidence_sha256": evidence_sha256, "score": score}


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _number(value: Any, label: str) -> float:
    if not _finite(value):
        raise IntegrityError(f"malformed/nonfinite metric: {label}")
    return float(value)


def score_report(policy: Mapping[str, Any], case_id: str, report: Mapping[str, Any]) -> dict[str, Any]:
    """Apply every approved deterministic gate to mode-10 rail metrics."""

    rail_gates = policy["metrics"]["rail_gates"]
    resonance_gates = policy["metrics"]["resonance"]
    macro_limits = {"bare": policy["metrics"]["macro"][case_id]["bare_vqps_db_max"], "loaded": policy["metrics"]["macro"][case_id]["loaded_db_max"]}
    failures: list[dict[str, Any]] = []
    gate_names = ("low_frequency_offset", "magnitude", "phase_rms", "phase_max", "sub_1_mohm", "resonance")
    counts = {
        "by_stratum": {
            stratum: {gate: {"applicable": 0, "pass": 0, "fail": 0, "N/A": 0} for gate in gate_names}
            for stratum in ("bare", "loaded")
        }
    }
    sample_totals = {"bare": 0, "loaded": 0}
    macro_results: dict[str, dict[str, Any]] = {}
    try:
        run = report["runs"]["candidate"]["10"]["rails"]
    except (KeyError, TypeError) as exc:
        raise IntegrityError("report lacks mode-10 rail metrics") from exc
    if type(run) is not dict or set(run) != set(RAILS):
        raise IntegrityError("report mode-10 rails are not exact")
    rms_values: dict[str, list[float]] = {"bare": [], "loaded": []}

    def record(stratum: str, gate: str, result: bool | None) -> None:
        bucket = counts["by_stratum"][stratum][gate]
        bucket["N/A" if result is None else "pass" if result else "fail"] += 1
        bucket["applicable"] += result is not None

    for rail in RAILS:
        stratum = "bare" if rail in RAILS[:10] else "loaded"
        outcome = run[rail]
        metrics = outcome["metrics"]
        if type(metrics) is not dict or set(metrics) != {"grid", "complex", "magnitude_db", "phase_deg", "anchors", "resonances", "sub_1_mohm"}:
            raise IntegrityError(f"malformed metric shape for {rail}")
        anchors = metrics["anchors"]
        try:
            low_offset = abs((_number(anchors["0.1MHz"]["signed_magnitude_error_db"], f"{rail}.anchor.0.1MHz") + _number(anchors["1MHz"]["signed_magnitude_error_db"], f"{rail}.anchor.1MHz")) / 2.0)
        except (KeyError, TypeError) as exc:
            raise IntegrityError(f"malformed low-frequency anchors for {rail}") from exc
        magnitude = metrics["magnitude_db"]
        phase = metrics["phase_deg"]
        max_abs_db = _number(magnitude.get("max_abs_db"), f"{rail}.magnitude.max_abs_db")
        rms_db = _number(magnitude.get("rms_db"), f"{rail}.magnitude.rms_db")
        phase_rms = _number(phase.get("rms_deg"), f"{rail}.phase.rms_deg")
        phase_max = _number(phase.get("max_abs_deg"), f"{rail}.phase.max_abs_deg")
        checks = {
            "low_frequency_offset": low_offset <= rail_gates["low_frequency_offset_db"]["max"],
            "magnitude": max_abs_db <= rail_gates["magnitude_max_abs_db"]["max"],
            "phase_rms": phase_rms <= rail_gates["phase_rms_deg"]["max"],
            "phase_max": phase_max <= rail_gates["phase_max_abs_deg"]["max"],
        }
        for gate in ("low_frequency_offset", "magnitude", "phase_rms", "phase_max"):
            record(stratum, gate, checks[gate])
            if not checks[gate]:
                failures.append({"scope": "rail", "name": rail, "gate": gate})

        sub = metrics["sub_1_mohm"]
        if type(sub) is not dict or not isinstance(sub.get("reference_grid_samples"), int) or isinstance(sub.get("reference_grid_samples"), bool) or sub["reference_grid_samples"] < 0:
            raise IntegrityError(f"malformed sub-1mOhm sample count for {rail}")
        samples = sub["reference_grid_samples"]
        sample_totals[stratum] += samples
        if samples == 0:
            record(stratum, "sub_1_mohm", None)
        else:
            sub_pass = _number(sub.get("complex_rms_uohm"), f"{rail}.sub_1_mohm.rms") <= rail_gates["complex_rms_uohm"]["max"] and _number(sub.get("complex_p95_uohm"), f"{rail}.sub_1_mohm.p95") <= rail_gates["complex_p95_uohm"]["max"]
            record(stratum, "sub_1_mohm", sub_pass)
            if not sub_pass:
                failures.append({"scope": "rail", "name": rail, "gate": "sub_1_mohm"})

        peaks = metrics["resonances"]
        if type(peaks) is not dict or set(peaks) != {"model_local_peaks", "reference_local_peaks", "model_imaginary_zero_crossings_hz", "reference_imaginary_zero_crossings_hz"}:
            raise IntegrityError(f"malformed resonance shape for {rail}")
        reference_peaks = peaks["reference_local_peaks"]
        model_peaks = peaks["model_local_peaks"]
        if type(reference_peaks) is not list or type(model_peaks) is not list:
            raise IntegrityError(f"malformed resonance rows for {rail}")
        for label, rows in (("reference", reference_peaks), ("model", model_peaks)):
            for row in rows:
                if type(row) is not dict or set(row) != {"frequency_hz", "magnitude_ohm"}:
                    raise IntegrityError(f"malformed {label} resonance row for {rail}")
                if _number(row["frequency_hz"], f"{rail}.{label}.frequency") <= 0.0 or _number(row["magnitude_ohm"], f"{rail}.{label}.magnitude") <= 0.0:
                    raise IntegrityError(f"non-positive {label} resonance row for {rail}")
        if not reference_peaks:
            resonance_pass: bool | None = None
        elif not model_peaks:
            resonance_pass = False
        else:
            model_peak = max(model_peaks, key=lambda item: (_number(item["magnitude_ohm"], f"{rail}.model.magnitude"), -_number(item["frequency_hz"], f"{rail}.model.frequency")))
            reference_peak = max(reference_peaks, key=lambda item: (_number(item["magnitude_ohm"], f"{rail}.reference.magnitude"), -_number(item["frequency_hz"], f"{rail}.reference.frequency")))
            frequency_error = 100.0 * abs(model_peak["frequency_hz"] / reference_peak["frequency_hz"] - 1.0)
            amplitude_error = abs(20.0 * math.log10(model_peak["magnitude_ohm"] / reference_peak["magnitude_ohm"]))
            resonance_pass = frequency_error <= resonance_gates["frequency_error_percent_max"] and amplitude_error <= resonance_gates["amplitude_error_db_max"]
        record(stratum, "resonance", resonance_pass)
        if resonance_pass is False:
            failures.append({"scope": "rail", "name": rail, "gate": "resonance"})
        rms_values[stratum].append(rms_db)
    for stratum, values in rms_values.items():
        macro = sum(values) / len(values) if values else math.nan
        macro_results[stratum] = {"rail_count": len(values), "value": macro, "limit": macro_limits[stratum], "status": "PASS" if _finite(macro) and macro <= macro_limits[stratum] else "FAIL"}
        if macro_results[stratum]["status"] != "PASS":
            failures.append({"scope": "stratum_macro", "name": stratum, "value": macro, "limit": macro_limits[stratum]})
    return {"status": "PASS" if not failures else "FAIL", "case_id": case_id, "counts": counts, "sub_1_mohm_sample_totals": sample_totals, "macro": macro_results, "failures": failures, "claim_scope": policy["approval_basis"]["claim_scope"]}


def _validate_import_binding(path: Path, candidate: Path, case: Mapping[str, Any]) -> dict[str, Any]:
    report = _json(path)
    if report.get("schema_version") != "candidate-import-save-validation-v1" or report.get("report_version") != 1 or report.get("app_version") != "0.23.0" or report.get("mode") != "import_save_only" or report.get("status") != "passed":
        raise IntegrityError("import report schema/mode/status mismatch")
    if report.get("frequency_solves_executed") != 0 or report.get("touchstone_read") is not False:
        raise IntegrityError("import-only report performed a solve or read Touchstone")
    if report.get("import", {}).get("mode") != "fresh_import":
        raise IntegrityError("import report is not fresh_import")
    source = report.get("source", {})
    expected_source = case["source"]
    if source.get("basename") != expected_source["basename"] or source.get("size_bytes") != expected_source["size_bytes"] or source.get("sha256") != expected_source["sha256"]:
        raise IntegrityError("import report source identity mismatch")
    binding = report.get("candidate_bundle", {})
    if binding.get("basename") != candidate.name or binding.get("size_bytes") != candidate.stat().st_size or binding.get("sha256") != _sha_file(candidate) or binding.get("reuse_requested") is not False:
        raise IntegrityError("import report candidate identity mismatch")
    atomic = report.get("atomic_save_load_validation", {})
    required = ("archive_manifest_and_member_hashes_validated", "scenario_schema_validated_after_reload", "source_identity_validated_after_reload", "attachment_hashes_validated_after_reload")
    if any(atomic.get(field) is not True for field in required):
        raise IntegrityError("import report atomic validation is incomplete")
    return {"sha256": _sha_file(path), "basename": path.name, "bytes": path.stat().st_size, "candidate_sha256": binding["sha256"], "candidate_basename": binding["basename"], "candidate_bytes": binding["size_bytes"]}


def _validate_correlation_binding(path: Path, import_binding: Mapping[str, Any], case: Mapping[str, Any], *, report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    report = _json(path) if report is None else report
    source = report.get("source", {})
    expected_source = case["source"]
    if source.get("basename") != expected_source["basename"] or source.get("size_bytes") != expected_source["size_bytes"] or source.get("sha256") != expected_source["sha256"]:
        raise IntegrityError("correlation source identity mismatch")
    touchstone = report.get("touchstone", {})
    expected_touchstone = case["touchstone"]
    if touchstone.get("basename") != expected_touchstone["basename"] or touchstone.get("ports") != 92 or touchstone.get("full_file_sha256") != expected_touchstone["sha256"]:
        raise IntegrityError("correlation reference identity mismatch")
    candidate = report.get("candidate_bundle", {})
    identity = report.get("identity", {})
    if identity.get("source_candidate_match") is not True or identity.get("source_sha256") != expected_source["sha256"] or identity.get("candidate_source_sha256") != expected_source["sha256"]:
        raise IntegrityError("correlation root source/candidate identity mismatch")
    if candidate.get("basename") != import_binding["candidate_basename"] or candidate.get("size_bytes") != import_binding["candidate_bytes"] or candidate.get("sha256") != import_binding["candidate_sha256"]:
        raise IntegrityError("correlation candidate does not cross-bind import")
    binding = report.get("candidate_import_report_binding", {})
    if binding.get("status") not in {"validated", "validated_same_process_fresh_import"}:
        raise IntegrityError("correlation import binding is not validated")
    if binding.get("basename") != import_binding["basename"] or binding.get("sha256") != import_binding["sha256"]:
        raise IntegrityError("correlation import report does not cross-bind exact import artifact")
    if tuple(report.get("score_split", {}).get("vqps_development", ())) + tuple(report.get("score_split", {}).get("vqps_holdout", ())) + tuple(report.get("score_split", {}).get("loaded_final_holdout", ())) != RAILS:
        raise IntegrityError("correlation score split is not exact")
    return {"sha256": _sha_file(path), "basename": path.name, "bytes": path.stat().st_size}


def _validate_blas_artifact(path: Path, *, raw: bytes | None = None) -> dict[str, Any]:
    raw = path.read_bytes() if raw is None else raw
    payload = _json_bytes(raw, path)
    required = {"schema_version", "status", "base_source_sha256", "adapter_source_sha256", "required_num_threads", "resolved_num_threads", "pools"}
    if set(payload) != required or payload.get("schema_version") != "powersi-blas-runtime-evidence-v1" or payload.get("status") != "validated" or payload.get("base_source_sha256") != _normalized_sha(BENCHMARK) or payload.get("adapter_source_sha256") != _normalized_sha(BENCHMARK_ADAPTER) or type(payload.get("required_num_threads")) is not int or type(payload.get("resolved_num_threads")) is not int or payload.get("required_num_threads") != 1 or payload.get("resolved_num_threads") != 1:
        raise IntegrityError("BLAS artifact identity/schema mismatch")
    pools = payload.get("pools")
    if type(pools) is not list or not pools:
        raise IntegrityError("BLAS artifact pools are empty")
    pool_keys = {"user_api", "internal_api", "version", "threading_layer", "architecture", "filepath", "num_threads"}
    ordering = []
    for pool in pools:
        if type(pool) is not dict or set(pool) != pool_keys or pool.get("user_api") != "blas" or type(pool.get("num_threads")) is not int or pool.get("num_threads") != 1 or type(pool.get("internal_api")) is not str or not pool["internal_api"].strip() or type(pool.get("version")) is not str or not pool["version"].strip():
            raise IntegrityError("BLAS artifact pool is not exact all-one")
        if any(pool[key] is not None and type(pool[key]) is not str for key in ("threading_layer", "architecture", "filepath")):
            raise IntegrityError("BLAS artifact optional pool metadata is invalid")
        ordering.append((pool.get("internal_api") or "", pool.get("filepath") or "", pool.get("version") or ""))
    if ordering != sorted(ordering):
        raise IntegrityError("BLAS artifact pool ordering is not deterministic")
    return {"basename": path.name, "bytes": len(raw), "sha256": _sha_bytes(raw), "payload": payload}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--verify-sidecar", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = _json(args.manifest)
        case_id = manifest.get("case_id")
        report_item = next(item for item in manifest.get("artifacts", ()) if item.get("basename", "").endswith("correlation_report.json"))
        report_path = _manifest_path(args.manifest.parent, report_item["basename"])
        result = verify_existing(policy_path=args.policy, case_id=case_id, manifest_path=args.manifest, report_path=report_path, sidecar_path=args.sidecar)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 2
    except (OSError, IntegrityError, KeyError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
