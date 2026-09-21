"""Fail-closed D115C source-local Port44/L29-L30 evidence gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from spd_decap_pi._core.io.spd import _VIA_RE as CORE_VIA_RE
    from spd_decap_pi.scenario_io import load_scenario_bundle
    from spd_decap_pi.source_plane_ownership_ir import (
        load_source_plane_ownership_ir,
        validate_project_source_plane_ownership_ir_envelope,
    )
except ImportError:  # direct script execution from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from spd_decap_pi._core.io.spd import _VIA_RE as CORE_VIA_RE
    from spd_decap_pi.scenario_io import load_scenario_bundle
    from spd_decap_pi.source_plane_ownership_ir import (
        load_source_plane_ownership_ir,
        validate_project_source_plane_ownership_ir_envelope,
    )


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
EXPECTED_RAIL = "ADC_VDD_180_VQPS_SYS_1_AON/0"
EXPECTED_HEAD = "e2f219e71d8c8a397009f72242cce10d78cfc7ab"
EXPECTED_PORT = "Port44_SITE0::ADC_VDD_180_VQPS_SYS_1_AON/0"
EXPECTED_RAW_PAD_SHA = "e4260e67261473b0387dbc855cf7dcda187c4c3108ae4a15bb7a6f4d8d24a291"
EXPECTED_PADSTACK = "DR-0102_60"
GRID_PM = 1_000_000_000
PM_PER_UM = 1_000_000
PM_PER_MM = 1_000_000_000


class AuditStop(RuntimeError):
    """A deterministic fail-closed audit stop code."""

    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _stop(code: str) -> None:
    raise AuditStop(code)


def _tick(deadline: float) -> None:
    if time.monotonic() > deadline:
        _stop("STOP_DEADLINE")


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        _stop("STOP_DEADLINE")
    return value


def _path(value: Path | str, code: str) -> Path:
    if not isinstance(value, (Path, str)):
        _stop(code)
    return Path(value)


def _sha_file(path: Path, deadline: float) -> tuple[int, str]:
    if not path.is_file():
        _stop("STOP_INPUT_MISSING")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while True:
                _tick(deadline)
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        _stop("STOP_INPUT_IDENTITY")
    return size, digest.hexdigest()


def _hash_gate(path: Path, expected: str, deadline: float, code: str) -> tuple[int, str]:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        _stop(code)
    size, actual = _sha_file(path, deadline)
    if actual.casefold() != expected.casefold():
        _stop(code)
    return size, actual


def _read_bytes(path: Path, deadline: float, code: str) -> bytes:
    if not path.is_file():
        _stop(code)
    chunks: list[bytes] = []
    try:
        with path.open("rb") as stream:
            while True:
                _tick(deadline)
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError:
        _stop(code)
    return b"".join(chunks)


def _read_json(path: Path, deadline: float, code: str) -> dict[str, Any]:
    try:
        payload = _read_bytes(path, deadline, code)
        _tick(deadline)
        value = json.loads(payload.decode("utf-8"))
        _tick(deadline)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _stop(code)
    if not isinstance(value, dict):
        _stop(code)
    return value


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _stop("STOP_INTERNAL_CANONICAL")


def _same_path(left: Path, right: Path) -> bool:
    try:
        return str(left.resolve(strict=False)).casefold() == str(right.resolve(strict=False)).casefold()
    except OSError:
        return os.path.normcase(str(left.absolute())) == os.path.normcase(str(right.absolute()))


def _text(row: Mapping[str, Any], key: str, code: str = "STOP_IR_INCOMPLETE") -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        _stop(code)
    return value


def _framed_sha256(values: Iterable[Any]) -> str:
    """Length-prefixed UTF-8 case-fold frames (4-byte big-endian lengths)."""

    digest = hashlib.sha256()
    for value in values:
        encoded = str(value).casefold().encode("utf-8")
        digest.update(struct.pack(">I", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


_framed_hash = _framed_sha256


def _git_identity(repo_root: Path, expected_head: str, deadline: float) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", expected_head or "") or not repo_root.is_dir():
        _stop("STOP_REPO_IDENTITY")

    def run(args: list[str]) -> str:
        _tick(deadline)
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=_remaining(deadline),
            )
        except subprocess.TimeoutExpired:
            _stop("STOP_DEADLINE")
        except (OSError, subprocess.SubprocessError):
            _stop("STOP_REPO_IDENTITY")
        return completed.stdout.strip()

    head = run(["rev-parse", "HEAD"])
    branch = run(["branch", "--show-current"])
    dirty = run(["status", "--porcelain", "--untracked-files=no"])
    if head.casefold() != expected_head.casefold() or branch != "main" or dirty:
        _stop("STOP_REPO_IDENTITY")
    return {"root": str(repo_root), "head": head, "branch": branch, "tracked_clean": True}


def _validate_d115b(
    receipt: Mapping[str, Any],
    *,
    d115b_path: Path,
    source_path: Path,
    source_size: int,
    source_sha: str,
    candidate_path: Path,
    candidate_size: int,
    candidate_sha: str,
    expected_head: str,
) -> dict[str, Any]:
    if (
        receipt.get("product") != PRODUCT
        or receipt.get("version") != VERSION
        or receipt.get("schema_version") != "d115b-source-plane-ownership-materialization-receipt-v1"
        or receipt.get("status") != "PASS"
        or receipt.get("disposition") != "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED"
        or str(receipt.get("contract_head", "")).casefold() != expected_head.casefold()
        or receipt.get("rail_id") != EXPECTED_RAIL
    ):
        _stop("STOP_D115B_SCHEMA")
    source = receipt.get("source")
    candidate = receipt.get("candidate")
    if not isinstance(source, Mapping) or not isinstance(candidate, Mapping):
        _stop("STOP_D115B_SCHEMA")
    if (
        not _same_path(Path(str(source.get("path", ""))), source_path)
        or source.get("size_bytes") != source_size
        or str(source.get("sha256", "")).casefold() != source_sha.casefold()
        or not _same_path(Path(str(receipt.get("source_path", source.get("path", "")))), source_path)
        or receipt.get("source_size_bytes") != source_size
    ):
        _stop("STOP_D115B_SOURCE_IDENTITY")
    if (
        candidate.get("present") is not True
        or not _same_path(Path(str(candidate.get("path", ""))), candidate_path)
        or candidate.get("size_bytes") != candidate_size
        or str(candidate.get("sha256", "")).casefold() != candidate_sha.casefold()
        or candidate.get("rail_count") != 2
        or candidate.get("terminal_count") != 6
        or not _same_path(d115b_path.parent, candidate_path.parent)
    ):
        _stop("STOP_D115B_CANDIDATE_IDENTITY")
    return dict(receipt)


def _validate_d103(
    d103: Mapping[str, Any],
    *,
    source_path: Path,
    source_size: int,
    source_sha: str,
) -> dict[str, Any]:
    if d103.get("status") != "PASS" or d103.get("version") != VERSION:
        _stop("STOP_D103_SCHEMA")
    source = d103.get("source")
    if not isinstance(source, Mapping) or not _same_path(Path(str(source.get("path", ""))), source_path) or source.get("size_bytes") != source_size or str(source.get("sha256", "")).casefold() != source_sha.casefold():
        _stop("STOP_D103_SOURCE_IDENTITY")
    layers = d103.get("stackup_layers")
    if not isinstance(layers, list):
        _stop("STOP_D103_STACKUP")
    by_ordinal: dict[int, Mapping[str, Any]] = {}
    for item in layers:
        if isinstance(item, Mapping) and type(item.get("ordinal")) is int:
            if item["ordinal"] in by_ordinal:
                _stop("STOP_D103_STACKUP")
            by_ordinal[item["ordinal"]] = item
    expected = {
        56: ("Signal$L29(DGND)", "conductor", Decimal("20"), Decimal("1947"), Decimal("1967")),
        57: ("Medium$DR2930", "dielectric", Decimal("30"), Decimal("1967"), Decimal("1997")),
        58: ("Signal$L30(OTHER_POWER1)", "conductor", Decimal("20"), Decimal("1997"), Decimal("2017")),
    }
    selected: list[dict[str, Any]] = []
    for ordinal, (name, kind, thickness, top, bottom) in expected.items():
        row = by_ordinal.get(ordinal)
        if row is None:
            _stop("STOP_D103_STACKUP")
        depth = row.get("depth_from_stack_top_um")
        try:
            observed = (
                row.get("layer_name"),
                row.get("layer_kind"),
                Decimal(str(row.get("thickness_um"))),
                Decimal(str(depth["top"])),
                Decimal(str(depth["bottom"])),
            )
        except (InvalidOperation, TypeError, KeyError):
            _stop("STOP_D103_STACKUP")
        if observed != (name, kind, thickness, top, bottom):
            _stop("STOP_D103_STACKUP")
        if row.get("raw_layer_ordinal", ordinal) != ordinal:
            _stop("STOP_D103_STACKUP")
        selected.append(dict(row))
    points = d103.get("dielectric_points")
    if not isinstance(points, list):
        _stop("STOP_D103_MATERIAL")
    material_points: list[Mapping[str, Any]] = []
    for point in points:
        if not isinstance(point, Mapping) or point.get("layer_name") != "Medium$DR2930":
            continue
        try:
            frequency = Decimal(str(point.get("frequency_hz")))
        except InvalidOperation:
            continue
        if frequency == Decimal("1000000"):
            material_points.append(point)
    if len(material_points) != 1:
        _stop("STOP_D103_MATERIAL")
    point = material_points[0]
    layer = by_ordinal[57]
    try:
        epsilon = Decimal(str(point.get("epsilon_r")))
    except InvalidOperation:
        _stop("STOP_D103_MATERIAL")
    if (
        epsilon != Decimal("3.4")
        or point.get("epsilon_origin") != "material_model"
        or point.get("epsilon_source_record_id") != "material:ABF-GL102"
        or point.get("frequency_origin") != "material_model"
        or point.get("frequency_source_record_id") != "material:ABF-GL102"
        or layer.get("material_name") != "ABF-GL102"
        or layer.get("material_origin") != "material_model"
        or layer.get("material_source_record_id") != "material:ABF-GL102"
    ):
        _stop("STOP_D103_MATERIAL")
    gap_pm = int(Decimal(str(layer["thickness_um"])) * PM_PER_UM)
    if gap_pm <= 0:
        _stop("STOP_D103_STACKUP")
    return {"status": d103.get("status"), "version": d103.get("version"), "layers": selected, "dielectric_point": dict(point), "gap_pm": gap_pm, "gap_um": gap_pm // PM_PER_UM}


TOKEN_RE = re.compile(r"(?<!\S)(?:\$Package\.)?Node[0-9]+!![0-9]+::[^\s]+", re.I)
PORT_HEADER_RE = re.compile(r"^\s*(Port[^\s]+)\s+", re.I)
MARKER_RE = re.compile(r"\b(PositiveTerminal|NegativeTerminal)\b", re.I)


def _token_from_row(row: Mapping[str, Any]) -> str:
    node = _text(row, "source_node_record_id")
    match = re.fullmatch(r"node:(Node[0-9]+):(.+)", node)
    if not match:
        _stop("STOP_IR_BINDING_MISMATCH")
    pin = _text(row, "pin_id").rsplit(":", 1)[-1]
    if not pin.isdigit():
        _stop("STOP_IR_BINDING_MISMATCH")
    return f"$Package.{match.group(1)}!!{pin}::{match.group(2)}"


def _port(
    source: Path,
    port_id: str,
    positive_count: int,
    negative_count: int,
    expected_positive: set[str],
    expected_negative: set[str],
    deadline: float,
) -> dict[str, Any]:
    if type(positive_count) is not int or type(negative_count) is not int or positive_count < 0 or negative_count < 0:
        _stop("STOP_PORT_TERMINAL_COUNT_MISMATCH")
    positives: list[str] = []
    negatives: list[str] = []
    active: str | None = None
    section_hash = hashlib.sha256()
    found = False
    header = ""
    header_raw = b""
    header_line = 0
    header_byte_start = 0
    header_byte_end = 0
    section_byte_end = 0
    section_line_end = 0
    section_terminator = ""
    try:
        with source.open("rb") as stream:
            line_number = 0
            while True:
                _tick(deadline)
                line_start = stream.tell()
                raw = stream.readline()
                if not raw:
                    break
                line_number += 1
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    _stop("STOP_SOURCE_IDENTITY")
                match = PORT_HEADER_RE.match(line)
                if match:
                    if found:
                        # Port blocks in production sources are adjacent; the
                        # next header terminates this target block and is not
                        # part of its byte/line/hash evidence.
                        section_byte_end = line_start
                        section_line_end = line_number - 1
                        section_terminator = "next_port_header"
                        break
                    if match.group(1).casefold() == port_id.casefold():
                        found = True
                        header = line.rstrip("\r\n")
                        header_raw = raw
                        header_line = line_number
                        header_byte_start = line_start
                        header_byte_end = line_start + len(raw)
                        section_hash.update(raw)
                    continue
                if not found:
                    continue
                if re.match(r"^\s*\.EndPort\b", line, re.I):
                    section_hash.update(raw)
                    section_byte_end = stream.tell()
                    section_line_end = line_number
                    section_terminator = "end_port"
                    break
                if re.match(r"^\s*\.", line):
                    _stop("STOP_PORT_FRAME")
                section_hash.update(raw)
                markers = list(MARKER_RE.finditer(line))
                if markers:
                    for index, marker in enumerate(markers):
                        role = "positive" if marker.group(1).casefold().startswith("positive") else "negative"
                        stop_at = markers[index + 1].start() if index + 1 < len(markers) else len(line)
                        values = [m.group(0) for m in TOKEN_RE.finditer(line[marker.end() : stop_at])]
                        values = [value if value.casefold().startswith("$package.") else "$Package." + value for value in values]
                        (positives if role == "positive" else negatives).extend(values)
                    active = "positive" if markers[-1].group(1).casefold().startswith("positive") else "negative"
                elif active:
                    values = [m.group(0) for m in TOKEN_RE.finditer(line)]
                    values = [value if value.casefold().startswith("$package.") else "$Package." + value for value in values]
                    (positives if active == "positive" else negatives).extend(values)
    except OSError:
        _stop("STOP_SOURCE_IDENTITY")
    if not found or not section_line_end:
        _stop("STOP_PORT_FRAME")
    if len(positives) != positive_count or len(negatives) != negative_count:
        _stop("STOP_PORT_TERMINAL_COUNT_MISMATCH")
    folded_positive = [value.casefold() for value in positives]
    folded_negative = [value.casefold() for value in negatives]
    if len(set(folded_positive)) != len(positives) or len(set(folded_negative)) != len(negatives):
        _stop("STOP_PORT_TERMINAL_DUPLICATE")
    if set(folded_positive) & set(folded_negative):
        _stop("STOP_PORT_ROLE_OVERLAP")
    if set(folded_positive) != {value.casefold() for value in expected_positive}:
        _stop("STOP_PORT_COMPILED_IDENTITY_UNPROVEN")
    if not {value.casefold() for value in expected_negative} <= set(folded_negative):
        _stop("STOP_TERMINAL_MISMATCH")
    return {
        "port_id": port_id,
        "header": header,
        "header_line": header_line,
        "header_line_start": header_line,
        "header_line_end": header_line,
        "header_byte_start": header_byte_start,
        "header_byte_end": header_byte_end,
        "header_sha256": hashlib.sha256(header_raw).hexdigest(),
        "section_line_start": header_line,
        "section_line_end": section_line_end,
        "section_line_count": section_line_end - header_line + 1,
        "section_byte_start": header_byte_start,
        "section_byte_end": section_byte_end,
        "section_byte_bounds": [header_byte_start, section_byte_end],
        "section_sha256": section_hash.hexdigest(),
        "section_terminator": section_terminator,
        "positive_terminals": positives,
        "positive_count": len(positives),
        "positive_sequence_sha256": _framed_sha256(positives),
        "positive_set_sha256": _framed_sha256(sorted(positives, key=str.casefold)),
        "negative_count": len(negatives),
        "negative_sequence_sha256": _framed_sha256(negatives),
        "negative_set_sha256": _framed_sha256(sorted(negatives, key=str.casefold)),
        "selected_negative_terminals": sorted(expected_negative, key=str.casefold),
        "selected_negative_count": len(expected_negative),
    }


def _rows(ir: Any, section: str, deadline: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        iterator = ir.iter_section(section)
        for row in iterator:
            _tick(deadline)
            if not isinstance(row, Mapping):
                _stop("STOP_IR_INCOMPLETE")
            result.append(dict(row))
    except AuditStop:
        raise
    except Exception:
        _stop("STOP_IR_INVALID")
    return result


def _canonical_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda row: (row.get("ordinal", 0), _canonical(row)))


def _validate_candidate(
    loaded: Any,
    manifest: Mapping[str, Any],
    d115b: Mapping[str, Any],
    *,
    source_sha: str,
    source_size: int,
    deadline: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Mapping[str, Any]], dict[str, Any]]:
    if (
        str(manifest.get("source_sha256", "")).casefold() != source_sha.casefold()
        or manifest.get("source_size_bytes") != source_size
        or manifest.get("target_rail_id") != EXPECTED_RAIL
        or manifest.get("app_version") != VERSION
    ):
        _stop("STOP_IR_BINDING_MISMATCH")
    rails = _rows(loaded, "rail_bindings", deadline)
    terminals = _rows(loaded, "terminal_bindings", deadline)
    candidate = d115b.get("candidate")
    if not isinstance(candidate, Mapping) or _canonical_rows(rails) != _canonical_rows(candidate.get("rail_rows", [])) or _canonical_rows(terminals) != _canonical_rows(candidate.get("terminal_rows", [])):
        _stop("STOP_D115B_CANDIDATE_BINDING")
    if len(rails) != 2 or len(terminals) != 6:
        _stop("STOP_IR_CARDINALITY")
    rail_expected = {
        "power": (EXPECTED_RAIL, EXPECTED_RAIL, "Signal$L30(OTHER_POWER1)", "spd-surface-island:cb8510a79529b7f6f4f4afd4"),
        "ground": ("DGND", "DGND", "Signal$L29(DGND)", "spd-surface-island:2db099ba622781734a17c3e0"),
    }
    seen_roles: set[str] = set()
    for row in rails:
        role = _text(row, "role").casefold()
        if role in seen_roles or role not in rail_expected:
            _stop("STOP_IR_BINDING_MISMATCH")
        seen_roles.add(role)
        logical, artwork, layer, island = rail_expected[role]
        if tuple(row.get(key) for key in ("rail_id", "logical_net", "artwork_net", "layer", "island_id", "state")) != (EXPECTED_RAIL, logical, artwork, layer, island, "source_bound"):
            _stop("STOP_IR_BINDING_MISMATCH")
    if seen_roles != set(rail_expected):
        _stop("STOP_IR_BINDING_MISMATCH")
    power_pins = {"SITE0:20122", "SITE0:20576", "SITE0:20589"}
    ground_pins = {"SITE0:19958", "SITE0:20612", "SITE0:19973"}
    powers = [row for row in terminals if str(row.get("role", "")).casefold() == "power"]
    grounds = [row for row in terminals if str(row.get("role", "")).casefold() == "ground"]
    if {row.get("pin_id") for row in powers} != power_pins or {row.get("pin_id") for row in grounds} != ground_pins:
        _stop("STOP_IR_BINDING_MISMATCH")
    if any(not isinstance(row.get(key), str) for row in terminals for key in ("terminal_id", "via_record_id", "finite_edge_id")) or any(len({str(row.get(key)).casefold() for row in terminals}) != 6 for key in ("terminal_id", "via_record_id", "finite_edge_id")):
        _stop("STOP_IR_BINDING_MISMATCH")
    expected_vertex = {"power": "spd-finite-via-vertex:aef72c164348031705f8bec6", "ground": "spd-finite-via-vertex:a0993f5ab9a6ce414dbfe97c"}
    expected_layer_island = {"power": ("Signal$L30(OTHER_POWER1)", "spd-surface-island:cb8510a79529b7f6f4f4afd4"), "ground": ("Signal$L29(DGND)", "spd-surface-island:2db099ba622781734a17c3e0")}
    required = ("terminal_id", "owner_kind", "rail_id", "branch_id", "pin_id", "role", "source_node_record_id", "via_record_id", "endpoint_node_id", "island_id", "component_id", "layer", "padstack_id", "paddef_source_record_id", "regular_source_record_id", "finite_vertex_id", "finite_edge_id", "via_owner_id")
    for row in terminals:
        role = str(row.get("role", "")).casefold()
        if role not in expected_vertex or any(not isinstance(row.get(key), str) or not row[key] for key in required):
            _stop("STOP_IR_INCOMPLETE")
        if (
            row.get("rail_id") != EXPECTED_RAIL
            or row.get("status") != "complete"
            or row.get("issues_json") != "[]"
            or row.get("via_record_required") != 1
            or row.get("padstack_id") != EXPECTED_PADSTACK
            or row.get("raw_pad_shape_ordinal") != 12
            or str(row.get("raw_pad_shape_sha256", "")).casefold() != EXPECTED_RAW_PAD_SHA
            or row.get("finite_vertex_id") != expected_vertex[role]
            or (row.get("layer"), row.get("island_id")) != expected_layer_island[role]
        ):
            _stop("STOP_IR_BINDING_MISMATCH")
        try:
            if json.loads(str(row["issues_json"])) != []:
                _stop("STOP_IR_INCOMPLETE")
        except (TypeError, ValueError):
            _stop("STOP_IR_INCOMPLETE")
    branches = {row["branch_id"] for row in terminals}
    if len(branches) != 3 or any(len([row for row in terminals if row["branch_id"] == branch]) != 2 or {row["role"] for row in terminals if row["branch_id"] == branch} != {"power", "ground"} for branch in branches):
        _stop("STOP_IR_BINDING_MISMATCH")
    needed_ids = {row[key] for row in terminals for key in ("source_node_record_id", "via_record_id", "paddef_source_record_id", "regular_source_record_id")}
    records: dict[str, Mapping[str, Any]] = {}
    try:
        for row in loaded.iter_section("source_records"):
            _tick(deadline)
            if isinstance(row, Mapping) and row.get("record_id") in needed_ids:
                records[str(row["record_id"])] = dict(row)
                if len(records) == len(needed_ids):
                    break
    except Exception:
        _stop("STOP_IR_INVALID")
    if set(records) != needed_ids:
        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISSING")
    return _canonical_rows(rails), _canonical_rows(terminals), records, dict(manifest)


def _source_slice(stream: Any, record: Mapping[str, Any], deadline: float, missing_code: str, mismatch_code: str) -> bytes:
    try:
        start, end = record["source_offset"], record["source_end"]
        expected = record["source_record_sha256"]
        if type(start) is not int or type(end) is not int or start < 0 or end <= start or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            _stop(mismatch_code)
        _tick(deadline)
        stream.seek(start)
        raw = stream.read(end - start)
    except (KeyError, OSError):
        _stop(missing_code)
    if len(raw) != end - start or hashlib.sha256(raw).hexdigest().casefold() != expected.casefold():
        _stop(mismatch_code)
    return raw


def _node_record_parts(identifier: str, code: str) -> tuple[str, str]:
    match = re.fullmatch(r"node:(Node[^:]+):(.+)", identifier)
    if match is None:
        _stop(code)
    return match.group(1), match.group(2)


def _selected_via_source_authority(
    terminals: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    loaded: Any,
    source: Path,
    deadline: float,
) -> dict[str, Any]:
    if len(terminals) != 6:
        _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
    ordered = sorted(terminals, key=lambda row: (row.get("ordinal", 0), str(row.get("terminal_id", "")), str(row.get("pin_id", ""))))
    via_ids = [_text(row, "via_record_id", "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING") for row in ordered]
    if len(set(identifier.casefold() for identifier in via_ids)) != 6:
        _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
    by_id = {str(identifier).casefold(): record for identifier, record in records.items()}
    parsed: list[dict[str, Any]] = []
    endpoint_ids: set[str] = set()
    try:
        with source.open("rb") as stream:
            for row, via_record_id in zip(ordered, via_ids):
                record = by_id.get(via_record_id.casefold())
                if not isinstance(record, Mapping):
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
                if str(record.get("record_id", "")).casefold() != via_record_id.casefold() or str(record.get("kind", "")).casefold() != "via":
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                raw = _source_slice(stream, record, deadline, "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING", "STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                primary = raw.splitlines(keepends=True)[0].rstrip(b"\r\n") if raw.splitlines(keepends=True) else b""
                match = CORE_VIA_RE.fullmatch(primary)
                if match is None:
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                try:
                    via_id, net, upper_id, lower_id, padstack = (value.decode("utf-8") for value in match.groups()[:5])
                except UnicodeDecodeError:
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                if upper_id.casefold() == lower_id.casefold():
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                expected_record_id = f"via:{via_id}:{net}"
                if expected_record_id.casefold() != via_record_id.casefold() or str(record.get("logical_net", "")).casefold() != net.casefold():
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                source_record_id = _text(row, "source_node_record_id", "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
                source_node_id, source_net = _node_record_parts(source_record_id, "STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                endpoint_node_id = _text(row, "endpoint_node_id", "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
                if endpoint_node_id.casefold() != source_node_id.casefold() or source_net.casefold() != net.casefold() or source_node_id.casefold() not in {upper_id.casefold(), lower_id.casefold()}:
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                if str(row.get("padstack_id", "")).casefold() != padstack.casefold():
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                other_node_id = lower_id if source_node_id.casefold() == upper_id.casefold() else upper_id
                source_record_id = f"node:{source_node_id}:{net}"
                other_record_id = f"node:{other_node_id}:{net}"
                selected_layer = _text(row, "layer", "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
                expected_layer = {"power": "Signal$L30(OTHER_POWER1)", "ground": "Signal$L29(DGND)"}.get(str(row.get("role", "")).casefold())
                if expected_layer is None or selected_layer.casefold() != expected_layer.casefold():
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                endpoint_ids.update((source_record_id.casefold(), other_record_id.casefold()))
                parsed.append({"row": row, "record": record, "source_offset": record["source_offset"], "source_end": record["source_end"], "source_record_sha256": record["source_record_sha256"], "net": net, "upper_node_id": upper_id, "lower_node_id": lower_id, "upper_record_id": f"node:{upper_id}:{net}", "lower_record_id": f"node:{lower_id}:{net}", "source_record_id": source_record_id, "other_record_id": other_record_id, "source_node_id": source_node_id, "other_node_id": other_node_id, "selected_plane_layer": selected_layer, "padstack_id": padstack})
    except AuditStop:
        raise
    except OSError:
        _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
    endpoint_records = dict(by_id)
    missing_endpoint_ids = endpoint_ids - set(endpoint_records)
    if missing_endpoint_ids:
        try:
            for source_record in loaded.iter_section("source_records"):
                _tick(deadline)
                if isinstance(source_record, Mapping) and str(source_record.get("record_id", "")).casefold() in missing_endpoint_ids:
                    endpoint_records[str(source_record["record_id"]).casefold()] = dict(source_record)
        except AuditStop:
            raise
        except Exception:
            _stop("STOP_IR_INVALID")
    if endpoint_ids - set(endpoint_records):
        _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
    node_info: dict[str, dict[str, Any]] = {}
    try:
        with source.open("rb") as stream:
            for record_id in sorted(endpoint_ids):
                record = endpoint_records.get(record_id)
                if not isinstance(record, Mapping) or str(record.get("kind", "")).casefold() != "node":
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                _source_slice(stream, record, deadline, "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING", "STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                expected_node, expected_net = _node_record_parts(str(record.get("record_id", "")), "STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                layer = record.get("layer")
                if not isinstance(layer, str) or not layer.strip() or str(record.get("logical_net", "")).casefold() != expected_net.casefold() or str(record.get("record_id", "")).casefold() != record_id:
                    _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
                node_info[record_id] = {"record_id": record["record_id"], "source_offset": record["source_offset"], "source_end": record["source_end"], "source_record_sha256": record["source_record_sha256"], "layer": layer}
    except AuditStop:
        raise
    except OSError:
        _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
    rows: list[dict[str, Any]] = []
    for item in parsed:
        upper_source, lower_source = node_info[item["upper_record_id"].casefold()], node_info[item["lower_record_id"].casefold()]
        source_layer = node_info[item["source_record_id"].casefold()]["layer"]
        opposite_layer = node_info[item["other_record_id"].casefold()]["layer"]
        if source_layer.casefold() != "signal$top" or opposite_layer != "Signal$L02(DGND)" or item["selected_plane_layer"].casefold() in {source_layer.casefold(), opposite_layer.casefold()}:
            _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH")
        rows.append({"terminal_id": _text(item["row"], "terminal_id", "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING"), "pin_id": _text(item["row"], "pin_id", "STOP_W0_VIA_SOURCE_EVIDENCE_MISSING"), "via_record_id": item["row"]["via_record_id"], "source_offset": item["source_offset"], "source_end": item["source_end"], "source_record_sha256": item["source_record_sha256"], "net": item["net"], "selected_plane_layer": item["selected_plane_layer"], "source_endpoint_node_id": item["source_node_id"], "source_endpoint_layer": source_layer, "opposite_endpoint_node_id": item["other_node_id"], "opposite_endpoint_layer": opposite_layer, "upper_node_id": item["upper_node_id"], "upper_layer": upper_source["layer"], "lower_node_id": item["lower_node_id"], "lower_layer": lower_source["layer"], "padstack_id": item["padstack_id"]})
    if len(rows) != 6:
        _stop("STOP_W0_VIA_SOURCE_EVIDENCE_MISSING")
    return {"status": "PARTIAL", "row_count": 6, "target_layer_traversal_proven": False, "scope": {"barrel_proven": False, "antipad_proven": False, "land_proven": False, "intermediate_access_proven": False, "l29_l30_physical_pad_proven": False, "three_dimensional_geometry_proven": False}, "rows": rows}


def _decimal_pm(value: str, unit: Decimal) -> int:
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
    if not decimal.is_finite() or (decimal * unit) != (decimal * unit).to_integral_value():
        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
    return int(decimal * unit)


NODE_RE = re.compile(r"\b(Node(?P<node>[0-9]+)!!(?P<pin>[0-9]+)::(?P<net>[^\s]+)\s+X\s*=\s*(?P<x>[+\-0-9.eE]+)mm\s+Y\s*=\s*(?P<y>[+\-0-9.eE]+)mm)", re.I)
REGULAR_RE = re.compile(r"^\s*Regular\s+Circle\s+(?P<value>3\.000000e-02)mm\s*$", re.I | re.M)
PADDEF_RE = re.compile(r"^\s*\.PadDef\s+Signal\$TOP\s*$", re.I | re.M)
def _derive_w0(
    terminals: list[dict[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    source: Path,
    asserted: Sequence[int],
    deadline: float,
    gap_pm: int | None = None,
) -> dict[str, Any]:
    if len(terminals) != 6 or len(asserted) != 4 or any(type(value) is not int for value in asserted):
        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISSING")
    boxes: list[list[int]] = []
    evidence: list[dict[str, Any]] = []
    first_radius: int | None = None
    try:
        with source.open("rb") as stream:
            for row in terminals:
                ids = tuple(_text(row, key, "STOP_W0_FOOTPRINT_EVIDENCE_MISSING") for key in ("source_node_record_id", "paddef_source_record_id", "regular_source_record_id"))
                if any(identifier not in records for identifier in ids):
                    _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISSING")
                chunks: list[bytes] = []
                refs: list[dict[str, Any]] = []
                for identifier in ids:
                    rec = records[identifier]
                    try:
                        start, end = rec["source_offset"], rec["source_end"]
                        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                            _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
                        _tick(deadline)
                        stream.seek(start)
                        raw = stream.read(end - start)
                    except (KeyError, OSError):
                        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISSING")
                    if len(raw) != end - start or hashlib.sha256(raw).hexdigest().casefold() != str(rec.get("source_record_sha256", "")).casefold():
                        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
                    try:
                        raw.decode("utf-8", errors="strict")
                    except UnicodeDecodeError:
                        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
                    chunks.append(raw)
                    refs.append({"record_id": identifier, "source_offset": start, "source_end": end, "source_record_sha256": rec.get("source_record_sha256")})
                try:
                    node_text, paddef_text, regular_text = (chunk.decode("utf-8") for chunk in chunks)
                except UnicodeDecodeError:
                    _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
                node_match = NODE_RE.search(node_text)
                if not node_match or not PADDEF_RE.search(paddef_text) or not REGULAR_RE.search(regular_text):
                    _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
                pin = _text(row, "pin_id").rsplit(":", 1)[-1]
                node_id = _text(row, "source_node_record_id").split(":", 2)[1]
                net = _text(row, "source_node_record_id").split(":", 2)[2]
                if node_match.group("node") != node_id.removeprefix("Node") or node_match.group("pin") != pin or node_match.group("net") != net:
                    _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
                x = _decimal_pm(node_match.group("x"), Decimal(PM_PER_MM))
                y = _decimal_pm(node_match.group("y"), Decimal(PM_PER_MM))
                radius = _decimal_pm("3.000000e-02", Decimal(PM_PER_MM))
                if first_radius is None:
                    first_radius = radius
                elif radius != first_radius:
                    _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISMATCH")
                diameter = 2 * radius
                bbox = [x - radius, y - radius, x + radius, y + radius]
                boxes.append(bbox)
                evidence.append({"pin_id": row.get("pin_id"), "source_records": refs, "center_pm": [x, y], "radius_pm": radius, "diameter_pm": diameter, "bbox_pm": bbox})
    except AuditStop:
        raise
    except OSError:
        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISSING")
    if len(boxes) != 6 or first_radius is None:
        _stop("STOP_W0_FOOTPRINT_EVIDENCE_MISSING")
    gap = first_radius if gap_pm is None else gap_pm
    if type(gap) is not int or gap <= 0:
        _stop("STOP_W0_GAP_MISSING")
    xmin = min(box[0] for box in boxes)
    ymin = min(box[1] for box in boxes)
    xmax = max(box[2] for box in boxes)
    ymax = max(box[3] for box in boxes)
    largest_diameter = max(box[2] - box[0] for box in boxes)
    padding = max(8 * gap, 4 * largest_diameter)
    raw_bounds = [xmin - padding, ymin - padding, xmax + padding, ymax + padding]
    snapped = [raw_bounds[0] // GRID_PM * GRID_PM, raw_bounds[1] // GRID_PM * GRID_PM, -((-raw_bounds[2]) // GRID_PM) * GRID_PM, -((-raw_bounds[3]) // GRID_PM) * GRID_PM]
    if snapped != list(asserted):
        _stop("STOP_W0_ASSERTION_MISMATCH")
    return {"gap_pm": gap, "gap_um": gap // PM_PER_UM, "padding_pm": padding, "padding_policy": "max(8*g,4*largest_pad_diameter)", "largest_diameter_pm": largest_diameter, "raw_bounds_pm": raw_bounds, "grid_pm": GRID_PM, "snapped_bounds_pm": snapped, "footprint_count": len(evidence), "footprints": evidence}


D104_EXPECTED: dict[int, tuple[str, str, str]] = {
    259: ("Signal$L29(DGND)", "DGND", "spd-surface-island:2db099ba622781734a17c3e0"),
    260: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRH/0", "spd-surface-island:b0fa08be68439c16ffc58c77"),
    261: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRH/1", "spd-surface-island:b28e75ae9922b1f7756588f4"),
    262: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRL/0", "spd-surface-island:8144c5b28d0583106ec2b663"),
    263: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRL/1", "spd-surface-island:862ac6d69e684b207043f1e3"),
    264: ("Signal$L30(OTHER_POWER1)", EXPECTED_RAIL, "spd-surface-island:cb8510a79529b7f6f4f4afd4"),
    265: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_180_VQPS_SYS_1_AON/1", "spd-surface-island:f1e1df51a7abbeeb63114ac5"),
    266: ("Signal$L30(OTHER_POWER1)", "DGND", "spd-surface-island:e58fb10620c5ba652b665ee3"),
}


def _finite_shape(shape: Any) -> bool:
    if shape.geom_type not in {"Polygon", "MultiPolygon"} or shape.is_empty or not shape.is_valid or not math.isfinite(float(shape.area)) or float(shape.area) <= 0:
        return False
    polygons = list(shape.geoms) if shape.geom_type == "MultiPolygon" else [shape]
    try:
        for polygon in polygons:
            for ring in (polygon.exterior, *polygon.interiors):
                for x, y, *_ in ring.coords:
                    if not math.isfinite(float(x)) or not math.isfinite(float(y)):
                        return False
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def _geometry(d104: Mapping[str, Any], root: Path, window: Sequence[int], deadline: float) -> list[dict[str, Any]]:
    if d104.get("layer_counts") != {"L28": 1, "L29": 1, "L30": 7, "L31": 7} or not isinstance(window, (list, tuple)) or len(window) != 4 or any(type(value) is not int for value in window) or window[0] >= window[2] or window[1] >= window[3]:
        _stop("STOP_D104_CENSUS_MISMATCH")
    cells = d104.get("cells")
    if not isinstance(cells, list) or len(cells) != 16:
        _stop("STOP_D104_CENSUS_MISMATCH")
    if any(not isinstance(item, Mapping) or type(item.get("ordinal")) is not int for item in cells):
        _stop("STOP_D104_CENSUS_MISMATCH")
    ordinals = [item["ordinal"] for item in cells]
    if sorted(ordinals) != list(range(258, 274)):
        _stop("STOP_D104_CENSUS_MISMATCH")
    selected = {int(item["ordinal"]): item for item in cells if isinstance(item, Mapping) and type(item.get("ordinal")) is int and item["ordinal"] in D104_EXPECTED}
    if set(selected) != set(D104_EXPECTED):
        _stop("STOP_D104_CENSUS_MISMATCH")
    try:
        from shapely import wkb
        from shapely.geometry import box
    except ImportError:
        _stop("STOP_INTERNAL_SHAPELY")
    win = box(window[0] / PM_PER_UM, window[1] / PM_PER_UM, window[2] / PM_PER_UM, window[3] / PM_PER_UM)
    result: list[dict[str, Any]] = []
    try:
        root_resolved = root.resolve(strict=True)
    except OSError:
        _stop("STOP_D104_IDENTITY")
    for ordinal in range(259, 267):
        _tick(deadline)
        item = selected[ordinal]
        expected_layer, expected_net, expected_island = D104_EXPECTED[ordinal]
        geometry = item.get("geometry")
        if (
            item.get("layer") != expected_layer
            or item.get("net") != expected_net
            or item.get("island_id") != expected_island
            or not isinstance(geometry, Mapping)
            or geometry.get("filename") != f"cell_{ordinal:04d}.wkb"
            or not isinstance(geometry.get("wkb_sha256"), str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", geometry["wkb_sha256"])
            or type(geometry.get("wkb_size_bytes")) is not int
            or not isinstance(geometry.get("bbox_um"), list)
            or len(geometry["bbox_um"]) != 4
        ):
            _stop("STOP_D104_CENSUS_MISMATCH")
        path = (root / str(geometry["filename"])).resolve(strict=False)
        if path.parent != root_resolved:
            _stop("STOP_D104_IDENTITY")
        payload = _read_bytes(path, deadline, "STOP_D104_IDENTITY")
        actual_sha = hashlib.sha256(payload).hexdigest()
        if len(payload) != geometry["wkb_size_bytes"] or actual_sha.casefold() != str(geometry["wkb_sha256"]).casefold():
            _stop("STOP_D104_IDENTITY")
        try:
            shape = wkb.loads(payload)
        except Exception:
            _stop("STOP_D104_GEOMETRY_INVALID")
        if not _finite_shape(shape):
            _stop("STOP_D104_GEOMETRY_INVALID")
        bounds = [float(value) for value in shape.bounds]
        expected_bounds = [float(value) for value in geometry["bbox_um"]]
        if any(not math.isfinite(value) for value in expected_bounds) or any(not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-6) for actual, expected in zip(bounds, expected_bounds)):
            _stop("STOP_D104_GEOMETRY_INVALID")
        expected_area = geometry.get("area_um2", geometry.get("area"))
        if expected_area is not None and (not isinstance(expected_area, (int, float)) or not math.isclose(float(shape.area), float(expected_area), rel_tol=1e-9, abs_tol=1e-3)):
            _stop("STOP_D104_GEOMETRY_INVALID")
        _tick(deadline)
        intersection = shape.intersection(win)
        _tick(deadline)
        nonempty = not intersection.is_empty and float(intersection.area) > 0
        if nonempty:
            intersection_payload = wkb.dumps(intersection)
            intersection_info: dict[str, Any] = {"wkb_sha256": hashlib.sha256(intersection_payload).hexdigest(), "wkb_size_bytes": len(intersection_payload), "bbox_um": [float(value) for value in intersection.bounds], "area_um2": float(intersection.area), "nonempty": True, "boundary_contact": bool(shape.boundary.intersects(win.boundary))}
        else:
            intersection_info = {"wkb_sha256": None, "wkb_size_bytes": None, "bbox_um": None, "area_um2": float(intersection.area), "nonempty": False, "boundary_contact": bool(shape.boundary.intersects(win.boundary))}
        source_wkb = {"filename": geometry["filename"], "sha256": actual_sha, "wkb_sha256": actual_sha, "size_bytes": len(payload), "wkb_size_bytes": len(payload), "bounds_um": bounds, "bbox_um": bounds, "area_um2": float(shape.area)}
        result.append({"ordinal": ordinal, "layer": expected_layer, "net": expected_net, "island_id": expected_island, "source_wkb": source_wkb, "intersection": intersection_info, "intersection_wkb_sha256": intersection_info["wkb_sha256"], "intersection_wkb_size_bytes": intersection_info["wkb_size_bytes"], "intersection_bbox_um": intersection_info["bbox_um"], "intersection_area_um2": intersection_info["area_um2"], "boundary_contact": intersection_info["boundary_contact"], "_shape": shape})
    return result


def _membership(cells: Sequence[Mapping[str, Any]], footprints: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    try:
        from shapely.geometry import Point
    except ImportError:
        _stop("STOP_INTERNAL_SHAPELY")
    by_ordinal: dict[int, Mapping[str, Any]] = {}
    for cell in cells:
        if not isinstance(cell, Mapping) or type(cell.get("ordinal")) is not int or "_shape" not in cell:
            _stop("STOP_FOOTPRINT_MEMBERSHIP")
        by_ordinal[cell["ordinal"]] = cell
    out: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for footprint in footprints:
        pin = _text(footprint, "pin_id", "STOP_FOOTPRINT_MEMBERSHIP")
        center = footprint.get("center_pm")
        radius_pm = footprint.get("radius_pm")
        if not isinstance(center, list) or len(center) != 2 or any(type(value) is not int for value in center) or type(radius_pm) is not int or radius_pm <= 0:
            _stop("STOP_FOOTPRINT_MEMBERSHIP")
        role = "power" if pin in {"SITE0:20122", "SITE0:20576", "SITE0:20589"} else "ground" if pin in {"SITE0:19958", "SITE0:20612", "SITE0:19973"} else ""
        target_ordinal = 264 if role == "power" else 259 if role == "ground" else -1
        if target_ordinal not in by_ordinal:
            _stop("STOP_FOOTPRINT_MEMBERSHIP")
        point = Point(center[0] / PM_PER_UM, center[1] / PM_PER_UM)
        radius_um = radius_pm / PM_PER_UM
        target = by_ordinal[target_ordinal]["_shape"]
        target_distance = float(point.distance(target.boundary))
        target_ok = bool(target.covers(point) and target_distance + 1e-9 >= radius_um)
        if not target_ok:
            violations.append({"pin_id": pin, "reason": "TARGET_ISLAND_CONTAINMENT", "target_ordinal": target_ordinal})
        other: list[dict[str, Any]] = []
        target_net = EXPECTED_RAIL if role == "power" else "DGND"
        target_layer = "Signal$L30(OTHER_POWER1)" if role == "power" else "Signal$L29(DGND)"
        for cell in cells:
            if cell.get("layer") != target_layer or cell.get("net") == target_net:
                continue
            distance = float(point.distance(cell["_shape"]))
            proven = math.isfinite(distance) and distance + 1e-9 >= radius_um
            other.append({"ordinal": cell["ordinal"], "net": cell["net"], "center_distance_um": distance, "radius_um": radius_um, "proven": proven})
            if not proven:
                violations.append({"pin_id": pin, "reason": "OTHER_SAME_LAYER_OVERLAP", "ordinal": cell["ordinal"], "net": cell["net"]})
        out.append({"pin_id": pin, "role": role, "target_ordinal": target_ordinal, "center_pm": center, "radius_pm": radius_pm, "target_center_covered": bool(target.covers(point)), "target_distance_to_boundary_um": target_distance, "target_proven": target_ok, "other_same_layer_islands": other})
    return {"evaluated": True, "passed": not violations, "violations": violations, "terminals": out}


def _public_cells(cells: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in cell.items() if key != "_shape"} for cell in cells]


def _atomic_write(output_path: Path, receipt: Mapping[str, Any]) -> None:
    data = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    except FileExistsError:
        _stop("STOP_OUTPUT_EXISTS")
    except OSError as exc:
        _stop("STOP_OUTPUT_WRITE:" + type(exc).__name__)


def audit_source_local_l29_l30_port_window(
    *,
    source_path: Path,
    d103_path: Path,
    d104_path: Path,
    d104_root: Path,
    d115b_path: Path,
    candidate_path: Path,
    output_path: Path,
    repo_root: Path,
    expected_source_sha256: str,
    expected_d103_sha256: str,
    expected_d104_sha256: str,
    expected_d115b_sha256: str,
    expected_candidate_sha256: str,
    expected_head: str,
    window_pm: Sequence[int],
    expected_positive_count: int = 3,
    expected_negative_count: int = 10919,
    port_id: str = EXPECTED_PORT,
    deadline_s: float = 1800.0,
) -> dict[str, Any]:
    source_path, d103_path, d104_path, d104_root, d115b_path, candidate_path, output_path, repo_root = (_path(value, "STOP_INPUT_MISSING") for value in (source_path, d103_path, d104_path, d104_root, d115b_path, candidate_path, output_path, repo_root))
    if output_path.exists():
        _stop("STOP_OUTPUT_EXISTS")
    if not d104_root.is_dir() or not output_path.parent.is_dir() or type(deadline_s) not in (int, float) or deadline_s <= 0 or not isinstance(window_pm, (list, tuple)) or len(window_pm) != 4 or any(type(value) is not int for value in window_pm) or window_pm[0] >= window_pm[2] or window_pm[1] >= window_pm[3]:
        _stop("STOP_INVALID_INPUT")
    if not _same_path(d104_root, d104_path.parent):
        _stop("STOP_D104_IDENTITY")
    deadline = time.monotonic() + float(deadline_s)
    git = _git_identity(repo_root, expected_head, deadline)
    source_size, source_sha = _hash_gate(source_path, expected_source_sha256, deadline, "STOP_SOURCE_IDENTITY")
    d103_size, d103_sha = _hash_gate(d103_path, expected_d103_sha256, deadline, "STOP_D103_IDENTITY")
    d104_size, d104_sha = _hash_gate(d104_path, expected_d104_sha256, deadline, "STOP_D104_IDENTITY")
    d115b_size, d115b_sha = _hash_gate(d115b_path, expected_d115b_sha256, deadline, "STOP_D115B_IDENTITY")
    candidate_size, candidate_sha = _hash_gate(candidate_path, expected_candidate_sha256, deadline, "STOP_CANDIDATE_IDENTITY")
    d103 = _read_json(d103_path, deadline, "STOP_D103_SCHEMA")
    d104 = _read_json(d104_path, deadline, "STOP_D104_SCHEMA")
    d115b = _read_json(d115b_path, deadline, "STOP_D115B_SCHEMA")
    d103_evidence = _validate_d103(d103, source_path=source_path, source_size=source_size, source_sha=source_sha)
    d115b_evidence = _validate_d115b(d115b, d115b_path=d115b_path, source_path=source_path, source_size=source_size, source_sha=source_sha, candidate_path=candidate_path, candidate_size=candidate_size, candidate_sha=candidate_sha, expected_head=expected_head)
    if (
        not isinstance(d104.get("source"), Mapping)
        or not _same_path(Path(str(d104["source"].get("path", ""))), source_path)
        or d104["source"].get("size_bytes") != source_size
        or str(d104["source"].get("sha256", "")).casefold() != source_sha.casefold()
        or d104.get("status") != "PASS"
        or d104.get("version") != VERSION
    ):
        _stop("STOP_D104_SOURCE_IDENTITY")
    cancelled = lambda: time.monotonic() > deadline
    try:
        bundle = load_scenario_bundle(candidate_path, is_cancelled=cancelled)
        manifest = validate_project_source_plane_ownership_ir_envelope(bundle.scenario.normalized_project, bundle.attachments)
        if manifest is None:
            _stop("STOP_IR_INCOMPLETE")
        expected_bindings = {f"expected_{key}": manifest[key] for key in ("source_sha256", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")}
        with load_source_plane_ownership_ir(manifest, bundle.attachments, expected_app_version=VERSION, is_cancelled=cancelled, **expected_bindings) as loaded:
            rails, terminals, records, loaded_manifest = _validate_candidate(loaded, manifest, d115b_evidence, source_sha=source_sha, source_size=source_size, deadline=deadline)
            selected_via_source_authority = _selected_via_source_authority(terminals, records, loaded, source_path, deadline)
    except AuditStop:
        raise
    except Exception as exc:
        _stop("STOP_IR_INVALID:" + type(exc).__name__)
    powers = [row for row in terminals if row.get("role") == "power"]
    grounds = [row for row in terminals if row.get("role") == "ground"]
    expected_positive = {_token_from_row(row) for row in powers}
    expected_negative = {_token_from_row(row) for row in grounds}
    port = _port(source_path, port_id, expected_positive_count, expected_negative_count, expected_positive, expected_negative, deadline)
    w0 = _derive_w0(terminals, records, source_path, window_pm, deadline, gap_pm=d103_evidence["gap_pm"])
    cells = _geometry(d104, d104_root, window_pm, deadline)
    empty_ordinals = [int(cell["ordinal"]) for cell in cells if not cell["intersection"]["nonempty"]]
    base_receipt: dict[str, Any] = {
        "product": PRODUCT,
        "version": VERSION,
        "schema": "source-local-l29-l30-port-window-receipt-v4",
        "status": "PASS_SOURCE_LOCAL_GAP_ONLY",
        "git": git,
        "git_head": git["head"],
        "git_branch": git["branch"],
        "tracked_clean": git["tracked_clean"],
        "inputs": {
            "source": {"path": str(source_path), "size_bytes": source_size, "sha256": source_sha},
            "d103": {"path": str(d103_path), "size_bytes": d103_size, "sha256": d103_sha},
            "d104": {"path": str(d104_path), "size_bytes": d104_size, "sha256": d104_sha},
            "d104_root": {"path": str(d104_root)},
            "d115b": {"path": str(d115b_path), "size_bytes": d115b_size, "sha256": d115b_sha},
            "candidate": {"path": str(candidate_path), "size_bytes": candidate_size, "sha256": candidate_sha},
            "repo_root": {"path": str(repo_root)},
        },
        "expected": {"head": expected_head, "window_pm": list(window_pm), "port_id": port_id, "positive_count": expected_positive_count, "negative_count": expected_negative_count},
        "d103": d103_evidence,
        "d115b": {"status": d115b_evidence.get("status"), "disposition": d115b_evidence.get("disposition"), "contract_head": d115b_evidence.get("contract_head"), "candidate": d115b_evidence.get("candidate")},
        "manifest": {key: loaded_manifest.get(key) for key in ("app_version", "source_sha256", "source_size_bytes", "target_rail_id")},
        "port44": port,
        "rail_bindings": rails,
        "terminal_bindings": terminals,
        "selected_via_source_authority": selected_via_source_authority,
        "w0": w0,
        "d104_cells": _public_cells(cells),
        "scope": {"source_local_shadow_only": True, "solver_executed": False, "powersi_executed": False, "generic_four_layer_model": False, "c_res": False},
    }
    base_receipt["d103"]["stackup_layers"] = base_receipt["d103"]["layers"]
    membership = _membership(cells, w0["footprints"])
    base_receipt["membership"] = membership
    if empty_ordinals:
        base_receipt.update({"status": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT", "code": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT", "empty_ordinals": empty_ordinals, "coverage_conflict": {"empty_ordinals": empty_ordinals, "required_ordinals": list(range(259, 267)), "all_eight_evaluated": True}})
        _atomic_write(output_path, base_receipt)
        _stop("STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT")
    if not membership["passed"]:
        base_receipt.update({"status": "STOP_FOOTPRINT_MEMBERSHIP", "code": "STOP_FOOTPRINT_MEMBERSHIP"})
        _atomic_write(output_path, base_receipt)
        _stop("STOP_FOOTPRINT_MEMBERSHIP")
    _atomic_write(output_path, base_receipt)
    return base_receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "d103", "d104", "d104-root", "d115b", "candidate", "output", "repo-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("source", "d103", "d104", "d115b", "candidate"):
        parser.add_argument("--expected-" + name + "-sha256", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--window-pm", type=int, nargs=4, required=True)
    parser.add_argument("--port-id", default=EXPECTED_PORT)
    parser.add_argument("--expected-positive-count", type=int, default=3)
    parser.add_argument("--expected-negative-count", type=int, default=10919)
    parser.add_argument("--deadline-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    try:
        receipt = audit_source_local_l29_l30_port_window(
            source_path=args.source,
            d103_path=args.d103,
            d104_path=args.d104,
            d104_root=args.d104_root,
            d115b_path=args.d115b,
            candidate_path=args.candidate,
            output_path=args.output,
            repo_root=args.repo_root,
            expected_source_sha256=args.expected_source_sha256,
            expected_d103_sha256=args.expected_d103_sha256,
            expected_d104_sha256=args.expected_d104_sha256,
            expected_d115b_sha256=args.expected_d115b_sha256,
            expected_candidate_sha256=args.expected_candidate_sha256,
            expected_head=args.expected_head,
            window_pm=args.window_pm,
            port_id=args.port_id,
            expected_positive_count=args.expected_positive_count,
            expected_negative_count=args.expected_negative_count,
            deadline_s=args.deadline_seconds,
        )
    except AuditStop as exc:
        print(json.dumps({"result": "STOP", "code": exc.code}, sort_keys=True))
        return 1
    print(json.dumps({"result": receipt["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
