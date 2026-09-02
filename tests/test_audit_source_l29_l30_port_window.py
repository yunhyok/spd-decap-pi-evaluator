import hashlib
import json
from pathlib import Path

import pytest
from shapely.geometry import LineString, box
from shapely.wkb import dumps

from tools.research.audit_source_l29_l30_port_window import AuditStop, audit_source_local_l29_l30_port_window


def _json(path: Path, value: dict) -> str:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _GuardSource:
    def __init__(self, path: Path):
        self.path = path

    def read_text(self, *args, **kwargs):
        raise AssertionError("source must be streamed, not read_text")

    def open(self, *args, **kwargs):
        return self.path.open(*args, **kwargs)


def test_source_local_port_window_real_schema_streams_and_fails_closed(tmp_path: Path) -> None:
    source_path = tmp_path / "board.spd"
    source_path.write_text(".Port\nPort44_SITE0::ADC_VDD_180_VQPS_SYS_1_AON/0 Auto GenFromCktInstance=\"SITE0\"\n+ PositiveTerminal $Package.Node1!!P::ADC_VDD_180_VQPS_SYS_1_AON/0\n+ PositiveTerminal $Package.Node2!!P::ADC_VDD_180_VQPS_SYS_1_AON/0\n+ PositiveTerminal $Package.Node3!!P::ADC_VDD_180_VQPS_SYS_1_AON/0\n+ NegativeTerminal $Package.Node4!!G::DGND\n+ $Package.Node5!!G::DGND\n+ NegativeTerminal $Package.Node6!!G::DGND\nPort45_SITE0::OTHER Auto\n+ PositiveTerminal $Package.Node99!!P::OTHER\n", encoding="utf-8", newline="\n")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    d103_sha = _json(tmp_path / "d103.json", {"source": {"sha256": source_sha}, "status": "PASS", "version": "v1"})
    artifact_root = tmp_path / "d104-root"
    artifact_root.mkdir()
    cells = []
    for ordinal in range(258, 274):
        if ordinal == 259:
            shape = box(0.0, 0.0, 0.5, 0.5)
            layer = "Signal$L29(DGND)"
            net = "DGND"
        elif ordinal == 260:
            shape = LineString([(1.0, 1.0), (1.0, 2.0)])
            layer = "Signal$L30(OTHER_POWER1)"
            net = "OTHER_POWER1"
        else:
            shape = box(100.0 + ordinal, 100.0, 101.0 + ordinal, 101.0)
            layer = "Signal$L28(AUX)" if ordinal < 259 else "Signal$L30(OTHER_POWER1)" if ordinal <= 266 else "Signal$L31(AUX)"
            net = "AUX" if ordinal not in range(260, 267) else "OTHER_POWER1"
        wkb = dumps(shape)
        filename = f"cell_{ordinal}.wkb"
        (artifact_root / filename).write_bytes(wkb)
        cells.append({"ordinal": ordinal, "layer": layer, "net": net, "island_id": "2db099..." if ordinal == 259 else "cb8510..." if ordinal == 264 else f"island-{ordinal}", "geometry": {"filename": filename, "bbox_um": list(shape.bounds), "wkb_sha256": hashlib.sha256(wkb).hexdigest(), "wkb_size_bytes": len(wkb)}})
    d104_sha = _json(tmp_path / "d104.json", {"source": {"sha256": source_sha}, "layer_counts": {"L29": 1, "L30": 7}, "cells": cells})
    bindings = [{"terminal_id": f"spd-terminal:{name}", "package_terminal_key": name, "role": "positive" if name.startswith("$Package.Node1") or name.startswith("$Package.Node2") or name.startswith("$Package.Node3") else "negative", "source_node_record_id": f"node:{name.split('.')[1].split('!!')[0]}", "landing_node": name.split('.')[1].split('!!')[0], "layer": "Signal$L30(OTHER_POWER1)", "pad_footprint": {"shape": "rect", "width_pm": 100, "height_pm": 100}, "finite_vertex_id": f"vertex-{name}", "finite_edge_id": f"edge-{name}", "via_owner_id": f"owner-{name}", "island_id": "2db099..." if name.endswith("Node1!!P::ADC_VDD_180_VQPS_SYS_1_AON/0") else "cb8510...", "component_id": "component-1"} for name in ("$Package.Node1!!P::ADC_VDD_180_VQPS_SYS_1_AON/0", "$Package.Node2!!P::ADC_VDD_180_VQPS_SYS_1_AON/0", "$Package.Node3!!P::ADC_VDD_180_VQPS_SYS_1_AON/0", "$Package.Node4!!G::DGND", "$Package.Node5!!G::DGND")]
    ir_sha = _json(tmp_path / "ir.json", {"manifest": {"source_sha256": source_sha}, "terminal_bindings": bindings})
    kwargs = {"source_path": _GuardSource(source_path), "d103_path": tmp_path / "d103.json", "d104_path": tmp_path / "d104.json", "ir_path": tmp_path / "ir.json", "output_path": tmp_path / "receipt-a.json", "expected_source_sha256": source_sha, "expected_d103_sha256": d103_sha, "expected_d104_sha256": d104_sha, "expected_ir_sha256": ir_sha, "expected_positive_count": 3, "expected_negative_count": 3, "window_pm": [0, 0, 1_000_000, 1_000_000], "d104_root": artifact_root, "port_id": "Port44_SITE0::ADC_VDD_180_VQPS_SYS_1_AON/0"}
    first = audit_source_local_l29_l30_port_window(**kwargs)
    kwargs["output_path"] = tmp_path / "receipt-b.json"
    second = audit_source_local_l29_l30_port_window(**kwargs)
    assert first == second and (tmp_path / "receipt-a.json").read_bytes() == (tmp_path / "receipt-b.json").read_bytes()
    assert first["result"] == "PASS_SOURCE_LOCAL_GAP_ONLY"
    assert [cell["ordinal"] for cell in first["d104_cells"]] == list(range(259, 267))
    assert first["d104_cells"][0]["intersects"] is True and first["d104_cells"][0]["boundary_touch"] is False
    assert first["d104_cells"][1]["intersects"] is True and first["d104_cells"][1]["boundary_touch"] is True
    assert first["d104_cells"][2]["intersects"] is False
    bad = json.loads((tmp_path / "ir.json").read_text())
    bad["terminal_bindings"][0]["package_terminal_key"] = "$Package.Node999!!P::wrong"
    bad_sha = _json(tmp_path / "bad-ir.json", bad)
    with pytest.raises(AuditStop, match="STOP_PORT_COMPILED_IDENTITY_UNPROVEN"):
        audit_source_local_l29_l30_port_window(**{**kwargs, "ir_path": tmp_path / "bad-ir.json", "expected_ir_sha256": bad_sha, "output_path": tmp_path / "bad.json"})
