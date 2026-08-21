from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from scipy.sparse import csc_matrix


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "tools" / "research"
sys.path.insert(0, str(RESEARCH))

import av_bs1_boundary_schur as h1
import av_bs1_boundary_schur_h4_p0r as p0r


@pytest.fixture(scope="module")
def manifest() -> dict[str, object]:
    return dict(p0r.run_manifest())


@pytest.fixture(scope="module")
def h4_p0_payload() -> dict[str, object]:
    return copy.deepcopy(p0r.p0.run_manifest())


def test_manifest_is_not_authorized_and_has_no_factor_or_physics(manifest: dict[str, object]) -> None:
    assert manifest["program"] == "SPD Decap PI Evaluator v0.22.0"
    assert manifest["status"] == "preregistered_H4_P0R_contract_only_no_factor"
    assert manifest["authorization_state"] == "not_authorized"
    assert manifest["factorization_performed"] is False
    assert manifest["physics_solve_performed"] is False
    assert manifest["available_solve_stages"] == []
    assert manifest["next_stage_authorized"] is False


def test_parent_provenance_and_h2_tombstone_are_bound(manifest: dict[str, object]) -> None:
    bindings = manifest["bindings"]
    assert bindings["h4_p0_commit"] == "8f40fe5696496edb2cb73086927f833ded5e0d5e"
    assert bindings["h4_p0_manifest_payload_sha256"] == p0r.H4_P0_PAYLOAD_SHA256
    assert bindings["fixture_sha256"] == p0r.H4_P0_BINDINGS["fixture_sha256"]
    assert bindings["static_test_sha256"] == p0r.H4_P0_BINDINGS["static_test_sha256"]
    assert bindings["h2_artifact_file_sha256"] == p0r.H2_ARTIFACT_FILE_SHA256
    assert bindings["h2_consumed_tombstone_sha256"] == p0r.H2_TOMBSTONE_SHA256
    tombstone = p0r._validate_h2_tombstone()
    assert tombstone["authorization_state"] == "consumed"
    assert tombstone["uses_remaining"] == 0
    assert tombstone["next_stage_authorized"] is False


def test_exact_h4_factor_inputs_and_equilibration(manifest: dict[str, object]) -> None:
    inputs = manifest["matrix_inputs"]
    assert inputs["interior_nodes"] == 31489
    assert inputs["frequency_hz"] == 100000.0
    assert inputs["sigma_s_per_m"] == 59600000.0
    assert inputs["omega_rad_per_s"] == 628318.5307179586
    assert inputs["omega_sigma"] == 37447784430790.33
    assert inputs["matrix_contract_sha256"] == "89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46"
    assert inputs["matrices"] == p0r.EXPECTED_MATRIX_INPUTS
    for name in ("A_background_II", "A_conductor_II"):
        record = inputs["matrices"][name]
        assert record["equilibrated_row_max_abs_min"] == 1.0
        assert record["equilibrated_row_max_abs_max"] == 1.0
        assert record["equilibrated_column_max_abs_min"] == 1.0
        assert record["equilibrated_column_max_abs_max"] == 1.0


def test_factor_contract_is_sequential_and_has_no_rhs(manifest: dict[str, object]) -> None:
    contract = manifest["factorization_contract"]
    assert contract["factor_order"] == ["A_background_II", "A_conductor_II"]
    assert contract["sequential_one_factor_resident"] is True
    assert contract["delete_factor_matrix_scales_and_collect_between_factors"] is True
    assert contract["permc_spec"] == "COLAMD"
    assert contract["diag_pivot_thresh"] == 1.0
    assert contract["superlu_equil"] is False
    assert contract["rhs_count"] == 0
    assert contract["batch_size"] == 0
    assert contract["factor_solve_forbidden"] is True
    assert contract["onenormest_forbidden"] is True
    assert contract["factor_bytes_portable_structural_formula"] == "24*(L_nnz+U_nnz)+8*(4*n+2)"
    assert contract["success_does_not_authorize_h4_p1"] is True


def test_resource_policy_retains_frozen_combined_envelope(manifest: dict[str, object]) -> None:
    policy = manifest["resource_policy"]
    assert policy == p0r.RESOURCE_POLICY
    assert manifest["resource_policy_sha256"] == p0r._canonical_sha(p0r.RESOURCE_POLICY)
    assert policy["tree_working_set_stop_bytes"] == 4 * 1024**3
    assert policy["tree_private_stop_bytes"] == 5 * 1024**3
    assert policy["tree_commit_stop_bytes"] == 5 * 1024**3
    assert policy["wall_stop_seconds"] == 900
    assert policy["minimum_available_physical_before_spawn_bytes"] == 5081474791
    assert policy["minimum_commit_headroom_before_spawn_bytes"] == 5618345703
    assert policy["retained_p0_guarded_envelope_with_25pct_margin_bytes"] == 3470862055
    assert policy["factor_fit_unproven"] is True
    assert p0r._validate_resource_policy() == p0r.EXPECTED_RESOURCE_POLICY_SHA256


def test_one_use_lifecycle_and_future_schemas_are_frozen(manifest: dict[str, object]) -> None:
    schemas = manifest["future_schemas"]
    assert schemas["review_token"] == p0r.REVIEW_SCHEMA
    assert schemas["claim"] == p0r.CLAIM_SCHEMA
    assert schemas["guard"] == p0r.GUARD_SCHEMA
    assert schemas["resource"] == p0r.RESOURCE_SCHEMA
    assert schemas["numerical"] == p0r.NUMERICAL_SCHEMA
    assert schemas["result"] == p0r.RESULT_SCHEMA
    assert schemas["consumed_tombstone"] == p0r.TOMBSTONE_SCHEMA
    lifecycle = manifest["one_use_lifecycle"]
    assert lifecycle["uses_remaining_before_attempt"] == 1
    assert lifecycle["atomic_create_new_claim_before_spawn"] is True
    assert lifecycle["consume_after_every_launched_child_outcome"] is True
    assert lifecycle["consumed_uses_remaining"] == 0
    assert lifecycle["replay_rejected"] is True
    assert lifecycle["next_stage_authorized"] is False
    token_fields = set(manifest["future_review_token_required_fields"])
    assert {"expires_utc", "p0r_preregistration_commit", "matrix_contract_sha256"} <= token_fields
    claim_fields = set(manifest["future_claim_required_fields"])
    assert {"review_token_sha256", "preflight_payload_sha256", "guard_nonce"} <= claim_fields
    result_fields = set(manifest["future_result_required_fields"])
    assert {"factor_certificates", "forbidden_operation_flags", "next_stage_authorized"} <= result_fields
    resource_fields = set(manifest["future_resource_required_fields"])
    assert {"execution_tree_includes_runner", "peak", "final_system", "thresholds"} <= resource_fields
    peak_fields = set(manifest["future_resource_nested_required_fields"]["peak"])
    assert {"tree_private_commit_bytes", "tree_committed_pagefile_bytes", "system_commit_headroom_min_bytes"} <= peak_fields


def test_forbidden_operations_are_explicit(manifest: dict[str, object]) -> None:
    forbidden = manifest["forbidden_operations"]
    assert set(forbidden.values()) == {False}
    assert manifest["failure_code_allowlist"] == [
        "BLOCKED_AV_BS_FACTOR",
        "BLOCKED_AV_BS_MESH_HASH",
        "BLOCKED_AV_BS_RESOURCE",
        "BLOCKED_AV_BS_RESULT_SCHEMA",
    ]
    assert "primary-h4-p0r" in manifest["unavailable_stages"]
    assert "primary-h4" in manifest["unavailable_stages"]


def test_fixture_ast_has_no_factor_solve_or_dense_materialization() -> None:
    source_path = RESEARCH / "av_bs1_boundary_schur_h4_p0r.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    banned = {"splu", "spsolve", "factorized", "solve", "onenormest", "toarray", "todense", "inv"}
    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    assert calls.isdisjoint(banned)
    parser = p0r._parser()
    stage = next(action for action in parser._actions if action.dest == "stage")
    assert stage.choices == ("manifest",)


def test_runner_is_manifest_only_and_powershell_parses() -> None:
    runner = RESEARCH / "run_av_bs1_h4_p0r_stage.ps1"
    text = runner.read_text(encoding="utf-8")
    assert 'ValidateSet("manifest")' in text
    for forbidden in ("primary-h4-p0r", "Start-Process", "splu", "factor", "solve"):
        assert forbidden not in text
    command = (
        "$errors=$null;$tokens=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{runner}',[ref]$tokens,[ref]$errors)|Out-Null;"
        "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("filename", "label"),
    (
        ("av_bs1_boundary_schur_h4_p0.py", "fixture_sha256"),
        ("run_av_bs1_h4_p0_stage.ps1", "runner_sha256"),
        ("test_research_av_bs1_boundary_schur_h4_p0.py", "static_test_sha256"),
        ("T1_AV_BOUNDARY_SCHUR_H4_P0_PREREG.md", "preregistration_doc_sha256"),
    ),
)
def test_parent_file_tamper_fails_before_matrix_reconstruction(
    monkeypatch: pytest.MonkeyPatch, filename: str, label: str
) -> None:
    original = h1._file_sha256

    def wrong_file(path: Path) -> str:
        if path.name == filename:
            return "0" * 64
        return original(path)

    monkeypatch.setattr(h1, "_file_sha256", wrong_file)
    with pytest.raises(h1.AvBsError, match=f"working H4-P0 {label} mismatch"):
        p0r._validate_h4_p0_commit()


def test_parent_subpayload_tamper_is_rejected(h4_p0_payload: dict[str, object]) -> None:
    altered = copy.deepcopy(h4_p0_payload)
    altered["mesh"]["nodes"] += 1
    with pytest.raises(h1.AvBsError, match="H4-P0 manifest payload mismatch"):
        p0r._validate_h4_p0_subpayloads(altered)


def test_h2_tombstone_field_tamper_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tombstone = json.loads(p0r.H2_TOMBSTONE_PATH.read_text(encoding="utf-8"))
    tombstone["uses_remaining"] = 1
    path = tmp_path / "tampered-h2-tombstone.json"
    path.write_text(json.dumps(tombstone, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    monkeypatch.setattr(p0r, "H2_TOMBSTONE_PATH", path)
    monkeypatch.setattr(p0r, "H2_TOMBSTONE_SHA256", h1._file_sha256(path))
    with pytest.raises(h1.AvBsError, match="H2 tombstone uses_remaining mismatch"):
        p0r._validate_h2_tombstone()


@pytest.mark.parametrize("name", ("K_II", "M_II", "A_background_II", "A_conductor_II"))
def test_matrix_tamper_is_rejected_without_factorization(name: str) -> None:
    dtype = "complex128" if name.startswith("A_") else "float64"
    matrix = csc_matrix([[1.0 + 0.0j]]) if dtype == "complex128" else csc_matrix([[1.0]])
    with pytest.raises(h1.AvBsError, match=f"H4-P0R {name} input mismatch"):
        p0r._matrix_record(
            name, matrix, dtype=dtype, equilibrate=name.startswith("A_")
        )


def test_resource_policy_tamper_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = dict(p0r.RESOURCE_POLICY)
    policy["p0r_factor_only_raw_total_bytes"] += 1
    monkeypatch.setattr(p0r, "RESOURCE_POLICY", policy)
    with pytest.raises(h1.AvBsError, match="resource p0r_factor_only_raw_total_bytes mismatch"):
        p0r._validate_resource_policy()


def test_manifest_wrapper_is_canonical_and_failure_is_fail_closed(manifest: dict[str, object]) -> None:
    wrapper = h1._wrapper(manifest)
    assert wrapper["payload_sha256"] == p0r._canonical_sha(manifest)
    assert wrapper["payload_sha256"] == p0r.EXPECTED_MANIFEST_PAYLOAD_SHA256
    assert p0r.validated_manifest_wrapper()["payload_sha256"] == p0r.EXPECTED_MANIFEST_PAYLOAD_SHA256
    output = p0r._failure_wrapper(h1.AvBsError("BLOCKED_AV_BS_RESOURCE", "probe"), "manifest")
    payload = output["payload"]
    assert output["payload_sha256"] == p0r._canonical_sha(payload)
    assert payload["mandatory_stage_pass"] is False
    assert payload["factorization_performed"] is False
    assert payload["physics_solve_performed"] is False
    assert payload["next_stage_authorized"] is False
    json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False)


def test_failure_wrapper_normalizes_codes_outside_the_exact_allowlist() -> None:
    output = p0r._failure_wrapper(
        h1.AvBsError("BLOCKED_AV_BS_RECIPROCITY", "parent diagnostic"), "manifest"
    )["payload"]
    assert output["status"] == "BLOCKED_AV_BS_RESULT_SCHEMA"
    assert output["failure_codes"] == ["BLOCKED_AV_BS_RESULT_SCHEMA"]
    assert output["detail"].startswith("normalized_from=BLOCKED_AV_BS_RECIPROCITY;")


def test_new_contract_files_are_utf8_lf_without_bom() -> None:
    paths = (
        RESEARCH / "av_bs1_boundary_schur_h4_p0r.py",
        RESEARCH / "run_av_bs1_h4_p0r_stage.ps1",
        Path(__file__),
        ROOT / "docs" / "evaluation-research" / "T1_AV_BOUNDARY_SCHUR_H4_P0R_PREREG.md",
    )
    for path in paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw
        raw.decode("utf-8", errors="strict")
