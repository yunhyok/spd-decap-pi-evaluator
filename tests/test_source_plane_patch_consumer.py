from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np
import pytest

from spd_decap_pi import source_plane_patch_consumer as consumer
from spd_decap_pi import spd_adapter
from spd_decap_pi._core.solver.layerwise_network import compile_layerwise_substrate
from spd_decap_pi._core.solver.global_mna import NodalAdmittanceBlock
from test_io_spd import MINI_SPD
from spd_decap_pi.raw_spatial_contact_asset import (
    RawSpatialLayerRow,
    RawSpatialNodeRow,
    RawSpatialPadShapeRow,
    RawSpatialPadstackRow,
    RawSpatialSectionCoverageRow,
    RawSpatialSourceCoverageRow,
    RawSpatialSurfaceRow,
    RawSpatialViaRow,
    build_raw_spatial_contact_asset,
    load_raw_spatial_contact_asset,
)
from spd_decap_pi.source_plane_ownership_ir import build_source_plane_ownership_ir, load_source_plane_ownership_ir
from test_source_plane_ownership_ir import draft


def _h(ch: str) -> str:
    assert len(ch) == 1 and ch in "0123456789abcdef"
    return ch * 64


def _empty_scenario_binding(substrate):
    from spd_decap_pi.layerwise_scenario_topology import compile_layerwise_scenario_network
    from spd_decap_pi.scenario_topology_plan import ScenarioTopologyPlan, SCENARIO_TOPOLOGY_PLAN_SCHEMA

    source_sha = substrate.provenance["source_sha256"]
    certificate_sha = next(
        substrate.provenance[key]
        for key in ("surface_connectivity_evidence_sha256", "finite_route_certificate_sha256", "finite_via_certificate_sha256")
        if substrate.provenance.get(key)
    )
    empty = ScenarioTopologyPlan(
        schema_version=SCENARIO_TOPOLOGY_PLAN_SCHEMA,
        source_sha256=source_sha,
        connection_evidence_sha256=_h("a"),
        source_contact_manifest_sha256=_h("b"),
        retarget_route_manifest_sha256=_h("c"),
        finite_route_certificate_sha256=certificate_sha,
        terminal_nodes=(), active_topology_links=(), active_retarget_routes=(),
        cap_body_requests=(), suppressed_base_cut_ids=(), source_owner_partition=(),
        plan_sha256=_h("d"),
    )
    payload = asdict(empty)
    payload.pop("plan_sha256")
    empty = replace(empty, plan_sha256=sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest())
    return compile_layerwise_scenario_network(base_substrate=substrate, plan=empty, cap_models={})


def _assets(tmp_path):
    source = b"NNNN" + b" " * 146 + b"VV"
    path = tmp_path / "coupon.spd"
    path.write_bytes(source)
    source_sha = sha256(source).hexdigest()
    coverage = (
        RawSpatialSectionCoverageRow(0, "Node", 0, 4, 4, sha256(source[:4]).hexdigest(), 4, 4, 4, 4, 0, 4, 0),
        RawSpatialSectionCoverageRow(1, "Trace", 4, 4, 0, sha256(b"").hexdigest(), 0, 0, 0, 0, 0, 0, 0),
        RawSpatialSectionCoverageRow(2, "Via", 4, 150, 146, sha256(source[4:150]).hexdigest(), 2, 2, 2, 2, 0, 2, 0),
    )
    records = [_h(format(i, "x")) if i < 16 else sha256(f"record-{i}".encode()).hexdigest() for i in range(20)]
    nodes = (
        RawSpatialNodeRow(0, "NP", "VDD", "EXPLICIT", "L3", 500000, 500000, "PS", 0, records[0]),
        RawSpatialNodeRow(1, "NP2", "VDD", "EXPLICIT", "L1", 500000, 500000, "PS", 0, records[14]),
        RawSpatialNodeRow(2, "NG", "GND", "EXPLICIT", "L3", 500000, 500000, "PS", 0, records[2]),
        RawSpatialNodeRow(3, "NG2", "GND", "EXPLICIT", "L1", 500000, 500000, "PS", 0, records[1]),
    )
    vias = (
        RawSpatialViaRow(0, "via-a", "VDD", "L1", "L3", "NP2", "NP", "PS", "EXACT", "via:vdd:via-a", 500000, 500000, 500000, 500000, 0, records[6]),
        RawSpatialViaRow(1, "via-b", "GND", "L1", "L3", "NG2", "NG", "PS", "EXACT", "via:gnd:via-b", 500000, 500000, 500000, 500000, 0, records[7]),
    )
    square = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 2000.0), (0.0, 2000.0))
    cutout = ((2000.0, 2000.0), (2100.0, 2000.0), (2100.0, 2100.0), (2000.0, 2100.0))
    payload = {
        "plane_primitives": [
            {"primitive_ordinal": 0, "layer_ordinal": 0, "layer_name": "L1", "net_name": "VDD", "polarity": "+", "kind": "polygon", "source_asset_name": "geometry/p", "source_asset_sha256": _h("4"), "coordinate_unit": "um", "primitive_sha256": _h("b")},
            {"primitive_ordinal": 1, "layer_ordinal": 0, "layer_name": "L1", "net_name": "VDD", "polarity": "-", "kind": "polygon", "source_asset_name": "geometry/p", "source_asset_sha256": _h("4"), "coordinate_unit": "um", "primitive_sha256": _h("b")},
            {"primitive_ordinal": 2, "layer_ordinal": 0, "layer_name": "L1", "net_name": "VDD", "polarity": "+", "kind": "circle", "source_asset_name": "geometry/p", "source_asset_sha256": _h("4"), "coordinate_unit": "um", "primitive_sha256": _h("b")},
            {"primitive_ordinal": 3, "layer_ordinal": 2, "layer_name": "L3", "net_name": "GND", "polarity": "+", "kind": "polygon", "source_asset_name": "geometry/g", "source_asset_sha256": _h("6"), "coordinate_unit": "um", "primitive_sha256": _h("b")},
        ],
        "plane_vertices": [{"primitive_ordinal": i, "vertex_ordinal": j, "x_um": x, "y_um": y} for i, coords in ((0, square), (3, square), (1, cutout)) for j, (x, y) in enumerate(coords)],
        "plane_circles": [{"primitive_ordinal": 2, "center_x_um": 3000.0, "center_y_um": 3000.0, "radius_um": 10.0}],
        "stackup_layers": [
            {"layer_ordinal": 0, "layer_name": "L1", "layer_kind": "conductor", "thickness_um": 35.0, "conductivity_s_per_m": 58000000.0, "material_name": "Copper"},
            {"layer_ordinal": 1, "layer_name": "L2", "layer_kind": "dielectric", "thickness_um": 100.0, "conductivity_s_per_m": None, "material_name": "FR4"},
            {"layer_ordinal": 2, "layer_name": "L3", "layer_kind": "conductor", "thickness_um": 35.0, "conductivity_s_per_m": 58000000.0, "material_name": "Copper"},
        ],
        "dielectric_points": [{"layer_ordinal": 1, "point_ordinal": 0, "frequency_hz": 1.0e9, "epsilon_r": 4.0, "loss_tangent": 0.01}],
    }
    def primitive_sha(polarity, kind, value):
        token = ("positive_" if polarity == "+" else "negative_") + kind
        payload_value = {"vertices": tuple(value), "kind": token} if kind == "polygon" else {"circle": tuple(value), "kind": token}
        return sha256(json.dumps(payload_value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for index, polarity, points in ((0, "+", square), (1, "-", cutout), (3, "+", square)):
        payload["plane_primitives"][index]["primitive_sha256"] = primitive_sha(polarity, "polygon", points)
    payload["plane_primitives"][2]["primitive_sha256"] = primitive_sha("+", "circle", (3000.0, 3000.0, 10.0))
    raw_manifest, raw_asset = build_raw_spatial_contact_asset(
        source_path=path, source_sha256=source_sha, project_binding_sha256=_h("d"), certificate_evidence_sha256=_h("e"), compiled_topology_identity_sha256=_h("f"),
        source_coverage=RawSpatialSourceCoverageRow(0, path.name, len(source), source_sha, 6, 6), section_coverage=coverage,
        layers=(RawSpatialLayerRow(0, "L1", True, records[8]), RawSpatialLayerRow(1, "L2", False, records[9]), RawSpatialLayerRow(2, "L3", True, records[10])),
        padstacks=(RawSpatialPadstackRow(0, "PS", 50000, "Copper", records[11]),), pad_shapes=(RawSpatialPadShapeRow(0, "PS", "L1", "RECTANGLE", 100000, 100000, records[12]), RawSpatialPadShapeRow(1, "PS", "L3", "RECTANGLE", 100000, 100000, records[14])),
        surfaces=(RawSpatialSurfaceRow(0, "surface:p", "VDD", "L1", _h("4"), _h("5"), records[12], 0, 0, 1000000000, 2000000000), RawSpatialSurfaceRow(1, "surface:g", "GND", "L3", _h("6"), _h("7"), records[13], 0, 0, 1000000000, 2000000000)), nodes=nodes, traces=(), vias=vias, plane_sheet_payload=payload,
    )
    ir = deepcopy(draft())
    ir.update(source_size_bytes=len(source), source_sha256=source_sha, project_binding_sha256=raw_manifest["project_binding_sha256"], certificate_evidence_sha256=raw_manifest["certificate_evidence_sha256"], compiled_topology_identity_sha256=raw_manifest["compiled_topology_identity_sha256"], raw_geometry_identity_sha256=raw_manifest["geometry_identity_sha256"], raw_logical_rows_sha256=raw_manifest["logical_rows_sha256"], raw_plane_sheet_sha256=raw_manifest["plane_sheet_payload_sha256"])
    ir["raw_manifest_sha256"] = sha256(consumer.concrete_canonical_json_bytes(dict(raw_manifest))).hexdigest()
    ir["surfaces"][1].update(layer="L3", geometry_asset_sha256=_h("6"))
    ir["surfaces"][0]["artwork_net"] = "VDD"
    ir["surfaces"][1]["artwork_net"] = "GND"
    ir["primitives"][3].update(surface_id="surface:g", source_asset_sha256=_h("6"), raw_primitive_ordinal=3)
    for primitive in ir["primitives"][:3]: primitive["source_asset_sha256"] = _h("4")
    for primitive in ir["primitives"]:
        primitive["primitive_sha256"] = payload["plane_primitives"][int(primitive["raw_primitive_ordinal"])]["primitive_sha256"]
    ir["rail_bindings"][1].update(layer="L3", surface_id="surface:g", island_id="island:g")
    ir["rail_bindings"][0]["artwork_net"] = "VDD"
    ir["rail_bindings"][1]["artwork_net"] = "GND"
    ir["plane_owner_scopes"][0]["artwork_net"] = "VDD"
    ir["terminal_bindings"][0].update(padstack_id="PS", via_owner_id="via:via-a", raw_pad_shape_sha256=records[12])
    ir["terminal_bindings"][0].update(endpoint_node_id="NP2", layer="L1", branch_id="branch:0")
    ir["terminal_bindings"][1].update(layer="L3", padstack_id="PS", via_owner_id="via:via-b", endpoint_node_id="NG", branch_id="branch:0", raw_pad_shape_sha256=records[14])
    ir["retained_owner_refs"][0]["owner_id"] = "via:via-a"
    ir["retained_owner_refs"][1]["owner_id"] = "via:via-b"
    ir["replacement_ledger_members"][1]["owner_id"] = "via:via-a"
    ir["replacement_ledger_members"][2]["owner_id"] = "via:via-b"
    ir["replacement_ledger"][0]["retained_set_sha256"] = sha256(consumer.concrete_canonical_json_bytes(["via:via-a", "via:via-b"])).hexdigest()
    for record in ir["source_records"]:
        if record["record_id"] in {"paddef-p", "regular-p"}: record["layer"] = "L1"
        if record["record_id"] in {"paddef-g", "regular-g"}: record["layer"] = "L3"
    ir["plane_owner_scopes"].append({"ordinal": 1, "scope_id": "scope:g", "namespace": "plane-ground", "compiler_owner_id": "PlaneOwnerG", "rail_id": "RAIL/0", "role": "ground", "artwork_net": "GND", "layer": "L3", "state": "declared_unconsumed", "owner_count": 1})
    ir["replacement_ledger_members"].append({"ledger_id": "ledger:0", "owner_id": "PlaneOwnerG", "action": "replaced"})
    ir["replacement_ledger"][0].update(replaced_count=2, replaced_set_sha256=sha256(consumer.concrete_canonical_json_bytes(["planeowner0", "planeownerg"])).hexdigest())
    ir["stackup_layers"].append({**ir["stackup_layers"][0], "ordinal": 2, "layer_name": "L3", "raw_layer_ordinal": 2, "raw_layer_sha256": sha256(json.dumps(payload["stackup_layers"][2], sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
    for row, raw in zip(ir["stackup_layers"][:2], payload["stackup_layers"][:2]): row["raw_layer_sha256"] = sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ir["dielectric_points"][0].update(raw_dielectric_ordinal=0, raw_dielectric_sha256=sha256(json.dumps(payload["dielectric_points"][0], sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    own_manifest, own_asset = build_source_plane_ownership_ir(ir)
    return path, raw_manifest, raw_asset, own_manifest, own_asset


def test_real_v3_loader_roundtrip(tmp_path):
    path, raw_manifest, raw_asset, own_manifest, own_asset = _assets(tmp_path)
    with load_raw_spatial_contact_asset(raw_manifest, {raw_asset[0]: raw_asset[1]}, expected_source_sha256=raw_manifest["source_sha256"], expected_project_binding_sha256=raw_manifest["project_binding_sha256"], expected_certificate_evidence_sha256=raw_manifest["certificate_evidence_sha256"], expected_compiled_topology_identity_sha256=raw_manifest["compiled_topology_identity_sha256"], expected_geometry_identity_sha256=raw_manifest["geometry_identity_sha256"], require_plane_sheet_payload=True) as raw: assert tuple(raw.iter_stackup_layers())
    with load_source_plane_ownership_ir(own_manifest, {own_asset[0]: own_asset[1]}, expected_source_sha256=own_manifest["source_sha256"], expected_project_binding_sha256=own_manifest["project_binding_sha256"], expected_certificate_evidence_sha256=own_manifest["certificate_evidence_sha256"], expected_compiled_topology_identity_sha256=own_manifest["compiled_topology_identity_sha256"], expected_raw_manifest_sha256=own_manifest["raw_manifest_sha256"], expected_raw_geometry_identity_sha256=own_manifest["raw_geometry_identity_sha256"], expected_raw_logical_rows_sha256=own_manifest["raw_logical_rows_sha256"], expected_raw_plane_sheet_sha256=own_manifest["raw_plane_sheet_sha256"], expected_app_version="0.23.1") as ir: assert len(tuple(ir.iter_section("terminal_bindings"))) == 2
    kwargs = {"rail_id": "RAIL/0", "frequency_hz": 1.0e9, "cell_um": 1000.0}
    first = consumer.consume_source_plane_patch(own_manifest, {own_asset[0]: own_asset[1]}, raw_manifest, {raw_asset[0]: raw_asset[1]}, **kwargs)
    second = consumer.consume_source_plane_patch(own_manifest, {own_asset[0]: own_asset[1]}, raw_manifest, {raw_asset[0]: raw_asset[1]}, **kwargs)
    assert first == second and first["shadow_only"] is True and first["condensation"]["admittance_s"]
    assert first["analytic"]["rdc_ohm"] == pytest.approx((2.0e-3 / 1.0e-3) * (2.0 / (58.0e6 * 35.0e-6)), rel=1e-10)
    assert first["analytic"]["inductance_h"] == pytest.approx(4.0e-7 * 3.141592653589793 * 100.0e-6 * 2.0e-3 / 1.0e-3, rel=1e-10)
    assert first["analytic"]["capacitance_f"] == pytest.approx(8.8541878128e-12 * 4.0 * 2.0e-6 / 100.0e-6, rel=1e-10)
    assert path.read_bytes() == b"NNNN" + b" " * 146 + b"VV"


def test_real_v3_identity_tamper_fails_closed(tmp_path):
    _path, raw_manifest, raw_asset, own_manifest, own_asset = _assets(tmp_path)
    with pytest.raises(consumer.SourcePlanePatchError): consumer.consume_source_plane_patch(own_manifest, {own_asset[0]: own_asset[1]}, {**raw_manifest, "geometry_identity_sha256": _h("e")}, {raw_asset[0]: raw_asset[1]}, rail_id="RAIL/0", frequency_hz=1.0e9, cell_um=1000.0)


def _v2_import(tmp_path: Path, *, outside: bool = False):
    payload = (
        MINI_SPD.replace("LEGACY_SOURCE_GRAPH_UNAVAILABLE", "TRACE_VIA_COMPONENTS_AVAILABLE")
        .replace("VDD_CORE/0", "VDD_CORE/1")
        .replace("Node1!!101::VDD_CORE/1 X = 0mm Y = 0mm", "Node1!!101::VDD_CORE/1 X = 0.5mm Y = 0mm")
        .replace("Node1!!101::VDD_CORE/1 X = 0.5mm Y = 0mm Layer = Signal$TOP PadStack = DUT", "Node1!!101::VDD_CORE/1 X = 0.5mm Y = 0mm Layer = Signal$TOP PadStack = DR-0102_60")
        .replace("Node2!!102::DGND X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = DUT", "Node2!!102::DGND X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = DR-0102_60")
        .replace("Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP", "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\nNode7!!7::VDD_CORE/1 X = 0.5mm Y = 0mm Layer = Signal$PWR PadStack = DR-0102_60\nNode8!!8::VDD_CORE/1 X = 1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60\nNode9!!9::DGND X = 0.1mm Y = 0mm Layer = Signal$GND PadStack = DR-0102_60\nNode10!!10::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60\nNode11!!11::VDD_CORE/1 X = 1.5mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60\nNode12!!12::VDD_CORE/1 X = 1.5mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60")
        .replace("Via1::VDD_CORE/1 UpperNode = Node1 LowerNode = Node3 PadStack = DR-0102_60", "Via1::VDD_CORE/1 UpperNode = Node1 LowerNode = Node7 PadStack = DR-0102_60")
        .replace("Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60", "Via2::DGND UpperNode = Node2 LowerNode = Node9 PadStack = DR-0102_60\nVia7::VDD_CORE/1 UpperNode = Node3 LowerNode = Node8 PadStack = DR-0102_60\nVia9::DGND UpperNode = Node4 LowerNode = Node10 PadStack = DR-0102_60\nVia11::VDD_CORE/1 UpperNode = Node12 LowerNode = Node11 PadStack = DR-0102_60 AbsoluteRotation = 4.5")
        .replace("* Via description lines", "* Trace description lines\nTrace11::VDD_CORE/1 StartingNode = Node1 EndingNode = Node12 Width = 0.10mm\n* Via description lines")
        .replace("Medium$D1 Thickness = 0.10mm Material = ABF", "Medium$D1 Thickness = 0.10mm Material = abf")
        .replace(".PadDef Signal$PWR\nRegular Circle 0.03mm\n.EndPadDef", ".PadDef Signal$PWR\nRegular Circle 0.03mm\n.EndPadDef\n.PadDef Signal$GND\nRegular Circle 0.03mm\n.EndPadDef")
    )
    if outside:
        payload = payload.replace("X = 1.5mm Y = 2mm", "X = 3.995mm Y = 0mm")
    source = tmp_path / "v2-consumer.spd"
    source.write_text(payload, encoding="ascii")
    return spd_adapter.import_spd_scenario(source, source_plane_ownership_rail_id="VDD_CORE/1")


def test_v2_contact_admissibility_and_atomic_rejection(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    result = consumer.evaluate_source_plane_contact_admissibility(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1")
    admissibility = result["contacts"]
    assert result["status"] == "complete"
    assert {row["owner_kind"] for row in admissibility} >= {"device", "decap", "other"}
    assert all(float(row["covered_area_m2"]) > 0.0 for row in admissibility)
    outside = _v2_import(tmp_path, outside=True)
    outside_project = outside.scenario.base_project
    outside_own = outside_project.metadata["spd_import"]["source_plane_ownership_ir"]
    outside_raw = outside_project.metadata["spd_import"]["raw_spatial_contact_asset"]
    with pytest.raises(consumer.SourcePlanePatchError, match="CONTACT_NOT_FULLY_COVERED"):
        consumer.evaluate_source_plane_contact_admissibility(outside_own, outside.attachments, outside_raw, outside.attachments, rail_id="VDD_CORE/1")


def test_v2_contact_complete_shadow_nport_condensation(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    kwargs = {"rail_id": "vdd_core/1", "frequency_hz": 1.0e9, "cell_um": 1000.0}
    p0 = consumer.evaluate_source_plane_contact_admissibility(own, imported.attachments, raw, imported.attachments, rail_id=kwargs["rail_id"])
    first = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, **kwargs)
    second = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, **kwargs)
    assert first == second and first["shadow_only"] is True and first["status"] == "complete"
    assert len(first["contact_ids"]) == len(first["owner_kinds"]) >= 3
    assert first["contact_ids"] == [row["contact_id"] for row in p0["contacts"]]
    assert {str(kind).casefold() for kind in first["owner_kinds"]} >= {"device", "decap", "other"}
    assert len(first["admittance_s"]) == len(first["contact_ids"])
    assert all(len(row) == len(first["contact_ids"]) for row in first["admittance_s"])
    assert all(len(value) == 2 and all(math.isfinite(float(item)) for item in value) for row in first["admittance_s"] for value in row)
    assert first["input_sha256"] == second["input_sha256"]
    assert len(first["terminal_constraint_matrix"]) == len(first["contact_ids"])
    assert first["terminal_constraint_matrix"] and len({len(row) for row in first["terminal_constraint_matrix"]}) == 1
    assert all(row and all(math.isfinite(float(value)) for value in row) for row in first["terminal_constraint_matrix"])
    assert all(math.isfinite(float(value)) for value in first["diagnostics"].values())


def test_source_plane_patch_owner_off_shadow_audit_mini_spd(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    first = consumer.audit_source_plane_patch_owner_off(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert first["shadow_only"] is True and first["replacement_ready"] is False
    assert first["status"] == "candidate_identified" and first["incident_edges"]
    assert len({item["fingerprint"] for item in first["incident_edges"]}) == len(first["incident_edges"])
    assert first["source_sha256"] == patch["source_sha256"] == raw["source_sha256"]
    assert set(first["component_identity"]) == {"power", "ground"}
    assert all(len(first["component_identity"][role]["islands"]) >= 1 for role in ("power", "ground"))
    assert set(first["terminal_anchor_identity"]) == {"power", "ground"}
    assert all(first["terminal_anchor_identity"][role]["anchor_node_ids"] and first["terminal_anchor_identity"][role]["expected_port_node_id"] for role in ("power", "ground"))
    assert len(first["candidate_scopes"]) == 2
    assert len(first["candidate_replaced_owner_ids"]) == 2
    assert first["retained_owner_count"] >= 2 and len(first["retained_owner_ids_sha256"]) == 64
    assert len(first["old_edge_set_sha256"]) == len(first["contact_map_sha256"]) == len(first["audit_sha256"]) == 64
    original_attachment_items = dict(imported.attachments)
    original_network_inventory = (substrate.network.surface_node_ids, substrate.network.via_links, substrate.network.partials, substrate.network.ports)
    join = consumer.audit_source_plane_patch_production_owner_join(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert join["status"] == "complete" and join["shadow_only"] is True
    assert join["replacement_ready"] is False and join["production_ready"] is False
    assert join["p2_audit_sha256"] == first["audit_sha256"]
    assert [row["contact_id"] for row in join["source_p1_contact_rows"]] == patch["contact_ids"]
    assert {row["role"] for row in join["source_p1_contact_rows"]} == {"power", "ground"}
    assert join["source_p1_contact_rows_sha256"] == sha256(consumer.concrete_canonical_json_bytes(join["source_p1_contact_rows"])).hexdigest()
    old_rows = join["production_old_maxwell_rows"]
    assert old_rows and len({row["fingerprint"] for row in old_rows}) == len(old_rows)
    assert sha256(consumer.concrete_canonical_json_bytes(sorted(row["fingerprint"] for row in old_rows))).hexdigest() == join["old_edge_set_sha256"]
    assert sha256(consumer.concrete_canonical_json_bytes(old_rows)).hexdigest() == join["production_old_maxwell_rows_sha256"]
    assert all(type(row["partial_ordinal"]) is int and type(row["upper_reduced_index"]) is int and type(row["lower_reduced_index"]) is int and type(row["aggregation_count"]) is int and row["partial_ordinal"] >= 0 and row["upper_reduced_index"] >= 0 and row["lower_reduced_index"] >= 0 and float.fromhex(row["capacitance_f_hex"]) > 0.0 and row["aggregation_count"] >= 1 for row in old_rows)
    grouped = {}
    for row in old_rows:
        key = (row["partial_ordinal"], row["upper_reduced_index"], row["lower_reduced_index"])
        grouped[key] = grouped.get(key, 0) + 1
    assert all(row["aggregation_count"] == grouped[(row["partial_ordinal"], row["upper_reduced_index"], row["lower_reduced_index"])] for row in old_rows)
    replaced = {item.casefold() for item in join["replaced_owner_ids"]}; retained = {item.casefold() for item in join["retained_owner_ids"]}
    assert not replaced & retained and join["replaced_owner_ids_sha256"] == sha256(consumer.concrete_canonical_json_bytes([item.casefold() for item in join["replaced_owner_ids"]])).hexdigest()
    assert join["retained_owner_ids_sha256"] == sha256(consumer.concrete_canonical_json_bytes([item.casefold() for item in join["retained_owner_ids"]])).hexdigest()
    assert join["action_ledger_sha256"] == sha256(consumer.concrete_canonical_json_bytes(join["action_ledger"])).hexdigest()
    report_without_sha = dict(join); report_without_sha.pop("final_report_sha256")
    assert join["final_report_sha256"] == sha256(consumer.concrete_canonical_json_bytes(report_without_sha)).hexdigest()
    assert dict(imported.attachments) == original_attachment_items and (substrate.network.surface_node_ids, substrate.network.via_links, substrate.network.partials, substrate.network.ports) == original_network_inventory
    with pytest.raises(consumer.SourcePlanePatchError):
        consumer.audit_source_plane_patch_owner_off(own, imported.attachments, raw, {**patch, "raw_manifest_sha256": _h("a")}, substrate, rail_id="VDD_CORE/1")
    tampered_provenance = {**dict(substrate.provenance), "raw_spatial_v3_manifest_sha256": _h("b")}
    with pytest.raises(consumer.SourcePlanePatchError):
        consumer.audit_source_plane_patch_owner_off(own, imported.attachments, raw, patch, replace(substrate, provenance=tampered_provenance), rail_id="VDD_CORE/1")


def test_source_plane_patch_contact_quotient_representability(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    first = consumer.audit_source_plane_patch_contact_quotient_representability(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    second = consumer.audit_source_plane_patch_contact_quotient_representability(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert first == second
    assert first["shadow_only"] is True and first["replacement_ready"] is False
    assert first["status"] == "stopped" and first["code"] == "CONTACT_INTERFACE_RANK_LOSS"
    assert first["quotient_representable"] is False and first["residual_norm_2"] > first["threshold"]
    assert len(first["contact_mapping"]) == len(patch["contact_ids"])
    assert len(first["B"]) == 2 and all(len(row) == len(patch["contact_ids"]) for row in first["B"])
    assert first["p1_input_sha256"] == patch["input_sha256"] and len(first["p1_output_sha256"]) == len(first["projector_sha256"]) == len(first["p2_audit_sha256"]) == 64
    with pytest.raises(consumer.SourcePlanePatchError, match="CONTACT_QUOTIENT_INVALID"):
        consumer.audit_source_plane_patch_contact_quotient_representability(own, imported.attachments, raw, {**patch, "admittance_s": []}, substrate, rail_id="VDD_CORE/1")


def test_source_plane_patch_selected_base_cutset_is_closed(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    first = consumer.audit_source_plane_patch_selected_base_cutset(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    second = consumer.audit_source_plane_patch_selected_base_cutset(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert first == second and first["status"] == "closed" and first["shadow_only"] is True
    assert first["split_ready"] is False and first["replacement_ready"] is False
    assert set(first["component_identity"]) == {"power", "ground"}
    assert first["source_sha256"] == raw["source_sha256"]
    assert len(first["p3_input_identity_sha256"]) == len(first["p2_audit_sha256"]) == len(first["substrate_identity_sha256"]) == 64
    assert len(first["base_cutset_sha256"]) == len(first["old_edge_set_sha256"]) == 64
    assert first["contact_mapping"] and all(item["finite_edge_id"] and item["owner_ids"] for item in first["contact_mapping"])
    expected_edges = {str(item["finite_edge_id"]).casefold() for item in first["contact_mapping"]}
    finite = tuple(link for link in substrate.network.via_links if str(link.mode) == "finite_parallel_rl" and str(link.link_id).casefold() in expected_edges)
    assert finite
    extra = replace(finite[0], link_id=f"{finite[0].link_id}-extra", owner_ids=("via:extra-owner",))
    extra_finite = (substrate.network.reduced_node_index(extra.first_node_id), substrate.network.reduced_node_index(extra.second_node_id), extra)
    extra_network = replace(substrate.network, via_links=(*substrate.network.via_links, extra), _finite_links=(*substrate.network._finite_links, extra_finite))
    stopped = consumer.audit_source_plane_patch_selected_base_cutset(own, imported.attachments, raw, patch, replace(substrate, network=extra_network), rail_id="VDD_CORE/1")
    assert stopped["status"] == "stopped" and stopped["code"] == "BASE_CUTSET_CONTACT_EDGE_MISSING_OR_EXTRA"


def test_source_plane_patch_shadow_contact_rewire_plan(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    original_nodes, original_links = substrate.network.surface_node_ids, substrate.network.via_links
    original_partials, original_ports = substrate.network.partials, substrate.network.ports
    first = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    second = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert first == second and first["status"] == "planned" and first["shadow_only"] is True
    assert first["production_ready"] is False and first["replacement_ready"] is False and first["old_selected_external_degree_after_plan"] == 0
    interfaces = [row["new_interface_node_id"] for row in first["rewire_rows"]]
    assert len(interfaces) == len(set(item.casefold() for item in interfaces)) and not set(item.casefold() for item in interfaces) & set(item.casefold() for item in original_nodes)
    original_by_id = {str(link.link_id).casefold(): link for link in original_links}
    for row in first["rewire_rows"]:
        link = original_by_id[row["old_finite_edge_id"].casefold()]
        assert row["external_endpoint"] in {link.first_node_id, link.second_node_id} and row["count"] == link.count and row["mode"] == link.mode and row["owner_ids"] == list(link.owner_ids)
        assert row["resistance_ohm_per_via_hex"] == link.resistance_ohm_per_via.hex() and row["inductance_h_per_via_hex"] == link.inductance_h_per_via.hex()
    disabled = first["disabled_old_edge_fingerprints"]
    assert disabled and disabled == sorted(set(disabled)) and sha256(consumer.concrete_canonical_json_bytes(disabled)).hexdigest() == first["old_edge_set_sha256"]
    assert [row["contact_id"] for row in first["rewire_rows"]] == patch["contact_ids"] and len(first["rewire_rows"]) == len(patch["contact_ids"])
    assert first["planned_stamp"]["owner_ids"] == first["planned_block_owner_ids"]
    assert not set(item.casefold() for item in first["planned_block_owner_ids"]) & set(item.casefold() for item in first["retained_contact_finite_owner_ids"])
    assert len(first["shadow_split_sha256"]) == 64
    assert substrate.network.surface_node_ids is original_nodes and substrate.network.via_links is original_links and substrate.network.partials is original_partials and substrate.network.ports is original_ports
    bad_patch = {**patch, "diagnostics": {**patch["diagnostics"], "passivity_min_eigenvalue_s": -1.0, "passivity_tolerance_s": 0.0}}
    stopped = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, bad_patch, substrate, rail_id="VDD_CORE/1")
    assert stopped["status"] == "stopped" and stopped["code"] == "SHADOW_REWIRE_PASSIVITY_INVALID"


def test_source_plane_patch_shadow_rewire_commutes_with_scenario_binding(tmp_path: Path):
    from spd_decap_pi._core.solver.layer_surface_network import compile_layer_surface_network

    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    rewire_plan = consumer.plan_source_plane_patch_shadow_contact_rewire(
        own, imported.attachments, raw,
        consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0),
        substrate, rail_id="VDD_CORE/1",
    )
    binding = _empty_scenario_binding(substrate)
    first = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire_plan, substrate, binding, rail_id="VDD_CORE/1")
    second = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire_plan, substrate, binding, rail_id="VDD_CORE/1")
    assert first == second and first["shadow_only"] is True
    assert first["production_ready"] is False and first["replacement_ready"] is False
    assert len(first["shadow_split_sha256"]) == len(first["scenario_commutation_sha256"]) == 64
    assert first["status"] == "passed" and first["code"] is None
    assert len(first["port_termination_boundary_sha256"]) == 64
    edge_id = str(rewire_plan["rewire_rows"][0]["old_finite_edge_id"]).casefold()
    links = tuple(link for link in binding.network.via_links if link.link_id.casefold() != edge_id)
    network = compile_layer_surface_network(binding.network.surface_node_ids, partials=binding.network.partials, via_links=links, ports=binding.network.ports)
    stopped_binding = replace(binding, network=network)
    stopped = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire_plan, substrate, stopped_binding, rail_id="VDD_CORE/1")
    assert stopped["status"] == "stopped" and stopped["code"] == "SCENARIO_REWIRE_SOURCE_EDGE_SUPPRESSED"


def test_source_plane_patch_shadow_local_replacement_recipe_is_atomic(tmp_path: Path):
    from spd_decap_pi._core.solver.layer_surface_network import compile_layer_surface_network

    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    binding = _empty_scenario_binding(substrate)
    original_nodes, original_links, original_partials, original_ports = (substrate.network.surface_node_ids, substrate.network.via_links, substrate.network.partials, substrate.network.ports)
    commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire, substrate, binding, rail_id="VDD_CORE/1")
    first = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, rewire, commutation, binding, rail_id="VDD_CORE/1")
    second = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, rewire, commutation, binding, rail_id="VDD_CORE/1")
    assert first == second and first["status"] == "passed" and first["shadow_only"] is True
    assert first["production_ready"] is False and first["replacement_ready"] is False
    assert first["remove_old_maxwell"] and first["rewire_finite"] and first["owner_ledger"]
    assert len(first["deterministic_recipe_sha256"]) == 64
    assert [row["contact_id"] for row in first["rewire_finite"]] == list(patch["contact_ids"])
    assert first["owner_ledger"]["removed_old_block_owner_ids"] == rewire["planned_block_owner_ids"]
    assert first["owner_ledger"]["added_p1_block_owner_ids"] == rewire["planned_block_owner_ids"]
    assert first["owner_ledger"]["retained_finite_owner_ids"] == rewire["retained_contact_finite_owner_ids"]
    assert tuple(item["new_interface_node_id"] for item in first["rewire_finite"]) == tuple(first["add_p1_nport"]["interface_node_ids"])
    assert substrate.network.surface_node_ids is original_nodes and substrate.network.via_links is original_links and substrate.network.partials is original_partials and substrate.network.ports is original_ports
    matched_ordinal = int(first["remove_old_maxwell"][0]["partial_ordinal"])
    partial = substrate.network.partials[matched_ordinal]
    material = partial.dispersion.layers[0][1] if hasattr(partial.dispersion, "layers") else partial.dispersion
    changed_material = replace(material, frequencies_hz=(2.0e9,), relative_permittivities=(float(material.relative_permittivities[0]),), loss_tangents=(float(material.loss_tangents[0]),))
    changed_dispersion = replace(partial.dispersion, layers=tuple((thickness, replace(material, frequencies_hz=(2.0e9,), relative_permittivities=(float(material.relative_permittivities[0]),), loss_tangents=(float(material.loss_tangents[0]),))) for thickness, material in partial.dispersion.layers)) if hasattr(partial.dispersion, "layers") else changed_material
    changed_partial = replace(partial, dispersion=changed_dispersion)
    changed_partials_list = list(substrate.network.partials)
    changed_partials_list[matched_ordinal] = changed_partial
    changed_partials = tuple(changed_partials_list)
    changed_network = compile_layer_surface_network(substrate.network.surface_node_ids, partials=changed_partials, via_links=substrate.network.via_links, ports=substrate.network.ports)
    changed_substrate = replace(substrate, network=changed_network)
    changed_binding = _empty_scenario_binding(changed_substrate)
    changed_commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire, changed_substrate, changed_binding, rail_id="VDD_CORE/1")
    assert changed_commutation["status"] == "passed"
    stopped = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, rewire, changed_commutation, changed_binding, rail_id="VDD_CORE/1")
    assert stopped["status"] == "stopped" and stopped["code"] == "LOCAL_REPLACEMENT_SOURCE_POINT_UNAVAILABLE"


def test_source_plane_patch_shadow_topology_embedding_is_complete(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    seed_rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert seed_rewire["status"] == "planned"
    seed_external = tuple(dict.fromkeys(str(row["external_endpoint"]) for row in seed_rewire["rewire_rows"]))
    assert len(seed_external) >= 2 and seed_external[0] != seed_external[1]
    from scipy.sparse import csc_matrix
    from spd_decap_pi._core.solver.layer_surface_network import compile_layer_surface_network
    template_wrapper = substrate.network.partials[0]
    capacitance = 1.0e-12
    augmented_matrix = csc_matrix([[capacitance, -capacitance], [-capacitance, capacitance]], dtype=float)
    augmented_partial = replace(template_wrapper.partial, net_names=tuple(seed_external[:2]), maxwell_capacitance_f=augmented_matrix)
    augmented_wrapper = replace(template_wrapper, partial=augmented_partial)
    augmented_network = compile_layer_surface_network(substrate.network.surface_node_ids, partials=substrate.network.partials + (augmented_wrapper,), via_links=substrate.network.via_links, ports=substrate.network.ports)
    augmented_identity = sha256(consumer.concrete_canonical_json_bytes({"base": substrate.substrate_identity_sha256, "p8": "augmented", "external": seed_external[:2]})).hexdigest()
    substrate = replace(substrate, network=augmented_network, substrate_identity_sha256=augmented_identity, provenance={**substrate.provenance, "substrate_identity_sha256": augmented_identity})
    rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    binding = _empty_scenario_binding(substrate)
    commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire, substrate, binding, rail_id="VDD_CORE/1")
    recipe = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, rewire, commutation, binding, rail_id="VDD_CORE/1")
    original = (substrate.network.surface_node_ids, substrate.network.via_links, substrate.network.partials, substrate.network.ports)
    edge = recipe["remove_old_maxwell"][0]
    matched_ordinal = int(edge["partial_ordinal"])
    original_csc = substrate.network.partials[matched_ordinal].partial.maxwell_capacitance_f
    original_csc_snapshot = (tuple(original_csc.data), tuple(original_csc.indices), tuple(original_csc.indptr))
    shadow_network, first = consumer.materialize_source_plane_patch_shadow_topology_embedding(commutation, recipe, binding, rail_id="VDD_CORE/1")
    second_network, second = consumer.materialize_source_plane_patch_shadow_topology_embedding(commutation, recipe, binding, rail_id="VDD_CORE/1")
    assert first == second and first["status"] == "passed" and first["shadow_only"] is True
    assert first["topology_materialized"] is True and first["p1_stamp_applied"] is False and first["solve_eligible"] is False and first["production_ready"] is False and first["replacement_ready"] is False
    assert shadow_network is not None and second_network is not None and type(shadow_network) is type(second_network)
    assert shadow_network.surface_node_ids == second_network.surface_node_ids and shadow_network.via_links == second_network.via_links and shadow_network.ports == second_network.ports
    assert any(partial is augmented_wrapper for partial in shadow_network.partials)
    assert [row["interface_node_id"] for row in first["interface_embedding"]] == recipe["add_p1_nport"]["interface_node_ids"]
    old_keys = {str(value).casefold() for value in commutation["old_selected_class_node_ids"]}
    assert not old_keys & {str(value).casefold() for value in shadow_network.surface_node_ids}
    assert tuple(shadow_network.surface_node_ids[-len(recipe["add_p1_nport"]["interface_node_ids"]):]) == tuple(recipe["add_p1_nport"]["interface_node_ids"])
    assert len({row["reduced_index"] for row in first["interface_embedding"]}) == len(first["interface_embedding"])
    for key in ("shadow_surface_manifest_sha256", "shadow_partial_manifest_sha256", "shadow_link_manifest_sha256", "shadow_port_manifest_sha256", "shadow_termination_manifest_sha256", "shadow_network_identity_sha256", "topology_embedding_sha256"):
        assert len(first[key]) == 64
    assert shadow_network.ports is binding.network.ports
    from spd_decap_pi._core.solver.layer_surface_termination import compile_layer_surface_termination_manifest
    shadow_termination_manifest = compile_layer_surface_termination_manifest(shadow_network.surface_node_ids, tuple(cluster.source for cluster in binding.termination_manifest.clusters))
    assert shadow_termination_manifest.manifest_sha256 == first["shadow_termination_manifest_sha256"]
    shadow_network.termination_reduced_node_mapping(shadow_termination_manifest)
    assert substrate.network.surface_node_ids is original[0] and substrate.network.via_links is original[1] and substrate.network.partials is original[2] and substrate.network.ports is original[3]
    retained_seed = next(value for value in seed_external if value.casefold() not in {str(item).casefold() for item in commutation["old_selected_class_node_ids"]})
    tampered_commutation = {**commutation, "old_selected_class_node_ids": list(commutation["old_selected_class_node_ids"]) + [retained_seed]}
    tampered_network, tampered = consumer.materialize_source_plane_patch_shadow_topology_embedding(tampered_commutation, recipe, binding, rail_id="VDD_CORE/1")
    assert tampered_network is None and tampered["status"] == "stopped" and tampered["code"] == "SHADOW_EMBEDDING_IDENTITY_MISMATCH"

    original_partial = substrate.network.partials[matched_ordinal]
    upper, lower = str(edge["upper_island_id"]), str(edge["lower_island_id"])
    capacitance = float.fromhex(edge["capacitance_f_hex"])
    narrow_matrix = csc_matrix([[capacitance, -capacitance], [-capacitance, capacitance]], dtype=float)
    narrow_partial = replace(original_partial.partial, net_names=(upper, lower), maxwell_capacitance_f=narrow_matrix)
    narrow_wrapper = replace(original_partial, partial=narrow_partial)
    narrow_network = compile_layer_surface_network(substrate.network.surface_node_ids, partials=(narrow_wrapper,), via_links=substrate.network.via_links, ports=substrate.network.ports)
    narrow_identity = sha256(consumer.concrete_canonical_json_bytes({"base": substrate.substrate_identity_sha256, "p8": "narrow"})).hexdigest()
    narrow_substrate = replace(substrate, network=narrow_network, substrate_identity_sha256=narrow_identity, provenance={**substrate.provenance, "substrate_identity_sha256": narrow_identity})
    narrow_rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, narrow_substrate, rail_id="VDD_CORE/1")
    narrow_binding = _empty_scenario_binding(narrow_substrate)
    narrow_commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(narrow_rewire, narrow_substrate, narrow_binding, rail_id="VDD_CORE/1")
    narrow_recipe = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, narrow_rewire, narrow_commutation, narrow_binding, rail_id="VDD_CORE/1")
    assert narrow_commutation["status"] == "passed" and narrow_recipe["status"] == "passed"
    none_network, stopped = consumer.materialize_source_plane_patch_shadow_topology_embedding(narrow_commutation, narrow_recipe, narrow_binding, rail_id="VDD_CORE/1")
    assert none_network is None and stopped["status"] == "stopped" and stopped["code"] == "SHADOW_EMBEDDING_UNREPRESENTABLE"
    assert (tuple(original_csc.data), tuple(original_csc.indices), tuple(original_csc.indptr)) == original_csc_snapshot


def test_source_plane_patch_shadow_nport_block_binding(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    seed = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert seed["status"] == "planned"
    external = tuple(dict.fromkeys(str(row["external_endpoint"]) for row in seed["rewire_rows"]))
    assert len(external) >= 2 and external[0] != external[1]
    from scipy.sparse import csc_matrix
    from spd_decap_pi._core.solver.layer_surface_network import compile_layer_surface_network
    template = substrate.network.partials[0]
    cap = 1.0e-12
    augmented_partial = replace(template.partial, net_names=external[:2], maxwell_capacitance_f=csc_matrix([[cap, -cap], [-cap, cap]], dtype=float))
    augmented_wrapper = replace(template, partial=augmented_partial)
    augmented_network = compile_layer_surface_network(substrate.network.surface_node_ids, partials=substrate.network.partials + (augmented_wrapper,), via_links=substrate.network.via_links, ports=substrate.network.ports)
    augmented_identity = sha256(consumer.concrete_canonical_json_bytes({"base": substrate.substrate_identity_sha256, "p9": "augmented", "external": external[:2]})).hexdigest()
    substrate = replace(substrate, network=augmented_network, substrate_identity_sha256=augmented_identity, provenance={**substrate.provenance, "substrate_identity_sha256": augmented_identity})
    rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    binding = _empty_scenario_binding(substrate)
    commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire, substrate, binding, rail_id="VDD_CORE/1")
    recipe = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, rewire, commutation, binding, rail_id="VDD_CORE/1")
    original = (substrate.network.surface_node_ids, substrate.network.via_links, substrate.network.partials, substrate.network.ports)
    shadow_a, block_a, first = consumer.bind_source_plane_patch_shadow_nport_block(patch, commutation, recipe, binding, rail_id="VDD_CORE/1")
    shadow_b, block_b, second = consumer.bind_source_plane_patch_shadow_nport_block(patch, commutation, recipe, binding, rail_id="VDD_CORE/1")
    assert first == second and first["status"] == "passed" and first["shadow_only"] is True
    assert first["topology_materialized"] is True and first["p1_stamp_bound"] is True and first["p1_stamp_applied"] is False and first["solve_eligible"] is False and first["production_ready"] is False and first["replacement_ready"] is False
    assert shadow_a is not None and shadow_b is not None and isinstance(block_a, NodalAdmittanceBlock) and isinstance(block_b, NodalAdmittanceBlock)
    assert block_a.node_ids == tuple(recipe["add_p1_nport"]["interface_node_ids"]) and block_a.owner_ids == tuple(recipe["owner_ledger"]["added_p1_block_owner_ids"])
    assert block_a.block_id == f"source-plane-shadow-p1-nport:{first['p1_output_sha256']}"
    expected_matrix = np.asarray([[complex(float(entry[0]), float(entry[1])) for entry in row] for row in patch["admittance_s"]], dtype=np.complex128)
    assert np.array_equal(block_a.admittance_s, block_b.admittance_s) and np.array_equal(block_a.admittance_s, expected_matrix) and block_a.admittance_s.dtype == np.complex128 and not block_a.admittance_s.flags.writeable
    assert first["reduced_indices"] == [int(shadow_a.reduced_node_index(node)) for node in block_a.node_ids]
    assert shadow_a.surface_node_ids == shadow_b.surface_node_ids and shadow_a.via_links == shadow_b.via_links and shadow_a.ports == shadow_b.ports
    assert substrate.network.surface_node_ids is original[0] and substrate.network.via_links is original[1] and substrate.network.partials is original[2] and substrate.network.ports is original[3]
    assert len(first["admittance_sha256"]) == 64 and len(first["nport_binding_sha256"]) == 64

    tampered_input = _h("e")
    assert tampered_input != patch["input_sha256"]
    tampered_patch = {**patch, "input_sha256": tampered_input}
    none_network, none_block, stopped = consumer.bind_source_plane_patch_shadow_nport_block(tampered_patch, commutation, recipe, binding, rail_id="VDD_CORE/1")
    assert none_network is None and none_block is None and stopped["status"] == "stopped" and stopped["code"] == "SHADOW_NPORT_IDENTITY_MISMATCH"


def test_source_plane_patch_shadow_augmented_component_closure(tmp_path: Path):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    seed = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    assert seed["status"] == "planned"
    external = tuple(dict.fromkeys(str(row["external_endpoint"]) for row in seed["rewire_rows"]))
    assert len(external) >= 2 and external[0] != external[1]
    from scipy.sparse import csc_matrix
    from spd_decap_pi._core.solver.layer_surface_network import compile_layer_surface_network
    template = substrate.network.partials[0]
    cap = 1.0e-12
    augmented_wrapper = replace(template, partial=replace(template.partial, net_names=external[:2], maxwell_capacitance_f=csc_matrix([[cap, -cap], [-cap, cap]], dtype=float)))
    augmented_network = compile_layer_surface_network(substrate.network.surface_node_ids, partials=substrate.network.partials + (augmented_wrapper,), via_links=substrate.network.via_links, ports=substrate.network.ports)
    augmented_identity = sha256(consumer.concrete_canonical_json_bytes({"base": substrate.substrate_identity_sha256, "p10": "augmented", "external": external[:2]})).hexdigest()
    substrate = replace(substrate, network=augmented_network, substrate_identity_sha256=augmented_identity, provenance={**substrate.provenance, "substrate_identity_sha256": augmented_identity})
    rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    binding = _empty_scenario_binding(substrate)
    commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire, substrate, binding, rail_id="VDD_CORE/1")
    recipe = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, rewire, commutation, binding, rail_id="VDD_CORE/1")
    original = (substrate.network.surface_node_ids, substrate.network.via_links, substrate.network.partials, substrate.network.ports)
    matched_ordinal = int(recipe["remove_old_maxwell"][0]["partial_ordinal"])
    original_csc = substrate.network.partials[matched_ordinal].partial.maxwell_capacitance_f
    original_csc_snapshot = (tuple(original_csc.data), tuple(original_csc.indices), tuple(original_csc.indptr))
    original_csc_flags = (bool(original_csc.data.flags.writeable), bool(original_csc.indices.flags.writeable), bool(original_csc.indptr.flags.writeable))
    first = consumer.audit_source_plane_patch_shadow_augmented_component_closure(patch, commutation, recipe, binding, rail_id="VDD_CORE/1")
    second = consumer.audit_source_plane_patch_shadow_augmented_component_closure(patch, commutation, recipe, binding, rail_id="VDD_CORE/1")
    assert first == second and first["status"] == "passed" and first["shadow_only"] is True
    assert first["component_closure_verified"] is True and first["p1_connectivity_accounted"] is True and first["p1_stamp_applied"] is False and first["global_matrix_assembled"] is False and first["solve_eligible"] is False and first["production_ready"] is False and first["replacement_ready"] is False
    assert first["port_components"] and len(first["interface_components"]) == len(recipe["add_p1_nport"]["interface_node_ids"])
    assert len(first["augmented_components"]) <= len(first["base_components"])
    port_representatives = {row["component_representative_surface_node_id"] for row in first["port_components"]}
    assert all(row["component_representative_surface_node_id"] in port_representatives for row in first["interface_components"])
    assert first["block_id"] == f"source-plane-shadow-p1-nport:{first['p1_output_sha256']}" and len(first["admittance_sha256"]) == 64
    for key in ("base_component_manifest_sha256", "augmented_component_manifest_sha256", "p1_structural_edge_sha256", "component_closure_sha256"):
        assert len(first[key]) == 64
    assert substrate.network.surface_node_ids is original[0] and substrate.network.via_links is original[1] and substrate.network.partials is original[2] and substrate.network.ports is original[3]
    assert (tuple(original_csc.data), tuple(original_csc.indices), tuple(original_csc.indptr)) == original_csc_snapshot
    assert (bool(original_csc.data.flags.writeable), bool(original_csc.indices.flags.writeable), bool(original_csc.indptr.flags.writeable)) == original_csc_flags

    from spd_decap_pi._core.solver.layer_surface_network import LayerSurfacePort
    isolated = "P10_ISOLATED"
    isolated_port = LayerSurfacePort("p10-isolated", isolated, external[0])
    isolated_network = compile_layer_surface_network(substrate.network.surface_node_ids + (isolated,), partials=substrate.network.partials, via_links=substrate.network.via_links, ports=substrate.network.ports + (isolated_port,))
    isolated_identity = sha256(consumer.concrete_canonical_json_bytes({"base": substrate.substrate_identity_sha256, "p10": "isolated"})).hexdigest()
    isolated_substrate = replace(substrate, network=isolated_network, substrate_identity_sha256=isolated_identity, provenance={**substrate.provenance, "substrate_identity_sha256": isolated_identity})
    isolated_rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, isolated_substrate, rail_id="VDD_CORE/1")
    isolated_binding = _empty_scenario_binding(isolated_substrate)
    isolated_commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(isolated_rewire, isolated_substrate, isolated_binding, rail_id="VDD_CORE/1")
    isolated_recipe = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, isolated_rewire, isolated_commutation, isolated_binding, rail_id="VDD_CORE/1")
    p9_network, p9_block, p9_audit = consumer.bind_source_plane_patch_shadow_nport_block(patch, isolated_commutation, isolated_recipe, isolated_binding, rail_id="VDD_CORE/1")
    assert p9_network is not None and p9_block is not None and p9_audit["status"] == "passed"
    stopped = consumer.audit_source_plane_patch_shadow_augmented_component_closure(patch, isolated_commutation, isolated_recipe, isolated_binding, rail_id="VDD_CORE/1")
    assert stopped["status"] == "stopped" and stopped["code"] == "SHADOW_COMPONENT_PORT_DISCONNECTED"


def test_source_plane_patch_shadow_one_frequency_augmented_solve(tmp_path: Path, monkeypatch):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)
    patch = consumer.evaluate_source_plane_contact_condensation(own, imported.attachments, raw, imported.attachments, rail_id="VDD_CORE/1", frequency_hz=1.0e9, cell_um=1000.0)
    seed = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    external = tuple(dict.fromkeys(str(row["external_endpoint"]) for row in seed["rewire_rows"]))
    assert len(external) >= 2 and external[0] != external[1]
    from scipy.sparse import csc_matrix
    from spd_decap_pi._core.solver.layer_surface_network import compile_layer_surface_network
    template = substrate.network.partials[0]
    cap = 1.0e-12
    augmented_wrapper = replace(template, partial=replace(template.partial, net_names=external[:2], maxwell_capacitance_f=csc_matrix([[cap, -cap], [-cap, cap]], dtype=float)))
    augmented_network = compile_layer_surface_network(substrate.network.surface_node_ids, partials=substrate.network.partials + (augmented_wrapper,), via_links=substrate.network.via_links, ports=substrate.network.ports)
    augmented_identity = sha256(consumer.concrete_canonical_json_bytes({"base": substrate.substrate_identity_sha256, "p11": "augmented", "external": external[:2]})).hexdigest()
    substrate = replace(substrate, network=augmented_network, substrate_identity_sha256=augmented_identity, provenance={**substrate.provenance, "substrate_identity_sha256": augmented_identity})
    rewire = consumer.plan_source_plane_patch_shadow_contact_rewire(own, imported.attachments, raw, patch, substrate, rail_id="VDD_CORE/1")
    binding = _empty_scenario_binding(substrate)
    commutation = consumer.audit_source_plane_patch_shadow_rewire_commutation(rewire, substrate, binding, rail_id="VDD_CORE/1")
    recipe = consumer.audit_source_plane_patch_shadow_local_replacement_recipe(patch, rewire, commutation, binding, rail_id="VDD_CORE/1")
    closure = consumer.audit_source_plane_patch_shadow_augmented_component_closure(patch, commutation, recipe, binding, rail_id="VDD_CORE/1")
    assert closure["status"] == "passed"
    original_inventory = (substrate.network.surface_node_ids, substrate.network.via_links, substrate.network.partials, substrate.network.ports)
    matched_ordinal = int(recipe["remove_old_maxwell"][0]["partial_ordinal"])
    original_csc = substrate.network.partials[matched_ordinal].partial.maxwell_capacitance_f
    original_csc_snapshot = (tuple(original_csc.data), tuple(original_csc.indices), tuple(original_csc.indptr))
    original_csc_flags = (bool(original_csc.data.flags.writeable), bool(original_csc.indices.flags.writeable), bool(original_csc.indptr.flags.writeable))
    cache_snapshot = (tuple(substrate.network._frequency_result_cache.items()), tuple(substrate.network._frequency_result_cache_bindings.items()), dict(substrate.network._frequency_result_cache_state_counts), dict(substrate.network._frequency_result_cache_binding_bytes), substrate.network._frequency_result_cache_bytes)
    first = consumer.audit_source_plane_patch_shadow_one_frequency_solve(patch, commutation, recipe, closure, binding, rail_id="VDD_CORE/1")
    second = consumer.audit_source_plane_patch_shadow_one_frequency_solve(patch, commutation, recipe, closure, binding, rail_id="VDD_CORE/1")
    assert first == second and first["status"] == "stopped" and first["code"] == "SHADOW_SOLVE_NUMERICAL_FAILURE" and first["shadow_only"] is True
    assert "forward-reliability pivot ratio is excessive" in first["detail"] and "(1.900e+15" in first["detail"] and "component_index=0" in first["detail"] and "retained_nodes=8" in first["detail"] and "backward_residual=7.308e-17" in first["detail"] and "condition_1_lower_bound=1.096e+17" in first["detail"] and "matrix_sha256=b0680d39fcc0f9bad2c6e619b6910fd3e52765a610b940e31f7dc6cb1be79c0e" in first["detail"]
    assert first["component_closure_verified"] is False and first["p1_stamp_applied"] is False and first["global_matrix_assembled"] is False and first["one_frequency_shadow_solve_executed"] is False and first["cache_reuse_eligible"] is False and first["solve_eligible"] is False and first["production_ready"] is False and first["replacement_ready"] is False and "solve_identity_sha256" not in first and "port_admittance" not in first and len(first["audit_sha256"]) == 64
    assert substrate.network.surface_node_ids is original_inventory[0] and substrate.network.via_links is original_inventory[1] and substrate.network.partials is original_inventory[2] and substrate.network.ports is original_inventory[3]
    assert (tuple(original_csc.data), tuple(original_csc.indices), tuple(original_csc.indptr)) == original_csc_snapshot and (bool(original_csc.data.flags.writeable), bool(original_csc.indices.flags.writeable), bool(original_csc.indptr.flags.writeable)) == original_csc_flags
    assert (tuple(substrate.network._frequency_result_cache.items()), tuple(substrate.network._frequency_result_cache_bindings.items()), dict(substrate.network._frequency_result_cache_state_counts), dict(substrate.network._frequency_result_cache_binding_bytes), substrate.network._frequency_result_cache_bytes) == cache_snapshot
    tampered = {**closure, "component_closure_sha256": _h("f")}
    def unexpected_p9(*_args, **_kwargs):
        raise AssertionError("P9 must not be called for invalid P10 identity")
    monkeypatch.setattr(consumer, "bind_source_plane_patch_shadow_nport_block", unexpected_p9)
    stopped = consumer.audit_source_plane_patch_shadow_one_frequency_solve(patch, commutation, recipe, tampered, binding, rail_id="VDD_CORE/1")
    assert stopped["status"] == "stopped" and stopped["code"] == "SHADOW_SOLVE_IDENTITY_MISMATCH" and stopped["p1_stamp_applied"] is False and stopped["global_matrix_assembled"] is False and "solve_identity_sha256" not in stopped and "port_admittance" not in stopped and len(stopped["audit_sha256"]) == 64


def test_source_plane_source_block_census_is_deterministic_and_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    imported = _v2_import(tmp_path)
    project = imported.scenario.base_project
    own = project.metadata["spd_import"]["source_plane_ownership_ir"]
    raw = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    substrate = compile_layerwise_substrate(project, imported.attachments, required_rail_id="VDD_CORE/1", require_plane_sheet_payload=True)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A2 census must not invoke P1 condensation, owner join, or solve")

    for name in ("evaluate_source_plane_contact_condensation", "audit_source_plane_patch_production_owner_join", "audit_source_plane_patch_shadow_one_frequency_solve", "evaluate_nodal_admittance_block"):
        monkeypatch.setattr(consumer, name, forbidden, raising=False)
    monkeypatch.setattr(type(substrate.network), "solve", forbidden, raising=False)
    first = consumer.audit_source_plane_source_block_census(own, imported.attachments, raw, substrate, rail_id="VDD_CORE/1")
    second = consumer.audit_source_plane_source_block_census(own, imported.attachments, raw, substrate, rail_id="VDD_CORE/1")
    assert first == second
    report_without_sha = dict(first); report_without_sha.pop("final_report_sha256")
    assert first["final_report_sha256"] == sha256(consumer.concrete_canonical_json_bytes(report_without_sha)).hexdigest() and first["status"] == "complete" and first["shadow_only"] is True and first["replacement_ready"] is False and first["production_ready"] is False
    assert set(first["numeric_row_classification_counts"]) == {"adjacent", "source-proven-nonlocal", "missing-source-excluded"}
    assert set(first["numeric_row_action_counts"]) == {"candidate", "retained", "excluded"}
    assert sum(first["numeric_row_classification_counts"].values()) == first["numeric_row_count"] == len(first["rows"])
    assert sum(first["numeric_row_action_counts"].values()) == first["numeric_row_count"]
    row_hash_payload = [{key: row[key] for key in ("fingerprint", "partial_ordinal", "upper_reduced_index", "lower_reduced_index", "classification", "action", "aggregation_count")} for row in first["rows"]]
    assert first["rows"] == sorted(first["rows"], key=lambda row: (row["partial_ordinal"], row["upper_reduced_index"], row["lower_reduced_index"], row["fingerprint"])) and first["rows_sha256"] == sha256(consumer.concrete_canonical_json_bytes(row_hash_payload)).hexdigest()
    assert all(first[f"{action}_fingerprints"] == sorted(row["fingerprint"] for row in first["rows"] if row["action"] == action) and first[f"{action}_fingerprints_sha256"] == sha256(consumer.concrete_canonical_json_bytes(first[f"{action}_fingerprints"])).hexdigest() for action in ("candidate", "retained", "excluded"))
    assert all(row["classification"] == "adjacent" for row in first["rows"] if row["action"] == "candidate")
    assert not ({row["fingerprint"] for row in first["rows"] if row["action"] == "candidate"} & {row["fingerprint"] for row in first["rows"] if row["action"] != "candidate"})
    assert first["symbolic_conductance_law"] == "G(f)=2*pi*f*C(f)*Df(f)"
    assert len(first["candidate_scope_ids"]) == 2 and first["candidate_scope_ids_sha256"] == sha256(consumer.concrete_canonical_json_bytes([item.casefold() for item in first["candidate_scope_ids"]])).hexdigest()
    assert first["known_exclusions_count"] == len(first["known_exclusions"]) == 2 and first["known_exclusions_sha256"] == sha256(consumer.concrete_canonical_json_bytes(first["known_exclusions"])).hexdigest() and all(set(item) == {"mechanism", "classification", "reason_code", "reason"} and item["mechanism"] and item["classification"] == "missing-source-excluded" and item["reason_code"] and item["reason"] and "capacitance" not in item and "magnitude" not in item for item in first["known_exclusions"])
    assert all(row.get("source_record_sha256") for row in first["material_source_records"])
    assert "w6" not in json.dumps(first["query_key"], ensure_ascii=False).casefold()
    assert len(consumer.concrete_canonical_json_bytes(first)) <= 1_048_576
    assert len(first["query_key_sha256"]) == len(first["rows_sha256"]) == len(first["final_report_sha256"]) == 64
    assert first["owner_ledger"]["retained_owner_count"] > 0 and len(first["owner_ledger"]["replaced_scope_ids"]) == 2
    ownership_sections = tuple(own["counts"])
    ownership_binding = {key: own[key] for key in ("app_version", "source_sha256", "source_size_bytes", "target_rail_id", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")}
    with load_source_plane_ownership_ir(own, imported.attachments, expected_app_version=own["app_version"], **{f"expected_{key}": own[key] for key in ("source_sha256", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")}) as loaded:
        ownership_data = {**ownership_binding, **{section: list(loaded.iter_section(section)) for section in ownership_sections}}
    altered_dielectric = deepcopy(ownership_data)
    for point in altered_dielectric["dielectric_points"]:
        point["epsilon_r"] = float(point["epsilon_r"]) + 1.0
    altered_manifest, altered_asset = build_source_plane_ownership_ir(altered_dielectric)
    altered_attachments = dict(imported.attachments); altered_attachments[altered_asset[0]] = altered_asset[1]
    with pytest.raises(consumer.SourcePlanePatchError):
        consumer.audit_source_plane_source_block_census(altered_manifest, altered_attachments, raw, substrate, rail_id="VDD_CORE/1")
    island_by_id = {str(row["island_id"]).casefold(): row for row in ownership_data["islands"]}
    surface_by_id = {str(row["surface_id"]).casefold(): row for row in ownership_data["surfaces"]}
    selected_bindings = {str(row["role"]).casefold(): row for row in ownership_data["rail_bindings"] if str(row["rail_id"]).casefold() == "vdd_core/1"}
    selected_reduced = {role: int(first["closures"][role]["reduced_indices"][0]) for role in ("power", "ground")}
    original_reduced_node_index = type(substrate.network).reduced_node_index
    negative_edge_partners = {}
    for wrapped in substrate.network.partials:
        partial = getattr(wrapped, "partial", wrapped)
        names = tuple(str(name) for name in getattr(partial, "net_names", ()))
        matrix = partial.maxwell_capacitance_f.tocoo(copy=False)
        for row_index, column_index, value in zip(matrix.row, matrix.col, matrix.data, strict=True):
            if int(row_index) < int(column_index) and float(value) < 0.0:
                left, right = names[int(row_index)], names[int(column_index)]
                negative_edge_partners.setdefault(left.casefold(), set()).add(right)
                negative_edge_partners.setdefault(right.casefold(), set()).add(left)
    alias_target = None
    for partial_ordinal, wrapped in enumerate(substrate.network.partials):
        partial = getattr(wrapped, "partial", wrapped)
        names = tuple(str(name) for name in getattr(partial, "net_names", ()))
        for row_index, column_index, value in sorted((int(row_index), int(column_index), float(value)) for row_index, column_index, value in zip(partial.maxwell_capacitance_f.tocoo(copy=False).row, partial.maxwell_capacitance_f.tocoo(copy=False).col, partial.maxwell_capacitance_f.tocoo(copy=False).data, strict=True) if int(row_index) < int(column_index) and float(value) < 0.0):
            for endpoint_index, _other_index in ((row_index, column_index), (column_index, row_index)):
                endpoint = names[endpoint_index]
                endpoint_island = island_by_id.get(endpoint.casefold())
                endpoint_surface = surface_by_id.get(str(endpoint_island.get("surface_id", "")).casefold()) if endpoint_island is not None else None
                if endpoint_island is None or endpoint_surface is None:
                    continue
                endpoint_reduced = int(original_reduced_node_index(substrate.network, endpoint))
                if endpoint_reduced in selected_reduced.values():
                    continue
                for role in ("power", "ground"):
                    binding_surface = str(selected_bindings[role]["surface_id"]).casefold()
                    partners = negative_edge_partners.get(endpoint.casefold(), ())
                    if str(endpoint_island.get("surface_id", "")).casefold() != binding_surface and partners and all(int(original_reduced_node_index(substrate.network, partner)) != selected_reduced[role] for partner in partners):
                        alias_target = (partial_ordinal, endpoint, selected_reduced[role])
                        break
                if alias_target is not None:
                    break
            if alias_target is not None:
                break
        if alias_target is not None:
            break
    assert alias_target is not None
    _partial_ordinal, alias_endpoint, alias_reduced = alias_target
    def synthetic_reduced_node_index(network, node):
        if str(node) == alias_endpoint:
            return alias_reduced
        return original_reduced_node_index(network, node)
    with monkeypatch.context() as alias_patch:
        alias_patch.setattr(type(substrate.network), "reduced_node_index", synthetic_reduced_node_index)
        with pytest.raises(consumer.SourcePlanePatchError, match="incident endpoint alias lacks direct layer witness"):
            consumer.audit_source_plane_source_block_census(own, imported.attachments, raw, substrate, rail_id="VDD_CORE/1")
    with pytest.raises(consumer.SourcePlanePatchError):
        consumer.audit_source_plane_source_block_census(own, imported.attachments, {**raw, "geometry_identity_sha256": _h("e")}, substrate, rail_id="VDD_CORE/1")
