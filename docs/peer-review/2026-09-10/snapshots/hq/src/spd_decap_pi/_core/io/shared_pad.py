"""Exact TOP-pad connectivity evidence extracted from an SPD source.

The extractor intentionally uses only source-backed geometry: layer-specific
padstack copper, terminal coordinates, and Via endpoint nodes.  REFDES suffixes
and nearest-neighbour distances are never electrical evidence.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from hashlib import sha256
from math import atan2, cos, floor, hypot, isfinite, pi, radians, sin
from statistics import median
from sys import float_info
from typing import Callable, Literal, Mapping, Sequence


PadShapeKind = Literal["CIRCLE", "RECTANGLE", "UNSUPPORTED"]
CopperPrimitiveKind = Literal[
    "positive_polygon",
    "negative_polygon",
    "positive_circle",
    "negative_circle",
]
DecapConnectionKind = Literal[
    "DIRECT",
    "SHARED_ANCHOR",
    "SHARED_DUMMY",
    "FLOATING_DUMMY",
    "UNRESOLVED",
    "OUT_OF_SCOPE",
]
SharedPadClusterState = Literal["ANCHORED", "FLOATING", "UNRESOLVED"]

_GEOMETRY_EPS_UM = 1.0e-9
_GENERIC_PATH_WORK_BUDGET = 200_000


@dataclass(frozen=True, slots=True)
class SpdPadShape:
    """One layer-specific regular pad shape from a ``.PadStackDef``."""

    layer: str
    kind: PadShapeKind
    width_um: float | None
    height_um: float | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SpdTopCopperGeometry:
    """Source-order TOP copper primitives for one NET.

    The tuples reference the already parsed SPD plane geometry.  They are not a
    second copy or a second source scan.  Shared-pad extraction uses positive
    PWR primitives to define components and uses GND primitives only inside an
    already established PWR component, so a board-wide ground shape cannot
    merge unrelated decap groups.
    """

    layer: str
    net: str
    positive_polygons_um: tuple[Sequence[tuple[float, float]], ...]
    negative_polygons_um: tuple[Sequence[tuple[float, float]], ...]
    positive_circles_um: tuple[tuple[float, float, float], ...] = ()
    negative_circles_um: tuple[tuple[float, float, float], ...] = ()
    primitive_order: tuple[tuple[CopperPrimitiveKind, int], ...] = ()


@dataclass(frozen=True, slots=True)
class SpdViaLanding:
    """A source Via whose endpoint copper overlaps a decap terminal pad."""

    via_id: str
    net: str
    endpoint_node_id: str
    x_um: float
    y_um: float
    padstack: str
    rotation_degrees: float = 0.0


@dataclass(frozen=True, slots=True)
class SpdDecapConnection:
    """Complete, fail-closed connection classification for one decap."""

    refdes: str
    kind: DecapConnectionKind
    cluster_id: str | None = None
    power_vias: tuple[SpdViaLanding, ...] = ()
    ground_vias: tuple[SpdViaLanding, ...] = ()
    reason: str | None = None

    @property
    def via_backed(self) -> bool:
        return bool(self.power_vias and self.ground_vias)


@dataclass(frozen=True, slots=True)
class SpdSharedPadCluster:
    """A deterministic component of directly overlapping TOP terminal pads."""

    cluster_id: str
    state: SharedPadClusterState
    member_refdes: tuple[str, ...]
    anchor_refdes: tuple[str, ...]
    dummy_refdes: tuple[str, ...]
    power_net: str
    ground_net: str
    layer: str
    power_edges: tuple[tuple[str, str], ...]
    ground_edges: tuple[tuple[str, str], ...]
    isolation_gap_refdes: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DecapPadEvidence:
    """Minimal source data needed by the geometry extractor."""

    refdes: str
    top_side: bool
    layer: str | None
    power_net: str
    ground_net: str
    power_x_um: float
    power_y_um: float
    power_padstack: str | None
    power_rotation_degrees: float
    ground_x_um: float
    ground_y_um: float
    ground_padstack: str | None
    ground_rotation_degrees: float
    power_rotation_valid: bool = True
    ground_rotation_valid: bool = True


@dataclass(frozen=True, slots=True)
class ViaTopEndpoint:
    """A Via endpoint resolved by the single Node-section scan."""

    via_id: str
    net: str
    endpoint_node_id: str
    x_um: float
    y_um: float
    padstack: str
    rotation_degrees: float


@dataclass(frozen=True, slots=True)
class SharedPadExtraction:
    connections: tuple[SpdDecapConnection, ...]
    clusters: tuple[SpdSharedPadCluster, ...]
    warnings: tuple[str, ...]
    source_copper_power_edges: int = 0
    source_copper_ground_edges: int = 0
    source_copper_member_count: int = 0


@dataclass(frozen=True, slots=True)
class _PlacedPad:
    owner_index: int
    terminal: Literal["PWR", "GND", "VIA"]
    net_key: str
    x_um: float
    y_um: float
    kind: Literal["CIRCLE", "RECTANGLE"]
    width_um: float
    height_um: float
    rotation_degrees: float

    @property
    def radius_um(self) -> float:
        return self.width_um / 2.0

    @property
    def axes(self) -> tuple[tuple[float, float], tuple[float, float]]:
        angle = radians(self.rotation_degrees)
        axis_x = (cos(angle), sin(angle))
        return axis_x, (-axis_x[1], axis_x[0])

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        if self.kind == "CIRCLE":
            radius = self.radius_um
            return (
                self.x_um - radius,
                self.x_um + radius,
                self.y_um - radius,
                self.y_um + radius,
            )
        axis_x, axis_y = self.axes
        half_x = self.width_um / 2.0
        half_y = self.height_um / 2.0
        extent_x = abs(axis_x[0]) * half_x + abs(axis_y[0]) * half_y
        extent_y = abs(axis_x[1]) * half_x + abs(axis_y[1]) * half_y
        return (
            self.x_um - extent_x,
            self.x_um + extent_x,
            self.y_um - extent_y,
            self.y_um + extent_y,
        )


class _ShapeGrid:
    """Small uniform index; broad shapes are retained once, not per cell."""

    def __init__(self, shapes: Sequence[_PlacedPad]) -> None:
        self._shapes = shapes
        sizes = [max(item.width_um, item.height_um) for item in shapes]
        self._cell_um = max(1.0, median(sizes) if sizes else 1.0)
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._broad: list[int] = []
        for index, shape in enumerate(shapes):
            ix0, ix1, iy0, iy1 = self._cell_bounds(shape.bbox)
            if (ix1 - ix0 + 1) * (iy1 - iy0 + 1) > 256:
                self._broad.append(index)
                continue
            for key in self._keys_from_bounds(ix0, ix1, iy0, iy1):
                self._cells.setdefault(key, []).append(index)

    def _cell_bounds(
        self, bbox: tuple[float, float, float, float]
    ) -> tuple[int, int, int, int]:
        x0, x1, y0, y1 = bbox
        ix0, ix1 = floor(x0 / self._cell_um), floor(x1 / self._cell_um)
        iy0, iy1 = floor(y0 / self._cell_um), floor(y1 / self._cell_um)
        return ix0, ix1, iy0, iy1

    @staticmethod
    def _keys_from_bounds(
        ix0: int, ix1: int, iy0: int, iy1: int
    ) -> tuple[tuple[int, int], ...]:
        return tuple(
            (ix, iy)
            for ix in range(ix0, ix1 + 1)
            for iy in range(iy0, iy1 + 1)
        )

    def query(self, bbox: tuple[float, float, float, float]) -> tuple[int, ...]:
        candidates = set(self._broad)
        for key in self._keys_from_bounds(*self._cell_bounds(bbox)):
            candidates.update(self._cells.get(key, ()))
        return tuple(sorted(candidates))


def _shape_for_layer(
    padstack_shapes: Mapping[str, tuple[SpdPadShape, ...]],
    padstack: str | None,
    layer: str,
) -> tuple[SpdPadShape | None, str | None]:
    if not padstack:
        return None, "terminal has no source padstack"
    candidates = tuple(
        item
        for item in padstack_shapes.get(padstack.casefold(), ())
        if item.layer.casefold() == layer.casefold()
    )
    if len(candidates) != 1:
        return None, (
            f"padstack {padstack!r} has no unique regular shape on {layer!r}"
        )
    shape = candidates[0]
    if (
        shape.kind == "UNSUPPORTED"
        or shape.width_um is None
        or shape.height_um is None
        or shape.width_um <= 0.0
        or shape.height_um <= 0.0
    ):
        return None, shape.reason or f"padstack {padstack!r} shape is unsupported"
    return shape, None


def _placed(
    owner_index: int,
    terminal: Literal["PWR", "GND", "VIA"],
    net: str,
    x_um: float,
    y_um: float,
    rotation_degrees: float,
    shape: SpdPadShape,
) -> _PlacedPad:
    assert shape.kind in {"CIRCLE", "RECTANGLE"}
    assert shape.width_um is not None and shape.height_um is not None
    return _PlacedPad(
        owner_index=owner_index,
        terminal=terminal,
        net_key=net.casefold(),
        x_um=x_um,
        y_um=y_um,
        kind=shape.kind,
        width_um=shape.width_um,
        height_um=shape.height_um,
        rotation_degrees=rotation_degrees % 360.0,
    )


def _circle_rectangle_relation(
    circle: _PlacedPad, rectangle: _PlacedPad
) -> Literal["positive", "boundary", "none"]:
    axis_x, axis_y = rectangle.axes
    delta_x = circle.x_um - rectangle.x_um
    delta_y = circle.y_um - rectangle.y_um
    local_x = delta_x * axis_x[0] + delta_y * axis_x[1]
    local_y = delta_x * axis_y[0] + delta_y * axis_y[1]
    outside_x = max(abs(local_x) - rectangle.width_um / 2.0, 0.0)
    outside_y = max(abs(local_y) - rectangle.height_um / 2.0, 0.0)
    distance = hypot(outside_x, outside_y)
    margin = circle.radius_um - distance
    if margin > _GEOMETRY_EPS_UM:
        return "positive"
    if margin >= -_GEOMETRY_EPS_UM:
        return "boundary"
    return "none"


def _relation(
    first: _PlacedPad, second: _PlacedPad
) -> Literal["positive", "boundary", "none"]:
    if first.kind == "CIRCLE" and second.kind == "CIRCLE":
        margin = first.radius_um + second.radius_um - hypot(
            first.x_um - second.x_um, first.y_um - second.y_um
        )
        if margin > _GEOMETRY_EPS_UM:
            return "positive"
        if margin >= -_GEOMETRY_EPS_UM:
            return "boundary"
        return "none"
    if first.kind == "CIRCLE":
        return _circle_rectangle_relation(first, second)
    if second.kind == "CIRCLE":
        return _circle_rectangle_relation(second, first)

    delta = (second.x_um - first.x_um, second.y_um - first.y_um)
    minimum_margin = float("inf")
    for axis in (*first.axes, *second.axes):
        center_distance = abs(delta[0] * axis[0] + delta[1] * axis[1])
        first_x, first_y = first.axes
        second_x, second_y = second.axes
        first_radius = (
            first.width_um / 2.0 * abs(first_x[0] * axis[0] + first_x[1] * axis[1])
            + first.height_um / 2.0 * abs(first_y[0] * axis[0] + first_y[1] * axis[1])
        )
        second_radius = (
            second.width_um / 2.0 * abs(second_x[0] * axis[0] + second_x[1] * axis[1])
            + second.height_um / 2.0 * abs(second_y[0] * axis[0] + second_y[1] * axis[1])
        )
        margin = first_radius + second_radius - center_distance
        if margin < -_GEOMETRY_EPS_UM:
            return "none"
        minimum_margin = min(minimum_margin, margin)
    return "positive" if minimum_margin > _GEOMETRY_EPS_UM else "boundary"


def _overlap_edges(
    shapes: Sequence[_PlacedPad],
    grid: _ShapeGrid | None = None,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]], set[int]]:
    grid = grid or _ShapeGrid(shapes)
    positive: set[tuple[int, int]] = set()
    boundary: set[tuple[int, int]] = set()
    net_conflict: set[int] = set()
    for index, shape in enumerate(shapes):
        for other_index in grid.query(shape.bbox):
            if other_index <= index:
                continue
            other = shapes[other_index]
            if shape.owner_index == other.owner_index:
                continue
            relation = _relation(shape, other)
            if relation == "none":
                continue
            if shape.net_key != other.net_key:
                net_conflict.update((shape.owner_index, other.owner_index))
                continue
            pair = tuple(sorted((shape.owner_index, other.owner_index)))
            if relation == "positive":
                positive.add(pair)
            else:
                boundary.add(pair)
    return positive, boundary, net_conflict


def _point_on_segment(
    x_um: float,
    y_um: float,
    first: tuple[float, float],
    second: tuple[float, float],
) -> bool:
    x1, y1 = first
    x2, y2 = second
    length = hypot(x2 - x1, y2 - y1)
    if length <= _GEOMETRY_EPS_UM:
        return hypot(x_um - x1, y_um - y1) <= _GEOMETRY_EPS_UM
    cross = abs((x_um - x1) * (y2 - y1) - (y_um - y1) * (x2 - x1))
    if cross > _GEOMETRY_EPS_UM * length:
        return False
    dot = (x_um - x1) * (x2 - x1) + (y_um - y1) * (y2 - y1)
    return (
        -_GEOMETRY_EPS_UM * length
        <= dot
        <= length * length + _GEOMETRY_EPS_UM * length
    )


def _point_in_polygon(
    x_um: float,
    y_um: float,
    polygon: Sequence[tuple[float, float]],
) -> Literal["positive", "boundary", "none"]:
    if len(polygon) < 3:
        return "none"
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _point_on_segment(x_um, y_um, previous, current):
            return "boundary"
        current_x, current_y = current
        previous_x, previous_y = previous
        if (current_y > y_um) != (previous_y > y_um):
            intersection_x = (previous_x - current_x) * (
                y_um - current_y
            ) / (previous_y - current_y) + current_x
            if intersection_x > x_um:
                inside = not inside
        previous = current
    return "positive" if inside else "none"


def _primitive_bounds(
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> tuple[float, float, float, float]:
    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        return (
            center_x - radius,
            center_x + radius,
            center_y - radius,
            center_y + radius,
        )
    cached = getattr(primitive, "bbox", None)
    if cached is not None:
        return cached
    points = primitive  # type: ignore[assignment]
    first_x, first_y = points[0]
    x_min = x_max = first_x
    y_min = y_max = first_y
    for x_um, y_um in points[1:]:
        x_min = min(x_min, x_um)
        x_max = max(x_max, x_um)
        y_min = min(y_min, y_um)
        y_max = max(y_max, y_um)
    return x_min, x_max, y_min, y_max


def _bounds_strictly_disjoint(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Prove two primitive closures are separated by a positive axis gap.

    Bounding-box overlap or contact is deliberately inconclusive.  This helper
    is used only to ignore a later subtraction that cannot reach a positive
    primitive at all; every touching or overlapping case stays fail-closed.
    """

    return (
        first[1] < second[0] - _GEOMETRY_EPS_UM
        or second[1] < first[0] - _GEOMETRY_EPS_UM
        or first[3] < second[2] - _GEOMETRY_EPS_UM
        or second[3] < first[2] - _GEOMETRY_EPS_UM
    )


def _point_in_primitive(
    shape: _PlacedPad,
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> Literal["positive", "boundary", "none"]:
    return _point_in_primitive_at(shape.x_um, shape.y_um, kind, primitive)


def _point_in_primitive_at(
    x_um: float,
    y_um: float,
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> Literal["positive", "boundary", "none"]:
    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        margin = radius - hypot(x_um - center_x, y_um - center_y)
        if margin > _GEOMETRY_EPS_UM:
            return "positive"
        if margin >= -_GEOMETRY_EPS_UM:
            return "boundary"
        return "none"
    return _point_in_polygon(
        x_um,
        y_um,
        primitive,  # type: ignore[arg-type]
    )


def _point_to_segment_distance(
    x_um: float,
    y_um: float,
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    """Return the Euclidean distance from a point to one closed segment."""

    x1, y1 = first
    x2, y2 = second
    delta_x, delta_y = x2 - x1, y2 - y1
    squared_length = delta_x * delta_x + delta_y * delta_y
    if squared_length <= _GEOMETRY_EPS_UM * _GEOMETRY_EPS_UM:
        return hypot(x_um - x1, y_um - y1)
    fraction = max(
        0.0,
        min(
            1.0,
            ((x_um - x1) * delta_x + (y_um - y1) * delta_y) / squared_length,
        ),
    )
    return hypot(x_um - (x1 + fraction * delta_x), y_um - (y1 + fraction * delta_y))


def _primitive_boundary_distance(
    x_um: float,
    y_um: float,
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> float:
    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        return abs(hypot(x_um - center_x, y_um - center_y) - radius)
    polygon = primitive  # type: ignore[assignment]
    if len(polygon) < 2:
        return float("inf")
    return min(
        _point_to_segment_distance(x_um, y_um, previous, current)
        for previous, current in zip((polygon[-1], *polygon[:-1]), polygon)
    )


def _replay_copper_fill(
    x_um: float,
    y_um: float,
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> bool | None:
    """Replay source boolean primitives at one non-boundary point.

    ``None`` means the point falls on an input primitive boundary.  Callers
    resolve that case against the *final* boolean fill, rather than treating an
    internal tessellation edge as an electrical ambiguity.
    """

    filled = False
    for kind, primitive in primitives:
        relation = _point_in_primitive_at(x_um, y_um, kind, primitive)
        if relation == "boundary":
            return None
        if relation == "positive":
            filled = kind.startswith("positive_")
    return filled


def _incident_boundary_angles(
    x_um: float,
    y_um: float,
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return local boundary rays and safe incident-length limits.

    These rays are derived from the actual curves through the query point.  A
    sector between every consecutive pair is a constant local boolean cell;
    unlike a fixed set of probe directions, this cannot step over a narrow
    wedge whose vertex is the terminal centre.
    """

    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        if _point_in_primitive_at(x_um, y_um, kind, primitive) != "boundary":
            return (), ()
        radial = atan2(y_um - center_y, x_um - center_x)
        return (
            ((radial - pi / 2.0) % (2.0 * pi), (radial + pi / 2.0) % (2.0 * pi)),
            (radius,),
        )

    polygon = tuple(primitive)  # type: ignore[arg-type]
    rays: list[float] = []
    lengths: list[float] = []
    for previous, current in zip((polygon[-1], *polygon[:-1]), polygon):
        if not _point_on_segment(x_um, y_um, previous, current):
            continue
        first_distance = hypot(previous[0] - x_um, previous[1] - y_um)
        second_distance = hypot(current[0] - x_um, current[1] - y_um)
        if first_distance > _GEOMETRY_EPS_UM:
            rays.append(atan2(previous[1] - y_um, previous[0] - x_um) % (2.0 * pi))
            lengths.append(first_distance)
        if second_distance > _GEOMETRY_EPS_UM:
            rays.append(atan2(current[1] - y_um, current[0] - x_um) % (2.0 * pi))
            lengths.append(second_distance)
    return tuple(rays), tuple(lengths)


def _final_copper_relation_at(
    x_um: float,
    y_um: float,
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> Literal["positive", "boundary", "none"]:
    """Classify one point against the final source copper boolean.

    At an input boundary, the incident curve rays form the complete local
    arrangement.  Replaying one point in every induced angular sector decides
    whether a full neighbourhood is filled (an internal tessellation seam),
    empty, or mixed (a true final boundary).  The probe directions therefore
    come from source topology rather than a fixed angular sampling pattern.
    """

    direct = _replay_copper_fill(x_um, y_um, primitives)
    if direct is not None:
        return "positive" if direct else "none"

    angles: list[float] = []
    incident_limits: list[float] = []
    incident_boundaries: list[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
            tuple[float, ...],
        ]
    ] = []
    unrelated_clearances: list[float] = []
    for kind, primitive in primitives:
        rays, lengths = _incident_boundary_angles(x_um, y_um, kind, primitive)
        if rays:
            angles.extend(rays)
            incident_limits.extend(lengths)
            incident_boundaries.append((kind, primitive, rays))
            continue
        distance = _primitive_boundary_distance(x_um, y_um, kind, primitive)
        if distance > _GEOMETRY_EPS_UM:
            unrelated_clearances.append(distance)
    if not angles:
        return "boundary"

    # Angular sectors describe first-order boundary topology.  Two different
    # curves with the same tangent can still enclose an arbitrarily narrow
    # second-order cusp (for example, tangent negative and later positive
    # circles).  No finite-radius sector probe can prove that cusp filled, so
    # fail closed whenever a curved incident boundary is tangent to a distinct
    # source boundary.  Exact duplicate circles are one coincident curve and
    # remain decidable by ordered replay.
    for first_index, (first_kind, first_primitive, first_rays) in enumerate(
        incident_boundaries
    ):
        for second_kind, second_primitive, second_rays in incident_boundaries[
            first_index + 1 :
        ]:
            if not (first_kind.endswith("circle") or second_kind.endswith("circle")):
                continue
            if (
                first_kind.endswith("circle")
                and second_kind.endswith("circle")
                and tuple(first_primitive) == tuple(second_primitive)
            ):
                continue
            if any(
                abs(sin(first_angle - second_angle)) <= 1.0e-12
                for first_angle in first_rays
                for second_angle in second_rays
            ):
                return "boundary"

    ordered_angles: list[float] = []
    for angle in sorted(angles):
        if not ordered_angles or abs(angle - ordered_angles[-1]) > 1.0e-12:
            ordered_angles.append(angle)
    # Stay within the same local arrangement cell.  The cap limits magnitude
    # only; angular coverage is derived exactly from every incident boundary.
    radius = min(
        1.0e-3,
        min(incident_limits, default=4.0e-3) / 4.0,
        min(unrelated_clearances, default=4.0e-3) / 4.0,
    )
    if radius <= _GEOMETRY_EPS_UM * 8.0:
        return "boundary"

    samples: set[bool] = set()
    for index, angle in enumerate(ordered_angles):
        next_angle = ordered_angles[(index + 1) % len(ordered_angles)]
        if index + 1 == len(ordered_angles):
            next_angle += 2.0 * pi
        probe_angle = (angle + next_angle) / 2.0
        sample = _replay_copper_fill(
            x_um + radius * cos(probe_angle),
            y_um + radius * sin(probe_angle),
            primitives,
        )
        if sample is None:
            return "boundary"
        samples.add(sample)
    if len(samples) != 1:
        return "boundary"
    return "positive" if samples.pop() else "none"


def _final_copper_relation(
    shape: _PlacedPad,
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> Literal["positive", "boundary", "none"]:
    return _final_copper_relation_at(shape.x_um, shape.y_um, primitives)


def _pad_interior_radius(shape: _PlacedPad) -> float:
    """Return a radius whose open disc is provably inside ``shape``."""

    if shape.kind == "CIRCLE":
        return shape.radius_um
    return min(shape.width_um, shape.height_um) / 2.0


def _filled_sector_witness_at_pad_center(
    shape: _PlacedPad,
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> tuple[float, float] | None:
    """Prove a positive-area final-fill sector inside a finite terminal pad.

    This deliberately has different semantics from
    :func:`_final_copper_relation_at`.  A point on the final copper boundary is
    ambiguous for a zero-width topology path, but a terminal is a finite pad:
    one non-boundary filled witness in an incident angular sector proves an
    open copper overlap.  Sector directions still come from every source curve
    through the centre, so a narrow polygon wedge cannot be skipped by a fixed
    angular sampling grid.
    """

    angles: list[float] = []
    incident_limits: list[float] = []
    unrelated_clearances: list[float] = []
    for kind, primitive in primitives:
        rays, lengths = _incident_boundary_angles(
            shape.x_um, shape.y_um, kind, primitive
        )
        if rays:
            angles.extend(rays)
            incident_limits.extend(lengths)
            continue
        distance = _primitive_boundary_distance(
            shape.x_um, shape.y_um, kind, primitive
        )
        if distance > _GEOMETRY_EPS_UM:
            unrelated_clearances.append(distance)
    if not angles:
        return None

    ordered_angles: list[float] = []
    for angle in sorted(angles):
        if not ordered_angles or abs(angle - ordered_angles[-1]) > 1.0e-12:
            ordered_angles.append(angle)
    radius = min(
        1.0e-3,
        _pad_interior_radius(shape) / 4.0,
        min(incident_limits, default=4.0e-3) / 4.0,
        min(unrelated_clearances, default=4.0e-3) / 4.0,
    )
    if radius <= _GEOMETRY_EPS_UM * 8.0:
        return None

    for index, angle in enumerate(ordered_angles):
        next_angle = ordered_angles[(index + 1) % len(ordered_angles)]
        if index + 1 == len(ordered_angles):
            next_angle += 2.0 * pi
        probe_angle = (angle + next_angle) / 2.0
        witness = (
            shape.x_um + radius * cos(probe_angle),
            shape.y_um + radius * sin(probe_angle),
        )
        if _replay_copper_fill(*witness, primitives) is True:
            return witness
    return None


def _ordered_copper_primitives(
    geometry: SpdTopCopperGeometry,
) -> tuple[
    tuple[
        CopperPrimitiveKind,
        Sequence[tuple[float, float]] | tuple[float, float, float],
    ],
    ...,
]:
    polygons = {
        "positive_polygon": geometry.positive_polygons_um,
        "negative_polygon": geometry.negative_polygons_um,
    }
    circles = {
        "positive_circle": geometry.positive_circles_um,
        "negative_circle": geometry.negative_circles_um,
    }
    order = geometry.primitive_order or tuple(
        [("positive_polygon", index) for index in range(len(geometry.positive_polygons_um))]
        + [("positive_circle", index) for index in range(len(geometry.positive_circles_um))]
        + [("negative_polygon", index) for index in range(len(geometry.negative_polygons_um))]
        + [("negative_circle", index) for index in range(len(geometry.negative_circles_um))]
    )
    result: list[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ] = []
    for kind, index in order:
        values = polygons.get(kind)
        if values is None:
            values = circles.get(kind)
        if values is None or index < 0 or index >= len(values):
            continue
        result.append((kind, values[index]))
    return tuple(result)


def _axis_aligned_rectangle_bounds(
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> tuple[float, float, float, float] | None:
    if len(primitive) != 4:
        return None
    points = tuple(primitive)  # type: ignore[arg-type]
    x_values = sorted({point[0] for point in points})
    y_values = sorted({point[1] for point in points})
    if (
        len(x_values) != 2
        or len(y_values) != 2
        or set(points)
        != {(x_um, y_um) for x_um in x_values for y_um in y_values}
        or x_values[1] - x_values[0] <= _GEOMETRY_EPS_UM
        or y_values[1] - y_values[0] <= _GEOMETRY_EPS_UM
    ):
        return None
    return x_values[0], x_values[1], y_values[0], y_values[1]


def _rectangle_as_pad(
    bounds: tuple[float, float, float, float],
) -> _PlacedPad:
    """Represent one non-degenerate axis-aligned open cell for SAT overlap."""

    x_min, x_max, y_min, y_max = bounds
    return _PlacedPad(
        owner_index=-1,
        terminal="VIA",
        net_key="",
        x_um=(x_min + x_max) / 2.0,
        y_um=(y_min + y_max) / 2.0,
        kind="RECTANGLE",
        width_um=x_max - x_min,
        height_um=y_max - y_min,
        rotation_degrees=0.0,
    )


def _pad_rectangle_relation(
    shape: _PlacedPad,
    bounds: tuple[float, float, float, float],
) -> Literal["positive", "boundary", "none"]:
    """Classify finite-pad intersection with one axis-aligned rectangle.

    ``positive`` means positive area, while ``boundary`` is closure-only edge
    or point contact.  The existing circle/OBB and OBB/OBB exact predicates
    make this independent of terminal rotation.
    """

    return _relation(shape, _rectangle_as_pad(bounds))


def _cell_indices_overlapping_interval(
    lower: float,
    upper: float,
    coordinates: Sequence[float],
) -> tuple[int, ...]:
    """Return coordinate cells whose closure intersects one closed interval."""

    if len(coordinates) < 2:
        return ()
    first = max(0, bisect_left(coordinates, lower - _GEOMETRY_EPS_UM) - 1)
    last = min(
        len(coordinates) - 2,
        bisect_right(coordinates, upper + _GEOMETRY_EPS_UM) - 1,
    )
    return tuple(
        index
        for index in range(first, last + 1)
        if coordinates[index + 1] >= lower - _GEOMETRY_EPS_UM
        and coordinates[index] <= upper + _GEOMETRY_EPS_UM
    )


def _rectangle_intersection_witness(
    shape: _PlacedPad,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    """Return an interior witness for a proven positive pad/rectangle overlap."""

    if _pad_rectangle_relation(shape, bounds) != "positive":
        return None
    if shape.kind == "CIRCLE":
        x_min, x_max, y_min, y_max = bounds
        closest_x = min(x_max, max(x_min, shape.x_um))
        closest_y = min(y_max, max(y_min, shape.y_um))
        distance = hypot(closest_x - shape.x_um, closest_y - shape.y_um)
        margin = shape.radius_um - distance
        inset = min(
            max(margin, _GEOMETRY_EPS_UM * 16.0) / 8.0,
            (x_max - x_min) / 4.0,
            (y_max - y_min) / 4.0,
        )
        return (
            min(x_max - inset, max(x_min + inset, shape.x_um)),
            min(y_max - inset, max(y_min + inset, shape.y_um)),
        )

    axis_x, axis_y = shape.axes
    half_x, half_y = shape.width_um / 2.0, shape.height_um / 2.0
    polygon = [
        (
            shape.x_um + sign_x * half_x * axis_x[0] + sign_y * half_y * axis_y[0],
            shape.y_um + sign_x * half_x * axis_x[1] + sign_y * half_y * axis_y[1],
        )
        for sign_x, sign_y in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    ]

    def clip(
        points: list[tuple[float, float]],
        inside: Callable[[tuple[float, float]], bool],
        intersect: Callable[
            [tuple[float, float], tuple[float, float]], tuple[float, float]
        ],
    ) -> list[tuple[float, float]]:
        if not points:
            return []
        result: list[tuple[float, float]] = []
        previous = points[-1]
        previous_inside = inside(previous)
        for current in points:
            current_inside = inside(current)
            if current_inside != previous_inside:
                result.append(intersect(previous, current))
            if current_inside:
                result.append(current)
            previous, previous_inside = current, current_inside
        return result

    x_min, x_max, y_min, y_max = bounds

    def x_intersection(
        first: tuple[float, float], second: tuple[float, float], value: float
    ) -> tuple[float, float]:
        fraction = (value - first[0]) / (second[0] - first[0])
        return value, first[1] + fraction * (second[1] - first[1])

    def y_intersection(
        first: tuple[float, float], second: tuple[float, float], value: float
    ) -> tuple[float, float]:
        fraction = (value - first[1]) / (second[1] - first[1])
        return first[0] + fraction * (second[0] - first[0]), value

    polygon = clip(
        polygon,
        lambda point: point[0] >= x_min,
        lambda first, second: x_intersection(first, second, x_min),
    )
    polygon = clip(
        polygon,
        lambda point: point[0] <= x_max,
        lambda first, second: x_intersection(first, second, x_max),
    )
    polygon = clip(
        polygon,
        lambda point: point[1] >= y_min,
        lambda first, second: y_intersection(first, second, y_min),
    )
    polygon = clip(
        polygon,
        lambda point: point[1] <= y_max,
        lambda first, second: y_intersection(first, second, y_max),
    )
    if len(polygon) < 3:
        return None
    # A convex polygon's vertex mean is strictly interior when it has positive
    # area; the SAT predicate above already excludes degenerate intersections.
    return (
        sum(point[0] for point in polygon) / len(polygon),
        sum(point[1] for point in polygon) / len(polygon),
    )


def _circle_intersection_witness(
    shape: _PlacedPad,
    circle: tuple[float, float, float],
) -> tuple[float, float] | None:
    """Return an interior witness for a proven positive pad/circle overlap."""

    center_x, center_y, radius = circle
    circle_pad = _PlacedPad(
        -1, "VIA", "", center_x, center_y, "CIRCLE", 2.0 * radius, 2.0 * radius, 0.0
    )
    if _relation(shape, circle_pad) != "positive":
        return None
    if shape.kind == "RECTANGLE":
        # Reuse the exact circle/OBB closest-point construction in the pad's
        # local coordinates, then inset toward the OBB interior by less than
        # the proven circle margin.
        axis_x, axis_y = shape.axes
        delta_x, delta_y = center_x - shape.x_um, center_y - shape.y_um
        local_x = delta_x * axis_x[0] + delta_y * axis_x[1]
        local_y = delta_x * axis_y[0] + delta_y * axis_y[1]
        half_x, half_y = shape.width_um / 2.0, shape.height_um / 2.0
        closest_x = min(half_x, max(-half_x, local_x))
        closest_y = min(half_y, max(-half_y, local_y))
        distance = hypot(local_x - closest_x, local_y - closest_y)
        margin = radius - distance
        inset = min(
            max(margin, _GEOMETRY_EPS_UM * 16.0) / 8.0,
            half_x / 4.0,
            half_y / 4.0,
        )
        witness_x = min(half_x - inset, max(-half_x + inset, local_x))
        witness_y = min(half_y - inset, max(-half_y + inset, local_y))
        return (
            shape.x_um + witness_x * axis_x[0] + witness_y * axis_y[0],
            shape.y_um + witness_x * axis_x[1] + witness_y * axis_y[1],
        )

    delta_x, delta_y = center_x - shape.x_um, center_y - shape.y_um
    distance = hypot(delta_x, delta_y)
    if distance <= _GEOMETRY_EPS_UM:
        return shape.x_um, shape.y_um
    direction_x, direction_y = delta_x / distance, delta_y / distance
    lower = max(-shape.radius_um, distance - radius)
    upper = min(shape.radius_um, distance + radius)
    parameter = (lower + upper) / 2.0
    return (
        shape.x_um + parameter * direction_x,
        shape.y_um + parameter * direction_y,
    )


def _pad_primitive_relation(
    shape: _PlacedPad,
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> Literal["positive", "boundary", "none"]:
    """Conservatively classify finite-pad overlap with one source primitive."""

    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        source = _PlacedPad(
            -1,
            "VIA",
            "",
            center_x,
            center_y,
            "CIRCLE",
            2.0 * radius,
            2.0 * radius,
            0.0,
        )
        return _relation(shape, source)
    rectangle = _axis_aligned_rectangle_bounds(primitive)
    if rectangle is not None:
        return _pad_rectangle_relation(shape, rectangle)
    center_relation = _point_in_primitive(shape, kind, primitive)
    if center_relation == "positive":
        return "positive"
    if center_relation == "boundary":
        positive_kind: CopperPrimitiveKind = "positive_polygon"
        if _filled_sector_witness_at_pad_center(
            shape, ((positive_kind, primitive),)
        ) is not None:
            return "positive"
        return "boundary"
    return "none"


def _rectangle_vertices(shape: _PlacedPad) -> tuple[tuple[float, float], ...]:
    """Return the four corners of a placed rectangular terminal."""

    axis_x, axis_y = shape.axes
    half_x, half_y = shape.width_um / 2.0, shape.height_um / 2.0
    return tuple(
        (
            shape.x_um
            + sign_x * half_x * axis_x[0]
            + sign_y * half_y * axis_y[0],
            shape.y_um
            + sign_x * half_x * axis_x[1]
            + sign_y * half_y * axis_y[1],
        )
        for sign_x, sign_y in (
            (-1.0, -1.0),
            (1.0, -1.0),
            (1.0, 1.0),
            (-1.0, 1.0),
        )
    )


def _pad_strictly_inside_primitive(
    shape: _PlacedPad,
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> bool:
    """Prove that the complete closed terminal lies inside one primitive.

    This is used only to prove that an ordered negative primitive completely
    erases earlier source copper under a finite pad.  Sampling is deliberately
    insufficient: polygon containment requires a simple boundary, strict
    interior vertices/centre, and no pad-boundary crossing.
    """

    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        if shape.kind == "CIRCLE":
            return (
                hypot(shape.x_um - center_x, shape.y_um - center_y)
                + shape.radius_um
                < radius - _GEOMETRY_EPS_UM
            )
        return all(
            hypot(x_um - center_x, y_um - center_y)
            < radius - _GEOMETRY_EPS_UM
            for x_um, y_um in _rectangle_vertices(shape)
        )

    polygon = tuple(primitive)  # type: ignore[arg-type]
    if len(polygon) >= 2 and polygon[0] == polygon[-1]:
        polygon = polygon[:-1]
    positive_kind: CopperPrimitiveKind = "positive_polygon"
    if not _primitive_is_simple_connected(positive_kind, polygon):
        return False
    polygon_edges = tuple(zip(polygon, (*polygon[1:], polygon[0])))
    if shape.kind == "CIRCLE":
        if _point_in_polygon(shape.x_um, shape.y_um, polygon) != "positive":
            return False
        clearance = min(
            _point_to_segment_distance(
                shape.x_um, shape.y_um, edge_start, edge_end
            )
            for edge_start, edge_end in polygon_edges
        )
        return clearance > shape.radius_um + _GEOMETRY_EPS_UM

    vertices = _rectangle_vertices(shape)
    if any(
        _point_in_polygon(x_um, y_um, polygon) != "positive"
        for x_um, y_um in vertices
    ):
        return False
    pad_edges = tuple(zip(vertices, (*vertices[1:], vertices[0])))
    return not any(
        _segment_intersects_segment(*pad_edge, *polygon_edge)
        for pad_edge in pad_edges
        for polygon_edge in polygon_edges
    )


def _finite_pad_final_copper_relation(
    shape: _PlacedPad,
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> Literal["positive", "boundary", "none"]:
    """Classify a finite terminal pad against the ordered final copper fill.

    A retained ``positive`` always has an explicit open-area witness.  A
    closure-only contact or an overlap whose ordered subtraction cannot be
    resolved stays ``boundary`` and therefore fail-closed.
    """

    center_relation = _final_copper_relation(shape, primitives)
    if center_relation == "positive":
        return "positive"
    if center_relation == "boundary":
        if _filled_sector_witness_at_pad_center(shape, primitives) is not None:
            return "positive"
        return "boundary"

    # A terminal can sit wholly inside an explicit TOP-copper void while its
    # own pad and Via still form a valid direct branch.  Earlier positive board
    # artwork is then irrelevant.  Return ``none`` only with a strict geometry
    # proof: one later negative contains the complete pad and no still-later
    # positive primitive can touch its closure.  Partial subtraction, boundary
    # contact, non-simple polygons, and later re-adds remain fail-closed below.
    for primitive_index, (kind, primitive) in enumerate(primitives):
        if not kind.startswith("negative_") or not _pad_strictly_inside_primitive(
            shape, kind, primitive
        ):
            continue
        pad_bounds = shape.bbox
        if all(
            not later_kind.startswith("positive_")
            or (
                (later_bounds := _primitive_bounds(later_kind, later_primitive))[1]
                < pad_bounds[0] - _GEOMETRY_EPS_UM
                or later_bounds[0] > pad_bounds[1] + _GEOMETRY_EPS_UM
                or later_bounds[3] < pad_bounds[2] - _GEOMETRY_EPS_UM
                or later_bounds[2] > pad_bounds[3] + _GEOMETRY_EPS_UM
            )
            for later_kind, later_primitive in primitives[primitive_index + 1 :]
        ):
            return "none"

    saw_boundary_contact = False
    saw_unresolved_positive_overlap = False
    for kind, primitive in primitives:
        if not kind.startswith("positive_"):
            continue
        relation = _pad_primitive_relation(shape, kind, primitive)
        if relation == "boundary":
            saw_boundary_contact = True
            continue
        if relation != "positive":
            continue
        witness: tuple[float, float] | None
        if kind.endswith("circle"):
            witness = _circle_intersection_witness(
                shape, primitive  # type: ignore[arg-type]
            )
        elif (bounds := _axis_aligned_rectangle_bounds(primitive)) is not None:
            witness = _rectangle_intersection_witness(shape, bounds)
        else:
            witness = (
                (shape.x_um, shape.y_um)
                if _point_in_primitive(shape, kind, primitive) == "positive"
                else None
            )
        if witness is not None and _replay_copper_fill(*witness, primitives) is True:
            return "positive"
        saw_unresolved_positive_overlap = True
    if saw_boundary_contact or saw_unresolved_positive_overlap:
        return "boundary"
    return "none"


def _rectangular_primitives(
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> tuple[tuple[CopperPrimitiveKind, tuple[float, float, float, float]], ...] | None:
    result: list[tuple[CopperPrimitiveKind, tuple[float, float, float, float]]] = []
    for kind, primitive in primitives:
        if kind.endswith("circle"):
            return None
        bounds = _axis_aligned_rectangle_bounds(primitive)
        if bounds is None:
            return None
        result.append((kind, bounds))
    return tuple(result)


def _rectangles_share_copper(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Whether two closed rectangles meet along area or a non-zero edge."""

    overlap_x = min(first[1], second[1]) - max(first[0], second[0])
    overlap_y = min(first[3], second[3]) - max(first[2], second[2])
    return (
        overlap_x >= -_GEOMETRY_EPS_UM
        and overlap_y >= -_GEOMETRY_EPS_UM
        and (overlap_x > _GEOMETRY_EPS_UM or overlap_y > _GEOMETRY_EPS_UM)
    )


def _positive_primitives_proven_connected(
    first_kind: CopperPrimitiveKind,
    first_primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
    second_kind: CopperPrimitiveKind,
    second_primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> bool:
    """Prove positive-area/edge connectivity without a global path replay."""

    first_rectangle = (
        None
        if first_kind.endswith("circle")
        else _axis_aligned_rectangle_bounds(first_primitive)
    )
    second_rectangle = (
        None
        if second_kind.endswith("circle")
        else _axis_aligned_rectangle_bounds(second_primitive)
    )
    if first_rectangle is not None and second_rectangle is not None:
        return _rectangles_share_copper(first_rectangle, second_rectangle)
    if first_kind.endswith("circle") and second_kind.endswith("circle"):
        first_x, first_y, first_radius = first_primitive  # type: ignore[misc]
        second_x, second_y, second_radius = second_primitive  # type: ignore[misc]
        return (
            hypot(first_x - second_x, first_y - second_y)
            < first_radius + second_radius - _GEOMETRY_EPS_UM
        )
    if first_rectangle is not None and second_kind.endswith("circle"):
        rectangle = first_rectangle
        circle_x, circle_y, radius = second_primitive  # type: ignore[misc]
    elif second_rectangle is not None and first_kind.endswith("circle"):
        rectangle = second_rectangle
        circle_x, circle_y, radius = first_primitive  # type: ignore[misc]
    else:
        return False
    outside_x = max(rectangle[0] - circle_x, 0.0, circle_x - rectangle[1])
    outside_y = max(rectangle[2] - circle_y, 0.0, circle_y - rectangle[3])
    return hypot(outside_x, outside_y) < radius - _GEOMETRY_EPS_UM


def _segment_intersects_segment(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    def orientation(
        start: tuple[float, float],
        end: tuple[float, float],
        point: tuple[float, float],
    ) -> float:
        return (end[0] - start[0]) * (point[1] - start[1]) - (
            end[1] - start[1]
        ) * (point[0] - start[0])

    first_second = orientation(first_start, first_end, second_start)
    first_end_second = orientation(first_start, first_end, second_end)
    second_first = orientation(second_start, second_end, first_start)
    second_end_first = orientation(second_start, second_end, first_end)
    if (
        (first_second > _GEOMETRY_EPS_UM and first_end_second < -_GEOMETRY_EPS_UM)
        or (first_second < -_GEOMETRY_EPS_UM and first_end_second > _GEOMETRY_EPS_UM)
    ) and (
        (second_first > _GEOMETRY_EPS_UM and second_end_first < -_GEOMETRY_EPS_UM)
        or (second_first < -_GEOMETRY_EPS_UM and second_end_first > _GEOMETRY_EPS_UM)
    ):
        return True
    return (
        abs(first_second) <= _GEOMETRY_EPS_UM
        and _point_on_segment(*second_start, first_start, first_end)
    ) or (
        abs(first_end_second) <= _GEOMETRY_EPS_UM
        and _point_on_segment(*second_end, first_start, first_end)
    ) or (
        abs(second_first) <= _GEOMETRY_EPS_UM
        and _point_on_segment(*first_start, second_start, second_end)
    ) or (
        abs(second_end_first) <= _GEOMETRY_EPS_UM
        and _point_on_segment(*first_end, second_start, second_end)
    )


def _primitive_is_simple_connected(
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> bool:
    """Conservatively prove that one positive primitive has one 2-D interior."""

    if kind.endswith("circle"):
        return primitive[2] > _GEOMETRY_EPS_UM  # type: ignore[index]
    points = tuple(primitive)  # type: ignore[arg-type]
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3 or len(points) > 512:
        return False
    edges = tuple(zip(points, (*points[1:], points[0])))
    if any(hypot(end[0] - start[0], end[1] - start[1]) <= _GEOMETRY_EPS_UM for start, end in edges):
        return False
    twice_area = sum(
        start[0] * end[1] - start[1] * end[0] for start, end in edges
    )
    if abs(twice_area) <= _GEOMETRY_EPS_UM:
        return False
    for first_index, first_edge in enumerate(edges):
        for second_index in range(first_index + 1, len(edges)):
            if second_index in {
                first_index,
                first_index + 1,
            } or (first_index == 0 and second_index == len(edges) - 1):
                continue
            if _segment_intersects_segment(*first_edge, *edges[second_index]):
                return False
    return True


def _merged_coordinates(values: Sequence[float]) -> tuple[float, ...]:
    result: list[float] = []
    for value in sorted(values):
        if not result or value - result[-1] > _GEOMETRY_EPS_UM:
            result.append(value)
    return tuple(result)


def _rectangular_copper_memberships(
    shapes: Sequence[_PlacedPad],
    shape_grid: _ShapeGrid,
    net_key: str,
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
    rectangles: Sequence[
        tuple[CopperPrimitiveKind, tuple[float, float, float, float]]
    ],
) -> tuple[tuple[set[int], ...], set[int], tuple[set[int], ...]] | None:
    """Return exact final-fill components and local separator certificates.

    Coordinate compression turns every source edge into a grid line.  Boolean
    fill is constant inside each open cell, so four-neighbour components are
    exactly the positive-area connected components.  Corner-only contact is
    intentionally not conductive, while adjacent tile edges join naturally.
    Potential positive-rectangle components are processed independently to
    keep thousands of short production clusters linear in practice.

    Separator certification is deliberately stricter: a local component must
    be one untouched positive rectangle with collinear terminal centers.
    """

    positive = [
        (index, bounds)
        for index, (kind, bounds) in enumerate(rectangles)
        if kind.startswith("positive_")
    ]
    if not positive:
        return (), set(), ()

    parents = list(range(len(positive)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    # A small spatial hash keeps a board-wide base rectangle as one broad item
    # while comparing each of thousands of local re-add rectangles only with
    # shapes in its neighbourhood.
    positive_sizes = [
        max(bounds[1] - bounds[0], bounds[3] - bounds[2])
        for _index, bounds in positive
    ]
    cell_um = max(1.0, median(positive_sizes))
    rectangle_cells: dict[tuple[int, int], list[int]] = {}
    broad_rectangles: list[int] = []
    for current in range(len(positive)):
        current_bounds = positive[current][1]
        ix0 = floor(current_bounds[0] / cell_um)
        ix1 = floor(current_bounds[1] / cell_um)
        iy0 = floor(current_bounds[2] / cell_um)
        iy1 = floor(current_bounds[3] / cell_um)
        key_count = (ix1 - ix0 + 1) * (iy1 - iy0 + 1)
        if key_count > 256:
            keys: tuple[tuple[int, int], ...] = ()
            candidates = set(range(current))
        else:
            keys = tuple(
                (x_index, y_index)
                for x_index in range(ix0, ix1 + 1)
                for y_index in range(iy0, iy1 + 1)
            )
            candidates = set(broad_rectangles)
            for key in keys:
                candidates.update(rectangle_cells.get(key, ()))
        for other in candidates:
            if not _rectangles_share_copper(current_bounds, positive[other][1]):
                continue
            left, right = root(current), root(other)
            if left != right:
                parents[max(left, right)] = min(left, right)
        if key_count > 256:
            broad_rectangles.append(current)
        else:
            for key in keys:
                rectangle_cells.setdefault(key, []).append(current)

    positive_components: dict[int, list[int]] = {}
    for index in range(len(positive)):
        positive_components.setdefault(root(index), []).append(index)

    owners_by_positive_component: dict[int, set[int]] = {}
    for positive_index, (_primitive_index, bounds) in enumerate(positive):
        for candidate_index in shape_grid.query(bounds):
            shape = shapes[candidate_index]
            if shape.net_key != net_key:
                continue
            # Candidate ownership follows the finite terminal pad.  A centre
            # may lie exactly on (or even outside) a source rectangle while a
            # substantial part of its copper overlaps the rectangle.
            if _pad_rectangle_relation(shape, bounds) != "none":
                owners_by_positive_component.setdefault(root(positive_index), set()).add(
                    shape.owner_index
                )

    shapes_by_owner = {shape.owner_index: shape for shape in shapes}
    groups: list[set[int]] = []
    separator_groups: list[set[int]] = []
    # A terminal can meet more than one disjoint source-positive component.
    # Keep area membership and closure-only contact separate until every
    # component has been considered: positive overlap with one component must
    # not be invalidated merely because another component touches the far edge
    # of the same finite pad.  The boundary-only component is intentionally not
    # attached, while a terminal with no positive-area membership still fails
    # closed below.
    positive_owners: set[int] = set()
    boundary_candidates: set[int] = set()
    for component_root, positive_indices in positive_components.items():
        owners = owners_by_positive_component.get(component_root, set())
        if not owners:
            continue
        member_bounds = [positive[index][1] for index in positive_indices]
        component_bounds = (
            min(item[0] for item in member_bounds),
            max(item[1] for item in member_bounds),
            min(item[2] for item in member_bounds),
            max(item[3] for item in member_bounds),
        )
        x_values = [component_bounds[0], component_bounds[1]]
        y_values = [component_bounds[2], component_bounds[3]]
        relevant_indices: list[int] = []
        for primitive_index, (_kind, bounds) in enumerate(rectangles):
            if not _rectangles_share_copper(component_bounds, bounds):
                continue
            relevant_indices.append(primitive_index)
            x_values.extend(
                (max(component_bounds[0], bounds[0]), min(component_bounds[1], bounds[1]))
            )
            y_values.extend(
                (max(component_bounds[2], bounds[2]), min(component_bounds[3], bounds[3]))
            )
        x_coordinates = _merged_coordinates(x_values)
        y_coordinates = _merged_coordinates(y_values)
        cell_count = (len(x_coordinates) - 1) * (len(y_coordinates) - 1)
        # A pathological board-wide boolean arrangement is not allowed to turn
        # import into an unbounded quadratic allocation.  The generic fallback
        # below remains fail-closed for subtractive geometry.
        if (
            cell_count > 250_000
            or cell_count * max(1, len(relevant_indices)) > 2_000_000
        ):
            return None
        relevant_primitives = tuple(primitives[index] for index in relevant_indices)

        filled: set[tuple[int, int]] = set()
        for x_index, (x_left, x_right) in enumerate(
            zip(x_coordinates, x_coordinates[1:])
        ):
            x_mid = (x_left + x_right) / 2.0
            for y_index, (y_bottom, y_top) in enumerate(
                zip(y_coordinates, y_coordinates[1:])
            ):
                y_mid = (y_bottom + y_top) / 2.0
                if _replay_copper_fill(x_mid, y_mid, relevant_primitives):
                    filled.add((x_index, y_index))

        cell_parents = {cell: cell for cell in filled}

        def cell_root(cell: tuple[int, int]) -> tuple[int, int]:
            while cell_parents[cell] != cell:
                cell_parents[cell] = cell_parents[cell_parents[cell]]
                cell = cell_parents[cell]
            return cell

        for cell in sorted(filled):
            for neighbour in ((cell[0] - 1, cell[1]), (cell[0], cell[1] - 1)):
                if neighbour not in filled:
                    continue
                left, right = cell_root(cell), cell_root(neighbour)
                if left != right:
                    canonical = min(left, right)
                    cell_parents[left] = canonical
                    cell_parents[right] = canonical

        members_by_cell_component: dict[tuple[int, int], set[int]] = {}
        for owner in owners:
            shape = shapes_by_owner[owner]
            x_min, x_max, y_min, y_max = shape.bbox
            positive_roots: set[tuple[int, int]] = set()
            boundary_contact = False
            for x_index in _cell_indices_overlapping_interval(
                x_min, x_max, x_coordinates
            ):
                for y_index in _cell_indices_overlapping_interval(
                    y_min, y_max, y_coordinates
                ):
                    cell = (x_index, y_index)
                    if cell not in filled:
                        continue
                    relation = _pad_rectangle_relation(
                        shape,
                        (
                            x_coordinates[x_index],
                            x_coordinates[x_index + 1],
                            y_coordinates[y_index],
                            y_coordinates[y_index + 1],
                        ),
                    )
                    if relation == "positive":
                        positive_roots.add(cell_root(cell))
                    elif relation == "boundary":
                        boundary_contact = True

            # One physical terminal may overlap multiple disconnected source
            # fill components.  Attach it to every such component: the finite
            # pad itself is then the real electrical bridge.  Mere edge/point
            # contact never adds a root.
            if positive_roots:
                positive_owners.add(owner)
                for component in positive_roots:
                    members_by_cell_component.setdefault(component, set()).add(owner)
            elif boundary_contact:
                boundary_candidates.add(owner)
        local_groups = [
            members
            for members in members_by_cell_component.values()
            if len(members) >= 2
        ]
        groups.extend(local_groups)

        # A separator certificate is stronger than a connectivity certificate.
        # Other disjoint Box primitives on the same NET are irrelevant, but the
        # local final component must come from exactly one untouched positive
        # rectangle.  Any local subtraction, re-add, overlap, or edge-connected
        # tile keeps the membership proof while withholding gap authorization.
        if len(positive_indices) == 1 and len(local_groups) == 1:
            source_primitive_index = positive[positive_indices[0]][0]
            if (
                relevant_indices == [source_primitive_index]
                and rectangles[source_primitive_index][0] == "positive_polygon"
                and _collinear_pad_centers(local_groups[0], shapes)
            ):
                separator_groups.append(local_groups[0])
    return (
        tuple(groups),
        boundary_candidates - positive_owners,
        tuple(separator_groups),
    )


def _segment_boundary_parameters(
    first: tuple[float, float],
    second: tuple[float, float],
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> tuple[float, ...]:
    """Return exact path parameters where one primitive boundary is met."""

    x0, y0 = first
    delta_x, delta_y = second[0] - x0, second[1] - y0
    result: list[float] = []
    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        a = delta_x * delta_x + delta_y * delta_y
        if a <= _GEOMETRY_EPS_UM * _GEOMETRY_EPS_UM:
            return ()
        offset_x, offset_y = x0 - center_x, y0 - center_y
        b = 2.0 * (offset_x * delta_x + offset_y * delta_y)
        c = offset_x * offset_x + offset_y * offset_y - radius * radius
        b_squared = b * b
        four_ac = 4.0 * a * c
        discriminant = b_squared - four_ac
        # At board-scale coordinates an exact tangent subtracts two ~1e10
        # terms.  Its rounded discriminant can therefore be slightly negative
        # even though the circle is met.  Clamp only within a scale-aware
        # floating-point error envelope; treating a numerically indistinguish-
        # able near tangent as a boundary is the required fail-closed choice.
        discriminant_tolerance = (
            64.0 * float_info.epsilon * (abs(b_squared) + abs(four_ac))
        )
        if discriminant >= -discriminant_tolerance:
            root_discriminant = max(discriminant, 0.0) ** 0.5
            for value in (
                (-b - root_discriminant) / (2.0 * a),
                (-b + root_discriminant) / (2.0 * a),
            ):
                if -_GEOMETRY_EPS_UM <= value <= 1.0 + _GEOMETRY_EPS_UM:
                    result.append(min(1.0, max(0.0, value)))
        return tuple(result)

    polygon = tuple(primitive)  # type: ignore[arg-type]
    for start, end in zip((polygon[-1], *polygon[:-1]), polygon):
        edge_x, edge_y = end[0] - start[0], end[1] - start[1]
        offset_x, offset_y = start[0] - x0, start[1] - y0
        denominator = delta_x * edge_y - delta_y * edge_x
        if abs(denominator) <= _GEOMETRY_EPS_UM:
            cross = offset_x * delta_y - offset_y * delta_x
            if abs(cross) > _GEOMETRY_EPS_UM:
                continue
            squared = delta_x * delta_x + delta_y * delta_y
            if squared <= _GEOMETRY_EPS_UM * _GEOMETRY_EPS_UM:
                continue
            for point in (start, end):
                value = ((point[0] - x0) * delta_x + (point[1] - y0) * delta_y) / squared
                if -_GEOMETRY_EPS_UM <= value <= 1.0 + _GEOMETRY_EPS_UM:
                    result.append(min(1.0, max(0.0, value)))
            continue
        path_parameter = (offset_x * edge_y - offset_y * edge_x) / denominator
        edge_parameter = (offset_x * delta_y - offset_y * delta_x) / denominator
        if (
            -_GEOMETRY_EPS_UM <= path_parameter <= 1.0 + _GEOMETRY_EPS_UM
            and -_GEOMETRY_EPS_UM <= edge_parameter <= 1.0 + _GEOMETRY_EPS_UM
        ):
            result.append(min(1.0, max(0.0, path_parameter)))
    return tuple(result)


def _segment_is_final_copper(
    first: _PlacedPad,
    second: _PlacedPad,
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> bool:
    """Prove a straight positive-width-region path in the final boolean fill."""

    start = (first.x_um, first.y_um)
    end = (second.x_um, second.y_um)
    path_bounds = (
        min(first.x_um, second.x_um),
        max(first.x_um, second.x_um),
        min(first.y_um, second.y_um),
        max(first.y_um, second.y_um),
    )
    relevant_primitives = tuple(
        (kind, primitive)
        for kind, primitive in primitives
        if (
            (bounds := _primitive_bounds(kind, primitive))[1]
            >= path_bounds[0] - _GEOMETRY_EPS_UM
            and bounds[0] <= path_bounds[1] + _GEOMETRY_EPS_UM
            and bounds[3] >= path_bounds[2] - _GEOMETRY_EPS_UM
            and bounds[2] <= path_bounds[3] + _GEOMETRY_EPS_UM
        )
    )
    parameters = [0.0, 1.0]
    for kind, primitive in relevant_primitives:
        parameters.extend(
            _segment_boundary_parameters(start, end, kind, primitive)
        )
    ordered: list[float] = []
    for value in sorted(parameters):
        if not ordered or value - ordered[-1] > 1.0e-12:
            ordered.append(value)
    for left, right in zip(ordered, ordered[1:]):
        if right - left <= 1.0e-12:
            continue
        parameter = (left + right) / 2.0
        if not _replay_copper_fill(
            first.x_um + parameter * (second.x_um - first.x_um),
            first.y_um + parameter * (second.y_um - first.y_um),
            relevant_primitives,
        ):
            return False
    for parameter in ordered[1:-1]:
        relation = _final_copper_relation_at(
            first.x_um + parameter * (second.x_um - first.x_um),
            first.y_um + parameter * (second.y_um - first.y_um),
            relevant_primitives,
        )
        if relation != "positive":
            return False
    return True


def _ordered_final_copper_shape(
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
):
    """Build exact ordered boolean copper for a bounded fallback proof."""

    try:
        from shapely.errors import GEOSException
        from shapely.geometry import GeometryCollection, Point, Polygon
        from shapely.ops import unary_union
    except ImportError:
        return None

    shape = GeometryCollection()
    runs: list[tuple[bool, list[object]]] = []
    try:
        for kind, raw in primitives:
            if kind.endswith("polygon"):
                primitive = Polygon(raw)  # type: ignore[arg-type]
            else:
                x_um, y_um, radius_um = map(float, raw)
                if not isfinite(radius_um) or radius_um <= 0.0:
                    return None
                primitive = Point(x_um, y_um).buffer(radius_um, quad_segs=64)
            if primitive.is_empty or not primitive.is_valid or primitive.area <= 0.0:
                return None
            positive = kind.startswith("positive_")
            if runs and runs[-1][0] == positive:
                runs[-1][1].append(primitive)
            else:
                runs.append((positive, [primitive]))
        for positive, run in runs:
            batch = unary_union(run)
            if batch.is_empty or not batch.is_valid or batch.area <= 0.0:
                return None
            shape = shape.union(batch) if positive else shape.difference(batch)
    except (TypeError, ValueError, IndexError, ArithmeticError, GEOSException):
        return None
    return None if shape.is_empty or not shape.is_valid or shape.area <= 0.0 else shape


def _placed_pad_polygon(shape: _PlacedPad):
    """Return one exact regular terminal shape for final-component queries."""

    from shapely.geometry import Point, Polygon

    if shape.kind == "CIRCLE":
        return Point(shape.x_um, shape.y_um).buffer(shape.radius_um, quad_segs=64)
    axis_x, axis_y = shape.axes
    half_x = shape.width_um / 2.0
    half_y = shape.height_um / 2.0
    return Polygon(
        tuple(
            (
                shape.x_um + x_sign * half_x * axis_x[0] + y_sign * half_y * axis_y[0],
                shape.y_um + x_sign * half_x * axis_x[1] + y_sign * half_y * axis_y[1],
            )
            for x_sign, y_sign in ((1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0), (1.0, -1.0))
        )
    )


def _exact_final_component_memberships(
    final_members: set[int],
    shapes_by_owner: Mapping[int, _PlacedPad],
    primitives: Sequence[
        tuple[
            CopperPrimitiveKind,
            Sequence[tuple[float, float]] | tuple[float, float, float],
        ]
    ],
) -> tuple[tuple[set[int], ...], set[int]] | None:
    """Map pads to exact final copper components after pair-budget exhaustion."""

    try:
        from shapely.errors import GEOSException
        from shapely.strtree import STRtree
    except ImportError:
        return None
    final_shape = _ordered_final_copper_shape(primitives)
    if final_shape is None:
        return None
    components = tuple(
        item
        for item in (
            final_shape.geoms
            if hasattr(final_shape, "geoms")
            else (final_shape,)
        )
        if item.geom_type in {"Polygon", "MultiPolygon"}
        and not item.is_empty
        and item.area > 0.0
    )
    if not components:
        return None
    tree = STRtree(components)
    memberships = [set() for _item in components]
    boundary_owners: set[int] = set()
    try:
        for owner in sorted(final_members):
            pad = _placed_pad_polygon(shapes_by_owner[owner])
            positive = False
            boundary = False
            for raw_index in tree.query(pad, predicate="intersects"):
                component_index = int(raw_index)
                overlap = components[component_index].intersection(pad)
                if overlap.area > 0.0:
                    memberships[component_index].add(owner)
                    positive = True
                elif not overlap.is_empty:
                    boundary = True
            if not positive:
                if boundary:
                    boundary_owners.add(owner)
                else:
                    # The analytic ordered replay admitted this finite pad, but
                    # GEOS could not place it in a final component.  Do not use
                    # a partial exact proof in that inconsistent state.
                    return None
    except (KeyError, TypeError, ValueError, ArithmeticError, GEOSException):
        return None
    return (
        tuple(members for members in memberships if len(members) >= 2),
        boundary_owners,
    )


def _copper_memberships(
    shapes: Sequence[_PlacedPad],
    grid: _ShapeGrid,
    geometries: Sequence[SpdTopCopperGeometry],
    *,
    top_layer: str,
) -> tuple[tuple[set[int], ...], set[int], tuple[set[int], ...]]:
    """Return memberships of provable final-boolean copper components."""

    groups: list[set[int]] = []
    separator_groups: list[set[int]] = []
    boundary_owners: set[int] = set()
    shape_net_keys = {shape.net_key for shape in shapes}
    shape_by_owner = {shape.owner_index: shape for shape in shapes}
    for geometry in geometries:
        if geometry.layer.casefold() != top_layer.casefold():
            continue
        net_key = geometry.net.casefold()
        if net_key not in shape_net_keys:
            continue
        primitives = _ordered_copper_primitives(geometry)
        rectangular = _rectangular_primitives(primitives)
        if rectangular is not None:
            exact = _rectangular_copper_memberships(
                shapes,
                grid,
                net_key,
                primitives,
                rectangular,
            )
            if exact is not None:
                exact_groups, exact_boundary, exact_separators = exact
                groups.extend(exact_groups)
                boundary_owners.update(exact_boundary)
                separator_groups.extend(exact_separators)
                continue

        primitive_bounds_by_index = tuple(
            _primitive_bounds(kind, primitive) for kind, primitive in primitives
        )
        local_groups: list[
            tuple[
                int,
                CopperPrimitiveKind,
                Sequence[tuple[float, float]] | tuple[float, float, float],
                set[int],
            ]
        ] = []
        candidate_primitives: dict[
            int,
            list[
                tuple[
                    CopperPrimitiveKind,
                    Sequence[tuple[float, float]] | tuple[float, float, float],
                ]
            ],
        ] = {}
        for primitive_index, (kind, primitive) in enumerate(primitives):
            bounds = primitive_bounds_by_index[primitive_index]
            members: set[int] = set()
            for candidate_index in grid.query(bounds):
                candidate = shapes[candidate_index]
                if candidate.net_key != net_key:
                    continue
                candidate_primitives.setdefault(candidate.owner_index, []).append(
                    (kind, primitive)
                )
                relation = _pad_primitive_relation(candidate, kind, primitive)
                # Keep only positive-area finite-pad overlap provisionally in
                # its source primitive.  Closure-only contact cannot make this
                # terminal an owner of another positive copper component.
                # ``final_members`` below still replays the full ordered boolean
                # and removes subtracted or otherwise ambiguous copper.
                if relation == "positive" and kind.startswith("positive_"):
                    members.add(candidate.owner_index)
            if kind.startswith("positive_") and members:
                local_groups.append((primitive_index, kind, primitive, members))
        final_members: set[int] = set()
        for owner, owner_primitives in candidate_primitives.items():
            relation = _finite_pad_final_copper_relation(
                shape_by_owner[owner], owner_primitives
            )
            if relation == "boundary":
                boundary_owners.add(owner)
            elif relation == "positive":
                final_members.add(owner)

        # Non-rectangular/circular arrangements use only explicit connectivity
        # proofs.  A source positive primitive is intrinsically connected when
        # no later subtraction can split it; otherwise every retained edge must
        # have a straight path whose complete boundary arrangement is filled.
        retained_groups: list[
            tuple[int, CopperPrimitiveKind, object, set[int], bool]
        ] = []
        exact_component_fallback_needed = False
        parents = {owner: owner for owner in final_members}

        def owner_root(owner: int) -> int:
            while parents[owner] != owner:
                parents[owner] = parents[parents[owner]]
                owner = parents[owner]
            return owner

        def join(first: int, second: int) -> None:
            left, right = owner_root(first), owner_root(second)
            if left != right:
                parents[max(left, right)] = min(left, right)

        for primitive_index, kind, primitive, members in local_groups:
            retained = members & final_members
            if not retained:
                continue
            primitive_bounds = primitive_bounds_by_index[primitive_index]
            later_subtraction_can_reach = any(
                later_kind.startswith("negative_")
                and not _bounds_strictly_disjoint(
                    primitive_bounds,
                    primitive_bounds_by_index[later_index],
                )
                for later_index, (later_kind, _later_primitive) in enumerate(
                    primitives[primitive_index + 1 :],
                    start=primitive_index + 1,
                )
            )
            safe_whole_primitive = (
                not later_subtraction_can_reach
                and _primitive_is_simple_connected(kind, primitive)
            )
            retained_groups.append(
                (primitive_index, kind, primitive, retained, safe_whole_primitive)
            )
            ordered_members = sorted(retained)
            if safe_whole_primitive:
                for owner in ordered_members[1:]:
                    join(ordered_members[0], owner)
            elif (
                len(ordered_members) <= 64
                and len(ordered_members) * (len(ordered_members) - 1) // 2
                * len(primitives)
                <= _GENERIC_PATH_WORK_BUDGET
            ):
                for first_index, first_owner in enumerate(ordered_members):
                    for second_owner in ordered_members[first_index + 1 :]:
                        if _segment_is_final_copper(
                            shape_by_owner[first_owner],
                            shape_by_owner[second_owner],
                            primitives,
                        ):
                            join(first_owner, second_owner)
            else:
                exact_component_fallback_needed = True

        # Merge tessellated positive primitives only through a source-replayed
        # path.  This accepts edge seams, but rejects point contacts and voids.
        safe_groups = [
            (*group, primitive_bounds_by_index[group[0]])
            for group in retained_groups
            if group[4]
        ]
        active_groups: list[tuple[object, ...]] = []
        for second_group in sorted(
            safe_groups, key=lambda group: group[5][0]  # type: ignore[index]
        ):
            second_bounds = second_group[5]  # type: ignore[assignment]
            active_groups = [
                group
                for group in active_groups
                if group[5][1] >= second_bounds[0] - _GEOMETRY_EPS_UM  # type: ignore[index]
            ]
            for first_group in active_groups:
                first_bounds = first_group[5]  # type: ignore[assignment]
                if not _rectangles_share_copper(first_bounds, second_bounds):
                    continue
                if _positive_primitives_proven_connected(
                    first_group[1],  # type: ignore[arg-type]
                    first_group[2],  # type: ignore[arg-type]
                    second_group[1],  # type: ignore[arg-type]
                    second_group[2],  # type: ignore[arg-type]
                ):
                    join(
                        next(iter(first_group[3])),  # type: ignore[arg-type]
                        next(iter(second_group[3])),  # type: ignore[arg-type]
                    )
                    continue
                connected = False
                for first_owner in sorted(first_group[3]):
                    for second_owner in sorted(second_group[3]):
                        if _segment_is_final_copper(
                            shape_by_owner[first_owner],
                            shape_by_owner[second_owner],
                            primitives,
                        ):
                            join(first_owner, second_owner)
                            connected = True
                            break
                    if connected:
                        break
            active_groups.append(second_group)

        if exact_component_fallback_needed:
            exact = _exact_final_component_memberships(
                final_members,
                shape_by_owner,
                primitives,
            )
            if exact is not None:
                exact_groups, exact_boundary = exact
                groups.extend(exact_groups)
                boundary_owners.update(exact_boundary)
                continue

        component_members: dict[int, set[int]] = {}
        for owner in final_members:
            component_members.setdefault(owner_root(owner), set()).add(owner)
        groups.extend(
            members for members in component_members.values() if len(members) >= 2
        )
        for _primitive_index, kind, primitive, members, _safe in retained_groups:
            if (
                kind == "positive_polygon"
                and not geometry.negative_polygons_um
                and not geometry.positive_circles_um
                and not geometry.negative_circles_um
                and _axis_aligned_rectangle(primitive)
                and _collinear_pad_centers(members, shapes)
            ):
                separator_groups.append(members)
    return tuple(groups), boundary_owners, tuple(separator_groups)


def _axis_aligned_rectangle(
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> bool:
    if len(primitive) != 4:
        return False
    points = tuple(primitive)  # type: ignore[arg-type]
    x_values = sorted({point[0] for point in points})
    y_values = sorted({point[1] for point in points})
    return (
        len(x_values) == 2
        and len(y_values) == 2
        and set(points)
        == {(x_um, y_um) for x_um in x_values for y_um in y_values}
    )


def _collinear_pad_centers(
    members: set[int],
    shapes: Sequence[_PlacedPad],
) -> bool:
    by_owner = {shape.owner_index: shape for shape in shapes}
    x_values = [by_owner[owner].x_um for owner in members]
    y_values = [by_owner[owner].y_um for owner in members]
    return (
        max(x_values) - min(x_values) <= _GEOMETRY_EPS_UM
        or max(y_values) - min(y_values) <= _GEOMETRY_EPS_UM
    )


def _minimum_distance_tree(
    members: Sequence[int],
    shapes_by_owner: Mapping[int, _PlacedPad],
    evidence: Sequence[DecapPadEvidence],
) -> set[tuple[int, int]]:
    """Return a deterministic local tree inside one source-proven copper island.

    The source primitive is the electrical evidence.  Distance is used only to
    preserve physical neighbourhood inside that already proven island, so a
    removed pad cell cuts its local links instead of cutting an arbitrary
    REFDES-based star.
    """

    ordered = tuple(
        sorted(
            members,
            key=lambda owner: (
                shapes_by_owner[owner].x_um,
                shapes_by_owner[owner].y_um,
                evidence[owner].refdes.casefold(),
            ),
        )
    )
    if len(ordered) < 2:
        return set()

    # Shared-pad groups in production are short rows.  Use an exact Euclidean
    # MST for ordinary groups; the bounded-neighbour fallback prevents a broad
    # source polygon from creating quadratic work.
    candidate_pairs: set[tuple[int, int]] = set()
    if len(ordered) <= 512:
        candidate_pairs = {
            (ordered[first], ordered[second])
            for first in range(len(ordered))
            for second in range(first + 1, len(ordered))
        }
    else:
        for axis in ("x", "y"):
            axis_order = sorted(
                ordered,
                key=lambda owner: (
                    shapes_by_owner[owner].x_um
                    if axis == "x"
                    else shapes_by_owner[owner].y_um,
                    shapes_by_owner[owner].y_um
                    if axis == "x"
                    else shapes_by_owner[owner].x_um,
                    evidence[owner].refdes.casefold(),
                ),
            )
            for left, right in zip(axis_order, axis_order[1:]):
                candidate_pairs.add((min(left, right), max(left, right)))

    parents = {owner: owner for owner in ordered}

    def root(owner: int) -> int:
        while parents[owner] != owner:
            parents[owner] = parents[parents[owner]]
            owner = parents[owner]
        return owner

    weighted: list[tuple[float, str, str, int, int]] = []
    for left, right in candidate_pairs:
        first, second = shapes_by_owner[left], shapes_by_owner[right]
        left_name, right_name = sorted(
            (evidence[left].refdes, evidence[right].refdes), key=str.casefold
        )
        weighted.append(
            (
                hypot(first.x_um - second.x_um, first.y_um - second.y_um),
                left_name.casefold(),
                right_name.casefold(),
                left,
                right,
            )
        )

    result: set[tuple[int, int]] = set()
    for _distance, _left_name, _right_name, left, right in sorted(weighted):
        left_root, right_root = root(left), root(right)
        if left_root == right_root:
            continue
        canonical_root = min(left_root, right_root)
        parents[left_root] = canonical_root
        parents[right_root] = canonical_root
        result.add((min(left, right), max(left, right)))
        if len(result) == len(ordered) - 1:
            break
    return result


def _local_copper_edges(
    groups: Sequence[set[int]],
    evidence: Sequence[DecapPadEvidence],
    shapes_by_owner: Mapping[int, _PlacedPad],
    *,
    component_by_owner: Mapping[int, int] | None = None,
) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for group in groups:
        buckets: dict[int, list[int]] = {}
        for owner in group:
            component = (
                0
                if component_by_owner is None
                else component_by_owner.get(owner, -1)
            )
            if component < 0:
                continue
            buckets.setdefault(component, []).append(owner)
        for members in buckets.values():
            result.update(
                _minimum_distance_tree(members, shapes_by_owner, evidence)
            )
    return result


def _connected(members: set[int], edges: set[tuple[int, int]]) -> bool:
    if len(members) <= 1:
        return True
    adjacency: dict[int, set[int]] = {item: set() for item in members}
    for first, second in edges:
        if first in members and second in members:
            adjacency[first].add(second)
            adjacency[second].add(first)
    visited: set[int] = set()
    pending = [next(iter(members))]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency[current] - visited)
    return visited == members


def _components(count: int, edges: set[tuple[int, int]]) -> tuple[set[int], ...]:
    parents = list(range(count))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for first, second in edges:
        left, right = root(first), root(second)
        if left != right:
            parents[max(left, right)] = min(left, right)
    grouped: dict[int, set[int]] = {}
    for index in range(count):
        grouped.setdefault(root(index), set()).add(index)
    return tuple(grouped[key] for key in sorted(grouped))


def _cluster_id(
    evidence: Sequence[DecapPadEvidence],
    members: set[int],
    power_edges: set[tuple[int, int]],
    ground_edges: set[tuple[int, int]],
) -> str:
    refs = {index: evidence[index].refdes for index in members}
    member_refs = sorted(refs.values(), key=str.casefold)

    def ref_edge(first: int, second: int) -> tuple[str, str]:
        left, right = refs[first], refs[second]
        return (left, right) if left.casefold() <= right.casefold() else (right, left)

    pwr = sorted(
        ref_edge(first, second)
        for first, second in power_edges
        if first in members and second in members
    )
    gnd = sorted(
        ref_edge(first, second)
        for first, second in ground_edges
        if first in members and second in members
    )
    source_identity = sorted(
        {
            (
                evidence[index].layer or "",
                evidence[index].power_net,
                evidence[index].ground_net,
            )
            for index in members
        },
        key=lambda item: tuple(value.casefold() for value in item),
    )
    payload = repr((source_identity, member_refs, pwr, gnd)).encode("utf-8")
    return f"SPDCL:{sha256(payload).hexdigest()[:16]}"


def extract_shared_pad_connectivity(
    evidence: Sequence[DecapPadEvidence],
    via_endpoints: Sequence[ViaTopEndpoint],
    padstack_shapes: Mapping[str, tuple[SpdPadShape, ...]],
    *,
    top_layer: str | None,
    uncertain_via_nets: Sequence[str] = (),
    top_copper_geometries: Sequence[SpdTopCopperGeometry] = (),
) -> SharedPadExtraction:
    """Classify every decap using exact source pad/via evidence.

    ``uncertain_via_nets`` is used for a source Via whose padstack or endpoint
    cannot be resolved exactly.  Every affected decap is then fail-closed.
    """

    count = len(evidence)
    connections: list[SpdDecapConnection | None] = [None] * count
    warnings: list[str] = []
    invalid: dict[int, list[str]] = {}
    power_shapes: list[_PlacedPad] = []
    ground_shapes: list[_PlacedPad] = []
    power_by_owner: dict[int, _PlacedPad] = {}
    ground_by_owner: dict[int, _PlacedPad] = {}
    shape_cache: dict[
        tuple[str, str], tuple[SpdPadShape | None, str | None]
    ] = {}

    def top_shape(padstack: str | None) -> tuple[SpdPadShape | None, str | None]:
        key = ((padstack or "").casefold(), top_layer.casefold())
        if key not in shape_cache:
            shape_cache[key] = _shape_for_layer(
                padstack_shapes, padstack, top_layer
            )
        return shape_cache[key]

    if top_layer is None:
        return SharedPadExtraction(
            connections=tuple(
                SpdDecapConnection(
                    item.refdes,
                    "UNRESOLVED",
                    reason="SPD stack-up has no TOP conductor",
                )
                for item in evidence
            ),
            clusters=(),
            warnings=("SPD stack-up has no TOP conductor for shared-pad extraction",),
        )

    for index, item in enumerate(evidence):
        if not item.top_side or not item.layer or item.layer.casefold() != top_layer.casefold():
            connections[index] = SpdDecapConnection(
                item.refdes,
                "OUT_OF_SCOPE",
                reason="shared-pad extraction currently applies only to the TOP conductor",
            )
            continue
        if not item.power_rotation_valid or not item.ground_rotation_valid:
            invalid[index] = ["terminal pad rotation is malformed or non-finite"]
            continue
        power_shape, power_error = top_shape(item.power_padstack)
        ground_shape, ground_error = top_shape(item.ground_padstack)
        errors = [error for error in (power_error, ground_error) if error]
        if errors:
            invalid[index] = errors
            continue
        assert power_shape is not None and ground_shape is not None
        placed_power = _placed(
            index,
            "PWR",
            item.power_net,
            item.power_x_um,
            item.power_y_um,
            item.power_rotation_degrees,
            power_shape,
        )
        placed_ground = _placed(
            index,
            "GND",
            item.ground_net,
            item.ground_x_um,
            item.ground_y_um,
            item.ground_rotation_degrees,
            ground_shape,
        )
        power_shapes.append(placed_power)
        ground_shapes.append(placed_ground)
        power_by_owner[index] = placed_power
        ground_by_owner[index] = placed_ground

    power_grid = _ShapeGrid(power_shapes)
    ground_grid = _ShapeGrid(ground_shapes)
    power_edges, power_boundary, power_conflicts = _overlap_edges(
        power_shapes, power_grid
    )
    ground_edges, ground_boundary, ground_conflicts = _overlap_edges(
        ground_shapes, ground_grid
    )
    exact_ground_edges = set(ground_edges)
    copper_power_groups: tuple[set[int], ...] = ()
    copper_ground_groups: tuple[set[int], ...] = ()
    copper_separator_groups: tuple[set[int], ...] = ()
    copper_power_boundary: set[int] = set()
    copper_ground_boundary: set[int] = set()
    if top_copper_geometries:
        (
            copper_power_groups,
            copper_power_boundary,
            copper_separator_groups,
        ) = _copper_memberships(
            power_shapes,
            power_grid,
            top_copper_geometries,
            top_layer=top_layer,
        )
        copper_ground_groups, copper_ground_boundary, _ = _copper_memberships(
            ground_shapes,
            ground_grid,
            top_copper_geometries,
            top_layer=top_layer,
        )
    exact_power_edges = set(power_edges)
    copper_power_edges = _local_copper_edges(
        copper_power_groups, evidence, power_by_owner
    )
    power_edges.update(copper_power_edges)
    for index in power_conflicts | ground_conflicts:
        invalid.setdefault(index, []).append(
            "overlapping TOP pads carry conflicting source NET names"
        )
    for index in copper_power_boundary | copper_ground_boundary:
        invalid.setdefault(index, []).append(
            "terminal pad has only boundary contact or unresolved overlap with source TOP copper"
        )

    power_vias: dict[int, dict[str, SpdViaLanding]] = {
        index: {} for index in power_by_owner
    }
    ground_vias: dict[int, dict[str, SpdViaLanding]] = {
        index: {} for index in ground_by_owner
    }
    owners_by_net: dict[str, set[int]] = {}
    for owner, item in enumerate(evidence):
        owners_by_net.setdefault(item.power_net.casefold(), set()).add(owner)
        owners_by_net.setdefault(item.ground_net.casefold(), set()).add(owner)
    for endpoint_index, endpoint in enumerate(via_endpoints):
        via_shape, via_error = top_shape(endpoint.padstack)
        if via_error or via_shape is None:
            for owner in owners_by_net.get(endpoint.net.casefold(), ()):
                invalid.setdefault(owner, []).append(
                    via_error or "Via landing shape is unresolved"
                )
            continue
        placed_via = _placed(
            endpoint_index,
            "VIA",
            endpoint.net,
            endpoint.x_um,
            endpoint.y_um,
            endpoint.rotation_degrees,
            via_shape,
        )
        landing = SpdViaLanding(
            via_id=endpoint.via_id,
            net=endpoint.net,
            endpoint_node_id=endpoint.endpoint_node_id,
            x_um=endpoint.x_um,
            y_um=endpoint.y_um,
            padstack=endpoint.padstack,
            rotation_degrees=endpoint.rotation_degrees,
        )
        matches: list[tuple[Literal["PWR", "GND"], int]] = []
        for terminal, shapes, grid in (
            ("PWR", power_shapes, power_grid),
            ("GND", ground_shapes, ground_grid),
        ):
            for candidate_index in grid.query(placed_via.bbox):
                candidate = shapes[candidate_index]
                relation = _relation(candidate, placed_via)
                if relation == "none":
                    continue
                if candidate.net_key != placed_via.net_key:
                    invalid.setdefault(candidate.owner_index, []).append(
                        "a Via TOP pad with a conflicting NET overlaps this terminal"
                    )
                    continue
                if relation == "positive":
                    matches.append((terminal, candidate.owner_index))
                else:
                    invalid.setdefault(candidate.owner_index, []).append(
                        "Via and terminal pads have boundary-only contact"
                    )
        matched_terminals = {terminal for terminal, _owner in matches}
        if len(matched_terminals) > 1:
            for _terminal, owner in matches:
                invalid.setdefault(owner, []).append(
                    "one Via TOP pad overlaps both PWR and GND terminal copper"
                )
            continue
        matched_owners = sorted(
            {owner for _terminal, owner in matches},
            key=lambda owner: evidence[owner].refdes.casefold(),
        )
        if len(matched_owners) > 1:
            bridge_edges = power_edges if matches[0][0] == "PWR" else ground_edges
            first_owner = matched_owners[0]
            bridge_edges.update(
                (first_owner, owner) for owner in matched_owners[1:]
            )
        for terminal, owner in matches:
            target = power_vias if terminal == "PWR" else ground_vias
            evidence_key = (
                f"{landing.via_id.casefold()}::{landing.endpoint_node_id.casefold()}"
            )
            target[owner][evidence_key] = landing

    unresolved_boundary_edges = (power_boundary - power_edges) | (
        ground_boundary - ground_edges
    )
    for first, second in unresolved_boundary_edges:
        for index in (first, second):
            invalid.setdefault(index, []).append(
                "pad geometry has boundary-only contact; electrical short is not inferred"
            )
    uncertain_keys = {item.casefold() for item in uncertain_via_nets}
    for index, item in enumerate(evidence):
        if (
            item.power_net.casefold() in uncertain_keys
            or item.ground_net.casefold() in uncertain_keys
        ):
            invalid.setdefault(index, []).append(
                "a relevant Via padstack or endpoint could not be resolved exactly"
            )

    usable_indexes = set(power_by_owner) & set(ground_by_owner)
    # PWR copper defines the editable shorted-pad component.  Board-wide or
    # overlapping GND copper may validate that component, but must never merge
    # two otherwise separate PWR strips.
    graph_edges = {
        edge
        for edge in power_edges
        if edge[0] in usable_indexes and edge[1] in usable_indexes
    }
    graph_components = _components(count, graph_edges)
    component_by_owner = {
        owner: component_index
        for component_index, members in enumerate(graph_components)
        for owner in members & usable_indexes
    }
    copper_ground_edges = _local_copper_edges(
        copper_ground_groups,
        evidence,
        ground_by_owner,
        component_by_owner=component_by_owner,
    )
    ground_edges.update(copper_ground_edges)
    power_edges_by_component: list[set[tuple[int, int]]] = [
        set() for _members in graph_components
    ]
    ground_edges_by_component: list[set[tuple[int, int]]] = [
        set() for _members in graph_components
    ]
    for edges, buckets in (
        (power_edges, power_edges_by_component),
        (ground_edges, ground_edges_by_component),
    ):
        for edge in edges:
            component_index = component_by_owner.get(edge[0])
            if (
                component_index is not None
                and component_by_owner.get(edge[1]) == component_index
            ):
                buckets[component_index].add(edge)

    clusters: list[SpdSharedPadCluster] = []
    for component_index, members in enumerate(graph_components):
        active_members = members & usable_indexes
        if not active_members:
            continue
        refs = tuple(
            evidence[index].refdes
            for index in sorted(active_members, key=lambda value: evidence[value].refdes.casefold())
        )
        component_power_edges = power_edges_by_component[component_index]
        component_ground_edges = ground_edges_by_component[component_index]
        reasons: list[str] = []
        if not _connected(active_members, component_power_edges):
            reasons.append("PWR pads do not form one connected TOP supernode")
        power_nets = {evidence[index].power_net.casefold() for index in active_members}
        ground_nets = {evidence[index].ground_net.casefold() for index in active_members}
        if len(power_nets) != 1 or len(ground_nets) != 1:
            reasons.append("cluster members do not share one source PWR/GND NET pair")
        for index in active_members:
            reasons.extend(invalid.get(index, ()))

        anchors: list[int] = []
        dummies: list[int] = []
        for index in active_members:
            has_power = bool(power_vias.get(index))
            has_ground = bool(ground_vias.get(index))
            if has_power or has_ground:
                anchors.append(index)
            else:
                dummies.append(index)
        aggregate_power_vias = {
            key for index in active_members for key in power_vias.get(index, {})
        }
        aggregate_ground_vias = {
            key for index in active_members for key in ground_vias.get(index, {})
        }
        if aggregate_power_vias and aggregate_ground_vias:
            # V5 carries the source PWR and GND copper graphs separately.  A
            # shared PWR pad component can legitimately span multiple isolated
            # GND top supernodes; each capacitor will later map to exactly one
            # of those components.  Do not merge them, but fail closed when an
            # otherwise anchored cluster has a physical GND component with no
            # source-proven Via anchor.  A fully via-less cluster remains the
            # documented FLOATING state.
            remaining_ground = set(active_members)
            ground_components: list[set[int]] = []
            ground_adjacency = {index: set() for index in active_members}
            for first, second in component_ground_edges:
                ground_adjacency[first].add(second)
                ground_adjacency[second].add(first)
            while remaining_ground:
                first = min(
                    remaining_ground,
                    key=lambda value: evidence[value].refdes.casefold(),
                )
                pending = [first]
                component: set[int] = set()
                while pending:
                    current = pending.pop()
                    if current in component:
                        continue
                    component.add(current)
                    pending.extend(ground_adjacency[current] - component)
                remaining_ground.difference_update(component)
                ground_components.append(component)
            for component in ground_components:
                if any(ground_vias.get(index) for index in component):
                    continue
                member_names = ", ".join(
                    evidence[index].refdes
                    for index in sorted(
                        component, key=lambda value: evidence[value].refdes.casefold()
                    )
                )
                reasons.append(
                    "GND TOP component has no source Via anchor: " + member_names
                )
        if len(active_members) == 1:
            if bool(aggregate_power_vias) != bool(aggregate_ground_vias):
                reasons.append(
                    f"{refs[0]} has Via evidence on only one terminal"
                )
        elif bool(aggregate_power_vias) != bool(aggregate_ground_vias):
            reasons.append(
                "shared cluster has Via evidence on only one aggregate PWR/GND supernode"
            )
        reasons = list(dict.fromkeys(reasons))
        state: SharedPadClusterState
        if reasons:
            state = "UNRESOLVED"
        elif aggregate_power_vias and aggregate_ground_vias:
            state = "ANCHORED"
        else:
            state = "FLOATING"

        cluster_id = (
            _cluster_id(
                evidence,
                active_members,
                component_power_edges,
                component_ground_edges,
            )
            if len(active_members) > 1
            else None
        )
        for index in active_members:
            pwr = tuple(
                power_vias.get(index, {}).get(key)
                for key in sorted(power_vias.get(index, {}))
            )
            gnd = tuple(
                ground_vias.get(index, {}).get(key)
                for key in sorted(ground_vias.get(index, {}))
            )
            power_items = tuple(item for item in pwr if item is not None)
            ground_items = tuple(item for item in gnd if item is not None)
            if state == "UNRESOLVED":
                kind: DecapConnectionKind = "UNRESOLVED"
            elif index in anchors:
                kind = "SHARED_ANCHOR" if cluster_id else "DIRECT"
            else:
                kind = "SHARED_DUMMY" if state == "ANCHORED" else "FLOATING_DUMMY"
            connections[index] = SpdDecapConnection(
                refdes=evidence[index].refdes,
                kind=kind,
                cluster_id=cluster_id,
                power_vias=power_items,
                ground_vias=ground_items,
                reason="; ".join(reasons) if reasons else None,
            )

        if cluster_id is not None:
            ref_by_index = {index: evidence[index].refdes for index in active_members}

            def canonical_ref_edge(edge: tuple[int, int]) -> tuple[str, str]:
                left, right = ref_by_index[edge[0]], ref_by_index[edge[1]]
                return (
                    (left, right)
                    if left.casefold() <= right.casefold()
                    else (right, left)
                )

            def edge_key(edge: tuple[int, int]) -> tuple[str, str]:
                left, right = canonical_ref_edge(edge)
                return left.casefold(), right.casefold()
            isolation_gap_refdes: tuple[str, ...] = ()
            if state == "ANCHORED":
                for separator_group in copper_separator_groups:
                    if separator_group == active_members:
                        local_path = _minimum_distance_tree(
                            tuple(active_members), power_by_owner, evidence
                        )
                        if local_path == component_power_edges:
                            isolation_gap_refdes = refs
                        break
            clusters.append(
                SpdSharedPadCluster(
                    cluster_id=cluster_id,
                    state=state,
                    member_refdes=refs,
                    anchor_refdes=tuple(
                        evidence[index].refdes
                        for index in sorted(
                            anchors, key=lambda value: evidence[value].refdes.casefold()
                        )
                    ),
                    dummy_refdes=tuple(
                        evidence[index].refdes
                        for index in sorted(
                            dummies, key=lambda value: evidence[value].refdes.casefold()
                        )
                    ),
                    power_net=evidence[min(active_members)].power_net,
                    ground_net=evidence[min(active_members)].ground_net,
                    layer=top_layer,
                    power_edges=tuple(
                        canonical_ref_edge(edge)
                        for edge in sorted(component_power_edges, key=edge_key)
                    ),
                    ground_edges=tuple(
                        canonical_ref_edge(edge)
                        for edge in sorted(component_ground_edges, key=edge_key)
                    ),
                    isolation_gap_refdes=isolation_gap_refdes,
                    reason="; ".join(reasons) if reasons else None,
                )
            )

    for index, item in enumerate(evidence):
        if connections[index] is None:
            reasons = invalid.get(index, ["TOP pad connectivity could not be resolved"])
            connections[index] = SpdDecapConnection(
                item.refdes,
                "UNRESOLVED",
                reason="; ".join(dict.fromkeys(reasons)),
            )
    unresolved = sum(item is not None and item.kind == "UNRESOLVED" for item in connections)
    if unresolved:
        warnings.append(
            f"{unresolved} decap(s) have unresolved TOP pad/Via connectivity; "
            "rail reassignment and evaluation must fail closed for those parts"
        )
    return SharedPadExtraction(
        connections=tuple(item for item in connections if item is not None),
        clusters=tuple(sorted(clusters, key=lambda item: item.cluster_id)),
        warnings=tuple(warnings),
        source_copper_power_edges=len(power_edges - exact_power_edges),
        source_copper_ground_edges=len(ground_edges - exact_ground_edges),
        source_copper_member_count=len(
            {
                owner
                for edge in (power_edges - exact_power_edges)
                for owner in edge
            }
        ),
    )


__all__ = [
    "DecapConnectionKind",
    "DecapPadEvidence",
    "CopperPrimitiveKind",
    "PadShapeKind",
    "SharedPadClusterState",
    "SharedPadExtraction",
    "SpdDecapConnection",
    "SpdPadShape",
    "SpdSharedPadCluster",
    "SpdTopCopperGeometry",
    "SpdViaLanding",
    "ViaTopEndpoint",
    "extract_shared_pad_connectivity",
]
