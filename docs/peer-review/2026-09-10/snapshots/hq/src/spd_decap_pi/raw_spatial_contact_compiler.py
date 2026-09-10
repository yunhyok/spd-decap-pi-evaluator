"""Streaming compiler for exact raw-SPD spatial-contact evidence.

The complete production SPD import calls this only after compiled-topology
certification.  It converts one stable raw SPD plus that certified project into
an independently versioned spatial-contact attachment; it never invokes a
solver or mutates its input project.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from fractions import Fraction
from hashlib import sha256
from itertools import zip_longest
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import zlib
from typing import TYPE_CHECKING, Any, Final

from ._core import services as core_services
from ._core.io.spd import (
    SpdTraceRecordError,
    _LENGTH_RE as _CORE_LENGTH_RE,
    _NODE_ATTR_RE as _CORE_NODE_ATTR_RE,
    _VIA_RE as _CORE_VIA_RE,
    _decimal_scaled_integer_exact,
    _iter_spd_trace_records,
    _length_pm_exact,
    _length_um,
)
from .raw_spatial_contact_asset import (
    MAX_RAW_SPATIAL_ROWS_PER_SECTION,
    MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES,
    RawSpatialLayerRow,
    RawSpatialNodeRow,
    RawSpatialPadShapeRow,
    RawSpatialPadstackRow,
    RawSpatialSectionCoverageRow,
    RawSpatialSourceCoverageRow,
    RawSpatialSurfaceRow,
    RawSpatialTraceRow,
    RawSpatialViaRow,
    build_raw_spatial_contact_asset,
    _plane_sheet_rows,
)
from .surface_certificate_asset import (
    SurfaceCertificateAssetError,
    validate_project_topology_storage_envelope,
)
from .source_plane_ownership_ir import (
    _SourcePlaneOwnershipSpool,
    _create_source_plane_ownership_v2_tables,
)

if TYPE_CHECKING:
    from ._core.domain import ProjectSpec
    from ._core.io.spd import SpdAnalysis


_LAYER_MARKER: Final = b"* Layer description lines"
_NODE_MARKER: Final = b"* Node description lines"
_TRACE_MARKER: Final = b"* Trace description lines"
_VIA_MARKER: Final = b"* Via description lines"
_WIREBOND_DEFINITION_MARKER: Final = b"* WirebondDefinition description lines"
_WIREBOND_GROUP_MARKER: Final = b"* WirebondGroup description lines"
_LEADFRAME_DEFINITION_MARKER: Final = b"* LeadframeDefinition description lines"
_LEADFRAME_GROUP_MARKER: Final = b"* LeadframeGroup description lines"
_PAD_MARKER: Final = b"* PadStack collection description lines"
_MATERIAL_MARKER: Final = b"* Material description lines"
_CIRCUIT_MARKER: Final = b"* Circuit description lines"
_KNOWN_MARKERS: Final = frozenset(
    {
        _LAYER_MARKER,
        _NODE_MARKER,
        _TRACE_MARKER,
        _VIA_MARKER,
        _WIREBOND_DEFINITION_MARKER,
        _WIREBOND_GROUP_MARKER,
        _LEADFRAME_DEFINITION_MARKER,
        _LEADFRAME_GROUP_MARKER,
        _PAD_MARKER,
        _MATERIAL_MARKER,
        _CIRCUIT_MARKER,
    }
)
_REQUIRED_MARKERS: Final = (
    _LAYER_MARKER,
    _NODE_MARKER,
    _TRACE_MARKER,
    _VIA_MARKER,
    _PAD_MARKER,
)
_MAX_LOGICAL_RECORD_BYTES: Final = 1024 * 1024
_MAX_LOGICAL_RECORD_LINES: Final = 64
_MAX_SHAPE_LOGICAL_RECORD_BYTES: Final = 64 * 1024 * 1024
_MAX_PADSTACK_BLOCK_BYTES: Final = 64 * 1024 * 1024
_MAX_GEOMETRY_UNCOMPRESSED_BYTES: Final = 64 * 1024 * 1024
_MAX_ROWS: Final = MAX_RAW_SPATIAL_ROWS_PER_SECTION
_MAX_BATCH_ROWS: Final = 10_000
_MAX_SOURCE_BYTES: Final = MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES
_MAX_SPOOL_BYTES: Final = MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES
_STREAM_BYTES: Final = 1024 * 1024
_MAX_EXACT_DECIMAL_LEXEME_BYTES: Final = 256
_SURFACE_DIGEST_DOMAIN: Final = b"spd-raw-surface-record-set-v1\0"
_PAD_SHAPE_DIGEST_DOMAIN: Final = b"spd-raw-pad-shape-record-set-v1\0"
_ISLAND_MANIFEST_SCHEMA: Final = "spd-raw-surface-island-manifest-v1"

_NODE_PRIMARY_RE = re.compile(
    rb"^(?P<id>Node[^\s:!]+)(?:!![^\s:]+)?(?:::(?P<net>\S+))?\s+(?P<body>.+)$"
)
_TRACE_METADATA_RE = re.compile(
    rb"^(?:ClippedTrace|SegmentedTrace) = [0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{8}){3}$"
)
_VIA_PRIMARY_RE = re.compile(rb"^(?P<id>Via[^\s:]+)::(?P<net>\S+)\s+(?P<body>.+)$")
_VIA_NO_ANTIPAD_RE = re.compile(
    rb"(?:^|[ \t])NoAntiPadLayers[ \t]*=[ \t]*(?P<layers>.*)$"
)
_ENDPOINT_RE = re.compile(rb"^(?P<id>Node[^\s:!]+)(?:!![^\s:]+)?(?:::(?P<net>\S+))?$")
_ATTRIBUTE_RE = re.compile(rb"(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=\s*(?P<value>\S+)")
_LAYER_RE = re.compile(
    rb"^(?P<id>\S+)\s+Thickness\s*=\s*(?P<thickness>\S+)(?P<tail>.*)$",
    re.IGNORECASE,
)
_LAYER_PATCH_RE = re.compile(
    rb"^Patch(?P<id>\S+)\s+Shape\s*=\s*(?P<shape>\S+)\s+Layer\s*=\s*(?P<layer>\S+)\s*$",
    re.IGNORECASE,
)
_PADSTACK_RE = re.compile(rb"^\.PadStackDef\s+(?P<id>\S+)(?P<tail>.*)$", re.IGNORECASE)
_PADDEF_RE = re.compile(rb"^\.PadDef\s+(?P<layer>\S+)\s*$", re.IGNORECASE)
_REGULAR_RE = re.compile(rb"^Regular\s+(?P<kind>\S+)(?P<tail>.*)$", re.IGNORECASE)
_LENGTH_TOKEN_RE = _CORE_LENGTH_RE
_EXACT_LENGTH_RE = re.compile(
    rb"^(?P<number>[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))"
    rb"(?:[eE][+-]?\d+)?)\s*(?P<unit>mil|mm|um|u|m)$",
    re.IGNORECASE,
)
_SHAPE_HEADER_RE = re.compile(rb"^\.Shape[ \t]+(?P<layer>\S+)", re.IGNORECASE)
_SHAPE_PRIMITIVE_RE = re.compile(
    rb"^(?P<kind>[A-Za-z_]+)[^\s:]*::(?P<net>\S+?)(?P<polarity>[+-])(?:\s+|$)"
)
_SHAPE_CONTINUATION_KINDS: Final = frozenset({b"Polygon", b"PolygonTrace"})


MAX_SOURCE_PLANE_OWNERSHIP_SELECTION_ROWS: Final = 150_000


class RawSpatialCompilerError(ValueError):
    """Fail-closed compiler error with a stable diagnostic code."""

    def __init__(self, code: str, message: str, *, source_offset: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.source_offset = source_offset


def _fail(code: str, message: str, *, offset: int | None = None) -> None:
    raise RawSpatialCompilerError(code, message, source_offset=offset)


@dataclass(frozen=True, slots=True)
class _SourcePlaneOwnershipRawEvidence:
    """Bounded hand-off from the raw pass to the ownership IR producer."""

    raw_manifest: Mapping[str, Any]
    raw_generated: tuple[str, bytes]
    spool: _SourcePlaneOwnershipSpool


@dataclass(frozen=True, slots=True)
class _SourceSpan:
    name: str
    marker: bytes
    start: int
    end: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _LogicalRecord:
    ordinal: int
    source_offset: int
    exact_bytes: bytes
    line_offsets: tuple[int, ...]
    source_sha256: str


def _without_ending(raw: bytes) -> bytes:
    if raw.endswith(b"\r\n"):
        return raw[:-2]
    if raw.endswith((b"\r", b"\n")):
        return raw[:-1]
    return raw


def _bounded_lines(handle: Any, start: int, end: int) -> Iterator[tuple[int, bytes]]:
    handle.seek(start)
    while handle.tell() < end:
        offset = handle.tell()
        remaining = end - offset
        raw = handle.readline(min(remaining, _MAX_LOGICAL_RECORD_BYTES + 1))
        if not raw:
            return
        carriage = raw.find(b"\r")
        newline = raw.find(b"\n")
        if carriage >= 0 and (newline < 0 or carriage < newline):
            stop = carriage + 1
            if newline == stop:
                stop += 1
            if stop < len(raw):
                handle.seek(offset + stop)
                raw = raw[:stop]
        if len(raw) > _MAX_LOGICAL_RECORD_BYTES:
            _fail(
                "RAW_SPATIAL_PHYSICAL_LINE_BOUND_EXCEEDED",
                "raw SPD physical line exceeds 1 MiB",
                offset=offset,
            )
        if offset + len(raw) > end:
            raw = raw[: end - offset]
        yield offset, raw


def _stream_sha256(path: Path, start: int, end: int, cancelled: Callable[[], bool]) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start
        while remaining:
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            chunk = handle.read(min(_STREAM_BYTES, remaining))
            if not chunk:
                _fail("RAW_SPATIAL_SOURCE_TRUNCATED", "raw SPD ended inside a declared interval")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _source_state(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))


def _snapshot_source(
    source: Path,
    destination: Path,
    expected_size: int,
    cancelled: Callable[[], bool],
) -> str:
    """Copy one content-addressed source snapshot for every subsequent parse."""

    digest = sha256()
    copied = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        while copied < expected_size:
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            chunk = reader.read(min(_STREAM_BYTES, expected_size - copied))
            if not chunk:
                _fail("RAW_SPATIAL_SOURCE_TRUNCATED", "raw SPD changed while snapshotting")
            writer.write(chunk)
            digest.update(chunk)
            copied += len(chunk)
        if reader.read(1):
            _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD grew while snapshotting")
    if copied != expected_size:
        _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD size changed while snapshotting")
    return digest.hexdigest()


def _index_markers(path: Path, size: int, cancelled: Callable[[], bool]) -> dict[bytes, int]:
    offsets: dict[bytes, int] = {}
    with path.open("rb") as handle:
        for offset, raw in _bounded_lines(handle, 0, size):
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            line = _without_ending(raw)
            if line not in _KNOWN_MARKERS:
                continue
            if line in offsets:
                _fail(
                    "RAW_SPATIAL_SECTION_DUPLICATE",
                    f"raw SPD marker {line!r} occurs more than once",
                    offset=offset,
                )
            offsets[line] = offset
    missing = [marker for marker in _REQUIRED_MARKERS if marker not in offsets]
    if missing:
        _fail("RAW_SPATIAL_SECTION_MISSING", f"raw SPD marker {missing[0]!r} is absent")
    ordered = [offsets[marker] for marker in _REQUIRED_MARKERS]
    if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
        _fail("RAW_SPATIAL_SECTION_ORDER_INVALID", "raw SPD spatial sections are reordered")
    return offsets


def _section_spans(
    path: Path, size: int, offsets: Mapping[bytes, int], cancelled: Callable[[], bool]
) -> tuple[_SourceSpan, ...]:
    via_start = offsets[_VIA_MARKER]
    via_end = min(
        offset
        for marker, offset in offsets.items()
        if marker
        in {
            _WIREBOND_DEFINITION_MARKER,
            _WIREBOND_GROUP_MARKER,
            _LEADFRAME_DEFINITION_MARKER,
            _LEADFRAME_GROUP_MARKER,
            _PAD_MARKER,
        }
        and offset > via_start
    )
    if via_end < offsets[_PAD_MARKER]:
        with path.open("rb") as handle:
            for offset, raw in _bounded_lines(
                handle, via_end, offsets[_PAD_MARKER]
            ):
                if cancelled():
                    _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
                if _without_ending(raw).startswith(b"Via"):
                    _fail(
                        "RAW_SPATIAL_SECTION_ORDER_INVALID",
                        "Via record occurs after an intermediate section marker",
                        offset=offset,
                    )
    definitions = (
        ("Node", _NODE_MARKER, offsets[_NODE_MARKER], offsets[_TRACE_MARKER]),
        ("Trace", _TRACE_MARKER, offsets[_TRACE_MARKER], offsets[_VIA_MARKER]),
        ("Via", _VIA_MARKER, via_start, via_end),
    )
    spans: list[_SourceSpan] = []
    for name, marker, start, end in definitions:
        if not 0 <= start < end <= size:
            _fail("RAW_SPATIAL_SECTION_INTERVAL_INVALID", f"{name} section interval is invalid")
        spans.append(
            _SourceSpan(name, marker, start, end, _stream_sha256(path, start, end, cancelled))
        )
    return tuple(spans)


def _frame_section(handle: Any, span: _SourceSpan, prefix: bytes) -> Iterator[_LogicalRecord]:
    current_offset: int | None = None
    current_lines: list[bytes] = []
    current_offsets: list[int] = []
    ordinal = 0
    marker_seen = False

    def finish() -> _LogicalRecord:
        nonlocal ordinal, current_offset, current_lines, current_offsets
        assert current_offset is not None
        exact = b"".join(current_lines)
        result = _LogicalRecord(
            ordinal,
            current_offset,
            exact,
            tuple(current_offsets),
            sha256(exact).hexdigest(),
        )
        ordinal += 1
        current_offset = None
        current_lines = []
        current_offsets = []
        return result

    for offset, raw in _bounded_lines(handle, span.start, span.end):
        physical = _without_ending(raw)
        if not marker_seen:
            if physical != span.marker or offset != span.start:
                _fail(
                    "RAW_SPATIAL_SECTION_MARKER_INVALID",
                    f"{span.name} interval does not start at its marker",
                    offset=offset,
                )
            marker_seen = True
            continue
        continuation = physical.lstrip().startswith(b"+")
        if continuation:
            if current_offset is None:
                _fail(
                    "RAW_SPATIAL_ORPHAN_CONTINUATION",
                    f"orphan {span.name} continuation",
                    offset=offset,
                )
            if len(current_lines) >= _MAX_LOGICAL_RECORD_LINES:
                _fail(
                    "RAW_SPATIAL_RECORD_LINE_BOUND_EXCEEDED",
                    f"{span.name} logical record exceeds 64 lines",
                    offset=current_offset,
                )
            if sum(map(len, current_lines)) + len(raw) > _MAX_LOGICAL_RECORD_BYTES:
                _fail(
                    "RAW_SPATIAL_RECORD_BYTE_BOUND_EXCEEDED",
                    f"{span.name} logical record exceeds 1 MiB",
                    offset=current_offset,
                )
            current_lines.append(raw)
            current_offsets.append(offset)
            continue
        if current_offset is not None:
            yield finish()
        if not physical.strip():
            continue
        if prefix == b"Trace" and _TRACE_METADATA_RE.fullmatch(physical):
            continue
        if not physical.startswith(prefix):
            _fail(
                "RAW_SPATIAL_SECTION_GRAMMAR_INVALID",
                f"unexpected nonblank primary in {span.name} section",
                offset=offset,
            )
        current_offset = offset
        current_lines = [raw]
        current_offsets = [offset]
    if current_offset is not None:
        yield finish()
    if not marker_seen:
        _fail("RAW_SPATIAL_SECTION_MARKER_INVALID", f"{span.name} marker was not framed")


def _decode_token(raw: bytes, label: str, *, offset: int) -> str:
    try:
        value = raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        _fail("RAW_SPATIAL_TEXT_INVALID", f"{label} is not UTF-8", offset=offset)
        raise AssertionError from exc
    if not value or any(ord(character) < 32 for character in value):
        _fail("RAW_SPATIAL_TEXT_INVALID", f"{label} is empty or contains controls", offset=offset)
    if len(value.encode("utf-8")) > 16 * 1024:
        _fail("RAW_SPATIAL_TEXT_INVALID", f"{label} exceeds its text bound", offset=offset)
    return value


def _logical_payload(record: _LogicalRecord) -> bytes:
    lines = record.exact_bytes.splitlines(keepends=True)
    parts = [_without_ending(lines[0]).strip()]
    for line in lines[1:]:
        continuation = _without_ending(line).lstrip()
        if not continuation.startswith(b"+"):
            _fail(
                "RAW_SPATIAL_RECORD_INVALID",
                "logical record continuation differs",
                offset=record.source_offset,
            )
        parts.append(continuation[1:].strip())
    return b" ".join(part for part in parts if part)


def _attributes(body: bytes, allowed: set[str], *, offset: int) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    cursor = 0
    while cursor < len(body):
        while cursor < len(body) and body[cursor : cursor + 1] in b" \t":
            cursor += 1
        if cursor == len(body):
            break
        match = _ATTRIBUTE_RE.match(body, cursor)
        if match is None:
            _fail(
                "RAW_SPATIAL_ATTRIBUTE_INVALID",
                "record contains an unframed attribute",
                offset=offset,
            )
        name = match.group("name").decode("ascii", errors="strict").casefold()
        if name not in allowed:
            _fail(
                "RAW_SPATIAL_ATTRIBUTE_UNKNOWN",
                f"record attribute {name!r} is unsupported",
                offset=offset,
            )
        if name in result:
            _fail(
                "RAW_SPATIAL_ATTRIBUTE_DUPLICATE",
                f"record attribute {name!r} is duplicated",
                offset=offset,
            )
        result[name] = match.group("value")
        cursor = match.end()
    return result


def _rotation_microdegrees(token: bytes, *, offset: int) -> int:
    try:
        raw = _decimal_scaled_integer_exact(
            token, 1_000_000, label="rotation in microdegrees"
        )
    except (DecimalException, OverflowError, UnicodeDecodeError, ValueError) as exc:
        _fail(
            "RAW_SPATIAL_ROTATION_INVALID",
            f"rotation is not an exact finite microdegree: {exc}",
            offset=offset,
        )
    return ((raw + 180_000_000) % 360_000_000) - 180_000_000


def _pm(token: bytes, label: str, *, offset: int, positive: bool = False) -> int:
    numeric: Decimal | None = None
    try:
        match = _EXACT_LENGTH_RE.fullmatch(token.strip())
        if match is not None:
            numeric = Decimal(match.group("number").decode("ascii"))
        value = _length_pm_exact(token)
    except (DecimalException, OverflowError, UnicodeDecodeError, ValueError) as exc:
        _fail("RAW_SPATIAL_LENGTH_INVALID", f"{label} is invalid: {exc}", offset=offset)
    if numeric is not None and numeric != 0 and value == 0:
        _fail(
            "RAW_SPATIAL_LENGTH_INVALID",
            f"{label} is a nonzero Decimal below the exact picometre range",
            offset=offset,
        )
    if positive and value <= 0:
        _fail("RAW_SPATIAL_LENGTH_INVALID", f"{label} must be positive", offset=offset)
    return value


def _double_pm(value: int, label: str, *, offset: int) -> int:
    if type(value) is not int or value < 0 or value > (2**63 - 1) // 2:
        _fail(
            "RAW_SPATIAL_LENGTH_INVALID",
            f"{label} diameter exceeds the signed 64-bit picometre bound",
            offset=offset,
        )
    return 2 * value


def _core_length_um(token: bytes, label: str, *, offset: int) -> float:
    """Replay the tracked parser's binary64 length conversion exactly."""

    try:
        value = _length_um(token)
    except (OverflowError, UnicodeDecodeError, ValueError) as exc:
        _fail(
            "RAW_SPATIAL_SOURCE_FLOAT_REPLAY_INVALID",
            f"{label} cannot be replayed by the tracked SPD parser: {exc}",
            offset=offset,
        )
    if type(value) is not float or not math.isfinite(value):
        _fail(
            "RAW_SPATIAL_SOURCE_FLOAT_REPLAY_INVALID",
            f"{label} did not produce one finite binary64 value",
            offset=offset,
        )
    return value


def _analysis_float(value: object, label: str, *, code: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        _fail(code, f"{label} is not one finite binary64 value")
    return value


def _optional_float_matches(
    actual: object,
    expected: float | None,
    label: str,
    *,
    code: str,
) -> bool:
    if expected is None:
        return actual is None
    return (
        _analysis_float(actual, label, code=code).hex()
        == expected.hex()
    )


@dataclass(frozen=True, slots=True)
class _PadstackFloatEvidence:
    name: str
    drill_diameter_um: float | None
    pad_width_um: float | None
    pad_height_um: float | None
    layers: tuple[str, ...]
    material: str | None
    shapes: tuple[
        tuple[str, str, float | None, float | None, str | None], ...
    ]


def _parse_node(
    record: _LogicalRecord,
    layer_by_fold: Mapping[str, str],
    padstack_by_fold: Mapping[str, str],
) -> tuple[Any, ...]:
    if len(record.line_offsets) != 1:
        _fail(
            "RAW_SPATIAL_NODE_PARSER_MISMATCH",
            "Node continuations are not consumed by the tracked SPD parser",
            offset=record.source_offset,
        )
    payload = _logical_payload(record)
    match = _NODE_PRIMARY_RE.fullmatch(payload)
    if match is None:
        _fail(
            "RAW_SPATIAL_NODE_MALFORMED",
            "Node logical record is malformed",
            offset=record.source_offset,
        )
    node_id = _decode_token(match.group("id"), "Node id", offset=record.source_offset)
    explicit_net = (
        _decode_token(match.group("net"), "Node net", offset=record.source_offset)
        if match.group("net") is not None
        else None
    )
    body = match.group("body")
    # PowerSI annotates some package-shape Nodes with a leading bare
    # ``PolygonVertex`` token.  The tracked core parser searches X/Y/LAYER
    # attributes within the full record, so strip only this known framing
    # marker before strict attribute validation; every other unframed token
    # remains fail-closed in _attributes.
    if body.startswith(b"PolygonVertex") and (
        len(body) == len(b"PolygonVertex")
        or body[len(b"PolygonVertex") : len(b"PolygonVertex") + 1]
        in b" \t"
    ):
        body = body[len(b"PolygonVertex") :].lstrip()
    attrs = _attributes(
        body,
        {"x", "y", "layer", "padstack", "absoluterotation"},
        offset=record.source_offset,
    )
    missing = {"x", "y", "layer"} - set(attrs)
    if missing:
        _fail(
            "RAW_SPATIAL_NODE_MALFORMED",
            f"Node is missing {sorted(missing)!r}",
            offset=record.source_offset,
        )
    primary = _without_ending(record.exact_bytes)
    tracked = _CORE_NODE_ATTR_RE.search(primary)
    if (
        tracked is None
        or tracked.group(1) != attrs["x"]
        or tracked.group(2) != attrs["y"]
    ):
        _fail(
            "RAW_SPATIAL_NODE_PARSER_MISMATCH",
            "Node geometry differs from the tracked SPD parser grammar",
            offset=record.source_offset,
        )
    layer_raw = _decode_token(attrs["layer"], "Node layer", offset=record.source_offset)
    layer = layer_by_fold.get(layer_raw.casefold())
    if layer is None:
        _fail(
            "RAW_SPATIAL_LAYER_UNRESOLVED",
            f"Node layer {layer_raw!r} is absent",
            offset=record.source_offset,
        )
    padstack: str | None = None
    rotation: int | None = None
    if "padstack" in attrs:
        raw_padstack = _decode_token(
            attrs["padstack"], "Node padstack", offset=record.source_offset
        )
        padstack = padstack_by_fold.get(raw_padstack.casefold())
        if padstack is None:
            _fail(
                "RAW_SPATIAL_PADSTACK_UNRESOLVED",
                f"Node padstack {raw_padstack!r} is absent",
                offset=record.source_offset,
            )
        rotation = _rotation_microdegrees(
            attrs.get("absoluterotation", b"0"), offset=record.source_offset
        )
    elif "absoluterotation" in attrs:
        _fail(
            "RAW_SPATIAL_NODE_MALFORMED",
            "Node rotation exists without Node padstack",
            offset=record.source_offset,
        )
    return (
        record.ordinal,
        node_id,
        node_id.casefold(),
        explicit_net,
        explicit_net.casefold() if explicit_net is not None else None,
        _pm(attrs["x"], "Node X", offset=record.source_offset),
        _pm(attrs["y"], "Node Y", offset=record.source_offset),
        layer,
        layer.casefold(),
        padstack,
        rotation,
        record.source_sha256,
    )


def _endpoint(token: bytes, label: str, *, offset: int) -> tuple[str, str | None]:
    match = _ENDPOINT_RE.fullmatch(token)
    if match is None:
        _fail("RAW_SPATIAL_ENDPOINT_MALFORMED", f"{label} endpoint is malformed", offset=offset)
    return (
        _decode_token(match.group("id"), f"{label} Node id", offset=offset),
        (
            _decode_token(match.group("net"), f"{label} Node net", offset=offset)
            if match.group("net") is not None
            else None
        ),
    )


def _parse_via(
    record: _LogicalRecord,
    padstack_by_fold: Mapping[str, str],
    layer_by_fold: Mapping[str, str],
) -> tuple[Any, ...]:
    payload = _logical_payload(record)
    match = _VIA_PRIMARY_RE.fullmatch(payload)
    if match is None:
        _fail(
            "RAW_SPATIAL_VIA_MALFORMED",
            "Via logical record is malformed",
            offset=record.source_offset,
        )
    via_id = _decode_token(match.group("id"), "Via id", offset=record.source_offset)
    net = _decode_token(match.group("net"), "Via net", offset=record.source_offset)
    body = match.group("body")
    no_antipad = _VIA_NO_ANTIPAD_RE.search(body)
    if no_antipad is not None:
        raw_layers = no_antipad.group("layers")
        layer_tokens = raw_layers.split()
        if not layer_tokens:
            _fail(
                "RAW_SPATIAL_ATTRIBUTE_INVALID",
                "NoAntiPadLayers requires one or more layers",
                offset=record.source_offset,
            )
        seen_layers: set[str] = set()
        for raw_layer in layer_tokens:
            if raw_layer == b"NoAntiPadLayers":
                _fail(
                    "RAW_SPATIAL_ATTRIBUTE_DUPLICATE",
                    "Via supplies duplicate NoAntiPadLayers suffix",
                    offset=record.source_offset,
                )
            layer_name = _decode_token(
                raw_layer, "NoAntiPadLayers layer", offset=record.source_offset
            )
            layer_fold = layer_name.casefold()
            if layer_fold in seen_layers:
                _fail(
                    "RAW_SPATIAL_ATTRIBUTE_DUPLICATE",
                    f"Via repeats NoAntiPadLayers layer {layer_name!r}",
                    offset=record.source_offset,
                )
            if layer_fold not in layer_by_fold:
                _fail(
                    "RAW_SPATIAL_LAYER_UNRESOLVED",
                    f"NoAntiPadLayers layer {layer_name!r} is absent",
                    offset=record.source_offset,
                )
            seen_layers.add(layer_fold)
        body = body[: no_antipad.start()].rstrip()
    attrs = _attributes(
        body,
        {"uppernode", "lowernode", "padstack", "absoluterotation", "rotation"},
        offset=record.source_offset,
    )
    missing = {"uppernode", "lowernode", "padstack"} - set(attrs)
    if missing:
        _fail(
            "RAW_SPATIAL_VIA_MALFORMED",
            f"Via is missing {sorted(missing)!r}",
            offset=record.source_offset,
        )
    if "absoluterotation" in attrs and "rotation" in attrs:
        _fail(
            "RAW_SPATIAL_ROTATION_DUPLICATE",
            "Via supplies both Rotation and AbsoluteRotation",
            offset=record.source_offset,
        )
    if "rotation" in attrs:
        _fail(
            "RAW_SPATIAL_VIA_PARSER_MISMATCH",
            "Rotation-only Via evidence is ignored by the tracked SPD parser",
            offset=record.source_offset,
        )
    upper_id, upper_net = _endpoint(attrs["uppernode"], "upper", offset=record.source_offset)
    lower_id, lower_net = _endpoint(attrs["lowernode"], "lower", offset=record.source_offset)
    for endpoint_net in (upper_net, lower_net):
        if endpoint_net is not None and endpoint_net.casefold() != net.casefold():
            _fail(
                "RAW_SPATIAL_ENDPOINT_NET_MISMATCH",
                "Via endpoint net differs from Via net",
                offset=record.source_offset,
            )
    raw_padstack = _decode_token(attrs["padstack"], "Via padstack", offset=record.source_offset)
    padstack = padstack_by_fold.get(raw_padstack.casefold())
    if padstack is None:
        _fail(
            "RAW_SPATIAL_PADSTACK_UNRESOLVED",
            f"Via padstack {raw_padstack!r} is absent",
            offset=record.source_offset,
        )
    primary = record.exact_bytes.splitlines(keepends=True)[0]
    tracked = _CORE_VIA_RE.fullmatch(_without_ending(primary))
    if tracked is None:
        _fail(
            "RAW_SPATIAL_VIA_PARSER_MISMATCH",
            "Via identity/order differs from the tracked SPD parser grammar",
            offset=record.source_offset,
        )
    tracked_fields = tuple(
        _decode_token(value, "tracked Via field", offset=record.source_offset)
        for value in tracked.groups()[:5]
    )
    if (
        tracked_fields[:4] != (via_id, net, upper_id, lower_id)
        or tracked_fields[4].casefold() != padstack.casefold()
    ):
        _fail(
            "RAW_SPATIAL_VIA_PARSER_MISMATCH",
            "Via fields differ from the tracked SPD parser result",
            offset=record.source_offset,
        )
    return (
        record.ordinal,
        via_id,
        via_id.casefold(),
        net,
        net.casefold(),
        upper_id,
        upper_id.casefold(),
        lower_id,
        lower_id.casefold(),
        padstack,
        padstack.casefold(),
        _rotation_microdegrees(
            attrs.get("absoluterotation", b"0"),
            offset=record.source_offset,
        ),
        record.source_sha256,
    )


def _project_spd_import(project: Any) -> Mapping[str, Any]:
    metadata = (
        project.get("metadata")
        if isinstance(project, Mapping)
        else getattr(project, "metadata", None)
    )
    if not isinstance(metadata, Mapping) or not isinstance(metadata.get("spd_import"), Mapping):
        _fail("RAW_SPATIAL_PROJECT_INVALID", "project SPD import metadata is absent")
    return metadata["spd_import"]


def _plane_sheet_payload(project: Any, analysis: Any) -> Mapping[str, Any]:
    stackup = tuple(project.stackup_layers)
    if not stackup:
        _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "typed ProjectSpec stackup is absent")
    metadata = project.metadata
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    records = spd_import.get("plane_geometries") if isinstance(spd_import, Mapping) else None
    if not isinstance(records, (list, tuple)):
        _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "canonical plane geometry records are absent")
    record_keys: list[tuple[str, str]] = []
    record_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in records:
        if not isinstance(row, Mapping):
            _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "canonical plane geometry record is invalid")
        key = (str(row.get("layer")), str(row.get("net")))
        if not key[0] or not key[1] or key in record_by_key:
            _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "canonical plane geometry keys are not unique")
        record_keys.append(key)
        record_by_key[key] = row
    geometries = tuple(getattr(analysis, "plane_geometries", ()))
    analysis_keys = [(str(getattr(item, "layer", "")), str(getattr(item, "net", ""))) for item in geometries]
    if len(set(analysis_keys)) != len(analysis_keys) or set(analysis_keys) != set(record_keys):
        _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "plane geometry identities differ from analysis")
    primitives: list[dict[str, Any]] = []
    vertices: list[dict[str, Any]] = []
    circles: list[dict[str, Any]] = []
    for geometry in geometries:
        key = (str(getattr(geometry, "layer", "")), str(getattr(geometry, "net", "")))
        record = record_by_key.get(key)
        if not isinstance(record, Mapping) or not isinstance(record.get("asset"), str) or not isinstance(record.get("asset_sha256"), str):
            _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "canonical source geometry binding is incomplete")
        order = tuple(getattr(geometry, "primitive_order", ()))
        arrays = {
            "positive_polygon": tuple(getattr(geometry, "positive_polygons_um", ())),
            "negative_polygon": tuple(getattr(geometry, "negative_polygons_um", ())),
            "positive_circle": tuple(getattr(geometry, "positive_circles_um", ())),
            "negative_circle": tuple(getattr(geometry, "negative_circles_um", ())),
        }
        for kind_name, index in order:
            if kind_name not in arrays or type(index) is not int or not 0 <= index < len(arrays[kind_name]):
                _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "canonical primitive order is invalid")
            polarity = "+" if kind_name.startswith("positive") else "-"
            kind = "circle" if kind_name.endswith("circle") else "polygon"
            primitive_ordinal = len(primitives)
            primitive = {"primitive_ordinal": primitive_ordinal, "layer_ordinal": next((i for i, item in enumerate(stackup) if item.name == key[0]), -1), "layer_name": key[0], "net_name": key[1], "polarity": polarity, "kind": kind, "source_asset_name": record["asset"], "source_asset_sha256": record["asset_sha256"], "coordinate_unit": "um"}
            if primitive["layer_ordinal"] < 0:
                _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "primitive layer is absent from ProjectSpec stackup")
            try:
                shape = arrays[kind_name][index]
                if kind == "polygon":
                    primitive_values = {"vertices": tuple(tuple(point) for point in shape)}
                    primitive["primitive_sha256"] = _canonical_sha(primitive_values | {"kind": kind_name})
                    for vertex_ordinal, (x_um, y_um) in enumerate(shape):
                        vertices.append({"primitive_ordinal": primitive_ordinal, "vertex_ordinal": vertex_ordinal, "x_um": x_um, "y_um": y_um})
                else:
                    center_x, center_y, radius = shape
                    primitive_values = {"circle": tuple(shape)}
                    primitive["primitive_sha256"] = _canonical_sha(primitive_values | {"kind": kind_name})
                    circles.append({"primitive_ordinal": primitive_ordinal, "center_x_um": center_x, "center_y_um": center_y, "radius_um": radius})
            except (TypeError, ValueError, OverflowError):
                _fail("RAW_SPATIAL_PLANE_SHEET_INVALID", "plane primitive geometry is invalid")
            primitives.append(primitive)
    if not primitives:
        _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "plane geometry has no primitives")
    stack_rows: list[dict[str, Any]] = []
    dielectric_points: list[dict[str, Any]] = []
    for ordinal, layer in enumerate(stackup):
        is_conductor = layer.is_conductor
        thickness = layer.thickness_um
        material = layer.material
        if type(is_conductor) is not bool or not isinstance(material, str) or not material or not isinstance(thickness, (int, float)) or not math.isfinite(float(thickness)) or thickness <= 0:
            _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "ProjectSpec stackup physical fields are incomplete")
        sigma = layer.conductivity_s_m
        stack_rows.append({"layer_ordinal": ordinal, "layer_name": layer.name, "layer_kind": "conductor" if is_conductor else "dielectric", "thickness_um": thickness, "conductivity_s_per_m": sigma if is_conductor else None, "material_name": material})
        if not is_conductor:
            for point_ordinal, point in enumerate(layer.dielectric_properties):
                frequency = point.frequency_hz
                epsilon = point.dk
                loss = point.df
                dielectric_points.append({"layer_ordinal": ordinal, "point_ordinal": point_ordinal, "frequency_hz": frequency, "epsilon_r": epsilon, "loss_tangent": loss})
    return {"plane_primitives": primitives, "plane_vertices": vertices, "plane_circles": circles, "stackup_layers": stack_rows, "dielectric_points": dielectric_points}


def _validate_plane_sheet_assets(payload: Mapping[str, Any], attachments: Mapping[str, bytes]) -> None:
    digests: dict[str, str] = {}
    for row in payload.get("plane_primitives", ()):
        name = row.get("source_asset_name") if isinstance(row, Mapping) else None
        digest = row.get("source_asset_sha256") if isinstance(row, Mapping) else None
        if not isinstance(name, str) or not isinstance(digest, str) or name not in attachments:
            _fail("RAW_SPATIAL_PLANE_SHEET_INVALID", "plane primitive source asset is absent")
        observed = digests.get(name)
        if observed is None:
            observed = sha256(attachments[name]).hexdigest()
            digests[name] = observed
        if observed != digest:
            _fail("RAW_SPATIAL_PLANE_SHEET_INVALID", "plane primitive source asset hash differs")


def _parse_layers(
    path: Path,
    start: int,
    end: int,
    analysis: Any,
    cancelled: Callable[[], bool],
    ownership_sink: _OwnershipSink | None = None,
) -> tuple[list[RawSpatialLayerRow], dict[str, str]]:
    analysis_layers = tuple(getattr(analysis, "stackup_layers", ()))
    expected: list[str] = []
    conductor_by_fold: dict[str, bool] = {}
    for item in analysis_layers:
        name = str(getattr(item, "name", ""))
        is_conductor = getattr(item, "is_conductor", None)
        if not name or type(is_conductor) is not bool:
            _fail(
                "RAW_SPATIAL_ANALYSIS_LAYER_MISMATCH",
                "SpdAnalysis layer identity/conductor evidence is incomplete",
            )
        key = name.casefold()
        if key in conductor_by_fold:
            _fail(
                "RAW_SPATIAL_ANALYSIS_LAYER_MISMATCH",
                "SpdAnalysis layer identities are duplicated",
            )
        expected.append(name)
        conductor_by_fold[key] = is_conductor
    rows: list[RawSpatialLayerRow] = []
    by_fold: dict[str, str] = {}
    marker_seen = False
    pending: tuple[str, bytearray, int, int, bool] | None = None
    patched_layers: set[str] = set()

    def finish_pending() -> None:
        nonlocal pending
        if pending is None:
            return
        if len(rows) >= _MAX_ROWS:
            _fail("RAW_SPATIAL_BOUND_EXCEEDED", "layer inventory exceeds its row bound")
        layer, exact, _offset, _line_count, _patch_seen = pending
        is_conductor = conductor_by_fold.get(layer.casefold())
        if is_conductor is None:
            _fail(
                "RAW_SPATIAL_ANALYSIS_LAYER_MISMATCH",
                f"raw layer {layer!r} is absent from SpdAnalysis",
            )
        rows.append(
            RawSpatialLayerRow(
                len(rows),
                layer,
                is_conductor,
                sha256(bytes(exact)).hexdigest(),
            )
        )
        if ownership_sink is not None and ownership_sink.selected("Layer", layer):
            ownership_sink.stage_source(record_id=f"layer:{layer}", kind="Layer", logical_net=None,
                layer=layer, lookup=(layer,), source_offset=_offset, source_end=_offset + len(exact),
                source_record_sha=sha256(bytes(exact)).hexdigest(), raw_ordinal=len(rows) - 1)
        pending = None

    with path.open("rb") as handle:
        for offset, raw in _bounded_lines(handle, start, end):
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            physical = _without_ending(raw)
            if not marker_seen:
                if physical != _LAYER_MARKER:
                    _fail(
                        "RAW_SPATIAL_LAYER_SECTION_INVALID",
                        "layer interval does not start at its marker",
                        offset=offset,
                    )
                marker_seen = True
                continue
            stripped = physical.strip()
            if physical.lstrip().startswith(b"+"):
                if pending is None:
                    _fail(
                        "RAW_SPATIAL_ORPHAN_CONTINUATION",
                        "orphan layer continuation",
                        offset=offset,
                    )
                layer, exact, primary_offset, line_count, patch_seen = pending
                if patch_seen:
                    _fail(
                        "RAW_SPATIAL_LAYER_PATCH_MALFORMED",
                        "layer Patch row cannot have a continuation",
                        offset=offset,
                    )
                if (
                    line_count >= _MAX_LOGICAL_RECORD_LINES
                    or len(exact) + len(raw) > _MAX_LOGICAL_RECORD_BYTES
                ):
                    _fail(
                        "RAW_SPATIAL_LAYER_RECORD_BOUND_EXCEEDED",
                        "layer logical record exceeds its bound",
                        offset=primary_offset,
                    )
                exact.extend(raw)
                pending = (layer, exact, primary_offset, line_count + 1, False)
                continue
            patch_match = _LAYER_PATCH_RE.fullmatch(stripped)
            layer_match = _LAYER_RE.fullmatch(stripped)
            if patch_match is not None or (
                layer_match is None and stripped.lower().startswith(b"patch")
            ):
                if patch_match is None:
                    _fail(
                        "RAW_SPATIAL_LAYER_PATCH_MALFORMED",
                        "layer section contains a malformed Patch row",
                        offset=offset,
                    )
                patch_id = _decode_token(
                    patch_match.group("id"), "layer Patch id", offset=offset
                )
                shape = _decode_token(
                    patch_match.group("shape"), "layer Patch shape", offset=offset
                )
                target = _decode_token(
                    patch_match.group("layer"), "layer Patch target", offset=offset
                )
                target_key = target.casefold()
                if (
                    patch_id.casefold() != target_key
                    or shape.casefold() != f"{target}pkgshape".casefold()
                    or not conductor_by_fold.get(target_key, False)
                ):
                    _fail(
                        "RAW_SPATIAL_LAYER_PATCH_MISMATCH",
                        "layer Patch id, shape, and target do not identify the "
                        "preceding conductor layer",
                        offset=offset,
                    )
                if target_key in patched_layers:
                    _fail(
                        "RAW_SPATIAL_LAYER_PATCH_DUPLICATE",
                        f"layer Patch row for {target!r} is duplicated",
                        offset=offset,
                    )
                if pending is None:
                    _fail(
                        "RAW_SPATIAL_LAYER_PATCH_MISMATCH",
                        "layer Patch row does not immediately follow its layer",
                        offset=offset,
                    )
                layer, exact, primary_offset, line_count, patch_seen = pending
                if layer.casefold() != target_key:
                    _fail(
                        "RAW_SPATIAL_LAYER_PATCH_MISMATCH",
                        "layer Patch id, shape, and target do not identify the "
                        "preceding conductor layer",
                        offset=offset,
                    )
                if patch_seen:
                    _fail(
                        "RAW_SPATIAL_LAYER_PATCH_DUPLICATE",
                        f"layer Patch row for {target!r} is duplicated",
                        offset=offset,
                    )
                if (
                    line_count >= _MAX_LOGICAL_RECORD_LINES
                    or len(exact) + len(raw) > _MAX_LOGICAL_RECORD_BYTES
                ):
                    _fail(
                        "RAW_SPATIAL_LAYER_RECORD_BOUND_EXCEEDED",
                        "layer record plus Patch evidence exceeds its bound",
                        offset=primary_offset,
                    )
                exact.extend(raw)
                patched_layers.add(target_key)
                pending = (layer, exact, primary_offset, line_count + 1, True)
                continue
            finish_pending()
            if not stripped or stripped.startswith((b"*", b".")):
                continue
            match = layer_match
            if match is None:
                _fail(
                    "RAW_SPATIAL_LAYER_RECORD_MALFORMED",
                    "layer section contains an unknown primary",
                    offset=offset,
                )
            layer = _decode_token(match.group("id"), "layer id", offset=offset)
            key = layer.casefold()
            if key in by_fold:
                _fail(
                    "RAW_SPATIAL_LAYER_DUPLICATE", f"layer {layer!r} is duplicated", offset=offset
                )
            if len(by_fold) >= _MAX_ROWS:
                _fail("RAW_SPATIAL_BOUND_EXCEEDED", "layer inventory exceeds its row bound")
            _pm(match.group("thickness"), "layer thickness", offset=offset, positive=True)
            by_fold[key] = layer
            pending = (layer, bytearray(raw), offset, 1, False)
    finish_pending()
    if tuple(row.layer_id for row in rows) != tuple(expected):
        _fail("RAW_SPATIAL_ANALYSIS_LAYER_MISMATCH", "raw layer inventory differs from SpdAnalysis")
    if not rows:
        _fail("RAW_SPATIAL_LAYER_SECTION_INVALID", "raw layer inventory is empty")
    return rows, by_fold


def _header_material(raw: bytes, *, offset: int) -> str | None:
    match = re.search(rb"\bMaterial\s*=\s*(\S+)", raw, re.IGNORECASE)
    return _decode_token(match.group(1), "padstack material", offset=offset) if match else None


def _parse_padstacks(
    path: Path,
    start: int,
    end: int,
    analysis: Any,
    layer_by_fold: Mapping[str, str],
    cancelled: Callable[[], bool],
    ownership_sink: _OwnershipSink | None = None,
) -> tuple[
    list[RawSpatialPadstackRow], list[RawSpatialPadShapeRow], dict[str, str], set[tuple[str, str]]
]:
    padstacks: list[RawSpatialPadstackRow] = []
    shapes: list[RawSpatialPadShapeRow] = []
    by_fold: dict[str, str] = {}
    shape_keys: set[tuple[str, str]] = set()
    definition_keys: set[tuple[str, str]] = set()
    float_evidence: list[_PadstackFloatEvidence] = []
    current_name: str | None = None
    current_fold: str | None = None
    current_drill: int | None = None
    current_core_drill: float | None = None
    current_core_width: float | None = None
    current_core_height: float | None = None
    current_core_layers: list[str] = []
    current_core_shapes: list[
        tuple[str, str, float | None, float | None, str | None]
    ] = []
    current_material: str | None = None
    current_hash: Any = None
    current_bytes = 0
    active_layer: str | None = None
    active_core_layer: str | None = None
    active_paddef: bytes | None = None
    active_paddef_id: str | None = None
    regular: tuple[str, str, int, int, bytearray, bool, str, bytes, int, str] | None = None

    def finish_regular() -> None:
        nonlocal regular
        if regular is None:
            return
        padstack, layer, width, height, exact_regular, supported, raw_kind, paddef, regular_offset, paddef_id = regular
        key = (padstack.casefold(), layer.casefold())
        if key in definition_keys:
            _fail(
                "RAW_SPATIAL_PAD_SHAPE_DUPLICATE", f"pad shape {padstack!r}/{layer!r} is duplicated"
            )
        if len(definition_keys) >= _MAX_ROWS:
            _fail(
                "RAW_SPATIAL_BOUND_EXCEEDED",
                "pad-shape definition inventory exceeds its row bound",
            )
        definition_keys.add(key)
        if supported:
            if len(shapes) >= _MAX_ROWS:
                _fail(
                    "RAW_SPATIAL_BOUND_EXCEEDED",
                    "pad-shape inventory exceeds its row bound",
                )
            shape_keys.add(key)
            digest = sha256()
            digest.update(_PAD_SHAPE_DIGEST_DOMAIN)
            for source_record in (paddef, bytes(exact_regular)):
                digest.update(len(source_record).to_bytes(8, "big"))
                digest.update(source_record)
            kind = "CIRCLE" if raw_kind == "circle" else "RECTANGLE"
            shape_ordinal = len(shapes)
            shapes.append(
                RawSpatialPadShapeRow(
                    shape_ordinal,
                    padstack,
                    layer,
                    kind,
                    width,
                    height,
                    digest.hexdigest(),
                )
            )
        else:
            shape_ordinal = None
            digest = None
        if ownership_sink is not None and ownership_sink.selected("Pad", *key):
            regular_id = f"regular:{padstack}:{layer}:{regular_offset}"
            regular_bytes = bytes(exact_regular)
            ownership_sink.stage_source(record_id=regular_id, kind="Regular", layer=layer,
                lookup=key, source_offset=regular_offset, source_end=regular_offset + len(regular_bytes),
                source_record_sha=sha256(regular_bytes).hexdigest(), padstack_id=padstack,
                paddef_record_id=paddef_id, pad_shape_ordinal=shape_ordinal,
                pad_shape_sha=(shapes[-1].source_record_sha256 if shape_ordinal is not None else None))
        regular = None

    def finish_padstack(*, offset: int) -> None:
        nonlocal current_name, current_fold, current_drill, current_material
        nonlocal current_core_drill, current_core_width, current_core_height
        nonlocal current_core_layers, current_core_shapes
        nonlocal current_hash, current_bytes, active_layer, active_core_layer, active_paddef, active_paddef_id
        if current_name is None or current_fold is None or current_hash is None:
            _fail(
                "RAW_SPATIAL_PADSTACK_BLOCK_INVALID",
                "padstack block terminator is orphaned",
                offset=offset,
            )
        if active_layer is not None or active_paddef is not None:
            _fail(
                "RAW_SPATIAL_PAD_SHAPE_INVALID",
                "PadDef is not terminated before EndPadStackDef",
                offset=offset,
            )
        finish_regular()
        if len(padstacks) >= _MAX_ROWS:
            _fail("RAW_SPATIAL_BOUND_EXCEEDED", "padstack inventory exceeds its row bound")
        padstacks.append(
            RawSpatialPadstackRow(
                len(padstacks),
                current_name,
                current_drill,
                current_material,
                current_hash.hexdigest(),
            )
        )
        unique_core_layers: list[str] = []
        seen_core_layers: set[str] = set()
        for source_layer in current_core_layers:
            source_key = source_layer.casefold()
            if source_key not in seen_core_layers:
                seen_core_layers.add(source_key)
                unique_core_layers.append(source_layer)
        float_evidence.append(
            _PadstackFloatEvidence(
                current_name,
                current_core_drill,
                current_core_width,
                current_core_height,
                tuple(unique_core_layers),
                current_material,
                tuple(current_core_shapes),
            )
        )
        current_name = current_fold = None
        current_drill = None
        current_core_drill = None
        current_core_width = None
        current_core_height = None
        current_core_layers = []
        current_core_shapes = []
        current_material = None
        current_hash = None
        current_bytes = 0
        active_layer = None
        active_core_layer = None
        active_paddef = None
        active_paddef_id = None

    with path.open("rb") as handle:
        for offset, raw in _bounded_lines(handle, start, end):
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            physical = _without_ending(raw)
            stripped = physical.strip()
            is_continuation = physical.lstrip().startswith(b"+")
            if current_hash is not None:
                current_hash.update(raw)
                current_bytes += len(raw)
                if current_bytes > _MAX_PADSTACK_BLOCK_BYTES:
                    _fail(
                        "RAW_SPATIAL_PADSTACK_BOUND_EXCEEDED",
                        "padstack block exceeds 64 MiB",
                        offset=offset,
                    )
            if is_continuation:
                if current_name is None:
                    _fail(
                        "RAW_SPATIAL_ORPHAN_CONTINUATION",
                        "orphan padstack continuation",
                        offset=offset,
                    )
                if regular is not None:
                    regular[4].extend(raw)
                    if len(regular[4]) > _MAX_LOGICAL_RECORD_BYTES:
                        _fail(
                            "RAW_SPATIAL_PAD_SHAPE_BOUND_EXCEEDED",
                            "Regular pad-shape logical record exceeds 1 MiB",
                            offset=offset,
                        )
                    regular = (*regular[:5], False, *regular[6:])
                continue
            finish_regular()
            start_match = _PADSTACK_RE.fullmatch(stripped)
            if start_match is not None:
                if current_name is not None:
                    _fail(
                        "RAW_SPATIAL_PADSTACK_BLOCK_INVALID",
                        "nested padstack definition",
                        offset=offset,
                    )
                current_name = _decode_token(start_match.group("id"), "padstack id", offset=offset)
                current_fold = current_name.casefold()
                if current_fold in by_fold:
                    _fail(
                        "RAW_SPATIAL_PADSTACK_DUPLICATE",
                        f"padstack {current_name!r} is duplicated",
                        offset=offset,
                    )
                if len(by_fold) >= _MAX_ROWS:
                    _fail(
                        "RAW_SPATIAL_BOUND_EXCEEDED",
                        "padstack inventory exceeds its row bound",
                        offset=offset,
                    )
                by_fold[current_fold] = current_name
                current_hash = sha256(raw)
                current_bytes = len(raw)
                lengths = [
                    match.group(0) for match in _LENGTH_TOKEN_RE.finditer(start_match.group("tail"))
                ]
                drill_radius = (
                    _pm(lengths[0], "padstack drill radius", offset=offset)
                    if lengths
                    else None
                )
                if drill_radius is not None and drill_radius < 0:
                    _fail(
                        "RAW_SPATIAL_LENGTH_INVALID",
                        "padstack drill radius must be non-negative",
                        offset=offset,
                    )
                current_drill = (
                    None
                    if drill_radius in {None, 0}
                    else _double_pm(drill_radius, "padstack drill", offset=offset)
                )
                current_core_drill = (
                    2.0
                    * _core_length_um(
                        lengths[0], "padstack drill radius", offset=offset
                    )
                    if lengths
                    else None
                )
                current_material = _header_material(stripped, offset=offset)
                continue
            if stripped.lower().startswith(b".endpadstackdef"):
                finish_padstack(offset=offset)
                continue
            if stripped.lower().startswith(b".endpaddef"):
                if active_layer is None or active_paddef is None:
                    _fail(
                        "RAW_SPATIAL_PAD_SHAPE_INVALID",
                        "orphan EndPadDef",
                        offset=offset,
                    )
                active_layer = None
                active_core_layer = None
                active_paddef = None
                active_paddef_id = None
                continue
            if current_name is None:
                continue
            paddef_match = _PADDEF_RE.fullmatch(stripped)
            if paddef_match is not None:
                if active_layer is not None or active_paddef is not None:
                    _fail(
                        "RAW_SPATIAL_PAD_SHAPE_INVALID",
                        "nested PadDef definition",
                        offset=offset,
                    )
                raw_layer = _decode_token(
                    paddef_match.group("layer"), "pad shape layer", offset=offset
                )
                active_layer = layer_by_fold.get(raw_layer.casefold())
                if active_layer is None:
                    _fail(
                        "RAW_SPATIAL_LAYER_UNRESOLVED",
                        f"pad shape layer {raw_layer!r} is absent",
                        offset=offset,
                    )
                active_core_layer = raw_layer
                current_core_layers.append(raw_layer)
                active_paddef = raw
                active_paddef_id = f"paddef:{current_name}:{raw_layer}:{offset}"
                if ownership_sink is not None and ownership_sink.selected("Pad", current_name, raw_layer):
                    ownership_sink.stage_source(record_id=active_paddef_id, kind="PadDef", layer=raw_layer,
                        lookup=(current_name, raw_layer), source_offset=offset, source_end=offset + len(raw),
                        source_record_sha=sha256(raw).hexdigest(), padstack_id=current_name)
                continue
            regular_match = _REGULAR_RE.fullmatch(stripped)
            if regular_match is None:
                continue
            if active_layer is None or active_paddef is None:
                _fail(
                    "RAW_SPATIAL_PAD_SHAPE_INVALID",
                    "Regular pad shape occurs outside PadDef",
                    offset=offset,
                )
            kind = _decode_token(
                regular_match.group("kind"), "pad shape kind", offset=offset
            ).casefold()
            lengths = [match.group(0) for match in _LENGTH_TOKEN_RE.finditer(stripped)]
            core_lengths = [
                _core_length_um(token, "pad shape dimension", offset=offset)
                for token in lengths
            ]
            core_width: float | None = None
            core_height: float | None = None
            core_reason: str | None = None
            folded = stripped.lower()
            if folded.startswith(b"regular circle") and core_lengths:
                core_width = core_height = 2.0 * core_lengths[0]
                core_kind = "CIRCLE"
                normalized_kind = "circle"
            elif folded.startswith(b"regular square") and core_lengths:
                core_width = core_height = core_lengths[0]
                core_kind = "RECTANGLE"
                normalized_kind = "square"
            elif folded.startswith(b"regular box") and len(core_lengths) >= 2:
                core_width, core_height = core_lengths[:2]
                core_kind = "RECTANGLE"
                normalized_kind = "box"
            else:
                core_kind = "UNSUPPORTED"
                normalized_kind = kind
                tokens = stripped.split(None, 2)
                geometry_label = _decode_token(
                    tokens[1] if len(tokens) > 1 else b"Regular",
                    "unsupported pad shape kind",
                    offset=offset,
                )
                core_reason = (
                    f"padstack {current_name!r} uses unsupported or malformed "
                    f"{geometry_label} geometry"
                )
            if active_core_layer is not None:
                current_core_shapes.append(
                    (
                        active_core_layer,
                        core_kind,
                        core_width,
                        core_height,
                        core_reason,
                    )
                )
            if core_width is not None and core_height is not None:
                current_core_width = (
                    core_width
                    if current_core_width is None
                    else max(current_core_width, core_width)
                )
                current_core_height = (
                    core_height
                    if current_core_height is None
                    else max(current_core_height, core_height)
                )
            supported = True
            width = height = 1
            if normalized_kind == "circle" and len(lengths) == 1:
                radius = _pm(
                    lengths[0], "pad radius", offset=offset, positive=True
                )
                width = height = _double_pm(radius, "pad", offset=offset)
            elif normalized_kind == "square" and len(lengths) == 1:
                width = height = _pm(lengths[0], "pad side", offset=offset, positive=True)
            elif normalized_kind == "box" and len(lengths) == 2:
                width = _pm(lengths[0], "pad width", offset=offset, positive=True)
                height = _pm(lengths[1], "pad height", offset=offset, positive=True)
            else:
                supported = False
            regular = (
                current_name,
                active_layer,
                width,
                height,
                bytearray(raw),
                supported,
                normalized_kind,
                active_paddef,
                offset,
                active_paddef_id or "",
            )
    if current_name is not None:
        _fail("RAW_SPATIAL_PADSTACK_BLOCK_INVALID", f"padstack {current_name!r} is unterminated")
    analysis_padstacks = tuple(getattr(analysis, "padstacks", ()))
    expected = tuple(str(getattr(item, "name", "")) for item in analysis_padstacks)
    if tuple(row.padstack_id for row in padstacks) != expected:
        _fail(
            "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
            "raw padstack inventory differs from SpdAnalysis",
        )
    if len(float_evidence) != len(analysis_padstacks):
        _fail(
            "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
            "raw binary64 padstack evidence differs from SpdAnalysis",
        )
    for row, source_float, analysis_padstack in zip(
        padstacks, float_evidence, analysis_padstacks
    ):
        if (
            source_float.name != row.padstack_id
            or not _optional_float_matches(
                getattr(analysis_padstack, "drill_diameter_um", None),
                source_float.drill_diameter_um,
                f"SpdAnalysis padstack {row.padstack_id!r} drill",
                code="RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
            )
            or not _optional_float_matches(
                getattr(analysis_padstack, "pad_width_um", None),
                source_float.pad_width_um,
                f"SpdAnalysis padstack {row.padstack_id!r} maximum width",
                code="RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
            )
            or not _optional_float_matches(
                getattr(analysis_padstack, "pad_height_um", None),
                source_float.pad_height_um,
                f"SpdAnalysis padstack {row.padstack_id!r} maximum height",
                code="RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
            )
            or tuple(getattr(analysis_padstack, "layers", ())) != source_float.layers
            or row.material != source_float.material
            or row.material != getattr(analysis_padstack, "material", None)
        ):
            _fail(
                "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
                f"padstack {row.padstack_id!r} definition differs from SpdAnalysis",
            )
        analysis_shapes = tuple(getattr(analysis_padstack, "pad_shapes", ()))
        if len(analysis_shapes) != len(source_float.shapes):
            _fail(
                "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
                f"padstack {row.padstack_id!r} shape count differs from SpdAnalysis",
            )
        expected_definitions: set[tuple[str, str]] = set()
        expected_supported: list[tuple[str, str]] = []
        for source_shape, shape in zip(source_float.shapes, analysis_shapes):
            (
                source_layer,
                source_kind,
                source_width,
                source_height,
                source_reason,
            ) = source_shape
            raw_layer = str(getattr(shape, "layer", ""))
            layer = layer_by_fold.get(source_layer.casefold())
            if layer is None:
                _fail(
                    "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
                    f"raw pad shape layer {source_layer!r} is absent",
                )
            kind = str(getattr(shape, "kind", ""))
            if (
                raw_layer != source_layer
                or kind != source_kind
                or getattr(shape, "reason", None) != source_reason
                or not _optional_float_matches(
                    getattr(shape, "width_um", None),
                    source_width,
                    f"SpdAnalysis padstack {row.padstack_id!r} width",
                    code="RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
                )
                or not _optional_float_matches(
                    getattr(shape, "height_um", None),
                    source_height,
                    f"SpdAnalysis padstack {row.padstack_id!r} height",
                    code="RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
                )
            ):
                _fail(
                    "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
                    f"padstack {row.padstack_id!r} source float shape differs from SpdAnalysis",
                )
            expected_definitions.add((row.padstack_id.casefold(), layer.casefold()))
            if source_width is not None and source_height is not None:
                expected_supported.append((layer, source_kind))
        observed_definitions = {
            key for key in definition_keys if key[0] == row.padstack_id.casefold()
        }
        observed_supported = [
            (shape.layer_id, shape.shape_kind)
            for shape in shapes
            if shape.padstack_id.casefold() == row.padstack_id.casefold()
        ]
        if observed_definitions != expected_definitions or observed_supported != expected_supported:
            _fail(
                "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH",
                f"padstack {row.padstack_id!r} shapes differ from SpdAnalysis",
            )
    return padstacks, shapes, by_fold, shape_keys


@dataclass(slots=True)
class _SurfaceGeometryCursor:
    geometry: Any
    order: Sequence[Any]
    category_counts: list[int]
    order_position: int = 0
    positive_subelement_count: int = 0
    negative_subelement_count: int = 0
    polygon_trace_count: int = 0
    box_count: int = 0
    min_x_pm: Fraction | None = None
    max_x_pm: Fraction | None = None
    min_y_pm: Fraction | None = None
    max_y_pm: Fraction | None = None


_SURFACE_CATEGORIES: Final = (
    ("positive_polygon", "positive_polygons_um"),
    ("negative_polygon", "negative_polygons_um"),
    ("positive_circle", "positive_circles_um"),
    ("negative_circle", "negative_circles_um"),
)


def _surface_pm_decimal(value: object, label: str) -> Decimal:
    if type(value) is float:
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            f"{label} is binary64 evidence and cannot certify an exact source grid",
        )
    try:
        source = Decimal(str(value))
        if not source.is_finite():
            _fail("RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH", f"{label} is not finite")
        sign, digits, exponent = source.as_tuple()
        if len(digits) > 128 or abs(int(exponent)) > 1000:
            _fail(
                "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
                f"{label} exceeds its exact decimal bound",
            )
        result = Decimal((sign, digits, int(exponent) + 6))
    except (DecimalException, OverflowError, ValueError) as exc:
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            f"{label} is not exact decimal micrometre evidence",
        )
        raise AssertionError from exc
    return result


def _half_integer_decimal(value: int) -> Decimal:
    """Return ``value / 2`` exactly without consulting Decimal context."""

    if type(value) is not int:
        raise TypeError("half-integer source must be an exact integer")
    sign = int(value < 0)
    coefficient = abs(value)
    if coefficient % 2 == 0:
        coefficient //= 2
        exponent = 0
    else:
        coefficient *= 5
        exponent = -1
    digits = tuple(int(character) for character in str(coefficient))
    return Decimal((sign, digits, exponent))


def _surface_pm_fraction(
    token: bytes,
    label: str,
    *,
    offset: int,
    positive: bool = False,
) -> Fraction:
    """Parse one Shape length as an exact, bounded rational picometre value."""

    match = _EXACT_LENGTH_RE.fullmatch(token.strip())
    if match is None:
        _fail("RAW_SPATIAL_LENGTH_INVALID", f"{label} is invalid", offset=offset)
    number = match.group("number")
    if len(number) > _MAX_EXACT_DECIMAL_LEXEME_BYTES:
        _fail(
            "RAW_SPATIAL_LENGTH_INVALID",
            f"{label} exceeds its exact decimal lexical bound",
            offset=offset,
        )
    try:
        source = Decimal(number.decode("ascii"))
    except (DecimalException, UnicodeDecodeError, ValueError) as exc:
        _fail(
            "RAW_SPATIAL_LENGTH_INVALID",
            f"{label} is not a finite exact Decimal: {exc}",
            offset=offset,
        )
    if not source.is_finite():
        _fail("RAW_SPATIAL_LENGTH_INVALID", f"{label} is not finite", offset=offset)
    sign, raw_digits, raw_exponent = source.as_tuple()
    if abs(int(raw_exponent)) > 1000:
        _fail(
            "RAW_SPATIAL_LENGTH_INVALID",
            f"{label} exceeds its exact decimal exponent bound",
            offset=offset,
        )
    digits = "".join(str(digit) for digit in raw_digits).lstrip("0")
    if not digits:
        result = Fraction(0, 1)
    else:
        trailing_zeros = len(digits) - len(digits.rstrip("0"))
        digits = digits.rstrip("0")
        exponent = int(raw_exponent) + trailing_zeros
        if len(digits) > 128 or abs(exponent) > 1000:
            _fail(
                "RAW_SPATIAL_LENGTH_INVALID",
                f"{label} exceeds its exact decimal bound",
                offset=offset,
            )
        scale = {
            b"m": 1_000_000_000_000,
            b"mm": 1_000_000_000,
            b"u": 1_000_000,
            b"um": 1_000_000,
            b"mil": 25_400_000,
        }[match.group("unit").lower()]
        coefficient = int(digits) * scale
        if exponent >= 0:
            if exponent > 19:
                _fail(
                    "RAW_SPATIAL_LENGTH_INVALID",
                    f"{label} is outside the signed 64-bit picometre range",
                    offset=offset,
                )
            result = Fraction(coefficient * (10**exponent), 1)
        else:
            result = Fraction(coefficient, 10 ** (-exponent))
        if sign:
            result = -result
    if result < -(2**63) or result > 2**63 - 1:
        _fail(
            "RAW_SPATIAL_LENGTH_INVALID",
            f"{label} is outside the signed 64-bit picometre range",
            offset=offset,
        )
    if positive and result <= 0:
        _fail("RAW_SPATIAL_LENGTH_INVALID", f"{label} must be positive", offset=offset)
    return result


def _checked_surface_pm(
    value: Fraction,
    label: str,
    *,
    offset: int,
) -> Fraction:
    if not isinstance(value, Fraction) or value < -(2**63) or value > 2**63 - 1:
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            f"{label} is outside the signed 64-bit picometre range",
            offset=offset,
        )
    return value


def _surface_sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail("RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH", f"{label} is not a sequence")
    return value


def _float_tree_signature(value: Any) -> Any:
    if type(value) is float:
        if not math.isfinite(value):
            return ("nonfinite",)
        return ("binary64", value.hex())
    if isinstance(value, tuple):
        return tuple(_float_tree_signature(item) for item in value)
    return ("invalid", type(value).__name__)


def _include_surface_bounds(
    cursor: _SurfaceGeometryCursor,
    points: Sequence[tuple[Fraction, Fraction]],
) -> None:
    for x_pm, y_pm in points:
        cursor.min_x_pm = x_pm if cursor.min_x_pm is None else min(cursor.min_x_pm, x_pm)
        cursor.max_x_pm = x_pm if cursor.max_x_pm is None else max(cursor.max_x_pm, x_pm)
        cursor.min_y_pm = y_pm if cursor.min_y_pm is None else min(cursor.min_y_pm, y_pm)
        cursor.max_y_pm = y_pm if cursor.max_y_pm is None else max(cursor.max_y_pm, y_pm)


def _analysis_surface_primitive(
    cursor: _SurfaceGeometryCursor,
    category_index: int,
    primitive_index: int,
    expected_length: int,
) -> tuple[Any, ...]:
    category, field_name = _SURFACE_CATEGORIES[category_index]
    collection = _surface_sequence(
        _geometry_field(cursor.geometry, field_name, ()),
        f"SpdAnalysis {category} collection",
    )
    if primitive_index >= len(collection):
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            f"raw Shape contains an extra {category}",
        )
    primitive = _surface_sequence(collection[primitive_index], f"SpdAnalysis {category}")
    if "polygon" in category:
        if len(primitive) != expected_length:
            _fail(
                "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
                f"raw Shape {category} vertex count differs from SpdAnalysis",
            )
        points: list[tuple[float, float]] = []
        for point in primitive:
            pair = _surface_sequence(point, f"SpdAnalysis {category} point")
            if len(pair) != 2:
                _fail(
                    "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
                    f"SpdAnalysis {category} point is not X/Y",
                )
            points.append(
                (
                    _analysis_float(
                        pair[0],
                        f"SpdAnalysis {category} X",
                        code="RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
                    ),
                    _analysis_float(
                        pair[1],
                        f"SpdAnalysis {category} Y",
                        code="RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
                    ),
                )
            )
        return tuple(points)
    if len(primitive) != 3:
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            f"SpdAnalysis {category} is not X/Y/radius",
        )
    return tuple(
        _analysis_float(
            value,
            f"SpdAnalysis {category}",
            code="RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
        )
        for value in primitive
    )


def _validate_raw_surface_primitive(
    cursor: _SurfaceGeometryCursor,
    primitive: re.Match[bytes],
    first_line: bytes,
    exact_record: bytes,
    *,
    offset: int,
) -> None:
    raw_kind = primitive.group("kind")
    if raw_kind not in {b"Polygon", b"PolygonTrace", b"Circle", b"Box"}:
        _fail(
            "RAW_SPATIAL_SURFACE_PRIMITIVE_UNSUPPORTED",
            f"retained raw Shape primitive {raw_kind!r} is unsupported",
            offset=offset,
        )
    tokens = [match.group(0) for match in _LENGTH_TOKEN_RE.finditer(exact_record)]
    values = [
        _surface_pm_fraction(token, "Shape primitive coordinate", offset=offset)
        for token in tokens
    ]
    core_values = [
        _core_length_um(token, "Shape primitive coordinate", offset=offset)
        for token in tokens
    ]
    polarity = primitive.group("polarity")
    category_index = (0 if raw_kind != b"Circle" else 2) + (1 if polarity == b"-" else 0)
    category = _SURFACE_CATEGORIES[category_index][0]
    primitive_index = cursor.category_counts[category_index]
    if cursor.order_position >= len(cursor.order):
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            f"raw Shape contains an extra {category}",
            offset=offset,
        )
    expected_order = _surface_sequence(
        cursor.order[cursor.order_position], "SpdAnalysis primitive order entry"
    )
    try:
        order_item = (str(expected_order[0]), int(expected_order[1]))
    except (IndexError, TypeError, ValueError) as exc:
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            f"SpdAnalysis primitive order is malformed: {exc}",
            offset=offset,
        )
    if order_item != (category, primitive_index):
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            "raw Shape primitive order differs from SpdAnalysis",
            offset=offset,
        )
    if raw_kind == b"Circle":
        if len(values) != 3 or values[2] <= 0:
            _fail(
                "RAW_SPATIAL_SURFACE_PRIMITIVE_MALFORMED",
                "retained Circle requires X/Y and one positive radius",
                offset=offset,
            )
        x, y, radius = values
        observed: tuple[Any, ...] = tuple(values)
        source_float: tuple[Any, ...] = tuple(core_values)
        min_x = _checked_surface_pm(
            x - radius, "Circle minimum X", offset=offset
        )
        max_x = _checked_surface_pm(
            x + radius, "Circle maximum X", offset=offset
        )
        min_y = _checked_surface_pm(
            y - radius, "Circle minimum Y", offset=offset
        )
        max_y = _checked_surface_pm(
            y + radius, "Circle maximum Y", offset=offset
        )
        source_bounds = (
            (min_x, min_y),
            (max_x, max_y),
        )
    elif raw_kind == b"Box":
        if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
            _fail(
                "RAW_SPATIAL_SURFACE_PRIMITIVE_MALFORMED",
                "retained Box requires X/Y and positive width/height",
                offset=offset,
            )
        x, y, width, height = values
        min_x = _checked_surface_pm(
            x, "Box minimum X", offset=offset
        )
        max_x = _checked_surface_pm(
            x + width, "Box maximum X", offset=offset
        )
        min_y = _checked_surface_pm(
            y, "Box minimum Y", offset=offset
        )
        max_y = _checked_surface_pm(
            y + height, "Box maximum Y", offset=offset
        )
        observed = (
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        )
        x_um, y_um, width_um, height_um = core_values
        source_float = (
            (x_um, y_um),
            (x_um + width_um, y_um),
            (x_um + width_um, y_um + height_um),
            (x_um, y_um + height_um),
        )
        source_bounds = observed
    else:
        if len(values) < 6 or len(values) % 2:
            _fail(
                "RAW_SPATIAL_SURFACE_PRIMITIVE_MALFORMED",
                "retained Polygon requires at least three X/Y pairs",
                offset=offset,
            )
        observed = tuple(
            (values[index], values[index + 1])
            for index in range(0, len(values), 2)
        )
        source_float = tuple(
            (core_values[index], core_values[index + 1])
            for index in range(0, len(core_values), 2)
        )
        source_bounds = observed
    expected = _analysis_surface_primitive(
        cursor, category_index, primitive_index, len(observed)
    )
    if _float_tree_signature(source_float) != _float_tree_signature(expected):
        _fail(
            "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
            "tracked raw Shape float operations differ from SpdAnalysis/artwork",
            offset=offset,
        )
    if polarity == b"+":
        _include_surface_bounds(cursor, source_bounds)
    if b"Sub-element" in first_line:
        if polarity == b"+":
            cursor.positive_subelement_count += 1
        else:
            cursor.negative_subelement_count += 1
    if raw_kind == b"PolygonTrace":
        cursor.polygon_trace_count += 1
    elif raw_kind == b"Box":
        cursor.box_count += 1
    cursor.category_counts[category_index] += 1
    cursor.order_position += 1


def _surface_source_hashes(
    path: Path,
    end: int,
    surface_keys: set[tuple[str, str]],
    analysis_geometry: Mapping[tuple[str, str], Any],
    cancelled: Callable[[], bool],
    ownership_sink: _OwnershipSink | None = None,
) -> tuple[
    dict[tuple[str, str], str],
    dict[tuple[str, str], tuple[Fraction, Fraction, Fraction, Fraction]],
]:
    digests: dict[tuple[str, str], Any] = {}
    counts = {key: 0 for key in surface_keys}
    cursors: dict[tuple[str, str], _SurfaceGeometryCursor] = {}
    for key in surface_keys:
        digest = sha256()
        digest.update(_SURFACE_DIGEST_DOMAIN)
        for token in key:
            encoded = token.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        digests[key] = digest
        order = _surface_sequence(
            _geometry_field(analysis_geometry[key], "primitive_order", ()),
            "SpdAnalysis primitive order",
        )
        cursors[key] = _SurfaceGeometryCursor(analysis_geometry[key], order, [0, 0, 0, 0])
    active_layer: str | None = None
    active_header: bytes | None = None
    header_emitted: set[tuple[str, str]] = set()
    pending: tuple[int, bytes] | None = None
    with path.open("rb") as handle:
        iterator = iter(_bounded_lines(handle, 0, end))
        while True:
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            if pending is not None:
                offset, raw = pending
                pending = None
            else:
                try:
                    offset, raw = next(iterator)
                except StopIteration:
                    break
            physical = _without_ending(raw)
            stripped = physical.strip()
            shape_header = _SHAPE_HEADER_RE.match(stripped)
            if shape_header is not None:
                active_layer = _decode_token(
                    shape_header.group("layer"), "Shape layer", offset=offset
                )
                if active_layer.casefold().endswith("pkgshape"):
                    active_layer = active_layer[: -len("PkgShape")]
                active_header = raw
                header_emitted.clear()
                continue
            if stripped.lower().startswith(b".endshape"):
                # The tracked parser assigns primitives to the nearest prior
                # .Shape header even when they occur after .EndShape.
                continue
            primitive = _SHAPE_PRIMITIVE_RE.match(physical)
            if primitive is None:
                continue
            exact = bytearray(raw)
            if primitive.group("kind") in _SHAPE_CONTINUATION_KINDS:
                while True:
                    try:
                        candidate_offset, candidate = next(iterator)
                    except StopIteration:
                        break
                    if not _without_ending(candidate).lstrip().startswith(b"+"):
                        pending = (candidate_offset, candidate)
                        break
                    if len(exact) + len(candidate) > _MAX_SHAPE_LOGICAL_RECORD_BYTES:
                        _fail(
                            "RAW_SPATIAL_SURFACE_RECORD_BOUND_EXCEEDED",
                            "Shape logical record exceeds its bound",
                            offset=offset,
                        )
                    exact.extend(candidate)
            if active_layer is None or active_header is None:
                continue
            net = _decode_token(primitive.group("net"), "Shape net", offset=offset)
            key = (net.casefold(), active_layer.casefold())
            if key not in digests:
                continue
            record = bytes(exact)
            if key not in header_emitted:
                digests[key].update(len(active_header).to_bytes(8, "big"))
                digests[key].update(active_header)
                header_emitted.add(key)
            digests[key].update(len(record).to_bytes(8, "big"))
            digests[key].update(record)
            _validate_raw_surface_primitive(
                cursors[key],
                primitive,
                physical,
                record,
                offset=offset,
            )
            if ownership_sink is not None and ownership_sink.selected("Surface", *key):
                local_ordinal = counts[key]
                shape_hash = sha256(record).hexdigest()
                ownership_sink.stage_source(record_id=f"shape:{net}:{active_layer}:{local_ordinal}:{shape_hash}", kind="Shape",
                    logical_net=net, layer=active_layer, lookup=(net, active_layer, str(local_ordinal)),
                    source_offset=offset, source_end=offset + len(record), source_record_sha=shape_hash,
                    local_ordinal=local_ordinal, shape_kind=primitive.group("kind").decode("ascii"))
            counts[key] += 1
    missing = [key for key, count in counts.items() if count == 0]
    if missing:
        _fail(
            "RAW_SPATIAL_SURFACE_SOURCE_MISSING",
            f"retained surface {missing[0]!r} has no raw Shape record",
        )
    for key, cursor in cursors.items():
        expected_counts = []
        for _category, field_name in _SURFACE_CATEGORIES:
            expected_counts.append(
                len(
                    _surface_sequence(
                        _geometry_field(cursor.geometry, field_name, ()),
                        f"SpdAnalysis {field_name}",
                    )
                )
            )
        try:
            expected_auxiliary = tuple(
                int(_geometry_field(cursor.geometry, name, 0))
                for name in (
                    "positive_subelement_count",
                    "negative_subelement_count",
                    "polygon_trace_count",
                    "box_count",
                )
            )
        except (TypeError, ValueError, OverflowError) as exc:
            _fail(
                "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
                f"SpdAnalysis surface counts are malformed: {exc}",
            )
        observed_auxiliary = (
            cursor.positive_subelement_count,
            cursor.negative_subelement_count,
            cursor.polygon_trace_count,
            cursor.box_count,
        )
        if (
            cursor.order_position != len(cursor.order)
            or cursor.category_counts != expected_counts
            or observed_auxiliary != expected_auxiliary
        ):
            _fail(
                "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH",
                f"raw Shape counts/order differ for retained surface {key!r}",
            )
    exact_bounds: dict[
        tuple[str, str], tuple[Fraction, Fraction, Fraction, Fraction]
    ] = {}
    for key, cursor in cursors.items():
        bounds = (
            cursor.min_x_pm,
            cursor.max_x_pm,
            cursor.min_y_pm,
            cursor.max_y_pm,
        )
        if (
            any(value is None for value in bounds)
            or cursor.min_x_pm is None
            or cursor.max_x_pm is None
            or cursor.min_y_pm is None
            or cursor.max_y_pm is None
            or cursor.max_x_pm <= cursor.min_x_pm
            or cursor.max_y_pm <= cursor.min_y_pm
        ):
            _fail(
                "RAW_SPATIAL_SURFACE_BBOX_INVALID",
                f"retained surface {key!r} has no exact positive source bounds",
            )
        exact_bounds[key] = (
            cursor.min_x_pm,
            cursor.max_x_pm,
            cursor.min_y_pm,
            cursor.max_y_pm,
        )
    return (
        {key: digest.hexdigest() for key, digest in digests.items()},
        exact_bounds,
    )


def _canonical_sha(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def _um_to_pm(value: object, label: str) -> int:
    if type(value) is float:
        _fail(
            "RAW_SPATIAL_SURFACE_BBOX_INVALID",
            f"{label} is binary64 evidence and cannot certify exact picometres",
        )
    try:
        return _decimal_scaled_integer_exact(
            str(value), 1_000_000, label=f"{label} in picometres"
        )
    except (DecimalException, OverflowError, ValueError) as exc:
        _fail("RAW_SPATIAL_SURFACE_BBOX_INVALID", f"{label} is not decimal")
        raise AssertionError from exc


def _exact_surface_bbox_pm(value: Fraction, label: str) -> int:
    """Require one exact rational source bound to fit the v1 integer-pm schema."""

    if not isinstance(value, Fraction) or value.denominator != 1:
        _fail(
            "RAW_SPATIAL_SURFACE_BBOX_INVALID",
            f"{label} is not an exact integer picometre",
        )
    result = value.numerator
    if result < -(2**63) or result > 2**63 - 1:
        _fail(
            "RAW_SPATIAL_SURFACE_BBOX_INVALID",
            f"{label} is outside the signed-64 picometre range",
        )
    return result


def _float_quad(
    value: object,
    label: str,
    *,
    code: str,
) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        _fail(code, f"{label} is not a four-value numeric sequence")
    result: list[float] = []
    for item in value:
        if type(item) is not float or not math.isfinite(item):
            _fail(code, f"{label} contains evidence that is not finite binary64")
        result.append(item)
    return (result[0], result[1], result[2], result[3])


def _float_quad_matches(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return tuple(value.hex() for value in left) == tuple(value.hex() for value in right)


def _geometry_field(source: Any, name: str, default: Any) -> Any:
    return (
        source.get(name, default) if isinstance(source, Mapping) else getattr(source, name, default)
    )


def _geometry_signature(
    source: Any,
    cancelled: Callable[[], bool],
    *,
    label: str,
    code: str,
) -> tuple[Any, ...]:
    def sequence(value: Any, field: str) -> Sequence[Any]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            _fail(code, f"{label} {field} is not a sequence")
        return value

    def binary64(value: Any, field: str) -> str:
        return _analysis_float(value, f"{label} {field}", code=code).hex()

    try:
        polygons: list[tuple[tuple[str, str], ...]] = []
        polygon_counts: list[int] = []
        for name in ("positive_polygons_um", "negative_polygons_um"):
            collection = sequence(_geometry_field(source, name, ()), name)
            polygon_counts.append(len(collection))
            for polygon in collection:
                points: list[tuple[str, str]] = []
                for point in sequence(polygon, f"{name} polygon"):
                    if not points or len(points) % 1024 == 0:
                        if cancelled():
                            _fail(
                                "RAW_SPATIAL_CANCELLED",
                                "raw spatial compilation was cancelled",
                            )
                    pair = sequence(point, f"{name} point")
                    if len(pair) != 2:
                        _fail(code, f"{label} {name} point is not X/Y")
                    points.append(
                        (
                            binary64(pair[0], f"{name} X"),
                            binary64(pair[1], f"{name} Y"),
                        )
                    )
                polygons.append(tuple(points))
        circles: list[tuple[str, str, str]] = []
        circle_counts: list[int] = []
        for name in ("positive_circles_um", "negative_circles_um"):
            collection = sequence(_geometry_field(source, name, ()), name)
            circle_counts.append(len(collection))
            for circle in collection:
                if cancelled():
                    _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
                values = sequence(circle, f"{name} circle")
                if len(values) != 3:
                    _fail(code, f"{label} {name} circle is not X/Y/radius")
                circles.append(
                    (
                        binary64(values[0], f"{name} X"),
                        binary64(values[1], f"{name} Y"),
                        binary64(values[2], f"{name} radius"),
                    )
                )
        order_rows: list[tuple[str, int]] = []
        for item in sequence(
            _geometry_field(source, "primitive_order", ()), "primitive_order"
        ):
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            pair = sequence(item, "primitive_order entry")
            if (
                len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not int
            ):
                _fail(code, f"{label} primitive_order entry is not exact")
            order_rows.append((pair[0], pair[1]))
        count_rows: list[int] = []
        for name in (
            "positive_subelement_count",
            "negative_subelement_count",
            "polygon_trace_count",
            "box_count",
        ):
            value = _geometry_field(source, name, 0)
            if type(value) is not int:
                _fail(code, f"{label} {name} is not an exact integer")
            count_rows.append(value)
        counts = tuple(count_rows)
    except RawSpatialCompilerError:
        raise
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        _fail(
            code,
            f"{label} primitive evidence is malformed: {exc}",
        )
    return (
        tuple(polygon_counts),
        tuple(polygons),
        tuple(circle_counts),
        tuple(circles),
        tuple(order_rows),
        counts,
    )


def _decode_geometry_attachment(
    content: bytes,
    expected_sha256: str,
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    digest = sha256()
    view = memoryview(content)
    for offset in range(0, len(view), _STREAM_BYTES):
        if cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
        digest.update(view[offset : offset + _STREAM_BYTES])
    if digest.hexdigest() != expected_sha256:
        _fail("RAW_SPATIAL_ARTWORK_ASSET_INVALID", "artwork attachment hash differs")

    decompressor = zlib.decompressobj()
    parts: list[bytes] = []
    total = 0
    try:
        for offset in range(0, len(view), _STREAM_BYTES):
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            remaining = _MAX_GEOMETRY_UNCOMPRESSED_BYTES + 1 - total
            part = decompressor.decompress(
                view[offset : offset + _STREAM_BYTES], remaining
            )
            if part:
                parts.append(part)
                total += len(part)
            if total > _MAX_GEOMETRY_UNCOMPRESSED_BYTES or decompressor.unconsumed_tail:
                _fail(
                    "RAW_SPATIAL_ARTWORK_ASSET_INVALID",
                    "artwork attachment exceeds the 64 MiB decoded bound",
                )
        remaining = _MAX_GEOMETRY_UNCOMPRESSED_BYTES + 1 - total
        tail = decompressor.flush(remaining)
    except zlib.error as exc:
        _fail(
            "RAW_SPATIAL_ARTWORK_ASSET_INVALID",
            f"artwork attachment compression is invalid: {exc}",
        )
    if tail:
        parts.append(tail)
        total += len(tail)
    if (
        total > _MAX_GEOMETRY_UNCOMPRESSED_BYTES
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        _fail(
            "RAW_SPATIAL_ARTWORK_ASSET_INVALID",
            "artwork attachment exceeds its decoded bound or framing",
        )
    if cancelled():
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
    try:
        payload = json.loads(b"".join(parts).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(
            "RAW_SPATIAL_ARTWORK_ASSET_INVALID",
            f"artwork attachment is not valid UTF-8 JSON: {exc}",
        )
    if cancelled():
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
    if not isinstance(payload, dict) or payload.get("format") != (
        "powersi-spd-plane-primitives-v1"
    ):
        _fail("RAW_SPATIAL_ARTWORK_ASSET_INVALID", "artwork attachment format is invalid")
    return payload


def _parse_surfaces(
    path: Path,
    shape_end: int,
    analysis: Any,
    project: Any,
    attachments: Mapping[str, bytes],
    layer_by_fold: Mapping[str, str],
    cancelled: Callable[[], bool],
    ownership_sink: _OwnershipSink | None = None,
) -> tuple[list[RawSpatialSurfaceRow], set[tuple[str, str]]]:
    spd_import = _project_spd_import(project)
    raw_records = spd_import.get("plane_geometries")
    if not isinstance(raw_records, list) or not raw_records:
        _fail("RAW_SPATIAL_SURFACE_MANIFEST_INVALID", "retained plane geometry manifest is absent")
    if len(raw_records) > _MAX_ROWS:
        _fail("RAW_SPATIAL_BOUND_EXCEEDED", "surface inventory exceeds its row bound")
    attachment_index: dict[str, tuple[str, bytes] | None] = {}
    for raw_name, content in attachments.items():
        if cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
        name = str(raw_name)
        folded = name.casefold()
        attachment_index[folded] = (
            None if folded in attachment_index else (name, content)
        )
    analysis_geometry: dict[tuple[str, str], Any] = {}
    for geometry in getattr(analysis, "plane_geometries", ()):
        if cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
        if len(analysis_geometry) >= _MAX_ROWS:
            _fail(
                "RAW_SPATIAL_BOUND_EXCEEDED",
                "SpdAnalysis surface inventory exceeds its row bound",
            )
        key = (
            str(getattr(geometry, "net", "")).casefold(),
            str(getattr(geometry, "layer", "")).casefold(),
        )
        if not key[0] or not key[1] or key in analysis_geometry:
            _fail(
                "RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH",
                "SpdAnalysis surface identities are empty or duplicated",
            )
        analysis_geometry[key] = geometry
    parsed: list[dict[str, Any]] = []
    keys: set[tuple[str, str]] = set()
    for raw in raw_records:
        if cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
        if not isinstance(raw, Mapping):
            _fail("RAW_SPATIAL_SURFACE_MANIFEST_INVALID", "plane geometry row is not a mapping")
        net = str(raw.get("net", "")).strip()
        raw_layer = str(raw.get("layer", "")).strip()
        asset_name = str(raw.get("asset", "")).strip()
        asset_sha = str(raw.get("asset_sha256", "")).strip()
        layer = layer_by_fold.get(raw_layer.casefold())
        if (
            not net
            or layer is None
            or not asset_name
            or not re.fullmatch(r"[0-9a-f]{64}", asset_sha)
        ):
            _fail("RAW_SPATIAL_SURFACE_MANIFEST_INVALID", "plane geometry identity is incomplete")
        key = (net.casefold(), layer.casefold())
        if key in keys:
            _fail("RAW_SPATIAL_SURFACE_DUPLICATE", f"surface {net!r}/{layer!r} is duplicated")
        analysis_item = analysis_geometry.get(key)
        if analysis_item is None:
            _fail(
                "RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH",
                f"surface {net!r}/{layer!r} is absent from SpdAnalysis",
            )
        keys.add(key)
        match = attachment_index.get(asset_name.casefold())
        if match is None or match[0] != asset_name or not isinstance(match[1], bytes):
            _fail(
                "RAW_SPATIAL_ARTWORK_ASSET_INVALID",
                f"artwork attachment {asset_name!r} is missing or ambiguous",
            )
        content = match[1]
        try:
            payload = _decode_geometry_attachment(content, asset_sha, cancelled)
            core_services._validate_spd_geometry_payload(
                payload, expected_layer=layer, expected_net=net
            )
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            decoded_bounds = core_services._spd_geometry_bounds(payload)
        except RawSpatialCompilerError:
            raise
        except (ValueError, ArithmeticError) as exc:
            _fail(
                "RAW_SPATIAL_ARTWORK_ASSET_INVALID",
                f"artwork attachment {asset_name!r} payload differs: {exc}",
            )
        if decoded_bounds is None:
            _fail(
                "RAW_SPATIAL_ARTWORK_ASSET_INVALID",
                f"artwork attachment {asset_name!r} has no finite positive bounds",
            )
        if _geometry_signature(
            payload,
            cancelled,
            label="decoded artwork",
            code="RAW_SPATIAL_ARTWORK_ASSET_INVALID",
        ) != _geometry_signature(
            analysis_item,
            cancelled,
            label="SpdAnalysis artwork",
            code="RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH",
        ):
            _fail(
                "RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH",
                "decoded artwork primitives/counts/order differ from SpdAnalysis",
            )
        islands = raw.get("island_ids")
        if not isinstance(islands, Sequence) or isinstance(islands, (str, bytes)):
            _fail("RAW_SPATIAL_ISLAND_MANIFEST_INVALID", "surface island inventory is invalid")
        island_ids = [str(item).strip() for item in islands]
        if (
            not island_ids
            or any(not item for item in island_ids)
            or len({item.casefold() for item in island_ids}) != len(island_ids)
        ):
            _fail("RAW_SPATIAL_ISLAND_MANIFEST_INVALID", "surface islands are empty or duplicated")
        bbox = raw.get("bbox_um")
        project_bbox = _float_quad(
            bbox,
            "project surface bbox",
            code="RAW_SPATIAL_SURFACE_BBOX_INVALID",
        )
        decoded_bbox = _float_quad(
            decoded_bounds,
            "decoded artwork bbox",
            code="RAW_SPATIAL_ARTWORK_ASSET_INVALID",
        )
        parsed.append(
            {
                "net": net,
                "layer": layer,
                "asset_sha": asset_sha,
                "islands": tuple(sorted(island_ids)),
                "project_bbox": project_bbox,
                "decoded_bbox": decoded_bbox,
                "key": key,
            }
        )
    analysis_keys = set(analysis_geometry)
    if analysis_keys != keys:
        _fail("RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH", "surface inventory differs from SpdAnalysis")
    source_hashes, exact_source_bounds = _surface_source_hashes(
        path, shape_end, keys, analysis_geometry, cancelled, ownership_sink,
    )
    for item in parsed:
        geometry = analysis_geometry[item["key"]]
        analysis_bounds = core_services._spd_geometry_bounds(
            {
                "positive_polygons_um": getattr(geometry, "positive_polygons_um", ()),
                "negative_polygons_um": getattr(geometry, "negative_polygons_um", ()),
                "positive_circles_um": getattr(geometry, "positive_circles_um", ()),
                "negative_circles_um": getattr(geometry, "negative_circles_um", ()),
            }
        )
        if analysis_bounds is None:
            _fail(
                "RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH",
                "SpdAnalysis surface has no finite positive bounds",
            )
        analysis_bbox = _float_quad(
            analysis_bounds,
            "SpdAnalysis artwork bbox",
            code="RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH",
        )
        if not _float_quad_matches(item["project_bbox"], item["decoded_bbox"]):
            _fail(
                "RAW_SPATIAL_SURFACE_BBOX_INVALID",
                "project surface bbox differs from decoded artwork binary64 bounds",
            )
        if not _float_quad_matches(item["decoded_bbox"], analysis_bbox):
            _fail(
                "RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH",
                "SpdAnalysis surface bounds differ from decoded artwork binary64 bounds",
            )
        min_x_raw, max_x_raw, min_y_raw, max_y_raw = exact_source_bounds[item["key"]]
        min_x = _exact_surface_bbox_pm(min_x_raw, "source surface min X")
        max_x = _exact_surface_bbox_pm(max_x_raw, "source surface max X")
        min_y = _exact_surface_bbox_pm(min_y_raw, "source surface min Y")
        max_y = _exact_surface_bbox_pm(max_y_raw, "source surface max Y")
        if min_x > max_x or min_y > max_y:
            _fail("RAW_SPATIAL_SURFACE_BBOX_INVALID", "exact source bbox is inverted")
        item["bbox"] = (min_x, min_y, max_x, max_y)
    rows: list[RawSpatialSurfaceRow] = []
    for item in sorted(parsed, key=lambda value: (value["key"], value["asset_sha"])):
        island_sha = _canonical_sha(
            {
                "schema": _ISLAND_MANIFEST_SCHEMA,
                "net": item["key"][0],
                "layer": item["key"][1],
                "artwork_asset_sha256": item["asset_sha"],
                "island_ids": item["islands"],
            }
        )
        surface_sha = _canonical_sha(
            {
                "schema": "spd-raw-surface-id-v1",
                "net": item["key"][0],
                "layer": item["key"][1],
                "artwork_asset_sha256": item["asset_sha"],
                "island_manifest_sha256": island_sha,
                "source_record_sha256": source_hashes[item["key"]],
            }
        )
        rows.append(
            RawSpatialSurfaceRow(
                len(rows),
                f"surface:{surface_sha}",
                item["net"],
                item["layer"],
                item["asset_sha"],
                island_sha,
                source_hashes[item["key"]],
                *item["bbox"],
            )
        )
    return rows, keys


_SPOOL_SQL: Final = """
CREATE TABLE nodes (
 ordinal INTEGER PRIMARY KEY, node_id TEXT NOT NULL, node_fold TEXT NOT NULL,
 explicit_net TEXT, explicit_net_fold TEXT, x_pm INTEGER NOT NULL, y_pm INTEGER NOT NULL,
 layer_id TEXT NOT NULL, layer_fold TEXT NOT NULL, padstack_id TEXT,
 rotation INTEGER, source_sha TEXT NOT NULL, resolved_net TEXT, resolved_net_fold TEXT,
 net_status TEXT, source_offset INTEGER NOT NULL DEFAULT 0, source_end INTEGER NOT NULL DEFAULT 0,
 UNIQUE(node_fold)
);
CREATE TABLE incidence (
 node_fold TEXT NOT NULL, net_fold TEXT NOT NULL, net_name TEXT NOT NULL,
 PRIMARY KEY(node_fold, net_fold)
) WITHOUT ROWID;
CREATE TABLE traces (
 ordinal INTEGER PRIMARY KEY, trace_id TEXT NOT NULL, trace_fold TEXT NOT NULL,
 net_name TEXT NOT NULL, net_fold TEXT NOT NULL, start_node_id TEXT NOT NULL,
 start_node_fold TEXT NOT NULL, end_node_id TEXT NOT NULL, end_node_fold TEXT NOT NULL,
 width_pm INTEGER, width_status TEXT NOT NULL, width_location TEXT NOT NULL,
 geometry_status TEXT NOT NULL, source_sha TEXT NOT NULL,
 UNIQUE(net_fold, trace_fold)
);
CREATE TABLE vias (
 ordinal INTEGER PRIMARY KEY, via_id TEXT NOT NULL, via_fold TEXT NOT NULL,
 net_name TEXT NOT NULL, net_fold TEXT NOT NULL, start_node_id TEXT NOT NULL,
 start_node_fold TEXT NOT NULL, end_node_id TEXT NOT NULL, end_node_fold TEXT NOT NULL,
 padstack_id TEXT NOT NULL, padstack_fold TEXT NOT NULL, rotation INTEGER NOT NULL,
 source_sha TEXT NOT NULL, status TEXT, source_offset INTEGER NOT NULL DEFAULT 0, source_end INTEGER NOT NULL DEFAULT 0,
 UNIQUE(via_fold)
);
CREATE TABLE ownership_selection (
 kind TEXT NOT NULL, key_a TEXT NOT NULL, key_b TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(kind,key_a,key_b)
) WITHOUT ROWID;
CREATE TABLE ownership_source_stage (
 record_id TEXT NOT NULL, record_fold TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, source_offset INTEGER NOT NULL,
 source_end INTEGER NOT NULL, source_record_sha256 TEXT NOT NULL,
 logical_net TEXT, layer TEXT, node_id TEXT, via_id TEXT, net_name TEXT,
 start_node_id TEXT, end_node_id TEXT, padstack_id TEXT, rotation INTEGER,
 x_pm INTEGER, y_pm INTEGER, raw_ordinal INTEGER, local_ordinal INTEGER,
 shape_kind TEXT, paddef_record_id TEXT, pad_shape_ordinal INTEGER,
 pad_shape_sha TEXT, lookup_a TEXT NOT NULL, lookup_b TEXT NOT NULL DEFAULT '',
 lookup_c TEXT NOT NULL DEFAULT '', PRIMARY KEY(record_id), UNIQUE(record_fold),
 UNIQUE(kind,lookup_a,lookup_b,lookup_c)
) WITHOUT ROWID;
CREATE TABLE ownership_surface_stage (
 ordinal INTEGER PRIMARY KEY, surface_id TEXT NOT NULL, artwork_net TEXT NOT NULL,
 layer TEXT NOT NULL, geometry_asset_name TEXT NOT NULL, geometry_asset_sha TEXT NOT NULL,
 island_manifest_sha TEXT NOT NULL, source_lineage_sha TEXT NOT NULL, component_count INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE ownership_primitive_stage (
 primitive_id TEXT PRIMARY KEY, surface_id TEXT NOT NULL, local_ordinal INTEGER NOT NULL,
 raw_primitive_ordinal INTEGER NOT NULL, polarity TEXT NOT NULL, kind TEXT NOT NULL,
 effect_status TEXT NOT NULL, source_record_id TEXT NOT NULL, source_asset_name TEXT NOT NULL,
 source_asset_sha TEXT NOT NULL, primitive_sha TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE ownership_pad_shape_stage (
 ordinal INTEGER PRIMARY KEY, padstack_id TEXT NOT NULL, layer TEXT NOT NULL,
 paddef_source_record_id TEXT NOT NULL, regular_source_record_id TEXT NOT NULL,
 raw_pad_shape_ordinal INTEGER, raw_pad_shape_sha TEXT
) WITHOUT ROWID;
"""


class _Batch:
    def __init__(
        self,
        connection: sqlite3.Connection,
        sql: str,
        size: int,
        cancelled: Callable[[], bool],
        duplicate_code: str,
    ) -> None:
        self.connection = connection
        self.sql = sql
        self.size = size
        self.cancelled = cancelled
        self.duplicate_code = duplicate_code
        self.rows: list[tuple[Any, ...]] = []

    def add(self, row: tuple[Any, ...]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        if self.cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
        try:
            self.connection.executemany(self.sql, self.rows)
        except sqlite3.IntegrityError as exc:
            _fail(self.duplicate_code, f"raw spatial composite identity is duplicated: {exc}")
        except sqlite3.OperationalError as exc:
            _translate_spool_error(exc, self.cancelled)
        self.rows.clear()


def _translate_spool_error(
    error: sqlite3.OperationalError, cancelled: Callable[[], bool]
) -> None:
    if cancelled() or "interrupted" in str(error).casefold():
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
    if "full" in str(error).casefold():
        _fail("RAW_SPATIAL_SPOOL_BOUND_EXCEEDED", "temporary contact spool exceeded 4 GiB")
    _fail("RAW_SPATIAL_SPOOL_INVALID", f"temporary contact spool failed: {error}")


def _configure_spool(
    connection: sqlite3.Connection, cancelled: Callable[[], bool]
) -> None:
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    page_limit = max(1, _MAX_SPOOL_BYTES // page_size)
    connection.execute(f"PRAGMA max_page_count={page_limit}")
    connection.set_progress_handler(lambda: 1 if cancelled() else 0, 10_000)


class _OwnershipSink:
    """SQLite-only ownership handoff; no input-sized Python ownership graph."""

    def __init__(self, connection: sqlite3.Connection, batch_rows: int, cancelled: Callable[[], bool]) -> None:
        self.connection = connection
        self.cancelled = cancelled
        self._source = _Batch(
            connection,
            "INSERT INTO ownership_source_stage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            batch_rows,
            cancelled,
            "RAW_SPATIAL_OWNERSHIP_EVIDENCE_COLLISION",
        )
        self._selection_rows = 0

    def ingest_selection(self, raw_selection: Mapping[str, Any]) -> None:
        if not isinstance(raw_selection, Mapping) or set(raw_selection) != {
            "surface_keys", "node_keys", "via_keys", "pad_keys", "layer_keys", "material_keys"
        }:
            _fail("RAW_SPATIAL_OWNERSHIP_SELECTION_INVALID", "raw_selection keys differ")
        widths = {"surface_keys": 2, "node_keys": 2, "via_keys": 2, "pad_keys": 2, "layer_keys": 1, "material_keys": 1}
        for kind, width in widths.items():
            rows = raw_selection[kind]
            if isinstance(rows, (str, bytes, Mapping)):
                _fail("RAW_SPATIAL_OWNERSHIP_SELECTION_INVALID", f"{kind} is not an iterable")
            try:
                iterator = iter(rows)
            except TypeError:
                _fail("RAW_SPATIAL_OWNERSHIP_SELECTION_INVALID", f"{kind} is not an iterable")
            for item in iterator:
                if width == 1 and isinstance(item, str):
                    item = (item,)
                if not isinstance(item, (tuple, list)) or len(item) != width:
                    _fail("RAW_SPATIAL_OWNERSHIP_SELECTION_INVALID", f"{kind} key width differs")
                values = tuple(str(part).strip().casefold() for part in item)
                if any(not value for value in values):
                    _fail("RAW_SPATIAL_OWNERSHIP_SELECTION_INVALID", f"{kind} contains empty key")
                self._selection_rows += 1
                if self._selection_rows > MAX_SOURCE_PLANE_OWNERSHIP_SELECTION_ROWS:
                    _fail("RAW_SPATIAL_BOUND_EXCEEDED", "ownership selection exceeds its row bound")
                try:
                    self.connection.execute(
                        "INSERT INTO ownership_selection(kind,key_a,key_b) VALUES (?,?,?)",
                        (kind[:-5].title() if kind.endswith("_keys") else kind, values[0], values[1] if width == 2 else ""),
                    )
                except sqlite3.IntegrityError as exc:
                    _fail("RAW_SPATIAL_OWNERSHIP_SELECTION_DUPLICATE", f"duplicate ownership selection: {exc}")

    def selected(self, kind: str, key_a: str, key_b: str = "") -> bool:
        return self.connection.execute(
            "SELECT 1 FROM ownership_selection WHERE kind=? AND key_a=? AND key_b=?",
            (kind, key_a.casefold(), key_b.casefold()),
        ).fetchone() is not None

    def _insert(self, sql: str, params: tuple[Any, ...], code: str) -> None:
        try:
            self.connection.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            _fail(code, f"ownership staging identity is duplicated: {exc}")

    def stage_source(self, *, record_id: str, kind: str, source_offset: int, source_end: int,
                     source_record_sha: str, logical_net: str | None = None, layer: str | None = None,
                     node_id: str | None = None, via_id: str | None = None, net_name: str | None = None,
                     start_node_id: str | None = None, end_node_id: str | None = None,
                     padstack_id: str | None = None, rotation: int | None = None,
                     x_pm: int | None = None, y_pm: int | None = None, raw_ordinal: int | None = None,
                     local_ordinal: int | None = None, shape_kind: str | None = None,
                     paddef_record_id: str | None = None, pad_shape_ordinal: int | None = None,
                     pad_shape_sha: str | None = None, lookup: tuple[str, ...] = ()) -> None:
        lookup_values = tuple(value.casefold() for value in lookup) + ("", "", "")
        self._source.add((record_id, record_id.casefold(), kind, source_offset, source_end, source_record_sha,
                          logical_net, layer, node_id, via_id, net_name, start_node_id,
                          end_node_id, padstack_id, rotation, x_pm, y_pm, raw_ordinal,
                          local_ordinal, shape_kind, paddef_record_id, pad_shape_ordinal,
                          pad_shape_sha, lookup_values[0], lookup_values[1], lookup_values[2]))
        if kind == "Regular" and paddef_record_id is not None:
            self._insert(
                "INSERT INTO ownership_pad_shape_stage VALUES ((SELECT COALESCE(MAX(ordinal),-1)+1 FROM ownership_pad_shape_stage),?,?,?,?,?,?)",
                (padstack_id or "", layer or "", paddef_record_id, record_id, pad_shape_ordinal, pad_shape_sha),
                "RAW_SPATIAL_OWNERSHIP_EVIDENCE_COLLISION",
            )

    def derive_via_endpoint_selection(self) -> None:
        self._source.flush()
        self.connection.execute(
            "INSERT OR IGNORE INTO ownership_selection(kind,key_a,key_b) "
            "SELECT 'Node', v.net_fold, v.start_node_fold FROM vias v "
            "JOIN ownership_selection s ON s.kind='Via' AND s.key_a=v.net_fold AND s.key_b=v.via_fold "
            "UNION SELECT 'Node', v.net_fold, v.end_node_fold FROM vias v "
            "JOIN ownership_selection s ON s.kind='Via' AND s.key_a=v.net_fold AND s.key_b=v.via_fold"
        )
        count = int(self.connection.execute("SELECT COUNT(*) FROM ownership_selection").fetchone()[0])
        if count > MAX_SOURCE_PLANE_OWNERSHIP_SELECTION_ROWS:
            _fail("RAW_SPATIAL_BOUND_EXCEEDED", "expanded ownership selection exceeds its row bound")

    def finalize_contact_sources(self) -> None:
        self._source.flush()
        for row in self.connection.execute(
            "SELECT n.node_id,n.resolved_net,n.layer_id,n.source_offset,n.source_end,n.source_sha,n.padstack_id,n.rotation,n.x_pm,n.y_pm,n.ordinal,n.resolved_net_fold,n.node_fold "
            "FROM nodes n JOIN ownership_selection s ON s.kind='Node' AND s.key_a=n.resolved_net_fold AND s.key_b=n.node_fold"
        ):
            node_id, net, layer, source_offset, source_end, source_sha, padstack, rotation, x_pm, y_pm, ordinal, net_fold, node_fold = row
            self.stage_source(record_id=f"node:{node_id}:{net}", kind="Node", source_offset=source_offset,
                source_end=source_end, source_record_sha=source_sha, logical_net=net, layer=layer,
                node_id=node_id, net_name=net, padstack_id=padstack, rotation=rotation, x_pm=x_pm,
                y_pm=y_pm, raw_ordinal=ordinal, lookup=(net_fold, node_fold))
        self._source.flush()

    def stage_material_draft(self, draft: Any) -> None:
        if not isinstance(draft, Mapping):
            return
        for item in draft.get("source_records", ()):
            if not isinstance(item, Mapping) or item.get("kind") != "Material":
                continue
            name = str(item.get("name", "")).strip()
            if name and self.selected("Material", name):
                self.stage_source(
                    record_id=str(item.get("record_id", f"material:{name}")), kind="Material",
                    source_offset=int(item.get("source_offset", 0)), source_end=int(item.get("source_end", 0)),
                    source_record_sha=str(item.get("source_record_sha256", "")), logical_net=None,
                    lookup=(name,)
                )
    def selected_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM ownership_selection").fetchone()[0])

    def stage_surfaces(self, rows: Sequence[RawSpatialSurfaceRow], project: Any) -> None:
        geometry = {
            (str(item.get("net", "")).casefold(), str(item.get("layer", "")).casefold()): item
            for item in _project_spd_import(project).get("plane_geometries", ())
            if isinstance(item, Mapping)
        }
        for ordinal, row in enumerate(rows):
            if not self.selected("Surface", row.net_name, row.layer_id):
                continue
            source = geometry.get((row.net_name.casefold(), row.layer_id.casefold()), {})
            self._insert(
                "INSERT INTO ownership_surface_stage VALUES (?,?,?,?,?,?,?,?,?)",
                (ordinal, row.surface_id, row.net_name, row.layer_id,
                 str(source.get("asset", "")), str(source.get("asset_sha256", row.artwork_asset_sha256)),
                 row.island_manifest_sha256, row.source_record_sha256, 0),
                "RAW_SPATIAL_OWNERSHIP_EVIDENCE_COLLISION",
            )

    def stage_primitives(self, payload: Mapping[str, Any] | None) -> None:
        if payload is None:
            return
        self._source.flush()
        primitives = payload.get("plane_primitives") if isinstance(payload, Mapping) else None
        if not isinstance(primitives, (list, tuple)):
            _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", "plane primitive context is absent")
        local: dict[tuple[str, str], int] = {}
        for raw in primitives:
            key = (str(raw.get("net_name", "")).casefold(), str(raw.get("layer_name", "")).casefold())
            if not self.selected("Surface", *key):
                continue
            ordinal = local.get(key, 0)
            local[key] = ordinal + 1
            source = self.connection.execute(
                "SELECT record_id,shape_kind FROM ownership_source_stage WHERE kind='Shape' AND lookup_a=? AND lookup_b=? AND lookup_c=?",
                (key[0], key[1], str(ordinal)),
            ).fetchone()
            if source is None:
                _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", "selected Shape source record is absent")
            surface = next((item for item in self.connection.execute(
                "SELECT surface_id,artwork_net,layer FROM ownership_surface_stage"
            ) if item[1].casefold() == key[0] and item[2].casefold() == key[1]), None)
            if surface is None:
                _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", "selected surface context is absent")
            self._insert(
                "INSERT INTO ownership_primitive_stage VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"{surface[0]}:p{ordinal}", surface[0], ordinal, int(raw.get("primitive_ordinal", ordinal)),
                 str(raw.get("polarity", "")), source[1] or "Polygon", "retained", source[0],
                 str(raw.get("source_asset_name", "")), str(raw.get("source_asset_sha256", "")),
                 str(raw.get("primitive_sha256", ""))),
                "RAW_SPATIAL_OWNERSHIP_EVIDENCE_COLLISION",
            )


def _parse_contacts(
    path: Path,
    spans: tuple[_SourceSpan, ...],
    connection: sqlite3.Connection,
    layer_by_fold: Mapping[str, str],
    padstack_by_fold: Mapping[str, str],
    batch_rows: int,
    cancelled: Callable[[], bool],
    ownership_sink: _OwnershipSink | None = None,
) -> tuple[int, int, int]:
    node_span, trace_span, via_span = spans
    node_batch = _Batch(
        connection,
        "INSERT INTO nodes (ordinal,node_id,node_fold,explicit_net,explicit_net_fold,x_pm,y_pm,layer_id,layer_fold,padstack_id,rotation,source_sha,source_offset,source_end) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        batch_rows,
        cancelled,
        "RAW_SPATIAL_NODE_DUPLICATE",
    )
    with path.open("rb") as handle:
        for record in _frame_section(handle, node_span, b"Node"):
            if record.ordinal >= _MAX_ROWS:
                _fail("RAW_SPATIAL_BOUND_EXCEEDED", "Node section exceeds its row bound")
            parsed_node = _parse_node(record, layer_by_fold, padstack_by_fold)
            node_batch.add(parsed_node + (record.source_offset, record.source_offset + len(record.exact_bytes)))
    node_batch.flush()

    trace_batch = _Batch(
        connection,
        "INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        batch_rows,
        cancelled,
        "RAW_SPATIAL_TRACE_DUPLICATE",
    )
    incidence_batch = _Batch(
        connection,
        "INSERT OR IGNORE INTO incidence VALUES (?,?,?)",
        batch_rows,
        cancelled,
        "RAW_SPATIAL_INCIDENCE_INVALID",
    )
    trace_count = 0
    with path.open("rb") as grammar_handle, path.open("rb") as parser_handle:
        framed = _frame_section(grammar_handle, trace_span, b"Trace")
        parsed = _iter_spd_trace_records(parser_handle, trace_span.start, trace_span.end)
        try:
            pairs = zip_longest(framed, parsed)
            for frame, record in pairs:
                if (
                    frame is None
                    or record is None
                    or frame.source_offset != record.source_offset
                    or frame.exact_bytes != record.exact_bytes
                ):
                    _fail(
                        "RAW_SPATIAL_TRACE_FRAMING_MISMATCH",
                        "tracked Trace parser differs from exact section framing",
                    )
                if frame.ordinal >= _MAX_ROWS:
                    _fail("RAW_SPATIAL_BOUND_EXCEEDED", "Trace section exceeds its row bound")
                required = (
                    record.source_id,
                    record.net,
                    record.starting_node_id,
                    record.ending_node_id,
                )
                if (
                    any(not isinstance(item, str) or not item for item in required)
                    or "MALFORMED_TRACE_RECORD" in record.issue_codes
                ):
                    _fail(
                        "RAW_SPATIAL_TRACE_MALFORMED",
                        "Trace identity or endpoints are malformed",
                        offset=record.source_offset,
                    )
                assert (
                    record.source_id
                    and record.net
                    and record.starting_node_id
                    and record.ending_node_id
                )
                for evidence in (
                    record.starting_node_net_evidence,
                    record.ending_node_net_evidence,
                ):
                    if evidence is not None and evidence.casefold() != record.net.casefold():
                        _fail(
                            "RAW_SPATIAL_ENDPOINT_NET_MISMATCH",
                            "Trace endpoint net differs from Trace net",
                            offset=record.source_offset,
                        )
                if record.geometry_status == "resolved":
                    width_status, width_location, geometry = "EXACT", record.width_location, "EXACT"
                elif record.geometry_status == "topology_only":
                    width_status, width_location, geometry = "MISSING", "ABSENT", "TOPOLOGY_ONLY"
                else:
                    width_status, width_location, geometry = "UNRESOLVED", "UNKNOWN", "UNRESOLVED"
                trace_batch.add(
                    (
                        frame.ordinal,
                        record.source_id,
                        record.source_id.casefold(),
                        record.net,
                        record.net.casefold(),
                        record.starting_node_id,
                        record.starting_node_id.casefold(),
                        record.ending_node_id,
                        record.ending_node_id.casefold(),
                        record.width_pm,
                        width_status,
                        width_location,
                        geometry,
                        record.source_sha256,
                    )
                )
                for node_id in (record.starting_node_id, record.ending_node_id):
                    incidence_batch.add((node_id.casefold(), record.net.casefold(), record.net))
                trace_count += 1
        except SpdTraceRecordError as exc:
            _fail(exc.code, str(exc), offset=exc.offset)
    trace_batch.flush()
    incidence_batch.flush()

    via_batch = _Batch(
        connection,
        "INSERT INTO vias (ordinal,via_id,via_fold,net_name,net_fold,start_node_id,start_node_fold,end_node_id,end_node_fold,padstack_id,padstack_fold,rotation,source_sha,source_offset,source_end) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        batch_rows,
        cancelled,
        "RAW_SPATIAL_VIA_DUPLICATE",
    )
    via_count = 0
    with path.open("rb") as handle:
        for record in _frame_section(handle, via_span, b"Via"):
            if record.ordinal >= _MAX_ROWS:
                _fail("RAW_SPATIAL_BOUND_EXCEEDED", "Via section exceeds its row bound")
            parsed_via = _parse_via(record, padstack_by_fold, layer_by_fold)
            via_batch.add(parsed_via + (record.source_offset, record.source_offset + len(record.exact_bytes)))
            via_key = (str(parsed_via[3]).casefold(), str(parsed_via[1]).casefold())
            if ownership_sink is not None and ownership_sink.selected("Via", *via_key):
                via_id = f"via:{parsed_via[1]}:{parsed_via[3]}"
                ownership_sink.stage_source(record_id=via_id, kind="Via", logical_net=parsed_via[3],
                    net_name=parsed_via[3], via_id=parsed_via[1], start_node_id=parsed_via[5],
                    end_node_id=parsed_via[7], padstack_id=parsed_via[9], rotation=parsed_via[11],
                    lookup=via_key, source_offset=record.source_offset,
                    source_end=record.source_offset + len(record.exact_bytes),
                    source_record_sha=parsed_via[12], raw_ordinal=record.ordinal)
            net, net_fold = parsed_via[3], parsed_via[4]
            for node_id, node_fold in (
                (parsed_via[5], parsed_via[6]),
                (parsed_via[7], parsed_via[8]),
            ):
                incidence_batch.add((node_fold, net_fold, net))
            via_count += 1
    via_batch.flush()
    incidence_batch.flush()
    if ownership_sink is not None:
        ownership_sink.derive_via_endpoint_selection()
    node_count = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    if (
        node_count == 0
        or trace_count != connection.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        or via_count != connection.execute("SELECT COUNT(*) FROM vias").fetchone()[0]
    ):
        _fail("RAW_SPATIAL_CARDINALITY_MISMATCH", "raw contact spool cardinality differs")
    return int(node_count), trace_count, via_count


def _resolve_contacts(
    connection: sqlite3.Connection,
    surface_keys: set[tuple[str, str]],
    pad_shape_keys: set[tuple[str, str]],
    drilled_padstack_keys: set[str],
    conductor_layer_order: Mapping[str, int],
    batch_rows: int,
    cancelled: Callable[[], bool],
) -> None:
    connection.execute(
        "UPDATE nodes SET resolved_net=explicit_net,resolved_net_fold=explicit_net_fold,"
        "net_status='EXPLICIT' "
        "WHERE explicit_net IS NOT NULL"
    )
    connection.execute(
        "UPDATE nodes SET resolved_net=(SELECT i.net_name FROM incidence i "
        "WHERE i.node_fold=nodes.node_fold),resolved_net_fold=(SELECT i.net_fold "
        "FROM incidence i WHERE i.node_fold=nodes.node_fold),net_status='INCIDENCE' "
        "WHERE explicit_net IS NULL "
        "AND (SELECT COUNT(*) FROM incidence i WHERE i.node_fold=nodes.node_fold)=1"
    )
    ambiguous = connection.execute(
        "SELECT ordinal,node_id,(SELECT COUNT(*) FROM incidence i "
        "WHERE i.node_fold=nodes.node_fold) FROM nodes WHERE explicit_net IS NULL "
        "AND (SELECT COUNT(*) FROM incidence i WHERE i.node_fold=nodes.node_fold)>1 "
        "ORDER BY ordinal LIMIT 1"
    ).fetchone()
    if ambiguous is not None:
        _fail(
            "RAW_SPATIAL_NODE_NET_AMBIGUOUS",
            f"Node {ambiguous[1]!r} has {ambiguous[2]} distinct incident nets",
        )
    connection.execute(
        "UPDATE nodes SET net_status='OUT_OF_SCOPE' WHERE explicit_net IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM incidence i WHERE i.node_fold=nodes.node_fold)"
    )
    unresolved = connection.execute(
        "SELECT ordinal,node_id FROM nodes WHERE net_status IS NULL ORDER BY ordinal LIMIT 1"
    ).fetchone()
    if unresolved is not None:
        _fail(
            "RAW_SPATIAL_NODE_NET_AMBIGUOUS",
            f"Node {unresolved[1]!r} has no deterministic net disposition",
        )
    try:
        connection.execute(
            "CREATE UNIQUE INDEX nodes_composite_identity "
            "ON nodes(resolved_net_fold,node_fold) WHERE resolved_net_fold IS NOT NULL"
        )
    except sqlite3.IntegrityError as exc:
        _fail(
            "RAW_SPATIAL_NODE_DUPLICATE", f"resolved composite Node identity is duplicated: {exc}"
        )
    for table in ("traces", "vias"):
        missing = connection.execute(
            f"SELECT s.ordinal FROM {table} s LEFT JOIN nodes a "
            "ON a.resolved_net_fold=s.net_fold AND a.node_fold=s.start_node_fold "
            "LEFT JOIN nodes b ON b.resolved_net_fold=s.net_fold "
            f"AND b.node_fold=s.end_node_fold WHERE a.ordinal IS NULL OR b.ordinal IS NULL LIMIT 1"
        ).fetchone()
        if missing is not None:
            _fail(
                "RAW_SPATIAL_ENDPOINT_UNRESOLVED",
                f"{table[:-1]} endpoint composite identity is absent",
            )
    crossed = connection.execute(
        "SELECT t.ordinal FROM traces t JOIN nodes a ON a.resolved_net_fold=t.net_fold "
        "AND a.node_fold=t.start_node_fold JOIN nodes b ON b.resolved_net_fold=t.net_fold "
        "AND b.node_fold=t.end_node_fold "
        "WHERE a.layer_fold<>b.layer_fold LIMIT 1"
    ).fetchone()
    if crossed is not None:
        _fail("RAW_SPATIAL_TRACE_CROSSES_LAYERS", "Trace endpoints name different layers")
    updates: list[tuple[str, int]] = []
    for row in connection.execute(
        "SELECT v.ordinal,v.net_fold,v.padstack_fold,a.layer_fold,a.x_pm,a.y_pm,"
        "b.layer_fold,b.x_pm,b.y_pm FROM vias v JOIN nodes a "
        "ON a.resolved_net_fold=v.net_fold AND a.node_fold=v.start_node_fold "
        "JOIN nodes b ON b.resolved_net_fold=v.net_fold AND b.node_fold=v.end_node_fold "
        "ORDER BY v.ordinal"
    ):
        if cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
        ordinal, net_fold, padstack_fold, start_layer, sx, sy, end_layer, ex, ey = row
        start_depth = conductor_layer_order.get(start_layer)
        end_depth = conductor_layer_order.get(end_layer)
        retained = (net_fold, start_layer) in surface_keys or (net_fold, end_layer) in surface_keys
        if not retained:
            status = "OUT_OF_SCOPE"
        elif (
            (sx, sy) == (ex, ey)
            and start_depth is not None
            and end_depth is not None
            and start_depth < end_depth
            and padstack_fold in drilled_padstack_keys
            and (padstack_fold, start_layer) in pad_shape_keys
            and (padstack_fold, end_layer) in pad_shape_keys
        ):
            status = "EXACT"
        else:
            status = "UNRESOLVED"
        updates.append((status, ordinal))
        if len(updates) >= batch_rows:
            connection.executemany("UPDATE vias SET status=? WHERE ordinal=?", updates)
            updates.clear()
    if updates:
        connection.executemany("UPDATE vias SET status=? WHERE ordinal=?", updates)


def _node_rows(connection: sqlite3.Connection) -> Iterator[RawSpatialNodeRow]:
    for row in connection.execute(
        "SELECT ordinal,node_id,resolved_net,net_status,layer_id,x_pm,y_pm,padstack_id,"
        "rotation,source_sha FROM nodes ORDER BY ordinal"
    ):
        yield RawSpatialNodeRow(*row)


def _trace_rows(connection: sqlite3.Connection) -> Iterator[RawSpatialTraceRow]:
    query = (
        "SELECT t.ordinal,t.trace_id,t.net_name,a.layer_id,t.start_node_id,t.end_node_id,"
        "t.width_pm,t.width_status,t.width_location,t.geometry_status,t.source_sha,a.x_pm,"
        "a.y_pm,b.x_pm,b.y_pm FROM traces t JOIN nodes a "
        "ON a.resolved_net_fold=t.net_fold AND a.node_fold=t.start_node_fold JOIN nodes b "
        "ON b.resolved_net_fold=t.net_fold AND b.node_fold=t.end_node_fold ORDER BY t.ordinal"
    )
    for row in connection.execute(query):
        (
            ordinal,
            trace_id,
            net,
            layer,
            start,
            end,
            width,
            width_status,
            location,
            geometry,
            source_sha,
            sx,
            sy,
            ex,
            ey,
        ) = row
        yield RawSpatialTraceRow(
            ordinal,
            trace_id,
            net,
            layer,
            start,
            end,
            width,
            width_status,
            location,
            geometry,
            f"trace:{net.casefold()}:{trace_id.casefold()}",
            source_sha,
            min(sx, ex),
            min(sy, ey),
            max(sx, ex),
            max(sy, ey),
        )


def _via_rows(connection: sqlite3.Connection) -> Iterator[RawSpatialViaRow]:
    query = (
        "SELECT v.ordinal,v.via_id,v.net_name,a.layer_id,b.layer_id,v.start_node_id,v.end_node_id,"
        "v.padstack_id,v.status,a.x_pm,a.y_pm,b.x_pm,b.y_pm,v.rotation,v.source_sha "
        "FROM vias v JOIN nodes a ON a.resolved_net_fold=v.net_fold "
        "AND a.node_fold=v.start_node_fold JOIN nodes b ON b.resolved_net_fold=v.net_fold "
        "AND b.node_fold=v.end_node_fold ORDER BY v.ordinal"
    )
    for row in connection.execute(query):
        (
            ordinal,
            via_id,
            net,
            start_layer,
            end_layer,
            start,
            end,
            padstack,
            status,
            sx,
            sy,
            ex,
            ey,
            rotation,
            source_sha,
        ) = row
        yield RawSpatialViaRow(
            ordinal,
            via_id,
            net,
            start_layer,
            end_layer,
            start,
            end,
            padstack,
            status,
            f"via:{net.casefold()}:{via_id.casefold()}",
            sx,
            sy,
            ex,
            ey,
            rotation,
            source_sha,
        )


def _compile_snapshot(
    path: Path,
    *,
    original_path: Path,
    initial_state: tuple[int, int, int, int],
    size: int,
    observed_sha: str,
    analysis: Any,
    project: Any,
    attachments: Mapping[str, bytes],
    bindings: Mapping[str, str],
    batch_rows: int,
    cancelled: Callable[[], bool],
    temp_dir: Path,
    plane_sheet_payload: Mapping[str, Any] | None = None,
    ownership_request: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[str, bytes]]:
    offsets = _index_markers(path, size, cancelled)
    spans = _section_spans(path, size, offsets, cancelled)
    spool_path = temp_dir / "raw-spatial-spool.sqlite"
    connection = sqlite3.connect(spool_path)
    def run_or_close(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except BaseException:
            connection.close()
            raise
    run_or_close(lambda: (_configure_spool(connection, cancelled), connection.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + _SPOOL_SQL)))
    ownership_sink = run_or_close(lambda: _OwnershipSink(connection, batch_rows, cancelled) if ownership_request is not None else None)
    if ownership_sink is not None:
        run_or_close(lambda: ownership_sink.ingest_selection(ownership_request.get("raw_selection")))
        run_or_close(lambda: _create_source_plane_ownership_v2_tables(connection))
    layer_rows, layer_by_fold = run_or_close(lambda: _parse_layers(
        path, offsets[_LAYER_MARKER], offsets[_NODE_MARKER], analysis, cancelled, ownership_sink,
    ))
    conductor_layer_order = run_or_close(lambda: {
        row.layer_id.casefold(): row.ordinal
        for row in layer_rows
        if row.is_conductor
    })
    pad_end = run_or_close(lambda: min(
        (
            offset
            for marker, offset in offsets.items()
            if marker in {_MATERIAL_MARKER, _CIRCUIT_MARKER} and offset > offsets[_PAD_MARKER]
        ),
        default=size,
    ))
    padstack_rows, pad_shape_rows, padstack_by_fold, pad_shape_keys = run_or_close(lambda: _parse_padstacks(
        path, offsets[_PAD_MARKER], pad_end, analysis, layer_by_fold, cancelled, ownership_sink,
    ))
    surface_rows, surface_keys = run_or_close(lambda: _parse_surfaces(
        path, offsets[_LAYER_MARKER], analysis, project, attachments, layer_by_fold, cancelled, ownership_sink,
    ))
    if ownership_sink is not None:
        run_or_close(lambda: (
            ownership_sink.stage_surfaces(surface_rows, project),
            ownership_sink.stage_primitives(plane_sheet_payload),
        ))
    try:
        node_count, trace_count, via_count = _parse_contacts(
            path,
            spans,
            connection,
            layer_by_fold,
            padstack_by_fold,
            batch_rows,
            cancelled,
            ownership_sink,
        )
        _resolve_contacts(
            connection,
            surface_keys,
            pad_shape_keys,
            {
                row.padstack_id.casefold()
                for row in padstack_rows
                if row.drill_diameter_pm is not None
            },
            conductor_layer_order,
            batch_rows,
            cancelled,
        )
        if ownership_sink is not None:
            ownership_sink.stage_material_draft(getattr(analysis, "source_plane_ownership_draft", None))
            ownership_sink.finalize_contact_sources()
            connection.execute(
                "INSERT INTO source_records(ordinal,record_id,kind,source_offset,source_end,source_record_sha256,logical_net,layer) "
                "SELECT ROW_NUMBER() OVER (ORDER BY source_offset,record_id COLLATE BINARY)-1,record_id,kind,source_offset,source_end,source_record_sha256,logical_net,layer "
                "FROM ownership_source_stage ORDER BY source_offset,record_id COLLATE BINARY"
            )
            if plane_sheet_payload is not None:
                sheet_rows, _ = _plane_sheet_rows(plane_sheet_payload)
                draft = getattr(analysis, "source_plane_ownership_draft", None)
                draft_stack = {
                    str(row.get("layer_name", "")).casefold(): dict(row)
                    for row in (draft.get("stackup_layers", ()) if isinstance(draft, Mapping) else ())
                    if isinstance(row, Mapping)
                }
                draft_points = {
                    (str(row.get("layer_name", "")).casefold(), int(row.get("point_ordinal", -1))): dict(row)
                    for row in (draft.get("dielectric_points", ()) if isinstance(draft, Mapping) else ())
                    if isinstance(row, Mapping)
                }
                selected_layers = {
                    str(row[0]).casefold()
                    for row in connection.execute(
                        "SELECT key_a FROM ownership_selection WHERE kind='Layer'"
                    )
                }
                actual_record_ids = {
                    str(row[0]).casefold()
                    for row in connection.execute("SELECT record_id FROM ownership_source_stage")
                }

                def canonical_row_hash(row: Mapping[str, Any]) -> str:
                    encoded = json.dumps(
                        dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                    return sha256(encoded).hexdigest()

                stackup_columns = (
                    "ordinal", "layer_name", "layer_kind", "raw_layer_ordinal",
                    "raw_layer_sha256", "thickness_um", "thickness_origin",
                    "thickness_source_record_id", "conductivity_s_per_m",
                    "conductivity_origin", "conductivity_source_record_id",
                    "material_name", "material_origin", "material_source_record_id",
                )
                stackup_ordinal = 0
                for raw_row in sheet_rows.get("stackup_layers", ()):
                    layer_key = str(raw_row.get("layer_name", "")).casefold()
                    if layer_key not in selected_layers:
                        continue
                    base = draft_stack.get(layer_key)
                    if base is None:
                        _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", "stackup provenance is absent")
                    merged = {
                        **base,
                        **dict(raw_row),
                        "ordinal": stackup_ordinal,
                        "raw_layer_ordinal": raw_row.get("layer_ordinal"),
                        "raw_layer_sha256": canonical_row_hash(raw_row),
                    }
                    for field in (
                        "thickness_source_record_id", "conductivity_source_record_id",
                        "material_source_record_id",
                    ):
                        value = merged.get(field)
                        if value is not None and (not isinstance(value, str) or value.casefold() not in actual_record_ids):
                            _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", f"{field} source record is absent")
                    connection.execute(
                        f"INSERT INTO stackup_layers ({','.join(stackup_columns)}) VALUES ({','.join('?' for _ in stackup_columns)})",
                        tuple(merged.get(column) for column in stackup_columns),
                    )
                    stackup_ordinal += 1

                dielectric_columns = (
                    "ordinal", "layer_name", "point_ordinal", "raw_dielectric_ordinal",
                    "raw_dielectric_sha256", "frequency_hz", "frequency_origin",
                    "frequency_source_record_id", "epsilon_r", "epsilon_origin",
                    "epsilon_source_record_id", "loss_tangent", "loss_tangent_origin",
                    "loss_tangent_source_record_id",
                )
                dielectric_ordinal = 0
                raw_layer_names = {
                    int(row.get("layer_ordinal", -1)): str(row.get("layer_name", "")).casefold()
                    for row in sheet_rows.get("stackup_layers", ())
                }
                for global_point_index, raw_row in enumerate(sheet_rows.get("dielectric_points", ())):
                    layer_key = raw_layer_names.get(int(raw_row.get("layer_ordinal", -1)), "")
                    if layer_key not in selected_layers:
                        continue
                    point_key = (layer_key, int(raw_row.get("point_ordinal", -1)))
                    base = draft_points.get(point_key)
                    if base is None:
                        _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", "dielectric provenance is absent")
                    merged = {
                        **base,
                        **dict(raw_row),
                        "ordinal": dielectric_ordinal,
                        "raw_dielectric_ordinal": global_point_index,
                        "raw_dielectric_sha256": canonical_row_hash(raw_row),
                    }
                    for field in (
                        "frequency_source_record_id", "epsilon_source_record_id",
                        "loss_tangent_source_record_id",
                    ):
                        value = merged.get(field)
                        if value is not None and (not isinstance(value, str) or value.casefold() not in actual_record_ids):
                            _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", f"{field} source record is absent")
                    connection.execute(
                        f"INSERT INTO dielectric_points ({','.join(dielectric_columns)}) VALUES ({','.join('?' for _ in dielectric_columns)})",
                        tuple(merged.get(column) for column in dielectric_columns),
                    )
                    dielectric_ordinal += 1
        connection.commit()
        if spool_path.stat().st_size > _MAX_SPOOL_BYTES:
            _fail(
                "RAW_SPATIAL_SPOOL_BOUND_EXCEEDED",
                "temporary contact spool exceeded its byte bound",
            )
        if cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
        if _source_state(original_path) != initial_state:
            _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD changed during compilation")
        if _stream_sha256(original_path, 0, size, cancelled) != observed_sha:
            _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD bytes changed during compilation")
        counts = {"Node": node_count, "Trace": trace_count, "Via": via_count}
        node_resolved = connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE net_status IN ('EXPLICIT','INCIDENCE')"
        ).fetchone()[0]
        node_out = connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE net_status='OUT_OF_SCOPE'"
        ).fetchone()[0]
        if node_resolved + node_out != node_count:
            _fail(
                "RAW_SPATIAL_NODE_NET_AMBIGUOUS",
                "Node net dispositions do not cover the parsed Node inventory",
            )
        trace_resolved = connection.execute(
            "SELECT COUNT(*) FROM traces WHERE geometry_status='EXACT'"
        ).fetchone()[0]
        via_resolved = connection.execute(
            "SELECT COUNT(*) FROM vias WHERE status='EXACT'"
        ).fetchone()[0]
        via_out = connection.execute(
            "SELECT COUNT(*) FROM vias WHERE status='OUT_OF_SCOPE'"
        ).fetchone()[0]
        coverage = (
            RawSpatialSectionCoverageRow(
                0,
                "Node",
                spans[0].start,
                spans[0].end,
                spans[0].end - spans[0].start,
                spans[0].sha256,
                node_count,
                node_count,
                node_count,
                node_resolved,
                node_out,
                node_resolved,
                node_out,
            ),
            RawSpatialSectionCoverageRow(
                1,
                "Trace",
                spans[1].start,
                spans[1].end,
                spans[1].end - spans[1].start,
                spans[1].sha256,
                trace_count,
                trace_count,
                trace_count,
                trace_resolved,
                trace_count - trace_resolved,
                trace_count,
                0,
            ),
            RawSpatialSectionCoverageRow(
                2,
                "Via",
                spans[2].start,
                spans[2].end,
                spans[2].end - spans[2].start,
                spans[2].sha256,
                via_count,
                via_count,
                via_count,
                via_resolved,
                via_count - via_resolved,
                via_count - via_out,
                via_out,
            ),
        )
        total = sum(counts.values())
        source_coverage = RawSpatialSourceCoverageRow(
            0, original_path.name, size, observed_sha, total, total
        )
        result = build_raw_spatial_contact_asset(
            source_path=path,
            source_sha256=observed_sha,
            source_coverage=source_coverage,
            section_coverage=coverage,
            layers=layer_rows,
            padstacks=padstack_rows,
            pad_shapes=pad_shape_rows,
            surfaces=surface_rows,
            nodes=_node_rows(connection),
            traces=_trace_rows(connection),
            vias=_via_rows(connection),
            plane_sheet_payload=plane_sheet_payload,
            batch_rows=batch_rows,
            is_cancelled=cancelled,
            **bindings,
        )
        if _source_state(original_path) != initial_state:
            _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD changed during asset build")
        if _stream_sha256(original_path, 0, size, cancelled) != observed_sha:
            _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD bytes changed during asset build")
        if _source_state(original_path) != initial_state:
            _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD changed during final source hash")
        if ownership_sink is not None:
            # Final source records are selected and ordered entirely in SQLite.
            missing = connection.execute(
                "SELECT 1 FROM ownership_selection s WHERE NOT EXISTS (SELECT 1 FROM ownership_source_stage r WHERE r.lookup_a=s.key_a AND r.lookup_b=s.key_b AND ((s.kind='Layer' AND r.kind='Layer') OR (s.kind='Node' AND r.kind='Node') OR (s.kind='Via' AND r.kind='Via') OR (s.kind='Pad' AND r.kind IN ('PadDef','Regular')) OR (s.kind='Surface' AND r.kind='Shape') OR (s.kind='Material' AND r.kind='Material'))) LIMIT 1"
            ).fetchone()
            if missing is not None:
                _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", "selected ownership source record is absent")
            missing_pad = connection.execute(
                "SELECT 1 FROM ownership_selection s WHERE s.kind='Pad' AND "
                "(SELECT COUNT(*) FROM ownership_source_stage r WHERE r.kind IN ('PadDef','Regular') "
                "AND r.lookup_a=s.key_a AND r.lookup_b=s.key_b) < 2 LIMIT 1"
            ).fetchone()
            if missing_pad is not None:
                _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", "selected PadDef/Regular source records are incomplete")
            checks = (
                ("Material", "r.kind='Material' AND r.lookup_a=s.key_a"),
                ("PadDef", "r.kind='PadDef' AND r.lookup_a=s.key_a AND r.lookup_b=s.key_b"),
                ("Regular", "r.kind='Regular' AND r.lookup_a=s.key_a AND r.lookup_b=s.key_b"),
            )
            for kind, predicate in checks:
                selection_kind = "Material" if kind == "Material" else "Pad"
                if connection.execute(
                    f"SELECT 1 FROM ownership_selection s WHERE s.kind=? "
                    f"AND (SELECT COUNT(*) FROM ownership_source_stage r WHERE {predicate}) <> 1 LIMIT 1",
                    (selection_kind,),
                ).fetchone() is not None:
                    _fail("RAW_SPATIAL_OWNERSHIP_EVIDENCE_INCOMPLETE", f"selected {kind} source record is not exact-one")
            connection.commit()
        return result
    except sqlite3.OperationalError as exc:
        _translate_spool_error(exc, cancelled)
    finally:
        connection.close()
    raise AssertionError("unreachable")


def compile_raw_spatial_contact_asset(
    source_path: str | Path,
    *,
    analysis: SpdAnalysis,
    project: ProjectSpec,
    attachments: Mapping[str, bytes],
    batch_rows: int = 10_000,
    is_cancelled: Callable[[], bool] | None = None,
    include_plane_sheet_payload: bool = False,
    source_plane_ownership_request: Mapping[str, Any] | None = None,
    source_plane_ownership_callback: Callable[[_SourcePlaneOwnershipRawEvidence], Any] | None = None,
) -> tuple[dict[str, Any], tuple[str, bytes]]:
    """Compile exact raw spatial contacts without mutating or wiring a project."""

    cancelled = is_cancelled or (lambda: False)
    if (
        isinstance(batch_rows, bool)
        or not isinstance(batch_rows, int)
        or not 1 <= batch_rows <= _MAX_BATCH_ROWS
    ):
        _fail("RAW_SPATIAL_BATCH_INVALID", "batch_rows must be in [1, 10000]")
    if type(include_plane_sheet_payload) is not bool:
        _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "include_plane_sheet_payload must be boolean")
    if (source_plane_ownership_request is None) != (source_plane_ownership_callback is None):
        _fail("RAW_SPATIAL_OWNERSHIP_CALLBACK_INVALID", "ownership request and callback must be supplied together")
    if source_plane_ownership_request is not None and not isinstance(source_plane_ownership_request, Mapping):
        _fail("RAW_SPATIAL_OWNERSHIP_CALLBACK_INVALID", "ownership request must be a mapping")
    if source_plane_ownership_request is not None and "raw_selection" not in source_plane_ownership_request:
        _fail("RAW_SPATIAL_OWNERSHIP_SELECTION_INVALID", "raw_selection is required")
    if source_plane_ownership_request is not None and not include_plane_sheet_payload:
        _fail("RAW_SPATIAL_PLANE_SHEET_REQUIRED", "ownership compilation requires plane-sheet payload")
    path = Path(source_path)
    if not path.is_file():
        _fail("RAW_SPATIAL_SOURCE_MISSING", f"raw SPD {path} is absent")
    initial_state = _source_state(path)
    size = initial_state[2]
    if size <= 0:
        _fail("RAW_SPATIAL_SOURCE_INVALID", "raw SPD is empty")
    if size > _MAX_SOURCE_BYTES:
        _fail(
            "RAW_SPATIAL_SOURCE_BOUND_EXCEEDED",
            "raw SPD exceeds the 4 GiB compiler snapshot bound",
        )
    source = getattr(analysis, "source", None)
    try:
        analysis_path = Path(source.path).resolve(strict=True)
        actual_path = path.resolve(strict=True)
    except (AttributeError, OSError) as exc:
        _fail(
            "RAW_SPATIAL_ANALYSIS_SOURCE_INVALID",
            f"SpdAnalysis source identity is incomplete: {exc}",
        )
    if os.path.normcase(str(analysis_path)) != os.path.normcase(str(actual_path)):
        _fail("RAW_SPATIAL_ANALYSIS_SOURCE_MISMATCH", "source path differs from SpdAnalysis")
    if source.name != path.name or source.size_bytes != size or source.mtime_ns != initial_state[3]:
        _fail(
            "RAW_SPATIAL_ANALYSIS_SOURCE_MISMATCH", "source stat identity differs from SpdAnalysis"
        )
    observed_sha = _stream_sha256(path, 0, size, cancelled)
    if source.sha256 != observed_sha:
        _fail("RAW_SPATIAL_ANALYSIS_SOURCE_MISMATCH", "source SHA-256 differs from SpdAnalysis")
    spd_import = _project_spd_import(project)
    if str(spd_import.get("source_sha256", "")) != observed_sha:
        _fail("RAW_SPATIAL_PROJECT_SOURCE_MISMATCH", "project names a different raw SPD")
    try:
        plane_sheet_payload = _plane_sheet_payload(project, analysis) if include_plane_sheet_payload else None
        if plane_sheet_payload is not None:
            _validate_plane_sheet_assets(plane_sheet_payload, attachments)
        _surface, compiled = validate_project_topology_storage_envelope(project, attachments)
    except SurfaceCertificateAssetError as exc:
        _fail(
            "RAW_SPATIAL_TOPOLOGY_ENVELOPE_INVALID", f"topology envelope failed [{exc.code}]: {exc}"
        )
    if not isinstance(compiled, Mapping):
        _fail("RAW_SPATIAL_COMPILED_TOPOLOGY_REQUIRED", "compiled topology manifest is absent")
    if str(compiled.get("source_sha256", "")) != observed_sha:
        _fail(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_MISMATCH", "compiled topology names a different source"
        )
    bindings = {
        "project_binding_sha256": str(compiled.get("project_binding_sha256", "")),
        "certificate_evidence_sha256": str(compiled.get("certificate_evidence_sha256", "")),
        "compiled_topology_identity_sha256": str(compiled.get("topology_identity_sha256", "")),
    }
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in bindings.values()):
        _fail(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_MISMATCH",
            "compiled topology binding identities are invalid",
        )

    with tempfile.TemporaryDirectory(prefix="spdpi-raw-compiler-") as temp_dir:
        temp_path = Path(temp_dir)
        snapshot_path = temp_path / path.name
        snapshot_sha = _snapshot_source(path, snapshot_path, size, cancelled)
        if snapshot_sha != observed_sha or _source_state(path) != initial_state:
            _fail("RAW_SPATIAL_SOURCE_MUTATED", "raw SPD changed while snapshotting")
        result = _compile_snapshot(
            snapshot_path,
            original_path=path,
            initial_state=initial_state,
            size=size,
            observed_sha=observed_sha,
            analysis=analysis,
            project=project,
            attachments=attachments,
            bindings=bindings,
            batch_rows=batch_rows,
            cancelled=cancelled,
            temp_dir=temp_path,
            plane_sheet_payload=plane_sheet_payload,
            ownership_request=source_plane_ownership_request,
        )
        if source_plane_ownership_callback is not None:
            if cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compilation was cancelled")
            source_plane_ownership_callback(
                _SourcePlaneOwnershipRawEvidence(
                    result[0], result[1], _SourcePlaneOwnershipSpool(temp_path / "raw-spatial-spool.sqlite")
                )
            )
        return result


__all__ = ["RawSpatialCompilerError", "_SourcePlaneOwnershipRawEvidence", "compile_raw_spatial_contact_asset"]
