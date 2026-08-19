"""Exact, fail-closed PWR-plane eligibility for decap reassignment.

The modal solver may approximate a non-rectangular rail with a bounding box,
but assignment eligibility deliberately uses the normalized PowerSI boolean
primitives at the physical PWR pad coordinate.  A point on a primitive boundary
is rejected because floating-point tolerance must never make a marginal pad
silently assignable.
"""

from __future__ import annotations

from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from math import ceil, floor, hypot, isfinite, sqrt
from typing import Literal

from spd_decap_pi._core.domain import (
    MixedReferenceCertificate,
    PlanePairSuggestion,
    StackupLayer,
)
from spd_decap_pi._core.io.spd import SpdPlaneGeometry
from spd_decap_pi._core.plane_pairs import suggest_effective_plane_pairs


Containment = Literal["inside", "outside", "boundary"]


@dataclass(frozen=True, slots=True)
class EligiblePlane:
    """One assignable PWR net directly under a decap PWR pad."""

    net: str
    pwr_layer: str
    gnd_layer: str
    separation_um: float


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    """Eligibility result with explicit fail-closed exclusion evidence."""

    eligible: tuple[EligiblePlane, ...]
    boundary_exclusions: tuple[str, ...] = ()

    @property
    def nets(self) -> tuple[str, ...]:
        return tuple(item.net for item in self.eligible)


@dataclass(frozen=True, slots=True)
class _IndexedPrimitive:
    kind: str
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float]
    bounds: tuple[float, float, float, float]
    polygon_y_index: _PolygonYIndex | None = None


_SPATIAL_TARGET_ITEMS_PER_CELL = 8
_SPATIAL_MAX_BINS_PER_AXIS = 128
_SPATIAL_MAX_CELLS_PER_ITEM = 64
_POLYGON_INDEX_MIN_EDGES = 64
_POLYGON_TARGET_EDGES_PER_BIN = 32
_POLYGON_MAX_Y_BINS = 4096


def _conservative_axis_padding(tolerance_um: float) -> float:
    """Return bbox padding that covers the legacy segment tolerance region.

    ``_point_on_segment`` permits ``tolerance_um`` both along and normal to a
    segment.  The projection of that parallelogram onto either Cartesian axis
    can therefore extend by ``sqrt(2) * tolerance_um`` for a diagonal edge.
    Spatial filters must use that larger value so they never hide an exact
    boundary result from the fail-closed geometry test.
    """

    return sqrt(2.0) * tolerance_um


def _iter_merged_ordered_indices(
    left: Sequence[int], right: Sequence[int]
) -> Iterator[int]:
    """Yield disjoint source indices in order without materializing a merged tuple."""

    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_value = left[left_index]
        right_value = right[right_index]
        if left_value < right_value:
            yield left_value
            left_index += 1
        else:
            yield right_value
            right_index += 1
    yield from left[left_index:]
    yield from right[right_index:]


@dataclass(frozen=True, slots=True)
class _SpatialGrid:
    """Conservative uniform-grid index for point-versus-bbox queries.

    Every indexed bbox is expanded by a conservative Cartesian projection of
    the exact eligibility tolerance.  Returned candidates are therefore a
    superset of the exact geometry test, which callers still perform.
    """

    domain: tuple[float, float, float, float]
    x_bins: int
    y_bins: int
    cell_width: float
    cell_height: float
    cells: dict[tuple[int, int], tuple[int, ...]]
    broad_indices: tuple[int, ...]

    @classmethod
    def build(
        cls,
        bounds: Sequence[tuple[float, float, float, float]],
        *,
        tolerance_um: float,
        domain: tuple[float, float, float, float] | None = None,
    ) -> _SpatialGrid | None:
        if not bounds:
            return None
        axis_padding = _conservative_axis_padding(tolerance_um)
        expanded = tuple(
            (
                item[0] - axis_padding,
                item[1] + axis_padding,
                item[2] - axis_padding,
                item[3] + axis_padding,
            )
            for item in bounds
        )
        if domain is None:
            domain = (
                min(item[0] for item in expanded),
                max(item[1] for item in expanded),
                min(item[2] for item in expanded),
                max(item[3] for item in expanded),
            )
        else:
            domain = (
                domain[0] - axis_padding,
                domain[1] + axis_padding,
                domain[2] - axis_padding,
                domain[3] + axis_padding,
            )

        x_extent = max(0.0, domain[1] - domain[0])
        y_extent = max(0.0, domain[3] - domain[2])
        target_cells = max(
            1, ceil(len(bounds) / _SPATIAL_TARGET_ITEMS_PER_CELL)
        )
        bins_per_axis = min(
            _SPATIAL_MAX_BINS_PER_AXIS,
            max(1, ceil(sqrt(target_cells))),
        )
        x_bins = bins_per_axis if x_extent > 0.0 else 1
        y_bins = bins_per_axis if y_extent > 0.0 else 1
        cell_width = x_extent / x_bins if x_extent > 0.0 else 1.0
        cell_height = y_extent / y_bins if y_extent > 0.0 else 1.0

        def axis_index(value: float, lower: float, step: float, bins: int) -> int:
            if bins == 1:
                return 0
            return min(bins - 1, max(0, floor((value - lower) / step)))

        mutable_cells: dict[tuple[int, int], list[int]] = {}
        broad: list[int] = []
        for index, item in enumerate(expanded):
            x_min = max(domain[0], item[0])
            x_max = min(domain[1], item[1])
            y_min = max(domain[2], item[2])
            y_max = min(domain[3], item[3])
            if x_min > x_max or y_min > y_max:
                continue
            x_start = axis_index(x_min, domain[0], cell_width, x_bins)
            x_stop = axis_index(x_max, domain[0], cell_width, x_bins)
            y_start = axis_index(y_min, domain[2], cell_height, y_bins)
            y_stop = axis_index(y_max, domain[2], cell_height, y_bins)
            touched_cells = (x_stop - x_start + 1) * (y_stop - y_start + 1)
            if touched_cells > _SPATIAL_MAX_CELLS_PER_ITEM:
                broad.append(index)
                continue
            for x_cell in range(x_start, x_stop + 1):
                for y_cell in range(y_start, y_stop + 1):
                    mutable_cells.setdefault((x_cell, y_cell), []).append(index)

        broad_indices = tuple(broad)
        # Store only cell-local references.  Replicating every broad item into
        # every populated cell makes memory grow as broad-items × cells on
        # production artwork.  Query merges the two already-sorted sequences
        # lazily so the original boolean primitive order is still replayed.
        cells = {key: tuple(indices) for key, indices in mutable_cells.items()}
        return cls(
            domain=domain,
            x_bins=x_bins,
            y_bins=y_bins,
            cell_width=cell_width,
            cell_height=cell_height,
            cells=cells,
            broad_indices=broad_indices,
        )

    @property
    def stored_reference_count(self) -> int:
        return len(self.broad_indices) + sum(len(items) for items in self.cells.values())

    def candidates(self, x_um: float, y_um: float) -> Iterator[int]:
        if not (
            self.domain[0] <= x_um <= self.domain[1]
            and self.domain[2] <= y_um <= self.domain[3]
        ):
            return
        x_cell = (
            0
            if self.x_bins == 1
            else min(
                self.x_bins - 1,
                max(0, floor((x_um - self.domain[0]) / self.cell_width)),
            )
        )
        y_cell = (
            0
            if self.y_bins == 1
            else min(
                self.y_bins - 1,
                max(0, floor((y_um - self.domain[2]) / self.cell_height)),
            )
        )
        local_indices = self.cells.get((x_cell, y_cell), ())
        yield from _iter_merged_ordered_indices(self.broad_indices, local_indices)


@dataclass(frozen=True, slots=True)
class _PolygonYIndex:
    """Compact scanline index for repeated point queries on a large polygon."""

    y_min: float
    y_max: float
    bins: int
    bin_height: float
    cells: dict[int, array]
    broad_indices: array

    @classmethod
    def build(
        cls,
        polygon: Sequence[tuple[float, float]],
        *,
        tolerance_um: float,
        y_min: float,
        y_max: float,
    ) -> _PolygonYIndex | None:
        edge_count = len(polygon)
        if edge_count < _POLYGON_INDEX_MIN_EDGES:
            return None
        axis_padding = _conservative_axis_padding(tolerance_um)
        domain_min = y_min - axis_padding
        domain_max = y_max + axis_padding
        extent = domain_max - domain_min
        if extent <= 0.0:
            return None
        bins = min(
            _POLYGON_MAX_Y_BINS,
            max(1, ceil(edge_count / _POLYGON_TARGET_EDGES_PER_BIN)),
        )
        bin_height = extent / bins

        def bin_index(value: float) -> int:
            return min(
                bins - 1,
                max(0, floor((value - domain_min) / bin_height)),
            )

        mutable_cells: dict[int, list[int]] = {}
        broad: list[int] = []
        previous_y = polygon[-1][1]
        for edge_index, (_current_x, current_y) in enumerate(polygon):
            edge_min = min(previous_y, current_y) - axis_padding
            edge_max = max(previous_y, current_y) + axis_padding
            start = bin_index(edge_min)
            stop = bin_index(edge_max)
            if stop - start + 1 > _SPATIAL_MAX_CELLS_PER_ITEM:
                broad.append(edge_index)
            else:
                for bin_number in range(start, stop + 1):
                    mutable_cells.setdefault(bin_number, []).append(edge_index)
            previous_y = current_y
        return cls(
            y_min=domain_min,
            y_max=domain_max,
            bins=bins,
            bin_height=bin_height,
            cells={
                key: array("I", indices) for key, indices in mutable_cells.items()
            },
            broad_indices=array("I", broad),
        )

    @property
    def stored_reference_count(self) -> int:
        return len(self.broad_indices) + sum(len(items) for items in self.cells.values())

    def candidates(self, y_um: float) -> Iterator[int]:
        if not self.y_min <= y_um <= self.y_max:
            return
        bin_number = min(
            self.bins - 1,
            max(0, floor((y_um - self.y_min) / self.bin_height)),
        )
        local_indices = self.cells.get(bin_number, ())
        yield from _iter_merged_ordered_indices(
            self.broad_indices,
            local_indices,
        )


@dataclass(frozen=True, slots=True)
class _IndexedPlane:
    geometry: SpdPlaneGeometry
    primitives: tuple[_IndexedPrimitive, ...]
    positive_bounds: tuple[float, float, float, float]
    primitive_grid: _SpatialGrid
    pair: PlanePairSuggestion


@dataclass(frozen=True, slots=True)
class IndexedPlaneGeometry:
    """Reusable exact ordered-boolean index for one arbitrary plane asset.

    Unlike :class:`PlaneEligibilityIndex`, this deliberately has no electrical
    pair semantics.  It is used when a retained GND asset must be checked as
    exact copper for an explicitly selected PWR/GND pair.
    """

    geometry: SpdPlaneGeometry
    primitives: tuple[_IndexedPrimitive, ...]
    positive_bounds: tuple[float, float, float, float]
    primitive_grid: _SpatialGrid

    @classmethod
    def build(
        cls, geometry: SpdPlaneGeometry, *, tolerance_um: float = 1.0e-6
    ) -> "IndexedPlaneGeometry | None":
        primitives = _ordered_primitives(geometry, tolerance_um=tolerance_um)
        positive = [
            item.bounds
            for item in primitives
            if item.kind.startswith("positive_")
        ]
        if not positive:
            return None
        positive_bounds = (
            min(item[0] for item in positive),
            max(item[1] for item in positive),
            min(item[2] for item in positive),
            max(item[3] for item in positive),
        )
        primitive_grid = _SpatialGrid.build(
            tuple(item.bounds for item in primitives),
            tolerance_um=tolerance_um,
            domain=positive_bounds,
        )
        if primitive_grid is None:
            return None
        return cls(geometry, primitives, positive_bounds, primitive_grid)

    def contains(self, x_um: float, y_um: float, *, tolerance_um: float = 1.0e-6) -> Containment:
        if not isfinite(x_um) or not isfinite(y_um):
            raise ValueError("plane query coordinates must be finite")
        if not _bounds_contains(self.positive_bounds, x_um, y_um, tolerance_um):
            return "outside"
        filled = False
        for primitive_index in self.primitive_grid.candidates(x_um, y_um):
            item = self.primitives[primitive_index]
            if not _bounds_contains(item.bounds, x_um, y_um, tolerance_um):
                continue
            if item.kind.endswith("polygon"):
                if item.polygon_y_index is None:
                    containment = point_in_polygon(
                        x_um, y_um, item.primitive, tolerance_um=tolerance_um
                    )
                else:
                    containment = _point_in_indexed_polygon(
                        x_um, y_um, item.primitive, item.polygon_y_index,
                        tolerance_um=tolerance_um,
                    )
            else:
                containment = _point_in_circle(
                    x_um, y_um, item.primitive, tolerance_um
                )
            if containment == "boundary":
                return "boundary"
            if containment == "inside":
                filled = item.kind.startswith("positive_")
        return "inside" if filled else "outside"


def _point_on_segment(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    tolerance_um: float,
) -> bool:
    length = hypot(x2 - x1, y2 - y1)
    if length <= tolerance_um:
        return hypot(x - x1, y - y1) <= tolerance_um
    cross = abs((x - x1) * (y2 - y1) - (y - y1) * (x2 - x1))
    if cross > tolerance_um * length:
        return False
    dot = (x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)
    return -tolerance_um * length <= dot <= length * length + tolerance_um * length


def point_in_polygon(
    x_um: float,
    y_um: float,
    polygon_um: Sequence[tuple[float, float]],
    *,
    tolerance_um: float = 1.0e-6,
) -> Containment:
    """Classify a point using even/odd fill with explicit edge detection."""

    if len(polygon_um) < 3:
        return "outside"
    inside = False
    previous_x, previous_y = polygon_um[-1]
    for current_x, current_y in polygon_um:
        if _point_on_segment(
            x_um,
            y_um,
            previous_x,
            previous_y,
            current_x,
            current_y,
            tolerance_um,
        ):
            return "boundary"
        crosses = (current_y > y_um) != (previous_y > y_um)
        if crosses:
            intersection_x = (previous_x - current_x) * (
                y_um - current_y
            ) / (previous_y - current_y) + current_x
            if intersection_x > x_um:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return "inside" if inside else "outside"


def _point_in_indexed_polygon(
    x_um: float,
    y_um: float,
    polygon_um: Sequence[tuple[float, float]],
    y_index: _PolygonYIndex,
    *,
    tolerance_um: float,
) -> Containment:
    """Classify a point using only edges whose Y interval can affect it."""

    inside = False
    for edge_index in y_index.candidates(y_um):
        current_x, current_y = polygon_um[edge_index]
        previous_x, previous_y = polygon_um[edge_index - 1]
        if _point_on_segment(
            x_um,
            y_um,
            previous_x,
            previous_y,
            current_x,
            current_y,
            tolerance_um,
        ):
            return "boundary"
        crosses = (current_y > y_um) != (previous_y > y_um)
        if crosses:
            intersection_x = (previous_x - current_x) * (
                y_um - current_y
            ) / (previous_y - current_y) + current_x
            if intersection_x > x_um:
                inside = not inside
    return "inside" if inside else "outside"


def _point_in_circle(
    x_um: float,
    y_um: float,
    circle_um: tuple[float, float, float],
    tolerance_um: float,
) -> Containment:
    center_x, center_y, radius = circle_um
    delta = hypot(x_um - center_x, y_um - center_y) - radius
    if abs(delta) <= tolerance_um:
        return "boundary"
    return "inside" if delta < 0 else "outside"


def point_in_plane_geometry(
    x_um: float,
    y_um: float,
    geometry: SpdPlaneGeometry,
    *,
    tolerance_um: float = 1.0e-6,
) -> Containment:
    """Replay PowerSI add/subtract primitives in their source order."""

    if not isfinite(x_um) or not isfinite(y_um):
        raise ValueError("plane query coordinates must be finite")
    if not isfinite(tolerance_um) or tolerance_um < 0:
        raise ValueError("tolerance_um must be finite and non-negative")

    polygons = {
        "positive_polygon": geometry.positive_polygons_um,
        "negative_polygon": geometry.negative_polygons_um,
    }
    circles = {
        "positive_circle": geometry.positive_circles_um,
        "negative_circle": geometry.negative_circles_um,
    }
    order = geometry.primitive_order
    if not order:
        order = tuple(
            [("positive_polygon", index) for index in range(len(geometry.positive_polygons_um))]
            + [("positive_circle", index) for index in range(len(geometry.positive_circles_um))]
            + [("negative_polygon", index) for index in range(len(geometry.negative_polygons_um))]
            + [("negative_circle", index) for index in range(len(geometry.negative_circles_um))]
        )

    filled = False
    for kind, index in order:
        if kind in polygons:
            primitives = polygons[kind]
            if index < 0 or index >= len(primitives):
                raise ValueError(f"invalid {kind} primitive index {index}")
            containment = point_in_polygon(
                x_um, y_um, primitives[index], tolerance_um=tolerance_um
            )
        elif kind in circles:
            primitives = circles[kind]
            if index < 0 or index >= len(primitives):
                raise ValueError(f"invalid {kind} primitive index {index}")
            containment = _point_in_circle(
                x_um, y_um, primitives[index], tolerance_um
            )
        else:
            raise ValueError(f"unsupported plane primitive kind {kind!r}")
        if containment == "boundary":
            return "boundary"
        if containment == "inside":
            filled = kind.startswith("positive_")
    return "inside" if filled else "outside"


def _bounds_contains(
    bounds: tuple[float, float, float, float],
    x_um: float,
    y_um: float,
    tolerance_um: float,
) -> bool:
    x_min, x_max, y_min, y_max = bounds
    axis_padding = _conservative_axis_padding(tolerance_um)
    return (
        x_min - axis_padding <= x_um <= x_max + axis_padding
        and y_min - axis_padding <= y_um <= y_max + axis_padding
    )


def _ordered_primitives(
    geometry: SpdPlaneGeometry,
    *,
    tolerance_um: float,
) -> tuple[_IndexedPrimitive, ...]:
    polygons = {
        "positive_polygon": geometry.positive_polygons_um,
        "negative_polygon": geometry.negative_polygons_um,
    }
    circles = {
        "positive_circle": geometry.positive_circles_um,
        "negative_circle": geometry.negative_circles_um,
    }
    order = geometry.primitive_order
    if not order:
        order = tuple(
            [("positive_polygon", index) for index in range(len(geometry.positive_polygons_um))]
            + [("positive_circle", index) for index in range(len(geometry.positive_circles_um))]
            + [("negative_polygon", index) for index in range(len(geometry.negative_polygons_um))]
            + [("negative_circle", index) for index in range(len(geometry.negative_circles_um))]
        )
    result: list[_IndexedPrimitive] = []
    for kind, index in order:
        if kind in polygons:
            values = polygons[kind]
            if index < 0 or index >= len(values):
                raise ValueError(f"invalid {kind} primitive index {index}")
            primitive = values[index]
            first_x, first_y = primitive[0]
            x_min = x_max = first_x
            y_min = y_max = first_y
            for x_um, y_um in primitive:
                x_min = min(x_min, x_um)
                x_max = max(x_max, x_um)
                y_min = min(y_min, y_um)
                y_max = max(y_max, y_um)
            bounds = (x_min, x_max, y_min, y_max)
            polygon_y_index = _PolygonYIndex.build(
                primitive,
                tolerance_um=tolerance_um,
                y_min=y_min,
                y_max=y_max,
            )
        elif kind in circles:
            values = circles[kind]
            if index < 0 or index >= len(values):
                raise ValueError(f"invalid {kind} primitive index {index}")
            primitive = values[index]
            center_x, center_y, radius = primitive
            bounds = (
                center_x - radius,
                center_x + radius,
                center_y - radius,
                center_y + radius,
            )
            polygon_y_index = None
        else:
            raise ValueError(f"unsupported plane primitive kind {kind!r}")
        result.append(
            _IndexedPrimitive(
                kind,
                primitive,
                bounds,
                polygon_y_index,
            )
        )
    return tuple(result)


class PlaneEligibilityIndex:
    """Reusable bbox index for thousands of decap PWR-pad queries."""

    def __init__(
        self,
        plane_geometries: Iterable[SpdPlaneGeometry],
        stackup_layers: Iterable[StackupLayer],
        *,
        gnd_aliases: Iterable[str] = ("DGND", "GND"),
        mixed_reference_certificates: Iterable[MixedReferenceCertificate] = (),
        selected_pairs: Iterable[PlanePairSuggestion] = (),
        tolerance_um: float = 1.0e-6,
    ) -> None:
        if not isfinite(tolerance_um) or tolerance_um < 0:
            raise ValueError("tolerance_um must be finite and non-negative")
        self.tolerance_um = tolerance_um
        stack = tuple(stackup_layers)
        aliases = tuple(gnd_aliases)
        certificates = tuple(mixed_reference_certificates)
        selected_by_net = {}
        for pair in selected_pairs:
            selected_by_net.setdefault(pair.rail_net.casefold(), []).append(pair)
        pair_cache: dict[str, tuple[PlanePairSuggestion, ...]] = {}
        indexed: list[_IndexedPlane] = []
        for geometry in plane_geometries:
            net_key = geometry.net.casefold()
            pairs = pair_cache.setdefault(
                net_key,
                tuple(selected_by_net.get(net_key, ()))
                or tuple(
                    suggest_effective_plane_pairs(
                        stack,
                        rail_net=geometry.net,
                        gnd_aliases=aliases,
                        mixed_reference_certificates=certificates,
                    )
                ),
            )
            pair = next(
                (
                    item
                    for item in pairs
                    if item.pwr_layer.casefold() == geometry.layer.casefold()
                ),
                None,
            )
            if pair is None:
                continue
            primitives = _ordered_primitives(
                geometry,
                tolerance_um=tolerance_um,
            )
            positive = [
                item.bounds
                for item in primitives
                if item.kind.startswith("positive_")
            ]
            if not positive:
                continue
            positive_bounds = (
                min(item[0] for item in positive),
                max(item[1] for item in positive),
                min(item[2] for item in positive),
                max(item[3] for item in positive),
            )
            primitive_grid = _SpatialGrid.build(
                tuple(item.bounds for item in primitives),
                tolerance_um=tolerance_um,
                domain=positive_bounds,
            )
            assert primitive_grid is not None
            indexed.append(
                _IndexedPlane(
                    geometry=geometry,
                    primitives=primitives,
                    positive_bounds=positive_bounds,
                    primitive_grid=primitive_grid,
                    pair=pair,
                )
            )
        self._planes = tuple(indexed)
        self._plane_grid = _SpatialGrid.build(
            tuple(item.positive_bounds for item in self._planes),
            tolerance_um=tolerance_um,
        )
        pair_net_names: dict[tuple[str, str, str], set[str]] = {}
        for item in self._planes:
            key = (
                item.geometry.net.casefold(),
                item.pair.pwr_layer.casefold(),
                item.pair.gnd_layer.casefold(),
            )
            pair_net_names.setdefault(key, set()).add(item.geometry.net)
        self._pair_net_names = {
            key: tuple(sorted(names, key=str.casefold))
            for key, names in pair_net_names.items()
        }

    @property
    def plane_count(self) -> int:
        return len(self._planes)

    @property
    def primitive_count(self) -> int:
        return sum(len(item.primitives) for item in self._planes)

    def query(self, x_um: float, y_um: float) -> EligibilityResult:
        if not isfinite(x_um) or not isfinite(y_um):
            raise ValueError("plane query coordinates must be finite")
        eligible: dict[tuple[str, str, str], EligiblePlane] = {}
        boundary: set[tuple[str, str, str]] = set()
        plane_indices = (
            () if self._plane_grid is None else self._plane_grid.candidates(x_um, y_um)
        )
        for plane_index in plane_indices:
            indexed = self._planes[plane_index]
            geometry = indexed.geometry
            net_key = geometry.net.casefold()
            pair_key = (
                net_key,
                indexed.pair.pwr_layer.casefold(),
                indexed.pair.gnd_layer.casefold(),
            )
            if pair_key in boundary:
                continue
            if not _bounds_contains(
                indexed.positive_bounds, x_um, y_um, self.tolerance_um
            ):
                continue
            filled = False
            touches_boundary = False
            for primitive_index in indexed.primitive_grid.candidates(x_um, y_um):
                item = indexed.primitives[primitive_index]
                if not _bounds_contains(item.bounds, x_um, y_um, self.tolerance_um):
                    continue
                if item.kind.endswith("polygon"):
                    if item.polygon_y_index is None:
                        containment = point_in_polygon(
                            x_um,
                            y_um,
                            item.primitive,  # type: ignore[arg-type]
                            tolerance_um=self.tolerance_um,
                        )
                    else:
                        containment = _point_in_indexed_polygon(
                            x_um,
                            y_um,
                            item.primitive,  # type: ignore[arg-type]
                            item.polygon_y_index,
                            tolerance_um=self.tolerance_um,
                        )
                else:
                    containment = _point_in_circle(
                        x_um,
                        y_um,
                        item.primitive,  # type: ignore[arg-type]
                        self.tolerance_um,
                    )
                if containment == "boundary":
                    touches_boundary = True
                    break
                if containment == "inside":
                    filled = item.kind.startswith("positive_")
            if touches_boundary:
                boundary.add(pair_key)
                eligible.pop(pair_key, None)
                continue
            if filled and pair_key not in eligible:
                eligible[pair_key] = EligiblePlane(
                    net=geometry.net,
                    pwr_layer=indexed.pair.pwr_layer,
                    gnd_layer=indexed.pair.gnd_layer,
                    separation_um=indexed.pair.separation_um,
                )
        ordered = sorted(
            eligible.values(),
            key=lambda item: (
                item.separation_um,
                item.net.casefold(),
                item.pwr_layer.casefold(),
            ),
        )
        boundary_names = {
            net for key in boundary for net in self._pair_net_names.get(key, ())
        }
        return EligibilityResult(
            tuple(ordered),
            tuple(sorted(boundary_names, key=str.casefold)),
        )


def eligible_power_planes(
    x_um: float,
    y_um: float,
    plane_geometries: Iterable[SpdPlaneGeometry],
    stackup_layers: Iterable[StackupLayer],
    *,
    gnd_aliases: Iterable[str] = ("DGND", "GND"),
    tolerance_um: float = 1.0e-6,
) -> EligibilityResult:
    """Return exact-under-pad nets that also have a solver-supported plane pair."""

    return PlaneEligibilityIndex(
        plane_geometries,
        stackup_layers,
        gnd_aliases=gnd_aliases,
        tolerance_um=tolerance_um,
    ).query(x_um, y_um)


__all__ = [
    "Containment",
    "EligibilityResult",
    "EligiblePlane",
    "IndexedPlaneGeometry",
    "PlaneEligibilityIndex",
    "eligible_power_planes",
    "point_in_plane_geometry",
    "point_in_polygon",
]
