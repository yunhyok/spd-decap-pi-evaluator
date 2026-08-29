"""Shadow-only source-plane patch witness (Phase 3).

This module consumes the authenticated Phase-1 ownership sidecar and raw-v3
plane sheet.  It deliberately returns data only; production MNA/Y/Z paths are
not touched.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from hashlib import sha256
import json
import math
from typing import Any

from .canonical_json import concrete_canonical_json_bytes
from .raw_spatial_contact_asset import load_raw_spatial_contact_asset
from .source_plane_ownership_ir import (
    MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS,
    load_source_plane_ownership_ir,
)
from ._core.geometry.ordered_boolean import ordered_spd_geometry
from ._core.solver.mfdm import EPSILON_0_F_PER_M, MU_0_H_PER_M
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


__all__ = ["SourcePlanePatchError", "consume_source_plane_patch", "evaluate_source_plane_contact_admissibility", "evaluate_source_plane_contact_condensation"]
