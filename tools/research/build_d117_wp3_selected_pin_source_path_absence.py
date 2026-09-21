"""Build the bounded D117 WP3 selected-pin source-path absence certificate.

The certificate is deliberately a preparation-only, source-record result.  It
does not scan the production SPD graph, infer geometry, or run a solver.  The
caller supplies a JSON evidence file containing *pointers* (offset, end, and
SHA-256) into a raw SPD file.  The raw file is opened once, its handle is
identity-verified, and pointed records are consumed through that handle; the
three lineage receipts are independently sealed by path/size/hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SCHEMA_VERSION = "d117-wp3-selected-pin-source-path-absence-v1"

STOP_NOT_REPRESENTED = "STOP_NOT_REPRESENTED"
STOP_INPUT_IDENTITY_MISMATCH = "STOP_INPUT_IDENTITY_MISMATCH"
STOP_RECORD_MISMATCH = "STOP_RECORD_MISMATCH"
STOP_PATH_MISMATCH = "STOP_PATH_MISMATCH"
STOP_BRANCH = "STOP_BRANCH_OR_CYCLE"
STOP_TARGET_LAYER = "STOP_TARGET_LAYER_INCIDENCE"
STOP_PADSTACK = "STOP_PADSTACK_MISMATCH"
STOP_BOUNDS = "STOP_RECORD_BOUNDS"
STOP_OVERLAP = "STOP_RECORD_OVERLAP"
STOP_OUTPUT_EXISTS = "STOP_OUTPUT_EXISTS"
STOP_RECEIPT_LINEAGE = "STOP_RECEIPT_LINEAGE_MISMATCH"

RAW_IDENTITY = {
    "path": r"D:\S4LB002-2Para_260729_1_injected.spd",
    "size_bytes": 1_116_717_287,
    "sha256": "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2",
}
D115B_IDENTITY = {
    "path": r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\d115b_source_plane_ownership_materialization_receipt.json",
    "size_bytes": 514_208,
    "sha256": "c69dce134ca02ff263b75f5df930106a7d8d75d05cccf2acd13b7d9b1919aa49",
}
D115C_IDENTITY = {
    "path": r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02\d115c_source_local_port_window_receipt.json",
    "size_bytes": 1_063_056,
    "sha256": "0c8daed46719b199ee50b1ec9b94dd5cac0fdedecbc7b58668b7665b5e9a2a80",
}
WP3_05_IDENTITY = {
    "path": r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp3-selected-via-source-authority-05\d117_wp3_selected_via_source_authority_receipt.json",
    "size_bytes": 1_321_085,
    "sha256": "5800f801467680af8e21f8638650e238df0394296d3aa8d3caf6084a39183371",
}
TARGET_LAYERS = ("Signal$L29(DGND)", "Signal$L30(OTHER_POWER1)")
PINS = (
    {
        "pin_id": "SITE0:20612",
        "via_id": "Via1360630",
        "source_node_id": "Node73624",
        "source_node_token": "Node73624!!20612",
        "lower_node_id": "Node2452705",
        "lower_layer": "Signal$L02(DGND)",
        "net": "DGND",
        "target_layer": "Signal$L29(DGND)",
        "terminal_layer": "Signal$L18(DGND)",
        "node_count": 21,
        "edge_count": 20,
    },
    {
        "pin_id": "SITE0:19973",
        "via_id": "Via1468557",
        "source_node_id": "Node79348",
        "source_node_token": "Node79348!!19973",
        "lower_node_id": "Node2543245",
        "lower_layer": "Signal$L02(DGND)",
        "net": "DGND",
        "target_layer": "Signal$L29(DGND)",
        "terminal_layer": "Signal$L20(DGND)",
        "node_count": 23,
        "edge_count": 22,
    },
)
_PIN_BY_ID = {row["pin_id"]: row for row in PINS}
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

# These expressions mirror the bounded logical-record grammar in
# ``spd_decap_pi._core.io.spd`` while retaining endpoint suffix/net captures.
_NODE_RE = re.compile(
    rb"^Node(?P<id>[0-9]+)(?:!!(?P<pin>[0-9]+))?::(?P<net>[^\s:]+)\s+"
    rb"X\s*=\s*(?P<x>[+\-0-9.eE]+)(?P<x_unit>mm|um)\s+"
    rb"Y\s*=\s*(?P<y>[+\-0-9.eE]+)(?P<y_unit>mm|um)(?P<tail>[^\r\n]*)$",
    re.IGNORECASE,
)
_VIA_RE = re.compile(
    rb"^(?P<id>Via[^\r\n:]*)::(?P<net>[^\s:]+)\s+"
    rb"UpperNode\s*=\s*(?P<upper>Node[0-9]+(?:!![0-9]+)?)(?:::(?P<upper_net>[^\s:]+))?\s+"
    rb"LowerNode\s*=\s*(?P<lower>Node[0-9]+(?:!![0-9]+)?)(?:::(?P<lower_net>[^\s:]+))?\s+"
    rb"PadStack\s*=\s*(?P<padstack>[^\s]+)(?P<tail>[^\r\n]*)$",
    re.IGNORECASE,
)
_TRACE_RE = re.compile(
    rb"^(?P<id>Trace[^\r\n:]*)::(?P<net>[^\s:]+)\s+"
    rb"(?:Thermal\s+)?StartingNode\s*=\s*(?P<start>Node[0-9]+(?:!![0-9]+)?)(?:::(?P<start_net>[^\s:]+))?\s+"
    rb"EndingNode\s*=\s*(?P<end>Node[0-9]+(?:!![0-9]+)?)(?:::(?P<end_net>[^\s:]+))?(?P<tail>[^\r\n]*)$",
    re.IGNORECASE,
)
_TRACE_CONTINUATION_RE = re.compile(
    rb"^[ \t]*\+[ \t]*Width\s*=\s*\S+[ \t]*$", re.IGNORECASE
)
_TRACE_PRIMARY_TAIL_RE = re.compile(
    rb"^[ \t]*(?:Width\s*=\s*\S+)?[ \t]*$", re.IGNORECASE
)
_PADSTACK_RE = re.compile(
    rb"^\s*\.PadStackDef\s+(?P<id>[^\s]+)(?P<tail>[^\r\n]*)$",
    re.IGNORECASE,
)
_PADDEF_RE = re.compile(
    rb"^\s*\.PadDef\s+(?P<layer>[^\s]+)(?P<tail>[^\r\n]*)$",
    re.IGNORECASE,
)
_END_PADDEF_RE = re.compile(rb"^\s*\.EndPadDef\s*$", re.IGNORECASE)
_END_PADSTACK_RE = re.compile(rb"^\s*\.EndPadStackDef\s*$", re.IGNORECASE)
_LAYER_RE = re.compile(rb"\bLayer\s*=\s*(\S+)", re.IGNORECASE)
_PADSTACK_ATTR_RE = re.compile(rb"\bPadStack\s*=\s*(\S+)", re.IGNORECASE)
_MATERIAL_RE = re.compile(rb"\bMaterial\s*=\s*([^\s\r\n]+)", re.IGNORECASE)
_NAMED_DRILL_RE = re.compile(
    rb"^\s*(?:Drill|Hole)\s*=\s*([+\-0-9.eE]+)\s*mm\s*$", re.IGNORECASE
)
_HEADER_DRILL_RE = re.compile(rb"([+\-0-9.eE]+)\s*mm", re.IGNORECASE)

_RECORD_CAPS = {
    "Node": 16 * 1024,
    "Via": 16 * 1024,
    "Trace": 1024 * 1024,
    "PadStack": 1024 * 1024,
}


class Refusal(RuntimeError):
    """A deterministic fail-closed status."""

    def __init__(self, status: str, message: str):
        self.status = status
        self.message = message
        super().__init__(f"{status}: {message}")


def _fail(status: str, message: str) -> None:
    raise Refusal(status, message)


def _strict_json(data: bytes) -> dict[str, Any]:
    """Parse JSON with duplicate-key and non-finite-number rejection."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    def finite(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
            parse_float=finite,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"strict JSON failed: {exc}")
    if type(parsed) is not dict:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "JSON root must be an object")
    return parsed


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
    except (TypeError, ValueError) as exc:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"canonical JSON failed: {exc}")


def _text(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} must be a non-empty string")
    return value


def _sha(value: Any, label: str) -> str:
    digest = _text(value, label).lower()
    if _HEX64.fullmatch(digest) is None:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} is not SHA-256")
    return digest


def _same_path(left: Any, right: Any) -> bool:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
            os.path.abspath(os.fspath(right))
        )
    except (TypeError, ValueError):
        return False


def _sealed_identity(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} identity is not an object")
    path = _text(value.get("path"), f"{label}.path")
    size = value.get("size_bytes")
    if type(size) is not int or size <= 0:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label}.size_bytes is invalid")
    digest = _sha(value.get("sha256"), f"{label}.sha256")
    return {"path": path, "size_bytes": size, "sha256": digest}


def _stream_identity(
    path_value: Path | str,
    expected_value: Mapping[str, Any],
    label: str,
    *,
    parse_json: bool = False,
    stream: BinaryIO | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    expected = _sealed_identity(expected_value, label)
    try:
        path = Path(path_value)
    except (TypeError, ValueError) as exc:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} path is invalid: {exc}")
    if not _same_path(path, expected["path"]):
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} path differs from sealed identity")
    owns_stream = stream is None
    try:
        before_path = path.stat()
        if not path.is_file() or path.is_symlink():
            _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} is not a regular file")
        if stream is None:
            stream = path.open("rb")
        before_handle = os.fstat(stream.fileno())
        if (
            before_handle.st_dev != before_path.st_dev
            or before_handle.st_ino != before_path.st_ino
            or before_handle.st_size != before_path.st_size
            or before_handle.st_size != expected["size_bytes"]
        ):
            _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} handle identity/size differs")
        digest = hashlib.sha256()
        size = 0
        chunks: list[bytes] = []
        stream.seek(0)
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if parse_json:
                if size > 32 * 1024 * 1024:
                    _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} receipt is too large")
                chunks.append(chunk)
        after_handle = os.fstat(stream.fileno())
        after_path = path.stat()
    except Refusal:
        raise
    except (AttributeError, ValueError, OSError) as exc:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} cannot be read: {exc}")
    finally:
        if owns_stream and stream is not None:
            stream.close()
    actual_sha = digest.hexdigest()
    if (
        before_handle.st_dev != after_handle.st_dev
        or before_handle.st_ino != after_handle.st_ino
        or before_handle.st_size != after_handle.st_size
        or before_handle.st_dev != after_path.st_dev
        or before_handle.st_ino != after_path.st_ino
        or before_handle.st_size != after_path.st_size
        or size != expected["size_bytes"]
        or actual_sha != expected["sha256"]
    ):
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"{label} size/hash differs from sealed identity")
    parsed = _strict_json(b"".join(chunks)) if parse_json else None
    # Return the verified identity, not a caller-provided identity object.
    return dict(expected), parsed


def _check_receipt_header(
    receipt: Mapping[str, Any],
    label: str,
    schema: str,
    status: str,
    schema_key: str,
) -> str:
    if receipt.get("product") != PRODUCT or receipt.get("version") != VERSION:
        _fail(STOP_RECEIPT_LINEAGE, f"{label} product/version differs")
    actual_schema = receipt.get(schema_key)
    if actual_schema != schema or receipt.get("status") != status:
        _fail(STOP_RECEIPT_LINEAGE, f"{label} schema/status differs")
    if "code" in receipt and receipt.get("code") != status:
        _fail(STOP_RECEIPT_LINEAGE, f"{label} code/status differs")
    return actual_schema


def _receipt_inputs(receipt: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    inputs = receipt.get("inputs")
    if not isinstance(inputs, Mapping):
        _fail(STOP_RECEIPT_LINEAGE, f"{label}.inputs is absent")
    return inputs


def _verify_embedded_identity(
    value: Any,
    expected: Mapping[str, Any],
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        _fail(STOP_RECEIPT_LINEAGE, f"{label} identity is absent")
    path = value.get("path")
    size = value.get("size_bytes")
    digest = value.get("sha256")
    if not _same_path(path, expected["path"]) or size != expected["size_bytes"] or digest != expected["sha256"]:
        _fail(STOP_RECEIPT_LINEAGE, f"{label} identity differs")


def _false_scope(value: Any, label: str) -> None:
    if not isinstance(value, Mapping):
        _fail(STOP_RECEIPT_LINEAGE, f"{label} scope is absent")
    for name in (
        "barrel_proven",
        "antipad_proven",
        "land_proven",
        "intermediate_access_proven",
        "l29_l30_physical_pad_proven",
        "three_dimensional_geometry_proven",
    ):
        if value.get(name) is not False:
            _fail(STOP_RECEIPT_LINEAGE, f"{label}.{name} is not false")


def _rows(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        _fail(STOP_RECEIPT_LINEAGE, f"{label} rows are not objects")
    return list(value)


def _target_rows(rows: list[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        pin_id = row.get("pin_id")
        if type(pin_id) is not str or pin_id not in _PIN_BY_ID:
            continue
        if pin_id in result:
            _fail(STOP_RECEIPT_LINEAGE, f"{label} duplicates {pin_id}")
        result[pin_id] = row
    if set(result) != set(_PIN_BY_ID):
        _fail(STOP_RECEIPT_LINEAGE, f"{label} does not contain both target pins")
    return result


_AUTHORITY_KEYS = (
    "terminal_id",
    "pin_id",
    "via_record_id",
    "source_offset",
    "source_end",
    "source_record_sha256",
    "net",
    "selected_plane_layer",
    "source_endpoint_node_id",
    "source_endpoint_layer",
    "opposite_endpoint_node_id",
    "opposite_endpoint_layer",
    "upper_node_id",
    "upper_layer",
    "lower_node_id",
    "lower_layer",
    "padstack_id",
)

# Complete selected terminal-binding contract shared by D115B, D115C, and
# WP3-05.  ``branch_id`` is deliberately excluded: it is a large census blob,
# not a source-authority semantic field.
_TERMINAL_BINDING_KEYS = (
    "terminal_id",
    "pin_id",
    "via_record_id",
    "source_node_record_id",
    "endpoint_node_id",
    "layer",
    "padstack_id",
    "via_record_required",
    "status",
    "owner_kind",
    "role",
    "rail_id",
    "ordinal",
    "island_id",
    "component_id",
    "finite_vertex_id",
    "finite_edge_id",
    "via_owner_id",
    "paddef_source_record_id",
    "regular_source_record_id",
    "raw_pad_shape_ordinal",
    "raw_pad_shape_sha256",
    "issues_json",
)


def _authority_row(row: Mapping[str, Any], contract: Mapping[str, Any], label: str) -> None:
    if any(key not in row for key in _AUTHORITY_KEYS):
        _fail(STOP_RECEIPT_LINEAGE, f"{label} keys are incomplete")
    expected = {
        "pin_id": contract["pin_id"],
        "via_record_id": f"via:{contract['via_id']}:{contract['net']}",
        "net": contract["net"],
        "selected_plane_layer": contract["target_layer"],
        "source_endpoint_node_id": contract["source_node_id"],
        "source_endpoint_layer": "Signal$TOP",
        "opposite_endpoint_node_id": contract["lower_node_id"],
        "opposite_endpoint_layer": contract["lower_layer"],
        "upper_node_id": contract["source_node_id"],
        "upper_layer": "Signal$TOP",
        "lower_node_id": contract["lower_node_id"],
        "lower_layer": contract["lower_layer"],
        "padstack_id": "DR-0102_60",
    }
    for key, value in expected.items():
        if row.get(key) != value:
            _fail(STOP_RECEIPT_LINEAGE, f"{label}.{key} differs")
    if type(row.get("terminal_id")) is not str or not row["terminal_id"]:
        _fail(STOP_RECEIPT_LINEAGE, f"{label}.terminal_id is invalid")
    if type(row.get("source_offset")) is not int or type(row.get("source_end")) is not int:
        _fail(STOP_RECEIPT_LINEAGE, f"{label} source bounds are invalid")
    _sha(row.get("source_record_sha256"), f"{label}.source_record_sha256")


def _validate_lineage(
    raw: Mapping[str, Any],
    d115b: Mapping[str, Any],
    d115c: Mapping[str, Any],
    wp3: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    _check_receipt_header(
        d115b,
        "D115B",
        "d115b-source-plane-ownership-materialization-receipt-v1",
        "PASS",
        "schema_version",
    )
    _check_receipt_header(
        d115c,
        "D115C",
        "source-local-l29-l30-port-window-receipt-v4",
        "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
        "schema",
    )
    _check_receipt_header(
        wp3,
        "WP3-05",
        "source-local-l29-l30-port-window-receipt-v4",
        "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
        "schema",
    )
    _verify_embedded_identity(d115b.get("source"), identities["raw"], "D115B.source")
    d115c_inputs = _receipt_inputs(d115c, "D115C")
    wp3_inputs = _receipt_inputs(wp3, "WP3-05")
    _verify_embedded_identity(d115c_inputs.get("source"), identities["raw"], "D115C.inputs.source")
    _verify_embedded_identity(wp3_inputs.get("source"), identities["raw"], "WP3-05.inputs.source")
    _verify_embedded_identity(d115c_inputs.get("d115b"), identities["d115b"], "D115C.inputs.d115b")
    _verify_embedded_identity(wp3_inputs.get("d115b"), identities["d115b"], "WP3-05.inputs.d115b")

    b_candidate = d115b.get("candidate")
    if not isinstance(b_candidate, Mapping):
        _fail(STOP_RECEIPT_LINEAGE, "D115B candidate is absent")
    b_rows = _target_rows(_rows(b_candidate.get("terminal_rows"), "D115B.candidate.terminal_rows"), "D115B.candidate.terminal_rows")

    c_rows = _target_rows(_rows(d115c.get("terminal_bindings"), "D115C.terminal_bindings"), "D115C.terminal_bindings")
    w_terminal_rows = _target_rows(_rows(wp3.get("terminal_bindings"), "WP3-05.terminal_bindings"), "WP3-05.terminal_bindings")
    w_authority = wp3.get("selected_via_source_authority")
    if not isinstance(w_authority, Mapping):
        _fail(STOP_RECEIPT_LINEAGE, "WP3-05.selected_via_source_authority is absent")
    if w_authority.get("status") != "PARTIAL":
        _fail(STOP_RECEIPT_LINEAGE, "WP3-05.selected_via_source_authority.status is not PARTIAL")
    w_rows_value = _rows(w_authority.get("rows"), "WP3-05.selected_via_source_authority.rows")
    if w_authority.get("row_count") != len(w_rows_value):
        _fail(STOP_RECEIPT_LINEAGE, "WP3-05.selected_via_source_authority.row_count differs")
    if w_authority.get("target_layer_traversal_proven") is not False:
        _fail(STOP_RECEIPT_LINEAGE, "WP3-05.selected_via_source_authority.target_layer_traversal_proven is not false")
    _false_scope(w_authority.get("scope"), "WP3-05.selected_via_source_authority")
    w_rows = _target_rows(w_rows_value, "WP3-05.selected_via_source_authority.rows")

    lineage: dict[str, dict[str, Any]] = {}
    for pin_id, contract in _PIN_BY_ID.items():
        b_row = b_rows[pin_id]
        if (
            b_row.get("source_node_record_id") != f"node:{contract['source_node_id']}:{contract['net']}"
            or b_row.get("via_record_id") != f"via:{contract['via_id']}:{contract['net']}"
            or b_row.get("endpoint_node_id") != contract["source_node_id"]
            or b_row.get("layer") != contract["target_layer"]
            or b_row.get("padstack_id") != "DR-0102_60"
            or b_row.get("via_record_required") != 1
            or b_row.get("status") != "complete"
        ):
            _fail(STOP_RECEIPT_LINEAGE, f"D115B terminal lineage differs for {pin_id}")
        paddef_id = b_row.get("paddef_source_record_id")
        regular_id = b_row.get("regular_source_record_id")
        if (
            type(paddef_id) is not str
            or not paddef_id.startswith("paddef:DR-0102_60:Signal$TOP:")
            or type(regular_id) is not str
            or not regular_id.startswith("regular:DR-0102_60:Signal$TOP:")
        ):
            _fail(STOP_RECEIPT_LINEAGE, f"D115B PadDef lineage differs for {pin_id}")
        c_row, w_terminal_row, w_row = c_rows[pin_id], w_terminal_rows[pin_id], w_rows[pin_id]
        for key in _TERMINAL_BINDING_KEYS:
            if key not in b_row or key not in c_row or key not in w_terminal_row:
                _fail(STOP_RECEIPT_LINEAGE, f"selected terminal binding contract is incomplete for {pin_id}: {key}")
            expected_value = b_row[key]
            if (
                type(c_row[key]) is not type(expected_value)
                or c_row[key] != expected_value
                or type(w_terminal_row[key]) is not type(expected_value)
                or w_terminal_row[key] != expected_value
            ):
                _fail(STOP_RECEIPT_LINEAGE, f"selected terminal binding differs for {pin_id}: {key}")
        _authority_row(w_row, contract, f"WP3-05 authority {pin_id}")
        if w_row["terminal_id"] != w_terminal_row["terminal_id"]:
            _fail(STOP_RECEIPT_LINEAGE, f"WP3-05 authority terminal binding differs for {pin_id}")
        projection = {
            "source_layer": "Signal$TOP",
            "target_layer": contract["target_layer"],
            "source_record_ids": [
                f"node:{contract['source_node_id']}:{contract['net']}",
                f"via:{contract['via_id']}:{contract['net']}",
                paddef_id,
                regular_id,
            ],
            "promotable_to_target_layer": False,
            "physical_geometry_claimed": False,
        }
        lineage[pin_id] = {"d115b": b_row, "d115c": c_row, "wp3_05": w_row, "projection": projection}
    metadata = {
        "d115b": {"status": d115b["status"], "disposition": d115b.get("disposition")},
        "d115c": {"schema": d115c["schema"], "status": d115c["status"]},
        "wp3_05": {"schema": wp3["schema"], "status": wp3["status"]},
    }
    if metadata["d115b"]["disposition"] != "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED":
        _fail(STOP_RECEIPT_LINEAGE, "D115B disposition is absent")
    return lineage, metadata


def _framed_lines(raw: bytes, kind: str) -> list[bytes]:
    if not raw or not raw.endswith(b"\n"):
        _fail(STOP_RECORD_MISMATCH, f"{kind} is not LF-terminated")
    lines = raw.splitlines(keepends=True)
    if b"".join(lines) != raw:
        _fail(STOP_RECORD_MISMATCH, f"{kind} contains an unframed line")
    bodies: list[bytes] = []
    for line in lines:
        if line.endswith(b"\r\n"):
            bodies.append(line[:-2])
        elif line.endswith(b"\n") and b"\r" not in line[:-1]:
            bodies.append(line[:-1])
        else:
            _fail(STOP_RECORD_MISMATCH, f"{kind} has a bare or partial CRLF")
    return bodies


def _line(raw: bytes, kind: str) -> bytes:
    lines = _framed_lines(raw, kind)
    if len(lines) != 1:
        _fail(STOP_RECORD_MISMATCH, f"{kind} is not one complete physical line")
    return lines[0]


def _node_token_parts(token: str, label: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"(Node[0-9]+)(?:!!([0-9]+))?", token)
    if match is None:
        _fail(STOP_RECORD_MISMATCH, f"{label} is not a Node token")
    return match.group(1), match.group(2)


def _read_record(
    row: Mapping[str, Any],
    kind: str,
    source: BinaryIO,
    raw_size: int,
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(row, Mapping):
        _fail(STOP_RECORD_MISMATCH, f"{kind} row is not an object")
    start, end, _ = _pointer_range(row, kind, raw_size)
    digest = _sha(row.get("source_record_sha256"), f"{kind}.source_record_sha256")
    try:
        if os.fstat(source.fileno()).st_size != raw_size:
            _fail(STOP_BOUNDS, f"{kind} source handle size differs")
        if start:
            source.seek(start - 1)
            if source.read(1) != b"\n":
                _fail(STOP_BOUNDS, f"{kind} source offset is not a record boundary")
        source.seek(start)
        raw = source.read(end - start)
        if len(raw) != end - start:
            _fail(STOP_BOUNDS, f"{kind} source slice is truncated")
        if kind == "Trace" and end < raw_size:
            source.seek(end)
            following = source.readline(_RECORD_CAPS["Trace"] + 1)
            if following.lstrip(b" \t").startswith(b"+"):
                _fail(STOP_BOUNDS, "Trace slice stops before its continuation")
    except Refusal:
        raise
    except (AttributeError, ValueError, OSError) as exc:
        _fail(STOP_RECORD_MISMATCH, f"{kind} source slice cannot be read: {exc}")
    if hashlib.sha256(raw).hexdigest() != digest:
        _fail(STOP_RECORD_MISMATCH, f"{kind} source hash mismatch")
    _framed_lines(raw, kind)
    base = {
        "kind": kind,
        "record_id": _text(row.get("record_id"), f"{kind}.record_id"),
        "source_offset": start,
        "source_end": end,
        "source_record_sha256": digest,
    }
    return base, raw


def _parse_node(row: Mapping[str, Any], source: BinaryIO, raw_size: int, contract: Mapping[str, Any]) -> dict[str, Any]:
    result, raw = _read_record(row, "Node", source, raw_size)
    match = _NODE_RE.fullmatch(_line(raw, "Node"))
    if match is None:
        _fail(STOP_RECORD_MISMATCH, "Node source is not a complete SPD Node record")
    node_id = "Node" + match.group("id").decode("ascii")
    pin_suffix = match.group("pin").decode("ascii") if match.group("pin") is not None else None
    node_token = node_id + (f"!!{pin_suffix}" if pin_suffix is not None else "")
    net = match.group("net").decode("utf-8")
    tail = match.group("tail")
    layer_matches = _LAYER_RE.findall(tail)
    padstack_matches = _PADSTACK_ATTR_RE.findall(tail)
    if len(layer_matches) != 1 or len(padstack_matches) > 1:
        _fail(STOP_RECORD_MISMATCH, "Node Layer/PadStack attributes are incomplete")
    layer = layer_matches[0].decode("utf-8")
    padstack_id = padstack_matches[0].decode("utf-8") if padstack_matches else None
    try:
        x = float(match.group("x"))
        y = float(match.group("y"))
    except (ValueError, OverflowError):
        _fail(STOP_RECORD_MISMATCH, "Node coordinates are invalid")
    if not math.isfinite(x) or not math.isfinite(y):
        _fail(STOP_RECORD_MISMATCH, "Node coordinates are non-finite")
    if match.group("x_unit").lower() == b"um":
        x /= 1000.0
    if match.group("y_unit").lower() == b"um":
        y /= 1000.0
    expected_record_id = f"node:{node_id}:{net}"
    if result["record_id"] != expected_record_id:
        _fail(STOP_RECORD_MISMATCH, "Node canonical record_id differs")
    if net != contract["net"]:
        _fail(STOP_RECORD_MISMATCH, "Node net differs from selected pin")
    incident = row.get("incident_edge_count")
    if type(incident) is not int or incident < 0:
        _fail(STOP_PATH_MISMATCH, "Node incident-edge count is invalid")
    result.update(
        {
            "node_id": node_id,
            "node_token": node_token,
            "pin_suffix": pin_suffix,
            "net": net,
            "x_mm": x,
            "y_mm": y,
            "layer": layer,
            "padstack_id": padstack_id,
            "incident_edge_count": incident,
        }
    )
    return result


def _endpoint_fields(token: str, endpoint_net: str | None, label: str) -> tuple[str, str | None]:
    node_id, suffix = _node_token_parts(token, label)
    if endpoint_net is not None:
        endpoint_net = _text(endpoint_net, f"{label}.net")
    return node_id, suffix


def _parse_edge(
    row: Mapping[str, Any],
    source: BinaryIO,
    raw_size: int,
    nodes: Mapping[str, Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        _fail(STOP_RECORD_MISMATCH, "edge row is not an object")
    edge_kind = row.get("edge_kind")
    if type(edge_kind) is not str or edge_kind.casefold() not in {"via", "trace"}:
        _fail(STOP_RECORD_MISMATCH, "edge kind must be Via or Trace")
    kind = "Via" if edge_kind.casefold() == "via" else "Trace"
    result, raw = _read_record(row, kind, source, raw_size)
    lines = _framed_lines(raw, kind)
    if not lines:
        _fail(STOP_RECORD_MISMATCH, f"{kind} source is empty")
    if kind == "Via" and len(lines) != 1:
        _fail(STOP_RECORD_MISMATCH, "Via source contains extra physical lines")
    primary = lines[0]
    parsed = (_VIA_RE.fullmatch(primary) if kind == "Via" else _TRACE_RE.fullmatch(primary))
    if parsed is None:
        _fail(STOP_RECORD_MISMATCH, f"{kind} source is not a complete SPD record")
    decoded = {name: (value.decode("utf-8") if value is not None else None) for name, value in parsed.groupdict().items()}
    edge_id, net = decoded["id"], decoded["net"]
    if net != contract["net"]:
        _fail(STOP_RECORD_MISMATCH, f"{kind} net differs from selected pin")
    if kind == "Trace":
        if _TRACE_PRIMARY_TAIL_RE.fullmatch(decoded["tail"].encode("utf-8")) is None:
            _fail(STOP_RECORD_MISMATCH, "Trace primary tail is not bounded")
        for continuation in lines[1:]:
            if _TRACE_CONTINUATION_RE.fullmatch(continuation) is None:
                _fail(STOP_RECORD_MISMATCH, "Trace continuation is not framed")
        start_token, end_token = decoded["start"], decoded["end"]
        start_net, end_net = decoded["start_net"], decoded["end_net"]
        start_id, start_suffix = _endpoint_fields(start_token, start_net, "Trace.start")
        end_id, end_suffix = _endpoint_fields(end_token, end_net, "Trace.end")
        if (start_id in nodes and start_token != nodes[start_id]["node_token"]) or (
            end_id in nodes and end_token != nodes[end_id]["node_token"]
        ):
            _fail(STOP_RECORD_MISMATCH, "Trace endpoint token differs from its Node record")
        if start_net is not None and start_net != net or end_net is not None and end_net != net:
            _fail(STOP_RECORD_MISMATCH, "Trace endpoint net annotation differs")
        if result["record_id"] != f"trace:{edge_id}:{net}":
            _fail(STOP_RECORD_MISMATCH, "Trace canonical record_id differs")
        if row.get("trace_id") is not None and row.get("trace_id") != edge_id:
            _fail(STOP_RECORD_MISMATCH, "Trace identity differs from source")
        result.update(
            {
                "edge_kind": "Trace",
                "trace_id": edge_id,
                "from_node_id": start_id,
                "to_node_id": end_id,
                "from_node_token": start_token,
                "to_node_token": end_token,
                "from_pin_suffix": start_suffix,
                "to_pin_suffix": end_suffix,
                "from_endpoint_net": start_net,
                "to_endpoint_net": end_net,
                "tail": decoded["tail"].strip(),
                "continuation_count": len(lines) - 1,
            }
        )
    else:
        upper_token, lower_token = decoded["upper"], decoded["lower"]
        upper_net, lower_net = decoded["upper_net"], decoded["lower_net"]
        upper_id, upper_suffix = _endpoint_fields(upper_token, upper_net, "Via.upper")
        lower_id, lower_suffix = _endpoint_fields(lower_token, lower_net, "Via.lower")
        if (upper_id in nodes and upper_token != nodes[upper_id]["node_token"]) or (
            lower_id in nodes and lower_token != nodes[lower_id]["node_token"]
        ):
            _fail(STOP_RECORD_MISMATCH, "Via endpoint token differs from its Node record")
        if upper_net is not None and upper_net != net or lower_net is not None and lower_net != net:
            _fail(STOP_RECORD_MISMATCH, "Via endpoint net annotation differs")
        if result["record_id"] != f"via:{edge_id}:{net}":
            _fail(STOP_RECORD_MISMATCH, "Via canonical record_id differs")
        if row.get("via_id") is not None and row.get("via_id") != edge_id:
            _fail(STOP_RECORD_MISMATCH, "Via identity differs from source")
        padstack = decoded["padstack"]
        if type(row.get("padstack_id")) is not str or row["padstack_id"] != padstack:
            _fail(STOP_PADSTACK, "Via PadStack differs from the selected PadStack")
        result.update(
            {
                "edge_kind": "Via",
                "via_id": edge_id,
                "padstack_id": padstack,
                "from_node_id": upper_id,
                "to_node_id": lower_id,
                "from_node_token": upper_token,
                "to_node_token": lower_token,
                "from_pin_suffix": upper_suffix,
                "to_pin_suffix": lower_suffix,
                "from_endpoint_net": upper_net,
                "to_endpoint_net": lower_net,
                "tail": decoded["tail"].strip(),
            }
        )
    if result["from_node_id"] not in nodes or result["to_node_id"] not in nodes or result["from_node_id"] == result["to_node_id"]:
        _fail(STOP_PATH_MISMATCH, f"{kind} endpoint is not in the selected Node set")
    result.update(
        {
            "net": net,
            "start_layer": nodes[result["from_node_id"]]["layer"],
            "end_layer": nodes[result["to_node_id"]]["layer"],
        }
    )
    return result


def _parse_padstack(row: Mapping[str, Any], source: BinaryIO, raw_size: int) -> dict[str, Any]:
    result, raw = _read_record(row, "PadStack", source, raw_size)
    lines = _framed_lines(raw, "PadStack")
    if len(lines) < 3:
        _fail(STOP_PADSTACK, "PadStack block is incomplete")
    header = _PADSTACK_RE.fullmatch(lines[0])
    if header is None or _END_PADSTACK_RE.fullmatch(lines[-1]) is None:
        _fail(STOP_PADSTACK, "PadStack block boundaries are incomplete")
    padstack_id = header.group("id").decode("utf-8")
    if result["record_id"] != f"padstack:{padstack_id}" or row.get("padstack_id") != padstack_id:
        _fail(STOP_PADSTACK, "PadStack canonical record_id differs")
    layers: list[str] = []
    index = 1
    while index < len(lines) - 1:
        match = _PADDEF_RE.fullmatch(lines[index])
        if match is None:
            if lines[index].strip():
                _fail(STOP_PADSTACK, "PadStack contains an unframed line")
            index += 1
            continue
        layers.append(match.group("layer").decode("utf-8"))
        index += 1
        while index < len(lines) - 1 and _END_PADDEF_RE.fullmatch(lines[index]) is None:
            index += 1
        if index >= len(lines) - 1:
            _fail(STOP_PADSTACK, "PadDef has no complete terminator")
        index += 1
    layer_keys = [layer.casefold() for layer in layers]
    target_keys = {layer.casefold() for layer in TARGET_LAYERS}
    if len(set(layer_keys)) != len(layer_keys) or target_keys.intersection(layer_keys):
        _fail(STOP_PADSTACK, "PadStack PadDef layers are duplicated or target-layer bound")
    materials = [item.decode("utf-8") for item in _MATERIAL_RE.findall(raw)]
    if len(materials) != 1 or materials[0] != "COPPER":
        _fail(STOP_PADSTACK, "PadStack material is not exactly COPPER")
    header_drills = [item.decode("ascii") for item in _HEADER_DRILL_RE.findall(lines[0])]
    named_drills = [item.group(1).decode("ascii") for line in lines[1:-1] if (item := _NAMED_DRILL_RE.fullmatch(line)) is not None]
    drills = header_drills + named_drills
    if len(drills) != 1:
        _fail(STOP_PADSTACK, "PadStack drill is absent or ambiguous")
    try:
        drill_mm = float(drills[0])
    except (ValueError, OverflowError):
        _fail(STOP_PADSTACK, "PadStack drill is invalid")
    if not math.isfinite(drill_mm) or drill_mm <= 0:
        _fail(STOP_PADSTACK, "PadStack drill is not finite and positive")
    result.update({"padstack_id": padstack_id, "material": materials[0], "drill_mm": drill_mm, "paddef_layers": list(layers)})
    return result


def _pointer_range(row: Any, kind: str, raw_size: int) -> tuple[int, int, str]:
    if not isinstance(row, Mapping):
        _fail(STOP_RECORD_MISMATCH, f"{kind} row is not an object")
    start, end = row.get("source_offset"), row.get("source_end")
    if type(start) is not int or type(end) is not int or not (0 <= start < end <= raw_size):
        _fail(STOP_BOUNDS, f"{kind} source bounds are invalid")
    if end - start > _RECORD_CAPS[kind]:
        _fail(STOP_BOUNDS, f"{kind} source range exceeds its cap")
    return start, end, kind


def _validate_ranges(ranges: Sequence[tuple[int, int, str]]) -> None:
    ordered = sorted(ranges, key=lambda value: (value[0], value[1], value[2]))
    previous: tuple[int, int, str] | None = None
    for current in ordered:
        if previous is not None and current[0] < previous[1]:
            _fail(STOP_OVERLAP, f"source ranges overlap: {previous[2]} and {current[2]}")
        previous = current


def _path_rows(
    pin: Mapping[str, Any], contract: Mapping[str, Any]
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    pin_id = pin.get("pin_id")
    path = pin.get("path")
    if not isinstance(path, Mapping) or not {"nodes", "edges", "terminal_node_id", "terminal_layer"}.issubset(path):
        _fail(STOP_PATH_MISMATCH, f"{pin_id} path keys are incomplete")
    raw_nodes, raw_edges = path.get("nodes"), path.get("edges")
    if type(raw_nodes) is not list or type(raw_edges) is not list:
        _fail(STOP_PATH_MISMATCH, f"{pin_id} path rows are not arrays")
    if (
        len(raw_nodes) != contract["node_count"]
        or len(raw_edges) != contract["edge_count"]
        or len(raw_edges) != len(raw_nodes) - 1
    ):
        _fail(STOP_PATH_MISMATCH, f"{pin_id} path count differs")
    return path, raw_nodes, raw_edges


def _build_pin(
    pin: Mapping[str, Any],
    source: BinaryIO,
    raw_size: int,
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(pin, Mapping):
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "selected pin row is not an object")
    pin_id = pin.get("pin_id")
    if type(pin_id) is not str:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "selected pin identity is invalid")
    contract = _PIN_BY_ID.get(pin_id)
    if contract is None:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "unexpected selected pin")
    path, raw_nodes, raw_edges = _path_rows(pin, contract)
    nodes = [_parse_node(row, source, raw_size, contract) for row in raw_nodes]
    by_id = {node["node_id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        _fail(STOP_BRANCH, f"{pin_id} repeats a Node")
    first = nodes[0]
    if first["node_id"] != contract["source_node_id"] or first["node_token"] != contract["source_node_token"] or first["layer"] != "Signal$TOP" or first["net"] != contract["net"]:
        _fail(STOP_PATH_MISMATCH, f"{pin_id} source Node seed differs")
    if first["padstack_id"] != "DUT":
        _fail(STOP_PADSTACK, f"{pin_id} source Node PadStack is not DUT")
    terminal = nodes[-1]
    if path.get("terminal_node_id") != terminal["node_id"] or path.get("terminal_layer") != terminal["layer"] or terminal["layer"] != contract["terminal_layer"]:
        _fail(STOP_PATH_MISMATCH, f"{pin_id} terminal Node/layer differs")
    target_set = {item.casefold() for item in TARGET_LAYERS}
    if any(node["layer"].casefold() in target_set for node in nodes):
        _fail(STOP_TARGET_LAYER, f"{pin_id} path has target-layer Node incidence")
    edges = [_parse_edge(row, source, raw_size, by_id, contract) for row in raw_edges]
    degree = {node_id: 0 for node_id in by_id}
    for index, edge in enumerate(edges):
        expected_from = (nodes[index]["node_token"], nodes[index]["net"])
        expected_to = (nodes[index + 1]["node_token"], nodes[index + 1]["net"])
        raw_endpoints = (
            (edge["from_node_token"], edge["from_endpoint_net"]),
            (edge["to_node_token"], edge["to_endpoint_net"]),
        )
        traversal_reversed = False
        if edge["edge_kind"] == "Trace":
            if raw_endpoints == (expected_to, expected_from):
                traversal_reversed = True
            elif raw_endpoints != (expected_from, expected_to):
                _fail(STOP_PATH_MISMATCH, f"{pin_id} edge order is not a chain")
        elif edge["from_node_id"] != nodes[index]["node_id"] or edge["to_node_id"] != nodes[index + 1]["node_id"]:
            _fail(STOP_PATH_MISMATCH, f"{pin_id} edge order is not a chain")
        edge["traversal_reversed"] = traversal_reversed
        if edge["start_layer"].casefold() in target_set or edge["end_layer"].casefold() in target_set:
            _fail(STOP_TARGET_LAYER, f"{pin_id} path has target-layer edge incidence")
        degree[edge["from_node_id"]] += 1
        degree[edge["to_node_id"]] += 1
    if edges[0].get("edge_kind") != "Via" or edges[0].get("via_id") != contract["via_id"]:
        _fail(STOP_PATH_MISMATCH, f"{pin_id} first Via seed differs")
    first_via = edges[0]
    if (
        first_via["from_node_token"] != contract["source_node_token"]
        or first_via["to_node_id"] != contract["lower_node_id"]
        or first_via["end_layer"] != contract["lower_layer"]
        or first_via["padstack_id"] != "DR-0102_60"
    ):
        _fail(STOP_PATH_MISMATCH, f"{pin_id} first Via endpoint seed differs")
    authority = lineage["wp3_05"]
    if (
        first_via["source_offset"] != authority["source_offset"]
        or first_via["source_end"] != authority["source_end"]
        or first_via["source_record_sha256"] != authority["source_record_sha256"]
    ):
        _fail(STOP_RECEIPT_LINEAGE, f"{pin_id} Via pointer differs from verified D115 lineage")
    if any(node["incident_edge_count"] != degree[node["node_id"]] for node in nodes):
        _fail(STOP_BRANCH, f"{pin_id} incident-edge counts differ")
    if degree[first["node_id"]] != 1 or degree[terminal["node_id"]] != 1 or any(degree[node_id] != 2 for node_id in list(degree)[1:-1]):
        _fail(STOP_BRANCH, f"{pin_id} path is branched or cyclic")
    projection = lineage["projection"]
    output = {
        "pin_id": pin_id,
        "via_id": contract["via_id"],
        "net": contract["net"],
        "target_layer": contract["target_layer"],
        "status": "CANNOT_DERIVE",
        "path": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "terminal": {"node_id": terminal["node_id"], "layer": terminal["layer"]},
            "incident_edge_counts": {node["node_id"]: degree[node["node_id"]] for node in nodes},
        },
        "selected_chain_intersection": {
            "target_layers": list(TARGET_LAYERS),
            "via_records": [],
            "paddef_records": [],
            "nonempty": False,
            "physical_geometry_claimed": False,
        },
        "d115_projection": projection,
        "physical_geometry_proven": False,
        "physical_traversal_proven": False,
        "target_layer_traversal_proven": False,
        "l29_l30_physical_pad_proven": False,
    }
    return output


def build_negative_certificate(
    evidence: Mapping[str, Any],
    *,
    raw_path: Path | str | None = None,
    d115b_path: Path | str | None = None,
    d115c_path: Path | str | None = None,
    wp3_05_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a certificate after verifying every required source and receipt."""

    if not isinstance(evidence, Mapping):
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "evidence must be an object")
    if any(path is None for path in (raw_path, d115b_path, d115c_path, wp3_05_path)):
        _fail(STOP_INPUT_IDENTITY_MISMATCH, "raw SPD and all three receipt paths are required")
    expected = {
        "raw": dict(RAW_IDENTITY),
        "d115b": dict(D115B_IDENTITY),
        "d115c": dict(D115C_IDENTITY),
        "wp3_05": dict(WP3_05_IDENTITY),
    }
    identities: dict[str, dict[str, Any]] = {}
    source = Path(raw_path)
    try:
        source_stream = source.open("rb")
    except OSError as exc:
        _fail(STOP_INPUT_IDENTITY_MISMATCH, f"raw SPD cannot be opened: {exc}")
    with source_stream:
        identities["raw"], _ = _stream_identity(raw_path, expected["raw"], "raw SPD", stream=source_stream)
        identities["d115b"], d115b = _stream_identity(d115b_path, expected["d115b"], "D115B receipt", parse_json=True)
        identities["d115c"], d115c = _stream_identity(d115c_path, expected["d115c"], "D115C receipt", parse_json=True)
        identities["wp3_05"], wp3 = _stream_identity(wp3_05_path, expected["wp3_05"], "WP3-05 receipt", parse_json=True)
        if not isinstance(d115b, Mapping) or not isinstance(d115c, Mapping) or not isinstance(wp3, Mapping):
            _fail(STOP_RECEIPT_LINEAGE, "receipt roots are not objects")
        raw_size = identities["raw"]["size_bytes"]
        lineage, receipt_metadata = _validate_lineage(source, d115b, d115c, wp3, identities)
        pins = evidence.get("pins")
        if type(pins) is not list or len(pins) != len(PINS):
            _fail(STOP_INPUT_IDENTITY_MISMATCH, "selected pin scope must contain exactly two pins")
        seen: set[str] = set()
        for pin in pins:
            if not isinstance(pin, Mapping) or type(pin.get("pin_id")) is not str or pin.get("pin_id") in seen:
                _fail(STOP_INPUT_IDENTITY_MISMATCH, "selected pin scope is duplicated or malformed")
            seen.add(pin.get("pin_id"))
        if seen != set(_PIN_BY_ID):
            _fail(STOP_INPUT_IDENTITY_MISMATCH, "selected pin identities are incomplete")
        if [pin.get("pin_id") for pin in pins] != [contract["pin_id"] for contract in PINS]:
            _fail(STOP_INPUT_IDENTITY_MISMATCH, "selected pin order differs from the sealed scope")

        padstack_rows = evidence.get("padstacks")
        if type(padstack_rows) is not list:
            _fail(STOP_PADSTACK, "PadStack rows are absent")
        candidates: dict[str, Mapping[str, Any]] = {}
        for row in padstack_rows:
            if not isinstance(row, Mapping):
                _fail(STOP_PADSTACK, "PadStack row is not an object")
            padstack_id = row.get("padstack_id")
            if type(padstack_id) is not str or not padstack_id:
                _fail(STOP_PADSTACK, "PadStack identity is invalid")
            if padstack_id in candidates:
                _fail(STOP_PADSTACK, f"PadStack row is duplicated: {padstack_id}")
            candidates[padstack_id] = row
        path_ranges: list[tuple[int, int, str]] = []
        for pin in pins:
            contract = _PIN_BY_ID[pin["pin_id"]]
            _, raw_nodes, raw_edges = _path_rows(pin, contract)
            path_ranges.extend(_pointer_range(row, "Node", raw_size) for row in raw_nodes)
            for row in raw_edges:
                kind_value = row.get("edge_kind") if isinstance(row, Mapping) else None
                kind = "Via" if isinstance(kind_value, str) and kind_value.casefold() == "via" else "Trace" if isinstance(kind_value, str) and kind_value.casefold() == "trace" else "Node"
                if kind == "Node":
                    _fail(STOP_RECORD_MISMATCH, "edge kind is invalid")
                path_ranges.append(_pointer_range(row, kind, raw_size))
        via_padstacks: set[str] = set()
        for pin in pins:
            for row in pin["path"]["edges"]:
                if not isinstance(row, Mapping):
                    continue
                edge_kind = row.get("edge_kind")
                if isinstance(edge_kind, str) and edge_kind.casefold() == "via":
                    padstack_id = row.get("padstack_id")
                    if type(padstack_id) is not str or not padstack_id:
                        _fail(STOP_PADSTACK, "Via PadStack evidence is absent")
                    via_padstacks.add(padstack_id)
        if set(candidates) != via_padstacks:
            _fail(STOP_PADSTACK, "PadStack evidence set differs from selected Via PadStacks")
        padstack_ranges = [
            _pointer_range(row, "PadStack", raw_size) for row in candidates.values()
        ]
        _validate_ranges(path_ranges + padstack_ranges)
        parsed_padstacks = {
            padstack_id: _parse_padstack(row, source_stream, raw_size)
            for padstack_id, row in candidates.items()
        }
        built = [_build_pin(pin, source_stream, raw_size, lineage[pin["pin_id"]]) for pin in pins]
        for pin in built:
            for edge in pin["path"]["edges"]:
                if edge["edge_kind"] != "Via":
                    continue
                padstack = parsed_padstacks.get(edge["padstack_id"])
                if padstack is None:
                    _fail(STOP_PADSTACK, "Via PadStack block is absent")
                layers = {layer.casefold() for layer in padstack["paddef_layers"]}
                if edge["start_layer"].casefold() not in layers or edge["end_layer"].casefold() not in layers:
                    _fail(STOP_PADSTACK, "Via endpoint layer is absent from its PadStack")
    proofs = {
        "three_dimensional_geometry_proven": False,
        "barrel_proven": False,
        "antipad_proven": False,
        "land_proven": False,
        "intermediate_access_proven": False,
        "target_layer_traversal_proven": False,
        "l29_l30_physical_pad_proven": False,
        "physical_geometry_proven": False,
        "physical_traversal_proven": False,
    }
    return {
        "product": PRODUCT,
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "status": "WP3=PARTIAL",
        "wp3_status": "PARTIAL",
        "gate": STOP_NOT_REPRESENTED,
        "per_pin_status": {contract["pin_id"]: "CANNOT_DERIVE" for contract in PINS},
        "scope": {
            "pins": [contract["pin_id"] for contract in PINS],
            "target_layers": list(TARGET_LAYERS),
            "raw_spd_graph_scan": False,
        },
        "execution_scope": {
            "preparation_only": True,
            "production_authorized": False,
            "immutable_hq_approval_required": True,
        },
        "identities": identities,
        **receipt_metadata,
        "pins": built,
        "padstacks": list(parsed_padstacks.values()),
        "proof_flags": proofs,
        "logical_ownership_preserved": True,
        "physical_nonconnection_claimed": False,
        "fallback_geometry_used": False,
    }


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    if os.path.lexists(path):
        _fail(STOP_OUTPUT_EXISTS, "output path already exists; refusing to clobber it")
    if not path.parent.is_dir():
        _fail(STOP_OUTPUT_EXISTS, "output parent directory does not exist")
    data = _canonical(payload)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        temporary.unlink()
        temporary = None
    except FileExistsError:
        _fail(STOP_OUTPUT_EXISTS, "output path appeared during write")
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} D117 WP3 path-absence certificate")
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--raw-spd", type=Path, required=True)
    parser.add_argument("--d115b", "--d115b-receipt", dest="d115b_path", type=Path, required=True)
    parser.add_argument("--d115c", "--d115c-receipt", dest="d115c_path", type=Path, required=True)
    parser.add_argument("--wp3-05", "--wp3-05-receipt", dest="wp3_05_path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        try:
            input_bytes = args.input.read_bytes()
        except OSError as exc:
            _fail(STOP_INPUT_IDENTITY_MISMATCH, f"input cannot be read: {exc}")
        evidence = _strict_json(input_bytes)
        result = build_negative_certificate(
            evidence,
            raw_path=args.raw_spd,
            d115b_path=args.d115b_path,
            d115c_path=args.d115c_path,
            wp3_05_path=args.wp3_05_path,
        )
        _write_atomic(args.output, result)
        print(
            f"{PRODUCT} v{VERSION} PREPARATION_ONLY "
            f"production_authorized=false {result['status']} wrote {args.output}"
        )
        return 0
    except Refusal as exc:
        print(f"{PRODUCT} v{VERSION} REFUSAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
