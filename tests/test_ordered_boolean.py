from __future__ import annotations

import math
import random

import pytest

from spd_decap_pi._core.geometry.ordered_boolean import (
    MAX_BOOLEAN_CHUNK_SIZE,
    SINGLE_GEOS_CALL_CANCELLATION_LIMITATION,
    ordered_spd_geometry,
)


def _legacy_ordered_spd_geometry(record: dict[str, object]):
    """Frozen pre-integration algorithm used only as an equivalence oracle."""

    from shapely.geometry import GeometryCollection, Point, Polygon
    from shapely.ops import unary_union

    collections = {
        "positive_polygon": record.get("positive_polygons_um", ()),
        "negative_polygon": record.get("negative_polygons_um", ()),
        "positive_circle": record.get("positive_circles_um", ()),
        "negative_circle": record.get("negative_circles_um", ()),
    }
    runs: list[tuple[bool, list[object]]] = []
    for raw_step in record["primitive_order"]:  # type: ignore[union-attr]
        kind, offset = raw_step  # type: ignore[misc]
        raw = collections[str(kind)][int(offset)]  # type: ignore[index]
        if str(kind).endswith("polygon"):
            primitive = Polygon(raw)
        else:
            x_um, y_um, radius_um = map(float, raw)
            assert math.isfinite(radius_um) and radius_um > 0.0
            primitive = Point(x_um, y_um).buffer(radius_um, quad_segs=64)
        positive = str(kind).startswith("positive_")
        if runs and runs[-1][0] == positive:
            runs[-1][1].append(primitive)
        else:
            runs.append((positive, [primitive]))
    shape = GeometryCollection()
    for positive, primitives in runs:
        batch = unary_union(primitives)
        shape = shape.union(batch) if positive else shape.difference(batch)
    return shape


def _rectangle(
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[tuple[float, float], ...]:
    return (
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
    )


def _synthetic_record() -> dict[str, object]:
    return {
        "positive_polygons_um": (
            _rectangle(0.0, 0.0, 100.0, 100.0),
            _rectangle(80.0, 0.0, 100.0, 100.0),
        ),
        "negative_polygons_um": (
            _rectangle(20.0, 20.0, 20.0, 20.0),
        ),
        "positive_circles_um": ((250.0, 0.0, 10.0),),
        "negative_circles_um": ((130.0, 50.0, 8.0),),
        "primitive_order": (
            ("positive_polygon", 0),
            ("positive_polygon", 1),
            ("negative_polygon", 0),
            ("negative_circle", 0),
            ("positive_circle", 0),
        ),
    }


def _random_record(seed: int) -> dict[str, object]:
    generator = random.Random(seed)
    positives: list[tuple[tuple[float, float], ...]] = []
    negatives: list[tuple[tuple[float, float], ...]] = []
    order: list[tuple[str, int]] = []

    # Integer-grid geometry is still irregular and overlapping, while avoiding
    # tolerance-based assertions that could hide a topology/order regression.
    for _ in range(80):
        x = generator.randint(0, 30) * 10.0
        y = generator.randint(0, 30) * 10.0
        width = generator.randint(1, 6) * 10.0
        height = generator.randint(1, 6) * 10.0
        positives.append(_rectangle(x, y, width, height))
        order.append(("positive_polygon", len(positives) - 1))
    for _ in range(25):
        x = generator.randint(0, 30) * 10.0
        y = generator.randint(0, 30) * 10.0
        width = generator.randint(1, 3) * 5.0
        height = generator.randint(1, 3) * 5.0
        negatives.append(_rectangle(x, y, width, height))
        order.append(("negative_polygon", len(negatives) - 1))
    for _ in range(20):
        x = generator.randint(0, 30) * 10.0
        y = generator.randint(0, 30) * 10.0
        width = generator.randint(1, 4) * 5.0
        height = generator.randint(1, 4) * 5.0
        positives.append(_rectangle(x, y, width, height))
        order.append(("positive_polygon", len(positives) - 1))
    return {
        "positive_polygons_um": tuple(positives),
        "negative_polygons_um": tuple(negatives),
        "positive_circles_um": (),
        "negative_circles_um": (),
        "primitive_order": tuple(order),
    }


@pytest.mark.parametrize(
    "record",
    [_synthetic_record(), *(_random_record(seed) for seed in range(5))],
)
def test_chunked_geometry_exactly_matches_existing_ordered_geometry(
    record: dict[str, object],
) -> None:
    expected = _legacy_ordered_spd_geometry(record)
    actual = ordered_spd_geometry(record, chunk_size=7)

    assert expected is not None
    assert actual is not None
    assert actual.area == expected.area
    assert bytes(actual.normalize().wkb) == bytes(expected.normalize().wkb)
    assert actual.symmetric_difference(expected).area == 0.0


def test_every_hierarchical_unary_union_respects_chunk_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shapely.ops

    positive = tuple(
        _rectangle(float(index * 3), 0.0, 2.0, 2.0)
        for index in range(321)
    )
    record = {
        "positive_polygons_um": positive,
        "negative_polygons_um": (),
        "positive_circles_um": (),
        "negative_circles_um": (),
        "primitive_order": tuple(
            ("positive_polygon", index) for index in range(len(positive))
        ),
    }
    original = shapely.ops.unary_union
    batch_sizes: list[int] = []

    def counted(values):
        materialized = tuple(values)
        batch_sizes.append(len(materialized))
        return original(materialized)

    monkeypatch.setattr(shapely.ops, "unary_union", counted)
    actual = ordered_spd_geometry(record, chunk_size=7)

    assert actual is not None
    assert actual.area == 321 * 4.0
    assert len(batch_sizes) > 10
    assert min(batch_sizes) >= 2
    assert max(batch_sizes) <= 7


def test_large_primitive_sequence_reports_repeated_progress() -> None:
    positive = tuple(
        _rectangle(float(index * 2), 0.0, 1.0, 1.0)
        for index in range(1_000)
    )
    record = {
        "positive_polygons_um": positive,
        "negative_polygons_um": (),
        "positive_circles_um": (),
        "negative_circles_um": (),
        "primitive_order": tuple(
            ("positive_polygon", index) for index in range(len(positive))
        ),
    }
    values: list[int] = []

    actual = ordered_spd_geometry(
        record,
        chunk_size=16,
        progress=lambda value, _message: values.append(value),
    )

    assert actual is not None
    assert values[0] == 0
    assert values[-1] == 100
    assert values == sorted(set(values))
    assert len(values) >= 50


def test_large_primitive_sequence_cancels_between_geos_calls() -> None:
    positive = tuple(
        _rectangle(float(index * 2), 0.0, 1.0, 1.0)
        for index in range(2_000)
    )
    record = {
        "positive_polygons_um": positive,
        "negative_polygons_um": (),
        "positive_circles_um": (),
        "negative_circles_um": (),
        "primitive_order": tuple(
            ("positive_polygon", index) for index in range(len(positive))
        ),
    }
    values: list[int] = []
    cancelled = False

    def progress(value: int, _message: str) -> None:
        nonlocal cancelled
        values.append(value)
        if value >= 30:
            cancelled = True

    with pytest.raises(RuntimeError, match="cancelled"):
        ordered_spd_geometry(
            record,
            chunk_size=8,
            progress=progress,
            is_cancelled=lambda: cancelled,
        )

    assert len(values) >= 10
    assert 30 <= values[-1] < 100


def test_immediate_cancellation_precedes_geometry_construction() -> None:
    with pytest.raises(RuntimeError, match="cancelled"):
        ordered_spd_geometry(
            _synthetic_record(),
            is_cancelled=lambda: True,
        )


@pytest.mark.parametrize(
    "chunk_size",
    (True, 1, 0, -1, 2.0, MAX_BOOLEAN_CHUNK_SIZE + 1),
)
def test_chunk_size_is_strictly_bounded(chunk_size: object) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        ordered_spd_geometry(
            _synthetic_record(),
            chunk_size=chunk_size,  # type: ignore[arg-type]
        )


def test_invalid_primitive_order_remains_fail_closed() -> None:
    record = _synthetic_record()
    record["primitive_order"] = (("positive_polygon", 999),)

    assert ordered_spd_geometry(record) is None


def test_single_geos_call_cancellation_limit_is_explicit() -> None:
    assert "cannot interrupt one GEOS operation" in (
        SINGLE_GEOS_CALL_CANCELLATION_LIMITATION
    )
    assert "operand count" in SINGLE_GEOS_CALL_CANCELLATION_LIMITATION
    assert MAX_BOOLEAN_CHUNK_SIZE <= 256
