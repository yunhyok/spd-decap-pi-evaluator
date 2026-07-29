"""Exact, fail-closed PWR-plane eligibility for decap reassignment.

The modal solver may approximate a non-rectangular rail with a bounding box,
but assignment eligibility deliberately uses the normalized PowerSI boolean
primitives at the physical PWR pad coordinate.  A point on a primitive boundary
is rejected because floating-point tolerance must never make a marginal pad
silently assignable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import hypot, isfinite
from typing import Literal

from spd_decap_pi._core.domain import PlanePairSuggestion, StackupLayer
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


@dataclass(frozen=True, slots=True)
class _IndexedPlane:
    geometry: SpdPlaneGeometry
    primitives: tuple[_IndexedPrimitive, ...]
    positive_bounds: tuple[float, float, float, float]
    pair: PlanePairSuggestion


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
    return (
        x_min - tolerance_um <= x_um <= x_max + tolerance_um
        and y_min - tolerance_um <= y_um <= y_max + tolerance_um
    )


def _ordered_primitives(geometry: SpdPlaneGeometry) -> tuple[_IndexedPrimitive, ...]:
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
            xs = [point[0] for point in primitive]
            ys = [point[1] for point in primitive]
            bounds = (min(xs), max(xs), min(ys), max(ys))
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
        else:
            raise ValueError(f"unsupported plane primitive kind {kind!r}")
        result.append(_IndexedPrimitive(kind, primitive, bounds))
    return tuple(result)


class PlaneEligibilityIndex:
    """Reusable bbox index for thousands of decap PWR-pad queries."""

    def __init__(
        self,
        plane_geometries: Iterable[SpdPlaneGeometry],
        stackup_layers: Iterable[StackupLayer],
        *,
        gnd_aliases: Iterable[str] = ("DGND", "GND"),
        tolerance_um: float = 1.0e-6,
    ) -> None:
        if not isfinite(tolerance_um) or tolerance_um < 0:
            raise ValueError("tolerance_um must be finite and non-negative")
        self.tolerance_um = tolerance_um
        stack = tuple(stackup_layers)
        aliases = tuple(gnd_aliases)
        pair_cache: dict[str, tuple[PlanePairSuggestion, ...]] = {}
        indexed: list[_IndexedPlane] = []
        for geometry in plane_geometries:
            net_key = geometry.net.casefold()
            pairs = pair_cache.setdefault(
                net_key,
                tuple(
                    suggest_effective_plane_pairs(
                        stack, rail_net=geometry.net, gnd_aliases=aliases
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
            primitives = _ordered_primitives(geometry)
            positive = [item.bounds for item in primitives if item.kind.startswith("positive_")]
            if not positive:
                continue
            indexed.append(
                _IndexedPlane(
                    geometry=geometry,
                    primitives=primitives,
                    positive_bounds=(
                        min(item[0] for item in positive),
                        max(item[1] for item in positive),
                        min(item[2] for item in positive),
                        max(item[3] for item in positive),
                    ),
                    pair=pair,
                )
            )
        self._planes = tuple(indexed)

    def query(self, x_um: float, y_um: float) -> EligibilityResult:
        if not isfinite(x_um) or not isfinite(y_um):
            raise ValueError("plane query coordinates must be finite")
        eligible: dict[tuple[str, str, str], EligiblePlane] = {}
        boundary: set[tuple[str, str, str]] = set()
        for indexed in self._planes:
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
            for item in indexed.primitives:
                if not _bounds_contains(item.bounds, x_um, y_um, self.tolerance_um):
                    continue
                if item.kind.endswith("polygon"):
                    containment = point_in_polygon(
                        x_um,
                        y_um,
                        item.primitive,  # type: ignore[arg-type]
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
        boundary_names = sorted(
            {
                indexed.geometry.net
                for indexed in self._planes
                if (
                    indexed.geometry.net.casefold(),
                    indexed.pair.pwr_layer.casefold(),
                    indexed.pair.gnd_layer.casefold(),
                )
                in boundary
            },
            key=str.casefold,
        )
        return EligibilityResult(tuple(ordered), tuple(boundary_names))


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
    "PlaneEligibilityIndex",
    "eligible_power_planes",
    "point_in_plane_geometry",
    "point_in_polygon",
]
