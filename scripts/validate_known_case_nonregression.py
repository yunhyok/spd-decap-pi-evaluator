"""Validate hash-bound regression ceilings for the two named PowerSI cases.

This is a non-regression gate, not an accuracy claim. A release requires both
the separate v5 validator to pass and the PASS sidecar produced here.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


POLICY_SHA256 = "a36679b817dfe6f2c878f05825c31e72d67044808708053eedc8b6887a247bf3"
TRACKED_V5_SHA256 = "28a23ceb2383dfaeaf2454242a2e12dbea3414d77e776c2cc97bb2f6baffffaa"
BENCHMARK_SOURCE_SHA256 = (
    "5a777379eefdafee07d087f439489f93a80cba607497328966fadf36bcaad052"
)
POLICY_SCHEMA = "known-case-nonregression-policy-v1"
SIDECAR_SCHEMA = "known-case-nonregression-result-v1"
METRIC_PATHS = {
    "complex_rms_uohm": ("complex", "rms_uohm"),
    "magnitude_rms_db": ("magnitude_db", "rms_db"),
    "magnitude_p95_abs_db": ("magnitude_db", "p95_abs_db"),
    "phase_p95_abs_deg": ("phase_deg", "p95_abs_deg"),
}
POLICY_METRIC_PATHS = {
    name: "metrics." + ".".join(path) for name, path in METRIC_PATHS.items()
}
RELEASE_IDENTITY = {
    "app_version": "0.22.0",
    "solver_version": "modal-mvp-0.8.3",
    "solver_profile": "layerwise_admittance_v1",
    "solver_profile_key": "layerwise_admittance_v1",
    "compiler_algorithm_id": (
        "layer-surface-adjacent-y-island-finite-via-termination-kron-v8"
    ),
    "compiler_version": (
        "layer-surface-adjacent-y-island-finite-via-termination-kron-v8"
    ),
    "static_compiler_algorithm_sha256": (
        "3894d6174dd6765f5f830bc19511cd1bb843f3f88b1b305ceb5be32c52056615"
    ),
}


class IntegrityError(ValueError):
    """The policy, report, or sidecar is incomplete or not hash-bound."""


@dataclass(frozen=True)
class FileSnapshot:
    """One owned immutable byte read of an input path."""

    path: Path
    raw: bytes
    sha256: str


@dataclass(frozen=True)
class JsonSnapshot:
    """A strict JSON object parsed from one owned file snapshot."""

    file: FileSnapshot
    value: dict[str, Any]


def _read_file_snapshot(path: Path) -> FileSnapshot:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IntegrityError(f"cannot snapshot {path}: {exc}") from exc
    return FileSnapshot(path=path, raw=raw, sha256=hashlib.sha256(raw).hexdigest())


def _sha256(path: Path) -> str:
    return _read_file_snapshot(path).sha256


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise IntegrityError(f"non-finite JSON constant {value!r}")


def _parse_json_snapshot(snapshot: FileSnapshot) -> JsonSnapshot:
    try:
        text = snapshot.raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, IntegrityError) as exc:
        raise IntegrityError(f"cannot read strict JSON {snapshot.path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{snapshot.path}: root must be an object")
    return JsonSnapshot(file=snapshot, value=value)


def _read_json_snapshot(path: Path) -> JsonSnapshot:
    return _parse_json_snapshot(_read_file_snapshot(path))


def _load_json(path: Path) -> dict[str, Any]:
    return _read_json_snapshot(path).value


def _policy_value_sha256(policy: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(
            policy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IntegrityError(f"cannot canonicalize policy: {exc}") from exc
    return hashlib.sha256(canonical).hexdigest()


def _policy_sha256(path: Path) -> str:
    return _policy_value_sha256(_read_json_snapshot(path).value)


def _normalized_text_sha256(snapshot: FileSnapshot) -> str:
    try:
        text = snapshot.raw.decode("utf-8")
    except UnicodeError as exc:
        raise IntegrityError(f"cannot decode tracked source {snapshot.path}: {exc}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _tracked_text_sha256(path: Path) -> str:
    return _normalized_text_sha256(_read_file_snapshot(path))


def _load_trusted_policy(
    path: Path,
    expected_sha256: str = POLICY_SHA256,
) -> tuple[dict[str, Any], str]:
    snapshot = _read_json_snapshot(path)
    return _trusted_policy_snapshot(snapshot, expected_sha256)


def _trusted_policy_snapshot(
    snapshot: JsonSnapshot,
    expected_sha256: str = POLICY_SHA256,
) -> tuple[dict[str, Any], str]:
    policy_sha256 = _policy_value_sha256(snapshot.value)
    _equal(policy_sha256, expected_sha256, "policy.sha256")
    policy = snapshot.value
    _validate_policy(policy)
    return policy, policy_sha256


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntegrityError(f"{path}: expected object")
    return value


def _finite(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntegrityError(f"{path}: expected finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise IntegrityError(f"{path}: expected finite nonnegative number")
    return number


def _equal(actual: Any, expected: Any, path: str) -> None:
    if actual != expected:
        raise IntegrityError(f"{path}: {actual!r} != {expected!r}")


def _exact_keys(value: dict[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise IntegrityError(f"{path}: keys {sorted(value)!r} != {sorted(expected)!r}")


def _sha_value(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise IntegrityError(f"{path}: expected SHA-256")
    if any(character not in "0123456789abcdef" for character in value):
        raise IntegrityError(f"{path}: expected lowercase SHA-256")
    return value


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IntegrityError(f"{path}: expected positive integer")
    return value


def _metric_values(outcome: dict[str, Any], path: str) -> dict[str, float]:
    metrics = _object(outcome.get("metrics"), path + ".metrics")
    result = {}
    for name, (section, field) in METRIC_PATHS.items():
        block = _object(metrics.get(section), f"{path}.metrics.{section}")
        result[name] = _finite(block.get(field), f"{path}.metrics.{section}.{field}")
    return result


def _identity(case_id: str, case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "source": case["source"],
        "touchstone": case["touchstone"],
        "candidate": case["candidate"],
    }


def _validate_policy(policy: dict[str, Any]) -> None:
    _exact_keys(
        policy,
        {
            "schema_version",
            "policy_name",
            "policy_hash_semantics",
            "claim_scope",
            "normalization",
            "release_condition",
            "release_identity",
            "benchmark_source",
            "trust_boundary",
            "group_aggregation",
            "metric_paths",
            "critical_grid",
            "threshold_derivation",
            "groups",
            "cases",
        },
        "policy",
    )
    _equal(policy.get("schema_version"), POLICY_SCHEMA, "policy.schema_version")
    _equal(policy.get("policy_name"), "known_case_nonregression", "policy.policy_name")
    _equal(
        policy.get("policy_hash_semantics"),
        "canonical_json_utf8_sort_keys_compact",
        "policy.policy_hash_semantics",
    )
    _equal(
        policy.get("claim_scope"),
        "regression_only_not_accuracy",
        "policy.claim_scope",
    )
    _equal(policy.get("normalization"), "none", "policy.normalization")
    _equal(
        policy.get("release_condition"),
        "v5_pass_and_known_case_nonregression_sidecar_pass",
        "policy.release_condition",
    )
    _equal(policy.get("release_identity"), RELEASE_IDENTITY, "policy.release_identity")
    _equal(
        policy.get("benchmark_source"),
        {
            "path": "scripts/benchmark_raw_spd_powersi_correlation.py",
            "hash_semantics": "utf8_lf_normalized",
            "sha256": BENCHMARK_SOURCE_SHA256,
        },
        "policy.benchmark_source",
    )
    _equal(
        policy.get("trust_boundary"),
        {
            "generator": "controlled_r4_benchmark_with_frozen_source",
            "validated_scope": (
                "immutable_report_structure_identity_and_nonregression_metrics"
            ),
            "excluded_scope": (
                "does_not_recompute_solver_curves_or_reported_metrics"
            ),
            "generator_authentication": "not_cryptographic",
        },
        "policy.trust_boundary",
    )
    _equal(
        policy.get("group_aggregation"),
        "equal_rail_macro_mean",
        "policy.group_aggregation",
    )
    _equal(policy.get("metric_paths"), POLICY_METRIC_PATHS, "policy.metric_paths")
    derivation = _object(policy.get("threshold_derivation"), "policy.derivation")
    _equal(
        derivation,
        {
            "rail": "max_of_named_mode6_and_mode8_baseline_per_rail",
            "group": "max_of_named_mode6_and_mode8_equal_rail_macro_mean",
            "comparison": "candidate_value_must_be_less_than_or_equal_to_threshold",
        },
        "policy.threshold_derivation",
    )
    grid = _object(policy.get("critical_grid"), "policy.critical_grid")
    _equal(grid, {"low_hz": 100000.0, "high_hz": 100000000.0, "points": 241},
           "policy.critical_grid")
    groups = _object(policy.get("groups"), "policy.groups")
    if set(groups) != {"vqps_development", "vqps_holdout", "loaded_final_holdout"}:
        raise IntegrityError("policy.groups: expected the three named groups")
    rails = []
    for group, members in groups.items():
        if not isinstance(members, list) or not members:
            raise IntegrityError(f"policy.groups.{group}: expected non-empty array")
        if any(not isinstance(rail, str) or not rail for rail in members):
            raise IntegrityError(f"policy.groups.{group}: invalid rail id")
        rails.extend(members)
    if len(rails) != 16 or len(set(rails)) != 16:
        raise IntegrityError("policy.groups: expected exactly 16 unique rails")
    cases = _object(policy.get("cases"), "policy.cases")
    if set(cases) != {"260729", "260804"}:
        raise IntegrityError("policy.cases: expected 260729 and 260804")
    for case_id, raw_case in cases.items():
        case = _object(raw_case, f"policy.cases.{case_id}")
        _exact_keys(
            case,
            {
                "source",
                "touchstone",
                "candidate",
                "baseline_reports",
                "rail_thresholds",
                "group_thresholds",
            },
            f"policy.cases.{case_id}",
        )
        source = _object(case.get("source"), f"policy.cases.{case_id}.source")
        _exact_keys(source, {"basename", "size_bytes", "sha256"},
                    f"policy.cases.{case_id}.source")
        touchstone = _object(
            case.get("touchstone"), f"policy.cases.{case_id}.touchstone"
        )
        _exact_keys(
            touchstone,
            {"basename", "sha256", "ports"},
            f"policy.cases.{case_id}.touchstone",
        )
        candidate = _object(
            case.get("candidate"), f"policy.cases.{case_id}.candidate"
        )
        _exact_keys(candidate, {"basename", "size_bytes", "sha256"},
                    f"policy.cases.{case_id}.candidate")
        for name, identity in (
            ("source", source),
            ("candidate", candidate),
        ):
            if not isinstance(identity.get("basename"), str) or not identity["basename"]:
                raise IntegrityError(f"policy.cases.{case_id}.{name}.basename: blank")
            _positive_int(identity.get("size_bytes"), f"policy.{case_id}.{name}.size")
            _sha_value(identity.get("sha256"), f"policy.{case_id}.{name}.sha256")
        if not isinstance(touchstone.get("basename"), str) or not touchstone["basename"]:
            raise IntegrityError(f"policy.cases.{case_id}.touchstone.basename: blank")
        _sha_value(touchstone.get("sha256"), f"policy.{case_id}.touchstone.sha256")
        _equal(touchstone.get("ports"), 92, f"policy.cases.{case_id}.ports")
        baselines = case.get("baseline_reports")
        if not isinstance(baselines, list) or len(baselines) != 2:
            raise IntegrityError(f"policy.cases.{case_id}.baseline_reports: expected two")
        modes = set()
        for index, raw_baseline in enumerate(baselines):
            path = f"policy.cases.{case_id}.baseline_reports[{index}]"
            baseline = _object(raw_baseline, path)
            _exact_keys(
                baseline,
                {"modal_max_index", "path", "size_bytes", "sha256"},
                path,
            )
            mode = baseline.get("modal_max_index")
            if isinstance(mode, bool) or not isinstance(mode, int):
                raise IntegrityError(f"{path}.modal_max_index: expected integer")
            modes.add(mode)
            if not isinstance(baseline.get("path"), str) or not baseline["path"]:
                raise IntegrityError(f"{path}.path: blank")
            _positive_int(baseline.get("size_bytes"), path + ".size_bytes")
            _sha_value(baseline.get("sha256"), path + ".sha256")
        _equal(modes, {6, 8}, f"policy.cases.{case_id}.baseline_modes")
        rail_thresholds = _object(
            case.get("rail_thresholds"), f"policy.cases.{case_id}.rail_thresholds"
        )
        if set(rail_thresholds) != set(rails):
            raise IntegrityError(f"policy.cases.{case_id}: rail manifest mismatch")
        group_thresholds = _object(
            case.get("group_thresholds"),
            f"policy.cases.{case_id}.group_thresholds",
        )
        if set(group_thresholds) != set(groups):
            raise IntegrityError(f"policy.cases.{case_id}: group manifest mismatch")
        for scope, rows in (("rail", rail_thresholds), ("group", group_thresholds)):
            for name, raw_thresholds in rows.items():
                thresholds = _object(
                    raw_thresholds,
                    f"policy.cases.{case_id}.{scope}_thresholds.{name}",
                )
                if set(thresholds) != set(METRIC_PATHS):
                    raise IntegrityError(
                        f"policy.cases.{case_id}.{scope}_thresholds.{name}: "
                        "metric manifest mismatch"
                    )
                for metric, value in thresholds.items():
                    _finite(value, f"policy.{case_id}.{scope}.{name}.{metric}")


def _verify_baseline_thresholds(
    policy: dict[str, Any],
    case_id: str,
    baseline_root: Path,
) -> dict[str, Any]:
    cases = _object(policy.get("cases"), "policy.cases")
    if case_id not in cases:
        raise IntegrityError(f"baseline case_id: unsupported {case_id!r}")
    case = _object(cases[case_id], f"policy.cases.{case_id}")
    groups = _object(policy.get("groups"), "policy.groups")
    root = baseline_root.resolve()
    runs_by_mode: dict[int, dict[str, Any]] = {}
    reports = []
    for raw_baseline in case["baseline_reports"]:
        baseline = _object(raw_baseline, "policy.baseline_report")
        report_path = (root / baseline["path"]).resolve()
        try:
            report_path.relative_to(root)
        except ValueError as exc:
            raise IntegrityError("baseline report path escapes baseline root") from exc
        snapshot = _read_json_snapshot(report_path)
        report_size = len(snapshot.file.raw)
        _equal(report_size, baseline["size_bytes"],
               f"baseline.{case_id}.{baseline['modal_max_index']}.size_bytes")
        report_sha256 = snapshot.file.sha256
        _equal(report_sha256, baseline["sha256"],
               f"baseline.{case_id}.{baseline['modal_max_index']}.sha256")
        report = snapshot.value
        source = _object(report.get("source"), "baseline.source")
        for field in ("basename", "size_bytes", "sha256"):
            _equal(source.get(field), case["source"][field], f"baseline.source.{field}")
        touchstone = _object(report.get("touchstone"), "baseline.touchstone")
        for field in ("basename", "sha256", "ports"):
            _equal(
                touchstone.get(field),
                case["touchstone"][field],
                f"baseline.touchstone.{field}",
            )
        _equal(
            touchstone.get("full_file_sha256"),
            case["touchstone"]["sha256"],
            "baseline.touchstone.full_file_sha256",
        )
        _equal(report.get("score_split"), groups, "baseline.score_split")
        mode = baseline["modal_max_index"]
        candidate_runs = _object(
            _object(report.get("runs"), "baseline.runs").get("candidate"),
            "baseline.runs.candidate",
        )
        run = _object(candidate_runs.get(str(mode)), f"baseline.runs.candidate.{mode}")
        rails = _object(run.get("rails"), f"baseline.runs.candidate.{mode}.rails")
        expected_rails = {rail for members in groups.values() for rail in members}
        if set(rails) != expected_rails:
            raise IntegrityError(f"baseline mode {mode}: exact 16-rail manifest required")
        for group, members in groups.items():
            for rail in members:
                outcome = _object(rails[rail], f"baseline.{mode}.rails.{rail}")
                _equal(outcome.get("status"), "completed", f"baseline.{mode}.{rail}.status")
                _equal(outcome.get("group"), group, f"baseline.{mode}.{rail}.group")
                metrics = _object(outcome.get("metrics"), f"baseline.{mode}.{rail}.metrics")
                _equal(metrics.get("grid"), policy["critical_grid"],
                       f"baseline.{mode}.{rail}.grid")
                _metric_values(outcome, f"baseline.{mode}.rails.{rail}")
        runs_by_mode[mode] = rails
        reports.append(
            {
                "modal_max_index": mode,
                "path": baseline["path"],
                "size_bytes": baseline["size_bytes"],
                "sha256": report_sha256,
            }
        )
    for rail, raw_limits in case["rail_thresholds"].items():
        limits = _object(raw_limits, f"policy.rail_thresholds.{rail}")
        for metric in METRIC_PATHS:
            values = [
                _metric_values(runs_by_mode[mode][rail], f"baseline.{mode}.{rail}")[
                    metric
                ]
                for mode in (6, 8)
            ]
            _equal(limits[metric], max(values), f"policy.rail_threshold.{rail}.{metric}")
    for group, members in groups.items():
        limits = _object(case["group_thresholds"][group],
                         f"policy.group_thresholds.{group}")
        for metric in METRIC_PATHS:
            means = []
            for mode in (6, 8):
                values = [
                    _metric_values(
                        runs_by_mode[mode][rail], f"baseline.{mode}.{rail}"
                    )[metric]
                    for rail in members
                ]
                means.append(sum(values) / len(values))
            _equal(limits[metric], max(means),
                   f"policy.group_threshold.{group}.{metric}")
    return {
        "status": "PASS",
        "derivation": "max_of_mode6_mode8",
        "group_aggregation": "equal_rail_macro_mean",
        "reports": reports,
    }


def _verify_benchmark_source(
    policy: dict[str, Any],
    benchmark_root: Path,
) -> dict[str, Any]:
    identity = _object(policy.get("benchmark_source"), "policy.benchmark_source")
    root = benchmark_root.resolve()
    source_path = (root / identity["path"]).resolve()
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise IntegrityError("benchmark source path escapes benchmark root") from exc
    snapshot = _read_file_snapshot(source_path)
    observed = _normalized_text_sha256(snapshot)
    _equal(observed, identity["sha256"], "benchmark_source.sha256")
    _equal(observed, BENCHMARK_SOURCE_SHA256, "trusted.benchmark_source.sha256")
    return {
        "status": "PASS",
        "path": identity["path"],
        "hash_semantics": "utf8_lf_normalized",
        "sha256": observed,
        "trust_boundary": policy["trust_boundary"],
    }


def _run_structural_v5(
    report_snapshot: JsonSnapshot,
    validator_snapshot: FileSnapshot,
) -> dict[str, Any]:
    validator_sha256 = _normalized_text_sha256(validator_snapshot)
    _equal(validator_sha256, TRACKED_V5_SHA256, "tracked_v5_validator.sha256")
    try:
        with tempfile.TemporaryDirectory(prefix="known-case-v5-") as temp_name:
            temp_dir = Path(temp_name)
            validator_copy = temp_dir / "validate_correlation_v5.py"
            report_copy = temp_dir / "correlation_report.json"
            validator_copy.write_bytes(validator_snapshot.raw)
            report_copy.write_bytes(report_snapshot.file.raw)
            completed = subprocess.run(
                [sys.executable, str(validator_copy), str(report_copy)],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IntegrityError(f"tracked v5 validator failed to execute: {exc}") from exc
    marker = "PASS: v5 release/convergence/invariance/reuse structure validated"
    if completed.returncode != 0 or marker not in completed.stdout:
        detail = (completed.stderr or completed.stdout).strip()
        raise IntegrityError(
            "tracked v5 validator did not PASS: " + detail[:2000]
        )
    return {
        "status": "PASS",
        "validator_basename": "validate_correlation_v5.py",
        "validator_sha256": validator_sha256,
        "validator_hash_semantics": "utf8_lf_normalized",
        "raw_report_sha256": report_snapshot.file.sha256,
        "exit_code": completed.returncode,
        "pass_marker": marker,
    }


def _validate_report_identity(
    report: dict[str, Any],
    case_id: str,
    case: dict[str, Any],
    groups: dict[str, Any],
) -> None:
    _equal(report.get("schema_version"), "powersi-correlation-report-v5",
           "report.schema_version")
    _equal(report.get("report_version"), 5, "report.report_version")
    source = _object(report.get("source"), "report.source")
    for field in ("basename", "size_bytes", "sha256"):
        _equal(source.get(field), case["source"][field], f"report.source.{field}")
    touchstone = _object(report.get("touchstone"), "report.touchstone")
    for field in ("basename", "sha256", "ports"):
        _equal(
            touchstone.get(field),
            case["touchstone"][field],
            f"report.touchstone.{field}",
        )
    _equal(
        touchstone.get("full_file_sha256"),
        case["touchstone"]["sha256"],
        "report.touchstone.full_file_sha256",
    )
    candidate = _object(report.get("candidate_bundle"), "report.candidate_bundle")
    for field in ("basename", "size_bytes", "sha256"):
        _equal(
            candidate.get(field),
            case["candidate"][field],
            f"report.candidate_bundle.{field}",
        )
    identity = _object(report.get("identity"), "report.identity")
    _equal(identity.get("source_sha256"), case["source"]["sha256"],
           "report.identity.source_sha256")
    _equal(identity.get("candidate_source_sha256"), case["source"]["sha256"],
           "report.identity.candidate_source_sha256")
    _equal(identity.get("candidate_bundle_sha256"), case["candidate"]["sha256"],
           "report.identity.candidate_bundle_sha256")
    split = _object(report.get("score_split"), "report.score_split")
    _equal(split, groups, f"report.score_split.{case_id}")


def _validate_release_report_identity(report: dict[str, Any]) -> None:
    for field, expected in RELEASE_IDENTITY.items():
        _equal(report.get(field), expected, f"report.{field}")
    runs = _object(
        _object(report.get("runs"), "report.runs").get("candidate"),
        "report.runs.candidate",
    )
    for mode in ("10", "12"):
        run = _object(runs.get(mode), f"report.runs.candidate.{mode}")
        _equal(
            run.get("solver_profile"),
            RELEASE_IDENTITY["solver_profile"],
            f"report.runs.candidate.{mode}.solver_profile",
        )
        rails = _object(run.get("rails"), f"report.runs.candidate.{mode}.rails")
        for rail, raw_outcome in rails.items():
            path = f"report.runs.candidate.{mode}.rails.{rail}"
            outcome = _object(raw_outcome, path)
            _equal(
                outcome.get("solver_version"),
                RELEASE_IDENTITY["solver_version"],
                path + ".solver_version",
            )
            _equal(
                outcome.get("solver_profile_key"),
                RELEASE_IDENTITY["solver_profile_key"],
                path + ".solver_profile_key",
            )
            provenance = _object(outcome.get("solver_provenance"),
                                 path + ".solver_provenance")
            for field in (
                "compiler_algorithm_id",
                "compiler_version",
                "static_compiler_algorithm_sha256",
            ):
                _equal(
                    provenance.get(field),
                    RELEASE_IDENTITY[field],
                    path + ".solver_provenance." + field,
                )
            _equal(
                provenance.get("profile_key"),
                RELEASE_IDENTITY["solver_profile_key"],
                path + ".solver_provenance.profile_key",
            )


def _validate_modes(report: dict[str, Any]) -> dict[str, Any]:
    all_runs = _object(report.get("runs"), "report.runs")
    runs = _object(all_runs.get("candidate"), "report.runs.candidate")
    if set(runs) != {"10", "12"}:
        raise IntegrityError("report.runs.candidate: expected only modes 10 and 12")
    mode10 = _object(runs["10"], "report.runs.candidate.10")
    mode12 = _object(runs["12"], "report.runs.candidate.12")
    _equal(mode10.get("modal_max_index"), 10, "report.runs.candidate.10.modal_max_index")
    _equal(mode12.get("modal_max_index"), 12, "report.runs.candidate.12.modal_max_index")
    reuse10 = _object(
        mode10.get("terminal_complete_batch_reuse"),
        "report.runs.candidate.10.terminal_complete_batch_reuse",
    )
    reuse12 = _object(
        mode12.get("terminal_complete_batch_reuse"),
        "report.runs.candidate.12.terminal_complete_batch_reuse",
    )
    _equal(reuse10.get("status"), "source_solve", "report.mode10.reuse.status")
    _equal(reuse10.get("source_modal_max_index"), 10, "report.mode10.reuse.source_mode")
    _equal(
        reuse12.get("status"),
        "reused_exact_mode_invariant",
        "report.mode12.reuse.status",
    )
    _equal(reuse12.get("source_modal_max_index"), 10, "report.mode12.reuse.source_mode")
    return mode10


def _evaluate_snapshots(
    policy_snapshot: JsonSnapshot,
    report_snapshot: JsonSnapshot,
    case_id: str,
    *,
    expected_policy_sha256: str = POLICY_SHA256,
    structural_v5: dict[str, Any] | None = None,
    baseline_verification: dict[str, Any] | None = None,
    benchmark_verification: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    policy, policy_sha256 = _trusted_policy_snapshot(
        policy_snapshot,
        expected_policy_sha256,
    )
    cases = _object(policy["cases"], "policy.cases")
    if case_id not in cases:
        raise IntegrityError(f"case_id: unsupported case {case_id!r}")
    case = _object(cases[case_id], f"policy.cases.{case_id}")
    report_sha256 = report_snapshot.file.sha256
    report = report_snapshot.value
    groups = _object(policy.get("groups"), "policy.groups")
    _validate_report_identity(report, case_id, case, groups)
    _validate_release_report_identity(report)
    mode10 = _validate_modes(report)
    rails = _object(mode10.get("rails"), "report.runs.candidate.10.rails")
    expected_rails = {rail for members in groups.values() for rail in members}
    if set(rails) != expected_rails:
        raise IntegrityError("report.mode10.rails: exact 16-rail manifest required")
    grid_expected = policy["critical_grid"]
    rail_values: dict[str, dict[str, float]] = {}
    failures: list[dict[str, Any]] = []
    thresholds = _object(case.get("rail_thresholds"), "case.rail_thresholds")
    for rail in sorted(expected_rails):
        outcome = _object(rails[rail], f"report.mode10.rails.{rail}")
        _equal(outcome.get("status"), "completed", f"report.mode10.rails.{rail}.status")
        _equal(outcome.get("group"), next(g for g, rs in groups.items() if rail in rs),
               f"report.mode10.rails.{rail}.group")
        metrics = _object(outcome.get("metrics"), f"report.mode10.rails.{rail}.metrics")
        _equal(metrics.get("grid"), grid_expected, f"report.mode10.rails.{rail}.grid")
        values = _metric_values(outcome, f"report.mode10.rails.{rail}")
        rail_values[rail] = values
        rail_limits = _object(thresholds[rail], f"case.rail_thresholds.{rail}")
        for metric, actual in values.items():
            limit = _finite(rail_limits[metric], f"case.rail_thresholds.{rail}.{metric}")
            if actual > limit:
                failures.append(
                    {
                        "scope": "rail",
                        "name": rail,
                        "metric": metric,
                        "actual": actual,
                        "threshold": limit,
                    }
                )
    group_values: dict[str, dict[str, float]] = {}
    group_limits_all = _object(case.get("group_thresholds"), "case.group_thresholds")
    for group, members in groups.items():
        values = {
            metric: sum(rail_values[rail][metric] for rail in members) / len(members)
            for metric in METRIC_PATHS
        }
        group_values[group] = values
        group_limits = _object(group_limits_all[group], f"case.group_thresholds.{group}")
        for metric, actual in values.items():
            limit = _finite(group_limits[metric], f"case.group_thresholds.{group}.{metric}")
            if actual > limit:
                failures.append(
                    {
                        "scope": "group_equal_rail_macro_mean",
                        "name": group,
                        "metric": metric,
                        "actual": actual,
                        "threshold": limit,
                    }
                )
    passed = not failures
    structural = structural_v5 or {"status": "NOT_RUN"}
    if structural_v5 is not None:
        _exact_keys(
            structural,
            {
                "status",
                "validator_basename",
                "validator_sha256",
                "validator_hash_semantics",
                "raw_report_sha256",
                "exit_code",
                "pass_marker",
            },
            "structural_v5",
        )
        _equal(structural.get("status"), "PASS", "structural_v5.status")
        _equal(
            structural.get("validator_basename"),
            "validate_correlation_v5.py",
            "structural_v5.validator_basename",
        )
        _equal(
            structural.get("validator_sha256"),
            TRACKED_V5_SHA256,
            "structural_v5.validator_sha256",
        )
        _equal(
            structural.get("validator_hash_semantics"),
            "utf8_lf_normalized",
            "structural_v5.validator_hash_semantics",
        )
        _equal(
            structural.get("raw_report_sha256"),
            report_sha256,
            "structural_v5.raw_report_sha256",
        )
        _equal(structural.get("exit_code"), 0, "structural_v5.exit_code")
        _equal(
            structural.get("pass_marker"),
            "PASS: v5 release/convergence/invariance/reuse structure validated",
            "structural_v5.pass_marker",
        )
    baseline_check = baseline_verification or {"status": "NOT_RUN"}
    if baseline_verification is not None:
        _equal(
            baseline_check,
            {
                "status": "PASS",
                "derivation": "max_of_mode6_mode8",
                "group_aggregation": "equal_rail_macro_mean",
                "reports": case["baseline_reports"],
            },
            "baseline_threshold_verification",
        )
    benchmark_check = benchmark_verification or {"status": "NOT_RUN"}
    if benchmark_verification is not None:
        _equal(
            benchmark_check,
            {
                "status": "PASS",
                "path": policy["benchmark_source"]["path"],
                "hash_semantics": "utf8_lf_normalized",
                "sha256": BENCHMARK_SOURCE_SHA256,
                "trust_boundary": policy["trust_boundary"],
            },
            "benchmark_source_verification",
        )
    overall_passed = (
        passed
        and structural.get("status") == "PASS"
        and baseline_check.get("status") == "PASS"
        and benchmark_check.get("status") == "PASS"
    )
    sidecar = {
        "schema_version": SIDECAR_SCHEMA,
        "policy_name": "known_case_nonregression",
        "claim_scope": "regression_only_not_accuracy",
        "normalization": "none",
        "release_condition": "v5_pass_and_known_case_nonregression_sidecar_pass",
        "status": (
            "PASS"
            if overall_passed
            else "FAIL"
            if not passed
            else "NONREGRESSION_PASS_RELEASE_ATTESTATIONS_NOT_RUN"
        ),
        "nonregression_status": "PASS" if passed else "FAIL",
        "structural_v5_validation": structural,
        "baseline_threshold_verification": baseline_check,
        "benchmark_source_verification": benchmark_check,
        "release_identity": RELEASE_IDENTITY,
        "trust_boundary": policy["trust_boundary"],
        "case_identity": _identity(case_id, case),
        "policy_sha256": policy_sha256,
        "policy_sha256_semantics": "canonical_json_utf8_sort_keys_compact",
        "raw_report_sha256": report_sha256,
        "evaluated_mode": 10,
        "mode10_status": "source_solve",
        "mode12_status": "reused_exact_mode_invariant",
        "critical_grid": grid_expected,
        "rail_count": len(expected_rails),
        "group_aggregation": "equal_rail_macro_mean",
        "rail_metrics": rail_values,
        "group_metrics": group_values,
        "failure_count": len(failures),
        "failures": failures,
    }
    return sidecar, passed


def evaluate(
    policy_path: Path,
    report_path: Path,
    case_id: str,
    *,
    expected_policy_sha256: str = POLICY_SHA256,
    structural_v5: dict[str, Any] | None = None,
    baseline_verification: dict[str, Any] | None = None,
    benchmark_verification: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    return _evaluate_snapshots(
        _read_json_snapshot(policy_path),
        _read_json_snapshot(report_path),
        case_id,
        expected_policy_sha256=expected_policy_sha256,
        structural_v5=structural_v5,
        baseline_verification=baseline_verification,
        benchmark_verification=benchmark_verification,
    )


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IntegrityError(f"cannot serialize canonical JSON: {exc}") from exc
    return (text + "\n").encode("utf-8")


def _write_sidecar(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise IntegrityError(f"refusing to overwrite existing sidecar {path}")
    raw = _canonical_json_bytes(result)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=path.name + f".{os.getpid()}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise IntegrityError(f"refusing to overwrite existing sidecar {path}") from exc
    except OSError as exc:
        raise IntegrityError(f"cannot write sidecar {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def verify_sidecar(
    policy_path: Path,
    report_path: Path,
    sidecar_path: Path,
    *,
    expected_policy_sha256: str = POLICY_SHA256,
    validator_path: Path | None = None,
    baseline_root: Path | None = None,
    benchmark_root: Path | None = None,
) -> None:
    sidecar_snapshot = _read_json_snapshot(sidecar_path)
    sidecar = sidecar_snapshot.value
    policy_snapshot = _read_json_snapshot(policy_path)
    policy, _policy_sha = _trusted_policy_snapshot(
        policy_snapshot,
        expected_policy_sha256,
    )
    report_snapshot = _read_json_snapshot(report_path)
    validator = validator_path or Path(__file__).with_name("validate_correlation_v5.py")
    validator_snapshot = _read_file_snapshot(validator)
    structural = _run_structural_v5(report_snapshot, validator_snapshot)
    case_identity = _object(sidecar.get("case_identity"), "sidecar.case_identity")
    case_id = case_identity.get("case_id")
    root = baseline_root or Path(__file__).parents[1]
    baseline_check = _verify_baseline_thresholds(policy, str(case_id), root)
    source_root = benchmark_root or Path(__file__).parents[1]
    benchmark_check = _verify_benchmark_source(policy, source_root)
    expected, passed = _evaluate_snapshots(
        policy_snapshot,
        report_snapshot,
        str(case_id),
        expected_policy_sha256=expected_policy_sha256,
        structural_v5=structural,
        baseline_verification=baseline_check,
        benchmark_verification=benchmark_check,
    )
    if not passed or expected.get("status") != "PASS":
        raise IntegrityError("sidecar report no longer passes known_case_nonregression")
    expected_raw = _canonical_json_bytes(expected)
    if sidecar_snapshot.file.raw != expected_raw:
        raise IntegrityError("sidecar.canonical_recomputed_bytes: mismatch")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--case", choices=("260729", "260804"))
    parser.add_argument("--verify-sidecar", action="store_true")
    parser.add_argument(
        "--v5-validator",
        type=Path,
        default=Path(__file__).with_name("validate_correlation_v5.py"),
    )
    parser.add_argument(
        "--baseline-root",
        type=Path,
        default=Path(__file__).parents[1],
    )
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=Path(__file__).parents[1],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verify_sidecar:
            verify_sidecar(
                args.policy,
                args.report,
                args.sidecar,
                validator_path=args.v5_validator,
                baseline_root=args.baseline_root,
                benchmark_root=args.benchmark_root,
            )
            print("PASS: known_case_nonregression sidecar integrity verified")
            return 0
        if args.case is None:
            raise IntegrityError("--case is required unless --verify-sidecar is used")
        if args.sidecar.exists():
            raise IntegrityError(f"refusing to overwrite existing sidecar {args.sidecar}")
        policy_snapshot = _read_json_snapshot(args.policy)
        policy, _policy_sha = _trusted_policy_snapshot(policy_snapshot)
        report_snapshot = _read_json_snapshot(args.report)
        validator_snapshot = _read_file_snapshot(args.v5_validator)
        structural = _run_structural_v5(report_snapshot, validator_snapshot)
        baseline_check = _verify_baseline_thresholds(
            policy,
            args.case,
            args.baseline_root,
        )
        benchmark_check = _verify_benchmark_source(policy, args.benchmark_root)
        result, passed = _evaluate_snapshots(
            policy_snapshot,
            report_snapshot,
            args.case,
            structural_v5=structural,
            baseline_verification=baseline_check,
            benchmark_verification=benchmark_check,
        )
        _write_sidecar(args.sidecar, result)
        if passed:
            print("PASS: known_case_nonregression")
            return 0
        print(
            f"FAIL: known_case_nonregression: {result['failure_count']} regression(s)",
            file=sys.stderr,
        )
        return 2
    except IntegrityError as exc:
        print(f"ERROR: known_case_nonregression integrity: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
