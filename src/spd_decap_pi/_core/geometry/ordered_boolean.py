"""Cancellation-aware ordered PowerSI artwork booleans.

PowerSI plane primitives are boolean instructions, not unordered artwork.  A
positive run may be unioned associatively and consecutive negative operations
``A \\ B \\ C`` may be represented as ``A \\ (B union C)``, but operations may
never cross a polarity boundary.  This module preserves that contract while
limiting every ``unary_union`` input batch to a validated chunk size.

Cancellation is cooperative between GEOS calls.  It cannot preempt one GEOS
call already executing, so a single polygon with an extreme vertex count, or a
boolean involving one already-enormous accumulated geometry, can still have a
long non-interruptible interval.  Chunking bounds the number of operands per
hierarchical union; it does not impose a vertex or overlay-complexity bound on
an individual operand.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
from typing import Any


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


DEFAULT_BOOLEAN_CHUNK_SIZE = 64
MAX_BOOLEAN_CHUNK_SIZE = 256
MIN_BOOLEAN_CHUNK_SIZE = 2
SINGLE_GEOS_CALL_CANCELLATION_LIMITATION = (
    "Cancellation is checked between GEOS calls but cannot interrupt one GEOS "
    "operation already executing; chunking bounds operand count, not geometry "
    "vertex count or overlay complexity."
)


class _GeometryRejected(Exception):
    """Internal sentinel used to preserve the legacy fail-closed ``None`` API."""


class _Reporter:
    def __init__(
        self,
        progress: ProgressCallback | None,
        is_cancelled: CancelCallback | None,
    ) -> None:
        self._progress = progress
        self._is_cancelled = is_cancelled
        self._last = -1

    def check(self) -> None:
        if self._is_cancelled is not None and self._is_cancelled():
            raise RuntimeError("ordered SPD geometry construction cancelled")

    def emit(self, value: int, message: str) -> None:
        self.check()
        bounded = max(0, min(100, int(value)))
        if bounded < self._last:
            raise RuntimeError("ordered SPD geometry progress regressed")
        if bounded != self._last and self._progress is not None:
            self._progress(bounded, message)
        self._last = bounded


def _validate_chunk_size(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("chunk_size must be an integer")
    if value < MIN_BOOLEAN_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be at least {MIN_BOOLEAN_CHUNK_SIZE}"
        )
    if value > MAX_BOOLEAN_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must not exceed {MAX_BOOLEAN_CHUNK_SIZE}"
        )
    return value


def _physical_geometry(value: Any) -> bool:
    try:
        area = float(value.area)
        return (
            not bool(value.is_empty)
            and bool(value.is_valid)
            and math.isfinite(area)
            and area > 0.0
        )
    except (AttributeError, TypeError, ValueError, ArithmeticError):
        return False


def _checked_union(
    geometries: Sequence[Any],
    *,
    unary_union: Callable[[Sequence[Any]], Any],
    reporter: _Reporter,
) -> Any:
    if not geometries:
        raise _GeometryRejected
    if len(geometries) == 1:
        result = geometries[0]
    else:
        reporter.check()
        result = unary_union(geometries)
        reporter.check()
    if not _physical_geometry(result):
        raise _GeometryRejected
    return result


def _hierarchical_union(
    geometries: Sequence[Any],
    *,
    chunk_size: int,
    unary_union: Callable[[Sequence[Any]], Any],
    reporter: _Reporter,
) -> Any:
    """Union values without presenting more than ``chunk_size`` to GEOS."""

    current = list(geometries)
    if not current:
        raise _GeometryRejected
    while len(current) > 1:
        next_level: list[Any] = []
        for start in range(0, len(current), chunk_size):
            reporter.check()
            next_level.append(
                _checked_union(
                    current[start : start + chunk_size],
                    unary_union=unary_union,
                    reporter=reporter,
                )
            )
        current = next_level
    return _checked_union(
        current,
        unary_union=unary_union,
        reporter=reporter,
    )


class _RunUnionAccumulator:
    """Online base-``chunk_size`` hierarchy for one polarity run."""

    def __init__(
        self,
        *,
        chunk_size: int,
        unary_union: Callable[[Sequence[Any]], Any],
        reporter: _Reporter,
    ) -> None:
        self._chunk_size = chunk_size
        self._unary_union = unary_union
        self._reporter = reporter
        self._levels: list[list[Any]] = []
        self._count = 0

    def add(self, geometry: Any) -> None:
        self._count += 1
        self._add_at_level(geometry, 0)

    def _add_at_level(self, geometry: Any, level: int) -> None:
        self._reporter.check()
        while len(self._levels) <= level:
            self._levels.append([])
        values = self._levels[level]
        values.append(geometry)
        if len(values) < self._chunk_size:
            return
        if len(values) != self._chunk_size:
            raise RuntimeError("ordered boolean hierarchy exceeded its chunk bound")
        merged = _checked_union(
            tuple(values),
            unary_union=self._unary_union,
            reporter=self._reporter,
        )
        values.clear()
        self._add_at_level(merged, level + 1)

    def finish(self) -> Any:
        if self._count < 1:
            raise _GeometryRejected
        if self._count == 1:
            # Keep the historical observable run batching contract: each
            # polarity run, including a singleton, is handed to unary_union.
            residual = [geometry for level in self._levels for geometry in level]
            self._reporter.check()
            result = self._unary_union(residual)
            self._reporter.check()
            if not _physical_geometry(result):
                raise _GeometryRejected
            return result
        # Every non-empty level contains fewer than chunk_size operands.  The
        # final bounded hierarchy combines those residual owners without
        # replaying or duplicating a primitive.
        residual = [
            geometry
            for level in self._levels
            for geometry in level
        ]
        return _hierarchical_union(
            residual,
            chunk_size=self._chunk_size,
            unary_union=self._unary_union,
            reporter=self._reporter,
        )


def _primitive_geometry(
    *,
    kind: str,
    raw: Any,
    Point: Any,
    Polygon: Any,
    reporter: _Reporter,
) -> Any:
    reporter.check()
    if kind.endswith("polygon"):
        primitive = Polygon(raw)
    else:
        x_um, y_um, radius_um = map(float, raw)
        if not math.isfinite(radius_um) or radius_um <= 0.0:
            raise _GeometryRejected
        # This matches the versioned legacy PowerSI primitive construction.
        primitive = Point(x_um, y_um).buffer(radius_um, quad_segs=64)
    reporter.check()
    if not _physical_geometry(primitive):
        raise _GeometryRejected
    return primitive


def ordered_spd_geometry(
    record: Mapping[str, Any],
    *,
    chunk_size: int = DEFAULT_BOOLEAN_CHUNK_SIZE,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> Any | None:
    """Return ordered PowerSI boolean artwork with bounded union batches.

    Invalid or nonphysical source data returns ``None`` to match the existing
    fail-closed geometry API.  Cancellation is distinct from invalid geometry
    and always raises ``RuntimeError``.  Progress is monotonic from 0 to 100.

    The chunk bound applies to every ``unary_union`` operand sequence.  See
    :data:`SINGLE_GEOS_CALL_CANCELLATION_LIMITATION` for the unavoidable limit
    of cooperative cancellation around native GEOS calls.
    """

    chunk_size = _validate_chunk_size(chunk_size)
    reporter = _Reporter(progress, is_cancelled)
    reporter.emit(0, "Validating ordered PowerSI primitive references")
    try:
        from shapely.errors import GEOSException
        from shapely.geometry import GeometryCollection, Point, Polygon
        from shapely.ops import unary_union
    except ImportError:
        return None

    positive_polygons = record.get("positive_polygons_um", ())
    negative_polygons = record.get("negative_polygons_um", ())
    positive_circles = record.get("positive_circles_um", ())
    negative_circles = record.get("negative_circles_um", ())
    collections = {
        "positive_polygon": positive_polygons,
        "negative_polygon": negative_polygons,
        "positive_circle": positive_circles,
        "negative_circle": negative_circles,
    }
    order = record.get("primitive_order", ())
    if not isinstance(order, Sequence) or not order:
        return None

    try:
        # Validate all references before starting GEOS work.  This avoids
        # returning geometry for a valid prefix followed by a malformed step.
        previous_positive: bool | None = None
        run_count = 0
        total = len(order)
        for index, step in enumerate(order):
            reporter.check()
            if not isinstance(step, Sequence) or len(step) != 2:
                return None
            kind, offset = str(step[0]), int(step[1])
            source = collections.get(kind)
            if source is None or offset < 0 or offset >= len(source):
                return None
            positive = kind.startswith("positive_")
            if previous_positive is None or positive != previous_positive:
                run_count += 1
                previous_positive = positive
            reporter.emit(
                1 + int(4 * (index + 1) / total),
                "Validating ordered PowerSI primitive references",
            )
        if run_count < 1:
            return None

        shape = GeometryCollection()
        current_positive: bool | None = None
        accumulator: _RunUnionAccumulator | None = None

        def apply_current_run() -> None:
            nonlocal shape, accumulator
            if accumulator is None or current_positive is None:
                return
            reporter.check()
            batch = accumulator.finish()
            reporter.check()
            shape = (
                shape.union(batch)
                if current_positive
                else shape.difference(batch)
            )
            reporter.check()
            accumulator = None

        for index, step in enumerate(order):
            reporter.check()
            kind, offset = str(step[0]), int(step[1])
            positive = kind.startswith("positive_")
            if current_positive is None or positive != current_positive:
                apply_current_run()
                current_positive = positive
                accumulator = _RunUnionAccumulator(
                    chunk_size=chunk_size,
                    unary_union=unary_union,
                    reporter=reporter,
                )
            source = collections[kind]
            primitive = _primitive_geometry(
                kind=kind,
                raw=source[offset],
                Point=Point,
                Polygon=Polygon,
                reporter=reporter,
            )
            if accumulator is None:  # defensive for static and runtime safety
                raise RuntimeError("ordered boolean run accumulator is missing")
            accumulator.add(primitive)
            reporter.emit(
                5 + int(90 * (index + 1) / total),
                "Constructing and reducing ordered PowerSI primitives",
            )
        apply_current_run()
    except RuntimeError:
        # Cancellation and internal invariant failures must never be converted
        # into the legacy invalid-geometry ``None`` result.
        raise
    except (
        _GeometryRejected,
        TypeError,
        ValueError,
        IndexError,
        ArithmeticError,
        GEOSException,
    ):
        return None

    reporter.check()
    if not _physical_geometry(shape):
        return None
    reporter.emit(100, "Ordered PowerSI boolean geometry complete")
    return shape


__all__ = [
    "CancelCallback",
    "DEFAULT_BOOLEAN_CHUNK_SIZE",
    "MAX_BOOLEAN_CHUNK_SIZE",
    "MIN_BOOLEAN_CHUNK_SIZE",
    "ProgressCallback",
    "SINGLE_GEOS_CALL_CANCELLATION_LIMITATION",
    "ordered_spd_geometry",
]
