"""Bounded D115 source-local Port44/L29-L30 receipt audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


class AuditStop(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _stop(code: str) -> None:
    raise AuditStop(code)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _stop("STOP_IDENTITY_MISMATCH")
    if not isinstance(value, dict):
        _stop("STOP_IDENTITY_MISMATCH")
    return value


def _hash_gate(path: Path, expected: str, label: str) -> str:
    if not path.is_file() or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        _stop("STOP_IDENTITY_MISMATCH")
    actual = _sha256(path)
    if actual.casefold() != expected.casefold():
        _stop("STOP_IDENTITY_MISMATCH")
    return actual


def _tokens(payload: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", payload.strip()) if token and token not in {";", "=>"}]


def _port_identity(source: Path, port_name: str, expected_positive: int, expected_negative: int, required_negative: set[str]) -> dict[str, Any]:
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError:
        _stop("STOP_IDENTITY_MISMATCH")
    starts = [index for index, line in enumerate(lines) if re.search(rf"^\s*\.Port\s+{re.escape(port_name)}(?:\s|$)", line, re.I)]
    if len(starts) != 1:
        _stop("STOP_AMBIGUOUS_OR_MISSING_PORT")
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^\s*\.(?:Port|Connect|End)", lines[index], re.I):
            end = index
            break
    positive: list[str] = []
    negative_count = 0
    negative_sequence = hashlib.sha256()
    set_accumulator = bytearray(32)
    selected_found: set[str] = set()
    for line in lines[start + 1 : end]:
        match = re.match(r"^\s*(positive|pos|pwr|\+|negative|neg|gnd|return|-)\s*[:=]?\s*(.*?)\s*$", line, re.I)
        if not match:
            continue
        role, payload = match.group(1).casefold(), match.group(2)
        values = _tokens(payload)
        if role in {"positive", "pos", "pwr", "+"}:
            positive.extend(values)
            continue
        for token in values:
            negative_count += 1
            if token in required_negative:
                selected_found.add(token)
            negative_sequence.update(token.encode("utf-8"))
            negative_sequence.update(b"\n")
            token_digest = hashlib.sha256(token.encode("utf-8")).digest()
            for offset, value in enumerate(token_digest):
                set_accumulator[offset] ^= value
    if len(positive) != expected_positive or negative_count != expected_negative:
        _stop("STOP_PORT_TERMINAL_COUNT_MISMATCH")
    if selected_found != required_negative:
        _stop("STOP_TERMINAL_MISMATCH")
    return {
        "port_name": port_name,
        "positive_terminals": positive,
        "positive_count": len(positive),
        "positive_sequence_sha256": hashlib.sha256("\n".join(positive).encode("utf-8")).hexdigest(),
        "positive_set_sha256": hashlib.sha256("\n".join(sorted(set(positive))).encode("utf-8")).hexdigest(),
        "negative_count": negative_count,
        "negative_sequence_sha256": negative_sequence.hexdigest(),
        "negative_set_sha256": hashlib.sha256(bytes(set_accumulator)).hexdigest(),
        "negative_set_hash_algorithm": "sha256(xor(sha256(terminal)))",
    }


def _binding_detail(binding: Mapping[str, Any]) -> dict[str, Any]:
    required = ("terminal_id", "role", "source_path", "landing_node", "landing_layer", "pad_footprint", "finite_vertex", "finite_edge", "owner_id", "island_id", "component_id")
    if any(key not in binding for key in required) or any(not isinstance(binding[key], str) or not binding[key] for key in ("terminal_id", "role", "landing_node", "landing_layer", "finite_vertex", "finite_edge", "owner_id", "island_id", "component_id")) or not isinstance(binding["pad_footprint"], Mapping) or not binding["pad_footprint"] or not isinstance(binding["source_path"], list) or not binding["source_path"]:
        _stop("STOP_IR_INCOMPLETE")
    path = binding["source_path"]
    first = path[0].get("kind") if isinstance(path[0], Mapping) else str(path[0]).split(":", 1)[0]
    second = path[1].get("kind") if len(path) > 1 and isinstance(path[1], Mapping) else (str(path[1]).split(":", 1)[0] if len(path) > 1 else "")
    if str(first).casefold() != "node" or str(second).casefold() not in {"via", "trace"}:
        _stop("STOP_IR_INCOMPLETE")
    return {key: binding[key] for key in required}


def _cells(d104: Mapping[str, Any], window: list[int]) -> list[dict[str, Any]]:
    raw = d104.get("cells")
    if not isinstance(raw, list) or len(raw) != 8:
        _stop("STOP_D104_CENSUS_MISMATCH")
    expected = [259, 260, 261, 262, 263, 264, 265, 266]
    cells: list[dict[str, Any]] = []
    for item, ordinal in zip(raw, expected):
        if not isinstance(item, Mapping) or item.get("ordinal") != ordinal:
            _stop("STOP_D104_CENSUS_MISMATCH")
        required = ("layer", "logical_net", "artwork_net", "island_id", "wkb_sha256", "bbox_pm")
        if any(key not in item for key in required) or any(not isinstance(item[key], str) or not item[key] for key in ("layer", "logical_net", "artwork_net", "island_id", "wkb_sha256")) or not re.fullmatch(r"[0-9a-fA-F]{64}", item["wkb_sha256"]) or not isinstance(item["bbox_pm"], list) or len(item["bbox_pm"]) != 4 or any(type(value) is not int for value in item["bbox_pm"]):
            _stop("STOP_D104_CENSUS_MISMATCH")
        expected_layer = "L29" if ordinal == 259 else "L30"
        if item["layer"] != expected_layer or not item["island_id"] or not item["wkb_sha256"]:
            _stop("STOP_D104_CENSUS_MISMATCH")
        x0, y0, x1, y1 = map(int, item["bbox_pm"])
        if x0 > x1 or y0 > y1:
            _stop("STOP_INVALID_WINDOW")
        wx0, wy0, wx1, wy1 = window
        ix0, iy0, ix1, iy1 = max(x0, wx0), max(y0, wy0), min(x1, wx1), min(y1, wy1)
        if ix0 > ix1 or iy0 > iy1:
            _stop("STOP_D104_CELL_OUTSIDE_WINDOW")
        cells.append({"ordinal": ordinal, "layer": item["layer"], "logical_net": item["logical_net"], "artwork_net": item["artwork_net"], "island_id": item["island_id"], "wkb_sha256": item["wkb_sha256"], "bbox_pm": [x0, y0, x1, y1], "intersection_pm": [ix0, iy0, ix1, iy1], "boundary_touch": ix0 in {wx0, wx1} or ix1 in {wx0, wx1} or iy0 in {wy0, wy1} or iy1 in {wy0, wy1} })
    return cells


def audit_source_local_l29_l30_port_window(*, source_path: Path, d103_path: Path, d104_path: Path, ir_path: Path, output_path: Path, expected_source_sha256: str, expected_d103_sha256: str, expected_d104_sha256: str, expected_ir_sha256: str, expected_positive_count: int = 3, expected_negative_count: int = 10919, window_pm: list[int] | None = None, deadline_s: float = 1800.0) -> dict[str, Any]:
    started = time.monotonic()
    if output_path.exists():
        _stop("STOP_OUTPUT_EXISTS")
    if window_pm is None or len(window_pm) != 4 or any(type(value) is not int for value in window_pm) or window_pm[0] > window_pm[2] or window_pm[1] > window_pm[3]:
        _stop("STOP_INVALID_WINDOW")
    source_sha = _hash_gate(source_path, expected_source_sha256, "source")
    d103_sha = _hash_gate(d103_path, expected_d103_sha256, "d103")
    d104_sha = _hash_gate(d104_path, expected_d104_sha256, "d104")
    ir_sha = _hash_gate(ir_path, expected_ir_sha256, "ir")
    d103, d104, ir = _json(d103_path), _json(d104_path), _json(ir_path)
    if any(not isinstance(meta.get("source_sha256"), str) or meta["source_sha256"].casefold() != source_sha.casefold() for meta in (d103, d104, ir)):
        _stop("STOP_IDENTITY_MISMATCH")
    bindings_raw = ir.get("terminal_bindings")
    if not isinstance(bindings_raw, list):
        _stop("STOP_IR_INCOMPLETE")
    bindings = [_binding_detail(item) for item in bindings_raw if isinstance(item, Mapping)]
    positive_bindings = [item for item in bindings if str(item["role"]).casefold() in {"positive", "power", "pwr"}]
    selected_negative = [item for item in bindings if str(item["role"]).casefold() in {"negative", "ground", "gnd", "return"}]
    selected_ids = [item["terminal_id"] for item in selected_negative]
    if len(selected_ids) != len(set(selected_ids)) or len(selected_ids) > expected_negative_count:
        _stop("STOP_TERMINAL_MISMATCH")
    port = _port_identity(source_path, "Port44", expected_positive_count, expected_negative_count, set(selected_ids))
    if [item["terminal_id"] for item in positive_bindings] != port["positive_terminals"]:
        _stop("STOP_TERMINAL_MISMATCH")
    cells = _cells(d104, window_pm)
    if time.monotonic() - started > deadline_s:
        _stop("STOP_DEADLINE")
    receipt = {
        "schema": "source-local-l29-l30-port-window-receipt-v1",
        "result": "PASS_SOURCE_LOCAL_GAP_ONLY",
        "source_sha256": source_sha,
        "evidence": {"d103_sha256": d103_sha, "d104_sha256": d104_sha, "source_ir_sha256": ir_sha},
        "port44": port,
        "terminal_bindings": bindings,
        "selected_negative_terminal_ids": selected_ids,
        "d104_cells": cells,
        "window_pm": window_pm,
        "scope": {"source_local_shadow_only": True, "full_port_negative_paths_resolved": False, "whole_layer_raw_primitive_census": False, "solver_executed": False, "powersi_executed": False},
        "limitations": ["manufactured/production solver input is not proved", "PowerSI accuracy is not claimed"],
    }
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except OSError:
        _stop("STOP_INTERNAL")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "d103", "d104", "ir", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("source", "d103", "d104", "ir"):
        parser.add_argument(f"--expected-{name}-sha256", required=True)
    parser.add_argument("--expected-positive-count", type=int, default=3)
    parser.add_argument("--expected-negative-count", type=int, default=10919)
    parser.add_argument("--window-pm", type=int, nargs=4, required=True)
    args = parser.parse_args()
    receipt = audit_source_local_l29_l30_port_window(source_path=args.source, d103_path=args.d103, d104_path=args.d104, ir_path=args.ir, output_path=args.output, expected_source_sha256=args.expected_source_sha256, expected_d103_sha256=args.expected_d103_sha256, expected_d104_sha256=args.expected_d104_sha256, expected_ir_sha256=args.expected_ir_sha256, expected_positive_count=args.expected_positive_count, expected_negative_count=args.expected_negative_count, window_pm=args.window_pm)
    print(json.dumps({"result": receipt["result"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
