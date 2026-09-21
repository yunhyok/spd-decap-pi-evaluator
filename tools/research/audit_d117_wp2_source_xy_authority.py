#!/usr/bin/env python
"""Bounded native-SPD census for the D117 WP2 XY authority gate.

The native SPD grammar exposes conductor ``Shape`` artwork, stack-up rows, and
material-model tables.  It does not expose a source-native record for XY
dielectric partitions or conductor-layer void fill.  This read-only census
records that fact while streaming the source one bounded line at a time.  It
does not invent a sidecar or marker contract, infer geometry/material values,
or authorize a solver.  ``PASS`` is intentionally unreachable until a real
source-native representation and parser are added.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Mapping


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SCHEMA_VERSION = "d117-wp2-src-xy-auth-00-native-v1"
SCOPE = "WP2-SRC-XY-AUTH-00"

PASS = "PASS"  # Reserved; no native-SPD path can return it today.
STOP_NOT_REPRESENTED = "STOP_NOT_REPRESENTED"
STOP_AMBIGUOUS = "STOP_AMBIGUOUS"
STOP_UNSEALED = "STOP_UNSEALED"
STOP_INPUT_IDENTITY_MISMATCH = "STOP_INPUT_IDENTITY_MISMATCH"
STOP_OUTPUT_EXISTS = "STOP_OUTPUT_EXISTS"

MAX_LINE_BYTES = 1 << 20
MAX_ISSUES = 64
MAX_NAMES = 1024
MAX_NAME_BYTES = 256
MAX_NAME_BYTES_TOTAL = 16 * 1024

_LENGTH_RE = re.compile(
    rb"([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)"
    rb"\s*(mil|mm|um|u|m)(?![A-Za-z])",
    re.IGNORECASE,
)
_FLOAT_RE = re.compile(rb"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?")
_SHAPE_HEADER_RE = re.compile(rb"^\.Shape[ \t]+(?P<name>\S+)", re.IGNORECASE)
_SHAPE_PRIMITIVE_RE = re.compile(
    rb"^(?P<kind>[A-Za-z_]+)[^\r\n\s]*::(?P<net>\S+?)(?P<polarity>[+-])(?:\s+|$)",
)
_LAYER_RE = re.compile(rb"^(?P<name>\S+)\s+Thickness\s*=\s*(?P<thickness>\S+)(?P<tail>.*)$", re.IGNORECASE)
_DIELECTRIC_RE = re.compile(rb"^\.DielectricModel[ \t]+(?P<name>\S+)", re.IGNORECASE)
_METAL_RE = re.compile(rb"^\.MetalModel[ \t]+(?P<name>\S+)", re.IGNORECASE)
_END_DIELECTRIC_RE = re.compile(rb"^\.EndDielectricModel\b", re.IGNORECASE)
_END_METAL_RE = re.compile(rb"^\.EndMetalModel\b", re.IGNORECASE)
_SHAPE_KINDS = frozenset(("Polygon", "PolygonTrace", "Circle", "Box"))
_CANDIDATE_XY_PREFIXES = (b".dielectricpartition", b"dielectricpartition", b"dielectric_partition", b"xy_dielectric")
_CANDIDATE_FILL_PREFIXES = (b".voidfill", b"voidfill", b"void_fill", b".conductorvoid", b"conductorvoid")


class Refusal(RuntimeError):
    """A CLI/output refusal; source evidence itself returns a receipt STOP."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"{status}: {message}")


def _canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError) as exc:
        raise Refusal(STOP_INPUT_IDENTITY_MISMATCH, f"canonical JSON failed: {exc}") from exc


def _token_prefix(value: bytes, prefix: bytes) -> bool:
    if not value.startswith(prefix):
        return False
    suffix = value[len(prefix) : len(prefix) + 1]
    return not suffix or suffix not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"


def _prefix_kind(raw: bytes) -> str | None:
    """Classify bounded line prefixes for ordinary and overlong records."""

    value = raw.lstrip().lower()
    if any(_token_prefix(value, prefix) for prefix in _CANDIDATE_XY_PREFIXES):
        return "xy_candidate"
    if any(_token_prefix(value, prefix) for prefix in _CANDIDATE_FILL_PREFIXES):
        return "fill_candidate"
    if _token_prefix(value, b".shape"):
        return "shape"
    if _token_prefix(value, b".dielectricmodel"):
        return "dielectric_model"
    if _token_prefix(value, b".metalmodel"):
        return "metal_model"
    if value.startswith((b"polygontrace", b"polygon", b"circle", b"box")):
        return "shape_primitive"
    if b"thickness" in value:
        return "thickness"
    return None


def _material_row_error(raw: bytes, model_kind: str) -> str | None:
    """Validate one numeric-leading material row without retaining its values."""

    count = 0
    position = 0
    for match in _FLOAT_RE.finditer(raw):
        gap = raw[position : match.start()]
        if (count == 0 and match.start() != 0) or (count and (not gap or gap.strip())):
            return "material model row is not numeric"
        try:
            value = float(match.group(0))
        except ValueError:
            return "material model row is not numeric"
        if not math.isfinite(value):
            return "material model row is non-finite"
        count += 1
        position = match.end()
    if count == 0 or position != len(raw):
        return "material model row is not numeric"
    minimum = 3 if model_kind == "dielectric" else 2
    if count < minimum:
        return f"{model_kind} model row requires at least {minimum} numeric values"
    return None


def _length_um(value: bytes) -> float:
    match = _LENGTH_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid SPD length {value!r}")
    number = float(match.group(1))
    scale = {b"m": 1.0e6, b"mm": 1.0e3, b"um": 1.0, b"u": 1.0, b"mil": 25.4}[match.group(2).lower()]
    result = number * scale
    if not math.isfinite(result):
        raise ValueError("SPD length is not finite")
    return result


def _add_issue(issues: list[dict[str, Any]], kind: str, line: int, message: str) -> None:
    item = {"kind": kind, "line_number": line, "message": message}
    if item not in issues and len(issues) < MAX_ISSUES:
        issues.append(item)


def _record(scan: dict[str, Any], key: str, line: int, offset: int | None) -> None:
    scan["native_record_counts"][key] += 1
    spans = scan["record_spans"].setdefault(key, {"count": 0, "first": None, "last": None})
    spans["count"] += 1
    point = {"line_number": line, "byte_offset": offset} if offset is not None else None
    if spans["first"] is None and point is not None:
        spans["first"] = point
    if point is not None:
        spans["last"] = point


def _name(scan: dict[str, Any], key: str, raw_value: bytes, kind: str, line: int) -> None:
    if len(raw_value) > MAX_NAME_BYTES:
        _add_issue(scan["issues"], kind, line, f"{key} name exceeds {MAX_NAME_BYTES} bytes")
        return
    value = raw_value.decode("utf-8", errors="replace")
    values = scan[key]
    if value in values:
        return
    if len(values) >= MAX_NAMES:
        _add_issue(scan["issues"], kind, line, f"{key} exceeds bounded name census")
        return
    retained = scan["retained_name_bytes"]
    if retained + len(raw_value) > MAX_NAME_BYTES_TOTAL:
        _add_issue(scan["issues"], kind, line, f"retained native names exceed {MAX_NAME_BYTES_TOTAL} bytes")
        return
    values.append(value)
    scan["retained_name_bytes"] = retained + len(raw_value)


def _new_scan() -> dict[str, Any]:
    return {
        "native_record_counts": {
            "shape_headers": 0,
            "supported_shape_primitives": 0,
            "unsupported_shape_primitives": 0,
            "dielectric_models": 0,
            "metal_models": 0,
            "material_table_rows": 0,
            "stackup_layers": 0,
        },
        "record_spans": {},
        "shape_layers": [],
        "dielectric_model_names": [],
        "metal_model_names": [],
        "stackup_layer_names": [],
        "retained_name_bytes": 0,
        "issues": [],
        "active_shape": False,
        "active_primitive": None,
        "active_model": None,
        "active_model_rows": 0,
    }


def _finish_primitive(scan: dict[str, Any]) -> None:
    primitive = scan["active_primitive"]
    if primitive is None:
        return
    kind = primitive["kind"]
    value_count = primitive["value_count"]
    dimensions = primitive["dimensions"]
    line = primitive["line_number"]
    valid = (
        value_count >= 6 and value_count % 2 == 0
        if kind in {"Polygon", "PolygonTrace"}
        else value_count == 3 and len(dimensions) >= 3 and dimensions[2] > 0
        if kind == "Circle"
        else value_count == 4 and len(dimensions) >= 4 and dimensions[2] > 0 and dimensions[3] > 0
        if kind == "Box"
        else False
    )
    if not valid:
        _add_issue(scan["issues"], "shape", line, f"malformed {kind} primitive")
    else:
        _record(scan, "supported_shape_primitives", line, primitive.get("byte_offset"))
    scan["active_primitive"] = None


def _consume_line(scan: dict[str, Any], raw: bytes, line_number: int, byte_offset: int) -> None:
    stripped = raw.strip()
    if not stripped or stripped.startswith(b"*"):
        return
    lowered = stripped.lower()
    prefix_kind = _prefix_kind(stripped)
    if prefix_kind == "xy_candidate":
        _add_issue(scan["issues"], "shape", line_number, "unrecognized XY dielectric candidate record")
        return
    if prefix_kind == "fill_candidate":
        _add_issue(scan["issues"], "stackup", line_number, "unrecognized conductor void-fill candidate record")
        return
    if _SHAPE_HEADER_RE.match(stripped):
        _finish_primitive(scan)
        match = _SHAPE_HEADER_RE.match(stripped)
        assert match is not None
        scan["active_shape"] = True
        _record(scan, "shape_headers", line_number, byte_offset)
        _name(scan, "shape_layers", match.group("name"), "shape", line_number)
        return
    if prefix_kind == "shape":
        _finish_primitive(scan)
        _add_issue(scan["issues"], "shape", line_number, "malformed Shape header")
        scan["active_shape"] = True
        return
    if _token_prefix(lowered, b".endshape"):
        _finish_primitive(scan)
        return

    if scan["active_shape"]:
        continuation = stripped.startswith(b"+")
        if continuation:
            primitive = scan["active_primitive"]
            if primitive is None:
                _add_issue(scan["issues"], "shape", line_number, "shape continuation has no primitive")
                return
            try:
                value_count = 0
                for match in _LENGTH_RE.finditer(stripped[1:]):
                    value = _length_um(match.group(0))
                    value_count += 1
                    dimensions = primitive["dimensions"]
                    if dimensions is not None and len(dimensions) < 4:
                        dimensions.append(value)
                if value_count == 0:
                    raise ValueError("shape continuation has no length values")
                primitive["value_count"] += value_count
            except (TypeError, ValueError) as exc:
                _add_issue(scan["issues"], "shape", line_number, f"malformed shape continuation: {exc}")
            return
        _finish_primitive(scan)
        match = _SHAPE_PRIMITIVE_RE.match(stripped)
        if match is not None:
            raw_kind = match.group("kind")
            kind = raw_kind.decode("ascii", errors="ignore")
            if kind not in _SHAPE_KINDS:
                _record(scan, "unsupported_shape_primitives", line_number, byte_offset)
                _add_issue(scan["issues"], "shape", line_number, f"unsupported Shape primitive {kind!r}")
                return
            value_count = 0
            dimensions: list[float] | None = [] if kind in {"Circle", "Box"} else None
            try:
                for item in _LENGTH_RE.finditer(stripped):
                    value = _length_um(item.group(0))
                    value_count += 1
                    if dimensions is not None and len(dimensions) < 4:
                        dimensions.append(value)
            except (TypeError, ValueError) as exc:
                _add_issue(scan["issues"], "shape", line_number, f"malformed {kind} primitive: {exc}")
                return
            scan["active_primitive"] = {
                "kind": kind,
                "value_count": value_count,
                "dimensions": dimensions,
                "line_number": line_number,
                "byte_offset": byte_offset,
            }
            return
        if b"::" in stripped and (stripped.endswith(b"+") or stripped.endswith(b"-")):
            _add_issue(scan["issues"], "shape", line_number, "malformed Shape primitive")
            return
        if any(_token_prefix(lowered, prefix) for prefix in (b"polygon", b"circle", b"box")):
            _add_issue(scan["issues"], "shape", line_number, "malformed Shape primitive")
            return

    dielectric = _DIELECTRIC_RE.match(stripped)
    metal = _METAL_RE.match(stripped)
    if dielectric is not None or metal is not None:
        _finish_primitive(scan)
        scan["active_shape"] = False
        if scan["active_model"] is not None:
            if scan["active_model_rows"] == 0:
                _add_issue(scan["issues"], "material", line_number, "material model has no numeric rows")
            _add_issue(scan["issues"], "material", line_number, "material model starts before prior model ended")
        model_kind = "dielectric" if dielectric is not None else "metal"
        match = dielectric if dielectric is not None else metal
        assert match is not None
        raw_name = match.group("name")
        name = raw_name.decode("utf-8", errors="replace") if len(raw_name) <= MAX_NAME_BYTES else None
        names = scan[f"{model_kind}_model_names"]
        if name is not None and name.casefold() in {item.casefold() for item in names}:
            _add_issue(scan["issues"], "material", line_number, f"duplicate {model_kind} model {name!r}")
        _name(scan, f"{model_kind}_model_names", raw_name, "material", line_number)
        _record(scan, f"{model_kind}_models", line_number, byte_offset)
        scan["active_model"] = model_kind
        scan["active_model_rows"] = 0
        return
    if prefix_kind in {"dielectric_model", "metal_model"}:
        _add_issue(scan["issues"], "material", line_number, "malformed material model header")
        return
    if _END_DIELECTRIC_RE.match(stripped) or _END_METAL_RE.match(stripped):
        end_kind = "dielectric" if _END_DIELECTRIC_RE.match(stripped) else "metal"
        if scan["active_model"] != end_kind:
            _add_issue(scan["issues"], "material", line_number, f"unexpected .End{end_kind.title()}Model")
        elif scan["active_model_rows"] == 0:
            _add_issue(scan["issues"], "material", line_number, f"{end_kind} model has no numeric rows")
        scan["active_model"] = None
        scan["active_model_rows"] = 0
        return
    if scan["active_model"] is not None and not stripped.startswith(b"."):
        row_error = _material_row_error(stripped, scan["active_model"])
        if row_error is None:
            scan["active_model_rows"] += 1
            _record(scan, "material_table_rows", line_number, byte_offset)
        else:
            _add_issue(scan["issues"], "material", line_number, row_error)
        return

    layer = _LAYER_RE.match(stripped)
    if layer is not None:
        _finish_primitive(scan)
        scan["active_shape"] = False
        raw_name = layer.group("name")
        name = raw_name.decode("utf-8", errors="replace") if len(raw_name) <= MAX_NAME_BYTES else None
        display_name = name if name is not None else "<oversized>"
        try:
            thickness = _length_um(layer.group("thickness"))
            if thickness <= 0:
                raise ValueError("thickness is not positive")
        except ValueError as exc:
            _add_issue(scan["issues"], "stackup", line_number, f"malformed layer {display_name!r}: {exc}")
            return
        if name is not None and name.casefold() in {item.casefold() for item in scan["stackup_layer_names"]}:
            _add_issue(scan["issues"], "stackup", line_number, f"duplicate stackup layer {name!r}")
        _name(scan, "stackup_layer_names", raw_name, "stackup", line_number)
        _record(scan, "stackup_layers", line_number, byte_offset)
        return
    if b"thickness" in lowered and not stripped.startswith((b"+", b".")):
        _add_issue(scan["issues"], "stackup", line_number, "layer Thickness record is malformed")


def _finalize_scan(scan: dict[str, Any], line_number: int) -> None:
    _finish_primitive(scan)
    if scan["active_model"] is not None:
        _add_issue(scan["issues"], "material", line_number, "material model is truncated before its end record")


def _empty_receipt(source: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "version": VERSION,
        "scope": SCOPE,
        "status": STOP_AMBIGUOUS if issues else STOP_NOT_REPRESENTED,
        "gate": STOP_AMBIGUOUS if issues else STOP_NOT_REPRESENTED,
        "xy_dielectric_partitions": STOP_AMBIGUOUS if issues else STOP_NOT_REPRESENTED,
        "conductor_layer_void_fill": STOP_AMBIGUOUS if issues else STOP_NOT_REPRESENTED,
        "source": source,
        "scan": {
            "line_count": 0,
            "max_line_bytes": MAX_LINE_BYTES,
            "bounded_memory": True,
            "read_only": True,
            "native_record_counts": {
                "shape_headers": 0,
                "supported_shape_primitives": 0,
                "unsupported_shape_primitives": 0,
                "dielectric_models": 0,
                "metal_models": 0,
                "material_table_rows": 0,
                "stackup_layers": 0,
            },
            "record_spans": {},
        },
        "bindings": {
            "xy_dielectric_partitions": {"status": STOP_AMBIGUOUS if issues else STOP_NOT_REPRESENTED, "explicit_source_records_found": False},
            "conductor_layer_void_fill": {"status": STOP_AMBIGUOUS if issues else STOP_NOT_REPRESENTED, "explicit_source_records_found": False},
        },
        "issues": issues[:MAX_ISSUES],
        "other_wp2_blockers": {
            "reference_points_and_panel_sides": STOP_UNSEALED,
            "outer_truncation_and_closure": STOP_UNSEALED,
            "absolute_source_z_transform": STOP_UNSEALED,
        },
        "wp2_overall_status": "PARTIAL",
        "promotion": "NONE",
        "numerical_execution": "STOP",
        "does_not_authorize": ["FasterCap", "Triangle", "solver", "PowerSI", "geometry/material inference"],
    }


def _escalate_receipt_ambiguous(receipt: dict[str, Any], issue: dict[str, Any] | None = None) -> None:
    """Make every in-scope receipt status agree that source identity is ambiguous."""

    for key in (
        "status",
        "gate",
        "xy_dielectric_partitions",
        "conductor_layer_void_fill",
    ):
        receipt[key] = STOP_AMBIGUOUS
    bindings = receipt.get("bindings")
    if isinstance(bindings, dict):
        for key in ("xy_dielectric_partitions", "conductor_layer_void_fill"):
            binding = bindings.get(key)
            if isinstance(binding, dict):
                binding["status"] = STOP_AMBIGUOUS
    if issue is not None:
        issues = receipt.get("issues")
        if isinstance(issues, list):
            _add_issue(issues, str(issue["kind"]), int(issue["line_number"]), str(issue["message"]))


class _PhysicalLineReader:
    """Split CR, LF, and CRLF records while hashing each source byte once."""

    def __init__(self, stream: BinaryIO, digest: Any) -> None:
        self._stream = stream
        self._digest = digest
        self._pending: bytes | None = None
        self._pending_offset = 0
        self._next_offset = 0

    @property
    def total_bytes(self) -> int:
        return self._next_offset

    def _pull(self) -> tuple[int, bytes] | None:
        if self._pending is not None:
            offset = self._pending_offset
            raw = self._pending
            self._pending = None
            return offset, raw
        raw = self._stream.readline(MAX_LINE_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError("source stream returned a non-bytes line")
        raw = bytes(raw)
        if not raw:
            return None
        offset = self._next_offset
        self._next_offset += len(raw)
        self._digest.update(raw)
        return offset, raw

    @staticmethod
    def _append_prefix(prefix: bytearray, piece: bytes) -> None:
        remaining = MAX_LINE_BYTES + 1 - len(prefix)
        if remaining > 0:
            prefix.extend(piece[:remaining])

    def readline(self) -> tuple[int, bytes, bool] | None:
        prefix = bytearray()
        line_bytes = 0
        start: int | None = None
        while True:
            item = self._pull()
            if item is None:
                if start is None:
                    return None
                return start, bytes(prefix), line_bytes > MAX_LINE_BYTES
            offset, chunk = item
            if start is None:
                start = offset
            carriage = chunk.find(b"\r")
            newline = chunk.find(b"\n")
            if carriage < 0 and newline < 0:
                self._append_prefix(prefix, chunk)
                line_bytes += len(chunk)
                continue
            if carriage >= 0 and (newline < 0 or carriage < newline):
                stop = carriage + 1
                if newline == stop:
                    stop += 1
                elif stop == len(chunk):
                    following = self._pull()
                    if following is not None:
                        following_offset, following_chunk = following
                        if following_chunk.startswith(b"\n"):
                            self._append_prefix(prefix, chunk[:stop])
                            self._append_prefix(prefix, b"\n")
                            line_bytes += stop + 1
                            remainder = following_chunk[1:]
                            if remainder:
                                self._pending = remainder
                                self._pending_offset = following_offset + 1
                            return start, bytes(prefix), line_bytes > MAX_LINE_BYTES
                        self._pending = following_chunk
                        self._pending_offset = following_offset
                segment = chunk[:stop]
            else:
                stop = newline + 1
                segment = chunk[:stop]
            self._append_prefix(prefix, segment)
            line_bytes += len(segment)
            remainder = chunk[stop:]
            if remainder:
                self._pending = remainder
                self._pending_offset = offset + stop
            return start, bytes(prefix), line_bytes > MAX_LINE_BYTES


def _scan_stream(stream: BinaryIO, source_path: str, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    scan = _new_scan()
    source_hash = hashlib.sha256()
    line_number = 0
    read_error: str | None = None
    reader = _PhysicalLineReader(stream, source_hash)
    while True:
        try:
            physical = reader.readline()
        except (OSError, ValueError) as exc:
            read_error = f"source read failed: {exc}"
            break
        if physical is None:
            break
        offset, raw, overlong = physical
        line_number += 1
        if overlong:
            # Do not retain a pathological source line; only candidate/native
            # records are ambiguous when their syntax cannot be bounded.
            prefix_kind = _prefix_kind(raw)
            if scan["active_shape"] or scan["active_model"] is not None or prefix_kind is not None:
                _add_issue(scan["issues"], "source", line_number, f"native record exceeds {MAX_LINE_BYTES} bytes")
            continue
        _consume_line(scan, raw.rstrip(b"\r\n"), line_number, offset)
    _finalize_scan(scan, line_number)
    if read_error is not None:
        _add_issue(scan["issues"], "source", line_number, read_error)
    source = {"path": source_path, "size_bytes": reader.total_bytes, "sha256": source_hash.hexdigest()}
    if expected is not None:
        try:
            if "path" in expected:
                expected_path = str(Path(str(expected["path"])).resolve())
                actual_path = source_path if source_path.startswith("<") else str(Path(source_path).resolve())
                if expected_path != actual_path:
                    _add_issue(scan["issues"], "source", 0, "source path differs from expected identity")
            if "size_bytes" in expected and type(expected["size_bytes"]) is not int:
                raise ValueError("expected size is not an integer")
            if "size_bytes" in expected and expected["size_bytes"] != reader.total_bytes:
                _add_issue(scan["issues"], "source", 0, "source size differs from expected identity")
            if "sha256" in expected and str(expected["sha256"]).lower() != source["sha256"]:
                _add_issue(scan["issues"], "source", 0, "source SHA-256 differs from expected identity")
        except (TypeError, ValueError) as exc:
            _add_issue(scan["issues"], "source", 0, f"malformed expected source identity: {exc}")
    issues = scan["issues"]
    issue_kinds = {item["kind"] for item in issues}
    ambiguous = bool(issues)
    xy_status = STOP_AMBIGUOUS if "shape" in issue_kinds or "source" in issue_kinds else STOP_NOT_REPRESENTED
    fill_status = STOP_AMBIGUOUS if issue_kinds & {"shape", "stackup", "material", "source"} else STOP_NOT_REPRESENTED
    return {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "version": VERSION,
        "scope": SCOPE,
        "status": STOP_AMBIGUOUS if ambiguous else STOP_NOT_REPRESENTED,
        "gate": STOP_AMBIGUOUS if ambiguous else STOP_NOT_REPRESENTED,
        "xy_dielectric_partitions": xy_status,
        "conductor_layer_void_fill": fill_status,
        "source": source,
        "scan": {
            "line_count": line_number,
            "max_line_bytes": MAX_LINE_BYTES,
            "bounded_memory": True,
            "read_only": True,
            "native_record_counts": scan["native_record_counts"],
            "record_spans": {key: scan["record_spans"][key] for key in sorted(scan["record_spans"])},
            "shape_layers": sorted(set(scan["shape_layers"]), key=lambda value: (value.casefold(), value)),
            "dielectric_model_names": sorted(set(scan["dielectric_model_names"]), key=lambda value: (value.casefold(), value)),
            "metal_model_names": sorted(set(scan["metal_model_names"]), key=lambda value: (value.casefold(), value)),
            "stackup_layer_names": sorted(set(scan["stackup_layer_names"]), key=lambda value: (value.casefold(), value)),
        },
        "bindings": {
            "xy_dielectric_partitions": {
                "status": xy_status,
                "explicit_source_records_found": False,
                "native_supporting_records": ["Shape conductor artwork"],
                "reason": "native SPD Shape records do not represent XY dielectric partitions or dielectric sidewalls",
            },
            "conductor_layer_void_fill": {
                "status": fill_status,
                "explicit_source_records_found": False,
                "native_supporting_records": ["Shape conductor artwork", "Thickness layer rows", "DielectricModel/MetalModel tables"],
                "reason": "native SPD stackup/material records do not explicitly bind conductor-layer void fill",
            },
        },
        "issues": issues[:MAX_ISSUES],
        "other_wp2_blockers": {
            "reference_points_and_panel_sides": STOP_UNSEALED,
            "outer_truncation_and_closure": STOP_UNSEALED,
            "absolute_source_z_transform": STOP_UNSEALED,
        },
        "wp2_overall_status": "PARTIAL",
        "promotion": "NONE",
        "numerical_execution": "STOP",
        "pass_unavailable_until": "source-native XY dielectric partition and conductor-layer void-fill records plus parser",
        "does_not_authorize": ["FasterCap", "Triangle", "solver", "PowerSI", "geometry/material inference"],
    }


def audit_stream(stream: BinaryIO, *, source_path: str = "<stream>", expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Census a binary SPD stream with bounded ``readline`` calls."""

    return _scan_stream(stream, source_path, expected)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (int(value.st_dev), int(value.st_ino), int(value.st_size), int(value.st_mtime_ns))


def audit_source(path: str | os.PathLike[str], *, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Hash and census one regular source file without modifying it."""

    source = Path(path)
    source_path = str(source.resolve())
    try:
        before = source.stat()
    except OSError as exc:
        return _empty_receipt({"path": source_path, "size_bytes": 0, "sha256": "0" * 64}, [{"kind": "source", "line_number": 0, "message": f"source stat failed: {exc}"}])
    if source.is_symlink() or not source.is_file():
        return _empty_receipt({"path": source_path, "size_bytes": int(before.st_size), "sha256": "0" * 64}, [{"kind": "source", "line_number": 0, "message": "source is not a regular non-symlink file"}])
    before_identity = _stat_identity(before)
    identity_messages: list[str] = []
    receipt: dict[str, Any] | None = None
    try:
        with source.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            descriptor_before_identity = _stat_identity(descriptor_before)
            if descriptor_before_identity != before_identity:
                identity_messages.append("opened descriptor differs from pre-scan path identity")
            receipt = _scan_stream(stream, source_path, expected)
            descriptor_after = os.fstat(stream.fileno())
            descriptor_after_identity = _stat_identity(descriptor_after)
            if descriptor_after_identity != descriptor_before_identity:
                identity_messages.append("opened descriptor changed while being scanned")
            path_after_open = source.stat()
            if descriptor_after_identity != _stat_identity(path_after_open):
                identity_messages.append("post-scan path differs from opened descriptor identity")
            if source.is_symlink() or not source.is_file():
                identity_messages.append("source is no longer a regular non-symlink file")
        after = source.stat()
    except OSError as exc:
        if receipt is None:
            return _empty_receipt({"path": source_path, "size_bytes": 0, "sha256": "0" * 64}, [{"kind": "source", "line_number": 0, "message": f"source read failed: {exc}"}])
        _escalate_receipt_ambiguous(
            receipt,
            {"kind": "source", "line_number": 0, "message": "source changed while being scanned"},
        )
        return receipt
    if _stat_identity(path_after_open) != _stat_identity(after):
        identity_messages.append("path changed after descriptor scan")
    assert receipt is not None
    if identity_messages:
        _escalate_receipt_ambiguous(
            receipt,
            {"kind": "source", "line_number": 0, "message": identity_messages[0]},
        )
        for message in identity_messages[1:]:
            _add_issue(receipt["issues"], "source", 0, message)
    return receipt


def write_receipt(path: str | os.PathLike[str], receipt: Mapping[str, Any]) -> None:
    output = Path(path)
    if not output.parent.is_dir() or os.path.lexists(output):
        raise Refusal(STOP_OUTPUT_EXISTS, "output path must be absent in an existing directory")
    data = _canonical(dict(receipt))
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(output):
            raise Refusal(STOP_OUTPUT_EXISTS, "output path appeared during write")
        os.link(temporary, output)
        temporary.unlink()
        temporary = None
    except FileExistsError as exc:
        raise Refusal(STOP_OUTPUT_EXISTS, "output path appeared during write") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _native_fixture() -> bytes:
    return (
        b".Shape Signal$L28(DGND)\n"
        b"Polygon0::DGND+ 0um 0um 10um 0um 10um 10um 0um 10um\n"
        b".Shape Signal$L29(DGND)\n"
        b"Box0::DGND+ 0um 0um 10um 10um\n"
        b".DielectricModel EL190T\n"
        b"* Frequency (Hz)\n"
        b"1000000 4.7 0.01\n"
        b".EndDielectricModel\n"
        b".MetalModel COPPER\n"
        b"1000000 59590000\n"
        b".EndMetalModel\n"
        b"Medium$DR2829 Thickness = 30um Material = EL190T\n"
        b"L28 Thickness = 20um Material = COPPER\n"
    )


def _self_check() -> dict[str, Any]:
    receipt = audit_stream(io.BytesIO(_native_fixture()), source_path="<synthetic-native>")
    assert receipt["status"] == STOP_NOT_REPRESENTED
    assert receipt["bindings"]["xy_dielectric_partitions"]["status"] == STOP_NOT_REPRESENTED
    assert receipt["bindings"]["conductor_layer_void_fill"]["status"] == STOP_NOT_REPRESENTED
    assert receipt["scan"]["native_record_counts"]["supported_shape_primitives"] == 2
    assert audit_stream(io.BytesIO(_native_fixture()), source_path="<synthetic-native>") == receipt
    return {"status": STOP_NOT_REPRESENTED, "output_written": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} {SCOPE}")
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    parser.add_argument("--self-check", action="store_true", help="run synthetic native-SPD checks")
    parser.add_argument("--input", "--source", dest="source", type=Path)
    parser.add_argument("--output", "--receipt", dest="output", type=Path)
    parser.add_argument("--expected-size", type=int)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args(argv)
    try:
        if args.self_check:
            if args.source is not None or args.output is not None:
                parser.error("--self-check cannot be combined with --input/--output")
            result = _self_check()
            print(f"{PRODUCT} v{VERSION} {result['status']} self-check PASS")
            return 0
        if args.source is None:
            parser.error("normal mode requires --input PATH")
        expected: dict[str, Any] = {}
        if args.expected_size is not None:
            expected["size_bytes"] = args.expected_size
        if args.expected_sha256 is not None:
            expected["sha256"] = args.expected_sha256
        receipt = audit_source(args.source, expected=expected or None)
        if args.output is None:
            print(_canonical(receipt).decode("ascii"), end="")
        else:
            write_receipt(args.output, receipt)
            print(f"{PRODUCT} v{VERSION} {receipt['status']} wrote {args.output}")
        return 0
    except Refusal as exc:
        print(f"{PRODUCT} v{VERSION} REFUSAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PRODUCT",
    "VERSION",
    "SCHEMA_VERSION",
    "SCOPE",
    "PASS",
    "STOP_NOT_REPRESENTED",
    "STOP_AMBIGUOUS",
    "STOP_UNSEALED",
    "STOP_INPUT_IDENTITY_MISMATCH",
    "STOP_OUTPUT_EXISTS",
    "MAX_LINE_BYTES",
    "MAX_NAME_BYTES",
    "MAX_NAME_BYTES_TOTAL",
    "Refusal",
    "_escalate_receipt_ambiguous",
    "audit_stream",
    "audit_source",
    "write_receipt",
    "_canonical",
    "_native_fixture",
    "_self_check",
    "main",
]
