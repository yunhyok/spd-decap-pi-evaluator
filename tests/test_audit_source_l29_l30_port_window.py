import hashlib
import json
from pathlib import Path

import pytest

from tools.research.audit_source_l29_l30_port_window import AuditStop, audit_source_local_l29_l30_port_window


def _write_json(path: Path, value: dict) -> str:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_local_port_window_receipt_is_bounded_and_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "board.spd"
    source.write_text(".Port Port44\npositive: P1!!P P2!!P P3!!P\nnegative: G1!!G G2!!G G3!!G\n.End\n", encoding="utf-8", newline="\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    d103_sha = _write_json(tmp_path / "d103.json", {"source_sha256": source_sha, "receipt": "d103"})
    cells = [{"ordinal": ordinal, "layer": "L29" if ordinal == 259 else "L30", "logical_net": "DGND" if ordinal == 259 else "OTHER_POWER1", "artwork_net": "Signal$L29(DGND)" if ordinal == 259 else "Signal$L30(OTHER_POWER1)", "island_id": f"island-{ordinal}", "wkb_sha256": f"{ordinal:064x}", "bbox_pm": [0, 0, 5, 5] if ordinal == 259 else [ordinal - 260, ordinal - 260, ordinal - 259, ordinal - 259]} for ordinal in (259, 260, 261, 262, 263, 264, 265, 266)]
    d104_sha = _write_json(tmp_path / "d104.json", {"source_sha256": source_sha, "cells": cells})
    bindings = [{"terminal_id": name, "role": "positive" if name.startswith("P") else "negative", "source_path": [{"kind": "Node", "id": name.split("!!")[0]}, {"kind": "Via", "id": name}], "landing_node": name.split("!!")[0], "landing_layer": "L30", "pad_footprint": {"shape": "rect", "width_pm": 100, "height_pm": 100}, "finite_vertex": f"vertex-{name}", "finite_edge": f"edge-{name}", "owner_id": f"owner-{name}", "island_id": "island-selected", "component_id": "component-1"} for name in ("P1!!P", "P2!!P", "P3!!P", "G1!!G", "G2!!G")]
    ir_sha = _write_json(tmp_path / "ir.json", {"source_sha256": source_sha, "terminal_bindings": bindings})
    kwargs = {"source_path": source, "d103_path": tmp_path / "d103.json", "d104_path": tmp_path / "d104.json", "ir_path": tmp_path / "ir.json", "expected_source_sha256": source_sha, "expected_d103_sha256": d103_sha, "expected_d104_sha256": d104_sha, "expected_ir_sha256": ir_sha, "expected_positive_count": 3, "expected_negative_count": 3, "window_pm": [0, 0, 10, 10]}
    first = audit_source_local_l29_l30_port_window(output_path=tmp_path / "receipt-a.json", **kwargs)
    second = audit_source_local_l29_l30_port_window(output_path=tmp_path / "receipt-b.json", **kwargs)
    assert first["result"] == "PASS_SOURCE_LOCAL_GAP_ONLY"
    assert first["scope"] == {"source_local_shadow_only": True, "full_port_negative_paths_resolved": False, "whole_layer_raw_primitive_census": False, "solver_executed": False, "powersi_executed": False}
    assert first["port44"]["positive_terminals"] == ["P1!!P", "P2!!P", "P3!!P"]
    assert first["port44"]["negative_count"] == 3
    assert len(first["d104_cells"]) == 8 and [cell["ordinal"] for cell in first["d104_cells"]] == list(range(259, 267))
    assert first["d104_cells"][0]["boundary_touch"] is True
    assert (tmp_path / "receipt-a.json").read_bytes() == (tmp_path / "receipt-b.json").read_bytes()
    bad_ir = json.loads((tmp_path / "ir.json").read_text())
    bad_ir["terminal_bindings"][0]["terminal_id"] = "WRONG!!P"
    bad_ir_path = tmp_path / "bad-ir.json"
    bad_ir_sha = _write_json(bad_ir_path, bad_ir)
    with pytest.raises(AuditStop, match="STOP_TERMINAL_MISMATCH"):
        audit_source_local_l29_l30_port_window(output_path=tmp_path / "bad-receipt.json", ir_path=bad_ir_path, expected_ir_sha256=bad_ir_sha, **{key: value for key, value in kwargs.items() if key not in {"ir_path", "expected_ir_sha256", "output_path"}})
