#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.23.1 D117 WP2 synthetic authority gate.

The only supported input is the tiny, code-bound synthetic contract returned by
``synthetic_input``. A later source-bound approval path must be implemented
separately; this module never reads raw SPD or runs a numerical tool.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
except ImportError:  # pragma: no cover - the product already ships Shapely
    Polygon = unary_union = None

try:
    from spd_decap_pi.canonical_json import concrete_canonical_json_bytes
except ImportError:  # direct script invocation from a source checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from spd_decap_pi.canonical_json import concrete_canonical_json_bytes


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
PROGRAM = PRODUCT
SCHEMA_VERSION = "d117-wp2-material-authority-receipt-v1"
SCOPE = "MATERIAL_AUTHORITY"
PASS_SYNTHETIC_CONTRACT = "PASS_SYNTHETIC_CONTRACT"
STATIC_CONTRACT_VALIDATED = "STATIC_CONTRACT_VALIDATED"

STOP_XY_PARTITION_UNSEALED = "STOP_XY_PARTITION_UNSEALED"
STOP_VOID_FILL_AMBIGUOUS = "STOP_VOID_FILL_AMBIGUOUS"
STOP_ABSOLUTE_SOURCE_Z_UNSEALED = "STOP_ABSOLUTE_SOURCE_Z_UNSEALED"
STOP_PANEL_SIDE_UNPROVEN = "STOP_PANEL_SIDE_UNPROVEN"
STOP_CLOSURE_BASIS_CONFLICT = "STOP_CLOSURE_BASIS_CONFLICT"
STOP_INPUT_IDENTITY_MISMATCH = "STOP_INPUT_IDENTITY_MISMATCH"
STOP_ARTIFACT_CAP_EXCEEDED = "STOP_ARTIFACT_CAP_EXCEEDED"
STOP_DETERMINISTIC_REPLAY_MISMATCH = "STOP_DETERMINISTIC_REPLAY_MISMATCH"
STOP_OUTPUT_EXISTS = "STOP_OUTPUT_EXISTS"
STOP_NUMERICAL_EXECUTION = "STOP_NUMERICAL_EXECUTION"

FIELDS = (
    "xy_dielectric_partitions",
    "conductor_layer_void_fill",
    "reference_points_and_panel_sides",
    "outer_truncation_and_closure",
    "absolute_source_z_transform",
)
ORDINALS = tuple(range(258, 274))
LAYERS = ("L28", "L29", *(["L30"] * 7), *(["L31"] * 7))
FILL_LAYERS = ("L28", "L29", "L30", "L31")
UNITS = frozenset(("um", "mm", "m"))
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_EPS = 1.0e-10


class Refusal(RuntimeError):
    """One fail-closed STOP with its exact status code."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"{status}: {message}")


def _fail(status: str, message: str) -> None:
    raise Refusal(status, message)


def _keys(value: Any, expected: set[str], label: str, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        _fail(status, f"{label} keys are not exact")
    return value


def _str(value: Any, label: str, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> str:
    if type(value) is not str or not value or "\x00" in value:
        _fail(status, f"{label} must be a non-empty string")
    return value


def _int(value: Any, label: str, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> int:
    if type(value) is not int or value < 0:
        _fail(status, f"{label} must be a non-negative integer")
    return value


def _num(value: Any, label: str, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        _fail(status, f"{label} must be finite")
    return float(value)


def _unit(value: Any, label: str, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> str:
    unit = _str(value, label, status)
    if unit not in UNITS:
        _fail(status, f"{label} is unsupported")
    return unit


def _hash(value: Any, label: str, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> str:
    digest = _str(value, label, status).lower()
    if _HEX64.fullmatch(digest) is None:
        _fail(status, f"{label} is not SHA-256")
    return digest


def _path(value: Any, label: str, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> str:
    path = _str(value, label, status)
    parsed = PurePosixPath(path)
    if "\\" in path or parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() != path:
        _fail(status, f"{label} is not a canonical relative path")
    return path


def _point(value: Any, label: str, count: int, status: str = STOP_INPUT_IDENTITY_MISMATCH) -> tuple[float, ...]:
    if type(value) is not list or len(value) != count:
        _fail(status, f"{label} coordinate count is invalid")
    return tuple(_num(item, f"{label}[{index}]", status) for index, item in enumerate(value))


def _canonical(value: Any) -> bytes:
    try:
        return concrete_canonical_json_bytes(value)
    except (TypeError, ValueError, OverflowError) as exc:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"canonical JSON failed: {exc}")


def _strict_json(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out

    def constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    def finite(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant, parse_float=finite)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"strict JSON failed: {exc}")
    if type(result) is not dict:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "JSON root is not an object")
    return result


def _artifact(row: Any, payload: dict[str, Any], seen: dict[str, dict[str, Any]], status: str = STOP_INPUT_IDENTITY_MISMATCH) -> dict[str, Any]:
    item = _keys(row, {"path", "size_bytes", "sha256"}, "artifact", status)
    path = _path(item["path"], "artifact.path", status)
    size = _int(item["size_bytes"], "artifact.size_bytes", status)
    digest = _hash(item["sha256"], "artifact.sha256", status)
    if size == 0 or size > MAX_ARTIFACT_BYTES:
        _fail(STOP_ARTIFACT_CAP_EXCEEDED, f"artifact cap exceeded: {path}")
    encoded = _canonical(payload)
    if size != len(encoded) or digest != sha256(encoded).hexdigest():
        _fail(status, f"artifact identity mismatch: {path}")
    if path in seen:
        _fail(status, f"duplicate artifact path: {path}")
    value = {"path": path, "size_bytes": size, "sha256": digest}
    seen[path] = value
    return value


def _raw_content() -> str:
    return "SPD-DECAP-PI-EVALUATOR synthetic raw source v1\n"


def _domain() -> dict[str, Any]:
    return {"id": "domain-0", "unit": "um", "outline": [[0.0, 0.0], [16.0, 0.0], [16.0, 1.0], [0.0, 1.0]]}


def _cells() -> list[dict[str, Any]]:
    rows = []
    for ordinal, layer in zip(ORDINALS, LAYERS):
        x = float(ordinal - ORDINALS[0])
        geometry = {"type": "Polygon", "coordinates": [[[x, 0.0], [x + 1.0, 0.0], [x + 1.0, 1.0], [x, 1.0], [x, 0.0]]]}
        core = {"ordinal": ordinal, "conductor_id": f"conductor-{ordinal}", "layer": layer, "island_id": f"island-{ordinal}", "unit": "um", "geometry": geometry}
        encoded = _canonical(core)
        rows.append({**core, "artifact": {"path": f"d104/cell-{ordinal}.json", "size_bytes": len(encoded), "sha256": sha256(encoded).hexdigest()}})
    return rows


def _static_evidence() -> dict[str, str]:
    raw = _raw_content()
    raw_hash = sha256(raw.encode()).hexdigest()
    d103 = {"schema_version": "d103-synthetic-v1", "program": PROGRAM, "version": VERSION, "status": "PASS", "raw_source_sha256": raw_hash, "materials": ["ABF-GL102", "EL190T", "COPPER", "AIR"]}
    d103_text = _canonical(d103).decode("utf-8")
    d103_hash = sha256(d103_text.encode()).hexdigest()
    d104 = {"schema_version": "d104-synthetic-v1", "program": PROGRAM, "version": VERSION, "status": "PASS", "raw_source_sha256": raw_hash, "d103_sha256": d103_hash, "domain": _domain(), "cells": _cells()}
    d104_text = _canonical(d104).decode("utf-8")
    d104_hash = sha256(d104_text.encode()).hexdigest()
    predecessor = {"schema_version": "d117-wp2-predecessor-v1", "program": PROGRAM, "version": VERSION, "status": STOP_NUMERICAL_EXECUTION, "material_authority_status": "PARTIAL", "executed_numerical_components": []}
    predecessor_text = _canonical(predecessor).decode("utf-8")
    d115b = {"schema_version": "d115b-synthetic-v1", "program": PROGRAM, "version": VERSION, "status": "PASS", "d104_sha256": d104_hash, "basis_ordinals": list(ORDINALS), "basis_islands": [f"island-{ordinal}" for ordinal in ORDINALS]}
    d115b_text = _canonical(d115b).decode("utf-8")
    return {"raw_source": raw, "wp2_predecessor_receipt": predecessor_text, "d103": d103_text, "d104": d104_text, "d115b": d115b_text}


_EXPECTED_EVIDENCE = _static_evidence()
_EVIDENCE_PATHS = {name: f"synthetic/{name}.json" for name in _EXPECTED_EVIDENCE}
_JSON_EVIDENCE = frozenset(set(_EXPECTED_EVIDENCE) - {"raw_source"})


def _evidence(records: dict[str, Any]) -> dict[str, Any]:
    evidence = _keys(records.get("evidence"), set(_EXPECTED_EVIDENCE), "evidence")
    parsed: dict[str, Any] = {}
    for name, expected in _EXPECTED_EVIDENCE.items():
        row = _keys(evidence[name], {"path", "content", "size_bytes", "sha256"}, f"evidence.{name}")
        path = _path(row["path"], f"evidence.{name}.path")
        content = row["content"]
        if type(content) is not str or content != expected:
            _fail(STOP_INPUT_IDENTITY_MISMATCH, f"evidence content mismatch: {name}")
        encoded = content.encode("utf-8")
        size = _int(row["size_bytes"], f"evidence.{name}.size_bytes")
        digest = _hash(row["sha256"], f"evidence.{name}.sha256")
        if path != _EVIDENCE_PATHS[name] or size != len(encoded) or digest != sha256(encoded).hexdigest() or size > MAX_EVIDENCE_BYTES:
            _fail(STOP_INPUT_IDENTITY_MISMATCH, f"evidence ledger mismatch: {name}")
        parsed[name] = _strict_json(encoded) if name in _JSON_EVIDENCE else None
    predecessor = parsed["wp2_predecessor_receipt"]
    _keys(predecessor, {"schema_version", "program", "version", "status", "material_authority_status", "executed_numerical_components"}, "predecessor receipt")
    if predecessor["program"] != PROGRAM or predecessor["version"] != VERSION or predecessor["status"] != STOP_NUMERICAL_EXECUTION or predecessor["material_authority_status"] != "PARTIAL" or predecessor["executed_numerical_components"] != []:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "predecessor status contract mismatch")
    d103 = parsed["d103"]
    _keys(d103, {"schema_version", "program", "version", "status", "raw_source_sha256", "materials"}, "D103 receipt")
    if d103["program"] != PROGRAM or d103["version"] != VERSION or d103["status"] != "PASS" or d103["raw_source_sha256"] != sha256(_EXPECTED_EVIDENCE["raw_source"].encode()).hexdigest() or d103["materials"] != ["ABF-GL102", "EL190T", "COPPER", "AIR"]:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "D103 anchor mismatch")
    d104 = parsed["d104"]
    _keys(d104, {"schema_version", "program", "version", "status", "raw_source_sha256", "d103_sha256", "domain", "cells"}, "D104 receipt")
    if d104["program"] != PROGRAM or d104["version"] != VERSION or d104["status"] != "PASS" or d104["raw_source_sha256"] != d103["raw_source_sha256"] or d104["d103_sha256"] != sha256(_EXPECTED_EVIDENCE["d103"].encode()).hexdigest():
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "D104 anchor mismatch")
    d115b = parsed["d115b"]
    _keys(d115b, {"schema_version", "program", "version", "status", "d104_sha256", "basis_ordinals", "basis_islands"}, "D115B receipt")
    if d115b["program"] != PROGRAM or d115b["version"] != VERSION or d115b["status"] != "PASS" or d115b["d104_sha256"] != sha256(_EXPECTED_EVIDENCE["d104"].encode()).hexdigest() or d115b["basis_ordinals"] != list(ORDINALS) or d115b["basis_islands"] != [f"island-{ordinal}" for ordinal in ORDINALS]:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "D115B anchor mismatch")
    return parsed


def _derived_basis(d104: dict[str, Any], seen: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    _keys(d104["domain"], {"id", "unit", "outline"}, "D104 domain", STOP_XY_PARTITION_UNSEALED)
    rows = d104["cells"]
    if type(rows) is not list or len(rows) != 16:
        _fail(STOP_XY_PARTITION_UNSEALED, "D104 cell count is not 16")
    basis: list[dict[str, Any]] = []
    ordinals: list[int] = []
    for row in rows:
        item = _keys(row, {"ordinal", "conductor_id", "layer", "island_id", "unit", "geometry", "artifact"}, "D104 cell", STOP_XY_PARTITION_UNSEALED)
        ordinal = _int(item["ordinal"], "D104 ordinal", STOP_XY_PARTITION_UNSEALED)
        if ordinal in ordinals or ordinal not in ORDINALS:
            _fail(STOP_XY_PARTITION_UNSEALED, "D104 ordinals are not exact")
        conductor_id = _str(item["conductor_id"], "D104 conductor_id", STOP_XY_PARTITION_UNSEALED)
        layer = _str(item["layer"], "D104 layer", STOP_XY_PARTITION_UNSEALED)
        island = _str(item["island_id"], "D104 island_id", STOP_XY_PARTITION_UNSEALED)
        unit = _unit(item["unit"], "D104 unit", STOP_XY_PARTITION_UNSEALED)
        core = {"ordinal": ordinal, "conductor_id": conductor_id, "layer": layer, "island_id": island, "unit": unit, "geometry": item["geometry"]}
        artifact = _artifact(item["artifact"], core, seen, STOP_XY_PARTITION_UNSEALED)
        basis.append({"ordinal": ordinal, "conductor_id": conductor_id, "layer": layer, "island_id": island})
        ordinals.append(ordinal)
        item["_artifact"] = artifact
    if tuple(sorted(ordinals)) != ORDINALS or len({item["conductor_id"] for item in basis}) != 16 or len({item["island_id"] for item in basis}) != 16:
        _fail(STOP_XY_PARTITION_UNSEALED, "D104 basis identity is not exhaustive")
    return basis


def _xy(records: dict[str, Any], d104: dict[str, Any], seen: dict[str, dict[str, Any]]) -> dict[str, Any]:
    data = _keys(records["xy_dielectric_partitions"], {"status", "domain", "coverage"}, "xy_dielectric_partitions", STOP_XY_PARTITION_UNSEALED)
    if data["status"] != "SEALED":
        _fail(STOP_XY_PARTITION_UNSEALED, "XY status is not SEALED")
    domain = _keys(data["domain"], {"id", "unit", "outline", "artifact"}, "XY domain", STOP_XY_PARTITION_UNSEALED)
    domain_id = _str(domain["id"], "XY domain.id", STOP_XY_PARTITION_UNSEALED)
    unit = _unit(domain["unit"], "XY domain.unit", STOP_XY_PARTITION_UNSEALED)
    outline = [_point(point, "XY outline point", 2, STOP_XY_PARTITION_UNSEALED) for point in domain["outline"]]
    if len(outline) < 4 or len(set(outline)) != len(outline):
        _fail(STOP_XY_PARTITION_UNSEALED, "XY outline is not unique")
    domain_shape = Polygon(outline) if Polygon is not None else None
    if domain_shape is None or not domain_shape.is_valid or domain_shape.area <= _EPS:
        _fail(STOP_XY_PARTITION_UNSEALED, "XY domain geometry is unavailable or invalid")
    canonical_domain = {"id": domain_id, "unit": unit, "outline": [list(point) for point in outline]}
    domain_artifact = _artifact(domain["artifact"], canonical_domain, seen, STOP_XY_PARTITION_UNSEALED)
    if d104["domain"] != canonical_domain:
        _fail(STOP_XY_PARTITION_UNSEALED, "XY domain is not D104-bound")
    rows = data["coverage"]
    if type(rows) is not list or len(rows) != len(ORDINALS):
        _fail(STOP_XY_PARTITION_UNSEALED, "XY coverage is not exhaustive")
    cells = {cell["ordinal"]: cell for cell in d104["cells"]}
    shapes = []
    artifacts = []
    ordinals = []
    for row in rows:
        item = _keys(row, {"ordinal", "unit", "source_cell_sha256", "geometry", "artifact"}, "XY coverage row", STOP_XY_PARTITION_UNSEALED)
        ordinal = _int(item["ordinal"], "XY coverage ordinal", STOP_XY_PARTITION_UNSEALED)
        if ordinal in ordinals or ordinal not in ORDINALS or ordinal not in cells:
            _fail(STOP_XY_PARTITION_UNSEALED, "XY coverage ordinals are not exact")
        if item["source_cell_sha256"] != cells[ordinal]["_artifact"]["sha256"]:
            _fail(STOP_XY_PARTITION_UNSEALED, "XY coverage is not D104-bound")
        if item["geometry"] != cells[ordinal]["geometry"]:
            _fail(STOP_XY_PARTITION_UNSEALED, "XY coverage geometry is not D104-bound")
        row_unit = _unit(item["unit"], "XY coverage unit", STOP_XY_PARTITION_UNSEALED)
        if row_unit != unit:
            _fail(STOP_XY_PARTITION_UNSEALED, "XY coverage units differ")
        geometry = _keys(item["geometry"], {"type", "coordinates"}, "D104 geometry", STOP_XY_PARTITION_UNSEALED)
        if geometry["type"] != "Polygon" or type(geometry["coordinates"]) is not list or len(geometry["coordinates"]) != 1:
            _fail(STOP_XY_PARTITION_UNSEALED, "D104 geometry is not a simple polygon")
        try:
            shape = Polygon([_point(point, "D104 vertex", 2, STOP_XY_PARTITION_UNSEALED) for point in geometry["coordinates"][0]])
        except Exception as exc:
            _fail(STOP_XY_PARTITION_UNSEALED, f"D104 geometry failed: {exc}")
        if not shape.is_valid or shape.area <= _EPS or not domain_shape.covers(shape):
            _fail(STOP_XY_PARTITION_UNSEALED, "D104 geometry is invalid or outside the domain")
        shape_artifact = _artifact(item["artifact"], {"ordinal": ordinal, "unit": row_unit, "source_cell_sha256": item["source_cell_sha256"], "geometry": geometry}, seen, STOP_XY_PARTITION_UNSEALED)
        shapes.append(shape)
        artifacts.append(shape_artifact)
        ordinals.append(ordinal)
    if tuple(sorted(ordinals)) != ORDINALS:
        _fail(STOP_XY_PARTITION_UNSEALED, "XY coverage ordinals are incomplete")
    for index, first in enumerate(shapes):
        if any(first.intersection(second).area > _EPS for second in shapes[index + 1 :]):
            _fail(STOP_XY_PARTITION_UNSEALED, "D104 geometries overlap")
    union = unary_union(shapes)
    if union.is_empty or abs(union.area - domain_shape.area) > _EPS or domain_shape.symmetric_difference(union).area > _EPS:
        _fail(STOP_XY_PARTITION_UNSEALED, "D104 geometries do not exhaustively cover the domain")
    return {"status": STATIC_CONTRACT_VALIDATED, "domain": canonical_domain, "coverage_ordinals": list(ORDINALS), "domain_artifact": domain_artifact, "union_area": float(union.area), "partition_artifacts": artifacts}


def _material(value: Any, label: str, materials: set[str], status: str) -> str:
    material = _str(value, label, status)
    if material not in materials:
        _fail(status, f"unknown material: {material}")
    return material


def _coverage(value: Any, label: str, *, full: bool, status: str) -> list[int]:
    if type(value) is not list or any(type(item) is not int for item in value):
        _fail(status, f"{label} is not an integer array")
    if len(set(value)) != len(value) or any(item not in ORDINALS for item in value) or (full and value != list(ORDINALS)):
        _fail(status, f"{label} is not bound to D104 coverage")
    return list(value)


def _fill(records: dict[str, Any], xy: dict[str, Any], d104: dict[str, Any], seen: dict[str, dict[str, Any]], materials: set[str]) -> dict[str, Any]:
    data = _keys(records["conductor_layer_void_fill"], {"status", "domain_id", "coverage_ordinals", "default_fill", "layer_fills", "aperture_exceptions"}, "conductor_layer_void_fill", STOP_VOID_FILL_AMBIGUOUS)
    if data["status"] != "SEALED" or data["domain_id"] != xy["domain"]["id"]:
        _fail(STOP_VOID_FILL_AMBIGUOUS, "void-fill status/domain is not bound")
    full = _coverage(data["coverage_ordinals"], "void-fill coverage", full=True, status=STOP_VOID_FILL_AMBIGUOUS)
    default = _keys(data["default_fill"], {"domain_id", "material", "explicit", "coverage_ordinals", "artifact"}, "default fill", STOP_VOID_FILL_AMBIGUOUS)
    if default["domain_id"] != xy["domain"]["id"]:
        _fail(STOP_VOID_FILL_AMBIGUOUS, "default fill domain is not bound")
    if default["explicit"] is not True:
        _fail(STOP_VOID_FILL_AMBIGUOUS, "default fill is not explicitly sealed")
    default_material = _material(default["material"], "default material", materials, STOP_VOID_FILL_AMBIGUOUS)
    if _coverage(default["coverage_ordinals"], "default coverage", full=True, status=STOP_VOID_FILL_AMBIGUOUS) != full:
        _fail(STOP_VOID_FILL_AMBIGUOUS, "default coverage differs")
    default_artifact = _artifact(default["artifact"], {"domain_id": xy["domain"]["id"], "material": default_material, "explicit": True, "coverage_ordinals": full}, seen, STOP_VOID_FILL_AMBIGUOUS)
    rows = data["layer_fills"]
    if type(rows) is not list or len(rows) != len(FILL_LAYERS):
        _fail(STOP_VOID_FILL_AMBIGUOUS, "layer fill count is incomplete")
    fills: dict[str, Any] = {}
    for row in rows:
        item = _keys(row, {"domain_id", "layer", "material", "explicit", "coverage_ordinals", "artifact", "aperture_exceptions"}, "layer fill", STOP_VOID_FILL_AMBIGUOUS)
        layer = _str(item["layer"], "layer fill.layer", STOP_VOID_FILL_AMBIGUOUS)
        if layer in fills or layer not in FILL_LAYERS or item["domain_id"] != xy["domain"]["id"] or item["explicit"] is not True:
            _fail(STOP_VOID_FILL_AMBIGUOUS, f"layer fill is duplicated/unexplicit: {layer}")
        material = _material(item["material"], f"{layer} material", materials, STOP_VOID_FILL_AMBIGUOUS)
        coverage = _coverage(item["coverage_ordinals"], f"{layer} coverage", full=True, status=STOP_VOID_FILL_AMBIGUOUS)
        if item["aperture_exceptions"] != []:
            _fail(STOP_VOID_FILL_AMBIGUOUS, f"{layer} aperture exceptions must be in the top-level ledger")
        artifact = _artifact(item["artifact"], {"domain_id": xy["domain"]["id"], "layer": layer, "material": material, "explicit": True, "coverage_ordinals": coverage, "aperture_exceptions": []}, seen, STOP_VOID_FILL_AMBIGUOUS)
        fills[layer] = {"layer": layer, "material": material, "coverage_ordinals": coverage, "aperture_exceptions": item["aperture_exceptions"], "artifact": artifact}
    if set(fills) != set(FILL_LAYERS):
        _fail(STOP_VOID_FILL_AMBIGUOUS, "layer fill coverage is incomplete")
    exceptions = data["aperture_exceptions"]
    if type(exceptions) is not list or not exceptions:
        _fail(STOP_VOID_FILL_AMBIGUOUS, "aperture exception ledger is missing")
    normalized_exceptions = []
    exception_ids: set[str] = set()
    domain_shape = Polygon(xy["domain"]["outline"])
    cell_shapes = {
        cell["ordinal"]: Polygon([_point(point, "D104 vertex", 2, STOP_VOID_FILL_AMBIGUOUS) for point in cell["geometry"]["coordinates"][0]])
        for cell in d104["cells"]
    }
    for exception in exceptions:
        item = _keys(exception, {"id", "layer", "material", "explicit", "coverage_ordinals", "geometry", "artifact"}, "aperture exception", STOP_VOID_FILL_AMBIGUOUS)
        ident = _str(item["id"], "aperture exception.id", STOP_VOID_FILL_AMBIGUOUS)
        layer = _str(item["layer"], "aperture exception.layer", STOP_VOID_FILL_AMBIGUOUS)
        if ident in exception_ids or layer not in FILL_LAYERS or item["explicit"] is not True:
            _fail(STOP_VOID_FILL_AMBIGUOUS, f"aperture exception identity is ambiguous: {ident}")
        exception_ids.add(ident)
        material = _material(item["material"], f"aperture exception {ident}.material", materials, STOP_VOID_FILL_AMBIGUOUS)
        coverage = _coverage(item["coverage_ordinals"], f"aperture exception {ident} coverage", full=False, status=STOP_VOID_FILL_AMBIGUOUS)
        geometry = _keys(item["geometry"], {"type", "coordinates"}, f"aperture exception {ident}.geometry", STOP_VOID_FILL_AMBIGUOUS)
        try:
            shape = Polygon([_point(point, "aperture vertex", 2, STOP_VOID_FILL_AMBIGUOUS) for point in geometry["coordinates"][0]]) if geometry["type"] == "Polygon" and type(geometry["coordinates"]) is list and len(geometry["coordinates"]) == 1 else None
        except Exception as exc:
            _fail(STOP_VOID_FILL_AMBIGUOUS, f"aperture exception geometry failed: {exc}")
        if shape is None or not shape.is_valid or shape.area <= _EPS or not domain_shape.covers(shape):
            _fail(STOP_VOID_FILL_AMBIGUOUS, f"aperture exception geometry is invalid: {ident}")
        covered_shape = unary_union([cell_shapes[ordinal] for ordinal in coverage])
        if not covered_shape.covers(shape):
            _fail(STOP_VOID_FILL_AMBIGUOUS, f"aperture exception geometry is outside declared coverage: {ident}")
        artifact = _artifact(item["artifact"], {"id": ident, "layer": layer, "material": material, "explicit": True, "coverage_ordinals": coverage, "geometry": geometry}, seen, STOP_VOID_FILL_AMBIGUOUS)
        normalized_exceptions.append({"id": ident, "layer": layer, "material": material, "coverage_ordinals": coverage, "geometry": geometry, "artifact": artifact})
    return {"status": STATIC_CONTRACT_VALIDATED, "domain_id": xy["domain"]["id"], "coverage_ordinals": full, "default_fill": {"material": default_material, "artifact": default_artifact}, "layer_fills": [fills[layer] for layer in FILL_LAYERS], "aperture_exceptions": normalized_exceptions}


def _panel(records: dict[str, Any], xy: dict[str, Any], seen: dict[str, dict[str, Any]], materials: set[str]) -> dict[str, Any]:
    data = _keys(records["reference_points_and_panel_sides"], {"status", "interfaces"}, "reference_points_and_panel_sides", STOP_PANEL_SIDE_UNPROVEN)
    if data["status"] != "SEALED" or type(data["interfaces"]) is not list or not data["interfaces"]:
        _fail(STOP_PANEL_SIDE_UNPROVEN, "panel interface ledger is not sealed")
    interface_ids: set[str] = set()
    panel_ids: set[str] = set()
    normalized = []
    domain_vertices = {tuple(point) for point in xy["domain"]["outline"]}
    for interface in data["interfaces"]:
        item = _keys(interface, {"id", "unit", "surface_vertices", "normal", "positive_material", "negative_material", "panels", "artifact"}, "interface", STOP_PANEL_SIDE_UNPROVEN)
        ident = _str(item["id"], "interface.id", STOP_PANEL_SIDE_UNPROVEN)
        if ident in interface_ids:
            _fail(STOP_PANEL_SIDE_UNPROVEN, f"duplicate interface: {ident}")
        interface_ids.add(ident)
        unit = _unit(item["unit"], "interface.unit", STOP_PANEL_SIDE_UNPROVEN)
        vertices = [_point(point, "surface vertex", 3, STOP_PANEL_SIDE_UNPROVEN) for point in item["surface_vertices"]]
        if len(vertices) < 3 or len(set(vertices)) != len(vertices) or any((point[0], point[1]) not in domain_vertices for point in vertices) or len({point[2] for point in vertices}) != 1:
            _fail(STOP_PANEL_SIDE_UNPROVEN, "surface vertices are not D104-bound")
        surface_area = 0.5 * sum(
            vertices[index][0] * vertices[(index + 1) % len(vertices)][1]
            - vertices[(index + 1) % len(vertices)][0] * vertices[index][1]
            for index in range(len(vertices))
        )
        if abs(surface_area) <= _EPS:
            _fail(STOP_PANEL_SIDE_UNPROVEN, "surface vertices have no planar area")
        normal = _point(item["normal"], "interface normal", 3, STOP_PANEL_SIDE_UNPROVEN)
        norm = math.sqrt(sum(value * value for value in normal))
        if norm <= _EPS or not math.isclose(abs(normal[2]), norm, rel_tol=1e-9, abs_tol=1e-9) or math.copysign(1.0, normal[2]) != math.copysign(1.0, surface_area):
            _fail(STOP_PANEL_SIDE_UNPROVEN, "interface normal is not surface-consistent")
        positive = _keys(item["positive_material"], {"id", "artifact"}, "positive material", STOP_PANEL_SIDE_UNPROVEN)
        negative = _keys(item["negative_material"], {"id", "artifact"}, "negative material", STOP_PANEL_SIDE_UNPROVEN)
        positive_id = _material(positive["id"], "positive material.id", materials, STOP_PANEL_SIDE_UNPROVEN)
        negative_id = _material(negative["id"], "negative material.id", materials, STOP_PANEL_SIDE_UNPROVEN)
        if positive_id == negative_id:
            _fail(STOP_PANEL_SIDE_UNPROVEN, "positive and negative materials are not distinct")
        positive_artifact = _artifact(positive["artifact"], {"id": positive_id}, seen, STOP_PANEL_SIDE_UNPROVEN)
        negative_artifact = _artifact(negative["artifact"], {"id": negative_id}, seen, STOP_PANEL_SIDE_UNPROVEN)
        panels = item["panels"]
        if type(panels) is not list or not panels:
            _fail(STOP_PANEL_SIDE_UNPROVEN, "interface has no panels")
        panel_rows = []
        origin = vertices[0]
        signs: set[tuple[int, int]] = set()
        for panel in panels:
            row = _keys(panel, {"id", "positive_reference_point", "negative_reference_point", "positive_signed_distance", "negative_signed_distance", "artifact"}, "panel", STOP_PANEL_SIDE_UNPROVEN)
            panel_id = _str(row["id"], "panel.id", STOP_PANEL_SIDE_UNPROVEN)
            if panel_id in panel_ids:
                _fail(STOP_PANEL_SIDE_UNPROVEN, f"duplicate panel: {panel_id}")
            panel_ids.add(panel_id)
            pos = _point(row["positive_reference_point"], "positive reference point", 3, STOP_PANEL_SIDE_UNPROVEN)
            neg = _point(row["negative_reference_point"], "negative reference point", 3, STOP_PANEL_SIDE_UNPROVEN)
            pos_distance = _num(row["positive_signed_distance"], "positive signed distance", STOP_PANEL_SIDE_UNPROVEN)
            neg_distance = _num(row["negative_signed_distance"], "negative signed distance", STOP_PANEL_SIDE_UNPROVEN)
            calculated_pos = sum((pos[index] - origin[index]) * normal[index] for index in range(3)) / norm
            calculated_neg = sum((neg[index] - origin[index]) * normal[index] for index in range(3)) / norm
            if pos_distance <= _EPS or neg_distance >= -_EPS or not math.isclose(pos_distance, calculated_pos, rel_tol=1e-9, abs_tol=1e-9) or not math.isclose(neg_distance, calculated_neg, rel_tol=1e-9, abs_tol=1e-9):
                _fail(STOP_PANEL_SIDE_UNPROVEN, f"panel references are not strict opposite sides: {panel_id}")
            signs.add((1 if calculated_pos > 0 else -1, 1 if calculated_neg > 0 else -1))
            panel_core = {"id": panel_id, "positive_reference_point": list(pos), "negative_reference_point": list(neg), "positive_signed_distance": pos_distance, "negative_signed_distance": neg_distance}
            panel_artifact = _artifact(row["artifact"], panel_core, seen, STOP_PANEL_SIDE_UNPROVEN)
            panel_rows.append({**panel_core, "artifact": panel_artifact})
        if signs != {(1, -1)}:
            _fail(STOP_PANEL_SIDE_UNPROVEN, f"panel side assignment is inconsistent: {ident}")
        core = {"id": ident, "unit": unit, "surface_vertices": [list(point) for point in vertices], "normal": list(normal), "positive_material": {"id": positive_id}, "negative_material": {"id": negative_id}, "panels": panel_rows}
        interface_artifact = _artifact(item["artifact"], core, seen, STOP_PANEL_SIDE_UNPROVEN)
        normalized.append({**core, "positive_material": {"id": positive_id, "artifact": positive_artifact}, "negative_material": {"id": negative_id, "artifact": negative_artifact}, "artifact": interface_artifact})
    return {"status": STATIC_CONTRACT_VALIDATED, "interfaces": normalized}


def _closure(records: dict[str, Any], basis: list[dict[str, Any]], xy: dict[str, Any], seen: dict[str, dict[str, Any]], materials: set[str]) -> dict[str, Any]:
    data = _keys(records["outer_truncation_and_closure"], {"status", "basis", "domain_artifact", "coverage_ordinals", "exterior_media", "infinity_target", "artificial_walls", "conductor_groups"}, "outer_truncation_and_closure", STOP_CLOSURE_BASIS_CONFLICT)
    if data["status"] != "SEALED":
        _fail(STOP_CLOSURE_BASIS_CONFLICT, "closure status is not SEALED")
    if data["basis"] != basis or data["coverage_ordinals"] != list(ORDINALS):
        _fail(STOP_CLOSURE_BASIS_CONFLICT, "closure basis/coverage differs from D104")
    domain_artifact = _keys(data["domain_artifact"], {"path", "size_bytes", "sha256"}, "closure domain artifact", STOP_CLOSURE_BASIS_CONFLICT)
    if domain_artifact != xy["domain_artifact"]:
        _fail(STOP_CLOSURE_BASIS_CONFLICT, "closure domain artifact is not source-bound")
    media = _keys(data["exterior_media"], {"top", "bottom", "domain"}, "exterior media", STOP_CLOSURE_BASIS_CONFLICT)
    normalized_media = {}
    for side in ("top", "bottom", "domain"):
        row = _keys(media[side], {"domain_id", "material", "source_record_id", "coverage_ordinals", "artifact"}, f"exterior {side}", STOP_CLOSURE_BASIS_CONFLICT)
        if row["domain_id"] != xy["domain"]["id"]:
            _fail(STOP_CLOSURE_BASIS_CONFLICT, f"exterior {side} domain is not source-bound")
        material = _material(row["material"], f"exterior {side}.material", materials, STOP_CLOSURE_BASIS_CONFLICT)
        source_id = _str(row["source_record_id"], f"exterior {side}.source_record_id", STOP_CLOSURE_BASIS_CONFLICT)
        if source_id != f"synthetic:air-{side}" or material != "AIR":
            _fail(STOP_CLOSURE_BASIS_CONFLICT, f"exterior {side} source identity is not pinned")
        coverage = _coverage(row["coverage_ordinals"], f"exterior {side}.coverage", full=True, status=STOP_CLOSURE_BASIS_CONFLICT)
        artifact = _artifact(row["artifact"], {"domain_id": xy["domain"]["id"], "material": material, "source_record_id": source_id, "coverage_ordinals": coverage}, seen, STOP_CLOSURE_BASIS_CONFLICT)
        normalized_media[side] = {"domain_id": xy["domain"]["id"], "material": material, "source_record_id": source_id, "coverage_ordinals": coverage, "artifact": artifact}
    if data["infinity_target"] != "NONE" or data["artificial_walls"] != [] or data["conductor_groups"] != []:
        _fail(STOP_CLOSURE_BASIS_CONFLICT, "closure adds walls, merges basis, or ties infinity")
    return {"status": STATIC_CONTRACT_VALIDATED, "basis": basis, "domain_artifact": xy["domain_artifact"], "coverage_ordinals": list(ORDINALS), "exterior_media": normalized_media, "infinity_target": "NONE", "artificial_walls": [], "conductor_groups": []}


def _transform(records: dict[str, Any], basis: list[dict[str, Any]], seen: dict[str, dict[str, Any]]) -> dict[str, Any]:
    data = _keys(records["absolute_source_z_transform"], {"status", "unit", "scale", "offset", "anchor"}, "absolute_source_z_transform", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    if data["status"] != "SEALED":
        _fail(STOP_ABSOLUTE_SOURCE_Z_UNSEALED, "absolute-z status is not SEALED")
    unit = _unit(data["unit"], "absolute-z unit", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    scale = _num(data["scale"], "absolute-z scale", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    offset = _num(data["offset"], "absolute-z offset", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    if abs(scale) <= _EPS:
        _fail(STOP_ABSOLUTE_SOURCE_Z_UNSEALED, "absolute-z scale is zero")
    anchor = _keys(data["anchor"], {"source_ordinal", "receipt_z", "absolute_z", "artifact"}, "absolute-z anchor", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    ordinal = _int(anchor["source_ordinal"], "absolute-z source ordinal", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    if ordinal not in {row["ordinal"] for row in basis}:
        _fail(STOP_ABSOLUTE_SOURCE_Z_UNSEALED, "absolute-z source ordinal is unknown")
    receipt_z = _num(anchor["receipt_z"], "receipt-relative z", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    absolute_z = _num(anchor["absolute_z"], "absolute z", STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    if not math.isclose(absolute_z, scale * receipt_z + offset, rel_tol=1e-9, abs_tol=1e-9):
        _fail(STOP_ABSOLUTE_SOURCE_Z_UNSEALED, "absolute-z mapping is inconsistent")
    artifact = _artifact(anchor["artifact"], {"source_ordinal": ordinal, "receipt_z": receipt_z, "absolute_z": absolute_z}, seen, STOP_ABSOLUTE_SOURCE_Z_UNSEALED)
    return {"status": STATIC_CONTRACT_VALIDATED, "unit": unit, "scale": scale, "offset": offset, "anchor": {"source_ordinal": ordinal, "receipt_z": receipt_z, "absolute_z": absolute_z, "artifact": artifact}}


def _base(status: str, message: str | None, predecessor: dict[str, Any] | None) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "program": PROGRAM, "version": VERSION, "scope": SCOPE, "status": status, "material_authority_status": "PARTIAL", "production_validation": False, "promotion": "NONE", "overall_status": STOP_NUMERICAL_EXECUTION, "predecessor_statuses": predecessor, "field_statuses": {field: STATIC_CONTRACT_VALIDATED if status == PASS_SYNTHETIC_CONTRACT else "STOP_UNSEALED" for field in FIELDS}, "ambiguities": [] if message is None else [message], "exact_16_conductor_basis_unchanged": status == PASS_SYNTHETIC_CONTRACT, "conductor_basis_count": 16 if status == PASS_SYNTHETIC_CONTRACT else 0, "infinity_tied_to_dgnd": False, "deterministic_artifacts_bound": status == PASS_SYNTHETIC_CONTRACT, "executed_numerical_components": [], "does_not_authorize": ["FasterCap", "Triangle", "C1", "solver", "PowerSI", "numerical oracle"], "artifacts": {}}


def audit_material_authority(records: dict[str, Any]) -> dict[str, Any]:
    """Audit one exact synthetic contract; return PASS_SYNTHETIC_CONTRACT or one STOP."""

    predecessor: dict[str, Any] | None = None
    try:
        expected = {"evidence", "conductor_basis", *FIELDS, "replay"}
        records = _keys(records, expected, "synthetic input")
        parsed = _evidence(records)
        predecessor = parsed["wp2_predecessor_receipt"]
        seen: dict[str, dict[str, Any]] = {}
        d104 = parsed["d104"]
        derived_basis = _derived_basis(d104, seen)
        if type(records["conductor_basis"]) is not list or records["conductor_basis"] != derived_basis:
            _fail(STOP_CLOSURE_BASIS_CONFLICT, "submitted basis differs from bound D104 basis")
        xy = _xy(records, d104, seen)
        d103_materials = set(parsed["d103"]["materials"])
        fill = _fill(records, xy, d104, seen, d103_materials)
        panel = _panel(records, xy, seen, d103_materials)
        closure = _closure(records, derived_basis, xy, seen, d103_materials)
        transform = _transform(records, derived_basis, seen)
        if sum(value["size_bytes"] for value in seen.values()) > MAX_TOTAL_ARTIFACT_BYTES:
            _fail(STOP_ARTIFACT_CAP_EXCEEDED, "aggregate artifact cap exceeded")
        receipt = _base(PASS_SYNTHETIC_CONTRACT, None, predecessor)
        receipt.update({"evidence_ledger": {name: {key: records["evidence"][name][key] for key in ("path", "size_bytes", "sha256")} for name in sorted(records["evidence"])}, "conductor_basis": derived_basis, "xy_dielectric_partitions": xy, "conductor_layer_void_fill": fill, "reference_points_and_panel_sides": panel, "outer_truncation_and_closure": closure, "absolute_source_z_transform": transform, "artifacts": {key: seen[key] for key in sorted(seen)}, "deterministic_artifacts_bound": True, "limits": {"per_artifact_bytes": MAX_ARTIFACT_BYTES, "aggregate_artifact_bytes": MAX_TOTAL_ARTIFACT_BYTES}})
        replay = _keys(records["replay"], {"expected_receipt_sha256"}, "replay", STOP_DETERMINISTIC_REPLAY_MISMATCH)
        if replay["expected_receipt_sha256"] is not None:
            expected_hash = _hash(replay["expected_receipt_sha256"], "replay.expected_receipt_sha256", STOP_DETERMINISTIC_REPLAY_MISMATCH)
            if expected_hash != sha256(_canonical(receipt)).hexdigest():
                _fail(STOP_DETERMINISTIC_REPLAY_MISMATCH, "receipt replay hash differs")
        return receipt
    except Refusal as exc:
        return _base(exc.status, exc.message, predecessor)


def synthetic_input() -> dict[str, Any]:
    """Return the code-bound, complete synthetic contract (no files are read)."""

    evidence = {}
    for name, content in _EXPECTED_EVIDENCE.items():
        encoded = content.encode("utf-8")
        evidence[name] = {"path": _EVIDENCE_PATHS[name], "content": content, "size_bytes": len(encoded), "sha256": sha256(encoded).hexdigest()}
    basis = [{"ordinal": ordinal, "conductor_id": f"conductor-{ordinal}", "layer": layer, "island_id": f"island-{ordinal}"} for ordinal, layer in zip(ORDINALS, LAYERS)]
    domain = _domain()
    domain_bytes = _canonical(domain)
    domain_row = {**domain, "artifact": {"path": "xy/domain.json", "size_bytes": len(domain_bytes), "sha256": sha256(domain_bytes).hexdigest()}}
    cells = _cells()
    coverage = []
    for cell in cells:
        row = {"ordinal": cell["ordinal"], "unit": "um", "source_cell_sha256": cell["artifact"]["sha256"], "geometry": cell["geometry"]}
        encoded = _canonical(row)
        coverage.append({**row, "artifact": {"path": f"xy/partition-{cell['ordinal']}.json", "size_bytes": len(encoded), "sha256": sha256(encoded).hexdigest()}})
    full = list(ORDINALS)
    default = {"domain_id": "domain-0", "material": "ABF-GL102", "explicit": True, "coverage_ordinals": full}
    default_bytes = _canonical(default)
    default["artifact"] = {"path": "fill/default.json", "size_bytes": len(default_bytes), "sha256": sha256(default_bytes).hexdigest()}
    layer_fills = []
    for layer in FILL_LAYERS:
        row = {"domain_id": "domain-0", "layer": layer, "material": "ABF-GL102", "explicit": True, "coverage_ordinals": full, "aperture_exceptions": []}
        encoded = _canonical(row)
        row["artifact"] = {"path": f"fill/{layer}.json", "size_bytes": len(encoded), "sha256": sha256(encoded).hexdigest()}
        layer_fills.append(row)
    exception = {"id": "aperture-258", "layer": "L28", "material": "EL190T", "explicit": True, "coverage_ordinals": [258], "geometry": cells[0]["geometry"]}
    exception_bytes = _canonical(exception)
    exception["artifact"] = {"path": "fill/aperture-258.json", "size_bytes": len(exception_bytes), "sha256": sha256(exception_bytes).hexdigest()}
    interfaces = []
    for index, z in enumerate((0.0, 2.0)):
        positive = {"id": "ABF-GL102"}
        positive_bytes = _canonical(positive)
        positive["artifact"] = {"path": f"panel/positive-{index}.json", "size_bytes": len(positive_bytes), "sha256": sha256(positive_bytes).hexdigest()}
        negative = {"id": "EL190T"}
        negative_bytes = _canonical(negative)
        negative["artifact"] = {"path": f"panel/negative-{index}.json", "size_bytes": len(negative_bytes), "sha256": sha256(negative_bytes).hexdigest()}
        panels = [{"id": f"panel-{index}", "positive_reference_point": [0.0, 0.0, z + 1.0], "negative_reference_point": [0.0, 0.0, z - 1.0], "positive_signed_distance": 1.0, "negative_signed_distance": -1.0}]
        panel_bytes = _canonical(panels[0])
        panels[0]["artifact"] = {"path": f"panel/panel-{index}.json", "size_bytes": len(panel_bytes), "sha256": sha256(panel_bytes).hexdigest()}
        interface = {"id": f"interface-{index}", "unit": "um", "surface_vertices": [[0.0, 0.0, z], [16.0, 0.0, z], [16.0, 1.0, z]], "normal": [0.0, 0.0, 1.0], "positive_material": positive, "negative_material": negative, "panels": panels}
        interface_core = {**interface, "positive_material": {"id": "ABF-GL102"}, "negative_material": {"id": "EL190T"}}
        interface_bytes = _canonical(interface_core)
        interface["artifact"] = {"path": f"panel/interface-{index}.json", "size_bytes": len(interface_bytes), "sha256": sha256(interface_bytes).hexdigest()}
        interfaces.append(interface)
    media = {}
    for side in ("top", "bottom", "domain"):
        row = {"domain_id": "domain-0", "material": "AIR", "source_record_id": f"synthetic:air-{side}", "coverage_ordinals": full}
        encoded = _canonical(row)
        row["artifact"] = {"path": f"closure/{side}-air.json", "size_bytes": len(encoded), "sha256": sha256(encoded).hexdigest()}
        media[side] = row
    anchor = {"source_ordinal": 258, "receipt_z": 250.0, "absolute_z": 750.0}
    anchor_bytes = _canonical(anchor)
    anchor["artifact"] = {"path": "z/anchor-258.json", "size_bytes": len(anchor_bytes), "sha256": sha256(anchor_bytes).hexdigest()}
    return {"evidence": evidence, "conductor_basis": basis, "xy_dielectric_partitions": {"status": "SEALED", "domain": domain_row, "coverage": coverage}, "conductor_layer_void_fill": {"status": "SEALED", "domain_id": "domain-0", "coverage_ordinals": full, "default_fill": default, "layer_fills": layer_fills, "aperture_exceptions": [exception]}, "reference_points_and_panel_sides": {"status": "SEALED", "interfaces": interfaces}, "outer_truncation_and_closure": {"status": "SEALED", "basis": basis, "domain_artifact": domain_row["artifact"], "coverage_ordinals": full, "exterior_media": media, "infinity_target": "NONE", "artificial_walls": [], "conductor_groups": []}, "absolute_source_z_transform": {"status": "SEALED", "unit": "um", "scale": -1.0, "offset": 1000.0, "anchor": anchor}, "replay": {"expected_receipt_sha256": None}}


def _write_atomic_no_clobber(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir() or os.path.lexists(path):
        _fail(STOP_OUTPUT_EXISTS, "output path must be absent in an existing directory")
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            _fail(STOP_OUTPUT_EXISTS, "output path appeared during write")
        os.link(temporary, path)
        temporary.unlink()
        temporary = None
        created = path.stat()
        with path.open("rb") as stream:
            written = stream.read()
        current = path.stat()
        if (created.st_dev, created.st_ino) != (current.st_dev, current.st_ino) or written != payload or sha256(written).hexdigest() != sha256(payload).hexdigest():
            _fail(STOP_DETERMINISTIC_REPLAY_MISMATCH, "receipt reread verification failed; foreign output left untouched")
    except FileExistsError as exc:
        _fail(STOP_OUTPUT_EXISTS, "output path appeared during write")
    except OSError as exc:
        _fail(STOP_DETERMINISTIC_REPLAY_MISMATCH, f"receipt reread failed; foreign output left untouched: {exc}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract_material_authority(records: dict[str, Any], output_json: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    if output_json is not None:
        output = Path(output_json).resolve()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise Refusal(STOP_OUTPUT_EXISTS, "output path must be absent in an existing directory")
    receipt = audit_material_authority(records)
    if output_json is None:
        return receipt
    if receipt["status"] != PASS_SYNTHETIC_CONTRACT:
        raise Refusal(receipt["status"], "synthetic contract is not promotable")
    _write_atomic_no_clobber(output, _canonical(receipt))
    return receipt


def _self_check() -> dict[str, Any]:
    receipt = audit_material_authority(synthetic_input())
    assert receipt["status"] == PASS_SYNTHETIC_CONTRACT
    assert receipt["production_validation"] is False and receipt["promotion"] == "NONE"
    assert all(value == STATIC_CONTRACT_VALIDATED for value in receipt["field_statuses"].values())
    assert receipt["absolute_source_z_transform"]["scale"] == -1.0
    assert receipt["executed_numerical_components"] == []
    return {"status": PASS_SYNTHETIC_CONTRACT, "artifact_count": len(receipt["artifacts"]), "output_written": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} D117 WP2 material authority")
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    parser.add_argument("--self-check", action="store_true", help="run the synthetic-only contract")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.self_check:
        if args.input is not None or args.output is not None:
            parser.error("--self-check cannot be combined with --input/--output")
        result = _self_check()
        print(f"{PRODUCT} v{VERSION} {result['status']} self-check PASS")
        return 0
    print(f"{PRODUCT} v{VERSION} REFUSAL: {STOP_NUMERICAL_EXECUTION}; external approval contract required", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PRODUCT", "PROGRAM", "VERSION", "SCHEMA_VERSION", "SCOPE", "PASS_SYNTHETIC_CONTRACT", "STATIC_CONTRACT_VALIDATED", "FIELDS", "STOP_XY_PARTITION_UNSEALED", "STOP_VOID_FILL_AMBIGUOUS", "STOP_ABSOLUTE_SOURCE_Z_UNSEALED", "STOP_PANEL_SIDE_UNPROVEN", "STOP_CLOSURE_BASIS_CONFLICT", "STOP_INPUT_IDENTITY_MISMATCH", "STOP_ARTIFACT_CAP_EXCEEDED", "STOP_DETERMINISTIC_REPLAY_MISMATCH", "STOP_OUTPUT_EXISTS", "STOP_NUMERICAL_EXECUTION", "MAX_ARTIFACT_BYTES", "MAX_TOTAL_ARTIFACT_BYTES", "Refusal", "audit_material_authority", "synthetic_input", "extract_material_authority", "_strict_json", "_self_check", "main"]
