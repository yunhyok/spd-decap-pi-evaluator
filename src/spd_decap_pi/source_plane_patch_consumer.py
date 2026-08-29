"""Shadow-only source-plane patch witness (Phase 3).

This module consumes the authenticated Phase-1 ownership sidecar and raw-v3
plane sheet.  It deliberately returns shadow data/ephemeral networks only;
production MNA/Y/Z paths are not touched.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from hashlib import sha256
import json
import math
from typing import Any

import numpy as np
from scipy.sparse import csc_matrix, issparse

from .canonical_json import concrete_canonical_json_bytes, iter_concrete_canonical_json_bytes
from .raw_spatial_contact_asset import load_raw_spatial_contact_asset
from .source_plane_ownership_ir import (
    MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS,
    load_source_plane_ownership_ir,
)
from ._core.geometry.ordered_boolean import ordered_spd_geometry
from ._core.solver.mfdm import EPSILON_0_F_PER_M, MU_0_H_PER_M
from ._core.solver.layerwise_network import LayerwiseScenarioNetworkBinding
from ._core.solver.layer_surface_network import CompiledLayerSurfaceNetwork, compile_layer_surface_network
from ._core.solver.layer_surface_termination import compile_layer_surface_termination_manifest
from ._core.solver.surface_patch_plane import (
    SurfacePatchArtwork,
    SurfacePatchConductor,
    SurfacePatchDielectric,
    SurfacePatchFinitePort,
    SurfacePatchMesh,
    compile_surface_patch_plane,
)



class SourcePlanePatchError(ValueError):
    """Fail-closed error at the source-plane patch trust boundary."""


def _fail(message: str) -> None:
    raise SourcePlanePatchError(message)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(f"{label} is invalid")
    return value


def _row_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if is_dataclass(row):
        return {item.name: getattr(row, item.name) for item in fields(row)}
    _fail("raw row is not typed")


def _row_hash(row: Any) -> str:
    return sha256(json.dumps(dict(_row_mapping(row)), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _object_mapping(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return {item.name: getattr(value, item.name) for item in fields(value)}
    return {}


def _rows(loaded: Any, section: str, cap: int, total: list[int]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in loaded.iter_section(section, batch_rows=1024):
        result.append(row)
        total[0] += 1
        if total[0] > cap:
            _fail("source-plane patch row bound exceeded")
    return result


def _raw_rows(loaded: Any, section: str, cap: int, total: list[int], keep: Any = None) -> list[Any]:
    result: list[Any] = []
    iterator = {
        "surfaces": loaded.iter_surfaces,
        "nodes": loaded.iter_nodes,
        "vias": loaded.iter_vias,
        "pad_shapes": loaded.iter_pad_shapes,
        "plane_primitives": loaded.iter_plane_primitives,
        "plane_vertices": loaded.iter_plane_vertices,
        "plane_circles": loaded.iter_plane_circles,
        "stackup_layers": loaded.iter_stackup_layers,
        "dielectric_points": loaded.iter_dielectric_points,
    }[section](batch_rows=1024)
    for row in iterator:
        if keep is not None and not keep(row):
            continue
        result.append(row)
        total[0] += 1
        if total[0] > cap:
            _fail("source-plane patch row bound exceeded")
    return result


def _binding_check(ownership: Mapping[str, Any], raw: Mapping[str, Any]) -> None:
    expected = {
        "source_sha256": raw.get("source_sha256"),
        "project_binding_sha256": raw.get("project_binding_sha256"),
        "certificate_evidence_sha256": raw.get("certificate_evidence_sha256"),
        "compiled_topology_identity_sha256": raw.get("compiled_topology_identity_sha256"),
        "raw_manifest_sha256": sha256(concrete_canonical_json_bytes(dict(raw))).hexdigest(),
        "raw_geometry_identity_sha256": raw.get("geometry_identity_sha256"),
        "raw_logical_rows_sha256": raw.get("logical_rows_sha256"),
        "raw_plane_sheet_sha256": raw.get("plane_sheet_payload_sha256"),
    }
    for key, value in expected.items():
        if ownership.get(key) != value:
            _fail(f"ownership/raw identity differs: {key}")


def _owner_gate(rows: Mapping[str, list[dict[str, Any]]], rail_id: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    folded = rail_id.casefold()
    rails = [row for row in rows["rail_bindings"] if str(row.get("rail_id", "")).casefold() == folded]
    if len(rails) != 2 or {str(row.get("role", "")) for row in rails} != {"power", "ground"}:
        _fail("target rail must have exactly one power and one ground binding")
    if any(row.get("state") != "source_bound" for row in rails):
        _fail("target rail binding is not source_bound")
    scopes = rows["plane_owner_scopes"]
    for row in rails:
        key = (folded, str(row.get("role", "")).casefold())
        matches = [scope for scope in scopes if (str(scope.get("rail_id", "")).casefold(), str(scope.get("role", "")).casefold()) == key]
        if len(matches) != 1 or matches[0].get("state") != "declared_unconsumed" or matches[0].get("owner_count") != 1:
            _fail("plane owner scope is incomplete")
    retained = rows["retained_owner_refs"]
    owner_ids = {str(row.get("owner_id", "")).casefold() for row in retained}
    plane_ids = {str(row.get("compiler_owner_id", "")).casefold() for row in scopes}
    if owner_ids & plane_ids:
        _fail("plane and retained owner namespaces overlap")
    members = rows["replacement_ledger_members"]
    member_ids = {str(row.get("owner_id", "")).casefold() for row in members}
    if member_ids != owner_ids | plane_ids:
        _fail("replacement ledger owner coverage differs")
    for row in members:
        owner = str(row.get("owner_id", "")).casefold()
        action = row.get("action")
        if owner in plane_ids and action != "replaced":
            _fail("plane owner ledger action is invalid")
        if owner in owner_ids and action != "retained":
            _fail("retained owner ledger action is invalid")
    branches: dict[str, dict[str, Any]] = {}
    for row in rows["terminal_bindings"]:
        if str(row.get("rail_id", "")).casefold() != folded or row.get("status") != "complete" or row.get("issues_json") != "[]":
            continue
        branch = _text(row.get("branch_id"), "terminal branch").casefold()
        role = _text(row.get("role"), "terminal role").casefold()
        if role not in {"power", "ground"}:
            continue
        role_rows = branches.setdefault(branch, {}).setdefault(role, [])
        role_rows.append(row)
    if any(any(len(rows) != 1 for rows in pair.values()) for pair in branches.values()):
        _fail("terminal branch has duplicate role bindings")
    pairs = [{role: rows[0] for role, rows in pair.items()} for pair in branches.values() if set(pair) == {"power", "ground"}]
    if not pairs:
        _fail("no complete same-branch power/ground terminal pair")
    pair = sorted(pairs, key=lambda value: (str(value["power"].get("terminal_id", "")).casefold(), str(value["ground"].get("terminal_id", "")).casefold()))[0]
    selected_owners: list[str] = []
    retained_by_id = {str(row.get("owner_id", "")).casefold(): row for row in retained}
    for terminal in pair.values():
        owner = _text(terminal.get("via_owner_id"), "terminal Via owner")
        ref = retained_by_id.get(owner.casefold())
        if ref is None or str(ref.get("edge_id", "")).casefold() != str(terminal.get("finite_edge_id", "")).casefold() or str(ref.get("island_id", "")).casefold() != str(terminal.get("island_id", "")).casefold():
            _fail("terminal Via owner is not bound to retained owner inventory")
        selected_owners.extend(str(item.get("owner_id")) for item in retained if str(item.get("edge_id", "")).casefold() == str(terminal.get("finite_edge_id", "")).casefold())
    selected_owners = sorted({item for item in selected_owners if item}, key=str.casefold)
    return pair["power"], pair["ground"], sorted(selected_owners, key=str.casefold)


def _terminal_raw_gate(terminals: tuple[dict[str, Any], dict[str, Any]], source_records: list[dict[str, Any]], nodes: list[Any], vias: list[Any], pads: list[Any], rail_bindings: Mapping[str, Mapping[str, Any]]) -> tuple[Any, Any]:
    from shapely.affinity import rotate
    from shapely.geometry import Point, box
    records = {str(row.get("record_id", "")).casefold(): row for row in source_records}
    footprints: list[Any] = []
    for terminal in terminals:
        binding = rail_bindings[str(terminal.get("role", "")).casefold()]
        node_record = records.get(str(terminal.get("source_node_record_id", "")).casefold())
        via_record = records.get(str(terminal.get("via_record_id", "")).casefold())
        if node_record is None or via_record is None:
            _fail("terminal source Node/Via record is absent")
        if str(node_record.get("kind", "")).casefold() != "node" or str(via_record.get("kind", "")).casefold() != "via":
            _fail("terminal Node/Via source record kind differs")
        node_matches = [row for row in nodes if str(row.source_record_sha256) == str(node_record.get("source_record_sha256"))]
        via_matches = [row for row in vias if row.source_record_sha256 == via_record.get("source_record_sha256")]
        if len(node_matches) != 1 or len(via_matches) != 1:
            _fail("terminal raw Node/Via provenance is ambiguous")
        via = via_matches[0]
        if via.status != "EXACT" or (via.start_x_pm, via.start_y_pm) != (via.end_x_pm, via.end_y_pm):
            _fail("terminal Via is not exact and non-slanted")
        source_node = node_matches[0]
        if str(source_node.net_name or "").casefold() != str(binding.get("logical_net", "")).casefold() or str(via.net_name).casefold() != str(binding.get("logical_net", "")).casefold() or str(source_node.node_id).casefold() not in {str(via.start_node_id).casefold(), str(via.end_node_id).casefold()}:
            _fail("terminal source Node/Via net or incidence differs")
        endpoint_matches = [row for row in nodes if str(row.node_id).casefold() == str(terminal.get("endpoint_node_id", "")).casefold() and str(row.net_name or "").casefold() == str(binding.get("logical_net", "")).casefold() and str(row.layer_id).casefold() == str(terminal.get("layer", "")).casefold()]
        if len(endpoint_matches) != 1:
            _fail("terminal external endpoint Node is absent or ambiguous")
        node = endpoint_matches[0]
        if str(source_node.node_id).casefold() == str(node.node_id).casefold():
            _fail("terminal source and external endpoint Node must differ")
        if (str(source_node.node_id).casefold() == str(via.start_node_id).casefold()) == (str(node.node_id).casefold() == str(via.start_node_id).casefold()):
            _fail("terminal source and external endpoint are not opposite Via endpoints")
        if str(node.node_id).casefold() not in {str(via.start_node_id).casefold(), str(via.end_node_id).casefold()} or str(terminal.get("layer", "")).casefold() not in {str(via.start_layer_id).casefold(), str(via.end_layer_id).casefold()} or str(terminal.get("padstack_id", "")).casefold() != str(via.padstack_id).casefold() or str(terminal.get("island_id", "")).casefold() != str(binding.get("island_id", "")).casefold():
            _fail("terminal Via endpoint/layer/padstack differs")
        if (str(node.node_id).casefold() == str(via.start_node_id).casefold() and (node.x_pm, node.y_pm) != (via.start_x_pm, via.start_y_pm)) or (str(node.node_id).casefold() == str(via.end_node_id).casefold() and (node.x_pm, node.y_pm) != (via.end_x_pm, via.end_y_pm)):
            _fail("terminal Node/Via endpoint coordinate differs")
        canonical_owner = _text(terminal.get("via_owner_id"), "terminal Via owner")
        # The finite quotient uses via:<via>; raw-v3 retains the explicit
        # composite owner via:<net>:<via>.  Both must identify this Via.
        if canonical_owner.casefold() != f"via:{via.via_id}".casefold():
            _fail("terminal canonical Via owner mismatch")
        composite_owner = f"via:{str(via.net_name).casefold()}:{str(via.via_id).casefold()}"
        if str(getattr(via, "owner_id", "")).casefold() != composite_owner:
            _fail("terminal composite Via owner mismatch")
        pad_matches = [row for row in pads if str(row.padstack_id).casefold() == str(terminal.get("padstack_id", "")).casefold() and str(row.layer_id).casefold() == str(terminal.get("layer", "")).casefold() and int(row.ordinal) == int(terminal.get("raw_pad_shape_ordinal", -1)) and str(row.source_record_sha256) == str(terminal.get("raw_pad_shape_sha256", ""))]
        if len(pad_matches) != 1:
            _fail("terminal pad-shape provenance differs")
        paddef = records.get(str(terminal.get("paddef_source_record_id", "")).casefold())
        regular = records.get(str(terminal.get("regular_source_record_id", "")).casefold())
        if (paddef is None or regular is None
                or str(paddef.get("kind", "")).casefold() != "paddef"
                or str(regular.get("kind", "")).casefold() != "regular"
                or str(paddef.get("layer", "")).casefold() != str(terminal.get("layer", "")).casefold()
                or str(regular.get("layer", "")).casefold() != str(terminal.get("layer", "")).casefold()):
            _fail("terminal PadDef/Regular source record kind differs")
        pad = pad_matches[0]
        x_um, y_um = float(node.x_pm) * 1e-6, float(node.y_pm) * 1e-6
        width_um, height_um = float(pad.width_pm) * 1e-6, float(pad.height_pm) * 1e-6
        if pad.shape_kind == "CIRCLE":
            footprint = Point(x_um, y_um).buffer(width_um / 2.0, quad_segs=64)
        else:
            footprint = box(x_um - width_um / 2.0, y_um - height_um / 2.0, x_um + width_um / 2.0, y_um + height_um / 2.0)
            if via.rotation_microdegrees:
                footprint = rotate(footprint, float(via.rotation_microdegrees) / 1_000_000.0, origin=(x_um, y_um))
        footprints.append(footprint)
    return footprints[0], footprints[1]


def _contact_raw_gate(
    contacts: list[dict[str, Any]],
    source_records: list[dict[str, Any]],
    nodes: list[Any],
    vias: list[Any],
    pads: list[Any],
    rail_bindings: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Reconstruct v2 contact footprints and bind them to selected artwork."""
    from shapely.affinity import rotate
    from shapely.geometry import Point, box

    records = {str(row.get("record_id", "")).casefold(): row for row in source_records}
    metadata: list[dict[str, Any]] = []
    for contact in contacts:
        net = _text(contact.get("net"), "contact net")
        layer = _text(contact.get("endpoint_layer"), "contact layer")
        matches = [
            row for row in rail_bindings
            if str(row.get("logical_net", "")).casefold() == net.casefold()
            and str(row.get("layer", "")).casefold() == layer.casefold()
        ]
        if len(matches) != 1:
            _fail("CONTACT_ADMISSIBILITY_INVALID: selected rail binding is ambiguous")
        artwork_net = _text(matches[0].get("artwork_net"), "contact artwork net")
        plane_id = _text(contact.get("plane_endpoint_node_id"), "contact plane Node")
        external_id = _text(contact.get("external_endpoint_node_id"), "contact external Node")
        plane_record_id = _text(contact.get("plane_endpoint_node_record_id"), "contact plane Node record")
        external_record_id = _text(contact.get("external_endpoint_node_record_id"), "contact external Node record")
        via_record_id = _text(contact.get("via_record_id"), "contact Via record")
        paddef_id = _text(contact.get("paddef_source_record_id"), "contact PadDef record")
        regular_id = _text(contact.get("regular_source_record_id"), "contact Regular record")
        refs = {name: records.get(value.casefold()) for name, value in (("plane", plane_record_id), ("external", external_record_id), ("via", via_record_id), ("paddef", paddef_id), ("regular", regular_id))}
        if any(value is None for value in refs.values()):
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact source record is absent")
        if str(refs["plane"].get("kind", "")).casefold() != "node" or str(refs["external"].get("kind", "")).casefold() != "node" or str(refs["via"].get("kind", "")).casefold() != "via" or str(refs["paddef"].get("kind", "")).casefold() != "paddef" or str(refs["regular"].get("kind", "")).casefold() != "regular":
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact source record kind differs")
        if plane_record_id.casefold() != str(contact.get("source_node_record_id", "")).casefold() or plane_id.casefold() != str(contact.get("endpoint_node_id", "")).casefold() or str(contact.get("opposite_endpoint_node_id", "")).casefold() != external_id.casefold():
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact endpoint aliases differ")
        expected_via = f"via:{str(contact.get('via_id', '')).strip()}:{net}"
        if via_record_id.casefold() != expected_via.casefold() or not paddef_id.casefold().startswith(f"paddef:{str(contact.get('padstack_id', '')).strip()}:{layer}:".casefold()) or not regular_id.casefold().startswith(f"regular:{str(contact.get('padstack_id', '')).strip()}:{layer}:".casefold()):
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact source identity differs")
        for name in ("plane", "external", "via"):
            if str(refs[name].get("logical_net", "")).casefold() != net.casefold():
                _fail("CONTACT_ADMISSIBILITY_INVALID: contact source net differs")
        if any(str(refs[name].get("layer", "")).casefold() != layer.casefold() for name in ("plane", "paddef", "regular")):
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact source layer differs")
        plane_matches = [row for row in nodes if str(row.node_id).casefold() == plane_id.casefold() and str(row.net_name or "").casefold() == net.casefold() and str(row.layer_id).casefold() == layer.casefold() and str(row.source_record_sha256) == str(refs["plane"].get("source_record_sha256"))]
        external_matches = [row for row in nodes if str(row.node_id).casefold() == external_id.casefold() and str(row.net_name or "").casefold() == net.casefold() and str(row.source_record_sha256) == str(refs["external"].get("source_record_sha256"))]
        via_matches = [row for row in vias if str(row.via_id).casefold() == str(contact.get("via_id", "")).casefold() and str(row.net_name or "").casefold() == net.casefold() and str(row.source_record_sha256) == str(refs["via"].get("source_record_sha256"))]
        if len(plane_matches) != 1 or len(external_matches) != 1 or len(via_matches) != 1:
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact raw provenance is ambiguous")
        plane, external, via = plane_matches[0], external_matches[0], via_matches[0]
        if via.status != "EXACT" or str(via.padstack_id).casefold() != str(contact.get("padstack_id", "")).casefold():
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact Via is not exact or padstack differs")
        if plane_id.casefold() == external_id.casefold():
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact endpoints must differ")
        if plane_id.casefold() == str(via.start_node_id).casefold() and external_id.casefold() == str(via.end_node_id).casefold():
            plane_layer, plane_xy = str(via.start_layer_id), (via.start_x_pm, via.start_y_pm)
            external_layer, external_xy = str(via.end_layer_id), (via.end_x_pm, via.end_y_pm)
        elif plane_id.casefold() == str(via.end_node_id).casefold() and external_id.casefold() == str(via.start_node_id).casefold():
            plane_layer, plane_xy = str(via.end_layer_id), (via.end_x_pm, via.end_y_pm)
            external_layer, external_xy = str(via.start_layer_id), (via.start_x_pm, via.start_y_pm)
        else:
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact endpoint coordinates differ")
        if plane_layer.casefold() != layer.casefold() or (plane.x_pm, plane.y_pm) != plane_xy or str(external.layer_id).casefold() != external_layer.casefold() or (external.x_pm, external.y_pm) != external_xy:
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact Via endpoint layer/coordinate differs")
        if str(refs["external"].get("layer", "")).casefold() != external_layer.casefold():
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact external source layer differs")
        rotation = float(contact.get("rotation_degrees"))
        normalized = ((float(via.rotation_microdegrees) / 1_000_000.0 + 180.0) % 360.0) - 180.0
        if not -180.0 <= rotation < 180.0 or not math.isclose(rotation, normalized, rel_tol=0.0, abs_tol=1.0e-9):
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact rotation differs")
        pad_matches = [row for row in pads if str(row.padstack_id).casefold() == str(contact.get("padstack_id", "")).casefold() and str(row.layer_id).casefold() == layer.casefold() and int(row.ordinal) == int(contact.get("raw_pad_shape_ordinal", -1)) and str(row.source_record_sha256) == str(contact.get("raw_pad_shape_sha256", ""))]
        if len(pad_matches) != 1:
            _fail("CONTACT_ADMISSIBILITY_INVALID: contact PadShape provenance differs")
        pad = pad_matches[0]
        x_um, y_um = float(plane.x_pm) * 1e-6, float(plane.y_pm) * 1e-6
        width_um, height_um = float(pad.width_pm) * 1e-6, float(pad.height_pm) * 1e-6
        if pad.shape_kind == "CIRCLE":
            footprint = Point(x_um, y_um).buffer(width_um / 2.0, quad_segs=64)
        else:
            footprint = box(x_um - width_um / 2.0, y_um - height_um / 2.0, x_um + width_um / 2.0, y_um + height_um / 2.0)
            if rotation:
                footprint = rotate(footprint, rotation, origin=(x_um, y_um))
        cid = _text(contact.get("contact_id"), "contact id")
        port = SurfacePatchFinitePort(cid, layer, artwork_net, footprint, "source-plane-ownership-ir-v2")
        metadata.append({"contact_id": cid, "owner_kind": _text(contact.get("owner_kind"), "contact owner kind"), "port": port, "surface_id": str(matches[0].get("surface_id", "")).casefold(), "artwork_net": artwork_net})
    return tuple(metadata)


def _surface_geometry(surface: Mapping[str, Any], primitives: list[Any], vertices: list[Any], circles: list[Any], expected_ir: list[Mapping[str, Any]]) -> Any:
    name = _text(surface.get("geometry_asset_name"), "surface geometry asset")
    asset_sha = _text(surface.get("geometry_asset_sha256"), "surface geometry hash")
    net = _text(surface.get("artwork_net"), "surface net")
    layer = _text(surface.get("layer"), "surface layer")
    selected_ordinals = {int(row.get("raw_primitive_ordinal", -1)) for row in expected_ir}
    if not selected_ordinals or len(selected_ordinals) != len(expected_ir):
        _fail("selected surface primitive ordinal set is invalid")
    candidates = [row for row in primitives if int(row.primitive_ordinal) in selected_ordinals and str(row.net_name).casefold() == net.casefold() and str(row.layer_name).casefold() == layer.casefold() and row.source_asset_name.casefold() == name.casefold() and row.source_asset_sha256 == asset_sha]
    if len(candidates) != len(selected_ordinals):
        _fail("selected surface has no exact raw plane primitive set")
    expected = sorted(candidates, key=lambda row: row.primitive_ordinal)
    ordered_ir = sorted(expected_ir, key=lambda row: int(row.get("local_ordinal", -1)))
    if len(ordered_ir) != len(candidates) or [int(row.get("local_ordinal", -1)) for row in ordered_ir] != list(range(len(ordered_ir))):
        _fail("IR/raw primitive order differs")
    for ir_row, raw_row in zip(ordered_ir, candidates, strict=True):
        ir_kind = str(ir_row.get("kind", "")).casefold()
        expected_kind = "circle" if ir_kind == "circle" else "polygon" if ir_kind in {"polygon", "polygontrace", "box"} else ""
        if (str(ir_row.get("polarity", "")) != str(raw_row.polarity)
                or expected_kind != str(raw_row.kind).casefold()
                or str(ir_row.get("source_asset_name", "")).casefold() != name.casefold()
                or str(ir_row.get("source_asset_sha256", "")) != asset_sha
                or str(ir_row.get("primitive_sha256", "")) != str(raw_row.primitive_sha256)
                or int(ir_row.get("raw_primitive_ordinal", -1)) != int(raw_row.primitive_ordinal)):
            _fail("IR/raw primitive provenance differs")
    geometry_ir = [row for row in ordered_ir if str(row.get("effect_status", "")).casefold() == "retained"]
    geometry_ordinals = {int(row.get("raw_primitive_ordinal", -1)) for row in geometry_ir}
    vertices_by: dict[int, list[Any]] = {}
    circles_by: dict[int, list[Any]] = {}
    for row in vertices:
        vertices_by.setdefault(int(row.primitive_ordinal), []).append(row)
    for row in circles:
        circles_by.setdefault(int(row.primitive_ordinal), []).append(row)
    payload: dict[str, Any] = {"positive_polygons_um": [], "negative_polygons_um": [], "positive_circles_um": [], "negative_circles_um": [], "primitive_order": []}
    for row in expected:
        if int(row.primitive_ordinal) not in geometry_ordinals:
            continue
        polarity = str(row.polarity)
        kind = str(row.kind).casefold()
        if kind == "polygon":
            points = tuple((float(item.x_um), float(item.y_um)) for item in sorted(vertices_by.get(row.primitive_ordinal, ()), key=lambda item: item.vertex_ordinal))
            if len(points) < 3:
                _fail("raw polygon geometry is incomplete")
            order_key = ("positive_" if polarity == "+" else "negative_") + "polygon"
            storage_key = order_key + "s_um"
            payload[storage_key].append(points)
        elif kind == "circle":
            circles_for = circles_by.get(row.primitive_ordinal, ())
            if len(circles_for) != 1:
                _fail("raw circle geometry is incomplete")
            circle = circles_for[0]
            order_key = ("positive_" if polarity == "+" else "negative_") + "circle"
            storage_key = order_key + "s_um"
            payload[storage_key].append((float(circle.center_x_um), float(circle.center_y_um), float(circle.radius_um)))
        else:
            _fail("unsupported raw plane primitive kind")
        payload["primitive_order"].append((order_key, len(payload[storage_key]) - 1))
    geometry = ordered_spd_geometry(payload)
    if geometry is None or geometry.is_empty or float(geometry.area) <= 0.0:
        _fail("ordered source plane geometry is empty")
    return geometry


def _surface_island_gate(surface: Mapping[str, Any], islands: list[Mapping[str, Any]], edges: list[Mapping[str, Any]], primitive_ids: set[str]) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    sid = _text(surface.get("surface_id"), "surface_id").casefold()
    selected = [row for row in islands if str(row.get("surface_id", "")).casefold() == sid]
    if len(selected) != 1 or int(surface.get("component_count", -1)) != 1:
        _fail("analytic coupon requires exactly one surface island/component")
    island = selected[0]
    iid = _text(island.get("island_id"), "island_id").casefold()
    related = tuple(row for row in edges if str(row.get("island_id", "")).casefold() == iid and str(row.get("primitive_id", "")).casefold() in primitive_ids)
    if not related or int(island.get("positive_witness_count", 0)) != sum(1 for row in related if row.get("witness_kind") == "positive_area_witness"):
        _fail("analytic coupon island witness set is incomplete")
    return island, related


def _rectangle(geometry: Any) -> tuple[float, float, float, float]:
    if getattr(geometry, "geom_type", None) != "Polygon" or len(getattr(geometry.exterior, "coords", ())) != 5 or len(geometry.interiors) != 0:
        _fail("analytic gate requires one rectangular patch")
    min_x, min_y, max_x, max_y = (float(value) for value in geometry.bounds)
    width, length = max_x - min_x, max_y - min_y
    if not all(math.isfinite(value) and value > 0.0 for value in (width, length)) or not math.isclose(float(geometry.area), width * length, rel_tol=1e-12, abs_tol=1e-12):
        _fail("patch geometry is not an axis-aligned rectangle")
    return min(width, length), max(width, length), min_x, min_y


def consume_source_plane_patch(
    ownership_manifest: Mapping[str, Any],
    ownership_attachments: Mapping[str, bytes],
    raw_manifest: Mapping[str, Any],
    raw_attachments: Mapping[str, bytes],
    *,
    rail_id: str,
    frequency_hz: float,
    cell_um: float,
) -> Mapping[str, Any]:
    """Build one deterministic, shadow-only source-plane finite-port witness."""
    rail_id = _text(rail_id, "rail_id")
    if not isinstance(frequency_hz, (int, float)) or isinstance(frequency_hz, bool) or not math.isfinite(float(frequency_hz)) or frequency_hz <= 0.0:
        _fail("frequency_hz is invalid")
    if not isinstance(cell_um, (int, float)) or isinstance(cell_um, bool) or not math.isfinite(float(cell_um)) or cell_um <= 0.0:
        _fail("cell_um is invalid")
    if not isinstance(ownership_manifest, Mapping) or not isinstance(raw_manifest, Mapping):
        _fail("asset manifests must be mappings")
    _binding_check(ownership_manifest, raw_manifest)
    ir_total = [0]
    with load_source_plane_ownership_ir(ownership_manifest, ownership_attachments, expected_source_sha256=str(raw_manifest["source_sha256"]), expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]), expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]), expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]), expected_raw_manifest_sha256=str(ownership_manifest["raw_manifest_sha256"]), expected_raw_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]), expected_raw_logical_rows_sha256=str(raw_manifest["logical_rows_sha256"]), expected_raw_plane_sheet_sha256=str(raw_manifest["plane_sheet_payload_sha256"]), expected_app_version=str(ownership_manifest.get("app_version", ""))) as ownership:
        ir_rows = {section: _rows(ownership, section, MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, ir_total) for section in ("source_records", "surfaces", "primitives", "islands", "primitive_island_edges", "stackup_layers", "dielectric_points", "rail_bindings", "terminal_bindings", "retained_owner_refs", "plane_owner_scopes", "replacement_ledger", "replacement_ledger_members")}
    power, ground, selected_owners = _owner_gate(ir_rows, rail_id)
    target_rail_bindings = [
        row for row in ir_rows["rail_bindings"]
        if str(row.get("rail_id", "")).casefold() == rail_id.casefold()
    ]
    power_binding_rows = [
        row for row in target_rail_bindings
        if str(row.get("role", "")).casefold() == "power"
    ]
    ground_binding_rows = [
        row for row in target_rail_bindings
        if str(row.get("role", "")).casefold() == "ground"
    ]
    if len(power_binding_rows) != 1 or len(ground_binding_rows) != 1:
        _fail("target rail binding is incomplete")
    power_binding, ground_binding = power_binding_rows[0], ground_binding_rows[0]
    rail_bindings = {"power": power_binding, "ground": ground_binding}
    source_records = ir_rows["source_records"]
    source_by_id = {str(row.get("record_id", "")).casefold(): row for row in source_records}
    if len(source_by_id) != len(source_records):
        _fail("source-record identity is ambiguous")
    power_surface_id = str(power_binding.get("surface_id", "")).strip().casefold()
    ground_surface_id = str(ground_binding.get("surface_id", "")).strip().casefold()
    if not power_surface_id or not ground_surface_id or power_surface_id == ground_surface_id:
        _fail("rail surface binding is unresolved")
    power_surface = next((row for row in ir_rows["surfaces"] if str(row.get("surface_id", "")).casefold() == power_surface_id), None)
    ground_surface = next((row for row in ir_rows["surfaces"] if str(row.get("surface_id", "")).casefold() == ground_surface_id), None)
    if power_surface is None or ground_surface is None:
        _fail("rail surface binding is unresolved")
    selected_surface_ids = {power_surface_id, ground_surface_id}
    selected_terminal_rows = (power, ground)
    selected_primitive_rows = [
        row for row in ir_rows["primitives"]
        if str(row.get("surface_id", "")).casefold() in selected_surface_ids
    ]
    selected_primitive_ordinals = {
        int(row.get("raw_primitive_ordinal", -1))
        for row in selected_primitive_rows
    }
    endpoint_node_ids = {
        str(row.get("endpoint_node_id", "")).strip().casefold()
        for row in selected_terminal_rows
        if str(row.get("endpoint_node_id", "")).strip()
    }
    terminal_node_record_shas: set[str] = set()
    terminal_via_record_shas: set[str] = set()
    for terminal in selected_terminal_rows:
        for field, target in (("source_node_record_id", terminal_node_record_shas), ("via_record_id", terminal_via_record_shas)):
            record = source_by_id.get(str(terminal.get(field, "")).casefold())
            if record is None or not str(record.get("source_record_sha256", "")).strip():
                _fail("terminal source record provenance is absent")
            target.add(str(record["source_record_sha256"]))
    pad_keys = {
        (
            str(row.get("padstack_id", "")).casefold(),
            str(row.get("layer", "")).casefold(),
            int(row.get("raw_pad_shape_ordinal", -1)),
            str(row.get("raw_pad_shape_sha256", "")),
        )
        for row in selected_terminal_rows
    }
    raw_total = [0]
    with load_raw_spatial_contact_asset(raw_manifest, raw_attachments, expected_source_sha256=str(raw_manifest["source_sha256"]), expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]), expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]), expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]), expected_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]), require_plane_sheet_payload=True) as raw:
        raw_primitives = _raw_rows(raw, "plane_primitives", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in selected_primitive_ordinals)
        raw_surfaces = _raw_rows(raw, "surfaces", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: str(row.surface_id).casefold() in selected_surface_ids)
        raw_nodes = _raw_rows(raw, "nodes", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: str(row.source_record_sha256) in terminal_node_record_shas or str(row.node_id).casefold() in endpoint_node_ids)
        raw_vias = _raw_rows(raw, "vias", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: str(row.source_record_sha256) in terminal_via_record_shas)
        raw_pads = _raw_rows(raw, "pad_shapes", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: (str(row.padstack_id).casefold(), str(row.layer_id).casefold(), int(row.ordinal), str(row.source_record_sha256)) in pad_keys)
        raw_vertices = _raw_rows(raw, "plane_vertices", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in selected_primitive_ordinals)
        raw_circles = _raw_rows(raw, "plane_circles", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in selected_primitive_ordinals)
        raw_stackup = _raw_rows(raw, "stackup_layers", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total)
        raw_points = _raw_rows(raw, "dielectric_points", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total)

    selected_islands: dict[str, tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]] = {}
    for surface in (power_surface, ground_surface):
        primitive_ids = {str(row.get("primitive_id", "")).casefold() for row in selected_primitive_rows if str(row.get("surface_id", "")).casefold() == str(surface.get("surface_id", "")).casefold()}
        selected_islands[str(surface["surface_id"]).casefold()] = _surface_island_gate(surface, ir_rows["islands"], ir_rows["primitive_island_edges"], primitive_ids)
        for primitive in selected_primitive_rows:
            if str(primitive.get("surface_id", "")).casefold() == str(surface.get("surface_id", "")).casefold():
                source = source_by_id.get(str(primitive.get("source_record_id", "")).casefold())
                if source is None or str(source.get("kind", "")).casefold() != "shape":
                    _fail("primitive Shape source provenance is absent")
    for surface in (power_surface, ground_surface):
        matches = [row for row in raw_surfaces if str(row.surface_id).casefold() == str(surface.get("surface_id", "")).casefold() and str(row.net_name).casefold() == str(surface.get("artwork_net", "")).casefold() and str(row.layer_id).casefold() == str(surface.get("layer", "")).casefold() and str(row.artwork_asset_sha256) == str(surface.get("geometry_asset_sha256", "")) and str(row.island_manifest_sha256) == str(surface.get("island_manifest_sha256", ""))]
        if len(matches) != 1:
            _fail("IR/raw surface identity differs")
    power_geometry = _surface_geometry(power_surface, raw_primitives, raw_vertices, raw_circles, [row for row in selected_primitive_rows if str(row.get("surface_id", "")).casefold() == str(power_surface.get("surface_id", "")).casefold()])
    ground_geometry = _surface_geometry(ground_surface, raw_primitives, raw_vertices, raw_circles, [row for row in selected_primitive_rows if str(row.get("surface_id", "")).casefold() == str(ground_surface.get("surface_id", "")).casefold()])
    width_um, length_um, min_x, min_y = _rectangle(power_geometry)
    g_width, g_length, g_min_x, g_min_y = _rectangle(ground_geometry)
    if not (math.isclose(width_um, g_width, rel_tol=1e-12, abs_tol=1e-12) and math.isclose(length_um, g_length, rel_tol=1e-12, abs_tol=1e-12) and math.isclose(min_x, g_min_x, rel_tol=0.0, abs_tol=1e-12) and math.isclose(min_y, g_min_y, rel_tol=0.0, abs_tol=1e-12)):
        _fail("power/ground patch rectangles differ")
    pwr_layer = str(power_binding.get("layer"))
    gnd_layer = str(ground_binding.get("layer"))
    layer_index = {str(row.layer_name).casefold(): index for index, row in enumerate(raw_stackup)}
    if pwr_layer.casefold() not in layer_index or gnd_layer.casefold() not in layer_index:
        _fail("rail conductor layer is absent from raw stackup")
    lo, hi = sorted((layer_index[pwr_layer.casefold()], layer_index[gnd_layer.casefold()]))
    between = raw_stackup[lo + 1 : hi]
    if len(between) != 1 or str(between[0].layer_kind).casefold() != "dielectric":
        _fail("analytic gate requires one dielectric layer between rail conductors")
    selected_layer_names = {pwr_layer.casefold(), gnd_layer.casefold(), str(between[0].layer_name).casefold()}
    conductor_rows = [row for row in raw_stackup if str(row.layer_name).casefold() in {pwr_layer.casefold(), gnd_layer.casefold()}]
    if len(conductor_rows) != 2 or any(str(row.layer_name).casefold() not in selected_layer_names for row in raw_stackup[lo : hi + 1]):
        _fail("selected rail conductor pair is incomplete")
    ir_stackup = {str(row.get("layer_name", "")).casefold(): row for row in ir_rows["stackup_layers"]}
    for row in raw_stackup[lo : hi + 1]:
        ir_row = ir_stackup.get(str(row.layer_name).casefold())
        if (ir_row is None or int(ir_row.get("raw_layer_ordinal", -1)) != int(row.layer_ordinal)
                or str(ir_row.get("raw_layer_sha256", "")) != _row_hash(row)
                or str(ir_row.get("layer_kind", "")).casefold() != str(row.layer_kind).casefold()
                or str(ir_row.get("layer_name", "")).casefold() != str(row.layer_name).casefold()
                or float(ir_row.get("thickness_um", -1.0)) != float(row.thickness_um)
                or ir_row.get("conductivity_s_per_m") != row.conductivity_s_per_m
                or str(ir_row.get("material_name", "")) != str(row.material_name)):
            _fail("IR/raw stackup provenance differs")
    dielectric = between[0]
    ordered_points = list(raw_points)
    points = [row for row in ordered_points if int(row.layer_ordinal) == int(dielectric.layer_ordinal) and float(row.frequency_hz) == float(frequency_hz)]
    if len(points) != 1:
        _fail("dielectric frequency point is absent or ambiguous")
    point = points[0]
    ir_point = next((row for row in ir_rows["dielectric_points"] if str(row.get("layer_name", "")).casefold() == str(dielectric.layer_name).casefold() and int(row.get("point_ordinal", -1)) == int(point.point_ordinal)), None)
    global_point_ordinal = ordered_points.index(point)
    if (ir_point is None or int(ir_point.get("raw_dielectric_ordinal", -1)) != global_point_ordinal
            or str(ir_point.get("raw_dielectric_sha256", "")) != _row_hash(point)
            or float(ir_point.get("frequency_hz", -1.0)) != float(point.frequency_hz)
            or float(ir_point.get("epsilon_r", -1.0)) != float(point.epsilon_r)
            or float(ir_point.get("loss_tangent", -1.0)) != float(point.loss_tangent)):
        _fail("IR/raw dielectric provenance differs")
    pwr = next(row for row in raw_stackup if str(row.layer_name).casefold() == pwr_layer.casefold())
    gnd = next(row for row in raw_stackup if str(row.layer_name).casefold() == gnd_layer.casefold())
    if any(row.conductivity_s_per_m is None or float(row.conductivity_s_per_m) <= 0.0 for row in (pwr, gnd)):
        _fail("selected conductor conductivity is unavailable")
    separation_um = float(dielectric.thickness_um)
    width_m, length_m, area_m2, separation_m = width_um * 1e-6, length_um * 1e-6, width_um * length_um * 1e-12, separation_um * 1e-6
    rdc = (length_m / width_m) * (1.0 / (float(pwr.conductivity_s_per_m) * float(pwr.thickness_um) * 1e-6) + 1.0 / (float(gnd.conductivity_s_per_m) * float(gnd.thickness_um) * 1e-6))
    inductance = MU_0_H_PER_M * separation_m * length_m / width_m
    capacitance = EPSILON_0_F_PER_M * float(point.epsilon_r) * area_m2 / separation_m
    layer_order = tuple(str(row.layer_name) for row in raw_stackup[lo : hi + 1] if str(row.layer_kind).casefold() == "conductor")
    mesh = SurfacePatchMesh.uniform(layer_order=layer_order, artwork=(SurfacePatchArtwork(pwr_layer, str(power_surface["artwork_net"]), power_geometry), SurfacePatchArtwork(gnd_layer, str(ground_surface["artwork_net"]), ground_geometry)), cell_um=float(cell_um), max_cells=MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS)
    conductors = {str(row.layer_name): SurfacePatchConductor(float(row.conductivity_s_per_m), float(row.thickness_um) * 1e-6, provenance=f"raw:{row.layer_ordinal}") for row in conductor_rows}
    upper_layer, lower_layer = (str(raw_stackup[lo].layer_name), str(raw_stackup[hi].layer_name))
    dielectric_model = SurfacePatchDielectric(upper_layer, lower_layer, separation_m, float(point.epsilon_r), float(point.loss_tangent), provenance=f"raw:{dielectric.layer_ordinal}:{point.point_ordinal}")
    operator = compile_surface_patch_plane(mesh, conductors=conductors, dielectrics=(dielectric_model,))
    pwr_port, gnd_port = _terminal_raw_gate((power, ground), source_records, raw_nodes, raw_vias, raw_pads, rail_bindings)
    condensation = operator.condense_finite_ports(float(frequency_hz), (SurfacePatchFinitePort(str(power["terminal_id"]), pwr_layer, str(power_surface["artwork_net"]), pwr_port, "source-plane-ownership-ir"), SurfacePatchFinitePort(str(ground["terminal_id"]), gnd_layer, str(ground_surface["artwork_net"]), gnd_port, "source-plane-ownership-ir")))
    diagnostics = {name: float(getattr(condensation, name)) for name in ("solve_residual", "compatible_current_residual", "gauge_residual", "reciprocity_relative", "passivity_min_eigenvalue_s", "passivity_tolerance_s", "terminal_condition")}
    witness = {
        "rail_bindings": [dict(power_binding), dict(ground_binding)],
        "terminals": [dict(power), dict(ground)],
        "surfaces": [dict(power_surface), dict(ground_surface)],
        "islands": [selected_islands[str(power_surface["surface_id"]).casefold()][0], selected_islands[str(ground_surface["surface_id"]).casefold()][0]],
        "primitives": [dict(row) for row in selected_primitive_rows],
        "edges": [dict(row) for item in selected_islands.values() for row in item[1]],
        "owners": selected_owners,
        "owner_scopes": [dict(row) for row in ir_rows["plane_owner_scopes"]],
        "ledger": [dict(row) for row in ir_rows["replacement_ledger"] + ir_rows["replacement_ledger_members"]],
        "bindings": {
            "source_size_bytes": int(ownership_manifest.get("source_size_bytes", 0)),
            "ownership_logical_rows_sha256": ownership_manifest.get("logical_rows_sha256"),
            "raw_logical_rows_sha256": ownership_manifest.get("raw_logical_rows_sha256"),
            "raw_geometry_identity_sha256": ownership_manifest.get("raw_geometry_identity_sha256"),
            "raw_plane_sheet_sha256": ownership_manifest.get("raw_plane_sheet_sha256"),
            "source_records": [dict(row) for row in source_records if str(row.get("record_id", "")).casefold() in ({str(item.get("source_record_id", "")).casefold() for item in selected_primitive_rows} | {str(item.get(field, "")).casefold() for item in (power, ground) for field in ("source_node_record_id", "via_record_id", "paddef_source_record_id", "regular_source_record_id")} | {str(item.get(field, "")).casefold() for item in ir_rows["stackup_layers"] if str(item.get("layer_name", "")).casefold() in selected_layer_names for field in ("thickness_source_record_id", "conductivity_source_record_id", "material_source_record_id")} | {str(item.get(field, "")).casefold() for item in ir_rows["dielectric_points"] if str(item.get("layer_name", "")).casefold() == str(dielectric.layer_name).casefold() for field in ("frequency_source_record_id", "epsilon_source_record_id", "loss_tangent_source_record_id")})],
            "stackup_layers": [dict(row) for row in ir_rows["stackup_layers"] if str(row.get("layer_name", "")).casefold() in selected_layer_names],
            "dielectric_points": [dict(row) for row in ir_rows["dielectric_points"] if str(row.get("layer_name", "")).casefold() == str(dielectric.layer_name).casefold()],
        },
    }
    result: dict[str, Any] = {"schema_version": "source-plane-patch-v1", "shadow_only": True, "rail_id": rail_id, "terminal_ids": list(condensation.port_ids), "owner_ids": selected_owners, "source_sha256": ownership_manifest["source_sha256"], "raw_manifest_sha256": ownership_manifest["raw_manifest_sha256"], "geometry": {"width_um": width_um, "length_um": length_um, "area_m2": area_m2, "separation_um": separation_um, "cell_um": float(cell_um)}, "analytic": {"frequency_hz": float(frequency_hz), "rdc_ohm": rdc, "inductance_h": inductance, "capacitance_f": capacitance}, "condensation": {"port_ids": list(condensation.port_ids), "admittance_s": [[[float(value.real), float(value.imag)] for value in row] for row in condensation.terminal_admittance_s.tolist()], "diagnostics": diagnostics}, "mesh_diagnostics": _object_mapping(mesh.diagnostics), "operator_diagnostics": _object_mapping(operator.diagnostics), "witness": witness}
    result["witness_sha256"] = sha256(concrete_canonical_json_bytes(result)).hexdigest()
    return result


def evaluate_source_plane_contact_admissibility(
    ownership_manifest: Mapping[str, Any],
    ownership_attachments: Mapping[str, bytes],
    raw_manifest: Mapping[str, Any],
    raw_attachments: Mapping[str, bytes],
    *,
    rail_id: str,
) -> Mapping[str, Any]:
    """Check v2 contact footprints against their selected source artwork."""
    rail_id = _text(rail_id, "rail_id")
    if not isinstance(ownership_manifest, Mapping) or not isinstance(raw_manifest, Mapping):
        _fail("CONTACT_ADMISSIBILITY_INVALID: asset manifests are not mappings")
    manifest_rail = ownership_manifest.get("target_rail_id")
    if not isinstance(manifest_rail, str) or not manifest_rail.strip():
        _fail("CONTACT_SURFACE_BINDING_INVALID: ownership target rail is absent")
    manifest_rail = _text(manifest_rail, "ownership target rail")
    if manifest_rail.casefold() != rail_id.casefold():
        _fail("CONTACT_SURFACE_BINDING_INVALID: requested rail differs from ownership target rail")
    rail_id = manifest_rail
    if "contact_boundary" not in ownership_manifest.get("counts", {}):
        _fail("CONTACT_IR_V2_REQUIRED: ownership IR v2 is required")
    try:
        _binding_check(ownership_manifest, raw_manifest)
        ir_total = [0]
        with load_source_plane_ownership_ir(
            ownership_manifest,
            ownership_attachments,
            expected_source_sha256=str(raw_manifest["source_sha256"]),
            expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]),
            expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]),
            expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]),
            expected_raw_manifest_sha256=str(ownership_manifest["raw_manifest_sha256"]),
            expected_raw_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]),
            expected_raw_logical_rows_sha256=str(raw_manifest["logical_rows_sha256"]),
            expected_raw_plane_sheet_sha256=str(raw_manifest["plane_sheet_payload_sha256"]),
            expected_app_version=str(ownership_manifest.get("app_version", "")),
        ) as ownership:
            sections = ("source_records", "surfaces", "primitives", "islands", "rail_bindings", "contact_boundary")
            ir_rows = {section: _rows(ownership, section, MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, ir_total) for section in sections}
        contacts = sorted(ir_rows["contact_boundary"], key=lambda row: (int(row.get("ordinal", 0)), str(row.get("contact_id", "")).casefold()))
        if not contacts:
            _fail("CONTACT_BOUNDARY_EMPTY: no contact boundaries are present")
        bindings = [row for row in ir_rows["rail_bindings"] if str(row.get("rail_id", "")).casefold() == rail_id.casefold() and row.get("state") == "source_bound"]
        if len(bindings) != 2 or {str(row.get("role", "")).casefold() for row in bindings} != {"power", "ground"}:
            _fail("CONTACT_SURFACE_BINDING_INVALID: selected rail bindings are incomplete")
        source_records = ir_rows["source_records"]
        source_by_id = {str(row.get("record_id", "")).casefold(): row for row in source_records}
        if len(source_by_id) != len(source_records):
            _fail("CONTACT_ADMISSIBILITY_INVALID: source-record identity is ambiguous")
        islands = {str(row.get("island_id", "")).casefold(): row for row in ir_rows["islands"]}
        surfaces = {str(row.get("surface_id", "")).casefold(): row for row in ir_rows["surfaces"]}
        target_surface_ids: set[str] = set()
        node_record_shas: set[str] = set()
        via_record_shas: set[str] = set()
        endpoint_ids: set[str] = set()
        pad_keys: set[tuple[str, str, int, str]] = set()
        for contact in contacts:
            net = _text(contact.get("net"), "contact net")
            layer = _text(contact.get("endpoint_layer"), "contact layer")
            matched = [row for row in bindings if str(row.get("logical_net", "")).casefold() == net.casefold() and str(row.get("layer", "")).casefold() == layer.casefold()]
            if len(matched) != 1:
                _fail("CONTACT_SURFACE_BINDING_INVALID: contact rail binding is ambiguous")
            binding = matched[0]
            island = islands.get(str(contact.get("island_id", "")).casefold())
            if island is None or str(island.get("surface_id", "")).casefold() != str(binding.get("surface_id", "")).casefold() or str(island.get("component_id", "")).casefold() != str(contact.get("component_id", "")).casefold() or str(island.get("island_id", "")).casefold() != str(binding.get("island_id", "")).casefold():
                _fail("CONTACT_SURFACE_BINDING_INVALID: contact island/component differs from rail binding")
            target_surface_ids.add(str(binding.get("surface_id", "")).casefold())
            endpoint_ids.update(str(contact.get(key, "")).casefold() for key in ("endpoint_node_id", "opposite_endpoint_node_id", "plane_endpoint_node_id", "external_endpoint_node_id") if str(contact.get(key, "")).strip())
            for key, target in (("source_node_record_id", node_record_shas), ("opposite_endpoint_node_record_id", node_record_shas), ("plane_endpoint_node_record_id", node_record_shas), ("external_endpoint_node_record_id", node_record_shas), ("via_record_id", via_record_shas)):
                record = source_by_id.get(str(contact.get(key, "")).casefold())
                if record is None or not str(record.get("source_record_sha256", "")).strip():
                    _fail("CONTACT_ADMISSIBILITY_INVALID: contact source record provenance is absent")
                target.add(str(record["source_record_sha256"]))
            pad_keys.add((str(contact.get("padstack_id", "")).casefold(), layer.casefold(), int(contact.get("raw_pad_shape_ordinal", -1)), str(contact.get("raw_pad_shape_sha256", ""))))
        selected_primitives = [row for row in ir_rows["primitives"] if str(row.get("surface_id", "")).casefold() in target_surface_ids]
        selected_ordinals = {int(row.get("raw_primitive_ordinal", -1)) for row in selected_primitives}
        with load_raw_spatial_contact_asset(
            raw_manifest,
            raw_attachments,
            expected_source_sha256=str(raw_manifest["source_sha256"]),
            expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]),
            expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]),
            expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]),
            expected_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]),
            require_plane_sheet_payload=True,
        ) as raw:
            raw_total = [0]
            raw_primitives = _raw_rows(raw, "plane_primitives", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in selected_ordinals)
            raw_vertices = _raw_rows(raw, "plane_vertices", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in selected_ordinals)
            raw_circles = _raw_rows(raw, "plane_circles", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in selected_ordinals)
            raw_nodes = _raw_rows(raw, "nodes", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: str(row.source_record_sha256) in node_record_shas or str(row.node_id).casefold() in endpoint_ids)
            raw_vias = _raw_rows(raw, "vias", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: str(row.source_record_sha256) in via_record_shas)
            raw_pads = _raw_rows(raw, "pad_shapes", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: (str(row.padstack_id).casefold(), str(row.layer_id).casefold(), int(row.ordinal), str(row.source_record_sha256)) in pad_keys)
        geometries: dict[str, Any] = {}
        for sid in target_surface_ids:
            surface = surfaces.get(sid)
            if surface is None:
                _fail("CONTACT_SURFACE_BINDING_INVALID: selected surface is absent")
            expected = [row for row in selected_primitives if str(row.get("surface_id", "")).casefold() == sid]
            try:
                geometries[sid] = _surface_geometry(surface, raw_primitives, raw_vertices, raw_circles, expected)
            except SourcePlanePatchError as exc:
                _fail(f"CONTACT_GEOMETRY_INVALID: {exc}")
        metadata = _contact_raw_gate(contacts, source_records, raw_nodes, raw_vias, raw_pads, bindings)
        rows: list[dict[str, Any]] = []
        for contact, item in zip(contacts, metadata, strict=True):
            surface_id = item["surface_id"]
            footprint = item["port"].geometry_um
            requested = float(footprint.area)
            try:
                covered = float(geometries[surface_id].intersection(footprint).area)
            except Exception as exc:
                _fail(f"CONTACT_GEOMETRY_INVALID: contact intersection failed: {exc}")
            tolerance = max(requested * 1.0e-10, 1.0e-9)
            if not math.isfinite(requested) or not math.isfinite(covered):
                _fail("CONTACT_GEOMETRY_INVALID: contact area is nonfinite")
            if covered <= 0.0 or abs(covered - requested) > tolerance:
                _fail(f"CONTACT_NOT_FULLY_COVERED: contact {contact.get('contact_id', '')!r} is not fully covered")
            rows.append({"contact_id": item["contact_id"], "owner_kind": item["owner_kind"], "logical_net": str(contact["net"]), "layer": str(contact["endpoint_layer"]), "artwork_net": item["artwork_net"], "covered_area_m2": covered * 1.0e-12})
        result: dict[str, Any] = {"schema_version": "source-plane-contact-admissibility-v1", "shadow_only": True, "status": "complete", "rail_id": rail_id, "source_sha256": ownership_manifest["source_sha256"], "raw_manifest_sha256": ownership_manifest["raw_manifest_sha256"], "contacts": rows}
        result["witness_sha256"] = sha256(concrete_canonical_json_bytes(result)).hexdigest()
        return result
    except SourcePlanePatchError:
        raise
    except Exception as exc:
        _fail(f"CONTACT_ADMISSIBILITY_INVALID: {exc}")


def evaluate_source_plane_contact_condensation(
    ownership_manifest: Mapping[str, Any],
    ownership_attachments: Mapping[str, bytes],
    raw_manifest: Mapping[str, Any],
    raw_attachments: Mapping[str, bytes],
    *,
    rail_id: str,
    frequency_hz: float,
    cell_um: float,
) -> Mapping[str, Any]:
    """Return a shadow-only finite-port N-port for every authenticated contact."""
    if not isinstance(frequency_hz, (int, float)) or isinstance(frequency_hz, bool) or not math.isfinite(float(frequency_hz)) or frequency_hz <= 0.0:
        _fail("CONTACT_ADMISSIBILITY_INVALID: frequency_hz is invalid")
    if not isinstance(cell_um, (int, float)) or isinstance(cell_um, bool) or not math.isfinite(float(cell_um)) or cell_um <= 0.0:
        _fail("CONTACT_ADMISSIBILITY_INVALID: cell_um is invalid")
    admissibility = evaluate_source_plane_contact_admissibility(ownership_manifest, ownership_attachments, raw_manifest, raw_attachments, rail_id=rail_id)
    canonical_rail = str(admissibility["rail_id"])
    total = [0]
    with load_source_plane_ownership_ir(
        ownership_manifest, ownership_attachments,
        expected_source_sha256=str(raw_manifest["source_sha256"]),
        expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]),
        expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]),
        expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]),
        expected_raw_manifest_sha256=str(ownership_manifest["raw_manifest_sha256"]),
        expected_raw_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]),
        expected_raw_logical_rows_sha256=str(raw_manifest["logical_rows_sha256"]),
        expected_raw_plane_sheet_sha256=str(raw_manifest["plane_sheet_payload_sha256"]),
        expected_app_version=str(ownership_manifest.get("app_version", "")),
    ) as loaded:
        sections = ("source_records", "surfaces", "primitives", "islands", "rail_bindings", "contact_boundary", "stackup_layers", "dielectric_points")
        ir = {section: _rows(loaded, section, MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, total) for section in sections}
    contacts = sorted(ir["contact_boundary"], key=lambda row: (int(row.get("ordinal", 0)), str(row.get("contact_id", "")).casefold()))
    bindings = [row for row in ir["rail_bindings"] if str(row.get("rail_id", "")).casefold() == canonical_rail.casefold() and row.get("state") == "source_bound"]
    source_records = ir["source_records"]
    records = {str(row.get("record_id", "")).casefold(): row for row in source_records}
    surfaces = {str(row.get("surface_id", "")).casefold(): row for row in ir["surfaces"]}
    selected_surface_ids = {str(row.get("surface_id", "")).casefold() for row in bindings}
    selected_primitives = [row for row in ir["primitives"] if str(row.get("surface_id", "")).casefold() in selected_surface_ids]
    ordinals = {int(row.get("raw_primitive_ordinal", -1)) for row in selected_primitives}
    node_shas: set[str] = set(); via_shas: set[str] = set(); endpoint_ids: set[str] = set(); pad_keys: set[tuple[str, str, int, str]] = set()
    for contact in contacts:
        endpoint_ids.update(str(contact.get(key, "")).casefold() for key in ("endpoint_node_id", "opposite_endpoint_node_id", "plane_endpoint_node_id", "external_endpoint_node_id") if str(contact.get(key, "")).strip())
        for key, target in (("source_node_record_id", node_shas), ("opposite_endpoint_node_record_id", node_shas), ("plane_endpoint_node_record_id", node_shas), ("external_endpoint_node_record_id", node_shas), ("via_record_id", via_shas)):
            record = records.get(str(contact.get(key, "")).casefold())
            if record is None:
                _fail("CONTACT_ADMISSIBILITY_INVALID: contact source record is absent")
            target.add(str(record["source_record_sha256"]))
        pad_keys.add((str(contact.get("padstack_id", "")).casefold(), str(contact.get("endpoint_layer", "")).casefold(), int(contact.get("raw_pad_shape_ordinal", -1)), str(contact.get("raw_pad_shape_sha256", ""))))
    with load_raw_spatial_contact_asset(
        raw_manifest, raw_attachments,
        expected_source_sha256=str(raw_manifest["source_sha256"]),
        expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]),
        expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]),
        expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]),
        expected_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]), require_plane_sheet_payload=True,
    ) as raw:
        raw_total = [0]
        primitives = _raw_rows(raw, "plane_primitives", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in ordinals)
        vertices = _raw_rows(raw, "plane_vertices", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in ordinals)
        circles = _raw_rows(raw, "plane_circles", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: int(row.primitive_ordinal) in ordinals)
        nodes = _raw_rows(raw, "nodes", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: str(row.source_record_sha256) in node_shas or str(row.node_id).casefold() in endpoint_ids)
        vias = _raw_rows(raw, "vias", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: str(row.source_record_sha256) in via_shas)
        pads = _raw_rows(raw, "pad_shapes", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total, lambda row: (str(row.padstack_id).casefold(), str(row.layer_id).casefold(), int(row.ordinal), str(row.source_record_sha256)) in pad_keys)
        stackup = _raw_rows(raw, "stackup_layers", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total)
        points = _raw_rows(raw, "dielectric_points", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, raw_total)
    geometries = {}
    for sid in selected_surface_ids:
        surface = surfaces.get(sid)
        if surface is None:
            _fail("CONTACT_SURFACE_BINDING_INVALID: selected surface is absent")
        try:
            geometries[sid] = _surface_geometry(surface, primitives, vertices, circles, [row for row in selected_primitives if str(row.get("surface_id", "")).casefold() == sid])
        except SourcePlanePatchError as exc:
            _fail(f"CONTACT_GEOMETRY_INVALID: {exc}")
    metadata = _contact_raw_gate(contacts, source_records, nodes, vias, pads, bindings)
    if [(str(row["contact_id"]), str(row["owner_kind"])) for row in metadata] != [(str(row["contact_id"]), str(row["owner_kind"])) for row in admissibility["contacts"]]:
        _fail("CONTACT_CONDENSATION_INVALID: admissibility contact order differs")
    layer_by_name = {str(row.layer_name).casefold(): row for row in stackup}
    rail_layers = {str(row.get("role", "")).casefold(): str(row.get("layer", "")) for row in bindings}
    if set(rail_layers) != {"power", "ground"} or any(layer.casefold() not in layer_by_name for layer in rail_layers.values()):
        _fail("CONTACT_SURFACE_BINDING_INVALID: rail conductor layer is absent")
    power_layer, ground_layer = rail_layers["power"], rail_layers["ground"]
    indices = {name: index for index, name in enumerate(str(row.layer_name).casefold() for row in stackup)}
    lo, hi = sorted((indices[power_layer.casefold()], indices[ground_layer.casefold()]))
    between = stackup[lo + 1:hi]
    if len(between) != 1 or str(between[0].layer_kind).casefold() != "dielectric":
        _fail("CONTACT_SURFACE_BINDING_INVALID: adjacent conductor pair is not exact")
    dielectric = between[0]
    ir_stackup = {str(row.get("layer_name", "")).casefold(): row for row in ir["stackup_layers"]}
    for row in stackup[lo:hi + 1]:
        ir_row = ir_stackup.get(str(row.layer_name).casefold())
        if (ir_row is None or int(ir_row.get("raw_layer_ordinal", -1)) != int(row.layer_ordinal)
                or str(ir_row.get("raw_layer_sha256", "")) != _row_hash(row)
                or str(ir_row.get("layer_kind", "")).casefold() != str(row.layer_kind).casefold()
                or str(ir_row.get("layer_name", "")).casefold() != str(row.layer_name).casefold()
                or float(ir_row.get("thickness_um", -1.0)) != float(row.thickness_um)
                or ir_row.get("conductivity_s_per_m") != row.conductivity_s_per_m
                or str(ir_row.get("material_name", "")) != str(row.material_name)):
            _fail("CONTACT_ADMISSIBILITY_INVALID: IR/raw stackup provenance differs")
    selected_points = [row for row in points if int(row.layer_ordinal) == int(dielectric.layer_ordinal) and float(row.frequency_hz) == float(frequency_hz)]
    if len(selected_points) != 1:
        _fail("CONTACT_ADMISSIBILITY_INVALID: exact source material point is absent or ambiguous")
    point = selected_points[0]
    ir_point = next((row for row in ir["dielectric_points"] if str(row.get("layer_name", "")).casefold() == str(dielectric.layer_name).casefold() and int(row.get("point_ordinal", -1)) == int(point.point_ordinal)), None)
    if (ir_point is None or int(ir_point.get("raw_dielectric_ordinal", -1)) != list(points).index(point)
            or str(ir_point.get("raw_dielectric_sha256", "")) != _row_hash(point)
            or float(ir_point.get("frequency_hz", -1.0)) != float(point.frequency_hz)
            or float(ir_point.get("epsilon_r", -1.0)) != float(point.epsilon_r)
            or float(ir_point.get("loss_tangent", -1.0)) != float(point.loss_tangent)):
        _fail("CONTACT_ADMISSIBILITY_INVALID: IR/raw dielectric provenance differs")
    try:
        artworks = tuple(SurfacePatchArtwork(str(row.get("layer", "")), str(row.get("artwork_net", "")), geometries[str(row.get("surface_id", "")).casefold()]) for row in bindings)
        conductor_rows = stackup[lo:hi + 1]
        conductors = {str(row.layer_name): SurfacePatchConductor(float(row.conductivity_s_per_m), float(row.thickness_um) * 1e-6, provenance=f"raw:{row.layer_ordinal}") for row in conductor_rows if str(row.layer_kind).casefold() == "conductor" and row.conductivity_s_per_m is not None}
        if len(conductors) != 2:
            _fail("CONTACT_ADMISSIBILITY_INVALID: conductor thickness or conductivity is unavailable")
        layer_order = tuple(str(row.layer_name) for row in conductor_rows if str(row.layer_kind).casefold() == "conductor")
        mesh = SurfacePatchMesh.uniform(layer_order=layer_order, artwork=artworks, cell_um=float(cell_um), max_cells=MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS)
        model = SurfacePatchDielectric(str(stackup[lo].layer_name), str(stackup[hi].layer_name), float(dielectric.thickness_um) * 1e-6, float(point.epsilon_r), float(point.loss_tangent), provenance=f"raw:{dielectric.layer_ordinal}:{point.point_ordinal}")
        operator = compile_surface_patch_plane(mesh, conductors=conductors, dielectrics=(model,))
        ports = tuple(item["port"] for item in metadata)
        condensation = operator.condense_finite_ports(float(frequency_hz), ports)
    except SourcePlanePatchError:
        raise
    except Exception as exc:
        _fail(f"CONTACT_CONDENSATION_INVALID: {exc}")
    if tuple(condensation.port_ids) != tuple(item["contact_id"] for item in metadata):
        _fail("CONTACT_CONDENSATION_INVALID: condensation contact order differs")
    diagnostics = {name: float(getattr(condensation, name)) for name in ("solve_residual", "compatible_current_residual", "gauge_residual", "reciprocity_relative", "passivity_min_eigenvalue_s", "passivity_tolerance_s", "terminal_condition")}
    contact_ids = list(condensation.port_ids)
    input_identity = {"source_sha256": ownership_manifest["source_sha256"], "ownership_logical_rows_sha256": str(ownership_manifest["logical_rows_sha256"]), "raw_manifest_sha256": ownership_manifest["raw_manifest_sha256"], "rail_id": canonical_rail, "frequency_hz": float(frequency_hz), "cell_um": float(cell_um), "contact_ids": contact_ids, "owner_kinds": [item["owner_kind"] for item in metadata], "surface_ids": [item["surface_id"] for item in metadata], "layers": [{"layer_ordinal": int(row.layer_ordinal), "layer_name": str(row.layer_name), "raw_layer_sha256": _row_hash(row), "thickness_um": float(row.thickness_um), "conductivity_s_per_m": row.conductivity_s_per_m, "material_name": str(row.material_name)} for row in conductor_rows] + [{"layer_ordinal": int(dielectric.layer_ordinal), "layer_name": str(dielectric.layer_name), "raw_layer_sha256": _row_hash(dielectric), "thickness_um": float(dielectric.thickness_um), "material_name": str(dielectric.material_name)}], "material": {"layer_ordinal": int(dielectric.layer_ordinal), "point_ordinal": int(point.point_ordinal), "raw_dielectric_sha256": _row_hash(point), "frequency_hz": float(point.frequency_hz), "epsilon_r": float(point.epsilon_r), "loss_tangent": float(point.loss_tangent)}}
    result: dict[str, Any] = {"schema_version": "source-plane-contact-condensation-v1", "shadow_only": True, "status": "complete", "rail_id": canonical_rail, "frequency_hz": float(frequency_hz), "cell_um": float(cell_um), "source_sha256": ownership_manifest["source_sha256"], "raw_manifest_sha256": ownership_manifest["raw_manifest_sha256"], "contact_ids": contact_ids, "owner_kinds": [item["owner_kind"] for item in metadata], "admittance_s": [[[float(value.real), float(value.imag)] for value in row] for row in condensation.terminal_admittance_s.tolist()], "terminal_constraint_matrix": [[float(value) for value in row] for row in condensation.terminal_constraint_matrix.tolist()], "diagnostics": diagnostics, "mesh_diagnostics": _object_mapping(mesh.diagnostics), "operator_diagnostics": _object_mapping(operator.diagnostics), "input_sha256": sha256(concrete_canonical_json_bytes(input_identity)).hexdigest()}
    return result


def audit_source_plane_patch_owner_off(
    ownership_manifest: Mapping[str, Any],
    ownership_attachments: Mapping[str, bytes],
    raw_manifest: Mapping[str, Any],
    patch_result: Mapping[str, Any],
    substrate: Any,
    *,
    rail_id: str,
) -> Mapping[str, Any]:
    """Identify, without mutation, old adjacent-gap edges eligible for owner-off."""
    try:
        rail_id = _text(rail_id, "rail_id")
        if not isinstance(patch_result, Mapping) or patch_result.get("schema_version") != "source-plane-contact-condensation-v1" or patch_result.get("shadow_only") is not True or patch_result.get("status") != "complete":
            _fail("OWNER_OFF_AUDIT_INVALID: patch result is not a complete shadow condensation")
        manifest_raw_sha = sha256(concrete_canonical_json_bytes(dict(raw_manifest))).hexdigest()
        provenance = getattr(substrate, "provenance", None)
        if not isinstance(provenance, Mapping) or manifest_raw_sha != str(ownership_manifest.get("raw_manifest_sha256")) or manifest_raw_sha != str(patch_result.get("raw_manifest_sha256")) or manifest_raw_sha != str(provenance.get("raw_spatial_v3_manifest_sha256")) or str(ownership_manifest.get("source_sha256")) != str(raw_manifest.get("source_sha256")) or str(patch_result.get("source_sha256")) != str(raw_manifest.get("source_sha256")) or str(provenance.get("source_sha256")) != str(raw_manifest.get("source_sha256")):
            _fail("OWNER_OFF_AUDIT_INVALID: source/raw identity differs")
        if not str(provenance.get("substrate_identity_sha256", "")).strip() or str(getattr(substrate, "substrate_identity_sha256", "")) != str(provenance["substrate_identity_sha256"]):
            _fail("OWNER_OFF_AUDIT_INVALID: substrate identity differs")
        if str(patch_result.get("rail_id", "")).casefold() != rail_id.casefold():
            _fail("OWNER_OFF_AUDIT_INVALID: patch rail differs")
        if not any(str(key).casefold() == rail_id.casefold() for key in getattr(substrate, "port_by_rail_key", {})):
            _fail("OWNER_OFF_AUDIT_INVALID: requested rail port is absent")
        total = [0]
        with load_source_plane_ownership_ir(ownership_manifest, ownership_attachments, expected_source_sha256=str(raw_manifest["source_sha256"]), expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]), expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]), expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]), expected_raw_manifest_sha256=str(ownership_manifest["raw_manifest_sha256"]), expected_raw_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]), expected_raw_logical_rows_sha256=str(raw_manifest["logical_rows_sha256"]), expected_raw_plane_sheet_sha256=str(raw_manifest["plane_sheet_payload_sha256"]), expected_app_version=str(ownership_manifest.get("app_version", ""))) as loaded:
            sections = ("surfaces", "primitives", "islands", "primitive_island_edges", "rail_bindings", "terminal_bindings", "contact_boundary", "retained_owner_refs", "plane_owner_scopes", "replacement_ledger", "replacement_ledger_members")
            ir = {section: _rows(loaded, section, MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, total) for section in sections}
        bindings = [row for row in ir["rail_bindings"] if str(row.get("rail_id", "")).casefold() == rail_id.casefold() and row.get("state") == "source_bound"]
        if len(bindings) != 2 or {str(row.get("role", "")).casefold() for row in bindings} != {"power", "ground"}:
            _fail("OWNER_OFF_AUDIT_INVALID: selected rail bindings are incomplete")
        contacts = sorted(ir["contact_boundary"], key=lambda row: (int(row.get("ordinal", 0)), str(row.get("contact_id", "")).casefold()))
        expected_contacts = [(str(row.get("contact_id")), str(row.get("owner_kind"))) for row in contacts]
        actual_contacts = list(zip(patch_result.get("contact_ids", ()), patch_result.get("owner_kinds", ()), strict=True))
        if actual_contacts != expected_contacts:
            _fail("OWNER_OFF_AUDIT_INVALID: patch contact order differs")
        islands = {str(row.get("island_id", "")).casefold(): row for row in ir["islands"]}
        surface_by_id = {str(row.get("surface_id", "")).casefold(): row for row in ir["surfaces"]}
        selected: dict[str, set[str]] = {}
        selected_reduced: dict[str, set[int]] = {}
        for role in ("power", "ground"):
            binding = next(row for row in bindings if str(row.get("role", "")).casefold() == role)
            anchor = islands.get(str(binding.get("island_id", "")).casefold())
            if anchor is None:
                _fail("OWNER_OFF_AUDIT_INVALID: selected island is absent")
            selected[role] = {str(row.get("island_id", "")).casefold() for row in ir["islands"] if str(row.get("surface_id", "")).casefold() == str(binding.get("surface_id", "")).casefold() and str(row.get("component_id", "")).casefold() == str(anchor.get("component_id", "")).casefold()}
            if not selected[role]:
                _fail("OWNER_OFF_AUDIT_INVALID: selected component island set is empty")
            try:
                selected_reduced[role] = {int(substrate.network.reduced_node_index(str(islands[item].get("island_id", "")))) for item in selected[role]}
            except Exception as exc:
                _fail(f"OWNER_OFF_AUDIT_INVALID: selected island mapping is absent: {exc}")
            if str(binding.get("island_id", "")).casefold() not in selected[role] or len(selected_reduced[role]) != 1:
                _fail("OWNER_OFF_AUDIT_INVALID: selected component is not one reduced node")
        if selected_reduced["power"] & selected_reduced["ground"]:
            _fail("OWNER_OFF_AUDIT_INVALID: selected power/ground reduced nodes overlap")
        port = next((value for key, value in getattr(substrate, "port_by_rail_key", {}).items() if str(key).casefold() == rail_id.casefold()), None)
        if port is None:
            _fail("OWNER_OFF_AUDIT_INVALID: production rail port is absent")
        if len({str(row[0]).casefold() for row in expected_contacts}) != len(expected_contacts):
            _fail("OWNER_OFF_AUDIT_INVALID: contact IDs are duplicated")
        selected_all = selected["power"] | selected["ground"]
        for contact in contacts:
            contact_island = str(contact.get("island_id", "")).casefold()
            if contact_island not in selected_all or str(contact.get("component_id", "")).casefold() != str(islands[contact_island].get("component_id", "")).casefold():
                _fail("OWNER_OFF_AUDIT_INVALID: contact is outside selected components")
        contacts_by_edge: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for contact in contacts:
            contact_key = (str(contact.get("island_id", "")).casefold(), str(contact.get("finite_edge_id", "")).casefold())
            contacts_by_edge.setdefault(contact_key, []).append(contact)
        terminal_rows_by_role: dict[str, list[Mapping[str, Any]]] = {}
        required_edge_keys: set[str] = set()
        for role in ("power", "ground"):
            terminal_rows = [row for row in ir["terminal_bindings"] if str(row.get("rail_id", "")).casefold() == rail_id.casefold() and str(row.get("role", "")).casefold() == role]
            if not terminal_rows:
                _fail("OWNER_OFF_AUDIT_INVALID: complete terminal binding is absent")
            terminal_rows_by_role[role] = terminal_rows
            for terminal in terminal_rows:
                if str(terminal.get("status", "")).casefold() != "complete" or str(terminal.get("island_id", "")).casefold() not in selected[role]:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal binding is outside selected component")
                finite_edge_id = str(terminal.get("finite_edge_id", "")).strip()
                if not finite_edge_id:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal finite identity is incomplete")
                required_edge_keys.add(finite_edge_id.casefold())
        links_by_edge: dict[str, list[Any]] = {}
        for link in getattr(substrate.network, "via_links", ()):
            edge_key = str(getattr(link, "link_id", "")).casefold()
            if edge_key in required_edge_keys:
                links_by_edge.setdefault(edge_key, []).append(link)
        terminal_pairs: list[tuple[str, Mapping[str, Any], list[str], Any]] = []
        terminal_identity: dict[str, dict[str, Any]] = {}
        for role in ("power", "ground"):
            terminal_rows = terminal_rows_by_role[role]
            role_rows: list[dict[str, Any]] = []
            for terminal in terminal_rows:
                island_id = str(terminal.get("island_id", "")).strip()
                finite_vertex_id = str(terminal.get("finite_vertex_id", "")).strip()
                finite_edge_id = str(terminal.get("finite_edge_id", "")).strip()
                via_owner_id = str(terminal.get("via_owner_id", "")).strip()
                if not island_id or not finite_vertex_id or not finite_edge_id or not via_owner_id:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal finite identity is incomplete")
                matches = contacts_by_edge.get((island_id.casefold(), finite_edge_id.casefold()), [])
                if len(matches) != 1:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal/contact edge join is ambiguous")
                contact = matches[0]
                contact_vertex_id = str(contact.get("finite_vertex_id", "")).strip()
                if not contact_vertex_id:
                    _fail("OWNER_OFF_AUDIT_INVALID: contact finite vertex is absent")
                try:
                    contact_reduced = int(substrate.network.reduced_node_index(contact_vertex_id))
                except Exception as exc:
                    _fail(f"OWNER_OFF_AUDIT_INVALID: contact finite vertex mapping is absent: {exc}")
                if contact_reduced != next(iter(selected_reduced[role])):
                    _fail("OWNER_OFF_AUDIT_INVALID: contact finite vertex is outside selected component")
                try:
                    owner_ids = json.loads(str(contact.get("owner_ids_json", "")))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    _fail(f"OWNER_OFF_AUDIT_INVALID: contact owner set is invalid: {exc}")
                if not isinstance(owner_ids, list) or not owner_ids or any(not isinstance(owner, str) or not owner.strip() for owner in owner_ids):
                    _fail("OWNER_OFF_AUDIT_INVALID: contact owner set is invalid")
                links = [link for link in links_by_edge.get(finite_edge_id.casefold(), ()) if str(getattr(link, "mode", "")) == "finite_parallel_rl"]
                if len(links) != 1:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal finite link is absent or ambiguous")
                link = links[0]
                endpoint_ids = {str(getattr(link, "first_node_id", "")).casefold(), str(getattr(link, "second_node_id", "")).casefold()}
                if finite_vertex_id.casefold() not in endpoint_ids or contact_vertex_id.casefold() not in endpoint_ids:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal finite link endpoints differ")
                link_owners = tuple(str(owner).casefold() for owner in getattr(link, "owner_ids", ()))
                contact_owners = tuple(str(owner).casefold() for owner in owner_ids)
                if link_owners != contact_owners:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal finite link owners differ")
                terminal_pairs.append((via_owner_id, contact, owner_ids, link))
                role_rows.append({"terminal_id": str(terminal.get("terminal_id", "")), "island_id": island_id, "finite_vertex_id": finite_vertex_id, "finite_edge_id": finite_edge_id, "via_owner_id": via_owner_id})
            anchor_by_key: dict[str, str] = {}
            for row in terminal_rows:
                value = str(row.get("finite_vertex_id", "")).strip()
                folded = value.casefold()
                if folded in anchor_by_key and anchor_by_key[folded] != value:
                    _fail("OWNER_OFF_AUDIT_INVALID: terminal anchor identity is ambiguous")
                anchor_by_key[folded] = value
            anchor_node_ids = tuple(sorted(anchor_by_key.values(), key=lambda item: (item.casefold(), item)))
            if not anchor_node_ids:
                _fail("OWNER_OFF_AUDIT_INVALID: terminal anchor nodes are absent")
            terminal_identity[role] = {"anchor_node_ids": list(anchor_node_ids), "terminal_rows": role_rows}
        expected_port_nodes: dict[str, str] = {}
        network_inventory = set(getattr(substrate.network, "surface_node_ids", ()))
        for role in ("power", "ground"):
            anchor_node_ids = tuple(str(item) for item in terminal_identity[role]["anchor_node_ids"])
            if any(item not in network_inventory for item in anchor_node_ids):
                _fail("OWNER_OFF_AUDIT_INVALID: terminal anchor node is absent from network")
            if len(anchor_node_ids) == 1:
                expected = anchor_node_ids[0]
            else:
                payload = {"schema": "finite-via-external-device-supernode-v1", "rail_id": rail_id.casefold(), "role": role, "anchor_node_ids": list(anchor_node_ids)}
                expected = f"spd-device-port-node:{sha256(concrete_canonical_json_bytes(payload)).hexdigest()[:24]}"
                try:
                    if not all(substrate.network.surfaces_share_ideal_node(expected, anchor) for anchor in anchor_node_ids):
                        _fail("OWNER_OFF_AUDIT_INVALID: terminal supernode topology differs")
                except Exception as exc:
                    _fail(f"OWNER_OFF_AUDIT_INVALID: terminal supernode mapping is absent: {exc}")
            if expected not in network_inventory:
                _fail("OWNER_OFF_AUDIT_INVALID: expected terminal port node is absent from network")
            expected_port_nodes[role] = expected
            terminal_identity[role]["expected_port_node_id"] = expected
        if str(getattr(port, "positive_node_id", "")) != expected_port_nodes["power"] or str(getattr(port, "negative_node_id", "")) != expected_port_nodes["ground"]:
            _fail("OWNER_OFF_AUDIT_INVALID: production rail port identity differs")
        edges_by_primitive: dict[str, list[Mapping[str, Any]]] = {}
        for edge in ir["primitive_island_edges"]:
            edges_by_primitive.setdefault(str(edge.get("primitive_id", "")).casefold(), []).append(edge)
        for binding in bindings:
            sid = str(binding.get("surface_id", "")).casefold(); role = str(binding.get("role", "")).casefold(); component = str(islands[str(binding.get("island_id", "")).casefold()].get("component_id", ""))
            if sid not in surface_by_id:
                _fail("OWNER_OFF_AUDIT_INVALID: selected surface is absent")
            for primitive in ir["primitives"]:
                if str(primitive.get("surface_id", "")).casefold() != sid or str(primitive.get("effect_status", "")).casefold() != "retained":
                    continue
                edges = edges_by_primitive.get(str(primitive.get("primitive_id", "")).casefold(), [])
                if not edges or any(str(edge.get("island_id", "")).casefold() not in selected[role] for edge in edges) or any(str(islands[str(edge.get("island_id", "")).casefold()].get("component_id", "")).casefold() != component.casefold() for edge in edges):
                    _fail("OWNER_OFF_AUDIT_INVALID: retained primitive component is ambiguous")
        contact_map = [{key: row.get(key) for key in ("contact_id", "owner_kind", "island_id", "component_id", "finite_vertex_id", "finite_edge_id", "plane_endpoint_node_id", "external_endpoint_node_id", "owner_ids_json")} for row in contacts]
        contact_map_sha = sha256(concrete_canonical_json_bytes(contact_map)).hexdigest()
        scopes = [row for row in ir["plane_owner_scopes"] if str(row.get("rail_id", "")).casefold() == rail_id.casefold() and row.get("state") == "declared_unconsumed" and int(row.get("owner_count", 0)) == 1]
        if len(scopes) != 2 or {str(row.get("role", "")).casefold() for row in scopes} != {"power", "ground"}:
            _fail("OWNER_OFF_AUDIT_INVALID: plane owner scopes are not prerequisite-only")
        if len(ir["replacement_ledger"]) != 1 or ir["replacement_ledger"][0].get("status") != "prerequisite_only":
            _fail("OWNER_OFF_AUDIT_INVALID: replacement ledger is not prerequisite-only")
        ledger_id = str(ir["replacement_ledger"][0].get("ledger_id", "")).casefold()
        members = ir["replacement_ledger_members"]
        if any(str(row.get("ledger_id", "")).casefold() != ledger_id for row in members):
            _fail("OWNER_OFF_AUDIT_INVALID: replacement ledger membership differs")
        replaced_owners = sorted({str(row.get("owner_id")) for row in members if row.get("action") == "replaced"}, key=str.casefold)
        retained_owners = sorted({str(row.get("owner_id")) for row in members if row.get("action") == "retained"}, key=str.casefold)
        scope_owners = sorted({str(row.get("compiler_owner_id")) for row in scopes}, key=str.casefold)
        retained_refs = sorted({str(row.get("owner_id")) for row in ir["retained_owner_refs"]}, key=str.casefold)
        if {item.casefold() for item in replaced_owners} != {item.casefold() for item in scope_owners} or {item.casefold() for item in retained_owners} != {item.casefold() for item in retained_refs} or {item.casefold() for item in replaced_owners} & {item.casefold() for item in retained_owners}:
            _fail("OWNER_OFF_AUDIT_INVALID: owner replacement sets overlap")
        retained_owner_keys = {item.casefold() for item in retained_owners}
        for via_owner_id, _contact, owner_ids, _link in terminal_pairs:
            if via_owner_id.casefold() not in {str(owner).casefold() for owner in owner_ids} or any(str(owner).casefold() not in retained_owner_keys for owner in owner_ids):
                _fail("OWNER_OFF_AUDIT_INVALID: terminal finite owner is not retained")
        incidents: list[dict[str, Any]] = []
        edge_keys: set[tuple[str, str, str, str]] = set()
        for wrapped in getattr(substrate.network, "partials", ()):
            partial = wrapped.partial
            names = tuple(str(name) for name in partial.net_names)
            matrix = partial.maxwell_capacitance_f
            if not issparse(matrix) or matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or matrix.shape[0] != len(names):
                _fail("OWNER_OFF_AUDIT_INVALID: old partial matrix is not finite symmetric")
            matrix = matrix.tocoo(copy=False)
            scale = max(float(np.max(np.abs(matrix.data), initial=0.0)), 1.0e-30)
            tolerance = max(1.0e-24, scale * 1.0e-10)
            difference = matrix.tocsr() - matrix.T.tocsr()
            if not np.all(np.isfinite(matrix.data)) or (difference.nnz and float(np.max(np.abs(difference.data))) > tolerance):
                _fail("OWNER_OFF_AUDIT_INVALID: old partial is not finite symmetric")
            row_sums = np.asarray(matrix.tocsr().sum(axis=1), dtype=np.float64).ravel()
            if float(np.max(np.abs(row_sums), initial=0.0)) > tolerance:
                _fail("OWNER_OFF_AUDIT_INVALID: old partial row-sum check failed")
            diagonal = np.asarray(matrix.tocsr().diagonal(), dtype=np.float64)
            if np.any(diagonal <= 0.0):
                _fail("OWNER_OFF_AUDIT_INVALID: old partial diagonal is not positive")
            offdiag_by_row = np.zeros(matrix.shape[0], dtype=np.float64)
            for row, column, value in zip(matrix.row, matrix.col, matrix.data, strict=True):
                if row != column:
                    if value > 0.0:
                        _fail("OWNER_OFF_AUDIT_INVALID: old partial has positive off-diagonal")
                    offdiag_by_row[int(row)] += float(value)
            if not np.allclose(diagonal, -offdiag_by_row, rtol=1.0e-12, atol=tolerance):
                _fail("OWNER_OFF_AUDIT_INVALID: old partial Laplacian reconstruction differs")
            for row, column, value in zip(matrix.row, matrix.col, matrix.data, strict=True):
                if row >= column or value >= 0.0:
                    continue
                endpoints = (names[int(row)], names[int(column)])
                endpoint_roles = [{role for role in ("power", "ground") if endpoint.casefold() in selected[role]} for endpoint in endpoints]
                incident = [bool(roles) for roles in endpoint_roles]
                if not any(incident):
                    continue
                if not all(incident) or {next(iter(roles)) for roles in endpoint_roles} != {"power", "ground"}:
                    _fail("OWNER_OFF_AUDIT_INVALID: incident edge is not selected P/G pair")
                endpoint_layers = [str(surface_by_id.get(str(islands.get(endpoint.casefold(), {}).get("surface_id", "")).casefold(), {}).get("layer", "")) for endpoint in endpoints]
                if {layer.casefold() for layer in endpoint_layers} != {str(partial.upper_layer).casefold(), str(partial.lower_layer).casefold()}:
                    _fail("OWNER_OFF_AUDIT_INVALID: partial endpoint layers differ")
                if endpoint_layers[0].casefold() == str(partial.upper_layer).casefold() and endpoint_layers[1].casefold() == str(partial.lower_layer).casefold():
                    upper_id, lower_id = endpoints
                elif endpoint_layers[1].casefold() == str(partial.upper_layer).casefold() and endpoint_layers[0].casefold() == str(partial.lower_layer).casefold():
                    upper_id, lower_id = endpoints[1], endpoints[0]
                else:
                    _fail("OWNER_OFF_AUDIT_INVALID: partial endpoint orientation is ambiguous")
                edge_key = (str(partial.upper_layer).casefold(), str(partial.lower_layer).casefold(), str(upper_id).casefold(), str(lower_id).casefold())
                if edge_key in edge_keys:
                    _fail("OWNER_OFF_AUDIT_INVALID: canonical old edge is duplicated")
                edge_keys.add(edge_key)
                payload = {"substrate_identity_sha256": str(substrate.substrate_identity_sha256), "upper_layer": str(partial.upper_layer), "lower_layer": str(partial.lower_layer), "upper_island_id": upper_id, "lower_island_id": lower_id, "capacitance_f_hex": float(-value).hex()}
                incidents.append({"fingerprint": sha256(concrete_canonical_json_bytes(payload)).hexdigest(), **payload})
        if not incidents:
            _fail("OWNER_OFF_AUDIT_INVALID: no selected incident old edges")
        fingerprints = sorted({str(item["fingerprint"]) for item in incidents})
        if len(fingerprints) != len(incidents):
            _fail("OWNER_OFF_AUDIT_INVALID: old edge fingerprints are duplicated")
        old_edge_set_sha = sha256(concrete_canonical_json_bytes(fingerprints)).hexdigest()
        input_sha = str(patch_result.get("input_sha256", ""))
        if len(input_sha) != 64 or input_sha != input_sha.casefold() or any(character not in "0123456789abcdef" for character in input_sha):
            _fail("OWNER_OFF_AUDIT_INVALID: patch input hash is absent")
        component_identity = {role: {"islands": sorted(str(islands[item].get("island_id", "")) for item in selected[role]), "reduced_index": next(iter(selected_reduced[role]))} for role in ("power", "ground")}
        scope_identity = [{"role": str(row.get("role")), "scope_id": str(row.get("scope_id")), "compiler_owner_id": str(row.get("compiler_owner_id"))} for row in sorted(scopes, key=lambda item: str(item.get("role", "")).casefold())]
        audit_identity = {"patch_input_sha256": input_sha, "substrate_identity_sha256": str(substrate.substrate_identity_sha256), "ownership_logical_rows_sha256": str(ownership_manifest.get("logical_rows_sha256")), "raw_spatial_v3_manifest_sha256": manifest_raw_sha, "rail_id": rail_id, "contact_map_sha256": contact_map_sha, "old_edge_set_sha256": old_edge_set_sha, "components": component_identity, "scopes": scope_identity, "replaced_owner_ids": replaced_owners, "terminal_anchor_identity": terminal_identity}
        result = {"schema_version": "source-plane-owner-off-audit-v1", "shadow_only": True, "status": "candidate_identified", "rail_id": rail_id, "source_sha256": raw_manifest["source_sha256"], "contact_map_sha256": contact_map_sha, "old_edge_set_sha256": old_edge_set_sha, "incident_edges": incidents, "component_identity": component_identity, "terminal_anchor_identity": terminal_identity, "candidate_scopes": scope_identity, "candidate_replaced_owner_ids": replaced_owners, "retained_owner_count": len(retained_owners), "retained_owner_ids_sha256": sha256(concrete_canonical_json_bytes([item.casefold() for item in retained_owners])).hexdigest(), "replacement_ready": False, "audit_sha256": sha256(concrete_canonical_json_bytes(audit_identity)).hexdigest()}
        return result
    except SourcePlanePatchError:
        raise
    except Exception as exc:
        _fail(f"OWNER_OFF_AUDIT_INVALID: {exc}")


def audit_source_plane_patch_contact_quotient_representability(
    ownership_manifest: Mapping[str, Any],
    ownership_attachments: Mapping[str, bytes],
    raw_manifest: Mapping[str, Any],
    patch_result: Mapping[str, Any],
    substrate: Any,
    *,
    rail_id: str,
) -> Mapping[str, Any]:
    """Audit whether the P1 contact space survives the production P/G quotient."""
    try:
        rail_id = _text(rail_id, "rail_id")
        p2_audit = audit_source_plane_patch_owner_off(
            ownership_manifest,
            ownership_attachments,
            raw_manifest,
            patch_result,
            substrate,
            rail_id=rail_id,
        )
        if (
            not isinstance(patch_result, Mapping)
            or patch_result.get("schema_version") != "source-plane-contact-condensation-v1"
            or patch_result.get("shadow_only") is not True
            or patch_result.get("status") != "complete"
        ):
            _fail("CONTACT_QUOTIENT_INVALID: P1 patch result is malformed")
        p1_input_sha = str(patch_result.get("input_sha256", ""))
        if len(p1_input_sha) != 64 or p1_input_sha != p1_input_sha.casefold() or any(character not in "0123456789abcdef" for character in p1_input_sha):
            _fail("CONTACT_QUOTIENT_INVALID: P1 input hash is malformed")
        if str(patch_result.get("source_sha256", "")) != str(raw_manifest.get("source_sha256", "")) or str(patch_result.get("raw_manifest_sha256", "")) != str(ownership_manifest.get("raw_manifest_sha256", "")):
            _fail("CONTACT_QUOTIENT_INVALID: P1 identity differs")
        p2_audit_sha = str(p2_audit.get("audit_sha256", ""))
        if len(p2_audit_sha) != 64 or p2_audit_sha != p2_audit_sha.casefold() or any(character not in "0123456789abcdef" for character in p2_audit_sha):
            _fail("CONTACT_QUOTIENT_INVALID: P2 audit identity is malformed")
        total = [0]
        with load_source_plane_ownership_ir(
            ownership_manifest,
            ownership_attachments,
            expected_source_sha256=str(raw_manifest["source_sha256"]),
            expected_project_binding_sha256=str(raw_manifest["project_binding_sha256"]),
            expected_certificate_evidence_sha256=str(raw_manifest["certificate_evidence_sha256"]),
            expected_compiled_topology_identity_sha256=str(raw_manifest["compiled_topology_identity_sha256"]),
            expected_raw_manifest_sha256=str(ownership_manifest["raw_manifest_sha256"]),
            expected_raw_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]),
            expected_raw_logical_rows_sha256=str(raw_manifest["logical_rows_sha256"]),
            expected_raw_plane_sheet_sha256=str(raw_manifest["plane_sheet_payload_sha256"]),
            expected_app_version=str(ownership_manifest.get("app_version", "")),
        ) as loaded:
            contacts = sorted(
                _rows(loaded, "contact_boundary", MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS, total),
                key=lambda row: (int(row.get("ordinal", 0)), str(row.get("contact_id", "")).casefold()),
            )
        p1_contact_ids = tuple(str(item) for item in patch_result.get("contact_ids", ()))
        p1_owner_kinds = tuple(str(item) for item in patch_result.get("owner_kinds", ()))
        expected_contacts = tuple((str(row.get("contact_id", "")), str(row.get("owner_kind", ""))) for row in contacts)
        if tuple(zip(p1_contact_ids, p1_owner_kinds, strict=True)) != expected_contacts:
            _fail("CONTACT_QUOTIENT_INVALID: P1 contact order differs")
        p2_contact_map = [
            {key: row.get(key) for key in ("contact_id", "owner_kind", "island_id", "component_id", "finite_vertex_id", "finite_edge_id", "plane_endpoint_node_id", "external_endpoint_node_id", "owner_ids_json")}
            for row in contacts
        ]
        contact_map_sha = sha256(concrete_canonical_json_bytes(p2_contact_map)).hexdigest()
        if str(p2_audit.get("contact_map_sha256", "")) != contact_map_sha:
            _fail("CONTACT_QUOTIENT_INVALID: P2 contact map differs")
        component_identity = p2_audit.get("component_identity")
        if not isinstance(component_identity, Mapping) or set(component_identity) != {"power", "ground"}:
            _fail("CONTACT_QUOTIENT_INVALID: P2 component identity is malformed")
        island_role: dict[str, str] = {}
        role_reduced: dict[str, int] = {}
        for role in ("power", "ground"):
            identity = component_identity.get(role)
            islands_for_role = identity.get("islands") if isinstance(identity, Mapping) else None
            reduced_for_role = identity.get("reduced_index") if isinstance(identity, Mapping) else None
            if not isinstance(islands_for_role, list) or not islands_for_role or not isinstance(reduced_for_role, int):
                _fail("CONTACT_QUOTIENT_INVALID: P2 component identity is malformed")
            role_reduced[role] = reduced_for_role
            for value in islands_for_role:
                folded = str(value).casefold()
                if not folded or folded in island_role:
                    _fail("CONTACT_QUOTIENT_INVALID: P2 component island identity is duplicated")
                island_role[folded] = role
        mapping: list[dict[str, Any]] = []
        B = np.zeros((2, len(contacts)), dtype=np.float64)
        for index, contact in enumerate(contacts):
            island_id = str(contact.get("island_id", "")).casefold()
            finite_vertex_id = str(contact.get("finite_vertex_id", "")).strip()
            role = island_role.get(island_id)
            if role is None:
                _fail("CONTACT_QUOTIENT_INVALID: contact does not map to exactly one quotient component")
            try:
                mapped_reduced = int(substrate.network.reduced_node_index(finite_vertex_id))
            except Exception as exc:
                _fail(f"CONTACT_QUOTIENT_INVALID: contact finite vertex mapping is absent: {exc}")
            if mapped_reduced != role_reduced[role]:
                _fail("CONTACT_QUOTIENT_INVALID: contact does not map to exactly one quotient component")
            role_index = 0 if role == "power" else 1
            B[role_index, index] = 1.0
            owner_ids_json = str(contact.get("owner_ids_json", ""))
            try:
                owner_ids = json.loads(owner_ids_json)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                _fail(f"CONTACT_QUOTIENT_INVALID: contact owner set is invalid: {exc}")
            if not isinstance(owner_ids, list) or not owner_ids or any(not isinstance(owner, str) or not owner.strip() for owner in owner_ids):
                _fail("CONTACT_QUOTIENT_INVALID: contact owner set is invalid")
            mapping.append({"contact_id": str(contact.get("contact_id", "")), "owner_kind": str(contact.get("owner_kind", "")), "island_id": str(contact.get("island_id", "")), "component_id": str(contact.get("component_id", "")), "finite_vertex_id": finite_vertex_id, "finite_edge_id": str(contact.get("finite_edge_id", "")), "owner_ids_json": owner_ids_json, "owner_ids": owner_ids, "reduced_index": role_reduced[role], "role": role})
        counts = B.sum(axis=1)
        if len(contacts) == 0 or np.any(counts <= 0.0):
            _fail("CONTACT_QUOTIENT_INVALID: quotient contact rows are empty")
        raw_admittance = patch_result.get("admittance_s")
        if not isinstance(raw_admittance, list) or len(raw_admittance) != len(contacts):
            _fail("CONTACT_QUOTIENT_INVALID: P1 admittance shape is malformed")
        Y = np.empty((len(contacts), len(contacts)), dtype=np.complex128)
        for row_index, row in enumerate(raw_admittance):
            if not isinstance(row, list) or len(row) != len(contacts):
                _fail("CONTACT_QUOTIENT_INVALID: P1 admittance shape is malformed")
            for column_index, value in enumerate(row):
                if not isinstance(value, list) or len(value) != 2:
                    _fail("CONTACT_QUOTIENT_INVALID: P1 admittance entry is malformed")
                real, imag = float(value[0]), float(value[1])
                if not math.isfinite(real) or not math.isfinite(imag):
                    _fail("CONTACT_QUOTIENT_INVALID: P1 admittance is non-finite")
                Y[row_index, column_index] = complex(real, imag)
        hermitian_conductance = (Y + Y.conj().T) * 0.5
        passivity_tolerance = max(float(np.linalg.norm(hermitian_conductance)) * 1.0e-10, float(np.linalg.norm(Y)) * 1.0e-14, 1.0e-24)
        try:
            passivity_minimum = float(np.min(np.linalg.eigvalsh(hermitian_conductance)))
        except Exception as exc:
            _fail(f"CONTACT_QUOTIENT_INVALID: P1 passivity eigensolve failed: {exc}")
        if not math.isfinite(passivity_minimum) or passivity_minimum < -passivity_tolerance:
            _fail("CONTACT_QUOTIENT_INVALID: P1 passivity minimum is below tolerance")
        raw_constraint = patch_result.get("terminal_constraint_matrix")
        if not isinstance(raw_constraint, list) or len(raw_constraint) != len(contacts) or not raw_constraint or any(not isinstance(row, list) or not row for row in raw_constraint):
            _fail("CONTACT_QUOTIENT_INVALID: P1 constraint shape is malformed")
        constraint = np.asarray(raw_constraint, dtype=np.float64)
        if constraint.ndim != 2 or constraint.shape[0] != len(contacts) or not np.all(np.isfinite(constraint)):
            _fail("CONTACT_QUOTIENT_INVALID: P1 constraint is non-finite")
        y_norm = float(np.linalg.norm(Y, 2))
        if not math.isfinite(y_norm) or y_norm <= 0.0:
            _fail("CONTACT_QUOTIENT_INVALID: P1 admittance norm is non-finite")
        reciprocity_norm = float(np.linalg.norm(Y - Y.T, 2) / max(y_norm, 1.0e-30))
        gauge_scale = max(y_norm * float(np.linalg.norm(constraint, 2)), 1.0e-30)
        gauge_residual = max(float(np.linalg.norm(Y @ constraint, 2)) / gauge_scale, float(np.linalg.norm(constraint.T @ Y, 2)) / gauge_scale)
        singular_values = np.linalg.svd(Y, compute_uv=False)
        rank_tolerance = max(float(singular_values[0]) * 1.0e-12 if singular_values.size else 0.0, np.finfo(np.float64).eps * max(len(contacts), 1) * 10.0)
        if singular_values.size and np.any(np.isclose(singular_values, rank_tolerance, rtol=0.1, atol=rank_tolerance * 0.1)):
            _fail("CONTACT_QUOTIENT_INVALID: P1 admittance rank is ambiguous")
        y_rank = int(np.count_nonzero(singular_values > rank_tolerance))
        constraint_singular_values = np.linalg.svd(constraint, compute_uv=False)
        constraint_tolerance = max(float(constraint_singular_values[0]) * 1.0e-12 if constraint_singular_values.size else 0.0, np.finfo(np.float64).eps * max(len(contacts), 1) * 10.0)
        if constraint_singular_values.size and np.any(np.isclose(constraint_singular_values, constraint_tolerance, rtol=0.1, atol=constraint_tolerance * 0.1)):
            _fail("CONTACT_QUOTIENT_INVALID: P1 constraint rank is ambiguous")
        constraint_rank = int(np.count_nonzero(constraint_singular_values > constraint_tolerance))
        if not math.isfinite(reciprocity_norm) or reciprocity_norm > 1.0e-12 or not math.isfinite(gauge_residual) or gauge_residual > 1.0e-12 or y_rank >= len(contacts) or constraint_rank <= 0 or y_rank != len(contacts) - constraint_rank:
            _fail("CONTACT_QUOTIENT_INVALID: P1 reciprocity or gauge/null validation failed")
        Q = B.T @ np.diag(1.0 / counts) @ B
        R = Y - Q @ Y @ Q
        residual_norm = float(np.linalg.norm(R, 2))
        threshold = max(y_norm * 1.0e-12, 1.0e-30)
        quotient_representable = math.isfinite(residual_norm) and residual_norm <= threshold
        projector_identity = {"B": B.tolist(), "contact_counts": [float(value) for value in counts.tolist()]}
        projector_sha = sha256(concrete_canonical_json_bytes(projector_identity)).hexdigest()
        p1_output_identity = {"contact_ids": list(p1_contact_ids), "owner_kinds": list(p1_owner_kinds), "admittance_s": [[[float(value.real), float(value.imag)] for value in row] for row in Y.tolist()], "terminal_constraint_matrix": [[float(value) for value in row] for row in constraint.tolist()]}
        p1_output_sha = sha256(concrete_canonical_json_bytes(p1_output_identity)).hexdigest()
        incident_fingerprints = sorted(str(row.get("fingerprint", "")) for row in p2_audit.get("incident_edges", ()) if isinstance(row, Mapping))
        raw_candidate_replaced_owner_ids = p2_audit.get("candidate_replaced_owner_ids")
        if not isinstance(raw_candidate_replaced_owner_ids, list) or not raw_candidate_replaced_owner_ids or any(not isinstance(value, str) or not value.strip() for value in raw_candidate_replaced_owner_ids) or len({value.casefold() for value in raw_candidate_replaced_owner_ids}) != len(raw_candidate_replaced_owner_ids) or raw_candidate_replaced_owner_ids != sorted(raw_candidate_replaced_owner_ids, key=str.casefold):
            _fail("CONTACT_QUOTIENT_INVALID: P2 candidate owner identity is malformed")
        candidate_replaced_owner_ids = list(raw_candidate_replaced_owner_ids)
        identity = {"p1_input_sha256": p1_input_sha, "p1_output_sha256": p1_output_sha, "p2_audit_sha256": p2_audit_sha, "p2_component_identity": component_identity, "p2_candidate_replaced_owner_ids": candidate_replaced_owner_ids, "p2_old_edge_set_sha256": str(p2_audit.get("old_edge_set_sha256", "")), "p2_incident_fingerprints": incident_fingerprints, "rail_id": rail_id, "contact_map_sha256": contact_map_sha, "projector_sha256": projector_sha, "mapping": mapping, "residual_norm_2": residual_norm, "admittance_norm_2": y_norm, "threshold": threshold}
        return {"schema_version": "source-plane-contact-quotient-representability-v1", "shadow_only": True, "status": "representable" if quotient_representable else "stopped", "code": None if quotient_representable else "CONTACT_INTERFACE_RANK_LOSS", "quotient_representable": quotient_representable, "replacement_ready": False, "rail_id": rail_id, "source_sha256": raw_manifest["source_sha256"], "p1_input_sha256": p1_input_sha, "p1_output_sha256": p1_output_sha, "p2_audit_sha256": p2_audit_sha, "p2_candidate_replaced_owner_ids": candidate_replaced_owner_ids, "p2_old_edge_set_sha256": str(p2_audit.get("old_edge_set_sha256", "")), "p2_component_identity": component_identity, "p2_incident_edges": list(p2_audit.get("incident_edges", ())), "contact_map_sha256": contact_map_sha, "projector_sha256": projector_sha, "contact_mapping": mapping, "B": B.tolist(), "contact_counts": [float(value) for value in counts.tolist()], "residual_norm_2": residual_norm, "admittance_norm_2": y_norm, "threshold": threshold, "reciprocity_residual": reciprocity_norm, "gauge_null_residual": gauge_residual, "admittance_rank": y_rank, "constraint_rank": constraint_rank, "input_identity_sha256": sha256(concrete_canonical_json_bytes(identity)).hexdigest()}
    except SourcePlanePatchError:
        raise
    except Exception as exc:
        _fail(f"CONTACT_QUOTIENT_INVALID: {exc}")


def audit_source_plane_patch_selected_base_cutset(
    ownership_manifest: Mapping[str, Any],
    ownership_attachments: Mapping[str, bytes],
    raw_manifest: Mapping[str, Any],
    patch_result: Mapping[str, Any],
    substrate: Any,
    *,
    rail_id: str,
) -> Mapping[str, Any]:
    """Audit the closed, read-only base-network cut set selected by P2/P3."""
    rail_id = _text(rail_id, "rail_id")
    p3 = audit_source_plane_patch_contact_quotient_representability(
        ownership_manifest, ownership_attachments, raw_manifest, patch_result, substrate, rail_id=rail_id
    )
    if not isinstance(p3, Mapping) or p3.get("schema_version") != "source-plane-contact-quotient-representability-v1" or p3.get("shadow_only") is not True:
        _fail("BASE_CUTSET_INVALID: P3 result is malformed")
    component_identity = p3.get("p2_component_identity")
    mapping = p3.get("contact_mapping")
    incident_edges = p3.get("p2_incident_edges")
    old_edge_set_sha = str(p3.get("p2_old_edge_set_sha256", ""))
    p3_input_sha = str(p3.get("input_identity_sha256", ""))
    p2_audit_sha = str(p3.get("p2_audit_sha256", ""))
    p1_input_sha = str(p3.get("p1_input_sha256", ""))
    p1_output_sha = str(p3.get("p1_output_sha256", ""))
    candidate_replaced_owner_ids = p3.get("p2_candidate_replaced_owner_ids")
    substrate_identity = str(getattr(substrate, "substrate_identity_sha256", ""))
    if not isinstance(component_identity, Mapping) or set(component_identity) != {"power", "ground"} or not isinstance(mapping, list) or not isinstance(incident_edges, list) or not isinstance(candidate_replaced_owner_ids, list) or len(old_edge_set_sha) != 64 or len(p3_input_sha) != 64 or len(p2_audit_sha) != 64 or len(p1_input_sha) != 64 or len(p1_output_sha) != 64 or len(substrate_identity) != 64 or any(value != value.casefold() or any(character not in "0123456789abcdef" for character in value) for value in (old_edge_set_sha, p3_input_sha, p2_audit_sha, p1_input_sha, p1_output_sha, substrate_identity)):
        _fail("BASE_CUTSET_INVALID: P2/P3 identity is incomplete")
    if p3.get("status") != "stopped" or p3.get("code") != "CONTACT_INTERFACE_RANK_LOSS" or p3.get("quotient_representable") is not False or p3.get("replacement_ready") is not False:
        _fail("BASE_CUTSET_IDENTITY_MISMATCH: P3 gate status differs")
    if str(p3.get("rail_id", "")).casefold() != rail_id.casefold() or str(p3.get("source_sha256", "")) != str(raw_manifest.get("source_sha256", "")):
        _fail("BASE_CUTSET_IDENTITY_MISMATCH: P3 source or rail differs")
    fingerprints = [str(row.get("fingerprint", "")) for row in incident_edges if isinstance(row, Mapping)]
    if len(fingerprints) != len(incident_edges) or len(set(fingerprints)) != len(fingerprints) or sha256(concrete_canonical_json_bytes(sorted(fingerprints))).hexdigest() != old_edge_set_sha:
        _fail("BASE_CUTSET_IDENTITY_MISMATCH: P2 old-edge fingerprint differs")
    common_identity = {"rail_id": rail_id, "source_sha256": str(raw_manifest["source_sha256"]), "substrate_identity_sha256": substrate_identity, "p3_input_identity_sha256": p3_input_sha, "p2_audit_sha256": p2_audit_sha, "p1_input_sha256": p1_input_sha, "p1_output_sha256": p1_output_sha, "p2_component_identity": component_identity, "p2_candidate_replaced_owner_ids": candidate_replaced_owner_ids, "contact_mapping": mapping, "old_edge_set_sha256": old_edge_set_sha, "incident_fingerprints": sorted(fingerprints)}
    def stopped(code: str, detail: str, evidence: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        payload = {**common_identity, "code": code, "detail": detail, "evidence": dict(evidence or {})}
        return {"schema_version": "source-plane-selected-base-cutset-v1", "shadow_only": True, "status": "stopped", "code": code, "detail": detail, "replacement_ready": False, "split_ready": False, "rail_id": rail_id, "source_sha256": raw_manifest["source_sha256"], "component_identity": component_identity, "contact_mapping": mapping, "old_edge_set_sha256": old_edge_set_sha, "p3_input_identity_sha256": p3_input_sha, "p2_audit_sha256": p2_audit_sha, "p1_input_sha256": p1_input_sha, "p1_output_sha256": p1_output_sha, "p2_candidate_replaced_owner_ids": candidate_replaced_owner_ids, "substrate_identity_sha256": substrate_identity, "base_cutset_sha256": sha256(concrete_canonical_json_bytes(payload)).hexdigest()}
    network = getattr(substrate, "network", None)
    surface_nodes = tuple(getattr(network, "surface_node_ids", ()))
    if not surface_nodes or len({str(item).casefold() for item in surface_nodes}) != len(surface_nodes):
        _fail("BASE_CUTSET_IDENTITY_MISMATCH: network surface inventory is malformed")
    by_surface = {str(item).casefold(): str(item) for item in surface_nodes}
    roles: dict[str, set[str]] = {"power": set(), "ground": set()}
    reduced: dict[str, int] = {}
    for role in ("power", "ground"):
        identity = component_identity.get(role)
        if not isinstance(identity, Mapping) or not isinstance(identity.get("islands"), list) or not identity.get("islands") or not isinstance(identity.get("reduced_index"), int):
            _fail("BASE_CUTSET_IDENTITY_MISMATCH: P2 component identity is malformed")
        reduced[role] = int(identity["reduced_index"])
        for value in identity["islands"]:
            key = str(value).casefold()
            if key not in by_surface:
                return stopped("BASE_CUTSET_IDENTITY_MISMATCH", "P2 island is absent from network", {"island": key})
            roles[role].add(key)
    seen_contacts: set[str] = set()
    seen_contact_owners: set[str] = set()
    expected_edges: dict[str, Mapping[str, Any]] = {}
    for row in mapping:
        if not isinstance(row, Mapping) or str(row.get("role", "")).casefold() not in roles:
            _fail("BASE_CUTSET_INVALID: P3 contact mapping is malformed")
        role = str(row["role"]).casefold(); vertex = str(row.get("finite_vertex_id", "")).strip(); edge = str(row.get("finite_edge_id", "")).strip(); contact_id = str(row.get("contact_id", "")).strip()
        if not vertex or not edge or not contact_id or contact_id.casefold() in seen_contacts or edge.casefold() in expected_edges:
            _fail("BASE_CUTSET_INVALID: P3 contact identity is duplicated or incomplete")
        if vertex.casefold() not in by_surface:
            _fail("BASE_CUTSET_IDENTITY_MISMATCH: P3 finite vertex is absent from network")
        owners = row.get("owner_ids")
        if not isinstance(owners, list) or not owners or any(not isinstance(owner, str) or not owner.strip() for owner in owners) or len({owner.casefold() for owner in owners}) != len(owners):
            _fail("BASE_CUTSET_INVALID: P3 owner identity is malformed")
        owner_keys = {owner.casefold() for owner in owners}
        if seen_contact_owners & owner_keys:
            _fail("BASE_CUTSET_INVALID: contact owner is not exact-once")
        seen_contact_owners.update(owner_keys)
        roles[role].add(vertex.casefold()); seen_contacts.add(contact_id.casefold()); expected_edges[edge.casefold()] = row
    if roles["power"] & roles["ground"] or any(not roles[role] for role in roles):
        _fail("BASE_CUTSET_IDENTITY_MISMATCH: P2/P3 role sets overlap")
    port = next((value for key, value in getattr(substrate, "port_by_rail_key", {}).items() if str(key).casefold() == rail_id.casefold()), None)
    if port is None:
        _fail("BASE_CUTSET_IDENTITY_MISMATCH: requested base-network port is absent")
    try:
        base_ports = tuple(getattr(network, "ports", ()))
    except Exception as exc:
        _fail(f"BASE_CUTSET_INVALID: network port inventory is malformed: {exc}")
    for base_port in base_ports:
        for endpoint_name in ("positive_node_id", "negative_node_id"):
            endpoint = str(getattr(base_port, endpoint_name, "")).strip()
            endpoint_key = endpoint.casefold()
            if not endpoint or endpoint_key not in by_surface:
                _fail("BASE_CUTSET_INVALID: network port endpoint is absent from surface inventory")
            try:
                endpoint_reduced = int(network.reduced_node_index(by_surface[endpoint_key]))
            except Exception as exc:
                _fail(f"BASE_CUTSET_INVALID: network port endpoint mapping is malformed: {exc}")
            if endpoint_reduced in reduced.values():
                return stopped("BASE_CUTSET_DIRECT_BASE_PORT_ATTACHMENT", "base port endpoint is directly in selected ideal class", {"port_id": str(getattr(base_port, "port_id", "")), "endpoint": endpoint, "endpoint_name": endpoint_name})
    for role in roles:
        try:
            preimage = {str(node).casefold() for node in surface_nodes if int(network.reduced_node_index(str(node))) == reduced[role]}
        except Exception as exc:
            _fail(f"BASE_CUTSET_INVALID: reduced-node mapping is malformed: {exc}")
        if preimage != roles[role]:
            payload = {"role": role, "expected": sorted(roles[role]), "preimage": sorted(preimage)}
            return stopped("BASE_CUTSET_IDEAL_MEMBER_UNACCOUNTED", "selected reduced-node preimage differs from P2/P3 class", payload)
    for row in incident_edges:
        if not isinstance(row, Mapping):
            _fail("BASE_CUTSET_INVALID: P2 incident edge is malformed")
        upper_role = next((role for role in roles if str(row.get("upper_island_id", "")).casefold() in roles[role]), None)
        lower_role = next((role for role in roles if str(row.get("lower_island_id", "")).casefold() in roles[role]), None)
        if {upper_role, lower_role} != {"power", "ground"}:
            return stopped("BASE_CUTSET_MAXWELL_ADJACENCY_ESCAPE", "P2 old Maxwell adjacency is outside the selected P/G classes", {"fingerprint": str(row.get("fingerprint", ""))})
    def role_of(node: str) -> str | None:
        key = node.casefold()
        return next((role for role in roles if key in roles[role]), None)
    seen_edges: set[str] = set()
    try:
        links = tuple(getattr(network, "via_links", ()))
    except Exception as exc:
        _fail(f"BASE_CUTSET_INVALID: network link inventory is malformed: {exc}")
    for link in links:
        first, second = str(getattr(link, "first_node_id", "")), str(getattr(link, "second_node_id", "")); mode = str(getattr(link, "mode", "")); rf, rs = role_of(first), role_of(second)
        if (rf or rs) and mode not in {"topology_only_ideal", "finite_parallel_rl"}:
            return stopped("BASE_CUTSET_CONTACT_MODE_MISMATCH", "selected link mode is unsupported", {"link_id": str(getattr(link, "link_id", ""))})
        if mode == "topology_only_ideal" and (rf or rs):
            if rf is None or rs is None or rf != rs:
                return stopped("BASE_CUTSET_IDEAL_ROLE_ESCAPE", "ideal link escapes the selected role class", {"link_id": str(getattr(link, "link_id", ""))})
            endpoint = next((value for value in (first, second) if value.casefold().startswith("spd-finite-via-vertex:")), None)
            if endpoint is not None:
                owners = tuple(str(owner) for owner in getattr(link, "owner_ids", ()))
                prefix = f"finite-vertex-surface:{endpoint}:".casefold()
                if len(owners) != 1 or not owners[0].casefold().startswith(prefix) or not owners[0][len(prefix):].isdigit():
                    return stopped("BASE_CUTSET_IDEAL_OWNER_MISMATCH", "finite-vertex ideal owner differs", {"link_id": str(getattr(link, "link_id", ""))})
            continue
        if mode != "finite_parallel_rl" or not (rf or rs):
            continue
        if rf and rs:
            return stopped("BASE_CUTSET_CONTACT_ENDPOINT_MISMATCH", "finite contact has no external endpoint", {"link_id": str(getattr(link, "link_id", ""))})
        edge_key = str(getattr(link, "link_id", "")).casefold(); expected = expected_edges.get(edge_key)
        if expected is None:
            return stopped("BASE_CUTSET_CONTACT_EDGE_MISSING_OR_EXTRA", "network finite edge is not present in P2 contact IR", {"edge_id": edge_key})
        selected = first if rf else second
        if selected.casefold() != str(expected.get("finite_vertex_id", "")).casefold():
            return stopped("BASE_CUTSET_CONTACT_ENDPOINT_MISMATCH", "finite edge selected endpoint differs", {"edge_id": edge_key})
        owners = tuple(str(owner).casefold() for owner in getattr(link, "owner_ids", ()))
        expected_owners = tuple(str(owner).casefold() for owner in expected.get("owner_ids", ()))
        if owners != expected_owners:
            return stopped("BASE_CUTSET_CONTACT_OWNER_MISMATCH", "finite edge owner set differs", {"edge_id": edge_key})
        seen_edges.add(edge_key)
    if seen_edges != set(expected_edges):
        return stopped("BASE_CUTSET_CONTACT_EDGE_MISSING_OR_EXTRA", "P2 contact finite edge is absent from network", {"expected": sorted(expected_edges), "seen": sorted(seen_edges)})
    return {"schema_version": "source-plane-selected-base-cutset-v1", "shadow_only": True, "status": "closed", "code": None, "rail_id": rail_id, "source_sha256": raw_manifest["source_sha256"], "component_identity": component_identity, "contact_mapping": mapping, "incident_edges": incident_edges, "old_edge_set_sha256": old_edge_set_sha, "p3_input_identity_sha256": p3_input_sha, "p2_audit_sha256": p2_audit_sha, "p1_input_sha256": p1_input_sha, "p1_output_sha256": p1_output_sha, "p2_candidate_replaced_owner_ids": candidate_replaced_owner_ids, "substrate_identity_sha256": substrate_identity, "split_ready": False, "replacement_ready": False, "base_cutset_sha256": sha256(concrete_canonical_json_bytes(common_identity)).hexdigest()}


def plan_source_plane_patch_shadow_contact_rewire(
    ownership_manifest: Mapping[str, Any],
    ownership_attachments: Mapping[str, bytes],
    raw_manifest: Mapping[str, Any],
    patch_result: Mapping[str, Any],
    substrate: Any,
    *,
    rail_id: str,
) -> Mapping[str, Any]:
    """Plan (without applying) a contact-interface shadow rewire."""
    rail_id = _text(rail_id, "rail_id")
    p4 = audit_source_plane_patch_selected_base_cutset(ownership_manifest, ownership_attachments, raw_manifest, patch_result, substrate, rail_id=rail_id)
    if not isinstance(p4, Mapping) or p4.get("schema_version") != "source-plane-selected-base-cutset-v1" or p4.get("status") != "closed" or p4.get("code") is not None or p4.get("shadow_only") is not True or p4.get("split_ready") is not False or p4.get("replacement_ready") is not False:
        _fail("SHADOW_REWIRE_INVALID: P4 result is not closed")
    mapping = p4.get("contact_mapping"); component_identity = p4.get("component_identity"); incident_edges = p4.get("incident_edges")
    source_sha = str(p4.get("source_sha256", ""))
    identities = tuple(str(p4.get(key, "")) for key in ("base_cutset_sha256", "p3_input_identity_sha256", "p2_audit_sha256", "p1_input_sha256", "p1_output_sha256", "old_edge_set_sha256", "substrate_identity_sha256"))
    if len(source_sha) != 64 or source_sha != source_sha.casefold() or any(character not in "0123456789abcdef" for character in source_sha) or source_sha != str(raw_manifest.get("source_sha256", "")) or not isinstance(mapping, list) or not mapping or not isinstance(component_identity, Mapping) or set(component_identity) != {"power", "ground"} or not isinstance(incident_edges, list) or any(len(value) != 64 or value != value.casefold() or any(character not in "0123456789abcdef" for character in value) for value in identities):
        _fail("SHADOW_REWIRE_INVALID: P4 identity is malformed")
    contact_ids = patch_result.get("contact_ids") if isinstance(patch_result, Mapping) else None
    if not isinstance(contact_ids, list) or not contact_ids or tuple(str(row.get("contact_id", "")) for row in mapping) != tuple(str(value) for value in contact_ids):
        _fail("SHADOW_REWIRE_INVALID: P4 contact order differs from P1")
    try:
        frequency_hz, cell_um = float(patch_result.get("frequency_hz")), float(patch_result.get("cell_um"))
    except (TypeError, ValueError) as exc:
        _fail(f"SHADOW_REWIRE_INVALID: P1 frequency/cell is malformed: {exc}")
    if not math.isfinite(frequency_hz) or frequency_hz <= 0.0 or not math.isfinite(cell_um) or cell_um <= 0.0:
        _fail("SHADOW_REWIRE_INVALID: P1 frequency/cell is non-positive")
    diagnostics = patch_result.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        _fail("SHADOW_REWIRE_INVALID: P1 diagnostics are non-finite")
    try:
        diagnostic_values = tuple(float(value) for value in diagnostics.values())
    except (TypeError, ValueError) as exc:
        _fail(f"SHADOW_REWIRE_INVALID: P1 diagnostics are malformed: {exc}")
    if any(not math.isfinite(value) for value in diagnostic_values):
        _fail("SHADOW_REWIRE_INVALID: P1 diagnostics are non-finite")
    try:
        passivity_min = float(diagnostics["passivity_min_eigenvalue_s"]); passivity_tolerance = float(diagnostics["passivity_tolerance_s"])
    except (KeyError, TypeError, ValueError) as exc:
        _fail(f"SHADOW_REWIRE_INVALID: P1 passivity diagnostics are malformed: {exc}")
    if not math.isfinite(passivity_min) or not math.isfinite(passivity_tolerance) or passivity_tolerance < 0.0:
        _fail("SHADOW_REWIRE_INVALID: P1 passivity diagnostics are malformed")
    common = {"rail_id": rail_id, "source_sha256": str(raw_manifest["source_sha256"]), "base_cutset_sha256": identities[0], "p3_input_identity_sha256": identities[1], "p2_audit_sha256": identities[2], "p1_input_sha256": identities[3], "p1_output_sha256": identities[4], "old_edge_set_sha256": identities[5], "substrate_identity_sha256": identities[6]}
    def stopped(code: str, detail: str, evidence: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        payload = {**common, "code": code, "detail": detail, "evidence": dict(evidence or {})}
        return {"schema_version": "source-plane-shadow-contact-rewire-plan-v1", "status": "stopped", "code": code, "detail": detail, "shadow_only": True, "production_ready": False, "replacement_ready": False, "rail_id": rail_id, "source_sha256": raw_manifest["source_sha256"], "base_cutset_sha256": identities[0], "p3_input_identity_sha256": identities[1], "p2_audit_sha256": identities[2], "p1_input_sha256": identities[3], "p1_output_sha256": identities[4], "old_edge_set_sha256": identities[5], "substrate_identity_sha256": identities[6], "shadow_split_sha256": sha256(concrete_canonical_json_bytes(payload)).hexdigest()}
    network = getattr(substrate, "network", None)
    surface_nodes = tuple(getattr(network, "surface_node_ids", ()))
    old_class = {str(value).casefold() for role in ("power", "ground") for value in component_identity[role].get("islands", ())}
    old_class.update(str(row.get("finite_vertex_id", "")).casefold() for row in mapping)
    surface_keys = {str(value).casefold() for value in surface_nodes}
    if not old_class or len(old_class) != len(surface_keys & old_class):
        _fail("SHADOW_REWIRE_INVALID: P4 old class is malformed")
    generated: set[str] = set(); interface_by_contact: dict[str, str] = {}; ordered_interfaces: list[dict[str, str]] = []
    for ordinal, row in enumerate(mapping):
        contact_id = str(row.get("contact_id", "")).strip(); edge_id = str(row.get("finite_edge_id", "")).strip()
        digest = sha256(concrete_canonical_json_bytes({"base_cutset_sha256": identities[0], "ordinal": ordinal, "contact_id": contact_id, "finite_edge_id": edge_id})).hexdigest()
        interface_id = f"spd-source-plane-interface:{digest[:32]}"
        key = interface_id.casefold()
        if key in generated or key in surface_keys:
            return stopped("SHADOW_REWIRE_INTERFACE_COLLISION", "generated interface node collides with network inventory", {"contact_id": contact_id, "interface_node_id": interface_id})
        generated.add(key); interface_by_contact[contact_id.casefold()] = interface_id; ordered_interfaces.append({"contact_id": contact_id, "interface_node_id": interface_id})
    edge_keys_in_order = [str(row.get("finite_edge_id", "")).casefold() for row in mapping]
    if len(edge_keys_in_order) != len(set(edge_keys_in_order)):
        _fail("SHADOW_REWIRE_INVALID: P4 finite edge identity is duplicated")
    expected = {key: row for key, row in zip(edge_keys_in_order, mapping, strict=True)}; seen_edges: set[str] = set(); seen_owners: set[str] = set(); rewires_by_edge: dict[str, dict[str, Any]] = {}
    try:
        links = tuple(getattr(network, "via_links", ()))
    except Exception as exc:
        _fail(f"SHADOW_REWIRE_INVALID: network link inventory is malformed: {exc}")
    for link in links:
        first, second = str(getattr(link, "first_node_id", "")), str(getattr(link, "second_node_id", "")); endpoints = (first, second); inside = [item for item in endpoints if item.casefold() in old_class]
        if len(inside) != 1:
            continue
        edge_key = str(getattr(link, "link_id", "")).casefold(); row = expected.get(edge_key)
        if row is None:
            return stopped("SHADOW_REWIRE_CONTACT_EDGE_MISSING_OR_EXTRA", "crossing finite link is not an expected contact", {"link_id": edge_key})
        if str(getattr(link, "mode", "")) != "finite_parallel_rl":
            return stopped("SHADOW_REWIRE_CONTACT_MODE_MISMATCH", "contact link mode differs", {"link_id": edge_key})
        selected, external = inside[0], second if inside[0].casefold() == first.casefold() else first
        if selected.casefold() != str(row.get("finite_vertex_id", "")).casefold() or external.casefold() in old_class:
            return stopped("SHADOW_REWIRE_CONTACT_ENDPOINT_MISMATCH", "contact link endpoint differs", {"link_id": edge_key})
        owners = tuple(str(owner) for owner in getattr(link, "owner_ids", ())); owner_keys = {owner.casefold() for owner in owners}
        expected_owners = tuple(str(owner) for owner in row.get("owner_ids", ()))
        if owners != expected_owners or len(owner_keys) != len(owners) or seen_owners & owner_keys:
            return stopped("SHADOW_REWIRE_CONTACT_OWNER_MISMATCH", "contact link owners differ or repeat", {"link_id": edge_key})
        try:
            count = int(getattr(link, "count")); resistance = float(getattr(link, "resistance_ohm_per_via")); inductance = float(getattr(link, "inductance_h_per_via"))
        except (TypeError, ValueError) as exc:
            _fail(f"SHADOW_REWIRE_INVALID: contact R/L metadata is malformed: {exc}")
        if count < 1 or not math.isfinite(resistance) or not math.isfinite(inductance) or resistance < 0.0 or inductance < 0.0 or (resistance == 0.0 and inductance == 0.0):
            return stopped("SHADOW_REWIRE_CONTACT_RL_INVALID", "contact R/L metadata is not finite/passive", {"link_id": edge_key})
        rewires_by_edge[edge_key] = {"contact_id": str(row.get("contact_id", "")), "old_finite_edge_id": str(getattr(link, "link_id", "")), "new_interface_node_id": interface_by_contact[str(row.get("contact_id", "")).casefold()], "selected_old_endpoint": selected, "external_endpoint": external, "count": count, "mode": "finite_parallel_rl", "owner_ids": list(owners), "resistance_ohm_per_via_hex": resistance.hex(), "inductance_h_per_via_hex": inductance.hex()}; seen_edges.add(edge_key); seen_owners.update(owner_keys)
    if seen_edges != set(expected) or len(rewires_by_edge) != len(mapping):
        return stopped("SHADOW_REWIRE_CONTACT_EDGE_MISSING_OR_EXTRA", "expected contact crossing set differs", {"expected": sorted(expected), "seen": sorted(seen_edges)})
    rewires = [rewires_by_edge[key] for key in edge_keys_in_order]
    disabled = sorted(str(row.get("fingerprint", "")) for row in incident_edges)
    if len(disabled) != len(set(disabled)) or any(len(value) != 64 for value in disabled) or sha256(concrete_canonical_json_bytes(disabled)).hexdigest() != identities[5]:
        _fail("SHADOW_REWIRE_INVALID: P4 disabled-edge set is malformed")
    candidate = p4.get("p2_candidate_replaced_owner_ids"); retained = sorted({str(owner) for row in mapping for owner in row.get("owner_ids", ())}, key=str.casefold)
    if not isinstance(candidate, list) or not candidate or any(not isinstance(value, str) or not value.strip() for value in candidate) or len({str(value).casefold() for value in candidate}) != len(candidate) or candidate != sorted(candidate, key=str.casefold) or {str(value).casefold() for value in candidate} & {value.casefold() for value in retained}:
        _fail("SHADOW_REWIRE_INVALID: planned owner sets overlap or are malformed")
    passivity_identity = {"passivity_min_eigenvalue_s": passivity_min, "passivity_tolerance_s": passivity_tolerance, "diagnostics": dict(diagnostics)}; passivity_sha = sha256(concrete_canonical_json_bytes(passivity_identity)).hexdigest()
    raw_admittance, raw_constraint = patch_result.get("admittance_s"), patch_result.get("terminal_constraint_matrix")
    if not isinstance(raw_admittance, list) or len(raw_admittance) != len(mapping) or any(not isinstance(row, list) or len(row) != len(mapping) for row in raw_admittance) or not isinstance(raw_constraint, list) or len(raw_constraint) != len(mapping) or any(not isinstance(row, list) or not row for row in raw_constraint):
        _fail("SHADOW_REWIRE_INVALID: P1 matrix shape differs")
    if passivity_min < -passivity_tolerance:
        return stopped("SHADOW_REWIRE_PASSIVITY_INVALID", "P1 passivity diagnostic is below tolerance", {"passivity_min_eigenvalue_s": passivity_min, "passivity_tolerance_s": passivity_tolerance})
    planned_owner_ids = list(candidate)
    stamp = {"contact_interfaces": ordered_interfaces, "contact_interface_node_ids": [item["interface_node_id"] for item in ordered_interfaces], "p1_input_sha256": identities[3], "p1_output_sha256": identities[4], "frequency_hz": frequency_hz, "cell_um": cell_um, "admittance_shape": [len(raw_admittance), len(raw_admittance[0])], "constraint_shape": [len(raw_constraint), len(raw_constraint[0])], "passivity_diagnostics_sha256": passivity_sha, "owner_ids": planned_owner_ids}
    identity = {**common, "contact_ids": [row["contact_id"] for row in rewires], "rewires": rewires, "disabled_old_edge_fingerprints": disabled, "planned_block_owner_ids": planned_owner_ids, "retained_contact_finite_owner_ids": retained, "stamp": stamp}
    return {"schema_version": "source-plane-shadow-contact-rewire-plan-v1", "status": "planned", "code": None, "shadow_only": True, "production_ready": False, "replacement_ready": False, "rail_id": rail_id, "source_sha256": raw_manifest["source_sha256"], "base_cutset_sha256": identities[0], "p3_input_identity_sha256": identities[1], "p2_audit_sha256": identities[2], "p1_input_sha256": identities[3], "p1_output_sha256": identities[4], "old_edge_set_sha256": identities[5], "substrate_identity_sha256": identities[6], "rewire_rows": rewires, "disabled_old_edge_fingerprints": disabled, "planned_block_owner_ids": planned_owner_ids, "retained_contact_finite_owner_ids": retained, "old_selected_external_degree_after_plan": 0, "planned_stamp": stamp, "shadow_split_sha256": sha256(concrete_canonical_json_bytes(identity)).hexdigest()}


def audit_source_plane_patch_shadow_rewire_commutation(
    rewire_plan: Mapping[str, Any],
    substrate: Any,
    binding: Any,
    *,
    rail_id: str,
) -> Mapping[str, Any]:
    """Audit P5's contact boundary against one compiled scenario binding.

    The audit is deliberately structural.  It never applies the planned
    interface nodes, mutates a network, or invokes a solver.
    """
    rail_id = _text(rail_id, "rail_id")
    plan = rewire_plan
    if not isinstance(plan, Mapping):
        _fail("SCENARIO_REWIRE_INVALID: P5 result is not a mapping")

    def digest(value: Any, label: str) -> str:
        if not isinstance(value, str):
            _fail(f"SCENARIO_REWIRE_INVALID: {label} is not SHA-256")
        value = value.strip()
        if len(value) != 64 or value != value.casefold() or any(
            character not in "0123456789abcdef" for character in value
        ):
            _fail(f"SCENARIO_REWIRE_INVALID: {label} is not SHA-256")
        return value

    if (
        plan.get("schema_version") != "source-plane-shadow-contact-rewire-plan-v1"
        or plan.get("status") != "planned"
        or plan.get("shadow_only") is not True
        or plan.get("production_ready") is not False
        or plan.get("replacement_ready") is not False
    ):
        _fail("SCENARIO_REWIRE_INVALID: P5 plan is not shadow/planned")
    source_sha = digest(plan.get("source_sha256"), "P5 source")
    substrate_provenance = getattr(substrate, "provenance", None)
    if not isinstance(substrate_provenance, Mapping):
        _fail("SCENARIO_REWIRE_INVALID: substrate provenance is absent")
    source_identity = digest(substrate_provenance.get("source_sha256"), "substrate source")
    if source_sha != source_identity:
        _fail("SCENARIO_REWIRE_INVALID: P5 source differs from substrate source")
    p5_keys = (
        "base_cutset_sha256",
        "p3_input_identity_sha256",
        "p2_audit_sha256",
        "p1_input_sha256",
        "p1_output_sha256",
        "old_edge_set_sha256",
        "substrate_identity_sha256",
        "shadow_split_sha256",
    )
    p5_ids = {key: digest(plan.get(key), f"P5 {key}") for key in p5_keys}
    if str(plan.get("rail_id", "")).casefold() != rail_id.casefold():
        _fail("SCENARIO_REWIRE_INVALID: P5 rail differs")

    stamp = plan.get("planned_stamp")
    rows = plan.get("rewire_rows")
    disabled = plan.get("disabled_old_edge_fingerprints")
    planned_owners = plan.get("planned_block_owner_ids")
    retained_owners = plan.get("retained_contact_finite_owner_ids")
    if (
        not isinstance(stamp, Mapping)
        or not isinstance(rows, list)
        or not rows
        or any(not isinstance(row, Mapping) for row in rows)
        or not isinstance(disabled, list)
        or not isinstance(planned_owners, list)
        or not isinstance(retained_owners, list)
    ):
        _fail("SCENARIO_REWIRE_INVALID: P5 disclosures are incomplete")
    if any(digest(value, "P5 disabled edge") != value for value in disabled):
        _fail("SCENARIO_REWIRE_INVALID: P5 disabled edge set is malformed")
    if any(not isinstance(value, str) or not value.strip() for value in (*planned_owners, *retained_owners)):
        _fail("SCENARIO_REWIRE_INVALID: P5 owner disclosure is malformed")
    if len({value.casefold() for value in planned_owners}) != len(planned_owners):
        _fail("SCENARIO_REWIRE_INVALID: P5 planned owners are duplicated")
    if len({value.casefold() for value in retained_owners}) != len(retained_owners):
        _fail("SCENARIO_REWIRE_INVALID: P5 retained owners are duplicated")
    if {value.casefold() for value in planned_owners} & {value.casefold() for value in retained_owners}:
        _fail("SCENARIO_REWIRE_INVALID: P5 owner sets overlap")
    interfaces = stamp.get("contact_interfaces")
    if not isinstance(interfaces, list) or len(interfaces) != len(rows):
        _fail("SCENARIO_REWIRE_INVALID: P5 interface disclosure is malformed")
    interface_ids = [str(row.get("interface_node_id", "")) for row in interfaces if isinstance(row, Mapping)]
    if len(interface_ids) != len(rows) or any(not value.strip() for value in interface_ids) or len({value.casefold() for value in interface_ids}) != len(interface_ids):
        _fail("SCENARIO_REWIRE_INVALID: P5 interface IDs are malformed")

    # Reconstruct P5's identity envelope so the scenario result cannot detach
    # from a different plan payload while retaining the same displayed SHA.
    common = {
        "rail_id": rail_id,
        "source_sha256": source_sha,
        "base_cutset_sha256": p5_ids["base_cutset_sha256"],
        "p3_input_identity_sha256": p5_ids["p3_input_identity_sha256"],
        "p2_audit_sha256": p5_ids["p2_audit_sha256"],
        "p1_input_sha256": p5_ids["p1_input_sha256"],
        "p1_output_sha256": p5_ids["p1_output_sha256"],
        "old_edge_set_sha256": p5_ids["old_edge_set_sha256"],
        "substrate_identity_sha256": p5_ids["substrate_identity_sha256"],
    }
    plan_identity = {
        **common,
        "contact_ids": [row.get("contact_id") for row in rows],
        "rewires": rows,
        "disabled_old_edge_fingerprints": disabled,
        "planned_block_owner_ids": planned_owners,
        "retained_contact_finite_owner_ids": retained_owners,
        "stamp": dict(stamp),
    }
    if sha256(concrete_canonical_json_bytes(plan_identity)).hexdigest() != p5_ids["shadow_split_sha256"]:
        _fail("SCENARIO_REWIRE_INVALID: P5 shadow split identity differs")

    base_network = getattr(substrate, "network", None)
    scenario_network = getattr(binding, "network", None)
    termination = getattr(binding, "termination_manifest", None)
    if (
        not isinstance(binding, LayerwiseScenarioNetworkBinding)
        or base_network is None
        or scenario_network is None
        or termination is None
    ):
        _fail("SCENARIO_REWIRE_INVALID: substrate/binding network is absent")
    base_id = digest(getattr(substrate, "substrate_identity_sha256", ""), "base substrate")
    binding_base = digest(getattr(binding, "base_substrate_identity_sha256", ""), "binding base")
    scenario_id = digest(getattr(binding, "scenario_identity_sha256", ""), "scenario identity")
    plan_id = digest(getattr(binding, "plan_sha256", ""), "scenario plan")
    termination_id = digest(getattr(termination, "manifest_sha256", ""), "termination manifest")
    provenance = getattr(binding, "provenance", None)
    if not isinstance(provenance, Mapping):
        _fail("SCENARIO_REWIRE_INVALID: binding provenance is absent")
    surface_manifest = digest(provenance.get("scenario_surface_node_manifest_sha256"), "scenario surface manifest")
    link_manifest = digest(provenance.get("scenario_link_manifest_sha256"), "scenario link manifest")
    provenance_scenario_id = digest(provenance.get("scenario_identity_sha256"), "provenance scenario identity")
    provenance_plan_id = digest(provenance.get("scenario_plan_sha256"), "provenance scenario plan")
    common.update({
        "shadow_split_sha256": p5_ids["shadow_split_sha256"],
        "scenario_identity_sha256": scenario_id,
        "scenario_plan_sha256": plan_id,
        "scenario_surface_node_manifest_sha256": surface_manifest,
        "scenario_link_manifest_sha256": link_manifest,
        "termination_manifest_sha256": termination_id,
    })

    def stopped(code: str, detail: str, evidence: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = {**common, "code": code, "detail": detail, "evidence": dict(evidence)}
        return {
            "schema_version": "source-plane-shadow-rewire-commutation-v1",
            "status": "stopped",
            "code": code,
            "detail": detail,
            "shadow_only": True,
            "production_ready": False,
            "replacement_ready": False,
            **common,
            "evidence": dict(evidence),
            "scenario_commutation_sha256": sha256(concrete_canonical_json_bytes(payload)).hexdigest(),
        }

    if binding_base != base_id or p5_ids["substrate_identity_sha256"] != base_id:
        return stopped("SCENARIO_REWIRE_IDENTITY_MISMATCH", "base substrate identity differs", {"base_substrate_identity_sha256": base_id, "binding_base_substrate_identity_sha256": binding_base, "p5_substrate_identity_sha256": p5_ids["substrate_identity_sha256"]})
    if provenance_scenario_id != scenario_id or provenance_plan_id != plan_id:
        return stopped("SCENARIO_REWIRE_IDENTITY_MISMATCH", "scenario provenance identity differs", {"scenario_identity_sha256": scenario_id, "provenance_scenario_identity_sha256": provenance_scenario_id, "scenario_plan_sha256": plan_id, "provenance_scenario_plan_sha256": provenance_plan_id})

    def sequence_sha256(values: Any) -> str:
        digest = sha256()
        digest.update(b"[")
        for ordinal, value in enumerate(values):
            if ordinal:
                digest.update(b",")
            encoded = concrete_canonical_json_bytes(value)
            digest.update(encoded[:-1] if encoded.endswith(b"\n") else encoded)
        digest.update(b"]\n")
        return digest.hexdigest()

    expected_surface_manifest = sequence_sha256(str(value).casefold() for value in getattr(scenario_network, "surface_node_ids", ()))
    def network_ports(network: Any) -> tuple[tuple[str, str, str], ...]:
        values: list[tuple[str, str, str]] = []
        for port in tuple(getattr(network, "ports", ())):
            values.append((str(port.port_id), str(port.positive_node_id), str(port.negative_node_id)))
        if len({value[0].casefold() for value in values}) != len(values):
            _fail("SCENARIO_REWIRE_INVALID: port IDs are duplicated")
        return tuple(sorted(values, key=lambda value: (value[0].casefold(), value[0])))

    base_ports = network_ports(base_network)
    scenario_ports = network_ports(scenario_network)
    if scenario_ports != base_ports:
        return stopped("SCENARIO_REWIRE_IDENTITY_MISMATCH", "scenario port inventory differs", {"base_ports": base_ports, "scenario_ports": scenario_ports})

    selected_display = {str(row.get("selected_old_endpoint", "")) for row in rows}
    selected = {value.casefold() for value in selected_display}
    if not selected or any(not value.strip() for value in selected_display):
        _fail("SCENARIO_REWIRE_INVALID: selected old endpoint disclosure is malformed")
    try:
        base_selected_reduced = {
            value.casefold(): int(base_network.reduced_node_index(value))
            for value in selected_display
        }
        selected_reduced = set(base_selected_reduced.values())
        base_preimages: dict[int, set[str]] = {}
        for value in getattr(base_network, "surface_node_ids", ()):
            reduced = int(base_network.reduced_node_index(value))
            if reduced in selected_reduced:
                base_preimages.setdefault(reduced, set()).add(str(value).casefold())
        old_class = set().union(*base_preimages.values())
    except Exception as exc:
        _fail(f"SCENARIO_REWIRE_INVALID: source selected class is malformed: {exc}")
    if not selected_reduced or not old_class:
        _fail("SCENARIO_REWIRE_INVALID: source selected class is empty")
    interface_keys = {value.casefold() for value in interface_ids}
    scenario_surface_ids = getattr(scenario_network, "surface_node_ids", ())
    try:
        scenario_selected_reduced = {
            value.casefold(): int(scenario_network.reduced_node_index(value))
            for value in selected_display
        }
        pairs: dict[int, set[int]] = {}
        for key, base_reduced in base_selected_reduced.items():
            pairs.setdefault(base_reduced, set()).add(scenario_selected_reduced[key])
        if any(len(values) != 1 for values in pairs.values()) or len({next(iter(values)) for values in pairs.values()}) != len(pairs):
            return stopped("SCENARIO_REWIRE_SOURCE_EDGE_DRIFT", "scenario selected classes were merged or split", {})
        scenario_reduced_values = set(scenario_selected_reduced.values())
        scenario_preimages: dict[int, set[str]] = {}
        for value in scenario_surface_ids:
            reduced = int(scenario_network.reduced_node_index(value))
            if reduced in scenario_reduced_values:
                scenario_preimages.setdefault(reduced, set()).add(value.casefold())
        for base_reduced, scenario_values in pairs.items():
            scenario_reduced = next(iter(scenario_values))
            if base_preimages.get(base_reduced, set()) != scenario_preimages.get(scenario_reduced, set()):
                return stopped("SCENARIO_REWIRE_SOURCE_EDGE_DRIFT", "scenario selected class members differ", {"base_reduced": base_reduced, "scenario_reduced": scenario_reduced})
        scenario_old_class = set().union(*scenario_preimages.values())
    except Exception as exc:
        return stopped("SCENARIO_REWIRE_SOURCE_EDGE_DRIFT", "scenario selected class is not preserved", {"error": str(exc)})
    if scenario_old_class != old_class:
        return stopped("SCENARIO_REWIRE_SOURCE_EDGE_DRIFT", "scenario selected class differs", {"base_class": sorted(old_class), "scenario_class": sorted(scenario_old_class)})
    boundary_keys = old_class | interface_keys
    if any(value.casefold() in interface_keys for value in scenario_surface_ids):
        return stopped("SCENARIO_REWIRE_BOUNDARY_BYPASS", "planned interface node already exists in scenario network", {"interfaces": interface_ids})
    for _port_id, positive, negative in scenario_ports:
        if {positive.casefold(), negative.casefold()} & boundary_keys:
            return stopped("SCENARIO_REWIRE_BOUNDARY_BYPASS", "port endpoint bypasses source-plane boundary", {"port_id": _port_id})

    expected_rows = {str(row.get("old_finite_edge_id", "")).casefold(): row for row in rows}
    rewire_keys = set(expected_rows)
    if len(rewire_keys) != len(rows) or any(not key for key in rewire_keys):
        _fail("SCENARIO_REWIRE_INVALID: P5 edge IDs are malformed")
    planned_owner_keys = {value.casefold() for value in planned_owners}
    seen_edges: set[str] = set()
    manifest_digest = sha256(b"[")
    for ordinal, link in enumerate(getattr(scenario_network, "via_links", ())):
        if ordinal:
            manifest_digest.update(b",")
        encoded = concrete_canonical_json_bytes({"link_id": link.link_id.casefold(), "first_node_id": link.first_node_id.casefold(), "second_node_id": link.second_node_id.casefold(), "count": link.count, "mode": link.mode, "resistance_ohm_per_via": link.resistance_ohm_per_via, "inductance_h_per_via": link.inductance_h_per_via, "owner_ids": sorted(owner.casefold() for owner in link.owner_ids)})
        manifest_digest.update(encoded[:-1] if encoded.endswith(b"\n") else encoded)
        edge_key = str(link.link_id).casefold()
        owner_keys = {str(owner).casefold() for owner in link.owner_ids}
        if planned_owner_keys & owner_keys:
            return stopped("SCENARIO_REWIRE_BOUNDARY_BYPASS", "planned plane owner is present in scenario links", {"link_id": edge_key})
        endpoints = {str(link.first_node_id).casefold(), str(link.second_node_id).casefold()}
        row = expected_rows.get(edge_key)
        if row is None:
            if endpoints & boundary_keys and not (str(link.mode) == "topology_only_ideal" and endpoints <= old_class):
                return stopped("SCENARIO_REWIRE_BOUNDARY_BYPASS", "scenario link crosses source-plane boundary", {"link_id": edge_key})
            continue
        if edge_key in seen_edges:
            return stopped("SCENARIO_REWIRE_SOURCE_EDGE_DRIFT", "P5 source edge is not exact-once", {"link_id": edge_key})
        selected_endpoint = str(row.get("selected_old_endpoint", ""))
        external_endpoint = str(row.get("external_endpoint", ""))
        try:
            metadata_drift = (
                str(link.mode) != str(row.get("mode"))
                or int(link.count) != int(row.get("count"))
                or tuple(str(owner) for owner in link.owner_ids) != tuple(str(owner) for owner in row.get("owner_ids", ()))
                or endpoints != {selected_endpoint.casefold(), external_endpoint.casefold()}
                or float(link.resistance_ohm_per_via).hex() != str(row.get("resistance_ohm_per_via_hex"))
                or float(link.inductance_h_per_via).hex() != str(row.get("inductance_h_per_via_hex"))
            )
        except (AttributeError, TypeError, ValueError) as exc:
            _fail(f"SCENARIO_REWIRE_INVALID: scenario source edge metadata is malformed: {exc}")
        if metadata_drift:
            return stopped("SCENARIO_REWIRE_SOURCE_EDGE_DRIFT", "scenario source edge metadata differs", {"link_id": edge_key})
        seen_edges.add(edge_key)
    manifest_digest.update(b"]\n")
    expected_link_manifest = manifest_digest.hexdigest()
    if seen_edges != rewire_keys:
        return stopped("SCENARIO_REWIRE_SOURCE_EDGE_SUPPRESSED", "P5 source edge is absent from scenario network", {"expected": sorted(rewire_keys), "seen": sorted(seen_edges)})
    if surface_manifest != expected_surface_manifest or link_manifest != expected_link_manifest:
        return stopped("SCENARIO_REWIRE_IDENTITY_MISMATCH", "scenario network manifest differs from binding provenance", {"surface_manifest": surface_manifest, "expected_surface_manifest": expected_surface_manifest, "link_manifest": link_manifest, "expected_link_manifest": expected_link_manifest})

    termination_owner_ids: set[str] = set()
    for cluster in tuple(getattr(termination, "clusters", ())):
        source = getattr(cluster, "source", None)
        if source is None:
            _fail("SCENARIO_REWIRE_INVALID: termination cluster source is absent")
        surface_endpoints = {
            str(getattr(source, "positive_surface_node_id", "")).casefold(),
            str(getattr(source, "negative_surface_node_id", "")).casefold(),
        }
        if surface_endpoints & boundary_keys:
            return stopped("SCENARIO_REWIRE_BOUNDARY_BYPASS", "termination surface bypasses source-plane boundary", {"cluster_id": str(getattr(source, "cluster_id", ""))})
        termination_owner_ids.update(str(owner).casefold() for owner in getattr(cluster, "owner_ids", ()))
    if {value.casefold() for value in planned_owners} & termination_owner_ids:
        return stopped("SCENARIO_REWIRE_BOUNDARY_BYPASS", "termination owner overlaps planned plane owner", {})
    if getattr(scenario_network, "partials", None) is not getattr(base_network, "partials", None):
        return stopped("SCENARIO_REWIRE_SOURCE_EDGE_DRIFT", "scenario partial objects were not reused", {})
    boundary_identity = {
        "port_inventory": scenario_ports,
        "termination_manifest_sha256": termination_id,
        "termination_owner_ids": sorted(termination_owner_ids),
        "old_selected_class_node_ids": sorted(old_class),
    }
    boundary_sha = sha256(concrete_canonical_json_bytes(boundary_identity)).hexdigest()

    identity = {
        **common,
        "rewire_rows": rows,
        "port_inventory": scenario_ports,
        "planned_interface_node_ids": interface_ids,
        "old_selected_class_node_ids": sorted(old_class),
        "termination_owner_ids": sorted(termination_owner_ids),
        "port_termination_boundary_sha256": boundary_sha,
    }
    return {
        "schema_version": "source-plane-shadow-rewire-commutation-v1",
        "status": "passed",
        "code": None,
        "detail": None,
        "shadow_only": True,
        "production_ready": False,
        "replacement_ready": False,
        **common,
        "rewire_rows": rows,
        "port_inventory": scenario_ports,
        "planned_interface_node_ids": interface_ids,
        "old_selected_class_node_ids": sorted(old_class),
        "port_termination_boundary_sha256": boundary_sha,
        "scenario_commutation_sha256": sha256(concrete_canonical_json_bytes(identity)).hexdigest(),
    }


def audit_source_plane_patch_shadow_local_replacement_recipe(
    patch_result: Mapping[str, Any],
    rewire_plan: Mapping[str, Any],
    commutation_result: Mapping[str, Any],
    binding: LayerwiseScenarioNetworkBinding,
    *,
    rail_id: str,
) -> Mapping[str, Any]:
    """Build a bounded, shadow-only local replacement recipe."""
    rail_id = _text(rail_id, "rail_id")

    def digest(value: Any, label: str) -> str:
        if not isinstance(value, str):
            _fail(f"LOCAL_REPLACEMENT_INVALID: {label} is not SHA-256")
        value = value.strip()
        if len(value) != 64 or value != value.casefold() or any(c not in "0123456789abcdef" for c in value):
            _fail(f"LOCAL_REPLACEMENT_INVALID: {label} is not SHA-256")
        return value

    if not isinstance(patch_result, Mapping) or patch_result.get("schema_version") != "source-plane-contact-condensation-v1" or patch_result.get("status") != "complete" or patch_result.get("shadow_only") is not True:
        _fail("LOCAL_REPLACEMENT_INVALID: P1 is not complete shadow output")
    if not isinstance(rewire_plan, Mapping) or rewire_plan.get("schema_version") != "source-plane-shadow-contact-rewire-plan-v1" or rewire_plan.get("status") != "planned" or rewire_plan.get("shadow_only") is not True or rewire_plan.get("production_ready") is not False or rewire_plan.get("replacement_ready") is not False:
        _fail("LOCAL_REPLACEMENT_INVALID: P5 is not planned shadow output")
    if not isinstance(commutation_result, Mapping) or commutation_result.get("schema_version") != "source-plane-shadow-rewire-commutation-v1" or commutation_result.get("status") != "passed" or commutation_result.get("code") is not None or commutation_result.get("shadow_only") is not True or commutation_result.get("production_ready") is not False or commutation_result.get("replacement_ready") is not False:
        _fail("LOCAL_REPLACEMENT_INVALID: P6 is not passed shadow output")
    if not isinstance(binding, LayerwiseScenarioNetworkBinding):
        _fail("LOCAL_REPLACEMENT_INVALID: scenario binding type is invalid")

    def stream_hash(payload: Any) -> str:
        value = sha256()
        for chunk in iter_concrete_canonical_json_bytes(payload):
            value.update(chunk)
        return value.hexdigest()

    contacts = patch_result.get("contact_ids")
    owner_kinds = patch_result.get("owner_kinds")
    admittance = patch_result.get("admittance_s")
    constraints = patch_result.get("terminal_constraint_matrix")
    if not all(isinstance(value, list) for value in (contacts, owner_kinds, admittance, constraints)):
        _fail("LOCAL_REPLACEMENT_INVALID: P1 payload is incomplete")
    p1_output = stream_hash({"contact_ids": contacts, "owner_kinds": owner_kinds, "admittance_s": admittance, "terminal_constraint_matrix": constraints})
    p1_input = digest(patch_result.get("input_sha256"), "P1 input")
    source_sha = digest(patch_result.get("source_sha256"), "P1 source")
    patch_rail = str(patch_result.get("rail_id", ""))
    p5_input = digest(rewire_plan.get("p1_input_sha256"), "P5 input")
    p6_input = digest(commutation_result.get("p1_input_sha256"), "P6 input")
    p5_output = digest(rewire_plan.get("p1_output_sha256"), "P5 output")
    p6_output = digest(commutation_result.get("p1_output_sha256"), "P6 output")
    source_p5 = digest(rewire_plan.get("source_sha256"), "P5 source")
    source_p6 = digest(commutation_result.get("source_sha256"), "P6 source")
    rail_p5 = str(rewire_plan.get("rail_id", "")); rail_p6 = str(commutation_result.get("rail_id", ""))
    old_edge_set = digest(rewire_plan.get("old_edge_set_sha256"), "P5 old edge set")
    old_edge_set_p6 = digest(commutation_result.get("old_edge_set_sha256"), "P6 old edge set")
    substrate_id = digest(rewire_plan.get("substrate_identity_sha256"), "P5 substrate")
    substrate_p6 = digest(commutation_result.get("substrate_identity_sha256"), "P6 substrate")
    shadow_split = digest(rewire_plan.get("shadow_split_sha256"), "P5 shadow split")
    shadow_split_p6 = digest(commutation_result.get("shadow_split_sha256"), "P6 shadow split")
    scenario_commutation = digest(commutation_result.get("scenario_commutation_sha256"), "P6 commutation")
    scenario_id = digest(binding.scenario_identity_sha256, "scenario")
    scenario_plan = digest(binding.plan_sha256, "scenario plan")
    termination_id = digest(binding.termination_manifest.manifest_sha256, "termination")
    binding_base = digest(binding.base_substrate_identity_sha256, "binding base")
    provenance = binding.provenance
    if not isinstance(provenance, Mapping):
        _fail("LOCAL_REPLACEMENT_INVALID: binding provenance is absent")
    surface_manifest = digest(provenance.get("scenario_surface_node_manifest_sha256"), "scenario surface manifest")
    link_manifest = digest(provenance.get("scenario_link_manifest_sha256"), "scenario link manifest")
    p6_surface_manifest = digest(commutation_result.get("scenario_surface_node_manifest_sha256"), "P6 surface manifest")
    p6_link_manifest = digest(commutation_result.get("scenario_link_manifest_sha256"), "P6 link manifest")
    boundary = digest(commutation_result.get("port_termination_boundary_sha256"), "P6 boundary")

    def stopped(code: str, detail: str, evidence: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        identity = {"rail_id": rail_id, "source_sha256": source_sha, "p1_input_sha256": p1_input, "p1_output_sha256": p1_output, "shadow_split_sha256": shadow_split, "scenario_commutation_sha256": scenario_commutation, "code": code, "detail": detail, "evidence": dict(evidence or {})}
        return {"schema_version": "source-plane-shadow-local-replacement-recipe-v1", "status": "stopped", "code": code, "shadow_only": True, "production_ready": False, "replacement_ready": False, **identity, "deterministic_recipe_sha256": sha256(concrete_canonical_json_bytes(identity)).hexdigest()}

    if source_p5 != source_sha or source_p6 != source_sha or patch_rail.casefold() != rail_id.casefold() or rail_p5.casefold() != rail_id.casefold() or rail_p6.casefold() != rail_id.casefold() or p5_input != p1_input or p6_input != p1_input or p5_output != p1_output or p6_output != p1_output or old_edge_set_p6 != old_edge_set or substrate_p6 != substrate_id or shadow_split_p6 != shadow_split:
        return stopped("LOCAL_REPLACEMENT_IDENTITY_MISMATCH", "P1/P5/P6 identity differs")
    if binding_base != substrate_id or scenario_id != digest(commutation_result.get("scenario_identity_sha256"), "P6 scenario") or scenario_plan != digest(commutation_result.get("scenario_plan_sha256"), "P6 plan") or termination_id != digest(commutation_result.get("termination_manifest_sha256"), "P6 termination"):
        return stopped("LOCAL_REPLACEMENT_IDENTITY_MISMATCH", "scenario identity differs")
    if p6_surface_manifest != surface_manifest or p6_link_manifest != link_manifest:
        return stopped("LOCAL_REPLACEMENT_IDENTITY_MISMATCH", "scenario provenance differs")
    p5_rows = rewire_plan.get("rewire_rows"); p6_rows = commutation_result.get("rewire_rows"); stamp = rewire_plan.get("planned_stamp")
    if not isinstance(p5_rows, list) or not isinstance(p6_rows, list) or not isinstance(stamp, Mapping) or any(not isinstance(row, Mapping) for row in (*p5_rows, *p6_rows)):
        _fail("LOCAL_REPLACEMENT_INVALID: rewire disclosures are malformed")
    interfaces = stamp.get("contact_interfaces")
    planned_interfaces = commutation_result.get("planned_interface_node_ids")
    stamp_interface_ids = stamp.get("contact_interface_node_ids")
    if not isinstance(interfaces, list) or not isinstance(planned_interfaces, list) or not isinstance(stamp_interface_ids, list) or [row.get("contact_id") for row in p5_rows] != contacts or p5_rows != p6_rows or [row.get("new_interface_node_id") for row in p5_rows] != stamp_interface_ids or [row.get("contact_id") for row in interfaces] != contacts or [row.get("interface_node_id") for row in interfaces] != stamp_interface_ids or stamp_interface_ids != planned_interfaces:
        return stopped("LOCAL_REPLACEMENT_CONTACT_ORDER_MISMATCH", "contact/interface order differs")
    planned_owners = rewire_plan.get("planned_block_owner_ids"); retained_owners = rewire_plan.get("retained_contact_finite_owner_ids")
    if not isinstance(planned_owners, list) or not isinstance(retained_owners, list) or any(not isinstance(owner, str) or not owner.strip() for owner in (*planned_owners, *retained_owners)) or len({owner.casefold() for owner in planned_owners}) != len(planned_owners) or len({owner.casefold() for owner in retained_owners}) != len(retained_owners) or stamp.get("owner_ids") != planned_owners:
        _fail("LOCAL_REPLACEMENT_INVALID: owner ledger is malformed")
    expected_retained = sorted({str(owner) for row in p5_rows for owner in row.get("owner_ids", ())}, key=str.casefold)
    if retained_owners != expected_retained or {str(owner).casefold() for owner in planned_owners} & {str(owner).casefold() for owner in retained_owners}:
        return stopped("LOCAL_REPLACEMENT_OWNER_CONFLICT", "owner ledger overlaps")
    diagnostics = patch_result.get("diagnostics")
    try:
        frequency_hz = float(patch_result["frequency_hz"]); passivity_min = float(diagnostics["passivity_min_eigenvalue_s"]); passivity_tolerance = float(diagnostics["passivity_tolerance_s"]); stamp_frequency = float(stamp["frequency_hz"])
    except (KeyError, TypeError, ValueError) as exc:
        _fail(f"LOCAL_REPLACEMENT_INVALID: diagnostics/frequency malformed: {exc}")
    if not isinstance(diagnostics, Mapping) or not math.isfinite(frequency_hz) or frequency_hz <= 0.0 or frequency_hz != 1.0e9 or stamp_frequency != frequency_hz or not math.isfinite(passivity_min) or not math.isfinite(passivity_tolerance):
        _fail("LOCAL_REPLACEMENT_INVALID: diagnostics/frequency malformed")
    passivity_sha = sha256(concrete_canonical_json_bytes({"passivity_min_eigenvalue_s": passivity_min, "passivity_tolerance_s": passivity_tolerance, "diagnostics": dict(diagnostics)})).hexdigest()
    if passivity_sha != digest(stamp.get("passivity_diagnostics_sha256"), "P5 passivity"):
        return stopped("LOCAL_REPLACEMENT_IDENTITY_MISMATCH", "passivity diagnostics differ")
    disabled = rewire_plan.get("disabled_old_edge_fingerprints")
    if not isinstance(disabled, list) or disabled != sorted(disabled) or any(digest(value, "disabled edge") != value for value in disabled) or len(disabled) != len(set(disabled)) or sha256(concrete_canonical_json_bytes(disabled)).hexdigest() != old_edge_set:
        _fail("LOCAL_REPLACEMENT_INVALID: disabled fingerprint set is malformed")
    network = binding.network; wanted = set(disabled); matched: list[dict[str, Any]] = []
    for partial_ordinal, wrapped in enumerate(network.partials):
        partial = wrapped.partial; matrix = partial.maxwell_capacitance_f; names = partial.net_names
        if not all(hasattr(matrix, key) for key in ("shape", "indptr", "indices", "data")) or matrix.shape != (len(names), len(names)):
            _fail("LOCAL_REPLACEMENT_INVALID: Maxwell partial is not CSC")
        for column in range(matrix.shape[1]):
            for offset in range(int(matrix.indptr[column]), int(matrix.indptr[column + 1])):
                row_index = int(matrix.indices[offset]); value = float(matrix.data[offset])
                if row_index >= column or value >= 0.0:
                    continue
                candidates = []
                for upper_id, lower_id in ((names[row_index], names[column]), (names[column], names[row_index])):
                    payload = {"substrate_identity_sha256": substrate_id, "upper_layer": str(partial.upper_layer), "lower_layer": str(partial.lower_layer), "upper_island_id": upper_id, "lower_island_id": lower_id, "capacitance_f_hex": float(-value).hex()}
                    fingerprint = sha256(concrete_canonical_json_bytes(payload)).hexdigest()
                    if fingerprint in wanted:
                        candidates.append((fingerprint, payload))
                if len(candidates) > 1:
                    return stopped("LOCAL_REPLACEMENT_OLD_EDGE_MISSING_OR_DUPLICATED", "old edge direction is ambiguous")
                if candidates:
                    fingerprint, payload = candidates[0]
                    matched.append({"fingerprint": fingerprint, "partial_ordinal": partial_ordinal, **payload})
    if len(matched) != len(wanted) or {row["fingerprint"] for row in matched} != wanted:
        return stopped("LOCAL_REPLACEMENT_OLD_EDGE_MISSING_OR_DUPLICATED", "old Maxwell edge is missing or duplicated")
    matched_ordinals = sorted({int(row["partial_ordinal"]) for row in matched})
    dispersion_by_ordinal: dict[int, dict[str, Any]] = {}; effective_by_ordinal: dict[int, complex] = {}
    for partial_ordinal in matched_ordinals:
        wrapped = network.partials[partial_ordinal]; partial = wrapped.partial; dispersion = wrapped.dispersion; layers = getattr(dispersion, "layers", None)
        if layers is None:
            if partial.separation_m is None:
                _fail("LOCAL_REPLACEMENT_INVALID: direct dielectric separation is absent")
            layers = ((float(partial.separation_m) * 1.0e6, dispersion),)
        materials: list[dict[str, Any]] = []
        for source_point_ordinal, (thickness_um, material) in enumerate(layers):
            frequencies = tuple(float(value) for value in material.frequencies_hz); points = [index for index, value in enumerate(frequencies) if value == frequency_hz]
            if len(points) != 1:
                return stopped("LOCAL_REPLACEMENT_SOURCE_POINT_UNAVAILABLE", "dielectric source point is unavailable", {"partial_ordinal": partial_ordinal, "source_point_ordinal": source_point_ordinal})
            point = points[0]; dk = float(material.relative_permittivities[point]); df = float(material.loss_tangents[point])
            if not all(math.isfinite(value) for value in (float(thickness_um), dk, df)):
                return stopped("LOCAL_REPLACEMENT_ALGEBRA_INVALID", "dielectric source point is non-finite", {"partial_ordinal": partial_ordinal, "source_point_ordinal": source_point_ordinal})
            materials.append({"ordinal": source_point_ordinal, "thickness_um_hex": float(thickness_um).hex(), "source_point_ordinal": point, "frequency_hz_hex": frequency_hz.hex(), "dk_hex": dk.hex(), "df_hex": df.hex()})
        effective_dk, effective_df = dispersion.interpolate((frequency_hz,)); dk_value = float(effective_dk[0]); df_value = float(effective_df[0]); nominal = float(partial.nominal_relative_permittivity)
        coefficient = 1j * 2.0 * math.pi * frequency_hz * (dk_value * (1.0 - 1j * df_value) / nominal)
        if not all(math.isfinite(value) for value in (dk_value, df_value, nominal, coefficient.real, coefficient.imag)) or nominal <= 0.0:
            return stopped("LOCAL_REPLACEMENT_ALGEBRA_INVALID", "dielectric admittance is non-finite", {"partial_ordinal": partial_ordinal})
        effective_by_ordinal[partial_ordinal] = coefficient
        dispersion_by_ordinal[partial_ordinal] = {"partial_ordinal": partial_ordinal, "frequency_hz_hex": frequency_hz.hex(), "effective_dk_hex": dk_value.hex(), "effective_df_hex": df_value.hex(), "nominal_dk_hex": nominal.hex(), "materials": materials}
    for row in matched:
        edge_y = effective_by_ordinal[int(row["partial_ordinal"])] * float.fromhex(row["capacitance_f_hex"])
        if not all(math.isfinite(value) for value in (edge_y.real, edge_y.imag)):
            return stopped("LOCAL_REPLACEMENT_ALGEBRA_INVALID", "Maxwell admittance is non-finite", {"partial_ordinal": row["partial_ordinal"]})
        row["frequency_hz_hex"] = frequency_hz.hex(); row["admittance_real_hex"] = float(edge_y.real).hex(); row["admittance_imag_hex"] = float(edge_y.imag).hex(); row["dispersion"] = dispersion_by_ordinal[int(row["partial_ordinal"])]
    by_edge = {str(row.get("old_finite_edge_id", "")).casefold(): row for row in p5_rows}
    if len(by_edge) != len(p5_rows):
        _fail("LOCAL_REPLACEMENT_INVALID: old finite edge IDs are duplicated")
    found: dict[str, dict[str, Any]] = {}
    for link in network.via_links:
        key = str(link.link_id).casefold(); row = by_edge.get(key)
        if row is None:
            continue
        if key in found:
            return stopped("LOCAL_REPLACEMENT_OLD_EDGE_MISSING_OR_DUPLICATED", "finite contact edge is duplicated")
        expected_rl = (str(row.get("resistance_ohm_per_via_hex", "")), str(row.get("inductance_h_per_via_hex", "")))
        actual_rl = (float(link.resistance_ohm_per_via).hex(), float(link.inductance_h_per_via).hex())
        if str(link.mode) != str(row.get("mode")) or tuple(link.owner_ids) != tuple(row.get("owner_ids", ())) or int(link.count) != int(row.get("count")) or actual_rl != expected_rl:
            return stopped("LOCAL_REPLACEMENT_OLD_EDGE_MISSING_OR_DUPLICATED", "finite contact metadata differs", {"link_id": link.link_id})
        selected = str(row.get("selected_old_endpoint", "")); external = str(row.get("external_endpoint", ""))
        if {selected, external} != {str(link.first_node_id), str(link.second_node_id)} or selected == external:
            return stopped("LOCAL_REPLACEMENT_OLD_EDGE_MISSING_OR_DUPLICATED", "finite contact endpoints differ", {"link_id": link.link_id})
        denominator = complex(float(link.resistance_ohm_per_via), 2.0 * math.pi * frequency_hz * float(link.inductance_h_per_via))
        if denominator == 0j:
            return stopped("LOCAL_REPLACEMENT_ALGEBRA_INVALID", "finite branch denominator is zero", {"link_id": link.link_id})
        y = float(link.count) / denominator
        if not all(math.isfinite(value) for value in (denominator.real, denominator.imag, y.real, y.imag)):
            return stopped("LOCAL_REPLACEMENT_ALGEBRA_INVALID", "finite branch admittance is invalid", {"link_id": link.link_id})
        interface = str(row.get("new_interface_node_id", "")); new_pair = (interface, external) if selected == str(link.first_node_id) else (external, interface)
        found[key] = {"contact_id": row.get("contact_id"), "old_finite_edge_id": link.link_id, "old_endpoint_pair": [str(link.first_node_id), str(link.second_node_id)], "new_endpoint_pair": list(new_pair), "selected_old_endpoint": selected, "external_endpoint": external, "new_interface_node_id": interface, "mode": str(link.mode), "count": int(link.count), "resistance_ohm_per_via_hex": actual_rl[0], "inductance_h_per_via_hex": actual_rl[1], "owner_ids": list(link.owner_ids), "admittance_real_hex": float(y.real).hex(), "admittance_imag_hex": float(y.imag).hex()}
    if set(found) != set(by_edge):
        return stopped("LOCAL_REPLACEMENT_OLD_EDGE_MISSING_OR_DUPLICATED", "finite contact edge is missing")
    rewire_finite = [found[str(row["old_finite_edge_id"]).casefold()] for row in p5_rows]
    owner_ledger = {"removed_old_block_owner_ids": list(planned_owners), "added_p1_block_owner_ids": list(planned_owners), "retained_finite_owner_ids": list(retained_owners)}
    add_p1 = {"contact_ids": list(contacts), "interface_node_ids": list(planned_interfaces), "planned_owner_ids": list(planned_owners), "p1_input_sha256": p1_input, "p1_output_sha256": p1_output, "admittance_shape": [len(admittance), len(admittance[0]) if admittance else 0], "constraint_shape": [len(constraints), len(constraints[0]) if constraints else 0], "passivity_diagnostics_sha256": passivity_sha}
    recipe_identity = {"rail_id": rail_id, "source_sha256": source_sha, "p1_input_sha256": p1_input, "p1_output_sha256": p1_output, "old_edge_set_sha256": old_edge_set, "substrate_identity_sha256": substrate_id, "shadow_split_sha256": shadow_split, "scenario_identity_sha256": scenario_id, "scenario_plan_sha256": scenario_plan, "scenario_commutation_sha256": scenario_commutation, "scenario_surface_node_manifest_sha256": surface_manifest, "scenario_link_manifest_sha256": link_manifest, "termination_manifest_sha256": termination_id, "port_termination_boundary_sha256": boundary, "remove_old_maxwell": matched, "rewire_finite": rewire_finite, "owner_ledger": owner_ledger, "add_p1_nport": add_p1}
    return {"schema_version": "source-plane-shadow-local-replacement-recipe-v1", "status": "passed", "code": None, "shadow_only": True, "production_ready": False, "replacement_ready": False, **{key: recipe_identity[key] for key in ("rail_id", "source_sha256", "p1_input_sha256", "p1_output_sha256", "old_edge_set_sha256", "substrate_identity_sha256", "shadow_split_sha256", "scenario_identity_sha256", "scenario_plan_sha256", "scenario_commutation_sha256", "scenario_surface_node_manifest_sha256", "scenario_link_manifest_sha256", "termination_manifest_sha256", "port_termination_boundary_sha256")}, "remove_old_maxwell": matched, "rewire_finite": rewire_finite, "owner_ledger": owner_ledger, "add_p1_nport": add_p1, "deterministic_recipe_sha256": sha256(concrete_canonical_json_bytes(recipe_identity)).hexdigest()}


def materialize_source_plane_patch_shadow_topology_embedding(
    commutation_result: Mapping[str, Any],
    recipe_result: Mapping[str, Any],
    binding: LayerwiseScenarioNetworkBinding,
    *,
    rail_id: str,
) -> tuple[CompiledLayerSurfaceNetwork | None, Mapping[str, Any]]:
    """Materialize P6/P7 topology into one ephemeral immutable shadow network."""
    rail_id = _text(rail_id, "rail_id")

    def digest(value: Any, label: str) -> str:
        if not isinstance(value, str):
            _fail(f"SHADOW_EMBEDDING_INVALID: {label} is not SHA-256")
        value = value.strip()
        if len(value) != 64 or value != value.casefold() or any(character not in "0123456789abcdef" for character in value):
            _fail(f"SHADOW_EMBEDDING_INVALID: {label} is not SHA-256")
        return value

    if not isinstance(commutation_result, Mapping) or commutation_result.get("schema_version") != "source-plane-shadow-rewire-commutation-v1" or commutation_result.get("status") != "passed" or commutation_result.get("code") is not None or commutation_result.get("shadow_only") is not True or commutation_result.get("production_ready") is not False or commutation_result.get("replacement_ready") is not False:
        _fail("SHADOW_EMBEDDING_INVALID: P6 is not passed shadow output")
    if not isinstance(recipe_result, Mapping) or recipe_result.get("schema_version") != "source-plane-shadow-local-replacement-recipe-v1" or recipe_result.get("status") != "passed" or recipe_result.get("code") is not None or recipe_result.get("shadow_only") is not True or recipe_result.get("production_ready") is not False or recipe_result.get("replacement_ready") is not False:
        _fail("SHADOW_EMBEDDING_INVALID: P7 is not passed shadow output")
    if not isinstance(binding, LayerwiseScenarioNetworkBinding):
        _fail("SHADOW_EMBEDDING_INVALID: scenario binding type is invalid")

    def sequence_hash(values: Any) -> str:
        result = sha256(); result.update(b"[")
        for ordinal, value in enumerate(values):
            if ordinal:
                result.update(b",")
            encoded = concrete_canonical_json_bytes(value)
            result.update(encoded[:-1] if encoded.endswith(b"\n") else encoded)
        result.update(b"]\n")
        return result.hexdigest()

    source_sha = digest(commutation_result.get("source_sha256"), "P6 source")
    recipe_source = digest(recipe_result.get("source_sha256"), "P7 source")
    p6_rail = str(commutation_result.get("rail_id", "")); p7_rail = str(recipe_result.get("rail_id", ""))
    p6_input = digest(commutation_result.get("p1_input_sha256"), "P6 input"); p7_input = digest(recipe_result.get("p1_input_sha256"), "P7 input")
    p6_output = digest(commutation_result.get("p1_output_sha256"), "P6 output"); p7_output = digest(recipe_result.get("p1_output_sha256"), "P7 output")
    p6_substrate = digest(commutation_result.get("substrate_identity_sha256"), "P6 substrate"); p7_substrate = digest(recipe_result.get("substrate_identity_sha256"), "P7 substrate")
    p6_shadow = digest(commutation_result.get("shadow_split_sha256"), "P6 shadow split"); p7_shadow = digest(recipe_result.get("shadow_split_sha256"), "P7 shadow split")
    p6_old_edge = digest(commutation_result.get("old_edge_set_sha256"), "P6 old edge"); p7_old_edge = digest(recipe_result.get("old_edge_set_sha256"), "P7 old edge")
    scenario_id = digest(binding.scenario_identity_sha256, "scenario identity"); scenario_plan = digest(binding.plan_sha256, "scenario plan"); termination_id = digest(binding.termination_manifest.manifest_sha256, "termination manifest")
    scenario_commutation = digest(commutation_result.get("scenario_commutation_sha256"), "P6 commutation")
    surface_manifest = digest(commutation_result.get("scenario_surface_node_manifest_sha256"), "P6 surface manifest"); link_manifest = digest(commutation_result.get("scenario_link_manifest_sha256"), "P6 link manifest"); boundary = digest(commutation_result.get("port_termination_boundary_sha256"), "P6 boundary")
    p7_scenario = digest(recipe_result.get("scenario_identity_sha256"), "P7 scenario"); p7_plan = digest(recipe_result.get("scenario_plan_sha256"), "P7 plan"); p7_termination = digest(recipe_result.get("termination_manifest_sha256"), "P7 termination"); p7_surface = digest(recipe_result.get("scenario_surface_node_manifest_sha256"), "P7 surface manifest"); p7_link = digest(recipe_result.get("scenario_link_manifest_sha256"), "P7 link manifest"); p7_boundary = digest(recipe_result.get("port_termination_boundary_sha256"), "P7 boundary"); p7_commutation = digest(recipe_result.get("scenario_commutation_sha256"), "P7 commutation")
    p7_recipe_hash = digest(recipe_result.get("deterministic_recipe_sha256"), "P7 recipe")
    p7_nport = recipe_result.get("add_p1_nport")
    if not isinstance(p7_nport, Mapping) or not isinstance(p7_nport.get("interface_node_ids"), list):
        _fail("SHADOW_EMBEDDING_INVALID: P7 interface disclosure is malformed")
    p7_interfaces = p7_nport["interface_node_ids"]
    provenance = binding.provenance
    if not isinstance(provenance, Mapping):
        _fail("SHADOW_EMBEDDING_INVALID: binding provenance is absent")
    common = {"schema_version": "source-plane-shadow-topology-embedding-v1", "shadow_only": True, "p1_stamp_applied": False, "solve_eligible": False, "production_ready": False, "replacement_ready": False, "rail_id": rail_id, "source_sha256": source_sha, "p1_input_sha256": p7_input, "p1_output_sha256": p7_output, "old_edge_set_sha256": p7_old_edge, "substrate_identity_sha256": p7_substrate, "shadow_split_sha256": p7_shadow, "scenario_identity_sha256": scenario_id, "scenario_plan_sha256": scenario_plan, "scenario_commutation_sha256": scenario_commutation, "scenario_surface_node_manifest_sha256": surface_manifest, "scenario_link_manifest_sha256": link_manifest, "termination_manifest_sha256": termination_id, "port_termination_boundary_sha256": boundary, "p7_deterministic_recipe_sha256": p7_recipe_hash}

    def stopped(code: str, detail: str) -> tuple[None, Mapping[str, Any]]:
        identity = {**common, "status": "stopped", "topology_materialized": False, "code": code, "detail": detail}
        return None, {**identity, "topology_embedding_sha256": sha256(concrete_canonical_json_bytes(identity)).hexdigest()}

    if source_sha != recipe_source or p6_rail.casefold() != rail_id.casefold() or p7_rail.casefold() != rail_id.casefold() or p6_input != p7_input or p6_output != p7_output or p6_substrate != p7_substrate or p6_shadow != p7_shadow or p6_old_edge != p7_old_edge or p6_substrate != digest(binding.base_substrate_identity_sha256, "binding base") or scenario_id != digest(commutation_result.get("scenario_identity_sha256"), "P6 scenario") or scenario_plan != digest(commutation_result.get("scenario_plan_sha256"), "P6 plan") or termination_id != digest(commutation_result.get("termination_manifest_sha256"), "P6 termination") or p7_scenario != scenario_id or p7_plan != scenario_plan or p7_termination != termination_id or p7_surface != surface_manifest or p7_link != link_manifest or p7_boundary != boundary or p7_commutation != scenario_commutation or p7_interfaces != commutation_result.get("planned_interface_node_ids") or surface_manifest != digest(provenance.get("scenario_surface_node_manifest_sha256"), "binding surface manifest") or link_manifest != digest(provenance.get("scenario_link_manifest_sha256"), "binding link manifest") or scenario_id != digest(provenance.get("scenario_identity_sha256"), "binding scenario identity") or scenario_plan != digest(provenance.get("scenario_plan_sha256"), "binding scenario plan"):
        return stopped("SHADOW_EMBEDDING_IDENTITY_MISMATCH", "identity chain differs")

    add_p1 = recipe_result.get("add_p1_nport"); remove_rows = recipe_result.get("remove_old_maxwell"); rewire_rows = recipe_result.get("rewire_finite")
    if not isinstance(add_p1, Mapping) or not isinstance(remove_rows, list) or not isinstance(rewire_rows, list):
        _fail("SHADOW_EMBEDDING_INVALID: P7 recipe disclosures are malformed")
    recipe_identity = {key: recipe_result.get(key) for key in ("rail_id", "source_sha256", "p1_input_sha256", "p1_output_sha256", "old_edge_set_sha256", "substrate_identity_sha256", "shadow_split_sha256", "scenario_identity_sha256", "scenario_plan_sha256", "scenario_commutation_sha256", "scenario_surface_node_manifest_sha256", "scenario_link_manifest_sha256", "termination_manifest_sha256", "port_termination_boundary_sha256")}
    recipe_identity.update({"remove_old_maxwell": remove_rows, "rewire_finite": rewire_rows, "owner_ledger": recipe_result.get("owner_ledger"), "add_p1_nport": add_p1})
    if p7_recipe_hash != sha256(concrete_canonical_json_bytes(recipe_identity)).hexdigest():
        return stopped("SHADOW_EMBEDDING_IDENTITY_MISMATCH", "P7 recipe hash differs")

    p6_common_keys = ("source_sha256", "base_cutset_sha256", "p3_input_identity_sha256", "p2_audit_sha256", "p1_input_sha256", "p1_output_sha256", "old_edge_set_sha256", "substrate_identity_sha256", "shadow_split_sha256", "scenario_identity_sha256", "scenario_plan_sha256", "scenario_surface_node_manifest_sha256", "scenario_link_manifest_sha256", "termination_manifest_sha256", "port_termination_boundary_sha256")
    p6_identity = {"rail_id": commutation_result.get("rail_id")}
    if not isinstance(p6_identity["rail_id"], str):
        _fail("SHADOW_EMBEDDING_INVALID: P6 rail disclosure is malformed")
    for key in p6_common_keys:
        p6_identity[key] = digest(commutation_result.get(key), f"P6 {key}")
    p6_rows = commutation_result.get("rewire_rows"); p6_ports = commutation_result.get("port_inventory"); p6_interfaces = commutation_result.get("planned_interface_node_ids"); p6_old_values = commutation_result.get("old_selected_class_node_ids")
    if not isinstance(p6_rows, list) or not isinstance(p6_ports, (list, tuple)) or not isinstance(p6_interfaces, list) or not isinstance(p6_old_values, list):
        _fail("SHADOW_EMBEDDING_INVALID: P6 identity disclosure is malformed")
    termination_owner_ids: set[str] = set()
    for cluster in tuple(binding.termination_manifest.clusters):
        owners = getattr(cluster, "owner_ids", None)
        if not isinstance(owners, tuple):
            _fail("SHADOW_EMBEDDING_INVALID: termination owner disclosure is malformed")
        termination_owner_ids.update(str(owner).casefold() for owner in owners)
    p6_identity.update({"rewire_rows": p6_rows, "port_inventory": p6_ports, "planned_interface_node_ids": p6_interfaces, "old_selected_class_node_ids": p6_old_values, "termination_owner_ids": sorted(termination_owner_ids)})
    if sha256(concrete_canonical_json_bytes(p6_identity)).hexdigest() != scenario_commutation:
        return stopped("SHADOW_EMBEDDING_IDENTITY_MISMATCH", "P6 commutation identity differs")

    old_values = commutation_result.get("old_selected_class_node_ids"); interface_ids = add_p1.get("interface_node_ids")
    if not isinstance(old_values, list) or not old_values or any(not isinstance(value, str) or not value.strip() for value in old_values) or old_values != sorted(set(old_values), key=str.casefold) or not isinstance(interface_ids, list) or not interface_ids or any(not isinstance(value, str) or not value.strip() for value in interface_ids) or len(interface_ids) != len(set(str(value).casefold() for value in interface_ids)) or any(not isinstance(row, Mapping) for row in rewire_rows) or [row.get("new_interface_node_id") for row in rewire_rows] != interface_ids:
        _fail("SHADOW_EMBEDDING_INVALID: class/interface order is malformed")
    old_keys = {str(value).casefold() for value in old_values}; interface_keys = {str(value).casefold() for value in interface_ids}
    surface_nodes = tuple(binding.network.surface_node_ids)
    surface_keys = {str(value).casefold() for value in surface_nodes}
    if old_keys != old_keys & surface_keys:
        return stopped("SHADOW_EMBEDDING_OLD_CLASS_INCOMPLETE", "old class is not a surface subset")
    if any(str(value).casefold() in surface_keys for value in interface_ids):
        return stopped("SHADOW_EMBEDDING_INTERFACE_COLLAPSED", "interfaces collide with retained surfaces")
    retained_surface_nodes = tuple(node for node in surface_nodes if str(node).casefold() not in old_keys)
    append_nodes = retained_surface_nodes + tuple(str(value) for value in interface_ids)
    network_partials = binding.network.partials
    expected_remove: dict[int, list[Mapping[str, Any]]] = {}
    expected_edge_lookup: dict[tuple[int, tuple[str, str], str], Mapping[str, Any]] = {}
    for row in remove_rows:
        if not isinstance(row, Mapping):
            _fail("SHADOW_EMBEDDING_INVALID: P7 old-Maxwell row is malformed")
        ordinal = row.get("partial_ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0 or ordinal >= len(network_partials):
            return stopped("SHADOW_EMBEDDING_OLD_CLASS_INCOMPLETE", "P7 partial ordinal is malformed")
        expected_remove.setdefault(ordinal, []).append(row)
        partial_ref = network_partials[ordinal].partial
        fingerprint = row.get("fingerprint")
        fingerprint_payload = {"substrate_identity_sha256": p7_substrate, "upper_layer": str(row.get("upper_layer", "")), "lower_layer": str(row.get("lower_layer", "")), "upper_island_id": row.get("upper_island_id"), "lower_island_id": row.get("lower_island_id"), "capacitance_f_hex": row.get("capacitance_f_hex")}
        if str(row.get("upper_island_id", "")).casefold() not in old_keys or str(row.get("lower_island_id", "")).casefold() not in old_keys or not isinstance(fingerprint, str) or str(row.get("upper_layer", "")) != str(partial_ref.upper_layer) or str(row.get("lower_layer", "")) != str(partial_ref.lower_layer) or sha256(concrete_canonical_json_bytes(fingerprint_payload)).hexdigest() != fingerprint:
            return stopped("SHADOW_EMBEDDING_OLD_CLASS_INCOMPLETE", "P7 edge leaves old class")
        edge_key = (ordinal, tuple(sorted((str(row.get("upper_island_id")).casefold(), str(row.get("lower_island_id")).casefold()))), str(row.get("capacitance_f_hex")))
        if edge_key in expected_edge_lookup:
            return stopped("SHADOW_EMBEDDING_OLD_CLASS_INCOMPLETE", "P7 old-Maxwell edge is ambiguous")
        expected_edge_lookup[edge_key] = row
    fingerprints = [str(row.get("fingerprint")) for rows in expected_remove.values() for row in rows]
    if len(fingerprints) != len(set(fingerprints)):
        return stopped("SHADOW_EMBEDDING_OLD_CLASS_INCOMPLETE", "P7 old-edge fingerprint is duplicated")
    partials: list[tuple[int, Any]] = []; dropped_partials: list[int] = []; touched_ordinals: set[int] = set(); matched_remove_counts = {fingerprint: 0 for fingerprint in fingerprints}
    for ordinal, wrapped in enumerate(binding.network.partials):
        partial = wrapped.partial; names = tuple(str(value) for value in partial.net_names); name_keys = tuple(value.casefold() for value in names); touched = any(value in old_keys for value in name_keys)
        if not touched:
            partials.append((ordinal, wrapped)); continue
        touched_ordinals.add(ordinal); matrix = partial.maxwell_capacitance_f
        matrix = matrix.tocsc(copy=False) if issparse(matrix) else csc_matrix(matrix)
        if matrix.shape != (len(names), len(names)):
            return stopped("SHADOW_EMBEDDING_PARTIAL_ESCAPE", "partial matrix shape differs")
        for column in range(matrix.shape[1]):
            for offset in range(int(matrix.indptr[column]), int(matrix.indptr[column + 1])):
                row_index = int(matrix.indices[offset]); value = float(matrix.data[offset])
                if row_index == column or value == 0.0:
                    continue
                left_old = name_keys[row_index] in old_keys; right_old = name_keys[column] in old_keys
                if left_old != right_old:
                    return stopped("SHADOW_EMBEDDING_PARTIAL_ESCAPE", "partial has old-to-retained coupling")
                if row_index < column:
                    edge_key = (ordinal, tuple(sorted((name_keys[row_index], name_keys[column]))), float(-value).hex())
                    expected = expected_edge_lookup.get(edge_key)
                    if expected is not None:
                        matched_remove_counts[str(expected.get("fingerprint"))] += 1
        keep = [index for index, key in enumerate(name_keys) if key not in old_keys]
        if not keep:
            dropped_partials.append(ordinal); continue
        retained_matrix = matrix[keep, :][:, keep].tocsc(); retained_matrix.eliminate_zeros()
        if retained_matrix.nnz == 0:
            dropped_partials.append(ordinal); continue
        try:
            new_partial = replace(partial, net_names=tuple(names[index] for index in keep), maxwell_capacitance_f=retained_matrix)
            partials.append((ordinal, replace(wrapped, partial=new_partial)))
        except Exception:
            return stopped("SHADOW_EMBEDDING_UNREPRESENTABLE", "partial replacement is invalid")
    if any(count != 1 for count in matched_remove_counts.values()) or not set(expected_remove).issubset(touched_ordinals):
        return stopped("SHADOW_EMBEDDING_OLD_CLASS_INCOMPLETE", "P7 old-Maxwell partial is absent")
    expected_links = {str(row.get("old_finite_edge_id", "")).casefold(): row for row in rewire_rows if isinstance(row, Mapping)}
    if len(expected_links) != len(rewire_rows):
        return stopped("SHADOW_EMBEDDING_LINK_ESCAPE", "P7 finite-link IDs are duplicated")
    found_links: set[str] = set(); links: list[Any] = []
    for link in binding.network.via_links:
        first = str(link.first_node_id); second = str(link.second_node_id); first_old = first.casefold() in old_keys; second_old = second.casefold() in old_keys; key = str(link.link_id).casefold()
        if first_old and second_old:
            if str(link.mode) != "topology_only_ideal":
                return stopped("SHADOW_EMBEDDING_LINK_ESCAPE", "finite old-class link is not removable")
            continue
        if first_old != second_old:
            row = expected_links.get(key)
            if row is None or str(link.mode) != "finite_parallel_rl" or found_links & {key}:
                return stopped("SHADOW_EMBEDDING_LINK_ESCAPE", "old boundary link lacks exact P7 rewire")
            selected = str(row.get("selected_old_endpoint", "")); external = str(row.get("external_endpoint", "")); interface = str(row.get("new_interface_node_id", ""))
            old_pair = row.get("old_endpoint_pair"); new_pair = row.get("new_endpoint_pair")
            expected_new_pair = [interface if first == selected else first, interface if second == selected else second]
            try:
                expected_mode = str(row["mode"]); expected_count = int(row["count"]); expected_owners = tuple(row["owner_ids"])
                expected_resistance = str(row["resistance_ohm_per_via_hex"]); expected_inductance = str(row["inductance_h_per_via_hex"])
                actual_resistance = float(link.resistance_ohm_per_via).hex(); actual_inductance = float(link.inductance_h_per_via).hex()
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                _fail(f"SHADOW_EMBEDDING_INVALID: link disclosure is malformed: {exc}")
            if not isinstance(old_pair, list) or not isinstance(new_pair, list) or old_pair != [first, second] or new_pair != expected_new_pair or selected not in {first, second} or external not in {first, second} or selected == external or interface.casefold() not in interface_keys or expected_mode != str(link.mode) or expected_count != int(link.count) or expected_owners != tuple(link.owner_ids) or expected_resistance != actual_resistance or expected_inductance != actual_inductance:
                return stopped("SHADOW_EMBEDDING_LINK_ESCAPE", "P7 endpoint mapping differs")
            kwargs = {"first_node_id": expected_new_pair[0], "second_node_id": expected_new_pair[1]}
            try:
                links.append(replace(link, **kwargs))
            except Exception:
                return stopped("SHADOW_EMBEDDING_UNREPRESENTABLE", "finite-link replacement is invalid")
            found_links.add(key); continue
        if key in expected_links:
            return stopped("SHADOW_EMBEDDING_LINK_ESCAPE", "P7 edge does not touch old class")
        links.append(link)
    if found_links != set(expected_links):
        return stopped("SHADOW_EMBEDDING_LINK_ESCAPE", "P7 finite link is missing")
    if not partials:
        return stopped("SHADOW_EMBEDDING_UNREPRESENTABLE", "all partials were removed")
    try:
        shadow_network = compile_layer_surface_network(append_nodes, partials=tuple(wrapper for _ordinal, wrapper in partials), via_links=tuple(links), ports=binding.network.ports)
        shadow_termination_manifest = compile_layer_surface_termination_manifest(shadow_network.surface_node_ids, tuple(cluster.source for cluster in binding.termination_manifest.clusters))
        shadow_network.termination_reduced_node_mapping(shadow_termination_manifest)
    except Exception:
        return stopped("SHADOW_EMBEDDING_UNREPRESENTABLE", "shadow topology is not representable")
    interface_embedding: list[dict[str, Any]] = []
    seen_reduced: set[int] = set()
    try:
        for row in rewire_rows:
            interface = str(row.get("new_interface_node_id")); finite_edge_id = str(row.get("old_finite_edge_id")); external_endpoint = str(row.get("external_endpoint")); contact_id = str(row.get("contact_id"))
            reduced = int(shadow_network.reduced_node_index(str(interface)))
            if reduced in seen_reduced:
                return stopped("SHADOW_EMBEDDING_INTERFACE_COLLAPSED", "interfaces share a reduced node")
            seen_reduced.add(reduced); interface_embedding.append({"contact_id": contact_id, "interface_node_id": interface, "reduced_index": reduced, "finite_edge_id": finite_edge_id, "external_endpoint": external_endpoint})
    except Exception:
        return stopped("SHADOW_EMBEDDING_INTERFACE_COLLAPSED", "interface reduced index is unavailable")
    surface_hash = sequence_hash(append_nodes)
    def partial_payloads():
        for original_ordinal, wrapped in partials:
            partial = wrapped.partial; matrix = partial.maxwell_capacitance_f
            matrix = matrix.tocsc(copy=False) if issparse(matrix) else csc_matrix(matrix)
            yield {"original_ordinal": original_ordinal, "upper_layer": partial.upper_layer, "lower_layer": partial.lower_layer, "nominal_relative_permittivity": float(partial.nominal_relative_permittivity).hex(), "separation_m_hex": None if partial.separation_m is None else float(partial.separation_m).hex(), "net_names": tuple(partial.net_names), "shape": tuple(int(value) for value in matrix.shape), "indptr": tuple(int(value) for value in matrix.indptr), "indices": tuple(int(value) for value in matrix.indices), "data": tuple(float(value).hex() for value in matrix.data)}
    partial_hash = sequence_hash(partial_payloads())
    link_hash = sequence_hash({"link_id": link.link_id, "first_node_id": link.first_node_id, "second_node_id": link.second_node_id, "mode": link.mode, "count": link.count, "resistance_ohm_per_via": link.resistance_ohm_per_via, "inductance_h_per_via": link.inductance_h_per_via, "owner_ids": link.owner_ids} for link in links)
    port_hash = sequence_hash({"port_id": port.port_id, "positive_node_id": port.positive_node_id, "negative_node_id": port.negative_node_id} for port in shadow_network.ports)
    shadow_surface_manifest = surface_hash; shadow_partial_manifest = partial_hash; shadow_link_manifest = link_hash; shadow_port_manifest = port_hash
    shadow_termination_manifest_sha256 = shadow_termination_manifest.manifest_sha256
    network_identity = {"base_substrate_identity_sha256": p7_substrate, "scenario_identity_sha256": scenario_id, "scenario_plan_sha256": scenario_plan, "p7_deterministic_recipe_sha256": p7_recipe_hash, "shadow_surface_manifest_sha256": shadow_surface_manifest, "shadow_partial_manifest_sha256": shadow_partial_manifest, "shadow_link_manifest_sha256": shadow_link_manifest, "shadow_port_manifest_sha256": shadow_port_manifest, "shadow_termination_manifest_sha256": shadow_termination_manifest_sha256, "interface_embedding": interface_embedding}
    shadow_network_identity = sha256(concrete_canonical_json_bytes(network_identity)).hexdigest()
    audit = {**common, "status": "passed", "topology_materialized": True, "removed_old_class_node_ids": list(old_values), "interface_embedding": interface_embedding, "dropped_partial_ordinals": dropped_partials, "shadow_surface_manifest_sha256": shadow_surface_manifest, "shadow_partial_manifest_sha256": shadow_partial_manifest, "shadow_link_manifest_sha256": shadow_link_manifest, "shadow_port_manifest_sha256": shadow_port_manifest, "shadow_termination_manifest_sha256": shadow_termination_manifest_sha256, "shadow_network_identity_sha256": shadow_network_identity}
    audit["topology_embedding_sha256"] = sha256(concrete_canonical_json_bytes(audit)).hexdigest()
    return shadow_network, audit


__all__ = ["SourcePlanePatchError", "consume_source_plane_patch", "evaluate_source_plane_contact_admissibility", "evaluate_source_plane_contact_condensation", "audit_source_plane_patch_owner_off", "audit_source_plane_patch_contact_quotient_representability", "audit_source_plane_patch_selected_base_cutset", "plan_source_plane_patch_shadow_contact_rewire", "audit_source_plane_patch_shadow_rewire_commutation", "audit_source_plane_patch_shadow_local_replacement_recipe", "materialize_source_plane_patch_shadow_topology_embedding"]
