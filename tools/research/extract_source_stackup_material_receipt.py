#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.23.1 source stackup/material receipt."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import mmap
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_SOURCE_ROOT = (_REPOSITORY_ROOT / "src").resolve()
_EXPECTED_PACKAGE_ROOT = (_REPOSITORY_SOURCE_ROOT / "spd_decap_pi").resolve()
sys.path.insert(0, str(_REPOSITORY_SOURCE_ROOT))

import spd_decap_pi as _runtime_package

_runtime_package_file = getattr(_runtime_package, "__file__", None)
_runtime_package_root = (
    Path(_runtime_package_file).resolve().parent
    if _runtime_package_file is not None
    else None
)
if _runtime_package_root != _EXPECTED_PACKAGE_ROOT:
    raise RuntimeError(
        "active-checkout import guard failed: expected spd_decap_pi from "
        f"{_EXPECTED_PACKAGE_ROOT}, imported {_runtime_package_root!s}"
    )

from spd_decap_pi._core.io.spd import (
    SpdDiagnostic,
    _find_line,
    _line_end,
    _parse_layers,
    _parse_materials,
)
from spd_decap_pi.canonical_json import concrete_canonical_json_bytes


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SOURCE_PATH = Path(r"D:\S4LB002-2Para_260729_1_injected.spd")
SOURCE_SIZE_BYTES = 1_116_717_287
SOURCE_SHA256 = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
TARGET_LAYERS = (
    "Signal$L29(DGND)",
    "Medium$DR2930",
    "Signal$L30(OTHER_POWER1)",
)


def _source_record_rows(provenance: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in provenance.get("source_records", ())]


def _stackup_rows(layers: tuple[Any, ...], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_rows = [dict(row) for row in provenance.get("stackup_layers", ())]
    if len(source_rows) != len(layers):
        raise ValueError("stackup provenance row count differs from parsed layers")
    depth = 0.0
    for ordinal, layer in enumerate(layers):
        thickness = float(layer.thickness_um)
        row = source_rows[ordinal]
        if row.get("layer_name") != str(layer.name) or row.get("thickness_um") != thickness:
            raise ValueError("stackup provenance identity differs from parsed layer")
        row["depth_from_stack_top_um"] = {"top": depth, "center": depth + thickness / 2.0, "bottom": depth + thickness}
        rows.append(row)
        depth += thickness
    return rows


def _dielectric_rows(layers: tuple[Any, ...], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    layer_ordinals = {str(layer.name): ordinal for ordinal, layer in enumerate(layers)}
    rows = [dict(row) for row in provenance.get("dielectric_points", ())]
    for row in rows:
        layer_name = str(row.get("layer_name", ""))
        if layer_name not in layer_ordinals:
            raise ValueError("dielectric provenance layer is absent from parsed stackup")
        row["layer_ordinal"] = layer_ordinals[layer_name]
    return rows


def extract_receipt(output_json: str | os.PathLike[str]) -> dict[str, Any]:
    output = Path(output_json).resolve()
    if not output.parent.is_dir():
        raise FileNotFoundError(f"output parent is absent: {output.parent}")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    try:
        stat = SOURCE_PATH.stat()
    except OSError as exc:
        raise FileNotFoundError(f"source is unavailable: {SOURCE_PATH}") from exc
    if stat.st_size != SOURCE_SIZE_BYTES:
        raise ValueError(f"source size mismatch: {stat.st_size}")

    diagnostics: list[SpdDiagnostic] = []
    provenance: dict[str, Any] = {"source_records": []}
    with SOURCE_PATH.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        observed_sha256 = sha256(data).hexdigest()
        if observed_sha256 != SOURCE_SHA256:
            raise ValueError(f"source SHA-256 mismatch: {observed_sha256}")
        layer_marker = data.find(b"* Layer description lines")
        node_marker = data.find(b"* Node description lines")
        via_marker = data.find(b"* Via description lines")
        pad_marker = data.find(b"* PadStack collection description lines")
        material_marker = data.find(b"* Material description lines")
        if min(layer_marker, node_marker, material_marker) < 0:
            raise ValueError("required SPD section marker is missing")
        material_start = material_marker
        material_end_marker = _find_line(data, b".EndMaterial", material_start)
        material_end = len(data) if material_end_marker < 0 else _line_end(data, material_end_marker, len(data))
        dielectrics, metals = _parse_materials(data, material_start, material_end, diagnostics, provenance)
        layer_start = layer_marker
        layer_end = node_marker if node_marker > layer_start else min((value for value in (via_marker, pad_marker, len(data)) if value > layer_start), default=len(data))
        layers = _parse_layers(data, layer_start, layer_end, {}, dielectrics, metals, set(), diagnostics, provenance)

    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    if errors:
        raise ValueError("SPD parser diagnostic error: " + "; ".join(item.code for item in errors))
    stackup = _stackup_rows(layers, provenance)
    names = [row["layer_name"] for row in stackup]
    if any(names.count(target) != 1 for target in TARGET_LAYERS):
        raise ValueError("target layer identity is not unique")
    indices = [names.index(target) for target in TARGET_LAYERS]
    if indices != [indices[0], indices[0] + 1, indices[0] + 2]:
        raise ValueError("target layers are not three consecutive source-order rows")
    neighborhoods = []
    for target, index in zip(TARGET_LAYERS, indices):
        neighborhoods.append(
            {
                "target_layer": target,
                "source_ordinal": index,
                "previous_source_order": stackup[index - 1] if index else None,
                "next_source_order": stackup[index + 1] if index + 1 < len(stackup) else None,
            }
        )
    receipt = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS",
        "source": {"path": str(SOURCE_PATH), "size_bytes": SOURCE_SIZE_BYTES, "sha256": SOURCE_SHA256},
        "derived_depth_basis": "cumulative source-order thickness; not absolute source z",
        "targets": neighborhoods,
        "stackup_layers": stackup,
        "dielectric_points": _dielectric_rows(layers, provenance),
        "source_records": _source_record_rows(provenance),
        "diagnostics": [{"severity": item.severity, "code": item.code, "message": item.message} for item in diagnostics],
    }
    receipt_bytes = concrete_canonical_json_bytes(receipt)
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
        temporary = Path(temporary_name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(receipt_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, output)
        temporary = None
        written = output.read_bytes()
        if len(written) != len(receipt_bytes) or sha256(written).hexdigest() != sha256(receipt_bytes).hexdigest() or written != receipt_bytes:
            raise ValueError("receipt reread verification failed")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}")
    parser.add_argument("output_json", help="new receipt JSON path; parent must already exist")
    args = parser.parse_args()
    receipt = extract_receipt(args.output_json)
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": receipt["status"], "output": args.output_json}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
