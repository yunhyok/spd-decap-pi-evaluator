"""Bounded D115 source-local Port44/L29-L30 receipt audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping


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


def _json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _stop(code)
    if not isinstance(value, dict):
        _stop(code)
    return value


def _hash_gate(path: Path, expected: str, code: str) -> str:
    if not path.is_file() or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        _stop(code)
    actual = _sha256(path)
    if actual.casefold() != expected.casefold():
        _stop(code)
    return actual


def _canonical_hash(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _scan_source(source: Path, port_id: str, expected_positive: int, expected_negative: int, selected_negative: set[str], deadline: float) -> tuple[str, dict[str, Any]]:
    source_digest = hashlib.sha256()
    record_digest = hashlib.sha256()
    positive: list[str] = []
    negative: list[str] = []
    found = False
    record_active = False
    record_lines = 0
    active_role = ""
    positive_re = re.compile(r"^\s*\+\s+PositiveTerminal\s+(.+?)\s*$", re.I)
    negative_re = re.compile(r"^\s*\+\s+NegativeTerminal\s+(.+?)\s*$", re.I)
    continuation_re = re.compile(r"^\s*\+\s+(\$Package\.[^\s]+|Node[^\s]+!![^\s]+::[^\s]+)\s*$", re.I)
    record_re = re.compile(r"^\s*(Port\S+)\s+", re.I)
    try:
        with source.open("rb") as stream:
            for line_number, raw in enumerate(stream, 1):
                if time.monotonic() > deadline:
                    _stop("STOP_DEADLINE")
                source_digest.update(raw)
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    _stop("STOP_SOURCE_IDENTITY")
                header = record_re.match(line)
                if header:
                    if record_active:
                        record_active = False
                    if header.group(1) == port_id:
                        if found:
                            _stop("STOP_PORT_AMBIGUOUS")
                        found = True
                        record_active = True
                        record_digest = hashlib.sha256(raw)
                        record_lines = 1
                        active_role = ""
                    continue
                if not record_active:
                    continue
                if line.lstrip().startswith("."):
                    record_active = False
                    continue
                record_digest.update(raw)
                record_lines += 1
                match = positive_re.match(line) or negative_re.match(line)
                role = "positive" if positive_re.match(line) else "negative" if negative_re.match(line) else ""
                payload = match.group(1) if match else (continuation_re.match(line).group(1) if continuation_re.match(line) else "")
                if role:
                    active_role = role
                else:
                    role = active_role
                if not payload:
                    continue
                terminal_match = re.search(r"(?:\$Package\.)?Node[^\s]+!![^\s]+::[^\s]+", payload)
                if terminal_match is None:
                    _stop("STOP_PORT_COMPILED_IDENTITY_UNPROVEN")
                token = terminal_match.group(0)
                if not token.startswith("$Package."):
                    token = "$Package." + token
                if role == "positive":
                    positive.append(token)
                elif role == "negative":
                    negative.append(token)
    except AuditStop:
        raise
    except OSError:
        _stop("STOP_SOURCE_IDENTITY")
    if not found:
        _stop("STOP_PORT_AMBIGUOUS")
    if len(positive) != expected_positive or len(negative) != expected_negative:
        _stop("STOP_PORT_TERMINAL_COUNT_MISMATCH")
    if len(set(positive)) != len(positive) or len(set(negative)) != len(negative):
        _stop("STOP_PORT_TERMINAL_DUPLICATE")
    if not selected_negative.issubset(set(negative)):
        _stop("STOP_TERMINAL_MISMATCH")
    return source_digest.hexdigest(), {"port_id": port_id, "record_sha256": record_digest.hexdigest(), "record_line_count": record_lines, "positive_terminals": positive, "positive_count": len(positive), "positive_sequence_sha256": _canonical_hash(positive), "positive_set_sha256": _canonical_hash(sorted(positive)), "negative_count": len(negative), "negative_sequence_sha256": _canonical_hash(negative), "negative_set_sha256": _canonical_hash(sorted(negative))}


def _binding_detail(binding: Mapping[str, Any]) -> dict[str, Any]:
    required = ("terminal_id", "package_terminal_key", "role", "source_node_record_id", "landing_node", "layer", "pad_footprint", "finite_vertex_id", "finite_edge_id", "via_owner_id", "island_id", "component_id")
    if any(key not in binding for key in required) or any(not isinstance(binding[key], str) or not binding[key] for key in ("terminal_id", "package_terminal_key", "role", "source_node_record_id", "landing_node", "layer", "finite_vertex_id", "island_id", "component_id")) or not isinstance(binding["pad_footprint"], Mapping) or not binding["pad_footprint"]:
        _stop("STOP_IR_INCOMPLETE")
    if not binding["source_node_record_id"].casefold().startswith("node:") or not (str(binding["via_owner_id"]) or str(binding["finite_edge_id"])):
        _stop("STOP_IR_INCOMPLETE")
    detail = {key: binding[key] for key in required}
    detail["source_path"] = [binding["source_node_record_id"], f"via:{binding['via_owner_id']}" if binding["via_owner_id"] else f"trace:{binding['finite_edge_id']}"]
    return detail


def _d104_cells(d104: Mapping[str, Any], root: Path, window_pm: list[int]) -> list[dict[str, Any]]:
    source = d104.get("source")
    if not isinstance(source, Mapping) or not isinstance(source.get("sha256"), str):
        _stop("STOP_D104_IDENTITY")
    counts = d104.get("layer_counts")
    if counts != {"L29": 1, "L30": 7}:
        _stop("STOP_D104_CENSUS_MISMATCH")
    raw = d104.get("cells")
    if not isinstance(raw, list) or len(raw) != 16:
        _stop("STOP_D104_CENSUS_MISMATCH")
    selected = [item for item in raw if isinstance(item, Mapping) and item.get("ordinal") in range(259, 267)]
    if len(selected) != 8 or {item["ordinal"] for item in selected} != set(range(259, 267)):
        _stop("STOP_D104_CENSUS_MISMATCH")
    result: list[dict[str, Any]] = []
    wx0, wy0, wx1, wy1 = window_pm
    for item in sorted(selected, key=lambda value: int(value["ordinal"])):
        ordinal = int(item["ordinal"])
        expected_layer = "Signal$L29(DGND)" if ordinal == 259 else "Signal$L30(OTHER_POWER1)"
        geometry = item.get("geometry")
        if item.get("layer") != expected_layer or not isinstance(item.get("net"), str) or not item["net"] or not isinstance(item.get("island_id"), str) or not item["island_id"] or not isinstance(geometry, Mapping):
            _stop("STOP_D104_CENSUS_MISMATCH")
        if ordinal == 259 and item["island_id"] != "2db099...":
            _stop("STOP_D104_CENSUS_MISMATCH")
        if ordinal == 264 and item["island_id"] != "cb8510...":
            _stop("STOP_D104_CENSUS_MISMATCH")
        filename, bbox, wkb_sha, wkb_size = geometry.get("filename"), geometry.get("bbox_um"), geometry.get("wkb_sha256"), geometry.get("wkb_size_bytes")
        if not isinstance(filename, str) or not isinstance(bbox, list) or len(bbox) != 4 or not isinstance(wkb_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", wkb_sha) or type(wkb_size) is not int:
            _stop("STOP_D104_CENSUS_MISMATCH")
        wkb_path = root / filename
        if not wkb_path.is_file() or wkb_path.stat().st_size != wkb_size or _sha256(wkb_path).casefold() != wkb_sha.casefold():
            _stop("STOP_D104_IDENTITY")
        try:
            from shapely import wkb
            from shapely.geometry import box
            shape = wkb.loads(wkb_path.read_bytes())
            window = box(wx0 / 1_000_000, wy0 / 1_000_000, wx1 / 1_000_000, wy1 / 1_000_000)
            intersects = bool(shape.intersects(window))
            intersection = shape.intersection(window)
            area = float(intersection.area)
        except Exception as exc:
            _stop(f"STOP_INTERNAL:{type(exc).__name__}")
        result.append({"ordinal": ordinal, "layer": item["layer"], "net": item["net"], "island_id": item["island_id"], "geometry": {"filename": filename, "bbox_um": bbox, "wkb_sha256": wkb_sha, "wkb_size_bytes": wkb_size}, "intersects": intersects, "intersection_area_um2": area, "boundary_touch": bool(intersects and area == 0.0)})
    return result


def audit_source_local_l29_l30_port_window(*, source_path: Path, d103_path: Path, d104_path: Path, ir_path: Path, output_path: Path, expected_source_sha256: str, expected_d103_sha256: str, expected_d104_sha256: str, expected_ir_sha256: str, expected_positive_count: int = 3, expected_negative_count: int = 10919, window_pm: list[int] | None = None, d104_root: Path | None = None, port_id: str = "Port44_SITE0::ADC_VDD_180_VQPS_SYS_1_AON/0", deadline_s: float = 1800.0) -> dict[str, Any]:
    started = time.monotonic()
    if output_path.exists():
        _stop("STOP_OUTPUT_EXISTS")
    if deadline_s <= 0 or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_source_sha256) or window_pm is None or len(window_pm) != 4 or any(type(value) is not int for value in window_pm) or window_pm[0] > window_pm[2] or window_pm[1] > window_pm[3]:
        _stop("STOP_INVALID_WINDOW")
    deadline = started + deadline_s
    d103_sha = _hash_gate(d103_path, expected_d103_sha256, "STOP_D103_IDENTITY")
    d104_sha = _hash_gate(d104_path, expected_d104_sha256, "STOP_D104_IDENTITY")
    ir_sha = _hash_gate(ir_path, expected_ir_sha256, "STOP_IR_IDENTITY")
    d103, d104, ir = _json(d103_path, "STOP_D103_IDENTITY"), _json(d104_path, "STOP_D104_IDENTITY"), _json(ir_path, "STOP_IR_IDENTITY")
    d103_source = d103.get("source", {}).get("sha256") if isinstance(d103.get("source"), Mapping) else None
    if not isinstance(d103_source, str) or d103_source.casefold() != expected_source_sha256.casefold() or ("status" in d103 and d103["status"] != "PASS"):
        _stop("STOP_D103_IDENTITY")
    ir_source = ir.get("manifest", {}).get("source_sha256") if isinstance(ir.get("manifest"), Mapping) else ir.get("source_sha256")
    if not isinstance(ir_source, str) or ir_source.casefold() != expected_source_sha256.casefold():
        _stop("STOP_IR_IDENTITY")
    bindings_raw = ir.get("terminal_bindings")
    if not isinstance(bindings_raw, list):
        _stop("STOP_IR_INCOMPLETE")
    bindings = [_binding_detail(item) for item in bindings_raw if isinstance(item, Mapping)]
    positive_bindings = [item for item in bindings if item["role"].casefold() in {"positive", "power", "pwr"}]
    selected_negative = [item for item in bindings if item["role"].casefold() in {"negative", "ground", "gnd", "return"}]
    selected_ids = {item["package_terminal_key"] for item in selected_negative}
    source_sha, port = _scan_source(source_path, port_id, expected_positive_count, expected_negative_count, selected_ids, deadline)
    if source_sha.casefold() != expected_source_sha256.casefold() or [item["package_terminal_key"] for item in positive_bindings] != port["positive_terminals"]:
        _stop("STOP_PORT_COMPILED_IDENTITY_UNPROVEN")
    if d104.get("source", {}).get("sha256", "").casefold() != expected_source_sha256.casefold():
        _stop("STOP_D104_IDENTITY")
    cells = _d104_cells(d104, d104_root or d104_path.parent, window_pm)
    if time.monotonic() > deadline:
        _stop("STOP_DEADLINE")
    receipt = {"schema": "source-local-l29-l30-port-window-receipt-v2", "result": "PASS_SOURCE_LOCAL_GAP_ONLY", "source_sha256": source_sha, "evidence": {"d103_sha256": d103_sha, "d104_sha256": d104_sha, "source_ir_sha256": ir_sha}, "port44": port, "terminal_bindings": bindings, "d104_cells": cells, "window_pm": window_pm, "scope": {"source_local_shadow_only": True, "full_port_negative_paths_resolved": False, "whole_layer_raw_primitive_census": False, "solver_executed": False, "powersi_executed": False}}
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        _stop(f"STOP_INTERNAL:{type(exc).__name__}")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "d103", "d104", "ir", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("source", "d103", "d104", "ir"):
        parser.add_argument(f"--expected-{name}-sha256", required=True)
    parser.add_argument("--port-id", required=True)
    parser.add_argument("--deadline-seconds", type=float, default=1800.0)
    parser.add_argument("--window-pm", type=int, nargs=4, required=True)
    parser.add_argument("--expected-positive-count", type=int, default=3)
    parser.add_argument("--expected-negative-count", type=int, default=10919)
    parser.add_argument("--d104-root", type=Path)
    args = parser.parse_args()
    try:
        receipt = audit_source_local_l29_l30_port_window(source_path=args.source, d103_path=args.d103, d104_path=args.d104, ir_path=args.ir, output_path=args.output, expected_source_sha256=args.expected_source_sha256, expected_d103_sha256=args.expected_d103_sha256, expected_d104_sha256=args.expected_d104_sha256, expected_ir_sha256=args.expected_ir_sha256, expected_positive_count=args.expected_positive_count, expected_negative_count=args.expected_negative_count, window_pm=args.window_pm, d104_root=args.d104_root, port_id=args.port_id, deadline_s=args.deadline_seconds)
    except AuditStop as exc:
        print(json.dumps({"result": "STOP", "code": exc.code}, sort_keys=True))
        return 1
    except Exception as exc:
        print(json.dumps({"result": "STOP", "code": f"STOP_INTERNAL:{type(exc).__name__}"}, sort_keys=True))
        return 1
    print(json.dumps({"result": receipt["result"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
