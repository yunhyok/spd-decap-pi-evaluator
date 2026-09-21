"""Archive-only skips for immutable evidence bound to the original checkout."""

import pytest


ARCHIVE_ONLY_SKIP_REASONS = {
    "tests/test_build_source_l29_l30_fastercap_input.py::test_production_receipts_materialize":
        "archive-only: sealed production materializer requires branch main at exact head e2f219e",
    "tests/test_d117_triangle_cell258_c1.py::test_production_frozen_cycles_area_and_canonical_regression":
        "archive-only: frozen input manifest pins the original checkout path, absent after archival move",
    "tests/test_d117_triangle_cell258_c1_single_call.py::test_real_clearance_receipt_and_controller_evidence_validate_read_only":
        "archive-only: immutable receipt pins the helper at the original checkout path, not this worktree",
    "tests/test_d117_triangle_cell264.py::test_actual_sealed_inputs_read_only_no_triangle_mesh":
        "archive-only: Stage0 helper identity is pinned to the original checkout path",
    "tests/test_d117_triangle_cell264.py::test_cap_application_isolated_stage0_import":
        "archive-only: Stage0 helper identity is pinned to the original checkout path",
    "tests/test_d117_triangle_cell264.py::test_static_sidewall_precheck_actual_full_cell_without_triangle":
        "archive-only: Stage0 helper identity is pinned to the original checkout path",
    "tests/test_run_d116_shadow_fastercap.py::test_manifest_rejects_tampered_runner_hash":
        "archive-only: D116 approval manifest requires the runner at the original authoritative checkout path",
    "tests/test_run_d116_shadow_fastercap.py::test_manifest_rejects_alternate_output_even_when_manifest_claims_it":
        "archive-only: D116 approval manifest requires the runner at the original authoritative checkout path",
    "tests/test_run_d116_shadow_fastercap.py::test_approval_sha_anchor_requires_correct_lowercase_hex":
        "archive-only: D116 approval manifest requires the runner at the original authoritative checkout path",
    "tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_wrong_size_and_digest_are_rejected_before_json_parse":
        "archive-only: sealed WP3 controller requires invocation cwd to equal the original repository root",
    "tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_changed_same_size_hq_is_not_accepted":
        "archive-only: sealed WP3 controller requires invocation cwd to equal the original repository root",
    "tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_substituted_valid_hq_is_parsed_but_cannot_authorize":
        "archive-only: sealed WP3 controller requires invocation cwd to equal the original repository root",
    "tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_duplicate_keys_are_rejected_after_exact_pin_validation":
        "archive-only: sealed WP3 controller requires invocation cwd to equal the original repository root",
    "tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_existing_reparse_parent_is_rejected_without_creating_or_touching_one":
        "archive-only: sealed WP3 controller requires invocation cwd to equal the original repository root",
}


def pytest_collection_modifyitems(items):
    for item in items:
        reason = ARCHIVE_ONLY_SKIP_REASONS.get(item.nodeid)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
