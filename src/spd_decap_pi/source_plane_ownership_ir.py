"""Authenticated, provenance-only source plane ownership sidecar (v1)."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import fields, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Final
import zlib

SOURCE_PLANE_OWNERSHIP_IR_METADATA_KEY: Final = "source_plane_ownership_ir"
SOURCE_PLANE_OWNERSHIP_IR_SCHEMA: Final = "source-plane-ownership-ir-v1"
SOURCE_PLANE_OWNERSHIP_IR_PAYLOAD_SCHEMA: Final = "source-plane-ownership-sqlite-v1"
SOURCE_PLANE_OWNERSHIP_IR_COMPILER_ID: Final = "source-plane-ownership-ir-compiler-v1"
SOURCE_PLANE_OWNERSHIP_IR_V2_SCHEMA: Final = "source-plane-ownership-ir-v2"
SOURCE_PLANE_OWNERSHIP_IR_V2_PAYLOAD_SCHEMA: Final = "source-plane-ownership-sqlite-v2"
SOURCE_PLANE_OWNERSHIP_IR_V2_COMPILER_ID: Final = "source-plane-ownership-ir-compiler-v2"
SOURCE_PLANE_OWNERSHIP_IR_PREFIX: Final = "ownership/"
SOURCE_PLANE_OWNERSHIP_IR_COMPRESSION: Final = "zlib"
MAX_SOURCE_PLANE_OWNERSHIP_IR_COMPRESSED_BYTES: Final = 512 * 1024 * 1024
MAX_SOURCE_PLANE_OWNERSHIP_IR_UNCOMPRESSED_BYTES: Final = 2 * 1024 * 1024 * 1024
# ponytail: selected-rail sidecar cap; move semantic joins fully into SQLite only if a real import exceeds it
MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS: Final = 100_000
MAX_SOURCE_PLANE_OWNERSHIP_IR_QUERY_ROWS: Final = 100_000
_APP_ID: Final = 0x53504F57
_USER_VERSION: Final = 1
_SECTIONS: Final = ("source_records", "surfaces", "primitives", "islands", "primitive_island_edges", "stackup_layers", "dielectric_points", "rail_bindings", "terminal_bindings", "retained_owner_refs", "plane_owner_scopes", "replacement_ledger", "replacement_ledger_members")
_V2_SECTIONS: Final = _SECTIONS + ("contact_boundary",)
_MANIFEST_KEYS: Final = frozenset({"storage_schema", "payload_schema", "compiler_id", "app_version", "source_sha256", "source_size_bytes", "target_rail_id", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256", "logical_rows_sha256", "asset_name", "compression", "compressed_size_bytes", "compressed_sha256", "uncompressed_size_bytes", "uncompressed_sha256", "counts"})


class SourcePlaneOwnershipIRError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "SOURCE_PLANE_OWNERSHIP_IR_INVALID"
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise SourcePlaneOwnershipIRError(code, message)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("SOURCE_PLANE_OWNERSHIP_IR_JSON_INVALID", str(exc))


def _text(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} must be trimmed text")
    return value


def _sha(value: Any, label: str, *, optional: bool = False) -> str | None:
    text = _text(value, label, optional=optional)
    if text is None:
        return None
    if len(text) != 64 or text != text.casefold() or any(char not in "0123456789abcdef" for char in text):
        _fail("SOURCE_PLANE_OWNERSHIP_IR_SHA_INVALID", f"{label} is not canonical SHA-256")
    return text


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 2**63 - 1:
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} is outside its integer range")
    return value


def _real(value: Any, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} must be finite")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or (minimum is not None and number < minimum):
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} is outside its range")
    return number


def _obj_map(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="python")
        except Exception as exc:
            _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} model_dump failed: {exc}")
        if isinstance(dumped, Mapping):
            return dict(dumped)
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} model_dump must return a Mapping")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} must be a Mapping, dataclass, or Pydantic model")


def _rows(value: Any, label: str, remaining: int, cancelled: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} must be an iterable of rows")
    try:
        iterator = iter(value)
    except TypeError:
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ROW_INVALID", f"{label} must be an iterable of rows")
    result: list[dict[str, Any]] = []
    for item in iterator:
        if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
        result.append(_obj_map(item, f"{label} row"))
        if len(result) > remaining: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", f"{label} exceeds row bound")
    return result


def _draft_map(draft: Any, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    result = {} if draft is None else _obj_map(draft, "draft")
    result.update(kwargs)
    return result


def _hash_owner_set(values: set[str]) -> str:
    return sha256(_canonical(sorted(values))).hexdigest()


_CREATE_SQL = """
PRAGMA application_id=1397772119;
PRAGMA user_version=1;
PRAGMA foreign_keys=ON;
CREATE TABLE meta(key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE source_records(ordinal INTEGER PRIMARY KEY, record_id TEXT NOT NULL, kind TEXT NOT NULL, source_offset INTEGER NOT NULL, source_end INTEGER NOT NULL, source_record_sha256 TEXT NOT NULL, logical_net TEXT, layer TEXT, UNIQUE(record_id), UNIQUE(record_id COLLATE NOCASE)) WITHOUT ROWID;
CREATE TABLE surfaces(ordinal INTEGER PRIMARY KEY, surface_id TEXT NOT NULL, artwork_net TEXT NOT NULL, layer TEXT NOT NULL, geometry_asset_name TEXT NOT NULL, geometry_asset_sha256 TEXT NOT NULL, island_manifest_sha256 TEXT NOT NULL, source_lineage_sha256 TEXT NOT NULL, component_count INTEGER NOT NULL, UNIQUE(surface_id), UNIQUE(surface_id COLLATE NOCASE)) WITHOUT ROWID;
CREATE TABLE primitives(primitive_id TEXT PRIMARY KEY NOT NULL, surface_id TEXT NOT NULL, local_ordinal INTEGER NOT NULL, raw_primitive_ordinal INTEGER NOT NULL, polarity TEXT NOT NULL, kind TEXT NOT NULL, effect_status TEXT NOT NULL, source_record_id TEXT NOT NULL, source_asset_name TEXT NOT NULL, source_asset_sha256 TEXT NOT NULL, primitive_sha256 TEXT NOT NULL, UNIQUE(primitive_id COLLATE NOCASE), UNIQUE(surface_id COLLATE NOCASE,local_ordinal), UNIQUE(raw_primitive_ordinal), FOREIGN KEY(surface_id) REFERENCES surfaces(surface_id), FOREIGN KEY(source_record_id) REFERENCES source_records(record_id));
CREATE TABLE islands(ordinal INTEGER PRIMARY KEY, island_id TEXT NOT NULL, surface_id TEXT NOT NULL, component_id TEXT NOT NULL, representative INTEGER NOT NULL, positive_witness_count INTEGER NOT NULL, UNIQUE(island_id), UNIQUE(island_id COLLATE NOCASE), FOREIGN KEY(surface_id) REFERENCES surfaces(surface_id)) WITHOUT ROWID;
CREATE TABLE primitive_island_edges(primitive_id TEXT NOT NULL, island_id TEXT NOT NULL, witness_kind TEXT NOT NULL, relation_sha256 TEXT NOT NULL, PRIMARY KEY(primitive_id,island_id,witness_kind), FOREIGN KEY(primitive_id) REFERENCES primitives(primitive_id), FOREIGN KEY(island_id) REFERENCES islands(island_id)) WITHOUT ROWID;
CREATE TABLE stackup_layers(ordinal INTEGER PRIMARY KEY, layer_name TEXT NOT NULL, layer_kind TEXT NOT NULL, raw_layer_ordinal INTEGER NOT NULL, raw_layer_sha256 TEXT NOT NULL, thickness_um REAL NOT NULL, thickness_origin TEXT NOT NULL, thickness_source_record_id TEXT NOT NULL, conductivity_s_per_m REAL, conductivity_origin TEXT, conductivity_source_record_id TEXT, material_name TEXT NOT NULL, material_origin TEXT NOT NULL, material_source_record_id TEXT NOT NULL, UNIQUE(layer_name), UNIQUE(layer_name COLLATE NOCASE), UNIQUE(raw_layer_ordinal), FOREIGN KEY(thickness_source_record_id) REFERENCES source_records(record_id), FOREIGN KEY(conductivity_source_record_id) REFERENCES source_records(record_id), FOREIGN KEY(material_source_record_id) REFERENCES source_records(record_id)) WITHOUT ROWID;
CREATE TABLE dielectric_points(ordinal INTEGER PRIMARY KEY, layer_name TEXT NOT NULL, point_ordinal INTEGER NOT NULL, raw_dielectric_ordinal INTEGER NOT NULL, raw_dielectric_sha256 TEXT NOT NULL, frequency_hz REAL NOT NULL, frequency_origin TEXT NOT NULL, frequency_source_record_id TEXT NOT NULL, epsilon_r REAL NOT NULL, epsilon_origin TEXT NOT NULL, epsilon_source_record_id TEXT NOT NULL, loss_tangent REAL NOT NULL, loss_tangent_origin TEXT NOT NULL, loss_tangent_source_record_id TEXT NOT NULL, UNIQUE(layer_name,point_ordinal), UNIQUE(layer_name COLLATE NOCASE,point_ordinal), UNIQUE(raw_dielectric_ordinal), FOREIGN KEY(layer_name) REFERENCES stackup_layers(layer_name), FOREIGN KEY(frequency_source_record_id) REFERENCES source_records(record_id), FOREIGN KEY(epsilon_source_record_id) REFERENCES source_records(record_id), FOREIGN KEY(loss_tangent_source_record_id) REFERENCES source_records(record_id)) WITHOUT ROWID;
CREATE TABLE rail_bindings(ordinal INTEGER PRIMARY KEY, rail_id TEXT NOT NULL, role TEXT NOT NULL, logical_net TEXT NOT NULL, artwork_net TEXT NOT NULL, layer TEXT NOT NULL, surface_id TEXT NOT NULL, island_id TEXT NOT NULL, pair_evidence_sha256 TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(rail_id COLLATE NOCASE,role), FOREIGN KEY(surface_id) REFERENCES surfaces(surface_id), FOREIGN KEY(island_id) REFERENCES islands(island_id)) WITHOUT ROWID;
CREATE TABLE terminal_bindings(ordinal INTEGER PRIMARY KEY, terminal_id TEXT NOT NULL, owner_kind TEXT NOT NULL, rail_id TEXT NOT NULL, branch_id TEXT NOT NULL, pin_id TEXT NOT NULL, role TEXT NOT NULL, source_node_record_id TEXT NOT NULL, via_record_required INTEGER NOT NULL, via_record_id TEXT, endpoint_node_id TEXT, island_id TEXT, component_id TEXT, layer TEXT, padstack_id TEXT, paddef_source_record_id TEXT NOT NULL, regular_source_record_id TEXT NOT NULL, raw_pad_shape_ordinal INTEGER, raw_pad_shape_sha256 TEXT, finite_vertex_id TEXT, finite_edge_id TEXT, via_owner_id TEXT, status TEXT NOT NULL, issues_json TEXT NOT NULL, UNIQUE(terminal_id COLLATE NOCASE), FOREIGN KEY(source_node_record_id) REFERENCES source_records(record_id), FOREIGN KEY(via_record_id) REFERENCES source_records(record_id), FOREIGN KEY(island_id) REFERENCES islands(island_id), FOREIGN KEY(paddef_source_record_id) REFERENCES source_records(record_id), FOREIGN KEY(regular_source_record_id) REFERENCES source_records(record_id)) WITHOUT ROWID;
CREATE TABLE retained_owner_refs(ordinal INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, namespace TEXT NOT NULL, owner_kind TEXT NOT NULL, rail_id TEXT, edge_id TEXT, island_id TEXT, state TEXT NOT NULL, UNIQUE(owner_id COLLATE NOCASE), FOREIGN KEY(island_id) REFERENCES islands(island_id)) WITHOUT ROWID;
CREATE TABLE plane_owner_scopes(ordinal INTEGER PRIMARY KEY, scope_id TEXT NOT NULL, namespace TEXT NOT NULL, compiler_owner_id TEXT NOT NULL, rail_id TEXT NOT NULL, role TEXT NOT NULL, artwork_net TEXT NOT NULL, layer TEXT NOT NULL, state TEXT NOT NULL, owner_count INTEGER NOT NULL, UNIQUE(scope_id COLLATE NOCASE), UNIQUE(compiler_owner_id COLLATE NOCASE)) WITHOUT ROWID;
CREATE TABLE replacement_ledger(ordinal INTEGER PRIMARY KEY, ledger_id TEXT NOT NULL, replaced_set_sha256 TEXT NOT NULL, retained_set_sha256 TEXT NOT NULL, intersection_count INTEGER NOT NULL, replaced_count INTEGER NOT NULL, retained_count INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(ledger_id), UNIQUE(ledger_id COLLATE NOCASE)) WITHOUT ROWID;
CREATE TABLE replacement_ledger_members(ledger_id TEXT NOT NULL, owner_id TEXT NOT NULL, action TEXT NOT NULL, PRIMARY KEY(ledger_id,owner_id), FOREIGN KEY(ledger_id) REFERENCES replacement_ledger(ledger_id)) WITHOUT ROWID;
CREATE TABLE section_ledger(section_name TEXT PRIMARY KEY NOT NULL, row_count INTEGER NOT NULL, logical_sha256 TEXT NOT NULL) WITHOUT ROWID;
"""

_CONTACT_BOUNDARY_SQL = """
CREATE TABLE contact_boundary(ordinal INTEGER PRIMARY KEY, contact_id TEXT NOT NULL, owner_kind TEXT NOT NULL, net TEXT NOT NULL, via_id TEXT NOT NULL, endpoint_node_id TEXT NOT NULL, opposite_endpoint_node_id TEXT, plane_endpoint_node_id TEXT NOT NULL, external_endpoint_node_id TEXT NOT NULL, endpoint_layer TEXT NOT NULL, island_id TEXT NOT NULL, component_id TEXT NOT NULL, padstack_id TEXT NOT NULL, rotation_degrees REAL NOT NULL, source_node_record_id TEXT NOT NULL, opposite_endpoint_node_record_id TEXT, plane_endpoint_node_record_id TEXT NOT NULL, external_endpoint_node_record_id TEXT NOT NULL, via_record_id TEXT NOT NULL, paddef_source_record_id TEXT NOT NULL, regular_source_record_id TEXT NOT NULL, raw_pad_shape_ordinal INTEGER NOT NULL, raw_pad_shape_sha256 TEXT NOT NULL, finite_vertex_id TEXT NOT NULL, finite_edge_id TEXT NOT NULL, owner_ids_json TEXT NOT NULL, status TEXT NOT NULL, issues_json TEXT NOT NULL, UNIQUE(contact_id COLLATE NOCASE), FOREIGN KEY(source_node_record_id) REFERENCES source_records(record_id), FOREIGN KEY(opposite_endpoint_node_record_id) REFERENCES source_records(record_id), FOREIGN KEY(plane_endpoint_node_record_id) REFERENCES source_records(record_id), FOREIGN KEY(external_endpoint_node_record_id) REFERENCES source_records(record_id), FOREIGN KEY(via_record_id) REFERENCES source_records(record_id), FOREIGN KEY(paddef_source_record_id) REFERENCES source_records(record_id), FOREIGN KEY(regular_source_record_id) REFERENCES source_records(record_id), FOREIGN KEY(island_id) REFERENCES islands(island_id)) WITHOUT ROWID;
"""
_CREATE_SQL_V2 = _CREATE_SQL.replace('CREATE TABLE section_ledger', _CONTACT_BOUNDARY_SQL + 'CREATE TABLE section_ledger')

_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "source_records": ("ordinal", "record_id", "kind", "source_offset", "source_end", "source_record_sha256", "logical_net", "layer"),
    "surfaces": ("ordinal", "surface_id", "artwork_net", "layer", "geometry_asset_name", "geometry_asset_sha256", "island_manifest_sha256", "source_lineage_sha256", "component_count"),
    "primitives": ("primitive_id", "surface_id", "local_ordinal", "raw_primitive_ordinal", "polarity", "kind", "effect_status", "source_record_id", "source_asset_name", "source_asset_sha256", "primitive_sha256"),
    "islands": ("ordinal", "island_id", "surface_id", "component_id", "representative", "positive_witness_count"),
    "primitive_island_edges": ("primitive_id", "island_id", "witness_kind", "relation_sha256"),
    "stackup_layers": ("ordinal", "layer_name", "layer_kind", "raw_layer_ordinal", "raw_layer_sha256", "thickness_um", "thickness_origin", "thickness_source_record_id", "conductivity_s_per_m", "conductivity_origin", "conductivity_source_record_id", "material_name", "material_origin", "material_source_record_id"),
    "dielectric_points": ("ordinal", "layer_name", "point_ordinal", "raw_dielectric_ordinal", "raw_dielectric_sha256", "frequency_hz", "frequency_origin", "frequency_source_record_id", "epsilon_r", "epsilon_origin", "epsilon_source_record_id", "loss_tangent", "loss_tangent_origin", "loss_tangent_source_record_id"),
    "rail_bindings": ("ordinal", "rail_id", "role", "logical_net", "artwork_net", "layer", "surface_id", "island_id", "pair_evidence_sha256", "state"),
    "terminal_bindings": ("ordinal", "terminal_id", "owner_kind", "rail_id", "branch_id", "pin_id", "role", "source_node_record_id", "via_record_required", "via_record_id", "endpoint_node_id", "island_id", "component_id", "layer", "padstack_id", "paddef_source_record_id", "regular_source_record_id", "raw_pad_shape_ordinal", "raw_pad_shape_sha256", "finite_vertex_id", "finite_edge_id", "via_owner_id", "status", "issues_json"),
    "retained_owner_refs": ("ordinal", "owner_id", "namespace", "owner_kind", "rail_id", "edge_id", "island_id", "state"),
    "plane_owner_scopes": ("ordinal", "scope_id", "namespace", "compiler_owner_id", "rail_id", "role", "artwork_net", "layer", "state", "owner_count"),
    "replacement_ledger": ("ordinal", "ledger_id", "replaced_set_sha256", "retained_set_sha256", "intersection_count", "replaced_count", "retained_count", "status"),
    "replacement_ledger_members": ("ledger_id", "owner_id", "action"),
}
_COLUMNS_V2: Final[dict[str, tuple[str, ...]]] = {
    **_COLUMNS,
    "contact_boundary": ("ordinal", "contact_id", "owner_kind", "net", "via_id", "endpoint_node_id", "opposite_endpoint_node_id", "plane_endpoint_node_id", "external_endpoint_node_id", "endpoint_layer", "island_id", "component_id", "padstack_id", "rotation_degrees", "source_node_record_id", "opposite_endpoint_node_record_id", "plane_endpoint_node_record_id", "external_endpoint_node_record_id", "via_record_id", "paddef_source_record_id", "regular_source_record_id", "raw_pad_shape_ordinal", "raw_pad_shape_sha256", "finite_vertex_id", "finite_edge_id", "owner_ids_json", "status", "issues_json"),
}


def _normalize(draft: Any, kwargs: Mapping[str, Any], cancelled: Any) -> dict[str, Any]:
    data = _draft_map(draft, kwargs); result: dict[str, Any] = {}
    result["app_version"] = _text(data.get("app_version"), "app_version"); result["target_rail_id"] = _text(data.get("target_rail_id"), "target_rail_id")
    result["source_sha256"] = _sha(data.get("source_sha256"), "source_sha256"); result["source_size_bytes"] = _integer(data.get("source_size_bytes"), "source_size_bytes", 1)
    for key in ("project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256"): result[key] = _sha(data.get(key), key)
    remaining = MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS
    sections = _V2_SECTIONS if "contact_boundary" in data else _SECTIONS
    for section in sections:
        result[section] = _rows(data.get(section), section, remaining, cancelled); remaining -= len(result[section])
    return result


def _validate_rows(data: dict[str, Any]) -> None:
    sections = _V2_SECTIONS if "contact_boundary" in data else _SECTIONS
    total = 0
    for section in sections:
        rows = data[section]; total += len(rows)
        if total > MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "row count exceeds bound")
        if section not in {"primitive_island_edges", "replacement_ledger_members"}:
            for index, row in enumerate(rows):
                if row.get("ordinal", index) != index: _fail("SOURCE_PLANE_OWNERSHIP_IR_ORDER_INVALID", f"{section} ordinals are not contiguous")
                _integer(row.get("ordinal", index), f"{section}.ordinal")
    records: dict[str, Mapping[str, Any]] = {}; previous_end = 0
    for row in sorted(data["source_records"], key=lambda value: _integer(value.get("source_offset"), "source_offset")):
        rid = _text(row.get("record_id"), "record_id"); assert rid is not None
        start = _integer(row.get("source_offset"), "source_offset"); end = _integer(row.get("source_end"), "source_end")
        if rid.casefold() in records: _fail("SOURCE_PLANE_OWNERSHIP_IR_ID_COLLISION", "source record IDs collide")
        if end <= start or end > data["source_size_bytes"] or start < previous_end: _fail("SOURCE_PLANE_OWNERSHIP_IR_SOURCE_SPAN_INVALID", "source spans overlap or exceed source")
        previous_end = end; records[rid.casefold()] = row; _text(row.get("kind"), "source record kind"); _sha(row.get("source_record_sha256"), "source_record_sha256"); _text(row.get("logical_net"), "logical_net", optional=True); _text(row.get("layer"), "layer", optional=True)
    surfaces: dict[str, Mapping[str, Any]] = {}
    for row in data["surfaces"]:
        sid = _text(row.get("surface_id"), "surface_id"); lineage = _sha(row.get("source_lineage_sha256"), "source_lineage_sha256"); assert sid and lineage
        if sid.casefold() in surfaces: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "surface identity is invalid")
        surfaces[sid.casefold()] = row
        for key in ("artwork_net", "layer", "geometry_asset_name"): _text(row.get(key), f"surface.{key}")
        for key in ("geometry_asset_sha256", "island_manifest_sha256"): _sha(row.get(key), f"surface.{key}")
        _integer(row.get("component_count"), "surface.component_count")
    primitive_ids: dict[str, Mapping[str, Any]] = {}; primitive_keys: set[tuple[str, int]] = set(); raw_ordinals: set[int] = set(); per_surface: dict[str, list[int]] = {}
    for row in data["primitives"]:
        pid = _text(row.get("primitive_id"), "primitive_id"); sid = _text(row.get("surface_id"), "primitive.surface_id"); source = _text(row.get("source_record_id"), "source_record_id"); assert pid and sid and source
        local = _integer(row.get("local_ordinal"), "local_ordinal"); raw = _integer(row.get("raw_primitive_ordinal"), "raw_primitive_ordinal")
        if pid.casefold() in primitive_ids or (sid.casefold(), local) in primitive_keys or raw in raw_ordinals or sid.casefold() not in surfaces or source.casefold() not in records or source != records[source.casefold()]["record_id"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_PRIMITIVE_INVALID", "primitive identity/order/source relation is invalid")
        primitive_ids[pid.casefold()] = row; primitive_keys.add((sid.casefold(), local)); raw_ordinals.add(raw); per_surface.setdefault(sid.casefold(), []).append(local)
        if row.get("polarity") not in {"+", "-"} or row.get("kind") not in {"Polygon", "PolygonTrace", "Circle", "Box"} or row.get("effect_status") not in {"retained", "no_survivor"}:
            _fail("SOURCE_PLANE_OWNERSHIP_IR_PRIMITIVE_INVALID", "primitive polarity/kind/effect enum is invalid")
        if str(records[source.casefold()].get("kind", "")).casefold() != "shape": _fail("SOURCE_PLANE_OWNERSHIP_IR_PRIMITIVE_INVALID", "primitive source is not Shape")
        for key in ("source_asset_name",): _text(row.get(key), f"primitive.{key}")
        _sha(row.get("source_asset_sha256"), "source_asset_sha256"); _sha(row.get("primitive_sha256"), "primitive_sha256")
    for values in per_surface.values():
        if sorted(values) != list(range(len(values))): _fail("SOURCE_PLANE_OWNERSHIP_IR_PRIMITIVE_INVALID", "primitive local ordinals are not exact-once")
    lineage: dict[str, set[str]] = {}
    for row in primitive_ids.values():
        lineage.setdefault(str(row["surface_id"]).casefold(), set()).add(str(row["source_record_id"]))
    for sid, surface in surfaces.items():
        expected_lineage = sha256(_canonical(sorted(lineage.get(sid, set())))).hexdigest()
        if surface["source_lineage_sha256"] != expected_lineage:
            _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "surface source lineage differs")
    islands: dict[str, Mapping[str, Any]] = {}; components: dict[str, set[str]] = {}
    for row in data["islands"]:
        iid = _text(row.get("island_id"), "island_id"); sid = _text(row.get("surface_id"), "island.surface_id"); component = _text(row.get("component_id"), "component_id"); assert iid and sid and component
        if iid.casefold() in islands or sid.casefold() not in surfaces: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "island identity/surface relation is invalid")
        islands[iid.casefold()] = row; components.setdefault(sid.casefold(), set()).add(component.casefold()); representative = _integer(row.get("representative"), "island.representative")
        if representative not in (0, 1) or _integer(row.get("positive_witness_count"), "positive_witness_count") < 1: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "island representative/witness is invalid")
    for sid, surface in surfaces.items():
        if _integer(surface.get("component_count"), "surface.component_count") != len(components.get(sid, set())): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "surface component count differs")
    positive: dict[str, int] = {}; edge_keys: set[tuple[str, str, str]] = set()
    for row in data["primitive_island_edges"]:
        pid = _text(row.get("primitive_id"), "edge.primitive_id"); iid = _text(row.get("island_id"), "edge.island_id"); witness = _text(row.get("witness_kind"), "witness_kind"); assert pid and iid and witness
        if pid.casefold() not in primitive_ids or iid.casefold() not in islands or witness not in {"positive_area_witness", "negative_boundary_witness"}: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "primitive/island edge is invalid")
        if str(primitive_ids[pid.casefold()]["surface_id"]).casefold() != str(islands[iid.casefold()]["surface_id"]).casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "edge crosses surfaces")
        key = (pid.casefold(), iid.casefold(), witness)
        if key in edge_keys: _fail("SOURCE_PLANE_OWNERSHIP_IR_ID_COLLISION", "primitive/island edge duplicates")
        edge_keys.add(key); _sha(row.get("relation_sha256"), "relation_sha256")
        if witness == "positive_area_witness": positive[iid.casefold()] = positive.get(iid.casefold(), 0) + 1
        primitive = primitive_ids[pid.casefold()]
        expected_witness = "positive_area_witness" if primitive["polarity"] == "+" else "negative_boundary_witness"
        if primitive["effect_status"] == "retained" and witness != expected_witness: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "retained primitive witness disagrees with polarity")
    for iid, row in islands.items():
        if positive.get(iid, 0) != _integer(row.get("positive_witness_count"), "positive_witness_count"): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "positive witness count differs")
    edge_by_primitive = {key[0] for key in edge_keys}
    shape_use: dict[str, int] = {}
    for primitive in primitive_ids.values():
        source = str(primitive["source_record_id"]).casefold(); shape_use[source] = shape_use.get(source, 0) + 1
        if primitive["effect_status"] == "retained" and primitive["primitive_id"].casefold() not in edge_by_primitive: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "retained primitive lacks witness")
        if primitive["effect_status"] == "no_survivor" and primitive["primitive_id"].casefold() in edge_by_primitive: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "no_survivor primitive has witness")
    for source, count in shape_use.items():
        if count != 1: _fail("SOURCE_PLANE_OWNERSHIP_IR_PRIMITIVE_INVALID", "Shape source record is not used exactly once")
    shape_records = {rid for rid, record in records.items() if str(record.get("kind", "")).casefold() == "shape"}
    if shape_records != set(shape_use): _fail("SOURCE_PLANE_OWNERSHIP_IR_PRIMITIVE_INVALID", "every Shape source record must be used exactly once")
    layers: dict[str, Mapping[str, Any]] = {}; raw_layers: set[int] = set(); raw_dielectrics: set[int] = set()
    for row in data["stackup_layers"]:
        name = _text(row.get("layer_name"), "layer_name"); assert name
        raw_layer = _integer(row.get("raw_layer_ordinal"), "raw_layer_ordinal")
        refs = {"thickness": (_text(row.get("thickness_source_record_id"), "thickness_source_record_id"), row.get("thickness_origin")), "conductivity": (_text(row.get("conductivity_source_record_id"), "conductivity_source_record_id", optional=True), row.get("conductivity_origin")), "material": (_text(row.get("material_source_record_id"), "material_source_record_id"), row.get("material_origin"))}
        if name.casefold() in layers or raw_layer in raw_layers: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "stackup layer identity is invalid")
        raw_layers.add(raw_layer); layers[name.casefold()] = row; layer_kind = _text(row.get("layer_kind"), "layer_kind"); _sha(row.get("raw_layer_sha256"), "raw_layer_sha256"); thickness = _real(row.get("thickness_um"), "thickness_um", 0); _text(row.get("thickness_origin"), "thickness_origin"); conductivity = _real(row.get("conductivity_s_per_m"), "conductivity_s_per_m", 0) if row.get("conductivity_s_per_m") is not None else None; _text(row.get("conductivity_origin"), "conductivity_origin", optional=True); _text(row.get("material_name"), "material_name"); _text(row.get("material_origin"), "material_origin")
        if layer_kind not in {"conductor", "dielectric"} or thickness <= 0: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "layer physical semantics are invalid")
        if layer_kind == "conductor" and (conductivity is None or conductivity <= 0 or row.get("conductivity_origin") == "unavailable"): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "conductor requires conductivity")
        if layer_kind == "dielectric" and (conductivity is not None or row.get("conductivity_origin") != "unavailable" or refs["conductivity"][0] is not None): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "dielectric conductivity must be unavailable")
        if row.get("thickness_origin") == "unavailable" or row.get("material_origin") == "unavailable": _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "numeric/material origin cannot be unavailable")
        origins = {row.get("thickness_origin"), row.get("conductivity_origin"), row.get("material_origin")};
        if not origins <= {"source", "layer_override", "material_model", "unavailable", None}: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "material origin vocabulary is invalid")
        if row.get("conductivity_s_per_m") is not None and row.get("conductivity_origin") is None: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "conductivity origin is missing")
        for field, (ref, origin) in refs.items():
            if field == "conductivity" and conductivity is None: continue
            expected_kind = "material" if origin == "material_model" else "layer"
            if ref is None or ref.casefold() not in records or ref != records[ref.casefold()]["record_id"] or origin not in {"source", "layer_override", "material_model"} or str(records[ref.casefold()].get("kind", "")).casefold() != expected_kind: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", f"{field} source/origin is invalid")
    points_by_layer: dict[str, list[int]] = {}
    for row in data["dielectric_points"]:
        layer = _text(row.get("layer_name"), "dielectric.layer_name"); assert layer
        raw_dielectric = _integer(row.get("raw_dielectric_ordinal"), "raw_dielectric_ordinal")
        if layer.casefold() not in layers or layers[layer.casefold()]["layer_kind"] != "dielectric" or raw_dielectric in raw_dielectrics: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "dielectric layer identity is invalid")
        point = _integer(row.get("point_ordinal"), "point_ordinal"); points_by_layer.setdefault(layer.casefold(), []).append(point); raw_dielectrics.add(raw_dielectric); _sha(row.get("raw_dielectric_sha256"), "raw_dielectric_sha256"); _real(row.get("frequency_hz"), "frequency_hz", 0); _real(row.get("epsilon_r"), "epsilon_r", 0); _real(row.get("loss_tangent"), "loss_tangent", 0)
        if float(row.get("frequency_hz")) <= 0 or float(row.get("epsilon_r")) <= 0 or float(row.get("loss_tangent")) < 0: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "dielectric physical values are invalid")
        for key, ref_key in (("frequency_origin", "frequency_source_record_id"), ("epsilon_origin", "epsilon_source_record_id"), ("loss_tangent_origin", "loss_tangent_source_record_id")):
            origin = row.get(key)
            if origin not in {"source", "layer_override", "material_model", "unavailable"}: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "dielectric origin vocabulary is invalid")
            ref = _text(row.get(ref_key), ref_key, optional=True)
            if origin == "unavailable" or ref is None or ref.casefold() not in records or ref != records[ref.casefold()]["record_id"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "dielectric source reference is invalid")
            expected_kind = "material" if origin == "material_model" else "layer"
            if origin not in {"source", "layer_override", "material_model"} or str(records[ref.casefold()].get("kind", "")).casefold() != expected_kind: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "dielectric origin/source kind is invalid")
    for points in points_by_layer.values():
        if sorted(points) != list(range(len(points))): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "dielectric point ordinals are not contiguous")
    rail_keys: set[tuple[str, str]] = set(); rail_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in data["rail_bindings"]:
        rail = _text(row.get("rail_id"), "rail_id"); role = _text(row.get("role"), "rail.role"); sid = _text(row.get("surface_id"), "rail.surface_id"); iid = _text(row.get("island_id"), "rail.island_id"); assert rail and role and sid and iid
        if role not in {"power", "ground"} or (rail.casefold(), role.casefold()) in rail_keys or sid.casefold() not in surfaces or iid.casefold() not in islands or str(islands[iid.casefold()]["surface_id"]).casefold() != sid.casefold() or str(row.get("layer", "")).casefold() != str(surfaces[sid.casefold()]["layer"]).casefold() or row.get("artwork_net") != surfaces[sid.casefold()]["artwork_net"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "rail binding relation is invalid")
        rail_keys.add((rail.casefold(), role.casefold())); rail_map[(rail.casefold(), role.casefold())] = row
        for key in ("logical_net", "artwork_net", "layer", "state"): _text(row.get(key), f"rail.{key}")
        if row.get("state") != "source_bound": _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "rail state must be source_bound")
        _sha(row.get("pair_evidence_sha256"), "pair_evidence_sha256")
    target = str(data["target_rail_id"]).casefold()
    if {(rail, role) for rail, role in rail_keys if rail == target} != {(target, "power"), (target, "ground")} or any(rail != target for rail, _ in rail_keys): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "target rail must have exactly power and ground bindings")
    owner_ids: set[str] = set(); retained_namespaces: set[str] = set(); retained_lookup: dict[str, Mapping[str, Any]] = {}
    for row in data["retained_owner_refs"]:
        owner = _text(row.get("owner_id"), "owner_id"); namespace = _text(row.get("namespace"), "namespace"); assert owner and namespace
        if owner.casefold() in owner_ids: _fail("SOURCE_PLANE_OWNERSHIP_IR_OWNER_COLLISION", "owner IDs collide")
        owner_ids.add(owner.casefold()); retained_namespaces.add(namespace.casefold()); _text(row.get("owner_kind"), "owner_kind")
        if _text(row.get("state"), "owner.state") != "retained": _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_FORBIDDEN", "retained owner state is not retained")
        for key in ("rail_id", "edge_id", "island_id"): _text(row.get(key), key, optional=True)
        if row.get("island_id") is not None and str(row["island_id"]).casefold() not in islands: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "owner island is unknown")
        if row.get("rail_id") is not None and not any(key[0] == str(row["rail_id"]).casefold() for key in rail_map): _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "owner rail is unknown")
        retained_lookup[owner.casefold()] = row
    plane_namespaces: set[str] = set()
    for row in data["plane_owner_scopes"]:
        namespace = _text(row.get("namespace"), "scope.namespace"); owner = _text(row.get("compiler_owner_id"), "compiler_owner_id"); assert namespace and owner
        if namespace.casefold() in retained_namespaces or namespace.casefold() in plane_namespaces or owner.casefold() in owner_ids: _fail("SOURCE_PLANE_OWNERSHIP_IR_OWNER_COLLISION", "plane/retained owner namespace or ID collides")
        owner_ids.add(owner.casefold()); plane_namespaces.add(namespace.casefold()); scope_rail = _text(row.get("rail_id"), "scope.rail_id"); scope_role = _text(row.get("role"), "scope.role"); _text(row.get("scope_id"), "scope_id"); _text(row.get("artwork_net"), "scope.artwork_net"); _text(row.get("layer"), "scope.layer")
        binding = rail_map.get((scope_rail.casefold(), scope_role.casefold())) if scope_rail and scope_role else None
        if binding is None or scope_role not in {"power", "ground"} or row.get("artwork_net") != binding["artwork_net"] or row.get("layer") != binding["layer"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "plane scope does not match rail binding")
        if scope_rail.casefold() != target: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "plane scope is not target rail")
        if _text(row.get("state"), "scope.state") != "declared_unconsumed": _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_FORBIDDEN", "plane owner scope is consumed")
        if _integer(row.get("owner_count"), "scope.owner_count", 1) != 1: _fail("SOURCE_PLANE_OWNERSHIP_IR_RELATION_INVALID", "plane scope owner_count must be one")
    terminal_ids: set[str] = set(); terminal_keys: set[tuple[str, str, str, str]] = set(); complete_roles: set[str] = set()
    for row in data["terminal_bindings"]:
        tid = _text(row.get("terminal_id"), "terminal_id"); rail = _text(row.get("rail_id"), "terminal.rail_id"); source = _text(row.get("source_node_record_id"), "source_node_record_id"); branch = _text(row.get("branch_id"), "branch_id"); pin = _text(row.get("pin_id"), "pin_id"); role = _text(row.get("role"), "terminal.role"); assert tid and rail and source and branch and pin and role
        terminal_key = (rail.casefold(), branch.casefold(), pin.casefold(), role.casefold())
        if tid.casefold() in terminal_ids or terminal_key in terminal_keys or (rail.casefold(), role.casefold()) not in rail_keys or (rail.casefold(), role.casefold()) not in rail_map or source.casefold() not in records or source != records[source.casefold()]["record_id"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal source/rail chain is invalid")
        if str(records[source.casefold()].get("kind", "")).casefold() != "node": _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal source record is not Node kind")
        terminal_ids.add(tid.casefold()); terminal_keys.add(terminal_key); _text(row.get("owner_kind"), "terminal.owner_kind"); required_via = _integer(row.get("via_record_required"), "via_record_required")
        if required_via not in (0, 1): _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "via_record_required is invalid")
        via = row.get("via_record_id")
        if required_via != 1: _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "complete terminal chain requires via record")
        if not via or str(via).casefold() not in records or str(via) != records[str(via).casefold()]["record_id"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "required via record is missing")
        if str(records[str(via).casefold()].get("kind", "")).casefold() != "via": _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "via record is not Via kind")
        if via is not None and (str(via).casefold() not in records or str(via) != records[str(via).casefold()]["record_id"]): _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal via record is unknown")
        paddef = _text(row.get("paddef_source_record_id"), "paddef_source_record_id", optional=True); regular = _text(row.get("regular_source_record_id"), "regular_source_record_id", optional=True)
        for key in ("via_record_id", "endpoint_node_id", "island_id", "component_id", "layer", "padstack_id", "finite_vertex_id", "finite_edge_id", "via_owner_id"): _text(row.get(key), key, optional=True)
        if row.get("island_id") is not None and str(row["island_id"]).casefold() not in islands: _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal island is unknown")
        binding = rail_map[(rail.casefold(), role.casefold())]
        if row.get("island_id") is not None and str(row["island_id"]).casefold() != str(binding["island_id"]).casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal island does not match rail binding island")
        if row.get("island_id") is not None and str(islands[str(row["island_id"]).casefold()]["surface_id"]).casefold() != str(binding["surface_id"]).casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal island does not match rail surface")
        if row.get("island_id") is not None and row.get("component_id") is not None and str(islands[str(row["island_id"]).casefold()]["component_id"]).casefold() != str(row["component_id"]).casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal component differs from island")
        if row.get("island_id") is not None and row.get("layer") is not None and str(row["layer"]).casefold() != str(surfaces[str(islands[str(row["island_id"]).casefold()]["surface_id"]).casefold()]["layer"]).casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal layer differs from island surface")
        status = _text(row.get("status"), "terminal.status"); assert status
        if status != "complete": _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "phase-1 terminal status must be complete")
        owner_ref = retained_lookup.get(str(row.get("via_owner_id", "")).casefold())
        if status == "complete" and (owner_ref is None or str(owner_ref.get("edge_id", "")).casefold() != str(row.get("finite_edge_id", "")).casefold() or str(owner_ref.get("rail_id", "")).casefold() != rail.casefold() or str(owner_ref.get("island_id", "")).casefold() != str(row.get("island_id", "")).casefold()): _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "terminal via owner does not match retained owner")
        try:
            issue = row.get("issues_json"); parsed = json.loads(issue); 
            if _canonical(parsed).decode("utf-8") != issue: raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError): _fail("SOURCE_PLANE_OWNERSHIP_IR_JSON_INVALID", "issues_json is not canonical JSON")
        if status == "complete":
            required = ("endpoint_node_id", "island_id", "component_id", "layer", "padstack_id", "finite_vertex_id", "finite_edge_id", "via_owner_id")
            if any(not row.get(key) for key in required) or row.get("raw_pad_shape_sha256") is None or row.get("raw_pad_shape_ordinal") is None or issue != "[]" or not paddef or not regular or paddef.casefold() not in records or regular.casefold() not in records or paddef != records[paddef.casefold()]["record_id"] or regular != records[regular.casefold()]["record_id"] or str(records[paddef.casefold()].get("kind", "")).casefold() != "paddef" or str(records[regular.casefold()].get("kind", "")).casefold() != "regular": _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "complete terminal chain is missing pad source provenance")
            complete_roles.add(role.casefold())
        elif not isinstance(parsed, list) or parsed != []: _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "complete terminal issues_json must be empty")
        if row.get("raw_pad_shape_ordinal") is not None: _integer(row.get("raw_pad_shape_ordinal"), "raw_pad_shape_ordinal")
        _sha(row.get("raw_pad_shape_sha256"), "raw_pad_shape_sha256", optional=True)
    if complete_roles != {"power", "ground"}: _fail("SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE", "target rail terminal chain must contain complete power and ground")
    ledgers: dict[str, Mapping[str, Any]] = {}; memberships: dict[str, list[Mapping[str, Any]]] = {}
    for row in data["replacement_ledger"]:
        lid = _text(row.get("ledger_id"), "ledger_id"); assert lid is not None
        if lid.casefold() in ledgers: _fail("SOURCE_PLANE_OWNERSHIP_IR_ID_COLLISION", "ledger IDs collide")
        if _text(row.get("status"), "replacement status") != "prerequisite_only": _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_FORBIDDEN", "replacement_ready is forbidden")
        for key in ("replaced_set_sha256", "retained_set_sha256"): _sha(row.get(key), key)
        for key in ("intersection_count", "replaced_count", "retained_count"): _integer(row.get(key), key)
        ledgers[lid.casefold()] = row
    member_keys: set[tuple[str, str]] = set(); owner_memberships: dict[str, tuple[str, str]] = {}
    for row in data["replacement_ledger_members"]:
        lid = _text(row.get("ledger_id"), "member.ledger_id"); owner = _text(row.get("owner_id"), "member.owner_id"); action = _text(row.get("action"), "member.action"); assert lid and owner and action
        if (lid.casefold(), owner.casefold()) in member_keys or lid.casefold() not in ledgers or owner.casefold() not in owner_ids or action not in {"replaced", "retained"} or owner.casefold() in owner_memberships: _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_INVALID", "replacement membership is invalid")
        if owner.casefold() in retained_lookup and action != "retained": _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_INVALID", "retained owner action must be retained")
        if owner.casefold() not in retained_lookup and action != "replaced": _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_INVALID", "plane owner action must be replaced")
        member_keys.add((lid.casefold(), owner.casefold())); memberships.setdefault(lid.casefold(), []).append(row)
        owner_memberships[owner.casefold()] = (lid.casefold(), action)
    if set(owner_memberships) != owner_ids: _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_INVALID", "every owner must occur in exactly one ledger")
    for lid, header in ledgers.items():
        rows = memberships.get(lid, []); replaced = {str(row["owner_id"]).casefold() for row in rows if row["action"] == "replaced"}; retained = {str(row["owner_id"]).casefold() for row in rows if row["action"] == "retained"}
        if replaced & retained or _integer(header["intersection_count"], "intersection_count") != 0 or _integer(header["replaced_count"], "replaced_count") != len(replaced) or _integer(header["retained_count"], "retained_count") != len(retained) or header["replaced_set_sha256"] != _hash_owner_set(replaced) or header["retained_set_sha256"] != _hash_owner_set(retained): _fail("SOURCE_PLANE_OWNERSHIP_IR_REPLACEMENT_INVALID", "replacement ledger counts/hash/intersection differ")
    if "contact_boundary" in data:
        contact_ids: set[str] = set()
        expanded_contact_owners: set[str] = set()
        seen_edge_ids: set[str] = set()
        seen_contact_owner_ids: set[str] = set()
        for row in data["contact_boundary"]:
            cid = _text(row.get("contact_id"), "contact.contact_id"); net = _text(row.get("net"), "contact.net"); via = _text(row.get("via_id"), "contact.via_id"); endpoint = _text(row.get("endpoint_node_id"), "contact.endpoint_node_id"); layer = _text(row.get("endpoint_layer"), "contact.endpoint_layer"); island = _text(row.get("island_id"), "contact.island_id"); component = _text(row.get("component_id"), "contact.component_id"); padstack = _text(row.get("padstack_id"), "contact.padstack_id"); assert cid and net and via and endpoint and layer and island and component and padstack
            if row.get("ordinal", len(contact_ids)) != len(contact_ids): _fail("SOURCE_PLANE_OWNERSHIP_IR_ORDER_INVALID", "contact ordinals are not contiguous")
            if cid.casefold() in contact_ids or island.casefold() not in islands or str(islands[island.casefold()]["component_id"]).casefold() != component.casefold() or str(surfaces[str(islands[island.casefold()]["surface_id"]).casefold()]["layer"]).casefold() != layer.casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact island/component/layer relation is invalid")
            contact_ids.add(cid.casefold()); owner_kind = _text(row.get("owner_kind"), "contact.owner_kind"); opposite_endpoint = _text(row.get("opposite_endpoint_node_id"), "contact.opposite_endpoint_node_id"); plane_endpoint = _text(row.get("plane_endpoint_node_id"), "contact.plane_endpoint_node_id"); external_endpoint = _text(row.get("external_endpoint_node_id"), "contact.external_endpoint_node_id"); rotation = _real(row.get("rotation_degrees"), "contact.rotation_degrees")
            if owner_kind not in {"device", "decap", "other"}: _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact owner kind is invalid")
            if endpoint.casefold() != plane_endpoint.casefold() or opposite_endpoint.casefold() != external_endpoint.casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact endpoint aliases differ")
            if not -180 <= rotation < 180: _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact rotation is outside normalized range")
            for key in ("source_node_record_id", "plane_endpoint_node_record_id", "external_endpoint_node_record_id", "opposite_endpoint_node_record_id", "via_record_id", "paddef_source_record_id", "regular_source_record_id"):
                ref = _text(row.get(key), f"contact.{key}")
                assert ref
                if ref.casefold() not in records or ref != records[ref.casefold()]["record_id"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact source reference is unknown")
            if any(str(records[str(row[key]).casefold()].get("kind", "")).casefold() != expected for key, expected in (("source_node_record_id", "node"), ("plane_endpoint_node_record_id", "node"), ("external_endpoint_node_record_id", "node"), ("via_record_id", "via"), ("paddef_source_record_id", "paddef"), ("regular_source_record_id", "regular"))): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact source kind differs")
            if row.get("opposite_endpoint_node_record_id") is not None and str(records[str(row["opposite_endpoint_node_record_id"]).casefold()].get("kind", "")).casefold() != "node": _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "opposite endpoint source kind differs")
            if str(row.get("source_node_record_id", "")).casefold() != str(row.get("plane_endpoint_node_record_id", "")).casefold(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "plane source reference differs")
            source_record = records[str(row["source_node_record_id"]).casefold()]; plane_record = records[str(row["plane_endpoint_node_record_id"]).casefold()]; external_record = records[str(row["external_endpoint_node_record_id"]).casefold()]; opposite_record = records[str(row["opposite_endpoint_node_record_id"]).casefold()]; via_record = records[str(row["via_record_id"]).casefold()]; paddef_record = records[str(row["paddef_source_record_id"]).casefold()]; regular_record = records[str(row["regular_source_record_id"]).casefold()]
            expected_plane = f"node:{plane_endpoint}:{net}"; expected_external = f"node:{external_endpoint}:{net}"; expected_via = f"via:{via}:{net}"
            if any(str(row[key]).casefold() != expected.casefold() for key, expected in (("source_node_record_id", expected_plane), ("plane_endpoint_node_record_id", expected_plane), ("external_endpoint_node_record_id", expected_external), ("opposite_endpoint_node_record_id", expected_external), ("via_record_id", expected_via))) or not str(row["paddef_source_record_id"]).casefold().startswith(f"paddef:{padstack}:{layer}:".casefold()) or not str(row["regular_source_record_id"]).casefold().startswith(f"regular:{padstack}:{layer}:".casefold()): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact source identity differs")
            if any(str(record.get("logical_net", "")).casefold() != net.casefold() for record in (source_record, plane_record, external_record, opposite_record, via_record)) or any(str(record.get("layer", "")).casefold() != layer.casefold() for record in (plane_record, paddef_record, regular_record)): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact source net/layer differs")
            _integer(row.get("raw_pad_shape_ordinal"), "contact.raw_pad_shape_ordinal"); _sha(row.get("raw_pad_shape_sha256"), "contact.raw_pad_shape_sha256"); _text(row.get("finite_vertex_id"), "contact.finite_vertex_id"); edge = _text(row.get("finite_edge_id"), "contact.finite_edge_id"); assert edge
            edge_key = edge.casefold()
            if edge_key in seen_edge_ids: _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "finite edge occurs in multiple contacts")
            seen_edge_ids.add(edge_key)
            try:
                owners = json.loads(row.get("owner_ids_json"));
                if not isinstance(owners, list) or not owners or any(not isinstance(owner, str) or not owner.strip() for owner in owners) or _canonical(owners).decode("utf-8") != row.get("owner_ids_json"): raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact owner set is not canonical JSON")
            owner_set = {owner.casefold() for owner in owners}
            edge_owner_set = {owner for owner, ref in retained_lookup.items() if str(ref.get("edge_id", "")).casefold() == edge.casefold()}
            if len(owner_set) != len(owners) or owner_set != edge_owner_set or not owner_set <= owner_ids or f"via:{via}".casefold() not in owner_set: _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact owner set is unknown or differs from finite edge")
            if seen_contact_owner_ids & owner_set: _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact owner occurs more than once")
            seen_contact_owner_ids.update(owner_set)
            expanded_contact_owners.update(owner_set)
            status = _text(row.get("status"), "contact.status"); issue = row.get("issues_json")
            if status != "complete": _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact status must be complete")
            try:
                parsed = json.loads(issue)
                if _canonical(parsed).decode("utf-8") != issue or parsed != []: raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError): _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact issues_json must be []")
        finite_owner_set = {owner for owner, ref in retained_lookup.items() if str(ref.get("namespace", "")).casefold() == "finite-via"}
        if expanded_contact_owners != finite_owner_set: _fail("SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID", "contact owner coverage differs from retained finite-via owners")


def _section_digest(connection: sqlite3.Connection, section: str, cancelled: Any = None, *, columns: Mapping[str, tuple[str, ...]] | None = None) -> tuple[int, str]:
    digest = sha256(); count = 0; callback = cancelled or (lambda: False); selected_columns = (columns or _COLUMNS)[section]; order = ",".join(selected_columns)
    for row in connection.execute(f"SELECT {order} FROM {section} ORDER BY {order}"):
        if callback(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
        digest.update(_canonical(list(row))); digest.update(b"\n"); count += 1
    return count, digest.hexdigest()


_META_KEYS = ("storage_schema", "payload_schema", "compiler_id", "app_version", "source_sha256", "source_size_bytes", "target_rail_id", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256", "logical_rows_sha256")

def _binding_map(data: Mapping[str, Any], logical: str | None = None, *, v2: bool = False) -> dict[str, Any]:
    result = {"storage_schema": SOURCE_PLANE_OWNERSHIP_IR_V2_SCHEMA if v2 else SOURCE_PLANE_OWNERSHIP_IR_SCHEMA, "payload_schema": SOURCE_PLANE_OWNERSHIP_IR_V2_PAYLOAD_SCHEMA if v2 else SOURCE_PLANE_OWNERSHIP_IR_PAYLOAD_SCHEMA, "compiler_id": SOURCE_PLANE_OWNERSHIP_IR_V2_COMPILER_ID if v2 else SOURCE_PLANE_OWNERSHIP_IR_COMPILER_ID}
    result.update({key: data[key] for key in ("app_version", "source_sha256", "source_size_bytes", "target_rail_id", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")})
    if logical is not None: result["logical_rows_sha256"] = logical
    return result

def _logical_hash(binding: Mapping[str, Any], counts: Mapping[str, int], section_hashes: Mapping[str, str]) -> str:
    return sha256(_canonical({"binding": dict(binding), "counts": dict(counts), "sections": dict(section_hashes)})).hexdigest()


def _stream_compress(path: Path, cancelled: Any) -> tuple[bytes, int, str]:
    compressor = zlib.compressobj(6); output = bytearray(); digest = sha256(); size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
            size += len(chunk); digest.update(chunk); output.extend(compressor.compress(chunk))
            if len(output) > MAX_SOURCE_PLANE_OWNERSHIP_IR_COMPRESSED_BYTES: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "compressed asset exceeds bound")
            if size > MAX_SOURCE_PLANE_OWNERSHIP_IR_UNCOMPRESSED_BYTES: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "uncompressed asset exceeds bound")
    if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
    output.extend(compressor.flush())
    if len(output) > MAX_SOURCE_PLANE_OWNERSHIP_IR_COMPRESSED_BYTES: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "compressed asset exceeds bound")
    return bytes(output), size, digest.hexdigest()


def build_source_plane_ownership_ir(draft: Any = None, *, batch_rows: int = 10_000, is_cancelled: Any = None, **kwargs: Any) -> tuple[dict[str, Any], tuple[str, bytes]]:
    if type(batch_rows) is not int or not 1 <= batch_rows <= MAX_SOURCE_PLANE_OWNERSHIP_IR_QUERY_ROWS: _fail("SOURCE_PLANE_OWNERSHIP_IR_BATCH_INVALID", "batch_rows is outside its bound")
    cancelled = is_cancelled or (lambda: False); data = _normalize(draft, kwargs, cancelled); v2 = "contact_boundary" in data; sections = _V2_SECTIONS if v2 else _SECTIONS; columns = _COLUMNS_V2 if v2 else _COLUMNS; create_sql = _CREATE_SQL_V2 if v2 else _CREATE_SQL; _validate_rows(data)
    with tempfile.TemporaryDirectory(prefix="spdpi-ownership-") as directory:
        path = Path(directory) / "ownership.sqlite"; connection = sqlite3.connect(path); connection.executescript(create_sql)
        try:
            for section in sections:
                section_columns = columns[section]; rows = data[section]
                for offset in range(0, len(rows), batch_rows):
                    if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
                    for index, row in enumerate(rows[offset:offset + batch_rows], offset):
                        values = dict(row); values.setdefault("ordinal", index); values = [values.get(column) for column in section_columns]
                        connection.execute(f"INSERT INTO {section} ({','.join(section_columns)}) VALUES ({','.join('?' for _ in section_columns)})", values)
            section_hashes: dict[str, str] = {}; counts: dict[str, int] = {}
            for section in sections: counts[section], section_hashes[section] = _section_digest(connection, section, cancelled, columns=columns)
            for section in sections: connection.execute("INSERT INTO section_ledger VALUES(?,?,?)", (section, counts[section], section_hashes[section]))
            logical = _logical_hash(_binding_map(data, v2=v2), counts, section_hashes)
            for key, value in {**_binding_map(data, v2=v2), "logical_rows_sha256": logical}.items(): connection.execute("INSERT INTO meta VALUES(?,?)", (key, str(value)))
            connection.commit()
        except sqlite3.Error as exc:
            _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", str(exc))
        finally: connection.close()
        compressed, raw_size, raw_sha = _stream_compress(path, cancelled)
    if len(compressed) > MAX_SOURCE_PLANE_OWNERSHIP_IR_COMPRESSED_BYTES or raw_size > len(compressed) * 256 + 8 * 1024 * 1024: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "compressed expansion exceeds bound")
    logical = _logical_hash(_binding_map(data, v2=v2), counts, section_hashes); version_tag = "v2" if v2 else "v1"; asset = f"{SOURCE_PLANE_OWNERSHIP_IR_PREFIX}source-plane-ownership-ir-{version_tag}-{data['source_sha256'][:16]}.sqlite.zlib"; identity = _binding_map(data, v2=v2)
    manifest = {**identity, "app_version": data["app_version"], "source_sha256": data["source_sha256"], "source_size_bytes": data["source_size_bytes"], "target_rail_id": data["target_rail_id"], "project_binding_sha256": data["project_binding_sha256"], "certificate_evidence_sha256": data["certificate_evidence_sha256"], "compiled_topology_identity_sha256": data["compiled_topology_identity_sha256"], "raw_manifest_sha256": data["raw_manifest_sha256"], "raw_geometry_identity_sha256": data["raw_geometry_identity_sha256"], "raw_logical_rows_sha256": data["raw_logical_rows_sha256"], "raw_plane_sheet_sha256": data["raw_plane_sheet_sha256"], "logical_rows_sha256": logical, "asset_name": asset, "compression": SOURCE_PLANE_OWNERSHIP_IR_COMPRESSION, "compressed_size_bytes": len(compressed), "compressed_sha256": sha256(compressed).hexdigest(), "uncompressed_size_bytes": raw_size, "uncompressed_sha256": raw_sha, "counts": counts}
    return manifest, (asset, compressed)


def _validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_KEYS: _fail("SOURCE_PLANE_OWNERSHIP_IR_MANIFEST_INVALID", "manifest keys differ")
    result = dict(manifest)
    for key in ("storage_schema", "payload_schema", "compiler_id", "compression", "asset_name", "app_version", "target_rail_id"): _text(result.get(key), key)
    identities = ((SOURCE_PLANE_OWNERSHIP_IR_SCHEMA, SOURCE_PLANE_OWNERSHIP_IR_PAYLOAD_SCHEMA, SOURCE_PLANE_OWNERSHIP_IR_COMPILER_ID, "v1"), (SOURCE_PLANE_OWNERSHIP_IR_V2_SCHEMA, SOURCE_PLANE_OWNERSHIP_IR_V2_PAYLOAD_SCHEMA, SOURCE_PLANE_OWNERSHIP_IR_V2_COMPILER_ID, "v2"))
    matched = next((item for item in identities if result["storage_schema"] == item[0] and result["payload_schema"] == item[1] and result["compiler_id"] == item[2]), None)
    if matched is None or result["compression"] != SOURCE_PLANE_OWNERSHIP_IR_COMPRESSION: _fail("SOURCE_PLANE_OWNERSHIP_IR_MANIFEST_INVALID", "manifest identity differs")
    for key in ("source_sha256", "project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256", "logical_rows_sha256", "compressed_sha256", "uncompressed_sha256"): _sha(result.get(key), key)
    _integer(result["source_size_bytes"], "source_size_bytes", 1); _integer(result["compressed_size_bytes"], "compressed_size_bytes", 1); _integer(result["uncompressed_size_bytes"], "uncompressed_size_bytes", 1)
    if result["compressed_size_bytes"] > MAX_SOURCE_PLANE_OWNERSHIP_IR_COMPRESSED_BYTES or result["uncompressed_size_bytes"] > MAX_SOURCE_PLANE_OWNERSHIP_IR_UNCOMPRESSED_BYTES or result["uncompressed_size_bytes"] > result["compressed_size_bytes"] * 256 + 8 * 1024 * 1024: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "manifest payload bounds differ")
    if result["asset_name"] != f"{SOURCE_PLANE_OWNERSHIP_IR_PREFIX}source-plane-ownership-ir-{matched[3]}-{result['source_sha256'][:16]}.sqlite.zlib": _fail("SOURCE_PLANE_OWNERSHIP_IR_MANIFEST_INVALID", "asset name is not source-derived")
    counts = result["counts"]
    sections = _V2_SECTIONS if matched[3] == "v2" else _SECTIONS
    if not isinstance(counts, Mapping) or set(counts) != set(sections) or any(type(value) is not int or value < 0 or value > MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS for value in counts.values()) or sum(counts.values()) > MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS: _fail("SOURCE_PLANE_OWNERSHIP_IR_MANIFEST_INVALID", "manifest counts differ")
    return result


def _manifest_v2(manifest: Mapping[str, Any]) -> bool:
    return manifest.get("storage_schema") == SOURCE_PLANE_OWNERSHIP_IR_V2_SCHEMA


def _validate_schema(connection: sqlite3.Connection, cancelled: Any = None, *, v2: bool = False) -> None:
    callback = cancelled or (lambda: False)
    sections = _V2_SECTIONS if v2 else _SECTIONS; columns = _COLUMNS_V2 if v2 else _COLUMNS; create_sql = _CREATE_SQL_V2 if v2 else _CREATE_SQL
    connection.execute("PRAGMA trusted_schema=OFF"); connection.set_progress_handler(lambda: 1 if callback() else 0, 1000)
    if connection.execute("PRAGMA application_id").fetchone()[0] != _APP_ID or connection.execute("PRAGMA user_version").fetchone()[0] != _USER_VERSION: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "SQLite identity differs")
    expected = set(sections) | {"meta", "section_ledger"}; actual = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if actual != expected: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "SQLite table set differs")
    if {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('index','trigger','view') AND name NOT LIKE 'sqlite_autoindex_%'")}: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "SQLite contains undeclared objects")
    for table, expected_columns in {**columns, "meta": ("key", "value"), "section_ledger": ("section_name", "row_count", "logical_sha256")}.items():
        if tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})")) != expected_columns: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", f"{table} columns differ")
    if list(connection.execute("PRAGMA foreign_key_check")): _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "foreign key check failed")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok": _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "SQLite integrity check failed")
    expected = sqlite3.connect(":memory:"); expected.executescript(create_sql)
    actual_sql = {row[0]: " ".join((row[1] or "").lower().split()) for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
    expected_sql = {row[0]: " ".join((row[1] or "").lower().split()) for row in expected.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
    expected.close()
    if actual_sql != expected_sql: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "SQLite DDL differs")


def _incremental_decompress(payload: bytes, expected_size: int, cancelled: Any, path: Path) -> tuple[int, str]:
    decoder = zlib.decompressobj(); digest = sha256(); size = 0
    try:
        with path.open("wb") as stream:
            for offset in range(0, len(payload), 64 * 1024):
                if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
                chunk = payload[offset:offset + 64 * 1024]
                while chunk:
                    if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
                    output = decoder.decompress(chunk, 64 * 1024); chunk = decoder.unconsumed_tail
                    if output:
                        size += len(output)
                        if size > expected_size or size > MAX_SOURCE_PLANE_OWNERSHIP_IR_UNCOMPRESSED_BYTES: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "decompressed payload exceeds bound")
                        stream.write(output); digest.update(output)
            if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
            output = decoder.flush()
            if output:
                size += len(output)
                if size > expected_size or size > MAX_SOURCE_PLANE_OWNERSHIP_IR_UNCOMPRESSED_BYTES: _fail("SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED", "decompressed payload exceeds bound")
                stream.write(output); digest.update(output)
    except zlib.error as exc:
        _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", f"zlib payload is invalid: {exc}")
    if not decoder.eof or decoder.unused_data: _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "zlib payload has trailing/incomplete data")
    return size, digest.hexdigest()


class LoadedSourcePlaneOwnershipIR:
    def __init__(self, connection: sqlite3.Connection, temporary: tempfile.TemporaryDirectory[str], manifest: Mapping[str, Any]) -> None:
        self._connection = connection; self._temporary = temporary; self.manifest = dict(manifest)
    def close(self) -> None:
        if self._connection is not None: self._connection.close(); self._connection = None; self._temporary.cleanup()
    def __enter__(self) -> "LoadedSourcePlaneOwnershipIR": return self
    def __exit__(self, *_args: Any) -> None: self.close()
    def iter_section(self, section: str, *, batch_rows: int = 1000) -> Iterator[dict[str, Any]]:
        if self._connection is None: _fail("SOURCE_PLANE_OWNERSHIP_IR_CLOSED", "asset is closed")
        columns = _COLUMNS_V2 if _manifest_v2(self.manifest) else _COLUMNS; sections = _V2_SECTIONS if _manifest_v2(self.manifest) else _SECTIONS
        if section not in sections: _fail("SOURCE_PLANE_OWNERSHIP_IR_SECTION_INVALID", "unknown section")
        if type(batch_rows) is not int or not 1 <= batch_rows <= MAX_SOURCE_PLANE_OWNERSHIP_IR_QUERY_ROWS: _fail("SOURCE_PLANE_OWNERSHIP_IR_BATCH_INVALID", "batch_rows is outside its bound")
        cursor = self._connection.execute(f"SELECT {','.join(columns[section])} FROM {section} ORDER BY {','.join(columns[section])}")
        while rows := cursor.fetchmany(batch_rows):
            for row in rows: yield dict(zip(columns[section], row, strict=True))

class _TableRows:
    def __init__(self, connection: sqlite3.Connection, section: str, columns: Mapping[str, tuple[str, ...]]) -> None:
        self.connection = connection; self.section = section; self.columns = columns
    def __iter__(self) -> Iterator[dict[str, Any]]:
        columns = self.columns[self.section]; order = ",".join(columns)
        for row in self.connection.execute(f"SELECT {order} FROM {self.section} ORDER BY {order}"):
            yield dict(zip(columns, row, strict=True))
    def __len__(self) -> int:
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {self.section}").fetchone()[0])


def load_source_plane_ownership_ir(manifest: Mapping[str, Any], attachments: Mapping[str, bytes], *, expected_source_sha256: str, expected_project_binding_sha256: str, expected_certificate_evidence_sha256: str, expected_compiled_topology_identity_sha256: str, expected_raw_manifest_sha256: str, expected_raw_geometry_identity_sha256: str, expected_raw_logical_rows_sha256: str, expected_raw_plane_sheet_sha256: str, expected_app_version: str | None = None, is_cancelled: Any = None) -> LoadedSourcePlaneOwnershipIR:
    validated = _validate_manifest(manifest); expected = {"source_sha256": expected_source_sha256, "project_binding_sha256": expected_project_binding_sha256, "certificate_evidence_sha256": expected_certificate_evidence_sha256, "compiled_topology_identity_sha256": expected_compiled_topology_identity_sha256, "raw_manifest_sha256": expected_raw_manifest_sha256, "raw_geometry_identity_sha256": expected_raw_geometry_identity_sha256, "raw_logical_rows_sha256": expected_raw_logical_rows_sha256, "raw_plane_sheet_sha256": expected_raw_plane_sheet_sha256}
    for key, value in expected.items():
        if validated[key] != _sha(value, f"expected {key}"): _fail("SOURCE_PLANE_OWNERSHIP_IR_BINDING_MISMATCH", f"{key} differs")
    if expected_app_version is not None and validated["app_version"] != _text(expected_app_version, "expected app_version"): _fail("SOURCE_PLANE_OWNERSHIP_IR_BINDING_MISMATCH", "app_version differs")
    if not isinstance(attachments, Mapping): _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "attachments are not a mapping")
    names = [name for name in attachments if isinstance(name, str) and name.casefold() == validated["asset_name"].casefold()]
    if names != [validated["asset_name"]]: _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "required attachment is absent or ambiguous")
    payload = attachments[validated["asset_name"]]
    if not isinstance(payload, bytes) or len(payload) != validated["compressed_size_bytes"] or sha256(payload).hexdigest() != validated["compressed_sha256"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "attachment hash/size differs")
    cancelled = is_cancelled or (lambda: False)
    if cancelled(): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
    temporary = tempfile.TemporaryDirectory(prefix="spdpi-ownership-read-"); path = Path(temporary.name) / "ownership.sqlite"
    try:
        size, digest = _incremental_decompress(payload, validated["uncompressed_size_bytes"], cancelled, path)
        if size != validated["uncompressed_size_bytes"] or digest != validated["uncompressed_sha256"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "uncompressed hash/size differs")
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True); connection.execute("PRAGMA query_only=ON"); _validate_schema(connection, cancelled, v2=_manifest_v2(validated))
        meta = {row[0]: row[1] for row in connection.execute("SELECT key,value FROM meta")}
        if set(meta) != set(_META_KEYS): _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "meta identity set differs")
        for key in _META_KEYS[:-1]:
            expected_value = _binding_map(validated, v2=_manifest_v2(validated)).get(key)
            if str(expected_value) != meta[key]: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", f"meta identity {key} differs")
        sections = _V2_SECTIONS if _manifest_v2(validated) else _SECTIONS; columns = _COLUMNS_V2 if _manifest_v2(validated) else _COLUMNS
        if {row[0] for row in connection.execute("SELECT section_name FROM section_ledger")} != set(sections): _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "section ledger set differs")
        counts: dict[str, int] = {}; hashes: dict[str, str] = {}
        for section in sections:
            counts[section], hashes[section] = _section_digest(connection, section, cancelled, columns=columns); ledger = connection.execute("SELECT row_count,logical_sha256 FROM section_ledger WHERE section_name=?", (section,)).fetchone()
            if ledger != (counts[section], hashes[section]) or counts[section] != validated["counts"][section]: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", f"{section} ledger differs")
        if _logical_hash(_binding_map(validated, v2=_manifest_v2(validated)), counts, hashes) != validated["logical_rows_sha256"] or meta["logical_rows_sha256"] != validated["logical_rows_sha256"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", "logical rows hash differs")
        draft = {section: _TableRows(connection, section, columns) for section in sections}; draft.update({"app_version": validated["app_version"], "source_sha256": validated["source_sha256"], "source_size_bytes": validated["source_size_bytes"], "target_rail_id": validated["target_rail_id"], **{key: validated[key] for key in ("project_binding_sha256", "certificate_evidence_sha256", "compiled_topology_identity_sha256", "raw_manifest_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "raw_plane_sheet_sha256")}}); _validate_rows(draft)
        return LoadedSourcePlaneOwnershipIR(connection, temporary, validated)
    except sqlite3.Error as exc:
        if "connection" in locals(): connection.close()
        temporary.cleanup()
        if cancelled() and (getattr(exc, "sqlite_errorname", "") == "SQLITE_INTERRUPT" or "interrupted" in str(exc).lower()): _fail("SOURCE_PLANE_OWNERSHIP_IR_CANCELLED", "operation cancelled")
        _fail("SOURCE_PLANE_OWNERSHIP_IR_DATABASE_INVALID", str(exc))
    except BaseException:
        if "connection" in locals(): connection.close()
        temporary.cleanup(); raise


def validate_project_source_plane_ownership_ir_envelope(project: Any, attachments: Mapping[str, bytes]) -> dict[str, Any] | None:
    if not isinstance(attachments, Mapping): _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "attachments are not a mapping")
    root = dict(project) if isinstance(project, Mapping) else _obj_map(project, "project"); metadata = root.get("metadata", {}); metadata = dict(metadata) if isinstance(metadata, Mapping) else {}; spd = metadata.get("spd_import", {}); spd = dict(spd) if isinstance(spd, Mapping) else {}; manifest = spd.get(SOURCE_PLANE_OWNERSHIP_IR_METADATA_KEY)
    if manifest is None:
        if isinstance(attachments, Mapping) and any(isinstance(name, str) and name.casefold().startswith(SOURCE_PLANE_OWNERSHIP_IR_PREFIX.casefold()) for name in attachments): _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_ORPHANED", "ownership attachment has no descriptor")
        return None
    validated = _validate_manifest(manifest); names = [name for name in attachments if isinstance(name, str) and name.casefold() == validated["asset_name"].casefold()]
    if names != [validated["asset_name"]] or any(isinstance(name, str) and name.casefold().startswith(SOURCE_PLANE_OWNERSHIP_IR_PREFIX.casefold()) and name.casefold() != validated["asset_name"].casefold() for name in attachments): _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "descriptor attachment is missing or ambiguous")
    payload = attachments[validated["asset_name"]]
    if type(payload) is not bytes or len(payload) != validated["compressed_size_bytes"] or sha256(payload).hexdigest() != validated["compressed_sha256"]: _fail("SOURCE_PLANE_OWNERSHIP_IR_ATTACHMENT_INVALID", "descriptor payload bytes/size/hash differ")
    return validated


__all__ = [name for name in globals() if name.startswith("SOURCE_PLANE_OWNERSHIP_IR") or name.startswith("MAX_SOURCE_PLANE_OWNERSHIP_IR") or name in {"SourcePlaneOwnershipIRError", "LoadedSourcePlaneOwnershipIR", "build_source_plane_ownership_ir", "load_source_plane_ownership_ir", "validate_project_source_plane_ownership_ir_envelope"}]
