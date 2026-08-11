"""Dormant exact-source geometry for straight constant-width SPD Traces.

This module is deliberately not connected to Evaluation.  It constructs only
the source geometry that can be proved from an exact Trace record and exact
integer-picometre Node coordinates.  No width, end-cap, connectivity, or hole
behavior is inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isfinite
import re
from typing import Any


SOURCE_TRACE_GEOMETRY_POLICY = (
    "source-trace-straight-constant-width-flat-v1"
)
SOURCE_TRACE_GEOMETRY_COMPILER = "source-trace-geometry/v1"
MAX_SOURCE_GEOMETRY_OWNERS = 4096
MAX_SOURCE_GEOMETRY_COORDINATES = 1_000_000
MAX_SOURCE_GEOMETRY_CONTACT_CHECKS = 1_000_000


class SourceTraceGeometryError(ValueError):
    """Fail-closed source geometry or connectivity error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(name: str, value: object) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must not be blank")
    return result


def _integer_pm(name: str, value: object, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an exact integer number of picometres")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _digest(name: str, value: object) -> str:
    result = str(value).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", result) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def _wkb_hex(geometry: Any) -> str:
    from shapely import to_wkb

    return str(
        to_wkb(
            geometry,
            hex=True,
            byte_order=1,
            output_dimension=2,
            include_srid=False,
        )
    ).lower()


def _geometry_from_wkb_hex(value: str) -> Any:
    from shapely import from_wkb

    return from_wkb(bytes.fromhex(value))


def _physical_polygonal_geometry(value: Any) -> bool:
    try:
        return (
            value.geom_type in {"Polygon", "MultiPolygon"}
            and not value.is_empty
            and value.is_valid
            and isfinite(float(value.area))
            and float(value.area) > 0.0
        )
    except (AttributeError, TypeError, ValueError, ArithmeticError):
        return False


@dataclass(frozen=True, slots=True)
class SourceTraceSegment:
    """Immutable source Trace segment with exact integer-pm scalar evidence."""

    source_id: str
    net: str
    layer: str
    starting_node_id: str
    ending_node_id: str
    start_x_pm: int
    start_y_pm: int
    end_x_pm: int
    end_y_pm: int
    width_pm: int | None
    source_record_sha256: str
    source_record_size_bytes: int
    source_record_line_count: int = 1
    geometry_policy: str = SOURCE_TRACE_GEOMETRY_POLICY

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "net",
            "layer",
            "starting_node_id",
            "ending_node_id",
        ):
            object.__setattr__(self, name, _clean_text(name, getattr(self, name)))
        for name in ("start_x_pm", "start_y_pm", "end_x_pm", "end_y_pm"):
            object.__setattr__(self, name, _integer_pm(name, getattr(self, name)))
        if (self.start_x_pm, self.start_y_pm) == (self.end_x_pm, self.end_y_pm):
            raise ValueError("source Trace endpoints must be distinct")
        if self.width_pm is not None:
            object.__setattr__(
                self,
                "width_pm",
                _integer_pm("width_pm", self.width_pm, positive=True),
            )
        object.__setattr__(
            self,
            "source_record_sha256",
            _digest("source_record_sha256", self.source_record_sha256),
        )
        if (
            isinstance(self.source_record_size_bytes, bool)
            or not isinstance(self.source_record_size_bytes, int)
            or self.source_record_size_bytes <= 0
        ):
            raise ValueError("source_record_size_bytes must be a positive integer")
        if (
            isinstance(self.source_record_line_count, bool)
            or not isinstance(self.source_record_line_count, int)
            or self.source_record_line_count <= 0
        ):
            raise ValueError("source_record_line_count must be a positive integer")
        if self.geometry_policy != SOURCE_TRACE_GEOMETRY_POLICY:
            raise ValueError("source Trace geometry policy is not supported")

    @property
    def identity_sha256(self) -> str:
        return _canonical_sha256(
            {
                "compiler": SOURCE_TRACE_GEOMETRY_COMPILER,
                "geometry_policy": self.geometry_policy,
                "source_id": self.source_id,
                "net": self.net,
                "layer": self.layer,
                "starting_node_id": self.starting_node_id,
                "ending_node_id": self.ending_node_id,
                "start_pm": [self.start_x_pm, self.start_y_pm],
                "end_pm": [self.end_x_pm, self.end_y_pm],
                "width_pm": self.width_pm,
                "source_record_sha256": self.source_record_sha256,
                "source_record_size_bytes": self.source_record_size_bytes,
                "source_record_line_count": self.source_record_line_count,
            }
        )

    def verify_source_record(self, exact_bytes: bytes) -> None:
        if not isinstance(exact_bytes, bytes):
            raise ValueError("exact source record must be bytes")
        if len(exact_bytes) != self.source_record_size_bytes:
            raise ValueError("source Trace record byte cardinality changed")
        if sha256(exact_bytes).hexdigest() != self.source_record_sha256:
            raise ValueError("source Trace record digest changed")

    def rectangle_coordinates_pm(
        self,
    ) -> tuple[tuple[float, float], ...] | None:
        """Return deterministic flat-ended rectangle corners in pm."""

        if self.width_pm is None:
            return None
        dx = self.end_x_pm - self.start_x_pm
        dy = self.end_y_pm - self.start_y_pm
        try:
            length = hypot(dx, dy)
        except (OverflowError, ValueError) as exc:
            raise SourceTraceGeometryError(
                "TRACE_VECTOR_INVALID",
                f"Trace {self.source_id!r} has an invalid endpoint vector",
            ) from exc
        if not isfinite(length) or length <= 0.0:
            raise SourceTraceGeometryError(
                "TRACE_VECTOR_INVALID",
                f"Trace {self.source_id!r} has an invalid endpoint vector",
            )
        try:
            offset_x = -dy * self.width_pm / (2.0 * length)
            offset_y = dx * self.width_pm / (2.0 * length)
            result = (
                (self.start_x_pm + offset_x, self.start_y_pm + offset_y),
                (self.end_x_pm + offset_x, self.end_y_pm + offset_y),
                (self.end_x_pm - offset_x, self.end_y_pm - offset_y),
                (self.start_x_pm - offset_x, self.start_y_pm - offset_y),
            )
        except (OverflowError, ValueError) as exc:
            raise SourceTraceGeometryError(
                "TRACE_RECTANGLE_NONFINITE",
                f"Trace {self.source_id!r} rectangle is non-finite",
            ) from exc
        if not all(isfinite(value) for point in result for value in point):
            raise SourceTraceGeometryError(
                "TRACE_RECTANGLE_NONFINITE",
                f"Trace {self.source_id!r} rectangle is non-finite",
            )
        return result

    def geometry(self) -> Any | None:
        coordinates = self.rectangle_coordinates_pm()
        if coordinates is None:
            return None
        from shapely.geometry import Polygon

        try:
            result = Polygon(coordinates)
        except (OverflowError, TypeError, ValueError) as exc:
            raise SourceTraceGeometryError(
                "TRACE_RECTANGLE_INVALID",
                f"Trace {self.source_id!r} does not form a physical rectangle",
            ) from exc
        if not _physical_polygonal_geometry(result):
            raise SourceTraceGeometryError(
                "TRACE_RECTANGLE_INVALID",
                f"Trace {self.source_id!r} does not form a physical rectangle",
            )
        return result


@dataclass(frozen=True, slots=True)
class SourceArtworkIsland:
    """Immutable exact artwork island used only for contact validation."""

    island_id: str
    net: str
    layer: str
    geometry_wkb_hex: str
    source_sha256: str

    def __post_init__(self) -> None:
        for name in ("island_id", "net", "layer"):
            object.__setattr__(self, name, _clean_text(name, getattr(self, name)))
        geometry_wkb_hex = str(self.geometry_wkb_hex).strip().lower()
        if not geometry_wkb_hex or re.fullmatch(r"[0-9a-f]+", geometry_wkb_hex) is None:
            raise ValueError("artwork geometry WKB must be non-empty hexadecimal")
        try:
            geometry = _geometry_from_wkb_hex(geometry_wkb_hex)
        except (ValueError, TypeError) as exc:
            raise ValueError("artwork geometry WKB is invalid") from exc
        if not _physical_polygonal_geometry(geometry):
            raise ValueError("artwork island must be valid positive-area polygonal geometry")
        object.__setattr__(self, "geometry_wkb_hex", _wkb_hex(geometry))
        object.__setattr__(
            self,
            "source_sha256",
            _digest("source_sha256", self.source_sha256),
        )

    @classmethod
    def from_geometry(
        cls,
        island_id: str,
        net: str,
        layer: str,
        geometry: Any,
        *,
        source_sha256: str | None = None,
    ) -> SourceArtworkIsland:
        if not _physical_polygonal_geometry(geometry):
            raise ValueError("artwork island must be valid positive-area polygonal geometry")
        wkb_hex = _wkb_hex(geometry)
        source_digest = source_sha256 or sha256(bytes.fromhex(wkb_hex)).hexdigest()
        return cls(island_id, net, layer, wkb_hex, source_digest)

    @property
    def geometry(self) -> Any:
        return _geometry_from_wkb_hex(self.geometry_wkb_hex)

    @property
    def identity_sha256(self) -> str:
        return _canonical_sha256(
            {
                "compiler": SOURCE_TRACE_GEOMETRY_COMPILER,
                "island_id": self.island_id,
                "net": self.net,
                "layer": self.layer,
                "geometry_wkb_hex": self.geometry_wkb_hex,
                "source_sha256": self.source_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class CompiledSourceTraceGeometry:
    """One deterministic same-NET/layer source geometry union."""

    net: str
    layer: str
    geometry_wkb_hex: str | None
    trace_identity_sha256s: tuple[str, ...]
    topology_only_trace_identity_sha256s: tuple[str, ...]
    artwork_identity_sha256s: tuple[str, ...]
    compiler: str
    geometry_policy: str
    identity_sha256: str

    def __post_init__(self) -> None:
        net = _clean_text("net", self.net)
        layer = _clean_text("layer", self.layer)
        if self.compiler != SOURCE_TRACE_GEOMETRY_COMPILER:
            raise ValueError("source Trace geometry compiler is not supported")
        if self.geometry_policy != SOURCE_TRACE_GEOMETRY_POLICY:
            raise ValueError("source Trace geometry policy is not supported")
        geometry_wkb_hex = self.geometry_wkb_hex
        if geometry_wkb_hex is not None:
            geometry = _geometry_from_wkb_hex(geometry_wkb_hex)
            if not _physical_polygonal_geometry(geometry):
                raise ValueError("compiled source Trace geometry is invalid")
            geometry_wkb_hex = _wkb_hex(geometry)
        tuple_fields = (
            "trace_identity_sha256s",
            "topology_only_trace_identity_sha256s",
            "artwork_identity_sha256s",
        )
        normalized_hashes: dict[str, tuple[str, ...]] = {}
        for name in tuple_fields:
            values = tuple(_digest(name, item) for item in getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
            normalized_hashes[name] = values
        if not set(normalized_hashes["trace_identity_sha256s"]).isdisjoint(
            normalized_hashes["topology_only_trace_identity_sha256s"]
        ):
            raise ValueError("resolved and topology-only Trace identities overlap")
        if geometry_wkb_hex is None and (
            normalized_hashes["trace_identity_sha256s"]
            or normalized_hashes["artwork_identity_sha256s"]
        ):
            raise ValueError("compiled physical owner identities require geometry")
        if geometry_wkb_hex is not None and not (
            normalized_hashes["trace_identity_sha256s"]
            or normalized_hashes["artwork_identity_sha256s"]
        ):
            raise ValueError("compiled geometry has no physical source owner")
        payload = {
            "compiler": self.compiler,
            "geometry_policy": self.geometry_policy,
            "net": net,
            "layer": layer,
            "geometry_wkb_hex": geometry_wkb_hex,
            "trace_identity_sha256s": normalized_hashes["trace_identity_sha256s"],
            "topology_only_trace_identity_sha256s": (
                normalized_hashes["topology_only_trace_identity_sha256s"]
            ),
            "artwork_identity_sha256s": normalized_hashes["artwork_identity_sha256s"],
        }
        if _canonical_sha256(payload) != _digest(
            "identity_sha256",
            self.identity_sha256,
        ):
            raise ValueError("compiled source Trace geometry identity was tampered")
        object.__setattr__(self, "net", net)
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "geometry_wkb_hex", geometry_wkb_hex)
        for name, values in normalized_hashes.items():
            object.__setattr__(self, name, values)

    @property
    def geometry(self) -> Any | None:
        if self.geometry_wkb_hex is None:
            return None
        return _geometry_from_wkb_hex(self.geometry_wkb_hex)


@dataclass(frozen=True, slots=True)
class _Owner:
    owner_id: str
    kind: str
    net: str
    layer: str
    identity_sha256: str
    geometry: Any


def _intersection_dimension(intersection: Any) -> int:
    if intersection.is_empty:
        return -1
    if float(intersection.area) > 0.0:
        return 2
    if float(intersection.length) > 0.0:
        return 1
    return 0


def _validate_contacts(owners: tuple[_Owner, ...]) -> None:
    from shapely import STRtree
    from shapely.errors import GEOSException

    by_layer: dict[str, list[_Owner]] = {}
    for owner in owners:
        by_layer.setdefault(owner.layer.casefold(), []).append(owner)
    checks = 0
    for layer_key in sorted(by_layer):
        layer_owners = tuple(by_layer[layer_key])
        tree = STRtree([item.geometry for item in layer_owners])
        for index, first in enumerate(layer_owners):
            candidates = sorted(int(item) for item in tree.query(first.geometry))
            for second_index in candidates:
                if second_index <= index:
                    continue
                checks += 1
                if checks > MAX_SOURCE_GEOMETRY_CONTACT_CHECKS:
                    raise SourceTraceGeometryError(
                        "SOURCE_GEOMETRY_CONTACT_BOUND_EXCEEDED",
                        "source geometry candidate-contact checks exceed "
                        f"{MAX_SOURCE_GEOMETRY_CONTACT_CHECKS}",
                    )
                second = layer_owners[second_index]
                try:
                    intersection = first.geometry.intersection(second.geometry)
                except GEOSException as exc:
                    raise SourceTraceGeometryError(
                        "SOURCE_GEOMETRY_INTERSECTION_FAILED",
                        f"source geometry intersection failed for {first.owner_id!r} "
                        f"and {second.owner_id!r}",
                    ) from exc
                if intersection.is_empty:
                    continue
                if first.net.casefold() != second.net.casefold():
                    raise SourceTraceGeometryError(
                        "CROSS_NET_GEOMETRY_CONTACT",
                        f"{first.kind} {first.owner_id!r} on {first.net!r} contacts "
                        f"{second.kind} {second.owner_id!r} on {second.net!r}",
                    )
                if _intersection_dimension(intersection) == 0:
                    raise SourceTraceGeometryError(
                        "POINT_ONLY_GEOMETRY_CONTACT",
                        f"{first.kind} {first.owner_id!r} and {second.kind} "
                        f"{second.owner_id!r} have only point contact",
                    )


def compile_source_trace_geometry(
    segments: tuple[SourceTraceSegment, ...],
    *,
    artwork_islands: tuple[SourceArtworkIsland, ...] = (),
) -> tuple[CompiledSourceTraceGeometry, ...]:
    """Validate and union bounded source Trace/artwork geometry by NET/layer."""

    if not all(isinstance(item, SourceTraceSegment) for item in segments):
        raise TypeError("segments must contain only SourceTraceSegment values")
    if not all(isinstance(item, SourceArtworkIsland) for item in artwork_islands):
        raise TypeError("artwork_islands must contain only SourceArtworkIsland values")
    if len(segments) + len(artwork_islands) > MAX_SOURCE_GEOMETRY_OWNERS:
        raise SourceTraceGeometryError(
            "SOURCE_GEOMETRY_BOUND_EXCEEDED",
            f"source geometry owner count exceeds {MAX_SOURCE_GEOMETRY_OWNERS}",
        )

    seen_trace_ids: set[tuple[str, str]] = set()
    owners: list[_Owner] = []
    topology_only: dict[tuple[str, str], list[str]] = {}
    trace_identities: dict[tuple[str, str], list[str]] = {}
    artwork_identities: dict[tuple[str, str], list[str]] = {}
    display_names: dict[tuple[str, str], tuple[str, str]] = {}
    for segment in segments:
        source_key = (segment.net.casefold(), segment.source_id.casefold())
        if source_key in seen_trace_ids:
            raise SourceTraceGeometryError(
                "DUPLICATE_SOURCE_TRACE_ID",
                f"duplicate source Trace ID {segment.source_id!r} on {segment.net!r}",
            )
        seen_trace_ids.add(source_key)
        key = (segment.net.casefold(), segment.layer.casefold())
        display_names.setdefault(key, (segment.net, segment.layer))
        geometry = segment.geometry()
        if geometry is None:
            topology_only.setdefault(key, []).append(segment.identity_sha256)
            continue
        trace_identities.setdefault(key, []).append(segment.identity_sha256)
        owners.append(
            _Owner(
                segment.source_id,
                "trace",
                segment.net,
                segment.layer,
                segment.identity_sha256,
                geometry,
            )
        )
    for island in artwork_islands:
        key = (island.net.casefold(), island.layer.casefold())
        display_names.setdefault(key, (island.net, island.layer))
        artwork_identities.setdefault(key, []).append(island.identity_sha256)
        owners.append(
            _Owner(
                island.island_id,
                "artwork",
                island.net,
                island.layer,
                island.identity_sha256,
                island.geometry,
            )
        )

    ordered_owners = tuple(
        sorted(
            owners,
            key=lambda item: (
                item.layer.casefold(),
                item.net.casefold(),
                item.kind,
                item.owner_id.casefold(),
                item.identity_sha256,
            ),
        )
    )
    from shapely import get_num_coordinates

    coordinate_count = sum(
        int(get_num_coordinates(item.geometry)) for item in ordered_owners
    )
    if coordinate_count > MAX_SOURCE_GEOMETRY_COORDINATES:
        raise SourceTraceGeometryError(
            "SOURCE_GEOMETRY_COORDINATE_BOUND_EXCEEDED",
            f"source geometry coordinate count exceeds {MAX_SOURCE_GEOMETRY_COORDINATES}",
        )
    _validate_contacts(ordered_owners)

    from shapely.ops import unary_union

    result: list[CompiledSourceTraceGeometry] = []
    for key in sorted(display_names):
        net, layer = display_names[key]
        group_owners = tuple(
            item
            for item in ordered_owners
            if item.net.casefold() == key[0] and item.layer.casefold() == key[1]
        )
        if group_owners:
            geometry = unary_union([item.geometry for item in group_owners])
            if not _physical_polygonal_geometry(geometry):
                raise SourceTraceGeometryError(
                    "SOURCE_GEOMETRY_UNION_INVALID",
                    f"source geometry union for {net!r} on {layer!r} is invalid",
                )
            geometry_wkb_hex: str | None = _wkb_hex(geometry)
        else:
            geometry_wkb_hex = None
        trace_hashes = tuple(sorted(trace_identities.get(key, ())))
        topology_hashes = tuple(sorted(topology_only.get(key, ())))
        artwork_hashes = tuple(sorted(artwork_identities.get(key, ())))
        identity_payload = {
            "compiler": SOURCE_TRACE_GEOMETRY_COMPILER,
            "geometry_policy": SOURCE_TRACE_GEOMETRY_POLICY,
            "net": net,
            "layer": layer,
            "geometry_wkb_hex": geometry_wkb_hex,
            "trace_identity_sha256s": trace_hashes,
            "topology_only_trace_identity_sha256s": topology_hashes,
            "artwork_identity_sha256s": artwork_hashes,
        }
        result.append(
            CompiledSourceTraceGeometry(
                net=net,
                layer=layer,
                geometry_wkb_hex=geometry_wkb_hex,
                trace_identity_sha256s=trace_hashes,
                topology_only_trace_identity_sha256s=topology_hashes,
                artwork_identity_sha256s=artwork_hashes,
                compiler=SOURCE_TRACE_GEOMETRY_COMPILER,
                geometry_policy=SOURCE_TRACE_GEOMETRY_POLICY,
                identity_sha256=_canonical_sha256(identity_payload),
            )
        )
    return tuple(result)


__all__ = [
    "CompiledSourceTraceGeometry",
    "MAX_SOURCE_GEOMETRY_CONTACT_CHECKS",
    "MAX_SOURCE_GEOMETRY_COORDINATES",
    "MAX_SOURCE_GEOMETRY_OWNERS",
    "SOURCE_TRACE_GEOMETRY_COMPILER",
    "SOURCE_TRACE_GEOMETRY_POLICY",
    "SourceArtworkIsland",
    "SourceTraceGeometryError",
    "SourceTraceSegment",
    "compile_source_trace_geometry",
]
