import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "validate_known_case_nonregression.py"
POLICY = ROOT / "validation-policies" / "known_case_nonregression_v1.json"
SPEC = importlib.util.spec_from_file_location("validate_known_case_nonregression", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def _metrics(value=0.0):
    return {
        "grid": {"low_hz": 100000.0, "high_hz": 100000000.0, "points": 241},
        "complex": {"rms_uohm": value},
        "magnitude_db": {"rms_db": value, "p95_abs_db": value},
        "phase_deg": {"p95_abs_deg": value},
    }


def _report(case_id="260729"):
    policy = _policy()
    case = policy["cases"][case_id]
    rails = {}
    for group, members in policy["groups"].items():
        for rail in members:
            rails[rail] = {
                "status": "completed",
                "group": group,
                "solver_version": MODULE.RELEASE_IDENTITY["solver_version"],
                "solver_profile_key": MODULE.RELEASE_IDENTITY["solver_profile_key"],
                "solver_provenance": {
                    "profile_key": MODULE.RELEASE_IDENTITY["solver_profile_key"],
                    "compiler_algorithm_id": MODULE.RELEASE_IDENTITY[
                        "compiler_algorithm_id"
                    ],
                    "compiler_version": MODULE.RELEASE_IDENTITY["compiler_version"],
                    "static_compiler_algorithm_sha256": MODULE.RELEASE_IDENTITY[
                        "static_compiler_algorithm_sha256"
                    ],
                },
                "convergence": {"converged": True},
                "metrics": _metrics(),
            }
    return {
        **MODULE.RELEASE_IDENTITY,
        "schema_version": "powersi-correlation-report-v5",
        "report_version": 5,
        "source": case["source"],
        "touchstone": {
            **case["touchstone"],
            "full_file_sha256": case["touchstone"]["sha256"],
        },
        "candidate_bundle": case["candidate"],
        "identity": {
            "source_sha256": case["source"]["sha256"],
            "candidate_source_sha256": case["source"]["sha256"],
            "candidate_bundle_sha256": case["candidate"]["sha256"],
        },
        "score_split": policy["groups"],
        "runs": {
            "candidate": {
                "10": {
                    "solver_profile": MODULE.RELEASE_IDENTITY["solver_profile"],
                    "modal_max_index": 10,
                    "terminal_complete_batch_reuse": {
                        "status": "source_solve",
                        "source_modal_max_index": 10,
                    },
                    "rails": rails,
                },
                "12": {
                    "solver_profile": MODULE.RELEASE_IDENTITY["solver_profile"],
                    "modal_max_index": 12,
                    "terminal_complete_batch_reuse": {
                        "status": "reused_exact_mode_invariant",
                        "source_modal_max_index": 10,
                    },
                    "rails": copy.deepcopy(rails),
                },
            }
        },
    }


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _v5_attestation(report_path):
    report_sha256 = (
        report_path.file.sha256
        if isinstance(report_path, MODULE.JsonSnapshot)
        else MODULE._sha256(report_path)
    )
    return {
        "status": "PASS",
        "validator_basename": "validate_correlation_v5.py",
        "validator_sha256": MODULE.TRACKED_V5_SHA256,
        "validator_hash_semantics": "utf8_lf_normalized",
        "raw_report_sha256": report_sha256,
        "exit_code": 0,
        "pass_marker": (
            "PASS: v5 release/convergence/invariance/reuse structure validated"
        ),
    }


def _baseline_attestation(case_id="260729"):
    return {
        "status": "PASS",
        "derivation": "max_of_mode6_mode8",
        "group_aggregation": "equal_rail_macro_mean",
        "reports": _policy()["cases"][case_id]["baseline_reports"],
    }


def _benchmark_attestation():
    policy = _policy()
    return {
        "status": "PASS",
        "path": policy["benchmark_source"]["path"],
        "hash_semantics": "utf8_lf_normalized",
        "sha256": MODULE.BENCHMARK_SOURCE_SHA256,
        "trust_boundary": policy["trust_boundary"],
    }


def test_policy_is_hash_bound_regression_only_and_has_exact_manifests():
    policy = _policy()

    assert MODULE._policy_sha256(POLICY) == MODULE.POLICY_SHA256
    assert MODULE.RELEASE_IDENTITY["app_version"] == "0.23.0"
    assert policy["release_identity"]["app_version"] == "0.23.0"
    assert policy["policy_name"] == "known_case_nonregression"
    assert policy["policy_hash_semantics"] == "canonical_json_utf8_sort_keys_compact"
    assert policy["claim_scope"] == "regression_only_not_accuracy"
    assert policy["normalization"] == "none"
    assert policy["group_aggregation"] == "equal_rail_macro_mean"
    assert policy["metric_paths"] == MODULE.POLICY_METRIC_PATHS
    assert policy["release_condition"] == (
        "v5_pass_and_known_case_nonregression_sidecar_pass"
    )
    assert sum(map(len, policy["groups"].values())) == 16
    assert set(policy["cases"]) == {"260729", "260804"}
    assert policy["cases"]["260729"]["group_thresholds"][
        "vqps_development"
    ]["complex_rms_uohm"] == pytest.approx(120474989.03875491)
    assert policy["cases"]["260804"]["group_thresholds"][
        "loaded_final_holdout"
    ]["phase_p95_abs_deg"] == pytest.approx(21.408662151781897)


def test_pass_sidecar_binds_policy_report_and_all_case_identities(tmp_path):
    report_path = tmp_path / "report.json"
    _write_json(report_path, _report())

    sidecar, passed = MODULE.evaluate(POLICY, report_path, "260729")

    assert passed is True
    assert sidecar["status"] == "NONREGRESSION_PASS_RELEASE_ATTESTATIONS_NOT_RUN"
    assert sidecar["nonregression_status"] == "PASS"
    assert sidecar["policy_sha256"] == MODULE.POLICY_SHA256
    assert sidecar["policy_sha256_semantics"] == (
        "canonical_json_utf8_sort_keys_compact"
    )
    assert sidecar["raw_report_sha256"] == MODULE._sha256(report_path)
    case = _policy()["cases"]["260729"]
    assert sidecar["case_identity"] == {
        "case_id": "260729",
        "source": case["source"],
        "touchstone": case["touchstone"],
        "candidate": case["candidate"],
    }
    assert sidecar["group_aggregation"] == "equal_rail_macro_mean"
    assert sidecar["rail_count"] == 16


def test_convergence_valid_metric_regression_fails_even_when_group_passes(tmp_path):
    policy = _policy()
    report = _report()
    rail = "ADC_VDD_180_VQPS_OTP_TOP_AON/0"
    limit = policy["cases"]["260729"]["rail_thresholds"][rail][
        "complex_rms_uohm"
    ]
    report["runs"]["candidate"]["10"]["rails"][rail]["metrics"]["complex"][
        "rms_uohm"
    ] = limit * 1.01
    report["runs"]["candidate"]["12"]["rails"][rail]["metrics"]["complex"][
        "rms_uohm"
    ] = limit * 1.01
    report_path = tmp_path / "report.json"
    _write_json(report_path, report)

    sidecar, passed = MODULE.evaluate(POLICY, report_path, "260729")

    assert report["runs"]["candidate"]["10"]["rails"][rail]["convergence"] == {
        "converged": True
    }
    assert passed is False
    assert any(
        failure["scope"] == "rail" and failure["name"] == rail
        for failure in sidecar["failures"]
    )
    assert not any(
        failure["scope"] == "group_equal_rail_macro_mean"
        for failure in sidecar["failures"]
    )


def test_policy_tamper_and_report_tamper_fail_closed(tmp_path, monkeypatch):
    tampered_policy = tmp_path / "policy.json"
    policy = _policy()
    policy["cases"]["260729"]["group_thresholds"]["vqps_development"][
        "complex_rms_uohm"
    ] += 1.0
    _write_json(tampered_policy, policy)
    report_path = tmp_path / "report.json"
    _write_json(report_path, _report())

    with pytest.raises(MODULE.IntegrityError, match="policy.sha256"):
        MODULE.evaluate(tampered_policy, report_path, "260729")

    sidecar, passed = MODULE.evaluate(
        POLICY,
        report_path,
        "260729",
        structural_v5=_v5_attestation(report_path),
        baseline_verification=_baseline_attestation(),
        benchmark_verification=_benchmark_attestation(),
    )
    assert passed is True
    sidecar_path = tmp_path / "result.json"
    MODULE._write_sidecar(sidecar_path, sidecar)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["tampered"] = True
    _write_json(report_path, report)
    monkeypatch.setattr(
        MODULE,
        "_run_structural_v5",
        lambda path, _validator: _v5_attestation(path),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_baseline_thresholds",
        lambda _policy, case_id, _root: _baseline_attestation(case_id),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_benchmark_source",
        lambda _policy, _root: _benchmark_attestation(),
    )
    with pytest.raises(MODULE.IntegrityError, match="canonical_recomputed_bytes"):
        MODULE.verify_sidecar(POLICY, report_path, sidecar_path)


def test_tampered_pass_sidecar_is_recomputed_and_rejected(tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    _write_json(report_path, _report())
    sidecar, passed = MODULE.evaluate(
        POLICY,
        report_path,
        "260729",
        structural_v5=_v5_attestation(report_path),
        baseline_verification=_baseline_attestation(),
        benchmark_verification=_benchmark_attestation(),
    )
    assert passed is True and sidecar["status"] == "PASS"
    sidecar["rail_metrics"]["ADC_VDD_055_VTRIP/0"]["complex_rms_uohm"] = 0.5
    sidecar_path = tmp_path / "result.json"
    MODULE._write_sidecar(sidecar_path, sidecar)
    monkeypatch.setattr(
        MODULE,
        "_run_structural_v5",
        lambda path, _validator: _v5_attestation(path),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_baseline_thresholds",
        lambda _policy, case_id, _root: _baseline_attestation(case_id),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_benchmark_source",
        lambda _policy, _root: _benchmark_attestation(),
    )

    with pytest.raises(MODULE.IntegrityError, match="canonical_recomputed_bytes"):
        MODULE.verify_sidecar(POLICY, report_path, sidecar_path)


@pytest.mark.parametrize("mutation", ["nan", "missing_rail"])
def test_nan_or_missing_rail_is_integrity_error(tmp_path, mutation):
    report = _report("260804")
    rail = "ADC_VDD_055_VTRIP/0"
    if mutation == "nan":
        report["runs"]["candidate"]["10"]["rails"][rail]["metrics"]["complex"][
            "rms_uohm"
        ] = float("nan")
    else:
        del report["runs"]["candidate"]["10"]["rails"][rail]
    report_path = tmp_path / "report.json"
    _write_json(report_path, report)

    with pytest.raises(MODULE.IntegrityError):
        MODULE.evaluate(POLICY, report_path, "260804")


def test_main_exit_codes_are_zero_two_and_one(tmp_path, monkeypatch):
    report = _report()
    report_path = tmp_path / "report.json"
    sidecar_path = tmp_path / "result.json"
    _write_json(report_path, report)
    common = [
        "--policy",
        str(POLICY),
        "--report",
        str(report_path),
        "--sidecar",
        str(sidecar_path),
        "--case",
        "260729",
    ]
    monkeypatch.setattr(
        MODULE,
        "_run_structural_v5",
        lambda path, _validator: _v5_attestation(path),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_baseline_thresholds",
        lambda _policy, case_id, _root: _baseline_attestation(case_id),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_benchmark_source",
        lambda _policy, _root: _benchmark_attestation(),
    )
    assert MODULE.main(common) == 0

    rail = "ADC_VDD_180_VQPS_SYS_0_AON/0"
    report["runs"]["candidate"]["10"]["rails"][rail]["metrics"][
        "magnitude_db"
    ]["rms_db"] = 999.0
    _write_json(report_path, report)
    common[5] = str(tmp_path / "regression-result.json")
    assert MODULE.main(common) == 2

    report["source"]["sha256"] = "0" * 64
    _write_json(report_path, report)
    common[5] = str(tmp_path / "integrity-result.json")
    assert MODULE.main(common) == 1


def test_policy_schema_rejects_provenance_and_formula_tamper():
    policy = _policy()
    policy["threshold_derivation"]["group"] = "weighted_mean"
    with pytest.raises(MODULE.IntegrityError, match="threshold_derivation"):
        MODULE._validate_policy(policy)

    policy = _policy()
    del policy["cases"]["260729"]["baseline_reports"][0]["sha256"]
    with pytest.raises(MODULE.IntegrityError, match="baseline_reports"):
        MODULE._validate_policy(policy)

    policy = _policy()
    policy["cases"]["260804"]["candidate"]["extra"] = "not allowed"
    with pytest.raises(MODULE.IntegrityError, match="candidate"):
        MODULE._validate_policy(policy)


def test_tracked_v5_validator_is_hash_bound_and_rejects_incomplete_report(tmp_path):
    validator = SCRIPT.with_name("validate_correlation_v5.py")
    assert MODULE._tracked_text_sha256(validator) == MODULE.TRACKED_V5_SHA256
    assert 'APP_VERSION = "0.23.0"' in validator.read_text(encoding="utf-8")
    report_path = tmp_path / "incomplete.json"
    _write_json(report_path, {"schema_version": "powersi-correlation-report-v5"})

    with pytest.raises(MODULE.IntegrityError, match="did not PASS"):
        MODULE._run_structural_v5(
            MODULE._read_json_snapshot(report_path),
            MODULE._read_file_snapshot(validator),
        )


def test_all_policy_thresholds_recompute_exactly_from_hash_bound_baselines():
    policy = _policy()
    missing = [
        baseline["path"]
        for case in policy["cases"].values()
        for baseline in case["baseline_reports"]
        if not (ROOT / baseline["path"]).is_file()
    ]
    assert not missing, "tracked known-case baseline fixtures are missing: " + ", ".join(missing)

    for case_id in policy["cases"]:
        result = MODULE._verify_baseline_thresholds(policy, case_id, ROOT)
        assert result == _baseline_attestation(case_id)


def test_candidate_size_identity_mismatch_fails_closed(tmp_path):
    report = _report()
    report["candidate_bundle"]["size_bytes"] += 1
    report_path = tmp_path / "report.json"
    _write_json(report_path, report)

    with pytest.raises(MODULE.IntegrityError, match="candidate_bundle.size_bytes"):
        MODULE.evaluate(POLICY, report_path, "260729")


@pytest.mark.parametrize("drift", ["root_solver", "rail_compiler"])
def test_exact_release_identity_drift_fails_closed(tmp_path, drift):
    report = _report()
    if drift == "root_solver":
        report["solver_version"] = "modal-mvp-0.8.2"
        expected = "report.solver_version"
    else:
        rail = "ADC_VDD_180_VQPS_SYS_2_AON/1"
        report["runs"]["candidate"]["12"]["rails"][rail][
            "solver_provenance"
        ]["compiler_version"] = "layer-surface-wrong-v9"
        expected = "solver_provenance.compiler_version"
    report_path = tmp_path / "report.json"
    _write_json(report_path, report)

    with pytest.raises(MODULE.IntegrityError, match=expected):
        MODULE.evaluate(POLICY, report_path, "260729")


@pytest.mark.parametrize("input_kind", ["policy", "report", "baseline", "sidecar"])
def test_duplicate_json_keys_are_rejected_for_every_gate_input(tmp_path, input_kind):
    path = tmp_path / f"{input_kind}.json"
    path.write_bytes(b'{"identity":{},"identity":{}}\n')

    with pytest.raises(MODULE.IntegrityError, match="duplicate JSON key"):
        MODULE._read_json_snapshot(path)


def test_owned_report_snapshot_is_immune_to_later_path_mutation(tmp_path):
    report_path = tmp_path / "report.json"
    _write_json(report_path, _report())
    policy_snapshot = MODULE._read_json_snapshot(POLICY)
    report_snapshot = MODULE._read_json_snapshot(report_path)
    report_path.write_bytes(b'{"tampered":true}\n')
    structural = _v5_attestation(report_snapshot)

    sidecar, passed = MODULE._evaluate_snapshots(
        policy_snapshot,
        report_snapshot,
        "260729",
        structural_v5=structural,
        baseline_verification=_baseline_attestation(),
        benchmark_verification=_benchmark_attestation(),
    )

    assert passed is True and sidecar["status"] == "PASS"
    assert sidecar["raw_report_sha256"] == report_snapshot.file.sha256
    assert sidecar["raw_report_sha256"] != MODULE._sha256(report_path)


def test_create_mode_refuses_stale_sidecar_without_changing_it(tmp_path):
    sidecar_path = tmp_path / "result.json"
    MODULE._write_sidecar(sidecar_path, {"status": "PASS"})
    original = sidecar_path.read_bytes()

    with pytest.raises(MODULE.IntegrityError, match="refusing to overwrite"):
        MODULE._write_sidecar(sidecar_path, {"status": "FAIL"})

    assert sidecar_path.read_bytes() == original


def test_canonical_type_tamper_is_rejected_not_loosely_equal(tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    _write_json(report_path, _report())
    sidecar, passed = MODULE.evaluate(
        POLICY,
        report_path,
        "260729",
        structural_v5=_v5_attestation(report_path),
        baseline_verification=_baseline_attestation(),
        benchmark_verification=_benchmark_attestation(),
    )
    assert passed is True and sidecar["rail_count"] == 16
    sidecar_path = tmp_path / "result.json"
    MODULE._write_sidecar(sidecar_path, sidecar)
    raw = sidecar_path.read_bytes()
    tampered = raw.replace(b'"rail_count":16', b'"rail_count":16.0')
    assert tampered != raw
    sidecar_path.write_bytes(tampered)
    monkeypatch.setattr(
        MODULE,
        "_run_structural_v5",
        lambda snapshot, _validator: _v5_attestation(snapshot),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_baseline_thresholds",
        lambda _policy, case_id, _root: _baseline_attestation(case_id),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_benchmark_source",
        lambda _policy, _root: _benchmark_attestation(),
    )

    with pytest.raises(MODULE.IntegrityError, match="canonical_recomputed_bytes"):
        MODULE.verify_sidecar(POLICY, report_path, sidecar_path)


def test_current_benchmark_source_matches_policy_and_trust_boundary():
    policy = _policy()

    assert MODULE._verify_benchmark_source(policy, ROOT) == _benchmark_attestation()
    assert policy["trust_boundary"]["excluded_scope"] == (
        "does_not_recompute_solver_curves_or_reported_metrics"
    )
    assert policy["trust_boundary"]["generator_authentication"] == (
        "not_cryptographic"
    )
