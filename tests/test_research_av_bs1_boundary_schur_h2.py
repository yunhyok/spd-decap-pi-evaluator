from __future__ import annotations

import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
H1_FIXTURE = ROOT / "tools" / "research" / "av_bs1_boundary_schur.py"
H1_RUNNER = ROOT / "tools" / "research" / "run_av_bs1_stage.ps1"
H1_TOMBSTONE = ROOT / "tools" / "research" / "av_bs1_primary_h_review_token.json"
FIXTURE = ROOT / "tools" / "research" / "av_bs1_boundary_schur_h2.py"
RUNNER = ROOT / "tools" / "research" / "run_av_bs1_h2_stage.ps1"

SPEC = importlib.util.spec_from_file_location("research_av_bs1_boundary_schur_h2", FIXTURE)
assert SPEC is not None and SPEC.loader is not None
h2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h2)


def test_h2_p0_manifest_is_static_and_h1_bytes_are_preserved() -> None:
    manifest = h2.run_manifest()
    assert manifest["schema"] == "AV-BS1-h2-p0-manifest-v1"
    assert manifest["program"] == "SPD Decap PI Evaluator v0.22.0"
    assert manifest["status"] == "preregistered_H2_P0_assembly_only_no_solve"
    assert manifest["authorization_state"] == "not_authorized"
    assert manifest["factorization_performed"] is False
    assert manifest["physics_solve_performed"] is False
    assert manifest["available_solve_stages"] == []
    assert manifest["unavailable_stages"] == ["primary-h", "primary-h2", "primary-h4", "withheld", "EQ0"]
    assert manifest["mesh"]["sha256"] == h2.EXPECTED_MESH[4]
    assert (manifest["mesh"]["nodes"], manifest["mesh"]["edges"], manifest["mesh"]["triangles"], manifest["mesh"]["boundary_edges"]) == (8065, 23936, 15872, 256)
    assert manifest["assembly"]["raw_stiffness_sha256"] == h2.EXPECTED_H2_ASSEMBLY["raw_stiffness_sha256"]
    assert manifest["assembly"]["canonical_stiffness_sha256"] == h2.EXPECTED_H2_ASSEMBLY["canonical_stiffness_sha256"]
    assert manifest["assembly"]["full_support_sha256"] == h2.EXPECTED_H2_ASSEMBLY["full_support_sha256"]
    assert manifest["assembly"]["canonical_support_sha256"] == h2.EXPECTED_H2_ASSEMBLY["canonical_support_sha256"]
    assert manifest["assembly"]["trace_support_sha256"] == h2.EXPECTED_H2_ASSEMBLY["trace_support_sha256"]
    assert manifest["assembly"]["maximum_cancellation_ratio"] == h2.EXPECTED_H2_ASSEMBLY["maximum_cancellation_ratio"]
    assert manifest["assembly"]["cancellation_margin"] == h2.EXPECTED_H2_ASSEMBLY["cancellation_margin"]
    assert manifest["assembly"]["cancellation_absolute_slack"] == h2.EXPECTED_H2_ASSEMBLY["cancellation_absolute_slack"]
    assert manifest["assembly"]["cancellation_relative_slack"] == h2.EXPECTED_H2_ASSEMBLY["cancellation_relative_slack"]
    assert manifest["assembly"]["nonzero_tagged_binary64_values"] == 3712
    assert manifest["assembly"]["minimum_tagged_binary64_abs"] == 1.8189894035458565e-12
    assert manifest["assembly"]["maximum_tagged_binary64_abs"] == 7.088601705618203e-09
    assert manifest["assembly"]["minimum_untagged_two_triangle_ratio"] == 0.19705186275542091
    assert manifest["assembly"]["raw_constant_null_relative"] == 1.5137986906722262e-16
    assert manifest["assembly"]["canonical_constant_null_relative"] == 1.616955055742965e-16
    assert manifest["assembly"]["correction_relative_frobenius"] == 1.8326181103987503e-16
    assert manifest["resource_preflight"]["sparse_base_arrays_bytes"] == 1_328_172
    assert manifest["resource_preflight"]["method"] == "h2_p0_dense_factor_upper_plus_sparse_and_rectangular_25pct"
    assert manifest["resource_preflight"]["raw_total_bytes"] == 2_043_070_476
    assert manifest["resource_preflight"]["total_with_25pct_margin_bytes"] == 2_553_838_095
    assert h2._file_sha256(H1_FIXTURE) == h2.H1_FIXTURE_SHA256
    assert h2._file_sha256(H1_RUNNER) == h2.H1_RUNNER_SHA256
    assert h2._file_sha256(H1_TOMBSTONE) == h2.H1_TOMBSTONE_SHA256
    assert manifest["dependencies"] == {
        "h1_fixture_sha256": h2.H1_FIXTURE_SHA256,
        "h1_runner_sha256": h2.H1_RUNNER_SHA256,
        "h1_consumed_tombstone_sha256": h2.H1_TOMBSTONE_SHA256,
    }


def test_h2_p0_lineage_and_partition_are_frozen() -> None:
    nodes, triangles, midpoint = h2.seed_mesh_h2()
    tags, lineage = h2._h2_tags(nodes, midpoint)
    assert len(tags) == 3712
    assert lineage["candidate_sha256"] == h2.EXPECTED_H2_ASSEMBLY["candidate_sha256"]
    assert lineage["mapping_sha256"] == h2.EXPECTED_H2_ASSEMBLY["mapping_sha256"]
    assert lineage["excluded_sha256"] == h2.EXPECTED_H2_ASSEMBLY["excluded_sha256"]
    assert sha256(h2.canonical_bytes([list(edge) for edge in tags])).hexdigest() == h2.EXPECTED_H2_ASSEMBLY["tag_sha256"]
    _, boundary = h2.h1.edge_data(triangles)
    interior, gamma, _ = h2._partition(len(nodes), boundary)
    assert len(interior) == 7809 and len(gamma) == 256


def test_h2_p0_cli_is_canonical_and_primary_h2_is_unavailable() -> None:
    completed = subprocess.run([sys.executable, str(FIXTURE), "--stage", "manifest"], cwd=ROOT, check=True, capture_output=True)
    assert completed.stderr == b""
    wrapper = json.loads(completed.stdout.decode("utf-8"))
    assert wrapper["payload"]["program"] == "SPD Decap PI Evaluator v0.22.0"
    assert wrapper["payload_sha256"] == sha256(h2.canonical_bytes(wrapper["payload"])).hexdigest()
    rejected = subprocess.run([sys.executable, str(FIXTURE), "--stage", "primary-h2"], cwd=ROOT, check=False, capture_output=True)
    assert rejected.returncode == 2
    assert b"invalid choice" in rejected.stderr


def test_h2_p0_source_excludes_product_imports_dense_inverse_and_execution_cli() -> None:
    source = FIXTURE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    forbidden: list[str] = []
    calls: list[str] = []
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.Attribute) and node.attr in {"inv", "pinv", "lstsq", "toarray", "todense"}:
            forbidden.append(node.attr)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
        elif isinstance(node, ast.FunctionDef):
            functions.add(node.name)
    assert not any(name == "spd_decap_pi" or name.startswith("spd_decap_pi.") for name in imports)
    assert forbidden == []
    assert not {"splu", "solve", "_solve_extensions", "_factor_operator"}.intersection(calls)
    assert not {
        "_validate_review_token",
        "_validate_guard",
        "_validate_checkout",
        "_prior_h_modes",
        "run_primary_h2",
        "run_finalizer",
    }.intersection(functions)
    assert 'choices=("manifest",)' in source


def test_h2_p0_dependency_tamper_fails_before_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h2, "H1_FIXTURE_SHA256", "0" * 64)
    with pytest.raises(h2.AvBsError, match="H1 dependency hash mismatch") as captured:
        h2.run_manifest()
    assert captured.value.code == "BLOCKED_AV_BS_RESULT_SCHEMA"


def test_h2_p0_runner_is_parseable_and_manifest_only() -> None:
    command = (
        "$e=$null;$t=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{str(RUNNER).replace("'", "''")}',[ref]$t,[ref]$e)|Out-Null;"
        "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], cwd=ROOT, check=True, capture_output=True, text=True)
    source = RUNNER.read_text(encoding="utf-8")
    assert '[ValidateSet("manifest")]' in source
    assert "primary-h2" not in source
    assert "review" not in source.lower()
