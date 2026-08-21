#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.22.0 AV-BS1 H4-P1 contract fixture.

This module is intentionally manifest-only.  It preregisters a prospective
``primary-h4`` accuracy/resource contract, but it cannot create an RHS, factor,
solve, sidecar, authorization artifact, or public result.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
from typing import Mapping

PROGRAM = "SPD Decap PI Evaluator v0.22.0"
CASE_ID = "AV-BS1-CIRCLE-PRIMARY"
SCHEMA = "AV-BS1-h4-p1-manifest-v1"
FAILURE_SCHEMA = "AV-BS1-h4-p1-manifest-failure-v1"
SIDECAR_SCHEMA = "AV-BS1-h4-p1-binary-sidecar-descriptor-v1"
ROOT = Path(__file__).resolve().parents[2]

BASE_CONTRACT_COMMIT = "01d198a851be7ce08db963507bb902986eb0dd15"
ORIGINAL_TOKEN_COMMIT = "e35ef01214f4bf9ec75e7b428e72b21d38c9161b"
CONSUMED_TOKEN_COMMIT = "bf94e1890c489e690801b3d90bcbf50ff61ca233"
RETIREMENT_COMMIT = "389ec1e51b87f3eff928278c76bbbf60241af972"
POSTPASS_DOCS_COMMIT = "50f9e908d0fc740c05e84c6ae65262dd8774553a"
TOKEN_PATH = "tools/research/av_bs1_h4_p0r_p1_review_token.json"

ORIGINAL_TOKEN_RAW_SHA256 = "63ceea36ce8fc3a0e97942c7bbdf3487e3478d1f048de2c9a5343118e63b53ca"
ORIGINAL_TOKEN_CANONICAL_SHA256 = "f4e61b9e05557f0d2705119670e1f760e52325da8f2767aa82a2080d3aa7a555"
CONSUMED_TOMBSTONE_SHA256 = "3ce2bc00bdb4ee2d2da1a706e7c2211eba2a0579e179990aa34d2e2855cd615f"
REVIEW_TOKEN_ID = "2435fa59edc64efca2fb4665b0854d43"

CORE_BINDINGS: Mapping[str, str] = {
    "tools/research/av_bs1_boundary_schur_h4_p0r_p1.py":
        "46082123fbf98102f3e5995c32bf65d8d04bb835b9c1305de0150349e75e48c7",
    "tools/research/run_av_bs1_h4_p0r_p1_stage.ps1":
        "f0159061dbd4d1b34881911edfdfb72146a3a23e5cdc75ca8fa4069c08aadb86",
    "tests/test_research_av_bs1_boundary_schur_h4_p0r_p1.py":
        "e5496ceb24c33c835b88ce20a3f67f941c8279e0471708a01022238fc211b109",
}

POSTPASS_DOC_BINDINGS: Mapping[str, str] = {
    "README.md": "9eededb053f33e54bd99302cee68f47bb2e7e74b88c8cbc950c19b6d9517e696",
    "docs/evaluation-research/ALGORITHM_CANDIDATES.md": "17e2b4404003f8e1b17c289014102551fb1d27c4ba3ec4bcb29dae50709600cf",
    "docs/evaluation-research/LOCAL_ORACLE_PLAN.md": "6db62ba8e27ebd6ec4a8f29c821d399c914faa3de5c012439ee05e78e560cc1b",
    "docs/evaluation-research/ORACLE_REPRODUCTION.md": "bcb6ee896123c5a65bda98c4a533512cf3e0c515d2fa902aa3a301e94551c151",
    "docs/evaluation-research/R2_ORACLE_RESULTS.md": "fcac10d7cb007ed4b8abe5514c7fbfa1d7b5aa72e4f82ca15bed2c95245b4e79",
    "docs/evaluation-research/README.md": "95b0471618ee319102b72f58811dff7dcf39970d36e6ebf9f28974fae370d774",
    "docs/evaluation-research/RESEARCH_STATE.md": "3dbe16b3cc52b1babec025654821a726b065bf8ed1fa5bd29efdfe8b048425b8",
    "docs/evaluation-research/SESSION_LOG.md": "0d44c6a6abebe1a48fba351f18bced194428ee13a47792a8fd8386da1f9ae3cb",
    "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md": "51678600dfc309250325d68cb30b5d6867256db6abc487c30e5afea62a94f57f",
    "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_RESULTS.md": "c9a3cfd622e4508ac2f64d993480303aa6e6cef1a1b3145549d7906ca68e7aec",
    "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_SPEC.md": "2080fa291263afed062b503043b7aeff11c3b74fe7b9f9df7a3a2822702a131d",
    "docs/evaluation-research/T1_CIRCLE_DTN_RESULTS.md": "392356adb52e5bf3155925acb242fb068fd0b0b16c82d3f6136148d435e49214",
    "docs/evaluation-research/T1_M1_EQ0_RESULTS.md": "ca599f4e13b48291bc0e4d222c82a0a8475f4be50df5bf89c641ad15356b742c",
    "docs/evaluation-research/T1_M1_REFERENCE_SPEC.md": "860aee6019bebdadf115f753ce359e3dceddbb7532d7b22a9fa9aeed29f0999c",
    "docs/evaluation-research/T1_TRACE_ORACLE_RESULTS.md": "ca3b206a97f934f7e38e85826af7c2a0ba8cf380b114f0ec213f139ffa3281c0",
}

EVIDENCE_COMMITMENTS: Mapping[str, Mapping[str, object]] = {
    "claim": {"raw_sha256": "7ee75af33cdba5ee9cb4f92ff4acc601a7939717eda24ffd35536f7275c646b9", "canonical_sha256": "00611c2a7425d0100f0c49abe881b43978645355e73946af23d5424254907b68"},
    "guard": {"raw_sha256": "f8454d99b28195070a8b22ee870df6a0a95d9dc3f35b83aa7ea107ad55f7e7c6", "canonical_sha256": "f99f1f20362eb7d7cae04f38cc20c01e4c56bf5500dd437914642699f439f755"},
    "numerical": {"raw_sha256": "0af448eeea2eb302f6aeebbb04fd01992067ccc55a017eb780507aa34924728f", "payload_sha256": "b236e160b41150b856fbd69f98ab682edb3c61d6f3a53f02c5960c553f1a5c78"},
    "factor_prefix_1": {"raw_sha256": "d37940d04ede5820c020d5b352c418592f344db89a4db6430762339b115b50e5", "payload_sha256": "7e9b6c9d719c8c782f7727a49ea53945867b76721e79b67d143205699b4e49cb"},
    "factor_prefix_2": {"raw_sha256": "174f6cca5f808094a306c681a9502b9551c7d741657d1d0db62b9510ca74a3e8", "payload_sha256": "45e1a18105128778ffae2d0e53aeb2621fd080d077d4a775020f3c52cd0d5ab7"},
    "resource": {"raw_sha256": "d6358608dd97c0f245bb781d0bfc63e929a1a74733907a7fa2a1fbdb9dca97de", "canonical_sha256": "537350ed7b9b9d14206624ed8fd46014de6cb0d417c0611b7753170bae9abf84"},
    "result": {"raw_sha256": "ad007d6ac4c053981f6ab9da72505848956019cd4b5cdb1e1ecfa7b5e0d02121", "payload_sha256": "0d33860805d50e887fe8a43c9adea3da81df99df73a812ca7559c92419bdb6b0"},
    "pre_exit": {"raw_sha256": "5ff55a34ac6687cd2a86c1c890b50b7ae43b2917e862d9a6c6a001d70e10cadb", "canonical_sha256": "b5cb10f9a8b8eb5fc40f04b4ff11984d497d0ddf178e34963db1aec1e3b61207"},
    "outer_close": {"raw_sha256": "184302a02026e1ab9aa84d4a94a2f9605c8254849920379d6b30fb0ce765f3c5", "canonical_sha256": "184302a02026e1ab9aa84d4a94a2f9605c8254849920379d6b30fb0ce765f3c5"},
    "terminal_seal": {"raw_sha256": "070c86e25aeb982d5ef1c9c99c9c15d9ed3a6d08ce4e521e55262e4586a516a4", "canonical_sha256": "8c9de1f93bccac285d81ca60d7b9bc827cb1e66ffc2fe036825a008a491513b6"},
    "consumed_tombstone": {"raw_sha256": CONSUMED_TOMBSTONE_SHA256, "canonical_sha256": CONSUMED_TOMBSTONE_SHA256},
}

MATRIX_CONTRACT: Mapping[str, object] = {
    "aggregate_matrix_contract_sha256":
        "89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46",
    "K_II_sha256":
        "dddee397ee96ff0bd7acc321cd5755dd611f1e3d1f6bfcf9664c388d27184361",
    "M_II_sha256":
        "4dccc26f1a12bbab4a4ee863e509d317cb518075661867da4e0b6980f7dcc6c4",
    "frequency_hz": 100000.0,
    "angular_frequency_rad_per_s": 628318.5307179586,
    "conductor_sigma_siemens_per_m": 59600000.0,
    "A_background_II_formula": "complex128(K_II)",
    "A_conductor_II_formula": "complex128(K_II)+1j*angular_frequency_rad_per_s*conductor_sigma_siemens_per_m*M_II",
    "A_background_II": {
        "raw_sha256":
            "8c099d2cb5947d1b72bd74f64329879c221b86c569b4100baa47dfea9aeff641",
        "row_scale_Dr_sha256":
            "13565a65f7c443cb5aadb9dc01cfd0b9e0c7d71bd5b92d4f62e8a2723120732e",
        "column_scale_Dc_sha256":
            "879a3dfdcde792239d2d2ad235c36e96c02eeb75f989c45adcaf3eee50cbc633",
        "equilibrated_Aeq_sha256":
            "9216cc8938d9efc1e531a4fb301aca45547fad55f01dec57863986622327dd74",
    },
    "A_conductor_II": {
        "raw_sha256":
            "5ead3bbdc2fc1b4f7a1a94bb7c9ebf9401c6a7d96ee63335045d64e3b082225f",
        "row_scale_Dr_sha256":
            "2e7a7b1c7d1bc2b51d33efd4185a2214422aaea0530d3483348c53422311987d",
        "column_scale_Dc_sha256":
            "0a531cd1ded535c412bb9151298e108a429bc82e29b96f665d8ba1448acba3f4",
        "equilibrated_Aeq_sha256":
            "b4efc2d388b2fe5c3280e8481d5afedd497952da3f18b34a86674a7ce97daf54",
    },
}

EXPECTED_MANIFEST_PAYLOAD_SHA256 = "ec489c784e01635955560fa0ac0681b22bd890759d6693c111cb8ff28c31b753"
EXPECTED_MANIFEST_WRAPPER_SHA256 = "cfa8f9a2504a8fad40ef03e9cb3842877e176a9f3d620f5805099a98c80523e7"

FAILURE_CODES = [
    "BLOCKED_AV_BS_SOLVE", "BLOCKED_AV_BS_RECIPROCITY",
    "BLOCKED_AV_BS_PASSIVITY", "BLOCKED_AV_BS_POWER",
    "BLOCKED_AV_BS_ANALYTIC", "BLOCKED_AV_BS_RESOURCE",
    "BLOCKED_AV_BS_MESH_HASH", "BLOCKED_AV_BS_RESULT_SCHEMA",
    "BLOCKED_AV_BS_FACTOR",
]

class AvBsError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical_sha(value: object) -> str:
    return sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _git(arguments: list[str], *, maximum_bytes: int = 2 * 1024 * 1024) -> bytes:
    read_only_commands = {"cat-file", "diff-tree", "ls-files", "ls-tree", "merge-base", "rev-parse"}
    if not arguments or arguments[0] not in read_only_commands:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "non-read-only git command rejected")
    if arguments[0] == "merge-base" and arguments[1:2] != ["--is-ancestor"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "unsupported merge-base form rejected")
    try:
        completed = subprocess.run(
            ["git", *arguments], cwd=ROOT, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"git provenance check failed: {arguments!r}") from exc
    if len(completed.stdout) > maximum_bytes:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "git provenance output exceeds bound")
    return completed.stdout


def _git_blob(commit: str, path: str, *, maximum_bytes: int = 2 * 1024 * 1024) -> bytes:
    spec = f"{commit}:{path}"
    try:
        size = int(_git(["cat-file", "-s", spec], maximum_bytes=128).decode("ascii").strip())
    except (UnicodeError, ValueError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"invalid git blob size for {path}") from exc
    if size < 0 or size > maximum_bytes:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"git blob size out of bounds for {path}")
    blob = _git(["cat-file", "blob", spec], maximum_bytes=maximum_bytes)
    if len(blob) != size:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"git blob size mismatch for {path}")
    return blob


def _strict_json_object(raw: bytes, label: str) -> Mapping[str, object]:
    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    def finite_float(text: str) -> float:
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return value

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs_hook,
            parse_float=finite_float,
            parse_constant=lambda text: (_ for _ in ()).throw(ValueError(f"invalid constant {text}")),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is not strict JSON") from exc
    if not isinstance(value, Mapping):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} root is not an object")
    return value


def _require_ancestor(commit: str) -> None:
    _git(["merge-base", "--is-ancestor", commit, "HEAD"], maximum_bytes=0)


def _validate_lifecycle_and_provenance() -> None:
    expected_parents = {
        ORIGINAL_TOKEN_COMMIT: BASE_CONTRACT_COMMIT,
        CONSUMED_TOKEN_COMMIT: ORIGINAL_TOKEN_COMMIT,
        RETIREMENT_COMMIT: CONSUMED_TOKEN_COMMIT,
        POSTPASS_DOCS_COMMIT: RETIREMENT_COMMIT,
    }
    expected_changes = {
        ORIGINAL_TOKEN_COMMIT: f"A\t{TOKEN_PATH}\n".encode(),
        CONSUMED_TOKEN_COMMIT: f"M\t{TOKEN_PATH}\n".encode(),
        RETIREMENT_COMMIT: f"D\t{TOKEN_PATH}\n".encode(),
    }
    _require_ancestor(POSTPASS_DOCS_COMMIT)
    for commit, parent in expected_parents.items():
        observed_parent = _git(["rev-parse", f"{commit}^"], maximum_bytes=128).decode("ascii").strip()
        if observed_parent != parent:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"lifecycle parent mismatch at {commit}")
    for commit, expected in expected_changes.items():
        observed = _git(["diff-tree", "--no-commit-id", "--name-status", "-r", commit])
        if observed != expected:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"lifecycle scope mismatch at {commit}")
    expected_doc_changes = b"".join(
        f"M\t{path}\n".encode() for path in POSTPASS_DOC_BINDINGS
    )
    if _git(["diff-tree", "--no-commit-id", "--name-status", "-r", POSTPASS_DOCS_COMMIT]) != expected_doc_changes:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "post-pass documentation scope mismatch")

    original_raw = _git_blob(ORIGINAL_TOKEN_COMMIT, TOKEN_PATH, maximum_bytes=16 * 1024 * 1024)
    consumed_raw = _git_blob(CONSUMED_TOKEN_COMMIT, TOKEN_PATH, maximum_bytes=16 * 1024 * 1024)
    if sha256(original_raw).hexdigest() != ORIGINAL_TOKEN_RAW_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "original review token hash mismatch")
    if sha256(consumed_raw).hexdigest() != CONSUMED_TOMBSTONE_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone hash mismatch")
    original = _strict_json_object(original_raw, "original review token")
    consumed = _strict_json_object(consumed_raw, "consumed tombstone")
    if _canonical_sha(original) != ORIGINAL_TOKEN_CANONICAL_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "original review token canonical hash mismatch")
    if _canonical_sha(consumed) != CONSUMED_TOMBSTONE_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone canonical hash mismatch")
    original_expected = {
        "schema": "AV-BS1-h4-p0r-review-token-v1", "program": PROGRAM,
        "case_id": CASE_ID, "authorized_stage": "primary-h4-p0r",
        "authorization_state": "authorized", "uses_remaining": 1,
        "next_stage_authorized": False, "review_token_id": REVIEW_TOKEN_ID,
        "p0r_preregistration_commit": BASE_CONTRACT_COMMIT,
        "reviewed_utc": "2026-08-16T01:46:08Z",
        "expires_utc": "2026-08-16T05:46:08Z",
        "review_disposition": "approved_h4_p0r_factor_only",
    }
    consumed_expected = {
        "schema": "AV-BS1-h4-p0r-consumed-review-token-v2", "program": PROGRAM,
        "case_id": CASE_ID, "authorized_stage": "primary-h4-p0r",
        "authorization_state": "consumed", "uses_remaining": 0,
        "consumed_review_token_id": REVIEW_TOKEN_ID,
        "consumed_review_token_sha256": ORIGINAL_TOKEN_RAW_SHA256,
        "consumed_review_token_canonical_sha256": ORIGINAL_TOKEN_CANONICAL_SHA256,
        "attempt_status": "completed_pass", "consumption_validated_pass": True,
        "effective_attempt_status": "completed_pass", "mandatory_stage_pass": True,
        "terminal_evidence_complete": False, "authoritative_stage_pass": False,
        "factorization_attempted": True, "factorization_performed": True,
        "physics_solve_performed": False, "next_stage_authorized": False,
        "failure_codes": [], "terminal_seal_state": "pending_outer_observed_inner_exit",
    }
    if any(original.get(key) != value for key, value in original_expected.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "original review token field mismatch")
    if any(consumed.get(key) != value for key, value in consumed_expected.items()):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone field mismatch")
    if consumed.get("evidence_validation_errors") != []:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "consumed tombstone evidence errors changed")

    for path, digest in {**CORE_BINDINGS, **POSTPASS_DOC_BINDINGS}.items():
        if sha256(_git_blob(POSTPASS_DOCS_COMMIT, path)).hexdigest() != digest:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"post-pass committed blob mismatch: {path}")
    runtime_prereg = _git_blob(
        BASE_CONTRACT_COMMIT,
        "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md",
    )
    if sha256(runtime_prereg).hexdigest() != "954a91feb1639b0eb0cb2f62c2068af7a388fc7d55fc2370e909ca32da7968b7":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "runtime preregistration blob mismatch")
    token_worktree = ROOT / TOKEN_PATH
    if token_worktree.exists() or token_worktree.is_symlink():
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "current review token unexpectedly exists")
    if _git(["ls-files", "--stage", "--", TOKEN_PATH]) != b"":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "current index still contains review token")
    if _git(["ls-tree", "--name-only", "HEAD", "--", TOKEN_PATH]) != b"":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "current HEAD still contains review token")


def _sidecar_descriptor() -> Mapping[str, object]:
    arrays = [
        {
            "name": "Y", "offset_bytes": 0, "nbytes": 4194304,
            "shape": [512, 512], "element_count": 512 * 512,
            "axes": ["frozen_boundary_node_order", "frozen_boundary_node_order"],
        },
        {
            "name": "Y_reverse", "offset_bytes": 4194304, "nbytes": 4194304,
            "shape": [512, 512], "element_count": 512 * 512,
            "axes": ["frozen_boundary_node_order", "frozen_boundary_node_order"],
        },
        {
            "name": "M9_interior", "offset_bytes": 8388608, "nbytes": 4534416,
            "shape": [31489, 9], "element_count": 31489 * 9,
            "axes": ["frozen_interior_node_order", "signed_mode_order_minus4_through_plus4"],
            "signed_mode_columns": list(range(-4, 5)),
        },
    ]
    cursor = 0
    for item in arrays:
        if item["offset_bytes"] != cursor or item["nbytes"] != item["element_count"] * 16:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "sidecar offset mismatch")
        cursor += int(item["nbytes"])
    total = 12923024
    encoded = 4 * ((total + 2) // 3)
    if cursor != total or encoded != 17230700 or encoded - 16 * 1024 * 1024 != 453484:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "sidecar size arithmetic mismatch")
    return {
        "schema": SIDECAR_SCHEMA,
        "contract_only_no_file_created": True,
        "header": "none_fixed_layout_contiguous",
        "dtype": "<c16",
        "scalar_encoding": "IEEE754_binary64_real_then_imag",
        "byte_order": "little_endian",
        "array_order": "C",
        "compression": "none",
        "arrays": arrays,
        "future_array_generation_contract": {
            "H_background_formula": "vertical_stack([X_background,I_boundary])",
            "H_conductor_formula": "vertical_stack([X_conductor,I_boundary])",
            "Y_formula": "sigma*H_background.T@M@H_conductor",
            "Y_reverse_formula": "sigma*H_conductor.T@M@H_background",
            "transpose_is_bilinear_not_conjugate_transpose": True,
            "V_M9_formula": "V_M9[n,j]=exp(1j*signed_modes[j]*theta_boundary[n])",
            "signed_modes": list(range(-4, 5)),
            "M9_interior_formula": "X_conductor@V_M9",
            "boundary_identity_is_implicit_and_not_serialized": True,
            "all_matrix_mesh_boundary_and_mode_orders_are_frozen": True,
        },
        "total_binary_payload_bytes": total,
        "base64_payload_only_bytes": encoded,
        "inline_json_limit_bytes": 16 * 1024 * 1024,
        "base64_payload_only_exceeds_inline_limit_bytes": 453484,
        "json_key_quote_descriptor_overhead_included": False,
        "future_descriptor_required_fields": [
            "schema", "relative_path", "dtype", "byte_order", "array_order",
            "arrays", "total_binary_payload_bytes", "raw_sha256",
        ],
        "future_binary_sha256": None,
        "future_binary_sha256_format": "lowercase_hex_64",
        "path_policy": {
            "fixed_basename": "av_bs1_h4_p1_arrays.bin",
            "same_quarantine_directory_as_descriptor": True,
            "absolute_path_forbidden": True,
            "parent_traversal_forbidden": True,
            "reparse_point_or_symlink_forbidden": True,
            "file_identity_recheck_required": True,
        },
        "publication_policy": {
            "same_directory_create_new_temporary": True,
            "temporary_and_final_must_be_on_same_volume": True,
            "flush_file_before_atomic_publish": True,
            "atomic_publish_must_be_no_replace": True,
            "check_then_replace_or_overwrite_forbidden": True,
            "existing_final_target_rejected_without_mutation": True,
            "post_publish_size_hash_and_file_identity_recheck": True,
            "temporary_cleanup_required_on_failure": True,
        },
        "validation_policy": {
            "exact_size_offsets_shapes_dtype_and_order_required": True,
            "stream_all_real_and_imaginary_values_finite": True,
            "normalize_each_negative_zero_component_to_positive_zero_before_serialization_and_write": True,
            "raw_sha256_covers_exact_published_normalized_12923024_bytes": True,
            "trailing_or_missing_bytes_rejected": True,
            "tamper_or_path_identity_change_rejected": True,
        },
        "future_evidence_binding_policy": {
            "actual_descriptor_canonical_sha256_required": True,
            "actual_binary_raw_sha256_required": True,
            "bind_both_hashes_in_quarantine_result_consumed_tombstone_and_terminal_seal": True,
            "descriptor_and_binary_binding_mismatch_rejected": True,
        },
        "sidecar_creation_performed": False,
    }


def _resource_policy() -> Mapping[str, object]:
    interior = 31489
    boundary = 512
    batch = 4
    sparse_base = 5_449_772
    sparse_copy = 16 * sparse_base
    factor_cap = 2 * 1024**3
    extensions = 2 * interior * boundary * 16
    boundary_dense = 3 * boundary * boundary * 16
    batch_rhs = 4 * interior * batch * 16
    raw = sparse_base + sparse_copy + factor_cap + extensions + boundary_dense + batch_rhs
    margin = (5 * raw + 3) // 4
    ws_stop = 4 * 1024**3
    expected = (
        sparse_base, sparse_copy, factor_cap, extensions, boundary_dense,
        batch_rhs, raw, margin, ws_stop - margin,
    )
    if expected != (
        5449772, 87196352, 2147483648, 515915776, 12582912,
        8061184, 2776689644, 3470862055, 824105241,
    ):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H4-P1 resource arithmetic mismatch")
    return {
        "schema": "AV-BS1-h4-p1-resource-policy-v1",
        "interior_nodes": interior, "boundary_nodes": boundary,
        "rhs_count_per_factor": boundary, "maximum_rhs_batch": batch,
        "sparse_base_arrays_bytes": sparse_base,
        "sparse_copy_allowance_multiplier": 16,
        "sparse_copy_allowance_bytes": sparse_copy,
        "one_factor_hard_cap_bytes": factor_cap,
        "two_interior_extensions_bytes": extensions,
        "boundary_dense_work_bytes": boundary_dense,
        "batch4_rhs_work_bytes": batch_rhs,
        "raw_total_bytes": raw,
        "total_with_25pct_margin_bytes": margin,
        "tree_working_set_stop_bytes": ws_stop,
        "tree_working_set_stop_predicate": "tree_working_set_bytes>4294967296",
        "tree_working_set_slack_bytes": ws_stop - margin,
        "tree_private_stop_bytes": 5 * 1024**3,
        "tree_private_stop_predicate": "tree_private_bytes>5368709120",
        "tree_commit_stop_bytes": 5 * 1024**3,
        "tree_commit_stop_predicate": "tree_commit_bytes>5368709120",
        "wall_stop_seconds": 900,
        "wall_stop_predicate": "wall_seconds>900",
        "available_physical_runtime_stop_bytes": int(1.5 * 1024**3),
        "available_physical_runtime_stop_predicate": "available_physical_bytes<1610612736",
        "system_commit_headroom_runtime_stop_bytes": 2 * 1024**3,
        "system_commit_headroom_runtime_stop_predicate": "system_commit_headroom_bytes<2147483648",
        "successful_tree_sample_count_minimum": 1,
        "resource_pass_requires_minimum_successful_tree_sample_count": True,
        "prospective_process_tree_monitor_contract": {
            "sample_interval_milliseconds": 100,
            "membership": "outer_observer_plus_inner_runner_plus_all_identity_bound_descendants",
            "identity_tuple": ["process_id", "process_birth_ticks"],
            "parent_only_sample_cannot_satisfy_factor_solve_visibility": True,
            "at_least_one_successful_sample_must_observe_factor_solve_child_identity": True,
            "incomplete_unreadable_query_or_identity_ambiguity_is_failure": True,
            "sample_or_retry_truncation_is_failure": True,
            "resource_stop_is_failure": True,
            "same_birth_descendants_must_all_be_absent_before_terminal_pass": True,
            "pid_reuse_does_not_count_as_original_identity_alive": True,
        },
        "minimum_available_physical_before_spawn_bytes": margin + int(1.5 * 1024**3),
        "minimum_commit_headroom_before_spawn_bytes": margin + 2 * 1024**3,
        "prospective_envelope_arithmetic_pass": True,
        "eight_gib_execution_proven": False,
    }


def _accuracy_gates() -> Mapping[str, object]:
    analytic_floor = 5.214985896880959e-08
    return {
        "comparison_input_bindings": {
            "h2_result_file_sha256": "b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55",
            "h2_result_payload_sha256": "5704f3feb5bb90b6e38f8ed3ec67fc690339e9b7f0c1722d40a977295e82593e",
            "h2_numerical_payload_sha256": "bd2f6542a93e3a7adc62f4435540752651ddf1cff84226319b4aa5e8dbdc0be5",
            "h2_result_status": "passed_AV_BS_h2_stage_only_pending_h4_preregistration",
            "h2_ignored_artifact_live_revalidated_by_manifest": False,
            "h2_commitment_note": "not_revalidated_in_manifest_clean_clone",
            "future_execution_requires_exact_h2_evidence_restore_or_committed_compact_certificate": True,
            "analytic_targets": "frozen_Ys_abs_m_for_signed_modes_minus4_through_plus4",
        },
        "solve_backward_residual": {
            "applies_to": "each_of_512_rhs_for_each_original_unscaled_A_background_II_and_A_conductor_II",
            "equilibrated_solve_formula": "Aeq*z=Dr*rhs; x=Dc*z",
            "raw_residual_vector_formula": "r=A_raw*x-rhs",
            "numerator_formula": "maxabs(r)",
            "denominator_formula": "norminf(A_raw)*maxabs(x)+maxabs(rhs)",
            "maximum": 1.0e-10,
            "numerator_denominator_and_result_must_be_finite": True,
            "denominator_must_be_strictly_positive": True,
            "zero_denominator_disposition": "BLOCKED_AV_BS_SOLVE",
        },
        "condition_estimate": {
            "applies_to": ["A_background_II_Aeq", "A_conductor_II_Aeq"],
            "inverse_linear_operator": {
                "matvec_and_matmat": "factor.solve(value,trans='N')",
                "rmatvec_and_rmatmat": "factor.solve(value,trans='H')",
                "same_resident_factor_required": True,
                "dtype": "complex128",
            },
            "per_seed_inverse_estimate_formula": "onenormest(inverse_linear_operator,t=4,itmax=10)",
            "aggregate_formula": "kappa1_u=norm1(Aeq)*max(inverse_estimate_seed_1729,inverse_estimate_seed_2718)*2**-53",
            "binary64_unit_roundoff_u": 2.0**-53,
            "maximum": 1.0e-8,
            "estimator": "onenormest",
            "onenormest_t": 4,
            "onenormest_itmax": 10,
            "frozen_seed_identities": [1729, 2718],
            "seed_application": "save_numpy_rng_state_seed_each_estimate_then_restore_rng_state",
            "minimum_average_or_single_seed_substitution_forbidden": True,
            "matrix_norm_inverse_norm_kappa_and_product_must_be_finite_and_positive": True,
            "rigorous_upper_bound": False,
        },
        "assembly_transpose": {
            "matrices": ["K_II", "M_II", "A_background_II_raw", "A_conductor_II_raw"],
            "per_matrix_formulas": {
                "K_II": "normF(K_II-K_II.T)/normF(K_II)",
                "M_II": "normF(M_II-M_II.T)/normF(M_II)",
                "A_background_II_raw": "normF(A_background_II_raw-A_background_II_raw.T)/normF(A_background_II_raw)",
                "A_conductor_II_raw": "normF(A_conductor_II_raw-A_conductor_II_raw.T)/normF(A_conductor_II_raw)",
            },
            "maximum": 1.0e-12,
            "numerator_denominator_and_result_must_be_finite": True,
            "denominator_must_be_strictly_positive": True,
        },
        "operator_scaling": {
            "all_Y_and_Y_reverse_entries_must_be_finite": True,
            "operator_floor_S_times_m": "max(1e-18,1e-10*maxabs(Y))",
            "relative_denominator": "max(normF(Y),512*operator_floor_S_times_m)",
            "floor_and_denominator_must_be_finite_and_strictly_positive": True,
        },
        "independent_reverse_operator": {
            "metric": "normF(Y_reverse-Y.T)/max(normF(Y),512*operator_floor_S_times_m)",
            "maximum": 1.0e-12,
            "numerator_and_result_must_be_finite": True,
        },
        "raw_reciprocity": {
            "metric": "normF(Y-Y.T)/max(normF(Y),512*operator_floor_S_times_m)",
            "maximum": 1.0e-8,
            "numerator_and_result_must_be_finite": True,
        },
        "raw_passivity": {
            "hermitian_gate_matrix_only": "H=(Y+Y.conj().T)/2",
            "minimum_hermitian_eigenvalue": ">=-max(operator_floor_S_times_m,1e-9*norm2(Y))",
            "eigenvalues_norm_threshold_and_comparison_must_be_finite": True,
            "H_must_not_replace_or_repair_raw_Y": True,
        },
        "modal_trace_normalization": {
            "signed_modes": list(range(-4, 5)),
            "boundary_vector_formula": "e_m[n]=exp(1j*m*theta_n)",
            "Yhat_formula": "(e_m.conj().T@Y@e_m)/(e_m.conj().T@M_Gamma@e_m)",
            "trace_denominator_must_be_finite_real_and_strictly_positive": True,
            "numerator_Yhat_and_all_intermediates_must_be_finite": True,
        },
        "modal_field_residual": {
            "signed_modes": list(range(-4, 5)),
            "raw_residual_formula": "r=A_conductor_II*u+A_conductor_I_Gamma*v",
            "denominator_formula": "norminf(A_conductor_II)*maxabs(u)+maxabs(A_conductor_I_Gamma*v)",
            "relative_formula": "maxabs(r)/denominator",
            "maximum": 1.0e-10,
            "numerator_denominator_result_and_fields_must_be_finite": True,
            "denominator_must_be_strictly_positive": True,
        },
        "modal_power": {
            "signed_modes": list(range(-4, 5)),
            "boundary_power_formula_W_per_m": "0.5*Re(v.conj().T@Y@v)",
            "volume_power_formula_W_per_m": "0.5*sigma*Re(u.conj().T@(M_II@u+M_I_Gamma@v)+v.conj().T@(M_Gamma_I@u+M_Gamma_Gamma@v))",
            "relative_mismatch_formula": "abs(P_boundary-P_volume)/max(abs(P_boundary),abs(P_volume),1e-18)",
            "relative_mismatch_maximum": 1.0e-8,
            "volume_power_minimum_W_per_m": -1.0e-18,
            "powers_denominator_mismatch_and_all_intermediates_must_be_finite": True,
            "denominator_must_be_strictly_positive": True,
        },
        "fine_analytic_signed_modes": {
            "signed_modes": list(range(-4, 5)),
            "mode_floor_formula_S": "max(1e-12,1e-10*maxabs(frozen_analytic_targets))",
            "frozen_analytic_mode_floor_S": analytic_floor,
            "relative_formula": "abs(Yhat_h4-analytic)/max(abs(analytic),frozen_analytic_mode_floor_S)",
            "relative_maximum_each_mode": 5.0e-3,
            "phase_eligible_formula": "abs(Yhat_h4)>=10*frozen_analytic_mode_floor_S and abs(analytic)>=10*frozen_analytic_mode_floor_S",
            "phase_error_formula_deg": "abs(angle(Yhat_h4/analytic,deg=True))",
            "eligible_phase_maximum_deg": 0.25,
            "eligible_phase_mode_count_minimum": 1,
            "analytic_target_formula": "analytic=Ys_abs_m",
            "ineligible_phase_output": None,
            "ineligible_phase_disposition": "JSON_null_not_zero_not_pass_substitution",
            "floor_targets_errors_ratios_and_phases_must_be_finite": True,
        },
        "h2_to_h4_signed_mode_convergence": {
            "signed_modes": list(range(-4, 5)), "equal_weight_per_signed_mode": True,
            "frozen_h2_comparison_floor_S": analytic_floor,
            "relative_formula": "abs(Yhat_h4-Yhat_h2)/max(abs(Yhat_h4),frozen_h2_comparison_floor_S)",
            "rms_formula": "sqrt(sum(relative_error_m**2 for m in signed_modes)/9)",
            "maximum_formula": "max(relative_error_m for m in signed_modes)",
            "rms_relative_maximum": 5.0e-3,
            "max_relative_maximum": 1.0e-2,
            "phase_eligible_formula": "abs(Yhat_h2)>=10*frozen_h2_comparison_floor_S and abs(Yhat_h4)>=10*frozen_h2_comparison_floor_S",
            "phase_error_formula_deg": "abs(angle(Yhat_h4/Yhat_h2,deg=True))",
            "eligible_phase_maximum_deg": 0.25,
            "eligible_phase_mode_count_minimum": 1,
            "ineligible_phase_output": None,
            "ineligible_phase_disposition": "JSON_null_not_zero_not_pass_substitution",
            "floor_values_errors_rms_max_ratios_and_phases_must_be_finite": True,
        },
        "plus_minus_mode_degeneracy": {
            "mode_pairs": [[1, -1], [2, -2], [3, -3], [4, -4]],
            "plus_minus_mode_floor_S": analytic_floor,
            "relative_formula": "abs(Yhat_m-Yhat_minus_m)/max(abs(Yhat_m),abs(Yhat_minus_m),plus_minus_mode_floor_S)",
            "maximum": 1.0e-8,
            "numerator_denominator_result_and_modes_must_be_finite": True,
            "denominator_must_be_strictly_positive": True,
        },
        "global_fail_closed_numeric_policy": {
            "all_required_values_and_intermediates_must_be_finite": True,
            "zero_or_nonfinite_required_denominator_is_failure": True,
            "missing_signed_mode_or_missing_eligible_phase_family_is_failure": True,
        },
        "raw_result_policy": {
            "symmetrization_forbidden": True, "plus_minus_averaging_forbidden": True,
            "clipping_forbidden": True, "gate_on_raw_values_before_presentation": True,
            "post_hoc_threshold_floor_seed_mode_or_eligibility_tuning_forbidden": True,
        },
    }


def run_manifest() -> Mapping[str, object]:
    _validate_lifecycle_and_provenance()
    sidecar = _sidecar_descriptor()
    resource = _resource_policy()
    gates = _accuracy_gates()
    return {
        "schema": SCHEMA, "program": PROGRAM, "case_id": CASE_ID,
        "stage": "manifest", "status": "preregistered_H4_P1_contract_only_no_solve",
        "authorization_state": "not_authorized", "execution_authorized": False,
        "token_state": "absent", "token_absence_validated": True,
        "mandatory_stage_pass": False,
        "terminal_evidence_complete": False, "authoritative_terminal_evidence": False,
        "authoritative_stage_pass": False,
        "current_terminal_classification": "token_absent_no_terminal_evidence",
        "current_terminal_validation_error": None,
        "provenance": {
            "required_ancestor_commit": POSTPASS_DOCS_COMMIT,
            "base_contract_commit": BASE_CONTRACT_COMMIT,
            "original_token_commit": ORIGINAL_TOKEN_COMMIT,
            "consumed_token_commit": CONSUMED_TOKEN_COMMIT,
            "retirement_commit": RETIREMENT_COMMIT,
            "token_path": TOKEN_PATH,
            "original_token_raw_sha256": ORIGINAL_TOKEN_RAW_SHA256,
            "original_token_canonical_sha256": ORIGINAL_TOKEN_CANONICAL_SHA256,
            "consumed_tombstone_sha256": CONSUMED_TOMBSTONE_SHA256,
            "runtime_preregistration_doc_sha256": "954a91feb1639b0eb0cb2f62c2068af7a388fc7d55fc2370e909ca32da7968b7",
            "postpass_preregistration_doc_sha256": POSTPASS_DOC_BINDINGS["docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md"],
            "core_bindings": dict(CORE_BINDINGS),
            "postpass_document_bindings": dict(POSTPASS_DOC_BINDINGS),
            "current_token_absence_validated_in_worktree_index_and_head": True,
        },
        "historical_parent_evidence": {
            "attempt_ordinal": 11, "review_token_id": REVIEW_TOKEN_ID,
            "classification": "consumed_v2_authoritative_sealed_pass",
            "status": "consumed_v2_authoritative_sealed_pass_no_next_stage_authorization",
            "terminal_authority_source": "retained_terminal_seal_plus_full_chain_as_audited_at_postpass_docs_commit",
            "published_result_pre_seal_authoritative": False,
            "consumed_tombstone_pre_seal_authoritative": False,
            "terminal_seal_historical_authoritative": True,
            "factor_order": ["A_background_II", "A_conductor_II"],
            "factor_certificates": [
                {
                    "name": "A_background_II", "input_nnz": 204545,
                    "L_nnz": 1570627, "U_nnz": 1615176,
                    "fill_ratio": 15.57507150015889, "exported_bytes": 64219892,
                    "portable_structural_bytes": 77466936,
                    "native_portable_bytes": 81218256, "factor_wall_seconds": 1.3330735,
                    "matrix_bindings": dict(MATRIX_CONTRACT["A_background_II"]),
                },
                {
                    "name": "A_conductor_II", "input_nnz": 219393,
                    "L_nnz": 1702688, "U_nnz": 1716333,
                    "fill_ratio": 15.584002224318917, "exported_bytes": 68884252,
                    "portable_structural_bytes": 83064168,
                    "native_portable_bytes": 86058168, "factor_wall_seconds": 1.4459152,
                    "matrix_bindings": dict(MATRIX_CONTRACT["A_conductor_II"]),
                },
            ],
            "rhs_count": 0, "factor_solve_called": False,
            "physics_solve_performed": False,
            "evidence_hash_commitments": {key: dict(value) for key, value in EVIDENCE_COMMITMENTS.items()},
            "ignored_evidence_files_opened_by_manifest": False,
            "evidence_hash_commitments_live_revalidated": False,
            "commitment_validation_note": "not_revalidated_in_manifest_clean_clone",
            "future_execution_requires_exact_evidence_restore_or_committed_compact_terminal_certificate": True,
            "historical_factor_only_not_h4_physics": True,
            "process_and_resource_summary": {
                "public_runner_process_id": 43900,
                "inner_process_id": 62976,
                "factor_process_id": 63268,
                "finalizer_process_id": 39580,
                "consumer_process_id": 60012,
                "exit_codes": {
                    "public_runner": 0,
                    "inner": 0,
                    "factor": 0,
                    "finalizer": 0,
                    "consumer": 0,
                },
                "inner_samples_successful_total": [243, 243],
                "inner_wall_seconds": 34.0036179,
                "inner_peak_working_set_bytes": 510849024,
                "inner_peak_private_bytes": 1954021376,
                "inner_peak_lifetime_commit_bytes": 2358079488,
                "outer_samples_successful_total": [677, 677],
                "outer_retry_event_count": 47,
                "outer_retry_events_truncated": False,
                "outer_wall_seconds": 94.5411269,
                "outer_peak_working_set_bytes": 513384448,
                "outer_peak_private_bytes": 1958121472,
                "outer_peak_lifetime_commit_bytes": 2358079488,
                "outer_recorded_identity_count": 164,
                "broader_unique_identity_count": 207,
                "broader_same_birth_live_count": 0,
                "broader_pid_absent_count": 206,
                "broader_pid_reused_count": 1,
                "broader_birth_unreadable_count": 0,
                "minimum_host_available_physical_bytes": 43885748224,
                "eight_gib_execution_evidence": False,
            },
        },
        "execution_contract": {
            "prospective_stage": "primary-h4", "prospective_stage_available": False,
            "one_process_factor_solve_and_sidecar_lifetime_required": True,
            "interior_nodes": 31489, "boundary_nodes": 512,
            "rhs_count_per_factor": 512, "maximum_rhs_batch": 4,
            "rhs_batch_column_order": "contiguous_ascending_0_through_511_without_skip_or_repeat",
            "factor_order": ["A_background_II", "A_conductor_II"],
            "matrix_contract": {
                **{key: value for key, value in MATRIX_CONTRACT.items()
                   if key not in {"A_background_II", "A_conductor_II"}},
                "A_background_II": dict(MATRIX_CONTRACT["A_background_II"]),
                "A_conductor_II": dict(MATRIX_CONTRACT["A_conductor_II"]),
            },
            "equilibration_contract": {
                "row_max_formula": "row_max[i]=max_j(abs(A_raw[i,j]))",
                "row_scale_formula": "Dr=diag(1/row_max)",
                "row_scaled_matrix_formula": "A_row=Dr@A_raw",
                "column_max_formula": "column_max[j]=max_i(abs(A_row[i,j]))",
                "column_scale_formula": "Dc=diag(1/column_max)",
                "equilibrated_matrix_formula": "Aeq=Dr@A_raw@Dc",
                "raw_matrix_scales_and_Aeq_must_be_finite": True,
                "zero_or_nonfinite_row_or_column_max_disposition": "BLOCKED_AV_BS_SOLVE",
                "deterministic_order": "row_max_then_row_scale_then_column_max_then_column_scale",
            },
            "factorization_contract": {
                "call": "scipy.sparse.linalg.splu(Aeq,permc_spec='COLAMD',diag_pivot_thresh=1.0,options={'Equil':False})",
                "permc_spec": "COLAMD",
                "diag_pivot_thresh": 1.0,
                "superlu_equil": False,
                "factorizations_exactly_once_per_factor": True,
                "fallback_reordering_pivot_threshold_or_tuning_forbidden": True,
            },
            "raw_system_solve_contract": {
                "equilibrated_rhs_formula": "rhs_eq=Dr@rhs_raw",
                "factor_solve_formula": "z=factor.solve(rhs_eq)",
                "raw_solution_recovery_formula": "x=Dc@z",
                "residual_evaluated_on_original_A_raw_rhs_raw_and_recovered_x": True,
                "solve_fallback_or_iterative_refinement_not_preregistered": True,
            },
            "one_factor_resident_at_a_time": True,
            "same_factor_must_solve_all_512_rhs_in_batches_before_release": True,
            "factor_rebuild_for_same_matrix_forbidden": True,
            "retain_two_interior_extensions_until_operator_assembly": True,
            "persist_L_U_or_permutations_forbidden": True,
            "rehydrate_L_U_or_permutations_forbidden": True,
            "release_factor_L_U_permutations_scales_and_matrix_before_next_factor": True,
            "sidecar_contains_L_U_or_permutations": False,
            "standalone_synthetic_solve_result_forbidden": True,
            "full_outputs": ["Y", "Y_reverse", "M9_interior"],
            "separate_future_authorization_required": True,
        },
        "binary_sidecar_descriptor": sidecar,
        "binary_sidecar_descriptor_sha256": _canonical_sha(sidecar),
        "accuracy_gates": gates,
        "accuracy_gates_sha256": _canonical_sha(gates),
        "resource_policy": resource,
        "resource_policy_sha256": _canonical_sha(resource),
        "failure_code_allowlist": list(FAILURE_CODES),
        "priority_order": ["PowerSI_accuracy_and_independent_oracle_validity", "8_GiB_laptop_feasibility"],
        "forbidden_operations": {
            "token_created": False, "claim_created": False,
            "rhs_generated": False, "factorization_performed": False,
            "factor_solve_called": False, "linear_solve_called": False,
            "extensions_generated": False, "boundary_operator_generated": False,
            "Y_generated": False, "Y_reverse_generated": False,
            "modal_response_generated": False, "power_generated": False,
            "sidecar_created": False, "public_result_written": False,
            "physics_solve_performed": False, "finalizer_called": False,
            "consumer_called": False,
        },
        "rhs_generated": False, "factorization_attempted": False,
        "factorization_performed": False,
        "linear_solve_called": False, "factor_solve_called": False,
        "factor_solve_performed": False, "physics_solve_performed": False,
        "accuracy_gates_evaluated": False, "resource_gate_evaluated": False,
        "sidecar_creation_performed": False, "sidecar_written": False,
        "power_si_comparison_performed": False,
        "power_si_accuracy_claim": False, "eight_gib_fit_claim": False,
        "available_stages": ["manifest"],
        "available_solve_stages": [],
        "token_gated_stages": [],
        "unavailable_stages": ["primary-h4", "withheld", "EQ0"],
        "next_stage_authorized": False,
    }


def _manifest_wrapper(payload: Mapping[str, object]) -> Mapping[str, object]:
    base = {"payload": payload, "payload_sha256": _canonical_sha(payload)}
    return {**base, "wrapper_sha256": _canonical_sha(base)}


def validated_manifest_wrapper() -> Mapping[str, object]:
    wrapper = _manifest_wrapper(run_manifest())
    if wrapper["payload_sha256"] != EXPECTED_MANIFEST_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H4-P1 manifest payload checksum mismatch")
    if wrapper["wrapper_sha256"] != EXPECTED_MANIFEST_WRAPPER_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H4-P1 manifest wrapper checksum mismatch")
    return wrapper


def _failure_wrapper(error: AvBsError, stage: str) -> Mapping[str, object]:
    code = error.code if error.code in FAILURE_CODES else "BLOCKED_AV_BS_RESULT_SCHEMA"
    payload = {
        "schema": FAILURE_SCHEMA, "program": PROGRAM, "case_id": CASE_ID,
        "stage": stage, "status": code, "authorization_state": "not_authorized",
        "execution_authorized": False,
        "token_state": "unvalidated_or_unexpected",
        "token_absence_validated": False,
        "mandatory_stage_pass": False, "failure_codes": [code],
        "detail": error.detail, "rhs_generated": False,
        "factorization_performed": False, "factor_solve_called": False,
        "physics_solve_performed": False, "sidecar_creation_performed": False,
        "next_stage_authorized": False,
    }
    return _manifest_wrapper(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SPD Decap PI Evaluator v0.22.0 H4-P1 manifest-only contract")
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
    print(json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
