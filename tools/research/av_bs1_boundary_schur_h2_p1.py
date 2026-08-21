#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.22.0 AV-BS1 H2-P1 execution contract.

This research-only fixture deliberately has no review token in the checkout.
It can certify the immutable H1/H2-P0 lineage, but a numerical h2 solve is
structurally unreachable before a separately reviewed, tracked token exists.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import math
import os
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Mapping

import numpy as np
from scipy.sparse import csc_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
import av_bs1_boundary_schur as h1
import av_bs1_boundary_schur_h2 as p0


PROGRAM = h1.PROGRAM
CASE_ID = h1.CASE_ID
FIXTURE_SCHEMA = "AV-BS1-h2-p1-fixture-v1"
NUMERICAL_SCHEMA = "AV-BS1-h2-p1-numerical-v1"
RESULT_SCHEMA = "AV-BS1-h2-p1-result-v1"
TOKEN_SCHEMA = "AV-BS1-h2-p1-review-token-v1"
RESOURCE_SCHEMA = "AV-BS1-h2-p1-resource-report-v1"
GUARD_SCHEMA = "AV-BS1-h2-p1-resource-guard-v1"
TOMBSTONE_SCHEMA = "AV-BS1-h2-p1-consumed-review-token-v1"
FAILURE_SCHEMA = "AV-BS1-h2-p1-failure-v1"
CLAIM_SCHEMA = "AV-BS1-h2-p1-token-claim-v1"
TREND_SCHEMA = "AV-BS1-h-to-h2-trend-v1"
ALLOWED_FAILURE_CODES = frozenset({
    "BLOCKED_AV_BS_ANALYTIC",
    "BLOCKED_AV_BS_MESH_HASH",
    "BLOCKED_AV_BS_PASSIVITY",
    "BLOCKED_AV_BS_POWER",
    "BLOCKED_AV_BS_RECIPROCITY",
    "BLOCKED_AV_BS_RESOURCE",
    "BLOCKED_AV_BS_RESULT_SCHEMA",
    "BLOCKED_AV_BS_SOLVE",
})
H1_FIXTURE_SHA256 = p0.H1_FIXTURE_SHA256
H1_RUNNER_SHA256 = p0.H1_RUNNER_SHA256
H1_TOMBSTONE_SHA256 = p0.H1_TOMBSTONE_SHA256
H1_ARTIFACT_SHA256 = "af17bbcc49cebc7e9ddb88e821ec0338a435fb2bf8ce51b117cfb3019b78b44d"
H1_PAYLOAD_SHA256 = "3cdef96c8de1585acfe4cc256d63e6815b93be9906df51d9b97ff5d4f51f330b"
H1_NUMERICAL_SHA256 = "a955393d69e22e87d759656b598e71fccee2492eff4c8d305db48623662f9fc4"
H1_RESOURCE_SHA256 = "8387d19253279120a116cf0e8b48c67007394a86d140bfb0a1770ad4f8b87d72"
H1_MODE_VIEW_SHA256 = "034154b41d75e3ecc707fa2c82ee5012ecd4b72155a20fabbb9a3abe7b427f69"
H1_OPERATOR_Y_SHA256 = "068540a32ffca34f6b861a46af00cc17dfa11bfc5d6a7604bf98f9fdabf0b7a4"
H1_OPERATOR_Y_REVERSE_SHA256 = "2098f2ba8c5d8424a29713bae7021f18091cd7a81e73afdf25181d0a6b464625"
H1_CONSUMED_TOKEN_SHA256 = "5ad21ccec9cb81e8999441fc43338589ecb92cf0ae08e7bc2b8b4a8c411f8d4e"
H1_MODE_FLOOR_S = 5.214985896880959e-08
H1_STATUS = "passed_AV_BS_h_stage_only_pending_h2_review"
P0_FIXTURE_SHA256 = "032100623fca51ab22a48493b46f23bc8ce5fd1250203d527062671d47599384"
P0_RUNNER_SHA256 = "6ce0002e9d3790542008d9e1e608168f1e40f51d56c9a8ff1d37003a15f8feb7"
P0_PAYLOAD_SHA256 = "68d2e20a471e0e9475dd8e575ffd1e246f4098bb6c0e2b688d0f6f702fb511ba"
P0_COMMIT = "4c1e3fce8aac659dd0aedb06c2b6d274fff73a12"
P0_TEST_SHA256 = "039b84ea05ee31c9f9d025d85dd1ec8d4741adc18e6db8883073d93e4d828c69"
P0_DOC_SHA256 = "623cfbc761c08a36aa1c44ad1d24596c55dbdfcdfd9e3c519bc08c0e630fae25"
P0_ASSEMBLY_PAYLOAD_SHA256 = "654ddd2e16365df57ee54013e817a73da97cc0b85bcfe90119d292b43040698a"
P0_RESOURCE_PAYLOAD_SHA256 = "b49fc985373f3ad145ae40095b97be8c817e9482900b045ffd5c4f787426c3c9"
WALL_STOP_SECONDS = 900
ONE_FACTOR_PER_FREQUENCY = True
BOUNDARY_RHS_BATCH = 4
SIGNED_M9 = tuple(range(-4, 5))
REVIEW_TOKEN_PATH = Path(__file__).with_name("av_bs1_h2_p1_review_token.json")
H1_ARTIFACT_RELATIVE = Path("validation-output/av-bs1/av-bs1-primary-h-20260814T190732Z.json")
H1_TOMBSTONE_RELATIVE = Path("tools/research/av_bs1_primary_h_review_token.json")
P1_PREREG_DOC_RELATIVE = Path(
    "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md"
)
TOKEN_BINDING_FIELDS = (
    "p1_fixture_sha256",
    "p1_runner_sha256",
    "p1_static_test_sha256",
    "p1_preregistration_doc_sha256",
    "h2_p1_resource_guard_policy_sha256",
    "p0_preregistration_commit",
    "p0_fixture_sha256",
    "p0_runner_sha256",
    "p0_static_test_sha256",
    "p0_preregistration_doc_sha256",
    "p0_manifest_payload_sha256",
    "p0_assembly_payload_sha256",
    "p0_resource_payload_sha256",
    "h1_fixture_sha256",
    "h1_runner_sha256",
    "h1_artifact_file_sha256",
    "h1_result_payload_sha256",
    "h1_numerical_payload_sha256",
    "h1_resource_report_sha256",
    "h1_numeric_view_schema",
    "h1_numeric_view_sha256",
    "h1_operator_y_sha256",
    "h1_operator_y_reverse_sha256",
    "h1_consumed_tombstone_file_sha256",
    "h1_consumed_token_sha256",
    "h1_status",
    "h1_Y_mode_floor_S",
)
H1_ARTIFACT_PATH = Path(__file__).resolve().parents[2] / H1_ARTIFACT_RELATIVE
H1_TOMBSTONE_PATH = Path(__file__).resolve().parents[2] / H1_TOMBSTONE_RELATIVE
_EXECUTION_PHASE = "preflight"

AvBsError = h1.AvBsError
canonical_bytes = h1.canonical_bytes


def _file_sha256(path: Path) -> str:
    return h1._file_sha256(path)


def _atomic_replace_json(path: Path, payload: Mapping[str, object]) -> None:
    """Durably replace a tracked token with canonical UTF-8 JSON on one volume."""
    path = path.resolve()
    path.parent.mkdir(parents=False, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name).resolve()
            if temporary.parent != path.parent:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "token replacement escaped its directory")
            stream.write(canonical_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except AvBsError:
        raise
    except OSError as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "atomic token consumption failed") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _wrapper(payload: Mapping[str, object]) -> Mapping[str, object]:
    return h1._wrapper(payload)


def _read_json(path: Path, label: str) -> Mapping[str, object]:
    return h1._read_json(path, label=label)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _json_int(value: object, label: str) -> int:
    return h1._json_int(value, label=label)


def _canonical_sha(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is not a SHA-256")
    return value


def _require_finite_pair(value: object, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is not a complex pair")
    try:
        pair = [float(value[0]), float(value[1])]
    except (TypeError, ValueError) as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is malformed") from error
    if not all(math.isfinite(item) for item in pair):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is non-finite")
    return pair


def _mode_view(numerical: Mapping[str, object], label: str) -> list[list[object]]:
    metrics = numerical.get("metrics")
    if not isinstance(metrics, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} metrics missing")
    modes = metrics.get("modes")
    if not isinstance(modes, list) or len(modes) != len(SIGNED_M9):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} signed M9 is incomplete")
    pairs: list[list[object]] = []
    for expected_mode, row in zip(SIGNED_M9, modes, strict=True):
        if not isinstance(row, Mapping) or type(row.get("m")) is not int:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} mode row invalid")
        if row["m"] != expected_mode:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} signed M9 order mismatch")
        pairs.append([expected_mode, _require_finite_pair(row.get("numeric_S"), f"{label} m={expected_mode}")])
    return pairs


def _canonical_mode_view(numerical: Mapping[str, object]) -> str:
    return _canonical_sha(_mode_view(numerical, "numerical"))


def _validated_wrapper(path: Path, label: str) -> Mapping[str, object]:
    wrapper = _read_json(path, label)
    payload = wrapper.get("payload")
    digest = wrapper.get("payload_sha256")
    if not isinstance(payload, Mapping) or not isinstance(digest, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} wrapper shape mismatch")
    if _canonical_sha(payload) != digest:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} checksum mismatch")
    return wrapper


def _validate_operator_blob(blob: object, expected_sha: str, label: str) -> None:
    if not isinstance(blob, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H1 {label} operator missing")
    if blob.get("sha256") != expected_sha or blob.get("shape") != [128, 128] or blob.get("dtype") != "<c16":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H1 {label} operator binding mismatch")
    encoded = blob.get("base64")
    if not isinstance(encoded, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H1 {label} operator payload missing")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H1 {label} base64 invalid") from error
    if len(raw) != 128 * 128 * 16 or sha256(raw).hexdigest() != expected_sha:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H1 {label} operator bytes mismatch")


def _validate_h2_operator_blob(blob: object, boundary_nodes: int, label: str) -> np.ndarray:
    if not isinstance(blob, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} operator missing")
    digest = _require_sha(blob.get("sha256"), f"H2 {label} operator")
    if blob.get("shape") != [boundary_nodes, boundary_nodes] or blob.get("dtype") != "<c16":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} operator shape/dtype mismatch")
    encoded = blob.get("base64")
    if not isinstance(encoded, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} operator payload missing")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} base64 invalid") from error
    if len(raw) != boundary_nodes * boundary_nodes * 16 or sha256(raw).hexdigest() != digest:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} operator bytes mismatch")
    values = np.frombuffer(raw, dtype="<c16").reshape((boundary_nodes, boundary_nodes)).copy()
    if np.any(~np.isfinite(values)):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} operator is non-finite")
    return values


def _validate_h2_modal_field_blob(
    blob: object, interior_nodes: int, label: str
) -> np.ndarray:
    if not isinstance(blob, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} field evidence missing")
    digest = _require_sha(blob.get("sha256"), f"H2 {label} fields")
    expected_shape = [interior_nodes, len(SIGNED_M9)]
    if blob.get("shape") != expected_shape or blob.get("dtype") != "<c16":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} field shape/dtype mismatch")
    encoded = blob.get("base64")
    if not isinstance(encoded, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} field payload missing")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} field base64 invalid") from error
    if len(raw) != interior_nodes * len(SIGNED_M9) * 16 or sha256(raw).hexdigest() != digest:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} field bytes mismatch")
    values = np.frombuffer(raw, dtype="<c16").reshape(expected_shape).copy()
    if np.any(~np.isfinite(values)):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {label} fields are non-finite")
    return values


def _finite_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is malformed") from error
    if not math.isfinite(number):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is non-finite")
    return number


def _roundoff_close(actual: float | complex, expected: float | complex, scale_floor: float = 1.0) -> bool:
    if not (np.isfinite(actual) and np.isfinite(expected)):
        return False
    tolerance = 4096.0 * np.finfo(float).eps * max(abs(actual), abs(expected), scale_floor)
    return bool(abs(actual - expected) <= tolerance)


def _validate_h1_artifact(path: Path) -> Mapping[str, object]:
    if not path.is_file() or _file_sha256(path) != H1_ARTIFACT_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 artifact hash mismatch")
    wrapper = _validated_wrapper(path, "H1 artifact")
    if wrapper.get("payload_sha256") != H1_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 artifact payload mismatch")
    payload = wrapper.get("payload")
    if not isinstance(payload, Mapping) or payload.get("schema") != h1.RESULT_SCHEMA:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 result schema mismatch")
    exact_scope = {
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "h",
        "status": H1_STATUS,
        "mandatory_stage_pass": True,
        "fine_analytic_pass": None,
        "mesh_convergence_pass": None,
        "final_circle_pass": None,
        "next_stage_authorized": False,
        "failure_codes": [],
        "numerical_payload_sha256": H1_NUMERICAL_SHA256,
        "resource_report_sha256": H1_RESOURCE_SHA256,
    }
    if any(payload.get(key) != expected for key, expected in exact_scope.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 result status binding mismatch")
    resource = payload.get("resource")
    resource_exact = {
        "schema": h1.RESOURCE_SCHEMA,
        "program": PROGRAM,
        "stage": "primary-h",
        "runner_sha256": H1_RUNNER_SHA256,
        "mandatory_resource_gate_pass": True,
        "child_exit_code": 0,
        "monitor_ok": True,
        "stop_reason": None,
    }
    if not isinstance(resource, Mapping) or any(
        resource.get(key) != expected for key, expected in resource_exact.items()
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 resource binding mismatch")
    if _json_int(resource.get("successful_tree_sample_count"), "H1 successful sample count") < 1:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 resource sample binding mismatch")
    numerical = payload.get("numerical")
    if not isinstance(numerical, Mapping) or _canonical_sha(numerical) != H1_NUMERICAL_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 numerical binding mismatch")
    if numerical.get("fixture_sha256") != H1_FIXTURE_SHA256 or numerical.get("runner_sha256") != H1_RUNNER_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 fixture/runner binding mismatch")
    operators = numerical.get("operators")
    if not isinstance(operators, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 operators missing")
    _validate_operator_blob(operators.get("Y"), H1_OPERATOR_Y_SHA256, "Y")
    _validate_operator_blob(operators.get("Y_reverse"), H1_OPERATOR_Y_REVERSE_SHA256, "Y_reverse")
    metrics = numerical.get("metrics")
    if not isinstance(metrics, Mapping) or metrics.get("signed_modes") != list(SIGNED_M9):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 metric mode binding mismatch")
    if metrics.get("Y_mode_floor_S") != H1_MODE_FLOOR_S:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 mode floor binding mismatch")
    if _canonical_mode_view(numerical) != H1_MODE_VIEW_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 mode-view binding mismatch")
    return payload


def _validate_h1_tombstone(path: Path) -> Mapping[str, object]:
    if not path.is_file() or _file_sha256(path) != H1_TOMBSTONE_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 consumed tombstone hash mismatch")
    tombstone = _read_json(path, "H1 consumed tombstone")
    exact = {
        "schema": "AV-BS1-review-token-consumed-v1",
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorization_state": "consumed",
        "fixture_sha256": H1_FIXTURE_SHA256,
        "runner_sha256": H1_RUNNER_SHA256,
        "consumed_artifact_sha256": H1_ARTIFACT_SHA256,
        "consumed_artifact_payload_sha256": H1_PAYLOAD_SHA256,
        "consumed_review_token_sha256": H1_CONSUMED_TOKEN_SHA256,
        "next_stage_authorized": False,
        "review_disposition": f"consumed_after_{H1_STATUS}",
    }
    if any(tombstone.get(key) != expected for key, expected in exact.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 consumed tombstone binding mismatch")
    return tombstone


def _h_to_h2_trend(h1_result: Mapping[str, object], h2_modes: list[Mapping[str, object]]) -> Mapping[str, object]:
    numerical = h1_result.get("numerical")
    metrics = numerical.get("metrics") if isinstance(numerical, Mapping) else None
    if not isinstance(metrics, Mapping) or metrics.get("Y_mode_floor_S") != H1_MODE_FLOOR_S:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 trend floor mismatch")
    previous_view = _mode_view(numerical, "H1") if isinstance(numerical, Mapping) else None
    h2_numerical = {"metrics": {"modes": h2_modes}}
    h2_view = _mode_view(h2_numerical, "H2")
    if previous_view is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 trend view missing")
    relative: list[float] = []
    eligible_phases: list[float] = []
    rows: list[Mapping[str, object]] = []
    for old_row, new_row in zip(previous_view, h2_view, strict=True):
        mode = int(old_row[0])
        before = complex(*old_row[1])
        after = complex(*new_row[1])
        floor = H1_MODE_FLOOR_S
        rel = abs(after - before) / max(abs(after), floor)
        eligible = abs(before) >= 10.0 * floor and abs(after) >= 10.0 * floor
        phase = abs(float(np.angle(after / before, deg=True))) if eligible else None
        relative.append(rel)
        if phase is not None:
            eligible_phases.append(phase)
        rows.append(
            {
                "m": mode,
                "h_numeric_S": old_row[1],
                "h2_numeric_S": new_row[1],
                "denominator_S": max(abs(after), floor),
                "relative_trend_only": rel,
                "phase_eligible": eligible,
                "phase_deg_trend_only": phase,
            }
        )
    return {
        "schema": TREND_SCHEMA,
        "Y_mode_floor_S": H1_MODE_FLOOR_S,
        "gate_applied": False,
        "authorization_effect": "none",
        "scope": "trend_only_no_h4_or_final_circle_claim",
        "h_view_sha256": _canonical_sha(previous_view),
        "h2_view_sha256": _canonical_sha(h2_view),
        "signed_modes": list(SIGNED_M9),
        "modes": rows,
        "rms_relative_trend_only": float(np.sqrt(np.mean(np.square(relative)))),
        "max_relative_trend_only": max(relative),
        "eligible_phase_modes": len(eligible_phases),
        "max_phase_deg_trend_only": max(eligible_phases) if eligible_phases else None,
        "trend_only": True,
        "gate_pass": None,
    }


def _p0_manifest() -> Mapping[str, object]:
    root = _repo_root()
    fixture_path = Path(__file__).with_name("av_bs1_boundary_schur_h2.py")
    runner_path = Path(__file__).with_name("run_av_bs1_h2_stage.ps1")
    test_path = root / "tests" / "test_research_av_bs1_boundary_schur_h2.py"
    doc_path = root / "docs" / "evaluation-research" / "T1_AV_BOUNDARY_SCHUR_H2_PREREG.md"
    expected_files = {
        fixture_path: P0_FIXTURE_SHA256,
        runner_path: P0_RUNNER_SHA256,
        test_path: P0_TEST_SHA256,
        doc_path: P0_DOC_SHA256,
    }
    if any(not path.is_file() or _file_sha256(path) != digest for path, digest in expected_files.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2-P0 dependency hash mismatch")
    p0_payload = p0.run_manifest()
    if _wrapper(p0_payload)["payload_sha256"] != P0_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2-P0 manifest payload mismatch")
    if _canonical_sha(p0_payload.get("assembly")) != P0_ASSEMBLY_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2-P0 assembly payload mismatch")
    if _canonical_sha(p0_payload.get("resource_preflight")) != P0_RESOURCE_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2-P0 resource payload mismatch")
    return p0_payload


def _guard_policy() -> Mapping[str, object]:
    return {
        "schema": "AV-BS1-h2-p1-resource-policy-v1",
        "poll_interval_ms": 100,
        "wall_stop_seconds": WALL_STOP_SECONDS,
        "tree_ws_stop_bytes": 4 * 1024**3,
        "tree_private_stop_bytes": 5 * 1024**3,
        "tree_commit_stop_bytes": 5 * 1024**3,
        "commit_headroom_floor_bytes": 2 * 1024**3,
        "available_physical_floor_bytes": int(1.5 * 1024**3),
        "successful_tree_sample_count_min": 1,
        "execution_tree_includes_runner": True,
        "one_factor_per_frequency": ONE_FACTOR_PER_FREQUENCY,
        "boundary_rhs_batch": BOUNDARY_RHS_BATCH,
    }


def _p1_paths() -> Mapping[str, Path]:
    root = _repo_root()
    return {
        "fixture": Path(__file__).resolve(),
        "runner": Path(__file__).with_name("run_av_bs1_h2_p1_stage.ps1"),
        "test": root / "tests" / "test_research_av_bs1_boundary_schur_h2_p1.py",
        "doc": root / P1_PREREG_DOC_RELATIVE,
    }


def _token_expected_bindings() -> Mapping[str, object]:
    paths = _p1_paths()
    bindings = {
        "p1_fixture_sha256": _file_sha256(paths["fixture"]),
        "p1_runner_sha256": _file_sha256(paths["runner"]),
        "p1_static_test_sha256": _file_sha256(paths["test"]),
        "p1_preregistration_doc_sha256": _file_sha256(paths["doc"]),
        "h2_p1_resource_guard_policy_sha256": _canonical_sha(_guard_policy()),
        "p0_preregistration_commit": P0_COMMIT,
        "p0_fixture_sha256": P0_FIXTURE_SHA256,
        "p0_runner_sha256": P0_RUNNER_SHA256,
        "p0_static_test_sha256": P0_TEST_SHA256,
        "p0_preregistration_doc_sha256": P0_DOC_SHA256,
        "p0_manifest_payload_sha256": P0_PAYLOAD_SHA256,
        "p0_assembly_payload_sha256": P0_ASSEMBLY_PAYLOAD_SHA256,
        "p0_resource_payload_sha256": P0_RESOURCE_PAYLOAD_SHA256,
        "h1_fixture_sha256": H1_FIXTURE_SHA256,
        "h1_runner_sha256": H1_RUNNER_SHA256,
        "h1_artifact_file_sha256": H1_ARTIFACT_SHA256,
        "h1_result_payload_sha256": H1_PAYLOAD_SHA256,
        "h1_numerical_payload_sha256": H1_NUMERICAL_SHA256,
        "h1_resource_report_sha256": H1_RESOURCE_SHA256,
        "h1_numeric_view_schema": "AV-BS1-h1-numeric-view-v1",
        "h1_numeric_view_sha256": H1_MODE_VIEW_SHA256,
        "h1_operator_y_sha256": H1_OPERATOR_Y_SHA256,
        "h1_operator_y_reverse_sha256": H1_OPERATOR_Y_REVERSE_SHA256,
        "h1_consumed_tombstone_file_sha256": H1_TOMBSTONE_SHA256,
        "h1_consumed_token_sha256": H1_CONSUMED_TOKEN_SHA256,
        "h1_status": H1_STATUS,
        "h1_Y_mode_floor_S": H1_MODE_FLOOR_S,
    }
    if tuple(bindings) != TOKEN_BINDING_FIELDS:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "token binding field order drift")
    return bindings


def _validate_checkout(token: Mapping[str, object], token_path: Path) -> str:
    try:
        root = _repo_root()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "merge-base", "--is-ancestor", P0_COMMIT, head], cwd=root, capture_output=True, text=True, check=True)
        prereg = token.get("p1_preregistration_commit")
        if not isinstance(prereg, str) or re.fullmatch(r"[0-9a-f]{40}", prereg) is None:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P1 preregistration commit is invalid")
        parent = subprocess.run(["git", "rev-parse", "HEAD^"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        if prereg != parent:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P1 preregistration is not the token-commit parent")
        subprocess.run(["git", "merge-base", "--is-ancestor", prereg, head], cwd=root, capture_output=True, text=True, check=True)
        completed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root,
                                   capture_output=True, text=True, check=True)
        relative = token_path.resolve().relative_to(root).as_posix()
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=root, capture_output=True, text=True, check=True)
        latest = subprocess.run(["git", "log", "-1", "--format=%H", "--", relative], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    except AvBsError:
        raise
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "clean research checkout proof failed") from error
    if completed.stdout.strip():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "research checkout is not clean")
    if latest != head:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token is not from current clean commit")
    return head


def _validate_review_token(path: Path) -> Mapping[str, object]:
    if not path.is_file():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token is missing")
    token = _read_json(path, "review token")
    try:
        bindings = _token_expected_bindings()
    except OSError as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "P1 preregistration dependency is missing") from error
    expected = {
        "schema": TOKEN_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorized_stage": "primary-h2",
        "authorization_state": "authorized",
        "uses_remaining": 1,
        "next_stage_authorized": False,
        "review_disposition": "approved_h2_primary_only_stage_evaluable",
        "review_scope": "authorize_h2_one_mesh_after_clean_p1_checkout",
        **bindings,
    }
    for key, value in expected.items():
        if token.get(key) != value:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"review token {key} mismatch")
    token_id, reviewed = token.get("review_token_id"), token.get("reviewed_utc")
    if not isinstance(token_id, str) or re.fullmatch(r"[0-9a-f]{32}", token_id) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token ID is invalid")
    if not isinstance(reviewed, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", reviewed) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token timestamp is invalid")
    if token.get("independent_audits") != ["Sol mathematical and fail-closed contract review", "Terra Windows runner and process-tree safety review", "Luna schema, resource, and bounded-test review"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token audit evidence mismatch")
    prereg = token.get("p1_preregistration_commit")
    if not isinstance(prereg, str) or re.fullmatch(r"[0-9a-f]{40}", prereg) is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token P1 preregistration commit is invalid")
    return token


def _review_binding_sha256(token: Mapping[str, object]) -> str:
    view = {key: token.get(key) for key in TOKEN_BINDING_FIELDS}
    view.update(
        {
            "p1_preregistration_commit": token.get("p1_preregistration_commit"),
            "review_token_id": token.get("review_token_id"),
        }
    )
    return _canonical_sha(view)


def _validate_claim(
    path: Path,
    token: Mapping[str, object],
    token_path: Path,
    git_head: str,
) -> Mapping[str, object]:
    root = _repo_root()
    token_id = token["review_token_id"]
    expected_relative = Path("validation-output") / "av-bs1" / "claims" / f"{token_id}.json"
    try:
        actual_relative = path.resolve().relative_to(root)
    except ValueError as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim path escaped research checkout") from error
    if actual_relative != expected_relative:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim path is not canonical")
    claim = _read_json(path, "one-use claim")
    exact = {
        "schema": CLAIM_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "primary-h2",
        "claim_relative_path": expected_relative.as_posix(),
        "token_id": token_id,
        "review_token_sha256": _file_sha256(token_path),
        "review_binding_sha256": _review_binding_sha256(token),
        "fixture_sha256": token["p1_fixture_sha256"],
        "runner_sha256": token["p1_runner_sha256"],
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "git_head": git_head,
    }
    if any(claim.get(key) != expected for key, expected in exact.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "one-use claim binding mismatch")
    _require_sha(claim.get("preflight_payload_sha256"), "claim preflight payload")
    claimed = claim.get("claimed_utc")
    if not isinstance(claimed, str) or not claimed.endswith("Z"):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claim timestamp is invalid")
    return claim


def _validate_guard(
    path: Path,
    nonce: str,
    token: Mapping[str, object],
    claim_path: Path,
    token_path: Path,
    git_head: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    claim = _validate_claim(claim_path, token, token_path, git_head)
    guard = _read_json(path, "resource guard")
    policy = _guard_policy()
    expected = {
        "schema": GUARD_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "primary-h2",
        "nonce": nonce,
        "monitor_ok": True,
        "runner_sha256": token["p1_runner_sha256"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _file_sha256(token_path),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "resource_guard_policy_sha256": _canonical_sha(policy),
        **{key: policy[key] for key in (
            "poll_interval_ms",
            "wall_stop_seconds",
            "tree_ws_stop_bytes",
            "tree_private_stop_bytes",
            "tree_commit_stop_bytes",
            "commit_headroom_floor_bytes",
            "available_physical_floor_bytes",
        )},
    }
    for key, value in expected.items():
        if guard.get(key) != value:
            raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"guard {key} mismatch")
    if _json_int(guard.get("parent_pid"), "guard parent_pid") != os.getppid():
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard parent PID mismatch")
    if _json_int(guard.get("baseline_commit_headroom_bytes"), "guard baseline commit headroom") < policy["commit_headroom_floor_bytes"]:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard baseline commit headroom is too small")
    if _json_int(guard.get("baseline_available_physical_bytes"), "guard baseline available physical") < policy["available_physical_floor_bytes"]:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard baseline available physical memory is too small")
    return guard, claim


def run_manifest() -> Mapping[str, object]:
    p0_payload = _p0_manifest()
    return {
        "schema": FIXTURE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "manifest",
        "status": "preregistered_H2_P1_token_missing_no_solve",
        "authorization_state": "not_authorized",
        "dependencies": {
            "p0_preregistration_commit": P0_COMMIT,
            "p0_fixture_sha256": P0_FIXTURE_SHA256,
            "p0_runner_sha256": P0_RUNNER_SHA256,
            "p0_static_test_sha256": P0_TEST_SHA256,
            "p0_preregistration_doc_sha256": P0_DOC_SHA256,
            "p0_manifest_payload_sha256": P0_PAYLOAD_SHA256,
            "p0_assembly_payload_sha256": P0_ASSEMBLY_PAYLOAD_SHA256,
            "p0_resource_payload_sha256": P0_RESOURCE_PAYLOAD_SHA256,
            "h1_fixture_sha256": H1_FIXTURE_SHA256,
            "h1_runner_sha256": H1_RUNNER_SHA256,
            "h1_artifact_file_sha256": H1_ARTIFACT_SHA256,
            "h1_result_payload_sha256": H1_PAYLOAD_SHA256,
            "h1_numerical_payload_sha256": H1_NUMERICAL_SHA256,
            "h1_resource_report_sha256": H1_RESOURCE_SHA256,
            "h1_numeric_view_sha256": H1_MODE_VIEW_SHA256,
            "h1_mode_view_sha256": H1_MODE_VIEW_SHA256,
            "h1_operator_y_sha256": H1_OPERATOR_Y_SHA256,
            "h1_operator_y_reverse_sha256": H1_OPERATOR_Y_REVERSE_SHA256,
            "h1_consumed_tombstone_file_sha256": H1_TOMBSTONE_SHA256,
            "h1_consumed_token_sha256": H1_CONSUMED_TOKEN_SHA256,
            "h1_status": H1_STATUS,
            "h1_Y_mode_floor_S": H1_MODE_FLOOR_S,
            "h2_p1_resource_guard_policy_sha256": _canonical_sha(_guard_policy()),
        },
        "h2_p0_mesh": p0_payload["mesh"],
        "h2_p0_partition": p0_payload["partition"],
        "h2_p0_lineage": p0_payload["cyclic_lineage"],
        "h2_p0_assembly": p0_payload["assembly"],
        "h2_p0_resource_preflight": p0_payload["resource_preflight"],
        "execution_contract": {
            "one_factor_per_frequency": ONE_FACTOR_PER_FREQUENCY,
            "boundary_rhs_batch": BOUNDARY_RHS_BATCH,
            "resource_scope": "full_process_tree_peak_working_set_private_commit_page_fault_mapped_residency",
            **{key: value for key, value in _guard_policy().items() if key not in (
                "schema", "successful_tree_sample_count_min", "execution_tree_includes_runner",
                "one_factor_per_frequency", "boundary_rhs_batch",
            )},
            "signed_modes": list(SIGNED_M9),
            "trend_schema": TREND_SCHEMA,
            "trend_only": "h_to_h2_signed_M9_no_fine_mesh_or_final_circle_claim",
            "volume_power_certificate_schema": "AV-BS1-h2-M9-volume-power-v1",
            "volume_power_interior_field_bytes": (
                p0_payload["partition"]["interior_nodes"] * len(SIGNED_M9) * 16
            ),
            "volume_power_finalizer_recomputation": "frozen_volume_mass_M9_quadratic_integral",
        },
        "finalizer_contract": {
            "resource_schema": RESOURCE_SCHEMA,
            "numerical_schema": NUMERICAL_SCHEMA,
            "failure_schema": FAILURE_SCHEMA,
            "result_schema": RESULT_SCHEMA,
            "claim_schema": CLAIM_SCHEMA,
            "token_tombstone_schema": TOMBSTONE_SCHEMA,
            "post_attempt_tombstone_required": True,
            "next_stage_authorized": False,
            "mandatory_result_fields": ["child_exit_code", "mandatory_resource_gate_pass",
                                        "numerical_stage_pass", "h_to_h2_trend"],
        },
        "factorization_performed": False,
        "physics_solve_performed": False,
        "available_solve_stages": [],
        "unavailable_stages": ["primary-h", "primary-h2", "primary-h4", "withheld", "EQ0"],
    }


def run_preflight_primary_h2(review_path: Path) -> Mapping[str, object]:
    token = _validate_review_token(review_path)
    git_head = _validate_checkout(token, review_path)
    h1_result = _validate_h1_artifact(H1_ARTIFACT_PATH)
    _validate_h1_tombstone(H1_TOMBSTONE_PATH)
    p0_payload = _p0_manifest()
    manifest = run_manifest()
    return {
        **manifest,
        "stage": "preflight-primary-h2",
        "status": "preflight_passed_not_claimed_no_solve",
        "authorization_state": "authorized_preflight_only",
        "git_head": git_head,
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _file_sha256(review_path),
        "review_binding_sha256": _review_binding_sha256(token),
        "h1_status": h1_result["status"],
        "p0_manifest_payload_sha256": _wrapper(p0_payload)["payload_sha256"],
        "preflight_pass": True,
        "factorization_performed": False,
        "physics_solve_performed": False,
        "available_solve_stages": ["primary-h2_after_atomic_claim"],
    }


def run_primary_h2(
    guard_path: Path,
    guard_nonce: str,
    review_path: Path,
    claim_path: Path,
) -> Mapping[str, object]:
    """Run exactly one reviewed H2 mesh; no H4/final-circle inference is allowed."""
    global _EXECUTION_PHASE
    _EXECUTION_PHASE = "preflight"
    token = _validate_review_token(review_path)
    git_head = _validate_checkout(token, review_path)
    h1_artifact = _validate_h1_artifact(H1_ARTIFACT_PATH)
    _validate_h1_tombstone(H1_TOMBSTONE_PATH)
    p0_payload = _p0_manifest()
    guard, claim = _validate_guard(
        guard_path, guard_nonce, token, claim_path, review_path, git_head
    )
    if _wrapper(run_preflight_primary_h2(review_path))["payload_sha256"] != claim["preflight_payload_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claimed preflight payload mismatch")
    _EXECUTION_PHASE = "claimed_pre_factorization"
    runtime = h1._runtime()
    if runtime != h1.EXPECTED_RUNTIME:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H2 runtime mismatch")

    nodes, triangles, midpoint = p0.seed_mesh_h2()
    manifest = p0.mesh_manifest(nodes, triangles)
    if manifest != p0_payload["mesh"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H2 mesh payload mismatch")
    tags, lineage = p0._h2_tags(nodes, midpoint)
    if lineage != p0_payload["cyclic_lineage"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H2 cyclic lineage mismatch")
    raw_stiffness, mass = h1._assemble_volume(nodes, triangles)
    stiffness, canonical = p0._canonicalize_h2(
        raw_stiffness, nodes, triangles, tags, manifest
    )
    _, boundary = h1.edge_data(triangles)
    interior, gamma, gamma_map = p0._partition(len(nodes), boundary)
    trace_mass = h1._assemble_trace_mass(nodes, boundary, gamma_map)
    actual_assembly = {
        "raw_stiffness_nnz": int(raw_stiffness.nnz),
        "raw_stiffness_sha256": h1._sparse_sha256(raw_stiffness),
        "canonical_stiffness_nnz": int(stiffness.nnz),
        "canonical_stiffness_sha256": h1._sparse_sha256(stiffness),
        "mass_nnz": int(mass.nnz),
        "mass_sha256": h1._sparse_sha256(mass),
        "trace_mass_nnz": int(trace_mass.nnz),
        "trace_mass_sha256": h1._sparse_sha256(trace_mass),
    }
    for key, value in actual_assembly.items():
        if value != p0_payload["assembly"].get(key):
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"H2 actual {key} mismatch")
    for key, value in canonical.items():
        if value != p0_payload["assembly"].get(key):
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"H2 canonical {key} mismatch")
    sparse_bytes = sum(
        int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
        for matrix in (stiffness, mass, trace_mass)
    )
    preflight = p0_payload["resource_preflight"]
    if sparse_bytes != preflight["sparse_base_arrays_bytes"]:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H2 sparse preflight byte mismatch")
    independently_computed = h1._resource_preflight(len(interior), len(gamma), sparse_bytes)
    byte_keys = (
        "sparse_base_arrays_bytes",
        "sparse_copy_allowance_bytes",
        "one_factor_dense_upper_bytes",
        "two_interior_extensions_bytes",
        "boundary_dense_work_bytes",
        "batch_rhs_work_bytes",
        "raw_total_bytes",
        "total_with_25pct_margin_bytes",
    )
    if any(independently_computed[key] != preflight[key] for key in byte_keys):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H2 resource preflight mismatch")

    omega = 2.0 * math.pi * h1.FREQUENCY_HZ
    full_background = csc_matrix(stiffness, dtype=np.complex128)
    full_conductor = csc_matrix(
        stiffness.astype(np.complex128) + 1j * omega * h1.SIGMA_S_PER_M * mass
    )
    full_background.sum_duplicates(); full_background.sort_indices()
    full_conductor.sum_duplicates(); full_conductor.sort_indices()
    transpose = {
        "K_full": h1._sparse_transpose_relative(stiffness),
        "M_full": h1._sparse_transpose_relative(mass),
        "M_Gamma": h1._sparse_transpose_relative(trace_mass),
        "A_background_full": h1._sparse_transpose_relative(full_background),
        "A_conductor_full": h1._sparse_transpose_relative(full_conductor),
    }
    if max(transpose.values()) > h1.MAX_ASSEMBLY_TRANSPOSE:
        raise AvBsError("BLOCKED_AV_BS_RECIPROCITY", "H2 full assembly transpose gate failed")
    background_matrix = full_background[interior, :][:, interior].tocsc()
    background_rhs = csc_matrix(-full_background[interior, :][:, gamma])
    conductor_matrix = full_conductor[interior, :][:, interior].tocsc()
    conductor_rhs = csc_matrix(-full_conductor[interior, :][:, gamma])
    m_ii = mass[interior, :][:, interior].tocsc()
    m_ig = mass[interior, :][:, gamma].tocsc()
    m_gi = mass[gamma, :][:, interior].tocsc()
    m_gg = mass[gamma, :][:, gamma].tocsc()
    _EXECUTION_PHASE = "background_factorization_attempted"
    x_background, background_certificate = h1._solve_extensions(
        "background", background_matrix, background_rhs
    )
    _EXECUTION_PHASE = "background_extension_completed"
    _EXECUTION_PHASE = "conductor_factorization_attempted"
    x_conductor, conductor_certificate = h1._solve_extensions(
        "conductor", conductor_matrix, conductor_rhs
    )
    _EXECUTION_PHASE = "extensions_completed"
    y, y_reverse = h1._assemble_cross_operators(
        x_background, x_conductor, m_ii, m_ig, m_gi, m_gg
    )
    _EXECUTION_PHASE = "physics_assembled"
    y_floor = max(1.0e-18, 1.0e-10 * float(np.max(np.abs(y))))
    denominator = max(float(np.linalg.norm(y)), len(gamma) * y_floor)
    reverse = float(np.linalg.norm(y_reverse - y.T) / denominator)
    reciprocity = float(np.linalg.norm(y - y.T) / denominator)
    if reverse > h1.MAX_REVERSE or reciprocity > h1.MAX_RECIPROCITY:
        raise AvBsError(
            "BLOCKED_AV_BS_RECIPROCITY",
            f"H2 reverse={reverse:.6e}, reciprocity={reciprocity:.6e}",
        )
    hermitian = 0.5 * (y + y.conj().T)
    eig_min = float(np.linalg.eigvalsh(hermitian)[0])
    passivity_tol = max(y_floor, 1.0e-9 * float(np.linalg.norm(y, 2)))
    if not math.isfinite(eig_min) or eig_min < -passivity_tol:
        raise AvBsError("BLOCKED_AV_BS_PASSIVITY", "H2 raw hidden negative mode")
    modes, power, degeneracy, mode_floor = h1._modal_metrics(
        nodes, gamma, trace_mass, y, x_conductor, m_ii, m_ig, m_gi, m_gg
    )
    boundary_xy = np.asarray(nodes, dtype=np.float64)[gamma]
    boundary_theta = np.mod(
        np.arctan2(boundary_xy[:, 1], boundary_xy[:, 0]), 2.0 * math.pi
    )
    boundary_mode_vectors = np.column_stack(
        [np.exp(1j * mode * boundary_theta) for mode in SIGNED_M9]
    )
    interior_modal_fields = x_conductor @ boundary_mode_vectors
    conductor_norm_inf = float(
        max(np.sum(abs(conductor_matrix).tocsr(), axis=1).flat)
    )
    modal_field_residuals: list[float] = []
    for mode_index in range(len(SIGNED_M9)):
        interior_field = interior_modal_fields[:, mode_index]
        modal_rhs = np.asarray(
            conductor_rhs @ boundary_mode_vectors[:, mode_index]
        ).reshape(-1)
        modal_residual = np.asarray(
            conductor_matrix @ interior_field - modal_rhs
        ).reshape(-1)
        residual_denominator = (
            conductor_norm_inf * float(np.max(np.abs(interior_field)))
            + float(np.max(np.abs(modal_rhs)))
        )
        if not math.isfinite(residual_denominator) or residual_denominator <= 0.0:
            raise AvBsError("BLOCKED_AV_BS_SOLVE", "H2 M9 field residual denominator invalid")
        modal_field_residuals.append(
            float(np.max(np.abs(modal_residual))) / residual_denominator
        )
    modal_field_residual_max = max(modal_field_residuals)
    if not math.isfinite(modal_field_residual_max) or modal_field_residual_max > h1.MAX_BACKWARD:
        raise AvBsError(
            "BLOCKED_AV_BS_SOLVE",
            f"H2 M9 field backward residual={modal_field_residual_max:.6e}",
        )
    if mode_floor != H1_MODE_FLOOR_S:
        raise AvBsError("BLOCKED_AV_BS_ANALYTIC", "H2 mode floor drifted from H1")
    trend = _h_to_h2_trend(h1_artifact, modes)
    passed = bool(
        background_certificate["backward_residual_max"] <= h1.MAX_BACKWARD
        and conductor_certificate["backward_residual_max"] <= h1.MAX_BACKWARD
        and background_certificate["kappa1_u"] <= h1.MAX_KAPPA_U
        and conductor_certificate["kappa1_u"] <= h1.MAX_KAPPA_U
        and power <= h1.MAX_POWER
    )
    if not passed:
        raise AvBsError("BLOCKED_AV_BS_SOLVE", "mandatory H2 numerical expression is false")
    return {
        "schema": NUMERICAL_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "h2",
        "git_head": git_head,
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "runner_sha256": token["p1_runner_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _file_sha256(review_path),
        "review_binding_sha256": _review_binding_sha256(token),
        "guard_contract_sha256": _file_sha256(guard_path),
        "resource_guard_policy_sha256": _canonical_sha(_guard_policy()),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "bindings": dict(_token_expected_bindings()),
        "runtime": list(runtime),
        "geometry": {
            "radius_m": h1.RADIUS_M,
            "frequency_hz": h1.FREQUENCY_HZ,
            "sigma_s_per_m": h1.SIGMA_S_PER_M,
            "mu_h_per_m": h1.MU0_H_PER_M,
            "phasor": "exp(+j omega t)",
        },
        "mesh": {
            **manifest,
            "interior_nodes": len(interior),
            "boundary_nodes": len(gamma),
            **actual_assembly,
            "cyclic_lineage": lineage,
        },
        "assembly": {
            "operator_order": ["background", "conductor"],
            "canonicalization": canonical,
            "one_factor_resident": True,
            "batch_size": BOUNDARY_RHS_BATCH,
            "extension_storage": "interior_only_boundary_identity_implicit",
            "extension_shapes": [list(x_background.shape), list(x_conductor.shape)],
            "raw_transpose_relative": transpose,
        },
        "solves": {
            "background": background_certificate,
            "conductor": conductor_certificate,
        },
        "operators": {"Y": h1._array_blob(y), "Y_reverse": h1._array_blob(y_reverse)},
        "power_certificate": {
            "schema": "AV-BS1-h2-M9-volume-power-v1",
            "signed_modes": list(SIGNED_M9),
            "source_conductor_extension_sha256": conductor_certificate["extension_sha256"],
            "integration": "0.5*sigma*Re(uH(MIIu+MIg*v)+vH(MGIu+MGGv))",
            "residual": "maxabs(ApII*u+ApIG*v)/(norminf(ApII)*maxabs(u)+maxabs(ApIG*v))",
            "field_backward_residuals": modal_field_residuals,
            "field_backward_residual_max": modal_field_residual_max,
            "interior_modal_fields": h1._array_blob(interior_modal_fields),
        },
        "metrics": {
            "signed_modes": list(SIGNED_M9),
            "Y_floor_S_m": y_floor,
            "Y_mode_floor_S": mode_floor,
            "reverse_order_relative": reverse,
            "raw_reciprocity_relative": reciprocity,
            "min_hermitian_eigenvalue_S_m": eig_min,
            "passivity_tolerance_S_m": passivity_tol,
            "power_mismatch_max": power,
            "m_plus_minus_relative_trend_only": degeneracy,
            "modes": modes,
            "h_to_h2_trend": trend,
            "fine_analytic_pass": None,
            "mesh_convergence_pass": None,
            "final_circle_pass": None,
        },
        "resource_preflight": preflight,
        "factorization_performed": True,
        "physics_solve_performed": True,
        "external_resource_monitor_pending": True,
        "numerical_stage_pass": True,
        "mandatory_stage_pass": None,
        "authorization_state": "claimed",
        "next_stage_authorized": False,
        "status": "numerical_pass_pending_external_resource_review",
        "failure_codes": [],
    }


def _resource_gate(resource: Mapping[str, object]) -> tuple[bool, list[str]]:
    policy = _guard_policy()
    failures: list[str] = []
    peak = resource.get("peak")
    baseline = resource.get("baseline")
    final_system = resource.get("final_system")
    thresholds = resource.get("thresholds")
    if not all(isinstance(value, Mapping) for value in (peak, baseline, final_system, thresholds)):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource metric shape mismatch")
    expected_thresholds = {
        key: policy[key]
        for key in (
            "tree_ws_stop_bytes",
            "tree_private_stop_bytes",
            "tree_commit_stop_bytes",
            "commit_headroom_floor_bytes",
            "available_physical_floor_bytes",
        )
    }
    if any(thresholds.get(key) != value for key, value in expected_thresholds.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource threshold contract mismatch")
    try:
        wall = float(resource.get("wall_seconds"))
    except (TypeError, ValueError) as error:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource wall time is invalid") from error
    if not math.isfinite(wall) or wall < 0.0 or wall > WALL_STOP_SECONDS:
        failures.append("wall_time")
    checks = {
        "sample_count": _json_int(resource.get("successful_tree_sample_count"), "resource sample count") >= 1,
        "tree_working_set": _json_int(peak.get("tree_working_set_bytes"), "peak tree WS") <= policy["tree_ws_stop_bytes"],
        "tree_private_commit": _json_int(peak.get("tree_private_commit_bytes"), "peak tree private") <= policy["tree_private_stop_bytes"],
        "tree_committed_pagefile": _json_int(peak.get("tree_committed_pagefile_bytes"), "peak tree commit") <= policy["tree_commit_stop_bytes"],
        "minimum_commit_headroom": _json_int(peak.get("system_commit_headroom_min_bytes"), "minimum commit headroom") >= policy["commit_headroom_floor_bytes"],
        "minimum_available_physical": _json_int(peak.get("available_physical_min_bytes"), "minimum available physical") >= policy["available_physical_floor_bytes"],
        "final_commit_headroom": _json_int(final_system.get("commit_headroom_bytes"), "final commit headroom") >= policy["commit_headroom_floor_bytes"],
        "final_available_physical": _json_int(final_system.get("available_physical_bytes"), "final available physical") >= policy["available_physical_floor_bytes"],
    }
    failures.extend(key for key, passed in checks.items() if not passed)
    if resource.get("monitor_ok") is not True:
        failures.append("monitor")
    if resource.get("monitor_error") is not None:
        failures.append("monitor_error")
    if resource.get("stop_reason") is not None:
        failures.append("stop_reason")
    if resource.get("mandatory_resource_gate_pass") is not True:
        failures.append("runner_resource_predicate")
    return not failures, failures


def _validate_h2_numerical_envelope(
    numerical: Mapping[str, object],
    token: Mapping[str, object],
    review_token_sha256: str,
    git_head: str,
    claim_relative_path: str,
    claim_sha256: str,
    guard_contract_sha256: str,
    preflight_payload_sha256: str,
) -> None:
    expected = {
        "schema": NUMERICAL_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "h2",
        "git_head": git_head,
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "runner_sha256": token["p1_runner_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": review_token_sha256,
        "review_binding_sha256": _review_binding_sha256(token),
        "guard_contract_sha256": guard_contract_sha256,
        "resource_guard_policy_sha256": _canonical_sha(_guard_policy()),
        "claim_relative_path": claim_relative_path,
        "claim_sha256": claim_sha256,
        "preflight_payload_sha256": preflight_payload_sha256,
        "bindings": dict(_token_expected_bindings()),
        "factorization_performed": True,
        "physics_solve_performed": True,
        "external_resource_monitor_pending": True,
        "numerical_stage_pass": True,
        "mandatory_stage_pass": None,
        "authorization_state": "claimed",
        "next_stage_authorized": False,
        "status": "numerical_pass_pending_external_resource_review",
        "failure_codes": [],
    }
    if any(numerical.get(key) != value for key, value in expected.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "numerical lineage/status mismatch")


def _reconstruct_h2_finalizer_context(
    p0_payload: Mapping[str, object],
) -> Mapping[str, object]:
    nodes, triangles, midpoint = p0.seed_mesh_h2()
    _, boundary_edges = h1.edge_data(triangles)
    interior, gamma, gamma_map = p0._partition(len(nodes), boundary_edges)
    trace_mass = h1._assemble_trace_mass(nodes, boundary_edges, gamma_map)
    if h1._sparse_sha256(trace_mass) != p0_payload["assembly"]["trace_mass_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H2 finalizer trace-mass mismatch")
    raw_stiffness, volume_mass = h1._assemble_volume(nodes, triangles)
    if (
        int(volume_mass.nnz) != p0_payload["assembly"]["mass_nnz"]
        or h1._sparse_sha256(volume_mass) != p0_payload["assembly"]["mass_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H2 finalizer volume-mass mismatch")
    tags, _ = p0._h2_tags(nodes, midpoint)
    stiffness, _ = p0._canonicalize_h2(
        raw_stiffness, nodes, triangles, tags, p0_payload["mesh"]
    )
    if (
        int(stiffness.nnz) != p0_payload["assembly"]["canonical_stiffness_nnz"]
        or h1._sparse_sha256(stiffness)
        != p0_payload["assembly"]["canonical_stiffness_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H2 finalizer stiffness mismatch")
    omega = 2.0 * math.pi * h1.FREQUENCY_HZ
    full_conductor = csc_matrix(
        stiffness.astype(np.complex128)
        + 1j * omega * h1.SIGMA_S_PER_M * volume_mass
    )
    full_conductor.sum_duplicates()
    full_conductor.sort_indices()
    return {
        "nodes": nodes,
        "interior": interior,
        "gamma": gamma,
        "trace_mass": trace_mass,
        "mass_ii": volume_mass[interior, :][:, interior].tocsc(),
        "mass_ig": volume_mass[interior, :][:, gamma].tocsc(),
        "mass_gi": volume_mass[gamma, :][:, interior].tocsc(),
        "mass_gg": volume_mass[gamma, :][:, gamma].tocsc(),
        "conductor_ii": full_conductor[interior, :][:, interior].tocsc(),
        "conductor_ig": full_conductor[interior, :][:, gamma].tocsc(),
    }


def _validate_h2_success_body(
    numerical: Mapping[str, object],
    p0_payload: Mapping[str, object],
    h1_result: Mapping[str, object],
) -> None:
    if numerical.get("runtime") != list(h1.EXPECTED_RUNTIME):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 runtime evidence mismatch")
    expected_geometry = {
        "radius_m": h1.RADIUS_M,
        "frequency_hz": h1.FREQUENCY_HZ,
        "sigma_s_per_m": h1.SIGMA_S_PER_M,
        "mu_h_per_m": h1.MU0_H_PER_M,
        "phasor": "exp(+j omega t)",
    }
    if numerical.get("geometry") != expected_geometry:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 geometry evidence mismatch")

    mesh = numerical.get("mesh")
    expected_mesh = {
        **p0_payload["mesh"],
        "interior_nodes": p0_payload["partition"]["interior_nodes"],
        "boundary_nodes": p0_payload["partition"]["boundary_nodes"],
        "raw_stiffness_nnz": p0_payload["assembly"]["raw_stiffness_nnz"],
        "raw_stiffness_sha256": p0_payload["assembly"]["raw_stiffness_sha256"],
        "canonical_stiffness_nnz": p0_payload["assembly"]["canonical_stiffness_nnz"],
        "canonical_stiffness_sha256": p0_payload["assembly"]["canonical_stiffness_sha256"],
        "mass_nnz": p0_payload["assembly"]["mass_nnz"],
        "mass_sha256": p0_payload["assembly"]["mass_sha256"],
        "trace_mass_nnz": p0_payload["assembly"]["trace_mass_nnz"],
        "trace_mass_sha256": p0_payload["assembly"]["trace_mass_sha256"],
        "cyclic_lineage": p0_payload["cyclic_lineage"],
    }
    if mesh != expected_mesh:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 numerical mesh binding mismatch")
    if numerical.get("resource_preflight") != p0_payload["resource_preflight"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 numerical preflight binding mismatch")

    assembly = numerical.get("assembly")
    if not isinstance(assembly, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 assembly evidence missing")
    expected_assembly_scope = {
        "operator_order": ["background", "conductor"],
        "one_factor_resident": True,
        "batch_size": BOUNDARY_RHS_BATCH,
        "extension_storage": "interior_only_boundary_identity_implicit",
        "extension_shapes": [
            [p0_payload["partition"]["interior_nodes"], p0_payload["partition"]["boundary_nodes"]],
            [p0_payload["partition"]["interior_nodes"], p0_payload["partition"]["boundary_nodes"]],
        ],
    }
    if any(assembly.get(key) != expected for key, expected in expected_assembly_scope.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 assembly execution contract mismatch")
    excluded = {
        "boundary_edges_sha256",
        "mass_nnz",
        "mass_sha256",
        "trace_mass_nnz",
        "trace_mass_sha256",
        "trace_support_sha256",
    }
    expected_canonical = {
        key: value for key, value in p0_payload["assembly"].items() if key not in excluded
    }
    if assembly.get("canonicalization") != expected_canonical:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 canonical assembly certificate mismatch")
    transpose = assembly.get("raw_transpose_relative")
    transpose_keys = {
        "K_full", "M_full", "M_Gamma", "A_background_full", "A_conductor_full"
    }
    if not isinstance(transpose, Mapping) or set(transpose) != transpose_keys:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 transpose certificate shape mismatch")
    if any(
        _finite_number(transpose[key], f"H2 transpose {key}") < 0.0
        or float(transpose[key]) > h1.MAX_ASSEMBLY_TRANSPOSE
        for key in transpose_keys
    ):
        raise AvBsError("BLOCKED_AV_BS_RECIPROCITY", "H2 transpose certificate failed")

    solves = numerical.get("solves")
    if not isinstance(solves, Mapping) or set(solves) != {"background", "conductor"}:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 solve certificates missing")
    interior_nodes = p0_payload["partition"]["interior_nodes"]
    boundary_nodes = p0_payload["partition"]["boundary_nodes"]
    for name in ("background", "conductor"):
        certificate = solves.get(name)
        if not isinstance(certificate, Mapping):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {name} solve certificate missing")
        exact = {
            "name": name,
            "raw_shape": [interior_nodes, interior_nodes],
            "rhs_count": boundary_nodes,
            "batch_size": BOUNDARY_RHS_BATCH,
            "permc_spec": "COLAMD",
            "superlu_equil": False,
            "diag_pivot_thresh": 1.0,
            "onenormest_t": 4,
            "onenormest_itmax": 10,
            "onenormest_seeds": [1729, 2718],
        }
        if any(certificate.get(key) != expected for key, expected in exact.items()):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {name} solve contract mismatch")
        residual = _finite_number(certificate.get("backward_residual_max"), f"H2 {name} residual")
        kappa = _finite_number(certificate.get("kappa1_u"), f"H2 {name} kappa1u")
        if residual < 0.0 or residual > h1.MAX_BACKWARD or kappa < 0.0 or kappa > h1.MAX_KAPPA_U:
            raise AvBsError("BLOCKED_AV_BS_SOLVE", f"H2 {name} solve gate failed")
        for key in ("L_nnz", "U_nnz", "factor_bytes_lower_estimate", "raw_nnz"):
            if _json_int(certificate.get(key), f"H2 {name} {key}") <= 0:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 {name} {key} is not positive")
        for key in (
            "extension_sha256", "row_scale_sha256", "column_scale_sha256",
            "perm_r_sha256", "perm_c_sha256",
        ):
            _require_sha(certificate.get(key), f"H2 {name} {key}")

    operators = numerical.get("operators")
    if not isinstance(operators, Mapping) or set(operators) != {"Y", "Y_reverse"}:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 operator evidence missing")
    y = _validate_h2_operator_blob(operators["Y"], boundary_nodes, "Y")
    y_reverse = _validate_h2_operator_blob(operators["Y_reverse"], boundary_nodes, "Y_reverse")
    power_certificate = numerical.get("power_certificate")
    if not isinstance(power_certificate, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 volume-power certificate missing")
    expected_power_certificate = {
        "schema": "AV-BS1-h2-M9-volume-power-v1",
        "signed_modes": list(SIGNED_M9),
        "source_conductor_extension_sha256": solves["conductor"]["extension_sha256"],
        "integration": "0.5*sigma*Re(uH(MIIu+MIg*v)+vH(MGIu+MGGv))",
        "residual": "maxabs(ApII*u+ApIG*v)/(norminf(ApII)*maxabs(u)+maxabs(ApIG*v))",
    }
    if set(power_certificate) != {
        *expected_power_certificate,
        "interior_modal_fields",
        "field_backward_residuals",
        "field_backward_residual_max",
    } or any(
        power_certificate.get(key) != expected
        for key, expected in expected_power_certificate.items()
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 volume-power certificate binding mismatch")
    interior_modal_fields = _validate_h2_modal_field_blob(
        power_certificate.get("interior_modal_fields"), interior_nodes, "M9 interior"
    )
    claimed_field_residuals = power_certificate.get("field_backward_residuals")
    if not isinstance(claimed_field_residuals, list) or len(claimed_field_residuals) != len(SIGNED_M9):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 M9 field residual list mismatch")

    metrics = numerical.get("metrics")
    if not isinstance(metrics, Mapping) or metrics.get("signed_modes") != list(SIGNED_M9):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 metric mode evidence missing")
    modes = metrics.get("modes")
    y_floor = max(1.0e-18, 1.0e-10 * float(np.max(np.abs(y))))
    denominator = max(float(np.linalg.norm(y)), boundary_nodes * y_floor)
    reverse = float(np.linalg.norm(y_reverse - y.T) / denominator)
    reciprocity = float(np.linalg.norm(y - y.T) / denominator)
    hermitian = 0.5 * (y + y.conj().T)
    eig_min = float(np.linalg.eigvalsh(hermitian)[0])
    passivity_tol = max(y_floor, 1.0e-9 * float(np.linalg.norm(y, 2)))
    if metrics.get("Y_floor_S_m") != y_floor:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 operator floor mismatch")
    if not _roundoff_close(
        _finite_number(metrics.get("reverse_order_relative"), "H2 reverse-order residual"), reverse
    ) or not _roundoff_close(
        _finite_number(metrics.get("raw_reciprocity_relative"), "H2 reciprocity residual"), reciprocity
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 recomputed reciprocity evidence mismatch")
    if not _roundoff_close(
        _finite_number(metrics.get("min_hermitian_eigenvalue_S_m"), "H2 passivity eigenvalue"),
        eig_min,
        max(abs(eig_min), 1.0e-18),
    ) or not _roundoff_close(
        _finite_number(metrics.get("passivity_tolerance_S_m"), "H2 passivity tolerance"),
        passivity_tol,
        max(abs(passivity_tol), 1.0e-18),
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 recomputed passivity evidence mismatch")
    if reverse > h1.MAX_REVERSE or reciprocity > h1.MAX_RECIPROCITY:
        raise AvBsError("BLOCKED_AV_BS_RECIPROCITY", "H2 operator reciprocity gate failed")
    if not math.isfinite(eig_min) or eig_min < -passivity_tol:
        raise AvBsError("BLOCKED_AV_BS_PASSIVITY", "H2 passivity certificate failed")

    context = _reconstruct_h2_finalizer_context(p0_payload)
    nodes = context["nodes"]
    gamma = context["gamma"]
    trace_mass = context["trace_mass"]
    mass_ii = context["mass_ii"]
    mass_ig = context["mass_ig"]
    mass_gi = context["mass_gi"]
    mass_gg = context["mass_gg"]
    conductor_ii = context["conductor_ii"]
    conductor_ig = context["conductor_ig"]
    conductor_norm_inf = float(
        max(np.sum(abs(conductor_ii).tocsr(), axis=1).flat)
    )
    xy = np.asarray(nodes, dtype=np.float64)[gamma]
    theta = np.mod(np.arctan2(xy[:, 1], xy[:, 0]), 2.0 * math.pi)
    targets = {mode: h1._analytic_target(mode) for mode in SIGNED_M9}
    mode_floor = max(1.0e-12, 1.0e-10 * max(abs(value) for value in targets.values()))
    if mode_floor != H1_MODE_FLOOR_S or metrics.get("Y_mode_floor_S") != mode_floor:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 mode floor mismatch")
    if not isinstance(modes, list) or len(modes) != len(SIGNED_M9):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 signed-mode rows missing")
    values: dict[int, complex] = {}
    power_errors: list[float] = []
    field_residuals: list[float] = []
    expected_mode_keys = {
        "m", "numeric_S", "analytic_S", "analytic_relative_trend_only",
        "analytic_phase_deg_trend_only", "trace_mass_denominator_m",
        "boundary_power_W_per_m", "volume_power_W_per_m", "power_mismatch",
    }
    for mode_index, (expected_mode, row) in enumerate(
        zip(SIGNED_M9, modes, strict=True)
    ):
        if not isinstance(row, Mapping) or set(row) != expected_mode_keys or row.get("m") != expected_mode:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 signed-mode row shape mismatch")
        vector = np.exp(1j * expected_mode * theta)
        trace_denominator = complex(np.vdot(vector, trace_mass @ vector))
        trace_scale = max(float(abs(trace_denominator.real)), 1.0e-300)
        if (
            not np.isfinite(trace_denominator)
            or trace_denominator.real <= 0.0
            or abs(trace_denominator.imag) > 64.0 * np.finfo(float).eps * trace_scale
        ):
            raise AvBsError("BLOCKED_AV_BS_ANALYTIC", f"H2 mode {expected_mode} trace denominator invalid")
        numeric = complex(np.vdot(vector, y @ vector) / trace_denominator.real)
        target = targets[expected_mode]
        analytic_relative = abs(numeric - target) / max(abs(target), mode_floor)
        analytic_phase = (
            float(abs(np.angle(numeric / target, deg=True)))
            if abs(numeric) >= 10.0 * mode_floor and abs(target) >= 10.0 * mode_floor
            else None
        )
        boundary_power = 0.5 * float(np.real(np.vdot(vector, y @ vector)))
        interior_field = interior_modal_fields[:, mode_index]
        conductor_boundary = np.asarray(conductor_ig @ vector).reshape(-1)
        field_equation_residual = np.asarray(
            conductor_ii @ interior_field + conductor_boundary
        ).reshape(-1)
        field_residual_denominator = (
            conductor_norm_inf * float(np.max(np.abs(interior_field)))
            + float(np.max(np.abs(conductor_boundary)))
        )
        if (
            not math.isfinite(field_residual_denominator)
            or field_residual_denominator <= 0.0
        ):
            raise AvBsError(
                "BLOCKED_AV_BS_SOLVE",
                f"H2 mode {expected_mode} field residual denominator invalid",
            )
        field_residual = (
            float(np.max(np.abs(field_equation_residual)))
            / field_residual_denominator
        )
        claimed_field_residual = _finite_number(
            claimed_field_residuals[mode_index],
            f"H2 mode {expected_mode} field backward residual",
        )
        if not _roundoff_close(claimed_field_residual, field_residual):
            raise AvBsError(
                "BLOCKED_AV_BS_RESULT_SCHEMA",
                f"H2 mode {expected_mode} field residual evidence mismatch",
            )
        if field_residual < 0.0 or field_residual > h1.MAX_BACKWARD:
            raise AvBsError(
                "BLOCKED_AV_BS_SOLVE",
                f"H2 mode {expected_mode} field backward residual failed",
            )
        mass_i = mass_ii @ interior_field + mass_ig @ vector
        mass_g = mass_gi @ interior_field + mass_gg @ vector
        volume_power = 0.5 * h1.SIGMA_S_PER_M * float(
            np.real(np.vdot(interior_field, mass_i) + np.vdot(vector, mass_g))
        )
        claimed_volume_power = _finite_number(
            row.get("volume_power_W_per_m"), f"H2 mode {expected_mode} volume power"
        )
        power_denominator = max(abs(volume_power), abs(boundary_power), 1.0e-18)
        power_error = abs(boundary_power - volume_power) / power_denominator
        claimed_numeric = complex(*_require_finite_pair(row.get("numeric_S"), f"H2 mode {expected_mode} numeric"))
        claimed_target = complex(*_require_finite_pair(row.get("analytic_S"), f"H2 mode {expected_mode} analytic"))
        claimed_relative = _finite_number(
            row.get("analytic_relative_trend_only"), f"H2 mode {expected_mode} analytic trend"
        )
        claimed_phase = row.get("analytic_phase_deg_trend_only")
        phase_matches = (
            claimed_phase is None and analytic_phase is None
        ) or (
            claimed_phase is not None
            and analytic_phase is not None
            and _roundoff_close(_finite_number(claimed_phase, f"H2 mode {expected_mode} phase"), analytic_phase)
        )
        if not all((
            _roundoff_close(claimed_numeric, numeric, mode_floor),
            _roundoff_close(claimed_target, target, mode_floor),
            _roundoff_close(claimed_relative, analytic_relative),
            phase_matches,
            _roundoff_close(
                _finite_number(row.get("trace_mass_denominator_m"), f"H2 mode {expected_mode} trace denominator"),
                trace_denominator.real,
                max(abs(trace_denominator.real), 1.0e-18),
            ),
            _roundoff_close(
                _finite_number(row.get("boundary_power_W_per_m"), f"H2 mode {expected_mode} boundary power"),
                boundary_power,
                max(abs(boundary_power), 1.0e-18),
            ),
            _roundoff_close(
                claimed_volume_power,
                volume_power,
                max(abs(volume_power), 1.0e-18),
            ),
            _roundoff_close(
                _finite_number(row.get("power_mismatch"), f"H2 mode {expected_mode} power mismatch"),
                power_error,
            ),
        )):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 mode {expected_mode} recomputation mismatch")
        if volume_power < -1.0e-18 or power_error > h1.MAX_POWER:
            raise AvBsError("BLOCKED_AV_BS_POWER", f"H2 mode {expected_mode} power gate failed")
        values[expected_mode] = numeric
        power_errors.append(power_error)
        field_residuals.append(field_residual)
    maximum_power = max(power_errors)
    maximum_field_residual = max(field_residuals)
    degeneracy = max(
        abs(values[mode] - values[-mode])
        / max(abs(values[mode]), abs(values[-mode]), mode_floor)
        for mode in range(1, 5)
    )
    if not _roundoff_close(
        _finite_number(metrics.get("power_mismatch_max"), "H2 maximum power mismatch"), maximum_power
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 maximum power evidence mismatch")
    if not _roundoff_close(
        _finite_number(
            power_certificate.get("field_backward_residual_max"),
            "H2 maximum M9 field backward residual",
        ),
        maximum_field_residual,
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 maximum field residual evidence mismatch")
    if not _roundoff_close(
        _finite_number(metrics.get("m_plus_minus_relative_trend_only"), "H2 signed-mode degeneracy"), degeneracy
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 signed-mode degeneracy mismatch")
    if metrics.get("h_to_h2_trend") != _h_to_h2_trend(h1_result, modes):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 trend certificate mismatch")
    if any(metrics.get(key) is not None for key in (
        "fine_analytic_pass", "mesh_convergence_pass", "final_circle_pass"
    )):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "future H2 gates are not null")


def _validate_child_failure_payload(payload: Mapping[str, object]) -> list[str]:
    exact = {
        "schema": FAILURE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "primary-h2",
        "mandatory_stage_pass": False,
        "next_stage_authorized": False,
    }
    if any(payload.get(key) != expected for key, expected in exact.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure scope mismatch")
    codes = payload.get("failure_codes")
    if (
        not isinstance(codes, list)
        or not codes
        or any(type(code) is not str or code not in ALLOWED_FAILURE_CODES for code in codes)
        or len(codes) != len(set(codes))
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure-code schema mismatch")
    if payload.get("status") != codes[0]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure status mismatch")
    if not isinstance(payload.get("detail"), str) or not payload["detail"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure detail missing")
    authorization = payload.get("authorization_state")
    attempted = payload.get("factorization_attempted")
    performed = payload.get("factorization_performed")
    physics = payload.get("physics_solve_performed")
    if authorization not in {"not_authorized", "claimed_attempt_failed"} or type(attempted) is not bool:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure provenance shape mismatch")
    if performed not in {False, True, None} or physics not in {False, True, None}:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure performed-state mismatch")
    if authorization == "not_authorized" and (attempted or performed is not False or physics is not False):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "unauthorized child failure provenance mismatch")
    if authorization == "claimed_attempt_failed":
        valid_claimed = (
            (not attempted and performed is False and physics is False)
            or (attempted and performed is None and physics is None)
            or (attempted and performed is True and physics is True)
        )
        if not valid_claimed:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "claimed child failure provenance mismatch")
    return list(dict.fromkeys(codes))


def _validate_finalizer_failure_payload(payload: Mapping[str, object]) -> list[str]:
    exact = {
        "schema": FAILURE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "finalize-primary-h2",
        "mandatory_stage_pass": False,
        "next_stage_authorized": False,
        "authorization_state": "claimed_attempt_failed",
        "factorization_attempted": None,
        "factorization_performed": None,
        "physics_solve_performed": None,
    }
    if any(payload.get(key) != expected for key, expected in exact.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer failure provenance mismatch")
    codes = payload.get("failure_codes")
    if (
        not isinstance(codes, list)
        or not codes
        or any(type(code) is not str or code not in ALLOWED_FAILURE_CODES for code in codes)
        or len(codes) != len(set(codes))
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer failure-code schema mismatch")
    if payload.get("status") != codes[0]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer failure status mismatch")
    if not isinstance(payload.get("detail"), str) or not payload["detail"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer failure detail missing")
    return list(dict.fromkeys(codes))


def finalize_primary_h2(
    resource_path: Path | None,
    child_stdout: Path | None,
    review_path: Path,
    guard_path: Path | None,
    guard_nonce: str | None,
) -> Mapping[str, object]:
    token = _validate_review_token(review_path)
    git_head = _validate_checkout(token, review_path)
    h1_result = _validate_h1_artifact(H1_ARTIFACT_PATH)
    _validate_h1_tombstone(H1_TOMBSTONE_PATH)
    p0_payload = _p0_manifest()
    if resource_path is None or guard_path is None or guard_nonce is None:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer inputs are incomplete")
    resource = _read_json(resource_path, "resource report")
    exact_resource = {
        "schema": RESOURCE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "primary-h2",
        "runner_sha256": token["p1_runner_sha256"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": _file_sha256(review_path),
        "resource_guard_policy_sha256": _canonical_sha(_guard_policy()),
        "wall_stop_seconds": WALL_STOP_SECONDS,
        "execution_tree_includes_runner": True,
    }
    if any(resource.get(key) != expected for key, expected in exact_resource.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource binding mismatch")
    claim_relative = resource.get("claim_relative_path")
    if not isinstance(claim_relative, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource claim path missing")
    claim_path = _repo_root() / Path(claim_relative)
    guard, claim = _validate_guard(
        guard_path, guard_nonce, token, claim_path, review_path, git_head
    )
    if _wrapper(run_preflight_primary_h2(review_path))["payload_sha256"] != claim["preflight_payload_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer preflight payload mismatch")
    if resource.get("claim_sha256") != _file_sha256(claim_path):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource claim checksum mismatch")
    if resource.get("preflight_payload_sha256") != claim["preflight_payload_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource preflight binding mismatch")
    if resource.get("guard_contract_sha256") != _file_sha256(guard_path):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource guard checksum mismatch")
    if guard.get("claim_sha256") != resource.get("claim_sha256"):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "guard/resource claim mismatch")
    child_exit = _json_int(resource.get("child_exit_code"), "resource child exit code")
    resource_pass, resource_failures = _resource_gate(resource)

    numerical_wrapper: Mapping[str, object] | None = None
    numerical_payload: Mapping[str, object] | None = None
    child_failure_codes: list[str] = []
    schema_exit_mismatch = False
    stdout_sha = resource.get("child_stdout_sha256")
    if child_stdout is not None and child_stdout.is_file():
        if stdout_sha != _file_sha256(child_stdout):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child stdout checksum mismatch")
        numerical_wrapper = _validated_wrapper(child_stdout, "numerical result")
        numerical_payload = numerical_wrapper["payload"]
        schema = numerical_payload.get("schema")
        if schema == FAILURE_SCHEMA:
            child_failure_codes = _validate_child_failure_payload(numerical_payload)
            schema_exit_mismatch = child_exit != 2
        elif schema == NUMERICAL_SCHEMA:
            _validate_h2_numerical_envelope(
                numerical_payload,
                token,
                _file_sha256(review_path),
                git_head,
                claim_relative,
                resource["claim_sha256"],
                resource["guard_contract_sha256"],
                claim["preflight_payload_sha256"],
            )
            _validate_h2_success_body(numerical_payload, p0_payload, h1_result)
            schema_exit_mismatch = child_exit != 0
        else:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child numerical schema mismatch")
    else:
        if stdout_sha is not None:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource references missing child stdout")
        schema_exit_mismatch = child_exit not in (2,)

    numerical_pass = bool(
        numerical_payload is not None
        and numerical_payload.get("schema") == NUMERICAL_SCHEMA
        and numerical_payload.get("numerical_stage_pass") is True
    )
    mandatory = bool(resource_pass and numerical_pass and child_exit == 0 and not schema_exit_mismatch)
    failure_codes = list(child_failure_codes)
    if schema_exit_mismatch and "BLOCKED_AV_BS_RESULT_SCHEMA" not in failure_codes:
        failure_codes.append("BLOCKED_AV_BS_RESULT_SCHEMA")
    if not resource_pass and "BLOCKED_AV_BS_RESOURCE" not in failure_codes:
        failure_codes.append("BLOCKED_AV_BS_RESOURCE")
    if not mandatory and not failure_codes:
        failure_codes.append("BLOCKED_AV_BS_SOLVE")
    status = "passed_AV_BS_h2_stage_only_pending_h4_preregistration" if mandatory else (
        "BLOCKED_AV_BS_RESOURCE" if not resource_pass else failure_codes[0]
    )
    return _wrapper(
        {
            "schema": RESULT_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": "h2",
            "git_head": git_head,
            "p1_preregistration_commit": token["p1_preregistration_commit"],
            "review_token_id": token["review_token_id"],
            "review_token_sha256": _file_sha256(review_path),
            "authorization_state": "claimed_attempt_complete_pending_tombstone",
            "token_consumption_required": True,
            "claim_relative_path": claim_relative,
            "claim_sha256": resource["claim_sha256"],
            "bindings": dict(_token_expected_bindings()),
            "numerical": numerical_payload,
            "numerical_payload_sha256": None if numerical_wrapper is None else numerical_wrapper["payload_sha256"],
            "resource": resource,
            "resource_report_sha256": _file_sha256(resource_path),
            "resource_gate_recheck_failures": resource_failures,
            "factorization_performed": None if numerical_payload is None else numerical_payload.get("factorization_performed"),
            "physics_solve_performed": None if numerical_payload is None else numerical_payload.get("physics_solve_performed"),
            "mandatory_stage_pass": mandatory,
            "fine_analytic_pass": None,
            "mesh_convergence_pass": None,
            "final_circle_pass": None,
            "next_stage_authorized": False,
            "status": status,
            "failure_codes": failure_codes,
        }
    )


def _validate_consumed_pass_result(
    result: Mapping[str, object],
    resource: Mapping[str, object],
    resource_sha256: str,
    token: Mapping[str, object],
    original_token_sha256: str,
    claim: Mapping[str, object],
    claim_path: Path,
    guard_sha256: str | None,
    git_head: str,
    p0_payload: Mapping[str, object],
    h1_result: Mapping[str, object],
) -> None:
    exact = {
        "schema": RESULT_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "h2",
        "git_head": git_head,
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": original_token_sha256,
        "authorization_state": "claimed_attempt_complete_pending_tombstone",
        "token_consumption_required": True,
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _file_sha256(claim_path),
        "bindings": dict(_token_expected_bindings()),
        "factorization_performed": True,
        "physics_solve_performed": True,
        "mandatory_stage_pass": True,
        "fine_analytic_pass": None,
        "mesh_convergence_pass": None,
        "final_circle_pass": None,
        "next_stage_authorized": False,
        "status": "passed_AV_BS_h2_stage_only_pending_h4_preregistration",
        "failure_codes": [],
        "resource_report_sha256": resource_sha256,
        "resource_gate_recheck_failures": [],
    }
    if any(result.get(key) != expected for key, expected in exact.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed pass result binding mismatch")
    if result.get("resource") != resource:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed pass embedded resource mismatch")
    numerical = result.get("numerical")
    if not isinstance(numerical, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed pass numerical payload missing")
    if result.get("numerical_payload_sha256") != _canonical_sha(numerical):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed pass numerical checksum mismatch")
    _validate_h2_numerical_envelope(
        numerical,
        token,
        original_token_sha256,
        git_head,
        claim["claim_relative_path"],
        _file_sha256(claim_path),
        guard_sha256,
        claim["preflight_payload_sha256"],
    )
    _validate_h2_success_body(numerical, p0_payload, h1_result)
    expected_resource = {
        "schema": RESOURCE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "primary-h2",
        "runner_sha256": token["p1_runner_sha256"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": original_token_sha256,
        "resource_guard_policy_sha256": _canonical_sha(_guard_policy()),
        "guard_contract_sha256": guard_sha256,
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "child_exit_code": 0,
        "mandatory_resource_gate_pass": True,
    }
    if any(resource.get(key) != expected for key, expected in expected_resource.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed pass resource binding mismatch")
    resource_pass, failures = _resource_gate(resource)
    if not resource_pass or failures:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "consumed pass resource gate failed")


def _validate_consumed_failure_result(
    result: Mapping[str, object],
    resource: Mapping[str, object],
    resource_sha256: str,
    token: Mapping[str, object],
    original_token_sha256: str,
    claim: Mapping[str, object],
    claim_path: Path,
    guard_sha256: str | None,
    git_head: str,
    p0_payload: Mapping[str, object],
    h1_result: Mapping[str, object],
) -> list[str]:
    exact = {
        "schema": RESULT_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "h2",
        "git_head": git_head,
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": original_token_sha256,
        "authorization_state": "claimed_attempt_complete_pending_tombstone",
        "token_consumption_required": True,
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _file_sha256(claim_path),
        "bindings": dict(_token_expected_bindings()),
        "mandatory_stage_pass": False,
        "fine_analytic_pass": None,
        "mesh_convergence_pass": None,
        "final_circle_pass": None,
        "next_stage_authorized": False,
        "resource_report_sha256": resource_sha256,
    }
    if any(result.get(key) != expected for key, expected in exact.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure result binding mismatch")
    if result.get("resource") != resource:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure embedded resource mismatch")
    expected_resource = {
        "schema": RESOURCE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "primary-h2",
        "runner_sha256": token["p1_runner_sha256"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": original_token_sha256,
        "resource_guard_policy_sha256": _canonical_sha(_guard_policy()),
        "guard_contract_sha256": guard_sha256,
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
    }
    if any(resource.get(key) != expected for key, expected in expected_resource.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure resource binding mismatch")
    child_exit = _json_int(resource.get("child_exit_code"), "consumed failure child exit")
    resource_pass, resource_failures = _resource_gate(resource)
    if result.get("resource_gate_recheck_failures") != resource_failures:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure resource recheck mismatch")

    numerical = result.get("numerical")
    numerical_sha = result.get("numerical_payload_sha256")
    child_codes: list[str] = []
    schema_exit_mismatch = False
    if isinstance(numerical, Mapping):
        if numerical_sha != _canonical_sha(numerical):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure numerical checksum mismatch")
        if numerical.get("schema") == FAILURE_SCHEMA:
            child_codes = _validate_child_failure_payload(numerical)
            schema_exit_mismatch = child_exit != 2
        elif numerical.get("schema") == NUMERICAL_SCHEMA:
            _validate_h2_numerical_envelope(
                numerical,
                token,
                original_token_sha256,
                git_head,
                claim["claim_relative_path"],
                _file_sha256(claim_path),
                guard_sha256,
                claim["preflight_payload_sha256"],
            )
            _validate_h2_success_body(numerical, p0_payload, h1_result)
            schema_exit_mismatch = child_exit != 0
        else:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure numerical schema mismatch")
        if result.get("factorization_performed") != numerical.get("factorization_performed") or result.get(
            "physics_solve_performed"
        ) != numerical.get("physics_solve_performed"):
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure execution provenance mismatch")
    else:
        if numerical is not None or numerical_sha is not None:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure missing numerical evidence mismatch")
        if result.get("factorization_performed") is not None or result.get("physics_solve_performed") is not None:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure absent-child provenance mismatch")
        schema_exit_mismatch = child_exit != 2

    expected_codes = list(child_codes)
    if schema_exit_mismatch:
        expected_codes.append("BLOCKED_AV_BS_RESULT_SCHEMA")
    if not resource_pass:
        expected_codes.append("BLOCKED_AV_BS_RESOURCE")
    if not expected_codes:
        expected_codes.append("BLOCKED_AV_BS_SOLVE")
    expected_codes = list(dict.fromkeys(expected_codes))
    status = "BLOCKED_AV_BS_RESOURCE" if not resource_pass else expected_codes[0]
    if result.get("failure_codes") != expected_codes or result.get("status") != status:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed failure status/code mismatch")
    return expected_codes


def consume_primary_h2_token(
    review_path: Path,
    claim_path: Path,
    guard_path: Path,
    guard_nonce: str,
    attempt_status: str,
    result_path: Path | None = None,
    resource_path: Path | None = None,
) -> Mapping[str, object]:
    """Irreversibly consume the active token after a child process was launched."""
    allowed_statuses = {
        "completed_pass",
        "completed_failure",
        "resource_stop",
        "finalizer_failure",
        "runner_exception",
    }
    if attempt_status not in allowed_statuses:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "token consumption status is invalid")
    token = _validate_review_token(review_path)
    git_head = _validate_checkout(token, review_path)
    _validate_h1_tombstone(H1_TOMBSTONE_PATH)
    p0_payload = _p0_manifest()
    h1_result = _validate_h1_artifact(H1_ARTIFACT_PATH)
    claim = _validate_claim(claim_path, token, review_path, git_head)
    evidence_errors: list[str] = []
    failure_codes: list[str] = []

    def record_evidence(label: str, error: AvBsError) -> None:
        evidence_errors.append(f"{label}:{error.detail}")
        failure_codes.append(error.code)

    guard_valid = False
    guard_sha256 = _file_sha256(guard_path) if guard_path.is_file() else None
    try:
        _validate_guard(
            guard_path, guard_nonce, token, claim_path, review_path, git_head
        )
        guard_valid = True
    except AvBsError as error:
        record_evidence("guard", error)
    original_token_sha256 = _file_sha256(review_path)

    result_sha256: str | None = None
    result_payload_sha256: str | None = None
    reported_result_status: str | None = None
    reported_mandatory_stage_pass: bool | None = None
    factorization_performed: bool | None = None
    physics_solve_performed: bool | None = None
    result_payload: Mapping[str, object] | None = None
    reported_failure_codes: list[str] = []
    result_evidence_valid = False
    if result_path is not None and result_path.is_file():
        result_sha256 = _file_sha256(result_path)
        try:
            result_wrapper = _validated_wrapper(result_path, "H2 final result")
            result_payload = result_wrapper["payload"]
            schema = result_payload.get("schema")
            if schema not in {RESULT_SCHEMA, FAILURE_SCHEMA}:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed result schema mismatch")
            result_payload_sha256 = result_wrapper["payload_sha256"]
            reported_result_status = result_payload.get("status")
            reported_mandatory_stage_pass = result_payload.get("mandatory_stage_pass")
            factorization_performed = result_payload.get("factorization_performed")
            physics_solve_performed = result_payload.get("physics_solve_performed")
            if schema == FAILURE_SCHEMA:
                if result_payload.get("stage") == "primary-h2":
                    codes = _validate_child_failure_payload(result_payload)
                elif result_payload.get("stage") == "finalize-primary-h2":
                    codes = _validate_finalizer_failure_payload(result_payload)
                else:
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        "consumed failure stage mismatch",
                    )
                reported_failure_codes = list(codes)
            else:
                codes = result_payload.get("failure_codes")
                if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA",
                        "consumed result failure codes invalid",
                    )
                reported_failure_codes = list(codes)
            if schema == FAILURE_SCHEMA:
                failure_codes.extend(codes)
            if attempt_status != "completed_pass" and reported_mandatory_stage_pass is True:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "token consumption result/status mismatch")
            result_evidence_valid = True
        except AvBsError as error:
            record_evidence("result", error)
    elif attempt_status == "completed_pass":
        record_evidence(
            "result",
            AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "passing attempt has no final result"),
        )

    resource_sha256: str | None = None
    resource: Mapping[str, object] | None = None
    resource_evidence_valid = False
    if resource_path is not None and resource_path.is_file():
        resource_sha256 = _file_sha256(resource_path)
        try:
            resource = _read_json(resource_path, "H2 resource report for token consumption")
            expected_resource = {
                "schema": RESOURCE_SCHEMA,
                "program": PROGRAM,
                "case_id": CASE_ID,
                "stage": "primary-h2",
                "runner_sha256": token["p1_runner_sha256"],
                "fixture_sha256": token["p1_fixture_sha256"],
                "review_token_id": token["review_token_id"],
                "review_token_sha256": original_token_sha256,
                "claim_relative_path": claim["claim_relative_path"],
                "claim_sha256": _file_sha256(claim_path),
                "guard_contract_sha256": guard_sha256,
            }
            if any(resource.get(key) != expected for key, expected in expected_resource.items()):
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed resource binding mismatch")
            resource_evidence_valid = True
        except AvBsError as error:
            record_evidence("resource", error)
    elif attempt_status == "completed_pass":
        record_evidence(
            "resource",
            AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "passing attempt has no resource report"),
        )

    consumption_validated_pass = False
    if attempt_status == "completed_pass":
        try:
            if not guard_valid or guard_sha256 is None:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "passing attempt guard evidence is invalid")
            if result_payload is None or resource is None or resource_sha256 is None:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "passing attempt evidence is incomplete")
            _validate_consumed_pass_result(
                result_payload,
                resource,
                resource_sha256,
                token,
                original_token_sha256,
                claim,
                claim_path,
                guard_sha256,
                git_head,
                p0_payload,
                h1_result,
            )
            consumption_validated_pass = True
        except AvBsError as error:
            record_evidence("pass", error)
    elif result_payload is not None and result_payload.get("schema") == RESULT_SCHEMA:
        try:
            if resource is None or resource_sha256 is None:
                raise AvBsError(
                    "BLOCKED_AV_BS_RESULT_SCHEMA",
                    "failed attempt result has no resource report",
                )
            failure_codes.extend(
                _validate_consumed_failure_result(
                    result_payload,
                    resource,
                    resource_sha256,
                    token,
                    original_token_sha256,
                    claim,
                    claim_path,
                    guard_sha256,
                    git_head,
                    p0_payload,
                    h1_result,
                )
            )
        except AvBsError as error:
            result_evidence_valid = False
            record_evidence("failure", error)

    failure_codes = list(dict.fromkeys(failure_codes))
    if consumption_validated_pass:
        mandatory_stage_pass = True
        result_status = "passed_AV_BS_h2_stage_only_pending_h4_preregistration"
        failure_codes = []
        effective_attempt_status = "completed_pass"
    else:
        mandatory_stage_pass = False
        effective_attempt_status = (
            "completed_invalid_evidence" if attempt_status == "completed_pass" else attempt_status
        )
        if not failure_codes:
            failure_codes.append(
                "BLOCKED_AV_BS_RESOURCE"
                if attempt_status == "resource_stop"
                else "BLOCKED_AV_BS_SOLVE"
                if attempt_status == "completed_failure"
                else "BLOCKED_AV_BS_RESULT_SCHEMA"
            )
        result_status = failure_codes[0]

    tombstone = {
        "schema": TOMBSTONE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorized_stage": "primary-h2",
        "authorization_state": "consumed",
        "uses_remaining": 0,
        "next_stage_authorized": False,
        "review_disposition": "consumed_after_primary_h2_child_launch",
        "review_scope": token["review_scope"],
        "consumed_review_token_id": token["review_token_id"],
        "consumed_review_token_sha256": original_token_sha256,
        "reviewed_utc": token["reviewed_utc"],
        "independent_audits": token["independent_audits"],
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "consumed_git_head": git_head,
        "review_binding_sha256": _review_binding_sha256(token),
        "bindings": dict(_token_expected_bindings()),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": _file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "guard_contract_sha256": guard_sha256,
        "guard_evidence_valid": guard_valid,
        "resource_guard_policy_sha256": token["h2_p1_resource_guard_policy_sha256"],
        "attempt_status": attempt_status,
        "effective_attempt_status": effective_attempt_status,
        "consumption_validated_pass": consumption_validated_pass,
        "consumed_result_file_sha256": result_sha256,
        "consumed_result_payload_sha256": result_payload_sha256,
        "consumed_resource_report_sha256": resource_sha256,
        "result_evidence_valid": result_evidence_valid,
        "resource_evidence_valid": resource_evidence_valid,
        "consumption_evidence_errors": evidence_errors,
        "reported_result_status": reported_result_status,
        "reported_mandatory_stage_pass": reported_mandatory_stage_pass,
        "reported_failure_codes": reported_failure_codes,
        "result_status": result_status,
        "mandatory_stage_pass": mandatory_stage_pass,
        "factorization_performed": factorization_performed,
        "physics_solve_performed": physics_solve_performed,
        "failure_codes": failure_codes,
        "consumed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _atomic_replace_json(review_path, tombstone)
    if _read_json(review_path, "H2 consumed tombstone") != tombstone:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone verification failed")
    return tombstone


def _failure(error: AvBsError, stage: str) -> Mapping[str, object]:
    if stage == "finalize-primary-h2":
        return _wrapper(
            {
                "schema": FAILURE_SCHEMA,
                "program": PROGRAM,
                "case_id": CASE_ID,
                "stage": stage,
                "mandatory_stage_pass": False,
                "next_stage_authorized": False,
                "status": error.code,
                "authorization_state": "claimed_attempt_failed",
                "failure_codes": [error.code],
                "detail": error.detail,
                "factorization_attempted": None,
                "factorization_performed": None,
                "physics_solve_performed": None,
            }
        )
    pre_execution = stage != "primary-h2" or _EXECUTION_PHASE == "preflight"
    claimed = stage == "primary-h2" and _EXECUTION_PHASE != "preflight"
    factorization_attempted = _EXECUTION_PHASE in {
        "background_factorization_attempted",
        "background_extension_completed",
        "conductor_factorization_attempted",
        "extensions_completed",
        "physics_assembled",
    }
    factorization_performed: bool | None
    physics_solve_performed: bool | None
    if pre_execution or _EXECUTION_PHASE == "claimed_pre_factorization":
        factorization_performed = False
        physics_solve_performed = False
    elif _EXECUTION_PHASE == "background_factorization_attempted":
        factorization_performed = None
        physics_solve_performed = None
    else:
        factorization_performed = True
        physics_solve_performed = True
    return _wrapper(
        {
            "schema": FAILURE_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": stage,
            "mandatory_stage_pass": False,
            "next_stage_authorized": False,
            "status": error.code,
            "authorization_state": "claimed_attempt_failed" if claimed else "not_authorized",
            "failure_codes": [error.code],
            "detail": error.detail,
            "factorization_attempted": factorization_attempted,
            "factorization_performed": factorization_performed,
            "physics_solve_performed": physics_solve_performed,
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AV-BS1 H2-P1 token-gated execution contract")
    parser.add_argument(
        "--stage",
        required=True,
        choices=(
            "manifest", "preflight-primary-h2", "primary-h2",
            "finalize-primary-h2", "consume-primary-h2-token",
        ),
    )
    parser.add_argument("--review-token", type=Path, default=REVIEW_TOKEN_PATH)
    parser.add_argument("--guard-contract", type=Path)
    parser.add_argument("--guard-nonce")
    parser.add_argument("--claim-file", type=Path)
    parser.add_argument("--resource-report", type=Path)
    parser.add_argument("--child-stdout", type=Path)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--attempt-status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.stage == "manifest":
            payload = run_manifest()
        elif args.stage == "preflight-primary-h2":
            payload = run_preflight_primary_h2(args.review_token.resolve())
        elif args.stage == "primary-h2":
            _validate_review_token(args.review_token.resolve())
            if args.guard_contract is None or args.guard_nonce is None or args.claim_file is None:
                raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard, claim, and review token are mandatory")
            payload = run_primary_h2(
                args.guard_contract.resolve(),
                args.guard_nonce,
                args.review_token.resolve(),
                args.claim_file.resolve(),
            )
        elif args.stage == "finalize-primary-h2":
            print(json.dumps(finalize_primary_h2(
                args.resource_report,
                args.child_stdout,
                args.review_token.resolve(),
                args.guard_contract,
                args.guard_nonce,
            ), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
            return 0
        elif args.stage == "consume-primary-h2-token":
            if args.claim_file is None or args.guard_contract is None or args.guard_nonce is None or args.attempt_status is None:
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "token consumption inputs are incomplete")
            payload = consume_primary_h2_token(
                args.review_token.resolve(),
                args.claim_file.resolve(),
                args.guard_contract.resolve(),
                args.guard_nonce,
                args.attempt_status,
                None if args.result_file is None else args.result_file.resolve(),
                None if args.resource_report is None else args.resource_report.resolve(),
            )
        else:
            payload = run_manifest()
        print(json.dumps(_wrapper(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0
    except AvBsError as error:
        print(json.dumps(_failure(error, args.stage), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
