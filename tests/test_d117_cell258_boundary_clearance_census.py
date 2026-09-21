from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from tools.research import d117_cell258_boundary_clearance_census as module


def test_pinned_helper_identity_matches_current_bytes():
    source = module.HELPER_PATH.read_bytes()
    assert len(source) == module.HELPER_SIZE
    assert hashlib.sha256(source).hexdigest() == module.HELPER_SHA256


def _same_cell_pslg() -> dict[str, np.ndarray]:
    return {
        "vertices": np.array(((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)), dtype=np.float64),
        "segments": np.array(((0, 1), (2, 3), (0, 1), (2, 3)), dtype=np.int32),
    }


def _prepared_metadata() -> dict[str, object]:
    return {
        "program": module.PRODUCT,
        "version": module.VERSION,
        "status": "READY_FOR_NO_TRIANGLE_IMPLEMENTATION",
        "c1_execution_status": module.C1_EXECUTION_STATUS,
        "canonical": {"bytes": module.EXPECTED_CANONICAL_BYTES, "sha256": module.EXPECTED_CANONICAL_SHA256},
        "triangle_modules_before": (),
        "triangle_modules_after": (),
        "triangle_extension_loaded": False,
        "solver_executed": False,
        "pslg": {},
    }


def test_exact_expansion_floor_at_negative_and_positive_boundaries():
    assert module._floor_expanded(-49_700.0, -1, 0) == -1
    assert module._floor_expanded(-49_700.0, 1, 0) == 0
    assert module._floor_expanded(49_700.0, -1, 0) == 709
    assert module._floor_expanded(49_700.0, 1, 0) == 710
    boundary = -49_700.0 + 140.0
    assert module._floor_expanded(boundary, -1, 0) == 0
    assert module._floor_expanded(boundary, 1, 0) == 1


def test_records_hash_and_scalar_rle_metrics_are_deterministic():
    first = module.census_pslg(_same_cell_pslg())
    second = module.census_pslg(_same_cell_pslg())
    assert first["record_count"] == 4
    assert first["occupied_cell_count"] == 1
    assert first["max_occupancy"] == 4
    assert first["candidate_visit_upper_bound"] == 6
    assert first["record_nbytes"] == 32
    assert first["packed_records_sha256"] == "d5b10fb23c40735d5f2717447afa9359ffaa32c227e293cec301ae40d415bc0c"
    assert first["packed_records_sha256"] == second["packed_records_sha256"]
    assert first["candidate_visit_upper_bound"] == second["candidate_visit_upper_bound"]


def _frozen_receipt_payload() -> dict[str, object]:
    return {
        "status": module.OVERALL_STATUS,
        "overall_status": module.OVERALL_STATUS,
        "occupancy_status": module.OCCUPANCY_STATUS,
        "segment_count": module.EXPECTED_SEGMENT_COUNT,
        "actual_record_count": 242_165,
        "record_count": 242_165,
        "candidate_visit_upper_bound": module.APPROVED_CANDIDATE_VISIT_CAP,
        "approved_candidate_visit_cap": None,
        "packed_records_sha256": module.FROZEN_PACKED_RECORDS_SHA256,
        "sorted_packed_records_sha256": module.FROZEN_PACKED_RECORDS_SHA256,
        "maximum_coordinate_dyadic_denominator_exponent": module.FROZEN_MAX_COORDINATE_EXPONENT,
        "actual_concurrent_ndarray_bytes": module.FROZEN_ACTUAL_NDARRAY_BYTES,
        "concurrent_ndarray_bytes_cap": module.COMBINED_NDARRAY_BYTES_CAP,
        "pair_loop_entered": False,
        "triangle_extension_loaded": False,
        "solver_executed": False,
        "canonical": {"bytes": module.EXPECTED_CANONICAL_BYTES, "sha256": module.EXPECTED_CANONICAL_SHA256},
    }


def _write_synthetic_frozen_receipt(path, payload: dict[str, object], monkeypatch) -> bytes:
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("ascii")
    path.write_bytes(data)
    monkeypatch.setattr(module, "FROZEN_OCCUPANCY_RECEIPT_BYTES", len(data))
    monkeypatch.setattr(module, "FROZEN_OCCUPANCY_RECEIPT_SHA256", hashlib.sha256(data).hexdigest())
    return data


def test_frozen_receipt_validator_requires_unapproved_null_cap(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-occupancy.json"
    payload = _frozen_receipt_payload()
    _write_synthetic_frozen_receipt(path, payload, monkeypatch)
    accepted = module._validate_frozen_occupancy_receipt(path)
    assert accepted["payload"]["approved_candidate_visit_cap"] is None

    payload["approved_candidate_visit_cap"] = module.APPROVED_CANDIDATE_VISIT_CAP
    _write_synthetic_frozen_receipt(path, payload, monkeypatch)
    with pytest.raises(module.Refusal, match="approved candidate cap"):
        module._validate_frozen_occupancy_receipt(path)

    payload.pop("approved_candidate_visit_cap")
    _write_synthetic_frozen_receipt(path, payload, monkeypatch)
    with pytest.raises(module.Refusal, match="approved candidate cap"):
        module._validate_frozen_occupancy_receipt(path)


def test_frozen_receipt_validator_rejects_real_wrong_bytes(tmp_path):
    path = tmp_path / "wrong-occupancy.json"
    path.write_bytes(b"{}\n")
    with pytest.raises(module.Refusal, match="size/hash"):
        module._validate_frozen_occupancy_receipt(path)


def test_four_cell_segment_and_total_record_cap_fail_closed():
    pslg = {
        "vertices": np.array(((-49_630.0, -49_630.0), (-49_559.0, -49_559.0)), dtype=np.float64),
        "segments": np.array(((0, 1), (0, 1)), dtype=np.int32),
    }
    result = module.census_pslg(pslg)
    assert result["record_count"] == 8
    assert result["max_cells_per_segment"] == 4
    assert result["record_count_cap"] == 8

    too_wide = {
        "vertices": np.array(((-49_630.0, -49_630.0), (-49_350.0, -49_620.0)), dtype=np.float64),
        "segments": np.array(((0, 1), (0, 1)), dtype=np.int32),
    }
    with pytest.raises(module.Refusal, match="more than four cells"):
        module.census_pslg(too_wide)

    original = module._iter_segment_records

    def too_many(vertices, segments, index):
        yield from original(vertices, segments, index)
        yield module._packed_record(0, 0, index)

    module._iter_segment_records = too_many
    try:
        with pytest.raises(module.Refusal):
            module.census_pslg(pslg)
    finally:
        module._iter_segment_records = original


def test_fixed_record_cap_fails_before_record_allocation(monkeypatch):
    calls = []
    original_empty = module.np.empty
    monkeypatch.setattr(module, "MAX_RECORD_COUNT", 3)
    monkeypatch.setattr(module.np, "empty", lambda *args, **kwargs: (calls.append((args, kwargs)), original_empty(*args, **kwargs))[1])
    with pytest.raises(module.Refusal, match="fixed packed record cap"):
        module.census_pslg(_same_cell_pslg())
    assert calls == []


def test_stop_status_proves_no_pair_or_distance_authority():
    result = module.census_pslg(_same_cell_pslg())
    assert result["status"] == "STOP_C0_BOUNDARY_CLEARANCE_EXACT_CAP_UNAPPROVED"
    assert result["occupancy_status"] == "PASS_C0_BOUNDARY_CLEARANCE_OCCUPANCY"
    assert result["approved_candidate_visit_cap"] is None
    assert result["pair_loop_entered"] is False
    assert result["exact_distance_evaluations"] == 0
    assert result["all_nonincident_frozen_split_pslg_segments_gt_2delta"] is None
    assert result["all_nonincident_frozen_split_pslg_segments_gt_2delta_status"] == "unknown"
    assert result["raw_source_segments_clearance_evaluated"] is False
    assert result["raw_source_segments_clearance_result"] == "unknown"
    assert result["scan_complete"] is False
    assert result["decision_complete"] is False
    assert "all_nonincident_source_segments_gt_2delta" not in result
    assert result["solver_executed"] is False
    assert result["triangle_extension_loaded"] is False


def test_self_check_is_synthetic_only(monkeypatch):
    monkeypatch.setattr(module, "_load_helper_once", lambda: (_ for _ in ()).throw(AssertionError("production loader called")))
    assert module._self_check()["status"] == module.OVERALL_STATUS


def test_source_preparation_evidence_binds_exact_metadata():
    evidence = module._validate_source_preparation(_prepared_metadata())
    assert evidence["program"] == module.PRODUCT
    assert evidence["version"] == module.VERSION
    assert evidence["canonical"] == {"bytes": 12_057_453, "sha256": module.EXPECTED_CANONICAL_SHA256}
    assert evidence["triangle_modules_before"] == []
    assert evidence["triangle_modules_after"] == []
    assert evidence["triangle_modules_before_count"] == evidence["triangle_modules_after_count"] == 0
    assert evidence["triangle_modules_equal"] is True
    assert evidence["pslg"] == {"is_dict": True}


@pytest.mark.parametrize(
    "field,value",
    (
        ("canonical", {"bytes": 1, "sha256": module.EXPECTED_CANONICAL_SHA256}),
        ("triangle_modules_before", ("triangle",)),
        ("triangle_modules_after", ("triangle",)),
        ("triangle_extension_loaded", True),
        ("solver_executed", True),
    ),
)
def test_source_preparation_evidence_refuses_untrusted_metadata(field, value):
    prepared = _prepared_metadata()
    prepared[field] = value
    with pytest.raises(module.Refusal):
        module._validate_source_preparation(prepared)


def test_run_production_attaches_evidence_and_checks_frozen_metrics(monkeypatch, tmp_path):
    prepared = _prepared_metadata()
    prepared["pslg"] = _same_cell_pslg()
    monkeypatch.setattr(module, "_load_helper_once", lambda: (prepared, {"path": "pinned", "size_bytes": 1, "sha256": "h"}))
    result = module.census_pslg(prepared["pslg"])
    result["segment_count"] = module.EXPECTED_SEGMENT_COUNT
    result["input_ndarray_bytes"] = module.INPUT_ARRAY_BYTES_CAP
    result["max_segment_length_um"] = module.EXPECTED_MAX_SEGMENT_LENGTH_UM
    monkeypatch.setattr(module, "census_pslg", lambda _pslg: result)
    published = []
    monkeypatch.setattr(module, "write_receipt_atomic", lambda path, payload: published.append((path, payload)))
    output = module.run_production(tmp_path / "receipt.json")
    assert output["source_preparation"]["canonical"]["bytes"] == module.EXPECTED_CANONICAL_BYTES
    assert output["source_preparation"]["triangle_modules_before_count"] == 0
    assert output["triangle_extension_loaded"] is False and output["solver_executed"] is False
    assert len(published) == 1

    refused = dict(result)
    refused["segment_count"] = 1
    monkeypatch.setattr(module, "census_pslg", lambda _pslg: refused)
    published.clear()
    with pytest.raises(module.Refusal, match="frozen source"):
        module.run_production(tmp_path / "refused.json")
    assert published == []


def test_writer_refuses_existing_and_cleans_forced_failure(tmp_path, monkeypatch):
    payload = {"program": module.PRODUCT, "version": module.VERSION, "value": 1}
    existing = tmp_path / "existing.json"
    existing.write_text("keep", encoding="ascii")
    with pytest.raises(module.Refusal):
        module.write_receipt_atomic(existing, payload)
    assert existing.read_text(encoding="ascii") == "keep"

    destination = tmp_path / "forced.json"
    with pytest.raises(OSError):
        monkeypatch.setattr(module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError("forced publication failure")))
        module.write_receipt_atomic(destination, payload)
    assert not destination.exists()
    assert list(tmp_path.glob(".forced.json.*.tmp")) == []


def test_writer_no_clobber_race_preserves_raced_destination(tmp_path, monkeypatch):
    payload = {"program": module.PRODUCT, "version": module.VERSION, "value": 1}
    destination = tmp_path / "race.json"

    def race_link(_temporary, target):
        target.write_bytes(b"raced")
        raise FileExistsError("destination appeared")

    monkeypatch.setattr(module.os, "link", race_link)
    with pytest.raises(FileExistsError):
        module.write_receipt_atomic(destination, payload)
    assert destination.read_bytes() == b"raced"
    assert list(tmp_path.glob(".race.json.*.tmp")) == []


def _exact_pslg(vertices, segments, markers=None) -> dict[str, np.ndarray]:
    if markers is None:
        markers = [1] * len(segments)
    return {
        "vertices": np.array(vertices, dtype=np.float64),
        "segments": np.array(segments, dtype=np.int32),
        "segment_markers": np.array(markers, dtype=np.int32).reshape((-1, 1)).copy(),
    }


def _scan(pslg, monkeypatch=None):
    bundle = module._build_record_bundle(pslg)
    if monkeypatch is not None:
        monkeypatch.setattr(module, "EXACT_DEADLINE_SECONDS", 270.0)
    return bundle, module._scan_exact_records(bundle)


def test_exact_crossing_witness_has_deterministic_intersection_fields():
    pslg = _exact_pslg(((0.0, 0.0), (4.0, 4.0), (0.0, 4.0), (4.0, 0.0)), ((0, 1), (2, 3), (0, 2), (1, 3)), (16, 32, 16, 32))
    first_bundle = module._build_record_bundle(pslg)
    first = module._scan_exact_records(first_bundle)
    second = module._scan_exact_records(module._build_record_bundle(pslg))
    assert first["status"] == module.EXACT_STATUS_WITNESS
    assert first["witness"] == second["witness"]
    witness = first["witness"]
    assert witness["predicate_case"] == "closed_segment_intersection"
    assert witness["segment_index_a"] == 0 and witness["segment_index_b"] == 1
    assert witness["endpoint_indices_a"] == [0, 1] and witness["endpoint_indices_b"] == [2, 3]
    assert witness["marker_a"] == 16 and witness["marker_b"] == 32
    assert witness["owner_cell"] == [354, 354]
    scale = module.EXACT_COORDINATE_SCALE
    assert witness["scaled_endpoints"] == [[0, 0], [4 * scale, 4 * scale], [0, 4 * scale], [4 * scale, 0]]
    assert witness["r_squared"] == module.EXACT_CLEARANCE_R_SQUARED
    assert witness["distance_numerator"] == 0 and witness["distance_denominator"] == 1
    orientation = 16 * scale * scale
    assert witness["orientations"] == [orientation, -orientation, -orientation, orientation]
    assert first["counter_scope"] == "through first witness inclusive"
    assert first["scan_complete"] is False
    assert first["decision_complete"] is True
    assert first["all_nonincident_frozen_split_pslg_segments_gt_2delta"] is False
    assert first["all_nonincident_frozen_split_pslg_segments_gt_2delta_status"] == "witness"
    assert first["raw_source_segments_clearance_evaluated"] is False
    assert first["raw_source_segments_clearance_result"] == "unknown"


def test_exact_threshold_equality_is_a_distance_witness():
    offset = float(1 << 18) / float(1 << 48)
    pslg = _exact_pslg(((1.0, 0.0), (3.0, 0.0), (0.0, offset), (4.0, offset)), ((0, 1), (2, 3), (0, 2), (1, 3)))
    result = module._scan_exact_records(module._build_record_bundle(pslg))
    assert result["status"] == module.EXACT_STATUS_WITNESS
    witness = result["witness"]
    assert witness["owner_cell"] == [355, 355]
    assert witness["endpoint_indices_a"] == [0, 1] and witness["endpoint_indices_b"] == [2, 3]
    assert witness["marker_a"] == 1 and witness["marker_b"] == 1
    assert witness["scaled_endpoints"] == [[module.EXACT_COORDINATE_SCALE, 0], [3 * module.EXACT_COORDINATE_SCALE, 0], [0, 1 << 18], [4 * module.EXACT_COORDINATE_SCALE, 1 << 18]]
    assert witness["r_squared"] == module.EXACT_CLEARANCE_R_SQUARED
    assert result["witness"]["predicate_case"] == "distance_interior_projection"
    assert result["witness"]["distance_numerator"] == module.EXACT_CLEARANCE_R_SQUARED * (4 * module.EXACT_COORDINATE_SCALE) ** 2
    assert result["witness"]["distance_denominator"] == (4 * module.EXACT_COORDINATE_SCALE) ** 2
    assert result["witness"]["orientations"] is None


def test_exact_one_scaled_unit_beyond_threshold_passes():
    offset = float((1 << 18) + 1) / float(1 << 48)
    pslg = _exact_pslg(((0.0, 0.0), (4.0, 0.0), (0.0, offset), (4.0, offset)), ((0, 1), (2, 3), (0, 2), (1, 3)))
    result = module._scan_exact_records(module._build_record_bundle(pslg))
    assert result["status"] == module.EXACT_STATUS_PASS
    assert result["exact_clearance"] is True
    assert result["scan_complete"] is True
    assert result["decision_complete"] is True
    assert result["all_nonincident_frozen_split_pslg_segments_gt_2delta"] is True
    assert result["all_nonincident_frozen_split_pslg_segments_gt_2delta_status"] == "pass"
    assert result["raw_source_segments_clearance_evaluated"] is False
    assert result["raw_source_segments_clearance_result"] == "unknown"
    assert module._scaled_coordinate(offset) == (1 << 18) + 1
    assert result["endpoint_distance_evaluations"] >= 4


def test_exact_cross_cell_duplicate_is_evaluated_once():
    boundary = -49_560.0
    pslg = _exact_pslg(((boundary - 1.0, 1.0), (boundary + 1.0, 1.0), (boundary - 1.0, 3.0), (boundary + 1.0, 3.0)), ((0, 1), (2, 3), (0, 2), (1, 3)))
    result = module._scan_exact_records(module._build_record_bundle(pslg))
    assert result["status"] == module.EXACT_STATUS_PASS
    assert result["raw_visits"] == 6
    assert result["duplicate_visits"] == 1
    assert result["exact_unique_nonincident_pairs"] == 1
    assert result["exact_pair_evaluations"] == 1
    assert result["counter_identity_holds"] is True
    assert result["candidate_visit_identity_holds"] is True
    assert result["exact_pair_identity_holds"] is True


def test_exact_shared_index_is_incident_after_owner_selection():
    pslg = _exact_pslg(((0.0, 0.0), (4.0, 0.0), (0.0, 4.0)), ((0, 1), (0, 2), (1, 2)))
    result = module._scan_exact_records(module._build_record_bundle(pslg))
    assert result["status"] == module.EXACT_STATUS_PASS
    assert result["incident_unique_pairs"] == 3
    assert result["exact_unique_nonincident_pairs"] == 0
    assert result["counter_identity_holds"] is True
    assert result["candidate_visit_identity_holds"] is True
    assert result["exact_pair_identity_holds"] is True


def test_exact_cap_and_deadline_stop_before_publication(monkeypatch):
    pslg = _exact_pslg(((0.0, 0.0), (4.0, 0.0), (0.0, 4.0), (4.0, 4.0)), ((0, 1), (2, 3), (0, 2), (1, 3)))
    monkeypatch.setattr(module, "APPROVED_CANDIDATE_VISIT_CAP", 0)
    capped = module._scan_exact_records(module._build_record_bundle(pslg))
    assert capped["status"] == module.EXACT_STATUS_INCOMPLETE
    assert capped["stop_reason"] == "candidate_visit_cap"
    assert capped["raw_visits"] == 1
    assert capped["scan_complete"] is False and capped["decision_complete"] is False
    assert capped["all_nonincident_frozen_split_pslg_segments_gt_2delta"] is None
    assert capped["raw_source_segments_clearance_evaluated"] is False
    assert capped["raw_source_segments_clearance_result"] == "unknown"
    monkeypatch.setattr(module, "APPROVED_CANDIDATE_VISIT_CAP", 375_962)
    deadline = module._scan_exact_records(module._build_record_bundle(pslg), deadline_seconds=-1.0)
    assert deadline["status"] == module.EXACT_STATUS_INCOMPLETE
    assert deadline["stop_reason"] == "deadline"
    assert deadline["raw_visits"] == 0


def test_exact_pass_enforces_rebuilt_candidate_visit_identity():
    pslg = _exact_pslg(((0.0, 0.0), (4.0, 0.0), (0.0, 4.0), (4.0, 4.0)), ((0, 1), (2, 3), (0, 2), (1, 3)))
    bundle = module._build_record_bundle(pslg)
    bundle["candidate_visit_upper_bound"] += 1
    result = module._scan_exact_records(bundle)
    assert result["status"] == module.EXACT_STATUS_INCOMPLETE
    assert result["stop_reason"] == "counter_identity"
    assert result["candidate_visit_identity_holds"] is False
    assert result["counter_identity_holds"] is True
    assert result["exact_pair_identity_holds"] is True


def test_pinned_receipt_mismatch_refuses_before_helper_or_publication(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_validate_frozen_occupancy_receipt", lambda _path: (_ for _ in ()).throw(module.Refusal("receipt mismatch")))
    monkeypatch.setattr(module, "_load_helper_once", lambda: (_ for _ in ()).throw(AssertionError("helper called")))
    with pytest.raises(module.Refusal, match="receipt mismatch"):
        module.run_exact_production(tmp_path / "missing.json", tmp_path / "new.json")


def test_rebuilt_hash_mismatch_refuses_before_pair_loop_or_publication(monkeypatch, tmp_path):
    prepared = _prepared_metadata()
    prepared["pslg"] = _exact_pslg(((0.0, 0.0), (4.0, 0.0)), ((0, 1), (0, 1)))
    occupancy = {"receipt_size_bytes": 3448, "receipt_sha256": "receipt", "canonical": {"bytes": module.EXPECTED_CANONICAL_BYTES, "sha256": module.EXPECTED_CANONICAL_SHA256}}
    monkeypatch.setattr(module, "_validate_frozen_occupancy_receipt", lambda _path: occupancy)
    monkeypatch.setattr(module, "_load_helper_once", lambda: (prepared, {"path": "pinned", "size_bytes": 1, "sha256": "h"}))
    monkeypatch.setattr(module, "_build_record_bundle", lambda _pslg: {"result": {"sorted_packed_records_sha256": "wrong", "concurrent_ndarray_bytes_cap": module.COMBINED_NDARRAY_BYTES_CAP}, "segment_count": module.EXPECTED_SEGMENT_COUNT, "record_count": 242_165, "candidate_visit_upper_bound": module.APPROVED_CANDIDATE_VISIT_CAP, "packed_records_sha256": "wrong", "maximum_coordinate_dyadic_denominator_exponent": 48, "actual_ndarray_bytes": module.FROZEN_ACTUAL_NDARRAY_BYTES, "arrays": prepared["pslg"], "records": np.array([0], dtype=np.uint64)})
    monkeypatch.setattr(module, "_scan_exact_records", lambda _bundle: (_ for _ in ()).throw(AssertionError("pair loop called")))
    with pytest.raises(module.Refusal, match="packed record hash mismatch"):
        module.run_exact_production(tmp_path / "occupancy.json", tmp_path / "new.json")


def test_rebuilt_canonical_mismatch_refuses_same_occupancy_before_scan(monkeypatch, tmp_path):
    base = _exact_pslg(((1.0, 1.0), (4.0, 1.0), (1.0, 4.0), (4.0, 4.0)), ((0, 1), (2, 3), (0, 2), (1, 3)), (16, 32, 16, 32))
    changed = _exact_pslg(((2.0, 2.0), (5.0, 2.0), (2.0, 5.0), (5.0, 5.0)), ((0, 1), (2, 3), (0, 3), (1, 2)), (17, 33, 17, 33))
    assert not np.array_equal(base["vertices"], changed["vertices"])
    assert not np.array_equal(base["segments"], changed["segments"])
    assert not np.array_equal(base["segment_markers"], changed["segment_markers"])
    base_bundle = module._build_record_bundle(base)
    changed_bundle = module._build_record_bundle(changed)
    for key in ("record_count", "candidate_visit_upper_bound", "maximum_coordinate_dyadic_denominator_exponent", "actual_ndarray_bytes"):
        assert changed_bundle[key] == base_bundle[key]
    assert changed_bundle["packed_records_sha256"] == base_bundle["packed_records_sha256"]
    changed_bundle.update({
        "segment_count": module.EXPECTED_SEGMENT_COUNT,
        "record_count": 242_165,
        "candidate_visit_upper_bound": module.APPROVED_CANDIDATE_VISIT_CAP,
        "maximum_coordinate_dyadic_denominator_exponent": module.FROZEN_MAX_COORDINATE_EXPONENT,
        "actual_ndarray_bytes": module.FROZEN_ACTUAL_NDARRAY_BYTES,
        "packed_records_sha256": module.FROZEN_PACKED_RECORDS_SHA256,
    })
    changed_bundle["result"].update({
        "segment_count": module.EXPECTED_SEGMENT_COUNT,
        "actual_record_count": 242_165,
        "record_count": 242_165,
        "candidate_visit_upper_bound": module.APPROVED_CANDIDATE_VISIT_CAP,
        "packed_records_sha256": module.FROZEN_PACKED_RECORDS_SHA256,
        "sorted_packed_records_sha256": module.FROZEN_PACKED_RECORDS_SHA256,
        "maximum_coordinate_dyadic_denominator_exponent": module.FROZEN_MAX_COORDINATE_EXPONENT,
        "actual_concurrent_ndarray_bytes": module.FROZEN_ACTUAL_NDARRAY_BYTES,
    })
    prepared = _prepared_metadata()
    prepared["pslg"] = base

    def recompute(pslg):
        if np.array_equal(pslg["vertices"], base["vertices"]) and np.array_equal(pslg["segments"], base["segments"]) and np.array_equal(pslg["segment_markers"], base["segment_markers"]):
            return module.EXPECTED_CANONICAL_BYTES, module.EXPECTED_CANONICAL_SHA256
        return module.EXPECTED_CANONICAL_BYTES, "0" * 64

    prepared[module._PINNED_CANONICAL_RECOMPUTE_KEY] = recompute
    occupancy = {"canonical": {"bytes": module.EXPECTED_CANONICAL_BYTES, "sha256": module.EXPECTED_CANONICAL_SHA256}}
    scanned = []
    published = []
    monkeypatch.setattr(module, "_validate_frozen_occupancy_receipt", lambda _path: occupancy)
    monkeypatch.setattr(module, "_load_helper_once", lambda: (prepared, {"path": "pinned", "size_bytes": 1, "sha256": "h"}))
    monkeypatch.setattr(module, "_build_record_bundle", lambda _pslg: changed_bundle)
    monkeypatch.setattr(module, "_scan_exact_records", lambda _bundle: scanned.append(True))
    monkeypatch.setattr(module, "write_receipt_atomic", lambda *_args: published.append(True))
    with pytest.raises(module.Refusal, match="canonical identity"):
        module.run_exact_production(tmp_path / "occupancy.json", tmp_path / "new.json")
    assert scanned == []
    assert published == []


@pytest.mark.parametrize(
    "argv",
    (
        (),
        ("--exact-production",),
        ("--production", "--receipt", "old.json", "--exact-production", "--occupancy-receipt", "old.json", "--exact-receipt", "new.json"),
        ("--exact-production", "--occupancy-receipt", "same.json", "--exact-receipt", "same.json"),
    ),
)
def test_exact_cli_options_fail_closed(argv):
    with pytest.raises(SystemExit):
        module.main(list(argv))


def test_exact_cli_incomplete_result_returns_status_two(monkeypatch, capsys):
    monkeypatch.setattr(module, "run_exact_production", lambda *_args: {"status": module.EXACT_STATUS_INCOMPLETE})
    code = module.main(["--exact-production", "--occupancy-receipt", "old.json", "--exact-receipt", "new.json"])
    captured = capsys.readouterr()
    assert code == 2
    assert module.EXACT_STATUS_INCOMPLETE in captured.err
    assert captured.out == ""


def test_exact_cli_witness_result_returns_status_one(monkeypatch, capsys):
    monkeypatch.setattr(module, "run_exact_production", lambda *_args: {"status": module.EXACT_STATUS_WITNESS})
    code = module.main(["--exact-production", "--occupancy-receipt", "old.json", "--exact-receipt", "new.json"])
    captured = capsys.readouterr()
    assert code == 1
    assert module.EXACT_STATUS_WITNESS in captured.out
    assert captured.err == ""


def test_exact_cli_refusal_is_labeled_incomplete(monkeypatch, capsys):
    monkeypatch.setattr(module, "run_exact_production", lambda *_args: (_ for _ in ()).throw(module.Refusal("contract drift")))
    code = module.main(["--exact-production", "--occupancy-receipt", "old.json", "--exact-receipt", "new.json"])
    captured = capsys.readouterr()
    assert code == 2
    assert module.EXACT_STATUS_INCOMPLETE in captured.err
    assert module.OVERALL_STATUS not in captured.err
    assert captured.out == ""


def test_exact_run_reuses_no_clobber_writer(monkeypatch, tmp_path):
    prepared = _prepared_metadata()
    pslg = _exact_pslg(((0.0, 0.0), (4.0, 0.0)), ((0, 1), (0, 1)))
    bundle = module._build_record_bundle(pslg)
    occupancy = {"receipt_size_bytes": 3448, "receipt_sha256": "receipt", "canonical": {"bytes": module.EXPECTED_CANONICAL_BYTES, "sha256": module.EXPECTED_CANONICAL_SHA256}}
    monkeypatch.setattr(module, "_validate_frozen_occupancy_receipt", lambda _path: occupancy)
    monkeypatch.setattr(module, "_load_helper_once", lambda: (prepared, {"path": "pinned", "size_bytes": 1, "sha256": "h"}))
    monkeypatch.setattr(module, "_validate_rebuilt_exact_contract", lambda *_args: None)
    monkeypatch.setattr(module, "_build_record_bundle", lambda _pslg: bundle)
    monkeypatch.setattr(module, "_scan_exact_records", lambda _bundle: {"status": module.EXACT_STATUS_PASS, "overall_status": module.EXACT_STATUS_PASS, "exact_status": module.EXACT_STATUS_PASS, "exact_clearance": True, "scan_complete": True, "decision_complete": True, "witness": None})
    destination = tmp_path / "existing.json"
    destination.write_text("keep", encoding="ascii")
    with pytest.raises(module.Refusal):
        module.run_exact_production(tmp_path / "occupancy.json", destination)
    assert destination.read_text(encoding="ascii") == "keep"


def test_exact_incomplete_builds_once_and_does_not_publish(monkeypatch, tmp_path):
    prepared = _prepared_metadata()
    pslg = _exact_pslg(((0.0, 0.0), (4.0, 0.0)), ((0, 1), (0, 1)))
    bundle = module._build_record_bundle(pslg)
    occupancy = {"receipt_size_bytes": 3448, "receipt_sha256": "receipt", "canonical": {"bytes": module.EXPECTED_CANONICAL_BYTES, "sha256": module.EXPECTED_CANONICAL_SHA256}}
    builds = []
    published = []
    monkeypatch.setattr(module, "_validate_frozen_occupancy_receipt", lambda _path: occupancy)
    monkeypatch.setattr(module, "_load_helper_once", lambda: (prepared, {"path": "pinned", "size_bytes": 1, "sha256": "h"}))
    monkeypatch.setattr(module, "_validate_rebuilt_exact_contract", lambda *_args: None)
    monkeypatch.setattr(module, "_build_record_bundle", lambda _pslg: (builds.append(True), bundle)[1])
    monkeypatch.setattr(module, "_scan_exact_records", lambda _bundle: {"status": module.EXACT_STATUS_INCOMPLETE, "overall_status": module.EXACT_STATUS_INCOMPLETE, "exact_status": module.EXACT_STATUS_INCOMPLETE, "exact_clearance": None, "scan_complete": False, "decision_complete": False, "witness": None})
    monkeypatch.setattr(module, "write_receipt_atomic", lambda *_args: published.append(True))
    output = module.run_exact_production(tmp_path / "occupancy.json", tmp_path / "new.json")
    assert output["status"] == module.EXACT_STATUS_INCOMPLETE
    assert len(builds) == 1
    assert published == []
