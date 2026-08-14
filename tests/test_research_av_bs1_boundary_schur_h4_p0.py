from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "tools" / "research"
sys.path.insert(0, str(RESEARCH))
import av_bs1_boundary_schur_h4_p0 as h4


@pytest.fixture(scope="module")
def manifest() -> dict[str, object]:
    return dict(h4.run_manifest())


def test_h4_p0_is_manifest_only(manifest: dict[str, object]) -> None:
    assert manifest["program"] == "SPD Decap PI Evaluator v0.22.0"
    assert manifest["schema"] == "AV-BS1-h4-p0-manifest-v1"
    assert manifest["status"] == "preregistered_H4_P0_assembly_only_no_solve"
    assert manifest["authorization_state"] == "not_authorized"
    assert manifest["factorization_performed"] is False
    assert manifest["physics_solve_performed"] is False
    assert manifest["available_solve_stages"] == []
    assert "primary-h4" in manifest["unavailable_stages"]


def test_h4_mesh_and_lineage_are_frozen(manifest: dict[str, object]) -> None:
    mesh = manifest["mesh"]
    assert (mesh["nodes"], mesh["edges"], mesh["triangles"]) == (32001, 95488, 63488)
    assert (mesh["boundary_edges"], mesh["interior_nodes"], mesh["euler"]) == (512, 31489, 1)
    assert mesh["sha256"] == "a91b4bf147628a34d1a29144ae353a83110b1756c699c71e2b57e9c36822835b"
    lineage = manifest["cyclic_lineage"]
    assert (lineage["parent_count"], lineage["candidate_count"], lineage["tag_count"]) == (3712, 7424, 7424)
    assert (lineage["parent_boundary_touch_count"], lineage["excluded_count"]) == (0, 0)
    assert lineage["candidate_sha256"] == lineage["tag_sha256"]
    assert lineage["tag_sha256"] == "3902a43ddd16f2cfce16b2892b908f45d02f4464c5157a5c42b5b3ac5cb9d98d"
    assert lineage["mapping_sha256"] == "561059787bc8b48a9e9c5963c69a3cceed2278779a22bf8ac37f619cee977b97"
    partition = manifest["partition"]
    assert (partition["interior_nodes"], partition["boundary_nodes"]) == (31489, 512)
    assert partition["interior_sha256"] == "b91ec9e63cc180c30daf3b61c6789b221ad9429ae9cc6bfc6d0b1d51e07e205c"
    assert partition["gamma_sha256"] == "749dbbb6b7f58565c757a1042dd9344f165c35afb23e77022222e21ba260c0b9"


def test_h4_sparse_assembly_certificate_is_exact(manifest: dict[str, object]) -> None:
    assembly = manifest["assembly"]
    assert (assembly["raw_stiffness_nnz"], assembly["canonical_stiffness_nnz"]) == (222977, 208129)
    assert assembly["raw_stiffness_sha256"] == "8a020c809634a9794292f49198ff1bede84328b6e2c5ef988255cbff32cfe93b"
    assert assembly["canonical_stiffness_sha256"] == "a510df2ab39cb85640720f863341d1fe468562442ecb074bafcaf70d9846f2e7"
    assert (assembly["mass_nnz"], assembly["trace_mass_nnz"]) == (222977, 1536)
    assert assembly["mass_sha256"] == "66bc7da7edfae88a53220ce553c4b8ab0cea51b5222d354139d377c4d34c05ac"
    assert assembly["trace_mass_sha256"] == "cac37897c0c7910ae64ba15c3307ad2ba7bf109f7832a0b692ed02bb5ff8771d"
    assert assembly["full_support_sha256"] == "bae1059a74cd662c6bac6e027beb8b54f1df865693939735dc1247a161c23240"
    assert assembly["canonical_support_sha256"] == "4416afcc176bce3cda2a18e3f73a53c2b08c21d270cc3cff1c78293a7450fa52"
    assert assembly["tag_incidence_two_count"] == 7424
    assert assembly["nonzero_tagged_binary64_values"] == 7424
    assert assembly["cancellation_formula"] == "256u_kappa_explicit_P1_determinant_expression"
    assert assembly["inherited_128u_kappa_pass"] is False
    assert assembly["inherited_128u_kappa_exceeding_tag_count"] == 16
    assert assembly["maximum_cancellation_ratio"] < assembly["cancellation_bound"]
    assert assembly["inherited_128u_kappa_exceeding_tag_count"] == 16
    assert assembly["minimum_untagged_two_triangle_ratio"] > assembly["cancellation_bound"]
    assert max(assembly["raw_transpose_relative"], assembly["canonical_transpose_relative"]) == 0.0
    assert assembly["canonical_block_nnz"] == {"II": 204545, "IG": 1024, "GI": 1024, "GG": 1536}
    assert assembly["mass_block_nnz"] == {"II": 219393, "IG": 1024, "GI": 1024, "GG": 1536}


def test_h4_resource_envelopes_do_not_authorize_execution(manifest: dict[str, object]) -> None:
    resource = manifest["resource_preflight"]
    dense = resource["dense_upper_diagnostic"]
    guarded = resource["guarded_one_factor_prospective"]
    assert dense["one_factor_dense_upper_bytes"] == 31729827872
    assert dense["total_with_25pct_margin_bytes"] == 40448792335
    assert dense["primary_h4_resource_preflight_pass"] is False
    assert guarded["sparse_base_arrays_bytes"] == 5449772
    assert guarded["one_factor_hard_cap_bytes"] == 2147483648
    assert guarded["total_with_25pct_margin_bytes"] == 3470862055
    assert guarded["tree_working_set_slack_bytes"] == 824105241
    assert guarded["candidate_envelope_with_cap_pass"] is True
    assert guarded["factor_fit_unproven"] is True
    assert guarded["minimum_available_physical_before_spawn_bytes"] == 5081474791
    assert guarded["minimum_commit_headroom_before_spawn_bytes"] == 5618345703
    assert resource["primary_h4_authorized"] is False
    assert resource["next_resource_decision_requires_separate_preregistration"] is True


def test_h4_cli_wrapper_checksum() -> None:
    completed = subprocess.run(
        [sys.executable, str(RESEARCH / "av_bs1_boundary_schur_h4_p0.py"), "--stage", "manifest"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wrapper = json.loads(completed.stdout)
    assert wrapper["payload"]["status"] == "preregistered_H4_P0_assembly_only_no_solve"
    assert wrapper["payload_sha256"] == h4.h1._wrapper(wrapper["payload"])["payload_sha256"]


def test_h4_source_and_runner_expose_no_solve_path() -> None:
    fixture_path = RESEARCH / "av_bs1_boundary_schur_h4_p0.py"
    tree = ast.parse(fixture_path.read_text(encoding="utf-8"))
    banned_attributes = {"solve", "toarray", "todense", "inv", "pinv", "lstsq"}
    assert not any(isinstance(node, ast.Attribute) and node.attr in banned_attributes for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Name) and node.id == "splu" for node in ast.walk(tree))
    runner = (RESEARCH / "run_av_bs1_h4_p0_stage.ps1").read_text(encoding="utf-8")
    assert 'ValidateSet("manifest")' in runner
    assert "av_bs1_boundary_schur_h4_p0.py" in runner
    assert "primary-h4" not in runner
