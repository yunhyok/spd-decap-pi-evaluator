#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.22.0 AV-BS1 H4-P0R-P1 factor-only contract.

The checked-in state has no review token.  ``manifest`` is therefore the only
normal executable stage; the primary path is deliberately token/claim/guard
gated and creates no RHS, solve, extension, boundary operator, modal response,
or physics certificate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from hashlib import sha256
import gc
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from time import perf_counter_ns, sleep
from typing import Mapping

import numpy as np
from scipy.sparse import csc_array, csc_matrix

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import av_bs1_boundary_schur as h1
import av_bs1_boundary_schur_h4_p0 as h4
import av_bs1_boundary_schur_h4_p0r as p0r

PROGRAM = h1.PROGRAM
CASE_ID = h1.CASE_ID
SCHEMA = "AV-BS1-h4-p0r-execution-fixture-v1"
# Execution artifacts intentionally reuse the P0R schema family frozen by the
# manifest-only parent; ``p1`` names files, not an incompatible evidence ABI.
TOKEN_SCHEMA = "AV-BS1-h4-p0r-review-token-v1"
CLAIM_SCHEMA = "AV-BS1-h4-p0r-token-claim-v1"
GUARD_SCHEMA = "AV-BS1-h4-p0r-resource-guard-v1"
RESOURCE_SCHEMA = "AV-BS1-h4-p0r-resource-report-v1"
NUMERICAL_SCHEMA = "AV-BS1-h4-p0r-factor-report-v1"
RESULT_SCHEMA = "AV-BS1-h4-p0r-result-v2"
FAILURE_SCHEMA = "AV-BS1-h4-p0r-failure-v1"
TOMBSTONE_SCHEMA = "AV-BS1-h4-p0r-consumed-review-token-v2"
EMERGENCY_TOMBSTONE_SCHEMA = (
    "AV-BS1-h4-p0r-emergency-consumed-review-token-v2"
)
CONTROL_PLANE_REPORT_SCHEMA = (
    "AV-BS1-h4-p0r-control-plane-process-report-v1"
)
CONTROL_PLANE_INDEX_SCHEMA = (
    "AV-BS1-h4-p0r-control-plane-session-index-v1"
)
CONTROL_PLANE_ENVELOPE_CLOSE_SCHEMA = (
    "AV-BS1-h4-p0r-control-plane-envelope-close-v1"
)
CONTROL_PLANE_PRE_EXIT_EVIDENCE_SCHEMA = (
    "AV-BS1-h4-p0r-control-plane-pre-exit-intent-evidence-v1"
)
EMERGENCY_REPLACEMENT_INTENT_SCHEMA = (
    "AV-BS1-h4-p0r-emergency-replacement-intent-v1"
)
EMERGENCY_REPLACEMENT_POSTVALIDATION_SCHEMA = (
    "AV-BS1-h4-p0r-emergency-replacement-postvalidation-v1"
)
OUTER_OBSERVER_CONTRACT_SCHEMA = "AV-BS1-h4-p0r-outer-observer-contract-v1"
OUTER_INNER_READY_SCHEMA = "AV-BS1-h4-p0r-outer-inner-ready-v1"
OUTER_START_RELEASE_SCHEMA = "AV-BS1-h4-p0r-outer-start-release-v1"
OUTER_HANDSHAKE_PREFIX_SCHEMA = (
    "AV-BS1-h4-p0r-outer-observer-handshake-prefix-v1"
)
OUTER_INNER_COMPLETE_SCHEMA = "AV-BS1-h4-p0r-outer-inner-complete-v1"
OUTER_EXIT_RELEASE_SCHEMA = "AV-BS1-h4-p0r-outer-exit-release-v1"
OUTER_RESOURCE_ENVELOPE_CLOSE_SCHEMA = (
    "AV-BS1-h4-p0r-outer-resource-envelope-close-v1"
)
OUTER_TERMINAL_SEAL_SCHEMA = "AV-BS1-h4-p0r-outer-terminal-seal-v2"
OUTER_TERMINAL_CLASSIFICATION_SCHEMA = (
    "AV-BS1-h4-p0r-manifest-terminal-evidence-classification-v1"
)

NORMAL_TOMBSTONE_FIELDS = frozenset(
    """
    schema program case_id authorized_stage authorization_state uses_remaining
    next_stage_authorized review_disposition consumed_review_token_id
    consumed_review_token_sha256 consumed_review_token_canonical_sha256
    p0r_preregistration_commit consumed_git_head review_binding_sha256 bindings
    claim_relative_path claim_sha256 claim_canonical_sha256 claim_evidence_valid
    claim_evidence preflight_payload_sha256 guard_contract_sha256
    guard_canonical_sha256 guard_evidence_valid guard_evidence
    resource_guard_policy_sha256 execution_resource_scope_sha256 attempt_status
    effective_attempt_status consumption_validated_pass child_launched
    child_process_id child_exit_code consumed_result_file_sha256
    consumed_result_payload_sha256 consumed_resource_report_sha256
    consumed_child_stdout_file_sha256 consumed_child_payload_sha256
    result_evidence resource_evidence child_stdout_evidence result_evidence_valid
    resource_evidence_valid child_stdout_evidence_valid
    factor_prefix_evidence_valid factor_prefix_evidence resource_gate_pass
    resource_gate_recheck_failures evidence_validation_errors failure_codes
    mandatory_stage_pass factorization_attempted factorization_performed
    active_factor completed_factors factor_order factor_certificates
    physics_solve_performed consumed_utc outer_observer_contract_sha256
    outer_observer_handshake_prefix_sha256 expected_terminal_seal_relative_path
    terminal_seal_required_for_authoritative_disposition terminal_seal_state
    terminal_evidence_complete authoritative_stage_pass
    """.split()
)
EMERGENCY_TOMBSTONE_FIELDS = frozenset(
    set(NORMAL_TOMBSTONE_FIELDS)
    | {
        "emergency_consumption",
        "consumer_control_gate_pass",
        "consumer_failure_message",
        "replaced_token_file_sha256",
        "runner_sha256",
    }
)
OUTER_INNER_COMPLETE_FIELDS = frozenset(
    """
    schema program case_id stage internal_mode observer_session_relative_path
    observer_nonce outer_process_id outer_process_birth_utc_ticks
    inner_process_id inner_process_birth_utc_ticks runner_sha256 review_token_id
    original_review_token_sha256 outer_observer_contract_sha256
    expected_terminal_seal_relative_path
    terminal_seal_required_for_authoritative_disposition
    inner_ready_relative_path inner_ready_sha256
    outer_start_release_relative_path outer_start_release_sha256
    outer_observer_handshake_prefix outer_observer_handshake_prefix_sha256
    claim_relative_path claim_sha256 claim_canonical_sha256 guard_sha256
    guard_canonical_sha256 resource_report_sha256 result_file_sha256
    child_stdout_sha256 child_stderr_sha256 factor_prefix_one_sha256
    factor_prefix_two_sha256 monitor_ready_marker_sha256
    factor_complete_marker_sha256 monitor_release_marker_sha256
    published_result_relative_path
    published_result_sha256 tombstone_relative_path tombstone_sha256
    tombstone_schema pre_exit_evidence_relative_path pre_exit_evidence_sha256
    pre_exit_evidence_schema final_control_plane_session_index_relative_path
    final_control_plane_session_index_sha256 intended_inner_exit_code
    provisional_inner_disposition inner_exit_observed
    temporary_attempt_cleanup_disposition attempt_evidence_relative_path
    terminal_evidence_complete authorization_blocker
    external_runner_or_machine_kill_terminal_state_guaranteed monotonic_ns
    completed_utc
    """.split()
)
OUTER_EXIT_RELEASE_FIELDS = frozenset(
    """
    schema program case_id stage observer_session_relative_path observer_nonce
    outer_process_id outer_process_birth_utc_ticks inner_process_id
    inner_process_birth_utc_ticks runner_sha256 review_token_id
    original_review_token_sha256 outer_observer_contract_sha256
    expected_terminal_seal_relative_path
    terminal_seal_required_for_authoritative_disposition
    inner_complete_relative_path inner_complete_sha256
    outer_observer_handshake_prefix_sha256 intended_inner_exit_code
    release_inner_to_exit outer_resource_gate_pass_before_inner_exit
    terminal_seal_still_pending
    external_runner_or_machine_kill_terminal_state_guaranteed monotonic_ns
    released_utc
    """.split()
)
OUTER_RESOURCE_ENVELOPE_CLOSE_FIELDS = frozenset(
    """
    authorization_blocker available_physical_min_bytes case_id cleanup_verified
    commit_headroom_min_bytes ended_utc excluded_head excluded_tail
    execution_tree_root_birth_utc_ticks execution_tree_root_pid
    external_runner_or_machine_kill_terminal_state_guaranteed
    high_post_available_physical_bytes high_post_commit_headroom_bytes
    high_pre_available_physical_bytes high_pre_commit_headroom_bytes
    high_pre_spawn_available_physical_bytes
    high_pre_spawn_commit_headroom_bytes inner_actual_exit_code
    inner_complete_relative_path inner_complete_sha256
    inner_exit_release_relative_path inner_exit_release_sha256
    inner_process_birth_utc_ticks inner_process_id inner_ready_relative_path
    inner_ready_sha256 inner_stderr_bytes inner_stderr_sha256 inner_stdout_bytes
    inner_stdout_sha256 inner_visible_sample_count
    mandatory_outer_resource_gate_pass monitor_error_present
    observed_lifecycle_scope observer_nonce observer_session_relative_path
    outer_observer_contract_sha256 peak post_cleanup_claim_relative_path
    post_cleanup_claim_sha256 post_cleanup_emergency_replacement_performed
    post_cleanup_recovery_state post_cleanup_terminal_seal_pending
    post_cleanup_token_sha256 process_membership_semantics program
    raw_bytes_are_canonical_json sample_count sampled_process_identities schema
    simultaneous_current_factor_interval_semantics started_utc stop_reason
    summed_os_lifetime_peak_semantics
    terminal_sample_after_inner_actual_exit_and_verified_cleanup thresholds
    wall_elapsed_nanoseconds wall_stop_seconds
    """.split()
)
OUTER_TERMINAL_SEAL_FIELDS = frozenset(
    """
    schema program case_id stage seal_kind review_token_id
    original_review_token_sha256 runner_sha256 outer_observer_contract_sha256
    expected_terminal_seal_relative_path
    terminal_seal_required_for_authoritative_disposition
    observer_session_relative_path observer_nonce outer_process_id
    outer_process_birth_utc_ticks inner_process_id inner_process_birth_utc_ticks
    retained_inner_process_handle_acquired inner_ready_relative_path
    inner_ready_sha256 outer_start_release_relative_path
    outer_start_release_sha256 outer_observer_handshake_prefix
    outer_observer_handshake_prefix_sha256 artifact_hash_source
    claim_relative_path claim_sha256 claim_canonical_sha256 guard_sha256
    guard_canonical_sha256 resource_report_sha256 result_file_sha256
    child_stdout_sha256 child_stderr_sha256 factor_prefix_one_sha256
    factor_prefix_two_sha256 monitor_ready_marker_sha256
    factor_complete_marker_sha256 monitor_release_marker_sha256
    published_result_relative_path
    published_result_sha256 inner_complete_relative_path inner_complete_sha256
    outer_exit_release_relative_path outer_exit_release_sha256
    inner_stdout_relative_path inner_stdout_bytes inner_stdout_sha256
    inner_stderr_relative_path inner_stderr_bytes inner_stderr_sha256
    outer_resource_envelope_close_schema
    outer_resource_envelope_close_relative_path
    outer_resource_envelope_close_raw_sha256
    outer_resource_envelope_close_canonical_sha256
    outer_resource_envelope_mandatory_gate_pass tombstone_relative_path
    tombstone_sha256 tombstone_schema pre_exit_evidence_relative_path
    pre_exit_evidence_sha256 final_control_plane_session_index_relative_path
    final_control_plane_session_index_sha256 inner_reported_exit_code
    inner_actual_exit_code inner_exit_observed inner_exit_code_matches_completion
    authoritative_inner_disposition terminal_evidence_complete
    tombstone_success_was_provisional_without_this_seal seal_observes
    seal_materialization_and_readback_excluded_from_outer_resource_envelope
    outer_process_exit_observed seal_written_before_outer_exit
    authorization_blocker authorization_effect next_stage_authorized
    external_runner_or_machine_kill_terminal_state_guaranteed sealed_utc
    authoritative_stage_pass
    supersedes_provisional_tombstone_and_any_present_result_disposition
    """.split()
)
CONTROL_PLANE_PRE_EXIT_EVIDENCE_FIELDS = frozenset(
    """
    schema program case_id authorized_stage evidence_kind review_token_id
    original_review_token_sha256 original_review_token_canonical_sha256
    claim_relative_path claim_sha256 claim_canonical_sha256 guard_sha256
    guard_canonical_sha256 resource_report_sha256 result_file_sha256
    child_stdout_sha256 child_stderr_sha256 factor_prefix_one_sha256
    factor_prefix_two_sha256 monitor_ready_marker_sha256
    factor_complete_marker_sha256 monitor_release_marker_sha256
    tombstone_schema tombstone_relative_path
    tombstone_sha256 emergency_replacement_postvalidated
    emergency_replacement_intent_relative_path
    emergency_replacement_intent_sha256
    emergency_replacement_postvalidation_relative_path
    emergency_replacement_postvalidation_sha256
    emergency_replacement_recovery_relative_path
    emergency_replacement_recovery_sha256 consumer_control_gate_pass
    consumer_report_relative_path consumer_report_sha256
    consumer_envelope_close_relative_path consumer_envelope_close_sha256
    final_session_index_relative_path final_session_index_sha256
    outer_observer_contract_sha256 expected_terminal_seal_relative_path
    terminal_seal_required_for_authoritative_disposition
    outer_observer_handshake_prefix outer_observer_handshake_prefix_sha256
    attempt_status runner_exit_observed intended_runner_exit_code
    intended_runner_disposition mandatory_stage_pass authorization_blocker
    authorization_effect normal_tombstone_authority
    normal_pass_outer_evidence_reconciliation_pass
    normal_pass_outer_evidence_reconciliation_errors
    tombstone_success_remains_provisional pre_exit_evidence_complete
    terminal_evidence_complete external_observer_required_for_terminal_exit
    written_before_runner_exit temporary_attempt_evidence_cleanup_observed
    temporary_attempt_evidence_cleanup_occurs_after_this_record
    consume_after_every_owned_claim_outcome
    external_runner_or_machine_kill_terminal_state_guaranteed
    execution_resource_scope_sha256 recorded_utc
    """.split()
)
CONTROL_PLANE_BOOTSTRAP_SHA256 = (
    "3b8d2230e316b541c0d59d6334c13eaa1cf1c0e56ab7f02b1753dbefaeddec0f"
)
CONTROL_PLANE_CANONICAL_HASH_HELPER_SHA256 = (
    "7d80a4c230409aa0462a59f5cb9de167101ddd53b9f31119d0679f08a853d45c"
)
FAILURE_CODES = frozenset({"BLOCKED_AV_BS_FACTOR", "BLOCKED_AV_BS_MESH_HASH", "BLOCKED_AV_BS_RESOURCE", "BLOCKED_AV_BS_RESULT_SCHEMA"})
RESOURCE_STOP_REASONS = frozenset(
    {
        "PRESPAWN_COMMIT_HEADROOM_STOP",
        "PRESPAWN_AVAILABLE_PHYSICAL_STOP",
        "TREE_WS_STOP",
        "TREE_LIFETIME_PEAK_WS_STOP",
        "TREE_PRIVATE_STOP",
        "TREE_COMMIT_STOP",
        "TREE_LIFETIME_PEAK_COMMIT_STOP",
        "SYSTEM_COMMIT_HEADROOM_STOP",
        "AVAILABLE_PHYSICAL_STOP",
        "WALL_TIME_STOP",
        "MONITOR_QUERY_FAILED",
        "OBSERVED_PROCESS_TERMINATION_FAILED",
        "ORPHANED_OBSERVED_PROCESS",
        "RUNNER_EXCEPTION",
    }
)
ROOT = Path(__file__).resolve().parents[2]
TOKEN_PATH = Path(__file__).with_name("av_bs1_h4_p0r_p1_review_token.json")
P0R_FIXTURE = Path(__file__).with_name("av_bs1_boundary_schur_h4_p0r.py")
P0R_RUNNER = Path(__file__).with_name("run_av_bs1_h4_p0r_stage.ps1")
P0R_TEST = ROOT / "tests/test_research_av_bs1_boundary_schur_h4_p0r.py"
P0R_DOC = ROOT / "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_PREREG.md"
P1_DOC = ROOT / "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md"
P1_TEST = ROOT / "tests/test_research_av_bs1_boundary_schur_h4_p0r_p1.py"
P1_RUNNER = Path(__file__).with_name("run_av_bs1_h4_p0r_p1_stage.ps1")
CLAIMS_ROOT = ROOT / "validation-output/av-bs1/claims"
CONTROL_PLANE_ROOT = ROOT / "validation-output/av-bs1/control-plane"
CONTROL_PLANE_PRE_EXIT_EVIDENCE_ROOT = (
    ROOT / "validation-output/av-bs1/control-plane-pre-exit-evidence"
)
OUTER_OBSERVER_ROOT = ROOT / "validation-output/av-bs1/outer-observer"
OUTER_TERMINAL_SEAL_ROOT = (
    ROOT / "validation-output/av-bs1/outer-observer-terminal-seals"
)
WALL_STOP_SECONDS = 900
MONITOR_HANDSHAKE_WAIT_SECONDS = 60
CONTROL_PLANE_WALL_STOP_SECONDS = 180
AUTHORIZED_STAGE = "primary-h4-p0r"
SUCCESS_STATUS = "passed_AV_BS_h4_p0r_factor_only_pending_h4_p1_preregistration"
P0R_CONTRACT_COMMIT = "b00904aea76fa2029e778ba9240df46074ecbbef"
P0R_CONTRACT_BINDINGS: Mapping[str, tuple[Path, str]] = {
    "h4_p0r_fixture_sha256": (
        P0R_FIXTURE,
        "f6c4149e425021a9133d7ac98fbea403ba048e48f9c70d6e88171e3436374461",
    ),
    "h4_p0r_runner_sha256": (
        P0R_RUNNER,
        "ff6623280a728b2dfe6ad219e965d4c21d2392020b6d0490dcc7e63f43a9e50f",
    ),
    "h4_p0r_static_test_sha256": (
        P0R_TEST,
        "d071584cf6843ca9d2b75cac348ddfa343ac6f173646fe4c2575af68d6d73129",
    ),
    "h4_p0r_preregistration_doc_sha256": (
        P0R_DOC,
        "5db1b047ca72c72be338b6003507e04598c0b0b89b992302d3aea108a8d2e1f4",
    ),
}
AUDITORS = [
    "Sol mathematical and factor-contract review",
    "Terra Windows process-tree and one-shot lifecycle review",
    "Luna schema, provenance, and bounded-test review",
]
FACTOR_CERTIFICATE_FIELDS = frozenset(
    {
        "name", "input_shape", "input_dtype", "input_nnz",
        "input_sparse_sha256", "input_sparse_array_bytes",
        "raw_input_sparse_sha256", "row_scale_sha256",
        "column_scale_sha256", "equilibrated_sparse_sha256",
        "construction_started_perf_counter_ns", "construction_started_utc",
        "construction_cleanup_completed_perf_counter_ns",
        "construction_cleanup_completed_utc", "permc_spec",
        "diag_pivot_thresh", "superlu_equil", "rhs_count", "L_shape",
        "L_dtype", "L_nnz", "L_sha256", "L_storage_sha256",
        "L_indices_dtype", "L_indptr_dtype", "L_storage_canonical",
        "L_explicit_zero_count", "L_lower_triangular", "L_unit_diagonal",
        "U_shape", "U_dtype", "U_nnz", "U_sha256", "U_storage_sha256",
        "U_indices_dtype", "U_indptr_dtype", "U_storage_canonical",
        "U_explicit_zero_count", "U_upper_triangular", "U_diagonal_dtype",
        "U_diagonal_count", "U_diagonal_sha256", "U_diagonal_abs_min",
        "U_diagonal_abs_max", "U_diagonal_finite_nonzero", "perm_r_dtype",
        "perm_r_count", "perm_r_storage_dtype", "perm_r_sha256",
        "perm_r_bijective", "perm_c_dtype", "perm_c_count",
        "perm_c_storage_dtype", "perm_c_sha256", "perm_c_bijective",
        "stored_fill_ratio", "fill_ratio", "factor_array_bytes",
        "native_factor_nnz", "native_portable_factor_bytes",
        "native_pre_materialization_cap_pass",
        "exported_factor_bytes", "portable_factor_bytes",
        "one_factor_hard_cap_bytes", "factor_bytes_gate_pass",
        "factor_started_perf_counter_ns", "factor_returned_perf_counter_ns",
        "factor_wall_ns", "factor_wall_seconds", "factor_started_utc",
        "factor_returned_utc", "factorization_attempted",
        "factorization_performed", "factor_solve_called",
        "factor_objects_cleanup_started_perf_counter_ns",
        "factor_objects_cleanup_completed_perf_counter_ns",
        "factor_objects_cleanup_started_utc",
        "factor_objects_cleanup_completed_utc",
        "matrix_cleanup_started_perf_counter_ns",
        "matrix_cleanup_completed_perf_counter_ns", "matrix_cleanup_started_utc",
        "matrix_cleanup_completed_utc",
    }
)

# This process-global state is evidence, not authorization.  It exists so a
# fail-closed child result tells the runner whether SuperLU had been entered
# and whether at least one factor object had returned before the failure.
_EXECUTION_PHASE: dict[str, object] = {
    "claim_validated": False,
    "factorization_attempted": False,
    "factorization_performed": False,
    "active_factor": None,
    "completed_factors": [],
    "factor_certificates": [],
    "monitor_handshake": None,
}

AvBsError = h1.AvBsError

def _sha(path: Path) -> str:
    return h1._file_sha256(path)

def _canonical_sha(value: object) -> str:
    return sha256(h1.canonical_bytes(value)).hexdigest()

def _wrap(payload: Mapping[str, object]) -> Mapping[str, object]:
    return h1._wrapper(payload)

def _read(path: Path, label: str) -> Mapping[str, object]:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key}")
            result[key] = value
        return result

    def finite_float(text: str) -> float:
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("non-finite JSON float")
        return value

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            parse_float=finite_float,
            object_pairs_hook=unique_object,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} unreadable") from exc
    if not isinstance(value, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} must be an object")
    return value


def _read_wrapper(path: Path, label: str) -> tuple[Mapping[str, object], str]:
    wrapper = _read(path, label)
    if set(wrapper) != {"payload", "payload_sha256"}:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} wrapper field set mismatch")
    payload = wrapper.get("payload")
    checksum = _require_sha(wrapper.get("payload_sha256"), f"{label} payload checksum")
    if not isinstance(payload, Mapping) or checksum != _canonical_sha(payload):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} wrapper checksum invalid")
    return payload, checksum

def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} must be a lowercase SHA-256")
    return value


def _json_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} must be an integer")
    return value


def _finite_float(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} must be finite")
    return float(value)


def _strict_json_equal(actual: object, expected: object) -> bool:
    """Compare JSON values without Python's ``bool``/``int`` aliasing."""
    if expected is None:
        return actual is None
    if type(expected) in (bool, int, float, str):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(actual) == set(expected)
            and all(_strict_json_equal(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_strict_json_equal(left, right) for left, right in zip(actual, expected))
        )
    return type(actual) is type(expected) and actual == expected

def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} missing")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} invalid") from exc
    if instant.tzinfo is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} lacks timezone")
    return instant.astimezone(timezone.utc)

def _validate_p0r_contract_parent() -> Mapping[str, str]:
    observed: dict[str, str] = {"h4_p0r_contract_commit": P0R_CONTRACT_COMMIT}
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", P0R_CONTRACT_COMMIT, "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "H4-P0R contract commit is not an ancestor"
        ) from exc
    for key, (path, expected) in P0R_CONTRACT_BINDINGS.items():
        actual = _sha(path)
        if actual != expected:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{key} drift")
        observed[key] = actual
    return observed


def _parent() -> Mapping[str, object]:
    _validate_p0r_contract_parent()
    wrapper = p0r.validated_manifest_wrapper()
    payload = wrapper["payload"]
    if not isinstance(payload, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P0R payload missing")
    if payload.get("status") != "preregistered_H4_P0R_contract_only_no_factor":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P0R status drift")
    if payload.get("authorization_state") != "not_authorized" or payload.get("next_stage_authorized") is not False:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P0R authorization drift")
    if payload.get("factorization_performed") is not False or payload.get("physics_solve_performed") is not False:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P0R scope drift")
    return payload

def _p1_bindings(parent: Mapping[str, object]) -> Mapping[str, object]:
    parent_bindings = {
        **dict(_validate_p0r_contract_parent()),
        "h4_p0_commit": p0r.H4_P0_COMMIT,
        "h4_p0_manifest_payload_sha256": p0r.H4_P0_PAYLOAD_SHA256,
        **dict(p0r.H4_P0_BINDINGS),
        "h2_artifact_file_sha256": p0r.H2_ARTIFACT_FILE_SHA256,
        "h2_artifact_payload_sha256": p0r.H2_ARTIFACT_PAYLOAD_SHA256,
        "h2_numerical_payload_sha256": p0r.H2_NUMERICAL_PAYLOAD_SHA256,
        "h2_resource_report_sha256": p0r.H2_RESOURCE_REPORT_SHA256,
        "h2_consumed_tombstone_sha256": p0r.H2_TOMBSTONE_SHA256,
    }
    return {
        "fixture_sha256": _sha(Path(__file__)),
        "runner_sha256": _sha(P1_RUNNER),
        "static_test_sha256": _sha(P1_TEST),
        "preregistration_doc_sha256": _sha(P1_DOC),
        "manifest_payload_sha256": _wrap(parent)["payload_sha256"],
        "matrix_contract_sha256": parent["matrix_inputs"]["matrix_contract_sha256"],
        "resource_policy_sha256": parent["resource_policy_sha256"],
        "parent_bindings": parent_bindings,
    }

def _policy(parent: Mapping[str, object]) -> Mapping[str, object]:
    policy = parent.get("resource_policy")
    if not isinstance(policy, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "P0R resource policy missing")
    exact = {"wall_stop_seconds": WALL_STOP_SECONDS, "tree_working_set_stop_bytes": 4 * 1024**3,
             "tree_private_stop_bytes": 5 * 1024**3, "tree_commit_stop_bytes": 5 * 1024**3,
             "commit_headroom_floor_bytes": 2 * 1024**3, "available_physical_floor_bytes": int(1.5 * 1024**3),
             "one_factor_hard_cap_bytes": 2 * 1024**3, "factor_fit_unproven": True,
             "sequential_one_factor_resident": True, "process_tree_includes_runner": True}
    for key, value in exact.items():
        if not _strict_json_equal(policy.get(key), value):
            raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"P0R resource policy {key} drift")
    return dict(policy)


def _execution_resource_scope() -> Mapping[str, object]:
    """Versioned P1 correction to the broader manifest-only P0R wording."""
    return {
        "schema": "AV-BS1-h4-p0r-execution-resource-scope-v1",
        "contract_revision": "P1_versioned_scope_correction_v1",
        "supersedes_parent_claims": [
            "runner_plus_every_descendant_without_interval_qualification",
            "race_free_process_membership_or_exact_lifetime_peak_preservation",
        ],
        "factor_fit_envelope": {
            "wall_start_event": "before_exclusive_claim_and_guard_materialization",
            "wall_end_event": "verified_primary_tree_exit_or_cleanup_then_terminal_system_sample_and_stopwatch_stop",
            "process_peak_start_event": "first_successful_runner_plus_primary_tree_sample",
            "process_peak_end_event": "last_successful_runner_plus_primary_tree_sample_before_verified_exit",
            "execution_tree_root": "outer_observer_pid_and_birth",
            "included_processes": "outer_observer_plus_inner_runner_plus_identity_bound_processes_present_in_successful_100ms_Toolhelp32_samples",
            "process_membership_semantics": "sampled_not_Job_Object; descendants_created_and_exited_between_polls_are_not_claimed",
            "peak_semantics": "sampled_simultaneous_tree_sums_are_factor_interval_measurements; outer_and_inner_OS_lifetime_peaks_are_conservative_lifecycle_contaminated_stop_gates_only",
            "factor_interval_coverage": "monitor_ready_factor_complete_monitor_release_actual_sample_handshake",
            "wall_clock": "System.Diagnostics.Stopwatch",
        },
        "control_plane": {
            "excluded_from_factor_interval_simultaneous_measurements": [
                "preflight", "canonical_json_hash_helpers", "finalizer", "token_consumer"
            ],
            "conservative_outer_and_inner_lifetime_stop_fields_include_control_plane_history": True,
            "independently_bounded": True,
            "wall_stop_seconds_each": CONTROL_PLANE_WALL_STOP_SECONDS,
            "tree_thresholds_equal_factor_envelope": True,
            "system_floor_recheck_before_and_after_each": True,
            "implementation_status": "implemented_and_static_audited",
            "authorization_blocker": False,
            "also_included_in_outer_lifecycle_envelope": True,
        },
        "authorization_effect": "factor_fit_evidence_only_no_h4_p1_authorization",
    }


def _outer_observer_contract() -> Mapping[str, object]:
    return {
        "schema": OUTER_OBSERVER_CONTRACT_SCHEMA,
        "contract_revision": "P1_outer_observer_v1",
        "public_stage": AUTHORIZED_STAGE,
        "hidden_internal_mode": "primary-h4-p0r-inner-v1",
        "observer_session_root_relative_path": (
            "validation-output/av-bs1/outer-observer"
        ),
        "terminal_seal_root_relative_path": (
            "validation-output/av-bs1/outer-observer-terminal-seals"
        ),
        "marker_sequence": [
            {
                "role": "inner_ready",
                "file_name": "inner-ready.json",
                "schema": OUTER_INNER_READY_SCHEMA,
                "writer": "inner",
            },
            {
                "role": "outer_start_release",
                "file_name": "outer-start-release.json",
                "schema": OUTER_START_RELEASE_SCHEMA,
                "writer": "outer",
            },
            {
                "role": "inner_complete",
                "file_name": "inner-complete.json",
                "schema": OUTER_INNER_COMPLETE_SCHEMA,
                "writer": "inner",
            },
            {
                "role": "outer_exit_release",
                "file_name": "outer-exit-release.json",
                "schema": OUTER_EXIT_RELEASE_SCHEMA,
                "writer": "outer",
            },
        ],
        "terminal_seal_schema": OUTER_TERMINAL_SEAL_SCHEMA,
        "outer_resource_envelope_close_schema": (
            OUTER_RESOURCE_ENVELOPE_CLOSE_SCHEMA
        ),
        "outer_resource_envelope_close_file_name": (
            "outer-resource-envelope-close.json"
        ),
        "handshake_prefix_schema": OUTER_HANDSHAKE_PREFIX_SCHEMA,
        "terminal_seal_required_for_authoritative_disposition": True,
        "outer_resource_envelope": {
            "wall_start_event": "dispatcher_stopwatch_start_before_candidate_token_hash_session_setup_and_inner_spawn",
            "wall_end_event": "after_retained_inner_actual_exit_cleanup_and_final_preseal_samples_before_envelope_close_materialization",
            "wall_stop_seconds": WALL_STOP_SECONDS,
            "tree_thresholds_equal_factor_envelope": True,
            "system_floor_recheck_before_and_after": True,
            "execution_tree_root": "outer_observer_pid_and_birth",
            "included_processes": "outer_observer_plus_identity_bound_sampled_inner_tree",
            "process_membership_semantics": "sampled_not_Job_Object_descendants_created_and_exited_between_polls_not_claimed",
            "excluded_head": [
                "public_PowerShell_process_startup_and_script_parse",
                "function_and_native_monitor_type_initialization",
                "immutable_runner_path_and_argument_discovery_before_dispatcher_stopwatch",
            ],
            "excluded_tail": [
                "post_cleanup_token_and_claim_recovery_classification",
                "outer_resource_envelope_close_materialization_and_readback",
                "terminal_seal_source_rechecks_materialization_and_readback",
                "inner_stdout_text_return_packaging",
                "outer_observer_exit",
            ],
            "seal_binding": "terminal_seal_binds_outer_resource_envelope_close_raw_and_canonical_hashes",
        },
        "seal_writer": "outer_after_retained_inner_handle_exit_observation",
        "seal_observes": "inner_runner_actual_exit_only",
        "claim_binding": (
            "ready_and_start_release_prefix_only_no_forward_completion_reference"
        ),
        "execution_tree_root": "outer_observer_pid_and_birth",
        "missing_or_invalid_seal_disposition": (
            "provisional_no_authoritative_pass_or_H4_P1_authorization"
        ),
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "adversarial_local_caller_exclusion_claimed": False,
        "implementation_status": "implemented_and_static_audited",
        "authorization_blocker": False,
    }


def _current_execution_schemas() -> Mapping[str, str]:
    return {
        "execution_fixture": SCHEMA,
        "execution_resource_scope": "AV-BS1-h4-p0r-execution-resource-scope-v1",
        "outer_observer_contract": OUTER_OBSERVER_CONTRACT_SCHEMA,
        "review_token": TOKEN_SCHEMA,
        "claim": CLAIM_SCHEMA,
        "guard": GUARD_SCHEMA,
        "resource": RESOURCE_SCHEMA,
        "factor_report": NUMERICAL_SCHEMA,
        "result": RESULT_SCHEMA,
        "failure": FAILURE_SCHEMA,
        "consumed_tombstone": TOMBSTONE_SCHEMA,
        "emergency_consumed_tombstone": EMERGENCY_TOMBSTONE_SCHEMA,
        "emergency_replacement_intent": EMERGENCY_REPLACEMENT_INTENT_SCHEMA,
        "emergency_replacement_postvalidation": (
            EMERGENCY_REPLACEMENT_POSTVALIDATION_SCHEMA
        ),
        "control_plane_process_report": CONTROL_PLANE_REPORT_SCHEMA,
        "control_plane_session_index": CONTROL_PLANE_INDEX_SCHEMA,
        "control_plane_envelope_close": CONTROL_PLANE_ENVELOPE_CLOSE_SCHEMA,
        "control_plane_pre_exit_intent": CONTROL_PLANE_PRE_EXIT_EVIDENCE_SCHEMA,
        "control_plane_bootstrap_ready": (
            "AV-BS1-h4-p0r-control-plane-bootstrap-ready-v1"
        ),
        "control_plane_start_release": (
            "AV-BS1-h4-p0r-control-plane-start-release-v1"
        ),
        "control_plane_target_complete": (
            "AV-BS1-h4-p0r-control-plane-target-complete-v1"
        ),
        "control_plane_exit_release": (
            "AV-BS1-h4-p0r-control-plane-exit-release-v1"
        ),
        "factor_monitor_ready": "AV-BS1-h4-p0r-monitor-ready-v1",
        "factor_complete": "AV-BS1-h4-p0r-factor-complete-v1",
        "factor_monitor_release": "AV-BS1-h4-p0r-monitor-release-v1",
        "factor_monitor_handshake": "AV-BS1-h4-p0r-factor-monitor-handshake-v1",
        "outer_inner_ready": OUTER_INNER_READY_SCHEMA,
        "outer_start_release": OUTER_START_RELEASE_SCHEMA,
        "outer_observer_handshake_prefix": OUTER_HANDSHAKE_PREFIX_SCHEMA,
        "outer_inner_complete": OUTER_INNER_COMPLETE_SCHEMA,
        "outer_exit_release": OUTER_EXIT_RELEASE_SCHEMA,
        "outer_resource_envelope_close": OUTER_RESOURCE_ENVELOPE_CLOSE_SCHEMA,
        "outer_terminal_seal": OUTER_TERMINAL_SEAL_SCHEMA,
        "manifest_terminal_evidence_classification": (
            OUTER_TERMINAL_CLASSIFICATION_SCHEMA
        ),
    }


def _authorization_prerequisites_ready(
    execution_scope: Mapping[str, object], outer_observer: Mapping[str, object]
) -> bool:
    control = execution_scope.get("control_plane")
    outer_envelope = outer_observer.get("outer_resource_envelope")
    return bool(
        isinstance(control, Mapping)
        and control.get("independently_bounded") is True
        and type(control.get("wall_stop_seconds_each")) is int
        and control.get("wall_stop_seconds_each") == CONTROL_PLANE_WALL_STOP_SECONDS
        and control.get("tree_thresholds_equal_factor_envelope") is True
        and control.get("system_floor_recheck_before_and_after_each") is True
        and control.get("implementation_status") == "implemented_and_static_audited"
        and control.get("authorization_blocker") is False
        and outer_observer.get("implementation_status")
        == "implemented_and_static_audited"
        and outer_observer.get("authorization_blocker") is False
        and outer_observer.get(
            "terminal_seal_required_for_authoritative_disposition"
        )
        is True
        and outer_observer.get("seal_writer")
        == "outer_after_retained_inner_handle_exit_observation"
        and outer_observer.get("seal_observes") == "inner_runner_actual_exit_only"
        and isinstance(outer_envelope, Mapping)
        and type(outer_envelope.get("wall_stop_seconds")) is int
        and outer_envelope.get("wall_stop_seconds") == WALL_STOP_SECONDS
        and outer_envelope.get("tree_thresholds_equal_factor_envelope") is True
        and outer_envelope.get("system_floor_recheck_before_and_after") is True
        and outer_envelope.get("execution_tree_root")
        == "outer_observer_pid_and_birth"
    )

def run_manifest() -> Mapping[str, object]:
    parent = _parent()
    policy = _policy(parent)
    bindings = _p1_bindings(parent)
    execution_scope = _execution_resource_scope()
    outer_observer = _outer_observer_contract()
    token_probe: Mapping[str, object] | None = None
    token_sha256: str | None = None
    if TOKEN_PATH.is_file():
        try:
            token_probe, token_sha256 = _read_stable_json(
                TOKEN_PATH, "review token state probe"
            )
        except (AvBsError, OSError):
            token_probe = {}
            try:
                token_sha256 = _sha(TOKEN_PATH)
            except OSError:
                token_sha256 = None
    preliminary_token_state = "absent"
    if token_probe is not None:
        preliminary_token_state = (
            "authorized_shape_pending_validation"
            if token_probe.get("schema") == TOKEN_SCHEMA
            and token_probe.get("authorization_state") == "authorized"
            and type(token_probe.get("uses_remaining")) is int
            and token_probe.get("uses_remaining") == 1
            else "present_invalid_no_factor"
        )
    control_plane_ready = _authorization_prerequisites_ready(
        execution_scope, outer_observer
    )
    token_gate_configured = (
        preliminary_token_state == "authorized_shape_pending_validation"
        and control_plane_ready
    )
    manifest: dict[str, object] = {
            "schema": SCHEMA, "program": PROGRAM, "case_id": CASE_ID, "stage": "manifest",
            "status": "terminal_evidence_classification_pending",
            "authorization_state": "not_authorized", "token_state": preliminary_token_state,
            "p0r_parent": {"status": parent["status"], "bindings": parent["bindings"], "matrix_inputs": parent["matrix_inputs"]},
            "bindings": bindings, "resource_policy": policy, "resource_policy_sha256": _canonical_sha(policy),
            "execution_resource_scope": execution_scope,
            "execution_resource_scope_sha256": _canonical_sha(execution_scope),
            "outer_observer_contract": outer_observer,
            "outer_observer_contract_sha256": _canonical_sha(outer_observer),
            "current_execution_schemas": _current_execution_schemas(),
            "factorization_contract": parent["factorization_contract"],
            "future_schemas": parent["future_schemas"],
            "one_use_lifecycle": {
                **dict(parent["one_use_lifecycle"]),
                "token_absent": preliminary_token_state == "absent",
                "tracked_clean_token_required": True,
                "consume_after_every_owned_claim_outcome": False,
                "external_runner_or_consumer_kill_tombstone_guarantee": False,
                "lifecycle_supervisor_status": "implemented_and_static_audited",
                "terminal_seal_required_for_authoritative_disposition": True,
                "tombstone_success_remains_provisional_without_terminal_seal": True,
            },
            "forbidden_operations": {"rhs_generated": False, "linear_solve_called": False, "extensions_generated": False,
                "boundary_operator_generated": False, "schur_or_Y_generated": False, "modal_response_generated": False,
                "PDE_residual_generated": False, "power_generated": False,
                "analytic_or_convergence_gate_generated": False,
                "physics_solve_performed": False},
            "failure_code_allowlist": sorted(FAILURE_CODES), "factorization_performed": False,
            "physics_solve_performed": False, "available_solve_stages": [],
            "token_gated_stages": ["primary-h4-p0r"] if token_gate_configured else [],
             "unavailable_stages": (
                 ["h4", "withheld", "EQ0"] if token_gate_configured
                 else ["primary-h4-p0r", "h4", "withheld", "EQ0"]
             ), "next_stage_authorized": False}
    token_state, status, terminal = _classify_manifest_terminal_evidence(
        manifest, token_probe, token_sha256
    )
    manifest["token_state"] = token_state
    manifest["status"] = status
    manifest["terminal_evidence"] = terminal
    manifest["terminal_evidence_complete"] = terminal[
        "authoritative_terminal_evidence"
    ]
    manifest["authoritative_stage_pass"] = terminal["authoritative_stage_pass"]
    manifest["one_use_lifecycle"] = {
        **dict(manifest["one_use_lifecycle"]),
        "token_absent": token_state == "absent",
        "terminal_evidence_classification": terminal["classification"],
        "authoritative_stage_pass": terminal["authoritative_stage_pass"],
    }
    token_gate_configured = bool(
        token_state == "authorized_shape_pending_validation"
        and control_plane_ready
    )
    manifest["token_gated_stages"] = (
        ["primary-h4-p0r"] if token_gate_configured else []
    )
    manifest["unavailable_stages"] = (
        ["h4", "withheld", "EQ0"]
        if token_gate_configured
        else ["primary-h4-p0r", "h4", "withheld", "EQ0"]
    )
    return manifest

def _validate_checkout(token: Mapping[str, object], token_path: Path) -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        prereg = token.get("p0r_preregistration_commit")
        if not isinstance(prereg, str) or re.fullmatch(r"[0-9a-f]{40}", prereg) is None:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P0R preregistration commit is invalid")
        subprocess.run(["git", "merge-base", "--is-ancestor", p0r.H4_P0_COMMIT, head], cwd=ROOT, check=True, capture_output=True)
        parent = subprocess.run(["git", "rev-parse", "HEAD^"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        if prereg != parent:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P0R preregistration is not token commit parent")
        parents = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", head],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        if len(parents) != 2 or parents[1] != prereg:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "review-token commit must have exactly the preregistration parent",
            )
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, capture_output=True, text=True, check=True)
        if status.stdout.strip():
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "research checkout is not clean")
        relative = token_path.resolve().relative_to(ROOT).as_posix()
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT, check=True, capture_output=True)
        latest = subprocess.run(["git", "log", "-1", "--format=%H", "--", relative], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        if latest != head:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token is not from current clean commit")
        changed_lines = [
            value.rstrip("\r\n")
            for value in subprocess.run(
                ["git", "diff", "--name-status", prereg, head],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            if value.strip()
        ]
        if changed_lines != [f"A\t{relative}"]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "review-token commit must add only the canonical token",
            )
        parent_has_token = subprocess.run(
            ["git", "cat-file", "-e", f"{prereg}:{relative}"],
            cwd=ROOT,
            capture_output=True,
        )
        if parent_has_token.returncode == 0:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "P0R executable-contract parent already contains a token",
            )
        return head
    except AvBsError:
        raise
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "clean research checkout proof failed") from exc


def _validate_token(
    path: Path,
    manifest: Mapping[str, object],
    *,
    check_expiry: bool = True,
) -> Mapping[str, object]:
    if path.resolve() != TOKEN_PATH.resolve() or not path.is_file():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token path is not canonical")
    control_plane = manifest.get("execution_resource_scope", {}).get("control_plane")
    outer_observer = manifest.get("outer_observer_contract")
    if (
        not isinstance(control_plane, Mapping)
        or not isinstance(outer_observer, Mapping)
        or not _authorization_prerequisites_ready(
            manifest["execution_resource_scope"], outer_observer
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "control-plane and outer-observer authorization prerequisites are incomplete",
        )
    token = _read(path, "review token")
    binding = manifest["bindings"]
    fixed = {"schema": TOKEN_SCHEMA, "program": PROGRAM, "case_id": CASE_ID, "authorized_stage": "primary-h4-p0r",
             "authorization_state": "authorized", "uses_remaining": 1, "next_stage_authorized": False,
             "fixture_sha256": binding["fixture_sha256"], "runner_sha256": binding["runner_sha256"],
             "static_test_sha256": binding["static_test_sha256"], "preregistration_doc_sha256": binding["preregistration_doc_sha256"],
             "manifest_payload_sha256": binding["manifest_payload_sha256"], "matrix_contract_sha256": binding["matrix_contract_sha256"],
              "resource_policy_sha256": manifest["resource_policy_sha256"],
              "execution_resource_scope_sha256": manifest["execution_resource_scope_sha256"],
              "outer_observer_contract_sha256": manifest["outer_observer_contract_sha256"],
              "terminal_seal_required_for_authoritative_disposition": True,
              "parent_bindings": binding["parent_bindings"],
             "review_scope": "authorize_h4_p0r_factor_only_after_clean_p1_checkout",
             "review_disposition": "approved_h4_p0r_factor_only"}
    required_keys = set(fixed) | {
        "review_token_id",
        "reviewed_utc",
        "expires_utc",
        "p0r_preregistration_commit",
        "independent_audits",
        "expected_terminal_seal_relative_path",
    }
    if set(token) != required_keys:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token field set mismatch")
    for key, expected in fixed.items():
        if not _strict_json_equal(token.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"review token {key} mismatch")
    identifier = token.get("review_token_id")
    if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token id invalid")
    if token.get("expected_terminal_seal_relative_path") != (
        f"validation-output/av-bs1/outer-observer-terminal-seals/{identifier}.json"
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "review token terminal-seal path mismatch",
        )
    reviewed = _utc(token.get("reviewed_utc"), "reviewed_utc")
    expires = _utc(token.get("expires_utc"), "expires_utc")
    if reviewed >= expires or reviewed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token chronology invalid")
    if not isinstance(token.get("p0r_preregistration_commit"), str) or re.fullmatch(r"[0-9a-f]{40}", token["p0r_preregistration_commit"]) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P1 preregistration commit invalid")
    if check_expiry and expires <= datetime.now(timezone.utc):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token expired")
    audits = token.get("independent_audits")
    if audits != AUDITORS:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "independent audits missing")
    _validate_checkout(token, path)
    return token

def preflight(
    token_path: Path, *, check_expiry: bool = True
) -> Mapping[str, object]:
    manifest = run_manifest()
    token = _validate_token(token_path, manifest, check_expiry=check_expiry)
    git_head = _validate_checkout(token, token_path)
    return {"schema": SCHEMA, "program": PROGRAM, "case_id": CASE_ID, "stage": "preflight-primary-h4-p0r",
            "status": "preflight_pass_token_gated_no_factor", "authorization_state": "authorized",
            "preflight_pass": True, "git_head": git_head, "p0r_preregistration_commit": token["p0r_preregistration_commit"],
            "review_token_id": token["review_token_id"], "review_token_sha256": _sha(token_path),
            "review_binding_sha256": _canonical_sha({"token_sha256": _sha(token_path), "bindings": manifest["bindings"]}),
             "manifest_payload_sha256": _wrap(manifest)["payload_sha256"],
             "manifest_bindings": manifest["bindings"],
             "resource_policy_sha256": manifest["resource_policy_sha256"],
             "execution_resource_scope_sha256": manifest["execution_resource_scope_sha256"],
             "outer_observer_contract_sha256": manifest["outer_observer_contract_sha256"],
             "expected_terminal_seal_relative_path": token[
                 "expected_terminal_seal_relative_path"
             ],
             "terminal_seal_required_for_authoritative_disposition": True,
             "factorization_performed": False,
            "physics_solve_performed": False, "next_stage_authorized": False}

def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "git head unavailable") from exc

def _validate_guard(path: Path, nonce: str, token: Mapping[str, object], manifest: Mapping[str, object], claim: Path) -> Mapping[str, object]:
    guard = _read(path, "guard")
    policy = manifest["resource_policy"]
    claim_value = _validate_claim(claim, token, manifest)
    if claim_value.get("guard_nonce") != nonce:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "claim guard nonce mismatch")
    fixed = {"schema": GUARD_SCHEMA, "program": PROGRAM, "case_id": CASE_ID, "stage": "primary-h4-p0r", "nonce": nonce,
             "review_token_id": token["review_token_id"], "review_token_sha256": _sha(TOKEN_PATH),
             "review_token_canonical_sha256": _canonical_sha(token),
             "fixture_sha256": manifest["bindings"]["fixture_sha256"], "runner_sha256": manifest["bindings"]["runner_sha256"],
             "claim_relative_path": claim_value["claim_relative_path"], "claim_sha256": _sha(claim),
             "claim_canonical_sha256": _canonical_sha(claim_value),
             "preflight_payload_sha256": claim_value["preflight_payload_sha256"],
             "resource_guard_policy_sha256": manifest["resource_policy_sha256"],
             "execution_resource_scope_sha256": manifest["execution_resource_scope_sha256"],
             "parent_pid": claim_value["parent_pid"],
             "parent_pid_birth_utc_ticks": claim_value["parent_pid_birth_utc_ticks"],
             "poll_interval_ms": 100, "wall_stop_seconds": policy["wall_stop_seconds"],
             "tree_ws_stop_bytes": policy["tree_working_set_stop_bytes"], "tree_private_stop_bytes": policy["tree_private_stop_bytes"],
             "tree_commit_stop_bytes": policy["tree_commit_stop_bytes"], "commit_headroom_floor_bytes": policy["commit_headroom_floor_bytes"],
             "available_physical_floor_bytes": policy["available_physical_floor_bytes"],
             "minimum_commit_headroom_before_spawn_bytes": policy["minimum_commit_headroom_before_spawn_bytes"],
             "minimum_available_physical_before_spawn_bytes": policy["minimum_available_physical_before_spawn_bytes"],
             "pre_spawn_resource_gate_pass": True}
    if set(guard) != set(fixed) | {
        "monitor_ok", "baseline_commit_headroom_bytes", "baseline_available_physical_bytes"
    }:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard field set mismatch")
    for key, expected in fixed.items():
        if not _strict_json_equal(guard.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"guard {key} mismatch")
    if guard.get("monitor_ok") is not True or not _strict_json_equal(
        guard.get("parent_pid"), claim_value["parent_pid"]
    ):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard monitor/parent binding mismatch")
    if (
        _json_int(guard.get("baseline_commit_headroom_bytes"), "guard baseline commit headroom")
        < policy["minimum_commit_headroom_before_spawn_bytes"]
        or _json_int(guard.get("baseline_available_physical_bytes"), "guard baseline available physical")
        < policy["minimum_available_physical_before_spawn_bytes"]
    ):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard pre-spawn resource floor failed")
    return guard


def _ordered_json_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=False,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _control_plane_artifact_path(
    relative_value: object, *, label: str, pattern: str
) -> Path:
    if (
        not isinstance(relative_value, str)
        or "\\" in relative_value
        or re.fullmatch(pattern, relative_value) is None
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} relative path invalid"
        )
    candidate = (ROOT / relative_value).resolve()
    try:
        actual_relative = candidate.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} path escaped checkout"
        ) from exc
    if actual_relative != relative_value or not candidate.is_file():
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} path is not canonical"
        )
    return candidate


def _validate_control_plane_identity_list(
    identities: object, ids: object, *, label: str
) -> None:
    if (
        not isinstance(ids, list)
        or any(type(value) is not int or value <= 0 for value in ids)
        or ids != sorted(set(ids))
        or not isinstance(identities, list)
        or len(identities) != len(ids)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} process identities invalid"
        )
    observed: list[int] = []
    for row in identities:
        if not isinstance(row, Mapping) or set(row) != {
            "process_id",
            "birth_utc_ticks",
        }:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} identity row invalid"
            )
        process_id = _json_int(row.get("process_id"), f"{label} process id")
        birth = _json_int(row.get("birth_utc_ticks"), f"{label} birth ticks")
        if process_id <= 0 or birth <= 0:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} identity value invalid"
            )
        observed.append(process_id)
    if observed != ids:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} identity/id mismatch"
        )


def _validate_control_tree_sample(value: object, *, label: str) -> Mapping[str, object]:
    fields = {
        "process_ids",
        "process_count",
        "working_set_bytes",
        "summed_process_peak_working_set_bytes",
        "committed_pagefile_bytes",
        "summed_process_peak_commit_bytes",
        "private_commit_bytes",
        "private_working_set_bytes",
        "nonprivate_working_set_proxy_bytes",
        "shared_commit_bytes",
        "page_fault_count",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} field set mismatch"
        )
    ids = value.get("process_ids")
    if (
        not isinstance(ids, list)
        or any(type(item) is not int or item <= 0 for item in ids)
        or ids != sorted(set(ids))
        or _json_int(value.get("process_count"), f"{label} process count")
        != len(ids)
        or not ids
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} process set invalid")
    for key in fields - {"process_ids", "process_count"}:
        if _json_int(value.get(key), f"{label} {key}") < 0:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} {key} is negative"
            )
    if _json_int(
        value.get("nonprivate_working_set_proxy_bytes"), f"{label} nonprivate"
    ) != max(
        0,
        _json_int(value.get("working_set_bytes"), f"{label} working set")
        - _json_int(
            value.get("private_working_set_bytes"), f"{label} private working set"
        ),
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} nonprivate arithmetic mismatch"
        )
    return value


def _validate_control_peak(
    value: object, policy: Mapping[str, object], *, label: str
) -> Mapping[str, object]:
    fields = {
        "tree_working_set_bytes",
        "tree_summed_process_lifetime_peak_working_set_bytes",
        "tree_private_commit_bytes",
        "tree_committed_pagefile_bytes",
        "tree_summed_process_lifetime_peak_commit_bytes",
        "tree_nonprivate_working_set_proxy_bytes",
        "tree_page_fault_count",
        "system_commit_total_bytes",
        "system_commit_headroom_min_bytes",
        "available_physical_min_bytes",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} field set mismatch"
        )
    observed = {
        key: _json_int(value.get(key), f"{label} {key}") for key in fields
    }
    if any(item < 0 for item in observed.values()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} has negative value")
    limits = {
        "tree_working_set_bytes": policy["tree_working_set_stop_bytes"],
        "tree_summed_process_lifetime_peak_working_set_bytes": policy[
            "tree_working_set_stop_bytes"
        ],
        "tree_private_commit_bytes": policy["tree_private_stop_bytes"],
        "tree_committed_pagefile_bytes": policy["tree_commit_stop_bytes"],
        "tree_summed_process_lifetime_peak_commit_bytes": policy[
            "tree_commit_stop_bytes"
        ],
    }
    for key, limit in limits.items():
        if observed[key] > limit:
            raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"{label} {key} exceeded")
    if (
        observed["system_commit_headroom_min_bytes"]
        < policy["commit_headroom_floor_bytes"]
        or observed["available_physical_min_bytes"]
        < policy["available_physical_floor_bytes"]
    ):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"{label} system floor failed")
    return value


def _require_control_peak_covers_tree_sample(
    peak: Mapping[str, object], sample: Mapping[str, object], *, label: str
) -> None:
    pairs = {
        "tree_working_set_bytes": "working_set_bytes",
        "tree_summed_process_lifetime_peak_working_set_bytes": (
            "summed_process_peak_working_set_bytes"
        ),
        "tree_private_commit_bytes": "private_commit_bytes",
        "tree_committed_pagefile_bytes": "committed_pagefile_bytes",
        "tree_summed_process_lifetime_peak_commit_bytes": (
            "summed_process_peak_commit_bytes"
        ),
        "tree_nonprivate_working_set_proxy_bytes": (
            "nonprivate_working_set_proxy_bytes"
        ),
        "tree_page_fault_count": "page_fault_count",
    }
    for peak_key, sample_key in pairs.items():
        if _json_int(peak.get(peak_key), f"{label} peak {peak_key}") < _json_int(
            sample.get(sample_key), f"{label} sample {sample_key}"
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"{label} peak does not cover {sample_key}",
            )


def _require_control_peak_covers_system_sample(
    peak: Mapping[str, object], sample: Mapping[str, object], *, label: str
) -> None:
    if (
        _json_int(
            peak.get("system_commit_total_bytes"), f"{label} peak commit total"
        )
        < _json_int(sample.get("commit_total_bytes"), f"{label} commit total")
        or _json_int(
            peak.get("system_commit_headroom_min_bytes"),
            f"{label} peak commit headroom",
        )
        > _json_int(
            sample.get("commit_headroom_bytes"), f"{label} commit headroom"
        )
        or _json_int(
            peak.get("available_physical_min_bytes"),
            f"{label} peak available physical",
        )
        > _json_int(
            sample.get("available_physical_bytes"), f"{label} available physical"
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            f"{label} system sample is not represented by the claimed peak",
        )


def _owned_control_file(
    raw_path: object,
    *,
    label: str,
    parent: Path | None = None,
    name: str | None = None,
) -> Path:
    if not isinstance(raw_path, (str, os.PathLike)):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} path invalid")
    candidate = Path(raw_path).resolve()
    if not candidate.is_file():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is missing")
    if parent is not None and candidate.parent != parent.resolve():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} parent mismatch")
    if name is not None and candidate.name != name:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} name mismatch")
    return candidate


def _validate_control_reference(
    value: object,
    *,
    operation: str,
    invocation_id: str,
    report_path: Path,
    report_sha256: str,
    report: Mapping[str, object],
    envelope_close_path: Path | None,
    envelope_close_sha256: str | None,
    mandatory_gate: bool,
) -> Mapping[str, object]:
    fields = {
        "operation",
        "invocation_id",
        "report_path",
        "report_sha256",
        "target_argv_json_sha256",
        "process_argv_json_sha256",
        "attempt_provenance_before_sha256",
        "attempt_provenance_after_sha256",
        "envelope_close_path",
        "envelope_close_sha256",
        "mandatory_control_plane_gate_pass",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "control report reference field set mismatch"
        )
    expected = {
        "operation": operation,
        "invocation_id": invocation_id,
        "report_path": str(report_path),
        "report_sha256": report_sha256,
        "target_argv_json_sha256": report["target_argv_json_sha256"],
        "process_argv_json_sha256": report["process_argv_json_sha256"],
        "attempt_provenance_before_sha256": report[
            "attempt_provenance_before_sha256"
        ],
        "attempt_provenance_after_sha256": report[
            "attempt_provenance_after_sha256"
        ],
        "envelope_close_path": (
            str(envelope_close_path) if envelope_close_path is not None else None
        ),
        "envelope_close_sha256": envelope_close_sha256,
        "mandatory_control_plane_gate_pass": mandatory_gate,
    }
    if not _strict_json_equal(value, expected):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "control report reference mismatch"
        )
    return value


def _validate_outer_handshake_prefix(
    claim: Mapping[str, object],
    token: Mapping[str, object],
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    prefix = claim.get("outer_observer_handshake_prefix")
    prefix_fields = {
        "schema",
        "inner_ready_relative_path",
        "inner_ready_sha256",
        "outer_start_release_relative_path",
        "outer_start_release_sha256",
    }
    if (
        not isinstance(prefix, Mapping)
        or set(prefix) != prefix_fields
        or prefix.get("schema") != OUTER_HANDSHAKE_PREFIX_SCHEMA
        or _canonical_sha(prefix)
        != _require_sha(
            claim.get("outer_observer_handshake_prefix_sha256"),
            "outer observer handshake prefix",
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "outer observer handshake prefix mismatch",
        )
    ready_path = _control_plane_artifact_path(
        prefix.get("inner_ready_relative_path"),
        label="outer observer inner-ready",
        pattern=(
            r"validation-output/av-bs1/outer-observer/"
            r"session-[0-9a-f]{32}/inner-ready\.json"
        ),
    )
    release_path = _control_plane_artifact_path(
        prefix.get("outer_start_release_relative_path"),
        label="outer observer start-release",
        pattern=(
            r"validation-output/av-bs1/outer-observer/"
            r"session-[0-9a-f]{32}/outer-start-release\.json"
        ),
    )
    if (
        ready_path.parent != release_path.parent
        or _sha(ready_path)
        != _require_sha(prefix.get("inner_ready_sha256"), "outer inner-ready")
        or _sha(release_path)
        != _require_sha(
            prefix.get("outer_start_release_sha256"), "outer start-release"
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer observer marker binding mismatch"
        )
    session_relative = ready_path.parent.relative_to(ROOT).as_posix()
    ready = _read(ready_path, "outer observer inner-ready")
    release = _read(release_path, "outer observer start-release")
    ready_fields = {
        "schema",
        "program",
        "case_id",
        "stage",
        "internal_mode",
        "observer_session_relative_path",
        "observer_nonce",
        "outer_process_id",
        "outer_process_birth_utc_ticks",
        "inner_process_id",
        "inner_process_birth_utc_ticks",
        "runner_relative_path",
        "runner_sha256",
        "review_token_relative_path",
        "review_token_sha256",
        "review_token_id",
        "outer_observer_contract_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "ready_written_before_preflight_and_claim",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns",
        "created_utc",
    }
    release_fields = {
        "schema",
        "program",
        "case_id",
        "stage",
        "observer_session_relative_path",
        "observer_nonce",
        "outer_process_id",
        "outer_process_birth_utc_ticks",
        "inner_process_id",
        "inner_process_birth_utc_ticks",
        "runner_sha256",
        "review_token_sha256",
        "review_token_id",
        "outer_observer_contract_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "inner_ready_relative_path",
        "inner_ready_sha256",
        "release_scope",
        "claim_handshake_prefix_ready",
        "terminal_seal_still_pending",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns",
        "released_utc",
    }
    if set(ready) != ready_fields or set(release) != release_fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "outer observer marker field set mismatch",
        )
    outer_pid = _json_int(ready.get("outer_process_id"), "outer process id")
    outer_birth = _json_int(
        ready.get("outer_process_birth_utc_ticks"), "outer process birth"
    )
    inner_pid = _json_int(ready.get("inner_process_id"), "inner process id")
    inner_birth = _json_int(
        ready.get("inner_process_birth_utc_ticks"), "inner process birth"
    )
    ready_monotonic = _json_int(ready.get("monotonic_ns"), "outer ready monotonic")
    release_monotonic = _json_int(
        release.get("monotonic_ns"), "outer release monotonic"
    )
    ready_utc = _utc(ready.get("created_utc"), "outer ready UTC")
    release_utc = _utc(release.get("released_utc"), "outer release UTC")
    ready_fixed = {
        "schema": OUTER_INNER_READY_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "internal_mode": "primary-h4-p0r-inner-v1",
        "observer_session_relative_path": session_relative,
        "runner_relative_path": P1_RUNNER.resolve().relative_to(ROOT).as_posix(),
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "review_token_relative_path": TOKEN_PATH.resolve().relative_to(ROOT).as_posix(),
        "review_token_sha256": _sha(TOKEN_PATH),
        "review_token_id": token["review_token_id"],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": token[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "ready_written_before_preflight_and_claim": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in ready_fixed.items():
        if not _strict_json_equal(ready.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"outer inner-ready {key} mismatch"
            )
    if (
        not isinstance(ready.get("observer_nonce"), str)
        or re.fullmatch(r"[0-9a-f]{32}", ready["observer_nonce"]) is None
        or ready_path.parent.name != f"session-{ready.get('observer_nonce')}"
        or outer_pid <= 0
        or outer_birth <= 0
        or inner_pid <= 0
        or inner_birth <= 0
        or inner_birth < outer_birth
        or inner_pid == outer_pid
        or ready_monotonic < 0
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer inner-ready identity invalid"
        )
    release_fixed = {
        "schema": OUTER_START_RELEASE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "observer_session_relative_path": session_relative,
        "observer_nonce": ready["observer_nonce"],
        "outer_process_id": outer_pid,
        "outer_process_birth_utc_ticks": outer_birth,
        "inner_process_id": inner_pid,
        "inner_process_birth_utc_ticks": inner_birth,
        "runner_sha256": ready["runner_sha256"],
        "review_token_sha256": ready["review_token_sha256"],
        "review_token_id": ready["review_token_id"],
        "outer_observer_contract_sha256": ready[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": ready[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "inner_ready_relative_path": prefix["inner_ready_relative_path"],
        "inner_ready_sha256": prefix["inner_ready_sha256"],
        "release_scope": "permit_inner_preflight_and_claim_only",
        "claim_handshake_prefix_ready": True,
        "terminal_seal_still_pending": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in release_fixed.items():
        if not _strict_json_equal(release.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"outer start-release {key} mismatch"
            )
    claim_created = _utc(claim.get("created_utc"), "claim created_utc")
    if (
        release_monotonic < ready_monotonic
        or release_utc < ready_utc
        or claim_created < release_utc
        or claim.get("parent_pid") != inner_pid
        or claim.get("parent_pid_birth_utc_ticks") != inner_birth
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "outer observer marker chronology or claim identity mismatch",
        )
    return {
        "session_relative_path": session_relative,
        "observer_nonce": ready["observer_nonce"],
        "outer_process_id": outer_pid,
        "outer_process_birth_utc_ticks": outer_birth,
        "inner_process_id": inner_pid,
        "inner_process_birth_utc_ticks": inner_birth,
        "ready_utc": ready_utc,
        "release_utc": release_utc,
    }


def _historical_preclaim_manifest_payload_sha256(
    manifest: Mapping[str, object], review_token_sha256: str
) -> str:
    """Rebuild the exact manifest state observed by a successful preflight.

    Every later claim validation runs after the claim exists, and terminal
    validation also runs after the token has become a tombstone.  Hashing the
    current manifest in either state would therefore compare the preflight to
    bytes that could not have existed when it ran.
    """
    _require_sha(review_token_sha256, "historical preflight review token")
    historical = dict(manifest)
    lifecycle = dict(manifest["one_use_lifecycle"])
    token_gate_configured = _authorization_prerequisites_ready(
        manifest["execution_resource_scope"], manifest["outer_observer_contract"]
    )
    terminal = {
        "schema": OUTER_TERMINAL_CLASSIFICATION_SCHEMA,
        "classification": "authorized_token_not_terminal_evidence",
        "authoritative_terminal_evidence": False,
        "authoritative_stage_pass": False,
        "authoritative_inner_disposition": None,
        "current_token_schema": TOKEN_SCHEMA,
        "current_token_sha256": review_token_sha256,
        "terminal_seal_present": False,
        "terminal_seal_relative_path": None,
        "terminal_seal_sha256": None,
        "validation_error_code": None,
    }
    historical.update(
        {
            "status": "candidate_token_present_pending_validation_no_factor",
            "authorization_state": "not_authorized",
            "token_state": "authorized_shape_pending_validation",
            "terminal_evidence": terminal,
            "terminal_evidence_complete": False,
            "authoritative_stage_pass": False,
            "token_gated_stages": [AUTHORIZED_STAGE] if token_gate_configured else [],
            "unavailable_stages": (
                ["h4", "withheld", "EQ0"]
                if token_gate_configured
                else [AUTHORIZED_STAGE, "h4", "withheld", "EQ0"]
            ),
        }
    )
    lifecycle.update(
        {
            "token_absent": False,
            "terminal_evidence_classification": (
                "authorized_token_not_terminal_evidence"
            ),
            "authoritative_stage_pass": False,
        }
    )
    historical["one_use_lifecycle"] = lifecycle
    return _wrap(historical)["payload_sha256"]


def _validate_preflight_control_plane_evidence(
    claim: Mapping[str, object],
    manifest: Mapping[str, object],
    observer: Mapping[str, object],
) -> None:
    report_path = _control_plane_artifact_path(
        claim.get("control_plane_preflight_report_relative_path"),
        label="preflight control report",
        pattern=(
            r"validation-output/av-bs1/control-plane/"
            r"session-[0-9a-f]{32}/preflight-[0-9a-f]{32}/"
            r"control-plane-report\.json"
        ),
    )
    final_index_path = _control_plane_artifact_path(
        claim.get("control_plane_preflight_session_index_relative_path"),
        label="preflight final control index",
        pattern=(
            r"validation-output/av-bs1/control-plane/"
            r"session-[0-9a-f]{32}/session-index-0002\.json"
        ),
    )
    report_sha = _require_sha(
        claim.get("control_plane_preflight_report_sha256"),
        "preflight control report",
    )
    final_index_sha = _require_sha(
        claim.get("control_plane_preflight_session_index_sha256"),
        "preflight final control index",
    )
    if _sha(report_path) != report_sha or _sha(final_index_path) != final_index_sha:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control-plane artifact checksum mismatch",
        )

    invocation_dir = report_path.parent
    session_dir = invocation_dir.parent
    if final_index_path.parent != session_dir:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight report/final-index session mismatch",
        )
    invocation_match = re.fullmatch(r"preflight-([0-9a-f]{32})", invocation_dir.name)
    session_match = re.fullmatch(r"session-([0-9a-f]{32})", session_dir.name)
    if invocation_match is None or session_match is None:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "preflight control path parse failed"
        )
    invocation_id = invocation_match.group(1)
    session_id = session_dir.name

    report = _read(report_path, "preflight control report")
    final_index = _read(final_index_path, "preflight final control index")
    report_fields = set(
        """
        schema program case_id stage operation invocation_id report_path runner_path
        runner_sha256 python_path python_sha256 bootstrap_path bootstrap_sha256
        target_script_path target_script_sha256 target_argv target_argv_json_sha256
        process_argv process_argv_json_sha256 attempt_provenance_before
        attempt_provenance_before_sha256 attempt_provenance_after
        attempt_provenance_after_sha256 execution_resource_scope_sha256
        execution_resource_scope_binding_semantics factor_fit_interval_included
        simultaneous_current_interval_semantics summed_os_lifetime_peak_semantics
        authorization_effect previous_session_index_path previous_session_index_sha256
        monitor_kind process_membership_semantics cleanup_scope
        resource_envelope_status resource_envelope_included_work
        resource_envelope_excluded_work report_and_index_required_for_invocation_success
        gate_is_authoritative_only_in_envelope_close envelope_close_path
        envelope_close_sha256 poll_interval_ms wall_stop_seconds stream_stop_bytes_each
        thresholds pre_spawn_system pre_helper_after_provenance_tree
        pre_helper_after_provenance_system final_system
        high_system_floor_recheck_before_and_after
        high_system_floor_recheck_authoritative_in_envelope_close started_utc ended_utc
        wall_seconds wall_clock_kind process_id process_birth_utc_ticks
        process_handle_acquired execution_tree_root_pid
        execution_tree_root_birth_utc_ticks execution_tree_includes_runner
        inner_runner_pid inner_runner_birth_utc_ticks
        observed_process_ids observed_process_identities cleanup_observed_process_ids
        cleanup_observed_process_identities successful_tree_sample_count
        target_visible_tree_sample_count peak bootstrap_ready_sha256
        start_release_sha256 target_complete_sha256 target_exit_evidence_path
        target_exit_evidence_sha256 exit_release_sha256 handshake_complete
        reported_exit_code actual_exit_code allowed_exit_codes exit_code_allowed
        reported_exit_matches_actual stdout_path stdout_bytes stdout_sha256 stderr_path
        stderr_bytes stderr_sha256 monitor_ok monitor_ok_before_envelope_close
        monitor_error stop_reason cleanup_attempted cleanup_verified
        cleanup_identity_complete fallback_process_object_cleanup_attempted
        fallback_process_object_root_exit_verified observed_survivors_after_cleanup
        terminal_system_sample_after_verified_termination_or_no_spawn
        mandatory_control_plane_gate_pass
        """.split()
    )
    if set(report) != report_fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control report field set mismatch",
        )

    scope_sha = manifest["execution_resource_scope_sha256"]
    target_argv = [
        str(Path(__file__).resolve()),
        "--stage",
        "preflight-primary-h4-p0r",
        "--review-token",
        str(TOKEN_PATH.resolve()),
    ]
    envelope_close_path = invocation_dir / "control-plane-envelope-close.json"
    included_work = [
        "attempt_provenance_before",
        "helper_spawn_identity_sampling_handshake_and_cleanup",
        "stdout_stderr_marker_hashes",
        "attempt_provenance_after",
        "process_report_materialization_and_hash",
        "preclose_session_index_materialization_and_hash",
        "runner_lifetime_peak_and_system_sample_after_preclose_index",
    ]
    excluded_work = [
        "envelope_close_materialization_and_hash",
        "final_session_index_materialization_and_hash",
        "stdout_return_text_read_and_return_packaging",
        "caller_side_processing_after_return",
        "session_bootstrap_and_helper_script_materialization_before_first_invocation",
        "candidate_scope_seed_read_before_preflight_invocation",
        "runner_emergency_token_replacement_after_failed_consumer_invocation",
        "pre_exit_intent_evidence_materialization",
        "temporary_attempt_evidence_cleanup_or_quarantine",
        "unobserved_descendants_created_and_exited_between_100ms_samples",
        "external_runner_or_machine_kill",
    ]
    report_fixed = {
        "schema": CONTROL_PLANE_REPORT_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "control-plane",
        "operation": "preflight",
        "invocation_id": invocation_id,
        "report_path": str(report_path),
        "runner_path": str(P1_RUNNER.resolve()),
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "target_script_path": str(Path(__file__).resolve()),
        "target_script_sha256": manifest["bindings"]["fixture_sha256"],
        "target_argv": target_argv,
        "target_argv_json_sha256": _ordered_json_sha256(target_argv),
        "execution_resource_scope_sha256": scope_sha,
        "execution_resource_scope_binding_semantics": (
            "candidate_token_hash_untrusted_until_preflight_payload_validation"
        ),
        "factor_fit_interval_included": False,
        "simultaneous_current_interval_semantics": (
            "sampled_current_outer_observer_plus_inner_runner_plus_helper_tree_"
            "during_this_control_invocation"
        ),
        "summed_os_lifetime_peak_semantics": (
            "conservative_stop_gate_includes_outer_and_inner_work_before_this_"
            "control_invocation_not_invocation_only_peak"
        ),
        "authorization_effect": "none_control_plane_evidence_only",
        "previous_session_index_path": None,
        "previous_session_index_sha256": None,
        "monitor_kind": (
            "Win32_outer_observer_plus_inner_runner_plus_identity_bound_sampled_"
            "python_tree_Toolhelp32_Psapi_100ms_with_completion_handshake"
        ),
        "process_membership_semantics": (
            "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_identity_"
            "bound_helper_tree_descendants_between_samples_not_claimed"
        ),
        "cleanup_scope": (
            "inner_owned_identity_bound_helper_tree_or_retained_Process_object_"
            "root_fallback_outer_observer_and_inner_runner_excluded_no_unobserved_"
            "descendant_or_external_kill_guarantee"
        ),
        "resource_envelope_status": "pending_envelope_close",
        "resource_envelope_included_work": included_work,
        "resource_envelope_excluded_work": excluded_work,
        "report_and_index_required_for_invocation_success": True,
        "gate_is_authoritative_only_in_envelope_close": True,
        "envelope_close_path": str(envelope_close_path),
        "envelope_close_sha256": None,
        "poll_interval_ms": 100,
        "wall_stop_seconds": CONTROL_PLANE_WALL_STOP_SECONDS,
        "stream_stop_bytes_each": 16 * 1024**2,
        "final_system": None,
        "high_system_floor_recheck_before_and_after": False,
        "high_system_floor_recheck_authoritative_in_envelope_close": True,
        "ended_utc": None,
        "wall_seconds": None,
        "wall_clock_kind": "System.Diagnostics.Stopwatch",
        "process_handle_acquired": True,
        "execution_tree_includes_runner": True,
        "allowed_exit_codes": [0],
        "exit_code_allowed": True,
        "reported_exit_matches_actual": True,
        "handshake_complete": True,
        "monitor_ok": False,
        "monitor_ok_before_envelope_close": True,
        "monitor_error": None,
        "stop_reason": None,
        "cleanup_verified": True,
        "cleanup_identity_complete": True,
        "fallback_process_object_cleanup_attempted": False,
        "fallback_process_object_root_exit_verified": False,
        "observed_survivors_after_cleanup": [],
        "terminal_system_sample_after_verified_termination_or_no_spawn": False,
        "mandatory_control_plane_gate_pass": False,
    }
    for key, expected in report_fixed.items():
        if not _strict_json_equal(report.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"preflight control report {key} mismatch",
            )
    if type(report.get("cleanup_attempted")) is not bool:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control cleanup_attempted type mismatch",
        )

    python_path = _owned_control_file(report.get("python_path"), label="control python")
    if python_path != Path(sys.executable).resolve():
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control Python executable mismatch",
        )
    bootstrap_path = _owned_control_file(
        report.get("bootstrap_path"),
        label="control bootstrap",
        parent=session_dir,
        name="bounded-bootstrap.py",
    )
    target_path = _owned_control_file(
        report.get("target_script_path"),
        label="control target",
        parent=Path(__file__).resolve().parent,
        name=Path(__file__).name,
    )
    canonical_helper_path = _owned_control_file(
        str(session_dir / "canonical-json-sha256.py"),
        label="control canonical-hash helper",
        parent=session_dir,
        name="canonical-json-sha256.py",
    )
    ready_path = _owned_control_file(
        invocation_dir / "bootstrap-ready.json",
        label="control bootstrap-ready",
        parent=invocation_dir,
        name="bootstrap-ready.json",
    )
    start_release_path = _owned_control_file(
        invocation_dir / "start-release.json",
        label="control start-release",
        parent=invocation_dir,
        name="start-release.json",
    )
    completion_path = _owned_control_file(
        report.get("target_exit_evidence_path"),
        label="control target-complete",
        parent=invocation_dir,
        name="target-complete.json",
    )
    exit_release_path = _owned_control_file(
        invocation_dir / "exit-release.json",
        label="control exit-release",
        parent=invocation_dir,
        name="exit-release.json",
    )
    stdout_path = _owned_control_file(
        report.get("stdout_path"),
        label="control stdout",
        parent=invocation_dir,
        name="stdout.txt",
    )
    stderr_path = _owned_control_file(
        report.get("stderr_path"),
        label="control stderr",
        parent=invocation_dir,
        name="stderr.txt",
    )

    hash_bindings = (
        (python_path, report.get("python_sha256"), "control python"),
        (bootstrap_path, report.get("bootstrap_sha256"), "control bootstrap"),
        (target_path, report.get("target_script_sha256"), "control target"),
        (ready_path, report.get("bootstrap_ready_sha256"), "control ready"),
        (
            start_release_path,
            report.get("start_release_sha256"),
            "control start release",
        ),
        (
            completion_path,
            report.get("target_complete_sha256"),
            "control target complete",
        ),
        (
            completion_path,
            report.get("target_exit_evidence_sha256"),
            "control target exit evidence",
        ),
        (exit_release_path, report.get("exit_release_sha256"), "control exit release"),
        (stdout_path, report.get("stdout_sha256"), "control stdout"),
        (stderr_path, report.get("stderr_sha256"), "control stderr"),
    )
    for path, expected_sha, label in hash_bindings:
        if _sha(path) != _require_sha(expected_sha, label):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} checksum mismatch"
            )
    canonical_helper_sha256 = _sha(canonical_helper_path)
    if (
        report.get("python_sha256") != _sha(Path(sys.executable).resolve())
        or report.get("bootstrap_sha256") != CONTROL_PLANE_BOOTSTRAP_SHA256
        or canonical_helper_sha256 != CONTROL_PLANE_CANONICAL_HASH_HELPER_SHA256
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control helper executable binding mismatch",
        )
    if (
        report.get("bootstrap_sha256") != _sha(bootstrap_path)
        or report.get("runner_sha256") != _sha(P1_RUNNER)
        or report.get("target_script_sha256") != _sha(Path(__file__))
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control executable binding mismatch",
        )

    process_argv = [
        str(bootstrap_path),
        str(ready_path),
        str(start_release_path),
        str(completion_path),
        str(exit_release_path),
        *target_argv,
    ]
    if (
        not _strict_json_equal(report.get("process_argv"), process_argv)
        or report.get("process_argv_json_sha256")
        != _ordered_json_sha256(process_argv)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "preflight control argv mismatch"
        )

    expected_provenance_keys = {
        "review_token",
        "claim",
        "guard",
        "resource",
        "result",
        "canonical_input",
    }
    before = report.get("attempt_provenance_before")
    after = report.get("attempt_provenance_after")
    if (
        not isinstance(before, Mapping)
        or set(before) != expected_provenance_keys
        or not _strict_json_equal(after, before)
        or report.get("attempt_provenance_before_sha256")
        != _ordered_json_sha256(before)
        or report.get("attempt_provenance_after_sha256")
        != _ordered_json_sha256(after)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control provenance mismatch",
        )
    for role, row in before.items():
        if not isinstance(row, Mapping) or set(row) != {
            "path",
            "exists",
            "bytes",
            "sha256",
        }:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "preflight control provenance row invalid",
            )
        if role == "review_token":
            expected_token_row = {
                "path": str(TOKEN_PATH.resolve()),
                "exists": True,
                "sha256": claim["review_token_sha256"],
            }
            row_matches = all(
                _strict_json_equal(row.get(key), expected)
                for key, expected in expected_token_row.items()
            ) and type(row.get("bytes")) is int and row["bytes"] > 0
        else:
            expected_row = {
                "path": None,
                "exists": False,
                "bytes": None,
                "sha256": None,
            }
            row_matches = _strict_json_equal(row, expected_row)
        if not row_matches:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"preflight control provenance {role} mismatch",
            )

    policy = manifest["resource_policy"]
    expected_thresholds = {
        "tree_ws_stop_bytes": policy["tree_working_set_stop_bytes"],
        "tree_private_stop_bytes": policy["tree_private_stop_bytes"],
        "tree_commit_stop_bytes": policy["tree_commit_stop_bytes"],
        "commit_headroom_floor_bytes": policy["commit_headroom_floor_bytes"],
        "available_physical_floor_bytes": policy["available_physical_floor_bytes"],
        "high_pre_post_commit_headroom_floor_bytes": policy[
            "minimum_commit_headroom_before_spawn_bytes"
        ],
        "high_pre_post_available_physical_floor_bytes": policy[
            "minimum_available_physical_before_spawn_bytes"
        ],
    }
    if not _strict_json_equal(report.get("thresholds"), expected_thresholds):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control thresholds mismatch",
        )
    system_samples: dict[str, Mapping[str, object]] = {}
    for sample_key in ("pre_spawn_system", "pre_helper_after_provenance_system"):
        sample = report.get(sample_key)
        if not isinstance(sample, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"preflight control {sample_key} missing",
            )
        _validate_system_sample(sample, f"preflight control {sample_key}")
        system_samples[sample_key] = sample
        if (
            _json_int(sample.get("commit_headroom_bytes"), sample_key)
            < policy["minimum_commit_headroom_before_spawn_bytes"]
            or _json_int(sample.get("available_physical_bytes"), sample_key)
            < policy["minimum_available_physical_before_spawn_bytes"]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE",
                f"preflight control {sample_key} high floor failed",
            )
    pre_helper_tree = _validate_control_tree_sample(
        report.get("pre_helper_after_provenance_tree"),
        label="preflight control pre-helper tree",
    )
    root_pid = _json_int(
        report.get("execution_tree_root_pid"), "preflight control root pid"
    )
    root_birth = _json_int(
        report.get("execution_tree_root_birth_utc_ticks"),
        "preflight control root birth",
    )
    process_pid = _json_int(report.get("process_id"), "preflight control process id")
    process_birth = _json_int(
        report.get("process_birth_utc_ticks"), "preflight control process birth"
    )
    inner_runner_pid = _json_int(
        report.get("inner_runner_pid"), "preflight control inner runner pid"
    )
    inner_runner_birth = _json_int(
        report.get("inner_runner_birth_utc_ticks"),
        "preflight control inner runner birth",
    )
    if (
        root_pid <= 0
        or root_birth <= 0
        or root_pid != observer["outer_process_id"]
        or root_birth != observer["outer_process_birth_utc_ticks"]
        or inner_runner_pid != observer["inner_process_id"]
        or inner_runner_birth != observer["inner_process_birth_utc_ticks"]
        or inner_runner_birth < root_birth
        or process_pid <= 0
        or process_birth < inner_runner_birth
        or root_pid not in pre_helper_tree["process_ids"]
        or inner_runner_pid not in pre_helper_tree["process_ids"]
        or _json_int(
            report.get("successful_tree_sample_count"), "preflight control samples"
        )
        < 2
        or _json_int(
            report.get("target_visible_tree_sample_count"),
            "preflight control visible samples",
        )
        < 2
        or _json_int(report.get("reported_exit_code"), "reported exit") != 0
        or _json_int(report.get("actual_exit_code"), "actual exit") != 0
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control process gate failed",
        )
    _validate_control_plane_identity_list(
        report.get("observed_process_identities"),
        report.get("observed_process_ids"),
        label="preflight control observed",
    )
    _validate_control_plane_identity_list(
        report.get("cleanup_observed_process_identities"),
        report.get("cleanup_observed_process_ids"),
        label="preflight control cleanup",
    )
    observed_births = {
        row["process_id"]: row["birth_utc_ticks"]
        for row in report["observed_process_identities"]
    }
    cleanup_births = {
        row["process_id"]: row["birth_utc_ticks"]
        for row in report["cleanup_observed_process_identities"]
    }
    if (
        root_pid not in report["observed_process_ids"]
        or inner_runner_pid not in report["observed_process_ids"]
        or process_pid not in report["observed_process_ids"]
        or process_pid not in report["cleanup_observed_process_ids"]
        or observed_births.get(root_pid) != root_birth
        or observed_births.get(inner_runner_pid) != inner_runner_birth
        or observed_births.get(process_pid) != process_birth
        or cleanup_births.get(process_pid) != process_birth
        or any(
            row["process_id"] != root_pid
            and row["birth_utc_ticks"] < root_birth
            for row in report["observed_process_identities"]
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control identity coverage mismatch",
        )
    report_peak = _validate_control_peak(
        report.get("peak"), policy, label="preflight control report peak"
    )
    _require_control_peak_covers_tree_sample(
        report_peak, pre_helper_tree, label="preflight control report"
    )
    for sample_key, sample in system_samples.items():
        _require_control_peak_covers_system_sample(
            report_peak, sample, label=f"preflight control report {sample_key}"
        )
    report_started = _utc(
        report.get("started_utc"), "preflight control started_utc"
    )

    if (
        _json_int(report.get("stdout_bytes"), "preflight stdout bytes")
        != stdout_path.stat().st_size
        or _json_int(report.get("stderr_bytes"), "preflight stderr bytes")
        != stderr_path.stat().st_size
        or stdout_path.stat().st_size > 16 * 1024**2
        or stderr_path.stat().st_size > 16 * 1024**2
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "preflight control stream size mismatch"
        )
    preflight_payload, preflight_payload_sha = _read_wrapper(
        stdout_path, "preflight control stdout"
    )
    expected_preflight = {
        "schema": SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "preflight-primary-h4-p0r",
        "status": "preflight_pass_token_gated_no_factor",
        "authorization_state": "authorized",
        "preflight_pass": True,
        "git_head": claim["git_head"],
        "p0r_preregistration_commit": claim["p0r_preregistration_commit"],
        "review_token_id": claim["review_token_id"],
        "review_token_sha256": claim["review_token_sha256"],
        "review_binding_sha256": claim["review_binding_sha256"],
        "manifest_payload_sha256": _historical_preclaim_manifest_payload_sha256(
            manifest, claim["review_token_sha256"]
        ),
        "manifest_bindings": manifest["bindings"],
        "resource_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": scope_sha,
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": claim[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "factorization_performed": False,
        "physics_solve_performed": False,
        "next_stage_authorized": False,
    }
    if (
        not _strict_json_equal(preflight_payload, expected_preflight)
        or preflight_payload_sha != claim["preflight_payload_sha256"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control stdout payload mismatch",
        )

    ready = _read(ready_path, "preflight control bootstrap-ready")
    completion = _read(completion_path, "preflight control target-complete")
    if (
        set(ready) != {"schema", "process_id", "monotonic_ns"}
        or ready.get("schema")
        != "AV-BS1-h4-p0r-control-plane-bootstrap-ready-v1"
        or _json_int(ready.get("process_id"), "ready process id") != process_pid
        or _json_int(ready.get("monotonic_ns"), "ready monotonic") < 0
        or set(completion) != {"schema", "process_id", "exit_code", "monotonic_ns"}
        or completion.get("schema")
        != "AV-BS1-h4-p0r-control-plane-target-complete-v1"
        or _json_int(completion.get("process_id"), "completion process id")
        != process_pid
        or _json_int(completion.get("exit_code"), "completion exit code") != 0
        or _json_int(completion.get("monotonic_ns"), "completion monotonic")
        < _json_int(ready.get("monotonic_ns"), "ready monotonic")
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control bootstrap marker mismatch",
        )
    release_markers: dict[str, Mapping[str, object]] = {}
    for marker_path, schema in (
        (
            start_release_path,
            "AV-BS1-h4-p0r-control-plane-start-release-v1",
        ),
        (
            exit_release_path,
            "AV-BS1-h4-p0r-control-plane-exit-release-v1",
        ),
    ):
        marker = _read(marker_path, marker_path.name)
        if (
            set(marker)
            != {
                "schema",
                "invocation_id",
                "process_id",
                "sample_perf_counter_ns",
                "sample_utc",
            }
            or marker.get("schema") != schema
            or marker.get("invocation_id") != invocation_id
            or _json_int(marker.get("process_id"), "release process id") != process_pid
            or _json_int(marker.get("sample_perf_counter_ns"), "release sample") < 0
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "preflight control release marker mismatch",
            )
        _utc(marker.get("sample_utc"), "preflight control release UTC")
        release_markers[marker_path.name] = marker

    start_release = release_markers["start-release.json"]
    exit_release = release_markers["exit-release.json"]
    ready_monotonic = _json_int(ready.get("monotonic_ns"), "ready monotonic")
    start_release_monotonic = _json_int(
        start_release.get("sample_perf_counter_ns"), "start release monotonic"
    )
    completion_monotonic = _json_int(
        completion.get("monotonic_ns"), "completion monotonic"
    )
    exit_release_monotonic = _json_int(
        exit_release.get("sample_perf_counter_ns"), "exit release monotonic"
    )
    start_release_utc = _utc(
        start_release.get("sample_utc"), "preflight start release UTC"
    )
    exit_release_utc = _utc(
        exit_release.get("sample_utc"), "preflight exit release UTC"
    )
    if not (
        ready_monotonic
        <= start_release_monotonic
        <= completion_monotonic
        <= exit_release_monotonic
    ) or not (report_started <= start_release_utc <= exit_release_utc):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control marker chronology mismatch",
        )

    index_fields = {
        "schema",
        "program",
        "case_id",
        "session_id",
        "sequence",
        "index_role",
        "previous_index_path",
        "previous_index_sha256",
        "runner_sha256",
        "bootstrap_sha256",
        "canonical_hash_helper_sha256",
        "execution_resource_scope_sha256",
        "execution_resource_scope_binding_semantics",
        "report_references",
        "envelope_close_path",
        "envelope_close_sha256",
        "mandatory_control_plane_gate_pass",
        "this_index_materialization_inside_resource_envelope",
        "consume_after_every_owned_claim_outcome",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
    }
    if set(final_index) != index_fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight final control index field set mismatch",
        )
    preclose_path = _owned_control_file(
        final_index.get("previous_index_path"),
        label="preflight preclose index",
        parent=session_dir,
        name="session-index-0001.json",
    )
    preclose_sha = _require_sha(
        final_index.get("previous_index_sha256"), "preflight preclose index"
    )
    close_path = _owned_control_file(
        final_index.get("envelope_close_path"),
        label="preflight envelope close",
        parent=invocation_dir,
        name="control-plane-envelope-close.json",
    )
    close_sha = _require_sha(
        final_index.get("envelope_close_sha256"), "preflight envelope close"
    )
    if (
        close_path != envelope_close_path
        or _sha(preclose_path) != preclose_sha
        or _sha(close_path) != close_sha
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control index chain checksum mismatch",
        )
    preclose_index = _read(preclose_path, "preflight preclose control index")
    close = _read(close_path, "preflight control envelope close")
    if set(preclose_index) != index_fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight preclose control index field set mismatch",
        )

    canonical_helper_sha = _sha(canonical_helper_path)
    index_common = {
        "schema": CONTROL_PLANE_INDEX_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "session_id": session_id,
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "bootstrap_sha256": report["bootstrap_sha256"],
        "canonical_hash_helper_sha256": canonical_helper_sha,
        "execution_resource_scope_sha256": scope_sha,
        "execution_resource_scope_binding_semantics": report[
            "execution_resource_scope_binding_semantics"
        ],
        "consume_after_every_owned_claim_outcome": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for index, role, sequence, previous_path, previous_sha, close_hash, gate, inside in (
        (
            preclose_index,
            "preclose_in_resource_envelope",
            1,
            None,
            None,
            None,
            False,
            True,
        ),
        (
            final_index,
            "final_binds_resource_envelope_close",
            2,
            str(preclose_path),
            preclose_sha,
            close_sha,
            True,
            False,
        ),
    ):
        expected = {
            **index_common,
            "sequence": sequence,
            "index_role": role,
            "previous_index_path": previous_path,
            "previous_index_sha256": previous_sha,
            "envelope_close_path": str(close_path),
            "envelope_close_sha256": close_hash,
            "mandatory_control_plane_gate_pass": gate,
            "this_index_materialization_inside_resource_envelope": inside,
        }
        for key, value in expected.items():
            if not _strict_json_equal(index.get(key), value):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"preflight {role} {key} mismatch",
                )

    _validate_control_reference(
        preclose_index.get("report_references", [None])[-1],
        operation="preflight",
        invocation_id=invocation_id,
        report_path=report_path,
        report_sha256=report_sha,
        report=report,
        envelope_close_path=None,
        envelope_close_sha256=None,
        mandatory_gate=False,
    )
    _validate_control_reference(
        final_index.get("report_references", [None])[-1],
        operation="preflight",
        invocation_id=invocation_id,
        report_path=report_path,
        report_sha256=report_sha,
        report=report,
        envelope_close_path=close_path,
        envelope_close_sha256=close_sha,
        mandatory_gate=True,
    )
    if (
        not isinstance(preclose_index.get("report_references"), list)
        or len(preclose_index["report_references"]) != 1
        or not isinstance(final_index.get("report_references"), list)
        or len(final_index["report_references"]) != 1
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control report reference count mismatch",
        )

    close_fields = set(
        """
        schema program case_id stage operation invocation_id report_path report_sha256
        preclose_session_index_path preclose_session_index_sha256
        target_argv_json_sha256 process_argv_json_sha256
        attempt_provenance_before_sha256 attempt_provenance_after_sha256
        execution_resource_scope_sha256 execution_resource_scope_binding_semantics
        resource_envelope_includes_report_and_preclose_index_work
        resource_envelope_excluded_tail process_membership_semantics
        simultaneous_current_interval_semantics summed_os_lifetime_peak_semantics
        thresholds
        pre_spawn_system pre_helper_after_provenance_system
        high_system_floor_recheck_before_and_after
        terminal_system_sample_after_verified_termination_or_no_spawn
        envelope_close_runner_tree_sample final_system peak monitor_ok monitor_error
        stop_reason cleanup_verified started_utc ended_utc wall_seconds wall_clock_kind
        wall_stop_seconds mandatory_control_plane_gate_pass authorization_effect
        consume_after_every_owned_claim_outcome
        external_runner_or_machine_kill_terminal_state_guaranteed
        """.split()
    )
    if set(close) != close_fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control envelope-close field set mismatch",
        )
    close_excluded = [
        "this_envelope_close_materialization_and_hash",
        "final_session_index_materialization_and_hash",
        "stdout_return_text_read_and_return_packaging",
        "caller_side_processing_after_return",
        "session_bootstrap_and_helper_script_materialization_before_first_invocation",
        "candidate_scope_seed_read_before_preflight_invocation",
        "runner_emergency_token_replacement_after_failed_consumer_invocation",
        "pre_exit_intent_evidence_materialization",
        "temporary_attempt_evidence_cleanup_or_quarantine",
    ]
    close_fixed = {
        "schema": CONTROL_PLANE_ENVELOPE_CLOSE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "control-plane",
        "operation": "preflight",
        "invocation_id": invocation_id,
        "report_path": str(report_path),
        "report_sha256": report_sha,
        "preclose_session_index_path": str(preclose_path),
        "preclose_session_index_sha256": preclose_sha,
        "target_argv_json_sha256": report["target_argv_json_sha256"],
        "process_argv_json_sha256": report["process_argv_json_sha256"],
        "attempt_provenance_before_sha256": report[
            "attempt_provenance_before_sha256"
        ],
        "attempt_provenance_after_sha256": report[
            "attempt_provenance_after_sha256"
        ],
        "execution_resource_scope_sha256": scope_sha,
        "execution_resource_scope_binding_semantics": report[
            "execution_resource_scope_binding_semantics"
        ],
        "resource_envelope_includes_report_and_preclose_index_work": True,
        "resource_envelope_excluded_tail": close_excluded,
        "process_membership_semantics": (
            "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_helper_"
            "descendants_created_and_exited_between_samples_not_claimed"
        ),
        "simultaneous_current_interval_semantics": (
            "sampled_current_outer_observer_plus_inner_runner_plus_helper_tree_"
            "during_this_control_invocation"
        ),
        "summed_os_lifetime_peak_semantics": (
            "conservative_stop_gate_includes_outer_and_inner_work_before_this_"
            "control_invocation_not_invocation_only_peak"
        ),
        "thresholds": expected_thresholds,
        "pre_spawn_system": report["pre_spawn_system"],
        "pre_helper_after_provenance_system": report[
            "pre_helper_after_provenance_system"
        ],
        "high_system_floor_recheck_before_and_after": True,
        "terminal_system_sample_after_verified_termination_or_no_spawn": True,
        "monitor_ok": True,
        "monitor_error": None,
        "stop_reason": None,
        "cleanup_verified": True,
        "started_utc": report["started_utc"],
        "wall_clock_kind": (
            "System.Diagnostics.Stopwatch_frozen_once_before_close_materialization"
        ),
        "wall_stop_seconds": CONTROL_PLANE_WALL_STOP_SECONDS,
        "mandatory_control_plane_gate_pass": True,
        "authorization_effect": "bounded_control_plane_evidence_only",
        "consume_after_every_owned_claim_outcome": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in close_fixed.items():
        if not _strict_json_equal(close.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"preflight control envelope-close {key} mismatch",
            )
    final_system = close.get("final_system")
    if not isinstance(final_system, Mapping):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "preflight final system sample missing"
        )
    _validate_system_sample(final_system, "preflight control final system")
    if (
        _json_int(final_system.get("commit_headroom_bytes"), "final commit headroom")
        < policy["minimum_commit_headroom_before_spawn_bytes"]
        or _json_int(
            final_system.get("available_physical_bytes"), "final available physical"
        )
        < policy["minimum_available_physical_before_spawn_bytes"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "preflight control final high floor failed"
        )
    close_tree = _validate_control_tree_sample(
        close.get("envelope_close_runner_tree_sample"),
        label="preflight control envelope-close tree",
    )
    if root_pid not in close_tree["process_ids"] or process_pid in close_tree["process_ids"]:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control envelope-close tree identity mismatch",
        )
    close_peak = _validate_control_peak(
        close.get("peak"), policy, label="preflight control envelope-close peak"
    )
    _require_control_peak_covers_tree_sample(
        close_peak, close_tree, label="preflight control envelope-close"
    )
    _require_control_peak_covers_system_sample(
        close_peak, final_system, label="preflight control envelope-close"
    )
    maximum_keys = {
        "tree_working_set_bytes",
        "tree_summed_process_lifetime_peak_working_set_bytes",
        "tree_private_commit_bytes",
        "tree_committed_pagefile_bytes",
        "tree_summed_process_lifetime_peak_commit_bytes",
        "tree_nonprivate_working_set_proxy_bytes",
        "tree_page_fault_count",
        "system_commit_total_bytes",
    }
    minimum_keys = {
        "system_commit_headroom_min_bytes",
        "available_physical_min_bytes",
    }
    if any(
        _json_int(close_peak.get(key), f"close peak {key}")
        < _json_int(report_peak.get(key), f"report peak {key}")
        for key in maximum_keys
    ) or any(
        _json_int(close_peak.get(key), f"close peak {key}")
        > _json_int(report_peak.get(key), f"report peak {key}")
        for key in minimum_keys
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control peak chronology mismatch",
        )
    started = _utc(close.get("started_utc"), "preflight close started_utc")
    ended = _utc(close.get("ended_utc"), "preflight close ended_utc")
    wall = _finite_float(close.get("wall_seconds"), "preflight control wall")
    if (
        started != report_started
        or ended < started
        or exit_release_utc > ended
        or wall < 0.0
        or wall > CONTROL_PLANE_WALL_STOP_SECONDS
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "preflight control wall gate failed"
        )
    utc_elapsed = (ended - started).total_seconds()
    if abs(utc_elapsed - wall) > max(5.0, 0.05 * max(1.0, wall)):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "preflight control UTC/Stopwatch chronology mismatch",
        )

def _validate_claim(path: Path, token: Mapping[str, object], manifest: Mapping[str, object]) -> Mapping[str, object]:
    expected_relative = Path("validation-output/av-bs1/claims") / f"{token['review_token_id']}.json"
    try:
        actual_relative = path.resolve().relative_to(ROOT)
    except ValueError as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim path escaped research checkout") from exc
    if actual_relative != expected_relative or not path.is_file():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim path is not canonical")
    claim = _read(path, "claim")
    bindings = manifest["bindings"]
    fixed = {"schema": CLAIM_SCHEMA, "program": PROGRAM, "case_id": CASE_ID, "stage": "primary-h4-p0r",
             "claim_relative_path": expected_relative.as_posix(), "review_token_id": token["review_token_id"],
             "review_token_sha256": _sha(TOKEN_PATH),
             "review_token_canonical_sha256": _canonical_sha(token),
             "review_binding_sha256": _canonical_sha({"token_sha256": _sha(TOKEN_PATH), "bindings": manifest["bindings"]}),
             "fixture_sha256": bindings["fixture_sha256"],
             "runner_sha256": bindings["runner_sha256"], "manifest_payload_sha256": bindings["manifest_payload_sha256"],
             "matrix_contract_sha256": bindings["matrix_contract_sha256"],
              "resource_policy_sha256": manifest["resource_policy_sha256"],
              "execution_resource_scope_sha256": manifest["execution_resource_scope_sha256"],
              "outer_observer_contract_sha256": manifest["outer_observer_contract_sha256"],
              "expected_terminal_seal_relative_path": token[
                  "expected_terminal_seal_relative_path"
              ],
              "terminal_seal_required_for_authoritative_disposition": True,
              "p0r_preregistration_commit": token["p0r_preregistration_commit"], "git_head": _git_head()}
    required_keys = set(fixed) | {
        "guard_nonce", "parent_pid", "parent_pid_birth_utc_ticks",
        "preflight_payload_sha256", "created_utc",
        "control_plane_preflight_report_relative_path",
        "control_plane_preflight_report_sha256",
        "control_plane_preflight_session_index_relative_path",
        "control_plane_preflight_session_index_sha256",
        "outer_observer_handshake_prefix",
        "outer_observer_handshake_prefix_sha256",
    }
    if set(claim) != required_keys:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim field set mismatch")
    for key, expected in fixed.items():
        if not _strict_json_equal(claim.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"claim {key} mismatch")
    if type(claim.get("parent_pid")) is not int or claim["parent_pid"] <= 0:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim parent pid invalid")
    if type(claim.get("parent_pid_birth_utc_ticks")) is not int or claim["parent_pid_birth_utc_ticks"] <= 0:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim parent birth time invalid")
    if not isinstance(claim.get("guard_nonce"), str) or re.fullmatch(r"[0-9a-f]{32}", claim["guard_nonce"]) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim guard nonce invalid")
    _require_sha(claim.get("preflight_payload_sha256"), "claim preflight payload")
    created = _utc(claim.get("created_utc"), "claim created_utc")
    reviewed = _utc(token.get("reviewed_utc"), "token reviewed_utc")
    expires = _utc(token.get("expires_utc"), "token expires_utc")
    if created < reviewed or created >= expires:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim/token chronology invalid")
    observer = _validate_outer_handshake_prefix(claim, token, manifest)
    _validate_preflight_control_plane_evidence(claim, manifest, observer)
    return claim

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_exact_fields(
    value: object, fields: frozenset[str] | set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} field set mismatch"
        )
    return value


def _require_bool(value: object, expected: bool, label: str) -> bool:
    if type(value) is not bool or value is not expected:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} Boolean mismatch")
    return value


def _optional_sha(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_sha(value, label)


def _strict_outer_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{7}\+00:00",
        value,
    ) is None:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is not strict outer UTC text"
        )
    return _utc(value, label)


def _read_stable_json(path: Path, label: str) -> tuple[Mapping[str, object], str]:
    before = _sha(path)
    value = _read(path, label)
    after = _sha(path)
    if before != after:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} changed during read"
        )
    return value, before


def _terminal_file(
    relative_value: object, *, label: str, pattern: str
) -> Path:
    return _control_plane_artifact_path(
        relative_value, label=label, pattern=pattern
    )


def _terminal_directory(
    relative_value: object, *, label: str, pattern: str
) -> Path:
    if (
        not isinstance(relative_value, str)
        or "\\" in relative_value
        or re.fullmatch(pattern, relative_value) is None
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} relative path invalid"
        )
    candidate = (ROOT / relative_value).resolve()
    try:
        actual = candidate.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} escaped checkout"
        ) from exc
    if actual != relative_value or not candidate.is_dir():
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} path is not canonical"
        )
    return candidate


def _validate_terminal_handshake(
    claim: Mapping[str, object],
    tombstone: Mapping[str, object],
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    prefix = _require_exact_fields(
        claim.get("outer_observer_handshake_prefix"),
        {
            "schema",
            "inner_ready_relative_path",
            "inner_ready_sha256",
            "outer_start_release_relative_path",
            "outer_start_release_sha256",
        },
        "terminal outer handshake prefix",
    )
    prefix_sha = _require_sha(
        claim.get("outer_observer_handshake_prefix_sha256"),
        "terminal outer handshake prefix",
    )
    if (
        prefix.get("schema") != OUTER_HANDSHAKE_PREFIX_SCHEMA
        or _canonical_sha(prefix) != prefix_sha
        or tombstone.get("outer_observer_handshake_prefix_sha256") != prefix_sha
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal outer handshake prefix mismatch"
        )
    ready_path = _terminal_file(
        prefix.get("inner_ready_relative_path"),
        label="terminal inner-ready",
        pattern=(
            r"validation-output/av-bs1/outer-observer/"
            r"session-[0-9a-f]{32}/inner-ready\.json"
        ),
    )
    start_path = _terminal_file(
        prefix.get("outer_start_release_relative_path"),
        label="terminal outer-start-release",
        pattern=(
            r"validation-output/av-bs1/outer-observer/"
            r"session-[0-9a-f]{32}/outer-start-release\.json"
        ),
    )
    if ready_path.parent != start_path.parent:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal handshake session mismatch"
        )
    ready, ready_sha = _read_stable_json(ready_path, "terminal inner-ready")
    start, start_sha = _read_stable_json(start_path, "terminal outer-start-release")
    if (
        ready_sha != _require_sha(prefix.get("inner_ready_sha256"), "inner-ready")
        or start_sha
        != _require_sha(prefix.get("outer_start_release_sha256"), "start-release")
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal handshake byte hash mismatch"
        )
    ready_fields = {
        "schema", "program", "case_id", "stage", "internal_mode",
        "observer_session_relative_path", "observer_nonce", "outer_process_id",
        "outer_process_birth_utc_ticks", "inner_process_id",
        "inner_process_birth_utc_ticks", "runner_relative_path", "runner_sha256",
        "review_token_relative_path", "review_token_sha256", "review_token_id",
        "outer_observer_contract_sha256", "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "ready_written_before_preflight_and_claim",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns", "created_utc",
    }
    start_fields = {
        "schema", "program", "case_id", "stage",
        "observer_session_relative_path", "observer_nonce", "outer_process_id",
        "outer_process_birth_utc_ticks", "inner_process_id",
        "inner_process_birth_utc_ticks", "runner_sha256", "review_token_sha256",
        "review_token_id", "outer_observer_contract_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "inner_ready_relative_path", "inner_ready_sha256", "release_scope",
        "claim_handshake_prefix_ready", "terminal_seal_still_pending",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns", "released_utc",
    }
    _require_exact_fields(ready, ready_fields, "terminal inner-ready")
    _require_exact_fields(start, start_fields, "terminal outer-start-release")
    session_relative = ready_path.parent.relative_to(ROOT).as_posix()
    nonce = ready.get("observer_nonce")
    if (
        not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
        or ready_path.parent.name != f"session-{nonce}"
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal observer nonce/session mismatch"
        )
    outer_pid = _json_int(ready.get("outer_process_id"), "terminal outer pid")
    outer_birth = _json_int(
        ready.get("outer_process_birth_utc_ticks"), "terminal outer birth"
    )
    inner_pid = _json_int(ready.get("inner_process_id"), "terminal inner pid")
    inner_birth = _json_int(
        ready.get("inner_process_birth_utc_ticks"), "terminal inner birth"
    )
    ready_ns = _json_int(ready.get("monotonic_ns"), "terminal ready monotonic")
    start_ns = _json_int(start.get("monotonic_ns"), "terminal start monotonic")
    ready_utc = _strict_outer_utc(ready.get("created_utc"), "terminal ready UTC")
    start_utc = _strict_outer_utc(start.get("released_utc"), "terminal start UTC")
    expected_seal = tombstone.get("expected_terminal_seal_relative_path")
    ready_fixed = {
        "schema": OUTER_INNER_READY_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "internal_mode": "primary-h4-p0r-inner-v1",
        "observer_session_relative_path": session_relative,
        "runner_relative_path": P1_RUNNER.resolve().relative_to(ROOT).as_posix(),
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "review_token_relative_path": TOKEN_PATH.resolve().relative_to(ROOT).as_posix(),
        "review_token_sha256": tombstone["consumed_review_token_sha256"],
        "review_token_id": tombstone["consumed_review_token_id"],
        "outer_observer_contract_sha256": manifest["outer_observer_contract_sha256"],
        "expected_terminal_seal_relative_path": expected_seal,
        "terminal_seal_required_for_authoritative_disposition": True,
        "ready_written_before_preflight_and_claim": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in ready_fixed.items():
        if not _strict_json_equal(ready.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal inner-ready {key} mismatch"
            )
    if (
        outer_pid <= 0 or outer_birth <= 0 or inner_pid <= 0 or inner_birth <= 0
        or outer_pid == inner_pid or inner_birth < outer_birth or ready_ns < 0
        or start_ns <= ready_ns or start_utc < ready_utc
        or claim.get("parent_pid") != inner_pid
        or claim.get("parent_pid_birth_utc_ticks") != inner_birth
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal handshake identity/chronology invalid"
        )
    start_fixed = {
        "schema": OUTER_START_RELEASE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "observer_session_relative_path": session_relative,
        "observer_nonce": nonce,
        "outer_process_id": outer_pid,
        "outer_process_birth_utc_ticks": outer_birth,
        "inner_process_id": inner_pid,
        "inner_process_birth_utc_ticks": inner_birth,
        "runner_sha256": ready["runner_sha256"],
        "review_token_sha256": ready["review_token_sha256"],
        "review_token_id": ready["review_token_id"],
        "outer_observer_contract_sha256": ready["outer_observer_contract_sha256"],
        "expected_terminal_seal_relative_path": expected_seal,
        "terminal_seal_required_for_authoritative_disposition": True,
        "inner_ready_relative_path": prefix["inner_ready_relative_path"],
        "inner_ready_sha256": ready_sha,
        "release_scope": "permit_inner_preflight_and_claim_only",
        "claim_handshake_prefix_ready": True,
        "terminal_seal_still_pending": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in start_fixed.items():
        if not _strict_json_equal(start.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"terminal outer-start-release {key} mismatch",
            )
    claim_utc = _strict_outer_utc(claim.get("created_utc"), "terminal claim UTC")
    if claim_utc < start_utc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal claim predates start release"
        )
    return {
        "prefix": prefix,
        "prefix_sha256": prefix_sha,
        "session_path": ready_path.parent,
        "session_relative_path": session_relative,
        "nonce": nonce,
        "outer_process_id": outer_pid,
        "outer_process_birth_utc_ticks": outer_birth,
        "inner_process_id": inner_pid,
        "inner_process_birth_utc_ticks": inner_birth,
        "ready_path": ready_path,
        "ready": ready,
        "ready_sha256": ready_sha,
        "ready_monotonic_ns": ready_ns,
        "ready_utc": ready_utc,
        "start_path": start_path,
        "start": start,
        "start_sha256": start_sha,
        "start_monotonic_ns": start_ns,
        "start_utc": start_utc,
    }


def _validate_terminal_claim(
    tombstone: Mapping[str, object], manifest: Mapping[str, object]
) -> Mapping[str, object]:
    identifier = tombstone["consumed_review_token_id"]
    expected_relative = f"validation-output/av-bs1/claims/{identifier}.json"
    if tombstone.get("claim_relative_path") != expected_relative:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal claim relative path mismatch"
        )
    path = _terminal_file(
        expected_relative,
        label="terminal claim",
        pattern=r"validation-output/av-bs1/claims/[0-9a-f]{32}\.json",
    )
    claim, claim_sha = _read_stable_json(path, "terminal claim")
    claim_fields = {
        "schema", "program", "case_id", "stage", "claim_relative_path",
        "review_token_id", "review_token_sha256",
        "review_token_canonical_sha256", "review_binding_sha256",
        "fixture_sha256", "runner_sha256", "p0r_preregistration_commit",
        "git_head", "preflight_payload_sha256", "manifest_payload_sha256",
        "matrix_contract_sha256", "resource_policy_sha256",
        "execution_resource_scope_sha256",
        "control_plane_preflight_report_relative_path",
        "control_plane_preflight_report_sha256",
        "control_plane_preflight_session_index_relative_path",
        "control_plane_preflight_session_index_sha256",
        "outer_observer_contract_sha256", "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "outer_observer_handshake_prefix",
        "outer_observer_handshake_prefix_sha256", "guard_nonce", "parent_pid",
        "parent_pid_birth_utc_ticks", "created_utc",
    }
    _require_exact_fields(claim, claim_fields, "terminal claim")
    expected = {
        "schema": CLAIM_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "claim_relative_path": expected_relative,
        "review_token_id": identifier,
        "review_token_sha256": tombstone["consumed_review_token_sha256"],
        "review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "review_binding_sha256": tombstone["review_binding_sha256"],
        "fixture_sha256": manifest["bindings"]["fixture_sha256"],
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "p0r_preregistration_commit": tombstone["p0r_preregistration_commit"],
        "git_head": tombstone["consumed_git_head"],
        "manifest_payload_sha256": manifest["bindings"]["manifest_payload_sha256"],
        "matrix_contract_sha256": manifest["bindings"]["matrix_contract_sha256"],
        "resource_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": tombstone[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
    }
    for key, value in expected.items():
        if not _strict_json_equal(claim.get(key), value):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal claim {key} mismatch"
            )
    if claim_sha != _require_sha(tombstone.get("claim_sha256"), "terminal claim"):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "terminal claim hash mismatch")
    claim_canonical = _canonical_sha(claim)
    if tombstone.get("schema") == TOMBSTONE_SCHEMA:
        if claim_canonical != _require_sha(
            tombstone.get("claim_canonical_sha256"), "terminal canonical claim"
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal canonical claim hash mismatch"
            )
    elif (
        tombstone.get("schema") != EMERGENCY_TOMBSTONE_SCHEMA
        or tombstone.get("claim_canonical_sha256") is not None
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "terminal emergency claim canonical evidence must remain unasserted",
        )
    if (
        not isinstance(claim.get("guard_nonce"), str)
        or re.fullmatch(r"[0-9a-f]{32}", claim["guard_nonce"]) is None
        or _json_int(claim.get("parent_pid"), "terminal claim parent pid") <= 0
        or _json_int(
            claim.get("parent_pid_birth_utc_ticks"), "terminal claim parent birth"
        ) <= 0
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "terminal claim identity invalid")
    _require_sha(claim.get("preflight_payload_sha256"), "terminal preflight payload")
    for key, pattern in (
        (
            "control_plane_preflight_report_relative_path",
            r"validation-output/av-bs1/control-plane/session-[0-9a-f]{32}/"
            r"preflight-[0-9a-f]{32}/control-plane-report\.json",
        ),
        (
            "control_plane_preflight_session_index_relative_path",
            r"validation-output/av-bs1/control-plane/session-[0-9a-f]{32}/"
            r"session-index-[0-9]{4}\.json",
        ),
    ):
        evidence_path = _terminal_file(
            claim.get(key), label=f"terminal claim {key}", pattern=pattern
        )
        hash_key = key.replace("_relative_path", "_sha256")
        if _sha(evidence_path) != _require_sha(claim.get(hash_key), hash_key):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal claim {key} hash mismatch"
            )
    observer = _validate_terminal_handshake(claim, tombstone, manifest)
    _validate_preflight_control_plane_evidence(claim, manifest, observer)
    return {
        "path": path,
        "relative_path": expected_relative,
        "value": claim,
        "sha256": claim_sha,
        "canonical_sha256": claim_canonical,
        "observer": observer,
    }


def _validate_consumed_tombstone(
    tombstone: Mapping[str, object],
    tombstone_sha256: str,
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    schema = tombstone.get("schema")
    if schema == TOMBSTONE_SCHEMA:
        fields = NORMAL_TOMBSTONE_FIELDS
        emergency = False
    elif schema == EMERGENCY_TOMBSTONE_SCHEMA:
        fields = EMERGENCY_TOMBSTONE_FIELDS
        emergency = True
    else:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone schema is not v2"
        )
    _require_exact_fields(tombstone, fields, "consumed tombstone")
    identifier = tombstone.get("consumed_review_token_id")
    if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone review token id invalid"
        )
    original_sha = _require_sha(
        tombstone.get("consumed_review_token_sha256"), "consumed review token"
    )
    original_canonical_sha = _require_sha(
        tombstone.get("consumed_review_token_canonical_sha256"),
        "canonical consumed review token",
    )
    expected_seal = (
        f"validation-output/av-bs1/outer-observer-terminal-seals/{identifier}.json"
    )
    fixed = {
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorized_stage": AUTHORIZED_STAGE,
        "authorization_state": "consumed",
        "uses_remaining": 0,
        "next_stage_authorized": False,
        "bindings": manifest["bindings"],
        "resource_guard_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": expected_seal,
        "terminal_seal_required_for_authoritative_disposition": True,
        "terminal_seal_state": "pending_outer_observed_inner_exit",
        "terminal_evidence_complete": False,
        "authoritative_stage_pass": False,
        "physics_solve_performed": False,
    }
    for key, expected in fixed.items():
        if not _strict_json_equal(tombstone.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"consumed tombstone {key} mismatch"
            )
    if (
        not isinstance(tombstone.get("p0r_preregistration_commit"), str)
        or re.fullmatch(r"[0-9a-f]{40}", tombstone["p0r_preregistration_commit"])
        is None
        or not isinstance(tombstone.get("consumed_git_head"), str)
        or re.fullmatch(r"[0-9a-f]{40}", tombstone["consumed_git_head"]) is None
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone commit binding invalid"
        )
    if tombstone.get("review_binding_sha256") != _canonical_sha(
        {"token_sha256": original_sha, "bindings": manifest["bindings"]}
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone review binding mismatch"
        )
    _utc(tombstone.get("consumed_utc"), "consumed tombstone UTC")
    for key in (
        "claim_evidence_valid", "guard_evidence_valid", "result_evidence_valid",
        "resource_evidence_valid", "child_stdout_evidence_valid",
        "factor_prefix_evidence_valid", "resource_gate_pass",
        "consumption_validated_pass", "mandatory_stage_pass",
    ):
        if type(tombstone.get(key)) is not bool:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"consumed tombstone {key} type invalid"
            )
    for key in (
        "resource_gate_recheck_failures", "evidence_validation_errors",
        "failure_codes", "completed_factors", "factor_order", "factor_certificates",
    ):
        if not isinstance(tombstone.get(key), list):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"consumed tombstone {key} type invalid"
            )
    failure_codes = tombstone["failure_codes"]
    if (
        any(type(code) is not str or code not in FAILURE_CODES for code in failure_codes)
        or failure_codes != list(dict.fromkeys(failure_codes))
        or any(type(item) is not str for item in tombstone["resource_gate_recheck_failures"])
        or any(type(item) is not str for item in tombstone["evidence_validation_errors"])
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone failure evidence invalid"
        )
    approved_factors = ["A_background_II", "A_conductor_II"]
    attempted = tombstone.get("factorization_attempted")
    performed = tombstone.get("factorization_performed")
    child_launched = tombstone.get("child_launched")
    child_pid = tombstone.get("child_process_id")
    child_exit = tombstone.get("child_exit_code")
    if (
        attempted is not None and type(attempted) is not bool
        or performed is not None and type(performed) is not bool
        or child_launched is not None and type(child_launched) is not bool
        or performed is True and attempted is not True
        or child_pid is not None and (type(child_pid) is not int or child_pid <= 0)
        or child_exit is not None and (type(child_exit) is not int or child_exit not in (0, 2))
        or child_launched is False and (child_pid is not None or child_exit is not None)
        or child_launched is True and child_pid is None
        or tombstone.get("active_factor") not in (None, *approved_factors)
        or tombstone["completed_factors"]
        != approved_factors[: len(tombstone["completed_factors"])]
        or tombstone["factor_order"]
        != approved_factors[: len(tombstone["factor_order"])]
        or len(tombstone["factor_certificates"]) > 2
        or any(not isinstance(item, Mapping) for item in tombstone["factor_certificates"])
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone factor/child state invalid"
        )
    _require_sha(tombstone.get("preflight_payload_sha256"), "tombstone preflight")
    claim_context = _validate_terminal_claim(tombstone, manifest)
    claim = claim_context["value"]
    if tombstone.get("preflight_payload_sha256") != claim["preflight_payload_sha256"]:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "tombstone/claim preflight binding mismatch"
        )
    if tombstone.get("outer_observer_handshake_prefix_sha256") != claim[
        "outer_observer_handshake_prefix_sha256"
    ]:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "tombstone/claim observer prefix mismatch"
        )
    outer_emergency = False
    if emergency:
        emergency_fixed = {
            "emergency_consumption": True,
            "replaced_token_file_sha256": None,
            "claim_evidence_valid": False,
            "claim_canonical_sha256": None,
            "claim_evidence": None,
            "guard_evidence_valid": False,
            "guard_canonical_sha256": None,
            "guard_evidence": None,
            "result_evidence_valid": False,
            "result_evidence": None,
            "resource_evidence_valid": False,
            "resource_evidence": None,
            "child_stdout_evidence_valid": False,
            "child_stdout_evidence": None,
            "factor_prefix_evidence_valid": False,
            "factor_prefix_evidence": None,
            "consumed_result_payload_sha256": None,
            "consumed_child_payload_sha256": None,
            "resource_gate_pass": False,
            "consumption_validated_pass": False,
            "mandatory_stage_pass": False,
            "active_factor": None,
        }
        for key, expected in emergency_fixed.items():
            if not _strict_json_equal(tombstone.get(key), expected):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"emergency consumed tombstone {key} mismatch",
                )
        if type(tombstone.get("consumer_control_gate_pass")) is not bool:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "emergency consumer gate type invalid",
            )
        disposition = tombstone.get("review_disposition")
        outer_emergency = (
            disposition
            == "emergency_consumed_after_outer_observer_post_claim_failure"
        )
        inner_emergency = disposition == "emergency_consumed_after_control_plane_failure"
        if outer_emergency is inner_emergency:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "emergency tombstone disposition is not an exact supported branch",
            )
        if (
            not isinstance(tombstone.get("consumer_failure_message"), str)
            or not tombstone["consumer_failure_message"]
            or len(tombstone["consumer_failure_message"]) > 4096
            or tombstone.get("runner_sha256")
            != manifest["bindings"]["runner_sha256"]
            or not tombstone["failure_codes"]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency tombstone failure evidence invalid"
            )
        if outer_emergency:
            outer_fixed = {
                "consumer_control_gate_pass": False,
                "guard_contract_sha256": None,
                "attempt_status": "outer_observer_post_claim_failure",
                "effective_attempt_status": "outer_observer_post_claim_failure",
                "child_launched": None,
                "child_process_id": None,
                "child_exit_code": None,
                "consumed_result_file_sha256": None,
                "consumed_result_payload_sha256": None,
                "consumed_resource_report_sha256": None,
                "consumed_child_stdout_file_sha256": None,
                "consumed_child_payload_sha256": None,
                "resource_gate_recheck_failures": [
                    "outer_observer_post_claim_failure"
                ],
                "evidence_validation_errors": [
                    tombstone["consumer_failure_message"]
                ],
                "factorization_attempted": None,
                "factorization_performed": None,
                "completed_factors": [],
                "factor_order": [],
                "factor_certificates": [],
            }
            for key, expected in outer_fixed.items():
                if not _strict_json_equal(tombstone.get(key), expected):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"outer emergency tombstone {key} mismatch",
                    )
            if tombstone["failure_codes"] not in (
                ["BLOCKED_AV_BS_RESOURCE"],
                ["BLOCKED_AV_BS_RESULT_SCHEMA"],
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "outer emergency tombstone failure code invalid",
                )
        else:
            allowed_attempts = {
                "completed_pass",
                "completed_failure",
                "resource_stop",
                "finalizer_failure",
                "runner_exception",
                "spawn_failure",
            }
            expected_code = (
                "BLOCKED_AV_BS_RESULT_SCHEMA"
                if tombstone["consumer_control_gate_pass"]
                else "BLOCKED_AV_BS_RESOURCE"
            )
            if (
                tombstone.get("effective_attempt_status")
                != "control_plane_consumer_failure"
                or tombstone.get("attempt_status") not in allowed_attempts
                or tombstone.get("resource_gate_recheck_failures")
                != ["emergency_consumer_control_plane_failure"]
                or tombstone.get("evidence_validation_errors")
                != [tombstone["consumer_failure_message"]]
                or tombstone.get("failure_codes") != [expected_code]
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "inner emergency tombstone disposition invalid",
                )
            launched = tombstone.get("child_launched")
            expected_phase = None if launched is True else False
            if (
                type(launched) is not bool
                or tombstone.get("child_exit_code") is not None
                or tombstone.get("factorization_attempted") is not expected_phase
                or tombstone.get("factorization_performed") is not expected_phase
                or tombstone.get("completed_factors") != []
                or tombstone.get("factor_order") != []
                or tombstone.get("factor_certificates") != []
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "inner emergency tombstone factor phase invalid",
                )
        provisional_pass = False
    else:
        if tombstone.get("review_disposition") != "consumed_after_primary_h4_p0r_claim":
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "normal tombstone disposition mismatch"
            )
        _require_bool(
            tombstone.get("claim_evidence_valid"), True, "normal tombstone claim evidence"
        )
        if not _strict_json_equal(tombstone.get("claim_evidence"), claim):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "normal tombstone claim evidence mismatch"
            )
        provisional_pass = bool(
            tombstone.get("consumption_validated_pass") is True
            and tombstone.get("mandatory_stage_pass") is True
            and tombstone.get("attempt_status") == "completed_pass"
            and tombstone.get("effective_attempt_status") == "completed_pass"
            and not tombstone["failure_codes"]
        )
        if provisional_pass:
            required_true = (
                "guard_evidence_valid", "result_evidence_valid",
                "resource_evidence_valid", "child_stdout_evidence_valid",
                "factor_prefix_evidence_valid", "resource_gate_pass",
                "factorization_attempted", "factorization_performed",
            )
            for key in required_true:
                _require_bool(tombstone.get(key), True, f"normal pass {key}")
            if (
                tombstone["resource_gate_recheck_failures"]
                or tombstone["evidence_validation_errors"]
                or tombstone.get("active_factor") is not None
                or tombstone.get("completed_factors")
                != ["A_background_II", "A_conductor_II"]
                or tombstone.get("factor_order")
                != ["A_background_II", "A_conductor_II"]
                or len(tombstone["factor_certificates"]) != 2
                or not isinstance(tombstone.get("result_evidence"), Mapping)
                or not isinstance(tombstone.get("resource_evidence"), Mapping)
                or not isinstance(tombstone.get("child_stdout_evidence"), Mapping)
                or not isinstance(tombstone.get("factor_prefix_evidence"), list)
                or len(tombstone["factor_prefix_evidence"]) != 2
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "normal pass tombstone evidence incomplete"
                )
            for key in (
                "consumed_result_file_sha256", "consumed_result_payload_sha256",
                "consumed_resource_report_sha256",
                "consumed_child_stdout_file_sha256",
                "consumed_child_payload_sha256",
            ):
                _require_sha(tombstone.get(key), f"normal pass {key}")
        elif (
            tombstone.get("consumption_validated_pass") is not False
            or tombstone.get("mandatory_stage_pass") is not False
            or not tombstone["failure_codes"]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "normal failure tombstone inconsistent"
            )
    return {
        "value": tombstone,
        "sha256": tombstone_sha256,
        "schema": schema,
        "emergency": emergency,
        "outer_emergency": outer_emergency,
        "provisional_pass": provisional_pass,
        "claim": claim_context,
        "observer": claim_context["observer"],
    }


def _validate_emergency_replacement_state(
    tombstone_context: Mapping[str, object],
    *,
    require_postvalidated: bool,
) -> Mapping[str, object]:
    if tombstone_context.get("emergency") is not True:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency replacement validator got normal tombstone"
        )
    tombstone = tombstone_context["value"]
    claim_context = tombstone_context["claim"]
    claim = claim_context["value"]
    identifier = tombstone["consumed_review_token_id"]
    nonce = (
        tombstone_context["observer"]["nonce"]
        if tombstone_context.get("outer_emergency") is True
        else claim["guard_nonce"]
    )
    journal_identity = f"{identifier}-{nonce}"
    journal_root = ROOT / "validation-output" / "av-bs1" / "emergency-replacement-journals"
    intent_path = journal_root / f"{journal_identity}.intent.json"
    postvalidation_path = journal_root / f"{journal_identity}.postvalidation.json"
    recovery_path = journal_root / f"{journal_identity}.authorized-token-backup.bin"
    if not intent_path.is_file():
        if require_postvalidated:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency replacement intent is missing"
            )
        return {
            "state": "replacement_unverified_missing_intent",
            "postvalidated": False,
            "intent_path": None,
            "intent_sha256": None,
            "postvalidation_path": None,
            "postvalidation_sha256": None,
            "recovery_path": None,
            "recovery_sha256": None,
        }
    intent, intent_sha = _read_stable_json(intent_path, "emergency replacement intent")
    intent_fields = {
        "schema", "program", "case_id", "authorized_stage", "journal_identity",
        "review_token_id", "original_review_token_sha256",
        "original_review_token_canonical_sha256", "claim_relative_path",
        "claim_sha256", "guard_sha256", "attempt_status", "target_relative_path",
        "file_replace_backup_relative_path", "expected_original_review_token_sha256",
        "expected_original_review_token_canonical_sha256", "pre_replace_observed_sha256",
        "pre_replace_observed_exact_retained_bytes_match", "replacement_state",
        "file_replace_is_atomic_compare_and_swap",
        "prior_bytes_verified_from_file_replace_backup", "replacement_authority",
        "authorization_blocker", "terminal_evidence_complete",
        "external_runner_or_machine_kill_terminal_state_guaranteed", "created_utc",
    }
    _require_exact_fields(intent, intent_fields, "emergency replacement intent")
    target_relative = TOKEN_PATH.resolve().relative_to(ROOT).as_posix()
    token_parent = TOKEN_PATH.parent.resolve().relative_to(ROOT).as_posix()
    backup_pattern = (
        rf"{re.escape(token_parent)}/\.{re.escape(TOKEN_PATH.name)}\.replaced\."
        r"[0-9a-f]{32}\.bak"
    )
    intent_fixed = {
        "schema": EMERGENCY_REPLACEMENT_INTENT_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorized_stage": AUTHORIZED_STAGE,
        "journal_identity": journal_identity,
        "review_token_id": identifier,
        "original_review_token_sha256": tombstone["consumed_review_token_sha256"],
        "original_review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "claim_relative_path": claim_context["relative_path"],
        "claim_sha256": claim_context["sha256"],
        "guard_sha256": tombstone.get("guard_contract_sha256"),
        "attempt_status": tombstone["attempt_status"],
        "target_relative_path": target_relative,
        "expected_original_review_token_sha256": tombstone[
            "consumed_review_token_sha256"
        ],
        "expected_original_review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "pre_replace_observed_sha256": tombstone["consumed_review_token_sha256"],
        "pre_replace_observed_exact_retained_bytes_match": True,
        "replacement_state": "pending_file_replace_postvalidation",
        "file_replace_is_atomic_compare_and_swap": False,
        "prior_bytes_verified_from_file_replace_backup": False,
        "replacement_authority": "none_pending_postvalidation",
        "authorization_blocker": True,
        "terminal_evidence_complete": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in intent_fixed.items():
        if not _strict_json_equal(intent.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"emergency intent {key} mismatch"
            )
    if (
        not isinstance(intent.get("file_replace_backup_relative_path"), str)
        or re.fullmatch(backup_pattern, intent["file_replace_backup_relative_path"])
        is None
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency intent backup path invalid"
        )
    intent_utc = _utc(intent.get("created_utc"), "emergency intent created UTC")
    if not postvalidation_path.is_file():
        if require_postvalidated:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency replacement postvalidation missing"
            )
        return {
            "state": "replacement_pending_postvalidation",
            "postvalidated": False,
            "intent_path": intent_path,
            "intent_sha256": intent_sha,
            "postvalidation_path": None,
            "postvalidation_sha256": None,
            "recovery_path": recovery_path if recovery_path.is_file() else None,
            "recovery_sha256": _sha(recovery_path) if recovery_path.is_file() else None,
        }
    postvalidation, postvalidation_sha = _read_stable_json(
        postvalidation_path, "emergency replacement postvalidation"
    )
    postvalidation_fields = {
        "schema", "program", "case_id", "authorized_stage", "journal_identity",
        "review_token_id", "original_review_token_sha256",
        "original_review_token_canonical_sha256", "claim_relative_path",
        "claim_sha256", "guard_sha256", "attempt_status", "intent_relative_path",
        "intent_sha256", "recovery_relative_path", "recovery_sha256",
        "emergency_tombstone_relative_path", "emergency_tombstone_sha256",
        "emergency_tombstone_schema", "file_replace_is_atomic_compare_and_swap",
        "prior_bytes_verified_from_file_replace_backup",
        "backup_exact_retained_original_bytes_match", "replacement_state",
        "replacement_authority", "authorization_blocker", "lifecycle_completion_claimed",
        "terminal_evidence_complete", "external_observer_required_for_terminal_exit",
        "external_runner_or_machine_kill_terminal_state_guaranteed", "recorded_utc",
    }
    _require_exact_fields(
        postvalidation, postvalidation_fields, "emergency replacement postvalidation"
    )
    intent_relative = intent_path.resolve().relative_to(ROOT).as_posix()
    recovery_relative = recovery_path.resolve().relative_to(ROOT).as_posix()
    postvalidation_fixed = {
        "schema": EMERGENCY_REPLACEMENT_POSTVALIDATION_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorized_stage": AUTHORIZED_STAGE,
        "journal_identity": journal_identity,
        "review_token_id": identifier,
        "original_review_token_sha256": tombstone["consumed_review_token_sha256"],
        "original_review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "claim_relative_path": claim_context["relative_path"],
        "claim_sha256": claim_context["sha256"],
        "guard_sha256": tombstone.get("guard_contract_sha256"),
        "attempt_status": tombstone["attempt_status"],
        "intent_relative_path": intent_relative,
        "intent_sha256": intent_sha,
        "recovery_relative_path": recovery_relative,
        "emergency_tombstone_relative_path": target_relative,
        "emergency_tombstone_sha256": tombstone_context["sha256"],
        "emergency_tombstone_schema": EMERGENCY_TOMBSTONE_SCHEMA,
        "file_replace_is_atomic_compare_and_swap": False,
        "prior_bytes_verified_from_file_replace_backup": True,
        "backup_exact_retained_original_bytes_match": True,
        "replacement_state": "replacement_postvalidated_pending_external_observer",
        "replacement_authority": (
            "postvalidated_file_replacement_only_not_terminal_lifecycle_evidence"
        ),
        "authorization_blocker": True,
        "lifecycle_completion_claimed": False,
        "terminal_evidence_complete": False,
        "external_observer_required_for_terminal_exit": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in postvalidation_fixed.items():
        if not _strict_json_equal(postvalidation.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"emergency postvalidation {key} mismatch"
            )
    if not recovery_path.is_file():
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency replacement recovery bytes missing"
        )
    recovery_bytes = recovery_path.read_bytes()
    recovery_sha = hashlib.sha256(recovery_bytes).hexdigest()
    if (
        recovery_sha != tombstone["consumed_review_token_sha256"]
        or postvalidation.get("recovery_sha256") != recovery_sha
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency replacement recovery hash mismatch"
        )
    try:
        recovered_token = json.loads(recovery_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency recovery is not original token JSON"
        ) from exc
    if (
        not isinstance(recovered_token, Mapping)
        or recovered_token.get("schema") != TOKEN_SCHEMA
        or recovered_token.get("review_token_id") != identifier
        or recovered_token.get("authorization_state") != "authorized"
        or type(recovered_token.get("uses_remaining")) is not int
        or recovered_token.get("uses_remaining") != 1
        or _canonical_sha(recovered_token)
        != tombstone["consumed_review_token_canonical_sha256"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency recovery token binding mismatch"
        )
    recorded_utc = _utc(
        postvalidation.get("recorded_utc"), "emergency postvalidation recorded UTC"
    )
    if recorded_utc < intent_utc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency journal chronology invalid"
        )
    if (
        _sha(intent_path) != intent_sha
        or _sha(postvalidation_path) != postvalidation_sha
        or _sha(recovery_path) != recovery_sha
        or _sha(TOKEN_PATH) != tombstone_context["sha256"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "emergency journal changed during validation"
        )
    return {
        "state": "replacement_postvalidated_pending_external_observer",
        "postvalidated": True,
        "intent_path": intent_path,
        "intent_sha256": intent_sha,
        "postvalidation_path": postvalidation_path,
        "postvalidation_sha256": postvalidation_sha,
        "recovery_path": recovery_path,
        "recovery_sha256": recovery_sha,
    }


def _validate_control_peak_shape(
    value: object, *, label: str
) -> Mapping[str, int]:
    fields = {
        "tree_working_set_bytes",
        "tree_summed_process_lifetime_peak_working_set_bytes",
        "tree_private_commit_bytes",
        "tree_committed_pagefile_bytes",
        "tree_summed_process_lifetime_peak_commit_bytes",
        "tree_nonprivate_working_set_proxy_bytes",
        "tree_page_fault_count",
        "system_commit_total_bytes",
        "system_commit_headroom_min_bytes",
        "available_physical_min_bytes",
    }
    _require_exact_fields(value, fields, label)
    parsed = {key: _json_int(value.get(key), f"{label} {key}") for key in fields}
    if any(item < 0 for item in parsed.values()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} has negative value")
    return parsed


def _validate_terminal_control_failed_invocation(
    *,
    report: Mapping[str, object],
    close: Mapping[str, object],
    reference: Mapping[str, object],
    report_path: Path,
    report_sha256: str,
    close_path: Path,
    close_sha256: str,
    operation: str,
    invocation_id: str,
    invocation_position: int,
    session_dir: Path,
    ready_path: Path,
    start_path: Path,
    complete_path: Path,
    exit_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    expected_thresholds: Mapping[str, object],
    observer: Mapping[str, object],
    policy: Mapping[str, object],
) -> None:
    bool_fields = (
        "process_handle_acquired", "execution_tree_includes_runner",
        "handshake_complete", "exit_code_allowed", "reported_exit_matches_actual",
        "monitor_ok", "monitor_ok_before_envelope_close", "cleanup_attempted",
        "cleanup_verified", "cleanup_identity_complete",
        "fallback_process_object_cleanup_attempted",
        "fallback_process_object_root_exit_verified",
        "terminal_system_sample_after_verified_termination_or_no_spawn",
        "mandatory_control_plane_gate_pass",
    )
    for key in bool_fields:
        if type(report.get(key)) is not bool:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control report {key} type invalid"
            )
    if (
        report["mandatory_control_plane_gate_pass"] is not False
        or report["monitor_ok"] is not False
        or report.get("final_system") is not None
        or report.get("ended_utc") is not None
        or report.get("wall_seconds") is not None
        or not isinstance(report.get("observed_survivors_after_cleanup"), list)
        or any(type(item) is not int or item <= 0 for item in report["observed_survivors_after_cleanup"])
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control report disposition invalid"
        )
    root_pid = _json_int(report.get("execution_tree_root_pid"), "failed control root pid")
    root_birth = _json_int(
        report.get("execution_tree_root_birth_utc_ticks"), "failed control root birth"
    )
    inner_pid = _json_int(report.get("inner_runner_pid"), "failed control inner pid")
    inner_birth = _json_int(
        report.get("inner_runner_birth_utc_ticks"), "failed control inner birth"
    )
    if (
        root_pid != observer["outer_process_id"]
        or root_birth != observer["outer_process_birth_utc_ticks"]
        or inner_pid != observer["inner_process_id"]
        or inner_birth != observer["inner_process_birth_utc_ticks"]
        or root_pid == inner_pid
        or inner_birth < root_birth
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control execution identity invalid"
        )
    _validate_control_plane_identity_list(
        report.get("observed_process_identities"),
        report.get("observed_process_ids"),
        label="failed terminal control observed",
    )
    _validate_control_plane_identity_list(
        report.get("cleanup_observed_process_identities"),
        report.get("cleanup_observed_process_ids"),
        label="failed terminal control cleanup",
    )
    observed = {
        row["process_id"]: row["birth_utc_ticks"]
        for row in report["observed_process_identities"]
    }
    cleanup = {
        row["process_id"]: row["birth_utc_ticks"]
        for row in report["cleanup_observed_process_identities"]
    }
    if (
        observed.get(root_pid) != root_birth
        or observed.get(inner_pid) != inner_birth
        or any(pid != root_pid and birth < root_birth for pid, birth in observed.items())
        or any(birth < inner_birth for birth in cleanup.values())
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control identity chronology invalid"
        )
    helper_pid = report.get("process_id")
    helper_birth = report.get("process_birth_utc_ticks")
    if report.get("process_handle_acquired") is True and (
        helper_pid is None or helper_birth is None
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control retained handle lacks identity"
        )
    if (helper_pid is None) != (helper_birth is None):
        if report.get("process_handle_acquired") is True or helper_birth is not None:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control helper identity null-pair invalid"
            )
    if helper_pid is not None:
        helper_pid = _json_int(helper_pid, "failed control helper pid")
        if helper_pid <= 0 or helper_pid in {root_pid, inner_pid}:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control helper pid invalid"
            )
        if helper_birth is not None:
            helper_birth = _json_int(helper_birth, "failed control helper birth")
            if helper_birth < inner_birth or observed.get(helper_pid) != helper_birth:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control helper birth invalid"
                )
    successful_samples = _json_int(
        report.get("successful_tree_sample_count"), "failed control sample count"
    )
    target_samples = _json_int(
        report.get("target_visible_tree_sample_count"), "failed control target samples"
    )
    if successful_samples < 0 or target_samples < 0 or target_samples > successful_samples:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control sample counts invalid"
        )
    report_peak = _validate_control_peak_shape(
        report.get("peak"), label="failed terminal control report peak"
    )
    for sample_key in ("pre_spawn_system", "pre_helper_after_provenance_system"):
        sample = report.get(sample_key)
        if sample is not None:
            _validate_system_sample(sample, f"failed terminal control {sample_key}")
            _require_control_peak_covers_system_sample(
                report_peak, sample, label=f"failed terminal control {sample_key}"
            )
    pre_tree = report.get("pre_helper_after_provenance_tree")
    if pre_tree is not None:
        parsed_tree = _validate_control_tree_sample(
            pre_tree, label="failed terminal control pre-helper tree"
        )
        if root_pid not in parsed_tree["process_ids"] or inner_pid not in parsed_tree["process_ids"]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control pre-helper identity missing"
            )
        if helper_pid is not None and helper_pid in parsed_tree["process_ids"]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control helper predates spawn"
            )
        _require_control_peak_covers_tree_sample(
            report_peak, parsed_tree, label="failed terminal control report"
        )
    for stream_name, stream_path in (("stdout", stdout_path), ("stderr", stderr_path)):
        size = report.get(f"{stream_name}_bytes")
        digest = report.get(f"{stream_name}_sha256")
        if (size is None) != (digest is None):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control {stream_name} null-pair invalid"
            )
        if size is not None:
            if not stream_path.is_file():
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control {stream_name} missing"
                )
            if (
                _json_int(size, f"failed control {stream_name} bytes")
                != stream_path.stat().st_size
                or _sha(stream_path) != _require_sha(digest, f"failed control {stream_name}")
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control {stream_name} mismatch"
                )
    marker_bindings = (
        (ready_path, "bootstrap_ready_sha256"),
        (start_path, "start_release_sha256"),
        (complete_path, "target_complete_sha256"),
        (complete_path, "target_exit_evidence_sha256"),
        (exit_path, "exit_release_sha256"),
    )
    for marker_path, key in marker_bindings:
        digest = report.get(key)
        if digest is not None:
            if not marker_path.is_file():
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control {key} missing"
                )
            if _sha(marker_path) != _require_sha(digest, f"failed control {key}"):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control {key} mismatch"
                )
    for key in ("reported_exit_code", "actual_exit_code"):
        if report.get(key) is not None:
            _json_int(report.get(key), f"failed control {key}")
    actual = report.get("actual_exit_code")
    reported = report.get("reported_exit_code")
    expected_exit_allowed = actual is not None and actual in report["allowed_exit_codes"]
    expected_exit_matches = actual is not None and reported is not None and actual == reported
    if (
        report.get("exit_code_allowed") is not expected_exit_allowed
        or report.get("reported_exit_matches_actual") is not expected_exit_matches
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control exit evidence inconsistent"
        )
    close_excluded = [
        "this_envelope_close_materialization_and_hash",
        "final_session_index_materialization_and_hash",
        "stdout_return_text_read_and_return_packaging",
        "caller_side_processing_after_return",
        "session_bootstrap_and_helper_script_materialization_before_first_invocation",
        "candidate_scope_seed_read_before_preflight_invocation",
        "runner_emergency_token_replacement_after_failed_consumer_invocation",
        "pre_exit_intent_evidence_materialization",
        "temporary_attempt_evidence_cleanup_or_quarantine",
    ]
    if (
        close.get("resource_envelope_excluded_tail") != close_excluded
        or close.get("process_membership_semantics")
        != "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_helper_descendants_created_and_exited_between_samples_not_claimed"
        or close.get("simultaneous_current_interval_semantics")
        != "sampled_current_outer_observer_plus_inner_runner_plus_helper_tree_during_this_control_invocation"
        or close.get("summed_os_lifetime_peak_semantics")
        != "conservative_stop_gate_includes_outer_and_inner_work_before_this_control_invocation_not_invocation_only_peak"
        or not _strict_json_equal(close.get("thresholds"), expected_thresholds)
        or not _strict_json_equal(
            close.get("pre_spawn_system"), report.get("pre_spawn_system")
        )
        or not _strict_json_equal(
            close.get("pre_helper_after_provenance_system"),
            report.get("pre_helper_after_provenance_system"),
        )
        or close.get("wall_clock_kind")
        != "System.Diagnostics.Stopwatch_frozen_once_before_close_materialization"
        or close.get("mandatory_control_plane_gate_pass") is not False
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control close semantics invalid"
        )
    for key in (
        "high_system_floor_recheck_before_and_after",
        "terminal_system_sample_after_verified_termination_or_no_spawn",
        "monitor_ok", "cleanup_verified",
    ):
        if type(close.get(key)) is not bool:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control close {key} type invalid"
            )
    for key in ("monitor_error", "stop_reason"):
        if close.get(key) is not None and (
            not isinstance(close.get(key), str) or not close[key]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"failed control close {key} invalid"
            )
    close_peak = _validate_control_peak_shape(
        close.get("peak"), label="failed terminal control close peak"
    )
    for key in report_peak:
        if key in {"system_commit_headroom_min_bytes", "available_physical_min_bytes"}:
            if close_peak[key] > report_peak[key]:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control minimum peak chronology invalid"
                )
        elif close_peak[key] < report_peak[key]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control maximum peak chronology invalid"
            )
    final_system = close.get("final_system")
    if final_system is not None:
        _validate_system_sample(final_system, "failed terminal control final system")
        _require_control_peak_covers_system_sample(
            close_peak, final_system, label="failed terminal control close"
        )
    close_tree = close.get("envelope_close_runner_tree_sample")
    if close_tree is not None:
        parsed_close_tree = _validate_control_tree_sample(
            close_tree, label="failed terminal control close tree"
        )
        if root_pid not in parsed_close_tree["process_ids"] or inner_pid not in parsed_close_tree["process_ids"]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control close root identity missing"
            )
        _require_control_peak_covers_tree_sample(
            close_peak, parsed_close_tree, label="failed terminal control close"
        )
    started = _utc(close.get("started_utc"), "failed terminal control close start UTC")
    ended = _utc(close.get("ended_utc"), "failed terminal control close end UTC")
    wall = _finite_float(close.get("wall_seconds"), "failed terminal control wall")
    if (
        started != _utc(report.get("started_utc"), "failed terminal control report start UTC")
        or ended < started
        or wall < 0.0
        or abs((ended - started).total_seconds() - wall)
        > max(5.0, 0.05 * max(1.0, wall))
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed terminal control wall chronology invalid"
        )
    peak_limits = {
        "tree_working_set_bytes": policy["tree_working_set_stop_bytes"],
        "tree_summed_process_lifetime_peak_working_set_bytes": policy[
            "tree_working_set_stop_bytes"
        ],
        "tree_private_commit_bytes": policy["tree_private_stop_bytes"],
        "tree_committed_pagefile_bytes": policy["tree_commit_stop_bytes"],
        "tree_summed_process_lifetime_peak_commit_bytes": policy[
            "tree_commit_stop_bytes"
        ],
    }
    streams_within_gate = all(
        stream_path.is_file()
        and type(report.get(f"{stream_name}_bytes")) is int
        and report[f"{stream_name}_bytes"] <= 16 * 1024**2
        for stream_name, stream_path in (("stdout", stdout_path), ("stderr", stderr_path))
    )
    final_floor_pass = bool(
        isinstance(final_system, Mapping)
        and final_system["commit_headroom_bytes"]
        >= policy["minimum_commit_headroom_before_spawn_bytes"]
        and final_system["available_physical_bytes"]
        >= policy["minimum_available_physical_before_spawn_bytes"]
    )
    recomputed_success_gate = bool(
        close.get("monitor_ok") is True
        and close.get("monitor_error") is None
        and close.get("stop_reason") is None
        and close.get("cleanup_verified") is True
        and close.get("high_system_floor_recheck_before_and_after") is True
        and close.get("terminal_system_sample_after_verified_termination_or_no_spawn")
        is True
        and report.get("exit_code_allowed") is True
        and report.get("reported_exit_matches_actual") is True
        and streams_within_gate
        and wall <= CONTROL_PLANE_WALL_STOP_SECONDS
        and all(close_peak[key] <= limit for key, limit in peak_limits.items())
        and close_peak["system_commit_headroom_min_bytes"]
        >= policy["commit_headroom_floor_bytes"]
        and close_peak["available_physical_min_bytes"]
        >= policy["available_physical_floor_bytes"]
        and final_floor_pass
    )
    if recomputed_success_gate:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control gate contradicts complete evidence"
        )
    expected_preclose = session_dir / f"session-index-{2 * invocation_position + 1:04d}.json"
    if (
        Path(close.get("preclose_session_index_path", "")).resolve()
        != expected_preclose.resolve()
        or not expected_preclose.is_file()
        or _sha(expected_preclose)
        != _require_sha(close.get("preclose_session_index_sha256"), "failed control preclose")
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failed control preclose binding invalid"
        )
    _validate_control_reference(
        reference,
        operation=operation,
        invocation_id=invocation_id,
        report_path=report_path,
        report_sha256=report_sha256,
        report=report,
        envelope_close_path=close_path,
        envelope_close_sha256=close_sha256,
        mandatory_gate=False,
    )


def _validate_terminal_control_invocation(
    *,
    report: Mapping[str, object],
    report_path: Path,
    report_sha256: str,
    close: Mapping[str, object],
    close_path: Path,
    close_sha256: str,
    reference: Mapping[str, object],
    operation: str,
    invocation_id: str,
    invocation_position: int,
    session_dir: Path,
    bootstrap: Path,
    canonical_helper: Path,
    observer: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    require_pass_gate: bool,
) -> None:
    policy = manifest["resource_policy"]
    invocation_dir = report_path.parent
    ready_path = (invocation_dir / "bootstrap-ready.json").resolve()
    start_path = (invocation_dir / "start-release.json").resolve()
    complete_path = (invocation_dir / "target-complete.json").resolve()
    exit_path = (invocation_dir / "exit-release.json").resolve()
    stdout_path = (invocation_dir / "stdout.txt").resolve()
    stderr_path = (invocation_dir / "stderr.txt").resolve()
    for raw_path, expected_path, label in (
        (report.get("target_exit_evidence_path"), complete_path, "target complete"),
        (report.get("stdout_path"), stdout_path, "stdout"),
        (report.get("stderr_path"), stderr_path, "stderr"),
    ):
        if not isinstance(raw_path, str) or Path(raw_path).resolve() != expected_path:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal control {label} path mismatch"
            )
    python_path = _owned_control_file(
        report.get("python_path"), label="terminal control Python"
    )
    target_path = _owned_control_file(
        report.get("target_script_path"), label="terminal control target"
    )
    if (
        python_path != Path(sys.executable).resolve()
        or report.get("python_sha256") != _sha(python_path)
        or report.get("bootstrap_sha256") != CONTROL_PLANE_BOOTSTRAP_SHA256
        or _sha(bootstrap) != CONTROL_PLANE_BOOTSTRAP_SHA256
        or _sha(canonical_helper) != CONTROL_PLANE_CANONICAL_HASH_HELPER_SHA256
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control helper binding mismatch"
        )
    expected_target = (
        canonical_helper.resolve()
        if operation == "canonical_json_hash"
        else Path(__file__).resolve()
    )
    expected_target_sha = (
        CONTROL_PLANE_CANONICAL_HASH_HELPER_SHA256
        if operation == "canonical_json_hash"
        else manifest["bindings"]["fixture_sha256"]
    )
    if (
        target_path != expected_target
        or report.get("target_script_sha256") != expected_target_sha
        or _sha(target_path) != expected_target_sha
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control target binding mismatch"
        )
    target_argv = report.get("target_argv")
    process_argv = report.get("process_argv")
    if (
        not isinstance(target_argv, list)
        or not target_argv
        or any(type(item) is not str for item in target_argv)
        or target_argv[0] != str(target_path)
        or not isinstance(process_argv, list)
        or any(type(item) is not str for item in process_argv)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control argv type invalid"
        )
    expected_process_argv = [
        str(bootstrap.resolve()),
        str(ready_path),
        str(start_path),
        str(complete_path),
        str(exit_path),
        *target_argv,
    ]
    if (
        not _strict_json_equal(process_argv, expected_process_argv)
        or report.get("target_argv_json_sha256") != _ordered_json_sha256(target_argv)
        or report.get("process_argv_json_sha256") != _ordered_json_sha256(process_argv)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control argv binding invalid"
        )
    expected_allowed = [0, 2] if operation == "finalizer" else [0]
    if report.get("allowed_exit_codes") != expected_allowed:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control allowed exit-code set mismatch"
        )
    if operation == "preflight":
        expected_target_argv = [
            str(Path(__file__).resolve()),
            "--stage",
            "preflight-primary-h4-p0r",
            "--review-token",
            str(TOKEN_PATH.resolve()),
        ]
        if not _strict_json_equal(target_argv, expected_target_argv):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal preflight argv mismatch"
            )
    elif operation == "canonical_json_hash":
        if len(target_argv) != 2 or not Path(target_argv[1]).is_absolute():
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal canonical-hash argv mismatch"
            )
    else:
        expected_stage = (
            "finalize-primary-h4-p0r"
            if operation == "finalizer"
            else "consume-primary-h4-p0r-token"
        )
        if (
            len(target_argv) < 3
            or target_argv[1:3] != ["--stage", expected_stage]
            or target_argv.count("--stage") != 1
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"terminal {operation} argv mismatch",
            )
        expected_nonce = claim.get("guard_nonce")
        if not isinstance(expected_nonce, str) or re.fullmatch(r"[0-9a-f]{32}", expected_nonce) is None:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control claim nonce invalid"
            )
        required_pairs = {
            "--review-token": "review_token",
            "--claim-file": "claim",
            "--guard-contract": "guard",
        }
        for flag, provenance_role in required_pairs.items():
            if target_argv.count(flag) != 1:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {operation} {flag} invalid"
                )
            flag_position = target_argv.index(flag)
            if flag_position + 1 >= len(target_argv):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {operation} {flag} lacks value"
                )
        guard_nonce_position = (
            target_argv.index("--guard-nonce")
            if target_argv.count("--guard-nonce") == 1
            else -1
        )
        if (
            guard_nonce_position < 0
            or guard_nonce_position + 1 >= len(target_argv)
            or target_argv[guard_nonce_position + 1] != expected_nonce
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {operation} guard nonce mismatch"
            )
        if operation == "finalizer":
            if (
                target_argv.count("--resource-report") != 1
                or target_argv.count("--attempt-status") != 0
                or target_argv.count("--result-file") != 0
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal finalizer argv flags invalid"
                )
        else:
            attempt_position = (
                target_argv.index("--attempt-status")
                if target_argv.count("--attempt-status") == 1
                else -1
            )
            if (
                attempt_position < 0
                or attempt_position + 1 >= len(target_argv)
                or target_argv[attempt_position + 1]
                not in {
                    "completed_pass", "completed_failure", "resource_stop",
                    "finalizer_failure", "runner_exception", "spawn_failure",
                }
                or target_argv.count("--result-file") > 1
                or target_argv.count("--resource-report") > 1
                or target_argv.count("--child-stdout") > 1
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal consumer argv flags invalid"
                )
    provenance_keys = {
        "review_token", "claim", "guard", "resource", "result", "canonical_input"
    }
    gate = reference.get("mandatory_control_plane_gate_pass")
    if type(gate) is not bool:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control reference gate type invalid"
        )
    for name in ("attempt_provenance_before", "attempt_provenance_after"):
        provenance = report.get(name)
        if provenance is None:
            if gate or report.get(f"{name}_sha256") is not None:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal control {name} null binding invalid"
                )
            continue
        if (
            not isinstance(provenance, Mapping)
            or set(provenance) != provenance_keys
            or report.get(f"{name}_sha256") != _ordered_json_sha256(provenance)
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal control {name} invalid"
            )
        for row in provenance.values():
            _require_exact_fields(
                row, {"path", "exists", "bytes", "sha256"}, "control provenance row"
            )
            exists = row.get("exists")
            if type(exists) is not bool or not isinstance(row.get("path"), (str, type(None))):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control provenance type invalid"
                )
            if exists:
                size = _json_int(row.get("bytes"), "control provenance bytes")
                if size < 0:
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA", "control provenance size invalid"
                    )
                _require_sha(row.get("sha256"), "control provenance sha256")
            elif row.get("bytes") is not None or row.get("sha256") is not None:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "control provenance null-pair invalid"
                )
    if operation == "canonical_json_hash" and isinstance(
        report.get("attempt_provenance_before"), Mapping
    ):
        canonical_row = report["attempt_provenance_before"]["canonical_input"]
        if canonical_row.get("path") != target_argv[1] or canonical_row.get("exists") is not True:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "canonical input provenance mismatch"
            )
    if isinstance(report.get("attempt_provenance_before"), Mapping):
        provenance_before = report["attempt_provenance_before"]
        if operation in {"finalizer", "token_consumer"}:
            for flag, role in required_pairs.items():
                row = provenance_before.get(role)
                flag_value = target_argv[target_argv.index(flag) + 1]
                if not isinstance(row, Mapping) or row.get("path") != flag_value:
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"terminal {operation} {role} provenance mismatch",
                    )
            resource_count = target_argv.count("--resource-report")
            resource_row = provenance_before.get("resource")
            if resource_count == 1 and (
                not isinstance(resource_row, Mapping)
                or resource_row.get("path")
                != target_argv[target_argv.index("--resource-report") + 1]
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal resource argv provenance mismatch"
                )
            result_count = target_argv.count("--result-file")
            result_row = provenance_before.get("result")
            if result_count == 1 and (
                not isinstance(result_row, Mapping)
                or result_row.get("path")
                != target_argv[target_argv.index("--result-file") + 1]
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal result argv provenance mismatch"
                )
            token_value = provenance_before["review_token"]["path"]
            claim_value = provenance_before["claim"]["path"]
            guard_value = provenance_before["guard"]["path"]
            if operation == "finalizer":
                expected_operation_argv = [
                    str(Path(__file__).resolve()),
                    "--stage", "finalize-primary-h4-p0r",
                    "--resource-report", provenance_before["resource"]["path"],
                    "--review-token", token_value,
                    "--guard-contract", guard_value,
                    "--guard-nonce", expected_nonce,
                    "--claim-file", claim_value,
                ]
                if target_argv.count("--child-stdout") == 1:
                    child_position = target_argv.index("--child-stdout")
                    if (
                        child_position + 1 >= len(target_argv)
                        or not Path(target_argv[child_position + 1]).is_absolute()
                        or Path(target_argv[child_position + 1]).name != "numerical.json"
                    ):
                        raise AvBsError(
                            "BLOCKED_AV_BS_RESULT_SCHEMA",
                            "terminal finalizer child stdout argv invalid",
                        )
                    expected_operation_argv += [
                        "--child-stdout", target_argv[child_position + 1]
                    ]
            else:
                expected_operation_argv = [
                    str(Path(__file__).resolve()),
                    "--stage", "consume-primary-h4-p0r-token",
                    "--review-token", token_value,
                    "--claim-file", claim_value,
                    "--guard-contract", guard_value,
                    "--guard-nonce", expected_nonce,
                    "--attempt-status", target_argv[attempt_position + 1],
                ]
                if target_argv.count("--result-file") == 1:
                    expected_operation_argv += [
                        "--result-file", provenance_before["result"]["path"]
                    ]
                if target_argv.count("--child-stdout") == 1:
                    child_position = target_argv.index("--child-stdout")
                    if (
                        child_position + 1 >= len(target_argv)
                        or not Path(target_argv[child_position + 1]).is_absolute()
                        or Path(target_argv[child_position + 1]).name != "numerical.json"
                    ):
                        raise AvBsError(
                            "BLOCKED_AV_BS_RESULT_SCHEMA",
                            "terminal consumer child stdout argv invalid",
                        )
                    expected_operation_argv += [
                        "--child-stdout", target_argv[child_position + 1]
                    ]
                if target_argv.count("--resource-report") == 1:
                    expected_operation_argv += [
                        "--resource-report", provenance_before["resource"]["path"]
                    ]
            if not _strict_json_equal(target_argv, expected_operation_argv):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal {operation} exact argv order mismatch",
                )
    expected_thresholds = {
        "tree_ws_stop_bytes": policy["tree_working_set_stop_bytes"],
        "tree_private_stop_bytes": policy["tree_private_stop_bytes"],
        "tree_commit_stop_bytes": policy["tree_commit_stop_bytes"],
        "commit_headroom_floor_bytes": policy["commit_headroom_floor_bytes"],
        "available_physical_floor_bytes": policy["available_physical_floor_bytes"],
        "high_pre_post_commit_headroom_floor_bytes": policy[
            "minimum_commit_headroom_before_spawn_bytes"
        ],
        "high_pre_post_available_physical_floor_bytes": policy[
            "minimum_available_physical_before_spawn_bytes"
        ],
    }
    expected_report_lists = {
        "resource_envelope_included_work": [
            "attempt_provenance_before",
            "helper_spawn_identity_sampling_handshake_and_cleanup",
            "stdout_stderr_marker_hashes",
            "attempt_provenance_after",
            "process_report_materialization_and_hash",
            "preclose_session_index_materialization_and_hash",
            "runner_lifetime_peak_and_system_sample_after_preclose_index",
        ],
        "resource_envelope_excluded_work": [
            "envelope_close_materialization_and_hash",
            "final_session_index_materialization_and_hash",
            "stdout_return_text_read_and_return_packaging",
            "caller_side_processing_after_return",
            "session_bootstrap_and_helper_script_materialization_before_first_invocation",
            "candidate_scope_seed_read_before_preflight_invocation",
            "runner_emergency_token_replacement_after_failed_consumer_invocation",
            "pre_exit_intent_evidence_materialization",
            "temporary_attempt_evidence_cleanup_or_quarantine",
            "unobserved_descendants_created_and_exited_between_100ms_samples",
            "external_runner_or_machine_kill",
        ],
    }
    for key, expected in expected_report_lists.items():
        if not _strict_json_equal(report.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal control {key} mismatch"
            )
    expected_previous_index = (
        None
        if invocation_position == 0
        else session_dir / f"session-index-{2 * invocation_position:04d}.json"
    )
    expected_previous_sha = (
        None if expected_previous_index is None else _sha(expected_previous_index)
    )
    if (
        report.get("monitor_kind")
        != "Win32_outer_observer_plus_inner_runner_plus_identity_bound_sampled_python_tree_Toolhelp32_Psapi_100ms_with_completion_handshake"
        or report.get("process_membership_semantics")
        != "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_identity_bound_helper_tree_descendants_between_samples_not_claimed"
        or report.get("cleanup_scope")
        != "inner_owned_identity_bound_helper_tree_or_retained_Process_object_root_fallback_outer_observer_and_inner_runner_excluded_no_unobserved_descendant_or_external_kill_guarantee"
        or report.get("simultaneous_current_interval_semantics")
        != "sampled_current_outer_observer_plus_inner_runner_plus_helper_tree_during_this_control_invocation"
        or report.get("summed_os_lifetime_peak_semantics")
        != "conservative_stop_gate_includes_outer_and_inner_work_before_this_control_invocation_not_invocation_only_peak"
        or (
            report.get("previous_session_index_path") is not None
            if expected_previous_index is None
            else Path(report.get("previous_session_index_path", "")).resolve()
            != expected_previous_index.resolve()
        )
        or report.get("previous_session_index_sha256") != expected_previous_sha
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control report chain semantics invalid"
        )
    if (
        not _strict_json_equal(report.get("thresholds"), expected_thresholds)
        or report.get("poll_interval_ms") != 100
        or report.get("wall_stop_seconds") != CONTROL_PLANE_WALL_STOP_SECONDS
        or report.get("stream_stop_bytes_each") != 16 * 1024**2
        or report.get("wall_clock_kind") != "System.Diagnostics.Stopwatch"
        or report.get("execution_tree_includes_runner") is not True
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control report gate evidence invalid"
        )
    if gate is False:
        if require_pass_gate:
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", "terminal pass contains a failed control gate"
            )
        _validate_terminal_control_failed_invocation(
            report=report,
            close=close,
            reference=reference,
            report_path=report_path,
            report_sha256=report_sha256,
            close_path=close_path,
            close_sha256=close_sha256,
            operation=operation,
            invocation_id=invocation_id,
            invocation_position=invocation_position,
            session_dir=session_dir,
            ready_path=ready_path,
            start_path=start_path,
            complete_path=complete_path,
            exit_path=exit_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            expected_thresholds=expected_thresholds,
            observer=observer,
            policy=policy,
        )
        return
    for artifact_path, label in (
        (ready_path, "ready"), (start_path, "start release"),
        (complete_path, "target complete"), (exit_path, "exit release"),
        (stdout_path, "stdout"), (stderr_path, "stderr"),
    ):
        if not artifact_path.is_file():
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal control {label} is missing"
            )
    if (
        report.get("process_handle_acquired") is not True
        or report.get("handshake_complete") is not True
        or report.get("monitor_ok_before_envelope_close") is not True
        or report.get("cleanup_verified") is not True
        or report.get("cleanup_identity_complete") is not True
        or report.get("observed_survivors_after_cleanup") != []
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control successful gate evidence invalid"
        )
    system_samples: dict[str, Mapping[str, object]] = {}
    for sample_key in ("pre_spawn_system", "pre_helper_after_provenance_system"):
        sample = report.get(sample_key)
        if not isinstance(sample, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", f"terminal control {sample_key} missing"
            )
        _validate_system_sample(sample, f"terminal control {sample_key}")
        system_samples[sample_key] = sample
        if (
            sample["commit_headroom_bytes"]
            < policy["minimum_commit_headroom_before_spawn_bytes"]
            or sample["available_physical_bytes"]
            < policy["minimum_available_physical_before_spawn_bytes"]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", f"terminal control {sample_key} floor failed"
            )
    pre_tree = _validate_control_tree_sample(
        report.get("pre_helper_after_provenance_tree"),
        label="terminal control pre-helper tree",
    )
    root_pid = _json_int(report.get("execution_tree_root_pid"), "control root pid")
    root_birth = _json_int(
        report.get("execution_tree_root_birth_utc_ticks"), "control root birth"
    )
    inner_pid = _json_int(report.get("inner_runner_pid"), "control inner pid")
    inner_birth = _json_int(
        report.get("inner_runner_birth_utc_ticks"), "control inner birth"
    )
    helper_pid = _json_int(report.get("process_id"), "control helper pid")
    helper_birth = _json_int(report.get("process_birth_utc_ticks"), "control helper birth")
    successful_samples = _json_int(
        report.get("successful_tree_sample_count"), "control samples"
    )
    target_samples = _json_int(
        report.get("target_visible_tree_sample_count"), "control target samples"
    )
    if (
        root_pid != observer["outer_process_id"]
        or root_birth != observer["outer_process_birth_utc_ticks"]
        or inner_pid != observer["inner_process_id"]
        or inner_birth != observer["inner_process_birth_utc_ticks"]
        or helper_pid in {root_pid, inner_pid}
        or helper_birth < inner_birth
        or not {root_pid, inner_pid}.issubset(set(pre_tree["process_ids"]))
        or helper_pid in pre_tree["process_ids"]
        or successful_samples < 2
        or target_samples < 2
        or target_samples > successful_samples
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control process/sample gate invalid"
        )
    _validate_control_plane_identity_list(
        report.get("observed_process_identities"),
        report.get("observed_process_ids"),
        label="terminal control observed",
    )
    _validate_control_plane_identity_list(
        report.get("cleanup_observed_process_identities"),
        report.get("cleanup_observed_process_ids"),
        label="terminal control cleanup",
    )
    observed = {row["process_id"]: row["birth_utc_ticks"] for row in report["observed_process_identities"]}
    cleanup = {row["process_id"]: row["birth_utc_ticks"] for row in report["cleanup_observed_process_identities"]}
    if (
        observed.get(root_pid) != root_birth
        or observed.get(inner_pid) != inner_birth
        or observed.get(helper_pid) != helper_birth
        or cleanup.get(helper_pid) != helper_birth
        or any(pid != root_pid and birth < root_birth for pid, birth in observed.items())
        or any(birth < inner_birth for birth in cleanup.values())
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control identity chain invalid"
        )
    report_peak = _validate_control_peak(
        report.get("peak"), policy, label="terminal control report peak"
    )
    _require_control_peak_covers_tree_sample(
        report_peak, pre_tree, label="terminal control report"
    )
    for name, sample in system_samples.items():
        _require_control_peak_covers_system_sample(
            report_peak, sample, label=f"terminal control {name}"
        )
    for stream_name, stream_path in (("stdout", stdout_path), ("stderr", stderr_path)):
        stream_size = _json_int(report.get(f"{stream_name}_bytes"), f"control {stream_name} bytes")
        if (
            stream_size != stream_path.stat().st_size
            or stream_size > 16 * 1024**2
            or _sha(stream_path)
            != _require_sha(report.get(f"{stream_name}_sha256"), f"control {stream_name}")
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", f"terminal control {stream_name} invalid"
            )
    marker_bindings = (
        (ready_path, "bootstrap_ready_sha256"),
        (start_path, "start_release_sha256"),
        (complete_path, "target_complete_sha256"),
        (complete_path, "target_exit_evidence_sha256"),
        (exit_path, "exit_release_sha256"),
    )
    for marker_path, key in marker_bindings:
        if _sha(marker_path) != _require_sha(report.get(key), f"control {key}"):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal control {key} mismatch"
            )
    ready = _read(ready_path, "terminal control ready")
    complete_marker = _read(complete_path, "terminal control complete")
    _require_exact_fields(
        ready, {"schema", "process_id", "monotonic_ns"}, "terminal control ready"
    )
    _require_exact_fields(
        complete_marker,
        {"schema", "process_id", "exit_code", "monotonic_ns"},
        "terminal control complete",
    )
    actual_exit = _json_int(report.get("actual_exit_code"), "control actual exit")
    reported_exit = _json_int(report.get("reported_exit_code"), "control reported exit")
    allowed = report.get("allowed_exit_codes")
    if (
        ready.get("schema") != "AV-BS1-h4-p0r-control-plane-bootstrap-ready-v1"
        or ready.get("process_id") != helper_pid
        or complete_marker.get("schema")
        != "AV-BS1-h4-p0r-control-plane-target-complete-v1"
        or complete_marker.get("process_id") != helper_pid
        or complete_marker.get("exit_code") != actual_exit
        or reported_exit != actual_exit
        or not isinstance(allowed, list)
        or any(type(item) is not int for item in allowed)
        or allowed != list(dict.fromkeys(allowed))
        or actual_exit not in allowed
        or report.get("exit_code_allowed") is not True
        or report.get("reported_exit_matches_actual") is not True
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control exit evidence invalid"
        )
    releases: list[Mapping[str, object]] = []
    for marker_path, schema in (
        (start_path, "AV-BS1-h4-p0r-control-plane-start-release-v1"),
        (exit_path, "AV-BS1-h4-p0r-control-plane-exit-release-v1"),
    ):
        marker = _read(marker_path, f"terminal control {marker_path.name}")
        _require_exact_fields(
            marker,
            {"schema", "invocation_id", "process_id", "sample_perf_counter_ns", "sample_utc"},
            f"terminal control {marker_path.name}",
        )
        if (
            marker.get("schema") != schema
            or marker.get("invocation_id") != invocation_id
            or marker.get("process_id") != helper_pid
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control release marker invalid"
            )
        _json_int(marker.get("sample_perf_counter_ns"), "control release sample")
        _utc(marker.get("sample_utc"), "control release UTC")
        releases.append(marker)
    report_started = _utc(report.get("started_utc"), "control report started UTC")
    marker_ns = (
        ready["monotonic_ns"],
        releases[0]["sample_perf_counter_ns"],
        complete_marker["monotonic_ns"],
        releases[1]["sample_perf_counter_ns"],
    )
    if marker_ns != tuple(sorted(marker_ns)) or report_started > _utc(
        releases[0]["sample_utc"], "control start release UTC"
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control marker chronology invalid"
        )
    close_excluded = [
        "this_envelope_close_materialization_and_hash",
        "final_session_index_materialization_and_hash",
        "stdout_return_text_read_and_return_packaging",
        "caller_side_processing_after_return",
        "session_bootstrap_and_helper_script_materialization_before_first_invocation",
        "candidate_scope_seed_read_before_preflight_invocation",
        "runner_emergency_token_replacement_after_failed_consumer_invocation",
        "pre_exit_intent_evidence_materialization",
        "temporary_attempt_evidence_cleanup_or_quarantine",
    ]
    if (
        close.get("resource_envelope_excluded_tail") != close_excluded
        or close.get("process_membership_semantics")
        != "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_helper_descendants_created_and_exited_between_samples_not_claimed"
        or close.get("simultaneous_current_interval_semantics")
        != "sampled_current_outer_observer_plus_inner_runner_plus_helper_tree_during_this_control_invocation"
        or close.get("summed_os_lifetime_peak_semantics")
        != "conservative_stop_gate_includes_outer_and_inner_work_before_this_control_invocation_not_invocation_only_peak"
        or not _strict_json_equal(close.get("thresholds"), expected_thresholds)
        or not _strict_json_equal(close.get("pre_spawn_system"), report["pre_spawn_system"])
        or not _strict_json_equal(
            close.get("pre_helper_after_provenance_system"),
            report["pre_helper_after_provenance_system"],
        )
        or close.get("wall_clock_kind")
        != "System.Diagnostics.Stopwatch_frozen_once_before_close_materialization"
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control close semantics invalid"
        )
    final_system = close.get("final_system")
    if not isinstance(final_system, Mapping):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control final system missing"
        )
    _validate_system_sample(final_system, "terminal control final system")
    close_tree = _validate_control_tree_sample(
        close.get("envelope_close_runner_tree_sample"),
        label="terminal control close tree",
    )
    if (
        root_pid not in close_tree["process_ids"]
        or inner_pid not in close_tree["process_ids"]
        or helper_pid in close_tree["process_ids"]
        or final_system["commit_headroom_bytes"]
        < policy["minimum_commit_headroom_before_spawn_bytes"]
        or final_system["available_physical_bytes"]
        < policy["minimum_available_physical_before_spawn_bytes"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control final sample gate failed"
        )
    close_peak = _validate_control_peak(
        close.get("peak"), policy, label="terminal control close peak"
    )
    _require_control_peak_covers_tree_sample(
        close_peak, close_tree, label="terminal control close"
    )
    _require_control_peak_covers_system_sample(
        close_peak, final_system, label="terminal control close"
    )
    maximum_keys = {
        "tree_working_set_bytes",
        "tree_summed_process_lifetime_peak_working_set_bytes",
        "tree_private_commit_bytes",
        "tree_committed_pagefile_bytes",
        "tree_summed_process_lifetime_peak_commit_bytes",
        "tree_nonprivate_working_set_proxy_bytes",
        "tree_page_fault_count",
        "system_commit_total_bytes",
    }
    minimum_keys = {"system_commit_headroom_min_bytes", "available_physical_min_bytes"}
    if any(close_peak[key] < report_peak[key] for key in maximum_keys) or any(
        close_peak[key] > report_peak[key] for key in minimum_keys
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control peak chronology invalid"
        )
    started = _utc(close.get("started_utc"), "terminal control close start UTC")
    ended = _utc(close.get("ended_utc"), "terminal control close end UTC")
    wall = _finite_float(close.get("wall_seconds"), "terminal control wall")
    if (
        started != report_started
        or ended < started
        or _utc(releases[1]["sample_utc"], "control exit release UTC") > ended
        or wall < 0.0
        or wall > CONTROL_PLANE_WALL_STOP_SECONDS
        or abs((ended - started).total_seconds() - wall)
        > max(5.0, 0.05 * max(1.0, wall))
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control wall chronology invalid"
        )
    recomputed_gate = bool(
        close.get("monitor_ok") is True
        and close.get("monitor_error") is None
        and close.get("stop_reason") is None
        and close.get("cleanup_verified") is True
        and close.get("high_system_floor_recheck_before_and_after") is True
        and close.get("terminal_system_sample_after_verified_termination_or_no_spawn")
        is True
    )
    gate = reference.get("mandatory_control_plane_gate_pass")
    if type(gate) is not bool or gate is not close.get("mandatory_control_plane_gate_pass"):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control gate binding invalid"
        )
    if gate is not recomputed_gate or (require_pass_gate and gate is not True):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal control gate recomputation failed"
        )
    expected_preclose = session_dir / f"session-index-{2 * invocation_position + 1:04d}.json"
    if (
        Path(close.get("preclose_session_index_path", "")).resolve()
        != expected_preclose.resolve()
        or not expected_preclose.is_file()
        or _sha(expected_preclose)
        != _require_sha(close.get("preclose_session_index_sha256"), "control preclose")
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control preclose binding invalid"
        )
    _validate_control_reference(
        reference,
        operation=operation,
        invocation_id=invocation_id,
        report_path=report_path,
        report_sha256=report_sha256,
        report=report,
        envelope_close_path=close_path,
        envelope_close_sha256=close_sha256,
        mandatory_gate=gate,
    )


def _validate_terminal_control_index(
    relative_path: object,
    expected_sha256: object,
    manifest: Mapping[str, object],
    observer: Mapping[str, object],
    claim: Mapping[str, object],
    require_pass_gates: bool,
) -> Mapping[str, object]:
    path = _terminal_file(
        relative_path,
        label="terminal control-plane session index",
        pattern=(
            r"validation-output/av-bs1/control-plane/session-[0-9a-f]{32}/"
            r"session-index-[0-9]{4}\.json"
        ),
    )
    index, index_sha = _read_stable_json(path, "terminal control-plane session index")
    if index_sha != _require_sha(expected_sha256, "terminal control-plane index"):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control-plane index hash mismatch"
        )
    index_fields = {
        "schema", "program", "case_id", "session_id", "sequence", "index_role",
        "previous_index_path", "previous_index_sha256", "runner_sha256",
        "bootstrap_sha256", "canonical_hash_helper_sha256",
        "execution_resource_scope_sha256",
        "execution_resource_scope_binding_semantics", "report_references",
        "envelope_close_path", "envelope_close_sha256",
        "mandatory_control_plane_gate_pass",
        "this_index_materialization_inside_resource_envelope",
        "consume_after_every_owned_claim_outcome",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
    }
    _require_exact_fields(index, index_fields, "terminal control-plane index")
    session_dir = path.parent
    session_id = session_dir.name
    sequence = _json_int(index.get("sequence"), "terminal control index sequence")
    references = index.get("report_references")
    if (
        index.get("schema") != CONTROL_PLANE_INDEX_SCHEMA
        or index.get("program") != PROGRAM
        or index.get("case_id") != CASE_ID
        or re.fullmatch(r"session-[0-9a-f]{32}", session_id) is None
        or index.get("session_id") != session_id
        or index.get("index_role") != "final_binds_resource_envelope_close"
        or not isinstance(references, list)
        or not references
        or sequence != 2 * len(references)
        or index.get("runner_sha256") != manifest["bindings"]["runner_sha256"]
        or index.get("execution_resource_scope_sha256")
        != manifest["execution_resource_scope_sha256"]
        or index.get("execution_resource_scope_binding_semantics")
        != "validated_preflight_execution_scope_hash"
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control-plane index identity mismatch"
        )
    _require_bool(
        index.get("this_index_materialization_inside_resource_envelope"),
        False,
        "terminal final index envelope placement",
    )
    _require_bool(
        index.get("consume_after_every_owned_claim_outcome"),
        False,
        "terminal final index lifecycle claim",
    )
    _require_bool(
        index.get("external_runner_or_machine_kill_terminal_state_guaranteed"),
        False,
        "terminal final index external-kill guarantee",
    )
    bootstrap = session_dir / "bounded-bootstrap.py"
    canonical_helper = session_dir / "canonical-json-sha256.py"
    if (
        not bootstrap.is_file()
        or not canonical_helper.is_file()
        or index.get("bootstrap_sha256") != CONTROL_PLANE_BOOTSTRAP_SHA256
        or index.get("canonical_hash_helper_sha256")
        != CONTROL_PLANE_CANONICAL_HASH_HELPER_SHA256
        or _sha(bootstrap) != CONTROL_PLANE_BOOTSTRAP_SHA256
        or _sha(canonical_helper)
        != CONTROL_PLANE_CANONICAL_HASH_HELPER_SHA256
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control helper binding mismatch"
        )
    report_fields = set(
        """
        schema program case_id stage operation invocation_id report_path runner_path
        runner_sha256 python_path python_sha256 bootstrap_path bootstrap_sha256
        target_script_path target_script_sha256 target_argv target_argv_json_sha256
        process_argv process_argv_json_sha256 attempt_provenance_before
        attempt_provenance_before_sha256 attempt_provenance_after
        attempt_provenance_after_sha256 execution_resource_scope_sha256
        execution_resource_scope_binding_semantics factor_fit_interval_included
        simultaneous_current_interval_semantics summed_os_lifetime_peak_semantics
        authorization_effect previous_session_index_path previous_session_index_sha256
        monitor_kind process_membership_semantics cleanup_scope resource_envelope_status
        resource_envelope_included_work resource_envelope_excluded_work
        report_and_index_required_for_invocation_success
        gate_is_authoritative_only_in_envelope_close envelope_close_path
        envelope_close_sha256 poll_interval_ms wall_stop_seconds
        stream_stop_bytes_each thresholds pre_spawn_system
        pre_helper_after_provenance_tree pre_helper_after_provenance_system
        final_system high_system_floor_recheck_before_and_after
        high_system_floor_recheck_authoritative_in_envelope_close started_utc
        ended_utc wall_seconds wall_clock_kind process_id process_birth_utc_ticks
        process_handle_acquired execution_tree_root_pid
        execution_tree_root_birth_utc_ticks execution_tree_includes_runner
        inner_runner_pid inner_runner_birth_utc_ticks observed_process_ids
        observed_process_identities cleanup_observed_process_ids
        cleanup_observed_process_identities successful_tree_sample_count
        target_visible_tree_sample_count peak bootstrap_ready_sha256
        start_release_sha256 target_complete_sha256 target_exit_evidence_path
        target_exit_evidence_sha256 exit_release_sha256 handshake_complete
        reported_exit_code actual_exit_code allowed_exit_codes exit_code_allowed
        reported_exit_matches_actual stdout_path stdout_bytes stdout_sha256
        stderr_path stderr_bytes stderr_sha256 monitor_ok
        monitor_ok_before_envelope_close monitor_error stop_reason cleanup_attempted
        cleanup_verified cleanup_identity_complete
        fallback_process_object_cleanup_attempted
        fallback_process_object_root_exit_verified observed_survivors_after_cleanup
        terminal_system_sample_after_verified_termination_or_no_spawn
        mandatory_control_plane_gate_pass
        """.split()
    )
    close_fields = set(
        """
        schema program case_id stage operation invocation_id report_path report_sha256
        preclose_session_index_path preclose_session_index_sha256
        target_argv_json_sha256 process_argv_json_sha256
        attempt_provenance_before_sha256 attempt_provenance_after_sha256
        execution_resource_scope_sha256 execution_resource_scope_binding_semantics
        resource_envelope_includes_report_and_preclose_index_work
        resource_envelope_excluded_tail process_membership_semantics
        simultaneous_current_interval_semantics summed_os_lifetime_peak_semantics
        thresholds pre_spawn_system pre_helper_after_provenance_system
        high_system_floor_recheck_before_and_after
        terminal_system_sample_after_verified_termination_or_no_spawn
        envelope_close_runner_tree_sample final_system peak monitor_ok monitor_error
        stop_reason cleanup_verified started_utc ended_utc wall_seconds
        wall_clock_kind wall_stop_seconds mandatory_control_plane_gate_pass
        authorization_effect consume_after_every_owned_claim_outcome
        external_runner_or_machine_kill_terminal_state_guaranteed
        """.split()
    )
    reference_fields = {
        "operation", "invocation_id", "report_path", "report_sha256",
        "target_argv_json_sha256", "process_argv_json_sha256",
        "attempt_provenance_before_sha256", "attempt_provenance_after_sha256",
        "envelope_close_path", "envelope_close_sha256",
        "mandatory_control_plane_gate_pass",
    }
    allowed_operations = {"preflight", "canonical_json_hash", "finalizer", "token_consumer"}
    validated_references: list[Mapping[str, object]] = []
    for position, reference in enumerate(references):
        reference = _require_exact_fields(
            reference, reference_fields, "terminal control report reference"
        )
        operation = reference.get("operation")
        invocation_id = reference.get("invocation_id")
        if (
            operation not in allowed_operations
            or not isinstance(invocation_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", invocation_id) is None
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control reference identity invalid"
            )
        invocation_dir = session_dir / f"{operation}-{invocation_id}"
        report_path = _owned_control_file(
            reference.get("report_path"),
            label="terminal control report",
            parent=invocation_dir,
            name="control-plane-report.json",
        )
        report, report_sha = _read_stable_json(report_path, "terminal control report")
        _require_exact_fields(report, report_fields, "terminal control report")
        scope_binding_semantics = (
            "candidate_token_hash_untrusted_until_preflight_payload_validation"
            if operation == "preflight"
            else "validated_preflight_execution_scope_hash"
        )
        if (
            report_sha != _require_sha(reference.get("report_sha256"), "control report")
            or report.get("schema") != CONTROL_PLANE_REPORT_SCHEMA
            or report.get("program") != PROGRAM
            or report.get("case_id") != CASE_ID
            or report.get("stage") != "control-plane"
            or report.get("operation") != operation
            or report.get("invocation_id") != invocation_id
            or report.get("report_path") != str(report_path)
            or report.get("runner_path") != str(P1_RUNNER.resolve())
            or report.get("runner_sha256") != manifest["bindings"]["runner_sha256"]
            or report.get("bootstrap_path") != str(bootstrap.resolve())
            or report.get("bootstrap_sha256") != _sha(bootstrap)
            or report.get("execution_resource_scope_sha256")
            != manifest["execution_resource_scope_sha256"]
            or report.get("execution_resource_scope_binding_semantics")
            != scope_binding_semantics
            or report.get("factor_fit_interval_included") is not False
            or report.get("authorization_effect") != "none_control_plane_evidence_only"
            or report.get("resource_envelope_status") != "pending_envelope_close"
            or report.get("report_and_index_required_for_invocation_success") is not True
            or report.get("gate_is_authoritative_only_in_envelope_close") is not True
            or report.get("envelope_close_sha256") is not None
            or report.get("mandatory_control_plane_gate_pass") is not False
            or report.get("final_system") is not None
            or report.get("ended_utc") is not None
            or report.get("wall_seconds") is not None
            or report.get("execution_tree_root_pid") != observer["outer_process_id"]
            or report.get("execution_tree_root_birth_utc_ticks")
            != observer["outer_process_birth_utc_ticks"]
            or report.get("inner_runner_pid") != observer["inner_process_id"]
            or report.get("inner_runner_birth_utc_ticks")
            != observer["inner_process_birth_utc_ticks"]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control report binding mismatch"
            )
        for sequence_key in ("target_argv", "process_argv"):
            if not isinstance(report.get(sequence_key), list) or _ordered_json_sha256(
                report[sequence_key]
            ) != _require_sha(
                report.get(f"{sequence_key}_json_sha256"), sequence_key
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {sequence_key} binding invalid"
                )
        target_script = _owned_control_file(
            report.get("target_script_path"), label="terminal control target script"
        )
        python_path = _owned_control_file(
            report.get("python_path"), label="terminal control Python"
        )
        if (
            _sha(target_script)
            != _require_sha(report.get("target_script_sha256"), "control target")
            or _sha(python_path)
            != _require_sha(report.get("python_sha256"), "control Python")
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control executable hash mismatch"
            )
        close_path = _owned_control_file(
            reference.get("envelope_close_path"),
            label="terminal control envelope close",
            parent=invocation_dir,
            name="control-plane-envelope-close.json",
        )
        close, close_sha = _read_stable_json(
            close_path, "terminal control envelope close"
        )
        _require_exact_fields(close, close_fields, "terminal control envelope close")
        gate = reference.get("mandatory_control_plane_gate_pass")
        if type(gate) is not bool:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control reference gate type invalid"
            )
        close_expected = {
            "schema": CONTROL_PLANE_ENVELOPE_CLOSE_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": "control-plane",
            "operation": operation,
            "invocation_id": invocation_id,
            "report_path": str(report_path),
            "report_sha256": report_sha,
            "target_argv_json_sha256": report["target_argv_json_sha256"],
            "process_argv_json_sha256": report["process_argv_json_sha256"],
            "attempt_provenance_before_sha256": report[
                "attempt_provenance_before_sha256"
            ],
            "attempt_provenance_after_sha256": report[
                "attempt_provenance_after_sha256"
            ],
            "execution_resource_scope_sha256": manifest[
                "execution_resource_scope_sha256"
            ],
            "execution_resource_scope_binding_semantics": (
                scope_binding_semantics
            ),
            "resource_envelope_includes_report_and_preclose_index_work": True,
            "wall_stop_seconds": CONTROL_PLANE_WALL_STOP_SECONDS,
            "mandatory_control_plane_gate_pass": gate,
            "authorization_effect": "bounded_control_plane_evidence_only",
            "consume_after_every_owned_claim_outcome": False,
            "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        }
        for key, expected in close_expected.items():
            if not _strict_json_equal(close.get(key), expected):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal control envelope-close {key} mismatch",
                )
        if close_sha != _require_sha(
            reference.get("envelope_close_sha256"), "control envelope close"
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control close hash mismatch"
            )
        _validate_terminal_control_invocation(
            report=report,
            report_path=report_path,
            report_sha256=report_sha,
            close=close,
            close_path=close_path,
            close_sha256=close_sha,
            reference=reference,
            operation=operation,
            invocation_id=invocation_id,
            invocation_position=position,
            session_dir=session_dir,
            bootstrap=bootstrap,
            canonical_helper=canonical_helper,
            observer=observer,
            manifest=manifest,
            claim=claim,
            require_pass_gate=require_pass_gates,
        )
        if gate:
            for key in (
                "high_system_floor_recheck_before_and_after",
                "terminal_system_sample_after_verified_termination_or_no_spawn",
                "monitor_ok", "cleanup_verified",
            ):
                _require_bool(close.get(key), True, f"terminal control close {key}")
            if close.get("monitor_error") is not None or close.get("stop_reason") is not None:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "passed control close has stop evidence"
                )
        validated_references.append(
            {
                "value": reference,
                "operation": operation,
                "report_path": report_path,
                "report_sha256": report_sha,
                "report": report,
                "close_path": close_path,
                "close_sha256": close_sha,
                "close": close,
                "gate": gate,
            }
        )
    operations = [item["operation"] for item in validated_references]
    if (
        operations[0] != "preflight"
        or operations.count("preflight") != 1
        or operations.count("finalizer") > 1
        or operations.count("token_consumer") != 1
        or operations[-1] != "token_consumer"
        or (
            "finalizer" in operations
            and operations.index("finalizer") > operations.index("token_consumer")
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control operation order invalid"
        )
    if require_pass_gates and (
        operations
        != [
            "preflight",
            "canonical_json_hash",
            "canonical_json_hash",
            "canonical_json_hash",
            "finalizer",
            "token_consumer",
        ]
        or any(item["gate"] is not True for item in validated_references)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "terminal pass control chain is incomplete"
        )
    if require_pass_gates:
        finalizer_provenance = validated_references[4]["report"][
            "attempt_provenance_before"
        ]
        expected_canonical_inputs = [
            finalizer_provenance["review_token"]["path"],
            finalizer_provenance["claim"]["path"],
            finalizer_provenance["guard"]["path"],
        ]
        canonical_references = validated_references[1:4]
        for position, (validated, expected_input) in enumerate(
            zip(canonical_references, expected_canonical_inputs), start=1
        ):
            canonical_row = validated["report"]["attempt_provenance_before"][
                "canonical_input"
            ]
            if (
                validated["report"]["target_argv"] != [
                    str(canonical_helper.resolve()), expected_input
                ]
                or canonical_row.get("path") != expected_input
                or canonical_row.get("exists") is not True
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal pass canonical helper {position} input mismatch",
                )
    previous_final_path: Path | None = None
    previous_final_sha: str | None = None
    first_final_path: Path | None = None
    first_final_sha: str | None = None
    validated_indexes: list[tuple[Path, str]] = []
    for position, validated in enumerate(validated_references):
        preclose_path_for_position = session_dir / f"session-index-{2 * position + 1:04d}.json"
        final_path_for_position = session_dir / f"session-index-{2 * position + 2:04d}.json"
        preclose_value, preclose_hash = _read_stable_json(
            preclose_path_for_position,
            f"terminal control preclose index {position}",
        )
        final_value, final_hash = _read_stable_json(
            final_path_for_position,
            f"terminal control final index {position}",
        )
        _require_exact_fields(
            preclose_value, index_fields, f"terminal control preclose index {position}"
        )
        _require_exact_fields(
            final_value, index_fields, f"terminal control final index {position}"
        )
        final_refs = [item["value"] for item in validated_references[: position + 1]]
        pending_refs = [dict(item) for item in final_refs]
        pending_refs[-1]["envelope_close_path"] = None
        pending_refs[-1]["envelope_close_sha256"] = None
        pending_refs[-1]["mandatory_control_plane_gate_pass"] = False
        scope_semantics = (
            "candidate_token_hash_untrusted_until_preflight_payload_validation"
            if validated["operation"] == "preflight"
            else "validated_preflight_execution_scope_hash"
        )
        common_expected = {
            "schema": CONTROL_PLANE_INDEX_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "session_id": session_id,
            "runner_sha256": manifest["bindings"]["runner_sha256"],
            "bootstrap_sha256": CONTROL_PLANE_BOOTSTRAP_SHA256,
            "canonical_hash_helper_sha256": CONTROL_PLANE_CANONICAL_HASH_HELPER_SHA256,
            "execution_resource_scope_sha256": manifest[
                "execution_resource_scope_sha256"
            ],
            "execution_resource_scope_binding_semantics": scope_semantics,
            "consume_after_every_owned_claim_outcome": False,
            "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        }
        for key, expected in common_expected.items():
            if (
                not _strict_json_equal(preclose_value.get(key), expected)
                or not _strict_json_equal(final_value.get(key), expected)
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal control index {position} {key} mismatch",
                )
        expected_previous_path = (
            None if previous_final_path is None else str(previous_final_path.resolve())
        )
        expected_preclose = {
            "sequence": 2 * position + 1,
            "index_role": "preclose_in_resource_envelope",
            "previous_index_path": expected_previous_path,
            "previous_index_sha256": previous_final_sha,
            "report_references": pending_refs,
            "envelope_close_path": str(validated["close_path"].resolve()),
            "envelope_close_sha256": None,
            "mandatory_control_plane_gate_pass": False,
            "this_index_materialization_inside_resource_envelope": True,
        }
        expected_final = {
            "sequence": 2 * position + 2,
            "index_role": "final_binds_resource_envelope_close",
            "previous_index_path": str(preclose_path_for_position.resolve()),
            "previous_index_sha256": preclose_hash,
            "report_references": final_refs,
            "envelope_close_path": str(validated["close_path"].resolve()),
            "envelope_close_sha256": validated["close_sha256"],
            "mandatory_control_plane_gate_pass": validated["gate"],
            "this_index_materialization_inside_resource_envelope": False,
        }
        for index_value, expected_items, label in (
            (preclose_value, expected_preclose, "preclose"),
            (final_value, expected_final, "final"),
        ):
            for key, expected in expected_items.items():
                if not _strict_json_equal(index_value.get(key), expected):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"terminal control {label} index {position} {key} mismatch",
                    )
        if (
            _sha(preclose_path_for_position) != preclose_hash
            or _sha(final_path_for_position) != final_hash
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control index chain changed"
            )
        validated_indexes.extend(
            [
                (preclose_path_for_position, preclose_hash),
                (final_path_for_position, final_hash),
            ]
        )
        if position == 0:
            first_final_path = final_path_for_position
            first_final_sha = final_hash
        previous_final_path = final_path_for_position
        previous_final_sha = final_hash
    if previous_final_path != path or previous_final_sha != index_sha:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal final index is not chain tip"
        )
    first_reference = validated_references[0]
    if (
        first_reference["report_path"].resolve()
        != (ROOT / claim["control_plane_preflight_report_relative_path"]).resolve()
        or first_reference["report_sha256"]
        != claim["control_plane_preflight_report_sha256"]
        or first_reference["report_path"].parent.parent != session_dir
        or first_final_path is None
        or first_final_sha is None
        or (ROOT / claim["control_plane_preflight_session_index_relative_path"]).resolve()
        != first_final_path.resolve()
        or claim["control_plane_preflight_session_index_sha256"] != first_final_sha
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal preflight report chain mismatch"
        )
    final_reference = validated_references[-1]
    if (
        Path(index.get("envelope_close_path", "")).resolve()
        != final_reference["close_path"]
        or index.get("envelope_close_sha256") != final_reference["close_sha256"]
        or index.get("mandatory_control_plane_gate_pass")
        is not final_reference["gate"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal final index close binding mismatch"
        )
    preclose_path = _owned_control_file(
        index.get("previous_index_path"),
        label="terminal preclose control index",
        parent=session_dir,
        name=f"session-index-{sequence - 1:04d}.json",
    )
    preclose, preclose_sha = _read_stable_json(
        preclose_path, "terminal preclose control index"
    )
    _require_exact_fields(preclose, index_fields, "terminal preclose control index")
    if (
        preclose_sha
        != _require_sha(index.get("previous_index_sha256"), "terminal preclose index")
        or preclose.get("sequence") != sequence - 1
        or preclose.get("index_role") != "preclose_in_resource_envelope"
        or Path(preclose.get("envelope_close_path", "")).resolve()
        != final_reference["close_path"]
        or preclose.get("envelope_close_sha256") is not None
        or preclose.get("mandatory_control_plane_gate_pass") is not False
        or preclose.get("this_index_materialization_inside_resource_envelope") is not True
        or preclose.get("runner_sha256") != index.get("runner_sha256")
        or preclose.get("bootstrap_sha256") != index.get("bootstrap_sha256")
        or preclose.get("canonical_hash_helper_sha256")
        != index.get("canonical_hash_helper_sha256")
        or preclose.get("execution_resource_scope_sha256")
        != index.get("execution_resource_scope_sha256")
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal preclose index mismatch"
        )
    pending_references = preclose.get("report_references")
    if not isinstance(pending_references, list) or len(pending_references) != len(references):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal preclose reference count mismatch"
        )
    for position, (pending, final) in enumerate(zip(pending_references, references)):
        expected_pending = dict(final)
        if position == len(references) - 1:
            expected_pending["envelope_close_path"] = None
            expected_pending["envelope_close_sha256"] = None
            expected_pending["mandatory_control_plane_gate_pass"] = False
        if not _strict_json_equal(pending, expected_pending):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal preclose reference chain mismatch"
            )
    if _sha(path) != index_sha or _sha(preclose_path) != preclose_sha:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal control index changed during validation"
        )
    return {
        "path": path,
        "sha256": index_sha,
        "value": index,
        "references": validated_references,
        "indexes": validated_indexes,
    }


def _validate_terminal_pre_exit(
    complete: Mapping[str, object],
    tombstone_context: Mapping[str, object],
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    tombstone = tombstone_context["value"]
    claim_context = tombstone_context["claim"]
    observer = tombstone_context["observer"]
    identifier = tombstone["consumed_review_token_id"]
    expected_relative = (
        f"validation-output/av-bs1/control-plane-pre-exit-evidence/{identifier}.json"
    )
    if complete.get("pre_exit_evidence_relative_path") != expected_relative:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal pre-exit path mismatch"
        )
    path = _terminal_file(
        expected_relative,
        label="terminal pre-exit evidence",
        pattern=(
            r"validation-output/av-bs1/control-plane-pre-exit-evidence/"
            r"[0-9a-f]{32}\.json"
        ),
    )
    value, value_sha = _read_stable_json(path, "terminal pre-exit evidence")
    _require_exact_fields(
        value, CONTROL_PLANE_PRE_EXIT_EVIDENCE_FIELDS, "terminal pre-exit evidence"
    )
    if value_sha != _require_sha(
        complete.get("pre_exit_evidence_sha256"), "terminal pre-exit evidence"
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal pre-exit hash mismatch"
        )
    intended_code = _json_int(
        value.get("intended_runner_exit_code"), "terminal pre-exit intended code"
    )
    normal_pass = tombstone_context["provisional_pass"]
    fixed = {
        "schema": CONTROL_PLANE_PRE_EXIT_EVIDENCE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorized_stage": AUTHORIZED_STAGE,
        "evidence_kind": "pre_exit_intent_only",
        "review_token_id": identifier,
        "original_review_token_sha256": tombstone[
            "consumed_review_token_sha256"
        ],
        "original_review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "claim_relative_path": claim_context["relative_path"],
        "claim_sha256": claim_context["sha256"],
        "claim_canonical_sha256": claim_context["canonical_sha256"],
        "tombstone_schema": tombstone["schema"],
        "tombstone_relative_path": TOKEN_PATH.resolve().relative_to(ROOT).as_posix(),
        "tombstone_sha256": tombstone_context["sha256"],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": tombstone[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "outer_observer_handshake_prefix": observer["prefix"],
        "outer_observer_handshake_prefix_sha256": observer["prefix_sha256"],
        "attempt_status": tombstone["attempt_status"],
        "runner_exit_observed": False,
        "intended_runner_exit_code": complete["intended_inner_exit_code"],
        "intended_runner_disposition": (
            "success_intent_pending_external_exit_observation"
            if normal_pass and complete["intended_inner_exit_code"] == 0
            else "failure_intent_pending_external_exit_observation"
        ),
        "mandatory_stage_pass": False,
        "authorization_blocker": True,
        "authorization_effect": "none_authorization_remains_blocked",
        "normal_tombstone_authority": (
            "provisional_candidate_only_pending_external_observer"
            if tombstone["schema"] == TOMBSTONE_SCHEMA
            else "not_applicable_emergency_failure_tombstone"
        ),
        "normal_pass_outer_evidence_reconciliation_pass": normal_pass,
        "tombstone_success_remains_provisional": True,
        "pre_exit_evidence_complete": True,
        "terminal_evidence_complete": False,
        "external_observer_required_for_terminal_exit": True,
        "written_before_runner_exit": True,
        "temporary_attempt_evidence_cleanup_observed": False,
        "temporary_attempt_evidence_cleanup_occurs_after_this_record": True,
        "consume_after_every_owned_claim_outcome": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
    }
    for key, expected in fixed.items():
        if not _strict_json_equal(value.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal pre-exit {key} mismatch"
            )
    if intended_code not in (0, 2):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal pre-exit code invalid"
        )
    _strict_outer_utc(value.get("recorded_utc"), "terminal pre-exit recorded UTC")
    errors = value.get("normal_pass_outer_evidence_reconciliation_errors")
    if (
        not isinstance(errors, list)
        or len(errors) > 64
        or any(
            type(item) is not str or not item or len(item) > 4096
            for item in errors
        )
        or errors != list(dict.fromkeys(errors))
        or (normal_pass and errors)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal pre-exit reconciliation errors invalid"
        )
    hash_pairs = (
        ("resource_report_sha256", "consumed_resource_report_sha256"),
        ("result_file_sha256", "consumed_result_file_sha256"),
        ("child_stdout_sha256", "consumed_child_stdout_file_sha256"),
    )
    for pre_key, tomb_key in hash_pairs:
        if value.get(pre_key) != tombstone.get(tomb_key):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal pre-exit {pre_key} mismatch"
            )
    for key in (
        "resource_report_sha256", "result_file_sha256", "child_stdout_sha256",
        "child_stderr_sha256", "factor_prefix_one_sha256",
        "factor_prefix_two_sha256", "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256", "monitor_release_marker_sha256",
    ):
        if value.get(key) != complete.get(key):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal pre-exit/complete {key} mismatch"
            )
        _optional_sha(value.get(key), f"terminal pre-exit {key}")
    if (
        value.get("guard_sha256") != complete.get("guard_sha256")
        or value.get("guard_canonical_sha256")
        != complete.get("guard_canonical_sha256")
        or value.get("guard_sha256") != tombstone.get("guard_contract_sha256")
        or (
            tombstone.get("guard_evidence_valid") is True
            and value.get("guard_canonical_sha256")
            != tombstone.get("guard_canonical_sha256")
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal guard hash reconciliation mismatch"
        )
    final_index = _validate_terminal_control_index(
        value.get("final_session_index_relative_path"),
        value.get("final_session_index_sha256"),
        manifest,
        observer,
        claim_context["value"],
        bool(normal_pass and complete["intended_inner_exit_code"] == 0),
    )
    if normal_pass and complete["intended_inner_exit_code"] == 0:
        expected_canonical_outputs = (
            (
                tombstone["consumed_review_token_sha256"],
                tombstone["consumed_review_token_canonical_sha256"],
            ),
            (claim_context["sha256"], claim_context["canonical_sha256"]),
            (complete["guard_sha256"], complete["guard_canonical_sha256"]),
        )
        canonical_references = final_index["references"][1:4]
        for position, (reference, expected_pair) in enumerate(
            zip(canonical_references, expected_canonical_outputs), start=1
        ):
            provenance = reference["report"]["attempt_provenance_before"][
                "canonical_input"
            ]
            stdout_path = Path(reference["report"]["stdout_path"]).resolve()
            try:
                output_lines = [
                    line
                    for line in stdout_path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
            except OSError as exc:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal canonical helper {position} stdout unreadable",
                ) from exc
            if (
                provenance.get("sha256") != expected_pair[0]
                or output_lines != [expected_pair[1]]
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal canonical helper {position} output mismatch",
                )
        finalizer_reference = final_index["references"][4]
        finalizer_payload, finalizer_payload_sha = _read_wrapper(
            Path(finalizer_reference["report"]["stdout_path"]),
            "terminal finalizer stdout",
        )
        if (
            not _strict_json_equal(finalizer_payload, tombstone.get("result_evidence"))
            or finalizer_payload_sha != tombstone.get("consumed_result_payload_sha256")
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal finalizer stdout/result evidence mismatch",
            )
    if (
        complete.get("final_control_plane_session_index_relative_path")
        != value.get("final_session_index_relative_path")
        or complete.get("final_control_plane_session_index_sha256")
        != final_index["sha256"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal final index reconciliation mismatch"
        )
    report_path_value = value.get("consumer_report_relative_path")
    close_path_value = value.get("consumer_envelope_close_relative_path")
    report_hash_value = value.get("consumer_report_sha256")
    close_hash_value = value.get("consumer_envelope_close_sha256")
    present = [
        report_path_value is not None, close_path_value is not None,
        report_hash_value is not None, close_hash_value is not None,
    ]
    if any(present) and not all(present):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal consumer report null-pair mismatch"
        )
    if not all(present):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "terminal consumer report/close evidence is incomplete",
        )
    consumer_gate = value.get("consumer_control_gate_pass")
    if type(consumer_gate) is not bool:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal consumer gate type invalid"
        )
    report_path = _terminal_file(
        report_path_value,
        label="terminal consumer report",
        pattern=(
            r"validation-output/av-bs1/control-plane/session-[0-9a-f]{32}/"
            r"token_consumer-[0-9a-f]{32}/control-plane-report\.json"
        ),
    )
    close_path = _terminal_file(
        close_path_value,
        label="terminal consumer close",
        pattern=(
            r"validation-output/av-bs1/control-plane/session-[0-9a-f]{32}/"
            r"token_consumer-[0-9a-f]{32}/control-plane-envelope-close\.json"
        ),
    )
    if (
        _sha(report_path) != _require_sha(report_hash_value, "consumer report")
        or _sha(close_path) != _require_sha(close_hash_value, "consumer close")
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal consumer byte binding mismatch"
        )
    matching = [
        ref for ref in final_index["references"]
        if ref["operation"] == "token_consumer"
        and ref["report_path"] == report_path
        and ref["close_path"] == close_path
    ]
    if len(matching) != 1 or matching[0]["gate"] is not consumer_gate:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal consumer index binding mismatch"
        )
    if consumer_gate:
        consumer_payload, _ = _read_wrapper(
            Path(matching[0]["report"]["stdout_path"]),
            "terminal consumer stdout",
        )
        if not _strict_json_equal(consumer_payload, tombstone):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal consumer stdout/tombstone mismatch",
            )
    emergency_fields = (
        "emergency_replacement_intent_relative_path",
        "emergency_replacement_intent_sha256",
        "emergency_replacement_postvalidation_relative_path",
        "emergency_replacement_postvalidation_sha256",
        "emergency_replacement_recovery_relative_path",
        "emergency_replacement_recovery_sha256",
    )
    emergency_values = [value.get(key) for key in emergency_fields]
    if tombstone_context["emergency"]:
        if consumer_gate is not tombstone.get("consumer_control_gate_pass"):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal emergency consumer gate/tombstone mismatch",
            )
        emergency_state = _validate_emergency_replacement_state(
            tombstone_context, require_postvalidated=True
        )
        _require_bool(
            value.get("emergency_replacement_postvalidated"),
            True,
            "terminal emergency replacement postvalidation",
        )
        if any(item is None for item in emergency_values):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal emergency journal binding missing"
            )
        for path_key, sha_key in (
            (emergency_fields[0], emergency_fields[1]),
            (emergency_fields[2], emergency_fields[3]),
            (emergency_fields[4], emergency_fields[5]),
        ):
            journal_path = _terminal_file(
                value.get(path_key),
                label=f"terminal {path_key}",
                pattern=(
                    r"validation-output/av-bs1/emergency-replacement-journals/"
                    r"[0-9a-f]{32}-[0-9a-f]{32}\."
                    + (r"intent\.json" if "intent" in path_key else
                       r"postvalidation\.json" if "postvalidation" in path_key else
                       r"authorized-token-backup\.bin")
                ),
            )
            if _sha(journal_path) != _require_sha(value.get(sha_key), sha_key):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal emergency journal hash mismatch"
                )
        expected_journal_bindings = {
            "emergency_replacement_intent_relative_path": emergency_state[
                "intent_path"
            ].resolve().relative_to(ROOT).as_posix(),
            "emergency_replacement_intent_sha256": emergency_state["intent_sha256"],
            "emergency_replacement_postvalidation_relative_path": emergency_state[
                "postvalidation_path"
            ].resolve().relative_to(ROOT).as_posix(),
            "emergency_replacement_postvalidation_sha256": emergency_state[
                "postvalidation_sha256"
            ],
            "emergency_replacement_recovery_relative_path": emergency_state[
                "recovery_path"
            ].resolve().relative_to(ROOT).as_posix(),
            "emergency_replacement_recovery_sha256": emergency_state[
                "recovery_sha256"
            ],
        }
        for key, expected in expected_journal_bindings.items():
            if value.get(key) != expected:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal emergency {key} does not bind validated journal",
                )
        if value.get("emergency_replacement_recovery_sha256") != tombstone[
            "consumed_review_token_sha256"
        ]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal emergency recovery bytes mismatch"
            )
    else:
        _require_bool(
            value.get("emergency_replacement_postvalidated"),
            False,
            "terminal normal replacement postvalidation",
        )
        if any(item is not None for item in emergency_values):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "normal terminal evidence has emergency journal"
            )
    return {"path": path, "sha256": value_sha, "value": value, "index": final_index}


def _validate_terminal_published_result(
    complete: Mapping[str, object],
    tombstone_context: Mapping[str, object],
    manifest: Mapping[str, object],
) -> Mapping[str, object] | None:
    relative = complete.get("published_result_relative_path")
    expected_sha = complete.get("published_result_sha256")
    if (relative is None) != (expected_sha is None):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "published result path/hash null-pair mismatch"
        )
    if relative is None:
        if tombstone_context["provisional_pass"]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "provisional pass lacks published result"
            )
        return None
    path = _terminal_file(
        relative,
        label="terminal published result",
        pattern=(
            r"validation-output/av-bs1/"
            r"av-bs1-primary-h4-p0r-[0-9]{8}T[0-9]{6}Z\.json"
        ),
    )
    raw_sha = _sha(path)
    if raw_sha != _require_sha(expected_sha, "terminal published result"):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "published result byte hash mismatch"
        )
    tombstone = tombstone_context["value"]
    if tombstone.get("result_evidence_valid") is not True:
        if _sha(path) != raw_sha:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "untrusted published result changed during validation",
            )
        return {
            "path": path,
            "sha256": raw_sha,
            "payload": None,
            "payload_sha256": None,
        }
    payload, payload_sha = _read_wrapper(path, "terminal published result")
    inner_emergency = bool(
        tombstone_context["emergency"]
        and tombstone_context.get("outer_emergency") is not True
    )
    if payload.get("schema") == RESULT_SCHEMA:
        if not inner_emergency:
            for evidence_key in (
                "guard_evidence_valid",
                "result_evidence_valid",
                "resource_evidence_valid",
                "factor_prefix_evidence_valid",
            ):
                _require_bool(
                    tombstone.get(evidence_key),
                    True,
                    f"terminal result-v2 {evidence_key}",
                )
        result_fields = {
            "schema", "program", "case_id", "stage", "status",
            "authorization_state", "token_consumption_required", "git_head",
            "p0r_preregistration_commit", "review_token_id", "review_token_sha256",
            "bindings", "matrix_inputs", "execution_resource_scope_sha256",
            "outer_observer_contract_sha256",
            "outer_observer_handshake_prefix_sha256",
            "expected_terminal_seal_relative_path",
            "terminal_seal_required_for_authoritative_disposition",
            "terminal_seal_state", "terminal_evidence_complete",
            "authoritative_stage_pass", "factor_order", "factor_certificates",
            "factor_prefix_evidence", "factor_sequence_nonoverlap", "numerical",
            "numerical_payload_sha256", "resource", "resource_report_sha256",
            "resource_gate_recheck_failures", "guard_contract_sha256",
            "guard_canonical_sha256", "claim_relative_path", "claim_sha256",
            "claim_canonical_sha256", "child_stdout_sha256", "child_exit_code",
            "mandatory_stage_pass", "failure_codes", "factorization_attempted",
            "factorization_performed", "active_factor", "completed_factors",
            "physics_solve_performed", "forbidden_operation_flags",
            "next_stage_authorized",
        }
        _require_exact_fields(payload, result_fields, "terminal result-v2")
        claim = tombstone_context["claim"]
        fixed = {
            "schema": RESULT_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": AUTHORIZED_STAGE,
            "authorization_state": "claimed_attempt_complete_pending_tombstone",
            "token_consumption_required": True,
            "git_head": tombstone["consumed_git_head"],
            "p0r_preregistration_commit": tombstone["p0r_preregistration_commit"],
            "review_token_id": tombstone["consumed_review_token_id"],
            "review_token_sha256": tombstone["consumed_review_token_sha256"],
            "bindings": manifest["bindings"],
            "matrix_inputs": manifest["p0r_parent"]["matrix_inputs"],
            "execution_resource_scope_sha256": manifest[
                "execution_resource_scope_sha256"
            ],
            "outer_observer_contract_sha256": manifest[
                "outer_observer_contract_sha256"
            ],
            "outer_observer_handshake_prefix_sha256": tombstone[
                "outer_observer_handshake_prefix_sha256"
            ],
            "expected_terminal_seal_relative_path": tombstone[
                "expected_terminal_seal_relative_path"
            ],
            "terminal_seal_required_for_authoritative_disposition": True,
            "terminal_seal_state": "pending_outer_observed_inner_exit",
            "terminal_evidence_complete": False,
            "authoritative_stage_pass": False,
            "claim_relative_path": claim["relative_path"],
            "claim_sha256": claim["sha256"],
            "claim_canonical_sha256": claim["canonical_sha256"],
            "resource_report_sha256": complete["resource_report_sha256"],
            "guard_contract_sha256": complete["guard_sha256"],
            "guard_canonical_sha256": complete["guard_canonical_sha256"],
            "child_stdout_sha256": complete["child_stdout_sha256"],
            "physics_solve_performed": False,
            "forbidden_operation_flags": manifest["forbidden_operations"],
            "next_stage_authorized": False,
        }
        for key, expected in fixed.items():
            if not _strict_json_equal(payload.get(key), expected):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal result-v2 {key} mismatch"
                )
        if not inner_emergency:
            if not _strict_json_equal(payload, tombstone.get("result_evidence")):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "published result/tombstone evidence mismatch",
                )
            if payload_sha != _require_sha(
                tombstone.get("consumed_result_payload_sha256"), "result payload"
            ) or raw_sha != _require_sha(
                tombstone.get("consumed_result_file_sha256"), "result file"
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "published result tombstone hash mismatch"
                )
        elif (
            tombstone.get("result_evidence_valid") is not False
            or tombstone.get("result_evidence") is not None
            or tombstone.get("consumed_result_payload_sha256") is not None
            or raw_sha
            != _require_sha(
                tombstone.get("consumed_result_file_sha256"),
                "inner emergency result file",
            )
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "inner emergency published result binding invalid",
            )
        mandatory = payload.get("mandatory_stage_pass")
        attempted = payload.get("factorization_attempted")
        performed = payload.get("factorization_performed")
        child_exit = payload.get("child_exit_code")
        failure_codes = payload.get("failure_codes")
        resource_failures = payload.get("resource_gate_recheck_failures")
        factor_order = payload.get("factor_order")
        completed = payload.get("completed_factors")
        certificates = payload.get("factor_certificates")
        prefix_evidence = payload.get("factor_prefix_evidence")
        approved_factors = ["A_background_II", "A_conductor_II"]
        if (
            type(mandatory) is not bool
            or (attempted is not None and type(attempted) is not bool)
            or (performed is not None and type(performed) is not bool)
            or performed is True and attempted is not True
            or child_exit is not None
            and (type(child_exit) is not int or child_exit not in (0, 2))
            or not isinstance(failure_codes, list)
            or any(
                type(code) is not str or code not in FAILURE_CODES
                for code in failure_codes
            )
            or failure_codes != list(dict.fromkeys(failure_codes))
            or not isinstance(resource_failures, list)
            or any(type(item) is not str for item in resource_failures)
            or resource_failures != list(dict.fromkeys(resource_failures))
            or not isinstance(factor_order, list)
            or factor_order != approved_factors[: len(factor_order)]
            or not isinstance(completed, list)
            or completed != approved_factors[: len(completed)]
            or not isinstance(certificates, list)
            or len(certificates) > len(factor_order)
            or len(certificates) > 2
            or any(not isinstance(item, Mapping) for item in certificates)
            or not isinstance(prefix_evidence, list)
            or len(prefix_evidence) > 2
            or any(not isinstance(item, Mapping) for item in prefix_evidence)
            or payload.get("active_factor") not in (None, *approved_factors)
            or not isinstance(payload.get("resource"), Mapping)
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal result-v2 factor/resource/failure state invalid",
            )
        numerical = payload.get("numerical")
        if numerical is not None and not isinstance(numerical, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal result-v2 numerical invalid"
            )
        if inner_emergency:
            embedded_resource = payload["resource"]
            resource_fixed = {
                "schema": RESOURCE_SCHEMA,
                "program": PROGRAM,
                "case_id": CASE_ID,
                "stage": AUTHORIZED_STAGE,
                "review_token_id": tombstone["consumed_review_token_id"],
                "review_token_sha256": tombstone["consumed_review_token_sha256"],
                "review_token_canonical_sha256": tombstone[
                    "consumed_review_token_canonical_sha256"
                ],
                "execution_resource_scope_sha256": manifest[
                    "execution_resource_scope_sha256"
                ],
                "claim_sha256": tombstone_context["claim"]["sha256"],
                "claim_canonical_sha256": tombstone_context["claim"][
                    "canonical_sha256"
                ],
                "guard_contract_sha256": complete["guard_sha256"],
                "guard_canonical_sha256": complete["guard_canonical_sha256"],
                "monitor_ready_marker_sha256": complete[
                    "monitor_ready_marker_sha256"
                ],
                "factor_complete_marker_sha256": complete[
                    "factor_complete_marker_sha256"
                ],
                "monitor_release_marker_sha256": complete[
                    "monitor_release_marker_sha256"
                ],
            }
            for key, expected in resource_fixed.items():
                if not _strict_json_equal(embedded_resource.get(key), expected):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"inner emergency result resource {key} mismatch",
                    )
            for position, row in enumerate(prefix_evidence, start=1):
                _require_exact_fields(
                    row,
                    {"count", "file_sha256", "payload_sha256", "payload"},
                    f"inner emergency result prefix {position}",
                )
                prefix_payload = row.get("payload")
                if (
                    _json_int(
                        row.get("count"),
                        f"inner emergency result prefix {position} count",
                    )
                    != position
                    or not isinstance(prefix_payload, Mapping)
                    or _canonical_sha(prefix_payload)
                    != _require_sha(
                        row.get("payload_sha256"),
                        f"inner emergency result prefix {position} payload",
                    )
                    or sha256(h1.canonical_bytes(_wrap(prefix_payload))).hexdigest()
                    != _require_sha(
                        row.get("file_sha256"),
                        f"inner emergency result prefix {position} file",
                    )
                ):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"inner emergency result prefix {position} invalid",
                    )
            if numerical is not None:
                numerical_schema = numerical.get("schema")
                if numerical_schema not in (NUMERICAL_SCHEMA, FAILURE_SCHEMA):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        "inner emergency result numerical schema invalid",
                    )
                monitor_handshake = numerical.get("monitor_handshake")
                numerical_certificates = numerical.get("factor_certificates", [])
                if monitor_handshake is not None:
                    if not isinstance(numerical_certificates, list):
                        raise AvBsError(
                            "BLOCKED_AV_BS_RESULT_SCHEMA",
                            "inner emergency numerical certificates invalid",
                        )
                    _validate_terminal_monitor_handshake_payload(
                        monitor_handshake, numerical_certificates
                    )
        if mandatory:
            if payload.get("status") != SUCCESS_STATUS or failure_codes:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "terminal result-v2 success disposition invalid",
                )
        elif not failure_codes or payload.get("status") != failure_codes[0]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal result-v2 failure disposition invalid",
            )
        numerical_sha = None if numerical is None else _canonical_sha(numerical)
        if payload.get("numerical_payload_sha256") != numerical_sha:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal result-v2 numerical checksum mismatch",
            )
        expected_sequence = True if certificates else None
        if payload.get("factor_sequence_nonoverlap") is not expected_sequence:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal result-v2 factor sequence state invalid",
            )
        if not inner_emergency:
            for result_key, tombstone_key in (
                ("factor_order", "factor_order"),
                ("factor_certificates", "factor_certificates"),
                ("factor_prefix_evidence", "factor_prefix_evidence"),
                ("resource", "resource_evidence"),
                ("resource_gate_recheck_failures", "resource_gate_recheck_failures"),
                ("child_exit_code", "child_exit_code"),
                ("factorization_attempted", "factorization_attempted"),
                ("factorization_performed", "factorization_performed"),
                ("active_factor", "active_factor"),
                ("completed_factors", "completed_factors"),
            ):
                if not _strict_json_equal(
                    payload.get(result_key), tombstone.get(tombstone_key)
                ):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"terminal result-v2/tombstone {result_key} mismatch",
                    )
        if tombstone_context["provisional_pass"]:
            for key in (
                "mandatory_stage_pass", "factorization_attempted",
                "factorization_performed",
            ):
                _require_bool(payload.get(key), True, f"terminal result pass {key}")
            if payload.get("failure_codes") != []:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal result pass has failures"
                )
            pass_fixed = {
                "status": SUCCESS_STATUS,
                "factor_order": ["A_background_II", "A_conductor_II"],
                "factor_certificates": tombstone["factor_certificates"],
                "factor_prefix_evidence": tombstone["factor_prefix_evidence"],
                "factor_sequence_nonoverlap": True,
                "numerical": tombstone["child_stdout_evidence"],
                "numerical_payload_sha256": tombstone[
                    "consumed_child_payload_sha256"
                ],
                "resource": tombstone["resource_evidence"],
                "resource_gate_recheck_failures": [],
                "child_exit_code": 0,
                "active_factor": None,
                "completed_factors": ["A_background_II", "A_conductor_II"],
            }
            for key, expected in pass_fixed.items():
                if not _strict_json_equal(payload.get(key), expected):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"terminal result pass {key} mismatch",
                    )
    elif payload.get("schema") == FAILURE_SCHEMA:
        if tombstone_context["provisional_pass"]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "provisional pass published a failure"
            )
        _validate_failure_payload(
            payload,
            expected_stage="finalize-primary-h4-p0r",
            expected_scope_sha256=manifest["execution_resource_scope_sha256"],
            expected_authorization_state="claimed_attempt_failed",
        )
    else:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "published result schema invalid"
        )
    if _sha(path) != raw_sha:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "published result changed during validation"
        )
    return {"path": path, "sha256": raw_sha, "payload": payload, "payload_sha256": payload_sha}


def _validate_terminal_monitor_markers(
    complete: Mapping[str, object],
    tombstone_context: Mapping[str, object],
    durable_attempt_path: Path | None,
    durable_artifacts: Mapping[str, Mapping[str, object]],
    published_result_context: Mapping[str, object] | None,
) -> Mapping[str, Mapping[str, object]]:
    marker_specs = (
        (
            "monitor_ready_marker",
            "monitor_ready_marker_sha256",
            "monitor-ready.json",
        ),
        (
            "factor_complete_marker",
            "factor_complete_marker_sha256",
            "factor-complete.json",
        ),
        (
            "monitor_release_marker",
            "monitor_release_marker_sha256",
            "monitor-release.json",
        ),
    )
    marker_hashes = [complete.get(hash_key) for _, hash_key, _ in marker_specs]
    marker_presence = [value is not None for value in marker_hashes]
    if marker_presence not in (
        [False, False, False],
        [True, False, False],
        [True, True, False],
        [True, True, True],
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor marker prefix invalid"
        )
    if durable_attempt_path is None:
        if any(marker_presence):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal factor markers lack a durable quarantine",
            )
        return {}
    resolved_attempt = durable_attempt_path.resolve()
    if tombstone_context["value"].get("resource_evidence_valid") is not True:
        return {
            role: durable_artifacts[role]
            for role, _, _ in marker_specs
            if role in durable_artifacts
        }
    marker_contexts: dict[str, Mapping[str, object]] = {}
    for role, hash_key, leaf in marker_specs:
        artifact = durable_artifacts.get(role)
        expected_hash = complete.get(hash_key)
        expected_path = (resolved_attempt / leaf).resolve()
        if expected_path.parent != resolved_attempt:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor marker path escaped"
            )
        if expected_hash is None:
            if artifact is not None or expected_path.is_file():
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal unbound {leaf} is present",
                )
            continue
        if artifact is None or artifact["path"] != expected_path:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {leaf} is missing"
            )
        marker, marker_sha = _read_stable_json(
            expected_path, f"terminal {leaf}"
        )
        if marker_sha != _require_sha(expected_hash, f"terminal {leaf}"):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"terminal {leaf} byte binding mismatch",
            )
        marker_contexts[role] = {
            "path": expected_path,
            "sha256": marker_sha,
            "value": marker,
        }

    if not marker_presence[0]:
        return marker_contexts
    tombstone = tombstone_context["value"]
    claim_context = tombstone_context["claim"]
    child_pid = tombstone.get("child_process_id")
    if (
        tombstone.get("child_launched") is not True
        or type(child_pid) is not int
        or child_pid <= 0
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "terminal factor marker lacks the claimed factor child identity",
        )
    ready = marker_contexts["monitor_ready_marker"]["value"]
    _require_exact_fields(
        ready,
        {
            "schema",
            "claim_sha256",
            "child_process_id",
            "sample_perf_counter_ns",
            "sample_utc",
        },
        "terminal monitor-ready marker",
    )
    if (
        ready.get("schema") != "AV-BS1-h4-p0r-monitor-ready-v1"
        or ready.get("claim_sha256") != claim_context["sha256"]
        or ready.get("child_process_id") != child_pid
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal monitor-ready binding mismatch"
        )
    ready_ns = _json_int(
        ready.get("sample_perf_counter_ns"), "terminal monitor-ready sample"
    )
    ready_utc = _utc(ready.get("sample_utc"), "terminal monitor-ready UTC")
    if ready_ns < 0:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal monitor-ready sample invalid"
        )

    completion_ns: int | None = None
    completion_utc: datetime | None = None
    if marker_presence[1]:
        completion = marker_contexts["factor_complete_marker"]["value"]
        _require_exact_fields(
            completion,
            {
                "schema",
                "claim_sha256",
                "child_process_id",
                "completed_factors",
                "factor_certificates_sha256",
                "completed_perf_counter_ns",
                "completed_utc",
            },
            "terminal factor-complete marker",
        )
        completed_factors = completion.get("completed_factors")
        approved = ["A_background_II", "A_conductor_II"]
        if (
            completion.get("schema") != "AV-BS1-h4-p0r-factor-complete-v1"
            or completion.get("claim_sha256") != claim_context["sha256"]
            or completion.get("child_process_id") != child_pid
            or not isinstance(completed_factors, list)
            or completed_factors != approved[: len(completed_factors)]
            or len(completed_factors) > 2
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal factor-complete binding invalid",
            )
        _require_sha(
            completion.get("factor_certificates_sha256"),
            "terminal factor-complete certificates",
        )
        completion_ns = _json_int(
            completion.get("completed_perf_counter_ns"),
            "terminal factor-complete sample",
        )
        completion_utc = _utc(
            completion.get("completed_utc"), "terminal factor-complete UTC"
        )
        if completion_ns < ready_ns or completion_utc < ready_utc:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal ready/complete marker chronology invalid",
            )

        trusted_certificate_lists: list[list[object]] = []
        child_evidence = tombstone.get("child_stdout_evidence")
        if (
            tombstone.get("child_stdout_evidence_valid") is True
            and isinstance(child_evidence, Mapping)
            and isinstance(child_evidence.get("factor_certificates"), list)
        ):
            trusted_certificate_lists.append(child_evidence["factor_certificates"])
        result_evidence = tombstone.get("result_evidence")
        if (
            tombstone.get("result_evidence_valid") is True
            and isinstance(result_evidence, Mapping)
            and result_evidence.get("schema") == RESULT_SCHEMA
            and isinstance(result_evidence.get("factor_certificates"), list)
        ):
            trusted_certificate_lists.append(result_evidence["factor_certificates"])
        prefix_evidence = tombstone.get("factor_prefix_evidence")
        if (
            tombstone.get("factor_prefix_evidence_valid") is True
            and isinstance(prefix_evidence, list)
            and prefix_evidence
            and isinstance(prefix_evidence[-1], Mapping)
            and isinstance(prefix_evidence[-1].get("payload"), Mapping)
            and isinstance(
                prefix_evidence[-1]["payload"].get("factor_certificates"), list
            )
        ):
            trusted_certificate_lists.append(
                prefix_evidence[-1]["payload"]["factor_certificates"]
            )
        if published_result_context is not None:
            published_payload = published_result_context.get("payload")
            if (
                isinstance(published_payload, Mapping)
                and published_payload.get("schema") == RESULT_SCHEMA
            ):
                published_certificates = published_payload.get(
                    "factor_certificates"
                )
                if isinstance(published_certificates, list):
                    trusted_certificate_lists.append(published_certificates)
        for certificates in trusted_certificate_lists:
            certificate_names = [
                certificate.get("name")
                for certificate in certificates
                if isinstance(certificate, Mapping)
            ]
            if (
                len(certificate_names) != len(certificates)
                or len(certificate_names) > len(completed_factors)
                or certificate_names != completed_factors[: len(certificate_names)]
                or _canonical_sha(certificates)
                != completion.get("factor_certificates_sha256")
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "terminal factor-complete certificate hash mismatch",
                )

    release_ns: int | None = None
    release_utc: datetime | None = None
    if marker_presence[2]:
        release = marker_contexts["monitor_release_marker"]["value"]
        _require_exact_fields(
            release,
            {
                "schema",
                "claim_sha256",
                "completion_marker_sha256",
                "child_process_id",
                "sample_perf_counter_ns",
                "sample_utc",
            },
            "terminal monitor-release marker",
        )
        if (
            release.get("schema") != "AV-BS1-h4-p0r-monitor-release-v1"
            or release.get("claim_sha256") != claim_context["sha256"]
            or release.get("completion_marker_sha256")
            != complete.get("factor_complete_marker_sha256")
            or release.get("child_process_id") != child_pid
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal monitor-release binding invalid",
            )
        release_ns = _json_int(
            release.get("sample_perf_counter_ns"), "terminal monitor-release sample"
        )
        release_utc = _utc(
            release.get("sample_utc"), "terminal monitor-release UTC"
        )
        if (
            completion_ns is None
            or completion_utc is None
            or release_ns < completion_ns
            or release_utc < completion_utc
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal complete/release marker chronology invalid",
            )

    trusted_factor_payloads: list[Mapping[str, object]] = []
    child_evidence = tombstone.get("child_stdout_evidence")
    if tombstone.get("child_stdout_evidence_valid") is True and isinstance(
        child_evidence, Mapping
    ):
        trusted_factor_payloads.append(child_evidence)
    result_evidence = tombstone.get("result_evidence")
    if (
        tombstone.get("result_evidence_valid") is True
        and isinstance(result_evidence, Mapping)
        and result_evidence.get("schema") == RESULT_SCHEMA
    ):
        numerical = result_evidence.get("numerical")
        if isinstance(numerical, Mapping):
            trusted_factor_payloads.append(numerical)
    if published_result_context is not None:
        published_payload = published_result_context.get("payload")
        if (
            isinstance(published_payload, Mapping)
            and published_payload.get("schema") == RESULT_SCHEMA
        ):
            numerical = published_payload.get("numerical")
            if isinstance(numerical, Mapping):
                trusted_factor_payloads.append(numerical)
    for factor_payload in trusted_factor_payloads:
        handshake = factor_payload.get("monitor_handshake")
        if handshake is None:
            continue
        if (
            marker_presence != [True, True, True]
            or completion_ns is None
            or completion_utc is None
            or release_ns is None
            or release_utc is None
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal factor handshake lacks its complete marker prefix",
            )
        ready = marker_contexts["monitor_ready_marker"]["value"]
        completion = marker_contexts["factor_complete_marker"]["value"]
        release = marker_contexts["monitor_release_marker"]["value"]
        expected_handshake = {
            "schema": "AV-BS1-h4-p0r-factor-monitor-handshake-v1",
            "ready_marker_sha256": complete["monitor_ready_marker_sha256"],
            "ready_sample_perf_counter_ns": ready_ns,
            "ready_sample_utc": ready["sample_utc"],
            "completion_marker_sha256": complete[
                "factor_complete_marker_sha256"
            ],
            "completion_perf_counter_ns": completion_ns,
            "completion_utc": completion["completed_utc"],
            "release_marker_sha256": complete["monitor_release_marker_sha256"],
            "release_sample_perf_counter_ns": release_ns,
            "release_sample_utc": release["sample_utc"],
            "factor_interval_bracketed_by_actual_samples": True,
        }
        if not _strict_json_equal(handshake, expected_handshake):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal factor handshake/current marker mismatch",
            )

    return marker_contexts


def _validate_terminal_complete(
    tombstone_context: Mapping[str, object], manifest: Mapping[str, object]
) -> Mapping[str, object]:
    tombstone = tombstone_context["value"]
    claim_context = tombstone_context["claim"]
    observer = tombstone_context["observer"]
    session = observer["session_path"]
    path = session / "inner-complete.json"
    if not path.is_file():
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal inner-complete is missing"
        )
    complete, complete_sha = _read_stable_json(path, "terminal inner-complete")
    _require_exact_fields(
        complete, OUTER_INNER_COMPLETE_FIELDS, "terminal inner-complete"
    )
    intended = _json_int(
        complete.get("intended_inner_exit_code"), "terminal intended inner exit"
    )
    complete_ns = _json_int(
        complete.get("monotonic_ns"), "terminal complete monotonic"
    )
    complete_utc = _strict_outer_utc(
        complete.get("completed_utc"), "terminal complete UTC"
    )
    fixed = {
        "schema": OUTER_INNER_COMPLETE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "internal_mode": "primary-h4-p0r-inner-v1",
        "observer_session_relative_path": observer["session_relative_path"],
        "observer_nonce": observer["nonce"],
        "outer_process_id": observer["outer_process_id"],
        "outer_process_birth_utc_ticks": observer["outer_process_birth_utc_ticks"],
        "inner_process_id": observer["inner_process_id"],
        "inner_process_birth_utc_ticks": observer["inner_process_birth_utc_ticks"],
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "review_token_id": tombstone["consumed_review_token_id"],
        "original_review_token_sha256": tombstone["consumed_review_token_sha256"],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": tombstone[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "inner_ready_relative_path": observer["ready_path"].relative_to(ROOT).as_posix(),
        "inner_ready_sha256": observer["ready_sha256"],
        "outer_start_release_relative_path": observer["start_path"].relative_to(ROOT).as_posix(),
        "outer_start_release_sha256": observer["start_sha256"],
        "outer_observer_handshake_prefix": observer["prefix"],
        "outer_observer_handshake_prefix_sha256": observer["prefix_sha256"],
        "claim_relative_path": claim_context["relative_path"],
        "claim_sha256": claim_context["sha256"],
        "claim_canonical_sha256": claim_context["canonical_sha256"],
        "tombstone_relative_path": TOKEN_PATH.resolve().relative_to(ROOT).as_posix(),
        "tombstone_sha256": tombstone_context["sha256"],
        "tombstone_schema": tombstone["schema"],
        "pre_exit_evidence_schema": CONTROL_PLANE_PRE_EXIT_EVIDENCE_SCHEMA,
        "inner_exit_observed": False,
        "terminal_evidence_complete": False,
        "authorization_blocker": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in fixed.items():
        if not _strict_json_equal(complete.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal inner-complete {key} mismatch"
            )
    if (
        intended not in (0, 2)
        or complete_ns <= observer["start_monotonic_ns"]
        or complete_utc < observer["start_utc"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal complete chronology invalid"
        )
    for key in (
        "claim_sha256", "claim_canonical_sha256", "guard_sha256",
        "guard_canonical_sha256", "pre_exit_evidence_sha256",
        "final_control_plane_session_index_sha256", "tombstone_sha256",
    ):
        _require_sha(complete.get(key), f"terminal complete {key}")
    for key in (
        "resource_report_sha256", "result_file_sha256", "child_stdout_sha256",
        "child_stderr_sha256", "factor_prefix_one_sha256",
        "factor_prefix_two_sha256", "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256", "monitor_release_marker_sha256",
        "published_result_sha256",
    ):
        _optional_sha(complete.get(key), f"terminal complete {key}")
    pre_exit = _validate_terminal_pre_exit(
        complete, tombstone_context, manifest
    )
    if complete.get("guard_sha256") != tombstone.get("guard_contract_sha256"):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal complete guard hash mismatch"
        )
    if tombstone_context["provisional_pass"] and intended == 0:
        if (
            complete.get("provisional_inner_disposition")
            != "provisional_pass_pending_outer_observed_exit"
            or tombstone["schema"] != TOMBSTONE_SCHEMA
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal pass disposition invalid"
            )
        for key in (
            "resource_report_sha256", "result_file_sha256", "child_stdout_sha256",
            "child_stderr_sha256", "factor_prefix_one_sha256",
            "factor_prefix_two_sha256", "monitor_ready_marker_sha256",
            "factor_complete_marker_sha256", "monitor_release_marker_sha256",
            "published_result_relative_path",
            "published_result_sha256",
        ):
            if complete.get(key) is None:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal pass lacks {key}"
                )
    elif intended == 2:
        if complete.get("provisional_inner_disposition") != (
            "provisional_failure_pending_outer_observed_exit"
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal failure disposition invalid"
            )
    else:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "inner reported success without a valid provisional pass tombstone",
        )
    cleanup = complete.get("temporary_attempt_cleanup_disposition")
    attempt_relative = complete.get("attempt_evidence_relative_path")
    durable_attempt_path: Path | None = None
    if cleanup == "removed":
        if attempt_relative is not None:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "removed attempt retains a path"
            )
    elif cleanup == "quarantined":
        expected_attempt = (
            "validation-output/av-bs1/quarantine/"
            f"{tombstone['consumed_review_token_id']}-"
            f"{claim_context['value']['guard_nonce']}"
        )
        if attempt_relative != expected_attempt:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "quarantined attempt path mismatch"
            )
        durable_attempt_path = _terminal_directory(
            attempt_relative,
            label="terminal quarantined attempt",
            pattern=(
                r"validation-output/av-bs1/quarantine/"
                r"[0-9a-f]{32}-[0-9a-f]{32}"
            ),
        )
    else:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal attempt cleanup disposition invalid"
        )
    artifact_specs = {
        "guard": ("guard.json", "guard_sha256"),
        "resource": ("resource.json", "resource_report_sha256"),
        "result": ("final.json", "result_file_sha256"),
        "child_stdout": ("numerical.json", "child_stdout_sha256"),
        "child_stderr": ("child.stderr.txt", "child_stderr_sha256"),
        "factor_prefix_one": ("factor-prefix-1.json", "factor_prefix_one_sha256"),
        "factor_prefix_two": ("factor-prefix-2.json", "factor_prefix_two_sha256"),
        "monitor_ready_marker": ("monitor-ready.json", "monitor_ready_marker_sha256"),
        "factor_complete_marker": (
            "factor-complete.json", "factor_complete_marker_sha256"
        ),
        "monitor_release_marker": (
            "monitor-release.json", "monitor_release_marker_sha256"
        ),
    }
    durable_artifacts: dict[str, Mapping[str, object]] = {}
    if durable_attempt_path is None:
        if tombstone_context["provisional_pass"] or any(
            complete.get(hash_key) is not None for _, hash_key in artifact_specs.values()
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal attempt artifacts were removed before outer validation",
            )
    else:
        for role, (leaf, hash_key) in artifact_specs.items():
            artifact_path = (durable_attempt_path / leaf).resolve()
            expected_hash = complete.get(hash_key)
            if artifact_path.parent != durable_attempt_path.resolve():
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {role} path escaped attempt"
                )
            if artifact_path.is_file():
                actual_hash = _sha(artifact_path)
                if actual_hash != _require_sha(expected_hash, f"terminal {role}"):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {role} byte hash mismatch"
                    )
                durable_artifacts[role] = {
                    "path": artifact_path,
                    "sha256": actual_hash,
                }
            elif expected_hash is not None:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal {role} artifact is missing"
                )
        guard_artifact = durable_artifacts.get("guard")
        if (
            guard_artifact is not None
            and tombstone.get("guard_evidence_valid") is True
        ):
            guard_value = _read(guard_artifact["path"], "terminal durable guard")
            if (
                _canonical_sha(guard_value) != complete.get("guard_canonical_sha256")
                or not _strict_json_equal(guard_value, tombstone.get("guard_evidence"))
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal durable guard evidence mismatch"
                )
        resource_artifact = durable_artifacts.get("resource")
        if (
            resource_artifact is not None
            and tombstone.get("resource_evidence_valid") is True
        ):
            resource_value = _read(
                resource_artifact["path"], "terminal durable resource"
            )
            if not _strict_json_equal(resource_value, tombstone.get("resource_evidence")):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal durable resource evidence mismatch"
                )
        result_artifact = durable_artifacts.get("result")
        if (
            result_artifact is not None
            and tombstone.get("result_evidence_valid") is True
        ):
            result_value, result_payload_sha = _read_wrapper(
                result_artifact["path"], "terminal durable result"
            )
            if (
                not _strict_json_equal(result_value, tombstone.get("result_evidence"))
                or result_payload_sha != tombstone.get("consumed_result_payload_sha256")
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal durable result evidence mismatch"
                )
        child_artifact = durable_artifacts.get("child_stdout")
        if (
            child_artifact is not None
            and tombstone.get("child_stdout_evidence_valid") is True
        ):
            child_value, child_payload_sha = _read_wrapper(
                child_artifact["path"], "terminal durable child stdout"
            )
            if (
                not _strict_json_equal(child_value, tombstone.get("child_stdout_evidence"))
                or child_payload_sha != tombstone.get("consumed_child_payload_sha256")
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal durable child evidence mismatch"
                )
        prefix_evidence = tombstone.get("factor_prefix_evidence")
        if tombstone.get("factor_prefix_evidence_valid") is True:
            for position, role in enumerate(
                ("factor_prefix_one", "factor_prefix_two"), start=1
            ):
                prefix_artifact = durable_artifacts.get(role)
                if prefix_artifact is None:
                    continue
                prefix_value, prefix_payload_sha = _read_wrapper(
                    prefix_artifact["path"], f"terminal durable factor prefix {position}"
                )
                if (
                    not isinstance(prefix_evidence, list)
                    or len(prefix_evidence) < position
                    or not _strict_json_equal(
                        prefix_value, prefix_evidence[position - 1].get("payload")
                    )
                    or prefix_payload_sha
                    != prefix_evidence[position - 1].get("payload_sha256")
                ):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        f"terminal durable factor prefix {position} mismatch",
                    )
    published = _validate_terminal_published_result(
        complete, tombstone_context, manifest
    )
    monitor_markers = _validate_terminal_monitor_markers(
        complete,
        tombstone_context,
        durable_attempt_path,
        durable_artifacts,
        published,
    )
    if published is not None and complete.get("result_file_sha256") != published["sha256"]:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "published/result raw hashes differ"
        )
    if _sha(path) != complete_sha or _sha(TOKEN_PATH) != tombstone_context["sha256"]:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal complete sources changed"
        )
    return {
        "path": path,
        "sha256": complete_sha,
        "value": complete,
        "monotonic_ns": complete_ns,
        "utc": complete_utc,
        "intended_exit_code": intended,
        "pre_exit": pre_exit,
        "published_result": published,
        "durable_artifacts": durable_artifacts,
        "monitor_markers": monitor_markers,
    }


def _validate_terminal_exit_release(
    complete_context: Mapping[str, object],
    tombstone_context: Mapping[str, object],
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    complete = complete_context["value"]
    tombstone = tombstone_context["value"]
    observer = tombstone_context["observer"]
    path = observer["session_path"] / "outer-exit-release.json"
    if not path.is_file():
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal outer-exit-release is missing"
        )
    value, value_sha = _read_stable_json(path, "terminal outer-exit-release")
    _require_exact_fields(value, OUTER_EXIT_RELEASE_FIELDS, "terminal exit release")
    monotonic = _json_int(value.get("monotonic_ns"), "terminal exit monotonic")
    released_utc = _strict_outer_utc(value.get("released_utc"), "terminal exit UTC")
    fixed = {
        "schema": OUTER_EXIT_RELEASE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "observer_session_relative_path": observer["session_relative_path"],
        "observer_nonce": observer["nonce"],
        "outer_process_id": observer["outer_process_id"],
        "outer_process_birth_utc_ticks": observer["outer_process_birth_utc_ticks"],
        "inner_process_id": observer["inner_process_id"],
        "inner_process_birth_utc_ticks": observer["inner_process_birth_utc_ticks"],
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "review_token_id": tombstone["consumed_review_token_id"],
        "original_review_token_sha256": tombstone["consumed_review_token_sha256"],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": tombstone[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "inner_complete_relative_path": complete_context["path"].relative_to(ROOT).as_posix(),
        "inner_complete_sha256": complete_context["sha256"],
        "outer_observer_handshake_prefix_sha256": observer["prefix_sha256"],
        "intended_inner_exit_code": complete_context["intended_exit_code"],
        "release_inner_to_exit": True,
        "outer_resource_gate_pass_before_inner_exit": True,
        "terminal_seal_still_pending": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    for key, expected in fixed.items():
        if not _strict_json_equal(value.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"terminal exit-release {key} mismatch"
            )
    if monotonic <= complete_context["monotonic_ns"] or released_utc < complete_context["utc"]:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal exit-release chronology invalid"
        )
    return {
        "path": path,
        "sha256": value_sha,
        "value": value,
        "monotonic_ns": monotonic,
        "utc": released_utc,
    }


def _validate_outer_resource_envelope_close(
    exit_context: Mapping[str, object],
    complete_context: Mapping[str, object],
    tombstone_context: Mapping[str, object],
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    observer = tombstone_context["observer"]
    tombstone = tombstone_context["value"]
    claim = tombstone_context["claim"]
    complete = complete_context["value"]
    path = observer["session_path"] / "outer-resource-envelope-close.json"
    value, raw_sha = _read_stable_json(path, "outer resource envelope close")
    _require_exact_fields(
        value, OUTER_RESOURCE_ENVELOPE_CLOSE_FIELDS, "outer resource envelope close"
    )
    if path.read_bytes() != h1.canonical_bytes(value):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "outer resource envelope close bytes are not canonical JSON",
        )
    policy = manifest["resource_policy"]
    started_utc = _strict_outer_utc(
        value.get("started_utc"), "outer resource envelope start UTC"
    )
    ended_utc = _strict_outer_utc(
        value.get("ended_utc"), "outer resource envelope end UTC"
    )
    fixed = {
        "schema": OUTER_RESOURCE_ENVELOPE_CLOSE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorization_blocker": True,
        "cleanup_verified": True,
        "execution_tree_root_pid": observer["outer_process_id"],
        "execution_tree_root_birth_utc_ticks": observer[
            "outer_process_birth_utc_ticks"
        ],
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "inner_actual_exit_code": complete_context["intended_exit_code"],
        "inner_complete_relative_path": complete_context["path"]
        .relative_to(ROOT)
        .as_posix(),
        "inner_complete_sha256": complete_context["sha256"],
        "inner_exit_release_relative_path": exit_context["path"]
        .relative_to(ROOT)
        .as_posix(),
        "inner_exit_release_sha256": exit_context["sha256"],
        "inner_process_id": observer["inner_process_id"],
        "inner_process_birth_utc_ticks": observer["inner_process_birth_utc_ticks"],
        "inner_ready_relative_path": observer["ready_path"]
        .relative_to(ROOT)
        .as_posix(),
        "inner_ready_sha256": observer["ready_sha256"],
        "mandatory_outer_resource_gate_pass": True,
        "monitor_error_present": False,
        "observed_lifecycle_scope": (
            "post_dispatch_stopwatch_start_before_candidate_token_capture_through_"
            "inner_actual_exit_verified_cleanup_terminal_stream_hash_and_system_tree_sample"
        ),
        "observer_nonce": observer["nonce"],
        "observer_session_relative_path": observer["session_relative_path"],
        "outer_observer_contract_sha256": manifest["outer_observer_contract_sha256"],
        "post_cleanup_claim_relative_path": claim["relative_path"],
        "post_cleanup_claim_sha256": claim["sha256"],
        "post_cleanup_emergency_replacement_performed": False,
        "post_cleanup_recovery_state": (
            "claim_present_same_attempt_tombstone_validated_by_inner_complete"
        ),
        "post_cleanup_terminal_seal_pending": True,
        "post_cleanup_token_sha256": tombstone_context["sha256"],
        "process_membership_semantics": (
            "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_identity_"
            "bound_descendants_between_samples_not_claimed"
        ),
        "raw_bytes_are_canonical_json": True,
        "simultaneous_current_factor_interval_semantics": (
            "inner_factor_resource_reports_measure_sampled_current_outer_plus_inner_"
            "plus_primary_helper_tree"
        ),
        "stop_reason": None,
        "summed_os_lifetime_peak_semantics": (
            "conservative_stop_gate_includes_outer_inner_preflight_control_and_factor_"
            "work_not_factor_only_peak"
        ),
        "terminal_sample_after_inner_actual_exit_and_verified_cleanup": True,
        "wall_stop_seconds": WALL_STOP_SECONDS,
        "excluded_head": manifest["outer_observer_contract"][
            "outer_resource_envelope"
        ]["excluded_head"],
        "excluded_tail": manifest["outer_observer_contract"][
            "outer_resource_envelope"
        ]["excluded_tail"],
    }
    for key, expected in fixed.items():
        if not _strict_json_equal(value.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"outer resource close {key} mismatch"
            )
    elapsed_ns = _json_int(
        value.get("wall_elapsed_nanoseconds"), "outer resource wall nanoseconds"
    )
    sample_count = _json_int(value.get("sample_count"), "outer resource sample count")
    inner_sample_count = _json_int(
        value.get("inner_visible_sample_count"), "outer inner-visible sample count"
    )
    if (
        started_utc > ended_utc
        or ended_utc < exit_context["utc"]
        or elapsed_ns < 0
        or elapsed_ns > WALL_STOP_SECONDS * 1_000_000_000
        or sample_count < 2
        or inner_sample_count < 2
        or inner_sample_count > sample_count
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "outer resource interval/sample gate invalid"
        )
    thresholds = value.get("thresholds")
    expected_thresholds = {
        "available_physical_floor_bytes": policy["available_physical_floor_bytes"],
        "high_available_physical_floor_bytes": policy[
            "minimum_available_physical_before_spawn_bytes"
        ],
        "high_commit_headroom_floor_bytes": policy[
            "minimum_commit_headroom_before_spawn_bytes"
        ],
        "system_commit_headroom_floor_bytes": policy[
            "commit_headroom_floor_bytes"
        ],
        "tree_commit_stop_bytes": policy["tree_commit_stop_bytes"],
        "tree_private_stop_bytes": policy["tree_private_stop_bytes"],
        "tree_working_set_stop_bytes": policy["tree_working_set_stop_bytes"],
        "wall_stop_seconds": WALL_STOP_SECONDS,
    }
    if not _strict_json_equal(thresholds, expected_thresholds):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "outer resource thresholds mismatch"
        )
    peak = _validate_control_peak(
        value.get("peak"), policy, label="outer resource peak"
    )
    if (
        value.get("available_physical_min_bytes")
        != peak["available_physical_min_bytes"]
        or value.get("commit_headroom_min_bytes")
        != peak["system_commit_headroom_min_bytes"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "outer resource top-level minimum mismatch"
        )
    high_available_keys = (
        "high_pre_available_physical_bytes",
        "high_pre_spawn_available_physical_bytes",
        "high_post_available_physical_bytes",
    )
    high_commit_keys = (
        "high_pre_commit_headroom_bytes",
        "high_pre_spawn_commit_headroom_bytes",
        "high_post_commit_headroom_bytes",
    )
    high_available = [
        _json_int(value.get(key), f"outer resource {key}") for key in high_available_keys
    ]
    high_commit = [
        _json_int(value.get(key), f"outer resource {key}") for key in high_commit_keys
    ]
    if (
        min(high_available)
        < policy["minimum_available_physical_before_spawn_bytes"]
        or min(high_commit) < policy["minimum_commit_headroom_before_spawn_bytes"]
        or peak["available_physical_min_bytes"] > min(high_available)
        or peak["system_commit_headroom_min_bytes"] > min(high_commit)
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", "outer resource high-floor evidence invalid"
        )
    identities = value.get("sampled_process_identities")
    if not isinstance(identities, list) or len(identities) < 2:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer sampled identities invalid"
        )
    parsed_identities: list[tuple[int, int]] = []
    for row in identities:
        _require_exact_fields(
            row, {"birth_utc_ticks", "process_id"}, "outer sampled identity"
        )
        pid = _json_int(row.get("process_id"), "outer sampled process id")
        birth = _json_int(row.get("birth_utc_ticks"), "outer sampled process birth")
        if pid <= 0 or birth <= 0:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "outer sampled identity value invalid"
            )
        parsed_identities.append((pid, birth))
    if parsed_identities != sorted(set(parsed_identities)):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer sampled identities are not unique/sorted"
        )
    required_identities = {
        (observer["outer_process_id"], observer["outer_process_birth_utc_ticks"]),
        (observer["inner_process_id"], observer["inner_process_birth_utc_ticks"]),
    }
    if not required_identities.issubset(set(parsed_identities)):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer/inner identity missing from samples"
        )
    outer_pid = observer["outer_process_id"]
    outer_birth = observer["outer_process_birth_utc_ticks"]
    if any(pid != outer_pid and birth < outer_birth for pid, birth in parsed_identities):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "outer sampled descendant identity predates execution-tree root",
        )
    stdout_path = observer["session_path"] / "inner.stdout.txt"
    stderr_path = observer["session_path"] / "inner.stderr.txt"
    stream_context: dict[str, Mapping[str, object]] = {}
    for name, stream_path in (("stdout", stdout_path), ("stderr", stderr_path)):
        if not stream_path.is_file():
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"outer inner {name} is missing"
            )
        size = _json_int(value.get(f"inner_{name}_bytes"), f"outer inner {name} bytes")
        stream_sha = _require_sha(
            value.get(f"inner_{name}_sha256"), f"outer inner {name}"
        )
        if size < 0 or size > 16 * 1024**2 or stream_path.stat().st_size != size:
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", f"outer inner {name} size gate invalid"
            )
        if _sha(stream_path) != stream_sha:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"outer inner {name} hash mismatch"
            )
        stream_context[name] = {
            "path": stream_path,
            "bytes": size,
            "sha256": stream_sha,
        }
    if (
        _sha(path) != raw_sha
        or _sha(complete_context["path"]) != complete_context["sha256"]
        or _sha(exit_context["path"]) != exit_context["sha256"]
        or _sha(TOKEN_PATH) != tombstone_context["sha256"]
        or _sha(claim["path"]) != claim["sha256"]
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer resource close sources changed"
        )
    return {
        "path": path,
        "sha256": raw_sha,
        "canonical_sha256": _canonical_sha(value),
        "value": value,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "streams": stream_context,
    }


def _validate_terminal_factor_payload(
    payload: Mapping[str, object],
    historical_token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim_context: Mapping[str, object],
    guard: Mapping[str, object],
    resource: Mapping[str, object] | None,
    terminal_bindings: Mapping[str, object],
) -> None:
    claim = claim_context["value"]
    historical_guard_path = claim_context["path"].with_name("historical-guard.json")
    if payload.get("schema") == NUMERICAL_SCHEMA:
        _validate_factor_report(
            payload,
            historical_token,
            manifest,
            claim,
            claim_context["path"],
            guard,
            historical_guard_path,
            validate_prefix_sidecars=False,
            terminal_bindings=terminal_bindings,
        )
    elif payload.get("schema") == FAILURE_SCHEMA:
        _validate_failure_payload(
            payload,
            expected_stage=AUTHORIZED_STAGE,
            expected_scope_sha256=manifest["execution_resource_scope_sha256"],
        )
        _validate_partial_factor_certificates(
            payload,
            historical_token,
            manifest,
            claim,
            claim_context["path"],
            guard,
            historical_guard_path,
            terminal_bindings=terminal_bindings,
        )
    else:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor payload schema invalid"
        )
    if resource is not None:
        _validate_factor_monitor_interval(payload, resource)


def _validate_terminal_embedded_evidence(
    complete_context: Mapping[str, object],
    tombstone_context: Mapping[str, object],
    manifest: Mapping[str, object],
) -> None:
    complete = complete_context["value"]
    tombstone = tombstone_context["value"]
    guard = tombstone.get("guard_evidence")
    if tombstone.get("guard_evidence_valid") is True:
        if not isinstance(guard, Mapping) or _canonical_sha(guard) != _require_sha(
            tombstone.get("guard_canonical_sha256"), "terminal embedded guard"
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal embedded guard mismatch"
            )
    resource = tombstone.get("resource_evidence")
    result = tombstone.get("result_evidence")
    child = tombstone.get("child_stdout_evidence")
    if tombstone.get("resource_evidence_valid") is True and not isinstance(
        resource, Mapping
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal embedded resource missing"
        )
    if tombstone.get("resource_evidence_valid") is True and isinstance(
        resource, Mapping
    ):
        for marker_key in (
            "monitor_ready_marker_sha256",
            "factor_complete_marker_sha256",
            "monitor_release_marker_sha256",
        ):
            if resource.get(marker_key) != complete.get(marker_key):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal resource/{marker_key} binding mismatch",
                )
    if tombstone.get("result_evidence_valid") is True:
        if not isinstance(result, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal embedded result missing"
            )
        if (
            result.get("schema") == RESULT_SCHEMA
            and not _strict_json_equal(result.get("resource"), resource)
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal result/resource evidence mismatch"
            )
    if tombstone.get("child_stdout_evidence_valid") is True:
        if not isinstance(child, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal embedded child evidence missing"
            )
        if (
            isinstance(result, Mapping)
            and result.get("schema") == RESULT_SCHEMA
            and not _strict_json_equal(result.get("numerical"), child)
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal result/child evidence mismatch"
            )
        if _canonical_sha(child) != _require_sha(
            tombstone.get("consumed_child_payload_sha256"),
            "terminal embedded child payload",
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal embedded child payload checksum mismatch",
            )
    claim_context = tombstone_context["claim"]
    claim = claim_context["value"]
    if tombstone.get("guard_evidence_valid") is True and isinstance(guard, Mapping):
        policy = manifest["resource_policy"]
        guard_fixed = {
            "schema": GUARD_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": AUTHORIZED_STAGE,
            "nonce": claim["guard_nonce"],
            "review_token_id": tombstone["consumed_review_token_id"],
            "review_token_sha256": tombstone["consumed_review_token_sha256"],
            "review_token_canonical_sha256": tombstone[
                "consumed_review_token_canonical_sha256"
            ],
            "fixture_sha256": manifest["bindings"]["fixture_sha256"],
            "runner_sha256": manifest["bindings"]["runner_sha256"],
            "claim_relative_path": claim_context["relative_path"],
            "claim_sha256": claim_context["sha256"],
            "claim_canonical_sha256": claim_context["canonical_sha256"],
            "preflight_payload_sha256": claim["preflight_payload_sha256"],
            "resource_guard_policy_sha256": manifest["resource_policy_sha256"],
            "execution_resource_scope_sha256": manifest[
                "execution_resource_scope_sha256"
            ],
            "parent_pid": claim["parent_pid"],
            "parent_pid_birth_utc_ticks": claim["parent_pid_birth_utc_ticks"],
            "poll_interval_ms": 100,
            "wall_stop_seconds": policy["wall_stop_seconds"],
            "tree_ws_stop_bytes": policy["tree_working_set_stop_bytes"],
            "tree_private_stop_bytes": policy["tree_private_stop_bytes"],
            "tree_commit_stop_bytes": policy["tree_commit_stop_bytes"],
            "commit_headroom_floor_bytes": policy["commit_headroom_floor_bytes"],
            "available_physical_floor_bytes": policy[
                "available_physical_floor_bytes"
            ],
            "minimum_commit_headroom_before_spawn_bytes": policy[
                "minimum_commit_headroom_before_spawn_bytes"
            ],
            "minimum_available_physical_before_spawn_bytes": policy[
                "minimum_available_physical_before_spawn_bytes"
            ],
            "pre_spawn_resource_gate_pass": True,
        }
        _require_exact_fields(
            guard,
            set(guard_fixed)
            | {
                "monitor_ok", "baseline_commit_headroom_bytes",
                "baseline_available_physical_bytes",
            },
            "terminal embedded guard",
        )
        for key, expected in guard_fixed.items():
            if not _strict_json_equal(guard.get(key), expected):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal embedded guard {key} mismatch",
                )
        if (
            guard.get("monitor_ok") is not True
            or _json_int(
                guard.get("baseline_commit_headroom_bytes"),
                "terminal guard baseline commit headroom",
            )
            < policy["minimum_commit_headroom_before_spawn_bytes"]
            or _json_int(
                guard.get("baseline_available_physical_bytes"),
                "terminal guard baseline physical",
            )
            < policy["minimum_available_physical_before_spawn_bytes"]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", "terminal embedded guard resource gate failed"
            )
    historical_token = {
        "review_token_id": tombstone["consumed_review_token_id"],
        "p0r_preregistration_commit": tombstone["p0r_preregistration_commit"],
    }
    terminal_bindings = {
        "observer": tombstone_context["observer"],
        "review_token_sha256": tombstone["consumed_review_token_sha256"],
        "review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "git_head": tombstone["consumed_git_head"],
        "guard_sha256": tombstone.get("guard_contract_sha256"),
        "guard_canonical_sha256": tombstone.get("guard_canonical_sha256"),
        "claim_sha256": claim_context["sha256"],
        "claim_canonical_sha256": claim_context["canonical_sha256"],
        "factor_prefix_1_sha256": complete.get("factor_prefix_one_sha256"),
        "factor_prefix_2_sha256": complete.get("factor_prefix_two_sha256"),
    }
    if (
        tombstone.get("resource_evidence_valid") is True
        and isinstance(resource, Mapping)
        and isinstance(guard, Mapping)
    ):
        resource_pass, resource_failures = _resource_gate(
            resource,
            historical_token,
            manifest,
            guard,
            claim,
            guard_path=claim_context["path"].with_name("historical-guard.json"),
            claim_path=claim_context["path"],
            terminal_bindings=terminal_bindings,
        )
        if (
            resource_pass is not tombstone.get("resource_gate_pass")
            or not _strict_json_equal(
                resource_failures, tombstone.get("resource_gate_recheck_failures")
            )
            or resource.get("child_stdout_sha256")
            != tombstone.get("consumed_child_stdout_file_sha256")
            or resource.get("child_stderr_sha256")
            != complete.get("child_stderr_sha256")
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal embedded resource gate reconciliation failed",
            )
    if tombstone.get("child_stdout_evidence_valid") is True:
        if not isinstance(child, Mapping) or not isinstance(guard, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal child evidence lacks strict guard context",
            )
        _validate_terminal_factor_payload(
            child,
            historical_token,
            manifest,
            claim_context,
            guard,
            (
                resource
                if tombstone.get("resource_evidence_valid") is True
                and isinstance(resource, Mapping)
                else None
            ),
            terminal_bindings,
        )
    result_numerical = result.get("numerical") if isinstance(result, Mapping) else None
    if (
        tombstone.get("result_evidence_valid") is True
        and isinstance(result_numerical, Mapping)
        and tombstone.get("child_stdout_evidence_valid") is not True
    ):
        if not isinstance(guard, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal result numerical evidence lacks guard context",
            )
        _validate_terminal_factor_payload(
            result_numerical,
            historical_token,
            manifest,
            claim_context,
            guard,
            (
                resource
                if tombstone.get("resource_evidence_valid") is True
                and isinstance(resource, Mapping)
                else None
            ),
            terminal_bindings,
        )
    if tombstone_context["provisional_pass"] and (
        not isinstance(child, Mapping) or child.get("schema") != NUMERICAL_SCHEMA
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "terminal provisional pass lacks complete factor evidence",
        )
    prefix = tombstone.get("factor_prefix_evidence")
    if tombstone.get("factor_prefix_evidence_valid") is True:
        if not isinstance(prefix, list):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor prefix evidence missing"
            )
        for position, row in enumerate(prefix, start=1):
            _require_exact_fields(
                row,
                {"count", "file_sha256", "payload_sha256", "payload"},
                f"terminal factor prefix {position}",
            )
            if (
                _json_int(row.get("count"), f"terminal factor prefix {position} count")
                != position
                or not isinstance(row.get("payload"), Mapping)
                or _canonical_sha(row["payload"])
                != _require_sha(
                    row.get("payload_sha256"),
                    f"terminal factor prefix {position} payload",
                )
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal factor prefix {position} evidence invalid",
                )
            if sha256(h1.canonical_bytes(_wrap(row["payload"]))).hexdigest() != _require_sha(
                row.get("file_sha256"), f"terminal factor prefix {position} file"
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal factor prefix {position} wrapper checksum mismatch",
                )
            expected_complete_hash = complete.get(f"factor_prefix_{'one' if position == 1 else 'two'}_sha256")
            if row.get("file_sha256") != expected_complete_hash:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"terminal factor prefix {position} byte hash mismatch",
                )
            if not isinstance(guard, Mapping):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal prefix lacks guard evidence"
                )
            _validate_factor_report(
                row["payload"],
                historical_token,
                manifest,
                claim,
                claim_context["path"],
                guard,
                claim_context["path"].with_name("historical-guard.json"),
                expected_count=position,
                expected_status="factor_certificate_prefix_checkpoint",
                require_handshake=False,
                validate_prefix_sidecars=False,
                terminal_bindings=terminal_bindings,
            )
        prefix_certificates = (
            [] if not prefix else list(prefix[-1]["payload"]["factor_certificates"])
        )
        prefix_order = [] if not prefix else list(prefix[-1]["payload"]["factor_order"])
        _validate_prefix_consistency(
            child if isinstance(child, Mapping) else None,
            prefix_certificates,
            prefix_order,
        )


def _validate_outer_terminal_seal(
    close_context: Mapping[str, object],
    exit_context: Mapping[str, object],
    complete_context: Mapping[str, object],
    tombstone_context: Mapping[str, object],
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    tombstone = tombstone_context["value"]
    observer = tombstone_context["observer"]
    complete = complete_context["value"]
    pre_exit = complete_context["pre_exit"]
    final_index = pre_exit["index"]
    expected_relative = tombstone["expected_terminal_seal_relative_path"]
    path = _terminal_file(
        expected_relative,
        label="outer terminal seal",
        pattern=(
            r"validation-output/av-bs1/outer-observer-terminal-seals/"
            r"[0-9a-f]{32}\.json"
        ),
    )
    seal, seal_sha = _read_stable_json(path, "outer terminal seal")
    _require_exact_fields(seal, OUTER_TERMINAL_SEAL_FIELDS, "outer terminal seal")
    actual_exit = _json_int(
        seal.get("inner_actual_exit_code"), "outer seal actual inner exit"
    )
    reported_exit = _json_int(
        seal.get("inner_reported_exit_code"), "outer seal reported inner exit"
    )
    result_context = complete_context["published_result"]
    result_payload = (
        result_context.get("payload")
        if isinstance(result_context, Mapping)
        else None
    )
    result_v2_provisional_pass = bool(
        isinstance(result_payload, Mapping)
        and result_payload.get("schema") == RESULT_SCHEMA
        and result_payload.get("mandatory_stage_pass") is True
        and result_payload.get("authoritative_stage_pass") is False
        and result_payload.get("terminal_evidence_complete") is False
    )
    authoritative_pass = bool(
        actual_exit == 0
        and reported_exit == 0
        and tombstone["schema"] == TOMBSTONE_SCHEMA
        and tombstone_context["provisional_pass"]
        and result_v2_provisional_pass
    )
    disposition = (
        "authoritative_inner_pass_observed"
        if actual_exit == 0
        else "authoritative_inner_failure_observed"
    )
    fixed = {
        "schema": OUTER_TERMINAL_SEAL_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "seal_kind": "outer_observer_retained_inner_actual_exit",
        "review_token_id": tombstone["consumed_review_token_id"],
        "original_review_token_sha256": tombstone["consumed_review_token_sha256"],
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "outer_observer_contract_sha256": manifest["outer_observer_contract_sha256"],
        "expected_terminal_seal_relative_path": expected_relative,
        "terminal_seal_required_for_authoritative_disposition": True,
        "observer_session_relative_path": observer["session_relative_path"],
        "observer_nonce": observer["nonce"],
        "outer_process_id": observer["outer_process_id"],
        "outer_process_birth_utc_ticks": observer["outer_process_birth_utc_ticks"],
        "inner_process_id": observer["inner_process_id"],
        "inner_process_birth_utc_ticks": observer["inner_process_birth_utc_ticks"],
        "retained_inner_process_handle_acquired": True,
        "inner_ready_relative_path": observer["ready_path"].relative_to(ROOT).as_posix(),
        "inner_ready_sha256": observer["ready_sha256"],
        "outer_start_release_relative_path": observer["start_path"]
        .relative_to(ROOT)
        .as_posix(),
        "outer_start_release_sha256": observer["start_sha256"],
        "outer_observer_handshake_prefix": observer["prefix"],
        "outer_observer_handshake_prefix_sha256": observer["prefix_sha256"],
        "artifact_hash_source": (
            "current_inner_complete_exact_matches_validated_pre_exit_evidence"
        ),
        "claim_relative_path": tombstone_context["claim"]["relative_path"],
        "claim_sha256": tombstone_context["claim"]["sha256"],
        "claim_canonical_sha256": tombstone_context["claim"]["canonical_sha256"],
        "guard_sha256": complete["guard_sha256"],
        "guard_canonical_sha256": complete["guard_canonical_sha256"],
        "resource_report_sha256": complete["resource_report_sha256"],
        "result_file_sha256": complete["result_file_sha256"],
        "child_stdout_sha256": complete["child_stdout_sha256"],
        "child_stderr_sha256": complete["child_stderr_sha256"],
        "factor_prefix_one_sha256": complete["factor_prefix_one_sha256"],
        "factor_prefix_two_sha256": complete["factor_prefix_two_sha256"],
        "monitor_ready_marker_sha256": complete["monitor_ready_marker_sha256"],
        "factor_complete_marker_sha256": complete["factor_complete_marker_sha256"],
        "monitor_release_marker_sha256": complete["monitor_release_marker_sha256"],
        "published_result_relative_path": complete["published_result_relative_path"],
        "published_result_sha256": complete["published_result_sha256"],
        "inner_complete_relative_path": complete_context["path"]
        .relative_to(ROOT)
        .as_posix(),
        "inner_complete_sha256": complete_context["sha256"],
        "outer_exit_release_relative_path": exit_context["path"]
        .relative_to(ROOT)
        .as_posix(),
        "outer_exit_release_sha256": exit_context["sha256"],
        "inner_stdout_relative_path": close_context["streams"]["stdout"]["path"]
        .relative_to(ROOT)
        .as_posix(),
        "inner_stdout_bytes": close_context["streams"]["stdout"]["bytes"],
        "inner_stdout_sha256": close_context["streams"]["stdout"]["sha256"],
        "inner_stderr_relative_path": close_context["streams"]["stderr"]["path"]
        .relative_to(ROOT)
        .as_posix(),
        "inner_stderr_bytes": close_context["streams"]["stderr"]["bytes"],
        "inner_stderr_sha256": close_context["streams"]["stderr"]["sha256"],
        "outer_resource_envelope_close_schema": OUTER_RESOURCE_ENVELOPE_CLOSE_SCHEMA,
        "outer_resource_envelope_close_relative_path": close_context["path"]
        .relative_to(ROOT)
        .as_posix(),
        "outer_resource_envelope_close_raw_sha256": close_context["sha256"],
        "outer_resource_envelope_close_canonical_sha256": close_context[
            "canonical_sha256"
        ],
        "outer_resource_envelope_mandatory_gate_pass": True,
        "tombstone_relative_path": TOKEN_PATH.resolve().relative_to(ROOT).as_posix(),
        "tombstone_sha256": tombstone_context["sha256"],
        "tombstone_schema": tombstone["schema"],
        "pre_exit_evidence_relative_path": pre_exit["path"]
        .relative_to(ROOT)
        .as_posix(),
        "pre_exit_evidence_sha256": pre_exit["sha256"],
        "final_control_plane_session_index_relative_path": final_index["path"]
        .relative_to(ROOT)
        .as_posix(),
        "final_control_plane_session_index_sha256": final_index["sha256"],
        "inner_reported_exit_code": complete_context["intended_exit_code"],
        "inner_actual_exit_code": complete_context["intended_exit_code"],
        "inner_exit_observed": True,
        "inner_exit_code_matches_completion": True,
        "authoritative_inner_disposition": disposition,
        "terminal_evidence_complete": True,
        "tombstone_success_was_provisional_without_this_seal": tombstone_context[
            "provisional_pass"
        ],
        "seal_observes": "inner_runner_actual_exit_only",
        "seal_materialization_and_readback_excluded_from_outer_resource_envelope": True,
        "outer_process_exit_observed": False,
        "seal_written_before_outer_exit": True,
        "authorization_blocker": True,
        "authorization_effect": (
            "authoritative_inner_disposition_only_no_h4_p1_or_next_stage_authorization"
        ),
        "next_stage_authorized": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "authoritative_stage_pass": authoritative_pass,
        "supersedes_provisional_tombstone_and_any_present_result_disposition": True,
    }
    for key, expected in fixed.items():
        if not _strict_json_equal(seal.get(key), expected):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"outer terminal seal {key} mismatch"
            )
    if actual_exit not in (0, 2) or reported_exit != actual_exit:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer terminal seal exit code invalid"
        )
    sealed_utc = _strict_outer_utc(seal.get("sealed_utc"), "outer seal UTC")
    if sealed_utc < exit_context["utc"] or sealed_utc < close_context["ended_utc"]:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer terminal seal chronology invalid"
        )
    if authoritative_pass:
        for key in (
            "claim_sha256", "claim_canonical_sha256", "guard_sha256",
            "guard_canonical_sha256", "resource_report_sha256", "result_file_sha256",
            "child_stdout_sha256", "child_stderr_sha256", "factor_prefix_one_sha256",
            "factor_prefix_two_sha256", "monitor_ready_marker_sha256",
            "factor_complete_marker_sha256", "monitor_release_marker_sha256",
            "published_result_sha256",
        ):
            _require_sha(seal.get(key), f"outer pass seal {key}")
    _validate_terminal_embedded_evidence(
        complete_context, tombstone_context, manifest
    )
    source_bindings: list[tuple[Path, str, str]] = [
        (observer["ready_path"], observer["ready_sha256"], "inner-ready"),
        (observer["start_path"], observer["start_sha256"], "start-release"),
        (complete_context["path"], complete_context["sha256"], "inner-complete"),
        (exit_context["path"], exit_context["sha256"], "exit-release"),
        (close_context["path"], close_context["sha256"], "outer close"),
        (TOKEN_PATH, tombstone_context["sha256"], "tombstone"),
        (
            tombstone_context["claim"]["path"],
            tombstone_context["claim"]["sha256"],
            "claim",
        ),
        (pre_exit["path"], pre_exit["sha256"], "pre-exit"),
        (final_index["path"], final_index["sha256"], "final control index"),
        (
            close_context["streams"]["stdout"]["path"],
            close_context["streams"]["stdout"]["sha256"],
            "inner stdout",
        ),
        (
            close_context["streams"]["stderr"]["path"],
            close_context["streams"]["stderr"]["sha256"],
            "inner stderr",
        ),
    ]
    if result_context is not None:
        source_bindings.append(
            (result_context["path"], result_context["sha256"], "published result")
        )
    for role, artifact in complete_context["durable_artifacts"].items():
        source_bindings.append(
            (artifact["path"], artifact["sha256"], f"durable attempt {role}")
        )
    for index_position, (index_path, index_sha) in enumerate(final_index["indexes"]):
        source_bindings.append(
            (index_path, index_sha, f"control index chain item {index_position}")
        )
    for reference_position, reference in enumerate(final_index["references"]):
        source_bindings.extend(
            [
                (
                    reference["report_path"],
                    reference["report_sha256"],
                    f"control report {reference_position}",
                ),
                (
                    reference["close_path"],
                    reference["close_sha256"],
                    f"control close {reference_position}",
                ),
            ]
        )
        report_value = reference["report"]
        invocation_dir = reference["report_path"].parent
        for artifact_path, digest, label in (
            (
                Path(report_value["stdout_path"]).resolve(),
                report_value.get("stdout_sha256"),
                "stdout",
            ),
            (
                Path(report_value["stderr_path"]).resolve(),
                report_value.get("stderr_sha256"),
                "stderr",
            ),
            (
                invocation_dir / "bootstrap-ready.json",
                report_value.get("bootstrap_ready_sha256"),
                "bootstrap-ready",
            ),
            (
                invocation_dir / "start-release.json",
                report_value.get("start_release_sha256"),
                "start-release",
            ),
            (
                invocation_dir / "target-complete.json",
                report_value.get("target_complete_sha256"),
                "target-complete",
            ),
            (
                invocation_dir / "exit-release.json",
                report_value.get("exit_release_sha256"),
                "exit-release",
            ),
        ):
            if digest is not None:
                source_bindings.append(
                    (
                        artifact_path.resolve(),
                        _require_sha(digest, f"control {label}"),
                        f"control {reference_position} {label}",
                    )
                )
    if tombstone_context["emergency"]:
        emergency_state = _validate_emergency_replacement_state(
            tombstone_context, require_postvalidated=True
        )
        source_bindings.extend(
            [
                (
                    emergency_state["intent_path"],
                    emergency_state["intent_sha256"],
                    "emergency intent",
                ),
                (
                    emergency_state["postvalidation_path"],
                    emergency_state["postvalidation_sha256"],
                    "emergency postvalidation",
                ),
                (
                    emergency_state["recovery_path"],
                    emergency_state["recovery_sha256"],
                    "emergency recovery bytes",
                ),
            ]
        )
    claim_value = tombstone_context["claim"]["value"]
    for path_key, sha_key, label in (
        (
            "control_plane_preflight_report_relative_path",
            "control_plane_preflight_report_sha256",
            "preflight report",
        ),
        (
            "control_plane_preflight_session_index_relative_path",
            "control_plane_preflight_session_index_sha256",
            "preflight index",
        ),
    ):
        source_bindings.append(
            (
                (ROOT / claim_value[path_key]).resolve(),
                claim_value[sha_key],
                label,
            )
        )
    for source_path, expected_sha, label in source_bindings:
        if not source_path.is_file() or _sha(source_path) != expected_sha:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"outer seal {label} source changed"
            )
    if (
        _sha(path) != seal_sha
        or close_context["path"].read_bytes()
        != h1.canonical_bytes(close_context["value"])
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "outer terminal seal/readback source changed"
        )
    return {
        "path": path,
        "sha256": seal_sha,
        "value": seal,
        "authoritative_stage_pass": authoritative_pass,
        "authoritative_inner_disposition": "pass" if authoritative_pass else "failure",
    }


def _validate_outer_terminal_evidence(
    tombstone: Mapping[str, object],
    tombstone_sha256: str,
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    tombstone_context = _validate_consumed_tombstone(
        tombstone, tombstone_sha256, manifest
    )
    if tombstone_context.get("outer_emergency") is True:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "outer-observer recovery tombstone is nonauthoritative without a later inner completion chain",
        )
    complete_context = _validate_terminal_complete(tombstone_context, manifest)
    exit_context = _validate_terminal_exit_release(
        complete_context, tombstone_context, manifest
    )
    close_context = _validate_outer_resource_envelope_close(
        exit_context, complete_context, tombstone_context, manifest
    )
    seal_context = _validate_outer_terminal_seal(
        close_context,
        exit_context,
        complete_context,
        tombstone_context,
        manifest,
    )
    return {
        "tombstone": tombstone_context,
        "complete": complete_context,
        "exit_release": exit_context,
        "outer_close": close_context,
        "seal": seal_context,
    }


def _terminal_classification_record(
    *,
    classification: str,
    authoritative_terminal_evidence: bool,
    authoritative_stage_pass: bool,
    authoritative_inner_disposition: str | None,
    current_token_schema: str | None,
    current_token_sha256: str | None,
    terminal_seal_present: bool,
    terminal_seal_relative_path: str | None,
    terminal_seal_sha256: str | None,
    validation_error_code: str | None,
) -> Mapping[str, object]:
    return {
        "schema": OUTER_TERMINAL_CLASSIFICATION_SCHEMA,
        "classification": classification,
        "authoritative_terminal_evidence": authoritative_terminal_evidence,
        "authoritative_stage_pass": authoritative_stage_pass,
        "authoritative_inner_disposition": authoritative_inner_disposition,
        "current_token_schema": current_token_schema,
        "current_token_sha256": current_token_sha256,
        "terminal_seal_present": terminal_seal_present,
        "terminal_seal_relative_path": terminal_seal_relative_path,
        "terminal_seal_sha256": terminal_seal_sha256,
        "validation_error_code": validation_error_code,
    }


def _terminal_classification_error_code(exc: BaseException) -> str:
    if isinstance(exc, AvBsError) and exc.code in FAILURE_CODES:
        return exc.code
    return "BLOCKED_AV_BS_RESULT_SCHEMA"


def _classify_manifest_terminal_evidence(
    manifest: Mapping[str, object],
    token_probe: Mapping[str, object] | None,
    token_sha256: str | None,
) -> tuple[str, str, Mapping[str, object]]:
    if token_probe is None:
        return (
            "absent",
            "candidate_token_missing_no_factor",
            _terminal_classification_record(
                classification="token_absent_no_terminal_evidence",
                authoritative_terminal_evidence=False,
                authoritative_stage_pass=False,
                authoritative_inner_disposition=None,
                current_token_schema=None,
                current_token_sha256=None,
                terminal_seal_present=False,
                terminal_seal_relative_path=None,
                terminal_seal_sha256=None,
                validation_error_code=None,
            ),
        )
    schema = token_probe.get("schema") if isinstance(token_probe.get("schema"), str) else None
    try:
        current_token_matches = bool(
            token_sha256 is not None
            and TOKEN_PATH.is_file()
            and _sha(TOKEN_PATH) == token_sha256
        )
    except OSError:
        current_token_matches = False
    if not current_token_matches:
        return (
            "present_invalid_no_factor",
            "candidate_token_changed_during_manifest_no_factor",
            _terminal_classification_record(
                classification="invalid_token_or_legacy_tombstone_no_terminal_evidence",
                authoritative_terminal_evidence=False,
                authoritative_stage_pass=False,
                authoritative_inner_disposition=None,
                current_token_schema=schema,
                current_token_sha256=token_sha256,
                terminal_seal_present=False,
                terminal_seal_relative_path=None,
                terminal_seal_sha256=None,
                validation_error_code="BLOCKED_AV_BS_RESULT_SCHEMA",
            ),
        )
    if (
        schema == TOKEN_SCHEMA
        and token_probe.get("authorization_state") == "authorized"
        and type(token_probe.get("uses_remaining")) is int
        and token_probe.get("uses_remaining") == 1
    ):
        identifier = token_probe.get("review_token_id")
        claim_path = (
            ROOT / "validation-output" / "av-bs1" / "claims" / f"{identifier}.json"
            if isinstance(identifier, str)
            and re.fullmatch(r"[0-9a-f]{32}", identifier)
            else None
        )
        if claim_path is not None and claim_path.is_file():
            try:
                authorized = _validate_token(TOKEN_PATH, manifest, check_expiry=False)
                _validate_claim(claim_path, authorized, manifest)
            except (AvBsError, OSError, ValueError, TypeError) as exc:
                return (
                    "authorized_token_claim_present_invalid_replay_blocked_no_authority",
                    "attempt_spent_locally_replay_blocked_by_claim_no_terminal_authority",
                    _terminal_classification_record(
                        classification=(
                            "authorized_token_with_unvalidated_claim_attempt_spent_"
                            "no_terminal_authority"
                        ),
                        authoritative_terminal_evidence=False,
                        authoritative_stage_pass=False,
                        authoritative_inner_disposition=None,
                        current_token_schema=schema,
                        current_token_sha256=token_sha256,
                        terminal_seal_present=False,
                        terminal_seal_relative_path=None,
                        terminal_seal_sha256=None,
                        validation_error_code=_terminal_classification_error_code(exc),
                    ),
                )
            return (
                "authorized_token_exact_claim_present_attempt_spent_no_terminal_authority",
                "attempt_spent_locally_replay_blocked_by_claim_no_terminal_authority",
                _terminal_classification_record(
                    classification=(
                        "authorized_token_with_exact_claim_attempt_spent_no_terminal_authority"
                    ),
                    authoritative_terminal_evidence=False,
                    authoritative_stage_pass=False,
                    authoritative_inner_disposition=None,
                    current_token_schema=schema,
                    current_token_sha256=token_sha256,
                    terminal_seal_present=False,
                    terminal_seal_relative_path=None,
                    terminal_seal_sha256=None,
                    validation_error_code=None,
                ),
            )
        return (
            "authorized_shape_pending_validation",
            "candidate_token_present_pending_validation_no_factor",
            _terminal_classification_record(
                classification="authorized_token_not_terminal_evidence",
                authoritative_terminal_evidence=False,
                authoritative_stage_pass=False,
                authoritative_inner_disposition=None,
                current_token_schema=schema,
                current_token_sha256=token_sha256,
                terminal_seal_present=False,
                terminal_seal_relative_path=None,
                terminal_seal_sha256=None,
                validation_error_code=None,
            ),
        )
    if schema not in (TOMBSTONE_SCHEMA, EMERGENCY_TOMBSTONE_SCHEMA):
        return (
            "present_invalid_no_factor",
            "candidate_token_invalid_no_factor",
            _terminal_classification_record(
                classification="invalid_token_or_legacy_tombstone_no_terminal_evidence",
                authoritative_terminal_evidence=False,
                authoritative_stage_pass=False,
                authoritative_inner_disposition=None,
                current_token_schema=schema,
                current_token_sha256=token_sha256,
                terminal_seal_present=False,
                terminal_seal_relative_path=None,
                terminal_seal_sha256=None,
                validation_error_code="BLOCKED_AV_BS_RESULT_SCHEMA",
            ),
        )
    identifier = token_probe.get("consumed_review_token_id")
    expected_seal = token_probe.get("expected_terminal_seal_relative_path")
    seal_path: Path | None = None
    if (
        isinstance(identifier, str)
        and re.fullmatch(r"[0-9a-f]{32}", identifier)
        and isinstance(expected_seal, str)
        and expected_seal
        == f"validation-output/av-bs1/outer-observer-terminal-seals/{identifier}.json"
    ):
        seal_path = (ROOT / expected_seal).resolve()
    seal_present = bool(seal_path is not None and seal_path.is_file())
    seal_sha: str | None = None
    if seal_present and seal_path is not None:
        try:
            seal_sha = _sha(seal_path)
        except OSError:
            seal_sha = None
    try:
        tombstone_context = _validate_consumed_tombstone(
            token_probe, _require_sha(token_sha256, "current tombstone"), manifest
        )
    except (AvBsError, OSError, ValueError, TypeError) as exc:
        prefix = "emergency_" if schema == EMERGENCY_TOMBSTONE_SCHEMA else ""
        return (
            f"{prefix}consumed_v2_invalid_tombstone_no_authority",
            f"{prefix}consumed_v2_invalid_tombstone_no_authority",
            _terminal_classification_record(
                classification="invalid_v2_consumed_tombstone_no_terminal_authority",
                authoritative_terminal_evidence=False,
                authoritative_stage_pass=False,
                authoritative_inner_disposition=None,
                current_token_schema=schema,
                current_token_sha256=token_sha256,
                terminal_seal_present=seal_present,
                terminal_seal_relative_path=(
                    expected_seal if isinstance(expected_seal, str) else None
                ),
                terminal_seal_sha256=seal_sha,
                validation_error_code=_terminal_classification_error_code(exc),
            ),
        )
    if tombstone_context["emergency"]:
        try:
            emergency_state = _validate_emergency_replacement_state(
                tombstone_context, require_postvalidated=False
            )
        except (AvBsError, OSError, ValueError, TypeError) as exc:
            return (
                "emergency_consumed_v2_invalid_replacement_journal_no_authority",
                "emergency_consumed_v2_invalid_replacement_journal_no_authority",
                _terminal_classification_record(
                    classification=(
                        "emergency_replacement_journal_invalid_no_terminal_authority"
                    ),
                    authoritative_terminal_evidence=False,
                    authoritative_stage_pass=False,
                    authoritative_inner_disposition=None,
                    current_token_schema=schema,
                    current_token_sha256=token_sha256,
                    terminal_seal_present=seal_present,
                    terminal_seal_relative_path=expected_seal,
                    terminal_seal_sha256=seal_sha,
                    validation_error_code=_terminal_classification_error_code(exc),
                ),
            )
        if emergency_state["postvalidated"] is not True:
            return (
                "emergency_replacement_pending_or_unverified_no_terminal_authority",
                "emergency_replacement_pending_or_unverified_no_terminal_authority",
                _terminal_classification_record(
                    classification=emergency_state["state"],
                    authoritative_terminal_evidence=False,
                    authoritative_stage_pass=False,
                    authoritative_inner_disposition=None,
                    current_token_schema=schema,
                    current_token_sha256=token_sha256,
                    terminal_seal_present=seal_present,
                    terminal_seal_relative_path=expected_seal,
                    terminal_seal_sha256=seal_sha,
                    validation_error_code=None,
                ),
            )
    if not seal_present:
        if tombstone_context["emergency"]:
            return (
                "emergency_replacement_postvalidated_terminal_cleanup_unresolved_no_authority",
                "emergency_replacement_postvalidated_terminal_cleanup_unresolved_no_authority",
                _terminal_classification_record(
                    classification=(
                        "emergency_replacement_postvalidated_only_"
                        "cleanup_and_exit_unresolved_missing_terminal_seal"
                    ),
                    authoritative_terminal_evidence=False,
                    authoritative_stage_pass=False,
                    authoritative_inner_disposition=None,
                    current_token_schema=schema,
                    current_token_sha256=token_sha256,
                    terminal_seal_present=False,
                    terminal_seal_relative_path=expected_seal,
                    terminal_seal_sha256=None,
                    validation_error_code=None,
                ),
            )
        return (
            "consumed_v2_provisional_missing_terminal_seal",
            "consumed_v2_provisional_missing_terminal_seal",
            _terminal_classification_record(
                classification=(
                    "valid_v2_consumed_tombstone_provisional_missing_terminal_seal"
                ),
                authoritative_terminal_evidence=False,
                authoritative_stage_pass=False,
                authoritative_inner_disposition=None,
                current_token_schema=schema,
                current_token_sha256=token_sha256,
                terminal_seal_present=False,
                terminal_seal_relative_path=expected_seal,
                terminal_seal_sha256=None,
                validation_error_code=None,
            ),
        )
    try:
        terminal = _validate_outer_terminal_evidence(token_probe, token_sha256, manifest)
        seal = terminal["seal"]
        passed = seal["authoritative_stage_pass"] is True
        if passed:
            token_state = "consumed_v2_authoritative_sealed_pass"
            status = "consumed_v2_authoritative_sealed_pass_no_next_stage_authorization"
            classification = "authoritative_sealed_pass"
            disposition_value = "pass"
        else:
            prefix = "emergency_" if tombstone_context["emergency"] else ""
            token_state = f"{prefix}consumed_v2_authoritative_sealed_failure"
            status = f"{prefix}consumed_v2_authoritative_sealed_failure"
            classification = "authoritative_sealed_failure"
            disposition_value = "failure"
        return (
            token_state,
            status,
            _terminal_classification_record(
                classification=classification,
                authoritative_terminal_evidence=True,
                authoritative_stage_pass=passed,
                authoritative_inner_disposition=disposition_value,
                current_token_schema=schema,
                current_token_sha256=token_sha256,
                terminal_seal_present=True,
                terminal_seal_relative_path=expected_seal,
                terminal_seal_sha256=seal["sha256"],
                validation_error_code=None,
            ),
        )
    except (AvBsError, OSError, ValueError, TypeError) as exc:
        prefix = "emergency_" if schema == EMERGENCY_TOMBSTONE_SCHEMA else ""
        return (
            f"{prefix}consumed_v2_provisional_invalid_terminal_evidence",
            f"{prefix}consumed_v2_provisional_invalid_terminal_evidence",
            _terminal_classification_record(
                classification=(
                    "valid_v2_consumed_tombstone_provisional_invalid_terminal_evidence"
                ),
                authoritative_terminal_evidence=False,
                authoritative_stage_pass=False,
                authoritative_inner_disposition=None,
                current_token_schema=schema,
                current_token_sha256=token_sha256,
                terminal_seal_present=seal_present,
                terminal_seal_relative_path=expected_seal if isinstance(expected_seal, str) else None,
                terminal_seal_sha256=seal_sha,
                validation_error_code=_terminal_classification_error_code(exc),
            ),
        )


def _validated_monitor_paths(
    guard_path: Path,
    ready_path: Path,
    completion_path: Path,
    release_path: Path,
) -> tuple[Path, Path, Path]:
    parent = guard_path.resolve().parent
    expected = {
        "monitor-ready.json": ready_path,
        "factor-complete.json": completion_path,
        "monitor-release.json": release_path,
    }
    resolved: dict[str, Path] = {}
    for name, path in expected.items():
        candidate = path.resolve()
        if candidate.parent != parent or candidate.name != name:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", f"{name} escaped the owned attempt directory"
            )
        resolved[name] = candidate
    return (
        resolved["monitor-ready.json"],
        resolved["factor-complete.json"],
        resolved["monitor-release.json"],
    )


def _wait_for_marker(path: Path, label: str) -> Mapping[str, object]:
    deadline = perf_counter_ns() + MONITOR_HANDSHAKE_WAIT_SECONDS * 1_000_000_000
    while perf_counter_ns() < deadline:
        if path.is_file():
            return _read(path, label)
        sleep(0.01)
    raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"{label} timed out")


def _create_marker(path: Path, payload: Mapping[str, object], label: str) -> None:
    raw = h1.canonical_bytes(payload)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory hard-link publication is atomic and refuses an
        # existing target. The runner can never observe partially written
        # marker bytes merely because the final path exists.
        os.link(temporary, path)
    except OSError as exc:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"{label} creation failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_ready_marker(
    marker: Mapping[str, object], claim_path: Path
) -> None:
    expected = {
        "schema": "AV-BS1-h4-p0r-monitor-ready-v1",
        "claim_sha256": _sha(claim_path),
        "child_process_id": os.getpid(),
    }
    if set(marker) != set(expected) | {"sample_perf_counter_ns", "sample_utc"}:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "monitor-ready field set mismatch")
    for key, value in expected.items():
        if not _strict_json_equal(marker.get(key), value):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"monitor-ready {key} mismatch")
    if _json_int(marker.get("sample_perf_counter_ns"), "monitor-ready sample") < 0:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "monitor-ready sample invalid")
    _utc(marker.get("sample_utc"), "monitor-ready sample_utc")


def _complete_monitor_handshake(
    ready: Mapping[str, object],
    ready_path: Path,
    completion_path: Path,
    release_path: Path,
    claim_path: Path,
) -> Mapping[str, object]:
    certificates = _EXECUTION_PHASE.get("factor_certificates", [])
    completed = _EXECUTION_PHASE.get("completed_factors", [])
    if not isinstance(certificates, list) or not isinstance(completed, list):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "monitor phase evidence corrupt")
    completion = {
        "schema": "AV-BS1-h4-p0r-factor-complete-v1",
        "claim_sha256": _sha(claim_path),
        "child_process_id": os.getpid(),
        "completed_factors": list(completed),
        "factor_certificates_sha256": _canonical_sha(certificates),
        "completed_perf_counter_ns": perf_counter_ns(),
        "completed_utc": _now_utc(),
    }
    _create_marker(completion_path, completion, "factor-complete marker")
    release = _wait_for_marker(release_path, "monitor-release marker")
    expected_release = {
        "schema": "AV-BS1-h4-p0r-monitor-release-v1",
        "claim_sha256": _sha(claim_path),
        "completion_marker_sha256": _sha(completion_path),
        "child_process_id": os.getpid(),
    }
    if set(release) != set(expected_release) | {"sample_perf_counter_ns", "sample_utc"}:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "monitor-release field set mismatch")
    for key, value in expected_release.items():
        if not _strict_json_equal(release.get(key), value):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"monitor-release {key} mismatch")
    ready_ns = _json_int(ready.get("sample_perf_counter_ns"), "monitor-ready sample")
    completion_ns = _json_int(
        completion.get("completed_perf_counter_ns"), "factor-complete sample"
    )
    release_ns = _json_int(release.get("sample_perf_counter_ns"), "monitor-release sample")
    if not (0 <= ready_ns <= completion_ns <= release_ns):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "monitor handshake interval is reversed")
    ready_utc = _utc(ready.get("sample_utc"), "monitor-ready sample_utc")
    completion_utc = _utc(completion.get("completed_utc"), "factor-complete completed_utc")
    release_utc = _utc(release.get("sample_utc"), "monitor-release sample_utc")
    if not (ready_utc <= completion_utc <= release_utc):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "monitor handshake UTC interval is reversed")
    return {
        "schema": "AV-BS1-h4-p0r-factor-monitor-handshake-v1",
        "ready_marker_sha256": _sha(ready_path),
        "ready_sample_perf_counter_ns": ready_ns,
        "ready_sample_utc": ready["sample_utc"],
        "completion_marker_sha256": _sha(completion_path),
        "completion_perf_counter_ns": completion_ns,
        "completion_utc": completion["completed_utc"],
        "release_marker_sha256": _sha(release_path),
        "release_sample_perf_counter_ns": release_ns,
        "release_sample_utc": release["sample_utc"],
        "factor_interval_bracketed_by_actual_samples": True,
    }


def _canonical_csc(matrix: object, *, dtype: np.dtype | type | None = None) -> csc_matrix:
    result = csc_matrix(matrix, dtype=dtype, copy=True)
    result.sum_duplicates()
    result.sort_indices()
    result.eliminate_zeros()
    return result


def _sparse_bytes(matrix: csc_matrix) -> int:
    return int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)


_HASH_CHUNK_ITEMS = 1024 * 1024


def _update_hash_array(
    digest: object, values: np.ndarray, *, dtype: str | None = None
) -> None:
    """Feed array bytes without materializing a whole-array ``bytes`` copy."""
    array = np.asarray(values)
    if array.ndim != 1:
        raise AvBsError("BLOCKED_AV_BS_FACTOR", "factor hash array must be one-dimensional")
    target = array.dtype if dtype is None else np.dtype(dtype)
    if array.dtype == target and array.flags.c_contiguous:
        digest.update(memoryview(array).cast("B"))
        return
    for start in range(0, int(array.size), _HASH_CHUNK_ITEMS):
        stop = min(start + _HASH_CHUNK_ITEMS, int(array.size))
        chunk = np.ascontiguousarray(array[start:stop], dtype=target)
        digest.update(memoryview(chunk).cast("B"))


def _streaming_sparse_sha256(matrix: csc_matrix) -> str:
    """Match ``h1._sparse_sha256`` for an already canonical CSC, bounded-memory."""
    if not matrix.has_canonical_format or not matrix.has_sorted_indices:
        raise AvBsError("BLOCKED_AV_BS_FACTOR", "sparse hash requires canonical CSC")
    digest = sha256(h1.canonical_bytes({"shape": matrix.shape}))
    data_dtype = "<c16" if np.iscomplexobj(matrix.data) else "<f8"
    _update_hash_array(digest, matrix.data, dtype=data_dtype)
    _update_hash_array(digest, matrix.indices, dtype="<i8")
    _update_hash_array(digest, matrix.indptr, dtype="<i8")
    return digest.hexdigest()


def _csc_storage_sha256(matrix: csc_matrix) -> str:
    """Hash the exact stored CSC arrays without canonicalizing or coercing them."""
    digest = sha256(
        h1.canonical_bytes(
            {
                "shape": [int(matrix.shape[0]), int(matrix.shape[1])],
                "data_dtype": np.dtype(matrix.data.dtype).str,
                "indices_dtype": np.dtype(matrix.indices.dtype).str,
                "indptr_dtype": np.dtype(matrix.indptr.dtype).str,
            }
        )
    )
    _update_hash_array(digest, matrix.data)
    _update_hash_array(digest, matrix.indices)
    _update_hash_array(digest, matrix.indptr)
    return digest.hexdigest()


def _scan_factor_data(values: np.ndarray) -> tuple[bool, int]:
    finite = True
    zero_count = 0
    for start in range(0, int(values.size), _HASH_CHUNK_ITEMS):
        stop = min(start + _HASH_CHUNK_ITEMS, int(values.size))
        chunk = values[start:stop]
        finite = finite and bool(np.all(np.isfinite(chunk)))
        zero_count += int(np.count_nonzero(chunk == 0.0))
    return finite, zero_count


def _csc_triangular(matrix: csc_matrix, *, lower: bool) -> bool:
    """Check triangular support directly from CSC column slices."""
    indices = matrix.indices
    indptr = matrix.indptr
    for column in range(int(matrix.shape[1])):
        rows = indices[int(indptr[column]):int(indptr[column + 1])]
        if lower:
            if np.any(rows < column):
                return False
        elif np.any(rows > column):
            return False
    return True


def _check_matrix_identity(
    name: str, matrix: csc_matrix, expected: Mapping[str, object]
) -> None:
    observed = {
        "dtype": np.dtype(matrix.dtype).name,
        "shape": [int(matrix.shape[0]), int(matrix.shape[1])],
        "nnz": int(matrix.nnz),
        "sha256": h1._sparse_sha256(matrix),
        "sparse_array_bytes": _sparse_bytes(matrix),
        "transpose_relative": h1._sparse_transpose_relative(matrix),
    }
    for key, value in observed.items():
        if not _strict_json_equal(expected.get(key), value):
            raise AvBsError(
                "BLOCKED_AV_BS_MESH_HASH", f"{name} parent {key} mismatch"
            )


def _build_scaled_matrix(
    name: str, matrix_inputs: Mapping[str, object]
) -> tuple[csc_matrix, Mapping[str, object]]:
    """Rebuild and freeze exactly one approved equilibrated factor input."""
    if name not in {"A_background_II", "A_conductor_II"}:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"unapproved factor input {name}")
    records = matrix_inputs.get("matrices")
    if not isinstance(records, Mapping):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "matrix records missing")
    for required in ("K_II", "M_II", name):
        if not isinstance(records.get(required), Mapping):
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"{required} record missing")
    if (
        not _strict_json_equal(matrix_inputs.get("frequency_hz"), h1.FREQUENCY_HZ)
        or not _strict_json_equal(matrix_inputs.get("sigma_s_per_m"), h1.SIGMA_S_PER_M)
        or not _strict_json_equal(
            matrix_inputs.get("omega_rad_per_s"), 2.0 * math.pi * h1.FREQUENCY_HZ
        )
        or not _strict_json_equal(
            matrix_inputs.get("omega_sigma"),
            2.0 * math.pi * h1.FREQUENCY_HZ * h1.SIGMA_S_PER_M,
        )
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "matrix runtime constants drift")

    construction_started_ns = perf_counter_ns()
    construction_started_utc = _now_utc()
    nodes = triangles = midpoint = mesh = boundary = tags = interior = None
    raw_stiffness = mass = stiffness = k_ii = m_ii = raw_input = None
    row_max = row_scale = row_scaled = column_max = column_scale = None
    equilibrated_row_max = equilibrated_column_max = None
    equilibrated: csc_matrix | None = None
    metadata: dict[str, object] | None = None
    try:
        nodes, triangles, midpoint = h4.seed_mesh_h4()
        mesh, boundary = h4.mesh_manifest(nodes, triangles)
        tags, _ = h4.h4_tags(midpoint)
        raw_stiffness, mass = h1._assemble_volume(nodes, triangles)
        stiffness, _ = h4.canonicalize_h4(
            raw_stiffness, nodes, triangles, tags, mesh
        )
        interior, _, _ = h4.partition(len(nodes), boundary)
        if len(interior) != matrix_inputs.get("interior_nodes"):
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "interior-node count drift")
        k_ii = _canonical_csc(stiffness[interior, :][:, interior], dtype=np.float64)
        m_ii = _canonical_csc(mass[interior, :][:, interior], dtype=np.float64)
        _check_matrix_identity("K_II", k_ii, records["K_II"])
        _check_matrix_identity("M_II", m_ii, records["M_II"])

        if name == "A_background_II":
            raw_input = _canonical_csc(k_ii, dtype=np.complex128)
        else:
            omega = 2.0 * math.pi * h1.FREQUENCY_HZ
            raw_input = _canonical_csc(
                k_ii.astype(np.complex128)
                + 1j * omega * h1.SIGMA_S_PER_M * m_ii,
                dtype=np.complex128,
            )
        expected = records[name]
        _check_matrix_identity(name, raw_input, expected)

        row_max = h1._row_max_abs(raw_input)
        if np.any(~np.isfinite(row_max)) or np.any(row_max <= 0.0):
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"{name} invalid row maxima")
        row_scale = np.asarray(1.0 / row_max, dtype=np.float64)
        observed_row = {
            "row_scale_sha256": h1._array_sha256(row_scale, dtype="<f8"),
            "row_scale_min": float(np.min(row_scale)),
            "row_scale_max": float(np.max(row_scale)),
        }
        for key, value in observed_row.items():
            if not _strict_json_equal(expected.get(key), value):
                raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"{name} {key} mismatch")
        row_scaled = _canonical_csc(raw_input.multiply(row_scale[:, None]))
        column_max = h1._column_max_abs(row_scaled)
        if np.any(~np.isfinite(column_max)) or np.any(column_max <= 0.0):
            raise AvBsError(
                "BLOCKED_AV_BS_MESH_HASH", f"{name} invalid column maxima"
            )
        column_scale = np.asarray(1.0 / column_max, dtype=np.float64)
        observed_column = {
            "column_scale_sha256": h1._array_sha256(column_scale, dtype="<f8"),
            "column_scale_min": float(np.min(column_scale)),
            "column_scale_max": float(np.max(column_scale)),
        }
        for key, value in observed_column.items():
            if not _strict_json_equal(expected.get(key), value):
                raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"{name} {key} mismatch")
        equilibrated = _canonical_csc(
            row_scaled.multiply(column_scale[None, :]), dtype=np.complex128
        )
        equilibrated_row_max = h1._row_max_abs(equilibrated)
        equilibrated_column_max = h1._column_max_abs(equilibrated)
        observed_equilibrated = {
            "equilibrated_nnz": int(equilibrated.nnz),
            "equilibrated_sha256": h1._sparse_sha256(equilibrated),
            "equilibrated_sparse_array_bytes": _sparse_bytes(equilibrated),
            "equilibrated_row_max_abs_min": float(np.min(equilibrated_row_max)),
            "equilibrated_row_max_abs_max": float(np.max(equilibrated_row_max)),
            "equilibrated_column_max_abs_min": float(
                np.min(equilibrated_column_max)
            ),
            "equilibrated_column_max_abs_max": float(
                np.max(equilibrated_column_max)
            ),
        }
        for key, value in observed_equilibrated.items():
            if not _strict_json_equal(expected.get(key), value):
                raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"{name} {key} mismatch")
        metadata = {
            "raw_input_sparse_sha256": expected["sha256"],
            "row_scale_sha256": expected["row_scale_sha256"],
            "column_scale_sha256": expected["column_scale_sha256"],
            "equilibrated_sparse_sha256": expected["equilibrated_sha256"],
            "construction_started_perf_counter_ns": construction_started_ns,
            "construction_started_utc": construction_started_utc,
        }
    except MemoryError as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", f"{name} matrix construction exhausted memory"
        ) from exc
    except AvBsError as exc:
        if exc.code == "BLOCKED_AV_BS_RESOURCE":
            raise
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", exc.detail) from exc
    except Exception as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_MESH_HASH", f"{name} matrix construction failed"
        ) from exc
    finally:
        nodes = triangles = midpoint = mesh = boundary = tags = interior = None
        raw_stiffness = mass = stiffness = k_ii = m_ii = raw_input = None
        row_max = row_scale = row_scaled = column_max = column_scale = None
        equilibrated_row_max = equilibrated_column_max = None
        gc.collect()

    if equilibrated is None or metadata is None:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"{name} matrix unavailable")
    metadata["construction_cleanup_completed_perf_counter_ns"] = perf_counter_ns()
    metadata["construction_cleanup_completed_utc"] = _now_utc()
    return equilibrated, metadata


def _certificate(
    name: str,
    matrix: csc_matrix,
    metadata: Mapping[str, object],
    cap: int,
) -> Mapping[str, object]:
    factor = raw_l = raw_u = l = u = None
    l_diagonal = u_diagonal = u_diagonal_abs = None
    perm_r_storage = perm_c_storage = perm_r = perm_c = target = None
    certificate: dict[str, object] | None = None
    factor_started_ns: int | None = None
    factor_returned_ns: int | None = None
    native_factor_nnz: int | None = None
    native_portable_bytes: int | None = None
    try:
        try:
            from scipy.sparse.linalg import splu
        except Exception as exc:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} SuperLU unavailable") from exc

        n = int(matrix.shape[0])
        if matrix.shape != (n, n) or matrix.nnz <= 0:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} invalid factor input")
        factor_started_ns = perf_counter_ns()
        factor_started_utc = _now_utc()
        _EXECUTION_PHASE["factorization_attempted"] = True
        _EXECUTION_PHASE["active_factor"] = name
        if not _EXECUTION_PHASE["completed_factors"]:
            _EXECUTION_PHASE["factorization_performed"] = None
        try:
            factor = splu(
                matrix,
                permc_spec="COLAMD",
                diag_pivot_thresh=1.0,
                options={"Equil": False},
            )
        except MemoryError as exc:
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", f"{name} SuperLU exhausted memory"
            ) from exc
        except Exception as exc:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} SuperLU failed") from exc
        factor_returned_ns = perf_counter_ns()
        factor_returned_utc = _now_utc()
        _EXECUTION_PHASE["factorization_performed"] = True
        completed = _EXECUTION_PHASE["completed_factors"]
        if not isinstance(completed, list):
            raise AvBsError("BLOCKED_AV_BS_FACTOR", "factor phase state corrupt")
        completed.append(name)

        raw_native_nnz = getattr(factor, "nnz", None)
        if type(raw_native_nnz) is not int or raw_native_nnz <= 0:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} native factor nnz invalid")
        native_factor_nnz = raw_native_nnz
        native_portable_bytes = int(
            24 * native_factor_nnz + 8 * (4 * n + 2)
        )
        if native_portable_bytes > cap:
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE",
                f"{name} native factor exceeds pre-materialization cap",
            )

        # Do not silently normalize SuperLU output.  The certificate binds the
        # arrays actually returned by the frozen runtime, and fails if they
        # would require duplicate/zero/index-order cleanup.
        raw_l = factor.L
        raw_u = factor.U
        perm_r_storage = np.array(factor.perm_r, copy=True, order="C")
        perm_c_storage = np.array(factor.perm_c, copy=True, order="C")
        factor = None
        gc.collect()
        if (
            not isinstance(raw_l, (csc_matrix, csc_array))
            or not isinstance(raw_u, (csc_matrix, csc_array))
            or np.dtype(raw_l.dtype) != np.dtype(np.complex128)
            or np.dtype(raw_u.dtype) != np.dtype(np.complex128)
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_FACTOR", f"{name} factor storage dtype/format mismatch"
            )
        l = raw_l
        u = raw_u
        if l.shape != matrix.shape or u.shape != matrix.shape:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} factor shape mismatch")
        if int(l.nnz + u.nnz) != native_factor_nnz:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} native/exported nnz mismatch")
        l_finite, l_explicit_zero_count = _scan_factor_data(l.data)
        u_finite, u_explicit_zero_count = _scan_factor_data(u.data)
        if not l_finite or not u_finite:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} non-finite factor data")
        l_storage_canonical = bool(l.has_canonical_format and l.has_sorted_indices)
        u_storage_canonical = bool(u.has_canonical_format and u.has_sorted_indices)
        if (
            not l_storage_canonical
            or not u_storage_canonical
            or l_explicit_zero_count != 0
            or u_explicit_zero_count != 0
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_FACTOR", f"{name} non-canonical factor storage"
            )

        l_diagonal = np.asarray(l.diagonal(), dtype="<c16")
        u_diagonal = np.asarray(u.diagonal(), dtype="<c16")
        l_lower = _csc_triangular(l, lower=True)
        l_unit_diagonal = bool(
            l_diagonal.size == n
            and np.all(np.isfinite(l_diagonal))
            and np.all(l_diagonal == np.complex128(1.0 + 0.0j))
        )
        u_upper = _csc_triangular(u, lower=False)
        u_diagonal_abs = np.abs(u_diagonal)
        u_diagonal_valid = bool(
            u_diagonal.size == n
            and np.all(np.isfinite(u_diagonal))
            and np.all(u_diagonal_abs > 0.0)
        )
        if not l_lower or not l_unit_diagonal or not u_upper or not u_diagonal_valid:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} triangular gate failed")

        perm_r = np.asarray(perm_r_storage, dtype="<i8")
        perm_c = np.asarray(perm_c_storage, dtype="<i8")
        target = np.arange(n, dtype="<i8")
        perm_r_bijective = bool(
            perm_r.shape == (n,) and np.array_equal(np.sort(perm_r), target)
        )
        perm_c_bijective = bool(
            perm_c.shape == (n,) and np.array_equal(np.sort(perm_c), target)
        )
        if not perm_r_bijective or not perm_c_bijective:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} permutation gate failed")

        exported_bytes = int(
            sum(
                array.nbytes
                for array in (
                    l.data,
                    l.indices,
                    l.indptr,
                    u.data,
                    u.indices,
                    u.indptr,
                    perm_r_storage,
                    perm_c_storage,
                )
            )
        )
        factor_array_bytes = {
            "L_data": int(l.data.nbytes),
            "L_indices": int(l.indices.nbytes),
            "L_indptr": int(l.indptr.nbytes),
            "U_data": int(u.data.nbytes),
            "U_indices": int(u.indices.nbytes),
            "U_indptr": int(u.indptr.nbytes),
            "perm_r": int(perm_r_storage.nbytes),
            "perm_c": int(perm_c_storage.nbytes),
        }
        portable_bytes = int(24 * (l.nnz + u.nnz) + 8 * (4 * n + 2))
        stored_fill_ratio = float((l.nnz + u.nnz) / matrix.nnz)
        certificate = {
            "name": name,
            "input_shape": [n, n],
            "input_dtype": np.dtype(matrix.dtype).name,
            "input_nnz": int(matrix.nnz),
            "input_sparse_sha256": h1._sparse_sha256(matrix),
            "input_sparse_array_bytes": _sparse_bytes(matrix),
            **dict(metadata),
            "permc_spec": "COLAMD",
            "diag_pivot_thresh": 1.0,
            "superlu_equil": False,
            "rhs_count": 0,
            "L_shape": [n, n],
            "L_dtype": np.dtype(l.dtype).name,
            "L_nnz": int(l.nnz),
            "L_sha256": _streaming_sparse_sha256(l),
            "L_storage_sha256": _csc_storage_sha256(l),
            "L_indices_dtype": np.dtype(l.indices.dtype).str,
            "L_indptr_dtype": np.dtype(l.indptr.dtype).str,
            "L_storage_canonical": l_storage_canonical,
            "L_explicit_zero_count": l_explicit_zero_count,
            "L_lower_triangular": l_lower,
            "L_unit_diagonal": l_unit_diagonal,
            "U_shape": [n, n],
            "U_dtype": np.dtype(u.dtype).name,
            "U_nnz": int(u.nnz),
            "U_sha256": _streaming_sparse_sha256(u),
            "U_storage_sha256": _csc_storage_sha256(u),
            "U_indices_dtype": np.dtype(u.indices.dtype).str,
            "U_indptr_dtype": np.dtype(u.indptr.dtype).str,
            "U_storage_canonical": u_storage_canonical,
            "U_explicit_zero_count": u_explicit_zero_count,
            "U_upper_triangular": u_upper,
            "U_diagonal_dtype": "complex128",
            "U_diagonal_count": int(u_diagonal.size),
            "U_diagonal_sha256": h1._array_sha256(u_diagonal, dtype="<c16"),
            "U_diagonal_abs_min": float(np.min(u_diagonal_abs)),
            "U_diagonal_abs_max": float(np.max(u_diagonal_abs)),
            "U_diagonal_finite_nonzero": u_diagonal_valid,
            "perm_r_dtype": "int64",
            "perm_r_count": int(perm_r.size),
            "perm_r_storage_dtype": np.dtype(perm_r_storage.dtype).str,
            "perm_r_sha256": h1._array_sha256(perm_r, dtype="<i8"),
            "perm_r_bijective": perm_r_bijective,
            "perm_c_dtype": "int64",
            "perm_c_count": int(perm_c.size),
            "perm_c_storage_dtype": np.dtype(perm_c_storage.dtype).str,
            "perm_c_sha256": h1._array_sha256(perm_c, dtype="<i8"),
            "perm_c_bijective": perm_c_bijective,
            "stored_fill_ratio": stored_fill_ratio,
            "fill_ratio": stored_fill_ratio,
            "native_factor_nnz": native_factor_nnz,
            "native_portable_factor_bytes": native_portable_bytes,
            "native_pre_materialization_cap_pass": True,
            "factor_array_bytes": factor_array_bytes,
            "exported_factor_bytes": exported_bytes,
            "portable_factor_bytes": portable_bytes,
            "one_factor_hard_cap_bytes": int(cap),
            "factor_bytes_gate_pass": max(exported_bytes, portable_bytes) <= cap,
            "factor_started_perf_counter_ns": factor_started_ns,
            "factor_returned_perf_counter_ns": factor_returned_ns,
            "factor_wall_ns": factor_returned_ns - factor_started_ns,
            "factor_wall_seconds": (factor_returned_ns - factor_started_ns) / 1.0e9,
            "factor_started_utc": factor_started_utc,
            "factor_returned_utc": factor_returned_utc,
            "factorization_attempted": True,
            "factorization_performed": True,
            "factor_solve_called": False,
        }
        if not certificate["factor_bytes_gate_pass"]:
            raise AvBsError(
                "BLOCKED_AV_BS_RESOURCE", f"{name} one-factor cap exceeded"
            )
    except MemoryError as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESOURCE", f"{name} factor certificate exhausted memory"
        ) from exc
    except AvBsError:
        raise
    except Exception as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_FACTOR", f"{name} factor certificate failed"
        ) from exc
    finally:
        cleanup_started_ns = perf_counter_ns()
        cleanup_started_utc = _now_utc()
        factor = raw_l = raw_u = l = u = None
        l_diagonal = u_diagonal = u_diagonal_abs = None
        perm_r_storage = perm_c_storage = perm_r = perm_c = target = None
        gc.collect()
        cleanup_completed_ns = perf_counter_ns()
        cleanup_completed_utc = _now_utc()
        _EXECUTION_PHASE["active_factor"] = None
        if certificate is not None:
            certificate["factor_objects_cleanup_started_perf_counter_ns"] = (
                cleanup_started_ns
            )
            certificate["factor_objects_cleanup_completed_perf_counter_ns"] = (
                cleanup_completed_ns
            )
            certificate["factor_objects_cleanup_started_utc"] = cleanup_started_utc
            certificate["factor_objects_cleanup_completed_utc"] = cleanup_completed_utc
    if certificate is None:
        raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} certificate unavailable")
    return certificate


def _factor_one(
    name: str, matrix_inputs: Mapping[str, object], cap: int
) -> Mapping[str, object]:
    matrix: csc_matrix | None = None
    certificate: dict[str, object] | None = None
    try:
        matrix, metadata = _build_scaled_matrix(name, matrix_inputs)
        certificate = dict(_certificate(name, matrix, metadata, cap))
    finally:
        matrix_cleanup_started_ns = perf_counter_ns()
        matrix_cleanup_started_utc = _now_utc()
        matrix = None
        gc.collect()
        if certificate is not None:
            certificate["matrix_cleanup_started_perf_counter_ns"] = (
                matrix_cleanup_started_ns
            )
            certificate["matrix_cleanup_completed_perf_counter_ns"] = perf_counter_ns()
            certificate["matrix_cleanup_started_utc"] = matrix_cleanup_started_utc
            certificate["matrix_cleanup_completed_utc"] = _now_utc()
    if certificate is None:
        raise AvBsError("BLOCKED_AV_BS_FACTOR", f"{name} factor unavailable")
    return certificate


def _factor_prefix_path(guard_path: Path, count: int) -> Path:
    if count not in (1, 2):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix count invalid")
    path = guard_path.with_name(f"factor-prefix-{count}.json").resolve()
    if path.parent != guard_path.resolve().parent:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix path escaped")
    return path


def _write_factor_prefix_checkpoint(
    guard_path: Path,
    claim_path: Path,
    guard: Mapping[str, object],
    claim: Mapping[str, object],
    token: Mapping[str, object],
    manifest: Mapping[str, object],
) -> None:
    certificates = _EXECUTION_PHASE.get("factor_certificates")
    if not isinstance(certificates, list) or len(certificates) not in (1, 2):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix state invalid")
    approved = ["A_background_II", "A_conductor_II"]
    count = len(certificates)
    payload = {
        "schema": NUMERICAL_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "status": "factor_certificate_prefix_checkpoint",
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _sha(TOKEN_PATH),
        "git_head": _git_head(),
        "p0r_preregistration_commit": token["p0r_preregistration_commit"],
        "bindings": manifest["bindings"],
        "manifest_payload_sha256": manifest["bindings"]["manifest_payload_sha256"],
        "matrix_contract_sha256": manifest["bindings"]["matrix_contract_sha256"],
        "resource_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "guard_contract_sha256": _sha(guard_path),
        "guard_canonical_sha256": _canonical_sha(guard),
        "claim_sha256": _sha(claim_path),
        "claim_canonical_sha256": _canonical_sha(claim),
        "monitor_handshake": None,
        "factor_order": approved[:count],
        "factor_certificates": list(certificates),
        "factor_sequence_nonoverlap": True,
        "factorization_attempted": True,
        "factorization_performed": True,
        "physics_solve_performed": False,
        "forbidden_operation_flags": manifest["forbidden_operations"],
        "next_stage_authorized": False,
    }
    _create_marker(
        _factor_prefix_path(guard_path, count),
        _wrap(payload),
        f"factor prefix {count}",
    )


def _validate_factor_prefix_checkpoint(
    path: Path,
    count: int,
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    claim_path: Path,
    guard: Mapping[str, object],
    guard_path: Path,
) -> Mapping[str, object]:
    if path.resolve() != _factor_prefix_path(guard_path, count) or not path.is_file():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix path mismatch")
    payload, _ = _read_wrapper(path, f"factor prefix {count}")
    _validate_factor_report(
        payload,
        token,
        manifest,
        claim,
        claim_path,
        guard,
        guard_path,
        expected_count=count,
        expected_status="factor_certificate_prefix_checkpoint",
        require_handshake=False,
        validate_prefix_sidecars=False,
    )
    return payload

def primary(
    guard_path: Path,
    nonce: str,
    token_path: Path,
    claim_path: Path,
    ready_path: Path,
    completion_path: Path,
    release_path: Path,
) -> Mapping[str, object]:
    _EXECUTION_PHASE.update(
        {
            "claim_validated": False,
            "factorization_attempted": False,
            "factorization_performed": False,
            "active_factor": None,
            "completed_factors": [],
            "factor_certificates": [],
            "monitor_handshake": None,
        }
    )
    if token_path.resolve() != TOKEN_PATH.resolve():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "unapproved token path")
    ready_path, completion_path, release_path = _validated_monitor_paths(
        guard_path, ready_path, completion_path, release_path
    )
    manifest = run_manifest()
    token = _validate_token(token_path, manifest, check_expiry=False)
    claim = _validate_claim(claim_path, token, manifest)
    _EXECUTION_PHASE["claim_validated"] = True
    guard = _validate_guard(guard_path, nonce, token, manifest, claim_path)
    preflight_payload = preflight(token_path, check_expiry=False)
    if _wrap(preflight_payload)["payload_sha256"] != claim["preflight_payload_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claimed preflight payload mismatch")
    ready = _wait_for_marker(ready_path, "monitor-ready marker")
    _validate_ready_marker(ready, claim_path)
    matrix_inputs = manifest["p0r_parent"]["matrix_inputs"]
    cap = int(manifest["resource_policy"]["one_factor_hard_cap_bytes"])
    try:
        bcert = _factor_one("A_background_II", matrix_inputs, cap)
        _EXECUTION_PHASE["factor_certificates"].append(bcert)
        _write_factor_prefix_checkpoint(
            guard_path, claim_path, guard, claim, token, manifest
        )
        ccert = _factor_one("A_conductor_II", matrix_inputs, cap)
        _EXECUTION_PHASE["factor_certificates"].append(ccert)
        if (
            int(bcert["matrix_cleanup_completed_perf_counter_ns"])
            > int(ccert["factor_started_perf_counter_ns"])
            or _EXECUTION_PHASE["completed_factors"]
            != ["A_background_II", "A_conductor_II"]
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_FACTOR", "factor sequence overlap/order gate failed"
            )
        _write_factor_prefix_checkpoint(
            guard_path, claim_path, guard, claim, token, manifest
        )
    except AvBsError:
        _EXECUTION_PHASE["monitor_handshake"] = _complete_monitor_handshake(
            ready, ready_path, completion_path, release_path, claim_path
        )
        raise
    handshake = _complete_monitor_handshake(
        ready, ready_path, completion_path, release_path, claim_path
    )
    _EXECUTION_PHASE["monitor_handshake"] = handshake
    return {
        "schema": NUMERICAL_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "status": "factor_certificates_complete",
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _sha(token_path),
        "git_head": _git_head(),
        "p0r_preregistration_commit": token["p0r_preregistration_commit"],
        "bindings": manifest["bindings"],
        "manifest_payload_sha256": manifest["bindings"]["manifest_payload_sha256"],
        "matrix_contract_sha256": manifest["bindings"]["matrix_contract_sha256"],
        "resource_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "guard_contract_sha256": _sha(guard_path),
        "guard_canonical_sha256": _canonical_sha(guard),
        "claim_sha256": _sha(claim_path),
        "claim_canonical_sha256": _canonical_sha(claim),
        "monitor_handshake": handshake,
        "factor_order": [bcert["name"], ccert["name"]],
        "factor_certificates": [bcert, ccert],
        "factor_sequence_nonoverlap": True,
        "factorization_attempted": True,
        "factorization_performed": True,
        "physics_solve_performed": False,
        "forbidden_operation_flags": manifest["forbidden_operations"],
        "next_stage_authorized": False,
    }


def _validate_system_sample(value: Mapping[str, object], label: str) -> None:
    required = {
        "commit_total_bytes", "commit_limit_bytes", "commit_headroom_bytes",
        "available_physical_bytes", "page_size_bytes",
    }
    if set(value) != required:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} field set mismatch")
    total = _json_int(value.get("commit_total_bytes"), f"{label} commit total")
    limit = _json_int(value.get("commit_limit_bytes"), f"{label} commit limit")
    headroom = _json_int(
        value.get("commit_headroom_bytes"), f"{label} commit headroom"
    )
    available = _json_int(
        value.get("available_physical_bytes"), f"{label} available physical"
    )
    page_size = _json_int(value.get("page_size_bytes"), f"{label} page size")
    if (
        total < 0
        or limit <= 0
        or total > limit
        or headroom != limit - total
        or available < 0
        or page_size <= 0
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} arithmetic mismatch")


def _resource_gate(
    resource: Mapping[str, object],
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    guard: Mapping[str, object],
    claim: Mapping[str, object],
    *,
    guard_path: Path,
    claim_path: Path,
    terminal_bindings: Mapping[str, object] | None = None,
) -> tuple[bool, list[str]]:
    """Recompute resource-only gates; child exit semantics are handled separately."""
    policy = manifest["resource_policy"]
    resource_fields = {
        "schema", "program", "case_id", "stage", "runner_sha256",
        "fixture_sha256", "review_token_id", "review_token_sha256",
        "review_token_canonical_sha256", "resource_guard_policy_sha256",
        "execution_resource_scope_sha256",
        "guard_contract_sha256", "guard_canonical_sha256", "parent_pid",
        "parent_pid_birth_utc_ticks", "monitor_kind", "execution_tree_root_pid",
        "execution_tree_root_birth_utc_ticks",
        "execution_tree_includes_runner", "child_process_id",
        "child_process_handle_acquired", "observed_child_process_ids",
        "observed_process_identities", "monitor_ok", "monitor_error",
        "simultaneous_current_factor_interval_semantics",
        "summed_os_lifetime_peak_semantics",
        "stop_reason", "successful_tree_sample_count",
        "child_visible_tree_sample_count",
        "first_child_visible_sample_perf_counter_ns",
        "first_child_visible_sample_utc",
        "last_child_visible_sample_perf_counter_ns",
        "last_child_visible_sample_utc", "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256", "monitor_release_marker_sha256",
        "factor_monitor_handshake_complete", "factor_prefix_one_sha256",
        "factor_prefix_two_sha256", "baseline", "immediate_pre_spawn",
        "peak", "thresholds",
        "started_utc", "ended_utc", "wall_seconds", "wall_stop_seconds",
        "wall_clock_kind",
        "claim_relative_path", "claim_sha256", "claim_canonical_sha256",
        "preflight_payload_sha256", "final_system", "final_system_sample_valid",
        "child_exit_code",
        "child_stdout_sha256", "child_stderr_sha256",
        "mandatory_resource_gate_pass",
    }
    if set(resource) not in (resource_fields, resource_fields | {"failure_code"}):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource field set mismatch")
    observer = (
        _validate_outer_handshake_prefix(claim, token, manifest)
        if terminal_bindings is None
        else terminal_bindings["observer"]
    )
    review_token_sha256 = (
        _sha(TOKEN_PATH)
        if terminal_bindings is None
        else terminal_bindings["review_token_sha256"]
    )
    review_token_canonical_sha256 = (
        _canonical_sha(token)
        if terminal_bindings is None
        else terminal_bindings["review_token_canonical_sha256"]
    )
    guard_sha256 = (
        _sha(guard_path)
        if terminal_bindings is None
        else terminal_bindings["guard_sha256"]
    )
    guard_canonical_sha256 = (
        _canonical_sha(guard)
        if terminal_bindings is None
        else terminal_bindings["guard_canonical_sha256"]
    )
    claim_sha256 = (
        _sha(claim_path)
        if terminal_bindings is None
        else terminal_bindings["claim_sha256"]
    )
    claim_canonical_sha256 = (
        _canonical_sha(claim)
        if terminal_bindings is None
        else terminal_bindings["claim_canonical_sha256"]
    )
    provenance = {
        "schema": RESOURCE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "fixture_sha256": manifest["bindings"]["fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": review_token_sha256,
        "review_token_canonical_sha256": review_token_canonical_sha256,
        "resource_guard_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "guard_contract_sha256": guard_sha256,
        "guard_canonical_sha256": guard_canonical_sha256,
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": claim_sha256,
        "claim_canonical_sha256": claim_canonical_sha256,
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "parent_pid": claim["parent_pid"],
        "parent_pid_birth_utc_ticks": claim["parent_pid_birth_utc_ticks"],
        "execution_tree_root_pid": observer["outer_process_id"],
        "execution_tree_root_birth_utc_ticks": observer[
            "outer_process_birth_utc_ticks"
        ],
        "execution_tree_includes_runner": True,
        "monitor_kind": (
            "Win32_outer_observer_plus_inner_runner_plus_factor_child_tree_"
            "Toolhelp32_Psapi_GetPerformanceInfo_100ms"
        ),
        "simultaneous_current_factor_interval_semantics": (
            "sampled_outer_observer_plus_inner_runner_plus_factor_child_tree_"
            "current_working_set_and_commit_during_nested_factor_interval"
        ),
        "summed_os_lifetime_peak_semantics": (
            "conservative_stop_gate_includes_outer_inner_preflight_control_and_"
            "factor_work_not_factor_only_peak"
        ),
        "wall_stop_seconds": policy["wall_stop_seconds"],
        "wall_clock_kind": "System.Diagnostics.Stopwatch",
    }
    for key, expected in provenance.items():
        if not _strict_json_equal(resource.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"resource {key} mismatch")

    thresholds = resource.get("thresholds")
    expected_thresholds = {
        "tree_ws_stop_bytes": policy["tree_working_set_stop_bytes"],
        "tree_private_stop_bytes": policy["tree_private_stop_bytes"],
        "tree_commit_stop_bytes": policy["tree_commit_stop_bytes"],
        "commit_headroom_floor_bytes": policy["commit_headroom_floor_bytes"],
        "available_physical_floor_bytes": policy["available_physical_floor_bytes"],
        "minimum_commit_headroom_before_spawn_bytes": policy[
            "minimum_commit_headroom_before_spawn_bytes"
        ],
        "minimum_available_physical_before_spawn_bytes": policy[
            "minimum_available_physical_before_spawn_bytes"
        ],
    }
    if not _strict_json_equal(thresholds, expected_thresholds):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource thresholds mismatch")

    baseline = resource.get("baseline")
    immediate = resource.get("immediate_pre_spawn")
    peak = resource.get("peak")
    final = resource.get("final_system")
    final_sample_valid = resource.get("final_system_sample_valid")
    if type(final_sample_valid) is not bool:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource final sample flag invalid")
    if not all(isinstance(value, Mapping) for value in (baseline, peak)):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource nested report missing")
    if final_sample_valid:
        if not isinstance(final, Mapping):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource final sample missing")
    elif final is not None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "invalid final sample must be null")
    if immediate is not None and not isinstance(immediate, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "immediate pre-spawn sample invalid")
    peak_fields = {
        "tree_working_set_bytes",
        "tree_summed_process_lifetime_peak_working_set_bytes",
        "tree_private_commit_bytes", "tree_committed_pagefile_bytes",
        "tree_summed_process_lifetime_peak_commit_bytes",
        "tree_nonprivate_working_set_proxy_bytes",
        "tree_page_fault_count", "system_commit_total_bytes",
        "system_commit_headroom_min_bytes", "available_physical_min_bytes",
    }
    if set(peak) != peak_fields:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource nested field set mismatch")
    _validate_system_sample(baseline, "resource baseline")
    if isinstance(final, Mapping):
        _validate_system_sample(final, "resource final system")
    if isinstance(immediate, Mapping):
        _validate_system_sample(immediate, "resource immediate pre-spawn")
    started = _utc(resource.get("started_utc"), "resource started_utc")
    ended = _utc(resource.get("ended_utc"), "resource ended_utc")
    if ended < started:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource UTC interval reversed")
    child_exit = resource.get("child_exit_code")
    if child_exit is not None:
        _json_int(child_exit, "resource child exit code")
    child_pid = resource.get("child_process_id")
    if child_pid is not None and (_json_int(child_pid, "resource child pid") <= 0):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource child pid invalid")
    handle_acquired = resource.get("child_process_handle_acquired")
    if type(handle_acquired) is not bool:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource child handle flag invalid")

    observed_ids = resource.get("observed_child_process_ids")
    identities = resource.get("observed_process_identities")
    if not isinstance(observed_ids, list) or any(type(value) is not int or value <= 0 for value in observed_ids):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource observed PID list invalid")
    if len(observed_ids) != len(set(observed_ids)) or observed_ids != sorted(observed_ids):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource observed PID order invalid")
    if not isinstance(identities, list):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource process identities missing")
    identity_ids: list[int] = []
    for identity in identities:
        if not isinstance(identity, Mapping) or set(identity) != {
            "process_id", "birth_utc_ticks"
        }:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource process identity invalid")
        pid = _json_int(identity.get("process_id"), "resource identity PID")
        birth = _json_int(identity.get("birth_utc_ticks"), "resource identity birth")
        if pid <= 0 or birth <= 0:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource process identity invalid")
        identity_ids.append(pid)
    if identity_ids != observed_ids:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource process identity set mismatch")
    parent_pid = _json_int(
        resource.get("parent_pid"), "resource parent PID"
    )
    parent_birth = _json_int(
        resource.get("parent_pid_birth_utc_ticks"), "resource parent birth"
    )
    root_pid = _json_int(
        resource.get("execution_tree_root_pid"), "resource tree root PID"
    )
    root_birth = _json_int(
        resource.get("execution_tree_root_birth_utc_ticks"),
        "resource tree root birth",
    )
    identity_births = {
        identity["process_id"]: identity["birth_utc_ticks"]
        for identity in identities
    }
    if (
        parent_pid != observer["inner_process_id"]
        or parent_birth != observer["inner_process_birth_utc_ticks"]
        or root_pid != observer["outer_process_id"]
        or root_birth != observer["outer_process_birth_utc_ticks"]
        or root_pid == parent_pid
        or parent_birth < root_birth
        or root_pid not in observed_ids
        or parent_pid not in observed_ids
        or identity_births.get(root_pid) != root_birth
        or identity_births.get(parent_pid) != parent_birth
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "resource outer/inner tree identity mismatch",
        )
    if any(
        _json_int(identity.get("birth_utc_ticks"), "resource identity birth")
        < root_birth
        for identity in identities
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "resource child identity predates tree root",
        )
    if child_pid is not None:
        if child_pid in (root_pid, parent_pid) or child_pid not in observed_ids:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "resource child identity missing"
            )
        if identity_births[child_pid] < parent_birth:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "resource factor child predates inner runner",
            )

    successful_samples = _json_int(
        resource.get("successful_tree_sample_count"), "resource successful sample count"
    )
    child_samples = _json_int(
        resource.get("child_visible_tree_sample_count"), "resource child sample count"
    )
    if successful_samples < 0 or child_samples < 0:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "resource sample count is negative"
        )
    wall_seconds = _finite_float(resource.get("wall_seconds"), "resource wall seconds")
    if wall_seconds < 0.0:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource wall interval invalid")
    if type(resource.get("monitor_ok")) is not bool:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource monitor flag invalid")
    monitor_ok = resource.get("monitor_ok") is True
    handshake_complete = resource.get("factor_monitor_handshake_complete")
    if type(handshake_complete) is not bool:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource handshake flag invalid")
    first_sample_ns = resource.get("first_child_visible_sample_perf_counter_ns")
    last_sample_ns = resource.get("last_child_visible_sample_perf_counter_ns")
    first_sample_utc = resource.get("first_child_visible_sample_utc")
    last_sample_utc = resource.get("last_child_visible_sample_utc")
    marker_hash_fields = (
        "monitor_ready_marker_sha256", "factor_complete_marker_sha256",
        "monitor_release_marker_sha256",
    )
    sample_values_present = all(
        value is not None
        for value in (first_sample_ns, last_sample_ns, first_sample_utc, last_sample_utc)
    )
    marker_values = [resource.get(key) for key in marker_hash_fields]
    marker_values_present = all(value is not None for value in marker_values)
    if sample_values_present:
        first_ns = _json_int(first_sample_ns, "resource first child sample")
        last_ns = _json_int(last_sample_ns, "resource last child sample")
        first_utc = _utc(first_sample_utc, "resource first child sample UTC")
        last_utc = _utc(last_sample_utc, "resource last child sample UTC")
        if first_ns < 0 or last_ns < first_ns or last_utc < first_utc:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource child sample interval invalid")
    elif any(
        value is not None
        for value in (first_sample_ns, last_sample_ns, first_sample_utc, last_sample_utc)
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource child sample interval partial")
    for key, value in zip(marker_hash_fields, marker_values, strict=True):
        if value is not None:
            _require_sha(value, f"resource {key}")
    marker_presence = [value is not None for value in marker_values]
    if marker_presence not in (
        [False, False, False],
        [True, False, False],
        [True, True, False],
        [True, True, True],
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource marker prefix invalid")
    if marker_presence[0] and not sample_values_present:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "ready marker lacks sample interval")
    if handshake_complete is not (sample_values_present and marker_values_present):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource handshake evidence mismatch")
    prefix_values = [
        resource.get("factor_prefix_one_sha256"),
        resource.get("factor_prefix_two_sha256"),
    ]
    prefix_presence = [value is not None for value in prefix_values]
    if prefix_presence not in ([False, False], [True, False], [True, True]):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource factor prefix order invalid")
    for count, value in enumerate(prefix_values, start=1):
        if terminal_bindings is None:
            path = _factor_prefix_path(guard_path, count)
            expected = _sha(path) if path.is_file() else None
        else:
            expected = terminal_bindings[f"factor_prefix_{count}_sha256"]
        if value is not None:
            _require_sha(value, f"resource factor prefix {count}")
        if not _strict_json_equal(value, expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"resource factor prefix {count} mismatch")
    stop_reason = resource.get("stop_reason")
    monitor_error = resource.get("monitor_error")
    if stop_reason is not None and not isinstance(stop_reason, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource stop reason invalid")
    if stop_reason is not None and stop_reason not in RESOURCE_STOP_REASONS:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource stop reason unknown")
    if monitor_error is not None and not isinstance(monitor_error, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource monitor error invalid")
    failures: list[str] = []
    if child_pid is None:
        failures.append("child_process_missing")
    if not monitor_ok:
        failures.append("monitor_not_ok")
    if stop_reason is not None:
        failures.append("stop_reason_present")
    if monitor_error is not None:
        failures.append("monitor_error_present")
    if not final_sample_valid:
        failures.append("final_system_sample_missing")
    if not handshake_complete:
        failures.append("factor_monitor_handshake_missing")
    if successful_samples < int(policy["successful_tree_sample_count_min"]):
        failures.append("tree_sample_missing")
    if child_samples > successful_samples:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "child sample count exceeds tree samples"
        )
    if not handle_acquired or child_samples < 2:
        failures.append("child_sample_or_handle_missing")
    if wall_seconds > float(policy["wall_stop_seconds"]):
        failures.append("wall_stop_exceeded")
    if _json_int(baseline.get("commit_headroom_bytes"), "baseline commit headroom") < int(
        policy["minimum_commit_headroom_before_spawn_bytes"]
    ):
        failures.append("pre_spawn_commit_headroom")
    if _json_int(baseline.get("available_physical_bytes"), "baseline available physical") < int(
        policy["minimum_available_physical_before_spawn_bytes"]
    ):
        failures.append("pre_spawn_available_physical")
    if (
        not _strict_json_equal(
            guard.get("baseline_commit_headroom_bytes"),
            baseline.get("commit_headroom_bytes"),
        )
        or not _strict_json_equal(
            guard.get("baseline_available_physical_bytes"),
            baseline.get("available_physical_bytes"),
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "guard/resource baseline mismatch"
        )
    if immediate is None:
        failures.append("immediate_pre_spawn_sample_missing")
    else:
        if _json_int(
            immediate.get("commit_headroom_bytes"),
            "immediate pre-spawn commit headroom",
        ) < int(policy["minimum_commit_headroom_before_spawn_bytes"]):
            failures.append("immediate_pre_spawn_commit_headroom")
        if _json_int(
            immediate.get("available_physical_bytes"),
            "immediate pre-spawn available physical",
        ) < int(policy["minimum_available_physical_before_spawn_bytes"]):
            failures.append("immediate_pre_spawn_available_physical")
    for key in peak_fields:
        if _json_int(peak.get(key), f"peak {key}") < 0:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"peak {key} negative")
    peak_checks = (
        ("tree_working_set_bytes", "tree_working_set_stop_bytes", "tree_ws"),
        (
            "tree_summed_process_lifetime_peak_working_set_bytes",
            "tree_working_set_stop_bytes",
            "tree_lifetime_peak_ws",
        ),
        ("tree_private_commit_bytes", "tree_private_stop_bytes", "tree_private"),
        ("tree_committed_pagefile_bytes", "tree_commit_stop_bytes", "tree_commit"),
        (
            "tree_summed_process_lifetime_peak_commit_bytes",
            "tree_commit_stop_bytes",
            "tree_lifetime_peak_commit",
        ),
    )
    for peak_key, policy_key, label in peak_checks:
        if _json_int(peak.get(peak_key), f"peak {peak_key}") > int(policy[policy_key]):
            failures.append(label)
    if _json_int(peak.get("system_commit_headroom_min_bytes"), "peak commit headroom") < int(
        policy["commit_headroom_floor_bytes"]
    ):
        failures.append("system_commit_headroom")
    if _json_int(peak.get("available_physical_min_bytes"), "peak available physical") < int(
        policy["available_physical_floor_bytes"]
    ):
        failures.append("available_physical")
    if isinstance(final, Mapping):
        if _json_int(final.get("commit_headroom_bytes"), "final commit headroom") < int(
            policy["commit_headroom_floor_bytes"]
        ):
            failures.append("final_commit_headroom")
        if _json_int(final.get("available_physical_bytes"), "final available physical") < int(
            policy["available_physical_floor_bytes"]
        ):
            failures.append("final_available_physical")
    resource_pass = not failures
    if "failure_code" in resource and resource.get("failure_code") != "BLOCKED_AV_BS_RESOURCE":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource failure code mismatch")
    if "failure_code" in resource and resource_pass:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "passing resource carries failure code")
    if resource.get("mandatory_resource_gate_pass") is not resource_pass:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource gate flag mismatch")
    for key in ("child_stdout_sha256", "child_stderr_sha256"):
        value = resource.get(key)
        if value is not None:
            _require_sha(value, f"resource {key}")
    return resource_pass, failures

def _validate_terminal_monitor_handshake_payload(
    value: object, certificates: list[object]
) -> None:
    fields = {
        "schema", "ready_marker_sha256", "ready_sample_perf_counter_ns",
        "ready_sample_utc", "completion_marker_sha256",
        "completion_perf_counter_ns", "completion_utc", "release_marker_sha256",
        "release_sample_perf_counter_ns", "release_sample_utc",
        "factor_interval_bracketed_by_actual_samples",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor handshake field set mismatch"
        )
    if value.get("schema") != "AV-BS1-h4-p0r-factor-monitor-handshake-v1":
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor handshake schema mismatch"
        )
    for key in (
        "ready_marker_sha256", "completion_marker_sha256", "release_marker_sha256"
    ):
        _require_sha(value.get(key), f"terminal factor handshake {key}")
    ready_ns = _json_int(
        value.get("ready_sample_perf_counter_ns"), "terminal factor ready sample"
    )
    completion_ns = _json_int(
        value.get("completion_perf_counter_ns"), "terminal factor completion sample"
    )
    release_ns = _json_int(
        value.get("release_sample_perf_counter_ns"), "terminal factor release sample"
    )
    ready_utc = _utc(value.get("ready_sample_utc"), "terminal factor ready UTC")
    completion_utc = _utc(
        value.get("completion_utc"), "terminal factor completion UTC"
    )
    release_utc = _utc(value.get("release_sample_utc"), "terminal factor release UTC")
    _require_bool(
        value.get("factor_interval_bracketed_by_actual_samples"),
        True,
        "terminal factor interval bracket",
    )
    if not (
        0 <= ready_ns <= completion_ns <= release_ns
        and ready_utc <= completion_utc <= release_utc
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor handshake chronology invalid"
        )
    for certificate in certificates:
        if not isinstance(certificate, Mapping):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "terminal factor certificate invalid"
            )
        started_ns = _json_int(
            certificate.get("factor_started_perf_counter_ns"),
            "terminal factor start sample",
        )
        returned_ns = _json_int(
            certificate.get("factor_returned_perf_counter_ns"),
            "terminal factor return sample",
        )
        started_utc = _utc(
            certificate.get("factor_started_utc"), "terminal factor started UTC"
        )
        returned_utc = _utc(
            certificate.get("factor_returned_utc"), "terminal factor returned UTC"
        )
        if not (
            ready_ns <= started_ns <= returned_ns <= release_ns
            and ready_utc <= started_utc <= returned_utc <= release_utc
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "terminal factor certificate is outside handshake interval",
            )


def _validate_factor_report(
    numerical: Mapping[str, object],
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    claim_path: Path,
    guard: Mapping[str, object],
    guard_path: Path,
    *,
    expected_count: int = 2,
    expected_status: str = "factor_certificates_complete",
    require_handshake: bool = True,
    validate_prefix_sidecars: bool = True,
    terminal_bindings: Mapping[str, object] | None = None,
) -> None:
    approved_order = ["A_background_II", "A_conductor_II"]
    if expected_count not in (1, 2):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor certificate prefix count invalid")
    review_token_sha256 = (
        _sha(TOKEN_PATH)
        if terminal_bindings is None
        else terminal_bindings["review_token_sha256"]
    )
    git_head = _git_head() if terminal_bindings is None else terminal_bindings["git_head"]
    guard_sha256 = (
        _sha(guard_path)
        if terminal_bindings is None
        else terminal_bindings["guard_sha256"]
    )
    guard_canonical_sha256 = (
        _canonical_sha(guard)
        if terminal_bindings is None
        else terminal_bindings["guard_canonical_sha256"]
    )
    claim_sha256 = (
        _sha(claim_path)
        if terminal_bindings is None
        else terminal_bindings["claim_sha256"]
    )
    claim_canonical_sha256 = (
        _canonical_sha(claim)
        if terminal_bindings is None
        else terminal_bindings["claim_canonical_sha256"]
    )
    exact = {"schema": NUMERICAL_SCHEMA, "program": PROGRAM, "case_id": CASE_ID,
             "stage": AUTHORIZED_STAGE, "status": expected_status,
             "review_token_id": token["review_token_id"], "review_token_sha256": review_token_sha256,
             "git_head": git_head,
             "p0r_preregistration_commit": token["p0r_preregistration_commit"],
             "bindings": manifest["bindings"],
             "manifest_payload_sha256": manifest["bindings"]["manifest_payload_sha256"],
             "matrix_contract_sha256": manifest["bindings"]["matrix_contract_sha256"],
             "resource_policy_sha256": manifest["resource_policy_sha256"],
             "execution_resource_scope_sha256": manifest["execution_resource_scope_sha256"],
             "guard_contract_sha256": guard_sha256,
             "guard_canonical_sha256": guard_canonical_sha256,
             "claim_sha256": claim_sha256,
             "claim_canonical_sha256": claim_canonical_sha256,
             "factor_order": approved_order[:expected_count],
             "factor_sequence_nonoverlap": True, "factorization_attempted": True,
             "factorization_performed": True,
             "physics_solve_performed": False, "next_stage_authorized": False}
    if set(numerical) != set(exact) | {
        "factor_certificates", "forbidden_operation_flags", "monitor_handshake"
    }:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "factor report field set mismatch"
        )
    for key, value in exact.items():
        if not _strict_json_equal(numerical.get(key), value):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"factor report {key} mismatch")
    for key in (
        "guard_contract_sha256", "guard_canonical_sha256", "claim_sha256",
        "claim_canonical_sha256", "resource_policy_sha256",
        "execution_resource_scope_sha256",
    ):
        _require_sha(numerical.get(key), f"factor {key}")
    flags = numerical.get("forbidden_operation_flags")
    if (
        not isinstance(flags, Mapping)
        or not _strict_json_equal(flags, manifest["forbidden_operations"])
        or any(type(value) is not bool or value for value in flags.values())
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor forbidden-operation flags are not all false")
    certs = numerical.get("factor_certificates")
    if not isinstance(certs, list) or len(certs) != expected_count:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor certificate count mismatch")
    matrix_records = manifest["p0r_parent"]["matrix_inputs"]["matrices"]
    cap = int(manifest["resource_policy"]["one_factor_hard_cap_bytes"])
    for cert, expected_name in zip(certs, exact["factor_order"], strict=True):
        if not isinstance(cert, Mapping) or cert.get("name") != expected_name:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor certificate name mismatch")
        if set(cert) != FACTOR_CERTIFICATE_FIELDS:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"factor {expected_name} certificate field set mismatch",
            )
        expected_matrix = matrix_records[expected_name]
        expected_cert = {
            "input_shape": expected_matrix["shape"],
            "input_dtype": "complex128",
            "input_nnz": expected_matrix["equilibrated_nnz"],
            "input_sparse_sha256": expected_matrix["equilibrated_sha256"],
            "input_sparse_array_bytes": expected_matrix[
                "equilibrated_sparse_array_bytes"
            ],
            "raw_input_sparse_sha256": expected_matrix["sha256"],
            "row_scale_sha256": expected_matrix["row_scale_sha256"],
            "column_scale_sha256": expected_matrix["column_scale_sha256"],
            "equilibrated_sparse_sha256": expected_matrix["equilibrated_sha256"],
            "permc_spec": "COLAMD",
            "diag_pivot_thresh": 1.0,
            "superlu_equil": False,
            "rhs_count": 0,
            "L_shape": expected_matrix["shape"],
            "L_dtype": "complex128",
            "L_storage_canonical": True,
            "L_explicit_zero_count": 0,
            "L_lower_triangular": True,
            "L_unit_diagonal": True,
            "U_shape": expected_matrix["shape"],
            "U_dtype": "complex128",
            "U_storage_canonical": True,
            "U_explicit_zero_count": 0,
            "U_upper_triangular": True,
            "U_diagonal_dtype": "complex128",
            "U_diagonal_count": expected_matrix["shape"][0],
            "U_diagonal_finite_nonzero": True,
            "perm_r_dtype": "int64",
            "perm_r_count": expected_matrix["shape"][0],
            "perm_r_bijective": True,
            "perm_c_dtype": "int64",
            "perm_c_count": expected_matrix["shape"][0],
            "perm_c_bijective": True,
            "one_factor_hard_cap_bytes": cap,
            "native_pre_materialization_cap_pass": True,
            "factor_bytes_gate_pass": True,
            "factorization_attempted": True,
            "factorization_performed": True,
            "factor_solve_called": False,
        }
        for key, value in expected_cert.items():
            if not _strict_json_equal(cert.get(key), value):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    f"factor {expected_name} {key} mismatch",
                )
        for key in ("input_sparse_sha256", "raw_input_sparse_sha256",
                    "row_scale_sha256", "column_scale_sha256",
                    "equilibrated_sparse_sha256", "L_sha256", "U_sha256",
                    "L_storage_sha256", "U_storage_sha256",
                    "U_diagonal_sha256", "perm_r_sha256", "perm_c_sha256"):
            _require_sha(cert.get(key), f"factor {expected_name} {key}")
        integer_fields = (
            "L_nnz", "U_nnz", "native_factor_nnz",
            "native_portable_factor_bytes", "input_nnz", "input_sparse_array_bytes",
            "exported_factor_bytes",
            "portable_factor_bytes", "factor_started_perf_counter_ns",
            "factor_returned_perf_counter_ns", "factor_wall_ns",
            "factor_objects_cleanup_started_perf_counter_ns",
            "factor_objects_cleanup_completed_perf_counter_ns",
            "matrix_cleanup_started_perf_counter_ns",
            "matrix_cleanup_completed_perf_counter_ns",
        )
        if any(type(cert.get(key)) is not int or cert[key] < 0 for key in integer_fields):
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"factor {expected_name} byte/certificate gate failed")
        n = int(expected_matrix["shape"][0])
        l_nnz = int(cert["L_nnz"])
        u_nnz = int(cert["U_nnz"])
        portable = 24 * (l_nnz + u_nnz) + 8 * (4 * n + 2)
        array_bytes = cert.get("factor_array_bytes")
        required_arrays = {
            "L_data", "L_indices", "L_indptr", "U_data", "U_indices",
            "U_indptr", "perm_r", "perm_c",
        }
        if (
            not isinstance(array_bytes, Mapping)
            or set(array_bytes) != required_arrays
            or any(type(value) is not int or value < 0 for value in array_bytes.values())
            or cert["exported_factor_bytes"] != sum(array_bytes.values())
        ):
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"factor {expected_name} exact-byte gate failed")
        try:
            dtype_fields = (
                "L_indices_dtype", "L_indptr_dtype", "U_indices_dtype",
                "U_indptr_dtype", "perm_r_storage_dtype", "perm_c_storage_dtype",
            )
            raw_dtypes = [cert.get(key) for key in dtype_fields]
            if any(not isinstance(value, str) for value in raw_dtypes):
                raise TypeError("factor storage dtype must be a canonical string")
            storage_dtypes = tuple(np.dtype(value) for value in raw_dtypes)
            if any(
                raw != dtype.str or dtype.str not in ("<i4", "<i8")
                for raw, dtype in zip(raw_dtypes, storage_dtypes, strict=True)
            ):
                raise TypeError("factor storage dtype spelling/endian is not canonical")
            (
                l_index_dtype, l_indptr_dtype, u_index_dtype, u_indptr_dtype,
                perm_r_dtype, perm_c_dtype,
            ) = storage_dtypes
            l_index_bytes = l_index_dtype.itemsize
            l_indptr_bytes = l_indptr_dtype.itemsize
            u_index_bytes = u_index_dtype.itemsize
            u_indptr_bytes = u_indptr_dtype.itemsize
            perm_r_bytes = perm_r_dtype.itemsize
            perm_c_bytes = perm_c_dtype.itemsize
        except (TypeError, ValueError) as exc:
            raise AvBsError(
                "BLOCKED_AV_BS_FACTOR", f"factor {expected_name} storage dtype invalid"
            ) from exc
        if (
            any(dtype.kind != "i" or dtype.itemsize not in (4, 8) for dtype in storage_dtypes)
            or l_index_bytes not in (4, 8)
            or l_indptr_bytes not in (4, 8)
            or u_index_bytes not in (4, 8)
            or u_indptr_bytes not in (4, 8)
            or perm_r_bytes not in (4, 8)
            or perm_c_bytes not in (4, 8)
            or array_bytes["L_data"] != 16 * l_nnz
            or array_bytes["L_indices"] != l_index_bytes * l_nnz
            or array_bytes["L_indptr"] != l_indptr_bytes * (n + 1)
            or array_bytes["U_data"] != 16 * u_nnz
            or array_bytes["U_indices"] != u_index_bytes * u_nnz
            or array_bytes["U_indptr"] != u_indptr_bytes * (n + 1)
            or array_bytes["perm_r"] != perm_r_bytes * n
            or array_bytes["perm_c"] != perm_c_bytes * n
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_FACTOR",
                f"factor {expected_name} factor-array byte formula failed",
            )
        if (
            l_nnz <= 0
            or u_nnz <= 0
            or cert["native_factor_nnz"] != l_nnz + u_nnz
            or cert["native_portable_factor_bytes"] != portable
            or cert["portable_factor_bytes"] != portable
            or cert["exported_factor_bytes"] <= 0
            or max(cert["exported_factor_bytes"], portable) > cap
            or type(cert.get("stored_fill_ratio")) is not float
            or type(cert.get("fill_ratio")) is not float
            or cert.get("stored_fill_ratio") != (l_nnz + u_nnz) / cert["input_nnz"]
            or cert.get("fill_ratio") != cert.get("stored_fill_ratio")
        ):
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"factor {expected_name} size gate failed")
        diagonal_min = cert.get("U_diagonal_abs_min")
        diagonal_max = cert.get("U_diagonal_abs_max")
        if (
            not isinstance(diagonal_min, float)
            or not isinstance(diagonal_max, float)
            or not math.isfinite(diagonal_min)
            or not math.isfinite(diagonal_max)
            or diagonal_min <= 0.0
            or diagonal_max < diagonal_min
        ):
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"factor {expected_name} diagonal gate failed")
        wall_seconds = cert.get("factor_wall_seconds")
        if (
            not isinstance(wall_seconds, float)
            or not math.isfinite(wall_seconds)
            or wall_seconds != cert["factor_wall_ns"] / 1.0e9
        ):
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"factor {expected_name} wall-time gate failed")
        ordering = (
            "construction_started_perf_counter_ns",
            "construction_cleanup_completed_perf_counter_ns",
            "factor_started_perf_counter_ns",
            "factor_returned_perf_counter_ns",
            "factor_objects_cleanup_started_perf_counter_ns",
            "factor_objects_cleanup_completed_perf_counter_ns",
            "matrix_cleanup_started_perf_counter_ns",
            "matrix_cleanup_completed_perf_counter_ns",
        )
        if any(type(cert.get(key)) is not int for key in ordering) or any(
            cert[left] > cert[right] for left, right in zip(ordering, ordering[1:])
        ) or cert["factor_wall_ns"] != cert["factor_returned_perf_counter_ns"] - cert["factor_started_perf_counter_ns"]:
            raise AvBsError("BLOCKED_AV_BS_FACTOR", f"factor {expected_name} timing gate failed")
        for key in (
            "construction_started_utc", "construction_cleanup_completed_utc",
            "factor_started_utc", "factor_returned_utc",
            "factor_objects_cleanup_started_utc",
            "factor_objects_cleanup_completed_utc", "matrix_cleanup_started_utc",
            "matrix_cleanup_completed_utc",
        ):
            _utc(cert.get(key), f"factor {expected_name} {key}")
    if len(certs) == 2 and certs[0]["matrix_cleanup_completed_perf_counter_ns"] > certs[1]["factor_started_perf_counter_ns"]:
        raise AvBsError("BLOCKED_AV_BS_FACTOR", "factor certificates overlap")
    if require_handshake:
        if terminal_bindings is None:
            _validate_monitor_handshake(
                numerical.get("monitor_handshake"), guard_path, claim_path, certs
            )
        else:
            _validate_terminal_monitor_handshake_payload(
                numerical.get("monitor_handshake"), certs
            )
    elif numerical.get("monitor_handshake") is not None:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "prefix checkpoint cannot claim monitor release"
        )
    if validate_prefix_sidecars:
        for count in range(1, expected_count + 1):
            prefix = _validate_factor_prefix_checkpoint(
                _factor_prefix_path(guard_path, count),
                count,
                token,
                manifest,
                claim,
                claim_path,
                guard,
                guard_path,
            )
            if not _strict_json_equal(
                prefix.get("factor_certificates"), certs[:count]
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix certificate mismatch"
                )


def _validate_monitor_handshake(
    value: object,
    guard_path: Path,
    claim_path: Path,
    certificates: list[object],
) -> None:
    if not isinstance(value, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor monitor handshake missing")
    fields = {
        "schema", "ready_marker_sha256", "ready_sample_perf_counter_ns",
        "ready_sample_utc", "completion_marker_sha256",
        "completion_perf_counter_ns", "completion_utc", "release_marker_sha256",
        "release_sample_perf_counter_ns", "release_sample_utc",
        "factor_interval_bracketed_by_actual_samples",
    }
    if set(value) != fields or value.get("schema") != "AV-BS1-h4-p0r-factor-monitor-handshake-v1":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor monitor handshake field set mismatch")
    ready_path, completion_path, release_path = _validated_monitor_paths(
        guard_path,
        guard_path.with_name("monitor-ready.json"),
        guard_path.with_name("factor-complete.json"),
        guard_path.with_name("monitor-release.json"),
    )
    for key, path in (
        ("ready_marker_sha256", ready_path),
        ("completion_marker_sha256", completion_path),
        ("release_marker_sha256", release_path),
    ):
        if not path.is_file() or value.get(key) != _sha(path):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"factor monitor {key} mismatch")
    ready = _read(ready_path, "monitor-ready marker")
    _validate_ready_marker(ready, claim_path)
    completion = _read(completion_path, "factor-complete marker")
    release = _read(release_path, "monitor-release marker")
    expected_completion = {
        "schema": "AV-BS1-h4-p0r-factor-complete-v1",
        "claim_sha256": _sha(claim_path),
        "child_process_id": ready["child_process_id"],
        "factor_certificates_sha256": _canonical_sha(certificates),
    }
    if set(completion) != set(expected_completion) | {
        "completed_factors", "completed_perf_counter_ns", "completed_utc"
    }:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor-complete field set mismatch")
    for key, expected in expected_completion.items():
        if not _strict_json_equal(completion.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"factor-complete {key} mismatch")
    completed_factors = completion.get("completed_factors")
    certificate_names = [
        certificate.get("name") for certificate in certificates
        if isinstance(certificate, Mapping)
    ]
    approved = ["A_background_II", "A_conductor_II"]
    if (
        not isinstance(completed_factors, list)
        or completed_factors != approved[:len(completed_factors)]
        or len(completed_factors) > 2
        or completed_factors[:len(certificate_names)] != certificate_names
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor-complete phase mismatch")
    expected_release = {
        "schema": "AV-BS1-h4-p0r-monitor-release-v1",
        "claim_sha256": _sha(claim_path),
        "completion_marker_sha256": _sha(completion_path),
        "child_process_id": ready["child_process_id"],
    }
    if set(release) != set(expected_release) | {"sample_perf_counter_ns", "sample_utc"}:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "monitor-release field set mismatch")
    for key, expected in expected_release.items():
        if not _strict_json_equal(release.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"monitor-release {key} mismatch")
    ready_ns = _json_int(ready.get("sample_perf_counter_ns"), "monitor-ready sample")
    completion_ns = _json_int(
        completion.get("completed_perf_counter_ns"), "factor-complete sample"
    )
    release_ns = _json_int(release.get("sample_perf_counter_ns"), "monitor-release sample")
    if not (0 <= ready_ns <= completion_ns <= release_ns):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor monitor interval reversed")
    ready_utc = _utc(ready.get("sample_utc"), "monitor-ready sample_utc")
    completion_utc = _utc(completion.get("completed_utc"), "factor-complete completed_utc")
    release_utc = _utc(release.get("sample_utc"), "monitor-release sample_utc")
    if not (ready_utc <= completion_utc <= release_utc):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor monitor UTC interval reversed")
    for certificate in certificates:
        if not isinstance(certificate, Mapping):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor certificate invalid")
        started_ns = _json_int(
            certificate.get("factor_started_perf_counter_ns"), "factor start sample"
        )
        returned_ns = _json_int(
            certificate.get("factor_returned_perf_counter_ns"), "factor return sample"
        )
        started_utc = _utc(certificate.get("factor_started_utc"), "factor started_utc")
        returned_utc = _utc(certificate.get("factor_returned_utc"), "factor returned_utc")
        if not (
            ready_ns <= started_ns <= returned_ns <= release_ns
            and ready_utc <= started_utc <= returned_utc <= release_utc
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "actual monitor samples do not bracket factor certificate",
            )
    expected_handshake = {
        "schema": "AV-BS1-h4-p0r-factor-monitor-handshake-v1",
        "ready_marker_sha256": _sha(ready_path),
        "ready_sample_perf_counter_ns": ready_ns,
        "ready_sample_utc": ready["sample_utc"],
        "completion_marker_sha256": _sha(completion_path),
        "completion_perf_counter_ns": completion_ns,
        "completion_utc": completion["completed_utc"],
        "release_marker_sha256": _sha(release_path),
        "release_sample_perf_counter_ns": release_ns,
        "release_sample_utc": release["sample_utc"],
        "factor_interval_bracketed_by_actual_samples": True,
    }
    if not _strict_json_equal(value, expected_handshake):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor monitor handshake mismatch")


def _validate_partial_factor_certificates(
    failure: Mapping[str, object],
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    claim_path: Path,
    guard: Mapping[str, object],
    guard_path: Path,
    *,
    terminal_bindings: Mapping[str, object] | None = None,
) -> None:
    certs = failure.get("factor_certificates")
    if not isinstance(certs, list):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure factor certificates missing")
    if not certs:
        return
    completed = failure.get("completed_factors")
    if not isinstance(completed, list) or len(certs) > len(completed):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "partial certificate phase mismatch")
    approved = ["A_background_II", "A_conductor_II"]
    names = [cert.get("name") if isinstance(cert, Mapping) else None for cert in certs]
    if names != approved[:len(certs)]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "partial certificate order mismatch")
    numerical = {
        "schema": NUMERICAL_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "status": "factor_certificates_complete",
        "review_token_id": token["review_token_id"],
        "review_token_sha256": (
            _sha(TOKEN_PATH)
            if terminal_bindings is None
            else terminal_bindings["review_token_sha256"]
        ),
        "git_head": (
            _git_head()
            if terminal_bindings is None
            else terminal_bindings["git_head"]
        ),
        "p0r_preregistration_commit": token["p0r_preregistration_commit"],
        "bindings": manifest["bindings"],
        "manifest_payload_sha256": manifest["bindings"]["manifest_payload_sha256"],
        "matrix_contract_sha256": manifest["bindings"]["matrix_contract_sha256"],
        "resource_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "guard_contract_sha256": (
            _sha(guard_path)
            if terminal_bindings is None
            else terminal_bindings["guard_sha256"]
        ),
        "guard_canonical_sha256": (
            _canonical_sha(guard)
            if terminal_bindings is None
            else terminal_bindings["guard_canonical_sha256"]
        ),
        "claim_sha256": (
            _sha(claim_path)
            if terminal_bindings is None
            else terminal_bindings["claim_sha256"]
        ),
        "claim_canonical_sha256": (
            _canonical_sha(claim)
            if terminal_bindings is None
            else terminal_bindings["claim_canonical_sha256"]
        ),
        "factor_order": approved[:len(certs)],
        "factor_certificates": certs,
        "factor_sequence_nonoverlap": True,
        "monitor_handshake": failure.get("monitor_handshake"),
        "factorization_attempted": True,
        "factorization_performed": True,
        "physics_solve_performed": False,
        "forbidden_operation_flags": manifest["forbidden_operations"],
        "next_stage_authorized": False,
    }
    _validate_factor_report(
        numerical,
        token,
        manifest,
        claim,
        claim_path,
        guard,
        guard_path,
        expected_count=len(certs),
        validate_prefix_sidecars=terminal_bindings is None,
        terminal_bindings=terminal_bindings,
    )

def _child_outcome(
    numerical: Mapping[str, object] | None,
    child_exit: int | None,
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    claim_path: Path,
    guard: Mapping[str, object],
    guard_path: Path,
) -> tuple[bool, list[str], bool | None, bool | None, list[str], object]:
    if numerical is None:
        return False, ([] if child_exit == 2 else ["BLOCKED_AV_BS_RESULT_SCHEMA"]), None, None, [], None
    schema = numerical.get("schema")
    if schema == NUMERICAL_SCHEMA:
        _validate_factor_report(
            numerical, token, manifest, claim, claim_path, guard, guard_path
        )
        codes = [] if child_exit == 0 else ["BLOCKED_AV_BS_RESULT_SCHEMA"]
        return True, codes, True, True, ["A_background_II", "A_conductor_II"], None
    if schema == FAILURE_SCHEMA:
        codes = _validate_failure_payload(
            numerical,
            expected_stage=AUTHORIZED_STAGE,
            expected_scope_sha256=manifest["execution_resource_scope_sha256"],
        )
        _validate_partial_factor_certificates(
            numerical, token, manifest, claim, claim_path, guard, guard_path
        )
        if child_exit != 2 and "BLOCKED_AV_BS_RESULT_SCHEMA" not in codes:
            codes.append("BLOCKED_AV_BS_RESULT_SCHEMA")
        return (
            False,
            codes,
            numerical["factorization_attempted"],
            numerical["factorization_performed"],
            list(numerical["completed_factors"]),
            numerical.get("active_factor"),
        )
    raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child factor schema mismatch")


def _factor_prefix_evidence(
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    claim_path: Path,
    guard: Mapping[str, object],
    guard_path: Path,
) -> list[Mapping[str, object]]:
    evidence: list[Mapping[str, object]] = []
    recovered: Mapping[str, object] | None = None
    for count in (1, 2):
        path = _factor_prefix_path(guard_path, count)
        if not path.is_file():
            if count == 1 and _factor_prefix_path(guard_path, 2).is_file():
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix two lacks prefix one"
                )
            break
        candidate = _validate_factor_prefix_checkpoint(
            path, count, token, manifest, claim, claim_path, guard, guard_path
        )
        if recovered is not None and not _strict_json_equal(
            candidate.get("factor_certificates")[: count - 1],
            recovered.get("factor_certificates"),
        ):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix chain mismatch")
        _, payload_sha = _read_wrapper(path, f"factor prefix {count}")
        evidence.append(
            {
                "count": count,
                "file_sha256": _sha(path),
                "payload_sha256": payload_sha,
                "payload": candidate,
            }
        )
        recovered = candidate
    return evidence


def _recover_factor_prefix(
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    claim_path: Path,
    guard: Mapping[str, object],
    guard_path: Path,
) -> Mapping[str, object] | None:
    evidence = _factor_prefix_evidence(
        token, manifest, claim, claim_path, guard, guard_path
    )
    return None if not evidence else evidence[-1]["payload"]


def _validate_prefix_consistency(
    numerical: Mapping[str, object] | None,
    prefix_certificates: list[object],
    prefix_order: list[object],
) -> None:
    """A durable prefix must be an exact prefix of any later valid child report."""
    if numerical is None or not prefix_certificates:
        return
    if numerical.get("schema") == NUMERICAL_SCHEMA:
        numerical_certificates = numerical.get("factor_certificates")
        numerical_order = numerical.get("factor_order")
    elif numerical.get("schema") == FAILURE_SCHEMA:
        numerical_certificates = numerical.get("factor_certificates")
        completed = numerical.get("completed_factors")
        numerical_order = (
            completed[: len(numerical_certificates)]
            if isinstance(completed, list) and isinstance(numerical_certificates, list)
            else None
        )
    else:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor prefix child schema mismatch")
    if (
        not isinstance(numerical_certificates, list)
        or not isinstance(numerical_order, list)
        or len(prefix_certificates) > len(numerical_certificates)
        or not _strict_json_equal(
            numerical_certificates[: len(prefix_certificates)], prefix_certificates
        )
        or not _strict_json_equal(
            numerical_order[: len(prefix_order)], prefix_order
        )
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "factor prefix contradicts later child evidence",
        )


def _validate_factor_monitor_interval(
    numerical: Mapping[str, object] | None,
    resource: Mapping[str, object],
    fallback_certificates: list[object] | None = None,
) -> None:
    certificates: object = fallback_certificates or []
    if numerical is not None and numerical.get("schema") in {
        NUMERICAL_SCHEMA, FAILURE_SCHEMA
    }:
        numerical_certificates = numerical.get("factor_certificates")
        if fallback_certificates:
            if (
                not isinstance(numerical_certificates, list)
                or len(fallback_certificates) > len(numerical_certificates)
                or not _strict_json_equal(
                    numerical_certificates[: len(fallback_certificates)],
                    fallback_certificates,
                )
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "factor prefix is not covered by child evidence",
                )
        certificates = numerical_certificates
    if not isinstance(certificates, list):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor certificates missing")
    if not certificates:
        return
    first_ns = _json_int(
        resource.get("first_child_visible_sample_perf_counter_ns"),
        "resource first child sample",
    )
    last_ns = _json_int(
        resource.get("last_child_visible_sample_perf_counter_ns"),
        "resource last child sample",
    )
    first_utc = _utc(
        resource.get("first_child_visible_sample_utc"),
        "resource first child sample UTC",
    )
    last_utc = _utc(
        resource.get("last_child_visible_sample_utc"),
        "resource last child sample UTC",
    )
    if numerical is not None:
        handshake = numerical.get("monitor_handshake")
        if not isinstance(handshake, Mapping):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor monitor handshake missing")
        if (
            resource.get("monitor_ready_marker_sha256")
            != handshake.get("ready_marker_sha256")
            or resource.get("factor_complete_marker_sha256")
            != handshake.get("completion_marker_sha256")
            or resource.get("monitor_release_marker_sha256")
            != handshake.get("release_marker_sha256")
            or first_ns != handshake.get("ready_sample_perf_counter_ns")
            or first_utc
            != _utc(handshake.get("ready_sample_utc"), "handshake ready sample UTC")
            or last_ns < _json_int(
                handshake.get("release_sample_perf_counter_ns"),
                "handshake release sample",
            )
            or last_utc
            < _utc(handshake.get("release_sample_utc"), "handshake release sample UTC")
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA", "resource/handshake sample binding mismatch"
            )
    for certificate in certificates:
        if not isinstance(certificate, Mapping):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "factor certificate invalid")
        factor_started = _utc(
            certificate.get("factor_started_utc"), "factor started_utc"
        )
        factor_returned = _utc(
            certificate.get("factor_returned_utc"), "factor returned_utc"
        )
        factor_started_ns = _json_int(
            certificate.get("factor_started_perf_counter_ns"), "factor start sample"
        )
        factor_returned_ns = _json_int(
            certificate.get("factor_returned_perf_counter_ns"), "factor return sample"
        )
        if not (
            first_ns <= factor_started_ns <= factor_returned_ns <= last_ns
            and first_utc <= factor_started <= factor_returned <= last_utc
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "actual resource samples do not cover factor interval",
            )


def finalize(
    resource_path: Path,
    token_path: Path,
    guard_path: Path,
    nonce: str,
    claim_path: Path,
    stdout_path: Path | None,
) -> Mapping[str, object]:
    _EXECUTION_PHASE["claim_validated"] = False
    manifest = run_manifest()
    token = _validate_token(token_path, manifest, check_expiry=False)
    claim = _validate_claim(claim_path, token, manifest)
    _EXECUTION_PHASE["claim_validated"] = True
    guard = _validate_guard(guard_path, nonce, token, manifest, claim_path)
    resource = _read(resource_path, "resource")
    resource_pass, resource_failures = _resource_gate(
        resource,
        token,
        manifest,
        guard,
        claim,
        guard_path=guard_path,
        claim_path=claim_path,
    )
    child_exit_value = resource.get("child_exit_code")
    child_exit = None if child_exit_value is None else _json_int(
        child_exit_value, "resource child exit code"
    )
    numerical_wrapper: Mapping[str, object] | None = None
    numerical: Mapping[str, object] | None = None
    if stdout_path is not None and stdout_path.is_file():
        if resource.get("child_stdout_sha256") != _sha(stdout_path):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child stdout checksum mismatch")
        numerical, numerical_checksum = _read_wrapper(stdout_path, "child stdout")
        numerical_wrapper = {"payload": numerical, "payload_sha256": numerical_checksum}
    elif resource.get("child_stdout_sha256") is not None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource references missing child stdout")

    numerical_pass, child_codes, attempted, performed, completed, active = _child_outcome(
        numerical, child_exit, token, manifest, claim, claim_path, guard, guard_path
    )
    prefix_evidence = _factor_prefix_evidence(
        token, manifest, claim, claim_path, guard, guard_path
    )
    prefix = None if not prefix_evidence else prefix_evidence[-1]["payload"]
    prefix_certificates = (
        [] if prefix is None else list(prefix.get("factor_certificates", []))
    )
    prefix_order = [] if prefix is None else list(prefix.get("factor_order", []))
    _validate_prefix_consistency(numerical, prefix_certificates, prefix_order)
    if numerical is None and len(prefix_certificates) > len(completed):
        attempted = True
        performed = True
        completed = prefix_order
        active = None
    _validate_factor_monitor_interval(numerical, resource, prefix_certificates)
    failure_codes: list[str] = []
    if not resource_pass:
        failure_codes.append("BLOCKED_AV_BS_RESOURCE")
    failure_codes.extend(child_codes)
    failure_codes = list(dict.fromkeys(failure_codes))
    mandatory = bool(resource_pass and numerical_pass and child_exit == 0 and not failure_codes)
    if not mandatory and not failure_codes:
        failure_codes.append("BLOCKED_AV_BS_FACTOR")
    status = SUCCESS_STATUS if mandatory else failure_codes[0]
    if numerical is None:
        factor_order = prefix_order
        factor_certificates = prefix_certificates
    elif numerical.get("schema") == FAILURE_SCHEMA:
        factor_certificates = list(numerical.get("factor_certificates", []))
        factor_order = list(numerical.get("completed_factors", []))[:len(factor_certificates)]
    else:
        factor_order = list(numerical.get("factor_order", []))
        factor_certificates = list(numerical.get("factor_certificates", []))
    return {
        "schema": RESULT_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "status": status,
        "authorization_state": "claimed_attempt_complete_pending_tombstone",
        "token_consumption_required": True,
        "git_head": _git_head(),
        "p0r_preregistration_commit": token["p0r_preregistration_commit"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _sha(token_path),
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "outer_observer_handshake_prefix_sha256": claim[
            "outer_observer_handshake_prefix_sha256"
        ],
        "expected_terminal_seal_relative_path": token[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "terminal_seal_state": "pending_outer_observed_inner_exit",
        "terminal_evidence_complete": False,
        "authoritative_stage_pass": False,
        "bindings": manifest["bindings"],
        "matrix_inputs": manifest["p0r_parent"]["matrix_inputs"],
        "factor_order": factor_order,
        "factor_certificates": factor_certificates,
        "factor_prefix_evidence": prefix_evidence,
        "factor_sequence_nonoverlap": True if factor_certificates else None,
        "numerical": numerical,
        "numerical_payload_sha256": None if numerical_wrapper is None else numerical_wrapper["payload_sha256"],
        "resource": resource,
        "resource_report_sha256": _sha(resource_path),
        "resource_gate_recheck_failures": resource_failures,
        "guard_contract_sha256": _sha(guard_path),
        "guard_canonical_sha256": _canonical_sha(guard),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _sha(claim_path),
        "claim_canonical_sha256": _canonical_sha(claim),
        "child_stdout_sha256": None if stdout_path is None or not stdout_path.is_file() else _sha(stdout_path),
        "child_exit_code": child_exit,
        "mandatory_stage_pass": mandatory,
        "failure_codes": [] if mandatory else failure_codes,
        "factorization_attempted": attempted,
        "factorization_performed": performed,
        "active_factor": active,
        "completed_factors": completed,
        "physics_solve_performed": False,
        "forbidden_operation_flags": manifest["forbidden_operations"],
        "next_stage_authorized": False,
    }

def _atomic_replace(path: Path,payload:Mapping[str,object])->None:
    tmp=None
    try:
        with tempfile.NamedTemporaryFile("wb",dir=path.parent,prefix="."+path.name,suffix=".tmp",delete=False) as out:
            tmp=Path(out.name);out.write(h1.canonical_bytes(payload));out.flush();os.fsync(out.fileno())
        os.replace(tmp,path);tmp=None
    except OSError as exc: raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA","atomic tombstone replacement failed") from exc
    finally:
        if tmp: tmp.unlink(missing_ok=True)

def _validate_failure_payload(
    payload: Mapping[str, object], *, expected_stage: str,
    expected_scope_sha256: str | None = None,
    expected_authorization_state: str | None = None,
) -> list[str]:
    scope_sha256 = (
        _canonical_sha(_execution_resource_scope())
        if expected_scope_sha256 is None
        else expected_scope_sha256
    )
    _require_sha(scope_sha256, "failure execution resource scope")
    if set(payload) != {
        "schema", "program", "case_id", "stage", "status",
        "authorization_state", "mandatory_stage_pass", "failure_codes",
        "detail", "factorization_attempted", "factorization_performed",
        "active_factor", "completed_factors", "factor_certificates",
        "monitor_handshake",
        "execution_resource_scope_sha256",
        "physics_solve_performed",
        "next_stage_authorized",
    }:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "failure payload field set mismatch"
        )
    if (
        payload.get("schema") != FAILURE_SCHEMA
        or payload.get("program") != PROGRAM
        or payload.get("case_id") != CASE_ID
        or payload.get("stage") != expected_stage
        or payload.get("execution_resource_scope_sha256") != scope_sha256
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure schema mismatch")
    codes = payload.get("failure_codes")
    if not isinstance(codes, list) or not codes or len(codes) != len(set(codes)) or any(code not in FAILURE_CODES for code in codes):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure code allowlist mismatch")
    if payload.get("status") != codes[0] or payload.get("mandatory_stage_pass") is not False or payload.get("next_stage_authorized") is not False or not isinstance(payload.get("detail"), str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure payload scope mismatch")
    if payload.get("physics_solve_performed") is not False:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure physics provenance mismatch")
    if expected_stage in {
        "finalize-primary-h4-p0r",
        "consume-primary-h4-p0r-token",
    }:
        authorization = payload.get("authorization_state")
        if expected_authorization_state is not None and authorization != expected_authorization_state:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "finalizer failure authorization mismatch",
            )
        if authorization == "claimed_attempt_failed":
            expected_attempted = expected_performed = None
        elif authorization == "not_authorized":
            expected_attempted = expected_performed = False
        else:
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "finalizer failure authorization is invalid",
            )
        if (
            payload.get("factorization_attempted") is not expected_attempted
            or payload.get("factorization_performed") is not expected_performed
            or payload.get("active_factor") is not None
            or payload.get("completed_factors") != []
            or payload.get("factor_certificates") != []
            or payload.get("monitor_handshake") is not None
        ):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer failure provenance mismatch")
        return list(codes)
    if payload.get("authorization_state") != "claimed_attempt_failed":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure authorization mismatch")
    attempted = payload.get("factorization_attempted")
    performed = payload.get("factorization_performed")
    completed = payload.get("completed_factors")
    certificates = payload.get("factor_certificates")
    handshake = payload.get("monitor_handshake")
    if type(attempted) is not bool or (performed is not False and performed is not None and performed is not True) or not isinstance(completed, list) or not isinstance(certificates, list):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure factor phase missing")
    approved = ["A_background_II", "A_conductor_II"]
    if completed != approved[:len(completed)] or len(completed) > 2 or payload.get("active_factor") is not None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure factor phase order mismatch")
    if (not attempted and (performed is not False or completed)) or (attempted and performed is False) or (attempted and performed is None and completed) or (performed is True and not completed):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure factor phase truth mismatch")
    if len(certificates) > len(completed):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure certificate phase mismatch")
    if certificates and not isinstance(handshake, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "failure monitor handshake missing")
    return list(codes)


def _validate_result_payload(
    payload: Mapping[str, object],
    token: Mapping[str, object],
    manifest: Mapping[str, object],
    claim: Mapping[str, object],
    claim_path: Path,
    guard: Mapping[str, object],
    guard_path: Path,
    resource: Mapping[str, object],
    resource_path: Path,
) -> None:
    allowed = {
        "schema", "program", "case_id", "stage", "status", "authorization_state",
        "token_consumption_required", "git_head", "p0r_preregistration_commit",
        "review_token_id", "review_token_sha256", "bindings", "matrix_inputs",
        "execution_resource_scope_sha256",
        "outer_observer_contract_sha256",
        "outer_observer_handshake_prefix_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "terminal_seal_state", "terminal_evidence_complete",
        "authoritative_stage_pass",
        "factor_order", "factor_certificates", "factor_prefix_evidence",
        "factor_sequence_nonoverlap",
        "numerical", "numerical_payload_sha256", "resource", "resource_report_sha256",
        "resource_gate_recheck_failures", "guard_contract_sha256",
        "guard_canonical_sha256", "claim_relative_path", "claim_sha256",
        "claim_canonical_sha256", "child_stdout_sha256", "child_exit_code",
        "mandatory_stage_pass", "failure_codes", "factorization_attempted",
        "factorization_performed", "active_factor", "completed_factors",
        "physics_solve_performed", "forbidden_operation_flags", "next_stage_authorized",
    }
    if set(payload) != allowed:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "result field set mismatch")
    exact = {
        "schema": RESULT_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": AUTHORIZED_STAGE,
        "authorization_state": "claimed_attempt_complete_pending_tombstone",
        "token_consumption_required": True,
        "git_head": _git_head(),
        "p0r_preregistration_commit": token["p0r_preregistration_commit"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _sha(TOKEN_PATH),
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "outer_observer_handshake_prefix_sha256": claim[
            "outer_observer_handshake_prefix_sha256"
        ],
        "expected_terminal_seal_relative_path": token[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "terminal_seal_state": "pending_outer_observed_inner_exit",
        "terminal_evidence_complete": False,
        "authoritative_stage_pass": False,
        "bindings": manifest["bindings"],
        "matrix_inputs": manifest["p0r_parent"]["matrix_inputs"],
        "resource": resource,
        "resource_report_sha256": _sha(resource_path),
        "guard_contract_sha256": _sha(guard_path),
        "guard_canonical_sha256": _canonical_sha(guard),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _sha(claim_path),
        "claim_canonical_sha256": _canonical_sha(claim),
        "physics_solve_performed": False,
        "forbidden_operation_flags": manifest["forbidden_operations"],
        "next_stage_authorized": False,
    }
    for key, expected in exact.items():
        if not _strict_json_equal(payload.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"result {key} mismatch")
    resource_pass, resource_failures = _resource_gate(
        resource,
        token,
        manifest,
        guard,
        claim,
        guard_path=guard_path,
        claim_path=claim_path,
    )
    child_exit_value = resource.get("child_exit_code")
    child_exit = None if child_exit_value is None else _json_int(
        child_exit_value, "resource child exit code"
    )
    numerical = payload.get("numerical")
    if numerical is not None and not isinstance(numerical, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "result numerical payload invalid")
    numerical_sha = None if numerical is None else _canonical_sha(numerical)
    if payload.get("numerical_payload_sha256") != numerical_sha:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "result numerical checksum mismatch")
    numerical_pass, child_codes, attempted, performed, completed, active = _child_outcome(
        numerical, child_exit, token, manifest, claim, claim_path, guard, guard_path
    )
    prefix_evidence = _factor_prefix_evidence(
        token, manifest, claim, claim_path, guard, guard_path
    )
    prefix = None if not prefix_evidence else prefix_evidence[-1]["payload"]
    prefix_certs = [] if prefix is None else list(prefix.get("factor_certificates", []))
    prefix_order = [] if prefix is None else list(prefix.get("factor_order", []))
    _validate_prefix_consistency(numerical, prefix_certs, prefix_order)
    if numerical is None and len(prefix_certs) > len(completed):
        attempted = True
        performed = True
        completed = prefix_order
        active = None
    _validate_factor_monitor_interval(numerical, resource, prefix_certs)
    codes: list[str] = []
    if not resource_pass:
        codes.append("BLOCKED_AV_BS_RESOURCE")
    codes.extend(child_codes)
    codes = list(dict.fromkeys(codes))
    mandatory = bool(resource_pass and numerical_pass and child_exit == 0 and not codes)
    if not mandatory and not codes:
        codes.append("BLOCKED_AV_BS_FACTOR")
    expected_status = SUCCESS_STATUS if mandatory else codes[0]
    if numerical is None:
        expected_order = prefix_order
        expected_certs = prefix_certs
    elif numerical.get("schema") == FAILURE_SCHEMA:
        expected_certs = list(numerical.get("factor_certificates", []))
        expected_order = list(numerical.get("completed_factors", []))[:len(expected_certs)]
    else:
        expected_order = list(numerical.get("factor_order", []))
        expected_certs = list(numerical.get("factor_certificates", []))
    derived = {
        "status": expected_status,
        "factor_order": expected_order,
        "factor_certificates": expected_certs,
        "factor_prefix_evidence": prefix_evidence,
        "factor_sequence_nonoverlap": True if expected_certs else None,
        "resource_gate_recheck_failures": resource_failures,
        "child_stdout_sha256": resource.get("child_stdout_sha256"),
        "child_exit_code": child_exit,
        "mandatory_stage_pass": mandatory,
        "failure_codes": [] if mandatory else codes,
        "factorization_attempted": attempted,
        "factorization_performed": performed,
        "active_factor": active,
        "completed_factors": completed,
    }
    for key, expected in derived.items():
        if not _strict_json_equal(payload.get(key), expected):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"result derived {key} mismatch")


def consume(
    token_path: Path,
    claim: Path,
    guard: Path,
    nonce: str,
    attempt: str,
    result: Path | None,
    resource: Path | None,
    child_stdout: Path | None,
) -> Mapping[str, object]:
    _EXECUTION_PHASE["claim_validated"] = False
    allowed_attempts = {
        "completed_pass", "completed_failure", "resource_stop", "finalizer_failure",
        "runner_exception", "spawn_failure",
    }
    if attempt not in allowed_attempts:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "token consumption status invalid")
    manifest = run_manifest()
    token = _validate_token(token_path, manifest, check_expiry=False)
    try:
        original_token_bytes = token_path.read_bytes()
    except OSError as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            f"authorized token bytes could not be retained: {exc}",
        ) from exc
    original_token_sha = _sha(token_path)
    if hashlib.sha256(original_token_bytes).hexdigest() != original_token_sha:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "authorized token changed while retaining original bytes",
        )
    original_token_canonical_sha = _canonical_sha(token)
    result_file_sha = result_payload_sha = resource_file_sha = None
    child_stdout_file_sha = child_payload_sha = None
    result_payload: Mapping[str, object] | None = None
    resource_value: Mapping[str, object] | None = None
    child_payload: Mapping[str, object] | None = None
    result_valid = resource_valid = resource_pass = child_evidence_valid = False
    child_attempted: bool | None = None
    child_performed: bool | None = None
    child_completed: list[str] = []
    child_active: object = None
    resource_failures: list[str] = []
    evidence_errors: list[str] = []
    codes: list[str] = []
    claim_value: Mapping[str, object] | None = None
    guard_value: Mapping[str, object] | None = None
    claim_valid = guard_valid = False
    prefix_evidence: list[Mapping[str, object]] = []
    prefix_evidence_valid = False
    try:
        claim_value = _validate_claim(claim, token, manifest)
        claim_valid = True
        _EXECUTION_PHASE["claim_validated"] = True
    except AvBsError as exc:
        codes.append(exc.code)
        evidence_errors.append(exc.detail)
    if claim_valid:
        try:
            guard_value = _validate_guard(guard, nonce, token, manifest, claim)
            guard_valid = True
        except AvBsError as exc:
            codes.append(exc.code)
            evidence_errors.append(exc.detail)

    if claim_valid and guard_valid and claim_value is not None and guard_value is not None:
        try:
            prefix_evidence = _factor_prefix_evidence(
                token,
                manifest,
                claim_value,
                claim,
                guard_value,
                guard,
            )
            prefix_evidence_valid = True
        except AvBsError as exc:
            codes.append(exc.code)
            evidence_errors.append(exc.detail)

    if resource is not None and resource.is_file():
        resource_file_sha = _sha(resource)
        try:
            resource_value = _read(resource, "resource")
            if claim_valid and guard_valid and claim_value is not None and guard_value is not None:
                resource_pass, resource_failures = _resource_gate(
                    resource_value, token, manifest, guard_value, claim_value,
                    guard_path=guard, claim_path=claim,
                )
                resource_valid = True
                if not resource_pass:
                    codes.append("BLOCKED_AV_BS_RESOURCE")
            else:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "resource cannot validate without claim and guard evidence",
                )
        except AvBsError as exc:
            codes.append(exc.code)
            evidence_errors.append(exc.detail)
    else:
        codes.append("BLOCKED_AV_BS_RESULT_SCHEMA")
        evidence_errors.append("resource report missing")

    if child_stdout is not None and child_stdout.is_file():
        child_stdout_file_sha = _sha(child_stdout)
        try:
            if (
                resource_valid
                and resource_value is not None
                and resource_value.get("child_stdout_sha256")
                != child_stdout_file_sha
            ):
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "child stdout does not match resource evidence",
                )
            child_payload, child_payload_sha = _read_wrapper(
                child_stdout, "child stdout"
            )
            child_exit_value = (
                None if resource_value is None else resource_value.get("child_exit_code")
            )
            child_exit_for_validation = (
                None
                if child_exit_value is None
                else _json_int(child_exit_value, "resource child exit code")
            )
            if not claim_valid or not guard_valid or claim_value is None or guard_value is None:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "child stdout cannot validate without claim and guard evidence",
                )
            (
                _, child_codes, child_attempted, child_performed,
                child_completed, child_active,
            ) = _child_outcome(
                child_payload,
                child_exit_for_validation,
                token,
                manifest,
                claim_value,
                claim,
                guard_value,
                guard,
            )
            codes.extend(child_codes)
            child_evidence_valid = True
        except AvBsError as exc:
            codes.append(exc.code)
            evidence_errors.append(exc.detail)
    elif (
        resource_valid
        and resource_value is not None
        and resource_value.get("child_stdout_sha256") is not None
    ):
        codes.append("BLOCKED_AV_BS_RESULT_SCHEMA")
        evidence_errors.append("child stdout evidence missing")

    if result is not None and result.is_file():
        result_file_sha = _sha(result)
        try:
            result_payload, result_payload_sha = _read_wrapper(result, "result")
            if result_payload.get("schema") == RESULT_SCHEMA:
                if (
                    not resource_valid
                    or resource_value is None
                    or resource is None
                    or claim_value is None
                    or guard_value is None
                ):
                    raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "result lacks valid resource evidence")
                _validate_result_payload(
                    result_payload, token, manifest, claim_value, claim,
                    guard_value, guard, resource_value, resource,
                )
                embedded = result_payload.get("numerical")
                if child_evidence_valid and not _strict_json_equal(
                    embedded, child_payload
                ):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        "result numerical evidence differs from child stdout",
                    )
                codes.extend(result_payload["failure_codes"])
            elif result_payload.get("schema") == FAILURE_SCHEMA:
                codes.extend(
                    _validate_failure_payload(
                        result_payload,
                        expected_stage="finalize-primary-h4-p0r",
                        expected_scope_sha256=manifest[
                            "execution_resource_scope_sha256"
                        ],
                        expected_authorization_state=(
                            "claimed_attempt_failed"
                            if claim_valid
                            else "not_authorized"
                        ),
                    )
                )
            else:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "result schema mismatch")
            result_valid = True
        except AvBsError as exc:
            codes.append(exc.code)
            evidence_errors.append(exc.detail)
    else:
        evidence_errors.append("result artifact missing")

    prefix_payload = (
        prefix_evidence[-1]["payload"]
        if prefix_evidence_valid and prefix_evidence
        else None
    )
    prefix_certificates = (
        [] if prefix_payload is None else list(prefix_payload.get("factor_certificates", []))
    )
    prefix_order = (
        [] if prefix_payload is None else list(prefix_payload.get("factor_order", []))
    )
    numerical_for_prefix: Mapping[str, object] | None = None
    if child_evidence_valid and isinstance(child_payload, Mapping):
        numerical_for_prefix = child_payload
    elif (
        result_valid
        and isinstance(result_payload, Mapping)
        and result_payload.get("schema") == RESULT_SCHEMA
        and isinstance(result_payload.get("numerical"), Mapping)
    ):
        numerical_for_prefix = result_payload["numerical"]
    if prefix_evidence_valid:
        try:
            _validate_prefix_consistency(
                numerical_for_prefix, prefix_certificates, prefix_order
            )
            if prefix_certificates:
                if not resource_valid or resource_value is None:
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        "factor prefix lacks valid resource interval evidence",
                    )
                _validate_factor_monitor_interval(
                    numerical_for_prefix,
                    resource_value,
                    prefix_certificates,
                )
        except AvBsError as exc:
            prefix_evidence_valid = False
            codes.append(exc.code)
            evidence_errors.append(exc.detail)

    if child_evidence_valid:
        attempted = child_attempted
        performed = child_performed
        completed = child_completed
        active = child_active
    elif result_valid and isinstance(result_payload, Mapping):
        attempted = result_payload.get("factorization_attempted")
        performed = result_payload.get("factorization_performed")
        completed = result_payload.get("completed_factors", [])
        active = result_payload.get("active_factor")
    elif attempt == "spawn_failure" or (
        resource_valid
        and resource_value is not None
        and resource_value.get("child_process_id") is None
    ):
        attempted, performed, completed = False, False, []
        active = None
    else:
        attempted, performed, completed = None, None, []
        active = None
    if prefix_evidence_valid and len(prefix_certificates) > len(completed):
        attempted = True
        performed = True
        completed = prefix_order
        active = None
    child_pid = (
        None
        if not resource_valid or resource_value is None
        else resource_value.get("child_process_id")
    )
    child_exit = (
        None
        if not resource_valid or resource_value is None
        else resource_value.get("child_exit_code")
    )
    if attempt == "spawn_failure":
        child_launched: bool | None = False
    elif resource_valid and resource_value is not None:
        child_launched = type(child_pid) is int and child_pid > 0
    else:
        child_launched = None

    if child_evidence_valid and isinstance(child_payload, Mapping):
        child_factor_certificates = list(child_payload.get("factor_certificates", []))
        child_factor_order = (
            list(child_payload.get("factor_order", []))
            if child_payload.get("schema") == NUMERICAL_SCHEMA
            else list(child_payload.get("completed_factors", []))[:len(child_factor_certificates)]
        )
    elif result_valid and isinstance(result_payload, Mapping):
        child_factor_certificates = list(result_payload.get("factor_certificates", []))
        child_factor_order = list(result_payload.get("factor_order", []))
    else:
        child_factor_certificates = []
        child_factor_order = []
    if prefix_evidence_valid and len(prefix_certificates) > len(child_factor_certificates):
        child_factor_certificates = prefix_certificates
        child_factor_order = prefix_order

    success = bool(
        attempt == "completed_pass"
        and result_valid and resource_valid and resource_pass
        and child_evidence_valid and not codes
        and isinstance(result_payload, Mapping)
        and result_payload.get("mandatory_stage_pass") is True
    )
    if attempt == "completed_pass" and not success:
        codes.append("BLOCKED_AV_BS_RESULT_SCHEMA")
    if not success and not codes:
        codes.append(
            "BLOCKED_AV_BS_RESOURCE" if attempt == "resource_stop"
            else "BLOCKED_AV_BS_FACTOR" if attempt == "completed_failure"
            else "BLOCKED_AV_BS_RESULT_SCHEMA"
        )
    codes = list(dict.fromkeys(codes))
    expected_claim_relative = (
        Path("validation-output/av-bs1/claims")
        / f"{token['review_token_id']}.json"
    ).as_posix()
    claim_file_sha = _sha(claim) if claim.is_file() else None
    guard_file_sha = _sha(guard) if guard.is_file() else None
    if not claim_valid or claim_value is None:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "normal v2 tombstone requires exact validated claim evidence",
        )
    tomb = {
        "schema": TOMBSTONE_SCHEMA, "program": PROGRAM, "case_id": CASE_ID,
        "authorized_stage": AUTHORIZED_STAGE, "authorization_state": "consumed",
        "uses_remaining": 0, "next_stage_authorized": False,
        "review_disposition": "consumed_after_primary_h4_p0r_claim",
        "consumed_review_token_id": token["review_token_id"],
        "consumed_review_token_sha256": original_token_sha,
        "consumed_review_token_canonical_sha256": original_token_canonical_sha,
        "p0r_preregistration_commit": token["p0r_preregistration_commit"],
        "consumed_git_head": _git_head(),
        "review_binding_sha256": _canonical_sha(
            {"token_sha256": original_token_sha, "bindings": manifest["bindings"]}
        ),
        "bindings": manifest["bindings"],
        "claim_relative_path": (
            claim_value["claim_relative_path"]
            if claim_valid and claim_value is not None
            else expected_claim_relative
        ),
        "claim_sha256": claim_file_sha,
        "claim_canonical_sha256": (
            _canonical_sha(claim_value)
            if claim_valid and claim_value is not None
            else None
        ),
        "claim_evidence_valid": claim_valid,
        "claim_evidence": claim_value if claim_valid else None,
        "preflight_payload_sha256": (
            claim_value["preflight_payload_sha256"]
            if claim_valid and claim_value is not None
            else None
        ),
        "guard_contract_sha256": guard_file_sha,
        "guard_canonical_sha256": (
            _canonical_sha(guard_value)
            if guard_valid and guard_value is not None
            else None
        ),
        "guard_evidence_valid": guard_valid,
        "guard_evidence": guard_value if guard_valid else None,
        "resource_guard_policy_sha256": manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest[
            "execution_resource_scope_sha256"
        ],
        "attempt_status": attempt,
        "effective_attempt_status": (
            "completed_pass" if success
            else "completed_invalid_evidence" if attempt == "completed_pass"
            else attempt
        ),
        "consumption_validated_pass": success,
        "child_launched": child_launched, "child_process_id": child_pid,
        "child_exit_code": child_exit,
        "consumed_result_file_sha256": result_file_sha,
        "consumed_result_payload_sha256": result_payload_sha,
        "consumed_resource_report_sha256": resource_file_sha,
        "consumed_child_stdout_file_sha256": child_stdout_file_sha,
        "consumed_child_payload_sha256": child_payload_sha,
        "result_evidence": result_payload if result_valid else None,
        "resource_evidence": resource_value if resource_valid else None,
        "child_stdout_evidence": child_payload if child_evidence_valid else None,
        "result_evidence_valid": result_valid, "resource_evidence_valid": resource_valid,
        "child_stdout_evidence_valid": child_evidence_valid,
        "factor_prefix_evidence_valid": prefix_evidence_valid,
        "factor_prefix_evidence": prefix_evidence if prefix_evidence_valid else None,
        "resource_gate_pass": resource_pass,
        "resource_gate_recheck_failures": resource_failures,
        "evidence_validation_errors": evidence_errors,
        "failure_codes": [] if success else codes, "mandatory_stage_pass": success,
        "factorization_attempted": True if success else attempted,
        "factorization_performed": True if success else performed,
        "active_factor": None if success else active,
        "completed_factors": ["A_background_II", "A_conductor_II"] if success else completed,
        "factor_order": ["A_background_II", "A_conductor_II"] if success else child_factor_order,
        "factor_certificates": child_factor_certificates,
        "outer_observer_contract_sha256": manifest[
            "outer_observer_contract_sha256"
        ],
        "outer_observer_handshake_prefix_sha256": claim_value[
            "outer_observer_handshake_prefix_sha256"
        ],
        "expected_terminal_seal_relative_path": token[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "terminal_seal_state": "pending_outer_observed_inner_exit",
        "terminal_evidence_complete": False,
        "authoritative_stage_pass": False,
        "physics_solve_performed": False, "consumed_utc": _now_utc(),
    }
    try:
        immediately_before_replace = token_path.read_bytes()
    except OSError as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            f"authorized token unavailable immediately before consumption: {exc}",
        ) from exc
    if (
        immediately_before_replace != original_token_bytes
        or hashlib.sha256(immediately_before_replace).hexdigest() != original_token_sha
    ):
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA",
            "authorized token bytes changed immediately before consumption",
        )
    _atomic_replace(token_path, tomb)
    if not _strict_json_equal(_read(token_path, "consumed tombstone"), tomb):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone readback mismatch")
    return tomb

def _failure(error:AvBsError,stage:str)->Mapping[str,object]:
    code=error.code if error.code in FAILURE_CODES else "BLOCKED_AV_BS_RESULT_SCHEMA"
    if stage == AUTHORIZED_STAGE:
        claimed = _EXECUTION_PHASE.get("claim_validated") is True
        authorization = "claimed_attempt_failed" if claimed else "not_authorized"
        attempted = _EXECUTION_PHASE.get("factorization_attempted", False) if claimed else False
        performed = _EXECUTION_PHASE.get("factorization_performed", False) if claimed else False
        completed_value = _EXECUTION_PHASE.get("completed_factors", []) if claimed else []
        certificates_value = _EXECUTION_PHASE.get("factor_certificates", []) if claimed else []
        handshake_value = _EXECUTION_PHASE.get("monitor_handshake") if claimed else None
        active = _EXECUTION_PHASE.get("active_factor") if claimed else None
    elif stage in {"finalize-primary-h4-p0r", "consume-primary-h4-p0r-token"}:
        claimed = _EXECUTION_PHASE.get("claim_validated") is True
        authorization = "claimed_attempt_failed" if claimed else "not_authorized"
        attempted = performed = None if claimed else False
        completed_value = []
        certificates_value = []
        handshake_value = None
        active = None
    else:
        authorization = "not_authorized"
        attempted = performed = False
        completed_value = []
        certificates_value = []
        handshake_value = None
        active = None
    return {
        "schema": FAILURE_SCHEMA, "program": PROGRAM, "case_id": CASE_ID,
        "stage": stage, "status": code, "authorization_state": authorization,
        "mandatory_stage_pass": False, "failure_codes": [code], "detail": error.detail,
        "factorization_attempted": attempted, "factorization_performed": performed,
        "active_factor": active,
        "completed_factors": list(completed_value) if isinstance(completed_value, list) else [],
        "factor_certificates": list(certificates_value) if isinstance(certificates_value, list) else [],
        "monitor_handshake": handshake_value if isinstance(handshake_value, Mapping) else None,
        "execution_resource_scope_sha256": _canonical_sha(
            _execution_resource_scope()
        ),
        "physics_solve_performed": False, "next_stage_authorized": False,
    }

def _parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="SPD Decap PI Evaluator v0.22.0 H4-P0R-P1 factor-only contract")
    p.add_argument("--stage",required=True,choices=("manifest","preflight-primary-h4-p0r","primary-h4-p0r","finalize-primary-h4-p0r","consume-primary-h4-p0r-token"));p.add_argument("--review-token",type=Path);p.add_argument("--guard-contract",type=Path);p.add_argument("--guard-nonce");p.add_argument("--claim-file",type=Path);p.add_argument("--resource-report",type=Path);p.add_argument("--child-stdout",type=Path);p.add_argument("--result-file",type=Path);p.add_argument("--attempt-status");p.add_argument("--monitor-ready",type=Path);p.add_argument("--monitor-complete",type=Path);p.add_argument("--monitor-release",type=Path);return p

def main(argv:list[str]|None=None)->int:
    a=_parser().parse_args(argv)
    _EXECUTION_PHASE["claim_validated"] = False
    try:
        if a.stage=="manifest": value=run_manifest()
        elif a.stage=="preflight-primary-h4-p0r": value=preflight(a.review_token)
        elif a.stage=="primary-h4-p0r":
            if any(path is None for path in (a.monitor_ready, a.monitor_complete, a.monitor_release)):
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "primary monitor marker paths are mandatory")
            value=primary(a.guard_contract,a.guard_nonce,a.review_token,a.claim_file,a.monitor_ready,a.monitor_complete,a.monitor_release)
        elif a.stage=="finalize-primary-h4-p0r":
            if a.claim_file is None: raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer claim file is mandatory")
            value=finalize(a.resource_report,a.review_token,a.guard_contract,a.guard_nonce,a.claim_file,a.child_stdout)
        else: value=consume(a.review_token,a.claim_file,a.guard_contract,a.guard_nonce,a.attempt_status,a.result_file,a.resource_report,a.child_stdout)
        output=_wrap(value);code=0
    except Exception as exc:
        error=exc if isinstance(exc,AvBsError) else AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA",str(exc));output=_wrap(_failure(error,a.stage));code=2
    print(json.dumps(output,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False));return code

if __name__=="__main__": raise SystemExit(main())
