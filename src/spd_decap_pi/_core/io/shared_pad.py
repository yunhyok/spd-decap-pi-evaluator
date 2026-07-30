"""Exact TOP-pad connectivity evidence extracted from an SPD source.

The extractor intentionally uses only source-backed geometry: layer-specific
padstack copper, terminal coordinates, and Via endpoint nodes.  REFDES suffixes
and nearest-neighbour distances are never electrical evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import cos, floor, hypot, radians, sin
from statistics import median
from typing import Literal, Mapping, Sequence


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


def _point_in_primitive(
    shape: _PlacedPad,
    kind: CopperPrimitiveKind,
    primitive: Sequence[tuple[float, float]] | tuple[float, float, float],
) -> Literal["positive", "boundary", "none"]:
    if kind.endswith("circle"):
        center_x, center_y, radius = primitive  # type: ignore[misc]
        margin = radius - hypot(shape.x_um - center_x, shape.y_um - center_y)
        if margin > _GEOMETRY_EPS_UM:
            return "positive"
        if margin >= -_GEOMETRY_EPS_UM:
            return "boundary"
        return "none"
    return _point_in_polygon(
        shape.x_um,
        shape.y_um,
        primitive,  # type: ignore[arg-type]
    )


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


def _copper_memberships(
    shapes: Sequence[_PlacedPad],
    grid: _ShapeGrid,
    geometries: Sequence[SpdTopCopperGeometry],
    *,
    top_layer: str,
) -> tuple[tuple[set[int], ...], set[int], tuple[set[int], ...]]:
    """Return source-positive primitive memberships after boolean fill replay."""

    groups: list[set[int]] = []
    separator_groups: list[set[int]] = []
    boundary_owners: set[int] = set()
    shape_net_keys = {shape.net_key for shape in shapes}
    for geometry in geometries:
        if geometry.layer.casefold() != top_layer.casefold():
            continue
        net_key = geometry.net.casefold()
        if net_key not in shape_net_keys:
            continue
        filled: dict[int, bool] = {}
        local_groups: list[
            tuple[
                CopperPrimitiveKind,
                Sequence[tuple[float, float]] | tuple[float, float, float],
                set[int],
            ]
        ] = []
        for kind, primitive in _ordered_copper_primitives(geometry):
            bounds = _primitive_bounds(kind, primitive)
            members: set[int] = set()
            for candidate_index in grid.query(bounds):
                candidate = shapes[candidate_index]
                if candidate.net_key != net_key:
                    continue
                relation = _point_in_primitive(candidate, kind, primitive)
                if relation == "boundary":
                    boundary_owners.add(candidate.owner_index)
                    continue
                if relation != "positive":
                    continue
                is_positive = kind.startswith("positive_")
                filled[candidate.owner_index] = is_positive
                if is_positive:
                    members.add(candidate.owner_index)
            if kind.startswith("positive_") and members:
                local_groups.append((kind, primitive, members))
        final_members = {
            owner for owner, is_filled in filled.items() if is_filled
        } - boundary_owners
        for kind, primitive, members in local_groups:
            retained = members & final_members
            if len(retained) < 2:
                continue
            groups.append(retained)
            if (
                kind == "positive_polygon"
                and not geometry.negative_polygons_um
                and not geometry.positive_circles_um
                and not geometry.negative_circles_um
                and _axis_aligned_rectangle(primitive)
                and _collinear_pad_centers(retained, shapes)
            ):
                separator_groups.append(retained)
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
            "terminal center lies on a source TOP copper boundary"
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
        if not _connected(active_members, component_ground_edges):
            reasons.append("GND pads do not form one connected TOP supernode")
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
