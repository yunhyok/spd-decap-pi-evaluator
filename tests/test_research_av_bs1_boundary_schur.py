from __future__ import annotations

import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from scipy.sparse import csc_matrix


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools" / "research" / "av_bs1_boundary_schur.py"
RUNNER = ROOT / "tools" / "research" / "run_av_bs1_stage.ps1"
SPEC = importlib.util.spec_from_file_location("research_av_bs1_boundary_schur", FIXTURE)
assert SPEC is not None and SPEC.loader is not None
avbs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(avbs)


def _review_token(path: Path) -> Path:
    payload = {
        "schema": avbs.REVIEW_SCHEMA,
        "program": avbs.PROGRAM,
        "case_id": avbs.CASE_ID,
        "authorized_stage": "primary-h",
        "prereg_commit": avbs.PREREG_COMMIT,
        "manifest_sha256": avbs.EXPECTED_MESH["h"][4],
        "prior_failed_artifact_sha256": avbs.H0_ARTIFACT_SHA256,
        "h1_cyclic_diagonal_sha256": avbs.EXPECTED_H_ASSEMBLY["cyclic_diagonal_sha256"],
        "fixture_sha256": avbs._file_sha256(FIXTURE),
        "runner_sha256": avbs._file_sha256(RUNNER),
        "next_stage_authorized": True,
        "review_disposition": "approved_static_h1_correction_only",
        "review_scope": "authorize_h1_primary_h_rerun_only_after_committed_clean_checkout",
        "independent_audits": [
            "Sol mathematical and fail-closed contract review",
            "Terra Windows runner and process-tree safety review",
            "Luna schema, resource, and bounded-test review",
        ],
        "review_evidence": {
            "manifest_payload_sha256": "e79cd30b88fbf339399b3b059ce958138a5b16ed90bc52cde1ed0f87c2dd9a95",
            "powershell_ast": "passed",
            "static_test_command": "python -m pytest -q tests/test_research_av_bs1_boundary_schur.py",
            "static_test_result": "16 passed",
            "h0_failure_status": "BLOCKED_AV_BS_MESH_HASH_before_factor",
            "h1_canonical_stiffness_sha256": avbs.EXPECTED_H_ASSEMBLY[
                "canonical_stiffness_sha256"
            ],
        },
        "reviewed_utc": "2026-08-15T00:00:00Z",
        "review_token_id": "0123456789abcdef0123456789abcdef",
    }
    path.write_bytes(avbs.canonical_bytes(payload))
    return path


def test_manifest_is_frozen_and_performs_no_physics_solve() -> None:
    manifest = avbs.run_manifest()
    assert manifest["program"] == "SPD Decap PI Evaluator v0.22.0"
    assert manifest["physics_solve_performed"] is False
    assert manifest["available_solve_stages"] == ["primary-h"]
    assert manifest["mesh"]["sha256"] == avbs.EXPECTED_MESH["h"][4]
    assert (
        manifest["mesh"]["nodes"],
        manifest["mesh"]["edges"],
        manifest["mesh"]["triangles"],
        manifest["mesh"]["boundary_edges"],
    ) == (2049, 6016, 3968, 128)


def test_manifest_cli_is_canonical_and_program_is_visible() -> None:
    completed = subprocess.run(
        [sys.executable, str(FIXTURE), "--stage", "manifest"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    assert completed.stderr == b""
    wrapper = json.loads(completed.stdout.decode("utf-8"))
    payload = wrapper["payload"]
    assert payload["program"] == "SPD Decap PI Evaluator v0.22.0"
    assert wrapper["payload_sha256"] == sha256(avbs.canonical_bytes(payload)).hexdigest()


def test_unavailable_primary_without_contract_is_canonical_failure() -> None:
    completed = subprocess.run(
        [sys.executable, str(FIXTURE), "--stage", "primary-h"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 2
    wrapper = json.loads(completed.stdout.decode("utf-8"))
    assert wrapper["payload"]["status"] == "BLOCKED_AV_BS_RESOURCE"
    assert wrapper["payload"]["mandatory_stage_pass"] is False
    assert wrapper["payload_sha256"] == sha256(
        avbs.canonical_bytes(wrapper["payload"])
    ).hexdigest()


def test_exact_p1_triangle_stiffness_and_mass() -> None:
    nodes = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    stiffness, mass = avbs._assemble_volume(nodes, [(0, 1, 2)])
    expected_k = np.asarray(
        ((2.0, -1.0, -1.0), (-1.0, 1.0, 0.0), (-1.0, 0.0, 1.0))
    ) / (2.0 * avbs.MU0_H_PER_M)
    expected_m = np.asarray(((2.0, 1.0, 1.0), (1.0, 2.0, 1.0), (1.0, 1.0, 2.0))) / 24.0
    np.testing.assert_allclose(stiffness.toarray(), expected_k, rtol=0.0, atol=1.0e-10)
    np.testing.assert_allclose(mass.toarray(), expected_m, rtol=0.0, atol=0.0)


def test_h_cyclic_diagonal_canonicalization_is_topology_frozen() -> None:
    nodes, triangles = avbs.seed_mesh()
    manifest = avbs.mesh_manifest(nodes, triangles)
    raw, mass = avbs._assemble_volume(nodes, triangles)
    canonical, certificate = avbs._canonicalize_cyclic_diagonals(
        raw,
        nodes,
        triangles,
        mesh_condition=manifest["max_element_kappa2"],
    )
    _, boundary = avbs.edge_data(triangles)
    _, _, mapping = avbs._boundary_partition(len(nodes), boundary)
    trace = avbs._assemble_trace_mass(nodes, boundary, mapping)

    assert certificate["tag_count"] == 1920
    assert certificate["tag_sha256"] == avbs.EXPECTED_H_ASSEMBLY["cyclic_diagonal_sha256"]
    assert certificate["two_triangle_incidence_pass"] is True
    assert certificate["cancellation_bound"] == pytest.approx(
        5.788860430596403e-13, rel=1.0e-15
    )
    assert certificate["maximum_cancellation_ratio"] == pytest.approx(
        2.1676835831040652e-13, rel=1.0e-15
    )
    assert certificate["cancellation_margin"] == pytest.approx(2.670528335278025, rel=1.0e-15)
    assert certificate["nonzero_tagged_binary64_values"] == 1917
    assert certificate["maximum_tagged_binary64_abs"] == pytest.approx(
        4.234607331454754e-09, rel=1.0e-15
    )
    assert certificate["raw_constant_null_relative"] == pytest.approx(
        1.2640750576031277e-16, rel=1.0e-15
    )
    assert certificate["canonical_constant_null_relative"] == pytest.approx(
        1.2084098802490622e-16, rel=1.0e-15
    )
    assert certificate["raw_transpose_relative"] == 0.0
    assert certificate["canonical_transpose_relative"] == 0.0
    assert certificate["correction_relative_frobenius"] == pytest.approx(
        1.3572884739080543e-16, rel=1.0e-15
    )
    assert raw.nnz == avbs.EXPECTED_H_ASSEMBLY["raw_stiffness_nnz"]
    assert canonical.nnz == avbs.EXPECTED_H_ASSEMBLY["canonical_stiffness_nnz"]
    assert mass.nnz == avbs.EXPECTED_H_ASSEMBLY["mass_nnz"]
    assert trace.nnz == avbs.EXPECTED_H_ASSEMBLY["trace_mass_nnz"]
    assert avbs._sparse_sha256(canonical) == avbs.EXPECTED_H_ASSEMBLY[
        "canonical_stiffness_sha256"
    ]
    assert avbs._sparse_sha256(raw) == avbs.EXPECTED_H_ASSEMBLY["raw_stiffness_sha256"]
    assert avbs._sparse_sha256(mass) == avbs.EXPECTED_H_ASSEMBLY["mass_sha256"]
    assert avbs._sparse_sha256(trace) == avbs.EXPECTED_H_ASSEMBLY["trace_mass_sha256"]


def test_cyclic_quadrilateral_diagonals_cancel_but_noncyclic_does_not() -> None:
    rectangle = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
    first, _ = avbs._assemble_volume(rectangle, [(0, 1, 2), (0, 2, 3)])
    second, _ = avbs._assemble_volume(rectangle, [(0, 1, 3), (1, 2, 3)])
    assert float(first[0, 2]) == 0.0
    assert float(second[1, 3]) == 0.0

    noncyclic = [(0.0, 0.0), (2.0, 0.0), (1.25, 1.0), (0.0, 1.0)]
    control, _ = avbs._assemble_volume(noncyclic, [(0, 1, 2), (0, 2, 3)])
    assert abs(float(control[0, 2])) > 1.0


def test_consistent_boundary_trace_mass() -> None:
    result = avbs._assemble_trace_mass([(0.0, 0.0), (2.0, 0.0)], [(0, 1)], {0: 0, 1: 1})
    np.testing.assert_allclose(
        result.toarray(), np.asarray(((2.0 / 3.0, 1.0 / 3.0), (1.0 / 3.0, 2.0 / 3.0)))
    )


def test_sparse_hash_is_invariant_to_duplicate_and_index_order() -> None:
    canonical = csc_matrix(np.asarray(((3.0, 0.0), (2.0, 4.0))))
    duplicate = csc_matrix(
        (
            np.asarray((1.0, 2.0, 4.0, 2.0)),
            (np.asarray((0, 0, 1, 1)), np.asarray((0, 0, 1, 0))),
        ),
        shape=(2, 2),
    )
    assert avbs._sparse_sha256(canonical) == avbs._sparse_sha256(duplicate)


def test_signed_mode_targets_share_absolute_order_anchor() -> None:
    for mode in range(1, 5):
        assert avbs._analytic_target(mode) == avbs._analytic_target(-mode)


def test_scaled_sparse_extension_solve_uses_original_system_residual() -> None:
    matrix = csc_matrix(np.asarray(((4.0 + 1.0j, 1.0), (1.0, 3.0 + 2.0j))))
    rhs = csc_matrix(np.eye(2, dtype=np.complex128))
    extension, certificate = avbs._solve_extensions("tiny", matrix, rhs)
    np.testing.assert_allclose(matrix @ extension, rhs.toarray(), rtol=1.0e-14, atol=1.0e-14)
    assert certificate["backward_residual_max"] <= avbs.MAX_BACKWARD
    assert certificate["kappa1_u"] <= avbs.MAX_KAPPA_U
    assert certificate["condition_kind"] == "deterministic_onenormest_lower_estimate"


def test_cross_operator_uses_bilinear_not_hermitian_transpose() -> None:
    xb = np.asarray(((1.0 + 2.0j, 0.5j), (2.0 - 1.0j, -0.25)))
    xp = np.asarray(((0.5 - 1.0j, 1.0j), (-1.0 + 0.25j, 2.0)))
    mii = csc_matrix(np.asarray(((2.0, 0.25), (0.25, 1.5))))
    mig = csc_matrix(np.asarray(((0.1, 0.2), (0.3, 0.4))))
    mgi = mig.T.tocsc()
    mgg = csc_matrix(np.asarray(((0.7, 0.05), (0.05, 0.8))))
    y, reverse = avbs._assemble_cross_operators(xb, xp, mii, mig, mgi, mgg)
    full_m = np.block([[mii.toarray(), mig.toarray()], [mgi.toarray(), mgg.toarray()]])
    hb = np.vstack((xb, np.eye(2)))
    hp = np.vstack((xp, np.eye(2)))
    np.testing.assert_allclose(y, avbs.SIGMA_S_PER_M * (hb.T @ full_m @ hp))
    np.testing.assert_allclose(reverse, avbs.SIGMA_S_PER_M * (hp.T @ full_m @ hb))
    assert not np.allclose(y, avbs.SIGMA_S_PER_M * (hb.conj().T @ full_m @ hp))


def test_review_token_is_bound_to_current_fixture_and_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked_token = json.loads(
        (ROOT / "tools" / "research" / "av_bs1_primary_h_review_token.json").read_text(
            encoding="utf-8"
        )
    )
    assert tracked_token["fixture_sha256"] == avbs._file_sha256(FIXTURE)
    assert tracked_token["runner_sha256"] == avbs._file_sha256(RUNNER)
    monkeypatch.setattr(avbs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(avbs.subprocess, "run", lambda *args, **kwargs: object())
    token_path = _review_token(tmp_path / "review.json")
    assert avbs._validate_review_token(token_path)["authorized_stage"] == "primary-h"
    tampered = json.loads(token_path.read_text(encoding="utf-8"))
    tampered["fixture_sha256"] = "0" * 64
    token_path.write_bytes(avbs.canonical_bytes(tampered))
    with pytest.raises(avbs.AvBsError, match="fixture hash mismatch"):
        avbs._validate_review_token(token_path)
    token_path = _review_token(tmp_path / "review.json")
    tampered = json.loads(token_path.read_text(encoding="utf-8"))
    tampered["review_evidence"]["h1_canonical_stiffness_sha256"] = "0" * 64
    token_path.write_bytes(avbs.canonical_bytes(tampered))
    with pytest.raises(avbs.AvBsError, match="canonical_stiffness_sha256 mismatch"):
        avbs._validate_review_token(token_path)


def test_finalizer_preserves_child_failure_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(avbs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(avbs.subprocess, "run", lambda *args, **kwargs: object())
    review_path = _review_token(tmp_path / "review.json")
    failure = avbs._failure_wrapper(
        avbs.AvBsError("BLOCKED_AV_BS_POWER", "synthetic failure"), stage="primary-h"
    )
    numerical_path = tmp_path / "numerical.json"
    numerical_path.write_bytes(avbs.canonical_bytes(failure) + b"\n")
    resource = {
        "schema": avbs.RESOURCE_SCHEMA,
        "stage": "primary-h",
        "runner_sha256": avbs._file_sha256(RUNNER),
        "child_stdout_sha256": avbs._file_sha256(numerical_path),
        "child_exit_code": 2,
        "mandatory_resource_gate_pass": True,
    }
    resource_path = tmp_path / "resource.json"
    resource_path.write_bytes(avbs.canonical_bytes(resource))
    wrapper = avbs.finalize_primary_h(numerical_path, resource_path, review_path)
    assert wrapper["payload"]["status"] == "BLOCKED_AV_BS_POWER"
    assert wrapper["payload"]["failure_codes"] == ["BLOCKED_AV_BS_POWER"]
    assert wrapper["payload"]["mandatory_stage_pass"] is False

    resource["child_exit_code"] = 0
    resource_path.write_bytes(avbs.canonical_bytes(resource))
    mismatched = avbs.finalize_primary_h(numerical_path, resource_path, review_path)
    assert mismatched["payload"]["failure_codes"] == [
        "BLOCKED_AV_BS_POWER",
        "BLOCKED_AV_BS_RESULT_SCHEMA",
    ]
    assert mismatched["payload"]["mandatory_stage_pass"] is False


def test_fixture_ast_forbids_product_imports_and_dense_inverse() -> None:
    source = FIXTURE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    forbidden_attributes: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Attribute) and node.attr in {"inv", "toarray", "todense"}:
            forbidden_attributes.append(node.attr)
    assert not any(name == "spd_decap_pi" or name.startswith("spd_decap_pi.") for name in imported)
    assert forbidden_attributes == []
    assert "np.linalg.solve" not in source
    assert "np.linalg.lstsq" not in source
    assert "np.linalg.pinv" not in source
    assert 'choices=("manifest", "primary-h", "finalize-primary-h")' in source
    assert "primary-h2" not in source.split("def _parser", 1)[1]
    assert "primary-h4" not in source.split("def _parser", 1)[1]


def test_runner_is_parseable_and_h_only() -> None:
    command = (
        "$e=$null;$t=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{str(RUNNER).replace("'", "''")}',[ref]$t,[ref]$e)|Out-Null;"
        "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    source = RUNNER.read_text(encoding="utf-8")
    assert '[ValidateSet("manifest", "primary-h")]' in source
    assert "-WindowStyle Hidden" in source
    assert "TREE_WS_STOP" in source
    assert "MONITOR_QUERY_FAILED" in source
    assert "$childProcessHandle = $process.Handle" in source
    assert "child exit code was not retained" in source


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner contract")
def test_powershell_redirected_child_handle_retains_exit_code(tmp_path: Path) -> None:
    stdout_path = str(tmp_path / "stdout.txt").replace("'", "''")
    stderr_path = str(tmp_path / "stderr.txt").replace("'", "''")
    python_path = str(Path(sys.executable)).replace("'", "''")
    command = (
        f"$p=Start-Process -FilePath '{python_path}' "
        "-ArgumentList '-c \"raise SystemExit(2)\"' -PassThru -WindowStyle Hidden "
        f"-RedirectStandardOutput '{stdout_path}' -RedirectStandardError '{stderr_path}';"
        "$h=$p.Handle;$p.WaitForExit();$p.Refresh();"
        "if($null -eq $p.ExitCode){exit 3};Write-Output $p.ExitCode"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "2"
