"""Static/fake-only checks for the H4-P0R-P1 executable contract.

The one-use token, ``primary()``, and a real SuperLU factorization are outside
this suite by design.  Tiny fake factor objects exercise the certificate gates.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pytest
from scipy.sparse import csc_array, csc_matrix
import scipy.sparse.linalg as sparse_linalg

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/research"
FIXTURE = TOOLS / "av_bs1_boundary_schur_h4_p0r_p1.py"
RUNNER = TOOLS / "run_av_bs1_h4_p0r_p1_stage.ps1"
DOC = ROOT / "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md"
sys.path.insert(0, str(TOOLS))
import av_bs1_boundary_schur_h4_p0r_p1 as p1


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
UTC = "2026-08-15T00:00:00Z"
PENDING_TERMINAL_VALUES = {
    "terminal_seal_required_for_authoritative_disposition": True,
    "terminal_seal_state": "pending_outer_observed_inner_exit",
    "terminal_evidence_complete": False,
    "authoritative_stage_pass": False,
}
PENDING_TERMINAL_BINDING_FIELDS = {
    "outer_observer_contract_sha256",
    "outer_observer_handshake_prefix_sha256",
    "expected_terminal_seal_relative_path",
}
PENDING_TERMINAL_FIELDS = (
    PENDING_TERMINAL_BINDING_FIELDS | set(PENDING_TERMINAL_VALUES)
)
OUTER_RESOURCE_STOP_REASONS = (
    "OUTER_PRESPAWN_COMMIT_HEADROOM_STOP",
    "OUTER_PRESPAWN_AVAILABLE_PHYSICAL_STOP",
    "OUTER_TREE_WS_STOP",
    "OUTER_TREE_LIFETIME_PEAK_WS_STOP",
    "OUTER_TREE_PRIVATE_STOP",
    "OUTER_TREE_COMMIT_STOP",
    "OUTER_TREE_LIFETIME_PEAK_COMMIT_STOP",
    "OUTER_SYSTEM_COMMIT_HEADROOM_STOP",
    "OUTER_AVAILABLE_PHYSICAL_STOP",
    "OUTER_WALL_TIME_STOP",
    "OUTER_STDOUT_SIZE_STOP",
    "OUTER_STDERR_SIZE_STOP",
    "OUTER_INNER_CLEANUP_FAILED",
    "OUTER_POSTEXIT_COMMIT_HEADROOM_STOP",
    "OUTER_POSTEXIT_AVAILABLE_PHYSICAL_STOP",
    "OUTER_TERMINAL_SAMPLE_FAILED",
    "OUTER_RESOURCE_EXCEPTION",
)


@pytest.fixture(autouse=True)
def forbid_real_factorization(monkeypatch: pytest.MonkeyPatch):
    """Fail any accidental SuperLU call unless that test installs its own fake."""
    saved = deepcopy(p1._EXECUTION_PHASE)

    def forbidden_splu(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real scipy.sparse.linalg.splu is forbidden in static tests")

    monkeypatch.setattr(sparse_linalg, "splu", forbidden_splu)
    p1._EXECUTION_PHASE.update(
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
    yield
    p1._EXECUTION_PHASE.clear()
    p1._EXECUTION_PHASE.update(saved)


@lru_cache(maxsize=1)
def manifest() -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(FIXTURE), "--stage", "manifest"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def _metadata() -> dict[str, object]:
    return {
        "raw_input_sparse_sha256": SHA_A,
        "row_scale_sha256": SHA_B,
        "column_scale_sha256": SHA_C,
        "equilibrated_sparse_sha256": SHA_D,
        "construction_started_perf_counter_ns": 10,
        "construction_started_utc": UTC,
        "construction_cleanup_completed_perf_counter_ns": 20,
        "construction_cleanup_completed_utc": UTC,
    }


class _FakeFactor:
    def __init__(
        self,
        *,
        lower: csc_matrix | None = None,
        upper: csc_matrix | None = None,
        perm_r: np.ndarray | None = None,
        perm_c: np.ndarray | None = None,
    ) -> None:
        self.L = lower if lower is not None else csc_matrix(
            np.array([[1.0, 0.0], [2.0, 1.0]], dtype=np.complex128)
        )
        self.U = upper if upper is not None else csc_matrix(
            np.array([[3.0, 4.0], [0.0, 5.0]], dtype=np.complex128)
        )
        self.perm_r = (
            perm_r if perm_r is not None else np.array([0, 1], dtype=np.int32)
        )
        self.perm_c = (
            perm_c if perm_c is not None else np.array([1, 0], dtype=np.int32)
        )
        self.nnz = int(self.L.nnz + self.U.nnz)


def _install_fake_splu(
    monkeypatch: pytest.MonkeyPatch, factor_or_error: object
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_splu(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        if isinstance(factor_or_error, BaseException):
            raise factor_or_error
        return factor_or_error

    monkeypatch.setattr(sparse_linalg, "splu", fake_splu)
    return calls


def _clock(monkeypatch: pytest.MonkeyPatch, values: list[int]) -> None:
    ticks = iter(values)
    monkeypatch.setattr(p1, "perf_counter_ns", lambda: next(ticks))
    monkeypatch.setattr(p1, "_now_utc", lambda: UTC)


def _synthetic_manifest() -> dict[str, object]:
    policy = {
        "wall_stop_seconds": 900,
        "tree_working_set_stop_bytes": 100,
        "tree_private_stop_bytes": 200,
        "tree_commit_stop_bytes": 300,
        "commit_headroom_floor_bytes": 50,
        "available_physical_floor_bytes": 40,
        "minimum_commit_headroom_before_spawn_bytes": 80,
        "minimum_available_physical_before_spawn_bytes": 70,
        "successful_tree_sample_count_min": 1,
        "one_factor_hard_cap_bytes": 10_000,
    }
    matrices = {
        name: {
            "shape": [2, 2],
            "equilibrated_nnz": 2,
            "equilibrated_sparse_array_bytes": 52,
            "equilibrated_sha256": SHA_A if name.endswith("background_II") else SHA_B,
            "sha256": SHA_C if name.endswith("background_II") else SHA_D,
            "row_scale_sha256": SHA_E,
            "column_scale_sha256": SHA_F,
        }
        for name in ("A_background_II", "A_conductor_II")
    }
    return {
        "bindings": {
            "fixture_sha256": SHA_A,
            "runner_sha256": SHA_B,
            "manifest_payload_sha256": SHA_C,
            "matrix_contract_sha256": SHA_D,
        },
        "resource_policy": policy,
        "resource_policy_sha256": SHA_E,
        "execution_resource_scope_sha256": SHA_F,
        "execution_resource_scope": {"control_plane": {}},
        "outer_observer_contract_sha256": SHA_D,
        "outer_observer_contract": {
            "outer_resource_envelope": {
                "excluded_head": [
                    "powershell_startup_parse_function_and_native_type_initialization"
                ],
                "excluded_tail": [
                    "terminal_seal_materialization_readback_and_outer_process_exit"
                ],
                "tree_sample_max_attempts": 3,
                "tree_sample_retry_event_limit": 16,
            }
        },
        "one_use_lifecycle": {},
        "p0r_parent": {"matrix_inputs": {"matrices": matrices}},
        "forbidden_operations": {
            "rhs_solve": False,
            "physics_solve": False,
            "boundary_schur": False,
        },
    }


def _synthetic_token() -> dict[str, object]:
    return {
        "review_token_id": "0" * 32,
        "p0r_preregistration_commit": "1" * 40,
        "expected_terminal_seal_relative_path": (
            "validation-output/av-bs1/outer-observer-terminal-seals/"
            + "0" * 32
            + ".json"
        ),
    }


def _synthetic_claim() -> dict[str, object]:
    return {
        "claim_relative_path": "validation-output/av-bs1/claims/" + "0" * 32 + ".json",
        "preflight_payload_sha256": SHA_A,
        "parent_pid": 111,
        "parent_pid_birth_utc_ticks": 222,
        "outer_observer_handshake_prefix_sha256": SHA_D,
    }


def _synthetic_certificate(
    name: str, record: dict[str, object], *, base: int
) -> dict[str, object]:
    factor_array_bytes = {
        "L_data": 32,
        "L_indices": 8,
        "L_indptr": 12,
        "U_data": 32,
        "U_indices": 8,
        "U_indptr": 12,
        "perm_r": 8,
        "perm_c": 8,
    }
    return {
        "name": name,
        "input_shape": [2, 2],
        "input_dtype": "complex128",
        "input_nnz": 2,
        "input_sparse_sha256": record["equilibrated_sha256"],
        "input_sparse_array_bytes": record["equilibrated_sparse_array_bytes"],
        "raw_input_sparse_sha256": record["sha256"],
        "row_scale_sha256": record["row_scale_sha256"],
        "column_scale_sha256": record["column_scale_sha256"],
        "equilibrated_sparse_sha256": record["equilibrated_sha256"],
        "construction_started_perf_counter_ns": base,
        "construction_started_utc": UTC,
        "construction_cleanup_completed_perf_counter_ns": base + 1,
        "construction_cleanup_completed_utc": UTC,
        "permc_spec": "COLAMD",
        "diag_pivot_thresh": 1.0,
        "superlu_equil": False,
        "rhs_count": 0,
        "L_shape": [2, 2],
        "L_dtype": "complex128",
        "L_nnz": 2,
        "L_sha256": SHA_A,
        "L_storage_sha256": SHA_F,
        "L_indices_dtype": "<i4",
        "L_indptr_dtype": "<i4",
        "L_storage_canonical": True,
        "L_explicit_zero_count": 0,
        "L_lower_triangular": True,
        "L_unit_diagonal": True,
        "U_shape": [2, 2],
        "U_dtype": "complex128",
        "U_nnz": 2,
        "U_sha256": SHA_B,
        "U_storage_sha256": SHA_F,
        "U_indices_dtype": "<i4",
        "U_indptr_dtype": "<i4",
        "U_storage_canonical": True,
        "U_explicit_zero_count": 0,
        "U_upper_triangular": True,
        "U_diagonal_dtype": "complex128",
        "U_diagonal_count": 2,
        "U_diagonal_sha256": SHA_C,
        "U_diagonal_abs_min": 1.0,
        "U_diagonal_abs_max": 2.0,
        "U_diagonal_finite_nonzero": True,
        "perm_r_dtype": "int64",
        "perm_r_count": 2,
        "perm_r_storage_dtype": "<i4",
        "perm_r_sha256": SHA_D,
        "perm_r_bijective": True,
        "perm_c_dtype": "int64",
        "perm_c_count": 2,
        "perm_c_storage_dtype": "<i4",
        "perm_c_sha256": SHA_E,
        "perm_c_bijective": True,
        "stored_fill_ratio": 2.0,
        "fill_ratio": 2.0,
        "native_factor_nnz": 4,
        "native_portable_factor_bytes": 176,
        "native_pre_materialization_cap_pass": True,
        "factor_array_bytes": factor_array_bytes,
        "exported_factor_bytes": sum(factor_array_bytes.values()),
        "portable_factor_bytes": 176,
        "one_factor_hard_cap_bytes": 10_000,
        "factor_bytes_gate_pass": True,
        "factor_started_perf_counter_ns": base + 2,
        "factor_returned_perf_counter_ns": base + 3,
        "factor_wall_ns": 1,
        "factor_wall_seconds": 1.0e-9,
        "factor_started_utc": UTC,
        "factor_returned_utc": UTC,
        "factorization_attempted": True,
        "factorization_performed": True,
        "factor_solve_called": False,
        "factor_objects_cleanup_started_perf_counter_ns": base + 4,
        "factor_objects_cleanup_completed_perf_counter_ns": base + 5,
        "factor_objects_cleanup_started_utc": UTC,
        "factor_objects_cleanup_completed_utc": UTC,
        "matrix_cleanup_started_perf_counter_ns": base + 6,
        "matrix_cleanup_completed_perf_counter_ns": base + 7,
        "matrix_cleanup_started_utc": UTC,
        "matrix_cleanup_completed_utc": UTC,
    }


def _synthetic_factor_report(
    manifest_value: dict[str, object], token: dict[str, object]
) -> dict[str, object]:
    records = manifest_value["p0r_parent"]["matrix_inputs"]["matrices"]
    return {
        "schema": p1.NUMERICAL_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "status": "factor_certificates_complete",
        "review_token_id": token["review_token_id"],
        "review_token_sha256": SHA_F,
        "git_head": "2" * 40,
        "p0r_preregistration_commit": token["p0r_preregistration_commit"],
        "manifest_payload_sha256": manifest_value["bindings"]["manifest_payload_sha256"],
        "matrix_contract_sha256": manifest_value["bindings"]["matrix_contract_sha256"],
        "bindings": manifest_value["bindings"],
        "resource_policy_sha256": manifest_value["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest_value[
            "execution_resource_scope_sha256"
        ],
        "guard_contract_sha256": SHA_F,
        "guard_canonical_sha256": SHA_E,
        "claim_sha256": SHA_F,
        "claim_canonical_sha256": SHA_E,
        "monitor_handshake": {
            "schema": "AV-BS1-h4-p0r-factor-monitor-handshake-v1",
            "ready_marker_sha256": SHA_F,
            "ready_sample_perf_counter_ns": 1,
            "ready_sample_utc": UTC,
            "completion_marker_sha256": SHA_F,
            "completion_perf_counter_ns": 35,
            "completion_utc": UTC,
            "release_marker_sha256": SHA_F,
            "release_sample_perf_counter_ns": 40,
            "release_sample_utc": UTC,
            "factor_interval_bracketed_by_actual_samples": True,
        },
        "factor_order": ["A_background_II", "A_conductor_II"],
        "factor_certificates": [
            _synthetic_certificate("A_background_II", records["A_background_II"], base=10),
            _synthetic_certificate("A_conductor_II", records["A_conductor_II"], base=30),
        ],
        "factor_sequence_nonoverlap": True,
        "factorization_attempted": True,
        "factorization_performed": True,
        "physics_solve_performed": False,
        "forbidden_operation_flags": manifest_value["forbidden_operations"],
        "next_stage_authorized": False,
    }


def _synthetic_resource(
    manifest_value: dict[str, object], token: dict[str, object], claim: dict[str, object]
) -> dict[str, object]:
    policy = manifest_value["resource_policy"]
    baseline_headroom = policy["minimum_commit_headroom_before_spawn_bytes"]
    baseline_available = policy["minimum_available_physical_before_spawn_bytes"]
    final_headroom = policy["commit_headroom_floor_bytes"]
    return {
        "schema": p1.RESOURCE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "runner_sha256": manifest_value["bindings"]["runner_sha256"],
        "fixture_sha256": manifest_value["bindings"]["fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": SHA_F,
        "review_token_canonical_sha256": SHA_E,
        "resource_guard_policy_sha256": manifest_value["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest_value[
            "execution_resource_scope_sha256"
        ],
        "guard_contract_sha256": SHA_F,
        "guard_canonical_sha256": SHA_E,
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": SHA_F,
        "claim_canonical_sha256": SHA_E,
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "parent_pid": claim["parent_pid"],
        "parent_pid_birth_utc_ticks": claim["parent_pid_birth_utc_ticks"],
        "execution_tree_root_pid": 101,
        "execution_tree_root_birth_utc_ticks": 100,
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
        "thresholds": {
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
        },
        "baseline": {
            "commit_total_bytes": 1_000,
            "commit_limit_bytes": 1_000 + baseline_headroom,
            "commit_headroom_bytes": baseline_headroom,
            "available_physical_bytes": baseline_available,
            "page_size_bytes": 4_096,
        },
        "immediate_pre_spawn": {
            "commit_total_bytes": 1_010,
            "commit_limit_bytes": 1_010 + baseline_headroom,
            "commit_headroom_bytes": baseline_headroom,
            "available_physical_bytes": baseline_available,
            "page_size_bytes": 4_096,
        },
        "peak": {
            "tree_working_set_bytes": policy["tree_working_set_stop_bytes"],
            "tree_summed_process_lifetime_peak_working_set_bytes": policy[
                "tree_working_set_stop_bytes"
            ],
            "tree_private_commit_bytes": policy["tree_private_stop_bytes"],
            "tree_committed_pagefile_bytes": policy["tree_commit_stop_bytes"],
            "tree_summed_process_lifetime_peak_commit_bytes": policy[
                "tree_commit_stop_bytes"
            ],
            "tree_nonprivate_working_set_proxy_bytes": 0,
            "tree_page_fault_count": 0,
            "system_commit_total_bytes": 1_030,
            "system_commit_headroom_min_bytes": policy["commit_headroom_floor_bytes"],
            "available_physical_min_bytes": policy["available_physical_floor_bytes"],
        },
        "final_system": {
            "commit_total_bytes": 1_030,
            "commit_limit_bytes": 1_030 + final_headroom,
            "commit_headroom_bytes": final_headroom,
            "available_physical_bytes": policy["available_physical_floor_bytes"],
            "page_size_bytes": 4_096,
        },
        "final_system_sample_valid": True,
        "started_utc": UTC,
        "ended_utc": "2026-08-15T00:00:00.250000Z",
        "child_exit_code": 2,
        "child_process_id": 333,
        "child_process_handle_acquired": True,
        "observed_child_process_ids": [101, 111, 333],
        "observed_process_identities": [
            {"process_id": 101, "birth_utc_ticks": 100},
            {"process_id": 111, "birth_utc_ticks": 222},
            {"process_id": 333, "birth_utc_ticks": 444}
        ],
        "successful_tree_sample_count": 2,
        "child_visible_tree_sample_count": 2,
        "first_child_visible_sample_perf_counter_ns": 1,
        "first_child_visible_sample_utc": UTC,
        "last_child_visible_sample_perf_counter_ns": 40,
        "last_child_visible_sample_utc": UTC,
        "monitor_ready_marker_sha256": SHA_F,
        "factor_complete_marker_sha256": SHA_F,
        "monitor_release_marker_sha256": SHA_F,
        "factor_monitor_handshake_complete": True,
        "factor_prefix_one_sha256": None,
        "factor_prefix_two_sha256": None,
        "wall_seconds": 0.25,
        "wall_clock_kind": "System.Diagnostics.Stopwatch",
        "monitor_ok": True,
        "monitor_error": None,
        "stop_reason": None,
        "child_stdout_sha256": SHA_A,
        "child_stderr_sha256": SHA_B,
        "mandatory_resource_gate_pass": True,
    }


def _synthetic_guard(manifest_value: dict[str, object]) -> dict[str, object]:
    policy = manifest_value["resource_policy"]
    return {
        "baseline_commit_headroom_bytes": policy[
            "minimum_commit_headroom_before_spawn_bytes"
        ],
        "baseline_available_physical_bytes": policy[
            "minimum_available_physical_before_spawn_bytes"
        ],
    }


def _synthetic_terminal_bindings(claim: dict[str, object]) -> dict[str, object]:
    return {
        "observer": {
            "outer_process_id": 101,
            "outer_process_birth_utc_ticks": 100,
            "inner_process_id": claim["parent_pid"],
            "inner_process_birth_utc_ticks": claim["parent_pid_birth_utc_ticks"],
        },
        "review_token_sha256": SHA_F,
        "review_token_canonical_sha256": SHA_E,
        "guard_sha256": SHA_F,
        "guard_canonical_sha256": SHA_E,
        "claim_sha256": SHA_F,
        "claim_canonical_sha256": SHA_E,
        "factor_prefix_1_sha256": None,
        "factor_prefix_2_sha256": None,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )


def _control_system_sample(*, headroom: int = 80, available: int = 70) -> dict[str, int]:
    total = 1_000
    return {
        "commit_total_bytes": total,
        "commit_limit_bytes": total + headroom,
        "commit_headroom_bytes": headroom,
        "available_physical_bytes": available,
        "page_size_bytes": 4_096,
    }


def _control_tree_sample(process_ids: list[int]) -> dict[str, object]:
    return {
        "process_ids": process_ids,
        "process_count": len(process_ids),
        "working_set_bytes": 10,
        "summed_process_peak_working_set_bytes": 11,
        "committed_pagefile_bytes": 12,
        "summed_process_peak_commit_bytes": 13,
        "private_commit_bytes": 9,
        "private_working_set_bytes": 8,
        "nonprivate_working_set_proxy_bytes": 2,
        "shared_commit_bytes": 3,
        "page_fault_count": 1,
    }


def _control_peak() -> dict[str, int]:
    return {
        "tree_working_set_bytes": 10,
        "tree_summed_process_lifetime_peak_working_set_bytes": 11,
        "tree_private_commit_bytes": 9,
        "tree_committed_pagefile_bytes": 12,
        "tree_summed_process_lifetime_peak_commit_bytes": 13,
        "tree_nonprivate_working_set_proxy_bytes": 2,
        "tree_page_fault_count": 1,
        "system_commit_total_bytes": 1_000,
        "system_commit_headroom_min_bytes": 80,
        "available_physical_min_bytes": 70,
    }


def _ordered_json_roundtrip(value: object) -> object:
    """Match the key order observed after reading a sort-key-written artifact."""
    return json.loads(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


@lru_cache(maxsize=2)
def _runner_single_quoted_here_string(variable: str) -> bytes:
    source = RUNNER.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^\${re.escape(variable)} = @'\n(.*?)\n'@$", source
    )
    assert match is not None, f"runner here-string not found: {variable}"
    return match.group(1).encode("utf-8")


def _build_preflight_control_plane_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mutations: dict[str, object] | None = None,
    corrupt_stdout_checksum: bool = False,
    terminal_outer_utc: bool = False,
) -> dict[str, object]:
    """Build an exact, tiny report -> index-1 -> close -> index-2 chain."""
    mutations = {} if mutations is None else mutations

    def mutate(name: str, value: object) -> None:
        callback = mutations.get(name)
        if callback is not None:
            callback(value)  # type: ignore[operator]

    root = tmp_path / "repo"
    tools = root / "tools" / "research"
    tools.mkdir(parents=True)
    fixture = tools / "synthetic_p1.py"
    runner = tools / "synthetic_runner.ps1"
    token_path = tools / "synthetic_review_token.json"
    identifier = "0" * 32
    expected_terminal_seal_relative_path = (
        "validation-output/av-bs1/outer-observer-terminal-seals/"
        f"{identifier}.json"
    )
    claim_token = {
        "schema": p1.TOKEN_SCHEMA,
        "review_token_id": identifier,
        "authorization_state": "authorized",
        "uses_remaining": 1,
        "p0r_preregistration_commit": "1" * 40,
        "expected_terminal_seal_relative_path": (
            expected_terminal_seal_relative_path
        ),
        "reviewed_utc": UTC,
        "expires_utc": "2099-08-15T00:00:00Z",
    }
    python_path = Path(sys.executable).resolve()
    fixture.write_text("# synthetic fixture\n", encoding="utf-8")
    runner.write_text("# synthetic runner\n", encoding="utf-8")
    _write_json(token_path, claim_token)

    monkeypatch.setattr(p1, "ROOT", root)
    monkeypatch.setattr(p1, "TOKEN_PATH", token_path)
    monkeypatch.setattr(p1, "P1_RUNNER", runner)
    monkeypatch.setattr(p1, "__file__", str(fixture))

    session_id = "session-" + "1" * 32
    invocation_id = "2" * 32
    session_dir = root / "validation-output" / "av-bs1" / "control-plane" / session_id
    invocation_dir = session_dir / ("preflight-" + invocation_id)
    invocation_dir.mkdir(parents=True)
    report_path = invocation_dir / "control-plane-report.json"
    preclose_path = session_dir / "session-index-0001.json"
    final_index_path = session_dir / "session-index-0002.json"
    close_path = invocation_dir / "control-plane-envelope-close.json"
    bootstrap_path = session_dir / "bounded-bootstrap.py"
    canonical_helper_path = session_dir / "canonical-json-sha256.py"
    ready_path = invocation_dir / "bootstrap-ready.json"
    start_release_path = invocation_dir / "start-release.json"
    completion_path = invocation_dir / "target-complete.json"
    exit_release_path = invocation_dir / "exit-release.json"
    stdout_path = invocation_dir / "stdout.txt"
    stderr_path = invocation_dir / "stderr.txt"
    observer_nonce = "4" * 32
    outer_session_relative = (
        "validation-output/av-bs1/outer-observer/session-" + observer_nonce
    )
    outer_session_dir = root / outer_session_relative
    outer_session_dir.mkdir(parents=True)
    outer_ready_path = outer_session_dir / "inner-ready.json"
    outer_start_release_path = outer_session_dir / "outer-start-release.json"
    bootstrap_path.write_bytes(
        _runner_single_quoted_here_string("controlPlaneBootstrapSource")
    )
    canonical_helper_path.write_bytes(
        _runner_single_quoted_here_string("controlPlaneCanonicalHashSource")
    )
    mutate("bootstrap", bootstrap_path)
    mutate("canonical_helper", canonical_helper_path)

    root_pid = 101
    inner_pid = 151
    process_pid = 202
    root_birth = 1_000
    inner_birth = 1_500
    process_birth = 2_000
    ready = {
        "schema": "AV-BS1-h4-p0r-control-plane-bootstrap-ready-v1",
        "process_id": process_pid,
        "monotonic_ns": 10,
    }
    mutate("ready", ready)
    _write_json(ready_path, ready)
    for mutation_name, path, schema, sample in (
        (
            "start_release",
            start_release_path,
            "AV-BS1-h4-p0r-control-plane-start-release-v1",
            11,
        ),
        (
            "exit_release",
            exit_release_path,
            "AV-BS1-h4-p0r-control-plane-exit-release-v1",
            21,
        ),
    ):
        marker = {
            "schema": schema,
            "invocation_id": invocation_id,
            "process_id": process_pid,
            "sample_perf_counter_ns": sample,
            "sample_utc": UTC,
        }
        mutate(mutation_name, marker)
        _write_json(path, marker)
    completion = {
        "schema": "AV-BS1-h4-p0r-control-plane-target-complete-v1",
        "process_id": process_pid,
        "exit_code": 0,
        "monotonic_ns": 20,
    }
    mutate("completion", completion)
    _write_json(completion_path, completion)
    stderr_path.write_text("", encoding="utf-8")

    manifest_value = _synthetic_manifest()
    manifest_value["bindings"]["fixture_sha256"] = p1._sha(fixture)
    manifest_value["bindings"]["runner_sha256"] = p1._sha(runner)
    policy = manifest_value["resource_policy"]
    scope_sha = manifest_value["execution_resource_scope_sha256"]
    outer_timestamp = (
        "2026-08-15T00:00:00.0000000+00:00" if terminal_outer_utc else UTC
    )
    token_sha = p1._sha(token_path)
    outer_ready = {
        "schema": p1.OUTER_INNER_READY_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "internal_mode": "primary-h4-p0r-inner-v1",
        "observer_session_relative_path": outer_session_relative,
        "observer_nonce": observer_nonce,
        "outer_process_id": root_pid,
        "outer_process_birth_utc_ticks": root_birth,
        "inner_process_id": inner_pid,
        "inner_process_birth_utc_ticks": inner_birth,
        "runner_relative_path": runner.relative_to(root).as_posix(),
        "runner_sha256": manifest_value["bindings"]["runner_sha256"],
        "review_token_relative_path": token_path.relative_to(root).as_posix(),
        "review_token_sha256": token_sha,
        "review_token_id": identifier,
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": expected_terminal_seal_relative_path,
        "terminal_seal_required_for_authoritative_disposition": True,
        "ready_written_before_preflight_and_claim": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "monotonic_ns": 1,
        "created_utc": outer_timestamp,
    }
    mutate("outer_ready", outer_ready)
    _write_json(outer_ready_path, outer_ready)
    outer_ready_relative = outer_ready_path.relative_to(root).as_posix()
    outer_start_release = {
        "schema": p1.OUTER_START_RELEASE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "observer_session_relative_path": outer_session_relative,
        "observer_nonce": outer_ready["observer_nonce"],
        "outer_process_id": outer_ready["outer_process_id"],
        "outer_process_birth_utc_ticks": outer_ready[
            "outer_process_birth_utc_ticks"
        ],
        "inner_process_id": outer_ready["inner_process_id"],
        "inner_process_birth_utc_ticks": outer_ready[
            "inner_process_birth_utc_ticks"
        ],
        "runner_sha256": outer_ready["runner_sha256"],
        "review_token_sha256": outer_ready["review_token_sha256"],
        "review_token_id": outer_ready["review_token_id"],
        "outer_observer_contract_sha256": outer_ready[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": outer_ready[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "inner_ready_relative_path": outer_ready_relative,
        "inner_ready_sha256": p1._sha(outer_ready_path),
        "release_scope": "permit_inner_preflight_and_claim_only",
        "claim_handshake_prefix_ready": True,
        "terminal_seal_still_pending": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "monotonic_ns": 2,
        "released_utc": outer_timestamp,
    }
    mutate("outer_start_release", outer_start_release)
    _write_json(outer_start_release_path, outer_start_release)
    handshake_prefix = {
        "schema": p1.OUTER_HANDSHAKE_PREFIX_SCHEMA,
        "inner_ready_relative_path": outer_ready_relative,
        "inner_ready_sha256": p1._sha(outer_ready_path),
        "outer_start_release_relative_path": outer_start_release_path.relative_to(
            root
        ).as_posix(),
        "outer_start_release_sha256": p1._sha(outer_start_release_path),
    }
    mutate("outer_prefix", handshake_prefix)
    observer = {
        "session_relative_path": outer_session_relative,
        "observer_nonce": outer_ready["observer_nonce"],
        "outer_process_id": outer_ready["outer_process_id"],
        "outer_process_birth_utc_ticks": outer_ready[
            "outer_process_birth_utc_ticks"
        ],
        "inner_process_id": outer_ready["inner_process_id"],
        "inner_process_birth_utc_ticks": outer_ready[
            "inner_process_birth_utc_ticks"
        ],
        "ready_utc": UTC,
        "release_utc": UTC,
    }
    claim = {
        **_synthetic_claim(),
        "schema": p1.CLAIM_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "parent_pid": inner_pid,
        "parent_pid_birth_utc_ticks": inner_birth,
        "git_head": "2" * 40,
        "p0r_preregistration_commit": claim_token["p0r_preregistration_commit"],
        "review_token_id": identifier,
        "review_token_sha256": token_sha,
        "review_token_canonical_sha256": p1._canonical_sha(claim_token),
        "review_binding_sha256": p1._canonical_sha(
            {"token_sha256": token_sha, "bindings": manifest_value["bindings"]}
        ),
        "fixture_sha256": manifest_value["bindings"]["fixture_sha256"],
        "runner_sha256": manifest_value["bindings"]["runner_sha256"],
        "manifest_payload_sha256": manifest_value["bindings"][
            "manifest_payload_sha256"
        ],
        "matrix_contract_sha256": manifest_value["bindings"][
            "matrix_contract_sha256"
        ],
        "resource_policy_sha256": manifest_value["resource_policy_sha256"],
        "execution_resource_scope_sha256": scope_sha,
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": expected_terminal_seal_relative_path,
        "terminal_seal_required_for_authoritative_disposition": True,
        "guard_nonce": "3" * 32,
        "created_utc": outer_timestamp,
    }
    claim["outer_observer_handshake_prefix"] = handshake_prefix
    claim["outer_observer_handshake_prefix_sha256"] = p1._canonical_sha(
        handshake_prefix
    )
    preflight_payload = {
        "schema": p1.SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "preflight-primary-h4-p0r",
        "status": "preflight_pass_token_gated_no_factor",
        "authorization_state": "authorized",
        "preflight_pass": True,
        "git_head": claim["git_head"],
        "p0r_preregistration_commit": claim["p0r_preregistration_commit"],
        "review_token_id": claim["review_token_id"],
        "review_token_sha256": claim["review_token_sha256"],
        "review_binding_sha256": claim["review_binding_sha256"],
        "manifest_payload_sha256": p1._historical_preclaim_manifest_payload_sha256(
            manifest_value, token_sha
        ),
        "manifest_bindings": manifest_value["bindings"],
        "resource_policy_sha256": manifest_value["resource_policy_sha256"],
        "execution_resource_scope_sha256": scope_sha,
        "outer_observer_contract_sha256": manifest_value[
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
    mutate("preflight_payload", preflight_payload)
    stdout_wrapper = p1._wrap(preflight_payload)
    if corrupt_stdout_checksum:
        stdout_wrapper["payload_sha256"] = SHA_A
    _write_json(stdout_path, stdout_wrapper)
    claim["preflight_payload_sha256"] = p1._canonical_sha(preflight_payload)

    target_argv = [
        str(fixture.resolve()),
        "--stage",
        "preflight-primary-h4-p0r",
        "--review-token",
        str(token_path.resolve()),
    ]
    process_argv = [
        str(bootstrap_path.resolve()),
        str(ready_path.resolve()),
        str(start_release_path.resolve()),
        str(completion_path.resolve()),
        str(exit_release_path.resolve()),
        *target_argv,
    ]
    provenance = {
        role: (
            {
                "path": str(token_path.resolve()),
                "exists": True,
                "bytes": token_path.stat().st_size,
                "sha256": p1._sha(token_path),
            }
            if role == "review_token"
            else {"path": None, "exists": False, "bytes": None, "sha256": None}
        )
        for role in (
            "review_token",
            "claim",
            "guard",
            "resource",
            "result",
            "canonical_input",
        )
    }
    provenance = _ordered_json_roundtrip(provenance)
    thresholds = {
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
    pre_spawn_system = _control_system_sample()
    pre_helper_system = _control_system_sample()
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
    report = {
        "schema": p1.CONTROL_PLANE_REPORT_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "control-plane",
        "operation": "preflight",
        "invocation_id": invocation_id,
        "report_path": str(report_path.resolve()),
        "runner_path": str(runner.resolve()),
        "runner_sha256": p1._sha(runner),
        "python_path": str(python_path.resolve()),
        "python_sha256": p1._sha(python_path),
        "bootstrap_path": str(bootstrap_path.resolve()),
        "bootstrap_sha256": p1._sha(bootstrap_path),
        "target_script_path": str(fixture.resolve()),
        "target_script_sha256": p1._sha(fixture),
        "target_argv": target_argv,
        "target_argv_json_sha256": p1._ordered_json_sha256(target_argv),
        "process_argv": process_argv,
        "process_argv_json_sha256": p1._ordered_json_sha256(process_argv),
        "attempt_provenance_before": provenance,
        "attempt_provenance_before_sha256": p1._ordered_json_sha256(provenance),
        "attempt_provenance_after": deepcopy(provenance),
        "attempt_provenance_after_sha256": p1._ordered_json_sha256(provenance),
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
            "python_tree_"
            "Toolhelp32_Psapi_100ms_with_completion_handshake"
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
        "envelope_close_path": str(close_path.resolve()),
        "envelope_close_sha256": None,
        "poll_interval_ms": 100,
        "wall_stop_seconds": p1.CONTROL_PLANE_WALL_STOP_SECONDS,
        "stream_stop_bytes_each": 16 * 1024**2,
        "thresholds": thresholds,
        "pre_spawn_system": pre_spawn_system,
        "pre_helper_after_provenance_tree": _control_tree_sample(
            [root_pid, inner_pid]
        ),
        "pre_helper_after_provenance_system": pre_helper_system,
        "final_system": None,
        "high_system_floor_recheck_before_and_after": False,
        "high_system_floor_recheck_authoritative_in_envelope_close": True,
        "started_utc": UTC,
        "ended_utc": None,
        "wall_seconds": None,
        "wall_clock_kind": "System.Diagnostics.Stopwatch",
        "process_id": process_pid,
        "process_birth_utc_ticks": process_birth,
        "process_handle_acquired": True,
        "execution_tree_root_pid": root_pid,
        "execution_tree_root_birth_utc_ticks": root_birth,
        "execution_tree_includes_runner": True,
        "inner_runner_pid": inner_pid,
        "inner_runner_birth_utc_ticks": inner_birth,
        "observed_process_ids": [root_pid, inner_pid, process_pid],
        "observed_process_identities": [
            {"process_id": root_pid, "birth_utc_ticks": root_birth},
            {"process_id": inner_pid, "birth_utc_ticks": inner_birth},
            {"process_id": process_pid, "birth_utc_ticks": process_birth},
        ],
        "cleanup_observed_process_ids": [process_pid],
        "cleanup_observed_process_identities": [
            {"process_id": process_pid, "birth_utc_ticks": process_birth}
        ],
        "successful_tree_sample_count": 2,
        "target_visible_tree_sample_count": 2,
        "peak": _control_peak(),
        "bootstrap_ready_sha256": p1._sha(ready_path),
        "start_release_sha256": p1._sha(start_release_path),
        "target_complete_sha256": p1._sha(completion_path),
        "target_exit_evidence_path": str(completion_path.resolve()),
        "target_exit_evidence_sha256": p1._sha(completion_path),
        "exit_release_sha256": p1._sha(exit_release_path),
        "handshake_complete": True,
        "reported_exit_code": 0,
        "actual_exit_code": 0,
        "allowed_exit_codes": [0],
        "exit_code_allowed": True,
        "reported_exit_matches_actual": True,
        "stdout_path": str(stdout_path.resolve()),
        "stdout_bytes": stdout_path.stat().st_size,
        "stdout_sha256": p1._sha(stdout_path),
        "stderr_path": str(stderr_path.resolve()),
        "stderr_bytes": stderr_path.stat().st_size,
        "stderr_sha256": p1._sha(stderr_path),
        "monitor_ok": False,
        "monitor_ok_before_envelope_close": True,
        "monitor_error": None,
        "stop_reason": None,
        "cleanup_attempted": True,
        "cleanup_verified": True,
        "cleanup_identity_complete": True,
        "fallback_process_object_cleanup_attempted": False,
        "fallback_process_object_root_exit_verified": False,
        "observed_survivors_after_cleanup": [],
        "terminal_system_sample_after_verified_termination_or_no_spawn": False,
        "mandatory_control_plane_gate_pass": False,
    }
    mutate("report", report)
    _write_json(report_path, report)
    report_sha = p1._sha(report_path)

    reference_common = {
        "operation": "preflight",
        "invocation_id": invocation_id,
        "report_path": str(report_path.resolve()),
        "report_sha256": report_sha,
        "target_argv_json_sha256": report["target_argv_json_sha256"],
        "process_argv_json_sha256": report["process_argv_json_sha256"],
        "attempt_provenance_before_sha256": report[
            "attempt_provenance_before_sha256"
        ],
        "attempt_provenance_after_sha256": report[
            "attempt_provenance_after_sha256"
        ],
    }
    index_common = {
        "schema": p1.CONTROL_PLANE_INDEX_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "session_id": session_id,
        "runner_sha256": p1._sha(runner),
        "bootstrap_sha256": p1._sha(bootstrap_path),
        "canonical_hash_helper_sha256": p1._sha(canonical_helper_path),
        "execution_resource_scope_sha256": scope_sha,
        "execution_resource_scope_binding_semantics": report[
            "execution_resource_scope_binding_semantics"
        ],
        "consume_after_every_owned_claim_outcome": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    preclose_index = {
        **index_common,
        "sequence": 1,
        "index_role": "preclose_in_resource_envelope",
        "previous_index_path": None,
        "previous_index_sha256": None,
        "report_references": [
            {
                **reference_common,
                "envelope_close_path": None,
                "envelope_close_sha256": None,
                "mandatory_control_plane_gate_pass": False,
            }
        ],
        "envelope_close_path": str(close_path.resolve()),
        "envelope_close_sha256": None,
        "mandatory_control_plane_gate_pass": False,
        "this_index_materialization_inside_resource_envelope": True,
    }
    mutate("preclose", preclose_index)
    _write_json(preclose_path, preclose_index)
    preclose_sha = p1._sha(preclose_path)

    close = {
        "schema": p1.CONTROL_PLANE_ENVELOPE_CLOSE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "control-plane",
        "operation": "preflight",
        "invocation_id": invocation_id,
        "report_path": str(report_path.resolve()),
        "report_sha256": report_sha,
        "preclose_session_index_path": str(preclose_path.resolve()),
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
        "resource_envelope_excluded_tail": [
            "this_envelope_close_materialization_and_hash",
            "final_session_index_materialization_and_hash",
            "stdout_return_text_read_and_return_packaging",
            "caller_side_processing_after_return",
            "session_bootstrap_and_helper_script_materialization_before_first_invocation",
            "candidate_scope_seed_read_before_preflight_invocation",
            "runner_emergency_token_replacement_after_failed_consumer_invocation",
            "pre_exit_intent_evidence_materialization",
            "temporary_attempt_evidence_cleanup_or_quarantine",
        ],
        "process_membership_semantics": (
            "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_helper_"
            "descendants_created_and_exited_between_samples_not_claimed"
        ),
        "simultaneous_current_interval_semantics": report[
            "simultaneous_current_interval_semantics"
        ],
        "summed_os_lifetime_peak_semantics": report[
            "summed_os_lifetime_peak_semantics"
        ],
        "thresholds": thresholds,
        "pre_spawn_system": pre_spawn_system,
        "pre_helper_after_provenance_system": pre_helper_system,
        "high_system_floor_recheck_before_and_after": True,
        "terminal_system_sample_after_verified_termination_or_no_spawn": True,
        "envelope_close_runner_tree_sample": _control_tree_sample(
            [root_pid, inner_pid]
        ),
        "final_system": _control_system_sample(),
        "peak": _control_peak(),
        "monitor_ok": True,
        "monitor_error": None,
        "stop_reason": None,
        "cleanup_verified": True,
        "started_utc": UTC,
        "ended_utc": UTC,
        "wall_seconds": 0.0,
        "wall_clock_kind": (
            "System.Diagnostics.Stopwatch_frozen_once_before_close_materialization"
        ),
        "wall_stop_seconds": p1.CONTROL_PLANE_WALL_STOP_SECONDS,
        "mandatory_control_plane_gate_pass": True,
        "authorization_effect": "bounded_control_plane_evidence_only",
        "consume_after_every_owned_claim_outcome": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
    }
    mutate("close", close)
    _write_json(close_path, close)
    close_sha = p1._sha(close_path)

    final_index = {
        **index_common,
        "sequence": 2,
        "index_role": "final_binds_resource_envelope_close",
        "previous_index_path": str(preclose_path.resolve()),
        "previous_index_sha256": preclose_sha,
        "report_references": [
            {
                **reference_common,
                "envelope_close_path": str(close_path.resolve()),
                "envelope_close_sha256": close_sha,
                "mandatory_control_plane_gate_pass": True,
            }
        ],
        "envelope_close_path": str(close_path.resolve()),
        "envelope_close_sha256": close_sha,
        "mandatory_control_plane_gate_pass": True,
        "this_index_materialization_inside_resource_envelope": False,
    }
    mutate("final_index", final_index)
    _write_json(final_index_path, final_index)

    claim.update(
        {
            "control_plane_preflight_report_relative_path": report_path.relative_to(
                root
            ).as_posix(),
            "control_plane_preflight_report_sha256": report_sha,
            "control_plane_preflight_session_index_relative_path": (
                final_index_path.relative_to(root).as_posix()
            ),
            "control_plane_preflight_session_index_sha256": p1._sha(
                final_index_path
            ),
        }
    )
    return {
        "manifest": manifest_value,
        "claim": claim,
        "root": root,
        "report": report,
        "report_path": report_path,
        "preclose_index": preclose_index,
        "preclose_path": preclose_path,
        "close": close,
        "close_path": close_path,
        "final_index": final_index,
        "final_index_path": final_index_path,
        "stdout_path": stdout_path,
        "token_path": token_path,
        "claim_token": claim_token,
        "observer": observer,
        "outer_ready": outer_ready,
        "outer_ready_path": outer_ready_path,
        "outer_start_release": outer_start_release,
        "outer_start_release_path": outer_start_release_path,
    }


def _install_terminal_tombstone_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    emergency_branch: str | None = None,
) -> dict[str, object]:
    """Install an exact claim plus a normal/inner/outer provisional v2 tombstone."""
    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, terminal_outer_utc=True
    )
    root = bundle["root"]
    manifest_value = bundle["manifest"]
    claim = bundle["claim"]
    token_path = bundle["token_path"]
    claim_path = root / claim["claim_relative_path"]
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(claim_path, claim)
    original_token_bytes = token_path.read_bytes()
    original_token_sha256 = hashlib.sha256(original_token_bytes).hexdigest()
    original_token_canonical_sha256 = p1._canonical_sha(bundle["claim_token"])
    assert claim["review_token_sha256"] == original_token_sha256
    assert (
        claim["review_token_canonical_sha256"]
        == original_token_canonical_sha256
    )
    identifier = claim["review_token_id"]
    failure_message = "synthetic controlled post-claim failure"
    tombstone = {
        "schema": p1.TOMBSTONE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorized_stage": p1.AUTHORIZED_STAGE,
        "authorization_state": "consumed",
        "uses_remaining": 0,
        "next_stage_authorized": False,
        "review_disposition": "consumed_after_primary_h4_p0r_claim",
        "consumed_review_token_id": identifier,
        "consumed_review_token_sha256": original_token_sha256,
        "consumed_review_token_canonical_sha256": (
            original_token_canonical_sha256
        ),
        "p0r_preregistration_commit": claim["p0r_preregistration_commit"],
        "consumed_git_head": claim["git_head"],
        "review_binding_sha256": claim["review_binding_sha256"],
        "bindings": manifest_value["bindings"],
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": p1._sha(claim_path),
        "claim_canonical_sha256": p1._canonical_sha(claim),
        "claim_evidence_valid": True,
        "claim_evidence": claim,
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "guard_contract_sha256": None,
        "guard_canonical_sha256": None,
        "guard_evidence_valid": False,
        "guard_evidence": None,
        "resource_guard_policy_sha256": manifest_value[
            "resource_policy_sha256"
        ],
        "execution_resource_scope_sha256": manifest_value[
            "execution_resource_scope_sha256"
        ],
        "attempt_status": "completed_failure",
        "effective_attempt_status": "completed_failure",
        "consumption_validated_pass": False,
        "child_launched": False,
        "child_process_id": None,
        "child_exit_code": None,
        "consumed_result_file_sha256": None,
        "consumed_result_payload_sha256": None,
        "consumed_resource_report_sha256": None,
        "consumed_child_stdout_file_sha256": None,
        "consumed_child_payload_sha256": None,
        "result_evidence": None,
        "resource_evidence": None,
        "child_stdout_evidence": None,
        "result_evidence_valid": False,
        "resource_evidence_valid": False,
        "child_stdout_evidence_valid": False,
        "factor_prefix_evidence_valid": False,
        "factor_prefix_evidence": None,
        "resource_gate_pass": False,
        "resource_gate_recheck_failures": [],
        "evidence_validation_errors": [],
        "failure_codes": ["BLOCKED_AV_BS_FACTOR"],
        "mandatory_stage_pass": False,
        "factorization_attempted": False,
        "factorization_performed": False,
        "active_factor": None,
        "completed_factors": [],
        "factor_order": [],
        "factor_certificates": [],
        "physics_solve_performed": False,
        "consumed_utc": UTC,
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "outer_observer_handshake_prefix_sha256": claim[
            "outer_observer_handshake_prefix_sha256"
        ],
        "expected_terminal_seal_relative_path": (
            f"validation-output/av-bs1/outer-observer-terminal-seals/"
            f"{identifier}.json"
        ),
        **PENDING_TERMINAL_VALUES,
    }
    if emergency_branch is not None:
        assert emergency_branch in {"inner", "outer"}
        outer_emergency = emergency_branch == "outer"
        tombstone.update(
            {
                "schema": p1.EMERGENCY_TOMBSTONE_SCHEMA,
                "review_disposition": (
                    "emergency_consumed_after_outer_observer_post_claim_failure"
                    if outer_emergency
                    else "emergency_consumed_after_control_plane_failure"
                ),
                "emergency_consumption": True,
                "consumer_control_gate_pass": not outer_emergency,
                "consumer_failure_message": failure_message,
                "replaced_token_file_sha256": None,
                "runner_sha256": manifest_value["bindings"]["runner_sha256"],
                "claim_canonical_sha256": None,
                "claim_evidence_valid": False,
                "claim_evidence": None,
                "attempt_status": (
                    "outer_observer_post_claim_failure"
                    if outer_emergency
                    else "runner_exception"
                ),
                "effective_attempt_status": (
                    "outer_observer_post_claim_failure"
                    if outer_emergency
                    else "control_plane_consumer_failure"
                ),
                "child_launched": None if outer_emergency else False,
                "resource_gate_recheck_failures": [
                    (
                        "outer_observer_post_claim_failure"
                        if outer_emergency
                        else "emergency_consumer_control_plane_failure"
                    )
                ],
                "evidence_validation_errors": [failure_message],
                "failure_codes": [
                    (
                        "BLOCKED_AV_BS_RESOURCE"
                        if outer_emergency
                        else "BLOCKED_AV_BS_RESULT_SCHEMA"
                    )
                ],
                "factorization_attempted": None if outer_emergency else False,
                "factorization_performed": None if outer_emergency else False,
            }
        )
    expected_fields = (
        p1.EMERGENCY_TOMBSTONE_FIELDS
        if emergency_branch is not None
        else p1.NORMAL_TOMBSTONE_FIELDS
    )
    assert set(tombstone) == expected_fields
    _write_json(token_path, tombstone)
    installed = json.loads(token_path.read_text(encoding="utf-8"))
    return {
        **bundle,
        "claim_path": claim_path,
        "tombstone": installed,
        "tombstone_sha256": p1._sha(token_path),
        "original_token_bytes": original_token_bytes,
        "original_token_sha256": original_token_sha256,
        "original_token_canonical_sha256": original_token_canonical_sha256,
        "emergency_branch": emergency_branch,
    }


def _install_synthetic_outer_terminal_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    passed: bool,
    untrusted_published_result: bool = False,
    malformed_ready_marker: bool = False,
) -> dict[str, object]:
    """Install a complete fake-only outer-observer terminal evidence chain.

    The process-control index is represented by durable synthetic files and a
    small validator adapter.  The real preflight/claim/tombstone, terminal
    complete, exit-release, outer-close, seal, and final evidence validators
    still run against current bytes on disk.
    """
    assert not (passed and untrusted_published_result)
    assert not (passed and malformed_ready_marker)
    bundle = _install_terminal_tombstone_bundle(monkeypatch, tmp_path)
    root = bundle["root"]
    manifest_value = bundle["manifest"]
    claim = bundle["claim"]
    claim_path = bundle["claim_path"]
    token_path = bundle["token_path"]
    identifier = claim["review_token_id"]
    outer_utc = "2026-08-15T00:00:00.0000000+00:00"
    manifest_value["outer_observer_contract"]["outer_resource_envelope"] = {
        "excluded_head": [
            "powershell_startup_parse_function_and_native_type_initialization"
        ],
        "excluded_tail": [
            "terminal_seal_materialization_readback_and_outer_process_exit"
        ],
        "tree_sample_max_attempts": 3,
        "tree_sample_retry_event_limit": 16,
    }

    attempt_relative = (
        "validation-output/av-bs1/quarantine/"
        f"{identifier}-{claim['guard_nonce']}"
    )
    attempt_path = root / attempt_relative
    attempt_path.mkdir(parents=True)
    published_relative = (
        "validation-output/av-bs1/"
        "av-bs1-primary-h4-p0r-20260815T000000Z.json"
    )
    published_path = root / published_relative
    published_path.parent.mkdir(parents=True, exist_ok=True)
    guard_path = attempt_path / "guard.json"
    resource_path = attempt_path / "resource.json"
    result_path = attempt_path / "final.json"
    child_path = attempt_path / "numerical.json"
    child_stderr_path = attempt_path / "child.stderr.txt"
    prefix_paths = [
        attempt_path / "factor-prefix-1.json",
        attempt_path / "factor-prefix-2.json",
    ]
    marker_paths = {
        "monitor_ready_marker_sha256": attempt_path / "monitor-ready.json",
        "factor_complete_marker_sha256": attempt_path / "factor-complete.json",
        "monitor_release_marker_sha256": attempt_path / "monitor-release.json",
    }

    tombstone = deepcopy(bundle["tombstone"])
    claim_sha = p1._sha(claim_path)
    claim_canonical = p1._canonical_sha(claim)
    marker_hashes = {key: None for key in marker_paths}
    factor_prefix_evidence: list[dict[str, object]] = []
    resource: dict[str, object] | None = None
    child: dict[str, object] | None = None

    if passed:
        policy = manifest_value["resource_policy"]
        guard = {
            "schema": p1.GUARD_SCHEMA,
            "program": p1.PROGRAM,
            "case_id": p1.CASE_ID,
            "stage": p1.AUTHORIZED_STAGE,
            "nonce": claim["guard_nonce"],
            "review_token_id": identifier,
            "review_token_sha256": bundle["original_token_sha256"],
            "review_token_canonical_sha256": bundle[
                "original_token_canonical_sha256"
            ],
            "fixture_sha256": manifest_value["bindings"]["fixture_sha256"],
            "runner_sha256": manifest_value["bindings"]["runner_sha256"],
            "claim_relative_path": claim["claim_relative_path"],
            "claim_sha256": claim_sha,
            "claim_canonical_sha256": claim_canonical,
            "preflight_payload_sha256": claim["preflight_payload_sha256"],
            "resource_guard_policy_sha256": manifest_value[
                "resource_policy_sha256"
            ],
            "execution_resource_scope_sha256": manifest_value[
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
            "monitor_ok": True,
            "baseline_commit_headroom_bytes": policy[
                "minimum_commit_headroom_before_spawn_bytes"
            ],
            "baseline_available_physical_bytes": policy[
                "minimum_available_physical_before_spawn_bytes"
            ],
            "pre_spawn_resource_gate_pass": True,
        }
        _write_json(guard_path, guard)
        guard_sha = p1._sha(guard_path)
        guard_canonical = p1._canonical_sha(guard)

        child = _synthetic_factor_report(manifest_value, bundle["claim_token"])
        child.update(
            {
                "review_token_sha256": bundle["original_token_sha256"],
                "git_head": claim["git_head"],
                "guard_contract_sha256": guard_sha,
                "guard_canonical_sha256": guard_canonical,
                "claim_sha256": claim_sha,
                "claim_canonical_sha256": claim_canonical,
            }
        )
        ready = {
            "schema": "AV-BS1-h4-p0r-monitor-ready-v1",
            "claim_sha256": claim_sha,
            "child_process_id": 333,
            "sample_perf_counter_ns": 1,
            "sample_utc": UTC,
        }
        _write_json(marker_paths["monitor_ready_marker_sha256"], ready)
        marker_hashes["monitor_ready_marker_sha256"] = p1._sha(
            marker_paths["monitor_ready_marker_sha256"]
        )
        factor_complete = {
            "schema": "AV-BS1-h4-p0r-factor-complete-v1",
            "claim_sha256": claim_sha,
            "child_process_id": 333,
            "completed_factors": ["A_background_II", "A_conductor_II"],
            "factor_certificates_sha256": p1._canonical_sha(
                child["factor_certificates"]
            ),
            "completed_perf_counter_ns": 35,
            "completed_utc": UTC,
        }
        _write_json(
            marker_paths["factor_complete_marker_sha256"], factor_complete
        )
        marker_hashes["factor_complete_marker_sha256"] = p1._sha(
            marker_paths["factor_complete_marker_sha256"]
        )
        release = {
            "schema": "AV-BS1-h4-p0r-monitor-release-v1",
            "claim_sha256": claim_sha,
            "completion_marker_sha256": marker_hashes[
                "factor_complete_marker_sha256"
            ],
            "child_process_id": 333,
            "sample_perf_counter_ns": 40,
            "sample_utc": UTC,
        }
        _write_json(marker_paths["monitor_release_marker_sha256"], release)
        marker_hashes["monitor_release_marker_sha256"] = p1._sha(
            marker_paths["monitor_release_marker_sha256"]
        )
        child["monitor_handshake"] = {
            "schema": "AV-BS1-h4-p0r-factor-monitor-handshake-v1",
            "ready_marker_sha256": marker_hashes[
                "monitor_ready_marker_sha256"
            ],
            "ready_sample_perf_counter_ns": 1,
            "ready_sample_utc": UTC,
            "completion_marker_sha256": marker_hashes[
                "factor_complete_marker_sha256"
            ],
            "completion_perf_counter_ns": 35,
            "completion_utc": UTC,
            "release_marker_sha256": marker_hashes[
                "monitor_release_marker_sha256"
            ],
            "release_sample_perf_counter_ns": 40,
            "release_sample_utc": UTC,
            "factor_interval_bracketed_by_actual_samples": True,
        }
        _write_json(child_path, p1._wrap(child))
        child_file_sha = p1._sha(child_path)
        child_payload_sha = p1._canonical_sha(child)
        child_stderr_path.write_bytes(b"")

        for position, prefix_path in enumerate(prefix_paths, start=1):
            prefix_payload = deepcopy(child)
            prefix_payload.update(
                {
                    "status": "factor_certificate_prefix_checkpoint",
                    "factor_order": child["factor_order"][:position],
                    "factor_certificates": child["factor_certificates"][:position],
                    "monitor_handshake": None,
                }
            )
            _write_json(prefix_path, p1._wrap(prefix_payload))
            factor_prefix_evidence.append(
                {
                    "count": position,
                    "file_sha256": p1._sha(prefix_path),
                    "payload_sha256": p1._canonical_sha(prefix_payload),
                    "payload": prefix_payload,
                }
            )

        resource = _synthetic_resource(
            manifest_value, bundle["claim_token"], claim
        )
        resource.update(
            {
                "review_token_sha256": bundle["original_token_sha256"],
                "review_token_canonical_sha256": bundle[
                    "original_token_canonical_sha256"
                ],
                "guard_contract_sha256": guard_sha,
                "guard_canonical_sha256": guard_canonical,
                "claim_sha256": claim_sha,
                "claim_canonical_sha256": claim_canonical,
                "parent_pid": claim["parent_pid"],
                "parent_pid_birth_utc_ticks": claim[
                    "parent_pid_birth_utc_ticks"
                ],
                "child_exit_code": 0,
                "observed_child_process_ids": [101, 151, 333],
                "observed_process_identities": [
                    {"process_id": 101, "birth_utc_ticks": 100},
                    {"process_id": 151, "birth_utc_ticks": 1_500},
                    {"process_id": 333, "birth_utc_ticks": 2_000},
                ],
                "monitor_ready_marker_sha256": marker_hashes[
                    "monitor_ready_marker_sha256"
                ],
                "factor_complete_marker_sha256": marker_hashes[
                    "factor_complete_marker_sha256"
                ],
                "monitor_release_marker_sha256": marker_hashes[
                    "monitor_release_marker_sha256"
                ],
                "factor_prefix_one_sha256": factor_prefix_evidence[0][
                    "file_sha256"
                ],
                "factor_prefix_two_sha256": factor_prefix_evidence[1][
                    "file_sha256"
                ],
                "child_stdout_sha256": child_file_sha,
                "child_stderr_sha256": p1._sha(child_stderr_path),
            }
        )
        _write_json(resource_path, resource)
        resource_sha = p1._sha(resource_path)

        result = {
            "schema": p1.RESULT_SCHEMA,
            "program": p1.PROGRAM,
            "case_id": p1.CASE_ID,
            "stage": p1.AUTHORIZED_STAGE,
            "status": p1.SUCCESS_STATUS,
            "authorization_state": "claimed_attempt_complete_pending_tombstone",
            "token_consumption_required": True,
            "git_head": claim["git_head"],
            "p0r_preregistration_commit": claim["p0r_preregistration_commit"],
            "review_token_id": identifier,
            "review_token_sha256": bundle["original_token_sha256"],
            "bindings": manifest_value["bindings"],
            "matrix_inputs": manifest_value["p0r_parent"]["matrix_inputs"],
            "execution_resource_scope_sha256": manifest_value[
                "execution_resource_scope_sha256"
            ],
            "outer_observer_contract_sha256": manifest_value[
                "outer_observer_contract_sha256"
            ],
            "outer_observer_handshake_prefix_sha256": claim[
                "outer_observer_handshake_prefix_sha256"
            ],
            "expected_terminal_seal_relative_path": claim[
                "expected_terminal_seal_relative_path"
            ],
            **PENDING_TERMINAL_VALUES,
            "factor_order": ["A_background_II", "A_conductor_II"],
            "factor_certificates": child["factor_certificates"],
            "factor_prefix_evidence": factor_prefix_evidence,
            "factor_sequence_nonoverlap": True,
            "numerical": child,
            "numerical_payload_sha256": child_payload_sha,
            "resource": resource,
            "resource_report_sha256": resource_sha,
            "resource_gate_recheck_failures": [],
            "guard_contract_sha256": guard_sha,
            "guard_canonical_sha256": guard_canonical,
            "claim_relative_path": claim["claim_relative_path"],
            "claim_sha256": claim_sha,
            "claim_canonical_sha256": claim_canonical,
            "child_stdout_sha256": child_file_sha,
            "child_exit_code": 0,
            "mandatory_stage_pass": True,
            "failure_codes": [],
            "factorization_attempted": True,
            "factorization_performed": True,
            "active_factor": None,
            "completed_factors": ["A_background_II", "A_conductor_II"],
            "physics_solve_performed": False,
            "forbidden_operation_flags": manifest_value["forbidden_operations"],
            "next_stage_authorized": False,
        }
        _write_json(result_path, p1._wrap(result))
        _write_json(published_path, p1._wrap(result))
        result_file_sha = p1._sha(result_path)
        assert result_file_sha == p1._sha(published_path)
        result_payload_sha = p1._canonical_sha(result)
        tombstone.update(
            {
                "guard_contract_sha256": guard_sha,
                "guard_canonical_sha256": guard_canonical,
                "guard_evidence_valid": True,
                "guard_evidence": guard,
                "attempt_status": "completed_pass",
                "effective_attempt_status": "completed_pass",
                "consumption_validated_pass": True,
                "child_launched": True,
                "child_process_id": 333,
                "child_exit_code": 0,
                "consumed_result_file_sha256": result_file_sha,
                "consumed_result_payload_sha256": result_payload_sha,
                "consumed_resource_report_sha256": resource_sha,
                "consumed_child_stdout_file_sha256": child_file_sha,
                "consumed_child_payload_sha256": child_payload_sha,
                "result_evidence": result,
                "resource_evidence": resource,
                "child_stdout_evidence": child,
                "result_evidence_valid": True,
                "resource_evidence_valid": True,
                "child_stdout_evidence_valid": True,
                "factor_prefix_evidence_valid": True,
                "factor_prefix_evidence": factor_prefix_evidence,
                "resource_gate_pass": True,
                "resource_gate_recheck_failures": [],
                "evidence_validation_errors": [],
                "failure_codes": [],
                "mandatory_stage_pass": True,
                "factorization_attempted": True,
                "factorization_performed": True,
                "completed_factors": ["A_background_II", "A_conductor_II"],
                "factor_order": ["A_background_II", "A_conductor_II"],
                "factor_certificates": child["factor_certificates"],
            }
        )

        def validate_synthetic_factor(
            payload: dict[str, object], *_args: object, **_kwargs: object
        ) -> None:
            certificates = payload.get("factor_certificates")
            assert isinstance(certificates, list)
            assert [item["name"] for item in certificates] == [
                "A_background_II",
                "A_conductor_II",
            ][: len(certificates)]

        monkeypatch.setattr(p1, "_validate_factor_report", validate_synthetic_factor)
        monkeypatch.setattr(p1, "_resource_gate", lambda *_args, **_kwargs: (True, []))
        monkeypatch.setattr(
            p1, "_validate_factor_monitor_interval", lambda *_args, **_kwargs: None
        )
    else:
        guard_path.write_bytes(b"{raw-bound-untrusted-guard")
        guard_sha = p1._sha(guard_path)
        guard_canonical = SHA_E
        if malformed_ready_marker:
            marker_paths["monitor_ready_marker_sha256"].write_bytes(
                b"{raw-bound-malformed-monitor-ready"
            )
            marker_hashes["monitor_ready_marker_sha256"] = p1._sha(
                marker_paths["monitor_ready_marker_sha256"]
            )
        p1._EXECUTION_PHASE.update(
            {
                "claim_validated": True,
                "factorization_attempted": False,
                "factorization_performed": False,
                "active_factor": None,
                "completed_factors": [],
                "factor_certificates": [],
                "monitor_handshake": None,
            }
        )
        result = p1._failure(
            p1.AvBsError("BLOCKED_AV_BS_FACTOR", "synthetic terminal failure"),
            "finalize-primary-h4-p0r",
        )
        result["execution_resource_scope_sha256"] = manifest_value[
            "execution_resource_scope_sha256"
        ]
        if untrusted_published_result:
            result_path.write_bytes(b"{raw-bound-untrusted-result")
            published_path.write_bytes(result_path.read_bytes())
            result_payload_sha = None
        else:
            _write_json(result_path, p1._wrap(result))
            _write_json(published_path, p1._wrap(result))
            result_payload_sha = p1._canonical_sha(result)
        result_file_sha = p1._sha(result_path)
        assert result_file_sha == p1._sha(published_path)
        tombstone.update(
            {
                "guard_contract_sha256": guard_sha,
                "guard_canonical_sha256": None,
                "guard_evidence_valid": False,
                "guard_evidence": None,
                "consumed_result_file_sha256": result_file_sha,
                "consumed_result_payload_sha256": result_payload_sha,
                "result_evidence": None if untrusted_published_result else result,
                "result_evidence_valid": not untrusted_published_result,
            }
        )

    assert set(tombstone) == p1.NORMAL_TOMBSTONE_FIELDS
    _write_json(token_path, tombstone)
    tombstone = json.loads(token_path.read_text(encoding="utf-8"))
    tombstone_sha = p1._sha(token_path)
    installed_tombstone_context = p1._validate_consumed_tombstone(
        tombstone, tombstone_sha, manifest_value
    )
    observer = installed_tombstone_context["observer"]

    control_session = (
        root
        / "validation-output"
        / "av-bs1"
        / "control-plane"
        / ("session-" + "7" * 32)
    )
    control_session.mkdir(parents=True)
    operations = [
        "preflight",
        "canonical_json_hash",
        "canonical_json_hash",
        "canonical_json_hash",
        "finalizer",
        "token_consumer",
    ]
    references: list[dict[str, object]] = []
    canonical_outputs = [
        (
            bundle["original_token_sha256"],
            bundle["original_token_canonical_sha256"],
        ),
        (claim_sha, claim_canonical),
        (guard_sha, guard_canonical),
    ]
    for position, operation in enumerate(operations):
        invocation_id = f"{position + 1:032x}"
        invocation_dir = control_session / f"{operation}-{invocation_id}"
        invocation_dir.mkdir()
        report_path = invocation_dir / "control-plane-report.json"
        close_path = invocation_dir / "control-plane-envelope-close.json"
        stdout_path = invocation_dir / "stdout.txt"
        stderr_path = invocation_dir / "stderr.txt"
        provenance = {"canonical_input": {"sha256": None}}
        if operation == "canonical_json_hash":
            raw_sha, canonical_sha = canonical_outputs[position - 1]
            provenance["canonical_input"]["sha256"] = raw_sha
            stdout_path.write_text(f"{canonical_sha}\n", encoding="utf-8")
        elif operation == "finalizer":
            _write_json(stdout_path, p1._wrap(result))
        elif operation == "token_consumer":
            _write_json(stdout_path, p1._wrap(tombstone))
        else:
            stdout_path.write_text("synthetic preflight\n", encoding="utf-8")
        stderr_path.write_bytes(b"")
        report = {
            "stdout_path": str(stdout_path.resolve()),
            "stdout_sha256": p1._sha(stdout_path),
            "stderr_path": str(stderr_path.resolve()),
            "stderr_sha256": p1._sha(stderr_path),
            "bootstrap_ready_sha256": None,
            "start_release_sha256": None,
            "target_complete_sha256": None,
            "exit_release_sha256": None,
            "attempt_provenance_before": provenance,
        }
        _write_json(report_path, {"operation": operation, "position": position})
        _write_json(close_path, {"gate": True, "position": position})
        references.append(
            {
                "operation": operation,
                "report_path": report_path.resolve(),
                "report_sha256": p1._sha(report_path),
                "close_path": close_path.resolve(),
                "close_sha256": p1._sha(close_path),
                "gate": True,
                "report": report,
            }
        )
    final_index_path = control_session / "session-index-0012.json"
    _write_json(final_index_path, {"synthetic_terminal_index": True})
    final_index_relative = final_index_path.relative_to(root).as_posix()
    final_index_sha = p1._sha(final_index_path)
    control_context = {
        "path": final_index_path.resolve(),
        "sha256": final_index_sha,
        "value": {"synthetic_terminal_index": True},
        "references": references,
        "indexes": [],
    }

    def validate_synthetic_control_index(
        relative: object,
        expected_sha: object,
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        if relative != final_index_relative or expected_sha != p1._sha(final_index_path):
            raise p1.AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                "synthetic terminal control index binding mismatch",
            )
        return control_context

    monkeypatch.setattr(
        p1, "_validate_terminal_control_index", validate_synthetic_control_index
    )

    consumer_reference = references[-1]
    pre_exit_relative = (
        "validation-output/av-bs1/control-plane-pre-exit-evidence/"
        f"{identifier}.json"
    )
    pre_exit_path = root / pre_exit_relative
    pre_exit_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_hashes = {
        "guard_sha256": guard_sha,
        "guard_canonical_sha256": guard_canonical,
        "resource_report_sha256": None if resource is None else p1._sha(resource_path),
        "result_file_sha256": result_file_sha,
        "child_stdout_sha256": None if child is None else p1._sha(child_path),
        "child_stderr_sha256": (
            None if child is None else p1._sha(child_stderr_path)
        ),
        "factor_prefix_one_sha256": (
            None if not factor_prefix_evidence else factor_prefix_evidence[0]["file_sha256"]
        ),
        "factor_prefix_two_sha256": (
            None if not factor_prefix_evidence else factor_prefix_evidence[1]["file_sha256"]
        ),
        **marker_hashes,
    }
    intended_exit = 0 if passed else 2
    pre_exit = {
        "schema": p1.CONTROL_PLANE_PRE_EXIT_EVIDENCE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorized_stage": p1.AUTHORIZED_STAGE,
        "evidence_kind": "pre_exit_intent_only",
        "review_token_id": identifier,
        "original_review_token_sha256": bundle["original_token_sha256"],
        "original_review_token_canonical_sha256": bundle[
            "original_token_canonical_sha256"
        ],
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": claim_sha,
        "claim_canonical_sha256": claim_canonical,
        **artifact_hashes,
        "tombstone_schema": p1.TOMBSTONE_SCHEMA,
        "tombstone_relative_path": token_path.relative_to(root).as_posix(),
        "tombstone_sha256": tombstone_sha,
        "emergency_replacement_postvalidated": False,
        "emergency_replacement_intent_relative_path": None,
        "emergency_replacement_intent_sha256": None,
        "emergency_replacement_postvalidation_relative_path": None,
        "emergency_replacement_postvalidation_sha256": None,
        "emergency_replacement_recovery_relative_path": None,
        "emergency_replacement_recovery_sha256": None,
        "consumer_control_gate_pass": True,
        "consumer_report_relative_path": consumer_reference[
            "report_path"
        ].relative_to(root).as_posix(),
        "consumer_report_sha256": consumer_reference["report_sha256"],
        "consumer_envelope_close_relative_path": consumer_reference[
            "close_path"
        ].relative_to(root).as_posix(),
        "consumer_envelope_close_sha256": consumer_reference["close_sha256"],
        "final_session_index_relative_path": final_index_relative,
        "final_session_index_sha256": final_index_sha,
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": claim[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "outer_observer_handshake_prefix": claim[
            "outer_observer_handshake_prefix"
        ],
        "outer_observer_handshake_prefix_sha256": claim[
            "outer_observer_handshake_prefix_sha256"
        ],
        "attempt_status": tombstone["attempt_status"],
        "runner_exit_observed": False,
        "intended_runner_exit_code": intended_exit,
        "intended_runner_disposition": (
            "success_intent_pending_external_exit_observation"
            if passed
            else "failure_intent_pending_external_exit_observation"
        ),
        "mandatory_stage_pass": False,
        "authorization_blocker": True,
        "authorization_effect": "none_authorization_remains_blocked",
        "normal_tombstone_authority": (
            "provisional_candidate_only_pending_external_observer"
        ),
        "normal_pass_outer_evidence_reconciliation_pass": passed,
        "normal_pass_outer_evidence_reconciliation_errors": (
            [] if passed else ["synthetic controlled failure"]
        ),
        "tombstone_success_remains_provisional": True,
        "pre_exit_evidence_complete": True,
        "terminal_evidence_complete": False,
        "external_observer_required_for_terminal_exit": True,
        "written_before_runner_exit": True,
        "temporary_attempt_evidence_cleanup_observed": False,
        "temporary_attempt_evidence_cleanup_occurs_after_this_record": True,
        "consume_after_every_owned_claim_outcome": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "execution_resource_scope_sha256": manifest_value[
            "execution_resource_scope_sha256"
        ],
        "recorded_utc": outer_utc,
    }
    assert set(pre_exit) == p1.CONTROL_PLANE_PRE_EXIT_EVIDENCE_FIELDS
    _write_json(pre_exit_path, pre_exit)

    complete_path = observer["session_path"] / "inner-complete.json"
    complete = {
        "schema": p1.OUTER_INNER_COMPLETE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "internal_mode": "primary-h4-p0r-inner-v1",
        "observer_session_relative_path": observer["session_relative_path"],
        "observer_nonce": observer["nonce"],
        "outer_process_id": observer["outer_process_id"],
        "outer_process_birth_utc_ticks": observer[
            "outer_process_birth_utc_ticks"
        ],
        "inner_process_id": observer["inner_process_id"],
        "inner_process_birth_utc_ticks": observer[
            "inner_process_birth_utc_ticks"
        ],
        "runner_sha256": manifest_value["bindings"]["runner_sha256"],
        "review_token_id": identifier,
        "original_review_token_sha256": bundle["original_token_sha256"],
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": claim[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "inner_ready_relative_path": bundle["outer_ready_path"].relative_to(
            root
        ).as_posix(),
        "inner_ready_sha256": p1._sha(bundle["outer_ready_path"]),
        "outer_start_release_relative_path": bundle[
            "outer_start_release_path"
        ].relative_to(root).as_posix(),
        "outer_start_release_sha256": p1._sha(
            bundle["outer_start_release_path"]
        ),
        "outer_observer_handshake_prefix": claim[
            "outer_observer_handshake_prefix"
        ],
        "outer_observer_handshake_prefix_sha256": claim[
            "outer_observer_handshake_prefix_sha256"
        ],
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": claim_sha,
        "claim_canonical_sha256": claim_canonical,
        **artifact_hashes,
        "published_result_relative_path": published_relative,
        "published_result_sha256": p1._sha(published_path),
        "tombstone_relative_path": token_path.relative_to(root).as_posix(),
        "tombstone_sha256": tombstone_sha,
        "tombstone_schema": p1.TOMBSTONE_SCHEMA,
        "pre_exit_evidence_relative_path": pre_exit_relative,
        "pre_exit_evidence_sha256": p1._sha(pre_exit_path),
        "pre_exit_evidence_schema": p1.CONTROL_PLANE_PRE_EXIT_EVIDENCE_SCHEMA,
        "final_control_plane_session_index_relative_path": final_index_relative,
        "final_control_plane_session_index_sha256": final_index_sha,
        "intended_inner_exit_code": intended_exit,
        "provisional_inner_disposition": (
            "provisional_pass_pending_outer_observed_exit"
            if passed
            else "provisional_failure_pending_outer_observed_exit"
        ),
        "inner_exit_observed": False,
        "temporary_attempt_cleanup_disposition": "quarantined",
        "attempt_evidence_relative_path": attempt_relative,
        "terminal_evidence_complete": False,
        "authorization_blocker": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "monotonic_ns": 3,
        "completed_utc": outer_utc,
    }
    assert set(complete) == p1.OUTER_INNER_COMPLETE_FIELDS
    _write_json(complete_path, complete)

    exit_path = observer["session_path"] / "outer-exit-release.json"
    exit_release = {
        "schema": p1.OUTER_EXIT_RELEASE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "observer_session_relative_path": observer["session_relative_path"],
        "observer_nonce": observer["nonce"],
        "outer_process_id": observer["outer_process_id"],
        "outer_process_birth_utc_ticks": observer[
            "outer_process_birth_utc_ticks"
        ],
        "inner_process_id": observer["inner_process_id"],
        "inner_process_birth_utc_ticks": observer[
            "inner_process_birth_utc_ticks"
        ],
        "runner_sha256": manifest_value["bindings"]["runner_sha256"],
        "review_token_id": identifier,
        "original_review_token_sha256": bundle["original_token_sha256"],
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": claim[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "inner_complete_relative_path": complete_path.relative_to(root).as_posix(),
        "inner_complete_sha256": p1._sha(complete_path),
        "outer_observer_handshake_prefix_sha256": claim[
            "outer_observer_handshake_prefix_sha256"
        ],
        "intended_inner_exit_code": intended_exit,
        "release_inner_to_exit": True,
        "outer_resource_gate_pass_before_inner_exit": True,
        "terminal_seal_still_pending": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "monotonic_ns": 4,
        "released_utc": outer_utc,
    }
    assert set(exit_release) == p1.OUTER_EXIT_RELEASE_FIELDS
    _write_json(exit_path, exit_release)

    inner_stdout_path = observer["session_path"] / "inner.stdout.txt"
    inner_stderr_path = observer["session_path"] / "inner.stderr.txt"
    inner_stdout_path.write_text("synthetic inner terminal stdout\n", encoding="utf-8")
    inner_stderr_path.write_bytes(b"")
    close_path = observer["session_path"] / "outer-resource-envelope-close.json"
    outer_close = {
        "schema": p1.OUTER_RESOURCE_ENVELOPE_CLOSE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorization_blocker": True,
        "available_physical_min_bytes": 70,
        "cleanup_verified": True,
        "commit_headroom_min_bytes": 80,
        "ended_utc": outer_utc,
        "excluded_head": manifest_value["outer_observer_contract"][
            "outer_resource_envelope"
        ]["excluded_head"],
        "excluded_tail": manifest_value["outer_observer_contract"][
            "outer_resource_envelope"
        ]["excluded_tail"],
        "execution_tree_root_birth_utc_ticks": observer[
            "outer_process_birth_utc_ticks"
        ],
        "execution_tree_root_pid": observer["outer_process_id"],
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "high_post_available_physical_bytes": 70,
        "high_post_commit_headroom_bytes": 80,
        "high_pre_available_physical_bytes": 70,
        "high_pre_commit_headroom_bytes": 80,
        "high_pre_spawn_available_physical_bytes": 70,
        "high_pre_spawn_commit_headroom_bytes": 80,
        "inner_actual_exit_code": intended_exit,
        "inner_complete_relative_path": complete_path.relative_to(root).as_posix(),
        "inner_complete_sha256": p1._sha(complete_path),
        "inner_exit_release_relative_path": exit_path.relative_to(root).as_posix(),
        "inner_exit_release_sha256": p1._sha(exit_path),
        "inner_parent_identity_verified": True,
        "inner_parent_process_birth_utc_ticks": observer[
            "outer_process_birth_utc_ticks"
        ],
        "inner_parent_process_id": observer["outer_process_id"],
        "inner_process_birth_utc_ticks": observer[
            "inner_process_birth_utc_ticks"
        ],
        "inner_process_id": observer["inner_process_id"],
        "inner_ready_relative_path": bundle["outer_ready_path"].relative_to(
            root
        ).as_posix(),
        "inner_ready_sha256": p1._sha(bundle["outer_ready_path"]),
        "inner_stderr_bytes": inner_stderr_path.stat().st_size,
        "inner_stderr_sha256": p1._sha(inner_stderr_path),
        "inner_stdout_bytes": inner_stdout_path.stat().st_size,
        "inner_stdout_sha256": p1._sha(inner_stdout_path),
        "inner_visible_sample_count": 2,
        "mandatory_outer_resource_gate_pass": True,
        "monitor_error_present": False,
        "monitor_failure": None,
        "observed_lifecycle_scope": (
            "post_dispatch_stopwatch_start_before_candidate_token_capture_through_"
            "inner_actual_exit_verified_cleanup_terminal_stream_hash_and_system_tree_sample"
        ),
        "observer_nonce": observer["nonce"],
        "observer_session_relative_path": observer["session_relative_path"],
        "original_review_token_sha256": bundle["original_token_sha256"],
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "peak": _control_peak(),
        "post_cleanup_claim_relative_path": claim["claim_relative_path"],
        "post_cleanup_claim_sha256": claim_sha,
        "post_cleanup_emergency_replacement_performed": False,
        "post_cleanup_recovery_state": (
            "claim_present_same_attempt_tombstone_validated_by_inner_complete"
        ),
        "post_cleanup_terminal_seal_pending": True,
        "post_cleanup_token_sha256": tombstone_sha,
        "process_membership_semantics": (
            "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_identity_"
            "bound_descendants_between_samples_not_claimed"
        ),
        "raw_bytes_are_canonical_json": True,
        "review_token_id": identifier,
        "reviewed_contract_git_commit": claim["p0r_preregistration_commit"],
        "runner_sha256": manifest_value["bindings"]["runner_sha256"],
        "sample_count": 2,
        "sampled_process_identities": [
            {
                "process_id": observer["outer_process_id"],
                "birth_utc_ticks": observer["outer_process_birth_utc_ticks"],
            },
            {
                "process_id": observer["inner_process_id"],
                "birth_utc_ticks": observer["inner_process_birth_utc_ticks"],
            },
        ],
        "simultaneous_current_factor_interval_semantics": (
            "inner_factor_resource_reports_measure_sampled_current_outer_plus_inner_"
            "plus_primary_helper_tree"
        ),
        "started_utc": outer_utc,
        "stop_reason": None,
        "summed_os_lifetime_peak_semantics": (
            "conservative_stop_gate_includes_outer_inner_preflight_control_and_factor_"
            "work_not_factor_only_peak"
        ),
        "terminal_sample_after_inner_actual_exit_and_verified_cleanup": True,
        "thresholds": {
            "available_physical_floor_bytes": manifest_value["resource_policy"][
                "available_physical_floor_bytes"
            ],
            "high_available_physical_floor_bytes": manifest_value[
                "resource_policy"
            ]["minimum_available_physical_before_spawn_bytes"],
            "high_commit_headroom_floor_bytes": manifest_value["resource_policy"][
                "minimum_commit_headroom_before_spawn_bytes"
            ],
            "system_commit_headroom_floor_bytes": manifest_value["resource_policy"][
                "commit_headroom_floor_bytes"
            ],
            "tree_commit_stop_bytes": manifest_value["resource_policy"][
                "tree_commit_stop_bytes"
            ],
            "tree_private_stop_bytes": manifest_value["resource_policy"][
                "tree_private_stop_bytes"
            ],
            "tree_working_set_stop_bytes": manifest_value["resource_policy"][
                "tree_working_set_stop_bytes"
            ],
            "wall_stop_seconds": p1.WALL_STOP_SECONDS,
        },
        "tree_sample_confirmed_disappearance_count": 0,
        "tree_sample_max_attempts": 3,
        "tree_sample_retry_events": [],
        "tree_sample_retry_events_truncated": False,
        "wall_elapsed_nanoseconds": 1,
        "wall_stop_seconds": p1.WALL_STOP_SECONDS,
    }
    assert set(outer_close) == p1.OUTER_RESOURCE_ENVELOPE_CLOSE_FIELDS
    _write_json(close_path, outer_close)

    seal_path = root / claim["expected_terminal_seal_relative_path"]
    seal_path.parent.mkdir(parents=True, exist_ok=True)
    seal = {
        "schema": p1.OUTER_TERMINAL_SEAL_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": p1.AUTHORIZED_STAGE,
        "seal_kind": "outer_observer_retained_inner_actual_exit",
        "review_token_id": identifier,
        "original_review_token_sha256": bundle["original_token_sha256"],
        "runner_sha256": manifest_value["bindings"]["runner_sha256"],
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "expected_terminal_seal_relative_path": claim[
            "expected_terminal_seal_relative_path"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "observer_session_relative_path": observer["session_relative_path"],
        "observer_nonce": observer["nonce"],
        "outer_process_id": observer["outer_process_id"],
        "outer_process_birth_utc_ticks": observer[
            "outer_process_birth_utc_ticks"
        ],
        "inner_process_id": observer["inner_process_id"],
        "inner_process_birth_utc_ticks": observer[
            "inner_process_birth_utc_ticks"
        ],
        "retained_inner_process_handle_acquired": True,
        "inner_ready_relative_path": bundle["outer_ready_path"].relative_to(
            root
        ).as_posix(),
        "inner_ready_sha256": p1._sha(bundle["outer_ready_path"]),
        "outer_start_release_relative_path": bundle[
            "outer_start_release_path"
        ].relative_to(root).as_posix(),
        "outer_start_release_sha256": p1._sha(
            bundle["outer_start_release_path"]
        ),
        "outer_observer_handshake_prefix": claim[
            "outer_observer_handshake_prefix"
        ],
        "outer_observer_handshake_prefix_sha256": claim[
            "outer_observer_handshake_prefix_sha256"
        ],
        "artifact_hash_source": (
            "current_inner_complete_exact_matches_validated_pre_exit_evidence"
        ),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": claim_sha,
        "claim_canonical_sha256": claim_canonical,
        **artifact_hashes,
        "published_result_relative_path": published_relative,
        "published_result_sha256": p1._sha(published_path),
        "inner_complete_relative_path": complete_path.relative_to(root).as_posix(),
        "inner_complete_sha256": p1._sha(complete_path),
        "outer_exit_release_relative_path": exit_path.relative_to(root).as_posix(),
        "outer_exit_release_sha256": p1._sha(exit_path),
        "inner_stdout_relative_path": inner_stdout_path.relative_to(root).as_posix(),
        "inner_stdout_bytes": inner_stdout_path.stat().st_size,
        "inner_stdout_sha256": p1._sha(inner_stdout_path),
        "inner_stderr_relative_path": inner_stderr_path.relative_to(root).as_posix(),
        "inner_stderr_bytes": inner_stderr_path.stat().st_size,
        "inner_stderr_sha256": p1._sha(inner_stderr_path),
        "outer_resource_envelope_close_schema": p1.OUTER_RESOURCE_ENVELOPE_CLOSE_SCHEMA,
        "outer_resource_envelope_close_relative_path": close_path.relative_to(
            root
        ).as_posix(),
        "outer_resource_envelope_close_raw_sha256": p1._sha(close_path),
        "outer_resource_envelope_close_canonical_sha256": p1._canonical_sha(
            outer_close
        ),
        "outer_resource_envelope_mandatory_gate_pass": True,
        "tombstone_relative_path": token_path.relative_to(root).as_posix(),
        "tombstone_sha256": tombstone_sha,
        "tombstone_schema": p1.TOMBSTONE_SCHEMA,
        "pre_exit_evidence_relative_path": pre_exit_relative,
        "pre_exit_evidence_sha256": p1._sha(pre_exit_path),
        "final_control_plane_session_index_relative_path": final_index_relative,
        "final_control_plane_session_index_sha256": final_index_sha,
        "inner_reported_exit_code": intended_exit,
        "inner_actual_exit_code": intended_exit,
        "inner_exit_observed": True,
        "inner_exit_code_matches_completion": True,
        "authoritative_inner_disposition": (
            "authoritative_inner_pass_observed"
            if passed
            else "authoritative_inner_failure_observed"
        ),
        "terminal_evidence_complete": True,
        "tombstone_success_was_provisional_without_this_seal": passed,
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
        "sealed_utc": outer_utc,
        "authoritative_stage_pass": passed,
        "supersedes_provisional_tombstone_and_any_present_result_disposition": True,
    }
    assert set(seal) == p1.OUTER_TERMINAL_SEAL_FIELDS
    _write_json(seal_path, seal)
    return {
        **bundle,
        "manifest": manifest_value,
        "tombstone": tombstone,
        "tombstone_sha256": tombstone_sha,
        "guard_path": guard_path,
        "resource_path": resource_path,
        "result_path": result_path,
        "published_path": published_path,
        "marker_paths": marker_paths,
        "pre_exit": pre_exit,
        "pre_exit_path": pre_exit_path,
        "complete": complete,
        "complete_path": complete_path,
        "exit_release": exit_release,
        "exit_path": exit_path,
        "outer_close": outer_close,
        "outer_close_path": close_path,
        "seal": seal,
        "seal_path": seal_path,
        "control_context": control_context,
        "passed": passed,
    }


def _write_postvalidated_emergency_journal(
    bundle: dict[str, object], tombstone_context: dict[str, object]
) -> dict[str, object]:
    root = bundle["root"]
    token_path = bundle["token_path"]
    tombstone = bundle["tombstone"]
    claim_context = tombstone_context["claim"]
    nonce = (
        tombstone_context["observer"]["nonce"]
        if tombstone_context["outer_emergency"] is True
        else claim_context["value"]["guard_nonce"]
    )
    identity = f"{tombstone['consumed_review_token_id']}-{nonce}"
    journal_root = (
        root
        / "validation-output"
        / "av-bs1"
        / "emergency-replacement-journals"
    )
    journal_root.mkdir(parents=True, exist_ok=True)
    intent_path = journal_root / f"{identity}.intent.json"
    postvalidation_path = journal_root / f"{identity}.postvalidation.json"
    recovery_path = journal_root / f"{identity}.authorized-token-backup.bin"
    backup_path = token_path.parent / (
        f".{token_path.name}.replaced." + "9" * 32 + ".bak"
    )
    original_bytes = bundle["original_token_bytes"]
    backup_path.write_bytes(original_bytes)
    recovery_path.write_bytes(original_bytes)
    intent = {
        "schema": p1.EMERGENCY_REPLACEMENT_INTENT_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorized_stage": p1.AUTHORIZED_STAGE,
        "journal_identity": identity,
        "review_token_id": tombstone["consumed_review_token_id"],
        "original_review_token_sha256": tombstone[
            "consumed_review_token_sha256"
        ],
        "original_review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "claim_relative_path": claim_context["relative_path"],
        "claim_sha256": claim_context["sha256"],
        "guard_sha256": tombstone["guard_contract_sha256"],
        "attempt_status": tombstone["attempt_status"],
        "target_relative_path": token_path.relative_to(root).as_posix(),
        "file_replace_backup_relative_path": backup_path.relative_to(
            root
        ).as_posix(),
        "expected_original_review_token_sha256": tombstone[
            "consumed_review_token_sha256"
        ],
        "expected_original_review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "pre_replace_observed_sha256": tombstone[
            "consumed_review_token_sha256"
        ],
        "pre_replace_observed_exact_retained_bytes_match": True,
        "replacement_state": "pending_file_replace_postvalidation",
        "file_replace_is_atomic_compare_and_swap": False,
        "prior_bytes_verified_from_file_replace_backup": False,
        "replacement_authority": "none_pending_postvalidation",
        "authorization_blocker": True,
        "terminal_evidence_complete": False,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "created_utc": UTC,
    }
    _write_json(intent_path, intent)
    recovery_sha256 = p1._sha(recovery_path)
    postvalidation = {
        "schema": p1.EMERGENCY_REPLACEMENT_POSTVALIDATION_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorized_stage": p1.AUTHORIZED_STAGE,
        "journal_identity": identity,
        "review_token_id": tombstone["consumed_review_token_id"],
        "original_review_token_sha256": tombstone[
            "consumed_review_token_sha256"
        ],
        "original_review_token_canonical_sha256": tombstone[
            "consumed_review_token_canonical_sha256"
        ],
        "claim_relative_path": claim_context["relative_path"],
        "claim_sha256": claim_context["sha256"],
        "guard_sha256": tombstone["guard_contract_sha256"],
        "attempt_status": tombstone["attempt_status"],
        "intent_relative_path": intent_path.relative_to(root).as_posix(),
        "intent_sha256": p1._sha(intent_path),
        "recovery_relative_path": recovery_path.relative_to(root).as_posix(),
        "recovery_sha256": recovery_sha256,
        "emergency_tombstone_relative_path": token_path.relative_to(
            root
        ).as_posix(),
        "emergency_tombstone_sha256": bundle["tombstone_sha256"],
        "emergency_tombstone_schema": p1.EMERGENCY_TOMBSTONE_SCHEMA,
        "file_replace_is_atomic_compare_and_swap": False,
        "prior_bytes_verified_from_file_replace_backup": True,
        "backup_exact_retained_original_bytes_match": True,
        "replacement_state": (
            "replacement_postvalidated_pending_external_observer"
        ),
        "replacement_authority": (
            "postvalidated_file_replacement_only_not_terminal_lifecycle_evidence"
        ),
        "authorization_blocker": True,
        "lifecycle_completion_claimed": False,
        "terminal_evidence_complete": False,
        "external_observer_required_for_terminal_exit": True,
        "external_runner_or_machine_kill_terminal_state_guaranteed": False,
        "recorded_utc": UTC,
    }
    _write_json(postvalidation_path, postvalidation)
    return {
        "intent_path": intent_path,
        "postvalidation_path": postvalidation_path,
        "recovery_path": recovery_path,
        "backup_path": backup_path,
    }


def _assert_preflight_control_bundle_rejected(
    bundle: dict[str, object],
    expected_message: str,
    *,
    expected_code: str = "BLOCKED_AV_BS_RESULT_SCHEMA",
) -> None:
    with pytest.raises(p1.AvBsError) as caught:
        p1._validate_preflight_control_plane_evidence(
            bundle["claim"], bundle["manifest"], bundle["observer"]
        )

    assert caught.value.code == expected_code
    assert expected_message in str(caught.value)


def test_preflight_control_chain_accepts_final_index_0002_and_rejects_0001(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _build_preflight_control_plane_bundle(monkeypatch, tmp_path)

    p1._validate_preflight_control_plane_evidence(
        bundle["claim"], bundle["manifest"], bundle["observer"]
    )

    preclose_claim = deepcopy(bundle["claim"])
    preclose_path = bundle["preclose_path"]
    preclose_claim["control_plane_preflight_session_index_relative_path"] = (
        preclose_path.relative_to(bundle["root"]).as_posix()
    )
    preclose_claim["control_plane_preflight_session_index_sha256"] = p1._sha(
        preclose_path
    )
    bundle["claim"] = preclose_claim

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight final control index relative path invalid"
    )


@pytest.mark.parametrize(
    ("artifact", "expected_message"),
    [
        ("report", "preflight control report field set mismatch"),
        ("preclose", "preflight preclose control index field set mismatch"),
        ("close", "preflight control envelope-close field set mismatch"),
        ("final_index", "preflight final control index field set mismatch"),
        ("final_reference", "control report reference field set mismatch"),
    ],
)
def test_preflight_control_chain_rejects_extra_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact: str,
    expected_message: str,
) -> None:
    mutation_name = "final_index" if artifact == "final_reference" else artifact

    def add_extra(value: dict[str, object]) -> None:
        if artifact == "final_reference":
            value["report_references"][0]["extra"] = False
        else:
            value["extra"] = False

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={mutation_name: add_extra}
    )

    _assert_preflight_control_bundle_rejected(bundle, expected_message)


def test_preflight_control_report_requires_candidate_scope_hash_not_null(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def null_scope(report: dict[str, object]) -> None:
        report["execution_resource_scope_sha256"] = None

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"report": null_scope}
    )

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight control report execution_resource_scope_sha256 mismatch"
    )


def test_preflight_control_envelope_close_mandatory_gate_cannot_be_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def clear_gate(close: dict[str, object]) -> None:
        close["mandatory_control_plane_gate_pass"] = False

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"close": clear_gate}
    )

    _assert_preflight_control_bundle_rejected(
        bundle,
        "preflight control envelope-close mandatory_control_plane_gate_pass mismatch",
    )


@pytest.mark.parametrize(
    ("target", "expected_message"),
    [
        ("report_sha", "preflight control-plane artifact checksum mismatch"),
        ("report_path", "preflight control report relative path invalid"),
        ("index_sha", "preflight control-plane artifact checksum mismatch"),
        ("close_sha", "preflight control index chain checksum mismatch"),
        ("close_path", "preflight envelope close parent mismatch"),
    ],
)
def test_preflight_control_chain_rejects_sha_and_path_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target: str,
    expected_message: str,
) -> None:
    mutations: dict[str, object] = {}
    if target == "close_sha":

        def tamper_close_sha(final_index: dict[str, object]) -> None:
            final_index["envelope_close_sha256"] = SHA_A
            final_index["report_references"][0]["envelope_close_sha256"] = SHA_A

        mutations["final_index"] = tamper_close_sha
    elif target == "close_path":
        wrong_path = (
            tmp_path
            / "repo"
            / "validation-output"
            / "av-bs1"
            / "control-plane"
            / ("session-" + "1" * 32)
            / "control-plane-envelope-close.json"
        ).resolve()
        correct_close_path = (
            wrong_path.parent
            / ("preflight-" + "2" * 32)
            / "control-plane-envelope-close.json"
        )

        def tamper_close_path(final_index: dict[str, object]) -> None:
            wrong_path.write_bytes(correct_close_path.read_bytes())
            final_index["envelope_close_path"] = str(wrong_path)

        mutations["final_index"] = tamper_close_path

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations=mutations
    )
    claim = bundle["claim"]
    if target == "report_sha":
        claim["control_plane_preflight_report_sha256"] = SHA_A
    elif target == "report_path":
        claim["control_plane_preflight_report_relative_path"] = (
            "../validation-output/av-bs1/control-plane/report.json"
        )
    elif target == "index_sha":
        claim["control_plane_preflight_session_index_sha256"] = SHA_A

    _assert_preflight_control_bundle_rejected(bundle, expected_message)


@pytest.mark.parametrize("argv_kind", ["target_path", "process_prefix"])
def test_preflight_control_chain_rejects_argv_path_or_prefix_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv_kind: str
) -> None:
    escaped_path = str((tmp_path / "outside.py").resolve())

    def tamper_argv(report: dict[str, object]) -> None:
        key = "target_argv" if argv_kind == "target_path" else "process_argv"
        report[key][0] = escaped_path
        report[f"{key}_json_sha256"] = p1._ordered_json_sha256(report[key])

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"report": tamper_argv}
    )

    expected = (
        "preflight control report target_argv mismatch"
        if argv_kind == "target_path"
        else "preflight control argv mismatch"
    )
    _assert_preflight_control_bundle_rejected(bundle, expected)


def test_preflight_control_stdout_wrapper_checksum_is_mandatory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, corrupt_stdout_checksum=True
    )

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight control stdout wrapper checksum invalid"
    )


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("outer_observer_contract_sha256", None),
        (
            "expected_terminal_seal_relative_path",
            "validation-output/av-bs1/outer-observer-terminal-seals/"
            + "f" * 32
            + ".json",
        ),
        ("terminal_seal_required_for_authoritative_disposition", False),
    ],
)
def test_preflight_control_stdout_requires_exact_outer_observer_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    tampered_value: object,
) -> None:
    def tamper_outer_binding(payload: dict[str, object]) -> None:
        if tampered_value is None:
            payload.pop(field)
        else:
            payload[field] = tampered_value

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch,
        tmp_path,
        mutations={"preflight_payload": tamper_outer_binding},
    )

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight control stdout payload mismatch"
    )


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("missing_outer_contract_sha", "claim field set mismatch"),
        (
            "drift_terminal_seal_path",
            "claim expected_terminal_seal_relative_path mismatch",
        ),
        (
            "clear_terminal_seal_required",
            "claim terminal_seal_required_for_authoritative_disposition mismatch",
        ),
        ("missing_handshake_prefix", "claim field set mismatch"),
        ("drift_handshake_prefix_sha", "outer observer handshake prefix mismatch"),
    ],
)
def test_claim_requires_outer_fields_and_canonical_handshake_prefix_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tamper: str,
    expected_message: str,
) -> None:
    bundle = _build_preflight_control_plane_bundle(monkeypatch, tmp_path)
    claim_path = bundle["root"] / bundle["claim"]["claim_relative_path"]
    claim_path.parent.mkdir(parents=True)
    monkeypatch.setattr(p1, "_git_head", lambda: bundle["claim"]["git_head"])
    _write_json(claim_path, bundle["claim"])

    p1._validate_claim(claim_path, bundle["claim_token"], bundle["manifest"])

    claim = deepcopy(bundle["claim"])
    if tamper == "missing_outer_contract_sha":
        claim.pop("outer_observer_contract_sha256")
    elif tamper == "drift_terminal_seal_path":
        claim["expected_terminal_seal_relative_path"] = (
            "validation-output/av-bs1/outer-observer-terminal-seals/"
            + "f" * 32
            + ".json"
        )
    elif tamper == "clear_terminal_seal_required":
        claim["terminal_seal_required_for_authoritative_disposition"] = False
    elif tamper == "missing_handshake_prefix":
        claim.pop("outer_observer_handshake_prefix")
    else:
        claim["outer_observer_handshake_prefix_sha256"] = SHA_A
    _write_json(claim_path, claim)

    with pytest.raises(p1.AvBsError, match=expected_message) as caught:
        p1._validate_claim(claim_path, bundle["claim_token"], bundle["manifest"])

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


@pytest.mark.parametrize(
    ("mutation_name", "tamper", "expected_message"),
    [
        ("outer_ready", "ready_extra", "outer observer marker field set mismatch"),
        (
            "outer_start_release",
            "release_extra",
            "outer observer marker field set mismatch",
        ),
        ("outer_ready", "ready_pid_bool", "outer process id"),
        (
            "outer_start_release",
            "release_bool_alias",
            "outer start-release claim_handshake_prefix_ready mismatch",
        ),
        ("outer_prefix", "prefix_extra", "outer observer handshake prefix mismatch"),
        ("outer_prefix", "ready_hash", "outer observer marker binding mismatch"),
        ("outer_ready", "session_nonce", "outer inner-ready identity invalid"),
        ("outer_ready", "inner_birth_before_outer", "outer inner-ready identity invalid"),
        (
            "outer_start_release",
            "release_before_ready",
            "outer observer marker chronology or claim identity mismatch",
        ),
    ],
)
def test_outer_handshake_markers_are_exact_hash_bound_and_chronological(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation_name: str,
    tamper: str,
    expected_message: str,
) -> None:
    def tamper_marker(value: dict[str, object]) -> None:
        if tamper in {"ready_extra", "release_extra", "prefix_extra"}:
            value["extra"] = False
        elif tamper == "ready_pid_bool":
            value["outer_process_id"] = True
        elif tamper == "release_bool_alias":
            value["claim_handshake_prefix_ready"] = 1
        elif tamper == "ready_hash":
            value["inner_ready_sha256"] = SHA_A
        elif tamper == "session_nonce":
            value["observer_nonce"] = "5" * 32
        elif tamper == "inner_birth_before_outer":
            value["inner_process_birth_utc_ticks"] = 999
        else:
            value["monotonic_ns"] = 0

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={mutation_name: tamper_marker}
    )
    claim_path = bundle["root"] / bundle["claim"]["claim_relative_path"]
    claim_path.parent.mkdir(parents=True)
    _write_json(claim_path, bundle["claim"])
    monkeypatch.setattr(p1, "_git_head", lambda: bundle["claim"]["git_head"])

    with pytest.raises(p1.AvBsError, match=expected_message) as caught:
        p1._validate_claim(claim_path, bundle["claim_token"], bundle["manifest"])

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


@pytest.mark.parametrize(
    ("claim_field", "tampered_value"),
    [("parent_pid", 101), ("parent_pid_birth_utc_ticks", 1_000)],
)
def test_outer_handshake_requires_claim_parent_to_be_inner_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    claim_field: str,
    tampered_value: int,
) -> None:
    bundle = _build_preflight_control_plane_bundle(monkeypatch, tmp_path)
    claim = deepcopy(bundle["claim"])
    claim[claim_field] = tampered_value
    claim_path = bundle["root"] / claim["claim_relative_path"]
    claim_path.parent.mkdir(parents=True)
    _write_json(claim_path, claim)
    monkeypatch.setattr(p1, "_git_head", lambda: claim["git_head"])

    with pytest.raises(
        p1.AvBsError,
        match="outer observer marker chronology or claim identity mismatch",
    ) as caught:
        p1._validate_claim(claim_path, bundle["claim_token"], bundle["manifest"])

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [("execution_tree_root_pid", 151), ("execution_tree_root_birth_utc_ticks", 1_500)],
)
def test_preflight_control_report_root_must_be_outer_observer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    tampered_value: int,
) -> None:
    def tamper_root(report: dict[str, object]) -> None:
        report[field] = tampered_value

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"report": tamper_root}
    )

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight control process gate failed"
    )


def test_preflight_helper_birth_must_not_predate_inner_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def predate_inner(report: dict[str, object]) -> None:
        report["process_birth_utc_ticks"] = 1_499
        report["observed_process_identities"][2]["birth_utc_ticks"] = 1_499
        report["cleanup_observed_process_identities"][0]["birth_utc_ticks"] = 1_499

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"report": predate_inner}
    )

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight control process gate failed"
    )


def test_observed_nonroot_identity_birth_must_not_predate_outer_observer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def add_early_identity(report: dict[str, object]) -> None:
        report["observed_process_ids"].insert(2, 175)
        report["observed_process_identities"].insert(
            2, {"process_id": 175, "birth_utc_ticks": 999}
        )

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"report": add_early_identity}
    )

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight control identity coverage mismatch"
    )


@pytest.mark.parametrize(
    ("identity_rows", "row_index"),
    [
        ("observed_process_identities", 0),
        ("observed_process_identities", 1),
        ("observed_process_identities", 2),
        ("cleanup_observed_process_identities", 0),
    ],
)
def test_preflight_control_identity_row_births_must_match_report_identities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    identity_rows: str,
    row_index: int,
) -> None:
    def tamper_birth(report: dict[str, object]) -> None:
        row = report[identity_rows][row_index]
        row["birth_utc_ticks"] += 1

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"report": tamper_birth}
    )

    _assert_preflight_control_bundle_rejected(
        bundle, "preflight control identity coverage mismatch"
    )


@pytest.mark.parametrize(
    ("sample_name", "expected_message"),
    [
        (
            "pre_helper_after_provenance_tree",
            "preflight control report peak does not cover working_set_bytes",
        ),
        (
            "pre_spawn_system",
            "preflight control report pre_spawn_system system sample is not "
            "represented by the claimed peak",
        ),
        (
            "pre_helper_after_provenance_system",
            "preflight control report pre_helper_after_provenance_system system "
            "sample is not represented by the claimed peak",
        ),
    ],
)
def test_preflight_control_report_peak_must_cover_all_report_samples(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_name: str,
    expected_message: str,
) -> None:
    def exceed_report_peak(report: dict[str, object]) -> None:
        sample = report[sample_name]
        if sample_name.endswith("_tree"):
            sample["working_set_bytes"] = report["peak"]["tree_working_set_bytes"] + 1
            sample["nonprivate_working_set_proxy_bytes"] = (
                sample["working_set_bytes"] - sample["private_working_set_bytes"]
            )
        else:
            sample["commit_total_bytes"] = (
                report["peak"]["system_commit_total_bytes"] + 1
            )
            sample["commit_limit_bytes"] = (
                sample["commit_total_bytes"] + sample["commit_headroom_bytes"]
            )

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"report": exceed_report_peak}
    )

    _assert_preflight_control_bundle_rejected(bundle, expected_message)


@pytest.mark.parametrize(
    ("sample_name", "expected_message"),
    [
        (
            "envelope_close_runner_tree_sample",
            "preflight control envelope-close peak does not cover working_set_bytes",
        ),
        (
            "final_system",
            "preflight control envelope-close system sample is not represented by "
            "the claimed peak",
        ),
    ],
)
def test_preflight_control_close_peak_must_cover_close_samples(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_name: str,
    expected_message: str,
) -> None:
    def exceed_close_peak(close: dict[str, object]) -> None:
        sample = close[sample_name]
        if sample_name.endswith("_sample"):
            sample["working_set_bytes"] = close["peak"]["tree_working_set_bytes"] + 1
            sample["nonprivate_working_set_proxy_bytes"] = (
                sample["working_set_bytes"] - sample["private_working_set_bytes"]
            )
        else:
            sample["commit_total_bytes"] = (
                close["peak"]["system_commit_total_bytes"] + 1
            )
            sample["commit_limit_bytes"] = (
                sample["commit_total_bytes"] + sample["commit_headroom_bytes"]
            )

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={"close": exceed_close_peak}
    )

    _assert_preflight_control_bundle_rejected(bundle, expected_message)


@pytest.mark.parametrize(
    ("binding", "expected_message"),
    [
        ("bootstrap", "preflight control helper executable binding mismatch"),
        ("canonical_helper", "preflight control helper executable binding mismatch"),
        ("python", "preflight control Python executable mismatch"),
    ],
)
def test_preflight_control_helpers_are_frozen_and_python_is_sys_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    binding: str,
    expected_message: str,
) -> None:
    mutations: dict[str, object]
    if binding == "python":
        alternate_python = tmp_path / "alternate-python.exe"
        alternate_python.write_bytes(b"not-the-running-python")

        def replace_python(report: dict[str, object]) -> None:
            report["python_path"] = str(alternate_python.resolve())
            report["python_sha256"] = p1._sha(alternate_python)

        mutations = {"report": replace_python}
    else:

        def alter_helper(path: Path) -> None:
            path.write_bytes(path.read_bytes() + b"\n# tampered\n")

        mutations = {binding: alter_helper}

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations=mutations
    )

    _assert_preflight_control_bundle_rejected(bundle, expected_message)


@pytest.mark.parametrize(
    ("marker_name", "field", "tampered_value", "expected_message", "expected_code"),
    [
        (
            "ready",
            "monotonic_ns",
            12,
            "preflight control marker chronology mismatch",
            "BLOCKED_AV_BS_RESULT_SCHEMA",
        ),
        (
            "start_release",
            "sample_perf_counter_ns",
            21,
            "preflight control marker chronology mismatch",
            "BLOCKED_AV_BS_RESULT_SCHEMA",
        ),
        (
            "completion",
            "monotonic_ns",
            22,
            "preflight control marker chronology mismatch",
            "BLOCKED_AV_BS_RESULT_SCHEMA",
        ),
        (
            "exit_release",
            "sample_perf_counter_ns",
            19,
            "preflight control marker chronology mismatch",
            "BLOCKED_AV_BS_RESULT_SCHEMA",
        ),
        (
            "start_release",
            "sample_utc",
            "2026-08-14T23:59:59Z",
            "preflight control marker chronology mismatch",
            "BLOCKED_AV_BS_RESULT_SCHEMA",
        ),
        (
            "exit_release",
            "sample_utc",
            "2026-08-15T00:00:01Z",
            "preflight control wall gate failed",
            "BLOCKED_AV_BS_RESOURCE",
        ),
    ],
)
def test_preflight_control_marker_order_and_release_utc_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    marker_name: str,
    field: str,
    tampered_value: object,
    expected_message: str,
    expected_code: str,
) -> None:
    def tamper_marker(marker: dict[str, object]) -> None:
        marker[field] = tampered_value

    bundle = _build_preflight_control_plane_bundle(
        monkeypatch, tmp_path, mutations={marker_name: tamper_marker}
    )

    _assert_preflight_control_bundle_rejected(
        bundle, expected_message, expected_code=expected_code
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema":"x","schema":"y"}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":1e999}',
        '{"value":-1e999}',
    ],
)
def test_strict_json_reader_rejects_duplicates_and_nonfinite_constants(
    tmp_path: Path, raw: str
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(p1.AvBsError) as caught:
        p1._read(path, "synthetic")

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


def test_wrapper_rejects_extra_fields(tmp_path: Path) -> None:
    payload = {"schema": "synthetic"}
    path = tmp_path / "wrapper.json"
    _write_json(
        path,
        {
            "payload": payload,
            "payload_sha256": p1._canonical_sha(payload),
            "extra": False,
        },
    )

    with pytest.raises(p1.AvBsError) as caught:
        p1._read_wrapper(path, "synthetic")

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (True, 1),
        (False, 0),
        (1, 1.0),
        ({"flag": 1}, {"flag": True}),
        ([False, 1], [0, 1]),
    ],
)
def test_strict_json_equality_rejects_numeric_type_aliases(
    actual: object, expected: object
) -> None:
    assert p1._strict_json_equal(actual, expected) is False


def test_manifest_is_token_missing_factor_free_and_parent_bound() -> None:
    payload = manifest()["payload"]
    assert payload["status"] == "candidate_token_missing_no_factor"
    assert payload["authorization_state"] == "not_authorized"
    assert payload["factorization_performed"] is False
    assert payload["physics_solve_performed"] is False
    assert payload["available_solve_stages"] == []
    assert payload["token_gated_stages"] == []
    assert "primary-h4-p0r" in payload["unavailable_stages"]
    assert payload["next_stage_authorized"] is False
    assert payload["one_use_lifecycle"]["token_absent"] is True
    assert payload["one_use_lifecycle"]["consume_after_every_owned_claim_outcome"] is False
    assert payload["one_use_lifecycle"]["external_runner_or_consumer_kill_tombstone_guarantee"] is False
    assert payload["p0r_parent"]["status"] == "preregistered_H4_P0R_contract_only_no_factor"
    assert payload["factorization_contract"]["factor_order"] == [
        "A_background_II",
        "A_conductor_II",
    ]
    control_plane = payload["execution_resource_scope"]["control_plane"]
    assert control_plane["independently_bounded"] is True
    assert control_plane["tree_thresholds_equal_factor_envelope"] is True
    assert control_plane["system_floor_recheck_before_and_after_each"] is True
    assert control_plane["implementation_status"] == "implemented_and_static_audited"
    assert control_plane["authorization_blocker"] is False
    assert p1._authorization_prerequisites_ready(
        payload["execution_resource_scope"], payload["outer_observer_contract"]
    ) is True
    assert payload["execution_resource_scope_sha256"] == p1._canonical_sha(
        payload["execution_resource_scope"]
    )


def test_manifest_freezes_outer_observer_preregistration_contract() -> None:
    payload = manifest()["payload"]
    expected = {
        "schema": "AV-BS1-h4-p0r-outer-observer-contract-v2",
        "contract_revision": "P1_outer_observer_v2",
        "public_stage": "primary-h4-p0r",
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
                "schema": "AV-BS1-h4-p0r-outer-inner-ready-v1",
                "writer": "inner",
            },
            {
                "role": "outer_start_release",
                "file_name": "outer-start-release.json",
                "schema": "AV-BS1-h4-p0r-outer-start-release-v1",
                "writer": "outer",
            },
            {
                "role": "inner_complete",
                "file_name": "inner-complete.json",
                "schema": "AV-BS1-h4-p0r-outer-inner-complete-v1",
                "writer": "inner",
            },
            {
                "role": "outer_exit_release",
                "file_name": "outer-exit-release.json",
                "schema": "AV-BS1-h4-p0r-outer-exit-release-v1",
                "writer": "outer",
            },
        ],
        "terminal_seal_schema": "AV-BS1-h4-p0r-outer-terminal-seal-v2",
        "outer_resource_envelope_close_schema": (
            "AV-BS1-h4-p0r-outer-resource-envelope-close-v2"
        ),
        "outer_resource_envelope_close_file_name": (
            "outer-resource-envelope-close.json"
        ),
        "handshake_prefix_schema": (
            "AV-BS1-h4-p0r-outer-observer-handshake-prefix-v1"
        ),
        "terminal_seal_required_for_authoritative_disposition": True,
        "outer_resource_envelope": {
            "wall_start_event": (
                "dispatcher_stopwatch_start_before_candidate_token_hash_session_"
                "setup_and_inner_spawn"
            ),
            "wall_end_event": (
                "after_retained_inner_actual_exit_cleanup_and_final_preseal_samples_"
                "before_envelope_close_materialization"
            ),
            "wall_stop_seconds": 900,
            "tree_thresholds_equal_factor_envelope": True,
            "system_floor_recheck_before_and_after": True,
            "execution_tree_root": "outer_observer_pid_and_birth",
            "included_processes": (
                "outer_observer_plus_identity_bound_sampled_inner_tree"
            ),
            "process_membership_semantics": (
                "sampled_not_Job_Object_descendants_created_and_exited_between_"
                "polls_not_claimed"
            ),
            "tree_sample_max_attempts": 3,
            "tree_sample_retry_event_limit": 16,
            "tree_sample_retry_policy": (
                "whole_sample_retry_only_after_identity_bound_nonroot_exit_or_"
                "win32_error_87_and_complete_toolhelp_snapshot_absence_with_"
                "stable_root_identity"
            ),
            "tree_sample_failure_policy": (
                "root_loss_pid_reuse_live_query_failure_access_denial_incomplete_"
                "snapshot_and_retry_exhaustion_remain_fatal"
            ),
            "tree_sample_failure_evidence": (
                "fixed_ascii_message_codes_operations_numeric_identity_and_"
                "win32_status_only_no_localized_exception_text"
            ),
            "excluded_head": [
                "public_PowerShell_process_startup_and_script_parse",
                "function_and_native_monitor_type_initialization",
                (
                    "immutable_runner_path_and_argument_discovery_before_dispatcher_"
                    "stopwatch"
                ),
            ],
            "excluded_tail": [
                "post_cleanup_token_and_claim_recovery_classification",
                "outer_resource_envelope_close_materialization_and_readback",
                "terminal_seal_source_rechecks_materialization_and_readback",
                "inner_stdout_text_return_packaging",
                "outer_observer_exit",
            ],
            "seal_binding": (
                "terminal_seal_binds_outer_resource_envelope_close_raw_and_"
                "canonical_hashes"
            ),
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

    assert p1._strict_json_equal(payload["outer_observer_contract"], expected)
    assert payload["outer_observer_contract_sha256"] == p1._canonical_sha(expected)


def test_manifest_distinguishes_factor_interval_from_lifecycle_peak_gates() -> None:
    scope = manifest()["payload"]["execution_resource_scope"]
    factor_envelope = scope["factor_fit_envelope"]

    assert factor_envelope["execution_tree_root"] == "outer_observer_pid_and_birth"
    assert factor_envelope["included_processes"] == (
        "outer_observer_plus_inner_runner_plus_identity_bound_processes_present_in_"
        "successful_100ms_Toolhelp32_samples"
    )
    assert factor_envelope["peak_semantics"] == (
        "sampled_simultaneous_tree_sums_are_factor_interval_measurements; "
        "outer_and_inner_OS_lifetime_peaks_are_conservative_lifecycle_contaminated_"
        "stop_gates_only"
    )
    control = scope["control_plane"]
    assert control["excluded_from_factor_interval_simultaneous_measurements"] == [
        "preflight",
        "canonical_json_hash_helpers",
        "finalizer",
        "token_consumer",
    ]
    assert (
        control[
            "conservative_outer_and_inner_lifetime_stop_fields_include_control_plane_history"
        ]
        is True
    )
    assert control["also_included_in_outer_lifecycle_envelope"] is True


def test_runner_public_stage_validate_set_matches_outer_observer_contract() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    match = re.search(
        r'\[ValidateSet\((?P<values>[^)]*)\)\]\s*\[string\]\$Stage', source
    )
    assert match is not None
    public_stages = re.findall(r'"([^"]+)"', match.group("values"))
    outer_observer = manifest()["payload"]["outer_observer_contract"]

    assert public_stages == ["manifest", "primary-h4-p0r"]
    assert outer_observer["public_stage"] in public_stages
    assert outer_observer["hidden_internal_mode"] not in public_stages


def test_manifest_freezes_current_execution_schema_map() -> None:
    expected = {
        "execution_fixture": "AV-BS1-h4-p0r-execution-fixture-v1",
        "execution_resource_scope": (
            "AV-BS1-h4-p0r-execution-resource-scope-v1"
        ),
        "outer_observer_contract": "AV-BS1-h4-p0r-outer-observer-contract-v2",
        "review_token": "AV-BS1-h4-p0r-review-token-v1",
        "claim": "AV-BS1-h4-p0r-token-claim-v1",
        "guard": "AV-BS1-h4-p0r-resource-guard-v1",
        "resource": "AV-BS1-h4-p0r-resource-report-v1",
        "factor_report": "AV-BS1-h4-p0r-factor-report-v1",
        "result": "AV-BS1-h4-p0r-result-v2",
        "failure": "AV-BS1-h4-p0r-failure-v1",
        "consumed_tombstone": "AV-BS1-h4-p0r-consumed-review-token-v2",
        "emergency_consumed_tombstone": (
            "AV-BS1-h4-p0r-emergency-consumed-review-token-v2"
        ),
        "emergency_replacement_intent": (
            "AV-BS1-h4-p0r-emergency-replacement-intent-v1"
        ),
        "emergency_replacement_postvalidation": (
            "AV-BS1-h4-p0r-emergency-replacement-postvalidation-v1"
        ),
        "control_plane_process_report": (
            "AV-BS1-h4-p0r-control-plane-process-report-v1"
        ),
        "control_plane_session_index": (
            "AV-BS1-h4-p0r-control-plane-session-index-v1"
        ),
        "control_plane_envelope_close": (
            "AV-BS1-h4-p0r-control-plane-envelope-close-v1"
        ),
        "control_plane_pre_exit_intent": (
            "AV-BS1-h4-p0r-control-plane-pre-exit-intent-evidence-v1"
        ),
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
        "outer_inner_ready": "AV-BS1-h4-p0r-outer-inner-ready-v1",
        "outer_start_release": "AV-BS1-h4-p0r-outer-start-release-v1",
        "outer_observer_handshake_prefix": (
            "AV-BS1-h4-p0r-outer-observer-handshake-prefix-v1"
        ),
        "outer_inner_complete": "AV-BS1-h4-p0r-outer-inner-complete-v1",
        "outer_exit_release": "AV-BS1-h4-p0r-outer-exit-release-v1",
        "outer_resource_envelope_close": (
            "AV-BS1-h4-p0r-outer-resource-envelope-close-v2"
        ),
        "outer_terminal_seal": "AV-BS1-h4-p0r-outer-terminal-seal-v2",
        "manifest_terminal_evidence_classification": (
            "AV-BS1-h4-p0r-manifest-terminal-evidence-classification-v1"
        ),
    }

    assert p1._strict_json_equal(
        manifest()["payload"]["current_execution_schemas"], expected
    )


def test_ready_outer_observer_keeps_unsealed_disposition_provisional() -> None:
    payload = manifest()["payload"]
    outer_observer = payload["outer_observer_contract"]
    lifecycle = payload["one_use_lifecycle"]

    assert outer_observer["implementation_status"] == "implemented_and_static_audited"
    assert outer_observer["authorization_blocker"] is False
    assert outer_observer["terminal_seal_required_for_authoritative_disposition"] is True
    assert outer_observer["missing_or_invalid_seal_disposition"] == (
        "provisional_no_authoritative_pass_or_H4_P1_authorization"
    )
    assert lifecycle["lifecycle_supervisor_status"] == "implemented_and_static_audited"
    assert lifecycle["terminal_seal_required_for_authoritative_disposition"] is True
    assert lifecycle["tombstone_success_remains_provisional_without_terminal_seal"] is True
    assert payload["authorization_state"] == "not_authorized"
    assert payload["available_solve_stages"] == []
    assert payload["token_gated_stages"] == []
    assert payload["unavailable_stages"] == [
        "primary-h4-p0r",
        "h4",
        "withheld",
        "EQ0",
    ]
    assert payload["next_stage_authorized"] is False


def test_result_normal_and_emergency_v2_share_exact_pending_terminal_abi() -> None:
    assert len(PENDING_TERMINAL_FIELDS) == 7
    assert PENDING_TERMINAL_FIELDS <= p1.NORMAL_TOMBSTONE_FIELDS
    assert PENDING_TERMINAL_FIELDS <= p1.EMERGENCY_TOMBSTONE_FIELDS
    assert p1.RESULT_SCHEMA == "AV-BS1-h4-p0r-result-v2"
    assert p1.TOMBSTONE_SCHEMA == "AV-BS1-h4-p0r-consumed-review-token-v2"
    assert p1.EMERGENCY_TOMBSTONE_SCHEMA == (
        "AV-BS1-h4-p0r-emergency-consumed-review-token-v2"
    )

    source = FIXTURE.read_text(encoding="utf-8")
    result_validator = source[
        source.index("def _validate_result_payload(") : source.index("def consume(")
    ]
    result_emitter = source[
        source.index("def finalize(") : source.index("def _validate_result_payload(")
    ]
    for field in PENDING_TERMINAL_FIELDS:
        assert f'"{field}"' in result_validator
        assert f'"{field}"' in result_emitter


def test_pending_outer_observer_rejects_otherwise_valid_authorized_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_manifest = deepcopy(manifest()["payload"])
    pending_manifest = deepcopy(ready_manifest)
    pending_manifest["outer_observer_contract"].update(
        {
            "implementation_status": "runner_and_python_binding_pending",
            "authorization_blocker": True,
        }
    )
    binding = ready_manifest["bindings"]
    identifier = "0" * 32
    authorized_token = {
        "schema": p1.TOKEN_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorized_stage": "primary-h4-p0r",
        "authorization_state": "authorized",
        "uses_remaining": 1,
        "next_stage_authorized": False,
        "fixture_sha256": binding["fixture_sha256"],
        "runner_sha256": binding["runner_sha256"],
        "static_test_sha256": binding["static_test_sha256"],
        "preregistration_doc_sha256": binding["preregistration_doc_sha256"],
        "manifest_payload_sha256": binding["manifest_payload_sha256"],
        "matrix_contract_sha256": binding["matrix_contract_sha256"],
        "resource_policy_sha256": ready_manifest["resource_policy_sha256"],
        "execution_resource_scope_sha256": ready_manifest[
            "execution_resource_scope_sha256"
        ],
        "outer_observer_contract_sha256": ready_manifest[
            "outer_observer_contract_sha256"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "parent_bindings": binding["parent_bindings"],
        "review_scope": "authorize_h4_p0r_factor_only_after_clean_p1_checkout",
        "review_disposition": "approved_h4_p0r_factor_only",
        "review_token_id": identifier,
        "reviewed_utc": "2026-08-15T00:00:00Z",
        "expires_utc": "2099-08-15T00:00:00Z",
        "p0r_preregistration_commit": "1" * 40,
        "independent_audits": deepcopy(p1.AUDITORS),
        "expected_terminal_seal_relative_path": (
            "validation-output/av-bs1/outer-observer-terminal-seals/"
            f"{identifier}.json"
        ),
    }

    class VirtualTokenPath:
        def __init__(self, resolved: Path) -> None:
            self._resolved = resolved

        def resolve(self) -> Path:
            return self._resolved

        def is_file(self) -> bool:
            return True

    token_path = VirtualTokenPath(p1.TOKEN_PATH.resolve())
    monkeypatch.setattr(p1, "TOKEN_PATH", token_path)
    monkeypatch.setattr(p1, "_read", lambda *_args: deepcopy(authorized_token))
    monkeypatch.setattr(p1, "_validate_checkout", lambda *_args: "2" * 40)

    assert p1._validate_token(token_path, ready_manifest) == authorized_token
    with pytest.raises(
        p1.AvBsError,
        match="control-plane and outer-observer authorization prerequisites are incomplete",
    ) as caught:
        p1._validate_token(token_path, pending_manifest)

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


def _authorized_token_for_manifest(
    manifest_value: dict[str, object], identifier: str = "0" * 32
) -> dict[str, object]:
    binding = manifest_value["bindings"]
    return {
        "schema": p1.TOKEN_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorized_stage": p1.AUTHORIZED_STAGE,
        "authorization_state": "authorized",
        "uses_remaining": 1,
        "next_stage_authorized": False,
        "fixture_sha256": binding["fixture_sha256"],
        "runner_sha256": binding["runner_sha256"],
        "static_test_sha256": binding["static_test_sha256"],
        "preregistration_doc_sha256": binding["preregistration_doc_sha256"],
        "manifest_payload_sha256": binding["manifest_payload_sha256"],
        "matrix_contract_sha256": binding["matrix_contract_sha256"],
        "resource_policy_sha256": manifest_value["resource_policy_sha256"],
        "execution_resource_scope_sha256": manifest_value[
            "execution_resource_scope_sha256"
        ],
        "outer_observer_contract_sha256": manifest_value[
            "outer_observer_contract_sha256"
        ],
        "terminal_seal_required_for_authoritative_disposition": True,
        "parent_bindings": binding["parent_bindings"],
        "review_scope": "authorize_h4_p0r_factor_only_after_clean_p1_checkout",
        "review_disposition": "approved_h4_p0r_factor_only",
        "review_token_id": identifier,
        "reviewed_utc": "2026-08-15T00:00:00Z",
        "expires_utc": "2099-08-15T00:00:00Z",
        "p0r_preregistration_commit": "1" * 40,
        "independent_audits": deepcopy(p1.AUDITORS),
        "expected_terminal_seal_relative_path": (
            "validation-output/av-bs1/outer-observer-terminal-seals/"
            f"{identifier}.json"
        ),
    }


@pytest.mark.parametrize(
    "partial_readiness",
    [
        "outer_status_without_blocker_clear",
        "outer_blocker_clear_without_status",
        "control_status_without_blocker_clear",
        "control_blocker_clear_without_status",
        "thresholds_false",
        "system_floor_rechecks_false",
        "outer_envelope_wall_stop_drift",
        "outer_envelope_thresholds_false",
        "outer_envelope_floor_rechecks_false",
        "outer_envelope_root_drift",
    ],
)
def test_partial_authorization_readiness_keeps_token_and_stage_blocked(
    monkeypatch: pytest.MonkeyPatch, partial_readiness: str
) -> None:
    base = deepcopy(manifest()["payload"])
    execution_scope = deepcopy(base["execution_resource_scope"])
    outer_observer = deepcopy(base["outer_observer_contract"])
    control = execution_scope["control_plane"]
    control.update(
        {
            "independently_bounded": True,
            "tree_thresholds_equal_factor_envelope": True,
            "system_floor_recheck_before_and_after_each": True,
            "implementation_status": "implemented_and_static_audited",
            "authorization_blocker": False,
        }
    )
    outer_observer.update(
        {
            "implementation_status": "implemented_and_static_audited",
            "authorization_blocker": False,
        }
    )
    if partial_readiness == "outer_status_without_blocker_clear":
        outer_observer["authorization_blocker"] = True
    elif partial_readiness == "outer_blocker_clear_without_status":
        outer_observer["implementation_status"] = "runner_and_python_binding_pending"
    elif partial_readiness == "control_status_without_blocker_clear":
        control["authorization_blocker"] = True
    elif partial_readiness == "control_blocker_clear_without_status":
        control["implementation_status"] = "bounded_control_plane_supervisor_pending"
    elif partial_readiness == "thresholds_false":
        control["tree_thresholds_equal_factor_envelope"] = False
    elif partial_readiness == "system_floor_rechecks_false":
        control["system_floor_recheck_before_and_after_each"] = False
    elif partial_readiness == "outer_envelope_wall_stop_drift":
        outer_observer["outer_resource_envelope"]["wall_stop_seconds"] = 899
    elif partial_readiness == "outer_envelope_thresholds_false":
        outer_observer["outer_resource_envelope"][
            "tree_thresholds_equal_factor_envelope"
        ] = False
    elif partial_readiness == "outer_envelope_floor_rechecks_false":
        outer_observer["outer_resource_envelope"][
            "system_floor_recheck_before_and_after"
        ] = False
    else:
        outer_observer["outer_resource_envelope"]["execution_tree_root"] = (
            "inner_runner_pid_and_birth"
        )

    assert p1._authorization_prerequisites_ready(execution_scope, outer_observer) is False

    parent = {
        **deepcopy(base["p0r_parent"]),
        "factorization_contract": deepcopy(base["factorization_contract"]),
        "future_schemas": deepcopy(base["future_schemas"]),
        "one_use_lifecycle": deepcopy(base["one_use_lifecycle"]),
    }
    monkeypatch.setattr(p1, "_parent", lambda: parent)
    monkeypatch.setattr(p1, "_policy", lambda _parent: deepcopy(base["resource_policy"]))
    monkeypatch.setattr(p1, "_p1_bindings", lambda _parent: deepcopy(base["bindings"]))
    monkeypatch.setattr(
        p1, "_execution_resource_scope", lambda: deepcopy(execution_scope)
    )
    monkeypatch.setattr(
        p1, "_outer_observer_contract", lambda: deepcopy(outer_observer)
    )

    class VirtualTokenPath:
        def __init__(self, resolved: Path) -> None:
            self._resolved = resolved

        def resolve(self) -> Path:
            return self._resolved

        def is_file(self) -> bool:
            return True

    token_path = VirtualTokenPath(p1.TOKEN_PATH.resolve())
    token_holder = {
        "value": {
            "schema": p1.TOKEN_SCHEMA,
            "authorization_state": "authorized",
            "uses_remaining": 1,
        }
    }
    monkeypatch.setattr(p1, "TOKEN_PATH", token_path)
    monkeypatch.setattr(
        p1, "_read", lambda *_args: deepcopy(token_holder["value"])
    )
    monkeypatch.setattr(
        p1,
        "_read_stable_json",
        lambda *_args: (deepcopy(token_holder["value"]), SHA_A),
    )
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_A)
    stage_manifest = p1.run_manifest()

    assert stage_manifest["token_state"] == "authorized_shape_pending_validation"
    assert stage_manifest["token_gated_stages"] == []
    assert "primary-h4-p0r" in stage_manifest["unavailable_stages"]
    assert stage_manifest["next_stage_authorized"] is False

    token_holder["value"] = _authorized_token_for_manifest(stage_manifest)
    monkeypatch.setattr(p1, "_validate_checkout", lambda *_args: "2" * 40)
    with pytest.raises(
        p1.AvBsError,
        match="control-plane and outer-observer authorization prerequisites are incomplete",
    ) as caught:
        p1._validate_token(token_path, stage_manifest)

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


@pytest.mark.parametrize(
    ("schema", "expected_state", "expected_status"),
    [
        (
            p1.TOMBSTONE_SCHEMA,
            "consumed_v2_invalid_tombstone_no_authority",
            "consumed_v2_invalid_tombstone_no_authority",
        ),
        (
            p1.EMERGENCY_TOMBSTONE_SCHEMA,
            "emergency_consumed_v2_invalid_tombstone_no_authority",
            "emergency_consumed_v2_invalid_tombstone_no_authority",
        ),
    ],
)
def test_consumed_tombstone_shape_is_not_advertised_as_validated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schema: str,
    expected_state: str,
    expected_status: str,
) -> None:
    token_path = tmp_path / "review-token.json"
    _write_json(
        token_path,
        {
            "schema": schema,
            "authorization_state": "consumed",
            "uses_remaining": 0,
            "extra_unvalidated_evidence": True,
        },
    )
    monkeypatch.setattr(p1, "TOKEN_PATH", token_path)

    payload = p1.run_manifest()

    assert payload["token_state"] == expected_state
    assert payload["status"] == expected_status
    assert payload["token_gated_stages"] == []
    assert "primary-h4-p0r" in payload["unavailable_stages"]


def test_valid_normal_v2_tombstone_without_seal_is_provisional_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_terminal_tombstone_bundle(monkeypatch, tmp_path)

    state, status, terminal = p1._classify_manifest_terminal_evidence(
        bundle["manifest"], bundle["tombstone"], bundle["tombstone_sha256"]
    )

    assert state == "consumed_v2_provisional_missing_terminal_seal"
    assert status == "consumed_v2_provisional_missing_terminal_seal"
    assert terminal["classification"] == (
        "valid_v2_consumed_tombstone_provisional_missing_terminal_seal"
    )
    assert terminal["terminal_seal_present"] is False
    assert terminal["authoritative_terminal_evidence"] is False
    assert terminal["authoritative_stage_pass"] is False
    assert {
        key: bundle["tombstone"][key] for key in PENDING_TERMINAL_FIELDS
    } == {
        "outer_observer_contract_sha256": bundle["manifest"][
            "outer_observer_contract_sha256"
        ],
        "outer_observer_handshake_prefix_sha256": bundle["claim"][
            "outer_observer_handshake_prefix_sha256"
        ],
        "expected_terminal_seal_relative_path": bundle["claim"][
            "expected_terminal_seal_relative_path"
        ],
        **PENDING_TERMINAL_VALUES,
    }


@pytest.mark.parametrize("emergency_branch", ["inner", "outer"])
def test_emergency_v2_semantic_branches_require_postvalidated_journals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    emergency_branch: str,
) -> None:
    bundle = _install_terminal_tombstone_bundle(
        monkeypatch, tmp_path, emergency_branch=emergency_branch
    )
    context = p1._validate_consumed_tombstone(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )
    assert context["emergency"] is True
    assert context["outer_emergency"] is (emergency_branch == "outer")
    pending = p1._validate_emergency_replacement_state(
        context, require_postvalidated=False
    )
    assert pending["state"] == "replacement_unverified_missing_intent"
    assert pending["postvalidated"] is False

    journal = _write_postvalidated_emergency_journal(bundle, context)
    validated = p1._validate_emergency_replacement_state(
        context, require_postvalidated=True
    )
    assert validated["state"] == (
        "replacement_postvalidated_pending_external_observer"
    )
    assert validated["postvalidated"] is True
    assert validated["intent_path"] == journal["intent_path"]
    assert validated["postvalidation_path"] == journal["postvalidation_path"]
    assert validated["recovery_path"] == journal["recovery_path"]
    assert journal["backup_path"].read_bytes() == bundle["original_token_bytes"]

    state, status, terminal = p1._classify_manifest_terminal_evidence(
        bundle["manifest"], bundle["tombstone"], bundle["tombstone_sha256"]
    )
    assert state == (
        "emergency_replacement_postvalidated_terminal_cleanup_unresolved_no_authority"
    )
    assert status == state
    assert terminal["terminal_seal_present"] is False
    assert terminal["authoritative_terminal_evidence"] is False
    assert terminal["authoritative_stage_pass"] is False


def test_marker_bytes_are_retained_raw_but_deep_parsed_only_for_valid_resource(
    tmp_path: Path,
) -> None:
    attempt_path = tmp_path / "durable-attempt"
    attempt_path.mkdir()
    ready_path = attempt_path / "monitor-ready.json"
    ready_path.write_bytes(b"{malformed-marker")
    ready_sha = hashlib.sha256(ready_path.read_bytes()).hexdigest()
    complete = {
        "monitor_ready_marker_sha256": ready_sha,
        "factor_complete_marker_sha256": None,
        "monitor_release_marker_sha256": None,
    }
    artifacts = {
        "monitor_ready_marker": {
            "path": ready_path.resolve(),
            "sha256": ready_sha,
        }
    }
    failure_context = {"value": {"resource_evidence_valid": False}}

    retained = p1._validate_terminal_monitor_markers(
        complete, failure_context, attempt_path, artifacts, None
    )
    assert retained["monitor_ready_marker"]["sha256"] == ready_sha

    resource_valid_context = {
        "value": {"resource_evidence_valid": True},
        "claim": {"sha256": SHA_A},
    }
    with pytest.raises(p1.AvBsError, match="terminal monitor-ready.json unreadable"):
        p1._validate_terminal_monitor_markers(
            complete,
            resource_valid_context,
            attempt_path,
            artifacts,
            None,
        )

    source = FIXTURE.read_text(encoding="utf-8")
    complete_validator = source[
        source.index("def _validate_terminal_complete(") : source.index(
            "def _validate_terminal_exit_release("
        )
    ]
    assert "actual_hash != _require_sha(expected_hash" in complete_validator
    assert '"monitor-ready.json", "monitor_ready_marker_sha256"' in complete_validator


def test_filesystem_terminal_failure_chain_is_authoritative_but_never_advances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch, tmp_path, passed=False
    )
    tombstone_context = p1._validate_consumed_tombstone(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )

    complete = p1._validate_terminal_complete(
        tombstone_context, bundle["manifest"]
    )
    exit_release = p1._validate_terminal_exit_release(
        complete, tombstone_context, bundle["manifest"]
    )
    outer_close = p1._validate_outer_resource_envelope_close(
        exit_release, complete, tombstone_context, bundle["manifest"]
    )
    seal = p1._validate_outer_terminal_seal(
        outer_close,
        exit_release,
        complete,
        tombstone_context,
        bundle["manifest"],
    )
    terminal = p1._validate_outer_terminal_evidence(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )
    state, status, classification = p1._classify_manifest_terminal_evidence(
        bundle["manifest"], bundle["tombstone"], bundle["tombstone_sha256"]
    )

    assert complete["published_result"]["payload"]["schema"] == p1.FAILURE_SCHEMA
    assert complete["published_result"]["payload"]["factor_certificates"] == []
    assert bundle["tombstone"]["resource_evidence"] is None
    assert bundle["tombstone"]["child_stdout_evidence"] is None
    assert bundle["tombstone"]["guard_canonical_sha256"] is None
    assert complete["value"]["guard_canonical_sha256"] == SHA_E
    assert seal["authoritative_stage_pass"] is False
    assert seal["value"]["next_stage_authorized"] is False
    assert terminal["seal"]["authoritative_inner_disposition"] == "failure"
    assert terminal["seal"]["authoritative_stage_pass"] is False
    assert state == "consumed_v2_authoritative_sealed_failure"
    assert status == state
    assert classification["authoritative_terminal_evidence"] is True
    assert classification["authoritative_stage_pass"] is False


def test_outer_close_v2_accepts_only_typed_complete_retry_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch, tmp_path, passed=False
    )
    tombstone_context = p1._validate_consumed_tombstone(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )
    complete = p1._validate_terminal_complete(
        tombstone_context, bundle["manifest"]
    )
    exit_release = p1._validate_terminal_exit_release(
        complete, tombstone_context, bundle["manifest"]
    )
    retry_event = {
        "attempt": 1,
        "confirmation": "limited_query_not_found_and_complete_snapshot_absent",
        "context": "outer_tree_sample",
        "expected_birth_utc_ticks": None,
        "message_code": "CONFIRMED_NONROOT_DISAPPEARANCE",
        "observed_birth_utc_ticks": None,
        "operation": "open_process",
        "process_id": 999,
        "process_role": "descendant",
        "win32_error_code": 87,
    }
    valid = deepcopy(bundle["outer_close"])
    valid["tree_sample_confirmed_disappearance_count"] = 1
    valid["tree_sample_retry_events"] = [retry_event]
    _write_json(bundle["outer_close_path"], valid)

    context = p1._validate_outer_resource_envelope_close(
        exit_release, complete, tombstone_context, bundle["manifest"]
    )
    assert context["value"]["tree_sample_retry_events"] == [retry_event]

    for mutate, expected in (
        (
            lambda value: value["tree_sample_retry_events"][0].__setitem__(
                "process_id", True
            ),
            "outer tree sample process id",
        ),
        (
            lambda value: value.__setitem__(
                "tree_sample_confirmed_disappearance_count", 2
            ),
            "outer tree retry count mismatch",
        ),
        (
            lambda value: value["tree_sample_retry_events"][0].__setitem__(
                "unreviewed", False
            ),
            "outer tree sample diagnostic field set mismatch",
        ),
    ):
        tampered = deepcopy(valid)
        mutate(tampered)
        _write_json(bundle["outer_close_path"], tampered)
        with pytest.raises(p1.AvBsError, match=expected):
            p1._validate_outer_resource_envelope_close(
                exit_release, complete, tombstone_context, bundle["manifest"]
            )


def test_outer_close_v2_reconciles_retry_births_by_process_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch, tmp_path, passed=False
    )
    tombstone_context = p1._validate_consumed_tombstone(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )
    complete = p1._validate_terminal_complete(
        tombstone_context, bundle["manifest"]
    )
    exit_release = p1._validate_terminal_exit_release(
        complete, tombstone_context, bundle["manifest"]
    )
    exited_event = {
        "attempt": 1,
        "confirmation": "signaled_handle_and_complete_snapshot_absent",
        "context": "outer_tree_sample",
        "expected_birth_utc_ticks": 3000,
        "message_code": "CONFIRMED_NONROOT_DISAPPEARANCE",
        "observed_birth_utc_ticks": 3000,
        "operation": "get_process_times",
        "process_id": 999,
        "process_role": "descendant",
        "win32_error_code": None,
    }
    valid = deepcopy(bundle["outer_close"])
    valid["tree_sample_confirmed_disappearance_count"] = 1
    valid["tree_sample_retry_events"] = [exited_event]
    valid["sampled_process_identities"].append(
        {"process_id": 999, "birth_utc_ticks": 3000}
    )
    valid["sampled_process_identities"].sort(
        key=lambda row: (row["process_id"], row["birth_utc_ticks"])
    )
    _write_json(bundle["outer_close_path"], valid)
    p1._validate_outer_resource_envelope_close(
        exit_release, complete, tombstone_context, bundle["manifest"]
    )

    conflicting = deepcopy(valid)
    next(
        row
        for row in conflicting["sampled_process_identities"]
        if row["process_id"] == 999
    )["birth_utc_ticks"] = 4000
    _write_json(bundle["outer_close_path"], conflicting)
    with pytest.raises(p1.AvBsError, match="retry identity conflicts"):
        p1._validate_outer_resource_envelope_close(
            exit_release, complete, tombstone_context, bundle["manifest"]
        )

    duplicate_pid = deepcopy(valid)
    duplicate_pid["sampled_process_identities"].append(
        {"process_id": 999, "birth_utc_ticks": 4000}
    )
    duplicate_pid["sampled_process_identities"].sort(
        key=lambda row: (row["process_id"], row["birth_utc_ticks"])
    )
    _write_json(bundle["outer_close_path"], duplicate_pid)
    with pytest.raises(p1.AvBsError, match="not unique by process id"):
        p1._validate_outer_resource_envelope_close(
            exit_release, complete, tombstone_context, bundle["manifest"]
        )


def test_outer_monitor_failure_allowlist_covers_every_runner_phase() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    phases = set(re.findall(r'\$currentOuterOperation = "([^"]+)"', source))
    assert phases == {
        "outer_initial_identity",
        "outer_initial_system_sample",
        "outer_initial_tree_sample",
        "outer_pre_spawn_system_sample",
        "outer_pre_spawn_tree_sample",
        "outer_inner_spawn",
        "outer_tree_sample",
        "inner_tree_sample",
        "outer_system_sample",
        "outer_ready_handshake",
        "outer_complete_handshake",
        "outer_retained_inner_exit",
        "outer_cleanup",
        "outer_final_tree_sample",
        "outer_final_system_sample",
        "outer_post_cleanup_classification",
    }
    for phase in phases:
        diagnostic = {
            "attempt": 1,
            "confirmation": None,
            "context": phase,
            "expected_birth_utc_ticks": None,
            "message_code": "OUTER_MONITOR_FAILURE",
            "observed_birth_utc_ticks": None,
            "operation": phase,
            "process_id": None,
            "process_role": None,
            "win32_error_code": None,
        }
        assert (
            p1._validate_outer_tree_sample_diagnostic(
                diagnostic, retry_event=False
            )
            == diagnostic
        )


def test_filesystem_terminal_pass_chain_is_authoritative_but_never_advances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch, tmp_path, passed=True
    )
    tombstone_context = p1._validate_consumed_tombstone(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )

    complete = p1._validate_terminal_complete(
        tombstone_context, bundle["manifest"]
    )
    exit_release = p1._validate_terminal_exit_release(
        complete, tombstone_context, bundle["manifest"]
    )
    outer_close = p1._validate_outer_resource_envelope_close(
        exit_release, complete, tombstone_context, bundle["manifest"]
    )
    seal = p1._validate_outer_terminal_seal(
        outer_close,
        exit_release,
        complete,
        tombstone_context,
        bundle["manifest"],
    )
    terminal = p1._validate_outer_terminal_evidence(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )
    state, status, classification = p1._classify_manifest_terminal_evidence(
        bundle["manifest"], bundle["tombstone"], bundle["tombstone_sha256"]
    )

    assert len(bundle["tombstone"]["factor_certificates"]) == 2
    assert complete["published_result"]["payload"]["schema"] == p1.RESULT_SCHEMA
    assert complete["published_result"]["payload"]["authoritative_stage_pass"] is False
    assert seal["authoritative_stage_pass"] is True
    assert seal["value"]["next_stage_authorized"] is False
    assert terminal["seal"]["authoritative_inner_disposition"] == "pass"
    assert terminal["seal"]["authoritative_stage_pass"] is True
    assert state == "consumed_v2_authoritative_sealed_pass"
    assert status == "consumed_v2_authoritative_sealed_pass_no_next_stage_authorization"
    assert classification["authoritative_terminal_evidence"] is True
    assert classification["authoritative_stage_pass"] is True


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("extra_seal_field", "outer terminal seal field set mismatch"),
        ("bool_as_int", "outer terminal seal next_stage_authorized mismatch"),
        ("stale_bound_source", "terminal result byte hash mismatch"),
        ("missing_bound_source", "terminal result artifact is missing"),
        ("null_consumer_reference", "terminal consumer report null-pair mismatch"),
        (
            "seal_close_hash_drift",
            "outer terminal seal outer_resource_envelope_close_raw_sha256 mismatch",
        ),
        ("close_byte_drift", "outer resource envelope close bytes are not canonical JSON"),
    ],
)
def test_filesystem_terminal_bundle_tamper_matrix_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tamper: str,
    expected_message: str,
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch, tmp_path, passed=False
    )

    if tamper == "extra_seal_field":
        seal = deepcopy(bundle["seal"])
        seal["unreviewed"] = False
        _write_json(bundle["seal_path"], seal)
    elif tamper == "bool_as_int":
        seal = deepcopy(bundle["seal"])
        seal["next_stage_authorized"] = 0
        _write_json(bundle["seal_path"], seal)
    elif tamper == "stale_bound_source":
        bundle["result_path"].write_bytes(b"stale result bytes")
    elif tamper == "missing_bound_source":
        bundle["result_path"].unlink()
    elif tamper == "null_consumer_reference":
        pre_exit = deepcopy(bundle["pre_exit"])
        pre_exit["consumer_report_relative_path"] = None
        _write_json(bundle["pre_exit_path"], pre_exit)
        complete = deepcopy(bundle["complete"])
        complete["pre_exit_evidence_sha256"] = p1._sha(bundle["pre_exit_path"])
        _write_json(bundle["complete_path"], complete)
        tombstone_context = p1._validate_consumed_tombstone(
            bundle["tombstone"],
            bundle["tombstone_sha256"],
            bundle["manifest"],
        )
        with pytest.raises(p1.AvBsError, match=expected_message):
            p1._validate_terminal_complete(tombstone_context, bundle["manifest"])
        return
    elif tamper == "seal_close_hash_drift":
        seal = deepcopy(bundle["seal"])
        seal["outer_resource_envelope_close_raw_sha256"] = SHA_A
        _write_json(bundle["seal_path"], seal)
    else:
        with bundle["outer_close_path"].open("ab") as stream:
            stream.write(b"\n")

    with pytest.raises(p1.AvBsError, match=expected_message):
        p1._validate_outer_terminal_evidence(
            bundle["tombstone"],
            bundle["tombstone_sha256"],
            bundle["manifest"],
        )


def test_raw_bound_malformed_marker_can_seal_only_as_untrusted_failure_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch,
        tmp_path,
        passed=False,
        malformed_ready_marker=True,
    )

    terminal = p1._validate_outer_terminal_evidence(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )

    assert bundle["tombstone"]["resource_evidence_valid"] is False
    assert terminal["complete"]["monitor_markers"]["monitor_ready_marker"][
        "sha256"
    ] == bundle["complete"]["monitor_ready_marker_sha256"]
    assert terminal["seal"]["authoritative_stage_pass"] is False


def test_raw_bound_marker_byte_mutation_is_rejected_even_when_resource_is_untrusted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch,
        tmp_path,
        passed=False,
        malformed_ready_marker=True,
    )
    bundle["marker_paths"]["monitor_ready_marker_sha256"].write_bytes(
        b"mutated after binding"
    )
    tombstone_context = p1._validate_consumed_tombstone(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )

    with pytest.raises(p1.AvBsError, match="terminal monitor_ready_marker byte hash mismatch"):
        p1._validate_terminal_complete(tombstone_context, bundle["manifest"])


def test_terminal_marker_hashes_allow_only_contiguous_nullable_prefixes(
    tmp_path: Path,
) -> None:
    context = {"value": {"resource_evidence_valid": False}}
    keys = (
        "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256",
        "monitor_release_marker_sha256",
    )
    for presence in (
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, True, True),
    ):
        complete = {
            key: SHA_A if present else None
            for key, present in zip(keys, presence)
        }
        assert p1._validate_terminal_monitor_markers(
            complete, context, tmp_path, {}, None
        ) == {}

    for presence in (
        (False, True, False),
        (False, False, True),
        (True, False, True),
        (False, True, True),
    ):
        complete = {
            key: SHA_A if present else None
            for key, present in zip(keys, presence)
        }
        with pytest.raises(p1.AvBsError, match="terminal factor marker prefix invalid"):
            p1._validate_terminal_monitor_markers(
                complete, context, tmp_path, {}, None
            )


def test_untrusted_published_result_is_raw_bound_without_payload_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _install_synthetic_outer_terminal_bundle(
        monkeypatch,
        tmp_path,
        passed=False,
        untrusted_published_result=True,
    )

    terminal = p1._validate_outer_terminal_evidence(
        bundle["tombstone"], bundle["tombstone_sha256"], bundle["manifest"]
    )

    assert terminal["complete"]["published_result"]["payload"] is None
    assert terminal["seal"]["authoritative_stage_pass"] is False


def test_resource_policy_and_four_code_allowlist_are_frozen() -> None:
    payload = manifest()["payload"]
    policy = payload["resource_policy"]
    assert policy["wall_stop_seconds"] == 900
    assert (
        policy["tree_working_set_stop_bytes"],
        policy["tree_private_stop_bytes"],
        policy["tree_commit_stop_bytes"],
    ) == (4 * 1024**3, 5 * 1024**3, 5 * 1024**3)
    assert policy["factor_fit_unproven"] is True
    assert payload["failure_code_allowlist"] == [
        "BLOCKED_AV_BS_FACTOR",
        "BLOCKED_AV_BS_MESH_HASH",
        "BLOCKED_AV_BS_RESOURCE",
        "BLOCKED_AV_BS_RESULT_SCHEMA",
    ]


def test_runner_lifetime_peak_stops_are_valid_resource_reasons() -> None:
    runner_source = RUNNER.read_text(encoding="utf-8")
    for reason in (
        "TREE_LIFETIME_PEAK_WS_STOP",
        "TREE_LIFETIME_PEAK_COMMIT_STOP",
    ):
        assert reason in runner_source
        assert reason in p1.RESOURCE_STOP_REASONS


def test_execution_artifact_schemas_reuse_the_frozen_p0r_family() -> None:
    assert p1.TOKEN_SCHEMA == "AV-BS1-h4-p0r-review-token-v1"
    assert p1.CLAIM_SCHEMA == "AV-BS1-h4-p0r-token-claim-v1"
    assert p1.GUARD_SCHEMA == "AV-BS1-h4-p0r-resource-guard-v1"
    assert p1.RESOURCE_SCHEMA == "AV-BS1-h4-p0r-resource-report-v1"
    assert p1.NUMERICAL_SCHEMA == "AV-BS1-h4-p0r-factor-report-v1"
    assert p1.RESULT_SCHEMA == "AV-BS1-h4-p0r-result-v2"
    assert p1.FAILURE_SCHEMA == "AV-BS1-h4-p0r-failure-v1"
    assert p1.TOMBSTONE_SCHEMA == "AV-BS1-h4-p0r-consumed-review-token-v2"
    assert (
        p1.EMERGENCY_TOMBSTONE_SCHEMA
        == "AV-BS1-h4-p0r-emergency-consumed-review-token-v2"
    )


def test_token_absence_blocks_all_primary_preflight() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(FIXTURE),
            "--stage",
            "preflight-primary-h4-p0r",
            "--review-token",
            str(TOOLS / "absent.json"),
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert completed.returncode == 2
    value = json.loads(completed.stdout)["payload"]
    assert value["status"] == "BLOCKED_AV_BS_RESULT_SCHEMA"
    assert value["factorization_performed"] is False


def test_control_plane_blocker_rejects_even_a_present_token_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class VirtualTokenPath:
        def __init__(self, resolved: Path) -> None:
            self._resolved = resolved

        def resolve(self) -> Path:
            return self._resolved

        def is_file(self) -> bool:
            return True

    token_path = VirtualTokenPath(p1.TOKEN_PATH.resolve())
    monkeypatch.setattr(p1, "TOKEN_PATH", token_path)
    blocked_manifest = {
        "execution_resource_scope": {
            "control_plane": {
                "independently_bounded": False,
                "authorization_blocker": True,
            }
        }
    }

    with pytest.raises(
        p1.AvBsError,
        match="control-plane and outer-observer authorization prerequisites are incomplete",
    ):
        p1._validate_token(token_path, blocked_manifest)


def test_factor_only_source_has_no_solve_or_physics_calls() -> None:
    source = FIXTURE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "solve" not in attributes
    assert "onenormest" not in source
    assert "splu" in source
    assert '"rhs_count": 0' in source
    assert '"extensions_generated": False' in source
    certificate_source = source[
        source.index("def _certificate("):source.index("def _factor_one(")
    ]
    assert ".tocoo(" not in certificate_source
    assert "h1._sparse_sha256(l)" not in certificate_source
    assert "h1._sparse_sha256(u)" not in certificate_source


def test_streaming_sparse_hashes_match_frozen_reference_without_whole_bytes_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = csc_matrix(
        np.array(
            [[1.0 + 2.0j, 0.0, 4.0], [3.0, 5.0 - 1.0j, 0.0], [0.0, 6.0, 7.0]],
            dtype=np.complex128,
        )
    )
    monkeypatch.setattr(p1, "_HASH_CHUNK_ITEMS", 2)
    assert p1._streaming_sparse_sha256(matrix) == p1.h1._sparse_sha256(matrix)

    digest = hashlib.sha256(
        p1.h1.canonical_bytes(
            {
                "shape": [3, 3],
                "data_dtype": np.dtype(matrix.data.dtype).str,
                "indices_dtype": np.dtype(matrix.indices.dtype).str,
                "indptr_dtype": np.dtype(matrix.indptr.dtype).str,
            }
        )
    )
    digest.update(matrix.data.tobytes(order="C"))
    digest.update(matrix.indices.tobytes(order="C"))
    digest.update(matrix.indptr.tobytes(order="C"))
    assert p1._csc_storage_sha256(matrix) == digest.hexdigest()


def test_this_suite_never_calls_primary() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "primary"
        for node in ast.walk(tree)
    )


def test_fake_splu_certificate_has_exact_options_bytes_and_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_splu(monkeypatch, _FakeFactor())
    _clock(monkeypatch, [30, 40, 50, 60])
    matrix = csc_matrix(np.eye(2, dtype=np.complex128))

    certificate = p1._certificate(
        "A_background_II", matrix, _metadata(), 1_000_000
    )

    assert len(calls) == 1
    assert calls[0][0] == (matrix,)
    assert calls[0][1] == {
        "permc_spec": "COLAMD",
        "diag_pivot_thresh": 1.0,
        "options": {"Equil": False},
    }
    assert certificate["rhs_count"] == 0
    assert certificate["factor_solve_called"] is False
    assert certificate["stored_fill_ratio"] == 3.0
    assert certificate["fill_ratio"] == 3.0
    assert certificate["exported_factor_bytes"] == sum(
        certificate["factor_array_bytes"].values()
    )
    assert certificate["portable_factor_bytes"] == 224
    assert certificate["factor_wall_ns"] == 10
    assert certificate["factor_wall_seconds"] == 1.0e-8
    assert certificate["factor_objects_cleanup_completed_perf_counter_ns"] == 60
    assert p1._EXECUTION_PHASE["completed_factors"] == ["A_background_II"]
    assert p1._EXECUTION_PHASE["active_factor"] is None


def test_fake_splu_certificate_accepts_frozen_runtime_csc_array_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factor = _FakeFactor(
        lower=csc_array(np.array([[1.0, 0.0], [2.0, 1.0]], dtype=np.complex128)),
        upper=csc_array(np.array([[3.0, 4.0], [0.0, 5.0]], dtype=np.complex128)),
    )
    _install_fake_splu(monkeypatch, factor)
    _clock(monkeypatch, [30, 40, 50, 60])

    certificate = p1._certificate(
        "A_background_II",
        csc_matrix(np.eye(2, dtype=np.complex128)),
        _metadata(),
        1_000_000,
    )

    assert certificate["L_nnz"] == 3
    assert certificate["U_nnz"] == 3


@pytest.mark.parametrize("tamper", ["upper_L", "zero_U_diagonal", "bad_permutation"])
def test_fake_splu_certificate_rejects_structural_tamper(
    monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    factor = _FakeFactor()
    if tamper == "upper_L":
        factor.L = csc_matrix(
            np.array([[1.0, 2.0], [0.0, 1.0]], dtype=np.complex128)
        )
    elif tamper == "zero_U_diagonal":
        factor.U = csc_matrix(
            np.array([[0.0, 2.0], [0.0, 1.0]], dtype=np.complex128)
        )
    else:
        factor.perm_r = np.array([0, 0], dtype=np.int32)
    _install_fake_splu(monkeypatch, factor)
    _clock(monkeypatch, [30, 40, 50, 60])

    with pytest.raises(p1.AvBsError) as caught:
        p1._certificate(
            "A_background_II",
            csc_matrix(np.eye(2, dtype=np.complex128)),
            _metadata(),
            1_000_000,
        )

    assert caught.value.code == "BLOCKED_AV_BS_FACTOR"
    assert p1._EXECUTION_PHASE["factorization_performed"] is True
    assert p1._EXECUTION_PHASE["completed_factors"] == ["A_background_II"]
    assert p1._EXECUTION_PHASE["active_factor"] is None


def test_fake_splu_cap_failure_is_resource_failure_after_factor_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_splu(monkeypatch, _FakeFactor())
    _clock(monkeypatch, [30, 40, 50, 60])

    with pytest.raises(p1.AvBsError) as caught:
        p1._certificate(
            "A_background_II",
            csc_matrix(np.eye(2, dtype=np.complex128)),
            _metadata(),
            1,
        )

    assert caught.value.code == "BLOCKED_AV_BS_RESOURCE"
    assert p1._EXECUTION_PHASE["factorization_attempted"] is True
    assert p1._EXECUTION_PHASE["factorization_performed"] is True
    assert p1._EXECUTION_PHASE["completed_factors"] == ["A_background_II"]


def test_native_factor_cap_fails_before_materializing_L_or_U(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedFactor:
        nnz = 100

        @property
        def L(self) -> object:
            raise AssertionError("L must not materialize after native cap failure")

        @property
        def U(self) -> object:
            raise AssertionError("U must not materialize after native cap failure")

    _install_fake_splu(monkeypatch, OversizedFactor())
    _clock(monkeypatch, [30, 40, 50, 60])

    with pytest.raises(p1.AvBsError) as caught:
        p1._certificate(
            "A_background_II",
            csc_matrix(np.eye(2, dtype=np.complex128)),
            _metadata(),
            1,
        )

    assert caught.value.code == "BLOCKED_AV_BS_RESOURCE"


def test_fake_splu_memory_failure_preserves_ambiguous_phase_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_splu(monkeypatch, MemoryError("synthetic only"))
    _clock(monkeypatch, [30, 50, 60])

    with pytest.raises(p1.AvBsError) as caught:
        p1._certificate(
            "A_background_II",
            csc_matrix(np.eye(2, dtype=np.complex128)),
            _metadata(),
            1_000_000,
        )

    assert caught.value.code == "BLOCKED_AV_BS_RESOURCE"
    assert p1._EXECUTION_PHASE["factorization_attempted"] is True
    assert p1._EXECUTION_PHASE["factorization_performed"] is None
    assert p1._EXECUTION_PHASE["completed_factors"] == []
    assert p1._EXECUTION_PHASE["active_factor"] is None


def test_build_scaled_matrix_rejects_contract_drift_before_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = deepcopy(manifest()["payload"]["p0r_parent"]["matrix_inputs"])
    inputs["frequency_hz"] = -1.0
    assembly_calls: list[str] = []
    monkeypatch.setattr(
        p1.h4,
        "seed_mesh_h4",
        lambda: assembly_calls.append("assembly must not start"),
    )

    with pytest.raises(p1.AvBsError) as caught:
        p1._build_scaled_matrix("A_background_II", inputs)

    assert caught.value.code == "BLOCKED_AV_BS_MESH_HASH"
    assert assembly_calls == []


def test_matrix_identity_tamper_is_rejected_without_factor_entry() -> None:
    matrix = p1._canonical_csc(np.eye(2, dtype=np.complex128))
    expected = {
        "dtype": np.dtype(matrix.dtype).name,
        "shape": [2, 2],
        "nnz": int(matrix.nnz),
        "sha256": p1.h1._sparse_sha256(matrix),
        "sparse_array_bytes": p1._sparse_bytes(matrix),
        "transpose_relative": p1.h1._sparse_transpose_relative(matrix),
    }
    expected["sha256"] = SHA_F

    with pytest.raises(p1.AvBsError) as caught:
        p1._check_matrix_identity("A_background_II", matrix, expected)

    assert caught.value.code == "BLOCKED_AV_BS_MESH_HASH"
    assert p1._EXECUTION_PHASE["factorization_attempted"] is False


def test_factor_report_accepts_exact_synthetic_certificate_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    report = _synthetic_factor_report(manifest_value, token)
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)
    monkeypatch.setattr(p1, "_git_head", lambda: "2" * 40)
    monkeypatch.setattr(p1, "_validate_monitor_handshake", lambda *_args: None)

    p1._validate_factor_report(
        report,
        token,
        manifest_value,
        claim,
        tmp_path / "claim",
        {"guard": "synthetic"},
        tmp_path / "guard",
        validate_prefix_sidecars=False,
    )


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    [
        ("factor_array_extra", "BLOCKED_AV_BS_FACTOR"),
        ("exported_bytes", "BLOCKED_AV_BS_FACTOR"),
        ("portable_bytes", "BLOCKED_AV_BS_FACTOR"),
        ("wall_seconds", "BLOCKED_AV_BS_FACTOR"),
        ("factor_overlap", "BLOCKED_AV_BS_FACTOR"),
        ("physics_truth", "BLOCKED_AV_BS_RESULT_SCHEMA"),
        ("bool_as_int", "BLOCKED_AV_BS_RESULT_SCHEMA"),
        ("int_as_bool", "BLOCKED_AV_BS_RESULT_SCHEMA"),
    ],
)
def test_factor_report_tamper_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tamper: str,
    expected_code: str,
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    report = _synthetic_factor_report(manifest_value, token)
    first, second = report["factor_certificates"]
    if tamper == "factor_array_extra":
        first["factor_array_bytes"]["unreviewed"] = 0
    elif tamper == "exported_bytes":
        first["exported_factor_bytes"] += 1
    elif tamper == "portable_bytes":
        first["portable_factor_bytes"] += 1
    elif tamper == "wall_seconds":
        first["factor_wall_seconds"] = 0.5
    elif tamper == "factor_overlap":
        first["matrix_cleanup_completed_perf_counter_ns"] = second[
            "factor_started_perf_counter_ns"
        ] + 1
    elif tamper == "bool_as_int":
        report["factorization_attempted"] = 1
    elif tamper == "int_as_bool":
        first["rhs_count"] = False
    else:
        report["physics_solve_performed"] = True
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)
    monkeypatch.setattr(p1, "_git_head", lambda: "2" * 40)
    monkeypatch.setattr(p1, "_validate_monitor_handshake", lambda *_args: None)

    with pytest.raises(p1.AvBsError) as caught:
        p1._validate_factor_report(
            report,
            token,
            manifest_value,
            claim,
            tmp_path / "claim",
            {"guard": "synthetic"},
            tmp_path / "guard",
        )

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (
            {
                "claim_validated": False,
                "factorization_attempted": True,
                "factorization_performed": True,
                "completed_factors": ["A_background_II"],
            },
            ("not_authorized", False, False, []),
        ),
        (
            {
                "claim_validated": True,
                "factorization_attempted": False,
                "factorization_performed": False,
                "completed_factors": [],
            },
            ("claimed_attempt_failed", False, False, []),
        ),
        (
            {
                "claim_validated": True,
                "factorization_attempted": True,
                "factorization_performed": None,
                "completed_factors": [],
            },
            ("claimed_attempt_failed", True, None, []),
        ),
        (
            {
                "claim_validated": True,
                "factorization_attempted": True,
                "factorization_performed": True,
                "completed_factors": ["A_background_II"],
            },
            ("claimed_attempt_failed", True, True, ["A_background_II"]),
        ),
    ],
)
def test_failure_payload_preserves_authorization_and_factor_phase_truth(
    phase: dict[str, object], expected: tuple[object, object, object, object]
) -> None:
    p1._EXECUTION_PHASE.update(phase)
    p1._EXECUTION_PHASE["active_factor"] = None
    p1._EXECUTION_PHASE["factor_certificates"] = []

    payload = p1._failure(
        p1.AvBsError("BLOCKED_AV_BS_FACTOR", "synthetic"), p1.AUTHORIZED_STAGE
    )

    observed = (
        payload["authorization_state"],
        payload["factorization_attempted"],
        payload["factorization_performed"],
        payload["completed_factors"],
    )
    assert observed == expected
    assert payload["physics_solve_performed"] is False
    assert payload["execution_resource_scope_sha256"] == p1._canonical_sha(
        p1._execution_resource_scope()
    )
    if phase["claim_validated"] is True:
        assert p1._validate_failure_payload(
            payload, expected_stage=p1.AUTHORIZED_STAGE
        ) == ["BLOCKED_AV_BS_FACTOR"]


@pytest.mark.parametrize(
    "stage",
    ["finalize-primary-h4-p0r", "consume-primary-h4-p0r-token"],
)
def test_control_stage_failure_does_not_fabricate_a_claim(stage: str) -> None:
    p1._EXECUTION_PHASE["claim_validated"] = False

    payload = p1._failure(
        p1.AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "pre-claim synthetic"),
        stage,
    )

    assert payload["authorization_state"] == "not_authorized"
    assert payload["factorization_attempted"] is False
    assert payload["factorization_performed"] is False
    assert p1._validate_failure_payload(
        payload,
        expected_stage=stage,
        expected_authorization_state="not_authorized",
    ) == ["BLOCKED_AV_BS_RESULT_SCHEMA"]


def test_control_stage_failure_preserves_a_validated_claim() -> None:
    p1._EXECUTION_PHASE["claim_validated"] = True

    payload = p1._failure(
        p1.AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "post-claim synthetic"),
        "finalize-primary-h4-p0r",
    )

    assert payload["authorization_state"] == "claimed_attempt_failed"
    assert payload["factorization_attempted"] is None
    assert payload["factorization_performed"] is None
    assert p1._validate_failure_payload(
        payload,
        expected_stage="finalize-primary-h4-p0r",
        expected_authorization_state="claimed_attempt_failed",
    ) == ["BLOCKED_AV_BS_RESULT_SCHEMA"]


def test_partial_factor_certificate_is_preserved_and_revalidated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = _synthetic_guard(manifest_value)
    record = manifest_value["p0r_parent"]["matrix_inputs"]["matrices"][
        "A_background_II"
    ]
    certificate = _synthetic_certificate("A_background_II", record, base=10)
    p1._EXECUTION_PHASE.update(
        {
            "claim_validated": True,
            "factorization_attempted": True,
            "factorization_performed": True,
            "active_factor": None,
            "completed_factors": ["A_background_II"],
            "factor_certificates": [certificate],
        }
    )
    failure = p1._failure(
        p1.AvBsError("BLOCKED_AV_BS_FACTOR", "synthetic second factor failure"),
        p1.AUTHORIZED_STAGE,
    )
    assert failure["factor_certificates"] == [certificate]

    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)
    monkeypatch.setattr(p1, "_git_head", lambda: "2" * 40)
    monkeypatch.setattr(
        p1,
        "_validate_factor_report",
        lambda numerical, *_args, expected_count=2, **_kwargs: (
            None
            if expected_count == 1
            and numerical["factor_certificates"] == [certificate]
            else (_ for _ in ()).throw(AssertionError("partial certificate mismatch"))
        ),
    )
    p1._validate_partial_factor_certificates(
        failure,
        token,
        manifest_value,
        claim,
        tmp_path / "claim.json",
        guard,
        tmp_path / "guard.json",
    )

    tampered = deepcopy(failure)
    tampered["factor_certificates"][0]["name"] = "A_conductor_II"
    with pytest.raises(p1.AvBsError, match="partial certificate order mismatch"):
        p1._validate_partial_factor_certificates(
            tampered,
            token,
            manifest_value,
            claim,
            tmp_path / "claim.json",
            guard,
            tmp_path / "guard.json",
        )


def test_resource_gate_is_independent_of_child_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = _synthetic_guard(manifest_value)
    resource = _synthetic_resource(manifest_value, token, claim)
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)

    passed, failures = p1._resource_gate(
        resource,
        token,
        manifest_value,
        guard,
        claim,
        guard_path=tmp_path / "guard",
        claim_path=tmp_path / "claim",
        terminal_bindings=_synthetic_terminal_bindings(claim),
    )

    assert resource["child_exit_code"] == 2
    assert passed is True
    assert failures == []


def test_resource_threshold_failure_is_recomputed_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    resource = _synthetic_resource(manifest_value, token, claim)
    resource["peak"]["tree_working_set_bytes"] += 1
    resource["mandatory_resource_gate_pass"] = False
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)

    passed, failures = p1._resource_gate(
        resource,
        token,
        manifest_value,
        _synthetic_guard(manifest_value),
        claim,
        guard_path=tmp_path / "guard",
        claim_path=tmp_path / "claim",
        terminal_bindings=_synthetic_terminal_bindings(claim),
    )

    assert passed is False
    assert failures == ["tree_ws"]


def test_resource_lifetime_peak_failure_is_recomputed_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    resource = _synthetic_resource(manifest_value, token, claim)
    resource["peak"]["tree_summed_process_lifetime_peak_commit_bytes"] += 1
    resource["mandatory_resource_gate_pass"] = False
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)

    passed, failures = p1._resource_gate(
        resource,
        token,
        manifest_value,
        _synthetic_guard(manifest_value),
        claim,
        guard_path=tmp_path / "guard",
        claim_path=tmp_path / "claim",
        terminal_bindings=_synthetic_terminal_bindings(claim),
    )

    assert passed is False
    assert failures == ["tree_lifetime_peak_commit"]


def test_resource_pid_identity_tamper_is_schema_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    resource = _synthetic_resource(manifest_value, token, claim)
    resource["observed_process_identities"][0]["birth_utc_ticks"] = 0
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)

    with pytest.raises(p1.AvBsError) as caught:
        p1._resource_gate(
            resource,
            token,
            manifest_value,
            _synthetic_guard(manifest_value),
            claim,
            guard_path=tmp_path / "guard",
            claim_path=tmp_path / "claim",
            terminal_bindings=_synthetic_terminal_bindings(claim),
        )

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


def test_resource_boolean_cannot_be_replaced_by_integer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    resource = _synthetic_resource(manifest_value, token, claim)
    resource["execution_tree_includes_runner"] = 1
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)

    with pytest.raises(p1.AvBsError) as caught:
        p1._resource_gate(
            resource,
            token,
            manifest_value,
            _synthetic_guard(manifest_value),
            claim,
            guard_path=tmp_path / "guard",
            claim_path=tmp_path / "claim",
            terminal_bindings=_synthetic_terminal_bindings(claim),
        )

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


@pytest.mark.parametrize("field", ["successful_tree_sample_count", "child_visible_tree_sample_count"])
def test_resource_negative_sample_count_is_schema_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    resource = _synthetic_resource(manifest_value, token, claim)
    resource[field] = -1
    resource["mandatory_resource_gate_pass"] = False
    monkeypatch.setattr(p1, "_sha", lambda _path: SHA_F)
    monkeypatch.setattr(p1, "_canonical_sha", lambda _value: SHA_E)

    with pytest.raises(p1.AvBsError) as caught:
        p1._resource_gate(
            resource,
            token,
            manifest_value,
            _synthetic_guard(manifest_value),
            claim,
            guard_path=tmp_path / "guard",
            claim_path=tmp_path / "claim",
            terminal_bindings=_synthetic_terminal_bindings(claim),
        )

    assert caught.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


def _patch_finalizer_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    manifest_value: dict[str, object],
    token: dict[str, object],
    claim: dict[str, object],
    guard: dict[str, object],
    resource_gate: tuple[bool, list[str]],
) -> None:
    monkeypatch.setattr(p1, "run_manifest", lambda: manifest_value)
    monkeypatch.setattr(p1, "_validate_token", lambda *_args, **_kwargs: token)
    monkeypatch.setattr(p1, "_validate_claim", lambda *_args, **_kwargs: claim)
    monkeypatch.setattr(p1, "_validate_guard", lambda *_args, **_kwargs: guard)
    monkeypatch.setattr(
        p1, "_resource_gate", lambda *_args, **_kwargs: resource_gate
    )
    monkeypatch.setattr(p1, "_git_head", lambda: "2" * 40)


def test_finalizer_embeds_resource_failure_and_unknown_factor_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = {"guard": "synthetic"}
    _patch_finalizer_dependencies(
        monkeypatch, manifest_value, token, claim, guard, (False, ["tree_ws"])
    )
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    resource = {"child_exit_code": 2, "child_stdout_sha256": None}
    _write_json(resource_path, resource)

    result = p1.finalize(
        resource_path, token_path, guard_path, "nonce", claim_path, None
    )

    assert result["status"] == "BLOCKED_AV_BS_RESOURCE"
    assert result["resource"] == resource
    assert result["resource_gate_recheck_failures"] == ["tree_ws"]
    assert result["factorization_attempted"] is None
    assert result["factorization_performed"] is None
    assert result["mandatory_stage_pass"] is False
    assert result["physics_solve_performed"] is False


def test_finalizer_preserves_partial_factor_failure_truth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = {"guard": "synthetic"}
    _patch_finalizer_dependencies(
        monkeypatch, manifest_value, token, claim, guard, (True, [])
    )
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    stdout_path = tmp_path / "stdout.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    p1._EXECUTION_PHASE.update(
        {
            "claim_validated": True,
            "factorization_attempted": True,
            "factorization_performed": True,
            "active_factor": None,
            "completed_factors": ["A_background_II"],
        }
    )
    failure = p1._failure(
        p1.AvBsError("BLOCKED_AV_BS_FACTOR", "synthetic second-factor failure"),
        p1.AUTHORIZED_STAGE,
    )
    failure["execution_resource_scope_sha256"] = manifest_value[
        "execution_resource_scope_sha256"
    ]
    _write_json(stdout_path, p1._wrap(failure))
    resource = {
        "child_exit_code": 2,
        "child_stdout_sha256": p1._sha(stdout_path),
    }
    _write_json(resource_path, resource)

    result = p1.finalize(
        resource_path, token_path, guard_path, "nonce", claim_path, stdout_path
    )

    assert result["status"] == "BLOCKED_AV_BS_FACTOR"
    assert result["factorization_attempted"] is True
    assert result["factorization_performed"] is True
    assert result["completed_factors"] == ["A_background_II"]
    assert result["factor_sequence_nonoverlap"] is None
    assert result["mandatory_stage_pass"] is False


def test_finalizer_rejects_forged_child_wrapper_checksum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    _patch_finalizer_dependencies(
        monkeypatch, manifest_value, token, claim, {}, (True, [])
    )
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    stdout_path = tmp_path / "stdout.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    failure = p1._failure(
        p1.AvBsError("BLOCKED_AV_BS_FACTOR", "synthetic"), p1.AUTHORIZED_STAGE
    )
    _write_json(stdout_path, {"payload": failure, "payload_sha256": SHA_A})
    _write_json(
        resource_path,
        {
            "child_exit_code": 2,
            "child_stdout_sha256": p1._sha(stdout_path),
        },
    )

    with pytest.raises(p1.AvBsError, match="checksum"):
        p1.finalize(
            resource_path,
            token_path,
            guard_path,
            "nonce",
            claim_path,
            stdout_path,
        )


def _patch_consumer_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    manifest_value: dict[str, object],
    token: dict[str, object],
    claim: dict[str, object],
    guard: dict[str, object],
    resource_gate: tuple[bool, list[str]],
) -> None:
    monkeypatch.setattr(p1, "run_manifest", lambda: manifest_value)
    monkeypatch.setattr(p1, "_validate_token", lambda *_args, **_kwargs: token)
    monkeypatch.setattr(p1, "_validate_claim", lambda *_args, **_kwargs: claim)
    monkeypatch.setattr(p1, "_validate_guard", lambda *_args, **_kwargs: guard)
    monkeypatch.setattr(
        p1, "_resource_gate", lambda *_args, **_kwargs: resource_gate
    )
    monkeypatch.setattr(p1, "_validate_result_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        p1,
        "_child_outcome",
        lambda *_args, **_kwargs: (
            True,
            [],
            True,
            True,
            ["A_background_II", "A_conductor_II"],
            None,
        ),
    )
    monkeypatch.setattr(
        p1, "_validate_factor_monitor_interval", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(p1, "_git_head", lambda: "2" * 40)
    monkeypatch.setattr(p1, "_now_utc", lambda: UTC)


@pytest.mark.parametrize("valid_wrapper", [True, False])
def test_consume_atomically_tombstones_completed_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, valid_wrapper: bool
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = {"guard": "synthetic"}
    _patch_consumer_dependencies(
        monkeypatch, manifest_value, token, claim, guard, (True, [])
    )
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    result_path = tmp_path / "result.json"
    child_stdout_path = tmp_path / "child.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    child_payload = {"schema": p1.NUMERICAL_SCHEMA, "synthetic": True}
    _write_json(child_stdout_path, p1._wrap(child_payload))
    _write_json(
        resource_path,
        {
            "child_process_id": 333,
            "child_exit_code": 0,
            "child_stdout_sha256": p1._sha(child_stdout_path),
        },
    )
    result_payload = {
        "schema": p1.RESULT_SCHEMA,
        "mandatory_stage_pass": True,
        "failure_codes": [],
        "factorization_attempted": True,
        "factorization_performed": True,
        "completed_factors": ["A_background_II", "A_conductor_II"],
        "numerical": child_payload,
    }
    wrapper = p1._wrap(result_payload)
    if not valid_wrapper:
        wrapper = {"payload": result_payload, "payload_sha256": SHA_A}
    _write_json(result_path, wrapper)

    tombstone = p1.consume(
        token_path,
        claim_path,
        guard_path,
        "nonce",
        "completed_pass",
        result_path,
        resource_path,
        child_stdout_path,
    )

    assert json.loads(token_path.read_text(encoding="utf-8")) == tombstone
    assert tombstone["authorization_state"] == "consumed"
    assert tombstone["uses_remaining"] == 0
    assert tombstone["next_stage_authorized"] is False
    assert tombstone["physics_solve_performed"] is False
    if valid_wrapper:
        assert tombstone["consumption_validated_pass"] is True
        assert tombstone["effective_attempt_status"] == "completed_pass"
        assert tombstone["failure_codes"] == []
    else:
        assert tombstone["consumption_validated_pass"] is False
        assert tombstone["effective_attempt_status"] == "completed_invalid_evidence"
        assert "BLOCKED_AV_BS_RESULT_SCHEMA" in tombstone["failure_codes"]
        assert tombstone["result_evidence_valid"] is False


def test_normal_consumer_rejects_token_byte_drift_immediately_before_replace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    _patch_consumer_dependencies(
        monkeypatch, manifest_value, token, claim, {}, (False, ["tree_ws"])
    )
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    _write_json(resource_path, {"child_process_id": None, "child_exit_code": None})
    original_bytes = token_path.read_bytes()

    def drift_before_replace() -> str:
        token_path.write_bytes(original_bytes + b" ")
        return "2" * 40

    monkeypatch.setattr(p1, "_git_head", drift_before_replace)
    with pytest.raises(
        p1.AvBsError,
        match="authorized token bytes changed immediately before consumption",
    ):
        p1.consume(
            token_path,
            claim_path,
            guard_path,
            "nonce",
            "resource_stop",
            None,
            resource_path,
            None,
        )

    assert token_path.read_bytes() == original_bytes + b" "


def test_consume_tombstones_resource_stop_even_without_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = {"guard": "synthetic"}
    _patch_consumer_dependencies(
        monkeypatch, manifest_value, token, claim, guard, (False, ["tree_ws"])
    )
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    _write_json(resource_path, {"child_process_id": None, "child_exit_code": None})

    tombstone = p1.consume(
        token_path,
        claim_path,
        guard_path,
        "nonce",
        "resource_stop",
        None,
        resource_path,
        None,
    )

    assert tombstone["authorization_state"] == "consumed"
    assert tombstone["attempt_status"] == "resource_stop"
    assert tombstone["resource_evidence_valid"] is True
    assert tombstone["result_evidence_valid"] is False
    assert tombstone["resource_gate_recheck_failures"] == ["tree_ws"]
    assert tombstone["failure_codes"] == ["BLOCKED_AV_BS_RESOURCE"]
    assert tombstone["factorization_attempted"] is False
    assert tombstone["factorization_performed"] is False


def test_consume_recovers_valid_factor_prefix_without_child_or_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = {"guard": "synthetic"}
    _patch_consumer_dependencies(
        monkeypatch, manifest_value, token, claim, guard, (False, ["tree_ws"])
    )
    certificate = {"name": "A_background_II", "synthetic": True}
    prefix_payload = {
        "factor_order": ["A_background_II"],
        "factor_certificates": [certificate],
    }
    prefix_evidence = [
        {
            "count": 1,
            "file_sha256": SHA_A,
            "payload_sha256": SHA_B,
            "payload": prefix_payload,
        }
    ]
    monkeypatch.setattr(
        p1, "_factor_prefix_evidence", lambda *_args, **_kwargs: prefix_evidence
    )
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    _write_json(resource_path, {"child_process_id": 333, "child_exit_code": None})

    tombstone = p1.consume(
        token_path,
        claim_path,
        guard_path,
        "nonce",
        "resource_stop",
        None,
        resource_path,
        None,
    )

    assert tombstone["factor_prefix_evidence_valid"] is True
    assert tombstone["factor_prefix_evidence"] == prefix_evidence
    assert tombstone["factorization_attempted"] is True
    assert tombstone["factorization_performed"] is True
    assert tombstone["completed_factors"] == ["A_background_II"]
    assert tombstone["factor_order"] == ["A_background_II"]
    assert tombstone["factor_certificates"] == [certificate]
    assert tombstone["failure_codes"] == ["BLOCKED_AV_BS_RESOURCE"]


def test_consume_does_not_promote_invalid_factor_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_value = _synthetic_manifest()
    token = _synthetic_token()
    claim = _synthetic_claim()
    guard = {"guard": "synthetic"}
    _patch_consumer_dependencies(
        monkeypatch, manifest_value, token, claim, guard, (False, ["tree_ws"])
    )

    def invalid_prefix(*_args: object, **_kwargs: object) -> object:
        raise p1.AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "prefix checksum mismatch")

    monkeypatch.setattr(p1, "_factor_prefix_evidence", invalid_prefix)
    token_path = tmp_path / "token.json"
    claim_path = tmp_path / "claim.json"
    guard_path = tmp_path / "guard.json"
    resource_path = tmp_path / "resource.json"
    for path in (token_path, claim_path, guard_path):
        _write_json(path, {})
    _write_json(resource_path, {"child_process_id": 333, "child_exit_code": None})

    tombstone = p1.consume(
        token_path,
        claim_path,
        guard_path,
        "nonce",
        "resource_stop",
        None,
        resource_path,
        None,
    )

    assert tombstone["factor_prefix_evidence_valid"] is False
    assert tombstone["factor_prefix_evidence"] is None
    assert tombstone["factorization_attempted"] is None
    assert tombstone["factorization_performed"] is None
    assert tombstone["factor_certificates"] == []
    assert "BLOCKED_AV_BS_RESULT_SCHEMA" in tombstone["failure_codes"]
    assert "prefix checksum mismatch" in tombstone["evidence_validation_errors"]


def test_factor_prefix_must_match_later_child_certificate_prefix() -> None:
    first = {"name": "A_background_II", "sha256": SHA_A}
    second = {"name": "A_conductor_II", "sha256": SHA_B}
    numerical = {
        "schema": p1.NUMERICAL_SCHEMA,
        "factor_order": ["A_background_II", "A_conductor_II"],
        "factor_certificates": [first, second],
    }
    p1._validate_prefix_consistency(numerical, [first], ["A_background_II"])

    with pytest.raises(p1.AvBsError, match="factor prefix contradicts"):
        p1._validate_prefix_consistency(
            numerical,
            [{"name": "A_background_II", "sha256": SHA_C}],
            ["A_background_II"],
        )
    with pytest.raises(p1.AvBsError, match="factor prefix contradicts"):
        p1._validate_prefix_consistency(
            numerical,
            [first, second, {"name": "impossible"}],
            ["A_background_II", "A_conductor_II", "impossible"],
        )


def test_runner_is_literal_native_h2_p1_derivative_with_primary_gated() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert '[ValidateSet("manifest", "primary-h4-p0r")]' in source
    assert "AvBsH4P0RNativeV1" in source
    assert "Toolhelp32" in source
    assert "GetPerformanceInfo" in source
    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "CreateNew" in source
    assert "consume-primary-h4-p0r-token" in source
    assert "preflight-primary-h4-p0r" in source
    assert "WALL_TIME_STOP" in source
    assert "Get-ProcessBirthTicks" in source
    assert "PID reuse detected" in source
    assert "child_visible_tree_sample_count" in source
    assert "Write-AtomicUtf8NoBom" in source
    assert "$claimCreated" in source
    assert "RUNNER_EXCEPTION" in source
    assert "$ids = @($ids | Sort-Object -Unique)" in source
    assert ":treeSampleAttempt for ($attempt = 1;" in source
    assert "$consumeWrapper.payload.failure_codes.Count" not in source
    assert source.count(
        "Write-Error $_.Exception.Message -ErrorAction Continue"
    ) == 2

    strict_mode_probe = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "Set-StrictMode -Version Latest; "
                "function Emit-Ids([int]$Count) { "
                "for ($i = 0; $i -lt $Count; $i += 1) { Write-Output $i } }; "
                "foreach ($expected in @(0, 1, 3)) { "
                "$ids = @(Emit-Ids $expected); "
                "if ($ids.Count -ne $expected) { exit 9 } }; "
                "Write-Output 'STRICT_ARRAY_CAPTURE_OK'"
            ),
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert strict_mode_probe.returncode == 0, strict_mode_probe.stderr
    assert strict_mode_probe.stdout.strip() == "STRICT_ARRAY_CAPTURE_OK"

    exit_code_probe = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                "try { throw 'EXPECTED_FAILURE' } "
                "catch { Write-Error $_.Exception.Message -ErrorAction Continue; exit 2 }"
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert exit_code_probe.returncode == 2
    assert "EXPECTED_FAILURE" in exit_code_probe.stderr


def _run_tree_sample_slice(
    tmp_path: Path, case_script: str
) -> dict[str, object]:
    source = RUNNER.read_text(encoding="utf-8")
    begin_marker = "# AV_BS_TREE_SAMPLE_TEST_SLICE_BEGIN"
    end_marker = "# AV_BS_TREE_SAMPLE_TEST_SLICE_END"
    assert source.count(begin_marker) == 1
    assert source.count(end_marker) == 1
    function_slice = source[
        source.index(begin_marker) : source.index(end_marker)
    ]
    for forbidden in (
        "Start-Process",
        "Add-Type",
        "AvBsH4P0RNativeV1",
        "review_token",
        "primary-h4-p0r",
        "splu",
        "consume-primary",
    ):
        assert forbidden not in function_slice
    assert function_slice.count("function Get-TreeSample(") == 1
    script = (
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest;\n"
        + function_slice
        + "\n"
        + case_script
    )
    script_path = tmp_path / "tree-sample-harness.ps1"
    script_path.write_text(script, encoding="ascii", newline="\n")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, completed.stdout
    return json.loads(lines[0])


def test_tree_sample_restarts_whole_sample_after_confirmed_nonroot_exit(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0
$script:metricAttempt=0
$script:childIdentityQueries=0
$script:childMetricCalls=0
$script:snapshotQueries=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId)
        $script:enumerations+=1; $script:metricAttempt=$script:enumerations
        if($script:enumerations -eq 1){return @([int]100,[int]200,[int]300)}
        return @([int]100,[int]200)
    }
    Identity={param([int]$ProcessId)
        if($ProcessId -eq 100){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
        if($ProcessId -eq 200){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]2000}}
        $script:childIdentityQueries+=1
        if($script:childIdentityQueries -eq 1){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]3000}}
        return [ordered]@{status='not_found';operation='open_process';win32_error_code=[int]87;birth_utc_ticks=$null}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -eq 300){$script:childMetricCalls+=1;return [ordered]@{status='not_found';operation='open_process';win32_error_code=[int]87;birth_utc_ticks=$null;values=$null}}
        $value=if($script:metricAttempt -eq 1){[int64]$ProcessId}else{[int64]($ProcessId/100)}
        $birth=if($ProcessId -eq 100){[int64]1000}else{[int64]2000}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=$birth;values=[int64[]]@($value,$value,$value,$value,$value,$value,$value,$value)}
    }
    SnapshotContains={param([int]$ProcessId) $script:snapshotQueries+=1; return $false}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
$sample=Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3
[ordered]@{sample=$sample;enumerations=$script:enumerations;child_identity_queries=$script:childIdentityQueries;child_metric_calls=$script:childMetricCalls;snapshot_queries=$script:snapshotQueries;retry_count=$diagnostics.confirmed_disappearance_count;events=@($diagnostics.retry_events)} | ConvertTo-Json -Depth 12 -Compress
"""
    )
    assert result["enumerations"] == 2
    assert result["child_identity_queries"] == 2
    assert result["child_metric_calls"] == 1
    assert result["snapshot_queries"] == 1
    assert result["retry_count"] == 1
    assert result["sample"]["process_ids"] == [100, 200]
    for key in (
        "working_set_bytes",
        "summed_process_peak_working_set_bytes",
        "committed_pagefile_bytes",
        "summed_process_peak_commit_bytes",
        "private_commit_bytes",
        "private_working_set_bytes",
        "shared_commit_bytes",
        "page_fault_count",
    ):
        assert result["sample"][key] == 3
    assert result["events"] == [
        {
            "attempt": 1,
            "confirmation": (
                "limited_query_not_found_and_complete_snapshot_absent"
            ),
            "context": "outer_tree_sample",
            "expected_birth_utc_ticks": 3000,
            "message_code": "CONFIRMED_NONROOT_DISAPPEARANCE",
            "observed_birth_utc_ticks": None,
            "operation": "open_process",
            "process_id": 300,
            "process_role": "descendant",
            "win32_error_code": 87,
        }
    ]


def test_tree_sample_live_metric_failure_is_fatal_without_retry(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; return @([int]100,[int]200)}
    Identity={param([int]$ProcessId)
        $birth=if($ProcessId -eq 100){[int64]1000}else{[int64]2000}
        return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=$birth}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -eq 200){return [ordered]@{status='query_failed';operation='get_process_memory_info';win32_error_code=[int]5;birth_utc_ticks=[int64]2000;values=$null}}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=[int64]1000;values=[int64[]]@(1,1,1,1,1,1,1,1)}
    }
    SnapshotContains={param([int]$ProcessId) return $true}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch {
    [ordered]@{message=$_.Exception.Message;message_code=$_.Exception.Data['message_code'];operation=$_.Exception.Data['operation'];process_id=$_.Exception.Data['process_id'];win32_error_code=$_.Exception.Data['win32_error_code'];enumerations=$script:enumerations;observed=@($ids|Sort-Object)} | ConvertTo-Json -Depth 8 -Compress
}
"""
    )
    assert result == {
        "message": (
            "BLOCKED_AV_BS_RESOURCE: tree sample failure "
            "PROCESS_METRIC_QUERY_FAILED_WHILE_LIVE"
        ),
        "message_code": "PROCESS_METRIC_QUERY_FAILED_WHILE_LIVE",
        "operation": "get_process_memory_info",
        "process_id": 200,
        "win32_error_code": 5,
        "enumerations": 1,
        "observed": [100, 200],
    }


def test_tree_sample_query_failure_remains_fatal_if_child_then_disappears(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0; $script:childIdentityQueries=0; $script:snapshotQueries=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; return @([int]100,[int]200)}
    Identity={param([int]$ProcessId)
        if($ProcessId -eq 100){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
        $script:childIdentityQueries+=1
        if($script:childIdentityQueries -eq 1){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]2000}}
        return [ordered]@{status='not_found';operation='open_process';win32_error_code=[int]87;birth_utc_ticks=$null}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -eq 200){return [ordered]@{status='query_failed';operation='get_process_memory_info';win32_error_code=[int]5;birth_utc_ticks=[int64]2000;values=$null}}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=[int64]1000;values=[int64[]]@(1,1,1,1,1,1,1,1)}
    }
    SnapshotContains={param([int]$ProcessId) $script:snapshotQueries+=1; return $false}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch { [ordered]@{message_code=$_.Exception.Data['message_code'];operation=$_.Exception.Data['operation'];process_id=$_.Exception.Data['process_id'];enumerations=$script:enumerations;child_identity_queries=$script:childIdentityQueries;snapshot_queries=$script:snapshotQueries;retry_count=$diagnostics.confirmed_disappearance_count} | ConvertTo-Json -Compress }
""",
    )
    assert result == {
        "message_code": "PROCESS_METRIC_QUERY_FAILED_WHILE_LIVE",
        "operation": "get_process_memory_info",
        "process_id": 200,
        "enumerations": 1,
        "child_identity_queries": 1,
        "snapshot_queries": 0,
        "retry_count": 0,
    }


@pytest.mark.parametrize(
    ("root_status", "root_birth", "message_code"),
    [
        ("not_found", None, "ROOT_DISAPPEARED_AFTER_ENUMERATION"),
        ("live", 1001, "ROOT_PID_REUSE_AFTER_ENUMERATION"),
    ],
)
def test_tree_sample_root_failure_after_enumeration_is_never_retried(
    tmp_path: Path,
    root_status: str,
    root_birth: int | None,
    message_code: str,
) -> None:
    birth_literal = "$null" if root_birth is None else f"[int64]{root_birth}"
    result = _run_tree_sample_slice(
        tmp_path,
        rf"""
$script:enumerations=0; $script:rootQueries=0
$providers=[ordered]@{{
    Enumerate={{param([int]$RootProcessId) $script:enumerations+=1; return @([int]100)}}
    Identity={{param([int]$ProcessId)
        $script:rootQueries+=1
        if($script:rootQueries -eq 1){{return [ordered]@{{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}}}
        return [ordered]@{{status='{root_status}';operation='get_process_times';win32_error_code=$null;birth_utc_ticks={birth_literal}}}
    }}
    Metrics={{param([int]$ProcessId) throw 'metrics must not run'}}
    SnapshotContains={{param([int]$ProcessId) throw 'snapshot confirmation must not run'}}
}}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try {{ [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }}
catch {{ [ordered]@{{message_code=$_.Exception.Data['message_code'];enumerations=$script:enumerations;root_queries=$script:rootQueries}} | ConvertTo-Json -Compress }}
"""
    )
    assert result == {
        "message_code": message_code,
        "enumerations": 1,
        "root_queries": 2,
    }


def test_tree_sample_exited_identity_is_retained_and_pid_reuse_is_fatal(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0; $script:childIdentityQueries=0; $script:childMetrics=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; return @([int]100,[int]300)}
    Identity={param([int]$ProcessId)
        if($ProcessId -eq 100){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
        $script:childIdentityQueries+=1
        if($script:childIdentityQueries -eq 1){return [ordered]@{status='exited';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]3000}}
        return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]4000}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -eq 300){$script:childMetrics+=1;throw 'reused child metrics must not run'}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=[int64]1000;values=[int64[]]@(1,1,1,1,1,1,1,1)}
    }
    SnapshotContains={param([int]$ProcessId) return $false}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch { [ordered]@{message_code=$_.Exception.Data['message_code'];expected_birth=$_.Exception.Data['expected_birth_utc_ticks'];observed_birth=$_.Exception.Data['observed_birth_utc_ticks'];enumerations=$script:enumerations;child_identity_queries=$script:childIdentityQueries;child_metrics=$script:childMetrics;recorded_birth=$births[300];retry_count=$diagnostics.confirmed_disappearance_count;event=@($diagnostics.retry_events)[0]} | ConvertTo-Json -Depth 10 -Compress }
""",
    )
    assert result["message_code"] == "NONROOT_PID_REUSE"
    assert result["expected_birth"] == 3000
    assert result["observed_birth"] == 4000
    assert result["enumerations"] == 2
    assert result["child_identity_queries"] == 2
    assert result["child_metrics"] == 0
    assert result["recorded_birth"] == 3000
    assert result["retry_count"] == 1
    assert result["event"]["process_id"] == 300
    assert result["event"]["observed_birth_utc_ticks"] == 3000


def test_tree_sample_first_seen_stale_exited_identity_is_fatal(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0; $script:snapshotQueries=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; return @([int]100,[int]300)}
    Identity={param([int]$ProcessId)
        if($ProcessId -eq 100){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
        return [ordered]@{status='exited';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]999}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -eq 300){throw 'stale child metrics must not run'}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=[int64]1000;values=[int64[]]@(1,1,1,1,1,1,1,1)}
    }
    SnapshotContains={param([int]$ProcessId) $script:snapshotQueries+=1; return $false}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch { [ordered]@{message_code=$_.Exception.Data['message_code'];observed_birth=$_.Exception.Data['observed_birth_utc_ticks'];enumerations=$script:enumerations;snapshot_queries=$script:snapshotQueries;retry_count=$diagnostics.confirmed_disappearance_count} | ConvertTo-Json -Compress }
""",
    )
    assert result == {
        "message_code": "NONROOT_IDENTITY_INVALID_OR_STALE",
        "observed_birth": 999,
        "enumerations": 1,
        "snapshot_queries": 0,
        "retry_count": 0,
    }


def test_inner_cleanup_tree_sample_failure_remains_fatal_after_later_sample() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    cleanup_start = source.index('$currentOuterOperation = "outer_cleanup"')
    cleanup_end = source.index("$highPrePostFloorsPass =", cleanup_start)
    cleanup = source[cleanup_start:cleanup_end]
    sample_call = cleanup.index('"inner_cleanup_tree_sample"')
    sample_guard = cleanup.rfind("if (-not $process.HasExited)", 0, sample_call)
    live_probe = cleanup.index("$liveOwnedBeforeCleanup = @(", sample_call)
    sample_failure_branch = cleanup[sample_guard:live_probe]

    assert "if (-not $process.HasExited)" in sample_failure_branch
    assert "catch { }" not in sample_failure_branch
    assert "Get-NormalizedOuterMonitorFailure" in sample_failure_branch
    assert '$_.Exception "inner_cleanup_tree_sample"' in sample_failure_branch
    assert "$monitorErrorPresent = $true" in sample_failure_branch
    assert '$stopReason = "OUTER_INNER_CLEANUP_FAILED"' in sample_failure_branch

    gate_start = source.index("$mandatoryOuterGate =", cleanup_end)
    gate_end = source.index("$identityEvidence =", gate_start)
    preservation = source[cleanup_start + sample_call : gate_start]
    mandatory_gate = source[gate_start:gate_end]
    assert "$monitorFailure = $null" not in preservation
    assert "$monitorErrorPresent = $false" not in preservation
    assert "$stopReason = $null" not in preservation
    assert "-not $monitorErrorPresent" in mandatory_gate
    assert "$null -eq $monitorFailure" in mandatory_gate
    assert "-not $stopReason" in mandatory_gate


def test_tree_sample_retry_exhaustion_is_bounded_and_fatal(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0; $script:childMetrics=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; return @([int]100,[int]300)}
    Identity={param([int]$ProcessId)
        if($ProcessId -eq 100){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
        return [ordered]@{status='not_found';operation='open_process';win32_error_code=[int]87;birth_utc_ticks=$null}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -eq 300){$script:childMetrics+=1;return [ordered]@{status='not_found';operation='open_process';win32_error_code=[int]87;birth_utc_ticks=$null;values=$null}}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=[int64]1000;values=[int64[]]@(1,1,1,1,1,1,1,1)}
    }
    SnapshotContains={param([int]$ProcessId) return $false}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch { [ordered]@{message_code=$_.Exception.Data['message_code'];attempt=$_.Exception.Data['attempt'];enumerations=$script:enumerations;child_metrics=$script:childMetrics;retry_count=$diagnostics.confirmed_disappearance_count} | ConvertTo-Json -Compress }
"""
    )
    assert result == {
        "message_code": "TRANSIENT_DESCENDANT_DISAPPEARANCE_RETRY_EXHAUSTED",
        "attempt": 3,
        "enumerations": 3,
        "child_metrics": 0,
        "retry_count": 3,
    }


def test_tree_sample_error87_with_pid_in_fresh_snapshot_is_fatal(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0; $script:snapshotQueries=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; return @([int]100,[int]300)}
    Identity={param([int]$ProcessId)
        if($ProcessId -eq 100){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
        return [ordered]@{status='not_found';operation='open_process';win32_error_code=[int]87;birth_utc_ticks=$null}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -ne 100){throw 'child identity failure must precede child metrics'}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=[int64]1000;values=[int64[]]@(1,1,1,1,1,1,1,1)}
    }
    SnapshotContains={param([int]$ProcessId) $script:snapshotQueries+=1; return $true}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch { [ordered]@{message_code=$_.Exception.Data['message_code'];enumerations=$script:enumerations;snapshot_queries=$script:snapshotQueries;retry_count=$diagnostics.confirmed_disappearance_count} | ConvertTo-Json -Compress }
""",
    )
    assert result == {
        "message_code": "NONROOT_DISAPPEARANCE_NOT_CONFIRMED",
        "enumerations": 1,
        "snapshot_queries": 1,
        "retry_count": 0,
    }


def test_tree_sample_incomplete_confirmation_snapshot_is_fatal(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0; $script:snapshotQueries=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; return @([int]100,[int]300)}
    Identity={param([int]$ProcessId)
        if($ProcessId -eq 100){return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
        return [ordered]@{status='not_found';operation='open_process';win32_error_code=[int]87;birth_utc_ticks=$null}
    }
    Metrics={param([int]$ProcessId)
        if($ProcessId -ne 100){throw 'child identity failure must precede child metrics'}
        return [ordered]@{status='ok';operation='get_process_memory_info';win32_error_code=$null;birth_utc_ticks=[int64]1000;values=[int64[]]@(1,1,1,1,1,1,1,1)}
    }
    SnapshotContains={param([int]$ProcessId) $script:snapshotQueries+=1; throw 'incomplete snapshot'}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch { [ordered]@{message_code=$_.Exception.Data['message_code'];operation=$_.Exception.Data['operation'];enumerations=$script:enumerations;snapshot_queries=$script:snapshotQueries;retry_count=$diagnostics.confirmed_disappearance_count} | ConvertTo-Json -Compress }
""",
    )
    assert result == {
        "message_code": "COMPLETE_PROCESS_SNAPSHOT_QUERY_FAILED",
        "operation": "toolhelp_process_snapshot",
        "enumerations": 1,
        "snapshot_queries": 1,
        "retry_count": 0,
    }


def test_tree_sample_incomplete_initial_enumeration_is_fatal(
    tmp_path: Path,
) -> None:
    result = _run_tree_sample_slice(
        tmp_path,
        r"""
$script:enumerations=0
$providers=[ordered]@{
    Enumerate={param([int]$RootProcessId) $script:enumerations+=1; throw 'incomplete enumeration'}
    Identity={param([int]$ProcessId) return [ordered]@{status='live';operation='get_process_times';win32_error_code=$null;birth_utc_ticks=[int64]1000}}
    Metrics={param([int]$ProcessId) throw 'metrics must not run'}
    SnapshotContains={param([int]$ProcessId) throw 'snapshot confirmation must not run'}
}
$ids=New-Object 'System.Collections.Generic.HashSet[int]'
$births=New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$births.Add(100,[int64]1000); [void]$ids.Add(100)
$diagnostics=New-TreeSampleDiagnostics 16
try { [void](Get-TreeSample 100 1000 $ids $births 'outer_tree_sample' $diagnostics $providers 3); exit 91 }
catch { [ordered]@{message_code=$_.Exception.Data['message_code'];operation=$_.Exception.Data['operation'];enumerations=$script:enumerations;retry_count=$diagnostics.confirmed_disappearance_count} | ConvertTo-Json -Compress }
""",
    )
    assert result == {
        "message_code": "TREE_ENUMERATION_FAILED",
        "operation": "toolhelp_process_snapshot",
        "enumerations": 1,
        "retry_count": 0,
    }


def test_runner_native_process_snapshot_and_metric_probes_are_fail_closed() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "[ValidateRange(1, 3)][int]$MaximumAttempts = 1" in source
    assert source.count(
        "$treeSampleDiagnostics $null $treeSampleMaximumAttempts"
    ) == 6
    native = source[
        source.index("public static class AvBsH4P0RNativeV1") : source.index(
            "function Get-NativeProcessParents"
        )
    ]
    assert "const int ERROR_NO_MORE_FILES = 18;" in native
    assert "const int ERROR_INVALID_PARAMETER = 87;" in native
    assert "if (error != ERROR_NO_MORE_FILES)" in native
    metric_probe = native[
        native.index("public static long[] ProcessMetricProbe") : native.index(
            "public static long[] ProcessIdentityProbe"
        )
    ]
    assert metric_probe.index("GetProcessTimes") < metric_probe.index(
        "GetProcessMemoryInfo"
    )
    assert "FILETIME creationAfter, exitAfter, kernelAfter, userAfter;" in metric_probe
    assert "!IsZero(exitAfter)" in metric_probe
    assert "error == ERROR_INVALID_PARAMETER ? 0 : -1" in metric_probe


def test_runner_gates_inner_and_outer_token_mutation_on_verified_cleanup() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    inner_consumer_gate = (
        "if ($claimCreated -and $factorRootExitObserved -and "
        "$factorCleanupVerified -and "
        "@($factorOwnedSurvivorsAfterCleanup).Count -eq 0)"
    )
    assert inner_consumer_gate in source
    inner_emergency = source[
        source.index("function Set-EmergencyConsumedTombstone(") : source.index(
            "function Get-OuterEmergencyReplacementJournalPaths("
        )
    ]
    for required in (
        "-not $FactorRootExitObserved",
        "-not $FactorCleanupVerified",
        "$OwnedSurvivorCount -ne 0",
        "emergency consumption withheld before verified factor-tree cleanup",
    ):
        assert required in inner_emergency

    outer_emergency = source[
        source.index("function Set-OuterObserverEmergencyConsumedTombstone(") :
        source.index("function Write-PreExitControlPlaneEvidence(")
    ]
    for required in (
        "-not $RetainedInnerActualExitObserved",
        "-not $OuterCleanupVerified",
        "$OwnedSurvivorCount -ne 0",
        "outer emergency recovery inputs are incomplete",
    ):
        assert required in outer_emergency
    assert (
        "$retainedInnerActualExitObserved -and\n"
        "        $cleanupVerified -and\n"
        "        $innerProcessLiveAfterCleanup -eq $false -and\n"
        "        @($ownedDescendantSurvivors).Count -eq 0"
    ) in source
    assert (
        "$postCleanupTokenSha256 -eq $originalTokenSha256 -and\n"
        "                        (Test-ByteArrayEqual $currentTokenBytes "
        "$originalTokenBytes) -and\n"
        "                        $outerRecoveryMutationGate"
    ) in source


def test_runner_preserves_hashed_markers_in_canonical_quarantine() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    terminal_hash_block = source[
        source.index("$terminalArtifactHashPresent = $false") : source.index(
            "$resolvedTemp = [IO.Path]::GetFullPath($tempDirectory)"
        )
    ]
    for field in (
        "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256",
        "monitor_release_marker_sha256",
    ):
        assert f'"{field}"' in terminal_hash_block
    assert "$preserveAttemptEvidence = $true" in terminal_hash_block
    cleanup_block = source[
        source.index("$resolvedTemp = [IO.Path]::GetFullPath($tempDirectory)") :
        source.index("if ($null -eq $runnerExitCode)")
    ]
    assert "-not $preserveAttemptEvidence -and -not $terminalArtifactHashPresent" in (
        cleanup_block
    )
    assert "Move-Item -LiteralPath $resolvedTemp -Destination $quarantinePath" in (
        cleanup_block
    )
    for leaf in ("monitor-ready.json", "factor-complete.json", "monitor-release.json"):
        assert leaf in source


def test_runner_rechecks_quarantined_markers_before_and_after_terminal_seal() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    marker_assertion = source[
        source.index("function Assert-QuarantinedFactorMonitorMarkerBindings(") :
        source.index("function Assert-OuterInnerCompleteMarker(")
    ]
    for required in (
        "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256",
        "monitor_release_marker_sha256",
        "Get-Sha256 $markerPath",
        "factor-monitor marker bytes changed",
    ):
        assert required in marker_assertion

    direct_complete_recheck = (
        'Assert-QuarantinedFactorMonitorMarkerBindings $Complete '
        '"outer inner-complete"'
    )
    source_current_recheck = (
        'Assert-QuarantinedFactorMonitorMarkerBindings $ExpectedComplete '
        '"outer terminal seal source current-byte recheck"'
    )
    seal_write = "Write-AtomicUtf8NoBom $sealPathInfo.path"
    seal_readback = "$sealReadback = Read-BoundedJsonObject $sealPathInfo.path"
    post_seal_source_recheck = source.rindex(
        "Assert-OuterTerminalSealSourcesCurrent `"
    )
    assert source.count("Assert-QuarantinedFactorMonitorMarkerBindings") == 3
    assert source.index(direct_complete_recheck) < source.index(seal_write)
    assert source.index(source_current_recheck) < source.index(seal_write)
    assert (
        source.index(seal_write)
        < source.index(seal_readback)
        < post_seal_source_recheck
    )


def test_runner_withholds_terminal_seal_without_consumer_report_and_close() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    source_recheck = source[
        source.index("function Assert-OuterTerminalSealSourcesCurrent(") :
        source.index("function Invoke-OuterObserverPrimary")
    ]
    for required in (
        "$consumerTerminalReferenceComplete = (",
        "$currentPreExit.consumer_report_relative_path -is [string]",
        "$currentPreExit.consumer_report_sha256 -match '^[0-9a-f]{64}$'",
        "$currentPreExit.consumer_envelope_close_relative_path -is [string]",
        "$currentPreExit.consumer_envelope_close_sha256 -match '^[0-9a-f]{64}$'",
        "token-consumer control report/close reference is missing; terminal seal withheld",
        "token-consumer control report/close source is missing; terminal seal withheld",
        "token-consumer control report/close source changed; terminal seal withheld",
    ):
        assert required in source_recheck
    assert source.index(
        "$consumerTerminalReferenceComplete = (",
        source.index("function Assert-OuterTerminalSealSourcesCurrent("),
    ) < source.index("Write-AtomicUtf8NoBom $sealPathInfo.path")


def test_runner_uses_one_exact_seventeen_reason_outer_resource_category_map() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    category_function = source[
        source.index("function Get-OuterObserverFailureCode(") : source.index(
            "function Wait-ForOuterObserverMarker("
        )
    ]
    observed = tuple(re.findall(r'"(OUTER_[A-Z_]+)"', category_function))
    assert observed == OUTER_RESOURCE_STOP_REASONS
    assert len(observed) == 17
    assert '$StopReason -in $resourceStopReasons' in category_function
    assert 'return "BLOCKED_AV_BS_RESOURCE"' in category_function
    assert category_function.rstrip().endswith("}")
    assert source.count("Get-OuterObserverFailureCode") == 3
    assert (
        "$recoveryFailureCode = Get-OuterObserverFailureCode "
        "([string]$stopReason)"
    ) in source
    assert (
        "$outerGateFailureCode = Get-OuterObserverFailureCode "
        "([string]$stopReason)"
    ) in source


def test_runner_repairs_only_an_owned_claim_before_finally_consumes() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    create = source.index("[IO.FileMode]::CreateNew")
    collision = source.index("token claim already exists")
    owner = source.index("$claimCreated = $true")
    recovery = source.index("# Retry only the deterministic evidence materialization")
    final_consume = source.index(
        "if ($claimCreated -and $factorRootExitObserved -and "
        "$factorCleanupVerified -and "
        "@($factorOwnedSurvivorsAfterCleanup).Count -eq 0)"
    )
    assert create < collision < owner < recovery < final_consume
    recovery_block = source[recovery:final_consume]
    for required in (
        "if ($claimCreated)",
        "[IO.FileMode]::Open",
        "$repairStream.SetLength(0)",
        "$repairStream.Flush($true)",
        "$claimHash = Get-Sha256 $claimPath",
        "$claimCanonicalHash = Get-CanonicalJsonSha256",
        "$claimPath",
        "$guard = & $buildGuardPayload $claimHash $claimCanonicalHash",
        "Write-AtomicUtf8NoBom $guardPath",
    ):
        assert required in recovery_block
    finally_block = source[final_consume:]
    assert '"--stage", "consume-primary-h4-p0r-token"' in finally_block
    assert '"--result-file", $finalPath' in finally_block


def test_claim_contract_binds_bounded_preflight_report_and_index() -> None:
    source = FIXTURE.read_text(encoding="utf-8")
    claim_validator = source[
        source.index("def _validate_claim("):source.index("def _now_utc(")
    ]
    for required in (
        "control_plane_preflight_report_relative_path",
        "control_plane_preflight_report_sha256",
        "control_plane_preflight_session_index_relative_path",
        "control_plane_preflight_session_index_sha256",
    ):
        assert required in claim_validator
    assert (
        "_validate_preflight_control_plane_evidence(claim, manifest, observer)"
        in claim_validator
    )


def test_runner_token_filename_and_fixture_binding_are_isolated() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "av_bs1_h4_p0r_p1_review_token.json" in source
    assert "av_bs1_boundary_schur_h4_p0r_p1.py" in source
    assert "h2_p1" not in source
    assert "primary-h2" not in source


def test_parser_has_distinct_result_artifact_for_consumption() -> None:
    parser = p1._parser()
    action_options = {option for action in parser._actions for option in action.option_strings}
    assert "--result-file" in action_options
    source = FIXTURE.read_text(encoding="utf-8")
    assert "a.result_file,a.resource_report,a.child_stdout" in source


def test_doc_declares_token_absent_no_factor_and_no_physics() -> None:
    text = DOC.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    status_fields = dict(
        re.findall(
            r"`(token_state|status|authorization_state|"
            r"terminal_evidence_complete|authoritative_stage_pass)=([^`]+)`",
            text,
        )
    )
    assert status_fields == {
        "token_state": "absent",
        "status": "candidate_token_missing_no_factor",
        "authorization_state": "not_authorized",
        "terminal_evidence_complete": "false",
        "authoritative_stage_pass": "false",
    }
    assert "no H4-P0R-P1 factorization or physics solve has run" in normalized
    assert "no later H4-P1 stage is authorized" in normalized
    assert "`control_plane.independently_bounded=true`" in text
    assert "`tree_thresholds_equal_factor_envelope=true`" in text
    assert "`system_floor_recheck_before_and_after_each=true`" in text
    assert "`implementation_status=implemented_and_static_audited`" in text
    assert "`authorization_blocker=false`" in text
    assert "The candidate generates no RHS and calls no factor solve" in normalized
    assert "authoritative factor-only pass grants no H4-P1 authorization" in normalized


def test_partial_factor_truth_and_current_lifecycle_blocker_are_explicit() -> None:
    text = DOC.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    assert "child PID/exit, and factorization attempted/performed are null" in normalized
    assert "`physics_solve_performed=false`" in text
    assert "`next_stage_authorized=false`" in text
    assert (
        "A missing token-consumer control report or envelope-close reference "
        "is a valid controlled emergency condition"
    ) in normalized
    assert "the outer observer must withhold both the seal" in normalized
    assert "No report is synthesized and no validator is weakened" in normalized
    disclosures = dict(
        re.findall(
            r"`(external_runner_or_machine_kill_terminal_state_guaranteed|"
            r"adversarial_local_caller_exclusion_claimed)=([^`]+)`",
            text,
        )
    )
    assert disclosures == {
        "external_runner_or_machine_kill_terminal_state_guaranteed": "false",
        "adversarial_local_caller_exclusion_claimed": "false",
    }
    source = FIXTURE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    consume_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "consume"
    )
    consume_source = ast.get_source_segment(source, consume_node)
    assert consume_source is not None
    assert "completed_pass" in consume_source
    assert "resource_stop" in consume_source
    assert "runner_exception" in consume_source
    assert "_atomic_replace" in consume_source
    assert "uses_remaining" in consume_source


def test_lf_and_hashes_are_well_formed() -> None:
    for path in (FIXTURE, RUNNER, Path(__file__), DOC):
        raw = path.read_bytes()
        assert b"\r\n" not in raw
        assert len(hashlib.sha256(raw).hexdigest()) == 64


def test_failure_normalization_is_four_code_closed() -> None:
    value = p1._failure(p1.AvBsError("NOT_AN_ALLOWED_CODE", "tamper"), "manifest")
    assert value["status"] == "BLOCKED_AV_BS_RESULT_SCHEMA"
    assert value["failure_codes"] == ["BLOCKED_AV_BS_RESULT_SCHEMA"]
    assert value["factorization_performed"] is False
