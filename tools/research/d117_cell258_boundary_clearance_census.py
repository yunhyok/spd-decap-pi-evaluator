"""C0 boundary-clearance census with an opt-in exact frozen-PSLG scan.

The default pass builds packed segment/cell occupancy records only.  The
separate exact entry point reuses that single sorted-record build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"

DELTA_UM = 2.0 ** -31
FUTURE_THRESHOLD_UM = 2.0 ** -30
GRID_WIDTH_UM = 140.0
GRID_WIDTH_INT = 140
GRID_ORIGIN_UM = (-49_700, -49_700)
GRID_AXIS_MIN = -1
GRID_AXIS_MAX = 710
GRID_AXIS_WIDTH = 712

SEGMENT_INDEX_BITS = 18
SEGMENT_INDEX_LIMIT = 1 << SEGMENT_INDEX_BITS
MAX_RECORDS_PER_SEGMENT = 4
EXPECTED_SEGMENT_COUNT = 153_246
MAX_RECORD_COUNT = MAX_RECORDS_PER_SEGMENT * EXPECTED_SEGMENT_COUNT
PACKED_RECORD_BYTES_CAP = 4_903_872
INPUT_ARRAY_BYTES_CAP = 4_323_656
COMBINED_NDARRAY_BYTES_CAP = 9_227_528
EXPECTED_CANONICAL_BYTES = 12_057_453
EXPECTED_CANONICAL_SHA256 = "1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b"
EXPECTED_MAX_SEGMENT_LENGTH_UM = 107.88000000000466

OVERALL_STATUS = "STOP_C0_BOUNDARY_CLEARANCE_EXACT_CAP_UNAPPROVED"
OCCUPANCY_STATUS = "PASS_C0_BOUNDARY_CLEARANCE_OCCUPANCY"
CONTROLLED_BUFFER_STATUS = "STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE"
GLOBAL_BOUNDARY_STATUS = "GLOBAL_BOUNDARY_EQUIVALENCE_STOP_ROBUST_OUTPUT_INTERSECTIONS_DEFERRED"
C1_EXECUTION_STATUS = "C1_EXECUTION_STILL_STOP"

EXACT_STATUS_PASS = "PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA"
EXACT_STATUS_WITNESS = "STOP_C0_FROZEN_PSLG_CLEARANCE_WITNESS"
EXACT_STATUS_INCOMPLETE = "STOP_C0_FROZEN_PSLG_CLEARANCE_INCOMPLETE"
EXACT_SOURCE_SEGMENT_SCOPE = "frozen_split_pslg_float64"
EXACT_GLOBAL_BOUNDARY_STATUS = "GLOBAL_BOUNDARY_EQUIVALENCE_STOP"
EXACT_DEADLINE_SECONDS = 270.0
EXACT_COORDINATE_BITS = 48
EXACT_COORDINATE_SCALE = 1 << EXACT_COORDINATE_BITS
EXACT_CLEARANCE_R = 1 << 18
EXACT_CLEARANCE_R_SQUARED = 1 << 36
APPROVED_CANDIDATE_VISIT_CAP = 375_962
FROZEN_OCCUPANCY_RECEIPT_BYTES = 3_448
FROZEN_OCCUPANCY_RECEIPT_SHA256 = "7e4e5e4b7b1c0b4d3aba88bcafbc655a05707bbd9f06d58eda283470676d4c51"
FROZEN_PACKED_RECORDS_SHA256 = "a0c9124c8f59eab11e2bad03a1b177b515157886046428d79f3ea3e24c0bf708"
FROZEN_ACTUAL_NDARRAY_BYTES = 6_260_976
FROZEN_MAX_COORDINATE_EXPONENT = 48

HELPER_PATH = Path(__file__).with_name("d117_triangle_cell258_c1.py")
HELPER_SIZE = 141_539
HELPER_SHA256 = "fe3d6eb82f90d33ac300cde155c919ec6e4b18356f2d3da4dd2fd73e45cee61c"

_INPUT_KEYS = ("vertices", "segments", "segment_markers", "holes")
_PINNED_CANONICAL_RECOMPUTE_KEY = "_pinned_recompute_c0_pslg_canonical"


class Refusal(RuntimeError):
    """Raised when a frozen census contract cannot be proven."""


def _floor_expanded(value: float, direction: int, axis: int) -> int:
    """Floor an exactly expanded float coordinate against the integer grid."""
    if direction not in (-1, 1) or axis not in (0, 1):
        raise Refusal("invalid floor request")
    coordinate = float(value)
    if not math.isfinite(coordinate):
        raise Refusal("nonfinite vertex coordinate")
    numerator, denominator = coordinate.as_integer_ratio()
    scale = 1 << 31
    expanded_numerator = numerator * scale + direction * denominator
    expanded_denominator = denominator * scale
    relative_numerator = expanded_numerator - GRID_ORIGIN_UM[axis] * expanded_denominator
    return relative_numerator // (GRID_WIDTH_INT * expanded_denominator)


def _denominator_exponent(value: float) -> int:
    numerator, denominator = float(value).as_integer_ratio()
    del numerator
    if denominator <= 0 or denominator & (denominator - 1):
        raise Refusal("coordinate denominator is not dyadic")
    return denominator.bit_length() - 1


def _validate_pslg(pslg: object) -> tuple[dict[str, np.ndarray], int]:
    if not isinstance(pslg, dict):
        raise Refusal("PSLG must be an object")
    if "vertices" not in pslg or "segments" not in pslg:
        raise Refusal("PSLG vertices/segments are required")
    vertices = pslg["vertices"]
    segments = pslg["segments"]
    if type(vertices) is not np.ndarray or vertices.dtype != np.dtype(np.float64) or vertices.ndim != 2 or vertices.shape[1] != 2:
        raise Refusal("vertices contract mismatch")
    if type(segments) is not np.ndarray or not np.issubdtype(segments.dtype, np.integer) or segments.ndim != 2 or segments.shape[1] != 2:
        raise Refusal("segments contract mismatch")
    if not vertices.flags.c_contiguous or not vertices.flags.aligned or not vertices.flags.owndata:
        raise Refusal("vertices storage contract mismatch")
    if not segments.flags.c_contiguous or not segments.flags.aligned or not segments.flags.owndata:
        raise Refusal("segments storage contract mismatch")
    count = int(segments.shape[0])
    if count <= 0 or count > SEGMENT_INDEX_LIMIT:
        raise Refusal("segment count does not fit 18 bits")
    if int(vertices.shape[0]) != count:
        raise Refusal("vertices/segments count mismatch")
    for row in range(int(vertices.shape[0])):
        if not math.isfinite(float(vertices[row, 0])) or not math.isfinite(float(vertices[row, 1])):
            raise Refusal("nonfinite vertices")
    if int(segments.min()) < 0 or int(segments.max()) >= count:
        raise Refusal("segment endpoint index out of range")

    arrays: dict[str, np.ndarray] = {"vertices": vertices, "segments": segments}
    for key in _INPUT_KEYS[2:]:
        value = pslg.get(key)
        if value is not None:
            if type(value) is not np.ndarray or not value.flags.c_contiguous or not value.flags.aligned or not value.flags.owndata:
                raise Refusal(f"{key} storage contract mismatch")
            arrays[key] = value
    input_bytes = sum(int(value.nbytes) for value in arrays.values())
    if input_bytes > INPUT_ARRAY_BYTES_CAP:
        raise Refusal("input ndarray cap exceeded")
    return arrays, count


def _segment_bounds(vertices: np.ndarray, segments: np.ndarray, index: int) -> tuple[int, int, int, int, float, int, int]:
    first = int(segments[index, 0])
    second = int(segments[index, 1])
    x_first = float(vertices[first, 0])
    y_first = float(vertices[first, 1])
    x_second = float(vertices[second, 0])
    y_second = float(vertices[second, 1])
    length = math.hypot(x_second - x_first, y_second - y_first)
    if not math.isfinite(length) or length <= 0.0:
        raise Refusal("zero-length or nonfinite segment")
    gx_min = _floor_expanded(min(x_first, x_second), -1, 0)
    gx_max = _floor_expanded(max(x_first, x_second), 1, 0)
    gy_min = _floor_expanded(min(y_first, y_second), -1, 1)
    gy_max = _floor_expanded(max(y_first, y_second), 1, 1)
    for coordinate in (gx_min, gx_max, gy_min, gy_max):
        if coordinate < GRID_AXIS_MIN or coordinate > GRID_AXIS_MAX:
            raise Refusal("expanded cell coordinate outside frozen axis")
    span_x = gx_max - gx_min + 1
    span_y = gy_max - gy_min + 1
    cells = span_x * span_y
    if span_x > 2 or span_y > 2 or cells > MAX_RECORDS_PER_SEGMENT:
        raise Refusal("segment spans more than four cells")
    return gx_min, gx_max, gy_min, gy_max, length, cells, max(
        _denominator_exponent(x_first),
        _denominator_exponent(y_first),
        _denominator_exponent(x_second),
        _denominator_exponent(y_second),
    )


def _packed_record(gx: int, gy: int, segment_index: int) -> int:
    if not (GRID_AXIS_MIN <= gx <= GRID_AXIS_MAX and GRID_AXIS_MIN <= gy <= GRID_AXIS_MAX):
        raise Refusal("packed cell coordinate out of range")
    if not (0 <= segment_index < SEGMENT_INDEX_LIMIT):
        raise Refusal("packed segment index out of range")
    cell = (gx + 1) * GRID_AXIS_WIDTH + (gy + 1)
    if not (0 <= cell < GRID_AXIS_WIDTH * GRID_AXIS_WIDTH):
        raise Refusal("packed cell field out of range")
    packed = (cell << SEGMENT_INDEX_BITS) | segment_index
    if not (0 <= packed < (1 << 64)):
        raise Refusal("packed uint64 overflow")
    return packed


def _iter_segment_records(vertices: np.ndarray, segments: np.ndarray, index: int):
    gx_min, gx_max, gy_min, gy_max, _length, _cells, _exponent = _segment_bounds(vertices, segments, index)
    for gx in range(gx_min, gx_max + 1):
        for gy in range(gy_min, gy_max + 1):
            yield _packed_record(gx, gy, index)


def _rle_metrics(records: np.ndarray) -> tuple[int, int, int]:
    if records.ndim != 1 or not records.flags.c_contiguous:
        raise Refusal("record storage contract mismatch")
    occupied = 0
    maximum = 0
    candidate_visits = 0
    if records.size == 0:
        return occupied, maximum, candidate_visits
    previous_cell: int | None = None
    run = 0
    for raw in records:
        cell = int(raw) >> SEGMENT_INDEX_BITS
        if previous_cell is None:
            previous_cell = cell
            run = 1
        elif cell == previous_cell:
            run += 1
        else:
            occupied += 1
            maximum = max(maximum, run)
            candidate_visits += run * (run - 1) // 2
            previous_cell = cell
            run = 1
    occupied += 1
    maximum = max(maximum, run)
    candidate_visits += run * (run - 1) // 2
    return occupied, maximum, candidate_visits


def _build_record_bundle(pslg: object) -> dict[str, Any]:
    """Build and sort packed records once for occupancy or exact scanning."""
    started = time.perf_counter()
    arrays, segment_count = _validate_pslg(pslg)
    vertices = arrays["vertices"]
    segments = arrays["segments"]
    record_count = 0
    max_segment_length = 0.0
    max_cells = 0
    max_exponent = 0
    observed_gx_min = GRID_AXIS_MAX
    observed_gx_max = GRID_AXIS_MIN
    observed_gy_min = GRID_AXIS_MAX
    observed_gy_max = GRID_AXIS_MIN

    for index in range(segment_count):
        gx_min, gx_max, gy_min, gy_max, length, cells, exponent = _segment_bounds(vertices, segments, index)
        if length + 2.0 * DELTA_UM >= GRID_WIDTH_UM:
            raise Refusal("maximum segment length does not fit one-cell contract")
        record_count += cells
        if record_count > MAX_RECORDS_PER_SEGMENT * segment_count:
            raise Refusal("packed record cap exceeded")
        if record_count > MAX_RECORD_COUNT:
            raise Refusal("fixed packed record cap exceeded")
        max_segment_length = max(max_segment_length, length)
        max_cells = max(max_cells, cells)
        max_exponent = max(max_exponent, exponent)
        observed_gx_min = min(observed_gx_min, gx_min)
        observed_gx_max = max(observed_gx_max, gx_max)
        observed_gy_min = min(observed_gy_min, gy_min)
        observed_gy_max = max(observed_gy_max, gy_max)

    records = np.empty(record_count, dtype=np.dtype("=u8"))
    if not records.dtype.isnative or records.dtype.itemsize != 8:
        raise Refusal("packed record dtype is not native uint64")
    cursor = 0
    for index in range(segment_count):
        for packed in _iter_segment_records(vertices, segments, index):
            if cursor >= record_count:
                raise Refusal("packed record fill overflow")
            records[cursor] = packed
            cursor += 1
    if cursor != record_count:
        raise Refusal("packed record fill count mismatch")
    records.sort(kind="quicksort")
    occupied, maximum, candidate_visits = _rle_metrics(records)
    digest = hashlib.sha256(memoryview(records).cast("B")).hexdigest()
    input_bytes = sum(int(value.nbytes) for value in arrays.values())
    actual_ndarray_bytes = input_bytes + int(records.nbytes)
    if actual_ndarray_bytes > COMBINED_NDARRAY_BYTES_CAP:
        raise Refusal("concurrent ndarray cap exceeded")
    elapsed = time.perf_counter() - started
    result = {
        "program": PRODUCT,
        "version": VERSION,
        "status": OVERALL_STATUS,
        "overall_status": OVERALL_STATUS,
        "occupancy_status": OCCUPANCY_STATUS,
        "delta_um": DELTA_UM,
        "future_threshold_um": FUTURE_THRESHOLD_UM,
        "grid_width_um": GRID_WIDTH_UM,
        "grid_origin_um": list(GRID_ORIGIN_UM),
        "grid_axis_min": GRID_AXIS_MIN,
        "grid_axis_max": GRID_AXIS_MAX,
        "grid_axis_width": GRID_AXIS_WIDTH,
        "segment_count": segment_count,
        "actual_record_count": record_count,
        "record_count": record_count,
        "record_count_cap": MAX_RECORDS_PER_SEGMENT * segment_count,
        "per_input_record_count_cap": MAX_RECORDS_PER_SEGMENT * segment_count,
        "fixed_record_count_cap": MAX_RECORD_COUNT,
        "occupied_cell_count": occupied,
        "max_occupancy": maximum,
        "candidate_visit_upper_bound": candidate_visits,
        "candidate_visits_include_cross_cell_duplicates": True,
        "approved_candidate_visit_cap": None,
        "pair_loop_entered": False,
        "exact_distance_evaluations": 0,
        "all_nonincident_frozen_split_pslg_segments_gt_2delta": None,
        "all_nonincident_frozen_split_pslg_segments_gt_2delta_status": "unknown",
        "raw_source_segments_clearance_evaluated": False,
        "raw_source_segments_clearance_result": "unknown",
        "scan_complete": False,
        "decision_complete": False,
        "packed_records_sha256": digest,
        "sorted_packed_records_sha256": digest,
        "record_dtype": records.dtype.str,
        "record_endianness": "native",
        "record_nbytes": int(records.nbytes),
        "observed_grid_bounds": {
            "gx_min": observed_gx_min,
            "gx_max": observed_gx_max,
            "gy_min": observed_gy_min,
            "gy_max": observed_gy_max,
        },
        "max_cells_per_segment": max_cells,
        "max_segment_length_um": max_segment_length,
        "maximum_coordinate_dyadic_denominator_exponent": max_exponent,
        "elapsed_seconds": elapsed,
        "actual_concurrent_ndarray_bytes": actual_ndarray_bytes,
        "input_ndarray_bytes": input_bytes,
        "input_ndarray_bytes_cap": INPUT_ARRAY_BYTES_CAP,
        "maximum_packed_records_ndarray_bytes_cap": PACKED_RECORD_BYTES_CAP,
        "concurrent_ndarray_bytes_cap": COMBINED_NDARRAY_BYTES_CAP,
        "concurrent_ndarray_exclusions": [
            "Python objects",
            "verified source bytes",
            "code namespace",
            "Shapely/ring storage",
            "NumPy allocator/sort stack",
            "canonical I/O/receipt",
        ],
        "controlled_buffer_status": CONTROLLED_BUFFER_STATUS,
        "global_boundary_equivalence_status": GLOBAL_BOUNDARY_STATUS,
        "c1_execution_status": C1_EXECUTION_STATUS,
        "solver_executed": False,
        "triangle_extension_loaded": False,
        "occupancy": {"status": OCCUPANCY_STATUS},
    }
    return {
        "result": result,
        "arrays": arrays,
        "segment_count": segment_count,
        "records": records,
        "record_count": record_count,
        "occupied_cell_count": occupied,
        "max_occupancy": maximum,
        "candidate_visit_upper_bound": candidate_visits,
        "packed_records_sha256": digest,
        "actual_ndarray_bytes": actual_ndarray_bytes,
        "maximum_coordinate_dyadic_denominator_exponent": max_exponent,
    }


def census_pslg(pslg: object) -> dict[str, Any]:
    """Run the two-pass occupancy census and stop before candidate inspection."""
    return _build_record_bundle(pslg)["result"]


def _strict_json_object(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise Refusal("occupancy receipt is not strict JSON") from exc
    if type(value) is not dict:
        raise Refusal("occupancy receipt must be a JSON object")
    return value


def _validate_frozen_occupancy_receipt(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise Refusal("pinned occupancy receipt read failed") from exc
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != FROZEN_OCCUPANCY_RECEIPT_BYTES or digest != FROZEN_OCCUPANCY_RECEIPT_SHA256:
        raise Refusal("pinned occupancy receipt size/hash mismatch")
    payload = _strict_json_object(data)

    def exact_int(key: str, expected: int) -> None:
        if type(payload.get(key)) is not int or payload[key] != expected:
            raise Refusal(f"pinned occupancy receipt {key} mismatch")

    def exact_string(key: str, expected: str) -> None:
        if type(payload.get(key)) is not str or payload[key] != expected:
            raise Refusal(f"pinned occupancy receipt {key} mismatch")

    exact_string("status", OVERALL_STATUS)
    exact_string("overall_status", OVERALL_STATUS)
    exact_string("occupancy_status", OCCUPANCY_STATUS)
    exact_int("segment_count", EXPECTED_SEGMENT_COUNT)
    exact_int("actual_record_count", 242_165)
    exact_int("record_count", 242_165)
    exact_int("candidate_visit_upper_bound", APPROVED_CANDIDATE_VISIT_CAP)
    if "approved_candidate_visit_cap" not in payload or payload["approved_candidate_visit_cap"] is not None:
        raise Refusal("pinned occupancy receipt approved candidate cap mismatch")
    exact_string("packed_records_sha256", FROZEN_PACKED_RECORDS_SHA256)
    exact_string("sorted_packed_records_sha256", FROZEN_PACKED_RECORDS_SHA256)
    exact_int("maximum_coordinate_dyadic_denominator_exponent", FROZEN_MAX_COORDINATE_EXPONENT)
    exact_int("actual_concurrent_ndarray_bytes", FROZEN_ACTUAL_NDARRAY_BYTES)
    exact_int("concurrent_ndarray_bytes_cap", COMBINED_NDARRAY_BYTES_CAP)
    if payload.get("pair_loop_entered") is not False or payload.get("triangle_extension_loaded") is not False or payload.get("solver_executed") is not False:
        raise Refusal("pinned occupancy receipt execution flags mismatch")

    canonical = payload.get("canonical")
    if canonical is None and isinstance(payload.get("source_preparation"), dict):
        canonical = payload["source_preparation"].get("canonical")
    if not isinstance(canonical, dict) or type(canonical.get("bytes")) is not int or canonical.get("bytes") != EXPECTED_CANONICAL_BYTES or canonical.get("sha256") != EXPECTED_CANONICAL_SHA256:
        raise Refusal("pinned occupancy receipt canonical identity mismatch")
    return {
        "receipt_size_bytes": len(data),
        "receipt_sha256": digest,
        "canonical": {"bytes": canonical["bytes"], "sha256": canonical["sha256"]},
        "payload": payload,
    }


def _validate_exact_pslg(arrays: dict[str, np.ndarray], segment_count: int) -> None:
    markers = arrays.get("segment_markers")
    if type(markers) is not np.ndarray or not np.issubdtype(markers.dtype, np.integer) or not markers.flags.c_contiguous or not markers.flags.aligned or not markers.flags.owndata:
        raise Refusal("exact segment marker contract mismatch")
    if markers.ndim == 1:
        if int(markers.shape[0]) != segment_count:
            raise Refusal("exact segment marker count mismatch")
    elif markers.ndim == 2 and markers.shape[1] == 1:
        if int(markers.shape[0]) != segment_count:
            raise Refusal("exact segment marker count mismatch")
    else:
        raise Refusal("exact segment marker shape mismatch")
    vertices = arrays["vertices"]
    for row in range(int(vertices.shape[0])):
        _scaled_coordinate(vertices[row, 0])
        _scaled_coordinate(vertices[row, 1])


def _scaled_coordinate(value: float) -> int:
    exponent = _denominator_exponent(value)
    if exponent > EXACT_COORDINATE_BITS:
        raise Refusal("coordinate denominator exponent exceeds exact bound")
    numerator, denominator = float(value).as_integer_ratio()
    if denominator != (1 << exponent):
        raise Refusal("coordinate denominator is not an exact dyadic")
    return int(numerator) << (EXACT_COORDINATE_BITS - exponent)


def _segment_marker(markers: np.ndarray, index: int) -> int:
    if markers.ndim == 1:
        return int(markers[index])
    return int(markers[index, 0])


def _scaled_segment(vertices: np.ndarray, segments: np.ndarray, index: int) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    endpoint_a = int(segments[index, 0])
    endpoint_b = int(segments[index, 1])
    return (
        (_scaled_coordinate(vertices[endpoint_a, 0]), _scaled_coordinate(vertices[endpoint_a, 1])),
        (_scaled_coordinate(vertices[endpoint_b, 0]), _scaled_coordinate(vertices[endpoint_b, 1])),
        (endpoint_a, endpoint_b),
    )


def _orientation(first: tuple[int, int], second: tuple[int, int], third: tuple[int, int]) -> int:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])


def _on_segment(first: tuple[int, int], second: tuple[int, int], point: tuple[int, int]) -> bool:
    return min(first[0], second[0]) <= point[0] <= max(first[0], second[0]) and min(first[1], second[1]) <= point[1] <= max(first[1], second[1])


def _intersection_witness(first: tuple[int, int], second: tuple[int, int], third: tuple[int, int], fourth: tuple[int, int]) -> list[int] | None:
    orientations = [
        _orientation(first, second, third),
        _orientation(first, second, fourth),
        _orientation(third, fourth, first),
        _orientation(third, fourth, second),
    ]
    if orientations[0] == 0 and _on_segment(first, second, third):
        return orientations
    if orientations[1] == 0 and _on_segment(first, second, fourth):
        return orientations
    if orientations[2] == 0 and _on_segment(third, fourth, first):
        return orientations
    if orientations[3] == 0 and _on_segment(third, fourth, second):
        return orientations
    signs_first = (orientations[0] > 0 and orientations[1] < 0) or (orientations[0] < 0 and orientations[1] > 0)
    signs_second = (orientations[2] > 0 and orientations[3] < 0) or (orientations[2] < 0 and orientations[3] > 0)
    return orientations if signs_first and signs_second else None


def _endpoint_distance(point: tuple[int, int], first: tuple[int, int], second: tuple[int, int]) -> tuple[int, int, str]:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 0:
        raise Refusal("exact distance encountered zero-length segment")
    point_dx = point[0] - first[0]
    point_dy = point[1] - first[1]
    projection = point_dx * dx + point_dy * dy
    if projection <= 0:
        numerator = point_dx * point_dx + point_dy * point_dy
        return numerator, 1, "distance_endpoint_projection"
    if projection >= length_squared:
        point_dx = point[0] - second[0]
        point_dy = point[1] - second[1]
        numerator = point_dx * point_dx + point_dy * point_dy
        return numerator, 1, "distance_endpoint_projection"
    cross = dx * point_dy - dy * point_dx
    return cross * cross, length_squared, "distance_interior_projection"


def _exact_witness(
    segment_index_a: int,
    segment_index_b: int,
    endpoint_indices_a: tuple[int, int],
    endpoint_indices_b: tuple[int, int],
    marker_a: int,
    marker_b: int,
    owner: tuple[int, int],
    endpoints: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]],
    predicate_case: str,
    orientations: list[int] | None,
    numerator: int | None,
    denominator: int | None,
) -> dict[str, Any]:
    return {
        "segment_index_a": segment_index_a,
        "segment_index_b": segment_index_b,
        "endpoint_indices_a": list(endpoint_indices_a),
        "endpoint_indices_b": list(endpoint_indices_b),
        "marker_a": marker_a,
        "marker_b": marker_b,
        "owner_cell": [owner[0], owner[1]],
        "scaled_endpoints": [list(point) for point in endpoints],
        "predicate_case": predicate_case,
        "r_squared": EXACT_CLEARANCE_R_SQUARED,
        "distance_numerator": numerator,
        "distance_denominator": denominator,
        "orientations": orientations,
    }


def _scan_exact_records(bundle: dict[str, Any], deadline_seconds: float = EXACT_DEADLINE_SECONDS) -> dict[str, Any]:
    arrays = bundle["arrays"]
    records = bundle["records"]
    _validate_exact_pslg(arrays, int(bundle["segment_count"]))
    vertices = arrays["vertices"]
    segments = arrays["segments"]
    markers = arrays["segment_markers"]
    started = time.perf_counter()
    raw_visits = 0
    duplicate_visits = 0
    incident_unique_pairs = 0
    exact_unique_nonincident_pairs = 0
    exact_pair_evaluations = 0
    endpoint_distance_evaluations = 0
    candidate_visit_upper_bound = int(bundle["candidate_visit_upper_bound"])
    if time.perf_counter() - started >= deadline_seconds:
        return _exact_scan_result(EXACT_STATUS_INCOMPLETE, None, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "deadline", candidate_visit_upper_bound)

    run_start = 0
    record_count = int(records.size)
    while run_start < record_count:
        current_cell = int(records[run_start]) >> SEGMENT_INDEX_BITS
        run_end = run_start + 1
        while run_end < record_count and int(records[run_end]) >> SEGMENT_INDEX_BITS == current_cell:
            run_end += 1
        current_gx = current_cell // GRID_AXIS_WIDTH - 1
        current_gy = current_cell % GRID_AXIS_WIDTH - 1
        if time.perf_counter() - started >= deadline_seconds:
            return _exact_scan_result(EXACT_STATUS_INCOMPLETE, None, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "deadline", candidate_visit_upper_bound)
        for left in range(run_start, run_end):
            segment_index_a = int(records[left]) & (SEGMENT_INDEX_LIMIT - 1)
            for right in range(left + 1, run_end):
                raw_visits += 1
                if raw_visits > APPROVED_CANDIDATE_VISIT_CAP:
                    return _exact_scan_result(EXACT_STATUS_INCOMPLETE, None, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "candidate_visit_cap", candidate_visit_upper_bound)
                if raw_visits % 1024 == 0 and time.perf_counter() - started >= deadline_seconds:
                    return _exact_scan_result(EXACT_STATUS_INCOMPLETE, None, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "deadline", candidate_visit_upper_bound)
                segment_index_b = int(records[right]) & (SEGMENT_INDEX_LIMIT - 1)
                bounds_a = _segment_bounds(vertices, segments, segment_index_a)
                bounds_b = _segment_bounds(vertices, segments, segment_index_b)
                owner = (max(bounds_a[0], bounds_b[0]), max(bounds_a[2], bounds_b[2]))
                if owner[0] > min(bounds_a[1], bounds_b[1]) or owner[1] > min(bounds_a[3], bounds_b[3]):
                    raise Refusal("candidate pair has no common owner cell")
                if (current_gx, current_gy) != owner:
                    duplicate_visits += 1
                    continue
                scaled_a, scaled_b, endpoint_indices_a = _scaled_segment(vertices, segments, segment_index_a)
                scaled_c, scaled_d, endpoint_indices_b = _scaled_segment(vertices, segments, segment_index_b)
                if endpoint_indices_a[0] in endpoint_indices_b or endpoint_indices_a[1] in endpoint_indices_b:
                    incident_unique_pairs += 1
                    continue
                exact_unique_nonincident_pairs += 1
                exact_pair_evaluations += 1
                orientations = _intersection_witness(scaled_a, scaled_b, scaled_c, scaled_d)
                marker_a = _segment_marker(markers, segment_index_a)
                marker_b = _segment_marker(markers, segment_index_b)
                endpoints = (scaled_a, scaled_b, scaled_c, scaled_d)
                if orientations is not None:
                    witness = _exact_witness(segment_index_a, segment_index_b, endpoint_indices_a, endpoint_indices_b, marker_a, marker_b, owner, endpoints, "closed_segment_intersection", orientations, 0, 1)
                    return _exact_scan_result(EXACT_STATUS_WITNESS, witness, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "witness", candidate_visit_upper_bound)
                for point, first, second in ((scaled_a, scaled_c, scaled_d), (scaled_b, scaled_c, scaled_d), (scaled_c, scaled_a, scaled_b), (scaled_d, scaled_a, scaled_b)):
                    numerator, denominator, case = _endpoint_distance(point, first, second)
                    endpoint_distance_evaluations += 1
                    if numerator <= EXACT_CLEARANCE_R_SQUARED * denominator:
                        witness = _exact_witness(segment_index_a, segment_index_b, endpoint_indices_a, endpoint_indices_b, marker_a, marker_b, owner, endpoints, case, None, numerator, denominator)
                        return _exact_scan_result(EXACT_STATUS_WITNESS, witness, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "witness", candidate_visit_upper_bound)
        run_start = run_end
    if raw_visits != candidate_visit_upper_bound or raw_visits != duplicate_visits + incident_unique_pairs + exact_unique_nonincident_pairs or exact_pair_evaluations != exact_unique_nonincident_pairs:
        return _exact_scan_result(EXACT_STATUS_INCOMPLETE, None, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "counter_identity", candidate_visit_upper_bound)
    return _exact_scan_result(EXACT_STATUS_PASS, None, raw_visits, duplicate_visits, incident_unique_pairs, exact_unique_nonincident_pairs, exact_pair_evaluations, endpoint_distance_evaluations, started, "complete", candidate_visit_upper_bound)


def _exact_scan_result(status: str, witness: dict[str, Any] | None, raw_visits: int, duplicate_visits: int, incident_unique_pairs: int, exact_unique_nonincident_pairs: int, exact_pair_evaluations: int, endpoint_distance_evaluations: int, started: float, scope: str, candidate_visit_upper_bound: int | None = None) -> dict[str, Any]:
    identity_holds = raw_visits == duplicate_visits + incident_unique_pairs + exact_unique_nonincident_pairs
    exact_pair_identity_holds = exact_pair_evaluations == exact_unique_nonincident_pairs
    scan_complete = status == EXACT_STATUS_PASS
    decision_complete = status in (EXACT_STATUS_PASS, EXACT_STATUS_WITNESS)
    return {
        "exact_status": status,
        "status": status,
        "overall_status": status,
        "exact_clearance": True if status == EXACT_STATUS_PASS else False if status == EXACT_STATUS_WITNESS else None,
        "scan_complete": scan_complete,
        "decision_complete": decision_complete,
        "witness": witness,
        "raw_visits": raw_visits,
        "duplicate_visits": duplicate_visits,
        "incident_unique_pairs": incident_unique_pairs,
        "exact_unique_nonincident_pairs": exact_unique_nonincident_pairs,
        "exact_pair_evaluations": exact_pair_evaluations,
        "endpoint_distance_evaluations": endpoint_distance_evaluations,
        "counter_identity_holds": identity_holds,
        "candidate_visit_identity_holds": None if candidate_visit_upper_bound is None else raw_visits == candidate_visit_upper_bound,
        "exact_pair_identity_holds": exact_pair_identity_holds,
        "counter_scope": "through first witness inclusive" if status == EXACT_STATUS_WITNESS else "complete scan" if status == EXACT_STATUS_PASS else "through stop",
        "stop_reason": scope if status == EXACT_STATUS_INCOMPLETE else None,
        "pair_loop_entered": raw_visits > 0,
        "exact_distance_evaluations": endpoint_distance_evaluations,
        "all_nonincident_frozen_split_pslg_segments_gt_2delta": True if status == EXACT_STATUS_PASS else False if status == EXACT_STATUS_WITNESS else None,
        "all_nonincident_frozen_split_pslg_segments_gt_2delta_status": "pass" if status == EXACT_STATUS_PASS else "witness" if status == EXACT_STATUS_WITNESS else "incomplete",
        "raw_source_segments_clearance_evaluated": False,
        "raw_source_segments_clearance_result": "unknown",
        "source_segment_scope": EXACT_SOURCE_SEGMENT_SCOPE,
        "global_boundary_equivalence_status": EXACT_GLOBAL_BOUNDARY_STATUS,
        "c1_execution_status": C1_EXECUTION_STATUS,
        "c1_authorized": False,
        "triangle_status": "STOP",
        "triangle_extension_loaded": False,
        "solver_status": "STOP",
        "solver_executed": False,
        "powersi_status": "STOP",
        "elapsed_seconds": time.perf_counter() - started,
    }


def _validate_rebuilt_exact_contract(bundle: dict[str, Any], occupancy: dict[str, Any], source_preparation: dict[str, Any], canonical_recompute: object | None = None) -> None:
    result = bundle.get("result")
    if not isinstance(result, dict):
        raise Refusal("rebuilt exact contract result missing")
    checks = (
        (bundle.get("segment_count"), EXPECTED_SEGMENT_COUNT, "segment count"),
        (bundle.get("record_count"), 242_165, "record count"),
        (bundle.get("candidate_visit_upper_bound"), APPROVED_CANDIDATE_VISIT_CAP, "candidate visit cap"),
        (bundle.get("packed_records_sha256"), FROZEN_PACKED_RECORDS_SHA256, "packed record hash"),
        (result.get("sorted_packed_records_sha256"), FROZEN_PACKED_RECORDS_SHA256, "sorted record hash"),
        (bundle.get("maximum_coordinate_dyadic_denominator_exponent"), FROZEN_MAX_COORDINATE_EXPONENT, "coordinate exponent"),
        (bundle.get("actual_ndarray_bytes"), FROZEN_ACTUAL_NDARRAY_BYTES, "ndarray bytes"),
        (result.get("concurrent_ndarray_bytes_cap"), COMBINED_NDARRAY_BYTES_CAP, "ndarray cap"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise Refusal(f"rebuilt exact contract {label} mismatch")
    result_checks = (
        (result.get("segment_count"), EXPECTED_SEGMENT_COUNT, "result segment count"),
        (result.get("actual_record_count"), 242_165, "result actual record count"),
        (result.get("record_count"), 242_165, "result record count"),
        (result.get("candidate_visit_upper_bound"), APPROVED_CANDIDATE_VISIT_CAP, "result candidate visit cap"),
        (result.get("packed_records_sha256"), FROZEN_PACKED_RECORDS_SHA256, "result packed record hash"),
        (result.get("maximum_coordinate_dyadic_denominator_exponent"), FROZEN_MAX_COORDINATE_EXPONENT, "result coordinate exponent"),
        (result.get("actual_concurrent_ndarray_bytes"), FROZEN_ACTUAL_NDARRAY_BYTES, "result ndarray bytes"),
    )
    for actual, expected, label in result_checks:
        if actual != expected:
            raise Refusal(f"rebuilt exact contract {label} mismatch")
    if "approved_candidate_visit_cap" not in result or result["approved_candidate_visit_cap"] is not None:
        raise Refusal("rebuilt exact contract approved candidate cap mismatch")
    if occupancy.get("canonical") != source_preparation.get("canonical"):
        raise Refusal("rebuilt exact contract canonical identity mismatch")
    if not callable(canonical_recompute):
        raise Refusal("rebuilt exact contract canonical recompute missing")
    try:
        canonical = canonical_recompute(bundle["arrays"])
    except Exception as exc:
        raise Refusal("rebuilt exact contract canonical identity mismatch") from exc
    if type(canonical) is not tuple or len(canonical) != 2 or type(canonical[0]) is not int or type(canonical[1]) is not str or canonical != (EXPECTED_CANONICAL_BYTES, EXPECTED_CANONICAL_SHA256):
        raise Refusal("rebuilt exact contract canonical identity mismatch")
    _validate_exact_pslg(bundle["arrays"], bundle["segment_count"])


def run_exact_production(occupancy_receipt_path: str | os.PathLike[str], receipt_path: str | os.PathLike[str]) -> dict[str, Any]:
    if os.path.abspath(os.fspath(occupancy_receipt_path)) == os.path.abspath(os.fspath(receipt_path)):
        raise Refusal("exact occupancy and output receipts must be different paths")
    occupancy = _validate_frozen_occupancy_receipt(occupancy_receipt_path)
    prepared, provenance = _load_helper_once()
    source_preparation = _validate_source_preparation(prepared)
    bundle = _build_record_bundle(prepared["pslg"])
    _validate_rebuilt_exact_contract(bundle, occupancy, source_preparation, prepared.get(_PINNED_CANONICAL_RECOMPUTE_KEY))
    exact = _scan_exact_records(bundle)
    result = dict(bundle["result"])
    result.update(exact)
    result.pop("complete", None)
    result["scan_complete"] = result.get("status") == EXACT_STATUS_PASS
    result["decision_complete"] = result.get("status") in (EXACT_STATUS_PASS, EXACT_STATUS_WITNESS)
    result["raw_source_segments_clearance_evaluated"] = False
    result["raw_source_segments_clearance_result"] = "unknown"
    result.update({
        "pinned_occupancy_receipt": {"size_bytes": occupancy["receipt_size_bytes"], "sha256": occupancy["receipt_sha256"]},
        "pinned_helper": provenance,
        "source_preparation": source_preparation,
        "source_segment_scope": EXACT_SOURCE_SEGMENT_SCOPE,
        "global_boundary_equivalence_status": EXACT_GLOBAL_BOUNDARY_STATUS,
        "c1_execution_status": C1_EXECUTION_STATUS,
        "c1_authorized": False,
        "triangle_status": "STOP",
        "triangle_extension_loaded": False,
        "solver_status": "STOP",
        "solver_executed": False,
        "powersi_status": "STOP",
        "approved_candidate_visit_cap": APPROVED_CANDIDATE_VISIT_CAP,
        "exact_deadline_seconds": EXACT_DEADLINE_SECONDS,
        "deadline_enforced_locally": True,
        "process_tree_limit_enforced_locally": False,
        "hard_wall_limit_enforced_locally": False,
        "resource_limit_scope": "process-tree and hard-wall limits are external only",
    })
    if result["status"] in (EXACT_STATUS_PASS, EXACT_STATUS_WITNESS):
        write_receipt_atomic(receipt_path, result)
    return result


def _load_helper_once() -> tuple[dict[str, Any], dict[str, Any]]:
    """Read, verify, compile, and execute the pinned helper exactly once."""
    try:
        source = HELPER_PATH.read_bytes()
    except OSError as exc:
        raise Refusal("pinned helper read failed") from exc
    if len(source) != HELPER_SIZE or hashlib.sha256(source).hexdigest() != HELPER_SHA256:
        raise Refusal("pinned helper size/hash mismatch")
    namespace: dict[str, Any] = {"__name__": "d117_triangle_cell258_c1_pinned", "__file__": str(HELPER_PATH)}
    try:
        exec(compile(source, str(HELPER_PATH), "exec"), namespace)
    except Exception as exc:
        raise Refusal("pinned helper compilation failed") from exc
    prepare = namespace.get("prepare_no_triangle")
    if not callable(prepare):
        raise Refusal("pinned helper preparation entry point missing")
    recompute = namespace.get("recompute_c0_pslg_canonical")
    if not callable(recompute):
        raise Refusal("pinned helper canonical recompute entry point missing")
    try:
        prepared = prepare()
    except Exception as exc:
        raise Refusal("pinned helper preparation refused") from exc
    if not isinstance(prepared, dict) or not isinstance(prepared.get("pslg"), dict):
        raise Refusal("pinned helper preparation result mismatch")
    prepared[_PINNED_CANONICAL_RECOMPUTE_KEY] = recompute
    provenance = {"path": str(HELPER_PATH), "size_bytes": len(source), "sha256": HELPER_SHA256}
    return prepared, provenance


def _validate_source_preparation(prepared: object) -> dict[str, Any]:
    if not isinstance(prepared, dict):
        raise Refusal("source preparation is not an object")
    if prepared.get("program") != PRODUCT or prepared.get("version") != VERSION:
        raise Refusal("source preparation product/version mismatch")
    if prepared.get("status") != "READY_FOR_NO_TRIANGLE_IMPLEMENTATION" or prepared.get("c1_execution_status") != C1_EXECUTION_STATUS:
        raise Refusal("source preparation status mismatch")
    canonical = prepared.get("canonical")
    if not isinstance(canonical, dict) or type(canonical.get("bytes")) is not int or canonical.get("bytes") != EXPECTED_CANONICAL_BYTES or canonical.get("sha256") != EXPECTED_CANONICAL_SHA256:
        raise Refusal("source preparation canonical mismatch")
    before = prepared.get("triangle_modules_before")
    after = prepared.get("triangle_modules_after")
    if not isinstance(before, (tuple, list)) or not isinstance(after, (tuple, list)) or any(type(name) is not str for name in (*before, *after)):
        raise Refusal("source preparation module census mismatch")
    if before or after or tuple(before) != tuple(after):
        raise Refusal("source preparation loaded optional extension")
    if prepared.get("triangle_extension_loaded") is not False or prepared.get("solver_executed") is not False:
        raise Refusal("source preparation execution flags mismatch")
    if not isinstance(prepared.get("pslg"), dict):
        raise Refusal("source preparation PSLG mismatch")
    return {
        "program": prepared["program"],
        "version": prepared["version"],
        "status": prepared["status"],
        "c1_execution_status": prepared["c1_execution_status"],
        "canonical": {"bytes": canonical["bytes"], "sha256": canonical["sha256"]},
        "triangle_modules_before": list(before),
        "triangle_modules_after": list(after),
        "triangle_modules_before_count": len(before),
        "triangle_modules_after_count": len(after),
        "triangle_modules_equal": True,
        "triangle_extension_loaded": False,
        "solver_executed": False,
        "pslg": {"is_dict": True},
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    try:
        return (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as exc:
        raise Refusal("receipt is not strict JSON") from exc


def write_receipt_atomic(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    """Write exactly one strict JSON receipt, refusing replacement."""
    destination = Path(path)
    parent = destination.parent
    if not parent.is_dir() or os.path.lexists(destination):
        raise Refusal("receipt destination is unavailable or already exists")
    data = _json_bytes(payload)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(destination):
            raise Refusal("receipt destination appeared during write")
        os.link(temporary, destination)
        temporary.unlink()
        temporary = None
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise


def run_production(receipt_path: str | os.PathLike[str]) -> dict[str, Any]:
    prepared, provenance = _load_helper_once()
    source_preparation = _validate_source_preparation(prepared)
    result = census_pslg(prepared["pslg"])
    if result["segment_count"] != EXPECTED_SEGMENT_COUNT or result["input_ndarray_bytes"] != INPUT_ARRAY_BYTES_CAP or result["max_segment_length_um"] != EXPECTED_MAX_SEGMENT_LENGTH_UM:
        raise Refusal("production census metrics do not match frozen source")
    result["pinned_helper"] = provenance
    result["source_preparation"] = source_preparation
    result["helper_preparation_status"] = source_preparation["status"]
    result["helper_c1_execution_status"] = source_preparation["c1_execution_status"]
    result["triangle_extension_loaded"] = source_preparation["triangle_extension_loaded"]
    result["solver_executed"] = source_preparation["solver_executed"]
    write_receipt_atomic(receipt_path, result)
    return result


def _self_check() -> dict[str, Any]:
    vertices = np.array(((-49_630.0, -49_630.0), (-49_590.0, -49_590.0), (-49_610.0, -49_620.0)), dtype=np.float64)
    segments = np.array(((0, 1), (1, 2), (2, 0)), dtype=np.int32)
    result = census_pslg({"vertices": vertices, "segments": segments})
    if result["status"] != OVERALL_STATUS or result["occupancy_status"] != OCCUPANCY_STATUS:
        raise Refusal("synthetic status check failed")
    if result["pair_loop_entered"] or result["exact_distance_evaluations"] != 0 or result["approved_candidate_visit_cap"] is not None:
        raise Refusal("synthetic stop check failed")
    if _floor_expanded(-49_700.0, -1, 0) != -1 or _floor_expanded(-49_700.0, 1, 0) != 0:
        raise Refusal("synthetic negative boundary floor check failed")
    if _floor_expanded(49_700.0, -1, 0) != 709 or _floor_expanded(49_700.0, 1, 0) != 710:
        raise Refusal("synthetic positive boundary floor check failed")
    return {"status": result["status"], "occupancy_status": result["occupancy_status"], "record_count": result["record_count"], "packed_records_sha256": result["packed_records_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} d117 cell258 boundary-clearance census")
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    parser.add_argument("--self-check", action="store_true", help="run synthetic-only checks")
    parser.add_argument("--production", action="store_true", help="run the pinned production helper")
    parser.add_argument("--receipt", type=Path, help="explicit JSON receipt destination")
    parser.add_argument("--exact-production", action="store_true", help="opt-in exact frozen-PSLG clearance scan")
    parser.add_argument("--occupancy-receipt", type=Path, help="pinned occupancy receipt for exact scanning")
    parser.add_argument("--exact-receipt", type=Path, help="new exact clearance receipt destination")
    args = parser.parse_args(argv)
    try:
        if args.self_check:
            if args.production or args.receipt is not None or args.exact_production or args.occupancy_receipt is not None or args.exact_receipt is not None:
                raise Refusal("self-check cannot accept production options")
            result = _self_check()
            print(f"{PRODUCT} v{VERSION} {result['status']} self-check PASS")
            return 0
        if args.production:
            if args.exact_production or args.occupancy_receipt is not None or args.exact_receipt is not None:
                parser.error("--production cannot combine with exact-production options")
            if args.receipt is None:
                parser.error("--production and --receipt PATH are required")
            result = run_production(args.receipt)
            print(f"{PRODUCT} v{VERSION} {result['status']}")
            return 0
        if args.exact_production:
            if args.receipt is not None or args.occupancy_receipt is None or args.exact_receipt is None:
                parser.error("--exact-production requires --occupancy-receipt PATH and --exact-receipt PATH only")
            if os.path.abspath(os.fspath(args.occupancy_receipt)) == os.path.abspath(os.fspath(args.exact_receipt)):
                parser.error("--occupancy-receipt and --exact-receipt must be different paths")
            result = run_exact_production(args.occupancy_receipt, args.exact_receipt)
            if not isinstance(result, dict) or result.get("status") not in (EXACT_STATUS_PASS, EXACT_STATUS_WITNESS):
                print(f"{PRODUCT} v{VERSION} {EXACT_STATUS_INCOMPLETE}", file=sys.stderr)
                return 2
            print(f"{PRODUCT} v{VERSION} {result['status']}")
            return 0 if result["status"] == EXACT_STATUS_PASS else 1
        parser.error("choose --self-check, --production with --receipt PATH, or --exact-production with both receipt paths")
    except (Refusal, OSError, ValueError, KeyError) as exc:
        status = EXACT_STATUS_INCOMPLETE if args.exact_production else OVERALL_STATUS
        print(f"{PRODUCT} v{VERSION} {status}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
