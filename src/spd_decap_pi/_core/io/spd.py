"""Memory-mapped Cadence PowerSI SPD import.

The format is line-oriented but production files are commonly hundreds of
megabytes.  This module therefore indexes byte ranges and only decodes records
that can contribute to the PI model.  In particular, multi-port circuit bodies
are skipped and the Node section is parsed only for IDs referenced by selected
device/capacitor connections.
"""

from __future__ import annotations

from array import array
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
import hashlib
from math import isfinite, log10
import mmap
from pathlib import Path
import re
from typing import Literal

from ..domain import MLOOutline, PinKind, PinRecord, StackupLayer, TerminalKind
from ..models.spice import PassiveSubcircuitModel, SpiceModelError, parse_passive_subcircuit
from .shared_pad import (
    DecapPadEvidence,
    SpdDecapConnection,
    SpdPadShape,
    SpdSharedPadCluster,
    SpdTopCopperGeometry,
    ViaTopEndpoint,
    extract_shared_pad_connectivity,
)


class SpdImportError(ValueError):
    """Raised when an SPD source cannot be opened or analysis is cancelled."""


@dataclass(frozen=True, slots=True)
class SpdDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SpdSourceInfo:
    path: Path
    name: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    title: str


@dataclass(frozen=True, slots=True)
class SpdPadStack:
    name: str
    drill_diameter_um: float | None
    pad_width_um: float | None
    pad_height_um: float | None
    layers: tuple[str, ...]
    pad_shapes: tuple[SpdPadShape, ...] = ()

    @property
    def pad_diameter_um(self) -> float | None:
        values = [item for item in (self.pad_width_um, self.pad_height_um) if item is not None]
        return max(values) if values else None


@dataclass(frozen=True, slots=True)
class SpdCapInstance:
    refdes: str
    model_id: str | None
    footprint: str
    power_net: str
    ground_net: str
    site: str | None
    mounted: bool
    x_um: float
    y_um: float
    power_pin_id: str
    ground_pin_id: str
    source_part_name: str | None = None
    start_layer: str | None = None
    attach_layer: str | None = None
    power_pad_x_um: float | None = None
    power_pad_y_um: float | None = None
    ground_pad_x_um: float | None = None
    ground_pad_y_um: float | None = None
    power_padstack: str | None = None
    ground_padstack: str | None = None
    power_pad_rotation_degrees: float = 0.0
    ground_pad_rotation_degrees: float = 0.0
    power_pad_rotation_valid: bool = True
    ground_pad_rotation_valid: bool = True

    @property
    def power_pin(self) -> str:
        return self.power_pin_id.partition(":")[2] or self.power_pin_id

    @property
    def ground_pin(self) -> str:
        return self.ground_pin_id.partition(":")[2] or self.ground_pin_id

    @property
    def power_x_um(self) -> float:
        return self.x_um if self.power_pad_x_um is None else self.power_pad_x_um

    @property
    def power_y_um(self) -> float:
        return self.y_um if self.power_pad_y_um is None else self.power_pad_y_um

    @property
    def geometry_present(self) -> bool:
        # Instances are filtered against selected positive-plane nets before
        # they are materialized.
        return True


@dataclass(frozen=True, slots=True)
class SpdViaUsage:
    net: str
    padstack: str
    count: int


@dataclass(frozen=True, slots=True)
class SpdViaPathSegment:
    """One uniquely traversed source Via segment in a recovered vertical path."""

    via_id: str
    padstack: str
    drill_diameter_um: float
    start_layer: str
    end_layer: str
    length_um: float
    end_x_um: float
    end_y_um: float
    rotation_degrees: float = 0.0


@dataclass(frozen=True, slots=True)
class SpdViaPathEvidence:
    """Compact source-proven TOP-to-target-plane path evidence."""

    via_id: str
    target_layer: str
    target_node_id: str
    target_padstack: str
    target_pad_kind: str
    target_pad_width_um: float
    target_pad_height_um: float
    target_x_um: float
    target_y_um: float
    segments: tuple[SpdViaPathSegment, ...]


@dataclass(frozen=True, slots=True)
class SpdViaPathRecovery:
    """Ephemeral recovery output; the selected raw graph is never persisted."""

    evidence_by_via: Mapping[str, tuple[SpdViaPathEvidence, ...]]
    diagnostics: tuple[SpdDiagnostic, ...]
    statistics: Mapping[str, int]

    def evidence_for(
        self, via_id: str, target_layer: str
    ) -> SpdViaPathEvidence | None:
        target_key = target_layer.casefold()
        return next(
            (
                item
                for item in self.evidence_by_via.get(via_id.casefold(), ())
                if item.target_layer.casefold() == target_key
            ),
            None,
        )


class _SpdPolygon(Sequence[tuple[float, float]]):
    """Compact immutable X/Y sequence for multi-million-vertex SPD imports."""

    __slots__ = ("_bbox", "_coordinates")

    def __init__(self, flat_coordinates_um: Iterable[float]) -> None:
        coordinates = array("d", flat_coordinates_um)
        if len(coordinates) < 6 or len(coordinates) % 2:
            raise ValueError("SPD polygon requires at least three X/Y pairs")
        self._coordinates = coordinates
        x_values = coordinates[0::2]
        y_values = coordinates[1::2]
        self._bbox = (
            min(x_values),
            max(x_values),
            min(y_values),
            max(y_values),
        )

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self._bbox

    def __len__(self) -> int:
        return len(self._coordinates) // 2

    def __iter__(self) -> Iterator[tuple[float, float]]:
        coordinates = self._coordinates
        for offset in range(0, len(coordinates), 2):
            yield coordinates[offset], coordinates[offset + 1]

    def __getitem__(
        self, index: int | slice
    ) -> tuple[float, float] | tuple[tuple[float, float], ...]:
        if isinstance(index, slice):
            return tuple(self[offset] for offset in range(*index.indices(len(self))))
        normalized = index if index >= 0 else len(self) + index
        if normalized < 0 or normalized >= len(self):
            raise IndexError(index)
        offset = normalized * 2
        return self._coordinates[offset], self._coordinates[offset + 1]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence):
            return tuple(self) == tuple(other)
        return NotImplemented

    def __repr__(self) -> str:
        return repr(tuple(self))


@dataclass(frozen=True, slots=True)
class SpdPlaneGeometry:
    """PowerSI boolean plane primitives for one layer/net assignment."""

    layer: str
    net: str
    positive_polygons_um: tuple[Sequence[tuple[float, float]], ...]
    negative_polygons_um: tuple[Sequence[tuple[float, float]], ...]
    positive_circles_um: tuple[tuple[float, float, float], ...] = ()
    negative_circles_um: tuple[tuple[float, float, float], ...] = ()
    primitive_order: tuple[tuple[str, int], ...] = ()
    positive_subelement_count: int = 0
    negative_subelement_count: int = 0
    polygon_trace_count: int = 0
    box_count: int = 0


@dataclass(frozen=True, slots=True)
class SpdAnalysis:
    source: SpdSourceInfo
    outline: MLOOutline | None
    stackup_layers: tuple[StackupLayer, ...]
    pins: tuple[PinRecord, ...]
    cap_models: dict[str, PassiveSubcircuitModel]
    cap_instances: tuple[SpdCapInstance, ...]
    padstacks: tuple[SpdPadStack, ...]
    via_usage: tuple[SpdViaUsage, ...]
    diagnostics: tuple[SpdDiagnostic, ...]
    model_assets: dict[str, str]
    counts: dict[str, int]
    power_plane_nets: tuple[str, ...] = ()
    ground_nets: tuple[str, ...] = ()
    plane_geometries: tuple[SpdPlaneGeometry, ...] = ()
    decap_connections: tuple[SpdDecapConnection, ...] = ()
    shared_pad_clusters: tuple[SpdSharedPadCluster, ...] = ()

    @property
    def partial_models(self) -> dict[str, PassiveSubcircuitModel]:
        """Compatibility name used by the SPD-to-project service."""

        return self.cap_models

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)

    @property
    def summary_lines(self) -> tuple[str, ...]:
        return (
            f"Source: {self.source.name} ({self.source.size_bytes:,} bytes)",
            f"Stack-up: {len(self.stackup_layers)} layers; outline: "
            + (
                f"{self.outline.width_um:g} x {self.outline.height_um:g} um"
                if self.outline is not None
                else "not found"
            ),
            f"Pins: {len(self.pins)}; capacitors: {len(self.cap_instances)}; "
            f"models: {len(self.cap_models)}",
            f"Padstacks: {len(self.padstacks)}; via groups: {len(self.via_usage)}",
        )


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
SpdAnalysisScope = Literal["selected_pi", "decap_scenario"]


@dataclass(frozen=True, slots=True)
class _Part:
    name: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Component:
    refdes: str
    start_layer: str | None
    attach_layer: str | None


@dataclass(frozen=True, slots=True)
class _Port:
    pin: str
    node_id: str
    net: str | None


@dataclass(frozen=True, slots=True)
class _Connection:
    refdes: str
    part_name: str
    usage: str | None
    ports: tuple[_Port, ...]


@dataclass(frozen=True, slots=True)
class _Node:
    x_um: float
    y_um: float
    padstack: str | None
    layer: str | None
    rotation_degrees: float = 0.0
    rotation_valid: bool = True


@dataclass(frozen=True, slots=True)
class _PinCandidate:
    refdes: str
    pin: str
    node_id: str
    net: str
    kind: PinKind
    terminal: TerminalKind
    domain: str | None
    site: str | None


@dataclass(frozen=True, slots=True)
class _CapCandidate:
    refdes: str
    model_id: str | None
    footprint: str
    power_net: str
    site: str | None
    mounted: bool
    power: _PinCandidate
    ground: _PinCandidate
    source_part_name: str | None = None
    start_layer: str | None = None
    attach_layer: str | None = None


class _Reporter:
    def __init__(
        self, progress: ProgressCallback | None, is_cancelled: CancelCallback | None
    ) -> None:
        self._progress = progress
        self._is_cancelled = is_cancelled
        self._last = -1

    def report(self, percent: int, message: str) -> None:
        self.check()
        value = max(self._last, min(100, max(0, int(percent))))
        if self._progress is not None and value != self._last:
            self._progress(value, message)
        self._last = value

    def check(self) -> None:
        if self._is_cancelled is not None and self._is_cancelled():
            raise SpdImportError("SPD import cancelled")


_LENGTH_RE = re.compile(
    rb"([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)"
    rb"\s*(mil|mm|um|u|m)(?![A-Za-z])",
    re.IGNORECASE,
)
_FLOAT_RE = re.compile(
    rb"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
)
_POSITIVE_POLYGON_RE = re.compile(
    rb"(?m)^Polygon[^\r\n\s]*::([^\r\n\s+]+)\+\s+"
)
_SHAPE_PRIMITIVE_RE = re.compile(
    rb"(?m)^([A-Za-z_]+)[^\r\n\s]*::(\S+?)([+-])(?:\s+|$)"
)
_SHAPE_RE = re.compile(rb"(?m)^\.Shape[ \t]+(\S+)")
_VIA_RE = re.compile(
    rb"(?m)^(Via[^\r\n:]*)::([^\s]+)\s+"
    rb"UpperNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"LowerNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"PadStack\s*=\s*(\S+)([^\r\n]*)"
)
_TRACE_RE = re.compile(
    rb"(?m)^(Trace[^\r\n:]*)::([^\s]+)\s+"
    rb"StartingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"EndingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?"
)
_NODE_ATTR_RE = re.compile(
    rb"\bX\s*=\s*(\S+)\s+Y\s*=\s*(\S+).*?(?:\bPadStack\s*=\s*(\S+))?"
)


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _line_end(data: mmap.mmap, start: int, end: int) -> int:
    found = data.find(b"\n", start, end)
    return end if found < 0 else found


def _iter_lines(data: mmap.mmap, start: int, end: int):
    position = max(0, start)
    if position and data[position - 1 : position] not in {b"\n", b"\r"}:
        newline = data.find(b"\n", position, end)
        position = end if newline < 0 else newline + 1
    while position < end:
        newline = data.find(b"\n", position, end)
        stop = end if newline < 0 else newline
        raw = data[position:stop]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        yield position, raw
        if newline < 0:
            break
        position = newline + 1


def _find_line(data: mmap.mmap, prefix: bytes, start: int = 0, end: int | None = None) -> int:
    stop = len(data) if end is None else end
    candidate = max(0, start)
    if (
        candidate < stop
        and (candidate == 0 or data[candidate - 1 : candidate] in {b"\n", b"\r"})
        and data[candidate : candidate + len(prefix)] == prefix
    ):
        return candidate
    found = data.find(b"\n" + prefix, candidate, stop)
    return -1 if found < 0 else found + 1


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _length_um(token: bytes | str) -> float:
    raw = token.encode("ascii", errors="ignore") if isinstance(token, str) else token
    match = _LENGTH_RE.fullmatch(raw.strip())
    if match is None:
        raise ValueError(f"invalid SPD length {_decode(raw)!r}")
    value = float(match.group(1))
    unit = match.group(2).lower()
    scale = {b"m": 1.0e6, b"mm": 1.0e3, b"u": 1.0, b"um": 1.0, b"mil": 25.4}[unit]
    result = value * scale
    if not isfinite(result):
        raise ValueError("SPD length is not finite")
    return result


def _lengths(raw: bytes) -> list[float]:
    return [_length_um(match.group(0)) for match in _LENGTH_RE.finditer(raw)]


def _attribute(raw: bytes, name: bytes) -> bytes | None:
    match = re.search(rb"\b" + re.escape(name) + rb"\s*=\s*(\S+)", raw, re.IGNORECASE)
    return None if match is None else match.group(1)


def _span_percent(position: int, start: int, end: int, low: int, high: int) -> int:
    if end <= start:
        return high
    return low + int((high - low) * (position - start) / (end - start))


def _parse_netlist(
    data: mmap.mmap,
    gnd_keys: set[str],
    diagnostics: list[SpdDiagnostic],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    start = _find_line(data, b".NetList")
    if start < 0:
        diagnostics.append(
            SpdDiagnostic("info", "NETLIST_MISSING", "No .NetList selection was found; positive plane polygons will be used as the rail filter.")
        )
        return (), ()
    end = _find_line(data, b".EndNetList", start)
    if end < 0:
        end = len(data)
        diagnostics.append(
            SpdDiagnostic("warning", "NETLIST_UNTERMINATED", ".NetList has no .EndNetList; parsed selections through end of file.")
        )
    # PowerSI writes .NetList as two ordered groups. Only the first row in a
    # group is guaranteed to contain ``-> GroundNets`` or ``-> PowerNets``;
    # later rows inherit that classification. Lines before the first group
    # marker are document properties or ordinary signal nets and must not be
    # promoted to editable power rails.
    _ = gnd_keys  # Kept in the signature for compatibility with older callers.
    power: list[str] = []
    ground: list[str] = []
    active_group: str | None = None
    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        if not stripped or stripped.startswith((b".", b"*")):
            continue
        token = stripped.split(None, 1)[0]
        if token.lower() in {b"powernets", b"groundnets"} or token.startswith(b"::"):
            continue

        # Production files may encode selection metadata on the destination,
        # e.g. ``VDD -> PowerNets::Unselected||DropShape``. The marker still
        # starts the PowerNets group and does not deselect the source token.
        if b"->" in stripped:
            tail = stripped.split(b"->", 1)[1].split(None, 1)
            destination = (tail[0] if tail else b"").lower()
            destination = destination.split(b"::", 1)[0]
            if destination == b"groundnets":
                active_group = "ground"
            elif destination == b"powernets":
                active_group = "power"

        if active_group is None or b"::unselected" in token.lower():
            continue

        name = _decode(token)
        if active_group == "ground":
            ground.append(name)
        else:
            power.append(name)
    selected_power, selected_ground = _unique(power), _unique(ground)
    if selected_power or selected_ground:
        diagnostics.append(
            SpdDiagnostic("info", "NETLIST_SELECTION_USED", f"Using {len(selected_power)} selected power net(s) and {len(selected_ground)} selected ground net(s) from .NetList.")
        )
    return selected_power, selected_ground


def _parse_shapes(
    data: mmap.mmap,
    start: int,
    end: int,
    selected_keys: set[str],
    geometry_keys: set[str] | None,
    reporter: _Reporter,
    diagnostics: list[SpdDiagnostic],
    *,
    transient_geometry_keys: set[str] | None = None,
    transient_geometry_layer: str | None = None,
) -> tuple[
    MLOOutline | None,
    dict[str, tuple[str, ...]],
    tuple[str, ...],
    tuple[SpdPlaneGeometry, ...],
]:
    shape_matches = list(_SHAPE_RE.finditer(data, start, end))
    shape_offsets = [item.start() for item in shape_matches]
    shape_names = [_decode(item.group(1)) for item in shape_matches]
    by_layer: dict[str, list[str]] = {}
    all_nets: list[str] = []
    best_bbox: tuple[float, float, float, float] | None = None
    best_area = -1.0
    geometry: dict[
        tuple[str, str],
        dict[str, object],
    ] = {}
    unsupported_seen: set[tuple[str, str, str]] = set()
    malformed_seen: set[tuple[str, str, str]] = set()
    polygon_kinds = {b"Polygon", b"PolygonTrace"}
    supported_kinds = {*polygon_kinds, b"Circle", b"Box"}
    for index, match in enumerate(_SHAPE_PRIMITIVE_RE.finditer(data, start, end)):
        if index % 128 == 0:
            reporter.report(
                _span_percent(match.start(), start, end, 15, 29),
                "Scanning selected plane geometry",
            )
        line_end = _line_end(data, match.start(), end)
        first_line = data[match.start():line_end]
        primitive_kind = match.group(1)
        net = _decode(match.group(2))
        polarity = match.group(3)
        is_sub_element = b"Sub-element" in first_line
        shape_index = bisect_right(shape_offsets, match.start()) - 1
        layer: str | None = None
        if shape_index >= 0:
            layer = shape_names[shape_index]
            if layer.casefold().endswith("pkgshape"):
                layer = layer[: -len("pkgshape")]
        if polarity == b"+" and not is_sub_element:
            all_nets.append(net)
            if layer is not None:
                by_layer.setdefault(layer.casefold(), []).append(net)

        retain_transient_geometry = bool(
            layer is not None
            and transient_geometry_layer is not None
            and layer.casefold() == transient_geometry_layer.casefold()
            and transient_geometry_keys is not None
            and net.casefold() in transient_geometry_keys
        )

        if primitive_kind not in supported_kinds:
            if (
                layer is not None
                and (
                    geometry_keys is None
                    or net.casefold() in geometry_keys
                    or retain_transient_geometry
                )
            ):
                unsupported_key = (
                    layer.casefold(),
                    net.casefold(),
                    _decode(primitive_kind).casefold(),
                )
                if unsupported_key not in unsupported_seen:
                    unsupported_seen.add(unsupported_key)
                    diagnostics.append(
                        SpdDiagnostic(
                            "error",
                            "SPD_PLANE_PRIMITIVE_UNSUPPORTED",
                            f"Unsupported {_decode(primitive_kind)} primitive on "
                            f"{layer}/{net} was not imported; Apply is blocked because "
                            "the selected PWR geometry would be incomplete.",
                        )
                    )
            continue

        retain_geometry = bool(
            layer is not None
            and (
                (
                    geometry_keys is None
                    and (not selected_keys or net.casefold() in selected_keys)
                )
                or (
                    geometry_keys is not None
                    and net.casefold() in geometry_keys
                )
                or retain_transient_geometry
            )
        )
        if retain_geometry:
            key = (layer.casefold(), net.casefold())
            entry = geometry.setdefault(
                key,
                {
                    "layer": layer,
                    "net": net,
                    "positive": [],
                    "negative": [],
                    "positive_circles": [],
                    "negative_circles": [],
                    "order": [],
                    "positive_sub": 0,
                    "negative_sub": 0,
                    "polygon_trace_count": 0,
                    "box_count": 0,
                },
            )
        elif is_sub_element or polarity != b"+":
            continue

        values: list[float] = []
        values.extend(_lengths(first_line))
        cursor = line_end + 1
        while primitive_kind in polygon_kinds and cursor < end:
            continuation_end = _line_end(data, cursor, end)
            continuation = data[cursor:continuation_end]
            if not continuation.lstrip().startswith(b"+"):
                break
            values.extend(_lengths(continuation))
            cursor = continuation_end + 1
        malformed_reason: str | None = None
        if primitive_kind == b"Circle":
            if len(values) != 3 or values[2] <= 0:
                malformed_reason = "Circle requires center X/Y and one positive radius"
            else:
                x_um, y_um, radius_um = values
        elif primitive_kind == b"Box":
            if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
                malformed_reason = "Box requires center X/Y and positive width/height"
            else:
                x_um, y_um, width_um, height_um = values
                half_width = width_um / 2.0
                half_height = height_um / 2.0
                values = [
                    x_um - half_width,
                    y_um - half_height,
                    x_um + half_width,
                    y_um - half_height,
                    x_um + half_width,
                    y_um + half_height,
                    x_um - half_width,
                    y_um + half_height,
                ]
        elif len(values) < 6 or len(values) % 2:
            malformed_reason = (
                f"{_decode(primitive_kind)} requires at least three finite X/Y pairs"
            )

        if malformed_reason is not None:
            if retain_geometry and layer is not None:
                malformed_key = (
                    layer.casefold(),
                    net.casefold(),
                    _decode(primitive_kind).casefold(),
                )
                if malformed_key not in malformed_seen:
                    malformed_seen.add(malformed_key)
                    diagnostics.append(
                        SpdDiagnostic(
                            "error",
                            "SPD_PLANE_PRIMITIVE_MALFORMED",
                            f"Malformed {_decode(primitive_kind)} primitive on "
                            f"{layer}/{net}: {malformed_reason}. Apply is blocked.",
                        )
                    )
            continue

        if retain_geometry and is_sub_element:
            count_key = "positive_sub" if polarity == b"+" else "negative_sub"
            entry[count_key] = int(entry[count_key]) + 1

        if primitive_kind == b"Circle":
            if polarity == b"+" and not is_sub_element:
                bbox = (
                    x_um - radius_um,
                    x_um + radius_um,
                    y_um - radius_um,
                    y_um + radius_um,
                )
                area = (2.0 * radius_um) ** 2
                if area > best_area:
                    best_bbox, best_area = bbox, area
            if retain_geometry:
                circle_key = (
                    "positive_circles"
                    if polarity == b"+"
                    else "negative_circles"
                )
                circles = entry[circle_key]
                assert isinstance(circles, list)
                primitive_order = entry["order"]
                assert isinstance(primitive_order, list)
                primitive_order.append(
                    (
                        "positive_circle"
                        if polarity == b"+"
                        else "negative_circle",
                        len(circles),
                    )
                )
                circles.append((x_um, y_um, radius_um))
        else:
            if polarity == b"+" and not is_sub_element:
                xs, ys = values[0::2], values[1::2]
                bbox = min(xs), max(xs), min(ys), max(ys)
                area = (bbox[1] - bbox[0]) * (bbox[3] - bbox[2])
                if area > best_area:
                    best_bbox, best_area = bbox, area
            if retain_geometry:
                polygon = _SpdPolygon(values)
                polygon_key = "positive" if polarity == b"+" else "negative"
                polygons = entry[polygon_key]
                assert isinstance(polygons, list)
                primitive_order = entry["order"]
                assert isinstance(primitive_order, list)
                primitive_order.append(
                    (
                        "positive_polygon"
                        if polarity == b"+"
                        else "negative_polygon",
                        len(polygons),
                    )
                )
                polygons.append(polygon)
                if primitive_kind == b"PolygonTrace":
                    entry["polygon_trace_count"] = (
                        int(entry["polygon_trace_count"]) + 1
                    )
                elif primitive_kind == b"Box":
                    entry["box_count"] = int(entry["box_count"]) + 1

    filtered: dict[str, tuple[str, ...]] = {}
    for key, nets in by_layer.items():
        kept = [item for item in _unique(nets) if not selected_keys or item.casefold() in selected_keys]
        filtered[key] = tuple(kept)
    outline = None
    if best_bbox is not None and best_bbox[1] > best_bbox[0] and best_bbox[3] > best_bbox[2]:
        outline = MLOOutline(
            width_um=best_bbox[1] - best_bbox[0],
            height_um=best_bbox[3] - best_bbox[2],
            origin_x_um=best_bbox[0],
            origin_y_um=best_bbox[2],
        )
    else:
        diagnostics.append(
            SpdDiagnostic("error", "OUTLINE_NOT_FOUND", "No positive Shape polygon with a usable coordinate bounding box was found.")
        )
    nets = _unique(all_nets)
    if not nets:
        diagnostics.append(
            SpdDiagnostic("error", "PLANE_NETS_NOT_FOUND", "No positive Shape polygon nets were found; signal pins cannot be filtered safely.")
        )
    plane_geometries = tuple(
        SpdPlaneGeometry(
            layer=str(entry["layer"]),
            net=str(entry["net"]),
            positive_polygons_um=tuple(entry["positive"]),
            negative_polygons_um=tuple(entry["negative"]),
            positive_circles_um=tuple(entry["positive_circles"]),
            negative_circles_um=tuple(entry["negative_circles"]),
            primitive_order=tuple(entry["order"]),
            positive_subelement_count=int(entry["positive_sub"]),
            negative_subelement_count=int(entry["negative_sub"]),
            polygon_trace_count=int(entry["polygon_trace_count"]),
            box_count=int(entry["box_count"]),
        )
        for _key, entry in sorted(geometry.items())
        if entry["positive"] or entry["positive_circles"]
    )
    circle_count = sum(
        len(item.positive_circles_um) + len(item.negative_circles_um)
        for item in plane_geometries
    )
    if circle_count:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_CIRCLE_RADIUS_INTERPRETATION",
                f"Interpreted the third length in {circle_count:,} selected Circle "
                "record(s) as radius, consistent with PowerSI pad/via circle records.",
            )
        )
    polygon_trace_count = sum(item.polygon_trace_count for item in plane_geometries)
    box_count = sum(item.box_count for item in plane_geometries)
    if polygon_trace_count:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_POLYGON_TRACE_NORMALIZED",
                f"Normalized {polygon_trace_count:,} selected PolygonTrace record(s) "
                "as ordered Polygon boundaries.",
            )
        )
    if box_count:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_BOX_CENTER_SIZE_INTERPRETATION",
                f"Normalized {box_count:,} selected Box record(s) as rectangles, "
                "interpreting the four lengths as center X/Y and width/height.",
            )
        )
    return outline, filtered, nets, plane_geometries


def _frequency_scale(header: bytes) -> float | None:
    folded = header.lower()
    if b"frequency" not in folded:
        return None
    if b"ghz" in folded:
        return 1.0e9
    if b"mhz" in folded:
        return 1.0e6
    if b"khz" in folded:
        return 1.0e3
    return 1.0


def _parse_materials(
    data: mmap.mmap, start: int, end: int, diagnostics: list[SpdDiagnostic]
) -> tuple[dict[str, tuple[float, float]], dict[str, float]]:
    dielectrics: dict[str, tuple[float, float]] = {}
    metals: dict[str, float] = {}
    kind: str | None = None
    name = ""
    rows: list[tuple[float, ...]] = []
    scale: float | None = None

    def flush() -> None:
        nonlocal kind, name, rows, scale
        if not kind or not name or not rows:
            kind, name, rows, scale = None, "", [], None
            return
        if scale is not None:
            valid = [row for row in rows if row[0] > 0]
            chosen = min(valid or rows, key=lambda row: abs(log10(max(row[0] * scale, 1e-300) / 1.0e9)))
        else:
            chosen = min(rows, key=lambda row: abs(row[0] - 20.0))
        if kind == "dielectric" and len(chosen) >= 3:
            dielectrics[name.casefold()] = (chosen[1], chosen[2])
        elif kind == "metal" and len(chosen) >= 2:
            metals[name.casefold()] = chosen[1]
        kind, name, rows, scale = None, "", [], None

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".dielectricmodel "):
            flush()
            kind, name = "dielectric", _decode(stripped.split(None, 1)[1])
        elif folded.startswith(b".metalmodel "):
            flush()
            kind, name = "metal", _decode(stripped.split(None, 1)[1])
        elif folded.startswith((b".enddielectricmodel", b".endmetalmodel")):
            flush()
        elif kind and stripped.startswith(b"*"):
            detected = _frequency_scale(stripped)
            if detected is not None:
                scale = detected
        elif kind and stripped and stripped[:1] in b"+-.0123456789":
            try:
                numbers = tuple(float(value) for value in _FLOAT_RE.findall(stripped))
            except ValueError:
                continue
            if numbers and all(isfinite(value) for value in numbers):
                rows.append(numbers)
    flush()
    if not dielectrics:
        diagnostics.append(SpdDiagnostic("warning", "DIELECTRIC_MODELS_MISSING", "No usable .DielectricModel property table was found."))
    if not metals:
        diagnostics.append(SpdDiagnostic("warning", "METAL_MODELS_MISSING", "No usable .MetalModel conductivity table was found."))
    return dielectrics, metals


def _parse_layers(
    data: mmap.mmap,
    start: int,
    end: int,
    layer_nets: dict[str, tuple[str, ...]],
    dielectrics: dict[str, tuple[float, float]],
    metals: dict[str, float],
    selected_keys: set[str],
    diagnostics: list[SpdDiagnostic],
) -> tuple[StackupLayer, ...]:
    result: list[StackupLayer] = []
    seen: set[str] = set()
    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        if not stripped or stripped.startswith((b"*", b"+", b".")) or b"Thickness" not in stripped:
            continue
        match = re.match(rb"(\S+)\s+Thickness\s*=\s*(\S+)(.*)$", stripped, re.IGNORECASE)
        if match is None:
            continue
        name = _decode(match.group(1))
        key = name.casefold()
        if key in seen:
            diagnostics.append(SpdDiagnostic("warning", "DUPLICATE_LAYER", f"Duplicate layer {name!r} was skipped."))
            continue
        try:
            thickness = _length_um(match.group(2))
        except ValueError as exc:
            diagnostics.append(SpdDiagnostic("warning", "LAYER_THICKNESS_INVALID", f"Layer {name!r} was skipped: {exc}."))
            continue
        material_raw = _attribute(stripped, b"Material")
        material = _decode(material_raw) if material_raw else ""
        material_key = material.casefold()
        conductor = key.startswith(("signal$", "power$", "conductor$")) or material_key in metals
        conductivity_raw = _attribute(stripped, b"Conductivity")
        conductivity = float(conductivity_raw) if conductivity_raw else metals.get(material_key)
        if not conductor:
            conductivity = None
        permittivity_raw = _attribute(stripped, b"Permittivity")
        loss_raw = _attribute(stripped, b"LossTangent")
        dk = float(permittivity_raw) if permittivity_raw else None
        df = float(loss_raw) if loss_raw else None
        if (dk is None or df is None) and material_key in dielectrics:
            material_dk, material_df = dielectrics[material_key]
            dk = material_dk if dk is None else dk
            df = material_df if df is None else df
        nets = list(layer_nets.get(key, ())) if conductor else []
        if conductor and not nets:
            parenthesized = re.search(r"\(([^)]+)\)", name)
            if parenthesized and (
                not selected_keys or parenthesized.group(1).casefold() in selected_keys
            ):
                nets.append(parenthesized.group(1))
        if conductor and conductivity is None:
            diagnostics.append(SpdDiagnostic("warning", "LAYER_CONDUCTIVITY_MISSING", f"Conductor layer {name!r} references material {material!r} without a usable conductivity table."))
        try:
            result.append(
                StackupLayer(
                    name=name,
                    thickness_um=thickness,
                    conductivity_s_m=conductivity,
                    dk=dk,
                    df=df,
                    pwr_nets=nets,
                )
            )
            seen.add(key)
        except ValueError as exc:
            diagnostics.append(SpdDiagnostic("warning", "LAYER_INVALID", f"Layer {name!r} was skipped: {exc}."))
    if not result:
        diagnostics.append(SpdDiagnostic("error", "STACKUP_NOT_FOUND", "No valid Thickness layer rows were found in the SPD layer section."))
    return tuple(result)


def _first_conductor_layer_name(
    data: mmap.mmap,
    start: int,
    end: int,
    metals: Mapping[str, float],
) -> str | None:
    """Read only the first conductor identity before the geometry pass.

    The full stack-up is still built after shape NET indexing.  This lightweight
    pre-read lets scenario imports retain configured GND geometry on TOP only,
    avoiding both an all-layer GND import and a second shape scan.
    """

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        if (
            not stripped
            or stripped.startswith((b"*", b"+", b"."))
            or b"Thickness" not in stripped
        ):
            continue
        match = re.match(
            rb"(\S+)\s+Thickness\s*=\s*(\S+)(.*)$",
            stripped,
            re.IGNORECASE,
        )
        if match is None:
            continue
        name = _decode(match.group(1))
        material_raw = _attribute(stripped, b"Material")
        material_key = _decode(material_raw).casefold() if material_raw else ""
        if name.casefold().startswith(("signal$", "power$", "conductor$")) or (
            material_key in metals
        ):
            return name
    return None


def _parse_padstacks(
    data: mmap.mmap, start: int, end: int, diagnostics: list[SpdDiagnostic]
) -> tuple[SpdPadStack, ...]:
    result: list[SpdPadStack] = []
    name: str | None = None
    drill: float | None = None
    width: float | None = None
    height: float | None = None
    layers: list[str] = []
    shapes: list[SpdPadShape] = []
    active_layer: str | None = None
    variable = False

    def flush() -> None:
        nonlocal name, drill, width, height, layers, shapes, active_layer, variable
        if name is not None:
            result.append(
                SpdPadStack(
                    name,
                    drill,
                    width,
                    height,
                    _unique(layers),
                    tuple(shapes),
                )
            )
            if variable:
                diagnostics.append(SpdDiagnostic("info", "PADSTACK_VARIABLE_PAD", f"Padstack {name!r} has layer-dependent pad sizes; maximum width/height were retained."))
        name, drill, width, height, layers, shapes, active_layer, variable = (
            None,
            None,
            None,
            None,
            [],
            [],
            None,
            False,
        )

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".padstackdef "):
            flush()
            tokens = stripped.split()
            name = _decode(tokens[1]) if len(tokens) > 1 else ""
            values = _lengths(b" ".join(tokens[2:]))
            drill = 2.0 * values[0] if values else None
        elif folded.startswith(b".endpadstackdef"):
            flush()
        elif name is not None and folded.startswith(b".paddef "):
            tokens = stripped.split(None, 1)
            if len(tokens) == 2:
                active_layer = _decode(tokens[1])
                layers.append(active_layer)
        elif name is not None and folded.startswith(b".endpaddef"):
            active_layer = None
        elif name is not None and folded.startswith(b"regular "):
            values = _lengths(stripped)
            candidate: tuple[float, float] | None = None
            shape_kind: Literal["CIRCLE", "RECTANGLE", "UNSUPPORTED"]
            reason: str | None = None
            if folded.startswith(b"regular circle") and values:
                candidate = (2.0 * values[0], 2.0 * values[0])
                shape_kind = "CIRCLE"
            elif folded.startswith(b"regular square") and values:
                candidate = (values[0], values[0])
                shape_kind = "RECTANGLE"
            elif folded.startswith(b"regular box") and len(values) >= 2:
                candidate = (values[0], values[1])
                shape_kind = "RECTANGLE"
            else:
                shape_kind = "UNSUPPORTED"
                reason = (
                    f"padstack {name!r} uses unsupported or malformed "
                    f"{_decode(stripped.split(None, 2)[1]) if len(stripped.split(None, 2)) > 1 else 'Regular'} geometry"
                )
            if active_layer is not None:
                shapes.append(
                    SpdPadShape(
                        layer=active_layer,
                        kind=shape_kind,
                        width_um=candidate[0] if candidate is not None else None,
                        height_um=candidate[1] if candidate is not None else None,
                        reason=reason,
                    )
                )
            if candidate is not None:
                if width is not None and (candidate[0] != width or candidate[1] != height):
                    variable = True
                width = candidate[0] if width is None else max(width, candidate[0])
                height = candidate[1] if height is None else max(height, candidate[1])
    flush()
    if result:
        diagnostics.append(SpdDiagnostic("info", "PADSTACK_RADII_CONVERTED", "PadStackDef drill values and Regular Circle values were interpreted as radii and converted to diameters."))
    return tuple(result)


def _parse_partial_circuits(
    data: mmap.mmap,
    start: int,
    end: int,
    frequencies: tuple[float, ...],
    source_name: str,
    reporter: _Reporter,
    diagnostics: list[SpdDiagnostic],
) -> tuple[dict[str, PassiveSubcircuitModel], dict[str, str], dict[str, str], set[str], int]:
    models: dict[str, PassiveSubcircuitModel] = {}
    assets: dict[str, str] = {}
    canonical: dict[str, str] = {}
    empty_names: set[str] = set()
    total = 0
    position = start
    while position < end:
        block_start = _find_line(data, b".PartialCkt", position, end)
        if block_start < 0:
            break
        total += 1
        first_end = _line_end(data, block_start, end)
        header_chunks = [data[block_start:first_end].strip()]
        body_start = first_end + 1
        while body_start < end:
            next_end = _line_end(data, body_start, end)
            continuation = data[body_start:next_end].strip()
            if not continuation.startswith(b"+"):
                break
            header_chunks.append(continuation[1:].strip())
            body_start = next_end + 1
        block_end = _find_line(data, b".EndPartialCkt", body_start, end)
        if block_end < 0:
            diagnostics.append(SpdDiagnostic("error", "PARTIAL_CKT_UNTERMINATED", f"PartialCkt beginning near byte {block_start:,} has no .EndPartialCkt."))
            break
        end_line = _line_end(data, block_end, end)
        header = b" ".join(header_chunks)
        match = re.match(rb"\.PartialCkt\s+(\S+)\s+ExtNode\s*=\s*(.*)$", header, re.IGNORECASE)
        if match is None:
            diagnostics.append(SpdDiagnostic("warning", "PARTIAL_CKT_HEADER_INVALID", f"Malformed PartialCkt header near byte {block_start:,} was skipped."))
            position = end_line + 1
            continue
        raw_name = _decode(match.group(1))
        ext_nodes = tuple(_decode(item) for item in match.group(2).split())
        if len(ext_nodes) != 2:
            position = end_line + 1
            continue
        body = data[body_start:block_end].decode("utf-8", errors="replace").strip("\r\n")
        if not body.strip():
            empty_names.add(raw_name.casefold())
            if "not_mounted" not in raw_name.casefold():
                diagnostics.append(SpdDiagnostic("info", "EMPTY_TWO_PORT", f"Empty two-port PartialCkt {raw_name!r} was not imported as a model."))
            position = end_line + 1
            continue
        asset = f".SUBCKT {raw_name} {ext_nodes[0]} {ext_nodes[1]}\n{body}\n.ENDS {raw_name}\n"
        try:
            model = parse_passive_subcircuit(asset, source_name=f"{source_name}:{raw_name}")
            model.impedance(frequencies)
        except (SpiceModelError, ValueError) as exc:
            diagnostics.append(SpdDiagnostic("warning", "PARTIAL_MODEL_UNSUPPORTED", f"Two-port PartialCkt {raw_name!r} was skipped: {exc}"))
        else:
            if model.model_id in models:
                diagnostics.append(SpdDiagnostic("warning", "DUPLICATE_PARTIAL_MODEL", f"Duplicate two-port model {model.model_id!r} was skipped."))
            else:
                models[model.model_id] = model
                assets[f"{model.model_id}.lib"] = asset
                canonical[raw_name.casefold()] = model.model_id
        position = end_line + 1
        reporter.report(_span_percent(position, start, end, 39, 46), "Parsing two-terminal passive models")
    return models, assets, canonical, empty_names, total


def _parse_port(raw: bytes) -> _Port | None:
    tokens = raw.strip().split()
    if len(tokens) < 2 or not tokens[1].startswith(b"$Package."):
        return None
    pin = _decode(tokens[0])
    reference = tokens[1][len(b"$Package.") :]
    node_part, separator, net_part = reference.partition(b"::")
    node_id = node_part.split(b"!!", 1)[0]
    if not node_id.startswith(b"Node"):
        return None
    return _Port(pin, _decode(node_id), _decode(net_part) if separator else None)


def _parse_metadata(
    data: mmap.mmap, start: int, end: int, diagnostics: list[SpdDiagnostic]
) -> tuple[dict[str, _Part], dict[str, _Component], tuple[_Connection, ...]]:
    parts: dict[str, _Part] = {}
    components: dict[str, _Component] = {}
    connections: list[_Connection] = []
    active_header: tuple[str, str, str | None] | None = None
    active_ports: list[_Port] = []

    def flush_connection(unterminated: bool = False) -> None:
        nonlocal active_header, active_ports
        if active_header is not None:
            connections.append(_Connection(*active_header, tuple(active_ports)))
            if unterminated:
                diagnostics.append(SpdDiagnostic("warning", "CONNECT_UNTERMINATED", f"Connection {active_header[0]!r} had no .EndC before the next record."))
        active_header, active_ports = None, []

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".connect "):
            flush_connection(active_header is not None)
            tokens = stripped.split()
            if len(tokens) >= 3:
                usage_raw = _attribute(stripped, b"Usage")
                active_header = (_decode(tokens[1]), _decode(tokens[2]), _decode(usage_raw) if usage_raw else None)
        elif folded.startswith(b".endc"):
            flush_connection()
        elif active_header is not None:
            port = _parse_port(stripped)
            if port is not None:
                active_ports.append(port)
        elif folded.startswith(b".part ") and not folded.startswith(b".partialckt"):
            tokens = stripped.split()
            if len(tokens) >= 2:
                part_name = _decode(tokens[1])
                tag_match = re.search(rb"\bTags\s*=\s*\"([^\"]*)\"", stripped, re.IGNORECASE)
                tags = () if tag_match is None else tuple(item.strip() for item in _decode(tag_match.group(1)).split(",") if item.strip())
                parts[part_name.casefold()] = _Part(part_name, tags)
        elif folded.startswith(b".component "):
            tokens = stripped.split()
            if len(tokens) >= 2:
                refdes = _decode(tokens[1])
                start_layer = _attribute(stripped, b"StartLayer")
                attach_layer = _attribute(stripped, b"AttachLayer")
                components[refdes.casefold()] = _Component(refdes, _decode(start_layer) if start_layer else None, _decode(attach_layer) if attach_layer else None)
    flush_connection(active_header is not None)
    return parts, components, tuple(connections)


def _site_for(refdes: str, net: str) -> str | None:
    if refdes.casefold().startswith("site"):
        return refdes
    match = re.search(r"/(\d+)$", net)
    return f"SITE{match.group(1)}" if match else None


def _footprint_for_part(part_name: str) -> str:
    match = re.match(r"CAP_([^_]+)", part_name, re.IGNORECASE)
    return match.group(1).upper() if match else part_name


def _is_capacitor_connection(
    connection: _Connection,
    part: _Part | None,
    canonical: dict[str, str],
    empty_names: set[str],
) -> bool:
    """Return whether a two-terminal connection is recognizably a decap.

    Normal PI import remains model-driven.  Scenario analysis additionally
    exposes DNP footprints whose original SPD part has no usable PartialCkt so
    the user can assign a separately imported capacitor model later.
    """

    if len(connection.ports) != 2:
        return False
    key = connection.part_name.casefold()
    if key in canonical or key in empty_names or "not_mounted" in key:
        return True
    tokens = [connection.part_name, connection.refdes]
    if part is not None:
        tokens.extend((part.name, *part.tags))
    folded = " ".join(tokens).casefold()
    return bool(
        re.match(r"^c(?:\d|[_-])", connection.refdes, re.IGNORECASE)
        or any(token in folded for token in ("capacitor", "decap", " cap", "cap_"))
    )


def _select_candidates(
    parts: dict[str, _Part],
    components: dict[str, _Component],
    connections: tuple[_Connection, ...],
    canonical: dict[str, str],
    empty_names: set[str],
    plane_keys: set[str],
    selected_power_keys: set[str],
    ground_keys: set[str],
    diagnostics: list[SpdDiagnostic],
    *,
    include_unmodeled_caps: bool = False,
    include_unselected_caps: bool = False,
) -> tuple[list[_PinCandidate], list[_CapCandidate], set[str], int, int]:
    device: list[_PinCandidate] = []
    caps: list[_CapCandidate] = []
    referenced: set[str] = set()
    skipped_unselected = 0
    ambiguous = 0
    for connection in connections:
        part = parts.get(connection.part_name.casefold())
        component = components.get(connection.refdes.casefold())
        is_io = bool(part and any(tag.casefold() == "io" for tag in part.tags)) and bool(
            component and component.attach_layer and component.attach_layer.casefold() == "topair"
        )
        if is_io:
            for port in connection.ports:
                if port.net is None or port.net.casefold() not in plane_keys:
                    continue
                terminal = TerminalKind.GND if port.net.casefold() in ground_keys else TerminalKind.PWR
                # /0 and /1 are separate device-side sites/domains.  Shared
                # DGND has no suffix and stays site-agnostic so coordinate
                # pairing can select the nearest return bump for either site.
                site = (
                    None
                    if terminal == TerminalKind.GND
                    else _site_for(connection.refdes, port.net)
                )
                candidate = _PinCandidate(connection.refdes, port.pin, port.node_id, port.net, PinKind.DEVICE_BUMP, terminal, port.net if terminal == TerminalKind.PWR else None, site)
                device.append(candidate)
                referenced.add(port.node_id.casefold())
            continue

        raw_model_key = connection.part_name.casefold()
        model_id = canonical.get(raw_model_key)
        mounted = connection.usage != "0b111000"
        if "not_mounted" in raw_model_key:
            mounted = False
            base = re.sub(r"_?not_mounted$", "", connection.part_name, flags=re.IGNORECASE)
            model_id = canonical.get(base.casefold())
        if len(connection.ports) != 2 or (
            model_id is None
            and not (
                include_unmodeled_caps
                and _is_capacitor_connection(connection, part, canonical, empty_names)
            )
        ):
            continue
        ground_ports = [port for port in connection.ports if port.net and port.net.casefold() in ground_keys]
        power_ports = [port for port in connection.ports if port.net and port.net.casefold() not in ground_keys]
        if len(ground_ports) != 1 or len(power_ports) != 1:
            ambiguous += 1
            continue
        power, ground = power_ports[0], ground_ports[0]
        assert power.net is not None and ground.net is not None
        if power.net.casefold() not in plane_keys and not include_unselected_caps:
            skipped_unselected += 1
            continue
        site = _site_for(connection.refdes, power.net)
        power_candidate = _PinCandidate(connection.refdes, power.pin, power.node_id, power.net, PinKind.DECAP_PAD, TerminalKind.PWR, power.net, site)
        ground_candidate = _PinCandidate(connection.refdes, ground.pin, ground.node_id, ground.net, PinKind.DECAP_PAD, TerminalKind.GND, power.net, site)
        part_name = part.name if part else connection.part_name
        caps.append(
            _CapCandidate(
                connection.refdes,
                model_id,
                _footprint_for_part(part_name),
                power.net,
                site,
                mounted,
                power_candidate,
                ground_candidate,
                source_part_name=part_name,
                start_layer=component.start_layer if component else None,
                attach_layer=component.attach_layer if component else None,
            )
        )
        referenced.update((power.node_id.casefold(), ground.node_id.casefold()))
    if skipped_unselected:
        diagnostics.append(SpdDiagnostic("info", "UNSELECTED_DECAPS_SKIPPED", f"Skipped {skipped_unselected} two-terminal capacitor instance(s) whose power net is not selected for PI analysis."))
    if ambiguous:
        diagnostics.append(SpdDiagnostic("warning", "AMBIGUOUS_TWO_PORT_CONNECTION", f"Skipped {ambiguous} two-port connection(s) that did not map to exactly one power and one ground terminal."))
    return device, caps, referenced, skipped_unselected, ambiguous


def _parse_referenced_nodes(
    data: mmap.mmap,
    start: int,
    end: int,
    referenced: set[str],
    reporter: _Reporter,
    *,
    top_layer: str | None = None,
    progress_low: int = 58,
    progress_high: int = 82,
) -> dict[str, _Node]:
    result: dict[str, _Node] = {}
    if not referenced and top_layer is None:
        return result
    top_marker = (
        b"Layer = " + top_layer.encode("utf-8") if top_layer is not None else None
    )
    for index, (offset, raw) in enumerate(_iter_lines(data, start, end)):
        if index % 16384 == 0:
            reporter.report(
                _span_percent(offset, start, end, progress_low, progress_high),
                "Resolving decap and TOP Via Node coordinates",
            )
        if not raw.startswith(b"Node"):
            continue
        top_position = raw.find(top_marker) if top_marker is not None else -1
        top_end = top_position + len(top_marker) if top_marker is not None else -1
        is_top = bool(
            top_position >= 0
            and (top_end == len(raw) or raw[top_end : top_end + 1].isspace())
        )
        cuts = [value for value in (raw.find(b"!!"), raw.find(b"::"), raw.find(b" ")) if value >= 0]
        if not cuts:
            continue
        node_id = _decode(raw[: min(cuts)])
        key = node_id.casefold()
        if key not in referenced and not is_top:
            continue
        match = _NODE_ATTR_RE.search(raw)
        if match is None:
            continue
        try:
            x_um, y_um = _length_um(match.group(1)), _length_um(match.group(2))
        except ValueError:
            continue
        padstack_raw = _attribute(raw, b"PadStack")
        padstack = (
            _decode(padstack_raw)
            if padstack_raw
            else _decode(match.group(3))
            if match.group(3)
            else None
        )
        layer_raw = _attribute(raw, b"Layer")
        rotation_raw = _attribute(raw, b"AbsoluteRotation")
        try:
            rotation = float(rotation_raw) if rotation_raw else 0.0
            rotation_valid = isfinite(rotation)
        except ValueError:
            rotation = 0.0
            rotation_valid = False
        result[key] = _Node(
            x_um,
            y_um,
            padstack,
            _decode(layer_raw) if layer_raw else None,
            rotation,
            rotation_valid,
        )
    return result


def _materialize_geometry(
    device: list[_PinCandidate],
    caps: list[_CapCandidate],
    nodes: dict[str, _Node],
    diagnostics: list[SpdDiagnostic],
) -> tuple[tuple[PinRecord, ...], tuple[SpdCapInstance, ...], int]:
    pins: list[PinRecord] = []
    instances: list[SpdCapInstance] = []
    missing = 0

    def make_pin(candidate: _PinCandidate) -> PinRecord | None:
        nonlocal missing
        node = nodes.get(candidate.node_id.casefold())
        if node is None:
            missing += 1
            return None
        return PinRecord(
            refdes=candidate.refdes,
            pin=candidate.pin,
            net=candidate.net,
            x_um=node.x_um,
            y_um=node.y_um,
            kind=candidate.kind,
            terminal=candidate.terminal,
            domain=candidate.domain,
            site=candidate.site,
            bump_group=candidate.refdes if candidate.kind == PinKind.DEVICE_BUMP else None,
            # A raw SPD padstack is physical provenance, not a calibrated
            # solver ViaLoopTemplate identifier.
            via_template_id=None,
        )

    for candidate in device:
        pin = make_pin(candidate)
        if pin is not None:
            pins.append(pin)
    for candidate in caps:
        power_pin, ground_pin = make_pin(candidate.power), make_pin(candidate.ground)
        if power_pin is None or ground_pin is None:
            continue
        pins.extend((power_pin, ground_pin))
        instances.append(
            SpdCapInstance(
                refdes=candidate.refdes,
                model_id=candidate.model_id,
                footprint=candidate.footprint,
                power_net=candidate.power_net,
                ground_net=candidate.ground.net,
                site=candidate.site,
                mounted=candidate.mounted,
                x_um=(power_pin.x_um + ground_pin.x_um) / 2.0,
                y_um=(power_pin.y_um + ground_pin.y_um) / 2.0,
                power_pin_id=power_pin.pin_id,
                ground_pin_id=ground_pin.pin_id,
                source_part_name=candidate.source_part_name,
                start_layer=candidate.start_layer,
                attach_layer=candidate.attach_layer,
                power_pad_x_um=power_pin.x_um,
                power_pad_y_um=power_pin.y_um,
                ground_pad_x_um=ground_pin.x_um,
                ground_pad_y_um=ground_pin.y_um,
                power_padstack=(
                    nodes[candidate.power.node_id.casefold()].padstack
                    if candidate.power.node_id.casefold() in nodes
                    else None
                ),
                ground_padstack=(
                    nodes[candidate.ground.node_id.casefold()].padstack
                    if candidate.ground.node_id.casefold() in nodes
                    else None
                ),
                power_pad_rotation_degrees=(
                    nodes[candidate.power.node_id.casefold()].rotation_degrees
                    if candidate.power.node_id.casefold() in nodes
                    else 0.0
                ),
                ground_pad_rotation_degrees=(
                    nodes[candidate.ground.node_id.casefold()].rotation_degrees
                    if candidate.ground.node_id.casefold() in nodes
                    else 0.0
                ),
                power_pad_rotation_valid=(
                    nodes[candidate.power.node_id.casefold()].rotation_valid
                    if candidate.power.node_id.casefold() in nodes
                    else False
                ),
                ground_pad_rotation_valid=(
                    nodes[candidate.ground.node_id.casefold()].rotation_valid
                    if candidate.ground.node_id.casefold() in nodes
                    else False
                ),
            )
        )
    if missing:
        diagnostics.append(SpdDiagnostic("warning", "REFERENCED_NODE_MISSING", f"Could not resolve {missing} referenced pin Node record(s); affected pins/instances were skipped."))
    pins.sort(key=lambda item: (item.refdes.casefold(), item.pin.casefold()))
    instances.sort(key=lambda item: item.refdes.casefold())
    return tuple(pins), tuple(instances), missing


def _parse_vias(
    data: mmap.mmap,
    start: int,
    end: int,
    plane_keys: set[str],
    connection_keys: set[str],
    nodes: dict[str, _Node],
    reporter: _Reporter,
    *,
    top_layer: str | None,
    padstacks: tuple[SpdPadStack, ...],
) -> tuple[
    tuple[SpdViaUsage, ...],
    int,
    tuple[ViaTopEndpoint, ...],
    tuple[str, ...],
]:
    counter: Counter[tuple[str, str]] = Counter()
    total = 0
    endpoints: list[ViaTopEndpoint] = []
    uncertain_nets: set[str] = set()
    padstack_by_key = {item.name.casefold(): item for item in padstacks}
    top_key = top_layer.casefold() if top_layer else None
    plane_key_bytes = {item.encode("utf-8") for item in plane_keys}
    connection_key_bytes = {item.encode("utf-8") for item in connection_keys}
    padstack_by_bytes = {
        key.encode("utf-8"): value for key, value in padstack_by_key.items()
    }
    top_padstack_keys = (
        {
            item.name.casefold()
            for item in padstacks
            if top_key in {layer.casefold() for layer in item.layers}
        }
        if top_key is not None
        else set()
    )
    top_padstack_key_bytes = {
        item.encode("utf-8") for item in top_padstack_keys
    }
    for index, match in enumerate(_VIA_RE.finditer(data, start, end)):
        total += 1
        if index % 8192 == 0:
            reporter.report(
                _span_percent(match.start(), start, end, 83, 96),
                "Counting vias and resolving TOP landing evidence",
            )
        net_raw = match.group(2)
        padstack_raw = match.group(5)
        net_key_raw = net_raw.lower()
        padstack_key_raw = padstack_raw.lower()
        net: str | None = None
        padstack: str | None = None
        if not plane_keys or net_key_raw in plane_key_bytes:
            net = _decode(net_raw)
            padstack = _decode(padstack_raw)
            counter[(net, padstack)] += 1
        if top_key is None or net_key_raw not in connection_key_bytes:
            continue
        definition = padstack_by_bytes.get(padstack_key_raw)
        if definition is None:
            # An unknown padstack matters only when one of the source endpoints
            # is demonstrably on TOP; otherwise it cannot affect TOP decaps.
            if any(
                (node := nodes.get(_decode(node_id).casefold())) is not None
                and node.layer is not None
                and node.layer.casefold() == top_key
                for node_id in (match.group(3), match.group(4))
            ):
                uncertain_nets.add(net or _decode(net_raw))
            continue
        if padstack_key_raw not in top_padstack_key_bytes:
            continue
        via_id = _decode(match.group(1))
        net = net or _decode(net_raw)
        upper_node_id = _decode(match.group(3))
        lower_node_id = _decode(match.group(4))
        padstack = padstack or _decode(padstack_raw)
        rotation_raw = _attribute(match.group(6), b"AbsoluteRotation")
        try:
            rotation = float(rotation_raw) if rotation_raw else 0.0
            if not isfinite(rotation):
                raise ValueError("non-finite Via rotation")
        except ValueError:
            rotation = 0.0
            uncertain_nets.add(net)
        for node_id in (upper_node_id, lower_node_id):
            node = nodes.get(node_id.casefold())
            if node is None or node.layer is None:
                # Every TOP node is retained by the single Node pass.  A missing
                # endpoint therefore cannot be TOP and needs no distance guess.
                continue
            if node.layer.casefold() != top_key:
                continue
            endpoints.append(
                ViaTopEndpoint(
                    via_id=via_id,
                    net=net,
                    endpoint_node_id=node_id,
                    x_um=node.x_um,
                    y_um=node.y_um,
                    padstack=padstack,
                    rotation_degrees=rotation,
                )
            )
    result = tuple(
        SpdViaUsage(net, padstack, count)
        for (net, padstack), count in sorted(counter.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold()))
    )
    return (
        result,
        total,
        tuple(
            sorted(
                endpoints,
                key=lambda item: (
                    item.via_id.casefold(),
                    item.endpoint_node_id.casefold(),
                ),
            )
        ),
        tuple(sorted(uncertain_nets, key=str.casefold)),
    )


@dataclass(frozen=True, slots=True)
class _RecoveredViaNode:
    node_id: str
    net: str
    x_um: float
    y_um: float
    layer: str
    padstack: str | None


def recover_spd_via_paths(
    path: str | Path,
    *,
    landings: Iterable[object],
    target_layers_by_net: Mapping[str, Iterable[str]],
    stackup_layers: Sequence[StackupLayer],
    padstacks: Iterable[SpdPadStack],
    top_layer: str | None,
    max_segments: int = 16,
    expected_source: SpdSourceInfo | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> SpdViaPathRecovery:
    """Recover compact, source-proven vertical Via chains after SPD planning.

    This intentionally reopens the source only after the import plan has selected
    the editable terminal Vias and plane layers.  It keeps just the frontier
    nodes/segments for those requests, never serializes a board-wide Via graph,
    and refuses to traverse Trace geometry.  A lateral Trace is therefore harmless
    when one monotonic Via route reaches the requested plane; it is reported only
    when reaching that plane would require lateral traversal.
    """

    reporter = _Reporter(progress, is_cancelled)
    reporter.report(0, "Recovering source-proven vertical Via paths")
    source_path = Path(path)
    if max_segments < 1:
        raise ValueError("max_segments must be >= 1")
    if not source_path.is_file():
        raise SpdImportError(f"SPD source does not exist: {source_path}")

    def stat_identity(
        *, phase: str, enforce_expected: bool = True
    ) -> tuple[int, int]:
        try:
            observed = source_path.stat()
        except OSError as exc:
            raise SpdImportError(
                f"cannot stat SPD source {source_path} {phase}: {exc}"
            ) from exc
        identity = (int(observed.st_size), int(observed.st_mtime_ns))
        if expected_source is not None and enforce_expected:
            try:
                same_path = source_path.resolve() == expected_source.path.resolve()
            except OSError as exc:
                raise SpdImportError(
                    f"cannot resolve SPD source identity {source_path}: {exc}"
                ) from exc
            expected_identity = (
                int(expected_source.size_bytes),
                int(expected_source.mtime_ns),
            )
            if not same_path or identity != expected_identity:
                raise SpdImportError(
                    "SPD source identity changed after analysis before source Via "
                    "path recovery; import aborted so path evidence cannot be "
                    "mixed with a different source SHA-256"
                )
        return identity

    before_recovery = stat_identity(phase="before source Via path recovery")

    def finish(result: SpdViaPathRecovery) -> SpdViaPathRecovery:
        after_recovery = stat_identity(
            phase="after source Via path recovery", enforce_expected=False
        )
        if after_recovery != before_recovery:
            raise SpdImportError(
                "SPD source changed during source Via path recovery; import "
                "aborted and no mixed-source path evidence was persisted"
            )
        return result

    if not top_layer:
        return finish(SpdViaPathRecovery(
            evidence_by_via={},
            diagnostics=(
                SpdDiagnostic(
                    "warning",
                    "SPD_VIA_PATH_FALLBACK",
                    "No TOP conductor layer was available; terminal Via paths use "
                    "the documented legacy rail template.",
                ),
            ),
            statistics={"requested": 0, "recovered": 0, "fallback": 0},
        ))

    layer_by_key = {item.name.casefold(): item.name for item in stackup_layers}
    depth_by_key = {
        item.name.casefold(): index for index, item in enumerate(stackup_layers)
    }
    top_key = top_layer.casefold()
    if top_key not in depth_by_key:
        return finish(SpdViaPathRecovery(
            evidence_by_via={},
            diagnostics=(
                SpdDiagnostic(
                    "warning",
                    "SPD_VIA_PATH_FALLBACK",
                    "The source TOP layer is absent from the normalized stack-up; "
                    "terminal Via paths use the documented legacy rail template.",
                ),
            ),
            statistics={"requested": 0, "recovered": 0, "fallback": 0},
        ))
    targets_by_net = {
        str(net).casefold(): tuple(
            dict.fromkeys(
                layer_by_key[layer.casefold()]
                for layer in layers
                if layer.casefold() in layer_by_key
            )
        )
        for net, layers in target_layers_by_net.items()
    }
    padstack_by_key = {item.name.casefold(): item for item in padstacks}

    def landing_value(landing: object, name: str) -> object:
        try:
            return getattr(landing, name)
        except AttributeError as exc:
            raise ValueError(f"Via landing lacks {name!r} source evidence") from exc

    states: dict[tuple[str, str], dict[str, object]] = {}
    nodes: dict[str, _RecoveredViaNode] = {}
    diagnostics: list[SpdDiagnostic] = []
    for landing in landings:
        via_id = str(landing_value(landing, "via_id"))
        net = str(landing_value(landing, "net"))
        endpoint_node_id = str(landing_value(landing, "endpoint_node_id"))
        via_key = via_id.casefold()
        net_key = net.casefold()
        endpoint_key = endpoint_node_id.casefold()
        for target_layer in targets_by_net.get(net_key, ()):
            state_key = (via_key, target_layer.casefold())
            previous = states.get(state_key)
            if previous is not None:
                if previous["node_key"] != endpoint_key or previous["net_key"] != net_key:
                    diagnostics.append(
                        SpdDiagnostic(
                            "warning",
                            "SPD_VIA_PATH_FALLBACK",
                            f"Via {via_id!r} has conflicting TOP landing evidence; "
                            "its terminal path uses the legacy rail template.",
                        )
                    )
                    previous["status"] = "CONFLICTING_TOP_LANDING"
                continue
            states[state_key] = {
                "via_id": via_id,
                "via_key": via_key,
                "net": net,
                "net_key": net_key,
                "target_layer": target_layer,
                "target_key": target_layer.casefold(),
                "node_id": endpoint_node_id,
                "node_key": endpoint_key,
                "layer": top_layer,
                "segments": [],
                "status": "PENDING",
            }
            nodes.setdefault(
                endpoint_key,
                _RecoveredViaNode(
                    node_id=endpoint_node_id,
                    net=net,
                    x_um=float(landing_value(landing, "x_um")),
                    y_um=float(landing_value(landing, "y_um")),
                    layer=top_layer,
                    padstack=str(landing_value(landing, "padstack")),
                ),
            )

    if not states:
        return finish(SpdViaPathRecovery(
            evidence_by_via={}, diagnostics=tuple(diagnostics),
            statistics={"requested": 0, "recovered": 0, "fallback": 0},
        ))

    def node_id_and_net(raw: bytes) -> tuple[str, str] | None:
        cuts = [
            value
            for value in (raw.find(b"!!"), raw.find(b"::"), raw.find(b" "))
            if value >= 0
        ]
        separator = raw.find(b"::")
        if not cuts or separator < 0:
            return None
        node_id = _decode(raw[: min(cuts)])
        net_token = raw[separator + 2 :].split(None, 1)[0]
        if not node_id or not net_token:
            return None
        return node_id, _decode(net_token)

    def resolve_nodes(
        data: mmap.mmap,
        start: int,
        end: int,
        requested: set[str],
    ) -> None:
        missing = requested - set(nodes)
        if not missing:
            return
        for line_index, (_offset, raw) in enumerate(_iter_lines(data, start, end)):
            if line_index % 16384 == 0:
                reporter.check()
            if not raw.startswith(b"Node"):
                continue
            identity = node_id_and_net(raw)
            if identity is None:
                continue
            node_id, net = identity
            node_key = node_id.casefold()
            if node_key not in missing:
                continue
            attributes = _NODE_ATTR_RE.search(raw)
            layer_raw = _attribute(raw, b"Layer")
            if attributes is None or layer_raw is None:
                continue
            try:
                x_um = _length_um(attributes.group(1))
                y_um = _length_um(attributes.group(2))
            except ValueError:
                continue
            layer = _decode(layer_raw)
            nodes[node_key] = _RecoveredViaNode(
                node_id=node_id,
                net=net,
                x_um=x_um,
                y_um=y_um,
                layer=layer,
                padstack=(
                    _decode(_attribute(raw, b"PadStack"))
                    if _attribute(raw, b"PadStack") is not None
                    else _decode(attributes.group(3))
                    if attributes.group(3) is not None
                    else None
                ),
            )

    def traces_touching(
        data: mmap.mmap,
        start: int,
        end: int,
        pending: Mapping[str, list[tuple[str, str]]],
    ) -> set[tuple[str, str]]:
        touched: set[tuple[str, str]] = set()
        if start < 0 or end <= start:
            return touched
        for match_index, match in enumerate(_TRACE_RE.finditer(data, start, end)):
            if match_index % 8192 == 0:
                reporter.check()
            net_key = _decode(match.group(2)).casefold()
            first = _decode(match.group(3)).casefold()
            second = _decode(match.group(4)).casefold()
            for node_key in (first, second):
                for state_key in pending.get(node_key, ()):
                    if str(states[state_key]["net_key"]) == net_key:
                        touched.add(state_key)
        return touched

    evidence: dict[str, list[SpdViaPathEvidence]] = {}
    failures: Counter[str] = Counter()
    try:
        with source_path.open("rb") as handle, mmap.mmap(
            handle.fileno(), 0, access=mmap.ACCESS_READ
        ) as data:
            node_start = _find_line(data, b"* Node description lines")
            trace_start = _find_line(data, b"* Trace description lines")
            via_start = _find_line(data, b"* Via description lines")
            pad_start = _find_line(data, b"* PadStack collection description lines")
            if node_start < 0 or via_start < 0:
                diagnostics.append(
                    SpdDiagnostic(
                        "warning",
                        "SPD_VIA_PATH_FALLBACK",
                        "The SPD has no readable Node/Via section for per-landing "
                        "path recovery; terminal paths use the legacy rail template.",
                    )
                )
                return finish(SpdViaPathRecovery(
                    evidence_by_via={},
                    diagnostics=tuple(diagnostics),
                    statistics={
                        "requested": len(states),
                        "recovered": 0,
                        "fallback": len(states),
                    },
                ))
            node_end = trace_start if trace_start > node_start else via_start
            trace_end = via_start if via_start > trace_start else pad_start
            via_end = pad_start if pad_start > via_start else len(data)

            for _step in range(max_segments + 1):
                reporter.report(
                    min(98, 4 + round(94 * _step / max_segments)),
                    "Recovering source-proven vertical Via paths "
                    f"(pass {_step + 1}/{max_segments + 1})",
                )
                pending_by_node: dict[str, list[tuple[str, str]]] = {}
                for state_key, state in states.items():
                    if state["status"] != "PENDING":
                        continue
                    current_key = str(state["node_key"])
                    current_node = nodes.get(current_key)
                    if current_node is None:
                        state["status"] = "MISSING_NODE"
                        failures[str(state["status"])] += 1
                        continue
                    if current_node.layer.casefold() == str(state["target_key"]):
                        segments = tuple(state["segments"])
                        # Raw Node records commonly omit ``PadStack`` away from
                        # TOP and may name a component feature rather than the
                        # actual barrel that reaches this plane.  The last
                        # traversed Via segment is therefore unconditionally
                        # authoritative for target-pad geometry.
                        target_padstack_name = (
                            segments[-1].padstack if segments else None
                        )
                        padstack = (
                            padstack_by_key.get(target_padstack_name.casefold())
                            if target_padstack_name
                            else None
                        )
                        shape = next(
                            (
                                item
                                for item in (padstack.pad_shapes if padstack else ())
                                if item.layer.casefold()
                                == str(state["target_key"])
                                and item.kind in {"CIRCLE", "RECTANGLE"}
                                and item.width_um is not None
                                and item.height_um is not None
                            ),
                            None,
                        )
                        if not segments or shape is None or target_padstack_name is None:
                            state["status"] = "TARGET_PAD_UNSUPPORTED"
                            failures[str(state["status"])] += 1
                            continue
                        target_width_um = float(shape.width_um)
                        target_height_um = float(shape.height_um)
                        terminal_rotation = segments[-1].rotation_degrees % 360.0
                        if shape.kind == "RECTANGLE" and terminal_rotation in {
                            90.0,
                            270.0,
                        }:
                            target_width_um, target_height_um = (
                                target_height_um,
                                target_width_um,
                            )
                        item = SpdViaPathEvidence(
                            via_id=str(state["via_id"]),
                            target_layer=str(state["target_layer"]),
                            target_node_id=current_node.node_id,
                            target_padstack=target_padstack_name,
                            target_pad_kind=shape.kind,
                            target_pad_width_um=target_width_um,
                            target_pad_height_um=target_height_um,
                            target_x_um=current_node.x_um,
                            target_y_um=current_node.y_um,
                            segments=segments,
                        )
                        evidence.setdefault(str(state["via_key"]), []).append(item)
                        state["status"] = "RECOVERED"
                        continue
                    if len(state["segments"]) >= max_segments:
                        state["status"] = "SEGMENT_LIMIT"
                        failures[str(state["status"])] += 1
                        continue
                    pending_by_node.setdefault(current_key, []).append(state_key)
                if not pending_by_node:
                    break

                candidates: dict[
                    tuple[str, str], list[tuple[str, str, str, float | None]]
                ] = {
                    state_key: []
                    for state_keys in pending_by_node.values()
                    for state_key in state_keys
                }
                candidate_nodes: set[str] = set()
                for match_index, match in enumerate(
                    _VIA_RE.finditer(data, via_start, via_end)
                ):
                    if match_index % 8192 == 0:
                        reporter.check()
                    upper_key = _decode(match.group(3)).casefold()
                    lower_key = _decode(match.group(4)).casefold()
                    if upper_key not in pending_by_node and lower_key not in pending_by_node:
                        continue
                    via_id = _decode(match.group(1))
                    via_key = via_id.casefold()
                    net_key = _decode(match.group(2)).casefold()
                    padstack = _decode(match.group(5))
                    rotation_raw = _attribute(match.group(6), b"AbsoluteRotation")
                    try:
                        raw_rotation = float(rotation_raw) if rotation_raw else 0.0
                        if not isfinite(raw_rotation):
                            raise ValueError("non-finite Via rotation")
                        normalized_rotation = raw_rotation % 360.0
                        nearest_quadrant = round(normalized_rotation / 90.0) % 4
                        rotation = float(nearest_quadrant * 90)
                        if abs(normalized_rotation - rotation) > 1.0e-6 and not (
                            rotation == 0.0
                            and abs(normalized_rotation - 360.0) <= 1.0e-6
                        ):
                            rotation = None
                    except ValueError:
                        rotation = None
                    for current_key, next_key in ((upper_key, lower_key), (lower_key, upper_key)):
                        for state_key in pending_by_node.get(current_key, ()):
                            state = states[state_key]
                            if str(state["net_key"]) != net_key:
                                continue
                            if not state["segments"] and via_key != str(state["via_key"]):
                                continue
                            candidates[state_key].append(
                                (via_id, padstack, next_key, rotation)
                            )
                            candidate_nodes.add(next_key)
                resolve_nodes(data, node_start, node_end, candidate_nodes)
                trace_needed: dict[str, list[tuple[str, str]]] = {}
                for state_key, state_candidates in candidates.items():
                    state = states[state_key]
                    current = nodes.get(str(state["node_key"]))
                    assert current is not None
                    current_depth = depth_by_key.get(current.layer.casefold())
                    target_depth = depth_by_key.get(str(state["target_key"]))
                    valid: list[
                        tuple[str, str, _RecoveredViaNode, SpdPadStack, float]
                    ] = []
                    overshoot = False
                    missing_node = False
                    missing_padstack = False
                    unsupported_rotation = False
                    for via_id, padstack_name, next_key, rotation in state_candidates:
                        next_node = nodes.get(next_key)
                        definition = padstack_by_key.get(padstack_name.casefold())
                        if next_node is None:
                            missing_node = True
                            continue
                        if definition is None:
                            missing_padstack = True
                            continue
                        if rotation is None:
                            unsupported_rotation = True
                            continue
                        if (
                            next_node.net.casefold() != str(state["net_key"])
                            or definition.drill_diameter_um is None
                            or definition.drill_diameter_um <= 0
                            or current_depth is None
                            or target_depth is None
                        ):
                            continue
                        next_depth = depth_by_key.get(next_node.layer.casefold())
                        if next_depth is None:
                            continue
                        direction = 1 if target_depth > current_depth else -1
                        delta = direction * (next_depth - current_depth)
                        remaining = direction * (target_depth - next_depth)
                        if delta <= 0 or remaining < 0:
                            overshoot = True
                            continue
                        valid.append(
                            (via_id, padstack_name, next_node, definition, rotation)
                        )
                    unique_valid = {
                        (via_id.casefold(), next_node.node_id.casefold()): item
                        for item in valid
                        for via_id, _padstack, next_node, _definition, _rotation in (item,)
                    }
                    if len(unique_valid) != 1:
                        if len(unique_valid) > 1:
                            state["status"] = "AMBIGUOUS_VERTICAL_BRANCH"
                            failures[str(state["status"])] += 1
                        elif missing_node:
                            state["status"] = "MISSING_NODE"
                            failures[str(state["status"])] += 1
                        elif missing_padstack:
                            state["status"] = "MISSING_PADSTACK"
                            failures[str(state["status"])] += 1
                        elif unsupported_rotation:
                            state["status"] = "UNSUPPORTED_VIA_ROTATION"
                            failures[str(state["status"])] += 1
                        else:
                            trace_needed.setdefault(str(state["node_key"]), []).append(
                                state_key
                            )
                            state["overshoot"] = overshoot
                        continue
                    via_id, padstack_name, next_node, definition, rotation = next(
                        iter(unique_valid.values())
                    )
                    segment = SpdViaPathSegment(
                        via_id=via_id,
                        padstack=padstack_name,
                        drill_diameter_um=float(definition.drill_diameter_um),
                        start_layer=current.layer,
                        end_layer=next_node.layer,
                        length_um=abs(
                            float(depth_by_key[next_node.layer.casefold()])
                            - float(depth_by_key[current.layer.casefold()])
                        )
                        * 1.0,
                        end_x_um=next_node.x_um,
                        end_y_um=next_node.y_um,
                        rotation_degrees=rotation,
                    )
                    # Stack-up layer indices are monotonic but not physical
                    # distances.  Replace the span with the actual centre-depth
                    # distance below once the compact depth map is available.
                    state["segments"].append(segment)
                    state["node_id"] = next_node.node_id
                    state["node_key"] = next_node.node_id.casefold()
                    state["layer"] = next_node.layer
                if trace_needed:
                    touched = traces_touching(data, trace_start, trace_end, trace_needed)
                    for state_keys in trace_needed.values():
                        for state_key in state_keys:
                            state = states[state_key]
                            state["status"] = (
                                "TRACE_REQUIRED"
                                if state_key in touched
                                else "OVERSHOOT_OR_NO_MONOTONIC_VIA"
                            )
                            failures[str(state["status"])] += 1
            for state in states.values():
                if state["status"] == "PENDING":
                    state["status"] = "SEGMENT_LIMIT"
                    failures[str(state["status"])] += 1
    except OSError as exc:
        raise SpdImportError(
            f"cannot recover source Via paths from {source_path}: {exc}"
        ) from exc

    # Replace provisional layer-index spans with physical conductor centre spans.
    centre_depth_um: dict[str, float] = {}
    depth_um = 0.0
    for layer in stackup_layers:
        centre_depth_um[layer.name.casefold()] = depth_um + float(layer.thickness_um) / 2.0
        depth_um += float(layer.thickness_um)
    corrected: dict[str, list[SpdViaPathEvidence]] = {}
    for via_key, items in evidence.items():
        corrected[via_key] = []
        for item in items:
            segments = tuple(
                SpdViaPathSegment(
                    via_id=segment.via_id,
                    padstack=segment.padstack,
                    drill_diameter_um=segment.drill_diameter_um,
                    start_layer=segment.start_layer,
                    end_layer=segment.end_layer,
                    length_um=abs(
                        centre_depth_um[segment.end_layer.casefold()]
                        - centre_depth_um[segment.start_layer.casefold()]
                    ),
                    end_x_um=segment.end_x_um,
                    end_y_um=segment.end_y_um,
                    rotation_degrees=segment.rotation_degrees,
                )
                for segment in item.segments
            )
            if any(segment.length_um <= 0 for segment in segments):
                failures["ZERO_PHYSICAL_SPAN"] += 1
                continue
            corrected[via_key].append(
                SpdViaPathEvidence(
                    via_id=item.via_id,
                    target_layer=item.target_layer,
                    target_node_id=item.target_node_id,
                    target_padstack=item.target_padstack,
                    target_pad_kind=item.target_pad_kind,
                    target_pad_width_um=item.target_pad_width_um,
                    target_pad_height_um=item.target_pad_height_um,
                    target_x_um=item.target_x_um,
                    target_y_um=item.target_y_um,
                    segments=segments,
                )
            )
    recovered = sum(len(items) for items in corrected.values())
    requested = len(states)
    if recovered:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_VIA_PATH_RECOVERED",
                f"Recovered {recovered:,}/{requested:,} unique source-proven TOP-to-plane "
                "Via paths; only their compact segment summaries were retained.",
            )
        )
    if requested - recovered:
        failure_summary = ", ".join(
            f"{code}={count}" for code, count in sorted(failures.items())
        ) or "no unique source path"
        diagnostics.append(
            SpdDiagnostic(
                "warning",
                "SPD_VIA_PATH_LEGACY_FALLBACK",
                f"{requested - recovered:,} terminal path(s) use the documented "
                f"legacy rail template ({failure_summary}).",
            )
        )
    reporter.report(100, "Recovered source-proven vertical Via paths")
    statistics: dict[str, int] = {
        "requested": requested,
        "recovered": recovered,
        "fallback": requested - recovered,
        "segments": sum(
            len(item.segments) for items in corrected.values() for item in items
        ),
    }
    statistics.update(
        {
            f"failure_{code.casefold()}": int(count)
            for code, count in sorted(failures.items())
            if count
        }
    )
    return finish(SpdViaPathRecovery(
        evidence_by_via={
            key: tuple(sorted(items, key=lambda item: item.target_layer.casefold()))
            for key, items in sorted(corrected.items())
            if items
        },
        diagnostics=tuple(diagnostics),
        statistics=statistics,
    ))


def analyze_spd(
    path: str | Path,
    frequencies_hz: Iterable[float] = (1.0e3, 1.0e6, 1.0e9),
    gnd_aliases: Iterable[str] = ("DGND", "GND"),
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    scope: SpdAnalysisScope = "selected_pi",
) -> SpdAnalysis:
    """Analyze an SPD without materializing the source file in memory."""

    reporter = _Reporter(progress, is_cancelled)
    reporter.report(0, "Validating SPD source")
    source_path = Path(path)
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise SpdImportError(f"cannot stat SPD source {source_path}: {exc}") from exc
    if not source_path.is_file():
        raise SpdImportError(f"SPD source is not a file: {source_path}")
    if stat.st_size <= 0:
        raise SpdImportError("SPD source is empty")
    try:
        frequencies = tuple(float(value) for value in frequencies_hz)
    except (TypeError, ValueError) as exc:
        raise SpdImportError("frequencies_hz must contain numeric values") from exc
    if not frequencies or any(not isfinite(value) or value <= 0 for value in frequencies):
        raise SpdImportError("frequencies_hz must contain at least one finite positive value")
    ground_aliases = _unique(alias.strip() for alias in gnd_aliases if alias.strip())
    if not ground_aliases:
        raise SpdImportError("at least one ground alias is required")
    if scope not in {"selected_pi", "decap_scenario"}:
        raise SpdImportError(f"unsupported SPD analysis scope: {scope!r}")
    gnd_keys = {item.casefold() for item in ground_aliases}
    diagnostics: list[SpdDiagnostic] = []

    try:
        with source_path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            reporter.report(3, "Hashing SPD source")
            digest = hashlib.sha256(data).hexdigest()
            first_end = _line_end(data, 0, min(len(data), 16_384))
            title = _decode(data[0:first_end]).strip()
            source = SpdSourceInfo(source_path.resolve(), source_path.name, stat.st_size, stat.st_mtime_ns, digest, title)

            reporter.report(9, "Reading selected power/ground nets")
            selected_power, selected_ground = _parse_netlist(data, gnd_keys, diagnostics)
            if scope == "decap_scenario" and not selected_power:
                diagnostics.append(
                    SpdDiagnostic(
                        "error",
                        "SPD_POWER_NET_CLASSIFICATION_MISSING",
                        (
                            "Editable decap assignment requires at least one explicit "
                            ".NetList PowerNets declaration; positive shapes and non-GND "
                            "IO ports are not promoted to PWR rails automatically."
                        ),
                    )
                )
            configured_ground_keys = {
                item.casefold() for item in (*ground_aliases, *selected_ground)
            }
            selected_keys = {
                item.casefold() for item in (*selected_power, *selected_ground)
            }

            layer_marker = data.find(b"* Layer description lines")
            node_marker = data.find(b"* Node description lines")
            via_marker = data.find(b"* Via description lines")
            pad_marker = data.find(b"* PadStack collection description lines")
            material_marker = data.find(b"* Material description lines")
            circuit_marker = data.find(b"* Circuit description lines")
            first_shape = _find_line(data, b".Shape")
            shape_start = first_shape if first_shape >= 0 else 0
            shape_end = layer_marker if layer_marker > shape_start else (node_marker if node_marker > shape_start else len(data))

            reporter.report(11, "Reading material and TOP-layer identity")
            material_start = material_marker if material_marker >= 0 else 0
            material_end_marker = _find_line(data, b".EndMaterial", material_start)
            material_end = (
                len(data)
                if material_end_marker < 0
                else _line_end(data, material_end_marker, len(data))
            )
            dielectrics, metals = _parse_materials(
                data, material_start, material_end, diagnostics
            )
            layer_start = layer_marker if layer_marker >= 0 else shape_end
            layer_end = (
                node_marker
                if node_marker > layer_start
                else min(
                    (
                        value
                        for value in (via_marker, pad_marker, len(data))
                        if value > layer_start
                    ),
                    default=len(data),
                )
            )
            top_layer_hint = _first_conductor_layer_name(
                data, layer_start, layer_end, metals
            )

            # Scenario mode needs candidate rails beyond the .NetList selection,
            # but retaining every signal polygon in a production SPD would be
            # prohibitively expensive.  Index compact Part/Connect metadata first
            # and use it to constrain the single exact-geometry scan.
            prefetched: tuple[
                dict[str, PassiveSubcircuitModel],
                dict[str, str],
                dict[str, str],
                set[str],
                int,
                dict[str, _Part],
                dict[str, _Component],
                tuple[_Connection, ...],
            ] | None = None
            scenario_power_keys = {
                item.casefold() for item in selected_power
            }
            if scope == "decap_scenario":
                reporter.report(12, "Indexing decap and device candidate nets")
                quiet_reporter = _Reporter(None, is_cancelled)
                material_start_early = material_marker if material_marker >= 0 else 0
                material_end_marker_early = _find_line(
                    data, b".EndMaterial", material_start_early
                )
                material_end_early = (
                    len(data)
                    if material_end_marker_early < 0
                    else _line_end(data, material_end_marker_early, len(data))
                )
                circuit_start_early = (
                    circuit_marker if circuit_marker >= 0 else material_end_early
                )
                connect_start_early = _find_line(
                    data, b".Connect", circuit_start_early
                )
                circuit_end_early = (
                    connect_start_early
                    if connect_start_early > circuit_start_early
                    else len(data)
                )
                (
                    cap_models_early,
                    model_assets_early,
                    canonical_early,
                    empty_names_early,
                    partial_count_early,
                ) = _parse_partial_circuits(
                    data,
                    circuit_start_early,
                    circuit_end_early,
                    frequencies,
                    source_path.name,
                    quiet_reporter,
                    diagnostics,
                )
                metadata_start_early = (
                    connect_start_early
                    if connect_start_early >= 0
                    else circuit_start_early
                )
                comp_end_marker_early = _find_line(
                    data, b".EndCompCollection", metadata_start_early
                )
                metadata_end_early = (
                    len(data)
                    if comp_end_marker_early < 0
                    else _line_end(data, comp_end_marker_early, len(data))
                )
                parts_early, components_early, connections_early = _parse_metadata(
                    data,
                    metadata_start_early,
                    metadata_end_early,
                    diagnostics,
                )
                prefetched = (
                    cap_models_early,
                    model_assets_early,
                    canonical_early,
                    empty_names_early,
                    partial_count_early,
                    parts_early,
                    components_early,
                    connections_early,
                )

            reporter.report(14, "Scanning positive plane polygons")
            # Editable scenario rails are fail-closed to explicit .NetList
            # PowerNets declarations.  Unselected capacitor locations remain
            # visible inventory, but signal/non-declared shapes are not choices.
            scenario_geometry_keys = scenario_power_keys
            shape_selected_keys = (
                selected_keys
                if scope == "selected_pi"
                else scenario_geometry_keys | configured_ground_keys
            )
            outline, layer_nets, positive_nets, plane_geometries = _parse_shapes(
                data,
                shape_start,
                shape_end,
                shape_selected_keys,
                (
                    {item.casefold() for item in selected_power}
                    if scope == "selected_pi" and selected_power
                    else scenario_geometry_keys
                    if scope == "decap_scenario"
                    else None
                ),
                reporter,
                diagnostics,
                transient_geometry_keys=(
                    configured_ground_keys if scope == "decap_scenario" else None
                ),
                transient_geometry_layer=(
                    top_layer_hint if scope == "decap_scenario" else None
                ),
            )
            positive_keys = {item.casefold() for item in positive_nets}
            usable_power = _unique(
                (
                    *(
                        item
                        for item in selected_power
                        if item.casefold() in positive_keys
                    ),
                    *(
                        item
                        for item in positive_nets
                        if scope == "decap_scenario"
                        and item.casefold() in scenario_geometry_keys
                        and item.casefold() not in configured_ground_keys
                    ),
                )
            )
            usable_ground = _unique(
                (
                    *(
                        item
                        for item in selected_ground
                        if item.casefold() in positive_keys
                    ),
                    *(
                        item
                        for item in positive_nets
                        if item.casefold() in configured_ground_keys
                    ),
                )
            )
            if scope == "decap_scenario" and not usable_ground:
                diagnostics.append(
                    SpdDiagnostic(
                        "error",
                        "SPD_GROUND_PLANE_CLASSIFICATION_MISSING",
                        (
                            "Editable decap assignment requires an explicit GroundNets "
                            "or configured ground-alias shape that forms a PWR/GND pair."
                        ),
                    )
                )
            ground_keys = configured_ground_keys | {
                item.casefold() for item in usable_ground
            }
            if selected_power:
                missing_shapes = [item for item in selected_power if item.casefold() not in positive_keys]
                if missing_shapes:
                    diagnostics.append(SpdDiagnostic("warning", "SELECTED_NET_WITHOUT_POSITIVE_SHAPE", f"{len(missing_shapes)} selected power net(s) have no positive Shape polygon and may not form a usable plane."))
            plane_keys = (
                {item.casefold() for item in (*usable_power, *usable_ground)}
                if selected_power or scope == "decap_scenario"
                else positive_keys
            )

            reporter.report(30, "Building stack-up")
            layers = _parse_layers(data, layer_start, layer_end, layer_nets, dielectrics, metals, plane_keys, diagnostics)
            top_layer = next((item.name for item in layers if item.is_conductor), None)

            reporter.report(35, "Reading padstack definitions")
            pad_start = _find_line(data, b".PadStackDef", pad_marker if pad_marker >= 0 else 0)
            if pad_start < 0:
                pad_start = 0
            pad_end = material_marker if material_marker > pad_start else (circuit_marker if circuit_marker > pad_start else len(data))
            padstacks = _parse_padstacks(data, pad_start, pad_end, diagnostics)

            reporter.report(38, "Reading passive PartialCkt models")
            circuit_start = circuit_marker if circuit_marker >= 0 else material_end
            connect_start = _find_line(data, b".Connect", circuit_start)
            circuit_end = connect_start if connect_start > circuit_start else len(data)
            if prefetched is None:
                cap_models, model_assets, canonical, empty_names, partial_count = _parse_partial_circuits(data, circuit_start, circuit_end, frequencies, source_path.name, reporter, diagnostics)
            else:
                (
                    cap_models,
                    model_assets,
                    canonical,
                    empty_names,
                    partial_count,
                    _prefetched_parts,
                    _prefetched_components,
                    _prefetched_connections,
                ) = prefetched

            reporter.report(47, "Reading Part, Component, and Connect records")
            metadata_start = connect_start if connect_start >= 0 else circuit_start
            comp_end_marker = _find_line(data, b".EndCompCollection", metadata_start)
            metadata_end = len(data) if comp_end_marker < 0 else _line_end(data, comp_end_marker, len(data))
            if prefetched is None:
                parts, components, connections = _parse_metadata(data, metadata_start, metadata_end, diagnostics)
            else:
                parts = _prefetched_parts
                components = _prefetched_components
                connections = _prefetched_connections
            device_candidates, cap_candidates, referenced, skipped_unselected, ambiguous = _select_candidates(
                parts,
                components,
                connections,
                canonical,
                empty_names,
                plane_keys,
                {item.casefold() for item in selected_power},
                ground_keys,
                diagnostics,
                include_unmodeled_caps=scope == "decap_scenario",
                include_unselected_caps=scope == "decap_scenario",
            )

            reporter.report(57, "Resolving referenced Node coordinates")
            node_start = node_marker if node_marker >= 0 else 0
            node_end = via_marker if via_marker > node_start else (pad_marker if pad_marker > node_start else len(data))
            nodes = _parse_referenced_nodes(
                data,
                node_start,
                node_end,
                referenced,
                reporter,
                top_layer=top_layer,
            )
            pins, cap_instances, missing_nodes = _materialize_geometry(device_candidates, cap_candidates, nodes, diagnostics)
            if not any(pin.kind == PinKind.DEVICE_BUMP for pin in pins):
                diagnostics.append(SpdDiagnostic("warning", "DEVICE_PINS_NOT_FOUND", "No selected PWR/GND pins from a top-attached IO component were resolved."))

            reporter.report(82, "Counting selected-net vias")
            via_start = via_marker if via_marker >= 0 else node_end
            via_end = pad_marker if pad_marker > via_start else (material_marker if material_marker > via_start else len(data))
            connection_keys = {
                candidate.power.net.casefold()
                for candidate in cap_candidates
            } | {
                candidate.ground.net.casefold()
                for candidate in cap_candidates
            }
            (
                via_usage,
                total_vias,
                top_via_endpoints,
                uncertain_via_nets,
            ) = _parse_vias(
                data,
                via_start,
                via_end,
                plane_keys,
                connection_keys,
                nodes,
                reporter,
                top_layer=top_layer,
                padstacks=padstacks,
            )
            padstack_shapes = {
                item.name.casefold(): item.pad_shapes for item in padstacks
            }
            shared_pad = extract_shared_pad_connectivity(
                tuple(
                    DecapPadEvidence(
                        refdes=instance.refdes,
                        top_side=bool(
                            top_layer
                            and (
                                (instance.start_layer or "").casefold()
                                == top_layer.casefold()
                                or (instance.attach_layer or "")
                                .casefold()
                                .replace("_", "")
                                in {"topair", "airtop"}
                            )
                        ),
                        layer=instance.start_layer,
                        power_net=instance.power_net,
                        ground_net=instance.ground_net,
                        power_x_um=instance.power_x_um,
                        power_y_um=instance.power_y_um,
                        power_padstack=instance.power_padstack,
                        power_rotation_degrees=instance.power_pad_rotation_degrees,
                        ground_x_um=(
                            instance.ground_pad_x_um
                            if instance.ground_pad_x_um is not None
                            else instance.x_um
                        ),
                        ground_y_um=(
                            instance.ground_pad_y_um
                            if instance.ground_pad_y_um is not None
                            else instance.y_um
                        ),
                        ground_padstack=instance.ground_padstack,
                        ground_rotation_degrees=instance.ground_pad_rotation_degrees,
                        power_rotation_valid=instance.power_pad_rotation_valid,
                        ground_rotation_valid=instance.ground_pad_rotation_valid,
                    )
                    for instance in cap_instances
                ),
                top_via_endpoints,
                padstack_shapes,
                top_layer=top_layer,
                uncertain_via_nets=uncertain_via_nets,
                top_copper_geometries=tuple(
                    SpdTopCopperGeometry(
                        layer=geometry.layer,
                        net=geometry.net,
                        positive_polygons_um=geometry.positive_polygons_um,
                        negative_polygons_um=geometry.negative_polygons_um,
                        positive_circles_um=geometry.positive_circles_um,
                        negative_circles_um=geometry.negative_circles_um,
                        primitive_order=geometry.primitive_order,
                    )
                    for geometry in plane_geometries
                    if top_layer is not None
                    and geometry.layer.casefold() == top_layer.casefold()
                    and geometry.net.casefold() in connection_keys
                ),
            )
            diagnostics.extend(
                SpdDiagnostic("warning", "SPD_SHARED_PAD_UNRESOLVED", message)
                for message in shared_pad.warnings
            )
            if shared_pad.source_copper_power_edges:
                diagnostics.append(
                    SpdDiagnostic(
                        "info",
                        "SPD_SHARED_PAD_TOP_COPPER_LINKS",
                        (
                            "Used source-positive TOP copper geometry to add "
                            f"{shared_pad.source_copper_power_edges:,} PWR and "
                            f"{shared_pad.source_copper_ground_edges:,} GND shared-pad "
                            "edge(s) without a second SPD scan."
                        ),
                    )
                )

            persisted_plane_geometries = (
                tuple(
                    geometry
                    for geometry in plane_geometries
                    if geometry.net.casefold() in scenario_geometry_keys
                )
                if scope == "decap_scenario"
                else plane_geometries
            )

            counts = {
                "positive_plane_nets": len(positive_nets),
                "selected_plane_geometry_groups": len(persisted_plane_geometries),
                "selected_plane_boundary_polygons": sum(
                    len(item.positive_polygons_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_boundary_vertices": sum(
                    len(polygon)
                    for item in persisted_plane_geometries
                    for polygon in item.positive_polygons_um
                ),
                "selected_plane_negative_polygons": sum(
                    len(item.negative_polygons_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_positive_circles": sum(
                    len(item.positive_circles_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_negative_circles": sum(
                    len(item.negative_circles_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_polygon_traces": sum(
                    item.polygon_trace_count for item in persisted_plane_geometries
                ),
                "selected_plane_boxes": sum(
                    item.box_count for item in persisted_plane_geometries
                ),
                "selected_plane_subelements": sum(
                    item.positive_subelement_count
                    + item.negative_subelement_count
                    for item in persisted_plane_geometries
                ),
                "selected_power_nets": len(usable_power),
                "selected_ground_nets": len(usable_ground),
                "stackup_layers": len(layers),
                "partial_circuits": partial_count,
                "cap_models": len(cap_models),
                "connections": len(connections),
                "referenced_nodes": len(referenced),
                "resolved_nodes": len(referenced.intersection(nodes)),
                "retained_top_nodes": len(nodes) - len(referenced.intersection(nodes)),
                "unresolved_nodes": len(referenced - set(nodes)),
                "pins": len(pins),
                "device_pins": sum(pin.kind == PinKind.DEVICE_BUMP for pin in pins),
                "decap_pins": sum(pin.kind == PinKind.DECAP_PAD for pin in pins),
                "cap_instances": len(cap_instances),
                "mounted_cap_instances": sum(item.mounted for item in cap_instances),
                "empty_cap_instances": sum(not item.mounted for item in cap_instances),
                "skipped_unselected_cap_instances": skipped_unselected,
                "ambiguous_two_port_connections": ambiguous,
                "missing_referenced_pin_nodes": missing_nodes,
                "padstacks": len(padstacks),
                "vias": total_vias,
                "via_usage_groups": len(via_usage),
                "top_via_endpoints": len(top_via_endpoints),
                "shared_pad_clusters": len(shared_pad.clusters),
                "shared_pad_anchored_clusters": sum(
                    item.state == "ANCHORED" for item in shared_pad.clusters
                ),
                "shared_pad_floating_clusters": sum(
                    item.state == "FLOATING" for item in shared_pad.clusters
                ),
                "shared_pad_unresolved_decaps": sum(
                    item.kind == "UNRESOLVED" for item in shared_pad.connections
                ),
                "shared_pad_source_copper_power_edges": (
                    shared_pad.source_copper_power_edges
                ),
                "shared_pad_source_copper_ground_edges": (
                    shared_pad.source_copper_ground_edges
                ),
                "shared_pad_source_copper_members": (
                    shared_pad.source_copper_member_count
                ),
            }
            reporter.report(100, "SPD analysis complete")
            return SpdAnalysis(
                source=source,
                outline=outline,
                stackup_layers=layers,
                pins=pins,
                cap_models=cap_models,
                cap_instances=cap_instances,
                padstacks=padstacks,
                via_usage=via_usage,
                diagnostics=tuple(diagnostics),
                model_assets=model_assets,
                counts=counts,
                power_plane_nets=(
                    usable_power
                    if scope == "decap_scenario"
                    else usable_power
                    or tuple(
                        item
                        for item in positive_nets
                        if item.casefold() not in ground_keys
                    )
                ),
                ground_nets=usable_ground,
                plane_geometries=persisted_plane_geometries,
                decap_connections=shared_pad.connections,
                shared_pad_clusters=shared_pad.clusters,
            )
    except SpdImportError:
        raise
    except OSError as exc:
        raise SpdImportError(f"cannot read SPD source {source_path}: {exc}") from exc


__all__ = [
    "SpdAnalysis",
    "SpdAnalysisScope",
    "SpdCapInstance",
    "SpdDecapConnection",
    "SpdDiagnostic",
    "SpdImportError",
    "SpdPadStack",
    "SpdPadShape",
    "SpdPlaneGeometry",
    "SpdSourceInfo",
    "SpdSharedPadCluster",
    "SpdViaPathEvidence",
    "SpdViaPathRecovery",
    "SpdViaPathSegment",
    "SpdViaUsage",
    "analyze_spd",
    "recover_spd_via_paths",
]
