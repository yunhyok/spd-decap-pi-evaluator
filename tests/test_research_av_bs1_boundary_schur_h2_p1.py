from __future__ import annotations

import ast
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import av_bs1_boundary_schur_h2_p1 as p1

FIXTURE = ROOT / "tools" / "research" / "av_bs1_boundary_schur_h2_p1.py"
RUNNER = ROOT / "tools" / "research" / "run_av_bs1_h2_p1_stage.ps1"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(p1.canonical_bytes(value))


def _token() -> dict[str, object]:
    return {
        "schema": p1.TOKEN_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "authorized_stage": "primary-h2",
        "authorization_state": "authorized",
        "uses_remaining": 1,
        "next_stage_authorized": False,
        "review_disposition": "approved_h2_primary_only_stage_evaluable",
        "review_scope": "authorize_h2_one_mesh_after_clean_p1_checkout",
        "review_token_id": "0123456789abcdef0123456789abcdef",
        "reviewed_utc": "2026-08-15T12:00:00Z",
        "independent_audits": [
            "Sol mathematical and fail-closed contract review",
            "Terra Windows runner and process-tree safety review",
            "Luna schema, resource, and bounded-test review",
        ],
        "p1_preregistration_commit": "1" * 40,
        **p1._token_expected_bindings(),
    }


def _claim(
    root: Path,
    token: dict[str, object],
    token_path: Path,
    git_head: str,
) -> tuple[Path, dict[str, object]]:
    relative = Path("validation-output/av-bs1/claims") / f"{token['review_token_id']}.json"
    value = {
        "schema": p1.CLAIM_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "primary-h2",
        "claim_relative_path": relative.as_posix(),
        "token_id": token["review_token_id"],
        "review_token_sha256": p1._file_sha256(token_path),
        "review_binding_sha256": p1._review_binding_sha256(token),
        "fixture_sha256": token["p1_fixture_sha256"],
        "runner_sha256": token["p1_runner_sha256"],
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "git_head": git_head,
        "preflight_payload_sha256": "2" * 64,
        "claimed_utc": "2026-08-15T12:01:00Z",
    }
    path = root / relative
    _write_json(path, value)
    return path, value


def _resource(
    token: dict[str, object],
    review_path: Path,
    claim_path: Path,
    claim: dict[str, object],
    child_path: Path,
    child_exit: int,
    guard_path: Path,
) -> dict[str, object]:
    policy = p1._guard_policy()
    return {
        "schema": p1.RESOURCE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "primary-h2",
        "runner_sha256": token["p1_runner_sha256"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": p1._file_sha256(review_path),
        "resource_guard_policy_sha256": p1._canonical_sha(policy),
        "guard_contract_sha256": p1._file_sha256(guard_path),
        "execution_tree_includes_runner": True,
        "monitor_ok": True,
        "monitor_error": None,
        "stop_reason": None,
        "successful_tree_sample_count": 2,
        "wall_seconds": 1.0,
        "wall_stop_seconds": 900,
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": p1._file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "child_exit_code": child_exit,
        "child_stdout_sha256": p1._file_sha256(child_path),
        "mandatory_resource_gate_pass": True,
        "thresholds": {key: policy[key] for key in (
            "tree_ws_stop_bytes", "tree_private_stop_bytes", "tree_commit_stop_bytes",
            "commit_headroom_floor_bytes", "available_physical_floor_bytes",
        )},
        "baseline": {},
        "peak": {
            "tree_working_set_bytes": 1,
            "tree_private_commit_bytes": 1,
            "tree_committed_pagefile_bytes": 1,
            "system_commit_headroom_min_bytes": 3 * 1024**3,
            "available_physical_min_bytes": 3 * 1024**3,
        },
        "final_system": {
            "commit_headroom_bytes": 3 * 1024**3,
            "available_physical_bytes": 3 * 1024**3,
        },
    }


def _guard(
    token: dict[str, object],
    token_path: Path,
    claim_path: Path,
    claim: dict[str, object],
    nonce: str = "abc",
) -> dict[str, object]:
    policy = p1._guard_policy()
    return {
        "schema": p1.GUARD_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "primary-h2",
        "nonce": nonce,
        "parent_pid": os.getppid(),
        "monitor_ok": True,
        "runner_sha256": token["p1_runner_sha256"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": p1._file_sha256(token_path),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": p1._file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "resource_guard_policy_sha256": p1._canonical_sha(policy),
        "baseline_commit_headroom_bytes": 3 * 1024**3,
        "baseline_available_physical_bytes": 3 * 1024**3,
        **{key: policy[key] for key in (
            "poll_interval_ms", "wall_stop_seconds", "tree_ws_stop_bytes",
            "tree_private_stop_bytes", "tree_commit_stop_bytes",
            "commit_headroom_floor_bytes", "available_physical_floor_bytes",
        )},
    }


def _success_numerical(
    token: dict[str, object],
    token_path: Path,
    claim_path: Path,
    claim: dict[str, object],
    guard_path: Path,
    p0_payload: dict[str, object],
    h1_result: dict[str, object],
    git_head: str,
) -> dict[str, object]:
    interior = p0_payload["partition"]["interior_nodes"]
    boundary = p0_payload["partition"]["boundary_nodes"]
    excluded = {
        "boundary_edges_sha256", "mass_nnz", "mass_sha256",
        "trace_mass_nnz", "trace_mass_sha256", "trace_support_sha256",
    }
    canonical = {
        key: value for key, value in p0_payload["assembly"].items() if key not in excluded
    }
    nodes, triangles, _ = p1.p0.seed_mesh_h2()
    _, boundary_edges = p1.h1.edge_data(triangles)
    interior_ids, gamma, gamma_map = p1.p0._partition(len(nodes), boundary_edges)
    trace_mass = p1.h1._assemble_trace_mass(nodes, boundary_edges, gamma_map)
    _, volume_mass = p1.h1._assemble_volume(nodes, triangles)
    mass_ii = volume_mass[interior_ids, :][:, interior_ids].tocsc()
    mass_ig = volume_mass[interior_ids, :][:, gamma].tocsc()
    mass_gi = volume_mass[gamma, :][:, interior_ids].tocsc()
    mass_gg = volume_mass[gamma, :][:, gamma].tocsc()
    interior_modal_fields = np.zeros(
        (interior, len(p1.SIGNED_M9)), dtype=np.complex128
    )
    interior_modal_fields[0, :] = 1.0
    xy = np.asarray(nodes, dtype=np.float64)[gamma]
    theta = np.mod(np.arctan2(xy[:, 1], xy[:, 0]), 2.0 * math.pi)
    boundary_vectors = [np.exp(1j * mode * theta) for mode in p1.SIGNED_M9]
    volume_powers: list[float] = []
    for mode_index, vector in enumerate(boundary_vectors):
        interior_field = interior_modal_fields[:, mode_index]
        mass_i = mass_ii @ interior_field + mass_ig @ vector
        mass_g = mass_gi @ interior_field + mass_gg @ vector
        volume_powers.append(
            0.5
            * p1.h1.SIGMA_S_PER_M
            * float(np.real(np.vdot(interior_field, mass_i) + np.vdot(vector, mass_g)))
        )
    y = np.zeros((boundary, boundary), dtype=np.complex128)
    for vector, volume_power in zip(boundary_vectors, volume_powers, strict=True):
        norm_squared = float(np.real(np.vdot(vector, vector)))
        y += (2.0 * volume_power / (norm_squared * norm_squared)) * np.outer(
            vector, vector.conj()
        )
    y_reverse = y.T.copy()
    operator = p1.h1._array_blob(y)
    reverse_operator = p1.h1._array_blob(y_reverse)
    targets = {mode: p1.h1._analytic_target(mode) for mode in p1.SIGNED_M9}
    mode_floor = max(1.0e-12, 1.0e-10 * max(abs(value) for value in targets.values()))
    modes: list[dict[str, object]] = []
    values: dict[int, complex] = {}
    power_errors: list[float] = []
    for mode_index, mode in enumerate(p1.SIGNED_M9):
        vector = boundary_vectors[mode_index]
        trace_denominator = complex(np.vdot(vector, trace_mass @ vector))
        numeric = complex(np.vdot(vector, y @ vector) / trace_denominator.real)
        target = targets[mode]
        boundary_power = 0.5 * float(np.real(np.vdot(vector, y @ vector)))
        interior_field = interior_modal_fields[:, mode_index]
        volume_power = volume_powers[mode_index]
        power_error = abs(boundary_power - volume_power) / max(
            abs(boundary_power), abs(volume_power), 1.0e-18
        )
        modes.append({
            "m": mode,
            "numeric_S": [numeric.real, numeric.imag],
            "analytic_S": [target.real, target.imag],
            "analytic_relative_trend_only": abs(numeric - target) / max(abs(target), mode_floor),
            "analytic_phase_deg_trend_only": (
                float(abs(np.angle(numeric / target, deg=True)))
                if abs(numeric) >= 10.0 * mode_floor and abs(target) >= 10.0 * mode_floor
                else None
            ),
            "trace_mass_denominator_m": trace_denominator.real,
            "boundary_power_W_per_m": boundary_power,
            "volume_power_W_per_m": volume_power,
            "power_mismatch": power_error,
        })
        values[mode] = numeric
        power_errors.append(power_error)
    degeneracy = max(
        abs(values[mode] - values[-mode])
        / max(abs(values[mode]), abs(values[-mode]), mode_floor)
        for mode in range(1, 5)
    )
    trend = p1._h_to_h2_trend(h1_result, modes)
    y_floor = max(1.0e-18, 1.0e-10 * float(np.max(np.abs(y))))
    operator_denominator = max(float(np.linalg.norm(y)), boundary * y_floor)
    reverse = float(np.linalg.norm(y_reverse - y.T) / operator_denominator)
    reciprocity = float(np.linalg.norm(y - y.T) / operator_denominator)
    eig_min = float(np.linalg.eigvalsh(0.5 * (y + y.conj().T))[0])
    passivity_tol = max(y_floor, 1.0e-9 * float(np.linalg.norm(y, 2)))

    def certificate(name: str, seed: str) -> dict[str, object]:
        return {
            "name": name,
            "raw_shape": [interior, interior],
            "rhs_count": boundary,
            "batch_size": p1.BOUNDARY_RHS_BATCH,
            "permc_spec": "COLAMD",
            "superlu_equil": False,
            "diag_pivot_thresh": 1.0,
            "onenormest_t": 4,
            "onenormest_itmax": 10,
            "onenormest_seeds": [1729, 2718],
            "backward_residual_max": 1.0e-15,
            "kappa1_u": 1.0e-12,
            "L_nnz": 1,
            "U_nnz": 1,
            "factor_bytes_lower_estimate": 1,
            "raw_nnz": 1,
            "extension_sha256": seed * 64,
            "row_scale_sha256": seed * 64,
            "column_scale_sha256": seed * 64,
            "perm_r_sha256": seed * 64,
            "perm_c_sha256": seed * 64,
        }

    return {
        "schema": p1.NUMERICAL_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "h2",
        "git_head": git_head,
        "p1_preregistration_commit": token["p1_preregistration_commit"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "runner_sha256": token["p1_runner_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": p1._file_sha256(token_path),
        "review_binding_sha256": p1._review_binding_sha256(token),
        "guard_contract_sha256": p1._file_sha256(guard_path),
        "resource_guard_policy_sha256": p1._canonical_sha(p1._guard_policy()),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": p1._file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "bindings": p1._token_expected_bindings(),
        "runtime": list(p1.h1.EXPECTED_RUNTIME),
        "geometry": {
            "radius_m": p1.h1.RADIUS_M,
            "frequency_hz": p1.h1.FREQUENCY_HZ,
            "sigma_s_per_m": p1.h1.SIGMA_S_PER_M,
            "mu_h_per_m": p1.h1.MU0_H_PER_M,
            "phasor": "exp(+j omega t)",
        },
        "mesh": {
            **p0_payload["mesh"],
            "interior_nodes": interior,
            "boundary_nodes": boundary,
            "raw_stiffness_nnz": p0_payload["assembly"]["raw_stiffness_nnz"],
            "raw_stiffness_sha256": p0_payload["assembly"]["raw_stiffness_sha256"],
            "canonical_stiffness_nnz": p0_payload["assembly"]["canonical_stiffness_nnz"],
            "canonical_stiffness_sha256": p0_payload["assembly"]["canonical_stiffness_sha256"],
            "mass_nnz": p0_payload["assembly"]["mass_nnz"],
            "mass_sha256": p0_payload["assembly"]["mass_sha256"],
            "trace_mass_nnz": p0_payload["assembly"]["trace_mass_nnz"],
            "trace_mass_sha256": p0_payload["assembly"]["trace_mass_sha256"],
            "cyclic_lineage": p0_payload["cyclic_lineage"],
        },
        "assembly": {
            "operator_order": ["background", "conductor"],
            "canonicalization": canonical,
            "one_factor_resident": True,
            "batch_size": p1.BOUNDARY_RHS_BATCH,
            "extension_storage": "interior_only_boundary_identity_implicit",
            "extension_shapes": [[interior, boundary], [interior, boundary]],
            "raw_transpose_relative": {
                "K_full": 0.0,
                "M_full": 0.0,
                "M_Gamma": 0.0,
                "A_background_full": 0.0,
                "A_conductor_full": 0.0,
            },
        },
        "solves": {
            "background": certificate("background", "a"),
            "conductor": certificate("conductor", "b"),
        },
        "operators": {"Y": operator, "Y_reverse": reverse_operator},
        "power_certificate": {
            "schema": "AV-BS1-h2-M9-volume-power-v1",
            "signed_modes": list(p1.SIGNED_M9),
            "source_conductor_extension_sha256": "b" * 64,
            "integration": "0.5*sigma*Re(uH(MIIu+MIg*v)+vH(MGIu+MGGv))",
            "residual": "maxabs(ApII*u+ApIG*v)/(norminf(ApII)*maxabs(u)+maxabs(ApIG*v))",
            "field_backward_residuals": [0.0] * len(p1.SIGNED_M9),
            "field_backward_residual_max": 0.0,
            "interior_modal_fields": p1.h1._array_blob(interior_modal_fields),
        },
        "metrics": {
            "signed_modes": list(p1.SIGNED_M9),
            "Y_floor_S_m": y_floor,
            "Y_mode_floor_S": mode_floor,
            "reverse_order_relative": reverse,
            "raw_reciprocity_relative": reciprocity,
            "min_hermitian_eigenvalue_S_m": eig_min,
            "passivity_tolerance_S_m": passivity_tol,
            "power_mismatch_max": max(power_errors),
            "m_plus_minus_relative_trend_only": degeneracy,
            "modes": modes,
            "h_to_h2_trend": trend,
            "fine_analytic_pass": None,
            "mesh_convergence_pass": None,
            "final_circle_pass": None,
        },
        "resource_preflight": p0_payload["resource_preflight"],
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


def _synthetic_finalizer_context(p0_payload: dict[str, object]) -> dict[str, object]:
    context = dict(p1._reconstruct_h2_finalizer_context(p0_payload))
    interior = p0_payload["partition"]["interior_nodes"]
    boundary = p0_payload["partition"]["boundary_nodes"]
    diagonal_ids = np.arange(1, interior, dtype=np.int64)
    context["conductor_ii"] = p1.csc_matrix(
        (np.ones(interior - 1), (diagonal_ids, diagonal_ids)),
        shape=(interior, interior),
        dtype=np.complex128,
    )
    context["conductor_ig"] = p1.csc_matrix(
        (interior, boundary), dtype=np.complex128
    )
    return context


def _success_attempt_evidence(tmp_path: Path) -> dict[str, object]:
    token = _token()
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    preflight_payload = {"preflight": True}
    claim["preflight_payload_sha256"] = p1._wrapper(preflight_payload)["payload_sha256"]
    _write_json(claim_path, claim)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    p0_payload = p1._p0_manifest()
    h1_result = p1._validate_h1_artifact(p1.H1_ARTIFACT_PATH)
    numerical = _success_numerical(
        token,
        token_path,
        claim_path,
        claim,
        guard_path,
        p0_payload,
        h1_result,
        "3" * 40,
    )
    child_path = tmp_path / "child.json"
    _write_json(child_path, p1._wrapper(numerical))
    resource = _resource(
        token, token_path, claim_path, claim, child_path, 0, guard_path
    )
    resource_path = tmp_path / "resource.json"
    _write_json(resource_path, resource)
    return {
        "token": token,
        "token_path": token_path,
        "claim": claim,
        "claim_path": claim_path,
        "guard_path": guard_path,
        "p0_payload": p0_payload,
        "finalizer_context": _synthetic_finalizer_context(p0_payload),
        "h1_result": h1_result,
        "preflight_payload": preflight_payload,
        "child_path": child_path,
        "resource_path": resource_path,
    }


def _patch_success_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    evidence: dict[str, object],
) -> None:
    bindings = dict(p1._token_expected_bindings())
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: evidence["token"])
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_validate_h1_artifact", lambda path: evidence["h1_result"])
    monkeypatch.setattr(p1, "_validate_h1_tombstone", lambda path: {})
    monkeypatch.setattr(p1, "_p0_manifest", lambda: evidence["p0_payload"])
    monkeypatch.setattr(
        p1,
        "_reconstruct_h2_finalizer_context",
        lambda payload: evidence["finalizer_context"],
    )
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        p1, "run_preflight_primary_h2", lambda path: evidence["preflight_payload"]
    )
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)


def test_manifest_freezes_h1_h2_p0_and_has_no_solve() -> None:
    manifest = p1.run_manifest()
    assert manifest["schema"] == p1.FIXTURE_SCHEMA
    assert manifest["status"] == "preregistered_H2_P1_token_missing_no_solve"
    assert manifest["authorization_state"] == "not_authorized"
    assert manifest["factorization_performed"] is False
    assert manifest["physics_solve_performed"] is False
    assert manifest["available_solve_stages"] == []
    assert manifest["h2_p0_mesh"]["sha256"] == "34eb4f9cadcefd0b20cff3ae6c483dbee4e412ca11d0ff1a8c2c6f49ec7896a9"
    assert manifest["h2_p0_assembly"]["canonical_stiffness_sha256"] == "8fcbbc2bd18c9ba098382cb61c45bf6880cb47886ed2578ee48def0f7a34eef9"
    assert manifest["h2_p0_assembly"]["mass_sha256"] == "69aefda6d301e9c7174aae8d1d557439749d288ddd57301291c21c7da5f581fc"
    assert manifest["h2_p0_assembly"]["trace_mass_sha256"] == "2de9980d426f88a1422588bae3bbd57cba2e2f7eccc216e2509e7a83e62ca45b"
    assert manifest["h2_p0_resource_preflight"]["total_with_25pct_margin_bytes"] == 2_553_838_095
    assert manifest["dependencies"]["h1_mode_view_sha256"] == p1.H1_MODE_VIEW_SHA256
    assert manifest["execution_contract"]["trend_schema"] == p1.TREND_SCHEMA
    assert manifest["execution_contract"]["wall_stop_seconds"] == 900
    assert manifest["execution_contract"]["volume_power_certificate_schema"] == "AV-BS1-h2-M9-volume-power-v1"
    assert manifest["execution_contract"]["volume_power_interior_field_bytes"] == 1_124_496
    assert manifest["finalizer_contract"]["post_attempt_tombstone_required"] is True
    assert manifest["finalizer_contract"]["next_stage_authorized"] is False


def test_finalizer_context_reconstructs_frozen_sparse_matrices_without_solve() -> None:
    payload = p1._p0_manifest()
    context = p1._reconstruct_h2_finalizer_context(payload)
    assert context["trace_mass"].shape == (256, 256)
    assert context["mass_ii"].shape == (7_809, 7_809)
    assert context["mass_ig"].shape == (7_809, 256)
    assert context["conductor_ii"].shape == (7_809, 7_809)
    assert context["conductor_ig"].shape == (7_809, 256)
    assert p1.h1._sparse_sha256(context["trace_mass"]) == payload["assembly"]["trace_mass_sha256"]


@pytest.mark.parametrize("stage", ["preflight-primary-h2", "primary-h2", "finalize-primary-h2"])
def test_executable_stages_are_token_blocked_before_physics(stage: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(FIXTURE), "--stage", stage],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)["payload"]
    assert payload["schema"] == p1.FAILURE_SCHEMA
    assert payload["status"] == "BLOCKED_AV_BS_RESULT_SCHEMA"
    if p1.REVIEW_TOKEN_PATH.is_file():
        token = json.loads(p1.REVIEW_TOKEN_PATH.read_text(encoding="utf-8"))
        assert token["schema"] == p1.TOMBSTONE_SCHEMA
        assert payload["detail"] == "review token schema mismatch"
    else:
        assert "token is missing" in payload["detail"]
    if stage == "finalize-primary-h2":
        assert payload["authorization_state"] == "claimed_attempt_failed"
        assert payload["factorization_attempted"] is None
        assert payload["factorization_performed"] is None
        assert payload["physics_solve_performed"] is None
    else:
        assert payload["factorization_performed"] is False
        assert payload["physics_solve_performed"] is False


def test_h1_artifact_resource_operator_mode_and_tombstone_bindings() -> None:
    result = p1._validate_h1_artifact(p1.H1_ARTIFACT_PATH)
    tombstone = p1._validate_h1_tombstone(p1.H1_TOMBSTONE_PATH)
    assert result["status"] == p1.H1_STATUS
    assert result["resource_report_sha256"] == p1.H1_RESOURCE_SHA256
    assert result["numerical"]["operators"]["Y"]["sha256"] == p1.H1_OPERATOR_Y_SHA256
    assert result["numerical"]["operators"]["Y_reverse"]["sha256"] == p1.H1_OPERATOR_Y_REVERSE_SHA256
    assert p1._canonical_mode_view(result["numerical"]) == p1.H1_MODE_VIEW_SHA256
    assert tombstone["consumed_review_token_sha256"] == p1.H1_CONSUMED_TOKEN_SHA256


def test_h1_semantic_tamper_fails_after_rebinding_outer_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = json.loads(p1.H1_ARTIFACT_PATH.read_text(encoding="utf-8"))
    wrapper["payload"]["resource"]["mandatory_resource_gate_pass"] = False
    wrapper["payload_sha256"] = p1._canonical_sha(wrapper["payload"])
    path = tmp_path / "tampered-h1.json"
    _write_json(path, wrapper)
    monkeypatch.setattr(p1, "H1_ARTIFACT_SHA256", p1._file_sha256(path))
    monkeypatch.setattr(p1, "H1_PAYLOAD_SHA256", wrapper["payload_sha256"])
    with pytest.raises(p1.AvBsError, match="resource binding mismatch"):
        p1._validate_h1_artifact(path)


def test_mode_view_requires_exact_order_uniqueness_and_finiteness() -> None:
    numerical = deepcopy(p1._validate_h1_artifact(p1.H1_ARTIFACT_PATH)["numerical"])
    numerical["metrics"]["modes"][0], numerical["metrics"]["modes"][1] = numerical["metrics"]["modes"][1], numerical["metrics"]["modes"][0]
    with pytest.raises(p1.AvBsError, match="order mismatch"):
        p1._canonical_mode_view(numerical)
    numerical = deepcopy(p1._validate_h1_artifact(p1.H1_ARTIFACT_PATH)["numerical"])
    numerical["metrics"]["modes"][0]["numeric_S"][0] = float("nan")
    with pytest.raises(p1.AvBsError, match="non-finite"):
        p1._canonical_mode_view(numerical)


def test_h_to_h2_trend_is_signed_m9_non_gating_and_uses_h2_denominator() -> None:
    h1_result = p1._validate_h1_artifact(p1.H1_ARTIFACT_PATH)
    h2_modes = deepcopy(h1_result["numerical"]["metrics"]["modes"])
    h2_modes[4]["numeric_S"][0] += 1.0
    trend = p1._h_to_h2_trend(h1_result, h2_modes)
    assert trend["schema"] == p1.TREND_SCHEMA
    assert trend["gate_applied"] is False
    assert trend["gate_pass"] is None
    assert trend["authorization_effect"] == "none"
    assert trend["signed_modes"] == list(range(-4, 5))
    row = trend["modes"][4]
    h2 = complex(*row["h2_numeric_S"])
    h = complex(*row["h_numeric_S"])
    assert row["denominator_S"] == max(abs(h2), p1.H1_MODE_FLOOR_S)
    assert row["relative_trend_only"] == abs(h2 - h) / row["denominator_S"]
    assert trend["eligible_phase_modes"] == 9


def test_review_token_requires_all_exact_bindings_and_metadata(tmp_path: Path) -> None:
    token = _token()
    path = tmp_path / "token.json"
    _write_json(path, token)
    assert p1._validate_review_token(path)["review_token_id"] == token["review_token_id"]
    for key in (
        "p1_runner_sha256",
        "p0_static_test_sha256",
        "h1_resource_report_sha256",
        "h1_operator_y_sha256",
        "h1_consumed_tombstone_file_sha256",
        "h2_p1_resource_guard_policy_sha256",
    ):
        damaged = dict(token)
        damaged[key] = "0" * 64
        _write_json(path, damaged)
        with pytest.raises(p1.AvBsError, match=key):
            p1._validate_review_token(path)
    damaged = dict(token)
    damaged["review_token_id"] = "not-hex"
    _write_json(path, damaged)
    with pytest.raises(p1.AvBsError, match="token ID"):
        p1._validate_review_token(path)


def test_claim_binds_canonical_path_token_lineage_and_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = _token()
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    assert p1._validate_claim(claim_path, token, token_path, "3" * 40) == claim
    claim["fixture_sha256"] = "0" * 64
    _write_json(claim_path, claim)
    with pytest.raises(p1.AvBsError, match="claim binding mismatch"):
        p1._validate_claim(claim_path, token, token_path, "3" * 40)


def test_guard_reads_claim_and_rejects_hash_or_parent_tamper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = _token()
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    policy = p1._guard_policy()
    guard = {
        "schema": p1.GUARD_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "primary-h2",
        "nonce": "abc",
        "parent_pid": os.getppid(),
        "monitor_ok": True,
        "runner_sha256": token["p1_runner_sha256"],
        "fixture_sha256": token["p1_fixture_sha256"],
        "review_token_id": token["review_token_id"],
        "review_token_sha256": p1._file_sha256(token_path),
        "claim_relative_path": claim["claim_relative_path"],
        "claim_sha256": p1._file_sha256(claim_path),
        "preflight_payload_sha256": claim["preflight_payload_sha256"],
        "resource_guard_policy_sha256": p1._canonical_sha(policy),
        "baseline_commit_headroom_bytes": 3 * 1024**3,
        "baseline_available_physical_bytes": 3 * 1024**3,
        **{key: policy[key] for key in (
            "poll_interval_ms", "wall_stop_seconds", "tree_ws_stop_bytes",
            "tree_private_stop_bytes", "tree_commit_stop_bytes",
            "commit_headroom_floor_bytes", "available_physical_floor_bytes",
        )},
    }
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, guard)
    validated, _ = p1._validate_guard(guard_path, "abc", token, claim_path, token_path, "3" * 40)
    assert validated["claim_sha256"] == p1._file_sha256(claim_path)
    guard["claim_sha256"] = "0" * 64
    _write_json(guard_path, guard)
    with pytest.raises(p1.AvBsError, match="guard claim_sha256 mismatch"):
        p1._validate_guard(guard_path, "abc", token, claim_path, token_path, "3" * 40)


def test_resource_gate_uses_peak_minima_and_fails_closed_on_malformed_int() -> None:
    policy = p1._guard_policy()
    resource = {
        "wall_seconds": 10.0,
        "successful_tree_sample_count": 2,
        "monitor_ok": True,
        "monitor_error": None,
        "stop_reason": None,
        "mandatory_resource_gate_pass": True,
        "thresholds": {key: policy[key] for key in (
            "tree_ws_stop_bytes", "tree_private_stop_bytes", "tree_commit_stop_bytes",
            "commit_headroom_floor_bytes", "available_physical_floor_bytes",
        )},
        "baseline": {},
        "peak": {
            "tree_working_set_bytes": 1,
            "tree_private_commit_bytes": 1,
            "tree_committed_pagefile_bytes": 1,
            "system_commit_headroom_min_bytes": 3 * 1024**3,
            "available_physical_min_bytes": 3 * 1024**3,
        },
        "final_system": {
            "commit_headroom_bytes": 3 * 1024**3,
            "available_physical_bytes": 3 * 1024**3,
        },
    }
    assert p1._resource_gate(resource) == (True, [])
    resource["peak"]["system_commit_headroom_min_bytes"] = 1
    assert "minimum_commit_headroom" in p1._resource_gate(resource)[1]
    resource["successful_tree_sample_count"] = True
    with pytest.raises(p1.AvBsError, match="not an integer"):
        p1._resource_gate(resource)


def test_finalizer_preserves_child_failure_code_and_resource_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    child_payload = {
        "schema": p1.FAILURE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "primary-h2",
        "mandatory_stage_pass": False,
        "next_stage_authorized": False,
        "status": "BLOCKED_AV_BS_PASSIVITY",
        "authorization_state": "claimed_attempt_failed",
        "failure_codes": ["BLOCKED_AV_BS_PASSIVITY"],
        "detail": "synthetic passivity failure",
        "factorization_attempted": True,
        "factorization_performed": True,
        "physics_solve_performed": True,
    }
    child_path = tmp_path / "child.json"
    _write_json(child_path, p1._wrapper(child_payload))
    preflight_payload = {"preflight": True}
    claim["preflight_payload_sha256"] = p1._wrapper(preflight_payload)["payload_sha256"]
    _write_json(claim_path, claim)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    resource = _resource(token, token_path, claim_path, claim, child_path, 2, guard_path)
    resource_path = tmp_path / "resource.json"
    _write_json(resource_path, resource)
    bindings = dict(p1._token_expected_bindings())
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_validate_h1_artifact", lambda path: {})
    monkeypatch.setattr(p1, "_validate_h1_tombstone", lambda path: {})
    monkeypatch.setattr(p1, "_p0_manifest", lambda: {})
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "run_preflight_primary_h2", lambda path: preflight_payload)
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)
    result = p1.finalize_primary_h2(
        resource_path, child_path, token_path, guard_path, "abc"
    )["payload"]
    assert result["mandatory_stage_pass"] is False
    assert result["failure_codes"] == ["BLOCKED_AV_BS_PASSIVITY"]
    assert result["resource_report_sha256"] == p1._file_sha256(resource_path)
    assert result["next_stage_authorized"] is False


@pytest.mark.parametrize("forged", [False, True])
def test_consumed_failure_result_is_fully_validated_and_never_trusts_forged_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forged: bool
) -> None:
    token = _token()
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    child_payload = {
        "schema": p1.FAILURE_SCHEMA,
        "program": p1.PROGRAM,
        "case_id": p1.CASE_ID,
        "stage": "primary-h2",
        "mandatory_stage_pass": False,
        "next_stage_authorized": False,
        "status": "BLOCKED_AV_BS_PASSIVITY",
        "authorization_state": "claimed_attempt_failed",
        "failure_codes": ["BLOCKED_AV_BS_PASSIVITY"],
        "detail": "synthetic passivity failure",
        "factorization_attempted": True,
        "factorization_performed": True,
        "physics_solve_performed": True,
    }
    child_path = tmp_path / "child.json"
    _write_json(child_path, p1._wrapper(child_payload))
    preflight_payload = {"preflight": True}
    claim["preflight_payload_sha256"] = p1._wrapper(preflight_payload)["payload_sha256"]
    _write_json(claim_path, claim)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    resource = _resource(token, token_path, claim_path, claim, child_path, 2, guard_path)
    resource_path = tmp_path / "resource.json"
    _write_json(resource_path, resource)
    bindings = dict(p1._token_expected_bindings())
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_validate_h1_artifact", lambda path: {})
    monkeypatch.setattr(p1, "_validate_h1_tombstone", lambda path: {})
    monkeypatch.setattr(p1, "_p0_manifest", lambda: {})
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "run_preflight_primary_h2", lambda path: preflight_payload)
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)
    result_wrapper = p1.finalize_primary_h2(
        resource_path, child_path, token_path, guard_path, "abc"
    )
    if forged:
        result_payload = deepcopy(result_wrapper["payload"])
        forged_numerical = deepcopy(result_payload["numerical"])
        forged_numerical["failure_codes"] = ["BLOCKED_AV_BS_GARBAGE"]
        forged_numerical["status"] = "BLOCKED_AV_BS_GARBAGE"
        result_payload["numerical"] = forged_numerical
        result_payload["numerical_payload_sha256"] = p1._canonical_sha(forged_numerical)
        result_payload["factorization_performed"] = False
        result_payload["failure_codes"] = ["BLOCKED_AV_BS_GARBAGE"]
        result_payload["status"] = "BLOCKED_AV_BS_GARBAGE"
        result_wrapper = p1._wrapper(result_payload)
    result_path = tmp_path / "failure-result.json"
    _write_json(result_path, result_wrapper)
    tombstone = p1.consume_primary_h2_token(
        token_path,
        claim_path,
        guard_path,
        "abc",
        "completed_failure",
        result_path=result_path,
        resource_path=resource_path,
    )
    assert tombstone["authorization_state"] == "consumed"
    assert tombstone["mandatory_stage_pass"] is False
    if forged:
        assert tombstone["result_evidence_valid"] is False
        assert "BLOCKED_AV_BS_RESULT_SCHEMA" in tombstone["failure_codes"]
        assert "BLOCKED_AV_BS_GARBAGE" not in tombstone["failure_codes"]
    else:
        assert tombstone["result_evidence_valid"] is True
        assert tombstone["failure_codes"] == ["BLOCKED_AV_BS_PASSIVITY"]


def test_resource_stop_without_child_payload_preserves_resource_and_exit_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    preflight_payload = {"preflight": True}
    claim["preflight_payload_sha256"] = p1._wrapper(preflight_payload)["payload_sha256"]
    _write_json(claim_path, claim)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    unused_child = tmp_path / "child-never-produced.json"
    unused_child.write_bytes(b"unused")
    resource = _resource(
        token, token_path, claim_path, claim, unused_child, -9, guard_path
    )
    resource["child_stdout_sha256"] = None
    resource["mandatory_resource_gate_pass"] = False
    resource["stop_reason"] = "wall_stop_seconds"
    resource["wall_seconds"] = 901.0
    resource_path = tmp_path / "resource-stop.json"
    _write_json(resource_path, resource)
    bindings = dict(p1._token_expected_bindings())
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_validate_h1_artifact", lambda path: {})
    monkeypatch.setattr(p1, "_validate_h1_tombstone", lambda path: {})
    monkeypatch.setattr(p1, "_p0_manifest", lambda: {})
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "run_preflight_primary_h2", lambda path: preflight_payload)
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)

    result_wrapper = p1.finalize_primary_h2(
        resource_path, None, token_path, guard_path, "abc"
    )
    result = result_wrapper["payload"]
    assert result["status"] == "BLOCKED_AV_BS_RESOURCE"
    assert result["failure_codes"] == [
        "BLOCKED_AV_BS_RESULT_SCHEMA", "BLOCKED_AV_BS_RESOURCE"
    ]
    assert result["numerical"] is None
    assert result["numerical_payload_sha256"] is None
    assert result["factorization_performed"] is None
    assert result["physics_solve_performed"] is None
    result_path = tmp_path / "resource-stop-result.json"
    _write_json(result_path, result_wrapper)

    tombstone = p1.consume_primary_h2_token(
        token_path,
        claim_path,
        guard_path,
        "abc",
        "resource_stop",
        result_path=result_path,
        resource_path=resource_path,
    )
    assert tombstone["authorization_state"] == "consumed"
    assert tombstone["result_evidence_valid"] is True
    assert tombstone["resource_evidence_valid"] is True
    assert tombstone["reported_result_status"] == "BLOCKED_AV_BS_RESOURCE"
    assert tombstone["reported_failure_codes"] == [
        "BLOCKED_AV_BS_RESULT_SCHEMA", "BLOCKED_AV_BS_RESOURCE"
    ]
    assert tombstone["failure_codes"] == [
        "BLOCKED_AV_BS_RESULT_SCHEMA", "BLOCKED_AV_BS_RESOURCE"
    ]


def test_finalizer_accepts_only_fully_bound_success_with_null_future_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    p0_payload = p1._p0_manifest()
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    preflight_payload = {"preflight": True}
    claim["preflight_payload_sha256"] = p1._wrapper(preflight_payload)["payload_sha256"]
    _write_json(claim_path, claim)
    bindings = p1._token_expected_bindings()
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    h1_result = p1._validate_h1_artifact(p1.H1_ARTIFACT_PATH)
    numerical_payload = _success_numerical(
        token, token_path, claim_path, claim, guard_path,
        p0_payload, h1_result, "3" * 40,
    )
    child_path = tmp_path / "child.json"
    _write_json(child_path, p1._wrapper(numerical_payload))
    resource = _resource(token, token_path, claim_path, claim, child_path, 0, guard_path)
    resource_path = tmp_path / "resource.json"
    _write_json(resource_path, resource)
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_validate_h1_artifact", lambda path: h1_result)
    monkeypatch.setattr(p1, "_validate_h1_tombstone", lambda path: {})
    monkeypatch.setattr(p1, "_p0_manifest", lambda: p0_payload)
    finalizer_context = _synthetic_finalizer_context(p0_payload)
    monkeypatch.setattr(
        p1, "_reconstruct_h2_finalizer_context", lambda payload: finalizer_context
    )
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "run_preflight_primary_h2", lambda path: preflight_payload)
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)
    result = p1.finalize_primary_h2(
        resource_path, child_path, token_path, guard_path, "abc"
    )["payload"]
    assert result["mandatory_stage_pass"] is True
    assert result["status"] == "passed_AV_BS_h2_stage_only_pending_h4_preregistration"
    assert result["fine_analytic_pass"] is None
    assert result["mesh_convergence_pass"] is None
    assert result["final_circle_pass"] is None
    assert result["next_stage_authorized"] is False
    assert result["token_consumption_required"] is True


def test_success_body_recomputes_operator_metrics_and_rejects_self_rehashed_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    p0_payload = p1._p0_manifest()
    h1_result = p1._validate_h1_artifact(p1.H1_ARTIFACT_PATH)
    numerical = _success_numerical(
        token, token_path, claim_path, claim, guard_path,
        p0_payload, h1_result, "3" * 40,
    )
    finalizer_context = _synthetic_finalizer_context(p0_payload)
    monkeypatch.setattr(
        p1, "_reconstruct_h2_finalizer_context", lambda payload: finalizer_context
    )
    p1._validate_h2_success_body(numerical, p0_payload, h1_result)
    forged_metric = deepcopy(numerical)
    forged_metric["metrics"]["min_hermitian_eigenvalue_S_m"] += 1.0
    with pytest.raises(p1.AvBsError, match="recomputed passivity evidence mismatch"):
        p1._validate_h2_success_body(forged_metric, p0_payload, h1_result)
    forged_operator = deepcopy(numerical)
    boundary = p0_payload["partition"]["boundary_nodes"]
    forged_operator["operators"]["Y"] = p1.h1._array_blob(
        np.zeros((boundary, boundary), dtype=np.complex128)
    )
    with pytest.raises(p1.AvBsError, match="operator floor mismatch"):
        p1._validate_h2_success_body(forged_operator, p0_payload, h1_result)
    forged_volume = deepcopy(numerical)
    forged_volume["power_certificate"]["interior_modal_fields"] = p1.h1._array_blob(
        np.ones(
            (p0_payload["partition"]["interior_nodes"], len(p1.SIGNED_M9)),
            dtype=np.complex128,
        )
    )
    with pytest.raises(p1.AvBsError, match="mode -4 field residual evidence mismatch"):
        p1._validate_h2_success_body(forged_volume, p0_payload, h1_result)


def test_cli_and_source_forbid_implicit_or_dense_execution() -> None:
    source = FIXTURE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for stage in (
        "manifest", "preflight-primary-h2", "primary-h2",
        "finalize-primary-h2", "consume-primary-h2-token",
    ):
        assert f'"{stage}"' in source
    assert "np.linalg.inv" not in source
    assert ".toarray(" not in source and ".todense(" not in source
    banned = {"solve", "inv", "pinv", "lstsq"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            if isinstance(node.value.value, ast.Name) and node.value.value.id == "np" and node.value.attr == "linalg":
                assert node.attr not in banned
    assert source.index("_validate_review_token(review_path)", source.index("def run_primary_h2")) < source.index("_solve_extensions", source.index("def run_primary_h2"))
    run_start = source.index("def run_primary_h2")
    transpose_gate = source.index("if max(transpose.values())", run_start)
    first_slice = source.index("background_matrix =", run_start)
    first_solve = source.index("h1._solve_extensions", run_start)
    assert transpose_gate < first_slice < first_solve
    assert 'AvBsError("BLOCKED_AV_BS_RECIPROCITY"' in source
    assert 'AvBsError("BLOCKED_AV_BS_PASSIVITY"' in source
    assert "os.fsync" in source and "os.replace" in source and "NamedTemporaryFile" in source
    assert '"factorization_performed": False' in source


def test_failure_phase_truth_table_is_claim_and_solve_accurate() -> None:
    original = p1._EXECUTION_PHASE
    cases = {
        "preflight": ("not_authorized", False, False, False),
        "claimed_pre_factorization": ("claimed_attempt_failed", False, False, False),
        "background_factorization_attempted": ("claimed_attempt_failed", True, None, None),
        "background_extension_completed": ("claimed_attempt_failed", True, True, True),
        "conductor_factorization_attempted": ("claimed_attempt_failed", True, True, True),
        "extensions_completed": ("claimed_attempt_failed", True, True, True),
        "physics_assembled": ("claimed_attempt_failed", True, True, True),
    }
    try:
        for phase, expected in cases.items():
            p1._EXECUTION_PHASE = phase
            payload = p1._failure(
                p1.AvBsError("BLOCKED_AV_BS_SOLVE", "synthetic"), "primary-h2"
            )["payload"]
            assert (
                payload["authorization_state"],
                payload["factorization_attempted"],
                payload["factorization_performed"],
                payload["physics_solve_performed"],
            ) == expected
    finally:
        p1._EXECUTION_PHASE = original


def test_child_failure_schema_requires_scope_detail_and_phase_provenance() -> None:
    original = p1._EXECUTION_PHASE
    try:
        p1._EXECUTION_PHASE = "claimed_pre_factorization"
        payload = p1._failure(
            p1.AvBsError("BLOCKED_AV_BS_RECIPROCITY", "synthetic"), "primary-h2"
        )["payload"]
        assert p1._validate_child_failure_payload(payload) == ["BLOCKED_AV_BS_RECIPROCITY"]
        malformed = dict(payload)
        malformed.pop("detail")
        with pytest.raises(p1.AvBsError, match="detail missing"):
            p1._validate_child_failure_payload(malformed)
        prefixed_fake = dict(payload)
        prefixed_fake["failure_codes"] = ["BLOCKED_AV_BS_GARBAGE"]
        prefixed_fake["status"] = "BLOCKED_AV_BS_GARBAGE"
        with pytest.raises(p1.AvBsError, match="failure-code schema mismatch"):
            p1._validate_child_failure_payload(prefixed_fake)
    finally:
        p1._EXECUTION_PHASE = original


def test_finalizer_failure_schema_preserves_code_and_unknown_execution_provenance() -> None:
    payload = p1._failure(
        p1.AvBsError("BLOCKED_AV_BS_POWER", "synthetic finalizer failure"),
        "finalize-primary-h2",
    )["payload"]
    assert p1._validate_finalizer_failure_payload(payload) == ["BLOCKED_AV_BS_POWER"]
    assert payload["authorization_state"] == "claimed_attempt_failed"
    assert payload["factorization_attempted"] is None
    assert payload["factorization_performed"] is None
    assert payload["physics_solve_performed"] is None
    malformed = dict(payload)
    malformed["stage"] = "primary-h2"
    with pytest.raises(p1.AvBsError, match="finalizer failure provenance mismatch"):
        p1._validate_finalizer_failure_payload(malformed)
    prefixed_fake = dict(payload)
    prefixed_fake["failure_codes"] = ["BLOCKED_AV_BS_GARBAGE"]
    prefixed_fake["status"] = "BLOCKED_AV_BS_GARBAGE"
    with pytest.raises(p1.AvBsError, match="failure-code schema mismatch"):
        p1._validate_finalizer_failure_payload(prefixed_fake)


def test_finalizer_rejects_forged_guard_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    token_path = tmp_path / "token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    preflight_payload = {"preflight": True}
    claim["preflight_payload_sha256"] = p1._wrapper(preflight_payload)["payload_sha256"]
    _write_json(claim_path, claim)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    child_path = tmp_path / "child.json"
    _write_json(child_path, p1._wrapper({
        "schema": p1.FAILURE_SCHEMA,
        "failure_codes": ["BLOCKED_AV_BS_SOLVE"],
        "status": "BLOCKED_AV_BS_SOLVE",
    }))
    resource = _resource(token, token_path, claim_path, claim, child_path, 2, guard_path)
    resource["guard_contract_sha256"] = "4" * 64
    resource_path = tmp_path / "resource.json"
    _write_json(resource_path, resource)
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_validate_h1_artifact", lambda path: {})
    monkeypatch.setattr(p1, "_validate_h1_tombstone", lambda path: {})
    monkeypatch.setattr(p1, "_p0_manifest", lambda: {})
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "run_preflight_primary_h2", lambda path: preflight_payload)
    with pytest.raises(p1.AvBsError, match="resource guard checksum mismatch"):
        p1.finalize_primary_h2(resource_path, child_path, token_path, guard_path, "abc")


def test_token_consumption_atomically_replaces_authorized_token_and_blocks_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    bindings = p1._token_expected_bindings()
    validate_token = p1._validate_review_token
    token_path = tmp_path / "av_bs1_h2_p1_review_token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "_p0_manifest", lambda: {})
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)
    tombstone = p1.consume_primary_h2_token(
        token_path, claim_path, guard_path, "abc", "runner_exception"
    )
    assert tombstone["schema"] == p1.TOMBSTONE_SCHEMA
    assert tombstone["authorization_state"] == "consumed"
    assert tombstone["uses_remaining"] == 0
    assert tombstone["consumed_review_token_sha256"] != p1._file_sha256(token_path)
    assert json.loads(token_path.read_text(encoding="utf-8")) == tombstone
    assert not list(token_path.parent.glob(f".{token_path.name}.*.tmp"))
    monkeypatch.undo()
    with pytest.raises(p1.AvBsError, match="review token schema mismatch"):
        validate_token(token_path)
    with pytest.raises(p1.AvBsError, match="review token schema mismatch"):
        p1.consume_primary_h2_token(
            token_path, claim_path, guard_path, "abc", "runner_exception"
        )


def test_token_consumption_survives_malformed_failure_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    bindings = p1._token_expected_bindings()
    token_path = tmp_path / "av_bs1_h2_p1_review_token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    malformed_result = tmp_path / "malformed-result.json"
    malformed_result.write_bytes(b"not-json")
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "_p0_manifest", lambda: {})
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)
    tombstone = p1.consume_primary_h2_token(
        token_path,
        claim_path,
        guard_path,
        "abc",
        "finalizer_failure",
        result_path=malformed_result,
    )
    assert tombstone["authorization_state"] == "consumed"
    assert tombstone["result_evidence_valid"] is False
    assert tombstone["failure_codes"] == ["BLOCKED_AV_BS_RESULT_SCHEMA"]
    assert tombstone["consumption_evidence_errors"]


def test_token_consumption_preserves_finalizer_failure_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token()
    bindings = p1._token_expected_bindings()
    token_path = tmp_path / "av_bs1_h2_p1_review_token.json"
    _write_json(token_path, token)
    claim_path, claim = _claim(tmp_path, token, token_path, "3" * 40)
    guard_path = tmp_path / "guard.json"
    _write_json(guard_path, _guard(token, token_path, claim_path, claim))
    result_path = tmp_path / "finalizer-failure.json"
    _write_json(
        result_path,
        p1._failure(
            p1.AvBsError("BLOCKED_AV_BS_POWER", "synthetic finalizer failure"),
            "finalize-primary-h2",
        ),
    )
    monkeypatch.setattr(p1, "_validate_review_token", lambda path: token)
    monkeypatch.setattr(p1, "_validate_checkout", lambda current, path: "3" * 40)
    monkeypatch.setattr(p1, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(p1, "_p0_manifest", lambda: {})
    monkeypatch.setattr(p1, "_token_expected_bindings", lambda: bindings)
    tombstone = p1.consume_primary_h2_token(
        token_path,
        claim_path,
        guard_path,
        "abc",
        "completed_failure",
        result_path=result_path,
    )
    assert tombstone["result_evidence_valid"] is True
    assert tombstone["failure_codes"] == ["BLOCKED_AV_BS_POWER"]
    assert tombstone["factorization_performed"] is None
    assert tombstone["physics_solve_performed"] is None


def test_completed_pass_consumption_revalidates_full_result_and_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = _success_attempt_evidence(tmp_path)
    _patch_success_context(monkeypatch, tmp_path, evidence)
    result_wrapper = p1.finalize_primary_h2(
        evidence["resource_path"],
        evidence["child_path"],
        evidence["token_path"],
        evidence["guard_path"],
        "abc",
    )
    result_path = tmp_path / "final-result.json"
    _write_json(result_path, result_wrapper)
    tombstone = p1.consume_primary_h2_token(
        evidence["token_path"],
        evidence["claim_path"],
        evidence["guard_path"],
        "abc",
        "completed_pass",
        result_path=result_path,
        resource_path=evidence["resource_path"],
    )
    assert tombstone["consumption_validated_pass"] is True
    assert tombstone["mandatory_stage_pass"] is True
    assert tombstone["effective_attempt_status"] == "completed_pass"
    assert tombstone["failure_codes"] == []
    assert tombstone["consumption_evidence_errors"] == []


@pytest.mark.parametrize("tamper", ["embedded_resource", "numerical_lineage"])
def test_forged_completed_pass_is_consumed_and_demoted_to_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    evidence = _success_attempt_evidence(tmp_path)
    _patch_success_context(monkeypatch, tmp_path, evidence)
    result_wrapper = p1.finalize_primary_h2(
        evidence["resource_path"],
        evidence["child_path"],
        evidence["token_path"],
        evidence["guard_path"],
        "abc",
    )
    forged_payload = deepcopy(result_wrapper["payload"])
    if tamper == "embedded_resource":
        forged_payload["resource"]["peak"]["tree_working_set_bytes"] += 1
    else:
        forged_payload["numerical"]["runner_sha256"] = "0" * 64
        forged_payload["numerical_payload_sha256"] = p1._canonical_sha(
            forged_payload["numerical"]
        )
    forged_path = tmp_path / "forged-pass.json"
    _write_json(forged_path, p1._wrapper(forged_payload))
    original_token_sha = p1._file_sha256(evidence["token_path"])
    tombstone = p1.consume_primary_h2_token(
        evidence["token_path"],
        evidence["claim_path"],
        evidence["guard_path"],
        "abc",
        "completed_pass",
        result_path=forged_path,
        resource_path=evidence["resource_path"],
    )
    assert tombstone["consumed_review_token_sha256"] == original_token_sha
    assert tombstone["consumption_validated_pass"] is False
    assert tombstone["mandatory_stage_pass"] is False
    assert tombstone["effective_attempt_status"] == "completed_invalid_evidence"
    assert "BLOCKED_AV_BS_RESULT_SCHEMA" in tombstone["failure_codes"]
    assert any(item.startswith("pass:") for item in tombstone["consumption_evidence_errors"])
    assert json.loads(evidence["token_path"].read_text(encoding="utf-8")) == tombstone


def test_runner_is_literal_h1_style_and_claims_after_complete_preflight() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert '[ValidateSet("manifest", "primary-h2")]' in source
    assert "$wallStopSeconds = 900" in source
    assert "preflight-primary-h2" in source
    assert source.index("preflight-primary-h2") < source.index("[IO.FileMode]::CreateNew")
    assert source.index("$baseline = Get-SystemSample") < source.index("[IO.FileMode]::CreateNew")
    assert source.index("[IO.FileMode]::CreateNew") < source.index("Start-Process")
    assert '"--claim-file"' in source
    assert "claim_relative_path" in source and "preflight_payload_sha256" in source
    assert "$finalSystem = Get-SystemSample" in source
    assert "Get-Content $" not in source and "Invoke-Expression" not in source
    assert "Toolhelp32" in source and "GetPerformanceInfo" in source
    assert "childProcessHandle = $process.Handle" in source
    assert "ORPHANED_OBSERVED_PROCESS" in source
    assert '$attemptStarted = $true' in source
    assert '"consume-primary-h2-token"' in source
    assert source.index('$attemptStarted = $true') < source.index('"consume-primary-h2-token"')
    assert 'post-attempt token consumption failed' in source
    assert '$consumeWrapper.payload.consumption_validated_pass -ne $true' in source
    environment = {**os.environ, "AVBS_P1_RUNNER": str(RUNNER)}
    parsed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "$e=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile($env:AVBS_P1_RUNNER,[ref]$null,[ref]$e); if($e){exit 1}"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
