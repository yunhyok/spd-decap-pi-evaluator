from __future__ import annotations

import ast
import hashlib
import math
import sys
import weakref
from pathlib import Path

import numpy as np
import pytest
from shapely import wkb
from shapely.geometry import Polygon

from tools.research import d117_triangle_cell258_c1 as module


def _fake_result() -> dict[str, np.ndarray]:
    return {
        "vertices": np.array(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)), dtype=np.float64),
        "vertex_markers": np.array(((1,), (2,), (3,)), dtype=np.int32),
        "triangles": np.array(((0, 1, 2),), dtype=np.int32),
        "segments": np.array(((0, 1), (1, 2), (2, 0)), dtype=np.int32),
        "segment_markers": np.array(((1,), (2,), (3,)), dtype=np.int32),
        "holes": np.array(((0.25, 0.25),), dtype=np.float64),
    }


def _square_result() -> dict[str, np.ndarray]:
    return {
        "vertices": np.array(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)), dtype=np.float64),
        "vertex_markers": np.ones((4, 1), dtype=np.int32),
        "triangles": np.array(((0, 1, 2), (0, 2, 3)), dtype=np.int32),
        "segments": np.array(((0, 1), (1, 2), (2, 3), (3, 0)), dtype=np.int32),
        "segment_markers": np.array(((10,), (20,), (30,), (40,)), dtype=np.int32),
        "holes": np.array(((0.25, 0.25),), dtype=np.float64),
    }


@pytest.fixture
def tiny_polygon_with_hole(monkeypatch):
    polygon = Polygon(
        ((0, 0), (0, 4), (4, 4), (4, 0)),
        holes=(((1, 1), (3, 1), (3, 3), (1, 3)),),
    )
    pslg = module.build_pslg_arrays(polygon, thickness=1.0)
    for name, value in {
        "EXPECTED_PSLG_VERTICES": 8,
        "EXPECTED_PSLG_SEGMENTS": 8,
        "EXPECTED_MARKERS": 8,
        "EXPECTED_HOLES": 1,
        "EXPECTED_RING_COUNT": 2,
        "EXPECTED_PSLG_DISTRIBUTION": {1: 8},
        "T_FLOOR": 8,
    }.items():
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module, "recompute_c0_pslg_canonical", lambda _pslg: (1, "a" * 64))
    triangles = np.array(
        (
            (0, 7, 1),
            (0, 4, 7),
            (1, 6, 2),
            (1, 7, 6),
            (2, 5, 3),
            (2, 6, 5),
            (3, 4, 0),
            (3, 5, 4),
        ),
        dtype=np.int32,
    )
    segments = pslg["segments"].copy()
    segments[[1, 3, 4, 6]] = segments[[1, 3, 4, 6]][:, ::-1]
    result = {
        "vertices": pslg["vertices"].copy(),
        "vertex_markers": np.ones((8, 1), dtype=np.int32),
        "triangles": triangles,
        "segments": segments,
        "segment_markers": pslg["segment_markers"].copy(),
        "holes": pslg["holes"].copy(),
    }
    return pslg, result


def _encoded_boundary_record(first: int, second: int, marker: int) -> int:
    minimum, maximum = sorted((first, second))
    undirected = (minimum << 19) | maximum
    oriented = (undirected << 1) | int(first > second)
    return (oriented << 18) | marker


@pytest.fixture
def small_future_count_floor(monkeypatch):
    monkeypatch.setattr(module, "EXPECTED_PSLG_VERTICES", 3)
    monkeypatch.setattr(module, "EXPECTED_PSLG_SEGMENTS", 3)
    monkeypatch.setattr(module, "EXPECTED_HOLES", 1)
    monkeypatch.setattr(module, "T_FLOOR", 1)


def test_visible_identity_and_stop_status():
    assert (module.PRODUCT, module.VERSION) == ("SPD Decap PI Evaluator", "0.23.1")
    assert module.PREPARATION_STATUS == "READY_FOR_NO_TRIANGLE_IMPLEMENTATION"
    assert module.C1_EXECUTION_STATUS == "C1_EXECUTION_STILL_STOP"
    assert module.FULL_2D_CERT_STATUS == "FULL_2D_CERT_STOP"
    assert module.T_FLOOR == 157_340
    assert module.C0_SIZE == 5_448_871
    assert module.C0_SHA256 == "5ae752680b63686f90dd0d80037b337bba0da71f3bd9d6d755a9e94365f9ac60"
    assert module.EXPECTED_PSLG_CANONICAL_BYTES == 12_057_453
    assert module.EXPECTED_PSLG_CANONICAL_SHA256 == "1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b"
    assert module.TRIANGLE_OPTIONS == "pq15CzS221330"
    assert module.STEINER_POINT_CAP == 221_330
    assert module.STATIC_NATIVE_BOUND == "NOT_PROVABLE_FROM_PINNED_SOURCE_ALONE"
    assert module._FUTURE_RESULT_KEYS == frozenset(("vertices", "vertex_markers", "triangles", "segments", "segment_markers", "holes"))
    assert module.OUTPUT_ARRAY_BYTES_CAP == 20_032_768
    assert module.COMBINED_ARRAY_BYTES_CAP == 24_356_424


def test_no_optional_extension_boundary_is_static_and_dynamic():
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert "triangle" not in imported
    assert "triangulate" not in source.lower()
    assert "unary_union" not in source
    assert ".tolist(" not in source
    assert "read_bytes" not in source
    assert "vertex_index" not in source
    assert callable(module.summarize_positive_area_streaming)
    assert "STATIC_CONTROLLED_BUFFER_MODEL_PASS" not in source
    before = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    module._self_check()
    after = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    assert before == after == set()


def test_production_frozen_cycles_area_and_canonical_regression():
    shape, _ = module.validate_frozen_inputs()
    pslg = module.build_pslg_arrays(shape, module.THICKNESS_UM)
    cycles = None
    try:
        _, _, _, _, cycles = module._boundary_cycle_evidence(
            pslg["vertices"], pslg["segments"][:, 0], pslg["segments"][:, 1]
        )
        assert len(cycles) == 2_049
        assert cycles[0][0] < 0.0
        assert sum(area < 0.0 for area, _ in cycles) == 1
        assert sum(area > 0.0 for area, _ in cycles) == 2_048
        frozen_area = abs(math.fsum(area for area, _ in cycles))
        assert frozen_area == 8_476_333_524.145536
        assert abs(frozen_area - module.EXPECTED_AREA_UM2) == 0.000148773193359375
        tolerance = max(1e-7, frozen_area * 1e-12)
        assert tolerance == 0.008476333524145537
        assert module.stream_pslg_canonical(pslg, require_frozen=True) == (
            12_057_453,
            "1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b",
        )
    finally:
        del cycles, pslg, shape


def test_two_pass_synthetic_polygon_is_owned_numpy_storage():
    polygon = Polygon(((0, 0), (12, 0), (12, 8), (0, 8)), holes=(((3, 2), (5, 2), (5, 4), (3, 4)),))
    pslg = module.build_pslg_arrays(polygon, thickness=1.0)
    assert set(pslg) == {"vertices", "segments", "segment_markers", "holes"}
    assert pslg["vertices"].shape == (pslg["segments"].shape[0], 2)
    assert pslg["segment_markers"].shape == (pslg["segments"].shape[0], 1)
    assert pslg["holes"].shape == (1, 2)
    for value in pslg.values():
        assert type(value) is np.ndarray
        assert value.flags.c_contiguous and value.flags.aligned and value.flags.owndata
    proof = module._validate_pslg_arrays(pslg, polygon, thickness=1.0)
    assert proof["vertex_count"] == proof["segment_count"]
    assert proof["hole_count"] == 1
    assert np.array_equal(pslg["segments"][0], (0, 1))


def test_stream_writer_is_deterministic_and_readback_is_streamed(tmp_path):
    polygon = Polygon(((0, 0), (4, 0), (4, 4), (0, 4)))
    pslg = module.build_pslg_arrays(polygon, thickness=1.0)
    first = module.stream_pslg_canonical(pslg)
    second = module.stream_pslg_canonical(pslg)
    assert first == second
    output = tmp_path / "pslg.canonical"
    assert module.write_streamed_canonical(output, pslg) == first
    assert module.readback_canonical(output, first) == first
    assert output.stat().st_size == first[0]


def test_pslg_canonical_byte_cap_fails_before_final_output(tmp_path):
    polygon = Polygon(((0, 0), (4, 0), (4, 4), (0, 4)))
    pslg = module.build_pslg_arrays(polygon, thickness=1.0)
    with pytest.raises(module.Refusal):
        module.stream_pslg_canonical(pslg, byte_cap=10)
    output = tmp_path / "too-small.canonical"
    with pytest.raises(module.Refusal):
        module.write_streamed_canonical(output, pslg, byte_cap=10)
    assert not output.exists()
    assert not (tmp_path / "too-small.canonical.tmp").exists()


def test_canonical_writer_refuses_existing_destination(tmp_path):
    polygon = Polygon(((0, 0), (4, 0), (4, 4), (0, 4)))
    pslg = module.build_pslg_arrays(polygon, thickness=1.0)
    output = tmp_path / "existing.canonical"
    sentinel = b"preserve this output"
    with output.open("wb") as stream:
        stream.write(sentinel)
    with pytest.raises(module.Refusal):
        module.write_streamed_canonical(output, pslg)
    with output.open("rb") as stream:
        assert stream.read(len(sentinel) + 1) == sentinel
    assert not (tmp_path / "existing.canonical.tmp").exists()


def test_strict_json_rejects_duplicate_and_nonfinite_values():
    with pytest.raises(module.Refusal):
        module._strict_json(b'{"x":1,"x":2}')
    with pytest.raises(module.Refusal):
        module._strict_json(b"1e400")


def test_verified_read_is_one_bounded_bytearray_and_chunk_bound_is_fail_closed(tmp_path):
    payload = b"bounded-read" * 17
    path = tmp_path / "sealed.bin"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    observed = module._read_verified(path, len(payload), digest, chunk_size=3)
    assert type(observed) is bytearray and observed == payload
    for chunk_size in (0, 8193):
        with pytest.raises(module.Refusal):
            module._read_verified(path, len(payload), digest, chunk_size=chunk_size)


def test_bytearray_wkb_loading_uses_actual_minimal_source_helper():
    polygon = Polygon(((0, 0), (1, 0), (1, 1), (0, 1)))
    shape = module._load_wkb_buffer(bytearray(wkb.dumps(polygon)))
    assert shape.geom_type == "Polygon" and shape.equals(polygon)


def test_stream_file_digest_chunk_bound_is_fail_closed(tmp_path):
    payload = b"bounded-digest" * 19
    path = tmp_path / "digest.bin"
    path.write_bytes(payload)
    expected = (len(payload), hashlib.sha256(payload).hexdigest())
    assert module._stream_file_digest(path, chunk_size=3) == expected
    for chunk_size in (0, 8193):
        with pytest.raises(module.Refusal):
            module._stream_file_digest(path, chunk_size=chunk_size)


def test_future_result_exact_ndarray_contract_and_streaming_summary(small_future_count_floor):
    result = _fake_result()
    assert module.validate_future_triangle_result(result) == {"vertex_count": 3, "vertex_marker_count": 3, "triangle_count": 1, "segment_count": 3, "marker_count": 3, "hole_count": 1}
    summary = module.summarize_positive_area_streaming(result, Polygon(((0, 0), (1, 0), (0, 1))))
    assert summary["coverage_certified"] is False
    assert summary["certification_status"] == "INCOMPLETE_C1_DEFERRED"
    assert summary["full_2d_cert_status"] == "FULL_2D_CERT_STOP"
    assert summary["area_sum_consistency"] is True
    assert summary["bounded_streaming"] is True
    assert summary["triangle_area_sum_um2"] == 0.5
    assert "streamed_area_sum" in summary["checks_performed"]
    assert "geometric_coverage" in summary["checks_deferred"]


@pytest.mark.parametrize("chunk_size", (0, 8193))
def test_positive_area_streaming_rejects_unbounded_chunk_size(small_future_count_floor, chunk_size):
    with pytest.raises(module.Refusal):
        module.summarize_positive_area_streaming(_fake_result(), chunk_size=chunk_size)


def test_oriented_boundary_records_pass_two_ccw_square(small_future_count_floor):
    records = module._oriented_boundary_records(_square_result(), chunk_size=2)
    expected = sorted(
        _encoded_boundary_record(first, second, marker)
        for (first, second), marker in zip(((0, 1), (1, 2), (2, 3), (3, 0)), (10, 20, 30, 40))
    )
    assert records.tolist() == expected
    assert records.dtype == np.dtype(np.uint64)
    assert records.shape == (4,)
    assert records.flags.c_contiguous and records.flags.aligned and records.flags.owndata
    assert records.base is None


def test_signed_zero_duplicate_coordinate_is_rejected():
    vertices = np.array(((0.0, 1.0), (-0.0, 1.0)), dtype=np.float64)
    with pytest.raises(module.Refusal):
        module._reject_duplicate_coordinate_records(vertices)


def test_permuted_duplicate_face_is_rejected():
    triangles = np.array(((0, 1, 2), (2, 0, 1)), dtype=np.int32)
    with pytest.raises(module.Refusal):
        module._reject_duplicate_face_keys(triangles)


def test_boundary_steiner_exact_subdivision_is_consumed():
    pslg = {
        "vertices": np.array(((0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0)), dtype=np.float64),
        "segments": np.array(((0, 1), (1, 2), (2, 3), (3, 0)), dtype=np.int32),
        "segment_markers": np.ones((4, 1), dtype=np.int32),
        "holes": np.empty((0, 2), dtype=np.float64),
    }
    result = {
        "vertices": np.array(((0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0), (1.0, 0.0)), dtype=np.float64),
        "segments": np.array(((0, 1), (4, 0), (3, 4), (2, 3), (1, 2)), dtype=np.int32),
        "segment_markers": np.ones((5, 1), dtype=np.int32),
    }
    induced_starts = np.array((1, 0, 4, 3, 2), dtype=np.int64)
    induced_ends = np.array((0, 4, 3, 2, 1), dtype=np.int64)
    next_vertex, _, next_edge, _ = module._boundary_adjacency(induced_starts, induced_ends, 5)
    consumed = module._partition_frozen_boundary(pslg, result, np.ones(5, dtype=np.bool_), next_vertex, next_edge)
    assert consumed == 5


def test_induced_boundary_direction_reversal_is_accepted_and_undirected_mismatch_rejected(small_future_count_floor):
    result = _square_result()
    result["segments"][0] = (1, 0)
    records = module._oriented_boundary_records(result, chunk_size=2)
    rows, starts, ends, markers = module._boundary_rows_from_records(result, records)
    assert rows.shape == (4,)
    assert np.array_equal(starts, np.array((0, 1, 2, 3)))
    assert np.array_equal(ends, np.array((1, 2, 3, 0)))
    assert np.array_equal(markers, result["segment_markers"][:, 0])
    result["segments"][0] = (0, 2)
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result, chunk_size=2)


def test_oriented_boundary_records_rejects_duplicate_triangle(small_future_count_floor):
    result = _fake_result()
    result["triangles"] = np.array(((0, 1, 2), (0, 1, 2)), dtype=np.int32)
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result)


def test_oriented_boundary_records_rejects_same_direction_shared_edge(small_future_count_floor):
    result = _square_result()
    result["triangles"] = np.array(((0, 1, 2), (2, 0, 3)), dtype=np.int32)
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result)


def test_oriented_boundary_records_rejects_incidence_above_two(small_future_count_floor):
    result = _square_result()
    result["triangles"] = np.array(((0, 1, 2), (2, 0, 3), (0, 2, 3)), dtype=np.int32)
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result)


@pytest.mark.parametrize(
    "segments,markers",
    (
        (np.array(((0, 1), (1, 2), (2, 3)), dtype=np.int32), np.array(((10,), (20,), (30,)), dtype=np.int32)),
        (np.array(((0, 1), (1, 2), (2, 3), (0, 2)), dtype=np.int32), np.array(((10,), (20,), (30,), (40,)), dtype=np.int32)),
        (np.array(((0, 1), (1, 2), (2, 3), (0, 1)), dtype=np.int32), np.array(((10,), (20,), (30,), (40,)), dtype=np.int32)),
    ),
)
def test_oriented_boundary_records_rejects_missing_extra_or_duplicate_segments(small_future_count_floor, segments, markers):
    result = _square_result()
    result["segments"] = segments
    result["segment_markers"] = markers
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result)


def test_oriented_boundary_records_rejects_long_vs_split_t_junction(small_future_count_floor):
    result = {
        "vertices": np.array(((0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (0.0, 1.0)), dtype=np.float64),
        "vertex_markers": np.ones((4, 1), dtype=np.int32),
        "triangles": np.array(((0, 2, 3),), dtype=np.int32),
        "segments": np.array(((0, 1), (1, 2), (2, 3)), dtype=np.int32),
        "segment_markers": np.array(((1,), (2,), (3,)), dtype=np.int32),
        "holes": np.array(((0.25, 0.25),), dtype=np.float64),
    }
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result)


def test_oriented_boundary_records_rejects_vertex_encoding_width(small_future_count_floor, monkeypatch):
    monkeypatch.setattr(module, "PLANAR_VERTEX_CAP", 1 << 19)
    monkeypatch.setattr(
        module,
        "validate_future_triangle_result",
        lambda result: {"vertex_count": 1 << 19, "triangle_count": 1, "segment_count": 3},
    )
    result = _fake_result()
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result)


def test_oriented_boundary_records_rejects_marker_encoding_width(small_future_count_floor):
    result = _fake_result()
    result["segment_markers"] = np.array(((1,), (2,), (1 << 18,)), dtype=np.int32)
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(result)


@pytest.mark.parametrize("chunk_size", (0, 8193))
def test_oriented_boundary_records_rejects_unbounded_chunk_size(small_future_count_floor, chunk_size):
    with pytest.raises(module.Refusal):
        module._oriented_boundary_records(_fake_result(), chunk_size=chunk_size)


def test_future_vertex_markers_allow_owned_zero_marker(small_future_count_floor):
    result = _fake_result()
    result["vertex_markers"] = np.array(((1,), (0,), (3,)), dtype=np.int32)
    assert module.validate_future_triangle_result(result)["vertex_marker_count"] == 3


def test_area_match_does_not_claim_geometric_coverage_for_shifted_overlap(small_future_count_floor):
    result = {
        "vertices": np.array(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.1, 0.0), (1.1, 0.0), (0.1, 1.0)), dtype=np.float64),
        "vertex_markers": np.arange(1, 7, dtype=np.int32).reshape(-1, 1).copy(),
        "triangles": np.array(((0, 1, 2), (3, 4, 5)), dtype=np.int32),
        "segments": np.array(((0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)), dtype=np.int32),
        "segment_markers": np.arange(1, 7, dtype=np.int32).reshape(-1, 1).copy(),
        "holes": np.array(((0.25, 0.25),), dtype=np.float64),
    }
    summary = module.summarize_positive_area_streaming(result, Polygon(((0, 0), (1, 0), (1, 1), (0, 1))))
    assert summary["area_sum_consistency"] is True
    assert summary["coverage_certified"] is False
    assert summary["certification_status"] == "INCOMPLETE_C1_DEFERRED"
    assert summary["full_2d_cert_status"] == "FULL_2D_CERT_STOP"
    assert "geometric_coverage" in summary["checks_deferred"]


def test_future_result_canonical_streams_all_six_arrays_and_reads_back(tmp_path, small_future_count_floor):
    result = _fake_result()
    first = module.stream_future_result_canonical(result)
    output = tmp_path / "future.canonical"
    assert module.write_future_result_canonical(output, result) == first
    assert module.readback_canonical(output, first, module.FUTURE_CANONICAL_BYTES_CAP) == first
    with output.open("rb") as stream:
        text = stream.read(512).decode("ascii")
    assert "Stage2A C1 future result" in text
    assert "vm 0 1" in text
    assert "t 0 0 1 2" in text
    assert "h 0.25 0.25" in text


def test_future_result_canonical_cap_cleans_temporary_output(tmp_path, small_future_count_floor):
    result = _fake_result()
    output = tmp_path / "future-too-small.canonical"
    with pytest.raises(module.Refusal):
        module.write_future_result_canonical(output, result, byte_cap=10)
    assert not output.exists()
    assert not (tmp_path / "future-too-small.canonical.tmp").exists()


def test_future_canonical_cap_uses_exact_format_line_bounds():
    assert module.CANONICAL_HEADER_LINE_BYTES == 56
    assert module.CANONICAL_VERTEX_LINE_BYTES == 59
    assert module.CANONICAL_VERTEX_MARKER_LINE_BYTES == 21
    assert module.CANONICAL_TRIANGLE_LINE_BYTES == 30
    assert module.CANONICAL_SEGMENT_LINE_BYTES == 23
    assert module.CANONICAL_SEGMENT_MARKER_LINE_BYTES == 20
    assert module.CANONICAL_HOLE_LINE_BYTES == 52
    expected = 56 + 400_000 * (59 + 21) + 600_000 * 30 + 400_000 * (23 + 20) + 2_048 * 52
    assert module.FUTURE_CANONICAL_BYTES_CAP == expected == 67_306_552


def test_fixed_steiner_count_contract_is_fail_closed():
    n = module.STEINER_POINT_CAP
    all_interior_v = module.EXPECTED_PSLG_VERTICES + n
    all_interior_b = module.EXPECTED_PSLG_SEGMENTS
    all_interior_t = module.T_FLOOR + 2 * n
    assert (all_interior_v, all_interior_b, all_interior_t) == (module.STEINER_VERTEX_MAX, module.EXPECTED_PSLG_SEGMENTS, module.STEINER_TRIANGLE_MAX)
    assert all_interior_v <= module.PLANAR_VERTEX_CAP and all_interior_t <= module.PLANAR_TRIANGLE_CAP and all_interior_b <= all_interior_v
    assert 2 * all_interior_v == module.STEINER_CLOSED_VERTEX_MAX <= module.CLOSED_VERTEX_CAP
    assert 4 * all_interior_v + 4 * module.EXPECTED_HOLES - 4 == module.STEINER_CLOSED_FACE_MAX <= module.CLOSED_FACE_CAP

    all_boundary_v = module.EXPECTED_PSLG_VERTICES + n
    all_boundary_b = module.EXPECTED_PSLG_SEGMENTS + n
    all_boundary_t = module.T_FLOOR + n
    assert (all_boundary_v, all_boundary_b) == (module.STEINER_VERTEX_MAX, module.STEINER_SEGMENT_MAX)
    assert all_boundary_t <= module.PLANAR_TRIANGLE_CAP and all_boundary_b <= all_boundary_v
    assert 2 * all_boundary_v <= module.CLOSED_VERTEX_CAP
    assert 4 * all_boundary_v + 4 * module.EXPECTED_HOLES - 4 <= module.CLOSED_FACE_CAP

    rejected_n = n + 1
    assert module.T_FLOOR + 2 * rejected_n == module.PLANAR_TRIANGLE_CAP + 2
    vertex_limited_n = module.PLANAR_VERTEX_CAP - module.EXPECTED_PSLG_VERTICES
    vertex_limited_t = module.T_FLOOR + 2 * vertex_limited_n
    assert vertex_limited_t == 650_848
    assert vertex_limited_t - module.PLANAR_TRIANGLE_CAP == 50_848
    assert module.STATIC_NATIVE_BOUND == "NOT_PROVABLE_FROM_PINNED_SOURCE_ALONE"


def test_future_result_v_below_source_floor_refuses(small_future_count_floor, monkeypatch):
    monkeypatch.setattr(module, "EXPECTED_PSLG_VERTICES", 4)
    with pytest.raises(module.Refusal):
        module.validate_future_triangle_result(_fake_result())


def test_future_result_t_below_floor_refuses(small_future_count_floor, monkeypatch):
    monkeypatch.setattr(module, "T_FLOOR", 2)
    with pytest.raises(module.Refusal):
        module.validate_future_triangle_result(_fake_result())


def test_future_result_b_below_source_floor_refuses(small_future_count_floor, monkeypatch):
    monkeypatch.setattr(module, "EXPECTED_PSLG_SEGMENTS", 4)
    with pytest.raises(module.Refusal):
        module.validate_future_triangle_result(_fake_result())


def test_future_result_b_above_v_refuses(small_future_count_floor):
    result = _fake_result()
    result["segments"] = np.array(((0, 1), (1, 2), (2, 0), (0, 2)), dtype=np.int32)
    result["segment_markers"] = np.array(((1,), (2,), (3,), (4,)), dtype=np.int32)
    with pytest.raises(module.Refusal):
        module.validate_future_triangle_result(result)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {**value, "extra": np.empty((1,), dtype=np.int32)},
        lambda value: {key: item for key, item in value.items() if key != "segments"},
        lambda value: {key: item for key, item in value.items() if key != "vertex_markers"},
        lambda value: {key: item for key, item in value.items() if key != "holes"},
        lambda value: {**value, "vertices": value["vertices"].astype(np.float32)},
        lambda value: {**value, "vertex_markers": value["vertex_markers"].astype(np.int64)},
        lambda value: {**value, "vertices": np.asfortranarray(value["vertices"])},
        lambda value: {**value, "vertices": value["vertices"][::1].view()},
        lambda value: {**value, "segment_markers": value["segment_markers"].reshape(-1)},
        lambda value: {**value, "triangles": np.array(((0, 1, 3),), dtype=np.int32)},
    ),
)
def test_future_result_contract_fails_closed(mutation, small_future_count_floor):
    value = mutation(_fake_result())
    with pytest.raises(module.Refusal):
        module.validate_future_triangle_result(value)


@pytest.mark.parametrize(
    "mutate_holes",
    (
        lambda value: value.astype(np.float32),
        lambda value: value.reshape(-1),
        lambda value: value.view(),
        lambda value: np.array(((np.nan, 0.25),), dtype=np.float64),
    ),
)
def test_future_holes_contract_fails_closed(mutate_holes, small_future_count_floor):
    result = _fake_result()
    result["holes"] = mutate_holes(result["holes"])
    with pytest.raises(module.Refusal):
        module.validate_future_triangle_result(result)


def test_future_holes_match_frozen_input_and_report_count(small_future_count_floor):
    result = _fake_result()
    expected_holes = np.array(((0.25, 0.25),), dtype=np.float64)
    assert module.validate_future_triangle_result(result, expected_holes=expected_holes)["hole_count"] == 1
    with pytest.raises(module.Refusal):
        module.validate_future_triangle_result(result, expected_holes=np.array(((0.5, 0.25),), dtype=np.float64))


def test_future_hole_line_is_included_and_bounded(small_future_count_floor):
    import io

    result = _fake_result()
    sink = io.BytesIO()
    module.stream_future_result_canonical(result, sink=sink)
    hole_lines = [line for line in sink.getvalue().splitlines(keepends=True) if line.startswith(b"h ")]
    assert hole_lines == [b"h 0.25 0.25\n"]
    assert all(len(line) <= module.CANONICAL_HOLE_LINE_BYTES for line in hole_lines)


def test_controlled_buffers_report_owned_arrays_and_incomplete_model(small_future_count_floor):
    polygon = Polygon(((0, 0), (4, 0), (4, 4), (0, 4)))
    pslg = module.build_pslg_arrays(polygon, thickness=1.0)
    report = module.controlled_buffer_accounting(pslg, _fake_result())
    assert report["status"] == "STATIC_OWNED_NDARRAY_BYTES_PASS"
    assert report["controlled_buffer_status"] == "STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE"
    assert report["excluded_terms"] == module.CONTROLLED_BUFFER_EXCLUDED_TERMS
    assert report["native_feasibility"] == "OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN"
    assert report["future_process_cap_bytes"] == 3_435_970_560
    assert report["future_process_cap_operational_only"] is True
    assert report["future_process_cap_is_proof"] is False
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "STATIC_MEMORY_MODEL_PASS" not in source
    assert "STATIC_CONTROLLED_BUFFER_MODEL_PASS" not in source
    assert report["output_array_bytes_cap"] == 20_032_768
    assert report["combined_array_bytes_cap"] == 24_356_424
    assert report["memory_ledger"]["known_concurrent_floor_bytes"] == report["memory_ledger"]["known_peak_floor_bytes"]
    assert report["memory_ledger"]["phase_floor_bytes"]["boundary"] == sum(
        report["memory_ledger"]["terms"][name]["bound_bytes"]
        for name in module.MEMORY_LEDGER_FLOOR_COMPONENTS
    )


def test_controlled_lifetime_memory_ledger_is_machine_checkable():
    ledger = module.controlled_lifetime_memory_ledger()
    assert tuple(ledger["phases"]) == module.MEMORY_LEDGER_PHASES
    assert set(ledger["terms"]) == set(module.MEMORY_LEDGER_REQUIRED_TERMS)
    assert ledger["known_concurrent_floor_bytes"] == ledger["known_peak_floor_bytes"]
    assert ledger["phase_floor_bytes"]["boundary"] == sum(
        ledger["terms"][name]["bound_bytes"] for name in module.MEMORY_LEDGER_FLOOR_COMPONENTS
    )
    assert module.BOUNDARY_MEMORY_BYTES_CAP == 17_796_608
    assert tuple(ledger["phase_ledger"]["boundary"]) == tuple(
        name for name, row in ledger["terms"].items() if "boundary" in row["phases"]
    )
    assert ledger["phase_ledger"]["native-call"] == ("pslg_input_arrays", "native_call_workspace", "result_arrays")
    assert ledger["phase_ledger"]["receipt"] == ("pslg_input_arrays", "result_arrays", "receipt_payload")
    assert tuple(ledger["phase_floor_components"]["boundary"]) == module.MEMORY_LEDGER_FLOOR_COMPONENTS
    assert sum(ledger["terms"][name]["bound_bytes"] for name in ledger["phase_floor_components"]["boundary"]) == ledger["phase_floor_bytes"]["boundary"]
    assert ledger["phase_floor_bytes"]["boundary"] < ledger["known_concurrent_floor_bytes"]
    assert set(ledger["phase_floor_candidates"]) == set(module.MEMORY_LEDGER_PHASES)
    assert ledger["known_peak_floor_bytes"] == max(ledger["phase_floor_bytes"].values())
    assert ledger["omitted_terms"] == ()
    assert ledger["owned_array_accounting_exclusions"] == module.CONTROLLED_BUFFER_EXCLUDED_TERMS
    assert ledger["terms"]["sealed_read_buffer"]["classification"] == "mutually exclusive"
    assert ledger["terms"]["native_call_workspace"]["classification"] == "job-contained opaque"
    assert ledger["native_feasibility"] == module.OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN
    assert ledger["c1_execution_status"] == "C1_EXECUTION_STILL_STOP"
    assert ledger["full_2d_cert_status"] == "FULL_2D_CERT_STOP"
    assert ledger["triangle_extension_loaded"] is False
    assert ledger["solver_status"] == ledger["powersi_status"] == "STOP"
    assert "not proven" in ledger["release_note"]


def test_certifier_memory_floors_are_implementation_derived():
    floors = module.certifier_memory_floors(374_576, 378_670, 374_576)
    caller = module._pslg_array_bytes(module.EXPECTED_PSLG_VERTICES, module.EXPECTED_HOLES) + module._result_array_bytes(374_576, 378_670, 374_576, module.EXPECTED_HOLES)
    assert floors["validation_edge"] == floors["validation"] == floors["known_peak"] == 60_643_152
    marker_slots = module.EXPECTED_MARKERS + 1
    validation_scratch = max(
        9 * module.EXPECTED_PSLG_VERTICES,
        16 * module.EXPECTED_PSLG_VERTICES,
        16 * module.EXPECTED_PSLG_SEGMENTS,
        8 * marker_slots + 8 * module.EXPECTED_MARKERS + marker_slots,
        2 * module.EXPECTED_PSLG_VERTICES,
        2 * module.EXPECTED_HOLES,
        2 * 374_576,
        378_670,
    )
    assert floors["validation_predicates"] == caller + validation_scratch == 23_421_231
    canonical_scratch_dynamic = max(9 * module.EXPECTED_PSLG_VERTICES, 2 * 374_576, 378_670, 374_576, 2 * module.EXPECTED_HOLES)
    assert floors["canonical"] == caller + canonical_scratch_dynamic == 22_266_110
    assert floors["boundary"] == floors["boundary_cycle_construct"] == 57_220_768
    assert floors["boundary_cycle_visit"] == caller + 29 * 374_576 + 18 * 374_576
    assert floors["boundary_partition"] == caller + 16 * 374_576 + 2 * 374_576
    assert floors["boundary_winding"] == caller + 16 * 374_576 + 17 * 374_576 + 32
    assert floors["quality_area"] == 22_430_672
    assert floors["quality_stream"] == floors["quality"] == 22_441_120
    link_bytes = sum(
        module.MEMORY_LEDGER_TERM_CONTRACT[name]["bound_bytes"]
        for name in (
            "certifier_vertex_link_boundary_degree",
            "certifier_vertex_link_corner_count",
            "certifier_vertex_link_outgoing_arcs",
            "certifier_vertex_link_incoming_arcs",
            "certifier_vertex_link_visitation",
        )
    )
    assert link_bytes == 20 * module.PLANAR_VERTEX_CAP + 72 * module.PLANAR_TRIANGLE_CAP
    static = module.certifier_memory_floors(400_000, 600_000, 400_000)
    assert static["quality"] == module.INPUT_ARRAY_BYTES_CAP + module.OUTPUT_ARRAY_BYTES_CAP + 400_000 + max(
        400_000 + 97 * 8_192,
        144 * 8_192,
        32,
    )
    assert static["quality"] == 25_951_048
    assert static["boundary"] == 63_156_424
    canonical_scratch = module.MEMORY_LEDGER_TERM_CONTRACT["certifier_canonical_predicate_scratch"]["bound_bytes"]
    assert static["canonical"] == module.INPUT_ARRAY_BYTES_CAP + module.OUTPUT_ARRAY_BYTES_CAP + canonical_scratch == 25_735_638
    assert static["known_peak"] == 87_156_424
    ledger = module.controlled_lifetime_memory_ledger()
    overlaps = {tuple(edge) for edge in ledger["overlaps"]}
    assert tuple(sorted(("certifier_boundary_adjacency_sort_records", "certifier_boundary_cycle_visitation"))) not in overlaps
    assert tuple(sorted(("certifier_boundary_adjacency_return_maps", "certifier_boundary_cycle_visitation"))) in overlaps
    assert tuple(sorted(("boundary_records", "certifier_boundary_mask"))) not in overlaps
    assert ledger["known_peak_floor_bytes"] == module.FULL_CERTIFIER_ARRAY_KNOWN_PEAK_BYTES
    # The former 64,712,288-B datum included boundary records/rows/maps that
    # are no longer co-live after their earliest-safe deletions.
    assert floors["known_peak"] == caller + 374_576 + 104 * 378_670
    assert floors["known_peak"] < 64_712_288


def test_controlled_lifetime_memory_ledger_rejects_missing_term_or_overlap():
    missing_term = module.controlled_lifetime_memory_ledger()
    missing_term["terms"] = dict(missing_term["terms"])
    del missing_term["terms"]["boundary_records"]
    with pytest.raises(module.Refusal):
        module.validate_controlled_lifetime_memory_ledger(missing_term)

    missing_overlap = module.controlled_lifetime_memory_ledger()
    missing_overlap["overlaps"] = tuple(
        edge for edge in missing_overlap["overlaps"] if edge != ("pslg_input_arrays", "result_arrays")
    )
    with pytest.raises(module.Refusal):
        module.validate_controlled_lifetime_memory_ledger(missing_overlap)

    missing_boundary_lifetime = module.controlled_lifetime_memory_ledger()
    missing_boundary_lifetime["phase_ledger"] = dict(missing_boundary_lifetime["phase_ledger"])
    missing_boundary_lifetime["phase_ledger"]["boundary"] = tuple(
        name for name in missing_boundary_lifetime["phase_ledger"]["boundary"] if name != "boundary_records"
    )
    with pytest.raises(module.Refusal):
        module.validate_controlled_lifetime_memory_ledger(missing_boundary_lifetime)

    missing_row_overlap = module.controlled_lifetime_memory_ledger()
    missing_row_overlap["terms"] = dict(missing_row_overlap["terms"])
    missing_row_overlap["terms"]["pslg_input_arrays"] = dict(missing_row_overlap["terms"]["pslg_input_arrays"])
    missing_row_overlap["terms"]["pslg_input_arrays"]["overlap_with"] = tuple(
        name for name in missing_row_overlap["terms"]["pslg_input_arrays"]["overlap_with"] if name != "pslg_ring_storage"
    )
    with pytest.raises(module.Refusal):
        module.validate_controlled_lifetime_memory_ledger(missing_row_overlap)


def test_controlled_lifetime_memory_ledger_exact_contract_is_immutable():
    paired_projection = module.controlled_lifetime_memory_ledger()
    paired_projection["terms"] = dict(paired_projection["terms"])
    paired_projection["terms"]["result_arrays"] = dict(paired_projection["terms"]["result_arrays"])
    paired_projection["terms"]["result_arrays"]["phases"] = tuple(
        phase for phase in paired_projection["terms"]["result_arrays"]["phases"] if phase not in {"native-call", "receipt"}
    )
    paired_projection["phase_ledger"] = dict(paired_projection["phase_ledger"])
    for phase in ("native-call", "receipt"):
        paired_projection["phase_ledger"][phase] = tuple(
            name for name in paired_projection["phase_ledger"][phase] if name != "result_arrays"
        )
    with pytest.raises(module.Refusal):
        module.validate_controlled_lifetime_memory_ledger(paired_projection)

    opaque_mutation = module.controlled_lifetime_memory_ledger()
    opaque_mutation["terms"] = dict(opaque_mutation["terms"])
    opaque_mutation["terms"]["native_call_workspace"] = dict(opaque_mutation["terms"]["native_call_workspace"])
    opaque_mutation["terms"]["native_call_workspace"].update({"classification": "formula-bounded", "bound_bytes": 0})
    with pytest.raises(module.Refusal):
        module.validate_controlled_lifetime_memory_ledger(opaque_mutation)

    for name, expected in module.MEMORY_LEDGER_TERM_CONTRACT.items():
        for field, value in (
            ("phases", tuple(expected["phases"]) + ("receipt",)),
            ("classification", "formula-bounded" if expected["classification"] != "formula-bounded" else "job-contained opaque"),
            ("bound_bytes", 0 if expected["bound_bytes"] is None or expected["bound_bytes"] != 0 else 1),
            ("bound_bytes", True),
        ):
            candidate = module.controlled_lifetime_memory_ledger()
            candidate["terms"] = dict(candidate["terms"])
            candidate["terms"][name] = dict(candidate["terms"][name])
            candidate["terms"][name][field] = value
            with pytest.raises(module.Refusal):
                module.validate_controlled_lifetime_memory_ledger(candidate)


def test_static_full_2d_certifier_accepts_tiny_polygon_with_hole(tiny_polygon_with_hole, tmp_path):
    pslg, result = tiny_polygon_with_hole
    output = tmp_path / "future.canonical"
    report = module.certify_full_2d_result(pslg, result, canonical_output_path=output, chunk_size=2)
    assert report["program"] == "SPD Decap PI Evaluator"
    assert report["version"] == "0.23.1"
    assert report["preparation_status"] == module.PREPARATION_STATUS
    assert report["full_2d_cert_status"] == "FULL_2D_CERT_STOP"
    assert report["authorization_status"] == "C1_NOT_AUTHORIZED"
    assert report["counts"]["triangle_count"] == 8
    assert report["topology"]["boundary_components"] == 2
    assert report["topology"]["euler_characteristic"] == 0
    proof = report["topology"]["proof_receipt"]
    assert proof["induced_boundary_direction_match"] is True
    assert proof["paired_internal_edge_directions_opposite"] is True
    assert proof["all_faces_strictly_positive_signed_area"] is True
    assert proof["triangle_dual_connected"] is True
    assert proof["vertex_links_connected_manifold"] is True
    assert proof["boundary_partition_exact"] is True
    assert proof["winding_one_zero"] == report["topology"]["winding_degree"]
    assert report["topology"]["coverage_certified"] is True
    assert report["quality"]["quality_pass"] is True
    assert report["area"]["area_sum_consistency"] is True
    assert report["area"]["frozen_boundary_area_um2"] == 12.0
    assert report["canonical"]["serializer_determinism_only"] is True
    assert report["canonical"]["triangle_replay"] is False
    assert report["canonical"]["write_readback"]["performed"] is True
    canonical_identity = (
        report["canonical"]["future_result"]["bytes"],
        report["canonical"]["future_result"]["sha256"],
    )
    assert module.readback_canonical(output, canonical_identity) == canonical_identity
    assert report["ledger_validated"] is True
    assert report["memory_ledger"]["full_2d_cert_status"] == module.FULL_2D_CERT_STATUS
    assert output.exists()


def test_full_certifier_boundary_lifetimes_are_released_before_partition_and_topology(tiny_polygon_with_hole, monkeypatch):
    pslg, result = tiny_polygon_with_hole
    refs = {}
    observed = {}
    original_rows = module._boundary_rows_from_records
    original_cycle = module._boundary_cycle_evidence
    original_partition = module._partition_frozen_boundary
    original_edge_topology = module._edge_topology

    def capture_rows(value, records):
        rows, starts, ends, markers = original_rows(value, records)
        refs["records"] = weakref.ref(records)
        refs["starts"] = weakref.ref(starts)
        refs["ends"] = weakref.ref(ends)
        refs["markers"] = weakref.ref(markers)
        return rows, starts, ends, markers

    def capture_cycle(vertices, starts, ends):
        evidence = original_cycle(vertices, starts, ends)
        if vertices is result["vertices"]:
            refs["next_vertex"] = weakref.ref(evidence[0])
            refs["previous_vertex"] = weakref.ref(evidence[1])
            refs["next_edge"] = weakref.ref(evidence[2])
            refs["out_count"] = weakref.ref(evidence[3])
        return evidence

    def capture_partition(value, future, boundary_mask, next_vertex, next_edge):
        observed["before_partition"] = {
            name: refs[name]() is not None for name in ("records", "markers", "starts", "ends")
        }
        return original_partition(value, future, boundary_mask, next_vertex, next_edge)

    def capture_edge_topology(*args):
        observed["before_edge_topology"] = {
            name: refs[name]() is not None for name in ("next_vertex", "previous_vertex", "next_edge", "out_count")
        }
        return original_edge_topology(*args)

    monkeypatch.setattr(module, "_boundary_rows_from_records", capture_rows)
    monkeypatch.setattr(module, "_boundary_cycle_evidence", capture_cycle)
    monkeypatch.setattr(module, "_partition_frozen_boundary", capture_partition)
    monkeypatch.setattr(module, "_edge_topology", capture_edge_topology)
    report = module.certify_full_2d_result(pslg, result, chunk_size=2)
    assert report["topology"]["coverage_certified"] is True
    assert observed["before_partition"] == {
        "records": False,
        "markers": False,
        "starts": False,
        "ends": False,
    }
    assert observed["before_edge_topology"] == {
        "next_vertex": False,
        "previous_vertex": False,
        "next_edge": False,
        "out_count": False,
    }


@pytest.mark.parametrize(
    "mutation",
    (
        lambda pslg, result: result["vertices"].__setitem__((7, 0), result["vertices"][0, 0]),
        lambda pslg, result: result["triangles"].__setitem__(1, result["triangles"][0]),
        lambda pslg, result: result["holes"].__setitem__(0, (1.5, 1.5)),
        lambda pslg, result: result["segment_markers"].__setitem__((0, 0), 99),
    ),
)
def test_static_full_2d_certifier_refuses_duplicate_hole_or_marker_mutations(tiny_polygon_with_hole, mutation):
    pslg, result = tiny_polygon_with_hole
    mutation(pslg, result)
    with pytest.raises(module.Refusal):
        module.certify_full_2d_result(pslg, result, chunk_size=2)


def test_static_full_2d_certifier_refuses_count_model_cap(tiny_polygon_with_hole):
    pslg, result = tiny_polygon_with_hole
    result["vertices"] = np.vstack((result["vertices"], np.array(((10.0, 10.0),), dtype=np.float64)))
    result["vertex_markers"] = np.vstack((result["vertex_markers"], np.array(((0,),), dtype=np.int32)))
    with pytest.raises(module.Refusal):
        module.certify_full_2d_result(pslg, result, chunk_size=2)


def test_static_full_2d_certifier_refuses_shifted_overlap_equal_area(tiny_polygon_with_hole):
    pslg, result = tiny_polygon_with_hole
    result["triangles"][[0, 1]] = result["triangles"][[1, 0]]
    result["triangles"][1] = result["triangles"][0]
    with pytest.raises(module.Refusal):
        module.certify_full_2d_result(pslg, result, chunk_size=2)


def test_static_full_2d_certifier_refuses_topology_and_euler_failures(tiny_polygon_with_hole, monkeypatch):
    pslg, result = tiny_polygon_with_hole
    monkeypatch.setattr(module, "_edge_topology", lambda *_args: (17, 8))
    with pytest.raises(module.Refusal):
        module.certify_full_2d_result(pslg, result, chunk_size=2)


def test_static_full_2d_quality_boundaries_are_strict(small_future_count_floor):
    angle = np.deg2rad(7.4999)
    result = _fake_result()
    result["vertices"] = np.array(((0.0, 0.0), (1.0, 0.0), (np.cos(angle), np.sin(angle))), dtype=np.float64)
    with pytest.raises(module.Refusal):
        module._stream_quality(result, 2)
    result["vertices"] = np.array(((0.0, 0.0), (1.0, 0.0), (0.5, 0.12)), dtype=np.float64)
    with pytest.raises(module.Refusal):
        module._stream_quality(result, 2)


def test_static_full_2d_certifier_requires_accepted_ledger_identity(tiny_polygon_with_hole, monkeypatch):
    pslg, result = tiny_polygon_with_hole
    original = module.controlled_lifetime_memory_ledger

    def rejected_ledger():
        ledger = original()
        ledger["c1_execution_status"] = "C1_AUTHORIZED"
        return ledger

    monkeypatch.setattr(module, "controlled_lifetime_memory_ledger", rejected_ledger)
    with pytest.raises(module.Refusal):
        module.certify_full_2d_result(pslg, result, chunk_size=2)
