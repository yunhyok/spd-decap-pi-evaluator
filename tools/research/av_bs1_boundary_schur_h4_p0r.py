#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.22.0 H4-P0R factor-pilot contract.

This file is deliberately manifest-only.  It reconstructs and hashes the two
H4 interior matrices that a later, separately reviewed one-use pilot may
factor.  It contains no factorization call, RHS, solve, extension, boundary
operator, modal response, or physics path.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Mapping

import numpy as np
from scipy.sparse import csc_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
import av_bs1_boundary_schur as h1
import av_bs1_boundary_schur_h4_p0 as p0


PROGRAM = h1.PROGRAM
CASE_ID = h1.CASE_ID
SCHEMA = "AV-BS1-h4-p0r-manifest-v1"
REVIEW_SCHEMA = "AV-BS1-h4-p0r-review-token-v1"
CLAIM_SCHEMA = "AV-BS1-h4-p0r-token-claim-v1"
GUARD_SCHEMA = "AV-BS1-h4-p0r-resource-guard-v1"
RESOURCE_SCHEMA = "AV-BS1-h4-p0r-resource-report-v1"
NUMERICAL_SCHEMA = "AV-BS1-h4-p0r-factor-report-v1"
RESULT_SCHEMA = "AV-BS1-h4-p0r-result-v1"
FAILURE_SCHEMA = "AV-BS1-h4-p0r-failure-v1"
TOMBSTONE_SCHEMA = "AV-BS1-h4-p0r-consumed-review-token-v1"
EXPECTED_MANIFEST_PAYLOAD_SHA256 = "eaab10df7fb1557490cc75db7e7ff9fca2881013b6a6fb13f02faea43ddf7023"
EXPECTED_MATRIX_CONTRACT_SHA256 = "89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46"
EXPECTED_RESOURCE_POLICY_SHA256 = "13df68af8b9008824c09653bdb32a56c618107858cdb71b987bf6f4375915680"

ROOT = Path(__file__).resolve().parents[2]
H4_P0_COMMIT = "8f40fe5696496edb2cb73086927f833ded5e0d5e"
H4_P0_PAYLOAD_SHA256 = "71f8e902322016541bd9302231fa9965d7dfff67cdce1135e16ee1011a2aa990"
H4_P0_BINDINGS: Mapping[str, str] = {
    "fixture_sha256": "331218882d2004d0d97e03378ae8af12b23cb4129a9c062e0592ee590a53e94b",
    "runner_sha256": "b45c907fb5300e46717f423c8512a3500c7db8b42b647101d64a8a11440116c9",
    "static_test_sha256": "1539ef4151b8ea416c9ee2f2bf71c1baebd4e83bb2d3684e050437fcb1def40c",
    "preregistration_doc_sha256": "419dfb85ff40a43f2a0c2b1143b8531b16c95402f0c0d454764cfd3f5c03e524",
    "mesh_payload_sha256": "4b2463c26b6e0cb3b0452b06a4ee6e372eed531f97740d1c908c9f734795f26d",
    "partition_payload_sha256": "bb45ac79f5e011bcb6fcedec7061695edf4525e0721caae7860ccd8c3507cb87",
    "lineage_payload_sha256": "ba21c8ba262488e8bb928cf6493fda8021bb5a2a4a0d5e8e1598f9f26436b3cb",
    "assembly_payload_sha256": "e9511977d30db8c4cf7ec2961a6df30d3f381fe45d651ec51a6c68e038eb311a",
    "resource_payload_sha256": "292e4da8d7970a0c8f35c0a4dd97fe330f032a9db58e510920cc4c31434c8a9b",
}
H4_P0_PATHS: Mapping[str, str] = {
    "fixture_sha256": "tools/research/av_bs1_boundary_schur_h4_p0.py",
    "runner_sha256": "tools/research/run_av_bs1_h4_p0_stage.ps1",
    "static_test_sha256": "tests/test_research_av_bs1_boundary_schur_h4_p0.py",
    "preregistration_doc_sha256": "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0_PREREG.md",
}

H2_ARTIFACT_FILE_SHA256 = "b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55"
H2_ARTIFACT_PAYLOAD_SHA256 = "5704f3feb5bb90b6e38f8ed3ec67fc690339e9b7f0c1722d40a977295e82593e"
H2_NUMERICAL_PAYLOAD_SHA256 = "bd2f6542a93e3a7adc62f4435540752651ddf1cff84226319b4aa5e8dbdc0be5"
H2_RESOURCE_REPORT_SHA256 = "b17468d7383ed5021a783ade4c3b7c1c5e21628580d5f6c298ef1b7b97b26bd2"
H2_TOMBSTONE_PATH = ROOT / "tools" / "research" / "av_bs1_h2_p1_review_token.json"
H2_TOMBSTONE_SHA256 = "81574c1099bdd140004940b7cb768a20298b153445c1b16b9e21de95011c48c2"

EXPECTED_MATRIX_INPUTS: Mapping[str, Mapping[str, object]] = {
    "K_II": {
        "dtype": "float64",
        "shape": [31489, 31489],
        "nnz": 204545,
        "sha256": "dddee397ee96ff0bd7acc321cd5755dd611f1e3d1f6bfcf9664c388d27184361",
        "sparse_array_bytes": 2580500,
        "transpose_relative": 0.0,
    },
    "M_II": {
        "dtype": "float64",
        "shape": [31489, 31489],
        "nnz": 219393,
        "sha256": "4dccc26f1a12bbab4a4ee863e509d317cb518075661867da4e0b6980f7dcc6c4",
        "sparse_array_bytes": 2758676,
        "transpose_relative": 0.0,
    },
    "A_background_II": {
        "dtype": "complex128",
        "shape": [31489, 31489],
        "nnz": 204545,
        "sha256": "8c099d2cb5947d1b72bd74f64329879c221b86c569b4100baa47dfea9aeff641",
        "sparse_array_bytes": 4216860,
        "transpose_relative": 0.0,
        "equilibrated_nnz": 204545,
        "equilibrated_sha256": "9216cc8938d9efc1e531a4fb301aca45547fad55f01dec57863986622327dd74",
        "equilibrated_sparse_array_bytes": 4216860,
        "row_scale_sha256": "13565a65f7c443cb5aadb9dc01cfd0b9e0c7d71bd5b92d4f62e8a2723120732e",
        "column_scale_sha256": "879a3dfdcde792239d2d2ad235c36e96c02eeb75f989c45adcaf3eee50cbc633",
        "row_scale_min": 3.0719121457746777e-08,
        "row_scale_max": 3.9991967772800766e-07,
        "column_scale_min": 1.0,
        "column_scale_max": 1.0000000000000002,
        "equilibrated_row_max_abs_min": 1.0,
        "equilibrated_row_max_abs_max": 1.0,
        "equilibrated_column_max_abs_min": 1.0,
        "equilibrated_column_max_abs_max": 1.0,
    },
    "A_conductor_II": {
        "dtype": "complex128",
        "shape": [31489, 31489],
        "nnz": 219393,
        "sha256": "5ead3bbdc2fc1b4f7a1a94bb7c9ebf9401c6a7d96ee63335045d64e3b082225f",
        "sparse_array_bytes": 4513820,
        "transpose_relative": 0.0,
        "equilibrated_nnz": 219393,
        "equilibrated_sha256": "b4efc2d388b2fe5c3280e8481d5afedd497952da3f18b34a86674a7ce97daf54",
        "equilibrated_sparse_array_bytes": 4513820,
        "row_scale_sha256": "2e7a7b1c7d1bc2b51d33efd4185a2214422aaea0530d3483348c53422311987d",
        "column_scale_sha256": "0a531cd1ded535c412bb9151298e108a429bc82e29b96f665d8ba1448acba3f4",
        "row_scale_min": 3.0719121457746777e-08,
        "row_scale_max": 3.99919677727939e-07,
        "column_scale_min": 1.0,
        "column_scale_max": 1.0000000000000002,
        "equilibrated_row_max_abs_min": 1.0,
        "equilibrated_row_max_abs_max": 1.0,
        "equilibrated_column_max_abs_min": 1.0,
        "equilibrated_column_max_abs_max": 1.0,
    },
}

RESOURCE_POLICY: Mapping[str, object] = {
    "schema": "AV-BS1-h4-p0r-resource-policy-v1",
    "wall_stop_seconds": 900,
    "tree_working_set_stop_bytes": 4294967296,
    "tree_private_stop_bytes": 5368709120,
    "tree_commit_stop_bytes": 5368709120,
    "commit_headroom_floor_bytes": 2147483648,
    "available_physical_floor_bytes": 1610612736,
    "minimum_available_physical_before_spawn_bytes": 5081474791,
    "minimum_commit_headroom_before_spawn_bytes": 5618345703,
    "one_factor_hard_cap_bytes": 2147483648,
    "p0_sparse_base_arrays_bytes": 5449772,
    "sparse_copy_allowance_bytes": 87196352,
    "p0r_factor_only_raw_total_bytes": 2240129772,
    "p0r_factor_only_with_25pct_margin_bytes": 2800162215,
    "p0r_factor_only_tree_working_set_slack_bytes": 1494805081,
    "retained_p0_guarded_envelope_with_25pct_margin_bytes": 3470862055,
    "retained_p0_guarded_envelope_tree_working_set_slack_bytes": 824105241,
    "successful_tree_sample_count_min": 1,
    "process_tree_includes_runner": True,
    "no_orphan_or_monitor_error": True,
    "sequential_one_factor_resident": True,
    "factor_fit_unproven": True,
}

FAILURE_CODES = [
    "BLOCKED_AV_BS_FACTOR",
    "BLOCKED_AV_BS_MESH_HASH",
    "BLOCKED_AV_BS_RESOURCE",
    "BLOCKED_AV_BS_RESULT_SCHEMA",
]


AvBsError = h1.AvBsError


def _canonical_sha(value: object) -> str:
    return sha256(h1.canonical_bytes(value)).hexdigest()


def _sparse_bytes(matrix: csc_matrix) -> int:
    return int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)


def _validate_resource_policy() -> str:
    policy = RESOURCE_POLICY
    raw_total = (
        int(policy["p0_sparse_base_arrays_bytes"])
        + int(policy["sparse_copy_allowance_bytes"])
        + int(policy["one_factor_hard_cap_bytes"])
    )
    margin = (5 * raw_total + 3) // 4
    checks = {
        "p0r_factor_only_raw_total_bytes": raw_total,
        "p0r_factor_only_with_25pct_margin_bytes": margin,
        "p0r_factor_only_tree_working_set_slack_bytes": int(
            policy["tree_working_set_stop_bytes"]
        )
        - margin,
        "minimum_available_physical_before_spawn_bytes": int(
            policy["retained_p0_guarded_envelope_with_25pct_margin_bytes"]
        )
        + int(policy["available_physical_floor_bytes"]),
        "minimum_commit_headroom_before_spawn_bytes": int(
            policy["retained_p0_guarded_envelope_with_25pct_margin_bytes"]
        )
        + int(policy["commit_headroom_floor_bytes"]),
        "retained_p0_guarded_envelope_tree_working_set_slack_bytes": int(
            policy["tree_working_set_stop_bytes"]
        )
        - int(policy["retained_p0_guarded_envelope_with_25pct_margin_bytes"]),
    }
    for key, value in checks.items():
        if policy[key] != value:
            raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"H4-P0R resource {key} mismatch")
    digest = _canonical_sha(policy)
    if digest != EXPECTED_RESOURCE_POLICY_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H4-P0R resource policy checksum mismatch")
    return digest


def _git_bytes(*arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments], cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"git provenance check failed: {arguments!r}") from exc
    return completed.stdout


def _validate_h4_p0_commit() -> None:
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", H4_P0_COMMIT, "HEAD"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H4-P0 commit is not an ancestor") from exc
    for key, relative_path in H4_P0_PATHS.items():
        expected = H4_P0_BINDINGS[key]
        working_path = ROOT / relative_path
        if h1._file_sha256(working_path) != expected:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"working H4-P0 {key} mismatch")
        committed = _git_bytes("show", f"{H4_P0_COMMIT}:{relative_path}")
        if sha256(committed).hexdigest() != expected:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"committed H4-P0 {key} mismatch")


def _validate_h2_tombstone() -> Mapping[str, object]:
    if h1._file_sha256(H2_TOMBSTONE_PATH) != H2_TOMBSTONE_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 consumed tombstone checksum mismatch")
    try:
        tombstone = json.loads(H2_TOMBSTONE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 consumed tombstone is unreadable") from exc
    expected = {
        "schema": "AV-BS1-h2-p1-consumed-review-token-v1",
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorization_state": "consumed",
        "uses_remaining": 0,
        "next_stage_authorized": False,
        "consumption_validated_pass": True,
        "consumption_evidence_errors": [],
        "mandatory_stage_pass": True,
        "failure_codes": [],
        "result_evidence_valid": True,
        "resource_evidence_valid": True,
        "guard_evidence_valid": True,
        "consumed_result_file_sha256": H2_ARTIFACT_FILE_SHA256,
        "consumed_result_payload_sha256": H2_ARTIFACT_PAYLOAD_SHA256,
        "consumed_resource_report_sha256": H2_RESOURCE_REPORT_SHA256,
        "result_status": "passed_AV_BS_h2_stage_only_pending_h4_preregistration",
    }
    for key, value in expected.items():
        if tombstone.get(key) != value:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H2 tombstone {key} mismatch")
    return tombstone


def _validate_h4_p0_subpayloads(payload: Mapping[str, object]) -> None:
    if h1._wrapper(payload)["payload_sha256"] != H4_P0_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H4-P0 manifest payload mismatch")
    subpayloads = {
        "mesh_payload_sha256": _canonical_sha(payload["mesh"]),
        "partition_payload_sha256": _canonical_sha(payload["partition"]),
        "lineage_payload_sha256": _canonical_sha(payload["cyclic_lineage"]),
        "assembly_payload_sha256": _canonical_sha(payload["assembly"]),
        "resource_payload_sha256": _canonical_sha(payload["resource_preflight"]),
    }
    for key, value in subpayloads.items():
        if value != H4_P0_BINDINGS[key]:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"H4-P0 {key} mismatch")


def _validate_parent_payload() -> tuple[Mapping[str, object], Mapping[str, object]]:
    _validate_h4_p0_commit()
    tombstone = _validate_h2_tombstone()
    payload = p0.run_manifest()
    _validate_h4_p0_subpayloads(payload)
    if payload["resource_preflight"]["guarded_one_factor_prospective"]["factor_fit_unproven"] is not True:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H4-P0 factor-fit state changed")
    if payload["resource_preflight"]["primary_h4_authorized"] is not False:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H4-P0 unexpectedly authorizes physics")
    return payload, tombstone


def _equilibrated_contract(matrix: csc_matrix) -> Mapping[str, object]:
    row_max = h1._row_max_abs(matrix)
    if np.any(~np.isfinite(row_max)) or np.any(row_max <= 0.0):
        raise AvBsError("BLOCKED_AV_BS_FACTOR", "non-finite H4-P0R row scale")
    row_scale = 1.0 / row_max
    row_scaled = csc_matrix(matrix.multiply(row_scale[:, None]))
    column_max = h1._column_max_abs(row_scaled)
    if np.any(~np.isfinite(column_max)) or np.any(column_max <= 0.0):
        raise AvBsError("BLOCKED_AV_BS_FACTOR", "non-finite H4-P0R column scale")
    column_scale = 1.0 / column_max
    equilibrated = csc_matrix(row_scaled.multiply(column_scale[None, :]))
    equilibrated.sum_duplicates()
    equilibrated.sort_indices()
    equilibrated.eliminate_zeros()
    equilibrated_row_max = h1._row_max_abs(equilibrated)
    equilibrated_column_max = h1._column_max_abs(equilibrated)
    return {
        "equilibrated_nnz": int(equilibrated.nnz),
        "equilibrated_sha256": h1._sparse_sha256(equilibrated),
        "equilibrated_sparse_array_bytes": _sparse_bytes(equilibrated),
        "row_scale_sha256": h1._array_sha256(row_scale, dtype="<f8"),
        "column_scale_sha256": h1._array_sha256(column_scale, dtype="<f8"),
        "row_scale_min": float(np.min(row_scale)),
        "row_scale_max": float(np.max(row_scale)),
        "column_scale_min": float(np.min(column_scale)),
        "column_scale_max": float(np.max(column_scale)),
        "equilibrated_row_max_abs_min": float(np.min(equilibrated_row_max)),
        "equilibrated_row_max_abs_max": float(np.max(equilibrated_row_max)),
        "equilibrated_column_max_abs_min": float(np.min(equilibrated_column_max)),
        "equilibrated_column_max_abs_max": float(np.max(equilibrated_column_max)),
    }


def _matrix_record(name: str, matrix: csc_matrix, *, dtype: str, equilibrate: bool) -> Mapping[str, object]:
    record: dict[str, object] = {
        "dtype": dtype,
        "shape": list(matrix.shape),
        "nnz": int(matrix.nnz),
        "sha256": h1._sparse_sha256(matrix),
        "sparse_array_bytes": _sparse_bytes(matrix),
        "transpose_relative": h1._sparse_transpose_relative(matrix),
    }
    if equilibrate:
        record.update(_equilibrated_contract(matrix))
    expected = EXPECTED_MATRIX_INPUTS[name]
    if record != expected:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"H4-P0R {name} input mismatch")
    return record


def matrix_input_contract() -> Mapping[str, object]:
    nodes, triangles, midpoint = p0.seed_mesh_h4()
    manifest, boundary = p0.mesh_manifest(nodes, triangles)
    tags, _ = p0.h4_tags(midpoint)
    raw_stiffness, mass = h1._assemble_volume(nodes, triangles)
    stiffness, _ = p0.canonicalize_h4(raw_stiffness, nodes, triangles, tags, manifest)
    interior, _, _ = p0.partition(len(nodes), boundary)
    k_ii = stiffness[interior, :][:, interior].tocsc()
    m_ii = mass[interior, :][:, interior].tocsc()
    for matrix in (k_ii, m_ii):
        matrix.sum_duplicates()
        matrix.sort_indices()
        matrix.eliminate_zeros()
    omega = 2.0 * math.pi * h1.FREQUENCY_HZ
    background = csc_matrix(k_ii, dtype=np.complex128)
    conductor = csc_matrix(k_ii.astype(np.complex128) + 1j * omega * h1.SIGMA_S_PER_M * m_ii)
    for matrix in (background, conductor):
        matrix.sum_duplicates()
        matrix.sort_indices()
        matrix.eliminate_zeros()
    records = {
        "K_II": _matrix_record("K_II", k_ii, dtype="float64", equilibrate=False),
        "M_II": _matrix_record("M_II", m_ii, dtype="float64", equilibrate=False),
        "A_background_II": _matrix_record(
            "A_background_II", background, dtype="complex128", equilibrate=True
        ),
        "A_conductor_II": _matrix_record(
            "A_conductor_II", conductor, dtype="complex128", equilibrate=True
        ),
    }
    if max(float(record["transpose_relative"]) for record in records.values()) > h1.MAX_ASSEMBLY_TRANSPOSE:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4-P0R matrix transpose gate failed")
    contract = {
        "frequency_hz": h1.FREQUENCY_HZ,
        "sigma_s_per_m": h1.SIGMA_S_PER_M,
        "omega_rad_per_s": omega,
        "omega_sigma": omega * h1.SIGMA_S_PER_M,
        "interior_nodes": len(interior),
        "formation": "A_background_II=complex128(K_II); A_conductor_II=K_II+j*omega*sigma*M_II",
        "matrices": records,
        "matrix_contract_sha256": _canonical_sha(records),
    }
    if contract["matrix_contract_sha256"] != EXPECTED_MATRIX_CONTRACT_SHA256:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4-P0R matrix-contract checksum mismatch")
    return contract


def run_manifest() -> Mapping[str, object]:
    if h1._runtime() != h1.EXPECTED_RUNTIME:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4-P0R runtime mismatch")
    p0_payload, tombstone = _validate_parent_payload()
    matrices = matrix_input_contract()
    resource_policy_sha256 = _validate_resource_policy()
    return {
        "schema": SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "manifest",
        "status": "preregistered_H4_P0R_contract_only_no_factor",
        "authorization_state": "not_authorized",
        "runtime": list(h1.EXPECTED_RUNTIME),
        "bindings": {
            "h4_p0_commit": H4_P0_COMMIT,
            "h4_p0_manifest_payload_sha256": H4_P0_PAYLOAD_SHA256,
            **dict(H4_P0_BINDINGS),
            "h2_artifact_file_sha256": H2_ARTIFACT_FILE_SHA256,
            "h2_artifact_payload_sha256": H2_ARTIFACT_PAYLOAD_SHA256,
            "h2_numerical_payload_sha256": H2_NUMERICAL_PAYLOAD_SHA256,
            "h2_resource_report_sha256": H2_RESOURCE_REPORT_SHA256,
            "h2_consumed_tombstone_sha256": H2_TOMBSTONE_SHA256,
            "h2_consumed_token_sha256": tombstone["consumed_review_token_sha256"],
        },
        "h4_p0_mesh": p0_payload["mesh"],
        "h4_p0_assembly": p0_payload["assembly"],
        "matrix_inputs": matrices,
        "factorization_contract": {
            "stage": "primary-h4-p0r",
            "factor_order": ["A_background_II", "A_conductor_II"],
            "sequential_one_factor_resident": True,
            "delete_factor_matrix_scales_and_collect_between_factors": True,
            "permc_spec": "COLAMD",
            "diag_pivot_thresh": 1.0,
            "superlu_equil": False,
            "explicit_row_then_column_scaling": True,
            "factor_bytes_portable_structural_formula": "24*(L_nnz+U_nnz)+8*(4*n+2)",
            "factor_bytes_object_formula": "sum(data+indices+indptr for L,U)+perm_r+perm_c",
            "one_factor_hard_cap_bytes": RESOURCE_POLICY["one_factor_hard_cap_bytes"],
            "rhs_count": 0,
            "batch_size": 0,
            "factor_solve_forbidden": True,
            "onenormest_forbidden": True,
            "required_factor_checks": [
                "L_U_nnz_and_sparse_sha256",
                "perm_r_perm_c_sha256_and_bijection",
                "finite_nonzero_U_diagonal",
                "exact_exported_and_portable_structural_bytes_below_cap",
                "factor_wall_seconds",
                "factor_sequence_nonoverlap",
            ],
            "success_status": "passed_AV_BS_h4_p0r_factor_only_pending_h4_p1_preregistration",
            "success_does_not_authorize_h4_p1": True,
        },
        "resource_policy": dict(RESOURCE_POLICY),
        "resource_policy_sha256": resource_policy_sha256,
        "future_schemas": {
            "review_token": REVIEW_SCHEMA,
            "claim": CLAIM_SCHEMA,
            "guard": GUARD_SCHEMA,
            "resource": RESOURCE_SCHEMA,
            "numerical": NUMERICAL_SCHEMA,
            "result": RESULT_SCHEMA,
            "failure": FAILURE_SCHEMA,
            "consumed_tombstone": TOMBSTONE_SCHEMA,
        },
        "future_review_token_required_fields": [
            "schema",
            "program",
            "case_id",
            "authorized_stage",
            "authorization_state",
            "uses_remaining",
            "next_stage_authorized",
            "review_token_id",
            "reviewed_utc",
            "expires_utc",
            "p0r_preregistration_commit",
            "fixture_sha256",
            "runner_sha256",
            "static_test_sha256",
            "preregistration_doc_sha256",
            "manifest_payload_sha256",
            "matrix_contract_sha256",
            "resource_policy_sha256",
            "parent_bindings",
            "independent_audits",
            "review_scope",
            "review_disposition",
        ],
        "future_claim_required_fields": [
            "schema",
            "review_token_id",
            "review_token_sha256",
            "git_head",
            "fixture_sha256",
            "runner_sha256",
            "preflight_payload_sha256",
            "resource_policy_sha256",
            "guard_nonce",
            "created_utc",
            "parent_pid",
        ],
        "future_resource_required_fields": [
            "schema",
            "stage",
            "runner_sha256",
            "fixture_sha256",
            "review_token_sha256",
            "claim_sha256",
            "guard_contract_sha256",
            "child_stdout_sha256",
            "child_exit_code",
            "wall_seconds",
            "successful_tree_sample_count",
            "execution_tree_includes_runner",
            "execution_tree_root_pid",
            "child_process_handle_acquired",
            "baseline",
            "peak",
            "final_system",
            "thresholds",
            "monitor_ok",
            "monitor_error",
            "stop_reason",
            "mandatory_resource_gate_pass",
        ],
        "future_resource_nested_required_fields": {
            "peak": [
                "tree_working_set_bytes",
                "tree_private_commit_bytes",
                "tree_committed_pagefile_bytes",
                "tree_nonprivate_working_set_proxy_bytes",
                "tree_page_fault_count",
                "available_physical_min_bytes",
                "system_commit_headroom_min_bytes",
                "system_commit_total_bytes",
            ],
            "final_system": [
                "available_physical_bytes",
                "commit_headroom_bytes",
                "commit_limit_bytes",
                "commit_total_bytes",
                "page_size_bytes",
            ],
            "thresholds": [
                "tree_ws_stop_bytes",
                "tree_private_stop_bytes",
                "tree_commit_stop_bytes",
                "available_physical_floor_bytes",
                "commit_headroom_floor_bytes",
            ],
        },
        "future_result_required_fields": [
            "schema",
            "program",
            "case_id",
            "stage",
            "status",
            "authorization_state",
            "review_token_id",
            "review_token_sha256",
            "bindings",
            "matrix_inputs",
            "factor_order",
            "factor_certificates",
            "factor_sequence_nonoverlap",
            "resource_report_sha256",
            "guard_contract_sha256",
            "claim_sha256",
            "child_stdout_sha256",
            "mandatory_stage_pass",
            "failure_codes",
            "factorization_performed",
            "physics_solve_performed",
            "forbidden_operation_flags",
            "next_stage_authorized",
        ],
        "one_use_lifecycle": {
            "review_token_tracked_and_clean_checkout": True,
            "review_token_expiry_required": True,
            "uses_remaining_before_attempt": 1,
            "atomic_create_new_claim_before_spawn": True,
            "consume_after_every_launched_child_outcome": True,
            "consumed_uses_remaining": 0,
            "replay_rejected": True,
            "next_stage_authorized": False,
            "required_independent_audits": [
                "Sol mathematical and factor-contract review",
                "Terra Windows process-tree and one-shot lifecycle review",
                "Luna schema, provenance, and bounded-test review",
            ],
        },
        "allowed_outputs": [
            "input_matrix_hashes_and_nnz",
            "equilibration_hashes",
            "L_U_nnz_and_fill_ratio",
            "L_U_sparse_sha256",
            "permutation_hashes",
            "factor_array_and_conservative_bytes",
            "factor_wall_seconds",
            "process_tree_resource_peaks",
            "guard_resource_result_checksums",
        ],
        "forbidden_operations": {
            "rhs_generated": False,
            "linear_solve_called": False,
            "extensions_generated": False,
            "boundary_operator_generated": False,
            "schur_or_Y_generated": False,
            "modal_response_generated": False,
            "PDE_residual_generated": False,
            "power_generated": False,
            "analytic_or_convergence_gate_generated": False,
            "physics_solve_performed": False,
        },
        "failure_code_allowlist": list(FAILURE_CODES),
        "factorization_performed": False,
        "physics_solve_performed": False,
        "available_solve_stages": [],
        "unavailable_stages": ["primary-h4-p0r", "primary-h4", "withheld", "EQ0"],
        "next_stage_authorized": False,
    }


def _failure_wrapper(error: AvBsError, stage: str) -> Mapping[str, object]:
    code = error.code if error.code in FAILURE_CODES else "BLOCKED_AV_BS_RESULT_SCHEMA"
    detail = error.detail if code == error.code else f"normalized_from={error.code}; {error.detail}"
    return h1._wrapper(
        {
            "schema": FAILURE_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": stage,
            "status": code,
            "authorization_state": "not_authorized",
            "mandatory_stage_pass": False,
            "failure_codes": [code],
            "detail": detail,
            "factorization_performed": False,
            "physics_solve_performed": False,
            "next_stage_authorized": False,
        }
    )


def validated_manifest_wrapper() -> Mapping[str, object]:
    wrapper = h1._wrapper(run_manifest())
    if wrapper["payload_sha256"] != EXPECTED_MANIFEST_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H4-P0R manifest payload mismatch")
    return wrapper


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SPD Decap PI Evaluator v0.22.0 H4-P0R contract")
    parser.add_argument("--stage", required=True, choices=("manifest",))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = validated_manifest_wrapper()
        code = 0
    except AvBsError as error:
        output = _failure_wrapper(error, args.stage)
        code = 2
    print(
        json.dumps(
            output,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
